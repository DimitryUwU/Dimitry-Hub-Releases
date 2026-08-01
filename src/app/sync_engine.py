from __future__ import annotations

import html
import hashlib
import json
import re
import tempfile
import traceback
import threading
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from .database import KNOWLEDGE_DIR, connect, utc_now
from .internet import download_to_file, http_get
from .knowledge import import_bundle, upsert_entry

GITHUB_API_VERSION = "2022-11-28"
PALWORLD_EDITOR_SOURCE = "github:oman-rod:palworld-save-pal"
_SYNC_LOCK = threading.Lock()


def _settings() -> dict[str, str]:
    with connect() as db:
        return {row["key"]: row["value"] for row in db.execute("SELECT key,value FROM settings")}


def _source(source_key: str) -> dict:
    with connect() as db:
        row = db.execute("SELECT * FROM update_sources WHERE source_key=?", (source_key,)).fetchone()
    if not row:
        raise RuntimeError(f"Fuente no registrada: {source_key}")
    return dict(row)


def _update_source(source_key: str, **values: object) -> None:
    allowed = {
        "etag", "last_modified", "current_version", "current_digest", "last_checked",
        "last_changed", "status", "error", "metadata_json", "url", "enabled",
    }
    clean = {key: value for key, value in values.items() if key in allowed}
    if not clean:
        return
    assignments = ",".join(f"{key}=?" for key in clean)
    with connect() as db:
        db.execute(f"UPDATE update_sources SET {assignments} WHERE source_key=?", [*clean.values(), source_key])


def _event(run_id: int, source_key: str, title: str, details: str = "", severity: str = "info") -> None:
    with connect() as db:
        db.execute(
            "INSERT INTO sync_events(run_id,source_key,severity,title,details,created_at) VALUES(?,?,?,?,?,?)",
            (run_id, source_key, severity, title, details[:12000], utc_now()),
        )


def _clean_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"\[(?:/?(?:b|i|u|h\d|list|table|tr|td|url|img|previewyoutube|code))[^\]]*\]", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def classify_palworld_news(title: str, content: str) -> list[str]:
    text = f"{title} {content}".lower()
    rules = {
        "nuevos-pals": ("new pal", "new pals", "nuevo pal", "nuevos pals", "pal added", "pals added"),
        "pasivas-habilidades": ("passive", "pasiva", "skill", "habilidad", "partner skill"),
        "objetos": ("item", "items", "objeto", "weapon", "arma", "armor", "armadura"),
        "mapa-bases": ("island", "isla", "map", "mapa", "base", "location", "ubicación", "region"),
        "estructura-save": ("save data", "save file", "guardado", "migration", "migración"),
        "balance": ("balance", "adjusted", "ajust", "nerf", "buff", "stat"),
        "correcciones": ("fixed", "fix", "bug", "crash", "correg", "error"),
    }
    categories = [name for name, words in rules.items() if any(word in text for word in words)]
    return categories or ["noticia-general"]


def _github_repo(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if parsed.hostname not in {"github.com", "www.github.com"} or len(parts) < 2:
        raise RuntimeError("La fuente del editor debe ser un repositorio público de GitHub")
    return parts[0], parts[1].removesuffix(".git")


def _github_headers(settings: dict[str, str]) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }
    token = settings.get("github_token", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def sync_steam_news(run_id: int) -> dict:
    source_key = "palworld-steam-news"
    source = _source(source_key)
    checked = utc_now()
    try:
        result = http_get(
            source["url"], timeout=60, max_bytes=18 * 1024 * 1024,
            etag=source["etag"], last_modified=source["last_modified"],
        )
        if result.not_modified:
            _update_source(source_key, last_checked=checked, status="current", error="")
            _event(run_id, source_key, "Noticias sin cambios", "Steam devolvió 304 Not Modified.")
            return {"source": source_key, "changed": False, "updated": 0}
        payload = result.json()
        items = payload.get("appnews", {}).get("newsitems", []) if isinstance(payload, dict) else []
        added = 0
        updated = 0
        important = 0
        for item in items:
            gid = str(item.get("gid") or item.get("url") or "")
            if not gid:
                continue
            title = _clean_text(item.get("title", "Actualización de Palworld"))
            content = _clean_text(item.get("contents", ""))
            categories = classify_palworld_news(title, content)
            category = categories[0]
            with connect() as db:
                previous = db.execute(
                    "SELECT content FROM knowledge_entries WHERE domain='palworld' AND source='steam-news-palworld' AND source_key=?",
                    (gid,),
                ).fetchone()
            if previous is None:
                added += 1
            elif previous["content"] != content:
                updated += 1
            if any(cat in categories for cat in ("nuevos-pals", "pasivas-habilidades", "estructura-save", "objetos", "mapa-bases")):
                important += 1
            upsert_entry(
                domain="palworld", category=category, source="steam-news-palworld", source_key=gid,
                title=title, content=content,
                metadata={
                    "url": item.get("url", ""), "date": item.get("date"),
                    "feedlabel": item.get("feedlabel", ""), "author": item.get("author", ""),
                    "classifications": categories, "official": True,
                },
            )
        changed = bool(added or updated)
        _update_source(
            source_key,
            etag=result.headers.get("etag", ""),
            last_modified=result.headers.get("last-modified", ""),
            current_version=str(max((item.get("date", 0) for item in items), default="")),
            last_checked=checked,
            last_changed=checked if changed else source["last_changed"],
            status="updated" if changed else "current",
            error="",
            metadata_json=json.dumps({"items": len(items), "added": added, "updated": updated, "important": important}, ensure_ascii=False),
        )
        _event(
            run_id, source_key,
            "Noticias oficiales actualizadas" if changed else "Noticias revisadas",
            f"{len(items)} publicaciones revisadas; {added} nuevas; {updated} modificadas; {important} relevantes para datos del juego.",
            "success" if changed else "info",
        )
        return {"source": source_key, "changed": changed, "updated": added + updated, "important": important}
    except Exception as exc:
        _update_source(source_key, last_checked=checked, status="error", error=str(exc))
        _event(run_id, source_key, "No se pudieron actualizar las noticias", str(exc), "error")
        return {"source": source_key, "changed": False, "error": str(exc)}


def _resolve_github_version(owner: str, repo: str, settings: dict[str, str]) -> dict:
    headers = _github_headers(settings)
    safe_owner = quote(owner, safe="")
    safe_repo = quote(repo, safe="")
    release_url = f"https://api.github.com/repos/{safe_owner}/{safe_repo}/releases/latest"
    try:
        release = http_get(release_url, timeout=45, max_bytes=3 * 1024 * 1024, headers=headers).json()
        if isinstance(release, dict) and release.get("tag_name"):
            tag = str(release["tag_name"])
            safe_tag = quote(tag, safe="")
            return {
                "version": tag,
                "download_url": f"https://codeload.github.com/{safe_owner}/{safe_repo}/zip/refs/tags/{safe_tag}",
                "fallback_url": f"https://github.com/{safe_owner}/{safe_repo}/archive/refs/tags/{safe_tag}.zip",
                "html_url": release.get("html_url", ""),
                "published_at": release.get("published_at", ""),
                "mode": "release",
                "notes": release.get("body", "") or "",
            }
    except Exception:
        pass
    repo_info = http_get(
        f"https://api.github.com/repos/{safe_owner}/{safe_repo}", timeout=45, max_bytes=2 * 1024 * 1024, headers=headers,
    ).json()
    if not isinstance(repo_info, dict):
        raise RuntimeError("GitHub no devolvió los datos esperados del repositorio")
    branch = str(repo_info.get("default_branch") or "main")
    commit = http_get(
        f"https://api.github.com/repos/{safe_owner}/{safe_repo}/commits/{quote(branch, safe='')}", timeout=45, max_bytes=3 * 1024 * 1024, headers=headers,
    ).json()
    sha = str(commit.get("sha") or "") if isinstance(commit, dict) else ""
    if not sha:
        raise RuntimeError("No se pudo determinar la versión actual del editor")
    return {
        "version": sha,
        "download_url": f"https://codeload.github.com/{safe_owner}/{safe_repo}/zip/{sha}",
        "fallback_url": f"https://github.com/{safe_owner}/{safe_repo}/archive/{sha}.zip",
        "html_url": repo_info.get("html_url", ""),
        "published_at": commit.get("commit", {}).get("committer", {}).get("date", "") if isinstance(commit, dict) else "",
        "mode": "commit",
        "notes": commit.get("commit", {}).get("message", "") if isinstance(commit, dict) else "",
        "branch": branch,
    }

def sync_palworld_editor(run_id: int) -> dict:
    """Comprueba la versión del editor sin descargar e indexar todo su código fuente.

    El componente ejecutable se administra por separado desde Palworld Workspace.
    """
    source_key = "palworld-editor-github"
    source = _source(source_key)
    settings = _settings()
    configured = settings.get("palworld_repo_url", "").strip() or source["url"]
    if "/archive/" in configured:
        configured = configured.split("/archive/", 1)[0]
    checked = utc_now()
    try:
        owner, repo = _github_repo(configured)
        resolved = _resolve_github_version(owner, repo, settings)
        version = str(resolved["version"])
        changed = source["current_version"] != version
        _update_source(
            source_key,
            url=configured,
            current_version=version,
            last_checked=checked,
            last_changed=checked if changed else source["last_changed"],
            status="updated" if changed else "current",
            error="",
            metadata_json=json.dumps(resolved, ensure_ascii=False),
        )
        _event(
            run_id, source_key,
            "Nueva versión del editor detectada" if changed else "Editor comprobado",
            f"Palworld Save Pal {version[:32]}. El componente se instala desde Palworld Workspace.",
            "success" if changed else "info",
        )
        return {"source": source_key, "changed": changed, "version": version}
    except Exception as exc:
        _update_source(source_key, url=configured, last_checked=checked, status="error", error=str(exc))
        _event(run_id, source_key, "No se pudo comprobar el editor", str(exc), "error")
        return {"source": source_key, "changed": False, "error": str(exc)}

def run_full_sync(trigger: str = "manual") -> dict:
    if not _SYNC_LOCK.acquire(blocking=False):
        with connect() as db:
            running = db.execute("SELECT * FROM sync_runs WHERE status='running' ORDER BY id DESC LIMIT 1").fetchone()
        return {"status": "running", "changed_count": 0, "message": "Ya hay una actualización en curso.", "run_id": running["id"] if running else None, "results": []}
    try:
        return _run_full_sync_locked(trigger)
    finally:
        _SYNC_LOCK.release()


def _run_full_sync_locked(trigger: str = "manual") -> dict:
    started = utc_now()
    with connect() as db:
        cursor = db.execute(
            "INSERT INTO sync_runs(trigger,status,started_at) VALUES(?, 'running', ?)",
            (trigger, started),
        )
        run_id = cursor.lastrowid
    results: list[dict] = []
    status = "success"
    error = ""
    try:
        results.append(sync_steam_news(run_id))
        results.append(sync_palworld_editor(run_id))
        if all(item.get("error") for item in results):
            status = "error"
        elif any(item.get("error") for item in results):
            status = "partial"
    except Exception as exc:
        status = "error"
        error = str(exc)
        _event(run_id, "system", "Error inesperado durante la sincronización", traceback.format_exc(), "error")
    changed_count = sum(1 for item in results if item.get("changed"))
    finished = utc_now()
    summary = {"run_id": run_id, "status": status, "changed_count": changed_count, "results": results, "started_at": started, "finished_at": finished}
    with connect() as db:
        db.execute(
            "UPDATE sync_runs SET status=?,finished_at=?,changed_count=?,summary_json=?,error=? WHERE id=?",
            (status, finished, changed_count, json.dumps(summary, ensure_ascii=False), error, run_id),
        )
        db.execute(
            "INSERT INTO settings(key,value) VALUES('last_full_sync',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (finished,),
        )
        # Preserve the v0.4 status fields for existing UI/data compatibility.
        editor = next((item for item in results if item.get("source") == "palworld-editor-github"), {})
        news = next((item for item in results if item.get("source") == "palworld-steam-news"), {})
        if not editor.get("error"):
            db.execute("INSERT INTO settings(key,value) VALUES('last_palworld_sync',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (finished,))
        if not news.get("error"):
            db.execute("INSERT INTO settings(key,value) VALUES('last_palworld_news_sync',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (finished,))
    return summary


def sync_status() -> dict:
    with connect() as db:
        sources = [dict(row) for row in db.execute("SELECT * FROM update_sources ORDER BY domain,name")]
        runs = [dict(row) for row in db.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 12")]
        events = [dict(row) for row in db.execute("SELECT * FROM sync_events ORDER BY id DESC LIMIT 30")]
        settings = {row["key"]: row["value"] for row in db.execute("SELECT key,value FROM settings")}
    return {"sources": sources, "runs": runs, "events": events, "settings": settings}


def auto_sync_due() -> bool:
    settings = _settings()
    if settings.get("internet_enabled", "1") != "1" or settings.get("sync_on_startup", "1") != "1":
        return False
    if settings.get("auto_sync_mode", "safe") == "manual":
        return False
    try:
        hours = max(1, min(168, int(settings.get("auto_sync_interval_hours", "24"))))
    except ValueError:
        hours = 24
    last = settings.get("last_full_sync", "")
    if not last:
        return True
    try:
        moment = datetime.fromisoformat(last.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - moment >= timedelta(hours=hours)
    except ValueError:
        return True
