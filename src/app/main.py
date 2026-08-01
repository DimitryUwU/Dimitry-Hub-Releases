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
from .lua_tools import analyze_lua_source, generate_gameguardian_script
from .research import bibliography_audit, crossref_doi, crossref_search, import_web_source, list_research_items, save_research_item, suggest_academic_sources
from .sync_engine import auto_sync_due, run_full_sync, sync_status
from .study_tools import generate_study_material
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
    institution: str = "Universidad Tecnológica de los Andes"
    course: str = ""
    teacher: str = ""
    city: str = "Abancay, Perú"
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


class LuaGenerateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    author: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=1000)
    changes: str = Field(min_length=1, max_length=100000)


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
        # La aplicación debe abrir aunque una fuente externa falle.
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
        log_activity(db, project_id, "Nota añadida", payload.title)
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
        raise HTTPException(413, "El archivo supera el límite de 300 MB")
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
            (project_id, name, str(target), upload.content_type or "application/octet-stream", len(content), digest, extracted, now),
        )
        db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
        log_activity(db, project_id, "Archivo importado", f"{name} · {len(content)} bytes")
        row = db.execute("SELECT * FROM files WHERE id=?", (cursor.lastrowid,)).fetchone()
    return dict(row)


@app.post("/api/extract")
async def extract_upload(upload: UploadFile = File(...)) -> dict:
    content = await upload.read()
    if len(content) > 100 * 1024 * 1024:
        raise HTTPException(413, "El archivo supera el límite de 100 MB")
    suffix = Path(upload.filename or "archivo.txt").suffix
    supported = {".pdf", ".docx", ".pptx", ".txt", ".md"}
    if suffix.lower() not in supported:
        raise HTTPException(400, "Formato no admitido. Usa PDF, DOCX, PPTX, TXT o MD.")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        path = Path(tmp.name)
    try:
        text = extract_text(path)
    finally:
        path.unlink(missing_ok=True)
    if not text.strip():
        raise HTTPException(422, "No se encontró texto extraíble. Si el archivo contiene imágenes escaneadas, conviértelas primero con reconocimiento de texto.")
    if text.startswith("[No se pudo extraer el texto:"):
        raise HTTPException(422, text.strip("[]"))
    return {"name": upload.filename, "text": text[:250000]}


@app.get("/api/files/{file_id}/download")
def download_file(file_id: int) -> FileResponse:
    with connect() as db:
        row = db.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    if not row or not Path(row["stored_path"]).exists():
        raise HTTPException(404, "Archivo no encontrado")
    return FileResponse(row["stored_path"], filename=row["original_name"])


@app.delete("/api/files/{file_id}")
def delete_file(file_id: int) -> dict:
    with connect() as db:
        row = db.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        if not row:
            return {"ok": True}
        source = Path(row["stored_path"])
        if source.exists():
            backup = BACKUPS_DIR / str(row["project_id"]) / f"deleted_{utc_now().replace(':','-')}_{safe_filename(row['original_name'])}"
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, backup)
            if sha256_file(source) != sha256_file(backup):
                raise HTTPException(500, "No se pudo verificar la copia de seguridad")
            source.unlink()
        db.execute("DELETE FROM files WHERE id=?", (file_id,))
        log_activity(db, row["project_id"], "Archivo eliminado con respaldo", row["original_name"])
    return {"ok": True}


@app.get("/api/compare")
def compare_files(left: int, right: int) -> dict:
    with connect() as db:
        a = db.execute("SELECT * FROM files WHERE id=?", (left,)).fetchone()
        b = db.execute("SELECT * FROM files WHERE id=?", (right,)).fetchone()
    if not a or not b:
        raise HTTPException(404, "Selecciona dos archivos válidos")
    a_text = a["extracted_text"] or ""
    b_text = b["extracted_text"] or ""
    diff = "\n".join(difflib.unified_diff(
        a_text.splitlines(), b_text.splitlines(),
        fromfile=a["original_name"], tofile=b["original_name"], lineterm=""
    ))
    return {
        "left": dict(a),
        "right": dict(b),
        "identical_hash": a["sha256"] == b["sha256"],
        "diff": diff[:250000],
    }


def build_context(project_id: int) -> str:
    with connect() as db:
        p = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not p:
            raise HTTPException(404, "Proyecto no encontrado")
        notes = db.execute("SELECT * FROM notes WHERE project_id=? ORDER BY updated_at DESC", (project_id,)).fetchall()
        files = db.execute("SELECT * FROM files WHERE project_id=? ORDER BY id DESC", (project_id,)).fetchall()
    lines = [
        f"# Contexto del proyecto: {p['name']}", "",
        f"Categoría: {p['category']}", f"Descripción: {p['description']}", "",
        "## Instrucciones permanentes", p["permanent_instructions"] or "Sin instrucciones permanentes.", "",
        "## Notas",
    ]
    for note in notes:
        lines += [f"### {note['title']}", note["body"], ""]
    if not notes:
        lines += ["Sin notas todavía.", ""]
    lines += ["## Archivos disponibles"]
    for file in files:
        lines.append(f"- {file['original_name']} · SHA-256 {file['sha256']} · {file['size']} bytes")
        snippet = (file["extracted_text"] or "").strip()[:2500]
        if snippet:
            lines += ["", f"### Extracto: {file['original_name']}", snippet, ""]
    lines += ["", "## Regla", "Usa este contexto como fuente. Señala los datos faltantes y no inventes fuentes."]
    return "\n".join(lines)


@app.get("/api/projects/{project_id}/context")
def context_file(project_id: int) -> StreamingResponse:
    text = build_context(project_id)
    return StreamingResponse(
        io.BytesIO(text.encode("utf-8")),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="project-{project_id}-CONTEXT.md"'},
    )


@app.get("/api/projects/{project_id}/export")
def export_project(project_id: int) -> FileResponse:
    with connect() as db:
        p = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        files = db.execute("SELECT * FROM files WHERE project_id=?", (project_id,)).fetchall()
        notes = db.execute("SELECT * FROM notes WHERE project_id=?", (project_id,)).fetchall()
    if not p:
        raise HTTPException(404, "Proyecto no encontrado")
    target = EXPORTS_DIR / f"{safe_filename(p['name'])}-{project_id}.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project.json", json.dumps({"project": dict(p), "notes": [dict(n) for n in notes]}, ensure_ascii=False, indent=2))
        archive.writestr("CONTEXT.md", build_context(project_id))
        for file in files:
            source = Path(file["stored_path"])
            if source.exists():
                archive.write(source, f"files/{safe_filename(file['original_name'])}")
    with connect() as db:
        log_activity(db, project_id, "Proyecto exportado", target.name)
    return FileResponse(target, filename=target.name)


@app.get("/api/settings")
def get_settings() -> dict:
    with connect() as db:
        return {r["key"]: r["value"] for r in db.execute("SELECT * FROM settings")}


@app.put("/api/settings")
def update_settings(payload: SettingsIn) -> dict:
    values = payload.model_dump(exclude_none=True)
    with connect() as db:
        for key, value in values.items():
            db.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
    return get_settings()


@app.get("/api/ai/status")
def ai_status() -> dict:
    return ai_provider_status()


@app.post("/api/ai/secret")
def ai_save_secret(payload: AISecretRequest) -> dict:
    key_name = "openai_api_key" if payload.provider == "openai" else "compatible_api_key"
    try:
        set_secret(key_name, payload.api_key)
        return ai_provider_status()
    except Exception as exc:
        raise HTTPException(500, f"No se pudo guardar la credencial: {exc}") from exc


@app.delete("/api/ai/secret/{provider}")
def ai_delete_secret(provider: str) -> dict:
    if provider not in {"openai", "compatible"}:
        raise HTTPException(400, "Proveedor no admitido")
    key_name = "openai_api_key" if provider == "openai" else "compatible_api_key"
    try:
        delete_secret(key_name)
        return ai_provider_status()
    except Exception as exc:
        raise HTTPException(500, f"No se pudo eliminar la credencial: {exc}") from exc


@app.get("/api/ai/models/{provider}")
def ai_models(provider: str) -> dict:
    try:
        if provider == "openai":
            return {"models": ai_openai_models()}
        if provider == "compatible":
            return {"models": ai_compatible_models()}
        if provider == "ollama":
            return {"models": ai_ollama_models()}
        raise HTTPException(400, "Proveedor no admitido")
    except AIError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/ai/threads")
def ai_threads() -> dict:
    with connect() as db:
        rows = [dict(row) for row in db.execute(
            "SELECT t.*, (SELECT COUNT(*) FROM ai_messages m WHERE m.thread_id=t.id) message_count "
            "FROM ai_threads t ORDER BY t.updated_at DESC LIMIT 50"
        )]
    return {"threads": rows}


@app.get("/api/ai/threads/{thread_id}")
def ai_thread(thread_id: int) -> dict:
    with connect() as db:
        thread = db.execute("SELECT * FROM ai_threads WHERE id=?", (thread_id,)).fetchone()
        if not thread:
            raise HTTPException(404, "Conversación no encontrada")
        messages = [dict(row) for row in db.execute(
            "SELECT * FROM ai_messages WHERE thread_id=? ORDER BY id", (thread_id,)
        )]
    for message in messages:
        try:
            message["sources"] = json.loads(message.pop("sources_json", "[]") or "[]")
        except json.JSONDecodeError:
            message["sources"] = []
    return {"thread": dict(thread), "messages": messages}


@app.delete("/api/ai/threads/{thread_id}")
def ai_delete_thread(thread_id: int) -> dict:
    with connect() as db:
        db.execute("DELETE FROM ai_threads WHERE id=?", (thread_id,))
    return {"ok": True}


@app.post("/api/ai/chat")
def ai_chat(payload: AIChatRequest) -> dict:
    now = utc_now()
    with connect() as db:
        thread = None
        if payload.thread_id:
            thread = db.execute("SELECT * FROM ai_threads WHERE id=?", (payload.thread_id,)).fetchone()
            if not thread:
                raise HTTPException(404, "Conversación no encontrada")
            thread_id = int(thread["id"])
        else:
            title = re.sub(r"\s+", " ", payload.message).strip()[:68] or "Nueva conversación"
            cursor = db.execute(
                "INSERT INTO ai_threads(title,domain,created_at,updated_at) VALUES(?,?,?,?)",
                (title, payload.domain, now, now),
            )
            thread_id = int(cursor.lastrowid)
        previous_rows = [dict(row) for row in db.execute(
            "SELECT role,content FROM ai_messages WHERE thread_id=? ORDER BY id DESC LIMIT 14",
            (thread_id,),
        )]
        history = list(reversed(previous_rows))
        db.execute(
            "INSERT INTO ai_messages(thread_id,role,content,created_at) VALUES(?,?,?,?)",
            (thread_id, "user", payload.message, now),
        )
        db.execute("UPDATE ai_threads SET domain=?,updated_at=? WHERE id=?", (payload.domain, now, thread_id))

    local_results: list[dict] = []
    context = ""
    if payload.include_library and payload.domain != "general":
        local_results = search_knowledge(payload.domain, payload.message, 10)
        context = "\n\n".join(
            f"FUENTE LOCAL: {item['title']} ({item['category']})\n{item['snippet']}"
            for item in local_results
        )

    domain_rules = {
        "palworld": "Eres un especialista en Palworld. Distingue fuentes oficiales, datos técnicos del editor, guías comunitarias e inferencias. No inventes códigos internos.",
        "gamemod": "Eres un analista técnico de Unity, IL2CPP, Mono, Lua, Smali y dumps. Trabaja de forma reversible sobre copias propias y no facilites evasión de pagos, DRM o anticheat.",
        "libtool": "Eres un especialista en LibTool y Lua. Explica hallazgos, riesgos, pruebas y reversión; no presupongas funciones que no aparecen en el dump.",
        "research": "Eres un investigador académico riguroso. No inventes autores, DOI, páginas ni referencias; diferencia hechos, interpretación y puntos pendientes.",
        "legal": "Eres un asistente de investigación y redacción jurídica peruana. No inventes normas, artículos, plazos, competencia ni jurisprudencia.",
    }
    system = domain_rules.get(payload.domain, "Eres Dimitry AI, un asistente práctico, riguroso y claro. No inventes datos ni fuentes.")
    prompt = payload.message
    if context:
        prompt = f"""Consulta:
{payload.message}

Biblioteca local recuperada:
{context}

Usa la biblioteca cuando sea relevante, menciona el título de cada fuente local utilizada y señala cuando la evidencia no alcance."""
    try:
        result = ai_generate(
            prompt,
            system=system,
            purpose="code" if payload.domain in {"gamemod", "libtool"} else ("legal" if payload.domain == "legal" else ("research" if payload.web_search else "general")),
            allow_web=payload.web_search,
            preferred_provider=payload.provider,
            timeout=480,
            history=history,
        )
    except AIError as exc:
        raise HTTPException(503, str(exc)) from exc

    sources = list(result.sources)
    for item in local_results[:8]:
        sources.append({"title": item["title"], "url": "", "kind": "local", "category": item.get("category", "")})
    with connect() as db:
        db.execute(
            "INSERT INTO ai_messages(thread_id,role,content,provider,model,sources_json,created_at) VALUES(?,?,?,?,?,?,?)",
            (thread_id, "assistant", result.text, result.provider, result.model, json.dumps(sources, ensure_ascii=False), utc_now()),
        )
        db.execute("UPDATE ai_threads SET updated_at=? WHERE id=?", (utc_now(), thread_id))
    return {"thread_id": thread_id, **result.as_dict(), "sources": sources}


def _ai_call(
    prompt: str,
    *,
    system: str = "",
    purpose: str = "general",
    allow_web: bool = False,
    allowed_domains: list[str] | None = None,
    preferred_provider: str | None = None,
    timeout: int = 360,
    history: list[dict[str, str]] | None = None,
) -> dict:
    try:
        return ai_generate(
            prompt,
            system=system,
            purpose=purpose,
            allow_web=allow_web,
            allowed_domains=allowed_domains,
            preferred_provider=preferred_provider,
            timeout=timeout,
            history=history,
        ).as_dict()
    except AIError as exc:
        raise HTTPException(503, str(exc)) from exc


def _ollama_call(prompt: str, model: str | None = None, system: str | None = None, timeout: int = 240) -> dict:
    """Compatibilidad interna con versiones anteriores; fuerza Ollama cuando se solicita."""
    try:
        result = ai_generate(
            prompt,
            system=system or "",
            purpose="general",
            preferred_provider="ollama",
            timeout=timeout,
        )
        return result.as_dict()
    except AIError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/ollama/models")
def ollama_models() -> dict:
    return {"models": ai_ollama_models()}


@app.post("/api/ollama/generate")
def ollama_generate(payload: OllamaRequest) -> dict:
    try:
        result = ai_generate(
            payload.prompt,
            system=payload.system or "",
            preferred_provider="ollama",
        )
        return result.as_dict()
    except AIError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/study/generate")
def study_generate(payload: StudyRequest) -> dict:
    templates = {
        "ficha": "Crea una ficha de estudio completa, jerárquica, detallada y fácil de repasar. Conserva definiciones, clasificaciones, requisitos, procedimientos, excepciones y ejemplos.",
        "simple": "Explícalo desde cero, paso a paso, con palabras sencillas, ejemplos y comprobaciones de comprensión.",
        "exam": "Crea un simulacro con 20 preguntas abiertas, respuestas modelo, posibles repreguntas y errores frecuentes.",
        "speech": "Crea un guion oral natural y profesional, dividido por tiempos, con frases que pueda decir y posibles preguntas del docente.",
        "cards": "Crea tarjetas de memoria en formato Pregunta | Respuesta, sin omitir conceptos importantes.",
        "outline": "Ordena el material como un libro de estudio con títulos, subtítulos, cuadros y conexiones entre conceptos.",
    }
    try:
        baseline = generate_study_material(payload.kind, payload.content)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    instruction = templates.get(payload.kind, templates["ficha"])
    context = ""
    if payload.project_id:
        context = build_context(payload.project_id)[:35000]
    prompt = f"""{instruction}

Reglas:
- Responde en español.
- No inventes normas, fuentes ni datos.
- Señala claramente lo dudoso o faltante.
- Usa títulos en negro/negrita cuando el contenido se exporte; no dependas de colores.

Contexto del proyecto:
{context}

Contenido principal:
{payload.content}
"""
    try:
        result = _ai_call(
            prompt,
            system="Eres un tutor académico riguroso, claro y orientado a exámenes. Conserva la fidelidad al material proporcionado y separa con claridad temas, conceptos y preguntas de repaso.",
            purpose="study",
            allow_web=payload.use_web,
        )
        if not result.get("response", "").strip():
            raise HTTPException(503, "La IA no devolvió contenido")
        result.update({"word_count": baseline["word_count"], "section_count": baseline["section_count"], "kind": baseline["kind"]})
        return result
    except HTTPException as exc:
        return {
            **baseline,
            "provider": "local",
            "model": "generador estructurado sin conexión",
            "sources": [],
            "warning": f"Se utilizó el generador local porque la IA no estaba disponible: {exc.detail}",
        }


def _deterministic_monograph_structure(content: str) -> str:
    """Organiza material sin insertar instrucciones o datos inventados."""
    text = content.replace("\\r\\n", "\n").replace("\\n", "\n").strip()
    if re.search(r"(?im)^(?:#{1,3}\s+|\d+(?:\.\d+){0,2}[.)]?\s+|INTRODUCCIÓN\s*$)", text):
        return text
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if len(paragraphs) >= 2:
        intro = paragraphs[0]
        development = "\n\n".join(paragraphs[1:])
    else:
        sentences = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)
        intro = sentences[0]
        development = sentences[1] if len(sentences) > 1 else text
    return f"# Introducción\n\n{intro}\n\n# Desarrollo\n\n{development}"


@app.post("/api/monographs/prepare")
def prepare_monograph(payload: MonographRequest) -> dict:
    structured = _deterministic_monograph_structure(payload.content)
    warning = ""
    ai_info: dict = {}
    if payload.use_ai:
        prompt = f"""Estructura el siguiente contenido como una monografía académica en español y devuelve únicamente el cuerpo listo para Word usando encabezados Markdown #, ## y ###.

Requisitos:
- Conserva toda la información útil y elimina repeticiones evidentes.
- Incluye Introducción, desarrollo jerárquico, Conclusiones y Recomendaciones cuando correspondan.
- Usa citas parentéticas APA 7 solo cuando puedan deducirse con seguridad de la bibliografía entregada.
- No inventes autores, años, páginas, DOI, normas ni referencias.
- Cuando falte el respaldo de una afirmación que requiere fuente, coloca [cita pendiente].
- Los títulos deben pensarse en negro, negrita y sin estilo azul.
- No incluyas portada, índice ni lista de referencias; la aplicación los agrega.

Bibliografía disponible:
{payload.bibliography}

Contenido:
{payload.content}
"""
        try:
            result = _ai_call(
                prompt,
                system="Eres un editor académico experto en APA 7 y metodología universitaria.",
                purpose="research",
                allow_web=payload.use_web,
                timeout=420,
            )
            ai_info = result
            if result.get("response", "").strip():
                structured = result["response"].strip()
        except HTTPException as exc:
            warning = exc.detail
    metadata = payload.model_dump(exclude={"content", "bibliography", "use_ai", "use_web"})
    target = create_monograph_docx(metadata, structured, payload.bibliography)
    now = utc_now()
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO monographs(title,metadata_json,source_text,bibliography,structured_text,output_path,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (payload.title, json.dumps(metadata, ensure_ascii=False), payload.content, payload.bibliography, structured, str(target), now, now),
        )
        monograph_id = cursor.lastrowid
    return {
        "id": monograph_id,
        "structured_text": structured,
        "download_url": f"/api/generated/{target.name}",
        "filename": target.name,
        "warning": warning,
        "provider": ai_info.get("provider", ""),
        "model": ai_info.get("model", ""),
        "sources": ai_info.get("sources", []),
    }


@app.post("/api/legal/create")
def legal_create(payload: LegalRequest) -> dict:
    kind = payload.kind.replace("-", "_")
    fields = {key: str(value).strip() for key, value in payload.fields.items()}
    common_required = {
        "authority": "autoridad, entidad u organización",
        "applicant": "nombre de la persona solicitante o responsable",
        "address": "domicilio o lugar",
        "city_date": "ciudad y fecha",
        "sumilla": "sumilla u objeto",
    }
    missing = [label for key, label in common_required.items() if not fields.get(key)]
    if kind == "acta_reunion":
        if not fields.get("participants"):
            missing.append("participantes")
        if not payload.request_text.strip():
            missing.append("agenda")
        if not payload.facts.strip():
            missing.append("desarrollo de la reunión")
        if not payload.evidence.strip():
            missing.append("acuerdos")
    else:
        dni = re.sub(r"\D", "", fields.get("dni", ""))
        if len(dni) != 8:
            missing.append("DNI de ocho dígitos")
        else:
            fields["dni"] = dni
        if not payload.request_text.strip():
            missing.append("petitorio")
        if not payload.facts.strip():
            missing.append("fundamentos de hecho")
        if kind == "poder_simple":
            proxy_dni = re.sub(r"\D", "", fields.get("proxy_dni", ""))
            if not fields.get("proxy_name"):
                missing.append("nombre de la persona apoderada")
            if len(proxy_dni) != 8:
                missing.append("DNI de ocho dígitos de la persona apoderada")
            else:
                fields["proxy_dni"] = proxy_dni
    if missing:
        raise HTTPException(422, "Completa antes de generar: " + ", ".join(missing) + ".")

    if kind == "acta_reunion":
        parts = [
            ("AGENDA", payload.request_text),
            ("DESARROLLO DE LA REUNIÓN", payload.facts),
            ("ACUERDOS", payload.evidence),
            ("BASE LEGAL O DOCUMENTAL", payload.legal_basis),
        ]
    elif kind == "carta_notarial":
        parts = [
            ("REQUERIMIENTO", payload.request_text),
            ("ANTECEDENTES", payload.facts),
            ("FUNDAMENTO", payload.legal_basis),
            ("DOCUMENTOS QUE SE ACOMPAÑAN", payload.evidence),
        ]
    elif kind == "poder_simple":
        parts = [
            ("ALCANCE DEL PODER", payload.request_text),
            ("FACULTADES OTORGADAS", payload.facts),
            ("BASE LEGAL O DOCUMENTAL", payload.legal_basis),
            ("DOCUMENTOS QUE SE ACOMPAÑAN", payload.evidence),
        ]
    elif kind == "denuncia":
        parts = [
            ("OBJETO DE LA DENUNCIA", payload.request_text),
            ("FUNDAMENTOS DE HECHO", payload.facts),
            ("FUNDAMENTOS DE DERECHO", payload.legal_basis),
            ("MEDIOS PROBATORIOS Y ANEXOS", payload.evidence),
        ]
    else:
        parts = [
            ("PETITORIO", payload.request_text),
            ("FUNDAMENTOS DE HECHO", payload.facts),
            ("FUNDAMENTOS DE DERECHO", payload.legal_basis),
            ("MEDIOS PROBATORIOS Y ANEXOS", payload.evidence),
        ]
    populated_parts = [(heading, content.strip()) for heading, content in parts if content.strip()]
    expected_sections = ", ".join(heading.title() for heading, _ in populated_parts)
    roman = ("I", "II", "III", "IV")
    draft = "\n".join(
        item
        for index, (heading, content) in enumerate(populated_parts)
        for item in (f"{roman[index]}. {heading}", content)
    )
    warning = ""
    ai_info: dict = {}
    if payload.use_ai:
        prompt = f"""Redacta un borrador jurídico peruano del tipo '{payload.kind}'.

Reglas estrictas:
- Usa únicamente los hechos, petitorio, normas y pruebas proporcionados.
- No inventes artículos, jurisprudencia, plazos, competencias ni datos personales.
- Omite cualquier sección opcional que no tenga contenido; no insertes instrucciones ni marcadores internos.
- Conserva estas secciones y no añadas otras: {expected_sections}.
- Tono formal, claro y directo.
- Este es un borrador para revisión profesional.

Petitorio:
{payload.request_text}

Hechos:
{payload.facts}

Fundamento legal proporcionado:
{payload.legal_basis}

Pruebas y anexos:
{payload.evidence}
"""
        try:
            result = _ai_call(
                prompt,
                system="Eres un asistente de redacción jurídica peruana. Nunca inventas una norma. Distingue claramente entre norma verificada, dato aportado y punto pendiente.",
                purpose="legal",
                allow_web=payload.verify_web,
                allowed_domains=["gob.pe", "pj.gob.pe", "tc.gob.pe", "congreso.gob.pe", "elperuano.pe"],
                timeout=360,
            )
            ai_info = result
            if result.get("response", "").strip():
                draft = result["response"].strip()
        except HTTPException as exc:
            warning = exc.detail
    target = create_legal_docx(kind, fields, draft)
    return {
        "draft": draft,
        "download_url": f"/api/generated/{target.name}",
        "filename": target.name,
        "warning": warning,
        "provider": ai_info.get("provider", ""),
        "model": ai_info.get("model", ""),
        "sources": ai_info.get("sources", []),
    }


@app.get("/api/generated/{filename}")
def download_generated(filename: str) -> FileResponse:
    target = GENERATED_DIR / safe_filename(filename)
    if not target.exists():
        raise HTTPException(404, "Documento generado no encontrado")
    return FileResponse(target, filename=target.name)


@app.post("/api/gamemod/lua/analyze")
async def gamemod_lua_analyze(upload: UploadFile = File(...)) -> dict:
    if not upload.filename or Path(upload.filename).suffix.lower() != ".lua":
        raise HTTPException(400, "Selecciona un archivo con extensión .lua")
    content = await upload.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(413, "El archivo Lua supera el límite de 5 MB")
    try:
        source = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        source = content.decode("latin-1")
    try:
        return analyze_lua_source(source, safe_filename(upload.filename))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/gamemod/lua/generate")
def gamemod_lua_generate(payload: LuaGenerateRequest) -> dict:
    try:
        script = generate_gameguardian_script(payload.name, payload.author, payload.description, payload.changes)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    base_name = safe_filename(Path(payload.name).stem).strip(" .") or "script-gameguardian"
    target = GENERATED_DIR / safe_filename(f"{base_name}-{utc_now().replace(':', '-')}.lua")
    atomic_write_bytes(target, script.encode("utf-8"))
    return {
        "filename": target.name,
        "download_url": f"/api/generated/{target.name}",
        "preview": script,
        "actions": len([line for line in payload.changes.splitlines() if line.strip() and not line.lstrip().startswith(("#", "--"))]),
    }


@app.get("/api/knowledge/{domain}/summary")
def knowledge_summary(domain: str) -> dict:
    return source_summary(domain)


@app.get("/api/knowledge/{domain}/search")
def knowledge_search(domain: str, q: str, limit: int = 12) -> dict:
    return {"results": search_knowledge(domain, q, max(1, min(limit, 30)))}


@app.post("/api/knowledge/{domain}/upload")
async def knowledge_upload(domain: str, upload: UploadFile = File(...), name: str = Form("")) -> dict:
    if not upload.filename or Path(upload.filename).suffix.lower() != ".zip":
        raise HTTPException(400, "Sube un archivo ZIP")
    content = await upload.read()
    if len(content) > 800 * 1024 * 1024:
        raise HTTPException(413, "El ZIP supera el límite de 800 MB")
    temp_dir = KNOWLEDGE_DIR / "incoming"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"{utc_now().replace(':','-')}_{safe_filename(upload.filename)}"
    atomic_write_bytes(temp_path, content)
    try:
        result = import_bundle(temp_path, domain=domain, display_name=name or upload.filename)
        analysis = analyze_bundle(Path(result["extract_dir"]))
        result["analysis"] = analysis
        return result
    except zipfile.BadZipFile:
        raise HTTPException(400, "El archivo no es un ZIP válido")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        temp_path.unlink(missing_ok=True)


@app.get("/api/palworld/workspace/status")
def palworld_workspace_status(check_online: bool = False) -> dict:
    try:
        return {"editor": editor_status(check_online), "sessions": list_sessions()}
    except RuntimeError as exc:
        return {"editor": {**editor_status(False), "online_error": str(exc)}, "sessions": list_sessions()}


@app.post("/api/palworld/workspace/upload")
async def palworld_workspace_upload(
    upload: UploadFile = File(...), expected_kind: str = Form("auto"),
) -> dict:
    if not upload.filename:
        raise HTTPException(400, "Selecciona un archivo")
    ensure_workspace_dirs()
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, dir=INCOMING_DIR, suffix=".part") as temp:
            temp_path = Path(temp.name)
            total = 0
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "El archivo supera el límite de 2 GB")
                temp.write(chunk)
        return create_session_from_path(temp_path, upload.filename, expected_kind)
    except HTTPException:
        raise
    except (ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"No se pudo preparar el guardado: {exc}") from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


@app.get("/api/palworld/workspace/sessions")
def palworld_workspace_sessions() -> dict:
    return {"sessions": list_sessions()}


@app.delete("/api/palworld/workspace/sessions/{session_id}")
def palworld_workspace_delete(session_id: str) -> dict:
    try:
        delete_session(session_id)
        return {"deleted": True}
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/palworld/workspace/sessions/{session_id}/download")
def palworld_workspace_download(session_id: str, variant: str = "backup") -> FileResponse:
    try:
        target = session_file(session_id, variant)
        return FileResponse(target, filename=target.name)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/palworld/workspace/sessions/{session_id}/restore")
def palworld_workspace_restore(session_id: str) -> dict:
    try:
        return {"restored": True, "session": restore_session(session_id)}
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/palworld/workspace/sessions/{session_id}/open-folder")
def palworld_workspace_open_folder(session_id: str) -> dict:
    try:
        return open_session_folder(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/palworld/editor/install")
def palworld_editor_install() -> dict:
    try:
        return install_editor_latest()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/palworld/editor/launch")
def palworld_editor_launch(payload: PalEditorLaunchRequest) -> dict:
    try:
        return launch_editor(payload.session_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/palworld/sync")
def sync_palworld() -> dict:
    """Compatibilidad con v0.4: ahora ejecuta el centro completo de sincronización."""
    return run_full_sync("palworld-button")


@app.post("/api/palworld/sync-news")
def sync_palworld_news() -> dict:
    return run_full_sync("palworld-news-button")


@app.post("/api/sync/all")
def sync_all() -> dict:
    return run_full_sync("manual")


@app.get("/api/sync/status")
def get_sync_status() -> dict:
    return sync_status()


@app.post("/api/research/web-import")
def research_web_import(payload: WebImportRequest) -> dict:
    try:
        return import_web_source(payload.url, payload.domain, payload.title)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))


@app.post("/api/research/crossref/search")
def research_crossref_search(payload: CrossrefSearchRequest) -> dict:
    try:
        return {"results": crossref_search(payload.query, payload.rows)}
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(503, str(exc))


@app.get("/api/research/crossref/doi")
def research_crossref_doi(doi: str) -> dict:
    if not doi.strip():
        raise HTTPException(400, "Escribe un DOI")
    try:
        return crossref_doi(doi)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(503, str(exc))


@app.post("/api/research/items")
def research_save(payload: ResearchSaveRequest) -> dict:
    return save_research_item(payload.item)


@app.get("/api/research/items")
def research_list(limit: int = 100) -> dict:
    return {"items": list_research_items(limit)}


@app.post("/api/research/audit")
def research_audit(payload: BibliographyAuditRequest) -> dict:
    return bibliography_audit(payload.content, payload.bibliography)


@app.post("/api/research/suggest")
def research_suggest(payload: AutoResearchRequest) -> dict:
    try:
        return suggest_academic_sources(payload.title, payload.content, payload.rows)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))


@app.post("/api/knowledge/ask")
def knowledge_ask(payload: KnowledgeQuestion) -> dict:
    results = search_knowledge(payload.domain, payload.question, 10)
    local_answer = offline_knowledge_answer(payload.question, results)
    if not results:
        return {"answer": local_answer, "results": [], "offline": True}

    status = ai_provider_status()
    providers = status.get("providers", {}) if isinstance(status, dict) else {}
    ai_ready = bool(
        providers.get("openai", {}).get("configured")
        or providers.get("ollama", {}).get("online")
        or providers.get("compatible", {}).get("configured")
    )
    if not ai_ready:
        return {
            "answer": local_answer,
            "results": results,
            "offline": True,
            "warning": "Respuesta generada directamente desde la biblioteca local; no hay un proveedor de IA configurado.",
        }

    context = "\n\n".join(
        f"FUENTE: {item['title']} ({item['category']})\n{item['snippet']}"
        for item in results
    )
    domain_rules = {
        "palworld": "Responde sobre Palworld usando el material local. Distingue datos confirmados de inferencias y no inventes códigos internos.",
        "gamemod": "Ayuda a analizar mods, dumps, LibTool, Unity y Lua de forma técnica y reversible.",
        "libtool": "Ayuda con LibTool, dumps y Lua de manera reversible y segura.",
    }
    system = domain_rules.get(payload.domain, "Responde usando exclusivamente el contexto local recuperado.")
    prompt = f"""Pregunta del usuario:
{payload.question}

Contexto local recuperado:
{context}

Contesta en español, cita los títulos usados y señala claramente cuando la evidencia no alcance.
"""
    try:
        result = _ai_call(
            prompt, system=system,
            purpose="code" if payload.domain in {"gamemod", "libtool"} else "general",
            timeout=180,
        )
        answer = result.get("response", "").strip()
        return {"answer": answer or local_answer, "results": results, "offline": not bool(answer)}
    except HTTPException as exc:
        return {
            "answer": local_answer,
            "results": results,
            "offline": True,
            "warning": f"La IA no respondió; se usó la respuesta local. {exc.detail}",
        }

