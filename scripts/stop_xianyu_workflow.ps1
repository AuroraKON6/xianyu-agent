$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$PidPath = Join-Path $Root ".server.pid"

function Test-XianyuWorkflowProcess {
    param([int]$ProcessId)
    $ProcessInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $ProcessInfo) {
        return $false
    }
    $CommandLine = [string]$ProcessInfo.CommandLine
    return (
        $CommandLine -and
        $CommandLine.IndexOf($Root, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $CommandLine.IndexOf("xianyu_agent.cli server", [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    )
}

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

if (-not (Test-XianyuWorkflowProcess -ProcessId ([int]$PidText))) {
    Remove-Item -LiteralPath $PidPath -Force
    Write-Output "Stale .server.pid removed; the recorded PID belongs to another process."
    exit 0
}

taskkill.exe /PID $Process.Id /T /F | Out-Null
Remove-Item -LiteralPath $PidPath -Force
Write-Output "Stopped xianyu workflow pid=$($Process.Id)."
