import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from runtime_workers import DesktopMonitor, IconLoader, WorkerSignals
from scanner import DesktopUnavailableError


class WorkerLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def wait_for(self, predicate, timeout=2):
        started = time.monotonic()
        while not predicate() and time.monotonic() - started < timeout:
            self.application.processEvents()
            time.sleep(0.005)
        self.application.processEvents()
        self.assertTrue(predicate(), "worker did not produce the expected result")

    def test_snapshot_survives_discovery_error_and_worker_recovers(self):
        first = SimpleNamespace(id="first")
        second = SimpleNamespace(id="second")
        class Scanner:
            count = 0
            last_renames = {}
            def __init__(self, _base): pass
            def scan(self, include_extra=False, game_paths=(), game_overrides=None):
                Scanner.count += 1
                if 2 <= Scanner.count <= 4:
                    raise DesktopUnavailableError("Explorer restarting")
                return [first] if Scanner.count == 1 else [second]
        signals = WorkerSignals()
        snapshots, states = [], []
        signals.snapshot.connect(snapshots.append)
        signals.sync.connect(lambda message, error: states.append(error))
        with patch("runtime_workers.GameLibrary", Scanner), \
                self.assertLogs("runtime_workers", level="WARNING") as logs:
            worker = DesktopMonitor(".", signals)
            worker.start()
            try:
                self.wait_for(lambda: len(snapshots) == 1)
                worker.refresh()
                self.wait_for(lambda: Scanner.count >= 2)
                self.application.processEvents()
                # A single transient failure is retried quietly; the user is not alarmed.
                self.assertNotIn(True, states)
                self.wait_for(lambda: True in states)
                self.assertGreaterEqual(Scanner.count, 4)
                self.assertEqual(snapshots, [[first]])
                self.wait_for(lambda: len(snapshots) == 2)
                self.assertEqual(snapshots[-1], [second])
                self.assertFalse(states[-1])
            finally:
                worker.stop()
                worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
        # An expected desktop hiccup is a warning line, not a stack trace in the log.
        self.assertEqual(len(logs.records), 1)
        self.assertIsNone(logs.records[0].exc_info)
        self.assertNotIn("Traceback", "\n".join(logs.output))

    def test_unexpected_discovery_error_keeps_its_traceback(self):
        class Scanner:
            count = 0
            last_renames = {}
            def __init__(self, _base): pass
            def scan(self, **options):
                Scanner.count += 1
                raise OSError("Explorer restarting")
        signals = WorkerSignals()
        states = []
        signals.sync.connect(lambda message, error: states.append(error))
        with patch("runtime_workers.GameLibrary", Scanner), \
                self.assertLogs("runtime_workers", level="ERROR") as logs:
            worker = DesktopMonitor(".", signals)
            worker.start()
            try:
                self.wait_for(lambda: True in states)
            finally:
                worker.stop()
                worker.join(timeout=2)
        self.assertEqual(len(logs.records), 1)
        self.assertIsNotNone(logs.records[0].exc_info)

    def test_refresh_invalidates_cached_icons_before_the_next_extraction(self):
        entry = SimpleNamespace(id="entry", fingerprint="v1")
        events, received = [], []
        class Scanner:
            def __init__(self, _base): pass
            def clear_icon_cache(self): events.append("clear")
            def extract_icon(self, item, size):
                events.append("extract")
                return b"image"
        signals = WorkerSignals()
        signals.icon.connect(lambda *args: received.append(args))
        with patch("runtime_workers.DesktopScanner", Scanner):
            loader = IconLoader(".", signals)
            loader.start()
            try:
                loader.submit(entry)
                self.wait_for(lambda: len(received) == 1)
                self.assertEqual(events, ["extract"])
                loader.invalidate()
                loader.submit(entry)
                self.wait_for(lambda: len(received) == 2)
                self.assertEqual(events, ["extract", "clear", "extract"])
            finally:
                loader.stop()
                loader.join(timeout=2)
            self.assertFalse(loader.is_alive())

    def test_slow_icon_does_not_delay_new_desktop_snapshot(self):
        extracting = threading.Event()
        entry = SimpleNamespace(id="entry", fingerprint="v1")
        class Scanner:
            count = 0
            last_renames = {}
            def __init__(self, _base): pass
            def scan(self, include_extra=False, game_paths=(), game_overrides=None):
                Scanner.count += 1
                return [Scanner.count]
            def extract_icon(self, _item, size):
                extracting.set()
                time.sleep(0.4)
                return b"image"
        signals = WorkerSignals()
        snapshots = []
        signals.snapshot.connect(snapshots.append)
        with patch("runtime_workers.DesktopScanner", Scanner), patch("runtime_workers.GameLibrary", Scanner):
            monitor, icons = DesktopMonitor(".", signals), IconLoader(".", signals)
            monitor.start()
            icons.start()
            try:
                self.wait_for(lambda: bool(snapshots))
                icons.submit(entry)
                self.wait_for(extracting.is_set)
                previous = len(snapshots)
                monitor.refresh()
                self.wait_for(lambda: len(snapshots) > previous, timeout=0.25)
            finally:
                monitor.stop()
                icons.stop()
                monitor.join(timeout=2)
                icons.join(timeout=2)
            self.assertFalse(monitor.is_alive())
            self.assertFalse(icons.is_alive())

    def test_game_preferences_are_copied_and_remaps_precede_snapshot(self):
        calls, events = [], []
        class Library:
            last_renames = {"old": "new"}
            def __init__(self, _base): pass
            def scan(self, **options):
                calls.append(options)
                return [len(calls)]
        signals = WorkerSignals()
        signals.library_remapped.connect(lambda value: events.append(("remap", value)))
        signals.snapshot.connect(lambda value: events.append(("snapshot", value)))
        selected, overrides = ["C:/old.exe"], {"old": True}
        with patch("runtime_workers.GameLibrary", Library):
            monitor = DesktopMonitor(".", signals, game_paths=selected, game_overrides=overrides)
            selected.clear(); overrides.clear()
            monitor.start()
            try:
                self.wait_for(lambda: len(events) >= 2)
                self.assertEqual(events[0], ("remap", {"old": "new"}))
                self.assertEqual(events[1][0], "snapshot")
                self.assertEqual(calls[0]["game_paths"], ("C:/old.exe",))
                self.assertEqual(calls[0]["game_overrides"], {"old": True})
                monitor.refresh(game_paths=[], game_overrides={"new": False})
                self.wait_for(lambda: len(calls) >= 2)
                self.assertEqual(calls[-1]["game_paths"], ())
                self.assertEqual(calls[-1]["game_overrides"], {"new": False})
            finally:
                monitor.stop()
                monitor.join(timeout=2)
            self.assertFalse(monitor.is_alive())


if __name__ == "__main__":
    unittest.main()
