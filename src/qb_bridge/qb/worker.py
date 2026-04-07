"""Standalone QB COM worker process.

This script runs as a subprocess, accepting qbXML requests on stdin
and returning responses on stdout. Each line is a JSON message.

Protocol:
  -> {"cmd": "execute", "qbxml": "..."}
  <- {"status": "ok", "response": "..."}
  <- {"status": "error", "message": "..."}

  -> {"cmd": "ping"}
  <- {"status": "ok", "connected": true|false}

  -> {"cmd": "disconnect"}
  <- {"status": "ok"}

  -> {"cmd": "quit"}
  <- (process exits)

This runs in a separate process to avoid COM apartment issues with
uvicorn's event loop / thread pool.
"""

from __future__ import annotations

import json
import logging
import sys

log = logging.getLogger(__name__)


def main() -> None:
    import pythoncom

    # QBConnection must be imported after CoInitialize so it inherits STA context.
    # We import inside main() so that worker.py can be safely imported in tests
    # on machines without pywin32.
    from qb_bridge.qb.connection import QBConnection

    company_file = sys.argv[1] if len(sys.argv) > 1 else ""

    pythoncom.CoInitialize()

    conn = QBConnection()

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
            conn.disconnect()
            break

        if cmd == "execute":
            try:
                if not conn.session_open:
                    conn.connect(company_file)
                response = conn.process_request(msg.get("qbxml", ""))
                _respond({"status": "ok", "response": response})
            except Exception as exc:
                conn.disconnect()
                _respond({"status": "error", "message": str(exc)})

        elif cmd == "ping":
            _respond({"status": "ok", "connected": conn.session_open})

        elif cmd == "disconnect":
            conn.disconnect()
            _respond({"status": "ok"})

        else:
            _respond({"status": "error", "message": f"Unknown command: {cmd}"})

    pythoncom.CoUninitialize()


def _respond(data: dict) -> None:
    sys.stdout.write(json.dumps(data) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
