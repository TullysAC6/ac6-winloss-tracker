$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$installerPath = Join-Path $root 'install.ps1'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($installerPath, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw 'install.ps1 syntax is invalid' }

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

$installer = Get-Content -LiteralPath $installerPath -Raw
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
