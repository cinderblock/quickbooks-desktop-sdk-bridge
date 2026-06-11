"""CLI utilities — API key management, etc.

Usage:
    uv run python -m qb_bridge.cli create-key "My Dev Machine"
    uv run python -m qb_bridge.cli create-key "Read-Write Key" --permissions '{"*":["admin"]}'
    uv run python -m qb_bridge.cli create-key "Insert Only" --permissions '{"Customer":["read","insert"]}'
    uv run python -m qb_bridge.cli list-keys
    uv run python -m qb_bridge.cli revoke-key 3
    uv run python -m qb_bridge.cli set-permissions 3 '{"*":["read"]}'

Permission presets (use with --preset instead of --permissions):
    readonly     = {"*": ["read"]}
    readwrite    = {"*": ["read", "write"]}
    admin        = {"*": ["admin"]}
"""

from __future__ import annotations

import hashlib
import json
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
    permissions TEXT NOT NULL DEFAULT '{"*":["read"]}',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1
);
"""

PRESETS = {
    "readonly": {"*": ["read"]},
    "readwrite": {"*": ["read", "write"]},
    "admin": {"*": ["admin"]},
}

DEFAULT_PERMISSIONS = {"*": ["read"]}


def _get_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA_SQL)

    # Migration: add permissions column if missing (existing DBs)
    try:
        db.execute(
            'ALTER TABLE api_keys ADD COLUMN permissions TEXT NOT NULL DEFAULT \'{"*":["read"]}\''
        )
        db.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists

    return db


def create_key(name: str, permissions: dict | None = None) -> None:
    db = _get_db()
    perms = permissions or DEFAULT_PERMISSIONS
    perms_json = json.dumps(perms, separators=(",", ":"))

    raw_key = "qbb_" + secrets.token_hex(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:12] + "..."

    db.execute(
        "INSERT INTO api_keys (name, key_hash, key_prefix, permissions) VALUES (?, ?, ?, ?)",
        (name, key_hash, key_prefix, perms_json),
    )
    db.commit()
    db.close()

    print()
    print("=" * 72)
    print(f"  API KEY: {raw_key}")
    print("=" * 72)
    print(f"  Name:        {name}")
    print(f"  Prefix:      {key_prefix}")
    print(f"  Permissions: {_describe_perms(perms)}")
    print()
    print("  Save this key now -- it will NOT be shown again.")
    print(f"  Usage:   curl -H 'X-API-Key: {raw_key}' http://localhost:8743/api/v1/status")
    print()


def list_keys() -> None:
    db = _get_db()
    rows = db.execute(
        "SELECT id, name, key_prefix, permissions, created_at, last_used_at, is_active "
        "FROM api_keys ORDER BY created_at DESC"
    ).fetchall()
    db.close()

    if not rows:
        print('No API keys. Create one with:  uv run python -m qb_bridge.cli create-key "name"')
        return

    print(
        f"{'ID':>4}  {'Name':<20} {'Prefix':<16} {'Permissions':<30} "
        f"{'Created':<20} {'Last Used':<20} {'Status'}"
    )
    print("-" * 140)
    for row in rows:
        status = "Active" if row["is_active"] else "REVOKED"
        last_used = row["last_used_at"] or "Never"
        perms = _parse_perms(row["permissions"])
        perms_str = _describe_perms(perms)
        if len(perms_str) > 28:
            perms_str = perms_str[:25] + "..."
        print(
            f"{row['id']:>4}  {row['name']:<20} {row['key_prefix']:<16} {perms_str:<30} "
            f"{row['created_at']:<20} {last_used:<20} {status}"
        )


def set_permissions(key_id: int, permissions: dict) -> None:
    db = _get_db()
    perms_json = json.dumps(permissions, separators=(",", ":"))
    cursor = db.execute(
        "UPDATE api_keys SET permissions = ? WHERE id = ?",
        (perms_json, key_id),
    )
    db.commit()
    db.close()

    if cursor.rowcount:
        print(f"Key {key_id} permissions updated: {_describe_perms(permissions)}")
    else:
        print(f"Key {key_id} not found.")


def revoke_key(key_id: int) -> None:
    db = _get_db()
    cursor = db.execute("UPDATE api_keys SET is_active = 0 WHERE id = ?", (key_id,))
    db.commit()
    db.close()

    if cursor.rowcount:
        print(f"Key {key_id} revoked.")
    else:
        print(f"Key {key_id} not found.")


def _parse_perms(perms_str: str | None) -> dict:
    if not perms_str:
        return DEFAULT_PERMISSIONS
    try:
        p = json.loads(perms_str)
        if isinstance(p, dict):
            return p
    except (json.JSONDecodeError, TypeError):
        pass
    return DEFAULT_PERMISSIONS


def _describe_perms(perms: dict) -> str:
    parts = []
    for entity, ops in perms.items():
        label = "All" if entity == "*" else entity
        parts.append(f"{label}: {','.join(ops)}")
    return "; ".join(parts)


def main() -> None:
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        return

    cmd = args[0]

    if cmd == "create-key":
        name = "Default"
        permissions = None

        # Parse positional name and optional flags
        rest = args[1:]
        positional = []
        i = 0
        while i < len(rest):
            if rest[i] == "--permissions" and i + 1 < len(rest):
                try:
                    permissions = json.loads(rest[i + 1])
                except json.JSONDecodeError:
                    print(f"Invalid JSON: {rest[i + 1]}")
                    return
                i += 2
            elif rest[i] == "--preset" and i + 1 < len(rest):
                preset = rest[i + 1].lower()
                if preset not in PRESETS:
                    print(f"Unknown preset: {preset}. Available: {', '.join(PRESETS)}")
                    return
                permissions = PRESETS[preset]
                i += 2
            else:
                positional.append(rest[i])
                i += 1

        if positional:
            name = positional[0]

        create_key(name, permissions)

    elif cmd == "list-keys":
        list_keys()

    elif cmd == "revoke-key":
        if len(args) < 2:
            print("Usage: revoke-key <id>")
            return
        revoke_key(int(args[1]))

    elif cmd == "set-permissions":
        if len(args) < 3:
            print("Usage: set-permissions <id> <json-or-preset>")
            print(f"Presets: {', '.join(PRESETS)}")
            return
        key_id = int(args[1])
        perm_arg = args[2]
        if perm_arg.lower() in PRESETS:
            permissions = PRESETS[perm_arg.lower()]
        else:
            try:
                permissions = json.loads(perm_arg)
            except json.JSONDecodeError:
                print(f"Invalid JSON: {perm_arg}")
                return
        set_permissions(key_id, permissions)

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
