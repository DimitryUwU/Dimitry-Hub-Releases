from __future__ import annotations

import difflib
import io
import os
import json
import html
import re
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .analyzer import analyze_bundle
from .ai import (
    AIError,
    compatible_models as ai_compatible_models,
    generate as ai_generate,
    ollama_models as ai_ollama_models,
    openai_models as ai_openai_models,
    provider_status as ai_provider_status,
)
from .database import (
    ASSET_ROOT,
    BACKUPS_DIR,
    EXPORTS_DIR,
    FILES_DIR,
    GENERATED_DIR,
    KNOWLEDGE_DIR,
    ROOT,
    atomic_write_bytes,
    connect,
    init_db,
    log_activity,
    safe_filename,
    sha256_file,
    utc_now,
)
from .documents import create_legal_docx, create_monograph_docx
from .extractors import extract_text
from .knowledge import import_bundle, search_knowledge, source_summary, upsert_entry
from .research import bibliography_audit, crossref_doi, crossref_search, import_web_source, list_research_items, save_research_item, suggest_academic_sources
from .sync_engine import auto_sync_due, run_full_sync, sync_status
from .secrets import delete_secret, set_secret
from .version import APP_VERSION
from .app_updates import check_now as check_app_update, install_available_update, startup_auto_update, status as app_update_status
from .palworld_workspace import (
    INCOMING_DIR, MAX_UPLOAD_BYTES, create_session_from_path, delete_session,
    editor_status, ensure_workspace_dirs, get_session, install_editor_latest,
    launch_editor, list_sessions, offline_knowledge_answer, open_session_folder,
    restore_session, session_file,
)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    threading.Thread(target=_startup_sync, name="dimitry-sync", daemon=True).start()
    threading.Thread(target=startup_auto_update, name="dimitry-app-update", daemon=True).start()
    yield


app = FastAPI(title="Dimitry Hub", version=APP_VERSION, lifespan=lifespan)
STATIC_DIR = ASSET_ROOT / "app" / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": APP_VERSION}


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="General", max_length=60)
    description: str = Field(default="", max_length=5000)
    permanent_instructions: str = Field(default="", max_length=20000)
    accent: str = Field(default="blue", max_length=20)


class NoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(default="", max_length=50000)


class SettingsIn(BaseModel):
    theme: str | None = None
    ollama_url: str | None = None
    ollama_model: str | None = None
    display_name: str | None = None
    simple_mode: str | None = None
    palworld_repo_url: str | None = None
    auto_sync_mode: str | None = None
    auto_sync_interval_hours: str | None = None
    sync_on_startup: str | None = None
    crossref_email: str | None = None
    github_token: str | None = None
    internet_enabled: str | None = None
    font_style: str | None = None
    ai_mode: str | None = None
    ai_provider: str | None = None
    ai_fallback_local: str | None = None
    ai_web_search_default: str | None = None
    ai_reasoning_effort: str | None = None
    ai_pro_mode: str | None = None
    openai_model: str | None = None
    openai_general_model: str | None = None
    openai_research_model: str | None = None
    openai_code_model: str | None = None
    openai_legal_model: str | None = None
    openai_study_model: str | None = None
    compatible_base_url: str | None = None
    compatible_model: str | None = None
    compatible_no_key: str | None = None
    ollama_think: str | None = None


class PalEditorLaunchRequest(BaseModel):
    session_id: str = Field(default="", max_length=80)


class OllamaRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=120000)
    model: str | None = None
    system: str | None = None


class StudyRequest(BaseModel):
    kind: str = "ficha"
    content: str = Field(min_length=1, max_length=180000)
    project_id: int | None = None
    use_web: bool = False


class KnowledgeQuestion(BaseModel):
    domain: str = Field(pattern=r"^[a-z0-9_-]{2,30}$")
    question: str = Field(min_length=2, max_length=20000)
    mode: str = "answer"


class MonographRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    author: str = ""
    institution: str = "Universidad TecnolÃ³gica de los Andes"
    course: str = ""
    teacher: str = ""
    city: str = "Abancay, PerÃº"
    date: str = ""
    content: str = Field(min_length=1, max_length=250000)
    bibliography: str = Field(default="", max_length=100000)
    use_ai: bool = True
    use_web: bool = False


class LegalRequest(BaseModel):
    kind: str = "solicitud"
    fields: dict = Field(default_factory=dict)
    facts: str = Field(default="", max_length=100000)
    legal_basis: str = Field(default="", max_length=50000)
    evidence: str = Field(default="", max_length=50000)
    request_text: str = Field(default="", max_length=50000)
    use_ai: bool = True
    verify_web: bool = False


class WebImportRequest(BaseModel):
    url: str = Field(min_length=8, max_length=3000)
    domain: str = Field(default="research", pattern=r"^[a-z0-9_-]{2,30}$")
    title: str = Field(default="", max_length=300)


class CrossrefSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    rows: int = Field(default=8, ge=1, le=20)


class ResearchSaveRequest(BaseModel):
    item: dict


class BibliographyAuditRequest(BaseModel):
    content: str = Field(default="", max_length=300000)
    bibliography: str = Field(default="", max_length=150000)


class AutoResearchRequest(BaseModel):
    title: str = Field(default="", max_length=500)
    content: str = Field(default="", max_length=300000)
    rows: int = Field(default=8, ge=1, le=20)


class AISecretRequest(BaseModel):
    provider: str = Field(pattern=r"^(openai|compatible)$")
    api_key: str = Field(min_length=8, max_length=5000)


class AIChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=120000)
    thread_id: int | None = None
    domain: str = Field(default="general", pattern=r"^[a-z0-9_-]{2,30}$")
    web_search: bool = False
    include_library: bool = True
    provider: str | None = Field(default=None, pattern=r"^(openai|ollama|compatible)$")


def _startup_sync() -> None:
    time.sleep(1.5)
    try:
        if auto_sync_due():
            run_full_sync("startup")
    except Exception:
        # La aplicaciÃ³n debe abrir aunque una fuente externa falle.
        pass


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/app-update/status")
def get_app_update_status() -> dict:
    return app_update_status()


@app.post("/api/app-update/check")
def check_for_app_update() -> dict:
    return check_app_update()


@app.post("/api/app-update/install")
def install_app_update() -> dict:
    result = install_available_update()
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    return result


def _shutdown_after_response() -> None:
    time.sleep(0.8)
    os._exit(0)


@app.post("/api/system/shutdown")
def shutdown_system() -> dict:
    threading.Thread(target=_shutdown_after_response, name="dimitry-shutdown", daemon=True).start()
    return {"status": "closing"}


@app.get("/api/dashboard")
def dashboard() -> dict:
    with connect() as db:
        project_count = db.execute("SELECT COUNT(*) n FROM projects WHERE archived=0").fetchone()["n"]
        file_count = db.execute("SELECT COUNT(*) n FROM files").fetchone()["n"]
        note_count = db.execute("SELECT COUNT(*) n FROM notes").fetchone()["n"]
        total_size = db.execute("SELECT COALESCE(SUM(size),0) n FROM files").fetchone()["n"]
        monographs = db.execute("SELECT COUNT(*) n FROM monographs").fetchone()["n"]
        recent = [dict(r) for r in db.execute(
            "SELECT activity.*, projects.name project_name FROM activity LEFT JOIN projects ON projects.id=activity.project_id ORDER BY activity.id DESC LIMIT 8"
        )]
    return {
        "projects": project_count,
        "files": file_count,
        "notes": note_count,
        "storage": total_size,
        "monographs": monographs,
        "recent": recent,
    }


@app.get("/api/projects")
def list_projects() -> list[dict]:
    with connect() as db:
        rows = db.execute(
            """
            SELECT p.*,
                   (SELECT COUNT(*) FROM files f WHERE f.project_id=p.id) file_count,
                   (SELECT COUNT(*) FROM notes n WHERE n.project_id=p.id) note_count
            FROM projects p
            WHERE archived=0
            ORDER BY p.updated_at DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/projects")
def create_project(payload: ProjectIn) -> dict:
    now = utc_now()
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO projects(name, category, description, permanent_instructions, accent, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (payload.name.strip(), payload.category.strip(), payload.description, payload.permanent_instructions, payload.accent, now, now),
        )
        log_activity(db, cursor.lastrowid, "Proyecto creado", payload.name)
        row = db.execute("SELECT * FROM projects WHERE id=?", (cursor.lastrowid,)).fetchone()
    return dict(row)


@app.get("/api/projects/{project_id}")
def get_project(project_id: int) -> dict:
    with connect() as db:
        project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            raise HTTPException(404, "Proyecto no encontrado")
        files = [dict(r) for r in db.execute("SELECT * FROM files WHERE project_id=? ORDER BY id DESC", (project_id,))]
        notes = [dict(r) for r in db.execute("SELECT * FROM notes WHERE project_id=? ORDER BY updated_at DESC", (project_id,))]
    return {"project": dict(project), "files": files, "notes": notes}


@app.put("/api/projects/{project_id}")
def update_project(project_id: int, payload: ProjectIn) -> dict:
    with connect() as db:
        if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            raise HTTPException(404, "Proyecto no encontrado")
        db.execute(
            """
            UPDATE projects SET name=?, category=?, description=?, permanent_instructions=?, accent=?, updated_at=?
            WHERE id=?
            """,
            (payload.name.strip(), payload.category.strip(), payload.description, payload.permanent_instructions, payload.accent, utc_now(), project_id),
        )
        log_activity(db, project_id, "Proyecto actualizado", payload.name)
        project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    return dict(project)


@app.delete("/api/projects/{project_id}")
def archive_project(project_id: int) -> dict:
    with connect() as db:
        db.execute("UPDATE projects SET archived=1, updated_at=? WHERE id=?", (utc_now(), project_id))
        log_activity(db, project_id, "Proyecto archivado", "Puede restaurarse desde la base local.")
    return {"ok": True}


@app.post("/api/projects/{project_id}/notes")
def create_note(project_id: int, payload: NoteIn) -> dict:
    now = utc_now()
    with connect() as db:
        if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            raise HTTPException(404, "Proyecto no encontrado")
        cursor = db.execute(
            "INSERT INTO notes(project_id,title,body,created_at,updated_at) VALUES (?,?,?,?,?)",
            (project_id, payload.title, payload.body, now, now),
        )
        db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
        log_activity(db, project_id, "Nota aÃ±adida", payload.title)
        note = db.execute("SELECT * FROM notes WHERE id=?", (cursor.lastrowid,)).fetchone()
    return dict(note)


@app.put("/api/notes/{note_id}")
def update_note(note_id: int, payload: NoteIn) -> dict:
    with connect() as db:
        note = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        if not note:
            raise HTTPException(404, "Nota no encontrada")
        db.execute("UPDATE notes SET title=?, body=?, updated_at=? WHERE id=?", (payload.title, payload.body, utc_now(), note_id))
        log_activity(db, note["project_id"], "Nota actualizada", payload.title)
        updated = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
    return dict(updated)


@app.delete("/api/notes/{note_id}")
def delete_note(note_id: int) -> dict:
    with connect() as db:
        note = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        if note:
            db.execute("DELETE FROM notes WHERE id=?", (note_id,))
            log_activity(db, note["project_id"], "Nota eliminada", note["title"])
    return {"ok": True}


@app.post("/api/projects/{project_id}/files")
async def upload_file(project_id: int, upload: UploadFile = File(...)) -> dict:
    with connect() as db:
        if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            raise HTTPException(404, "Proyecto no encontrado")
    content = await upload.read()
    if len(content) > 300 * 1024 * 1024:
        raise HTTPException(413, "El archivo supera el lÃ­mite de 300 MB")
    name = safe_filename(upload.filename or "archivo")
    project_dir = FILES_DIR / str(project_id)
    target = project_dir / f"{utc_now().replace(':','-')}_{name}"
    atomic_write_bytes(target, content)
    digest = sha256_file(target)
    extracted = extract_text(target)
    now = utc_now()
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO files(project_id, original_name, stored_path, media_type, size, sha256, extracted_text, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (project_id, name, str(target), uë®û¶‰žËkºwµçI…Á¡}¥€ôÕÉÍ½È¹±…ÍÑÉ½Ý¥4(€€€É•ÑÕÉ¸ì4(€€€€€€€€‰¥ˆèµ½¹½É…Á¡}¥°4(€€€€€€€€‰ÍÑÉÕÑÕÉ•‘}Ñ•áÐˆèÍÑÉÕÑÕÉ•°4(€€€€€€€€‰‘½Ý¹±½…‘}ÕÉ°ˆè˜ˆ½…Á¤½•¹•É…Ñ•½íÑ…É•Ð¹¹…µ•ôˆ°4(€€€€€€€€‰™¥±•¹…µ”ˆèÑ…É•Ð¹¹…µ”°4(€€€€€€€€‰Ý…É¹¥¹œˆèÝ…É¹¥¹œ°4(€€€€€€€€‰ÁÉ½Ù¥‘•Èˆè…¥}¥¹™¼¹•Ð ‰ÁÉ½Ù¥‘•Èˆ°€ˆˆ¤°4(€€€€€€€€‰µ½‘•°ˆè…¥}¥¹™¼¹•Ð ‰µ½‘•°ˆ°€ˆˆ¤°4(€€€€€€€€‰Í½ÕÉ•Ìˆè…¥}¥¹™¼¹•Ð ‰Í½ÕÉ•Ìˆ°mt¤°4(€€€ô4(4(4)…ÁÀ¹Á½ÍÐ ˆ½…Á¤½±•…°½É•…Ñ”ˆ¤)‘•˜±•…±}É•…Ñ”¡Á…å±½…è1•…±I•ÅÕ•ÍÐ¤€´ø‘¥Ðè(€€€­¥¹€ôÁ…å±½…¹­¥¹¹É•Á±…” ˆ´ˆ°€‰|ˆ¤(€€€™¥•±‘Ì€ôí­•äèÍÑÈ¡Ù…±Õ”¤¹ÍÑÉ¥À ¤™½È­•ä°Ù…±Õ”¥¸Á…å±½…¹™¥•±‘Ì¹¥Ñ•µÌ ¥ô(€€€½µµ½¹}É•ÅÕ¥É•€ôì(€€€€€€€€‰…ÕÑ¡½É¥Ñäˆè€‰…ÕÑ½É¥‘…°•¹Ñ¥‘…Ô½É…¹¥é…§Í¸ˆ°(€€€€€€€€‰…ÁÁ±¥…¹Ðˆè€‰¹½µ‰É”‘”±„Á•ÉÍ½¹„Í½±¥¥Ñ…¹Ñ”¼É•ÍÁ½¹Í…‰±”ˆ°(€€€€€€€€‰…‘‘É•ÍÌˆè€‰‘½µ¥¥±¥¼¼±Õ…Èˆ°(€€€€€€€€‰¥Ñå}‘…Ñ”ˆè€‰¥Õ‘…ä™•¡„ˆ°(€€€€€€€€‰ÍÕµ¥±±„ˆè€‰ÍÕµ¥±±„Ô½‰©•Ñ¼ˆ°(€€€ô(€€€µ¥ÍÍ¥¹œ€ôm±…‰•°™½È­•ä°±…‰•°¥¸½µµ½¹}É•ÅÕ¥É•¹¥Ñ•µÌ ¤¥˜¹½Ð™¥•±‘Ì¹•Ð¡­•ä¥t(€€€¥˜­¥¹€ôô€‰…Ñ…}É•Õ¹¥½¸ˆè(€€€€€€€¥˜¹½Ð™¥•±‘Ì¹•Ð ‰Á…ÉÑ¥¥Á…¹ÑÌˆ¤è(€€€€€€€€€€€µ¥ÍÍ¥¹œ¹…ÁÁ•¹ ‰Á…ÉÑ¥¥Á…¹Ñ•Ìˆ¤(€€€€€€€¥˜¹½ÐÁ…å±½…¹É•ÅÕ•ÍÑ}Ñ•áÐ¹ÍÑÉ¥À ¤è(€€€€€€€€€€€µ¥ÍÍ¥¹œ¹…ÁÁ•¹ ‰…•¹‘„ˆ¤(€€€€€€€¥˜¹½ÐÁ…å±½…¹™…ÑÌ¹ÍÑÉ¥À ¤è(€€€€€€€€€€€µ¥ÍÍ¥¹œ¹…ÁÁ•¹ ‰‘•Í…ÉÉ½±±¼‘”±„É•Õ¹§Í¸ˆ¤(€€€€€€€¥˜¹½ÐÁ…å±½…¹•Ù¥‘•¹”¹ÍÑÉ¥À ¤è(€€€€€€€€€€€µ¥ÍÍ¥¹œ¹…ÁÁ•¹ ‰…Õ•É‘½Ìˆ¤(€€€•±Í”è(€€€€€€€‘¹¤€ôÉ”¹ÍÕˆ¡È‰qˆ°€ˆˆ°™¥•±‘Ì¹•Ð ‰‘¹¤ˆ°€ˆˆ¤¤(€€€€€€€¥˜±•¸¡‘¹¤¤€„ô€àè(€€€€€€€€€€€µ¥ÍÍ¥¹œ¹…ÁÁ•¹ ‰9$‘”½¡¼“µ¥Ñ½Ìˆ¤(€€€€€€€•±Í”è(€€€€€€€€€€€™¥•±‘Íl‰‘¹¤‰t€ô‘¹¤(€€€€€€€¥˜¹½ÐÁ…å±½…¹É•ÅÕ•ÍÑ}Ñ•áÐ¹ÍÑÉ¥À ¤è(€€€€€€€€€€€µ¥ÍÍ¥¹œ¹…ÁÁ•¹ ‰Á•Ñ¥Ñ½É¥¼ˆ¤(€€€€€€€¥˜¹½ÐÁ…å±½…¹™…ÑÌ¹ÍÑÉ¥À ¤è(€€€€€€€€€€€µ¥ÍÍ¥¹œ¹…ÁÁ•¹ ‰™Õ¹‘…µ•¹Ñ½Ì‘”¡•¡¼ˆ¤(€€€¥˜µ¥ÍÍ¥¹œè(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÈÈ°€‰½µÁ±•Ñ„…¹Ñ•Ì‘”•¹•É…Èè€ˆ€¬€ˆ°€ˆ¹©½¥¸¡µ¥ÍÍ¥¹œ¤€¬€ˆ¸ˆ¤((€€€¥˜­¥¹€ôô€‰…Ñ…}É•Õ¹¥½¸ˆè(€€€€€€€Á…ÉÑÌ€ôl(€€€€€€€€€€€€ ‰9ˆ°Á…å±½…¹É•ÅÕ•ÍÑ}Ñ•áÐ¤°(€€€€€€€€€€€€ ‰MII=11<1IU9'M8ˆ°Á…å±½…¹™…ÑÌ¤°(€€€€€€€€€€€€ ‰UI=Lˆ°Á…å±½…¹•Ù¥‘•¹”¤°(€€€€€€€€€€€€ ‰	M10<=U59Q0ˆ°Á…å±½…¹±•…±}‰…Í¥Ì¤°(€€€€€€€t(€€€•±Í”è(€€€€€€€Á…ÉÑÌ€ôl(€€€€€€€€€€€€ ‰AQ%Q=I%<ˆ°Á…å±½…¹É•ÅÕ•ÍÑ}Ñ•áÐ¤°(€€€€€€€€€€€€ ‰U959Q=L!!<ˆ°Á…å±½…¹™…ÑÌ¤°(€€€€€€€€€€€€ ‰U959Q=LI!<ˆ°Á…å±½…¹±•…±}‰…Í¥Ì¤°(€€€€€€€€€€€€ ‰5%=LAI=	Q=I%=Ld9a=Lˆ°Á…å±½…¹•Ù¥‘•¹”¤°(€€€€€€€t(€€€Á½ÁÕ±…Ñ•‘}Á…ÉÑÌ€ôl¡¡•…‘¥¹œ°½¹Ñ•¹Ð¹ÍÑÉ¥À ¤¤™½È¡•…‘¥¹œ°½¹Ñ•¹Ð¥¸Á…ÉÑÌ¥˜½¹Ñ•¹Ð¹ÍÑÉ¥À ¥t(€€€É½µ…¸€ô€ ‰$ˆ°€‰%$ˆ°€‰%%$ˆ°€‰%Xˆ¤(€€€‘É…™Ð€ô€‰q¸ˆ¹©½¥¸ (€€€€€€€¥Ñ•´(€€€€€€€™½È¥¹‘•à°€¡¡•…‘¥¹œ°½¹Ñ•¹Ð¤¥¸•¹Õµ•É…Ñ”¡Á½ÁÕ±…Ñ•‘}Á…ÉÑÌ¤(€€€€€€€™½È¥Ñ•´¥¸€¡˜‰íÉ½µ…¹m¥¹‘•áuô¸í¡•…‘¥¹ôˆ°½¹Ñ•¹Ð¤(€€€€¤(€€€Ý…É¹¥¹œ€ô€ˆˆ(€€€…¥}¥¹™¼è‘¥Ð€ôíô(€€€¥˜Á…å±½…¹ÕÍ•}…¤è(€€€€€€€ÁÉ½µÁÐ€ô˜ˆˆ‰I•‘…Ñ„Õ¸‰½ÉÉ…‘½È©ÕËµ‘¥¼Á•ÉÕ…¹¼‘•°Ñ¥Á¼€íÁ…å±½…¹­¥¹‘ôœ¸()I•±…Ì•ÍÑÉ¥Ñ…Ìè(´UÍ„ƒé¹¥…µ•¹Ñ”±½Ì¡•¡½Ì°Á•Ñ¥Ñ½É¥¼°¹½Éµ…ÌäÁÉÕ•‰…ÌÁÉ½Á½É¥½¹…‘½Ì¸(´9¼¥¹Ù•¹Ñ•Ì…ÉÓµÕ±½Ì°©ÕÉ¥ÍÁÉÕ‘•¹¥„°Á±…é½Ì°½µÁ•Ñ•¹¥…Ì¹¤‘…Ñ½ÌÁ•ÉÍ½¹…±•Ì¸(´=µ¥Ñ”Õ…±ÅÕ¥•ÈÍ•§Í¸½Á¥½¹…°ÅÕ”¹¼Ñ•¹„½¹Ñ•¹¥‘¼ì¹¼¥¹Í•ÉÑ•Ì¥¹ÍÑÉÕ¥½¹•Ì¹¤µ…É…‘½É•Ì¥¹Ñ•É¹½Ì¸(´¸Õ¸…Ñ„°‘•ÙÕ•±Ù”ƒé¹¥…µ•¹Ñ”•¹‘„°•Í…ÉÉ½±±¼‘”±„É•Õ¹§Í¸°Õ•É‘½Ìä±„‰…Í”±•…°¼‘½Õµ•¹Ñ…°Õ…¹‘¼•á¥ÍÑ„¸(´¸±½Ì‘•·…Ì•ÍÉ¥Ñ½Ì°‘•ÙÕ•±Ù”ƒé¹¥…µ•¹Ñ”A•Ñ¥Ñ½É¥¼°Õ¹‘…µ•¹Ñ½Ì‘”¡•¡¼°Õ¹‘…µ•¹Ñ½Ì‘”‘•É•¡¼ä5•‘¥½ÌÁÉ½‰…Ñ½É¥½Ì½…¹•á½ÌÕ…¹‘¼½ÉÉ•ÍÁ½¹‘…¸¸(´Q½¹¼™½Éµ…°°±…É¼ä‘¥É•Ñ¼¸(´ÍÑ”•ÌÕ¸‰½ÉÉ…‘½ÈÁ…É„É•Ù¥Í§Í¸ÁÉ½™•Í¥½¹…°¸(4)A•Ñ¥Ñ½É¥¼è4)íÁ…å±½…¹É•ÅÕ•ÍÑ}Ñ•áÑô4(4)!•¡½Ìè4)íÁ…å±½…¹™…ÑÍô4(4)Õ¹‘…µ•¹Ñ¼±•…°ÁÉ½Á½É¥½¹…‘¼è4)íÁ…å±½…¹±•…±}‰…Í¥Íô4(4)AÉÕ•‰…Ìä…¹•á½Ìè4)íÁ…å±½…¹•Ù¥‘•¹•ô4(ˆˆˆ4(€€€€€€€ÑÉäè4(€€€€€€€€€€€É•ÍÕ±Ð€ô}…¥}…±° 4(€€€€€€€€€€€€€€€ÁÉ½µÁÐ°4(€€€€€€€€€€€€€€€ÍåÍÑ•´ô‰É•ÌÕ¸…Í¥ÍÑ•¹Ñ”‘”É•‘…§Í¸©ÕËµ‘¥„Á•ÉÕ…¹„¸9Õ¹„¥¹Ù•¹Ñ…ÌÕ¹„¹½Éµ„¸¥ÍÑ¥¹Õ”±…É…µ•¹Ñ”•¹ÑÉ”¹½Éµ„Ù•É¥™¥…‘„°‘…Ñ¼…Á½ÉÑ…‘¼äÁÕ¹Ñ¼Á•¹‘¥•¹Ñ”¸ˆ°4(€€€€€€€€€€€€€€€ÁÕÉÁ½Í”ô‰±•…°ˆ°4(€€€€€€€€€€€€€€€…±±½Ý}Ý•ˆõÁ…å±½…¹Ù•É¥™å}Ý•ˆ°4(€€€€€€€€€€€€€€€…±±½Ý•‘}‘½µ…¥¹Ìõl‰½ˆ¹Á”ˆ°€‰Á¨¹½ˆ¹Á”ˆ°€‰ÑŒ¹½ˆ¹Á”ˆ°€‰½¹É•Í¼¹½ˆ¹Á”ˆ°€‰•±Á•ÉÕ…¹¼¹Á”‰t°4(€€€€€€€€€€€€€€€Ñ¥µ•½ÕÐôÌØÀ°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€…¥}¥¹™¼€ôÉ•ÍÕ±Ð4(€€€€€€€€€€€¥˜É•ÍÕ±Ð¹•Ð ‰É•ÍÁ½¹Í”ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤è4(€€€€€€€€€€€€€€€‘É…™Ð€ôÉ•ÍÕ±Ñl‰É•ÍÁ½¹Í”‰t¹ÍÑÉ¥À ¤4(€€€€€€€•á•ÁÐ!QQAá•ÁÑ¥½¸…Ì•áŒè4(€€€€€€€€€€€Ý…É¹¥¹œ€ô•áŒ¹‘•Ñ…¥°4(€€€Ñ…É•Ð€ôÉ•…Ñ•}±•…±}‘½à¡­¥¹°™¥•±‘Ì°‘É…™Ð¤(€€€É•ÑÕÉ¸ì4(€€€€€€€€‰‘É…™Ðˆè‘É…™Ð°4(€€€€€€€€‰‘½Ý¹±½…‘}ÕÉ°ˆè˜ˆ½…Á¤½•¹•É…Ñ•½íÑ…É•Ð¹¹…µ•ôˆ°4(€€€€€€€€‰™¥±•¹…µ”ˆèÑ…É•Ð¹¹…µ”°4(€€€€€€€€‰Ý…É¹¥¹œˆèÝ…É¹¥¹œ°4(€€€€€€€€‰ÁÉ½Ù¥‘•Èˆè…¥}¥¹™¼¹•Ð ‰ÁÉ½Ù¥‘•Èˆ°€ˆˆ¤°4(€€€€€€€€‰µ½‘•°ˆè…¥}¥¹™¼¹•Ð ‰µ½‘•°ˆ°€ˆˆ¤°4(€€€€€€€€‰Í½ÕÉ•Ìˆè…¥}¥¹™¼¹•Ð ‰Í½ÕÉ•Ìˆ°mt¤°4(€€€ô4(4(4)…ÁÀ¹•Ð ˆ½…Á¤½•¹•É…Ñ•½í™¥±•¹…µ•ôˆ¤4)‘•˜‘½Ý¹±½…‘}•¹•É…Ñ•¡™¥±•¹…µ”èÍÑÈ¤€´ø¥±•I•ÍÁ½¹Í”è4(€€€Ñ…É•Ð€ô9IQ}%H€¼Í…™•}™¥±•¹…µ”¡™¥±•¹…µ”¤4(€€€¥˜¹½ÐÑ…É•Ð¹•á¥ÍÑÌ ¤è4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÀÐ°€‰½Õµ•¹Ñ¼•¹•É…‘¼¹¼•¹½¹ÑÉ…‘¼ˆ¤4(€€€É•ÑÕÉ¸¥±•I•ÍÁ½¹Í”¡Ñ…É•Ð°™¥±•¹…µ”õÑ…É•Ð¹¹…µ”¤4(4(4)…ÁÀ¹•Ð ˆ½…Á¤½­¹½Ý±•‘”½í‘½µ…¥¹ô½ÍÕµµ…Éäˆ¤4)‘•˜­¹½Ý±•‘•}ÍÕµµ…Éä¡‘½µ…¥¸èÍÑÈ¤€´ø‘¥Ðè4(€€€É•ÑÕÉ¸Í½ÕÉ•}ÍÕµµ…Éä¡‘½µ…¥¸¤4(4(4)…ÁÀ¹•Ð ˆ½…Á¤½­¹½Ý±•‘”½í‘½µ…¥¹ô½Í•…É ˆ¤4)‘•˜­¹½Ý±•‘•}Í•…É ¡‘½µ…¥¸èÍÑÈ°ÄèÍÑÈ°±¥µ¥Ðè¥¹Ð€ô€ÄÈ¤€´ø‘¥Ðè4(€€€É•ÑÕÉ¸ì‰É•ÍÕ±ÑÌˆèÍ•…É¡}­¹½Ý±•‘”¡‘½µ…¥¸°Ä°µ…à Ä°µ¥¸¡±¥µ¥Ð°€ÌÀ¤¤¥ô4(4(4)…ÁÀ¹Á½ÍÐ ˆ½…Á¤½­¹½Ý±•‘”½í‘½µ…¥¹ô½ÕÁ±½…ˆ¤4)…Íå¹Œ‘•˜­¹½Ý±•‘•}ÕÁ±½…¡‘½µ…¥¸èÍÑÈ°ÕÁ±½…èUÁ±½…‘¥±”€ô¥±” ¸¸¸¤°¹…µ”èÍÑÈ€ô½É´ ˆˆ¤¤€´ø‘¥Ðè4(€€€¥˜¹½ÐÕÁ±½…¹™¥±•¹…µ”½ÈA…Ñ ¡ÕÁ±½…¹™¥±•¹…µ”¤¹ÍÕ™™¥à¹±½Ý•È ¤€„ô€ˆ¹é¥Àˆè4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÀÀ°€‰MÕ‰”Õ¸…É¡¥Ù¼i%@ˆ¤4(€€€½¹Ñ•¹Ð€ô…Ý…¥ÐÕÁ±½…¹É•… ¤4(€€€¥˜±•¸¡½¹Ñ•¹Ð¤€ø€àÀÀ€¨€ÄÀÈÐ€¨€ÄÀÈÐè4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÄÌ°€‰°i%@ÍÕÁ•É„•°³µµ¥Ñ”‘”€àÀÀ5ˆ¤4(€€€Ñ•µÁ}‘¥È€ô-9=]1}%H€¼€‰¥¹½µ¥¹œˆ4(€€€Ñ•µÁ}‘¥È¹µ­‘¥È¡Á…É•¹ÑÌõQÉÕ”°•á¥ÍÑ}½¬õQÉÕ”¤4(€€€Ñ•µÁ}Á…Ñ €ôÑ•µÁ}‘¥È€¼˜‰íÕÑ}¹½Ü ¤¹É•Á±…” œèœ°œ´œ¥õ}íÍ…™•}™¥±•¹…µ”¡ÕÁ±½…¹™¥±•¹…µ”¥ôˆ4(€€€…Ñ½µ¥}ÝÉ¥Ñ•}‰åÑ•Ì¡Ñ•µÁ}Á…Ñ °½¹Ñ•¹Ð¤4(€€€ÑÉäè4(€€€€€€€É•ÍÕ±Ð€ô¥µÁ½ÉÑ}‰Õ¹‘±”¡Ñ•µÁ}Á…Ñ °‘½µ…¥¸õ‘½µ…¥¸°‘¥ÍÁ±…å}¹…µ”õ¹…µ”½ÈÕÁ±½…¹™¥±•¹…µ”¤4(€€€€€€€…¹…±åÍ¥Ì€ô…¹…±åé•}‰Õ¹‘±”¡A…Ñ ¡É•ÍÕ±Ñl‰•áÑÉ…Ñ}‘¥È‰t¤¤4(€€€€€€€É•ÍÕ±Ñl‰…¹…±åÍ¥Ì‰t€ô…¹…±åÍ¥Ì4(€€€€€€€É•ÑÕÉ¸É•ÍÕ±Ð4(€€€•á•ÁÐé¥Á™¥±”¹	…‘i¥Á¥±”è4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÀÀ°€‰°…É¡¥Ù¼¹¼•ÌÕ¸i%@Û…±¥‘¼ˆ¤4(€€€•á•ÁÐY…±Õ•ÉÉ½È…Ì•áŒè4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÀÀ°ÍÑÈ¡•áŒ¤¤4(€€€™¥¹…±±äè4(€€€€€€€Ñ•µÁ}Á…Ñ ¹Õ¹±¥¹¬¡µ¥ÍÍ¥¹}½¬õQÉÕ”¤4(4(4)…ÁÀ¹•Ð ˆ½…Á¤½Á…±Ý½É±½Ý½É­ÍÁ…”½ÍÑ…ÑÕÌˆ¤4)‘•˜Á…±Ý½É±‘}Ý½É­ÍÁ…•}ÍÑ…ÑÕÌ¡¡•­}½¹±¥¹”è‰½½°€ô…±Í”¤€´ø‘¥Ðè4(€€€ÑÉäè4(€€€€€€€É•ÑÕÉ¸ì‰•‘¥Ñ½Èˆè•‘¥Ñ½É}ÍÑ…ÑÕÌ¡¡•­}½¹±¥¹”¤°€‰Í•ÍÍ¥½¹Ìˆè±¥ÍÑ}Í•ÍÍ¥½¹Ì ¥ô4(€€€•á•ÁÐIÕ¹Ñ¥µ•ÉÉ½È…Ì•áŒè4(€€€€€€€É•ÑÕÉ¸ì‰•‘¥Ñ½Èˆèì¨©•‘¥Ñ½É}ÍÑ…ÑÕÌ¡…±Í”¤°€‰½¹±¥¹•}•ÉÉ½ÈˆèÍÑÈ¡•áŒ¥ô°€‰Í•ÍÍ¥½¹Ìˆè±¥ÍÑ}Í•ÍÍ¥½¹Ì ¥ô4(4(4)…ÁÀ¹Á½ÍÐ ˆ½…Á¤½Á…±Ý½É±½Ý½É­ÍÁ…”½ÕÁ±½…ˆ¤4)…Íå¹Œ‘•˜Á…±Ý½É±‘}Ý½É­ÍÁ…•}ÕÁ±½… 4(€€€ÕÁ±½…èUÁ±½…‘¥±”€ô¥±” ¸¸¸¤°•áÁ•Ñ•‘}­¥¹èÍÑÈ€ô½É´ ‰…ÕÑ¼ˆ¤°4(¤€´ø‘¥Ðè4(€€€¥˜¹½ÐÕÁ±½…¹™¥±•¹…µ”è4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÀÀ°€‰M•±•¥½¹„Õ¸…É¡¥Ù¼ˆ¤4(€€€•¹ÍÕÉ•}Ý½É­ÍÁ…•}‘¥ÉÌ ¤4(€€€Ñ•µÁ}Á…Ñ èA…Ñ ð9½¹”€ô9½¹”4(€€€ÑÉäè4(€€€€€€€Ý¥Ñ Ñ•µÁ™¥±”¹9…µ•‘Q•µÁ½É…Éå¥±”¡‘•±•Ñ”õ…±Í”°‘¥Èõ%9=5%9}%H°ÍÕ™™¥àôˆ¹Á…ÉÐˆ¤…ÌÑ•µÀè4(€€€€€€€€€€€Ñ•µÁ}Á…Ñ €ôA…Ñ ¡Ñ•µÀ¹¹…µ”¤4(€€€€€€€€€€€Ñ½Ñ…°€ô€À4(€€€€€€€€€€€Ý¡¥±”QÉÕ”è4(€€€€€€€€€€€€€€€¡Õ¹¬€ô…Ý…¥ÐÕÁ±½…¹É•… ÄÀÈÐ€¨€ÄÀÈÐ¤4(€€€€€€€€€€€€€€€¥˜¹½Ð¡Õ¹¬è4(€€€€€€€€€€€€€€€€€€€‰É•…¬4(€€€€€€€€€€€€€€€Ñ½Ñ…°€¬ô±•¸¡¡Õ¹¬¤4(€€€€€€€€€€€€€€€¥˜Ñ½Ñ…°€ø5a}UA1=}	eQLè4(€€€€€€€€€€€€€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÄÌ°€‰°…É¡¥Ù¼ÍÕÁ•É„•°³µµ¥Ñ”‘”€Èˆ¤4(€€€€€€€€€€€€€€€Ñ•µÀ¹ÝÉ¥Ñ”¡¡Õ¹¬¤4(€€€€€€€É•ÑÕÉ¸É•…Ñ•}Í•ÍÍ¥½¹}™É½µ}Á…Ñ ¡Ñ•µÁ}Á…Ñ °ÕÁ±½…¹™¥±•¹…µ”°•áÁ•Ñ•‘}­¥¹¤4(€€€•á•ÁÐ!QQAá•ÁÑ¥½¸è4(€€€€€€€É…¥Í”4(€€€•á•ÁÐ€¡Y…±Õ•ÉÉ½È°é¥Á™¥±”¹	…‘i¥Á¥±”¤…Ì•áŒè4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÀÀ°ÍÑÈ¡•áŒ¤¤™É½´•áŒ4(€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÔÀÀ°˜‰9¼Í”ÁÕ‘¼ÁÉ•Á…É…È•°Õ…É‘…‘¼èí•áôˆ¤™É½´•áŒ4(€€€™¥¹…±±äè4(€€€€€€€¥˜Ñ•µÁ}Á…Ñ …¹Ñ•µÁ}Á…Ñ ¹•á¥ÍÑÌ ¤è4(€€€€€€€€€€€Ñ•µÁ}Á…Ñ ¹Õ¹±¥¹¬¡µ¥ÍÍ¥¹}½¬õQÉÕ”¤4(4(4)…ÁÀ¹•Ð ˆ½…Á¤½Á…±Ý½É±½Ý½É­ÍÁ…”½Í•ÍÍ¥½¹Ìˆ¤4)‘•˜Á…±Ý½É±‘}Ý½É­ÍÁ…•}Í•ÍÍ¥½¹Ì ¤€´ø‘¥Ðè4(€€€É•ÑÕÉ¸ì‰Í•ÍÍ¥½¹Ìˆè±¥ÍÑ}Í•ÍÍ¥½¹Ì ¥ô4(4(4)…ÁÀ¹‘•±•Ñ” ˆ½…Á¤½Á…±Ý½É±½Ý½É­ÍÁ…”½Í•ÍÍ¥½¹Ì½íÍ•ÍÍ¥½¹}¥‘ôˆ¤4)‘•˜Á…±Ý½É±‘}Ý½É­ÍÁ…•}‘•±•Ñ”¡Í•ÍÍ¥½¹}¥èÍÑÈ¤€´ø‘¥Ðè4(€€€ÑÉäè4(€€€€€€€‘•±•Ñ•}Í•ÍÍ¥½¸¡Í•ÍÍ¥½¹}¥¤4(€€€€€€€É•ÑÕÉ¸ì‰‘•±•Ñ•ˆèQÉÕ•ô4(€€€•á•ÁÐ¥±•9½Ñ½Õ¹‘ÉÉ½È…Ì•áŒè4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÀÐ°ÍÑÈ¡•áŒ¤¤™É½´•áŒ4(€€€•á•ÁÐY…±Õ•ÉÉ½È…Ì•áŒè4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÀÀ°ÍÑÈ¡•áŒ¤¤™É½´•áŒ4(4(4)…ÁÀ¹•Ð ˆ½…Á¤½Á…±Ý½É±½Ý½É­ÍÁ…”½Í•ÍÍ¥½¹Ì½íÍ•ÍÍ¥½¹}¥‘ô½‘½Ý¹±½…ˆ¤)‘•˜Á…±Ý½É±‘}Ý½É­ÍÁ…•}‘½Ý¹±½…¡Í•ÍÍ¥½¹}¥èÍÑÈ°Ù…É¥…¹ÐèÍÑÈ€ô€‰‰…­ÕÀˆ¤€´ø¥±•I•ÍÁ½¹Í”è4(€€€ÑÉäè4(€€€€€€€Ñ…É•Ð€ôÍ•ÍÍ¥½¹}™¥±”¡Í•ÍÍ¥½¹}¥°Ù…É¥…¹Ð¤4(€€€€€€€É•ÑÕÉ¸¥±•I•ÍÁ½¹Í”¡Ñ…É•Ð°™¥±•¹…µ”õÑ…É•Ð¹¹…µ”¤4(€€€•á•ÁÐ¥±•9½Ñ½Õ¹‘ÉÉ½È…Ì•áŒè4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÀÐ°ÍÑÈ¡•áŒ¤¤™É½´•áŒ4(€€€•á•ÁÐY…±Õ•ÉÉ½È…Ì•áŒè4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÀÀ°ÍÑÈ¡•áŒ¤¤™É½´•áŒ(()…ÁÀ¹Á½ÍÐ ˆ½…Á¤½Á…±Ý½É±½Ý½É­ÍÁ…”½Í•ÍÍ¥½¹Ì½íÍ•ÍÍ¥½¹}¥‘ô½É•ÍÑ½É”ˆ¤)‘•˜Á…±Ý½É±‘}Ý½É­ÍÁ…•}É•ÍÑ½É”¡Í•ÍÍ¥½¹}¥èÍÑÈ¤€´ø‘¥Ðè(€€€ÑÉäè(€€€€€€€É•ÑÕÉ¸ì‰É•ÍÑ½É•ˆèQÉÕ”°€‰Í•ÍÍ¥½¸ˆèÉ•ÍÑ½É•}Í•ÍÍ¥½¸¡Í•ÍÍ¥½¹}¥¥ô(€€€•á•ÁÐ¥±•9½Ñ½Õ¹‘ÉÉ½È…Ì•áŒè(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÀÐ°ÍÑÈ¡•áŒ¤¤™É½´•áŒ(€€€•á•ÁÐY…±Õ•ÉÉ½È…Ì•áŒè(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÀÀ°ÍÑÈ¡•áŒ¤¤™É½´•áŒ(4(4)…ÁÀ¹Á½ÍÐ ˆ½…Á¤½Á…±Ý½É±½Ý½É­ÍÁ…”½Í•ÍÍ¥½¹Ì½íÍ•ÍÍ¥½¹}¥‘ô½½Á•¸µ™½±‘•Èˆ¤4)‘•˜Á…±Ý½É±‘}Ý½É­ÍÁ…•}½Á•¹}™½±‘•È¡Í•ÍÍ¥½¹}¥èÍÑÈ¤€´ø‘¥Ðè4(€€€ÑÉäè4(€€€€€€€É•ÑÕÉ¸½Á•¹}Í•ÍÍ¥½¹}™½±‘•È¡Í•ÍÍ¥½¹}¥¤4(€€€•á•ÁÐ¥±•9½Ñ½Õ¹‘ÉÉ½È…Ì•áŒè4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÀÐ°ÍÑÈ¡•áŒ¤¤™É½´•áŒ4(€€€•á•ÁÐ€¡Y…±Õ•ÉÉ½È°IÕ¹Ñ¥µ•ÉÉ½È¤…Ì•áŒè4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÀÀ°ÍÑÈ¡•áŒ¤¤™É½´•áŒ4(4(4)…ÁÀ¹Á½ÍÐ ˆ½…Á¤½Á…±Ý½É±½•‘¥Ñ½È½¥¹ÍÑ…±°ˆ¤4)‘•˜Á…±Ý½É±‘}•‘¥Ñ½É}¥¹ÍÑ…±° ¤€´ø‘¥Ðè4(€€€ÑÉäè4(€€€€€€€É•ÑÕÉ¸¥¹ÍÑ…±±}•‘¥Ñ½É}±…Ñ•ÍÐ ¤4(€€€•á•ÁÐIÕ¹Ñ¥µ•ÉÉ½È…Ì•áŒè4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÔÀÌ°ÍÑÈ¡•áŒ¤¤™É½´•áŒ4(4(4)…ÁÀ¹Á½ÍÐ ˆ½…Á¤½Á…±Ý½É±½•‘¥Ñ½È½±…Õ¹ ˆ¤4)‘•˜Á…±Ý½É±‘}•‘¥Ñ½É}±…Õ¹ ¡Á…å±½…èA…±‘¥Ñ½É1…Õ¹¡I•ÅÕ•ÍÐ¤€´ø‘¥Ðè4(€€€ÑÉäè4(€€€€€€€É•ÑÕÉ¸±…Õ¹¡}•‘¥Ñ½È¡Á…å±½…¹Í•ÍÍ¥½¹}¥¤4(€€€•á•ÁÐ¥±•9½Ñ½Õ¹‘ÉÉ½È…Ì•áŒè4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÀÐ°ÍÑÈ¡•áŒ¤¤™É½´•áŒ4(€€€•á•ÁÐ€¡Y…±Õ•ÉÉ½È°IÕ¹Ñ¥µ•ÉÉ½È¤…Ì•áŒè4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÀÀ°ÍÑÈ¡•áŒ¤¤™É½´•áŒ4(4(4)…ÁÀ¹Á½ÍÐ ˆ½…Á¤½Á…±Ý½É±½Íå¹Œˆ¤4)‘•˜Íå¹}Á…±Ý½É± ¤€´ø‘¥Ðè4(€€€€ˆˆ‰½µÁ…Ñ¥‰¥±¥‘…½¸ØÀ¸Ðè…¡½É„•©•ÕÑ„•°•¹ÑÉ¼½µÁ±•Ñ¼‘”Í¥¹É½¹¥é…§Í¸¸ˆˆˆ4(€€€É•ÑÕÉ¸ÉÕ¹}™Õ±±}Íå¹Œ ‰Á…±Ý½É±µ‰ÕÑÑ½¸ˆ¤4(4(4)…ÁÀ¹Á½ÍÐ ˆ½…Á¤½Á…±Ý½É±½Íå¹Œµ¹•ÝÌˆ¤4)‘•˜Íå¹}Á…±Ý½É±‘}¹•ÝÌ ¤€´ø‘¥Ðè4(€€€É•ÑÕÉ¸ÉÕ¹}™Õ±±}Íå¹Œ ‰Á…±Ý½É±µ¹•ÝÌµ‰ÕÑÑ½¸ˆ¤4(4(4)…ÁÀ¹Á½ÍÐ ˆ½…Á¤½Íå¹Œ½…±°ˆ¤4)‘•˜Íå¹}…±° ¤€´ø‘¥Ðè4(€€€É•ÑÕÉ¸ÉÕ¹}™Õ±±}Íå¹Œ ‰µ…¹Õ…°ˆ¤4(4(4)…ÁÀ¹•Ð ˆ½…Á¤½Íå¹Œ½ÍÑ…ÑÕÌˆ¤4)‘•˜•Ñ}Íå¹}ÍÑ…ÑÕÌ ¤€´ø‘¥Ðè4(€€€É•ÑÕÉ¸Íå¹}ÍÑ…ÑÕÌ ¤4(4(4)…ÁÀ¹Á½ÍÐ ˆ½…Á¤½É•Í•…É ½Ý•ˆµ¥µÁ½ÉÐˆ¤4)‘•˜É•Í•…É¡}Ý•‰}¥µÁ½ÉÐ¡Á…å±½…è]•‰%µÁ½ÉÑI•ÅÕ•ÍÐ¤€´ø‘¥Ðè4(€€€ÑÉäè4(€€€€€€€É•ÑÕÉ¸¥µÁ½ÉÑ}Ý•‰}Í½ÕÉ”¡Á…å±½…¹ÕÉ°°Á…å±½…¹‘½µ…¥¸°Á…å±½…¹Ñ¥Ñ±”¤4(€€€•á•ÁÐY…±Õ•ÉÉ½È…Ì•áŒè4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÀÀ°ÍÑÈ¡•áŒ¤¤4(€€€•á•ÁÐIÕ¹Ñ¥µ•ÉÉ½È…Ì•áŒè4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÔÀÌ°ÍÑÈ¡•áŒ¤¤4(4(4)…ÁÀ¹Á½ÍÐ ˆ½…Á¤½É•Í•…É ½É½ÍÍÉ•˜½Í•…É ˆ¤4)‘•˜É•Í•…É¡}É½ÍÍÉ•™}Í•…É ¡Á…å±½…èÉ½ÍÍÉ•™M•…É¡I•ÅÕ•ÍÐ¤€´ø‘¥Ðè4(€€€ÑÉäè4(€€€€€€€É•ÑÕÉ¸ì‰É•ÍÕ±ÑÌˆèÉ½ÍÍÉ•™}Í•…É ¡Á…å±½…¹ÅÕ•Éä°Á…å±½…¹É½ÝÌ¥ô4(€€€•á•ÁÐ€¡IÕ¹Ñ¥µ•ÉÉ½È°Y…±Õ•ÉÉ½È¤…Ì•áŒè4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÔÀÌ°ÍÑÈ¡•áŒ¤¤4(4(4)…ÁÀ¹•Ð ˆ½…Á¤½É•Í•…É ½É½ÍÍÉ•˜½‘½¤ˆ¤4)‘•˜É•Í•…É¡}É½ÍÍÉ•™}‘½¤¡‘½¤èÍÑÈ¤€´ø‘¥Ðè4(€€€¥˜¹½Ð‘½¤¹ÍÑÉ¥À ¤è4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÀÀ°€‰ÍÉ¥‰”Õ¸=$ˆ¤4(€€€ÑÉäè4(€€€€€€€É•ÑÕÉ¸É½ÍÍÉ•™}‘½¤¡‘½¤¤4(€€€•á•ÁÐ€¡IÕ¹Ñ¥µ•ÉÉ½È°Y…±Õ•ÉÉ½È¤…Ì•áŒè4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÔÀÌ°ÍÑÈ¡•áŒ¤¤4(4(4)…ÁÀ¹Á½ÍÐ ˆ½…Á¤½É•Í•…É ½¥Ñ•µÌˆ¤4)‘•˜É•Í•…É¡}Í…Ù”¡Á…å±½…èI•Í•…É¡M…Ù•I•ÅÕ•ÍÐ¤€´ø‘¥Ðè4(€€€É•ÑÕÉ¸Í…Ù•}É•Í•…É¡}¥Ñ•´¡Á…å±½…¹¥Ñ•´¤4(4(4)…ÁÀ¹•Ð ˆ½…Á¤½É•Í•…É ½¥Ñ•µÌˆ¤4)‘•˜É•Í•…É¡}±¥ÍÐ¡±¥µ¥Ðè¥¹Ð€ô€ÄÀÀ¤€´ø‘¥Ðè4(€€€É•ÑÕÉ¸ì‰¥Ñ•µÌˆè±¥ÍÑ}É•Í•…É¡}¥Ñ•µÌ¡±¥µ¥Ð¥ô4(4(4)…ÁÀ¹Á½ÍÐ ˆ½…Á¤½É•Í•…É ½…Õ‘¥Ðˆ¤4)‘•˜É•Í•…É¡}…Õ‘¥Ð¡Á…å±½…è	¥‰±¥½É…Á¡åÕ‘¥ÑI•ÅÕ•ÍÐ¤€´ø‘¥Ðè4(€€€É•ÑÕÉ¸‰¥‰±¥½É…Á¡å}…Õ‘¥Ð¡Á…å±½…¹½¹Ñ•¹Ð°Á…å±½…¹‰¥‰±¥½É…Á¡ä¤4(4(4)…ÁÀ¹Á½ÍÐ ˆ½…Á¤½É•Í•…É ½ÍÕ•ÍÐˆ¤4)‘•˜É•Í•…É¡}ÍÕ•ÍÐ¡Á…å±½…èÕÑ½I•Í•…É¡I•ÅÕ•ÍÐ¤€´ø‘¥Ðè4(€€€ÑÉäè4(€€€€€€€É•ÑÕÉ¸ÍÕ•ÍÑ}……‘•µ¥}Í½ÕÉ•Ì¡Á…å±½…¹Ñ¥Ñ±”°Á…å±½…¹½¹Ñ•¹Ð°Á…å±½…¹É½ÝÌ¤4(€€€•á•ÁÐY…±Õ•ÉÉ½È…Ì•áŒè4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÐÀÀ°ÍÑÈ¡•áŒ¤¤4(€€€•á•ÁÐIÕ¹Ñ¥µ•ÉÉ½È…Ì•áŒè4(€€€€€€€É…¥Í”!QQAá•ÁÑ¥½¸ ÔÀÌ°ÍÑÈ¡•áŒ¤¤4(4(4)…ÁÀ¹Á½ÍÐ ˆ½…Á¤½­¹½Ý±•‘”½…Í¬ˆ¤4)‘•˜­¹½Ý±•‘•}…Í¬¡Á…å±½…è-¹½Ý±•‘•EÕ•ÍÑ¥½¸¤€´ø‘¥Ðè4(€€€É•ÍÕ±ÑÌ€ôÍ•…É¡}­¹½Ý±•‘”¡Á…å±½…¹‘½µ…¥¸°Á…å±½…¹ÅÕ•ÍÑ¥½¸°€ÄÀ¤4(€€€±½…±}…¹ÍÝ•È€ô½™™±¥¹•}­¹½Ý±•‘•}…¹ÍÝ•È¡Á…å±½…¹ÅÕ•ÍÑ¥½¸°É•ÍÕ±ÑÌ¤4(€€€¥˜¹½ÐÉ•ÍÕ±ÑÌè4(€€€€€€€É•ÑÕÉ¸ì‰…¹ÍÝ•Èˆè±½…±}…¹ÍÝ•È°€‰É•ÍÕ±ÑÌˆèmt°€‰½™™±¥¹”ˆèQÉÕ•ô4(4(€€€ÍÑ…ÑÕÌ€ô…¥}ÁÉ½Ù¥‘•É}ÍÑ…ÑÕÌ ¤4(€€€ÁÉ½Ù¥‘•ÉÌ€ôÍÑ…ÑÕÌ¹•Ð ‰ÁÉ½Ù¥‘•ÉÌˆ°íô¤¥˜¥Í¥¹ÍÑ…¹”¡ÍÑ…ÑÕÌ°‘¥Ð¤•±Í”íô4(€€€…¥}É•…‘ä€ô‰½½° 4(€€€€€€€ÁÉ½Ù¥‘•ÉÌ¹•Ð ‰½Á•¹…¤ˆ°íô¤¹•Ð ‰½¹™¥ÕÉ•ˆ¤4(€€€€€€€½ÈÁÉ½Ù¥‘•ÉÌ¹•Ð ‰½±±…µ„ˆ°íô¤¹•Ð ‰½¹±¥¹”ˆ¤4(€€€€€€€½ÈÁÉ½Ù¥‘•ÉÌ¹•Ð ‰½µÁ…Ñ¥‰±”ˆ°íô¤¹•Ð ‰½¹™¥ÕÉ•ˆ¤4(€€€€¤4(€€€¥˜¹½Ð…¥}É•…‘äè4(€€€€€€€É•ÑÕÉ¸ì4(€€€€€€€€€€€€‰…¹ÍÝ•Èˆè±½…±}…¹ÍÝ•È°4(€€€€€€€€€€€€‰É•ÍÕ±ÑÌˆèÉ•ÍÕ±ÑÌ°4(€€€€€€€€€€€€‰½™™±¥¹”ˆèQÉÕ”°4(€€€€€€€€€€€€‰Ý…É¹¥¹œˆè€‰I•ÍÁÕ•ÍÑ„•¹•É…‘„‘¥É•Ñ…µ•¹Ñ”‘•Í‘”±„‰¥‰±¥½Ñ•„±½…°ì¹¼¡…äÕ¸ÁÉ½Ù••‘½È‘”%½¹™¥ÕÉ…‘¼¸ˆ°4(€€€€€€€ô4(4(€€€½¹Ñ•áÐ€ô€‰q¹q¸ˆ¹©½¥¸ 4(€€€€€€€˜‰U9Qèí¥Ñ•µlÑ¥Ñ±”uô€¡í¥Ñ•µl…Ñ•½Éäuô¥q¹í¥Ñ•µlÍ¹¥ÁÁ•Ðuôˆ4(€€€€€€€™½È¥Ñ•´¥¸É•ÍÕ±ÑÌ4(€€€€¤4(€€€‘½µ…¥¹}ÉÕ±•Ì€ôì4(€€€€€€€€‰Á…±Ý½É±ˆè€‰I•ÍÁ½¹‘”Í½‰É”A…±Ý½É±ÕÍ…¹‘¼•°µ…Ñ•É¥…°±½…°¸¥ÍÑ¥¹Õ”‘…Ñ½Ì½¹™¥Éµ…‘½Ì‘”¥¹™•É•¹¥…Ìä¹¼¥¹Ù•¹Ñ•ÌÍ‘¥½Ì¥¹Ñ•É¹½Ì¸ˆ°4(€€€€€€€€‰…µ•µ½ˆè€‰åÕ‘„„…¹…±¥é…Èµ½‘Ì°‘ÕµÁÌ°1¥‰Q½½°°U¹¥Ñää1Õ„‘”™½Éµ„Ó¥¹¥„äÉ•Ù•ÉÍ¥‰±”¸ˆ°4(€€€€€€€€‰±¥‰Ñ½½°ˆè€‰åÕ‘„½¸1¥‰Q½½°°‘ÕµÁÌä1Õ„‘”µ…¹•É„É•Ù•ÉÍ¥‰±”äÍ•ÕÉ„¸ˆ°4(€€€ô4(€€€ÍåÍÑ•´€ô‘½µ…¥¹}ÉÕ±•Ì¹•Ð¡Á…å±½…¹‘½µ…¥¸°€‰I•ÍÁ½¹‘”ÕÍ…¹‘¼•á±ÕÍ¥Ù…µ•¹Ñ”•°½¹Ñ•áÑ¼±½…°É•ÕÁ•É…‘¼¸ˆ¤4(€€€ÁÉ½µÁÐ€ô˜ˆˆ‰AÉ•Õ¹Ñ„‘•°ÕÍÕ…É¥¼è4)íÁ…å±½…¹ÅÕ•ÍÑ¥½¹ô4(4)½¹Ñ•áÑ¼±½…°É•ÕÁ•É…‘¼è4)í½¹Ñ•áÑô4(4)½¹Ñ•ÍÑ„•¸•ÍÁ‡Å½°°¥Ñ„±½ÌÓµÑÕ±½ÌÕÍ…‘½ÌäÍ—Å…±„±…É…µ•¹Ñ”Õ…¹‘¼±„•Ù¥‘•¹¥„¹¼…±…¹”¸4(ˆˆˆ4(€€€ÑÉäè4(€€€€€€€É•ÍÕ±Ð€ô}…¥}…±° 4(€€€€€€€€€€€ÁÉ½µÁÐ°ÍåÍÑ•´õÍåÍÑ•´°4(€€€€€€€€€€€ÁÕÉÁ½Í”ô‰½‘”ˆ¥˜Á…å±½…¹‘½µ…¥¸¥¸ì‰…µ•µ½ˆ°€‰±¥‰Ñ½½°‰ô•±Í”€‰•¹•É…°ˆ°4(€€€€€€€€€€€Ñ¥µ•½ÕÐôÄàÀ°4(€€€€€€€€¤4(€€€€€€€…¹ÍÝ•È€ôÉ•ÍÕ±Ð¹•Ð ‰É•ÍÁ½¹Í”ˆ°€ˆˆ¤¹ÍÑÉ¥À ¤4(€€€€€€€É•ÑÕÉ¸ì‰…¹ÍÝ•Èˆè…¹ÍÝ•È½È±½…±}…¹ÍÝ•È°€‰É•ÍÕ±ÑÌˆèÉ•ÍÕ±ÑÌ°€‰½™™±¥¹”ˆè¹½Ð‰½½°¡…¹ÍÝ•È¥ô4(€€€•á•ÁÐ!QQAá•ÁÑ¥½¸…Ì•áŒè4(€€€€€€€É•ÑÕÉ¸ì4(€€€€€€€€€€€€‰…¹ÍÝ•Èˆè±½…±}…¹ÍÝ•È°4(€€€€€€€€€€€€‰É•ÍÕ±ÑÌˆèÉ•ÍÕ±ÑÌ°4(€€€€€€€€€€€€‰½™™±¥¹”ˆèQÉÕ”°4(€€€€€€€€€€€€‰Ý…É¹¥¹œˆè˜‰1„%¹¼É•ÍÁ½¹‘§ÌìÍ”ÕÏÌ±„É•ÍÁÕ•ÍÑ„±½…°¸í•áŒ¹‘•Ñ…¥±ôˆ°4(€€€€€€€ô4(4