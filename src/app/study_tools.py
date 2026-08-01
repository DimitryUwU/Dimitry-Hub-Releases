from __future__ import annotations

import re


STUDY_LABELS = {
    "ficha": "Ficha de estudio",
    "simple": "Explicación sencilla",
    "exam": "Simulacro de examen",
    "speech": "Guion de exposición",
    "cards": "Tarjetas de memoria",
    "outline": "Libro de estudio",
}


def _clean_source(content: str) -> str:
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if len(re.findall(r"\w+", cleaned, flags=re.UNICODE)) < 5:
        raise ValueError("El contenido es demasiado breve para crear material de estudio.")
    return cleaned


def _is_heading(line: str) -> bool:
    plain = line.strip().lstrip("#").strip()
    if not plain or len(plain) > 120:
        return False
    return bool(
        re.match(r"^(?:#{1,4}\s+|DIAPOSITIVA\s+\d+\b|UNIDAD\s+\d+\b|CAP[IÍ]TULO\s+\d+\b|TEMA\s+\d+\b)", line, re.I)
        or (plain.isupper() and len(plain.split()) <= 12)
    )


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+|(?=\s*[-•] ?)", text)
    sentences: list[str] = []
    for part in parts:
        value = re.sub(r"^[-•◦▪]\s*", "", part.strip())
        if len(value) >= 3 and value not in sentences:
            sentences.append(value)
    return sentences


def _sections(text: str) -> list[tuple[str, list[str]]]:
    result: list[tuple[str, list[str]]] = []
    title = "Contenido principal"
    buffer: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _is_heading(line):
            if buffer:
                result.append((title, _sentences("\n".join(buffer))))
            title = line.lstrip("#").strip().title() if not line.upper().startswith("DIAPOSITIVA") else line.title()
            buffer = []
        else:
            buffer.append(line)
    if buffer:
        result.append((title, _sentences("\n".join(buffer))))
    if not result:
        result = [("Contenido principal", _sentences(text))]

    polished: list[tuple[str, list[str]]] = []
    for section_title, ideas in result:
        if section_title.lower().startswith("diapositiva") and len(ideas) > 1 and len(ideas[0]) <= 100:
            section_title = f"{section_title}: {ideas[0]}"
            ideas = ideas[1:]
        if ideas:
            polished.append((section_title, ideas))
    return polished or [("Contenido principal", _sentences(text))]


def _all_ideas(sections: list[tuple[str, list[str]]]) -> list[tuple[str, str]]:
    return [(title, idea) for title, ideas in sections for idea in ideas if idea]


def _definitions(ideas: list[tuple[str, str]]) -> list[str]:
    patterns = r"\b(?:es|son|consiste en|se define como|se entiende por|comprende|requiere|permite)\b"
    return [idea for _, idea in ideas if re.search(patterns, idea, re.I)][:12]


def _ficha(sections: list[tuple[str, list[str]]], word_count: int) -> str:
    ideas = _all_ideas(sections)
    overview = [idea for _, idea in ideas[:4]]
    definitions = _definitions(ideas)
    lines = ["# Ficha de estudio", "", "## Panorama general"]
    lines.extend(f"- {idea}" for idea in overview)
    lines.extend(["", "## Contenido organizado por temas"])
    for title, section_ideas in sections[:20]:
        lines.extend(["", f"### {title}"])
        lines.extend(f"- {idea}" for idea in section_ideas[:10])
    if definitions:
        lines.extend(["", "## Definiciones, requisitos y relaciones clave"])
        lines.extend(f"- {item}" for item in definitions)
    lines.extend(["", "## Preguntas de repaso"])
    for index, (title, section_ideas) in enumerate(sections[:12], start=1):
        answer = section_ideas[0] if section_ideas else "Revisar el material fuente."
        lines.extend([f"{index}. ¿Qué idea principal desarrolla «{title}»?", f"   Respuesta: {answer}"])
    lines.extend([
        "",
        "## Control de fidelidad",
        f"- Base utilizada: {word_count} palabras extraídas o pegadas.",
        "- Esta ficha reorganiza únicamente el material proporcionado; no agrega datos, normas ni fuentes externas.",
        "- Si una diapositiva dependía de una imagen sin texto, revísala junto con el archivo original.",
    ])
    return "\n".join(lines)


def _cards(sections: list[tuple[str, list[str]]]) -> str:
    lines = ["# Tarjetas de memoria", "", "Pregunta | Respuesta"]
    for title, ideas in sections:
        if ideas:
            lines.append(f"¿Cuál es la idea principal de {title}? | {ideas[0]}")
        for idea in ideas[1:4]:
            lines.append(f"¿Qué debe recordarse sobre {title}? | {idea}")
    return "\n".join(lines[:34])


def _exam(sections: list[tuple[str, list[str]]]) -> str:
    ideas = _all_ideas(sections)
    stems = (
        "Explique con sus propias palabras",
        "Identifique el dato esencial de",
        "Relacione con el tema general",
        "Señale un posible error al interpretar",
    )
    lines = ["# Simulacro de examen", "", "## Preguntas y respuestas modelo"]
    number = 1
    for index, (title, idea) in enumerate(ideas[:20]):
        lines.extend([f"{number}. {stems[index % len(stems)]} «{title}».", f"   Respuesta modelo: {idea}"])
        number += 1
        if number > 20:
            break
    lines.extend(["", "## Criterio de corrección", "- La respuesta debe conservar el sentido del material fuente y diferenciar datos expresos de inferencias."])
    return "\n".join(lines)


def _simple(sections: list[tuple[str, list[str]]]) -> str:
    lines = ["# Explicación sencilla", "", "## Idea general"]
    ideas = _all_ideas(sections)
    lines.extend(f"- {idea}" for _, idea in ideas[:3])
    lines.extend(["", "## Explicación paso a paso"])
    for index, (title, section_ideas) in enumerate(sections, start=1):
        lines.append(f"{index}. {title}")
        lines.extend(f"   - {idea}" for idea in section_ideas[:6])
    lines.extend(["", "## Comprueba si lo entendiste"])
    lines.extend(f"- ¿Puedes explicar {title} sin mirar el material?" for title, _ in sections[:8])
    return "\n".join(lines)


def _speech(sections: list[tuple[str, list[str]]]) -> str:
    lines = ["# Guion de exposición", "", "## Apertura (20 segundos)", "Presentaré las ideas principales del material proporcionado y la relación entre sus temas."]
    for index, (title, ideas) in enumerate(sections[:8], start=1):
        lines.extend(["", f"## Parte {index}: {title}"])
        lines.extend(f"- {idea}" for idea in ideas[:5])
    lines.extend(["", "## Cierre (20 segundos)", "En conclusión, conviene repasar las ideas anteriores directamente en el material fuente para conservar sus matices.", "", "## Posibles preguntas", *[f"- ¿Cuál es la idea central de {title}?" for title, _ in sections[:6]]])
    return "\n".join(lines)


def _outline(sections: list[tuple[str, list[str]]]) -> str:
    lines = ["# Libro de estudio", "", "## Índice temático"]
    lines.extend(f"{index}. {title}" for index, (title, _) in enumerate(sections, start=1))
    for index, (title, ideas) in enumerate(sections, start=1):
        lines.extend(["", f"## {index}. {title}"])
        lines.extend(f"- {idea}" for idea in ideas)
    lines.extend(["", "## Repaso final", "- Vuelve al archivo original para revisar gráficos, imágenes o elementos no textuales."])
    return "\n".join(lines)


def generate_study_material(kind: str, content: str) -> dict:
    text = _clean_source(content)
    sections = _sections(text)
    word_count = len(re.findall(r"\w+", text, flags=re.UNICODE))
    normalized_kind = kind if kind in STUDY_LABELS else "ficha"
    builders = {
        "ficha": lambda: _ficha(sections, word_count),
        "simple": lambda: _simple(sections),
        "exam": lambda: _exam(sections),
        "speech": lambda: _speech(sections),
        "cards": lambda: _cards(sections),
        "outline": lambda: _outline(sections),
    }
    response = builders[normalized_kind]()
    return {
        "response": response,
        "kind": normalized_kind,
        "label": STUDY_LABELS[normalized_kind],
        "word_count": word_count,
        "section_count": len(sections),
    }
