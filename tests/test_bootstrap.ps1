$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'bootstrap.ps1') -LibraryOnly

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('ac6-bootstrap-test-' + [Guid]::NewGuid().ToString('N'))
$fixtureRoot = Join-Path $testRoot 'fixtures'
New-Item -ItemType Directory -Path $fixtureRoot -Force | Out-Null
$existingBootstrapDirectories = @(Get-ChildItem -LiteralPath ([System.IO.Path]::GetTempPath()) -Directory -Filter 'AC6Bootstrap-*' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)

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
$script:missingRelease = $false
$script:requestedUris = New-Object 'System.Collections.Generic.List[string]'
$web = {
    param($Uri, $OutFile)
    $script:requestedUris.Add([string]$Uri)
    if ($script:downloadFailure) { throw 'mock HTTP failure' }
    if ($script:missingRelease -and $Uri -match '/releases/tags/') { throw 'mock HTTP 404 Not Found' }
    $name = if ($Uri -match '/releases/') { 'release.json' } else { Split-Path -Leaf $Uri }
    Copy-Item -LiteralPath (Join-Path $fixtureRoot $name) -Destination $OutFile -Force
}
$script:childExitCode = 0
$script:childTag = ''
$script:childInvoked = $false
$child = { param($Path, $Mode, $Tag) $script:childInvoked = $true; $script:childTag = $Tag; return $script:childExitCode }

try {
    New-FixtureRelease
    $script:requestedUris.Clear()
    $result = Invoke-VerifiedReleaseScript -Mode Install -Repository owner/repo -ReleaseTag v1.0.0 -WebRequestInvoker $web -ChildInvoker $child
    if ($result -ne 0) { throw 'successful verified download failed' }
    if ($script:childTag -cne 'v1.0.0') { throw 'verified release tag was not passed to installer' }
    # A pinned tag fetches that Release's metadata and its assets, and nothing else.
    $expectedRequests = @(
        'https://api.github.com/repos/owner/repo/releases/tags/v1.0.0',
        'https://github.com/example/install.ps1',
        'https://github.com/example/install.ps1.sha256'
    )
    if (($script:requestedUris -join ' ') -cne ($expectedRequests -join ' ')) {
        throw "pinned bootstrap requested: $($script:requestedUris -join ', ')"
    }

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
                -VerifiedReleaseTag 'v1.2.0'
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

    # A pinned tag resolves that Release only. An empty tag is the one way to reach
    # releases/latest, and a published README command always pins its own tag.
    $pinnedApiUrl = Get-ReleaseApiUrl -Repository 'TullysAC6/ac6-winloss-tracker' -ReleaseTag 'v1.2.0'
    if ($pinnedApiUrl -cne 'https://api.github.com/repos/TullysAC6/ac6-winloss-tracker/releases/tags/v1.2.0') {
        throw "pinned release tag resolved to $pinnedApiUrl"
    }
    if ((Get-ReleaseApiUrl -Repository owner/repo -ReleaseTag '') -cne 'https://api.github.com/repos/owner/repo/releases/latest') {
        throw 'unpinned bootstrap no longer resolves releases/latest'
    }
    foreach ($badTag in @('latest', 'v1.2', '1.2.0', 'v1.2.0-rc1', 'v1.2.0/../latest')) {
        $rejected = $false
        try { [void](Get-ReleaseApiUrl -Repository owner/repo -ReleaseTag $badTag) } catch { $rejected = $true }
        if (-not $rejected) { throw "malformed release tag was accepted: $badTag" }
    }

    # A missing pinned Release fails closed in both modes and never falls back to
    # releases/latest, although this mock would serve a latest Release.
    New-FixtureRelease
    foreach ($pinnedMode in @('Install', 'Uninstall')) {
        $script:requestedUris.Clear()
        $script:childInvoked = $false
        $script:missingRelease = $true
        $failed = $false
        try {
            [void](Invoke-VerifiedReleaseScript -Mode $pinnedMode -Repository owner/repo -ReleaseTag v1.2.0 -WebRequestInvoker $web -ChildInvoker $child)
        } catch { $failed = $true } finally { $script:missingRelease = $false }
        if (-not $failed) { throw "missing pinned Release did not fail closed ($pinnedMode)" }
        if ($script:childInvoked) { throw "a release script ran although the pinned Release is missing ($pinnedMode)" }
        if (($script:requestedUris -join ' ') -cne 'https://api.github.com/repos/owner/repo/releases/tags/v1.2.0') {
            throw "pinned bootstrap requested $($script:requestedUris -join ', ') ($pinnedMode)"
        }
    }

    # Metadata for a different Release is never accepted for a pinned tag.
    New-FixtureRelease
    $script:childInvoked = $false
    $failed = $false
    try {
        [void](Invoke-VerifiedReleaseScript -Mode Install -Repository owner/repo -ReleaseTag v1.2.0 -WebRequestInvoker $web -ChildInvoker $child)
    } catch { $failed = $true }
    if (-not $failed -or $script:childInvoked) { throw 'metadata for another Release was accepted for a pinned tag' }

    $leftovers = @(Get-ChildItem -LiteralPath ([System.IO.Path]::GetTempPath()) -Directory -Filter 'AC6Bootstrap-*' -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notin $existingBootstrapDirectories })
    if ($leftovers.Count -ne 0) { throw 'bootstrap temporary directory was not cleaned' }
    Write-Host 'Verified bootstrap tests: OK'
} finally {
    if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
}
