$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
$readmePath = Join-Path $root 'README.md'
$readme = [System.IO.File]::ReadAllText($readmePath)
$bootstrapPath = Join-Path $root 'bootstrap.ps1'
$expectedHash = '82B223413A44BF9FDBBF399E7EED2AF6983794151DD25C9EE939B569BCD5881B'
$expectedTag = 'v1.2.0'
$repository = 'TullysAC6/ac6-winloss-tracker'
$expectedBootstrapUrl = "https://raw.githubusercontent.com/$repository/refs/tags/$expectedTag/bootstrap.ps1"
$pinnedReleaseApiUrl = "https://api.github.com/repos/$repository/releases/tags/$expectedTag"
$latestReleaseApiUrl = "https://api.github.com/repos/$repository/releases/latest"

# The commands are published inside the tag they name, so that tag has to be the
# version this tree ships.
$versionSource = [System.IO.File]::ReadAllText((Join-Path $root 'app_paths.py'))
$versionMatch = [Regex]::Match($versionSource, '(?m)^VERSION\s*=\s*["''](\d+\.\d+\.\d+)["'']\s*$')
if (-not $versionMatch.Success -or ('v' + $versionMatch.Groups[1].Value) -cne $expectedTag) {
    throw "README release tag $expectedTag does not match app_paths.py VERSION"
}

function Get-ReadmeCommand {
    param([Parameter(Mandatory = $true)][string]$Heading)
    $pattern = '(?ms)^## ' + [Regex]::Escape($Heading) + '\s+.*?^```powershell\s*\r?\n(?<command>[^\r\n]+)\r?\n```'
    $matches = [Regex]::Matches($readme, $pattern)
    if ($matches.Count -ne 1) {
        throw "README command section was not found exactly once: $Heading"
    }
    return $matches[0].Groups['command'].Value
}

function Get-ExpectedBootstrapArguments {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][bool]$IsUninstall
    )
    # Without -ReleaseTag the bootstrap asks GitHub for releases/latest, so a
    # tag-pinned command could still install another version: an older Release
    # before this one is published, or a newer one afterwards.
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $FilePath)
    if ($IsUninstall) { $arguments += @('-Mode', 'Uninstall') }
    $arguments += @('-ReleaseTag', $expectedTag)
    return $arguments
}

function Assert-ReadmeCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][bool]$IsUninstall
    )
    $kind = if ($IsUninstall) { 'uninstall' } else { 'install' }
    if ($Command -match '^(?i)powershell(?:\.exe)?\s') {
        throw 'README command must run directly in the opened PowerShell session'
    }
    if ($Command -notmatch ('refs/tags/' + [Regex]::Escape($expectedTag) + '/bootstrap\.ps1')) {
        throw "README command does not use the immutable $expectedTag bootstrap"
    }
    if ($Command -notmatch [Regex]::Escape($expectedHash)) {
        throw 'README command does not contain the expected bootstrap SHA-256'
    }
    if ($IsUninstall -and $Command -notmatch '-Mode Uninstall') {
        throw 'README uninstall command does not select uninstall mode'
    }
    if (-not $IsUninstall -and $Command -match '-Mode Uninstall') {
        throw 'README install command unexpectedly selects uninstall mode'
    }

    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseInput(
        $Command, [ref]$tokens, [ref]$errors
    )
    if ($errors.Count -gt 0) {
        $errors | Format-List -Force
        throw 'README command has PowerShell syntax errors'
    }
    $exitStatements = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.ExitStatementAst]
    }, $true))
    if ($exitStatements.Count -ne 0) {
        throw 'README command must not terminate the current PowerShell session'
    }
    foreach ($name in @('u', 'p')) {
        $variables = @($ast.FindAll({
            param($node)
            $node -is [System.Management.Automation.Language.VariableExpressionAst] -and
                $node.VariablePath.UserPath -ceq $name
        }, $true))
        if ($variables.Count -lt 2) {
            throw "README command lost its `$${name} variable references"
        }
    }

    $launches = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.CommandAst] -and
            $node.GetCommandName() -ieq 'powershell.exe'
    }, $true))
    if ($launches.Count -ne 1) {
        throw "README $kind command must launch the downloaded bootstrap exactly once"
    }
    $actual = @($launches[0].CommandElements | Select-Object -Skip 1 | ForEach-Object { $_.Extent.Text })
    $expected = @(Get-ExpectedBootstrapArguments -FilePath '$p' -IsUninstall $IsUninstall)
    if (($actual -join ' ') -cne ($expected -join ' ')) {
        throw ("README $kind command does not pin the bootstrap to -ReleaseTag $expectedTag. " +
            "Expected: $($expected -join ' ') Found: $($actual -join ' ')")
    }
}

function Invoke-ReadmeCommandContinuationTest {
    param([Parameter(Mandatory = $true)][string]$Command)
    $testRoot = Join-Path ([IO.Path]::GetTempPath()) ('ac6-readme-command-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
    $harnessPath = Join-Path $testRoot 'harness.ps1'
    $descendantPidPath = Join-Path $testRoot 'descendant.pid'
    $afterMarkerPath = Join-Path $testRoot 'after.marker'
    $downloadUriPath = Join-Path $testRoot 'download.uri'
    $downloadFilePath = Join-Path $testRoot 'download.file'
    $launchArgumentsPath = Join-Path $testRoot 'launch.arguments'
    $hostExecutable = [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
    $escapedHost = $hostExecutable.Replace("'", "''")
    $escapedPidPath = $descendantPidPath.Replace("'", "''")
    $escapedAfterPath = $afterMarkerPath.Replace("'", "''")
    $escapedDownloadUriPath = $downloadUriPath.Replace("'", "''")
    $escapedDownloadFilePath = $downloadFilePath.Replace("'", "''")
    $escapedLaunchArgumentsPath = $launchArgumentsPath.Replace("'", "''")
    $harness = @"
`$ErrorActionPreference = 'Stop'
function Invoke-WebRequest {
    param(`$Uri, `$OutFile, [switch]`$UseBasicParsing)
    [IO.File]::WriteAllText('$escapedDownloadUriPath', [string]`$Uri)
    [IO.File]::WriteAllText('$escapedDownloadFilePath', [string]`$OutFile)
    [IO.File]::WriteAllText(`$OutFile, 'fixture')
}
function Get-FileHash {
    param(`$Path, `$Algorithm)
    return [pscustomobject]@{ Hash = '$expectedHash' }
}
function powershell.exe {
    param([Parameter(ValueFromRemainingArguments=`$true)][object[]]`$Remaining)
    [IO.File]::WriteAllLines('$escapedLaunchArgumentsPath', [string[]]@(`$Remaining | ForEach-Object { [string]`$_ }))
    `$child = Start-Process -FilePath '$escapedHost' -ArgumentList @('-NoProfile', '-Command', 'Start-Sleep -Seconds 30') -PassThru
    [IO.File]::WriteAllText('$escapedPidPath', [string]`$child.Id)
    `$global:LASTEXITCODE = 0
}
$Command
Write-Output 'AFTER_AC6_COMMAND'
[IO.File]::WriteAllText('$escapedAfterPath', 'AFTER_AC6_COMMAND')
"@
    [IO.File]::WriteAllText($harnessPath, $harness, (New-Object Text.UTF8Encoding($true)))
    $descendant = $null
    try {
        $startInfo = New-Object Diagnostics.ProcessStartInfo
        $startInfo.FileName = $hostExecutable
        $startInfo.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $harnessPath + '"'
        $startInfo.UseShellExecute = $false
        $harnessProcess = [Diagnostics.Process]::Start($startInfo)
        try {
            if (-not $harnessProcess.WaitForExit(120000)) {
                $harnessProcess.Kill()
                throw 'README command harness did not finish within 120 seconds'
            }
            $childExitCode = [int]$harnessProcess.ExitCode
        } finally {
            $harnessProcess.Dispose()
        }
        if ($childExitCode -ne 0) {
            throw "README command harness failed with exit code $childExitCode"
        }
        if (-not (Test-Path -LiteralPath $afterMarkerPath -PathType Leaf) -or
            [IO.File]::ReadAllText($afterMarkerPath) -cne 'AFTER_AC6_COMMAND') {
            throw 'control did not return after the README command'
        }
        if (-not (Test-Path -LiteralPath $descendantPidPath -PathType Leaf)) {
            throw 'README command harness did not start its long-lived descendant'
        }
        $descendantPid = [int]([IO.File]::ReadAllText($descendantPidPath))
        $descendant = Get-Process -Id $descendantPid -ErrorAction Stop
        if ($descendant.HasExited) {
            throw 'long-lived descendant did not remain alive after the README command returned'
        }
        foreach ($capture in @($downloadUriPath, $downloadFilePath, $launchArgumentsPath)) {
            if (-not (Test-Path -LiteralPath $capture -PathType Leaf)) {
                throw "README command harness did not record $(Split-Path -Leaf $capture)"
            }
        }
        return [pscustomobject]@{
            Uri = [IO.File]::ReadAllText($downloadUriPath)
            OutFile = [IO.File]::ReadAllText($downloadFilePath)
            Arguments = [string[]][IO.File]::ReadAllLines($launchArgumentsPath)
        }
    } finally {
        if ($null -ne $descendant -and -not $descendant.HasExited) {
            Stop-Process -Id $descendant.Id -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $testRoot) {
            Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Assert-BootstrapInvocation {
    param(
        [Parameter(Mandatory = $true)]$Invocation,
        [Parameter(Mandatory = $true)][bool]$IsUninstall
    )
    $kind = if ($IsUninstall) { 'uninstall' } else { 'install' }
    if ($Invocation.Uri -cne $expectedBootstrapUrl) {
        throw "README $kind command downloaded $($Invocation.Uri) instead of $expectedBootstrapUrl"
    }
    # The file that was hash-checked is the one launched, and it receives the tag.
    $expected = @(Get-ExpectedBootstrapArguments -FilePath $Invocation.OutFile -IsUninstall $IsUninstall)
    $actual = @($Invocation.Arguments)
    if ($actual.Count -ne $expected.Count -or ($actual -join "`n") -cne ($expected -join "`n")) {
        throw "README $kind command did not hand -ReleaseTag $expectedTag to the downloaded bootstrap. Received: $($actual -join ' ')"
    }
    return @($actual | Select-Object -Skip 5)
}

function New-ReleaseFixture {
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)][string]$TagName,
        [Parameter(Mandatory = $true)][hashtable]$Routes,
        [Parameter(Mandatory = $true)][string]$MetadataUrl
    )
    $releaseDirectory = Join-Path $Directory $TagName
    New-Item -ItemType Directory -Path $releaseDirectory -Force | Out-Null
    $encoding = New-Object System.Text.UTF8Encoding($false)
    $assets = New-Object 'System.Collections.Generic.List[object]'
    foreach ($name in @('install.ps1', 'uninstall.ps1')) {
        $scriptPath = Join-Path $releaseDirectory $name
        [System.IO.File]::WriteAllText($scriptPath, '[CmdletBinding()] param([string]$SourceTag) exit 0', $encoding)
        $scriptHash = (Get-FileHash -LiteralPath $scriptPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $checksumPath = "$scriptPath.sha256"
        [System.IO.File]::WriteAllText($checksumPath, "$scriptHash *$name`n", $encoding)
        $checksumHash = (Get-FileHash -LiteralPath $checksumPath -Algorithm SHA256).Hash.ToLowerInvariant()
        foreach ($asset in @(
            [pscustomobject]@{ Name = $name; Path = $scriptPath; Hash = $scriptHash },
            [pscustomobject]@{ Name = "$name.sha256"; Path = $checksumPath; Hash = $checksumHash }
        )) {
            $url = "https://github.com/$repository/releases/download/$TagName/$($asset.Name)"
            $Routes[$url] = $asset.Path
            # Windows PowerShell 5.1 cannot pass an [ordered] literal straight to a .NET method,
            # nor apply @() to a generic List, so both go through variables and ToArray().
            $entry = [ordered]@{ name = $asset.Name; browser_download_url = $url; digest = "sha256:$($asset.Hash)" }
            $assets.Add($entry)
        }
    }
    $metadataPath = Join-Path $releaseDirectory 'release.json'
    $metadata = [ordered]@{
        tag_name = $TagName
        draft = $false
        prerelease = $false
        assets = $assets.ToArray()
    } | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText($metadataPath, $metadata, $encoding)
    $Routes[$MetadataUrl] = $metadataPath
}

function Invoke-PinnedBootstrapChain {
    param(
        [Parameter(Mandatory = $true)][string[]]$ScriptArguments,
        [Parameter(Mandatory = $true)][hashtable]$Routes
    )
    if (($ScriptArguments.Count % 2) -ne 0) {
        throw "README bootstrap arguments are not name/value pairs: $($ScriptArguments -join ' ')"
    }
    $bound = @{}
    for ($index = 0; $index -lt $ScriptArguments.Count; $index += 2) {
        $parameter = [Regex]::Match($ScriptArguments[$index], '^-([A-Za-z]+)$')
        if (-not $parameter.Success) {
            throw "unexpected README bootstrap argument: $($ScriptArguments[$index])"
        }
        $bound[$parameter.Groups[1].Value] = $ScriptArguments[$index + 1]
    }
    $state = @{
        Requested = New-Object 'System.Collections.Generic.List[string]'
        ChildInvoked = $false
        ChildMode = $null
        ChildTag = $null
    }
    $outcome = @(& {
        param($Bound, $BootstrapScript, $State, $Routes)
        # Bind exactly what the README passes through bootstrap.ps1's own parameters.
        # An argument the bootstrap does not declare fails here.
        . $BootstrapScript -LibraryOnly @Bound
        $web = {
            param($Uri, $OutFile)
            $State.Requested.Add([string]$Uri)
            if (-not $Routes.ContainsKey([string]$Uri)) { throw "fixture HTTP 404 Not Found: $Uri" }
            Copy-Item -LiteralPath $Routes[[string]$Uri] -Destination $OutFile -Force
        }
        $child = {
            param($ScriptPath, $SelectedMode, $VerifiedReleaseTag)
            $State.ChildInvoked = $true
            $State.ChildMode = $SelectedMode
            $State.ChildTag = $VerifiedReleaseTag
            return 0
        }
        $failure = $null
        try {
            [void](Invoke-VerifiedReleaseScript -Mode $Mode -Repository $Repository -ReleaseTag $ReleaseTag `
                -WebRequestInvoker $web -ChildInvoker $child)
        } catch {
            $failure = $_.Exception.Message
        }
        [pscustomobject]@{ Mode = $Mode; Repository = $Repository; ReleaseTag = $ReleaseTag; Failure = $failure }
    } $bound $bootstrapPath $state $Routes)[-1]
    return [pscustomobject]@{
        Mode = $outcome.Mode
        Repository = $outcome.Repository
        ReleaseTag = $outcome.ReleaseTag
        Failure = $outcome.Failure
        Requested = $state.Requested.ToArray()
        ChildInvoked = $state.ChildInvoked
        ChildMode = $state.ChildMode
        ChildTag = $state.ChildTag
    }
}

function Assert-PinnedReleaseChain {
    param(
        [Parameter(Mandatory = $true)][string[]]$ScriptArguments,
        [Parameter(Mandatory = $true)][bool]$IsUninstall
    )
    $kind = if ($IsUninstall) { 'uninstall' } else { 'install' }
    $expectedMode = if ($IsUninstall) { 'Uninstall' } else { 'Install' }
    $assetName = if ($IsUninstall) { 'uninstall.ps1' } else { 'install.ps1' }
    $existingTemporary = @(Get-ChildItem -LiteralPath ([IO.Path]::GetTempPath()) -Directory -Filter 'AC6Bootstrap-*' -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty FullName)
    $fixtureRoot = Join-Path ([IO.Path]::GetTempPath()) ('ac6-readme-release-chain-' + [Guid]::NewGuid().ToString('N'))
    try {
        # A. The tag exists but its Release is not published yet, while an older
        #    Release is still latest. The command must stop, not install that one.
        $routes = @{}
        New-ReleaseFixture -Directory $fixtureRoot -TagName 'v1.1.1' -Routes $routes -MetadataUrl $latestReleaseApiUrl
        $run = Invoke-PinnedBootstrapChain -ScriptArguments $ScriptArguments -Routes $routes
        if ($run.Mode -cne $expectedMode -or $run.ReleaseTag -cne $expectedTag -or $run.Repository -cne $repository) {
            throw "README $kind command bound Mode=$($run.Mode) ReleaseTag=$($run.ReleaseTag) Repository=$($run.Repository)"
        }
        if ($null -eq $run.Failure) {
            throw "README $kind command did not fail closed while the $expectedTag Release is missing"
        }
        if ($run.ChildInvoked) {
            throw "README $kind command ran a script from another Release while $expectedTag is missing"
        }
        if (($run.Requested -join ' ') -cne $pinnedReleaseApiUrl) {
            throw "README $kind command requested $($run.Requested -join ', ') instead of only $pinnedReleaseApiUrl"
        }

        # B. The Release is published and a newer one has become latest. The
        #    command must still resolve its own Release and its own asset.
        $routes = @{}
        New-ReleaseFixture -Directory $fixtureRoot -TagName $expectedTag -Routes $routes -MetadataUrl $pinnedReleaseApiUrl
        New-ReleaseFixture -Directory $fixtureRoot -TagName 'v1.3.0' -Routes $routes -MetadataUrl $latestReleaseApiUrl
        $run = Invoke-PinnedBootstrapChain -ScriptArguments $ScriptArguments -Routes $routes
        if ($null -ne $run.Failure) {
            throw "README $kind command failed against the published $expectedTag Release: $($run.Failure)"
        }
        $assetBase = "https://github.com/$repository/releases/download/$expectedTag"
        $expectedRequests = @($pinnedReleaseApiUrl, "$assetBase/$assetName", "$assetBase/$assetName.sha256")
        if (($run.Requested -join ' ') -cne ($expectedRequests -join ' ')) {
            throw "README $kind command requested $($run.Requested -join ', ')"
        }
        if (-not $run.ChildInvoked -or $run.ChildMode -cne $expectedMode -or $run.ChildTag -cne $expectedTag) {
            throw "README $kind command launched Mode=$($run.ChildMode) Tag=$($run.ChildTag) instead of $expectedMode $expectedTag"
        }

        # C. The tag endpoint answers with metadata for a different Release.
        $routes = @{}
        New-ReleaseFixture -Directory $fixtureRoot -TagName 'v1.1.1' -Routes $routes -MetadataUrl $pinnedReleaseApiUrl
        $run = Invoke-PinnedBootstrapChain -ScriptArguments $ScriptArguments -Routes $routes
        if ($null -eq $run.Failure -or $run.ChildInvoked) {
            throw "README $kind command accepted metadata for another Release as $expectedTag"
        }
    } finally {
        if (Test-Path -LiteralPath $fixtureRoot) {
            Remove-Item -LiteralPath $fixtureRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    $leftovers = @(Get-ChildItem -LiteralPath ([IO.Path]::GetTempPath()) -Directory -Filter 'AC6Bootstrap-*' -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notin $existingTemporary })
    if ($leftovers.Count -ne 0) { throw 'bootstrap temporary directory was not cleaned' }
}

$installCommand = Get-ReadmeCommand -Heading 'インストール / 更新'
$uninstallCommand = Get-ReadmeCommand -Heading 'アンインストール'
Assert-ReadmeCommand -Command $installCommand -IsUninstall $false
Assert-ReadmeCommand -Command $uninstallCommand -IsUninstall $true
$installArguments = @(Assert-BootstrapInvocation -Invocation (Invoke-ReadmeCommandContinuationTest -Command $installCommand) -IsUninstall $false)
$uninstallArguments = @(Assert-BootstrapInvocation -Invocation (Invoke-ReadmeCommandContinuationTest -Command $uninstallCommand) -IsUninstall $true)
Assert-PinnedReleaseChain -ScriptArguments $installArguments -IsUninstall $false
Assert-PinnedReleaseChain -ScriptArguments $uninstallArguments -IsUninstall $true

Write-Host 'README PowerShell commands: OK'
