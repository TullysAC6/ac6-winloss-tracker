@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

where py >nul 2>&1 || (
  echo [ERROR] Python 3 is required only to BUILD the release.
  exit /b 1
)

py -3 -m pip install --upgrade pyinstaller mss==10.2.0 || exit /b 1
py -3 -m PyInstaller --noconfirm --clean --onefile --noconsole ^
  --name "AC6-WinLoss-Tracker" ^
  --add-data "overlay.html;." ^
  --add-data "detector_templates.json;." ^
  app.py || exit /b 1

echo.
echo Built: dist\AC6-WinLoss-Tracker.exe
endlocal
