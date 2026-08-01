from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app import palworld_workspace as workspace


class PalworldWorkspaceTests(unittest.TestCase):
    def test_detect_save_types(self):
        self.assertEqual(workspace.detect_save_kind("Level.sav"), "level")
        self.assertEqual(workspace.detect_save_kind("GlobalPalStorage.sav"), "global_storage")
        self.assertEqual(workspace.detect_save_kind("A" * 32 + ".sav"), "player")
        self.assertEqual(workspace.detect_save_kind("save.zip"), "save_bundle")
        with self.assertRaises(ValueError):
            workspace.detect_save_kind("foto.png")

    def test_selects_portable_windows_asset(self):
        release = {
            "assets": [
                {"name": "PalworldSavePal_1.2.0_x64-setup.exe", "browser_download_url": "setup", "size": 20},
                {"name": "PalworldSavePal_1.2.0_windows-portable.zip", "browser_download_url": "portable", "size": 10},
                {"name": "PalworldSavePal_1.2.0.AppImage", "browser_download_url": "linux", "size": 30},
            ]
        }
        self.assertEqual(workspace.select_windows_asset(release)["browser_download_url"], "portable")

    def test_session_creates_backup_and_extracts_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_values = (workspace.WORKSPACE_DIR, workspace.SESSIONS_DIR, workspace.TOOLS_DIR, workspace.INCOMING_DIR, workspace.EDITOR_META)
            workspace.WORKSPACE_DIR = root / "workspace"
            workspace.SESSIONS_DIR = workspace.WORKSPACE_DIR / "sessions"
            workspace.TOOLS_DIR = root / "tools"
            workspace.INCOMING_DIR = workspace.WORKSPACE_DIR / "incoming"
            workspace.EDITOR_META = workspace.TOOLS_DIR / "installed.json"
            try:
                incoming = root / "save.zip"
                with zipfile.ZipFile(incoming, "w") as zf:
                    zf.writestr("SaveGames/Level.sav", b"PALWORLD-LEVEL-DATA" * 4)
                    zf.writestr("SaveGames/GlobalPalStorage.sav", b"PALWORLD-GLOBAL-DATA" * 4)
                session = workspace.create_session_from_path(incoming, "save.zip", "save_bundle")
                self.assertTrue(session["backup_created"])
                self.assertEqual({item["kind"] for item in session["files"]}, {"level", "global_storage"})
                stored = workspace.get_session(session["id"])
                self.assertTrue(Path(stored["backup_path"]).exists())
                self.assertTrue(Path(stored["content_path"], "SaveGames", "Level.sav").exists())
            finally:
                (workspace.WORKSPACE_DIR, workspace.SESSIONS_DIR, workspace.TOOLS_DIR, workspace.INCOMING_DIR, workspace.EDITOR_META) = old_values

    def test_offline_answer_is_not_empty(self):
        answer = workspace.offline_knowledge_answer(
            "pasivas",
            [{"title": "PassiveSkills.json", "category": "skills", "snippet": "Legend y Serenity aparecen en la tabla."}],
        )
        self.assertIn("PassiveSkills.json", answer)
        self.assertIn("Legend", answer)

    def test_zip_slip_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "peligroso.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../../Level.sav", b"PALWORLD-DATA" * 4)
            with self.assertRaises(ValueError):
                workspace._safe_extract_saves(archive, root / "salida")

    def test_restore_and_download_working_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_values = (workspace.WORKSPACE_DIR, workspace.SESSIONS_DIR, workspace.TOOLS_DIR, workspace.INCOMING_DIR, workspace.EDITOR_META)
            workspace.WORKSPACE_DIR = root / "workspace"
            workspace.SESSIONS_DIR = workspace.WORKSPACE_DIR / "sessions"
            workspace.TOOLS_DIR = root / "tools"
            workspace.INCOMING_DIR = workspace.WORKSPACE_DIR / "incoming"
            workspace.EDITOR_META = workspace.TOOLS_DIR / "installed.json"
            try:
                incoming = root / "Level.sav"
                original = b"PALWORLD-ORIGINAL" * 4
                incoming.write_bytes(original)
                session = workspace.create_session_from_path(incoming, "Level.sav", "level")
                stored = workspace.get_session(session["id"])
                working = Path(stored["content_path"]) / "Level.sav"
                working.write_bytes(b"PALWORLD-MODIFICADO" * 4)
                self.assertIn(b"MODIFICADO", workspace.session_file(session["id"], "working").read_bytes())
                workspace.restore_session(session["id"])
                self.assertEqual(working.read_bytes(), original)
            finally:
                (workspace.WORKSPACE_DIR, workspace.SESSIONS_DIR, workspace.TOOLS_DIR, workspace.INCOMING_DIR, workspace.EDITOR_META) = old_values

    def test_editor_still_launches_when_explorer_cannot_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_values = (workspace.WORKSPACE_DIR, workspace.SESSIONS_DIR, workspace.TOOLS_DIR, workspace.INCOMING_DIR, workspace.EDITOR_META)
            workspace.WORKSPACE_DIR = root / "workspace"
            workspace.SESSIONS_DIR = workspace.WORKSPACE_DIR / "sessions"
            workspace.TOOLS_DIR = root / "tools"
            workspace.INCOMING_DIR = workspace.WORKSPACE_DIR / "incoming"
            workspace.EDITOR_META = workspace.TOOLS_DIR / "installed.json"
            try:
                executable = workspace.TOOLS_DIR / "v1.2.0" / "psp.exe"
                executable.parent.mkdir(parents=True)
                executable.write_bytes(b"MZ-test")
                workspace._write_json(workspace.EDITOR_META, {"version": "v1.2.0", "executable": str(executable)})
                incoming = root / "Level.sav"
                incoming.write_bytes(b"PALWORLD-DATA" * 4)
                session = workspace.create_session_from_path(incoming, "Level.sav", "level")
                with patch.object(workspace.os, "startfile", side_effect=PermissionError("denegado")), patch.object(workspace, "_wait_for_editor", side_effect=[False, True]), patch.object(workspace.subprocess, "Popen") as popen:
                    result = workspace.launch_editor(session["id"])
                self.assertTrue(result["launched"])
                self.assertFalse(result["folder_opened"])
                self.assertEqual(result["editor_url"], "http://127.0.0.1:5174/")
                popen.assert_called_once()
            finally:
                (workspace.WORKSPACE_DIR, workspace.SESSIONS_DIR, workspace.TOOLS_DIR, workspace.INCOMING_DIR, workspace.EDITOR_META) = old_values


if __name__ == "__main__":
    unittest.main()
