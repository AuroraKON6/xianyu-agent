param(
    [int]$Port = 4310,
    [int]$GatewayPort = 18789
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

& (Join-Path $Root "scripts\start_openclaw_control_center.ps1") -Port $Port -GatewayPort $GatewayPort | Out-Host

$Url = "http://127.0.0.1:$Port/?section=overview&lang=zh"
Start-Process $Url
Write-Output "OpenClaw Control Center opened: $Url"
