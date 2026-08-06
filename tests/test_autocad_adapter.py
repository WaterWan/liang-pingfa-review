"""SDK-free structural and fail-closed checks for the licensed adapter source."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from liang_pingfa_review.canonical import (
    CanonicalJsonError,
    canonical_json_bytes,
    canonical_sha256,
)
from liang_pingfa_review.native_contracts import (
    MAX_NATIVE_GEOMETRY_JSON_BYTES,
    opaque_embedded_json_rules,
)
from liang_pingfa_review.native_bridge import validate_pipe_name


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PROJECT = (
    PROJECT_ROOT
    / "native-cad/src/LiangPingfa.NativeCad.AutoCAD.Adapter"
    / "LiangPingfa.NativeCad.AutoCAD.Adapter.csproj"
)
ADAPTER_TEST_PROJECT = (
    PROJECT_ROOT
    / "native-cad/tests/LiangPingfa.NativeCad.AutoCAD.Adapter.Tests"
    / "LiangPingfa.NativeCad.AutoCAD.Adapter.Tests.csproj"
)
CORE_TEST_PROJECT = (
    PROJECT_ROOT
    / "native-cad/tests/LiangPingfa.NativeCad.Core.Tests"
    / "LiangPingfa.NativeCad.Core.Tests.csproj"
)
ADAPTER_ROOT = ADAPTER_PROJECT.parent
AUTOCAD_ADAPTER_CAPABILITIES = [
    "create_review_marker/v1",
    "read.exact_geometry/v1",
    "read.inventory/v1",
    "translate_dbtext/v1",
]


def _run(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["dotnet", *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
    )


def _output(result: subprocess.CompletedProcess[bytes]) -> str:
    return (result.stdout + result.stderr).decode("utf-8", errors="replace")


class AutoCadAdapterSourceTests(unittest.TestCase):
    """These checks do not load Autodesk assemblies or inspect a DWG."""

    def test_project_has_explicit_profiles_and_fail_closed_sdk_gate(self) -> None:
        text = ADAPTER_PROJECT.read_text(encoding="utf-8")
        for profile, framework in (
            ("autocad2024", "net48"),
            ("tssd2024", "net48"),
            ("autocad2025", "net8.0-windows"),
            ("tssd2025", "net8.0-windows"),
            ("autocad2026", "net8.0-windows"),
            ("tssd2026", "net8.0-windows"),
        ):
            with self.subTest(profile=profile):
                self.assertIn(profile, text)
                self.assertIn(framework, text)
        for required in (
            "BuildAutoCadAdapter",
            "UseAutodeskApiStubs",
            "CadHostProfile",
            "CadSdkDir",
            "AcMgd.dll",
            "AcDbMgd.dll",
            "AcCoreMgd.dll",
            "<Private>false</Private>",
            "<CopyLocal>false</CopyLocal>",
            "RejectAutoCadAdapterPack",
            "RejectAutoCadAdapterPublish",
            "RejectCopiedAutodeskAssemblies",
        ):
            self.assertIn(required, text)
        self.assertNotIn("PackageReference", text)

    def test_net8_stub_profiles_compile_exact_adapter_source(self) -> None:
        for profile in ("autocad2025", "autocad2026"):
            with self.subTest(profile=profile):
                result = _run(
                    "build",
                    str(ADAPTER_PROJECT),
                    "-c",
                    "Release",
                    "--nologo",
                    "-p:BuildAutoCadAdapter=true",
                    "-p:UseAutodeskApiStubs=true",
                    f"-p:CadHostProfile={profile}",
                )
                self.assertEqual(0, result.returncode, _output(result))

        output = ADAPTER_ROOT / "bin/Release/net8.0-windows"
        for forbidden in (
            "AcMgd.dll",
            "AcDbMgd.dll",
            "AcCoreMgd.dll",
            "LiangPingfa.NativeCad.AutoCAD.ApiStubs.dll",
        ):
            self.assertFalse((output / forbidden).exists(), forbidden)

    def test_net48_stub_profile_compiles_when_targeting_pack_is_present(self) -> None:
        program_files_x86 = os.environ.get("ProgramFiles(x86)", "")
        targeting_pack = (
            Path(program_files_x86)
            / "Reference Assemblies"
            / "Microsoft"
            / "Framework"
            / ".NETFramework"
            / "v4.8"
        )
        if not targeting_pack.is_dir():
            self.skipTest("net48 targeting pack is not installed on this worker")
        result = _run(
            "build",
            str(ADAPTER_PROJECT),
            "-c",
            "Release",
            "--nologo",
            "-p:BuildAutoCadAdapter=true",
            "-p:UseAutodeskApiStubs=true",
            "-p:CadHostProfile=autocad2024",
        )
        self.assertEqual(0, result.returncode, _output(result))

    def test_real_mode_missing_sdk_fails_before_compile(self) -> None:
        result = _run(
            "build",
            str(ADAPTER_PROJECT),
            "-c",
            "Release",
            "--nologo",
            "-p:BuildAutoCadAdapter=true",
            "-p:UseAutodeskApiStubs=false",
            "-p:CadHostProfile=autocad2025",
            "-p:CadSdkDir="
            + os.fspath(
                Path(os.environ.get("SystemDrive", "C:") + os.sep)
                / "liang-pingfa-missing-autodesk-sdk"
            ),
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("CadSdkDir does not exist", _output(result))

    def test_reflection_and_static_source_checks_run_without_host(self) -> None:
        result = _run(
            "run",
            "--project",
            str(ADAPTER_TEST_PROJECT),
            "-c",
            "Release",
            "--nologo",
        )
        self.assertEqual(0, result.returncode, _output(result))
        self.assertIn("PASS:", _output(result))

    def test_generated_adapter_pipe_tokens_satisfy_python_grammar(self) -> None:
        build = _run("build", str(ADAPTER_TEST_PROJECT), "-c", "Release", "--nologo")
        self.assertEqual(0, build.returncode, _output(build))
        result = _run(
            "run",
            "--project",
            str(ADAPTER_TEST_PROJECT),
            "-c",
            "Release",
            "--no-build",
            "--",
            "pipe-tokens",
            "128",
        )
        self.assertEqual(0, result.returncode, _output(result))
        tokens = _output(result).splitlines()
        self.assertEqual(128, len(tokens))
        for pipe_name in tokens:
            with self.subTest(pipe_name=pipe_name):
                self.assertEqual(pipe_name, validate_pipe_name(pipe_name))

    def test_csharp_adapter_capabilities_match_python_exactly(self) -> None:
        """Wire order is protocol identity, not an implementation detail."""

        build = _run("build", str(ADAPTER_TEST_PROJECT), "-c", "Release", "--nologo")
        self.assertEqual(0, build.returncode, _output(build))
        result = _run(
            "run",
            "--project",
            str(ADAPTER_TEST_PROJECT),
            "-c",
            "Release",
            "--no-build",
            "--",
            "canonical-capabilities",
        )
        self.assertEqual(0, result.returncode, _output(result))
        self.assertEqual(
            AUTOCAD_ADAPTER_CAPABILITIES,
            json.loads(_output(result)),
        )

    def test_source_contains_real_database_flow_and_no_ui_escape_hatch(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(ADAPTER_ROOT.glob("*.cs"))
        )
        for required in (
            "IExtensionApplication",
            "LPF_NATIVE_BRIDGE_BOOTSTRAP",
            "LPF_NATIVE_EXECUTE_MANIFEST",
            "LPF_NATIVE_EXPORT_MANIFEST",
            "GetObjectId",
            "DBText",
            "StartTransaction",
            "Commit",
            "Abort",
            "SaveAs",
            "ReadDwgFile",
            "ExecuteInCommandContextAsync",
            "CreateNamedPipe",
            "PipeRejectRemoteClients",
            "GetNamedPipeClientProcessId",
        ):
            self.assertIn(required, source)
        for forbidden in (
            "Editor.Command",
            "SendStringToExecute",
            "SendKeys",
            "SendInput",
            "GetSelection",
            "Marshal.GetActiveObject",
            "AcadApplication",
            "RunCommand",
            "GeometricExtents",
        ):
            self.assertNotIn(forbidden, source)
        core = (
            PROJECT_ROOT / "native-cad/src/LiangPingfa.NativeCad.Core/ManifestExecution.cs"
        ).read_text(encoding="utf-8")
        protocol = (
            PROJECT_ROOT / "native-cad/src/LiangPingfa.NativeCad.Protocol/ProtocolV2.cs"
        ).read_text(encoding="utf-8")
        commands = (ADAPTER_ROOT / "NativePluginCommands.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn("PortablePrewriteProjectionV2", core)
        self.assertIn("PortablePrewriteProjection", source)
        self.assertIn("MaxConsoleExportBytes = 32 * 1024 * 1024", protocol)
        self.assertIn("CanonicalizeConsoleExportPayload", commands)
        self.assertIn("NativeCadCanonicalJsonProfiles.ConsoleExport", commands)
        self.assertIn("CanonicalJsonOptions.Strict", commands)

    def test_unattended_fresh_readback_is_silent_and_read_only(self) -> None:
        """The Core Console path must never wait for code-page consent."""

        database = (ADAPTER_ROOT / "AutodeskCadDatabase.cs").read_text(
            encoding="utf-8"
        )
        self.assertIsNone(
            re.search(
                r"ReadDwgFile\s*\(\s*privatePath\s*,\s*"
                r"FileOpenMode\.OpenForReadAndAllShare\s*,\s*false\s*,",
                database,
                flags=re.DOTALL,
            ),
            "allowCPConversion:false is dialog-capable in unattended readback",
        )
        self.assertIsNotNone(
            re.search(
                r"ReadDwgFile\s*\(\s*privatePath\s*,\s*"
                r"FileOpenMode\.OpenForReadAndAllShare\s*,\s*true\s*,\s*"
                r"string\.Empty\s*\)",
                database,
                flags=re.DOTALL,
            ),
            "fresh readback must use documented silent code-page conversion",
        )
        self.assertNotIn("reopened.SaveAs", database)

    def test_initial_autocad_write_profile_excludes_delete_before_transaction(self) -> None:
        """AutoCAD preserves only operations with a real-host-safe v2 contract."""

        identity = (ADAPTER_ROOT / "AdapterIdentity.cs").read_text(encoding="utf-8")
        reader = (ADAPTER_ROOT / "ManifestProjectionReader.cs").read_text(
            encoding="utf-8"
        )
        database = (ADAPTER_ROOT / "AutodeskCadDatabase.cs").read_text(
            encoding="utf-8"
        )
        commands = (ADAPTER_ROOT / "NativePluginCommands.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn('"translate_dbtext/v1"', identity)
        self.assertIn('"create_review_marker/v1"', identity)
        self.assertNotIn('"delete_auxiliary_overlay_text/v1"', identity)
        self.assertNotIn(".Erase()", database)
        delete_branch = reader.index('"delete_auxiliary_overlay_text"')
        self.assertIn("LPF_UNSUPPORTED_OPERATION", reader[delete_branch:])
        self.assertNotIn(
            "new DeleteAuxiliaryOverlayTextOperationV2",
            reader[delete_branch:],
        )
        self.assertLess(
            commands.index("ManifestProjectionReader.Read("),
            commands.index("NativeCommandRuntime.CreateDatabase("),
        )

    def test_console_export_uses_adapter_core_and_python_carrier_profile(self) -> None:
        """Exercise the adapter writer path against generated opaque carriers."""

        for project in (ADAPTER_TEST_PROJECT, CORE_TEST_PROJECT):
            result = _run("build", str(project), "-c", "Release", "--nologo")
            self.assertEqual(0, result.returncode, _output(result))

        rules = opaque_embedded_json_rules("console_export")

        def payload_for(carrier: str) -> dict[str, object]:
            payload: dict[str, object] = {"geometry_json": carrier}
            payload["integrity"] = {
                "algorithm": "SHA-256",
                "sha256": canonical_sha256(
                    payload,
                    opaque_string_rules=rules,
                ),
            }
            return payload

        exact_multibyte = (
            "中" * (MAX_NATIVE_GEOMETRY_JSON_BYTES // len("中".encode("utf-8")))
            + "a"
            * (
                MAX_NATIVE_GEOMETRY_JSON_BYTES
                % len("中".encode("utf-8"))
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            for label, carrier in (
                ("65537-char", "a" * 65_537),
                ("16mib-multibyte", exact_multibyte),
            ):
                with self.subTest(label=label):
                    payload = payload_for(carrier)
                    expected = canonical_json_bytes(
                        payload,
                        opaque_string_rules=rules,
                    )
                    path = temporary_path / f"{label}.json"
                    path.write_bytes(expected)
                    adapter = _run(
                        "run",
                        "--project",
                        str(ADAPTER_TEST_PROJECT),
                        "-c",
                        "Release",
                        "--no-build",
                        "--",
                        "canonical-console-export",
                        str(path),
                    )
                    core = _run(
                        "run",
                        "--project",
                        str(CORE_TEST_PROJECT),
                        "-c",
                        "Release",
                        "--no-build",
                        "--",
                        "canonical-profile",
                        "console-export",
                        str(path),
                    )
                    self.assertEqual(0, adapter.returncode, _output(adapter))
                    self.assertEqual(0, core.returncode, _output(core))
                    expected_hash = sha256(expected).hexdigest()
                    for result in (adapter, core):
                        observed = json.loads(_output(result))
                        self.assertEqual(expected_hash, observed["canonical_sha256"])
                        self.assertEqual(len(expected), observed["canonical_utf8_bytes"])

            # The carrier contains multibyte content but crosses the raw
            # opaque UTF-8 bound by one ASCII byte. The adapter must reject
            # before it can write an oversized outer export.
            cap_plus_one = exact_multibyte + "a"
            path = temporary_path / "cap-plus-one.json"
            path.write_text(
                json.dumps(
                    {"geometry_json": cap_plus_one},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            with self.assertRaises(CanonicalJsonError):
                canonical_json_bytes(
                    {"geometry_json": cap_plus_one},
                    opaque_string_rules=rules,
                )
            rejected = _run(
                "run",
                "--project",
                str(ADAPTER_TEST_PROJECT),
                "-c",
                "Release",
                "--no-build",
                "--",
                "canonical-console-export",
                str(path),
            )
            self.assertNotEqual(0, rejected.returncode, _output(rejected))
            core_rejected = _run(
                "run",
                "--project",
                str(CORE_TEST_PROJECT),
                "-c",
                "Release",
                "--no-build",
                "--",
                "canonical-profile",
                "console-export",
                str(path),
            )
            self.assertNotEqual(0, core_rejected.returncode, _output(core_rejected))

    def test_checkpoint_two_session_bootstrap_and_state_guards(self) -> None:
            bridge = (ADAPTER_ROOT / "NativePipeBridge.cs").read_text(encoding="utf-8")
            commands = (ADAPTER_ROOT / "NativePluginCommands.cs").read_text(
                encoding="utf-8"
            )
            advertisement = bridge.split(
                "internal static class NativeBridgeAdvertisement", 1
            )[1].split("internal static class NativePipeFactory", 1)[0]
            self.assertIn("BindRequestSession", bridge)
            self.assertIn("The first bridge request must be health.", bridge)
            self.assertNotIn('sessionId = "native-session-"', bridge)
            self.assertNotIn('"session_id"', advertisement)
            for field in (
                '"pid"',
                '"pipe"',
                '"protocol_version"',
                '"mode", "read_only"',
                '"adapter"',
                '"plugin"',
                '"capabilities"',
                '"expires_at"',
                '"nonce"',
            ):
                self.assertIn(field, advertisement)
            self.assertNotIn(".GetAwaiter().GetResult()", commands)
            self.assertNotIn(".Wait()", commands)
            self.assertNotIn(".GetAwaiter().GetResult()", bridge)
            self.assertNotIn(".Wait()", bridge)

    def test_checkpoint_two_actual_handles_fields_and_cached_bindings(self) -> None:
            core = (
                PROJECT_ROOT / "native-cad/src/LiangPingfa.NativeCad.Core/ManifestExecution.cs"
            ).read_text(encoding="utf-8")
            in_memory = (
                PROJECT_ROOT / "native-cad/src/LiangPingfa.NativeCad.Core/InMemoryCadDatabase.cs"
            ).read_text(encoding="utf-8")
            database = (ADAPTER_ROOT / "AutodeskCadDatabase.cs").read_text(
                encoding="utf-8"
            )
            paths = (ADAPTER_ROOT / "PrivatePaths.cs").read_text(encoding="utf-8")
            stubs = (
                PROJECT_ROOT
                / "native-cad/src/LiangPingfa.NativeCad.AutoCAD.ApiStubs/AutodeskApiStubs.cs"
            ).read_text(encoding="utf-8")
            self.assertIn("MarkerAppendRequestV2", core)
            self.assertNotIn("NextGeneratedHandle", core)
            self.assertIn("CadEntitySnapshot AppendExact", in_memory)
            self.assertIn("ObjectId markerId = record.AppendEntity(marker)", database)
            self.assertIn("request.WithActualHandle", database)
            self.assertIn("marker_handle", core)
            self.assertIn("HasFields", database)
            self.assertIn("DbTextFieldPolicy", database)
            self.assertIn("public bool HasFields", stubs)
            self.assertIn("public ObjectId GetField()", stubs)
            self.assertIn("RetainedPrivateDwgBinding", paths)
            transaction = database.split(
                "internal sealed class AutodeskCadTransaction", 1
            )[1]
            self.assertIn("privateBinding.CachedBinding", transaction)
            self.assertNotIn("NativeSourceBindingCapture.Capture(", transaction)

    def test_including_erased_stub_uses_documented_block_record_shape(self) -> None:
        database = (ADAPTER_ROOT / "AutodeskCadDatabase.cs").read_text(
            encoding="utf-8"
        )
        stubs = (
            PROJECT_ROOT
            / "native-cad/src/LiangPingfa.NativeCad.AutoCAD.ApiStubs/AutodeskApiStubs.cs"
        ).read_text(encoding="utf-8")
        self.assertIn("public BlockTableRecord IncludingErased", stubs)
        self.assertNotIn("public IEnumerable IncludingErased", stubs)
        self.assertIn(
            "BlockTableRecord erasedInclusiveRecord = record.IncludingErased",
            database,
        )
        self.assertIn(
            "BlockTableRecord erasedInclusiveRecord =\n                container.Record.IncludingErased",
            database,
        )

    def test_v2_adapter_carries_explicit_physical_container_counts(self) -> None:
        database = (ADAPTER_ROOT / "AutodeskCadDatabase.cs").read_text(
            encoding="utf-8"
        )
        core = (
            PROJECT_ROOT / "native-cad/src/LiangPingfa.NativeCad.Core/ExactCadExporter.cs"
        ).read_text(encoding="utf-8")
        self.assertIn("new CadContainerPhysicalSlots(", database)
        self.assertIn("container.PhysicalSlotCount", database)
        self.assertIn("expectedPhysicalContainer", database)
        self.assertIn('"physical_slot_count"', core)
        self.assertIn("snapshot.Containers", core)

    def test_checkpoint_two_basetext_and_document_state_gates(self) -> None:
            database = (ADAPTER_ROOT / "AutodeskCadDatabase.cs").read_text(
                encoding="utf-8"
            )
            bridge = (ADAPTER_ROOT / "NativePipeBridge.cs").read_text(
                encoding="utf-8"
            )
            gate = (ADAPTER_ROOT / "AutodeskDocumentReadGate.cs").read_text(
                encoding="utf-8"
            )
            stubs = (
                PROJECT_ROOT
                / "native-cad/src/LiangPingfa.NativeCad.AutoCAD.ApiStubs/AutodeskApiStubs.cs"
            ).read_text(encoding="utf-8")
            self.assertIn("DbTextAlignmentPolicy.RequireBaseLeft(text)", database)
            self.assertIn("RequireBaseLeftModes(", database)
            self.assertIn("text.Position = position;", database)
            self.assertIn(
                "Position = AutodeskSnapshotExporter.ToPoint(markerOperation.Position)",
                database,
            )
            self.assertNotIn("AlignmentPoint", database)
            self.assertIn("Application.GetSystemVariable", gate)
            self.assertIn('ReadIntegerSystemVariable("DWGTITLED")', gate)
            self.assertIn('ReadIntegerSystemVariable("DBMOD")', gate)
            self.assertNotIn("document.Saved", gate)
            self.assertNotIn("database.TransactionManager.TopTransaction", gate)
            self.assertNotIn("NumberOfActiveTransactions", gate)
            self.assertIn("database.FingerprintGuid", gate)
            self.assertIn("database.VersionGuid", gate)
            self.assertIn("NativeSourceBindingCapture.Capture(documentPath)", gate)
            self.assertIn("AutodeskDocumentReadGate.Capture(document);", bridge)
            self.assertIn(
                "before.RequireUnchanged(AutodeskDocumentReadGate.Capture(document))",
                bridge,
            )
            exporter = database.split(
                "internal static class AutodeskSnapshotExporter", 1
            )[1]
            self.assertNotIn("MarkerPolicyBindingV2", exporter)
            self.assertNotIn("RequireBinding(),\n                            null,", bridge)
            for required_stub in (
                "public static object GetSystemVariable",
                "public Guid FingerprintGuid",
                "public Guid VersionGuid",
            ):
                self.assertIn(required_stub, stubs)
            self.assertNotIn("public bool Saved", stubs)
            self.assertNotIn("public Transaction TopTransaction", stubs)

    def test_no_vendor_binary_is_present_in_source_tree(self) -> None:
        suffixes = {".dll", ".exe", ".bundle", ".nupkg", ".zip"}
        present = [
            path.relative_to(PROJECT_ROOT).as_posix()
            for path in ADAPTER_ROOT.rglob("*")
            if path.is_file()
            and not {"bin", "obj"}.intersection(path.relative_to(ADAPTER_ROOT).parts)
            and path.suffix.casefold() in suffixes
        ]
        self.assertEqual([], present)


if __name__ == "__main__":
    unittest.main()
