"""Metadata-based game library over the exact Explorer desktop inventory.

The original Shell item is retained for launching and file management. Explicit
manual selections/boolean overrides take precedence over automatic recognition.
No store requests or installed-library imports are needed.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import logging
import ntpath
import os
from pathlib import Path
import re
from urllib.parse import unquote

import win32com.client

from scanner import DesktopItem, DesktopScanner, canonical_identity, item_from_path

log = logging.getLogger(__name__)
_SUPPORTED = {".lnk", ".url", ".website", ".exe"}
# Verified locally in appmanifest_431960.acf: this is desktop software.
_STEAM_SOFTWARE_IDS = {"431960"}
_TOOL_NAMES = re.compile(
    r"\b(?:roblox studio|wallpaper engine|theme studio|force shutdown|"
    r"msi afterburner|nvidia app|asus driverhub|samsung magician|"
    r"unreal editor|godot editor|blender|photoshop|everything|ntlite|dismode)\b",
    re.IGNORECASE,
)
_TOOL_EXECUTABLES = {
    "robloxstudiobeta.exe", "wallpaper32.exe", "wallpaper64.exe",
    "forceshutdowntimer.exe", "dismode.exe", "blender.exe", "photoshop.exe",
    "unrealeditor.exe", "everything.exe", "msiafterburner.exe",
    "nvidia app.exe", "samsungmagician.exe", "ntlite.exe",
}
_GENERIC_LAUNCHERS = {
    "steam.exe", "galaxyclient.exe", "epicgameslauncher.exe",
    "ubisoftconnect.exe", "upc.exe", "origin.exe", "eadesktop.exe",
    "battle.net.exe", "battlenet.exe", "riotclientservices.exe",
}
_SELF_NAMES = {"gry", "gamedock", "centrum gier"}
_INSTALLER = re.compile(
    r"(?:^|[\s_.-])(?:setup|installer|install|uninstall|unins\d*|updater)(?:$|[\s_.-])"
    r"|(?:setup|install|unins\d*|installer|uninstaller|updater)\.exe$",
    re.IGNORECASE,
)
_GAME_DIRECTORIES = (
    "\\steamapps\\common\\", "\\gog galaxy\\games\\", "\\gog games\\",
    "\\epic games\\", "\\xboxgames\\", "\\origin games\\", "\\ea games\\",
    "\\ubisoft game launcher\\games\\", "\\games\\", "\\gry\\",
)


@dataclass(frozen=True, slots=True)
class ShortcutMetadata:
    target: str = ""
    arguments: str = ""
    working_directory: str = ""
    url: str = ""


def _read_url(path: str) -> str:
    data = Path(path).read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16")
    elif b"\x00" in data[:128]:
        text = data.decode("utf-16-le")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("mbcs", errors="replace")
    # Parse only the InternetShortcut section, without percent interpolation.
    inside = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            inside = line.casefold() == "[internetshortcut]"
        elif inside and "=" in line:
            key, value = line.split("=", 1)
            if key.strip().casefold() == "url":
                return value.strip()
    return ""


def _read_metadata(item: DesktopItem) -> ShortcutMetadata:
    extension = item.extension
    if extension in {".url", ".website"}:
        return ShortcutMetadata(url=_read_url(item.path))
    if extension == ".lnk":
        windows_shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = windows_shell.CreateShortcut(item.path)
        return ShortcutMetadata(
            target=str(shortcut.TargetPath or ""),
            arguments=str(shortcut.Arguments or ""),
            working_directory=str(shortcut.WorkingDirectory or ""),
        )
    return ShortcutMetadata(target=item.path or "")


def _steam_app_id(text: str) -> str | None:
    match = re.search(r"steam://(?:rungameid|run)/(\d+)", text, re.IGNORECASE)
    if not match:
        match = re.search(r"(?:^|\s)-applaunch\s+(\d+)(?:\s|$)", text, re.IGNORECASE)
    return match.group(1) if match else None


def _game_protocol(text: str) -> bool:
    text = unquote(text).casefold()
    steam_id = _steam_app_id(text)
    if steam_id:
        return steam_id not in _STEAM_SOFTWARE_IDS
    if re.search(r"com\.epicgames\.launcher://apps/[^\s?]+(?:\?[^\s]*)?", text):
        return bool(re.search(r"(?:[?&])action=launch(?:[&\s]|$)", text))
    return bool(re.search(
        r"(?:uplay|ubisoftconnect)://launch/\d+"
        r"|(?:origin|ea)://launchgame/[^\s]+"
        r"|origin2://game/launch\?[^\s]*offerids="
        r"|roblox-player:",
        text,
    ))


def _godot_runtime(metadata: ShortcutMetadata) -> bool:
    basename = ntpath.basename(metadata.target).casefold()
    if not basename.startswith("godot") or not basename.endswith(".exe"):
        return False
    arguments = metadata.arguments
    if re.search(r"(?:^|\s)(?:--editor|-e|--project-manager|--import)(?:\s|$)", arguments):
        return False
    match = re.search(r'(?:^|\s)--path(?:=|\s+)(?:"([^"]+)"|([^\s]+))', arguments)
    directory = (match.group(1) or match.group(2)) if match else metadata.working_directory
    if not directory:
        return False
    try:
        project = Path(os.path.expandvars(directory)) / "project.godot"
        return bool(re.search(r'(?m)^\s*run/main_scene\s*=\s*"[^"]+"', project.read_text(encoding="utf-8-sig")))
    except (OSError, UnicodeError):
        return False


def is_game(item: DesktopItem, metadata: ShortcutMetadata) -> bool:
    """Recognize a launch intent, never infer a game from .url/.lnk alone."""
    if not item.path or item.kind in {"system", "folder"} or item.extension not in _SUPPORTED:
        return False
    title = item.display_name.casefold()
    target = os.path.expandvars(metadata.target).replace("/", "\\")
    executable = ntpath.basename(target).casefold()
    arguments = metadata.arguments
    normal_title = re.sub(r"[_-]+", " ", title)
    if title.strip() in _SELF_NAMES:
        return False
    if "gamedock.vbs" in arguments.casefold() or "theme_studio.py" in arguments.casefold():
        return False
    if _TOOL_NAMES.search(normal_title) or executable in _TOOL_EXECUTABLES:
        return False
    if _INSTALLER.search(ntpath.basename(item.path)) or _INSTALLER.search(executable):
        return False
    if _game_protocol(metadata.url + " " + target + " " + arguments):
        return True
    if executable == "galaxyclient.exe":
        return bool(re.search(r"/command[=:]rungame\b", arguments, re.IGNORECASE)
                    and re.search(r"/gameid[=:]\d+\b", arguments, re.IGNORECASE))
    if executable in {"battle.net.exe", "battlenet.exe"}:
        return bool(re.search(r'--exec\s*=\s*"?launch\s+\w+', arguments, re.IGNORECASE))
    if executable in {"robloxplayerbeta.exe", "robloxplayerlauncher.exe"}:
        return True
    if executable in {"minecraftlauncher.exe", "sklauncher.exe", "prismlauncher.exe", "multimc.exe"}:
        return bool(re.search(r"\bminecraft\b", normal_title))
    if executable.startswith("godot"):
        return _godot_runtime(metadata)
    if executable in _GENERIC_LAUNCHERS:
        return False
    # A web page is not a launch shortcut. A direct local game executable must
    # have installation-path evidence (or be explicitly selected by the user).
    if not executable.endswith(".exe"):
        return False
    lowered_path = "\\" + target.casefold().lstrip("\\")
    return any(part in lowered_path for part in _GAME_DIRECTORIES) or bool(
        re.search(r"(?:[-_])win(?:32|64)[-_]shipping\.exe$", executable)
    )


class GameLibrary:
    def __init__(self, base_dir: str):
        self.scanner = DesktopScanner(base_dir)
        self._metadata_cache: dict[str, tuple[tuple[str, str], ShortcutMetadata]] = {}
        self._last_items: list[DesktopItem] = []
        self.id_mapping: dict[str, str] = {}
        self.last_renames: dict[str, str] = {}
        self.uncertain_items: list[str] = []

    def _redirect(self, identity: str) -> str:
        seen = set()
        while identity in self.id_mapping and identity not in seen:
            seen.add(identity)
            identity = self.id_mapping[identity]
        return identity

    def _track_renames(self, items: list[DesktopItem]):
        self.last_renames = {}
        current_ids = {item.id for item in items}
        # A returned/reused path is a current identity again. Old aliases must
        # not redirect explicit choices for its new occupant or form A/B cycles.
        self.id_mapping = {
            old: new for old, new in self.id_mapping.items() if old not in current_ids
        }
        old_counts = Counter(item.file_identity for item in self._last_items if item.file_identity)
        new_counts = Counter(item.file_identity for item in items if item.file_identity)
        new_by_file = {item.file_identity: item.id for item in items if item.file_identity}
        for item in self._last_items:
            identity = item.file_identity
            if identity and old_counts[identity] == new_counts[identity] == 1:
                current = new_by_file[identity]
                if item.id != current:
                    if item.id not in current_ids:
                        self.id_mapping[item.id] = current
                    self.last_renames[item.id] = current
        self._last_items = list(items)

    def _metadata(self, item: DesktopItem) -> ShortcutMetadata | None:
        cached = self._metadata_cache.get(item.id)
        metadata_key = (item.fingerprint, item.file_identity)
        if cached and cached[0] == metadata_key:
            return cached[1]
        try:
            metadata = _read_metadata(item)
        except Exception:
            self.uncertain_items.append(item.display_name)
            log.debug("Nie można odczytać skrótu %s", item.display_name, exc_info=True)
            # Retry on the next snapshot; retain an already recognized item
            # across transient sharing/COM failures instead of losing the game.
            return cached[1] if cached and cached[0][1] == item.file_identity else None
        self._metadata_cache[item.id] = (metadata_key, metadata)
        return metadata

    def scan(self, include_extra=False, game_paths=(), game_overrides=None) -> list[DesktopItem]:
        items = self.scanner.scan(include_extra=include_extra)
        self._track_renames(items)
        self.uncertain_items = []
        existing = {item.id for item in items}
        manual_ids = set()
        for path in game_paths or ():
            if not isinstance(path, str):
                continue
            identity = self._redirect(canonical_identity(path))
            manual_ids.add(identity)
            if identity in existing:
                continue
            if os.path.isfile(path) and os.path.splitext(path)[1].casefold() in _SUPPORTED:
                manual = item_from_path(path, "manual")
                if manual.id not in existing:
                    items.append(manual)
                    existing.add(manual.id)
                manual_ids.add(manual.id)
        overrides = {
            self._redirect(canonical_identity(identity)): included
            for identity, included in (game_overrides or {}).items()
            if isinstance(identity, str) and isinstance(included, bool)
        }
        games = []
        for item in items:
            if not item.path or item.kind in {"system", "folder"} or item.extension not in _SUPPORTED:
                continue
            if item.id in overrides:
                include = overrides[item.id]
            elif item.id in manual_ids:
                include = True
            else:
                metadata = self._metadata(item)
                include = metadata is not None and is_game(item, metadata)
            if include:
                games.append(item)
        alive_ids = {item.id for item in items}
        self._metadata_cache = {
            identity: cached for identity, cached in self._metadata_cache.items() if identity in alive_ids
        }
        return games
