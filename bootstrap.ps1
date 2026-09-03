[CmdletBinding()]
param(
    [ValidateSet('Install', 'Uninstall')][string]$Mode = 'Install',
    [Parameter(DontShow = $true)][string]$Repository = 'TullysAC6/ac6-winloss-tracker',
    [Parameter(DontShow = $true)][string]$ReleaseTag = '',
    [Parameter(DontShow = $true)][switch]$LibraryOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Get-ReleaseApiUrl {
    param([string]$Repository, [string]$ReleaseTag)
    if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
        throw 'Repository名の形式が正しくありません。'
    }
    if ([string]::IsNullOrWhiteSpace($ReleaseTag)) {
        return "https://api.github.com/repos/$Repository/releases/latest"
    }
    if ($ReleaseTag -notmatch '^v\d+\.\d+\.\d+$') {
        throw 'Release tagの形式が正しくありません。'
    }
    return "https://api.github.com/repos/$Repository/releases/tags/$ReleaseTag"
}

function Assert-StableReleaseMetadata {
    param([Parameter(Mandatory = $true)]$Release)
    if ($null -eq $Release -or $Release.draft -eq $true -or $Release.prerelease -eq $true) {
        throw 'Stable Release metadataを確認できませんでした。'
    }
    if ([string]$Release.tag_name -notmatch '^v\d+\.\d+\.\d+$') {
        throw 'Stable Release tagの形式が正しくありません。'
    }
    if ($null -eq $Release.assets) {
        throw 'Stable Release assetsを確認できませんでした。'
    }
}

function Get-UniqueReleaseAsset {
    param(
        [Parameter(Mandatory = $true)]$Release,
        [Parameter(Mandatory = $true)][string]$Name
    )
    $matches = @($Release.assets | Where-Object { [string]$_.name -ceq $Name })
    if ($matches.Count -ne 1) {
        throw "Release asset $Name を一意に確認できませんでした。"
    }
    $asset = $matches[0]
    if ([string]$asset.browser_download_url -notmatch '^https://github\.com/') {
        throw "Release asset $Name のHTTPS URLを確認できませんでした。"
    }
    return $asset
}

function Get-AssetSha256 {
    param([Parameter(Mandatory = $true)]$Asset)
    $digest = [string]$Asset.digest
    if ($digest -notmatch '^sha256:([0-9a-fA-F]{64})$') {
        throw "Release asset $($Asset.name) のGitHub SHA-256 digestを確認できませんでした。"
    }
    return $Matches[1].ToLowerInvariant()
}

function Assert-ChecksumFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ExpectedName,
        [Parameter(Mandatory = $true)][string]$ExpectedHash
    )
    $text = [System.IO.File]::ReadAllText($Path).Trim()
    $pattern = '^([0-9a-fA-F]{64})\s+\*?' + [Regex]::Escape($ExpectedName) + '$'
    if ($text -notmatch $pattern) {
        throw 'Release checksum fileの形式が正しくありません。'
    }
    if ($Matches[1].ToLowerInvariant() -cne $ExpectedHash) {
        throw 'Release metadataとchecksum fileのSHA-256が一致しません。'
    }
}

function Assert-PowerShellScript {
    param([Parameter(Mandatory = $true)][string]$Path)
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $Path, [ref]$tokens, [ref]$errors
    )
    if ($errors.Count -gt 0) {
        throw '取得したPowerShell scriptの構文が正しくありません。'
    }
}

function Invoke-VerifiedReleaseScript {
    param(
        [ValidateSet('Install', 'Uninstall')][string]$Mode,
        [string]$Repository,
        [string]$ReleaseTag,
        [Parameter(Mandatory = $true)][scriptblock]$WebRequestInvoker,
        [Parameter(Mandatory = $true)][scriptblock]$ChildInvoker
    )
    $temporaryDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ('AC6Bootstrap-' + [Guid]::NewGuid().ToString('N'))
    $scriptName = if ($Mode -eq 'Uninstall') { 'uninstall.ps1' } else { 'install.ps1' }
    $checksumName = "$scriptName.sha256"
    try {
        New-Item -ItemType Directory -Path $temporaryDirectory -Force | Out-Null
        $metadataPath = Join-Path $temporaryDirectory 'release.json'
        & $WebRequestInvoker (Get-ReleaseApiUrl -Repository $Repository -ReleaseTag $ReleaseTag) $metadataPath
        if (-not (Test-Path -LiteralPath $metadataPath -PathType Leaf) -or (Get-Item -LiteralPath $metadataPath).Length -le 0) {
            throw 'Stable Release metadataのdownloadが不完全です。'
        }
        try {
            $release = Get-Content -LiteralPath $metadataPath -Raw -Encoding UTF8 | ConvertFrom-Json
        } catch {
            throw 'Stable Release metadataの形式が正しくありません。'
        }
        Assert-StableReleaseMetadata -Release $release
        if ($ReleaseTag -and [string]$release.tag_name -cne $ReleaseTag) {
            throw '要求した固定Release tagとmetadataが一致しません。'
        }

        $scriptAsset = Get-UniqueReleaseAsset -Release $release -Name $scriptName
        $checksumAsset = Get-UniqueReleaseAsset -Release $release -Name $checksumName
        $expectedScriptHash = Get-AssetSha256 -Asset $scriptAsset
        $expectedChecksumHash = Get-AssetSha256 -Asset $checksumAsset

        $scriptPath = Join-Path $temporaryDirectory $scriptName
        $checksumPath = Join-Path $temporaryDirectory $checksumName
        & $WebRequestInvoker ([string]$scriptAsset.browser_download_url) $scriptPath
        & $WebRequestInvoker ([string]$checksumAsset.browser_download_url) $checksumPath
        foreach ($path in @($scriptPath, $checksumPath)) {
            if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path).Length -le 0) {
                throw 'Release assetのdownloadが不完全です。'
            }
        }

        $actualScriptHash = (Get-FileHash -LiteralPath $scriptPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualScriptHash -cne $expectedScriptHash) {
            throw '取得したinstallerのSHA-256がGitHub Release metadataと一致しません。実行を中止しました。'
        }
        $actualChecksumHash = (Get-FileHash -LiteralPath $checksumPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualChecksumHash -cne $expectedChecksumHash) {
            throw '取得したchecksum fileのSHA-256がGitHub Release metadataと一致しません。実行を中止しました。'
        }
        Assert-ChecksumFile -Path $checksumPath -ExpectedName $scriptName -ExpectedHash $expectedScriptHash
        Assert-PowerShellScript -Path $scriptPath

        $childExitCode = & $ChildInvoker $scriptPath $Mode ([string]$release.tag_name)
        if ($null -eq $childExitCode) { $childExitCode = 0 }
        return [int]$childExitCode
    } finally {
        if (Test-Path -LiteralPath $temporaryDirectory) {
            Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

if (-not $LibraryOnly) {
    $webRequest = {
        param($Uri, $OutFile)
        Invoke-WebRequest -Uri $Uri -OutFile $OutFile -UseBasicParsing -TimeoutSec 60 `
            -Headers @{ 'User-Agent' = 'AC6-WinLoss-Tracker-Bootstrap/1.0.0' } -ErrorAction Stop
    }
    $childProcess = {
        param($Path, $SelectedMode, $VerifiedReleaseTag)
        $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $Path)
        if ($SelectedMode -eq 'Install') { $arguments += @('-SourceTag', $VerifiedReleaseTag) }
        $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Wait -PassThru
        return [int]$process.ExitCode
    }
    try {
        $result = Invoke-VerifiedReleaseScript -Mode $Mode -Repository $Repository -ReleaseTag $ReleaseTag `
            -WebRequestInvoker $webRequest -ChildInvoker $childProcess
        exit $result
    } catch {
        Write-Host "`nAC6 Win/Loss Trackerの安全な取得に失敗しました。" -ForegroundColor Red
        Write-Host $_.Exception.Message -ForegroundColor Red
        Write-Host 'インターネット接続を確認し、同じ1行コマンドをもう一度お試しください。' -ForegroundColor Yellow
        exit 1
    }
}
