@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

if not exist "config.json" (
  if not exist "config.example.json" (
    echo [ERROR] config.example.json not found.
    pause
    exit /b 1
  )
  copy /y "config.example.json" "config.json" >nul
  echo Created config.json from config.example.json.
)

set "PY="
py -3 -c "import sys; assert sys.version_info >= (3,10); print(sys.executable)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"

if not defined PY (
  python -c "import sys; assert sys.version_info >= (3,10); print(sys.executable)" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)

if not defined PY (
  echo [ERROR] Python 3.10 or later was not found.
  echo Microsoft Store App Execution Alias alone is not sufficient.
  pause
  exit /b 1
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
