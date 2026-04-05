"""API key generation, hashing, and validation."""

from __future__ import annotations

import hashlib
import secrets

import aiosqlite

PREFIX = "qbb_"


def generate_api_key() -> tuple[str, str]:
    """Generate a new API key.

    Returns:
        ``(plaintext_key, sha256_hash)`` — store only the hash.
    """
    raw = secrets.token_hex(32)  # 64 hex chars
    key = PREFIX + raw
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    return key, key_hash


def hash_key(key: str) -> str:
    """Hash a plaintext API key for comparison."""
    return hashlib.sha256(key.encode()).hexdigest()


async def create_key(db: aiosqlite.Connection, name: str) -> tuple[int, str]:
    """Create a new API key in the database.

    Returns:
        ``(key_id, plaintext_key)`` — the plaintext key is shown once.
    """
    key, key_hash = generate_api_key()
    key_prefix = key[:12] + "..."  # "qbb_abcd1234..."

    cursor = await db.execute(
        "INSERT INTO api_keys (name, key_hash, key_prefix) VALUES (?, ?, ?)",
        (name, key_hash, key_prefix),
    )
    await db.commit()
    return cursor.lastrowid, key


async def validate_key(db: aiosqlite.Connection, provided_key: str) -> dict | None:
    """Validate an API key against the database.

    Returns the key row as a dict if valid, or None.
    Also updates ``last_used_at``.
    """
    provided_hash = hash_key(provided_key)
    async with db.execute(
        "SELECT id, name, key_prefix, is_active FROM api_keys WHERE key_hash = ?",
        (provided_hash,),
    ) as cursor:
        row = await cursor.fetchone()

    if row is None:
        return None

    if not row["is_active"]:
        return None

    # Update last_used_at
    await db.execute(
        "UPDATE api_keys SET last_used_at = datetime('now') WHERE id = ?",
        (row["id"],),
    )
    await db.commit()

    return dict(row)


async def list_keys(db: aiosqlite.Connection) -> list[dict]:
    """List all API keys (without hashes)."""
    async with db.execute(
        "SELECT id, name, key_prefix, created_at, last_used_at, is_active "
        "FROM api_keys ORDER BY created_at DESC"
    ) as cursor:
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def revoke_key(db: aiosqlite.Connection, key_id: int) -> bool:
    """Deactivate an API key."""
    cursor = await db.execute(
        "UPDATE api_keys SET is_active = 0 WHERE id = ?", (key_id,)
    )
    await db.commit()
    return cursor.rowcount > 0


async def delete_key(db: aiosqlite.Connection, key_id: int) -> bool:
    """Permanently delete an API key."""
    cursor = await db.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
    await db.commit()
    return cursor.rowcount > 0
