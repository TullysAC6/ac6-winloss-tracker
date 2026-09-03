$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
$readmePath = Join-Path $root 'README.md'
$readme = [System.IO.File]::ReadAllText($readmePath)
$expectedHash = '39E7E8C54239F1FA61666FF4C9199AFF6BF86B5937C7F69C6B14EBBC59D1C9E8'

function Get-ReadmeCommand {
    param([Parameter(Mandatory = $true)][string]$Heading)
    $pattern = '(?ms)^## ' + [Regex]::Escape($Heading) + '\s+.*?^```powershell\s*\r?\n(?<command>[^\r\n]+)\r?\n```'
    $matches = [Regex]::Matches($readme, $pattern)
    if ($matches.Count -ne 1) {
        throw "README command section was not found exactly once: $Heading"
    }
    return $matches[0].Groups['command'].Value
}

function Assert-ReadmeCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][bool]$IsUninstall
    )
    if ($Command -match '^(?i)powershell(?:\.exe)?\s') {
        throw 'README command must run directly in the opened PowerShell session'
    }
    if ($Command -notmatch "refs/tags/v1\.0\.1/bootstrap\.ps1") {
        throw 'README command does not use the immutable v1.0.1 bootstrap'
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
}

function Invoke-ReadmeCommandContinuationTest {
    param([Parameter(Mandatory = $true)][string]$Command)
    $testRoot = Join-Path ([IO.Path]::GetTempPath()) ('ac6-readme-command-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
    $harnessPath = Join-Path $testRoot 'harness.ps1'
    $descendantPidPath = Join-Path $testRoot 'descendant.pid'
    $hostExecutable = [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
    $escapedHost = $hostExecutable.Replace("'", "''")
    $escapedPidPath = $descendantPidPath.Replace("'", "''")
    $escapedOutputPath = (Join-Path $testRoot 'descendant.out').Replace("'", "''")
    $escapedErrorPath = (Join-Path $testRoot 'descendant.err').Replace("'", "''")
    $harness = @"
`$ErrorActionPreference = 'Stop'
function Invoke-WebRequest {
    param(`$Uri, `$OutFile, [switch]`$UseBasicParsing)
    [IO.File]::WriteAllText(`$OutFile, 'fixture')
}
function Get-FileHash {
    param(`$Path, `$Algorithm)
    return [pscustomobject]@{ Hash = '$expectedHash' }
}
function powershell.exe {
    param([Parameter(ValueFromRemainingArguments=`$true)][object[]]`$Remaining)
    `$child = Start-Process -FilePath '$escapedHost' -ArgumentList @('-NoProfile', '-Command', 'Start-Sleep -Seconds 30') -PassThru -RedirectStandardOutput '$escapedOutputPath' -RedirectStandardError '$escapedErrorPath'
    [IO.File]::WriteAllText('$escapedPidPath', [string]`$child.Id)
    `$global:LASTEXITCODE = 0
}
$Command
Write-Output 'AFTER_AC6_COMMAND'
"@
    [IO.File]::WriteAllText($harnessPath, $harness, (New-Object Text.UTF8Encoding($true)))
    $descendant = $null
    try {
        $output = @(& $hostExecutable -NoProfile -ExecutionPolicy Bypass -File $harnessPath 2>&1)
        $childExitCode = $LASTEXITCODE
        if ($childExitCode -ne 0) {
            throw "README command harness failed with exit code $childExitCode`: $($output -join ' ')"
        }
        if ($output -notcontains 'AFTER_AC6_COMMAND') {
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
    } finally {
        if ($null -ne $descendant -and -not $descendant.HasExited) {
            Stop-Process -Id $descendant.Id -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $testRoot) {
            Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

$installCommand = Get-ReadmeCommand -Heading 'インストール / 更新'
$uninstallCommand = Get-ReadmeCommand -Heading 'アンインストール'
Assert-ReadmeCommand -Command $installCommand -IsUninstall $false
Assert-ReadmeCommand -Command $uninstallCommand -IsUninstall $true
Invoke-ReadmeCommandContinuationTest -Command $installCommand
Invoke-ReadmeCommandContinuationTest -Command $uninstallCommand

Write-Host 'README PowerShell commands: OK'
