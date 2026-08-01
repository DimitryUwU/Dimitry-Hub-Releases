from __future__ import annotations

import re
from collections import Counter


GAMEGUARDIAN_TYPES = {
    "BYTE": "gg.TYPE_BYTE",
    "WORD": "gg.TYPE_WORD",
    "DWORD": "gg.TYPE_DWORD",
    "QWORD": "gg.TYPE_QWORD",
    "FLOAT": "gg.TYPE_FLOAT",
    "DOUBLE": "gg.TYPE_DOUBLE",
    "XOR": "gg.TYPE_XOR",
}


def _lua_quote(value: str) -> str:
    """Devuelve una cadena Lua sin permitir que el texto inyecte código."""
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _read_lua_string(source: str, start: int) -> tuple[str, int] | None:
    if start >= len(source) or source[start] not in {'"', "'"}:
        return None
    quote = source[start]
    escaped = False
    chars: list[str] = []
    for index in range(start + 1, len(source)):
        char = source[index]
        if char == quote and not escaped:
            return "".join(chars), index + 1
        chars.append(char)
        if char == "\\" and not escaped:
            escaped = True
        else:
            escaped = False
    return None


def _decode_lua_literal(literal: str) -> str:
    data = bytearray()
    index = 0
    simple = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, '"': 34, "'": 39}
    while index < len(literal):
        char = literal[index]
        if char != "\\":
            data.extend(char.encode("utf-8"))
            index += 1
            continue
        index += 1
        if index >= len(literal):
            data.append(92)
            break
        if literal[index].isdigit():
            match = re.match(r"\d{1,3}", literal[index:])
            digits = match.group(0) if match else ""
            value = int(digits or "0")
            if value > 255:
                raise ValueError("La secuencia decimal Lua contiene un byte fuera de rango.")
            data.append(value)
            index += len(digits)
            continue
        if literal[index] == "x" and re.match(r"[0-9a-fA-F]{2}", literal[index + 1:index + 3]):
            data.append(int(literal[index + 1:index + 3], 16))
            index += 3
            continue
        data.append(simple.get(literal[index], ord(literal[index])))
        index += 1
    return data.decode("utf-8", errors="replace")


def decode_load_wrapper(source: str) -> str | None:
    """Decodifica el contenedor load("\\ddd...")() sin ejecutar el contenido."""
    cleaned = re.sub(r"\A\s*--\[\[.*?\]\]\s*", "", source, count=1, flags=re.S)
    match = re.match(r"\s*(?:load|loadstring)\s*\(\s*", cleaned)
    if not match:
        return None
    parsed = _read_lua_string(cleaned, match.end())
    if not parsed:
        return None
    literal, end = parsed
    tail = cleaned[end:]
    if not re.fullmatch(r"\s*\)\s*\(\s*\)\s*;?\s*", tail):
        return None
    return _decode_lua_literal(literal)


def analyze_lua_source(source: str, filename: str = "script.lua") -> dict:
    decoded = decode_load_wrapper(source)
    effective = decoded if decoded is not None else source
    gg_calls = Counter(re.findall(r"\bgg\.([A-Za-z_]\w*)\s*\(", effective))
    functions = sorted(set(re.findall(r"\b(?:local\s+)?function\s+([A-Za-z_]\w*(?:[.:][A-Za-z_]\w*)*)", effective)))

    environment = "GameGuardian" if gg_calls or re.search(r"\bgg\.", effective) else "Lua estándar o entorno no identificado"
    if re.search(r"\b(?:RegisterHook|RegisterKeyBind|UE4SS)\b", effective, re.I):
        environment = "UE4SS"

    findings: list[dict[str, str]] = []
    if decoded is not None:
        findings.append({"level": "warning", "label": "Carga dinámica ofuscada", "detail": "El archivo envuelve el código en load(...). Se decodificó como texto, sin ejecutarlo."})
    checks = (
        (r"\b(?:os\.execute|io\.popen)\s*\(", "danger", "Ejecución de comandos", "Puede iniciar comandos externos del sistema."),
        (r"\b(?:gg\.makeRequest|socket\.|http\.request|https\.request)\b", "danger", "Acceso de red", "Incluye una función capaz de comunicarse por red."),
        (r"\b(?:package\.loadlib|ffi\.load)\s*\(", "danger", "Carga de biblioteca nativa", "Puede cargar código compilado externo."),
        (r"\b(?:io\.open|io\.output|os\.remove|os\.rename)\s*\(", "warning", "Acceso a archivos", "Lee, escribe, elimina o cambia archivos locales."),
        (r"\b(?:dofile|loadfile|require)\s*\(", "warning", "Carga de código adicional", "Carga otro archivo o módulo durante la ejecución."),
        (r"\b(?:load|loadstring)\s*\(", "warning", "Código dinámico", "Construye o carga código durante la ejecución."),
    )
    for pattern, level, label, detail in checks:
        if re.search(pattern, effective, re.I):
            findings.append({"level": level, "label": label, "detail": detail})

    if gg_calls:
        findings.append({"level": "info", "label": "Modificación de memoria", "detail": "Usa la API de GameGuardian para buscar o cambiar valores del proceso seleccionado."})
    if not findings:
        findings.append({"level": "info", "label": "Sin indicadores evidentes", "detail": "El examen estático no encontró llamadas sensibles conocidas."})

    severity = "alto" if any(item["level"] == "danger" for item in findings) else ("medio" if any(item["level"] == "warning" for item in findings) else "bajo")
    return {
        "filename": filename,
        "environment": environment,
        "obfuscated_wrapper": decoded is not None,
        "risk_level": severity,
        "source_characters": len(source),
        "decoded_characters": len(effective),
        "line_count": len(effective.splitlines()) or 1,
        "functions": functions[:100],
        "function_count": len(functions),
        "api_calls": dict(sorted(gg_calls.items())),
        "findings": findings,
        "preview": effective[:20000],
        "preview_truncated": len(effective) > 20000,
    }


def parse_gameguardian_changes(text: str) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    for line_number, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 4:
            raise ValueError(f"La línea {line_number} debe tener cuatro partes separadas por |.")
        name, search, value_type, value = parts
        value_type = value_type.upper()
        if not name or not search or not value:
            raise ValueError(f"La línea {line_number} contiene un dato vacío.")
        if value_type not in GAMEGUARDIAN_TYPES:
            allowed = ", ".join(GAMEGUARDIAN_TYPES)
            raise ValueError(f"Tipo no permitido en la línea {line_number}. Usa: {allowed}.")
        if not re.fullmatch(r"-?(?:\d+(?:\.\d+)?|\.\d+)", value):
            raise ValueError(f"El valor nuevo de la línea {line_number} debe ser numérico.")
        if max(len(name), len(search), len(value)) > 300:
            raise ValueError(f"La línea {line_number} supera el límite de 300 caracteres por dato.")
        changes.append({"name": name, "search": search, "type": value_type, "value": value})
        if len(changes) > 100:
            raise ValueError("Se permiten como máximo 100 acciones por script.")
    if not changes:
        raise ValueError("Añade al menos una acción con el formato indicado.")
    return changes


def generate_gameguardian_script(name: str, author: str, description: str, changes_text: str) -> str:
    name = (name or "").strip()
    author = (author or "").strip()
    description = (description or "").strip()
    if not name:
        raise ValueError("Indica un nombre para el script.")
    if len(name) > 120 or len(author) > 120 or len(description) > 1000:
        raise ValueError("El nombre, autor o finalidad supera el límite permitido.")
    changes = parse_gameguardian_changes(changes_text)
    rows = []
    for item in changes:
        rows.append(
            "    { name = %s, search = %s, value_type = %s, value = %s },"
            % (_lua_quote(item["name"]), _lua_quote(item["search"]), GAMEGUARDIAN_TYPES[item["type"]], item["value"])
        )
    author_value = author or "Sin autor indicado"
    description_value = description or "Acciones configuradas por la persona usuaria."
    table_rows = "\n".join(rows)
    return f'''-- {name}
-- Autor: {author_value}
-- Finalidad: {description_value}
-- Generado por Dimitry Hub. Código legible y sin carga remota.
-- Úsalo solo sobre una copia propia, fuera de servicios competitivos, y crea un respaldo.

local SCRIPT_NAME = {_lua_quote(name)}
local MAX_RESULTS = 1000
local actions = {{
{table_rows}
}}

local original_values = {{}}
local remembered = {{}}

local function remember(result)
    local key = tostring(result.address) .. ":" .. tostring(result.flags)
    if remembered[key] then
        return
    end
    remembered[key] = true
    original_values[#original_values + 1] = {{
        address = result.address,
        flags = result.flags,
        value = result.value,
    }}
end

local function apply_action(item)
    gg.clearResults()
    gg.searchNumber(item.search, item.value_type)
    local count = gg.getResultCount()
    if count == 0 then
        gg.alert("No se encontraron coincidencias para: " .. item.name)
        return
    end

    local results = gg.getResults(math.min(count, MAX_RESULTS))
    for _, result in ipairs(results) do
        remember(result)
        result.value = item.value
        result.flags = item.value_type
    end
    gg.setValues(results)
    gg.clearResults()
    gg.toast(item.name .. ": " .. tostring(#results) .. " valor(es) cambiados")
end

local function restore_session()
    if #original_values == 0 then
        gg.alert("No hay cambios de esta sesión para restaurar.")
        return
    end
    gg.setValues(original_values)
    gg.clearResults()
    original_values = {{}}
    remembered = {{}}
    gg.toast("Valores originales restaurados")
end

local running = true
while running do
    local options = {{}}
    for _, item in ipairs(actions) do
        options[#options + 1] = item.name
    end
    options[#options + 1] = "Restaurar cambios de esta sesión"
    options[#options + 1] = "Salir"

    local choice = gg.choice(options, nil, SCRIPT_NAME .. "\nSelecciona una acción")
    if choice == nil then
        gg.toast("Menú cerrado; vuelve a ejecutar el script para abrirlo.")
        running = false
    elseif choice <= #actions then
        local ok, err = pcall(apply_action, actions[choice])
        if not ok then
            gg.alert("No se pudo aplicar la acción:\n" .. tostring(err))
        end
    elseif choice == #actions + 1 then
        local ok, err = pcall(restore_session)
        if not ok then
            gg.alert("No se pudieron restaurar los valores:\n" .. tostring(err))
        end
    else
        running = false
    end
end

gg.setVisible(true)
'''
