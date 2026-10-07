"""Identity-based desktop model, filtering and scalable icon rendering."""

from __future__ import annotations

import unicodedata
from typing import Any

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt, Signal, QSortFilterProxyModel
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen
from PySide6.QtWidgets import QApplication, QListView, QStyle, QStyledItemDelegate, QStyleOptionViewItem


ITEM_ROLE = int(Qt.ItemDataRole.UserRole) + 1
ID_ROLE = ITEM_ROLE + 1
KIND_ROLE = ITEM_ROLE + 2
FAVORITE_ROLE = ITEM_ROLE + 3
KIND_LABELS = {"shortcut": "Skrót", "file": "Plik", "folder": "Folder", "system": "System"}


def searchable(value: str) -> str:
    """Polish ł does not decompose under Unicode NFKD."""
    value = value.casefold().translate(str.maketrans({"ł": "l", "ø": "o", "đ": "d"}))
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


class DesktopModel(QAbstractListModel):
    checked_changed = Signal()

    def __init__(self, favorites=(), parent=None):
        super().__init__(parent)
        self._items: list[Any] = []
        self._rows: dict[str, int] = {}
        self._icons: dict[str, QIcon] = {}
        self._checked: set[str] = set()
        self._favorites = set(favorites)
        style = QApplication.style()
        self._fallbacks = {
            "folder": style.standardIcon(QStyle.StandardPixmap.SP_DirIcon),
            "shortcut": style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon),
            "file": style.standardIcon(QStyle.StandardPixmap.SP_FileIcon),
            "system": style.standardIcon(QStyle.StandardPixmap.SP_DesktopIcon),
        }

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        item = self._items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return item.display_name
        if role == Qt.ItemDataRole.DecorationRole:
            return self._icons.get(item.id, self._fallbacks.get(item.kind, self._fallbacks["file"]))
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{item.display_name}\n{item.path or 'Element systemowy Windows'}"
        if role == Qt.ItemDataRole.CheckStateRole:
            return Qt.CheckState.Checked if item.id in self._checked else Qt.CheckState.Unchecked
        if role == ITEM_ROLE:
            return item
        if role == ID_ROLE:
            return item.id
        if role == KIND_ROLE:
            return item.kind
        if role == FAVORITE_ROLE:
            return item.id in self._favorites
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return item.display_name
        return None

    def flags(self, index):
        return (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable) if index.isValid() else Qt.ItemFlag.NoItemFlags

    @property
    def items(self):
        return list(self._items)

    @property
    def checked_items(self):
        return [item for item in self._items if item.id in self._checked]

    def index_for_id(self, item_id):
        row = self._rows.get(item_id)
        return self.index(row, 0) if row is not None else QModelIndex()

    def _reindex(self):
        self._rows = {item.id: row for row, item in enumerate(self._items)}

    def apply_snapshot(self, items):
        """Keep surviving rows and their icons/checks; the proxy owns ordering."""
        incoming = {item.id: item for item in items}
        selection_changed = bool(self._checked - incoming.keys())
        for row in range(len(self._items) - 1, -1, -1):
            item = self._items[row]
            if item.id not in incoming:
                self.beginRemoveRows(QModelIndex(), row, row)
                self._items.pop(row)
                self._icons.pop(item.id, None)
                self._checked.discard(item.id)
                self.endRemoveRows()
        self._reindex()
        for row, old in enumerate(self._items):
            new = incoming[old.id]
            if old != new:
                self._items[row] = new
                if old.fingerprint != new.fingerprint:
                    self._icons.pop(old.id, None)
                index = self.index(row, 0)
                self.dataChanged.emit(index, index)
        additions = [item for item in incoming.values() if item.id not in self._rows]
        if additions:
            start = len(self._items)
            self.beginInsertRows(QModelIndex(), start, start + len(additions) - 1)
            self._items.extend(additions)
            self.endInsertRows()
            self._reindex()
        if selection_changed:
            self.checked_changed.emit()

    def apply_icon(self, item_id: str, png: bytes):
        index = self.index_for_id(item_id)
        if not index.isValid():
            return
        from PySide6.QtGui import QPixmap
        pixmap = QPixmap()
        if pixmap.loadFromData(png):
            self._icons[item_id] = QIcon(pixmap)
            self.dataChanged.emit(index, index, [int(Qt.ItemDataRole.DecorationRole)])

    def set_checked(self, item_id: str, checked: bool):
        index = self.index_for_id(item_id)
        if not index.isValid() or (item_id in self._checked) == checked:
            return
        if checked:
            self._checked.add(item_id)
        else:
            self._checked.discard(item_id)
        self.dataChanged.emit(index, index, [int(Qt.ItemDataRole.CheckStateRole)])
        self.checked_changed.emit()

    def set_checked_ids(self, item_ids, checked=True):
        ids = set(item_ids) & self._rows.keys()
        changed = ids - self._checked if checked else ids & self._checked
        if not changed:
            return
        if checked:
            self._checked.update(changed)
        else:
            self._checked.difference_update(changed)
        rows = [self._rows[item_id] for item_id in changed]
        self.dataChanged.emit(self.index(min(rows), 0), self.index(max(rows), 0), [int(Qt.ItemDataRole.CheckStateRole)])
        self.checked_changed.emit()

    def clear_checked(self):
        if not self._checked:
            return
        self._checked.clear()
        if self._items:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._items)-1, 0), [int(Qt.ItemDataRole.CheckStateRole)])
        self.checked_changed.emit()

    def set_favorite(self, item_id, favorite):
        if favorite:
            self._favorites.add(item_id)
        else:
            self._favorites.discard(item_id)
        index = self.index_for_id(item_id)
        if index.isValid():
            self.dataChanged.emit(index, index, [FAVORITE_ROLE])

    def remap_item_ids(self, mapping):
        self._checked = {mapping.get(item_id, item_id) for item_id in self._checked}
        self._favorites = {mapping.get(item_id, item_id) for item_id in self._favorites}
        for previous, current in mapping.items():
            if previous in self._icons:
                self._icons[current] = self._icons.pop(previous)

    def set_favorites(self, favorites):
        updated = set(favorites)
        if updated == self._favorites:
            return
        self._favorites = updated
        if self._items:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._items)-1, 0), [FAVORITE_ROLE])


class DesktopProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.query = ""
        self.kind = "all"
        self.sort_mode = "name_asc"
        self.setDynamicSortFilter(True)

    def set_query(self, text):
        self.beginFilterChange()
        self.query = searchable(text.strip())
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def set_kind(self, kind):
        self.beginFilterChange()
        self.kind = kind
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def set_sort_mode(self, mode):
        self.sort_mode = mode
        self.invalidate()
        order = Qt.SortOrder.DescendingOrder if mode in ("name_desc", "modified") else Qt.SortOrder.AscendingOrder
        self.sort(0, order)

    def filterAcceptsRow(self, row, parent):
        index = self.sourceModel().index(row, 0, parent)
        item = index.data(ITEM_ROLE)
        if self.kind == "favorites" and not index.data(FAVORITE_ROLE):
            return False
        if self.kind not in ("all", "favorites") and item.kind != self.kind:
            return False
        return all(word in searchable(item.display_name + " " + (item.path or "")) for word in self.query.split())

    def lessThan(self, left, right):
        a, b = left.data(ITEM_ROLE), right.data(ITEM_ROLE)
        if self.sort_mode == "modified":
            return (a.modified or 0, searchable(a.display_name), a.id) < (b.modified or 0, searchable(b.display_name), b.id)
        return (searchable(a.display_name), a.id) < (searchable(b.display_name), b.id)


class DesktopDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.list_mode = False
        self.selection_mode = False
        self.keyboard_navigation = False

    def sizeHint(self, option, index):
        return QSize(180, 76 if self.list_mode else 146)

    def icon_rect(self, rect):
        if self.list_mode:
            return QRect(rect.left()+43, rect.top()+15, 42, 42)
        return QRect(rect.left()+(rect.width()-80)//2, rect.top(), 80, 80)

    def control_rect(self, rect, control):
        if control == "check":
            return QRect(rect.left()+13, rect.top()+14, 23, 23)
        return QRect(rect.right()-35, rect.top()+14, 23, 23)

    def hit_control(self, rect, point):
        if self.selection_mode and self.control_rect(rect, "check").contains(point):
            return "check"
        return None

    @staticmethod
    def _draw_name(painter, rect, alignment, text):
        painter.setPen(QColor(0, 0, 0, 210))
        painter.drawText(rect.translated(1, 1), alignment, text)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(rect, alignment, text)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(7, 4, -7, -4)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focused = bool(option.state & QStyle.StateFlag.State_HasFocus)
        checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
        icon_rect = self.icon_rect(option.rect)
        if focused and self.keyboard_navigation:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(205, 225, 248, 180), 1))
            painter.drawRoundedRect(icon_rect.adjusted(-4, -4, 4, 4), 8, 8)
        if self.selection_mode:
            check = self.control_rect(option.rect, "check")
            painter.setBrush(QColor("#b5cee9") if checked else QColor(42, 51, 63, 100))
            painter.setPen(QPen(QColor(210, 226, 247, 170), 1))
            painter.drawRoundedRect(check.adjusted(3, 3, -3, -3), 5, 5)
            if checked:
                painter.setPen(QPen(QColor("#273b55"), 2))
                painter.drawLine(check.left()+7, check.top()+11, check.left()+10, check.top()+14)
                painter.drawLine(check.left()+10, check.top()+14, check.left()+16, check.top()+8)
        item = index.data(ITEM_ROLE)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if self.list_mode:
            title_rect = QRect(icon_rect.right()+14, rect.top()+10, max(20, rect.width()-120), 24)
            subtitle = QRect(title_rect.left(), rect.top()+35, title_rect.width(), 18)
            font = QFont("Segoe UI")
            font.setPixelSize(13)
            font.setWeight(QFont.Weight.DemiBold)
            font.setUnderline(hovered)
            painter.setFont(font)
            self._draw_name(painter, title_rect, Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(item.display_name, Qt.TextElideMode.ElideRight, title_rect.width()))
            font.setPixelSize(11)
            font.setWeight(QFont.Weight.Normal)
            font.setUnderline(False)
            painter.setFont(font)
            painter.setPen(QColor("#cad3e0"))
            detail = KIND_LABELS.get(item.kind, "Plik") + (" · " + item.path if item.path else " · Windows")
            painter.drawText(subtitle, Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(detail, Qt.TextElideMode.ElideMiddle, subtitle.width()))
        else:
            title_rect = QRect(rect.left()+5, option.rect.top()+92, rect.width()-10, 20)
            font = QFont("Segoe UI")
            font.setPixelSize(11)
            font.setWeight(QFont.Weight.Bold)
            font.setUnderline(hovered)
            painter.setFont(font)
            self._draw_name(painter, title_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(item.display_name, Qt.TextElideMode.ElideRight, title_rect.width()))
        if isinstance(icon, QIcon):
            icon.paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter)
        painter.restore()


class DesktopView(QListView):
    item_clicked = Signal(QModelIndex, int)
    control_clicked = Signal(QModelIndex, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pressed_control = None
        self._press_position = None
        self._syncing_grid = False
        self.setMouseTracking(True)
        self.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.setEditTriggers(QListView.EditTrigger.NoEditTriggers)
        self.setMovement(QListView.Movement.Static)
        self.setLayoutMode(QListView.LayoutMode.Batched)
        self.setBatchSize(100)
        self.setUniformItemSizes(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setAccessibleName("Gry")

    def set_list_mode(self, enabled):
        self.itemDelegate().list_mode = enabled
        self.setViewMode(QListView.ViewMode.ListMode if enabled else QListView.ViewMode.IconMode)
        self.setMovement(QListView.Movement.Static)
        self.setWrapping(not enabled)
        if enabled:
            self.setGridSize(QSize())
        else:
            self._sync_grid_geometry()
        self.doItemsLayout()
        self.viewport().update()

    def _grid_cell_width(self):
        full_width = max(1, self.width())
        columns = max(1, min(8, full_width // 180))
        return max(1, full_width // columns-1)

    def setModel(self, model):
        super().setModel(model)
        if model is not None:
            for signal in (model.rowsInserted, model.rowsRemoved, model.modelReset, model.layoutChanged):
                signal.connect(self._sync_grid_geometry)

    def _sync_grid_geometry(self, *args):
        delegate = self.itemDelegate()
        if self._syncing_grid or not isinstance(delegate, DesktopDelegate) or delegate.list_mode:
            return
        self._syncing_grid = True
        try:
            self.setGridSize(QSize(self._grid_cell_width(), 146))
        finally:
            self._syncing_grid = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        delegate = self.itemDelegate()
        if isinstance(delegate, DesktopDelegate) and not delegate.list_mode:
            self._sync_grid_geometry()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right,
                           Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            self.itemDelegate().keyboard_navigation = True
            self.viewport().update()
        super().keyPressEvent(event)

    def focusInEvent(self, event):
        if event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason):
            self.itemDelegate().keyboard_navigation = True
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self.itemDelegate().keyboard_navigation = False
        super().focusOutEvent(event)

    def mousePressEvent(self, event):
        self.itemDelegate().keyboard_navigation = False
        self.viewport().update()
        self._press_position = event.position().toPoint()
        index = self.indexAt(self._press_position)
        self._pressed_control = None
        if event.button() == Qt.MouseButton.LeftButton and index.isValid():
            self._pressed_control = self.itemDelegate().hit_control(self.visualRect(index), self._press_position)
            if self._pressed_control:
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        point = event.position().toPoint()
        index = self.indexAt(point)
        if event.button() == Qt.MouseButton.LeftButton and index.isValid():
            control = self.itemDelegate().hit_control(self.visualRect(index), point)
            if self._pressed_control:
                if self._pressed_control == control:
                    self.control_clicked.emit(index, control)
                self._pressed_control = None
                event.accept()
                return
            moved = self._press_position is not None and (point-self._press_position).manhattanLength() >= QApplication.startDragDistance()
            super().mouseReleaseEvent(event)
            if not moved:
                self.item_clicked.emit(index, int(event.modifiers().value))
            return
        self._pressed_control = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        # One release is already enough to open; never emit a second launch.
        event.accept()
