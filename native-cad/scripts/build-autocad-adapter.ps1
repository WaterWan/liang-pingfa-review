[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet(
        "autocad2024",
        "autocad2025",
        "autocad2026"
    )]
    [string]$Profile,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$CadSdkDir,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$PackageDirectory,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$PrivateRoot,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ReceiptPath,

    [ValidateSet("Release")]
    [string]$Configuration = "Release",

    [switch]$DryRun,

    # This switch exists solely to exercise package finalization on generated
    # SDK-shaped fixtures.  Its receipt is deliberately outside the real
    # receipt contract and qualification rejects it.
    [switch]$TestOnlyFakeSdk
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "powershell-compatibility.ps1")

$script:VendorAssemblies = @("AcMgd.dll", "AcDbMgd.dll", "AcCoreMgd.dll")
$script:ForbiddenPayloadNames = @(
    "AcMgd.dll",
    "AcDbMgd.dll",
    "AcCoreMgd.dll",
    "LiangPingfa.NativeCad.AutoCAD.ApiStubs.dll"
)
$script:RuntimePackageFormat = "liang-pingfa/autocad-runtime-package/v1"
$script:ReceiptSchemaVersion = "liang-pingfa/autocad-adapter-build-receipt/v2"
$script:ReceiptFormat = "liang-pingfa/autocad-adapter-build-receipt-format/v1"
$script:AdapterAssemblyName = "LiangPingfa.NativeCad.AutoCAD.Adapter.dll"
$script:CoreAssemblyName = "LiangPingfa.NativeCad.Core.dll"
$script:ProtocolAssemblyName = "LiangPingfa.NativeCad.Protocol.dll"
$script:AdapterDepsName = "LiangPingfa.NativeCad.AutoCAD.Adapter.deps.json"
$script:AuxiliaryPackageNames = @(
    "LiangPingfa.NativeCad.AutoCAD.Adapter.pdb",
    "LiangPingfa.NativeCad.Core.pdb",
    "LiangPingfa.NativeCad.Protocol.pdb",
    "README.md",
    "native-bootstrap-context.template.json"
)

function Fail-Closed([string]$Message) {
    throw "LiangPingfa adapter build refused: $Message"
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
        Fail-Closed "a required path is not a normal absolute local path"
    }

    $full = [IO.Path]::GetFullPath($Value)
    if ($full -notmatch '^[A-Za-z]:\\') {
        Fail-Closed "a required path is not on a local drive"
    }
    $drive = [IO.DriveInfo]::new($full.Substring(0, 3))
    if (
        $drive.DriveType -ne [IO.DriveType]::Fixed -or
        -not [string]::Equals($drive.DriveFormat, "NTFS", [StringComparison]::OrdinalIgnoreCase)
    ) {
        Fail-Closed "a required path is not on a fixed local NTFS volume"
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
                Fail-Closed "a required path contains a reparse point"
            }
        }
    }
    if ($MustExist -and -not (Test-Path -LiteralPath $full)) {
        Fail-Closed "a required path is unavailable"
    }
    if ($MustExist -and $RequireDirectory -and -not (Test-Path -LiteralPath $full -PathType Container)) {
        Fail-Closed "a required directory is unavailable"
    }
    return $full
}

function Assert-PrivateRoot([string]$Path) {
    $root = Get-NormalLocalNtfsPath $Path $true $true
    $acl = Get-Acl -LiteralPath $root
    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $trusted = @($currentSid, "S-1-5-18")
    $ownerSid = $acl.Owner
    try {
        $ownerSid = (
            [Security.Principal.NTAccount]$acl.Owner
        ).Translate([Security.Principal.SecurityIdentifier]).Value
    } catch {
        Fail-Closed "the private root owner cannot be verified"
    }
    # Elevated Windows tokens commonly use BUILTIN\Administrators as their
    # default file owner. The DACL below remains strictly current-user/SYSTEM;
    # this narrow owner allowance mirrors the retained-handle policy.
    $trustedOwners = @($currentSid, "S-1-5-18", "S-1-5-32-544")
    if ($trustedOwners -notcontains $ownerSid) {
        Fail-Closed "the private root owner is not current-user or SYSTEM"
    }
    $seen = @{}
    foreach ($rule in $acl.Access) {
        if (
            $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow
        ) {
            Fail-Closed "the private root has an unsupported DACL rule"
        }
        try {
            $sid = $rule.IdentityReference.Translate(
                [Security.Principal.SecurityIdentifier]
            ).Value
        } catch {
            Fail-Closed "the private root DACL cannot be verified"
        }
        if ($trusted -notcontains $sid) {
            Fail-Closed "the private root grants a non-private SID"
        }
        $seen[$sid] = $true
    }
    foreach ($sid in $trusted) {
        if (-not $seen.ContainsKey($sid)) {
            Fail-Closed "the private root is missing a required DACL principal"
        }
    }
    return $root
}

function Assert-ChildOfPrivateRoot([string]$Path, [string]$Root) {
    $full = Get-NormalLocalNtfsPath $Path $false $false
    $normalizedRoot = $Root.TrimEnd('\', '/')
    if (
        -not $full.StartsWith(
            $normalizedRoot + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        Fail-Closed "the private receipt escaped the explicitly supplied private root"
    }
    return $full
}

function Assert-PrivateFile([string]$Path) {
    $file = Get-NormalLocalNtfsPath $Path $true $false
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
        Fail-Closed "the private receipt is not a regular file"
    }
    $item = Get-Item -LiteralPath $file -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Fail-Closed "the private receipt is a reparse point"
    }
    $acl = Get-Acl -LiteralPath $file
    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $trusted = @($currentSid, "S-1-5-18")
    try {
        $ownerSid = (
            [Security.Principal.NTAccount]$acl.Owner
        ).Translate([Security.Principal.SecurityIdentifier]).Value
    } catch {
        Fail-Closed "the private receipt owner cannot be verified"
    }
    if (@($currentSid, "S-1-5-18", "S-1-5-32-544") -notcontains $ownerSid) {
        Fail-Closed "the private receipt owner is not trusted"
    }
    $seen = @{}
    foreach ($rule in $acl.Access) {
        if (
            $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow
        ) {
            Fail-Closed "the private receipt has an unsupported DACL rule"
        }
        try {
            $sid = $rule.IdentityReference.Translate(
                [Security.Principal.SecurityIdentifier]
            ).Value
        } catch {
            Fail-Closed "the private receipt DACL cannot be verified"
        }
        if ($trusted -notcontains $sid) {
            Fail-Closed "the private receipt grants a non-private SID"
        }
        $seen[$sid] = $true
    }
    foreach ($sid in $trusted) {
        if (-not $seen.ContainsKey($sid)) {
            Fail-Closed "the private receipt is missing a required DACL principal"
        }
    }
    return $file
}

function Get-ExactSdkInputs([string]$SdkDirectory) {
    $sdk = Get-NormalLocalNtfsPath $SdkDirectory $true $true
    $inputs = [ordered]@{}
    foreach ($name in $script:VendorAssemblies) {
        $candidate = Join-Path $sdk $name
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            Fail-Closed "one required Autodesk SDK assembly is missing"
        }
        $item = Get-Item -LiteralPath $candidate -Force
        if (
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            $item.Length -le 0
        ) {
            Fail-Closed "one required Autodesk SDK assembly is invalid"
        }
        $inputs[$name] = Get-Sha256File $candidate
    }
    return @{ Directory = $sdk; Hashes = $inputs }
}

function Get-ProfileFramework([string]$SelectedProfile) {
    switch ($SelectedProfile) {
        "autocad2024" { return "net48" }
        "autocad2025" { return "net8.0-windows" }
        "autocad2026" { return "net10.0-windows" }
        default { Fail-Closed "the requested host profile is unsupported" }
    }
}

function Get-OrdinalSortedNames([string[]]$Names) {
    $values = [Collections.Generic.List[string]]::new()
    foreach ($name in @($Names)) {
        if ($name -isnot [string]) {
            Fail-Closed "a package file name is invalid"
        }
        $values.Add($name)
    }
    $values.Sort([StringComparer]::Ordinal)
    return @($values)
}

function Get-RuntimeComponentNames([string]$SelectedProfile) {
    $names = @(
        $script:AdapterAssemblyName,
        $script:CoreAssemblyName,
        $script:ProtocolAssemblyName
    )
    if ($SelectedProfile -ne "autocad2024") {
        $names += $script:AdapterDepsName
    }
    return Get-OrdinalSortedNames $names
}

function Get-AllowedPackageNames([string]$SelectedProfile) {
    return Get-OrdinalSortedNames @(
        (Get-RuntimeComponentNames $SelectedProfile) +
        $script:AuxiliaryPackageNames
    )
}

function Get-Sha256OfUtf8([string]$Value) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.UTF8Encoding]::new($false, $true).GetBytes($Value)
        return ConvertTo-LowercaseHex $algorithm.ComputeHash($bytes)
    } finally {
        $algorithm.Dispose()
    }
}

function Get-RuntimePackageFingerprint(
    [string]$SelectedProfile,
    [string]$Framework,
    [object[]]$Components
) {
    $expected = Get-RuntimeComponentNames $SelectedProfile
    $seen = @{}
    $ordered = @()
    foreach ($component in @($Components)) {
        if (
            $null -eq $component -or
            $component.name -isnot [string] -or
            ($component.byte_size -isnot [long] -and
            $component.byte_size -isnot [int]) -or
            $component.sha256 -isnot [string] -or
            $component.name -match '[\\/]' -or
            $component.name -ne $component.name.Normalize([Text.NormalizationForm]::FormC) -or
            $component.sha256 -notmatch '^[a-f0-9]{64}$' -or
            [int64]$component.byte_size -le 0
        ) {
            Fail-Closed "a runtime package component record is invalid"
        }
        $folded = $component.name.ToLowerInvariant()
        if ($seen.ContainsKey($folded)) {
            Fail-Closed "runtime package component names case-collide"
        }
        $seen[$folded] = $true
        $ordered += $component
    }
    $byName = @{}
    foreach ($component in $ordered) {
        $byName[$component.name] = $component
    }
    $ordered = @(
        Get-OrdinalSortedNames @($byName.Keys) |
            ForEach-Object { $byName[$_] }
    )
    if (
        $ordered.Count -ne $expected.Count -or
        (Compare-Object -ReferenceObject $expected -DifferenceObject @($ordered | ForEach-Object { $_.name }))
    ) {
        Fail-Closed "the runtime package component set is incomplete or unlisted"
    }
    $text = $script:RuntimePackageFormat + "`n" + $SelectedProfile + "`n" +
        $Framework + "`n"
    foreach ($component in $ordered) {
        $text += $component.name + "`t" +
            ([int64]$component.byte_size).ToString([Globalization.CultureInfo]::InvariantCulture) +
            "`t" + $component.sha256 + "`n"
    }
    return Get-Sha256OfUtf8 $text
}

function Get-ReceiptFingerprint([System.Collections.IDictionary]$Receipt) {
    $runtime = $Receipt.runtime_package
    $filesByName = @{}
    foreach ($file in @($Receipt.allowed_files)) {
        $filesByName[$file.name] = $file
    }
    $files = @(
        Get-OrdinalSortedNames @($filesByName.Keys) |
            ForEach-Object { $filesByName[$_] }
    )
    $sdk = $Receipt.sdk_input_fingerprints
    $text = $script:ReceiptFormat + "`n" + $script:ReceiptSchemaVersion + "`n" +
        $Receipt.profile + "`n" + $Receipt.target_framework + "`nRelease`n" +
        $runtime.fingerprint + "`n"
    foreach ($file in $files) {
        $text += $file.role + "`t" + $file.name + "`t" +
            ([int64]$file.byte_size).ToString([Globalization.CultureInfo]::InvariantCulture) +
            "`t" + $file.sha256 + "`n"
    }
    foreach ($name in (Get-OrdinalSortedNames @($sdk.Keys))) {
        $text += "sdk`t" + $name + "`t" + $sdk[$name] + "`n"
    }
    return Get-Sha256OfUtf8 $text
}

function Get-PackageFileRecord([string]$Directory, [string]$Name, [string]$Role) {
    $path = Join-Path $Directory $Name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Fail-Closed "an expected package file is unavailable"
    }
    $item = Get-Item -LiteralPath $path -Force
    if (
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $item.Length -le 0
    ) {
        Fail-Closed "an expected package file is invalid"
    }
    return [ordered]@{
        name = $Name
        byte_size = [int64]$item.Length
        sha256 = Get-Sha256File $path
        role = $Role
    }
}

function Assert-RepositoryManagedReferences(
    [string]$Directory,
    [string[]]$RuntimeNames,
    [bool]$AllowSyntaxOnlyStubReference
) {
    $repository = @(
        "LiangPingfa.NativeCad.AutoCAD.Adapter",
        "LiangPingfa.NativeCad.Core",
        "LiangPingfa.NativeCad.Protocol"
    )
    $vendor = @("AcMgd", "AcDbMgd", "AcCoreMgd")
    foreach ($name in $RuntimeNames | Where-Object { $_.EndsWith(".dll") }) {
        $path = Join-Path $Directory $name
        try {
            # AssemblyName identifies the image but cannot enumerate its
            # references. LoadFile only reads the assembly metadata here; no
            # adapter code is invoked.
            $references = [Reflection.Assembly]::LoadFile($path).GetReferencedAssemblies()
        } catch {
            Fail-Closed "a repository runtime assembly reference cannot be inspected"
        }
        foreach ($reference in $references) {
            $identity = $reference.Name
            if (
                $repository -contains $identity -or
                $vendor -contains $identity -or
                (
                    $AllowSyntaxOnlyStubReference -and
                    $identity -eq "LiangPingfa.NativeCad.AutoCAD.ApiStubs"
                ) -or
                $identity -eq "mscorlib" -or
                $identity -eq "netstandard" -or
                $identity.StartsWith("System", [StringComparison]::Ordinal) -or
                $identity.StartsWith("Microsoft.", [StringComparison]::Ordinal)
            ) {
                continue
            }
            Fail-Closed "a runtime assembly references an unlisted dependency"
        }
    }
}

function Assert-DependencyMetadata(
    [string]$Directory,
    [string]$SelectedProfile
) {
    if ($SelectedProfile -eq "autocad2024") {
        return
    }
    $deps = Join-Path $Directory $script:AdapterDepsName
    try {
        $value = ConvertFrom-JsonToDeterministicHashtable (
            Get-Content -LiteralPath $deps -Raw
        )
        $libraries = @($value.libraries.Keys)
        $targets = @($value.targets.Values)
    } catch {
        Fail-Closed "the adapter dependency metadata is invalid"
    }
    $expectedLibraries = @(
        "LiangPingfa.NativeCad.AutoCAD.Adapter/1.0.0",
        "LiangPingfa.NativeCad.Core/1.0.0",
        "LiangPingfa.NativeCad.Protocol/1.0.0"
    )
    if (
        $libraries.Count -ne $expectedLibraries.Count -or
        (Compare-Object -ReferenceObject ($expectedLibraries | Sort-Object) -DifferenceObject ($libraries | Sort-Object)) -or
        $targets.Count -ne 1
    ) {
        Fail-Closed "the adapter dependency metadata contains an unlisted dependency"
    }
    $runtimeAssets = @()
    foreach ($target in $targets) {
        foreach ($library in $target.Values) {
            if ($null -ne $library.runtime) {
                $runtimeAssets += @($library.runtime.Keys)
            }
        }
    }
    $expectedAssets = @(
        $script:AdapterAssemblyName,
        $script:CoreAssemblyName,
        $script:ProtocolAssemblyName
    )
    if (
        $runtimeAssets.Count -ne $expectedAssets.Count -or
        (Compare-Object -ReferenceObject ($expectedAssets | Sort-Object) -DifferenceObject ($runtimeAssets | Sort-Object))
    ) {
        Fail-Closed "the adapter dependency metadata runtime assets are unlisted"
    }
}

function Write-PrivateReceipt(
    [string]$Path,
    [string]$Root,
    [System.Collections.IDictionary]$Value
) {
    $receipt = Assert-ChildOfPrivateRoot $Path $Root
    if (Test-Path -LiteralPath $receipt) {
        Fail-Closed "the private build receipt already exists"
    }
    $parent = Split-Path -Path $receipt -Parent
    Assert-PrivateRoot $parent | Out-Null
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(
        ($Value | ConvertTo-Json -Depth 8 -Compress)
    )
    $stream = $null
    $created = $false
    try {
        $stream = [IO.File]::Open(
            $receipt,
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
            Remove-Item -LiteralPath $receipt -Force -ErrorAction SilentlyContinue
        }
        throw
    } finally {
        if ($null -ne $stream) {
            $stream.Dispose()
        }
    }
    Assert-PrivateRoot $parent | Out-Null
    Assert-PrivateFile $receipt | Out-Null
}

$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$project = Join-Path $repositoryRoot (
    "native-cad\src\LiangPingfa.NativeCad.AutoCAD.Adapter\" +
    "LiangPingfa.NativeCad.AutoCAD.Adapter.csproj"
)
$template = Join-Path $repositoryRoot "native-cad\templates\native-bootstrap-context.template.json"
if (-not (Test-Path -LiteralPath $project -PathType Leaf) -or
    -not (Test-Path -LiteralPath $template -PathType Leaf)) {
    Fail-Closed "the repository-authored build inputs are unavailable"
}

$sdkInputs = Get-ExactSdkInputs $CadSdkDir
$privateRoot = Assert-PrivateRoot $PrivateRoot
$receipt = Assert-ChildOfPrivateRoot $ReceiptPath $privateRoot
$framework = Get-ProfileFramework $Profile
$package = Get-NormalLocalNtfsPath $PackageDirectory $false $true
if (Test-Path -LiteralPath $package) {
    Fail-Closed "the operator package directory must not already exist"
}
$packageParent = Split-Path -Path $package -Parent
Get-NormalLocalNtfsPath $packageParent $true $true | Out-Null

if ($DryRun) {
    Write-Output (
        '{"status":"dry-run","profile":"' + $Profile +
        '","runtime":"' + $framework +
        '","sdk_inputs":"validated","vendor_binaries":"not-copied"}'
    )
    exit 0
}

$createdPackage = $false
try {
    & dotnet build $project -c $Configuration --nologo `
        "-p:BuildAutoCadAdapter=true" `
        "-p:UseAutodeskApiStubs=false" `
        "-p:CadHostProfile=$Profile" `
        "-p:TargetFramework=$framework" `
        "-p:CadSdkDir=$($sdkInputs.Directory)" `
        "-p:Deterministic=true"
    if ($LASTEXITCODE -ne 0) {
        Fail-Closed "the licensed adapter build failed"
    }

    $buildOutput = Join-Path (
        Split-Path -Path $project -Parent
    ) ("bin\$Configuration\$framework")
    Get-NormalLocalNtfsPath $buildOutput $true $true | Out-Null
    foreach ($forbidden in $script:ForbiddenPayloadNames) {
        if (Test-Path -LiteralPath (Join-Path $buildOutput $forbidden)) {
            Fail-Closed "the adapter build copied a vendor or syntax-stub binary"
        }
    }

    $runtimeNames = Get-RuntimeComponentNames $Profile
    # SDK-style net48 has no dependency file. Modern profiles deliberately
    # require one, so host-loader metadata is bound with the managed runtime
    # rather than treated as an optional sidecar.
    foreach ($name in ($runtimeNames + @(
        "LiangPingfa.NativeCad.AutoCAD.Adapter.pdb",
        "LiangPingfa.NativeCad.Core.pdb",
        "LiangPingfa.NativeCad.Protocol.pdb"
    ))) {
        if (-not (Test-Path -LiteralPath (Join-Path $buildOutput $name) -PathType Leaf)) {
            Fail-Closed "the licensed adapter build did not produce an expected repository artifact"
        }
    }
    Assert-RepositoryManagedReferences `
        $buildOutput $runtimeNames $TestOnlyFakeSdk.IsPresent
    Assert-DependencyMetadata $buildOutput $Profile
    $payloadNames = @(
        $runtimeNames +
        @(
            "LiangPingfa.NativeCad.AutoCAD.Adapter.pdb",
            "LiangPingfa.NativeCad.Core.pdb",
            "LiangPingfa.NativeCad.Protocol.pdb"
        )
    )

    New-Item -ItemType Directory -Path $package -ErrorAction Stop | Out-Null
    $createdPackage = $true
    foreach ($name in $payloadNames) {
        Copy-Item -LiteralPath (Join-Path $buildOutput $name) `
            -Destination (Join-Path $package $name) -ErrorAction Stop
    }
    Copy-Item -LiteralPath (Join-Path $repositoryRoot "native-cad\README.md") `
        -Destination (Join-Path $package "README.md") -ErrorAction Stop
    Copy-Item -LiteralPath $template `
        -Destination (Join-Path $package "native-bootstrap-context.template.json") `
        -ErrorAction Stop

    $expectedPackage = Get-AllowedPackageNames $Profile
    $packageEntries = @(Get-ChildItem -LiteralPath $package -Force)
    $actualPackage = @($packageEntries | ForEach-Object { $_.Name } | Sort-Object)
    if (
        @($packageEntries | Where-Object {
            $_.PSIsContainer -or
            ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
        }).Count -ne 0 -or
        @($actualPackage | ForEach-Object { $_.ToLowerInvariant() } |
            Select-Object -Unique).Count -ne @($actualPackage).Count -or
        @($actualPackage).Count -ne @($expectedPackage).Count -or
        (Compare-Object -ReferenceObject $expectedPackage `
            -DifferenceObject $actualPackage)
    ) {
        Fail-Closed "the operator package contains an unexpected file"
    }
    foreach ($forbidden in $script:ForbiddenPayloadNames) {
        if (Test-Path -LiteralPath (Join-Path $package $forbidden)) {
            Fail-Closed "the operator package contains a vendor or syntax-stub binary"
        }
    }

    $runtimeComponents = @()
    foreach ($name in $runtimeNames) {
        $record = Get-PackageFileRecord $package $name "runtime"
        $runtimeComponents += [ordered]@{
            name = $record.name
            byte_size = $record.byte_size
            sha256 = $record.sha256
        }
    }
    $runtimeFingerprint = Get-RuntimePackageFingerprint `
        $Profile $framework $runtimeComponents
    $allowedFiles = @()
    foreach ($name in $expectedPackage) {
        $role = if ($runtimeNames -contains $name) { "runtime" } else { "auxiliary" }
        $allowedFiles += Get-PackageFileRecord $package $name $role
    }
    $runtimeComponentsByName = @{}
    foreach ($component in $runtimeComponents) {
        $runtimeComponentsByName[$component.name] = $component
    }
    $orderedRuntimeComponents = @()
    foreach ($name in (Get-OrdinalSortedNames @($runtimeComponentsByName.Keys))) {
        $orderedRuntimeComponents += $runtimeComponentsByName[$name]
    }
    $allowedFilesByName = @{}
    foreach ($file in $allowedFiles) {
        $allowedFilesByName[$file.name] = $file
    }
    $orderedAllowedFiles = @()
    foreach ($name in (Get-OrdinalSortedNames @($allowedFilesByName.Keys))) {
        $orderedAllowedFiles += $allowedFilesByName[$name]
    }
    $receiptValue = [ordered]@{
        schema_version = $script:ReceiptSchemaVersion
        receipt_format_version = $script:ReceiptFormat
        profile = $Profile
        target_framework = $framework
        configuration = $Configuration
        runtime_package = [ordered]@{
            format_version = $script:RuntimePackageFormat
            profile = $Profile
            target_framework = $framework
            fingerprint = $runtimeFingerprint
            components = $orderedRuntimeComponents
        }
        allowed_files = $orderedAllowedFiles
        sdk_input_fingerprints = $sdkInputs.Hashes
    }
    $receiptValue.integrity = [ordered]@{
        algorithm = "SHA-256"
        sha256 = Get-ReceiptFingerprint $receiptValue
    }
    if ($TestOnlyFakeSdk) {
        # Keep fixture output visually and semantically distinct from the
        # strict receipt schema that real-host qualification accepts.
        $receiptValue.test_only = "syntax-only-fake-sdk"
    }
    Write-PrivateReceipt $receipt $privateRoot $receiptValue
    Write-Output (
        '{"status":"ok","profile":"' + $Profile +
        '","runtime":"' + $framework +
        '","package":"repository-authored-only","qualification":"' +
        $(if ($TestOnlyFakeSdk) { "test-only-fake-sdk" } else { "not-run" }) +
        '"}'
    )
} catch {
    if ($createdPackage -and (Test-Path -LiteralPath $package)) {
        Remove-Item -LiteralPath $package -Recurse -Force -ErrorAction SilentlyContinue
    }
    throw
}
