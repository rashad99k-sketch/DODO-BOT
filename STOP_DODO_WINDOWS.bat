@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

echo ============================================================
echo   DODO BOT - SAFE STOP HELPER
echo ============================================================
echo.
echo If the bot is running in the foreground CMD window,
echo press Ctrl+C in that window for a graceful stop.
echo.
echo This helper will NOT kill arbitrary Python processes,
echo because doing so could terminate unrelated programs.
echo.
pause
exit /b 0
