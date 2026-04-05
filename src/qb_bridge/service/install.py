"""CLI helper for installing / managing the Windows service."""

from __future__ import annotations

import subprocess
import sys


def main():
    """Dispatch to the service module's command-line handler."""
    from qb_bridge.service.svc import main as svc_main

    svc_main()


def install_with_user(username: str, password: str) -> None:
    """Install the service to run under a specific user account.

    This is needed because the QB COM objects require the same
    user session that authorized the app in QuickBooks.
    """
    python_exe = sys.executable
    svc_module = "qb_bridge.service.svc"

    cmd = [
        python_exe,
        "-m",
        svc_module,
        "--startup",
        "auto",
        "--username",
        username,
        "--password",
        password,
        "install",
    ]
    print(f"Installing service: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(f"Error: {result.stderr}", file=sys.stderr)
        return

    # Configure failure recovery: restart after 60s
    sc_cmd = [
        "sc.exe",
        "failure",
        "QBBridge",
        "reset=",
        "86400",
        "actions=",
        "restart/60000/restart/60000/restart/60000",
    ]
    subprocess.run(sc_cmd, capture_output=True)
    print("Service installed with auto-restart on failure.")


if __name__ == "__main__":
    main()
