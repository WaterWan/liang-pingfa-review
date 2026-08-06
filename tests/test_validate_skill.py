"""Focused tests for the deterministic Skill repository validator."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIRECTORY = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIRECTORY))

import validate_skill


class ValidateSkillTests(unittest.TestCase):
    """Exercise valid and invalid repository states in isolated copies."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary_directory.name) / "repository"
        shutil.copytree(
            PROJECT_ROOT,
            self.repository,
            ignore=shutil.ignore_patterns(
                ".git",
                "__pycache__",
                ".pytest_cache",
                ".mypy_cache",
                "build",
                "dist",
                "*.egg-info",
            ),
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def assert_validation_fails_with(self, expected_message: str) -> None:
        with self.assertRaises(validate_skill.ValidationError) as raised:
            validate_skill.validate_repository(self.repository)
        self.assertIn(expected_message, raised.exception.issues)

    def mutate_multi_annotation_contract(self, mutation) -> None:
        """Apply a JSON-only mutation to the checked multi-annotation contract."""

        contract_path = self.repository / validate_skill.MULTI_ANNOTATION_CONTRACT_PATH
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        mutation(contract)
        contract_path.write_text(
            json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def mutate_topology_contract(self, mutation) -> None:
        """Apply an isolated JSON-only topology contract mutation."""

        contract_path = self.repository / validate_skill.TOPOLOGY_CONTRACT_PATH
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        mutation(contract)
        contract_path.write_text(
            json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_clean_repository_passes(self) -> None:
        validate_skill.validate_repository(self.repository)

    def test_frozen_native_v1_schema_hash_mutation_fails(self) -> None:
        target = (
            self.repository
            / "src/liang_pingfa_review/schemas/native-audit-v1.schema.json"
        )
        target.write_bytes(target.read_bytes() + b"\n")
        self.assert_validation_fails_with(
            "frozen native v1 artifact hash drifted: "
            "src/liang_pingfa_review/schemas/native-audit-v1.schema.json"
        )

    def test_frozen_native_v1_csharp_surface_hash_mutation_fails(self) -> None:
        target = self.repository / "native-bridge-contracts/ProtocolV1.cs"
        target.write_bytes(target.read_bytes() + b"\n")
        self.assert_validation_fails_with(
            "frozen native v1 artifact hash drifted: "
            "native-bridge-contracts/ProtocolV1.cs"
        )

    def test_python_literal_unc_decoding_rejects_escaped_bypasses(self) -> None:
        slash = chr(92)
        target = self.repository / "src/liang_pingfa_review/native_manifest.py"
        original = target.read_text(encoding="utf-8")
        cases = {
            "normal": (
                'value = "'
                + slash * 4
                + "private-server"
                + slash * 2
                + 'share"\n'
            ),
            "raw": (
                'value = r"'
                + slash * 2
                + "private-server"
                + slash
                + 'share"\n'
            ),
            "bytes": (
                'value = b"'
                + slash * 4
                + "private-server"
                + slash * 2
                + 'share"\n'
            ),
            "f-constant": (
                'value = f"'
                + slash * 4
                + "private-server"
                + slash * 2
                + 'share"\n'
            ),
            "device": (
                'value = "'
                + slash * 4
                + "?"
                + slash * 2
                + "C:"
                + slash * 2
                + 'secret"\n'
            ),
            "localhost": (
                'value = "'
                + slash * 4
                + "localhost"
                + slash * 2
                + 'share"\n'
            ),
            "mixed": (
                'value = "'
                + slash * 4
                + "."
                + slash * 2
                + "pipe"
                + slash * 2
                + "liang-pingfa-native-a1b2c3d4e5f6g7h8 "
                + slash * 4
                + "private-server"
                + slash * 2
                + 'share"\n'
            ),
        }
        for name, source in cases.items():
            with self.subTest(name=name):
                target.write_text(
                    original + "\n" + source,
                    encoding="utf-8",
                )
                with self.assertRaises(validate_skill.ValidationError) as raised:
                    validate_skill.validate_repository(self.repository)
                if name == "device":
                    self.assertTrue(
                        any(
                            "local path found in src/liang_pingfa_review/native_manifest.py"
                            in issue
                            for issue in raised.exception.issues
                        )
                    )
                else:
                    self.assertIn(
                        "UNC local path found in src/liang_pingfa_review/native_manifest.py",
                        raised.exception.issues,
                    )

    def test_python_concatenated_literal_with_backslashes_fails_closed(self) -> None:
        slash = chr(92)
        target = self.repository / "src/liang_pingfa_review/native_manifest.py"
        source = (
            'value = "'
            + slash * 4
            + '" "private-server'
            + slash * 2
            + 'share"\n'
        )
        target.write_text(
            target.read_text(encoding="utf-8") + "\n" + source,
            encoding="utf-8",
        )
        with self.assertRaises(validate_skill.ValidationError) as raised:
            validate_skill.validate_repository(self.repository)
        self.assertTrue(
            any(
                "UNC local path found" in issue
                or "dynamic or concatenated Python literal" in issue
                for issue in raised.exception.issues
            )
        )

    def test_python_dynamic_f_string_with_backslash_literal_fails_closed(self) -> None:
        slash = chr(92)
        target = self.repository / "src/liang_pingfa_review/native_manifest.py"
        source = (
            'host = "generated"\nvalue = f"'
            + slash * 4
            + "{host}"
            + slash * 2
            + 'share"\n'
        )
        target.write_text(
            target.read_text(encoding="utf-8") + "\n" + source,
            encoding="utf-8",
        )
        with self.assertRaises(validate_skill.ValidationError) as raised:
            validate_skill.validate_repository(self.repository)
        self.assertIn(
            "dynamic or concatenated Python literal with backslashes found in "
            "src/liang_pingfa_review/native_manifest.py",
            raised.exception.issues,
        )

    def test_decoded_exact_project_pipe_remains_allowed_only_in_protocol_context(self) -> None:
        slash = chr(92)
        target = self.repository / "tests/test_native_protocol.py"
        source = (
            'allowed_pipe = "'
            + slash * 4
            + "."
            + slash * 2
            + "pipe"
            + slash * 2
            + 'liang-pingfa-native-a1b2c3d4e5f6g7h8"\n'
        )
        target.write_text(
            target.read_text(encoding="utf-8") + "\n" + source,
            encoding="utf-8",
        )
        validate_skill.validate_repository(self.repository)

    def test_generated_build_dist_and_egg_info_are_ignored(self) -> None:
        generated_files = (
            self.repository / "build/lib/generated.py",
            self.repository / "dist/liang_pingfa_review-0.1.0.whl",
            self.repository / "dist/liang-pingfa-review-0.1.0.tar.gz",
            self.repository / "src/liang_pingfa_review.egg-info/PKG-INFO",
        )
        for generated_file in generated_files:
            generated_file.parent.mkdir(parents=True, exist_ok=True)
            generated_file.write_bytes(b"generated")

        validate_skill.validate_repository(self.repository)

    def test_exact_csharp_project_bin_and_obj_are_ignored(self) -> None:
        generated_files = (
            self.repository
            / "native-bridge-contracts/bin/Release/net8.0/generated.dll",
            self.repository
            / "native-bridge-contracts/obj/Release/net8.0/generated.cs",
        )
        for generated_file in generated_files:
            generated_file.parent.mkdir(parents=True, exist_ok=True)
            generated_file.write_bytes(b"generated")

        validate_skill.validate_repository(self.repository)

    def test_native_cad_exact_project_bin_and_obj_are_ignored(self) -> None:
        generated_files = (
            self.repository
            / "native-cad/src/LiangPingfa.NativeCad.Protocol/bin/Release/netstandard2.0/generated.dll",
            self.repository
            / "native-cad/src/LiangPingfa.NativeCad.Core/obj/Release/netstandard2.0/generated.cs",
            self.repository
            / "native-cad/src/LiangPingfa.NativeCad.AutoCAD.ApiStubs/bin/Release/netstandard2.0/generated.dll",
            self.repository
            / "native-cad/tests/LiangPingfa.NativeCad.Core.Tests/obj/Release/net8.0/generated.cs",
        )
        for generated_file in generated_files:
            generated_file.parent.mkdir(parents=True, exist_ok=True)
            generated_file.write_bytes(b"generated")

        validate_skill.validate_repository(self.repository)

    def test_native_cad_unapproved_binary_path_fails(self) -> None:
        artifact = self.repository / "native-cad/deployment/generated.dll"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"generated")

        self.assert_validation_fails_with(
            "forbidden artifact extension: native-cad/deployment/generated.dll"
        )

    def test_native_cad_package_reference_fails(self) -> None:
        project = (
            self.repository
            / "native-cad/src/LiangPingfa.NativeCad.Core"
            / "LiangPingfa.NativeCad.Core.csproj"
        )
        project.write_text(
            project.read_text(encoding="utf-8").replace(
                "</Project>",
                '  <ItemGroup><PackageReference Include="generated" Version="1.0.0" /></ItemGroup>\n'
                "</Project>",
                1,
            ),
            encoding="utf-8",
        )

        self.assert_validation_fails_with(
            "native CAD project contains forbidden package/proprietary reference: "
            "native-cad/src/LiangPingfa.NativeCad.Core/LiangPingfa.NativeCad.Core.csproj"
        )

    def test_native_cad_shared_msbuild_dependency_injections_fail(self) -> None:
        """Every tracked props/targets file is scanned despite MSBuild conditions."""

        injections = {
            "PackageReference": (
                '<ItemGroup><PackageReference Include="generated" Version="1.0.0" /></ItemGroup>'
            ),
            "FrameworkReference": (
                '<ItemGroup><FrameworkReference Include="generated" /></ItemGroup>'
            ),
            "Reference": '<ItemGroup><Reference Include="generated" /></ItemGroup>',
            "HintPath": "<ItemGroup><HintPath>generated.dll</HintPath></ItemGroup>",
            "Import": '<Import Project="generated.props" />',
            "UsingTask": (
                '<UsingTask TaskName="Generated" AssemblyFile="generated.dll" />'
            ),
            "Exec": '<Target Name="Generated"><Exec Command="generated" /></Target>',
        }
        for filename in ("Directory.Build.props", "Directory.Build.targets"):
            path = self.repository / "native-cad" / filename
            original = path.read_text(encoding="utf-8")
            relative = f"native-cad/{filename}"
            for kind, injection in injections.items():
                with self.subTest(file=filename, injection=kind):
                    path.write_text(
                        original.replace(
                            "</Project>",
                            "  " + injection + "\n</Project>",
                            1,
                        ),
                        encoding="utf-8",
                    )
                    self.assert_validation_fails_with(
                        "native CAD MSBuild file contains forbidden "
                        f"{kind}: {relative}"
                    )
                    path.write_text(original, encoding="utf-8")

    def test_native_cad_conditional_package_bypass_fails(self) -> None:
        """A false condition cannot hide an otherwise forbidden dependency."""

        path = self.repository / "native-cad" / "Directory.Build.props"
        original = path.read_text(encoding="utf-8")
        path.write_text(
            original.replace(
                "</Project>",
                "  <ItemGroup Condition=\"'false' == 'true'\">"
                '<PackageReference Include="generated" Version="1.0.0" />'
                "</ItemGroup>\n</Project>",
                1,
            ),
            encoding="utf-8",
        )
        self.assert_validation_fails_with(
            "native CAD MSBuild file contains forbidden PackageReference: "
            "native-cad/Directory.Build.props"
        )

    def test_native_cad_sdk_sources_must_be_exact_and_root_only(self) -> None:
        """Child, alternate, and conditional SDK declarations cannot bypass policy."""

        project = (
            self.repository
            / "native-cad/src/LiangPingfa.NativeCad.Core"
            / "LiangPingfa.NativeCad.Core.csproj"
        )
        original = project.read_text(encoding="utf-8")
        cases = {
            "alternate-root": (
                original.replace(
                    '<Project Sdk="Microsoft.NET.Sdk">',
                    '<Project Sdk="Microsoft.NET.Sdk.Web">',
                    1,
                ),
                "native CAD project must use exact root "
                'Project Sdk="Microsoft.NET.Sdk": '
                "native-cad/src/LiangPingfa.NativeCad.Core/"
                "LiangPingfa.NativeCad.Core.csproj",
            ),
            "child-sdk-element": (
                original.replace(
                    "</Project>",
                    '  <Sdk Name="Microsoft.NET.Sdk" Version="8.0.0" />\n</Project>',
                    1,
                ),
                "native CAD MSBuild file contains a child or secondary SDK "
                "declaration: native-cad/src/LiangPingfa.NativeCad.Core/"
                "LiangPingfa.NativeCad.Core.csproj",
            ),
            "conditional-sdk-import": (
                original.replace(
                    "</Project>",
                    '  <Import Project="generated.props" Sdk="Microsoft.NET.Sdk" '
                    'Condition="\'false\' == \'true\'" />\n</Project>',
                    1,
                ),
                "native CAD MSBuild file contains a child or secondary SDK "
                "declaration: native-cad/src/LiangPingfa.NativeCad.Core/"
                "LiangPingfa.NativeCad.Core.csproj",
            ),
        }
        for name, (mutated, expected) in cases.items():
            with self.subTest(case=name):
                project.write_text(mutated, encoding="utf-8")
                self.assert_validation_fails_with(expected)
                project.write_text(original, encoding="utf-8")

    def test_native_cad_target_framework_props_targets_and_conditions_fail(self) -> None:
        """Only one unconditional target definition in each approved project exists."""

        for filename in ("Directory.Build.props", "Directory.Build.targets"):
            path = self.repository / "native-cad" / filename
            original = path.read_text(encoding="utf-8")
            path.write_text(
                original.replace(
                    "</Project>",
                    "  <PropertyGroup><TargetFramework>net9.0</TargetFramework>"
                    "</PropertyGroup>\n</Project>",
                    1,
                ),
                encoding="utf-8",
            )
            self.assert_validation_fails_with(
                "native CAD TargetFramework definition is only allowed "
                f"in an approved project: native-cad/{filename}"
            )

        project = (
            self.repository
            / "native-cad/src/LiangPingfa.NativeCad.Core"
            / "LiangPingfa.NativeCad.Core.csproj"
        )
        original = project.read_text(encoding="utf-8")
        project.write_text(
            original.replace(
                "</Project>",
                '  <PropertyGroup Condition="\'$(Injected)\' == \'true\'">'
                "<TargetFramework>net9.0</TargetFramework></PropertyGroup>\n"
                "</Project>",
                1,
            ),
            encoding="utf-8",
        )
        self.assert_validation_fails_with(
            "native CAD TargetFramework must have one unconditional "
            "authoritative definition: native-cad/src/"
            "LiangPingfa.NativeCad.Core/LiangPingfa.NativeCad.Core.csproj"
        )

    def test_native_cad_target_frameworks_multi_target_bypass_fails(self) -> None:
        project = (
            self.repository
            / "native-cad/src/LiangPingfa.NativeCad.Core"
            / "LiangPingfa.NativeCad.Core.csproj"
        )
        project.write_text(
            project.read_text(encoding="utf-8").replace(
                "</Project>",
                "  <PropertyGroup><TargetFrameworks>net8.0;net9.0"
                "</TargetFrameworks></PropertyGroup>\n</Project>",
                1,
            ),
            encoding="utf-8",
        )
        self.assert_validation_fails_with(
            "native CAD projects must not define TargetFrameworks: "
            "native-cad/src/LiangPingfa.NativeCad.Core/"
            "LiangPingfa.NativeCad.Core.csproj"
        )

    def test_native_cad_policy_import_must_be_exact_unconditional_and_final(self) -> None:
        """The per-project policy import cannot be removed, redirected, or duplicated."""

        project = (
            self.repository
            / "native-cad/src/LiangPingfa.NativeCad.Core"
            / "LiangPingfa.NativeCad.Core.csproj"
        )
        original = project.read_text(encoding="utf-8")
        policy_import = r'<Import Project="..\..\NativeCad.RepositoryPolicy.targets" />'
        expected = (
            "native CAD project must contain exactly one approved "
            "unconditional policy import: native-cad/src/"
            "LiangPingfa.NativeCad.Core/LiangPingfa.NativeCad.Core.csproj"
        )
        cases = {
            "removed": original.replace(policy_import + "\n", "", 1),
            "conditional": original.replace(
                policy_import,
                policy_import[:-3] + ' Condition="\'$(SkipPolicy)\' == \'true\'" />',
                1,
            ),
            "alternative": original.replace(
                policy_import,
                '<Import Project="..\\..\\generated.targets" />',
                1,
            ),
            "duplicate": original.replace(
                policy_import,
                policy_import + "\n  " + policy_import,
                1,
            ),
        }
        for name, mutated in cases.items():
            with self.subTest(case=name):
                project.write_text(mutated, encoding="utf-8")
                self.assert_validation_fails_with(expected)
                project.write_text(original, encoding="utf-8")

        project.write_text(
            original.replace(
                "</Project>",
                "  <PropertyGroup><Deterministic>true</Deterministic></PropertyGroup>\n"
                "</Project>",
                1,
            ),
            encoding="utf-8",
        )
        self.assert_validation_fails_with(
            "native CAD policy import must be the final project element: "
            "native-cad/src/LiangPingfa.NativeCad.Core/"
            "LiangPingfa.NativeCad.Core.csproj"
        )

    def test_native_cad_environment_target_override_fails(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"DirectoryBuildTargetsPath": "generated.targets"},
            clear=False,
        ):
            self.assert_validation_fails_with(
                "native CAD environment override is forbidden: "
                "DirectoryBuildTargetsPath"
            )

    def test_native_cad_external_project_reference_fails(self) -> None:
        """Only repository-local literal project references are allowed."""

        path = (
            self.repository
            / "native-cad/src/LiangPingfa.NativeCad.Core"
            / "LiangPingfa.NativeCad.Core.csproj"
        )
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "</Project>",
                '  <ItemGroup><ProjectReference Include="..\\..\\..\\outside.csproj" /></ItemGroup>\n'
                "</Project>",
                1,
            ),
            encoding="utf-8",
        )
        self.assert_validation_fails_with(
            "native CAD ProjectReference escapes native-cad: "
            "native-cad/src/LiangPingfa.NativeCad.Core/"
            "LiangPingfa.NativeCad.Core.csproj"
        )

    def test_native_cad_stub_project_reference_requires_copylocal_false(self) -> None:
        """A syntax-only stub cannot become a transitive deployment asset."""

        path = (
            self.repository
            / "native-cad/tests/LiangPingfa.NativeCad.Core.Tests"
            / "LiangPingfa.NativeCad.Core.Tests.csproj"
        )
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "<Private>false</Private>",
                "<Private>true</Private>",
                1,
            ),
            encoding="utf-8",
        )
        self.assert_validation_fails_with(
            "native CAD syntax-stub ProjectReference must disable copy-local: "
            "native-cad/tests/LiangPingfa.NativeCad.Core.Tests/"
            "LiangPingfa.NativeCad.Core.Tests.csproj"
        )

    def test_autocad_adapter_sdk_reference_must_remain_copylocal_false(self) -> None:
        """The reviewed adapter exception cannot turn into a deployable SDK copy."""

        path = (
            self.repository
            / "native-cad/src/LiangPingfa.NativeCad.AutoCAD.Adapter"
            / "LiangPingfa.NativeCad.AutoCAD.Adapter.csproj"
        )
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "<Reference Include=\"AcMgd\">\n"
                "      <HintPath>$(CadSdkDir)\\AcMgd.dll</HintPath>\n"
                "      <Private>false</Private>",
                "<Reference Include=\"AcMgd\">\n"
                "      <HintPath>$(CadSdkDir)\\AcMgd.dll</HintPath>\n"
                "      <Private>true</Private>",
                1,
            ),
            encoding="utf-8",
        )
        self.assert_validation_fails_with(
            "native CAD adapter SDK reference is not exact/copy-local-disabled: AcMgd"
        )

    def test_autocad_adapter_delete_capability_or_erase_fails_validation(self) -> None:
        identity = (
            self.repository
            / "native-cad/src/LiangPingfa.NativeCad.AutoCAD.Adapter/AdapterIdentity.cs"
        )
        identity.write_text(
            identity.read_text(encoding="utf-8").replace(
                '"create_review_marker/v1",',
                '"create_review_marker/v1",\n                    '
                '"delete_auxiliary_overlay_text/v1",',
                1,
            ),
            encoding="utf-8",
        )
        self.assert_validation_fails_with(
            "AutoCAD adapter capability advertisement must exclude native delete"
        )

    def test_autocad_runtime_qualification_claim_requires_private_marker(self) -> None:
        path = self.repository / "native-cad/README.md"
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\nThis adapter is runtime qualified for every host.\n",
            encoding="utf-8",
        )
        self.assert_validation_fails_with(
            "native CAD runtime qualification claim lacks private evidence marker: "
            "native-cad/README.md"
        )

    def test_native_cad_stub_runtime_disclosure_removal_fails(self) -> None:
        source = (
            self.repository
            / "native-cad/src/LiangPingfa.NativeCad.AutoCAD.ApiStubs/AutodeskApiStubs.cs"
        )
        source.write_text(
            source.read_text(encoding="utf-8").replace(
                "SYNTAX-ONLY API STUBS",
                "removed syntax disclosure",
                1,
            ),
            encoding="utf-8",
        )

        self.assert_validation_fails_with(
            "native CAD API stubs lack syntax-only disclosure: SYNTAX-ONLY API STUBS"
        )

    def test_native_cad_stub_deployment_output_fails(self) -> None:
        project = (
            self.repository
            / "native-cad/src/LiangPingfa.NativeCad.AutoCAD.ApiStubs"
            / "LiangPingfa.NativeCad.AutoCAD.ApiStubs.csproj"
        )
        project.write_text(
            project.read_text(encoding="utf-8").replace(
                "<AssemblyName>",
                "<OutputType>Exe</OutputType>\n    <AssemblyName>",
                1,
            ),
            encoding="utf-8",
        )

        self.assert_validation_fails_with(
            "syntax-only API stubs must not be deployment/package output"
        )

    def test_native_cad_stub_publish_guard_removal_fails(self) -> None:
        project = (
            self.repository
            / "native-cad/src/LiangPingfa.NativeCad.AutoCAD.ApiStubs"
            / "LiangPingfa.NativeCad.AutoCAD.ApiStubs.csproj"
        )
        project.write_text(
            project.read_text(encoding="utf-8").replace(
                "RejectSyntaxOnlyStubPublish",
                "RemovedSyntaxOnlyStubPublishGuard",
                1,
            ),
            encoding="utf-8",
        )
        self.assert_validation_fails_with(
            "syntax-only API stubs must reject packaging and deployment: "
            "RejectSyntaxOnlyStubPublish"
        )

    def test_native_cad_golden_hash_mutation_fails(self) -> None:
        fixture = self.repository / validate_skill.NATIVE_CAD_GOLDEN_FIXTURE_PATH
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        payload["canonical_sha256"] = "0" * 64
        fixture.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        self.assert_validation_fails_with(
            "native CAD golden fixture must retain canonical JSON/hash compatibility"
        )

    def test_nested_csharp_bin_and_obj_remain_subject_to_policy(self) -> None:
        for relative in (
            "native-bridge-contracts/tools/bin/generated.dll",
            "src/liang_pingfa_review/obj/generated.py",
        ):
            with self.subTest(relative=relative):
                generated_file = self.repository / relative
                generated_file.parent.mkdir(parents=True, exist_ok=True)
                generated_file.write_bytes(b"generated")

                self.assert_validation_fails_with(
                    f"path is not allowed by repository policy: {relative}"
                )
                generated_file.unlink()

    def test_nested_build_directory_remains_subject_to_policy(self) -> None:
        nested_generated_file = self.repository / "src/build/generated.py"
        nested_generated_file.parent.mkdir(parents=True, exist_ok=True)
        nested_generated_file.write_text("generated\n", encoding="utf-8")

        self.assert_validation_fails_with(
            "path is not allowed by repository policy: src/build/generated.py"
        )

    def test_wheel_outside_dist_remains_forbidden(self) -> None:
        artifact_path = self.repository / "release.whl"
        artifact_path.write_bytes(b"not a wheel")

        self.assert_validation_fails_with(
            "forbidden artifact extension: release.whl"
        )

    def test_sdist_outside_dist_remains_forbidden(self) -> None:
        artifact_path = self.repository / "release.tar.gz"
        artifact_path.write_bytes(b"not a source archive")

        self.assert_validation_fails_with(
            "forbidden artifact extension: release.tar.gz"
        )

    def test_tracked_build_and_egg_info_files_fail(self) -> None:
        tracked_repository = Path(self.temporary_directory.name) / "tracked-repository"
        tracked_repository.mkdir()
        tracked_files = (
            tracked_repository / "build/manifest.txt",
            tracked_repository / "dist/manifest.txt",
            tracked_repository / "src/liang_pingfa_review.egg-info/PKG-INFO",
        )
        for tracked_file in tracked_files:
            tracked_file.parent.mkdir(parents=True, exist_ok=True)
            tracked_file.write_text("generated\n", encoding="utf-8")

        subprocess.run(
            ["git", "init"],
            cwd=tracked_repository,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "add", "."],
            cwd=tracked_repository,
            check=True,
            capture_output=True,
            text=True,
        )

        with self.assertRaises(validate_skill.ValidationError) as raised:
            validate_skill.validate_tracked_files(tracked_repository)

        self.assertIn(
            "tracked path is not allowed by repository policy: build/manifest.txt",
            raised.exception.issues,
        )
        self.assertIn(
            "tracked path is not allowed by repository policy: dist/manifest.txt",
            raised.exception.issues,
        )
        self.assertIn(
            "tracked path is not allowed by repository policy: "
            "src/liang_pingfa_review.egg-info/PKG-INFO",
            raised.exception.issues,
        )

    def test_forced_tracked_csharp_bin_and_obj_files_fail(self) -> None:
        tracked_repository = Path(self.temporary_directory.name) / "tracked-csharp"
        tracked_repository.mkdir()
        tracked_files = (
            tracked_repository
            / "native-bridge-contracts/bin/Release/net8.0/generated.dll",
            tracked_repository
            / "native-bridge-contracts/obj/Release/net8.0/generated.cs",
        )
        for tracked_file in tracked_files:
            tracked_file.parent.mkdir(parents=True, exist_ok=True)
            tracked_file.write_bytes(b"generated")

        subprocess.run(
            ["git", "init"],
            cwd=tracked_repository,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "add", "--force", "."],
            cwd=tracked_repository,
            check=True,
            capture_output=True,
            text=True,
        )

        with self.assertRaises(validate_skill.ValidationError) as raised:
            validate_skill.validate_tracked_files(tracked_repository)

        self.assertIn(
            "tracked path is not allowed by repository policy: "
            "native-bridge-contracts/bin/Release/net8.0/generated.dll",
            raised.exception.issues,
        )
        self.assertIn(
            "tracked path is not allowed by repository policy: "
            "native-bridge-contracts/obj/Release/net8.0/generated.cs",
            raised.exception.issues,
        )

    def test_pip_install_then_validation_passes_without_cleanup(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", ".", "--no-deps"],
            cwd=self.repository,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        validate_skill.validate_repository(self.repository)

    def test_dotnet_build_then_validation_passes_without_cleanup(self) -> None:
        if shutil.which("dotnet") is None:
            self.skipTest(".NET SDK is unavailable outside the CI build image")
        result = subprocess.run(
            [
                "dotnet",
                "build",
                "native-bridge-contracts/LiangPingfa.NativeBridge.Contracts.csproj",
                "-c",
                "Release",
                "--nologo",
            ],
            cwd=self.repository,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        validate_skill.validate_repository(self.repository)

    def test_native_cad_evaluated_target_frameworks_are_frozen(self) -> None:
        """MSBuild evaluation confirms every project has the one approved TFM."""

        if shutil.which("dotnet") is None:
            self.skipTest(".NET SDK is unavailable outside the CI build image")
        for relative, expected in (
            validate_skill.NATIVE_CAD_EXPECTED_TARGET_FRAMEWORKS.items()
        ):
            if relative == validate_skill.NATIVE_CAD_AUTOCAD_ADAPTER_PROJECT:
                # The adapter refuses a default profile; individual explicit
                # profile builds are covered by test_autocad_adapter.py.
                continue
            with self.subTest(project=relative):
                result = subprocess.run(
                    [
                        "dotnet",
                        "msbuild",
                        relative,
                        "-nologo",
                        "-getProperty:TargetFramework",
                        "-getProperty:TargetFrameworks",
                    ],
                    cwd=self.repository,
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                properties = json.loads(result.stdout)["Properties"]
                self.assertEqual(expected, properties["TargetFramework"])
                self.assertEqual("", properties["TargetFrameworks"])

    def test_native_cad_build_rejects_framework_reference_and_global_tfm_override(
        self,
    ) -> None:
        """The evaluated fail targets reject items and global-property bypasses."""

        if shutil.which("dotnet") is None:
            self.skipTest(".NET SDK is unavailable outside the CI build image")
        core = (
            self.repository
            / "native-cad/src/LiangPingfa.NativeCad.Core"
            / "LiangPingfa.NativeCad.Core.csproj"
        )
        core.write_text(
            core.read_text(encoding="utf-8").replace(
                "</Project>",
                "  <ItemGroup><FrameworkReference Include=\"generated\" />"
                "</ItemGroup>\n</Project>",
                1,
            ),
            encoding="utf-8",
        )
        rejected_framework = subprocess.run(
            [
                "dotnet",
                "build",
                str(core),
                "-c",
                "Release",
                "--nologo",
            ],
            cwd=self.repository,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertNotEqual(
            0,
            rejected_framework.returncode,
            rejected_framework.stdout + rejected_framework.stderr,
        )
        self.assertIn(
            "must not use FrameworkReference",
            rejected_framework.stdout + rejected_framework.stderr,
        )

        protocol = (
            self.repository
            / "native-cad/src/LiangPingfa.NativeCad.Protocol"
            / "LiangPingfa.NativeCad.Protocol.csproj"
        )
        rejected_override = subprocess.run(
            [
                "dotnet",
                "build",
                str(protocol),
                "-c",
                "Release",
                "--nologo",
                "-p:TargetFramework=net8.0",
            ],
            cwd=self.repository,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertNotEqual(
            0,
            rejected_override.returncode,
            rejected_override.stdout + rejected_override.stderr,
        )
        self.assertIn(
            "TargetFramework differs",
            rejected_override.stdout + rejected_override.stderr,
        )

    def test_each_ci_job_checks_tracked_files_before_building_contracts(self) -> None:
        workflow = (
            self.repository / ".github/workflows/validate.yml"
        ).read_text(encoding="utf-8")
        tracked_command = "python scripts/validate_skill.py --tracked"
        build_command = (
            "dotnet build native-bridge-contracts/"
            "LiangPingfa.NativeBridge.Contracts.csproj"
        )
        jobs_section = workflow.split("\njobs:\n", 1)[-1]
        workflow_jobs = re.findall(r"(?m)^  ([A-Za-z0-9_-]+):\s*$", jobs_section)
        self.assertEqual(workflow_jobs, ["validate-windows"])
        self.assertNotIn("ubuntu", workflow.casefold())
        self.assertNotIn("linux", workflow.casefold())
        for job_name in ("validate-windows",):
            start = workflow.index(f"  {job_name}:")
            next_job = workflow.find("\n  validate-", start + 1)
            job = workflow[start : next_job if next_job >= 0 else len(workflow)]
            self.assertLess(job.index(tracked_command), job.index(build_command))

    def test_invalid_name_fails(self) -> None:
        skill_path = self.repository / validate_skill.SKILL_PATH
        skill_path.write_text(
            skill_path.read_text(encoding="utf-8").replace(
                "name: liang-pingfa-tuzhi-shencha",
                "name: invalid--name",
                1,
            ),
            encoding="utf-8",
        )

        self.assert_validation_fails_with("invalid skill name: invalid--name")

    def test_missing_reference_fails(self) -> None:
        reference_path = (
            self.repository
            / validate_skill.SKILL_DIRECTORY
            / "references/workflow-output.md"
        )
        reference_path.unlink()

        self.assert_validation_fails_with(
            "missing referenced resource in SKILL.md: references/workflow-output.md"
        )

    def test_missing_multi_annotation_reference_fails(self) -> None:
        reference_path = (
            self.repository
            / validate_skill.SKILL_DIRECTORY
            / "references/multi-annotation-overlap.md"
        )
        reference_path.unlink()

        self.assert_validation_fails_with(
            "missing required reference file: references/multi-annotation-overlap.md"
        )

    def test_missing_multi_annotation_contract_fails(self) -> None:
        (self.repository / validate_skill.MULTI_ANNOTATION_CONTRACT_PATH).unlink()

        self.assert_validation_fails_with(
            "missing required multi-annotation contract: "
            "tests/contracts/multi-annotation-overlap.json"
        )

    def test_missing_beam_topology_contract_fails(self) -> None:
        (self.repository / validate_skill.TOPOLOGY_CONTRACT_PATH).unlink()

        self.assert_validation_fails_with(
            "missing required beam topology contract: "
            "tests/contracts/beam-topology-in-situ.json"
        )

    def test_native_privacy_docs_require_owner_and_raw_artifact_disclosure(self) -> None:
        reference = (
            self.repository
            / validate_skill.SKILL_DIRECTORY
            / "references"
            / "native-cad-bridge.md"
        )
        reference.write_text(
            reference.read_text(encoding="utf-8").replace(
                "owner/DACL validation is required",
                "removed retained-handle owner validation",
                1,
            ),
            encoding="utf-8",
        )

        self.assert_validation_fails_with(
            "native bridge reference is missing private-artifact privacy wording: "
            "owner/DACL validation is required"
        )

    def test_native_privacy_docs_reject_false_redaction_claims(self) -> None:
        readme = self.repository / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8")
            + "\nprivate artifacts contain no raw data\n",
            encoding="utf-8",
        )

        self.assert_validation_fails_with(
            "README.md falsely claims private artifacts are redacted: "
            "private artifacts contain no raw data"
        )

    def test_topology_contract_rejects_profile_execution_controls(self) -> None:
        def mutate(contract) -> None:
            contract["profile"]["forbidden_profile_controls"].remove("mutation")

        self.mutate_topology_contract(mutate)

        self.assert_validation_fails_with(
            "beam topology contract must forbid profile execution controls"
        )

    def test_topology_contract_rejects_overstated_self_integrity(self) -> None:
        def mutate(contract) -> None:
            contract["audit_trust"]["self_integrity"] = "authenticates-all-editors"

        self.mutate_topology_contract(mutate)

        self.assert_validation_fails_with(
            "beam topology contract must state self-integrity and fresh re-audit limits"
        )

    def test_missing_topology_audit_trust_wording_fails(self) -> None:
        readme = self.repository / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8").replace(
                "自完整性 SHA-256 只用于检测意外损坏；它不能认证恶意同帐户编辑者重新签名的工件。",
                "removed topology audit trust boundary",
                1,
            ),
            encoding="utf-8",
        )

        self.assert_validation_fails_with(
            "README.md is missing topology audit trust-boundary wording: "
            "自完整性 SHA-256 只用于检测意外损坏；它不能认证恶意同帐户编辑者重新签名的工件。"
        )

    def test_topology_contract_rejects_actionable_scenario(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["actionability"] = True

        self.mutate_topology_contract(mutate)

        self.assert_validation_fails_with(
            "beam topology contract scenario must be non-actionable: "
            "unique-legal-placement"
        )

    def test_audit_v2_schema_rejects_token_fingerprint_oracle(self) -> None:
        schema_path = (
            self.repository
            / "src/liang_pingfa_review/schemas/audit-v2.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        trace = schema["$defs"]["topologyTrace"]
        trace["required"].append("parsed_value_fingerprint")
        trace["properties"]["parsed_value_fingerprint"] = {
            "$ref": "#/$defs/sha256"
        }
        schema_path.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        self.assert_validation_fails_with(
            "audit v2 schema must expose only a boolean token equality relation"
        )

    def test_missing_multi_annotation_skill_phrase_fails(self) -> None:
        skill_path = self.repository / validate_skill.SKILL_PATH
        skill_path.write_text(
            skill_path.read_text(encoding="utf-8").replace(
                "重叠簇门在 P1 之前",
                "removed overlap ordering",
                1,
            ),
            encoding="utf-8",
        )

        self.assert_validation_fails_with(
            "SKILL.md is missing required multi-annotation wording: 重叠簇门在 P1 之前"
        )

    def test_multi_annotation_duplicate_candidate_finding_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["findings"][1]["candidate_id"] = "cluster-a"

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract findings contain duplicate candidate ID: cluster-a"
        )

    def test_multi_annotation_missing_candidate_finding_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["findings"].pop()

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract findings must cover every affected candidate; "
            "missing: cluster-b"
        )

    def test_multi_annotation_extra_candidate_finding_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["findings"].append(
                {"candidate_id": "cluster-extra", "status": "证据不足"}
            )

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract finding candidate ID is not an affected "
            "candidate: cluster-extra"
        )

    def test_multi_annotation_empty_candidate_finding_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["findings"][1]["candidate_id"] = ""

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract findings must use nonempty candidate IDs"
        )

    def test_multi_annotation_non_insufficient_finding_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["findings"][0]["status"] = "一致"

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract every affected candidate finding must be 证据不足"
        )

    def test_multi_annotation_p1_pass_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["p1"] = "passed"

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract must record P1 failure for every affected candidate"
        )

    def test_multi_annotation_p2_allowed_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["p2"] = "allowed"

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract must block P2 for every failed affected candidate"
        )

    def test_multi_annotation_removed_concatenation_forbidden_behavior_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["forbidden"].remove(
                "candidate concatenation"
            )

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract unresolved scenario must forbid: "
            "candidate concatenation"
        )

    def test_multi_annotation_field_merge_enabled_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["forbidden"].remove("field merge")

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract unresolved scenario must forbid: field merge"
        )

    def test_multi_annotation_nearest_binding_enabled_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["forbidden"].remove(
                "nearest-distance binding"
            )

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract unresolved scenario must forbid: "
            "nearest-distance binding"
        )

    def test_multi_annotation_partial_ocr_enabled_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["forbidden"].remove("partial OCR pass")

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract unresolved scenario must forbid: partial OCR pass"
        )

    def test_multi_annotation_color_layer_semantic_proof_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["forbidden"].remove(
                "color-or-layer semantic proof"
            )

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract unresolved scenario must forbid: "
            "color-or-layer semantic proof"
        )

    def test_multi_annotation_unresolved_consistent_region_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["expected"]["region_status_must_not_be"] = "不同"

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract must prohibit a region status of 一致 while "
            "overlap is unresolved"
        )

    def test_multi_annotation_readable_conflict_must_be_evidence_backed(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["expected"]["only_when"] = "automatic conflict"

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract must permit only evidence-backed readable conflict"
        )

    def test_multi_annotation_proximity_only_overlap_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["overlap_evidence"] = ["proximity only"]

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract overlap_evidence must use allowlisted "
            "actual intersection types and exactly identify affected candidates"
        )

    def test_multi_annotation_color_only_overlap_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["overlap_evidence"] = [
                {
                    "type": "color",
                    "candidate_ids": ["cluster-a", "cluster-b"],
                }
            ]

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract overlap_evidence must use allowlisted "
            "actual intersection types and exactly identify affected candidates"
        )

    def test_multi_annotation_empty_intersection_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][0]["overlap_evidence"] = [
                {"type": "ink_mask_intersection", "candidate_ids": []}
            ]

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract overlap_evidence must use allowlisted "
            "actual intersection types and exactly identify affected candidates"
        )

    def test_multi_annotation_duplicate_readable_candidate_id_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["readable_candidate_ids"][1] = "cluster-c"

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable_candidate_ids contain duplicate "
            "candidate ID: cluster-c"
        )

    def test_multi_annotation_missing_readable_candidate_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["readable_candidate_ids"].pop()

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable_candidate_ids must cover every "
            "candidate; missing: cluster-d"
        )

    def test_multi_annotation_extra_readable_candidate_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["readable_candidate_ids"].append("cluster-extra")

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable candidate ID is not a candidate "
            "cluster: cluster-extra"
        )

    def test_multi_annotation_color_only_independent_evidence_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["independent_evidence"] = [
                {"type": "color", "value": "hint-c"}
            ]

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable scenario must not use free-form "
            "or legacy evidence fields"
        )

    def test_multi_annotation_color_only_visible_conflict_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["visible_expression_conflict_evidence"] = [
                {
                    "type": "color",
                    "candidate_ids": ["cluster-c", "cluster-d"],
                }
            ]

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract visible expression conflict evidence must "
            "be allowlisted and identify every readable candidate"
        )

    def test_multi_annotation_missing_readable_boundary_evidence_fails(self) -> None:
        def mutate(contract) -> None:
            del contract["scenarios"][1]["candidate_evidence"][0]["boundary_evidence"]

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable evidence requires allowlisted "
            "boundary_evidence for: cluster-c"
        )

    def test_multi_annotation_missing_readable_scope_evidence_fails(self) -> None:
        def mutate(contract) -> None:
            del contract["scenarios"][1]["candidate_evidence"][0]["scope_evidence"]

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable evidence requires allowlisted "
            "scope_evidence for: cluster-c"
        )

    def test_multi_annotation_missing_readable_binding_evidence_fails(self) -> None:
        def mutate(contract) -> None:
            del contract["scenarios"][1]["candidate_evidence"][0]["binding_evidence"]

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable evidence requires allowlisted "
            "binding_evidence for: cluster-c"
        )

    def test_multi_annotation_missing_visible_conflict_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["visible_expression_conflict_evidence"] = []

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable scenario requires separate visible "
            "expression conflict evidence"
        )

    def test_multi_annotation_readable_field_concatenation_enabled_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["expected"]["forbidden"].remove(
                "field concatenation"
            )

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable scenario must forbid: "
            "field concatenation"
        )

    def test_multi_annotation_readable_field_merge_enabled_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["expected"]["forbidden"].remove("field merge")

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable scenario must forbid: field merge"
        )

    def test_multi_annotation_readable_scope_merge_enabled_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["expected"]["forbidden"].remove("scope merge")

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable scenario must forbid: scope merge"
        )

    def test_multi_annotation_merged_scope_string_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["candidate_clusters"][0]["scope"] = (
                "concentrated-and-in-situ-merged"
            )

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract candidate scope must be exactly one "
            "allowlisted semantic scope: concentrated_annotation or "
            "in_situ_annotation"
        )

    def test_multi_annotation_combined_scope_string_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["candidate_clusters"][0]["scope"] = (
                "concentrated_and_in_situ_combined"
            )

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract candidate scope must be exactly one "
            "allowlisted semantic scope: concentrated_annotation or "
            "in_situ_annotation"
        )

    def test_multi_annotation_scope_list_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["candidate_clusters"][0]["scope"] = [
                "concentrated_annotation",
                "in_situ_annotation",
            ]

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract candidates must use separate scope and "
            "structured non-semantic candidate_hints"
        )

    def test_multi_annotation_unknown_scope_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["candidate_clusters"][0]["scope"] = (
                "section_annotation"
            )

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract candidate scope must be exactly one "
            "allowlisted semantic scope: concentrated_annotation or "
            "in_situ_annotation"
        )

    def test_multi_annotation_duplicate_same_scope_candidates_fail(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["candidate_clusters"][1]["scope"] = (
                "concentrated_annotation"
            )

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable scenario must not contain "
            "duplicate same-scope candidates"
        )

    def test_multi_annotation_missing_required_scope_role_fails(self) -> None:
        def mutate(contract) -> None:
            contract["scenarios"][1]["candidate_clusters"][0]["scope"] = (
                "in_situ_annotation"
            )

        self.mutate_multi_annotation_contract(mutate)

        self.assert_validation_fails_with(
            "multi-annotation contract readable scenario is missing required "
            "scope role: concentrated_annotation"
        )

    def test_forbidden_source_artifact_fails(self) -> None:
        artifact_path = self.repository / "tests/local-fixtures/fixture.pdf"
        artifact_path.write_bytes(b"not a real PDF")

        self.assert_validation_fails_with(
            "forbidden artifact extension: tests/local-fixtures/fixture.pdf"
        )

    def test_forbidden_archive_in_pytest_cache_fails(self) -> None:
        artifact_path = self.repository / ".pytest_cache/source-drawing.tar.gz"
        artifact_path.parent.mkdir()
        artifact_path.write_bytes(b"not a real source archive")

        self.assert_validation_fails_with(
            "forbidden artifact extension: .pytest_cache/source-drawing.tar.gz"
        )

    def test_absolute_local_path_fails(self) -> None:
        readme_path = self.repository / "README.md"
        separator = chr(92)
        private_path = "C" + chr(58) + separator + "private" + separator + "drawing.pdf"
        readme_path.write_text(
            readme_path.read_text(encoding="utf-8") + f"\nPrivate input: {private_path}\n",
            encoding="utf-8",
        )

        self.assert_validation_fails_with(
            "obvious Windows absolute local path found in README.md"
        )

    def test_unc_validator_allows_only_exact_pipe_spans(self) -> None:
        source_path = self.repository / "tests/support/synthetic_native.py"
        pipe = (
            chr(92) * 2
            + "."
            + chr(92)
            + "pipe"
            + chr(92)
            + "liang-pingfa-native-<runtime-token>"
        )
        source_path.write_text(
            source_path.read_text(encoding="utf-8")
            + f'\nGENERIC_TEST_PIPE = r"{pipe}"\n',
            encoding="utf-8",
        )
        validate_skill.validate_repository(self.repository)

        unsafe_prefix = chr(92) * 2
        for name, candidate in (
            ("server", unsafe_prefix + "private-server" + chr(92) + "share"),
            ("localhost", unsafe_prefix + "localhost" + chr(92) + "share"),
            ("device", unsafe_prefix + "?" + chr(92) + "device"),
            (
                "mixed",
                pipe + " and " + unsafe_prefix + "private-server" + chr(92) + "share",
            ),
        ):
            with self.subTest(name=name):
                source_path.write_text(
                    source_path.read_text(encoding="utf-8")
                    + f'\nUNSAFE_TEST_LITERAL = r"{candidate}"\n',
                    encoding="utf-8",
                )
                self.assert_validation_fails_with(
                    "UNC local path found in tests/support/synthetic_native.py"
                )
                source_path.write_text(
                    source_path.read_text(encoding="utf-8").rsplit(
                        "\nUNSAFE_TEST_LITERAL", 1
                    )[0]
                    + "\n",
                    encoding="utf-8",
                )

    def test_local_audit_artifact_fails(self) -> None:
        artifact_path = self.repository / "output/audit.json"
        artifact_path.parent.mkdir()
        artifact_path.write_text("{}", encoding="utf-8")

        self.assert_validation_fails_with(
            "path is not allowed by repository policy: output/audit.json"
        )

    def test_unapproved_package_file_fails(self) -> None:
        source_path = self.repository / "src/liang_pingfa_review/unapproved.py"
        source_path.write_text("pass\n", encoding="utf-8")

        self.assert_validation_fails_with(
            "path is not allowed by repository policy: src/liang_pingfa_review/unapproved.py"
        )

    def test_missing_windows_phase_two_workflow_fails(self) -> None:
        workflow_path = self.repository / ".github/workflows/validate.yml"
        workflow_path.write_text(
            workflow_path.read_text(encoding="utf-8").replace(
                "validate-windows:",
                "removed-windows-job:",
                1,
            ),
            encoding="utf-8",
        )

        self.assert_validation_fails_with(
            "validate workflow is missing required command or platform: validate-windows:"
        )

    def test_missing_bounded_oda_threat_model_fails(self) -> None:
        readme_path = self.repository / "README.md"
        readme_path.write_text(
            readme_path.read_text(encoding="utf-8").replace(
                "trusted Windows account/session, ODA executable,",
                "removed threat model,",
                1,
            ),
            encoding="utf-8",
        )

        self.assert_validation_fails_with(
            "README.md is missing the bounded trusted-local-session threat model"
        )

    def test_missing_public_support_boundary_fails(self) -> None:
        readme_path = self.repository / "README.md"
        readme_path.write_text(
            readme_path.read_text(encoding="utf-8").replace(
                "R2018/AC1032 DXF-exposable",
                "removed support profile",
            ),
            encoding="utf-8",
        )

        self.assert_validation_fails_with(
            "README.md is missing required public support-boundary wording: "
            "R2018/AC1032 DXF-exposable"
        )

    def test_skill_rejects_bypassing_unsupported_drawings(self) -> None:
        skill_path = self.repository / validate_skill.SKILL_PATH
        skill_path.write_text(
            skill_path.read_text(encoding="utf-8").replace(
                "不得绕过这些兼容性门",
                "removed no-bypass instruction",
                1,
            ),
            encoding="utf-8",
        )

        self.assert_validation_fails_with(
            "SKILL.md is missing required public support-boundary wording: "
            "不得绕过这些兼容性门"
        )

if __name__ == "__main__":
    unittest.main()
