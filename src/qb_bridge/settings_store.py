"""Settings the GUI stores in the database, applied at startup.

The Connection page writes ``idle_timeout_seconds``, ``auto_launch_qb`` and
friends into the ``settings`` table, but startup only ever read
``company_file_path`` back out — so those choices silently did nothing.
:func:`apply_db_settings` applies all of them.

Precedence is **explicit beats stored beats default**: a value given as an
environment variable (or passed to ``Settings(...)``) wins over the database,
because that is someone deliberately overriding this machine's saved
configuration. Anything the database holds that isn't a known setting, or that
can't be read as the right type, is logged rather than dropped on the floor.
"""

from __future__ import annotations

import logging

import aiosqlite

from qb_bridge.config import Settings

log = logging.getLogger(__name__)

# database key -> Settings field
DB_SETTING_FIELDS: dict[str, str] = {
    "company_file_path": "company_file",
    "idle_timeout_seconds": "idle_timeout",
    "auto_launch_qb": "auto_launch_qb",
    "auto_close_qb": "auto_close_qb",
    "qb_exe_path": "qb_exe_path",
    "log_level": "log_level",
}

# Keys read before the app exists (start_server.py binds the socket), so they
# are not overrides applied here.
STARTUP_ONLY_KEYS = frozenset({"listen_host", "listen_port"})

# Keys that live in the same table but aren't configuration.
NON_SETTING_KEYS = frozenset({"gui_password_hash", "gui_session_secret"})

_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})


def _coerce(field: str, value: str, current: object) -> object:
    """Convert a stored string to the type of the setting it overrides."""
    if isinstance(current, bool):
        lowered = value.strip().lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise ValueError(f"expected a boolean (true/false), got {value!r}")
    if isinstance(current, int):
        return int(value)
    if isinstance(current, float):
        return float(value)
    if field == "log_level":
        level = value.strip().upper()
        if not isinstance(logging.getLevelName(level), int):
            raise ValueError(f"unknown log level {value!r}")
        return level
    return value


async def apply_db_settings(db: aiosqlite.Connection, settings: Settings) -> Settings:
    """Return *settings* with the database's stored values applied."""
    async with db.execute("SELECT key, value FROM settings") as cursor:
        stored = {row["key"]: row["value"] for row in await cursor.fetchall()}

    updates: dict[str, object] = {}
    for key, value in stored.items():
        if key in NON_SETTING_KEYS:
            continue
        if key in STARTUP_ONLY_KEYS:
            continue

        field = DB_SETTING_FIELDS.get(key)
        if field is None:
            log.warning(
                "Ignoring unknown setting %r=%r in the database — it is not a known "
                "setting, so nothing is reading it. Known keys: %s",
                key,
                value,
                sorted(DB_SETTING_FIELDS) + sorted(STARTUP_ONLY_KEYS),
            )
            continue

        if value is None or value == "":
            continue

        if field in settings.model_fields_set:
            log.info(
                "Setting %s=%r from the database is overridden by QBB_%s=%r",
                key,
                value,
                field.upper(),
                getattr(settings, field),
            )
            continue

        try:
            updates[field] = _coerce(field, value, getattr(settings, field))
        except ValueError as exc:
            log.error(
                "Ignoring unusable setting %r=%r from the database: %s. "
                "Fix it on the Connection page; using %r until then.",
                key,
                value,
                exc,
                getattr(settings, field),
            )

    if not updates:
        return settings

    log.info(
        "Applied stored settings: %s",
        ", ".join(f"{field}={value!r}" for field, value in sorted(updates.items())),
    )
    applied = settings.model_copy(update=updates)

    if "log_level" in updates:
        logging.getLogger("qb_bridge").setLevel(getattr(logging, applied.log_level.upper()))

    return applied
