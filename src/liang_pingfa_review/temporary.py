"""Identity-bound cleanup for application-created temporary resources."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import re
import tempfile
import time
from types import TracebackType

from .errors import ErrorCode, PipelineError
from .ownership import (
    FileOwnershipBackend,
    LexicalDirectoryChainLease,
    OwnedPath,
    OwnedPathBinding,
    PrivateDirectoryCreation,
    OwnershipCleanupError,
    OwnershipError,
    OwnershipLostError,
    bind_existing_path,
    binding_matches_path,
    acquire_lexical_directory_chain,
    create_private_directory,
    create_private_workspace_directory,
    dispose_live_owned_path,
    dispose_owned_binding,
    is_reparse_point,
    platform_backend,
    verify_private_staging_file,
    validate_private_staging_ancestry,
)


_CLEANUP_RETRY_DELAYS = (0.01, 0.05, 0.1)
_PUBLICATION_TEMPORARY_NAME = re.compile(
    r"\A[.]liang-pingfa-publish-[0-9a-f]{32}[.]tmp\Z"
)


def _retry_cleanup(operation: Callable[[], None]) -> None:
    """Retry sharing conflicts but never retry an ownership mismatch."""

    last_error: BaseException | None = None
    for attempt, delay in enumerate(_CLEANUP_RETRY_DELAYS):
        try:
            operation()
            return
        except OwnershipLostError:
            raise
        except (OwnershipCleanupError, OSError) as error:
            last_error = error
            if attempt < len(_CLEANUP_RETRY_DELAYS) - 1:
                time.sleep(delay)
    assert last_error is not None
    raise OwnershipCleanupError("owned cleanup did not complete") from last_error


def _cleanup_pipeline_error(error: BaseException) -> PipelineError:
    """Map implementation details to a redacted cleanup outcome."""

    if isinstance(error, OwnershipLostError):
        return PipelineError(
            ErrorCode.PUBLICATION_CLEANUP_FAILURE,
            "application-owned temporary identity was lost",
        )
    return PipelineError(
        ErrorCode.PUBLICATION_CLEANUP_FAILURE,
        "application temporary cleanup did not complete",
    )


def _resolve_backend(
    backend: FileOwnershipBackend | None,
) -> FileOwnershipBackend:
    if backend is not None:
        return backend
    try:
        return platform_backend(require_windows=True)
    except OwnershipCleanupError as error:
        raise PipelineError(
            ErrorCode.WINDOWS_PLATFORM_REQUIRED,
            "private staging requires Windows handle semantics",
        ) from error


def recover_publication_temporary(
    temporary: Path,
    output_parent: Path,
    *,
    binding: OwnedPathBinding,
    backend: FileOwnershipBackend | None = None,
    opened: OwnedPath | None = None,
) -> None:
    """Delete only a recorded publication temporary through its owned handle.

    A replacement at ``temporary`` is never unlinked.  When the creator still
    holds a handle, deletion targets that handle after rechecking its content
    binding; otherwise the current path is reopened with DELETE access before
    its identity is compared and deleted.
    """

    try:
        # The caller retains the validated output-parent handle through this
        # rollback. Comparing lexical absolute spellings here avoids another
        # reparse-following resolution during cleanup; deletion itself remains
        # bound to the already-open temporary handle below.
        expected_parent = Path(os.path.abspath(os.fspath(output_parent)))
        actual_parent = Path(os.path.abspath(os.fspath(temporary.parent)))
    except (OSError, ValueError) as error:
        raise PipelineError(
            ErrorCode.PUBLICATION_CLEANUP_FAILURE,
            "publication temporary recovery path is unavailable",
        ) from error
    if (
        actual_parent != expected_parent
        or _PUBLICATION_TEMPORARY_NAME.fullmatch(temporary.name) is None
        or binding.path != temporary
        or binding.is_directory
    ):
        raise PipelineError(
            ErrorCode.PUBLICATION_CLEANUP_FAILURE,
            "publication temporary recovery binding is invalid",
        )
    selected_backend = _resolve_backend(backend)
    try:
        if opened is None:
            _retry_cleanup(
                lambda: dispose_owned_binding(binding, selected_backend)
            )
        else:
            _retry_cleanup(
                lambda: dispose_live_owned_path(opened, binding, selected_backend)
            )
    except (OwnershipError, OSError) as error:
        raise _cleanup_pipeline_error(error) from error


class PrivateWorkspace:
    """Own and remove a known, identity-bound private staging directory.

    Callers register every file/directory they create through
    :meth:`track_created_file` or :meth:`track_created_directory`.  Cleanup
    refuses unknown, missing, or replacement entries instead of recursively
    deleting by pathname.
    """

    def __init__(
        self,
        *,
        prefix: str,
        directory: Path | None = None,
        backend: FileOwnershipBackend | None = None,
    ) -> None:
        self._prefix = prefix
        self._directory = directory
        self._backend = _resolve_backend(backend)
        self._path: Path | None = None
        self._root_binding: OwnedPathBinding | None = None
        self._children: dict[Path, OwnedPathBinding] = {}
        self._work_root_chain: LexicalDirectoryChainLease | None = None
        self._workspace_root_chain: LexicalDirectoryChainLease | None = None
        self._child_directory_chains: dict[Path, LexicalDirectoryChainLease] = {}
        self._retained_files: list[OwnedPath] = []

    @property
    def path(self) -> Path:
        """Return the private root path after the context has been entered."""

        if self._path is None:
            raise RuntimeError("workspace has not been entered")
        return self._path

    @property
    def backend(self) -> FileOwnershipBackend:
        """Expose the workspace's retained-handle backend to staging adapters."""

        return self._backend

    def __fspath__(self) -> str:
        return os.fspath(self.path)

    def __truediv__(self, value: str | Path) -> Path:
        return self.path / value

    def __enter__(self) -> "PrivateWorkspace":
        """Create a private root beneath a retained lexical work-root chain."""

        creation: PrivateDirectoryCreation | None = None
        try:
            work_root = (
                Path(os.fspath(self._directory))
                if self._directory is not None
                else Path(tempfile.gettempdir())
            )
            # ``directory`` is user intent, so it is never resolved before
            # every lexical component from its drive/root is opened no-follow.
            self._work_root_chain = acquire_lexical_directory_chain(
                work_root,
                self._backend,
            )
            validate_private_staging_ancestry(
                self._work_root_chain.path,
                self._backend,
            )
            # Windows receives a protected owner/DACL in CreateDirectoryW,
            # before a directory entry exists. The returned handle is bound
            # and validated before this method can create any child.
            creation = create_private_workspace_directory(
                self._work_root_chain.path,
                self._prefix,
                self._backend,
            )
            created = creation.path
            if (
                created.parent != self._work_root_chain.path
                or not created.name.startswith(self._prefix)
            ):
                raise OwnershipLostError("private workspace path is not a direct child")
            # The initial DELETE-capable handle intentionally denies child
            # writers. Release it only after validation so the regular lexical
            # workspace lease can retain compatible ancestor protection.
            creation.close()
            # The root itself is another retained lexical chain. Its canonical
            # path comes only from its final no-follow handle, never resolve().
            self._workspace_root_chain = acquire_lexical_directory_chain(
                created,
                self._backend,
            )
            self._path = self._workspace_root_chain.path
            self._root_binding = self._workspace_root_chain.binding
            if not self._root_binding.same_identity_and_content(creation.binding):
                raise OwnershipLostError("private workspace registration changed identity")
            self._workspace_root_chain.require_binding()
            # Ownership is now represented by the retained lexical chain;
            # normal context recovery handles the registered root.
            creation = None
        except (OSError, OwnershipError) as error:
            cleanup_error: BaseException | None = None
            if creation is not None:
                try:
                    # A lexical workspace lease blocks DELETE sharing, so
                    # release only that local chain while retaining the
                    # work-root ancestry for exact-handle cleanup.
                    if self._workspace_root_chain is not None:
                        self._workspace_root_chain.close()
                        self._workspace_root_chain = None
                    creation.dispose(self._backend)
                except (OSError, OwnershipError) as cleanup:
                    cleanup_error = cleanup
            self._close_workspace_chains_safely()
            raise PipelineError(
                ErrorCode.CONVERSION_FAILURE,
                "private staging workspace cannot be created",
            ) from (cleanup_error or error)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        try:
            self._recover()
        except PipelineError as cleanup_error:
            if exc is not None:
                raise cleanup_error from exc
            raise
        return False

    def _close_workspace_chains_safely(self) -> None:
        """Best-effort release used only while context entry is already failing."""

        for opened in reversed(self._retained_files):
            try:
                opened.close()
            except (OSError, OwnershipError):
                pass
        self._retained_files.clear()
        chains = [
            *self._child_directory_chains.values(),
            *(
                (self._workspace_root_chain,)
                if self._workspace_root_chain is not None
                else ()
            ),
            *(
                (self._work_root_chain,)
                if self._work_root_chain is not None
                else ()
            ),
        ]
        for chain in reversed(chains):
            try:
                chain.close()
            except (OSError, OwnershipError):
                pass

    def _release_workspace_local_chains(self) -> None:
        """Release child/root handles before reopening the root for deletion.

        The retained work-root chain remains open.  It continues to deny
        replacement of every lexical ancestor while the workspace's own root
        is reopened with DELETE access for identity-bound recovery.
        """

        failure: BaseException | None = None
        for opened in reversed(self._retained_files):
            try:
                opened.close()
            except (OSError, OwnershipError) as error:
                if failure is None:
                    failure = error
        self._retained_files.clear()
        chains = sorted(
            self._child_directory_chains.values(),
            key=lambda chain: len(chain.path.parts),
            reverse=True,
        )
        if self._workspace_root_chain is not None:
            chains.append(self._workspace_root_chain)
        for chain in chains:
            try:
                chain.close()
            except (OSError, OwnershipError) as error:
                if failure is None:
                    failure = error
        if failure is not None:
            raise _cleanup_pipeline_error(failure)

    def _normalize_child(self, path: Path) -> Path:
        """Validate lexical containment without resolving a workspace entry."""

        try:
            raw_path = os.fspath(path)
            normalized = Path(raw_path)
            if (
                not raw_path
                or "\x00" in raw_path
                or not normalized.is_absolute()
            ):
                raise ValueError("workspace child is not an absolute path")
            pieces = (
                raw_path.replace("/", chr(92)).split(chr(92))
                if normalized.drive
                else raw_path.split("/")
            )
            if any(piece in {".", ".."} for piece in pieces):
                raise ValueError("workspace child contains traversal")
            normalized.relative_to(self.path)
        except (OSError, ValueError) as error:
            raise PipelineError(
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                "workspace child path is invalid",
            ) from error
        if normalized == self.path:
            raise PipelineError(
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                "workspace root is not a child",
            )
        return normalized

    def _require_live_workspace_chains(self) -> None:
        """Keep the retained no-follow workspace ancestry authoritative."""

        if (
            self._work_root_chain is None
            or self._workspace_root_chain is None
            or self._root_binding is None
        ):
            raise OwnershipLostError("workspace chains are unavailable")
        self._work_root_chain.require_binding()
        self._workspace_root_chain.require_binding()

    def _validate_opened_private_file(
        self,
        normalized: Path,
        opened: OwnedPath,
        *,
        expected_binding: OwnedPathBinding | None = None,
    ) -> OwnedPathBinding:
        """Validate one externally reachable private file through its lease.

        The caller holds both the workspace ancestry and this no-write/no-
        delete file handle.  We deliberately validate owner/DACL before and
        after capturing the content binding, so an external Core Console save
        cannot be adopted merely because a filename happened to remain.
        """

        self._require_live_workspace_chains()
        before = opened.capture_binding()
        final_path = opened.final_path()
        if (
            before.is_directory
            or before.sha256 is None
            or before.byte_size is None
            or final_path.name.casefold() != normalized.name.casefold()
            or not self._backend.path_matches_binding(normalized, before)
        ):
            raise OwnershipLostError("external workspace file escaped binding")
        owner_before = verify_private_staging_file(
            opened,
            self._backend,
            require_protected=False,
        )
        after = opened.capture_binding()
        owner_after = verify_private_staging_file(
            opened,
            self._backend,
            require_protected=False,
        )
        if (
            not after.same_identity_and_content(before)
            or (
                owner_before is not None
                and owner_after is not None
                and owner_before != owner_after
            )
            or (
                expected_binding is not None
                and not after.same_identity_and_content(expected_binding)
            )
            or not self._backend.path_matches_binding(normalized, after)
        ):
            raise OwnershipLostError("external workspace file changed during validation")
        return after

    def open_validated_external_file_read_lease(
        self,
        path: Path,
        *,
        allow_replacement: bool,
    ) -> OwnedPath:
        """Lease a known external-save target only after private validation.

        A write-mode Core Console is the sole caller allowed to replace its
        pre-registered DWG.  Readback callers must retain the same exact
        identity; both paths receive a read-only sharing lease before any
        bytes, header, result, or publication path can inspect the file.
        """

        normalized = self._normalize_child(path)
        previous = self._children.get(normalized)
        if previous is None or previous.is_directory:
            raise PipelineError(
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                "external workspace file was not pre-registered",
            )
        opened: OwnedPath | None = None
        try:
            opened = self._backend.open_existing_file_read_lease(normalized)
            binding = self._validate_opened_private_file(normalized, opened)
            if (
                not allow_replacement
                and not binding.same_identity_and_content(previous)
            ):
                raise OwnershipLostError("external workspace file replacement is not allowed")
            return opened
        except (OSError, OwnershipError) as error:
            if opened is not None:
                try:
                    opened.close()
                except (OSError, OwnershipError):
                    pass
            raise _cleanup_pipeline_error(error) from error

    def validate_retained_private_file(
        self,
        path: Path,
        opened: OwnedPath,
        *,
        expected_binding: OwnedPathBinding | None = None,
    ) -> OwnedPathBinding:
        """Revalidate a caller-retained private read lease before use."""

        normalized = self._normalize_child(path)
        if normalized not in self._children:
            raise PipelineError(
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                "workspace private file was not registered",
            )
        try:
            return self._validate_opened_private_file(
                normalized,
                opened,
                expected_binding=expected_binding,
            )
        except (OSError, OwnershipError) as error:
            raise _cleanup_pipeline_error(error) from error

    def create_owned_directory(self, path: Path) -> Path:
        """Atomically create and bind one private child directory before use.

        External tools may receive this directory only after its exact
        identity is registered.  In particular, callers must not create an
        output root and defer registration until after a converter returns:
        a timeout or launch failure would otherwise strand other owned
        staging files behind that unknown directory.
        """

        candidate = self._normalize_child(path)
        if (
            candidate == self.path
            or (
                candidate.parent != self.path
                and candidate.parent not in self._children
            )
        ):
            raise PipelineError(
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                "workspace directory parent was not registered",
            )
        if os.path.lexists(candidate):
            raise PipelineError(
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                "workspace directory already exists",
            )
        creation: PrivateDirectoryCreation | None = None
        registered: Path | None = None
        try:
            self._require_live_workspace_chains()
            creation = create_private_directory(candidate, self._backend)
            if (
                creation.path.parent != self.path
                and creation.path.parent not in self._children
            ):
                raise OwnershipLostError("private workspace directory escaped its parent")
            creation.close()
            registered = self.track_created_directory(creation.path)
            binding = self._children[registered]
            if not binding.same_identity_and_content(creation.binding):
                raise OwnershipLostError(
                    "private workspace directory registration changed identity"
                )
            creation = None
            return registered
        except (OSError, OwnershipError) as error:
            cleanup_error: BaseException | None = None
            if creation is not None:
                try:
                    if registered is not None:
                        chain = self._child_directory_chains.pop(registered, None)
                        if chain is not None:
                            chain.close()
                        self._children.pop(registered, None)
                    creation.dispose(self._backend)
                except (OSError, OwnershipError) as cleanup:
                    cleanup_error = cleanup
            raise _cleanup_pipeline_error(cleanup_error or error) from error

    def create_private_oda_root(self, path: Path) -> Path:
        """Create one random ODA root with its restrictive DACL at creation.

        The workspace ancestry is already no-follow and NTFS-qualified. This
        atomic child creation makes every process-specific root independently
        private before input/output children or a converter command line exist.
        """

        return self.create_owned_directory(path)

    def track_created_directory(self, path: Path) -> Path:
        """Record the exact identity of a just-created private directory."""

        normalized = self._normalize_child(path)
        if normalized in self._children:
            return normalized
        parent = normalized.parent
        if parent != self.path and parent not in self._children:
            raise PipelineError(
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                "workspace directory parent was not registered",
            )
        chain: LexicalDirectoryChainLease | None = None
        try:
            chain = acquire_lexical_directory_chain(normalized, self._backend)
            actual = chain.path
            if (
                actual == self.path
                or (
                    actual.parent != self.path
                    and actual.parent not in self._children
                )
            ):
                raise OwnershipLostError("workspace directory escaped its parent")
            binding = chain.binding
        except (OSError, OwnershipError) as error:
            if chain is not None:
                try:
                    chain.close()
                except (OSError, OwnershipError):
                    pass
            raise _cleanup_pipeline_error(error) from error
        self._children[actual] = binding
        self._child_directory_chains[actual] = chain
        return actual

    def track_created_file(
        self,
        path: Path,
        *,
        expected_binding: OwnedPathBinding | None = None,
    ) -> Path:
        """Record a private file, optionally transferring one exact binding.

        ``expected_binding`` is for a producer that already published under a
        retained handle (for example local regression's apply result). The
        current pathname must still name those exact bytes; a replacement is
        never adopted and remains quarantined for fail-closed cleanup.
        """

        normalized = self._normalize_child(path)
        if normalized in self._children:
            if expected_binding is not None:
                existing = self._children[normalized]
                if not existing.same_identity_and_content(expected_binding):
                    raise PipelineError(
                        ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                        "workspace file binding differs from expected transfer",
                    )
            return normalized
        if normalized.parent != self.path and normalized.parent not in self._children:
            raise PipelineError(
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                "workspace file parent was not registered",
            )
        try:
            binding = bind_existing_path(
                normalized,
                self._backend,
                is_directory=False,
            )
            if expected_binding is not None:
                expected_path = os.path.normcase(
                    os.path.abspath(os.fspath(expected_binding.path))
                )
                normalized_path = os.path.normcase(
                    os.path.abspath(os.fspath(normalized))
                )
                if (
                    expected_binding.is_directory
                    or expected_path != normalized_path
                    or not binding.same_identity_and_content(expected_binding)
                    or not self._backend.path_matches_binding(
                        normalized,
                        expected_binding,
                    )
                ):
                    raise OwnershipLostError(
                        "workspace file differs from expected ownership transfer"
                    )
        except (OSError, OwnershipError) as error:
            raise _cleanup_pipeline_error(error) from error
        # Preserve the producer's binding, not a freshly reopened equivalent:
        # later cleanup must delete only the exact handle-bound output which
        # apply produced, never a pathname replacement.
        self._children[normalized] = expected_binding or binding
        return normalized

    def track_external_file(self, path: Path) -> Path:
        """Register an external result only after owner/DACL lease validation."""

        normalized = self._normalize_child(path)
        if normalized in self._children:
            raise PipelineError(
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                "external workspace file is already registered",
            )
        if normalized.parent != self.path and normalized.parent not in self._children:
            raise PipelineError(
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                "workspace file parent was not registered",
            )
        opened: OwnedPath | None = None
        try:
            opened = self._backend.open_existing_file_read_lease(normalized)
            binding = self._validate_opened_private_file(normalized, opened)
            self._children[normalized] = binding
            return normalized
        except (OSError, OwnershipError) as error:
            raise _cleanup_pipeline_error(error) from error
        finally:
            if opened is not None:
                try:
                    opened.close()
                except (OSError, OwnershipError) as error:
                    raise _cleanup_pipeline_error(error) from error

    def adopt_external_file(
        self,
        path: Path,
        *,
        opened: OwnedPath | None = None,
        allow_replacement: bool = False,
        expected_binding: OwnedPathBinding | None = None,
    ) -> Path:
        """Refresh one known private file after an approved external process.

        Most workspace files are immutable after their creator seals them.
        Native Core Console is the narrow exception: it is intentionally
        allowed to save only the already registered private DWG copy. A
        caller must opt in to replacement explicitly, and only after its
        retained read lease has proven trusted owner, exact private DACL,
        regular-file identity, and stable bytes. It never adopts an unknown
        sidecar or a path outside the private root.
        """

        normalized = self._normalize_child(path)
        previous = self._children.get(normalized)
        if previous is None or previous.is_directory:
            raise PipelineError(
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                "external workspace file was not pre-registered",
            )
        owns_opened = opened is None
        try:
            if opened is None:
                opened = self._backend.open_existing_file_read_lease(normalized)
            binding = self._validate_opened_private_file(
                normalized,
                opened,
                expected_binding=expected_binding,
            )
            if (
                not allow_replacement
                and not binding.same_identity_and_content(previous)
            ):
                raise OwnershipLostError("external workspace file replacement is not allowed")
        except (OSError, OwnershipError) as error:
            raise _cleanup_pipeline_error(error) from error
        finally:
            if owns_opened and opened is not None:
                try:
                    opened.close()
                except (OSError, OwnershipError) as error:
                    raise _cleanup_pipeline_error(error) from error
        self._children[normalized] = binding
        return normalized

    def read_tracked_file_bytes(
        self,
        path: Path,
        *,
        maximum_bytes: int,
        consume: Callable[[bytes], object] | None = None,
    ) -> object:
        """Read one registered private file through a no-write/delete lease.

        External native processes may create a bounded result artifact after
        successful exit.  The caller first registers it, then this helper
        proves the current handle still names that registration before and
        after reading; it never falls back to a path-only JSON read.
        """

        if maximum_bytes < 1:
            raise PipelineError(
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                "workspace read bound is invalid",
            )
        normalized = self._normalize_child(path)
        expected = self._children.get(normalized)
        if expected is None or expected.is_directory:
            raise PipelineError(
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                "workspace read file was not registered",
            )
        opened: OwnedPath | None = None
        try:
            opened = self._backend.open_existing_file_read_lease(normalized)
            self._validate_opened_private_file(
                normalized,
                opened,
                expected_binding=expected,
            )
            chunks: list[bytes] = []
            total = 0
            for chunk in opened.read_chunks():
                total += len(chunk)
                if total > maximum_bytes:
                    raise OwnershipLostError("workspace result exceeds read bound")
                chunks.append(chunk)
            payload = b"".join(chunks)
            result = consume(payload) if consume is not None else payload
            self._validate_opened_private_file(
                normalized,
                opened,
                expected_binding=expected,
            )
            return result
        except (OSError, OwnershipError) as error:
            raise _cleanup_pipeline_error(error) from error
        finally:
            if opened is not None:
                try:
                    opened.close()
                except (OSError, OwnershipError) as error:
                    raise _cleanup_pipeline_error(error) from error

    def require_tracked_file_security(self, path: Path) -> None:
        """Recheck one registered private input through its retained read handle.

        A manifest, source copy, or fixed script may be generated inside this
        workspace, but that provenance does not waive the final ownership,
        no-follow identity, and effective current-user/SYSTEM DACL proof
        immediately before an external process receives its path.
        """

        normalized = self._normalize_child(path)
        expected = self._children.get(normalized)
        if expected is None or expected.is_directory:
            raise PipelineError(
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                "workspace private input was not registered",
            )
        opened: OwnedPath | None = None
        try:
            opened = self._backend.open_existing_file_read_lease(normalized)
            self._validate_opened_private_file(
                normalized,
                opened,
                expected_binding=expected,
            )
        except (OSError, OwnershipError) as error:
            raise _cleanup_pipeline_error(error) from error
        finally:
            if opened is not None:
                try:
                    opened.close()
                except (OSError, OwnershipError) as error:
                    raise _cleanup_pipeline_error(error) from error

    def track_opened_file(self, opened: OwnedPath) -> Path:
        """Record a private file through its already-retained ownership handle.

        A staged DWG must be leased before its first hash/header binding.  This
        method lets the conversion boundary register that generated file for
        workspace cleanup without reopening its pathname and reintroducing an
        ABA window.
        """

        try:
            normalized = self._normalize_child(opened.path)
            if normalized in self._children:
                return normalized
            if normalized.parent != self.path and normalized.parent not in self._children:
                raise OwnershipLostError("workspace file parent was not registered")
            binding = opened.capture_binding()
            if (
                binding.is_directory
                or not self._backend.path_matches_binding(normalized, binding)
            ):
                raise OwnershipLostError("opened workspace file is not owned")
            self._children[normalized] = binding
            return normalized
        except (OSError, OwnershipError) as error:
            raise _cleanup_pipeline_error(error) from error

    def create_owned_file(self, path: Path) -> OwnedPath:
        """Exclusively create, bind, and retain an empty private file.

        The caller receives an already-registered handle before it can write
        one byte.  It must call :meth:`seal_owned_file` after a successful
        write or :meth:`discard_owned_file` after a failed write; both paths
        operate through this retained identity rather than rediscovering the
        pathname.
        """

        return self._create_owned_file(path, self._backend.create_new_file)

    def _create_owned_file(
        self,
        path: Path,
        creator: Callable[[Path], OwnedPath],
    ) -> OwnedPath:
        """Create and bind one empty file with the selected ownership mode."""

        candidate = self._normalize_child(path)
        if (
            candidate == self.path
            or (
                candidate.parent != self.path
                and candidate.parent not in self._children
            )
        ):
            raise PipelineError(
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                "workspace file parent was not registered",
            )
        if os.path.lexists(candidate):
            raise PipelineError(
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                "workspace file already exists",
            )

        opened: OwnedPath | None = None
        try:
            opened = creator(candidate)
            binding = opened.capture_binding()
            if (
                binding.is_directory
                or binding.byte_size != 0
                or binding.sha256 is None
                or not self._backend.path_matches_binding(candidate, binding)
            ):
                raise OwnershipLostError("new workspace file is not owned")
            self._children[candidate] = binding
            return opened
        except (OSError, OwnershipError) as error:
            if opened is not None:
                try:
                    # The handle was created by this workspace and has not
                    # been released, so deleting through it cannot target a
                    # later pathname replacement even when binding failed.
                    opened.request_delete()
                    opened.close()
                except (OSError, OwnershipError) as cleanup_error:
                    raise _cleanup_pipeline_error(cleanup_error) from error
            raise _cleanup_pipeline_error(error) from error

    def retain_opened_file(self, opened: OwnedPath) -> Path:
        """Keep a read lease open until workspace cleanup releases it safely."""

        normalized = self.track_opened_file(opened)
        if not any(existing is opened for existing in self._retained_files):
            self._retained_files.append(opened)
        return normalized

    def release_retained_file(self, opened: OwnedPath) -> None:
        """Release a file retained by :meth:`retain_opened_file` exactly once."""

        try:
            self._retained_files = [
                existing for existing in self._retained_files if existing is not opened
            ]
            opened.close()
        except (OSError, OwnershipError) as error:
            raise _cleanup_pipeline_error(error) from error

    def seal_owned_file(self, opened: OwnedPath) -> Path:
        """Update a registered file binding after a successful owned write."""

        try:
            normalized = self._normalize_child(opened.path)
            previous = self._children.get(normalized)
            if previous is None or previous.is_directory:
                raise OwnershipLostError("workspace file was not registered")
            binding = opened.capture_binding()
            if (
                binding.is_directory
                or binding.identity != previous.identity
            ):
                raise OwnershipLostError("workspace file identity changed while writing")
            if not self._backend.path_matches_binding(normalized, binding):
                raise OwnershipLostError("workspace file name changed while writing")
            self._children[normalized] = binding
            opened.close()
            return normalized
        except (OSError, OwnershipError) as error:
            raise _cleanup_pipeline_error(error) from error

    def discard_owned_file(self, opened: OwnedPath) -> None:
        """Remove a failed partial write through its retained owned handle."""

        try:
            normalized = self._normalize_child(opened.path)
            previous = self._children.get(normalized)
            if previous is None or previous.is_directory:
                raise OwnershipLostError("workspace file was not registered")
            current = opened.capture_binding()
            if current.is_directory or current.identity != previous.identity:
                raise OwnershipLostError("workspace file identity changed while writing")
            dispose_live_owned_path(opened, current, self._backend)
            del self._children[normalized]
        except (OSError, OwnershipError) as error:
            raise _cleanup_pipeline_error(error) from error

    def discard_registered_file(
        self,
        path: Path,
        *,
        expected_identity: OwnedPathBinding,
        opened: OwnedPath | None = None,
    ) -> None:
        """Delete a registered file only through its same-identity handle.

        A path replacement is never deleted: opening, binding comparison,
        and deletion all occur through one DELETE-capable handle.
        """

        normalized = self._normalize_child(path)
        recorded = self._children.get(normalized)
        if (
            recorded is None
            or recorded.is_directory
            or expected_identity.is_directory
            or recorded.identity != expected_identity.identity
        ):
            raise PipelineError(
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                "workspace reserved file binding is unavailable",
            )
        discard_handle = opened
        try:
            if discard_handle is None:
                discard_handle = self._backend.open_existing_file(
                    normalized,
                    for_delete=True,
                )
            if discard_handle.path != normalized:
                raise OwnershipLostError("workspace discard handle path differs")
            current = discard_handle.capture_binding()
            if (
                current.is_directory
                or current.identity != expected_identity.identity
                or not self._backend.path_matches_binding(normalized, current)
            ):
                raise OwnershipLostError(
                    "workspace reserved file identity differs during discard"
                )
            self._children[normalized] = current
            dispose_live_owned_path(discard_handle, current, self._backend)
            discard_handle = None
            del self._children[normalized]
        except (OSError, OwnershipError) as error:
            raise _cleanup_pipeline_error(error) from error
        finally:
            if discard_handle is not None:
                try:
                    discard_handle.close()
                except (OSError, OwnershipError):
                    pass

    def _validate_recovery_root(self) -> OwnedPath:
        """Open the root with DELETE access before examining its contents."""

        assert self._root_binding is not None
        try:
            opened = self._backend.open_existing_directory(self.path, for_delete=True)
            current = opened.capture_binding()
            if (
                not current.same_identity_and_content(self._root_binding)
                or not self._backend.path_matches_binding(self.path, current)
            ):
                opened.close()
                raise OwnershipLostError("workspace root identity differs")
            return opened
        except (OSError, OwnershipError) as error:
            raise _cleanup_pipeline_error(error) from error

    def _actual_paths(self, directory: Path) -> set[Path]:
        """List entries while the root's no-write/delete handle is held."""

        paths: set[Path] = set()
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as error:
            raise _cleanup_pipeline_error(error) from error
        for entry in entries:
            paths.add(entry)
            try:
                if is_reparse_point(entry):
                    continue
                is_directory = entry.is_dir()
            except (OSError, OwnershipError) as error:
                raise _cleanup_pipeline_error(error) from error
            if is_directory:
                # A junction can report ``is_dir()`` while it points back to
                # an ancestor. The reparse check above must remain before
                # this branch so cleanup never follows it recursively.
                paths.update(self._actual_paths(entry))
        return paths

    def require_exact_inventory(self) -> None:
        """Require every private entry to be a registered, unchanged child.

        This is a non-mutating pre-publication check.  It deliberately uses
        the same no-follow traversal and identity comparison as recovery so a
        caller can stage public *temporary* bytes only after no converter
        sidecar, reparse point, missing child, or pathname replacement remains
        in the workspace.  Recovery repeats the check because an external
        process could still race the interval before context exit.
        """

        try:
            if (
                self._work_root_chain is None
                or self._workspace_root_chain is None
                or self._root_binding is None
            ):
                raise OwnershipLostError("workspace chains are unavailable")
            self._work_root_chain.require_binding()
            self._workspace_root_chain.require_binding()
            actual = self._actual_paths(self.path)
            expected = set(self._children)
            if actual != expected:
                raise OwnershipLostError("workspace inventory differs")
            for path, binding in self._children.items():
                if not binding_matches_path(binding, self._backend):
                    raise OwnershipLostError("workspace child identity differs")
                # ``binding_matches_path`` protects identity; this explicit
                # lexical containment check makes an invalid bookkeeping entry
                # fail before a caller can stage any public temporary.
                self._normalize_child(path)
        except PipelineError:
            raise
        except (OSError, OwnershipError) as error:
            raise _cleanup_pipeline_error(error) from error

    def _recover(self) -> None:
        """Remove all independently safe owned entries before surfacing failure.

        An unregistered converter sidecar is never deleted.  It also must not
        prevent cleanup of a separately owned staged input elsewhere in the
        workspace, so recovery deletes valid files and directories that are
        not ancestors of an unknown or invalid entry before reporting the
        quarantined workspace.
        """

        temporary = self.path
        try:
            if (
                self._work_root_chain is None
                or self._workspace_root_chain is None
                or self._root_binding is None
            ):
                raise OwnershipLostError("workspace chains are unavailable")
            self._work_root_chain.require_binding()
            self._workspace_root_chain.require_binding()
            if (
                temporary.parent != self._work_root_chain.path
                or not temporary.name.startswith(self._prefix)
            ):
                raise OwnershipLostError("workspace root no longer has its bound parent")
            # The work-root chain remains live after this call. Releasing the
            # workspace root chain lets a DELETE-capable handle bind exactly
            # that root for cleanup without giving up ancestor protection.
            self._release_workspace_local_chains()
            root_handle = self._validate_recovery_root()
        except PipelineError:
            self._close_workspace_chains_safely()
            raise
        except (OSError, OwnershipError) as error:
            self._close_workspace_chains_safely()
            raise PipelineError(
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                "private staging workspace recovery path is unavailable",
            ) from error
        failure: PipelineError | None = None

        def record_failure(error: BaseException) -> None:
            nonlocal failure
            if failure is None:
                failure = (
                    error
                    if isinstance(error, PipelineError)
                    else _cleanup_pipeline_error(error)
                )

        owned_directories = {
            binding.path
            for binding in self._children.values()
            if binding.is_directory
        }
        blocked_directories: set[Path] = set()

        def block_parent_directories(path: Path, *, include_path: bool = False) -> None:
            current = path if include_path else path.parent
            while current != temporary:
                if current in owned_directories:
                    blocked_directories.add(current)
                current = current.parent

        try:
            actual = self._actual_paths(temporary)
            expected = set(self._children)
            for path in actual - expected:
                record_failure(
                    PipelineError(
                        ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                        "workspace has unknown or reparse entries",
                    )
                )
                block_parent_directories(path)
            for path in expected - actual:
                record_failure(
                    PipelineError(
                        ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                        "workspace has missing entries",
                    )
                )
                block_parent_directories(
                    path,
                    include_path=path in owned_directories,
                )

            valid_bindings: dict[Path, OwnedPathBinding] = {}
            for path, binding in self._children.items():
                if path not in actual:
                    continue
                try:
                    matches = binding_matches_path(binding, self._backend)
                except (OwnershipError, OSError) as error:
                    record_failure(error)
                    block_parent_directories(
                        path,
                        include_path=binding.is_directory,
                    )
                    continue
                if not matches:
                    record_failure(
                        PipelineError(
                            ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                            "workspace child identity differs",
                        )
                    )
                    block_parent_directories(
                        path,
                        include_path=binding.is_directory,
                    )
                    continue
                valid_bindings[path] = binding

            files = sorted(
                (
                    binding
                    for binding in valid_bindings.values()
                    if not binding.is_directory
                ),
                key=lambda item: len(item.path.parts),
                reverse=True,
            )
            for binding in files:
                try:
                    _retry_cleanup(
                        lambda binding=binding: dispose_owned_binding(
                            binding,
                            self._backend,
                        )
                    )
                except (OwnershipError, OSError) as error:
                    record_failure(error)
                    block_parent_directories(binding.path)

            directories = sorted(
                (
                    binding
                    for binding in valid_bindings.values()
                    if binding.is_directory
                ),
                key=lambda item: len(item.path.parts),
                reverse=True,
            )
            for binding in directories:
                if binding.path in blocked_directories:
                    continue
                try:
                    _retry_cleanup(
                        lambda binding=binding: dispose_owned_binding(
                            binding,
                            self._backend,
                        )
                    )
                except (OwnershipError, OSError) as error:
                    record_failure(error)
                    block_parent_directories(binding.path, include_path=True)

            if failure is None:
                if not root_handle.capture_binding().same_identity_and_content(
                    self._root_binding
                ):
                    record_failure(
                        PipelineError(
                            ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                            "workspace root identity changed during cleanup",
                        )
                    )
                else:
                    try:
                        root_handle.request_delete()
                    except (OwnershipError, OSError) as error:
                        record_failure(error)
        finally:
            try:
                root_handle.close()
            except (OwnershipError, OSError) as error:
                if failure is None:
                    record_failure(error)

        work_root_failure: PipelineError | None = None
        assert self._work_root_chain is not None
        try:
            self._work_root_chain.close()
        except (OwnershipError, OSError) as error:
            work_root_failure = _cleanup_pipeline_error(error)

        if failure is not None:
            raise failure
        if work_root_failure is not None:
            raise work_root_failure
        if self._backend.path_exists(temporary):
            raise PipelineError(
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                "workspace root survived identity-bound deletion",
            )
