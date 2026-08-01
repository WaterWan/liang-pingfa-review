"""ODA File Converter discovery and bounded private conversion wrapper."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import ctypes
import os
from pathlib import Path
import re
import secrets
import shutil
import stat as stat_module
import subprocess
from typing import Final

from .canonical import canonical_sha256
from .errors import ErrorCode, PipelineError
from .ownership import (
    FileOwnershipBackend,
    LexicalDirectoryChainLease,
    OwnedPath,
    OwnedPathBinding,
    OwnershipCleanupError,
    OwnershipError,
    OwnershipLostError,
    SourcePathLease,
    acquire_lexical_directory_chain,
    is_reparse_point,
    platform_backend,
)
from .raw_dxf import preflight_ascii_dxf_bytes, read_bounded_dxf_chunks
from .snapshots import (
    Snapshot,
    _volatile_object_tag_codes,
    open_preflighted_dxf,
    snapshot_document,
)
from .temporary import PrivateWorkspace


SUPPORTED_ODA_VERSION: Final[str] = "27.1.0"
ODA_TIMEOUT_SECONDS: Final[int] = 120
# Generated ODA 27.1.0 probes show that both DWG and DXF byte images carry
# volatile serializer metadata. Complete raw/semantic snapshots, rather than
# byte equality, are therefore the narrow compatibility oracle.
ODA_OUTPUT_BYTES_STABLE: Final[bool] = False


def _default_program_files() -> Path:
    """Build a conventional Windows path without embedding a local path."""

    return Path("C" + ":" + chr(92) + "Program Files")


def _normal_path(path: Path) -> Path:
    try:
        return path.expanduser().resolve(strict=True)
    except OSError as error:
        raise PipelineError(ErrorCode.ODA_NOT_FOUND, "converter candidate is unavailable") from error


def _converter_backend() -> FileOwnershipBackend:
    """Require the production no-follow Windows handle implementation."""

    try:
        return platform_backend(require_windows=True)
    except OwnershipCleanupError as error:
        raise PipelineError(
            ErrorCode.WINDOWS_PLATFORM_REQUIRED,
            "ODA conversion requires Windows handle semantics",
        ) from error


def _path_key(path: Path) -> str:
    """Compare already-open final paths without resolving a new leaf."""

    return os.path.normcase(os.path.abspath(os.fspath(path)))


@dataclass
class _ConverterDirectoryLease:
    """A retained no-follow lexical chain for one ODA staging directory."""

    chain: LexicalDirectoryChainLease
    _closed: bool = False

    @property
    def lexical_path(self) -> Path:
        return self.chain.lexical_path

    @property
    def path(self) -> Path:
        return self.chain.path

    @property
    def owned(self) -> OwnedPath:
        return self.chain.owned

    @property
    def binding(self) -> OwnedPathBinding:
        return self.chain.binding

    @property
    def backend(self) -> FileOwnershipBackend:
        return self.chain.backend

    def require_binding(self) -> None:
        try:
            self.chain.require_binding()
        except (OSError, OwnershipError) as error:
            raise PipelineError(
                ErrorCode.CONVERSION_FAILURE,
                "converter directory lease is unavailable",
            ) from error

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.chain.close()
        except (OSError, OwnershipError) as error:
            raise PipelineError(
                ErrorCode.CONVERSION_FAILURE,
                "converter directory lease cannot be released",
            ) from error
        self._closed = True


@dataclass
class _ConverterInputLease:
    """A random staged ODA source held read-only through process completion."""

    path: Path
    owned: OwnedPath
    binding: OwnedPathBinding
    directory: _ConverterDirectoryLease
    backend: FileOwnershipBackend
    _closed: bool = False

    def require_binding(self) -> None:
        self.directory.require_binding()
        try:
            current = self.owned.capture_binding()
            if (
                current.is_directory
                or not current.same_identity_and_content(self.binding)
                or not self.backend.path_matches_binding(self.path, current)
            ):
                raise OwnershipLostError("converter input identity changed")
        except (OSError, OwnershipError) as error:
            raise PipelineError(
                ErrorCode.CONVERSION_FAILURE,
                "converter input lease is unavailable",
            ) from error

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.owned.close()
        except (OSError, OwnershipError) as error:
            raise PipelineError(
                ErrorCode.CONVERSION_FAILURE,
                "converter input lease cannot be released",
            ) from error
        self._closed = True


@dataclass
class _ConverterInvocationLeases:
    """All no-follow identities retained while one ODA subprocess runs."""

    input_directory: _ConverterDirectoryLease
    staged_input: _ConverterInputLease
    output_directory: _ConverterDirectoryLease

    def require_binding(self) -> None:
        self.input_directory.require_binding()
        self.staged_input.require_binding()
        self.output_directory.require_binding()

    def close(self) -> None:
        failure: PipelineError | None = None
        for lease in (self.staged_input, self.output_directory, self.input_directory):
            try:
                lease.close()
            except PipelineError as error:
                if failure is None:
                    failure = error
        if failure is not None:
            raise failure


@dataclass(frozen=True)
class _PreRunInventory:
    """Direct, non-recursive evidence captured before a converter launch."""

    input_names: tuple[str, ...]
    input_binding: OwnedPathBinding
    output_names: tuple[str, ...]


@dataclass
class _AcceptedConverterOutput:
    """A post-open-bound private output retained until its next handoff."""

    path: Path
    owned: OwnedPath
    binding: OwnedPathBinding
    backend: FileOwnershipBackend
    _retained_by_workspace: bool = False
    _closed: bool = False

    def require_binding(self) -> None:
        if self._closed:
            raise PipelineError(
                ErrorCode.ODA_OUTPUT_INCOMPATIBLE,
                "accepted converter output lease was released",
            )
        try:
            current = self.owned.capture_binding()
            if (
                current.is_directory
                or not current.same_identity_and_content(self.binding)
                or not self.backend.path_matches_binding(self.path, current)
            ):
                raise OwnershipLostError("accepted converter output changed")
        except (OSError, OwnershipError) as error:
            raise PipelineError(
                ErrorCode.ODA_OUTPUT_INCOMPATIBLE,
                "accepted converter output is unavailable",
            ) from error

    def retain_in_workspace(self, workspace: PrivateWorkspace) -> None:
        """Transfer lifetime of the exact output lease to workspace cleanup."""

        self.require_binding()
        workspace.retain_opened_file(self.owned)
        self._retained_by_workspace = True

    def discard(self, workspace: PrivateWorkspace) -> None:
        """Delete only this redundant, already-bound private output."""

        self.require_binding()
        try:
            self.owned.close()
        except (OSError, OwnershipError) as error:
            raise PipelineError(
                ErrorCode.ODA_OUTPUT_INCOMPATIBLE,
                "redundant converter output lease cannot be released",
            ) from error
        self._closed = True
        workspace.discard_registered_file(
            self.path,
            expected_identity=self.binding,
        )

    def close(self) -> None:
        if self._closed or self._retained_by_workspace:
            return
        try:
            self.owned.close()
        except (OSError, OwnershipError) as error:
            raise PipelineError(
                ErrorCode.ODA_OUTPUT_INCOMPATIBLE,
                "converter output lease cannot be released",
            ) from error
        self._closed = True


def _acquire_converter_directory_lease(
    directory: Path,
    backend: FileOwnershipBackend,
    *,
    restrict_child_writers: bool = False,
) -> _ConverterDirectoryLease:
    """Retain all lexical staging ancestors before ODA receives a pathname."""

    try:
        final_opener = (
            backend.open_existing_directory_read_lease
            if restrict_child_writers
            else None
        )
        lease = _ConverterDirectoryLease(
            acquire_lexical_directory_chain(
                directory,
                backend,
                final_directory_opener=final_opener,
            )
        )
        lease.require_binding()
        return lease
    except (OSError, OwnershipError) as error:
        raise PipelineError(
            ErrorCode.CONVERSION_FAILURE,
            "converter directory cannot be leased",
        ) from error


def _acquire_converter_input_lease(
    directory: _ConverterDirectoryLease,
    expected_name: str,
) -> _ConverterInputLease:
    """Lease exactly one CSPRNG-named staged source before launch."""

    staged_path = directory.path / expected_name
    opened: OwnedPath | None = None
    try:
        directory.require_binding()
        opened = directory.backend.open_existing_file_read_lease(staged_path)
        binding = opened.capture_binding()
        final_path = opened.final_path()
        if (
            binding.is_directory
            or final_path.name.casefold() != expected_name.casefold()
            or _path_key(final_path.parent) != _path_key(directory.path)
            or not directory.backend.path_matches_binding(staged_path, binding)
            or not directory.backend.path_matches_binding(final_path, binding)
        ):
            raise OwnershipLostError("converter input is not a direct bound child")
        lease = _ConverterInputLease(
            path=final_path,
            owned=opened,
            binding=binding,
            directory=directory,
            backend=directory.backend,
        )
        _require_exact_converter_input(lease)
        return lease
    except PipelineError:
        if opened is not None:
            try:
                opened.close()
            except (OSError, OwnershipError):
                pass
        raise
    except (OSError, OwnershipError) as error:
        if opened is not None:
            try:
                opened.close()
            except (OSError, OwnershipError):
                pass
        raise PipelineError(
            ErrorCode.CONVERSION_FAILURE,
            "converter input cannot be leased",
        ) from error


def _require_isolated_converter_directories(
    input_directory: _ConverterDirectoryLease,
    output_directory: _ConverterDirectoryLease,
) -> None:
    """Reject overlapping final directories before a converter can recurse."""

    input_root = input_directory.path
    output_root = output_directory.path
    if (
        _path_key(input_root) == _path_key(output_root)
        or input_root in output_root.parents
        or output_root in input_root.parents
    ):
        raise PipelineError(ErrorCode.CONVERSION_FAILURE, "non-isolated converter directories")


def _direct_entries(directory: _ConverterDirectoryLease) -> tuple[Path, ...]:
    """List only direct entries without following a sidecar or junction."""

    directory.require_binding()
    try:
        entries = tuple(sorted(directory.path.iterdir(), key=lambda item: item.name.casefold()))
        for entry in entries:
            if is_reparse_point(entry):
                raise OwnershipLostError("converter directory contains a reparse entry")
    except (OSError, OwnershipError) as error:
        raise PipelineError(
            ErrorCode.CONVERSION_FAILURE,
            "converter directory inventory is unavailable",
        ) from error
    return entries


def _require_exact_converter_input(input_lease: _ConverterInputLease) -> None:
    """Require one direct, bound input leaf and no other direct children."""

    input_lease.require_binding()
    expected_path = input_lease.path
    try:
        entries = _direct_entries(input_lease.directory)
        if len(entries) != 1 or entries[0].name.casefold() != expected_path.name.casefold():
            raise OwnershipLostError("converter input directory contents changed")
        entry = entries[0]
        if (
            not stat_module.S_ISREG(entry.lstat().st_mode)
            or not input_lease.backend.path_matches_binding(entry, input_lease.binding)
        ):
            raise OwnershipLostError("converter input is not its bound source")
    except PipelineError:
        raise
    except (OSError, OwnershipError) as error:
        raise PipelineError(
            ErrorCode.CONVERSION_FAILURE,
            "converter input is not exact",
        ) from error


def _capture_pre_run_inventory(
    leases: _ConverterInvocationLeases,
) -> _PreRunInventory:
    """Record direct input/output inventories before a subprocess starts."""

    leases.require_binding()
    _require_exact_converter_input(leases.staged_input)
    input_entries = _direct_entries(leases.input_directory)
    output_entries = _direct_entries(leases.output_directory)
    if output_entries:
        raise PipelineError(
            ErrorCode.ODA_OUTPUT_INCOMPATIBLE,
            "private converter output directory was not empty",
        )
    return _PreRunInventory(
        input_names=tuple(entry.name.casefold() for entry in input_entries),
        input_binding=leases.staged_input.binding,
        output_names=tuple(entry.name.casefold() for entry in output_entries),
    )


def _require_unchanged_input_inventory(
    leases: _ConverterInvocationLeases,
    before: _PreRunInventory,
) -> None:
    """Require the same direct input entry and exact bound bytes after ODA."""

    leases.require_binding()
    _require_exact_converter_input(leases.staged_input)
    input_entries = _direct_entries(leases.input_directory)
    if (
        tuple(entry.name.casefold() for entry in input_entries) != before.input_names
        or not leases.staged_input.binding.same_identity_and_content(before.input_binding)
    ):
        raise PipelineError(
            ErrorCode.CONVERSION_FAILURE,
            "converter input inventory changed",
        )


def _post_run_candidate(
    leases: _ConverterInvocationLeases,
    before: _PreRunInventory,
    expected_output: Path,
    expected_suffix: str,
) -> Path:
    """Require exactly one new regular output and no sidecar/leftover entry."""

    _require_unchanged_input_inventory(leases, before)
    entries = _direct_entries(leases.output_directory)
    if before.output_names:
        raise PipelineError(
            ErrorCode.ODA_OUTPUT_INCOMPATIBLE,
            "converter output inventory was not initially empty",
        )
    if len(entries) != 1:
        raise PipelineError(
            ErrorCode.ODA_OUTPUT_INCOMPATIBLE,
            "converter emitted missing, multiple, or sidecar outputs",
        )
    candidate = entries[0]
    try:
        is_regular = stat_module.S_ISREG(candidate.lstat().st_mode)
    except OSError as error:
        raise PipelineError(
            ErrorCode.ODA_OUTPUT_INCOMPATIBLE,
            "converter output cannot be inspected",
        ) from error
    if (
        not is_regular
        or candidate.suffix.casefold() != expected_suffix
        or candidate.name.casefold() != expected_output.name.casefold()
        or _path_key(candidate.parent) != _path_key(leases.output_directory.path)
    ):
        raise PipelineError(
            ErrorCode.ODA_OUTPUT_INCOMPATIBLE,
            "converter output name or type is invalid",
        )
    return candidate


def _inspect_failed_run(
    leases: _ConverterInvocationLeases,
    before: _PreRunInventory,
) -> None:
    """Still prove the staged source did not drift after an unsuccessful run."""

    _require_unchanged_input_inventory(leases, before)


def _acquire_converter_invocation_leases(
    input_directory: Path,
    output_directory: Path,
    expected_input_name: str,
) -> _ConverterInvocationLeases:
    """Acquire every no-follow lease before a converter receives any path."""

    backend = _converter_backend()
    input_lease: _ConverterDirectoryLease | None = None
    output_lease: _ConverterDirectoryLease | None = None
    staged_input: _ConverterInputLease | None = None
    try:
        input_lease = _acquire_converter_directory_lease(
            input_directory,
            backend,
            restrict_child_writers=True,
        )
        output_lease = _acquire_converter_directory_lease(output_directory, backend)
        _require_isolated_converter_directories(input_lease, output_lease)
        staged_input = _acquire_converter_input_lease(input_lease, expected_input_name)
        leases = _ConverterInvocationLeases(
            input_directory=input_lease,
            staged_input=staged_input,
            output_directory=output_lease,
        )
        leases.require_binding()
        return leases
    except BaseException:
        if staged_input is not None:
            try:
                staged_input.close()
            except PipelineError:
                pass
        if output_lease is not None:
            try:
                output_lease.close()
            except PipelineError:
                pass
        if input_lease is not None:
            try:
                input_lease.close()
            except PipelineError:
                pass
        raise


def _validate_bound_candidate(
    opened: OwnedPath,
    binding: OwnedPathBinding,
    *,
    expected_path: Path,
    output_type: str,
    backend: FileOwnershipBackend,
) -> Path:
    """Validate identity, bytes, and format only after the no-write lease."""

    final_path = opened.final_path()
    if (
        binding.is_directory
        or binding.byte_size is None
        or binding.sha256 is None
        or binding.byte_size <= 0
        or final_path.name.casefold() != expected_path.name.casefold()
        or _path_key(final_path.parent) != _path_key(expected_path.parent)
        or not backend.path_matches_binding(expected_path, binding)
        or not backend.path_matches_binding(final_path, binding)
    ):
        raise OwnershipLostError("converter output is not a direct bound file")
    if output_type == "DWG":
        if opened.read_prefix(6) != b"AC1032":
            raise PipelineError(
                ErrorCode.ODA_OUTPUT_INCOMPATIBLE,
                "converter output is not R2018 DWG",
            )
    else:
        # This raw preflight happens only after identity/size/hash/header
        # binding. Higher-level DXF parsing is deferred to the dual oracle.
        preflight_ascii_dxf_bytes(read_bounded_dxf_chunks(opened.read_chunks()))
    if not opened.capture_binding().same_identity_and_content(binding):
        raise OwnershipLostError("converter output changed while validating")
    return final_path


def _adopt_converter_output(
    workspace: PrivateWorkspace | None,
    leases: _ConverterInvocationLeases,
    before: _PreRunInventory,
    expected_output: Path,
    output_type: str,
) -> _AcceptedConverterOutput:
    """Post-open adopt one new private candidate; no filename is authority."""

    expected_suffix = "." + output_type.lower()
    candidate = _post_run_candidate(
        leases,
        before,
        expected_output,
        expected_suffix,
    )
    opened: OwnedPath | None = None
    binding: OwnedPathBinding | None = None
    registered_for_cleanup = False
    try:
        opened = leases.output_directory.backend.open_existing_file_read_lease(candidate)
        binding = opened.capture_binding()
        # The direct inventory and no-follow lease have already bound this
        # private child. Register that identity for fail-closed cleanup before
        # raw format validation so malformed converter bytes never turn into
        # an unknown workspace sidecar that masks the authoritative error.
        if workspace is not None:
            workspace.track_opened_file(opened)
            registered_for_cleanup = True
        final_path = _validate_bound_candidate(
            opened,
            binding,
            expected_path=expected_output,
            output_type=output_type,
            backend=leases.output_directory.backend,
        )
        accepted = _AcceptedConverterOutput(
            path=final_path,
            owned=opened,
            binding=binding,
            backend=leases.output_directory.backend,
        )
        accepted.require_binding()
        return accepted
    except PipelineError as error:
        if opened is not None:
            try:
                opened.close()
            except (OSError, OwnershipError):
                pass
        if (
            workspace is not None
            and binding is not None
            and registered_for_cleanup
        ):
            try:
                workspace.discard_registered_file(
                    binding.path,
                    expected_identity=binding,
                )
            except PipelineError as cleanup_error:
                raise cleanup_error from error
        raise
    except (OSError, OwnershipError) as error:
        if opened is not None:
            try:
                opened.close()
            except (OSError, OwnershipError):
                pass
        raise PipelineError(
            ErrorCode.ODA_OUTPUT_INCOMPATIBLE,
            "converter output could not be post-open bound",
        ) from error


def _candidate_paths(environment: Mapping[str, str]) -> list[Path]:
    candidates: list[Path] = []
    for root_text in (
        environment.get("ProgramFiles"),
        environment.get("ProgramFiles(x86)"),
    ):
        root = Path(root_text) if root_text else _default_program_files()
        oda_root = root / "ODA"
        if oda_root.is_dir():
            candidates.extend(oda_root.glob("ODAFileConverter */ODAFileConverter.exe"))
    return candidates


def discover_oda(
    explicit_path: Path | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> Path:
    """Discover exactly one converter according to documented precedence."""

    env = os.environ if environment is None else environment
    if explicit_path is not None:
        candidate = _normal_path(explicit_path)
        if not candidate.is_file():
            raise PipelineError(ErrorCode.ODA_NOT_FOUND, "explicit converter is not a file")
        return candidate
    environment_path = env.get("ODA_FILE_CONVERTER")
    if environment_path:
        candidate = _normal_path(Path(environment_path))
        if not candidate.is_file():
            raise PipelineError(ErrorCode.ODA_NOT_FOUND, "environment converter is not a file")
        return candidate

    finder = shutil.which if which is None else which
    candidates: list[Path] = []
    for executable_name in ("ODAFileConverter.exe", "ODAFileConverter"):
        found = finder(executable_name)
        if found:
            candidates.append(Path(found))
    candidates.extend(_candidate_paths(env))

    resolved: dict[str, Path] = {}
    for candidate in candidates:
        try:
            normalized = _normal_path(candidate)
        except PipelineError:
            continue
        if normalized.is_file():
            resolved[str(normalized).casefold()] = normalized
    if not resolved:
        raise PipelineError(ErrorCode.ODA_NOT_FOUND, "converter was not discovered")
    if len(resolved) != 1:
        raise PipelineError(ErrorCode.ODA_DISCOVERY_AMBIGUOUS, "multiple converters discovered")
    return next(iter(resolved.values()))


def _file_product_version(path: Path) -> str | None:
    """Read Windows version metadata without launching the converter."""

    if os.name != "nt":
        return None
    try:
        version_dll = ctypes.windll.version
        size = version_dll.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not version_dll.GetFileVersionInfoW(str(path), 0, size, buffer):
            return None
        value_pointer = ctypes.c_void_p()
        value_length = ctypes.c_uint()
        separator = chr(92)
        query = (
            separator
            + "StringFileInfo"
            + separator
            + "040904B0"
            + separator
            + "ProductVersion"
        )
        if version_dll.VerQueryValueW(
            buffer,
            query,
            ctypes.byref(value_pointer),
            ctypes.byref(value_length),
        ):
            if value_pointer.value and value_length.value:
                return ctypes.wstring_at(value_pointer.value, value_length.value).rstrip(chr(0))

        class FixedFileInfo(ctypes.Structure):
            _fields_ = [
                ("signature", ctypes.c_uint32),
                ("struct_version", ctypes.c_uint32),
                ("file_version_ms", ctypes.c_uint32),
                ("file_version_ls", ctypes.c_uint32),
                ("product_version_ms", ctypes.c_uint32),
                ("product_version_ls", ctypes.c_uint32),
                ("file_flags_mask", ctypes.c_uint32),
                ("file_flags", ctypes.c_uint32),
                ("file_os", ctypes.c_uint32),
                ("file_type", ctypes.c_uint32),
                ("file_subtype", ctypes.c_uint32),
                ("file_date_ms", ctypes.c_uint32),
                ("file_date_ls", ctypes.c_uint32),
            ]

        root_pointer = ctypes.c_void_p()
        root_length = ctypes.c_uint()
        if not version_dll.VerQueryValueW(
            buffer,
            separator,
            ctypes.byref(root_pointer),
            ctypes.byref(root_length),
        ):
            return None
        if not root_pointer.value:
            return None
        information = ctypes.cast(
            root_pointer, ctypes.POINTER(FixedFileInfo)
        ).contents
        parts = (
            information.product_version_ms >> 16,
            information.product_version_ms & 0xFFFF,
            information.product_version_ls >> 16,
        )
        return ".".join(str(part) for part in parts)
    except (AttributeError, OSError, ValueError):
        return None


def oda_version(path: Path) -> str:
    """Return only the normalized three-part converter version."""

    raw_version = _file_product_version(path)
    if raw_version is None:
        raise PipelineError(ErrorCode.ODA_VERSION_UNSUPPORTED, "converter version unavailable")
    match = re.search(r"([0-9]+)\.([0-9]+)\.([0-9]+)", raw_version)
    if match is None:
        raise PipelineError(ErrorCode.ODA_VERSION_UNSUPPORTED, "converter version malformed")
    return ".".join(match.groups())


@dataclass(frozen=True)
class OdaRunner:
    """A verified external converter restricted to random private directories."""

    executable: Path
    version: str
    timeout_seconds: int = ODA_TIMEOUT_SECONDS

    @classmethod
    def discover(
        cls,
        explicit_path: Path | None = None,
        *,
        environment: Mapping[str, str] | None = None,
    ) -> "OdaRunner":
        executable = discover_oda(explicit_path, environment=environment)
        version = oda_version(executable)
        if version != SUPPORTED_ODA_VERSION:
            raise PipelineError(ErrorCode.ODA_VERSION_UNSUPPORTED, "unsupported converter")
        return cls(executable=executable, version=version)

    def _invoke(
        self,
        leases: _ConverterInvocationLeases,
        before: _PreRunInventory,
        output_type: str,
    ) -> Path:
        """Run exactly one subprocess while all ancestor/input/output leases live."""

        if self.version != SUPPORTED_ODA_VERSION:
            raise PipelineError(ErrorCode.ODA_VERSION_UNSUPPORTED, "unsupported converter")
        if output_type not in {"DWG", "DXF"}:
            raise PipelineError(ErrorCode.INVALID_ARGUMENT, "unsupported conversion type")
        input_root = leases.input_directory.path
        output_root = leases.output_directory.path
        staged_input = leases.staged_input.path
        expected_output = output_root / f"{staged_input.stem}.{output_type.lower()}"
        command = [
            str(self.executable),
            str(input_root),
            str(output_root),
            "ACAD2018",
            output_type,
            "0",
            "1",
            # Exact random leaf filter, after recurse/audit. ODA never gets a
            # wildcard or a recursive source tree to select from.
            staged_input.name,
        ]
        leases.require_binding()
        try:
            completed = subprocess.run(
                command,
                check=False,
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            _inspect_failed_run(leases, before)
            raise PipelineError(ErrorCode.ODA_TIMEOUT, "converter timeout") from error
        except OSError as error:
            _inspect_failed_run(leases, before)
            raise PipelineError(
                ErrorCode.CONVERSION_FAILURE,
                "converter launch failure",
            ) from error
        if completed.returncode != 0:
            _inspect_failed_run(leases, before)
            raise PipelineError(ErrorCode.CONVERSION_FAILURE, "converter failed")
        _require_unchanged_input_inventory(leases, before)
        return expected_output

    def convert(
        self,
        input_directory: Path,
        output_directory: Path,
        output_type: str,
        *,
        register_output: Callable[[Path], Path] | None = None,
    ) -> Path:
        """Compatibility helper for generated-only direct converter probes.

        Public workflow code uses :func:`staged_dwg_to_dxf` and
        :func:`staged_dxf_to_dwg`, which retain the adopted candidate through
        the subsequent private handoff. This helper still enforces empty
        output, direct inventories, and strict post-open binding before it
        yields a path to a probe callback.
        """

        input_suffix = ".dwg" if output_type == "DXF" else ".dxf"
        expected_name = "source" + input_suffix
        leases = _acquire_converter_invocation_leases(
            input_directory,
            output_directory,
            expected_name,
        )
        accepted: _AcceptedConverterOutput | None = None
        try:
            before = _capture_pre_run_inventory(leases)
            expected = self._invoke(leases, before, output_type)
            accepted = _adopt_converter_output(
                None,
                leases,
                before,
                expected,
                output_type,
            )
            callback = register_output or (lambda candidate: candidate)
            registered = callback(accepted.path)
            if _path_key(registered) != _path_key(accepted.path):
                raise PipelineError(
                    ErrorCode.ODA_OUTPUT_INCOMPATIBLE,
                    "converter probe registrar changed the bound output path",
                )
            return accepted.path
        finally:
            if accepted is not None:
                accepted.close()
            leases.close()


def _copy_single_staged_input(
    source: SourcePathLease,
    input_directory: Path,
    input_name: str,
    *,
    workspace: PrivateWorkspace,
) -> None:
    """Copy one immutable source into a random, exact staged input filename."""

    opened: OwnedPath | None = None
    try:
        if not input_directory.is_dir() or any(input_directory.iterdir()):
            raise OSError("input directory is not an empty owned directory")
        staged = input_directory / input_name
        opened = workspace.create_owned_file(staged)
        try:
            source.require_binding()
            opened.write_chunks(source.read_chunks())
            source.require_binding()
            workspace.seal_owned_file(opened)
            opened = None
        except BaseException as error:
            try:
                workspace.discard_owned_file(opened)
            except PipelineError as cleanup_error:
                raise cleanup_error from error
            raise
    except PipelineError:
        raise
    except (OSError, shutil.Error) as error:
        raise PipelineError(
            ErrorCode.CONVERSION_FAILURE,
            "unable to stage converter input",
        ) from error


@dataclass(frozen=True)
class _DxfEquivalenceState:
    """Complete nonvolatile DXF state used to compare independent runs."""

    snapshot: Snapshot
    raw_section_structure_digest: str
    raw_header_manifest_digest: str
    raw_modeled_records_digest: str
    raw_classes_wire_manifest_digest: str
    raw_classes_wire_multiset_digest: str


def _complete_dxf_equivalence_state(path: Path) -> _DxfEquivalenceState:
    """Capture raw-section and complete preservation state under a read lease."""

    with open_preflighted_dxf(path) as (document, raw_preflight):
        return _DxfEquivalenceState(
            snapshot=snapshot_document(document, raw_preflight=raw_preflight),
            raw_section_structure_digest=raw_preflight.section_structure_digest,
            raw_header_manifest_digest=raw_preflight.raw_header_manifest_digest,
            raw_modeled_records_digest=_modeled_records_equivalence_digest(
                raw_preflight,
                document,
            ),
            raw_classes_wire_manifest_digest=raw_preflight.classes_wire_manifest_digest,
            raw_classes_wire_multiset_digest=raw_preflight.classes_wire_multiset_digest,
        )


def _modeled_records_equivalence_digest(raw_preflight: object, document: object) -> str:
    """Digest raw modeled records after only the fixed writer timestamp omission."""

    # The one `EZDXF_META/WRITTEN_BY_EZDXF` tag is already a documented
    # snapshot volatility. Keep it out of dual raw comparison as well, while
    # retaining every other raw tag, record order, handle, and section.
    ignored = _volatile_object_tag_codes(document)
    records = []
    for record in raw_preflight.modeled_records:  # type: ignore[attr-defined]
        ignored_codes = ignored.get(record.handle, frozenset())
        records.append(
            {
                "section": record.section,
                "record_type": record.record_type,
                "handle": record.handle,
                "tags": [
                    [code, value]
                    for code, value in record.canonical_tags
                    if code not in ignored_codes
                ],
            }
        )
    return canonical_sha256({"records": records})


def _require_equivalent_dxf_states(
    first: _DxfEquivalenceState,
    second: _DxfEquivalenceState,
) -> None:
    """Reject raw-section, snapshot, order, table, object, or semantic drift."""

    if (
        first.raw_section_structure_digest != second.raw_section_structure_digest
        or first.raw_header_manifest_digest != second.raw_header_manifest_digest
        or first.raw_modeled_records_digest != second.raw_modeled_records_digest
        or first.raw_classes_wire_manifest_digest
        != second.raw_classes_wire_manifest_digest
        or first.raw_classes_wire_multiset_digest
        != second.raw_classes_wire_multiset_digest
        or first.snapshot != second.snapshot
    ):
        raise PipelineError(
            ErrorCode.ODA_OUTPUT_INCOMPATIBLE,
            "independent converter outputs are not equivalent",
        )


def _require_equivalent_outputs(
    first: _AcceptedConverterOutput,
    second: _AcceptedConverterOutput,
    *,
    workspace: PrivateWorkspace,
    converter: OdaRunner,
    output_type: str,
    stage_name: str,
    expected_state_proof: Callable[[Path], None] | None = None,
) -> None:
    """Require two random ODA runs plus complete state proof before selection."""

    first.require_binding()
    second.require_binding()
    if output_type == "DXF":
        _require_equivalent_dxf_states(
            _complete_dxf_equivalence_state(first.path),
            _complete_dxf_equivalence_state(second.path),
        )
        return

    # A DWG has no safe in-process parser. Each independently post-open-bound
    # DWG is reverse-converted through the same dual path, then both complete
    # snapshots must agree. The optional callback proves expected
    # before-minus-target state for *each* candidate before either is selected.
    first_dxf = staged_dwg_to_dxf(
        first.path,
        workspace,
        converter,
        stage_name=f"{stage_name}-first-dwg-check",
    )
    second_dxf = staged_dwg_to_dxf(
        second.path,
        workspace,
        converter,
        stage_name=f"{stage_name}-second-dwg-check",
    )
    _require_equivalent_dxf_states(
        _complete_dxf_equivalence_state(first_dxf),
        _complete_dxf_equivalence_state(second_dxf),
    )
    if expected_state_proof is not None:
        expected_state_proof(first_dxf)
        expected_state_proof(second_dxf)


def _staged_convert(
    source: Path | SourcePathLease,
    workspace: PrivateWorkspace,
    converter: OdaRunner,
    *,
    input_suffix: str,
    output_type: str,
    stage_name: str,
    register_output: Callable[[Path], Path] | None = None,
    expected_state_proof: Callable[[Path], None] | None = None,
) -> Path:
    """Run two CSPRNG-isolated conversions and select only proven output."""

    if (
        not stage_name
        or Path(stage_name).name != stage_name
        or stage_name in {".", ".."}
    ):
        raise PipelineError(ErrorCode.INVALID_ARGUMENT, "invalid conversion stage name")
    owns_source_lease = False
    if isinstance(source, SourcePathLease):
        source_lease = source
    else:
        from .canonical import acquire_source_lease

        source_lease = acquire_source_lease(source, backend=workspace.backend)
        owns_source_lease = True

    outputs: list[_AcceptedConverterOutput] = []
    try:
        for _run_index in range(2):
            # Neither process receives a predictable or shared staging root.
            stage_directory = workspace.create_private_oda_root(
                workspace / f"oda-{secrets.token_hex(24)}"
            )
            input_directory = workspace.create_owned_directory(stage_directory / "input")
            output_directory = workspace.create_owned_directory(stage_directory / "output")
            input_name = f"source-{secrets.token_hex(16)}{input_suffix}"
            _copy_single_staged_input(
                source_lease,
                input_directory,
                input_name,
                workspace=workspace,
            )
            leases = _acquire_converter_invocation_leases(
                input_directory,
                output_directory,
                input_name,
            )
            accepted: _AcceptedConverterOutput | None = None
            try:
                before = _capture_pre_run_inventory(leases)
                expected_output = output_directory / (
                    f"{Path(input_name).stem}.{output_type.lower()}"
                )
                if isinstance(converter, OdaRunner):
                    produced = converter._invoke(leases, before, output_type)
                else:
                    # Test doubles receive the same empty output root, random
                    # source leaf, exact inventories, and post-open adoption.
                    produced = converter.convert(
                        input_directory,
                        output_directory,
                        output_type,
                        register_output=lambda candidate: candidate,
                    )
                if _path_key(produced) != _path_key(expected_output):
                    raise PipelineError(
                        ErrorCode.ODA_OUTPUT_INCOMPATIBLE,
                        "converter returned an unexpected output name",
                    )
                accepted = _adopt_converter_output(
                    workspace,
                    leases,
                    before,
                    expected_output,
                    output_type,
                )
                outputs.append(accepted)
                accepted = None
            finally:
                if accepted is not None:
                    accepted.close()
                leases.close()

        first, redundant = outputs
        _require_equivalent_outputs(
            first,
            redundant,
            workspace=workspace,
            converter=converter,
            output_type=output_type,
            stage_name=f"{stage_name}-dual",
            expected_state_proof=expected_state_proof,
        )
        # Keep the selected candidate's no-write/no-delete handle through
        # audit, re-audit, registrar handoff, and publication. The redundant
        # result is deleted only by its post-open-bound identity.
        first.retain_in_workspace(workspace)
        redundant.discard(workspace)
        if register_output is not None:
            registered = register_output(first.path)
            if _path_key(registered) != _path_key(first.path):
                raise PipelineError(
                    ErrorCode.CONVERSION_FAILURE,
                    "converter output registrar changed the accepted path",
                )
        return first.path
    except BaseException:
        for output in reversed(outputs):
            try:
                output.close()
            except PipelineError:
                pass
        abort = getattr(register_output, "abort", None)
        if callable(abort):
            abort()
        raise
    finally:
        if owns_source_lease:
            source_lease.close()


def staged_dwg_to_dxf(
    source: Path | SourcePathLease,
    workspace: PrivateWorkspace,
    converter: OdaRunner,
    *,
    stage_name: str = "dwg-to-dxf",
) -> Path:
    """Convert a DWG through two random private ODA-to-DXF runs."""

    return _staged_convert(
        source,
        workspace,
        converter,
        input_suffix=".dwg",
        output_type="DXF",
        stage_name=stage_name,
    )


def staged_dxf_to_dwg(
    source: Path | SourcePathLease,
    workspace: PrivateWorkspace,
    converter: OdaRunner,
    *,
    stage_name: str = "dxf-to-dwg",
    register_output: Callable[[Path], Path] | None = None,
    expected_state_proof: Callable[[Path], None] | None = None,
) -> Path:
    """Convert an edited DXF through dual DWG and reverse-state proof."""

    return _staged_convert(
        source,
        workspace,
        converter,
        input_suffix=".dxf",
        output_type="DWG",
        stage_name=stage_name,
        register_output=register_output,
        expected_state_proof=expected_state_proof,
    )
