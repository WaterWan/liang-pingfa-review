[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("audit", "apply")]
    [string]$Phase,

    [Parameter(Mandatory = $true)]
    [ValidateSet(
        "autocad2024",
        "autocad2025",
        "autocad2026"
    )]
    [string]$Profile,

    [Parameter(Mandatory = $true)]
    [string]$PythonExecutable,

    [Parameter(Mandatory = $true)]
    [string]$HostExecutable,

    [Parameter(Mandatory = $true)]
    [string]$CoreConsoleExecutable,

    [Parameter(Mandatory = $true)]
    [string]$AdapterPackage,

    [Parameter(Mandatory = $true)]
    [string]$ReceiptPath,

    [Parameter(Mandatory = $true)]
    [string]$NativeConfig,

    [Parameter(Mandatory = $true)]
    [string]$Bootstrap,

    [Parameter(Mandatory = $true)]
    [string]$SessionPath,

    [Parameter(Mandatory = $true)]
    [string]$SourceDrawing,

    [Parameter(Mandatory = $true)]
    [string]$WorkRoot,

    [Parameter(Mandatory = $true)]
    [string]$EvidenceOutput,

    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "powershell-compatibility.ps1")

function Fail-Qualification([string]$Message) {
    throw "LiangPingfa real-host qualification refused: $Message"
}

# The parameter binder rejects this before the script body. Keep this
# defense-in-depth guard adjacent to initialization so a future parameter
# change cannot let an unimplemented TSSD label reach any child command or
# private-evidence path.
if ($Profile -like "tssd*") {
    Fail-Qualification "TSSD is not implemented or qualified by this AutoCAD adapter"
}

function Get-NormalLocalNtfsPath(
    [string]$Value,
    [bool]$MustExist,
    [bool]$RequireDirectory
) {
    if (
        [string]::IsNullOrWhiteSpace($Value) -or
        $Value.IndexOf([char]0) -ge 0 -or
        $Value -match '["'']' -or
        $Value -match '^(\\\\|\\\\\?\\|\\\\\.\\)' -or
        -not [IO.Path]::IsPathRooted($Value) -or
        $Value -match '(^|[\\/])\.\.?([\\/]|$)'
    ) {
        Fail-Qualification "a supplied path is not a normal absolute local path"
    }
    $full = [IO.Path]::GetFullPath($Value)
    if ($full -notmatch '^[A-Za-z]:\\') {
        Fail-Qualification "a supplied path is not on a local drive"
    }
    $drive = [IO.DriveInfo]::new($full.Substring(0, 3))
    if (
        $drive.DriveType -ne [IO.DriveType]::Fixed -or
        -not [string]::Equals($drive.DriveFormat, "NTFS", [StringComparison]::OrdinalIgnoreCase)
    ) {
        Fail-Qualification "a supplied path is not on a fixed local NTFS volume"
    }
    $parts = $full.Substring(3).Split(
        [char[]]@('\', '/'),
        [StringSplitOptions]::RemoveEmptyEntries
    )
    $current = $full.Substring(0, 3)
    foreach ($part in $parts) {
        $current = Join-Path $current $part
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                Fail-Qualification "a supplied path contains a reparse point"
            }
        }
    }
    if ($MustExist -and -not (Test-Path -LiteralPath $full)) {
        Fail-Qualification "a required path is unavailable"
    }
    if ($MustExist -and $RequireDirectory -and -not (Test-Path -LiteralPath $full -PathType Container)) {
        Fail-Qualification "a required directory is unavailable"
    }
    return $full
}

function Assert-PrivateDirectory([string]$Value) {
    $path = Get-NormalLocalNtfsPath $Value $true $true
    $acl = Get-Acl -LiteralPath $path
    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $trusted = @($currentSid, "S-1-5-18")
    try {
        $ownerSid = (
            [Security.Principal.NTAccount]$acl.Owner
        ).Translate([Security.Principal.SecurityIdentifier]).Value
    } catch {
        Fail-Qualification "a private directory owner cannot be verified"
    }
    if (@($currentSid, "S-1-5-18", "S-1-5-32-544") -notcontains $ownerSid) {
        Fail-Qualification "a private directory owner is not trusted"
    }
    $seen = @{}
    foreach ($rule in $acl.Access) {
        if ($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow) {
            Fail-Qualification "a private directory has an unsupported DACL rule"
        }
        try {
            $sid = $rule.IdentityReference.Translate(
                [Security.Principal.SecurityIdentifier]
            ).Value
        } catch {
            Fail-Qualification "a private directory DACL cannot be verified"
        }
        if ($trusted -notcontains $sid) {
            Fail-Qualification "a private directory grants a non-private SID"
        }
        $seen[$sid] = $true
    }
    foreach ($sid in $trusted) {
        if (-not $seen.ContainsKey($sid)) {
            Fail-Qualification "a private directory is missing a required DACL principal"
        }
    }
    return $path
}

function Assert-PrivateFile([string]$Value) {
    $path = Get-NormalLocalNtfsPath $Value $true $false
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Fail-Qualification "a private receipt is not a regular file"
    }
    $item = Get-Item -LiteralPath $path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Fail-Qualification "a private receipt is a reparse point"
    }
    $acl = Get-Acl -LiteralPath $path
    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $trusted = @($currentSid, "S-1-5-18")
    try {
        $ownerSid = (
            [Security.Principal.NTAccount]$acl.Owner
        ).Translate([Security.Principal.SecurityIdentifier]).Value
    } catch {
        Fail-Qualification "a private receipt owner cannot be verified"
    }
    if (@($currentSid, "S-1-5-18", "S-1-5-32-544") -notcontains $ownerSid) {
        Fail-Qualification "a private receipt owner is not trusted"
    }
    $seen = @{}
    foreach ($rule in $acl.Access) {
        if ($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow) {
            Fail-Qualification "a private receipt has an unsupported DACL rule"
        }
        try {
            $sid = $rule.IdentityReference.Translate(
                [Security.Principal.SecurityIdentifier]
            ).Value
        } catch {
            Fail-Qualification "a private receipt DACL cannot be verified"
        }
        if ($trusted -notcontains $sid) {
            Fail-Qualification "a private receipt grants a non-private SID"
        }
        $seen[$sid] = $true
    }
    foreach ($sid in $trusted) {
        if (-not $seen.ContainsKey($sid)) {
            Fail-Qualification "a private receipt is missing a required DACL principal"
        }
    }
    return $path
}

function Assert-ChildOf([string]$Path, [string]$Root, [string]$Label) {
    $full = Get-NormalLocalNtfsPath $Path $false $false
    $trimmedRoot = $Root.TrimEnd('\', '/')
    if (
        -not $full.StartsWith(
            $trimmedRoot + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        Fail-Qualification "$Label escaped its explicitly supplied private root"
    }
    return $full
}

function ConvertFrom-FsutilFileIdOutput([string[]]$Output) {
    # fsutil localizes the prose around the file ID.  Only its explicitly
    # prefixed hexadecimal token is stable across those locales.  Do not use
    # a word boundary before the digits: the `x` in `0x` is a word character.
    $text = [string]::Join([Environment]::NewLine, @($Output))
    $matches = [regex]::Matches(
        $text,
        '(?i)0x([0-9A-Fa-f]{16,64})(?![0-9A-Za-z])'
    )
    if ($matches.Count -ne 1) {
        Fail-Qualification "the source file identity is malformed"
    }
    return ("0x" + $matches[0].Groups[1].Value.ToLowerInvariant())
}

function Get-FileIdentity([string]$Path) {
    # fsutil is a documented Windows command-line file-ID query. It receives
    # the already validated path as one argument; no shell interpolation or
    # command string is constructed from operator input.
    $raw = & fsutil file queryfileid $Path 2>$null
    if ($LASTEXITCODE -ne 0) {
        Fail-Qualification "the source file identity is unavailable"
    }
    return ConvertFrom-FsutilFileIdOutput @($raw)
}

function Get-BoundFileState([string]$Path) {
    $file = Get-NormalLocalNtfsPath $Path $true $false
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
        Fail-Qualification "a required file is not a regular file"
    }
    $item = Get-Item -LiteralPath $file -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Fail-Qualification "a required file is a reparse point"
    }
    $stream = $null
    $algorithm = $null
    try {
        $stream = [IO.File]::Open(
            $file,
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::Read
        )
        $algorithm = [Security.Cryptography.SHA256]::Create()
        $hash = ConvertTo-LowercaseHex $algorithm.ComputeHash($stream)
        return [ordered]@{
            file_identity = Get-FileIdentity $file
            byte_size = $item.Length
            creation_time_utc = $item.CreationTimeUtc.ToString("O")
            last_write_time_utc = $item.LastWriteTimeUtc.ToString("O")
            sha256 = $hash
        }
    } finally {
        if ($null -ne $algorithm) {
            $algorithm.Dispose()
        }
        if ($null -ne $stream) {
            $stream.Dispose()
        }
    }
}

function Assert-SameFileState(
    [System.Collections.IDictionary]$Before,
    [System.Collections.IDictionary]$After,
    [string]$Label
) {
    foreach ($field in @(
        "file_identity",
        "byte_size",
        "creation_time_utc",
        "last_write_time_utc",
        "sha256"
    )) {
        if ($Before[$field] -ne $After[$field]) {
            Fail-Qualification "$Label changed during qualification"
        }
    }
}

function Write-PrivateJson(
    [string]$Path,
    [System.Collections.IDictionary]$Value
) {
    if (Test-Path -LiteralPath $Path) {
        Fail-Qualification "a private qualification artifact already exists"
    }
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(
        ($Value | ConvertTo-Json -Depth 8 -Compress)
    )
    $stream = $null
    $created = $false
    try {
        $stream = [IO.File]::Open(
            $Path,
            [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write,
            [IO.FileShare]::None
        )
        $created = $true
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    } catch {
        if ($null -ne $stream) {
            $stream.Dispose()
            $stream = $null
        }
        if ($created) {
            Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
        }
        throw
    } finally {
        if ($null -ne $stream) {
            $stream.Dispose()
        }
    }
}

function Invoke-NativePython([string[]]$Arguments) {
    # Child commands may complete their own private artifact phase before a
    # later binding check fails.  Keep their progress output private so this
    # launcher emits a success-shaped record only after its final gate.
    $null = & $python @Arguments
    if ($LASTEXITCODE -ne 0) {
        Fail-Qualification "an existing Python native orchestration command failed"
    }
}

function Get-PlanId([string]$Plan) {
    $previous = $env:LIANG_PINGFA_QUALIFICATION_PLAN
    try {
        $env:LIANG_PINGFA_QUALIFICATION_PLAN = $Plan
        $id = & $python -c @'
import os
from pathlib import Path
from liang_pingfa_review.native_contracts import load_native_artifact
print(load_native_artifact('plan', Path(os.environ['LIANG_PINGFA_QUALIFICATION_PLAN']))['plan_id'])
'@ 2>$null
        if (
            $LASTEXITCODE -ne 0 -or
            $id -notmatch '^native-plan-[a-f0-9]{32}$'
        ) {
            Fail-Qualification "the private deterministic plan cannot be confirmed"
        }
        return $id
    } finally {
        if ($null -eq $previous) {
            Remove-Item Env:LIANG_PINGFA_QUALIFICATION_PLAN -ErrorAction SilentlyContinue
        } else {
            $env:LIANG_PINGFA_QUALIFICATION_PLAN = $previous
        }
    }
}

function Assert-ConfigMatchesExplicitHostPackage(
    [string]$ConfigPath,
    [string]$PackagePath,
    [string]$ReceiptFile,
    [string]$HostPath,
    [string]$CoreConsolePath,
    [string]$SelectedProfile
) {
    $savedConfig = $env:LIANG_PINGFA_QUALIFICATION_CONFIG
    $savedPackage = $env:LIANG_PINGFA_QUALIFICATION_PACKAGE
    $savedReceipt = $env:LIANG_PINGFA_QUALIFICATION_RECEIPT
    $savedHost = $env:LIANG_PINGFA_QUALIFICATION_HOST
    $savedCore = $env:LIANG_PINGFA_QUALIFICATION_CORE
    $savedProfile = $env:LIANG_PINGFA_QUALIFICATION_PROFILE
    try {
        $env:LIANG_PINGFA_QUALIFICATION_CONFIG = $ConfigPath
        $env:LIANG_PINGFA_QUALIFICATION_PACKAGE = $PackagePath
        $env:LIANG_PINGFA_QUALIFICATION_RECEIPT = $ReceiptFile
        $env:LIANG_PINGFA_QUALIFICATION_HOST = $HostPath
        $env:LIANG_PINGFA_QUALIFICATION_CORE = $CoreConsolePath
        $env:LIANG_PINGFA_QUALIFICATION_PROFILE = $SelectedProfile
        $result = & $python -c @'
import json
import os
from pathlib import Path
from liang_pingfa_review.canonical import strict_json_loads
from liang_pingfa_review.native_contracts import load_native_config
from liang_pingfa_review.runtime_package import (
    ADAPTER_ASSEMBLY,
    normalize_runtime_package_descriptor,
    validate_adapter_receipt,
    verify_adapter_package_against_receipt,
)

def digest(path: Path) -> str:
    import hashlib
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()

def same_path(left: Path, right: Path) -> bool:
    if os.path.normcase(os.path.normpath(str(left))) == os.path.normcase(
        os.path.normpath(str(right))
    ):
        return True
    try:
        # PowerShell has already rejected reparse points. Treat a normal NTFS
        # 8.3/long-name alias as the same explicit package object, not as a
        # directory switch, while retaining the lexical fast path above.
        return os.path.samefile(left, right)
    except OSError:
        return False

config_path = Path(os.environ['LIANG_PINGFA_QUALIFICATION_CONFIG'])
receipt_path = Path(os.environ['LIANG_PINGFA_QUALIFICATION_RECEIPT'])
if receipt_path.stat().st_size > 65536:
    raise SystemExit(1)
receipt = validate_adapter_receipt(
    strict_json_loads(receipt_path.read_text(encoding='utf-8'))
)
config = load_native_config(config_path)
package = Path(os.environ['LIANG_PINGFA_QUALIFICATION_PACKAGE'])
host = Path(os.environ['LIANG_PINGFA_QUALIFICATION_HOST'])
core = Path(os.environ['LIANG_PINGFA_QUALIFICATION_CORE'])
verified_receipt = verify_adapter_package_against_receipt(package, receipt)
runtime = normalize_runtime_package_descriptor(
    config['runtime_package'], require_directory=True
)
receipt_runtime = verified_receipt['runtime_package']
adapter = next(
    item for item in runtime['components'] if item['name'] == ADAPTER_ASSEMBLY
)
checks = {
    'profile': config['adapter']['profile']
    != os.environ['LIANG_PINGFA_QUALIFICATION_PROFILE'],
    'host': digest(host) != config['full_host']['sha256'],
    'core': digest(core) != config['core_console']['sha256'],
    'runtime-profile': runtime['profile'] != config['adapter']['profile'],
    'receipt-runtime': (
        {key: value for key, value in runtime.items() if key != 'directory'}
        != receipt_runtime
    ),
    'directory': not same_path(Path(runtime['directory']), package),
    'plugins': any(
        plugin['sha256'] != adapter['sha256']
        or plugin['runtime_package_fingerprint'] != runtime['fingerprint']
        or not same_path(Path(plugin['path']), package / ADAPTER_ASSEMBLY)
        for plugin in config['plugins'].values()
    ),
}
if any(checks.values()):
    raise SystemExit(','.join(name for name, failed in checks.items() if failed))
print(json.dumps({
    'fingerprint': runtime['fingerprint'],
    'components': [item['name'] for item in runtime['components']],
}, separators=(',', ':'), sort_keys=True))
'@ 2>$null
        if ($LASTEXITCODE -ne 0) {
            Fail-Qualification "the private config does not match the explicit host package"
        }
        try {
            # Python strictly parses the receipt with duplicate-key rejection,
            # schema validation, canonical receipt validation, and package
            # verification before producing this small binding. PowerShell's
            # JSON parser is therefore never trusted to detect duplicates.
            $binding = @(
                ConvertFrom-JsonToDeterministicHashtable (
                    [string]::Join([Environment]::NewLine, @($result))
                )
            )
            if (
                $binding.Count -ne 1 -or
                $binding[0]["fingerprint"] -notmatch '^[a-f0-9]{64}$' -or
                $binding[0]["components"].Count -lt 3
            ) {
                throw "invalid runtime package binding"
            }
        } catch {
            Fail-Qualification "the private config runtime package binding is invalid"
        }
        return $binding[0]
    } finally {
        foreach ($entry in @(
            @{ Name = "LIANG_PINGFA_QUALIFICATION_CONFIG"; Value = $savedConfig },
            @{ Name = "LIANG_PINGFA_QUALIFICATION_PACKAGE"; Value = $savedPackage },
            @{ Name = "LIANG_PINGFA_QUALIFICATION_RECEIPT"; Value = $savedReceipt },
            @{ Name = "LIANG_PINGFA_QUALIFICATION_HOST"; Value = $savedHost },
            @{ Name = "LIANG_PINGFA_QUALIFICATION_CORE"; Value = $savedCore },
            @{ Name = "LIANG_PINGFA_QUALIFICATION_PROFILE"; Value = $savedProfile }
        )) {
            if ($null -eq $entry.Value) {
                Remove-Item ("Env:" + $entry.Name) -ErrorAction SilentlyContinue
            } else {
                Set-Item ("Env:" + $entry.Name) $entry.Value
            }
        }
    }
}

function Get-RuntimePackageState(
    [string]$ConfigPath,
    [string]$PackagePath,
    [string]$ReceiptFile,
    [string]$HostPath,
    [string]$CoreConsolePath,
    [string]$SelectedProfile
) {
    $binding = Assert-ConfigMatchesExplicitHostPackage `
        $ConfigPath $PackagePath $ReceiptFile $HostPath $CoreConsolePath $SelectedProfile
    $components = [ordered]@{}
    foreach ($name in @($binding["components"] | Sort-Object)) {
        if (
            $name -notmatch '^LiangPingfa\.NativeCad\.(AutoCAD\.Adapter|Core|Protocol)\.dll$' -and
            $name -ne "LiangPingfa.NativeCad.AutoCAD.Adapter.deps.json"
        ) {
            Fail-Qualification "the receipt advertises an unlisted runtime component"
        }
        $components[$name] = Get-BoundFileState (Join-Path $PackagePath $name)
    }
    return [ordered]@{
        runtime_package_fingerprint = $binding["fingerprint"]
        receipt_state = Get-BoundFileState $ReceiptFile
        components = $components
    }
}

function Assert-SameRuntimePackageState(
    [System.Collections.IDictionary]$Before,
    [System.Collections.IDictionary]$After
) {
    if (
        $Before["runtime_package_fingerprint"] -ne $After["runtime_package_fingerprint"]
    ) {
        Fail-Qualification "the runtime package fingerprint changed during qualification"
    }
    Assert-SameFileState $Before["receipt_state"] $After["receipt_state"] "the private build receipt"
    $beforeComponents = $Before["components"]
    $afterComponents = $After["components"]
    if ($beforeComponents.Count -ne $afterComponents.Count) {
        Fail-Qualification "the runtime package component set changed during qualification"
    }
    foreach ($name in $beforeComponents.Keys) {
        if (-not (Test-MapContainsKey $afterComponents $name)) {
            Fail-Qualification "the runtime package component set changed during qualification"
        }
        Assert-SameFileState `
            $beforeComponents[$name] $afterComponents[$name] `
            "the runtime package component"
    }
}

function Assert-AuditedHostBinding(
    [string]$AuditPath,
    [string]$ConfigPath,
    [System.Collections.IDictionary]$HostState,
    [string]$SelectedProfile
) {
    # load_native_artifact/load_native_config use the bounded private-artifact
    # reader, duplicate-key rejection, schema validation, and integrity check.
    # Do not parse this private audit with ConvertFrom-Json.
    $savedAudit = $env:LIANG_PINGFA_QUALIFICATION_AUDIT
    $savedConfig = $env:LIANG_PINGFA_QUALIFICATION_CONFIG
    $savedHostHash = $env:LIANG_PINGFA_QUALIFICATION_HOST_SHA256
    $savedProfile = $env:LIANG_PINGFA_QUALIFICATION_PROFILE
    try {
        $env:LIANG_PINGFA_QUALIFICATION_AUDIT = $AuditPath
        $env:LIANG_PINGFA_QUALIFICATION_CONFIG = $ConfigPath
        $env:LIANG_PINGFA_QUALIFICATION_HOST_SHA256 = $HostState["sha256"]
        $env:LIANG_PINGFA_QUALIFICATION_PROFILE = $SelectedProfile
        $result = & $python -c @'
import os
from pathlib import Path
from liang_pingfa_review.native_contracts import (
    load_native_artifact,
    load_native_config,
    require_qualification_host_binding,
)

audit = load_native_artifact(
    'audit', Path(os.environ['LIANG_PINGFA_QUALIFICATION_AUDIT'])
)
config = load_native_config(Path(os.environ['LIANG_PINGFA_QUALIFICATION_CONFIG']))
require_qualification_host_binding(
    audit,
    config,
    host_executable_sha256=os.environ['LIANG_PINGFA_QUALIFICATION_HOST_SHA256'],
    profile=os.environ['LIANG_PINGFA_QUALIFICATION_PROFILE'],
)
print('ok')
'@ 2>$null
        if ($LASTEXITCODE -ne 0 -or $result -ne "ok") {
            Fail-Qualification "the audited full-host binding does not match the retained host"
        }
    } finally {
        foreach ($entry in @(
            @{ Name = "LIANG_PINGFA_QUALIFICATION_AUDIT"; Value = $savedAudit },
            @{ Name = "LIANG_PINGFA_QUALIFICATION_CONFIG"; Value = $savedConfig },
            @{ Name = "LIANG_PINGFA_QUALIFICATION_HOST_SHA256"; Value = $savedHostHash },
            @{ Name = "LIANG_PINGFA_QUALIFICATION_PROFILE"; Value = $savedProfile }
        )) {
            if ($null -eq $entry.Value) {
                Remove-Item ("Env:" + $entry.Name) -ErrorAction SilentlyContinue
            } else {
                Set-Item ("Env:" + $entry.Name) $entry.Value
            }
        }
    }
}

function Assert-AdapterPackage([string]$Path) {
    $package = Get-NormalLocalNtfsPath $Path $true $true
    foreach ($required in @(
        "LiangPingfa.NativeCad.AutoCAD.Adapter.dll",
        "LiangPingfa.NativeCad.Core.dll",
        "LiangPingfa.NativeCad.Protocol.dll"
    )) {
        if (-not (Test-Path -LiteralPath (Join-Path $package $required) -PathType Leaf)) {
            Fail-Qualification "the adapter package is incomplete"
        }
    }
    foreach ($forbidden in @(
        "AcMgd.dll",
        "AcDbMgd.dll",
        "AcCoreMgd.dll",
        "LiangPingfa.NativeCad.AutoCAD.ApiStubs.dll"
    )) {
        if (Test-Path -LiteralPath (Join-Path $package $forbidden)) {
            Fail-Qualification "the adapter package contains a vendor or syntax-stub binary"
        }
    }
    return $package
}

if (-not $DryRun -and $env:LIANG_PINGFA_RUN_REAL_HOST -ne "1") {
    Fail-Qualification "real-host execution requires LIANG_PINGFA_RUN_REAL_HOST=1"
}

$python = Get-NormalLocalNtfsPath $PythonExecutable $true $false
$hostExecutable = Get-NormalLocalNtfsPath $HostExecutable $true $false
$coreConsole = Get-NormalLocalNtfsPath $CoreConsoleExecutable $true $false
$package = Assert-AdapterPackage $AdapterPackage
$receipt = Assert-PrivateFile $ReceiptPath
$work = Assert-PrivateDirectory $WorkRoot
$evidence = Assert-PrivateDirectory $EvidenceOutput
$source = Get-NormalLocalNtfsPath $SourceDrawing $true $false
$bootstrap = Get-NormalLocalNtfsPath $Bootstrap $true $false
$session = Assert-ChildOf $SessionPath $work "the session descriptor"
$config = Get-NormalLocalNtfsPath $NativeConfig $true $false

if (
    -not $source.StartsWith(
        $work.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
    ) -or
    (Split-Path -Path $source -Leaf) -notmatch '^liang-pingfa-qualification-[A-Za-z0-9_-]+\.dwg$'
) {
    Fail-Qualification "the source is not an authorized generated private fixture"
}
if (
    -not $hostExecutable.EndsWith(".exe", [StringComparison]::OrdinalIgnoreCase) -or
    -not $coreConsole.EndsWith(".exe", [StringComparison]::OrdinalIgnoreCase)
) {
    Fail-Qualification "the licensed host and Core Console inputs must be explicit executables"
}

$stateRoot = Join-Path $work "liang-pingfa-realhost-qualification"
$statePath = Join-Path $stateRoot "qualification-state.json"
$auditPath = Join-Path $stateRoot "audit.json"
$auditReport = Join-Path $stateRoot "audit.md"
$intentPath = Join-Path $stateRoot "qualification-intent.json"
$planPath = Join-Path $stateRoot "plan.json"
$planReport = Join-Path $stateRoot "plan-review.md"
$outputPath = Join-Path $stateRoot "qualified-output.dwg"
$verificationPath = Join-Path $stateRoot "verification.json"
$summaryPath = Join-Path $evidence ("qualification-" + $Phase + "-summary.json")

# This mandatory gate hashes every receipt-listed package file, verifies the
# receipt's canonical integrity, and binds the config directory/components
# before even a dry-run can emit a success-shaped record.
$runtimeBeforePhase = Get-RuntimePackageState `
    $config $package $receipt $hostExecutable $coreConsole $Profile

if ($DryRun) {
    Write-Output (
        '{"status":"dry-run","phase":"' + $Phase +
        '","profile":"' + $Profile +
        '","host":"not-launched","qualification":"not-claimed"}'
    )
    exit 0
}

Assert-SameRuntimePackageState $runtimeBeforePhase (
    Get-RuntimePackageState $config $package $receipt $hostExecutable $coreConsole $Profile
)

if ($Phase -eq "audit") {
    if (Test-Path -LiteralPath $stateRoot) {
        Fail-Qualification "the qualification work root already exists"
    }
    New-Item -ItemType Directory -Path $stateRoot -ErrorAction Stop | Out-Null
    $sourceBefore = Get-BoundFileState $source
    $hostBefore = Get-BoundFileState $hostExecutable
    $coreBefore = Get-BoundFileState $coreConsole
    try {
        $runtimeBeforeBootstrap = Get-RuntimePackageState `
            $config $package $receipt $hostExecutable $coreConsole $Profile
        Assert-SameRuntimePackageState $runtimeBeforePhase $runtimeBeforeBootstrap
        Invoke-NativePython @(
            "-m", "liang_pingfa_review", "native-session", "prepare",
            "--bootstrap", $bootstrap,
            "--session-out", $session,
            "--native-config", $config
        )
        $doctor = & $python -m liang_pingfa_review native-doctor --native-config $config
        $doctorValue = ConvertFrom-JsonToDeterministicHashtable (
            [string]::Join([Environment]::NewLine, @($doctor))
        )
        if ($LASTEXITCODE -ne 0 -or $doctorValue["status"] -ne "ok") {
            Fail-Qualification "the configured native doctor is not ready"
        }
        Invoke-NativePython @(
            "-m", "liang_pingfa_review", "native-audit",
            "--input", $source,
            "--session", $session,
            "--audit-out", $auditPath,
            "--report-out", $auditReport,
            "--native-config", $config
        )
        $hostAfterAudit = Get-BoundFileState $hostExecutable
        $coreAfterAudit = Get-BoundFileState $coreConsole
        $runtimeAfterAudit = Get-RuntimePackageState `
            $config $package $receipt $hostExecutable $coreConsole $Profile
        Assert-SameFileState $hostBefore $hostAfterAudit "the licensed host"
        Assert-SameFileState $coreBefore $coreAfterAudit "the Core Console"
        Assert-SameRuntimePackageState $runtimeBeforeBootstrap $runtimeAfterAudit
        Assert-AuditedHostBinding $auditPath $config $hostAfterAudit $Profile
        Invoke-NativePython @(
            "-m", "liang_pingfa_review", "native-qualification-intent",
            "--audit", $auditPath,
            "--intent-out", $intentPath
        )
        Invoke-NativePython @(
            "-m", "liang_pingfa_review", "native-plan",
            "--audit", $auditPath,
            "--intent", $intentPath,
            "--plan-out", $planPath,
            "--review-out", $planReport,
            "--native-config", $config
        )
        $sourceAfter = Get-BoundFileState $source
        Assert-SameFileState $sourceBefore $sourceAfter "the qualification source"
        Write-PrivateJson $statePath ([ordered]@{
            schema_version = "liang-pingfa/real-host-qualification-state/v1"
            phase = "audit-complete"
            profile = $Profile
            source_before = $sourceBefore
            host_executable_state = $hostAfterAudit
            core_console_state = $coreAfterAudit
            runtime_package_state = $runtimeAfterAudit
        })
        Write-PrivateJson $summaryPath ([ordered]@{
            schema_version = "liang-pingfa/real-host-qualification-summary/v1"
            phase = "audit"
            profile = $Profile
            result = "private-evidence-created"
            runtime_qualification = "not-claimed-until-apply-phase"
        })
    } catch {
        if (Test-Path -LiteralPath $stateRoot) {
            Remove-Item -LiteralPath $stateRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
        throw
    }
    Write-Output (
        '{"status":"ok","phase":"audit","profile":"' + $Profile +
        '","next":"operator-must-create-a-fresh-bootstrap-for-apply","qualification":"not-claimed"}'
    )
    exit 0
}

if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
    Fail-Qualification "apply requires an earlier private audit phase"
}
$state = ConvertFrom-JsonToDeterministicHashtable (
    Get-Content -LiteralPath $statePath -Raw
)
if ($state["phase"] -ne "audit-complete" -or $state["profile"] -ne $Profile) {
    Fail-Qualification "the private audit state does not bind this profile"
}
$sourceBeforeApply = Get-BoundFileState $source
Assert-SameFileState $state["source_before"] $sourceBeforeApply "the qualification source"
$hostBeforeApply = Get-BoundFileState $hostExecutable
$coreBeforeApply = Get-BoundFileState $coreConsole
Assert-SameFileState $state["host_executable_state"] $hostBeforeApply "the licensed host"
Assert-SameFileState $state["core_console_state"] $coreBeforeApply "the Core Console"
Assert-AuditedHostBinding $auditPath $config $hostBeforeApply $Profile
$runtimeBeforeApplyBootstrap = Get-RuntimePackageState `
    $config $package $receipt $hostExecutable $coreConsole $Profile
Assert-SameRuntimePackageState $state["runtime_package_state"] $runtimeBeforeApplyBootstrap
foreach ($required in @($auditPath, $intentPath, $planPath)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        Fail-Qualification "the private audit/plan handoff is incomplete"
    }
}
if (Test-Path -LiteralPath $outputPath -or Test-Path -LiteralPath $verificationPath) {
    Fail-Qualification "the qualification output paths must be new"
}

Invoke-NativePython @(
    "-m", "liang_pingfa_review", "native-session", "prepare",
    "--bootstrap", $bootstrap,
    "--session-out", $session,
    "--native-config", $config
)
$planId = Get-PlanId $planPath
# The full host and Core Console are rehashed after preparing the fresh
# session and immediately before native-apply.  A path substitution between
# audit and apply cannot gain a success record.
$hostImmediatelyBeforeApply = Get-BoundFileState $hostExecutable
$coreImmediatelyBeforeApply = Get-BoundFileState $coreConsole
Assert-SameFileState $state["host_executable_state"] $hostImmediatelyBeforeApply "the licensed host"
Assert-SameFileState $state["core_console_state"] $coreImmediatelyBeforeApply "the Core Console"
$runtimeImmediatelyBeforeApply = Get-RuntimePackageState `
    $config $package $receipt $hostExecutable $coreConsole $Profile
Assert-SameRuntimePackageState $runtimeBeforeApplyBootstrap $runtimeImmediatelyBeforeApply
Assert-AuditedHostBinding $auditPath $config $hostImmediatelyBeforeApply $Profile
Invoke-NativePython @(
    "-m", "liang_pingfa_review", "native-apply",
    "--input", $source,
    "--session", $session,
    "--audit", $auditPath,
    "--intent", $intentPath,
    "--plan", $planPath,
    "--confirm-plan", $planId,
    "--output", $outputPath,
    "--verification-out", $verificationPath,
    "--native-config", $config
)
Invoke-NativePython @(
    "-m", "liang_pingfa_review", "native-verify",
    "--input", $outputPath,
    "--verification", $verificationPath
)
$sourceAfterApply = Get-BoundFileState $source
Assert-SameFileState $sourceBeforeApply $sourceAfterApply "the qualification source"
$hostAfterVerification = Get-BoundFileState $hostExecutable
$coreAfterVerification = Get-BoundFileState $coreConsole
Assert-SameFileState $state["host_executable_state"] $hostAfterVerification "the licensed host"
Assert-SameFileState $state["core_console_state"] $coreAfterVerification "the Core Console"
$runtimeAfterVerification = Get-RuntimePackageState `
    $config $package $receipt $hostExecutable $coreConsole $Profile
Assert-SameRuntimePackageState $runtimeImmediatelyBeforeApply $runtimeAfterVerification
Assert-AuditedHostBinding $auditPath $config $hostAfterVerification $Profile
$outputState = Get-BoundFileState $outputPath
if ($outputState["sha256"] -eq $sourceBeforeApply["sha256"]) {
    Fail-Qualification "the authorized translation did not change the output bytes"
}
Write-PrivateJson $summaryPath ([ordered]@{
    schema_version = "liang-pingfa/real-host-qualification-summary/v1"
    phase = "apply"
    profile = $Profile
    source_unchanged = $true
    output_changed = $true
    native_readback = "passed"
    runtime_qualification = "private-evidence-only"
})
Write-Output (
    '{"status":"ok","phase":"apply","profile":"' + $Profile +
    '","source":"unchanged","output":"changed","readback":"passed","qualification":"private-evidence-only"}'
)
