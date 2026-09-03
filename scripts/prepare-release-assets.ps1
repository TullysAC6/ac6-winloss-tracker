[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$OutputDirectory)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$output = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $output -Force | Out-Null

foreach ($name in @('install.ps1', 'uninstall.ps1')) {
    $source = Join-Path $root $name
    $destination = Join-Path $output $name
    Copy-Item -LiteralPath $source -Destination $destination -Force
    $hash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
    [System.IO.File]::WriteAllText(
        (Join-Path $output "$name.sha256"), "$hash *$name`n",
        (New-Object System.Text.UTF8Encoding($false))
    )
}

Write-Host "Verified release script assets prepared at $output"
