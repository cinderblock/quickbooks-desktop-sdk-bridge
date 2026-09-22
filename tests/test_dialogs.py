"""QuickBooks dialog recognition, dismissal, and the endpoints around them.

No real windows are involved: dialogs are built as plain :class:`Dialog`
objects and the Win32 click is stubbed, so these run anywhere.
"""

from __future__ import annotations

import json

import pytest

from qb_bridge.qb import dialogs as dialogs_mod
from qb_bridge.qb.dialogs import (
    BUILTIN_RULES,
    Dialog,
    DialogButton,
    DialogRule,
    DialogRuleError,
    DialogWatcher,
    _is_button,
    load_rules,
    rules_from_data,
)

BACKUP_FAILED_TEXT = (
    "The following error(s) occurred while attempting to backup your data:\n\n"
    "QuickBooks encountered a problem attempting to backup "
    "C:\\Users\\me\\Documents\\Company.QBW - Drive specified could not be accessed.\n\n"
    "Please refer to the QBBackup.log file for more details."
)


def make_dialog(title: str, text: str = "", buttons: tuple[str, ...] = ("OK",)) -> Dialog:
    return Dialog(
        hwnd=1000,
        pid=42,
        process="qbw32",
        window_class="#32770",
        title=title,
        text=text,
        buttons=tuple(
            DialogButton(hwnd=2000 + i, control_id=i + 1, label=label)
            for i, label in enumerate(buttons)
        ),
    )


class TestRuleMatching:
    def test_backup_failure_dialog_is_recognized(self):
        """The dialog seen in the wild: a scheduled backup to a missing drive."""
        watcher = DialogWatcher(enabled=False)
        matched = watcher.match(make_dialog("QuickBooks Backup", BACKUP_FAILED_TEXT))
        assert matched is not None
        rule, button = matched
        assert rule.name == "backup-failed"
        assert button.label == "OK"

    def test_unknown_dialog_matches_nothing(self):
        watcher = DialogWatcher(enabled=False)
        dialog = make_dialog(
            "Delete Transaction",
            "Are you sure you want to delete this transaction?",
            ("Yes", "No"),
        )
        assert watcher.match(dialog) is None

    def test_body_pattern_distinguishes_same_titled_dialogs(self):
        """Two 'QuickBooks Backup' dialogs, only one of which we recognize."""
        watcher = DialogWatcher(enabled=False)
        unknown = make_dialog(
            "QuickBooks Backup",
            "Overwrite the existing backup file?",
            ("Yes", "No"),
        )
        assert watcher.match(unknown) is None

    def test_rule_needs_the_button_it_names(self):
        rule = DialogRule(name="r", title="^Anything$", button="^OK$")
        assert rule.match(make_dialog("Anything", buttons=("Cancel",))) is None
        assert rule.match(make_dialog("Anything", buttons=("Cancel", "OK"))) is not None

    def test_disabled_rule_never_matches(self):
        rule = DialogRule(name="r", title="^Anything$", button="^OK$", enabled=False)
        assert rule.match(make_dialog("Anything")) is None

    def test_first_matching_rule_wins(self):
        watcher = DialogWatcher(
            [
                DialogRule(name="first", title="Msg", button="^OK$"),
                DialogRule(name="second", title="Msg", button="^OK$"),
            ],
            enabled=False,
        )
        matched = watcher.match(make_dialog("Msg"))
        assert matched is not None and matched[0].name == "first"


class TestRuleLoading:
    def test_builtin_rules_all_compile(self):
        assert BUILTIN_RULES
        assert all(r.match(make_dialog("nothing like this")) is None for r in BUILTIN_RULES)

    def test_custom_rule_file_extends_builtins(self, tmp_path):
        path = tmp_path / "dialog_rules.json"
        path.write_text(
            json.dumps([{"name": "custom", "title": "^Payroll$", "button": "^Close$"}]),
            encoding="utf-8",
        )
        rules = load_rules(path)
        assert [r.name for r in rules if r.name == "custom"]
        assert len(rules) == len(BUILTIN_RULES) + 1

    def test_custom_rule_replaces_builtin_of_the_same_name(self, tmp_path):
        path = tmp_path / "dialog_rules.json"
        path.write_text(
            json.dumps([{"name": "backup-failed", "title": "x", "button": "y", "enabled": False}]),
            encoding="utf-8",
        )
        rules = {r.name: r for r in load_rules(path)}
        assert rules["backup-failed"].enabled is False
        assert len(rules) == len(BUILTIN_RULES)

    def test_missing_file_means_builtins_only(self, tmp_path):
        assert load_rules(tmp_path / "nope.json") == list(BUILTIN_RULES)

    def test_malformed_json_raises(self, tmp_path):
        path = tmp_path / "dialog_rules.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(DialogRuleError, match="could not read dialog rules"):
            load_rules(path)

    def test_unknown_field_raises(self):
        with pytest.raises(DialogRuleError, match="unknown field"):
            rules_from_data(
                [{"name": "r", "title": "t", "button": "b", "click": "OK"}], source="test"
            )

    def test_missing_field_raises(self):
        with pytest.raises(DialogRuleError, match="missing required field"):
            rules_from_data([{"name": "r", "title": "t"}], source="test")

    def test_bad_regex_raises(self):
        with pytest.raises(DialogRuleError, match="not a valid regex"):
            rules_from_data([{"name": "r", "title": "(unclosed", "button": "b"}], source="test")

    def test_duplicate_names_raise(self):
        with pytest.raises(DialogRuleError, match="duplicate rule name"):
            rules_from_data(
                [
                    {"name": "r", "title": "a", "button": "b"},
                    {"name": "r", "title": "c", "button": "d"},
                ],
                source="test",
            )

    def test_rules_file_failure_is_reported_not_swallowed(self, tmp_path):
        path = tmp_path / "dialog_rules.json"
        path.write_text("[{}]", encoding="utf-8")
        watcher = DialogWatcher.from_settings(path, enabled=False)
        assert watcher.rules_error is not None
        assert watcher.status()["rules_error"] == watcher.rules_error
        # Built-ins still apply, so a typo doesn't disable dialog handling.
        assert watcher.match(make_dialog("QuickBooks Backup", BACKUP_FAILED_TEXT)) is not None


class TestSweep:
    def test_recognized_dialog_is_clicked(self, monkeypatch):
        dialog = make_dialog("QuickBooks Backup", BACKUP_FAILED_TEXT)
        clicked: list[tuple[str, str]] = []
        monkeypatch.setattr(dialogs_mod, "find_dialogs", lambda *a, **k: [dialog])
        monkeypatch.setattr(
            dialogs_mod,
            "click_button",
            lambda d, b, timeout=5.0: clicked.append((d.title, b.label)) or True,
        )

        watcher = DialogWatcher(enabled=False)
        events = watcher.sweep()

        assert clicked == [("QuickBooks Backup", "OK")]
        assert [e.action for e in events] == ["dismissed"]
        assert watcher.dismissed_count == 1

    def test_unrecognized_dialog_is_reported_but_not_clicked(self, monkeypatch):
        dialog = make_dialog("Delete Transaction", "Are you sure?", ("Yes", "No"))
        monkeypatch.setattr(dialogs_mod, "find_dialogs", lambda *a, **k: [dialog])
        monkeypatch.setattr(
            dialogs_mod,
            "click_button",
            lambda *a, **k: pytest.fail("must not click an unrecognized dialog"),
        )

        watcher = DialogWatcher(enabled=False)
        events = watcher.sweep()

        assert [e.action for e in events] == ["unrecognized"]
        assert watcher.dismissed_count == 0

    def test_unrecognized_dialog_is_not_re_reported_every_sweep(self, monkeypatch):
        dialog = make_dialog("Delete Transaction", "Are you sure?", ("Yes", "No"))
        monkeypatch.setattr(dialogs_mod, "find_dialogs", lambda *a, **k: [dialog])

        watcher = DialogWatcher(enabled=False, unrecognized_warn_interval=300.0)
        assert len(watcher.sweep()) == 1
        assert watcher.sweep() == []
        assert len(watcher.events) == 1

    def test_failed_click_is_recorded_as_failed(self, monkeypatch):
        dialog = make_dialog("QuickBooks Backup", BACKUP_FAILED_TEXT)
        monkeypatch.setattr(dialogs_mod, "find_dialogs", lambda *a, **k: [dialog])
        monkeypatch.setattr(dialogs_mod, "click_button", lambda d, b, timeout=5.0: False)

        watcher = DialogWatcher(enabled=False)
        events = watcher.sweep()

        assert [e.action for e in events] == ["failed"]
        assert watcher.dismissed_count == 0

    def test_access_denied_click_reports_the_elevation_problem(self, monkeypatch):
        """Windows refusing the click is a setup problem and must say so."""
        dialog = make_dialog("QuickBooks Backup", BACKUP_FAILED_TEXT)

        def denied(d, b, timeout=5.0):
            raise dialogs_mod.DialogError(dialogs_mod._UIPI_HINT)

        monkeypatch.setattr(dialogs_mod, "find_dialogs", lambda *a, **k: [dialog])
        monkeypatch.setattr(dialogs_mod, "click_button", denied)

        watcher = DialogWatcher(enabled=False)
        (event,) = watcher.sweep()

        assert event.action == "failed"
        assert "elevated" in event.detail

    def test_failed_dialog_is_retried_after_backoff(self, monkeypatch):
        dialog = make_dialog("QuickBooks Backup", BACKUP_FAILED_TEXT)
        attempts = []
        monkeypatch.setattr(dialogs_mod, "find_dialogs", lambda *a, **k: [dialog])
        monkeypatch.setattr(
            dialogs_mod,
            "click_button",
            lambda d, b, timeout=5.0: attempts.append(b.label) and False,
        )

        watcher = DialogWatcher(enabled=False, failed_retry_interval=1000.0)
        watcher.sweep()
        watcher.sweep()
        assert len(attempts) == 1, "a dialog that refused to close should not be retried at once"

        watcher.failed_retry_interval = 0.0
        watcher.sweep()
        assert len(attempts) == 2


class TestDialogEndpoints:
    async def test_list_shows_dialogs_and_their_rule(self, client, monkeypatch):
        import qb_bridge.api.routes.dialogs as route_mod

        dialog = make_dialog("QuickBooks Backup", BACKUP_FAILED_TEXT)
        monkeypatch.setattr(route_mod, "find_dialogs", lambda *a, **k: [dialog])

        r = await client.get("/api/v1/dialogs")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["meta"]["count"] == 1
        assert body["data"][0]["title"] == "QuickBooks Backup"
        assert body["data"][0]["rule"] == "backup-failed"
        assert body["data"][0]["auto_dismiss_button"] == "OK"

    async def test_list_flags_dialogs_no_rule_covers(self, client, monkeypatch):
        import qb_bridge.api.routes.dialogs as route_mod

        dialog = make_dialog("Delete Transaction", "Are you sure?", ("Yes", "No"))
        monkeypatch.setattr(route_mod, "find_dialogs", lambda *a, **k: [dialog])

        r = await client.get("/api/v1/dialogs")
        assert r.json()["data"][0]["rule"] is None

    async def test_dismiss_clicks_the_named_button(self, client, monkeypatch):
        import qb_bridge.api.routes.dialogs as route_mod

        dialog = make_dialog("Delete Transaction", "Are you sure?", ("Yes", "No"))
        clicked = []
        monkeypatch.setattr(route_mod, "find_dialogs", lambda *a, **k: [dialog])
        monkeypatch.setattr(
            dialogs_mod,
            "click_button",
            lambda d, b, timeout=5.0: clicked.append(b.label) or True,
        )

        r = await client.post(f"/api/v1/dialogs/{dialog.hwnd}/dismiss", params={"button": "No"})
        assert r.status_code == 200, r.text
        assert clicked == ["No"]
        assert r.json()["data"]["action"] == "dismissed"

    async def test_dismiss_rejects_a_button_that_is_not_there(self, client, monkeypatch):
        import qb_bridge.api.routes.dialogs as route_mod

        dialog = make_dialog("Delete Transaction", "Are you sure?", ("Yes", "No"))
        monkeypatch.setattr(route_mod, "find_dialogs", lambda *a, **k: [dialog])

        r = await client.post(f"/api/v1/dialogs/{dialog.hwnd}/dismiss", params={"button": "Maybe"})
        assert r.status_code == 400
        assert r.json()["detail"]["error"]["code"] == "BUTTON_NOT_FOUND"

    async def test_dismiss_without_a_button_needs_a_matching_rule(self, client, monkeypatch):
        import qb_bridge.api.routes.dialogs as route_mod

        dialog = make_dialog("Delete Transaction", "Are you sure?", ("Yes", "No"))
        monkeypatch.setattr(route_mod, "find_dialogs", lambda *a, **k: [dialog])

        r = await client.post(f"/api/v1/dialogs/{dialog.hwnd}/dismiss")
        assert r.status_code == 409
        assert r.json()["detail"]["error"]["code"] == "NO_MATCHING_RULE"

    async def test_dismiss_unknown_handle_is_404(self, client, monkeypatch):
        import qb_bridge.api.routes.dialogs as route_mod

        monkeypatch.setattr(route_mod, "find_dialogs", lambda *a, **k: [])

        r = await client.post("/api/v1/dialogs/999/dismiss", params={"button": "OK"})
        assert r.status_code == 404
        assert r.json()["detail"]["error"]["code"] == "DIALOG_NOT_FOUND"

    async def test_events_list_watcher_history(self, client, dialog_watcher, monkeypatch):
        dialog = make_dialog("QuickBooks Backup", BACKUP_FAILED_TEXT)
        monkeypatch.setattr(dialogs_mod, "find_dialogs", lambda *a, **k: [dialog])
        monkeypatch.setattr(dialogs_mod, "click_button", lambda d, b, timeout=5.0: True)
        dialog_watcher.sweep()

        r = await client.get("/api/v1/dialogs/events")
        assert r.status_code == 200, r.text
        assert r.json()["data"][0]["action"] == "dismissed"
        assert r.json()["meta"]["dismissed_count"] == 1

    async def test_status_reports_blocking_dialogs(self, client, dialog_watcher, monkeypatch):
        dialog = make_dialog("Delete Transaction", "Are you sure?", ("Yes", "No"))
        monkeypatch.setattr(dialogs_mod, "find_dialogs", lambda *a, **k: [dialog])
        dialog_watcher.sweep()

        r = await client.get("/api/v1/status")
        data = r.json()["data"]
        assert data["qb_dialogs_open"] == 1
        assert data["qb_dialogs_unrecognized"] == ["Delete Transaction"]


class TestBookkeeping:
    def test_state_for_closed_dialogs_is_forgotten(self, monkeypatch):
        """Per-dialog dedupe state must not grow for the life of the process."""
        dialog = make_dialog("Delete Transaction", "Are you sure?", ("Yes", "No"))
        open_dialogs = [dialog]
        monkeypatch.setattr(dialogs_mod, "find_dialogs", lambda *a, **k: list(open_dialogs))

        watcher = DialogWatcher(enabled=False, unrecognized_warn_interval=0.0)
        watcher.sweep()
        assert watcher._warned

        open_dialogs.clear()
        watcher.sweep()
        assert not watcher._warned


class TestButtonDetection:
    """QuickBooks draws most of its dialogs with its own toolkit."""

    def test_win32_and_maui_buttons_both_count(self):
        # Seen live: the QuickBooks Desktop Login dialog is a MauiForm whose
        # OK/Cancel are MauiPushButton, not Button.
        assert _is_button("Button")
        assert _is_button("MauiPushButton")
        assert _is_button("mauipushbutton")

    def test_non_buttons_are_not_buttons(self):
        assert not _is_button("Static")
        assert not _is_button("Edit")
        assert not _is_button("MauiForm")


class TestNeedsHuman:
    """Some blockers can't be clicked away — say what to do instead."""

    def test_login_prompt_explains_the_unattended_fix(self):
        dialog = make_dialog("QuickBooks Desktop Login", "Cameron", ("OK", "Cancel"))
        guidance = dialogs_mod.needs_human(dialog)
        assert guidance and "log in automatically" in guidance

    def test_ordinary_dialog_has_no_special_guidance(self):
        assert dialogs_mod.needs_human(make_dialog("QuickBooks Backup")) is None

    def test_sweep_reports_needs_human_rather_than_unrecognized(self, monkeypatch):
        dialog = make_dialog("QuickBooks Desktop Login", "Cameron", ("OK", "Cancel"))
        monkeypatch.setattr(dialogs_mod, "find_dialogs", lambda *a, **k: [dialog])
        monkeypatch.setattr(
            dialogs_mod,
            "click_button",
            lambda *a, **k: pytest.fail("a login prompt must never be clicked"),
        )

        watcher = DialogWatcher(enabled=False)
        (event,) = watcher.sweep()

        assert event.action == "needs_human"
        assert "Integrated Applications" in event.detail


class TestProcessMatching:
    def test_quickbooks_itself_is_matched(self):
        assert any("qbw32pro".startswith(p) for p in dialogs_mod.QB_PROCESS_PREFIXES)
        assert any("qbw32".startswith(p) for p in dialogs_mod.QB_PROCESS_PREFIXES)

    def test_web_connector_is_not_quickbooks(self):
        """qbwebconnector has its own window; it does not block the company file."""
        assert not any("qbwebconnector".startswith(p) for p in dialogs_mod.QB_PROCESS_PREFIXES)
