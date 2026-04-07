"""Install QBBridge as a login-triggered scheduled task.
Run from an Admin command prompt:
    .venv\Scripts\python.exe install_task.py
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
import time

work_dir = os.path.dirname(os.path.abspath(__file__))
pythonw_exe = os.path.join(work_dir, ".venv", "Scripts", "pythonw.exe")
launcher = os.path.join(work_dir, "start_server.py")

print()
print("  QuickBooks Bridge - Task Scheduler Installer")
print("  =============================================")
print()

username = input(f"  Windows username [{os.environ.get('USERNAME', '')}]: ").strip()
if not username:
    username = os.environ.get("USERNAME", "")
password = getpass.getpass("  Windows password: ")

# --- Cleanup old installations ---
print()
print("  [1/3] Cleaning up old service/task...")
subprocess.run(["schtasks", "/End", "/TN", "QBBridge"], capture_output=True)
subprocess.run(["schtasks", "/Delete", "/TN", "QBBridge", "/F"], capture_output=True)

nssm = os.path.join(work_dir, "nssm.exe")
if os.path.exists(nssm):
    subprocess.run([nssm, "stop", "QBBridge"], capture_output=True)
    subprocess.run([nssm, "remove", "QBBridge", "confirm"], capture_output=True)
subprocess.run(["sc", "stop", "QBBridge"], capture_output=True)
subprocess.run(["sc", "delete", "QBBridge"], capture_output=True)

# Kill anything on port 8743
r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
for line in r.stdout.splitlines():
    if ":8743" in line and "LISTEN" in line:
        pid = line.strip().split()[-1]
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)

time.sleep(2)

# --- Create scheduled task via XML ---
print("  [2/3] Creating scheduled task...")

os.makedirs(r"C:\ProgramData\QBBridge\logs", exist_ok=True)

xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{username}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal>
      <UserId>{username}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
  </Settings>
  <Actions>
    <Exec>
      <Command>{pythonw_exe}</Command>
      <Arguments>{launcher}</Arguments>
      <WorkingDirectory>{work_dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""

xml_path = os.path.join(os.environ["TEMP"], "qbbridge_task.xml")
with open(xml_path, "w", encoding="utf-16") as f:
    f.write(xml)

result = subprocess.run(
    ["schtasks", "/Create", "/TN", "QBBridge", "/XML", xml_path,
     "/RU", username, "/RP", password, "/F"],
    capture_output=True, text=True,
)
os.remove(xml_path)

if result.returncode != 0:
    print(f"  ERROR: {result.stderr}")
    sys.exit(1)
print("  Task created.")

# --- Start it ---
print("  [3/3] Starting...")
subprocess.run(["schtasks", "/Run", "/TN", "QBBridge"], capture_output=True)

time.sleep(5)

import urllib.request
try:
    resp = urllib.request.urlopen("http://localhost:8743/", timeout=5)
    print(f"  SUCCESS! API is responding (HTTP {resp.status})")
except Exception:
    print("  Task started. Waiting a few more seconds...")
    time.sleep(5)
    try:
        resp = urllib.request.urlopen("http://localhost:8743/", timeout=5)
        print(f"  SUCCESS! API is responding (HTTP {resp.status})")
    except Exception:
        print("  API not responding yet. Check logs at C:\\ProgramData\\QBBridge\\logs\\")

print()
print("  " + "=" * 56)
print("  QBBridge installed (Task Scheduler)")
print("  Runs in your login session — auto-starts on login")
print("  Auto-restarts on failure")
print()
print("  API:   http://localhost:8743")
print("  Docs:  http://localhost:8743/docs")
print()
print("  Manage:")
print('    schtasks /Run /TN "QBBridge"       (start)')
print('    schtasks /End /TN "QBBridge"       (stop)')
print('    schtasks /Delete /TN "QBBridge" /F (remove)')
print("  " + "=" * 56)
