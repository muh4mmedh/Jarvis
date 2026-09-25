@echo off
title Build J.A.R.V.I.S.
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo   Python was not found on your PATH.
    echo   Install Python 3.10+ from https://python.org and tick
    echo   "Add Python to PATH" during setup.
    echo.
    pause
    exit /b 1
)

rem Build from the project virtual environment, never the system Python.
rem PyInstaller can only bundle what the building interpreter can import, so
rem building from a Python without the dependencies produces a broken exe.
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   Creating the build environment. This takes a minute.
    echo.
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 goto failed
)

.venv\Scripts\python.exe -m pip install --quiet pyinstaller
.venv\Scripts\python.exe build.py %*
if errorlevel 1 goto failed

echo.
echo   Done. Your executable is in the dist folder.
echo.
pause
exit /b 0

:failed
echo.
echo   Build failed. See the output above.
echo.
pause
exit /b 1
