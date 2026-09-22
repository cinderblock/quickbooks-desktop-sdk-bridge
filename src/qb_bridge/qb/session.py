"""QuickBooks COM session manager.

Runs COM in a **subprocess** to avoid STA apartment issues with uvicorn.
The subprocess (worker.py) handles COM directly on its main thread,
communicating via JSON-over-stdio.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from .exceptions import QBConnectionError, QBError, QBTimeoutError, QBUnavailableError
from .retry import (
    REMEDY_DISMISS_DIALOGS,
    REMEDY_LAUNCH_QB,
    REMEDY_RESTART_WORKER,
    TransientFault,
    classify,
)

log = logging.getLogger(__name__)

WORKER_SCRIPT = str(Path(__file__).parent / "worker.py")

_CONNECT_RETRY_DELAY = 5.0
_CONNECT_MAX_RETRIES = 12


class QBSessionManager:
    """Async session manager that delegates COM to a subprocess."""

    def __init__(
        self,
        company_file: str = "",
        idle_timeout: int = 600,
        auto_launch_qb: bool = True,
        qb_exe_path: str = "",
        auto_close_qb: bool = False,
        request_timeout: float = 90.0,
        report_timeout: float = 180.0,
        max_attempts: int = 3,
        retry_backoff: float = 2.0,
        on_blocked: Callable[[], Awaitable[list[str] | None]] | None = None,
    ) -> None:
        self.company_file = company_file
        self.idle_timeout = idle_timeout
        self.auto_launch_qb = auto_launch_qb
        self.qb_exe_path = qb_exe_path
        self.auto_close_qb = auto_close_qb
        self.request_timeout = request_timeout
        self.report_timeout = report_timeout
        self.max_attempts = max(1, max_attempts)
        self.retry_backoff = retry_backoff
        # Called before retrying a request QuickBooks refused because a modal
        # dialog was up; wired to the dialog watcher's sweep in main.py. Returns
        # the titles of any dialogs it could not dismiss.
        self.on_blocked = on_blocked
        self._blocking_dialogs: list[str] = []

        self.retry_count = 0
        self.last_fault: str | None = None

        self._proc: subprocess.Popen | None = None
        self._lock = asyncio.Lock()
        self._running = False
        self._last_activity: float = 0.0
        self._connection_state: str = "disconnected"
        self._idle_task: asyncio.Task | None = None

    @property
    def state(self) -> str:
        return self._connection_state

    @property
    def idle_seconds(self) -> float:
        if self._last_activity == 0:
            return 0
        return time.monotonic() - self._last_activity

    async def start(self) -> None:
        self._running = True
        if self.idle_timeout > 0:
            self._idle_task = asyncio.create_task(self._idle_monitor(), name="qb-idle-monitor")
        log.info("QBSessionManager started (idle_timeout=%ds)", self.idle_timeout)

    async def stop(self) -> None:
        self._running = False
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._idle_task
        await self._kill_worker()
        log.info("QBSessionManager stopped")

    async def execute(
        self,
        qbxml: str,
        timeout: float | None = None,
        *,
        idempotent: bool = False,
    ) -> str:
        """Send a qbXML request to the worker subprocess, retrying transient faults.

        ``timeout`` overrides the default ``request_timeout`` for this one call
        (reports pass the larger ``report_timeout``).

        ``idempotent`` says whether repeating this request is harmless — true
        for queries and reports, false for anything that writes. A write is
        still retried when QuickBooks provably never saw it (the session failed
        to open, the pipe broke before the send); it is *not* retried once the
        request was in flight, because QuickBooks may have applied it and a
        second attempt would duplicate the record.
        """
        if not self._running:
            raise QBConnectionError("QBSessionManager is not running")

        attempt = 1
        while True:
            try:
                return await self._execute_once(qbxml, timeout)
            except QBError as exc:
                reached_qb = getattr(exc, "_reached_qb", True)
                fault = classify(str(exc))

                # Unrecognized: repeating it would just fail the same way.
                if fault is None:
                    raise

                # Recognized, but the request may already have been applied and
                # the caller hasn't said it's safe to repeat. Report the real
                # error rather than inviting a retry that could duplicate data.
                if reached_qb and not idempotent:
                    log.warning(
                        "Not retrying a non-idempotent request after a %r fault: "
                        "QuickBooks may have applied it already (%s)",
                        fault.name,
                        exc,
                    )
                    raise

                if attempt >= self.max_attempts:
                    # A dialog we can't dismiss is the useful thing to say:
                    # "QuickBooks is waiting on a login prompt" beats "try again".
                    blocked_by = ""
                    if self._blocking_dialogs:
                        blocked_by = (
                            " QuickBooks is waiting on a dialog nobody here can answer: "
                            f"{', '.join(repr(t) for t in self._blocking_dialogs)}. "
                            "Someone needs to deal with it on the QuickBooks machine."
                        )
                    raise QBUnavailableError(
                        f"{exc} (still failing after {attempt} attempts; "
                        f"{fault.description}){blocked_by}",
                        fault=fault.name,
                        attempts=attempt,
                        retry_after=max(5, int(self.retry_backoff * 2)),
                    ) from exc

                self.retry_count += 1
                self.last_fault = fault.name
                log.warning(
                    "Attempt %d/%d failed with transient fault %r (%s) — applying remedy %r",
                    attempt,
                    self.max_attempts,
                    fault.name,
                    exc,
                    fault.remedy,
                )
                await self._apply_remedy(fault)
                await asyncio.sleep(self.retry_backoff * attempt)
                attempt += 1

    async def _apply_remedy(self, fault: TransientFault) -> None:
        """Do the thing that makes the next attempt worth making."""
        if fault.remedy == REMEDY_DISMISS_DIALOGS and self.on_blocked is not None:
            self._blocking_dialogs = await self.on_blocked() or []
        elif fault.remedy == REMEDY_LAUNCH_QB:
            from .process import is_qb_running, launch_qb

            if is_qb_running():
                return
            if not self.auto_launch_qb:
                log.warning(
                    "QuickBooks is not running and auto_launch_qb is off — enable it on "
                    "the Connection page (or set QBB_AUTO_LAUNCH_QB=true) to recover "
                    "from this automatically"
                )
                return
            log.info("Launching QuickBooks Desktop to recover from %r", fault.name)
            launch_qb(company_file=self.company_file or None, exe_path=self.qb_exe_path)
            await asyncio.sleep(8)
        elif fault.remedy == REMEDY_RESTART_WORKER:
            async with self._lock:
                await self._kill_worker()

    async def _execute_once(self, qbxml: str, timeout: float | None = None) -> str:
        """One attempt: hand the request to the worker and read its answer.

        Failures carry ``_reached_qb``: False means QuickBooks never saw the
        request, so even a write can safely be sent again.
        """
        effective_timeout = timeout if timeout is not None else self.request_timeout

        async with self._lock:
            self._last_activity = time.monotonic()

            # Ensure worker is alive
            if not self._proc or self._proc.poll() is not None:
                try:
                    await self._start_worker()
                except QBError as exc:
                    # Nothing was sent, so this is safe to retry for writes too.
                    exc._reached_qb = False
                    raise

            # Send request
            msg = json.dumps({"cmd": "execute", "qbxml": qbxml}) + "\n"
            try:
                self._proc.stdin.write(msg)
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._connection_state = "error"
                await self._kill_worker()
                error = QBConnectionError(f"Worker pipe broken: {exc}")
                error._reached_qb = False
                raise error from exc

            # Read response (with timeout)
            try:
                response_line = await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(None, self._proc.stdout.readline),
                    timeout=effective_timeout,
                )
            except TimeoutError as exc:
                self._connection_state = "error"
                await self._kill_worker()
                raise QBTimeoutError(
                    f"QuickBooks did not respond within {effective_timeout:.0f}s. The request is "
                    "likely too large — for reports, narrow the date range (from_date/to_date) "
                    "or filter by entity; for lists, lower max_returned or page with iterator_id."
                ) from exc

            if not response_line:
                self._connection_state = "error"
                await self._kill_worker()
                raise QBConnectionError("Worker process died")

            try:
                result = json.loads(response_line)
            except json.JSONDecodeError as exc:
                raise QBConnectionError(f"Invalid worker response: {response_line!r}") from exc

            if result.get("status") == "ok":
                self._connection_state = "connected"
                return result["response"]
            else:
                error_msg = result.get("message", "Unknown worker error")
                # Worker will retry connection on next request
                self._connection_state = "error"
                error = QBConnectionError(f"QB error: {error_msg}")
                # The worker tells us which phase failed: "connect" means the
                # session never opened, so QuickBooks never saw the request.
                error._reached_qb = result.get("phase") != "connect"
                raise error

    async def _idle_monitor(self) -> None:
        """Background task: disconnect QB after idle_timeout seconds of inactivity.

        Sends a 'disconnect' command to the worker (ending the QB session but
        keeping the subprocess alive for fast reconnect). If auto_close_qb is
        set, also closes the QB Desktop process.
        """
        check_interval = min(60, max(10, self.idle_timeout // 6))
        while self._running:
            await asyncio.sleep(check_interval)
            if not self._running:
                break

            if self._last_activity == 0:
                # No request has been made yet; nothing to time out.
                continue

            if self.idle_seconds < self.idle_timeout:
                continue

            # We've been idle long enough — kill the worker subprocess so
            # the OS reclaims all COM handles and releases the QB company
            # file lock.  The worker will be re-spawned on the next request.
            log.info(
                "QB session idle for %.0fs (threshold %ds), stopping worker",
                self.idle_seconds,
                self.idle_timeout,
            )
            async with self._lock:
                await self._kill_worker()
                self._last_activity = 0.0
                log.info("QB worker stopped due to idle timeout — company file released")

            if self.auto_close_qb:
                from .dialogs import DialogError, qb_has_visible_windows
                from .process import close_qb

                # The idle timer measures *our* inactivity, not a person's. A
                # QuickBooks with windows on screen is one somebody may be
                # working in, and closing it is a force-kill — so leave it be.
                try:
                    in_use = qb_has_visible_windows()
                except DialogError as exc:
                    log.warning("auto_close_qb: could not check for QB windows: %s", exc)
                    in_use = True

                if in_use:
                    log.info(
                        "auto_close_qb=True but QuickBooks has windows on screen — "
                        "leaving it alone, someone may be using it"
                    )
                else:
                    log.info("auto_close_qb=True and QuickBooks has no UI — closing it")
                    close_qb()

    async def _start_worker(self) -> None:
        """Launch the COM worker subprocess.

        Deliberately does *not* start QuickBooks itself. The SDK can start it
        headlessly when the app is authorized to log in automatically, and that
        path needs no password; starting the GUI first pre-empts it and, on a
        company file with a user password, parks QuickBooks on a login prompt
        that no amount of retrying can clear. So we let BeginSession try, and
        only fall back to launching the GUI as the ``qb-not-started`` remedy.
        """
        await self._kill_worker()

        # Always use python.exe (not pythonw.exe) for the worker — it needs
        # working stdin/stdout pipes for our JSON protocol
        python_exe = sys.executable.replace("pythonw.exe", "python.exe")
        cmd = [python_exe, WORKER_SCRIPT, self.company_file]

        log.info("Starting QB worker subprocess: %s", " ".join(cmd))
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line-buffered
            # python.exe is a console app: without this it pops up a console
            # window on the user's desktop, which someone will eventually close
            # (killing the worker). The pipes above still work fine.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        # Wait for "ready" message
        try:
            ready_line = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(None, self._proc.stdout.readline),
                timeout=30.0,
            )
            ready = json.loads(ready_line)
            if ready.get("status") != "ready":
                raise QBConnectionError(f"Worker failed to start: {ready}")
            log.info("QB worker subprocess ready (PID %d)", self._proc.pid)
            self._connection_state = "disconnected"
        except TimeoutError as exc:
            await self._kill_worker()
            raise QBConnectionError("Worker subprocess did not start within 30s") from exc
        except Exception as exc:
            stderr = ""
            if self._proc and self._proc.stderr:
                stderr = self._proc.stderr.read()
            await self._kill_worker()
            raise QBConnectionError(f"Worker startup failed: {stderr}") from exc

    async def _kill_worker(self) -> None:
        """Kill the worker subprocess if running."""
        if self._proc:
            try:
                self._proc.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
                self._proc.stdin.flush()
                self._proc.wait(timeout=5)
            except Exception:
                with contextlib.suppress(Exception):
                    self._proc.kill()
            self._proc = None
        self._connection_state = "disconnected"
