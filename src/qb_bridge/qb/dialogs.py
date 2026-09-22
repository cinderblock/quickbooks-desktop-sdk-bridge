"""Detect and auto-dismiss QuickBooks Desktop modal dialogs.

QuickBooks Desktop puts up modal dialogs on its own schedule: a scheduled
backup that failed, an update reminder, an informational message.  While one is
open the COM request processor stops answering, so every bridge request hangs
until a human walks over to that machine and clicks OK.

This module enumerates QuickBooks' dialog windows, matches them against a list
of *explicitly recognized* dialogs, and clicks the button the matching rule
names.  A dialog with no matching rule is **never** clicked — it may be asking
a question only a person should answer — but it is logged as a warning and
listed by ``GET /api/v1/dialogs`` so a rule can be written for it.

Rules live in :data:`BUILTIN_RULES`; more can be added at runtime through a
JSON file (see :func:`load_rules`) without editing code.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import sys
import time
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Process image names (lower-cased, extension stripped) whose windows we look
# at.  QuickBooks 2021 runs as QBW32Pro.exe -> "qbw32pro"; the updater is
# qbupdate.exe.  Anything else on the desktop is none of our business.
#
# Deliberately not a bare "qbw" prefix: that also matches qbwebconnector, whose
# window would then count as a QuickBooks dialog blocking the company file.
QB_PROCESS_PREFIXES: tuple[str, ...] = ("qbw32", "qbupdate", "quickbooks")

# Win32 constants
_BM_CLICK = 0x00F5
_WM_COMMAND = 0x0111
_WM_GETTEXT = 0x000D
_WM_GETTEXTLENGTH = 0x000E
_BN_CLICKED = 0
_GW_OWNER = 4
_SMTO_ABORTIFHUNG = 0x0002
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ACCESS_DENIED = 5

# Classes whose text is chrome, not message body
_NON_BODY_CLASSES = frozenset({"ScrollBar", "ComboBox", "ListBox", "SysHeader32"})


def _is_button(window_class: str) -> bool:
    """True for anything that behaves like a push button.

    QuickBooks draws most of its own dialogs with its "Maui" toolkit, whose
    buttons are ``MauiPushButton`` rather than the Win32 ``Button`` class —
    without this, those dialogs come back with no buttons at all and no rule
    could ever dismiss them.
    """
    lowered = window_class.lower()
    return lowered == "button" or lowered.endswith("pushbutton")


class DialogError(Exception):
    """Raised when dialog inspection or dismissal cannot be performed."""


class DialogRuleError(DialogError):
    """Raised when a dialog rule is malformed."""


# ---------------------------------------------------------------------------
# Window model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DialogButton:
    """A clickable button on a QuickBooks dialog."""

    hwnd: int
    control_id: int
    label: str

    def as_dict(self) -> dict:
        return {"label": self.label, "control_id": self.control_id, "hwnd": self.hwnd}


@dataclass(frozen=True)
class Dialog:
    """A QuickBooks dialog window and the text/buttons it is showing."""

    hwnd: int
    pid: int
    process: str
    window_class: str
    title: str
    text: str
    buttons: tuple[DialogButton, ...] = ()

    def button(self, label: str) -> DialogButton | None:
        """Find a button by exact (case-insensitive) label."""
        wanted = label.strip().casefold()
        for btn in self.buttons:
            if btn.label.casefold() == wanted:
                return btn
        return None

    def as_dict(self) -> dict:
        return {
            "hwnd": self.hwnd,
            "pid": self.pid,
            "process": self.process,
            "window_class": self.window_class,
            "title": self.title,
            "text": self.text,
            "buttons": [b.as_dict() for b in self.buttons],
        }


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


@dataclass
class DialogRule:
    """A recognized dialog and the button to click on it.

    ``title``/``body``/``button`` are case-insensitive regular expressions
    searched against the window title, the dialog's static text, and each
    button's label.  ``body`` is optional; when given it must also match, which
    is how two dialogs sharing a title are told apart.
    """

    name: str
    title: str
    button: str
    body: str | None = None
    description: str = ""
    enabled: bool = True

    _title_re: re.Pattern = field(init=False, repr=False, compare=False)
    _body_re: re.Pattern | None = field(init=False, repr=False, compare=False)
    _button_re: re.Pattern = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.name:
            raise DialogRuleError("dialog rule needs a name")
        self._title_re = self._compile("title", self.title)
        self._button_re = self._compile("button", self.button)
        self._body_re = self._compile("body", self.body) if self.body else None

    def _compile(self, field_name: str, pattern: str) -> re.Pattern:
        if not isinstance(pattern, str):
            raise DialogRuleError(
                f"dialog rule {self.name!r}: {field_name} must be a string, "
                f"got {type(pattern).__name__}"
            )
        try:
            return re.compile(pattern, re.IGNORECASE | re.DOTALL)
        except re.error as exc:
            raise DialogRuleError(
                f"dialog rule {self.name!r}: {field_name} is not a valid regex ({pattern!r}): {exc}"
            ) from exc

    def match(self, dialog: Dialog) -> DialogButton | None:
        """Return the button this rule wants clicked, or None if it doesn't apply."""
        if not self.enabled:
            return None
        if not self._title_re.search(dialog.title):
            return None
        if self._body_re is not None and not self._body_re.search(dialog.text):
            return None
        for btn in dialog.buttons:
            if self._button_re.search(btn.label):
                return btn
        return None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "body": self.body,
            "button": self.button,
            "description": self.description,
            "enabled": self.enabled,
        }


# Dialogs we are confident are safe to click unattended.  Every one of these
# either reports something that already happened or postpones an optional
# action; none of them touches company data.
BUILTIN_RULES: tuple[DialogRule, ...] = (
    DialogRule(
        name="backup-failed",
        title=r"^QuickBooks Backup$",
        body=r"error\(s\) occurred while attempting to backup"
        r"|problem attempting to backup"
        r"|Drive specified could not be accessed",
        button=r"^OK$",
        description=(
            "Scheduled backup failed (typically an unreachable backup drive). "
            "The error is already in QBBackup.log; the dialog only blocks QB."
        ),
    ),
    DialogRule(
        name="backup-succeeded",
        title=r"^QuickBooks (Backup|Information)$",
        body=r"backup.{0,40}(completed|was successful|successfully)",
        button=r"^OK$",
        description="Scheduled backup finished notification.",
    ),
    DialogRule(
        name="update-available",
        title=r"QuickBooks (Desktop )?Update",
        body=r"update.{0,60}(available|ready to install)|install.{0,30}update",
        button=r"^(Install Later|Later|Remind me later|No Thanks)$",
        description="Update reminder — postpone it rather than patching QB mid-request.",
    ),
)


# Dialogs that are blocking QuickBooks and that *only a person* can answer.
# These are never clicked — the point is to say what is wrong and what fixes it,
# instead of reporting a generic "unrecognized dialog".
NEEDS_HUMAN: tuple[tuple[str, str], ...] = (
    (
        r"^QuickBooks (Desktop )?Login$|Enter the password|You must log in",
        "QuickBooks wants a user password. Log in on the QuickBooks machine, or — so "
        "this stops happening — authorize the bridge to log in by itself: in QuickBooks, "
        "as Admin in single-user mode, Edit > Preferences > Integrated Applications > "
        "Company Preferences > 'QuickBooks Bridge API' > Properties > Access Rights, "
        "tick 'Allow this application to log in automatically' and pick a QuickBooks user.",
    ),
    (
        r"^No Company Open$|Open a Company",
        "QuickBooks is running with no company file open. Open the company file on the "
        "QuickBooks machine.",
    ),
)


def needs_human(dialog: Dialog) -> str | None:
    """Why a dialog needs a person, if it is one of the known blockers."""
    for pattern, explanation in NEEDS_HUMAN:
        if re.search(pattern, dialog.title, re.IGNORECASE):
            return explanation
    return None


_ALLOWED_RULE_KEYS = {"name", "title", "button", "body", "description", "enabled"}


def rules_from_data(data: object, *, source: str) -> list[DialogRule]:
    """Build rules from parsed JSON. Raises DialogRuleError on anything unexpected."""
    if not isinstance(data, list):
        raise DialogRuleError(
            f"{source}: expected a JSON array of rules, got {type(data).__name__}"
        )

    rules: list[DialogRule] = []
    for index, entry in enumerate(data):
        where = f"{source}[{index}]"
        if not isinstance(entry, dict):
            raise DialogRuleError(f"{where}: expected an object, got {type(entry).__name__}")
        unknown = set(entry) - _ALLOWED_RULE_KEYS
        if unknown:
            raise DialogRuleError(
                f"{where}: unknown field(s) {sorted(unknown)}; "
                f"allowed fields are {sorted(_ALLOWED_RULE_KEYS)}"
            )
        missing = {"name", "title", "button"} - set(entry)
        if missing:
            raise DialogRuleError(f"{where}: missing required field(s) {sorted(missing)}")
        rules.append(DialogRule(**entry))

    names = [r.name for r in rules]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise DialogRuleError(f"{source}: duplicate rule name(s) {sorted(duplicates)}")
    return rules


def load_rules(path: Path | None) -> list[DialogRule]:
    """Return the built-in rules plus any defined in *path*.

    A rule in the file that reuses a built-in name replaces it, which is how
    you retarget or disable a built-in (``{"name": ..., "enabled": false}``).
    A missing file is fine — it just means "built-ins only" — but a file that
    exists and is malformed raises rather than being skipped, so a typo can't
    silently leave QuickBooks blocked on a dialog you thought was handled.
    """
    rules = list(BUILTIN_RULES)
    if path is None or not path.exists():
        return rules

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DialogRuleError(f"{path}: could not read dialog rules: {exc}") from exc

    custom = rules_from_data(raw, source=str(path))
    by_name = {r.name: r for r in rules}
    for rule in custom:
        by_name[rule.name] = rule
    log.info("Loaded %d custom dialog rule(s) from %s", len(custom), path)
    return list(by_name.values())


# ---------------------------------------------------------------------------
# Win32 window enumeration
# ---------------------------------------------------------------------------


def _win32():
    """Return (user32, kernel32) with argtypes set, or raise on non-Windows."""
    if sys.platform != "win32":
        raise DialogError("QuickBooks dialog handling requires Windows")

    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # HWND/LPARAM are pointer-sized; without explicit argtypes ctypes passes
    # them as 32-bit ints and calls can silently target the wrong window.
    user32.IsWindow.argtypes = [wt.HWND]
    user32.IsWindowVisible.argtypes = [wt.HWND]
    user32.IsWindowEnabled.argtypes = [wt.HWND]
    user32.GetWindowTextLengthW.argtypes = [wt.HWND]
    user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    user32.GetDlgCtrlID.argtypes = [wt.HWND]
    user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
    user32.GetWindow.restype = wt.HWND
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.SendMessageTimeoutW.argtypes = [
        wt.HWND,
        wt.UINT,
        wt.WPARAM,
        wt.LPARAM,
        wt.UINT,
        wt.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wt.HANDLE,
        wt.DWORD,
        wt.LPWSTR,
        ctypes.POINTER(wt.DWORD),
    ]
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    return user32, kernel32


def _window_text(user32, hwnd: int) -> str:
    """Read a window's text, falling back to WM_GETTEXT for foreign controls."""
    import ctypes

    length = user32.GetWindowTextLengthW(hwnd)
    if length > 0:
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value:
            return buf.value

    result = ctypes.c_size_t()
    if not user32.SendMessageTimeoutW(
        hwnd, _WM_GETTEXTLENGTH, 0, 0, _SMTO_ABORTIFHUNG, 500, ctypes.byref(result)
    ):
        return ""
    length = int(result.value)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    if not user32.SendMessageTimeoutW(
        hwnd,
        _WM_GETTEXT,
        length + 1,
        ctypes.cast(buf, ctypes.c_void_p).value,
        _SMTO_ABORTIFHUNG,
        1000,
        ctypes.byref(result),
    ):
        return ""
    return buf.value


def _window_class(user32, hwnd: int) -> str:
    import ctypes

    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _process_name(kernel32, pid: int) -> str:
    """Image name of *pid* without directory or extension, lower-cased."""
    import ctypes
    import ctypes.wintypes as wt

    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wt.DWORD(512)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return Path(buf.value).stem.lower()
    finally:
        kernel32.CloseHandle(handle)


def find_dialogs(process_prefixes: Sequence[str] = QB_PROCESS_PREFIXES) -> list[Dialog]:
    """Enumerate QuickBooks' visible dialog windows.

    A window counts as a dialog if it belongs to a QuickBooks process and is
    either the standard dialog class (``#32770``) or owned by another window —
    which skips the QuickBooks main window while catching its popups.
    """
    import ctypes
    import ctypes.wintypes as wt

    user32, kernel32 = _win32()
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    pid_names: dict[int, str] = {}
    dialogs: list[Dialog] = []

    def on_window(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True

        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pid_names:
            pid_names[pid.value] = _process_name(kernel32, pid.value)
        process = pid_names[pid.value]
        if not any(process.startswith(prefix) for prefix in process_prefixes):
            return True

        window_class = _window_class(user32, hwnd)
        owner = user32.GetWindow(hwnd, _GW_OWNER)
        if window_class != "#32770" and not owner:
            return True  # the QuickBooks main window, not a dialog

        body_parts: list[str] = []
        buttons: list[DialogButton] = []

        def on_child(child, _lp):
            child_class = _window_class(user32, child)
            text = _window_text(user32, child).strip()
            if _is_button(child_class):
                if text and user32.IsWindowVisible(child) and user32.IsWindowEnabled(child):
                    buttons.append(
                        DialogButton(
                            hwnd=int(child),
                            control_id=user32.GetDlgCtrlID(child),
                            label=text.replace("&", ""),
                        )
                    )
            elif text and child_class not in _NON_BODY_CLASSES:
                body_parts.append(text)
            return True

        user32.EnumChildWindows(hwnd, enum_proc(on_child), 0)

        dialogs.append(
            Dialog(
                hwnd=int(hwnd),
                pid=pid.value,
                process=process,
                window_class=window_class,
                title=_window_text(user32, hwnd).strip(),
                text="\n".join(body_parts),
                buttons=tuple(buttons),
            )
        )
        return True

    if not user32.EnumWindows(enum_proc(on_window), 0):
        err = ctypes.get_last_error()
        # EnumWindows also returns 0 when a callback stops the enumeration; we
        # never stop early, so only a real error code means something broke.
        if err:
            raise DialogError(f"EnumWindows failed (error {err})")
    return dialogs


def is_elevated() -> bool:
    """True if this process is running with administrator rights."""
    if sys.platform != "win32":
        return False
    import ctypes

    return bool(ctypes.windll.shell32.IsUserAnAdmin())


_UIPI_HINT = (
    "Windows refused the click (access denied). QuickBooks is running at a higher "
    "integrity level than the bridge — run the bridge elevated (the QBBridge scheduled "
    "task uses RunLevel HighestAvailable, so install it with install_task.py from an "
    "Administrator prompt) or start QuickBooks without 'Run as administrator'."
)


def qb_has_visible_windows(process_prefixes: Sequence[str] = QB_PROCESS_PREFIXES) -> bool:
    """True if QuickBooks is showing anything on screen.

    A QuickBooks the SDK started for us runs with no UI at all, while one a
    person is working in has a main window. That difference is what makes it
    safe to close an idle QuickBooks automatically: no windows, nobody there.
    """
    import ctypes
    import ctypes.wintypes as wt

    user32, kernel32 = _win32()
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    pid_names: dict[int, str] = {}
    found = False

    def on_window(hwnd, _lparam):
        nonlocal found
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pid_names:
            pid_names[pid.value] = _process_name(kernel32, pid.value)
        process = pid_names[pid.value]
        if any(process.startswith(prefix) for prefix in process_prefixes):
            found = True
            return False  # stop enumerating
        return True

    user32.EnumWindows(enum_proc(on_window), 0)
    return found


def click_button(dialog: Dialog, button: DialogButton, timeout: float = 5.0) -> bool:
    """Click *button* and wait for *dialog* to close. Returns True if it closed.

    Raises :class:`DialogError` if Windows rejected the click outright, which
    is a configuration problem (see :data:`_UIPI_HINT`) rather than a stubborn
    dialog, and needs to be reported as such.
    """
    import ctypes

    user32, _ = _win32()

    ctypes.set_last_error(0)
    posted = user32.PostMessageW(button.hwnd, _BM_CLICK, 0, 0)
    post_error = ctypes.get_last_error() if not posted else 0
    if posted and _wait_closed(user32, dialog.hwnd, timeout):
        return True

    # Some QuickBooks dialogs ignore BM_CLICK on the control itself and only
    # act on the notification their own window procedure expects.
    result = ctypes.c_size_t()
    ctypes.set_last_error(0)
    sent = user32.SendMessageTimeoutW(
        dialog.hwnd,
        _WM_COMMAND,
        (_BN_CLICKED << 16) | (button.control_id & 0xFFFF),
        button.hwnd,
        _SMTO_ABORTIFHUNG,
        2000,
        ctypes.byref(result),
    )
    send_error = ctypes.get_last_error() if not sent else 0

    if not posted and not sent:
        if ERROR_ACCESS_DENIED in (post_error, send_error):
            raise DialogError(_UIPI_HINT)
        raise DialogError(
            f"Could not deliver a click to {dialog.title!r}: "
            f"PostMessage error {post_error}, SendMessageTimeout error {send_error}"
        )

    return _wait_closed(user32, dialog.hwnd, timeout)


def _wait_closed(user32, hwnd: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------


@dataclass
class DialogEvent:
    """Something the watcher did — or refused to do — about a dialog."""

    timestamp: float
    action: str  # "dismissed" | "unrecognized" | "needs_human" | "failed"
    title: str
    text: str
    rule: str | None = None
    button: str | None = None
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp)),
            "action": self.action,
            "title": self.title,
            "text": self.text,
            "rule": self.rule,
            "button": self.button,
            "detail": self.detail,
        }


def _signature(dialog: Dialog) -> str:
    return f"{dialog.title}\x00{dialog.text}"


class DialogWatcher:
    """Polls for QuickBooks dialogs and clicks the ones a rule recognizes."""

    def __init__(
        self,
        rules: Iterable[DialogRule] | None = None,
        *,
        enabled: bool = True,
        poll_interval: float = 5.0,
        history: int = 100,
        unrecognized_warn_interval: float = 300.0,
        failed_retry_interval: float = 60.0,
    ) -> None:
        self.rules: list[DialogRule] = list(rules) if rules is not None else list(BUILTIN_RULES)
        self.enabled = enabled
        self.poll_interval = poll_interval
        self.unrecognized_warn_interval = unrecognized_warn_interval
        self.failed_retry_interval = failed_retry_interval

        self.events: deque[DialogEvent] = deque(maxlen=history)
        self.rules_error: str | None = None
        self.dismissed_count = 0
        self.last_sweep: float | None = None
        self.last_error: str | None = None
        self.open_dialogs: list[Dialog] = []

        self._warned: dict[str, float] = {}
        self._failed: dict[str, float] = {}
        self._task: asyncio.Task | None = None
        self._running = False

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if not self.enabled:
            log.info("QB dialog watcher disabled (QBB_DIALOG_WATCH=false)")
            return
        if sys.platform != "win32":
            log.error("QB dialog watcher needs Windows — not starting on %s", sys.platform)
            self.enabled = False
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="qb-dialog-watcher")
        log.info(
            "QB dialog watcher started (%d rules, polling every %.0fs)",
            len(self.rules),
            self.poll_interval,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    async def _loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.poll_interval)
            if not self._running:
                break
            try:
                await asyncio.to_thread(self.sweep)
            except Exception as exc:
                # A failed sweep must not kill the watcher quietly: record it,
                # surface it on /api/v1/dialogs, and keep polling.
                self.last_error = str(exc)
                log.error("QB dialog sweep failed: %s", exc, exc_info=True)

    # -- one pass ----------------------------------------------------------

    def match(self, dialog: Dialog) -> tuple[DialogRule, DialogButton] | None:
        """First rule that recognizes *dialog*, with the button it wants clicked."""
        for rule in self.rules:
            button = rule.match(dialog)
            if button is not None:
                return rule, button
        return None

    def sweep(self) -> list[DialogEvent]:
        """Scan once: dismiss recognized dialogs, warn about the rest."""
        dialogs = find_dialogs()
        self.last_sweep = time.monotonic()
        self.last_error = None
        self.open_dialogs = dialogs

        now = time.monotonic()
        new_events: list[DialogEvent] = []
        for dialog in dialogs:
            signature = _signature(dialog)
            matched = self.match(dialog)
            if matched is None:
                event = self._note_unrecognized(dialog)
            else:
                # A dialog that refused to close (or that Windows won't let us
                # click at all) would otherwise be retried — and logged as an
                # error — on every poll. Back off, but keep trying.
                last_failure = self._failed.get(signature)
                if last_failure is not None and now - last_failure < self.failed_retry_interval:
                    continue
                rule, button = matched
                event = self.dismiss(dialog, button, rule=rule)
                if event.action == "failed":
                    self._failed[signature] = now
                else:
                    self._failed.pop(signature, None)
            if event is not None:
                new_events.append(event)

        self._prune(now, {_signature(d) for d in dialogs})
        return new_events

    def _prune(self, now: float, open_signatures: set[str]) -> None:
        """Forget bookkeeping for dialogs that are gone and past their interval.

        Without this, one entry per distinct dialog text accumulates for the
        life of the process.
        """
        for state, interval in (
            (self._warned, self.unrecognized_warn_interval),
            (self._failed, self.failed_retry_interval),
        ):
            for signature, when in list(state.items()):
                if signature not in open_signatures and now - when >= interval:
                    del state[signature]

    def dismiss(
        self,
        dialog: Dialog,
        button: DialogButton,
        rule: DialogRule | None = None,
    ) -> DialogEvent:
        """Click *button* on *dialog* and record the outcome."""
        rule_name = rule.name if rule else "manual"
        log.info(
            "Dismissing QB dialog %r via rule %r: clicking %r",
            dialog.title,
            rule_name,
            button.label,
        )
        try:
            closed = click_button(dialog, button)
        except DialogError as exc:
            log.error(
                "Could not click %r on QB dialog %r (rule %r): %s",
                button.label,
                dialog.title,
                rule_name,
                exc,
            )
            event = DialogEvent(
                timestamp=time.time(),
                action="failed",
                title=dialog.title,
                text=dialog.text,
                rule=rule_name,
                button=button.label,
                detail=str(exc),
            )
            self.events.appendleft(event)
            return event

        if closed:
            self.dismissed_count += 1
            self._warned.pop(_signature(dialog), None)
            event = DialogEvent(
                timestamp=time.time(),
                action="dismissed",
                title=dialog.title,
                text=dialog.text,
                rule=rule_name,
                button=button.label,
            )
        else:
            log.error(
                "Clicked %r on QB dialog %r (rule %r) but it is still open",
                button.label,
                dialog.title,
                rule_name,
            )
            event = DialogEvent(
                timestamp=time.time(),
                action="failed",
                title=dialog.title,
                text=dialog.text,
                rule=rule_name,
                button=button.label,
                detail="dialog still open after the click",
            )
        self.events.appendleft(event)
        return event

    def _note_unrecognized(self, dialog: Dialog) -> DialogEvent | None:
        """Warn about a dialog no rule covers — once, then periodically."""
        signature = _signature(dialog)
        now = time.monotonic()
        last = self._warned.get(signature)
        if last is not None and now - last < self.unrecognized_warn_interval:
            return None
        self._warned[signature] = now

        explanation = needs_human(dialog)
        if explanation:
            log.warning(
                "QuickBooks is blocked on %r, which needs a person: %s",
                dialog.title,
                explanation,
            )
        else:
            log.warning(
                "Unrecognized QuickBooks dialog is open and may be blocking requests — "
                "title=%r buttons=%s text=%r",
                dialog.title,
                [b.label for b in dialog.buttons],
                dialog.text,
            )
        event = DialogEvent(
            timestamp=time.time(),
            action="needs_human" if explanation else "unrecognized",
            title=dialog.title,
            text=dialog.text,
            detail=explanation
            or "no rule matches; add one to dialog_rules.json or dismiss it manually",
        )
        self.events.appendleft(event)
        return event

    # -- construction ------------------------------------------------------

    @classmethod
    def from_settings(
        cls,
        rules_path: Path | None,
        *,
        enabled: bool = True,
        poll_interval: float = 5.0,
    ) -> DialogWatcher:
        """Build a watcher, loading custom rules from *rules_path* if present.

        A rules file we can't parse doesn't stop the bridge — the API is the
        bridge's real job — but it must not pass unnoticed either: the failure
        is logged as an error and reported by ``GET /api/v1/dialogs`` as
        ``rules_error`` until it is fixed.
        """
        rules_error: str | None = None
        try:
            rules = load_rules(rules_path)
        except DialogRuleError as exc:
            rules_error = str(exc)
            log.error("Dialog rules rejected — falling back to built-in rules only: %s", exc)
            rules = list(BUILTIN_RULES)

        watcher = cls(rules, enabled=enabled, poll_interval=poll_interval)
        watcher.rules_error = rules_error
        return watcher

    # -- introspection -----------------------------------------------------

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "running": self._running,
            # Clicking into an elevated QuickBooks from a non-elevated bridge
            # is blocked by Windows, so this is the first thing to check when
            # dismissals fail.
            "elevated": is_elevated(),
            "poll_interval": self.poll_interval,
            "rules": [r.as_dict() for r in self.rules],
            "rules_error": self.rules_error,
            "dismissed_count": self.dismissed_count,
            "seconds_since_last_sweep": (
                None if self.last_sweep is None else round(time.monotonic() - self.last_sweep, 1)
            ),
            "last_error": self.last_error,
        }
