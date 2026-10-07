"""Qt tray integration; stop only hides the icon and never re-enters app shutdown."""
from pathlib import Path
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon, QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

class TrayIconManager(QObject):
    open_requested = Signal()
    dock_requested = Signal()
    refresh_requested = Signal()
    quit_requested = Signal()

    def __init__(self, base_dir):
        super().__init__()
        self.icon = QSystemTrayIcon(QIcon(str(Path(base_dir) / 'assets' / 'gamedock.ico')), self)
        self.icon.setToolTip('GameDock — Gry')
        self.menu = QMenu()
        action = self.menu.addAction('Otwórz gry')
        action.triggered.connect(self.open_requested)
        action = self.menu.addAction('Odśwież gry')
        action.triggered.connect(self.refresh_requested)
        self.dock_action = self.menu.addAction('Przycisk Gry na pulpicie')
        self.dock_action.setCheckable(True)
        self.dock_action.triggered.connect(lambda checked: self.dock_requested.emit())
        self.menu.addSeparator()
        action = self.menu.addAction('Zamknij GameDock')
        action.triggered.connect(self.quit_requested)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activate)

    def _activate(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open_requested.emit()

    def set_dock_visible(self, visible):
        self.dock_action.setChecked(visible)

    def start(self):
        self.icon.show()

    def stop(self):
        self.icon.hide()
