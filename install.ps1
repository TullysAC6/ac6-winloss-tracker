[CmdletBinding()]
param(
    [ValidatePattern('^v\d+\.\d+\.\d+$')][string]$SourceTag = 'v1.1.0'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$appName = 'AC6 WinLoss Tracker'
$channel = 'stable'
$version = '1.1.0'
$repository = 'TullysAC6/ac6-winloss-tracker'
$releaseCommitUrl = "https://api.github.com/repos/$repository/commits/$SourceTag"
$resolvedCommit = $null
$archiveUrl = $null
$dataPath = Join-Path $env:LOCALAPPDATA 'AC6WinLossTracker'
$script:logPath = Join-Path $dataPath 'source-install.log'
$installPath = Join-Path $env:LOCALAPPDATA 'Programs\AC6WinLossTrackerSource'
$installParent = Split-Path -Parent $installPath
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'Programs\AC6WinLossTrackerRuntime'
$venvPath = Join-Path $runtimeRoot 'venv'
$script:venvBackupPath = Join-Path $runtimeRoot 'venv.previous'
$script:venvCandidatePath = $null
$tempRoot = $null
$python = $null
$preferredPythonMinor = 14
$fallbackPythonMinor = 13
$pythonWingetPackage = 'Python.Python.3.14'
$exitCode = 0
$script:currentStage = 'startup'
$script:sourceSwapped = $false
$script:hadPreviousInstall = $false
$script:backupPath = "$installPath.previous"
$script:shortcutPath = $null
$script:shortcutBackupPath = $null
$script:shortcutExisted = $false
$script:shortcutChanged = $false
$script:previousPythonwPath = $null
$script:previousShortcutArguments = $null
$script:previousShortcutWorkingDirectory = $null
$script:runtimeSwapped = $false
$script:hadPreviousRuntime = $false
$script:installerMutex = $null
$script:installerMutexOwned = $false

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

function New-LaunchId {
    $bytes = New-Object byte[] 24
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Enter-InstallerMutex {
    $createdNew = $false
    $script:installerMutex = New-Object System.Threading.Mutex($false, 'Local\AC6WinLossTrackerInstaller', [ref]$createdNew)
    try {
        $script:installerMutexOwned = $script:installerMutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $script:installerMutexOwned = $true
    }
    if (-not $script:installerMutexOwned) {
        throw '別のAC6 Win/Loss Trackerセットアップが実行中です。完了後にもう一度お試しください。'
    }
    Write-InstallLog 'installer mutex acquired'
}

function Exit-InstallerMutex {
    if ($script:installerMutex) {
        if ($script:installerMutexOwned) {
            try { $script:installerMutex.ReleaseMutex() } catch {}
        }
        $script:installerMutex.Dispose()
        $script:installerMutex = $null
        $script:installerMutexOwned = $false
    }
}

function Get-InstalledRevision {
    $metadataPath = Join-Path $dataPath 'installed-version.json'
    try {
        if (Test-Path -LiteralPath $metadataPath -PathType Leaf) {
            $metadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
            if ([string]$metadata.resolved_commit -match '^[0-9a-fA-F]{40}$') {
                return [string]$metadata.resolved_commit
            }
        }
    } catch {
        Write-InstallLog "installed metadata inspection failed: $($_.Exception.Message)"
    }
    return '(unknown)'
}

function Resolve-StableCommit {
    Write-Step "GitHub Stable $SourceTag の固定リビジョンを確認しています。"
    try {
        # Exactly one unauthenticated API request is used per installer run.
        $response = Invoke-WebRequest -Uri $releaseCommitUrl -UseBasicParsing -TimeoutSec 15 `
            -Headers @{ 'User-Agent' = 'AC6-WinLoss-Tracker-Installer/1.1.0' } `
            -ErrorAction Stop
        $payload = $response.Content | ConvertFrom-Json
        $sha = [string]$payload.sha
        if ($sha -notmatch '^[0-9a-fA-F]{40}$') {
            throw 'GitHubから受信したcommit SHAの形式が正しくありません。現在のTrackerは変更していません。'
        }
        return $sha.ToLowerInvariant()
    } catch {
        $resolveError = $_
        $statusCode = 0
        $retryAfter = $null
        $rateRemaining = $null
        $rateReset = $null
        try {
            $statusCode = [int]$resolveError.Exception.Response.StatusCode
            $retryAfter = $resolveError.Exception.Response.Headers['Retry-After']
            $rateRemaining = $resolveError.Exception.Response.Headers['X-RateLimit-Remaining']
            $rateReset = $resolveError.Exception.Response.Headers['X-RateLimit-Reset']
        } catch {
            # Network exceptions do not always carry an HTTP response.
        }
        if ($statusCode -eq 403 -or $statusCode -eq 429) {
            $installed = Get-InstalledRevision
            $detail = "現在のinstalled revision: $installed"
            if ($retryAfter) { $detail += "; Retry-After: $retryAfter seconds" }
            if ($rateRemaining) { $detail += "; remaining: $rateRemaining" }
            if ($rateReset -match '^\d+$') {
                $resetLocal = [DateTimeOffset]::FromUnixTimeSeconds([int64]$rateReset).LocalDateTime
                $detail += "; reset: $($resetLocal.ToString('yyyy-MM-dd HH:mm:ss'))"
            }
            Write-InstallLog "GitHub rate limit: HTTP $statusCode; $detail"
            throw "GitHubの更新確認リクエスト制限に達しています。現在のTrackerは変更していません。しばらく待ってから同じコマンドを再実行してください。`n$detail"
        }
        throw "GitHub Stable $SourceTag の確認に失敗しました。現在のTrackerは変更していません。インターネット接続とGitHubの状態を確認してください。"
    }
}

function Write-InstalledMetadata {
    param(
        [Parameter(Mandatory = $true)][string]$Commit,
        [Parameter(Mandatory = $true)]$Python
    )
    $metadataPath = Join-Path $dataPath 'installed-version.json'
    $temporaryPath = Join-Path $dataPath 'installed-version.json.tmp'
    $metadata = [ordered]@{
        channel = $channel
        version = $version
        resolved_commit = $Commit
        installed_at = [DateTimeOffset]::UtcNow.ToString('o')
        python_version = [string]$Python.Version
        python_role = [string]$Python.Role
        python_base_runtime = [string]$Python.PythonPath
        python_venv_runtime = [string]$Python.VenvPythonPath
        venv_active = $true
        requirements_lock_sha256 = [string]$Python.RequirementsHash
        dependency_versions = [string]$Python.DependencyVersions
    } | ConvertTo-Json
    [System.IO.File]::WriteAllText(
        $temporaryPath, $metadata, (New-Object System.Text.UTF8Encoding($false))
    )
    Move-Item -LiteralPath $temporaryPath -Destination $metadataPath -Force
}

function Get-SupportedPythonRole {
    param(
        [int]$Major,
        [int]$Minor,
        [string]$ReleaseLevel,
        [string]$GilDisabled
    )

    if ($ReleaseLevel -ne 'final' -or $GilDisabled -ne '0') { return $null }
    if ($Major -eq 3 -and $Minor -eq $preferredPythonMinor) { return 'preferred' }
    if ($Major -eq 3 -and $Minor -eq $fallbackPythonMinor) { return 'fallback' }
    return $null
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
        [string[]]$ArgumentList = @(),
        [hashtable]$Environment = @{}
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
        foreach ($name in $Environment.Keys) {
            $startInfo.EnvironmentVariables[[string]$name] = [string]$Environment[$name]
        }

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

function Test-IsPythonFoundationSigner {
    param($SignedExecutable)
    return (
        $null -ne $SignedExecutable -and
        $null -ne $SignedExecutable.Signature -and
        $SignedExecutable.Signature.Status -eq 'Valid' -and
        $SignedExecutable.Signature.SignatureType -eq 'Authenticode' -and
        $null -ne $SignedExecutable.Signature.SignerCertificate -and
        $SignedExecutable.Signature.SignerCertificate.Subject -match 'Python Software Foundation'
    )
}

function Get-PythonCandidatePaths {
    $candidates = New-Object 'System.Collections.Generic.List[string]'
    $pythonBase = Join-Path $env:LOCALAPPDATA 'Programs\Python'

    try {
        Add-PythonCandidate -List $candidates -Path (Join-Path $pythonBase 'Python314\python.exe')
        Add-PythonCandidate -List $candidates -Path (Join-Path $pythonBase 'Python313\python.exe')
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

    # Output one scalar per line so PowerShell never evaluates Python-provided code.
    $verificationCode = 'import platform,sys,sysconfig; print(sys.version_info.major); print(sys.version_info.minor); print(sys.version_info.micro); print(sys.version_info.releaselevel); print(1 if sysconfig.get_config_var("Py_GIL_DISABLED") else 0); print(sys.executable); print(platform.machine())'
    $result = Invoke-NativeCommand -FilePath $PythonPath -ArgumentList @('-c', $verificationCode)
    Write-InstallLog "Python verification candidate path: $PythonPath"
    Write-InstallLog "Python verification exit code: $($result.ExitCode)"
    Write-InstallLog "Python verification stdout:`n$($result.StdOut)"
    Write-InstallLog "Python verification stderr:`n$($result.StdErr)"
    return $result
}

function Select-SupportedPythonCandidate {
    param([AllowEmptyCollection()][object[]]$Candidates)
    if (-not $Candidates -or $Candidates.Count -eq 0) { return $null }
    return @($Candidates | Sort-Object `
        @{ Expression = { $_.Priority }; Descending = $true }, `
        @{ Expression = { $_.Major }; Descending = $true }, `
        @{ Expression = { $_.Minor }; Descending = $true }, `
        @{ Expression = { $_.Patch }; Descending = $true })[0]
}

function Find-SupportedPython {
    $seen = @{}
    $supported = New-Object 'System.Collections.Generic.List[object]'
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
        if (-not (Test-IsPythonFoundationSigner -SignedExecutable $signedPython)) {
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
            if ($versionLines.Count -ne 7) {
                Write-CandidateSkipped -Kind 'Python' -Path $signedPython.Path -Reason ("runtime check returned {0} lines instead of 7; see Python verification log" -f $versionLines.Count)
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
            $releaseLevel = $versionLines[3].Trim()
            $gilDisabled = $versionLines[4].Trim()
            $role = Get-SupportedPythonRole -Major $major -Minor $minor -ReleaseLevel $releaseLevel -GilDisabled $gilDisabled
            if (-not $role) {
                Write-CandidateSkipped -Kind 'Python' -Path $signedPython.Path -Reason ("unsupported Python series/build: {0}.{1}.{2}, releaselevel={3}, Py_GIL_DISABLED={4}" -f $major, $minor, $micro, $releaseLevel, $gilDisabled)
                continue
            }

            $actualPython = (Resolve-Path -LiteralPath $versionLines[5].Trim()).Path
            if ($actualPython -ne $signedPython.Path) {
                $signedPython = Get-SignedExecutableInfo -Path $actualPython
                if (-not $signedPython) {
                    continue
                }
                if (-not (Test-IsPythonFoundationSigner -SignedExecutable $signedPython)) {
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
            if ((Split-Path -Parent $signedPythonw.Path) -ne (Split-Path -Parent $signedPython.Path)) {
                Write-CandidateSkipped -Kind 'Python' -Path $pythonwPath -Reason 'pythonw.exe is not from the selected Python installation'
                continue
            }

            $supported.Add([PSCustomObject]@{
                PythonPath = $signedPython.Path
                PythonwPath = $signedPythonw.Path
                Version = ('{0}.{1}.{2}' -f $major, $minor, $micro)
                Major = $major
                Minor = $minor
                Patch = $micro
                ReleaseLevel = $releaseLevel
                FreeThreaded = $false
                Architecture = $versionLines[6].Trim()
                Role = $role
                Priority = $(if ($role -eq 'preferred') { 2 } else { 1 })
                SignatureStatus = [string]$signedPython.Signature.Status
                SignerSubject = [string]$signedPython.Signature.SignerCertificate.Subject
            }) | Out-Null
        } catch {
            Write-CandidateSkipped -Kind 'Python' -Path $signedPython.Path -Reason ("verification failed: {0}" -f $_.Exception.Message)
            continue
        }
    }
    return Select-SupportedPythonCandidate -Candidates $supported.ToArray()
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

    $packageId = $pythonWingetPackage
    Write-Step '公式Python 3.14を現在のユーザー用に自動インストールしています。'
    $wingetResult = Invoke-NativeCommand -FilePath $signedWinget.Path -ArgumentList @(
        'install', '--exact', '--id', $packageId, '--source', 'winget', '--scope', 'user', '--silent',
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

function Get-IsolatedPythonEnvironment {
    return @{
        PYTHONNOUSERSITE = '1'
        PYTHONUTF8 = '1'
        PYTHONIOENCODING = 'utf-8'
        PIP_USER = '0'
        PIP_REQUIRE_VIRTUALENV = '1'
    }
}

function New-TrackerVenv {
    param(
        [Parameter(Mandatory = $true)]$BasePython,
        [Parameter(Mandatory = $true)][string]$RequirementsPath,
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$DestinationPath
    )

    Write-Step 'Tracker専用のPython環境を準備しています。'
    if (Test-Path -LiteralPath $DestinationPath) {
        Remove-Item -LiteralPath $DestinationPath -Recurse -Force
    }
    $createResult = Invoke-NativeCommand -FilePath $BasePython.PythonPath -ArgumentList @(
        '-m', 'venv', $DestinationPath
    )
    Write-InstallLog "venv creation command: $($createResult.Command)"
    Write-InstallLog "venv creation exit code: $($createResult.ExitCode)"
    Write-InstallLog "venv creation stdout:`n$($createResult.StdOut)"
    Write-InstallLog "venv creation stderr:`n$($createResult.StdErr)"
    if ($createResult.ExitCode -ne 0) {
        throw '[ENV-VENV-CREATE] Tracker専用Python環境の作成に失敗しました。現在のTrackerは変更していません。'
    }

    $venvPython = Join-Path $DestinationPath 'Scripts\python.exe'
    $venvPythonw = Join-Path $DestinationPath 'Scripts\pythonw.exe'
    foreach ($requiredRuntime in @($venvPython, $venvPythonw)) {
        if (-not (Test-Path -LiteralPath $requiredRuntime -PathType Leaf)) {
            throw "[ENV-VENV-INCOMPLETE] Tracker専用Python環境に $requiredRuntime がありません。"
        }
    }

    $isolatedEnvironment = Get-IsolatedPythonEnvironment
    $pipResult = Invoke-NativeCommand -FilePath $venvPython -Environment $isolatedEnvironment -ArgumentList @(
        '-m', 'pip', 'install', '--require-hashes',
        '--only-binary=:all:', '-r', $RequirementsPath
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
        throw '[ENV-DEPENDENCY-MISSING] 必要なPythonライブラリのインストールに失敗しました。インターネット接続を確認してください。'
    }
    Write-InstallLog 'dedicated venv pip install result: success'

    $pipCheckResult = Invoke-NativeCommand -FilePath $venvPython -Environment $isolatedEnvironment -ArgumentList @('-m', 'pip', 'check')
    Write-InstallLog "pip check exit code: $($pipCheckResult.ExitCode)"
    Write-InstallLog "pip check stdout:`n$($pipCheckResult.StdOut)"
    Write-InstallLog "pip check stderr:`n$($pipCheckResult.StdErr)"
    if ($pipCheckResult.ExitCode -ne 0) {
        throw '[ENV-DEPENDENCY-CHECK] Tracker専用Python環境の依存関係確認に失敗しました。'
    }

    $dependencyCode = 'import importlib.metadata as md,json,platform,site,struct,sys,sysconfig; import mss, ttkbootstrap, PIL, tkinter, sqlite3; print(json.dumps({"major":sys.version_info.major,"minor":sys.version_info.minor,"micro":sys.version_info.micro,"releaselevel":sys.version_info.releaselevel,"gil_disabled":bool(sysconfig.get_config_var("Py_GIL_DISABLED")),"bits":struct.calcsize("P")*8,"machine":platform.machine(),"executable":sys.executable,"prefix":sys.prefix,"base_prefix":sys.base_prefix,"user_site_enabled":site.ENABLE_USER_SITE,"mss_version":md.version("mss"),"ttkbootstrap_version":md.version("ttkbootstrap"),"pillow_version":md.version("Pillow"),"mss_path":mss.__file__,"ttkbootstrap_path":ttkbootstrap.__file__,"pillow_path":PIL.__file__},separators=(",",":")))'
    $dependencyResult = Invoke-NativeCommand -FilePath $venvPython -Environment $isolatedEnvironment -ArgumentList @('-c', $dependencyCode)
    Write-InstallLog "dependency verification command: $($dependencyResult.Command)"
    Write-InstallLog "dependency verification exit code: $($dependencyResult.ExitCode)"
    Write-InstallLog "dependency verification stdout:`n$($dependencyResult.StdOut)"
    Write-InstallLog "dependency verification stderr:`n$($dependencyResult.StdErr)"
    if ($dependencyResult.ExitCode -ne 0) {
        throw '[ENV-DEPENDENCY-IMPORT] インストールしたPythonライブラリを読み込めませんでした。source-install.logを添えて報告してください。'
    }
    try {
        $runtimeInfo = $dependencyResult.StdOut.Trim() | ConvertFrom-Json
    } catch {
        throw '[ENV-VENV-VERIFY] Tracker専用Python環境の検証結果を確認できませんでした。'
    }

    $expectedBasePrefix = (Split-Path -Parent $BasePython.PythonPath).TrimEnd('\')
    $actualBasePrefix = ([string]$runtimeInfo.base_prefix).TrimEnd('\')
    $resolvedVenvPython = (Resolve-Path -LiteralPath $venvPython).Path
    if ([int]$runtimeInfo.major -ne [int]$BasePython.Major -or
        [int]$runtimeInfo.minor -ne [int]$BasePython.Minor -or
        [string]$runtimeInfo.releaselevel -ne 'final' -or
        [bool]$runtimeInfo.gil_disabled -or
        [int]$runtimeInfo.bits -ne 64 -or
        [string]$runtimeInfo.executable -ne $resolvedVenvPython -or
        [string]$runtimeInfo.prefix -eq [string]$runtimeInfo.base_prefix -or
        $actualBasePrefix -ne $expectedBasePrefix -or
        [bool]$runtimeInfo.user_site_enabled) {
        throw '[ENV-VENV-POLICY] Tracker専用Python環境が必要なRuntimeポリシーを満たしていません。'
    }
    $expectedVersions = @{
        mss_version = '10.2.0'
        ttkbootstrap_version = '2.2.2'
        pillow_version = '12.3.0'
    }
    foreach ($propertyName in $expectedVersions.Keys) {
        if ([string]$runtimeInfo.$propertyName -ne [string]$expectedVersions[$propertyName]) {
            throw "[ENV-DEPENDENCY-VERSION] $propertyName がrequirements.lockと一致しません。"
        }
    }
    $venvPrefix = ([string]$runtimeInfo.prefix).TrimEnd('\') + '\'
    foreach ($propertyName in @('mss_path', 'ttkbootstrap_path', 'pillow_path')) {
        if (-not ([string]$runtimeInfo.$propertyName).StartsWith($venvPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "[ENV-DEPENDENCY-ISOLATION] $propertyName がTracker専用Python環境の外から読み込まれました。"
        }
    }

    $compileResult = Invoke-NativeCommand -FilePath $venvPython -Environment $isolatedEnvironment -ArgumentList @('-m', 'compileall', '-q', $SourcePath)
    if ($compileResult.ExitCode -ne 0) {
        throw '[ENV-RUNTIME-SMOKE] TrackerソースのRuntime確認に失敗しました。'
    }
    $smokeCode = 'import sys; sys.path.insert(0,sys.argv[1]); import app_paths,config_utils,result_detector,result_gate,history_store; print("tracker-runtime-smoke-ok")'
    $smokeResult = Invoke-NativeCommand -FilePath $venvPython -Environment $isolatedEnvironment -ArgumentList @('-c', $smokeCode, $SourcePath)
    Write-InstallLog "runtime smoke exit code: $($smokeResult.ExitCode)"
    Write-InstallLog "runtime smoke stdout:`n$($smokeResult.StdOut)"
    Write-InstallLog "runtime smoke stderr:`n$($smokeResult.StdErr)"
    if ($smokeResult.ExitCode -ne 0 -or $smokeResult.StdOut.Trim() -ne 'tracker-runtime-smoke-ok') {
        throw '[ENV-RUNTIME-SMOKE] TrackerモジュールのRuntime確認に失敗しました。'
    }

    $requirementsHash = (Get-FileHash -LiteralPath $RequirementsPath -Algorithm SHA256).Hash.ToUpperInvariant()
    Write-InstallLog "dependency verification result: success ($($dependencyResult.StdOut.Trim()))"
    Write-InstallLog "requirements.lock SHA-256: $requirementsHash"
    Write-InstallLog 'PYTHONNOUSERSITE isolation: enabled; user site disabled'
    return [PSCustomObject]@{
        VenvPythonPath = $venvPython
        VenvPythonwPath = $venvPythonw
        RequirementsHash = $requirementsHash
        DependencyVersions = 'mss=10.2.0; ttkbootstrap=2.2.2; Pillow=12.3.0'
    }
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
    if (Test-Path -LiteralPath $script:backupPath) {
        Remove-Item -LiteralPath $script:backupPath -Recurse -Force
    }

    $script:hadPreviousInstall = Test-Path -LiteralPath $installPath
    if ($script:hadPreviousInstall) {
        Move-Item -LiteralPath $installPath -Destination $script:backupPath
    }

    try {
        Move-Item -LiteralPath $SourcePath -Destination $installPath
        $script:sourceSwapped = $true
    } catch {
        if ($script:hadPreviousInstall -and -not (Test-Path -LiteralPath $installPath) -and (Test-Path -LiteralPath $script:backupPath)) {
            Move-Item -LiteralPath $script:backupPath -Destination $installPath
        }
        throw
    }
}

function Complete-SourceInstall {
    try {
        if (Test-Path -LiteralPath $script:backupPath) {
            Remove-Item -LiteralPath $script:backupPath -Recurse -Force
        }
    } catch {
        Write-InstallLog "previous source cleanup warning: $($_.Exception.Message)"
    }
    $script:sourceSwapped = $false
}

function Restore-PreviousSource {
    if (-not $script:sourceSwapped) { return }
    Write-InstallLog 'transaction rollback started'
    if (Test-Path -LiteralPath $installPath) {
        Remove-Item -LiteralPath $installPath -Recurse -Force
    }
    if ($script:hadPreviousInstall -and (Test-Path -LiteralPath $script:backupPath)) {
        Move-Item -LiteralPath $script:backupPath -Destination $installPath
        Write-InstallLog 'transaction rollback restored previous source'
    } else {
        Write-InstallLog 'transaction rollback removed incomplete first install'
    }
    $script:sourceSwapped = $false
}

function Install-TrackerRuntime {
    param([Parameter(Mandatory = $true)][string]$CandidatePath)

    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
    if (Test-Path -LiteralPath $script:venvBackupPath) {
        Remove-Item -LiteralPath $script:venvBackupPath -Recurse -Force
    }
    $script:hadPreviousRuntime = Test-Path -LiteralPath $venvPath -PathType Container
    if ($script:hadPreviousRuntime) {
        Move-Item -LiteralPath $venvPath -Destination $script:venvBackupPath
    }
    try {
        Move-Item -LiteralPath $CandidatePath -Destination $venvPath
        $script:runtimeSwapped = $true
    } catch {
        if ($script:hadPreviousRuntime -and -not (Test-Path -LiteralPath $venvPath) -and (Test-Path -LiteralPath $script:venvBackupPath)) {
            Move-Item -LiteralPath $script:venvBackupPath -Destination $venvPath
        }
        throw
    }
}

function Complete-TrackerRuntimeInstall {
    try {
        if (Test-Path -LiteralPath $script:venvBackupPath) {
            Remove-Item -LiteralPath $script:venvBackupPath -Recurse -Force
        }
    } catch {
        Write-InstallLog "previous dedicated runtime cleanup warning: $($_.Exception.Message)"
    }
    $script:runtimeSwapped = $false
}

function Restore-PreviousTrackerRuntime {
    if (-not $script:runtimeSwapped) { return }
    if (Test-Path -LiteralPath $venvPath) {
        Remove-Item -LiteralPath $venvPath -Recurse -Force
    }
    if ($script:hadPreviousRuntime -and (Test-Path -LiteralPath $script:venvBackupPath)) {
        Move-Item -LiteralPath $script:venvBackupPath -Destination $venvPath
        Write-InstallLog 'transaction rollback restored previous dedicated runtime'
    } else {
        Write-InstallLog 'transaction rollback removed incomplete first dedicated runtime'
    }
    $script:runtimeSwapped = $false
}

function Wait-AppRuntimeReady {
    param(
        [Parameter(Mandatory = $true)][string]$ExpectedLaunchId,
        [int]$TimeoutSeconds = 15
    )

    $runtimePath = Join-Path $dataPath '.runtime.json'
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            if (Test-Path -LiteralPath $runtimePath -PathType Leaf) {
                $runtime = Get-Content -LiteralPath $runtimePath -Raw -ErrorAction Stop | ConvertFrom-Json
                $runtimePid = [int]$runtime.pid
                $runtimePort = [int]$runtime.port
                $runtimeLaunchId = [string]$runtime.launch_id
                if ($runtimeLaunchId -ceq $ExpectedLaunchId -and $runtimePid -gt 0 -and $runtimePort -ge 1 -and $runtimePort -le 65535) {
                    $runtimeProcess = Get-Process -Id $runtimePid -ErrorAction Stop
                    if ($runtimeProcess -and -not $runtimeProcess.HasExited) {
                        $response = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/health" -f $runtimePort) -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
                        $health = $response.Content | ConvertFrom-Json
                        if ([int]$response.StatusCode -eq 200 -and $health.ok -eq $true) {
                            Write-InstallLog "application runtime path: $runtimePath"
                            Write-InstallLog "application runtime PID: $runtimePid"
                            Write-InstallLog "application runtime port: $runtimePort"
                            Write-InstallLog "application launch identity: verified"
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

function Backup-AppShortcut {
    $desktopPath = [Environment]::GetFolderPath('Desktop')
    if ([string]::IsNullOrWhiteSpace($desktopPath)) {
        throw 'デスクトップの場所を確認できなかったため、ショートカットを保護できませんでした。'
    }
    $script:shortcutPath = Join-Path $desktopPath 'AC6 WinLoss Tracker.lnk'
    $script:shortcutBackupPath = Join-Path $tempRoot 'shortcut.previous.lnk'
    $script:shortcutExisted = Test-Path -LiteralPath $script:shortcutPath -PathType Leaf
    if ($script:shortcutExisted) {
        Copy-Item -LiteralPath $script:shortcutPath -Destination $script:shortcutBackupPath -Force
        $shell = New-Object -ComObject WScript.Shell
        $oldShortcut = $null
        try {
            $oldShortcut = $shell.CreateShortcut($script:shortcutPath)
            $script:previousPythonwPath = [string]$oldShortcut.TargetPath
            $script:previousShortcutArguments = [string]$oldShortcut.Arguments
            $script:previousShortcutWorkingDirectory = [string]$oldShortcut.WorkingDirectory
        } finally {
            if ($oldShortcut) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($oldShortcut) }
            [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
        }
        Write-InstallLog 'previous shortcut backed up for transaction rollback'
    }
}

function Restore-AppShortcut {
    if (-not $script:shortcutChanged) { return }
    if ($script:shortcutExisted -and (Test-Path -LiteralPath $script:shortcutBackupPath -PathType Leaf)) {
        Copy-Item -LiteralPath $script:shortcutBackupPath -Destination $script:shortcutPath -Force
        Write-InstallLog 'transaction rollback restored previous shortcut'
    } elseif ($script:shortcutPath -and (Test-Path -LiteralPath $script:shortcutPath -PathType Leaf)) {
        Remove-Item -LiteralPath $script:shortcutPath -Force
        Write-InstallLog 'transaction rollback removed new shortcut'
    }
    $script:shortcutChanged = $false
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
        $shortcut.Arguments = '-s "{0}"' -f $LauncherPath
        $shortcut.WorkingDirectory = $installPath
        $shortcut.Description = 'AC6 Win/Loss Tracker Stable'
        $shortcut.Save()
        $script:shortcutChanged = $true
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
    Write-InstallLog "Installer channel: $channel"
    Write-InstallLog "Installer version: $version"
    Write-InstallLog "Windows version: $([Environment]::OSVersion.VersionString)"
    Set-InstallStage -Name 'startup'
    Set-InstallStage -Name 'installer-lock'
    Enter-InstallerMutex

    Set-InstallStage -Name 'revision-resolve'
    $resolvedCommit = Resolve-StableCommit
    $archiveUrl = "https://github.com/$repository/archive/$resolvedCommit.zip"
    Write-InstallLog "resolved Stable tag: $SourceTag"
    Write-InstallLog "resolved Stable revision: $resolvedCommit"

    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('AC6WinLossTrackerSource-' + [Guid]::NewGuid().ToString('N'))
    $zipPath = Join-Path $tempRoot 'source.zip'
    $extractPath = Join-Path $tempRoot 'extracted'
    New-Item -ItemType Directory -Path $extractPath -Force | Out-Null

    Set-InstallStage -Name 'source-download'
    Write-Step 'GitHubからStableソースをHTTPSで取得し、内容を確認しています。'
    try {
        Invoke-WebRequest -Uri $archiveUrl -OutFile $zipPath -UseBasicParsing -TimeoutSec 60 `
            -Headers @{ 'User-Agent' = 'AC6-WinLoss-Tracker-Installer/1.1.0' }
        if (-not (Test-Path -LiteralPath $zipPath -PathType Leaf) -or (Get-Item -LiteralPath $zipPath).Length -le 0) {
            throw 'downloaded archive is empty'
        }
        Expand-Archive -LiteralPath $zipPath -DestinationPath $extractPath -Force
    } catch {
        throw 'ソースコードの取得または展開に失敗しました。現在のTrackerは変更していません。インターネット接続とGitHubの状態を確認してください。'
    }

    $repositoryName = ($repository -split '/')[-1]
    $expectedSourceRoot = Join-Path $extractPath ("{0}-{1}" -f $repositoryName, $resolvedCommit)
    if (-not (Test-Path -LiteralPath $expectedSourceRoot -PathType Container)) {
        throw '取得したZIPの内容を確認できませんでした。現在のTrackerは変更していません。'
    }
    $sourceRoot = Get-Item -LiteralPath $expectedSourceRoot
    foreach ($requiredFile in @('app.py', 'app_paths.py', 'launcher.pyw', 'dashboard.py', 'requirements.lock', 'uninstall.ps1')) {
        if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot.FullName $requiredFile) -PathType Leaf)) {
            throw "取得したZIPに必要なファイル $requiredFile がありません。現在のTrackerは変更していません。"
        }
    }

    $versionSource = Get-Content -LiteralPath (Join-Path $sourceRoot.FullName 'app_paths.py') -Raw
    if ($versionSource -notmatch ('(?m)^VERSION\s*=\s*["'']{0}["'']\s*$' -f [Regex]::Escape($version))) {
        throw "取得したソースのバージョンがStable $version と一致しません。現在のTrackerは変更していません。"
    }

    $forbiddenFiles = @(Get-ChildItem -LiteralPath $sourceRoot.FullName -Recurse -File |
        Where-Object { $_.Extension -match '(?i)^\.(exe|com|scr|msi|msix|pfx|p12)$' })
    if ($forbiddenFiles.Count -gt 0) {
        throw '取得したソースに、配布対象外の実行バイナリまたは証明書ファイルが含まれていました。現在のTrackerは変更していません。'
    }
    Write-InstallLog "archive validation: success; immutable revision $resolvedCommit"
    Write-InstallLog "Python selection: preferred=3.$preferredPythonMinor; fallback=3.$fallbackPythonMinor; new install=$pythonWingetPackage"

    Set-InstallStage -Name 'python-discovery'
    Write-Step 'Stableで検証済みのPSF署名Python Runtimeを探しています。'
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
        throw '[ENV-PYTHON-UNSUPPORTED] PSF署名済みのPython 3.14または3.13（final・通常GIL）を確認できませんでした。install.ps1を再実行してください。'
    }

    Write-Host "Python $($python.Version) ($($python.Role)): $($python.PythonPath)"
    Write-InstallLog "selected Python version: $($python.Version)"
    Write-InstallLog "selected Python path: $($python.PythonPath)"
    Write-InstallLog "selected Pythonw path: $($python.PythonwPath)"
    Write-InstallLog "selected Python Authenticode Status: $($python.SignatureStatus)"
    Write-InstallLog "selected Python signer: $($python.SignerSubject)"
    Write-InstallLog "selected Python role: $($python.Role)"
    Write-InstallLog "selected Python free-threaded: $($python.FreeThreaded)"
    Write-InstallLog "selected Python architecture: $($python.Architecture)"

    Set-InstallStage -Name 'venv-prepare'
    $script:venvCandidatePath = Join-Path $tempRoot 'venv.candidate'
    $preparedRuntime = New-TrackerVenv -BasePython $python `
        -RequirementsPath (Join-Path $sourceRoot.FullName 'requirements.lock') `
        -SourcePath $sourceRoot.FullName -DestinationPath $script:venvCandidatePath
    Set-InstallStage -Name 'python-verification'
    $confirmedPython = Find-SupportedPython
    if (-not $confirmedPython -or
        $confirmedPython.PythonPath -ne $python.PythonPath -or
        $confirmedPython.PythonwPath -ne $python.PythonwPath -or
        $confirmedPython.Version -ne $python.Version -or
        $confirmedPython.Role -ne $python.Role) {
        throw '[ENV-PYTHON-UNSUPPORTED] 依存確認後にselected Python Runtimeの完全性を再確認できませんでした。現在のTrackerは変更していません。'
    }
    $python = $confirmedPython
    Write-InstallLog 'selected base Python post-venv validation: success'
    Backup-AppShortcut

    if (Test-Path -LiteralPath $installPath -PathType Container) {
        Set-InstallStage -Name 'stop-running-app'
        Stop-RunningTracker
    }

    Set-InstallStage -Name 'runtime-install'
    Install-TrackerRuntime -CandidatePath $script:venvCandidatePath
    $python | Add-Member -NotePropertyName VenvPythonPath -NotePropertyValue (Join-Path $venvPath 'Scripts\python.exe') -Force
    $python | Add-Member -NotePropertyName VenvPythonwPath -NotePropertyValue (Join-Path $venvPath 'Scripts\pythonw.exe') -Force
    $python | Add-Member -NotePropertyName RequirementsHash -NotePropertyValue $preparedRuntime.RequirementsHash -Force
    $python | Add-Member -NotePropertyName DependencyVersions -NotePropertyValue $preparedRuntime.DependencyVersions -Force

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
    $shortcutPath = New-AppShortcut -PythonwPath $python.VenvPythonwPath -LauncherPath $launcherPath
    Write-InstallLog "shortcut path: $shortcutPath"
    Write-InstallLog "shortcut TargetPath: $($python.VenvPythonwPath)"
    Write-InstallLog "shortcut Arguments: -s `"$launcherPath`""

    Set-InstallStage -Name 'launch'
    Write-Step 'ショートカットと同じ方法でアプリを起動しています。'
    $launchId = New-LaunchId
    $launcherArguments = '-s "{0}" --launch-id {1}' -f $launcherPath, $launchId
    Start-Process -FilePath $python.VenvPythonwPath -ArgumentList $launcherArguments -WorkingDirectory $installPath | Out-Null
    if (-not (Wait-AppRuntimeReady -ExpectedLaunchId $launchId -TimeoutSeconds 15)) {
        throw 'アプリの起動を確認できませんでした。startup.logを確認してください。'
    }

    Write-InstallLog 'application launch result: success'
    Write-InstalledMetadata -Commit $resolvedCommit -Python $python
    Write-InstallLog "installed revision: $resolvedCommit"
    Complete-SourceInstall
    Complete-TrackerRuntimeInstall
    Write-Host "`nセットアップが完了しました。" -ForegroundColor Green
    Write-Host "デスクトップの「AC6 WinLoss Tracker」から次回以降も起動できます。"
    Write-Host "ログ: $script:logPath"
} catch {
    $exitCode = 1
    $errorRecord = $_
    if ($script:runtimeSwapped -or $script:sourceSwapped -or $script:shortcutChanged) {
        try {
            Stop-RunningTracker
        } catch {
            try { Write-InstallLog "rollback shutdown warning: $($_.Exception.Message)" } catch {}
        }
        try {
            Restore-PreviousSource
        } catch {
            try { Write-InstallLog "source rollback failed: $($_.Exception.Message)" } catch {}
        }
        try {
            Restore-PreviousTrackerRuntime
        } catch {
            try { Write-InstallLog "runtime rollback failed: $($_.Exception.Message)" } catch {}
        }
        try {
            Restore-AppShortcut
        } catch {
            try { Write-InstallLog "shortcut rollback failed: $($_.Exception.Message)" } catch {}
        }
        try {
            if ($script:hadPreviousInstall -and $python -and (Test-Path -LiteralPath (Join-Path $installPath 'launcher.pyw'))) {
                $previousLauncher = Join-Path $installPath 'launcher.pyw'
                $rollbackPythonw = if ($script:previousPythonwPath -and (Test-Path -LiteralPath $script:previousPythonwPath -PathType Leaf)) { $script:previousPythonwPath } else { $python.PythonwPath }
                $rollbackArguments = if ($script:previousShortcutArguments) { $script:previousShortcutArguments } else { '"{0}"' -f $previousLauncher }
                $rollbackWorkingDirectory = if ($script:previousShortcutWorkingDirectory) { $script:previousShortcutWorkingDirectory } else { $installPath }
                Start-Process -FilePath $rollbackPythonw -ArgumentList $rollbackArguments -WorkingDirectory $rollbackWorkingDirectory | Out-Null
                Write-InstallLog 'transaction rollback restarted previous source'
            }
        } catch {
            try { Write-InstallLog "transaction rollback restart failed: $($_.Exception.Message)" } catch {}
        }
    } elseif ($script:shortcutChanged) {
        try { Restore-AppShortcut } catch {
            try { Write-InstallLog "shortcut rollback failed: $($_.Exception.Message)" } catch {}
        }
    }
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
    Exit-InstallerMutex
}

if ($exitCode -ne 0) {
    exit $exitCode
}
