@echo off
title J.A.R.V.I.S.
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo   Python was not found on your PATH.
    echo   Install Python 3.10 or newer from https://python.org and tick
    echo   "Add Python to PATH" during setup, then run this again.
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   First run - setting up. This takes a minute.
    echo.
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo   Dependency installation failed. See the errors above.
        pause
        exit /b 1
    )
)

.venv\Scripts\python.exe -m jarvis
