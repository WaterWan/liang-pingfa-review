"""Generated-package integrity tests for the licensed AutoCAD adapter lane.

These tests use the repository's syntax-only compilation output exclusively.
They never create an operator package, load an Autodesk binary, or inspect a
customer drawing.  The generated package exists only long enough to prove
that Python and the actual adapter assembly agree on the package contract.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from liang_pingfa_review.canonical import canonical_json_bytes, strict_json_loads
from liang_pingfa_review.errors import PipelineError
from liang_pingfa_review.native_contracts import (
    AUTOCAD_ADAPTER_ID,
    validate_native_contract,
)
from liang_pingfa_review.runtime_package import (
    ADAPTER_ASSEMBLY,
    ADAPTER_DEPS,
    allowed_package_file_names,
    build_adapter_receipt,
    build_runtime_package_descriptor,
    component_records_from_directory,
    runtime_package_fingerprint,
    validate_adapter_receipt,
    verify_adapter_package_against_receipt,
)
from tests.support.synthetic_native import (
    configure_autocad_runtime_package,
    config as synthetic_config,
)


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
BUILD_SCRIPT = PROJECT_ROOT / "native-cad/scripts/build-autocad-adapter.ps1"
POWERSHELL_COMPATIBILITY_SCRIPT = (
    PROJECT_ROOT / "native-cad/scripts/powershell-compatibility.ps1"
)
_PROFILE_OUTPUTS = {
    "autocad2024": "net48",
    "autocad2025": "net8.0-windows",
    "autocad2026": "net10.0-windows",
}


def _run(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["dotnet", *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
    )


def _output(result: subprocess.CompletedProcess[bytes]) -> str:
    return (result.stdout + result.stderr).decode("utf-8", errors="replace")


def _file_record(path: Path, *, name: str, role: str) -> dict[str, object]:
    return {
        "name": name,
        "byte_size": path.stat().st_size,
        "sha256": sha256(path.read_bytes()).hexdigest(),
        "role": role,
    }


@unittest.skipUnless(shutil.which("dotnet"), "dotnet SDK is required")
class RuntimePackageTests(unittest.TestCase):
    """Package receipts fail closed for every repository-owned runtime file."""

    @classmethod
    def setUpClass(cls) -> None:
        for profile in _PROFILE_OUTPUTS:
            build_adapter = _run(
                "build",
                str(ADAPTER_PROJECT),
                "-c",
                "Release",
                "--nologo",
                "-p:BuildAutoCadAdapter=true",
                "-p:UseAutodeskApiStubs=true",
                f"-p:CadHostProfile={profile}",
            )
            if build_adapter.returncode != 0:
                raise AssertionError(_output(build_adapter))
        restore = _run(
            "restore",
            str(ADAPTER_TEST_PROJECT),
            "--nologo",
            "-p:CadHostProfile=autocad2025",
        )
        if restore.returncode != 0:
            raise AssertionError(_output(restore))
        build = _run(
            "build",
            str(ADAPTER_TEST_PROJECT),
            "-c",
            "Release",
            "--nologo",
            "--no-restore",
            "-p:CadHostProfile=autocad2025",
        )
        if build.returncode != 0:
            raise AssertionError(_output(build))

    def _generated_package(
        self,
        root: Path,
        *,
        profile: str = "autocad2025",
    ) -> tuple[Path, dict[str, object], dict[str, object]]:
        package = root / ("generated-runtime-package-" + profile)
        package.mkdir()
        output = ADAPTER_PROJECT.parent / "bin/Release" / _PROFILE_OUTPUTS[profile]
        for name in allowed_package_file_names(profile):
            if name == "README.md":
                source = PROJECT_ROOT / "native-cad/README.md"
            elif name == "native-bootstrap-context.template.json":
                source = PROJECT_ROOT / "native-cad/templates" / name
            else:
                source = output / name
            self.assertTrue(source.is_file(), source)
            shutil.copy2(source, package / name)
        runtime = build_runtime_package_descriptor(
            profile=profile,
            components=component_records_from_directory(
                package,
                profile=profile,
            ),
        )
        allowed = [
            _file_record(
                package / name,
                name=name,
                role=(
                    "runtime"
                    if name
                    in {
                        component["name"]
                        for component in runtime["components"]
                    }
                    else "auxiliary"
                ),
            )
            for name in allowed_package_file_names(profile)
        ]
        receipt = build_adapter_receipt(
            profile=profile,
            configuration="Release",
            runtime_package=runtime,
            allowed_files=allowed,
            sdk_input_fingerprints={
                "AcMgd.dll": "a" * 64,
                "AcDbMgd.dll": "b" * 64,
                "AcCoreMgd.dll": "c" * 64,
            },
        )
        return package, runtime, receipt

    def test_generated_package_receipts_validate_all_stub_profiles(self) -> None:
        """Every explicit stub profile yields one complete receipt package."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for profile, framework in _PROFILE_OUTPUTS.items():
                with self.subTest(profile=profile):
                    package, runtime, receipt = self._generated_package(
                        root,
                        profile=profile,
                    )
                    self.assertEqual(framework, runtime["target_framework"])
                    self.assertEqual(
                        receipt,
                        verify_adapter_package_against_receipt(package, receipt),
                    )

    def test_generated_package_receipt_and_csharp_fingerprint_agree(self) -> None:
        """Build output -> receipt -> Python/C# package fingerprint parity."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, runtime, receipt = self._generated_package(root)
            receipt_path = root / "private-receipt.json"
            receipt_path.write_bytes(canonical_json_bytes(receipt))
            parsed = strict_json_loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt, validate_adapter_receipt(parsed))
            self.assertEqual(
                receipt,
                verify_adapter_package_against_receipt(package, parsed),
            )

            generated = _run(
                "run",
                "--project",
                str(ADAPTER_TEST_PROJECT),
                "-c",
                "Release",
                "--no-build",
                "-p:CadHostProfile=autocad2025",
                "--",
                "runtime-package-fingerprint",
                str(package),
            )
            self.assertEqual(0, generated.returncode, _output(generated))
            self.assertEqual(
                runtime["fingerprint"],
                _output(generated).strip(),
            )

    @unittest.skipUnless(
        shutil.which("pwsh") or shutil.which("powershell"),
        "PowerShell is required for build-script fingerprint parity",
    )
    def test_build_script_runtime_fingerprint_matches_generated_receipt(self) -> None:
        """Exercise the production PowerShell fingerprint implementation."""

        with tempfile.TemporaryDirectory() as temporary:
            package, runtime, _receipt = self._generated_package(Path(temporary))
            driver = r"""
$errors = $null
. $env:LPF_POWERSHELL_COMPATIBILITY_SCRIPT
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:LPF_BUILD_SCRIPT, [ref]$null, [ref]$errors)
if ($errors) { throw "build script did not parse" }
$wanted = @(
    "Fail-Closed",
    "Get-OrdinalSortedNames",
    "Get-RuntimeComponentNames",
    "Get-Sha256OfUtf8",
    "Get-RuntimePackageFingerprint",
    "Get-ReceiptFingerprint"
)
$definitions = $ast.FindAll(
    {
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $wanted -contains $node.Name
    },
    $true)
foreach ($name in $wanted) {
    $definition = @($definitions | Where-Object { $_.Name -eq $name })
    if ($definition.Count -ne 1) { throw "build function not found" }
    if (
        $definition[0].Parent -isnot [System.Management.Automation.Language.NamedBlockAst] -or
        $definition[0].Parent.Parent -isnot [System.Management.Automation.Language.ScriptBlockAst]
    ) {
        throw "build function is nested instead of script-scoped"
    }
    . ([scriptblock]::Create($definition[0].Extent.Text))
}
$script:RuntimePackageFormat = "liang-pingfa/autocad-runtime-package/v1"
$script:ReceiptSchemaVersion = "liang-pingfa/autocad-adapter-build-receipt/v2"
$script:ReceiptFormat = "liang-pingfa/autocad-adapter-build-receipt-format/v1"
$script:AdapterAssemblyName = "LiangPingfa.NativeCad.AutoCAD.Adapter.dll"
$script:CoreAssemblyName = "LiangPingfa.NativeCad.Core.dll"
$script:ProtocolAssemblyName = "LiangPingfa.NativeCad.Protocol.dll"
$script:AdapterDepsName = "LiangPingfa.NativeCad.AutoCAD.Adapter.deps.json"
$components = ConvertFrom-JsonToDeterministicHashtable $env:LPF_RUNTIME_COMPONENTS
Get-RuntimePackageFingerprint "autocad2025" "net8.0-windows" @($components)
$receipt = ConvertFrom-JsonToDeterministicHashtable $env:LPF_RUNTIME_RECEIPT
Get-ReceiptFingerprint $receipt
"""
            completed = subprocess.run(
                [
                    shutil.which("pwsh") or shutil.which("powershell") or "",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    driver,
                ],
                cwd=PROJECT_ROOT,
                env={
                    **os.environ,
                    "LPF_BUILD_SCRIPT": str(BUILD_SCRIPT),
                    "LPF_POWERSHELL_COMPATIBILITY_SCRIPT": str(
                        POWERSHELL_COMPATIBILITY_SCRIPT
                    ),
                    "LPF_RUNTIME_COMPONENTS": json.dumps(runtime["components"]),
                    "LPF_RUNTIME_RECEIPT": json.dumps(_receipt),
                },
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, _output(completed))
            self.assertEqual(
                [
                    runtime["fingerprint"],
                    _receipt["integrity"]["sha256"],
                ],
                _output(completed).splitlines(),
            )

    def test_component_substitution_and_package_inventory_fail_closed(self) -> None:
        """Every critical file, including deps metadata, is package-bound."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, runtime, receipt = self._generated_package(root)
            self.assertIn(
                ADAPTER_DEPS,
                {component["name"] for component in runtime["components"]},
            )
            for component in runtime["components"]:
                with self.subTest(component=component["name"]):
                    candidate = root / ("tamper-" + component["name"])
                    shutil.copytree(package, candidate)
                    path = candidate / component["name"]
                    original = path.read_bytes()
                    path.write_bytes(
                        bytes([original[0] ^ 0x01]) + original[1:]
                    )
                    self.assertEqual(len(original), path.stat().st_size)
                    with self.assertRaises(ValueError):
                        verify_adapter_package_against_receipt(candidate, receipt)

            renamed = root / "renamed"
            shutil.copytree(package, renamed)
            (renamed / ADAPTER_ASSEMBLY).rename(
                renamed / ("renamed-" + ADAPTER_ASSEMBLY)
            )
            with self.assertRaises(ValueError):
                verify_adapter_package_against_receipt(renamed, receipt)

            case_collision = deepcopy(runtime["components"])
            case_collision.append(
                {
                    **case_collision[1],
                    "name": case_collision[1]["name"].upper(),
                }
            )
            with self.assertRaises(ValueError):
                runtime_package_fingerprint(
                    format_version=runtime["format_version"],
                    profile=runtime["profile"],
                    target_framework=runtime["target_framework"],
                    components=case_collision,
                )

            missing_metadata = root / "missing-deps"
            shutil.copytree(package, missing_metadata)
            (missing_metadata / ADAPTER_DEPS).unlink()
            with self.assertRaises(ValueError):
                verify_adapter_package_against_receipt(missing_metadata, receipt)
            missing_csharp = _run(
                "run",
                "--project",
                str(ADAPTER_TEST_PROJECT),
                "-c",
                "Release",
                "--no-build",
                "-p:CadHostProfile=autocad2025",
                "--",
                "runtime-package-fingerprint",
                str(missing_metadata),
            )
            self.assertNotEqual(0, missing_csharp.returncode)

            extra_vendor = root / "extra-vendor"
            shutil.copytree(package, extra_vendor)
            (extra_vendor / "AcMgd.dll").write_bytes(b"not-a-vendor-binary")
            with self.assertRaises(ValueError):
                verify_adapter_package_against_receipt(extra_vendor, receipt)
            extra_csharp = _run(
                "run",
                "--project",
                str(ADAPTER_TEST_PROJECT),
                "-c",
                "Release",
                "--no-build",
                "-p:CadHostProfile=autocad2025",
                "--",
                "runtime-package-fingerprint",
                str(extra_vendor),
            )
            self.assertNotEqual(0, extra_csharp.returncode)

            extra_stub = root / "extra-stub"
            shutil.copytree(package, extra_stub)
            (extra_stub / "LiangPingfa.NativeCad.AutoCAD.ApiStubs.dll").write_bytes(
                b"not-a-stub-binary"
            )
            with self.assertRaises(ValueError):
                verify_adapter_package_against_receipt(extra_stub, receipt)

            tampered_receipt = deepcopy(receipt)
            tampered_receipt["allowed_files"][0]["sha256"] = "0" * 64
            with self.assertRaises(ValueError):
                validate_adapter_receipt(tampered_receipt)

    def test_concrete_config_rejects_package_directory_switch(self) -> None:
        """The config cannot point its adapter entry at another package root."""

        with tempfile.TemporaryDirectory() as temporary:
            package, runtime, _receipt = self._generated_package(Path(temporary))
            configured = synthetic_config()
            configured["adapter"] = {
                "id": AUTOCAD_ADAPTER_ID,
                "profile": "autocad2025",
                "version": "2.0.0",
            }
            configured["host_compatibility"].update(
                {
                    "host_product": "autocad",
                    "host_release": "2025",
                    "host_runtime": "net8",
                }
            )
            configured["required_capabilities"].append("translate_dbtext/v1")
            configured["operation_profiles"]["delete_auxiliary_overlay_text/v1"] = False
            configure_autocad_runtime_package(
                configured,
                "autocad2025",
                directory=str(package),
            )
            configured["runtime_package"] = {
                **runtime,
                "directory": str(package),
            }
            adapter = next(
                item
                for item in configured["runtime_package"]["components"]
                if item["name"] == ADAPTER_ASSEMBLY
            )
            for plugin in configured["plugins"].values():
                plugin.update(
                    {
                        "path": str(package / ADAPTER_ASSEMBLY),
                        "sha256": adapter["sha256"],
                        "runtime_package_fingerprint": runtime["fingerprint"],
                    }
                )
            self.assertEqual(
                runtime["fingerprint"],
                validate_native_contract("config", configured)["runtime_package"][
                    "fingerprint"
                ],
            )
            switched = deepcopy(configured)
            switched["runtime_package"]["directory"] = str(
                package.parent / "switched-runtime-package"
            )
            with self.assertRaises(PipelineError):
                validate_native_contract("config", switched)


if __name__ == "__main__":
    unittest.main()
