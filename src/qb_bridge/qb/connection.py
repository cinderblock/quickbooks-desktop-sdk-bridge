"""Low-level COM wrapper for QBXMLRP2.RequestProcessor.

All methods in this module MUST be called from the same thread that
called ``connect()``, with ``pythoncom.CoInitialize()`` already done.
"""

from __future__ import annotations

import logging

import pythoncom
import win32com.client

from .exceptions import QBConnectionError, QBRequestError, QBSessionError

log = logging.getLogger(__name__)

APP_ID = "QBBridge"
APP_NAME = "QuickBooks Bridge API"

# qbFileOpenDoNotCare — use whatever company file QB has open
QB_FILE_MODE_DO_NOT_CARE = 2


class QBConnection:
    """Manages a single COM connection + session to QuickBooks Desktop."""

    def __init__(self) -> None:
        self._rp: win32com.client.CDispatch | None = None
        self._ticket: str | None = None
        self._connected: bool = False
        self._session_open: bool = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def session_open(self) -> bool:
        return self._session_open

    def connect(self, company_file: str = "") -> None:
        """Create COM object, open connection, and begin session.

        Args:
            company_file: Path to .qbw file, or "" for whatever QB has open.
        """
        if self._session_open:
            return

        try:
            log.debug("Dispatching QBXMLRP2.RequestProcessor")
            self._rp = win32com.client.Dispatch("QBXMLRP2.RequestProcessor")
        except pythoncom.com_error as exc:
            raise QBConnectionError(
                f"Could not create QBXMLRP2 COM object: {exc}"
            ) from exc

        try:
            log.debug("OpenConnection2")
            self._rp.OpenConnection2(APP_ID, APP_NAME, 1)  # 1 = localQBD
            self._connected = True
        except pythoncom.com_error as exc:
            raise QBConnectionError(
                f"OpenConnection2 failed: {_com_error_desc(exc)}"
            ) from exc

        try:
            log.debug("BeginSession (company_file=%r)", company_file)
            self._ticket = self._rp.BeginSession(company_file, QB_FILE_MODE_DO_NOT_CARE)
            self._session_open = True
            log.info("QB session opened, ticket=%s", self._ticket)
        except pythoncom.com_error as exc:
            desc = _com_error_desc(exc)
            self._close_connection()
            raise QBSessionError(f"BeginSession failed: {desc}") from exc

    def process_request(self, qbxml: str) -> str:
        """Send a qbXML request and return the raw XML response.

        Raises QBRequestError if QB returns an error-level status.
        """
        if not self._session_open or self._rp is None or self._ticket is None:
            raise QBSessionError("No active QB session")

        try:
            response: str = self._rp.ProcessRequest(self._ticket, qbxml)
        except pythoncom.com_error as exc:
            raise QBRequestError(
                f"ProcessRequest COM error: {_com_error_desc(exc)}"
            ) from exc

        return response

    def disconnect(self) -> None:
        """End session and close connection. Safe to call multiple times."""
        self._end_session()
        self._close_connection()

    def _end_session(self) -> None:
        if self._session_open and self._rp is not None and self._ticket is not None:
            try:
                self._rp.EndSession(self._ticket)
                log.debug("EndSession OK")
            except Exception as exc:
                log.warning("EndSession failed: %s", exc)
            finally:
                self._session_open = False
                self._ticket = None

    def _close_connection(self) -> None:
        if self._connected and self._rp is not None:
            try:
                self._rp.CloseConnection()
                log.debug("CloseConnection OK")
            except Exception as exc:
                log.warning("CloseConnection failed: %s", exc)
            finally:
                self._connected = False
                self._rp = None


def _com_error_desc(exc: pythoncom.com_error) -> str:
    """Extract a human-readable description from a COM error."""
    if hasattr(exc, "args") and len(exc.args) >= 3 and exc.args[2]:
        try:
            return str(exc.args[2][2])
        except (IndexError, TypeError):
            pass
    return str(exc)
