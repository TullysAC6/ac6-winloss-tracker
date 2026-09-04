[CmdletBinding()]
param([switch]$RemoveUserData)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$appName = 'AC6 WinLoss Tracker'
$dataPath = Join-Path $env:LOCALAPPDATA 'AC6WinLossTracker'
$installPath = Join-Path $env:LOCALAPPDATA 'Programs\AC6WinLossTrackerSource'
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'Programs\AC6WinLossTrackerRuntime'
$runtimePath = Join-Path $dataPath '.runtime.json'
$logPath = Join-Path $dataPath 'source-uninstall.log'
$runtimeFileNames = @(
    '.runtime.json',
    '.runtime.json.tmp',
    '.overlay-runtime.json',
    '.overlay-runtime.json.tmp',
    '.dashboard-runtime.json',
    '.dashboard-runtime.json.tmp'
)

function Write-UninstallLog {
    param([Parameter(Mandatory = $true)][string]$Message)
    if (-not (Test-Path -LiteralPath $dataPath -PathType Container)) {
        New-Item -ItemType Directory -Path $dataPath -Force | Out-Null
    }
    Add-Content -LiteralPath $logPath -Value ('{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message) -Encoding UTF8
}

function Get-TrackerRuntime {
    if (-not (Test-Path -LiteralPath $runtimePath -PathType Leaf)) { return $null }
    try {
        $runtime = Get-Content -LiteralPath $runtimePath -Raw -ErrorAction Stop | ConvertFrom-Json
        $runtimePid = [int]$runtime.pid
        $runtimePort = [int]$runtime.port
        $runtimeToken = [string]$runtime.token
        if ($runtimePid -le 0 -or $runtimePort -lt 1 -or $runtimePort -gt 65535 -or [string]::IsNullOrWhiteSpace($runtimeToken)) {
            return $null
        }
        return [PSCustomObject]@{ Pid = $runtimePid; Port = $runtimePort; Token = $runtimeToken }
    } catch {
        Write-UninstallLog "runtime inspection failed: $($_.Exception.Message)"
        return $null
    }
}

function Test-TrackerCommandLine {
    param([string]$Name, [string]$CommandLine)
    if ($Name -notmatch '(?i)^pythonw?\.exe$' -or [string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
    $entry = '(?i){0}[\\/](app\.py|launcher\.pyw|dashboard\.py)(?="|\s|$)' -f [Regex]::Escape($installPath)
    return $CommandLine -match $entry
}

function Get-TrackerProcesses {
    $found = @{}
    try {
        foreach ($processInfo in @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction Stop)) {
            if (Test-TrackerCommandLine -Name ([string]$processInfo.Name) -CommandLine ([string]$processInfo.CommandLine)) {
                $found[[int]$processInfo.ProcessId] = $processInfo
            }
        }
    } catch {
        Write-UninstallLog "Tracker process enumeration failed: $($_.Exception.Message)"
    }
    return @($found.Values)
}

function Test-TrackerEndpoint {
    param([int]$Port, [string]$Path)
    if ($Port -lt 1 -or $Port -gt 65535) { return $false }
    try {
        Invoke-WebRequest -Uri ("http://127.0.0.1:{0}{1}" -f $Port, $Path) -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop | Out-Null
        return $true
    } catch { return $false }
}

function Wait-TrackerStopped {
    param([int]$Port = 0, [int]$TimeoutSeconds = 12)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $processes = @(Get-TrackerProcesses)
        $health = Test-TrackerEndpoint -Port $Port -Path '/health'
        $stats = Test-TrackerEndpoint -Port $Port -Path '/stats'
        if ($processes.Count -eq 0 -and -not $health -and -not $stats) { return $true }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    return $false
}

function Stop-TrackerSafely {
    $runtime = Get-TrackerRuntime
    $port = if ($runtime) { [int]$runtime.Port } else { 0 }
    $ownedBefore = @(Get-TrackerProcesses)
    if ($runtime -and ($ownedBefore.ProcessId -contains [int]$runtime.Pid)) {
        try {
            $headers = @{ 'X-Control-Token' = [string]$runtime.Token }
            Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/api/system/shutdown" -f $port) -Method Post `
                -Headers $headers -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop | Out-Null
            Write-UninstallLog "authenticated graceful shutdown requested for Tracker PID $($runtime.Pid)"
        } catch {
            Write-UninstallLog "graceful shutdown unavailable: $($_.Exception.Message)"
        }
    }
    if (Wait-TrackerStopped -Port $port -TimeoutSeconds 8) { return $port }

    # Fallback is restricted to Python processes whose command line names an
    # entry point inside the exact Tracker installation directory.
    foreach ($processInfo in @(Get-TrackerProcesses)) {
        try {
            Stop-Process -Id ([int]$processInfo.ProcessId) -Force -ErrorAction Stop
            Write-UninstallLog "Tracker-only fallback stopped PID $($processInfo.ProcessId)"
        } catch {
            Write-UninstallLog "Tracker fallback stop failed for PID $($processInfo.ProcessId): $($_.Exception.Message)"
        }
    }
    if (-not (Wait-TrackerStopped -Port $port -TimeoutSeconds 5)) {
        throw 'Trackerを安全に停止できませんでした。アプリを閉じてから、もう一度実行してください。'
    }
    return $port
}

function Remove-TrackerRuntimeFiles {
    foreach ($name in $runtimeFileNames) {
        $path = Join-Path $dataPath $name
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    }
    # game_overlay.py writes this exact PID-qualified temporary naming form.
    foreach ($temporary in @(Get-ChildItem -LiteralPath $dataPath -Filter '.overlay-runtime.json.*.tmp' -File -Force -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath $temporary.FullName -Force
    }
}

function Confirm-UserDataRemoval {
    Write-Host ''
    Write-Host '警告: history.db、config.json、stats.json、diagnosticsを含む全ユーザーデータを完全削除します。' -ForegroundColor Red
    Write-Host '通常アンインストールでは、これらの戦績・設定データは保持されます。' -ForegroundColor Yellow
    try {
        $answer = Read-Host '完全削除する場合だけ YES と入力してください'
        return [string]$answer -ceq 'YES'
    } catch {
        Write-UninstallLog "user data confirmation unavailable: $($_.Exception.Message)"
        return $false
    }
}

function Remove-TrackerUserData {
    if (-not (Confirm-UserDataRemoval)) {
        Write-UninstallLog 'user data removal declined or confirmation unavailable'
        return $false
    }
    if (Test-Path -LiteralPath $dataPath -PathType Container) {
        Remove-Item -LiteralPath $dataPath -Recurse -Force
    }
    return $true
}

function Remove-TrackerShortcut {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $shortcut = Join-Path $desktop 'AC6 WinLoss Tracker.lnk'
    if (Test-Path -LiteralPath $shortcut -PathType Leaf) { Remove-Item -LiteralPath $shortcut -Force }
}

function Remove-TrackerSource {
    $expected = [System.IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs\AC6WinLossTrackerSource'))
    $actual = [System.IO.Path]::GetFullPath($installPath)
    if ($actual -ne $expected) { throw 'アプリのインストール先を安全に確認できないため、削除を中止しました。' }
    if (Test-Path -LiteralPath $actual -PathType Container) { Remove-Item -LiteralPath $actual -Recurse -Force }
    $previous = "$actual.previous"
    if (Test-Path -LiteralPath $previous -PathType Container) { Remove-Item -LiteralPath $previous -Recurse -Force }
}

function Remove-TrackerDedicatedRuntime {
    $expected = [System.IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs\AC6WinLossTrackerRuntime'))
    $actual = [System.IO.Path]::GetFullPath($runtimeRoot)
    if ($actual -ne $expected) { throw 'Tracker専用Python環境の場所を安全に確認できないため、削除を中止しました。' }
    if (Test-Path -LiteralPath $actual -PathType Container) {
        Remove-Item -LiteralPath $actual -Recurse -Force
        Write-UninstallLog 'dedicated Tracker Python environment removed'
    }
}

try {
    Write-Host "[$appName] アンインストールを開始します。" -ForegroundColor Cyan
    Write-UninstallLog "uninstall started; remove user data=$RemoveUserData"
    $stoppedPort = Stop-TrackerSafely
    Remove-TrackerRuntimeFiles
    Remove-TrackerShortcut
    Remove-TrackerSource
    Remove-TrackerDedicatedRuntime

    if ($RemoveUserData) {
        if (Remove-TrackerUserData) {
            Write-Host 'アプリ本体とユーザーデータを削除しました。' -ForegroundColor Green
        } else {
            Write-Host 'ユーザーデータの削除はキャンセルされました。アプリ本体のみ削除しました。' -ForegroundColor Yellow
        }
    } else {
        Write-UninstallLog 'user data preserved'
        Write-Host 'アンインストールが完了しました。戦績・設定・診断データは保持されています。' -ForegroundColor Green
    }
    Write-Host 'Pythonは削除していません。'
    exit 0
} catch {
    try { Write-UninstallLog "ERROR: $($_.Exception.Message)" } catch {}
    Write-Host "`nアンインストールを完了できませんでした。" -ForegroundColor Red
    Write-Host $_.Exception.Message
    exit 1
}
