$ErrorActionPreference = 'Stop'
$Root = (Get-Item -LiteralPath $PSScriptRoot).Parent.Parent.FullName
Set-Location -LiteralPath $Root
$Before = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'baseline.json') -Raw | ConvertFrom-Json
$Sources = @(
    foreach ($Folder in @('xianyu_agent','tests','scripts','web','.pi')) {
        Get-ChildItem -LiteralPath $Folder -Recurse -File -Force |
            Where-Object { $_.Extension -in @('.py','.ps1','.js','.ts','.css','.html','.json') -and $_.FullName -notmatch '__pycache__|node_modules' } |
            ForEach-Object { [pscustomobject]@{
                path = $_.FullName.Substring($Root.Length + 1).Replace('\','/')
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            } }
    }
) | Sort-Object path
$Live = @(Get-ChildItem -LiteralPath 'data\live' -Recurse -File -Force |
    ForEach-Object { [pscustomobject]@{
        path = $_.FullName.Substring($Root.Length + 1).Replace('\','/')
        sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    } }) | Sort-Object path
$SourceDelta = @(Compare-Object ($Before.sources | ConvertTo-Json -Compress) ($Sources | ConvertTo-Json -Compress))
$LiveDelta = @(Compare-Object ($Before.live | ConvertTo-Json -Compress) ($Live | ConvertTo-Json -Compress))
$ProbeDelta = @($Before.probes | Where-Object {
    $_.sha256 -ne (Get-FileHash -LiteralPath $_.path -Algorithm SHA256).Hash.ToLowerInvariant()
})
$RunNames = @(
    'run-05b9ca4122e14bb780fd7cb752827aa4',
    'run-c34f078035c14b0b895d61785fa70bf9',
    'run-0aba2e13b6e640be8c06e42f9a4d4c3c',
    'run-99c127731eeb4cc9bf79847b5d52b03c',
    'run-f0324914d1aa46498e0ebade67db8df1',
    'run-85d418837a6a41579256fd3effa16f34',
    'run-4598760ebcf049fb82f6aa2277c0628a',
    'run-c634f09eba4b4f0889b0b79fc7f8c160'
)
$Runs = @($RunNames | ForEach-Object {
    $Path = Join-Path $Root ('research\codex-verification-2026-10-12\' + $_ + '\result.json')
    [pscustomobject]@{ id = $_; result = (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json) }
})
$Artifacts = @(
    Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -File -Force |
        Where-Object { $_.Name -ne 'recheck-manifest.json' -and $_.FullName -notmatch '__pycache__' }
    foreach ($Name in $RunNames) {
        Get-ChildItem -LiteralPath (Join-Path $Root ('research\codex-verification-2026-10-12\' + $Name)) -File
    }
    Get-Item -LiteralPath 'docs\acceptance\CODEX_RECHECK_2026-10-13.md'
) | ForEach-Object { [pscustomobject]@{
    path = $_.FullName.Substring($Root.Length + 1).Replace('\','/')
    sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
} } | Sort-Object path
[pscustomobject]@{
    schema_version = 1
    client_date = '2026-10-13'
    head = (git rev-parse HEAD)
    source_count_before = $Before.sources.Count
    source_count_after = $Sources.Count
    source_unchanged = ($SourceDelta.Count -eq 0)
    live_count_before = $Before.live.Count
    live_count_after = $Live.Count
    live_unchanged = ($LiveDelta.Count -eq 0)
    original_probe_scripts_unchanged = ($ProbeDelta.Count -eq 0)
    prior_live_differences = $Before.prior_live_differences
    runs = $Runs
    artifacts = $Artifacts
    source_after = $Sources
    live_after = $Live
} | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'recheck-manifest.json') -Encoding UTF8
Write-Output ('SOURCE_UNCHANGED=' + ($SourceDelta.Count -eq 0))
Write-Output ('LIVE_UNCHANGED=' + ($LiveDelta.Count -eq 0))
Write-Output ('ORIGINAL_PROBES_UNCHANGED=' + ($ProbeDelta.Count -eq 0))
if ($SourceDelta.Count -gt 0 -or $LiveDelta.Count -gt 0 -or $ProbeDelta.Count -gt 0) { exit 2 }
exit 0
