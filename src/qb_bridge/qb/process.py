"""QB Desktop process management — launch, detect, close."""

from __future__ import annotations

import logging
import subprocess
import time

log = logging.getLogger(__name__)

# All known QB Desktop executable names
QB_EXE_NAMES = ("QBW32Pro.exe", "QBW32.EXE", "QBW.exe")

# Default path to the QB executable on this machine
QB_EXE_PATH = r"C:\Program Files (x86)\Intuit\QuickBooks 2021\QBW32Pro.exe"


def is_qb_running() -> bool:
    """Check if any QuickBooks Desktop process is running."""
    for exe_name in QB_EXE_NAMES:
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if exe_name.lower() in result.stdout.lower():
                return True
        except Exception:
            continue
    return False


def launch_qb(
    company_file: str | None = None,
    exe_path: str = QB_EXE_PATH,
    wait_seconds: int = 30,
) -> bool:
    """Launch QuickBooks Desktop and wait for it to be ready.

    Args:
        company_file: Path to .qbw file to open, or None for default.
        exe_path: Path to QB executable.
        wait_seconds: Max seconds to wait for QB to start.

    Returns:
        True if QB is running after this call.
    """
    if is_qb_running():
        log.info("QuickBooks is already running")
        return True

    cmd = [exe_path]
    if company_file:
        cmd.append(company_file)

    log.info("Launching QuickBooks: %s", cmd)
    try:
        subprocess.Popen(cmd)
    except FileNotFoundError:
        log.error("QuickBooks executable not found: %s", exe_path)
        return False
    except Exception as exc:
        log.error("Failed to launch QuickBooks: %s", exc)
        return False

    # Poll until QB is in the process list
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if is_qb_running():
            # Give QB a few more seconds to fully initialize
            time.sleep(3)
            log.info("QuickBooks is running")
            return True
        time.sleep(1)

    log.error("QuickBooks did not start within %d seconds", wait_seconds)
    return False


def close_qb(force: bool = False) -> bool:
    """Close QuickBooks Desktop gracefully.

    Uses taskkill with /F only if force=True.
    Returns True if QB is no longer running.
    """
    if not is_qb_running():
        return True

    for exe_name in QB_EXE_NAMES:
        try:
            cmd = ["taskkill"]
            if force:
                cmd.append("/F")
            cmd.extend(["/IM", exe_name])
            subprocess.run(cmd, capture_output=True, timeout=15)
        except Exception as exc:
            log.warning("taskkill %s failed: %s", exe_name, exc)

    time.sleep(2)
    return not is_qb_running()
