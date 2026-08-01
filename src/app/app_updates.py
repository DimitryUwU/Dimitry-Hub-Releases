from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .version import APP_VERSION, LATEST_MANIFEST_URL

_LOCK = threading.RLock()
_STATE: dict[str, Any] = {
    "current_version": APP_VERSION,
    "latest_version": APP_VERSION,
    "available": False,
    "checking": False,
    "installing": False,
    "last_checked": "",
    "error": "",
    "notes": "",
    "manifest": {},
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for item in value.strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in item if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts or [0])


def status() -> dict[str, Any]:
    with _LOCK:
        return dict(_STATE)


def check_now(timeout: float = 12.0) -> dict[str, Any]:
    with _LOCK:
        if _STATE["checking"]:
            return dict(_STATE)
        _STATE["checking"] = True
        _STATE["error"] = ""
    try:
        request = urllib.request.Request(
            LATEST_MANIFEST_URL,
            headers={"User-Agent": f"Dimitry-Hub/{APP_VERSION}"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            manifest = json.loads(response.read().decode("utf-8"))
        latest = str(manifest.get("version", APP_VERSION)).strip()
        available = _version_tuple(latest) > _version_tuple(APP_VERSION)
        with _LOCK:
            _STATE.update(
                latest_version=latest,
                available=available,
                notes=str(manifest.get("notes", "")),
                manifest=manifest,
                last_checked=_utc_now(),
            )
    except Exception as exc:
        with _LOCK:
            _STATE["error"] = f"No se pudo comprobar la actualización: {exc}"
            _STATE["last_checked"] = _utc_now()
    finally:
        with _LOCK:
            _STATE["checking"] = False
            return dict(_STATE)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_available_update() -> dict[str, Any]:
    with _LOCK:
        if _STATE["installing"]:
            return dict(_STATE)
        manifest = dict(_STATE.get("manifest") or {})
        if not _STATE.get("available"):
            return dict(_STATE)
        _STATE["installing"] = True
        _STATE["error"] = ""

    try:
        if os.name != "nt" or not getattr(sys, "frozen", False):
            raise RuntimeError("La instalación automática solo se ejecuta en la aplicación instalada para Windows.")
        url = str(manifest.get("installer_url", "")).strip()
        expected = str(manifest.get("sha256", "")).strip().lower()
        if not url or len(expected) != 64:
            raise RuntimeError("El manifiesto de actualización está incompleto.")

        temp_dir = Path(tempfile.mkdtemp(prefix="dimitry-hub-update-"))
        installer = temp_dir / "Dimitry_Hub_Setup_x64.exe"
        request = urllib.request.Request(url, headers={"User-Agent": f"Dimitry-Hub/{APP_VERSION}"})
        with urllib.request.urlopen(request, timeout=120) as response, installer.open("wb") as target:
            shutil.copyfileobj(response, target)
        actual = _sha256(installer)
        if actual.lower() != expected:
            installer.unlink(missing_ok=True)
            raise RuntimeError("La verificación SHA-256 de la actualización no coincide.")

        app_dir = Path(sys.executable).resolve().parent
        updater_source = app_dir / "DimitryHubUpdater.exe"
        if not updater_source.exists():
            raise RuntimeError("No se encontró el componente seguro de actualización.")
        updater_copy = temp_dir / "DimitryHubUpdater.exe"
        shutil.copy2(updater_source, updater_copy)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            [
                str(updater_copy),
                "--wait-pid", str(os.getpid()),
                "--installer", str(installer),
                "--restart", str(sys.executable),
            ],
            creationflags=flags,
            close_fds=True,
        )
        with _LOCK:
            _STATE["installing"] = True
        threading.Thread(target=_exit_soon, daemon=True).start()
    except Exception as exc:
        with _LOCK:
            _STATE["installing"] = False
            _STATE["error"] = f"No se pudo instalar la actualización: {exc}"
    return status()


def _exit_soon() -> None:
    time.sleep(1.1)
    os._exit(0)


def startup_auto_update() -> None:
    # Da tiempo a que la interfaz se abra. Si hay una versión nueva, la instala
    # silenciosamente y conserva los datos almacenados fuera del directorio del programa.
    time.sleep(8)
    result = check_now()
    if result.get("available") and not result.get("error"):
        install_available_update()
