"""Standalone QB COM worker process.

This script runs as a subprocess, accepting qbXML requests on stdin
and returning responses on stdout. Each line is a JSON message.

Protocol:
  -> {"cmd": "execute", "qbxml": "..."}
  <- {"status": "ok", "response": "..."}
  <- {"status": "error", "message": "..."}

  -> {"cmd": "quit"}
  <- (process exits)

This runs in a separate process to avoid COM apartment issues with
uvicorn's event loop / thread pool.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sys

log = logging.getLogger(__name__)


def main() -> None:
    import pythoncom
    import win32com.client

    # Read config from argv
    company_file = sys.argv[1] if len(sys.argv) > 1 else ""

    pythoncom.CoInitialize()

    rp = None
    ticket = None

    def connect():
        nonlocal rp, ticket
        rp = win32com.client.Dispatch("QBXMLRP2.RequestProcessor")
        rp.OpenConnection2("QBBridge", "QuickBooks Bridge API", 1)
        ticket = rp.BeginSession(company_file, 2)

    def disconnect():
        nonlocal rp, ticket
        if rp and ticket:
            with contextlib.suppress(Exception):
                rp.EndSession(ticket)
            with contextlib.suppress(Exception):
                rp.CloseConnection()
        rp = None
        ticket = None

    # Signal ready
    sys.stdout.write(json.dumps({"status": "ready"}) + "\n")
    sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _respond({"status": "error", "message": "Invalid JSON"})
            continue

        cmd = msg.get("cmd", "")

        if cmd == "quit":
            disconnect()
            break

        if cmd == "execute":
            qbxml = msg.get("qbxml", "")
            try:
                if not rp or not ticket:
                    connect()

                response = rp.ProcessRequest(ticket, qbxml)
                _respond({"status": "ok", "response": response})
            except Exception as exc:
                disconnect()
                _respond({"status": "error", "message": str(exc)})

        elif cmd == "ping":
            _respond({"status": "ok", "connected": rp is not None})

        elif cmd == "disconnect":
            disconnect()
            _respond({"status": "ok"})

        else:
            _respond({"status": "error", "message": f"Unknown command: {cmd}"})

    pythoncom.CoUninitialize()


def _respond(data: dict) -> None:
    sys.stdout.write(json.dumps(data) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
