from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

USER_AGENT = "DimitryHub/0.5 (+local desktop knowledge manager)"
DEFAULT_MAX_BYTES = 8 * 1024 * 1024


@dataclass
class HttpResult:
    status: int
    url: str
    body: bytes
    headers: dict[str, str]
    not_modified: bool = False

    @property
    def text(self) -> str:
        charset = "utf-8"
        content_type = self.headers.get("content-type", "")
        if "charset=" in content_type:
            charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip()
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

    def json(self) -> object:
        return json.loads(self.text)


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    base = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.6",
        "Accept-Encoding": "identity",
    }
    if extra:
        base.update({k: v for k, v in extra.items() if v})
    return base


def http_get(
    url: str,
    *,
    timeout: int = 45,
    max_bytes: int = DEFAULT_MAX_BYTES,
    headers: dict[str, str] | None = None,
    etag: str = "",
    last_modified: str = "",
) -> HttpResult:
    request_headers = _headers(headers)
    if etag:
        request_headers["If-None-Match"] = etag
    if last_modified:
        request_headers["If-Modified-Since"] = last_modified
    request = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(min(1024 * 1024, max_bytes - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"La descarga supera el límite seguro de {max_bytes // (1024 * 1024)} MB")
                chunks.append(chunk)
            return HttpResult(
                status=getattr(response, "status", 200),
                url=response.geturl(),
                body=b"".join(chunks),
                headers={k.lower(): v for k, v in response.headers.items()},
            )
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return HttpResult(304, url, b"", {k.lower(): v for k, v in exc.headers.items()}, True)
        body = exc.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:500] or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"No se pudo conectar: {exc.reason}") from exc


def download_to_file(
    url: str,
    target: Path,
    *,
    timeout: int = 120,
    max_bytes: int = 350 * 1024 * 1024,
    headers: dict[str, str] | None = None,
) -> tuple[int, str, dict[str, str]]:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers=_headers(headers))
    digest = hashlib.sha256()
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, target.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    output.close()
                    target.unlink(missing_ok=True)
                    raise ValueError(f"La descarga supera el límite seguro de {max_bytes // (1024 * 1024)} MB")
                digest.update(chunk)
                output.write(chunk)
            return total, digest.hexdigest(), {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.URLError as exc:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"No se pudo descargar: {exc.reason}") from exc


def safe_public_url(url: str) -> str:
    """Validate user-supplied URLs and block local/private targets."""
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Usa una dirección http o https válida")
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("No se permiten direcciones locales")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))}
    except socket.gaierror as exc:
        raise ValueError("No se pudo resolver el dominio") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("La dirección apunta a una red privada o reservada")
    return urllib.parse.urlunparse(parsed)
