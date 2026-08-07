"""Public structural checks for the SDK-free executable native CAD core."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

from liang_pingfa_review.canonical import (
    CanonicalJsonError,
    attach_integrity,
    canonical_json_bytes,
    canonical_sha256,
    strict_json_loads,
)
from liang_pingfa_review.errors import PipelineError
from liang_pingfa_review.native_audit import build_native_audit
from liang_pingfa_review.native_contracts import (
    bits_from_float,
    canonical_native_contract_bytes,
    MAX_NATIVE_GEOMETRY_JSON_BYTES,
    opaque_embedded_json_rules,
    translate_binary64_bits,
    validate_native_contract,
)
from liang_pingfa_review.native_manifest import build_native_manifest
from liang_pingfa_review.native_plan import generate_native_plan
from liang_pingfa_review.native_protocol import derive_challenge_response
from liang_pingfa_review.native_verify import (
    geometry_from_console_export,
    validate_console_result,
)
from tests.support.synthetic_native import config, entity, geometry, intent, session


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NATIVE_CAD_ROOT = PROJECT_ROOT / "native-cad"
FIXTURE_PATH = NATIVE_CAD_ROOT / "tests" / "fixtures" / "native-cad-v2-golden.json"
NATIVE_CAD_SOLUTION = NATIVE_CAD_ROOT / "LiangPingfa.NativeCad.sln"
STUB_PROJECT = (
    NATIVE_CAD_ROOT
    / "src"
    / "LiangPingfa.NativeCad.AutoCAD.ApiStubs"
    / "LiangPingfa.NativeCad.AutoCAD.ApiStubs.csproj"
)
CORE_TEST_PROJECT = (
    NATIVE_CAD_ROOT
    / "tests"
    / "LiangPingfa.NativeCad.Core.Tests"
    / "LiangPingfa.NativeCad.Core.Tests.csproj"
)


def _renew_marker_session(value: dict) -> dict:
    """Create the distinct but compatible renewed session required for writes."""

    renewed = deepcopy(value)
    renewed["session_id"] = "native-session-" + "f" * 32
    renewed["pid"] += 1
    renewed["client_nonce"] = "f" * 43
    renewed["challenge"] = "g" * 43
    renewed["bridge_nonce"] = "h" * 43
    renewed["challenge_response"] = derive_challenge_response(
        renewed["client_nonce"],
        renewed["challenge"],
        renewed["bridge_nonce"],
        session_id=renewed["session_id"],
    )
    renewed["process"]["instance_fingerprint"] = canonical_sha256(
        {"renewed_session": renewed["session_id"]}
    )
    renewed["current_document"]["database_instance_fingerprint"] = canonical_sha256(
        {"renewed_database": renewed["session_id"]}
    )
    renewed["current_document"]["revision_fingerprint"] = canonical_sha256(
        {"renewed_revision": renewed["session_id"]}
    )
    return attach_integrity(renewed)


def _generated_marker_manifest() -> dict:
    """Build one source-free Python manifest whose marker maps to the C# runner."""

    before = geometry([entity("10", layer="OTHER")])
    audit_session = session()
    native_config = config()
    native_config["operation_profiles"]["create_review_marker/v1"] = True
    native_config["marker_policy"]["enabled"] = True
    native_config["marker_policy"]["plugin_capability"] = True
    audit = build_native_audit(before, audit_session, native_config)
    private_intent = intent(
        audit,
        operations=[
            {
                "operation_id": "native-operation-" + "a" * 24,
                "kind": "create_review_marker",
                "position": [
                    bits_from_float(7),
                    bits_from_float(8),
                    bits_from_float(0),
                ],
            }
        ],
    )
    plan = generate_native_plan(audit, private_intent, native_config)
    fresh_session = _renew_marker_session(audit_session)
    fresh_export = geometry(
        deepcopy(before["entities"]),
        source_value=deepcopy(before["source"]),
        session_value=fresh_session,
    )
    return build_native_manifest(
        audit,
        plan,
        private_intent,
        fresh_export,
        fresh_session,
        native_config,
        private_source_copy={
            "sha256": before["source"]["sha256"],
            "byte_size": before["source"]["byte_size"],
            "file_identity_fingerprint": "b" * 64,
        },
        output_path=Path("generated-marker-output.dwg"),
    )


class NativeCadCoreCheckpointTests(unittest.TestCase):
    """Prove only source-free checkpoint-one structure and shared vectors."""

    @staticmethod
    def _command_output(completed: subprocess.CompletedProcess[bytes]) -> str:
        """Decode the runner's explicit UTF-8 JSON independent of Windows ACP."""

        return (completed.stdout + completed.stderr).decode("utf-8", errors="replace")

    @staticmethod
    def _canonical_depth_json(shape: str, depth: int) -> str:
        """Build one source-free shared parser/serializer boundary vector."""

        if depth < 1:
            raise ValueError("depth must be positive")
        if shape == "empty-arrays":
            value = "[]"
            for _ in range(1, depth):
                value = "[" + value + "]"
            return value
        if shape == "empty-objects":
            value = "{}"
            for _ in range(1, depth):
                value = '{"node":' + value + "}"
            return value
        if shape == "mixed-containers":
            value = "[]"
            for index in range(depth - 1):
                value = (
                    '{"node":' + value + "}"
                    if index % 2 == 0
                    else "[" + value + "]"
                )
            return value
        if shape == "scalar-leaves":
            value = "0"
            for index in range(depth):
                value = (
                    '{"node":' + value + "}"
                    if index % 2 == 0
                    else "[" + value + "]"
                )
            return value
        raise ValueError(f"unknown depth-vector shape: {shape}")

    @staticmethod
    def _canonical_depth_value(shape: str, depth: int) -> object:
        """Build the matching in-memory value to exercise serialization."""

        if depth < 1:
            raise ValueError("depth must be positive")
        if shape == "empty-arrays":
            value: object = []
            for _ in range(1, depth):
                value = [value]
            return value
        if shape == "empty-objects":
            value = {}
            for _ in range(1, depth):
                value = {"node": value}
            return value
        if shape == "mixed-containers":
            value = []
            for index in range(depth - 1):
                value = {"node": value} if index % 2 == 0 else [value]
            return value
        if shape == "scalar-leaves":
            value = 0
            for index in range(depth):
                value = {"node": value} if index % 2 == 0 else [value]
            return value
        raise ValueError(f"unknown depth-vector shape: {shape}")

    @classmethod
    def setUpClass(cls) -> None:
        """Build the C# vector runner that this Python suite executes."""

        completed = subprocess.run(
            [
                "dotnet",
                "build",
                str(NATIVE_CAD_SOLUTION),
                "-c",
                "Release",
                "--nologo",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(
                "C# canonical vector runner failed to build:\n"
                + cls._command_output(completed)
            )

    def test_projects_are_sdk_free_and_reference_only_project_source(self) -> None:
        projects = {
            "protocol": NATIVE_CAD_ROOT
            / "src"
            / "LiangPingfa.NativeCad.Protocol"
            / "LiangPingfa.NativeCad.Protocol.csproj",
            "core": NATIVE_CAD_ROOT
            / "src"
            / "LiangPingfa.NativeCad.Core"
            / "LiangPingfa.NativeCad.Core.csproj",
            "stubs": NATIVE_CAD_ROOT
            / "src"
            / "LiangPingfa.NativeCad.AutoCAD.ApiStubs"
            / "LiangPingfa.NativeCad.AutoCAD.ApiStubs.csproj",
            "tests": NATIVE_CAD_ROOT
            / "tests"
            / "LiangPingfa.NativeCad.Core.Tests"
            / "LiangPingfa.NativeCad.Core.Tests.csproj",
        }
        expected_frameworks = {
            "protocol": "netstandard2.0",
            "core": "netstandard2.0",
            "stubs": "netstandard2.0",
            "tests": "net8.0",
        }
        expected_modes = {
            "protocol": "checkpoint-1-protocol",
            "core": "checkpoint-1-core",
            "stubs": "syntax-only-stub",
            "tests": "checkpoint-1-tests",
        }
        policy_import = r'<Import Project="..\..\NativeCad.RepositoryPolicy.targets" />'
        for name, path in projects.items():
            with self.subTest(project=name):
                text = path.read_text(encoding="utf-8")
                self.assertIn(
                    f"<TargetFramework>{expected_frameworks[name]}</TargetFramework>",
                    text,
                )
                lowered = text.casefold()
                for forbidden in (
                    "packagereference",
                    "<reference",
                    "<hintpath",
                    "autodesk",
                    "teigha",
                    "tssd",
                    "oda",
                ):
                    self.assertNotIn(forbidden, lowered)
                self.assertEqual(1, text.count(policy_import))
                self.assertTrue(
                    text.rstrip().endswith(policy_import + "\n</Project>"),
                    "the unconditional policy import is the final project element",
                )
                self.assertIn(
                    f"<NativeCadPolicyMode>{expected_modes[name]}</NativeCadPolicyMode>",
                    text,
                )

        core = projects["core"].read_text(encoding="utf-8")
        tests = projects["tests"].read_text(encoding="utf-8")
        self.assertIn("LiangPingfa.NativeCad.Protocol.csproj", core)
        self.assertIn("LiangPingfa.NativeCad.Core.csproj", tests)
        self.assertIn("LiangPingfa.NativeCad.Protocol.csproj", tests)
        self.assertIn("LiangPingfa.NativeCad.AutoCAD.ApiStubs.csproj", tests)

    def test_explicit_repository_policy_survives_global_import_suppression(
        self,
    ) -> None:
        """Directory.Build bypasses and global-property overrides must fail closed."""

        policy = NATIVE_CAD_ROOT / "NativeCad.RepositoryPolicy.targets"
        policy_text = policy.read_text(encoding="utf-8")
        self.assertIn("EnforceNativeCadRepositoryPolicy", policy_text)
        self.assertIn("RejectUnapprovedNativeCadDependencies", policy_text)
        self.assertIn("TreatWarningsAsErrors", policy_text)
        self.assertIn("NativeCadPolicyMode", policy_text)
        self.assertIn("HintPath", policy_text)

        probes = (
            (
                "directory-build-import-suppression",
                [
                    "-p:ImportDirectoryBuildProps=false",
                    "-p:ImportDirectoryBuildTargets=false",
                ],
            ),
            ("global-target-framework", ["-p:TargetFramework=net8.0"]),
            ("global-warning-policy", ["-p:TreatWarningsAsErrors=false"]),
        )
        core = (
            NATIVE_CAD_ROOT
            / "src"
            / "LiangPingfa.NativeCad.Core"
            / "LiangPingfa.NativeCad.Core.csproj"
        )
        for name, properties in probes:
            with self.subTest(probe=name):
                completed = subprocess.run(
                    [
                        "dotnet",
                        "build",
                        str(core),
                        "-c",
                        "Release",
                        "--nologo",
                        *properties,
                    ],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(0, completed.returncode, self._command_output(completed))
                self.assertIn(
                    "NativeCadRepositoryPolicy:",
                    self._command_output(completed),
                )

    def test_owner_digests_bind_unused_ordered_records(self) -> None:
        """Owner additions, removals, reorders, and changes cannot reuse a digest."""

        before = geometry([entity("10")], owners=["AA", "AB"])
        for name, owners in (
            ("added", ["AA", "AB", "AC"]),
            ("removed", ["AA"]),
            ("reordered", ["AB", "AA"]),
            ("changed-unused", ["AA", "AC"]),
        ):
            with self.subTest(change=name):
                valid_changed = geometry([entity("10")], owners=owners)
                self.assertNotEqual(
                    before["document"]["protected_state_digest"],
                    valid_changed["document"]["protected_state_digest"],
                )
                if name == "reordered":
                    self.assertNotEqual(
                        before["document"]["protected_order_digest"],
                        valid_changed["document"]["protected_order_digest"],
                    )

                forged = deepcopy(before)
                forged["owners"] = owners
                forged = attach_integrity(forged)
                with self.assertRaises(PipelineError):
                    validate_native_contract("geometry", forged)

    def test_repository_policy_rejects_package_before_restore_can_proceed(
        self,
    ) -> None:
        """The explicit policy blocks an injected package at the Restore boundary."""

        with tempfile.TemporaryDirectory() as temporary:
            copied_root = Path(temporary) / "native-cad"
            shutil.copytree(
                NATIVE_CAD_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns("bin", "obj"),
            )
            core = (
                copied_root
                / "src"
                / "LiangPingfa.NativeCad.Core"
                / "LiangPingfa.NativeCad.Core.csproj"
            )
            policy_import = r'<Import Project="..\..\NativeCad.RepositoryPolicy.targets" />'
            core.write_text(
                core.read_text(encoding="utf-8").replace(
                    policy_import,
                    '  <ItemGroup><PackageReference Include="generated" Version="1.0.0" /></ItemGroup>\n'
                    + "  "
                    + policy_import,
                    1,
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    "dotnet",
                    "build",
                    str(core),
                    "-c",
                    "Release",
                    "--nologo",
                ],
                cwd=Path(temporary),
                capture_output=True,
                check=False,
            )
            output = self._command_output(completed)
            self.assertNotEqual(0, completed.returncode, output)
            self.assertIn("NativeCadRepositoryPolicy:", output)
            self.assertIn("PackageReference", output)

    def test_stubs_are_prominently_syntax_only_and_not_runtime_code(self) -> None:
        source = (
            NATIVE_CAD_ROOT
            / "src"
            / "LiangPingfa.NativeCad.AutoCAD.ApiStubs"
            / "AutodeskApiStubs.cs"
        ).read_text(encoding="utf-8")
        project = (
            NATIVE_CAD_ROOT
            / "src"
            / "LiangPingfa.NativeCad.AutoCAD.ApiStubs"
            / "LiangPingfa.NativeCad.AutoCAD.ApiStubs.csproj"
        ).read_text(encoding="utf-8")
        for required in (
            "SYNTAX-ONLY API STUBS",
            "original project source",
            "NOT DEPLOYABLE",
            "NotSupportedException",
            "namespace Autodesk.AutoCAD.Runtime",
            "namespace Autodesk.AutoCAD.ApplicationServices",
            "namespace Autodesk.AutoCAD.DatabaseServices",
            "namespace Autodesk.AutoCAD.Geometry",
        ):
            self.assertIn(required, source)
        self.assertIn("SYNTAX-ONLY, non-deployable", project)
        self.assertNotIn("<OutputType>Exe</OutputType>", project)
        self.assertNotIn("PackageReference", project)
        self.assertIn("<IsPackable>false</IsPackable>", project)
        self.assertIn("<IsPublishable>false</IsPublishable>", project)
        self.assertIn(
            "<GeneratePackageOnBuild>false</GeneratePackageOnBuild>",
            project,
        )
        self.assertIn("<IncludeBuildOutput>false</IncludeBuildOutput>", project)
        self.assertIn("RejectSyntaxOnlyStubPack", project)
        self.assertIn("RejectSyntaxOnlyStubPublish", project)

        syntax_project = CORE_TEST_PROJECT.read_text(encoding="utf-8")
        self.assertIn(
            "<Private>false</Private>",
            syntax_project,
        )
        self.assertIn(
            "<CopyLocal>false</CopyLocal>",
            syntax_project,
        )

    def test_syntax_stub_is_not_copied_to_compile_boundary_output(self) -> None:
        """The runner compiles declarations but carries no deployable stub DLL."""

        output_directory = CORE_TEST_PROJECT.parent / "bin" / "Release" / "net8.0"
        self.assertFalse(
            (
                output_directory
                / "LiangPingfa.NativeCad.AutoCAD.ApiStubs.dll"
            ).exists(),
        )

    def test_syntax_stub_pack_publish_are_rejected_and_core_excludes_it(self) -> None:
        """Stubs cannot pack/publish, while a core package contains no stub."""

        with tempfile.TemporaryDirectory() as temporary:
            package_directory = Path(temporary)
            rejected = subprocess.run(
                [
                    "dotnet",
                    "pack",
                    str(STUB_PROJECT),
                    "-c",
                    "Release",
                    "--nologo",
                    "-o",
                    str(package_directory),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(
                0,
                rejected.returncode,
                self._command_output(rejected),
            )
            self.assertIn("syntax-only", self._command_output(rejected).casefold())
            self.assertEqual([], list(package_directory.glob("*.nupkg")))

            publish_directory = package_directory / "publish"
            published = subprocess.run(
                [
                    "dotnet",
                    "publish",
                    str(STUB_PROJECT),
                    "-c",
                    "Release",
                    "--nologo",
                    "-o",
                    str(publish_directory),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(
                0,
                published.returncode,
                self._command_output(published),
            )
            self.assertIn("syntax-only", self._command_output(published).casefold())
            self.assertEqual(
                [],
                [path for path in publish_directory.rglob("*") if path.is_file()]
                if publish_directory.exists()
                else [],
            )

            core_project = (
                NATIVE_CAD_ROOT
                / "src"
                / "LiangPingfa.NativeCad.Core"
                / "LiangPingfa.NativeCad.Core.csproj"
            )
            packed = subprocess.run(
                [
                    "dotnet",
                    "pack",
                    str(core_project),
                    "-c",
                    "Release",
                    "--nologo",
                    "-o",
                    str(package_directory),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                packed.returncode,
                self._command_output(packed),
            )
            packages = list(package_directory.glob("*.nupkg"))
            self.assertEqual(1, len(packages))
            with zipfile.ZipFile(packages[0]) as package:
                self.assertFalse(
                    any(
                        "LiangPingfa.NativeCad.AutoCAD.ApiStubs"
                        in member
                        for member in package.namelist()
                    ),
                )

    def test_shared_golden_vectors_match_python_v1_canonical_helpers(self) -> None:
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        self.assertEqual("native-cad-v2", fixture["fixture_version"])
        self.assertIs(fixture["source_free"], True)
        self.assertEqual(
            {
                "legacy_manifest": "liang-pingfa/native-edit-manifest/v1",
                "manifest": "liang-pingfa/native-edit-manifest/v2",
                "console_result": "liang-pingfa/native-console-result/v2",
                "console_export": "liang-pingfa/native-console-export/v2",
                "verification": "liang-pingfa/native-verification/v2",
            },
            fixture["mutable_write_artifact_versions"],
        )
        payload = fixture["canonical_payload"]
        canonical = canonical_json_bytes(payload)
        self.assertEqual(fixture["canonical_json"].encode("utf-8"), canonical)
        self.assertEqual(fixture["canonical_sha256"], canonical_sha256(payload))
        self.assertEqual(
            fixture["canonical_sha256"],
            sha256(canonical).hexdigest(),
        )
        for vector in fixture["canonical_vectors"]:
            with self.subTest(vector=vector["name"]):
                vector_bytes = canonical_json_bytes(vector["payload"])
                self.assertEqual(
                    vector["canonical_json"].encode("utf-8"),
                    vector_bytes,
                )
                self.assertEqual(
                    vector["canonical_sha256"],
                    canonical_sha256(vector["payload"]),
                )
        expected_depth_vectors = {
            f"{shape}-depth-{depth}"
            for shape in (
                "empty-arrays",
                "empty-objects",
                "mixed-containers",
                "scalar-leaves",
            )
            for depth in (127, 128, 129)
        }
        self.assertEqual(
            expected_depth_vectors,
            {vector["name"] for vector in fixture["canonical_depth_vectors"]},
        )
        opaque_rules = opaque_embedded_json_rules("manifest")
        for vector in fixture["canonical_depth_vectors"]:
            with self.subTest(vector=vector["name"]):
                text = self._canonical_depth_json(
                    vector["shape"],
                    vector["depth"],
                )
                value = self._canonical_depth_value(
                    vector["shape"],
                    vector["depth"],
                )
                outer = {"preconditions_geometry_json": text}
                outer_text = canonical_json_bytes(
                    outer,
                    opaque_string_rules=opaque_rules,
                ).decode("utf-8")
                restored = strict_json_loads(
                    outer_text,
                    opaque_string_rules=opaque_rules,
                )
                self.assertEqual(text, restored["preconditions_geometry_json"])
                if vector["accepted"]:
                    self.assertEqual(
                        text.encode("utf-8"),
                        canonical_json_bytes(strict_json_loads(text)),
                    )
                    self.assertEqual(
                        text.encode("utf-8"),
                        canonical_json_bytes(value),
                    )
                    self.assertEqual(
                        text.encode("utf-8"),
                        canonical_json_bytes(
                            strict_json_loads(
                                restored["preconditions_geometry_json"]
                            )
                        ),
                    )
                else:
                    with self.assertRaises(CanonicalJsonError):
                        strict_json_loads(text)
                    with self.assertRaises(CanonicalJsonError):
                        canonical_json_bytes(value)
                    with self.assertRaises(CanonicalJsonError):
                        strict_json_loads(
                            restored["preconditions_geometry_json"]
                        )
        for vector in fixture["binary64_vectors"]:
            with self.subTest(vector=vector["original"]):
                self.assertEqual(
                    vector["translated"],
                    translate_binary64_bits(vector["original"], vector["delta"]),
                )
        self.assertEqual(
            {
                "max_json_nesting_depth": 128,
                "max_geometry_entities": 2000,
                "max_geometry_segments": 10000,
                "max_geometry_sequence_index": 1000000,
                "max_geometry_containers": 2001,
                "max_physical_slot_count": 1000001,
                "max_geometry_json_bytes": 16 * 1024 * 1024,
                "max_native_operations": 1024,
                "max_console_result_bytes": 256 * 1024,
                "max_console_result_canonical_bytes": 240 * 1024,
            },
            fixture["limits"],
        )

    def test_built_csharp_runner_matches_python_canonical_bytes_and_hashes(self) -> None:
        """Execute C# for every shared vector instead of trusting fixtures alone."""

        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            for index, vector in enumerate(fixture["canonical_vectors"]):
                with self.subTest(vector=vector["name"]):
                    payload_path = temporary_path / f"canonical-{index}.json"
                    payload_path.write_text(
                        json.dumps(
                            vector["payload"],
                            ensure_ascii=False,
                            separators=(",", ":"),
                            allow_nan=False,
                        ),
                        encoding="utf-8",
                    )
                    completed = subprocess.run(
                        [
                            "dotnet",
                            "run",
                            "--project",
                            str(CORE_TEST_PROJECT),
                            "-c",
                            "Release",
                            "--no-build",
                            "--",
                            "canonical",
                            str(payload_path),
                        ],
                        cwd=PROJECT_ROOT,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(
                        0,
                        completed.returncode,
                        self._command_output(completed),
                    )
                    response = json.loads(completed.stdout.decode("utf-8"))
                    expected_bytes = canonical_json_bytes(vector["payload"])
                    self.assertEqual(
                        expected_bytes,
                        response["canonical_json"].encode("utf-8"),
                    )
                    self.assertEqual(
                        sha256(expected_bytes).hexdigest(),
                        response["canonical_sha256"],
                    )

    def test_built_csharp_runner_matches_shared_depth_boundaries(
        self,
    ) -> None:
        """C# and Python agree on empty/nonempty 127/128/129 containers."""

        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        opaque_rules = opaque_embedded_json_rules("manifest")
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            for index, vector in enumerate(fixture["canonical_depth_vectors"]):
                with self.subTest(vector=vector["name"]):
                    text = self._canonical_depth_json(
                        vector["shape"],
                        vector["depth"],
                    )
                    inner_path = temporary_path / f"depth-inner-{index}.json"
                    inner_path.write_text(text, encoding="utf-8")
                    completed = subprocess.run(
                        [
                            "dotnet",
                            "run",
                            "--project",
                            str(CORE_TEST_PROJECT),
                            "-c",
                            "Release",
                            "--no-build",
                            "--",
                            "canonical",
                            str(inner_path),
                        ],
                        cwd=PROJECT_ROOT,
                        capture_output=True,
                        check=False,
                    )
                    if vector["accepted"]:
                        self.assertEqual(
                            0,
                            completed.returncode,
                            self._command_output(completed),
                        )
                        response = json.loads(completed.stdout.decode("utf-8"))
                        self.assertEqual(
                            text,
                            response["canonical_json"],
                        )
                        self.assertEqual(
                            sha256(text.encode("utf-8")).hexdigest(),
                            response["canonical_sha256"],
                        )
                    else:
                        self.assertNotEqual(
                            0,
                            completed.returncode,
                            self._command_output(completed),
                        )

                    # The outer manifest profile deliberately treats this as
                    # an exact opaque string. Once the carrier is decoded as
                    # JSON above, both languages apply the same inner cap.
                    outer = {"preconditions_geometry_json": text}
                    outer_path = temporary_path / f"depth-outer-{index}.json"
                    outer_path.write_text(
                        json.dumps(
                            outer,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ),
                        encoding="utf-8",
                    )
                    outer_completed = subprocess.run(
                        [
                            "dotnet",
                            "run",
                            "--project",
                            str(CORE_TEST_PROJECT),
                            "-c",
                            "Release",
                            "--no-build",
                            "--",
                            "canonical-profile",
                            "manifest",
                            str(outer_path),
                        ],
                        cwd=PROJECT_ROOT,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(
                        0,
                        outer_completed.returncode,
                        self._command_output(outer_completed),
                    )
                    expected_outer = canonical_json_bytes(
                        outer,
                        opaque_string_rules=opaque_rules,
                    )
                    outer_response = json.loads(
                        outer_completed.stdout.decode("utf-8")
                    )
                    self.assertEqual(
                        sha256(expected_outer).hexdigest(),
                        outer_response["canonical_sha256"],
                    )
                    self.assertEqual(
                        len(expected_outer),
                        outer_response["canonical_utf8_bytes"],
                    )

    def test_built_csharp_runner_rejects_unsupported_number_spellings(self) -> None:
        """C# rejects numbers whose Python wire spelling it does not implement."""

        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            payload_path = Path(temporary) / "unsupported-number.json"
            for token in fixture["rejected_number_tokens"]:
                with self.subTest(token=token):
                    payload_path.write_text(token, encoding="utf-8")
                    completed = subprocess.run(
                        [
                            "dotnet",
                            "run",
                            "--project",
                            str(CORE_TEST_PROJECT),
                            "-c",
                            "Release",
                            "--no-build",
                            "--",
                            "canonical",
                            str(payload_path),
                        ],
                        cwd=PROJECT_ROOT,
                        capture_output=True,
                        check=False,
                    )
                    self.assertNotEqual(
                        0,
                        completed.returncode,
                        self._command_output(completed),
                    )

    def test_built_csharp_runner_matches_path_aware_opaque_carrier_vectors(
        self,
    ) -> None:
        """C# preserves only Python's exact v1 carrier paths and byte caps."""

        profile = "manifest"
        rules = opaque_embedded_json_rules("manifest")
        combining_carrier = '"中😀\u0344"'
        vectors = (
            (
                "65537-bytes",
                {"preconditions_geometry_json": "a" * 65_537},
                True,
            ),
            (
                "exact-16mib",
                {
                    "preconditions_geometry_json": "a"
                    * MAX_NATIVE_GEOMETRY_JSON_BYTES
                },
                True,
            ),
            (
                "above-16mib",
                {
                    "preconditions_geometry_json": "a"
                    * (MAX_NATIVE_GEOMETRY_JSON_BYTES + 1)
                },
                False,
            ),
            (
                "chinese-astral-combining",
                {"preconditions_geometry_json": combining_carrier},
                True,
            ),
            (
                "same-key-wrong-path",
                {"nested": {"preconditions_geometry_json": combining_carrier}},
                False,
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            for index, (name, payload, accepted) in enumerate(vectors):
                with self.subTest(vector=name):
                    payload_path = temporary_path / f"opaque-{index}.json"
                    payload_path.write_text(
                        json.dumps(
                            payload,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ),
                        encoding="utf-8",
                    )
                    completed = subprocess.run(
                        [
                            "dotnet",
                            "run",
                            "--project",
                            str(CORE_TEST_PROJECT),
                            "-c",
                            "Release",
                            "--no-build",
                            "--",
                            "canonical-profile",
                            profile,
                            str(payload_path),
                        ],
                        cwd=PROJECT_ROOT,
                        capture_output=True,
                        check=False,
                    )
                    if not accepted:
                        self.assertNotEqual(
                            0,
                            completed.returncode,
                            self._command_output(completed),
                        )
                        continue

                    self.assertEqual(
                        0,
                        completed.returncode,
                        self._command_output(completed),
                    )
                    expected = canonical_json_bytes(
                        payload,
                        opaque_string_rules=rules,
                    )
                    response = json.loads(completed.stdout.decode("utf-8"))
                    self.assertEqual(
                        sha256(expected).hexdigest(),
                        response["canonical_sha256"],
                    )
                    self.assertEqual(
                        len(expected),
                        response["canonical_utf8_bytes"],
                    )

    def test_built_csharp_runner_covers_each_native_opaque_carrier_path(
        self,
    ) -> None:
        """Bridge and console carrier exceptions remain exact-path only."""

        combining_carrier = '"中😀\u0344"'
        vectors = (
            (
                "bridge-geometry",
                "bridge-response",
                "response",
                {"result": {"geometry_json": combining_carrier}},
                True,
            ),
            (
                "bridge-inventory",
                "bridge-response",
                "response",
                {"result": {"inventory_json": "[]" * 30_000}},
                True,
            ),
            (
                "bridge-wrong-path",
                "bridge-response",
                "response",
                {
                    "result": {
                        "nested": {"geometry_json": combining_carrier},
                    }
                },
                False,
            ),
            (
                "console-export",
                "console-export",
                "console_export",
                {"geometry_json": combining_carrier},
                True,
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            for index, (name, profile, kind, payload, accepted) in enumerate(vectors):
                with self.subTest(vector=name):
                    payload_path = temporary_path / f"opaque-path-{index}.json"
                    payload_path.write_text(
                        json.dumps(
                            payload,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ),
                        encoding="utf-8",
                    )
                    completed = subprocess.run(
                        [
                            "dotnet",
                            "run",
                            "--project",
                            str(CORE_TEST_PROJECT),
                            "-c",
                            "Release",
                            "--no-build",
                            "--",
                            "canonical-profile",
                            profile,
                            str(payload_path),
                        ],
                        cwd=PROJECT_ROOT,
                        capture_output=True,
                        check=False,
                    )
                    if not accepted:
                        self.assertNotEqual(
                            0,
                            completed.returncode,
                            self._command_output(completed),
                        )
                        continue

                    self.assertEqual(
                        0,
                        completed.returncode,
                        self._command_output(completed),
                    )
                    expected = canonical_json_bytes(
                        payload,
                        opaque_string_rules=opaque_embedded_json_rules(kind),
                    )
                    response = json.loads(completed.stdout.decode("utf-8"))
                    self.assertEqual(
                        sha256(expected).hexdigest(),
                        response["canonical_sha256"],
                    )
                    self.assertEqual(
                        len(expected),
                        response["canonical_utf8_bytes"],
                    )

    def test_generated_manifest_core_execution_preserves_full_integrity(self) -> None:
        """Validate a Python manifest/result pair after the C# core executes it."""

        manifest = _generated_marker_manifest()
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "native-manifest.json"
            manifest_path.write_bytes(
                canonical_native_contract_bytes("manifest", manifest) + b"\n"
            )
            completed = subprocess.run(
                [
                    "dotnet",
                    "run",
                    "--project",
                    str(CORE_TEST_PROJECT),
                    "-c",
                    "Release",
                    "--no-build",
                    "--",
                    "execute-marker-manifest",
                    str(manifest_path),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                completed.returncode,
                self._command_output(completed),
            )
            result = json.loads(completed.stdout.decode("utf-8"))
            # The SDK-free core intentionally has no knowledge of a loaded
            # adapter package. The real adapter writer adds this v2 runtime
            # attestation immediately before it publishes the result.
            result["runtime_package_fingerprint"] = manifest["environment"][
                "runtime_package_fingerprint"
            ]
            result = attach_integrity(result)
            self.assertEqual(
                manifest["integrity"]["sha256"],
                result["manifest_integrity_sha256"],
            )
            self.assertNotEqual(
                manifest["expected_prewrite_output_copy_binding"]["sha256"],
                result["final_document_binding"]["output_copy_binding"]["sha256"],
            )
            self.assertEqual(
                manifest["final_output_constraints"][
                    "authorized_private_path_fingerprint"
                ],
                result["final_document_binding"]["output_copy_binding"][
                    "path_fingerprint"
                ],
            )
            self.assertEqual(
                result,
                validate_console_result(
                    manifest,
                    result,
                    run_id=result["run_id"],
                ),
            )

            readback_completed = subprocess.run(
                [
                    "dotnet",
                    "run",
                    "--project",
                    str(CORE_TEST_PROJECT),
                    "-c",
                    "Release",
                    "--no-build",
                    "--",
                    "execute-marker-manifest-readback",
                    str(manifest_path),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                readback_completed.returncode,
                self._command_output(readback_completed),
            )
            console_export = json.loads(readback_completed.stdout.decode("utf-8"))
            console_export["runtime_package_fingerprint"] = manifest["environment"][
                "runtime_package_fingerprint"
            ]
            console_export["console_result_integrity_sha256"] = result["integrity"][
                "sha256"
            ]
            console_export = attach_integrity(console_export)
            geometry_from_console_export(
                manifest,
                console_export,
                run_id=result["run_id"],
                result=result,
            )

            # A valid-looking reduced projection hash is not the full Python
            # manifest integrity and must be rejected before C# transaction work.
            reduced_projection_hash = canonical_sha256(
                {
                    "manifest_id": manifest["manifest_id"],
                    "nonce": manifest["nonce"],
                    "marker_policy_binding": manifest["marker_policy_binding"],
                    "operations": manifest["operations"],
                }
            )
            self.assertNotEqual(
                manifest["integrity"]["sha256"],
                reduced_projection_hash,
            )
            forged = deepcopy(manifest)
            forged["integrity"]["sha256"] = reduced_projection_hash
            manifest_path.write_bytes(
                canonical_native_contract_bytes("manifest", forged) + b"\n"
            )
            rejected = subprocess.run(
                [
                    "dotnet",
                    "run",
                    "--project",
                    str(CORE_TEST_PROJECT),
                    "-c",
                    "Release",
                    "--no-build",
                    "--",
                    "execute-marker-manifest",
                    str(manifest_path),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(
                0,
                rejected.returncode,
                self._command_output(rejected),
            )
            self.assertIn("integrity", self._command_output(rejected).casefold())


if __name__ == "__main__":
    unittest.main()
