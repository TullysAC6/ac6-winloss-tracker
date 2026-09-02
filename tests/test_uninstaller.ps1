$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$uninstallerPath = Join-Path $root 'uninstall.ps1'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($uninstallerPath, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw 'uninstall.ps1 syntax is invalid' }

foreach ($name in @('Test-TrackerCommandLine', 'Remove-TrackerRuntimeFiles', 'Remove-TrackerSource', 'Stop-TrackerSafely')) {
    $functionAst = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    if (-not $functionAst) { throw "Uninstaller function missing: $name" }
    Invoke-Expression $functionAst.Extent.Text
}

$originalLocalAppData = $env:LOCALAPPDATA
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('ac6-uninstall-test-' + [Guid]::NewGuid().ToString('N'))
try {
    $env:LOCALAPPDATA = $testRoot
    $dataPath = Join-Path $testRoot 'AC6WinLossTracker'
    $installPath = Join-Path $testRoot 'Programs\AC6WinLossTrackerSource'
    $runtimeFileNames = @('.runtime.json', '.runtime.json.tmp', '.overlay-runtime.json', '.dashboard-runtime.json')
    New-Item -ItemType Directory -Path $installPath -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $dataPath 'diagnostics') -Force | Out-Null
    foreach ($name in @('history.db', 'config.json', 'stats.json')) { Set-Content -LiteralPath (Join-Path $dataPath $name) -Value 'fixture' }
    foreach ($name in $runtimeFileNames) { Set-Content -LiteralPath (Join-Path $dataPath $name) -Value '{}' }
    Set-Content -LiteralPath (Join-Path $installPath 'app.py') -Value '# fixture'
    New-Item -ItemType Directory -Path "$installPath.previous" -Force | Out-Null

    if (-not (Test-TrackerCommandLine 'python.exe' ('"C:\Python314\python.exe" "' + $installPath + '\app.py"'))) { throw 'owned app process rejected' }
    if (-not (Test-TrackerCommandLine 'pythonw.exe' ('"C:\Python314\pythonw.exe" "' + $installPath + '\dashboard.py"'))) { throw 'owned dashboard process rejected' }
    if (-not (Test-TrackerCommandLine 'pythonw.exe' ('"C:\Python314\pythonw.exe" "' + $installPath + '\launcher.pyw"'))) { throw 'owned launcher/overlay process rejected' }
    if (Test-TrackerCommandLine 'python.exe' '"C:\Python314\python.exe" "C:\Other\app.py"') { throw 'unrelated Python accepted' }
    if (Test-TrackerCommandLine 'python.exe' ('"C:\Python314\python.exe" "' + $installPath + '-similar\app.py"')) { throw 'similar path accepted' }

    Remove-TrackerRuntimeFiles
    foreach ($name in $runtimeFileNames) { if (Test-Path -LiteralPath (Join-Path $dataPath $name)) { throw "runtime file remains: $name" } }
    Remove-TrackerSource
    if (Test-Path -LiteralPath $installPath) { throw 'application source remains' }
    if (Test-Path -LiteralPath "$installPath.previous") { throw 'previous application source remains' }
    foreach ($name in @('history.db', 'config.json', 'stats.json', 'diagnostics')) { if (-not (Test-Path -LiteralPath (Join-Path $dataPath $name))) { throw "user data removed: $name" } }

    New-Item -ItemType Directory -Path $installPath -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $installPath 'app.py') -Value '# reinstalled'
    if ((Get-Content -LiteralPath (Join-Path $dataPath 'history.db') -Raw).Trim() -ne 'fixture') { throw 'history fixture was not preserved across reinstall' }
} finally {
    $env:LOCALAPPDATA = $originalLocalAppData
    if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
}

# Running Tracker: authenticated graceful shutdown completes without fallback.
$script:mockRunning = $true
$script:fallbackCalls = 0
function Get-TrackerRuntime { [PSCustomObject]@{ Pid=4242; Port=8765; Token='unit-test-token' } }
function Get-TrackerProcesses { if ($script:mockRunning) { @([PSCustomObject]@{ ProcessId=4242 }) } else { @() } }
function Invoke-WebRequest { param($Uri, $Method, $Headers, [switch]$UseBasicParsing, $TimeoutSec, $ErrorAction); if ($Headers['X-Control-Token'] -ne 'unit-test-token') { throw 'missing token' }; $script:mockRunning=$false }
function Wait-TrackerStopped { return -not $script:mockRunning }
function Stop-Process { param($Id, [switch]$Force, $ErrorAction); $script:fallbackCalls++ }
function Write-UninstallLog { param($Message) }
if ((Stop-TrackerSafely) -ne 8765 -or $script:fallbackCalls -ne 0) { throw 'graceful running-Tracker shutdown failed' }

# Graceful failure: only verified Tracker processes reach fallback stop.
$script:mockRunning = $true
$script:waitCalls = 0
function Invoke-WebRequest { throw 'mock graceful failure' }
function Wait-TrackerStopped { $script:waitCalls++; return $script:waitCalls -ge 2 }
function Stop-Process { param($Id, [switch]$Force, $ErrorAction); if ($Id -ne 4242) { throw 'unrelated process targeted' }; $script:fallbackCalls++; $script:mockRunning=$false }
[void](Stop-TrackerSafely)
if ($script:fallbackCalls -ne 1) { throw 'Tracker-only fallback was not used exactly once' }

$source = Get-Content -LiteralPath $uninstallerPath -Raw
foreach ($required in @('X-Control-Token', '/api/system/shutdown', '/health', '/stats', 'Stop-Process', 'AC6 WinLoss Tracker.lnk', 'ShouldProcess', 'RemoveUserData')) {
    if (-not $source.Contains($required)) { throw "Uninstaller safety behavior missing: $required" }
}
if ($source -match '(?i)winget\s+uninstall|Python\.Python.*uninstall') { throw 'Uninstaller must not remove Python' }
Write-Host 'Uninstaller ownership, preservation, cleanup and reinstall checks: OK'
