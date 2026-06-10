param(
    [int]$Port = 0,
    [switch]$NoDeepSeek
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$PidPath = Join-Path $Root ".server.pid"

if (-not (Test-Path $Python)) {
    throw "Python virtualenv not found: $Python"
}

if ($Port -gt 0) {
    $env:SERVER_PORT = [string]$Port
}

if ($NoDeepSeek) {
    $env:DEEPSEEK_API_KEY = ""
}

if (Test-Path $PidPath) {
    $ExistingPid = (Get-Content -Path $PidPath -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($ExistingPid) {
        $ExistingProcess = Get-Process -Id ([int]$ExistingPid) -ErrorAction SilentlyContinue
        if ($ExistingProcess) {
            Write-Output "xianyu workflow already running, pid=$ExistingPid"
            exit 0
        }
    }
}

$Process = Start-Process `
    -WindowStyle Hidden `
    -PassThru `
    -FilePath $Python `
    -ArgumentList @("-m", "xianyu_agent.cli", "server") `
    -WorkingDirectory $Root

$Process.Id | Set-Content -Path $PidPath -Encoding ASCII
Start-Sleep -Seconds 4

$HealthPort = $env:SERVER_PORT
if (-not $HealthPort) {
    $HealthPort = "8788"
}
$HealthUrl = "http://127.0.0.1:" + $HealthPort + "/health"
try {
    $Health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 10
    $Health | ConvertTo-Json -Depth 5
} catch {
    throw "Started pid=$($Process.Id), but health check failed at $HealthUrl. $($_.Exception.Message)"
}
