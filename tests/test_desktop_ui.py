"""Headless interaction tests; no desktop reads or file operations."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from dataclasses import dataclass, replace
from unittest.mock import patch
from PySide6.QtCore import QBuffer, QIODevice, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFontDatabase, QImage, QPainter
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QStyle, QStyleOptionViewItem, QWidget
from desktop_model import DesktopModel, DesktopProxyModel, ID_ROLE, ITEM_ROLE
from hub_window import GameHubWindow


@dataclass(frozen=True)
class Item:
    id: str
    display_name: str
    parsing_name: str
    path: str | None
    kind: str = "shortcut"
    modified: float = 0
    fingerprint: str = "one"
    source: str = "desktop"
    can_rename: bool = True
    can_move: bool = True
    can_delete: bool = True
    can_copy: bool = True


def item(number=0, **kwargs):
    fields = dict(id=f"id:{number}", display_name=f"Gra {number:04}", parsing_name=f"C:\\Desktop\\{number}.lnk", path=f"C:\\Desktop\\{number}.lnk")
    fields.update(kwargs)
    return Item(**fields)


class Config:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)


APP = QApplication.instance() or QApplication([])
if not QFontDatabase.families():
    for font in ("segoeui.ttf", "seguisb.ttf", "seguisym.ttf"):
        QFontDatabase.addApplicationFont(os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", font))


class DesktopModelTests(unittest.TestCase):
    def test_same_title_different_identity_and_incremental_changes(self):
        model = DesktopModel()
        reset = QSignalSpy(model.modelReset)
        a, b = item(1, display_name="Projekt"), item(2, display_name="Projekt")
        model.apply_snapshot([a, b])
        model.set_checked(b.id, True)
        updated = replace(b, display_name="Projekt nowy", fingerprint="two")
        model.apply_snapshot([updated, item(3)])
        self.assertEqual(model.rowCount(), 2)
        self.assertEqual(model.checked_items, [updated])
        self.assertEqual(model.index_for_id(b.id).data(ITEM_ROLE), updated)
        self.assertFalse(model.index_for_id(a.id).isValid())
        self.assertEqual(reset.count(), 0)

    def test_polish_search_filter_favorites_and_date_sort(self):
        model, proxy = DesktopModel(), DesktopProxyModel()
        proxy.setSourceModel(model)
        a = item(1, display_name="Żółć i Łódź.txt", modified=50)
        b = item(2, display_name="Zwykły folder", kind="folder", modified=100)
        model.apply_snapshot([a, b])
        proxy.set_query("zolc lodz")
        self.assertEqual(proxy.rowCount(), 1)
        self.assertEqual(proxy.index(0, 0).data(ID_ROLE), a.id)
        proxy.set_query("")
        proxy.set_kind("folder")
        self.assertEqual(proxy.index(0, 0).data(ID_ROLE), b.id)
        proxy.set_kind("favorites")
        self.assertEqual(proxy.rowCount(), 0)
        model.set_favorite(a.id, True)
        self.assertEqual(proxy.rowCount(), 1)
        proxy.set_kind("all")
        proxy.set_sort_mode("modified")
        self.assertEqual(proxy.index(0, 0).data(ID_ROLE), b.id)


class DesktopWindowTests(unittest.TestCase):
    def setUp(self):
        self.window = GameHubWindow(os.getcwd(), Config())
        self.window.resize(1100, 740)
        self.window.show()
        APP.processEvents()

    def tearDown(self):
        self.window.hide()
        self.window.deleteLater()
        APP.processEvents()

    def populate(self, items):
        self.window.apply_snapshot(items)
        self.window.view.doItemsLayout()
        QTest.qWait(40)
        APP.processEvents()

    def test_empty_and_thousand_items_use_one_view_without_card_widgets(self):
        self.assertEqual(self.window.stack.currentIndex(), 1)
        self.populate([item(i) for i in range(1000)])
        self.assertEqual(self.window.proxy.rowCount(), 1000)
        self.assertEqual(self.window.stack.currentIndex(), 0)
        self.assertGreater(self.window.view.verticalScrollBar().maximum(), 0)
        self.assertLess(len(self.window.view.findChildren(QWidget)), 20)
        self.assertTrue(self.window.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertEqual(self.window.windowOpacity(), 1)

    def test_snapshot_preserves_search_focus_checked_item_and_scroll(self):
        items = [item(i) for i in range(300)]
        self.populate(items)
        self.window.search.setText("Gra")
        focused = self.window.proxy.mapFromSource(self.window.model.index_for_id(items[160].id))
        self.window.view.setCurrentIndex(focused)
        self.window.set_selection_mode(True)
        self.window.model.set_checked(items[160].id, True)
        self.window.view.scrollTo(focused)
        APP.processEvents()
        self.assertGreater(self.window.view.verticalScrollBar().value(), 0)
        self.populate([replace(x, modified=10) if x.id == items[160].id else x for x in items] + [item(900)])
        self.assertEqual(self.window.search.text(), "Gra")
        self.assertEqual(self.window.view.currentIndex().data(ID_ROLE), items[160].id)
        self.assertEqual([x.id for x in self.window.model.checked_items], [items[160].id])
        self.assertGreater(self.window.view.verticalScrollBar().value(), 0)

    def test_main_click_opens_checkbox_and_selection_click_do_not(self):
        self.populate([item(1), item(2)])
        opened = QSignalSpy(self.window.open_requested)
        index = self.window.proxy.index(0, 0)
        rect = self.window.view.visualRect(index)
        QTest.mouseClick(self.window.view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        self.assertEqual(opened.count(), 1)
        self.assertIsNone(self.window.delegate.hit_control(rect, rect.center()))
        self.window.set_selection_mode(True)
        checkbox = self.window.delegate.control_rect(rect, "check").center()
        QTest.mouseClick(self.window.view.viewport(), Qt.MouseButton.LeftButton, pos=checkbox)
        self.assertEqual(opened.count(), 1)
        self.assertEqual(len(self.window.model.checked_items), 1)
        QTest.mouseClick(self.window.view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        self.assertEqual(opened.count(), 1)
        self.assertEqual(len(self.window.model.checked_items), 0)
        self.window.set_selection_mode(False)
        star = self.window.delegate.control_rect(rect, "favorite").center()
        self.assertIsNone(self.window.delegate.hit_control(rect, star))

    def test_virtual_item_disables_file_management_and_location(self):
        virtual = item(1, path=None, kind="system", can_rename=False, can_move=False, can_delete=False, can_copy=False)
        self.populate([virtual])
        self.window.set_selection_mode(True)
        self.window.model.set_checked(virtual.id, True)
        menu = self.window._make_item_menu(virtual)
        actions = {action.text(): action for action in menu.actions()}
        self.assertNotIn("Zarządzaj skrótem", actions)
        self.assertFalse(actions["Pokaż w folderze"].isEnabled())
        self.assertTrue(actions["Otwórz"].isEnabled())
        self.assertTrue(actions["Właściwości"].isEnabled())

    def test_busy_blocks_open_and_escape_keeps_selection_exit_separate(self):
        self.populate([item(1)])
        opened = QSignalSpy(self.window.open_requested)
        self.window.set_operation_busy(True)
        self.window._item_clicked(self.window.proxy.index(0, 0))
        self.assertEqual(opened.count(), 0)
        self.window.set_operation_busy(False)
        self.window.set_selection_mode(True)
        self.window._escape()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window._selection_mode)
        self.window._escape()
        self.assertFalse(self.window.isVisible())

    def test_keyboard_navigation_select_open_and_file_commands(self):
        self.populate([item(1), item(2)])
        self.window.view.setFocus()
        self.window.view.setCurrentIndex(self.window.proxy.index(0, 0))
        opened = QSignalSpy(self.window.open_requested)
        operations = QSignalSpy(self.window.operation_requested)
        QTest.keyClick(self.window.view, Qt.Key.Key_Right)
        focused = self.window.view.currentIndex().data(ITEM_ROLE)
        QTest.keyClick(self.window.view, Qt.Key.Key_Space)
        self.assertEqual([x.id for x in self.window.model.checked_items], [focused.id])
        self.assertEqual(opened.count(), 0)
        QTest.keyClick(self.window.view, Qt.Key.Key_Return)
        self.assertEqual(opened.count(), 1)
        with patch("hub_window.QInputDialog.getText", return_value=("new.txt", True)) as dialog:
            QTest.keyClick(self.window.view, Qt.Key.Key_F2)
        # Like Explorer: the dialog shows the name without .lnk and keeps the extension.
        self.assertEqual(dialog.call_args.kwargs["text"], focused.path.rsplit("\\", 1)[-1][:-4])
        self.assertEqual(operations.count(), 1)
        self.assertEqual(operations.at(0)[0]["action"], "rename")
        self.assertEqual(operations.at(0)[0]["new_name"], "new.txt.lnk")
        with patch("hub_window.QInputDialog.getText", return_value=(" Inna.LNK ", True)):
            QTest.keyClick(self.window.view, Qt.Key.Key_F2)
        self.assertEqual(operations.at(1)[0]["new_name"], "Inna.LNK")
        with patch("hub_window.QInputDialog.getText", return_value=(focused.path.rsplit("\\", 1)[-1], True)):
            QTest.keyClick(self.window.view, Qt.Key.Key_F2)
        self.assertEqual(operations.count(), 2)

    def test_select_all_is_filtered_and_batched(self):
        self.window.config_mgr.values["favorites"] = [f"id:{i}" for i in range(1000) if i % 2]
        self.populate([item(i) for i in range(1000)])
        self.window._set_filter("favorites")
        self.window.view.setFocus()
        changes = QSignalSpy(self.window.model.checked_changed)
        self.window._select_all()
        self.assertEqual(len(self.window.model.checked_items), 500)
        self.assertTrue(all(int(x.id.split(":")[1]) % 2 for x in self.window.model.checked_items))
        self.assertEqual(changes.count(), 1)

    def test_rename_remaps_focus_check_and_favorite_without_losing_filtered_item(self):
        original = item(1)
        renamed = replace(original, id="renamed", display_name="Nowa nazwa", fingerprint="new")
        self.window.config_mgr.values["favorites"] = [original.id]
        self.populate([original, item(2)])
        self.window._set_filter("favorites")
        self.window.view.setCurrentIndex(self.window.proxy.index(0, 0))
        self.window.set_selection_mode(True)
        self.window.model.set_checked(original.id, True)
        self.window.config_mgr.values["favorites"] = [renamed.id]
        self.window.remap_item_ids({original.id: renamed.id})
        self.populate([renamed, item(2)])
        self.assertEqual(self.window.proxy.rowCount(), 1)
        self.assertEqual(self.window.view.currentIndex().data(ID_ROLE), renamed.id)
        self.assertEqual([x.id for x in self.window.model.checked_items], [renamed.id])

    def test_native_operation_results_success_partial_and_cancelled(self):
        from file_operations import OperationResult
        self.window.show_operation_result(OperationResult("rename", successful_ids=["x"]))
        self.assertIn("Gotowe", self.window.message_label.text())
        self.assertFalse(self.window.message_label.isVisible())
        self.assertNotIn("#ffccd1", self.window.message_label.styleSheet())
        self.window.show_operation_result(OperationResult("move", successful_ids=["x"], errors=["Brak dostępu"]))
        self.assertIn("Wykonane elementy: 1", self.window.message_label.text())
        self.assertIn("Brak dostępu", self.window.message_label.text())
        self.assertIn("#ffccd1", self.window.message_label.styleSheet())
        self.window.show_operation_result(OperationResult("delete", cancelled=True))
        self.assertIn("anulowana", self.window.message_label.text())
        self.assertNotIn("#ffccd1", self.window.message_label.styleSheet())

    def test_minimal_default_and_transient_search_escape(self):
        self.populate([item(1), item(2)])
        self.assertFalse(self.window.search.isVisible())
        self.assertEqual(self.window.header.findChildren(QLabel), [])
        self.assertEqual(self.window.findChildren(QPushButton), [])
        self.window._show_search()
        self.window.search.setText("0001")
        self.assertEqual(self.window.proxy.rowCount(), 1)
        self.window._escape()
        self.assertFalse(self.window.search.isVisible())
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.window.proxy.rowCount(), 2)
        self.window._escape()
        self.assertFalse(self.window.isVisible())

    def test_reference_header_eight_columns_and_five_spacious_rows(self):
        self.window.resize(1600, 860)
        self.populate([item(i) for i in range(37)])
        first = self.window.view.visualRect(self.window.proxy.index(0, 0))
        eighth = self.window.view.visualRect(self.window.proxy.index(7, 0))
        ninth = self.window.view.visualRect(self.window.proxy.index(8, 0))
        self.assertEqual(first.top(), eighth.top())
        self.assertGreater(ninth.top(), first.top())
        self.assertEqual(ninth.top()-first.top(), 146)
        self.assertEqual(self.window.view.gridSize().height(), 146)
        self.assertEqual(self.window.view.verticalScrollBar().maximum(), 0)
        icon = self.window.delegate.icon_rect(first)
        icon_top_left = self.window.view.viewport().mapTo(self.window, icon.topLeft())
        self.assertEqual(icon.size(), QSize(80, 80))
        self.assertEqual(icon_top_left, QPoint(80, 82))
        self.assertEqual(eighth.left()-first.left(), 7*193)
        self.assertEqual(self.window.view.visualRect(self.window.proxy.index(36, 0)).top(), 4*146)

    def test_scrollbar_threshold_preserves_eight_columns(self):
        self.window.resize(1600, 860)
        for count in (37, 40, 41, 1000, 40):
            self.populate([item(i) for i in range(count)])
            first = self.window.view.visualRect(self.window.proxy.index(0, 0))
            eighth = self.window.view.visualRect(self.window.proxy.index(7, 0))
            ninth = self.window.view.visualRect(self.window.proxy.index(8, 0))
            self.assertEqual(first.top(), eighth.top(), count)
            self.assertEqual(ninth.top()-first.top(), 146, count)
            self.assertEqual(self.window.view.verticalScrollBar().maximum() > 0, count > 40)
            self.assertEqual(self.window.view.verticalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.assertFalse(self.window.view.verticalScrollBar().isVisible())
        self.populate([item(i) for i in range(1000)])
        self.window.view.setFocus()
        QTest.keyClick(self.window.view, Qt.Key.Key_End)
        self.assertGreater(self.window.view.verticalScrollBar().value(), 0)
        self.window._set_view_mode("list", False)
        self.assertFalse(self.window.view.verticalScrollBar().isVisible())

    def test_hover_and_mouse_focus_never_paint_tile_cards(self):
        self.populate([item(1)])
        index = self.window.proxy.index(0, 0)
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 192, 146)
        option.state = QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_HasFocus
        self.assertFalse(self.window.delegate.keyboard_navigation)
        image = QImage(option.rect.size(), QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        self.window.delegate.paint(painter, option, index)
        painter.end()
        for point in (QPoint(10, 12), QPoint(15, 50), QPoint(100, 130)):
            self.assertEqual(image.pixelColor(point).alpha(), 0)
        self.window.view.setFocus()
        QTest.keyClick(self.window.view, Qt.Key.Key_Right)
        self.assertTrue(self.window.delegate.keyboard_navigation)
        QTest.mouseClick(self.window.view.viewport(), Qt.MouseButton.LeftButton, pos=self.window.view.visualRect(index).center())
        self.assertFalse(self.window.delegate.keyboard_navigation)

    def test_clear_glass_keeps_neutral_alpha_and_opaque_foreground(self):
        game = item(1)
        self.populate([game])
        icon = QImage(80, 80, QImage.Format.Format_ARGB32)
        icon.fill(QColor(40, 230, 60, 255))
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        self.assertTrue(icon.save(buffer, "PNG"))
        self.window.apply_icon(game.id, bytes(buffer.data()))
        self.window._acrylic = True
        self.window.repaint()
        image = self.window.grab().toImage()
        self.assertEqual(image.pixelColor(300, 700), QColor(0, 0, 0, 71))
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        rect = self.window.delegate.icon_rect(self.window.view.visualRect(self.window.proxy.index(0, 0)))
        center = self.window.view.viewport().mapTo(self.window, rect.center())
        self.assertEqual(image.pixelColor(center).alpha(), 255)
        self.assertTrue(all(image.pixelColor(x, y) == QColor(0, 0, 0, 71) for x in range(31, 80) for y in range(20, 64)))
        self.window.config_mgr.values["glass_opacity"] = 0
        self.window.repaint()
        self.assertEqual(self.window.grab().toImage().pixelColor(300, 700), QColor(0, 0, 0, 1))

    def test_games_menu_has_only_game_filters_and_manual_add_is_nondestructive(self):
        self.populate([item(1)])
        menu = self.window._make_header_menu()
        actions = {action.text(): action for action in menu.actions()}
        self.assertIn("Dodaj grę…", actions)
        self.assertNotIn("Nowy folder…", actions)
        self.assertEqual([action.text() for action in actions["Pokaż"].menu().actions()], ["Wszystkie gry", "Ulubione"])
        added = QSignalSpy(self.window.game_added)
        path = "C:\\Games\\My Game.exe"
        with patch("hub_window.QFileDialog.getOpenFileName", return_value=(path, "")):
            self.window._add_game()
        self.assertEqual(added.count(), 1)
        self.assertEqual(added.at(0)[0], path)
        with patch("hub_window.QFileDialog.getOpenFileName", return_value=("", "")):
            self.window._add_game()
        self.assertEqual(added.count(), 1)

    def test_removal_and_delete_key_only_exclude_game_executable(self):
        executable = item(1, kind="file", path="C:\\Games\\Game.exe")
        self.populate([executable])
        removed = QSignalSpy(self.window.game_removed)
        operations = QSignalSpy(self.window.operation_requested)
        menu = self.window._make_item_menu(executable)
        actions = {action.text(): action for action in menu.actions()}
        self.assertNotIn("Zarządzaj skrótem", actions)
        actions["Usuń z listy gier"].trigger()
        self.assertEqual(removed.at(0)[0], executable.id)
        self.window.view.setFocus()
        self.window.view.setCurrentIndex(self.window.proxy.index(0, 0))
        QTest.keyClick(self.window.view, Qt.Key.Key_Delete)
        self.assertEqual(removed.count(), 2)
        with patch("hub_window.QInputDialog.getText") as dialog:
            QTest.keyClick(self.window.view, Qt.Key.Key_F2)
            dialog.assert_not_called()
        self.assertEqual(operations.count(), 0)

    def test_shortcut_file_operations_are_grouped_separately(self):
        shortcut = item(1)
        self.populate([shortcut])
        menu = self.window._make_item_menu(shortcut)
        actions = {action.text(): action for action in menu.actions()}
        self.assertIn("Usuń z listy gier", actions)
        self.assertNotIn("Zmień nazwę…", actions)
        management = {action.text(): action for action in actions["Zarządzaj skrótem"].menu().actions()}
        self.assertIn("Zmień nazwę…", management)
        self.assertIn("Przenieś skrót do Kosza…", management)

    def test_programmatic_chrome_resize_never_persists_geometry(self):
        settings = QSignalSpy(self.window.settings_changed)
        self.window.resize(1616, 899)
        self.window.hide()
        self.assertEqual(settings.count(), 0)
        self.assertEqual(self.window._preferred_size, QSize(1600, 860))
        self.window.show()
        self.window._interactive_start_size = QSize(self.window.size())
        self.window.resize(1200, 720)
        self.window._finish_user_resize()
        self.assertEqual(settings.count(), 2)
        self.assertEqual(self.window._preferred_size, QSize(1200, 720))
        self.window.hide()
        self.assertEqual(settings.count(), 2)

    def test_restore_games_only_appears_when_library_has_exclusions(self):
        title = "Przywróć gry usunięte z listy"
        self.assertNotIn(title, [action.text() for action in self.window._make_settings_menu().actions()])
        self.window.config_mgr.values["game_overrides"] = {"manual": True, "excluded": False}
        restored = QSignalSpy(self.window.games_restored)
        actions = {action.text(): action for action in self.window._make_settings_menu().actions()}
        actions[title].trigger()
        self.assertEqual(restored.count(), 1)
        self.window.config_mgr.values["game_overrides"] = {"manual": True}
        self.assertNotIn(title, [action.text() for action in self.window._make_settings_menu().actions()])


if __name__ == "__main__":
    unittest.main()
