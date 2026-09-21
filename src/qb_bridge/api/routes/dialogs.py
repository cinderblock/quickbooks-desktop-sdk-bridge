"""QuickBooks dialog inspection and dismissal.

QuickBooks Desktop stops answering COM requests while one of its own modal
dialogs is open. The watcher clicks the ones it recognizes; these endpoints
show what is on screen, what the watcher has done, and let a caller dismiss a
dialog no rule covers.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query

from qb_bridge.api.deps import get_dialog_watcher, require_api_key, require_permission
from qb_bridge.api.strict import StrictQueryParamsRoute
from qb_bridge.qb.dialogs import DialogError, DialogWatcher, find_dialogs

router = APIRouter(prefix="/api/v1/dialogs", tags=["Dialogs"], route_class=StrictQueryParamsRoute)


def _describe(watcher: DialogWatcher, dialog) -> dict:
    """Dialog as JSON, annotated with the rule that would dismiss it."""
    data = dialog.as_dict()
    matched = watcher.match(dialog)
    data["rule"] = matched[0].name if matched else None
    data["auto_dismiss_button"] = matched[1].label if matched else None
    return data


@router.get(
    "",
    summary="List open QuickBooks dialogs",
    description=(
        "Scans for QuickBooks dialog windows right now and returns their title, text, "
        "and buttons, plus the rule (if any) that the watcher would use to dismiss each "
        "one. A dialog with `rule: null` is blocking QuickBooks until someone dismisses "
        "it — either manually, or by adding a rule for it."
    ),
)
async def list_dialogs(
    watcher: DialogWatcher = Depends(get_dialog_watcher),
    _key: dict = Depends(require_api_key),
    _perm=Depends(require_permission("Dialog", "list")),
):
    try:
        dialogs = await asyncio.to_thread(find_dialogs)
    except DialogError as exc:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "error": {"code": "DIALOG_SCAN_FAILED", "message": str(exc)}},
        ) from exc

    return {
        "ok": True,
        "data": [_describe(watcher, d) for d in dialogs],
        "meta": {"count": len(dialogs), "watcher": watcher.status()},
    }


@router.get(
    "/events",
    summary="Recent dialog watcher activity",
    description=(
        "The most recent dialogs the watcher dismissed, failed to dismiss, or did not "
        "recognize — newest first. Use the `text` of an `unrecognized` event to write a "
        "rule for it."
    ),
)
async def list_events(
    limit: int = Query(50, ge=1, le=500, description="How many events to return"),
    watcher: DialogWatcher = Depends(get_dialog_watcher),
    _key: dict = Depends(require_api_key),
    _perm=Depends(require_permission("Dialog", "list")),
):
    events = list(watcher.events)[:limit]
    return {
        "ok": True,
        "data": [e.as_dict() for e in events],
        "meta": {"count": len(events), "dismissed_count": watcher.dismissed_count},
    }


@router.post(
    "/{hwnd}/dismiss",
    summary="Dismiss an open QuickBooks dialog",
    description=(
        "Clicks a button on the dialog with the given window handle (`hwnd`, from "
        "`GET /api/v1/dialogs`). Pass `button` to choose which button to click; without "
        "it, the dialog must match a rule, and that rule's button is used."
    ),
)
async def dismiss_dialog(
    hwnd: int,
    button: str | None = Query(
        None,
        description="Exact button label to click, e.g. 'OK'. Omit to use the matching rule.",
    ),
    watcher: DialogWatcher = Depends(get_dialog_watcher),
    _key: dict = Depends(require_api_key),
    _perm=Depends(require_permission("Dialog", "update")),
):
    try:
        dialogs = await asyncio.to_thread(find_dialogs)
    except DialogError as exc:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "error": {"code": "DIALOG_SCAN_FAILED", "message": str(exc)}},
        ) from exc

    dialog = next((d for d in dialogs if d.hwnd == hwnd), None)
    if dialog is None:
        raise HTTPException(
            status_code=404,
            detail={
                "ok": False,
                "error": {
                    "code": "DIALOG_NOT_FOUND",
                    "message": (
                        f"No open QuickBooks dialog with hwnd {hwnd}. It may have already "
                        "been dismissed — call GET /api/v1/dialogs for current handles."
                    ),
                },
            },
        )

    rule = None
    if button is None:
        matched = watcher.match(dialog)
        if matched is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "ok": False,
                    "error": {
                        "code": "NO_MATCHING_RULE",
                        "message": (
                            f"No rule recognizes {dialog.title!r}, so there is no button to "
                            "click by default. Pass ?button=<label> to choose one of: "
                            f"{[b.label for b in dialog.buttons]}"
                        ),
                    },
                },
            )
        rule, target = matched
    else:
        target = dialog.button(button)
        if target is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "ok": False,
                    "error": {
                        "code": "BUTTON_NOT_FOUND",
                        "message": (
                            f"{dialog.title!r} has no button labelled {button!r}. "
                            f"Available: {[b.label for b in dialog.buttons]}"
                        ),
                    },
                },
            )

    event = await asyncio.to_thread(watcher.dismiss, dialog, target, rule)
    if event.action != "dismissed":
        raise HTTPException(
            status_code=502,
            detail={
                "ok": False,
                "error": {
                    "code": "DISMISS_FAILED",
                    "message": f"Clicked {target.label!r} on {dialog.title!r} but it did not "
                    f"close: {event.detail}",
                },
            },
        )

    return {"ok": True, "data": event.as_dict()}
