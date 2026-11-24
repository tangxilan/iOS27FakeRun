@echo off
cd /d "%~dp0"
set "PY_CMD=python"
where python >nul 2>nul
if errorlevel 1 (
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
        set "PY_CMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    ) else (
        echo [ERROR] Python 3.10-3.12 not found. Please install Python and check Add to PATH.
        pause
        exit /b 1
    )
)
if not exist ".venv\Scripts\python.exe" (
    echo [1/2] Creating .venv virtual environment...
    "%PY_CMD%" -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create .venv.
        pause
        exit /b 1
    )
)
echo [2/2] Installing dependencies from requirements.txt...
".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
if errorlevel 1 (
    echo [WARN] Mirror failed, retrying with default PyPI...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed.
        pause
        exit /b 1
    )
)
echo [OK] Setup complete! You can now launch the desktop app.
pause
