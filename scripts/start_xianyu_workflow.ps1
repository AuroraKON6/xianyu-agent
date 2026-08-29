param(
    [int]$Port = 0,
    [switch]$NoDeepSeek,
    [string]$DeepSeekModel = "deepseek-v4-flash"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$PiCli = Join-Path $Root "node_modules\@earendil-works\pi-coding-agent\dist\cli.js"
$PidPath = Join-Path $Root ".server.pid"
$LogDir = Join-Path $Root "data\logs"
$StdoutPath = Join-Path $LogDir "server.out.log"
$StderrPath = Join-Path $LogDir "server.err.log"

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

if (-not (Test-Path $Python)) {
    throw "Python virtualenv not found: $Python"
}

if (-not $NoDeepSeek) {
    $NodeCommand = Get-Command node -ErrorAction SilentlyContinue
    if (-not $NodeCommand) {
        throw "Node.js not found. Pi requires Node.js 22.19 or newer."
    }
    if (-not (Test-Path $PiCli)) {
        throw "Project-local Pi not found: $PiCli. Run npm install in $Root."
    }
    $NodeVersionText = (& $NodeCommand.Source --version).Trim().TrimStart("v")
    $NodeVersion = [version]$NodeVersionText
    if ($NodeVersion -lt [version]"22.19.0") {
        throw "Node.js $NodeVersionText is too old. Pi requires Node.js 22.19 or newer."
    }
}

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

$ProcessPath = [Environment]::GetEnvironmentVariable("Path", "Process")
if (-not $ProcessPath) {
    $ProcessPath = [Environment]::GetEnvironmentVariable("PATH", "Process")
}
$PythonHome = ""
$PyVenvConfig = Join-Path $Root ".venv\pyvenv.cfg"
if (Test-Path $PyVenvConfig) {
    $HomeLine = Get-Content -Path $PyVenvConfig | Where-Object { $_ -like "home = *" } | Select-Object -First 1
    if ($HomeLine) {
        $PythonHome = ($HomeLine -replace "^home\s*=\s*", "").Trim()
    }
}
$ExtraPaths = @(
    (Split-Path -Parent $Python),
    $PythonHome,
    $(if ($PythonHome) { Join-Path $PythonHome "DLLs" }),
    $(if ($PythonHome) { Join-Path $PythonHome "Library\bin" })
) | Where-Object { $_ -and (Test-Path $_) }
$PathParts = @()
$PathParts += $ExtraPaths
if ($ProcessPath) {
    $PathParts += ($ProcessPath -split ";")
}
$NormalizedPath = ($PathParts | Where-Object { $_ } | Select-Object -Unique) -join ";"
if ($NormalizedPath) {
    [Environment]::SetEnvironmentVariable("Path", $NormalizedPath, "Process")
    [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
}

if ($Port -gt 0) {
    $env:SERVER_PORT = [string]$Port
}

$EffectivePort = $env:SERVER_PORT
if (-not $EffectivePort) {
    $EffectivePort = "8788"
}
$env:XIANYU_API_BASE_URL = "http://127.0.0.1:$EffectivePort"

if ($NoDeepSeek) {
    $env:DEEPSEEK_API_KEY = ""
    $env:PI_ENABLED = "false"
} elseif (-not $env:PI_ENABLED) {
    $env:PI_ENABLED = "true"
}

if ($DeepSeekModel) {
    $env:DEEPSEEK_MODEL = $DeepSeekModel
}

if (Test-Path $PidPath) {
    $ExistingPid = (Get-Content -Path $PidPath -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($ExistingPid) {
        $ExistingProcess = Get-Process -Id ([int]$ExistingPid) -ErrorAction SilentlyContinue
        if ($ExistingProcess -and (Test-XianyuWorkflowProcess -ProcessId ([int]$ExistingPid))) {
            Write-Output "xianyu workflow already running, pid=$ExistingPid"
            exit 0
        }
    }
    Remove-Item -LiteralPath $PidPath -Force
}

Set-Content -Path $StdoutPath -Value "" -Encoding UTF8
Set-Content -Path $StderrPath -Value "" -Encoding UTF8

$CmdLine = 'set "PATH=' + $NormalizedPath + '" && cd /d "' + $Root + '" && "' + $Python + '" -m xianyu_agent.cli server 1>> "' + $StdoutPath + '" 2>> "' + $StderrPath + '"'
$Process = Start-Process `
    -WindowStyle Hidden `
    -PassThru `
    -FilePath "cmd.exe" `
    -ArgumentList @("/d", "/c", $CmdLine) `
    -WorkingDirectory $Root

$Process.Id | Set-Content -Path $PidPath -Encoding ASCII
Start-Sleep -Seconds 4

$HealthPort = $EffectivePort
$HealthUrl = "http://127.0.0.1:" + $HealthPort + "/health"
$Health = $null
$LastHealthError = ""
for ($Attempt = 1; $Attempt -le 30; $Attempt++) {
    try {
        $Health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2
        break
    } catch {
        $LastHealthError = $_.Exception.Message
        Start-Sleep -Seconds 1
    }
}
if (-not $Health) {
    throw "Started pid=$($Process.Id), but health check failed at $HealthUrl after 30 attempts. $LastHealthError"
}

$PiHealth = $null
$PiStartupWarning = ""
if (-not $NoDeepSeek) {
    $PiHealthUrl = "http://127.0.0.1:" + $HealthPort + "/agents/pi/status?active=true"
    try {
        $PiHealth = Invoke-RestMethod -Uri $PiHealthUrl -TimeoutSec 30
    } catch {
        $PiStartupWarning = $_.Exception.Message
        $PassivePiHealthUrl = "http://127.0.0.1:" + $HealthPort + "/agents/pi/status?active=false"
        for ($Attempt = 1; $Attempt -le 30; $Attempt++) {
            try {
                $PiHealth = Invoke-RestMethod -Uri $PassivePiHealthUrl -TimeoutSec 3
                if ($PiHealth.ok -or $PiHealth.running) {
                    break
                }
            } catch {
                $PiHealth = $null
            }
            Start-Sleep -Seconds 1
        }
        if (-not $PiHealth) {
            throw "Service started, but Pi RPC handshake failed at $PiHealthUrl and passive status did not recover. $PiStartupWarning"
        }
    }
    if (-not ($PiHealth.ok -or $PiHealth.running)) {
        throw "Service started, but Pi RPC is not healthy: $($PiHealth.error)"
    }
}

@{
    service = $Health
    pi = $PiHealth
    pi_startup_warning = $PiStartupWarning
} | ConvertTo-Json -Depth 6
