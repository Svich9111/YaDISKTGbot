@echo off
setlocal

REM Check if virtual environment exists
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate

REM Install dependencies
pip install -r requirements.txt

REM Run healthcheck
echo Running healthcheck...
python healthcheck.py

if %errorlevel% equ 0 (
    echo Healthcheck passed. Starting bot...
    :loop
    python main.py
    echo Bot crashed. Restarting in 5 seconds...
    timeout /t 5
    goto loop
) else (
    echo Healthcheck failed. Please check logs.
    exit /b 1
)