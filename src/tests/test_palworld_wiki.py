from __future__ import annotations

import unittest


class PalworldWikiTests(unittest.TestCase):
    def test_common_questions_prioritize_the_curated_local_guides(self):
        from app.database import init_db
        from app.knowledge import search_knowledge
        from app.palworld_workspace import offline_knowledge_answer

        init_db()
        cases = {
            "¿Cuáles son los mejores Pals iniciales y por qué?": "palworld-1.0-early-pals",
            "Lista completa de las mejores habilidades pasivas de Pals": "palworld-1.0-passives",
            "Coordenadas de las mejores ubicaciones para una base de minería": "palworld-1.0-mining-bases",
            "¿Cómo conseguir aceite de Pal de alta calidad?": "palworld-1.0-high-quality-pal-oil",
            "¿Cómo conseguir un órgano eléctrico?": "palworld-1.0-electric-organ",
            "¿Cómo creo un Pal perfecto macho y hembra manteniendo nivel 1, trabajo predeterminado y cuatro pasivas?": "palworld-perfect-pal-profile",
        }
        for question, expected_key in cases.items():
            with self.subTest(question=question):
                results = search_knowledge("palworld", question, 8)
                self.assertTrue(results)
                self.assertEqual(expected_key, results[0]["source_key"])
                self.assertEqual([expected_key], [item["source_key"] for item in results])
                answer = offline_knowledge_answer(question, results)
                self.assertIn(results[0]["title"], answer)
                self.assertNotIn("Mod Support Improvement", answer)
                if expected_key == "palworld-perfect-pal-profile":
                    self.assertIn("nivel 1", answer.lower())
                    self.assertIn("exactamente cuatro pasivas", answer.lower())
                    self.assertIn("uno macho y otro hembra", answer.lower())
                    self.assertIn("globalpalstorage.sav", answer.lower())
                    self.assertIn("gumoss con flor roja", answer.lower())
                    self.assertIn("panthalus alfa boss_kingwhale", answer.lower())
                    self.assertIn("astralym excluido", answer.lower())


if __name__ == "__main__":
    unittest.main()
