from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
    ASSET_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))
else:
    ROOT = Path(__file__).resolve().parent.parent
    ASSET_ROOT = ROOT
def _default_data_dir() -> Path:
    configured = os.environ.get("DIMITRY_HUB_DATA")
    if configured:
        return Path(configured).expanduser()
    if getattr(sys, "frozen", False) and os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "Dimitry Hub" / "Data"
    return ROOT / "data"


DATA_DIR = _default_data_dir()
FILES_DIR = DATA_DIR / "files"
BACKUPS_DIR = DATA_DIR / "backups"
EXPORTS_DIR = DATA_DIR / "exports"
GENERATED_DIR = DATA_DIR / "generated"
KNOWLEDGE_DIR = DATA_DIR / "knowledge"
PALWORLD_DIR = KNOWLEDGE_DIR / "palworld"
LIBTOOL_DIR = KNOWLEDGE_DIR / "libtool"
DB_PATH = DATA_DIR / "dimitry_hub.db"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_dirs() -> None:
    for directory in (
        DATA_DIR,
        FILES_DIR,
        BACKUPS_DIR,
        EXPORTS_DIR,
        GENERATED_DIR,
        KNOWLEDGE_DIR,
        PALWORLD_DIR,
        LIBTOOL_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    ensure_dirs()
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    ensure_dirs()
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'General',
                description TEXT NOT NULL DEFAULT '',
                permanent_instructions TEXT NOT NULL DEFAULT '',
                accent TEXT NOT NULL DEFAULT 'blue',
                archived INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                original_name TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
                size INTEGER NOT NULL DEFAULT 0,
                sha256 TEXT NOT NULL,
                extracted_text TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
                action TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS monographs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                source_text TEXT NOT NULL DEFAULT '',
                bibliography TEXT NOT NULL DEFAULT '',
                structured_text TEXT NOT NULL DEFAULT '',
                output_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS knowledge_bundles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL,
                name TEXT NOT NULL,
                original_name TEXT NOT NULL DEFAULT '',
                stored_path TEXT NOT NULL DEFAULT '',
                file_count INTEGER NOT NULL DEFAULT 0,
                entry_count INTEGER NOT NULL DEFAULT 0,
                source_url TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS knowledge_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',
                source TEXT NOT NULL DEFAULT '',
                source_key TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                UNIQUE(domain, source, source_key)
            );

            CREATE TABLE IF NOT EXISTS update_sources (
                source_key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                domain TEXT NOT NULL DEFAULT 'general',
                url TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                etag TEXT NOT NULL DEFAULT '',
                last_modified TEXT NOT NULL DEFAULT '',
                current_version TEXT NOT NULL DEFAULT '',
                current_digest TEXT NOT NULL DEFAULT '',
                last_checked TEXT NOT NULL DEFAULT '',
                last_changed TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger TEXT NOT NULL DEFAULT 'manual',
                status TEXT NOT NULL DEFAULT 'running',
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '',
                changed_count INTEGER NOT NULL DEFAULT 0,
                summary_json TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS sync_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER REFERENCES sync_runs(id) ON DELETE CASCADE,
                source_key TEXT NOT NULL DEFAULT '',
                severity TEXT NOT NULL DEFAULT 'info',
                title TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS source_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL,
                version TEXT NOT NULL DEFAULT '',
                digest TEXT NOT NULL DEFAULT '',
                stored_path TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS research_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                url TEXT NOT NULL DEFAULT '',
                source_type TEXT NOT NULL DEFAULT 'web',
                authors TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT '',
                doi TEXT NOT NULL DEFAULT '',
                apa_reference TEXT NOT NULL DEFAULT '',
                abstract TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS web_cache (
                cache_key TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                etag TEXT NOT NULL DEFAULT '',
                last_modified TEXT NOT NULL DEFAULT '',
                status_code INTEGER NOT NULL DEFAULT 0,
                content_type TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ai_threads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL DEFAULT 'Nueva conversación',
                domain TEXT NOT NULL DEFAULT 'general',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ai_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id INTEGER NOT NULL REFERENCES ai_threads(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                sources_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_knowledge_domain ON knowledge_entries(domain);
            CREATE INDEX IF NOT EXISTS idx_knowledge_title ON knowledge_entries(title);
            CREATE INDEX IF NOT EXISTS idx_files_project ON files(project_id);
            CREATE INDEX IF NOT EXISTS idx_sync_events_run ON sync_events(run_id);
            CREATE INDEX IF NOT EXISTS idx_research_doi ON research_items(doi);
            CREATE INDEX IF NOT EXISTS idx_research_title ON research_items(title);
            CREATE INDEX IF NOT EXISTS idx_ai_messages_thread ON ai_messages(thread_id);
            """
        )
        count = db.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
        if count == 0:
            seed_projects(db)
        defaults = {
            "theme": "obsidian-gold",
            "ollama_url": "http://127.0.0.1:11434",
            "ollama_model": "",
            "display_name": "Dimitry",
            "simple_mode": "1",
            "first_run_done": "0",
            "palworld_repo_url": "https://github.com/oMaN-Rod/palworld-save-pal/archive/refs/heads/main.zip",
            "last_palworld_sync": "",
            "last_palworld_news_sync": "",
            "auto_sync_mode": "safe",
            "auto_sync_interval_hours": "24",
            "sync_on_startup": "1",
            "last_full_sync": "",
            "crossref_email": "",
            "github_token": "",
            "internet_enabled": "1",
            "research_default_domain": "research",
            "font_style": "study-pro",
            "ai_mode": "automatic",
            "ai_provider": "automatic",
            "ai_fallback_local": "1",
            "ai_web_search_default": "1",
            "ai_reasoning_effort": "medium",
            "ai_pro_mode": "0",
            "openai_model": "gpt-5.6",
            "openai_general_model": "",
            "openai_research_model": "",
            "openai_code_model": "",
            "openai_legal_model": "",
            "openai_study_model": "",
            "compatible_base_url": "",
            "compatible_model": "",
            "compatible_no_key": "0",
            "ollama_think": "off",
        }
        for key, value in defaults.items():
            db.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                (key, value),
            )
        seed_update_sources(db)
        seed_knowledge(db)


def seed_update_sources(db: sqlite3.Connection) -> None:
    sources = [
        (
            "palworld-steam-news",
            "Noticias oficiales de Palworld",
            "steam_news",
            "palworld",
            "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/?appid=1623730&count=80&maxlength=0&format=json",
        ),
        (
            "palworld-editor-github",
            "Palworld Save Pal",
            "github_repo",
            "palworld",
            "https://github.com/oMaN-Rod/palworld-save-pal",
        ),
        (
            "crossref-metadata",
            "Crossref académico",
            "crossref",
            "research",
            "https://api.crossref.org/works",
        ),
    ]
    for source_key, name, kind, domain, url in sources:
        db.execute(
            """
            INSERT OR IGNORE INTO update_sources(source_key,name,kind,domain,url,enabled,status)
            VALUES(?,?,?,?,?,1,'pending')
            """,
            (source_key, name, kind, domain, url),
        )


def seed_projects(db: sqlite3.Connection) -> None:
    now = utc_now()
    projects = [
        (
            "Derecho UTEA",
            "Estudio",
            "Apuntes, fichas, monografías y exposiciones de Derecho.",
            "Organizar de forma jerárquica, completa y clara. Separar hechos, normas, doctrina y ejemplos. No inventar citas ni fuentes.",
            "blue",
        ),
        (
            "Palworld — Proyecto principal",
            "Gaming",
            "Reglas, datos y respaldos del proyecto Palworld.",
            "Crear siempre una copia antes de tocar un save. Conservar decisiones definitivas, códigos verificados, variantes problemáticas y reglas de la caja global.",
            "red",
        ),
    ]
    for project in projects:
        cursor = db.execute(
            """
            INSERT INTO projects(name, category, description, permanent_instructions, accent, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (*project, now, now),
        )
        db.execute(
            "INSERT INTO activity(project_id, action, details, created_at) VALUES (?, ?, ?, ?)",
            (cursor.lastrowid, "Proyecto creado", "Proyecto inicial incluido en Dimitry Hub.", now),
        )


def seed_knowledge(db: sqlite3.Connection) -> None:
    now = utc_now()
    entries = [
        (
            "palworld",
            "seguridad",
            "base",
            "backup-save",
            "Regla principal para editar saves",
            "Trabaja siempre con una copia del archivo original. Verifica el respaldo antes de reemplazar o eliminar un save.",
        ),
        (
            "palworld",
            "proyecto",
            "base",
            "global-box-rule",
            "Regla de la caja global",
            "El proyecto principal contempla dos Pals perfectos por especie, uno macho y otro hembra, idénticos salvo el sexo, evitando códigos o variantes que causen errores.",
        ),
        (
            "libtool",
            "seguridad",
            "base",
            "test-copy",
            "Prueba cambios en una copia",
            "Antes de aplicar parches, scripts o cambios binarios, conserva una copia limpia y registra cada modificación para poder revertirla.",
        ),
        (
            "gamemod", "unity", "base", "unity-mono",
            "Cómo reconocer Unity Mono",
            "Un proyecto Unity Mono suele incluir una carpeta Managed y archivos como Assembly-CSharp.dll. Las clases y métodos administrados pueden inspeccionarse con herramientas compatibles con .NET; el análisis debe hacerse sobre una copia.",
        ),
        (
            "gamemod", "unity", "base", "unity-il2cpp",
            "Cómo reconocer Unity IL2CPP",
            "Unity IL2CPP suele incluir global-metadata.dat junto con GameAssembly.dll en Windows o libil2cpp.so en Android/Linux. El dump debe relacionar metadatos y binario de la misma versión del juego.",
        ),
        (
            "gamemod", "libtool", "base", "libtool-workflow",
            "Flujo seguro de LibTool",
            "Guarda una copia limpia, registra la versión exacta, aplica un solo cambio por vez, prueba, documenta el resultado y conserva una ruta de restauración. Evita parches masivos sin identificar el método y el tipo de retorno.",
        ),
        (
            "gamemod", "libtool", "base", "boolean-patch",
            "Parches booleanos",
            "Antes de forzar true o false confirma que el método realmente devuelve un booleano y que no tiene efectos secundarios necesarios. Un nombre sugerente no basta; revisa llamadas, parámetros y referencias.",
        ),
        (
            "gamemod", "lua", "base", "lua-context",
            "Crear Lua con contexto real",
            "Un script Lua útil debe basarse en funciones, direcciones o APIs confirmadas por el material del juego o la herramienta. Si el dump no muestra una función, debe marcarse como hipótesis y no presentarse como código listo.",
        ),
        (
            "gamemod", "analisis", "base", "search-symbols",
            "Búsqueda de símbolos",
            "Para localizar inventario, monedas, habilidades o desbloqueos busca nombres de clases, campos, getters, setters, serialización y llamadas relacionadas. Después verifica dónde se guarda el valor y qué lo sobrescribe al reiniciar.",
        ),
        (
            "gamemod", "persistencia", "base", "save-vs-runtime",
            "Cambios temporales y permanentes",
            "Un valor modificado solo en memoria puede volver a su estado original al reiniciar. Para persistencia hay que identificar el sistema de guardado, la serialización y cualquier validación del servidor o de la nube.",
        ),
        (
            "gamemod", "seguridad", "base", "boundaries",
            "Límites del laboratorio",
            "El laboratorio está orientado a análisis local, aprendizaje, mods y copias propias. No debe emplearse para evadir pagos, DRM, sistemas anticheat o afectar cuentas, servicios o partidas ajenas.",
        ),
        (
            "palworld", "fuentes", "base", "source-priority",
            "Prioridad de fuentes de Palworld",
            "Para códigos internos y compatibilidad de saves prioriza el código y las versiones del editor. Para cambios del juego prioriza noticias oficiales. Para estrategias y ubicaciones, importa guías recientes y distingue opinión de datos confirmados.",
        ),
        (
            "palworld", "actualizacion", "base", "one-click-sync",
            "Actualización de la biblioteca",
            "Sincronizar datos consulta las noticias oficiales de Steam y comprueba la versión técnica del editor, pero nunca modifica saves ni instala programas. Actualizar componente descarga manualmente la publicación más reciente de Palworld Save Pal. Tras un parche, deja primero que el juego migre el guardado y no edites especies, pasivas o campos nuevos hasta que el editor actualizado confirme compatibilidad. Los paquetes ZIP permiten añadir dumps, tablas o guías propias a la biblioteca.",
        ),
        (
            "palworld", "edicion-save", "base-guide", "palworld-perfect-pal-profile",
            "Regla del proyecto: perfil de Pal perfecto",
            "Para crear la versión perfecta de un Pal, analiza primero su especie y su función real: combate, montura aérea o terrestre, base, rancho, transporte u otra especialidad. Por cada especie crea dos ejemplares equivalentes, uno macho y otro hembra, tanto en la partida normal como en GlobalPalStorage.sav, salvo una exclusión expresa o una incompatibilidad verificada. Ambos conservan exactamente el mismo nombre, build y ataques, el nivel 1 y las aptitudes o niveles de trabajo propios de la especie en su valor predeterminado. Maximiza IV, almas, rango y las demás estadísticas o mejoras compatibles que el editor actualizado exponga, sin inventar campos ni códigos. Asigna exactamente cuatro pasivas compatibles y útiles para esa función, y usa las mismas cuatro en ambos ejemplares; una pasiva de velocidad de vuelo solo corresponde a una montura aérea que pueda aprovecharla. Mantén las decisiones históricas del proyecto: Gumoss con flor roja, Panthalus Alfa BOSS_KingWhale y Astralym excluido de la Caja Pal Global mientras no se indique lo contrario. Si la especie nueva, la pasiva o su identificador aún no aparece en Palworld Save Pal, detén la edición y espera una versión compatible. Trabaja siempre sobre la copia de la sesión de Dimitry Hub y valida los dos ejemplares dentro del juego antes de reemplazar el respaldo.",
        ),
        (
            "palworld", "guia-inicial", "base-guide", "palworld-1.0-early-pals",
            "Guía Palworld 1.0: mejores Pals iniciales",
            "Guía comunitaria orientativa, revisada para Palworld 1.0 en julio de 2026. Cattiva es una opción inicial versátil: aumenta la capacidad de carga y trabaja en labores manuales, transporte, recolección y minería. Foxparks aporta combate temprano y encendido. Pengullet cubre riego, enfriamiento, transporte y labores manuales. Lamball ayuda en el rancho con materiales para armadura; Chikipi aporta alimento; Lifmunk cubre siembra, recolección, tala, labores manuales y medicina. La elección depende de si priorizas combate, base o movilidad. Fuente comunitaria: https://all.gg/news/best-early-game-pals-in-palworld-1-0/",
        ),
        (
            "palworld", "pasivas-habilidades", "base-guide", "palworld-1.0-passives",
            "Guía Palworld 1.0: mejores habilidades pasivas de Pals",
            "Resumen comunitario orientativo para la versión 1.0 de julio de 2026. Para combate destacan Immortality, Legend, Diamond Body, Demon's Hand, Idiosyncratic y Eternal Engine. Para trabajo de base conviene revisar Ranch Master, Babysitter y Remarkable Craftsmanship; Artisan sigue siendo útil. Para movilidad aparecen Swift y Eternal Engine. No existe una única lista óptima: separa configuraciones de combate, montura, rancho y producción, y revisa los efectos dentro de tu versión del juego. Fuente comunitaria: https://allthings.how/palworld-1-0-best-passives-tier-list/",
        ),
        (
            "palworld", "mapa-bases", "base-guide", "palworld-1.0-mining-bases",
            "Guía Palworld 1.0: coordenadas de bases y minería",
            "Estas son algunas de las mejores ubicaciones y coordenadas para una base de minería (también buscado como cordenadas o mineria). Confirma cada punto en el mapa de tu versión antes de construir. Mineral y carbón: 189, -38 en Verdant Brook. Azufre: -594, -525 cerca de la torre del volcán. Cuarzo puro: -212, 249 en Astral Mountain. Cromita y cuarzo Hexolite: -1172, -1225 en Feybreak. Soralite de la versión 1.0: 583, 144 en Sun Reach. Para una base general plana en contenido 1.0 se cita Crystal Pool en -540, -1361, con la precaución de un jefe cercano. Fuentes comunitarias: https://www.playerauctions.com/palworld-guide/tips-tricks/best-mining-base-spots/ y https://allthings.how/palworld-1-0-best-base-locations-with-coordinates/",
        ),
        (
            "palworld", "objetos", "base-guide", "palworld-1.0-high-quality-pal-oil",
            "Cómo conseguir aceite de Pal de alta calidad en Palworld 1.0",
            "Métodos comunitarios revisados en julio de 2026: asigna un Dumud al rancho para una producción sostenida; Dumud Gild puede añadir monedas. Como alternativa rápida, Mammorest puede soltar entre 5 y 10 unidades al capturarlo o derrotarlo. También se vende por 300 monedas en comerciantes como Duneshelter y Fisherman's Point. Verifica precios y botín en tu versión antes de planificar una granja. Fuente comunitaria: https://dotesports.com/palworld/guides/how-to-get-high-quality-oil-in-palworld-1-0",
        ),
        (
            "palworld", "objetos", "base-guide", "palworld-1.0-electric-organ",
            "Cómo conseguir órgano eléctrico en Palworld 1.0",
            "El Órgano eléctrico (Electric Organ, código interno ElectricOrgan; también buscado como organo electrico) sí existe y es un material obtenido de Pals de tipo Rayo. Para conseguirlo pronto captura o derrota Pals eléctricos: Sparkit entrega 1–2, Jolthog y Univolt entregan 1, y otros Pals de Rayo también pueden soltarlo. También puede comprarse por 200 monedas al Caravan Leader o a comerciantes ambulantes de incidentes. En Palworld 1.0 la comunidad también documenta producción en rancho con Sparkit, pero conviene confirmarla en la versión instalada. Fuentes de datos actuales consultadas en agosto de 2026: https://paldb.cc/en/Electric_Organ y https://palworld.wiki.gg/wiki/Electric_Organ",
        ),
    ]
    for domain, category, source, source_key, title, content in entries:
        db.execute(
            """
            INSERT INTO knowledge_entries(domain, category, source, source_key, title, content, metadata_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, '{}', ?)
            ON CONFLICT(domain, source, source_key) DO UPDATE SET
                category=excluded.category,
                title=excluded.title,
                content=excluded.content,
                updated_at=excluded.updated_at
            """,
            (domain, category, source, source_key, title, content, now),
        )


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def log_activity(db: sqlite3.Connection, project_id: int | None, action: str, details: str = "") -> None:
    db.execute(
        "INSERT INTO activity(project_id, action, details, created_at) VALUES (?, ?, ?, ?)",
        (project_id, action, details, utc_now()),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_bytes(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, dir=target.parent) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_path = Path(tmp.name)
    temp_path.replace(target)


def safe_filename(name: str) -> str:
    cleaned = "".join(ch for ch in Path(name).name if ch.isalnum() or ch in "._- ()[]")
    return cleaned.strip() or "archivo"
