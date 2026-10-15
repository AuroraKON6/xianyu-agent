param(
    [ValidateSet('full','suite','adversarial','audit')][string]$Mode = 'full',
    [ValidatePattern('^test_[A-Za-z0-9_]+$')][string]$Suite = 'test_pi_outbound_gate',
    [ValidateSet('adversarial_repros','radar_repros','finance_repros','storage_repros')][string]$Probe = 'adversarial_repros',
    [ValidatePattern('^(all|[A-Za-z][A-Za-z0-9_]*)$')][string]$Case = 'all'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Get-Item -LiteralPath $PSScriptRoot).Parent.Parent.FullName
$PythonPath = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$ResultRoot = Join-Path $PSScriptRoot ('run-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $ResultRoot | Out-Null
Set-Location -LiteralPath $RepoRoot
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:SHADOW_OBSERVER_ENABLED = '0'
$env:RADAR_DISPATCHER_ENABLED = '0'
$env:AUTO_REPLY_ENABLED = '0'
$env:MARKET_CHANNEL_PROBE_AUTHORIZED = '0'
$env:DEEPSEEK_API_KEY = ''
$env:MIMO_API_KEY = ''

function Get-ReviewState {
    $SourceEntries = @()
    foreach ($FolderName in @('xianyu_agent','tests','scripts','web','.pi')) {
        $FolderPath = Join-Path $RepoRoot $FolderName
        if (-not (Test-Path -LiteralPath $FolderPath)) { continue }
        $SourceEntries += Get-ChildItem -LiteralPath $FolderPath -Recurse -File |
            Where-Object { $_.Extension -in @('.py','.ps1','.js','.ts','.css','.html','.json') -and $_.FullName -notmatch '__pycache__|node_modules' } |
            ForEach-Object { [pscustomobject]@{
                path = $_.FullName.Substring($RepoRoot.Length + 1).Replace('\','/')
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            } }
    }
    $LiveEntries = @(Get-ChildItem -LiteralPath (Join-Path $RepoRoot 'data\live') -Recurse -File |
        ForEach-Object { [pscustomobject]@{
            path = $_.FullName.Substring($RepoRoot.Length + 1).Replace('\','/')
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        } })
    return [pscustomobject]@{
        sources = @($SourceEntries | Sort-Object path)
        live = @($LiveEntries | Sort-Object path)
    }
}

$BeforeState = Get-ReviewState
$BeforeState | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $ResultRoot 'state-before.json') -Encoding UTF8
$OldDataDir = $env:DATA_DIR
$OldDbPath = $env:DB_PATH
$OldAdapter = $env:XIANYU_ADAPTER
$RunExit = 1
try {
    $env:DATA_DIR = Join-Path $ResultRoot 'isolated-data'
    $env:DB_PATH = Join-Path $ResultRoot 'isolated.sqlite'
    $env:XIANYU_ADAPTER = 'stub'
    $ErrorActionPreference = 'Continue'
    $LogPath = Join-Path $ResultRoot 'output.log'
    switch ($Mode) {
        'full' { & powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_tests_isolated.ps1 2>&1 | Tee-Object -FilePath $LogPath }
        'suite' { & $PythonPath -m unittest discover -s tests -p ($Suite + '.py') -v 2>&1 | Tee-Object -FilePath $LogPath }
        'adversarial' { & $PythonPath (Join-Path $PSScriptRoot ($Probe + '.py')) --case $Case 2>&1 | Tee-Object -FilePath $LogPath }
        'audit' { & $PythonPath scripts/audit_commerce_ledgers.py --data-dir data/live 2>&1 | Tee-Object -FilePath $LogPath }
    }
    $RunExit = $LASTEXITCODE
} finally {
    $env:DATA_DIR = $OldDataDir
    $env:DB_PATH = $OldDbPath
    $env:XIANYU_ADAPTER = $OldAdapter
    $ErrorActionPreference = 'Stop'
    $AfterState = Get-ReviewState
    $AfterState | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $ResultRoot 'state-after.json') -Encoding UTF8
    $SourceDelta = @(Compare-Object ($BeforeState.sources | ConvertTo-Json -Compress) ($AfterState.sources | ConvertTo-Json -Compress))
    $LiveDelta = @(Compare-Object ($BeforeState.live | ConvertTo-Json -Compress) ($AfterState.live | ConvertTo-Json -Compress))
    $CommandExit = $RunExit
    if ($SourceDelta.Count -gt 0 -or $LiveDelta.Count -gt 0) { $RunExit = 2 }
    [pscustomobject]@{
        mode = $Mode
        suite = $Suite
        probe = $Probe
        case = $Case
        exit_code = $RunExit
        command_exit_code = $CommandExit
        source_count = $BeforeState.sources.Count
        source_unchanged = ($SourceDelta.Count -eq 0)
        live_file_count = $BeforeState.live.Count
        live_unchanged = ($LiveDelta.Count -eq 0)
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ResultRoot 'result.json') -Encoding UTF8
    Write-Output ('EVIDENCE_DIR=' + $ResultRoot)
    Write-Output ('EXIT_CODE=' + $RunExit)
    if ($SourceDelta.Count -gt 0 -or $LiveDelta.Count -gt 0) {
        Write-Error 'Monitored sources or live data changed; invalidate this run.' -ErrorAction Continue
        $RunExit = 2
    }
}
exit $RunExit
