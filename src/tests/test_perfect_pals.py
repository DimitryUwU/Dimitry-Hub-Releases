from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from app import perfect_pals


def skill(effect_type: str, value: float, *, riding: bool = False, base: bool = False) -> dict:
    return {
        "rank": 4,
        "effects": [{"type": effect_type, "value": value, "target": "ToSelf"}],
        "invoke_always": True,
        "invoke_riding": riding,
        "invoke_in_base": base,
        "add_pal": True,
        "disabled": False,
    }


def pal(*, work: dict | None = None, ride: int = 0, deck: int = 1) -> dict:
    return {
        "is_pal": True,
        "disabled": False,
        "pal_deck_index": deck,
        "element_types": ["Fire"],
        "ride_sprint_speed": ride,
        "work_suitability": work or {"EmitFlame": 1, "MonsterFarm": 0},
        "skill_set": {"FireBlast": 1, "FireBall": 50, "Inferno": 40, "AirCanon": 7},
        "passive_skills": [],
        "max_full_stomach": 150,
    }


class PerfectPalsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.old_paths = (
            perfect_pals.PERFECT_PALS_DIR,
            perfect_pals.CATALOG_DIR,
            perfect_pals.GENERATIONS_DIR,
            perfect_pals.STATE_FILE,
            perfect_pals.CATALOG_META_FILE,
        )
        perfect_pals.PERFECT_PALS_DIR = root / "perfect"
        perfect_pals.CATALOG_DIR = perfect_pals.PERFECT_PALS_DIR / "catalogo"
        perfect_pals.GENERATIONS_DIR = perfect_pals.PERFECT_PALS_DIR / "generaciones"
        perfect_pals.STATE_FILE = perfect_pals.PERFECT_PALS_DIR / "estado.json"
        perfect_pals.CATALOG_META_FILE = perfect_pals.CATALOG_DIR / "catalogo.json"

    def tearDown(self):
        (
            perfect_pals.PERFECT_PALS_DIR,
            perfect_pals.CATALOG_DIR,
            perfect_pals.GENERATIONS_DIR,
            perfect_pals.STATE_FILE,
            perfect_pals.CATALOG_META_FILE,
        ) = self.old_paths
        self.temp.cleanup()

    def catalog(self) -> dict:
        passives = {
            "AttackBest": skill("ShotAttack", 40),
            "CooldownBest": skill("ActiveSkillCoolTime_Decrease", 30),
            "DefenseBest": skill("Defense", 30),
            "ElementBest": skill("ElementBoost_Fire", 30),
            "MoveBest": skill("MoveSpeed", 40, riding=True),
            "MoveSecond": skill("MoveSpeed", 30),
            "MoveThird": skill("MoveSpeed", 20),
            "MoveFourth": skill("MoveSpeed", 10),
            "WorkBest": skill("CraftSpeed", 75, base=True),
            "WorkSecond": skill("CraftSpeed", 50, base=True),
            "WorkThird": skill("CraftSpeed", 30),
            "WorkFourth": skill("CraftSpeed", 20),
            "BadPenalty": skill("ShotAttack", -90),
        }
        pals = {
            "PlantSlime": pal(deck=12),
            "PlantSlime_Flower": pal(deck=12),
            "KingWhale": pal(ride=1200, deck=203),
            "Astralym": pal(deck=999),
            "Worker": pal(work={"Handcraft": 4, "Mining": 3, "MonsterFarm": 0}, deck=2),
            "Mount": pal(work={"Transport": 1, "MonsterFarm": 0}, ride=1500, deck=3),
            "Fighter": pal(work={"EmitFlame": 1, "MonsterFarm": 0}, deck=4),
        }
        names = {key: {"localized_name": key} for key in pals}
        return {
            "pals": pals,
            "passive_skills": passives,
            "pals_es": names,
            "passive_skills_es": {},
            "items_es": {
                "SkillUnlock_Mount": {
                    "localized_name": "Sillín de prueba",
                    "description": "Permite montar a lomos de este Pal.",
                }
            },
            "catalog_meta": {"source": "fixture", "synced_at": "2026-08-01T00:00:00Z"},
        }

    def test_classifies_mount_worker_and_combat(self):
        self.assertEqual(perfect_pals.classify_role(pal(ride=1500)), "montura")
        self.assertEqual(
            perfect_pals.classify_role(
                pal(work={"Handcraft": 4}, ride=1500),
                "Rideable",
                {"SkillUnlock_Rideable": {"description": "Permite montar este Pal"}},
            ),
            "montura",
        )
        self.assertEqual(
            perfect_pals.classify_role(pal(work={"Handcraft": 4, "Mining": 3})),
            "trabajo",
        )
        self.assertEqual(perfect_pals.classify_role(pal()), "combate")

    def test_selects_four_safe_role_passives(self):
        catalog = self.catalog()
        mount = catalog["pals"]["Mount"]
        selected = perfect_pals.select_passives(
            mount, catalog["passive_skills"], perfect_pals.classify_role(mount)
        )
        self.assertEqual(len(selected), 4)
        self.assertEqual(selected[0], "MoveBest")
        self.assertNotIn("BadPenalty", selected)

    def test_generates_valid_male_and_female_packages(self):
        result = perfect_pals.generate_profile_packages(self.catalog(), refresh=False)
        self.assertEqual(result["species"], 5)
        self.assertEqual(result["profiles"], 10)
        generation = perfect_pals.GENERATIONS_DIR / result["generation_id"]
        with zipfile.ZipFile(generation / "Dimitry_Perfectos_Machos.zip") as archive:
            self.assertEqual(len(archive.namelist()), 5)
            profiles = [json.loads(archive.read(name)) for name in archive.namelist()]
        self.assertTrue(all(item["pal_preset"]["gender"] == "Male" for item in profiles))
        self.assertTrue(all(item["pal_preset"]["level"] == 1 for item in profiles))
        self.assertTrue(all(len(item["pal_preset"]["passive_skills"]) == 4 for item in profiles))
        ids = {item["pal_preset"]["character_id"] for item in profiles}
        self.assertIn("PlantSlime_Flower", ids)
        self.assertNotIn("PlantSlime", ids)
        self.assertNotIn("Astralym", ids)
        panthalus = next(item for item in profiles if item["pal_preset"]["character_id"] == "KingWhale")
        self.assertTrue(panthalus["pal_preset"]["is_boss"])

    def test_detects_only_species_added_after_baseline(self):
        first = perfect_pals.generate_profile_packages(self.catalog(), refresh=False)
        self.assertEqual(first["new_species"], 0)
        updated = self.catalog()
        updated["pals"]["NewPal"] = pal(deck=300)
        updated["pals_es"]["NewPal"] = {"localized_name": "Pal nuevo"}
        second = perfect_pals.generate_profile_packages(updated, refresh=False)
        self.assertEqual(second["new_species"], 1)
        self.assertEqual(second["new_species_ids"], ["NewPal"])
        generation = perfect_pals.GENERATIONS_DIR / second["generation_id"]
        with zipfile.ZipFile(generation / "Dimitry_Perfectos_Solo_Nuevos.zip") as archive:
            self.assertEqual(len(archive.namelist()), 2)

    def test_download_rejects_invalid_generation(self):
        with self.assertRaises(ValueError):
            perfect_pals.generation_download("../../peligroso", "male")

    def test_live_catalog_answers_item_names_without_ai(self):
        perfect_pals.ensure_perfect_dirs()
        perfect_pals._write_json(
            perfect_pals.CATALOG_DIR / "items_es.json",
            {
                "ElectricOrgan": {
                    "localized_name": "Órgano eléctrico",
                    "description": "Material obtenido de Pals de tipo Rayo.",
                }
            },
        )
        perfect_pals._write_json(
            perfect_pals.CATALOG_META_FILE,
            {"synced_at": "2026-08-01T00:00:00Z"},
        )
        results = perfect_pals.search_live_catalog("¿Cómo consigo un organo electrico?")
        self.assertEqual(results[0]["source_key"], "catalog-items_es-ElectricOrgan")
        self.assertIn("Pals de tipo Rayo", results[0]["snippet"])


if __name__ == "__main__":
    unittest.main()
