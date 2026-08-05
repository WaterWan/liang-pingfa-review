"""Fixed-script and redaction boundaries for external Core Console invocation."""

from __future__ import annotations

import json
from pathlib import Path
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from liang_pingfa_review.canonical import (
    attach_integrity,
    canonical_json_bytes,
    canonical_sha256,
)
from liang_pingfa_review.core_console import (
    _MAX_CONSOLE_STREAM_BYTES,
    fixed_script_content,
    run_core_console,
)
from liang_pingfa_review.errors import ErrorCode, PipelineError
from liang_pingfa_review.native_bridge import native_doctor_status
from liang_pingfa_review.temporary import PrivateWorkspace
from tests.support.synthetic_native import config, digest, geometry


class GeneratedMockCoreConsoleTests(unittest.TestCase):
    """The generated stand-in must not hide copy-only write behavior."""

    def test_write_mode_changes_private_dwg_for_each_operation_kind(self) -> None:
        script = Path(__file__).with_name("support") / "mock_core_console.py"
        operations = (
            {"operation_id": "native-operation-" + "a" * 24, "kind": "translate_dbtext"},
            {
                "operation_id": "native-operation-" + "b" * 24,
                "kind": "delete_auxiliary_overlay_text",
            },
            {
                "operation_id": "native-operation-" + "c" * 24,
                "kind": "create_review_marker",
            },
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for operation in operations:
                with self.subTest(kind=operation["kind"]):
                    case_root = root / operation["kind"]
                    case_root.mkdir()
                    private_dwg = case_root / "private.dwg"
                    manifest = case_root / "manifest.json"
                    result = case_root / "native-console-result.json"
                    original = b"AC1032generated-mock-input"
                    private_dwg.write_bytes(original)
                    manifest.write_text(
                        json.dumps({"operations": [operation]}),
                        encoding="utf-8",
                    )
                    environment = {
                        **os.environ,
                        "LIANG_PINGFA_NATIVE_MANIFEST": str(manifest),
                        "LIANG_PINGFA_NATIVE_RESULT": str(result),
                        "LIANG_PINGFA_TEST_CONSOLE_PAYLOAD": "{}",
                    }
                    completed = subprocess.run(
                        [
                            sys.executable,
                            str(script),
                            "/i",
                            str(private_dwg),
                            "/s",
                            "generated.scr",
                        ],
                        env=environment,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(0, completed.returncode, completed.stderr)
                    self.assertNotEqual(original, private_dwg.read_bytes())
                    self.assertTrue(private_dwg.read_bytes().startswith(b"AC1032"))
                    self.assertEqual("{}", result.read_text(encoding="utf-8"))


class _FakeComponentLeases:
    """Test-only component lease substitute for generated console processes."""

    def __init__(self) -> None:
        self.paths = {
            "core_console": Path("generated-core.exe"),
            "write_plugin": Path("generated-write.dll"),
            "readback_plugin": Path("generated-readback.dll"),
        }
        self.require_count = 0

    def require_bindings(self) -> None:
        self.require_count += 1

    def close(self) -> None:
        pass


class NativeCoreConsoleTests(unittest.TestCase):
    """No test starts a proprietary host; only fixed local strings are checked."""

    def test_fixed_write_and_readback_scripts_have_exact_three_lines(self) -> None:
        plugin = Path("generated-plugin.dll")
        self.assertEqual(
            fixed_script_content(plugin, "LPF_NATIVE_EXECUTE_MANIFEST"),
            b'_.NETLOAD\r\n"generated-plugin.dll"\r\nLPF_NATIVE_EXECUTE_MANIFEST\r\n',
        )
        self.assertEqual(
            fixed_script_content(plugin, "LPF_NATIVE_EXPORT_MANIFEST"),
            b'_.NETLOAD\r\n"generated-plugin.dll"\r\nLPF_NATIVE_EXPORT_MANIFEST\r\n',
        )

    def test_script_never_accepts_command_or_path_injection(self) -> None:
        with self.assertRaises(PipelineError) as raised:
            fixed_script_content(Path('generated-"bad.dll'), "LPF_NATIVE_EXECUTE_MANIFEST")
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CONFIG_INVALID)
        with self.assertRaises(PipelineError) as raised:
            fixed_script_content(Path("generated-plugin.dll"), "_.ERASE")
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CONFIG_INVALID)

    def test_doctor_is_redacted_without_explicit_config(self) -> None:
        status = native_doctor_status(None)
        self.assertEqual(status["command"], "native-doctor")
        self.assertEqual(status["per_file_compatibility"], "audit_required")
        self.assertEqual(status["integration_claim"], "external-adapter-not-validated")
        self.assertNotIn("path", " ".join(status))


@unittest.skipUnless(os.name == "nt", "Core Console execution boundary is Windows-only")
class NativeCoreConsoleExecutionTests(unittest.TestCase):
    """Use a generated Popen double to test fixed launch and bounded failure paths."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _workspace_files(self, workspace: PrivateWorkspace) -> tuple[Path, Path]:
        drawing = workspace.path / "private.dwg"
        drawing_handle = workspace.create_owned_file(drawing)
        drawing_handle.write_bytes(b"AC1032generated-private-copy")
        workspace.seal_owned_file(drawing_handle)
        manifest = workspace.path / "manifest.json"
        manifest_handle = workspace.create_owned_file(manifest)
        manifest_handle.write_bytes(b"{}")
        workspace.seal_owned_file(manifest_handle)
        return drawing, manifest

    @staticmethod
    def _installations() -> dict[str, Path]:
        return {
            "core_console": Path("generated-core.exe"),
            "write_plugin": Path("generated-write.dll"),
            "readback_plugin": Path("generated-readback.dll"),
        }

    def _launcher(
        self,
        *,
        write_result: bool = True,
        oversized: bool = False,
        timeout: bool = False,
    ):
        seen: dict[str, object] = {}

        class Process:
            def __init__(
                self,
                command: list[str],
                environment: dict[str, str],
            ) -> None:
                seen["command"] = command
                seen["environment"] = environment
                self._environment = environment
                self.returncode = 0
                self.stdout = io.BytesIO(
                    b"x" * (_MAX_CONSOLE_STREAM_BYTES + 1) if oversized else b""
                )
                self.stderr = io.BytesIO()

            def wait(self, timeout: float | None = None) -> int:
                if timeout:
                    seen["timeout"] = timeout
                if timeout is not None and timeout_marker[0]:
                    raise subprocess.TimeoutExpired(["generated"], timeout)
                if write_result:
                    if Path(
                        self._environment["LIANG_PINGFA_NATIVE_RESULT"]
                    ).name == "native-console-export.json":
                        exported = geometry()
                        payload = attach_integrity(
                            {
                                "schema_version": "liang-pingfa/native-console-export/v2",
                                "run_id": self._environment[
                                    "LIANG_PINGFA_NATIVE_RUN_ID"
                                ],
                                "manifest_id": "native-manifest-" + "a" * 32,
                                "manifest_integrity_sha256": digest("manifest"),
                                "manifest_schema_version": (
                                    "liang-pingfa/native-edit-manifest/v2"
                                ),
                                "console_result_integrity_sha256": digest(
                                    "console-result"
                                ),
                                "console_result_schema_version": (
                                    "liang-pingfa/native-console-result/v2"
                                ),
                                "nonce": "a" * 43,
                                "final_revision_fingerprint": exported["document"][
                                    "revision_fingerprint"
                                ],
                                "final_document_binding": {
                                    "database_instance_fingerprint": exported[
                                        "document"
                                    ]["database_instance_fingerprint"],
                                    "revision_fingerprint": exported["document"][
                                        "revision_fingerprint"
                                    ],
                                    "output_copy_binding": exported["source"],
                                },
                                "geometry_json": canonical_json_bytes(exported).decode(
                                    "utf-8"
                                ),
                                "geometry_sha256": canonical_sha256(exported),
                            }
                        )
                    else:
                        payload = attach_integrity(
                            {
                            "schema_version": "liang-pingfa/native-console-result/v2",
                            "run_id": self._environment["LIANG_PINGFA_NATIVE_RUN_ID"],
                            "manifest_id": "native-manifest-" + "a" * 32,
                            "manifest_integrity_sha256": digest("manifest"),
                            "manifest_schema_version": (
                                "liang-pingfa/native-edit-manifest/v2"
                            ),
                            "nonce": "a" * 43,
                            "final_revision_fingerprint": digest("revision"),
                            "final_revision_transition": "save_reopen_changed",
                            "final_document_binding": {
                                "database_instance_fingerprint": digest("database"),
                                "revision_fingerprint": digest("revision"),
                                "output_copy_binding": {
                                    "format": "DWG",
                                    "sha256": digest("output"),
                                    "byte_size": 128,
                                    "path_fingerprint": digest("output-path"),
                                    "file_identity_fingerprint": digest("output-identity"),
                                    "dwg_header_signature": "AC1032",
                                },
                            },
                            "transaction": {
                                "preflight": "passed",
                                "outcome": "committed",
                                "rollback": "not_required",
                            },
                            "operation_results": [
                                {
                                    "operation_id": "native-operation-" + "a" * 24,
                                    "status": "applied",
                                    "postcondition_digest": digest("postcondition"),
                                }
                            ],
                            }
                        )
                    Path(self._environment["LIANG_PINGFA_NATIVE_RESULT"]).write_bytes(
                        canonical_json_bytes(payload)
                    )
                return 0

            def poll(self) -> int:
                return self.returncode

            def close(self) -> None:
                self.stdout.close()
                self.stderr.close()

        timeout_marker = [timeout]

        class Job:
            def __init__(self) -> None:
                seen["job"] = self
                self.closed = False
                self.termination_count = 0

            def terminate_and_wait(self, process: Process) -> None:
                self.termination_count += 1
                if process.returncode is None:
                    process.returncode = -1

            def close(self) -> None:
                self.closed = True

        def launch(
            *,
            job: Job,
            application: Path,
            command: list[str],
            cwd: Path,
            environment: dict[str, str],
        ) -> Process:
            seen["application"] = application
            seen["cwd"] = cwd
            seen["job_argument"] = job
            return Process(command, environment)

        return launch, Job, seen

    @staticmethod
    def _components() -> _FakeComponentLeases:
        return _FakeComponentLeases()

    def test_fixed_launch_uses_private_copy_exact_args_and_no_manifest_script_line(self) -> None:
        fake_launcher, fake_job, seen = self._launcher()

        with PrivateWorkspace(
            prefix="native-core-test-",
            directory=self.root,
        ) as workspace:
            drawing, manifest = self._workspace_files(workspace)
            components = self._components()
            with (
                mock.patch(
                    "liang_pingfa_review.core_console._launch_windows_contained_process",
                    fake_launcher,
                ),
                mock.patch(
                    "liang_pingfa_review.core_console._WindowsKillJob",
                    fake_job,
                ),
            ):
                outcome = run_core_console(
                    workspace=workspace,
                    private_dwg=drawing,
                    manifest_path=manifest,
                    config=config(),
                    mode="write",
                    component_leases=components,
                )
            self.assertEqual(outcome.artifact["schema_version"], "liang-pingfa/native-console-result/v2")
            command = seen["command"]
            self.assertEqual(command[1:3], ["/i", str(drawing)])
            self.assertEqual(command[3], "/s")
            script = Path(command[4]).read_text(encoding="utf-8")
            self.assertNotIn(str(manifest), script)
            self.assertEqual(script.splitlines()[0], "_.NETLOAD")
            self.assertEqual(
                seen["environment"]["LIANG_PINGFA_NATIVE_MANIFEST"],
                str(manifest),
            )
            self.assertNotIn("PATH", seen["environment"])
            self.assertEqual(seen["application"], self._installations()["core_console"])
            self.assertGreaterEqual(components.require_count, 2)
            self.assertTrue(seen["job"].closed)
            self.assertEqual(seen["job"].termination_count, 1)

    def test_timeout_and_oversized_console_output_fail_closed(self) -> None:
        for name, arguments, expected in (
            ("timeout", {"timeout": True}, ErrorCode.NATIVE_CONSOLE_TIMEOUT),
            ("oversized", {"oversized": True, "write_result": False}, ErrorCode.NATIVE_CONSOLE_FAILURE),
        ):
            with self.subTest(name=name), PrivateWorkspace(
                prefix="native-core-failure-",
                directory=self.root,
            ) as workspace:
                drawing, manifest = self._workspace_files(workspace)
                fake_launcher, fake_job, _seen = self._launcher(**arguments)
                components = self._components()
                with (
                    mock.patch(
                        "liang_pingfa_review.core_console._launch_windows_contained_process",
                        fake_launcher,
                    ),
                    mock.patch(
                        "liang_pingfa_review.core_console._WindowsKillJob",
                        fake_job,
                    ),
                    self.assertRaises(PipelineError) as raised,
                ):
                    run_core_console(
                        workspace=workspace,
                        private_dwg=drawing,
                        manifest_path=manifest,
                        config=config(),
                        mode="write",
                        component_leases=components,
                    )
                self.assertEqual(raised.exception.code, expected)

    def test_write_console_uses_configured_bounded_deadline(self) -> None:
        with PrivateWorkspace(
            prefix="native-core-configured-timeout-",
            directory=self.root,
        ) as workspace:
            drawing, manifest = self._workspace_files(workspace)
            fake_launcher, fake_job, seen = self._launcher()
            configured = config()
            configured["timeouts"]["write_console_seconds"] = 7
            with (
                mock.patch(
                    "liang_pingfa_review.core_console._launch_windows_contained_process",
                    fake_launcher,
                ),
                mock.patch(
                    "liang_pingfa_review.core_console._WindowsKillJob",
                    fake_job,
                ),
            ):
                run_core_console(
                    workspace=workspace,
                    private_dwg=drawing,
                    manifest_path=manifest,
                    config=configured,
                    mode="write",
                    component_leases=self._components(),
                )
            self.assertEqual(seen["timeout"], 7)

    def test_readback_console_uses_configured_bounded_deadline(self) -> None:
        with PrivateWorkspace(
            prefix="native-core-readback-timeout-",
            directory=self.root,
        ) as workspace:
            drawing, manifest = self._workspace_files(workspace)
            fake_launcher, fake_job, seen = self._launcher()
            configured = config()
            configured["timeouts"]["readback_console_seconds"] = 8
            with (
                mock.patch(
                    "liang_pingfa_review.core_console._launch_windows_contained_process",
                    fake_launcher,
                ),
                mock.patch(
                    "liang_pingfa_review.core_console._WindowsKillJob",
                    fake_job,
                ),
            ):
                outcome = run_core_console(
                    workspace=workspace,
                    private_dwg=drawing,
                    manifest_path=manifest,
                    config=configured,
                    mode="readback",
                    component_leases=self._components(),
                )
            self.assertEqual(
                outcome.artifact["schema_version"],
                "liang-pingfa/native-console-export/v2",
            )
            self.assertEqual(seen["timeout"], 8)

    def test_job_creation_and_suspended_launch_fail_closed(self) -> None:
        with PrivateWorkspace(
            prefix="native-core-startup-failure-",
            directory=self.root,
        ) as workspace:
            drawing, manifest = self._workspace_files(workspace)
            components = self._components()

            class FailingJob:
                def __init__(self) -> None:
                    raise PipelineError(
                        ErrorCode.NATIVE_CONSOLE_FAILURE,
                        "generated job setup failure",
                    )

            with (
                mock.patch(
                    "liang_pingfa_review.core_console._WindowsKillJob",
                    FailingJob,
                ),
                self.assertRaises(PipelineError) as raised,
            ):
                run_core_console(
                    workspace=workspace,
                    private_dwg=drawing,
                    manifest_path=manifest,
                    config=config(),
                    mode="write",
                    component_leases=components,
                )
            self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CONSOLE_FAILURE)

        with PrivateWorkspace(
            prefix="native-core-create-failure-",
            directory=self.root,
        ) as workspace:
            drawing, manifest = self._workspace_files(workspace)
            components = self._components()
            seen: dict[str, object] = {}

            class Job:
                def __init__(self) -> None:
                    self.closed = False
                    seen["job"] = self

                def close(self) -> None:
                    self.closed = True

            with (
                mock.patch("liang_pingfa_review.core_console._WindowsKillJob", Job),
                mock.patch(
                    "liang_pingfa_review.core_console._launch_windows_contained_process",
                    side_effect=PipelineError(
                        ErrorCode.NATIVE_CONSOLE_FAILURE,
                        "generated CreateProcess failure",
                    ),
                ),
                self.assertRaises(PipelineError) as raised,
            ):
                run_core_console(
                    workspace=workspace,
                    private_dwg=drawing,
                    manifest_path=manifest,
                    config=config(),
                    mode="write",
                    component_leases=components,
                )
            self.assertEqual(raised.exception.code, ErrorCode.NATIVE_CONSOLE_FAILURE)
            self.assertTrue(seen["job"].closed)


if __name__ == "__main__":
    unittest.main()
