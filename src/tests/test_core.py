from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class CoreTests(unittest.TestCase):
    def test_health_endpoint_is_registered(self):
        from app.main import app

        self.assertTrue(any(route.path == "/api/health" for route in app.routes))

    def test_perfect_pal_mass_routes_are_registered(self):
        from app.main import app

        paths = {route.path for route in app.routes}
        self.assertIn("/api/palworld/perfect-pals/status", paths)
        self.assertIn("/api/palworld/perfect-pals/generate", paths)
        self.assertIn("/api/palworld/perfect-pals/download/{generation_id}/{file_key}", paths)

    def test_safe_filename(self):
        from app.database import safe_filename
        self.assertEqual(safe_filename("../mi save?.sav"), "mi save.sav")

    def test_atomic_write_and_hash(self):
        from app.database import atomic_write_bytes, sha256_file
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "test.bin"
            atomic_write_bytes(target, b"dimitry")
            self.assertTrue(target.exists())
            self.assertEqual(len(sha256_file(target)), 64)

    def test_text_extraction(self):
        from app.extractors import extract_text
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nota.txt"
            target.write_text("Hola Dimitry", encoding="utf-8")
            self.assertEqual(extract_text(target), "Hola Dimitry")


if __name__ == "__main__":
    unittest.main()
