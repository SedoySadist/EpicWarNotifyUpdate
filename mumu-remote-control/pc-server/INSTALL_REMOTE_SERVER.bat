@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==========================================
echo MuMu Remote - установка сервера в MuMuBot
echo ==========================================
echo.
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 install_into_mumubot.py
) else (
  python install_into_mumubot.py
)
echo.
pause
