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
from pathlib import Path

from .exceptions import QBConnectionError, QBTimeoutError

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
    ) -> None:
        self.company_file = company_file
        self.idle_timeout = idle_timeout
        self.auto_launch_qb = auto_launch_qb
        self.qb_exe_path = qb_exe_path
        self.auto_close_qb = auto_close_qb
        self.request_timeout = request_timeout

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
            self._idle_task = asyncio.create_task(
                self._idle_monitor(), name="qb-idle-monitor"
            )
        log.info("QBSessionManager started (idle_timeout=%ds)", self.idle_timeout)

    async def stop(self) -> None:
        self._running = False
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._idle_task
        await self._kill_worker()
        log.info("QBSessionManager stopped")

    async def execute(self, qbxml: str) -> str:
        """Send a qbXML request to the worker subprocess."""
        if not self._running:
            raise QBConnectionError("QBSessionManager is not running")

        async with self._lock:
            self._last_activity = time.monotonic()

            # Ensure worker is alive
            if not self._proc or self._proc.poll() is not None:
                await self._start_worker()

            # Send request
            msg = json.dumps({"cmd": "execute", "qbxml": qbxml}) + "\n"
            try:
                self._proc.stdin.write(msg)
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._connection_state = "error"
                await self._kill_worker()
                raise QBConnectionError(f"Worker pipe broken: {exc}") from exc

            # Read response (with timeout)
            try:
                response_line = await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(None, self._proc.stdout.readline),
                    timeout=self.request_timeout,
                )
            except TimeoutError as exc:
                self._connection_state = "error"
                await self._kill_worker()
                raise QBTimeoutError(
                    f"Worker did not respond within {self.request_timeout}s"
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
                raise QBConnectionError(f"QB error: {error_msg}")

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
                from .process import close_qb

                log.info("auto_close_qb=True — closing QuickBooks Desktop")
                close_qb()

    async def _start_worker(self) -> None:
        """Launch the COM worker subprocess."""
        await self._kill_worker()

        # Auto-launch QB Desktop if configured and not running
        if self.auto_launch_qb:
            from .process import is_qb_running, launch_qb

            if not is_qb_running():
                log.info("QuickBooks is not running — launching now")
                launch_qb(
                    company_file=self.company_file or None,
                    exe_path=self.qb_exe_path,
                )
                # Give QB time to initialise before we try to connect
                await asyncio.sleep(8)

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
