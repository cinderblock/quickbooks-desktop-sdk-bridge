"""Recognizing which QuickBooks failures are worth retrying.

Retrying the wrong thing is worse than not retrying: a duplicated invoice is
far more expensive than a 502. So this works like the dialog rules — only
*explicitly recognized* faults are retried, and everything else (a qbXML parse
error, a stale EditSequence, a rejected value) fails immediately with the
message QuickBooks gave, because repeating it would only fail again.

Each recognized fault names a remedy the session applies before trying again;
the point of a retry is that something changed in between.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# What to do between attempts.
REMEDY_DISMISS_DIALOGS = "dismiss_dialogs"  # sweep QB's modal dialogs, then retry
REMEDY_LAUNCH_QB = "launch_qb"  # start QuickBooks if it isn't running
REMEDY_RESTART_WORKER = "restart_worker"  # respawn the COM subprocess
REMEDY_WAIT = "wait"  # QuickBooks is busy; just back off


@dataclass(frozen=True)
class TransientFault:
    """A QuickBooks failure that another attempt can plausibly get past."""

    name: str
    pattern: str
    remedy: str
    description: str

    def matches(self, message: str) -> bool:
        return re.search(self.pattern, message, re.IGNORECASE | re.DOTALL) is not None


TRANSIENT_FAULTS: tuple[TransientFault, ...] = (
    TransientFault(
        name="modal-dialog",
        pattern=r"modal dialog box is showing",
        remedy=REMEDY_DISMISS_DIALOGS,
        description=(
            "QuickBooks is blocked on one of its own dialogs. The dialog watcher "
            "clicks the recognized ones, so the next attempt usually gets through."
        ),
    ),
    TransientFault(
        name="qb-not-started",
        pattern=r"Could not start QuickBooks",
        remedy=REMEDY_LAUNCH_QB,
        description="QuickBooks Desktop is closed and the SDK could not start it.",
    ),
    TransientFault(
        name="worker-gone",
        pattern=r"Worker process died|Worker pipe broken|Worker subprocess did not start",
        remedy=REMEDY_RESTART_WORKER,
        description="The COM subprocess died; a fresh one reconnects.",
    ),
    TransientFault(
        name="qb-busy",
        pattern=r"call was rejected by callee"
        r"|application is busy"
        r"|RPC_E_(SERVERCALL_RETRYLATER|CALL_REJECTED)",
        remedy=REMEDY_WAIT,
        description=(
            "QuickBooks' COM server refused the call because it was mid-operation "
            "(a user clicking around in the UI, a file check running)."
        ),
    ),
)


def classify(message: str) -> TransientFault | None:
    """The recognized transient fault in *message*, or None if it isn't one."""
    for fault in TRANSIENT_FAULTS:
        if fault.matches(message):
            return fault
    return None
