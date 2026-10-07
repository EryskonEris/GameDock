"""Read-only configuration loading and atomic preferences with explicit Windows integration."""
from __future__ import annotations
import copy
import json
import logging
import math
import os
import sys
import tempfile
from pathlib import Path
import winreg

log = logging.getLogger(__name__)
DEFAULT_CONFIG = {
    "schema_version": 3, "dock_x": 1200, "dock_y": 1330,
    "dock_locked": False, "dock_visible": True, "dock_always_on_top": False,
    "auto_hide_on_launch": True, "autostart": True, "favorites": [],
    "hidden_ids": [], "theme": "glass-dark", "desktop_cleaned": False,
    "hub_width": 1600, "hub_height": 860, "view_mode": "grid",
    "sort_mode": "name_asc", "include_shortcuts": False,
    "game_paths": [], "game_overrides": {},
    "glass_opacity": 0.28,
}

class ConfigManager:
    def __init__(self, base_dir: str):
        self.base_dir = os.path.abspath(base_dir)
        self.config_path = os.path.join(self.base_dir, "config.json")
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    def load(self):
        try:
            with open(self.config_path, encoding="utf-8-sig") as stream:
                loaded = json.load(stream)
            if not isinstance(loaded, dict):
                raise ValueError("Konfiguracja musi być obiektem JSON")
            self.config.update(loaded)
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            log.exception("Nie można odczytać ustawień; używam wartości domyślnych")
        for key in ("favorites", "hidden_ids", "game_paths"):
            if not isinstance(self.config.get(key), list):
                self.config[key] = []
        self.config["game_paths"] = [path for path in self.config["game_paths"] if isinstance(path, str)]
        overrides = self.config.get("game_overrides", {})
        self.config["game_overrides"] = {key: value for key, value in overrides.items()
                                         if isinstance(key, str) and isinstance(value, bool)} if isinstance(overrides, dict) else {}
        opacity = self.config.get("glass_opacity", 0.28)
        if isinstance(opacity, bool) or not isinstance(opacity, (int, float)) or not math.isfinite(opacity):
            opacity = 0.28
        self.config["glass_opacity"] = max(0.0, min(1.0, float(opacity)))
        self.config["schema_version"] = 3

    def save(self):
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=self.base_dir)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(self.config, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.config_path)
        finally:
            if temporary and os.path.exists(temporary):
                os.remove(temporary)

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.set_many({key: value})

    def set_many(self, values):
        previous = copy.deepcopy(self.config)
        self.config.update(values)
        try:
            self.save()
        except OSError:
            self.config = previous
            raise

    def set_autostart(self, enable: bool, *, save: bool = True) -> bool:
        """Use one HKCU Run entry; remove only our verified legacy shortcut."""
        launcher = os.path.join(self.base_dir, "GameDock.vbs")
        wscript = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "wscript.exe")
        executable = os.path.join(self.base_dir, "GameDock.exe")
        if getattr(sys, "frozen", False):
            command = f'"{executable}" --background'
        else:
            command = f'"{wscript}" "{launcher}" --background'
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                   r"Software\Microsoft\Windows\CurrentVersion\Run",
                                   0, winreg.KEY_SET_VALUE) as key:
                if enable:
                    winreg.SetValueEx(key, "GameDock", 0, winreg.REG_SZ, command)
                else:
                    try:
                        winreg.DeleteValue(key, "GameDock")
                    except FileNotFoundError:
                        pass
            self._remove_owned_legacy_startup_shortcut()
            if save:
                self.set("autostart", bool(enable))
            return True
        except Exception as error:
            log.warning("Nie można zmienić autostartu: %s", error)
            return False

    def reconcile_autostart(self):
        return self.set_autostart(bool(self.get("autostart", True)), save=False)

    def _remove_owned_legacy_startup_shortcut(self):
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            windows_shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = Path(windows_shell.SpecialFolders("Startup")) / "GameDock.lnk"
            if shortcut.is_file():
                link = windows_shell.CreateShortcut(str(shortcut))
                launcher = str(Path(self.base_dir) / "GameDock.vbs").casefold()
                if launcher in str(link.Arguments).casefold():
                    shortcut.unlink()
        finally:
            pythoncom.CoUninitialize()
