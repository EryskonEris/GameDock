"""Inventory the actual Explorer desktop and perform native Shell actions.

Call scan, icon and open methods from a COM-initialized STA worker.
DesktopItem contains values only; COM objects never leave the calling thread.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import uuid

from PIL import Image, ImageDraw, ImageOps
import pythoncom
import win32com.client
import win32gui
from win32com.shell import shell, shellcon


class DesktopUnavailableError(RuntimeError):
    """Explorer cannot provide a complete snapshot; retain the previous one."""


def canonical_identity(parsing_name: str) -> str:
    if parsing_name.startswith("::") or parsing_name.lower().startswith("shell:"):
        return parsing_name.casefold()
    return os.path.normcase(os.path.normpath(parsing_name))


@dataclass(frozen=True, slots=True)
class DesktopItem:
    id: str
    display_name: str
    parsing_name: str
    path: str | None
    kind: str
    modified: float = 0.0
    fingerprint: str = ""
    source: str = "desktop"
    can_rename: bool = True
    can_move: bool = True
    can_delete: bool = True
    can_copy: bool = True
    file_identity: str = ""

    @property
    def extension(self) -> str:
        return os.path.splitext(self.path or "")[1].casefold() if self.kind != "folder" else ""


_ATTRIBUTES = (
    shellcon.SFGAO_FILESYSTEM | shellcon.SFGAO_FOLDER
    | shellcon.SFGAO_CANRENAME | shellcon.SFGAO_CANMOVE
    | shellcon.SFGAO_CANDELETE | shellcon.SFGAO_CANCOPY
)


def _desktop_view():
    windows = win32com.client.Dispatch("Shell.Application").Windows()
    desktop = windows.FindWindowSW(
        shellcon.CSIDL_DESKTOP, None, shellcon.SWC_DESKTOP, 0,
        shellcon.SWFO_NEEDDISPATCH,
    )
    provider = desktop._oleobj_.QueryInterface(pythoncom.IID_IServiceProvider)
    browser = provider.QueryService(shell.SID_STopLevelBrowser, shell.IID_IShellBrowser)
    view = browser.QueryActiveShellView().QueryInterface(shell.IID_IFolderView)
    return view, view.GetFolder(shell.IID_IShellFolder)


def item_from_path(path: str, source: str = "desktop") -> DesktopItem:
    """Build a filesystem item without creating or modifying anything."""
    path = os.path.abspath(path)
    attrs = _ATTRIBUTES & ~shellcon.SFGAO_FOLDER
    if os.path.isdir(path):
        attrs |= shellcon.SFGAO_FOLDER
    name = os.path.basename(path)
    if os.path.splitext(name)[1].casefold() in {".lnk", ".url", ".website"}:
        name = os.path.splitext(name)[0]
    return _make_item(name, path, attrs, source)


def _make_item(name: str, parsing_name: str, attributes: int, source: str, icon_index: int = -1) -> DesktopItem:
    filesystem = bool(attributes & shellcon.SFGAO_FILESYSTEM)
    path = parsing_name if filesystem else None
    modified, file_identity, stat_signature = 0.0, "", ()
    if path:
        try:
            stat = os.stat(path, follow_symlinks=False)
            modified = stat.st_mtime
            stat_signature = (stat.st_mtime_ns, stat.st_size, getattr(stat, "st_file_attributes", 0))
            if stat.st_ino:
                file_identity = f"{stat.st_dev}:{stat.st_ino}"
        except OSError:
            stat_signature = ("unavailable",)
    if not filesystem:
        kind = "system"
    elif os.path.splitext(parsing_name)[1].casefold() in {".lnk", ".url", ".website"}:
        kind = "shortcut"
    elif attributes & shellcon.SFGAO_FOLDER:
        kind = "folder"
    else:
        kind = "file"
    item_id = canonical_identity(parsing_name)
    fingerprint = hashlib.blake2b(
        json.dumps((item_id, name, attributes, stat_signature, icon_index), ensure_ascii=False).encode("utf-8"),
        digest_size=16,
    ).hexdigest()
    return DesktopItem(
        id=item_id, display_name=name, parsing_name=parsing_name, path=path,
        kind=kind, modified=modified, fingerprint=fingerprint, source=source,
        can_rename=filesystem and bool(attributes & shellcon.SFGAO_CANRENAME),
        can_move=filesystem and bool(attributes & shellcon.SFGAO_CANMOVE),
        can_delete=filesystem and bool(attributes & shellcon.SFGAO_CANDELETE),
        can_copy=filesystem and bool(attributes & shellcon.SFGAO_CANCOPY),
        file_identity=file_identity,
    )


class DesktopScanner:
    def __init__(self, base_dir: str):
        self.base_dir = str(Path(base_dir).resolve())
        self.shortcuts_dir = os.path.join(self.base_dir, "Shortcuts")
        self._icon_cache: OrderedDict[tuple[str, str, int], bytes] = OrderedDict()

    def scan(self, include_extra: bool = False) -> list[DesktopItem]:
        view, count = None, None
        try:
            view, folder = _desktop_view()
            count = view.ItemCount(shellcon.SVGIO_ALLVIEW)
            items = []
            for index in range(count):
                # pywin32.Items is unimplemented; Item returns one SHITEMID.
                pidl = [view.Item(index)]
                name = folder.GetDisplayNameOf(pidl, shellcon.SHGDN_NORMAL)
                parsing_name = folder.GetDisplayNameOf(pidl, shellcon.SHGDN_FORPARSING)
                attrs = folder.GetAttributesOf([pidl], _ATTRIBUTES)
                icon_index = -1
                if not attrs & shellcon.SFGAO_FILESYSTEM:
                    try:
                        _, info = shell.SHGetFileInfo(pidl, 0, shellcon.SHGFI_PIDL | shellcon.SHGFI_SYSICONINDEX)
                        icon_index = info[1]
                    except (pythoncom.com_error, OSError):
                        pass
                items.append(_make_item(name, parsing_name, attrs, "desktop", icon_index))
            if view.ItemCount(shellcon.SVGIO_ALLVIEW) != count or len({item.id for item in items}) != count:
                raise DesktopUnavailableError("Pulpit zmienił się podczas odczytu; ponawiam synchronizację.")
        except DesktopUnavailableError:
            raise
        except Exception as exc:
            if view is not None and self._view_changed(view, count):
                # An item added or removed mid-scan makes Item() fail; retry soon.
                raise DesktopUnavailableError("Pulpit zmienił się podczas odczytu; ponawiam synchronizację.") from exc
            raise DesktopUnavailableError("Nie można teraz odczytać pulpitu z Eksploratora Windows.") from exc

        if include_extra and os.path.isdir(self.shortcuts_dir):
            seen = {item.id for item in items}
            try:
                with os.scandir(self.shortcuts_dir) as entries:
                    for entry in entries:
                        try:
                            if getattr(entry.stat(), "st_file_attributes", 0) & 6:
                                continue
                            item = item_from_path(entry.path, "shortcuts")
                            if item.id not in seen:
                                items.append(item)
                                seen.add(item.id)
                        except OSError:
                            continue
            except OSError:
                pass
        return items

    @staticmethod
    def _view_changed(view, count) -> bool:
        try:
            return view.ItemCount(shellcon.SVGIO_ALLVIEW) != count
        except Exception:
            return False

    def clear_icon_cache(self):
        """Forget rendered icons so a manual refresh re-reads them from Windows."""
        self._icon_cache.clear()

    def extract_icon(self, item: DesktopItem, size: int = 96) -> bytes:
        size = max(16, min(512, int(size)))
        key = (item.id, item.fingerprint, size)
        if key in self._icon_cache:
            self._icon_cache.move_to_end(key)
            return self._icon_cache[key]
        image, hicon = None, None
        try:
            pidl, _ = shell.SHParseDisplayName(item.parsing_name, 0, None)
            _, info = shell.SHGetFileInfo(pidl, 0, shellcon.SHGFI_PIDL | shellcon.SHGFI_SYSICONINDEX)
            hicon = _large_shell_icon(info[1])
            if not hicon:
                _, info = shell.SHGetFileInfo(
                    pidl, 0, shellcon.SHGFI_PIDL | shellcon.SHGFI_ICON | shellcon.SHGFI_LARGEICON,
                )
                hicon = info[0]
            if hicon:
                image = _icon_to_image(hicon, 256)
        except Exception:
            image = None
        finally:
            if hicon:
                win32gui.DestroyIcon(hicon)
        extracted = image is not None
        if image is None:
            image = _fallback_icon(item.kind)
        canvas = _fit_icon_image(image, size)
        output = io.BytesIO()
        canvas.save(output, format="PNG")
        result = output.getvalue()
        if extracted:
            # A placeholder is never remembered: the next request tries Windows again.
            self._icon_cache[key] = result
            while len(self._icon_cache) > 384:
                self._icon_cache.popitem(last=False)
        return result

    def open_item(self, item: DesktopItem, verb: str = "open") -> bool:
        try:
            pidl, _ = shell.SHParseDisplayName(item.parsing_name, 0, None)
            result = shell.ShellExecuteEx(
                fMask=(shellcon.SEE_MASK_INVOKEIDLIST | shellcon.SEE_MASK_FLAG_NO_UI
                       | 0x00000100),  # SEE_MASK_NOASYNC: worker has no message loop.
                lpIDList=pidl, lpVerb=verb, nShow=1,
            )
            handle = result.get("hProcess") if isinstance(result, dict) else None
            if handle:
                import win32api
                win32api.CloseHandle(handle)
            return True
        except Exception:
            return False

    def open_location(self, item: DesktopItem) -> bool:
        if not item.path:
            return self.open_item(item)
        try:
            pidl, _ = shell.SHParseDisplayName(item.path, 0, None)
            # A full item PIDL with zero children selects that item in its parent.
            shell.SHOpenFolderAndSelectItems(pidl, [], 0)
            return True
        except Exception:
            return False


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8),
    ]


def _large_shell_icon(index: int) -> int | None:
    """Retrieve a jumbo Shell icon and release the IImageList reference."""
    get_list = ctypes.WinDLL("shell32").SHGetImageList
    get_list.argtypes = [ctypes.c_int, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
    get_list.restype = ctypes.c_long
    iid = _GUID.from_buffer_copy(uuid.UUID("46eb5926-582e-4017-9fdf-e8998daa0950").bytes_le)
    image_list = ctypes.c_void_p()
    if get_list(4, ctypes.byref(iid), ctypes.byref(image_list)) < 0 or not image_list:
        return None
    table = ctypes.cast(image_list, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    get_icon = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_void_p, ctypes.c_int, ctypes.c_uint,
        ctypes.POINTER(wintypes.HICON),
    )(table[10])
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[2])
    icon = wintypes.HICON()
    try:
        return icon.value if get_icon(image_list, index, 1, ctypes.byref(icon)) >= 0 else None
    finally:
        release(image_list)


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 1)]


def _icon_to_image(hicon: int, size: int) -> Image.Image:
    """Draw into a top-down 32-bit DIB, preserving alpha and legacy masks."""
    user32, gdi32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
    user32.GetDC.argtypes, user32.GetDC.restype = [wintypes.HWND], wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.DrawIconEx.argtypes = [
        wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HICON,
        ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.HBRUSH, wintypes.UINT,
    ]
    gdi32.CreateCompatibleDC.argtypes, gdi32.CreateCompatibleDC.restype = [wintypes.HDC], wintypes.HDC
    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC, ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
    ]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes, gdi32.SelectObject.restype = [wintypes.HDC, wintypes.HANDLE], wintypes.HANDLE
    gdi32.DeleteObject.argtypes, gdi32.DeleteDC.argtypes = [wintypes.HANDLE], [wintypes.HDC]
    screen = user32.GetDC(None)
    dc = gdi32.CreateCompatibleDC(screen)
    bits, info = ctypes.c_void_p(), _BITMAPINFO()
    info.bmiHeader = _BITMAPINFOHEADER(
        ctypes.sizeof(_BITMAPINFOHEADER), size, -size, 1, 32, 0,
        size * size * 4, 0, 0, 0, 0,
    )
    bitmap = gdi32.CreateDIBSection(dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
    old = None
    try:
        if not bitmap or not bits:
            raise OSError("Nie można odczytać ikony Windows.")
        old = gdi32.SelectObject(dc, bitmap)
        ctypes.memset(bits, 0, size * size * 4)
        if not user32.DrawIconEx(dc, 0, 0, hicon, size, size, 0, None, 3):
            raise OSError("Nie można narysować ikony Windows.")
        image = Image.frombytes("RGBA", (size, size), ctypes.string_at(bits, size * size * 4), "raw", "BGRA")
        if image.getchannel("A").getextrema()[1] == 0:
            ctypes.memset(bits, 0, size * size * 4)
            user32.DrawIconEx(dc, 0, 0, hicon, size, size, 0, None, 1)
            mask = Image.frombytes("RGBA", (size, size), ctypes.string_at(bits, size * size * 4), "raw", "BGRA")
            image.putalpha(ImageOps.invert(mask.getchannel("R")))
        else:
            image = Image.frombytes("RGBa", image.size, image.tobytes()).convert("RGBA")
        return image
    finally:
        if old:
            gdi32.SelectObject(dc, old)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if dc:
            gdi32.DeleteDC(dc)
        if screen:
            user32.ReleaseDC(None, screen)


def _fit_icon_image(image: Image.Image, size: int) -> Image.Image:
    """Normalize the visible glyph, including tiny icons on jumbo canvases."""
    image = image.convert("RGBA")
    bounds = image.getchannel("A").getbbox()
    if bounds:
        image = image.crop(bounds)
    fitted = ImageOps.contain(image, (size, size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size))
    canvas.alpha_composite(fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2))
    return canvas


def _fallback_icon(kind: str) -> Image.Image:
    image = Image.new("RGBA", (96, 96))
    draw = ImageDraw.Draw(image)
    if kind == "folder":
        draw.rounded_rectangle((12, 25, 51, 43), 6, fill="#e7b75e")
        draw.rounded_rectangle((9, 35, 87, 80), 8, fill="#f4c76b")
    else:
        draw.rounded_rectangle((24, 11, 73, 85), 8, fill="#8cb9ea")
        for y in (39, 51, 63):
            draw.line((35, y, 61, y), fill="white", width=4)
    return image
