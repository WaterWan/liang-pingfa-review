"""Handle- and identity-bound Windows ownership primitives for temporary files."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from hashlib import sha256
import ctypes
import io
import json
import os
from pathlib import Path
import ntpath
import re
import secrets
import tempfile
from typing import BinaryIO, Protocol, TextIO


_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_DELETE = 0x00010000
_READ_CONTROL = 0x00020000
_WRITE_DAC = 0x00040000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_CREATE_NEW = 1
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_RENAME_INFO = 3
_FILE_DISPOSITION_INFO = 4
_ERROR_FILE_EXISTS = 80
_ERROR_ALREADY_EXISTS = 183
_PRIVATE_DIRECTORY_CREATE_ATTEMPTS = 8
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "aux",
        "com1",
        "com2",
        "com3",
        "com4",
        "com5",
        "com6",
        "com7",
        "com8",
        "com9",
        "con",
        "lpt1",
        "lpt2",
        "lpt3",
        "lpt4",
        "lpt5",
        "lpt6",
        "lpt7",
        "lpt8",
        "lpt9",
        "nul",
        "prn",
    }
)


class OwnershipError(RuntimeError):
    """Base class for a failed identity-bound ownership operation."""


class OwnershipLostError(OwnershipError):
    """The requested path no longer names the exact application-owned object."""


class OwnershipCleanupError(OwnershipError):
    """An owned object could not be disposed of through its opened identity."""


class DestinationExistsError(OwnershipError):
    """A no-replace rename found an independently created destination."""


def is_reparse_point(path: Path) -> bool:
    """Return whether ``path`` is a link-like object without following it.

    ``Path.is_symlink()`` deliberately does not identify NTFS junctions.
    Cleanup must treat every reparse point as quarantined content before it
    decides whether a directory is safe to descend into.
    """

    try:
        if path.is_symlink():
            return True
        if os.name != "nt":
            return False
        isjunction = getattr(os.path, "isjunction", None)
        if callable(isjunction) and isjunction(path):
            return True
        attributes = int(getattr(path.lstat(), "st_file_attributes", 0))
        return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError as error:
        raise OwnershipLostError("reparse state is unavailable") from error


@dataclass(frozen=True)
class FileIdentity:
    """Opaque, privacy-safe identity inputs returned by an open file handle."""

    namespace: str
    first: int
    second: int
    creation_time_100ns: int

    def fingerprint(self) -> str:
        """Return a stable SHA-256 fingerprint without disclosing raw IDs."""

        payload = {
            "creation_time_100ns": self.creation_time_100ns,
            "first": self.first,
            "namespace": self.namespace,
            "second": self.second,
        }
        return sha256(
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class OwnedPathBinding:
    """Exact content and identity binding for one application-owned pathname."""

    path: Path
    identity: FileIdentity
    byte_size: int | None
    sha256: str | None
    is_directory: bool

    @property
    def file_identity_fingerprint(self) -> str:
        """Return the artifact-safe fingerprint for this opened identity."""

        return self.identity.fingerprint()

    def same_identity_and_content(self, other: "OwnedPathBinding") -> bool:
        """Compare bindings while intentionally ignoring their path spellings."""

        return (
            self.is_directory == other.is_directory
            and self.identity == other.identity
            and self.byte_size == other.byte_size
            and self.sha256 == other.sha256
        )


class OwnedPath(Protocol):
    """An open object whose identity remains protected while the handle lives."""

    path: Path

    def copy_from(self, source: Path) -> None:
        """Copy a source into a newly created writable regular file."""

    def read_chunks(self, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        """Yield bytes through this retained regular-file handle."""

    def write_chunks(self, chunks: Iterable[bytes]) -> None:
        """Write and fsync chunks through this retained regular-file handle."""

    def write_bytes(self, payload: bytes) -> None:
        """Write and fsync bytes through a newly created retained handle."""

    def write_text(self, writer: Callable[[TextIO], None]) -> None:
        """Write UTF-8 text through this retained regular-file handle."""

    def read_prefix(self, length: int) -> bytes:
        """Read a short prefix through the retained regular-file handle."""

    def capture_binding(self) -> OwnedPathBinding:
        """Hash and identify the open object through its held handle."""

    def final_path(self) -> Path:
        """Return the canonical path of this still-open Windows object."""

    def rename_no_replace(self, destination: Path) -> None:
        """Rename this open file to a new destination without replacement."""

    def request_delete(self) -> None:
        """Mark this exact opened object for deletion."""

    def close(self) -> None:
        """Release the held ownership handle."""


class FileOwnershipBackend(Protocol):
    """Factory and inspection surface used by publication and cleanup code."""

    def create_new_file(self, path: Path) -> OwnedPath:
        """Create a regular file once and retain its ownership handle."""

    def create_private_file(self, path: Path) -> OwnedPath:
        """Create a secret-bearing file with no sharing before its DACL is set."""

    def open_existing_file(self, path: Path, *, for_delete: bool) -> OwnedPath:
        """Open an existing regular file while optionally denying replacement."""

    def open_existing_file_read_lease(self, path: Path) -> OwnedPath:
        """Open a file with read-only sharing that denies writers and deleters."""

    def open_existing_directory(self, path: Path, *, for_delete: bool) -> OwnedPath:
        """Open an existing directory while optionally denying replacement."""

    def open_existing_directory_read_lease(self, path: Path) -> OwnedPath:
        """Open a directory while denying child-writer and delete sharing."""

    def open_output_parent_directory(self, path: Path) -> OwnedPath:
        """Open a parent directory that denies DELETE sharing but is not deletable."""

    def path_exists(self, path: Path) -> bool:
        """Return whether a directory entry currently exists, including links."""

    def path_matches_binding(self, path: Path, binding: OwnedPathBinding) -> bool:
        """Return whether a pathname resolves to the recorded open identity."""


@dataclass
class _DirectoryChainComponent:
    """One no-follow lexical directory component retained by a chain lease."""

    lexical_path: Path
    path: Path
    owned: OwnedPath
    binding: OwnedPathBinding
    _closed: bool = False

    def close(self) -> None:
        """Release this component exactly once."""

        if self._closed:
            return
        self.owned.close()
        self._closed = True


@dataclass
class LexicalDirectoryChainLease:
    """Retain every no-follow directory from a lexical root to one target.

    A final directory handle alone cannot prove that an earlier lexical
    component was not a junction when it was traversed.  This lease opens
    every component independently with ``OPEN_REPARSE_POINT``, captures its
    identity, and retains every handle until the caller completes the
    path-based operation.  The only canonical spelling exposed by the lease
    comes from the final retained handle.
    """

    components: tuple[_DirectoryChainComponent, ...]
    backend: FileOwnershipBackend
    _closed: bool = False

    @property
    def lexical_path(self) -> Path:
        """Return the caller's validated lexical final directory spelling."""

        return self.components[-1].lexical_path

    @property
    def path(self) -> Path:
        """Return the final directory path reported by its retained handle."""

        return self.components[-1].path

    @property
    def owned(self) -> OwnedPath:
        """Expose the retained final directory handle to boundary adapters."""

        return self.components[-1].owned

    @property
    def binding(self) -> OwnedPathBinding:
        """Return the identity binding captured from the final handle."""

        return self.components[-1].binding

    def require_binding(self) -> None:
        """Require every lexical component still names its held identity."""

        if self._closed:
            raise OwnershipLostError("directory chain lease was released")
        for component in self.components:
            if component._closed:
                raise OwnershipLostError("directory chain component was released")
            current = component.owned.capture_binding()
            if (
                not current.is_directory
                or not current.same_identity_and_content(component.binding)
                or not self.backend.path_matches_binding(
                    component.lexical_path,
                    current,
                )
                or not self.backend.path_matches_binding(component.path, current)
            ):
                raise OwnershipLostError("lexical directory chain identity changed")

    def close(self) -> None:
        """Release retained components deepest-first, preserving first failure."""

        if self._closed:
            return
        failure: BaseException | None = None
        for component in reversed(self.components):
            try:
                component.close()
            except (OSError, OwnershipError) as error:
                if failure is None:
                    failure = error
        self._closed = True
        if failure is not None:
            if isinstance(failure, OwnershipError):
                raise failure
            raise OwnershipCleanupError("directory chain handle close failed") from failure


def lexical_absolute_path(path: Path, *, cwd: Path | None = None) -> Path:
    """Anchor one local relative spelling without resolving any filesystem link.

    Public paths may be written relative to the captured current working
    directory (for example ``.{sep}output{sep}audit.json`` where ``sep`` is
    the platform separator).  ``resolve()`` is not
    suitable here because it follows the very junctions that the retained
    ancestor chain must reject.  This helper performs only string-level
    joining and dot normalization; callers must still pass its result through
    :func:`acquire_lexical_directory_chain` before opening any target.
    """

    raw_path = os.fspath(path)
    if not raw_path or "\x00" in raw_path:
        raise OwnershipLostError("path is invalid")

    windows_form = (
        os.name == "nt"
        or bool(getattr(path, "drive", ""))
        or (len(raw_path) >= 2 and raw_path[1] == ":")
    )
    if windows_form:
        normalized = raw_path.replace("/", "\\")
        drive, tail = ntpath.splitdrive(normalized)
        if (
            normalized.startswith("\\\\")
            or normalized.startswith("\\")
            or (drive and not (len(drive) == 2 and drive[0].isalpha()))
            or (drive and not tail.startswith("\\"))
            or any(part == ".." for part in normalized.split("\\"))
        ):
            raise OwnershipLostError("path is not a normal local drive path")
        if drive:
            candidate = Path(ntpath.normpath(normalized))
        else:
            root = Path(os.fspath(cwd)) if cwd is not None else Path(os.getcwd())
            root_text = os.fspath(root).replace("/", "\\")
            root_drive, root_tail = ntpath.splitdrive(root_text)
            if (
                not root_drive
                or not root_tail.startswith("\\")
                or root_text.startswith("\\\\")
            ):
                raise OwnershipLostError("current directory is not a local drive path")
            candidate = Path(ntpath.normpath(ntpath.join(root_text, normalized)))
    else:
        if any(part == ".." for part in raw_path.split("/")):
            raise OwnershipLostError("path traversal is unsupported")
        if raw_path.startswith("/"):
            candidate = Path(os.path.normpath(raw_path))
        else:
            root = Path(os.fspath(cwd)) if cwd is not None else Path(os.getcwd())
            if not root.is_absolute():
                raise OwnershipLostError("current directory is not absolute")
            candidate = Path(os.path.normpath(os.path.join(os.fspath(root), raw_path)))

    if not candidate.is_absolute():
        raise OwnershipLostError("path is not absolute")
    return candidate


def _validated_lexical_file(path: Path) -> Path:
    """Reject non-local file syntax before the source file is opened."""

    lexical = lexical_absolute_path(path)
    raw_path = os.fspath(lexical)
    windows_form = (
        os.name == "nt"
        or bool(getattr(lexical, "drive", ""))
        or (len(raw_path) >= 2 and raw_path[1] == ":")
    )
    if windows_form:
        normalized = raw_path.replace("/", "\\")
        drive, tail = ntpath.splitdrive(normalized)
        if (
            normalized.startswith("\\\\")
            or len(drive) != 2
            or not drive[0].isalpha()
            or not tail.startswith("\\")
            or _contains_lexical_traversal(normalized, windows_form=True)
        ):
            raise OwnershipLostError("source path is not a normal local drive path")
        components = [part for part in tail.split("\\") if part]
    else:
        if (
            not lexical.is_absolute()
            or _contains_lexical_traversal(raw_path, windows_form=False)
        ):
            raise OwnershipLostError("source path is not absolute")
        components = [part for part in raw_path.split("/") if part]
    if (
        not components
        or lexical.name in {"", ".", ".."}
        or any(":" in component for component in components)
        or any(component != component.rstrip(" .") for component in components)
        or lexical.name.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES
    ):
        # A colon after the drive designator names an alternate data stream
        # on NTFS.  Device and UNC forms were rejected before this point.
        raise OwnershipLostError("source path has an unsupported stream or name")
    return lexical


def _contains_lexical_traversal(raw_path: str, *, windows_form: bool) -> bool:
    """Return whether a raw pathname contains a dot traversal component."""

    if windows_form:
        separator = chr(92)
        pieces = raw_path.replace("/", separator).split(separator)
    else:
        pieces = raw_path.split("/")
    return any(piece in {".", ".."} for piece in pieces)


def _validated_lexical_directory(path: Path) -> Path:
    """Accept only a normal absolute local path without lexical escapes.

    UNC, device, drive-relative, and relative forms have semantics that this
    local retained-handle model does not explicitly represent.  Reject them
    rather than canonicalizing them with ``resolve()`` or ``abspath()``.
    """

    raw_path = os.fspath(path)
    if not raw_path or "\x00" in raw_path:
        raise OwnershipLostError("directory path is invalid")

    windows_form = (
        bool(getattr(path, "drive", ""))
        or (len(raw_path) >= 2 and raw_path[1] == ":")
        or os.name == "nt"
    )
    if windows_form:
        separator = chr(92)
        normalized = raw_path.replace("/", separator)
        if (
            normalized.startswith(separator * 2)
            or len(normalized) < 3
            or normalized[1] != ":"
            or normalized[2] != separator
            or not normalized[0].isalpha()
            or _contains_lexical_traversal(normalized, windows_form=True)
        ):
            raise OwnershipLostError("directory path is not a normal local drive path")
        tail = normalized[3:]
        if any(
            part != part.rstrip(" .")
            or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES
            for part in tail.split(separator)
            if part
        ):
            raise OwnershipLostError("directory path has an unsupported component")
    elif (
        not path.is_absolute()
        or _contains_lexical_traversal(raw_path, windows_form=False)
    ):
        raise OwnershipLostError("directory path is not absolute")

    if not path.is_absolute():
        raise OwnershipLostError("directory path is not absolute")
    return path


def _lexical_directory_components(path: Path) -> tuple[Path, ...]:
    """Build root-to-leaf paths without resolving any directory entry."""

    lexical = _validated_lexical_directory(path)
    anchor = lexical.anchor
    if not anchor:
        raise OwnershipLostError("directory root is unavailable")
    current = Path(anchor)
    components = [current]
    for part in lexical.parts[1:]:
        if part in {"", ".", ".."}:
            raise OwnershipLostError("directory path contains traversal")
        current = current / part
        components.append(current)
    return tuple(components)


def acquire_lexical_directory_chain(
    path: Path,
    backend: FileOwnershipBackend,
    *,
    final_directory_opener: Callable[[Path], OwnedPath] | None = None,
) -> LexicalDirectoryChainLease:
    """Open and retain every existing lexical directory component no-follow.

    Ancestors use the normal output-parent sharing mode: they allow intended
    child creation while denying delete/rename replacement.  Callers may use
    a stricter final opener (ODA input staging does) when the target directory
    must also reject child writers.
    """

    lexical_components = _lexical_directory_components(path)
    retained: list[_DirectoryChainComponent] = []
    try:
        for index, lexical_component in enumerate(lexical_components):
            opener = (
                final_directory_opener
                if index == len(lexical_components) - 1
                and final_directory_opener is not None
                else backend.open_output_parent_directory
            )
            opened = opener(lexical_component)
            try:
                binding = opened.capture_binding()
                final_path = opened.final_path()
                if (
                    not final_path.is_absolute()
                    or not binding.is_directory
                    or not backend.path_matches_binding(lexical_component, binding)
                    or not backend.path_matches_binding(final_path, binding)
                ):
                    raise OwnershipLostError(
                        "directory component did not retain its binding"
                    )
                retained.append(
                    _DirectoryChainComponent(
                        lexical_path=lexical_component,
                        path=final_path,
                        owned=opened,
                        binding=binding,
                    )
                )
            except BaseException:
                try:
                    opened.close()
                except (OSError, OwnershipError):
                    pass
                raise
        lease = LexicalDirectoryChainLease(tuple(retained), backend)
        lease.require_binding()
        return lease
    except BaseException:
        for component in reversed(retained):
            try:
                component.close()
            except (OSError, OwnershipError):
                pass
        raise


def _lexical_path_key(path: Path) -> str:
    """Compare already-absolute lexical spellings without following links."""

    return os.path.normcase(os.path.normpath(os.fspath(path)))


@dataclass
class SourcePathLease:
    """One no-follow source file and every lexical ancestor retained together.

    The source file is opened only after its parent chain has been retained.
    Its read lease shares only READ access, so a later path lookup cannot
    silently swap the bytes being hashed, staged, or used at publication.
    """

    lexical_path: Path
    path: Path
    owned: OwnedPath
    binding: OwnedPathBinding
    chain: LexicalDirectoryChainLease
    backend: FileOwnershipBackend
    _closed: bool = False

    def require_binding(self) -> None:
        """Re-read the held bytes and require every held name to stay exact."""

        if self._closed:
            raise OwnershipLostError("source lease was released")
        self.chain.require_binding()
        current = self.owned.capture_binding()
        final_path = self.owned.final_path()
        if (
            current.is_directory
            or not current.same_identity_and_content(self.binding)
            or _lexical_path_key(final_path) != _lexical_path_key(self.path)
            or final_path.name.casefold() != self.lexical_path.name.casefold()
            or _lexical_path_key(final_path.parent) != _lexical_path_key(self.chain.path)
            or not self.backend.path_matches_binding(self.lexical_path, current)
            or not self.backend.path_matches_binding(final_path, current)
        ):
            raise OwnershipLostError("source identity or lexical chain changed")

    def read_chunks(self, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        """Stream the immutable held source and verify it again afterwards."""

        self.require_binding()
        try:
            yield from self.owned.read_chunks(chunk_size)
        finally:
            self.require_binding()

    def read_prefix(self, length: int) -> bytes:
        """Read source header bytes only through the retained file handle."""

        self.require_binding()
        prefix = self.owned.read_prefix(length)
        self.require_binding()
        return prefix

    def close(self) -> None:
        """Release file then ancestors, preserving the first close failure."""

        if self._closed:
            return
        failure: BaseException | None = None
        try:
            self.owned.close()
        except (OSError, OwnershipError) as error:
            failure = error
        try:
            self.chain.close()
        except (OSError, OwnershipError) as error:
            if failure is None:
                failure = error
        self._closed = True
        if failure is not None:
            if isinstance(failure, OwnershipError):
                raise failure
            raise OwnershipCleanupError("source lease handle close failed") from failure


def acquire_source_path_lease(
    source: Path,
    backend: FileOwnershipBackend,
) -> SourcePathLease:
    """Open a normal local source through retained no-follow ancestor handles.

    No filesystem resolution, metadata query, header read, or byte read is
    performed before every lexical parent has been opened with
    ``OPEN_REPARSE_POINT`` and the direct source file has its own retained
    no-write/no-delete read handle.
    """

    lexical = _validated_lexical_file(source)
    chain: LexicalDirectoryChainLease | None = None
    opened: OwnedPath | None = None
    try:
        chain = acquire_lexical_directory_chain(
            lexical.parent,
            backend,
            # The source file itself is the immutable no-write/no-delete
            # authority. Its parent must deny replacement of the lexical
            # component while still allowing a distinct public output to be
            # created beside the source during phase two.
            final_directory_opener=backend.open_output_parent_directory,
        )
        chain.require_binding()
        opened = backend.open_existing_file_read_lease(lexical)
        binding = opened.capture_binding()
        final_path = opened.final_path()
        if (
            binding.is_directory
            or final_path.name.casefold() != lexical.name.casefold()
            or _lexical_path_key(final_path.parent) != _lexical_path_key(chain.path)
            or not backend.path_matches_binding(lexical, binding)
            or not backend.path_matches_binding(final_path, binding)
        ):
            raise OwnershipLostError("source is not a direct bound child")
        lease = SourcePathLease(
            lexical_path=lexical,
            path=final_path,
            owned=opened,
            binding=binding,
            chain=chain,
            backend=backend,
        )
        lease.require_binding()
        return lease
    except BaseException:
        if opened is not None:
            try:
                opened.close()
            except (OSError, OwnershipError):
                pass
        if chain is not None:
            try:
                chain.close()
            except (OSError, OwnershipError):
                pass
        raise


def _hash_stream(stream: BinaryIO) -> tuple[str, int]:
    """Hash a regular file through its already-open descriptor."""

    stream.seek(0)
    digest = sha256()
    byte_size = 0
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
        byte_size += len(chunk)
    stream.seek(0)
    return digest.hexdigest(), byte_size


def _path_chunks(source: Path) -> Iterator[bytes]:
    """Yield bounded source chunks without ever selecting an output pathname."""

    with source.open("rb", buffering=0) as input_file:
        while chunk := input_file.read(1024 * 1024):
            yield chunk


def _write_chunks(stream: BinaryIO, chunks: Iterable[bytes]) -> None:
    """Write a complete bounded stream, then flush and fsync the held handle."""

    for chunk in chunks:
        if not isinstance(chunk, bytes):
            raise OwnershipCleanupError("file copy chunk is invalid")
        written = stream.write(chunk)
        if written is not None and written != len(chunk):
            raise OSError("short write through owned file handle")
    stream.flush()
    os.fsync(stream.fileno())
    stream.seek(0)


def _write_text(
    stream: BinaryIO,
    writer: Callable[[TextIO], None],
) -> None:
    """Run a text serializer directly against a retained binary file handle."""

    text_stream = io.TextIOWrapper(
        stream,
        encoding="utf-8",
        newline="",
        write_through=True,
    )
    try:
        writer(text_stream)
        text_stream.flush()
    finally:
        # ``detach`` keeps the underlying owned handle alive for binding and
        # cleanup even when the serializer raises after writing a partial DXF.
        try:
            text_stream.detach()
        except ValueError:
            pass
    stream.flush()
    os.fsync(stream.fileno())
    stream.seek(0)


class _FileTime(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", ctypes.c_uint32),
        ("dwHighDateTime", ctypes.c_uint32),
    ]


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", ctypes.c_uint32),
        ("ftCreationTime", _FileTime),
        ("ftLastAccessTime", _FileTime),
        ("ftLastWriteTime", _FileTime),
        ("dwVolumeSerialNumber", ctypes.c_uint32),
        ("nFileSizeHigh", ctypes.c_uint32),
        ("nFileSizeLow", ctypes.c_uint32),
        ("nNumberOfLinks", ctypes.c_uint32),
        ("nFileIndexHigh", ctypes.c_uint32),
        ("nFileIndexLow", ctypes.c_uint32),
    ]


class _FileRenameInformation(ctypes.Structure):
    _fields_ = [
        ("ReplaceIfExists", ctypes.c_ubyte),
        ("RootDirectory", ctypes.c_void_p),
        ("FileNameLength", ctypes.c_uint32),
        ("FileName", ctypes.c_wchar * 1),
    ]


class _FileDispositionInformation(ctypes.Structure):
    _fields_ = [("DeleteFile", ctypes.c_ubyte)]


class _SecurityAttributes(ctypes.Structure):
    """Win32 SECURITY_ATTRIBUTES retained through CreateDirectoryW."""

    _fields_ = [
        ("nLength", ctypes.c_uint32),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", ctypes.c_int),
    ]


_FILE_RENAME_NAME_OFFSET = _FileRenameInformation.FileName.offset


def _filetime_value(value: _FileTime) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def _rename_information_buffer(
    file_name: str,
    root_directory: int | None = None,
) -> tuple[ctypes.Array[ctypes.c_char], int]:
    """Build a legacy FILE_RENAME_INFO with replacement explicitly disabled."""

    encoded = file_name.encode("utf-16-le")
    if not encoded:
        raise OwnershipCleanupError("empty rename destination")
    # FILE_RENAME_INFO already includes one WCHAR and rounds the structure to
    # pointer alignment. SetFileInformationByHandle validates that documented
    # full structure size, not merely FileName.offset + byte length.
    size = (
        ctypes.sizeof(_FileRenameInformation)
        + len(encoded)
        - ctypes.sizeof(ctypes.c_wchar)
    )
    buffer = ctypes.create_string_buffer(size)
    information = _FileRenameInformation.from_buffer(buffer)
    information.ReplaceIfExists = 0
    information.RootDirectory = root_directory
    information.FileNameLength = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + _FILE_RENAME_NAME_OFFSET, encoded, len(encoded))
    return buffer, size


class WindowsKernelApi(Protocol):
    """Small mockable kernel32 boundary for platform-neutral API tests."""

    def create_directory(
        self,
        path: str,
        security_attributes: _SecurityAttributes,
    ) -> bool:
        """Create a directory once with its final security descriptor."""

    def create_file(
        self,
        path: str,
        desired_access: int,
        share_mode: int,
        creation_disposition: int,
        flags_and_attributes: int,
    ) -> int:
        """Create or open a handle."""

    def close_handle(self, handle: int) -> bool:
        """Close a handle."""

    def get_file_information(self, handle: int) -> _ByHandleFileInformation | None:
        """Fetch identity metadata."""

    def get_file_size(self, handle: int) -> int | None:
        """Fetch a file size through its handle."""

    def get_final_path_name(self, handle: int) -> str | None:
        """Fetch the canonical final path for this exact open handle."""

    def set_file_information(
        self,
        handle: int,
        information_class: int,
        information: object,
        information_size: int,
    ) -> bool:
        """Set rename or disposition metadata."""

    def last_error(self) -> int:
        """Return the last Win32 error code."""


class NativeWindowsKernelApi:
    """ctypes-backed kernel32 adapter, constructed only on Windows."""

    def __init__(self) -> None:
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        except AttributeError as error:
            raise OwnershipCleanupError("Windows kernel API is unavailable") from error
        self._create_file = kernel32.CreateFileW
        self._create_file.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        self._create_file.restype = ctypes.c_void_p
        self._create_directory = kernel32.CreateDirectoryW
        self._create_directory.argtypes = [
            ctypes.c_wchar_p,
            ctypes.POINTER(_SecurityAttributes),
        ]
        self._create_directory.restype = ctypes.c_int
        self._close_handle = kernel32.CloseHandle
        self._close_handle.argtypes = [ctypes.c_void_p]
        self._close_handle.restype = ctypes.c_int
        self._get_file_information = kernel32.GetFileInformationByHandle
        self._get_file_information.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_ByHandleFileInformation),
        ]
        self._get_file_information.restype = ctypes.c_int
        self._get_file_size = kernel32.GetFileSizeEx
        self._get_file_size.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_longlong)]
        self._get_file_size.restype = ctypes.c_int
        self._get_final_path_name = kernel32.GetFinalPathNameByHandleW
        self._get_final_path_name.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        self._get_final_path_name.restype = ctypes.c_uint32
        self._set_file_information = kernel32.SetFileInformationByHandle
        self._set_file_information.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        self._set_file_information.restype = ctypes.c_int

    def create_file(
        self,
        path: str,
        desired_access: int,
        share_mode: int,
        creation_disposition: int,
        flags_and_attributes: int,
    ) -> int:
        handle = self._create_file(
            path,
            desired_access,
            share_mode,
            None,
            creation_disposition,
            flags_and_attributes,
            None,
        )
        return int(handle) if handle else 0

    def create_directory(
        self,
        path: str,
        security_attributes: _SecurityAttributes,
    ) -> bool:
        """Use CreateDirectoryW with a descriptor already attached."""

        return bool(
            self._create_directory(
                path,
                ctypes.byref(security_attributes),
            )
        )

    def close_handle(self, handle: int) -> bool:
        return bool(self._close_handle(ctypes.c_void_p(handle)))

    def get_file_information(self, handle: int) -> _ByHandleFileInformation | None:
        information = _ByHandleFileInformation()
        if not self._get_file_information(
            ctypes.c_void_p(handle), ctypes.byref(information)
        ):
            return None
        return information

    def get_file_size(self, handle: int) -> int | None:
        size = ctypes.c_longlong()
        if not self._get_file_size(ctypes.c_void_p(handle), ctypes.byref(size)):
            return None
        return int(size.value)

    def get_final_path_name(self, handle: int) -> str | None:
        """Read a DOS-style final path without a pathname re-open."""

        capacity = 512
        for _attempt in range(4):
            buffer = ctypes.create_unicode_buffer(capacity)
            length = int(
                self._get_final_path_name(
                    ctypes.c_void_p(handle),
                    buffer,
                    capacity,
                    0,
                )
            )
            if length == 0:
                return None
            if length < capacity:
                return str(buffer.value)
            # The API reports the required size when the buffer is too small.
            # Add one defensively for variants that exclude the trailing NUL.
            capacity = length + 1
        return None

    def set_file_information(
        self,
        handle: int,
        information_class: int,
        information: object,
        information_size: int,
    ) -> bool:
        return bool(
            self._set_file_information(
                ctypes.c_void_p(handle),
                information_class,
                ctypes.byref(information),
                information_size,
            )
        )

    def last_error(self) -> int:
        return int(ctypes.get_last_error())


def _windows_identity(information: _ByHandleFileInformation) -> FileIdentity:
    return FileIdentity(
        namespace="windows-file-id",
        first=int(information.dwVolumeSerialNumber),
        second=(
            (int(information.nFileIndexHigh) << 32)
            | int(information.nFileIndexLow)
        ),
        creation_time_100ns=_filetime_value(information.ftCreationTime),
    )


def _path_from_final_handle_name(value: str) -> Path:
    """Normalize a final-handle DOS path without resolving it again."""

    separator = chr(92)
    extended_unc = separator * 2 + "?" + separator + "UNC" + separator
    extended = separator * 2 + "?" + separator
    if value.startswith(extended_unc):
        value = separator * 2 + value[len(extended_unc) :]
    elif value.startswith(extended):
        value = value[len(extended) :]
    if not value:
        raise OwnershipLostError("final handle path is unavailable")
    return Path(value)


class WindowsOwnedPath:
    """A Windows file or directory whose handle denies writer/delete sharing."""

    def __init__(
        self,
        backend: "WindowsFileOwnershipBackend",
        path: Path,
        handle: int,
        stream: BinaryIO | None,
        *,
        is_directory: bool,
    ) -> None:
        self._backend = backend
        self.path = path
        self._handle = handle
        self._stream = stream
        self._is_directory = is_directory
        self._delete_requested = False

    @property
    def handle(self) -> int:
        """Expose the native handle only to the isolated backend implementation."""

        return self._handle

    def _information(self) -> _ByHandleFileInformation:
        information = self._backend.api.get_file_information(self._handle)
        if information is None:
            raise OwnershipLostError("handle identity is unavailable")
        return information

    def _size(self) -> int:
        size = self._backend.api.get_file_size(self._handle)
        if size is None or size < 0:
            raise OwnershipLostError("handle size is unavailable")
        return size

    def _validate_type(self, information: _ByHandleFileInformation) -> None:
        attributes = int(information.dwFileAttributes)
        if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise OwnershipLostError("reparse object is not owned")
        if self._is_directory != bool(attributes & _FILE_ATTRIBUTE_DIRECTORY):
            raise OwnershipLostError("object type changed")

    def copy_from(self, source: Path) -> None:
        if self._stream is None or self._is_directory:
            raise OwnershipCleanupError("not a writable regular file")
        _write_chunks(self._stream, _path_chunks(source))

    def read_chunks(self, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        if self._stream is None or self._is_directory or chunk_size <= 0:
            raise OwnershipLostError("not a readable regular file")
        self._stream.seek(0)
        try:
            while chunk := self._stream.read(chunk_size):
                yield chunk
        finally:
            self._stream.seek(0)

    def write_chunks(self, chunks: Iterable[bytes]) -> None:
        if self._stream is None or self._is_directory:
            raise OwnershipCleanupError("not a writable regular file")
        _write_chunks(self._stream, chunks)

    def write_bytes(self, payload: bytes) -> None:
        if self._stream is None or self._is_directory:
            raise OwnershipCleanupError("not a writable regular file")
        _write_chunks(self._stream, (payload,))

    def write_text(self, writer: Callable[[TextIO], None]) -> None:
        if self._stream is None or self._is_directory:
            raise OwnershipCleanupError("not a writable regular file")
        _write_text(self._stream, writer)

    def read_prefix(self, length: int) -> bytes:
        if self._stream is None or self._is_directory:
            raise OwnershipLostError("not a regular file")
        self._stream.seek(0)
        prefix = self._stream.read(length)
        self._stream.seek(0)
        return prefix

    def capture_binding(self) -> OwnedPathBinding:
        before = self._information()
        self._validate_type(before)
        if self._is_directory:
            return OwnedPathBinding(
                path=self.path,
                identity=_windows_identity(before),
                byte_size=None,
                sha256=None,
                is_directory=True,
            )
        if self._stream is None:
            raise OwnershipLostError("regular file handle has no readable stream")
        expected_size = self._size()
        digest, byte_size = _hash_stream(self._stream)
        after = self._information()
        self._validate_type(after)
        actual_size = self._size()
        if (
            _windows_identity(before) != _windows_identity(after)
            or expected_size != actual_size
            or byte_size != actual_size
        ):
            raise OwnershipLostError("object changed while binding")
        return OwnedPathBinding(
            path=self.path,
            identity=_windows_identity(after),
            byte_size=byte_size,
            sha256=digest,
            is_directory=False,
        )

    def final_path(self) -> Path:
        """Return this object's canonical path from the currently held handle."""

        if self._handle == 0:
            raise OwnershipLostError("final path requested after handle close")
        value = self._backend.api.get_final_path_name(self._handle)
        if value is None:
            raise OwnershipLostError("handle final path is unavailable")
        return _path_from_final_handle_name(value)

    def rename_no_replace(self, destination: Path) -> None:
        if self._is_directory:
            raise OwnershipCleanupError("directories cannot be published")
        self._backend.rename_open_file_no_replace(self._handle, destination)
        self.path = destination

    def request_delete(self) -> None:
        if self._delete_requested:
            return
        information = _FileDispositionInformation()
        information.DeleteFile = 1
        if not self._backend.api.set_file_information(
            self._handle,
            _FILE_DISPOSITION_INFO,
            information,
            ctypes.sizeof(information),
        ):
            raise OwnershipCleanupError("handle deletion failed")
        self._delete_requested = True

    def close(self) -> None:
        if self._handle == 0:
            return
        handle = self._handle
        self._handle = 0
        if self._stream is not None:
            stream = self._stream
            self._stream = None
            stream.close()
            return
        if not self._backend.api.close_handle(handle):
            raise OwnershipCleanupError("handle close failed")


class WindowsFileOwnershipBackend:
    """Production backend using CreateFileW and SetFileInformationByHandle."""

    def __init__(self, api: WindowsKernelApi | None = None) -> None:
        self.api: WindowsKernelApi = api or NativeWindowsKernelApi()

    def _open(
        self,
        path: Path,
        *,
        desired_access: int,
        share_mode: int,
        creation_disposition: int,
        is_directory: bool,
        writable_stream: bool,
        readable_stream: bool = True,
    ) -> WindowsOwnedPath:
        flags = _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT
        if is_directory:
            flags |= _FILE_FLAG_BACKUP_SEMANTICS
        handle = self.api.create_file(
            str(path),
            desired_access,
            share_mode,
            creation_disposition,
            flags,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if handle in (0, None, invalid_handle):
            raise OwnershipLostError("Windows handle open failed")
        native_handle = int(handle)
        stream: BinaryIO | None = None
        if not is_directory and readable_stream:
            try:
                import msvcrt

                descriptor = msvcrt.open_osfhandle(native_handle, os.O_BINARY)
                stream = os.fdopen(
                    descriptor,
                    "r+b" if writable_stream else "rb",
                    buffering=0,
                )
            except (OSError, ValueError) as error:
                self.api.close_handle(native_handle)
                raise OwnershipCleanupError("file descriptor conversion failed") from error
        return WindowsOwnedPath(
            self,
            path,
            native_handle,
            stream,
            is_directory=is_directory,
        )

    def create_new_file(self, path: Path) -> WindowsOwnedPath:
        return self._open(
            path,
            desired_access=_GENERIC_READ | _GENERIC_WRITE | _DELETE,
            share_mode=_FILE_SHARE_READ,
            creation_disposition=_CREATE_NEW,
            is_directory=False,
            writable_stream=True,
        )

    def create_private_file(self, path: Path) -> WindowsOwnedPath:
        """Create a descriptor with no reader/writer sharing window."""

        return self._open(
            path,
            desired_access=(
                _GENERIC_READ
                | _GENERIC_WRITE
                | _DELETE
                | _READ_CONTROL
                | _WRITE_DAC
            ),
            share_mode=0,
            creation_disposition=_CREATE_NEW,
            is_directory=False,
            writable_stream=True,
        )

    def open_existing_file(
        self, path: Path, *, for_delete: bool
    ) -> WindowsOwnedPath:
        return self._open(
            path,
            desired_access=_GENERIC_READ | (_DELETE if for_delete else 0),
            # Inspection opens must allow the existing retained owner handle's
            # READ/WRITE/DELETE access. The retained handle itself still
            # denies future writers and deleters.
            share_mode=(
                _FILE_SHARE_READ
                if for_delete
                else _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE
            ),
            creation_disposition=_OPEN_EXISTING,
            is_directory=False,
            writable_stream=False,
        )

    def open_existing_file_read_lease(self, path: Path) -> WindowsOwnedPath:
        """Open a staged source without requiring DELETE sharing from readers."""

        return self._open(
            path,
            desired_access=_GENERIC_READ,
            share_mode=_FILE_SHARE_READ,
            creation_disposition=_OPEN_EXISTING,
            is_directory=False,
            writable_stream=False,
        )

    def open_existing_directory(
        self, path: Path, *, for_delete: bool
    ) -> WindowsOwnedPath:
        return self._open(
            path,
            desired_access=_GENERIC_READ | (_DELETE if for_delete else 0),
            share_mode=(
                _FILE_SHARE_READ
                if for_delete
                else _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE
            ),
            creation_disposition=_OPEN_EXISTING,
            is_directory=True,
            writable_stream=False,
        )

    def open_existing_directory_read_lease(self, path: Path) -> WindowsOwnedPath:
        """Lease an ODA input root without sharing write or delete access.

        ODA needs only to open the already-bound child source as a reader.
        Refusing WRITE sharing on the directory asks Windows to reject another
        process that tries to create or alter direct children while the
        converter invocation is in flight.
        """

        return self._open(
            path,
            desired_access=_GENERIC_READ,
            share_mode=_FILE_SHARE_READ,
            creation_disposition=_OPEN_EXISTING,
            is_directory=True,
            writable_stream=False,
        )

    def open_output_parent_directory(self, path: Path) -> WindowsOwnedPath:
        """Lease a parent without blocking child creates and handle renames.

        The lease omits ``FILE_SHARE_DELETE`` so a junction/rename/delete of
        the directory cannot coexist.  It deliberately does *not* request
        DELETE access itself: a full-path FileRenameInfo operation may need
        normal parent-directory sharing, while no parent deletion capability
        is required for this guard.
        """

        return self._open(
            path,
            desired_access=_GENERIC_READ,
            share_mode=_FILE_SHARE_READ | _FILE_SHARE_WRITE,
            creation_disposition=_OPEN_EXISTING,
            is_directory=True,
            writable_stream=False,
        )

    def path_exists(self, path: Path) -> bool:
        return os.path.lexists(path)

    def rename_open_file_no_replace(self, handle: int, destination: Path) -> None:
        """Rename using a full destination path and zero replacement flags."""

        buffer, size = _rename_information_buffer(str(destination))
        if not self.api.set_file_information(
            handle,
            _FILE_RENAME_INFO,
            buffer,
            size,
        ):
            error_number = self.api.last_error()
            if error_number in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}:
                raise DestinationExistsError("destination exists")
            raise OwnershipCleanupError("handle rename failed")

    def path_matches_binding(self, path: Path, binding: OwnedPathBinding) -> bool:
        try:
            opened = (
                self.open_existing_directory(path, for_delete=False)
                if binding.is_directory
                else self.open_existing_file(path, for_delete=False)
            )
        except OwnershipError:
            return False
        try:
            return opened.capture_binding().same_identity_and_content(binding)
        except OwnershipError:
            return False
        finally:
            opened.close()


_DACL_SECURITY_INFORMATION = 0x00000004
_OWNER_SECURITY_INFORMATION = 0x00000001
_SE_FILE_OBJECT = 1
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_UNPROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
_TOKEN_QUERY = 0x0008
_TOKEN_USER_INFORMATION_CLASS = 1
_TOKEN_OWNER_INFORMATION_CLASS = 4
_SYSTEM_SID = "S-1-5-18"
_ADMINISTRATORS_SID = "S-1-5-32-544"


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [
        ("Sid", ctypes.c_void_p),
        ("Attributes", ctypes.c_uint32),
    ]


class _TokenUser(ctypes.Structure):
    _fields_ = [("User", _SidAndAttributes)]


class _TokenOwner(ctypes.Structure):
    _fields_ = [("Owner", ctypes.c_void_p)]


@dataclass(frozen=True)
class PrivateStagingCapability:
    """Redacted readiness result for private ODA staging."""

    windows: bool
    ntfs: bool
    dacl: bool

    @property
    def ready(self) -> bool:
        """Return whether all public ODA staging prerequisites are available."""

        return self.windows and self.ntfs and self.dacl


@dataclass(frozen=True)
class PublicOutputAclPolicy:
    """An opaque, handle-captured DACL policy for a future public final.

    A public-parent temporary starts with this inherited policy, then receives
    a restrictive private DACL while its exclusive creation handle is held.
    The policy is restored through that same handle immediately before its
    no-replace final rename.  It is intentionally process-local and never
    appears in a report or verification artifact.
    """

    dacl_sddl: str


@dataclass
class PrivateDirectoryCreation:
    """One atomically private directory held until lexical registration.

    ``CreateDirectoryW`` does not return a handle, so this object retains the
    immediate no-follow reopen used to bind and verify the exact new directory.
    If later registration fails, ``dispose`` deletes that same identity rather
    than resolving a pathname again.
    """

    path: Path
    binding: OwnedPathBinding
    opened: OwnedPath | None

    def close(self) -> None:
        """Release the initial verification handle before a compatible lease."""

        if self.opened is None:
            return
        opened = self.opened
        self.opened = None
        opened.close()

    def dispose(self, backend: FileOwnershipBackend) -> None:
        """Remove the exact empty directory after a failed registration."""

        if self.opened is not None:
            opened = self.opened
            self.opened = None
            dispose_live_owned_path(opened, self.binding, backend)
            return
        dispose_owned_binding(self.binding, backend)


def _windows_api(name: str) -> object:
    """Load one Windows DLL only on the production Windows path."""

    if os.name != "nt":
        raise OwnershipCleanupError("Windows security APIs are unavailable")
    try:
        return ctypes.WinDLL(name, use_last_error=True)
    except (AttributeError, OSError) as error:
        raise OwnershipCleanupError("Windows security API is unavailable") from error


def _current_user_sid() -> str:
    """Return the current trusted session SID without publishing it."""

    kernel32 = _windows_api("kernel32")
    advapi32 = _windows_api("advapi32")
    open_process_token = advapi32.OpenProcessToken  # type: ignore[attr-defined]
    open_process_token.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    open_process_token.restype = ctypes.c_int
    get_current_process = kernel32.GetCurrentProcess  # type: ignore[attr-defined]
    get_current_process.argtypes = []
    get_current_process.restype = ctypes.c_void_p
    get_token_information = advapi32.GetTokenInformation  # type: ignore[attr-defined]
    get_token_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    get_token_information.restype = ctypes.c_int
    convert_sid = advapi32.ConvertSidToStringSidW  # type: ignore[attr-defined]
    convert_sid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    convert_sid.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle  # type: ignore[attr-defined]
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    local_free = kernel32.LocalFree  # type: ignore[attr-defined]
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    token = ctypes.c_void_p()
    if not open_process_token(get_current_process(), _TOKEN_QUERY, ctypes.byref(token)):
        raise OwnershipCleanupError("current session token cannot be opened")
    try:
        size = ctypes.c_uint32()
        # The first call is intentionally expected to fail with
        # ERROR_INSUFFICIENT_BUFFER; the returned size is authoritative.
        get_token_information(
            token,
            _TOKEN_USER_INFORMATION_CLASS,
            None,
            0,
            ctypes.byref(size),
        )
        if not size.value:
            raise OwnershipCleanupError("current session SID is unavailable")
        buffer = ctypes.create_string_buffer(size.value)
        if not get_token_information(
            token,
            _TOKEN_USER_INFORMATION_CLASS,
            buffer,
            size.value,
            ctypes.byref(size),
        ):
            raise OwnershipCleanupError("current session SID cannot be read")
        token_user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
        if not token_user.User.Sid:
            raise OwnershipCleanupError("current session SID is empty")
        sid_text = ctypes.c_wchar_p()
        if not convert_sid(token_user.User.Sid, ctypes.byref(sid_text)):
            raise OwnershipCleanupError("current session SID cannot be encoded")
        try:
            value = sid_text.value
            if value is None or not re.fullmatch(r"S-\d+(?:-\d+)+", value):
                raise OwnershipCleanupError("current session SID is malformed")
            return value
        finally:
            if sid_text:
                local_free(ctypes.cast(sid_text, ctypes.c_void_p))
    finally:
        if token.value and not close_handle(token):
            raise OwnershipCleanupError("current session token cannot be closed")


def _current_token_owner_sid() -> str:
    """Return the exact default owner Windows applies to files we create."""

    kernel32 = _windows_api("kernel32")
    advapi32 = _windows_api("advapi32")
    open_process_token = advapi32.OpenProcessToken  # type: ignore[attr-defined]
    open_process_token.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    open_process_token.restype = ctypes.c_int
    get_current_process = kernel32.GetCurrentProcess  # type: ignore[attr-defined]
    get_current_process.argtypes = []
    get_current_process.restype = ctypes.c_void_p
    get_token_information = advapi32.GetTokenInformation  # type: ignore[attr-defined]
    get_token_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    get_token_information.restype = ctypes.c_int
    convert_sid = advapi32.ConvertSidToStringSidW  # type: ignore[attr-defined]
    convert_sid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    convert_sid.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle  # type: ignore[attr-defined]
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    local_free = kernel32.LocalFree  # type: ignore[attr-defined]
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    token = ctypes.c_void_p()
    if not open_process_token(get_current_process(), _TOKEN_QUERY, ctypes.byref(token)):
        raise OwnershipCleanupError("current session token cannot be opened")
    try:
        size = ctypes.c_uint32()
        get_token_information(
            token,
            _TOKEN_OWNER_INFORMATION_CLASS,
            None,
            0,
            ctypes.byref(size),
        )
        if not size.value:
            raise OwnershipCleanupError("current session default owner is unavailable")
        buffer = ctypes.create_string_buffer(size.value)
        if not get_token_information(
            token,
            _TOKEN_OWNER_INFORMATION_CLASS,
            buffer,
            size.value,
            ctypes.byref(size),
        ):
            raise OwnershipCleanupError("current session default owner cannot be read")
        owner = ctypes.cast(buffer, ctypes.POINTER(_TokenOwner)).contents.Owner
        if not owner:
            raise OwnershipCleanupError("current session default owner is empty")
        text = ctypes.c_wchar_p()
        if not convert_sid(owner, ctypes.byref(text)):
            raise OwnershipCleanupError("current session default owner cannot be encoded")
        try:
            value = text.value
            if value is None or re.fullmatch(r"S-\d+(?:-\d+)+", value) is None:
                raise OwnershipCleanupError("current session default owner is malformed")
            return value
        finally:
            if text:
                local_free(ctypes.cast(text, ctypes.c_void_p))
    finally:
        if token.value and not close_handle(token):
            raise OwnershipCleanupError("current session token cannot be closed")


def current_user_sid() -> str:
    """Return the current trusted-session SID for narrow component ACL checks."""

    return _current_user_sid()


def private_input_trusted_owner_sids(
    user_sid: str | None = None,
    *,
    allow_administrators_if_token_owner: bool = False,
) -> frozenset[str]:
    """Return the deliberately small owner set for persisted private bytes.

    Private files are created by the current interactive session.  SYSTEM is
    retained for supported local service creation.  Administrators is allowed
    only when Windows reports it as this process token's default file owner,
    which is the documented elevated-token creation behavior.  This excludes
    Builtin Users, Everyone, Authenticated Users, arbitrary service SIDs, and
    every unrelated account even when a forged DACL looks restrictive.
    """

    current = user_sid or _current_user_sid()
    if re.fullmatch(r"S-\d+(?:-\d+)+", current) is None:
        raise OwnershipCleanupError("trusted private input owner SID is malformed")
    trusted = {current, _SYSTEM_SID}
    if (
        allow_administrators_if_token_owner
        and _current_token_owner_sid() == _ADMINISTRATORS_SID
    ):
        trusted.add(_ADMINISTRATORS_SID)
    return frozenset(trusted)


def validate_private_input_owner(
    owner_sid: str,
    *,
    user_sid: str | None = None,
    allow_administrators_if_token_owner: bool = False,
) -> None:
    """Reject a private input whose retained-handle owner is not trusted."""

    if (
        not isinstance(owner_sid, str)
        or re.fullmatch(r"S-\d+(?:-\d+)+", owner_sid) is None
        or owner_sid
        not in private_input_trusted_owner_sids(
            user_sid,
            allow_administrators_if_token_owner=allow_administrators_if_token_owner,
        )
    ):
        raise OwnershipCleanupError(
            "private input owner is outside the current-user/SYSTEM trust set"
        )


def _is_ntfs_volume(path: Path) -> bool:
    """Require a normal local NTFS volume for private converter staging."""

    if os.name != "nt":
        return False
    raw_path = os.fspath(path)
    root = path.anchor
    separator = chr(92)
    if (
        not root
        or root.startswith(separator * 2)
        or raw_path.startswith(separator * 2 + "?" + separator)
        or raw_path.startswith(separator * 2 + "." + separator)
        or re.fullmatch(r"[A-Za-z]:[\\/]", root) is None
    ):
        return False
    kernel32 = _windows_api("kernel32")
    # A mapped remote share may report NTFS through GetVolumeInformationW,
    # but DriveType=REMOTE proves it is not a fixed local private volume.
    get_drive_type = kernel32.GetDriveTypeW  # type: ignore[attr-defined]
    get_drive_type.argtypes = [ctypes.c_wchar_p]
    get_drive_type.restype = ctypes.c_uint32
    if int(get_drive_type(root)) != 3:  # DRIVE_FIXED
        return False
    get_volume_information = kernel32.GetVolumeInformationW  # type: ignore[attr-defined]
    get_volume_information.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_wchar_p,
        ctypes.c_uint32,
    ]
    get_volume_information.restype = ctypes.c_int
    filesystem = ctypes.create_unicode_buffer(64)
    serial = ctypes.c_uint32()
    maximum_component = ctypes.c_uint32()
    flags = ctypes.c_uint32()
    if not get_volume_information(
        root,
        None,
        0,
        ctypes.byref(serial),
        ctypes.byref(maximum_component),
        ctypes.byref(flags),
        filesystem,
        len(filesystem),
    ):
        return False
    return filesystem.value.casefold() == "ntfs"


def _private_staging_sddl(user_sid: str) -> str:
    """Build the protected DACL for one trusted local ODA staging root."""

    if not re.fullmatch(r"S-\d+(?:-\d+)+", user_sid):
        raise OwnershipCleanupError("trusted session SID is malformed")
    # Full control is deliberately limited to the current trusted session and
    # SYSTEM. Protected inheritance prevents an ambient users/everyone ACE
    # from silently reaching the converter-private children.
    return f"D:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;{user_sid})"


def _private_directory_sddl(user_sid: str) -> str:
    """Build the complete owner and protected-DACL policy for new directories."""

    if not re.fullmatch(r"S-\d+(?:-\d+)+", user_sid):
        raise OwnershipCleanupError("trusted session SID is malformed")
    # Setting the owner in the creation descriptor avoids a later SetSecurity
    # call and keeps a broadly writable parent from determining the child
    # owner. The DACL remains inherited by descendants only from this private
    # root, never from its ambient parent.
    return f"O:{user_sid}{_private_staging_sddl(user_sid)}"


def _private_directory_security_attributes(
    user_sid: str,
) -> tuple[_SecurityAttributes, ctypes.c_void_p]:
    """Allocate the descriptor that CreateDirectoryW consumes synchronously."""

    advapi32 = _windows_api("advapi32")
    convert_descriptor = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW  # type: ignore[attr-defined]
    convert_descriptor.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    convert_descriptor.restype = ctypes.c_int
    descriptor = ctypes.c_void_p()
    descriptor_size = ctypes.c_uint32()
    if not convert_descriptor(
        _private_directory_sddl(user_sid),
        1,
        ctypes.byref(descriptor),
        ctypes.byref(descriptor_size),
    ) or not descriptor.value:
        raise OwnershipCleanupError("private directory security descriptor cannot be created")
    attributes = _SecurityAttributes()
    attributes.nLength = ctypes.sizeof(_SecurityAttributes)
    attributes.lpSecurityDescriptor = descriptor
    attributes.bInheritHandle = 0
    return attributes, descriptor


def _free_private_directory_security_descriptor(descriptor: ctypes.c_void_p) -> None:
    """Release one LocalAlloc descriptor after CreateDirectoryW has returned."""

    if not descriptor.value:
        return
    kernel32 = _windows_api("kernel32")
    local_free = kernel32.LocalFree  # type: ignore[attr-defined]
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    local_free(descriptor)


def _dacl_sddl(path: Path) -> str:
    """Read a path's DACL as SDDL for exact restricted-principal validation."""

    advapi32 = _windows_api("advapi32")
    kernel32 = _windows_api("kernel32")
    get_file_security = advapi32.GetFileSecurityW  # type: ignore[attr-defined]
    get_file_security.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    get_file_security.restype = ctypes.c_int
    convert_descriptor = advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW  # type: ignore[attr-defined]
    convert_descriptor.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    convert_descriptor.restype = ctypes.c_int
    local_free = kernel32.LocalFree  # type: ignore[attr-defined]
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    size = ctypes.c_uint32()
    get_file_security(
        str(path),
        _DACL_SECURITY_INFORMATION,
        None,
        0,
        ctypes.byref(size),
    )
    if not size.value:
        raise OwnershipCleanupError("staging DACL cannot be read")
    descriptor = ctypes.create_string_buffer(size.value)
    if not get_file_security(
        str(path),
        _DACL_SECURITY_INFORMATION,
        descriptor,
        size.value,
        ctypes.byref(size),
    ):
        raise OwnershipCleanupError("staging DACL cannot be read")
    text = ctypes.c_wchar_p()
    text_size = ctypes.c_uint32()
    if not convert_descriptor(
        descriptor,
        1,
        _DACL_SECURITY_INFORMATION,
        ctypes.byref(text),
        ctypes.byref(text_size),
    ):
        raise OwnershipCleanupError("staging DACL cannot be encoded")
    try:
        value = text.value
        if value is None:
            raise OwnershipCleanupError("staging DACL is empty")
        return value
    finally:
        if text:
            local_free(ctypes.cast(text, ctypes.c_void_p))


def _dacl_sddl_for_handle(handle: int) -> str:
    """Read a DACL through a retained Windows handle, never by reopening path."""

    advapi32 = _windows_api("advapi32")
    kernel32 = _windows_api("kernel32")
    get_security_info = advapi32.GetSecurityInfo  # type: ignore[attr-defined]
    get_security_info.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_security_info.restype = ctypes.c_uint32
    convert_descriptor = advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW  # type: ignore[attr-defined]
    convert_descriptor.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    convert_descriptor.restype = ctypes.c_int
    local_free = kernel32.LocalFree  # type: ignore[attr-defined]
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    status = get_security_info(
        ctypes.c_void_p(handle),
        _SE_FILE_OBJECT,
        _DACL_SECURITY_INFORMATION,
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if status != 0 or not dacl.value or not descriptor.value:
        raise OwnershipCleanupError("private session file DACL cannot be read")
    try:
        text = ctypes.c_wchar_p()
        text_size = ctypes.c_uint32()
        if not convert_descriptor(
            descriptor,
            1,
            _DACL_SECURITY_INFORMATION,
            ctypes.byref(text),
            ctypes.byref(text_size),
        ):
            raise OwnershipCleanupError("private session file DACL cannot be encoded")
        try:
            value = text.value
            if value is None:
                raise OwnershipCleanupError("private session file DACL is empty")
            return value
        finally:
            if text:
                local_free(ctypes.cast(text, ctypes.c_void_p))
    finally:
        local_free(descriptor)


def _canonical_private_dacl_principal(descriptor: ctypes.c_void_p) -> str:
    """Read the current SID's canonical SDDL spelling from our own descriptor."""

    advapi32 = _windows_api("advapi32")
    kernel32 = _windows_api("kernel32")
    convert_descriptor = advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW  # type: ignore[attr-defined]
    convert_descriptor.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    convert_descriptor.restype = ctypes.c_int
    local_free = kernel32.LocalFree  # type: ignore[attr-defined]
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    text = ctypes.c_wchar_p()
    text_size = ctypes.c_uint32()
    if not convert_descriptor(
        descriptor,
        1,
        _DACL_SECURITY_INFORMATION,
        ctypes.byref(text),
        ctypes.byref(text_size),
    ):
        raise OwnershipCleanupError("private staging DACL cannot be canonicalized")
    try:
        value = text.value
        if value is None:
            raise OwnershipCleanupError("private staging DACL canonical form is empty")
        principals = [
            parts[5]
            for ace in re.findall(r"\(([^()]*)\)", value)
            if len(parts := ace.split(";")) == 6
            and parts[0] == "A"
            and parts[5] != "SY"
        ]
        if len(principals) != 1:
            raise OwnershipCleanupError(
                "private staging DACL canonical principal is unavailable"
            )
        return principals[0]
    finally:
        if text:
            local_free(ctypes.cast(text, ctypes.c_void_p))


def _apply_private_staging_dacl(path: Path, user_sid: str) -> None:
    """Apply then read back a protected current-user/SYSTEM-only DACL."""

    advapi32 = _windows_api("advapi32")
    kernel32 = _windows_api("kernel32")
    convert_descriptor = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW  # type: ignore[attr-defined]
    convert_descriptor.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    convert_descriptor.restype = ctypes.c_int
    set_file_security = advapi32.SetFileSecurityW  # type: ignore[attr-defined]
    set_file_security.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p]
    set_file_security.restype = ctypes.c_int
    local_free = kernel32.LocalFree  # type: ignore[attr-defined]
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    descriptor = ctypes.c_void_p()
    descriptor_size = ctypes.c_uint32()
    if not convert_descriptor(
        _private_staging_sddl(user_sid),
        1,
        ctypes.byref(descriptor),
        ctypes.byref(descriptor_size),
    ):
        raise OwnershipCleanupError("private staging DACL cannot be created")
    try:
        expected_user_principal = _canonical_private_dacl_principal(descriptor)
        if not set_file_security(str(path), _DACL_SECURITY_INFORMATION, descriptor):
            raise OwnershipCleanupError("private staging DACL cannot be applied")
    finally:
        if descriptor.value:
            local_free(descriptor)
    actual = _dacl_sddl(path)
    if not _private_staging_dacl_is_exact(actual, expected_user_principal):
        raise OwnershipCleanupError("private staging DACL cannot be verified")


def _apply_private_staging_dacl_to_handle(handle: int, user_sid: str) -> None:
    """Apply/read back the restrictive DACL through an exclusive file handle."""

    if not isinstance(handle, int) or handle <= 0:
        raise OwnershipCleanupError("private session file handle is unavailable")
    advapi32 = _windows_api("advapi32")
    kernel32 = _windows_api("kernel32")
    convert_descriptor = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW  # type: ignore[attr-defined]
    convert_descriptor.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    convert_descriptor.restype = ctypes.c_int
    get_dacl = advapi32.GetSecurityDescriptorDacl  # type: ignore[attr-defined]
    get_dacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_int),
    ]
    get_dacl.restype = ctypes.c_int
    set_security_info = advapi32.SetSecurityInfo  # type: ignore[attr-defined]
    set_security_info.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    set_security_info.restype = ctypes.c_uint32
    local_free = kernel32.LocalFree  # type: ignore[attr-defined]
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    descriptor_size = ctypes.c_uint32()
    if not convert_descriptor(
        _private_staging_sddl(user_sid),
        1,
        ctypes.byref(descriptor),
        ctypes.byref(descriptor_size),
    ):
        raise OwnershipCleanupError("private session file DACL cannot be created")
    try:
        expected_user_principal = _canonical_private_dacl_principal(descriptor)
        present = ctypes.c_int()
        dacl = ctypes.c_void_p()
        defaulted = ctypes.c_int()
        if (
            not get_dacl(
                descriptor,
                ctypes.byref(present),
                ctypes.byref(dacl),
                ctypes.byref(defaulted),
            )
            or not present.value
            or not dacl.value
        ):
            raise OwnershipCleanupError("private session file DACL cannot be extracted")
        if (
            set_security_info(
                ctypes.c_void_p(handle),
                _SE_FILE_OBJECT,
                # Passing only the raw ACL loses the ``D:P`` protection bit
                # encoded in the descriptor and lets broad parent ACEs be
                # inherited again.  The file is private until finalization.
                _DACL_SECURITY_INFORMATION
                | _PROTECTED_DACL_SECURITY_INFORMATION,
                None,
                None,
                dacl,
                None,
            )
            != 0
        ):
            raise OwnershipCleanupError("private session file DACL cannot be applied")
    finally:
        if descriptor.value:
            local_free(descriptor)
    actual = _dacl_sddl_for_handle(handle)
    if not _private_staging_dacl_is_exact(
        actual,
        expected_user_principal,
        require_inheritance=False,
    ):
        raise OwnershipCleanupError("private session file DACL cannot be verified")


def _expected_private_dacl_principal(user_sid: str) -> str:
    """Canonicalize the current user's SID through the private DACL shape."""

    advapi32 = _windows_api("advapi32")
    kernel32 = _windows_api("kernel32")
    convert_descriptor = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW  # type: ignore[attr-defined]
    convert_descriptor.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    convert_descriptor.restype = ctypes.c_int
    local_free = kernel32.LocalFree  # type: ignore[attr-defined]
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    descriptor_size = ctypes.c_uint32()
    if not convert_descriptor(
        _private_staging_sddl(user_sid),
        1,
        ctypes.byref(descriptor),
        ctypes.byref(descriptor_size),
    ):
        raise OwnershipCleanupError("private session file DACL cannot be created")
    try:
        return _canonical_private_dacl_principal(descriptor)
    finally:
        if descriptor.value:
            local_free(descriptor)


def _owner_sid_for_handle(handle: int) -> str:
    """Read a file owner through one already-retained Windows file handle."""

    if not isinstance(handle, int) or handle <= 0:
        raise OwnershipCleanupError("private session file handle is unavailable")
    advapi32 = _windows_api("advapi32")
    kernel32 = _windows_api("kernel32")
    get_security_info = advapi32.GetSecurityInfo  # type: ignore[attr-defined]
    get_security_info.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_security_info.restype = ctypes.c_uint32
    convert_sid = advapi32.ConvertSidToStringSidW  # type: ignore[attr-defined]
    convert_sid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    convert_sid.restype = ctypes.c_int
    is_valid_sid = advapi32.IsValidSid  # type: ignore[attr-defined]
    is_valid_sid.argtypes = [ctypes.c_void_p]
    is_valid_sid.restype = ctypes.c_int
    local_free = kernel32.LocalFree  # type: ignore[attr-defined]
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    owner = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    status = get_security_info(
        ctypes.c_void_p(handle),
        _SE_FILE_OBJECT,
        _OWNER_SECURITY_INFORMATION,
        ctypes.byref(owner),
        None,
        None,
        None,
        ctypes.byref(descriptor),
    )
    if status != 0 or not owner.value or not descriptor.value or not is_valid_sid(owner):
        raise OwnershipCleanupError("private input owner cannot be read")
    try:
        text = ctypes.c_wchar_p()
        if not convert_sid(owner, ctypes.byref(text)):
            raise OwnershipCleanupError("private input owner cannot be encoded")
        try:
            value = text.value
            if value is None or re.fullmatch(r"S-\d+(?:-\d+)+", value) is None:
                raise OwnershipCleanupError("private input owner is malformed")
            return value
        finally:
            if text:
                local_free(ctypes.cast(text, ctypes.c_void_p))
    finally:
        local_free(descriptor)


def _verify_private_staging_dacl_on_handle(
    handle: int,
    user_sid: str,
    *,
    require_protected: bool = True,
) -> None:
    """Read DACL and owner twice through one handle, rejecting owner drift."""

    if not isinstance(handle, int) or handle <= 0:
        raise OwnershipCleanupError("private session file handle is unavailable")
    owner_before = _owner_sid_for_handle(handle)
    validate_private_input_owner(
        owner_before,
        user_sid=user_sid,
        allow_administrators_if_token_owner=True,
    )
    actual = _dacl_sddl_for_handle(handle)
    if not _private_staging_dacl_is_exact(
        actual,
        _expected_private_dacl_principal(user_sid),
        require_inheritance=False,
        require_protected=require_protected,
    ):
        raise OwnershipCleanupError("private session file DACL cannot be verified")
    owner_after = _owner_sid_for_handle(handle)
    validate_private_input_owner(
        owner_after,
        user_sid=user_sid,
        allow_administrators_if_token_owner=True,
    )
    if owner_after != owner_before:
        raise OwnershipCleanupError("private input owner changed during validation")


def _private_staging_dacl_is_exact(
    actual: str,
    expected_user_principal: str,
    *,
    require_inheritance: bool = True,
    require_protected: bool = True,
) -> bool:
    """Reject inherited, broad, or weakened ACEs after the DACL readback."""

    if require_protected and not actual.startswith("D:P"):
        return False
    if not require_protected and not actual.startswith("D:"):
        return False
    aces = re.findall(r"\(([^()]*)\)", actual)
    saw_system = False
    saw_user = False
    if len(aces) != 2:
        return False
    for ace in aces:
        parts = ace.split(";")
        if len(parts) != 6:
            return False
        ace_type, flags, rights, object_guid, inherit_guid, principal = parts
        allowed_flags = (
            {"OICI"} if require_inheritance else {"", "ID", "OICI"}
        )
        if (
            ace_type != "A"
            or flags not in allowed_flags
            or rights != "FA"
            or object_guid
            or inherit_guid
        ):
            return False
        if principal == "SY":
            saw_system = True
        # The expected principal is read from the just-created descriptor, so
        # aliases such as ``LA`` are accepted only when they canonicalize the
        # current trusted session SID, never as a generic administrator ACE.
        elif principal == expected_user_principal:
            saw_user = True
        else:
            return False
    return saw_system and saw_user


def validate_private_staging_ancestry(
    path: Path,
    backend: FileOwnershipBackend,
) -> None:
    """Fail closed unless a retained workspace ancestry is on local NTFS."""

    if isinstance(backend, WindowsFileOwnershipBackend):
        if not _is_ntfs_volume(path):
            raise OwnershipCleanupError("private staging requires local NTFS")
        return
    probe = getattr(backend, "validate_private_staging_ancestry", None)
    if callable(probe):
        probe(path)
        return
    raise OwnershipCleanupError("private staging capability is unavailable")


def secure_private_staging_directory(
    path: Path,
    backend: FileOwnershipBackend,
) -> None:
    """Apply and verify the restrictive DACL on one random staging root."""

    if isinstance(backend, WindowsFileOwnershipBackend):
        if not _is_ntfs_volume(path):
            raise OwnershipCleanupError("private staging requires local NTFS")
        _apply_private_staging_dacl(path, _current_user_sid())
        return
    probe = getattr(backend, "secure_private_staging_directory", None)
    if callable(probe):
        probe(path)
        return
    raise OwnershipCleanupError("private staging DACL capability is unavailable")


def create_private_directory(
    path: Path,
    backend: FileOwnershipBackend,
) -> PrivateDirectoryCreation:
    """Create one empty directory with privacy attached before it exists.

    Production Windows calls ``CreateDirectoryW`` exactly once with a
    protected current-user/SYSTEM-only security descriptor. There is no
    ``mkdir``/``SetFileSecurity`` interval. Any non-Windows path requires an
    explicitly injected private-directory creator; production never falls
    back to a generic directory create on Windows.
    """

    if (
        not path.is_absolute()
        or not path.name
        or path.name in {".", ".."}
        or "\x00" in os.fspath(path)
    ):
        raise OwnershipCleanupError("private directory path is invalid")

    opened: OwnedPath | None = None
    binding: OwnedPathBinding | None = None
    try:
        if isinstance(backend, WindowsFileOwnershipBackend):
            user_sid = _current_user_sid()
            attributes, descriptor = _private_directory_security_attributes(user_sid)
            try:
                if not backend.api.create_directory(str(path), attributes):
                    error_number = backend.api.last_error()
                    if error_number in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}:
                        raise DestinationExistsError("private directory already exists")
                    raise OwnershipCleanupError("private directory cannot be created")
            finally:
                _free_private_directory_security_descriptor(descriptor)
        else:
            creator = getattr(backend, "create_private_directory", None)
            if not callable(creator):
                raise OwnershipCleanupError(
                    "private directory creation capability is unavailable"
                )
            try:
                creator(path)
            except FileExistsError as error:
                raise DestinationExistsError("private directory already exists") from error

        # Open no-follow before a caller can create a child. The deletion
        # capability lets failure cleanup target this exact retained identity.
        opened = backend.open_existing_directory(path, for_delete=True)
        binding = opened.capture_binding()
        final_path = opened.final_path()
        if (
            not binding.is_directory
            or _lexical_path_key(final_path) != _lexical_path_key(path)
        ):
            raise OwnershipLostError("private directory did not retain its creation identity")

        if isinstance(backend, WindowsFileOwnershipBackend):
            owner = verify_private_staging_file(opened, backend)
            # The creation descriptor explicitly requested the interactive
            # user's SID as owner. Accepting a substituted owner would turn a
            # successfully protected DACL into an ambiguous private binding.
            if owner != user_sid:
                raise OwnershipCleanupError("private directory owner cannot be verified")
        else:
            secure_private_staging_directory(final_path, backend)
        return PrivateDirectoryCreation(
            path=final_path,
            binding=binding,
            opened=opened,
        )
    except BaseException as error:
        cleanup_error: BaseException | None = None
        if opened is not None:
            try:
                if binding is not None:
                    dispose_live_owned_path(opened, binding, backend)
                    opened = None
                else:
                    opened.close()
                    opened = None
            except (OSError, OwnershipError) as cleanup:
                cleanup_error = cleanup
        if cleanup_error is not None:
            raise OwnershipCleanupError(
                "private directory cleanup after failed validation did not complete"
            ) from error
        raise


def create_private_workspace_directory(
    parent: Path,
    prefix: str,
    backend: FileOwnershipBackend,
) -> PrivateDirectoryCreation:
    """Create one cryptographically named private workspace below ``parent``.

    Retrying is permitted only for the bounded set of collision outcomes from
    the atomic directory create. Every other error remains terminal.
    """

    if (
        not isinstance(prefix, str)
        or not prefix
        or "\x00" in prefix
        or "/" in prefix
        or "\\" in prefix
    ):
        raise OwnershipCleanupError("private workspace prefix is invalid")
    for _attempt in range(_PRIVATE_DIRECTORY_CREATE_ATTEMPTS):
        candidate = parent / f"{prefix}{secrets.token_hex(16)}"
        try:
            return create_private_directory(candidate, backend)
        except DestinationExistsError:
            continue
    raise OwnershipCleanupError("private workspace name collision limit reached")


def secure_private_staging_file(
    opened: OwnedPath,
    backend: FileOwnershipBackend,
) -> None:
    """Apply and verify the current-user/SYSTEM-only DACL on a held file.

    Callers retain the file's exclusive creation handle while invoking this
    helper. Production uses that same handle for DACL application and
    readback, so no pathname reopen or inherited ACL window exists.
    """

    if isinstance(backend, WindowsFileOwnershipBackend):
        path = opened.final_path()
        if not _is_ntfs_volume(path):
            raise OwnershipCleanupError("private session file requires local NTFS")
        handle = getattr(opened, "handle", None)
        _apply_private_staging_dacl_to_handle(handle, _current_user_sid())
        _verify_private_staging_dacl_on_handle(handle, _current_user_sid())
        return
    probe = getattr(backend, "secure_private_staging_file", None)
    if callable(probe):
        probe(opened.final_path())
        return
    raise OwnershipCleanupError("private session file DACL capability is unavailable")


def verify_private_staging_file(
    opened: OwnedPath,
    backend: FileOwnershipBackend,
    *,
    require_protected: bool = True,
) -> str | None:
    """Require a held file to retain a trusted owner and private-only DACL.

    This is intentionally verification-only.  Native machine-readable
    artifacts use it after their no-replace rename and before accepting a
    persisted input, so a broad parent DACL is never restored or silently
    accepted merely because creation initially used a private temporary.  A
    private-workspace child can retain safe inherited ACEs, but only callers
    explicitly requesting ``require_protected=False`` may use that form.
    """

    if isinstance(backend, WindowsFileOwnershipBackend):
        path = opened.final_path()
        if not _is_ntfs_volume(path):
            raise OwnershipCleanupError("private session file requires local NTFS")
        handle = getattr(opened, "handle", None)
        _verify_private_staging_dacl_on_handle(
            handle,
            _current_user_sid(),
            require_protected=require_protected,
        )
        return _owner_sid_for_handle(handle)
    probe = getattr(backend, "verify_private_staging_file", None)
    if callable(probe):
        probe(opened.final_path())
        return None
    raise OwnershipCleanupError("private session file DACL capability is unavailable")


def capture_public_output_acl_policy(
    opened: OwnedPath,
    backend: FileOwnershipBackend,
) -> PublicOutputAclPolicy:
    """Capture the new file's inherited public DACL through its open handle.

    The capture happens before private staging replaces the DACL.  No public
    pathname is reopened, so even a broadly readable output parent cannot
    expose staged bytes while the exclusive handle is retained.
    """

    if isinstance(backend, WindowsFileOwnershipBackend):
        handle = getattr(opened, "handle", None)
        if not isinstance(handle, int) or handle <= 0:
            raise OwnershipCleanupError("public output file handle is unavailable")
        value = _dacl_sddl_for_handle(handle)
        if not value.startswith("D:") or "\x00" in value:
            raise OwnershipCleanupError("public output DACL policy is invalid")
        return PublicOutputAclPolicy(dacl_sddl=value)
    capture = getattr(backend, "capture_public_output_acl_policy", None)
    if callable(capture):
        policy = capture(opened)
        if isinstance(policy, PublicOutputAclPolicy):
            return policy
    raise OwnershipCleanupError("public output DACL policy capability is unavailable")


def _restore_public_output_dacl_to_handle(handle: int, policy: PublicOutputAclPolicy) -> None:
    """Restore a captured public DACL through one still-exclusive handle."""

    if not isinstance(handle, int) or handle <= 0:
        raise OwnershipCleanupError("public output file handle is unavailable")
    value = policy.dacl_sddl
    if not isinstance(value, str) or not value.startswith("D:") or "\x00" in value:
        raise OwnershipCleanupError("public output DACL policy is invalid")
    advapi32 = _windows_api("advapi32")
    kernel32 = _windows_api("kernel32")
    convert_descriptor = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW  # type: ignore[attr-defined]
    convert_descriptor.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    convert_descriptor.restype = ctypes.c_int
    get_dacl = advapi32.GetSecurityDescriptorDacl  # type: ignore[attr-defined]
    get_dacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_int),
    ]
    get_dacl.restype = ctypes.c_int
    set_security_info = advapi32.SetSecurityInfo  # type: ignore[attr-defined]
    set_security_info.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    set_security_info.restype = ctypes.c_uint32
    local_free = kernel32.LocalFree  # type: ignore[attr-defined]
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    descriptor = ctypes.c_void_p()
    descriptor_size = ctypes.c_uint32()
    if not convert_descriptor(
        value,
        1,
        ctypes.byref(descriptor),
        ctypes.byref(descriptor_size),
    ):
        raise OwnershipCleanupError("public output DACL cannot be created")
    try:
        present = ctypes.c_int()
        dacl = ctypes.c_void_p()
        defaulted = ctypes.c_int()
        if (
            not get_dacl(
                descriptor,
                ctypes.byref(present),
                ctypes.byref(dacl),
                ctypes.byref(defaulted),
            )
            or not present.value
            or not dacl.value
        ):
            raise OwnershipCleanupError("public output DACL cannot be extracted")
        protection = (
            _PROTECTED_DACL_SECURITY_INFORMATION
            if value.startswith("D:P")
            else _UNPROTECTED_DACL_SECURITY_INFORMATION
        )
        if (
            set_security_info(
                ctypes.c_void_p(handle),
                _SE_FILE_OBJECT,
                _DACL_SECURITY_INFORMATION | protection,
                None,
                None,
                dacl,
                None,
            )
            != 0
        ):
            raise OwnershipCleanupError("public output DACL cannot be applied")
    finally:
        if descriptor.value:
            local_free(descriptor)
    if not _public_output_dacl_matches(value, _dacl_sddl_for_handle(handle)):
        raise OwnershipCleanupError("public output DACL cannot be verified")


def _public_output_dacl_matches(expected: str, actual: str) -> bool:
    """Compare a captured DACL while tolerating Windows' auto-inherit marker.

    ``SetSecurityInfo(...UNPROTECTED_DACL...)`` may add ``AI`` to an otherwise
    identical inherited DACL.  The ACE list and protected-state bit remain
    authoritative; accepting only this marker avoids rejecting normal public
    output after a safe policy restoration.
    """

    def normalized(value: str) -> str | None:
        match = re.fullmatch(r"D:([A-Z]*)(\(.*\))?", value)
        if match is None:
            return None
        flags = match.group(1).replace("AI", "")
        aces = match.group(2) or ""
        return "D:" + flags + aces

    expected_normalized = normalized(expected)
    actual_normalized = normalized(actual)
    return (
        expected_normalized is not None
        and expected_normalized == actual_normalized
    )


def apply_public_output_acl_policy(
    opened: OwnedPath,
    policy: PublicOutputAclPolicy,
    backend: FileOwnershipBackend,
) -> None:
    """Restore and read back the intended final DACL while the handle is held."""

    if isinstance(backend, WindowsFileOwnershipBackend):
        handle = getattr(opened, "handle", None)
        _restore_public_output_dacl_to_handle(handle, policy)
        return
    apply = getattr(backend, "apply_public_output_acl_policy", None)
    if callable(apply):
        apply(opened, policy)
        return
    raise OwnershipCleanupError("public output DACL policy capability is unavailable")


def private_staging_capability() -> PrivateStagingCapability:
    """Probe only Windows/NTFS/DACL readiness without exposing local details."""

    if os.name != "nt":
        return PrivateStagingCapability(windows=False, ntfs=False, dacl=False)
    chain: LexicalDirectoryChainLease | None = None
    creation: PrivateDirectoryCreation | None = None
    try:
        backend = WindowsFileOwnershipBackend()
        chain = acquire_lexical_directory_chain(
            Path(tempfile.gettempdir()),
            backend,
        )
        if not _is_ntfs_volume(chain.path):
            return PrivateStagingCapability(windows=True, ntfs=False, dacl=False)
        creation = create_private_workspace_directory(
            chain.path,
            "liang-pingfa-staging-",
            backend,
        )
        creation.dispose(backend)
        creation = None
        return PrivateStagingCapability(windows=True, ntfs=True, dacl=True)
    except (OSError, OwnershipError, OwnershipCleanupError):
        return PrivateStagingCapability(windows=True, ntfs=False, dacl=False)
    finally:
        if creation is not None:
            try:
                creation.dispose(backend)
            except (OSError, OwnershipError):
                pass
        if chain is not None:
            try:
                chain.close()
            except (OSError, OwnershipError):
                pass


def platform_backend(*, require_windows: bool) -> FileOwnershipBackend:
    """Select Windows handles; test doubles live outside the installed package."""

    if os.name == "nt":
        return WindowsFileOwnershipBackend()
    del require_windows
    raise OwnershipCleanupError("Windows handle semantics are required")


def bind_existing_path(
    path: Path,
    backend: FileOwnershipBackend,
    *,
    is_directory: bool = False,
) -> OwnedPathBinding:
    """Return an exact binding for the currently opened regular file or directory."""

    opened = (
        backend.open_existing_directory(path, for_delete=False)
        if is_directory
        else backend.open_existing_file(path, for_delete=False)
    )
    try:
        binding = opened.capture_binding()
        if not backend.path_matches_binding(path, binding):
            raise OwnershipLostError("path changed while binding")
        return binding
    finally:
        opened.close()


def binding_matches_path(
    binding: OwnedPathBinding,
    backend: FileOwnershipBackend,
) -> bool:
    """Return whether a live path still resolves to its recorded exact binding."""

    return backend.path_matches_binding(binding.path, binding)


def dispose_owned_binding(
    binding: OwnedPathBinding,
    backend: FileOwnershipBackend,
) -> None:
    """Delete only the currently opened identity that matches ``binding``.

    The pathname is opened with DELETE access and sharing that denies a
    replacement before the identity/content comparison. Deletion then targets
    that same handle, never a later pathname lookup.
    """

    try:
        opened = (
            backend.open_existing_directory(binding.path, for_delete=True)
            if binding.is_directory
            else backend.open_existing_file(binding.path, for_delete=True)
        )
    except (OSError, OwnershipError) as error:
        raise OwnershipLostError("owned path cannot be opened") from error
    try:
        current = opened.capture_binding()
        if not current.same_identity_and_content(binding):
            raise OwnershipLostError("owned path identity differs")
        opened.request_delete()
    finally:
        try:
            opened.close()
        except OwnershipError:
            raise
        except OSError as error:
            raise OwnershipCleanupError("owned handle close failed") from error
    if backend.path_exists(binding.path):
        # A mock backend can model a replacement after the protected handle
        # was opened. Windows sharing normally prevents it, but never treat a
        # surviving directory entry as a successful cleanup.
        raise OwnershipLostError("owned path survived deletion")


def dispose_live_owned_path(
    opened: OwnedPath,
    binding: OwnedPathBinding,
    backend: FileOwnershipBackend,
) -> None:
    """Dispose a still-open owned object after proving its handle binding.

    An exclusive private publication handle intentionally denies every
    pathname reopen.  Its own final path is therefore the authority here;
    reopening via ``path_matches_binding`` would both fail on Windows and
    defeat the private-stage no-reopen rule.
    """

    try:
        current = opened.capture_binding()
        expected_path = os.path.normcase(
            os.path.normpath(os.fspath(binding.path))
        )
        handle_paths = (
            os.path.normcase(os.path.normpath(os.fspath(opened.final_path()))),
            os.path.normcase(os.path.normpath(os.fspath(opened.path))),
        )
        if (
            not current.same_identity_and_content(binding)
            or expected_path not in handle_paths
        ):
            raise OwnershipLostError("open owned path lost its name")
        opened.request_delete()
    finally:
        opened.close()
    if backend.path_exists(binding.path):
        raise OwnershipLostError("open owned path survived deletion")


def dispose_retained_owned_path(
    opened: OwnedPath,
    binding: OwnedPathBinding,
) -> None:
    """Delete the exact retained object without looking up its name again.

    This primitive is intentionally narrower than :func:`dispose_live_owned_path`.
    It is used only for a still-open temporary or just-published object whose
    creator must roll it back after the parent pathname may have changed.  The
    handle is the authority: after proving that the open object still has the
    recorded identity and content, ``request_delete`` can only target that
    object, never a pathname replacement.
    """

    try:
        current = opened.capture_binding()
        if not current.same_identity_and_content(binding):
            raise OwnershipLostError("retained owned path identity differs")
        opened.request_delete()
    finally:
        opened.close()
