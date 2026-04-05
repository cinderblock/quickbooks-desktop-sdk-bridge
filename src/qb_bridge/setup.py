"""Interactive setup wizard for QuickBooks Bridge.

Run with:  uv run python -m qb_bridge.setup

Handles everything in one shot:
1. Create data directory + SQLite database
2. Launch QuickBooks Desktop if needed
3. Trigger QB authorization dialog + verify connection
4. Set GUI admin password
5. Generate first API key
6. Optionally install + start Windows Service
"""

from __future__ import annotations

import getpass
import os
import socket
import sqlite3
import sys
import time

# We use synchronous code here since this is a one-shot CLI script

BANNER = r"""
  ___  ____    ____       _     _
 / _ \| __ )  | __ ) _ __(_) __| | __ _  ___
| | | |  _ \  |  _ \| '__| |/ _` |/ _` |/ _ \
| |_| | |_) | | |_) | |  | | (_| | (_| |  __/
 \__\_\____/  |____/|_|  |_|\__,_|\__, |\___|
                                   |___/
  Setup Wizard
"""


def main() -> None:
    print(BANNER)

    data_dir = os.environ.get("QBB_DATA_DIR", r"C:\ProgramData\QBBridge")
    db_path = os.path.join(data_dir, "qbbridge.db")
    port = 8743

    # ---- Step 1: Create data directory + database ----
    print("[1/6] Setting up data directory...")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(os.path.join(data_dir, "logs"), exist_ok=True)

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    _init_schema(db)
    print(f"  Database: {db_path}")

    # ---- Step 2: Check / launch QuickBooks ----
    print("\n[2/6] Checking QuickBooks Desktop...")
    from qb_bridge.qb.process import is_qb_running, launch_qb

    if is_qb_running():
        print("  QuickBooks is already running.")
    else:
        print("  QuickBooks is not running. Launching...")
        if launch_qb(wait_seconds=60):
            print("  QuickBooks launched successfully.")
        else:
            print("  WARNING: Could not launch QuickBooks.")
            print("  Please start QuickBooks manually with a company file open,")
            print("  then press Enter to continue...")
            input()

    # ---- Step 3: Authorize + verify connection ----
    print("\n[3/6] Connecting to QuickBooks SDK...")
    print("  If this is the first time, QuickBooks will show an authorization dialog.")
    print("  >>> Switch to QuickBooks and click 'Yes, always allow access' <<<")
    print()

    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        try:
            company_name = _test_qb_connection()
            print(f"  Connected! Company: {company_name}")
            break
        except Exception as exc:
            print(f"  Attempt {attempt}/{max_attempts} failed: {exc}")
            if attempt < max_attempts:
                print("  Retrying in 10 seconds (check the QB authorization dialog)...")
                time.sleep(10)
            else:
                print("\n  Could not connect to QuickBooks after multiple attempts.")
                print("  Troubleshooting:")
                print("    - Is QuickBooks open with a company file?")
                print("    - Did you click 'Yes, always allow' in the QB dialog?")
                print("    - Edit > Preferences > Integrated Applications > Company Prefs")
                print("      Make sure this app is allowed.")
                resp = input("\n  Continue setup anyway? (y/n): ").strip().lower()
                if resp != "y":
                    db.close()
                    sys.exit(1)

    # ---- Step 4: Set GUI password ----
    print("\n[4/6] Set admin password for the web dashboard...")
    while True:
        pw1 = getpass.getpass("  Password: ")
        pw2 = getpass.getpass("  Confirm:  ")
        if pw1 == pw2 and len(pw1) >= 4:
            break
        print("  Passwords don't match or too short (min 4 chars). Try again.")

    import bcrypt

    pw_hash = bcrypt.hashpw(pw1.encode(), bcrypt.gensalt()).decode()
    _set_setting(db, "gui_password_hash", pw_hash)
    print("  Password set.")

    # ---- Step 5: Generate first API key ----
    print("\n[5/6] Generating your first API key...")
    key_name = input("  Name for this key (e.g. 'Dev Machine'): ").strip() or "Default"

    import hashlib
    import secrets

    raw_key = "qbb_" + secrets.token_hex(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:12] + "..."

    db.execute(
        "INSERT INTO api_keys (name, key_hash, key_prefix) VALUES (?, ?, ?)",
        (key_name, key_hash, key_prefix),
    )
    db.commit()

    print()
    print("  " + "=" * 70)
    print(f"  API KEY: {raw_key}")
    print("  " + "=" * 70)
    print("  Save this key now. It will NOT be shown again.")
    print(f"  Use as header: X-API-Key: {raw_key}")
    print()

    # ---- Step 6: Service install (optional) ----
    print("[6/6] Windows Service installation...")
    install_svc = input("  Install as Windows Service? (y/n): ").strip().lower()

    if install_svc == "y":
        username = input(f"  Windows username [{os.environ.get('USERNAME', '')}]: ").strip()
        if not username:
            username = os.environ.get("USERNAME", "")
        # Prefix with machine name if not already qualified
        if "\\" not in username and "@" not in username:
            username = f".\\{username}"
        password = getpass.getpass("  Windows password: ")

        try:
            from qb_bridge.service.install import install_with_user

            install_with_user(username, password)
            print("  Service installed!")

            start_svc = input("  Start the service now? (y/n): ").strip().lower()
            if start_svc == "y":
                import subprocess

                subprocess.run(["sc.exe", "start", "QBBridge"], capture_output=True)
                time.sleep(3)
                # Quick health check
                try:
                    import urllib.request

                    resp = urllib.request.urlopen(
                        f"http://localhost:{port}/api/v1/status", timeout=5
                    )
                    print(f"  Service is running! (HTTP {resp.status})")
                except Exception:
                    print("  Service started. It may take a few seconds to be ready.")
        except Exception as exc:
            print(f"  Service installation failed: {exc}")
            print("  You can install later with: python -m qb_bridge.service.svc install")
    else:
        print("  Skipped. Run manually with: uv run python -m qb_bridge.main")

    db.close()

    # ---- Summary ----
    local_ip = _get_local_ip()
    print()
    print("=" * 60)
    print("  Setup Complete!")
    print("=" * 60)
    print(f"  API URL:     http://{local_ip}:{port}")
    print(f"  Swagger UI:  http://{local_ip}:{port}/docs")
    print(f"  ReDoc:       http://{local_ip}:{port}/redoc")
    print(f"  Dashboard:   http://{local_ip}:{port}/gui/")
    print(f"  Data dir:    {data_dir}")
    print()


def _test_qb_connection() -> str:
    """Test the QB COM connection synchronously. Returns company name."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        rp = win32com.client.Dispatch("QBXMLRP2.RequestProcessor")
        rp.OpenConnection2("QBBridge", "QuickBooks Bridge API", 1)
        ticket = rp.BeginSession("", 2)  # qbFileOpenDoNotCare

        request = """<?xml version="1.0" encoding="utf-8"?>
<?qbxml version="13.0"?>
<QBXML>
  <QBXMLMsgsRq onError="stopOnError">
    <CompanyQueryRq>
    </CompanyQueryRq>
  </QBXMLMsgsRq>
</QBXML>"""

        response = rp.ProcessRequest(ticket, request)

        # Parse company name
        import xml.etree.ElementTree as ET

        root = ET.fromstring(response)
        name_elem = root.find(".//CompanyName")
        company_name = name_elem.text if name_elem is not None else "Unknown"

        rp.EndSession(ticket)
        rp.CloseConnection()
        return company_name
    finally:
        pythoncom.CoUninitialize()


def _init_schema(db: sqlite3.Connection) -> None:
    """Create tables if they don't exist."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS api_keys (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            key_hash    TEXT NOT NULL UNIQUE,
            key_prefix  TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            last_used_at TEXT,
            is_active   INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
            api_key_id  INTEGER,
            client_ip   TEXT NOT NULL,
            method      TEXT NOT NULL,
            path        TEXT NOT NULL,
            status_code INTEGER,
            duration_ms REAL,
            qb_request_type TEXT,
            error       TEXT,
            FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
        );
        CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);
    """)

    # Seed default settings
    defaults = {
        "company_file_path": "",
        "listen_host": "0.0.0.0",
        "listen_port": "8743",
        "idle_timeout_seconds": "600",
        "auto_launch_qb": "true",
        "auto_close_qb": "false",
        "qb_exe_path": r"C:\Program Files (x86)\Intuit\QuickBooks 2021\QBW32.EXE",
        "log_level": "INFO",
        "gui_password_hash": "",
    }
    for key, value in defaults.items():
        db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
    db.commit()


def _set_setting(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
        (key, value),
    )
    db.commit()


def _get_local_ip() -> str:
    """Get the machine's local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


if __name__ == "__main__":
    main()
