"""Settings saved by the GUI must actually reach the running service.

The Connection page wrote auto_launch_qb/idle_timeout into the database and
startup only ever read company_file_path back, so those settings silently did
nothing. These pin down that they are applied — and that bad or unknown values
are reported rather than ignored.
"""

from __future__ import annotations

import logging

import pytest

from qb_bridge.config import Settings
from qb_bridge.database import init_db, set_setting
from qb_bridge.settings_store import apply_db_settings


@pytest.fixture()
async def db(tmp_path):
    connection = await init_db(tmp_path / "test.db")
    yield connection
    await connection.close()


def base_settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path, db_path=tmp_path / "test.db", log_dir=tmp_path / "logs")


class TestApplyDbSettings:
    async def test_stored_values_are_applied(self, db, tmp_path):
        await set_setting(db, "auto_launch_qb", "true")
        await set_setting(db, "idle_timeout_seconds", "600")
        await set_setting(db, "qb_exe_path", r"C:\QB\QBW32Pro.exe")
        await set_setting(db, "company_file_path", r"C:\Books\Co.QBW")

        applied = await apply_db_settings(db, base_settings(tmp_path))

        assert applied.auto_launch_qb is True
        assert applied.idle_timeout == 600
        assert applied.qb_exe_path == r"C:\QB\QBW32Pro.exe"
        assert applied.company_file == r"C:\Books\Co.QBW"

    async def test_booleans_accept_the_usual_spellings(self, db, tmp_path):
        for stored, expected in (("TRUE", True), ("no", False), ("1", True), ("off", False)):
            await set_setting(db, "auto_close_qb", stored)
            applied = await apply_db_settings(db, base_settings(tmp_path))
            assert applied.auto_close_qb is expected, stored

    async def test_explicit_env_value_wins_over_the_database(self, db, tmp_path):
        """An env var is someone deliberately overriding the saved config."""
        await set_setting(db, "idle_timeout_seconds", "600")
        settings = Settings(
            data_dir=tmp_path,
            db_path=tmp_path / "test.db",
            log_dir=tmp_path / "logs",
            idle_timeout=45,
        )

        applied = await apply_db_settings(db, settings)

        assert applied.idle_timeout == 45

    async def test_unusable_value_is_reported_and_the_default_kept(self, db, tmp_path, caplog):
        await set_setting(db, "idle_timeout_seconds", "soon")
        settings = base_settings(tmp_path)

        with caplog.at_level(logging.ERROR):
            applied = await apply_db_settings(db, settings)

        assert applied.idle_timeout == settings.idle_timeout
        assert "idle_timeout_seconds" in caplog.text

    async def test_bad_boolean_is_reported(self, db, tmp_path, caplog):
        await set_setting(db, "auto_launch_qb", "sometimes")

        with caplog.at_level(logging.ERROR):
            applied = await apply_db_settings(db, base_settings(tmp_path))

        assert applied.auto_launch_qb is False
        assert "sometimes" in caplog.text

    async def test_unknown_key_is_reported(self, db, tmp_path, caplog):
        await set_setting(db, "sync_with_the_moon", "true")

        with caplog.at_level(logging.WARNING):
            await apply_db_settings(db, base_settings(tmp_path))

        assert "sync_with_the_moon" in caplog.text

    async def test_secrets_and_startup_only_keys_are_not_flagged(self, db, tmp_path, caplog):
        """These live in the same table but aren't settings we apply."""
        await set_setting(db, "gui_session_secret", "deadbeef")
        await set_setting(db, "gui_password_hash", "$2b$12$whatever")
        await set_setting(db, "listen_port", "8743")
        await set_setting(db, "listen_host", "0.0.0.0")

        with caplog.at_level(logging.WARNING):
            await apply_db_settings(db, base_settings(tmp_path))

        assert "deadbeef" not in caplog.text
        assert "listen_port" not in caplog.text

    async def test_empty_values_leave_the_default_alone(self, db, tmp_path):
        await set_setting(db, "company_file_path", "")
        applied = await apply_db_settings(db, base_settings(tmp_path))
        assert applied.company_file == ""

    async def test_log_level_is_validated_and_applied(self, db, tmp_path):
        await set_setting(db, "log_level", "debug")
        applied = await apply_db_settings(db, base_settings(tmp_path))
        assert applied.log_level == "DEBUG"
        assert logging.getLogger("qb_bridge").level == logging.DEBUG
        logging.getLogger("qb_bridge").setLevel(logging.INFO)

    async def test_bogus_log_level_is_rejected(self, db, tmp_path, caplog):
        await set_setting(db, "log_level", "CHATTY")

        with caplog.at_level(logging.ERROR):
            applied = await apply_db_settings(db, base_settings(tmp_path))

        assert applied.log_level == "INFO"
        assert "CHATTY" in caplog.text
