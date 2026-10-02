"""Unit tests for QBSessionManager request handling — timeout behavior."""

import asyncio
import time

import pytest

from qb_bridge.qb.exceptions import QBTimeoutError
from qb_bridge.qb.session import QBSessionManager


class _FakeStdin:
    def write(self, _s):
        pass

    def flush(self):
        pass


class _HangingStdout:
    """readline() blocks longer than any test timeout, simulating a worker
    stuck inside a slow QuickBooks ProcessRequest call."""

    def readline(self):
        time.sleep(1.0)
        return ""


class _HangingProc:
    def __init__(self):
        self.stdin = _FakeStdin()
        self.stdout = _HangingStdout()
        self.stderr = None

    def poll(self):
        return None  # still alive

    def wait(self, timeout=None):
        raise RuntimeError("worker won't exit")

    def kill(self):
        pass


class TestExecuteTimeout:
    async def test_timeout_raises_helpful_message(self):
        """A hung worker must raise QBTimeoutError (not crash) with an
        actionable message, and honor the per-call timeout override."""
        mgr = QBSessionManager()
        mgr._running = True
        mgr._proc = _HangingProc()

        with pytest.raises(QBTimeoutError) as exc_info:
            await mgr.execute("<QBXML/>", timeout=0.2)

        message = str(exc_info.value)
        assert "QuickBooks did not respond within" in message
        assert "narrow the date range" in message
        # Worker is torn down so the next call re-spawns cleanly.
        assert mgr._proc is None


class _QuietProc:
    """A worker that quits when asked."""

    def __init__(self) -> None:
        self.stdin = _FakeStdin()
        self.quit = False

    def wait(self, timeout=None):
        self.quit = True
        return 0

    def kill(self) -> None:
        self.quit = True


class TestLettingGoOfQuickBooks:
    """An open session stops a person closing QuickBooks, so it isn't kept while they're in it."""

    def _manager(
        self, monkeypatch, *, someone: bool, idle: float
    ) -> tuple[QBSessionManager, _QuietProc]:
        mgr = QBSessionManager(idle_timeout=600, attended_idle_timeout=15)
        proc = _QuietProc()
        mgr._proc = proc
        mgr._last_activity = time.monotonic() - idle
        monkeypatch.setattr(mgr, "_someone_is_using_qb", lambda: someone)
        return mgr, proc

    async def test_released_within_seconds_when_someone_has_quickbooks_open(self, monkeypatch):
        mgr, proc = self._manager(monkeypatch, someone=True, idle=20)
        await mgr._idle_check()
        assert proc.quit and mgr._proc is None
        assert mgr.state == "disconnected"

    async def test_kept_while_requests_are_still_coming(self, monkeypatch):
        mgr, proc = self._manager(monkeypatch, someone=True, idle=5)
        await mgr._idle_check()
        assert not proc.quit and mgr._proc is proc

    async def test_a_quickbooks_the_bridge_started_is_kept_for_the_full_timeout(self, monkeypatch):
        mgr, proc = self._manager(monkeypatch, someone=False, idle=300)
        await mgr._idle_check()
        assert not proc.quit and mgr._proc is proc
        mgr._last_activity = time.monotonic() - 601
        await mgr._idle_check()
        assert proc.quit and mgr._proc is None

    async def test_a_request_that_arrives_while_waiting_keeps_the_session(self, monkeypatch):
        mgr, proc = self._manager(monkeypatch, someone=True, idle=20)
        async with mgr._lock:
            check = asyncio.ensure_future(mgr._idle_check())
            await asyncio.sleep(0)
            mgr._last_activity = time.monotonic()  # a request just ran
        await check
        assert not proc.quit and mgr._proc is proc

    async def test_someone_in_quickbooks_is_never_closed_out_of_it(self, monkeypatch):
        mgr, proc = self._manager(monkeypatch, someone=True, idle=20)
        mgr.auto_close_qb = True
        closed = []
        monkeypatch.setattr("qb_bridge.qb.process.close_qb", lambda *a, **k: closed.append(1))
        await mgr._idle_check()
        assert proc.quit and closed == []

    async def test_turned_off_it_waits_the_full_timeout(self, monkeypatch):
        mgr, proc = self._manager(monkeypatch, someone=True, idle=20)
        mgr.attended_idle_timeout = 0
        await mgr._idle_check()
        assert not proc.quit
