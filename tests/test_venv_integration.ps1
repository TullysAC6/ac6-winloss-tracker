param([Parameter(Mandatory = $true)][string]$BasePythonPath)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$installerPath = Join-Path $root 'install.ps1'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($installerPath, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw 'install.ps1 syntax is invalid' }

foreach ($name in @('ConvertTo-NativeArgument', 'Invoke-NativeCommand', 'Get-IsolatedPythonEnvironment', 'New-TrackerVenv')) {
    $functionAst = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    if (-not $functionAst) { throw "Installer function missing: $name" }
    Invoke-Expression $functionAst.Extent.Text
}
function Write-InstallLog { param($Message) }
function Write-Step { param($Message) }

$versionInvocation = Invoke-NativeCommand -FilePath $BasePythonPath -ArgumentList @('-c', 'import json,sys; print(json.dumps({"major":sys.version_info.major,"minor":sys.version_info.minor,"version":".".join(map(str,sys.version_info[:3]))}))')
if ($versionInvocation.ExitCode -ne 0) { throw "base Python version query failed: $($versionInvocation.StdErr)" }
$version = $versionInvocation.StdOut | ConvertFrom-Json
if ([int]$version.major -ne 3 -or [int]$version.minor -notin @(13, 14)) { throw 'integration test requires Python 3.13 or 3.14' }
$base = [PSCustomObject]@{
    PythonPath = (Resolve-Path -LiteralPath $BasePythonPath).Path
    Major = [int]$version.major
    Minor = [int]$version.minor
    Version = [string]$version.version
}

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('ac6-venv-integration-' + [Guid]::NewGuid().ToString('N'))
$oldPythonUserBase = $env:PYTHONUSERBASE
try {
    $sourcePath = $root
    $destinationPath = Join-Path $testRoot 'venv'
    $userBase = Join-Path $testRoot 'existing-user-python'
    New-Item -ItemType Directory -Path $userBase -Force | Out-Null
    $userSentinel = Join-Path $userBase 'existing-package.txt'
    Set-Content -LiteralPath $userSentinel -Value 'must remain unchanged'
    $env:PYTHONUSERBASE = $userBase

    $result = New-TrackerVenv -BasePython $base -RequirementsPath (Join-Path $root 'requirements.lock') `
        -SourcePath $sourcePath -DestinationPath $destinationPath
    if (-not (Test-Path -LiteralPath $result.VenvPythonPath -PathType Leaf)) { throw 'venv python missing after integration build' }
    if (-not (Test-Path -LiteralPath $result.VenvPythonwPath -PathType Leaf)) { throw 'venv pythonw missing after integration build' }
    if ($result.DependencyVersions -ne 'mss=10.2.0; ttkbootstrap=2.2.2; Pillow=12.3.0') { throw 'locked dependency versions differ' }
    if ((Get-Content -LiteralPath $userSentinel -Raw).Trim() -ne 'must remain unchanged') { throw 'existing user Python package fixture changed' }
    $lifetime = Invoke-NativeCommand -FilePath $result.VenvPythonPath -ArgumentList @((Join-Path $root 'tests/test_venv_lifetime.py'))
    Write-Host $lifetime.StdOut
    if ($lifetime.ExitCode -ne 0) { throw "venv launcher lifetime failed: $($lifetime.StdErr)" }
} finally {
    $env:PYTHONUSERBASE = $oldPythonUserBase
    if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
}

Write-Host "Dedicated venv integration on Python $($base.Version): OK"
