$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Virtual environment not found. Running setup_basic.ps1 first..."
    .\setup_basic.ps1
}

.\.venv\Scripts\python.exe scrape_latest_board_papers.py @args
