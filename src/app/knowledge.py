from __future__ import annotations

import json
import re
import shutil
import zipfile
from collections import Counter
from pathlib import Path
from typing import Iterable

from .database import KNOWLEDGE_DIR, connect, safe_filename, utc_now

TEXT_EXTENSIONS = {
    ".txt", ".md", ".json", ".lua", ".py", ".cs", ".java", ".smali",
    ".xml", ".ini", ".cfg", ".yaml", ".yml", ".toml", ".rs", ".ts",
    ".tsx", ".js", ".jsx", ".cpp", ".c", ".h", ".hpp", ".sql",
}
MAX_TEXT_FILE_BYTES = 4 * 1024 * 1024
MAX_INDEX_CONTENT = 120_000
MAX_ZIP_ENTRIES = 50_000
MAX_ZIP_UNCOMPRESSED = 3 * 1024 * 1024 * 1024
MAX_ZIP_MEMBER = 512 * 1024 * 1024
TOKEN_RE = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9_]{3,}")


def safe_extract_zip(zip_path: Path, target_dir: Path) -> list[Path]:
    """Extract a zip while blocking absolute paths and path traversal."""
    target_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    root = target_dir.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        if len(members) > MAX_ZIP_ENTRIES:
            raise ValueError(f"El ZIP contiene demasiados elementos ({len(members)}).")
        total_uncompressed = sum(max(0, member.file_size) for member in members)
        if total_uncompressed > MAX_ZIP_UNCOMPRESSED:
            raise ValueError("El ZIP supera el límite seguro de tamaño descomprimido.")
        for member in members:
            if member.file_size > MAX_ZIP_MEMBER:
                raise ValueError(f"Un archivo del ZIP es demasiado grande: {member.filename}")
            # UNIX symlinks can point outside the extraction folder.
            if ((member.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError(f"El ZIP contiene un enlace simbólico no permitido: {member.filename}")
            member_name = member.filename.replace("\\", "/")
            if not member_name or member_name.endswith("/"):
                continue
            destination = (target_dir / member_name).resolve()
            if root != destination and root not in destination.parents:
                raise ValueError(f"Ruta insegura dentro del ZIP: {member.filename}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)
            extracted.append(destination)
    return extracted


def read_text_file(path: Path) -> str:
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        return ""
    try:
        if path.stat().st_size > MAX_TEXT_FILE_BYTES:
            return ""
        data = path.read_bytes()
    except OSError:
        return ""
    for encoding in ("utf-8", "utf-8-sig", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)[:MAX_INDEX_CONTENT]
        except UnicodeDecodeError:
            continue
    return ""


def _candidate_title(value: dict, fallback: str) -> str:
    preferred = (
        "display_name", "localized_name", "name", "Name", "title", "Title",
        "character_name", "character_id", "CharacterID", "id", "ID", "key",
        "internal_name", "InternalName", "code", "Code",
    )
    for key in preferred:
        current = value.get(key)
        if isinstance(current, (str, int, float)) and str(current).strip():
            return str(current).strip()[:240]
    return fallback[:240]


def json_records(data: object, prefix: str = "entry") -> Iterable[tuple[str, str, str]]:
    """Yield source_key, title, compact-json content from arbitrary JSON."""
    if isinstance(data, list):
        for index, item in enumerate(data):
            key = f"{prefix}:{index}"
            if isinstance(item, dict):
                title = _candidate_title(item, key)
                content = json.dumps(item, ensure_ascii=False, indent=2)
            else:
                title = str(item)[:240]
                content = json.dumps(item, ensure_ascii=False)
            yield key, title, content
        return

    if isinstance(data, dict):
        # Many game data files are mappings keyed by an internal code.
        for key, item in data.items():
            source_key = f"{prefix}:{key}"
            if isinstance(item, dict):
                enriched = dict(item)
                enriched.setdefault("source_key", key)
                title = _candidate_title(enriched, str(key))
                content = json.dumps(enriched, ensure_ascii=False, indent=2)
            else:
                title = str(key)
                content = json.dumps({"key": key, "value": item}, ensure_ascii=False, indent=2)
            yield source_key, title, content
        return

    yield prefix, prefix, json.dumps(data, ensure_ascii=False)


def upsert_entry(
    *, domain: str, category: str, source: str, source_key: str,
    title: str, content: str, metadata: dict | None = None,
) -> None:
    with connect() as db:
        db.execute(
            """
            INSERT INTO knowledge_entries(domain, category, source, source_key, title, content, metadata_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(domain, source, source_key) DO UPDATE SET
                category=excluded.category,
                title=excluded.title,
                content=excluded.content,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                domain, category, source, source_key, title[:240],
                content[:MAX_INDEX_CONTENT],
                json.dumps(metadata or {}, ensure_ascii=False), utc_now(),
            ),
        )


def index_json_file(
    path: Path, domain: str, source: str, category: str | None = None,
    *, source_prefix: str | None = None, relative_path: str | None = None,
) -> int:
    text = read_text_file(path)
    if not text:
        return 0
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return 0
    count = 0
    category_name = category or path.stem.lower()
    stable_prefix = source_prefix or path.stem
    for source_key, title, content in json_records(data, stable_prefix):
        upsert_entry(
            domain=domain,
            category=category_name,
            source=source,
            source_key=source_key,
            title=title,
            content=content,
            metadata={"path": str(path), "relative_path": relative_path or path.name},
        )
        count += 1
    return count


def index_text_file(
    path: Path, domain: str, source: str, category: str | None = None,
    *, source_key: str | None = None, relative_path: str | None = None,
) -> int:
    text = read_text_file(path)
    if not text:
        return 0
    relative_key = source_key or relative_path or path.name
    upsert_entry(
        domain=domain,
        category=category or path.suffix.lower().lstrip(".") or "texto",
        source=source,
        source_key=relative_key,
        title=path.name,
        content=text,
        metadata={"path": str(path), "relative_path": relative_path or relative_key, "extension": path.suffix.lower()},
    )
    return 1


def index_directory(root: Path, domain: str, source: str) -> tuple[int, int]:
    """Index text files using keys that remain stable across downloaded versions.

    Absolute extraction folders contain timestamps and release names. They must not
    become part of ``source_key`` or every update would look like a completely new
    database. Relative paths also prevent same-named JSON files from overwriting one
    another.
    """
    files = 0
    entries = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        files += 1
        relative = path.relative_to(root).as_posix()
        if path.suffix.lower() == ".json":
            indexed = index_json_file(
                path, domain, source,
                source_prefix=relative,
                relative_path=relative,
            )
            entries += indexed or index_text_file(
                path, domain, source, "json",
                source_key=relative,
                relative_path=relative,
            )
        else:
            entries += index_text_file(
                path, domain, source,
                source_key=relative,
                relative_path=relative,
            )
    return files, entries


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]


def search_knowledge(domain: str, query: str, limit: int = 12) -> list[dict]:
    tokens = tokenize(query)
    if not tokens:
        return []
    clauses = " OR ".join(["lower(title) LIKE ? OR lower(content) LIKE ?"] * min(len(tokens), 8))
    params: list[str] = []
    for token in tokens[:8]:
        like = f"%{token}%"
        params.extend([like, like])
    with connect() as db:
        rows = db.execute(
            f"SELECT * FROM knowledge_entries WHERE domain=? AND ({clauses}) LIMIT 200",
            [domain, *params],
        ).fetchall()

    query_counts = Counter(tokens)
    scored: list[tuple[float, dict]] = []
    for row in rows:
        item = dict(row)
        title_tokens = Counter(tokenize(item["title"]))
        content_tokens = Counter(tokenize(item["content"][:25_000]))
        score = 0.0
        for token, count in query_counts.items():
            score += min(count, title_tokens[token]) * 8
            score += min(count, content_tokens[token]) * 1.5
            if token in item["title"].lower():
                score += 4
        scored.append((score, item))
    scored.sort(key=lambda pair: (pair[0], pair[1]["updated_at"]), reverse=True)
    results = []
    for score, item in scored[:limit]:
        item["score"] = round(score, 2)
        item["snippet"] = compact_snippet(item["content"], tokens)
        results.append(item)
    return results


def compact_snippet(content: str, tokens: list[str], length: int = 700) -> str:
    lowered = content.lower()
    positions = [lowered.find(token) for token in tokens if lowered.find(token) >= 0]
    start = max(0, (min(positions) if positions else 0) - 120)
    snippet = content[start:start + length].strip()
    return snippet + ("…" if start + length < len(content) else "")


def remove_source(domain: str, source: str) -> None:
    with connect() as db:
        db.execute("DELETE FROM knowledge_entries WHERE domain=? AND source=?", (domain, source))


def source_summary(domain: str) -> dict:
    with connect() as db:
        total = db.execute("SELECT COUNT(*) n FROM knowledge_entries WHERE domain=?", (domain,)).fetchone()["n"]
        categories = [dict(r) for r in db.execute(
            "SELECT category, COUNT(*) count FROM knowledge_entries WHERE domain=? GROUP BY category ORDER BY count DESC LIMIT 30",
            (domain,),
        )]
        bundles = [dict(r) for r in db.execute(
            "SELECT * FROM knowledge_bundles WHERE domain=? ORDER BY updated_at DESC",
            (domain,),
        )]
    return {"entries": total, "categories": categories, "bundles": bundles}


def clean_bundle_name(name: str) -> str:
    return safe_filename(name).replace(" ", "_")[:100]


def import_bundle(zip_path: Path, *, domain: str, display_name: str, source_url: str = "", source_id: str | None = None) -> dict:
    """Store, safely extract, index and register a knowledge ZIP bundle."""
    bundle_name = clean_bundle_name(display_name or zip_path.stem)
    stamp = utc_now().replace(":", "-")
    bundle_root = KNOWLEDGE_DIR / domain / f"{stamp}_{bundle_name}"
    # database.py already ensures knowledge/domain dirs for known domains; generic dirs are okay.
    bundle_root.mkdir(parents=True, exist_ok=True)
    stored_zip = bundle_root / safe_filename(zip_path.name)
    if zip_path.resolve() != stored_zip.resolve():
        shutil.copy2(zip_path, stored_zip)
    extract_dir = bundle_root / "extracted"
    safe_extract_zip(stored_zip, extract_dir)
    source = source_id or f"bundle:{domain}:{stamp}:{bundle_name}"
    if source_id:
        remove_source(domain, source_id)
    files, entries = index_directory(extract_dir, domain, source)
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO knowledge_bundles(domain,name,original_name,stored_path,file_count,entry_count,source_url,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (domain, display_name or bundle_name, zip_path.name, str(bundle_root), files, entries, source_url, utc_now(), utc_now()),
        )
        bundle_id = cursor.lastrowid
    return {
        "id": bundle_id,
        "domain": domain,
        "name": display_name or bundle_name,
        "stored_path": str(bundle_root),
        "source": source,
        "file_count": files,
        "entry_count": entries,
        "extract_dir": str(extract_dir),
    }


def import_directory(root: Path, *, domain: str, display_name: str, source_url: str = "") -> dict:
    stamp = utc_now().replace(":", "-")
    bundle_name = clean_bundle_name(display_name or root.name)
    source = f"directory:{domain}:{stamp}:{bundle_name}"
    files, entries = index_directory(root, domain, source)
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO knowledge_bundles(domain,name,original_name,stored_path,file_count,entry_count,source_url,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (domain, display_name or root.name, root.name, str(root), files, entries, source_url, utc_now(), utc_now()),
        )
        bundle_id = cursor.lastrowid
    return {
        "id": bundle_id,
        "domain": domain,
        "name": display_name or root.name,
        "stored_path": str(root),
        "source": source,
        "file_count": files,
        "entry_count": entries,
        "extract_dir": str(root),
    }
