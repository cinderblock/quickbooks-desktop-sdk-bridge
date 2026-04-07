@echo off
REM ============================================================
REM  QuickBooks Bridge - Windows Service Installer
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

set WORK_DIR=C:\Users\chtacklind\git\QuickBooks Bridge
set PYTHON=%WORK_DIR%\.venv\Scripts\python.exe

echo  [1/4] Removing old service if exists...
sc stop QBBridge >nul 2>&1
sc delete QBBridge >nul 2>&1
timeout /t 2 /nobreak >nul

echo  [2/4] Installing service via NSSM pattern...
REM sc.exe can't run Python directly, so we use a wrapper approach:
REM Create the service pointing to cmd.exe which runs our Python
sc create QBBridge binPath= "cmd.exe /c cd /d \"%WORK_DIR%\" && \"%PYTHON%\" -m uvicorn qb_bridge.main:app --host 0.0.0.0 --port 8743" start= auto DisplayName= "QuickBooks Bridge API"

if %errorlevel% neq 0 (
    echo.
    echo  ERROR: Service creation failed.
    pause
    exit /b 1
)

REM Set description
sc description QBBridge "REST API bridge to QuickBooks Desktop - CRUD endpoints, report generation, real-time status"

echo  [3/4] Configuring failure recovery (auto-restart)...
sc failure QBBridge reset= 86400 actions= restart/10000/restart/10000/restart/30000

echo.
echo  [4/4] Starting service...
net start QBBridge

echo.
echo  ============================================================
echo   Service installed: QBBridge
echo.
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
