"""Windows handle ownership regressions using generated temporary files only."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import ctypes

from liang_pingfa_review.atomic_output import (
    acquire_new_output_target_leases,
    acquire_staged_output_lease,
    publish_new_artifacts,
    publish_no_replace,
    stage_publication_transaction,
)
from liang_pingfa_review.canonical import write_new_text
from liang_pingfa_review.errors import ErrorCode, PipelineError
from liang_pingfa_review.ownership import (
    DestinationExistsError,
    FileIdentity,
    OwnedPathBinding,
    OwnershipCleanupError,
    OwnershipLostError,
    WindowsFileOwnershipBackend,
    WindowsOwnedPath,
    create_private_directory,
    create_private_workspace_directory,
    private_input_trusted_owner_sids,
    secure_private_staging_directory,
    verify_private_staging_file,
    validate_private_input_owner,
)
import liang_pingfa_review.ownership as ownership
from liang_pingfa_review.temporary import (
    PrivateWorkspace,
    recover_publication_temporary,
)


def _synthetic_windows_path(name: str) -> Path:
    """Build an API-shape-only Windows pathname without a local path literal."""

    separator = chr(92)
    return Path("C" + chr(58) + separator + "generated" + separator + name)


class PrivateInputOwnerTests(unittest.TestCase):
    """Exercise owner policy and retained-handle drift with generated SIDs."""

    _user = "S-1-5-21-100"
    _system = "S-1-5-18"

    def test_private_input_owner_set_is_only_current_user_and_system(self) -> None:
        self.assertEqual(
            private_input_trusted_owner_sids(self._user),
            frozenset({self._user, self._system}),
        )
        for owner in (
            "S-1-5-32-545",  # Builtin Users
            "S-1-1-0",  # Everyone
            "S-1-5-11",  # Authenticated Users
            "S-1-5-32-544",  # Administrators
            "S-1-5-80-123-456-789-1011-1213",  # arbitrary service
            "S-1-5-21-999",
        ):
            with self.subTest(owner=owner), self.assertRaises(OwnershipCleanupError):
                validate_private_input_owner(owner, user_sid=self._user)
        validate_private_input_owner(self._user, user_sid=self._user)
        validate_private_input_owner(self._system, user_sid=self._user)

    def test_administrators_owner_requires_the_token_default_owner_policy(self) -> None:
        administrators = "S-1-5-32-544"
        with mock.patch.object(
            ownership,
            "_current_token_owner_sid",
            return_value=administrators,
        ):
            self.assertIn(
                administrators,
                private_input_trusted_owner_sids(
                    self._user,
                    allow_administrators_if_token_owner=True,
                ),
            )
        with self.assertRaises(OwnershipCleanupError):
            validate_private_input_owner(administrators, user_sid=self._user)

    def test_safe_dacl_cannot_rescue_untrusted_or_drifting_handle_owner(self) -> None:
        dacl = "D:P(A;;FA;;;SY)(A;;FA;;;S-1-5-21-100)"
        with (
            mock.patch.object(
                ownership,
                "_dacl_sddl_for_handle",
                return_value=dacl,
            ),
            mock.patch.object(
                ownership,
                "_expected_private_dacl_principal",
                return_value=self._user,
            ),
            mock.patch.object(
                ownership,
                "_owner_sid_for_handle",
                return_value="S-1-5-32-545",
            ),
            self.assertRaises(OwnershipCleanupError),
        ):
            ownership._verify_private_staging_dacl_on_handle(7, self._user)

        with (
            mock.patch.object(
                ownership,
                "_dacl_sddl_for_handle",
                return_value=dacl,
            ),
            mock.patch.object(
                ownership,
                "_expected_private_dacl_principal",
                return_value=self._user,
            ),
            mock.patch.object(
                ownership,
                "_owner_sid_for_handle",
                side_effect=(self._user, self._system),
            ),
            self.assertRaises(OwnershipCleanupError),
        ):
            ownership._verify_private_staging_dacl_on_handle(7, self._user)


class _RecordingKernelApi:
    """Minimal mock kernel32 boundary for platform-neutral buffer assertions."""

    def __init__(
        self,
        *,
        rename_error: int = 0,
        final_path_name: str | None = None,
    ) -> None:
        self.rename_error = rename_error
        self.final_path_name = final_path_name
        self.calls: list[tuple[int, int, bytes, int]] = []

    def create_file(
        self,
        path: str,
        desired_access: int,
        share_mode: int,
        creation_disposition: int,
        flags_and_attributes: int,
    ) -> int:
        raise AssertionError("this test constructs an already-open handle")

    def close_handle(self, handle: int) -> bool:
        return True

    def get_file_information(self, handle: int) -> object | None:
        return None

    def get_file_size(self, handle: int) -> int | None:
        return None

    def get_final_path_name(self, handle: int) -> str | None:
        del handle
        return self.final_path_name

    def set_file_information(
        self,
        handle: int,
        information_class: int,
        information: object,
        information_size: int,
    ) -> bool:
        self.calls.append(
            (
                handle,
                information_class,
                bytes(information),  # type: ignore[arg-type]
                information_size,
            )
        )
        return not (information_class == 3 and self.rename_error)

    def last_error(self) -> int:
        return self.rename_error


class _RecordingPrivateDirectoryApi:
    """Mock CreateDirectoryW boundary that exposes security attributes."""

    def __init__(
        self,
        *,
        created: bool = True,
        error_number: int = 0,
        after_create: object | None = None,
    ) -> None:
        self.created = created
        self.error_number = error_number
        self.after_create = after_create
        self.calls: list[tuple[str, object]] = []

    def create_directory(self, path: str, attributes: object) -> bool:
        self.calls.append((path, attributes))
        if self.created and callable(self.after_create):
            self.after_create(attributes)
        return self.created

    def last_error(self) -> int:
        return self.error_number


class _AtomicDirectoryOwnedPath:
    """Generated retained directory handle used by atomic-create tests."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._binding = OwnedPathBinding(
            path=path,
            identity=FileIdentity("generated-directory", 19, 23, 29),
            byte_size=None,
            sha256=None,
            is_directory=True,
        )
        self.delete_requested = False
        self.closed = False

    def capture_binding(self) -> OwnedPathBinding:
        return self._binding

    def final_path(self) -> Path:
        return self.path

    def request_delete(self) -> None:
        self.delete_requested = True

    def close(self) -> None:
        self.closed = True


class WindowsApiShapeTests(unittest.TestCase):
    """Exercise the isolated ctypes contract without calling Windows."""

    def test_rename_uses_handle_info_and_never_sets_replace(self) -> None:
        api = _RecordingKernelApi()
        backend = WindowsFileOwnershipBackend(api=api)
        opened = WindowsOwnedPath(
            backend,
            _synthetic_windows_path("publication.tmp"),
            101,
            None,
            is_directory=False,
        )
        opened.rename_no_replace(_synthetic_windows_path("final.dwg"))
        self.assertEqual(len(api.calls), 1)
        handle, information_class, payload, size = api.calls[0]
        self.assertEqual(handle, 101)
        self.assertEqual(information_class, 3)  # FileRenameInfo
        self.assertEqual(payload[0], 0)  # ReplaceIfExists is explicitly false.
        self.assertGreater(size, len("final.dwg".encode("utf-16-le")))

    def test_rename_maps_an_atomic_destination_race(self) -> None:
        api = _RecordingKernelApi(rename_error=183)
        backend = WindowsFileOwnershipBackend(api=api)
        opened = WindowsOwnedPath(
            backend,
            _synthetic_windows_path("publication.tmp"),
            102,
            None,
            is_directory=False,
        )
        with self.assertRaises(DestinationExistsError):
            opened.rename_no_replace(_synthetic_windows_path("final.dwg"))
        self.assertEqual(api.calls[0][1], 3)
        self.assertEqual(api.calls[0][2][0], 0)

    def test_disposition_targets_the_opened_handle(self) -> None:
        api = _RecordingKernelApi()
        backend = WindowsFileOwnershipBackend(api=api)
        opened = WindowsOwnedPath(
            backend,
            _synthetic_windows_path("owned.tmp"),
            103,
            None,
            is_directory=False,
        )
        opened.request_delete()
        self.assertEqual(api.calls, [(103, 4, b"\x01", 1)])

    def test_final_path_comes_from_the_open_handle(self) -> None:
        separator = chr(92)
        api = _RecordingKernelApi(
            final_path_name=(
                separator * 2
                + "?"
                + separator
                + "C"
                + chr(58)
                + separator
                + "generated"
                + separator
                + "bound-parent"
            )
        )
        backend = WindowsFileOwnershipBackend(api=api)
        opened = WindowsOwnedPath(
            backend,
            _synthetic_windows_path("lexical-parent"),
            104,
            None,
            is_directory=True,
        )
        self.assertEqual(
            opened.final_path(),
            _synthetic_windows_path("bound-parent"),
        )


class WindowsAtomicPrivateDirectoryTests(unittest.TestCase):
    """Exercise the CreateDirectoryW security-attribute boundary off Windows."""

    _user = "S-1-5-21-100"
    _other_user = "S-1-5-21-200"

    @staticmethod
    def _attributes() -> object:
        attributes = ownership._SecurityAttributes()
        attributes.nLength = ctypes.sizeof(ownership._SecurityAttributes)
        attributes.lpSecurityDescriptor = ctypes.c_void_p(123)
        attributes.bInheritHandle = 0
        return attributes

    def _backend(
        self,
        api: _RecordingPrivateDirectoryApi,
        opened: _AtomicDirectoryOwnedPath,
    ) -> WindowsFileOwnershipBackend:
        backend = WindowsFileOwnershipBackend(api=api)  # type: ignore[arg-type]
        backend.open_existing_directory = mock.Mock(return_value=opened)  # type: ignore[method-assign]
        return backend

    def test_create_directory_supplies_private_security_before_exposure_callback(self) -> None:
        """An observer at creation sees a descriptor, never a bare directory."""

        exposure_without_dacl: list[object] = []

        def exposure_callback(attributes: object) -> None:
            if (
                not isinstance(attributes, ownership._SecurityAttributes)
                or attributes.nLength != ctypes.sizeof(ownership._SecurityAttributes)
                or not attributes.lpSecurityDescriptor
                or attributes.bInheritHandle
            ):
                exposure_without_dacl.append(attributes)

        path = _synthetic_windows_path("atomic-private")
        api = _RecordingPrivateDirectoryApi(after_create=exposure_callback)
        opened = _AtomicDirectoryOwnedPath(path)
        backend = self._backend(api, opened)
        with (
            mock.patch.object(ownership, "_current_user_sid", return_value=self._user),
            mock.patch.object(
                ownership,
                "_private_directory_security_attributes",
                return_value=(self._attributes(), ctypes.c_void_p(123)),
            ),
            mock.patch.object(ownership, "_free_private_directory_security_descriptor"),
            mock.patch.object(
                ownership,
                "verify_private_staging_file",
                return_value=self._user,
            ),
        ):
            creation = create_private_directory(path, backend)
        try:
            self.assertEqual([call_path for call_path, _ in api.calls], [str(path)])
            self.assertEqual(exposure_without_dacl, [])
            self.assertFalse(opened.closed)
        finally:
            creation.dispose(backend)
        self.assertTrue(opened.delete_requested)
        self.assertTrue(opened.closed)

    def test_collision_retries_only_the_bounded_atomic_create(self) -> None:
        marker = object()
        with (
            mock.patch.object(
                ownership.secrets,
                "token_hex",
                side_effect=("a" * 32, "b" * 32),
            ),
            mock.patch.object(
                ownership,
                "create_private_directory",
                side_effect=(DestinationExistsError("collision"), marker),
            ) as created,
        ):
            result = create_private_workspace_directory(
                _synthetic_windows_path("generated"),
                "workspace-",
                mock.Mock(),
            )
        self.assertIs(result, marker)
        self.assertEqual(created.call_count, 2)
        self.assertTrue(str(created.call_args_list[0].args[0]).endswith("workspace-" + "a" * 32))
        self.assertTrue(str(created.call_args_list[1].args[0]).endswith("workspace-" + "b" * 32))

    def test_descriptor_or_create_failure_never_opens_an_unprotected_directory(self) -> None:
        path = _synthetic_windows_path("atomic-failure")
        opened = _AtomicDirectoryOwnedPath(path)
        descriptor_api = _RecordingPrivateDirectoryApi()
        descriptor_backend = self._backend(descriptor_api, opened)
        with mock.patch.object(
            ownership,
            "_private_directory_security_attributes",
            side_effect=OwnershipCleanupError("synthetic descriptor failure"),
        ):
            with self.assertRaises(OwnershipCleanupError):
                create_private_directory(path, descriptor_backend)
        self.assertEqual(descriptor_api.calls, [])
        descriptor_backend.open_existing_directory.assert_not_called()

        create_api = _RecordingPrivateDirectoryApi(created=False, error_number=5)
        create_backend = self._backend(create_api, _AtomicDirectoryOwnedPath(path))
        with (
            mock.patch.object(ownership, "_current_user_sid", return_value=self._user),
            mock.patch.object(
                ownership,
                "_private_directory_security_attributes",
                return_value=(self._attributes(), ctypes.c_void_p(123)),
            ),
            mock.patch.object(ownership, "_free_private_directory_security_descriptor"),
        ):
            with self.assertRaises(OwnershipCleanupError):
                create_private_directory(path, create_backend)
        self.assertEqual(len(create_api.calls), 1)
        create_backend.open_existing_directory.assert_not_called()

    def test_non_windows_creator_requires_an_explicit_safe_backend_capability(self) -> None:
        """No generic mkdir fallback is available to an arbitrary backend."""

        class UnsafeBackend:
            pass

        with self.assertRaises(OwnershipCleanupError):
            create_private_directory(
                _synthetic_windows_path("unsafe-fallback"),
                UnsafeBackend(),  # type: ignore[arg-type]
            )

    def test_post_creation_validation_failure_deletes_the_bound_empty_directory(self) -> None:
        path = _synthetic_windows_path("atomic-cleanup")
        opened = _AtomicDirectoryOwnedPath(path)
        api = _RecordingPrivateDirectoryApi()
        backend = self._backend(api, opened)
        with (
            mock.patch.object(ownership, "_current_user_sid", return_value=self._user),
            mock.patch.object(
                ownership,
                "_private_directory_security_attributes",
                return_value=(self._attributes(), ctypes.c_void_p(123)),
            ),
            mock.patch.object(ownership, "_free_private_directory_security_descriptor"),
            mock.patch.object(
                ownership,
                "verify_private_staging_file",
                side_effect=OwnershipCleanupError("synthetic readback failure"),
            ),
            self.assertRaises(OwnershipCleanupError),
        ):
            create_private_directory(path, backend)
        self.assertEqual(len(api.calls), 1)
        self.assertTrue(opened.delete_requested)
        self.assertTrue(opened.closed)

    def test_policy_rejects_a_broad_temp_parent_and_second_user(self) -> None:
        """The creation descriptor never inherits a broad parent or other SID."""

        sddl = ownership._private_directory_sddl(self._user)
        dacl = sddl[sddl.index("D:") :]
        self.assertEqual(
            dacl,
            f"D:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;{self._user})",
        )
        self.assertIn(f"O:{self._user}", sddl)
        self.assertNotIn(self._other_user, sddl)
        self.assertTrue(ownership._private_staging_dacl_is_exact(dacl, self._user))
        self.assertFalse(
            ownership._private_staging_dacl_is_exact(
                dacl + f"(A;OICI;FA;;;{self._other_user})",
                self._user,
            )
        )


class _SwapAfterCheckOwnedPath:
    """Synthetic handle that models a replacement after identity comparison."""

    def __init__(
        self,
        path: Path,
        binding: OwnedPathBinding,
        replacement: bytes,
    ) -> None:
        self.path = path
        self._binding = binding
        self._replacement = replacement

    def copy_from(self, source: Path) -> None:
        raise AssertionError("not used")

    def write_bytes(self, payload: bytes) -> None:
        raise AssertionError("not used")

    def read_prefix(self, length: int) -> bytes:
        raise AssertionError("not used")

    def capture_binding(self) -> OwnedPathBinding:
        return self._binding

    def rename_no_replace(self, destination: Path) -> None:
        raise AssertionError("not used")

    def request_delete(self) -> None:
        # A pathname-based unlink would now remove this replacement. A real
        # Windows handle deletes the originally opened object instead; model
        # that outcome and leave the replacement at the public path.
        self.path.unlink()
        self.path.write_bytes(self._replacement)

    def close(self) -> None:
        return None


class _SwapAfterCheckBackend:
    """Synthetic backend deliberately violating sharing to test postconditions."""

    def __init__(
        self,
        path: Path,
        binding: OwnedPathBinding,
        replacement: bytes,
    ) -> None:
        self.path = path
        self.binding = binding
        self.replacement = replacement

    def create_new_file(self, path: Path) -> object:
        raise AssertionError("not used")

    def open_existing_file(self, path: Path, *, for_delete: bool) -> _SwapAfterCheckOwnedPath:
        self.assert_path(path)
        return _SwapAfterCheckOwnedPath(path, self.binding, self.replacement)

    def open_existing_directory(self, path: Path, *, for_delete: bool) -> object:
        raise AssertionError("not used")

    def path_exists(self, path: Path) -> bool:
        return os.path.lexists(path)

    def path_matches_binding(self, path: Path, binding: OwnedPathBinding) -> bool:
        return path == binding.path

    def assert_path(self, path: Path) -> None:
        if path != self.path:
            raise AssertionError("unexpected path")


class OwnershipCleanupRaceTests(unittest.TestCase):
    """Ensure cleanup never deletes a pathname replacement."""

    def test_synthetic_swap_between_check_and_cleanup_survives(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            temporary = root / f".liang-pingfa-publish-{'a' * 32}.tmp"
            temporary.write_bytes(b"original-generated-content")
            replacement = b"replacement-owned-by-another-writer"
            binding = OwnedPathBinding(
                path=temporary,
                identity=FileIdentity("synthetic", 1, 2, 3),
                byte_size=len(b"original-generated-content"),
                sha256="a" * 64,
                is_directory=False,
            )
            backend = _SwapAfterCheckBackend(temporary, binding, replacement)
            with self.assertRaises(PipelineError) as raised:
                recover_publication_temporary(
                    temporary,
                    root,
                    binding=binding,
                    backend=backend,  # type: ignore[arg-type]
                )
            self.assertEqual(
                raised.exception.code,
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
            )
            self.assertEqual(temporary.read_bytes(), replacement)

    def test_workspace_replacement_survives_and_cleanup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace_path: Path | None = None
            replacement = b"unrelated-workspace-replacement"
            with self.assertRaises(PipelineError) as raised:
                with PrivateWorkspace(
                    prefix="liang-pingfa-workspace-race-",
                    directory=root,
                ) as workspace:
                    workspace_path = workspace.path
                    owned = workspace / "owned.bin"
                    owned.write_bytes(b"owned-generated-content")
                    workspace.track_created_file(owned)
                    owned.unlink()
                    owned.write_bytes(replacement)
            self.assertEqual(
                raised.exception.code,
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
            )
            assert workspace_path is not None
            self.assertEqual(
                (workspace_path / "owned.bin").read_bytes(),
                replacement,
            )
            shutil.rmtree(workspace_path)

    def test_unknown_workspace_child_survives_and_cleanup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace_path: Path | None = None
            with self.assertRaises(PipelineError) as raised:
                with PrivateWorkspace(
                    prefix="liang-pingfa-workspace-unknown-",
                    directory=root,
                ) as workspace:
                    workspace_path = workspace.path
                    known = workspace / "known.bin"
                    known.write_bytes(b"known-generated-content")
                    workspace.track_created_file(known)
                    unknown = workspace / "untracked.bin"
                    unknown.write_bytes(b"unrelated-generated-content")
            self.assertEqual(
                raised.exception.code,
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
            )
            assert workspace_path is not None
            self.assertEqual(
                (workspace_path / "untracked.bin").read_bytes(),
                b"unrelated-generated-content",
            )
            shutil.rmtree(workspace_path)


@unittest.skipUnless(os.name == "nt", "Windows handle integration only")
class WindowsHandleIntegrationTests(unittest.TestCase):
    """Run only against generated text/binary files in a temporary directory."""

    def test_temp_swap_is_denied_or_detected_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            staged = root / "staged.bin"
            destination = root / "output.bin"
            replacement = root / "replacement.bin"
            staged.write_bytes(b"verified-generated-content")
            replacement.write_bytes(b"unrelated-replacement")
            swap_error: OSError | None = None

            def attempt_swap() -> None:
                nonlocal swap_error
                temporary = next(root.glob(".liang-pingfa-publish-*.tmp"))
                try:
                    replacement.replace(temporary)
                except OSError as error:
                    swap_error = error

            try:
                publish_no_replace(
                    staged,
                    destination,
                    before_commit=attempt_swap,
                )
            except PipelineError as error:
                # If a platform unexpectedly permits the swap, the held-handle
                # identity check must still prevent publication.
                self.assertIsNone(swap_error)
                self.assertIn(
                    error.code,
                    {
                        ErrorCode.ATOMIC_PUBLISH_FAILED,
                        ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                    },
                )
                self.assertFalse(destination.exists())
                self.assertTrue(
                    any(root.glob(".liang-pingfa-publish-*.tmp"))
                    or not replacement.exists()
                )
            else:
                self.assertIsNotNone(swap_error)
                self.assertEqual(destination.read_bytes(), staged.read_bytes())

    def test_private_staging_dacl_is_applied_and_read_back(self) -> None:
        """Generated converter roots require verified current-session DACLs."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            secure_private_staging_directory(
                root,
                WindowsFileOwnershipBackend(),
            )
            self.assertTrue(root.is_dir())

    def test_workspace_creation_is_private_before_children_and_cleans_up(self) -> None:
        """A broad system TEMP parent cannot weaken the atomically private child."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace_path: Path | None = None
            with PrivateWorkspace(
                prefix="liang-pingfa-atomic-workspace-",
                directory=root,
            ) as workspace:
                workspace_path = workspace.path
                assert workspace._workspace_root_chain is not None
                owner = verify_private_staging_file(
                    workspace._workspace_root_chain.owned,
                    workspace.backend,
                )
                self.assertEqual(owner, ownership.current_user_sid())
                # The verified root is still empty at this point: a child is
                # created only after its exact private root binding exists.
                self.assertEqual(list(workspace.path.iterdir()), [])
            assert workspace_path is not None
            self.assertFalse(workspace_path.exists())

    def test_staged_swap_before_copy_is_rejected_against_its_audited_lease(self) -> None:
        """A staged pathname replacement cannot satisfy a prior held binding."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            staged = root / "staged.dwg"
            replacement = root / "replacement.dwg"
            destination = root / "output.dwg"
            staged.write_bytes(b"AC1032verified-staged-output")
            replacement.write_bytes(b"AC1032unverified-replacement")

            baseline = acquire_staged_output_lease(staged)
            expectation = baseline.expectation
            baseline.close()
            replacement.replace(staged)

            with self.assertRaises(PipelineError) as raised:
                acquire_staged_output_lease(staged, expectation=expectation)
            self.assertEqual(raised.exception.code, ErrorCode.RE_AUDIT_MISMATCH)
            self.assertFalse(destination.exists())

    def test_staged_write_during_held_copy_is_denied_or_detected(self) -> None:
        """A mutation attempted while copy reads the held staged handle is safe."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            staged = root / "staged.dwg"
            destination = root / "output.dwg"
            payload = b"AC1032verified-staged-output"
            staged.write_bytes(payload)
            lease = acquire_staged_output_lease(staged)
            original_read_chunks = WindowsOwnedPath.read_chunks
            write_error: OSError | None = None

            def attempt_mutation(
                opened: WindowsOwnedPath,
                chunk_size: int = 1024 * 1024,
            ) -> object:
                nonlocal write_error
                for chunk in original_read_chunks(opened, chunk_size):
                    if write_error is None:
                        try:
                            staged.write_bytes(b"AC1032concurrent-unverified-output")
                        except OSError as error:
                            write_error = error
                    yield chunk

            with mock.patch.object(
                WindowsOwnedPath,
                "read_chunks",
                side_effect=attempt_mutation,
                autospec=True,
            ):
                try:
                    publish_no_replace(lease, destination)
                except PipelineError as error:
                    self.assertEqual(error.code, ErrorCode.RE_AUDIT_MISMATCH)
                    self.assertFalse(destination.exists())
                else:
                    self.assertIsNotNone(write_error)
                    self.assertEqual(destination.read_bytes(), payload)

    def test_staged_change_after_copy_before_commit_publishes_nothing(self) -> None:
        """The required pre-rename lease recheck aborts without a final DWG."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            staged = root / "staged.dwg"
            destination = root / "output.dwg"
            staged.write_bytes(b"AC1032verified-staged-output")
            lease = acquire_staged_output_lease(staged)
            changed = PipelineError(
                ErrorCode.RE_AUDIT_MISMATCH,
                "synthetic staged mutation after copy",
            )
            with mock.patch.object(
                lease,
                "require_binding",
                side_effect=(None, changed),
            ):
                with self.assertRaises(PipelineError) as raised:
                    publish_no_replace(lease, destination)
            self.assertEqual(raised.exception.code, ErrorCode.RE_AUDIT_MISMATCH)
            self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob(".liang-pingfa-publish-*.tmp")), [])

    def test_staged_change_after_commit_callback_rolls_back_final_output(self) -> None:
        """A post-callback source-binding loss cannot leave a final output."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            staged = root / "staged.dwg"
            destination = root / "output.dwg"
            staged.write_bytes(b"AC1032verified-staged-output")
            lease = acquire_staged_output_lease(staged)
            callback_events: list[str] = []
            changed = PipelineError(
                ErrorCode.RE_AUDIT_MISMATCH,
                "synthetic staged mutation after commit callback",
            )
            with mock.patch.object(
                lease,
                "require_binding",
                side_effect=(None, None, changed),
            ):
                with self.assertRaises(PipelineError) as raised:
                    publish_no_replace(
                        lease,
                        destination,
                        after_commit=lambda _opened, _binding: callback_events.append(
                            "called"
                        ),
                    )
            self.assertEqual(raised.exception.code, ErrorCode.RE_AUDIT_MISMATCH)
            self.assertEqual(callback_events, ["called"])
            self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob(".liang-pingfa-publish-*.tmp")), [])

    def test_destination_race_preserves_source_and_other_writer_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            staged = root / "staged.bin"
            destination = root / "output.bin"
            source_bytes = b"verified-generated-content"
            destination_bytes = b"other-writer-content"
            staged.write_bytes(source_bytes)
            with self.assertRaises(PipelineError) as raised:
                publish_no_replace(
                    staged,
                    destination,
                    before_commit=lambda: destination.write_bytes(destination_bytes),
                )
            self.assertEqual(raised.exception.code, ErrorCode.OUTPUT_EXISTS)
            self.assertEqual(staged.read_bytes(), source_bytes)
            self.assertEqual(destination.read_bytes(), destination_bytes)
            self.assertEqual(list(root.glob(".liang-pingfa-publish-*.tmp")), [])

    def test_normal_workspace_cleanup_leaves_no_generated_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with PrivateWorkspace(
                prefix="liang-pingfa-normal-cleanup-",
                directory=root,
            ) as workspace:
                owned = workspace / "generated.bin"
                owned.write_bytes(b"generated-only")
                workspace.track_created_file(owned)
            self.assertEqual(
                list(root.glob("liang-pingfa-normal-cleanup-*")),
                [],
            )

    def test_junction_to_workspace_ancestor_is_quarantined_without_recursion(self) -> None:
        """A junction cannot turn cleanup into recursive deletion or a loop."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace_path: Path | None = None
            junction: Path | None = None
            started = time.monotonic()
            try:
                with self.assertRaises(PipelineError) as raised:
                    with PrivateWorkspace(
                        prefix="liang-pingfa-junction-",
                        directory=root,
                    ) as workspace:
                        workspace_path = workspace.path
                        safe_directory = workspace.create_owned_directory(
                            workspace / "safe"
                        )
                        owned = workspace.create_owned_file(
                            safe_directory / "source-copy.dwg"
                        )
                        owned.write_bytes(b"AC1032separately-owned-source-copy")
                        workspace.seal_owned_file(owned)
                        junction = workspace / "ancestor-junction"
                        created = subprocess.run(
                            [
                                os.environ.get("ComSpec", "cmd.exe"),
                                "/d",
                                "/c",
                                "mklink",
                                "/J",
                                str(junction),
                                str(workspace.path),
                            ],
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        if created.returncode != 0:
                            self.skipTest("junction creation lacks permission")
                self.assertEqual(
                    raised.exception.code,
                    ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                )
                self.assertLess(time.monotonic() - started, 5.0)
                assert workspace_path is not None
                assert junction is not None
                self.assertFalse((workspace_path / "safe" / "source-copy.dwg").exists())
                self.assertTrue(os.path.lexists(junction))
            finally:
                if junction is not None and os.path.lexists(junction):
                    junction.rmdir()
                if workspace_path is not None and workspace_path.exists():
                    workspace_path.rmdir()

    def test_output_parent_junction_swap_is_denied_or_detected(self) -> None:
        """A retained parent lease cannot redirect public bytes through a junction."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            staged = root / "staged.bin"
            output_parent = root / "bound-output"
            moved_parent = root / "moved-output"
            external_parent = root / "external-output"
            destination = output_parent / "published.bin"
            staged.write_bytes(b"verified-generated-content")
            output_parent.mkdir()
            external_parent.mkdir()
            leases = acquire_new_output_target_leases((destination,))
            parent_replaced = False
            try:
                try:
                    output_parent.replace(moved_parent)
                    parent_replaced = True
                except OSError:
                    # The expected native result: the directory handle omits
                    # DELETE sharing, so the rename never starts.
                    publish_no_replace(
                        staged,
                        destination,
                        output_target=leases.targets[0],
                    )
                    self.assertEqual(
                        destination.read_bytes(),
                        b"verified-generated-content",
                    )
                    self.assertFalse((external_parent / destination.name).exists())
                    return

                linked = subprocess.run(
                    [
                        os.environ.get("ComSpec", "cmd.exe"),
                        "/d",
                        "/c",
                        "mklink",
                        "/J",
                        str(output_parent),
                        str(external_parent),
                    ],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if linked.returncode != 0:
                    self.skipTest("junction creation lacks permission")
                with self.assertRaises(PipelineError):
                    publish_no_replace(
                        staged,
                        destination,
                        output_target=leases.targets[0],
                    )
                self.assertFalse((external_parent / destination.name).exists())
                self.assertFalse((moved_parent / destination.name).exists())
            finally:
                try:
                    leases.close()
                finally:
                    if parent_replaced:
                        if os.path.lexists(output_parent):
                            output_parent.rmdir()
                        if moved_parent.exists():
                            moved_parent.replace(output_parent)

    def test_lexical_parent_swap_after_no_follow_open_never_writes_external(self) -> None:
        """A junction race after CreateFileW is denied or rejected before use."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            staged = root / "staged.bin"
            lexical_parent = root / "lexical-output"
            moved_parent = root / "moved-output"
            external_parent = root / "external-output"
            destination = lexical_parent / "published.bin"
            staged.write_bytes(b"verified-generated-content")
            lexical_parent.mkdir()
            external_parent.mkdir()
            original_open = WindowsFileOwnershipBackend.open_output_parent_directory
            rename_error: OSError | None = None
            parent_moved = False

            def open_then_race(
                backend: WindowsFileOwnershipBackend,
                path: Path,
            ) -> WindowsOwnedPath:
                nonlocal rename_error, parent_moved
                opened = original_open(backend, path)
                if path == lexical_parent:
                    try:
                        lexical_parent.replace(moved_parent)
                        parent_moved = True
                    except OSError as error:
                        rename_error = error
                    else:
                        subprocess.Popen(
                            [
                                os.environ.get("ComSpec", "cmd.exe"),
                                "/d",
                                "/c",
                                "mklink",
                                "/J",
                                str(lexical_parent),
                                str(external_parent),
                            ],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        ).wait()
                return opened

            try:
                with mock.patch.object(
                    WindowsFileOwnershipBackend,
                    "open_output_parent_directory",
                    side_effect=open_then_race,
                    autospec=True,
                ):
                    if rename_error is not None:
                        self.fail("race state was checked before the no-follow open")
                    try:
                        leases = acquire_new_output_target_leases((destination,))
                    except PipelineError:
                        # A permitted synthetic/unusual rename must be
                        # detected from the still-held lexical binding.
                        self.assertTrue(parent_moved)
                    else:
                        try:
                            self.assertIsNotNone(rename_error)
                            publish_no_replace(
                                staged,
                                destination,
                                output_target=leases.targets[0],
                            )
                            self.assertEqual(
                                destination.read_bytes(),
                                b"verified-generated-content",
                            )
                        finally:
                            leases.close()
                self.assertFalse((external_parent / destination.name).exists())
                self.assertFalse((moved_parent / destination.name).exists())
            finally:
                if destination.exists():
                    destination.unlink()
                if os.path.lexists(lexical_parent):
                    lexical_parent.rmdir()
                if parent_moved and moved_parent.exists():
                    moved_parent.replace(lexical_parent)

    def test_ancestor_junction_rejects_output_and_workspace_before_writes(self) -> None:
        """A normal child below a grandparent junction is never authoritative."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            external_root = root / "external-root"
            nested_parent = external_root / "normal-child" / "output"
            external_root.mkdir()
            nested_parent.mkdir(parents=True)
            junction = root / "ancestor-junction"
            staged = root / "staged.bin"
            staged.write_bytes(b"verified-generated-content")
            created = subprocess.run(
                [
                    os.environ.get("ComSpec", "cmd.exe"),
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    str(junction),
                    str(external_root),
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if created.returncode != 0:
                self.skipTest("junction creation lacks permission")
            try:
                destination = junction / "normal-child" / "output" / "published.bin"
                with self.assertRaises(PipelineError) as raised:
                    acquire_new_output_target_leases((destination,))
                self.assertEqual(raised.exception.code, ErrorCode.INVALID_ARGUMENT)
                self.assertFalse((nested_parent / "published.bin").exists())

                with self.assertRaises(PipelineError) as raised:
                    with PrivateWorkspace(
                        prefix="liang-pingfa-ancestor-workspace-",
                        directory=junction / "normal-child" / "output",
                    ):
                        self.fail("workspace must reject its lexical junction ancestor")
                self.assertEqual(raised.exception.code, ErrorCode.CONVERSION_FAILURE)
                self.assertEqual(list(nested_parent.iterdir()), [])
            finally:
                if os.path.lexists(junction):
                    junction.rmdir()

    def test_ancestor_replacement_is_denied_or_aborts_publication(self) -> None:
        """Retained ancestor handles prevent replacement above a normal parent."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            staged = root / "staged.bin"
            ancestor = root / "retained-ancestor"
            output_parent = ancestor / "normal-child"
            moved_ancestor = root / "moved-ancestor"
            external_root = root / "external-root"
            external_parent = external_root / "normal-child"
            staged.write_bytes(b"verified-generated-content")
            output_parent.mkdir(parents=True)
            external_parent.mkdir(parents=True)
            destination = output_parent / "published.bin"
            leases = acquire_new_output_target_leases((destination,))
            moved = False
            try:
                try:
                    ancestor.replace(moved_ancestor)
                    moved = True
                except OSError:
                    publish_no_replace(
                        staged,
                        destination,
                        output_target=leases.targets[0],
                    )
                    self.assertEqual(
                        destination.read_bytes(),
                        b"verified-generated-content",
                    )
                    self.assertFalse((external_parent / destination.name).exists())
                    return

                linked = subprocess.Popen(
                    [
                        os.environ.get("ComSpec", "cmd.exe"),
                        "/d",
                        "/c",
                        "mklink",
                        "/J",
                        str(ancestor),
                        str(external_root),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).wait()
                if linked != 0:
                    self.skipTest("junction creation lacks permission")
                with self.assertRaises(PipelineError):
                    publish_no_replace(
                        staged,
                        destination,
                        output_target=leases.targets[0],
                    )
                self.assertFalse((external_parent / destination.name).exists())
                self.assertFalse(
                    (moved_ancestor / "normal-child" / destination.name).exists()
                )
            finally:
                try:
                    leases.close()
                finally:
                    if os.path.lexists(ancestor):
                        is_junction = getattr(
                            os.path,
                            "isjunction",
                            lambda _path: False,
                        )
                        if ancestor.is_symlink() or is_junction(ancestor):
                            ancestor.rmdir()
                        else:
                            published = ancestor / "normal-child" / destination.name
                            if published.exists():
                                published.unlink()
                            child = ancestor / "normal-child"
                            if child.exists():
                                child.rmdir()
                            ancestor.rmdir()
                    if moved and moved_ancestor.exists():
                        moved_ancestor.replace(ancestor)

    def test_junction_output_parent_is_rejected_before_publication(self) -> None:
        """A pre-existing junction is never accepted as a public parent."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            external_parent = root / "external-parent"
            junction_parent = root / "junction-parent"
            external_parent.mkdir()
            created = subprocess.run(
                [
                    os.environ.get("ComSpec", "cmd.exe"),
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    str(junction_parent),
                    str(external_parent),
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if created.returncode != 0:
                self.skipTest("junction creation lacks permission")
            try:
                with self.assertRaises(PipelineError) as raised:
                    acquire_new_output_target_leases(
                        (junction_parent / "must-not-exist.json",)
                    )
                self.assertEqual(raised.exception.code, ErrorCode.INVALID_ARGUMENT)
                self.assertFalse(
                    (external_parent / "must-not-exist.json").exists()
                )
            finally:
                if os.path.lexists(junction_parent):
                    junction_parent.rmdir()

    def test_partial_artifact_writes_leave_no_public_file(self) -> None:
        """Every public text/JSON artifact cleans an interrupted owned write."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            original_write = WindowsOwnedPath.write_bytes

            def write_one_byte_then_fail(
                opened: WindowsOwnedPath,
                payload: bytes,
            ) -> None:
                original_write(opened, payload[:1])
                raise OSError("synthetic artifact write failure")

            with mock.patch.object(
                WindowsOwnedPath,
                "write_bytes",
                side_effect=write_one_byte_then_fail,
                autospec=True,
            ):
                for name in (
                    "audit.md",
                    "audit.json",
                    "plan.json",
                    "plan-review.md",
                    "verification.json",
                ):
                    with self.subTest(name=name):
                        target = root / name
                        with self.assertRaises(PipelineError) as raised:
                            publish_new_artifacts(((target, b"artifact-payload"),))
                        self.assertEqual(
                            raised.exception.code,
                            ErrorCode.ATOMIC_PUBLISH_FAILED,
                        )
                        self.assertFalse(target.exists())
                        self.assertEqual(
                            list(root.glob(".liang-pingfa-artifact-*.tmp")),
                            [],
                        )

    def test_multi_artifact_commit_failure_rolls_back_prior_final(self) -> None:
        """A JSON/Markdown pair never leaves its first final after later failure."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            audit_output = root / "audit.json"
            report_output = root / "audit.md"
            original_rename = WindowsOwnedPath.rename_no_replace

            def fail_report_commit(
                opened: WindowsOwnedPath,
                destination: Path,
            ) -> None:
                if destination.name == report_output.name:
                    raise DestinationExistsError("synthetic later commit race")
                original_rename(opened, destination)

            with mock.patch.object(
                WindowsOwnedPath,
                "rename_no_replace",
                side_effect=fail_report_commit,
                autospec=True,
            ):
                with self.assertRaises(PipelineError) as raised:
                    publish_new_artifacts(
                        (
                            (audit_output, b'{"audit":true}\n'),
                            (report_output, b"# audit\n"),
                        )
                    )
            self.assertEqual(raised.exception.code, ErrorCode.OUTPUT_EXISTS)
            self.assertFalse(audit_output.exists())
            self.assertFalse(report_output.exists())
            self.assertEqual(list(root.glob(".liang-pingfa-artifact-*.tmp")), [])

    def test_artifact_encode_fsync_and_close_failures_leave_no_final(self) -> None:
        """Every late artifact failure cleans only the transaction's own file."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            encoding_target = root / "encoding.json"
            with self.assertRaises(UnicodeEncodeError):
                write_new_text(encoding_target, "\ud800")
            self.assertFalse(encoding_target.exists())

            fsync_target = root / "fsync.json"
            with mock.patch(
                "liang_pingfa_review.ownership.os.fsync",
                side_effect=OSError("synthetic fsync failure"),
            ):
                with self.assertRaises(PipelineError) as raised:
                    publish_new_artifacts(((fsync_target, b"artifact"),))
            self.assertEqual(raised.exception.code, ErrorCode.ATOMIC_PUBLISH_FAILED)
            self.assertFalse(fsync_target.exists())

            close_target = root / "close.json"
            original_close = WindowsOwnedPath.close
            close_failures = 0

            def close_after_release(opened: WindowsOwnedPath) -> None:
                nonlocal close_failures
                original_close(opened)
                if (
                    not opened._is_directory
                    and opened.path.name == close_target.name
                    and close_failures == 0
                ):
                    close_failures += 1
                    raise OSError("synthetic close failure")

            with mock.patch.object(
                WindowsOwnedPath,
                "close",
                side_effect=close_after_release,
                autospec=True,
            ):
                with self.assertRaises(PipelineError) as raised:
                    publish_new_artifacts(((close_target, b"artifact"),))
            self.assertEqual(raised.exception.code, ErrorCode.ATOMIC_PUBLISH_FAILED)
            self.assertFalse(close_target.exists())
            self.assertEqual(list(root.glob(".liang-pingfa-artifact-*.tmp")), [])

    def test_private_publication_temp_resists_public_parent_and_restores_readability(self) -> None:
        """A broad parent cannot expose bytes before cleanup/commit."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            # Use an SID rather than a localized account name.  This is a
            # generated directory only; failure to set the probe ACL is an
            # environment limitation, not a reason to weaken the test.
            acl = subprocess.run(
                [
                    "icacls",
                    str(root),
                    "/grant",
                    "*S-1-1-0:(OI)(CI)F",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if acl.returncode != 0:
                self.skipTest("cannot create generated broad-parent ACL probe")

            staged = root / "generated-source.dwg"
            output = root / "published.dwg"
            verification = root / "published.json"
            replacement = root / "generated-replacement.bin"
            staged.write_bytes(b"AC1032generated-private-publication")
            replacement.write_bytes(b"generated-replacement")
            targets = acquire_new_output_target_leases((output, verification))
            transaction = None
            try:
                transaction = stage_publication_transaction(
                    staged,
                    targets.targets[0],
                )
                transaction.stage_artifact(
                    targets.targets[1],
                    b'{"passed":true,"generated":true}',
                )
                temporaries = [
                    transaction.output.binding.path,
                    transaction.artifact.binding.path,  # type: ignore[union-attr]
                ]
                probe = (
                    "import os, sys\n"
                    "path, replacement = sys.argv[1:]\n"
                    "opened = []\n"
                    "try:\n"
                    "    with open(path, 'rb') as stream: stream.read(1)\n"
                    "    opened.append('read')\n"
                    "except OSError: pass\n"
                    "try:\n"
                    "    with open(path, 'r+b') as stream: stream.write(b'x')\n"
                    "    opened.append('write')\n"
                    "except OSError: pass\n"
                    "try:\n"
                    "    os.replace(replacement, path)\n"
                    "    opened.append('replace')\n"
                    "except OSError: pass\n"
                    "try:\n"
                    "    os.unlink(path)\n"
                    "    opened.append('delete')\n"
                    "except OSError: pass\n"
                    "raise SystemExit(1 if opened else 0)\n"
                )
                for temporary in temporaries:
                    result = subprocess.run(
                        [sys.executable, "-c", probe, str(temporary), str(replacement)],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10,
                    )
                    self.assertEqual(
                        result.returncode,
                        0,
                        f"second process accessed private temporary {temporary.name}",
                    )
                transaction.commit()
                transaction.finalize()
                for final in (output, verification):
                    result = subprocess.run(
                        [
                            sys.executable,
                            "-c",
                            "import pathlib, sys; pathlib.Path(sys.argv[1]).read_bytes()",
                            str(final),
                        ],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10,
                    )
                    self.assertEqual(result.returncode, 0)
            finally:
                if transaction is not None:
                    transaction.abort()
                targets.close()
