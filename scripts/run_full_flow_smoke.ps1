param(
    [switch]$KeepServer
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Smoke = Join-Path $Root "scripts\full_flow_smoke.py"
$PidPath = Join-Path $Root ".server.pid"
$StartedHere = $false

Set-Location $Root

try {
    Invoke-RestMethod -Uri "http://127.0.0.1:8788/health" -TimeoutSec 3 | Out-Null
} catch {
    & (Join-Path $Root "scripts\start_xianyu_workflow.ps1") -NoDeepSeek | Out-Host
    $StartedHere = $true
}

& $Python $Smoke

if ($StartedHere -and -not $KeepServer) {
    & (Join-Path $Root "scripts\stop_xianyu_workflow.ps1") | Out-Host
} elseif (Test-Path $PidPath) {
    Write-Output "Server left running; pid=$(Get-Content -Path $PidPath | Select-Object -First 1)"
}
