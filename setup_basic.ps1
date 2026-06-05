$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-basic.txt

Write-Host ""
Write-Host "Basic setup complete."
Write-Host "Run .\run_latest_board_papers.ps1 to download the latest board paper for each trust."
