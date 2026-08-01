from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path


class ExtendedTests(unittest.TestCase):
    def test_zip_path_traversal_is_blocked(self):
        from app.knowledge import safe_extract_zip
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../outside.txt", "bad")
            with self.assertRaises(ValueError):
                safe_extract_zip(archive, Path(tmp) / "out")

    def test_analyzer_recognizes_unity_il2cpp(self):
        from app.analyzer import analyze_bundle
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "global-metadata.dat").write_bytes(b"meta")
            (root / "GameAssembly.dll").write_bytes(b"dll")
            result = analyze_bundle(root)
            self.assertTrue(any("IL2CPP" in item for item in result["classification"]))

    def test_monograph_headings_are_black(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["DIMITRY_HUB_DATA"] = tmp
            # Import in a fresh process would be ideal; XML assertion still validates the style code.
            from app.documents import create_monograph_docx
            path = create_monograph_docx(
                {"title": "Prueba", "author": "Autor"},
                "# Introducción\n\nTexto de prueba.\n\n# Conclusiones\n\nConclusión.",
                "Autor, A. (2026). Obra.",
            )
            with zipfile.ZipFile(path) as zf:
                styles = zf.read("word/styles.xml").decode("utf-8")
            self.assertIn('w:val="000000"', styles)

    def test_monograph_never_exports_internal_instructions(self):
        from app.main import _deterministic_monograph_structure

        structured = _deterministic_monograph_structure(
            "La introducción presenta el tema.\\n\\nEl desarrollo explica los hechos comprobados."
        )
        self.assertIn("# Introducción", structured)
        self.assertNotIn("[Redacte", structured)
        self.assertNotIn("[Incluya", structured)

    def test_peruvian_legal_document_uses_identity_fields(self):
        from app.documents import create_legal_docx

        path = create_legal_docx(
            "solicitud",
            {
                "authority": "SEÑOR DIRECTOR",
                "applicant": "Persona de prueba",
                "dni": "12345678",
                "address": "Abancay",
                "city_date": "Abancay, 1 de agosto de 2026",
            },
            "I. Petitorio\nSolicito atención.\nII. Fundamentos de hecho\n1. Hecho comprobado.",
        )
        with zipfile.ZipFile(path) as zf:
            document = zf.read("word/document.xml").decode("utf-8")
        self.assertIn("Persona de prueba", document)
        self.assertIn("12345678", document)
        self.assertIn("1. Hecho comprobado.", document)
        self.assertNotIn("[DNI PENDIENTE]", document)

    def test_peruvian_minutes_use_a_specific_structure(self):
        from app.documents import create_legal_docx

        path = create_legal_docx(
            "acta_reunion",
            {
                "authority": "Asociación de prueba",
                "applicant": "Persona responsable",
                "address": "Abancay",
                "city_date": "1 de agosto de 2026",
                "time": "10:00 a. m.",
                "sumilla": "Aprobación del plan de trabajo",
                "participants": "Ana Pérez\nLuis Quispe",
            },
            "I. Agenda\nRevisión del plan.\nII. Desarrollo de la reunión\nSe revisaron los hitos.\nIII. Acuerdos\nSe aprobó el plan por unanimidad.",
        )
        with zipfile.ZipFile(path) as zf:
            document = zf.read("word/document.xml").decode("utf-8")
        self.assertIn("ACTA DE REUNIÓN", document)
        self.assertIn("PARTICIPANTES", document)
        self.assertIn("Ana Pérez", document)
        self.assertIn("ACUERDOS", document)
        self.assertIn("Se aprobó el plan por unanimidad.", document)
        self.assertNotIn("POR TANTO", document)


if __name__ == "__main__":
    unittest.main()
