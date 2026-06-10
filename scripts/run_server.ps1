$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path ".venv\Scripts\python.exe")) {
  python -m venv .venv
}
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m xianyu_agent.cli server
