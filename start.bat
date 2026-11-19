@echo off
setlocal
cd /d "%~dp0"
title Plant Pathology Console

rem --- find Python 3.10+ (py launcher first, then python) ---
set "PY="
py -3 --version >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    python --version >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo [ERROR] Python 3.10 or newer was not found.
    echo Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^) and run start.bat again.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 ( echo [ERROR] Could not create the virtual environment. & pause & exit /b 1 )
)

if not exist ".env" (
    copy /y ".env.example" ".env" >nul
    echo Created .env from .env.example - add your free API key there, or use the "API Keys" button in the app.
)

echo Installing / checking dependencies ^(first run takes a few minutes^)...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 ( echo [ERROR] Dependency installation failed. & pause & exit /b 1 )

echo Starting server on http://127.0.0.1:8001  ^(Ctrl+C to stop^)
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8001
pause
