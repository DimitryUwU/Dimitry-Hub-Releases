from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .database import connect
from .secrets import get_secret, has_secret, masked_secret, storage_mode


class AIError(RuntimeError):
    pass


@dataclass
class AIResult:
    text: str
    provider: str
    model: str
    sources: list[dict[str, str]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "response": self.text,
            "provider": self.provider,
            "model": self.model,
            "sources": self.sources,
            "usage": self.usage,
        }


def settings() -> dict[str, str]:
    with connect() as db:
        return {row["key"]: row["value"] for row in db.execute("SELECT key,value FROM settings")}


def _request_json(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 240,
) -> dict[str, Any]:
    final_headers = {"Accept": "application/json", "User-Agent": "DimitryHub/0.5"}
    if headers:
        final_headers.update(headers)
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        final_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=final_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:4000]
        try:
            parsed = json.loads(body)
            message = parsed.get("error", {}).get("message") or parsed.get("detail") or body
        except Exception:
            message = body or exc.reason
        raise AIError(f"El proveedor respondió {exc.code}: {message}") from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        raise AIError(f"No se pudo conectar con el proveedor de IA: {reason}") from exc
    except json.JSONDecodeError as exc:
        raise AIError("El proveedor devolvió una respuesta que no se pudo interpretar") from exc


def ollama_models(base_url: str | None = None) -> list[dict[str, Any]]:
    cfg = settings()
    url = (base_url or cfg.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/") + "/api/tags"
    try:
        payload = _request_json(url, timeout=4)
        return payload.get("models", []) if isinstance(payload, dict) else []
    except AIError:
        return []


def openai_models() -> list[dict[str, str]]:
    key = get_secret("openai_api_key")
    if not key:
        return []
    payload = _request_json(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=20,
    )
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    result = []
    for item in rows:
        model_id = str(item.get("id", "")).strip()
        if model_id:
            result.append({"id": model_id, "owned_by": str(item.get("owned_by", ""))})
    return sorted(result, key=lambda item: item["id"])


def compatible_models() -> list[dict[str, str]]:
    cfg = settings()
    base = (cfg.get("compatible_base_url") or "").strip().rstrip("/")
    if not base:
        return []
    key = get_secret("compatible_api_key")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    payload = _request_json(f"{base}/models", headers=headers, timeout=20)
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    return sorted(
        [{"id": str(item.get("id", "")), "owned_by": str(item.get("owned_by", ""))} for item in rows if item.get("id")],
        key=lambda item: item["id"],
    )


def _extract_openai_text(payload: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    parts: list[str] = []
    sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    if isinstance(payload.get("output_text"), str):
        parts.append(payload["output_text"])
    for item in payload.get("output", []) or []:
        if item.get("type") == "message":
            for content in item.get("content", []) or []:
                if content.get("type") == "output_text" and content.get("text"):
                    parts.append(str(content["text"]))
                for annotation in content.get("annotations", []) or []:
                    url = annotation.get("url") or annotation.get("url_citation", {}).get("url")
                    title = annotation.get("title") or annotation.get("url_citation", {}).get("title") or url
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        sources.append({"title": str(title or url), "url": str(url)})
        if item.get("type") == "web_search_call":
            action = item.get("action", {}) or {}
            for source in action.get("sources", []) or []:
                url = source.get("url")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    sources.append({"title": str(source.get("title") or url), "url": str(url)})
    text = "\n".join(part.strip() for part in parts if part and part.strip()).strip()
    return text, sources


def _purpose_model(cfg: dict[str, str], provider: str, purpose: str) -> str:
    purpose = purpose if purpose in {"general", "research", "code", "legal", "study"} else "general"
    return (
        cfg.get(f"{provider}_{purpose}_model", "").strip()
        or cfg.get(f"{provider}_model", "").strip()
    )


def _ollama_generate(
    messages: list[dict[str, str]],
    *,
    system: str,
    model: str,
    timeout: int,
    cfg: dict[str, str],
) -> AIResult:
    if not model:
        raise AIError("Selecciona un modelo de Ollama en Ajustes")
    chat_messages = []
    local_identity = (
        "Eres la inteligencia artificial local integrada de Dimitry Hub mediante Ollama. "
        "Esta respuesta solo puede generarse porque Ollama está conectado y operativo. "
        "Responde siempre en español claro, salvo que la persona solicite expresamente otro idioma, "
        "y nunca inventes que eres un servicio separado de Dimitry Hub."
    )
    chat_messages.append(
        {"role": "system", "content": f"{system.strip()}\n\n{local_identity}" if system else local_identity}
    )
    chat_messages.extend(dict(message) for message in messages)
    payload: dict[str, Any] = {"model": model, "messages": chat_messages, "stream": False}
    think = cfg.get("ollama_think", "off")
    if think in {"low", "medium", "high"}:
        payload["think"] = think
    else:
        # Qwen 3 entiende mejor la orden al inicio del último mensaje. El valor
        # booleano cubre además los modelos que implementan la opción nativa.
        payload["think"] = False
        for message in reversed(chat_messages):
            if message.get("role") == "user":
                content = str(message.get("content") or "")
                if not content.lstrip().startswith("/no_think"):
                    message["content"] = f"/no_think\n{content}"
                break
    response = _request_json(
        (cfg.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/") + "/api/chat",
        payload=payload,
        timeout=timeout,
    )
    message = response.get("message", {}) if isinstance(response, dict) else {}
    text = str(message.get("content") or response.get("response") or "").strip()
    text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    # Algunas plantillas entregan el razonamiento sin la etiqueta inicial y
    # conservan únicamente </think>. En ese caso solo exponemos la respuesta.
    if re.search(r"</think>", text, flags=re.IGNORECASE):
        text = re.split(r"</think>", text, flags=re.IGNORECASE)[-1].strip()
    if not text:
        raise AIError("Ollama no devolvió contenido")
    usage = {
        "prompt_tokens": response.get("prompt_eval_count"),
        "completion_tokens": response.get("eval_count"),
        "total_duration": response.get("total_duration"),
    }
    return AIResult(text=text, provider="ollama", model=model, usage=usage, raw=response)


def _openai_generate(
    messages: list[dict[str, str]],
    *,
    system: str,
    model: str,
    timeout: int,
    allow_web: bool,
    allowed_domains: list[str] | None,
    cfg: dict[str, str],
) -> AIResult:
    key = get_secret("openai_api_key")
    if not key:
        raise AIError("Falta la clave de OpenAI API")
    if not model:
        raise AIError("Selecciona un modelo de OpenAI API en Ajustes")
    payload: dict[str, Any] = {
        "model": model,
        "input": messages,
        "store": False,
    }
    if system:
        payload["instructions"] = system
    if allow_web:
        web_tool: dict[str, Any] = {"type": "web_search", "search_context_size": "high"}
        if allowed_domains:
            web_tool["filters"] = {"allowed_domains": allowed_domains}
        payload["tools"] = [web_tool]
    effort = cfg.get("ai_reasoning_effort", "medium")
    if effort in {"none", "low", "medium", "high", "xhigh", "max"} and (model.startswith("gpt-5") or model.startswith("o")):
        payload["reasoning"] = {"effort": effort}
        if cfg.get("ai_pro_mode", "0") == "1" and model.startswith("gpt-5.6"):
            payload["reasoning"]["mode"] = "pro"
    response = _request_json(
        "https://api.openai.com/v1/responses",
        payload=payload,
        headers={"Authorization": f"Bearer {key}"},
        timeout=timeout,
    )
    text, sources = _extract_openai_text(response)
    if not text:
        raise AIError("OpenAI API no devolvió contenido")
    return AIResult(
        text=text,
        provider="openai",
        model=model,
        sources=sources,
        usage=response.get("usage", {}) or {},
        raw=response,
    )


def _compatible_generate(
    messages: list[dict[str, str]],
    *,
    system: str,
    model: str,
    timeout: int,
    cfg: dict[str, str],
) -> AIResult:
    base = (cfg.get("compatible_base_url") or "").strip().rstrip("/")
    if not base:
        raise AIError("Configura la dirección del proveedor compatible")
    if not model:
        raise AIError("Selecciona un modelo compatible en Ajustes")
    key = get_secret("compatible_api_key")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    chat_messages = []
    if system:
        chat_messages.append({"role": "system", "content": system})
    chat_messages.extend(messages)
    response = _request_json(
        f"{base}/chat/completions",
        payload={"model": model, "messages": chat_messages, "stream": False},
        headers=headers,
        timeout=timeout,
    )
    choices = response.get("choices", []) if isinstance(response, dict) else []
    text = str(choices[0].get("message", {}).get("content", "")).strip() if choices else ""
    if not text:
        raise AIError("El proveedor compatible no devolvió contenido")
    return AIResult(text=text, provider="compatible", model=model, usage=response.get("usage", {}) or {}, raw=response)


def provider_status() -> dict[str, Any]:
    cfg = settings()
    local_models = ollama_models(cfg.get("ollama_url"))
    return {
        "mode": cfg.get("ai_mode", "automatic"),
        "preferred_provider": cfg.get("ai_provider", "automatic"),
        "credential_storage": storage_mode(),
        "providers": {
            "openai": {
                "configured": has_secret("openai_api_key"),
                "masked_key": masked_secret("openai_api_key"),
                "model": cfg.get("openai_model", ""),
                "web_search": True,
            },
            "ollama": {
                "configured": bool(cfg.get("ollama_model")),
                "online": bool(local_models),
                "model": cfg.get("ollama_model", ""),
                "models": local_models,
                "web_search": False,
            },
            "compatible": {
                "configured": bool(cfg.get("compatible_base_url")) and (has_secret("compatible_api_key") or cfg.get("compatible_no_key", "0") == "1"),
                "masked_key": masked_secret("compatible_api_key"),
                "base_url": cfg.get("compatible_base_url", ""),
                "model": cfg.get("compatible_model", ""),
                "web_search": False,
            },
        },
    }


def _available(provider: str, cfg: dict[str, str]) -> bool:
    if provider == "openai":
        return has_secret("openai_api_key") and bool(_purpose_model(cfg, "openai", "general"))
    if provider == "ollama":
        return bool(_purpose_model(cfg, "ollama", "general"))
    if provider == "compatible":
        has_auth = has_secret("compatible_api_key") or cfg.get("compatible_no_key", "0") == "1"
        return bool(cfg.get("compatible_base_url")) and has_auth and bool(_purpose_model(cfg, "compatible", "general"))
    return False


def _provider_order(cfg: dict[str, str], preferred: str | None = None) -> list[str]:
    mode = cfg.get("ai_mode", "automatic")
    explicit = preferred or cfg.get("ai_provider", "automatic")
    if mode == "local":
        return ["ollama"]
    if mode == "cloud":
        return [explicit] if explicit in {"openai", "compatible"} else ["openai", "compatible"]
    if explicit in {"openai", "ollama", "compatible"}:
        order = [explicit]
    else:
        order = ["openai", "compatible", "ollama"]
    if cfg.get("ai_fallback_local", "1") == "1" and "ollama" not in order:
        order.append("ollama")
    return order


def generate(
    prompt: str,
    *,
    system: str = "",
    purpose: str = "general",
    allow_web: bool = False,
    allowed_domains: list[str] | None = None,
    preferred_provider: str | None = None,
    timeout: int = 360,
    history: list[dict[str, str]] | None = None,
) -> AIResult:
    cfg = settings()
    messages = list(history or [])
    messages.append({"role": "user", "content": prompt})
    errors: list[str] = []
    for provider in _provider_order(cfg, preferred_provider):
        model = _purpose_model(cfg, provider, purpose)
        if provider == "openai" and not has_secret("openai_api_key"):
            errors.append("OpenAI: clave no configurada")
            continue
        if provider == "compatible":
            has_auth = has_secret("compatible_api_key") or cfg.get("compatible_no_key", "0") == "1"
            if not cfg.get("compatible_base_url") or not has_auth:
                errors.append("Compatible: conexión incompleta")
                continue
        if provider == "ollama" and not model:
            errors.append("Ollama: modelo no seleccionado")
            continue
        try:
            if provider == "openai":
                return _openai_generate(messages, system=system, model=model, timeout=timeout, allow_web=allow_web, allowed_domains=allowed_domains, cfg=cfg)
            if provider == "compatible":
                return _compatible_generate(messages, system=system, model=model, timeout=timeout, cfg=cfg)
            if provider == "ollama":
                return _ollama_generate(messages, system=system, model=model, timeout=timeout, cfg=cfg)
        except AIError as exc:
            errors.append(f"{provider}: {exc}")
            continue
    joined = "; ".join(errors) if errors else "No hay un proveedor de IA configurado"
    raise AIError(joined)
