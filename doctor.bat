@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -m jarvis doctor
) else (
    python -m jarvis doctor
)
pause
