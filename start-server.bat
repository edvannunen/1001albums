@echo off
cd /d "%~dp0"
echo Starting 1001 Albums server at http://localhost:8000
echo Press Ctrl+C to stop.
.venv\Scripts\python.exe -m uvicorn server:app --reload --port 8000
