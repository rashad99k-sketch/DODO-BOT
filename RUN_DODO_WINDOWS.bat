@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"

set "BOT_FILE=test cv new.py"
set "VENV_DIR=.venv"
set "PY=%VENV_DIR%\Scripts\python.exe"


echo ============================================================
echo   DODO BOT - WINDOWS LAUNCHER
echo   BingX Futures / Flask / Trade Forensics
echo ============================================================
echo.

if not exist "%BOT_FILE%" (
    echo [ERROR] Bot file not found: "%BOT_FILE%"
    echo [ERROR] Make sure this BAT file is inside the DODO project folder.
    pause
    exit /b 1
)

where py >nul 2>&1
if %errorlevel%==0 (
    set "PY_CMD=py -3"
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python was not found.
        echo Install Python 3.11/3.12 and enable ^"Add Python to PATH^".
        pause
        exit /b 1
    )
    set "PY_CMD=python"
)

if not exist "%PY%" (
    echo [SETUP] Creating virtual environment...
    %PY_CMD% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

if not exist "%PY%" (
    echo [ERROR] Virtual environment Python was not created.
    pause
    exit /b 1
)

echo [CHECK] Python:
"%PY%" --version
if errorlevel 1 goto :python_error

echo.
echo [SETUP] Upgrading pip tooling...
"%PY%" -m pip install --disable-pip-version-check --upgrade pip setuptools wheel
if errorlevel 1 (
    echo [WARN] pip tooling upgrade failed. Continuing with existing environment...
)

echo.
echo [SETUP] Installing / verifying dependencies...
"%PY%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency installation failed.
    echo Check your internet connection or Python environment.
    pause
    exit /b 1
)

if not exist ".env" (
    if exist "env.example" (
        echo.
        echo [SETUP] Creating .env from env.example...
        copy /Y "env.example" ".env" >nul
        echo [NOTICE] .env created. Default PAPER mode is preserved.
        echo [NOTICE] Do NOT add LIVE credentials unless you intentionally want LIVE trading.
    ) else (
        echo [WARN] env.example not found. Continuing with process environment.
    )
) else (
    echo [CHECK] Existing .env found. It will NOT be overwritten.
)

rem Safety: PAPER mode is explicit. The bot will not place live orders in this mode.
set "PAPER_MODE=True"

if not exist "trade_forensics" mkdir "trade_forensics"

 echo.
echo [CHECK] Configuration safety...
echo   PAPER_MODE=True (forced by this launcher)
echo   LIVE orders: DISABLED
echo.
echo [CHECK] Compiling bot source...
"%PY%" -m py_compile "%BOT_FILE%" trade_forensics.py
if errorlevel 1 (
    echo [ERROR] Python source compilation failed.
    pause
    exit /b 1
)

 echo.
echo ============================================================
echo   STARTING DODO BOT
echo   Mode: PAPER
echo   Dashboard: http://127.0.0.1:5000
echo   Forensics: .\trade_forensics\
echo ============================================================
echo.
echo [INFO] Press Ctrl+C to stop the bot.
echo.

"%PY%" "%BOT_FILE%"
set "RC=%errorlevel%"

echo.
echo ============================================================
echo   DODO BOT STOPPED - EXIT CODE %RC%
echo ============================================================
if not "%RC%"=="0" (
    echo [ERROR] Bot exited with an error.
) else (
    echo [INFO] Bot exited normally.
)
pause
exit /b %RC%

:python_error
echo [ERROR] Python environment is not working correctly.
pause
exit /b 1
