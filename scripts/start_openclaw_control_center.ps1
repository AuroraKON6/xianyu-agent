param(
    [int]$Port = 4310,
    [int]$GatewayPort = 18789,
    [switch]$NoGateway
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$CenterRoot = Join-Path $Root "openclaw-control-center"
$EnvPath = Join-Path $CenterRoot ".env"
$NodeModules = Join-Path $CenterRoot "node_modules"
$Npm = "E:\nodejs\npm.cmd"
$PidPath = Join-Path $Root ".control-center.pid"
$LogDir = Join-Path $Root "data\logs"
$OutLog = Join-Path $LogDir "openclaw-control-center.out.log"
$ErrLog = Join-Path $LogDir "openclaw-control-center.err.log"

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

function Convert-ToEnvPath {
    param([string]$Path)
    return $Path.Replace("\", "/")
}

function New-LocalToken {
    $Bytes = [byte[]]::new(32)
    $Rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $Rng.GetBytes($Bytes)
    } finally {
        $Rng.Dispose()
    }
    return [Convert]::ToBase64String($Bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function Set-EnvValue {
    param(
        [string]$Path,
        [string]$Key,
        [string]$Value
    )
    $Line = "$Key=$Value"
    $Lines = @()
    if (Test-Path $Path) {
        $Lines = @(Get-Content -Path $Path -ErrorAction SilentlyContinue)
    }
    $Pattern = "^\s*#?\s*$([Regex]::Escape($Key))="
    $Index = -1
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match $Pattern) {
            $Index = $i
            break
        }
    }
    if ($Index -ge 0) {
        $Lines[$Index] = $Line
    } else {
        $Lines += $Line
    }
    Set-Content -Path $Path -Value $Lines -Encoding UTF8
}

if (-not (Test-Path $CenterRoot)) {
    throw "Control center directory not found: $CenterRoot"
}
if (-not (Test-Path $Npm)) {
    throw "npm not found: $Npm"
}

if (-not (Test-Path $EnvPath)) {
    Copy-Item -LiteralPath (Join-Path $CenterRoot ".env.example") -Destination $EnvPath
}

$UserProfile = [Environment]::GetFolderPath("UserProfile")
$OpenClawHome = Join-Path $UserProfile ".openclaw"
$OpenClawConfig = Join-Path $OpenClawHome "openclaw.json"
$OpenClawWorkspace = Join-Path $OpenClawHome "workspace"
$RuntimeDir = Join-Path $Root "data\control-center-runtime"
$Wrapper = Join-Path $CenterRoot "scripts\openclaw-local.cmd"

Set-EnvValue -Path $EnvPath -Key "GATEWAY_URL" -Value "ws://127.0.0.1:$GatewayPort"
Set-EnvValue -Path $EnvPath -Key "OPENCLAW_CONTROL_UI_URL" -Value "http://127.0.0.1:$Port/"
Set-EnvValue -Path $EnvPath -Key "UI_TIMEZONE" -Value "Asia/Shanghai"
Set-EnvValue -Path $EnvPath -Key "OPENCLAW_HOME" -Value (Convert-ToEnvPath $OpenClawHome)
Set-EnvValue -Path $EnvPath -Key "OPENCLAW_CONFIG_PATH" -Value (Convert-ToEnvPath $OpenClawConfig)
Set-EnvValue -Path $EnvPath -Key "OPENCLAW_WORKSPACE_ROOT" -Value (Convert-ToEnvPath $OpenClawWorkspace)
Set-EnvValue -Path $EnvPath -Key "OPENCLAW_BIN_PATH" -Value (Convert-ToEnvPath $Wrapper)
Set-EnvValue -Path $EnvPath -Key "OPENCLAW_RUNTIME_DIR" -Value (Convert-ToEnvPath $RuntimeDir)
Set-EnvValue -Path $EnvPath -Key "OPENCLAW_CLI_MEMORY_STATUS_ENABLED" -Value "false"
Set-EnvValue -Path $EnvPath -Key "READONLY_MODE" -Value "true"
Set-EnvValue -Path $EnvPath -Key "APPROVAL_ACTIONS_ENABLED" -Value "false"
Set-EnvValue -Path $EnvPath -Key "APPROVAL_ACTIONS_DRY_RUN" -Value "true"
Set-EnvValue -Path $EnvPath -Key "IMPORT_MUTATION_ENABLED" -Value "false"
Set-EnvValue -Path $EnvPath -Key "IMPORT_MUTATION_DRY_RUN" -Value "false"
Set-EnvValue -Path $EnvPath -Key "LOCAL_TOKEN_AUTH_REQUIRED" -Value "true"
Set-EnvValue -Path $EnvPath -Key "MONITOR_CONTINUOUS" -Value "false"
Set-EnvValue -Path $EnvPath -Key "UI_MODE" -Value "true"
Set-EnvValue -Path $EnvPath -Key "UI_PORT" -Value ([string]$Port)

$ExistingToken = ""
if (Test-Path $EnvPath) {
    $TokenLine = Get-Content -Path $EnvPath -ErrorAction SilentlyContinue |
        Where-Object { $_ -match "^\s*LOCAL_API_TOKEN=" } |
        Select-Object -First 1
    if ($TokenLine) {
        $ExistingToken = $TokenLine.Substring($TokenLine.IndexOf("=") + 1).Trim()
    }
}
if (-not $ExistingToken -or $ExistingToken -eq "<set-a-long-random-local-token>") {
    Set-EnvValue -Path $EnvPath -Key "LOCAL_API_TOKEN" -Value (New-LocalToken)
}

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (-not $NoGateway) {
    & (Join-Path $Root "scripts\start_openclaw_gateway.ps1") -Port $GatewayPort | Out-Host
}

if (-not (Test-Path $NodeModules)) {
    Push-Location $CenterRoot
    try {
        & $Npm install --registry=https://registry.npmmirror.com
    } finally {
        Pop-Location
    }
}

if (Test-TcpPort -HostName "127.0.0.1" -TargetPort $Port) {
    Write-Output "OpenClaw Control Center already reachable at http://127.0.0.1:$Port/"
    exit 0
}

if (Test-Path $PidPath) {
    $ExistingPid = Get-Content -Path $PidPath -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($ExistingPid) {
        $ExistingProcess = Get-Process -Id ([int]$ExistingPid) -ErrorAction SilentlyContinue
        if ($ExistingProcess) {
            Write-Output "OpenClaw Control Center process already running, pid=$ExistingPid"
            exit 0
        }
    }
}

$Process = Start-Process `
    -WindowStyle Hidden `
    -PassThru `
    -FilePath $Npm `
    -ArgumentList @("run", "dev:ui") `
    -WorkingDirectory $CenterRoot `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog

$Process.Id | Set-Content -Path $PidPath -Encoding ASCII

for ($i = 0; $i -lt 24; $i++) {
    Start-Sleep -Milliseconds 750
    if (Test-TcpPort -HostName "127.0.0.1" -TargetPort $Port) {
        Write-Output "OpenClaw Control Center started at http://127.0.0.1:$Port/; pid=$($Process.Id)"
        exit 0
    }
    $Process.Refresh()
    if ($Process.HasExited) {
        $Tail = ""
        if (Test-Path $ErrLog) {
            $Tail = (Get-Content -Path $ErrLog -Tail 20 -ErrorAction SilentlyContinue) -join "`n"
        }
        throw "OpenClaw Control Center exited early. See $ErrLog. $Tail"
    }
}

throw "Started OpenClaw Control Center pid=$($Process.Id), but port $Port did not become reachable. See $ErrLog"
