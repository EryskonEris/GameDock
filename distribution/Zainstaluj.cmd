@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 (
    echo.
    echo Instalacja nie zostala zakonczona. Zobacz komunikat powyzej.
    pause
    exit /b 1
)
echo.
pause
