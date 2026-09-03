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

$installCommand = Get-ReadmeCommand -Heading 'インストール / 更新'
$uninstallCommand = Get-ReadmeCommand -Heading 'アンインストール'
Assert-ReadmeCommand -Command $installCommand -IsUninstall $false
Assert-ReadmeCommand -Command $uninstallCommand -IsUninstall $true

Write-Host 'README PowerShell commands: OK'
