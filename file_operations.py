"""Native, undoable Windows file operations for a COM-initialized STA worker."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
import re

import pythoncom
from win32com.server.exception import COMException
from win32com.server.policy import DesignatedWrapPolicy
from win32com.shell import shell, shellcon

from scanner import DesktopItem, canonical_identity

FOFX_RECYCLEONDELETE = 0x00080000
FOFX_ADDUNDORECORD = 0x20000000
_E_ABORT = -2147467260
_CANCEL_CODES = {0x800704C7, 0x80270000, 0x80270001}
# Shell's successful rename/move result can be DONT_PROCESS_CHILDREN rather
# than S_OK. Ignored and pending work must not be reported as completed.
_INCOMPLETE_CODES = {0x00270003, 0x00270004, 0x00270005, 0x0027000B,
                     0x0027000D, 0x0027000F, 0x00270010, 0x00270011}
_ALIASES = {
    "new_folder": "create_folder", "newfolder": "create_folder",
    "mkdir": "create_folder", "recycle": "delete",
}


@dataclass(frozen=True, slots=True)
class OperationRequest:
    action: str
    items: tuple[DesktopItem, ...] | list[DesktopItem]
    destination: str | None = None
    new_name: str | None = None
    owner_hwnd: int = 0

    def __post_init__(self):
        object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True, slots=True)
class OperationResult:
    action: str
    successful_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    id_mapping: dict[str, str] = field(default_factory=dict)


@dataclass
class _Outcome:
    successful_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    id_mapping: dict[str, str] = field(default_factory=dict)

    def error(self, message: str):
        if message not in self.errors:
            self.errors.append(message)


def _parsing_name(item) -> str:
    return item.GetDisplayName(shellcon.SIGDN_DESKTOPABSOLUTEPARSING)


def _is_cancel(code: int) -> bool:
    return code & 0xFFFFFFFF in _CANCEL_CODES


def _valid_name(name: str | None) -> bool:
    if not name or name in {".", ".."} or name != name.strip() or name[-1] == ".":
        return False
    if any(ord(char) < 32 or char in '<>:"/\\|?*' for char in name):
        return False
    if len(name.encode("utf-16-le")) // 2 > 255:
        return False
    return not re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]", name.split(".")[0], re.IGNORECASE)


class _ProgressSink(DesignatedWrapPolicy):
    _com_interfaces_ = [shell.IID_IFileOperationProgressSink]
    _public_methods_ = [
        "StartOperations", "FinishOperations", "PreRenameItem", "PostRenameItem",
        "PreMoveItem", "PostMoveItem", "PreCopyItem", "PostCopyItem",
        "PreDeleteItem", "PostDeleteItem", "PreNewItem", "PostNewItem",
        "UpdateProgress", "ResetTimer", "PauseTimer", "ResumeTimer",
    ]

    def __init__(self, action: str, outcome: _Outcome, source_ids: set[str] | None = None):
        self.action, self.outcome = action, outcome
        self.source_ids = source_ids
        self._wrap_(self)

    def _post(self, item, result: int, created=None):
        try:
            source_id = canonical_identity(_parsing_name(item)) if item is not None else None
            new_id = canonical_identity(_parsing_name(created)) if created is not None else None
        except Exception:
            source_id, new_id = None, None
        if not result & 0x80000000 and result not in _INCOMPLETE_CODES:
            # A folder operation also reports its children. UI results and
            # favorite mappings refer to the selected top-level items only.
            if source_id and self.source_ids is not None and source_id not in self.source_ids:
                return
            success_id = source_id or new_id
            if success_id and success_id not in self.outcome.successful_ids:
                self.outcome.successful_ids.append(success_id)
            if self.action in {"rename", "move"} and source_id and new_id:
                self.outcome.id_mapping[source_id] = new_id
        elif _is_cancel(result) or result in _INCOMPLETE_CODES:
            self.outcome.cancelled = True
        else:
            label = source_id or new_id or "element"
            self.outcome.error(f"Nie udało się wykonać operacji: {label} (0x{result & 0xFFFFFFFF:08X}).")

    def StartOperations(self): pass

    def FinishOperations(self, result):
        if _is_cancel(result):
            self.outcome.cancelled = True
        elif result < 0 and not self.outcome.errors:
            self.outcome.error(f"Windows nie ukończył operacji (0x{result & 0xFFFFFFFF:08X}).")

    def PreRenameItem(self, flags, item, new_name): pass

    def PostRenameItem(self, flags, item, new_name, result, created):
        self._post(item, result, created)

    def PreMoveItem(self, flags, item, destination, new_name): pass

    def PostMoveItem(self, flags, item, destination, new_name, result, created):
        self._post(item, result, created)

    def PreCopyItem(self, flags, item, destination, new_name): pass

    def PostCopyItem(self, flags, item, destination, new_name, result, created):
        self._post(item, result, created)

    def PreDeleteItem(self, flags, item):
        # Reject deletions without recycle intent. If recycling is unavailable,
        # the native permanent-delete warning remains enabled; no dialog is
        # auto-confirmed. Internal delete steps of a move use different policy.
        if self.action == "delete" and not flags & shellcon.TSF_DELETE_RECYCLE_IF_POSSIBLE:
            self.outcome.error("Windows nie może przenieść tego elementu do Kosza. Trwałe usunięcie zostało zablokowane.")
            self.outcome.cancelled = True
            raise COMException(
                desc="GameDock zezwala wyłącznie na usuwanie do Kosza.",
                scode=_E_ABORT,
            )

    def PostDeleteItem(self, flags, item, result, created):
        self._post(item, result, created)

    def PreNewItem(self, flags, destination, new_name): pass

    def PostNewItem(self, flags, destination, new_name, template, attributes, result, created):
        self._post(None, result, created)

    def UpdateProgress(self, total, done): pass
    def ResetTimer(self): pass
    def PauseTimer(self): pass
    def ResumeTimer(self): pass


def _new_operation():
    return pythoncom.CoCreateInstance(
        shell.CLSID_FileOperation, None, pythoncom.CLSCTX_INPROC_SERVER,
        shell.IID_IFileOperation,
    )


def _shell_item(path: str):
    return shell.SHCreateItemFromParsingName(path, None, shell.IID_IShellItem)


def _wrap_sink(sink):
    return pythoncom.WrapObject(sink, shell.IID_IFileOperationProgressSink)


def perform_operation(request: OperationRequest) -> OperationResult:
    """Queue and execute an explicit operation, retaining native conflict UI.

    This function never changes the current desktop during discovery, never
    auto-confirms a conflict, and never falls back to os.remove/shutil.rmtree.
    """
    action = _ALIASES.get(request.action, request.action)
    outcome = _Outcome()

    def result():
        return OperationResult(
            request.action, outcome.successful_ids, outcome.errors,
            outcome.cancelled, outcome.id_mapping,
        )

    if action not in {"rename", "move", "copy", "delete", "create_folder"}:
        outcome.error("Nieznana operacja na plikach.")
        return result()
    if action == "rename" and len(request.items) != 1:
        outcome.error("Zmiana nazwy wymaga wybrania jednego elementu.")
        return result()
    if action in {"rename", "create_folder"} and not _valid_name(request.new_name):
        outcome.error("Podaj poprawną nazwę bez znaków zastrzeżonych przez Windows.")
        return result()
    if action != "create_folder" and not request.items:
        outcome.error("Wybierz element do wykonania operacji.")
        return result()

    destination = request.destination
    if action == "create_folder" and not destination:
        try:
            destination = shell.SHGetKnownFolderPath(shellcon.FOLDERID_Desktop, 0, None)
        except Exception:
            outcome.error("Nie można ustalić lokalizacji pulpitu.")
            return result()
    if action in {"copy", "move", "create_folder"}:
        if not destination or not os.path.isdir(destination):
            outcome.error("Wybierz istniejący folder docelowy.")
            return result()
        destination = os.path.abspath(destination)

    valid_items = []
    for item in request.items:
        if not item.path or item.kind == "system" or not getattr(item, f"can_{action}", False):
            outcome.error(f"Ta operacja jest niedostępna dla: {item.display_name}.")
        elif not os.path.lexists(item.path):
            outcome.error(f"Element już nie istnieje: {item.display_name}.")
        else:
            valid_items.append(item)
    if action != "create_folder" and not valid_items:
        return result()

    operation, sink, cookie = None, None, None
    try:
        operation = _new_operation()
        if request.owner_hwnd:
            operation.SetOwnerWindow(request.owner_hwnd)
        flags = shellcon.FOF_ALLOWUNDO | FOFX_ADDUNDORECORD
        if action == "delete":
            flags |= FOFX_RECYCLEONDELETE | shellcon.FOF_WANTNUKEWARNING
        operation.SetOperationFlags(flags)
        sink = _ProgressSink(action, outcome, {item.id for item in valid_items})
        cookie = operation.Advise(_wrap_sink(sink))
        destination_item = _shell_item(destination) if destination else None
        if action == "create_folder":
            operation.NewItem(destination_item, 0x10, request.new_name, None, None)
        for item in valid_items:
            try:
                source_item = _shell_item(item.parsing_name)
                if action == "rename":
                    operation.RenameItem(source_item, request.new_name)
                elif action == "move":
                    operation.MoveItem(source_item, destination_item)
                elif action == "copy":
                    operation.CopyItem(source_item, destination_item)
                elif action == "delete":
                    operation.DeleteItem(source_item)
            except Exception as exc:
                outcome.error(f"Nie można przygotować operacji dla {item.display_name}: {exc}.")
        operation.PerformOperations()
        if operation.GetAnyOperationsAborted():
            outcome.cancelled = True
    except pythoncom.com_error as exc:
        if _is_cancel(exc.hresult):
            outcome.cancelled = True
        elif not outcome.errors:
            outcome.error(f"Windows nie wykonał operacji: {exc.strerror or exc}.")
    except Exception as exc:
        outcome.error(f"Nie udało się wykonać operacji: {exc}.")
    finally:
        if operation is not None and cookie is not None:
            try:
                operation.Unadvise(cookie)
            except pythoncom.com_error:
                pass
    return result()
