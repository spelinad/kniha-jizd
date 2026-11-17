@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Vytvarim virtualni prostredi Python 3.13...
    py -3.13 -m venv .venv
    if errorlevel 1 (
        echo Chyba: Python 3.13 nebyl nalezen pres "py -3.13".
        pause
        exit /b 1
    )
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
)

echo Spoustim Knihu jizd na http://127.0.0.1:8000 ...
start "" http://127.0.0.1:8000
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
