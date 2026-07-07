"""Unit tests for QBSessionManager request handling — timeout behavior."""

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
