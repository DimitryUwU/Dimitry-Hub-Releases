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
from .perfect_pals import (
    generate_profile_packages, generation_download, perfect_pals_status, search_live_catalog,
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


class PerfectPalsRequest(BaseModel):
    refresh_catalog: bool = True


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
    name = safe_filename(upload.filename or "arch×MuîÚ$z{-®éÜj×"Â$””’"Â$•b"¢G&gBÒ%Æâ"æ¦ö–â€¢—FVÐ¢f÷"–æFW‚Â††VF–ærÂ6öçFVçB’–âVçVÖW&FR‡÷VÆFVE÷'G2¢f÷"—FVÒ–â†b'·&öÖå¶–æFW…×Òâ¶†VF–æwÒ"Â6öçFVçB¢¢v&æ–ærÒ" ¢•ö–æfó¢F–7BÒ·Ð¢–b–ÆöBçW6Uö“ ¢&ö×BÒb""%&VF7FVâ&÷'&F÷"§W,:ÖF–6òW'VæòFVÂF—òw·–ÆöBæ¶–æGÒrà ¥&VvÆ2W7G&–7F3 ¢ÒW6;¦æ–6ÖVçFRÆ÷2†V6†÷2ÂWF—F÷&–òÂæ÷&Ö2’'VV&2&÷÷&6–öæF÷2à¢Òæò–çfVçFW2'L:Ö7VÆ÷2Â§W&—7'VFVæ6–ÂÆ¦÷2Â6ö×WFVæ6–2æ’FF÷2W'6öæÆW2à¢ÒöÖ—FR7VÇV–W"6V66œ;6â÷6–öæÂVRæòFVæv6öçFVæ–Fó²æò–ç6W'FW2–ç7G'V66–öæW2æ’Ö&6F÷&W2–çFW&æ÷2à¢Ò6öç6W'fW7F26V66–öæW2’æò;F2÷G&3¢¶W‡V7FVE÷6V7F–öç7Òà¢ÒFöæòf÷&ÖÂÂ6Æ&ò’F—&V7Fòà¢ÒW7FRW2Vâ&÷'&F÷"&&Wf—6œ;6â&öfW6–öæÂà Ð¥WF—F÷&–ó Ð§·–ÆöBç&WVW7E÷FW‡GÐÐ Ð¤†V6†÷3 Ð§·–ÆöBæf7G7ÐÐ Ð¤gVæFÖVçFòÆVvÂ&÷÷&6–öæFó Ð§·–ÆöBæÆVvÅö&6—7ÐÐ Ð¥'VV&2’æW†÷3 Ð§·–ÆöBæWf–FVæ6WÐÐ¢"" Ð¢G'“ Ð¢&W7VÇBÒö•ö6ÆÂ€Ð¢&ö×BÀÐ¢7—7FVÓÒ$W&W2Vâ6—7FVçFRFR&VF66œ;6â§W,:ÖF–6W'VæâçVæ6–çfVçF2Vææ÷&ÖâF—7F–æwVR6Æ&ÖVçFRVçG&Ræ÷&ÖfW&–f–6FÂFFò÷'FFò’VçFòVæF–VçFRâ"ÀÐ¢W'÷6SÒ&ÆVvÂ"ÀÐ¢ÆÆ÷u÷vV#×–ÆöBçfW&–g•÷vV"ÀÐ¢ÆÆ÷vVEöFöÖ–ç3Õ²&vö"çR"Â'¢ævö"çR"Â'F2ævö"çR"Â&6öæw&W6òævö"çR"Â&VÇW'VæòçR%ÒÀÐ¢F–ÖV÷WCÓ3cÀÐ¢Ð¢•ö–æfòÒ&W7VÇ@Ð¢–b&W7VÇBævWB‚'&W7öç6R"Â""’ç7G&—‚“ Ð¢G&gBÒ&W7VÇE²'&W7öç6R%Òç7G&—‚Ð¢W†6WB…EEW†6WF–öâ2W†3 Ð¢v&æ–ærÒW†2æFWF–ÀÐ¢F&vWBÒ7&VFUöÆVvÅöFö7‚†¶–æBÂf–VÆG2ÂG&gB¢&WGW&â°Ð¢&G&gB#¢G&gBÀÐ¢&F÷væÆöE÷W&Â#¢b"ö’övVæW&FVB÷·F&vWBææÖWÒ"ÀÐ¢&f–ÆVæÖR#¢F&vWBææÖRÀÐ¢'v&æ–ær#¢v&æ–ærÀÐ¢'&÷f–FW"#¢•ö–æfòævWB‚'&÷f–FW""Â""’ÀÐ¢&ÖöFVÂ#¢•ö–æfòævWB‚&ÖöFVÂ"Â""’ÀÐ¢'6÷W&6W2#¢•ö–æfòævWB‚'6÷W&6W2"ÂµÒ’ÀÐ¢ÐÐ Ð Ð¤ævWB‚"ö’övVæW&FVB÷¶f–ÆVæÖWÒ"¦FVbF÷væÆöEövVæW&FVB†f–ÆVæÖS¢7G"’Óâf–ÆU&W7öç6S ¢F&vWBÒtTäU$DTEôD•"ò6fUöf–ÆVæÖR†f–ÆVæÖRÐ¢–bæ÷BF&vWBæW†—7G2‚“ Ð¢&—6R…EEW†6WF–öâƒCBÂ$Fö7VÖVçFòvVæW&FòæòVæ6öçG&Fò"Ð¢&WGW&âf–ÆU&W7öç6R‡F&vWBÂf–ÆVæÖS×F&vWBææÖR  ¤ç÷7B‚"ö’övÖVÖöBöÇVöæÇ—¦R"¦7–æ2FVbvÖVÖöEöÇVöæÇ—¦R‡WÆöC¢WÆöDf–ÆRÒf–ÆR‚âââ’’ÓâF–7C ¢–bæ÷BWÆöBæf–ÆVæÖR÷"F‚‡WÆöBæf–ÆVæÖR’ç7Vff—‚æÆ÷vW"‚’Ò"æÇV# ¢&—6R…EEW†6WF–öâƒCÂ%6VÆV66–öæVâ&6†—fò6öâW‡FVç6œ;6âæÇV"¢6öçFVçBÒv—BWÆöBç&VB‚¢–bÆVâ†6öçFVçB’âR¢#B¢#C ¢&—6R…EEW†6WF–öâƒC2Â$VÂ&6†—fòÇV7WW&VÂÌ:ÖÖ—FRFRRÔ""¢G'“ ¢6÷W&6RÒ6öçFVçBæFV6öFR‚'WFbÓ‚×6–r"¢W†6WBVæ–6öFTFV6öFTW'&÷# ¢6÷W&6RÒ6öçFVçBæFV6öFR‚&ÆF–âÓ"¢G'“ ¢&WGW&âæÇ—¦UöÇV÷6÷W&6R‡6÷W&6RÂ6fUöf–ÆVæÖR‡WÆöBæf–ÆVæÖR’¢W†6WBfÇVTW'&÷"2W†3 ¢&—6R…EEW†6WF–öâƒCÂ7G"†W†2’  ¤ç÷7B‚"ö’övÖVÖöBöÇVövVæW&FR"¦FVbvÖVÖöEöÇVövVæW&FR‡–ÆöC¢ÇVvVæW&FU&WVW7B’ÓâF–7C ¢G'“ ¢67&—BÒvVæW&FUövÖVwV&F–å÷67&—B‡–ÆöBææÖRÂ–ÆöBæWF†÷"Â–ÆöBæFW67&—F–öâÂ–ÆöBæ6†ævW2¢W†6WBfÇVTW'&÷"2W†3 ¢&—6R…EEW†6WF–öâƒC#"Â7G"†W†2’¢&6UöæÖRÒ6fUöf–ÆVæÖR…F‚‡–ÆöBææÖR’ç7FVÒ’ç7G&—‚"â"’÷"'67&—BÖvÖVwV&F–â ¢F&vWBÒtTäU$DTEôD•"ò6fUöf–ÆVæÖR†b'¶&6UöæÖWÒ×·WF5öæ÷r‚’ç&WÆ6R‚s¢rÂrÒr—ÒæÇV"¢FöÖ–5÷w&—FUö'—FW2‡F&vWBÂ67&—BæVæ6öFR‚'WFbÓ‚"’¢&WGW&â°¢&f–ÆVæÖR#¢F&vWBææÖRÀ¢&F÷væÆöE÷W&Â#¢b"ö’övVæW&FVB÷·F&vWBææÖWÒ"À¢'&Wf–Wr#¢67&—BÀ¢&7F–öç2#¢ÆVâ…¶Æ–æRf÷"Æ–æR–â–ÆöBæ6†ævW2ç7Æ—FÆ–æW2‚’–bÆ–æRç7G&—‚’æBæ÷BÆ–æRæÇ7G&—‚’ç7F'G7v—F‚‚‚"2"Â"ÒÒ"’•Ò’À¢Ð Ð Ð¤ævWB‚"ö’ö¶æ÷vÆVFvR÷¶FöÖ–çÒ÷7VÖÖ'’"Ð¦FVb¶æ÷vÆVFvU÷7VÖÖ'’†FöÖ–ã¢7G"’ÓâF–7C Ð¢&WGW&â6÷W&6U÷7VÖÖ'’†FöÖ–âÐ Ð Ð¤ævWB‚"ö’ö¶æ÷vÆVFvR÷¶FöÖ–çÒ÷6V&6‚"Ð¦FVb¶æ÷vÆVFvU÷6V&6‚†FöÖ–ã¢7G"Â¢7G"ÂÆ–Ö—C¢–çBÒ"’ÓâF–7C Ð¢&WGW&â²'&W7VÇG2#¢6V&6…ö¶æ÷vÆVFvR†FöÖ–âÂÂÖ‚ƒÂÖ–â†Æ–Ö—BÂ3’’—ÐÐ Ð Ð¤ç÷7B‚"ö’ö¶æ÷vÆVFvR÷¶FöÖ–çÒ÷WÆöB"Ð¦7–æ2FVb¶æ÷vÆVFvU÷WÆöB†FöÖ–ã¢7G"ÂWÆöC¢WÆöDf–ÆRÒf–ÆR‚âââ’ÂæÖS¢7G"Òf÷&Ò‚""’’ÓâF–7C Ð¢–bæ÷BWÆöBæf–ÆVæÖR÷"F‚‡WÆöBæf–ÆVæÖR’ç7Vff—‚æÆ÷vW"‚’Ò"ç¦—# Ð¢&—6R…EEW†6WF–öâƒCÂ%7V&RVâ&6†—fò¤•"Ð¢6öçFVçBÒv—BWÆöBç&VB‚Ð¢–bÆVâ†6öçFVçB’âƒ¢#B¢#C Ð¢&—6R…EEW†6WF–öâƒC2Â$VÂ¤•7WW&VÂÌ:ÖÖ—FRFRƒÔ""Ð¢FV×öF—"Ò´äõtÄTDtUôD•"ò&–æ6öÖ–ær Ð¢FV×öF—"æÖ¶F—"‡&VçG3ÕG'VRÂW†—7Eöö³ÕG'VRÐ¢FV×÷F‚ÒFV×öF—"òb'·WF5öæ÷r‚’ç&WÆ6R‚s¢rÂrÒr—Õ÷·6fUöf–ÆVæÖR‡WÆöBæf–ÆVæÖR—Ò Ð¢FöÖ–5÷w&—FUö'—FW2‡FV×÷F‚Â6öçFVçBÐ¢G'“ Ð¢&W7VÇBÒ–×÷'Eö'VæFÆR‡FV×÷F‚ÂFöÖ–ãÖFöÖ–âÂF—7Æ•öæÖSÖæÖR÷"WÆöBæf–ÆVæÖRÐ¢æÇ—6—2ÒæÇ—¦Uö'VæFÆR…F‚‡&W7VÇE²&W‡G&7EöF—"%Ò’Ð¢&W7VÇE²&æÇ—6—2%ÒÒæÇ—6—0Ð¢&WGW&â&W7VÇ@Ð¢W†6WB¦—f–ÆRä&E¦—f–ÆS Ð¢&—6R…EEW†6WF–öâƒCÂ$VÂ&6†—fòæòW2Vâ¤•l:Æ–Fò"Ð¢W†6WBfÇVTW'&÷"2W†3 Ð¢&—6R…EEW†6WF–öâƒCÂ7G"†W†2’Ð¢f–æÆÇ“ Ð¢FV×÷F‚çVæÆ–æ²†Ö—76–æuöö³ÕG'VRÐ Ð Ð¤ævWB‚"ö’÷Çv÷&ÆB÷v÷&·76R÷7FGW2"Ð¦FVbÇv÷&ÆE÷v÷&·76U÷7FGW2†6†V6µööæÆ–æS¢&ööÂÒfÇ6R’ÓâF–7C Ð¢G'“ Ð¢&WGW&â²&VF—F÷"#¢VF—F÷%÷7FGW2†6†V6µööæÆ–æR’Â'6W76–öç2#¢Æ—7E÷6W76–öç2‚—ÐÐ¢W†6WB'VçF–ÖTW'&÷"2W†3 Ð¢&WGW&â²&VF—F÷"#¢²¢¦VF—F÷%÷7FGW2„fÇ6R’Â&öæÆ–æUöW'&÷"#¢7G"†W†2—ÒÂ'6W76–öç2#¢Æ—7E÷6W76–öç2‚—ÐÐ Ð Ð¤ç÷7B‚"ö’÷Çv÷&ÆB÷v÷&·76R÷WÆöB"Ð¦7–æ2FVbÇv÷&ÆE÷v÷&·76U÷WÆöB€Ð¢WÆöC¢WÆöDf–ÆRÒf–ÆR‚âââ’ÂW‡V7FVEö¶–æC¢7G"Òf÷&Ò‚&WFò"’ÀÐ¢’ÓâF–7C Ð¢–bæ÷BWÆöBæf–ÆVæÖS Ð¢&—6R…EEW†6WF–öâƒCÂ%6VÆV66–öæVâ&6†—fò"Ð¢Vç7W&U÷v÷&·76UöF—'2‚Ð¢FV×÷Fƒ¢F‚ÂæöæRÒæöæPÐ¢G'“ Ð¢v—F‚FV×f–ÆRäæÖVEFV×÷&'”f–ÆR†FVÆWFSÔfÇ6RÂF—#Ô”ä4ôÔ”äuôD•"Â7Vff—ƒÒ"ç'B"’2FV× Ð¢FV×÷F‚ÒF‚‡FV×ææÖRÐ¢F÷FÂÒ Ð¢v†–ÆRG'VS Ð¢6‡Væ²Òv—BWÆöBç&VBƒ#B¢#BÐ¢–bæ÷B6‡Væ³ Ð¢'&V°Ð¢F÷FÂ³ÒÆVâ†6‡Væ²Ð¢–bF÷FÂâÔ…õUÄôEô%•DU3 Ð¢&—6R…EEW†6WF–öâƒC2Â$VÂ&6†—fò7WW&VÂÌ:ÖÖ—FRFR"t""Ð¢FV×çw&—FR†6‡Væ²Ð¢&WGW&â7&VFU÷6W76–öåög&öÕ÷F‚‡FV×÷F‚ÂWÆöBæf–ÆVæÖRÂW‡V7FVEö¶–æBÐ¢W†6WB…EEW†6WF–öã Ð¢&—6PÐ¢W†6WB…fÇVTW'&÷"Â¦—f–ÆRä&E¦—f–ÆR’2W†3 Ð¢&—6R…EEW†6WF–öâƒCÂ7G"†W†2’’g&öÒW†0Ð¢W†6WBW†6WF–öâ2W†3 Ð¢&—6R…EEW†6WF–öâƒSÂb$æò6RVFò&W&"VÂwV&FFó¢¶W†7Ò"’g&öÒW†0Ð¢f–æÆÇ“ Ð¢–bFV×÷F‚æBFV×÷F‚æW†—7G2‚“ Ð¢FV×÷F‚çVæÆ–æ²†Ö—76–æuöö³ÕG'VRÐ Ð Ð¤ævWB‚"ö’÷Çv÷&ÆB÷v÷&·76R÷6W76–öç2"Ð¦FVbÇv÷&ÆE÷v÷&·76U÷6W76–öç2‚’ÓâF–7C Ð¢&WGW&â²'6W76–öç2#¢Æ—7E÷6W76–öç2‚—ÐÐ Ð Ð¤æFVÆWFR‚"ö’÷Çv÷&ÆB÷v÷&·76R÷6W76–öç2÷·6W76–öåö–GÒ"Ð¦FVbÇv÷&ÆE÷v÷&·76UöFVÆWFR‡6W76–öåö–C¢7G"’ÓâF–7C Ð¢G'“ Ð¢FVÆWFU÷6W76–öâ‡6W76–öåö–BÐ¢&WGW&â²&FVÆWFVB#¢G'VWÐÐ¢W†6WBf–ÆTæ÷Df÷VæDW'&÷"2W†3 Ð¢&—6R…EEW†6WF–öâƒCBÂ7G"†W†2’’g&öÒW†0Ð¢W†6WBfÇVTW'&÷"2W†3 Ð¢&—6R…EEW†6WF–öâƒCÂ7G"†W†2’’g&öÒW†0Ð Ð Ð¤ævWB‚"ö’÷Çv÷&ÆB÷v÷&·76R÷6W76–öç2÷·6W76–öåö–GÒöF÷væÆöB"¦FVbÇv÷&ÆE÷v÷&·76UöF÷væÆöB‡6W76–öåö–C¢7G"Âf&–çC¢7G"Ò&&6·W"’Óâf–ÆU&W7öç6S Ð¢G'“ Ð¢F&vWBÒ6W76–öåöf–ÆR‡6W76–öåö–BÂf&–çBÐ¢&WGW&âf–ÆU&W7öç6R‡F&vWBÂf–ÆVæÖS×F&vWBææÖRÐ¢W†6WBf–ÆTæ÷Df÷VæDW'&÷"2W†3 Ð¢&—6R…EEW†6WF–öâƒCBÂ7G"†W†2’’g&öÒW†0Ð¢W†6WBfÇVTW'&÷"2W†3 Ð¢&—6R…EEW†6WF–öâƒCÂ7G"†W†2’’g&öÒW†0  ¤ç÷7B‚"ö’÷Çv÷&ÆB÷v÷&·76R÷6W76–öç2÷·6W76–öåö–GÒ÷&W7F÷&R"¦FVbÇv÷&ÆE÷v÷&·76U÷&W7F÷&R‡6W76–öåö–C¢7G"’ÓâF–7C ¢G'“ ¢&WGW&â²'&W7F÷&VB#¢G'VRÂ'6W76–öâ#¢&W7F÷&U÷6W76–öâ‡6W76–öåö–B—Ð¢W†6WBf–ÆTæ÷Df÷VæDW'&÷"2W†3 ¢&—6R…EEW†6WF–öâƒCBÂ7G"†W†2’’g&öÒW†0¢W†6WBfÇVTW'&÷"2W†3 ¢&—6R…EEW†6WF–öâƒCÂ7G"†W†2’’g&öÒW†0 Ð Ð¤ç÷7B‚"ö’÷Çv÷&ÆB÷v÷&·76R÷6W76–öç2÷·6W76–öåö–GÒö÷VâÖföÆFW""Ð¦FVbÇv÷&ÆE÷v÷&·76Uö÷VåöföÆFW"‡6W76–öåö–C¢7G"’ÓâF–7C Ð¢G'“ Ð¢&WGW&â÷Vå÷6W76–öåöföÆFW"‡6W76–öåö–BÐ¢W†6WBf–ÆTæ÷Df÷VæDW'&÷"2W†3 Ð¢&—6R…EEW†6WF–öâƒCBÂ7G"†W†2’’g&öÒW†0Ð¢W†6WB…fÇVTW'&÷"Â'VçF–ÖTW'&÷"’2W†3 Ð¢&—6R…EEW†6WF–öâƒCÂ7G"†W†2’’g&öÒW†0Ð Ð Ð¤ç÷7B‚"ö’÷Çv÷&ÆBöVF—F÷"ö–ç7FÆÂ"Ð¦FVbÇv÷&ÆEöVF—F÷%ö–ç7FÆÂ‚’ÓâF–7C Ð¢G'“ Ð¢&WGW&â–ç7FÆÅöVF—F÷%öÆFW7B‚Ð¢W†6WB'VçF–ÖTW'&÷"2W†3 Ð¢&—6R…EEW†6WF–öâƒS2Â7G"†W†2’’g&öÒW†0Ð Ð Ð¤ç÷7B‚"ö’÷Çv÷&ÆBöVF—F÷"öÆVæ6‚"¦FVbÇv÷&ÆEöVF—F÷%öÆVæ6‚‡–ÆöC¢ÄVF—F÷$ÆVæ6…&WVW7B’ÓâF–7C ¢G'“ Ð¢&WGW&âÆVæ6…öVF—F÷"‡–ÆöBç6W76–öåö–BÐ¢W†6WBf–ÆTæ÷Df÷VæDW'&÷"2W†3 Ð¢&—6R…EEW†6WF–öâƒCBÂ7G"†W†2’’g&öÒW†0Ð¢W†6WB…fÇVTW'&÷"Â'VçF–ÖTW'&÷"’2W†3 ¢&—6R…EEW†6WF–öâƒCÂ7G"†W†2’’g&öÒW†0  ¤ævWB‚"ö’÷Çv÷&ÆB÷W&fV7B×Ç2÷7FGW2"¦FVbÇv÷&ÆE÷W&fV7E÷Ç5÷7FGW2‚’ÓâF–7C ¢&WGW&âW&fV7E÷Ç5÷7FGW2‚  ¤ç÷7B‚"ö’÷Çv÷&ÆB÷W&fV7B×Ç2övVæW&FR"¦FVbÇv÷&ÆE÷W&fV7E÷Ç5övVæW&FR‡–ÆöC¢W&fV7EÇ5&WVW7B’ÓâF–7C ¢G'“ ¢&WGW&âvVæW&FU÷&öf–ÆU÷6¶vW2‡&Vg&W6ƒ×–ÆöBç&Vg&W6…ö6FÆör¢W†6WB'VçF–ÖTW'&÷"2W†3 ¢&—6R…EEW†6WF–öâƒS2Â7G"†W†2’’g&öÒW†0  ¤ævWB‚"ö’÷Çv÷&ÆB÷W&fV7B×Ç2öF÷væÆöB÷¶vVæW&F–öåö–GÒ÷¶f–ÆUö¶W—Ò"¦FVbÇv÷&ÆE÷W&fV7E÷Ç5öF÷væÆöB†vVæW&F–öåö–C¢7G"Âf–ÆUö¶W“¢7G"’Óâf–ÆU&W7öç6S ¢G'“ ¢F&vWBÂæÖRÒvVæW&F–öåöF÷væÆöB†vVæW&F–öåö–BÂf–ÆUö¶W’¢&WGW&âf–ÆU&W7öç6R‡F&vWBÂf–ÆVæÖSÖæÖR¢W†6WBf–ÆTæ÷Df÷VæDW'&÷"2W†3 ¢&—6R…EEW†6WF–öâƒCBÂ7G"†W†2’’g&öÒW†0¢W†6WBfÇVTW'&÷"2W†3 ¢&—6R…EEW†6WF–öâƒCÂ7G"†W†2’’g&öÒW†0  ¤ç÷7B‚"ö’÷Çv÷&ÆB÷7–æ2"¦FVb7–æ5÷Çv÷&ÆB‚’ÓâF–7C ¢""$6ö×F–&–Æ–FB6öâcãC¢†÷&V¦V7WFVÂ6VçG&ò6ö×ÆWFòFR6–æ7&öæ—¦6œ;6ââ"" Ð¢&WGW&â'VåögVÆÅ÷7–æ2‚'Çv÷&ÆBÖ'WGFöâ"Ð Ð Ð¤ç÷7B‚"ö’÷Çv÷&ÆB÷7–æ2ÖæWw2"Ð¦FVb7–æ5÷Çv÷&ÆEöæWw2‚’ÓâF–7C Ð¢&WGW&â'VåögVÆÅ÷7–æ2‚'Çv÷&ÆBÖæWw2Ö'WGFöâ"Ð Ð Ð¤ç÷7B‚"ö’÷7–æ2öÆÂ"Ð¦FVb7–æ5öÆÂ‚’ÓâF–7C Ð¢&WGW&â'VåögVÆÅ÷7–æ2‚&ÖçVÂ"Ð Ð Ð¤ævWB‚"ö’÷7–æ2÷7FGW2"Ð¦FVbvWE÷7–æ5÷7FGW2‚’ÓâF–7C Ð¢&WGW&â7–æ5÷7FGW2‚Ð Ð Ð¤ç÷7B‚"ö’÷&W6V&6‚÷vV"Ö–×÷'B"Ð¦FVb&W6V&6…÷vV%ö–×÷'B‡–ÆöC¢vV$–×÷'E&WVW7B’ÓâF–7C Ð¢G'“ Ð¢&WGW&â–×÷'E÷vV%÷6÷W&6R‡–ÆöBçW&ÂÂ–ÆöBæFöÖ–âÂ–ÆöBçF—FÆRÐ¢W†6WBfÇVTW'&÷"2W†3 Ð¢&—6R…EEW†6WF–öâƒCÂ7G"†W†2’Ð¢W†6WB'VçF–ÖTW'&÷"2W†3 Ð¢&—6R…EEW†6WF–öâƒS2Â7G"†W†2’Ð Ð Ð¤ç÷7B‚"ö’÷&W6V&6‚ö7&÷77&Vb÷6V&6‚"Ð¦FVb&W6V&6…ö7&÷77&Ve÷6V&6‚‡–ÆöC¢7&÷77&Ve6V&6…&WVW7B’ÓâF–7C Ð¢G'“ Ð¢&WGW&â²'&W7VÇG2#¢7&÷77&Ve÷6V&6‚‡–ÆöBçVW'’Â–ÆöBç&÷w2—ÐÐ¢W†6WB…'VçF–ÖTW'&÷"ÂfÇVTW'&÷"’2W†3 Ð¢&—6R…EEW†6WF–öâƒS2Â7G"†W†2’Ð Ð Ð¤ævWB‚"ö’÷&W6V&6‚ö7&÷77&VböFö’"Ð¦FVb&W6V&6…ö7&÷77&VeöFö’†Fö“¢7G"’ÓâF–7C Ð¢–bæ÷BFö’ç7G&—‚“ Ð¢&—6R…EEW†6WF–öâƒCÂ$W67&–&RVâDô’"Ð¢G'“ Ð¢&WGW&â7&÷77&VeöFö’†Fö’Ð¢W†6WB…'VçF–ÖTW'&÷"ÂfÇVTW'&÷"’2W†3 Ð¢&—6R…EEW†6WF–öâƒS2Â7G"†W†2’Ð Ð Ð¤ç÷7B‚"ö’÷&W6V&6‚ö—FV×2"Ð¦FVb&W6V&6…÷6fR‡–ÆöC¢&W6V&6…6fU&WVW7B’ÓâF–7C Ð¢&WGW&â6fU÷&W6V&6…ö—FVÒ‡–ÆöBæ—FVÒÐ Ð Ð¤ævWB‚"ö’÷&W6V&6‚ö—FV×2"Ð¦FVb&W6V&6…öÆ—7B†Æ–Ö—C¢–çBÒ’ÓâF–7C Ð¢&WGW&â²&—FV×2#¢Æ—7E÷&W6V&6…ö—FV×2†Æ–Ö—B—ÐÐ Ð Ð¤ç÷7B‚"ö’÷&W6V&6‚öVF—B"Ð¦FVb&W6V&6…öVF—B‡–ÆöC¢&–&Æ–öw&‡”VF—E&WVW7B’ÓâF–7C Ð¢&WGW&â&–&Æ–öw&‡•öVF—B‡–ÆöBæ6öçFVçBÂ–ÆöBæ&–&Æ–öw&‡’Ð Ð Ð¤ç÷7B‚"ö’÷&W6V&6‚÷7VvvW7B"Ð¦FVb&W6V&6…÷7VvvW7B‡–ÆöC¢WFõ&W6V&6…&WVW7B’ÓâF–7C Ð¢G'“ Ð¢&WGW&â7VvvW7Eö6FVÖ–5÷6÷W&6W2‡–ÆöBçF—FÆRÂ–ÆöBæ6öçFVçBÂ–ÆöBç&÷w2Ð¢W†6WBfÇVTW'&÷"2W†3 Ð¢&—6R…EEW†6WF–öâƒCÂ7G"†W†2’Ð¢W†6WB'VçF–ÖTW'&÷"2W†3 Ð¢&—6R…EEW†6WF–öâƒS2Â7G"†W†2’Ð Ð Ð¤ç÷7B‚"ö’ö¶æ÷vÆVFvRö6²"Ð¦FVb¶æ÷vÆVFvUö6²‡–ÆöC¢¶æ÷vÆVFvUVW7F–öâ’ÓâF–7C ¢&W7VÇG2Ò6V&6…ö¶æ÷vÆVFvR‡–ÆöBæFöÖ–âÂ–ÆöBçVW7F–öâÂ¢–b–ÆöBæFöÖ–âÓÒ'Çv÷&ÆB# ¢Æ—fU÷&W7VÇG2Ò6V&6…öÆ—fUö6FÆör‡–ÆöBçVW7F–öâÂR¢W†—7F–ærÒ¶—FVÒævWB‚'6÷W&6Uö¶W’"’f÷"—FVÒ–âÆ—fU÷&W7VÇG7Ð¢&W7VÇG2Ò†Æ—fU÷&W7VÇG2²¶—FVÒf÷"—FVÒ–â&W7VÇG2–b—FVÒævWB‚'6÷W&6Uö¶W’"’æ÷B–âW†—7F–æuÒ•³£Ð¢Æö6Åöç7vW"ÒöffÆ–æUö¶æ÷vÆVFvUöç7vW"‡–ÆöBçVW7F–öâÂ&W7VÇG2Ð¢–bæ÷B&W7VÇG3 Ð¢&WGW&â²&ç7vW"#¢Æö6Åöç7vW"Â'&W7VÇG2#¢µÒÂ&öffÆ–æR#¢G'VWÐÐ Ð¢7FGW2Ò•÷&÷f–FW%÷7FGW2‚Ð¢&÷f–FW'2Ò7FGW2ævWB‚'&÷f–FW'2"Â·Ò’–b—6–ç7Fæ6R‡7FGW2ÂF–7B’VÇ6R·ÐÐ¢•÷&VG’Ò&ööÂ€Ð¢&÷f–FW'2ævWB‚&÷Væ’"Â·Ò’ævWB‚&6öæf–wW&VB"Ð¢÷"&÷f–FW'2ævWB‚&öÆÆÖ"Â·Ò’ævWB‚&öæÆ–æR"Ð¢÷"&÷f–FW'2ævWB‚&6ö×F–&ÆR"Â·Ò’ævWB‚&6öæf–wW&VB"Ð¢Ð¢–bæ÷B•÷&VG“ Ð¢&WGW&â°Ð¢&ç7vW"#¢Æö6Åöç7vW"ÀÐ¢'&W7VÇG2#¢&W7VÇG2ÀÐ¢&öffÆ–æR#¢G'VRÀÐ¢'v&æ–ær#¢%&W7VW7FvVæW&FF—&V7FÖVçFRFW6FRÆ&–&Æ–÷FV6Æö6Ã²æò†’Vâ&÷fVVF÷"FR”6öæf–wW&Fòâ"ÀÐ¢ÐÐ Ð¢6öçFW‡BÒ%ÆåÆâ"æ¦ö–â€Ð¢b$eTTåDS¢¶—FVÕ²wF—FÆRu×Ò‡¶—FVÕ²v6FVv÷'’u×Ò•Æç¶—FVÕ²w6æ—WBu×Ò Ð¢f÷"—FVÒ–â&W7VÇG0Ð¢Ð¢FöÖ–å÷'VÆW2Ò°Ð¢'Çv÷&ÆB#¢%&W7öæFR6ö'&RÇv÷&ÆBW6æFòVÂÖFW&–ÂÆö6ÂâF—7F–æwVRFF÷26öæf—&ÖF÷2FR–æfW&Væ6–2’æò–çfVçFW2<;6F–v÷2–çFW&æ÷2â"ÀÐ¢&vÖVÖöB#¢$—VFæÆ—¦"ÖöG2ÂGV×2ÂÆ–%FööÂÂVæ—G’’ÇVFRf÷&ÖL:–6æ–6’&WfW'6–&ÆRâ"ÀÐ¢&Æ–'FööÂ#¢$—VF6öâÆ–%FööÂÂGV×2’ÇVFRÖæW&&WfW'6–&ÆR’6VwW&â"ÀÐ¢ÐÐ¢7—7FVÒÒFöÖ–å÷'VÆW2ævWB‡–ÆöBæFöÖ–âÂ%&W7öæFRW6æFòW†6ÇW6—fÖVçFRVÂ6öçFW‡FòÆö6Â&V7WW&Fòâ"Ð¢&ö×BÒb""%&VwVçFFVÂW7V&–ó Ð§·–ÆöBçVW7F–öçÐÐ Ð¤6öçFW‡FòÆö6Â&V7WW&Fó Ð§¶6öçFW‡GÐÐ Ð¤6öçFW7FVâW7;öÂÂ6—FÆ÷2L:×GVÆ÷2W6F÷2’6\;Æ6Æ&ÖVçFR7VæFòÆWf–FVæ6–æòÆ6æ6RàÐ¢"" Ð¢G'“ Ð¢&W7VÇBÒö•ö6ÆÂ€Ð¢&ö×BÂ7—7FVÓ×7—7FVÒÀÐ¢W'÷6SÒ&6öFR"–b–ÆöBæFöÖ–â–â²&vÖVÖöB"Â&Æ–'FööÂ'ÒVÇ6R&vVæW&Â"ÀÐ¢F–ÖV÷WCÓƒÀÐ¢Ð¢ç7vW"Ò&W7VÇBævWB‚'&W7öç6R"Â""’ç7G&—‚Ð¢&WGW&â²&ç7vW"#¢ç7vW"÷"Æö6Åöç7vW"Â'&W7VÇG2#¢&W7VÇG2Â&öffÆ–æR#¢æ÷B&ööÂ†ç7vW"—ÐÐ¢W†6WB…EEW†6WF–öâ2W†3 Ð¢&WGW&â°Ð¢&ç7vW"#¢Æö6Åöç7vW"ÀÐ¢'&W7VÇG2#¢&W7VÇG2ÀÐ¢&öffÆ–æR#¢G'VRÀÐ¢'v&æ–ær#¢b$Æ”æò&W7öæFœ;3²6RW<;2Æ&W7VW7FÆö6Ââ¶W†2æFWF–ÇÒ"ÀÐ¢ÐÐ Ð