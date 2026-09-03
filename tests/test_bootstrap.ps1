$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'bootstrap.ps1') -LibraryOnly

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('ac6-bootstrap-test-' + [Guid]::NewGuid().ToString('N'))
$fixtureRoot = Join-Path $testRoot 'fixtures'
New-Item -ItemType Directory -Path $fixtureRoot -Force | Out-Null

function New-FixtureRelease {
    param(
        [string]$ScriptText = '[CmdletBinding()] param() exit 0',
        [switch]$BadHash,
        [switch]$MalformedMetadata,
        [switch]$EmptyScript
    )
    $scriptPath = Join-Path $fixtureRoot 'install.ps1'
    if ($EmptyScript) { [System.IO.File]::WriteAllBytes($scriptPath, [byte[]]@()) }
    else { [System.IO.File]::WriteAllText($scriptPath, $ScriptText, (New-Object System.Text.UTF8Encoding($false))) }
    $hash = (Get-FileHash -LiteralPath $scriptPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $metadataHash = if ($BadHash) { '0' * 64 } else { $hash }
    [System.IO.File]::WriteAllText(
        (Join-Path $fixtureRoot 'install.ps1.sha256'), "$hash *install.ps1`n",
        (New-Object System.Text.UTF8Encoding($false))
    )
    if ($MalformedMetadata) {
        Set-Content -LiteralPath (Join-Path $fixtureRoot 'release.json') -Value '{broken' -Encoding UTF8
        return
    }
    $checksumHash = (Get-FileHash -LiteralPath (Join-Path $fixtureRoot 'install.ps1.sha256') -Algorithm SHA256).Hash.ToLowerInvariant()
    $metadata = [ordered]@{
        tag_name = 'v1.0.0'
        draft = $false
        prerelease = $false
        assets = @(
            [ordered]@{ name = 'install.ps1'; browser_download_url = 'https://github.com/example/install.ps1'; digest = "sha256:$metadataHash" },
            [ordered]@{ name = 'install.ps1.sha256'; browser_download_url = 'https://github.com/example/install.ps1.sha256'; digest = "sha256:$checksumHash" }
        )
    } | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText(
        (Join-Path $fixtureRoot 'release.json'), $metadata, (New-Object System.Text.UTF8Encoding($false))
    )
}

$script:downloadFailure = $false
$web = {
    param($Uri, $OutFile)
    if ($script:downloadFailure) { throw 'mock HTTP failure' }
    $name = if ($Uri -match '/releases/') { 'release.json' } else { Split-Path -Leaf $Uri }
    Copy-Item -LiteralPath (Join-Path $fixtureRoot $name) -Destination $OutFile -Force
}
$script:childExitCode = 0
$script:childTag = ''
$child = { param($Path, $Mode, $Tag) $script:childTag = $Tag; return $script:childExitCode }

try {
    New-FixtureRelease
    $result = Invoke-VerifiedReleaseScript -Mode Install -Repository owner/repo -ReleaseTag v1.0.0 -WebRequestInvoker $web -ChildInvoker $child
    if ($result -ne 0) { throw 'successful verified download failed' }
    if ($script:childTag -cne 'v1.0.0') { throw 'verified release tag was not passed to installer' }

    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        $descendantPidPath = Join-Path $testRoot 'descendant.pid'
        $escapedPidPath = $descendantPidPath.Replace("'", "''")
        $fakeInstallerPath = Join-Path $testRoot 'fake-installer.ps1'
        $fakeInstaller = @"
param([string]`$SourceTag)
`$child = Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile', '-Command', 'Start-Sleep -Seconds 30') -PassThru
[System.IO.File]::WriteAllText('$escapedPidPath', [string]`$child.Id)
exit 0
"@
        [System.IO.File]::WriteAllText(
            $fakeInstallerPath, $fakeInstaller, (New-Object System.Text.UTF8Encoding($false))
        )
        $descendant = $null
        try {
            $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
            $processResult = Invoke-InstallerChildProcess -Path $fakeInstallerPath -Mode Install `
                -VerifiedReleaseTag 'v1.0.1'
            $stopwatch.Stop()
            if ($processResult -ne 0) { throw 'fake installer exit code was not propagated' }
            if (-not (Test-Path -LiteralPath $descendantPidPath -PathType Leaf)) {
                throw 'fake installer did not record its long-lived child'
            }
            $descendantPid = [int](Get-Content -LiteralPath $descendantPidPath -Raw)
            $descendant = Get-Process -Id $descendantPid -ErrorAction Stop
            if ($descendant.HasExited) { throw 'bootstrap waited for the installer descendant to exit' }
            if ($stopwatch.Elapsed.TotalSeconds -ge 15) {
                throw "bootstrap did not return promptly after its direct child exited: $($stopwatch.Elapsed)"
            }
        } finally {
            if ($null -ne $descendant -and -not $descendant.HasExited) {
                Stop-Process -Id $descendant.Id -Force -ErrorAction SilentlyContinue
            }
        }
    }

    $script:childExitCode = 23
    $result = Invoke-VerifiedReleaseScript -Mode Install -Repository owner/repo -ReleaseTag v1.0.0 -WebRequestInvoker $web -ChildInvoker $child
    if ($result -ne 23) { throw 'child installer exit code was not propagated' }
    $script:childExitCode = 0

    foreach ($case in @('http', 'partial', 'hash', 'metadata', 'syntax')) {
        New-FixtureRelease -EmptyScript:($case -eq 'partial') -BadHash:($case -eq 'hash') `
            -MalformedMetadata:($case -eq 'metadata') -ScriptText $(if ($case -eq 'syntax') { 'param(' } else { '[CmdletBinding()] param() exit 0' })
        $script:downloadFailure = ($case -eq 'http')
        $failed = $false
        try {
            [void](Invoke-VerifiedReleaseScript -Mode Install -Repository owner/repo -ReleaseTag v1.0.0 -WebRequestInvoker $web -ChildInvoker $child)
        } catch { $failed = $true }
        $script:downloadFailure = $false
        if (-not $failed) { throw "$case case did not fail closed" }
    }

    $leftovers = @(Get-ChildItem -LiteralPath ([System.IO.Path]::GetTempPath()) -Directory -Filter 'AC6Bootstrap-*' -ErrorAction SilentlyContinue)
    if ($leftovers.Count -ne 0) { throw 'bootstrap temporary directory was not cleaned' }
    Write-Host 'Verified bootstrap tests: OK'
} finally {
    if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
}
