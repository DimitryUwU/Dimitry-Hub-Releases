from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stop_process(pid: int) -> None:
    if os.name == "nt" and process_alive(pid):
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-pid", type=int, required=True)
    parser.add_argument("--installer", required=True)
    parser.add_argument("--restart", required=True)
    args = parser.parse_args()

    installer = Path(args.installer).resolve()
    restart = Path(args.restart).resolve()
    if not installer.exists():
        return 2

    deadline = time.time() + 20
    while process_alive(args.wait_pid) and time.time() < deadline:
        time.sleep(0.35)
    if process_alive(args.wait_pid):
        stop_process(args.wait_pid)
        time.sleep(1.0)

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [
            str(installer),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/CLOSEAPPLICATIONS",
            "/NORESTARTAPPLICATIONS",
        ],
        creationflags=flags,
    )
    if result.returncode != 0:
        return result.returncode

    if restart.exists():
        subprocess.Popen([str(restart)], creationflags=flags)

    if os.name == "nt":
        me = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
        command = (
            'timeout /t 3 /nobreak >nul & '
            f'del /f /q "{installer}" >nul 2>&1 & '
            f'del /f /q "{me}" >nul 2>&1'
        )
        subprocess.Popen(["cmd", "/d", "/c", command], creationflags=flags)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
