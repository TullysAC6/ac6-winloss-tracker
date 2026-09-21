$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$installerPath = Join-Path $root 'install.ps1'
$tokens = $null
$errors = $null
$utf8 = New-Object System.Text.UTF8Encoding($false)
$installerSource = [System.IO.File]::ReadAllText($installerPath, $utf8)
$ast = [System.Management.Automation.Language.Parser]::ParseInput($installerSource, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) {
    $errors | Format-List -Force
    throw 'install.ps1 syntax is invalid'
}

foreach ($name in @('Get-SupportedPythonRole', 'Select-SupportedPythonCandidate', 'Test-IsPythonFoundationSigner')) {
    $functionAst = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    if (-not $functionAst) { throw "Installer function missing: $name" }
    Invoke-Expression $functionAst.Extent.Text
}
$preferredPythonMinor = 14
$fallbackPythonMinor = 13

function Assert-Role($Version, $Expected, $ReleaseLevel = 'final', $GilDisabled = '0') {
    $actual = Get-SupportedPythonRole -Major $Version[0] -Minor $Version[1] -ReleaseLevel $ReleaseLevel -GilDisabled $GilDisabled
    if ($actual -ne $Expected) { throw "Selection mismatch for $($Version -join '.'): expected=$Expected actual=$actual" }
}
Assert-Role @(3, 14, 0) 'preferred'
Assert-Role @(3, 14, 99) 'preferred'
Assert-Role @(3, 13, 0) 'fallback'
Assert-Role @(3, 13, 99) 'fallback'
Assert-Role @(3, 12, 99) $null
Assert-Role @(3, 15, 0) $null
Assert-Role @(3, 14, 0) $null 'candidate' '0'
Assert-Role @(3, 14, 0) $null 'final' '1'

function Candidate($Version, $Role) {
    $parts = @($Version -split '\.' | ForEach-Object { [int]$_ })
    [PSCustomObject]@{ Version=$Version; Major=$parts[0]; Minor=$parts[1]; Patch=$parts[2]; Role=$Role; Priority=$(if ($Role -eq 'preferred') { 2 } else { 1 }) }
}
if ((Select-SupportedPythonCandidate @((Candidate '3.13.99' fallback), (Candidate '3.14.0' preferred))).Version -ne '3.14.0') { throw '3.14 preference failed' }
if ((Select-SupportedPythonCandidate @((Candidate '3.14.1' preferred), (Candidate '3.14.9' preferred))).Version -ne '3.14.9') { throw 'newest 3.14 selection failed' }
if ((Select-SupportedPythonCandidate @((Candidate '3.13.1' fallback), (Candidate '3.13.9' fallback))).Version -ne '3.13.9') { throw 'newest 3.13 selection failed' }
if (Select-SupportedPythonCandidate @()) { throw 'empty candidate selection must fail closed' }

# Windows PowerShell 5.1 must not array-subexpress a Generic List directly.
$genericCandidates = New-Object 'System.Collections.Generic.List[object]'
$genericCandidates.Add((Candidate '3.13.9' fallback)) | Out-Null
$genericCandidates.Add((Candidate '3.14.9' preferred)) | Out-Null
if ((Select-SupportedPythonCandidate -Candidates $genericCandidates.ToArray()).Version -ne '3.14.9') { throw 'Generic List candidate conversion failed' }
$fallbackCandidates = New-Object 'System.Collections.Generic.List[object]'
$fallbackCandidates.Add((Candidate '3.13.9' fallback)) | Out-Null
if ((Select-SupportedPythonCandidate -Candidates $fallbackCandidates.ToArray()).Version -ne '3.13.9') { throw 'Generic List fallback conversion failed' }
if ((Get-SupportedPythonRole -Major 3 -Minor 12 -ReleaseLevel final -GilDisabled 0) -ne $null) { throw 'Python 3.12 must lead to Python 3.14 preparation' }

function SignedInfo($Status, $SignatureType, $Subject) {
    [PSCustomObject]@{ Signature=[PSCustomObject]@{ Status=$Status; SignatureType=$SignatureType; SignerCertificate=[PSCustomObject]@{ Subject=$Subject } } }
}
if (-not (Test-IsPythonFoundationSigner (SignedInfo Valid Authenticode 'CN=Python Software Foundation'))) { throw 'valid PSF signer rejected' }
if (Test-IsPythonFoundationSigner (SignedInfo NotSigned None 'CN=Python Software Foundation')) { throw 'unsigned Python accepted' }
if (Test-IsPythonFoundationSigner (SignedInfo Valid Authenticode 'CN=Unexpected Signer')) { throw 'unexpected signer accepted' }

$installer = $installerSource
foreach ($required in @('Get-AuthenticodeSignature', 'pythonw.exe signer does not match python.exe', "`$pythonWingetPackage = 'Python.Python.3.14'", "'--source', 'winget'")) {
    if (-not $installer.Contains($required)) { throw "Installer safety check missing: $required" }
}
if (-not $installer.Contains('Select-SupportedPythonCandidate -Candidates $supported.ToArray()')) { throw 'PowerShell 5.1-safe Generic List conversion missing' }
$prepare = $installer.IndexOf('Install-PythonWithWinget')
$stop = $installer.IndexOf('Stop-RunningTracker', $prepare)
if ($prepare -lt 0 -or $stop -le $prepare) { throw 'Python preparation must remain before Tracker shutdown' }
foreach ($removed in @('runtime-policy.json', 'runtime_policy.py', 'minimum_patch', 'python_policy_version')) {
    if ($installer.Contains($removed)) { throw "Overbuilt runtime policy remains: $removed" }
}
$signatureCheck = $installer.IndexOf('Get-SignedExecutableInfo -Path $fullPath')
$runtimeCheck = $installer.IndexOf('Get-SupportedPythonRole -Major')
$pip = $installer.IndexOf('Invoke-PipInstall', $runtimeCheck)
$shutdown = $installer.IndexOf('Stop-RunningTracker', $pip)
if ($signatureCheck -lt 0 -or $runtimeCheck -le $signatureCheck -or $pip -le $runtimeCheck -or $shutdown -le $pip) { throw 'Installer validation/shutdown ordering is unsafe' }
Write-Host 'Simple Python selection and installer safety ordering: OK'

# Exercise the actual filesystem transaction without launching or downloading.
foreach ($name in @('Assert-InstallPath','Remove-OwnedInstallPath','Install-SourceTree','Restore-PreviousSource')) {
    $functionAst = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    Invoke-Expression $functionAst.Extent.Text
}
function Write-InstallLog { param($Message) }
$contractRoot = Join-Path ([IO.Path]::GetTempPath()) ('ac6-install-contract-' + [guid]::NewGuid().ToString('N'))
$contractRoot = [IO.Path]::GetFullPath($contractRoot)
if (-not $contractRoot.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test root' }
try {
    $ownedRoot = Join-Path $contractRoot 'Programs\AC6WinLossTracker'
    $legacyPath = Join-Path $contractRoot 'Programs\AC6WinLossTrackerSource'
    $installPath = Join-Path $ownedRoot 'app'
    $installParent = $ownedRoot
    $script:backupPath = "$installPath.previous"
    $script:sourceSwapped = $false
    New-Item -ItemType Directory -Path $installPath -Force | Out-Null
    Set-Content (Join-Path $installPath 'sentinel') 'old'
    foreach ($bad in @($contractRoot,($ownedRoot + '-similar'),(Join-Path $ownedRoot '..\other'))) {
        $refused = $false
        try { Remove-OwnedInstallPath $bad } catch { $refused = $true }
        if (-not $refused -or -not (Test-Path (Join-Path $installPath 'sentinel'))) { throw 'Path ownership guard failed' }
    }
    $failed = $false
    try { Install-SourceTree -SourcePath (Join-Path $contractRoot 'missing-source') } catch { $failed = $true }
    if (-not $failed -or -not (Test-Path (Join-Path $installPath 'sentinel')) -or (Test-Path $script:backupPath)) { throw 'Failure after old-tree rename lost original source' }
    $source = Join-Path $contractRoot 'new-source'
    New-Item -ItemType Directory -Path $source | Out-Null
    Set-Content (Join-Path $source 'new') 'new'
    Install-SourceTree -SourcePath $source
    if (-not $script:sourceSwapped -or -not (Test-Path (Join-Path $script:backupPath 'sentinel'))) { throw 'Swap did not retain rollback source' }
    Restore-PreviousSource
    if ($script:sourceSwapped -or -not (Test-Path (Join-Path $installPath 'sentinel')) -or (Test-Path (Join-Path $installPath 'new'))) { throw 'Source rollback failed' }
    # A cross-volume move can create only part of the new tree before failing.
    # Inject that condition after the old tree has actually been renamed.
    function Move-Item {
        param($LiteralPath,$Destination)
        if ($LiteralPath -eq $source) {
            New-Item -ItemType Directory -Path $Destination | Out-Null
            Set-Content (Join-Path $Destination 'partial') 'partial'
            throw 'Injected partial move'
        }
        Microsoft.PowerShell.Management\Move-Item -LiteralPath $LiteralPath -Destination $Destination
    }
    $failed = $false
    try { Install-SourceTree -SourcePath $source } catch { $failed = $true }
    finally { Remove-Item Function:\Move-Item }
    if (-not $failed -or -not (Test-Path (Join-Path $installPath 'sentinel')) -or (Test-Path $script:backupPath) -or (Test-Path (Join-Path $installPath 'partial'))) { throw 'Partial move did not restore original source' }
    Write-Host 'Installer path guards / failure after rename / source rollback: OK'
} finally {
    if (Test-Path -LiteralPath $contractRoot) { Remove-Item -LiteralPath $contractRoot -Recurse -Force }
}
if (Test-Path -LiteralPath $contractRoot) { throw 'Installer test TEMP residue' }

# Execute the production parameter binding and source selection without starting
# the transaction. Only the prefix before data-path initialization is evaluated.
$prefixEnd = $installerSource.IndexOf('$dataPath =')
if ($prefixEnd -lt 0) { throw 'Source-selection prefix missing' }
$selectSource = [scriptblock]::Create($installerSource.Substring(0, $prefixEnd) + @'
[PSCustomObject]@{Channel=$channel;Kind=$sourceKind;Ref=$sourceRef;Url=$releaseCommitUrl;Version=$version}
'@)
$candidateSha = 'abcdef01' * 5
foreach ($arguments in @(@{}, @{SourceTag='v1.2.0'})) {
    $selected = & $selectSource @arguments
    if ($selected.Channel -ne 'stable' -or $selected.Kind -ne 'tag' -or $selected.Ref -ne 'v1.2.0' -or
        $selected.Url -ne 'https://api.github.com/repos/TullysAC6/ac6-winloss-tracker/commits/v1.2.0') { throw 'Stable selection changed' }
}
$selected = & $selectSource -SourceCommit $candidateSha.ToUpperInvariant()
if ($selected.Channel -ne 'candidate' -or $selected.Kind -ne 'commit' -or $selected.Ref -cne $candidateSha -or
    $selected.Url -ne "https://api.github.com/repos/TullysAC6/ac6-winloss-tracker/commits/$candidateSha") { throw 'Exact commit selection failed' }
foreach ($bad in @('', 'abcdef0', 'feature/test', 'main', 'HEAD', 'v1.2.0', 'refs/pull/40/head', ('g'*40), ($candidateSha + "`n"))) {
    $refused = $false
    try { $null = & $selectSource -SourceCommit $bad } catch { $refused = $true }
    if (-not $refused) { throw "Invalid commit accepted: $bad" }
}
$refused = $false
try { $null = & $selectSource -SourceTag v1.2.0 -SourceCommit $candidateSha } catch { $refused = $true }
if (-not $refused) { throw 'Ambiguous source modes accepted' }

foreach ($name in @('Resolve-StableCommit','Write-InstalledMetadata','Get-InstalledRevision')) {
    $functionAst = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    if (-not $functionAst) { throw "Installer function missing: $name" }
    Invoke-Expression $functionAst.Extent.Text
}
function Write-Step { param($Message) }
function Invoke-WebRequest {
    [CmdletBinding()]param($Uri, [switch]$UseBasicParsing, $TimeoutSec, $Headers)
    if ($Uri -cne $script:expectedCommitUrl) { throw "Unexpected source request: $Uri" }
    $script:commitRequests++
    [PSCustomObject]@{Content=(@{sha=$script:returnedSha} | ConvertTo-Json)}
}
try {
    $channel = 'candidate'; $sourceKind = 'commit'; $sourceRef = $candidateSha
    $releaseCommitUrl = $selected.Url
    $script:expectedCommitUrl = $selected.Url
    $script:returnedSha = $candidateSha.ToUpperInvariant()
    $script:commitRequests = 0
    if ((Resolve-StableCommit) -cne $candidateSha -or $script:commitRequests -ne 1) { throw 'Exact resolution failed' }
    foreach ($bad in @(('b'*40), 'abcdef0', ($candidateSha + "`n"))) {
        $script:returnedSha = $bad
        $refused = $false
        try { $null = Resolve-StableCommit } catch { $refused = $true }
        if (-not $refused) { throw 'Mismatched/malformed GitHub commit accepted' }
    }
    $channel = 'stable'; $sourceKind = 'tag'; $sourceRef = 'v1.2.0'
    $releaseCommitUrl = 'https://api.github.com/repos/TullysAC6/ac6-winloss-tracker/commits/v1.2.0'
    $script:expectedCommitUrl = $releaseCommitUrl
    $script:returnedSha = $candidateSha
    if ((Resolve-StableCommit) -cne $candidateSha) { throw 'Stable tag resolution changed' }

    New-Item -ItemType Directory -Path $contractRoot | Out-Null
    $dataPath = $contractRoot
    $version = '1.2.0'; $script:activeEnvironment = 'fixture-venv'; $script:lockHash = 'fixture-hash'
    $python = [PSCustomObject]@{Version='3.14.7';Role='preferred';BasePythonPath='fixture-python'}
    Write-InstalledMetadata -Commit $candidateSha -Python $python
    $metadata = Get-Content (Join-Path $dataPath 'installed-version.json') -Raw | ConvertFrom-Json
    $stableKeys = 'base_python_path,channel,environment_path,installed_at,python_role,python_version,requirements_sha256,resolved_commit,version'
    if ((($metadata.PSObject.Properties.Name | Sort-Object) -join ',') -ne $stableKeys -or
        $metadata.channel -ne 'stable' -or $metadata.version -ne '1.2.0' -or (Get-InstalledRevision) -ne $candidateSha) { throw 'Stable metadata contract changed' }
    $channel = 'candidate'; $sourceKind = 'commit'; $sourceRef = $candidateSha
    Write-InstalledMetadata -Commit $candidateSha -Python $python
    $metadata = Get-Content (Join-Path $dataPath 'installed-version.json') -Raw | ConvertFrom-Json
    if ($metadata.channel -ne 'candidate' -or $metadata.source_kind -ne 'commit' -or $metadata.source_ref -cne $candidateSha -or
        $metadata.resolved_commit -cne $candidateSha -or (Get-InstalledRevision) -ne $candidateSha) { throw 'Candidate metadata identity lost' }
} finally {
    Remove-Item Function:\Invoke-WebRequest
    if (Test-Path -LiteralPath $contractRoot) { Remove-Item -LiteralPath $contractRoot -Recurse -Force }
}
if (-not $installerSource.Contains('$archiveUrl = "https://github.com/$repository/archive/$resolvedCommit.zip"')) { throw 'Archive must use verified immutable commit' }
$bootstrap = [IO.File]::ReadAllText((Join-Path $root 'bootstrap.ps1'))
if ($bootstrap.Contains('SourceCommit') -or -not $bootstrap.Contains('-SourceTag')) { throw 'Public bootstrap must remain stable-only' }
Write-Host 'Stable/candidate binding, immutable GitHub identity, archive and metadata contracts: OK'
