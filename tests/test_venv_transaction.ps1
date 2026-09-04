$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$installerPath = Join-Path $root 'install.ps1'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($installerPath, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw 'install.ps1 syntax is invalid' }

foreach ($name in @('Enter-InstallerMutex', 'Exit-InstallerMutex', 'Install-TrackerRuntime', 'Complete-TrackerRuntimeInstall', 'Restore-PreviousTrackerRuntime')) {
    $functionAst = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    if (-not $functionAst) { throw "Installer function missing: $name" }
    Invoke-Expression $functionAst.Extent.Text
}
function Write-InstallLog { param($Message) }
$script:installerMutex = $null
$script:installerMutexOwned = $false

if ($env:OS -eq 'Windows_NT') {
    Add-Type -TypeDefinition @'
using System;
using System.Threading;
public static class AC6InstallerMutexHolder {
    private static Mutex mutex;
    private static Thread thread;
    private static readonly ManualResetEvent ready = new ManualResetEvent(false);
    private static readonly ManualResetEvent release = new ManualResetEvent(false);
    public static void Start() {
        thread = new Thread(() => {
            bool created;
            mutex = new Mutex(false, @"Local\AC6WinLossTrackerInstaller", out created);
            mutex.WaitOne();
            ready.Set();
            release.WaitOne();
            mutex.ReleaseMutex();
            mutex.Dispose();
        });
        thread.IsBackground = true;
        thread.Start();
        if (!ready.WaitOne(5000)) throw new Exception("mutex holder did not start");
    }
    public static void Stop() {
        release.Set();
        if (thread != null) thread.Join(5000);
    }
}
'@
    [AC6InstallerMutexHolder]::Start()
    try {
        $blocked = $false
        try { Enter-InstallerMutex } catch { $blocked = $true }
        if (-not $blocked) { throw 'concurrent installer acquired an owned mutex' }
    } finally {
        Exit-InstallerMutex
        [AC6InstallerMutexHolder]::Stop()
    }
}

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('ac6-venv-transaction-' + [Guid]::NewGuid().ToString('N'))
try {
    $runtimeRoot = Join-Path $testRoot 'runtime'
    $venvPath = Join-Path $runtimeRoot 'venv'
    $script:venvBackupPath = Join-Path $runtimeRoot 'venv.previous'
    $script:runtimeSwapped = $false
    $script:hadPreviousRuntime = $false

    # First install can be rolled back without leaving a partial runtime.
    $candidate = Join-Path $testRoot 'candidate-first'
    New-Item -ItemType Directory -Path $candidate -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $candidate 'marker.txt') -Value 'first'
    Install-TrackerRuntime -CandidatePath $candidate
    if (-not (Test-Path -LiteralPath (Join-Path $venvPath 'marker.txt'))) { throw 'first venv was not activated' }
    Restore-PreviousTrackerRuntime
    if (Test-Path -LiteralPath $venvPath) { throw 'first-install rollback left active venv' }

    # Update rollback restores the prior runtime exactly.
    New-Item -ItemType Directory -Path $venvPath -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $venvPath 'marker.txt') -Value 'old'
    $candidate = Join-Path $testRoot 'candidate-update'
    New-Item -ItemType Directory -Path $candidate -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $candidate 'marker.txt') -Value 'new'
    Install-TrackerRuntime -CandidatePath $candidate
    if ((Get-Content -LiteralPath (Join-Path $venvPath 'marker.txt') -Raw).Trim() -ne 'new') { throw 'new venv was not activated' }
    Restore-PreviousTrackerRuntime
    if ((Get-Content -LiteralPath (Join-Path $venvPath 'marker.txt') -Raw).Trim() -ne 'old') { throw 'old venv was not restored' }

    # A broken existing runtime is replaced and the backup is removed only on completion.
    $candidate = Join-Path $testRoot 'candidate-rebuild'
    New-Item -ItemType Directory -Path $candidate -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $candidate 'marker.txt') -Value 'rebuilt'
    Install-TrackerRuntime -CandidatePath $candidate
    Complete-TrackerRuntimeInstall
    if ((Get-Content -LiteralPath (Join-Path $venvPath 'marker.txt') -Raw).Trim() -ne 'rebuilt') { throw 'broken venv was not rebuilt' }
    if (Test-Path -LiteralPath $script:venvBackupPath) { throw 'completed update left venv backup' }
} finally {
    if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
}

$source = Get-Content -LiteralPath $installerPath -Raw
if (-not $source.Contains("New-Object System.Threading.Mutex(`$false, 'Local\AC6WinLossTrackerInstaller'")) { throw 'named installer mutex missing' }
if (-not $source.Contains('$shortcut.Arguments = ''-s "{0}"''')) { throw 'shortcut does not disable user site at interpreter startup' }
if ($source -match "'--user'") { throw 'user-site pip fallback is forbidden' }
Write-Host 'Dedicated venv first install, update, rollback and rebuild checks: OK'
