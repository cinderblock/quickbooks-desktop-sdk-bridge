"""Install QBBridge as a login-triggered scheduled task.
Run from an Admin command prompt."""

import subprocess
import sys
import os
import getpass

work_dir = os.path.dirname(os.path.abspath(__file__))
python_exe = os.path.join(work_dir, ".venv", "Scripts", "pythonw.exe")
log_file = r"C:\ProgramData\QBBridge\logs\service_stdout.log"

username = input(f"  Windows username [{os.environ.get('USERNAME', '')}]: ").strip()
if not username:
    username = os.environ.get("USERNAME", "")
password = getpass.getpass("  Windows password: ")

# Remove old service/task
subprocess.run(["schtasks", "/Delete", "/TN", "QBBridge", "/F"], capture_output=True)
# Also remove NSSM service if present
nssm = os.path.join(work_dir, "nssm.exe")
if os.path.exists(nssm):
    subprocess.run([nssm, "stop", "QBBridge"], capture_output=True)
    subprocess.run([nssm, "remove", "QBBridge", "confirm"], capture_output=True)
subprocess.run(["sc", "stop", "QBBridge"], capture_output=True)
subprocess.run(["sc", "delete", "QBBridge"], capture_output=True)

# Kill any leftover python on port 8743
import re
r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
for line in r.stdout.splitlines():
    if ":8743" in line and "LISTEN" in line:
        pid = line.strip().split()[-1]
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)

import time
time.sleep(2)

# Create scheduled task
# /SC ONLOGON + /RU with password = runs in the user's interactive session
cmd_line = f'"{python_exe}" -m uvicorn qb_bridge.main:app --host 0.0.0.0 --port 8743'

result = subprocess.run(
    ["schtasks", "/Create",
     "/TN", "QBBridge",
     "/TR", cmd_line,
     "/SC", "ONLOGON",
     "/RU", username,
     "/RP", password,
     "/RL", "HIGHEST",
     "/F"],
    capture_output=True, text=True,
)
print(result.stdout)
if result.returncode != 0:
    print(f"ERROR: {result.stderr}")
    sys.exit(1)

# Set working directory via XML update (schtasks /Create doesn't support it directly)
# Instead we'll use a wrapper approach — change the TR to include cd
subprocess.run(["schtasks", "/Delete", "/TN", "QBBridge", "/F"], capture_output=True)

# Write XML task definition for full control
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
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
  </Settings>
  <Actions>
    <Exec>
      <Command>{python_exe}</Command>
      <Arguments>-m uvicorn qb_bridge.main:app --host 0.0.0.0 --port 8743</Arguments>
      <WorkingDirectory>{work_dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""

xml_path = os.path.join(os.environ["TEMP"], "qbbridge_task.xml")
with open(xml_path, "w", encoding="utf-16") as f:
    f.write(xml)

result = subprocess.run(
    ["schtasks", "/Create", "/TN", "QBBridge", "/XML", xml_path, "/RU", username, "/RP", password, "/F"],
    capture_output=True, text=True,
)
os.remove(xml_path)
print(result.stdout)
if result.returncode != 0:
    print(f"ERROR: {result.stderr}")
    sys.exit(1)

print("Task installed! Starting now...")
result = subprocess.run(["schtasks", "/Run", "/TN", "QBBridge"], capture_output=True, text=True)
print(result.stdout)

time.sleep(5)

# Health check
import urllib.request
try:
    resp = urllib.request.urlopen("http://localhost:8743/", timeout=5)
    print(f"SUCCESS! API is responding (HTTP {resp.status})")
except Exception:
    print("Task started. API may need a few more seconds.")

print()
print("=" * 60)
print("  QBBridge installed as a scheduled task")
print("  Runs in your login session on every login")
print("  Auto-restarts on failure (up to 3 times)")
print()
print("  API:   http://localhost:8743")
print("  Docs:  http://localhost:8743/docs")
print()
print("  Manage:")
print('    schtasks /Run /TN "QBBridge"       (start)')
print('    schtasks /End /TN "QBBridge"       (stop)')
print('    schtasks /Delete /TN "QBBridge" /F (remove)')
print("=" * 60)
