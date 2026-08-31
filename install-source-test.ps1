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

function Test-IsAppExecutionAlias {
    param([Parameter(Mandatory = $true)][string]$Path)
    return $Path -match '(?i)\\Microsoft\\WindowsApps\\'
}

function Get-SignedExecutableInfo {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$AllowWindowsApps
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }

    $resolvedPath = (Resolve-Path -LiteralPath $Path).Path
    if (-not $AllowWindowsApps -and (Test-IsAppExecutionAlias -Path $resolvedPath)) {
        return $null
    }

    $signature = Get-AuthenticodeSignature -LiteralPath $resolvedPath
    if ($signature.Status -ne 'Valid' -or $signature.SignatureType -ne 'Authenticode' -or -not $signature.SignerCertificate) {
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
    $List.Add($Path) | Out-Null
}

function Get-PythonCandidatePaths {
    $candidates = New-Object 'System.Collections.Generic.List[string]'
    $pythonBase = Join-Path $env:LOCALAPPDATA 'Programs\Python'

    Add-PythonCandidate -List $candidates -Path (Join-Path $pythonBase 'Python312\python.exe')
    if (Test-Path -LiteralPath $pythonBase -PathType Container) {
        Get-ChildItem -LiteralPath $pythonBase -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object {
                Add-PythonCandidate -List $candidates -Path (Join-Path $_.FullName 'python.exe')
            }
    }

    $pyCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyCommand) {
        $pyInfo = Get-SignedExecutableInfo -Path $pyCommand.Source
        if ($pyInfo) {
            $registered = @(& $pyInfo.Path -0p 2>$null)
            if ($LASTEXITCODE -eq 0) {
                foreach ($line in $registered) {
                    if ([string]$line -match '([A-Za-z]:\\.+\\python(?:\d+(?:\.\d+)?)?\.exe)\s*$') {
                        Add-PythonCandidate -List $candidates -Path $Matches[1]
                    }
                }
            }
        }
    }

    foreach ($name in @('python.exe', 'python3.exe', 'python')) {
        $commands = @(Get-Command $name -All -ErrorAction SilentlyContinue)
        foreach ($command in $commands) {
            $path = $command.Source
            if (-not [string]::IsNullOrWhiteSpace($path)) {
                Add-PythonCandidate -List $candidates -Path $path
            }
        }
    }

    return $candidates
}

function Find-SupportedPython {
    $seen = @{}
    foreach ($candidate in (Get-PythonCandidatePaths)) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }

        try {
            $fullPath = [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($candidate))
        } catch {
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
            continue
        }

        try {
            $versionOutput = @(& $signedPython.Path -c 'import json,sys; print(json.dumps({"major":sys.version_info.major,"minor":sys.version_info.minor,"micro":sys.version_info.micro,"executable":sys.executable}))' 2>$null)
            if ($LASTEXITCODE -ne 0 -or $versionOutput.Count -eq 0) {
                continue
            }
            $versionInfo = $versionOutput[-1] | ConvertFrom-Json
            if ([int]$versionInfo.major -lt 3 -or ([int]$versionInfo.major -eq 3 -and [int]$versionInfo.minor -lt 10)) {
                continue
            }

            $actualPython = (Resolve-Path -LiteralPath ([string]$versionInfo.executable)).Path
            if ($actualPython -ne $signedPython.Path) {
                $signedPython = Get-SignedExecutableInfo -Path $actualPython
                if (-not $signedPython) {
                    continue
                }
            }

            $pythonwPath = Join-Path (Split-Path -Parent $signedPython.Path) 'pythonw.exe'
            $signedPythonw = Get-SignedExecutableInfo -Path $pythonwPath
            if (-not $signedPythonw) {
                continue
            }
            if ($signedPythonw.Signature.SignerCertificate.Subject -ne $signedPython.Signature.SignerCertificate.Subject) {
                continue
            }

            return [PSCustomObject]@{
                PythonPath = $signedPython.Path
                PythonwPath = $signedPythonw.Path
                Version = '{0}.{1}.{2}' -f $versionInfo.major, $versionInfo.minor, $versionInfo.micro
                SignatureStatus = [string]$signedPython.Signature.Status
                SignerSubject = [string]$signedPython.Signature.SignerCertificate.Subject
            }
        } catch {
            continue
        }
    }
    return $null
}

function Install-PythonWithWinget {
    $wingetCommand = Get-Command winget.exe -ErrorAction SilentlyContinue
    $wingetCandidates = New-Object 'System.Collections.Generic.List[string]'
    if ($wingetCommand -and -not [string]::IsNullOrWhiteSpace($wingetCommand.Source)) {
        $wingetCandidates.Add($wingetCommand.Source) | Out-Null
    }

    $appInstallerPackages = @(Get-AppxPackage -Name Microsoft.DesktopAppInstaller -ErrorAction SilentlyContinue |
        Sort-Object Version -Descending)
    foreach ($package in $appInstallerPackages) {
        foreach ($fileName in @('winget.exe', 'AppInstallerCLI.exe')) {
            $wingetCandidates.Add((Join-Path $package.InstallLocation $fileName)) | Out-Null
        }
    }

    if ($wingetCandidates.Count -eq 0) {
        throw 'wingetが見つかりませんでした。Windowsの「アプリ インストーラー」を更新してから、もう一度実行してください。'
    }

    $signedWinget = $null
    foreach ($candidate in $wingetCandidates) {
        $signedWinget = Get-SignedExecutableInfo -Path $candidate -AllowWindowsApps
        if ($signedWinget) {
            break
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

    Write-Step '署名済みのPython 3.10以上を探しています。'
    $python = Find-SupportedPython
    if (-not $python) {
        Install-PythonWithWinget
        # Do not depend on a refreshed PATH; explicit user-install locations are searched first.
        $python = Find-SupportedPython
    }
    if (-not $python) {
        throw 'Python 3.10以上の実体を確認できませんでした。Pythonの自動インストールに失敗した可能性があります。'
    }

    Write-Host "Python $($python.Version): $($python.PythonPath)"
    Write-InstallLog "Python version: $($python.Version)"
    Write-InstallLog "python.exe path: $($python.PythonPath)"
    Write-InstallLog "pythonw.exe path: $($python.PythonwPath)"
    Write-InstallLog "Python Authenticode Status: $($python.SignatureStatus)"
    Write-InstallLog "Python signer Subject: $($python.SignerSubject)"

    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('AC6WinLossTrackerSource-' + [Guid]::NewGuid().ToString('N'))
    $zipPath = Join-Path $tempRoot 'source.zip'
    $extractPath = Join-Path $tempRoot 'extracted'
    New-Item -ItemType Directory -Path $extractPath -Force | Out-Null

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

    Invoke-PipInstall -PythonPath $python.PythonPath -RequirementsPath (Join-Path $sourceRoot.FullName 'requirements.txt')

    Write-Step 'アプリのソースをユーザー領域へインストールしています。'
    Install-SourceTree -SourcePath $sourceRoot.FullName
    $appPath = Join-Path $installPath 'app.py'
    if (-not (Test-Path -LiteralPath $appPath -PathType Leaf)) {
        throw 'インストール後のapp.pyを確認できませんでした。'
    }
    Write-InstallLog "source install path: $installPath"

    Write-Step 'デスクトップショートカットを作成しています。'
    $shortcutPath = New-AppShortcut -PythonwPath $python.PythonwPath -AppPath $appPath
    Write-InstallLog "shortcut path: $shortcutPath"
    Write-InstallLog "shortcut TargetPath: $($python.PythonwPath)"
    Write-InstallLog "shortcut Arguments: `"$appPath`""

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
    $friendlyMessage = [string]$_.Exception.Message
    if ([string]::IsNullOrWhiteSpace($friendlyMessage)) {
        $friendlyMessage = 'セットアップ中に問題が発生しました。インターネット接続を確認して、もう一度お試しください。'
    }
    try {
        if (Test-Path -LiteralPath $dataPath) {
            Write-InstallLog "ERROR: $friendlyMessage"
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
