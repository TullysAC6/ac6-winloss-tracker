#Requires -Version 5.1
<#
.SYNOPSIS
    Read-only performance/process capture tool for AC6 Win/Loss Tracker RC validation.

.DESCRIPTION
    Records system performance counters and process metrics (system, AC6, and this
    tool's own tracker/overlay/worker-child processes) to CSV while the tester plays
    Armored Core VI. Observation only: it never modifies, kills, or configures the
    application under test. Press Ctrl+C to stop; a summary is generated on exit.

    Run it in its own PowerShell window and leave it open while playing:

        powershell -ExecutionPolicy Bypass -File .\tools\Start-RC-Performance-Capture.ps1

.PARAMETER IntervalSeconds
    Sampling interval for system/process performance counters. Default 1.

.PARAMETER DiscoveryIntervalSeconds
    How often to re-scan process command lines / parent-child relationships to find
    or re-find the tracker, overlay, dashboard, and worker-child processes. This is
    the only part of the script that uses CIM/WMI, kept deliberately infrequent.
    Default 5.

.PARAMETER LingerThresholdSeconds
    A worker-child process (WGC capture / screenshot-save, indistinguishable from the
    outside) still alive after this many seconds is flagged once in process-events.csv
    as a possible lingering child. Default 10.

.PARAMETER GpuIntervalSeconds
    How often to sample \GPU Engine(*)\Utilization Percentage. Measured at ~1.4-1.7s per
    call on real hardware (hundreds of wildcard counter instances to resolve), so it is
    decoupled from DiscoveryIntervalSeconds and kept infrequent by default. Set to 0 to
    disable GPU sampling entirely. Default 20.

.PARAMETER OutputRoot
    Base folder under which a new timestamped session folder is created. Defaults to
    the current user's Desktop, falling back to %TEMP% if Desktop is not writable.
#>
[CmdletBinding()]
param(
    [double]$IntervalSeconds = 1.0,
    [int]$DiscoveryIntervalSeconds = 5,
    [double]$LingerThresholdSeconds = 10.0,
    [int]$GpuIntervalSeconds = 20,
    [string]$OutputRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
$AppDataRoot = Join-Path $env:LOCALAPPDATA 'AC6WinLossTracker'
$RuntimeJsonPath = Join-Path $AppDataRoot '.runtime.json'
$OverlayRuntimeJsonPath = Join-Path $AppDataRoot '.overlay-runtime.json'
$DashboardRuntimeJsonPath = Join-Path $AppDataRoot '.dashboard-runtime.json'
$TrackerStartupLogPath = Join-Path $AppDataRoot 'startup.log'
$Ac6ProcessName = 'armoredcore6'

# --------------------------------------------------------------------------
# Output folder
# --------------------------------------------------------------------------
function Get-OutputDirectory {
    param([string]$RequestedRoot)

    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($RequestedRoot) { $candidates.Add($RequestedRoot) }
    try {
        $desktop = [Environment]::GetFolderPath('Desktop')
        if ($desktop) { $candidates.Add((Join-Path $desktop 'AC6-RC-Measurements')) }
    } catch { }
    $candidates.Add((Join-Path $env:TEMP 'AC6-RC-Measurements'))

    foreach ($base in $candidates) {
        try {
            $dir = Join-Path $base $stamp
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
            $probe = Join-Path $dir '.write-test.tmp'
            [System.IO.File]::WriteAllText($probe, 'ok')
            Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
            return (Resolve-Path -LiteralPath $dir).ProviderPath
        } catch {
            continue
        }
    }
    throw 'Could not create a writable output directory (tried Desktop and %TEMP%).'
}

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
function Get-Iso8601 {
    (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss.fffK')
}

function ConvertTo-CsvField {
    param($Value)
    if ($null -eq $Value) { return '' }
    $s = [string]$Value
    if ($s -match '[",\r\n]') {
        return '"' + ($s -replace '"', '""') + '"'
    }
    return $s
}

function Write-CsvRow {
    param(
        [System.IO.StreamWriter]$Writer,
        [object[]]$Fields
    )
    $line = ($Fields | ForEach-Object { ConvertTo-CsvField $_ }) -join ','
    $Writer.WriteLine($line)
}

function Format-Num {
    param($Value, [int]$Decimals = 2)
    if ($null -eq $Value) { return 'N/A' }
    return [math]::Round([double]$Value, $Decimals)
}

# --------------------------------------------------------------------------
# Power status (P/Invoke, no admin rights, no extra assemblies)
# --------------------------------------------------------------------------
$powerStatusTypeSource = @'
using System;
using System.Runtime.InteropServices;
public static class Ac6RcPower {
    [StructLayout(LayoutKind.Sequential)]
    public struct SYSTEM_POWER_STATUS {
        public byte ACLineStatus;
        public byte BatteryFlag;
        public byte BatteryLifePercent;
        public byte Reserved1;
        public int BatteryLifeTime;
        public int BatteryFullLifeTime;
    }
    [DllImport("kernel32.dll")]
    public static extern bool GetSystemPowerStatus(out SYSTEM_POWER_STATUS lpSystemPowerStatus);
}
'@
$script:PowerApiAvailable = $true
try {
    Add-Type -TypeDefinition $powerStatusTypeSource -ErrorAction Stop
} catch {
    $script:PowerApiAvailable = $false
}

function Get-PowerSample {
    if (-not $script:PowerApiAvailable) {
        return @{ Power = 'N/A'; Battery = 'N/A' }
    }
    try {
        $status = New-Object Ac6RcPower+SYSTEM_POWER_STATUS
        $ok = [Ac6RcPower]::GetSystemPowerStatus([ref]$status)
        if (-not $ok) { return @{ Power = 'N/A'; Battery = 'N/A' } }
        $power = switch ($status.ACLineStatus) {
            0 { 'Battery' }
            1 { 'AC' }
            default { 'Unknown' }
        }
        $battery = if ($status.BatteryLifePercent -le 100) { [string]$status.BatteryLifePercent } else { 'N/A' }
        return @{ Power = $power; Battery = $battery }
    } catch {
        return @{ Power = 'N/A'; Battery = 'N/A' }
    }
}

# --------------------------------------------------------------------------
# GPU (best-effort, standard Windows perf counters, disabled on first failure)
# --------------------------------------------------------------------------
$script:GpuSupported = $true
$script:GpuDisabledReason = $null

function Get-GpuSample {
    if (-not $script:GpuSupported) {
        return @{ Sum = 'N/A'; MaxEngine = 'N/A'; PerPid = @{} }
    }
    try {
        $result = Get-Counter -Counter '\GPU Engine(*)\Utilization Percentage' -ErrorAction Stop
        $sum = 0.0
        $max = 0.0
        $perPid = @{}
        foreach ($sample in $result.CounterSamples) {
            $val = [double]$sample.CookedValue
            if ($val -le 0) { continue }
            $sum += $val
            if ($val -gt $max) { $max = $val }
            if ($sample.InstanceName -match 'pid_(\d+)_') {
                $targetPid = $matches[1]
                if ($perPid.ContainsKey($targetPid)) { $perPid[$targetPid] += $val } else { $perPid[$targetPid] = $val }
            }
        }
        return @{ Sum = [math]::Round($sum, 2); MaxEngine = [math]::Round($max, 2); PerPid = $perPid }
    } catch {
        $script:GpuSupported = $false
        $script:GpuDisabledReason = $_.Exception.Message
        return @{ Sum = 'N/A'; MaxEngine = 'N/A'; PerPid = @{} }
    }
}

# --------------------------------------------------------------------------
# CPU delta tracking (Task-Manager-meaningful %, not the raw Get-Process snapshot)
# --------------------------------------------------------------------------
$script:ProcPrev = @{}
$script:LogicalProcessorCount = [Environment]::ProcessorCount

function Get-ProcessCpuSample {
    param([System.Diagnostics.Process]$Proc, [double]$NowMs)

    $key = [string]$Proc.Id
    $cpuMs = $null
    try { $cpuMs = $Proc.TotalProcessorTime.TotalMilliseconds } catch { }

    $oneCorePct = $null
    $systemPct = $null
    if ($null -ne $cpuMs) {
        if ($script:ProcPrev.ContainsKey($key)) {
            $prev = $script:ProcPrev[$key]
            $deltaCpu = $cpuMs - $prev.CpuMs
            $deltaWall = $NowMs - $prev.WallMs
            if ($deltaWall -gt 0 -and $deltaCpu -ge 0) {
                $oneCorePct = [math]::Round(($deltaCpu / $deltaWall) * 100.0, 2)
                $systemPct = [math]::Round($oneCorePct / $script:LogicalProcessorCount, 2)
            }
        }
        $script:ProcPrev[$key] = @{ CpuMs = $cpuMs; WallMs = $NowMs }
    }
    return @{ OneCorePct = $oneCorePct; SystemPct = $systemPct; CpuTimeSeconds = if ($cpuMs) { [math]::Round($cpuMs / 1000.0, 3) } else { $null } }
}

function Remove-ProcessCpuTracking {
    param([string]$PidKey)
    if ($script:ProcPrev.ContainsKey($PidKey)) { $script:ProcPrev.Remove($PidKey) }
}

# --------------------------------------------------------------------------
# Aggregation for summary.txt
# --------------------------------------------------------------------------
$script:Agg = @{}
function Update-Agg {
    param([string]$Key, $CpuOneCore, $CpuSystem, $WorkingSetMB, $PrivateMB, $TargetPid)

    if (-not $script:Agg.ContainsKey($Key)) {
        $script:Agg[$Key] = [ordered]@{
            SampleCount     = 0
            SumCpuSystem    = 0.0
            PeakCpuSystem   = 0.0
            SumCpuOneCore   = 0.0
            PeakCpuOneCore  = 0.0
            PeakWorkingSetMB = 0.0
            PeakPrivateMB   = 0.0
            Pids            = New-Object System.Collections.Generic.HashSet[string]
        }
    }
    $a = $script:Agg[$Key]
    if ($null -ne $CpuSystem) {
        $a.SampleCount++
        $a.SumCpuSystem += $CpuSystem
        if ($CpuSystem -gt $a.PeakCpuSystem) { $a.PeakCpuSystem = $CpuSystem }
    }
    if ($null -ne $CpuOneCore) {
        $a.SumCpuOneCore += $CpuOneCore
        if ($CpuOneCore -gt $a.PeakCpuOneCore) { $a.PeakCpuOneCore = $CpuOneCore }
    }
    if ($null -ne $WorkingSetMB -and $WorkingSetMB -gt $a.PeakWorkingSetMB) { $a.PeakWorkingSetMB = $WorkingSetMB }
    if ($null -ne $PrivateMB -and $PrivateMB -gt $a.PeakPrivateMB) { $a.PeakPrivateMB = $PrivateMB }
    if ($TargetPid) { [void]$a.Pids.Add([string]$TargetPid) }
}

# --------------------------------------------------------------------------
# Process/role discovery (low-frequency; the only CIM/WMI usage in the loop)
# --------------------------------------------------------------------------
$script:KnownRoles = @{
    tracker   = $null   # pid (int) or $null
    overlay   = $null
    dashboard = $null
    launcher  = $null
}
$script:WorkerChildren = @{}     # pid -> @{ FirstSeenAt = [datetime]; LingerLogged = $false }
$script:PpidCache = @{}          # pid -> ppid
$script:Ac6Pids = New-Object System.Collections.Generic.HashSet[string]
$script:DuplicateTrackerObserved = $false
$script:DuplicateOverlayObserved = $false
$script:MultipleCaptureChildObserved = $false
$script:OrphanLikeChildObserved = $false

function Read-RuntimeJsonPid {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        $raw = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        if ($raw.pid -is [int] -or $raw.pid -is [long] -or $raw.pid -is [double]) {
            $targetPid = [int]$raw.pid
            if ($targetPid -gt 0 -and (Get-Process -Id $targetPid -ErrorAction SilentlyContinue)) { return $targetPid }
        }
    } catch { }
    return $null
}

function Write-ProcessEvent {
    param([string]$EventType, [string]$Role, $TargetPid, $PPid, [string]$Detail)
    Write-CsvRow -Writer $script:EventsWriter -Fields @(
        (Get-Iso8601), $EventType, $Role, $TargetPid, $PPid, $Detail
    )
    $script:EventsWriter.Flush()
}

function Invoke-RoleDiscovery {
    # Snapshot before this tick's updates: a child's ParentProcessId still points at the
    # tracker PID even after the tracker itself has died, so child reconciliation below
    # must key off the tracker PID that was valid a moment ago, not the possibly-just-nulled
    # current one (otherwise every worker child would look like it vanished the instant the
    # tracker does, and the orphan-like check below would never see them).
    $lastKnownTrackerPid = $script:KnownRoles.tracker

    # Runtime-json-backed roles: exact PIDs published by the app itself (read-only).
    $newTracker = Read-RuntimeJsonPid -Path $RuntimeJsonPath
    $newOverlay = Read-RuntimeJsonPid -Path $OverlayRuntimeJsonPath
    $newDashboard = Read-RuntimeJsonPid -Path $DashboardRuntimeJsonPath

    foreach ($pair in @(
            @{ Role = 'tracker'; New = $newTracker },
            @{ Role = 'overlay'; New = $newOverlay },
            @{ Role = 'dashboard'; New = $newDashboard }
        )) {
        $role = $pair.Role
        $old = $script:KnownRoles[$role]
        $new = $pair.New
        if ($new -and -not $old) {
            Write-ProcessEvent -EventType 'process discovered' -Role $role -TargetPid $new -PPid '' -Detail 'resolved from runtime json'
        } elseif ($new -and $old -and $new -ne $old) {
            Write-ProcessEvent -EventType 'PID changed' -Role $role -TargetPid $new -PPid '' -Detail "previous pid $old"
            Remove-ProcessCpuTracking -PidKey ([string]$old)
        } elseif (-not $new -and $old) {
            Write-ProcessEvent -EventType 'process disappeared' -Role $role -TargetPid $old -PPid '' -Detail ''
            Remove-ProcessCpuTracking -PidKey ([string]$old)
        }
        $script:KnownRoles[$role] = $new
    }

    # Low-frequency CIM scan: command-line based classification + parent/child map.
    # Mirrors the same read-only Win32_Process pattern already used by install.ps1.
    try {
        $procs = Get-CimInstance -ClassName Win32_Process `
            -Filter "Name='python.exe' OR Name='pythonw.exe' OR Name='$Ac6ProcessName.exe'" `
            -ErrorAction Stop
    } catch {
        $procs = @()
    }

    $trackerLikePids = @()
    $overlayLikePids = @()
    $launcherPid = $null

    foreach ($p in $procs) {
        $cmd = [string]$p.CommandLine
        $targetPid = [int]$p.ProcessId
        $ppid = [int]$p.ParentProcessId
        $script:PpidCache[[string]$targetPid] = $ppid

        if ($p.Name -eq "$Ac6ProcessName.exe") {
            # AC6 discovered/disappeared events and set membership are owned by the main
            # loop's own Get-Process check below; this scan only contributes its ppid.
            continue
        }
        if (-not $cmd) { continue }
        if ($cmd -match 'launcher\.pyw') {
            $launcherPid = $targetPid
        } elseif ($cmd -match 'dashboard\.py') {
            # already resolved precisely via .dashboard-runtime.json; nothing to add
        } elseif ($cmd -match 'app\.py') {
            if ($cmd -match '--overlay') { $overlayLikePids += $targetPid } else { $trackerLikePids += $targetPid }
        }
    }

    if ($launcherPid -and -not $script:KnownRoles.launcher) {
        Write-ProcessEvent -EventType 'process discovered' -Role 'launcher' -TargetPid $launcherPid -PPid '' -Detail 'resolved from command line'
    } elseif (-not $launcherPid -and $script:KnownRoles.launcher) {
        Write-ProcessEvent -EventType 'process disappeared' -Role 'launcher' -TargetPid $script:KnownRoles.launcher -PPid '' -Detail ''
        Remove-ProcessCpuTracking -PidKey ([string]$script:KnownRoles.launcher)
    }
    $script:KnownRoles.launcher = $launcherPid

    if ($trackerLikePids.Count -gt 1) {
        $script:DuplicateTrackerObserved = $true
        Write-ProcessEvent -EventType 'duplicate Tracker role detected' -Role 'tracker' -TargetPid ($trackerLikePids -join ';') -PPid '' -Detail 'multiple app.py (non-overlay) processes observed'
    }
    if ($overlayLikePids.Count -gt 1) {
        $script:DuplicateOverlayObserved = $true
        Write-ProcessEvent -EventType 'duplicate Overlay detected' -Role 'overlay' -TargetPid ($overlayLikePids -join ';') -PPid '' -Detail 'multiple app.py --overlay processes observed'
    }

    # Worker children: direct children of the tracker PID that are not tracker/overlay/dashboard/launcher.
    $classifiedPids = New-Object System.Collections.Generic.HashSet[string]
    foreach ($r in 'tracker', 'overlay', 'dashboard', 'launcher') {
        if ($script:KnownRoles[$r]) { [void]$classifiedPids.Add([string]$script:KnownRoles[$r]) }
    }
    $trackerPid = $script:KnownRoles.tracker
    $currentChildren = New-Object System.Collections.Generic.HashSet[string]
    if ($lastKnownTrackerPid) {
        foreach ($p in $procs) {
            $targetPid = [int]$p.ProcessId
            $ppid = [int]$p.ParentProcessId
            if ($ppid -eq $lastKnownTrackerPid -and -not $classifiedPids.Contains([string]$targetPid)) {
                [void]$currentChildren.Add([string]$targetPid)
                if (-not $script:WorkerChildren.ContainsKey([string]$targetPid)) {
                    $script:WorkerChildren[[string]$targetPid] = @{ FirstSeenAt = Get-Date; LingerLogged = $false }
                    Write-ProcessEvent -EventType 'process discovered' -Role 'worker-child' -TargetPid $targetPid -PPid $ppid -Detail 'capture/screenshot child, role not distinguishable from outside'
                }
            }
        }
    }
    # Detect children that disappeared since the last scan.
    $goneKeys = @($script:WorkerChildren.Keys | Where-Object { -not $currentChildren.Contains($_) })
    foreach ($k in $goneKeys) {
        Write-ProcessEvent -EventType 'process disappeared' -Role 'worker-child' -TargetPid $k -PPid '' -Detail ''
        Remove-ProcessCpuTracking -PidKey $k
        $script:WorkerChildren.Remove($k)
    }
    if ($currentChildren.Count -gt 1) {
        $script:MultipleCaptureChildObserved = $true
        Write-ProcessEvent -EventType 'multiple capture children detected' -Role 'worker-child' -TargetPid ($currentChildren -join ';') -PPid $lastKnownTrackerPid -Detail "$($currentChildren.Count) concurrent worker children"
    }

    # Orphan-like: a worker child still alive while tracker itself has disappeared.
    if (-not $trackerPid -and $script:WorkerChildren.Count -gt 0) {
        foreach ($k in $script:WorkerChildren.Keys) {
            if (Get-Process -Id ([int]$k) -ErrorAction SilentlyContinue) {
                $script:OrphanLikeChildObserved = $true
                Write-ProcessEvent -EventType 'orphan-like child observed' -Role 'worker-child' -TargetPid $k -PPid '' -Detail 'tracker process has disappeared while this child is still alive'
            }
        }
    }

    # Lingering child heuristic (log once per pid).
    foreach ($k in $script:WorkerChildren.Keys) {
        $info = $script:WorkerChildren[$k]
        if (-not $info.LingerLogged) {
            $age = (New-TimeSpan -Start $info.FirstSeenAt -End (Get-Date)).TotalSeconds
            if ($age -ge $LingerThresholdSeconds) {
                $info.LingerLogged = $true
                Write-ProcessEvent -EventType 'screenshot/save child possibly lingering' -Role 'worker-child' -TargetPid $k -PPid '' -Detail "alive for ${age}s (capture-vs-screenshot not distinguishable from outside)"
            }
        }
    }
}

# --------------------------------------------------------------------------
# Per-process sample -> processes.csv row + aggregation
# --------------------------------------------------------------------------
function Write-ProcessSample {
    param([string]$Role, [System.Diagnostics.Process]$Proc, [double]$NowMs, [hashtable]$GpuPerPid)

    $targetPid = $Proc.Id
    $cpu = Get-ProcessCpuSample -Proc $Proc -NowMs $NowMs

    $workingSetMB = $null
    try { $workingSetMB = [math]::Round($Proc.WorkingSet64 / 1MB, 2) } catch { }
    $privateMB = $null
    try { $privateMB = [math]::Round($Proc.PrivateMemorySize64 / 1MB, 2) } catch { }
    $threads = $null
    try { $threads = $Proc.Threads.Count } catch { }
    $handles = $null
    try { $handles = $Proc.HandleCount } catch { }
    $startTime = 'N/A'
    try { $startTime = $Proc.StartTime.ToString('yyyy-MM-ddTHH:mm:ss.fffK') } catch { $startTime = 'N/A' }
    $ppid = if ($script:PpidCache.ContainsKey([string]$targetPid)) { $script:PpidCache[[string]$targetPid] } else { '' }
    $gpuPct = 'N/A'
    if ($GpuPerPid -and $GpuPerPid.ContainsKey([string]$targetPid)) {
        $gpuPct = [math]::Round($GpuPerPid[[string]$targetPid], 2)
    } elseif ($script:GpuSupported) {
        $gpuPct = 0
    }

    Write-CsvRow -Writer $script:ProcessesWriter -Fields @(
        (Get-Iso8601), $Role, $targetPid, $ppid,
        $(if ($cpu.OneCorePct -ne $null) { $cpu.OneCorePct } else { 'N/A' }),
        $(if ($cpu.SystemPct -ne $null) { $cpu.SystemPct } else { 'N/A' }),
        $(if ($cpu.CpuTimeSeconds -ne $null) { $cpu.CpuTimeSeconds } else { 'N/A' }),
        $(if ($workingSetMB -ne $null) { $workingSetMB } else { 'N/A' }),
        $(if ($privateMB -ne $null) { $privateMB } else { 'N/A' }),
        $(if ($threads -ne $null) { $threads } else { 'N/A' }),
        $(if ($handles -ne $null) { $handles } else { 'N/A' }),
        $gpuPct, $startTime, 'running'
    )

    Update-Agg -Key $Role -CpuOneCore $cpu.OneCorePct -CpuSystem $cpu.SystemPct -WorkingSetMB $workingSetMB -PrivateMB $privateMB -TargetPid $targetPid
}

# ==========================================================================
# Setup
# ==========================================================================
$OutDir = Get-OutputDirectory -RequestedRoot $OutputRoot
$SessionStart = Get-Date
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

Write-Host '========================================'
Write-Host 'AC6 RC Performance Capture'
Write-Host '========================================'
Write-Host "Recording started: $(Get-Iso8601)"
Write-Host "Output: $OutDir"
Write-Host ''
Write-Host 'Start AC6 / Tracker and play normally.'
Write-Host ''
Write-Host 'Press Ctrl+C to stop recording and generate summary.'
Write-Host 'Press M (no Enter needed) at any time to drop a timestamped marker.'
Write-Host '========================================'

$SystemCsvPath = Join-Path $OutDir 'system.csv'
$ProcessesCsvPath = Join-Path $OutDir 'processes.csv'
$EventsCsvPath = Join-Path $OutDir 'process-events.csv'
$MarkersCsvPath = Join-Path $OutDir 'markers.csv'
$SessionInfoPath = Join-Path $OutDir 'session-info.txt'
$SummaryPath = Join-Path $OutDir 'summary.txt'
$TrackerLogTailPath = Join-Path $OutDir 'tracker-log-tail.txt'

$Utf8Bom = New-Object System.Text.UTF8Encoding($true)
$script:SystemWriter = New-Object System.IO.StreamWriter($SystemCsvPath, $false, $Utf8Bom)
$script:ProcessesWriter = New-Object System.IO.StreamWriter($ProcessesCsvPath, $false, $Utf8Bom)
$script:EventsWriter = New-Object System.IO.StreamWriter($EventsCsvPath, $false, $Utf8Bom)
$script:MarkersWriter = New-Object System.IO.StreamWriter($MarkersCsvPath, $false, $Utf8Bom)

Write-CsvRow -Writer $script:SystemWriter -Fields @(
    'timestamp', 'elapsed_seconds', 'cpu_system_pct', 'available_memory_mb', 'committed_memory_mb',
    'total_memory_mb', 'memory_usage_pct', 'logical_processor_count', 'power_status', 'battery_percent',
    'gpu_utilization_sum_pct', 'gpu_utilization_max_engine_pct',
    'sampler_cpu_one_core_pct', 'sampler_cpu_system_pct', 'sampler_working_set_mb', 'sample_gap_ms'
)
Write-CsvRow -Writer $script:ProcessesWriter -Fields @(
    'timestamp', 'role', 'pid', 'ppid', 'cpu_one_core_pct', 'cpu_system_normalized_pct',
    'cpu_time_seconds', 'working_set_mb', 'private_memory_mb', 'thread_count', 'handle_count',
    'gpu_pct', 'start_time', 'status'
)
Write-CsvRow -Writer $script:EventsWriter -Fields @('timestamp', 'event_type', 'role', 'pid', 'ppid', 'detail')
Write-CsvRow -Writer $script:MarkersWriter -Fields @('timestamp', 'label')
$script:SystemWriter.Flush(); $script:ProcessesWriter.Flush(); $script:EventsWriter.Flush(); $script:MarkersWriter.Flush()

# session-info.txt (static, one-time)
$totalMemoryBytes = $null
try { $totalMemoryBytes = (Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop).TotalPhysicalMemory } catch { }
$osInfo = $null
try { $osInfo = (Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop) } catch { }
$psVersionText = $PSVersionTable.PSVersion.ToString()

$sessionInfoLines = @(
    'AC6 RC Performance Capture - session info',
    "Script: tools\Start-RC-Performance-Capture.ps1",
    "Started: $(Get-Iso8601)",
    "Output folder: $OutDir",
    "PowerShell version: $psVersionText  (edition: $($PSVersionTable.PSEdition))",
    "OS: $(if ($osInfo) { $osInfo.Caption } else { 'N/A' })",
    "Logical processor count: $($script:LogicalProcessorCount)",
    "Total physical memory (MB): $(if ($totalMemoryBytes) { [math]::Round($totalMemoryBytes/1MB,2) } else { 'N/A' })",
    "Sampling interval (seconds): $IntervalSeconds",
    "Discovery interval (seconds): $DiscoveryIntervalSeconds",
    "GPU sampling interval (seconds): $(if ($GpuIntervalSeconds -gt 0) { $GpuIntervalSeconds } else { 'disabled' })",
    "Lingering-child threshold (seconds): $LingerThresholdSeconds",
    "Tracker data root: $AppDataRoot",
    ''
)
[System.IO.File]::WriteAllLines($SessionInfoPath, $sessionInfoLines, $Utf8Bom)

# Tracker log tail: remember the current size so we can copy only what gets appended.
$script:TrackerLogStartOffset = 0
$script:TrackerLogAvailable = $false
try {
    if (Test-Path -LiteralPath $TrackerStartupLogPath) {
        $script:TrackerLogStartOffset = (Get-Item -LiteralPath $TrackerStartupLogPath).Length
        $script:TrackerLogAvailable = $true
    }
} catch { }

# CPU perf counter warm-up (one-time, outside the main loop).
$script:CpuCounter = $null
try {
    $script:CpuCounter = New-Object System.Diagnostics.PerformanceCounter('Processor', '% Processor Time', '_Total')
    [void]$script:CpuCounter.NextValue()
    Start-Sleep -Milliseconds 200
} catch {
    $script:CpuCounter = $null
}
$script:AvailMemCounter = $null
$script:CommitMemCounter = $null
try { $script:AvailMemCounter = New-Object System.Diagnostics.PerformanceCounter('Memory', 'Available MBytes') } catch { }
try { $script:CommitMemCounter = New-Object System.Diagnostics.PerformanceCounter('Memory', 'Committed Bytes') } catch { }

$script:SelfProcess = Get-Process -Id $PID
[void](Get-ProcessCpuSample -Proc $script:SelfProcess -NowMs $Stopwatch.Elapsed.TotalMilliseconds)

# GPU counter warm-up (one-time, outside the main loop; first PDH wildcard resolution is slow).
$script:LastGpuSample = @{ Sum = 'N/A'; MaxEngine = 'N/A'; PerPid = @{} }
if ($GpuIntervalSeconds -gt 0) {
    try { $script:LastGpuSample = Get-GpuSample } catch { }
} else {
    $script:GpuSupported = $false
    $script:GpuDisabledReason = 'disabled via -GpuIntervalSeconds 0'
}

# Marker key support (best-effort; disabled silently if console input is unavailable).
$script:MarkerSupported = $true
try { [void][Console]::KeyAvailable } catch { $script:MarkerSupported = $false }
$script:MarkerCount = 0

# ==========================================================================
# Main loop
# ==========================================================================
# Ctrl+C is handled by letting PowerShell's own pipeline-stop unwind through the
# try/finally below (the standard, reliable mechanism for a .ps1 host). A custom
# [Console]::CancelKeyPress handler was tried and rejected: invoking a PowerShell
# scriptblock from the CLR's native console-control callback thread is unsafe here
# and was observed to hard-kill the process before cleanup could run.

$script:Errors = New-Object System.Collections.Generic.List[string]
if (-not $script:PowerApiAvailable) { $script:Errors.Add('Power status API unavailable; power_status/battery_percent are N/A.') }

$tick = 0
$lastTickMs = $Stopwatch.Elapsed.TotalMilliseconds
$maxGapMs = 0.0
$sampleCount = 0

try {
    while ($true) {
        $tickStartMs = $Stopwatch.Elapsed.TotalMilliseconds
        $gapMs = $tickStartMs - $lastTickMs
        if ($sampleCount -gt 0 -and $gapMs -gt $maxGapMs) { $maxGapMs = $gapMs }
        $lastTickMs = $tickStartMs
        $sampleCount++

        if ($tick % $DiscoveryIntervalSeconds -eq 0) {
            try { Invoke-RoleDiscovery } catch { $script:Errors.Add("Role discovery error: $($_.Exception.Message)") }
        }
        # GPU wildcard-instance counters are expensive to resolve (measured ~1.4-1.7s on real
        # hardware with hundreds of instances), so they run on their own, much slower cadence
        # rather than every 1s tick or even every discovery tick.
        if ($GpuIntervalSeconds -gt 0 -and $tick % $GpuIntervalSeconds -eq 0) {
            try { $script:LastGpuSample = Get-GpuSample } catch { }
        }
        $gpu = $script:LastGpuSample

        # ---- System sample ----
        $cpuPct = 'N/A'
        if ($script:CpuCounter) { try { $cpuPct = [math]::Round($script:CpuCounter.NextValue(), 2) } catch { $cpuPct = 'N/A' } }
        $availMb = 'N/A'
        if ($script:AvailMemCounter) { try { $availMb = [math]::Round($script:AvailMemCounter.NextValue(), 2) } catch { } }
        $commitMb = 'N/A'
        if ($script:CommitMemCounter) { try { $commitMb = [math]::Round($script:CommitMemCounter.NextValue() / 1MB, 2) } catch { } }
        $totalMb = if ($totalMemoryBytes) { [math]::Round($totalMemoryBytes / 1MB, 2) } else { 'N/A' }
        $memUsagePct = 'N/A'
        if ($totalMemoryBytes -and $availMb -ne 'N/A') {
            $memUsagePct = [math]::Round((1.0 - (($availMb * 1MB) / $totalMemoryBytes)) * 100.0, 2)
        }
        $power = Get-PowerSample

        $selfCpu = $null
        try {
            $script:SelfProcess.Refresh()
            $selfCpu = Get-ProcessCpuSample -Proc $script:SelfProcess -NowMs $tickStartMs
        } catch { }
        $selfWorkingSetMb = 'N/A'
        try { $selfWorkingSetMb = [math]::Round($script:SelfProcess.WorkingSet64 / 1MB, 2) } catch { }

        Write-CsvRow -Writer $script:SystemWriter -Fields @(
            (Get-Iso8601), (Format-Num ($tickStartMs / 1000.0) 1), $cpuPct, $availMb, $commitMb, $totalMb, $memUsagePct,
            $script:LogicalProcessorCount, $power.Power, $power.Battery,
            $gpu.Sum, $gpu.MaxEngine,
            $(if ($selfCpu -and $selfCpu.OneCorePct -ne $null) { $selfCpu.OneCorePct } else { 'N/A' }),
            $(if ($selfCpu -and $selfCpu.SystemPct -ne $null) { $selfCpu.SystemPct } else { 'N/A' }),
            $selfWorkingSetMb,
            (Format-Num $gapMs 1)
        )
        if ($cpuPct -ne 'N/A') { Update-Agg -Key 'system' -CpuOneCore $null -CpuSystem $cpuPct -WorkingSetMB $null -PrivateMB $null -TargetPid $null }
        if ($availMb -ne 'N/A') {
            if (-not $script:Agg.ContainsKey('system-mem')) { $script:Agg['system-mem'] = [ordered]@{ MinAvailMb = [double]::MaxValue } }
            if ($availMb -lt $script:Agg['system-mem'].MinAvailMb) { $script:Agg['system-mem'].MinAvailMb = $availMb }
        }
        if ($selfCpu -and $selfCpu.OneCorePct -ne $null) {
            Update-Agg -Key 'sampler' -CpuOneCore $selfCpu.OneCorePct -CpuSystem $selfCpu.SystemPct -WorkingSetMB $selfWorkingSetMb -PrivateMB $null -TargetPid $PID
        }

        # ---- Tracked processes ----
        foreach ($role in 'tracker', 'overlay', 'dashboard', 'launcher') {
            $rpid = $script:KnownRoles[$role]
            if (-not $rpid) { continue }
            $proc = $null
            try { $proc = Get-Process -Id $rpid -ErrorAction Stop } catch { $proc = $null }
            if (-not $proc) { continue }
            Write-ProcessSample -Role $role -Proc $proc -NowMs $tickStartMs -GpuPerPid $gpu.PerPid
        }
        foreach ($k in @($script:WorkerChildren.Keys)) {
            $proc = $null
            try { $proc = Get-Process -Id ([int]$k) -ErrorAction Stop } catch { $proc = $null }
            if (-not $proc) { continue }
            Write-ProcessSample -Role 'worker-child' -Proc $proc -NowMs $tickStartMs -GpuPerPid $gpu.PerPid
        }
        $ac6Procs = @()
        try { $ac6Procs = @(Get-Process -Name $Ac6ProcessName -ErrorAction SilentlyContinue) } catch { $ac6Procs = @() }
        $currentAc6Keys = New-Object System.Collections.Generic.HashSet[string]
        foreach ($proc in $ac6Procs) {
            [void]$currentAc6Keys.Add([string]$proc.Id)
            if (-not $script:Ac6Pids.Contains([string]$proc.Id)) {
                [void]$script:Ac6Pids.Add([string]$proc.Id)
                Write-ProcessEvent -EventType 'process discovered' -Role 'ac6' -TargetPid $proc.Id -PPid '' -Detail "$Ac6ProcessName.exe"
            }
            Write-ProcessSample -Role 'ac6' -Proc $proc -NowMs $tickStartMs -GpuPerPid $gpu.PerPid
        }
        foreach ($k in @($script:Ac6Pids)) {
            if (-not $currentAc6Keys.Contains($k) -and (Get-Process -Id ([int]$k) -ErrorAction SilentlyContinue) -eq $null) {
                Write-ProcessEvent -EventType 'process disappeared' -Role 'ac6' -TargetPid $k -PPid '' -Detail ''
                Remove-ProcessCpuTracking -PidKey $k
                [void]$script:Ac6Pids.Remove($k)
            }
        }

        $script:SystemWriter.Flush()
        $script:ProcessesWriter.Flush()

        $tick++

        # ---- Sleep until next tick, polling for the marker key in short slices ----
        # (Ctrl+C during Start-Sleep unwinds straight through both loops into the
        # outer try/finally; nothing here needs to detect it explicitly.)
        $targetNextMs = $tickStartMs + ($IntervalSeconds * 1000.0)
        while ($true) {
            $remainingMs = $targetNextMs - $Stopwatch.Elapsed.TotalMilliseconds
            if ($remainingMs -le 0) { break }
            $sliceMs = [Math]::Min(100, [Math]::Max(1, $remainingMs))
            Start-Sleep -Milliseconds $sliceMs

            if ($script:MarkerSupported) {
                try {
                    while ([Console]::KeyAvailable) {
                        $key = [Console]::ReadKey($true)
                        if ($key.Key -eq 'M') {
                            $script:MarkerCount++
                            $label = "marker-$($script:MarkerCount)"
                            Write-CsvRow -Writer $script:MarkersWriter -Fields @((Get-Iso8601), $label)
                            $script:MarkersWriter.Flush()
                            Write-Host "[marker] $label recorded at $(Get-Iso8601)"
                        }
                    }
                } catch {
                    $script:MarkerSupported = $false
                }
            }
        }
    }
} finally {
    Write-Host ''
    Write-Host 'Stopping measurement...'

    Write-Host 'Flushing logs...'
    foreach ($w in @($script:SystemWriter, $script:ProcessesWriter, $script:EventsWriter, $script:MarkersWriter)) {
        try { $w.Flush(); $w.Close() } catch { }
    }

    # Copy only the newly-appended tail of the tracker's own startup.log (read-only, no lock).
    if ($script:TrackerLogAvailable) {
        try {
            $fs = [System.IO.FileStream]::new($TrackerStartupLogPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
            try {
                $offset = $script:TrackerLogStartOffset
                if ($offset -gt $fs.Length) { $offset = 0 }  # log was rotated/truncated; note and take from the top
                $fs.Seek($offset, [System.IO.SeekOrigin]::Begin) | Out-Null
                $reader = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8)
                $tail = $reader.ReadToEnd()
                [System.IO.File]::WriteAllText($TrackerLogTailPath, $tail, $Utf8Bom)
            } finally {
                $fs.Dispose()
            }
        } catch {
            $script:Errors.Add("Could not copy tracker log tail: $($_.Exception.Message)")
        }
    } else {
        $script:Errors.Add('Tracker startup.log was not found at session start; nothing to tail.')
    }

    Write-Host 'Generating summary...'

    # No helper processes are spawned by this script (perf counters and Get-Counter run
    # in-process), so there is nothing to reap. AC6 / Tracker / Overlay are left running.
    $sessionEnd = Get-Date
    $duration = New-TimeSpan -Start $SessionStart -End $sessionEnd

    function Get-AggLine {
        param([string]$Key, [string]$Label)
        if (-not $script:Agg.ContainsKey($Key)) { return "$Label : no samples" }
        $a = $script:Agg[$Key]
        $targetPids = if ($a.Pids.Count -gt 0) { ($a.Pids -join ', ') } else { '(none)' }
        return "$Label : detected PID(s)=$targetPids  avg_cpu_system%=$(Format-Num ($(if ($a.SampleCount -gt 0) { $a.SumCpuSystem / $a.SampleCount } else { $null })))  peak_cpu_system%=$(Format-Num $a.PeakCpuSystem)  peak_working_set_mb=$(Format-Num $a.PeakWorkingSetMB)  peak_private_mb=$(Format-Num $a.PeakPrivateMB)"
    }

    $summaryLines = New-Object System.Collections.Generic.List[string]
    $summaryLines.Add('AC6 RC Performance Capture - summary')
    $summaryLines.Add('')
    $summaryLines.Add('[Session]')
    $summaryLines.Add("start    : $($SessionStart.ToString('yyyy-MM-ddTHH:mm:ss.fffK'))")
    $summaryLines.Add("end      : $($sessionEnd.ToString('yyyy-MM-ddTHH:mm:ss.fffK'))")
    $summaryLines.Add("duration : $duration")
    $summaryLines.Add('')
    $summaryLines.Add('[System]')
    if ($script:Agg.ContainsKey('system')) {
        $s = $script:Agg['system']
        $avg = if ($s.SampleCount -gt 0) { $s.SumCpuSystem / $s.SampleCount } else { $null }
        $summaryLines.Add("average CPU % (system, 100%=all cores) : $(Format-Num $avg)")
        $summaryLines.Add("peak CPU %                              : $(Format-Num $s.PeakCpuSystem)")
    } else {
        $summaryLines.Add('CPU: no samples')
    }
    $minAvail = if ($script:Agg.ContainsKey('system-mem')) { $script:Agg['system-mem'].MinAvailMb } else { $null }
    $summaryLines.Add("minimum available memory (MB)           : $(if ($minAvail -and $minAvail -lt [double]::MaxValue) { Format-Num $minAvail } else { 'N/A' })")
    $summaryLines.Add('')
    $summaryLines.Add('[AC6]')
    $summaryLines.Add((Get-AggLine -Key 'ac6' -Label 'armoredcore6.exe'))
    $summaryLines.Add('')
    $summaryLines.Add('[Tracker]')
    $summaryLines.Add((Get-AggLine -Key 'tracker' -Label 'tracker (app.py / server)'))
    $summaryLines.Add((Get-AggLine -Key 'overlay' -Label 'overlay (app.py --overlay)'))
    $summaryLines.Add((Get-AggLine -Key 'dashboard' -Label 'dashboard'))
    $summaryLines.Add((Get-AggLine -Key 'launcher' -Label 'launcher (launcher.pyw)'))
    $summaryLines.Add((Get-AggLine -Key 'worker-child' -Label 'worker-child (WGC capture / screenshot-save, not distinguishable from outside)'))
    $summaryLines.Add('')
    $summaryLines.Add('[Process safety]')
    $summaryLines.Add("duplicate Tracker observed        : $(if ($script:DuplicateTrackerObserved) { 'YES' } else { 'NO' })")
    $summaryLines.Add("duplicate Overlay observed         : $(if ($script:DuplicateOverlayObserved) { 'YES' } else { 'NO' })")
    $summaryLines.Add("multiple capture children observed : $(if ($script:MultipleCaptureChildObserved) { 'YES' } else { 'NO' })")
    $summaryLines.Add("orphan-like child observed          : $(if ($script:OrphanLikeChildObserved) { 'YES' } else { 'NO' })")
    $summaryLines.Add('')
    $summaryLines.Add('[Sampling]')
    $summaryLines.Add("expected interval (s) : $IntervalSeconds")
    $summaryLines.Add("actual samples        : $sampleCount")
    $summaryLines.Add("largest sampling gap (ms) : $(Format-Num $maxGapMs 1)")
    if ($script:Agg.ContainsKey('sampler')) {
        $summaryLines.Add("sampler own CPU peak (one-core normalized %) : $(Format-Num $script:Agg['sampler'].PeakCpuOneCore)")
        $summaryLines.Add("sampler own CPU peak (system normalized %)   : $(Format-Num $script:Agg['sampler'].PeakCpuSystem)")
    }
    $summaryLines.Add('markers recorded      : ' + $script:MarkerCount)
    $summaryLines.Add('')
    $summaryLines.Add('[Definitions]')
    $summaryLines.Add('cpu_one_core_pct           : delta(TotalProcessorTime) / delta(wall time) * 100. One fully-used core = 100%; can exceed 100% for multi-threaded processes (classic Task Manager "Details" convention).')
    $summaryLines.Add('cpu_system_normalized_pct  : cpu_one_core_pct / logical_processor_count. 100% means the whole machine is saturated.')
    $summaryLines.Add('private_memory_mb          : Process.PrivateMemorySize64 (private bytes / commit charge), not the perfmon "Working Set - Private" counter.')
    $summaryLines.Add('gpu_utilization_sum_pct    : sum of all \GPU Engine(*)\Utilization Percentage instances; can exceed 100% (multiple engines).')
    $summaryLines.Add('gpu_utilization_max_engine_pct : highest single engine instance value observed in that sample.')
    $summaryLines.Add('')
    $summaryLines.Add('[Errors / unsupported counters]')
    if (-not $script:GpuSupported) {
        $summaryLines.Add("GPU counters unavailable on this machine: $($script:GpuDisabledReason)")
    }
    if ($script:Errors.Count -gt 0) {
        foreach ($e in $script:Errors) { $summaryLines.Add($e) }
    }
    if ($script:GpuSupported -and $script:Errors.Count -eq 0) {
        $summaryLines.Add('(none)')
    }

    try { [System.IO.File]::WriteAllLines($SummaryPath, $summaryLines, $Utf8Bom) } catch { }

    Write-Host ''
    Write-Host 'Measurement completed.'
    Write-Host 'Output:'
    Write-Host $OutDir
}
