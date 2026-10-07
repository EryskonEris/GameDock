import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from config_mgr import ConfigManager, DEFAULT_CONFIG
from main import map_renames


def item(identity, file_identity):
    return SimpleNamespace(id=identity, file_identity=file_identity)


class ConfigurationTests(unittest.TestCase):
    def test_autostart_command_uses_the_installed_runtime_for_each_distribution(self):
        with tempfile.TemporaryDirectory(prefix="GameDock package ") as directory:
            config = ConfigManager(directory)
            for frozen in (False, True):
                with patch("config_mgr.sys.frozen", frozen, create=True), \
                     patch("config_mgr.winreg.CreateKeyEx"), \
                     patch("config_mgr.winreg.SetValueEx") as registry_write, \
                     patch.object(config, "_remove_owned_legacy_startup_shortcut"):
                    self.assertTrue(config.set_autostart(True, save=False))
                command = registry_write.call_args.args[-1]
                if frozen:
                    self.assertEqual(command, f'"{Path(directory) / "GameDock.exe"}" --background')
                else:
                    self.assertIn(f'"{Path(directory) / "GameDock.vbs"}" --background', command)
                    self.assertIn('wscript.exe"', command)

    def test_loading_preserves_file_and_disabled_autostart_without_integration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            original = '{"autostart": false, "favorites": ["zażółć"], "custom": 7}'
            path.write_text(original, encoding="utf-8")
            with patch.object(ConfigManager, "set_autostart") as integration:
                config = ConfigManager(directory)
                integration.assert_not_called()
            self.assertFalse(config.get("autostart"))
            self.assertEqual(config.get("custom"), 7)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_atomic_preferences_roundtrip_and_unknown_fields_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(directory)
            config.set_many({"dock_x": -240, "favorites": ["żółw"], "unknown": "keep"})
            loaded = ConfigManager(directory)
            self.assertEqual(loaded.get("dock_x"), -240)
            self.assertEqual(loaded.get("favorites"), ["żółw"])
            self.assertEqual(loaded.get("unknown"), "keep")
            self.assertEqual(list(Path(directory).glob(".config-*")), [])

    def test_failed_atomic_replace_does_not_corrupt_last_file_or_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(directory)
            config.set("dock_locked", False)
            before = (Path(directory) / "config.json").read_bytes()
            with patch("config_mgr.os.replace", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    config.set("dock_locked", True)
            self.assertFalse(config.get("dock_locked"))
            self.assertEqual((Path(directory) / "config.json").read_bytes(), before)
            self.assertEqual(list(Path(directory).glob(".config-*")), [])

    def test_default_lists_are_not_shared_between_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = ConfigManager(directory), ConfigManager(directory)
            first.config["favorites"].append("first")
            self.assertEqual(second.get("favorites"), [])
            self.assertEqual(DEFAULT_CONFIG["favorites"], [])

    def test_game_library_preferences_validate_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"game_paths": ["C:/Games/Gra.url", 12],
                                        "game_overrides": {"game": True, "tool": False, "bad": "yes"}}), encoding="utf-8")
            config = ConfigManager(directory)
            self.assertEqual(config.get("game_paths"), ["C:/Games/Gra.url"])
            self.assertEqual(config.get("game_overrides"), {"game": True, "tool": False})
            config.save()
            loaded = ConfigManager(directory)
            self.assertEqual(loaded.get("game_overrides"), {"game": True, "tool": False})
            self.assertEqual(loaded.get("schema_version"), 3)

    def test_neutral_glass_opacity_is_bounded_and_invalid_values_use_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for supplied, expected in ((0, 0.0), (0.28, 0.28), (2, 1.0), (-3, 0.0), ("bad", 0.28), (True, 0.28)):
                path.write_text(json.dumps({"glass_opacity": supplied}), encoding="utf-8")
                self.assertEqual(ConfigManager(directory).get("glass_opacity"), expected)


class DesktopRenameTests(unittest.TestCase):
    def test_unique_external_rename_remaps_favorite_identity(self):
        self.assertEqual(map_renames([item("old", "disk:13")], [item("new", "disk:13")]),
                         {"old": "new"})

    def test_same_title_different_files_do_not_merge_and_ambiguous_hardlinks_do_not_remap(self):
        self.assertEqual(map_renames([item("a", "disk:13"), item("b", "disk:13")],
                                    [item("new", "disk:13")]), {})
        self.assertEqual(map_renames([item("a", "disk:13")], [item("new", "disk:14")]), {})

    def test_unchanged_paths_do_not_remap(self):
        self.assertEqual(map_renames([item("a", "disk:13")], [item("a", "disk:13")]), {})


if __name__ == "__main__":
    unittest.main()
