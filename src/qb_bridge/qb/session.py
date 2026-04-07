"""Thread-safe async bridge to QuickBooks COM.

Architecture:
    FastAPI async handlers -> asyncio.Queue -> dedicated COM worker thread.
    The worker thread owns all COM objects (apartment-threaded requirement).
    Results flow back via ``loop.call_soon_threadsafe(future.set_result, ...)``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue
import threading
import time
from dataclasses import dataclass, field

from .connection import QBConnection
from .exceptions import QBConnectionError, QBSessionError, QBTimeoutError
from .process import QB_EXE_PATH, launch_qb

log = logging.getLogger(__name__)

# Sentinel to tell the worker thread to shut down
_SHUTDOWN = object()

# How long to wait between connection retries (seconds)
_CONNECT_RETRY_DELAY = 5.0
# Max retries when connecting (covers QB login screen, slow startup, etc.)
_CONNECT_MAX_RETRIES = 12  # 12 * 5s = 60s total


@dataclass
class _QBRequest:
    """Internal wrapper for a queued QB request."""

    qbxml: str
    future: asyncio.Future
    loop: asyncio.AbstractEventLoop
    submitted_at: float = field(default_factory=time.monotonic)


class QBSessionManager:
    """Async-safe manager for QuickBooks COM sessions.

    Usage::

        mgr = QBSessionManager(company_file="", idle_timeout=600)
        await mgr.start()
        response_xml = await mgr.execute(qbxml_request)
        await mgr.stop()
    """

    def __init__(
        self,
        company_file: str = "",
        idle_timeout: int = 600,
        auto_launch_qb: bool = True,
        qb_exe_path: str = "",
        auto_close_qb: bool = False,
        request_timeout: float = 90.0,
    ) -> None:
        self.company_file = company_file
        self.idle_timeout = idle_timeout
        self.auto_launch_qb = auto_launch_qb
        self.qb_exe_path = qb_exe_path
        self.auto_close_qb = auto_close_qb
        self.request_timeout = request_timeout

        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._running = False
        self._last_activity: float = 0.0
        self._connection_state: str = (
            "disconnected"  # disconnected | connecting | connected | error
        )
        self._last_connect_attempt: float = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        return self._connection_state

    @property
    def idle_seconds(self) -> float:
        if self._last_activity == 0:
            return 0
        return time.monotonic() - self._last_activity

    async def start(self) -> None:
        """Start the COM worker thread."""
        if self._running:
            return
        self._running = True
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="qb-com-worker",
            daemon=True,
        )
        self._worker.start()
        log.info("QBSessionManager started")

    async def stop(self) -> None:
        """Gracefully shut down: disconnect and join the worker thread."""
        if not self._running:
            return
        self._running = False
        self._queue.put(_SHUTDOWN)
        if self._worker is not None:
            self._worker.join(timeout=15)
        log.info("QBSessionManager stopped")

    async def execute(self, qbxml: str) -> str:
        """Submit a qbXML request and await the response.

        Raises:
            QBConnectionError: Cannot connect to QB.
            QBTimeoutError: Request did not complete within timeout.
            QBRequestError: QB returned an error status.
        """
        if not self._running:
            raise QBConnectionError("QBSessionManager is not running")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        request = _QBRequest(qbxml=qbxml, future=future, loop=loop)
        self._queue.put(request)

        try:
            return await asyncio.wait_for(future, timeout=self.request_timeout)
        except TimeoutError as exc:
            raise QBTimeoutError(f"QB request timed out after {self.request_timeout}s") from exc

    # -------------------------------------------------------------------
    # Worker thread (all COM calls happen here)
    # -------------------------------------------------------------------

    def _worker_loop(self) -> None:
        """Main loop for the dedicated COM thread."""
        import pythoncom

        pythoncom.CoInitialize()
        log.debug("COM initialized on worker thread")

        conn = QBConnection()

        try:
            while self._running:
                # Check idle timeout
                if conn.session_open and self._last_activity > 0:
                    idle = time.monotonic() - self._last_activity
                    if idle > self.idle_timeout:
                        log.info(
                            "Idle timeout (%.0fs > %ds), disconnecting",
                            idle,
                            self.idle_timeout,
                        )
                        conn.disconnect()
                        self._connection_state = "disconnected"

                        if self.auto_close_qb:
                            from .process import close_qb

                            close_qb()

                # Poll queue with short timeout so we can check idle
                try:
                    item = self._queue.get(timeout=1.0)
                except queue.Empty:
                    continue

                if item is _SHUTDOWN:
                    break

                request: _QBRequest = item
                self._last_activity = time.monotonic()

                try:
                    # Ensure connected (with retries for QB startup/login)
                    if not conn.session_open:
                        self._connect_with_retry(conn)

                    response = conn.process_request(request.qbxml)
                    request.loop.call_soon_threadsafe(request.future.set_result, response)
                except Exception as exc:
                    log.error("QB request failed: %s", exc, exc_info=True)
                    self._connection_state = "error"
                    with contextlib.suppress(Exception):
                        conn.disconnect()
                    request.loop.call_soon_threadsafe(request.future.set_exception, exc)
        finally:
            conn.disconnect()
            self._connection_state = "disconnected"
            pythoncom.CoUninitialize()
            log.debug("COM uninitialized on worker thread")

    def _connect_with_retry(self, conn: QBConnection) -> None:
        """Try to connect to QB with retries.

        The COM BeginSession call is the only reliable way to know if QB is
        ready — process detection is unreliable across 32/64-bit boundaries.
        We just retry the actual connection, which handles:
        - QB still loading after launch
        - QB on the login/password screen
        - QB switching company files
        - QB not running (launches it on first attempt if auto_launch is on)
        """
        self._connection_state = "connecting"
        launched = False
        last_error: Exception | None = None

        for attempt in range(1, _CONNECT_MAX_RETRIES + 1):
            if not self._running:
                raise QBConnectionError("Shutting down")

            try:
                log.info("Connecting to QB (attempt %d/%d)...", attempt, _CONNECT_MAX_RETRIES)
                conn.connect(self.company_file)
                self._connection_state = "connected"
                log.info("Connected to QuickBooks!")
                return
            except (QBConnectionError, QBSessionError) as exc:
                last_error = exc
                log.warning(
                    "Connection attempt %d/%d failed: %s",
                    attempt,
                    _CONNECT_MAX_RETRIES,
                    exc,
                )
                with contextlib.suppress(Exception):
                    conn.disconnect()

                # Try launching QB once if auto_launch is enabled
                if not launched and self.auto_launch_qb:
                    launched = True
                    exe = self.qb_exe_path or QB_EXE_PATH
                    cf = self.company_file or None
                    log.info("Attempting to launch QuickBooks...")
                    launch_qb(company_file=cf, exe_path=exe)

                if attempt < _CONNECT_MAX_RETRIES:
                    log.info(
                        "Retrying in %.0fs (QB may still be loading or on login screen)...",
                        _CONNECT_RETRY_DELAY,
                    )
                    time.sleep(_CONNECT_RETRY_DELAY)

        # All retries exhausted
        self._connection_state = "error"
        raise QBConnectionError(
            f"Could not connect to QuickBooks after {_CONNECT_MAX_RETRIES} attempts. "
            f"Last error: {last_error}. "
            f"Make sure QuickBooks is fully open with a company file loaded."
        ) from last_error
