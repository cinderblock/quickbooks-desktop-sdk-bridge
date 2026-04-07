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
    idle_timeout: int = 600  # seconds
    auto_launch_qb: bool = True
    auto_close_qb: bool = False
    request_timeout: float = 60.0

    model_config = {"env_prefix": "QBB_"}

    def model_post_init(self, __context) -> None:
        if self.db_path is None:
            self.db_path = self.data_dir / "qbbridge.db"
        if self.log_dir is None:
            self.log_dir = self.data_dir / "logs"


def get_settings() -> Settings:
    """Create a Settings instance (reads env vars)."""
    return Settings()
