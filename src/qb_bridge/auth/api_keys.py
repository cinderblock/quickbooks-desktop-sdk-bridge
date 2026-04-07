"""API key generation, hashing, validation, and permission management."""

from __future__ import annotations

import hashlib
import secrets

import aiosqlite

from .permissions import DEFAULT_PERMISSIONS, serialize_permissions

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


async def create_key(
    db: aiosqlite.Connection,
    name: str,
    permissions: dict[str, list[str]] | None = None,
) -> tuple[int, str]:
    """Create a new API key in the database.

    Args:
        name: Human label for the key.
        permissions: Permission dict, defaults to full admin.

    Returns:
        ``(key_id, plaintext_key)`` — the plaintext key is shown once.
    """
    key, key_hash = generate_api_key()
    key_prefix = key[:12] + "..."
    perms_json = serialize_permissions(permissions or DEFAULT_PERMISSIONS)

    cursor = await db.execute(
        "INSERT INTO api_keys (name, key_hash, key_prefix, permissions) VALUES (?, ?, ?, ?)",
        (name, key_hash, key_prefix, perms_json),
    )
    await db.commit()
    return cursor.lastrowid, key


async def validate_key(db: aiosqlite.Connection, provided_key: str) -> dict | None:
    """Validate an API key against the database.

    Returns the key row as a dict (including ``permissions`` as a parsed dict)
    if valid, or None. Also updates ``last_used_at``.
    """
    from .permissions import parse_permissions

    provided_hash = hash_key(provided_key)
    async with db.execute(
        "SELECT id, name, key_prefix, is_active, permissions FROM api_keys WHERE key_hash = ?",
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

    result = dict(row)
    result["permissions"] = parse_permissions(result.get("permissions"))
    return result


async def update_key_permissions(
    db: aiosqlite.Connection, key_id: int, permissions: dict[str, list[str]]
) -> bool:
    """Update permissions for an existing key."""
    perms_json = serialize_permissions(permissions)
    cursor = await db.execute(
        "UPDATE api_keys SET permissions = ? WHERE id = ?",
        (perms_json, key_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def list_keys(db: aiosqlite.Connection) -> list[dict]:
    """List all API keys (without hashes)."""
    from .permissions import parse_permissions

    async with db.execute(
        "SELECT id, name, key_prefix, permissions, created_at, last_used_at, is_active "
        "FROM api_keys ORDER BY created_at DESC"
    ) as cursor:
        rows = await cursor.fetchall()

    results = []
    for row in rows:
        d = dict(row)
        d["permissions"] = parse_permissions(d.get("permissions"))
        results.append(d)
    return results


async def revoke_key(db: aiosqlite.Connection, key_id: int) -> bool:
    """Deactivate an API key."""
    cursor = await db.execute("UPDATE api_keys SET is_active = 0 WHERE id = ?", (key_id,))
    await db.commit()
    return cursor.rowcount > 0


async def delete_key(db: aiosqlite.Connection, key_id: int) -> bool:
    """Permanently delete an API key."""
    cursor = await db.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
    await db.commit()
    return cursor.rowcount > 0
