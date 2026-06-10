$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$PidPath = Join-Path $Root ".server.pid"

if (-not (Test-Path $PidPath)) {
    Write-Output "No .server.pid found; nothing to stop."
    exit 0
}

$PidText = (Get-Content -Path $PidPath -ErrorAction SilentlyContinue | Select-Object -First 1)
if (-not $PidText) {
    Remove-Item -LiteralPath $PidPath -Force
    Write-Output "Empty .server.pid removed."
    exit 0
}

$Process = Get-Process -Id ([int]$PidText) -ErrorAction SilentlyContinue
if (-not $Process) {
    Remove-Item -LiteralPath $PidPath -Force
    Write-Output "Recorded process is not running; .server.pid removed."
    exit 0
}

Stop-Process -Id $Process.Id
Remove-Item -LiteralPath $PidPath -Force
Write-Output "Stopped xianyu workflow pid=$($Process.Id)."
