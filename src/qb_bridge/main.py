"""FastAPI application factory, lifespan management, and CLI entry point."""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from qb_bridge import __version__
from qb_bridge.api.middleware import IPFilterMiddleware, RequestLoggingMiddleware
from qb_bridge.api.router import build_api_router
from qb_bridge.config import Settings, get_settings
from qb_bridge.database import init_db
from qb_bridge.qb.session import QBSessionManager

log = logging.getLogger("qb_bridge")


def _setup_logging(settings: Settings) -> None:
    """Configure logging to console + rotating file."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    root_logger = logging.getLogger("qb_bridge")
    root_logger.setLevel(level)

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root_logger.addHandler(console)

    # File handler
    log_dir = settings.log_dir
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        from logging.handlers import RotatingFileHandler

        file_handler = RotatingFileHandler(
            log_dir / "qbbridge.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root_logger.addHandler(file_handler)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return the FastAPI application."""
    if settings is None:
        settings = get_settings()

    _setup_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Startup / shutdown lifecycle."""
        # Ensure data dir exists
        settings.data_dir.mkdir(parents=True, exist_ok=True)

        # Initialize database
        db = await init_db(settings.db_path)
        app.state.db = db

        # Load company file path from DB (overrides env/default if set)
        from qb_bridge.database import get_setting, set_setting

        db_company_file = await get_setting(db, "company_file_path")
        company_file = db_company_file or settings.company_file

        # Ensure the GUI session secret is persisted in the DB.
        # If it was never set, generate a random one and save it so that
        # sessions survive service restarts.
        import secrets as _secrets

        gui_secret = await get_setting(db, "gui_session_secret")
        if not gui_secret:
            gui_secret = _secrets.token_hex(32)
            await set_setting(db, "gui_session_secret", gui_secret)
            log.info("Generated new GUI session secret")

        try:
            from qb_bridge.gui.router import set_session_secret

            set_session_secret(gui_secret)
        except ImportError:
            pass
        if company_file:
            log.info("Using company file: %s", company_file)

        # Start QB session manager
        qb_session = QBSessionManager(
            company_file=company_file,
            idle_timeout=settings.idle_timeout,
            auto_launch_qb=settings.auto_launch_qb,
            qb_exe_path=settings.qb_exe_path,
            auto_close_qb=settings.auto_close_qb,
            request_timeout=settings.request_timeout,
        )
        await qb_session.start()
        app.state.qb_session = qb_session

        log.info(
            "QuickBooks Bridge v%s started on %s:%d",
            __version__,
            settings.host,
            settings.port,
        )

        yield

        # Shutdown
        await qb_session.stop()
        await db.close()
        log.info("QuickBooks Bridge stopped")

    app = FastAPI(
        title="QuickBooks Bridge API",
        version=__version__,
        description=(
            "REST API bridge to QuickBooks Desktop. "
            "Provides clean CRUD endpoints for all QB entities, "
            "report generation (JSON/CSV/PDF), and real-time status monitoring.\n\n"
            "**Authentication:** Include `X-API-Key` header with every request to `/api/v1/*`.\n\n"
            "**IP Restriction:** Only private network IPs (10.x, 172.16-31.x, 192.168.x, 127.x) are allowed."
        ),
        lifespan=lifespan,
        docs_url=None,  # We mount our own with persistAuth
        redoc_url="/redoc",
        swagger_ui_oauth2_redirect_url=None,
    )

    # Custom Swagger UI with localStorage persistence for API key
    from fastapi.openapi.docs import get_swagger_ui_html

    @app.get("/docs", include_in_schema=False)
    async def custom_swagger_ui():
        return get_swagger_ui_html(
            openapi_url=app.openapi_url,
            title=app.title + " - Docs",
            swagger_ui_parameters={
                "persistAuthorization": True,  # Saves API key to localStorage
            },
        )

    # Global exception handler for QB errors
    from fastapi.responses import JSONResponse
    from qb_bridge.qb.exceptions import QBError

    @app.exception_handler(QBError)
    async def qb_error_handler(request, exc: QBError):
        log.error("QB error: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=502,
            content={
                "ok": False,
                "error": {
                    "code": type(exc).__name__,
                    "message": str(exc),
                    "qb_status_code": getattr(exc, "qb_status_code", None),
                },
            },
        )

    @app.exception_handler(Exception)
    async def general_error_handler(request, exc: Exception):
        log.error("Unhandled error: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": str(exc),
                },
            },
        )

    # Middleware (applied in reverse order — outermost first)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(IPFilterMiddleware)

    # API routes
    api_router = build_api_router()
    app.include_router(api_router)

    # GUI routes (lazy import to avoid circular deps)
    try:
        from qb_bridge.gui.router import gui_router

        app.include_router(gui_router)

        # Static files for GUI
        static_dir = Path(__file__).parent / "gui" / "static"
        if static_dir.exists():
            app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    except ImportError:
        log.debug("GUI module not available, skipping")

    return app


# ---------------------------------------------------------------------------
# Module-level app instance for uvicorn (e.g. ``uvicorn qb_bridge.main:app``)
# ---------------------------------------------------------------------------

app = create_app()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def cli() -> None:
    """Run the server directly (for development)."""
    import uvicorn

    settings = get_settings()

    uvicorn.run(
        "qb_bridge.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    cli()
