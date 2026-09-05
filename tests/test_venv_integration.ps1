param(
    [Parameter(Mandatory = $true)][string]$BasePythonPath,
    [switch]$RunLifecycle
)

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
$oldLocalAppData = $env:LOCALAPPDATA
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

    if ($RunLifecycle) {
        $localAppData = Join-Path $testRoot 'localappdata'
        $probeResultPath = Join-Path $testRoot 'topology.json'
        New-Item -ItemType Directory -Path $localAppData -Force | Out-Null
        $env:LOCALAPPDATA = $localAppData
        $probeArguments = '"{0}" "{1}" "{2}"' -f `
            (Join-Path $root 'tests\venv_product_lifecycle_probe.py'), $root, $probeResultPath
        $launcherWrapper = Start-Process -FilePath $result.VenvPythonwPath -ArgumentList $probeArguments -PassThru
        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        while (-not (Test-Path -LiteralPath $probeResultPath -PathType Leaf) -and [DateTime]::UtcNow -lt $deadline) {
            Start-Sleep -Milliseconds 200
        }
        if (-not (Test-Path -LiteralPath $probeResultPath -PathType Leaf)) { throw 'product lifecycle probe did not publish topology' }
        $topology = Get-Content -LiteralPath $probeResultPath -Raw | ConvertFrom-Json
        if ($topology.error) { throw "product lifecycle probe failed: $($topology.error)" }
        $runtime = $topology.runtime
        $runtimeProcess = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f [int]$runtime.pid)
        if (-not $runtimeProcess) { throw 'runtime process missing after launcher exit' }
        Write-Host ("topology launcher={0} wrapper={1} popen={2} runtime={3} parent={4} executable={5} command={6}" -f `
            $topology.launcher_pid, $launcherWrapper.Id, $topology.popen_pid, $runtime.pid, `
            $runtimeProcess.ParentProcessId, $runtimeProcess.ExecutablePath, $runtimeProcess.CommandLine)
        Write-Host ("topology sys.executable={0} sys.prefix={1} sys.base_prefix={2}" -f `
            $topology.launcher_executable, $topology.launcher_prefix, $topology.launcher_base_prefix)
        if ([string]$runtime.launch_id -ne [string]$topology.launch_id) { throw 'runtime launch identity differs from launcher identity' }
        if ([int]$runtime.pid -le 0 -or [int]$topology.popen_pid -le 0) { throw 'invalid topology PID' }

        $launcherDeadline = [DateTime]::UtcNow.AddSeconds(8)
        while (-not $launcherWrapper.HasExited -and [DateTime]::UtcNow -lt $launcherDeadline) {
            $launcherWrapper.Refresh()
            Start-Sleep -Milliseconds 100
        }
        if (-not $launcherWrapper.HasExited) { throw 'launcher wrapper remained after startup verification' }
        $launcherProcessDeadline = [DateTime]::UtcNow.AddSeconds(8)
        while ((Get-Process -Id ([int]$topology.launcher_pid) -ErrorAction SilentlyContinue) -and [DateTime]::UtcNow -lt $launcherProcessDeadline) {
            Start-Sleep -Milliseconds 100
        }
        if (Get-Process -Id ([int]$topology.launcher_pid) -ErrorAction SilentlyContinue) {
            throw 'launcher process remained after startup verification'
        }

        $healthUrl = "http://127.0.0.1:$([int]$runtime.port)/health"
        $healthDeadline = [DateTime]::UtcNow.AddSeconds(30)
        do {
            $health = (Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2).Content | ConvertFrom-Json
            if (-not $health.ok -or -not $health.overlay.ok) { throw 'product health or overlay became unhealthy' }
            Start-Sleep -Seconds 1
        } while ([DateTime]::UtcNow -lt $healthDeadline)

        Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/api/system/shutdown" -f [int]$runtime.port) `
            -Method Post -Headers @{ 'X-Control-Token' = [string]$runtime.token } -UseBasicParsing -TimeoutSec 3 | Out-Null
        $shutdownDeadline = [DateTime]::UtcNow.AddSeconds(15)
        while ((Get-Process -Id ([int]$runtime.pid) -ErrorAction SilentlyContinue)) {
            if ([DateTime]::UtcNow -ge $shutdownDeadline) { throw 'runtime remained after authenticated shutdown' }
            Start-Sleep -Milliseconds 200
        }
        foreach ($processId in @([int]$topology.launcher_pid, [int]$runtime.pid, [int]$health.overlay.pid)) {
            if ($processId -gt 0 -and (Get-Process -Id $processId -ErrorAction SilentlyContinue)) { throw "orphan process remains: $processId" }
        }
    }
} finally {
    $env:PYTHONUSERBASE = $oldPythonUserBase
    $env:LOCALAPPDATA = $oldLocalAppData
    if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
}

Write-Host "Dedicated venv integration on Python $($base.Version): OK"
