@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title WePeiYang Board

echo ============================================================
echo   WePeiYang Qingnianhudi Board (local)
echo ============================================================
echo.
echo   Starting... browser will open automatically.
echo   URL: http://127.0.0.1:8765/
echo.
echo   Close this window = stop the board server.
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3 and add it to PATH.
  pause
  exit /b 1
)

netstat -ano | findstr "LISTENING" | findstr ":8765 " >nul
if not errorlevel 1 (
  echo Port 8765 already listening. Opening browser only.
  start "" "http://127.0.0.1:8765/"
  echo.
  pause
  exit /b 0
)

python -m wpy serve --host 127.0.0.1 --port 8765
pause