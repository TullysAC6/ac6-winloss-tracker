@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "AC6-WinLoss-Tracker.exe" (
  rem Public EXE has no direct manual increment path. Undo is intentionally retained.
  echo Undo is available through the source/dev control helper while the server is running.
  py -3 control.py undo
) else (
  py -3 control.py undo
)
pause
