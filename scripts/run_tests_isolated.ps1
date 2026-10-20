# Isolated test entry (taskbook R01).
#
# Default: delegate to scripts/run_offline_checks.py, which scrubs
# credential env vars, refuses live data paths before any business
# import, blocks non-loopback network and unvetted subprocesses, and
# verifies the live data/ tree is byte-identical afterwards.
#
# -Legacy: the pre-R01 behaviour (env-var redirection only; the
# repository .env is still loaded via setdefault, so credentials enter
# the test process). Kept for rollback/diagnosis only - results from
# the legacy entry are NOT valid for new acceptance evidence.
param(
    [switch]$Legacy,
    [switch]$KeepSandbox
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Python virtualenv not found: $Python"
}

if (-not $Legacy) {
    if ($KeepSandbox) {
        & $Python (Join-Path $Root "scripts\run_offline_checks.py") --keep-sandbox
    } else {
        & $Python (Join-Path $Root "scripts\run_offline_checks.py")
    }
    exit $LASTEXITCODE
}

# --- legacy entry (not valid for new acceptance) -----------------------
$TempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$TempRoot = [IO.Path]::GetFullPath((Join-Path $TempBase ("xianyu-tests-" + [guid]::NewGuid().ToString("N"))))

if (-not $TempRoot.StartsWith($TempBase, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use test directory outside the system temp directory: $TempRoot"
}

$OriginalDataDir = $env:DATA_DIR
$OriginalDbPath = $env:DB_PATH
$OriginalAdapter = $env:XIANYU_ADAPTER

New-Item -ItemType Directory -Path $TempRoot | Out-Null
try {
    $env:DATA_DIR = Join-Path $TempRoot "data"
    $env:DB_PATH = Join-Path $TempRoot "xianyu-tests.db"
    $env:XIANYU_ADAPTER = "stub"

    & $Python -m unittest discover -s tests
    $TestExitCode = $LASTEXITCODE
} finally {
    $env:DATA_DIR = $OriginalDataDir
    $env:DB_PATH = $OriginalDbPath
    $env:XIANYU_ADAPTER = $OriginalAdapter

    $ResolvedTempRoot = [IO.Path]::GetFullPath($TempRoot)
    if ($ResolvedTempRoot.StartsWith($TempBase, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path $ResolvedTempRoot)) {
        Remove-Item -LiteralPath $ResolvedTempRoot -Recurse -Force
    }
}

exit $TestExitCode
