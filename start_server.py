"""Launcher script for Task Scheduler.

Runs uvicorn with stdout/stderr redirected to log files so it
works headless without a console window.

Exits 0 on clean shutdown (reboot, manual stop) so the Task Scheduler's
RestartOnFailure counter resets. Only actual crashes produce non-zero exits.
"""

import os
import sys

# Redirect stdout/stderr FIRST — before any imports that could fail — so
# errors are captured instead of lost in the hidden console.
log_dir = r"C:\ProgramData\QBBridge\logs"
os.makedirs(log_dir, exist_ok=True)
sys.stdout = open(os.path.join(log_dir, "task_stdout.log"), "a", encoding="utf-8")
sys.stderr = open(os.path.join(log_dir, "task_stderr.log"), "a", encoding="utf-8")

os.chdir(os.path.dirname(os.path.abspath(__file__)))

import asyncio
import signal
import socket
import sqlite3
import uvicorn

from qb_bridge.main import app

def _stored_port(default=8743):
    """Port saved by the GUI's Connection page, if it is usable.

    The socket is bound before the app (and its database connection) exist, so
    this is read directly. A bad value falls back to the default loudly rather
    than leaving the service unreachable with no explanation.
    """
    db_path = os.path.join(r"C:\ProgramData\QBBridge", "qbbridge.db")
    if not os.path.exists(db_path):
        return default
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute("SELECT value FROM settings WHERE key = 'listen_port'").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        print(f"Could not read listen_port from {db_path}: {exc}; using {default}", flush=True)
        return default

    if not row or not row[0]:
        return default
    try:
        port = int(row[0])
    except (TypeError, ValueError):
        print(f"Stored listen_port {row[0]!r} is not a number; using {default}", flush=True)
        return default
    if not 1 <= port <= 65535:
        print(f"Stored listen_port {port} is out of range; using {default}", flush=True)
        return default
    return port


# Create a dual-stack socket so we accept both IPv4 and IPv6 on one port.
# On Windows, IPV6_V6ONLY defaults to 1 (IPv6 only), so we must explicitly
# disable it to also receive IPv4 connections.
#
# The bind address is always "::" (all interfaces): the stored listen_host is
# not applied here, and the IP filter — not the bind address — is what limits
# who can reach the API.
port = _stored_port()
sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("::", port))
sock.listen(128)
print(f"Listening on [::]:{port}", flush=True)

# On Windows reboot/logoff, the process receives CTRL_CLOSE_EVENT (mapped
# to SIGBREAK). Catch it so we exit 0, resetting the Task Scheduler's
# RestartOnFailure counter.
def _clean_exit(signum, frame):
    raise SystemExit(0)

signal.signal(signal.SIGBREAK, _clean_exit)

try:
    config = uvicorn.Config(app, log_level="info")
    server = uvicorn.Server(config)
    asyncio.run(server.serve(sockets=[sock]))
except (KeyboardInterrupt, SystemExit):
    sys.exit(0)
