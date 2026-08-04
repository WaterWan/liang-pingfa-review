"""Generated-mock native copy-only apply/readback orchestration tests."""

from __future__ import annotations

from copy import deepcopy
from contextlib import ExitStack
from hashlib import sha256
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import unicodedata
from unittest import mock

import liang_pingfa_review.native_apply as apply_module
from liang_pingfa_review.canonical import (
    attach_integrity,
    canonical_json_bytes,
    canonical_sha256,
    load_json_file,
)
from liang_pingfa_review.core_console import CoreConsoleOutcome
from liang_pingfa_review.errors import ErrorCode, PipelineError
from liang_pingfa_review.native_apply import native_apply
from liang_pingfa_review.native_audit import build_native_audit, native_source_from_lease
from liang_pingfa_review.native_contracts import (
    bits_from_float,
    geometry_document_binding_digest,
    validate_native_contract,
)
from liang_pingfa_review.native_plan import generate_native_plan
from liang_pingfa_review.native_protocol import derive_challenge_response
from liang_pingfa_review.ownership import (
    OwnershipCleanupError,
    OwnershipLostError,
    acquire_source_path_lease,
    platform_backend,
    verify_private_staging_file,
)
from liang_pingfa_review.temporary import PrivateWorkspace
from tests.support.synthetic_native import config, digest, entity, geometry, intent, session


class _FakeComponentLeases:
    """Test-only retained component lease shape for mocked Core Console runs."""

    def __init__(
        self,
        *,
        close_fails: bool = False,
        fail_after_close: bool = False,
    ) -> None:
        self.paths = {
            "core_console": Path("generated-core.exe"),
            "write_plugin": Path("generated-write.dll"),
            "readback_plugin": Path("generated-readback.dll"),
        }
        self.require_count = 0
        self.closed = False
        self.close_fails = close_fails
        self.fail_after_close = fail_after_close

    def require_bindings(self) -> None:
        self.require_count += 1

    def close(self) -> None:
        if self.fail_after_close:
            self.closed = True
        if self.close_fails:
            raise OSError("generated component close failure")
        self.closed = True


class _CloseFailureWrapper:
    """Expose a real resource until its generated post-rename close failure."""

    def __init__(self, wrapped: object, *, fail_after_close: bool) -> None:
        self.wrapped = wrapped
        self.fail_after_close = fail_after_close
        self.close_attempted = False

    def __getattr__(self, name: str) -> object:
        return getattr(self.wrapped, name)

    def close(self) -> None:
        self.close_attempted = True
        if self.fail_after_close:
            self.wrapped.close()  # type: ignore[attr-defined]
        raise OSError("generated post-rename close failure")


def _rehash(entity_value: dict) -> None:
    projection = dict(entity_value)
    projection.pop("geometry_fingerprint", None)
    projection.pop("opaque_state_digest", None)
    entity_value["geometry_fingerprint"] = canonical_sha256({"geometry": projection})
    entity_value["opaque_state_digest"] = canonical_sha256({"opaque_state": projection})


@unittest.skipUnless(os.name == "nt", "native publish uses real Windows handle leases")
class NativeApplyVerifyTests(unittest.TestCase):
    """Exercise generated bridge/console boundaries without a real host."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source_path = self.root / "source.dwg"
        self.source_path.write_bytes(b"AC1032generated-native-source")
        backend = platform_backend(require_windows=True)
        lease = acquire_source_path_lease(self.source_path, backend)
        try:
            self.source_binding = native_source_from_lease(lease)
        finally:
            lease.close()
        self.before = geometry(
            [entity("10", text="generated-only")],
            source_value=self.source_binding,
        )
        self.read_session = session(source_value=self.source_binding)
        self.audit = build_native_audit(self.before, self.read_session, config())
        self.apply_session = deepcopy(self.read_session)
        self.apply_session["session_id"] = "native-session-" + "d" * 32
        self.apply_session["pid"] = 4321
        self.apply_session["process"]["instance_fingerprint"] = canonical_sha256(
            {"apply_session": "generated"}
        )
        self.apply_session["challenge_response"] = derive_challenge_response(
            self.apply_session["client_nonce"],
            self.apply_session["challenge"],
            self.apply_session["bridge_nonce"],
            session_id=self.apply_session["session_id"],
        )
        self.apply_session = attach_integrity(self.apply_session)
        self.fresh_before = geometry(
            [deepcopy(self.before["entities"][0])],
            source_value=self.source_binding,
            session_value=self.apply_session,
        )
        target = self.audit["records"][0]["target_id"]
        self.intent = intent(
            self.audit,
            operations=[
                {
                    "operation_id": "native-operation-" + "6" * 24,
                    "kind": "translate_dbtext",
                    "target_id": target,
                    "delta": [bits_from_float(1), bits_from_float(0), bits_from_float(0)],
                }
            ],
        )
        self.config = config()
        self.plan = generate_native_plan(self.audit, self.intent, self.config)
        moved = deepcopy(self.before["entities"][0])
        moved["position"] = [bits_from_float(2), bits_from_float(2), bits_from_float(0)]
        moved["bounds"] = {
            "minimum": [bits_from_float(2), bits_from_float(2), bits_from_float(0)],
            "maximum": [bits_from_float(2), bits_from_float(2), bits_from_float(0)],
        }
        _rehash(moved)
        self.after = geometry(
            [moved],
            source_value=self.source_binding,
            database_instance=digest("final-database"),
            revision=digest("final-revision"),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _fake_bridge(self) -> type:
        before = self.fresh_before

        class FakeBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def export_exact_geometry(self) -> dict:
                return before

            def close(self) -> None:
                pass

        return FakeBridge

    @staticmethod
    def _private_output_source(kwargs: dict[str, object]) -> dict:
        workspace = kwargs["workspace"]
        private_dwg = Path(kwargs["private_dwg"])
        opened = workspace.backend.open_existing_file_read_lease(private_dwg)
        try:
            binding = opened.capture_binding()
            final_path = opened.final_path()
            return {
                "format": "DWG",
                "sha256": binding.sha256,
                "byte_size": binding.byte_size,
                "path_fingerprint": sha256(
                    unicodedata.normalize("NFC", str(final_path)).encode("utf-8")
                ).hexdigest(),
                "file_identity_fingerprint": binding.file_identity_fingerprint,
                "dwg_header_signature": opened.read_prefix(6).decode("ascii"),
            }
        finally:
            opened.close()

    def _fake_console(
        self,
        after: dict,
        *,
        sidecar_name: str | None = None,
        before_write_result=None,
        before_readback_result=None,
    ):
        counter = [0]

        def run(**kwargs: object) -> CoreConsoleOutcome:
            counter[0] += 1
            manifest = validate_native_contract(
                "manifest",
                load_json_file(kwargs["manifest_path"]),  # type: ignore[arg-type]
            )
            run_id = "native-run-" + str(counter[0]) * 32
            if kwargs["mode"] == "write":
                if before_write_result is not None:
                    before_write_result(kwargs)
                output_copy_binding = self._private_output_source(kwargs)
                result = {
                    "schema_version": "liang-pingfa/native-console-result/v1",
                    "run_id": run_id,
                    "manifest_id": manifest["manifest_id"],
                    "manifest_integrity_sha256": manifest["integrity"]["sha256"],
                    "nonce": manifest["nonce"],
                    "final_revision_fingerprint": after["document"][
                        "revision_fingerprint"
                    ],
                    "final_revision_transition": "save_reopen_changed",
                    "final_document_binding": {
                        "database_instance_fingerprint": after["document"][
                            "database_instance_fingerprint"
                        ],
                        "revision_fingerprint": after["document"][
                            "revision_fingerprint"
                        ],
                        "output_copy_binding": output_copy_binding,
                    },
                    "transaction": {
                        "preflight": "passed",
                        "outcome": "committed",
                        "rollback": "not_required",
                    },
                    "operation_results": [
                        {
                            "operation_id": operation["operation_id"],
                            "status": "applied",
                            "postcondition_digest": canonical_sha256(operation),
                        }
                        for operation in manifest["operations"]
                    ],
                }
                artifact = attach_integrity(result)
            else:
                if before_readback_result is not None:
                    before_readback_result(kwargs)
                output_export = deepcopy(after)
                output_export["source"] = self._private_output_source(kwargs)
                output_export["binding"][
                    "document_binding_digest"
                ] = geometry_document_binding_digest(output_export)
                output_export = attach_integrity(output_export)
                artifact = attach_integrity(
                    {
                        "schema_version": "liang-pingfa/native-console-export/v1",
                        "run_id": run_id,
                        "manifest_id": manifest["manifest_id"],
                        "nonce": manifest["nonce"],
                        "final_revision_fingerprint": output_export["document"][
                            "revision_fingerprint"
                        ],
                        "final_document_binding": {
                            "database_instance_fingerprint": output_export["document"][
                                "database_instance_fingerprint"
                            ],
                            "revision_fingerprint": output_export["document"][
                                "revision_fingerprint"
                            ],
                            "output_copy_binding": output_export["source"],
                        },
                        "geometry_json": canonical_json_bytes(output_export).decode("utf-8"),
                        "geometry_sha256": canonical_sha256(output_export),
                    }
                )
            if sidecar_name is not None and kwargs["mode"] == "readback":
                (Path(kwargs["workspace"].path) / sidecar_name).write_bytes(  # type: ignore[index,union-attr]
                    b"generated-unregistered-sidecar"
                )
            return CoreConsoleOutcome(
                run_id=run_id,
                artifact=artifact,
                result_path=Path(kwargs["workspace"].path) / "generated-result.json",  # type: ignore[index,union-attr]
            )

        return run

    def test_generated_write_and_fresh_readback_publish_only_after_exact_match(self) -> None:
        output = self.root / "output.dwg"
        verification = self.root / "verification.json"
        component_leases = _FakeComponentLeases()
        with (
            mock.patch(
                "liang_pingfa_review.native_apply.acquire_native_installation_leases",
                return_value=component_leases,
            ),
            mock.patch(
                "liang_pingfa_review.native_apply.NativeBridgeClient",
                self._fake_bridge(),
            ),
            mock.patch(
                "liang_pingfa_review.native_apply.run_core_console",
                side_effect=self._fake_console(self.after),
            ) as console,
        ):
            result = native_apply(
                self.source_path,
                self.apply_session,
                self.audit,
                self.plan,
                self.intent,
                self.config,
                confirm_plan=self.plan["plan_id"],
                output_path=output,
                verification_path=verification,
            )
        self.assertEqual(console.call_count, 2)
        self.assertTrue(output.is_file())
        self.assertTrue(verification.is_file())
        self.assertTrue(result.verification["passed"])
        self.assertEqual(output.read_bytes(), self.source_path.read_bytes())
        self.assertTrue(component_leases.closed)

    def test_external_saved_dwg_owner_dacl_failures_publish_nothing(self) -> None:
        """Post-save validation rejects broad, untrusted, or unreadable ACLs."""

        import liang_pingfa_review.temporary as temporary_module

        original_verify = temporary_module.verify_private_staging_file
        for name in ("broad-dacl", "untrusted-owner", "dacl-query-failure"):
            with self.subTest(case=name):
                output = self.root / f"{name}.dwg"
                verification = self.root / f"{name}.json"
                after_write = [False]

                def mark_write(_kwargs: object) -> None:
                    after_write[0] = True

                def reject_saved(
                    opened: object,
                    backend: object,
                    *,
                    require_protected: bool = True,
                ) -> object:
                    if (
                        after_write[0]
                        and opened.final_path().name == "native-source-copy.dwg"  # type: ignore[attr-defined]
                    ):
                        raise OwnershipCleanupError(f"generated {name}")
                    return original_verify(
                        opened,  # type: ignore[arg-type]
                        backend,  # type: ignore[arg-type]
                        require_protected=require_protected,
                    )

                with (
                    mock.patch(
                        "liang_pingfa_review.native_apply.acquire_native_installation_leases",
                        return_value=_FakeComponentLeases(),
                    ),
                    mock.patch(
                        "liang_pingfa_review.native_apply.NativeBridgeClient",
                        self._fake_bridge(),
                    ),
                    mock.patch(
                        "liang_pingfa_review.native_apply.run_core_console",
                        side_effect=self._fake_console(
                            self.after,
                            before_write_result=mark_write,
                        ),
                    ),
                    mock.patch(
                        "liang_pingfa_review.temporary.verify_private_staging_file",
                        side_effect=reject_saved,
                    ),
                    self.assertRaises(PipelineError) as raised,
                ):
                    native_apply(
                        self.source_path,
                        self.apply_session,
                        self.audit,
                        self.plan,
                        self.intent,
                        self.config,
                        confirm_plan=self.plan["plan_id"],
                        output_path=output,
                        verification_path=verification,
                    )
                self.assertEqual(
                    raised.exception.code,
                    ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                )
                self.assertFalse(output.exists())
                self.assertFalse(verification.exists())

    def test_safe_external_replacement_requires_exact_result_binding(self) -> None:
        """A contract-authorized save replacement is adopted only when bound."""

        output = self.root / "replacement-output.dwg"
        verification = self.root / "replacement-verification.json"
        replacement = b"AC1032generated-safe-replacement"

        def replace_before_result(kwargs: object) -> None:
            private_dwg = Path(kwargs["private_dwg"])  # type: ignore[index]
            temporary = private_dwg.with_name("generated-save-replacement.tmp")
            temporary.write_bytes(replacement)
            os.replace(temporary, private_dwg)

        with (
            mock.patch(
                "liang_pingfa_review.native_apply.acquire_native_installation_leases",
                return_value=_FakeComponentLeases(),
            ),
            mock.patch(
                "liang_pingfa_review.native_apply.NativeBridgeClient",
                self._fake_bridge(),
            ),
            mock.patch(
                "liang_pingfa_review.native_apply.run_core_console",
                side_effect=self._fake_console(
                    self.after,
                    before_write_result=replace_before_result,
                ),
            ),
        ):
            result = native_apply(
                self.source_path,
                self.apply_session,
                self.audit,
                self.plan,
                self.intent,
                self.config,
                confirm_plan=self.plan["plan_id"],
                output_path=output,
                verification_path=verification,
            )
        self.assertTrue(result.verification["passed"])
        self.assertEqual(output.read_bytes(), replacement)
        self.assertTrue(verification.is_file())

    def test_unbound_external_replacement_publishes_nothing(self) -> None:
        """A saved filename alone cannot authorize a replacement DWG."""

        output = self.root / "unbound-replacement-output.dwg"
        verification = self.root / "unbound-replacement-verification.json"
        base_console = self._fake_console(self.after)

        def replace_after_result(**kwargs: object) -> CoreConsoleOutcome:
            outcome = base_console(**kwargs)
            if kwargs["mode"] == "write":
                private_dwg = Path(kwargs["private_dwg"])
                temporary = private_dwg.with_name("generated-unbound-replacement.tmp")
                temporary.write_bytes(b"AC1032generated-unbound-replacement")
                os.replace(temporary, private_dwg)
            return outcome

        with (
            mock.patch(
                "liang_pingfa_review.native_apply.acquire_native_installation_leases",
                return_value=_FakeComponentLeases(),
            ),
            mock.patch(
                "liang_pingfa_review.native_apply.NativeBridgeClient",
                self._fake_bridge(),
            ),
            mock.patch(
                "liang_pingfa_review.native_apply.run_core_console",
                side_effect=replace_after_result,
            ),
            self.assertRaises(PipelineError) as raised,
        ):
            native_apply(
                self.source_path,
                self.apply_session,
                self.audit,
                self.plan,
                self.intent,
                self.config,
                confirm_plan=self.plan["plan_id"],
                output_path=output,
                verification_path=verification,
            )
        self.assertIn(
            raised.exception.code,
            {
                ErrorCode.NATIVE_CONSOLE_RESULT_INVALID,
                # A replacement intentionally remains quarantined rather
                # than being deleted by pathname during workspace recovery.
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
            },
        )
        self.assertFalse(output.exists())
        self.assertFalse(verification.exists())

    def test_saved_private_dwg_lease_blocks_second_process_write(self) -> None:
        """Readback runs while the validated DWG lease denies other writers."""

        output = self.root / "lease-output.dwg"
        verification = self.root / "lease-verification.json"
        writer_status: list[int] = []

        def attempt_second_process_write(kwargs: object) -> None:
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path; import sys; "
                        "Path(sys.argv[1]).write_bytes(b'AC1032foreign-write')"
                    ),
                    os.fspath(Path(kwargs["private_dwg"])),  # type: ignore[index]
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            writer_status.append(result.returncode)

        with (
            mock.patch(
                "liang_pingfa_review.native_apply.acquire_native_installation_leases",
                return_value=_FakeComponentLeases(),
            ),
            mock.patch(
                "liang_pingfa_review.native_apply.NativeBridgeClient",
                self._fake_bridge(),
            ),
            mock.patch(
                "liang_pingfa_review.native_apply.run_core_console",
                side_effect=self._fake_console(
                    self.after,
                    before_readback_result=attempt_second_process_write,
                ),
            ),
        ):
            native_apply(
                self.source_path,
                self.apply_session,
                self.audit,
                self.plan,
                self.intent,
                self.config,
                confirm_plan=self.plan["plan_id"],
                output_path=output,
                verification_path=verification,
            )
        self.assertEqual(len(writer_status), 1)
        self.assertNotEqual(writer_status[0], 0)
        self.assertTrue(output.is_file())
        self.assertTrue(verification.is_file())

    def test_post_readback_private_dwg_drift_publishes_nothing(self) -> None:
        """The final pre-publication owner/DACL/content recheck is mandatory."""

        output = self.root / "drift-output.dwg"
        verification = self.root / "drift-verification.json"
        readback_started = [False]
        original_validate = PrivateWorkspace.validate_retained_private_file

        def mark_readback(_kwargs: object) -> None:
            readback_started[0] = True

        def drifting_validate(
            workspace: PrivateWorkspace,
            path: Path,
            opened: object,
            **kwargs: object,
        ) -> object:
            if readback_started[0]:
                raise OwnershipLostError("generated saved DWG content drift")
            return original_validate(
                workspace,
                path,
                opened,  # type: ignore[arg-type]
                **kwargs,  # type: ignore[arg-type]
            )

        with (
            mock.patch(
                "liang_pingfa_review.native_apply.acquire_native_installation_leases",
                return_value=_FakeComponentLeases(),
            ),
            mock.patch(
                "liang_pingfa_review.native_apply.NativeBridgeClient",
                self._fake_bridge(),
            ),
            mock.patch(
                "liang_pingfa_review.native_apply.run_core_console",
                side_effect=self._fake_console(
                    self.after,
                    before_readback_result=mark_readback,
                ),
            ),
            mock.patch.object(
                PrivateWorkspace,
                "validate_retained_private_file",
                new=drifting_validate,
            ),
            self.assertRaises(PipelineError) as raised,
        ):
            native_apply(
                self.source_path,
                self.apply_session,
                self.audit,
                self.plan,
                self.intent,
                self.config,
                confirm_plan=self.plan["plan_id"],
                output_path=output,
                verification_path=verification,
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_READBACK_INVALID)
        self.assertFalse(output.exists())
        self.assertFalse(verification.exists())

    def test_native_verification_final_retains_private_dacl_after_rename(self) -> None:
        """The public DWG/public parent must not broaden verification JSON."""

        output = self.root / "private-verification-output.dwg"
        verification = self.root / "private-verification.json"
        component_leases = _FakeComponentLeases()
        with (
            mock.patch(
                "liang_pingfa_review.native_apply.acquire_native_installation_leases",
                return_value=component_leases,
            ),
            mock.patch(
                "liang_pingfa_review.native_apply.NativeBridgeClient",
                self._fake_bridge(),
            ),
            mock.patch(
                "liang_pingfa_review.native_apply.run_core_console",
                side_effect=self._fake_console(self.after),
            ),
        ):
            native_apply(
                self.source_path,
                self.apply_session,
                self.audit,
                self.plan,
                self.intent,
                self.config,
                confirm_plan=self.plan["plan_id"],
                output_path=output,
                verification_path=verification,
            )
        backend = platform_backend(require_windows=True)
        opened = backend.open_existing_file_read_lease(verification)
        try:
            verify_private_staging_file(opened, backend)
        finally:
            opened.close()

    def test_final_paths_remain_absent_until_private_workspace_cleanup_succeeds(self) -> None:
        output = self.root / "cleanup-gated-output.dwg"
        verification = self.root / "cleanup-gated-verification.json"
        component_leases = _FakeComponentLeases()
        observed_at_workspace_exit: list[tuple[bool, bool]] = []
        original_exit = apply_module.PrivateWorkspace.__exit__

        def observing_exit(
            workspace: object,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> bool:
            observed_at_workspace_exit.append((output.exists(), verification.exists()))
            return original_exit(workspace, exc_type, exc, traceback)  # type: ignore[arg-type]

        with (
            mock.patch(
                "liang_pingfa_review.native_apply.acquire_native_installation_leases",
                return_value=component_leases,
            ),
            mock.patch(
                "liang_pingfa_review.native_apply.NativeBridgeClient",
                self._fake_bridge(),
            ),
            mock.patch(
                "liang_pingfa_review.native_apply.run_core_console",
                side_effect=self._fake_console(self.after),
            ),
            mock.patch.object(
                apply_module.PrivateWorkspace,
                "__exit__",
                new=observing_exit,
            ),
        ):
            result = native_apply(
                self.source_path,
                self.apply_session,
                self.audit,
                self.plan,
                self.intent,
                self.config,
                confirm_plan=self.plan["plan_id"],
                output_path=output,
                verification_path=verification,
            )
        self.assertEqual(observed_at_workspace_exit, [(False, False)])
        self.assertTrue(result.verification["passed"])
        self.assertTrue(output.exists())
        self.assertTrue(verification.exists())

    def test_post_rename_source_target_and_component_close_failures_roll_back_pair(self) -> None:
        """All final cleanup runs before final handles are irreversibly released."""

        cases = (
            ("source-before-close", "source", False),
            ("source-after-close", "source", True),
            ("target-before-close", "target", False),
            ("target-after-close", "target", True),
            ("component-before-close", "component", False),
            ("component-after-close", "component", True),
        )
        for name, resource, after_close in cases:
            with self.subTest(name=name):
                output = self.root / f"{name}.dwg"
                verification = self.root / f"{name}.json"
                component_leases = _FakeComponentLeases(
                    close_fails=resource == "component",
                    fail_after_close=after_close,
                )
                source_wrapper: _CloseFailureWrapper | None = None
                target_wrapper: _CloseFailureWrapper | None = None
                real_targets = None
                original_acquire = apply_module.acquire_source_path_lease

                def acquire_with_failure(*args: object, **kwargs: object) -> object:
                    nonlocal source_wrapper
                    source_wrapper = _CloseFailureWrapper(
                        original_acquire(*args, **kwargs),
                        fail_after_close=after_close,
                    )
                    return source_wrapper

                try:
                    with ExitStack() as stack:
                        stack.enter_context(
                            mock.patch(
                                "liang_pingfa_review.native_apply.acquire_native_installation_leases",
                                return_value=component_leases,
                            )
                        )
                        stack.enter_context(
                            mock.patch(
                                "liang_pingfa_review.native_apply.NativeBridgeClient",
                                self._fake_bridge(),
                            )
                        )
                        stack.enter_context(
                            mock.patch(
                                "liang_pingfa_review.native_apply.run_core_console",
                                side_effect=self._fake_console(self.after),
                            )
                        )
                        if resource == "source":
                            stack.enter_context(
                                mock.patch(
                                    "liang_pingfa_review.native_apply.acquire_source_path_lease",
                                    side_effect=acquire_with_failure,
                                )
                            )
                        if resource == "target":
                            real_targets = apply_module.acquire_new_output_target_leases(
                                (output, verification)
                            )
                            target_wrapper = _CloseFailureWrapper(
                                real_targets,
                                fail_after_close=after_close,
                            )
                            stack.enter_context(
                                mock.patch(
                                    "liang_pingfa_review.native_apply._validate_apply_targets",
                                    return_value=target_wrapper,
                                )
                            )
                        with self.assertRaises(PipelineError) as raised:
                            native_apply(
                                self.source_path,
                                self.apply_session,
                                self.audit,
                                self.plan,
                                self.intent,
                                self.config,
                                confirm_plan=self.plan["plan_id"],
                                output_path=output,
                                verification_path=verification,
                            )
                    self.assertEqual(
                        raised.exception.code,
                        ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                    )
                    self.assertFalse(output.exists())
                    self.assertFalse(verification.exists())
                    if source_wrapper is not None:
                        self.assertTrue(source_wrapper.close_attempted)
                    if target_wrapper is not None:
                        self.assertTrue(target_wrapper.close_attempted)
                    if resource == "component":
                        self.assertEqual(component_leases.closed, after_close)
                finally:
                    # Before-close probes intentionally leave their wrapped
                    # resource available to this test's explicit cleanup.
                    if source_wrapper is not None:
                        try:
                            source_wrapper.wrapped.close()  # type: ignore[attr-defined]
                        except OSError:
                            pass
                    if real_targets is not None:
                        try:
                            real_targets.close()
                        except PipelineError:
                            pass

    def test_unknown_and_spoofed_core_console_sidecars_publish_nothing(self) -> None:
        for sidecar_name in ("generated-console-sidecar.err", "native-console-result.json"):
            with self.subTest(sidecar_name=sidecar_name):
                output = self.root / f"{sidecar_name}.dwg"
                verification = self.root / f"{sidecar_name}.json"
                component_leases = _FakeComponentLeases()
                with (
                    mock.patch(
                        "liang_pingfa_review.native_apply.acquire_native_installation_leases",
                        return_value=component_leases,
                    ),
                    mock.patch(
                        "liang_pingfa_review.native_apply.NativeBridgeClient",
                        self._fake_bridge(),
                    ),
                    mock.patch(
                        "liang_pingfa_review.native_apply.run_core_console",
                        side_effect=self._fake_console(
                            self.after,
                            sidecar_name=sidecar_name,
                        ),
                    ),
                    self.assertRaises(PipelineError) as raised,
                ):
                    native_apply(
                        self.source_path,
                        self.apply_session,
                        self.audit,
                        self.plan,
                        self.intent,
                        self.config,
                        confirm_plan=self.plan["plan_id"],
                        output_path=output,
                        verification_path=verification,
                    )
                self.assertEqual(
                    raised.exception.code,
                    ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                )
                self.assertFalse(output.exists())
                self.assertFalse(verification.exists())

    def test_workspace_cleanup_exception_after_staging_aborts_native_publication(self) -> None:
        output = self.root / "cleanup-exception-output.dwg"
        verification = self.root / "cleanup-exception-verification.json"
        component_leases = _FakeComponentLeases()
        original_workspace = apply_module.PrivateWorkspace
        created_workspaces: list[PrivateWorkspace] = []

        def workspace_in_test_root(*args: object, **kwargs: object) -> PrivateWorkspace:
            kwargs["directory"] = self.root
            workspace = original_workspace(*args, **kwargs)  # type: ignore[arg-type]
            created_workspaces.append(workspace)
            return workspace

        def fail_recovery() -> None:
            raise PipelineError(
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
                "generated private cleanup failure",
            )

        try:
            with (
                mock.patch(
                    "liang_pingfa_review.native_apply.acquire_native_installation_leases",
                    return_value=component_leases,
                ),
                mock.patch(
                    "liang_pingfa_review.native_apply.NativeBridgeClient",
                    self._fake_bridge(),
                ),
                mock.patch(
                    "liang_pingfa_review.native_apply.run_core_console",
                    side_effect=self._fake_console(self.after),
                ),
                mock.patch(
                    "liang_pingfa_review.native_apply.PrivateWorkspace",
                    side_effect=workspace_in_test_root,
                ),
                mock.patch.object(
                    original_workspace,
                    "_recover",
                    side_effect=fail_recovery,
                ),
                self.assertRaises(PipelineError) as raised,
            ):
                native_apply(
                    self.source_path,
                    self.apply_session,
                    self.audit,
                    self.plan,
                    self.intent,
                    self.config,
                    confirm_plan=self.plan["plan_id"],
                    output_path=output,
                    verification_path=verification,
                )
            self.assertEqual(
                raised.exception.code,
                ErrorCode.PUBLICATION_CLEANUP_FAILURE,
            )
            self.assertFalse(output.exists())
            self.assertFalse(verification.exists())
            self.assertEqual(list(self.root.glob(".liang-pingfa-publish-*.tmp")), [])
            self.assertEqual(list(self.root.glob(".liang-pingfa-artifact-*.tmp")), [])
        finally:
            # The mocked cleanup intentionally left the workspace handles
            # open.  Restore and invoke real identity-bound recovery before
            # removing any generated test directory.
            for workspace in created_workspaces:
                if workspace.path.exists():
                    workspace._recover()
            for workspace_path in self.root.glob("liang-pingfa-native-apply-*"):
                if workspace_path.exists():
                    shutil.rmtree(workspace_path)

    def test_unplanned_readback_change_publishes_nothing(self) -> None:
        output = self.root / "output.dwg"
        verification = self.root / "verification.json"
        component_leases = _FakeComponentLeases()
        bad_entity = deepcopy(self.after["entities"][0])
        bad_entity["text"] = "changed"
        _rehash(bad_entity)
        bad_after = geometry(
            [bad_entity],
            source_value=self.source_binding,
            database_instance=self.after["document"]["database_instance_fingerprint"],
            revision=self.after["document"]["revision_fingerprint"],
        )
        with (
            mock.patch(
                "liang_pingfa_review.native_apply.acquire_native_installation_leases",
                return_value=component_leases,
            ),
            mock.patch(
                "liang_pingfa_review.native_apply.NativeBridgeClient",
                self._fake_bridge(),
            ),
            mock.patch(
                "liang_pingfa_review.native_apply.run_core_console",
                side_effect=self._fake_console(bad_after),
            ),
            self.assertRaises(PipelineError) as raised,
        ):
            native_apply(
                self.source_path,
                self.apply_session,
                self.audit,
                self.plan,
                self.intent,
                self.config,
                confirm_plan=self.plan["plan_id"],
                output_path=output,
                verification_path=verification,
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_READBACK_INVALID)
        self.assertFalse(output.exists())
        self.assertFalse(verification.exists())


if __name__ == "__main__":
    unittest.main()
