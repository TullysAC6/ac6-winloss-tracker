[CmdletBinding(DefaultParameterSetName = 'Stable')]
param(
    [Parameter(ParameterSetName = 'Stable')]
    [ValidatePattern('^v\d+\.\d+\.\d+$')][string]$SourceTag = 'v1.2.0',
    [Parameter(Mandatory = $true, ParameterSetName = 'Candidate')]
    [ValidatePattern('\A[0-9a-fA-F]{40}\z')][string]$SourceCommit
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$appName = 'AC6 WinLoss Tracker'
$channel = 'stable'
$version = '1.2.0'
$repository = 'TullysAC6/ac6-winloss-tracker'
$sourceKind = 'tag'
$sourceRef = $SourceTag
if ($PSCmdlet.ParameterSetName -eq 'Candidate') {
    $channel = 'candidate'
    $sourceKind = 'commit'
    $sourceRef = $SourceCommit.ToLowerInvariant()
}
$releaseCommitUrl = "https://api.github.com/repos/$repository/commits/$sourceRef"
$resolvedCommit = $null
$archiveUrl = $null
$dataPath = Join-Path $env:LOCALAPPDATA 'AC6WinLossTracker'
$script:logPath = Join-Path $dataPath 'source-install.log'
$ownedRoot = Join-Path $env:LOCALAPPDATA 'Programs\AC6WinLossTracker'
$legacyPath = Join-Path $env:LOCALAPPDATA 'Programs\AC6WinLossTrackerSource'
$installPath = Join-Path $ownedRoot 'app'
$installParent = Split-Path -Parent $installPath
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
$script:previousLauncherPath = $null
$script:previousRunning = $false
$script:stopAttempted = $false
$script:committed = $false
$script:newEnvironment = $null
$script:activeEnvironment = $null
$script:installerMutex = $null
$script:ownsInstallerMutex = $false
$script:metadataBackup = $null
$script:metadataExisted = $false
$script:metadataCaptured = $false
$script:rollbackUnsafe = $false
$script:installNonce = [Guid]::NewGuid().ToString('N') + [Guid]::NewGuid().ToString('N')
$script:launcherProcess = $null

function Assert-InstallPath {
    param([string]$Path)
    $full = [IO.Path]::GetFullPath($Path)
    $owned = [IO.Path]::GetFullPath($ownedRoot)
    $legacy = [IO.Path]::GetFullPath($legacyPath)
    if ($full -ne $owned -and -not $full.StartsWith($owned + '\', [StringComparison]::OrdinalIgnoreCase) -and
        $full -ne $legacy -and $full -ne "$legacy.previous") { throw 'Unsafe installation path' }
    $cursor = $full
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Installation path traverses a reparse point: $cursor"
            }
        }
        $cursor = Split-Path -Parent $cursor
    }
    return $full
}

function Remove-OwnedInstallPath {
    param([string]$Path)
    $full = Assert-InstallPath $Path
    if (Test-Path -LiteralPath $full) {
        if (@(Get-ChildItem -LiteralPath $full -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) { throw 'Unsafe reparse point inside installation' }
        Remove-Item -LiteralPath $full -Recurse -Force
    }
}

function Enter-InstallerMutex {
    $script:installerMutex = New-Object Threading.Mutex($false, 'Local\AC6WinLossTrackerInstaller')
    try { $script:ownsInstallerMutex = $script:installerMutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $script:ownsInstallerMutex = $true }
    if (-not $script:ownsInstallerMutex) { throw '[ENV-INSTALL-BUSY] Another installer or uninstaller is running. Retry after it exits.' }
}

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
    Write-Step "GitHub $channel $sourceRef の固定リビジョンを確認しています。"
    try {
        # Exactly one unauthenticated API request is used per installer run.
        $response = Invoke-WebRequest -Uri $releaseCommitUrl -UseBasicParsing -TimeoutSec 15 `
            -Headers @{ 'User-Agent' = 'AC6-WinLoss-Tracker-Installer/1.2.0' } `
            -ErrorAction Stop
        $payload = $response.Content | ConvertFrom-Json
        $sha = [string]$payload.sha
        if ($sha -notmatch '\A[0-9a-fA-F]{40}\z') {
            throw 'GitHubから受信したcommit SHAの形式が正しくありません。現在のTrackerは変更していません。'
        }
        if ($sourceKind -eq 'commit' -and $sha -ine $sourceRef) {
            throw 'GitHub returned a different commit from the requested candidate.'
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
        throw "GitHub $channel $sourceRef の確認に失敗しました。現在のTrackerは変更していません。インターネット接続とGitHubの状態を確認してください。"
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
        environment_path = $script:activeEnvironment
        base_python_path = [string]$Python.BasePythonPath
        requirements_sha256 = $script:lockHash
    }
    if ($sourceKind -eq 'commit') {
        $metadata['source_kind'] = $sourceKind
        $metadata['source_ref'] = $sourceRef
    }
    $metadata = $metadata | ConvertTo-Json
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
        [int]$TimeoutSeconds = 300
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
        foreach ($name in @('PYTHONPATH','PYTHONHOME','PYTHONUSERBASE','PYTHONSTARTUP','PYTHONOPTIMIZE','PYTHONINSPECT','PYTHONEXECUTABLE','PYTHONPLATLIBDIR','__PYVENV_LAUNCHER__','AC6_LAUNCH_NONCE')) {
            $startInfo.EnvironmentVariables.Remove($name)
        }
        $startInfo.EnvironmentVariables['PYTHONUTF8'] = '1'
        $startInfo.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
        # --isolated alone still reads global/site pip.ini. Never allow a host
        # target/prefix/index configuration to redirect the locked install.
        $startInfo.EnvironmentVariables['PIP_CONFIG_FILE'] = 'nul'
        # Bypass the Windows redirector so the timeout handle owns Python.
        $cfgPath = Join-Path (Split-Path -Parent (Split-Path -Parent $FilePath)) 'pyvenv.cfg'
        if ((Split-Path -Leaf (Split-Path -Parent $FilePath)) -eq 'Scripts' -and (Test-Path -LiteralPath $cfgPath)) {
            $cfg = Get-Content -LiteralPath $cfgPath -Raw
            $match = [regex]::Match($cfg, '(?m)^home\s*=\s*(.+)\s*$')
            if (-not $match.Success) { throw '[ENV-VENV-BROKEN] Missing base Python. Re-run the installer.' }
            $baseExe = Join-Path $match.Groups[1].Value.Trim() (Split-Path -Leaf $FilePath)
            if (-not (Test-Path -LiteralPath $baseExe -PathType Leaf)) { throw '[ENV-VENV-BROKEN] Missing base Python. Re-run the installer.' }
            $startInfo.FileName = $baseExe
            $startInfo.EnvironmentVariables['__PYVENV_LAUNCHER__'] = $FilePath
        }

        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $startInfo
        if (-not $process.Start()) {
            throw 'native process did not start'
        }

        # Read both streams asynchronously so neither pipe can block the process.
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill()
            [void]$process.WaitForExit(5000)
            throw "Native command exceeded ${TimeoutSeconds}s"
        }
        if (-not $stdoutTask.Wait(5000) -or -not $stderrTask.Wait(5000)) { throw 'Native output pipes did not close' }
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

function Test-AppEnvironment {
    param([string]$EnvironmentPath, [string]$RequirementsPath)
    [void](Assert-InstallPath $EnvironmentPath)
    $envPython = Join-Path $EnvironmentPath 'Scripts\python.exe'
    $code = @'
import sys,site,re,json,importlib,importlib.metadata
from pathlib import Path
root=Path(sys.argv[1]).resolve()
assert Path(sys.prefix).resolve()==root and sys.prefix!=sys.base_prefix
assert site.ENABLE_USER_SITE is False
assert sys.version_info[:2] in ((3,13),(3,14))
assert not any('site-packages' in p.lower() and not Path(p).resolve().is_relative_to(root) for p in sys.path)
modules={'mss':'mss','pillow':'PIL','ttkbootstrap':'ttkbootstrap','windows-capture':'windows_capture','numpy':'numpy','opencv-python':'cv2'}
for name,version in re.findall(r'^([a-zA-Z0-9_-]+)==([^\s;]+)',Path(sys.argv[2]).read_text(),re.M):
 assert importlib.metadata.version(name)==version,(name,version)
 module=importlib.import_module(modules[name.lower()])
 assert Path(module.__file__).resolve().is_relative_to(root),name
import tkinter,sqlite3
print(json.dumps({'prefix':sys.prefix,'base_prefix':sys.base_prefix,'version':sys.version}))
'@
    $check = Invoke-NativeCommand -FilePath $envPython -ArgumentList @('-c',$code,$EnvironmentPath,$RequirementsPath)
    if ($check.ExitCode -ne 0) { throw "[ENV-VENV-BROKEN] Environment verification failed. Re-run the installer. $($check.StdErr)" }
    $pipCheck = Invoke-NativeCommand -FilePath $envPython -ArgumentList @('-m','pip','--isolated','check')
    if ($pipCheck.ExitCode -ne 0) { throw '[ENV-DEPENDENCY-MISSING] pip check failed.' }
    Write-InstallLog "app-local environment verified: $($check.StdOut.Trim())"
}

function New-AppEnvironment {
    param($BasePython, [string]$RequirementsPath)
    $script:lockHash = (Get-FileHash -LiteralPath $RequirementsPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $canonicalEnvironmentPath = Join-Path $ownedRoot ('venv\' + $script:lockHash)
    $environmentPath = $canonicalEnvironmentPath
    # A repaired environment has its own final path. Reuse the committed choice
    # on the next same-lock update instead of rebuilding the canonical folder.
    try {
        $installed = Get-Content -LiteralPath (Join-Path $dataPath 'installed-version.json') -Raw | ConvertFrom-Json
        if ($installed.PSObject.Properties['environment_path'] -and $installed.PSObject.Properties['requirements_sha256'] -and
            $installed.requirements_sha256 -eq $script:lockHash) {
            $candidate = [IO.Path]::GetFullPath([string]$installed.environment_path)
            if ($candidate -eq $canonicalEnvironmentPath -or $candidate -match ('^' + [regex]::Escape($canonicalEnvironmentPath) + '-[0-9a-f]{32}$')) {
                [void](Assert-InstallPath $candidate)
                if (Test-Path -LiteralPath $candidate -PathType Container) { $environmentPath = $candidate }
            }
        }
    } catch { Write-InstallLog 'No reusable committed environment choice' }
    [void](Assert-InstallPath $environmentPath)
    if (Test-Path -LiteralPath $environmentPath) {
        try {
            $marker = Get-Content -LiteralPath (Join-Path $environmentPath '.ac6-environment.json') -Raw | ConvertFrom-Json
            if ($marker.lock_sha256 -ne $script:lockHash -or $marker.base_python -ne $BasePython.PythonPath) { throw 'Environment identity differs' }
            Test-AppEnvironment -EnvironmentPath $environmentPath -RequirementsPath $RequirementsPath
            $script:activeEnvironment = $environmentPath
            return $environmentPath
        } catch {
            # Do not mutate or relocate a potentially active environment. Build
            # a replacement in its permanent path and retain rollback choice.
            Write-InstallLog 'Existing environment not reusable; building a separate repair environment'
            $environmentPath = $canonicalEnvironmentPath + '-' + [Guid]::NewGuid().ToString('N')
        }
    }
    [void](Assert-InstallPath $environmentPath)
    New-Item -ItemType Directory -Path $environmentPath -ErrorAction Stop | Out-Null
    $script:newEnvironment = $environmentPath
    Set-InstallStage -Name 'venv-create'
    $created = Invoke-NativeCommand -FilePath $BasePython.PythonPath -ArgumentList @('-m','venv',$environmentPath)
    if ($created.ExitCode -ne 0) { throw "[ENV-VENV-CREATE] App-local environment creation failed. $($created.StdErr)" }
    Set-InstallStage -Name 'pip-install'
    Invoke-PipInstall -PythonPath (Join-Path $environmentPath 'Scripts\python.exe') -RequirementsPath $RequirementsPath
    Set-InstallStage -Name 'venv-verify'
    Test-AppEnvironment -EnvironmentPath $environmentPath -RequirementsPath $RequirementsPath
    $marker = @{lock_sha256=$script:lockHash;base_python=$BasePython.PythonPath} | ConvertTo-Json
    [IO.File]::WriteAllText((Join-Path $environmentPath '.ac6-environment.json'),$marker,(New-Object Text.UTF8Encoding($false)))
    $script:activeEnvironment = $environmentPath
    return $environmentPath
}

function Invoke-PipInstall {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$RequirementsPath
    )

    [void](Assert-InstallPath $PythonPath)
    $cfg = Join-Path (Split-Path -Parent (Split-Path -Parent $PythonPath)) 'pyvenv.cfg'
    if (-not (Test-Path -LiteralPath $cfg -PathType Leaf)) { throw 'Refusing pip outside an app-owned venv' }
    Write-Step '専用Python環境の依存ライブラリを確認しています。'
    $pipCheck = Invoke-NativeCommand -FilePath $PythonPath -ArgumentList @('-m', 'pip', '--version')
    Write-InstallLog "pip version command: $($pipCheck.Command)"
    Write-InstallLog "pip version exit code: $($pipCheck.ExitCode)"
    Write-InstallLog "pip version stdout:`n$($pipCheck.StdOut)"
    Write-InstallLog "pip version stderr:`n$($pipCheck.StdErr)"
    if ($pipCheck.ExitCode -ne 0) {
        throw '[ENV-DEPENDENCY-MISSING] The new venv has no pip. Repair base Python and re-run the installer.'
    }

    $pipResult = Invoke-NativeCommand -FilePath $PythonPath -ArgumentList @(
        '-m', 'pip', '--isolated', 'install', '--no-warn-script-location', '--require-hashes',
        '--only-binary=:all:', '--no-deps', '--disable-pip-version-check', '-r', $RequirementsPath
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
    Write-InstallLog 'pip install result: success'

    $dependencyCode = 'from importlib.metadata import version; import mss, ttkbootstrap, PIL, tkinter, sqlite3, windows_capture; print("mss=" + mss.__version__ + "; ttkbootstrap=" + version("ttkbootstrap") + "; pillow=" + version("Pillow") + "; sqlite=" + sqlite3.sqlite_version)'
    $dependencyResult = Invoke-NativeCommand -FilePath $PythonPath -ArgumentList @('-c', $dependencyCode)
    Write-InstallLog "dependency verification command: $($dependencyResult.Command)"
    Write-InstallLog "dependency verification exit code: $($dependencyResult.ExitCode)"
    Write-InstallLog "dependency verification stdout:`n$($dependencyResult.StdOut)"
    Write-InstallLog "dependency verification stderr:`n$($dependencyResult.StdErr)"
    if ($dependencyResult.ExitCode -ne 0) {
        throw '[ENV-DEPENDENCY-IMPORT] インストールしたPythonライブラリを読み込めませんでした。source-install.logを添えて報告してください。'
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
    $trackerEntryPattern = '(?i)(?:{0}|{1})[\\/](app\.py|launcher\.pyw|dashboard\.py)(?="|\s|$)' -f [Regex]::Escape($installPath),[Regex]::Escape($legacyPath)
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
        throw
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

function Stop-VerifiedTrackerProcess {
    param($ProcessInfo)
    # Pin the process handle before re-reading identity. Kill uses that handle,
    # never a PID that could have been recycled since enumeration.
    $process = $null
    try {
        $process = [Diagnostics.Process]::GetProcessById([int]$ProcessInfo.ProcessId)
        $null = $process.Handle
        $fresh = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $ProcessInfo.ProcessId)
        if (-not $fresh -or -not $ProcessInfo.CreationDate -or
            $fresh.CreationDate -ne $ProcessInfo.CreationDate -or
            -not (Test-TrackerCommandLine -Name $fresh.Name -CommandLine $fresh.CommandLine)) {
            throw 'Tracker process identity changed; refusing termination'
        }
        if (-not $process.HasExited) { $process.Kill() }
        if (-not $process.WaitForExit(5000)) { throw 'Tracker process did not exit' }
    } finally { if ($process) { $process.Dispose() } }
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
    if ($runtime -and ($trackerProcesses.ProcessId -contains $runtime.Pid) -and $httpAlive -and -not [string]::IsNullOrWhiteSpace($runtime.Token)) {
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
            Stop-VerifiedTrackerProcess -ProcessInfo $processInfo
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

    [void](Assert-InstallPath $installPath)
    [void](Assert-InstallPath $script:backupPath)
    New-Item -ItemType Directory -Path $installParent -Force | Out-Null
    if (Test-Path -LiteralPath $script:backupPath) {
        throw 'Previous transaction backup exists. Preserve it and repair the installation before retrying.'
    }

    $script:hadPreviousInstall = Test-Path -LiteralPath $installPath
    if ($script:hadPreviousInstall) {
        Move-Item -LiteralPath $installPath -Destination $script:backupPath
    }

    try {
        # Move across volumes may leave a partial destination before failing.
        # Register rollback ownership before the first destination mutation.
        $script:sourceSwapped = $true
        Move-Item -LiteralPath $SourcePath -Destination $installPath
    } catch {
        try { Restore-PreviousSource } catch {
            $script:rollbackUnsafe = $true
            throw
        }
        throw
    }
}

function Complete-SourceInstall {
    # Metadata is durable and readiness passed. Cleanup cannot roll back a
    # committed installation after a previous tree has been partly removed.
    $script:committed = $true
    $script:sourceSwapped = $false
    foreach ($path in @($script:backupPath,$legacyPath,"$legacyPath.previous")) {
        try { Remove-OwnedInstallPath $path } catch { Write-InstallLog "post-commit cleanup deferred: $path" }
    }
    foreach ($envDir in @(Get-ChildItem -LiteralPath (Join-Path $ownedRoot 'venv') -Directory)) {
        if ($envDir.FullName -ne $script:activeEnvironment -and (Test-Path -LiteralPath (Join-Path $envDir.FullName '.ac6-environment.json'))) {
            try { Remove-OwnedInstallPath $envDir.FullName } catch { Write-InstallLog "old environment cleanup deferred: $($envDir.FullName)" }
        }
    }
}

function Restore-PreviousSource {
    if (-not $script:sourceSwapped) { return }
    Write-InstallLog 'transaction rollback started'
    [void](Assert-InstallPath $installPath)
    [void](Assert-InstallPath $script:backupPath)
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
                if (-not $runtime.PSObject.Properties['install_nonce'] -or $runtime.install_nonce -cne $script:installNonce) {
                    Start-Sleep -Milliseconds 100
                    continue
                }
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
        $shortcut.Arguments = '"{0}"' -f $LauncherPath
        $shortcut.WorkingDirectory = $installPath
        $shortcut.Description = if ($channel -eq 'candidate') { 'AC6 Win/Loss Tracker Candidate' } else { 'AC6 Win/Loss Tracker Stable' }
        $script:shortcutChanged = $true
        $shortcut.Save()
    } finally {
        if ($shortcut) {
            [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shortcut)
        }
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
    }
    return $shortcutPath
}

function Start-TrackerLauncher {
    param([string]$PythonwPath, [string]$LauncherPath)
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $PythonwPath
    $info.Arguments = '"{0}"' -f $LauncherPath
    $info.WorkingDirectory = Split-Path -Parent $LauncherPath
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    foreach ($name in @('PYTHONPATH','PYTHONHOME','PYTHONUSERBASE','PYTHONSTARTUP','PYTHONOPTIMIZE','PYTHONINSPECT','PYTHONEXECUTABLE','PYTHONPLATLIBDIR','__PYVENV_LAUNCHER__','AC6_LAUNCH_NONCE')) { $info.EnvironmentVariables.Remove($name) }
    $info.EnvironmentVariables['AC6_INSTALL_NONCE'] = $script:installNonce
    $info.EnvironmentVariables['PYTHONUTF8'] = '1'
    $info.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
    $cfgPath = Join-Path (Split-Path -Parent (Split-Path -Parent $PythonwPath)) 'pyvenv.cfg'
    if ((Split-Path -Leaf (Split-Path -Parent $PythonwPath)) -eq 'Scripts' -and (Test-Path -LiteralPath $cfgPath)) {
        $cfg = Get-Content -LiteralPath $cfgPath -Raw
        $match = [regex]::Match($cfg, '(?m)^home\s*=\s*(.+)\s*$')
        if (-not $match.Success) { throw '[ENV-VENV-BROKEN] Re-run the installer to repair base Python.' }
        $info.FileName = Join-Path $match.Groups[1].Value.Trim() 'pythonw.exe'
        if (-not (Test-Path -LiteralPath $info.FileName)) { throw '[ENV-VENV-BROKEN] Re-run the installer to repair base Python.' }
        $info.EnvironmentVariables['__PYVENV_LAUNCHER__'] = $PythonwPath
    }
    return [Diagnostics.Process]::Start($info)
}

try {
    if ($env:OS -ne 'Windows_NT') {
        throw 'このインストーラーはWindows 11用です。WindowsのPowerShellから実行してください。'
    }
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw 'WindowsのLOCALAPPDATAフォルダーを確認できませんでした。Windowsへサインインし直してからお試しください。'
    }

    Enter-InstallerMutex
    [void](Assert-InstallPath $ownedRoot)
    [void](Assert-InstallPath $legacyPath)
    New-Item -ItemType Directory -Path $dataPath -Force | Out-Null
    Write-InstallLog '------------------------------------------------------------'
    Write-InstallLog "Installer channel: $channel"
    Write-InstallLog "Installer version: $version"
    Write-InstallLog "Windows version: $([Environment]::OSVersion.VersionString)"
    Set-InstallStage -Name 'startup'

    Set-InstallStage -Name 'revision-resolve'
    $resolvedCommit = Resolve-StableCommit
    $archiveUrl = "https://github.com/$repository/archive/$resolvedCommit.zip"
    Write-InstallLog "resolved $channel $sourceKind`: $sourceRef"
    Write-InstallLog "resolved $channel revision: $resolvedCommit"

    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('AC6WinLossTrackerSource-' + [Guid]::NewGuid().ToString('N'))
    $zipPath = Join-Path $tempRoot 'source.zip'
    $extractPath = Join-Path $tempRoot 'extracted'
    New-Item -ItemType Directory -Path $extractPath -Force | Out-Null

    Set-InstallStage -Name 'source-download'
    Write-Step "GitHubから$channel ソースをHTTPSで取得し、内容を確認しています。"
    try {
        Invoke-WebRequest -Uri $archiveUrl -OutFile $zipPath -UseBasicParsing -TimeoutSec 60 `
            -Headers @{ 'User-Agent' = 'AC6-WinLoss-Tracker-Installer/1.2.0' }
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
    foreach ($requiredFile in @('app.py', 'app_paths.py', 'launcher.pyw', 'dashboard.py', 'python_spawn.py', 'requirements.lock', 'uninstall.ps1')) {
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
    $environmentPath = New-AppEnvironment -BasePython $python -RequirementsPath (Join-Path $sourceRoot.FullName 'requirements.lock')
    Set-InstallStage -Name 'python-verification'
    $confirmedPython = Find-SupportedPython
    if (-not $confirmedPython -or
        $confirmedPython.PythonPath -ne $python.PythonPath -or
        $confirmedPython.PythonwPath -ne $python.PythonwPath -or
        $confirmedPython.Version -ne $python.Version -or
        $confirmedPython.Role -ne $python.Role) {
        throw '[ENV-PYTHON-UNSUPPORTED] 依存確認後にselected Python Runtimeの完全性を再確認できませんでした。現在のTrackerは変更していません。'
    }
    $python = [PSCustomObject]@{
        PythonPath=(Join-Path $environmentPath 'Scripts\python.exe')
        PythonwPath=(Join-Path $environmentPath 'Scripts\pythonw.exe')
        BasePythonPath=$confirmedPython.PythonPath
        Version=$confirmedPython.Version
        Role=$confirmedPython.Role
    }
    Write-InstallLog 'selected Python post-dependency validation: success'
    Backup-AppShortcut
    $metadataPath = Join-Path $dataPath 'installed-version.json'
    $script:metadataBackup = Join-Path $tempRoot 'installed-version.previous.json'
    $script:metadataExisted = Test-Path -LiteralPath $metadataPath
    if ($script:metadataExisted) { Copy-Item -LiteralPath $metadataPath -Destination $script:metadataBackup }
    $script:metadataCaptured = $true
    $script:previousRunning = @(Get-TrackerProcesses).Count -gt 0
    $script:previousLauncherPath = if (Test-Path -LiteralPath (Join-Path $installPath 'launcher.pyw')) {
        Join-Path $installPath 'launcher.pyw'
    } else { Join-Path $legacyPath 'launcher.pyw' }
    if ($script:previousRunning -and (-not $script:previousPythonwPath -or -not (Test-Path -LiteralPath $script:previousPythonwPath))) {
        throw 'Cannot prove the previous running environment. Restore its shortcut before updating.'
    }

    if ((Test-Path -LiteralPath $installPath -PathType Container) -or (Test-Path -LiteralPath $legacyPath -PathType Container)) {
        Set-InstallStage -Name 'stop-running-app'
        $script:stopAttempted = $true
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
    $script:launcherProcess = Start-TrackerLauncher -PythonwPath $python.PythonwPath -LauncherPath $launcherPath
    if (-not (Wait-AppRuntimeReady -TimeoutSeconds 15)) {
        throw 'アプリの起動を確認できませんでした。startup.logを確認してください。'
    }

    Write-InstallLog 'application launch result: success'
    Set-InstallStage -Name 'metadata'
    Write-InstalledMetadata -Commit $resolvedCommit -Python $python
    Write-InstallLog "installed revision: $resolvedCommit"
    Set-InstallStage -Name 'commit'
    Complete-SourceInstall
    Write-Host "`nセットアップが完了しました。" -ForegroundColor Green
    Write-Host "デスクトップの「AC6 WinLoss Tracker」から次回以降も起動できます。"
    Write-Host "ログ: $script:logPath"
} catch {
    $exitCode = 1
    $errorRecord = $_
    if ($script:sourceSwapped -and -not $script:committed) {
        try {
            Stop-RunningTracker
        } catch {
            try { Write-InstallLog "rollback shutdown warning: $($_.Exception.Message)" } catch {}
            $script:rollbackUnsafe = $true
        }
        if (-not $script:rollbackUnsafe) {
            try { Restore-PreviousSource } catch {
                $script:rollbackUnsafe = $true
                try { Write-InstallLog "source rollback failed: $($_.Exception.Message)" } catch {}
            }
        }
        try {
            Restore-AppShortcut
        } catch {
            try { Write-InstallLog "shortcut rollback failed: $($_.Exception.Message)" } catch {}
        }
    } elseif ($script:shortcutChanged) {
        try { Restore-AppShortcut } catch {
            try { Write-InstallLog "shortcut rollback failed: $($_.Exception.Message)" } catch {}
        }
    }
    if ($script:metadataCaptured -and -not $script:committed -and -not $script:rollbackUnsafe) {
        try {
            $metadataPath = Join-Path $dataPath 'installed-version.json'
            if ($script:metadataExisted) { Copy-Item -LiteralPath $script:metadataBackup -Destination $metadataPath -Force }
            elseif (Test-Path -LiteralPath $metadataPath) { Remove-Item -LiteralPath $metadataPath -Force }
            if ($script:stopAttempted -and $script:previousRunning -and (Test-Path -LiteralPath $script:previousLauncherPath)) {
                $restoredLauncher = Start-TrackerLauncher -PythonwPath $script:previousPythonwPath -LauncherPath $script:previousLauncherPath
                $restoredLauncher.Dispose()
                Write-InstallLog 'transaction rollback restarted previous environment and source'
            }
        } catch {
            $script:rollbackUnsafe = $true
            try { Write-InstallLog "rollback restore failed: $($_.Exception.Message)" } catch {}
        }
    }
    $friendlyMessage = [string]$errorRecord.Exception.Message
    if ([string]::IsNullOrWhiteSpace($friendlyMessage)) {
        $friendlyMessage = 'セットアップ中に問題が発生しました。インターネット接続を確認して、もう一度お試しください。'
    }
    try {
        if ($script:ownsInstallerMutex -and (Test-Path -LiteralPath $dataPath)) {
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
    try {
    if ($script:launcherProcess) {
        if (-not $script:committed -and -not $script:launcherProcess.HasExited) {
            $script:launcherProcess.Kill()
            [void]$script:launcherProcess.WaitForExit(5000)
        }
        $script:launcherProcess.Dispose()
    }
    if ($script:newEnvironment -and -not $script:committed -and -not $script:rollbackUnsafe) {
        try { Remove-OwnedInstallPath $script:newEnvironment } catch { Write-Warning 'Incomplete environment retained for inspection.' }
    }
    if ($script:rollbackUnsafe) { Write-Warning "Rollback needs repair; source/environment and transaction evidence retained at $tempRoot" }
    if ($tempRoot -and -not $script:rollbackUnsafe -and (Test-Path -LiteralPath $tempRoot)) {
        $expectedTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
        if (-not ([IO.Path]::GetFullPath($tempRoot)).StartsWith($expectedTemp, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe temporary root' }
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    } finally {
        if ($script:ownsInstallerMutex) { $script:installerMutex.ReleaseMutex() }
        if ($script:installerMutex) { $script:installerMutex.Dispose() }
    }
}

if ($exitCode -ne 0) {
    exit $exitCode
}
