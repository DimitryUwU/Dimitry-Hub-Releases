from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path


class V05Tests(unittest.TestCase):
    def test_palworld_news_classifier(self):
        from app.sync_engine import classify_palworld_news
        categories = classify_palworld_news(
            "Major update adds new Pals",
            "New passive skills, items and save data migration fixes are included.",
        )
        self.assertIn("nuevos-pals", categories)
        self.assertIn("pasivas-habilidades", categories)
        self.assertIn("objetos", categories)
        self.assertIn("estructura-save", categories)

    def test_bibliography_audit(self):
        from app.research import bibliography_audit
        result = bibliography_audit(
            "La doctrina sostiene esta idea (Pérez, 2024). También Gómez (2023) coincide.",
            "Pérez, J. (2024). Título del artículo. Revista.\n",
        )
        self.assertIn("Gómez, 2023", result["missing_references"])
        self.assertEqual(result["references"], 1)

    def test_source_quality(self):
        from app.research import source_quality
        self.assertEqual(source_quality("https://www.gob.pe/institucion/minjus")["level"], "oficial")
        self.assertEqual(source_quality("https://api.crossref.org/works")["level"], "académica")

    def test_zip_symlink_is_blocked(self):
        from app.knowledge import safe_extract_zip
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "link.zip"
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (0o120777 << 16)
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr(info, "../../outside")
            with self.assertRaises(ValueError):
                safe_extract_zip(zip_path, Path(tmp) / "out")


if __name__ == "__main__":
    unittest.main()
