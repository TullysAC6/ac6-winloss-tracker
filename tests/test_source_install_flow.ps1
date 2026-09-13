[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$PythonPath)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$shellPath = (Get-Process -Id $PID).Path
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ac6-source-flow-' + [guid]::NewGuid().ToString('N'))
$fixture = [IO.Path]::GetFullPath($fixture)
if (-not $fixture.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe fixture root' }
$originalLocal = $env:LOCALAPPDATA
$originalUserBase = $env:PYTHONUSERBASE
$originalPipOverride = $env:PIP_BREAK_SYSTEM_PACKAGES
$fixtureVariables = @('AC6_FLOW_ROOT','AC6_FLOW_PYTHON','AC6_FLOW_VERSION','AC6_FLOW_ARCHIVE','AC6_FLOW_DESKTOP','AC6_FLOW_SOURCE','AC6_FLOW_FAIL_READY')
$oldVariables = @{}
foreach ($name in $fixtureVariables) { $oldVariables[$name] = [Environment]::GetEnvironmentVariable($name) }
$commit = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
$installed = Join-Path $fixture 'Programs\AC6WinLossTrackerSource'
$data = Join-Path $fixture 'AC6WinLossTracker'
$utf8 = New-Object System.Text.UTF8Encoding($true)
function Assert-NoOwnedProcess {
    $owned = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($installed) -and $_.Name -match '^pythonw?\.exe$' })
    if ($owned.Count) { throw "Owned processes remain: $($owned.ProcessId -join ',')" }
}
function Read-Runtime { Get-Content -LiteralPath (Join-Path $data '.runtime.json') -Raw | ConvertFrom-Json }
function Run-Installer {
    & $shellPath -NoProfile -ExecutionPolicy Bypass -File (Join-Path $fixture 'install-fixture.ps1') *> (Join-Path $fixture 'last-install.log')
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
    return $r
}
function Run-Uninstaller {
    & $shellPath -NoProfile -ExecutionPolicy Bypass -File (Join-Path $fixture 'uninstall-fixture.ps1') *> (Join-Path $fixture 'last-uninstall.log')
    if ($LASTEXITCODE -ne 0) { Get-Content (Join-Path $fixture 'last-uninstall.log'); throw 'Uninstaller failed' }
    Assert-NoOwnedProcess
    if (Test-Path $installed) { throw 'Source remains' }
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
    # Test-only uv runtimes may be externally managed. The production pip call
    # uses --user, confined to the unique PYTHONUSERBASE above; never base Python.
    $env:PIP_BREAK_SYSTEM_PACKAGES = '1'
    $env:AC6_FLOW_ROOT = $fixture
    $env:AC6_FLOW_FAIL_READY = '0'
    $env:AC6_FLOW_PYTHON = [IO.Path]::GetFullPath($PythonPath)
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
function Resolve-StableCommit { return 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' }
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
    if ($Uri -eq 'https://github.com/TullysAC6/ac6-winloss-tracker/archive/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.zip') {
        Copy-Item -LiteralPath $env:AC6_FLOW_ARCHIVE -Destination $OutFile
        return
    }
    Microsoft.PowerShell.Utility\Invoke-WebRequest @PSBoundParameters
}
'@
    $installer = [IO.File]::ReadAllText((Join-Path $root 'install.ps1')).Replace("`r`n","`n")
    $installer = $installer.Replace('function Wait-AppRuntimeReady {','function Wait-AppRuntimeReadyReal {')
    $marker = "try {`n    if (`$env:OS"
    if (-not $installer.Contains($marker)) { throw 'Installer transaction marker changed' }
    $installer = $installer.Replace($marker, $overrides + "`n" + $marker).Replace("[Environment]::GetFolderPath('Desktop')", '$env:AC6_FLOW_DESKTOP')
    [IO.File]::WriteAllText((Join-Path $fixture 'install-fixture.ps1'),$installer,$utf8)
    $uninstaller = [IO.File]::ReadAllText((Join-Path $root 'uninstall.ps1')).Replace("[Environment]::GetFolderPath('Desktop')", '$env:AC6_FLOW_DESKTOP')
    [IO.File]::WriteAllText((Join-Path $fixture 'uninstall-fixture.ps1'),$uninstaller,$utf8)
    $first = Run-Installer
    Check-Report
    Write-Output 'Fresh source install / preserved existing data: PASS'
    $overlayPid = (Get-Content (Join-Path $data '.overlay-runtime.json') -Raw | ConvertFrom-Json).pid
    # Repeat the supported launcher entry. Its status dialog stays open by design.
    $repeatLauncher = Start-Process -FilePath (Join-Path (Split-Path $PythonPath) 'pythonw.exe') -ArgumentList ('"{0}"' -f (Join-Path $installed 'launcher.pyw')) -WorkingDirectory $installed -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 2
    $duplicate = Start-Process -FilePath (Join-Path (Split-Path $PythonPath) 'pythonw.exe') -ArgumentList ('"{0}" --overlay' -f (Join-Path $installed 'app.py')) -WorkingDirectory $installed -WindowStyle Hidden -PassThru
    if (-not $duplicate.WaitForExit(10000)) { throw 'Duplicate Overlay did not exit' }
    if ((Read-Runtime).pid -ne $first.pid) { throw 'Duplicate server took ownership' }
    if ((Get-Content (Join-Path $data '.overlay-runtime.json') -Raw | ConvertFrom-Json).pid -ne $overlayPid) { throw 'Duplicate Overlay took ownership' }
    Stop-Process -Id $repeatLauncher.Id -Force -ErrorAction SilentlyContinue
    $dashboard = Start-Process -FilePath (Join-Path (Split-Path $PythonPath) 'pythonw.exe') -ArgumentList ('"{0}"' -f (Join-Path $installed 'dashboard.py')) -WorkingDirectory $installed -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 2
    if ($dashboard.HasExited) { throw 'Dashboard failed to start' }
    $second = Run-Installer
    if ($second.pid -eq $first.pid -or (Get-Process -Id $first.pid,$overlayPid,$dashboard.Id -ErrorAction SilentlyContinue)) { throw 'Update retained old processes' }
    Write-Output 'Update with running Launcher/Server/Overlay/Dashboard, no manual exit: PASS'
    # Fail readiness after the new application starts, then exercise the real
    # rollback stop/restore/restart. No production transaction logic is replaced.
    $sentinel = Join-Path $installed 'rollback-sentinel.txt'
    Set-Content -LiteralPath $sentinel -Value 'previous-source'
    $shortcutHash = (Get-FileHash (Join-Path $env:AC6_FLOW_DESKTOP 'AC6 WinLoss Tracker.lnk')).Hash
    $env:AC6_FLOW_FAIL_READY = '1'
    & $shellPath -NoProfile -ExecutionPolicy Bypass -File (Join-Path $fixture 'install-fixture.ps1') *> (Join-Path $fixture 'rollback.log')
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
    Stop-Process -Id $restored.pid -Force
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
} finally {
    # All targets are verified descendants of this unique fixture directory.
    foreach ($p in @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -and $_.CommandLine.Contains($installed) })) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    $env:LOCALAPPDATA = $originalLocal
    $env:PYTHONUSERBASE = $originalUserBase
    $env:PIP_BREAK_SYSTEM_PACKAGES = $originalPipOverride
    foreach ($name in $fixtureVariables) { [Environment]::SetEnvironmentVariable($name,$oldVariables[$name]) }
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
}
