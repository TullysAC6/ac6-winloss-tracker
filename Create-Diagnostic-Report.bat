@echo off
cd /d "%~dp0"
if exist "AC6-WinLoss-Tracker.exe" (
  "AC6-WinLoss-Tracker.exe" --diagnostics
  exit /b %errorlevel%
)
py -3 app.py --diagnostics
