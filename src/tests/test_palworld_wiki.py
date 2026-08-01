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


if __name__ == "__main__":
    unittest.main()
