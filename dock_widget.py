"""A sharp, compact Qt raster button embedded in Explorer's desktop view."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QBackingStore, QColor, QFont, QGuiApplication,
                          QLinearGradient, QPainter, QPainterPath, QPen,
                          QRegion, QSurface, QSurfaceFormat, QWindow)
from PySide6.QtWidgets import QMenu

from native_windows import (cursor_position, desktop_host, is_window, monitor_dpi,
                            monitor_for_point, place_desktop_child,
                            same_dpi_context, window_parent,
                            window_rect)


class DockWindow(QWindow):
    activated = Signal()
    position_changed = Signal(int, int)
    lock_changed = Signal(bool)
    visibility_requested = Signal(bool)
    refresh_requested = Signal()
    quit_requested = Signal()
    WIDTH = 124
    HEIGHT = 42

    def __init__(self, base_dir: str, config_mgr):
        super().__init__()
        self.base_dir = base_dir
        self._locked = bool(config_mgr.get("dock_locked", False))
        self._wanted_visible = bool(config_mgr.get("dock_visible", True))
        try:
            self._saved_position = (int(config_mgr.get("dock_x", 1200)),
                                    int(config_mgr.get("dock_y", 1330)))
        except (TypeError, ValueError):
            self._saved_position = None
        self._host = 0
        self._foreign_parent = None
        self._native_handle = 0
        self._started = False
        self._recovering = False
        self._hover = False
        self._pressed = False
        self._moved = False
        self._press_cursor = None
        self._press_origin = None
        self._count = 0
        self._render_dpi = 96
        self._menu = None
        self.setTitle("GameDock — Gry")
        self.setFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
        self.setSurfaceType(QSurface.RasterSurface)
        surface_format = QSurfaceFormat()
        surface_format.setAlphaBufferSize(8)
        self.setFormat(surface_format)
        self.resize(self.WIDTH, self.HEIGHT)
        self.setMinimumSize(QSize(self.WIDTH, self.HEIGHT))
        self.setMaximumSize(QSize(self.WIDTH, self.HEIGHT))
        self.setCursor(Qt.PointingHandCursor)
        self._backing = QBackingStore(self)
        self._host_timer = QTimer(self)
        self._host_timer.setInterval(2000)
        self._host_timer.timeout.connect(self._check_host)
        application = QGuiApplication.instance()
        if application is not None:
            application.screenAdded.connect(self._screen_changed)
            application.screenRemoved.connect(self._screen_changed)
            for screen in application.screens():
                screen.availableGeometryChanged.connect(self._clamp_to_screen)

    def start(self):
        if self._started:
            return
        self._started = True
        self._host_timer.start()
        self.recover_host()

    def stop(self):
        self._started = False
        self._host_timer.stop()
        if self._menu is not None:
            self._menu.close()
        self.hide()
        self.destroy()
        self._native_handle = 0
        self._host = 0
        self.setParent(None)
        self._release_foreign()

    def set_count(self, count: int):
        self._count = max(0, int(count))
        self.setTitle(f"GameDock — Gry ({self._count})")

    def set_visible(self, visible: bool):
        self._wanted_visible = bool(visible)
        if self._wanted_visible and self._started:
            self.recover_host()
        else:
            self.hide()

    def set_locked(self, locked: bool):
        self._locked = bool(locked)

    def _release_foreign(self):
        foreign, self._foreign_parent = self._foreign_parent, None
        if foreign is not None:
            foreign.deleteLater()

    def recover_host(self):
        """Rebuild the native child after Explorer loss, without restarting it."""
        if not self._started or self._recovering:
            return
        self._recovering = True
        try:
            host = desktop_host()
            native_alive = is_window(self._native_handle)
            if (host and host == self._host and native_alive
                    and window_parent(self._native_handle) == host):
                self._place_saved()
                if self._wanted_visible:
                    self.show()
                    self.requestUpdate()
                return
            self.hide()
            # Explorer destroys its native children on exit. Discard Qt's old
            # platform window before rebuilding; do not reuse the stale HWND.
            self.destroy()
            self._native_handle = 0
            self.setParent(None)
            self._release_foreign()
            self._host = 0
            if not host:
                return
            self.create()
            self._native_handle = int(self.winId())
            if not same_dpi_context(self._native_handle, host):
                # Cross-process SetParent can reset the process's DPI awareness.
                self.destroy()
                self._native_handle = 0
                return
            foreign = QWindow.fromWinId(host)
            if foreign is None:
                self.destroy()
                self._native_handle = 0
                return
            self._foreign_parent = foreign
            self.setParent(foreign)
            self._native_handle = int(self.winId())
            if window_parent(self._native_handle) != host:
                self.hide()
                self.destroy()
                self._native_handle = 0
                self.setParent(None)
                self._release_foreign()
                return
            self._host = host
            self._backing.resize(self.size())
            self._place_saved()
            if self._wanted_visible:
                self.show()
                self.requestUpdate()
        finally:
            self._recovering = False

    def _check_host(self):
        if self._started:
            self.recover_host()

    def _physical_size(self, position=None):
        point = position or self._saved_position or cursor_position() or (0, 0)
        scale = monitor_dpi(*point) / 96.0
        return round(self.WIDTH * scale), round(self.HEIGHT * scale)

    def _clamped_position(self, position):
        point = position or cursor_position() or (0, 0)
        width, height = self._physical_size(point)
        monitor = monitor_for_point(*point)
        if monitor is None:
            return point
        if position is None or not monitor.contains(*position):
            scale = monitor_dpi(*point) / 96.0
            return (monitor.work_left + (monitor.work_right - monitor.work_left - width) // 2,
                    max(monitor.work_top, monitor.work_bottom - height - round(24 * scale)))
        return (max(monitor.work_left, min(position[0], monitor.work_right - width)),
                max(monitor.work_top, min(position[1], monitor.work_bottom - height)))

    def _place_saved(self):
        if self._host and is_window(self._native_handle):
            self._saved_position = self._clamped_position(self._saved_position)
            self._render_dpi = monitor_dpi(*self._saved_position)
            width, height = self._physical_size()
            # Native children inherit their parent's Qt screen/DPR. Allocate
            # enough pixels for the monitor the pill actually occupies and
            # paint the vector pill at that scale instead of stretching a bitmap.
            qt_dpr = max(1.0, self.devicePixelRatio())
            logical_size = QSize(round(width / qt_dpr), round(height / qt_dpr))
            self.setMinimumSize(logical_size)
            self.setMaximumSize(logical_size)
            if self.size() != logical_size:
                self.resize(logical_size)
            if self._backing.size() != logical_size:
                self._backing.resize(logical_size)
            place_desktop_child(self._native_handle, self._host,
                                *self._saved_position, width, height)

    def _screen_changed(self, screen):
        if screen in QGuiApplication.screens():
            screen.availableGeometryChanged.connect(self._clamp_to_screen)
        QTimer.singleShot(100, self._clamp_to_screen)

    def _clamp_to_screen(self, *_args):
        self._place_saved()

    def event(self, event):
        if event.type() == QEvent.UpdateRequest:
            self._render()
            return True
        if event.type() == QEvent.Enter:
            self._hover = True
            self.requestUpdate()
        elif event.type() == QEvent.Leave:
            self._hover = False
            self.requestUpdate()
        return super().event(event)

    def exposeEvent(self, event):
        if self.isExposed():
            self._render()

    def resizeEvent(self, event):
        self._backing.resize(event.size())
        self.requestUpdate()

    def _render(self):
        if not self.isExposed() or not is_window(self._native_handle):
            return
        rectangle = QRect(QPoint(), self.size())
        region = QRegion(rectangle)
        self._backing.beginPaint(region)
        painter = QPainter(self._backing.paintDevice())
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(rectangle, Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(self._render_dpi / (96.0 * self.devicePixelRatio()),
                      self._render_dpi / (96.0 * self.devicePixelRatio()))
        pill = QRectF(2.0, 2.0, self.WIDTH - 4.0, self.HEIGHT - 4.0)
        path = QPainterPath()
        path.addRoundedRect(pill, 19.0, 19.0)
        gradient = QLinearGradient(pill.topLeft(), pill.bottomLeft())
        if self._pressed:
            gradient.setColorAt(0, QColor(37, 49, 69, 245))
            gradient.setColorAt(1, QColor(23, 31, 46, 245))
        elif self._hover:
            gradient.setColorAt(0, QColor(66, 84, 111, 240))
            gradient.setColorAt(1, QColor(27, 40, 61, 240))
        else:
            gradient.setColorAt(0, QColor(45, 57, 78, 238))
            gradient.setColorAt(1, QColor(20, 28, 44, 238))
        painter.fillPath(path, gradient)
        painter.setPen(QPen(QColor(152, 184, 222, 175 if self._hover else 105), 1.0))
        painter.drawPath(path)
        painter.setClipPath(path)
        painter.setPen(QPen(QColor(220, 235, 255, 62), 1.0))
        painter.drawLine(QPoint(19, 3), QPoint(self.WIDTH - 19, 3))
        painter.setClipping(False)
        font = QFont("Segoe UI")
        font.setPixelSize(14)
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor("#f4f7ff"))
        painter.drawText(pill, Qt.AlignCenter, "Gry")
        painter.end()
        self._backing.endPaint()
        self._backing.flush(region, self)

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self._show_menu(event.globalPosition().toPoint())
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self._moved = False
            self._press_cursor = cursor_position()
            rect = window_rect(self._native_handle)
            self._press_origin = rect[:2] if rect else self._saved_position
            self.requestUpdate()
            event.accept()

    def mouseMoveEvent(self, event):
        if not self._pressed or not self._press_cursor or not self._press_origin:
            return
        current = cursor_position()
        if not current:
            return
        dx, dy = current[0] - self._press_cursor[0], current[1] - self._press_cursor[1]
        threshold = max(4, round(4 * monitor_dpi(*current) / 96.0))
        if self._moved or abs(dx) > threshold or abs(dy) > threshold:
            self._moved = True
            if not self._locked:
                self._saved_position = self._clamped_position((self._press_origin[0] + dx,
                                                                self._press_origin[1] + dy))
                self._place_saved()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or not self._pressed:
            return
        self._pressed = False
        self.requestUpdate()
        if self._moved and not self._locked and self._saved_position:
            self.position_changed.emit(*self._saved_position)
        elif not self._moved and QRect(QPoint(), self.size()).contains(event.position().toPoint()):
            self.activated.emit()
        self._press_cursor = None
        self._press_origin = None
        event.accept()

    def _show_menu(self, point):
        menu = QMenu()
        self._menu = menu
        menu.addAction("Otwórz gry", self.activated.emit)
        menu.addAction("Odśwież gry", self.refresh_requested.emit)
        menu.addSeparator()
        lock_action = menu.addAction("Zablokuj pozycję")
        lock_action.setCheckable(True)
        lock_action.setChecked(self._locked)
        lock_action.toggled.connect(self._toggle_lock)
        menu.addAction("Ukryj przycisk", lambda: self.visibility_requested.emit(False))
        menu.addSeparator()
        menu.addAction("Zamknij GameDock", self.quit_requested.emit)
        menu.setStyleSheet("QMenu { background: #192231; color: #e8eef8; border: 1px solid #3c4c64; "
                           "padding: 6px; } QMenu::item { padding: 7px 22px; border-radius: 5px; } "
                           "QMenu::item:selected { background: #304d71; } "
                           "QMenu::separator { height: 1px; background: #344155; margin: 5px; }")
        menu.aboutToHide.connect(lambda: self._menu_closed(menu))
        menu.popup(point)

    def _menu_closed(self, menu):
        if self._menu is menu:
            self._menu = None
        menu.deleteLater()

    def _toggle_lock(self, locked):
        self.set_locked(locked)
        self.lock_changed.emit(locked)
