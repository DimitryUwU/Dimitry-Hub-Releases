from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class AIAndSyncTests(unittest.TestCase):
    def test_embedded_ai_keeps_assistant_available_without_credentials(self):
        from app.ai import AIError
        from app.main import AIChatRequest, ai_chat, ai_status

        status = ai_status()
        self.assertTrue(status["providers"]["local"]["configured"])
        self.assertTrue(
            status["providers"].get("compatible", {}).get("embedded")
            or status["providers"].get("ollama", {}).get("online")
        )
        with patch("app.main.ai_generate", side_effect=AIError("sin proveedor externo")):
            result = ai_chat(AIChatRequest(message="Hola, ¿qué puedes hacer?", domain="general"))
        self.assertEqual("local", result["provider"])
        self.assertIn("motor local integrado", result["response"])
        self.assertGreater(result["thread_id"], 0)

    def test_openai_response_text_and_sources(self):
        from app.ai import _extract_openai_text

        text, sources = _extract_openai_text(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Respuesta verificada.",
                                "annotations": [
                                    {"type": "url_citation", "url": "https://example.com/a", "title": "Fuente A"}
                                ],
                            }
                        ],
                    },
                    {
                        "type": "web_search_call",
                        "action": {
                            "sources": [
                                {"url": "https://example.com/a", "title": "Duplicada"},
                                {"url": "https://example.com/b", "title": "Fuente B"},
                            ]
                        },
                    },
                ]
            }
        )
        self.assertEqual(text, "Respuesta verificada.")
        self.assertEqual([item["url"] for item in sources], ["https://example.com/a", "https://example.com/b"])

    def test_top_openai_payload_supports_max_and_pro(self):
        from app.ai import _openai_generate

        captured = {}

        def fake_request(url, *, payload=None, headers=None, timeout=0):
            captured.update({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
            return {"output_text": "Hecho", "usage": {"total_tokens": 10}}

        with patch("app.ai.get_secret", return_value="sk-test"), patch("app.ai._request_json", side_effect=fake_request):
            result = _openai_generate(
                [{"role": "user", "content": "Analiza"}],
                system="Sé riguroso",
                model="gpt-5.6",
                timeout=30,
                allow_web=True,
                allowed_domains=["gob.pe"],
                cfg={"ai_reasoning_effort": "max", "ai_pro_mode": "1"},
            )

        self.assertEqual(result.text, "Hecho")
        self.assertEqual(captured["payload"]["reasoning"], {"effort": "max", "mode": "pro"})
        self.assertEqual(captured["payload"]["tools"][0]["filters"]["allowed_domains"], ["gob.pe"])

    def test_provider_order_prefers_cloud_then_local_fallback(self):
        from app.ai import _provider_order

        cfg = {"ai_mode": "automatic", "ai_provider": "automatic", "ai_fallback_local": "1"}
        self.assertEqual(_provider_order(cfg), ["openai", "compatible", "ollama"])
        self.assertEqual(_provider_order({**cfg, "ai_provider": "openai"}), ["openai", "ollama"])
        self.assertEqual(_provider_order({**cfg, "ai_mode": "local"}), ["ollama"])

    def test_ollama_off_disables_visible_reasoning(self):
        from app.ai import _ollama_generate

        captured = {}

        def fake_request(url, *, payload=None, headers=None, timeout=0):
            captured.update({"url": url, "payload": payload, "timeout": timeout})
            return {"message": {"content": "razonamiento interno sin etiqueta inicial</think>\nRespuesta limpia."}}

        with patch("app.ai._request_json", side_effect=fake_request):
            result = _ollama_generate(
                [{"role": "user", "content": "Confirma el estado"}],
                system="Responde en español",
                model="qwen3:4b",
                timeout=30,
                cfg={"ollama_url": "http://127.0.0.1:11434", "ollama_think": "off"},
            )

        self.assertFalse(captured["payload"]["think"])
        self.assertTrue(captured["payload"]["messages"][-1]["content"].startswith("/no_think\n"))
        self.assertEqual(result.text, "Respuesta limpia.")

    def test_secret_store_roundtrip(self):
        import app.secrets as secrets

        with tempfile.TemporaryDirectory() as tmp:
            secret_file = Path(tmp) / "secrets.dat"
            with patch.object(secrets, "SECRET_FILE", secret_file), \
                 patch.object(secrets, "_dpapi_encrypt", secrets._fallback_encrypt), \
                 patch.object(secrets, "_dpapi_decrypt", secrets._fallback_decrypt):
                secrets.set_secret("openai_api_key", "sk-test-123456789")
                self.assertEqual(secrets.get_secret("openai_api_key"), "sk-test-123456789")
                self.assertIn("••••", secrets.masked_secret("openai_api_key"))
                secrets.delete_secret("openai_api_key")
                self.assertEqual(secrets.get_secret("openai_api_key"), "")

    def test_index_keys_are_stable_between_downloaded_versions(self):
        from app.knowledge import index_directory

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "snapshot-a" / "repo" / "data"
            second = base / "snapshot-b" / "repo" / "data"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            content = '{"PAL_ALPHA": {"name": "Alpha"}, "PAL_BETA": {"name": "Beta"}}'
            (first / "pals.json").write_text(content, encoding="utf-8")
            (second / "pals.json").write_text(content, encoding="utf-8")

            keys_first = []
            keys_second = []
            with patch("app.knowledge.upsert_entry", side_effect=lambda **kwargs: keys_first.append(kwargs["source_key"])):
                index_directory(base / "snapshot-a", "palworld", "source")
            with patch("app.knowledge.upsert_entry", side_effect=lambda **kwargs: keys_second.append(kwargs["source_key"])):
                index_directory(base / "snapshot-b", "palworld", "source")

            self.assertEqual(keys_first, keys_second)
            self.assertEqual(keys_first, ["repo/data/pals.json:PAL_ALPHA", "repo/data/pals.json:PAL_BETA"])


if __name__ == "__main__":
    unittest.main()
