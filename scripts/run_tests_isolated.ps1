$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$TempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$TempRoot = [IO.Path]::GetFullPath((Join-Path $TempBase ("xianyu-tests-" + [guid]::NewGuid().ToString("N"))))

if (-not $TempRoot.StartsWith($TempBase, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use test directory outside the system temp directory: $TempRoot"
}
if (-not (Test-Path $Python)) {
    throw "Python virtualenv not found: $Python"
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
