from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from .database import DATA_DIR, atomic_write_bytes, safe_filename, utc_now

WORKSPACE_DIR = DATA_DIR / "palworld_workspace"
SESSIONS_DIR = WORKSPACE_DIR / "sessions"
TOOLS_DIR = DATA_DIR / "tools" / "palworld-save-pal"
INCOMING_DIR = WORKSPACE_DIR / "incoming"
EDITOR_META = TOOLS_DIR / "installed.json"
PSP_RELEASE_API = "https://api.github.com/repos/oMaN-Rod/palworld-save-pal/releases/latest"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_ZIP_ENTRIES = 20_000
MAX_ZIP_UNCOMPRESSED = 4 * 1024 * 1024 * 1024

SAVE_KIND_LABELS = {
    "level": "Level.sav — mundo completo",
    "global_storage": "GlobalPalStorage.sav — Caja Pal global",
    "world_options": "WorldOption.sav — opciones del mundo",
    "local_data": "LocalData.sav — datos locales",
    "player": "Player .sav — personaje",
    "save_bundle": "Paquete ZIP de guardado",
    "sav": "Archivo SAV de Palworld",
}


def ensure_workspace_dirs() -> None:
    for directory in (WORKSPACE_DIR, SESSIONS_DIR, TOOLS_DIR, INCOMING_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def _json_request(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Dimitry-Hub-Palworld/1.1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(8 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:1000]
        raise RuntimeError(f"GitHub respondió {exc.code}: {body or exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"No se pudo conectar con GitHub: {exc}") from exc
    try:
        value = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("GitHub devolvió una respuesta no válida") from exc
    if not isinstance(value, dict):
        raise RuntimeError("GitHub no devolvió los datos esperados")
    return value


def detect_save_kind(filename: str) -> str:
    name = Path(filename).name.lower()
    if name.endswith(".zip"):
        return "save_bundle"
    if name == "level.sav":
        return "level"
    if name == "globalpalstorage.sav":
        return "global_storage"
    if name == "worldoption.sav":
        return "world_options"
    if name == "localdata.sav":
        return "local_data"
    stem = Path(name).stem
    if name.endswith(".sav") and re.fullmatch(r"[0-9a-f]{32}", stem):
        return "player"
    if name.endswith(".sav"):
        return "sav"
    raise ValueError("Solo se admiten archivos .sav o paquetes .zip de Palworld")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: dict) -> None:
    atomic_write_bytes(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def _safe_extract_saves(zip_path: Path, target: Path) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    root = target.resolve()
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        if len(members) > MAX_ZIP_ENTRIES:
            raise ValueError("El ZIP contiene demasiados archivos")
        if sum(max(0, m.file_size) for m in members) > MAX_ZIP_UNCOMPRESSED:
            raise ValueError("El ZIP es demasiado grande al descomprimirse")
        for member in members:
            if member.is_dir():
                continue
            normalized = member.filename.replace("\\", "/")
            if not normalized.lower().endswith((".sav", ".ini")):
                continue
            destination = (target / normalized).resolve()
            if root != destination and root not in destination.parents:
                raise ValueError(f"Ruta insegura dentro del ZIP: {member.filename}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)
            extracted.append(destination)
    if not extracted:
        raise ValueError("El ZIP no contiene archivos .sav de Palworld")
    return extracted


def create_session_from_path(temp_path: Path, original_name: str, expected_kind: str = "") -> dict:
    ensure_workspace_dirs()
    kind = detect_save_kind(original_name)
    if expected_kind and expected_kind not in {"auto", kind}:
        allowed = {
            "level": {"level", "save_bundle"},
            "global_storage": {"global_storage", "save_bundle"},
            "save_bundle": {"save_bundle"},
        }
        if kind not in allowed.get(expected_kind, {expected_kind}):
            raise ValueError(f"Esperaba {SAVE_KIND_LABELS.get(expected_kind, expected_kind)}, pero recibí {original_name}")
    size = temp_path.stat().st_size
    if size <= 16:
        raise ValueError("El archivo está vacío o es demasiado pequeño")
    if size > MAX_UPLOAD_BYTES:
        raise ValueError("El archivo supera el límite de 2 GB")

    session_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    root = SESSIONS_DIR / session_id
    originals = root / "originales"
    backups = root / "copias_de_seguridad"
    extracted = root / "contenido"
    originals.mkdir(parents=True)
    backups.mkdir(parents=True)
    clean_name = safe_filename(Path(original_name).name)
    stored = originals / clean_name
    shutil.move(str(temp_path), stored)
    backup = backups / clean_name
    shutil.copy2(stored, backup)

    discovered: list[dict] = []
    if kind == "save_bundle":
        extracted_root = extracted.resolve()
        for item in _safe_extract_saves(stored, extracted_root):
            rel = item.relative_to(extracted_root).as_posix()
            item_kind = detect_save_kind(item.name) if item.suffix.lower() == ".sav" else "config"
            discovered.append({
                "name": item.name,
                "relative_path": rel,
                "kind": item_kind,
                "kind_label": SAVE_KIND_LABELS.get(item_kind, "Configuración"),
                "size": item.stat().st_size,
            })
    else:
        extracted.mkdir(parents=True, exist_ok=True)
        shutil.copy2(stored, extracted / clean_name)
        discovered.append({
            "name": clean_name,
            "relative_path": clean_name,
            "kind": kind,
            "kind_label": SAVE_KIND_LABELS.get(kind, kind),
            "size": size,
        })

    metadata = {
        "id": session_id,
        "name": clean_name,
        "kind": kind,
        "kind_label": SAVE_KIND_LABELS.get(kind, kind),
        "size": size,
        "sha256": _sha256(stored),
        "created_at": utc_now(),
        "root": str(root),
        "original_path": str(stored),
        "backup_path": str(backup),
        "content_path": str(extracted),
        "files": discovered,
        "backup_created": True,
    }
    _write_json(root / "session.json", metadata)
    return public_session(metadata)


def public_session(metadata: dict) -> dict:
    return {key: value for key, value in metadata.items() if not key.endswith("_path") and key != "root"}


def get_session(session_id: str) -> dict:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{8}", session_id):
        raise ValueError("Sesión no válida")
    path = SESSIONS_DIR / session_id / "session.json"
    if not path.exists():
        raise FileNotFoundError("La sesión de guardado no existe")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("La sesión está dañada")
    return value


def list_sessions(limit: int = 30) -> list[dict]:
    ensure_workspace_dirs()
    rows: list[dict] = []
    for metadata_path in sorted(SESSIONS_DIR.glob("*/session.json"), reverse=True):
        try:
            value = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                rows.append(public_session(value))
        except Exception:
            continue
        if len(rows) >= limit:
            break
    return rows


def delete_session(session_id: str) -> None:
    metadata = get_session(session_id)
    root = Path(metadata["root"]).resolve()
    sessions_root = SESSIONS_DIR.resolve()
    if sessions_root not in root.parents:
        raise ValueError("Ruta de sesión insegura")
    shutil.rmtree(root)


def session_file(session_id: str, variant: str) -> Path:
    metadata = get_session(session_id)
    if variant == "backup":
        path = Path(metadata["backup_path"])
    elif variant in {"working", "edited"}:
        content = Path(metadata["content_path"])
        files = [item for item in content.rglob("*") if item.is_file()]
        if metadata.get("kind") != "save_bundle" and len(files) == 1:
            path = files[0]
        else:
            export_dir = Path(metadata["root"]) / "exportaciones"
            export_dir.mkdir(exist_ok=True)
            path = export_dir / "save-editado.zip"
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for item in files:
                    archive.write(item, item.relative_to(content).as_posix())
    elif variant == "original":
        path = Path(metadata["original_path"])
    else:
        raise ValueError("La variante solicitada no es válida")
    if not path.exists():
        raise FileNotFoundError("El archivo ya no existe")
    return path


def restore_session(session_id: str) -> dict:
    metadata = get_session(session_id)
    backup = Path(metadata["backup_path"])
    original = Path(metadata["original_path"])
    content = Path(metadata["content_path"])
    if not backup.exists():
        raise FileNotFoundError("La copia de seguridad ya no existe")
    shutil.copy2(backup, original)
    if content.exists():
        shutil.rmtree(content)
    content.mkdir(parents=True)
    if metadata.get("kind") == "save_bundle":
        _safe_extract_saves(backup, content)
    else:
        shutil.copy2(backup, content / original.name)
    metadata["restored_at"] = utc_now()
    _write_json(Path(metadata["root"]) / "session.json", metadata)
    return public_session(metadata)


def open_session_folder(session_id: str) -> dict:
    metadata = get_session(session_id)
    path = Path(metadata["content_path"])
    if os.name != "nt":
        raise RuntimeError("Abrir carpeta está disponible en Windows")
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except OSError as exc:
        raise RuntimeError(f"Windows no pudo abrir la carpeta de la sesión: {exc}") from exc
    return {"opened": True, "path": str(path)}


def select_windows_asset(release: dict) -> dict:
    assets = release.get("assets") or []
    candidates: list[tuple[int, dict]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        lower = name.lower()
        url = str(asset.get("browser_download_url") or "")
        if not name or not url or any(x in lower for x in (".sig", ".sha256", ".blockmap", "appimage", ".deb", ".dmg")):
            continue
        score = 0
        if lower.endswith(".zip"):
            score += 60
        if lower.endswith(".exe"):
            score += 35
        if lower.endswith(".msi"):
            score += 20
        if "portable" in lower:
            score += 50
        if any(token in lower for token in ("windows", "win64", "x64", "win")):
            score += 35
        if "setup" in lower or "installer" in lower:
            score -= 15
        if score > 0:
            candidates.append((score, asset))
    if not candidates:
        raise RuntimeError("La publicación no contiene un editor portátil compatible con Windows")
    candidates.sort(key=lambda item: (item[0], int(item[1].get("size") or 0)), reverse=True)
    return candidates[0][1]


def _read_editor_meta() -> dict:
    if not EDITOR_META.exists():
        return {}
    try:
        value = json.loads(EDITOR_META.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def editor_status(check_online: bool = False) -> dict:
    ensure_workspace_dirs()
    meta = _read_editor_meta()
    executable = Path(str(meta.get("executable") or "")) if meta.get("executable") else None
    status = {
        "installed": bool(executable and executable.exists()),
        "installed_version": str(meta.get("version") or ""),
        "executable": str(executable) if executable and executable.exists() else "",
        "latest_version": "",
        "update_available": False,
        "source": "Palworld Save Pal",
        "license": "MIT",
    }
    if check_online:
        release = _json_request(PSP_RELEASE_API)
        latest = str(release.get("tag_name") or "")
        status["latest_version"] = latest
        status["update_available"] = bool(latest and latest != status["installed_version"])
    return status


def _download(url: str, target: Path, max_bytes: int = 1_500_000_000) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "Dimitry-Hub-Palworld/1.1"})
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(req, timeout=240) as response, target.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise RuntimeError("El componente descargado supera el límite de seguridad")
            digest.update(chunk)
            output.write(chunk)
    return size, digest.hexdigest()


def _safe_extract_tool(archive: Path, target: Path) -> None:
    root = target.resolve()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            destination = (target / member.filename.replace("\\", "/")).resolve()
            if root != destination and root not in destination.parents:
                raise RuntimeError("El editor contiene una ruta insegura")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)


def _find_editor_executable(root: Path) -> Path:
    candidates: list[tuple[int, Path]] = []
    for path in root.rglob("*.exe"):
        lower = path.name.lower()
        if any(token in lower for token in ("unins", "updater", "crash", "webview")):
            continue
        score = 0
        if "palworld" in lower:
            score += 20
        if "save" in lower:
            score += 15
        if "pal" in lower or "psp" in lower:
            score += 8
        candidates.append((score, path))
    if not candidates:
        raise RuntimeError("No se encontró el ejecutable del editor dentro del paquete")
    candidates.sort(key=lambda item: (item[0], item[1].stat().st_size), reverse=True)
    return candidates[0][1]


def install_editor_latest() -> dict:
    ensure_workspace_dirs()
    release = _json_request(PSP_RELEASE_API, timeout=45)
    version = str(release.get("tag_name") or "latest")
    asset = select_windows_asset(release)
    asset_name = safe_filename(str(asset.get("name") or "editor.zip"))
    asset_url = str(asset.get("browser_download_url") or "")
    if not asset_url:
        raise RuntimeError("La publicación no ofrece una dirección de descarga")

    with tempfile.TemporaryDirectory(prefix="dimitry-psp-") as temp:
        archive = Path(temp) / asset_name
        size, digest = _download(asset_url, archive)
        declared = str(asset.get("digest") or "")
        if declared.startswith("sha256:") and declared.split(":", 1)[1].lower() != digest.lower():
            raise RuntimeError("La verificación SHA-256 del editor no coincide")
        version_dir = TOOLS_DIR / re.sub(r"[^A-Za-z0-9._-]+", "_", version)
        if version_dir.exists():
            shutil.rmtree(version_dir)
        version_dir.mkdir(parents=True)
        if archive.suffix.lower() == ".zip":
            _safe_extract_tool(archive, version_dir)
            executable = _find_editor_executable(version_dir)
        elif archive.suffix.lower() == ".exe":
            executable = version_dir / asset_name
            shutil.copy2(archive, executable)
        else:
            raise RuntimeError("La publicación solo ofrece un instalador MSI; no se encontró versión portátil")

    meta = {
        "version": version,
        "asset": asset_name,
        "asset_url": asset_url,
        "size": size,
        "sha256": digest,
        "executable": str(executable),
        "installed_at": utc_now(),
        "repository": "oMaN-Rod/palworld-save-pal",
        "license": "MIT",
    }
    _write_json(EDITOR_META, meta)
    return {**editor_status(False), "latest_version": version, "installed_version": version, "asset": asset_name}


def _wait_for_editor(timeout: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() <= deadline:
        try:
            with socket.create_connection(("127.0.0.1", 5174), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def launch_editor(session_id: str = "") -> dict:
    status = editor_status(False)
    if not status["installed"]:
        raise RuntimeError("Primero instala el componente Palworld Save Pal desde Dimitry Hub")
    executable = Path(status["executable"])
    folder = ""
    folder_opened = False
    if session_id:
        metadata = get_session(session_id)
        folder = str(Path(metadata["content_path"]))
        if os.name == "nt":
            try:
                os.startfile(folder)  # type: ignore[attr-defined]
                folder_opened = True
            except OSError:
                # El editor todavía debe abrir aunque Windows no permita mostrar Explorer.
                folder_opened = False
    already_running = _wait_for_editor()
    if not already_running:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.Popen([str(executable)], cwd=str(executable.parent), creationflags=flags)
        except OSError as exc:
            raise RuntimeError(f"Windows no pudo abrir Palworld Save Pal: {exc}") from exc
    ready = already_running or _wait_for_editor(12.0)
    if not ready:
        raise RuntimeError("Palworld Save Pal se inició, pero su interfaz local no respondió en el tiempo esperado")
    editor_url = "http://127.0.0.1:5174/"
    message = "El editor completo está preparado."
    if folder:
        message += (
            " La carpeta de la sesión también se abrió para elegir el save."
            if folder_opened
            else f" El save preparado está en: {folder}"
        )
    return {
        "launched": True,
        "version": status["installed_version"],
        "session_folder": folder,
        "folder_opened": folder_opened,
        "editor_url": editor_url,
        "already_running": already_running,
        "message": message,
    }


def offline_knowledge_answer(question: str, results: list[dict]) -> str:
    if not results:
        return (
            "No encontré una coincidencia suficiente en la biblioteca local de Palworld. "
            "Prueba con el nombre exacto del Pal, habilidad, pasiva, archivo o código interno."
        )
    top_score = float(results[0].get("score") or 0)
    selected = (
        [
            item for item in results
            if float(item.get("score") or 0) >= max(1.0, top_score * 0.55)
        ][:4]
        if top_score > 0
        else results[:4]
    )
    lines = ["Guía local de Palworld basada en las entradas más pertinentes:", ""]
    for index, item in enumerate(selected, 1):
        title = re.sub(r"\s+", " ", str(item.get("title") or "Fuente local")).strip()
        category = re.sub(r"\s+", " ", str(item.get("category") or "general")).strip()
        snippet = re.sub(r"\s+", " ", str(item.get("snippet") or "")).strip()
        max_length = 1200 if item.get("source") == "base-guide" else 420
        if len(snippet) > max_length:
            snippet = snippet[:max_length - 3].rstrip() + "…"
        lines.append(f"{index}. {title} [{category}]")
        lines.append(snippet or "La entrada coincide con la búsqueda, pero no contiene un resumen legible.")
        lines.append("")
    lines.append("Verifica coordenadas, estadísticas, botín y precios dentro de la versión instalada: las guías comunitarias pueden quedar desactualizadas.")
    return "\n".join(lines)
