from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from urllib.parse import quote, urlparse

from .database import connect, utc_now
from .internet import http_get, safe_public_url
from .knowledge import upsert_entry


class ReadableHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._in_title = False
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript", "svg", "canvas"}:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in {"p", "div", "article", "section", "h1", "h2", "h3", "li", "br", "tr"} and not self._skip:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg", "canvas"} and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = html.unescape(data).strip()
        if not text:
            return
        if self._in_title:
            self.title = (self.title + " " + text).strip()
        self.parts.append(text + " ")

    def content(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text)
        return text.strip()


def _settings() -> dict[str, str]:
    with connect() as db:
        return {row["key"]: row["value"] for row in db.execute("SELECT key,value FROM settings")}


def source_quality(url: str) -> dict:
    host = (urlparse(url).hostname or "").lower()
    official_hosts = (
        ".gob.pe", "congreso.gob.pe", "pj.gob.pe", "tc.gob.pe", "elperuano.pe",
        "pocketpair.jp", "palworldgame.com", "steampowered.com", "github.com",
    )
    academic_hosts = ("doi.org", "crossref.org", "scielo.", "edu.", ".edu", "researchgate.net")
    if any(host == suffix.lstrip(".") or host.endswith(suffix) for suffix in official_hosts):
        return {"level": "oficial", "score": 5}
    if any(part in host for part in academic_hosts):
        return {"level": "académica", "score": 4}
    return {"level": "web", "score": 2}


def import_web_source(url: str, domain: str = "research", title: str = "") -> dict:
    clean_url = safe_public_url(url)
    result = http_get(clean_url, timeout=50, max_bytes=6 * 1024 * 1024)
    content_type = result.headers.get("content-type", "")
    if "html" in content_type or result.text.lstrip().startswith("<"):
        parser = ReadableHTML()
        parser.feed(result.text)
        text = parser.content()
        resolved_title = title.strip() or parser.title.strip() or clean_url
    else:
        text = result.text.strip()
        resolved_title = title.strip() or clean_url
    if len(text) < 80:
        raise ValueError("La página no contiene suficiente texto legible")
    quality = source_quality(clean_url)
    source_key = clean_url
    upsert_entry(
        domain=domain, category=f"web-{quality['level']}", source="web-import", source_key=source_key,
        title=resolved_title[:240], content=text[:120000],
        metadata={"url": clean_url, "quality": quality, "content_type": content_type, "fetched_at": utc_now()},
    )
    with connect() as db:
        existing = db.execute("SELECT id FROM research_items WHERE url=? ORDER BY id DESC LIMIT 1", (clean_url,)).fetchone()
        if existing:
            item_id = existing["id"]
            db.execute(
                "UPDATE research_items SET title=?,source_type=?,abstract=?,metadata_json=?,updated_at=? WHERE id=?",
                (resolved_title, quality["level"], text[:10000], json.dumps({"quality": quality}, ensure_ascii=False), utc_now(), item_id),
            )
        else:
            cursor = db.execute(
                "INSERT INTO research_items(title,url,source_type,abstract,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (resolved_title, clean_url, quality["level"], text[:10000], json.dumps({"quality": quality}, ensure_ascii=False), utc_now(), utc_now()),
            )
            item_id = cursor.lastrowid
    return {"id": item_id, "title": resolved_title, "url": clean_url, "characters": len(text), "quality": quality}


def _author_name(author: dict) -> str:
    family = str(author.get("family") or "").strip()
    given = str(author.get("given") or "").strip()
    initials = " ".join(f"{piece[0]}." for piece in re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", given) if piece)
    return f"{family}, {initials}".strip(", ") if family else (given or "Autor desconocido")


def _author_text(authors: list[dict]) -> str:
    names = [_author_name(item) for item in authors if isinstance(item, dict)]
    if not names:
        return "Autor desconocido"
    if len(names) == 1:
        return names[0]
    if len(names) <= 20:
        return ", ".join(names[:-1]) + ", & " + names[-1]
    return ", ".join(names[:19]) + ", … " + names[-1]


def crossref_to_item(message: dict) -> dict:
    title_list = message.get("title") or ["Sin título"]
    title = str(title_list[0] if isinstance(title_list, list) else title_list).strip()
    authors = message.get("author") or []
    author_text = _author_text(authors if isinstance(authors, list) else [])
    date_parts = (message.get("published-print") or message.get("published-online") or message.get("issued") or {}).get("date-parts", [[""]])
    year = str(date_parts[0][0]) if date_parts and date_parts[0] else "s. f."
    container = message.get("container-title") or []
    journal = str(container[0] if isinstance(container, list) and container else "").strip()
    volume = str(message.get("volume") or "").strip()
    issue = str(message.get("issue") or "").strip()
    pages = str(message.get("page") or "").strip()
    doi = str(message.get("DOI") or "").strip()
    url = str(message.get("URL") or (f"https://doi.org/{doi}" if doi else "")).strip()
    reference = f"{author_text} ({year}). {title}."
    if journal:
        reference += f" {journal}"
        if volume:
            reference += f", {volume}"
        if issue:
            reference += f"({issue})"
        if pages:
            reference += f", {pages}"
        reference += "."
    if doi:
        reference += f" https://doi.org/{doi}"
    elif url:
        reference += f" {url}"
    return {
        "title": title,
        "authors": author_text,
        "year": year,
        "doi": doi,
        "url": url,
        "apa_reference": reference,
        "abstract": re.sub(r"<[^>]+>", " ", str(message.get("abstract") or "")).strip(),
        "publisher": message.get("publisher", ""),
        "type": message.get("type", ""),
    }


def crossref_search(query: str, rows: int = 8) -> list[dict]:
    settings = _settings()
    params = f"query.bibliographic={quote(query)}&rows={max(1, min(rows, 20))}&select=DOI,title,author,published-print,published-online,issued,container-title,volume,issue,page,URL,publisher,type,abstract"
    email = settings.get("crossref_email", "").strip()
    if email:
        params += f"&mailto={quote(email)}"
    result = http_get(f"https://api.crossref.org/works?{params}", timeout=50, max_bytes=8 * 1024 * 1024)
    payload = result.json()
    items = payload.get("message", {}).get("items", []) if isinstance(payload, dict) else []
    return [crossref_to_item(item) for item in items if isinstance(item, dict)]


def crossref_doi(doi: str) -> dict:
    clean = doi.strip().removeprefix("https://doi.org/").removeprefix("http://doi.org/")
    settings = _settings()
    suffix = f"?mailto={quote(settings['crossref_email'])}" if settings.get("crossref_email", "").strip() else ""
    result = http_get(f"https://api.crossref.org/works/{quote(clean, safe='')}{suffix}", timeout=45, max_bytes=4 * 1024 * 1024)
    payload = result.json()
    message = payload.get("message", {}) if isinstance(payload, dict) else {}
    return crossref_to_item(message)


def save_research_item(item: dict) -> dict:
    now = utc_now()
    doi = str(item.get("doi") or "").strip()
    url = str(item.get("url") or "").strip()
    with connect() as db:
        existing = None
        if doi:
            existing = db.execute("SELECT id FROM research_items WHERE doi=? ORDER BY id DESC LIMIT 1", (doi,)).fetchone()
        if not existing and url:
            existing = db.execute("SELECT id FROM research_items WHERE url=? ORDER BY id DESC LIMIT 1", (url,)).fetchone()
        values = (
            str(item.get("title") or "Sin título"), url, "académica", str(item.get("authors") or ""),
            str(item.get("year") or ""), doi, str(item.get("apa_reference") or ""),
            str(item.get("abstract") or ""), json.dumps(item, ensure_ascii=False), now,
        )
        if existing:
            item_id = existing["id"]
            db.execute(
                "UPDATE research_items SET title=?,url=?,source_type=?,authors=?,year=?,doi=?,apa_reference=?,abstract=?,metadata_json=?,updated_at=? WHERE id=?",
                (*values, item_id),
            )
        else:
            cursor = db.execute(
                "INSERT INTO research_items(title,url,source_type,authors,year,doi,apa_reference,abstract,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (*values, now),
            )
            item_id = cursor.lastrowid
    upsert_entry(
        domain="research", category="crossref", source="crossref", source_key=doi or url or f"item:{item_id}",
        title=values[0], content="\n".join(filter(None, [values[6], values[7]])), metadata=item,
    )
    return {"id": item_id, **item}


def bibliography_audit(content: str, bibliography: str) -> dict:
    refs = [line.strip() for line in bibliography.splitlines() if len(line.strip()) > 8]
    duplicate_refs = sorted({ref for ref in refs if refs.count(ref) > 1})
    # Conservative citation detector: (Apellido, 2024), Apellido (2024), with suffixes allowed.
    parenthetical = re.findall(r"\(([A-ZÁÉÍÓÚÜÑ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ'’\-]+)(?:\s+et\s+al\.)?,\s*((?:19|20)\d{2}[a-z]?)\)", content)
    narrative = re.findall(r"\b([A-ZÁÉÍÓÚÜÑ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ'’\-]+)(?:\s+et\s+al\.)?\s*\(((?:19|20)\d{2}[a-z]?)\)", content)
    citations = sorted({(a, y) for a, y in parenthetical + narrative})
    missing = []
    for author, year in citations:
        if not any(author.lower() in ref.lower() and year.lower() in ref.lower() for ref in refs):
            missing.append(f"{author}, {year}")
    unused = []
    for ref in refs:
        year_match = re.search(r"\(((?:19|20)\d{2}[a-z]?)\)", ref)
        author_match = re.match(r"([A-ZÁÉÍÓÚÜÑ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ'’\-]+)", ref)
        if year_match and author_match and (author_match.group(1), year_match.group(1)) not in citations:
            unused.append(ref)
    dois = sorted(set(re.findall(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", bibliography, flags=re.I)))
    return {
        "references": len(refs), "citations": len(citations), "missing_references": missing,
        "possibly_unused": unused[:30], "duplicates": duplicate_refs[:20], "dois": dois,
        "score": max(0, 100 - 15 * len(missing) - 5 * len(duplicate_refs)),
    }



SPANISH_STOPWORDS = {
    "para", "como", "desde", "hasta", "entre", "sobre", "este", "esta", "estos", "estas",
    "todo", "toda", "todos", "todas", "porque", "cuando", "donde", "cual", "cuales",
    "una", "uno", "unos", "unas", "del", "las", "los", "con", "por", "que", "sus",
    "se", "al", "en", "y", "o", "u", "de", "la", "el", "un", "es", "son", "ser",
    "derecho", "monografia", "monografía", "trabajo", "estudio", "analisis", "análisis",
}

def suggest_academic_sources(title: str, content: str, rows: int = 8) -> dict:
    words = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{4,}", f"{title} {content[:5000]}".lower())
    counts: dict[str, int] = {}
    for word in words:
        if word in SPANISH_STOPWORDS:
            continue
        counts[word] = counts.get(word, 0) + 1
    keywords = [word for word, _ in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:8]]
    query = " ".join(filter(None, [title.strip(), " ".join(keywords[:5])])).strip()
    if not query:
        raise ValueError("No hay suficiente contenido para construir una búsqueda")
    return {"query": query, "keywords": keywords, "results": crossref_search(query, rows)}

def list_research_items(limit: int = 100) -> list[dict]:
    with connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM research_items ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 300)),))]
