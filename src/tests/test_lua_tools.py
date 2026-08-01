from __future__ import annotations

import unittest


class LuaToolsTests(unittest.TestCase):
    def test_decimal_load_wrapper_is_decoded_without_execution(self):
        from app.lua_tools import analyze_lua_source

        payload = 'gg.toast("Listo")\nfunction mainMenu()\n  gg.choice({"Salir"})\nend\n'
        encoded = "".join(f"\\{byte}" for byte in payload.encode("utf-8"))
        result = analyze_lua_source(f'--[[ referencia ]]\nload("{encoded}")()', "muestra.lua")

        self.assertTrue(result["obfuscated_wrapper"])
        self.assertEqual("GameGuardian", result["environment"])
        self.assertIn("mainMenu", result["functions"])
        self.assertEqual(1, result["api_calls"]["toast"])
        self.assertIn("gg.choice", result["preview"])

    def test_analyzer_marks_sensitive_calls(self):
        from app.lua_tools import analyze_lua_source

        result = analyze_lua_source('os.execute("x")\ngg.makeRequest("https://example.invalid")')
        labels = {item["label"] for item in result["findings"]}
        self.assertEqual("alto", result["risk_level"])
        self.assertIn("Ejecución de comandos", labels)
        self.assertIn("Acceso de red", labels)

    def test_generator_creates_readable_reversible_script(self):
        from app.lua_tools import analyze_lua_source, generate_gameguardian_script

        script = generate_gameguardian_script(
            'Prueba "local"',
            "Autor",
            "Comprobación sobre una copia propia",
            "Monedas | 100 | DWORD | 999\nVelocidad | 1.5 | FLOAT | 2.0",
        )

        self.assertIn('local SCRIPT_NAME = "Prueba \\"local\\""', script)
        self.assertIn("local function restore_session()", script)
        self.assertIn("gg.setValues(original_values)", script)
        self.assertIn("pcall(apply_action", script)
        self.assertNotIn("load(", script)
        self.assertNotIn("makeRequest", script)
        result = analyze_lua_source(script)
        self.assertEqual("GameGuardian", result["environment"])
        self.assertEqual("bajo", result["risk_level"])

    def test_generator_rejects_unknown_types_and_non_numeric_values(self):
        from app.lua_tools import generate_gameguardian_script

        with self.assertRaisesRegex(ValueError, "Tipo no permitido"):
            generate_gameguardian_script("Prueba", "", "", "Acción | 1 | TEXTO | 2")
        with self.assertRaisesRegex(ValueError, "debe ser numérico"):
            generate_gameguardian_script("Prueba", "", "", "Acción | 1 | DWORD | dos")

    def test_lua_api_routes_are_registered(self):
        from app.main import app

        paths = {route.path for route in app.routes}
        self.assertIn("/api/gamemod/lua/analyze", paths)
        self.assertIn("/api/gamemod/lua/generate", paths)


if __name__ == "__main__":
    unittest.main()
