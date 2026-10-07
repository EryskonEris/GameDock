"""Read-only diagnostics for the distributed application and its bundled runtime."""
from pathlib import Path
import logging
import sys
import traceback


def run(base_dir: Path) -> int:
    lines = ["GameDock — sprawdzenie instalacji", f"Folder: {base_dir}", f"Python: {sys.version.split()[0]}"]
    result = 0
    try:
        import pythoncom
        import PySide6
        from PySide6.QtGui import QIcon, QImage
        from PySide6.QtWidgets import QApplication
        from config_mgr import ConfigManager
        from file_operations import _new_operation
        from game_library import GameLibrary

        application = QApplication.instance() or QApplication([])
        lines.append(f"Qt/PySide6: {PySide6.__version__}; platforma: {application.platformName()}")
        if QIcon(str(base_dir / "assets" / "gamedock.ico")).isNull():
            raise RuntimeError("Brak ikony aplikacji. Wypakuj cały ZIP, razem z folderem assets.")
        lines.append("Ikona aplikacji: OK")
        config = ConfigManager(str(base_dir))
        lines.append("Odczyt ustawień: OK")
        pythoncom.CoInitialize()
        try:
            library = GameLibrary(str(base_dir))
            games = library.scan(
                include_extra=bool(config.get("include_shortcuts", False)),
                game_paths=config.get("game_paths", []),
                game_overrides=config.get("game_overrides", {}),
            )
            lines.append(f"Odczyt pulpitu Windows: OK; wykryte gry: {len(games)}")
            for item in games[:8]:
                if QImage.fromData(library.scanner.extract_icon(item, 96)).isNull():
                    raise RuntimeError(f"Nie można odczytać ikony: {item.display_name}")
            lines.append(f"Ładowanie ikon: OK (sprawdzone: {min(8, len(games))})")
            operation = _new_operation()
            del operation
            lines.append("Windows IFileOperation: OK (bez wykonywania operacji na plikach)")
        finally:
            pythoncom.CoUninitialize()
        lines.append("WYNIK: OK. Możesz uruchomić GameDock.exe.")
    except Exception:
        result = 1
        lines.append("WYNIK: BŁĄD\n" + traceback.format_exc())
        logging.getLogger("gamedock").exception("Sprawdzenie paczki nie powiodło się")
    (base_dir / "logs").mkdir(exist_ok=True)
    (base_dir / "logs" / "sprawdzenie.txt").write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return result
