"""Handle-bound, same-volume, no-replace DWG publication primitives."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

from .errors import ErrorCode, PipelineError
from .ownership import (
    DestinationExistsError,
    FileOwnershipBackend,
    OwnedPath,
    OwnedPathBinding,
    OwnershipCleanupError,
    OwnershipError,
    OwnershipLostError,
    LexicalDirectoryChainLease,
    acquire_lexical_directory_chain,
    dispose_owned_binding,
    dispose_retained_owned_path,
    lexical_absolute_path,
    platform_backend,
)
from .temporary import recover_publication_temporary


_Result = TypeVar("_Result")


def _exists_or_link(path: Path) -> bool:
    return os.path.lexists(path)


def require_new_dwg_output(source: Path, destination: Path) -> Path:
    """Validate the final output path before any mutation work starts."""

    if source.suffix.casefold() != ".dwg" or destination.suffix.casefold() != ".dwg":
        raise PipelineError(ErrorCode.INVALID_ARGUMENT, "DWG paths are required")
    targets = acquire_new_output_target_leases((destination,))
    try:
        try:
            source_lexical = lexical_absolute_path(source)
        except (OSError, OwnershipError) as error:
            raise PipelineError(ErrorCode.INVALID_ARGUMENT, "invalid output path") from error
        destination_bound = targets.targets[0].destination
        if _path_key(source_lexical) == _path_key(destination_bound):
            raise PipelineError(ErrorCode.OUTPUT_EXISTS, "output must be a new distinct path")
        return destination_bound
    finally:
        targets.close()


def acquire_new_dwg_output_target(
    source: Path,
    destination: Path,
    *,
    backend: FileOwnershipBackend | None = None,
) -> OutputTargetLeaseSet:
    """Bind a new DWG destination and its parent for the whole apply run."""

    if (
        source.suffix.casefold() != ".dwg"
        or destination.suffix.casefold() != ".dwg"
    ):
        raise PipelineError(ErrorCode.INVALID_ARGUMENT, "DWG paths are required")
    targets = acquire_new_output_target_leases((destination,), backend=backend)
    try:
        try:
            source_lexical = lexical_absolute_path(source)
        except (OSError, OwnershipError) as error:
            raise PipelineError(ErrorCode.INVALID_ARGUMENT, "invalid output path") from error
        if _path_key(source_lexical) == _path_key(targets.targets[0].destination):
            raise PipelineError(ErrorCode.OUTPUT_EXISTS, "output must be distinct")
        return targets
    except BaseException:
        targets.close()
        raise


def _publication_backend(
    backend: FileOwnershipBackend | None,
) -> FileOwnershipBackend:
    """Select real Windows handles unless a synthetic backend is injected."""

    if backend is not None:
        return backend
    try:
        return platform_backend(require_windows=True)
    except OwnershipCleanupError as error:
        raise PipelineError(
            ErrorCode.WINDOWS_PLATFORM_REQUIRED,
            "Windows handle-safe publication is required",
        ) from error


def _path_key(path: Path) -> str:
    """Return a case-normalized comparison key without resolving a leaf."""

    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _lexical_path_key(path: Path) -> str:
    """Compare pre-lease spellings without turning them into canonical paths."""

    return os.path.normcase(os.fspath(path))


def _require_safe_child_name(destination: Path) -> str:
    """Reject names that could escape or alias a bound output parent."""

    name = destination.name
    forbidden_characters = set('<>:"|?*')
    reserved_names = {
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
    if (
        not name
        or name in {".", ".."}
        or Path(name).name != name
        or any(separator in name for separator in ("/", "\\"))
        or any(character in forbidden_characters for character in name)
        or "\x00" in name
        or name != name.rstrip(" .")
        or name.split(".", 1)[0].casefold() in reserved_names
    ):
        raise PipelineError(ErrorCode.INVALID_ARGUMENT, "output file name is unsafe")
    return name


def _lexical_output_parent(parent: Path) -> Path:
    """Return the raw parent spelling without opening or resolving a link.

    The retained-chain constructor, not this helper, validates that this is a
    normal absolute path.  Keeping the raw lexical spelling here ensures a
    relative or ``..`` escape cannot be normalized into an apparently safe
    parent before every component is opened no-follow.
    """

    try:
        return lexical_absolute_path(parent)
    except (OSError, OwnershipError) as error:
        raise PipelineError(ErrorCode.INVALID_ARGUMENT, "output parent is invalid") from error


@dataclass
class OutputParentLease:
    """A retained root-to-parent no-follow lease for a public output path."""

    chain: LexicalDirectoryChainLease
    _closed: bool = False

    @property
    def lexical_path(self) -> Path:
        """Return the user-intent spelling validated by the retained chain."""

        return self.chain.lexical_path

    @property
    def path(self) -> Path:
        """Return the final parent spelling derived from its held handle."""

        return self.chain.path

    @property
    def owned(self) -> OwnedPath:
        """Expose the final held directory handle for existing adapters."""

        return self.chain.owned

    @property
    def binding(self) -> OwnedPathBinding:
        """Return the final parent identity captured by the chain."""

        return self.chain.binding

    @property
    def backend(self) -> FileOwnershipBackend:
        """Return the backend that owns every retained component."""

        return self.chain.backend

    def require_binding(self) -> None:
        """Require every lexical ancestor and final parent to retain bindings."""

        try:
            self.chain.require_binding()
        except (OSError, OwnershipError) as error:
            raise PipelineError(
                ErrorCode.ATOMIC_PUBLISH_FAILED,
                "output parent binding is unavailable",
            ) from error

    def close(self) -> None:
        """Release the directory lease only after its transaction is complete."""

        if self._closed:
            return
        try:
            self.chain.close()
        except (OSError, OwnershipError) as error:
            raise PipelineError(
                ErrorCode.ATOMIC_PUBLISH_FAILED,
                "output parent lease cannot be released",
            ) from error
        self._closed = True


@dataclass(frozen=True)
class OutputTargetLease:
    """A safe child name bound to a retained output-parent identity."""

    destination: Path
    parent: OutputParentLease
    backend: FileOwnershipBackend

    def require_parent_binding(self) -> None:
        """Recheck the retained parent before a path-based Win32 operation."""

        self.parent.require_binding()

    def require_new_destination(self) -> None:
        """Require the bound parent and a still-absent final child."""

        self.require_parent_binding()
        if self.backend.path_exists(self.destination):
            raise PipelineError(ErrorCode.OUTPUT_EXISTS, "output already exists")

    def require_existing_destination(self) -> None:
        """Require the bound parent and a current child for verification input."""

        self.require_parent_binding()
        if not self.backend.path_exists(self.destination):
            raise PipelineError(ErrorCode.INVALID_ARGUMENT, "output is unavailable")


@dataclass
class OutputTargetLeaseSet:
    """One or more output targets sharing retained parent-directory leases."""

    targets: tuple[OutputTargetLease, ...]
    _parents: tuple[OutputParentLease, ...]
    _closed: bool = False

    def close(self) -> None:
        """Release every parent lease, reporting the first safe failure."""

        if self._closed:
            return
        failure: PipelineError | None = None
        for parent in reversed(self._parents):
            try:
                parent.close()
            except PipelineError as error:
                if failure is None:
                    failure = error
        if failure is not None:
            raise failure
        self._closed = True


def _acquire_output_parent_lease(
    parent: Path,
    backend: FileOwnershipBackend,
) -> OutputParentLease:
    """Retain every lexical ancestor before any public output operation."""

    lexical = _lexical_output_parent(parent)
    try:
        return OutputParentLease(
            acquire_lexical_directory_chain(lexical, backend)
        )
    except OwnershipLostError as error:
        # No pathname is resolved here. ``path_exists`` is only a bounded
        # lexical presence check used to retain the pre-existing public error
        # distinction after the no-follow chain open has already failed.
        code = (
            ErrorCode.OUTPUT_PARENT_MISSING
            if not backend.path_exists(lexical)
            else ErrorCode.INVALID_ARGUMENT
        )
        raise PipelineError(
            code,
            "output parent is missing or a reparse point",
        ) from error
    except (OSError, OwnershipError) as error:
        raise PipelineError(
            ErrorCode.ATOMIC_PUBLISH_FAILED,
            "output parent cannot be leased",
        ) from error


def _validated_output_target(destination: Path) -> tuple[Path, str]:
    """Return an unresolved lexical parent and validated direct child name."""

    name = _require_safe_child_name(destination)
    return _lexical_output_parent(destination.parent), name


def validate_new_output_targets(destinations: Sequence[Path]) -> tuple[Path, ...]:
    """Validate through short-lived no-follow parent leases."""

    targets = acquire_new_output_target_leases(destinations)
    try:
        return tuple(target.destination for target in targets.targets)
    finally:
        targets.close()


def _acquire_output_target_leases(
    destinations: Sequence[Path],
    *,
    backend: FileOwnershipBackend | None = None,
    require_new: bool,
    existing_parents: Sequence[OutputParentLease] = (),
) -> OutputTargetLeaseSet:
    """Bind all output parents before creating any temporary or final file."""

    if not destinations:
        raise PipelineError(ErrorCode.INVALID_ARGUMENT, "no output destinations")
    selected_backend = _publication_backend(backend)
    prepared = [_validated_output_target(destination) for destination in destinations]
    target_keys: set[str] = set()
    grouped_parents: dict[str, Path] = {}
    prepared_targets: list[tuple[Path, str, str]] = []
    for parent, name in prepared:
        destination = parent / name
        target_key = _lexical_path_key(destination)
        if target_key in target_keys:
            raise PipelineError(ErrorCode.INVALID_ARGUMENT, "output destinations alias")
        target_keys.add(target_key)
        parent_key = _lexical_path_key(parent)
        grouped_parents.setdefault(parent_key, parent)
        prepared_targets.append((parent, name, parent_key))

    parents: dict[str, OutputParentLease] = {}
    for existing_parent in existing_parents:
        parents[_path_key(existing_parent.path)] = existing_parent
        parents[_path_key(existing_parent.lexical_path)] = existing_parent
        parents[_lexical_path_key(existing_parent.lexical_path)] = existing_parent
    acquired_parents: dict[str, OutputParentLease] = {}
    try:
        for parent in existing_parents:
            parent.require_binding()
        for key, parent in grouped_parents.items():
            if key not in parents:
                acquired = _acquire_output_parent_lease(parent, selected_backend)
                parents[key] = acquired
                parents[_path_key(acquired.path)] = acquired
                acquired_parents[key] = acquired
        targets = tuple(
            OutputTargetLease(
                destination=parents[parent_key].path / name,
                parent=parents[parent_key],
                backend=selected_backend,
            )
            for _parent, name, parent_key in prepared_targets
        )
        final_target_keys: set[str] = set()
        for target in targets:
            final_target_key = _path_key(target.destination)
            if final_target_key in final_target_keys:
                raise PipelineError(
                    ErrorCode.INVALID_ARGUMENT,
                    "output destinations alias",
                )
            final_target_keys.add(final_target_key)
            if require_new:
                target.require_new_destination()
            else:
                target.require_existing_destination()
        return OutputTargetLeaseSet(
            targets=targets,
            _parents=tuple(acquired_parents.values()),
        )
    except BaseException:
        for parent in reversed(tuple(acquired_parents.values())):
            try:
                parent.close()
            except PipelineError:
                pass
        raise


def acquire_new_output_target_leases(
    destinations: Sequence[Path],
    *,
    backend: FileOwnershipBackend | None = None,
    existing_parents: Sequence[OutputParentLease] = (),
) -> OutputTargetLeaseSet:
    """Acquire no-delete parent leases for new public outputs."""

    return _acquire_output_target_leases(
        destinations,
        backend=backend,
        require_new=True,
        existing_parents=existing_parents,
    )


def acquire_existing_output_target_leases(
    destinations: Sequence[Path],
    *,
    backend: FileOwnershipBackend | None = None,
) -> OutputTargetLeaseSet:
    """Acquire no-delete parent leases for existing verification inputs."""

    return _acquire_output_target_leases(
        destinations,
        backend=backend,
        require_new=False,
    )


@dataclass
class PublicationTemporary:
    """A copied, fsynced temporary whose handle remains open through commit."""

    owned: OwnedPath
    binding: OwnedPathBinding


@dataclass
class ArtifactTemporary:
    """A retained, fsynced artifact temporary that has not been published."""

    target: OutputTargetLease
    owned: OwnedPath
    binding: OwnedPathBinding


@dataclass
class PublishedArtifact:
    """A no-replace public artifact still backed by its retained handle."""

    path: Path
    owned: OwnedPath
    binding: OwnedPathBinding
    backend: FileOwnershipBackend


def _artifact_temporary_path(parent: OutputParentLease) -> Path:
    """Allocate a private sibling name beneath an already-bound parent."""

    return parent.path / f".liang-pingfa-artifact-{uuid4().hex}.tmp"


def _discard_retained_artifact(
    opened: OwnedPath,
    binding: OwnedPathBinding | None,
    original_failure: BaseException,
) -> None:
    """Delete only the exact owned temporary/final handle after a failure."""

    try:
        try:
            current = binding or opened.capture_binding()
        except (OSError, OwnershipError):
            # The handle was created by this transaction. If a late I/O
            # failure prevents a fresh binding, requesting deletion through
            # that same open handle is still safer than rediscovering its
            # pathname and cannot target another writer's replacement.
            try:
                opened.request_delete()
            finally:
                opened.close()
            return
        dispose_retained_owned_path(opened, current)
    except (OSError, OwnershipError) as error:
        raise PipelineError(
            ErrorCode.PUBLICATION_CLEANUP_FAILURE,
            "owned artifact cleanup failed",
        ) from original_failure


def _stage_new_artifact(
    target: OutputTargetLease,
    payload: bytes,
) -> ArtifactTemporary:
    """Write one artifact only to a retained temporary under its bound parent."""

    if not isinstance(payload, bytes):
        raise PipelineError(ErrorCode.INVALID_ARGUMENT, "artifact payload is invalid")
    opened: OwnedPath | None = None
    binding: OwnedPathBinding | None = None
    try:
        # This is deliberately immediately before CreateFileW.  Holding the
        # directory's no-delete lease plus this identity recheck closes the
        # parent-junction window for the pathname-based create operation.
        target.require_new_destination()
        temporary = _artifact_temporary_path(target.parent)
        if temporary == target.destination:
            raise PipelineError(ErrorCode.INVALID_ARGUMENT, "artifact name is reserved")
        opened = target.backend.create_new_file(temporary)
        opened.write_bytes(payload)
        binding = opened.capture_binding()
        if (
            binding.is_directory
            or binding.byte_size != len(payload)
            or binding.sha256 != sha256(payload).hexdigest()
            or not target.backend.path_matches_binding(temporary, binding)
        ):
            raise OwnershipLostError("artifact temporary binding differs")
        target.require_new_destination()
        return ArtifactTemporary(target=target, owned=opened, binding=binding)
    except PipelineError as error:
        if opened is not None:
            _discard_retained_artifact(opened, binding, error)
        raise
    except (OSError, OwnershipError) as error:
        failure = PipelineError(
            ErrorCode.ATOMIC_PUBLISH_FAILED,
            "artifact temporary cannot be written",
        )
        if opened is not None:
            _discard_retained_artifact(opened, binding, failure)
        raise failure from error


def _commit_new_artifact(temporary: ArtifactTemporary) -> PublishedArtifact:
    """Rename a retained artifact temporary with no replacement semantics."""

    target = temporary.target
    try:
        target.require_new_destination()
        current = temporary.owned.capture_binding()
        if (
            not current.same_identity_and_content(temporary.binding)
            or not target.backend.path_matches_binding(
                temporary.binding.path,
                temporary.binding,
            )
        ):
            raise OwnershipLostError("artifact temporary ownership changed")
        temporary.owned.rename_no_replace(target.destination)
        final_binding = temporary.owned.capture_binding()
        target.require_parent_binding()
        if (
            not final_binding.same_identity_and_content(temporary.binding)
            or not target.backend.path_matches_binding(target.destination, final_binding)
        ):
            raise OwnershipLostError("artifact publication ownership changed")
        return PublishedArtifact(
            path=target.destination,
            owned=temporary.owned,
            binding=final_binding,
            backend=target.backend,
        )
    except DestinationExistsError as error:
        raise PipelineError(
            ErrorCode.OUTPUT_EXISTS,
            "artifact appeared during publication",
        ) from error
    except PipelineError:
        raise
    except (OSError, OwnershipError) as error:
        raise PipelineError(
            ErrorCode.ATOMIC_PUBLISH_FAILED,
            "artifact no-replace publication failed",
        ) from error


def _close_published_artifact(artifact: PublishedArtifact) -> None:
    """Close a successfully committed artifact only after its final binding check."""

    current = artifact.owned.capture_binding()
    if (
        not current.same_identity_and_content(artifact.binding)
        or not artifact.backend.path_matches_binding(artifact.path, current)
    ):
        raise OwnershipLostError("artifact changed before handle release")
    artifact.owned.close()


def _rollback_published_artifact(
    artifact: PublishedArtifact,
    original_failure: BaseException,
) -> None:
    """Roll back only a just-published artifact through its retained handle."""

    try:
        _discard_retained_artifact(artifact.owned, artifact.binding, original_failure)
    except PipelineError as retained_error:
        # A close failure can consume the final handle after a successful
        # no-replace rename. Reopening is safe only when the current pathname
        # still proves the exact recorded binding; never unlink a replacement.
        if not artifact.backend.path_exists(artifact.path):
            return
        try:
            dispose_owned_binding(artifact.binding, artifact.backend)
        except (OSError, OwnershipError) as error:
            raise PipelineError(
                ErrorCode.PUBLICATION_ROLLBACK_FAILURE,
                "published artifact cannot be safely rolled back",
            ) from retained_error


def publish_new_artifacts(
    artifacts: Sequence[tuple[Path, bytes]],
    *,
    backend: FileOwnershipBackend | None = None,
    retain_handles: bool = False,
    existing_parents: Sequence[OutputParentLease] = (),
) -> list[PublishedArtifact]:
    """Atomically publish one or more JSON/Markdown-style byte artifacts.

    Every payload is first flushed and fsynced into a retained temporary.  No
    final name is committed until all temporary writes have succeeded.  A
    later no-replace commit failure rolls back each earlier final through its
    own still-open handle, so a command producing JSON and Markdown cannot
    report a half-published pair.
    """

    destinations = tuple(path for path, _payload in artifacts)
    target_set = acquire_new_output_target_leases(
        destinations,
        backend=backend,
        existing_parents=existing_parents,
    )
    staged: list[ArtifactTemporary] = []
    published: list[PublishedArtifact] = []
    failure: BaseException | None = None
    parent_closed = False
    try:
        for target, (_destination, payload) in zip(target_set.targets, artifacts):
            staged.append(_stage_new_artifact(target, payload))
        for temporary in staged:
            published.append(_commit_new_artifact(temporary))
        # Recheck all final names while every parent lease and final file
        # handle remains open.  This also catches synthetic backends that
        # model a parent replacement despite Windows sharing.
        for artifact in published:
            current = artifact.owned.capture_binding()
            if (
                not current.same_identity_and_content(artifact.binding)
                or not artifact.backend.path_matches_binding(artifact.path, current)
            ):
                raise OwnershipLostError("artifact changed after publication")

        # Parent leases must remain held through all final rename checks.  If
        # release itself fails, roll back while the artifact handles still
        # identify exactly what this transaction created.
        target_set.close()
        parent_closed = True
        if not retain_handles:
            for artifact in published:
                _close_published_artifact(artifact)
        return published
    except PipelineError as error:
        failure = error
    except (OSError, OwnershipError) as error:
        failure = PipelineError(
            ErrorCode.ATOMIC_PUBLISH_FAILED,
            "artifact publication failed",
        )
        failure.__cause__ = error

    assert failure is not None
    cleanup_failure: PipelineError | None = None
    published_ids = {id(artifact.owned) for artifact in published}
    for artifact in reversed(published):
        try:
            _rollback_published_artifact(artifact, failure)
        except PipelineError as error:
            if cleanup_failure is None:
                cleanup_failure = error
    for temporary in reversed(staged):
        if id(temporary.owned) in published_ids:
            continue
        try:
            _discard_retained_artifact(temporary.owned, temporary.binding, failure)
        except PipelineError as error:
            if cleanup_failure is None:
                cleanup_failure = error
    if not parent_closed:
        try:
            target_set.close()
        except PipelineError as error:
            if cleanup_failure is None:
                cleanup_failure = error
    if cleanup_failure is not None:
        raise cleanup_failure from failure
    if failure.__cause__ is not None:
        raise failure from failure.__cause__
    raise failure


@dataclass(frozen=True)
class StagedOutputExpectation:
    """The exact audited DWG values that publication must continue to hold."""

    sha256: str
    byte_size: int
    file_identity_fingerprint: str
    dwg_header_signature: str


@dataclass
class StagedOutputLease:
    """A no-write/delete source handle retained through final publication."""

    path: Path
    owned: OwnedPath
    binding: OwnedPathBinding
    expectation: StagedOutputExpectation
    backend: FileOwnershipBackend
    _closed: bool = False

    def require_binding(self) -> None:
        """Require the held source and its pathname to remain audited bytes."""

        try:
            current = self.owned.capture_binding()
            header = self.owned.read_prefix(6).decode("ascii", errors="ignore")
            path_matches = self.backend.path_matches_binding(self.path, current)
        except (OSError, OwnershipError) as error:
            raise PipelineError(
                ErrorCode.RE_AUDIT_MISMATCH,
                "verified staged output handle is unavailable",
            ) from error
        if (
            current.is_directory
            or current.sha256 != self.expectation.sha256
            or current.byte_size != self.expectation.byte_size
            or current.file_identity_fingerprint
            != self.expectation.file_identity_fingerprint
            or header != self.expectation.dwg_header_signature
            or not current.same_identity_and_content(self.binding)
            or not path_matches
        ):
            raise PipelineError(
                ErrorCode.RE_AUDIT_MISMATCH,
                "verified staged output changed before publication",
            )

    def close(self) -> None:
        """Release the held source only after publication cannot fail open."""

        if self._closed:
            return
        try:
            self.owned.close()
        except (OSError, OwnershipError) as error:
            raise PipelineError(
                ErrorCode.RE_AUDIT_MISMATCH,
                "verified staged output lease cannot be released",
            ) from error
        self._closed = True


def acquire_staged_output_lease(
    staged: Path,
    *,
    backend: FileOwnershipBackend | None = None,
    expectation: StagedOutputExpectation | None = None,
) -> StagedOutputLease:
    """Open and bind a staged DWG before any publication bytes are copied.

    The dedicated read lease shares only read access, preventing later
    writer/delete opens while still allowing ODA to open the staged pathname
    as a reader.  Requiring DELETE access on the held handle would force ODA
    to share DELETE too and would reject safe read-only converter opens.
    """

    selected_backend = _publication_backend(backend)
    opened: OwnedPath | None = None
    try:
        lexical = lexical_absolute_path(staged)
        opened = selected_backend.open_existing_file_read_lease(lexical)
        binding = opened.capture_binding()
        resolved = opened.final_path()
        if (
            binding.is_directory
            or resolved.name.casefold() != lexical.name.casefold()
            or not selected_backend.path_matches_binding(lexical, binding)
            or not selected_backend.path_matches_binding(resolved, binding)
        ):
            raise OwnershipLostError("staged output is not a direct bound file")
        header = opened.read_prefix(6).decode("ascii", errors="ignore")
        derived = StagedOutputExpectation(
            sha256=binding.sha256 or "",
            byte_size=binding.byte_size if binding.byte_size is not None else -1,
            file_identity_fingerprint=binding.file_identity_fingerprint,
            dwg_header_signature=header,
        )
        expected = expectation or derived
        lease = StagedOutputLease(
            path=resolved,
            owned=opened,
            binding=binding,
            expectation=expected,
            backend=selected_backend,
        )
        lease.require_binding()
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
            ErrorCode.RE_AUDIT_MISMATCH,
            "verified staged output cannot be leased",
        ) from error


def _copy_for_publication(
    staged: Path | StagedOutputLease,
    destination: Path,
    *,
    backend: FileOwnershipBackend | None = None,
    target: OutputTargetLease | None = None,
) -> PublicationTemporary:
    """Copy from a held staged handle into a private handle-owned temporary."""

    selected_backend = _publication_backend(backend)
    supplied_lease = isinstance(staged, StagedOutputLease)
    lease = (
        staged
        if supplied_lease
        else acquire_staged_output_lease(staged, backend=selected_backend)
    )
    if target is not None:
        target.require_new_destination()
        destination = target.destination
        temporary_parent = target.parent.path
    else:
        temporary_parent = destination.parent
    temporary = temporary_parent / f".liang-pingfa-publish-{uuid4().hex}.tmp"
    owned: OwnedPath | None = None
    binding: OwnedPathBinding | None = None
    try:
        if target is not None:
            # The retained parent handle denies a junction/rename replacement;
            # this identity recheck is the fallback for pathname-based create.
            target.require_new_destination()
        owned = selected_backend.create_new_file(temporary)
        # The source bytes come from the already-open staged handle rather
        # than a second path lookup. The source lease denies a replacement or
        # new writer for this whole stream transfer.
        owned.write_chunks(lease.owned.read_chunks())
        binding = owned.capture_binding()
        if (
            binding.is_directory
            or binding.sha256 != lease.expectation.sha256
            or binding.byte_size != lease.expectation.byte_size
            or not selected_backend.path_matches_binding(temporary, binding)
        ):
            raise OwnershipLostError("publication temporary content binding differs")
        # Rebind after the destination flush/fsync while the source handle is
        # still retained. This closes the formerly path-only copy race.
        lease.require_binding()
        return PublicationTemporary(owned=owned, binding=binding)
    except PipelineError:
        failure: BaseException = PipelineError(
            ErrorCode.RE_AUDIT_MISMATCH,
            "verified staged output cannot be published",
        )
        if owned is not None:
            _cleanup_unfinished_temporary(
                owned,
                binding,
                selected_backend,
                temporary_parent,
                failure,
            )
        raise failure
    except (OSError, OwnershipError) as error:
        failure = PipelineError(
            ErrorCode.ATOMIC_PUBLISH_FAILED, "unable to stage publication"
        )
        if owned is not None:
            _cleanup_unfinished_temporary(
                owned,
                binding,
                selected_backend,
                temporary_parent,
                failure,
            )
        raise failure from error
    finally:
        if not supplied_lease:
            lease.close()


def _cleanup_temporary_after_failure(
    owned: OwnedPath,
    binding: OwnedPathBinding,
    backend: FileOwnershipBackend,
    output_parent: Path,
    original_failure: BaseException,
) -> None:
    """Dispose only the still-open temporary or surface a stable failure."""

    try:
        recover_publication_temporary(
            binding.path,
            output_parent,
            binding=binding,
            backend=backend,
            opened=owned,
        )
    except PipelineError as cleanup_error:
        raise cleanup_error from original_failure


def _cleanup_unfinished_temporary(
    owned: OwnedPath,
    binding: OwnedPathBinding | None,
    backend: FileOwnershipBackend,
    output_parent: Path,
    original_failure: BaseException,
) -> None:
    """Recover a partly copied temporary through the still-open identity."""

    try:
        current_binding = binding or owned.capture_binding()
    except (OSError, OwnershipError) as error:
        try:
            owned.close()
        except (OSError, OwnershipError):
            pass
        raise PipelineError(
            ErrorCode.PUBLICATION_CLEANUP_FAILURE,
            "partly copied publication temporary cannot be bound for cleanup",
        ) from error
    _cleanup_temporary_after_failure(
        owned,
        current_binding,
        backend,
        output_parent,
        original_failure,
    )


def _verify_published_binding(
    publication: PublicationTemporary,
    destination: Path,
    backend: FileOwnershipBackend,
) -> OwnedPathBinding:
    """Require the renamed handle and final pathname to be the verified bytes."""

    current = publication.owned.capture_binding()
    if (
        not current.same_identity_and_content(publication.binding)
        or not backend.path_matches_binding(destination, current)
    ):
        raise OwnershipLostError("published output ownership changed")
    return current


def _rollback_published_output(
    publication: PublicationTemporary,
    binding: OwnedPathBinding | None,
    backend: FileOwnershipBackend,
    original_failure: BaseException,
) -> None:
    """Delete a post-rename failure only through the retained final handle."""

    if binding is None:
        raise PipelineError(
            ErrorCode.PUBLICATION_ROLLBACK_FAILURE,
            "published output cannot be safely rolled back",
        ) from original_failure
    try:
        # The final handle, not the current parent pathname, remains the
        # rollback authority. This stays safe if a synthetic backend models a
        # parent replacement after the final rename.
        dispose_retained_owned_path(publication.owned, binding)
    except (OSError, OwnershipError) as error:
        raise PipelineError(
            ErrorCode.PUBLICATION_ROLLBACK_FAILURE,
            "published output cannot be safely rolled back",
        ) from original_failure


def publish_no_replace(
    staged: Path | StagedOutputLease,
    destination: Path,
    *,
    before_commit: Callable[[], None] | None = None,
    source_binding: Callable[[], None] | None = None,
    after_commit: Callable[[OwnedPath, OwnedPathBinding], _Result] | None = None,
    backend: FileOwnershipBackend | None = None,
    output_target: OutputTargetLease | None = None,
) -> _Result | None:
    """Publish verified bytes without ever replacing a pre-existing output.

    On Windows the temporary is created with a retained DELETE+read handle
    that shares only read access.  The final rename uses that same handle with
    ``FileRenameInfo.ReplaceIfExists = FALSE``.  An injected backend exists
    solely for synthetic tests; the public production default fails closed
    away from Windows.
    """

    selected_backend = _publication_backend(backend)
    owned_target_set: OutputTargetLeaseSet | None = None
    target = output_target
    if target is None:
        owned_target_set = acquire_new_output_target_leases(
            (destination,),
            backend=selected_backend,
        )
        target = owned_target_set.targets[0]
    else:
        target.require_new_destination()
    destination = target.destination
    supplied_lease = isinstance(staged, StagedOutputLease)
    lease = (
        staged
        if supplied_lease
        else acquire_staged_output_lease(staged, backend=selected_backend)
    )
    publication: PublicationTemporary | None = None
    committed = False
    final_binding: OwnedPathBinding | None = None
    failure: BaseException | None = None
    try:
        publication = _copy_for_publication(
            lease,
            destination,
            backend=selected_backend,
            target=target,
        )
        # Test hooks deliberately execute while the temporary's handle is
        # retained. A same-user replacement is denied by Windows sharing; a
        # synthetic backend that models a swap is detected below.
        if before_commit is not None:
            before_commit()
        if source_binding is not None:
            source_binding()
        target.require_new_destination()
        if (
            not publication.owned.capture_binding().same_identity_and_content(
                publication.binding
            )
            or not selected_backend.path_matches_binding(
                publication.binding.path, publication.binding
            )
        ):
            raise OwnershipLostError("publication temporary ownership changed")
        # This is the final staged-output binding check immediately before
        # invoking the irreversible no-replace handle rename.
        lease.require_binding()

        # There is intentionally no path-exists check here. The handle rename
        # is the only final decision, atomically failing when a destination
        # appears and never setting a replace-existing flag.
        publication.owned.rename_no_replace(destination)
        committed = True
        target.require_parent_binding()
        final_binding = _verify_published_binding(
            publication,
            destination,
            selected_backend,
        )
        if after_commit is not None:
            result = after_commit(publication.owned, final_binding)
        else:
            result = None
        # An after-commit verifier is still inside the held leases. A hostile
        # test backend may model a staged swap here; in production the Windows
        # source handle denies it. Either way, never return evidence unless
        # both source and final bindings remain exact.
        _verify_published_binding(publication, destination, selected_backend)
        lease.require_binding()
    except DestinationExistsError as error:
        failure = PipelineError(
            ErrorCode.OUTPUT_EXISTS, "output appeared during publication"
        )
        failure.__cause__ = error
    except PipelineError as error:
        failure = error
    except (OSError, OwnershipError) as error:
        failure = PipelineError(
            ErrorCode.ATOMIC_PUBLISH_FAILED, "handle-safe publication failed"
        )
        failure.__cause__ = error

    if failure is not None:
        try:
            if publication is not None:
                if committed:
                    _rollback_published_output(
                        publication,
                        final_binding,
                        selected_backend,
                        failure,
                    )
                else:
                    _cleanup_temporary_after_failure(
                        publication.owned,
                        publication.binding,
                        selected_backend,
                        destination.parent,
                        failure,
                    )
        finally:
            try:
                lease.close()
            except PipelineError:
                # The original publication failure already prevents a success;
                # avoid replacing its redacted category with close mechanics.
                pass
            if owned_target_set is not None:
                try:
                    owned_target_set.close()
                except PipelineError:
                    # A prior failure already fails closed. The parent handle
                    # is still process-owned and no success is reported.
                    pass
        if failure.__cause__ is not None:
            raise failure from failure.__cause__
        raise failure

    assert publication is not None
    try:
        # A source-lease release failure occurs after final rename, so roll
        # back while the publication handle still proves the final identity.
        lease.close()
    except PipelineError as error:
        _rollback_published_output(
            publication,
            final_binding,
            selected_backend,
            error,
        )
        raise
    if owned_target_set is not None:
        try:
            # Keep the parent lease through source release and every final
            # output verification. A release failure is still rollback-safe
            # because the publication handle remains retained.
            owned_target_set.close()
        except PipelineError as error:
            _rollback_published_output(
                publication,
                final_binding,
                selected_backend,
                error,
            )
            raise
    try:
        publication.owned.close()
    except (OSError, OwnershipError) as error:
        close_failure = PipelineError(
            ErrorCode.ATOMIC_PUBLISH_FAILED,
            "published handle cannot be released",
        )
        try:
            _rollback_published_output(
                publication,
                final_binding,
                selected_backend,
                close_failure,
            )
        except PipelineError:
            raise
        raise close_failure from error
    return result
