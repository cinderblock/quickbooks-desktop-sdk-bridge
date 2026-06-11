"""QB Desktop process management — launch, detect, close."""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)

# Default path to the QB executable on this machine
QB_EXE_PATH = r"C:\Program Files (x86)\Intuit\QuickBooks 2021\QBW32Pro.exe"


def is_qb_running() -> bool:
    """Best-effort check if QuickBooks Desktop is running.

    Uses ctypes CreateToolhelp32Snapshot (works from both 32/64-bit Python).
    Note: may not detect QB in all cases due to session isolation.
    """
    try:
        import ctypes
        import ctypes.wintypes

        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.wintypes.DWORD),
                ("cntUsage", ctypes.wintypes.DWORD),
                ("th32ProcessID", ctypes.wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", ctypes.wintypes.DWORD),
                ("cntThreads", ctypes.wintypes.DWORD),
                ("th32ParentProcessID", ctypes.wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        k32 = ctypes.windll.kernel32
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)

        if k32.Process32FirstW(snap, ctypes.byref(entry)):
            while True:
                name = entry.szExeFile.lower()
                if "qbw32" in name:
                    k32.CloseHandle(snap)
                    return True
                if not k32.Process32NextW(snap, ctypes.byref(entry)):
                    break
        k32.CloseHandle(snap)
    except Exception as exc:
        log.debug("Process detection failed: %s", exc)

    return False


def can_connect_to_qb() -> bool:
    """Try a lightweight COM connection to see if QB is ready.

    This is the most reliable way to check — if BeginSession works, QB is open.
    """
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            rp = win32com.client.Dispatch("QBXMLRP2.RequestProcessor")
            rp.OpenConnection2("QBBridgeProbe", "QB Bridge Probe", 1)
            ticket = rp.BeginSession("", 2)
            rp.EndSession(ticket)
            rp.CloseConnection()
            return True
        except Exception as exc:
            import contextlib

            # Not ready is a normal, expected state (QB closed, company file
            # not open, app not authorized) — log at debug so the reason is
            # discoverable without spamming a polling caller.
            log.debug("can_connect_to_qb: probe failed: %s", exc)
            with contextlib.suppress(Exception):
                rp.CloseConnection()
            return False
        finally:
            pythoncom.CoUninitialize()
    except Exception as exc:
        log.debug("can_connect_to_qb: COM init/dispatch failed: %s", exc)
        return False


def launch_qb(
    company_file: str | None = None,
    exe_path: str = QB_EXE_PATH,
    wait_seconds: int = 30,
) -> bool:
    """Launch QuickBooks Desktop.

    Returns True if the launch command was issued (QB may still be loading).
    """
    log.info("Launching QuickBooks: %s", exe_path)
    try:
        cmd = [exe_path]
        if company_file:
            cmd.append(company_file)
        subprocess.Popen(cmd)
        return True
    except FileNotFoundError:
        log.error("QuickBooks executable not found: %s", exe_path)
        return False
    except Exception as exc:
        log.error("Failed to launch QuickBooks: %s", exc)
        return False


def close_qb(force: bool = False) -> bool:
    """Close QuickBooks Desktop. Returns True only if QB is no longer running.

    A caller relying on this to release the company-file lock must be able to
    trust the result, so we verify QB actually stopped rather than reporting
    success just because the command was issued.
    """
    if not is_qb_running():
        return True
    try:
        result = subprocess.run(
            ["powershell", "-Command", "Stop-Process -Name 'QBW32' -Force"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as exc:
        log.warning("close_qb: failed to run Stop-Process: %s", exc)
        return False

    if result.returncode != 0:
        log.warning("close_qb: Stop-Process exited %d: %s", result.returncode, result.stderr.strip())

    if is_qb_running():
        log.warning("close_qb: QuickBooks is still running after Stop-Process")
        return False
    return True
