"""Launcher script for Task Scheduler.

Runs uvicorn with stdout/stderr redirected to log files so it
works headless without a console window.
"""

import os
import sys

# Ensure we're in the project directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Redirect stdout/stderr to log files
log_dir = r"C:\ProgramData\QBBridge\logs"
os.makedirs(log_dir, exist_ok=True)

sys.stdout = open(os.path.join(log_dir, "task_stdout.log"), "a", encoding="utf-8")
sys.stderr = open(os.path.join(log_dir, "task_stderr.log"), "a", encoding="utf-8")

import uvicorn

from qb_bridge.main import app

uvicorn.run(app, host="::", port=8743, log_level="info")
