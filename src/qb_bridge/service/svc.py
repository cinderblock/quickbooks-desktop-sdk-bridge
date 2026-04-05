"""Windows Service implementation using pywin32."""

from __future__ import annotations

import logging
import sys

import servicemanager
import win32event
import win32service
import win32serviceutil

log = logging.getLogger(__name__)


class QBBridgeService(win32serviceutil.ServiceFramework):
    """QuickBooks Bridge API Windows Service."""

    _svc_name_ = "QBBridge"
    _svc_display_name_ = "QuickBooks Bridge API"
    _svc_description_ = (
        "REST API bridge to QuickBooks Desktop. "
        "Provides CRUD endpoints, report generation, and real-time status."
    )

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.server = None

    def SvcStop(self):
        """Handle service stop request."""
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        log.info("Service stop requested")
        win32event.SetEvent(self.stop_event)
        if self.server:
            self.server.should_exit = True

    def SvcDoRun(self):
        """Main service entry point."""
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        log.info("Service starting")
        self.main()

    def main(self):
        """Run the FastAPI/uvicorn server."""
        import uvicorn

        from qb_bridge.config import get_settings
        from qb_bridge.main import create_app

        settings = get_settings()
        app = create_app(settings)

        config = uvicorn.Config(
            app,
            host=settings.host,
            port=settings.port,
            log_level=settings.log_level.lower(),
        )
        self.server = uvicorn.Server(config)
        self.server.run()

        log.info("Service stopped")


def main():
    """Entry point for service management commands."""
    if len(sys.argv) == 1:
        # Running as a service
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(QBBridgeService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        # CLI commands: install, remove, start, stop, restart, debug
        win32serviceutil.HandleCommandLine(QBBridgeService)


if __name__ == "__main__":
    main()
