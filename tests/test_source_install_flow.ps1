[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$PythonPath)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$shellPath = (Get-Process -Id $PID).Path
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ac6-f-' + [guid]::NewGuid().ToString('N').Substring(0,12))
$fixture = [IO.Path]::GetFullPath($fixture)
if (-not $fixture.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe fixture root' }
$originalLocal = $env:LOCALAPPDATA
$originalUserBase = $env:PYTHONUSERBASE
$originalPipOverride = $env:PIP_BREAK_SYSTEM_PACKAGES
$fixtureVariables = @('AC6_FLOW_ROOT','AC6_FLOW_PYTHON','AC6_FLOW_VERSION','AC6_FLOW_ARCHIVE','AC6_FLOW_DESKTOP','AC6_FLOW_SOURCE','AC6_FLOW_FAIL_READY','AC6_FLOW_FAIL_STAGE','AC6_FLOW_HOLD','PIP_CONFIG_FILE','PIP_TARGET')
$oldVariables = @{}
foreach ($name in $fixtureVariables) { $oldVariables[$name] = [Environment]::GetEnvironmentVariable($name) }
$commit = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
$ownedRoot = Join-Path $fixture 'Programs\AC6WinLossTracker'
$installed = Join-Path $ownedRoot 'app'
$legacy = Join-Path $fixture 'Programs\AC6WinLossTrackerSource'
$data = Join-Path $fixture 'AC6WinLossTracker'
$utf8 = New-Object System.Text.UTF8Encoding($true)
function Assert-NoOwnedProcess {
    $owned = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and ($_.CommandLine.Contains($installed) -or $_.CommandLine.Contains($legacy)) -and $_.Name -match '^pythonw?\.exe$' })
    if ($owned.Count) { throw "Owned processes remain: $($owned.ProcessId -join ',')" }
}
function Read-Runtime { Get-Content -LiteralPath (Join-Path $data '.runtime.json') -Raw | ConvertFrom-Json }
function Stop-FixtureProcess {
    param($ProcessInfo)
    if (-not $ProcessInfo -or -not $ProcessInfo.CommandLine -or
        -not ($ProcessInfo.CommandLine.Contains($installed) -or $ProcessInfo.CommandLine.Contains($legacy))) { throw 'Unowned test process' }
    $handle = [Diagnostics.Process]::GetProcessById([int]$ProcessInfo.ProcessId)
    try {
        $null = $handle.Handle
        $fresh = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $ProcessInfo.ProcessId)
        if (-not $fresh -or $fresh.CreationDate -ne $ProcessInfo.CreationDate) { throw 'Test process identity changed' }
        if (-not $handle.HasExited) { $handle.Kill() }
        if (-not $handle.WaitForExit(5000)) { throw 'Test process cleanup timed out' }
    } finally { $handle.Dispose() }
}
function Start-FixturePython {
    param([string]$Arguments, [string]$EnvironmentPath, [string]$Directory=$installed)
    if (-not $EnvironmentPath) { $EnvironmentPath = (Get-Content (Join-Path $data 'installed-version.json') -Raw | ConvertFrom-Json).environment_path }
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = Join-Path (Split-Path $env:AC6_FLOW_PYTHON) 'pythonw.exe'
    $info.Arguments = $Arguments
    $info.WorkingDirectory = $Directory
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    foreach ($name in @('PYTHONPATH','PYTHONHOME','PYTHONUSERBASE','PYTHONSTARTUP')) { $info.EnvironmentVariables.Remove($name) }
    $info.EnvironmentVariables['__PYVENV_LAUNCHER__'] = Join-Path $EnvironmentPath 'Scripts\pythonw.exe'
    return [Diagnostics.Process]::Start($info)
}
function Wait-Healthy {
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        try {
            $r = Read-Runtime
            $h = Invoke-RestMethod -Uri "http://127.0.0.1:$($r.port)/health" -TimeoutSec 2
            if ($h.ok) { return $r }
        } catch {}
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Previous working application was not restored'
}
function Check-Rollback {
    param([string]$Stage)
    $beforeRuntime = Read-Runtime
    $metadataHash = (Get-FileHash (Join-Path $data 'installed-version.json')).Hash
    $shortcutHash = (Get-FileHash (Join-Path $env:AC6_FLOW_DESKTOP 'AC6 WinLoss Tracker.lnk')).Hash
    $environment = (Get-Content (Join-Path $data 'installed-version.json') -Raw | ConvertFrom-Json).environment_path
    $environments = @(Get-ChildItem (Join-Path $ownedRoot 'venv') -Directory).FullName -join ';'
    Set-Content (Join-Path $installed 'rollback-sentinel.txt') 'previous-source'
    $env:AC6_FLOW_FAIL_STAGE = $Stage
    try {
        & $shellPath -NoProfile -ExecutionPolicy Bypass -File (Join-Path $fixture 'install-fixture.ps1') -SourceCommit $commit *> (Join-Path $fixture "rollback-$Stage.log")
        if ($LASTEXITCODE -eq 0) { throw "Injected $Stage failure was accepted" }
    } finally { $env:AC6_FLOW_FAIL_STAGE = '' }
    if (-not (Test-Path (Join-Path $installed 'rollback-sentinel.txt'))) { throw "$Stage lost source" }
    if ((Get-FileHash (Join-Path $data 'installed-version.json')).Hash -ne $metadataHash) { throw "$Stage lost environment metadata" }
    if ((Get-FileHash (Join-Path $env:AC6_FLOW_DESKTOP 'AC6 WinLoss Tracker.lnk')).Hash -ne $shortcutHash) { throw "$Stage lost shortcut" }
    if ((@(Get-ChildItem (Join-Path $ownedRoot 'venv') -Directory).FullName -join ';') -ne $environments) { throw "$Stage leaked environment" }
    if (-not (Test-Path $environment)) { throw "$Stage removed the active environment" }
    $restored = Wait-Healthy
    if ($Stage -in @('venv-create','pip-install','venv-verify','python-verification','stop-running-app') -and $restored.pid -ne $beforeRuntime.pid) { throw "$Stage stopped the previous application too early" }
    if (Test-Path "$installed.previous") { throw "$Stage leaked previous source" }
    Write-Output "Rollback $Stage / source / environment / shortcut / metadata / running state: PASS"
}
function Run-Installer {
    param([switch]$Stable)
    $sourceArgs = if ($Stable) { @() } else { @('-SourceCommit', $commit) }
    & $shellPath -NoProfile -ExecutionPolicy Bypass -File (Join-Path $fixture 'install-fixture.ps1') @sourceArgs *> (Join-Path $fixture 'last-install.log')
    if ($LASTEXITCODE -ne 0) {
        Get-Content (Join-Path $fixture 'last-install.log') -Tail 20 | ForEach-Object { Write-Host $_ }
        if (Test-Path (Join-Path $data 'source-install.log')) { Get-Content (Join-Path $data 'source-install.log') -Tail 12 | ForEach-Object { Write-Host $_ } }
        throw 'Installer failed'
    }
    $r = Read-Runtime
    $summary = Invoke-RestMethod -Uri "http://127.0.0.1:$($r.port)/api/dashboard/summary"
    if ($summary.lifetime.matches -ne 4 -or $summary.lifetime.wins -ne 3 -or $summary.lifetime.losses -ne 1) { throw 'Lifetime data lost' }
    if ((Get-Content (Join-Path $data 'config.json') -Raw) -ne $script:configBefore) { throw 'Config changed' }
    if (Test-Path "$installed.previous") { throw 'Backup source remains after successful install' }
    if (Test-Path (Join-Path $fixture 'forbidden-pip-target')) { throw 'Host pip configuration escaped the owned environment' }
    $metadata = Get-Content (Join-Path $data 'installed-version.json') -Raw | ConvertFrom-Json
    if ($metadata.resolved_commit -ne $commit -or $metadata.version -ne '1.2.0') { throw 'Installed source identity incorrect' }
    if ($Stable) {
        if ($metadata.channel -ne 'stable' -or $metadata.PSObject.Properties['source_kind']) { throw 'Stable metadata changed' }
    } elseif ($metadata.channel -ne 'candidate' -or $metadata.source_kind -ne 'commit' -or $metadata.source_ref -ne $commit) { throw 'Candidate source identity missing' }
    return $r
}
function Run-Uninstaller {
    & $shellPath -NoProfile -ExecutionPolicy Bypass -File (Join-Path $fixture 'uninstall-fixture.ps1') *> (Join-Path $fixture 'last-uninstall.log')
    if ($LASTEXITCODE -ne 0) { Get-Content (Join-Path $fixture 'last-uninstall.log'); throw 'Uninstaller failed' }
    Assert-NoOwnedProcess
    if ((Test-Path $ownedRoot) -or (Test-Path $legacy)) { throw 'Owned environment or source remains' }
    if (Test-Path (Join-Path $env:AC6_FLOW_DESKTOP 'AC6 WinLoss Tracker.lnk')) { throw 'Shortcut remains' }
    foreach ($name in @('history.db','config.json','stats.json','diagnostics','installed-version.json')) {
        if (-not (Test-Path (Join-Path $data $name))) { throw "Preserved data missing: $name" }
    }
    if (@(Get-ChildItem $data -Filter '.*runtime*').Count) { throw 'Runtime files remain' }
}
function Check-Report {
    $code = @'
import os,sys,zipfile,json
sys.path.insert(0,os.environ['AC6_FLOW_SOURCE'])
from diagnostics import RECORDER
p=RECORDER.export(destination_dir=os.environ['AC6_FLOW_ROOT'])
with zipfile.ZipFile(p) as z:
 assert z.testzip() is None
 assert {'manifest.json','startup.log','source-install.log','installed-version.json','detector.jsonl'} <= set(z.namelist())
 assert '.runtime.json' not in z.namelist()
 assert 'windows-capture' in json.loads(z.read('manifest.json'))['dependency_status']
'@
    & $PythonPath -c $code
    if ($LASTEXITCODE -ne 0) { throw 'Live/offline diagnostic report failed' }
}
try {
    New-Item -ItemType Directory -Path $fixture,$data,(Join-Path $fixture 'Desktop') -Force | Out-Null
    $env:LOCALAPPDATA = $fixture
    $env:PYTHONUSERBASE = Join-Path $fixture 'python-user'
    # Deliberate contamination: production children must exclude this user site.
    $env:PIP_BREAK_SYSTEM_PACKAGES = $null
    $env:PIP_CONFIG_FILE = Join-Path $fixture 'hostile-pip.ini'
    $env:PIP_TARGET = Join-Path $fixture 'forbidden-pip-target'
    [IO.File]::WriteAllText($env:PIP_CONFIG_FILE, "[install]`ntarget = $($env:PIP_TARGET)`n", (New-Object Text.UTF8Encoding($false)))
    $env:AC6_FLOW_ROOT = $fixture
    $env:AC6_FLOW_FAIL_READY = '0'
    $env:AC6_FLOW_FAIL_STAGE = ''
    $env:AC6_FLOW_HOLD = ''
    $env:AC6_FLOW_PYTHON = (& $PythonPath -c 'import sys;print(sys._base_executable)').Trim()
    $env:AC6_FLOW_VERSION = (& $PythonPath -c 'import platform;print(platform.python_version())').Trim()
    $env:AC6_FLOW_DESKTOP = Join-Path $fixture 'Desktop'
    $env:AC6_FLOW_ARCHIVE = Join-Path $fixture 'source.zip'
    $stage = Join-Path $fixture "ac6-winloss-tracker-$commit"
    New-Item -ItemType Directory -Path $stage | Out-Null
    $files = & git -C $root -c "safe.directory=$root" ls-files --cached --others --exclude-standard
    if ($LASTEXITCODE -ne 0) { throw 'Source enumeration failed' }
    foreach ($file in $files) {
        $destination = Join-Path $stage $file
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $root $file) -Destination $destination
    }
    # Only fixture namespacing changes: never compete with the user's Overlay/game.
    $overlayPath = Join-Path $stage 'game_overlay.py'
    $overlay = [IO.File]::ReadAllText($overlayPath).Replace('AC6StatsOverlayV22', ('AC6Flow-' + [guid]::NewGuid().ToString('N'))).Replace('armoredcore6.exe', 'ac6-flow-no-game.exe')
    [IO.File]::WriteAllText($overlayPath, $overlay, (New-Object System.Text.UTF8Encoding($false)))
    $dashboardPath = Join-Path $stage 'dashboard.py'
    [IO.File]::WriteAllText($dashboardPath, [IO.File]::ReadAllText($dashboardPath).Replace('AC6WinLossTrackerDashboard', ('AC6FlowDashboard-' + [guid]::NewGuid().ToString('N'))), (New-Object Text.UTF8Encoding($false)))
    Compress-Archive -LiteralPath $stage -DestinationPath $env:AC6_FLOW_ARCHIVE
    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback,0)
    $listener.Start(); $port = $listener.LocalEndpoint.Port; $listener.Stop()
    # Seed the schema version the source actually expects: a stale literal
    # would be migrated on startup and rewrite config.json.
    $configVersion = [int]([regex]::Match(
        [IO.File]::ReadAllText((Join-Path $root 'config_utils.py')),
        '(?m)^CONFIG_VERSION\s*=\s*(\d+)').Groups[1].Value)
    if ($configVersion -lt 1) { throw 'CONFIG_VERSION could not be read' }
    $config = @{config_version=$configVersion;port=$port;stats_enabled=$true;result_detector_enabled=$false;effect_screenshot_enabled=$true} | ConvertTo-Json
    [IO.File]::WriteAllText((Join-Path $data 'config.json'),$config,(New-Object System.Text.UTF8Encoding($false)))
    $script:configBefore = Get-Content (Join-Path $data 'config.json') -Raw
    $seed = @'
import os,sys
from pathlib import Path
sys.path.insert(0,os.environ['AC6_FLOW_SOURCE'])
from history_store import HistoryStore
h=HistoryStore(Path(os.environ['LOCALAPPDATA'])/'AC6WinLossTracker');h.start_session()
for i,result in enumerate(('win','win','win','loss')): h.record_result(str(i),result,'fixture',{'wins':min(i+1,3),'losses':int(i==3),'streak':0 if i==3 else i+1})
'@
    $env:AC6_FLOW_SOURCE = $stage
    & $PythonPath -c $seed
    if ($LASTEXITCODE -ne 0) { throw 'Data seed failed' }
    # Run the full production transaction. Substitute only release transport,
    # preselected test Python discovery, and the OS Desktop path. pip, COM .lnk,
    # stop/swap/rollback/start/readiness/metadata/cleanup remain production code.
    $overrides = @'

function Set-InstallStage {
    param([string]$Name)
    Set-InstallStageReal -Name $Name
    if ($env:AC6_FLOW_FAIL_STAGE -eq $Name) { throw "Injected stage failure: $Name" }
    if ($env:AC6_FLOW_HOLD -eq '1' -and $Name -eq 'venv-prepare') {
        [IO.File]::WriteAllText((Join-Path $env:AC6_FLOW_ROOT 'held'), 'ready')
        $deadline = [DateTime]::UtcNow.AddSeconds(40)
        while (-not (Test-Path (Join-Path $env:AC6_FLOW_ROOT 'release'))) {
            if ([DateTime]::UtcNow -gt $deadline) { throw 'Fixture barrier timed out' }
            Start-Sleep -Milliseconds 100
        }
    }
}
function Find-SupportedPython {
    return [PSCustomObject]@{PythonPath=$env:AC6_FLOW_PYTHON;PythonwPath=(Join-Path (Split-Path $env:AC6_FLOW_PYTHON) 'pythonw.exe');Version=$env:AC6_FLOW_VERSION;Role='fixture';SignatureStatus='fixture';SignerSubject='fixture';FreeThreaded=$false;Architecture='64-bit'}
}
function Wait-AppRuntimeReady {
    param([int]$TimeoutSeconds=15)
    $ready = Wait-AppRuntimeReadyReal -TimeoutSeconds $TimeoutSeconds
    if ($env:AC6_FLOW_FAIL_READY -eq '1') { return $false }
    return $ready
}
function Invoke-WebRequest {
    [CmdletBinding()]param($Uri,$OutFile,$Method,$Headers,[switch]$UseBasicParsing,$TimeoutSec)
    if ($Uri -in @('https://api.github.com/repos/TullysAC6/ac6-winloss-tracker/commits/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'https://api.github.com/repos/TullysAC6/ac6-winloss-tracker/commits/v1.2.0')) {
        return [PSCustomObject]@{ Content = '{"sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}' }
    }
    if ($Uri -eq 'https://github.com/TullysAC6/ac6-winloss-tracker/archive/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.zip') {
        Copy-Item -LiteralPath $env:AC6_FLOW_ARCHIVE -Destination $OutFile
        return
    }
    Microsoft.PowerShell.Utility\Invoke-WebRequest @PSBoundParameters
}
'@
    $fixtureMutex = 'AC6FlowInstaller-' + [guid]::NewGuid().ToString('N')
    $installer = [IO.File]::ReadAllText((Join-Path $root 'install.ps1')).Replace("`r`n","`n").Replace('AC6WinLossTrackerInstaller', $fixtureMutex)
    $installer = $installer.Replace('function Wait-AppRuntimeReady {','function Wait-AppRuntimeReadyReal {')
    $installer = $installer.Replace('function Set-InstallStage {','function Set-InstallStageReal {')
    $marker = "try {`n    if (`$env:OS"
    if (-not $installer.Contains($marker)) { throw 'Installer transaction marker changed' }
    $installer = $installer.Replace($marker, $overrides + "`n" + $marker).Replace("[Environment]::GetFolderPath('Desktop')", '$env:AC6_FLOW_DESKTOP')
    [IO.File]::WriteAllText((Join-Path $fixture 'install-fixture.ps1'),$installer,$utf8)
    $uninstaller = [IO.File]::ReadAllText((Join-Path $root 'uninstall.ps1')).Replace("[Environment]::GetFolderPath('Desktop')", '$env:AC6_FLOW_DESKTOP').Replace('AC6WinLossTrackerInstaller', $fixtureMutex)
    [IO.File]::WriteAllText((Join-Path $fixture 'uninstall-fixture.ps1'),$uninstaller,$utf8)
    $first = Run-Installer -Stable
    Check-Report
    Write-Output 'Fresh source install / preserved existing data: PASS'
    $overlayPid = (Get-Content (Join-Path $data '.overlay-runtime.json') -Raw | ConvertFrom-Json).pid
    # Repeat the supported launcher entry. Its status dialog stays open by design.
    $firstEnvironment = (Get-Content (Join-Path $data 'installed-version.json') -Raw | ConvertFrom-Json).environment_path
    $repeatLauncher = Start-FixturePython -Arguments ('"{0}"' -f (Join-Path $installed 'launcher.pyw'))
    Start-Sleep -Seconds 2
    $duplicate = Start-FixturePython -Arguments ('"{0}" --overlay' -f (Join-Path $installed 'app.py'))
    if (-not $duplicate.WaitForExit(10000)) { throw 'Duplicate Overlay did not exit' }
    if ((Read-Runtime).pid -ne $first.pid) { throw 'Duplicate server took ownership' }
    if ((Get-Content (Join-Path $data '.overlay-runtime.json') -Raw | ConvertFrom-Json).pid -ne $overlayPid) { throw 'Duplicate Overlay took ownership' }
    if (-not $repeatLauncher.HasExited) { $repeatLauncher.Kill(); [void]$repeatLauncher.WaitForExit(5000) }
    $repeatLauncher.Dispose()
    $duplicate.Dispose()
    $dashboard = Start-FixturePython -Arguments ('"{0}"' -f (Join-Path $installed 'dashboard.py'))
    Start-Sleep -Seconds 2
    if ($dashboard.HasExited) { throw 'Dashboard failed to start' }
    if ((Get-Content (Join-Path $data '.dashboard-runtime.json') -Raw | ConvertFrom-Json).pid -ne $dashboard.Id) { throw 'Dashboard handle is a redirector PID' }
    $second = Run-Installer
    if ((Get-Content (Join-Path $data 'installed-version.json') -Raw | ConvertFrom-Json).environment_path -ne $firstEnvironment) { throw 'Unchanged lock did not reuse the verified venv' }
    if ($second.pid -eq $first.pid -or (Get-Process -Id $first.pid,$overlayPid,$dashboard.Id -ErrorAction SilentlyContinue)) { throw 'Update retained old processes' }
    Write-Output 'Update with running Launcher/Server/Overlay/Dashboard, no manual exit: PASS'
    $dashboard.Dispose()
    foreach ($failure in @('python-verification','stop-running-app','source-install','shortcut','launch','metadata','commit')) { Check-Rollback $failure }
    # Change only the fixture lock comment, retaining every pinned wheel/hash.
    $lockPath = Join-Path $stage 'requirements.lock'
    [IO.File]::AppendAllText($lockPath, "`n# isolated changed-lock fixture`n", (New-Object Text.UTF8Encoding($false)))
    Compress-Archive -LiteralPath $stage -DestinationPath $env:AC6_FLOW_ARCHIVE -Force
    foreach ($failure in @('venv-create','pip-install','venv-verify','shortcut','metadata','commit')) { Check-Rollback $failure }
    $null = Run-Installer
    $changedEnvironment = (Get-Content (Join-Path $data 'installed-version.json') -Raw | ConvertFrom-Json).environment_path
    if ($changedEnvironment -eq $firstEnvironment -or (Test-Path $firstEnvironment)) { throw 'Changed-lock environment was not committed/pruned' }
    Write-Output 'Changed lock / new final-path environment / previous environment pruning: PASS'
    $cfgPath = Join-Path $changedEnvironment 'pyvenv.cfg'
    [IO.File]::WriteAllText($cfgPath, [IO.File]::ReadAllText($cfgPath).Replace('include-system-site-packages = false','include-system-site-packages = true'), (New-Object Text.UTF8Encoding($false)))
    $null = Run-Installer
    $repairedEnvironment = (Get-Content (Join-Path $data 'installed-version.json') -Raw | ConvertFrom-Json).environment_path
    if ($repairedEnvironment -eq $changedEnvironment -or (Test-Path $changedEnvironment)) { throw 'Unsafe environment was reused instead of repaired' }
    $null = Run-Installer
    if ((Get-Content (Join-Path $data 'installed-version.json') -Raw | ConvertFrom-Json).environment_path -ne $repairedEnvironment) { throw 'Verified repair environment was not reused' }
    Write-Output 'Contaminated environment rejected / repaired in final path / repaired environment reused: PASS'
    # Hold the real first installer after mutex acquisition. A competing one
    # must fail without even changing the installation log.
    $env:AC6_FLOW_HOLD = '1'
    $firstInstaller = Start-Process -FilePath $shellPath -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File "{0}" -SourceCommit {1}' -f (Join-Path $fixture 'install-fixture.ps1'), $commit) -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $fixture 'concurrent-first.log') -RedirectStandardError (Join-Path $fixture 'concurrent-first.err')
    # Start-Process releases its handle, so pin one before the process can
    # exit. Without it ExitCode reads back empty and a success looks failed.
    $null = $firstInstaller.Handle
    $env:AC6_FLOW_HOLD = ''
    try {
        $deadline = [DateTime]::UtcNow.AddSeconds(20)
        while (-not (Test-Path (Join-Path $fixture 'held'))) {
            if ($firstInstaller.HasExited -or [DateTime]::UtcNow -gt $deadline) { throw 'First installer did not reach barrier' }
            Start-Sleep -Milliseconds 100
        }
        $logHash = (Get-FileHash (Join-Path $data 'source-install.log')).Hash
        & $shellPath -NoProfile -ExecutionPolicy Bypass -File (Join-Path $fixture 'install-fixture.ps1') -SourceCommit $commit *> (Join-Path $fixture 'concurrent-second.log')
        if ($LASTEXITCODE -eq 0 -or (Get-FileHash (Join-Path $data 'source-install.log')).Hash -ne $logHash) { throw 'Concurrent installer did not fail before mutation' }
        [IO.File]::WriteAllText((Join-Path $fixture 'release'), 'go')
        # The released installer still runs a whole transaction, including
        # pruning the superseded environment. Bound it well above that work,
        # and never report a slow finish as a failed one.
        if (-not $firstInstaller.WaitForExit(180000)) { throw 'First installer did not finish after concurrent refusal' }
        if ($firstInstaller.ExitCode -ne 0) { throw "First installer failed after concurrent refusal (exit $($firstInstaller.ExitCode)): $(Get-Content (Join-Path $fixture 'concurrent-first.err') -Raw)" }
    } finally {
        if (-not $firstInstaller.HasExited) { $firstInstaller.Kill(); [void]$firstInstaller.WaitForExit(5000) }
        $firstInstaller.Dispose()
    }
    Write-Output 'Concurrent installer refused before mutation / mutex release: PASS'
    # Fail readiness after the new application starts, then exercise the real
    # rollback stop/restore/restart. No production transaction logic is replaced.
    $sentinel = Join-Path $installed 'rollback-sentinel.txt'
    Set-Content -LiteralPath $sentinel -Value 'previous-source'
    $shortcutHash = (Get-FileHash (Join-Path $env:AC6_FLOW_DESKTOP 'AC6 WinLoss Tracker.lnk')).Hash
    $env:AC6_FLOW_FAIL_READY = '1'
    & $shellPath -NoProfile -ExecutionPolicy Bypass -File (Join-Path $fixture 'install-fixture.ps1') -SourceCommit $commit *> (Join-Path $fixture 'rollback.log')
    if ($LASTEXITCODE -eq 0) { throw 'Injected readiness failure was accepted' }
    $env:AC6_FLOW_FAIL_READY = '0'
    if (-not (Test-Path $sentinel)) { throw 'Previous source not restored' }
    if ((Get-FileHash (Join-Path $env:AC6_FLOW_DESKTOP 'AC6 WinLoss Tracker.lnk')).Hash -ne $shortcutHash) { throw 'Shortcut not restored' }
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    $restored = $null
    do {
        try { $restored = Read-Runtime; $null = Invoke-RestMethod -Uri "http://127.0.0.1:$($restored.port)/health" } catch { $restored = $null }
        if ($restored) { break }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    if (-not $restored) { throw 'Rollback did not restart previous source' }
    Write-Output 'Injected update failure / source and shortcut rollback / restart: PASS'
    Stop-FixtureProcess (Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $restored.pid))
    $third = Run-Installer
    Write-Output 'Abnormal server exit / stale runtime recovery / update: PASS'
    Run-Uninstaller
    Check-Report
    Write-Output 'Diagnostic report while running and after stop/uninstall: PASS'
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw 'Port remains after uninstall' }
    Write-Output 'Running uninstall / source and shortcut removed / data retained: PASS'
    $null = Run-Installer
    Write-Output 'Reinstall / lifetime and config restored: PASS'
    Run-Uninstaller
    # Exercise the legacy source layout against the caller's existing shared
    # test interpreter (outside the application's owned root). Never pip into it.
    Copy-Item -LiteralPath $stage -Destination $legacy -Recurse
    $legacyPythonw = Join-Path (Split-Path $PythonPath) 'pythonw.exe'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path $env:AC6_FLOW_DESKTOP 'AC6 WinLoss Tracker.lnk'))
    $shortcut.TargetPath = $legacyPythonw
    $shortcut.Arguments = '"{0}"' -f (Join-Path $legacy 'launcher.pyw')
    $shortcut.WorkingDirectory = $legacy
    $shortcut.Save()
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shortcut)
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
    # CPython's base executable + launcher keeps ownership if the test
    # interpreter itself is a venv. CI's shared base interpreter needs no hint.
    $legacyInfo = New-Object Diagnostics.ProcessStartInfo
    $legacyInfo.FileName = Join-Path (Split-Path $env:AC6_FLOW_PYTHON) 'pythonw.exe'
    $legacyInfo.Arguments = '"{0}"' -f (Join-Path $legacy 'launcher.pyw')
    $legacyInfo.WorkingDirectory = $legacy
    $legacyInfo.UseShellExecute = $false
    $legacyInfo.CreateNoWindow = $true
    if (Test-Path (Join-Path (Split-Path (Split-Path $PythonPath)) 'pyvenv.cfg')) {
        $legacyInfo.EnvironmentVariables['__PYVENV_LAUNCHER__'] = $legacyPythonw
    }
    $legacyHandle = [Diagnostics.Process]::Start($legacyInfo)
    try {
        $null = Wait-Healthy
        $env:AC6_FLOW_FAIL_READY = '1'
        & $shellPath -NoProfile -ExecutionPolicy Bypass -File (Join-Path $fixture 'install-fixture.ps1') -SourceCommit $commit *> (Join-Path $fixture 'legacy-rollback.log')
        if ($LASTEXITCODE -eq 0) { throw 'Legacy readiness failure was accepted' }
        $env:AC6_FLOW_FAIL_READY = '0'
        if (-not (Test-Path $legacy) -or (Test-Path $installed)) { throw 'Legacy rollback source boundary failed' }
        $null = Wait-Healthy
        $null = Run-Installer
        if (Test-Path $legacy) { throw 'Legacy source not removed after commit' }
        Write-Output 'Running legacy shared test interpreter / rollback / migration / data preservation: PASS'
        Run-Uninstaller
        # Legacy-only uninstall is also a supported entry state.
        Copy-Item -LiteralPath $stage -Destination $legacy -Recurse
        Run-Uninstaller
    } finally {
        if (-not $legacyHandle.HasExited) { $legacyHandle.Kill(); [void]$legacyHandle.WaitForExit(5000) }
        $legacyHandle.Dispose()
    }
} catch {
    foreach ($log in @(@(Get-ChildItem -LiteralPath $fixture -File -Filter '*.err' -ErrorAction SilentlyContinue).FullName + (Join-Path $fixture 'last-install.log'), (Join-Path $data 'source-install.log'), (Join-Path $data 'startup.log'), (Join-Path $data 'dashboard.log'))) {
        if (Test-Path -LiteralPath $log) { Write-Host (Split-Path -Leaf $log); Get-Content -LiteralPath $log -Tail 18 | ForEach-Object { Write-Host $_ } }
    }
    throw
} finally {
    # All targets are verified descendants of this unique fixture directory.
    foreach ($p in @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -and ($_.CommandLine.Contains($installed) -or $_.CommandLine.Contains($legacy)) })) {
        Stop-FixtureProcess $p
    }
    $env:LOCALAPPDATA = $originalLocal
    $env:PYTHONUSERBASE = $originalUserBase
    $env:PIP_BREAK_SYSTEM_PACKAGES = $originalPipOverride
    foreach ($name in $fixtureVariables) { [Environment]::SetEnvironmentVariable($name,$oldVariables[$name]) }
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
}
