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

import asyncio
import socket
import uvicorn

from qb_bridge.main import app

# Create a dual-stack socket so we accept both IPv4 and IPv6 on one port.
# On Windows, IPV6_V6ONLY defaults to 1 (IPv6 only), so we must explicitly
# disable it to also receive IPv4 connections.
sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("::", 8743))
sock.listen(128)

config = uvicorn.Config(app, log_level="info")
server = uvicorn.Server(config)
asyncio.run(server.serve(sockets=[sock]))
