"""Pointer-safe Windows bridges for the Qt desktop surfaces."""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass

IS_WINDOWS = os.name == "nt"
_user32 = ctypes.WinDLL("user32", use_last_error=True) if IS_WINDOWS else None
_dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True) if IS_WINDOWS else None
_shcore = ctypes.WinDLL("shcore", use_last_error=True) if IS_WINDOWS else None
_custom_panels: set[int] = set()
_layered_panels: set[int] = set()


class _Margins(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int) for name in ("left", "right", "top", "bottom")]


class _HighContrast(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwFlags", wintypes.DWORD),
                ("lpszDefaultScheme", wintypes.LPWSTR)]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


class _NcCalcSizeParams(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


class _StyleStruct(ctypes.Structure):
    _fields_ = [("styleOld", wintypes.DWORD), ("styleNew", wintypes.DWORD)]


@dataclass(frozen=True)
class MonitorArea:
    """Monitor and usable rectangle in physical desktop pixels."""
    left: int
    top: int
    right: int
    bottom: int
    work_left: int
    work_top: int
    work_right: int
    work_bottom: int
    primary: bool = False

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom


if IS_WINDOWS:
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL
    _user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.FindWindowW.restype = wintypes.HWND
    _user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND,
                                    wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.FindWindowExW.restype = wintypes.HWND
    _EnumWindowProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows.argtypes = [_EnumWindowProc, wintypes.LPARAM]
    _user32.EnumWindows.restype = wintypes.BOOL
    _user32.GetParent.argtypes = [wintypes.HWND]
    _user32.GetParent.restype = wintypes.HWND
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _user32.GetCursorPos.restype = wintypes.BOOL
    _user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    _user32.ScreenToClient.restype = wintypes.BOOL
    _user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    _user32.SetWindowPos.restype = wintypes.BOOL
    _user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    _user32.MonitorFromPoint.restype = wintypes.HMONITOR
    _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.MonitorFromWindow.restype = wintypes.HMONITOR
    _user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MonitorInfo)]
    _user32.GetMonitorInfoW.restype = wintypes.BOOL
    _user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT,
                                            ctypes.c_void_p, wintypes.UINT]
    _user32.SystemParametersInfoW.restype = wintypes.BOOL
    _get_window_long = getattr(_user32, "GetWindowLongPtrW", _user32.GetWindowLongW)
    _get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
    _get_window_long.restype = ctypes.c_ssize_t
    _set_window_long = getattr(_user32, "SetWindowLongPtrW", _user32.SetWindowLongW)
    _set_window_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _set_window_long.restype = ctypes.c_ssize_t
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.IsZoomed.argtypes = [wintypes.HWND]
    _user32.IsZoomed.restype = wintypes.BOOL
    _dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                             ctypes.c_void_p, wintypes.DWORD]
    _dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
    _dwmapi.DwmIsCompositionEnabled.argtypes = [ctypes.POINTER(wintypes.BOOL)]
    _dwmapi.DwmIsCompositionEnabled.restype = ctypes.c_long
    _dwmapi.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND,
                                                   ctypes.POINTER(_Margins)]
    _dwmapi.DwmExtendFrameIntoClientArea.restype = ctypes.c_long
    if hasattr(_user32, "GetWindowDpiAwarenessContext"):
        _user32.GetWindowDpiAwarenessContext.argtypes = [wintypes.HWND]
        _user32.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
        _user32.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        _user32.AreDpiAwarenessContextsEqual.restype = wintypes.BOOL
    if hasattr(_user32, "GetDpiForWindow"):
        _user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        _user32.GetDpiForWindow.restype = wintypes.UINT
    _shcore.GetDpiForMonitor.argtypes = [wintypes.HMONITOR, ctypes.c_int,
                                       ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT)]
    _shcore.GetDpiForMonitor.restype = ctypes.c_long


def is_window(hwnd: int) -> bool:
    return bool(IS_WINDOWS and hwnd and _user32.IsWindow(hwnd))


def _composition_available() -> bool:
    if not IS_WINDOWS:
        return False
    composition = wintypes.BOOL()
    return _dwmapi.DwmIsCompositionEnabled(ctypes.byref(composition)) >= 0 and bool(composition.value)


def _high_contrast_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    high_contrast = _HighContrast()
    high_contrast.cbSize = ctypes.sizeof(high_contrast)
    success = _user32.SystemParametersInfoW(0x0042, high_contrast.cbSize,
                                          ctypes.byref(high_contrast), 0)
    return bool(success and high_contrast.dwFlags & 0x00000001)


def transparency_enabled() -> bool:
    """Whether Windows permits its blurred Acrylic material."""
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            if not winreg.QueryValueEx(key, "EnableTransparency")[0]:
                return False
    except OSError:
        pass
    return _composition_available() and not _high_contrast_enabled()


def _set_dwm(hwnd: int, attribute: int, value: int) -> bool:
    data = ctypes.c_uint32(value)
    return _dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data),
                                        ctypes.sizeof(data)) >= 0


def uses_per_pixel_transparency(hwnd: int) -> bool:
    """Whether the explicit Qt layered surface can draw the app's glass.

    WA_TranslucentBackground + FramelessWindowHint makes Qt's raster backend
    present premultiplied pixels via UpdateLayeredWindowIndirect/AC_SRC_ALPHA.
    A DWM bitmap-alpha setter on the ordinary GDI BitBlt path does not provide
    that compositor guarantee. Text and icons retain their own opaque alpha.
    """
    return bool(is_window(hwnd) and _get_window_long(hwnd, -20) & 0x00080000
                and _composition_available() and not _high_contrast_enabled())


def apply_acrylic(hwnd: int, glass: bool = True) -> bool:
    """Enable the requested glass surface, with native Acrylic where available.

    Qt must use premultiplied alpha with a native frame. ExpandedClientAreaHint
    is supported; FramelessWindowHint would instead create a layered HWND.
    When Windows disables its blurred material, the explicitly requested app
    glass still uses redirection alpha without blur. Icons and text retain their
    own opacity; no global opacity or Windows preferences are changed. High
    contrast, unavailable composition and older Windows use an opaque fallback.
    """
    if not is_window(hwnd):
        return False
    if _get_window_long(hwnd, -20) & 0x00080000:
        # Native backdrop materials are not supported by Qt's layered HWND.
        # The caller keeps the requested paint alpha using the separate helper
        # above, rather than interpreting unavailable Acrylic as opaque UI.
        _set_dwm(hwnd, 38, 1)
        _set_dwm(hwnd, 39, 0)
        _dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_Margins(0, 0, 0, 0)))
        return False
    _set_dwm(hwnd, 20, 1)
    _set_dwm(hwnd, 33, 2)
    compatible = sys.getwindowsversion().build >= 26100
    if (not glass or not compatible or not _composition_available()
            or _high_contrast_enabled()):
        _set_dwm(hwnd, 38, 1)
        _set_dwm(hwnd, 39, 0)
        return False
    alpha_ok = _set_dwm(hwnd, 39, 1)
    acrylic_ok = _set_dwm(hwnd, 38, 3 if transparency_enabled() else 1)
    margins = _Margins(-1, -1, -1, -1)
    frame_ok = _dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins)) >= 0
    if alpha_ok and acrylic_ok and frame_ok:
        return True
    _set_dwm(hwnd, 38, 1)
    _set_dwm(hwnd, 39, 0)
    return False


def configure_panel_chrome(hwnd: int) -> bool:
    """Remove native chrome without overriding Qt's chosen presentation mode.

    Frameless layered surfaces retain their explicit per-pixel compositor path.
    Older normal surfaces keep their existing custom-frame compatibility path.
    """
    if not is_window(hwnd):
        return False
    _custom_panels.add(hwnd)
    style = _get_window_long(hwnd, -16)
    layered = bool(_get_window_long(hwnd, -20) & 0x00080000)
    if layered:
        _layered_panels.add(hwnd)
        # Qt's frameless layered window already has a full-size client. Never
        # reintroduce native frame flags or caption margins into this surface.
        desired = style & ~(0x00C00000 | 0x00040000)
    else:
        _layered_panels.discard(hwnd)
        desired = (style & ~0x00C00000) | 0x00040000 | 0x00080000
    if style != desired:
        ctypes.set_last_error(0)
        previous = _set_window_long(hwnd, -16, desired)
        if not previous and ctypes.get_last_error():
            return False
        # Recalculate the client area immediately. The QWidget nativeEvent
        # bridge below suppresses the non-client band while retaining resizing.
        _user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                             0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
    # ExpandedClientAreaHint uses a separate Qt-native overlay for the system
    # title/caption buttons. This window owns its own header and close button.
    overlay = _user32.FindWindowExW(hwnd, None, "_q_titlebar", None)
    if overlay:
        _user32.ShowWindow(overlay, 0)
    _set_dwm(hwnd, 20, 1)
    _set_dwm(hwnd, 33, 2)
    _set_dwm(hwnd, 34, 0xFFFFFFFE)  # DWMWA_COLOR_NONE: no native border.
    if layered:
        _set_dwm(hwnd, 38, 1)
        _set_dwm(hwnd, 39, 0)
        _dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_Margins(0, 0, 0, 0)))
    return not bool(_get_window_long(hwnd, -16) & 0x00C00000)


prepare_panel_window = configure_panel_chrome


def place_panel_window(hwnd: int, x: int, y: int, width: int, height: int) -> bool:
    """Place the custom panel at an exact physical client/outer rectangle.

    Qt's cached caption margins can outlive the native caption removal. Adding
    those margins repeatedly makes saved sizes grow on each launch. Our full
    client-area native hook makes the outer rectangle equal to the client, so
    placement intentionally bypasses AdjustWindowRect and Qt frame conversion.
    """
    if not is_window(hwnd) or hwnd not in _custom_panels or width < 1 or height < 1:
        return False
    return bool(_user32.SetWindowPos(hwnd, None, int(x), int(y), int(width), int(height),
                                     0x0004 | 0x0010 | 0x0020))


def _panel_resize_hit(x: int, y: int, rect: tuple[int, int, int, int], dpi: int) -> int:
    """Physical coordinate hit test, separated for mixed-DPI verification."""
    left, top, right, bottom = rect
    if not (left <= x < right and top <= y < bottom):
        return 0  # HTNOWHERE
    border = max(6, round(7 * dpi / 96.0))
    at_left, at_right = x < left + border, x >= right - border
    at_top, at_bottom = y < top + border, y >= bottom - border
    if at_top:
        return 13 if at_left else 14 if at_right else 12
    if at_bottom:
        return 16 if at_left else 17 if at_right else 15
    return 10 if at_left else 11 if at_right else 1


def handle_panel_native_event(message) -> tuple[bool, int]:
    """Forward QWidget.nativeEvent's MSG pointer here before calling super()."""
    if not IS_WINDOWS:
        return False, 0
    try:
        address = int(message)
        if not address:
            return False, 0
        event = wintypes.MSG.from_address(address)
    except (TypeError, ValueError, OverflowError):
        return False, 0
    hwnd = int(event.hWnd or 0)
    if event.message == 0x0082:  # WM_NCDESTROY
        _custom_panels.discard(hwnd)
        _layered_panels.discard(hwnd)
        return False, 0
    if hwnd in _custom_panels:
        if event.message == 0x007C and ctypes.c_ssize_t(event.wParam).value == -16 and event.lParam:
            # Qt may restore its normal window flags in showNormal(). Amend
            # the proposed native style rather than racing another SetWindowLong.
            styles = _StyleStruct.from_address(event.lParam)
            if hwnd in _layered_panels:
                styles.styleNew &= ~(0x00C00000 | 0x00040000)
            else:
                styles.styleNew = (styles.styleNew & ~0x00C00000) | 0x00040000 | 0x00080000
            return True, 0
        if event.message == 0x0085:  # WM_NCPAINT: all content is drawn by Qt.
            return True, 0
        if event.message == 0x0086:  # WM_NCACTIVATE
            return True, 1
    if event.message == 0x0083:  # WM_NCCALCSIZE
        if event.wParam and event.lParam and _user32.IsZoomed(hwnd):
            monitor = _user32.MonitorFromWindow(hwnd, 2)
            info = _MonitorInfo()
            info.cbSize = ctypes.sizeof(info)
            if monitor and _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                params = _NcCalcSizeParams.from_address(event.lParam)
                params.rgrc[0] = info.rcWork
        return True, 0
    if event.message == 0x0084:  # WM_NCHITTEST
        rect = window_rect(hwnd)
        if rect is None:
            return False, 0
        if _user32.IsZoomed(hwnd):
            return True, 1  # HTCLIENT; custom header manages moving/restoring.
        x = ctypes.c_short(event.lParam & 0xFFFF).value
        y = ctypes.c_short((event.lParam >> 16) & 0xFFFF).value
        return True, _panel_resize_hit(x, y, rect, window_dpi(hwnd))
    return False, 0


def desktop_host() -> int:
    """Find the icon view without creating or rearranging WorkerW windows."""
    if not IS_WINDOWS:
        return 0
    progman = _user32.FindWindowW("Progman", None)
    view = _user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None) if progman else 0
    if view:
        return int(view)
    result = []

    @_EnumWindowProc
    def inspect(hwnd, _lparam):
        found = _user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
        if found:
            result.append(int(found))
            return False
        return True

    _user32.EnumWindows(inspect, 0)
    return result[0] if result else 0


def window_parent(hwnd: int) -> int:
    return int(_user32.GetParent(hwnd) or 0) if is_window(hwnd) else 0


def same_dpi_context(hwnd: int, parent: int) -> bool:
    if not (is_window(hwnd) and is_window(parent)):
        return False
    if not hasattr(_user32, "GetWindowDpiAwarenessContext"):
        return False
    own = _user32.GetWindowDpiAwarenessContext(hwnd)
    host = _user32.GetWindowDpiAwarenessContext(parent)
    return bool(own and host and _user32.AreDpiAwarenessContextsEqual(own, host))


def window_dpi(hwnd: int) -> int:
    if is_window(hwnd) and hasattr(_user32, "GetDpiForWindow"):
        return int(_user32.GetDpiForWindow(hwnd) or 96)
    return 96


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    if is_window(hwnd) and _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return rect.left, rect.top, rect.right, rect.bottom
    return None


def cursor_position() -> tuple[int, int] | None:
    point = wintypes.POINT()
    if IS_WINDOWS and _user32.GetCursorPos(ctypes.byref(point)):
        return point.x, point.y
    return None


def monitor_for_point(x: int, y: int) -> MonitorArea | None:
    if not IS_WINDOWS:
        return None
    handle = _user32.MonitorFromPoint(wintypes.POINT(x, y), 2)
    info = _MonitorInfo()
    info.cbSize = ctypes.sizeof(info)
    if not handle or not _user32.GetMonitorInfoW(handle, ctypes.byref(info)):
        return None
    monitor, work = info.rcMonitor, info.rcWork
    return MonitorArea(monitor.left, monitor.top, monitor.right, monitor.bottom,
                       work.left, work.top, work.right, work.bottom, bool(info.dwFlags & 1))


def monitor_dpi(x: int, y: int) -> int:
    """Effective DPI of the target monitor, including the user's scale setting.

    Unlike a desktop child HWND's inherited DPI, this follows the target point.
    Qt initializes this application as per-monitor aware, for which Shcore
    returns the display's actual effective DPI.
    """
    if not IS_WINDOWS:
        return 96
    monitor = _user32.MonitorFromPoint(wintypes.POINT(x, y), 2)
    dpi_x, dpi_y = wintypes.UINT(), wintypes.UINT()
    if monitor and _shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) >= 0:
        return int(dpi_x.value or 96)
    return 96


def place_desktop_child(hwnd: int, parent: int, x: int, y: int,
                        width: int, height: int) -> bool:
    """Move only our child, never Explorer or desktop top-level Z order."""
    if window_parent(hwnd) != parent:
        return False
    point = wintypes.POINT(x, y)
    if not _user32.ScreenToClient(parent, ctypes.byref(point)):
        return False
    return bool(_user32.SetWindowPos(hwnd, 0, point.x, point.y, width, height,
                                     0x0010))
