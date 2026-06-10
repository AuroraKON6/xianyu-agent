param(
    [int]$Port = 8788
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$HealthUrl = "http://127.0.0.1:$Port/health"
$ConsoleUrl = "http://127.0.0.1:$Port/console"

Set-Location $Root

try {
    Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 3 | Out-Null
} catch {
    & (Join-Path $Root "scripts\start_xianyu_workflow.ps1") -Port $Port | Out-Host
}

Start-Process $ConsoleUrl
Write-Output "Xianyu Agent console opened: $ConsoleUrl"
