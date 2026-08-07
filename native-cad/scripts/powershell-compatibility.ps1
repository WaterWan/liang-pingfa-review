# Windows PowerShell 5.1 and PowerShell 7 share this deliberately small
# compatibility surface.  Keep it free of PowerShell 7 syntax and .NET Core
# APIs because operator scripts dot-source it before validating private input.

if ($PSVersionTable.PSEdition -eq "Desktop") {
    # A Windows PowerShell process started from a pwsh parent can inherit a
    # PSModulePath that resolves the incompatible PowerShell 7 security module
    # first. Load the desktop inbox module by its PSHOME manifest instead.
    $desktopSecurityModule = Join-Path `
        (Join-Path $PSHOME "Modules") `
        "Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1"
    if (Test-Path -LiteralPath $desktopSecurityModule) {
        Import-Module -Name $desktopSecurityModule -ErrorAction Stop
    }
}

function ConvertTo-LowercaseHex {
    param(
        [Parameter(Mandatory = $true)]
        [byte[]]$Bytes
    )

    # The newer framework hex encoder is unavailable on .NET Framework.
    # BitConverter produces identical bytes once separators are removed and
    # the invariant lower-case transform is applied.
    return [BitConverter]::ToString($Bytes).Replace("-", "").ToLowerInvariant()
}

function Test-MapContainsKey {
    param(
        [AllowNull()]
        [object]$Map,

        [Parameter(Mandatory = $true)]
        [string]$Key
    )

    # OrderedDictionary intentionally exposes Contains rather than
    # ContainsKey.  Do not call either: Hashtable's lookup is normally
    # case-insensitive, whereas receipt component names are ordinal contract
    # values. Enumerating keys keeps all supported map representations exact.
    if ($null -eq $Map) {
        return $false
    }
    if ($Map -is [System.Collections.IDictionary]) {
        foreach ($candidate in $Map.Keys) {
            if (
                $candidate -is [string] -and
                [string]::Equals(
                    $candidate, $Key, [StringComparison]::Ordinal
                )
            ) {
                return $true
            }
        }
        return $false
    }
    if ($Map -is [pscustomobject]) {
        foreach ($property in $Map.PSObject.Properties) {
            if (
                [string]::Equals(
                    $property.Name, $Key, [StringComparison]::Ordinal
                )
            ) {
                return $true
            }
        }
    }
    return $false
}

function Get-Sha256File {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $stream = $null
    $algorithm = $null
    try {
        $stream = [IO.File]::Open(
            $Path,
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::Read
        )
        $algorithm = [Security.Cryptography.SHA256]::Create()
        return ConvertTo-LowercaseHex $algorithm.ComputeHash($stream)
    } finally {
        if ($null -ne $algorithm) {
            $algorithm.Dispose()
        }
        if ($null -ne $stream) {
            $stream.Dispose()
        }
    }
}

function ConvertTo-DeterministicHashtable {
    param(
        [AllowNull()]
        [object]$Value
    )

    if ($null -eq $Value) {
        return $null
    }
    if ($Value -is [System.Collections.IDictionary]) {
        # Ordinary PowerShell hashtables are case-insensitive. Receipt and
        # package names are case-sensitive, so preserve insertion order and
        # ordinal key identity on both supported PowerShell implementations.
        $result = [System.Collections.Specialized.OrderedDictionary]::new(
            [StringComparer]::Ordinal
        )
        foreach ($key in $Value.Keys) {
            if ($key -isnot [string]) {
                throw "JSON object key is not a string"
            }
            $result.Add($key, (ConvertTo-DeterministicHashtable $Value[$key]))
        }
        return $result
    }
    if ($Value -is [pscustomobject]) {
        $result = [System.Collections.Specialized.OrderedDictionary]::new(
            [StringComparer]::Ordinal
        )
        foreach ($property in $Value.PSObject.Properties) {
            $result.Add(
                $property.Name,
                (ConvertTo-DeterministicHashtable $property.Value)
            )
        }
        return $result
    }
    if ($Value -is [System.Collections.IEnumerable] -and
        $Value -isnot [string]) {
        $result = @()
        foreach ($item in $Value) {
            $result += ,(ConvertTo-DeterministicHashtable $item)
        }
        return ,$result
    }
    return $Value
}

function ConvertFrom-JsonToDeterministicHashtable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Json
    )

    return ConvertTo-DeterministicHashtable (
        ConvertFrom-Json -InputObject $Json -ErrorAction Stop
    )
}
