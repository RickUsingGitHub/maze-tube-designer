@echo off
rem Build a standalone Windows app in dist\MazeTubeDesigner\MazeTubeDesigner.exe
cd /d "%~dp0"
tasklist /FI "IMAGENAME eq MazeTubeDesigner.exe" 2>nul | find /I "MazeTubeDesigner.exe" >nul
if not errorlevel 1 (
    echo Maze Tube Designer is running - close it first, then build again.
    exit /b 1
)
if not exist .venv\Scripts\python.exe (
    python -m venv .venv || goto :error
)
.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller || goto :error
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed ^
    --name MazeTubeDesigner ^
    --icon assets\icon.ico ^
    --add-data "assets;assets" ^
    --exclude-module tkinter ^
    --exclude-module PySide6.QtQml ^
    --exclude-module PySide6.QtQuick ^
    --exclude-module PySide6.QtWebEngineCore ^
    --exclude-module PySide6.QtMultimedia ^
    --exclude-module PySide6.Qt3DCore ^
    main.py || goto :error
echo.
echo Done: dist\MazeTubeDesigner\MazeTubeDesigner.exe
exit /b 0
:error
echo Build failed.
exit /b 1
