"""Application configuration via environment variables and database."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Settings loaded from environment variables (overridable)."""

    # Paths
    data_dir: Path = Path(os.environ.get("QBB_DATA_DIR", r"C:\ProgramData\QBBridge"))
    db_path: Path | None = None  # derived from data_dir if not set
    log_dir: Path | None = None  # derived from data_dir if not set

    # Server
    host: str = "0.0.0.0"
    port: int = 8743
    log_level: str = "INFO"

    # QuickBooks
    company_file: str = ""  # empty = use whatever QB has open
    qb_exe_path: str = r"C:\Program Files (x86)\Intuit\QuickBooks 2021\QBW32Pro.exe"
    idle_timeout: int = 120  # seconds — release QB lock quickly so Desktop can start
    auto_launch_qb: bool = False
    auto_close_qb: bool = False
    request_timeout: float = 60.0
    # Reports (esp. General Ledger over a wide range) can legitimately take far
    # longer than a CRUD call, so they get their own, larger timeout.
    report_timeout: float = 180.0

    # Retries — how hard to try again after a *recognized* transient fault
    # (QB blocked on a dialog, QB closed, dead COM worker). Unrecognized
    # errors are never retried.
    max_attempts: int = 3
    retry_backoff: float = 2.0

    # QuickBooks dialogs — QB blocks on its own modal dialogs (failed backup,
    # update reminder), which hangs every request until someone clicks them.
    dialog_watch: bool = True
    dialog_poll_interval: float = 5.0
    dialog_rules_file: Path | None = None  # derived from data_dir if not set

    model_config = {"env_prefix": "QBB_"}

    def model_post_init(self, __context) -> None:
        if self.db_path is None:
            self.db_path = self.data_dir / "qbbridge.db"
        if self.log_dir is None:
            self.log_dir = self.data_dir / "logs"
        if self.dialog_rules_file is None:
            self.dialog_rules_file = self.data_dir / "dialog_rules.json"


def get_settings() -> Settings:
    """Create a Settings instance (reads env vars)."""
    return Settings()
