param(
    [int]$Port = 18789
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$OpenClawRoot = "E:\openclaw\openclaw-main"
$Node = "E:\nodejs\node.exe"
$OpenClawEntry = Join-Path $OpenClawRoot "openclaw.mjs"
$PidPath = Join-Path $Root ".openclaw-gateway.pid"
$LogDir = Join-Path $Root "data\logs"
$OutLog = Join-Path $LogDir "openclaw-gateway.out.log"
$ErrLog = Join-Path $LogDir "openclaw-gateway.err.log"

function Test-TcpPort {
    param(
        [string]$HostName,
        [int]$TargetPort
    )
    $Client = [System.Net.Sockets.TcpClient]::new()
    try {
        $Async = $Client.BeginConnect($HostName, $TargetPort, $null, $null)
        if (-not $Async.AsyncWaitHandle.WaitOne(1000)) {
            return $false
        }
        $Client.EndConnect($Async)
        return $true
    } catch {
        return $false
    } finally {
        $Client.Close()
    }
}

if (-not (Test-Path $Node)) {
    throw "Node executable not found: $Node"
}
if (-not (Test-Path $OpenClawEntry)) {
    throw "OpenClaw entry not found: $OpenClawEntry"
}

if (Test-TcpPort -HostName "127.0.0.1" -TargetPort $Port) {
    Write-Output "OpenClaw gateway already reachable at ws://127.0.0.1:$Port"
    exit 0
}

if (Test-Path $PidPath) {
    $ExistingPid = Get-Content -Path $PidPath -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($ExistingPid) {
        $ExistingProcess = Get-Process -Id ([int]$ExistingPid) -ErrorAction SilentlyContinue
        if ($ExistingProcess) {
            Write-Output "OpenClaw gateway process already running, pid=$ExistingPid"
            exit 0
        }
    }
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Process = Start-Process `
    -WindowStyle Hidden `
    -PassThru `
    -FilePath $Node `
    -ArgumentList @($OpenClawEntry, "gateway", "--port", [string]$Port) `
    -WorkingDirectory $OpenClawRoot `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog

$Process.Id | Set-Content -Path $PidPath -Encoding ASCII

for ($i = 0; $i -lt 120; $i++) {
    Start-Sleep -Milliseconds 750
    if (Test-TcpPort -HostName "127.0.0.1" -TargetPort $Port) {
        Write-Output "OpenClaw gateway started at ws://127.0.0.1:$Port; pid=$($Process.Id)"
        exit 0
    }
    $Process.Refresh()
    if ($Process.HasExited) {
        $Tail = ""
        if (Test-Path $ErrLog) {
            $Tail = (Get-Content -Path $ErrLog -Tail 20 -ErrorAction SilentlyContinue) -join "`n"
        }
        throw "OpenClaw gateway exited early. See $ErrLog. $Tail"
    }
}

throw "Started OpenClaw gateway pid=$($Process.Id), but port $Port did not become reachable. See $ErrLog"
