@echo off
REM QuickBooks Bridge API — service runner
REM Called by Windows Service Manager (sc.exe / NSSM)
cd /d "C:\Users\chtacklind\git\QuickBooks Bridge"
.venv\Scripts\python.exe -m uvicorn qb_bridge.main:app --host 0.0.0.0 --port 8743
