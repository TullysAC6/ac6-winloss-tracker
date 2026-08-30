@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Source/development run only: run setup.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" app.py
pause
