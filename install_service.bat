@echo off
REM ============================================================
REM  QuickBooks Bridge - Windows Service Installer (using NSSM)
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
set NSSM=%WORK_DIR%\nssm.exe
set PYTHON=%WORK_DIR%\.venv\Scripts\python.exe

REM Get username and password for the service account
echo  The service needs to run under your Windows user account
echo  (required for QuickBooks file access).
echo.
set /p SVC_USER=  Windows username [%USERNAME%]:
if "%SVC_USER%"=="" set SVC_USER=%USERNAME%

REM Add .\ prefix if not already qualified
echo %SVC_USER% | findstr /C:"\" >nul 2>&1
if %errorlevel% neq 0 (
    echo %SVC_USER% | findstr /C:"@" >nul 2>&1
    if %errorlevel% neq 0 (
        set SVC_USER=.\%SVC_USER%
    )
)

echo.
set /p SVC_PASS=  Windows password:
echo.

echo  [1/6] Stopping and removing old service...
"%NSSM%" stop QBBridge >nul 2>&1
"%NSSM%" remove QBBridge confirm >nul 2>&1
sc stop QBBridge >nul 2>&1
sc delete QBBridge >nul 2>&1
timeout /t 2 /nobreak >nul

echo  [2/6] Killing any leftover processes on port 8743...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8743.*LISTEN"') do taskkill /PID %%a /F >nul 2>&1
timeout /t 2 /nobreak >nul

echo  [3/6] Installing service with NSSM...
"%NSSM%" install QBBridge "%PYTHON%" -m uvicorn qb_bridge.main:app --host 0.0.0.0 --port 8743
"%NSSM%" set QBBridge AppDirectory "%WORK_DIR%"
"%NSSM%" set QBBridge DisplayName "QuickBooks Bridge API"
"%NSSM%" set QBBridge Description "REST API bridge to QuickBooks Desktop"
"%NSSM%" set QBBridge Start SERVICE_AUTO_START
"%NSSM%" set QBBridge AppStdout C:\ProgramData\QBBridge\logs\service_stdout.log
"%NSSM%" set QBBridge AppStderr C:\ProgramData\QBBridge\logs\service_stderr.log
"%NSSM%" set QBBridge AppStdoutCreationDisposition 4
"%NSSM%" set QBBridge AppStderrCreationDisposition 4
"%NSSM%" set QBBridge AppRotateFiles 1
"%NSSM%" set QBBridge AppRotateBytes 10485760
"%NSSM%" set QBBridge AppRestartDelay 5000

echo.
echo  [4/6] Setting service to run as %SVC_USER%...
"%NSSM%" set QBBridge ObjectName %SVC_USER% %SVC_PASS%
if %errorlevel% neq 0 (
    echo.
    echo  ERROR: Failed to set service account. Check username/password.
    "%NSSM%" remove QBBridge confirm >nul 2>&1
    pause
    exit /b 1
)

echo.
echo  [5/6] Configuring failure recovery (auto-restart)...
sc failure QBBridge reset= 86400 actions= restart/10000/restart/10000/restart/30000

echo.
echo  [6/6] Starting service...
net start QBBridge

timeout /t 5 /nobreak >nul

echo.
REM Quick health check
curl -s http://localhost:8743/ >nul 2>&1
if %errorlevel% equ 0 (
    echo  SUCCESS! API is responding.
) else (
    echo  Service started. API may need a few seconds to be ready.
)

echo.
echo  ============================================================
echo   Service installed: QBBridge  (via NSSM)
echo   Running as: %SVC_USER%
echo.
echo   API URL:       http://localhost:8743
echo   Swagger docs:  http://localhost:8743/docs
echo.
echo   Manage with:
echo     net stop QBBridge      (stop)
echo     net start QBBridge     (start)
echo     nssm remove QBBridge   (remove)
echo.
echo   Logs at: C:\ProgramData\QBBridge\logs\
echo  ============================================================
echo.
pause
