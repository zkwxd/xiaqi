@echo off
setlocal
cd /d "%~dp0"
if not exist "demo\launch.py" cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" goto ready
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -m venv .venv
) else (
  python -m venv .venv
)
if errorlevel 1 goto failed
:ready
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, httpx" >nul 2>nul
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" demo\launch.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo Demo could not start. Install Python 3.11 or newer, then try again.
pause
exit /b 1
