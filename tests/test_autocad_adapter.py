"""SDK-free structural and fail-closed checks for the licensed adapter source."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from liang_pingfa_review.canonical import (
    CanonicalJsonError,
    canonical_json_bytes,
    canonical_sha256,
)
from liang_pingfa_review.native_contracts import (
    MAX_NATIVE_GEOMETRY_JSON_BYTES,
    opaque_embedded_json_rules,
    require_active_native_contract,
    validate_native_contract,
)
from liang_pingfa_review.errors import ErrorCode, PipelineError
from liang_pingfa_review.native_bridge import (
    NativeSessionClockReading,
    ProcessIdentity,
    prepare_native_session_from_bootstrap,
    validate_pipe_name,
)
from liang_pingfa_review.ownership import (
    FileIdentity,
    OwnedPathBinding,
    WindowsFileOwnershipBackend,
    platform_backend,
    secure_private_staging_directory,
    secure_private_staging_file,
)
from liang_pingfa_review.runtime_package import (
    ADAPTER_ASSEMBLY,
    allowed_package_file_names,
    build_adapter_receipt,
    build_runtime_package_descriptor,
    component_records_from_directory,
    required_runtime_component_names,
    validate_adapter_receipt,
    verify_adapter_package_against_receipt,
)
from tests.support.synthetic_native import (
    configure_autocad_runtime_package,
    config as synthetic_native_config,
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
CORE_TEST_PROJECT = (
    PROJECT_ROOT
    / "native-cad/tests/LiangPingfa.NativeCad.Core.Tests"
    / "LiangPingfa.NativeCad.Core.Tests.csproj"
)
REALHOST_TEST_PROJECT = (
    PROJECT_ROOT
    / "native-cad/tests/LiangPingfa.NativeCad.AutoCAD.RealHost.Tests"
    / "LiangPingfa.NativeCad.AutoCAD.RealHost.Tests.csproj"
)
ADAPTER_ROOT = ADAPTER_PROJECT.parent
BUILD_ADAPTER_SCRIPT = PROJECT_ROOT / "native-cad/scripts/build-autocad-adapter.ps1"
QUALIFY_REAL_HOST_SCRIPT = PROJECT_ROOT / "native-cad/scripts/qualify-real-host.ps1"
POWERSHELL_COMPATIBILITY_SCRIPT = (
    PROJECT_ROOT / "native-cad/scripts/powershell-compatibility.ps1"
)
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


def _powershell_executables() -> tuple[str, ...]:
    """Return each installed supported PowerShell host in deterministic order."""

    candidates = (
        shutil.which("powershell.exe") or shutil.which("powershell"),
        shutil.which("pwsh"),
    )
    executables = tuple(
        dict.fromkeys(executable for executable in candidates if executable)
    )
    if not executables:
        raise unittest.SkipTest("PowerShell is required for Windows script tests")
    return executables


def _powershell() -> str:
    """Return the test-selected host, defaulting to Windows PowerShell 5.1."""

    selected = os.environ.get("LPF_TEST_POWERSHELL")
    if selected:
        selected_key = os.path.normcase(os.path.abspath(selected))
        available = {
            os.path.normcase(os.path.abspath(executable))
            for executable in _powershell_executables()
        }
        if selected_key not in available:
            raise unittest.SkipTest("selected PowerShell host is unavailable")
        return selected
    return _powershell_executables()[0]


def _make_private_directory(path: Path) -> None:
    """Create a generated current-user/SYSTEM-only directory for script tests."""

    path.mkdir()
    if os.name != "nt":
        raise unittest.SkipTest("private Windows DACL test requires Windows")
    # The broad temporary parent is intentional: only this generated root is
    # an operator-private input.  Apply and verify it through the production
    # API rather than icacls, whose inheritance removal can retain copied
    # broad ACEs on GitHub-hosted runners.
    secure_private_staging_directory(path, WindowsFileOwnershipBackend())


def _make_private_file(path: Path) -> None:
    """Apply the same generated current-user/SYSTEM-only DACL to one file."""

    if os.name != "nt":
        raise unittest.SkipTest("private Windows DACL test requires Windows")
    backend = WindowsFileOwnershipBackend()
    payload = path.read_bytes()
    path.unlink()
    opened = backend.create_private_file(path)
    try:
        # Recreate it through the production private-creation API, which
        # applies and verifies the DACL through the retained write-DAC handle
        # before restoring generated bytes.
        secure_private_staging_file(opened, backend)
        opened.write_bytes(payload)
    finally:
        opened.close()


class AutoCadAdapterSourceTests(unittest.TestCase):
    """These checks do not load Autodesk assemblies or inspect a DWG."""

    def test_powershell_compatibility_helper_has_exact_cross_host_results(self) -> None:
        """Keep hex and parsed JSON values byte-for-byte stable in both shells."""

        helper = POWERSHELL_COMPATIBILITY_SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("[Convert]::ToHexString", helper)
        self.assertNotIn("-AsHashtable", helper)
        driver = r"""
. $env:LPF_POWERSHELL_COMPATIBILITY_SCRIPT
$value = ConvertFrom-JsonToDeterministicHashtable @'
{"MixedCase":{"Nested":true},"items":[null,false,true,1,2.5,"text"]}
'@
$dictionary = [Collections.Generic.Dictionary[string, object]]::new(
    [StringComparer]::Ordinal
)
$dictionary.Add("MixedCase", $true)
[ordered]@{
    hash = ConvertTo-LowercaseHex ([byte[]](0, 15, 16, 255))
    key_order = @($value.Keys)
    nested_key = @($value["MixedCase"].Keys)
    null_value = $null -eq $value["items"][0]
    bool_values = @($value["items"][1], $value["items"][2])
    number_values = @($value["items"][3], $value["items"][4])
    map_key_results = [ordered]@{
        ordered = @(
            (Test-MapContainsKey $value "MixedCase"),
            (Test-MapContainsKey $value "mixedcase")
        )
        hashtable = @(
            (Test-MapContainsKey @{ MixedCase = $true } "MixedCase"),
            (Test-MapContainsKey @{ MixedCase = $true } "mixedcase")
        )
        dictionary = @(
            (Test-MapContainsKey $dictionary "missing"),
            (Test-MapContainsKey $dictionary "MixedCase")
        )
        object = @(
            (Test-MapContainsKey ([pscustomobject]@{ MixedCase = $true }) "MixedCase"),
            (Test-MapContainsKey ([pscustomobject]@{ MixedCase = $true }) "mixedcase")
        )
    }
} | ConvertTo-Json -Compress
"""
        results: list[dict[str, object]] = []
        for executable in _powershell_executables():
            result = subprocess.run(
                [
                    executable,
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
                    "LPF_POWERSHELL_COMPATIBILITY_SCRIPT": os.fspath(
                        POWERSHELL_COMPATIBILITY_SCRIPT
                    ),
                },
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, _output(result))
            results.append(json.loads(_output(result)))

        expected = {
            "hash": "000f10ff",
            "key_order": ["MixedCase", "items"],
            "nested_key": ["Nested"],
            "null_value": True,
            "bool_values": [False, True],
            "number_values": [1, 2.5],
            "map_key_results": {
                "ordered": [True, False],
                "hashtable": [True, False],
                "dictionary": [False, True],
                "object": [True, False],
            },
        }
        self.assertEqual([expected] * len(results), results)

    def test_runtime_package_state_guard_compares_component_keys_ordinally(self) -> None:
        """Ordered receipt maps must be guarded without Hashtable-only APIs."""

        driver = r"""
. $env:LPF_POWERSHELL_COMPATIBILITY_SCRIPT
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:LPF_QUALIFICATION_SCRIPT, [ref]$null, [ref]$errors)
if ($errors) { throw "qualification script did not parse" }
$wanted = @(
    "Fail-Qualification",
    "Assert-SameFileState",
    "Assert-SameRuntimePackageState"
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
    if ($definition.Count -ne 1) { throw "qualification function not found" }
    . ([scriptblock]::Create($definition[0].Extent.Text))
}
function New-FileState([int]$Size, [string]$Hash) {
    return [ordered]@{
        file_identity = "0x1"
        byte_size = $Size
        creation_time_utc = "2026-01-01T00:00:00.0000000Z"
        last_write_time_utc = "2026-01-01T00:00:00.0000000Z"
        sha256 = $Hash
    }
}
function New-ComponentMap([string]$Kind, [object[]]$Entries) {
    if ($Kind -eq "ordered") {
        $map = [System.Collections.Specialized.OrderedDictionary]::new(
            [StringComparer]::Ordinal
        )
    } else {
        $map = @{}
    }
    foreach ($entry in $Entries) {
        $map[$entry[0]] = $entry[1]
    }
    return $map
}
function New-State([string]$Kind, [object[]]$Entries) {
    return [ordered]@{
        runtime_package_fingerprint = "f" * 64
        receipt_state = New-FileState 10 ("a" * 64)
        components = New-ComponentMap $Kind $Entries
    }
}
$component = New-FileState 10 ("b" * 64)
$driftedHash = New-FileState 10 ("c" * 64)
$driftedSize = New-FileState 11 ("b" * 64)
$cases = @(
    @{ name = "ordered-matching"; before = (New-State "ordered" @(, @("Component.dll", $component))); after = (New-State "ordered" @(, @("Component.dll", $component))); success = $true; error = $null },
    @{ name = "hashtable-matching"; before = (New-State "hashtable" @(, @("Component.dll", $component))); after = (New-State "hashtable" @(, @("Component.dll", $component))); success = $true; error = $null },
    @{ name = "ordered-missing"; before = (New-State "ordered" @(, @("Component.dll", $component))); after = (New-State "ordered" @()); success = $false; error = "component set changed" },
    @{ name = "hashtable-extra"; before = (New-State "hashtable" @(, @("Component.dll", $component))); after = (New-State "hashtable" @(@("Component.dll", $component), @("Extra.dll", $component))); success = $false; error = "component set changed" },
    @{ name = "ordered-case-change"; before = (New-State "ordered" @(, @("Component.dll", $component))); after = (New-State "ordered" @(, @("component.dll", $component))); success = $false; error = "component set changed" },
    @{ name = "hashtable-hash-drift"; before = (New-State "hashtable" @(, @("Component.dll", $component))); after = (New-State "hashtable" @(, @("Component.dll", $driftedHash))); success = $false; error = "component changed" },
    @{ name = "ordered-size-drift"; before = (New-State "ordered" @(, @("Component.dll", $component))); after = (New-State "ordered" @(, @("Component.dll", $driftedSize))); success = $false; error = "component changed" }
)
$observed = foreach ($case in $cases) {
    try {
        Assert-SameRuntimePackageState $case.before $case.after
        [ordered]@{ name = $case.name; success = $true; error = $null }
    } catch {
        [ordered]@{ name = $case.name; success = $false; error = $_.Exception.Message }
    }
}
@($observed) | ConvertTo-Json -Compress
"""
        expected = {
            "ordered-matching": (True, None),
            "hashtable-matching": (True, None),
            "ordered-missing": (False, "component set changed"),
            "hashtable-extra": (False, "component set changed"),
            "ordered-case-change": (False, "component set changed"),
            "hashtable-hash-drift": (False, "component changed"),
            "ordered-size-drift": (False, "component changed"),
        }
        for executable in _powershell_executables():
            with self.subTest(powershell=Path(executable).name):
                result = subprocess.run(
                    [
                        executable,
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
                        "LPF_POWERSHELL_COMPATIBILITY_SCRIPT": os.fspath(
                            POWERSHELL_COMPATIBILITY_SCRIPT
                        ),
                        "LPF_QUALIFICATION_SCRIPT": os.fspath(
                            QUALIFY_REAL_HOST_SCRIPT
                        ),
                    },
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(0, result.returncode, _output(result))
                observed = {
                    entry["name"]: (entry["success"], entry["error"])
                    for entry in json.loads(_output(result))
                }
                self.assertEqual(
                    set(expected),
                    set(observed),
                )
                for name, (succeeds, error) in expected.items():
                    self.assertEqual(succeeds, observed[name][0], name)
                    if error is None:
                        self.assertIsNone(observed[name][1], name)
                    else:
                        self.assertIn(error, observed[name][1], name)

    @unittest.skipUnless(
        os.name == "nt",
        "PowerShell operator-script matrix requires Windows",
    )
    def test_operator_script_matrix_uses_each_available_powershell_host(self) -> None:
        """Exercise generated package, receipt rejection, and valid receipt paths."""

        test_names = (
            "tests.test_autocad_adapter.AutoCadAdapterSourceTests."
            "test_licensed_build_script_is_explicit_and_fake_sdk_dry_run_never_packages_vendor_files",
            "tests.test_autocad_adapter.AutoCadAdapterSourceTests."
            "test_fake_sdk_finalization_is_explicitly_test_only_and_not_qualified",
            "tests.test_autocad_adapter.AutoCadAdapterSourceTests."
            "test_qualification_script_dry_run_never_launches_or_qualifies_a_host",
        )
        for executable in _powershell_executables():
            with self.subTest(powershell=Path(executable).name):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "unittest",
                        *test_names,
                    ],
                    cwd=PROJECT_ROOT,
                    env={
                        **os.environ,
                        "LPF_TEST_POWERSHELL": executable,
                        "PYTHONPATH": os.pathsep.join(
                            (
                                os.fspath(PROJECT_ROOT / "src"),
                                os.environ.get("PYTHONPATH", ""),
                            )
                        ),
                    },
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(0, result.returncode, _output(result))

    def test_project_has_explicit_profiles_and_fail_closed_sdk_gate(self) -> None:
        text = ADAPTER_PROJECT.read_text(encoding="utf-8")
        for profile, framework in (
            ("autocad2024", "net48"),
            ("autocad2025", "net8.0-windows"),
            ("autocad2026", "net10.0-windows"),
        ):
            with self.subTest(profile=profile):
                self.assertIn(profile, text)
                self.assertIn(framework, text)
        self.assertNotIn("tssd", text.casefold())
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

    def test_net8_and_net10_stub_profiles_compile_exact_adapter_source(self) -> None:
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

        for framework in ("net8.0-windows", "net10.0-windows"):
            output = ADAPTER_ROOT / "bin/Release" / framework
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

    def test_realhost_runner_is_sdk_free_for_every_autocad_profile(self) -> None:
        """The runner invokes external package paths; it never links an adapter."""

        project = REALHOST_TEST_PROJECT.read_text(encoding="utf-8")
        self.assertNotIn("LiangPingfa.NativeCad.AutoCAD.Adapter", project)
        self.assertNotIn("ProjectReference", project)
        for profile in (
            "autocad2024",
            "autocad2025",
            "autocad2026",
        ):
            with self.subTest(profile=profile):
                result = _run(
                    "build",
                    str(REALHOST_TEST_PROJECT),
                    "-c",
                    "Release",
                    "--nologo",
                    f"-p:CadHostProfile={profile}",
                )
                self.assertEqual(0, result.returncode, _output(result))

    def test_tssd_profiles_are_rejected_by_current_adapter_build_and_scripts(self) -> None:
        """A future vendor adapter needs a distinct identity; this one is AutoCAD-only."""

        rejected_build = _run(
            "build",
            str(ADAPTER_PROJECT),
            "-c",
            "Release",
            "--nologo",
            "-p:BuildAutoCadAdapter=true",
            "-p:UseAutodeskApiStubs=true",
            "-p:CadHostProfile=tssd2025",
        )
        self.assertNotEqual(0, rejected_build.returncode)
        self.assertIn("CadHostProfile", _output(rejected_build))

        for script in (BUILD_ADAPTER_SCRIPT, QUALIFY_REAL_HOST_SCRIPT):
            with self.subTest(script=script.name):
                rejected_script = subprocess.run(
                    [
                        _powershell(),
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        os.fspath(script),
                        "-Profile",
                        "tssd2025",
                    ],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(0, rejected_script.returncode)
                self.assertIn("tssd2025", _output(rejected_script).casefold())

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

    def test_licensed_build_script_is_explicit_and_fake_sdk_dry_run_never_packages_vendor_files(
        self,
    ) -> None:
        """Generated SDK-shaped files prove only fail-closed script behavior."""

        script = BUILD_ADAPTER_SCRIPT.read_text(encoding="utf-8")
        for required in (
            "[string]$Profile",
            "[string]$CadSdkDir",
            "UseAutodeskApiStubs=false",
            "TargetFramework=$framework",
            "FileMode]::CreateNew",
            "Get-Sha256File",
            "sdk_input_fingerprints",
            "runtime_package",
            "allowed_files",
            "Get-RuntimePackageFingerprint",
            "Assert-DependencyMetadata",
            "LiangPingfa.NativeCad.AutoCAD.ApiStubs.dll",
        ):
            self.assertIn(required, script)
        self.assertNotIn("dotnet pack", script.casefold())
        self.assertNotIn("dotnet publish", script.casefold())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "generated-sdk"
            sdk.mkdir()
            for name in ("AcMgd.dll", "AcDbMgd.dll", "AcCoreMgd.dll"):
                (sdk / name).write_bytes(b"MZgenerated-sdk-signature")
            broad_private_root = root / "broad-private-root"
            broad_private_root.mkdir()
            broad_root_result = subprocess.run(
                [
                    _powershell(),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    os.fspath(BUILD_ADAPTER_SCRIPT),
                    "-Profile",
                    "autocad2026",
                    "-CadSdkDir",
                    os.fspath(sdk),
                    "-PackageDirectory",
                    os.fspath(root / "broad-operator-package"),
                    "-PrivateRoot",
                    os.fspath(broad_private_root),
                    "-ReceiptPath",
                    os.fspath(broad_private_root / "build-receipt.json"),
                    "-DryRun",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, broad_root_result.returncode)
            self.assertIn(
                "private root grants a non-private SID",
                _output(broad_root_result),
            )
            private = root / "private"
            _make_private_directory(private)
            package = root / "operator-package"
            receipt = private / "build-receipt.json"
            result = subprocess.run(
                [
                    _powershell(),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    os.fspath(BUILD_ADAPTER_SCRIPT),
                    "-Profile",
                    "autocad2026",
                    "-CadSdkDir",
                    os.fspath(sdk),
                    "-PackageDirectory",
                    os.fspath(package),
                    "-PrivateRoot",
                    os.fspath(private),
                    "-ReceiptPath",
                    os.fspath(receipt),
                    "-DryRun",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, _output(result))
            self.assertFalse(package.exists())
            self.assertFalse(receipt.exists())
            event = json.loads(_output(result))
            self.assertEqual("dry-run", event["status"])
            self.assertEqual("not-copied", event["vendor_binaries"])

            fake_real_build = subprocess.run(
                [
                    _powershell(),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    os.fspath(BUILD_ADAPTER_SCRIPT),
                    "-Profile",
                    "autocad2026",
                    "-CadSdkDir",
                    os.fspath(sdk),
                    "-PackageDirectory",
                    os.fspath(package),
                    "-PrivateRoot",
                    os.fspath(private),
                    "-ReceiptPath",
                    os.fspath(receipt),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, fake_real_build.returncode)
            self.assertFalse(package.exists())
            self.assertFalse(receipt.exists())

            missing = root / "missing-sdk"
            failed = subprocess.run(
                [
                    _powershell(),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    os.fspath(BUILD_ADAPTER_SCRIPT),
                    "-Profile",
                    "autocad2025",
                    "-CadSdkDir",
                    os.fspath(missing),
                    "-PackageDirectory",
                    os.fspath(package),
                    "-PrivateRoot",
                    os.fspath(private),
                    "-ReceiptPath",
                    os.fspath(receipt),
                    "-DryRun",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, failed.returncode)
            self.assertFalse(package.exists())

    def test_fake_sdk_finalization_is_explicitly_test_only_and_not_qualified(
        self,
    ) -> None:
        """Mocked real-mode invocation reaches finalization but marks its receipt."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "generated-sdk"
            sdk.mkdir()
            for name in ("AcMgd.dll", "AcDbMgd.dll", "AcCoreMgd.dll"):
                (sdk / name).write_bytes(b"MZgenerated-sdk-signature")
            private = root / "private"
            _make_private_directory(private)
            package = root / "operator-package"
            receipt = private / "test-only-build-receipt.json"

            # Produce repository-owned test output first; the invoked build
            # below is a fake SDK test and its dotnet process is deliberately
            # mocked rather than accepted as an Autodesk SDK build.
            stub_build = _run(
                "build",
                str(ADAPTER_PROJECT),
                "-c",
                "Release",
                "--nologo",
                "-p:BuildAutoCadAdapter=true",
                "-p:UseAutodeskApiStubs=true",
                "-p:CadHostProfile=autocad2026",
            )
            self.assertEqual(0, stub_build.returncode, _output(stub_build))
            fake_bin = root / "fake-dotnet"
            fake_bin.mkdir()
            (fake_bin / "dotnet.cmd").write_text(
                "@echo off\r\nexit /b 0\r\n",
                encoding="ascii",
            )
            finalized = subprocess.run(
                [
                    _powershell(),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    os.fspath(BUILD_ADAPTER_SCRIPT),
                    "-Profile",
                    "autocad2026",
                    "-CadSdkDir",
                    os.fspath(sdk),
                    "-PackageDirectory",
                    os.fspath(package),
                    "-PrivateRoot",
                    os.fspath(private),
                    "-ReceiptPath",
                    os.fspath(receipt),
                    "-TestOnlyFakeSdk",
                ],
                cwd=PROJECT_ROOT,
                env={
                    **os.environ,
                    "PATH": os.pathsep.join(
                        (os.fspath(fake_bin), os.environ["PATH"])
                    ),
                },
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, finalized.returncode, _output(finalized))
            self.assertEqual(
                "test-only-fake-sdk",
                json.loads(_output(finalized))["qualification"],
            )
            self.assertTrue(receipt.is_file())
            fixture_receipt = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(
                "syntax-only-fake-sdk",
                fixture_receipt["test_only"],
            )
            # A test-only receipt is not a qualified real receipt: strict
            # receipt validation is the qualification script's first package
            # gate, before a host can be launched.
            with self.assertRaises(ValueError):
                validate_adapter_receipt(fixture_receipt)

            self.assertEqual(
                set(allowed_package_file_names("autocad2026")),
                {entry.name for entry in package.iterdir()},
            )
            for forbidden in (
                "AcMgd.dll",
                "AcDbMgd.dll",
                "AcCoreMgd.dll",
                "LiangPingfa.NativeCad.AutoCAD.ApiStubs.dll",
            ):
                self.assertFalse((package / forbidden).exists(), forbidden)

            runtime_package = build_runtime_package_descriptor(
                profile="autocad2026",
                components=component_records_from_directory(
                    package,
                    profile="autocad2026",
                ),
            )
            runtime_names = set(required_runtime_component_names("autocad2026"))
            valid_receipt = build_adapter_receipt(
                profile="autocad2026",
                configuration="Release",
                runtime_package=runtime_package,
                allowed_files=[
                    {
                        "name": name,
                        "byte_size": (package / name).stat().st_size,
                        "sha256": sha256((package / name).read_bytes()).hexdigest(),
                        "role": (
                            "runtime"
                            if name in runtime_names
                            else "auxiliary"
                        ),
                    }
                    for name in allowed_package_file_names("autocad2026")
                ],
                sdk_input_fingerprints={
                    name: sha256((sdk / name).read_bytes()).hexdigest()
                    for name in ("AcMgd.dll", "AcDbMgd.dll", "AcCoreMgd.dll")
                },
            )
            self.assertEqual(
                valid_receipt,
                verify_adapter_package_against_receipt(package, valid_receipt),
            )

    def test_qualification_script_dry_run_never_launches_or_qualifies_a_host(self) -> None:
        """Mock files exercise only argument/DACL/package gates in dry-run."""

        script = QUALIFY_REAL_HOST_SCRIPT.read_text(encoding="utf-8")
        for required in (
            "LIANG_PINGFA_RUN_REAL_HOST",
            "native-session",
            "native-audit",
            "native-plan",
            "native-apply",
            "native-verify",
            "Get-BoundFileState",
            "Assert-AuditedHostBinding",
            "$null = & $python @Arguments",
            "load_native_artifact",
            "require_qualification_host_binding",
            "operator-must-create-a-fresh-bootstrap-for-apply",
        ):
            self.assertIn(required, script)
        for forbidden in ("SendKeys", "SendInput", "SetForegroundWindow", "NETLOAD"):
            self.assertNotIn(forbidden, script)
        summary_section = script[script.index('Write-PrivateJson $summaryPath'):]
        self.assertNotIn("host_executable_sha256", summary_section)
        self.assertNotIn("core_console_sha256", summary_section)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "private-work"
            evidence = root / "private-evidence"
            _make_private_directory(work)
            _make_private_directory(evidence)
            source = work / "liang-pingfa-qualification-fixture.dwg"
            source.write_bytes(b"AC1032")
            bootstrap = work / "bootstrap.json"
            config = work / "config.json"
            bootstrap.write_text("{}", encoding="utf-8")
            package = root / "adapter-package"
            package.mkdir()
            runtime_names = {
                "LiangPingfa.NativeCad.AutoCAD.Adapter.dll",
                "LiangPingfa.NativeCad.Core.dll",
                "LiangPingfa.NativeCad.Protocol.dll",
                "LiangPingfa.NativeCad.AutoCAD.Adapter.deps.json",
            }
            components = []
            allowed_files = []
            for index, name in enumerate(allowed_package_file_names("autocad2026")):
                payload = ("generated-package-" + name).encode("utf-8")
                (package / name).write_bytes(payload)
                digest = sha256(payload).hexdigest()
                if name in runtime_names:
                    components.append(
                        {
                            "name": name,
                            "byte_size": len(payload),
                            "sha256": digest,
                        }
                    )
                allowed_files.append(
                    {
                        "name": name,
                        "byte_size": len(payload),
                        "sha256": digest,
                        "role": "runtime" if name in runtime_names else "auxiliary",
                    }
                )
            runtime_package = build_runtime_package_descriptor(
                profile="autocad2026",
                components=components,
            )
            command_processor = Path(
                os.environ.get(
                    "ComSpec",
                    os.fspath(
                        Path(os.environ.get("SystemRoot", "Windows"))
                        / "System32"
                        / "cmd.exe"
                    ),
                )
            )
            python_executable = Path(sys.executable)
            executable_hash = sha256(command_processor.read_bytes()).hexdigest()
            adapter_component = next(
                component
                for component in runtime_package["components"]
                if component["name"] == ADAPTER_ASSEMBLY
            )
            native_config = synthetic_native_config()
            native_config["adapter"] = {
                "id": "liang-pingfa-autocad-adapter",
                "profile": "autocad2026",
                "version": "2.0.0",
            }
            configure_autocad_runtime_package(
                native_config,
                "autocad2026",
                directory=os.fspath(package),
            )
            native_config["runtime_package"] = {
                **runtime_package,
                "directory": os.fspath(package),
            }
            native_config["required_capabilities"] = list(
                AUTOCAD_ADAPTER_CAPABILITIES
            )
            native_config["host_compatibility"].update(
                {
                    "host_family": "autocad",
                    "host_product": "autocad",
                    "host_release": "2026",
                    "host_runtime": "net10",
                }
            )
            native_config["operation_profiles"].update(
                {
                    "translate_dbtext/v1": True,
                    "delete_auxiliary_overlay_text/v1": False,
                    "create_review_marker/v1": False,
                }
            )
            native_config["full_host"] = {
                "path": os.fspath(command_processor),
                "sha256": executable_hash,
            }
            native_config["core_console"] = {
                "path": os.fspath(command_processor),
                "sha256": executable_hash,
            }
            for plugin in native_config["plugins"].values():
                plugin.update(
                    {
                        "id": "liang-pingfa-autocad-plugin",
                        "version": "2.0.0",
                        "path": os.fspath(package / ADAPTER_ASSEMBLY),
                        "sha256": adapter_component["sha256"],
                        "runtime_package_fingerprint": runtime_package[
                            "fingerprint"
                        ],
                    }
                )
            config.write_bytes(canonical_json_bytes(native_config))
            _make_private_file(config)
            receipt = work / "build-receipt.json"
            receipt_payload = build_adapter_receipt(
                profile="autocad2026",
                configuration="Release",
                runtime_package=runtime_package,
                allowed_files=allowed_files,
                sdk_input_fingerprints={
                    "AcMgd.dll": "a" * 64,
                    "AcDbMgd.dll": "b" * 64,
                    "AcCoreMgd.dll": "c" * 64,
                },
            )
            receipt.write_bytes(canonical_json_bytes(receipt_payload))
            _make_private_file(receipt)
            self.assertEqual(
                runtime_package,
                verify_adapter_package_against_receipt(
                    package,
                    validate_adapter_receipt(
                        json.loads(receipt.read_text(encoding="utf-8"))
                    ),
                )["runtime_package"],
            )
            self.assertEqual(
                runtime_package["fingerprint"],
                validate_native_contract("config", native_config)["runtime_package"][
                    "fingerprint"
                ],
            )
            result = subprocess.run(
                [
                    _powershell(),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    os.fspath(QUALIFY_REAL_HOST_SCRIPT),
                    "-Phase",
                    "audit",
                    "-Profile",
                    "autocad2026",
                    "-PythonExecutable",
                    os.fspath(python_executable),
                    "-HostExecutable",
                    os.fspath(command_processor),
                    "-CoreConsoleExecutable",
                    os.fspath(command_processor),
                    "-AdapterPackage",
                    os.fspath(package),
                    "-ReceiptPath",
                    os.fspath(receipt),
                    "-NativeConfig",
                    os.fspath(config),
                    "-Bootstrap",
                    os.fspath(bootstrap),
                    "-SessionPath",
                    os.fspath(work / "session.json"),
                    "-SourceDrawing",
                    os.fspath(source),
                    "-WorkRoot",
                    os.fspath(work),
                    "-EvidenceOutput",
                    os.fspath(evidence),
                    "-DryRun",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, _output(result))
            event = json.loads(_output(result))
            self.assertEqual("dry-run", event["status"])
            self.assertEqual("not-launched", event["host"])
            self.assertFalse((work / "liang-pingfa-realhost-qualification").exists())

            command = list(result.args)
            # Drive the non-dry-run path only through the package/component
            # guard and the first mock child gates.  This proves a valid
            # OrderedDictionary component map reaches the audited-binding
            # gate; it neither launches nor requires a proprietary host.
            mock_python = work / "generated-mock-python.cmd"
            mock_calls = work / "generated-mock-python-calls.txt"
            mock_binding = json.dumps(
                {
                    "components": [
                        component["name"]
                        for component in runtime_package["components"]
                    ],
                    "fingerprint": runtime_package["fingerprint"],
                },
                separators=(",", ":"),
            )
            mock_python.write_text(
                "@echo off\r\n"
                'if "%1"=="-c" (\r\n'
                f"  echo {mock_binding}\r\n"
                "  exit /b 0\r\n"
                ")\r\n"
                'echo %*>> "%LPF_QUALIFICATION_MOCK_CALLS%"\r\n'
                'echo {"status":"ok"}\r\n'
                "exit /b 0\r\n",
                encoding="ascii",
            )
            python_index = command.index("-PythonExecutable") + 1
            non_dry_run = [
                argument
                for argument in command
                if argument != "-DryRun"
            ]
            non_dry_run[python_index] = os.fspath(mock_python)
            preflight = subprocess.run(
                non_dry_run,
                cwd=PROJECT_ROOT,
                env=os.environ
                | {
                    "LIANG_PINGFA_RUN_REAL_HOST": "1",
                    "LPF_QUALIFICATION_MOCK_CALLS": os.fspath(mock_calls),
                },
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, preflight.returncode, _output(preflight))
            self.assertIn(
                "audited full-host binding does not match",
                _output(preflight),
            )
            self.assertTrue(mock_calls.is_file())
            mock_call_text = mock_calls.read_text(encoding="utf-8")
            for expected_child in (
                "native-session",
                "native-doctor",
                "native-audit",
            ):
                self.assertIn(expected_child, mock_call_text)
            self.assertFalse(
                (work / "liang-pingfa-realhost-qualification").exists()
            )

            # Windows PowerShell accepts an exact duplicate key in some
            # versions. Qualification must reject it through Python's strict
            # duplicate-key parser before this JSON reaches the compatibility
            # conversion helper.
            canonical_receipt = canonical_json_bytes(receipt_payload).decode("utf-8")
            receipt.write_text(
                '{"schema_version":"duplicate-key-probe",'
                + canonical_receipt[1:],
                encoding="utf-8",
            )
            _make_private_file(receipt)
            duplicate_key_result = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, duplicate_key_result.returncode)
            self.assertFalse((work / "liang-pingfa-realhost-qualification").exists())
            receipt.write_bytes(canonical_json_bytes(receipt_payload))
            _make_private_file(receipt)

            fake_receipt = json.loads(json.dumps(receipt_payload))
            fake_receipt["test_only"] = "syntax-only-fake-sdk"
            receipt.write_bytes(canonical_json_bytes(fake_receipt))
            _make_private_file(receipt)
            self.assertNotEqual(
                0,
                subprocess.run(
                    command,
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    check=False,
                ).returncode,
            )
            receipt.write_bytes(canonical_json_bytes(receipt_payload))
            _make_private_file(receipt)

            receipt_index = command.index("-ReceiptPath") + 1
            missing_receipt = list(command)
            missing_receipt[receipt_index] = os.fspath(work / "missing-receipt.json")
            self.assertNotEqual(
                0,
                subprocess.run(
                    missing_receipt,
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    check=False,
                ).returncode,
            )
            self.assertFalse((work / "liang-pingfa-realhost-qualification").exists())

            tampered_receipt = json.loads(json.dumps(receipt_payload))
            tampered_receipt["allowed_files"][0]["sha256"] = "0" * 64
            receipt.write_bytes(canonical_json_bytes(tampered_receipt))
            _make_private_file(receipt)
            self.assertNotEqual(
                0,
                subprocess.run(
                    command,
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    check=False,
                ).returncode,
            )
            receipt.write_bytes(canonical_json_bytes(receipt_payload))
            _make_private_file(receipt)

            unexpected = package / "AcMgd.dll"
            unexpected.write_bytes(b"generated-forbidden-extra")
            self.assertNotEqual(
                0,
                subprocess.run(
                    command,
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    check=False,
                ).returncode,
            )
            unexpected.unlink()
            self.assertFalse((work / "liang-pingfa-realhost-qualification").exists())

            rejected_tssd = subprocess.run(
                [
                    _powershell(),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    os.fspath(QUALIFY_REAL_HOST_SCRIPT),
                    "-Phase",
                    "audit",
                    "-Profile",
                    "tssd2026",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, rejected_tssd.returncode)
            self.assertFalse((work / "liang-pingfa-realhost-qualification").exists())

            # The SDK-free runner owns only binding preflight and argument
            # forwarding. The generated receipt above is deliberately passed
            # to a harmless private-script substitute so this test proves the
            # runner reaches the authoritative script boundary without
            # exposing a receipt path or content in its output.
            runner_root = root / "runner-repository"
            runner_script = (
                runner_root / "native-cad" / "scripts" / "qualify-real-host.ps1"
            )
            runner_script.parent.mkdir(parents=True)
            runner_script.write_text(
                """
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Captured
)
[IO.File]::WriteAllLines($env:LPF_REALHOST_ARGUMENT_CAPTURE, $Captured)
Write-Output '{"status":"ok","qualification":"argument-captured"}'
""".strip(),
                encoding="utf-8",
            )
            argument_capture = work / "runner-arguments.txt"
            runner_environment = os.environ.copy()
            runner_environment.update(
                {
                    "LPF_REALHOST_TESTS": "1",
                    "LIANG_PINGFA_RUN_REAL_HOST": "1",
                    "LPF_REALHOST_PHASE": "audit",
                    "LPF_REALHOST_PROFILE": "autocad2026",
                    "LPF_REALHOST_PYTHON": os.fspath(python_executable),
                    "LPF_REALHOST_HOST": os.fspath(command_processor),
                    "LPF_REALHOST_CORE_CONSOLE": os.fspath(command_processor),
                    "LPF_REALHOST_ADAPTER_PACKAGE": os.fspath(package),
                    "LIANG_PINGFA_REAL_HOST_RECEIPT": os.fspath(receipt),
                    "LPF_REALHOST_NATIVE_CONFIG": os.fspath(config),
                    "LPF_REALHOST_BOOTSTRAP": os.fspath(bootstrap),
                    "LPF_REALHOST_SESSION": os.fspath(work / "runner-session.json"),
                    "LPF_REALHOST_SOURCE": os.fspath(source),
                    "LPF_REALHOST_WORK_ROOT": os.fspath(work),
                    "LPF_REALHOST_EVIDENCE_OUTPUT": os.fspath(evidence),
                    "LPF_REALHOST_REPOSITORY_ROOT": os.fspath(runner_root),
                    "LPF_REALHOST_POWERSHELL": _powershell(),
                    "LPF_REALHOST_ARGUMENT_CAPTURE": os.fspath(argument_capture),
                }
            )
            runner_command = [
                "dotnet",
                "run",
                "--project",
                os.fspath(REALHOST_TEST_PROJECT),
                "-c",
                "Release",
                "--nologo",
            ]

            missing_receipt_environment = runner_environment.copy()
            del missing_receipt_environment["LIANG_PINGFA_REAL_HOST_RECEIPT"]
            missing_runner_receipt = subprocess.run(
                runner_command,
                cwd=PROJECT_ROOT,
                env=missing_receipt_environment,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, missing_runner_receipt.returncode)
            self.assertFalse(argument_capture.exists())

            nonexistent_receipt_environment = runner_environment.copy()
            nonexistent_receipt_environment["LIANG_PINGFA_REAL_HOST_RECEIPT"] = (
                os.fspath(work / "missing-runner-receipt.json")
            )
            nonexistent_runner_receipt = subprocess.run(
                runner_command,
                cwd=PROJECT_ROOT,
                env=nonexistent_receipt_environment,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, nonexistent_runner_receipt.returncode)
            self.assertFalse(argument_capture.exists())

            runner_result = subprocess.run(
                runner_command,
                cwd=PROJECT_ROOT,
                env=runner_environment,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, runner_result.returncode, _output(runner_result))
            self.assertNotIn(os.fspath(receipt), _output(runner_result))
            captured_arguments = argument_capture.read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, captured_arguments.count("-ReceiptPath"))
            receipt_argument_index = captured_arguments.index("-ReceiptPath")
            self.assertEqual(
                os.fspath(receipt),
                captured_arguments[receipt_argument_index + 1],
            )

    def test_qualification_file_identity_parser_accepts_only_one_prefixed_id(self) -> None:
        """Exercise the real PowerShell parser without invoking fsutil or a host."""

        cases = [
            {
                "name": "english-standard",
                "output": ["File ID is 0x000000000000000000050000000022ec"],
                "expected": "0x000000000000000000050000000022ec",
            },
            {
                "name": "localized-prefix",
                "output": ["任意本地化前缀：0x0123456789abcdef"],
                "expected": "0x0123456789abcdef",
            },
            {
                "name": "uppercase-prefix-and-hex",
                "output": ["FILE ID IS 0XABCDEF0123456789"],
                "expected": "0xabcdef0123456789",
            },
            {
                "name": "crlf-and-whitespace",
                "output": ["\r\n  File ID is\t0x1111111111111111  \r\n"],
                "expected": "0x1111111111111111",
            },
            {
                "name": "multiple",
                "output": ["File ID is 0x0123456789abcdef; copy 0xfedcba9876543210"],
                "expected": None,
            },
            {
                "name": "short",
                "output": ["File ID is 0x0123456789abcde"],
                "expected": None,
            },
            {
                "name": "nonhex-trailing-contamination",
                "output": ["File ID is 0x0123456789abcdefg"],
                "expected": None,
            },
            {
                "name": "overlong-trailing-contamination",
                "output": ["File ID is 0x" + "a" * 65],
                "expected": None,
            },
            {
                "name": "unprefixed-unrelated-hex",
                "output": ["Checksum 0123456789abcdef"],
                "expected": None,
            },
            {
                "name": "missing",
                "output": ["File identity is unavailable"],
                "expected": None,
            },
        ]
        driver = r"""
$source = [IO.File]::ReadAllText($env:LPF_QUALIFICATION_SCRIPT)
$function = [regex]::Match(
    $source,
    '(?ms)^function ConvertFrom-FsutilFileIdOutput\b.*?^}'
)
if (-not $function.Success) {
    throw "qualification parser function was not found"
}
function Fail-Qualification([string]$Message) {
    throw $Message
}
. ([scriptblock]::Create($function.Value))
$observed = foreach ($case in ($env:LPF_FILE_ID_CASES | ConvertFrom-Json)) {
    try {
        $value = ConvertFrom-FsutilFileIdOutput @($case.output)
        [ordered]@{ name = $case.name; succeeded = $true; value = $value }
    } catch {
        [ordered]@{ name = $case.name; succeeded = $false; value = $null }
    }
}
ConvertTo-Json -InputObject @($observed) -Compress
"""
        environment = os.environ | {
            "LPF_QUALIFICATION_SCRIPT": os.fspath(QUALIFY_REAL_HOST_SCRIPT),
            "LPF_FILE_ID_CASES": json.dumps(cases, ensure_ascii=False),
        }
        result = subprocess.run(
            [
                _powershell(),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                driver,
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, _output(result))
        observed = {entry["name"]: entry for entry in json.loads(_output(result))}
        for case in cases:
            with self.subTest(case=case["name"]):
                result_case = observed[case["name"]]
                self.assertEqual(case["expected"] is not None, result_case["succeeded"])
                self.assertEqual(case["expected"], result_case["value"])

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

    @unittest.skipUnless(
        os.name == "nt" and shutil.which("dotnet"),
        "Windows .NET SDK is required for NTFS identity compatibility",
    )
    def test_generated_ntfs_file_identity_fingerprint_matches_python_and_csharp(
        self,
    ) -> None:
        """One real NTFS file must publish identical frozen identity bytes."""

        build = _run("build", str(ADAPTER_TEST_PROJECT), "-c", "Release", "--nologo")
        self.assertEqual(0, build.returncode, _output(build))
        static_checks = _run(
            "run",
            "--project",
            str(ADAPTER_TEST_PROJECT),
            "-c",
            "Release",
            "--no-build",
        )
        self.assertEqual(0, static_checks.returncode, _output(static_checks))

        def python_binding(path: Path) -> OwnedPathBinding:
            opened = platform_backend(require_windows=True).open_existing_file(
                path,
                for_delete=False,
            )
            try:
                return opened.capture_binding()
            finally:
                opened.close()

        def csharp_identity(path: Path) -> str:
            result = _run(
                "run",
                "--project",
                str(ADAPTER_TEST_PROJECT),
                "-c",
                "Release",
                "--no-build",
                "--",
                "file-identity-fingerprint",
                os.fspath(path),
            )
            self.assertEqual(0, result.returncode, _output(result))
            return _output(result).strip()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "generated-source.dwg"
            source.write_bytes(b"AC1032-generated-identity-source")
            first = python_binding(source)
            self.assertIsNotNone(first.sha256)
            self.assertIsNotNone(first.byte_size)
            self.assertEqual(
                canonical_sha256(
                    {
                        "creation_time_100ns": first.identity.creation_time_100ns,
                        "first": first.identity.first,
                        "namespace": "windows-file-id",
                        "second": first.identity.second,
                    }
                ),
                first.file_identity_fingerprint,
            )
            self.assertEqual(
                first.file_identity_fingerprint,
                csharp_identity(source),
            )

            # A same-file write changes separate content evidence while
            # retaining the immutable NTFS identity projection.
            source.write_bytes(b"AC1032-generated-identity-source-with-growth")
            changed_stat = source.stat()
            os.utime(
                source,
                ns=(changed_stat.st_atime_ns, changed_stat.st_mtime_ns + 2_000_000_000),
            )
            changed = python_binding(source)
            self.assertEqual(
                first.file_identity_fingerprint,
                changed.file_identity_fingerprint,
            )
            self.assertEqual(
                changed.file_identity_fingerprint,
                csharp_identity(source),
            )
            self.assertNotEqual(first.byte_size, changed.byte_size)
            self.assertNotEqual(first.sha256, changed.sha256)
            self.assertFalse(first.same_identity_and_content(changed))

            # A distinct NTFS file gets a new real file index. The C# runner
            # additionally mutates synthetic creation/volume/index values in
            # its static checks above, because only the file index is safely
            # controllable for a generated file on the live volume.
            replacement = root / "generated-replacement.dwg"
            replacement.write_bytes(b"AC1032-generated-replacement")
            different = python_binding(replacement)
            self.assertNotEqual(first.identity.second, different.identity.second)
            self.assertNotEqual(
                first.file_identity_fingerprint,
                different.file_identity_fingerprint,
            )
            self.assertNotEqual(
                csharp_identity(source),
                csharp_identity(replacement),
            )

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
                '"protocol"',
                '"mode", "read_only"',
                '"adapter"',
                '"plugin"',
                '"host"',
                '"process"',
                '"capabilities"',
                '"bootstrap"',
                '"config_sha256"',
                '"issued_at"',
                '"expires_at"',
                "MaxBootstrapAdvertisementBytes",
                "FileMode.CreateNew",
            ):
                self.assertIn(field, advertisement)
            self.assertNotIn(".GetAwaiter().GetResult()", commands)
            self.assertNotIn(".Wait()", commands)
            self.assertNotIn(".GetAwaiter().GetResult()", bridge)
            self.assertNotIn(".Wait()", bridge)

    def test_generated_csharp_bootstrap_validates_and_prepares_python_session(self) -> None:
        """Use production C# serialization, then Python's schema/config gate."""

        nonce = "n" * 43
        issued_at = datetime.now(UTC).replace(microsecond=0)
        expires_at = issued_at + timedelta(minutes=2)
        config = synthetic_native_config()
        config["adapter"] = {
            "id": "liang-pingfa-autocad-adapter",
            "profile": "autocad2025",
            "version": "2.0.0",
        }
        configure_autocad_runtime_package(config, "autocad2025")
        plugin_fingerprint = config["runtime_package"]["fingerprint"]
        plugin = {
            "id": "liang-pingfa-autocad-plugin",
            "version": "2.0.0",
            "fingerprint": plugin_fingerprint,
        }
        config["plugins"]["write"].update(
            {
                "id": plugin["id"],
                "version": plugin["version"],
            }
        )
        config["plugins"]["readback"].update(
            {
                "id": plugin["id"],
                "version": plugin["version"],
            }
        )
        config["required_capabilities"] = list(AUTOCAD_ADAPTER_CAPABILITIES)
        config["host_compatibility"].update(
            {
                "host_family": "autocad",
                "host_product": "autocad",
                "host_release": "2025",
                "host_runtime": "net8",
            }
        )
        config["operation_profiles"].update(
            {
                "translate_dbtext/v1": True,
                "delete_auxiliary_overlay_text/v1": False,
                "create_review_marker/v1": False,
            }
        )
        config["bootstrap"] = {
            "nonce": nonce,
            "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        config_hash = canonical_sha256(config)
        build = _run(
            "build",
            str(ADAPTER_TEST_PROJECT),
            "-c",
            "Release",
            "--nologo",
            "-p:CadHostProfile=autocad2025",
        )
        self.assertEqual(0, build.returncode, _output(build))
        generated = _run(
            "run",
            "--project",
            str(ADAPTER_TEST_PROJECT),
            "-c",
            "Release",
            "--no-build",
            "-p:CadHostProfile=autocad2025",
            "--",
            "bootstrap-advertisement",
            nonce,
            config_hash,
            plugin_fingerprint,
            issued_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        self.assertEqual(0, generated.returncode, _output(generated))
        advertisement = json.loads(_output(generated))
        checked = require_active_native_contract("bootstrap", advertisement)
        self.assertNotIn("session_id", checked)
        self.assertEqual(AUTOCAD_ADAPTER_CAPABILITIES, checked["capabilities"])
        self.assertEqual("net8", checked["host"]["runtime"])

        bootstrap_path = Path("generated-private-bootstrap") / "bootstrap.json"

        class GeneratedBootstrapFile:
            def __init__(self, path: Path, payload: bytes) -> None:
                self.path = path
                self.payload = payload
                self.identity = FileIdentity("bootstrap", 1, 2, 3)
                self.renamed = False
                self.deleted = False
                self.closed = False

            def capture_binding(self) -> OwnedPathBinding:
                return OwnedPathBinding(
                    path=self.path,
                    identity=self.identity,
                    byte_size=len(self.payload),
                    sha256=sha256(self.payload).hexdigest(),
                    is_directory=False,
                )

            def final_path(self) -> Path:
                return self.path

            def rename_no_replace(self, destination: Path) -> None:
                self.path = destination
                self.renamed = True

            def read_chunks(self, _size: int = 1024 * 1024):
                yield self.payload

            def read_prefix(self, length: int) -> bytes:
                return self.payload[:length]

            def request_delete(self) -> None:
                self.deleted = True

            def close(self) -> None:
                self.closed = True

        class GeneratedBootstrapBackend:
            def __init__(self, opened: GeneratedBootstrapFile) -> None:
                self.opened = opened

            def open_existing_file(
                self,
                _path: Path,
                *,
                for_delete: bool,
            ) -> GeneratedBootstrapFile:
                self.for_delete = for_delete
                return self.opened

            def path_matches_binding(
                self,
                path: Path,
                binding: OwnedPathBinding,
            ) -> bool:
                return path == binding.path and not self.opened.deleted

            def path_exists(self, _path: Path) -> bool:
                return not self.opened.deleted

            def validate_private_artifact_ancestry(self, _path: Path) -> None:
                return

            def verify_private_staging_file(self, _path: Path) -> None:
                return

        class GeneratedDirectoryChain:
            def __init__(self, path: Path) -> None:
                self.path = path
                self.components = ()

            def require_binding(self) -> None:
                return

            def close(self) -> None:
                return

        def claim_patches(backend: GeneratedBootstrapBackend):
            return (
                mock.patch("liang_pingfa_review.native_bridge._require_windows"),
                mock.patch(
                    "liang_pingfa_review.native_bridge.lexical_absolute_path",
                    return_value=bootstrap_path,
                ),
                mock.patch(
                    "liang_pingfa_review.native_bridge.platform_backend",
                    return_value=backend,
                ),
                mock.patch(
                    "liang_pingfa_review.native_bridge.acquire_lexical_directory_chain",
                    return_value=GeneratedDirectoryChain(bootstrap_path.parent),
                ),
            )

        contexts = []

        class CapturingHandshake:
            def __init__(
                self,
                context,
                *,
                config,
                session_clock,
                component_leases,
            ) -> None:
                self.context = context
                self.config = config
                self.session_clock = session_clock
                contexts.append(context)

            def complete_session_descriptor(self) -> dict[str, str]:
                return {"generated": "client-owned-session"}

            def close(self) -> None:
                return

        opened = GeneratedBootstrapFile(
            bootstrap_path,
            canonical_json_bytes(checked),
        )
        backend = GeneratedBootstrapBackend(opened)
        with ExitStack() as stack:
            for patcher in claim_patches(backend):
                stack.enter_context(patcher)
            process_identity = stack.enter_context(
                mock.patch(
                    "liang_pingfa_review.native_bridge._require_bootstrap_process_identity"
                )
            )
            process = ProcessIdentity(
                pid=checked["pid"],
                windows_session_id=checked["process"]["windows_session_id"],
                creation_time_100ns=int(
                    checked["process"]["creation_time_100ns"]
                ),
                instance_fingerprint=checked["process"]["instance_fingerprint"],
                executable_fingerprint=checked["process"]["executable_fingerprint"],
            )
            stack.enter_context(
                mock.patch("liang_pingfa_review.native_bridge._require_windows")
            )
            stack.enter_context(
                mock.patch(
                    "liang_pingfa_review.native_bridge.utc_now",
                    return_value=issued_at,
                )
            )
            stack.enter_context(
                mock.patch(
                    "liang_pingfa_review.native_bridge.acquire_native_installation_leases",
                    return_value=mock.Mock(),
                )
            )
            stack.enter_context(
                mock.patch(
                    "liang_pingfa_review.native_bridge.inspect_process",
                    return_value=process,
                )
            )
            stack.enter_context(
                mock.patch(
                    "liang_pingfa_review.native_bridge.NativeBridgeHandshakeClient",
                    CapturingHandshake,
                )
            )
            observed = prepare_native_session_from_bootstrap(
                bootstrap_path=bootstrap_path,
                config=config,
                session_clock=lambda: NativeSessionClockReading(
                    clock="windows-gettickcount64-ms/v1",
                    boot_id="a" * 32,
                    uptime_milliseconds=1_000_000,
                ),
            )
        self.assertEqual(observed, {"generated": "client-owned-session"})
        self.assertTrue(opened.renamed)
        self.assertTrue(opened.deleted)
        self.assertTrue(opened.closed)
        process_identity.assert_called_once_with(checked)
        self.assertEqual(len(contexts), 1)
        context = contexts[0]
        self.assertEqual(context.prepared_process, process)
        self.assertEqual(context.pipe_name, checked["pipe"])
        self.assertEqual(context.created_at, issued_at)
        self.assertEqual(
            context.expires_at,
            expires_at,
        )
        self.assertEqual(context.monotonic_issued, "1000000")
        self.assertEqual(context.monotonic_expires, "1120000")

        wrong_config = json.loads(json.dumps(config))
        wrong_config["bootstrap"]["nonce"] = "x" * 43
        wrong_opened = GeneratedBootstrapFile(
            bootstrap_path,
            canonical_json_bytes(checked),
        )
        wrong_backend = GeneratedBootstrapBackend(wrong_opened)
        with ExitStack() as stack:
            for patcher in claim_patches(wrong_backend):
                stack.enter_context(patcher)
            pipe_prepare = stack.enter_context(
                mock.patch("liang_pingfa_review.native_bridge.prepare_native_session")
            )
            with self.assertRaises(PipelineError) as raised:
                prepare_native_session_from_bootstrap(
                    bootstrap_path=bootstrap_path,
                    config=wrong_config,
                )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)
        pipe_prepare.assert_not_called()
        self.assertTrue(wrong_opened.renamed)
        self.assertTrue(wrong_opened.deleted)

    def test_generated_csharp_bootstrap_preparation_honors_remaining_lifetime(self) -> None:
        """C# advertisements exercise Python's real bounded prepare calculation."""

        nonce = "n" * 43
        fingerprint_config = synthetic_native_config()
        configure_autocad_runtime_package(fingerprint_config, "autocad2025")
        plugin_fingerprint = fingerprint_config["runtime_package"]["fingerprint"]
        base = datetime(2030, 1, 1, tzinfo=UTC)
        build = _run(
            "build",
            str(ADAPTER_TEST_PROJECT),
            "-c",
            "Release",
            "--nologo",
            "-p:CadHostProfile=autocad2025",
        )
        self.assertEqual(0, build.returncode, _output(build))

        def configured(expires_at: datetime) -> dict:
            result = synthetic_native_config()
            result["adapter"] = {
                "id": "liang-pingfa-autocad-adapter",
                "profile": "autocad2025",
                "version": "2.0.0",
            }
            configure_autocad_runtime_package(result, "autocad2025")
            plugin = {
                "id": "liang-pingfa-autocad-plugin",
                "version": "2.0.0",
                "fingerprint": plugin_fingerprint,
            }
            for name in ("write", "readback"):
                result["plugins"][name].update(
                    {
                        "id": plugin["id"],
                        "version": plugin["version"],
                    }
                )
            result["required_capabilities"] = list(AUTOCAD_ADAPTER_CAPABILITIES)
            result["host_compatibility"].update(
                {
                    "host_family": "autocad",
                    "host_product": "autocad",
                    "host_release": "2025",
                    "host_runtime": "net8",
                }
            )
            result["operation_profiles"].update(
                {
                    "translate_dbtext/v1": True,
                    "delete_auxiliary_overlay_text/v1": False,
                    "create_review_marker/v1": False,
                }
            )
            result["bootstrap"] = {
                "nonce": nonce,
                "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            return result

        def generated(
            issued_at: datetime,
            expires_at: datetime,
            config: dict,
        ) -> dict:
            result = _run(
                "run",
                "--project",
                str(ADAPTER_TEST_PROJECT),
                "-c",
                "Release",
                "--no-build",
                "-p:CadHostProfile=autocad2025",
                "--",
                "bootstrap-advertisement",
                nonce,
                canonical_sha256(config),
                plugin_fingerprint,
                issued_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            self.assertEqual(0, result.returncode, _output(result))
            return require_active_native_contract(
                "bootstrap",
                json.loads(_output(result)),
            )

        contexts = []

        class CapturingHandshake:
            def __init__(
                self,
                context,
                *,
                config,
                session_clock,
                component_leases,
            ) -> None:
                self.context = context
                contexts.append(context)

            def complete_session_descriptor(self) -> dict[str, str]:
                return {"prepared": "generated-csharp-bootstrap"}

            def close(self) -> None:
                return

        def prepare(
            advertisement: dict,
            config: dict,
            *,
            now: datetime,
            uptime_milliseconds: int,
        ) -> dict[str, str]:
            process = ProcessIdentity(
                pid=advertisement["pid"],
                windows_session_id=advertisement["process"]["windows_session_id"],
                creation_time_100ns=int(
                    advertisement["process"]["creation_time_100ns"]
                ),
                instance_fingerprint=advertisement["process"]["instance_fingerprint"],
                executable_fingerprint=advertisement["process"]["executable_fingerprint"],
            )

            @contextmanager
            def advertised_bootstrap(_path: Path):
                yield advertisement

            with (
                mock.patch(
                    "liang_pingfa_review.native_bridge.consume_native_bridge_bootstrap",
                    advertised_bootstrap,
                ),
                mock.patch(
                    "liang_pingfa_review.native_bridge._require_bootstrap_process_identity"
                ),
                mock.patch("liang_pingfa_review.native_bridge._require_windows"),
                mock.patch(
                    "liang_pingfa_review.native_bridge.utc_now",
                    return_value=now,
                ),
                mock.patch(
                    "liang_pingfa_review.native_bridge.acquire_native_installation_leases",
                    return_value=mock.Mock(),
                ),
                mock.patch(
                    "liang_pingfa_review.native_bridge.inspect_process",
                    return_value=process,
                ),
                mock.patch(
                    "liang_pingfa_review.native_bridge.NativeBridgeHandshakeClient",
                    CapturingHandshake,
                ),
            ):
                return prepare_native_session_from_bootstrap(
                    bootstrap_path=Path("generated-bootstrap.json"),
                    config=config,
                    session_clock=lambda: NativeSessionClockReading(
                        clock="windows-gettickcount64-ms/v1",
                        boot_id="a" * 32,
                        uptime_milliseconds=uptime_milliseconds,
                    ),
                )

        cases = (
            ("two-minutes", base, base + timedelta(minutes=2), base, 120_000),
            (
                "one-millisecond",
                base,
                base + timedelta(minutes=2),
                base + timedelta(minutes=2, milliseconds=-1),
                1,
            ),
            ("five-minutes", base, base + timedelta(minutes=5), base, 300_000),
            # The C# serializer always limits its own issued-to-expiry span.
            # A clock before that issued time proves Python independently caps
            # advertised remaining wall time to its session maximum.
            (
                "advertised-over-cap",
                base + timedelta(minutes=5),
                base + timedelta(minutes=10),
                base,
                300_000,
            ),
        )
        for label, issued_at, expires_at, now, expected_lifetime in cases:
            with self.subTest(case=label):
                config = configured(expires_at)
                advertisement = generated(issued_at, expires_at, config)
                self.assertEqual(
                    prepare(
                        advertisement,
                        config,
                        now=now,
                        uptime_milliseconds=1_000_000,
                    ),
                    {"prepared": "generated-csharp-bootstrap"},
                )
                context = contexts.pop()
                self.assertEqual(
                    int(context.monotonic_expires)
                    - int(context.monotonic_issued),
                    expected_lifetime,
                )
                self.assertEqual(context.expires_at, min(
                    now + timedelta(minutes=5),
                    expires_at,
                ))

        expired_config = configured(base + timedelta(minutes=2))
        expired = generated(base, base + timedelta(minutes=2), expired_config)
        with self.assertRaises(PipelineError) as raised:
            prepare(
                expired,
                expired_config,
                now=base + timedelta(minutes=2),
                uptime_milliseconds=1_000_000,
            )
        self.assertEqual(raised.exception.code, ErrorCode.NATIVE_SESSION_INVALID)
        self.assertEqual(contexts, [])

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
