from __future__ import annotations

import atexit
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

import uvicorn

HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}"
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def _data_dir() -> Path:
    configured = os.environ.get("DIMITRY_HUB_DATA")
    if configured:
        return Path(configured).expanduser()
    if getattr(sys, "frozen", False) and os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "Dimitry Hub" / "Data"
    return APP_DIR / "data"


DATA_DIR = _data_dir()
PID_FILE = DATA_DIR / "dimitry_hub.pid"
LOG_DIR = DATA_DIR.parent / "Logs"
STARTUP_LOG = LOG_DIR / "startup.log"


def server_is_running() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.35):
            return True
    except OSError:
        return False


def open_url() -> None:
    try:
        if os.name == "nt":
            os.startfile(URL)  # type: ignore[attr-defined]
        else:
            webbrowser.open(URL)
    except Exception:
        webbrowser.open(URL)


def open_browser_when_ready(timeout: float = 25.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server_is_running():
            open_url()
            return
        time.sleep(0.2)


def remove_pid_file() -> None:
    try:
        if PID_FILE.exists() and PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            PID_FILE.unlink()
    except OSError:
        pass


def report_startup_error(exc: BaseException) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        STARTUP_LOG.write_text(details, encoding="utf-8")
    except Exception:
        pass

    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0,
                "Dimitry Hub no pudo iniciarse.\n\n"
                f"Se guardó un informe en:\n{STARTUP_LOG}",
                "Dimitry Hub",
                0x10,
            )
        except Exception:
            pass


def main() -> int:
    if server_is_running():
        open_url()
        return 0

    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        atexit.register(remove_pid_file)

        # Importación directa: garantiza que PyInstaller incluya toda la aplicación.
        from app.main import app as fastapi_app

        threading.Thread(target=open_browser_when_ready, name="dimitry-browser", daemon=True).start()
        uvicorn.run(
            fastapi_app,
            host=HOST,
            port=PORT,
            reload=False,
            access_log=False,
            log_config=None,
        )
        return 0
    except Exception as exc:
        report_startup_error(exc)
        return 1
    finally:
        remove_pid_file()


if __name__ == "__main__":
    raise SystemExit(main())
