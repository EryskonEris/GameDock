import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
from file_operations import OperationResult
from main import GameDockApp
from scanner import DesktopItem


class FakeTray(QObject):
    open_requested = Signal()
    dock_requested = Signal()
    refresh_requested = Signal()
    quit_requested = Signal()
    def __init__(self, base_dir):
        super().__init__()
        self.visible = None
    def start(self): pass
    def stop(self): pass
    def set_dock_visible(self, value): self.visible = value


class FakeWorker:
    def __init__(self, *args, **kwargs):
        self.submitted, self.refreshes = [], []
        self.invalidated = 0
    def invalidate(self): self.invalidated += 1
    def start(self): pass
    def stop(self): pass
    def join(self, timeout=None): pass
    def submit(self, *args): self.submitted.append(args)
    def refresh(self, *args, **kwargs): self.refreshes.append((args, kwargs))


class AppIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.patches = [patch("tray_icon.TrayIconManager", FakeTray),
                        patch("runtime_workers.DesktopMonitor", FakeWorker),
                        patch("runtime_workers.IconLoader", FakeWorker),
                        patch("runtime_workers.ShellCommands", FakeWorker)]
        for mocked in self.patches:
            mocked.start()
        self.controller = GameDockApp(self.app, None, preview=True, no_dock=True)

    def tearDown(self):
        self.controller.hub.hide()
        self.controller.cleanup()
        self.controller.hub.deleteLater()
        self.controller.deleteLater()
        self.app.processEvents()
        for mocked in reversed(self.patches):
            mocked.stop()

    def test_controller_keeps_qobject_event_callable_and_initializes_signal_children(self):
        self.assertTrue(callable(self.controller.event))
        self.assertIsNotNone(self.controller.signals)
        self.assertTrue(Path(self.controller.config_mgr.config_path).parent.is_dir())

    def test_launch_is_queued_once_and_panel_only_hides_after_success(self):
        entry = DesktopItem("file", "file", "C:/file.txt", "C:/file.txt", "file")
        self.controller.hub.show()
        self.controller.open_item(entry)
        self.controller.open_item(entry)
        self.assertEqual(self.controller.commands.submitted, [("open", entry)])
        self.assertTrue(self.controller.hub.isVisible())
        self.controller.open_finished(entry, True, "")
        self.assertFalse(self.controller.hub.isVisible())
        self.assertFalse(self.controller.launch_pending)

    def test_manual_refresh_rescans_and_re_extracts_every_icon(self):
        entry = DesktopItem("file", "file", "C:/file.txt", "C:/file.txt", "file", fingerprint="a")
        self.controller.apply_snapshot([entry])
        self.controller.apply_snapshot([entry])
        self.assertEqual(self.controller.icons.submitted, [(entry,)])
        self.controller.refresh()
        self.assertEqual(self.controller.icons.invalidated, 1)
        self.assertEqual(self.controller.icons.submitted, [(entry,), (entry,)])
        self.assertTrue(self.controller.monitor.refreshes)

    def test_native_operation_result_reaches_panel_and_preserves_favorite_after_rename(self):
        self.controller.config_mgr.set("favorites", ["old"])
        old = DesktopItem("old", "Old", "C:/old", "C:/old", "file", fingerprint="a")
        new = DesktopItem("new", "New", "C:/new", "C:/new", "file", fingerprint="b")
        self.controller.apply_snapshot([old])
        self.controller.operation_busy = True
        self.controller.operation_finished(OperationResult("rename", ["old"], [], False, {"old": "new"}))
        self.controller.apply_snapshot([new])
        self.assertEqual(self.controller.config_mgr.get("favorites"), ["new"])
        self.assertFalse(self.controller.operation_busy)
        self.assertTrue(self.controller.monitor.refreshes)

    def test_manual_game_registration_is_deduplicated_and_removal_keeps_original(self):
        from scanner import canonical_identity
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "Gra.url"
            original.write_text("[InternetShortcut]\nURL=steam://rungameid/123\n", encoding="utf-8")
            self.controller.hub.game_added.emit(str(original))
            self.controller.hub.game_added.emit(str(original))
            identity = canonical_identity(str(original))
            self.assertEqual(self.controller.config_mgr.get("game_paths"), [str(original)])
            self.assertTrue(self.controller.config_mgr.get("game_overrides")[identity])
            self.controller.hub.game_removed.emit(identity)
            self.assertTrue(original.is_file())
            self.assertEqual(self.controller.config_mgr.get("game_paths"), [])
            self.assertFalse(self.controller.config_mgr.get("game_overrides")[identity])
            self.assertEqual(self.controller.monitor.refreshes[-1][1]["game_overrides"], {identity: False})
            self.assertEqual(self.controller.commands.submitted, [])
            self.controller.hub.games_restored.emit()
            self.assertEqual(self.controller.config_mgr.get("game_paths"), [identity])
            self.assertTrue(self.controller.config_mgr.get("game_overrides")[identity])
            self.assertTrue(original.is_file())

    def test_explicit_game_choices_follow_native_rename(self):
        from scanner import canonical_identity
        old = str(Path("C:/Games/Old.url"))
        new = str(Path("C:/Games/New.url"))
        old_id, new_id = canonical_identity(old), canonical_identity(new)
        self.controller.config_mgr.set_many({"game_paths": [old], "game_overrides": {old_id: True}})
        self.controller.remap_library({old_id: new_id})
        self.assertEqual(self.controller.config_mgr.get("game_paths"), [new_id])
        self.assertEqual(self.controller.config_mgr.get("game_overrides"), {new_id: True})

    def test_moving_detected_shortcut_outside_desktop_keeps_it_in_library(self):
        from scanner import canonical_identity
        old_id = canonical_identity("C:/Desktop/Gra.url")
        new_id = canonical_identity("C:/Games/Gra.url")
        entry = DesktopItem(old_id, "Gra", old_id, old_id, "shortcut")
        self.controller.apply_snapshot([entry])
        self.controller.operation_finished(OperationResult("move", [old_id], [], False, {old_id: new_id}))
        self.assertEqual(self.controller.config_mgr.get("game_paths"), [new_id])
        self.assertEqual(self.controller.config_mgr.get("game_overrides"), {new_id: True})


if __name__ == "__main__":
    unittest.main()
