"""Independent STA workers: desktop discovery, slow icons, and user Shell commands."""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import OrderedDict

import pythoncom
from PySide6.QtCore import QObject, Signal

from scanner import DesktopScanner, DesktopUnavailableError
from game_library import GameLibrary
from file_operations import OperationRequest, perform_operation

log = logging.getLogger(__name__)
# A desktop change mid-scan or an Explorer restart usually clears within a
# moment. Retry quietly first; alert the user only when it keeps failing.
QUIET_RETRIES = 2
QUIET_RETRY_DELAY = 0.25


class WorkerSignals(QObject):
    library_remapped = Signal(object)
    snapshot = Signal(object)
    icon = Signal(str, str, bytes)
    sync = Signal(str, bool)
    opened = Signal(object, bool, str)
    operation = Signal(object)
    command_error = Signal(str)


class DesktopMonitor(threading.Thread):
    def __init__(self, base_dir, signals, include_extra=False, *, game_paths=(), game_overrides=None):
        super().__init__(name="GameDock-desktop", daemon=True)
        self.base_dir, self.signals = base_dir, signals
        self.include_extra = include_extra
        self.game_paths = tuple(game_paths or ())
        self.game_overrides = dict(game_overrides or {})
        self.preferences_lock = threading.Lock()
        self.stopping = threading.Event()
        self.wake = threading.Event()

    def refresh(self, include_extra=None, *, game_paths=None, game_overrides=None):
        with self.preferences_lock:
            if include_extra is not None:
                self.include_extra = bool(include_extra)
            if game_paths is not None:
                self.game_paths = tuple(game_paths)
            if game_overrides is not None:
                self.game_overrides = dict(game_overrides)
        self.wake.set()

    def stop(self):
        self.stopping.set()
        self.wake.set()

    def run(self):
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        previous = None
        healthy = None
        failures = 0
        try:
            library = GameLibrary(self.base_dir)
            while not self.stopping.is_set():
                started = time.monotonic()
                self.wake.clear()
                delay = 1.0
                try:
                    with self.preferences_lock:
                        options = {
                            "include_extra": self.include_extra,
                            "game_paths": self.game_paths,
                            "game_overrides": dict(self.game_overrides),
                        }
                    items = library.scan(**options)
                    if self.stopping.is_set():
                        break
                    if library.last_renames:
                        self.signals.library_remapped.emit(dict(library.last_renames))
                    if items != previous:
                        self.signals.snapshot.emit(items)
                        previous = items
                    if healthy is not True:
                        self.signals.sync.emit("Na bieżąco", False)
                    healthy = True
                    failures = 0
                except Exception as error:
                    failures += 1
                    if failures <= QUIET_RETRIES:
                        log.debug("Odczyt biblioteki gier nie powiódł się (próba %d)", failures, exc_info=True)
                        delay = QUIET_RETRY_DELAY
                    elif healthy is not False:
                        if isinstance(error, DesktopUnavailableError):
                            log.warning("Odczyt biblioteki gier chwilowo niedostępny: %s", error)
                        else:
                            log.exception("Odczyt biblioteki gier chwilowo niedostępny")
                        self.signals.sync.emit("Łączę ponownie z biblioteką gier…", True)
                        healthy = False
                pythoncom.PumpWaitingMessages()
                self.wake.wait(max(0.05, delay - (time.monotonic() - started)))
        finally:
            pythoncom.CoUninitialize()


class IconLoader(threading.Thread):
    def __init__(self, base_dir, signals):
        super().__init__(name="GameDock-icons", daemon=True)
        self.base_dir, self.signals = base_dir, signals
        self.stopping = threading.Event()
        self.wake = threading.Event()
        self.pending = OrderedDict()
        self.lock = threading.Lock()
        self.invalidated = threading.Event()

    def submit(self, item):
        with self.lock:
            self.pending[item.id] = item
        self.wake.set()

    def invalidate(self):
        """Drop rendered icons before the next extraction, e.g. on manual refresh."""
        self.invalidated.set()
        self.wake.set()

    def stop(self):
        self.stopping.set()
        self.wake.set()

    def run(self):
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        try:
            scanner = DesktopScanner(self.base_dir)
            while not self.stopping.is_set():
                if self.invalidated.is_set():
                    self.invalidated.clear()
                    scanner.clear_icon_cache()
                with self.lock:
                    if self.pending:
                        _, item = self.pending.popitem(last=False)
                    else:
                        item = None
                        self.wake.clear()
                if item is None:
                    self.wake.wait(0.3)
                    continue
                try:
                    png = scanner.extract_icon(item, size=128)
                    if png and not self.stopping.is_set():
                        self.signals.icon.emit(item.id, item.fingerprint, png)
                except Exception:
                    log.debug("Nie można pobrać ikony %s", item.display_name, exc_info=True)
                pythoncom.PumpWaitingMessages()
        finally:
            pythoncom.CoUninitialize()


class ShellCommands(threading.Thread):
    def __init__(self, base_dir, signals):
        super().__init__(name="GameDock-operations", daemon=True)
        self.base_dir, self.signals = base_dir, signals
        self.commands = queue.Queue()
        self.stopping = threading.Event()

    def submit(self, action, payload):
        self.commands.put((action, payload))

    def stop(self):
        self.stopping.set()
        self.commands.put((None, None))

    def run(self):
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        try:
            scanner = DesktopScanner(self.base_dir)
            while not self.stopping.is_set():
                try:
                    action, payload = self.commands.get(timeout=0.2)
                except queue.Empty:
                    pythoncom.PumpWaitingMessages()
                    continue
                if action is None:
                    break
                try:
                    if action == "open":
                        success = scanner.open_item(payload)
                        self.signals.opened.emit(payload, success,
                                                 "" if success else "Windows nie mógł otworzyć tego elementu.")
                    elif action == "location":
                        if not scanner.open_location(payload):
                            self.signals.command_error.emit("Nie można otworzyć lokalizacji tego elementu.")
                    elif action == "properties":
                        if not scanner.open_item(payload, verb="properties"):
                            self.signals.command_error.emit("Nie można otworzyć właściwości tego elementu.")
                    elif action == "operation":
                        self.signals.operation.emit(perform_operation(payload))
                except Exception as error:
                    log.exception("Polecenie %s nie powiodło się", action)
                    if action == "open":
                        self.signals.opened.emit(payload, False, str(error))
                    elif action == "operation":
                        from file_operations import OperationResult
                        self.signals.operation.emit(OperationResult(
                            action=payload.action, successful_ids=[],
                            errors=[str(error)], cancelled=False, id_mapping={}))
                    else:
                        self.signals.command_error.emit(str(error))
                finally:
                    self.commands.task_done()
                    pythoncom.PumpWaitingMessages()
        finally:
            pythoncom.CoUninitialize()
