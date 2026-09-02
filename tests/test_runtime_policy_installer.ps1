$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$installerPath = Join-Path $root 'install.ps1'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $installerPath, [ref]$tokens, [ref]$errors
)
if ($errors.Count -gt 0) { throw 'install.ps1 syntax is invalid' }

foreach ($name in @('Read-RuntimePolicy', 'Get-RuntimePolicyRole', 'Select-SupportedPythonCandidate', 'Test-IsPythonFoundationSigner')) {
    $functionAst = $ast.Find(
        { param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name },
        $true
    )
    if (-not $functionAst) { throw "Installer function missing: $name" }
    Invoke-Expression $functionAst.Extent.Text
}

$script:runtimePolicy = Read-RuntimePolicy -SourcePath $root

function Assert-Role {
    param($Version, $Expected, $ReleaseLevel = 'final', $GilDisabled = '0')
    $actual = Get-RuntimePolicyRole -Major $Version[0] -Minor $Version[1] -Patch $Version[2] `
        -ReleaseLevel $ReleaseLevel -GilDisabled $GilDisabled
    if ($actual -ne $Expected) {
        throw "Policy mismatch for $($Version -join '.'): expected=$Expected actual=$actual"
    }
}

Assert-Role @(3, 14, 7) 'preferred'
Assert-Role @(3, 14, 8) 'preferred'
Assert-Role @(3, 14, 6) $null
Assert-Role @(3, 13, 15) 'fallback'
Assert-Role @(3, 13, 16) 'fallback'
Assert-Role @(3, 13, 14) $null
Assert-Role @(3, 12, 10) $null
Assert-Role @(3, 10, 99) $null
Assert-Role @(3, 15, 0) $null
Assert-Role @(3, 14, 7) $null 'candidate' '0'
Assert-Role @(3, 14, 7) $null 'final' '1'

function Candidate($Version, $Role) {
    $parts = @($Version -split '\.' | ForEach-Object { [int]$_ })
    return [PSCustomObject]@{
        Version = $Version
        Major = $parts[0]
        Minor = $parts[1]
        Patch = $parts[2]
        Role = $Role
        Priority = $(if ($Role -eq 'preferred') { 2 } else { 1 })
    }
}

if ((Select-SupportedPythonCandidate @((Candidate '3.13.15' fallback), (Candidate '3.14.7' preferred))).Version -ne '3.14.7') { throw 'Preferred selection failed' }
if ((Select-SupportedPythonCandidate @((Candidate '3.14.7' preferred), (Candidate '3.14.8' preferred))).Version -ne '3.14.8') { throw 'Highest preferred patch selection failed' }
if ((Select-SupportedPythonCandidate @((Candidate '3.13.15' fallback), (Candidate '3.13.16' fallback))).Version -ne '3.13.16') { throw 'Highest fallback patch selection failed' }
if (Select-SupportedPythonCandidate @()) { throw 'Empty candidate selection must fail closed' }

function SignedInfo($Status, $SignatureType, $Subject) {
    return [PSCustomObject]@{
        Signature = [PSCustomObject]@{
            Status = $Status
            SignatureType = $SignatureType
            SignerCertificate = [PSCustomObject]@{ Subject = $Subject }
        }
    }
}
if (-not (Test-IsPythonFoundationSigner (SignedInfo Valid Authenticode 'CN=Python Software Foundation'))) { throw 'Valid PSF signer rejected' }
if (Test-IsPythonFoundationSigner (SignedInfo NotSigned None 'CN=Python Software Foundation')) { throw 'Unsigned Python accepted' }
if (Test-IsPythonFoundationSigner (SignedInfo Valid Authenticode 'CN=Unexpected Signer')) { throw 'Invalid signer accepted' }

$installer = Get-Content -LiteralPath $installerPath -Raw
foreach ($required in @(
    'Get-AuthenticodeSignature',
    "`$signature.Status -ne 'Valid'",
    'Python Software Foundation',
    'pythonw.exe signer does not match python.exe',
    "Sort-Object ``",
    "'--source', 'winget'",
    'runtime-policy.json'
)) {
    if (-not $installer.Contains($required)) { throw "Installer policy check missing: $required" }
}

$signatureCheck = $installer.IndexOf('Get-SignedExecutableInfo -Path $fullPath')
$runtimeCheck = $installer.IndexOf('Get-RuntimePolicyRole -Major')
$shutdown = $installer.IndexOf('Stop-RunningTracker', $runtimeCheck)
$pip = $installer.IndexOf('Invoke-PipInstall', $runtimeCheck)
if ($signatureCheck -lt 0 -or $runtimeCheck -le $signatureCheck -or $pip -le $runtimeCheck -or $shutdown -le $pip) {
    throw 'Installer validation/shutdown ordering is unsafe'
}

Write-Host 'Installer Runtime Policy semantics and safety ordering: OK'
