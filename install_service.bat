@echo off
REM ============================================================
REM  QuickBooks Bridge — Windows Service Installer
REM  Run this as Administrator!
REM ============================================================

echo.
echo  QuickBooks Bridge - Service Installer
echo  ======================================
echo.

REM Check for admin
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: This script must be run as Administrator.
    echo  Right-click and select "Run as administrator".
    echo.
    pause
    exit /b 1
)

set PYTHON=C:\Users\chtacklind\git\QuickBooks Bridge\.venv\Scripts\python.exe
set SVC_MODULE=qb_bridge.service.svc
set WORK_DIR=C:\Users\chtacklind\git\QuickBooks Bridge

echo  [1/3] Installing service...
cd /d "%WORK_DIR%"
"%PYTHON%" -m %SVC_MODULE% --startup auto --username .\chtacklind --interactive install
if %errorlevel% neq 0 (
    echo.
    echo  Service install failed. Trying without --username...
    "%PYTHON%" -m %SVC_MODULE% --startup auto install
)

echo.
echo  [2/3] Configuring failure recovery (auto-restart)...
sc failure QBBridge reset= 86400 actions= restart/10000/restart/10000/restart/30000

echo.
echo  [3/3] Starting service...
net start QBBridge

echo.
echo  ============================================================
echo   Done! QuickBooks Bridge is running as a Windows Service.
echo.
echo   Service name:  QBBridge
echo   API URL:       http://localhost:8743
echo   Swagger docs:  http://localhost:8743/docs
echo.
echo   Manage with:
echo     net stop QBBridge      (stop)
echo     net start QBBridge     (start)
echo     sc delete QBBridge     (remove)
echo  ============================================================
echo.
pause
