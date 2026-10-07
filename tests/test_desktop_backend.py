"""Behavior tests; native operations touch only unique temporary fixtures."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import io
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import uuid

from PIL import Image, ImageDraw
import pythoncom
import win32com.client
from win32com.shell import shell, shellcon

import scanner
from scanner import DesktopItem, DesktopScanner, DesktopUnavailableError, canonical_identity, item_from_path
import file_operations
from file_operations import OperationRequest, perform_operation


class ScannerBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="gamedock-unit-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.scanner = DesktopScanner(str(self.root))

    def test_inventory_keeps_same_name_files_folders_and_virtual_items(self):
        left, right = self.root / "user", self.root / "public"
        left.mkdir(); right.mkdir()
        for directory in (left, right):
            (directory / "żółć.txt").write_text("contents", encoding="utf8")
        (left / "folder.with.dots").mkdir()
        paths = [str(left / "żółć.txt"), str(right / "żółć.txt"), str(left / "folder.with.dots"),
                 "::{645FF040-5081-101B-9F08-00AA002F954E}"]
        names = ["żółć.txt", "żółć.txt", "folder.with.dots", "Kosz"]
        view = Mock()
        view.ItemCount.return_value = len(paths)
        view.Item.side_effect = lambda index: bytes([index])
        folder = Mock()
        folder.GetDisplayNameOf.side_effect = lambda pidl, flags: (
            paths[pidl[0][0]] if flags == shellcon.SHGDN_FORPARSING else names[pidl[0][0]]
        )
        def attributes(pidls, mask):
            index = pidls[0][0][0]
            attrs = shellcon.SFGAO_FOLDER if index in (2, 3) else 0
            if index != 3:
                attrs |= scanner._ATTRIBUTES & ~shellcon.SFGAO_FOLDER
            return attrs
        folder.GetAttributesOf.side_effect = attributes
        with patch("scanner._desktop_view", return_value=(view, folder)), patch(
            "scanner.shell.SHGetFileInfo", return_value=(1, (0, 32, 0, "", ""))
        ):
            items = self.scanner.scan()
        self.assertEqual([item.kind for item in items], ["file", "file", "folder", "system"])
        self.assertEqual(len({item.id for item in items}), 4)
        self.assertEqual(items[2].display_name, "folder.with.dots")
        self.assertIsNone(items[3].path)
        self.assertFalse(items[3].can_delete)
        self.assertFalse(items[3].can_rename)

    def test_explorer_failure_never_replaces_actual_view_with_directory_guess(self):
        with patch("scanner._desktop_view", side_effect=RuntimeError("Explorer restarting")):
            with self.assertRaises(DesktopUnavailableError):
                self.scanner.scan(include_extra=True)
        self.assertFalse((self.root / "Shortcuts").exists())

    def test_optional_collection_and_snapshot_changed_mid_scan(self):
        shortcuts = self.root / "Shortcuts"
        shortcuts.mkdir()
        (shortcuts / "ordinary.pdf").write_bytes(b"fixture")
        view, folder = Mock(), Mock()
        view.ItemCount.return_value = 0
        with patch("scanner._desktop_view", return_value=(view, folder)):
            self.assertEqual(self.scanner.scan(), [])
            self.assertEqual(self.scanner.scan(True)[0].source, "shortcuts")
        view.ItemCount.side_effect = [0, 1]
        with patch("scanner._desktop_view", return_value=(view, folder)):
            with self.assertRaises(DesktopUnavailableError):
                self.scanner.scan()

    def test_item_vanishing_mid_scan_is_reported_as_a_desktop_change(self):
        view, folder = Mock(), Mock()
        view.ItemCount.side_effect = [2, 1]
        view.Item.side_effect = pythoncom.com_error(-2147483637, "out of range", None, None)
        with patch("scanner._desktop_view", return_value=(view, folder)):
            with self.assertRaises(DesktopUnavailableError) as raised:
                self.scanner.scan()
        self.assertIn("zmienił się", str(raised.exception))
        view.ItemCount.side_effect = [2, 2]
        with patch("scanner._desktop_view", return_value=(view, folder)):
            with self.assertRaises(DesktopUnavailableError) as raised:
                self.scanner.scan()
        self.assertIn("Eksploratora", str(raised.exception))

    def test_failed_icon_extraction_is_not_cached_and_refresh_clears_cached_icons(self):
        item = item_from_path(str(self.root / "missing.lnk"))
        key = (item.id, item.fingerprint, 48)
        with patch("scanner.shell.SHParseDisplayName", side_effect=OSError("no icon")):
            fallback = self.scanner.extract_icon(item, 48)
        self.assertNotIn(key, self.scanner._icon_cache)
        glyph = Image.new("RGBA", (64, 64), (10, 200, 30, 255))
        with patch("scanner.shell.SHParseDisplayName", return_value=(b"pidl", 0)), \
                patch("scanner.shell.SHGetFileInfo", return_value=(0, (0, 7, 0, "", ""))), \
                patch("scanner._large_shell_icon", return_value=12), \
                patch("scanner._icon_to_image", return_value=glyph), \
                patch("scanner.win32gui.DestroyIcon"):
            real = self.scanner.extract_icon(item, 48)
        self.assertNotEqual(fallback, real)
        self.assertIn(key, self.scanner._icon_cache)
        self.scanner.clear_icon_cache()
        self.assertEqual(len(self.scanner._icon_cache), 0)

    def test_external_rename_changes_id_but_preserves_file_identity(self):
        before = self.root / "before.txt"
        before.write_text("data", encoding="utf8")
        original = item_from_path(str(before))
        after = self.root / "after.txt"
        before.rename(after)
        renamed = item_from_path(str(after))
        self.assertNotEqual(original.id, renamed.id)
        self.assertEqual(original.file_identity, renamed.file_identity)
        self.assertTrue(original.file_identity)

    def test_a_link_to_a_folder_remains_a_shortcut(self):
        item = scanner._make_item("Folder link", str(self.root / "folder.lnk"), scanner._ATTRIBUTES, "desktop")
        self.assertEqual(item.kind, "shortcut")
        self.assertEqual(item.extension, ".lnk")

    def test_sparse_jumbo_glyph_and_full_icon_have_equal_visible_size(self):
        sparse = Image.new("RGBA", (256, 256))
        ImageDraw.Draw(sparse).rectangle((3, 7, 34, 38), fill=(32, 120, 240, 255))
        full = Image.new("RGBA", (256, 256), (32, 120, 240, 255))
        sparse_fitted = scanner._fit_icon_image(sparse, 96)
        full_fitted = scanner._fit_icon_image(full, 96)
        self.assertEqual(sparse_fitted.getchannel("A").getbbox(), (0, 0, 96, 96))
        self.assertEqual(sparse_fitted.tobytes(), full_fitted.tobytes())
        portrait = Image.new("RGBA", (256, 256))
        ImageDraw.Draw(portrait).rectangle((3, 7, 18, 38), fill="white")
        fitted = scanner._fit_icon_image(portrait, 96)
        self.assertEqual(fitted.getchannel("A").getbbox(), (24, 0, 72, 96))

    def test_open_uses_pidl_and_location_is_not_a_shell_command(self):
        item = item_from_path(str(self.root / "arguments.lnk"))
        pidl = [b"fixture"]
        with patch("scanner.shell.SHParseDisplayName", return_value=(pidl, 0)), patch(
            "scanner.shell.ShellExecuteEx", return_value={}
        ) as execute, patch("scanner.shell.SHOpenFolderAndSelectItems") as select:
            self.assertTrue(self.scanner.open_item(item, "properties"))
            self.assertEqual(execute.call_args.kwargs["lpIDList"], pidl)
            self.assertEqual(execute.call_args.kwargs["lpVerb"], "properties")
            self.assertTrue(self.scanner.open_location(item))
            select.assert_called_once_with(pidl, [], 0)

    def test_data_records_and_request_are_immutable(self):
        item = DesktopItem("id", "name", "parse", None, "system")
        request_items = [item]
        request = OperationRequest("copy", request_items)
        request_items.clear()
        self.assertEqual(request.items, (item,))
        with self.assertRaises(FrozenInstanceError):
            item.display_name = "changed"


class FakeShellItem:
    def __init__(self, path): self.path = path
    def GetDisplayName(self, flags): return self.path


class FakeOperation:
    def __init__(self, status=0x00270008, cancelled=False, recycle=True):
        self.status, self.cancelled, self.recycle = status, cancelled, recycle
        self.jobs, self.flags = [], 0
    def SetOwnerWindow(self, hwnd): self.owner = hwnd
    def SetOperationFlags(self, flags): self.flags = flags
    def Advise(self, sink): self.sink = sink; return 1
    def Unadvise(self, cookie): pass
    def GetAnyOperationsAborted(self): return self.cancelled
    def RenameItem(self, item, name): self.jobs.append(("rename", item, name))
    def MoveItem(self, item, destination): self.jobs.append(("move", item, destination))
    def CopyItem(self, item, destination): self.jobs.append(("copy", item, destination))
    def DeleteItem(self, item): self.jobs.append(("delete", item, None))
    def NewItem(self, destination, attributes, name, template, sink):
        self.jobs.append(("create_folder", destination, name))
    def PerformOperations(self):
        if self.cancelled: return
        for action, item, argument in self.jobs:
            if action == "rename":
                created = FakeShellItem(str(Path(item.path).with_name(argument)))
                self.sink.PostRenameItem(0, item, argument, self.status, created)
            elif action in {"copy", "move"}:
                created = FakeShellItem(str(Path(argument.path) / Path(item.path).name))
                getattr(self.sink, "Post" + action.capitalize() + "Item")(0, item, argument, None, self.status, created)
            elif action == "delete":
                flags = shellcon.TSF_DELETE_RECYCLE_IF_POSSIBLE if self.recycle else 0
                self.sink.PreDeleteItem(flags, item)
                self.sink.PostDeleteItem(flags, item, self.status, FakeShellItem("recycled-fixture"))
            else:
                created = FakeShellItem(str(Path(item.path) / argument))
                self.sink.PostNewItem(0, item, argument, None, 0x10, self.status, created)


class OperationSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="gamedock-operation-unit-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "fixture.txt"
        self.path.write_text("untouched", encoding="utf8")
        self.item = item_from_path(str(self.path))

    def perform(self, request, operation):
        with patch("file_operations._new_operation", return_value=operation), patch(
            "file_operations._shell_item", side_effect=FakeShellItem
        ), patch("file_operations._wrap_sink", side_effect=lambda value: value):
            return perform_operation(request)

    def test_informational_success_preserves_rename_mapping(self):
        result = self.perform(OperationRequest("rename", [self.item], new_name="new.txt"), FakeOperation())
        self.assertEqual(result.successful_ids, [self.item.id])
        self.assertEqual(result.id_mapping[self.item.id], canonical_identity(str(self.root / "new.txt")))
        self.assertFalse(result.errors)

    def test_cancel_and_hresult_error_never_report_success(self):
        cancelled = self.perform(OperationRequest("delete", [self.item]), FakeOperation(cancelled=True))
        self.assertTrue(cancelled.cancelled)
        self.assertFalse(cancelled.successful_ids)
        failed = self.perform(OperationRequest("rename", [self.item], new_name="new.txt"),
                              FakeOperation(status=-2147024891))
        self.assertTrue(failed.errors)
        self.assertFalse(failed.successful_ids)
        ignored = self.perform(OperationRequest("rename", [self.item], new_name="new.txt"),
                               FakeOperation(status=0x00270005))
        self.assertTrue(ignored.cancelled)
        self.assertFalse(ignored.successful_ids)

    def test_permanent_delete_is_vetoed_and_native_conflicts_are_not_auto_confirmed(self):
        operation = FakeOperation(recycle=False)
        result = self.perform(OperationRequest("delete", [self.item]), operation)
        self.assertTrue(result.errors)
        self.assertTrue(result.cancelled)
        self.assertFalse(result.successful_ids)
        self.assertTrue(operation.flags & file_operations.FOFX_RECYCLEONDELETE)
        self.assertTrue(operation.flags & shellcon.FOF_WANTNUKEWARNING)
        self.assertFalse(operation.flags & shellcon.FOF_NOCONFIRMATION)
        self.assertFalse(operation.flags & shellcon.FOF_SILENT)
        self.assertTrue(self.path.exists())

    def test_namespace_capability_invalid_name_and_missing_destination_block_before_com(self):
        system = DesktopItem("system", "Kosz", "::{GUID}", None, "system")
        with patch("file_operations._new_operation") as new_operation:
            for request in [
                OperationRequest("delete", [system]),
                OperationRequest("delete", [replace(self.item, can_delete=False)]),
                OperationRequest("rename", [self.item], new_name="../outside"),
                OperationRequest("rename", [self.item], new_name="CON.txt"),
                OperationRequest("copy", [self.item], destination=str(self.root / "missing")),
            ]:
                self.assertTrue(perform_operation(request).errors)
            new_operation.assert_not_called()

    def test_copy_success_does_not_transfer_favorite_identity(self):
        result = self.perform(OperationRequest("copy", [self.item], str(self.root)), FakeOperation(status=0))
        self.assertEqual(result.successful_ids, [self.item.id])
        self.assertEqual(result.id_mapping, {})

    def test_recursive_progress_reports_only_the_selected_folder(self):
        outcome = file_operations._Outcome()
        sink = file_operations._ProgressSink("copy", outcome, {self.item.id})
        nested = FakeShellItem(str(self.root / "nested.txt"))
        sink.PostCopyItem(0, nested, FakeShellItem(str(self.root)), None, 0, nested)
        self.assertEqual(outcome.successful_ids, [])


class NativeDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pythoncom.CoInitialize()
    @classmethod
    def tearDownClass(cls):
        pythoncom.CoUninitialize()

    def test_actual_explorer_snapshot_and_transparent_shell_icons(self):
        desktop = DesktopScanner(str(Path(__file__).resolve().parents[1]))
        items = desktop.scan()
        self.assertEqual(len(items), len({item.id for item in items}))
        for item in items[:3]:
            image = Image.open(io.BytesIO(desktop.extract_icon(item, 96)))
            self.assertEqual(image.size, (96, 96))
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.getchannel("A").getextrema()[1], 255)
            self.assertIsNotNone(image.getbbox())

    def test_actual_view_tracks_only_the_temporary_desktop_folder_and_rename(self):
        desktop_path = Path(shell.SHGetKnownFolderPath(shellcon.FOLDERID_Desktop, 0, None)).resolve()
        fixture_name = "GameDock-sync-test-" + uuid.uuid4().hex
        fixture = desktop_path / fixture_name
        renamed = desktop_path / (fixture_name + "-renamed")
        desktop = DesktopScanner(str(Path(__file__).resolve().parents[1]))

        def wait_for(path):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    match = next((item for item in desktop.scan() if item.id == canonical_identity(str(path))), None)
                    if match:
                        return match
                except DesktopUnavailableError:
                    pass
                time.sleep(0.15)
            self.fail("Explorer did not publish the temporary fixture within five seconds.")

        try:
            fixture.mkdir()
            (fixture / "contained-test.txt").write_text("isolated fixture", encoding="utf8")
            original = wait_for(fixture)
            self.assertEqual(original.kind, "folder")
            self.assertTrue(original.can_rename)
            self.assertTrue(original.can_move)
            self.assertTrue(original.can_delete)
            fixture.rename(renamed)
            current = wait_for(renamed)
            self.assertEqual(current.kind, "folder")
            self.assertEqual(original.file_identity, current.file_identity)
            self.assertNotEqual(original.id, current.id)
            self.assertFalse(any(item.id == original.id for item in desktop.scan()))
        finally:
            # Verify each absolute target belongs to this exact unique fixture
            # before recursively removing anything from the user's Desktop.
            for target in (fixture, renamed):
                resolved = target.resolve()
                self.assertEqual(resolved.parent, desktop_path)
                self.assertIn(resolved.name, {fixture_name, fixture_name + "-renamed"})
                if resolved.exists():
                    shutil.rmtree(resolved)

    def test_native_copy_unicode_rename_move_and_new_folder(self):
        with tempfile.TemporaryDirectory(prefix="gamedock-native-") as temporary:
            root = Path(temporary)
            destination = root / "destination"
            destination.mkdir()
            source = root / "żółć.txt"
            source.write_text("unique GameDock fixture", encoding="utf8")
            original = item_from_path(str(source))
            copied = perform_operation(OperationRequest("copy", [original], str(destination)))
            self.assertFalse(copied.errors, copied)
            self.assertEqual((destination / source.name).read_text(encoding="utf8"), "unique GameDock fixture")
            renamed = perform_operation(OperationRequest("rename", [original], new_name="nowa nazwa.txt"))
            self.assertFalse(renamed.errors, renamed)
            target = root / "nowa nazwa.txt"
            self.assertEqual(renamed.id_mapping[original.id], canonical_identity(str(target)))
            moved_item = item_from_path(str(target))
            moved = perform_operation(OperationRequest("move", [moved_item], str(destination)))
            self.assertFalse(moved.errors, moved)
            self.assertTrue((destination / target.name).exists())
            self.assertFalse(target.exists())
            self.assertEqual(moved.id_mapping[moved_item.id], canonical_identity(str(destination / target.name)))
            created = perform_operation(OperationRequest("create_folder", [], str(root), "folder z kropką.v1"))
            self.assertFalse(created.errors, created)
            self.assertTrue((root / "folder z kropką.v1").is_dir())

    def test_native_delete_actually_recycles_only_the_temporary_fixture(self):
        with tempfile.TemporaryDirectory(prefix="gamedock-recycle-") as temporary:
            root = Path(temporary)
            source = root / f"gamedock-recycle-{uuid.uuid4().hex}.txt"
            payload = "GameDock isolated recycle verification"
            source.write_text(payload, encoding="utf8")
            result = perform_operation(OperationRequest("delete", [item_from_path(str(source))]))
            self.assertFalse(result.errors, result)
            self.assertFalse(result.cancelled, result)
            self.assertFalse(source.exists())
            recycle = win32com.client.Dispatch("Shell.Application").NameSpace(10)
            found = False
            for entry in recycle.Items():
                deleted_from = entry.ExtendedProperty("System.Recycle.DeletedFrom")
                if str(deleted_from).casefold() == str(root).casefold():
                    if os.path.isfile(entry.Path) and Path(entry.Path).read_text(encoding="utf8") == payload:
                        found = True
                        break
            self.assertTrue(found, "The fixture must exist in the Recycle Bin, not merely disappear.")


if __name__ == "__main__":
    unittest.main()
