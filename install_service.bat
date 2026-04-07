@echo off
echo Installing QuickBooks Bridge as a startup task...

REM Kill any existing task with this name
schtasks /Delete /TN "QBBridge" /F >nul 2>&1

REM Create a scheduled task that runs at system startup
REM /RU = run as current user, /RL HIGHEST = admin privileges
REM /SC ONSTART = run when the computer starts
schtasks /Create /TN "QBBridge" /TR "\"C:\Users\chtacklind\git\QuickBooks Bridge\.venv\Scripts\pythonw.exe\" -m uvicorn qb_bridge.main:app --host 0.0.0.0 --port 8743" /SC ONSTART /RU "%USERNAME%" /RL HIGHEST /F

if %ERRORLEVEL% EQU 0 (
    echo.
    echo Service installed! QuickBooks Bridge will start automatically on boot.
    echo.
    echo To start it now:   schtasks /Run /TN "QBBridge"
    echo To stop it:        taskkill /IM pythonw.exe /F
    echo To remove:         schtasks /Delete /TN "QBBridge" /F
) else (
    echo.
    echo Installation failed. Try running this as Administrator.
)
pause
