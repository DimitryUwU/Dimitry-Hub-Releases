from __future__ import annotations

import json
import re
import unicodedata
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import DATA_DIR, atomic_write_bytes, utc_now


PERFECT_PALS_DIR = DATA_DIR / "palworld_perfect_pals"
CATALOG_DIR = PERFECT_PALS_DIR / "catalogo"
GENERATIONS_DIR = PERFECT_PALS_DIR / "generaciones"
STATE_FILE = PERFECT_PALS_DIR / "estado.json"
CATALOG_META_FILE = CATALOG_DIR / "catalogo.json"

UPSTREAM_ROOT = "https://raw.githubusercontent.com/oMaN-Rod/palworld-save-pal/main/data/json"
CATALOG_SOURCES = {
    "pals": f"{UPSTREAM_ROOT}/pals.json",
    "passive_skills": f"{UPSTREAM_ROOT}/passive_skills.json",
    "pals_es": f"{UPSTREAM_ROOT}/l10n/es/pals.json",
    "passive_skills_es": f"{UPSTREAM_ROOT}/l10n/es/passive_skills.json",
    "items_es": f"{UPSTREAM_ROOT}/l10n/es/items.json",
}
MAX_CATALOG_BYTES = 16 * 1024 * 1024

SPECIAL_MARKERS = ("PREDATOR_", "RAID_", "GYM_", "SUMMON_", "_OILRIG")
EXCLUDED_SPECIES = {"Astralym"}
PREFERRED_VARIANTS = {"PlantSlime": "PlantSlime_Flower"}

CORE_STATS = {"ShotAttack", "CraftSpeed", "MoveSpeed", "Defense", "MaxHP"}
ELEMENT_EFFECTS = {
    "Fire": "ElementBoost_Fire",
    "Water": "ElementBoost_Water",
    "Electricity": "ElementBoost_Electricity",
    "Leaf": "ElementBoost_Leaf",
    "Ice": "ElementBoost_Ice",
    "Earth": "ElementBoost_Earth",
    "Dark": "ElementBoost_Dark",
    "Dragon": "ElementBoost_Dragon",
    "Normal": "ElementBoost_Normal",
}

CATALOG_SEARCH_FILES = {
    "items_es": ("Objeto del catálogo actualizado", "objeto"),
    "pals_es": ("Pal del catálogo actualizado", "pal"),
    "passive_skills_es": ("Pasiva del catálogo actualizado", "pasiva"),
}
CATALOG_SEARCH_STOP_WORDS = {
    "a", "al", "como", "con", "cual", "cuales", "de", "del", "el", "en", "es", "la", "las", "lo", "los",
    "me", "para", "por", "que", "se", "un", "una", "y",
}


def ensure_perfect_dirs() -> None:
    for directory in (PERFECT_PALS_DIR, CATALOG_DIR, GENERATIONS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _search_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_marks = "".join(character for character in normalized if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", " ", without_marks.lower()).strip()


def search_live_catalog(question: str, limit: int = 5) -> list[dict[str, Any]]:
    """Busca nombres actuales de Pals, objetos y pasivas sin depender de una IA."""
    query = _search_text(question)
    query_tokens = {token for token in query.split() if len(token) > 2 and token not in CATALOG_SEARCH_STOP_WORDS}
    if not query_tokens:
        return []
    metadata = _read_json(CATALOG_META_FILE, {}) or {}
    matches: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for file_key, (source_label, category) in CATALOG_SEARCH_FILES.items():
        records = _read_json(CATALOG_DIR / f"{file_key}.json", {}) or {}
        if not isinstance(records, dict):
            continue
        for internal_id, record in records.items():
            if not isinstance(record, dict):
                continue
            name = str(record.get("localized_name") or "").strip()
            if not name:
                continue
            normalized_name = _search_text(name)
            name_tokens = set(normalized_name.split())
            overlap = query_tokens & name_tokens
            if not overlap and normalized_name not in query:
                continue
            description = str(record.get("description") or "").strip()
            description_tokens = set(_search_text(description).split())
            score = (30 if normalized_name and normalized_name in query else 0) + len(overlap) * 8 + len(query_tokens & description_tokens)
            dedupe_key = f"{category}:{normalized_name}"
            if dedupe_key in seen_names:
                continue
            seen_names.add(dedupe_key)
            matches.append({
                "domain": "palworld",
                "category": category,
                "source": "catalogo-palworld-save-pal",
                "source_key": f"catalog-{file_key}-{internal_id}",
                "title": name,
                "snippet": f"{source_label}. Código interno: {internal_id}. {description}".strip(),
                "score": score,
                "updated_at": metadata.get("synced_at") or metadata.get("updated_at") or "",
            })
    matches.sort(key=lambda item: (-int(item["score"]), str(item["title"]).casefold()))
    if matches and int(matches[0]["score"]) >= 30:
        threshold = int(matches[0]["score"]) - 8
        matches = [item for item in matches if int(item["score"]) >= threshold]
    return matches[: max(1, min(int(limit), 20))]


def _download_json(url: str, timeout: int = 45) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Dimitry-Hub-Perfect-Pals/1.2",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_CATALOG_BYTES:
                raise RuntimeError("El catálogo remoto supera el límite de seguridad")
            raw = response.read(MAX_CATALOG_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"El catálogo de Pals respondió con HTTP {exc.code}") from exc
    except Exception as exc:
        raise RuntimeError(f"No se pudo descargar el catálogo actualizado: {exc}") from exc
    if len(raw) > MAX_CATALOG_BYTES:
        raise RuntimeError("El catálogo remoto supera el límite de seguridad")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("El catálogo remoto no contiene JSON válido") from exc
    if not isinstance(value, dict) or not value:
        raise RuntimeError("El catálogo remoto está vacío o tiene un formato incompatible")
    return value


def sync_catalog(force: bool = True) -> dict:
    """Descarga como una unidad el catálogo que el editor oficial usa para los Pals."""
    ensure_perfect_dirs()
    cached = load_cached_catalog()
    if cached and not force:
        return cached

    downloaded: dict[str, dict] = {}
    try:
        for key, url in CATALOG_SOURCES.items():
            downloaded[key] = _download_json(url)
    except RuntimeError:
        if cached:
            cached["catalog_meta"]["using_cache"] = True
            return cached
        raise

    if len(downloaded["pals"]) < 100 or len(downloaded["passive_skills"]) < 20:
        raise RuntimeError("El catálogo actualizado parece incompleto; se conservó la copia anterior")

    for key, value in downloaded.items():
        _write_json(CATALOG_DIR / f"{key}.json", value)
    meta = {
        "synced_at": utc_now(),
        "source": "oMaN-Rod/palworld-save-pal",
        "pals": len(downloaded["pals"]),
        "passive_skills": len(downloaded["passive_skills"]),
        "using_cache": False,
    }
    _write_json(CATALOG_META_FILE, meta)
    return {**downloaded, "catalog_meta": meta}


def load_cached_catalog() -> dict:
    ensure_perfect_dirs()
    values: dict[str, dict] = {}
    for key in CATALOG_SOURCES:
        value = _read_json(CATALOG_DIR / f"{key}.json")
        if not isinstance(value, dict) or not value:
            return {}
        values[key] = value
    meta = _read_json(CATALOG_META_FILE, {})
    values["catalog_meta"] = meta if isinstance(meta, dict) else {}
    return values


def _localized_name(localized: dict, character_id: str) -> str:
    value = localized.get(character_id)
    if isinstance(value, dict):
        name = value.get("localized_name") or value.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return character_id


def _normal_species(pals: dict) -> list[tuple[str, dict]]:
    rows: list[tuple[str, dict]] = []
    for character_id, data in pals.items():
        if not isinstance(data, dict) or not data.get("is_pal") or data.get("disabled"):
            continue
        upper = character_id.upper()
        if any(marker in upper for marker in SPECIAL_MARKERS):
            continue
        if character_id in EXCLUDED_SPECIES:
            continue
        rows.append((character_id, data))

    # Gumoss se conserva únicamente en su variante histórica con flor roja.
    for base, preferred in PREFERRED_VARIANTS.items():
        if any(character_id == preferred for character_id, _ in rows):
            rows = [(character_id, data) for character_id, data in rows if character_id != base]

    rows.sort(key=lambda item: (int(item[1].get("pal_deck_index") or 99999), item[0]))
    return rows


def classify_role(pal: dict, character_id: str = "", localized_items: dict | None = None) -> str:
    suitability = pal.get("work_suitability") if isinstance(pal.get("work_suitability"), dict) else {}
    native_values = [max(0, int(value or 0)) for value in suitability.values()]
    ranch = max(0, int(suitability.get("MonsterFarm") or 0))
    non_ranch = [value for key, value in suitability.items() if key != "MonsterFarm"]
    max_non_ranch = max([int(value or 0) for value in non_ranch] or [0])
    mount_item = (localized_items or {}).get(f"SkillUnlock_{character_id}")
    mount_description = ""
    if isinstance(mount_item, dict):
        mount_description = str(mount_item.get("description") or "").lower()
    if any(word in mount_description for word in ("montar", "montura", "volar")):
        return "montura"
    ride_speed = int(pal.get("ride_sprint_speed") or 0)
    if ride_speed >= 900 and max(native_values or [0]) <= 2:
        return "montura"
    if ranch > 0 and max_non_ranch <= 1:
        return "rancho"
    if max(native_values or [0]) >= 3 or sum(native_values) >= 8:
        return "trabajo"
    return "combate"


def _skill_effects(skill: dict) -> list[dict]:
    effects = skill.get("effects")
    return [effect for effect in effects if isinstance(effect, dict)] if isinstance(effects, list) else []


def _has_core_penalty(skill: dict) -> bool:
    for effect in _skill_effects(skill):
        if effect.get("type") in CORE_STATS and float(effect.get("value") or 0) < 0:
            return True
    return False


def _effect_value(skill: dict, effect_type: str) -> float:
    total = 0.0
    for effect in _skill_effects(skill):
        if effect.get("type") == effect_type and effect.get("target") in {None, "ToSelf"}:
            total += float(effect.get("value") or 0)
    return total


def _passive_score(skill: dict, role: str, elements: list[str]) -> float:
    if skill.get("disabled") or not skill.get("add_pal") or _has_core_penalty(skill):
        return -1.0
    skill_id = str(skill.get("_dimitry_id") or "")
    if re.match(r"^(?:TEST|DEBUG|DEV|CHEAT)[_-]", skill_id, re.IGNORECASE):
        return -1.0
    score = 0.0
    if role == "montura":
        score += max(0.0, _effect_value(skill, "MoveSpeed")) * 9
        score += max(0.0, _effect_value(skill, "ShotAttack")) * 1.5
        score += max(0.0, _effect_value(skill, "ActiveSkillCoolTime_Decrease"))
        if skill.get("invoke_riding"):
            score += 180
    elif role == "trabajo":
        score += max(0.0, _effect_value(skill, "CraftSpeed")) * 9
        score += max(0.0, _effect_value(skill, "MoveSpeed")) * 2
        score += max(0.0, _effect_value(skill, "FullStomatch_Decrease")) * 2
        score += max(0.0, _effect_value(skill, "Sanity_Decrease")) * 2
        if skill.get("invoke_in_base"):
            score += 80
    elif role == "rancho":
        score += max(0.0, _effect_value(skill, "MoveSpeed")) * 3
        score += max(0.0, _effect_value(skill, "FullStomatch_Decrease")) * 6
        score += max(0.0, _effect_value(skill, "Sanity_Decrease")) * 6
        score += max(0.0, _effect_value(skill, "CraftSpeed"))
        if skill.get("invoke_in_base"):
            score += 80
    else:
        score += max(0.0, _effect_value(skill, "ShotAttack")) * 8
        score += max(0.0, _effect_value(skill, "ActiveSkillCoolTime_Decrease")) * 5
        score += max(0.0, _effect_value(skill, "Defense")) * 2
        score += max(0.0, _effect_value(skill, "MaxHP")) * 2
        for element in elements:
            effect_type = ELEMENT_EFFECTS.get(element)
            if effect_type:
                score += max(0.0, _effect_value(skill, effect_type)) * 6
    if score <= 0:
        return -1.0
    score += float(skill.get("rank") or 0) * 0.1
    if skill.get("invoke_always"):
        score += 3
    return score


def select_passives(pal: dict, passive_skills: dict, role: str) -> list[str]:
    elements = [str(value) for value in (pal.get("element_types") or [])]
    chosen: list[str] = []
    inherent = pal.get("passive_skills") if isinstance(pal.get("passive_skills"), list) else []
    for skill_id in inherent:
        skill = passive_skills.get(skill_id)
        if isinstance(skill, dict):
            skill = {**skill, "_dimitry_id": str(skill_id)}
        if (
            isinstance(skill, dict)
            and _passive_score(skill, role, elements) > 0
            and skill_id not in chosen
        ):
            chosen.append(str(skill_id))
            if len(chosen) == 4:
                return chosen

    scored: list[tuple[float, str]] = []
    for skill_id, skill in passive_skills.items():
        if not isinstance(skill, dict) or skill_id in chosen:
            continue
        score = _passive_score({**skill, "_dimitry_id": str(skill_id)}, role, elements)
        if score > 0:
            scored.append((score, str(skill_id)))
    scored.sort(key=lambda item: (-item[0], item[1]))
    for _, skill_id in scored:
        chosen.append(skill_id)
        if len(chosen) == 4:
            break
    if len(chosen) != 4:
        raise RuntimeError("El catálogo actualizado no ofrece cuatro pasivas compatibles")
    return chosen


def _active_and_learned_skills(pal: dict) -> tuple[list[str], list[str]]:
    raw = pal.get("skill_set") if isinstance(pal.get("skill_set"), dict) else {}
    ordered = sorted(raw.items(), key=lambda item: (int(item[1] or 0), item[0]))
    learned = [f"EPalWazaID::{skill_id}" for skill_id, _ in ordered if skill_id and skill_id != "None"]
    return learned[-3:], learned


def build_profile(
    character_id: str,
    pal: dict,
    gender: str,
    passive_skills: dict,
    localized_pals: dict,
    localized_items: dict | None = None,
) -> dict:
    if gender not in {"Male", "Female"}:
        raise ValueError("El sexo del perfil no es válido")
    name = _localized_name(localized_pals, character_id)
    role = classify_role(pal, character_id, localized_items)
    active, learned = _active_and_learned_skills(pal)
    work = pal.get("work_suitability") if isinstance(pal.get("work_suitability"), dict) else {}
    profile = {
        "name": f"Dimitry · {name} · {'Macho' if gender == 'Male' else 'Hembra'} · {role}",
        "type": "pal_preset",
        "pal_preset": {
            "lock": True,
            "lock_element": False,
            "character_id": character_id,
            "is_lucky": False,
            "is_boss": character_id == "KingWhale",
            "gender": gender,
            "rank_hp": 20,
            "rank_attack": 20,
            "rank_defense": 20,
            "rank_craftspeed": 20,
            "talent_hp": 100,
            "talent_shot": 100,
            "talent_defense": 100,
            "rank": 5,
            "level": 1,
            "exp": 0,
            "learned_skills": learned,
            "active_skills": active,
            "passive_skills": select_passives(pal, passive_skills, role),
            "work_suitability": {key: int(value or 0) for key, value in work.items()},
            "sanity": 100.0,
            "nickname": name,
            "filtered_nickname": name,
            "stomach": float(pal.get("max_full_stomach") or 150),
            "friendship_point": 200000,
        },
    }
    validate_profile(profile, pal)
    return profile


def validate_profile(profile: dict, source_pal: dict) -> None:
    pal_profile = profile.get("pal_preset") if isinstance(profile.get("pal_preset"), dict) else {}
    if pal_profile.get("gender") not in {"Male", "Female"}:
        raise RuntimeError("Un perfil generado carece de sexo válido")
    if pal_profile.get("level") != 1 or pal_profile.get("exp") != 0:
        raise RuntimeError("Un perfil generado no conserva el nivel 1")
    if len(pal_profile.get("passive_skills") or []) != 4:
        raise RuntimeError("Un perfil generado no contiene exactamente cuatro pasivas")
    expected_work = {
        key: int(value or 0)
        for key, value in (source_pal.get("work_suitability") or {}).items()
    }
    if pal_profile.get("work_suitability") != expected_work:
        raise RuntimeError("Un perfil generado alteró el trabajo nativo de la especie")
    for key in ("talent_hp", "talent_shot", "talent_defense"):
        if pal_profile.get(key) != 100:
            raise RuntimeError("Un perfil generado no maximizó los IV")
    for key in ("rank_hp", "rank_attack", "rank_defense", "rank_craftspeed"):
        if pal_profile.get(key) != 20:
            raise RuntimeError("Un perfil generado no maximizó las almas")


def _safe_entry_name(index: int, character_id: str, gender: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", character_id).strip("._") or "Pal"
    sex = "macho" if gender == "Male" else "hembra"
    return f"{index:04d}-{safe}-{sex}.json"


def _write_profile_zip(path: Path, profiles: list[tuple[str, dict]], gender: str) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, (character_id, profile) in enumerate(profiles, 1):
            validate = profile.get("pal_preset", {})
            if validate.get("gender") != gender:
                raise RuntimeError("El paquete mezcló perfiles de sexos diferentes")
            archive.writestr(
                _safe_entry_name(index, character_id, gender),
                json.dumps(profile, ensure_ascii=False, indent=2).encode("utf-8"),
            )


def _state() -> dict:
    value = _read_json(STATE_FILE, {})
    return value if isinstance(value, dict) else {}


def generate_profile_packages(catalog: dict | None = None, refresh: bool = True) -> dict:
    ensure_perfect_dirs()
    catalog = catalog or sync_catalog(force=refresh)
    pals = catalog.get("pals") or {}
    passive_skills = catalog.get("passive_skills") or {}
    localized_pals = catalog.get("pals_es") or {}
    localized_items = catalog.get("items_es") or {}
    species = _normal_species(pals)
    if not species:
        raise RuntimeError("No se encontraron especies compatibles en el catálogo actualizado")

    previous_ids = set(_state().get("known_species") or [])
    current_ids = {character_id for character_id, _ in species}
    new_ids = current_ids - previous_ids if previous_ids else set()

    generation_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    generation_dir = GENERATIONS_DIR / generation_id
    suffix = 2
    while generation_dir.exists():
        generation_dir = GENERATIONS_DIR / f"{generation_id}-{suffix}"
        suffix += 1
    generation_dir.mkdir(parents=True)
    generation_id = generation_dir.name

    male_profiles: list[tuple[str, dict]] = []
    female_profiles: list[tuple[str, dict]] = []
    new_profiles: list[tuple[str, dict]] = []
    roles: Counter[str] = Counter()
    plan_rows: list[dict] = []
    for character_id, pal in species:
        role = classify_role(pal, character_id, localized_items)
        male = build_profile(character_id, pal, "Male", passive_skills, localized_pals, localized_items)
        female = build_profile(character_id, pal, "Female", passive_skills, localized_pals, localized_items)
        male_profiles.append((character_id, male))
        female_profiles.append((character_id, female))
        if character_id in new_ids:
            new_profiles.extend(((character_id, male), (character_id, female)))
        roles[role] += 1
        plan_rows.append({
            "character_id": character_id,
            "name": _localized_name(localized_pals, character_id),
            "role": role,
            "passive_skills": male["pal_preset"]["passive_skills"],
            "level": 1,
            "work_suitability": male["pal_preset"]["work_suitability"],
            "new": character_id in new_ids,
        })

    male_zip = generation_dir / "Dimitry_Perfectos_Machos.zip"
    female_zip = generation_dir / "Dimitry_Perfectos_Hembras.zip"
    new_zip = generation_dir / "Dimitry_Perfectos_Solo_Nuevos.zip"
    _write_profile_zip(male_zip, male_profiles, "Male")
    _write_profile_zip(female_zip, female_profiles, "Female")
    with zipfile.ZipFile(new_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, (character_id, profile) in enumerate(new_profiles, 1):
            gender = profile["pal_preset"]["gender"]
            archive.writestr(
                _safe_entry_name(index, character_id, gender),
                json.dumps(profile, ensure_ascii=False, indent=2).encode("utf-8"),
            )

    plan = {
        "generation_id": generation_id,
        "generated_at": utc_now(),
        "source": catalog.get("catalog_meta", {}).get("source", "oMaN-Rod/palworld-save-pal"),
        "catalog_synced_at": catalog.get("catalog_meta", {}).get("synced_at", ""),
        "species": len(species),
        "pairs": len(species),
        "profiles": len(species) * 2,
        "new_species": len(new_ids),
        "new_species_ids": sorted(new_ids),
        "roles": dict(sorted(roles.items())),
        "rules": {
            "level": 1,
            "genders": ["Male", "Female"],
            "passive_skills": 4,
            "ivs": 100,
            "souls": 20,
            "rank": 5,
            "native_work_suitability": True,
            "preferred_gumoss": "PlantSlime_Flower",
            "panthalus_alpha": "KingWhale",
            "excluded_global_storage": sorted(EXCLUDED_SPECIES),
        },
        "pals": plan_rows,
    }
    _write_json(generation_dir / "plan.json", plan)
    _write_json(STATE_FILE, {
        "known_species": sorted(current_ids),
        "last_generation_id": generation_id,
        "updated_at": utc_now(),
    })
    return public_generation(plan)


def public_generation(plan: dict) -> dict:
    generation_id = str(plan.get("generation_id") or "")
    return {
        key: value for key, value in plan.items() if key != "pals"
    } | {
        "preview": plan.get("pals", [])[:30],
        "downloads": {
            "male": f"/api/palworld/perfect-pals/download/{generation_id}/male",
            "female": f"/api/palworld/perfect-pals/download/{generation_id}/female",
            "new": f"/api/palworld/perfect-pals/download/{generation_id}/new",
            "plan": f"/api/palworld/perfect-pals/download/{generation_id}/plan",
        },
    }


def perfect_pals_status() -> dict:
    ensure_perfect_dirs()
    state = _state()
    generation_id = str(state.get("last_generation_id") or "")
    plan = _read_json(GENERATIONS_DIR / generation_id / "plan.json", {}) if generation_id else {}
    return {
        "available": bool(plan),
        "last_generation": public_generation(plan) if isinstance(plan, dict) and plan else None,
        "catalog": _read_json(CATALOG_META_FILE, {}),
        "workflow": {
            "mass_fill": True,
            "male_and_female": True,
            "preview_before_save": True,
            "automatic_backup": True,
            "direct_original_write": False,
        },
    }


def generation_download(generation_id: str, file_key: str) -> tuple[Path, str]:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}(?:-[0-9]+)?", generation_id):
        raise ValueError("La generación solicitada no es válida")
    names = {
        "male": "Dimitry_Perfectos_Machos.zip",
        "female": "Dimitry_Perfectos_Hembras.zip",
        "new": "Dimitry_Perfectos_Solo_Nuevos.zip",
        "plan": "plan.json",
    }
    name = names.get(file_key)
    if not name:
        raise ValueError("El archivo solicitado no es válido")
    path = (GENERATIONS_DIR / generation_id / name).resolve()
    root = GENERATIONS_DIR.resolve()
    if root not in path.parents or not path.exists():
        raise FileNotFoundError("La generación solicitada ya no existe")
    return path, name
