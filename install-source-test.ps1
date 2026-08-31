[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$appName = 'AC6 WinLoss Tracker'
$branchName = 'test/python-source-install'
$archiveUrl = 'https://github.com/TullysAC6/ac6-winloss-tracker/archive/refs/heads/test/python-source-install.zip'
$dataPath = Join-Path $env:LOCALAPPDATA 'AC6WinLossTracker'
$script:logPath = Join-Path $dataPath 'source-install.log'
$installPath = Join-Path $env:LOCALAPPDATA 'Programs\AC6WinLossTrackerSource'
$installParent = Split-Path -Parent $installPath
$tempRoot = $null
$exitCode = 0
$script:currentStage = 'startup'

function Write-InstallLog {
    param([Parameter(Mandatory = $true)][string]$Message)
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -LiteralPath $script:logPath -Value $line -Encoding UTF8
}

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "`n[$appName] $Message" -ForegroundColor Cyan
    Write-InstallLog $Message
}

function Set-InstallStage {
    param([Parameter(Mandatory = $true)][string]$Name)
    $script:currentStage = $Name
    Write-InstallLog "stage: $Name"
}

function Write-CandidateSkipped {
    param(
        [Parameter(Mandatory = $true)][string]$Kind,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Reason
    )

    try {
        Write-InstallLog "$Kind candidate skipped: $Path"
        Write-InstallLog "reason: $Reason"
    } catch {
        # A diagnostic write must never stop candidate discovery.
    }
}

function Test-IsAppExecutionAlias {
    param([Parameter(Mandatory = $true)][string]$Path)
    return $Path -match '(?i)\\Microsoft\\WindowsApps\\'
}

function ConvertTo-NativeArgument {
    param([AllowEmptyString()][string]$Argument)

    if ($null -eq $Argument -or $Argument.Length -eq 0) {
        return '""'
    }
    if ($Argument -notmatch '[\s"]') {
        return $Argument
    }

    # Apply the CommandLineToArgvW quoting rules used by Windows native programs.
    $quoted = New-Object System.Text.StringBuilder
    [void]$quoted.Append('"')
    $backslashCount = 0
    foreach ($character in $Argument.ToCharArray()) {
        if ($character -eq '\') {
            $backslashCount++
            continue
        }
        if ($character -eq '"') {
            [void]$quoted.Append(('\' * (($backslashCount * 2) + 1)))
            [void]$quoted.Append('"')
        } else {
            if ($backslashCount -gt 0) {
                [void]$quoted.Append(('\' * $backslashCount))
            }
            [void]$quoted.Append($character)
        }
        $backslashCount = 0
    }
    if ($backslashCount -gt 0) {
        [void]$quoted.Append(('\' * ($backslashCount * 2)))
    }
    [void]$quoted.Append('"')
    return $quoted.ToString()
}

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @()
    )

    $nativeArguments = @($ArgumentList | ForEach-Object { ConvertTo-NativeArgument -Argument ([string]$_) })
    $argumentString = $nativeArguments -join ' '
    $displayCommand = @((ConvertTo-NativeArgument -Argument $FilePath)) + $nativeArguments -join ' '
    $process = $null
    $stdout = ''
    $stderr = ''
    $nativeExitCode = -1

    try {
        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = $FilePath
        $startInfo.Arguments = $argumentString
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true

        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $startInfo
        if (-not $process.Start()) {
            throw 'native process did not start'
        }

        # Read both streams asynchronously so neither pipe can block the process.
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        $nativeExitCode = $process.ExitCode
    } catch {
        $stderr = @($stderr, $_.Exception.ToString()) -join [Environment]::NewLine
    } finally {
        if ($process) {
            $process.Dispose()
        }
    }

    return [PSCustomObject]@{
        Command = $displayCommand
        ExitCode = $nativeExitCode
        StdOut = [string]$stdout
        StdErr = [string]$stderr
    }
}

function Get-SignedExecutableInfo {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$AllowWindowsApps
    )

    $kind = if ($AllowWindowsApps) { 'Executable' } else { 'Python' }

    # Reject Python App Execution Alias paths before touching the file.
    if (-not $AllowWindowsApps -and (Test-IsAppExecutionAlias -Path $Path)) {
        Write-CandidateSkipped -Kind $kind -Path $Path -Reason 'WindowsApps App Execution Alias'
        return $null
    }

    try {
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf -ErrorAction Stop)) {
            Write-CandidateSkipped -Kind $kind -Path $Path -Reason 'file not found'
            return $null
        }
    } catch {
        Write-CandidateSkipped -Kind $kind -Path $Path -Reason ("Test-Path failed: {0}" -f $_.Exception.Message)
        return $null
    }

    try {
        $resolvedPath = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
    } catch {
        Write-CandidateSkipped -Kind $kind -Path $Path -Reason ("Resolve-Path failed: {0}" -f $_.Exception.Message)
        return $null
    }

    if (-not $AllowWindowsApps -and (Test-IsAppExecutionAlias -Path $resolvedPath)) {
        Write-CandidateSkipped -Kind $kind -Path $resolvedPath -Reason 'WindowsApps App Execution Alias'
        return $null
    }

    try {
        $signature = Get-AuthenticodeSignature -LiteralPath $resolvedPath -ErrorAction Stop
    } catch {
        Write-CandidateSkipped -Kind $kind -Path $resolvedPath -Reason ("Get-AuthenticodeSignature failed: {0}" -f $_.Exception.Message)
        return $null
    }

    if ($signature.Status -ne 'Valid' -or $signature.SignatureType -ne 'Authenticode' -or -not $signature.SignerCertificate) {
        Write-CandidateSkipped -Kind $kind -Path $resolvedPath -Reason ("signature is not valid Authenticode (Status={0}, Type={1})" -f $signature.Status, $signature.SignatureType)
        return $null
    }

    return [PSCustomObject]@{
        Path = $resolvedPath
        Signature = $signature
    }
}

function Add-PythonCandidate {
    param(
        [Parameter(Mandatory = $true)]$List,
        [Parameter(Mandatory = $true)][string]$Path
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return
    }
    if (Test-IsAppExecutionAlias -Path $Path) {
        Write-CandidateSkipped -Kind 'Python' -Path $Path -Reason 'WindowsApps App Execution Alias'
        return
    }
    $List.Add($Path) | Out-Null
}

function Get-PythonCandidatePaths {
    $candidates = New-Object 'System.Collections.Generic.List[string]'
    $pythonBase = Join-Path $env:LOCALAPPDATA 'Programs\Python'

    try {
        Add-PythonCandidate -List $candidates -Path (Join-Path $pythonBase 'Python312\python.exe')
        if (Test-Path -LiteralPath $pythonBase -PathType Container -ErrorAction Stop) {
            $pythonDirectories = @(Get-ChildItem -LiteralPath $pythonBase -Directory -Filter 'Python3*' -ErrorAction Stop |
                Sort-Object Name -Descending)
            foreach ($directory in $pythonDirectories) {
                try {
                    Add-PythonCandidate -List $candidates -Path (Join-Path $directory.FullName 'python.exe')
                } catch {
                    Write-CandidateSkipped -Kind 'Python' -Path ([string]$directory.FullName) -Reason ("LOCALAPPDATA candidate failed: {0}" -f $_.Exception.Message)
                }
            }
        }
    } catch {
        Write-CandidateSkipped -Kind 'Python' -Path $pythonBase -Reason ("LOCALAPPDATA search failed: {0}" -f $_.Exception.Message)
    }

    $pyCommand = $null
    try {
        $pyCommand = Get-Command py.exe -ErrorAction Stop
    } catch {
        Write-CandidateSkipped -Kind 'Python launcher' -Path 'py.exe' -Reason ("Get-Command failed: {0}" -f $_.Exception.Message)
    }
    if ($pyCommand) {
        $pyInfo = $null
        try {
            $pySource = [string]$pyCommand.Source
            if (Test-IsAppExecutionAlias -Path $pySource) {
                Write-CandidateSkipped -Kind 'Python launcher' -Path $pySource -Reason 'WindowsApps App Execution Alias'
            } else {
                $pyInfo = Get-SignedExecutableInfo -Path $pySource
            }
        } catch {
            Write-CandidateSkipped -Kind 'Python launcher' -Path 'py.exe' -Reason ("launcher inspection failed: {0}" -f $_.Exception.Message)
        }
        if ($pyInfo) {
            try {
                $launcherResult = Invoke-NativeCommand -FilePath $pyInfo.Path -ArgumentList @('-0p')
                if ($launcherResult.ExitCode -eq 0) {
                    foreach ($line in @($launcherResult.StdOut -split '\r?\n')) {
                        if ([string]$line -match '([A-Za-z]:\\.+\\python(?:\d+(?:\.\d+)?)?\.exe)\s*$') {
                            try {
                                Add-PythonCandidate -List $candidates -Path $Matches[1]
                            } catch {
                                Write-CandidateSkipped -Kind 'Python' -Path ([string]$Matches[1]) -Reason ("py launcher candidate failed: {0}" -f $_.Exception.Message)
                            }
                        }
                    }
                } else {
                    Write-CandidateSkipped -Kind 'Python launcher' -Path $pyInfo.Path -Reason ("py.exe -0p failed with exit code {0}: {1}" -f $launcherResult.ExitCode, $launcherResult.StdErr.Trim())
                }
            } catch {
                Write-CandidateSkipped -Kind 'Python launcher' -Path $pyInfo.Path -Reason ("py.exe -0p failed: {0}" -f $_.Exception.Message)
            }
        }
    }

    foreach ($name in @('python.exe', 'python3.exe', 'python')) {
        try {
            $commands = @(Get-Command $name -All -ErrorAction Stop)
            foreach ($command in $commands) {
                try {
                    $path = [string]$command.Source
                    if (-not [string]::IsNullOrWhiteSpace($path)) {
                        if (Test-IsAppExecutionAlias -Path $path) {
                            Write-CandidateSkipped -Kind 'Python' -Path $path -Reason 'WindowsApps App Execution Alias'
                            continue
                        }
                        Add-PythonCandidate -List $candidates -Path $path
                    }
                } catch {
                    Write-CandidateSkipped -Kind 'Python' -Path $name -Reason ("command candidate failed: {0}" -f $_.Exception.Message)
                }
            }
        } catch {
            Write-CandidateSkipped -Kind 'Python command' -Path $name -Reason ("Get-Command failed: {0}" -f $_.Exception.Message)
        }
    }

    return $candidates
}

function Invoke-PythonVerification {
    param([Parameter(Mandatory = $true)][string]$PythonPath)

    # Keep the Python snippet free of nested quotes and JSON escaping.
    $verificationCode = 'import sys; print(sys.version_info.major); print(sys.version_info.minor); print(sys.version_info.micro); print(sys.executable)'
    $result = Invoke-NativeCommand -FilePath $PythonPath -ArgumentList @('-c', $verificationCode)
    Write-InstallLog "Python verification candidate path: $PythonPath"
    Write-InstallLog "Python verification exit code: $($result.ExitCode)"
    Write-InstallLog "Python verification stdout:`n$($result.StdOut)"
    Write-InstallLog "Python verification stderr:`n$($result.StdErr)"
    return $result
}

function Find-SupportedPython {
    $seen = @{}
    try {
        $candidatePaths = @(Get-PythonCandidatePaths)
    } catch {
        Write-CandidateSkipped -Kind 'Python discovery' -Path '(candidate enumeration)' -Reason $_.Exception.Message
        $candidatePaths = @()
    }

    foreach ($candidate in $candidatePaths) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }

        if (Test-IsAppExecutionAlias -Path $candidate) {
            Write-CandidateSkipped -Kind 'Python' -Path $candidate -Reason 'WindowsApps App Execution Alias'
            continue
        }

        try {
            $fullPath = [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($candidate))
        } catch {
            Write-CandidateSkipped -Kind 'Python' -Path $candidate -Reason ("path normalization failed: {0}" -f $_.Exception.Message)
            continue
        }
        if ($seen.ContainsKey($fullPath)) {
            continue
        }
        $seen[$fullPath] = $true

        # Never execute an App Execution Alias or an unsigned Python candidate.
        $signedPython = Get-SignedExecutableInfo -Path $fullPath
        if (-not $signedPython) {
            continue
        }
        if ($signedPython.Signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
            Write-CandidateSkipped -Kind 'Python' -Path $fullPath -Reason ("unexpected signer: {0}" -f $signedPython.Signature.SignerCertificate.Subject)
            continue
        }

        try {
            $verification = Invoke-PythonVerification -PythonPath $signedPython.Path
            if ($verification.ExitCode -ne 0) {
                Write-CandidateSkipped -Kind 'Python' -Path $signedPython.Path -Reason ("version check failed with exit code {0}; see Python verification log" -f $verification.ExitCode)
                continue
            }

            $versionLines = @($verification.Stdout -split '\r?\n' | Where-Object { $_ -ne '' })
            if ($versionLines.Count -ne 4) {
                Write-CandidateSkipped -Kind 'Python' -Path $signedPython.Path -Reason ("version check returned {0} lines instead of 4; see Python verification log" -f $versionLines.Count)
                continue
            }

            $major = 0
            $minor = 0
            $micro = 0
            if (-not [int]::TryParse($versionLines[0].Trim(), [ref]$major) -or
                -not [int]::TryParse($versionLines[1].Trim(), [ref]$minor) -or
                -not [int]::TryParse($versionLines[2].Trim(), [ref]$micro)) {
                Write-CandidateSkipped -Kind 'Python' -Path $signedPython.Path -Reason 'version check returned invalid numeric fields; see Python verification log'
                continue
            }
            if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
                Write-CandidateSkipped -Kind 'Python' -Path $signedPython.Path -Reason ("Python version is below 3.10: {0}.{1}.{2}" -f $major, $minor, $micro)
                continue
            }

            $actualPython = (Resolve-Path -LiteralPath $versionLines[3].Trim()).Path
            if ($actualPython -ne $signedPython.Path) {
                $signedPython = Get-SignedExecutableInfo -Path $actualPython
                if (-not $signedPython) {
                    continue
                }
                if ($signedPython.Signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
                    Write-CandidateSkipped -Kind 'Python' -Path $actualPython -Reason ("unexpected signer: {0}" -f $signedPython.Signature.SignerCertificate.Subject)
                    continue
                }
            }

            $pythonwPath = Join-Path (Split-Path -Parent $signedPython.Path) 'pythonw.exe'
            $signedPythonw = Get-SignedExecutableInfo -Path $pythonwPath
            if (-not $signedPythonw) {
                continue
            }
            if ($signedPythonw.Signature.SignerCertificate.Thumbprint -ne $signedPython.Signature.SignerCertificate.Thumbprint) {
                Write-CandidateSkipped -Kind 'Python' -Path $pythonwPath -Reason 'pythonw.exe signer does not match python.exe'
                continue
            }

            return [PSCustomObject]@{
                PythonPath = $signedPython.Path
                PythonwPath = $signedPythonw.Path
                Version = '{0}.{1}.{2}' -f $major, $minor, $micro
                SignatureStatus = [string]$signedPython.Signature.Status
                SignerSubject = [string]$signedPython.Signature.SignerCertificate.Subject
            }
        } catch {
            Write-CandidateSkipped -Kind 'Python' -Path $signedPython.Path -Reason ("verification failed: {0}" -f $_.Exception.Message)
            continue
        }
    }
    return $null
}

function Install-PythonWithWinget {
    $wingetCandidates = New-Object 'System.Collections.Generic.List[string]'
    try {
        $wingetCommand = Get-Command winget.exe -ErrorAction Stop
        if ($wingetCommand -and -not [string]::IsNullOrWhiteSpace($wingetCommand.Source)) {
            $wingetCandidates.Add([string]$wingetCommand.Source) | Out-Null
        }
    } catch {
        Write-CandidateSkipped -Kind 'winget' -Path 'winget.exe' -Reason ("Get-Command failed: {0}" -f $_.Exception.Message)
    }

    try {
        $appInstallerPackages = @(Get-AppxPackage -Name Microsoft.DesktopAppInstaller -ErrorAction Stop |
            Sort-Object Version -Descending)
    } catch {
        Write-CandidateSkipped -Kind 'winget' -Path 'Microsoft.DesktopAppInstaller' -Reason ("Get-AppxPackage failed: {0}" -f $_.Exception.Message)
        $appInstallerPackages = @()
    }
    foreach ($package in $appInstallerPackages) {
        foreach ($fileName in @('winget.exe', 'AppInstallerCLI.exe')) {
            try {
                $wingetCandidates.Add((Join-Path ([string]$package.InstallLocation) $fileName)) | Out-Null
            } catch {
                Write-CandidateSkipped -Kind 'winget' -Path $fileName -Reason ("package candidate failed: {0}" -f $_.Exception.Message)
            }
        }
    }

    if ($wingetCandidates.Count -eq 0) {
        throw 'wingetが見つかりませんでした。Windowsの「アプリ インストーラー」を更新してから、もう一度実行してください。'
    }

    $signedWinget = $null
    foreach ($candidate in $wingetCandidates) {
        try {
            $signedWinget = Get-SignedExecutableInfo -Path $candidate -AllowWindowsApps
            if ($signedWinget) {
                break
            }
        } catch {
            Write-CandidateSkipped -Kind 'winget' -Path $candidate -Reason ("candidate inspection failed: {0}" -f $_.Exception.Message)
        }
    }
    if (-not $signedWinget) {
        throw '安全性を確認できるwingetが見つかりませんでした。Windows Updateを実行してから、もう一度お試しください。'
    }

    Write-Step 'Python 3.12を現在のユーザー用に自動インストールしています。'
    $wingetResult = Invoke-NativeCommand -FilePath $signedWinget.Path -ArgumentList @(
        'install', '--exact', '--id', 'Python.Python.3.12', '--scope', 'user', '--silent',
        '--accept-package-agreements', '--accept-source-agreements'
    )
    Write-InstallLog "winget command: $($wingetResult.Command)"
    Write-InstallLog "winget exit code: $($wingetResult.ExitCode)"
    Write-InstallLog "winget stdout:`n$($wingetResult.StdOut)"
    Write-InstallLog "winget stderr:`n$($wingetResult.StdErr)"
    if (-not [string]::IsNullOrWhiteSpace($wingetResult.StdOut)) {
        Write-Host $wingetResult.StdOut
    }
    if ($wingetResult.ExitCode -ne 0) {
        throw "Pythonの自動インストールに失敗しました（winget終了コード: $($wingetResult.ExitCode)）。インターネット接続を確認してください。"
    }
}

function Invoke-PipInstall {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$RequirementsPath
    )

    Write-Step 'Python依存ライブラリを確認しています。'
    $pipCheck = Invoke-NativeCommand -FilePath $PythonPath -ArgumentList @('-m', 'pip', '--version')
    Write-InstallLog "pip version command: $($pipCheck.Command)"
    Write-InstallLog "pip version exit code: $($pipCheck.ExitCode)"
    Write-InstallLog "pip version stdout:`n$($pipCheck.StdOut)"
    Write-InstallLog "pip version stderr:`n$($pipCheck.StdErr)"
    if ($pipCheck.ExitCode -ne 0) {
        Write-Host 'pipが見つからないため、Python標準のensurepipを実行します。'
        $ensureResult = Invoke-NativeCommand -FilePath $PythonPath -ArgumentList @('-m', 'ensurepip', '--upgrade')
        Write-InstallLog "ensurepip command: $($ensureResult.Command)"
        Write-InstallLog "ensurepip exit code: $($ensureResult.ExitCode)"
        Write-InstallLog "ensurepip stdout:`n$($ensureResult.StdOut)"
        Write-InstallLog "ensurepip stderr:`n$($ensureResult.StdErr)"
        if (-not [string]::IsNullOrWhiteSpace($ensureResult.StdOut)) {
            Write-Host $ensureResult.StdOut
        }
        if ($ensureResult.ExitCode -ne 0) {
            Write-InstallLog "pip install result: failed (ensurepip exit code $($ensureResult.ExitCode))"
            throw 'pipの準備に失敗しました。Pythonを再インストールしてから、もう一度お試しください。'
        }
    }

    $pipResult = Invoke-NativeCommand -FilePath $PythonPath -ArgumentList @(
        '-m', 'pip', 'install', '--user', '--no-warn-script-location', '-r', $RequirementsPath
    )
    Write-InstallLog "pip command: $($pipResult.Command)"
    Write-InstallLog "pip exit code: $($pipResult.ExitCode)"
    Write-InstallLog "pip stdout:`n$($pipResult.StdOut)"
    Write-InstallLog "pip stderr:`n$($pipResult.StdErr)"
    if (-not [string]::IsNullOrWhiteSpace($pipResult.StdOut)) {
        Write-Host $pipResult.StdOut
    }
    if ($pipResult.ExitCode -ne 0) {
        Write-InstallLog "pip install result: failed (exit code $($pipResult.ExitCode))"
        throw '必要なPythonライブラリのインストールに失敗しました。インターネット接続を確認してください。'
    }
    Write-InstallLog 'pip install result: success'

    $dependencyCode = 'from importlib.metadata import version; import mss, ttkbootstrap; print("mss=" + mss.__version__ + "; ttkbootstrap=" + version("ttkbootstrap"))'
    $dependencyResult = Invoke-NativeCommand -FilePath $PythonPath -ArgumentList @('-c', $dependencyCode)
    Write-InstallLog "dependency verification command: $($dependencyResult.Command)"
    Write-InstallLog "dependency verification exit code: $($dependencyResult.ExitCode)"
    Write-InstallLog "dependency verification stdout:`n$($dependencyResult.StdOut)"
    Write-InstallLog "dependency verification stderr:`n$($dependencyResult.StdErr)"
    if ($dependencyResult.ExitCode -ne 0) {
        throw 'インストールしたPythonライブラリを読み込めませんでした。source-install.logを添えて報告してください。'
    }
    Write-InstallLog "dependency verification result: success ($($dependencyResult.StdOut.Trim()))"
}

function Get-TrackerRuntime {
    $runtimePath = Join-Path $dataPath '.runtime.json'
    try {
        if (-not (Test-Path -LiteralPath $runtimePath -PathType Leaf)) {
            return $null
        }
        $runtime = Get-Content -LiteralPath $runtimePath -Raw -ErrorAction Stop | ConvertFrom-Json
        $runtimePid = [int]$runtime.pid
        $runtimePort = [int]$runtime.port
        $runtimeToken = [string]$runtime.token
        if ($runtimePid -le 0 -or $runtimePort -lt 1 -or $runtimePort -gt 65535) {
            throw 'runtime PID or port is invalid'
        }
        return [PSCustomObject]@{
            Path = $runtimePath
            Pid = $runtimePid
            Port = $runtimePort
            Token = $runtimeToken
        }
    } catch {
        Write-InstallLog "runtime inspection failed: $($_.Exception.Message)"
        return $null
    }
}

function Test-TrackerCommandLine {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [AllowEmptyString()][string]$CommandLine
    )

    if ($Name -notmatch '(?i)^pythonw?\.exe$') {
        return $false
    }
    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return $false
    }
    $trackerEntryPattern = '(?i){0}[\\/](app\.py|launcher\.pyw|dashboard\.py)(?="|\s|$)' -f [Regex]::Escape($installPath)
    return $CommandLine -match $trackerEntryPattern
}

function Get-TrackerProcesses {
    param([int]$RuntimePort = 0)

    $found = @{}
    try {
        $pythonProcesses = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction Stop)
        foreach ($processInfo in $pythonProcesses) {
            if (Test-TrackerCommandLine -Name ([string]$processInfo.Name) -CommandLine ([string]$processInfo.CommandLine)) {
                $found[[int]$processInfo.ProcessId] = $processInfo
            }
        }
    } catch {
        Write-InstallLog "Tracker process enumeration failed: $($_.Exception.Message)"
    }

    # A port alone is never trusted. Its owner must pass the same executable
    # name and Tracker command-line checks before it can become a stop target.
    if ($RuntimePort -ge 1 -and $RuntimePort -le 65535) {
        try {
            $listeners = @(Get-NetTCPConnection -LocalPort $RuntimePort -State Listen -ErrorAction Stop)
            foreach ($listener in $listeners) {
                $ownerPid = [int]$listener.OwningProcess
                if ($ownerPid -le 0) {
                    continue
                }
                $owner = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $ownerPid) -ErrorAction Stop
                if ($owner -and (Test-TrackerCommandLine -Name ([string]$owner.Name) -CommandLine ([string]$owner.CommandLine))) {
                    $found[$ownerPid] = $owner
                    Write-InstallLog "verified Tracker listener PID: $ownerPid (port $RuntimePort)"
                }
            }
        } catch {
            Write-InstallLog "Tracker listener inspection unavailable: $($_.Exception.Message)"
        }
    }

    return @($found.Values)
}

function Test-TrackerHttp {
    param([int]$Port)

    if ($Port -lt 1 -or $Port -gt 65535) {
        return $false
    }
    try {
        $response = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/stats" -f $Port) -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        return [int]$response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Test-TrackerHealth {
    param([int]$Port)
    if ($Port -lt 1 -or $Port -gt 65535) { return $false }
    try {
        $response = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/health" -f $Port) -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        $health = $response.Content | ConvertFrom-Json
        return [int]$response.StatusCode -eq 200 -and $health.ok -eq $true
    } catch { return $false }
}

function Wait-TrackerStopped {
    param(
        [int]$RuntimePort = 0,
        [int]$TimeoutSeconds = 10
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $trackerProcesses = @(Get-TrackerProcesses -RuntimePort $RuntimePort)
        if ($trackerProcesses.Count -eq 0 -and
            -not (Test-TrackerHttp -Port $RuntimePort) -and
            -not (Test-TrackerHealth -Port $RuntimePort)) {
            Remove-Item -LiteralPath (Join-Path $dataPath '.runtime.json') -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath (Join-Path $dataPath '.overlay-runtime.json') -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath (Join-Path $dataPath '.dashboard-runtime.json') -Force -ErrorAction SilentlyContinue
            return $true
        }
        Start-Sleep -Milliseconds 250
    }
    return $false
}

function Stop-RunningTracker {
    $runtimePath = Join-Path $dataPath '.runtime.json'
    $runtime = Get-TrackerRuntime
    $runtimePort = if ($runtime) { [int]$runtime.Port } else { 0 }
    $trackerProcesses = @(Get-TrackerProcesses -RuntimePort $runtimePort)
    $httpAlive = Test-TrackerHttp -Port $runtimePort

    if ($trackerProcesses.Count -eq 0 -and -not $httpAlive) {
        if (Test-Path -LiteralPath $runtimePath -PathType Leaf) {
            Remove-Item -LiteralPath $runtimePath -Force -ErrorAction SilentlyContinue
            Write-InstallLog "removed stale runtime file: $runtimePath"
        }
        Remove-Item -LiteralPath (Join-Path $dataPath '.dashboard-runtime.json') -Force -ErrorAction SilentlyContinue
        return
    }

    Write-Step '更新のため実行中のアプリを終了しています。'
    $gracefulRequested = $false
    if ($runtime -and $httpAlive -and -not [string]::IsNullOrWhiteSpace($runtime.Token)) {
        try {
            $headers = @{ 'X-Control-Token' = [string]$runtime.Token }
            $shutdownResponse = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/api/system/shutdown" -f $runtime.Port) -Method Post -Headers $headers -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ([int]$shutdownResponse.StatusCode -eq 200) {
                $gracefulRequested = $true
                Write-InstallLog "graceful shutdown requested for runtime PID $($runtime.Pid) on port $($runtime.Port)"
            }
        } catch {
            Write-InstallLog "graceful shutdown unavailable: $($_.Exception.Message)"
        }
    }

    if ($gracefulRequested -and (Wait-TrackerStopped -RuntimePort $runtimePort -TimeoutSeconds 10)) {
        Remove-Item -LiteralPath $runtimePath -Force -ErrorAction SilentlyContinue
        Write-InstallLog 'Tracker graceful shutdown completed'
        return
    }

    $trackerProcesses = @(Get-TrackerProcesses -RuntimePort $runtimePort)
    foreach ($processInfo in $trackerProcesses) {
        $processId = [int]$processInfo.ProcessId
        Write-InstallLog "fallback stopping Tracker PID ${processId}: $($processInfo.CommandLine)"
        try {
            Stop-Process -Id $processId -Force -ErrorAction Stop
        } catch {
            Write-InstallLog "fallback stop failed for Tracker PID ${processId}: $($_.Exception.Message)"
        }
    }

    if (-not (Wait-TrackerStopped -RuntimePort $runtimePort -TimeoutSeconds 10)) {
        throw 'AC6 WinLoss Trackerを終了できませんでした。数秒待ってからもう一度実行してください。'
    }
    Remove-Item -LiteralPath $runtimePath -Force -ErrorAction SilentlyContinue
    Write-InstallLog 'Tracker fallback shutdown completed'
}

function Install-SourceTree {
    param([Parameter(Mandatory = $true)][string]$SourcePath)

    New-Item -ItemType Directory -Path $installParent -Force | Out-Null
    $backupPath = "$installPath.previous"
    if (Test-Path -LiteralPath $backupPath) {
        Remove-Item -LiteralPath $backupPath -Recurse -Force
    }

    $hadPreviousInstall = Test-Path -LiteralPath $installPath
    if ($hadPreviousInstall) {
        Move-Item -LiteralPath $installPath -Destination $backupPath
    }

    try {
        Move-Item -LiteralPath $SourcePath -Destination $installPath
    } catch {
        if ($hadPreviousInstall -and -not (Test-Path -LiteralPath $installPath) -and (Test-Path -LiteralPath $backupPath)) {
            Move-Item -LiteralPath $backupPath -Destination $installPath
        }
        throw
    }

    if (Test-Path -LiteralPath $backupPath) {
        Remove-Item -LiteralPath $backupPath -Recurse -Force
    }
}

function Wait-AppRuntimeReady {
    param([int]$TimeoutSeconds = 15)

    $runtimePath = Join-Path $dataPath '.runtime.json'
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            if (Test-Path -LiteralPath $runtimePath -PathType Leaf) {
                $runtime = Get-Content -LiteralPath $runtimePath -Raw -ErrorAction Stop | ConvertFrom-Json
                $runtimePid = [int]$runtime.pid
                $runtimePort = [int]$runtime.port
                if ($runtimePid -gt 0 -and $runtimePort -ge 1 -and $runtimePort -le 65535) {
                    $runtimeProcess = Get-Process -Id $runtimePid -ErrorAction Stop
                    if ($runtimeProcess -and -not $runtimeProcess.HasExited) {
                        $response = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/health" -f $runtimePort) -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
                        $health = $response.Content | ConvertFrom-Json
                        if ([int]$response.StatusCode -eq 200 -and $health.ok -eq $true) {
                            Write-InstallLog "application runtime path: $runtimePath"
                            Write-InstallLog "application runtime PID: $runtimePid"
                            Write-InstallLog "application runtime port: $runtimePort"
                            Write-InstallLog 'application HTTP /health status: 200; overall health ready'
                            return $true
                        }
                    }
                }
            }
        } catch {
            # Startup is asynchronous. Keep polling until the deadline.
        }
        Start-Sleep -Milliseconds 250
    }
    Write-InstallLog "application runtime readiness timed out after $TimeoutSeconds seconds"
    return $false
}

function New-AppShortcut {
    param(
        [Parameter(Mandatory = $true)][string]$PythonwPath,
        [Parameter(Mandatory = $true)][string]$LauncherPath
    )

    $desktopPath = [Environment]::GetFolderPath('Desktop')
    if ([string]::IsNullOrWhiteSpace($desktopPath)) {
        throw 'デスクトップの場所を確認できなかったため、ショートカットを作成できませんでした。'
    }

    $shortcutPath = Join-Path $desktopPath 'AC6 WinLoss Tracker.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $null
    try {
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = $PythonwPath
        $shortcut.Arguments = '"{0}"' -f $LauncherPath
        $shortcut.WorkingDirectory = $installPath
        $shortcut.Description = 'AC6 Win/Loss Tracker (Python source test)'
        $shortcut.Save()
    } finally {
        if ($shortcut) {
            [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shortcut)
        }
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
    }
    return $shortcutPath
}

try {
    if ($env:OS -ne 'Windows_NT') {
        throw 'このインストーラーはWindows 11用です。WindowsのPowerShellから実行してください。'
    }
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw 'WindowsのLOCALAPPDATAフォルダーを確認できませんでした。Windowsへサインインし直してからお試しください。'
    }

    New-Item -ItemType Directory -Path $dataPath -Force | Out-Null
    Write-InstallLog '------------------------------------------------------------'
    Write-InstallLog "Installer branch: $branchName"
    Write-InstallLog "Windows version: $([Environment]::OSVersion.VersionString)"
    Set-InstallStage -Name 'startup'

    Set-InstallStage -Name 'python-discovery'
    Write-Step '署名済みのPython 3.10以上を探しています。'
    $python = Find-SupportedPython
    if (-not $python) {
        Set-InstallStage -Name 'winget-install'
        Install-PythonWithWinget
        # Do not depend on a refreshed PATH; explicit user-install locations are searched first.
        Set-InstallStage -Name 'python-verification'
        $python = Find-SupportedPython
    } else {
        Set-InstallStage -Name 'python-verification'
    }
    if (-not $python) {
        throw 'Python 3.10以上の実体を確認できませんでした。Pythonの自動インストールに失敗した可能性があります。'
    }

    Write-Host "Python $($python.Version): $($python.PythonPath)"
    Write-InstallLog "selected Python version: $($python.Version)"
    Write-InstallLog "selected Python path: $($python.PythonPath)"
    Write-InstallLog "selected Pythonw path: $($python.PythonwPath)"
    Write-InstallLog "selected Python Authenticode Status: $($python.SignatureStatus)"
    Write-InstallLog "selected Python signer: $($python.SignerSubject)"

    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('AC6WinLossTrackerSource-' + [Guid]::NewGuid().ToString('N'))
    $zipPath = Join-Path $tempRoot 'source.zip'
    $extractPath = Join-Path $tempRoot 'extracted'
    New-Item -ItemType Directory -Path $extractPath -Force | Out-Null

    Set-InstallStage -Name 'source-download'
    Write-Step 'GitHubからテスト用ソースをHTTPSで取得しています。'
    try {
        Invoke-WebRequest -Uri $archiveUrl -OutFile $zipPath -UseBasicParsing
        Expand-Archive -LiteralPath $zipPath -DestinationPath $extractPath -Force
    } catch {
        throw 'ソースコードの取得に失敗しました。インターネット接続とGitHubの状態を確認してください。'
    }

    $sourceRoot = Get-ChildItem -LiteralPath $extractPath -Directory |
        Where-Object {
            (Test-Path -LiteralPath (Join-Path $_.FullName 'app.py')) -and
            (Test-Path -LiteralPath (Join-Path $_.FullName 'launcher.pyw')) -and
            (Test-Path -LiteralPath (Join-Path $_.FullName 'dashboard.py')) -and
            (Test-Path -LiteralPath (Join-Path $_.FullName 'requirements.txt'))
        } |
        Select-Object -First 1
    if (-not $sourceRoot) {
        throw '取得したZIPの内容を確認できませんでした。配布ブランチの構成を確認してください。'
    }

    $forbiddenFiles = @(Get-ChildItem -LiteralPath $sourceRoot.FullName -Recurse -File |
        Where-Object { $_.Extension -match '(?i)^\.(exe|com|scr|msi|msix|pfx|p12)$' })
    if ($forbiddenFiles.Count -gt 0) {
        throw '取得したソースに、このテストでは実行しないバイナリまたは証明書ファイルが含まれていました。'
    }

    Set-InstallStage -Name 'pip-install'
    Invoke-PipInstall -PythonPath $python.PythonPath -RequirementsPath (Join-Path $sourceRoot.FullName 'requirements.txt')

    if (Test-Path -LiteralPath $installPath -PathType Container) {
        Set-InstallStage -Name 'stop-running-app'
        Stop-RunningTracker
    }

    Set-InstallStage -Name 'source-install'
    Write-Step 'アプリのソースをユーザー領域へインストールしています。'
    Install-SourceTree -SourcePath $sourceRoot.FullName
    $appPath = Join-Path $installPath 'app.py'
    if (-not (Test-Path -LiteralPath $appPath -PathType Leaf)) {
        throw 'インストール後のapp.pyを確認できませんでした。'
    }
    $launcherPath = Join-Path $installPath 'launcher.pyw'
    if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf)) {
        throw 'インストール後のlauncher.pywを確認できませんでした。'
    }
    $dashboardPath = Join-Path $installPath 'dashboard.py'
    if (-not (Test-Path -LiteralPath $dashboardPath -PathType Leaf)) {
        throw 'インストール後のdashboard.pyを確認できませんでした。'
    }
    Write-InstallLog "source install path: $installPath"

    Set-InstallStage -Name 'shortcut'
    Write-Step 'デスクトップショートカットを作成しています。'
    $shortcutPath = New-AppShortcut -PythonwPath $python.PythonwPath -LauncherPath $launcherPath
    Write-InstallLog "shortcut path: $shortcutPath"
    Write-InstallLog "shortcut TargetPath: $($python.PythonwPath)"
    Write-InstallLog "shortcut Arguments: `"$launcherPath`""

    Set-InstallStage -Name 'launch'
    Write-Step 'ショートカットと同じ方法でアプリを起動しています。'
    $launcherArguments = '"{0}"' -f $launcherPath
    Start-Process -FilePath $python.PythonwPath -ArgumentList $launcherArguments -WorkingDirectory $installPath | Out-Null
    if (-not (Wait-AppRuntimeReady -TimeoutSeconds 15)) {
        throw 'アプリの起動を確認できませんでした。startup.logを確認してください。'
    }

    Write-InstallLog 'application launch result: success'
    Write-Host "`nセットアップが完了しました。" -ForegroundColor Green
    Write-Host "デスクトップの「AC6 WinLoss Tracker」から次回以降も起動できます。"
    Write-Host "ログ: $script:logPath"
} catch {
    $exitCode = 1
    $errorRecord = $_
    $friendlyMessage = [string]$errorRecord.Exception.Message
    if ([string]::IsNullOrWhiteSpace($friendlyMessage)) {
        $friendlyMessage = 'セットアップ中に問題が発生しました。インターネット接続を確認して、もう一度お試しください。'
    }
    try {
        if (Test-Path -LiteralPath $dataPath) {
            Write-InstallLog "stage: $script:currentStage"
            Write-InstallLog "exception type: $($errorRecord.Exception.GetType().FullName)"
            Write-InstallLog "message: $friendlyMessage"
            Write-InstallLog "FullyQualifiedErrorId: $($errorRecord.FullyQualifiedErrorId)"
            Write-InstallLog "stack: $($errorRecord.ScriptStackTrace)"
        }
    } catch {
        # Logging must not replace the user-facing error.
    }
    Write-Host "`nセットアップを完了できませんでした。" -ForegroundColor Red
    Write-Host $friendlyMessage -ForegroundColor Red
    Write-Host '問題が続く場合は、source-install.logを添えて報告してください。' -ForegroundColor Yellow
} finally {
    if ($tempRoot -and (Test-Path -LiteralPath $tempRoot)) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($exitCode -ne 0) {
    exit $exitCode
}
