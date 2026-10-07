"""Game classification, explicit choices, caching and rename regression tests."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import pythoncom

from game_library import GameLibrary, ShortcutMetadata, is_game
from scanner import DesktopItem, DesktopUnavailableError, canonical_identity, item_from_path


def entry(title="Game", extension=".lnk"):
    path = "C:\\Desktop\\" + title + extension
    return DesktopItem(canonical_identity(path), title, path, path, "shortcut", fingerprint="v1")


class GameClassifierTests(unittest.TestCase):
    def test_game_urls_require_a_launch_protocol(self):
        for url in [
            "steam://rungameid/292030",
            "steam://run/292030",
            "com.epicgames.launcher://apps/abc?action=launch&silent=true",
            "uplay://launch/1234/0",
            "origin://launchgame/offer123",
        ]:
            with self.subTest(url=url):
                self.assertTrue(is_game(entry(extension=".url"), ShortcutMetadata(url=url)))
        for url in [
            "https://store.steampowered.com/app/292030/",
            "https://example.com/my-game",
            "com.epicgames.launcher://apps/abc?action=show",
            "steam://rungameid/431960",
            "",
        ]:
            with self.subTest(url=url):
                self.assertFalse(is_game(entry(extension=".url"), ShortcutMetadata(url=url)))

    def test_platform_clients_require_game_arguments(self):
        self.assertTrue(is_game(entry("Heroes III"), ShortcutMetadata(
            target=r"D:\GOG Galaxy\GalaxyClient.exe",
            arguments='/command=runGame /gameId=1718139055 /path="D:\\Games\\Heroes III"',
        )))
        self.assertTrue(is_game(entry(), ShortcutMetadata(
            target=r"C:\Steam\steam.exe", arguments="-applaunch 1234 -fullscreen"
        )))
        for executable in ("steam.exe", "GalaxyClient.exe", "EpicGamesLauncher.exe",
                           "UbisoftConnect.exe", "Battle.net.exe"):
            with self.subTest(executable=executable):
                self.assertFalse(is_game(entry(), ShortcutMetadata(target="C:\\Games\\" + executable)))

    def test_tools_installers_editors_and_self_are_excluded(self):
        cases = [
            ("Roblox Studio", r"C:\Roblox\RobloxStudioBeta.exe"),
            ("Wallpaper Engine", r"C:\Steam\steamapps\common\wallpaper_engine\wallpaper64.exe"),
            ("Other title", r"D:\Games\My Game\setup.exe"),
            ("GameShift-Setup-0.6.8-win-x64", r"C:\Desktop\GameShift-Setup-0.6.8-win-x64.exe"),
            ("Force Shutdown Timer", r"C:\Games\ForceShutdownTimer.exe"),
            ("Gry", r"C:\Windows\wscript.exe"),
            ("Ghoul Cyber - Theme Studio", r"C:\Python314\pythonw.exe"),
            ("Renamed editor", r"D:\Games\UnrealEditor.exe"),
        ]
        for title, target in cases:
            with self.subTest(title=title):
                self.assertFalse(is_game(entry(title), ShortcutMetadata(target=target)))
        self.assertFalse(is_game(entry("Renamed"), ShortcutMetadata(
            target=r"C:\Windows\wscript.exe", arguments='"C:\\Users\\erykb\\GameDock\\GameDock.vbs"'
        )))
        self.assertFalse(is_game(entry("Wallpaper Engine"), ShortcutMetadata(url="steam://rungameid/431960")))

    def test_actual_player_and_installation_evidence_without_name_rewriting(self):
        for title, target in [
            ("Minecraft", r"C:\Program Files\sklauncher\sklauncher.exe"),
            ("Roblox Player", r"C:\Roblox\RobloxPlayerBeta.exe"),
            ("Unusual title – version 2", r"D:\Games\Some Game\game.exe"),
            ("Other title", r"C:\Steam\steamapps\common\SomeGame\game.exe"),
            ("Unreal shipping game", r"D:\Other\game-Win64-Shipping.exe"),
        ]:
            with self.subTest(title=title):
                self.assertTrue(is_game(entry(title), ShortcutMetadata(target=target)))
        self.assertFalse(is_game(entry("Unknown utility"), ShortcutMetadata(target=r"C:\Tools\utility.exe")))
        self.assertFalse(is_game(entry("SKLauncher"), ShortcutMetadata(target=r"C:\Tools\sklauncher.exe")))

    def test_godot_runtime_requires_main_scene_and_rejects_editor(self):
        with tempfile.TemporaryDirectory(prefix="gamedock-godot-") as temporary:
            project = Path(temporary)
            (project / "project.godot").write_text(
                '[application]\nconfig/name="Living Arsenal"\nrun/main_scene="res://main.tscn"\n',
                encoding="utf8",
            )
            metadata = ShortcutMetadata(
                target=r"C:\Downloads\Godot_v4.7.2-stable_win64.exe",
                arguments=f'--path "{temporary}"', working_directory=temporary,
            )
            self.assertTrue(is_game(entry("Living Arsenal"), metadata))
            self.assertFalse(is_game(entry("Living Arsenal"), replace(metadata, arguments=metadata.arguments + " --editor")))
            (project / "project.godot").write_text("[application]\n", encoding="utf8")
            self.assertFalse(is_game(entry("Living Arsenal"), metadata))


class GameLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="gamedock-library-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.library = GameLibrary(str(self.root))
        self.inventory = Mock()
        self.library.scanner = self.inventory

    def test_metadata_cached_by_identity_and_fingerprint(self):
        item = entry()
        self.inventory.scan.return_value = [item]
        with patch("game_library._read_metadata", side_effect=[
            ShortcutMetadata(url="https://example.com"),
            ShortcutMetadata(url="steam://rungameid/1234"),
        ]) as read:
            self.assertEqual(self.library.scan(), [])
            self.assertEqual(self.library.scan(), [])
            self.assertEqual(read.call_count, 1)
            changed = replace(item, fingerprint="v2")
            self.inventory.scan.return_value = [changed]
            self.assertEqual(self.library.scan(), [changed])
            self.assertEqual(read.call_count, 2)

    def test_transient_metadata_failure_retries_and_retains_previous_recognition(self):
        item = entry()
        self.inventory.scan.return_value = [item]
        with patch("game_library._read_metadata", side_effect=[
            OSError("temporary lock"), ShortcutMetadata(url="steam://rungameid/1234"),
            OSError("temporary lock"), ShortcutMetadata(url="https://example.com"),
        ]) as read:
            self.assertEqual(self.library.scan(), [])
            self.assertEqual(self.library.uncertain_items, ["Game"])
            self.assertEqual(self.library.scan(), [item])
            changed = replace(item, fingerprint="v2")
            self.inventory.scan.return_value = [changed]
            self.assertEqual(self.library.scan(), [changed])
            self.assertEqual(self.library.scan(), [])
            self.assertEqual(read.call_count, 4)

    def test_replaced_file_at_same_path_does_not_reuse_old_metadata(self):
        before = replace(entry(), file_identity="old file")
        after = replace(before, file_identity="replacement file")
        with patch("game_library._read_metadata", side_effect=[
            ShortcutMetadata(url="steam://rungameid/1234"),
            ShortcutMetadata(url="https://example.com"),
        ]) as read:
            self.inventory.scan.return_value = [before]
            self.assertEqual(self.library.scan(), [before])
            self.inventory.scan.return_value = [after]
            self.assertEqual(self.library.scan(), [])
            self.assertEqual(read.call_count, 2)

    def test_manual_original_files_and_boolean_overrides_take_precedence(self):
        app = self.root / "ordinary utility.exe"
        app.write_bytes(b"fixture")
        original = item_from_path(str(app))
        self.inventory.scan.return_value = []
        with patch("game_library._read_metadata") as read:
            games = self.library.scan(game_paths=[str(app)])
            self.assertEqual(len(games), 1)
            self.assertEqual(games[0].path, str(app))
            self.assertEqual(games[0].source, "manual")
            self.assertEqual(self.library.scan(game_paths=[str(app)], game_overrides={original.id: False}), [])
            read.assert_not_called()
        self.inventory.scan.return_value = [original]
        with patch("game_library._read_metadata") as read:
            self.assertEqual(self.library.scan(game_overrides={original.id: True}), [original])
            read.assert_not_called()
        document = self.root / "not-a-game.txt"
        document.write_text("fixture")
        self.assertEqual(self.library.scan(game_paths=[str(document), str(self.root / "missing.exe")],
                                           game_overrides={original.id: False}), [])

    def test_manual_selection_and_exclusion_survive_unique_external_rename(self):
        old_path = self.root / "original.exe"
        old_path.write_bytes(b"fixture")
        old = item_from_path(str(old_path))
        self.inventory.scan.return_value = [old]
        self.assertEqual(self.library.scan(game_paths=[str(old_path)]), [old])
        new_path = self.root / "renamed.exe"
        old_path.rename(new_path)
        new = item_from_path(str(new_path))
        self.inventory.scan.return_value = [new]
        self.assertEqual(self.library.scan(game_paths=[str(old_path)]), [new])
        self.assertEqual(self.library.last_renames, {old.id: new.id})
        # The effective old-ID override is honored immediately, before the GUI
        # has persisted the rename and refreshed worker preferences.
        self.assertEqual(self.library.scan(game_paths=[str(old_path)], game_overrides={old.id: False}), [])
        self.assertEqual(self.library.last_renames, {})
        fresh = GameLibrary(str(self.root))
        fresh.scanner = self.inventory
        self.assertEqual(fresh.scan(game_paths=[str(new_path)], game_overrides={new.id: False}), [])

    def test_renamed_excluded_automatic_game_stays_excluded_in_same_snapshot(self):
        old = replace(entry(), file_identity="unique", fingerprint="v1")
        new = replace(entry("Renamed"), file_identity="unique", fingerprint="v2")
        with patch("game_library._read_metadata", return_value=ShortcutMetadata(url="steam://rungameid/1234")):
            self.inventory.scan.return_value = [old]
            self.assertEqual(self.library.scan(game_overrides={old.id: False}), [])
            self.inventory.scan.return_value = [new]
            self.assertEqual(self.library.scan(game_overrides={old.id: False}), [])
            self.assertEqual(self.library.last_renames, {old.id: new.id})

    def test_full_inventory_failure_propagates_and_collection_flag_is_preserved(self):
        self.inventory.scan.side_effect = DesktopUnavailableError("Explorer unavailable")
        with self.assertRaises(DesktopUnavailableError):
            self.library.scan(include_extra=True)
        self.inventory.scan.assert_called_once_with(include_extra=True)

    def test_utf16_url_and_percent_characters_are_parsed(self):
        source = self.root / "encoded.url"
        source.write_text(
            "[InternetShortcut]\nURL=com.epicgames.launcher://apps/abc?action=launch&name=100%25\n",
            encoding="utf16",
        )
        item = item_from_path(str(source))
        self.inventory.scan.return_value = [item]
        self.assertEqual(self.library.scan(), [item])

    def test_website_shortcuts_work_for_automatic_and_manual_selection(self):
        source = self.root / "game.website"
        source.write_text("[InternetShortcut]\nURL=steam://rungameid/292030\n", encoding="utf8")
        item = item_from_path(str(source))
        self.inventory.scan.return_value = [item]
        self.assertEqual(self.library.scan(), [item])
        self.inventory.scan.return_value = []
        self.assertEqual(self.library.scan(game_paths=[str(source)])[0].path, str(source))

    def test_rename_back_and_reused_path_do_not_redirect_current_choices(self):
        first = replace(entry("A"), file_identity="original")
        second = replace(entry("B"), file_identity="original")
        with patch("game_library._read_metadata", return_value=ShortcutMetadata(url="https://example.com")):
            self.inventory.scan.return_value = [first]
            self.library.scan(game_overrides={first.id: True})
            self.inventory.scan.return_value = [second]
            self.assertEqual(self.library.scan(game_overrides={first.id: True}), [second])
            self.inventory.scan.return_value = [first]
            self.assertEqual(self.library.scan(game_overrides={first.id: True}), [first])
            self.assertEqual(self.library._redirect(second.id), first.id)
            self.assertNotIn(first.id, self.library.id_mapping)
            self.inventory.scan.return_value = [second]
            self.library.scan()
            reused = replace(first, file_identity="new occupant", fingerprint="v3")
            self.inventory.scan.return_value = [second, reused]
            self.assertEqual(self.library.scan(game_overrides={first.id: True}), [reused])
            self.assertNotIn(first.id, self.library.id_mapping)


class ActualGameLibraryTests(unittest.TestCase):
    def test_actual_visible_desktop_games_and_exclusions(self):
        pythoncom.CoInitialize()
        try:
            library = GameLibrary(str(Path(__file__).resolve().parents[1]))
            games = library.scan()
            names = {item.display_name for item in games}
            for excluded in ("Gry", "Roblox Studio", "Force Shutdown Timer", "Dismode",
                             "Wallpaper Engine (bez Steam)", "Ghoul Cyber - Theme Studio"):
                self.assertNotIn(excluded, names)
            self.assertTrue(all(item.path and item.extension in {".lnk", ".url", ".website", ".exe"} for item in games))
            self.assertEqual(len(games), len({item.id for item in games}))
            self.assertEqual(library.uncertain_items, [])
        finally:
            pythoncom.CoUninitialize()


if __name__ == "__main__":
    unittest.main()
