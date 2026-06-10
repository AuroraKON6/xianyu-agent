$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$PidPath = Join-Path $Root ".control-center.pid"

$Ids = @()
if (Test-Path $PidPath) {
    $ExistingPid = Get-Content -Path $PidPath -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($ExistingPid) {
        $Ids += [int]$ExistingPid
    }
}

$Processes = Get-CimInstance Win32_Process -Filter "name = 'node.exe'" |
    Where-Object {
        $_.CommandLine -like "*openclaw-control-center*" -or
        ($_.CommandLine -like "*openclaw.mjs*" -and $_.CommandLine -notlike "*openclaw.mjs*gateway*")
    }
foreach ($Process in $Processes) {
    $Ids += [int]$Process.ProcessId
}

$Ids = $Ids | Sort-Object -Unique
foreach ($Id in $Ids) {
    Stop-Process -Id $Id -Force -ErrorAction SilentlyContinue
}

Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
Write-Output "Stopped OpenClaw Control Center process(es): $($Ids -join ', ')"
