param(
    [string]$OpenClawRoot = "E:\openclaw\openclaw-main",
    [switch]$WorkspaceOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SkillSource = Join-Path $ProjectRoot "skills\xianyu-commerce-agent"

if (-not (Test-Path -LiteralPath $SkillSource)) {
    throw "Skill source not found: $SkillSource"
}

if (-not (Test-Path -LiteralPath $OpenClawRoot)) {
    throw "OpenClaw root not found: $OpenClawRoot"
}

$Targets = @()
if (-not $WorkspaceOnly) {
    $Targets += Join-Path $OpenClawRoot "skills\xianyu-commerce-agent"
    $Targets += Join-Path $OpenClawRoot ".agents\skills\xianyu-commerce-agent"
}

$OpenClawWorkspace = Join-Path $env:USERPROFILE ".openclaw\workspace"
if (Test-Path -LiteralPath $OpenClawWorkspace) {
    $Targets += Join-Path $OpenClawWorkspace "skills\xianyu-commerce-agent"
}

if (-not $Targets) {
    throw "No OpenClaw skill targets found."
}

foreach ($Target in $Targets) {
    $Parent = Split-Path -Parent $Target
    if (-not (Test-Path -LiteralPath $Parent)) {
        New-Item -ItemType Directory -Path $Parent | Out-Null
    }
    if (Test-Path -LiteralPath $Target) {
        Remove-Item -LiteralPath $Target -Recurse -Force
    }
    Copy-Item -LiteralPath $SkillSource -Destination $Target -Recurse -Force
    Write-Output "Installed xianyu-commerce-agent skill -> $Target"
}

$SnippetPath = Join-Path $ProjectRoot "openclaw\openclaw-config-snippet.jsonc"
Write-Output "Optional config snippet: $SnippetPath"
