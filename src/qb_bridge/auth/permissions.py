"""Per-key permission model.

Permissions are stored as a JSON object on each API key::

    {"*": ["admin"]}                                    # full access (default)
    {"*": ["read"]}                                     # read-only everything
    {"*": ["read"], "Customer": ["read", "write"]}      # read all, write customers
    {"Invoice": ["read", "insert"], "Report": ["read"]} # invoices r/insert, reports read

Entity names use the QB entity name (e.g. "Customer", "Invoice", "Account")
or special names: "*" (all entities), "Report" (all reports), "Company" (company info).

Operation shorthands:
    "admin"  = everything (read + write + delete)
    "read"   = list + get
    "write"  = insert + update + delete
    "insert" = create only (no update, no delete)
    "update" = modify existing (requires read to get EditSequence)
    "delete" = remove records

Explicit operations (for fine-grained control):
    "list", "get", "create", "update", "delete"
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

# Map shorthands to explicit operations
SHORTHANDS = {
    "admin": {"list", "get", "create", "update", "delete"},
    "read": {"list", "get"},
    "write": {"create", "update", "delete"},
    "insert": {"create"},
}

# Default permissions for new keys — read-only by default
DEFAULT_PERMISSIONS = {"*": ["read"]}


def expand_operations(ops: list[str]) -> set[str]:
    """Expand shorthand operations into explicit ones."""
    result: set[str] = set()
    for op in ops:
        if op in SHORTHANDS:
            result |= SHORTHANDS[op]
        else:
            result.add(op)
    return result


def check_permission(
    permissions: dict[str, list[str]],
    entity: str,
    operation: str,
) -> bool:
    """Check if a permission set allows *operation* on *entity*.

    Args:
        permissions: The key's permission dict (parsed from JSON).
        entity: QB entity name (e.g. "Customer") or "Report", "Company".
        operation: One of "list", "get", "create", "update", "delete".

    Returns:
        True if allowed.
    """
    # Check entity-specific permissions first
    if entity in permissions:
        allowed = expand_operations(permissions[entity])
        return operation in allowed

    # Fall back to wildcard
    if "*" in permissions:
        allowed = expand_operations(permissions["*"])
        return operation in allowed

    # No matching rule = denied
    return False


def parse_permissions(perms_json: str | None) -> dict[str, list[str]]:
    """Parse a key's stored permissions JSON.

    An empty/unset value means a brand-new key and yields the read-only
    default. A *non-empty but unparseable* value means the stored permissions
    are corrupt — we log it and **deny everything** (return ``{}``) rather than
    silently substituting a permissive default, which would hide the corruption
    and risk granting access the key was never meant to have.
    """
    if not perms_json:
        return dict(DEFAULT_PERMISSIONS)
    try:
        perms = json.loads(perms_json)
    except (json.JSONDecodeError, TypeError):
        log.error("Corrupt permissions JSON on API key; denying all access: %r", perms_json)
        return {}
    if not isinstance(perms, dict):
        log.error(
            "Permissions JSON is not an object (%s); denying all access: %r",
            type(perms).__name__,
            perms_json,
        )
        return {}
    return perms


def serialize_permissions(perms: dict[str, list[str]]) -> str:
    """Serialize permissions dict to JSON string."""
    return json.dumps(perms, separators=(",", ":"))


def describe_permissions(perms: dict[str, list[str]]) -> str:
    """Human-readable summary of permissions."""
    parts = []
    for entity, ops in perms.items():
        entity_label = "All entities" if entity == "*" else entity
        ops_str = ", ".join(ops)
        parts.append(f"{entity_label}: {ops_str}")
    return "; ".join(parts)
