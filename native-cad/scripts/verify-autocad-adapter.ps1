param(
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$project = Join-Path $PSScriptRoot "..\src\LiangPingfa.NativeCad.AutoCAD.Adapter\LiangPingfa.NativeCad.AutoCAD.Adapter.csproj"
$testProject = Join-Path $PSScriptRoot "..\tests\LiangPingfa.NativeCad.AutoCAD.Adapter.Tests\LiangPingfa.NativeCad.AutoCAD.Adapter.Tests.csproj"
$adapterRoot = Split-Path -Parent $project

function Invoke-StubBuild {
    param([string]$Profile)

    & dotnet build $project -c $Configuration --nologo `
        "-p:BuildAutoCadAdapter=true" `
        "-p:UseAutodeskApiStubs=true" `
        "-p:CadHostProfile=$Profile"
    if ($LASTEXITCODE -ne 0) {
        throw "Syntax-only adapter build failed for $Profile."
    }
}

function Invoke-StubTestHostBuild {
    param([string]$Profile)

    & dotnet build $testProject -c $Configuration --nologo `
        "-p:CadHostProfile=$Profile"
    if ($LASTEXITCODE -ne 0) {
        throw "Syntax-only adapter test-host build failed for $Profile."
    }
}

$net48Reference = "C:\Program Files (x86)\Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8"
if (-not (Test-Path $net48Reference)) {
    throw "The net48 targeting pack is required to compile the autocad2024 syntax profile."
}

foreach ($profile in @(
    "autocad2024",
    "autocad2025",
    "autocad2026"
)) {
    Invoke-StubBuild -Profile $profile
    Invoke-StubTestHostBuild -Profile $profile
}

& dotnet build $project -c $Configuration --nologo `
    "-p:BuildAutoCadAdapter=true" `
    "-p:UseAutodeskApiStubs=false" `
    "-p:CadHostProfile=autocad2025" `
    "-p:CadSdkDir=C:\liang-pingfa-missing-autodesk-sdk"
if ($LASTEXITCODE -eq 0) {
    throw "A real adapter build unexpectedly accepted a missing SDK directory."
}

& dotnet pack $project -c $Configuration --nologo `
    "-p:BuildAutoCadAdapter=true" `
    "-p:UseAutodeskApiStubs=true" `
    "-p:CadHostProfile=autocad2025"
if ($LASTEXITCODE -eq 0) {
    throw "Adapter packing unexpectedly succeeded."
}

& dotnet publish $project -c $Configuration --nologo `
    "-p:BuildAutoCadAdapter=true" `
    "-p:UseAutodeskApiStubs=true" `
    "-p:CadHostProfile=autocad2025"
if ($LASTEXITCODE -eq 0) {
    throw "Adapter publishing unexpectedly succeeded."
}

$forbidden = Get-ChildItem $adapterRoot -Recurse -File -Include `
    AcMgd.dll,AcDbMgd.dll,AcCoreMgd.dll,LiangPingfa.NativeCad.AutoCAD.ApiStubs.dll
if ($forbidden) {
    throw "Adapter output contains a vendor or syntax-stub DLL."
}

& dotnet run --project $testProject -c $Configuration --nologo `
    "-p:CadHostProfile=autocad2025"
if ($LASTEXITCODE -ne 0) {
    throw "Syntax-only adapter test host failed."
}

Write-Output "PASS: syntax-only AutoCAD adapter profiles, test hosts, fail-closed probes, and output scans."
$global:LASTEXITCODE = 0
