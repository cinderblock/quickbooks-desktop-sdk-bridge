"""QuickBooks-specific exception hierarchy."""

from __future__ import annotations


class QBError(Exception):
    """Base exception for all QuickBooks errors."""

    def __init__(self, message: str, *, qb_status_code: int | None = None):
        super().__init__(message)
        self.qb_status_code = qb_status_code


class QBConnectionError(QBError):
    """Failed to open a connection to QuickBooks Desktop."""


class QBSessionError(QBError):
    """Failed to begin a session (company file not open, auth denied, etc.)."""


class QBRequestError(QBError):
    """QuickBooks returned an error status for a request."""

    def __init__(
        self,
        message: str,
        *,
        qb_status_code: int | None = None,
        severity: str = "Error",
    ):
        super().__init__(message, qb_status_code=qb_status_code)
        self.severity = severity


class QBNotRunningError(QBError):
    """QuickBooks Desktop process is not running and could not be launched."""


class QBTimeoutError(QBError):
    """A QuickBooks operation timed out."""
