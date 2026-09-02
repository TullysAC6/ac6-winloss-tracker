@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

set "PY="
py -3 -c "import runtime_policy; runtime_policy.require_supported_runtime()" >nul 2>&1
if not errorlevel 1 set "PY=py -3"

if not defined PY (
  python -c "import runtime_policy; runtime_policy.require_supported_runtime()" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)

if not defined PY (
  echo [ERROR] Stable v1.0.0 supported Python runtime was not found.
  echo Run install.ps1 to select or prepare an approved runtime.
  pause
  exit /b 1
)

if not exist "config.json" (
  if not exist "config.example.json" (
    echo [ERROR] config.example.json not found.
    pause
    exit /b 1
  )
  copy /y "config.example.json" "config.json" >nul
  echo Created config.json from config.example.json.
)

%PY% -m venv .venv
if errorlevel 1 goto error

".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto error

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto error

echo Setup completed successfully.
pause
exit /b 0

:error
echo Setup failed.
pause
exit /b 1
