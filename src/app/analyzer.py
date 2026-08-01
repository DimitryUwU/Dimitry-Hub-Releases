from __future__ import annotations

import json
import re
import shutil
import zipfile
from collections import Counter
from pathlib import Path

from .database import KNOWLEDGE_DIR, safe_filename, utc_now
from .knowledge import TEXT_EXTENSIONS, index_directory, safe_extract_zip

UNITY_MARKERS = {
    "assembly-csharp.dll": "Unity Mono / código C# administrado",
    "global-metadata.dat": "Unity IL2CPP",
    "gameassembly.dll": "Unity IL2CPP en Windows",
    "libil2cpp.so": "Unity IL2CPP en Android/Linux",
    "resources.assets": "Recursos de Unity",
    "sharedassets": "Paquetes de recursos de Unity",
    "managed": "Carpeta de ensamblados administrados",
}

LANGUAGE_BY_SUFFIX = {
    ".lua": "Lua", ".cs": "C#", ".smali": "Smali", ".java": "Java",
    ".cpp": "C++", ".c": "C", ".h": "C/C++ header", ".py": "Python",
    ".js": "JavaScript", ".ts": "TypeScript", ".rs": "Rust", ".json": "JSON",
    ".xml": "XML", ".ini": "INI/config", ".cfg": "Config", ".dll": "DLL",
    ".so": "Shared library", ".dat": "Data/binary", ".apk": "Android package",
}


def analyze_bundle(root: Path) -> dict:
    files = [path for path in root.rglob("*") if path.is_file()]
    extensions = Counter(path.suffix.lower() or "[sin extensión]" for path in files)
    languages = Counter(LANGUAGE_BY_SUFFIX.get(path.suffix.lower(), "Otro") for path in files)
    markers: list[dict] = []
    for path in files:
        lower_name = path.name.lower()
        lower_parts = {part.lower() for part in path.parts}
        for marker, meaning in UNITY_MARKERS.items():
            if marker in lower_name or marker in lower_parts:
                markers.append({"marker": marker, "meaning": meaning, "path": str(path.relative_to(root))})
    text_files = sum(1 for path in files if path.suffix.lower() in TEXT_EXTENSIONS)
    likely = []
    marker_text = " ".join(item["marker"] for item in markers)
    if "global-metadata.dat" in marker_text or "gameassembly.dll" in marker_text or "libil2cpp.so" in marker_text:
        likely.append("Proyecto o dump Unity IL2CPP")
    if "assembly-csharp.dll" in marker_text or any(part.lower() == "managed" for path in files for part in path.parts):
        likely.append("Proyecto o dump Unity Mono")
    if extensions[".lua"]:
        likely.append("Incluye scripts Lua")
    if extensions[".smali"]:
        likely.append("Incluye código Android desensamblado (Smali)")
    if extensions[".apk"]:
        likely.append("Incluye un APK")
    return {
        "file_count": len(files),
        "text_file_count": text_files,
        "total_bytes": sum(path.stat().st_size for path in files),
        "extensions": [{"name": key, "count": value} for key, value in extensions.most_common(20)],
        "languages": [{"name": key, "count": value} for key, value in languages.most_common(12)],
        "markers": markers[:30],
        "classification": likely or ["Paquete genérico; se indexarán los archivos de texto reconocibles."],
    }
