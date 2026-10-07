"""GameDock: a live game library, asynchronous Shell integration and tray."""
from __future__ import annotations

import argparse
import ctypes
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import tempfile
import shutil

import win32api
import win32event
import winerror
from PySide6.QtCore import QObject, QTimer, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

BASE_DIR = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
MUTEX_NAME = "GameDock_SingleInstance_Mutex"
EVENT_NAME = "GameDock_ShowHub_Event"
log = logging.getLogger("gamedock")


def setup_logging():
    directory = BASE_DIR / "logs"
    directory.mkdir(exist_ok=True)
    handler = RotatingFileHandler(directory / "gamedock.log", maxBytes=1_000_000,
                                  backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    def report_exception(kind, value, traceback):
        log.critical("Nieobsłużony błąd aplikacji", exc_info=(kind, value, traceback))
        if QApplication.instance():
            QMessageBox.critical(None, "GameDock", "Wystąpił błąd. Szczegóły zapisano w folderze logs.")
    sys.excepthook = report_exception


def acquire_instance(background=False, preview=False):
    suffix = "_Preview" if preview else ""
    mutex = win32event.CreateMutex(None, False, MUTEX_NAME + suffix)
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        if not background:
            try:
                ctypes.windll.user32.AllowSetForegroundWindow(0xFFFFFFFF)
                event = win32event.OpenEvent(win32event.EVENT_MODIFY_STATE, False, EVENT_NAME + suffix)
                win32event.SetEvent(event)
                win32api.CloseHandle(event)
            except Exception:
                log.exception("Nie można pokazać działającej instancji")
        win32api.CloseHandle(mutex)
        return None, None
    event = win32event.CreateEvent(None, False, False, EVENT_NAME + suffix)
    return mutex, event


def map_renames(previous, current):
    """Rename only when the filesystem identity has a unique old and new match."""
    old_ids = {item.id for item in previous}
    new_ids = {item.id for item in current}
    old_by_file, new_by_file = {}, {}
    for item in previous:
        identity = getattr(item, "file_identity", "")
        if identity and item.id not in new_ids:
            old_by_file.setdefault(identity, []).append(item.id)
    for item in current:
        identity = getattr(item, "file_identity", "")
        if identity and item.id not in old_ids:
            new_by_file.setdefault(identity, []).append(item.id)
    return {values[0]: new_by_file[identity][0]
            for identity, values in old_by_file.items()
            if len(values) == 1 and len(new_by_file.get(identity, [])) == 1}


class GameDockApp(QObject):
    def __init__(self, app, event, *, preview=False, no_dock=False):
        super().__init__()
        from config_mgr import ConfigManager
        from dock_widget import DockWindow
        from hub_window import GameHubWindow
        from tray_icon import TrayIconManager
        from runtime_workers import WorkerSignals, DesktopMonitor, IconLoader, ShellCommands

        self.app, self.show_event_handle = app, event
        self.preview, self.no_dock = preview, no_dock
        self._preview_directory = None
        config_dir = str(BASE_DIR)
        if preview:
            self._preview_directory = tempfile.TemporaryDirectory(prefix="GameDock-preview-")
            config_dir = self._preview_directory.name
            if (BASE_DIR / "config.json").is_file():
                shutil.copy2(BASE_DIR / "config.json", Path(config_dir) / "config.json")
        self.config_mgr = ConfigManager(config_dir)
        self.items = {}
        self._icon_keys = set()
        self.operation_busy = False
        self.launch_pending = False
        self.quit_after_operation = False
        self.shutting_down = False
        self.hub = GameHubWindow(str(BASE_DIR), self.config_mgr)
        self.hub.open_requested.connect(self.open_item)
        self.hub.location_requested.connect(lambda item: self.commands.submit("location", item))
        self.hub.properties_requested.connect(lambda item: self.commands.submit("properties", item))
        self.hub.operation_requested.connect(self.request_operation)
        self.hub.refresh_requested.connect(self.refresh)
        self.hub.favorite_changed.connect(self.favorite_changed)
        self.hub.extra_source_changed.connect(self.extra_source_changed)
        self.hub.settings_changed.connect(self.setting_changed)
        self.hub.game_added.connect(self.add_game)
        self.hub.game_removed.connect(self.remove_game)
        self.hub.games_restored.connect(self.restore_games)

        self.dock = None
        if not no_dock:
            self.dock = DockWindow(str(BASE_DIR), self.config_mgr)
            self.dock.activated.connect(self.toggle_hub)
            self.dock.position_changed.connect(self.save_dock_position)
            self.dock.lock_changed.connect(lambda locked: self.setting_changed("dock_locked", locked))
            self.dock.visibility_requested.connect(lambda visible: self.setting_changed("dock_visible", visible))
            self.dock.refresh_requested.connect(self.refresh)
            self.dock.quit_requested.connect(self.quit_app)
            self.dock.start()

        self.tray = TrayIconManager(str(BASE_DIR))
        self.tray.open_requested.connect(self.show_hub)
        self.tray.dock_requested.connect(self.toggle_dock)
        self.tray.refresh_requested.connect(self.refresh)
        self.tray.quit_requested.connect(self.quit_app)
        self.tray.set_dock_visible(bool(self.config_mgr.get("dock_visible", True)))
        self.tray.start()

        self.signals = WorkerSignals(self)
        self.signals.snapshot.connect(self.apply_snapshot)
        self.signals.library_remapped.connect(self.apply_library_remap)
        self.signals.icon.connect(self.apply_icon)
        self.signals.sync.connect(self.hub.set_sync_status)
        self.signals.opened.connect(self.open_finished)
        self.signals.operation.connect(self.operation_finished)
        self.signals.command_error.connect(self.command_error)
        self.monitor = DesktopMonitor(str(BASE_DIR), self.signals,
                                      bool(self.config_mgr.get("include_shortcuts", False)),
                                      game_paths=self.config_mgr.get("game_paths", []),
                                      game_overrides=self.config_mgr.get("game_overrides", {}))
        self.icons = IconLoader(str(BASE_DIR), self.signals)
        self.commands = ShellCommands(str(BASE_DIR), self.signals)
        self.workers = (self.monitor, self.icons, self.commands)
        for worker in self.workers:
            worker.start()
        self.ipc_timer = QTimer(self)
        self.ipc_timer.setInterval(150)
        self.ipc_timer.timeout.connect(self.check_ipc)
        self.ipc_timer.start()
        if not preview and not self.config_mgr.reconcile_autostart():
            self.hub.set_sync_status("Nie udało się ustawić autostartu. Sprawdź ustawienia.", True)
        self.app.aboutToQuit.connect(self.cleanup)

    @Slot()
    def check_ipc(self):
        if self.show_event_handle and win32event.WaitForSingleObject(self.show_event_handle, 0) == win32event.WAIT_OBJECT_0:
            self.show_hub()

    @Slot()
    def show_hub(self):
        if not self.shutting_down:
            self.hub.show_panel()

    @Slot()
    def toggle_hub(self):
        if self.hub.isVisible():
            if not self.operation_busy:
                self.hub.hide()
        else:
            self.show_hub()

    @Slot()
    def toggle_dock(self):
        self.setting_changed("dock_visible", not bool(self.config_mgr.get("dock_visible", True)))

    @Slot()
    def refresh(self):
        self.monitor.refresh()
        # Manual refresh is also an explicit request to repair cached icons.
        self.icons.invalidate()
        self._icon_keys.clear()
        for item in self.items.values():
            self.icons.submit(item)
            self._icon_keys.add((item.id, item.fingerprint))

    @Slot(object)
    def apply_snapshot(self, items):
        mapping = map_renames(list(self.items.values()), items)
        if mapping:
            self.remap_favorites(mapping)
            self.remap_library(mapping)
        favorites = self.config_mgr.get("favorites", [])
        current_ids = {item.id for item in items}
        if any(favorite not in current_ids for favorite in favorites):
            self.migrate_legacy_favorites(items, favorites)
        self.items = {item.id: item for item in items}
        if hasattr(self.hub, "remap_item_ids") and mapping:
            self.hub.remap_item_ids(mapping)
        self.hub.apply_snapshot(items)
        if self.dock:
            self.dock.set_count(len(items))
        live_keys = {(item.id, item.fingerprint) for item in items}
        self._icon_keys.intersection_update(live_keys)
        for item in items:
            key = (item.id, item.fingerprint)
            if key not in self._icon_keys:
                self._icon_keys.add(key)
                self.icons.submit(item)

    def migrate_legacy_favorites(self, items, favorites):
        """Legacy IDs were hashes of titles. Migrate only unique exact-name matches."""
        import hashlib
        legacy_mapping = {}
        for item in items:
            legacy = hashlib.md5(item.display_name.encode("utf-8")).hexdigest()[:10]
            if legacy in favorites or item.display_name in favorites:
                same = [candidate for candidate in items if candidate.display_name == item.display_name]
                if len(same) == 1:
                    legacy_mapping[legacy] = item.id
                    legacy_mapping[item.display_name] = item.id
        if legacy_mapping:
            self.remap_favorites(legacy_mapping)

    @Slot(str, str, bytes)
    def apply_icon(self, item_id, fingerprint, png):
        current = self.items.get(item_id)
        if current and current.fingerprint == fingerprint:
            self.hub.apply_icon(item_id, png)

    @Slot(object)
    def open_item(self, item):
        if not self.operation_busy and not self.launch_pending:
            self.launch_pending = True
            self.commands.submit("open", item)

    @Slot(object, bool, str)
    def open_finished(self, item, success, message):
        self.launch_pending = False
        if success:
            if self.config_mgr.get("auto_hide_on_launch", True):
                self.hub.hide()
        else:
            self.command_error(f"Nie można otworzyć „{item.display_name}”. {message}")

    @Slot(object)
    def request_operation(self, payload):
        if self.operation_busy:
            return
        from file_operations import OperationRequest
        try:
            request = OperationRequest(action=payload["action"], items=payload.get("items", []),
                                       destination=payload.get("destination"), new_name=payload.get("new_name"),
                                       owner_hwnd=int(self.hub.winId()))
        except Exception as error:
            self.command_error(str(error))
            return
        self.operation_busy = True
        self.hub.set_operation_busy(True)
        self.commands.submit("operation", request)

    @Slot(object)
    def operation_finished(self, result):
        self.operation_busy = False
        self.hub.set_operation_busy(False)
        if result.id_mapping:
            self.remap_favorites(result.id_mapping)
            self.remap_library(result.id_mapping, retain_moved=result.action == "move")
            if hasattr(self.hub, "remap_item_ids"):
                self.hub.remap_item_ids(result.id_mapping)
        self.hub.show_operation_result(result)
        self.monitor.refresh()
        if self.quit_after_operation:
            self.quit_app()

    def remap_favorites(self, mapping):
        favorites = self.config_mgr.get("favorites", [])
        updated = list(dict.fromkeys(mapping.get(item_id, item_id) for item_id in favorites))
        if updated != favorites:
            try:
                self.config_mgr.set("favorites", updated)
                if hasattr(self.hub, "set_favorites"):
                    self.hub.set_favorites(updated)
            except OSError:
                log.exception("Nie można zachować ulubionych po zmianie nazwy")

    @Slot(object)
    def apply_library_remap(self, mapping):
        self.remap_favorites(mapping)
        self.remap_library(mapping)
        self.hub.remap_item_ids(mapping)

    def remap_library(self, mapping, *, retain_moved=False):
        """Keep explicit choices and original manual paths through native renames."""
        from scanner import canonical_identity
        previous_paths = self.config_mgr.get("game_paths", [])
        paths = list(dict.fromkeys(mapping.get(canonical_identity(path), path) for path in previous_paths))
        previous_overrides = self.config_mgr.get("game_overrides", {})
        overrides = {mapping.get(item_id, item_id): value for item_id, value in previous_overrides.items()}
        if retain_moved:
            # A shortcut deliberately moved through this panel remains a game.
            for previous, current in mapping.items():
                if previous in self.items and current not in paths:
                    paths.append(current)
                    overrides[current] = True
        if paths != previous_paths or overrides != previous_overrides:
            try:
                self.config_mgr.set_many({"game_paths": paths, "game_overrides": overrides})
                self.monitor.refresh(game_paths=paths, game_overrides=overrides)
            except OSError:
                log.exception("Nie można zachować ustawień gry po zmianie nazwy")

    @Slot(str)
    def add_game(self, path):
        """Register the selected original; never copy or alter its shortcut."""
        from scanner import canonical_identity
        path = os.path.abspath(path)
        if not os.path.isfile(path) or Path(path).suffix.casefold() not in {".lnk", ".url", ".website", ".exe"}:
            self.command_error("Wybierz istniejący skrót gry lub plik .exe.")
            return
        item_id = canonical_identity(path)
        paths = list(self.config_mgr.get("game_paths", []))
        if not any(canonical_identity(existing) == item_id for existing in paths):
            paths.append(path)
        overrides = dict(self.config_mgr.get("game_overrides", {}))
        overrides[item_id] = True
        try:
            self.config_mgr.set_many({"game_paths": paths, "game_overrides": overrides})
            self.monitor.refresh(game_paths=paths, game_overrides=overrides)
        except OSError:
            self.command_error("Nie udało się dodać gry do listy.")

    @Slot(str)
    def remove_game(self, item_id):
        """Hide a game from this library without touching its files."""
        from scanner import canonical_identity
        paths = [path for path in self.config_mgr.get("game_paths", []) if canonical_identity(path) != item_id]
        overrides = dict(self.config_mgr.get("game_overrides", {}))
        overrides[item_id] = False
        try:
            self.config_mgr.set_many({"game_paths": paths, "game_overrides": overrides})
            self.monitor.refresh(game_paths=paths, game_overrides=overrides)
            self.items.pop(item_id, None)
            self.hub.apply_snapshot(list(self.items.values()))
            if self.dock:
                self.dock.set_count(len(self.items))
        except OSError:
            self.command_error("Nie udało się usunąć gry z listy.")

    @Slot()
    def restore_games(self):
        """Undo library exclusions, including explicitly selected game originals."""
        from scanner import canonical_identity
        paths = list(self.config_mgr.get("game_paths", []))
        existing = {canonical_identity(path) for path in paths}
        overrides = dict(self.config_mgr.get("game_overrides", {}))
        for identity, included in list(overrides.items()):
            if included is False and Path(identity).suffix.casefold() in {".lnk", ".url", ".website", ".exe"}:
                overrides[identity] = True
                if identity not in existing:
                    paths.append(identity)
                    existing.add(identity)
        try:
            self.config_mgr.set_many({"game_paths": paths, "game_overrides": overrides})
            self.monitor.refresh(game_paths=paths, game_overrides=overrides)
        except OSError:
            self.command_error("Nie udało się przywrócić listy gier.")

    @Slot(str, bool)
    def favorite_changed(self, item_id, favorite):
        favorites = list(self.config_mgr.get("favorites", []))
        if favorite and item_id not in favorites:
            favorites.append(item_id)
        elif not favorite:
            favorites = [value for value in favorites if value != item_id]
        self.setting_changed("favorites", favorites)

    @Slot(bool)
    def extra_source_changed(self, enabled):
        self.setting_changed("include_shortcuts", bool(enabled))

    @Slot(int, int)
    def save_dock_position(self, x, y):
        try:
            self.config_mgr.set_many({"dock_x": x, "dock_y": y})
        except OSError:
            self.command_error("Nie udało się zapisać pozycji przycisku.")

    @Slot(str, object)
    def setting_changed(self, key, value):
        try:
            if key == "autostart" and not self.preview:
                if not self.config_mgr.set_autostart(bool(value)):
                    self.command_error("Nie udało się zmienić autostartu.")
                    return
            else:
                self.config_mgr.set(key, value)
            if key == "dock_visible":
                if self.dock:
                    self.dock.set_visible(bool(value))
                self.tray.set_dock_visible(bool(value))
            elif key == "dock_locked" and self.dock:
                self.dock.set_locked(bool(value))
            elif key == "include_shortcuts":
                self.monitor.refresh(include_extra=bool(value))
            if key == "favorites" and self.items:
                if hasattr(self.hub, "set_favorites"):
                    self.hub.set_favorites(value)
                self.hub.apply_snapshot(list(self.items.values()))
        except OSError:
            log.exception("Nie można zapisać ustawienia %s", key)
            self.command_error("Nie udało się zapisać ustawienia.")

    @Slot(str)
    def command_error(self, message):
        self.hub.set_sync_status(message, True)
        self.show_hub()

    @Slot()
    def quit_app(self):
        if self.shutting_down:
            return
        if self.operation_busy:
            self.quit_after_operation = True
            self.hub.set_sync_status("Zamknę GameDock po zakończeniu operacji.", False)
            return
        self.shutting_down = True
        self.app.quit()

    @Slot()
    def cleanup(self):
        self.shutting_down = True
        self.ipc_timer.stop()
        for worker in self.workers:
            worker.stop()
        # Join in a bounded way; do not destroy Qt signal targets while workers run.
        for worker in self.workers:
            worker.join(timeout=2.0)
        self.tray.stop()
        if self.dock:
            self.dock.stop()
        if self._preview_directory:
            self._preview_directory.cleanup()


def main():
    parser = argparse.ArgumentParser(description="GameDock — panel gier")
    parser.add_argument("--background", action="store_true", help="Uruchom bez otwierania panelu")
    parser.add_argument("--verify-package", action="store_true", help="Sprawdź instalację i zapisz raport w logs/sprawdzenie.txt")
    parser.add_argument("--preview", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-dock", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--preview-glass", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.preview_glass:
        args.preview = args.no_dock = True
        # Visual QA only: exercise the material without changing Windows preferences.
        import native_windows
        native_windows.transparency_enabled = lambda: True
    setup_logging()
    if args.verify_package:
        from package_check import run
        return run(BASE_DIR)
    log.info("Start GameDock (preview=%s, background=%s)", args.preview, args.background)
    mutex, event = acquire_instance(args.background, args.preview)
    if mutex is None:
        return 0
    app = QApplication(sys.argv[:1])
    app.setApplicationName("GameDock")
    app.setOrganizationName("GameDock")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(str(BASE_DIR / "assets" / "gamedock.ico")))
    from PySide6.QtGui import QFont
    app.setFont(QFont("Segoe UI", 10))
    app.setStyle("Fusion")
    controller = GameDockApp(app, event, preview=args.preview, no_dock=args.no_dock)
    log.info("Kontroler uruchomiony")
    if not args.background:
        QTimer.singleShot(100, controller.show_hub)
    try:
        return app.exec()
    finally:
        win32api.CloseHandle(event)
        win32api.CloseHandle(mutex)


if __name__ == "__main__":
    raise SystemExit(main())
