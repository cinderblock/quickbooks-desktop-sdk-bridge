"""SQLite database setup, schema, and helpers."""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA_SQL = """
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
    permissions TEXT NOT NULL DEFAULT '{"*":["read"]}',
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
"""

DEFAULT_SETTINGS = {
    "company_file_path": "",
    "listen_host": "0.0.0.0",
    "listen_port": "8743",
    "idle_timeout_seconds": "600",
    "auto_launch_qb": "true",
    "auto_close_qb": "false",
    "qb_exe_path": r"C:\Program Files (x86)\Intuit\QuickBooks 2021\QBW32Pro.exe",
    "log_level": "INFO",
    "gui_password_hash": "",
    # Generated randomly on first startup; stored so sessions survive restarts
    "gui_session_secret": "",
}


async def init_db(db_path: Path) -> aiosqlite.Connection:
    """Open (or create) the database and apply schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(db_path))
    db.row_factory = aiosqlite.Row

    await db.executescript(SCHEMA_SQL)

    # Migration: add permissions column if missing (existing DBs)
    try:
        await db.execute(
            "ALTER TABLE api_keys ADD COLUMN permissions TEXT NOT NULL DEFAULT '{\"*\":[\"read\"]}'"
        )
        await db.commit()
        log.info("Migrated: added permissions column to api_keys (default: read-only)")
    except aiosqlite.OperationalError as exc:
        # The only expected failure is the column already existing on an
        # already-migrated DB. Anything else (locked DB, disk error, bad
        # schema) is a real problem and must not be swallowed.
        if "duplicate column name" not in str(exc).lower():
            raise

    # Migration: convert existing admin keys to read-only
    # Keys that still have the old default get downgraded
    cursor = await db.execute(
        "UPDATE api_keys SET permissions = '{\"*\":[\"read\"]}' "
        "WHERE permissions = '{\"*\":[\"admin\"]}'"
    )
    if cursor.rowcount:
        await db.commit()
        log.info("Migrated: converted %d existing key(s) to read-only", cursor.rowcount)

    # Seed default settings
    for key, value in DEFAULT_SETTINGS.items():
        await db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )

    # Track schema version
    await db.execute(
        "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
        (SCHEMA_VERSION,),
    )

    await db.commit()
    log.info("Database initialized at %s (schema v%d)", db_path, SCHEMA_VERSION)
    return db


async def get_setting(db: aiosqlite.Connection, key: str) -> str | None:
    """Get a single setting value."""
    async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
        row = await cursor.fetchone()
        return row["value"] if row else None


async def set_setting(db: aiosqlite.Connection, key: str, value: str) -> None:
    """Set a setting value."""
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
        (key, value),
    )
    await db.commit()


async def log_request(
    db: aiosqlite.Connection,
    *,
    api_key_id: int | None,
    client_ip: str,
    method: str,
    path: str,
    status_code: int | None = None,
    duration_ms: float | None = None,
    qb_request_type: str | None = None,
    error: str | None = None,
) -> None:
    """Write an entry to the audit log."""
    await db.execute(
        "INSERT INTO audit_log "
        "(api_key_id, client_ip, method, path, status_code, duration_ms, qb_request_type, error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (api_key_id, client_ip, method, path, status_code, duration_ms, qb_request_type, error),
    )
    await db.commit()
