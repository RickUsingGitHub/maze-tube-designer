@echo off
rem Run Maze Tube Designer from source (creates the virtual environment on first run)
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
    echo First run: setting up Python environment...
    python -m venv .venv || goto :error
    .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :error
)
start "" .venv\Scripts\pythonw.exe main.py %*
exit /b 0
:error
echo Setup failed - make sure Python 3.10+ is installed and on PATH.
pause
exit /b 1
