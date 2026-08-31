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
                $registered = @(& $pyInfo.Path -0p 2>$null)
                if ($LASTEXITCODE -eq 0) {
                    foreach ($line in $registered) {
                        if ([string]$line -match '([A-Za-z]:\\.+\\python(?:\d+(?:\.\d+)?)?\.exe)\s*$') {
                            try {
                                Add-PythonCandidate -List $candidates -Path $Matches[1]
                            } catch {
                                Write-CandidateSkipped -Kind 'Python' -Path ([string]$Matches[1]) -Reason ("py launcher candidate failed: {0}" -f $_.Exception.Message)
                            }
                        }
                    }
                } else {
                    Write-CandidateSkipped -Kind 'Python launcher' -Path $pyInfo.Path -Reason ("py.exe -0p failed with exit code $LASTEXITCODE")
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

    $verificationId = [Guid]::NewGuid().ToString('N')
    $stdoutPath = Join-Path ([System.IO.Path]::GetTempPath()) "AC6PythonVerification-$verificationId.stdout"
    $stderrPath = Join-Path ([System.IO.Path]::GetTempPath()) "AC6PythonVerification-$verificationId.stderr"
    $stdout = ''
    $stderr = ''
    $verificationExitCode = -1

    try {
        # Keep the Python snippet free of nested quotes and JSON escaping.
        $verificationCode = 'import sys; print(sys.version_info.major); print(sys.version_info.minor); print(sys.version_info.micro); print(sys.executable)'
        & $PythonPath -c $verificationCode 1> $stdoutPath 2> $stderrPath
        $verificationExitCode = $LASTEXITCODE

        if (Test-Path -LiteralPath $stdoutPath -PathType Leaf) {
            $stdout = [string](Get-Content -LiteralPath $stdoutPath -Raw -ErrorAction Stop)
        }
        if (Test-Path -LiteralPath $stderrPath -PathType Leaf) {
            $stderr = [string](Get-Content -LiteralPath $stderrPath -Raw -ErrorAction Stop)
        }
    } catch {
        $stderr = @($stderr, $_.Exception.ToString()) -join [Environment]::NewLine
    } finally {
        Write-InstallLog "Python verification candidate path: $PythonPath"
        Write-InstallLog "Python verification exit code: $verificationExitCode"
        Write-InstallLog "Python verification stdout:`n$stdout"
        Write-InstallLog "Python verification stderr:`n$stderr"
        Remove-Item -LiteralPath $stdoutPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
    }

    return [PSCustomObject]@{
        ExitCode = $verificationExitCode
        Stdout = $stdout
        Stderr = $stderr
    }
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
    $wingetOutput = @(& $signedWinget.Path install --exact --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements 2>&1)
    $wingetExitCode = $LASTEXITCODE
    if ($wingetOutput.Count -gt 0) {
        Write-Host ($wingetOutput -join [Environment]::NewLine)
    }
    if ($wingetExitCode -ne 0) {
        throw "Pythonの自動インストールに失敗しました（winget終了コード: $wingetExitCode）。インターネット接続を確認してください。"
    }
}

function Invoke-PipInstall {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$RequirementsPath
    )

    Write-Step 'Python依存ライブラリを確認しています。'
    $pipCheck = @(& $PythonPath -m pip --version 2>&1)
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'pipが見つからないため、Python標準のensurepipを実行します。'
        $ensureOutput = @(& $PythonPath -m ensurepip --upgrade 2>&1)
        $ensureExitCode = $LASTEXITCODE
        if ($ensureOutput.Count -gt 0) {
            Write-Host ($ensureOutput -join [Environment]::NewLine)
        }
        if ($ensureExitCode -ne 0) {
            Write-InstallLog "pip install result: failed (ensurepip exit code $ensureExitCode)"
            throw 'pipの準備に失敗しました。Pythonを再インストールしてから、もう一度お試しください。'
        }
    }

    $pipOutput = @(& $PythonPath -m pip install --user -r $RequirementsPath 2>&1)
    $pipExitCode = $LASTEXITCODE
    if ($pipOutput.Count -gt 0) {
        Write-Host ($pipOutput -join [Environment]::NewLine)
    }
    if ($pipExitCode -ne 0) {
        Write-InstallLog "pip install result: failed (exit code $pipExitCode)"
        throw '必要なPythonライブラリのインストールに失敗しました。インターネット接続を確認してください。'
    }
    Write-InstallLog 'pip install result: success'
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

function New-AppShortcut {
    param(
        [Parameter(Mandatory = $true)][string]$PythonwPath,
        [Parameter(Mandatory = $true)][string]$AppPath
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
        $shortcut.Arguments = '"{0}"' -f $AppPath
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

    Set-InstallStage -Name 'source-install'
    Write-Step 'アプリのソースをユーザー領域へインストールしています。'
    Install-SourceTree -SourcePath $sourceRoot.FullName
    $appPath = Join-Path $installPath 'app.py'
    if (-not (Test-Path -LiteralPath $appPath -PathType Leaf)) {
        throw 'インストール後のapp.pyを確認できませんでした。'
    }
    Write-InstallLog "source install path: $installPath"

    Set-InstallStage -Name 'shortcut'
    Write-Step 'デスクトップショートカットを作成しています。'
    $shortcutPath = New-AppShortcut -PythonwPath $python.PythonwPath -AppPath $appPath
    Write-InstallLog "shortcut path: $shortcutPath"
    Write-InstallLog "shortcut TargetPath: $($python.PythonwPath)"
    Write-InstallLog "shortcut Arguments: `"$appPath`""

    Set-InstallStage -Name 'launch'
    Write-Step 'ショートカットと同じ方法でアプリを起動しています。'
    $appArguments = '"{0}"' -f $appPath
    $process = Start-Process -FilePath $python.PythonwPath -ArgumentList $appArguments -WorkingDirectory $installPath -PassThru
    Start-Sleep -Seconds 3
    if ($process.HasExited) {
        throw "アプリが起動直後に終了しました（終了コード: $($process.ExitCode)）。ログを確認してください。"
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
