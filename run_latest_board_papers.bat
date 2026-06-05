@echo off
setlocal

if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Running setup_basic.ps1 first...
  powershell -ExecutionPolicy Bypass -File setup_basic.ps1
)

".venv\Scripts\python.exe" scrape_latest_board_papers.py %*
