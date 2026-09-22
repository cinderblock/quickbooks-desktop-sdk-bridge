"""Retrying transient QuickBooks faults — and refusing to retry the rest.

``QBSessionManager._execute_once`` is replaced with a scripted sequence of
outcomes, so these exercise the retry decision without a COM worker.
"""

from __future__ import annotations

import pytest

from qb_bridge.qb.exceptions import (
    QBConnectionError,
    QBRequestError,
    QBTimeoutError,
    QBUnavailableError,
)
from qb_bridge.qb.retry import REMEDY_DISMISS_DIALOGS, TRANSIENT_FAULTS, classify
from qb_bridge.qb.session import QBSessionManager

DIALOG_ERROR = (
    "QB error: BeginSession failed: A modal dialog box is showing in the QuickBooks "
    "user interface. Your application cannot access QuickBooks until the user "
    "dismisses the dialog box."
)
QB_CLOSED_ERROR = "QB error: BeginSession failed: Could not start QuickBooks."
PARSE_ERROR = (
    "QB error: ProcessRequest COM error: QuickBooks found an error when parsing the "
    "provided XML text stream."
)


def make_session(outcomes, **kwargs) -> QBSessionManager:
    """A session whose attempts return/raise *outcomes* in order.

    Each outcome is either a string (the response) or an exception to raise.
    """
    session = QBSessionManager(retry_backoff=0.0, **kwargs)
    session._running = True
    session.attempts: list[str] = []

    async def fake_execute_once(qbxml, timeout=None):
        session.attempts.append(qbxml)
        outcome = outcomes[min(len(session.attempts) - 1, len(outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    session._execute_once = fake_execute_once
    return session


def not_sent(exc: Exception) -> Exception:
    """Mark an error as having happened before QuickBooks saw the request."""
    exc._reached_qb = False
    return exc


class TestClassification:
    def test_known_faults_are_recognized(self):
        assert classify(DIALOG_ERROR).name == "modal-dialog"
        assert classify(QB_CLOSED_ERROR).name == "qb-not-started"
        assert classify("Worker process died").name == "worker-gone"

    def test_request_errors_are_not_transient(self):
        """A malformed request is our bug — retrying just fails identically."""
        assert classify(PARSE_ERROR) is None
        assert classify('The provided edit sequence "1" is out-of-date.') is None
        assert classify('The iteratorID "abc" is not valid.') is None

    def test_every_fault_pattern_compiles(self):
        assert TRANSIENT_FAULTS
        for fault in TRANSIENT_FAULTS:
            assert fault.matches("") is False
            assert fault.remedy
            assert fault.description


class TestRetryDecision:
    async def test_transient_fault_is_retried_and_succeeds(self):
        session = make_session([QBConnectionError(DIALOG_ERROR), "<ok/>"])
        assert await session.execute("<q/>", idempotent=True) == "<ok/>"
        assert len(session.attempts) == 2
        assert session.retry_count == 1
        assert session.last_fault == "modal-dialog"

    async def test_unrecognized_error_is_not_retried(self):
        session = make_session([QBConnectionError(PARSE_ERROR), "<ok/>"])
        with pytest.raises(QBConnectionError, match="parsing"):
            await session.execute("<q/>", idempotent=True)
        assert len(session.attempts) == 1

    async def test_retries_are_bounded_and_end_as_unavailable(self):
        session = make_session([QBConnectionError(QB_CLOSED_ERROR)], max_attempts=3)
        with pytest.raises(QBUnavailableError) as excinfo:
            await session.execute("<q/>", idempotent=True)
        assert len(session.attempts) == 3
        assert excinfo.value.fault == "qb-not-started"
        assert excinfo.value.attempts == 3
        assert excinfo.value.retry_after >= 5

    async def test_write_is_not_retried_once_quickbooks_saw_it(self):
        """The whole point: a maybe-applied invoice must not be sent twice."""
        session = make_session([QBConnectionError(DIALOG_ERROR), "<ok/>"])
        with pytest.raises(QBConnectionError):
            await session.execute("<add/>", idempotent=False)
        assert len(session.attempts) == 1, "a write that reached QB must not be repeated"

    async def test_write_is_retried_when_quickbooks_never_saw_it(self):
        """A failure to even open the session is provably safe to repeat."""
        session = make_session([not_sent(QBConnectionError(DIALOG_ERROR)), "<ok/>"])
        assert await session.execute("<add/>", idempotent=False) == "<ok/>"
        assert len(session.attempts) == 2

    async def test_timeout_is_not_retried(self):
        """A 60s timeout usually means too-large request, not a blocked QB."""
        session = make_session([QBTimeoutError("QuickBooks did not respond within 60s."), "<ok/>"])
        with pytest.raises(QBTimeoutError):
            await session.execute("<q/>", idempotent=True)
        assert len(session.attempts) == 1

    async def test_qb_request_errors_pass_straight_through(self):
        session = make_session([QBRequestError("The specified record does not exist")])
        with pytest.raises(QBRequestError):
            await session.execute("<q/>", idempotent=True)
        assert len(session.attempts) == 1

    async def test_max_attempts_one_disables_retrying(self):
        session = make_session([QBConnectionError(DIALOG_ERROR), "<ok/>"], max_attempts=1)
        with pytest.raises(QBUnavailableError):
            await session.execute("<q/>", idempotent=True)
        assert len(session.attempts) == 1


class TestRemedies:
    async def test_dialog_fault_sweeps_dialogs_before_retrying(self):
        """The retry only makes sense because the dialog got clicked first."""
        swept = []
        session = make_session([QBConnectionError(DIALOG_ERROR), "<ok/>"])

        async def sweep():
            swept.append(len(session.attempts))

        session.on_blocked = sweep
        await session.execute("<q/>", idempotent=True)

        assert swept == [1], "should sweep after the failed attempt, before the retry"

    async def test_dialog_fault_still_retries_without_a_sweep_hook(self):
        session = make_session([QBConnectionError(DIALOG_ERROR), "<ok/>"])
        session.on_blocked = None
        assert await session.execute("<q/>", idempotent=True) == "<ok/>"

    async def test_closed_quickbooks_is_relaunched(self, monkeypatch):
        launched = []
        monkeypatch.setattr("qb_bridge.qb.process.is_qb_running", lambda: False)
        monkeypatch.setattr(
            "qb_bridge.qb.process.launch_qb",
            lambda company_file=None, exe_path="", **kw: launched.append(exe_path) or True,
        )
        monkeypatch.setattr("asyncio.sleep", _no_sleep)

        session = make_session(
            [QBConnectionError(QB_CLOSED_ERROR), "<ok/>"],
            auto_launch_qb=True,
            qb_exe_path="C:/QB/QBW32Pro.exe",
        )
        assert await session.execute("<q/>", idempotent=True) == "<ok/>"
        assert launched == ["C:/QB/QBW32Pro.exe"]

    async def test_relaunch_is_skipped_when_auto_launch_is_off(self, monkeypatch):
        launched = []
        monkeypatch.setattr("qb_bridge.qb.process.is_qb_running", lambda: False)
        monkeypatch.setattr(
            "qb_bridge.qb.process.launch_qb",
            lambda **kw: launched.append(kw) or True,
        )

        session = make_session(
            [QBConnectionError(QB_CLOSED_ERROR), "<ok/>"],
            auto_launch_qb=False,
        )
        assert await session.execute("<q/>", idempotent=True) == "<ok/>"
        assert launched == [], "must not start QuickBooks when the operator said not to"

    def test_dialog_remedy_is_the_sweep(self):
        assert classify(DIALOG_ERROR).remedy == REMEDY_DISMISS_DIALOGS


async def _no_sleep(_seconds):
    return None


class TestRouteRetrySafety:
    """The routes must classify themselves correctly — retry safety depends on it."""

    async def test_list_is_marked_safe_to_repeat(self, client, fake_qb_session):
        r = await client.get("/api/v1/customers", params={"max_returned": 5})
        assert r.status_code == 200, r.text
        assert fake_qb_session.last_idempotent is True

    async def test_get_by_id_is_marked_safe_to_repeat(self, client, fake_qb_session):
        await client.get("/api/v1/customers/80000001-1611594109")
        assert fake_qb_session.last_idempotent is True

    async def test_company_query_is_marked_safe_to_repeat(self, client, fake_qb_session):
        await client.get("/api/v1/company")
        assert fake_qb_session.last_idempotent is True

    async def test_create_is_not_marked_safe_to_repeat(self, client, fake_qb_session):
        await client.post("/api/v1/customers", json={"Name": "Acme"})
        assert fake_qb_session.last_idempotent is False, "a create must never be auto-repeated"

    async def test_delete_is_not_marked_safe_to_repeat(self, client, fake_qb_session):
        await client.delete("/api/v1/customers/80000001-1611594109")
        assert fake_qb_session.last_idempotent is False

    async def test_exhausted_transient_fault_is_a_503_with_retry_after(
        self, client, fake_qb_session
    ):
        """Callers should back off and retry, not rewrite the request."""

        async def unavailable(qbxml, timeout=None, *, idempotent=False):
            raise QBUnavailableError(
                "QuickBooks is blocked on a dialog",
                fault="modal-dialog",
                attempts=3,
                retry_after=10,
            )

        fake_qb_session.execute = unavailable

        r = await client.get("/api/v1/customers", params={"max_returned": 5})

        assert r.status_code == 503
        assert r.headers["Retry-After"] == "10"
        body = r.json()
        assert body["error"]["code"] == "QB_UNAVAILABLE"
        assert body["error"]["fault"] == "modal-dialog"
        assert body["error"]["attempts"] == 3


class TestStartupOrder:
    async def test_worker_start_does_not_preempt_the_sdk_by_launching_the_gui(self, monkeypatch):
        """Launching the QB GUI first parks it on a login prompt nothing can clear.

        The SDK can start QuickBooks headlessly when the app is authorized to
        log in automatically, so BeginSession must get first refusal.
        """
        launched = []
        monkeypatch.setattr("qb_bridge.qb.process.is_qb_running", lambda: False)
        monkeypatch.setattr(
            "qb_bridge.qb.process.launch_qb",
            lambda **kw: launched.append(kw) or True,
        )

        def no_spawn(*args, **kwargs):
            raise OSError("subprocess spawning is stubbed out in tests")

        monkeypatch.setattr("qb_bridge.qb.session.subprocess.Popen", no_spawn)

        session = QBSessionManager(auto_launch_qb=True)
        with pytest.raises(OSError, match="stubbed out"):
            await session._start_worker()

        assert launched == [], "starting the worker must not launch the QuickBooks GUI"
