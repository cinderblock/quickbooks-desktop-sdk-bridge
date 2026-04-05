"""CLI utilities — API key management, etc.

Usage:
    uv run python -m qb_bridge.cli create-key "My Dev Machine"
    uv run python -m qb_bridge.cli list-keys
    uv run python -m qb_bridge.cli revoke-key 3
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import sys
from pathlib import Path

DATA_DIR = Path("C:/ProgramData/QBBridge")
DB_PATH = DATA_DIR / "qbbridge.db"

SCHEMA_SQL = """
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
"""


def _get_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA_SQL)
    return db


def create_key(name: str) -> None:
    db = _get_db()
    raw_key = "qbb_" + secrets.token_hex(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:12] + "..."

    db.execute(
        "INSERT INTO api_keys (name, key_hash, key_prefix) VALUES (?, ?, ?)",
        (name, key_hash, key_prefix),
    )
    db.commit()
    db.close()

    print()
    print("=" * 72)
    print(f"  API KEY: {raw_key}")
    print("=" * 72)
    print(f"  Name:    {name}")
    print(f"  Prefix:  {key_prefix}")
    print()
    print("  Save this key now -- it will NOT be shown again.")
    print(f"  Usage:   curl -H 'X-API-Key: {raw_key}' http://localhost:8743/api/v1/status")
    print()


def list_keys() -> None:
    db = _get_db()
    rows = db.execute(
        "SELECT id, name, key_prefix, created_at, last_used_at, is_active "
        "FROM api_keys ORDER BY created_at DESC"
    ).fetchall()
    db.close()

    if not rows:
        print('No API keys. Create one with:  uv run python -m qb_bridge.cli create-key "name"')
        return

    print(f"{'ID':>4}  {'Name':<25} {'Prefix':<18} {'Created':<20} {'Last Used':<20} {'Status'}")
    print("-" * 115)
    for row in rows:
        status = "Active" if row["is_active"] else "REVOKED"
        last_used = row["last_used_at"] or "Never"
        print(
            f"{row['id']:>4}  {row['name']:<25} {row['key_prefix']:<18} "
            f"{row['created_at']:<20} {last_used:<20} {status}"
        )


def revoke_key(key_id: int) -> None:
    db = _get_db()
    cursor = db.execute("UPDATE api_keys SET is_active = 0 WHERE id = ?", (key_id,))
    db.commit()
    db.close()

    if cursor.rowcount:
        print(f"Key {key_id} revoked.")
    else:
        print(f"Key {key_id} not found.")


def main() -> None:
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        return

    cmd = args[0]

    if cmd == "create-key":
        name = args[1] if len(args) > 1 else "Default"
        create_key(name)
    elif cmd == "list-keys":
        list_keys()
    elif cmd == "revoke-key":
        if len(args) < 2:
            print("Usage: revoke-key <id>")
            return
        revoke_key(int(args[1]))
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
