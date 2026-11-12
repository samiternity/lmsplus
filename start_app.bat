@echo off
title LMS+ Application
echo Starting LMS+ Application...

:: Change to the directory where this script is located
cd /d "%~dp0"

:: Check if the virtual environment exists
if not exist "venv\Scripts\activate.bat" (
    echo Error: Virtual environment not found at .venv\Scripts\activate.bat
    echo Please make sure the .venv folder exists.
    pause
    exit /b 1
)

:: Activate the virtual environment
call venv\Scripts\activate.bat

:: Run the application using explicit venv python to avoid path issues
venv\Scripts\python.exe run.py

:: Pause so the window doesn't immediately close if there's an error
pause
