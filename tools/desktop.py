from __future__ import annotations

import ctypes
import hashlib
import struct
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from core.backend_schemas import (
    normalize_desktop_command_result,
    normalize_desktop_pointer_action,
    normalize_desktop_process_action,
    normalize_desktop_window_readiness,
)
from core.desktop_evidence import (
    build_desktop_evidence_bundle,
    collect_display_metadata,
    get_desktop_evidence_store,
    summarize_evidence_bundle,
)
from core.desktop_mapping import (
    action_point_from_mapping,
    build_desktop_coordinate_mapping,
    monitor_for_rect,
    rect_contains_point,
)
from core.desktop_matching import select_window_candidate
from core.desktop_recovery import classify_window_recovery_state, select_window_recovery_strategy
from core.desktop_scene import interpret_desktop_scene
from core.windows_opening import (
    choose_windows_open_strategy,
    classify_open_target,
    infer_open_request_preferences,
    open_target_signature,
)
from tools.desktop_backends import (
    create_screenshot_backend,
    create_ui_evidence_backend,
    create_window_backend,
    describe_backends,
    inspect_process_details,
    list_process_contexts,
    probe_process_context,
    probe_visual_stability,
    probe_window_readiness,
    launch_unowned_process,
    open_in_explorer,
    open_path_with_association,
    open_url_with_shell,
    run_bounded_command,
    start_owned_process,
    stop_owned_process,
)


DESKTOP_DEFAULT_WINDOW_LIMIT = 12
DESKTOP_DEFAULT_MAX_OBSERVATION_AGE_SECONDS = 45
DESKTOP_DEFAULT_TYPE_MAX_CHARS = 160
DESKTOP_DEFAULT_CAPTURE_MAX_WIDTH = 7680
DESKTOP_DEFAULT_CAPTURE_MAX_HEIGHT = 4320
DESKTOP_DEFAULT_CAPTURE_SCOPE = "primary_monitor"
DESKTOP_OBSERVATION_LIMIT = 24
DESKTOP_TOOL_NAMES = {
    "desktop_list_windows",
    "desktop_get_active_window",
    "desktop_focus_window",
    "desktop_capture_screenshot",
    "desktop_inspect_window_state",
    "desktop_recover_window",
    "desktop_wait_for_window_ready",
    "desktop_click_point",
    "desktop_move_mouse",
    "desktop_hover_point",
    "desktop_click_mouse",
    "desktop_scroll",
    "desktop_press_key",
    "desktop_press_key_sequence",
    "desktop_type_text",
    "desktop_list_processes",
    "desktop_inspect_process",
    "desktop_start_process",
    "desktop_stop_process",
    "desktop_run_command",
    "desktop_open_target",
}
DESKTOP_APPROVAL_TOOL_NAMES = {
    "desktop_click_point",
    "desktop_move_mouse",
    "desktop_hover_point",
    "desktop_click_mouse",
    "desktop_scroll",
    "desktop_press_key",
    "desktop_press_key_sequence",
    "desktop_type_text",
    "desktop_start_process",
    "desktop_stop_process",
    "desktop_run_command",
    "desktop_open_target",
}
DESKTOP_SENSITIVE_FIELD_TERMS = {
    "2fa",
    "auth code",
    "one-time code",
    "otp",
    "passcode",
    "password",
    "pin",
    "secret",
    "token",
    "verification code",
}
DESKTOP_DEFAULT_KEY_REPEAT = 1
DESKTOP_MAX_KEY_REPEAT = 4
DESKTOP_MAX_KEY_SEQUENCE_STEPS = 3
DESKTOP_DEFAULT_HOVER_MS = 600
DESKTOP_MAX_HOVER_MS = 2_000
DESKTOP_DEFAULT_SCROLL_UNITS = 3
DESKTOP_MAX_SCROLL_UNITS = 8
DESKTOP_DEFAULT_PROCESS_LIMIT = 8
DESKTOP_DEFAULT_VERIFICATION_SAMPLES = 3
DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS = 140
DESKTOP_MAX_PROCESS_LIMIT = 16
DESKTOP_DEFAULT_COMMAND_TIMEOUT_SECONDS = 8
DESKTOP_MAX_COMMAND_TIMEOUT_SECONDS = 20
DESKTOP_SAFE_KEY_VK = {
    "enter": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E,
    "del": 0x2E,
    "up": 0x26,
    "arrowup": 0x26,
    "down": 0x28,
    "arrowdown": 0x28,
    "left": 0x25,
    "arrowleft": 0x25,
    "right": 0x27,
    "arrowright": 0x27,
    "home": 0x24,
    "end": 0x23,
    "insert": 0x2D,
    "pageup": 0x21,
    "pagedown": 0x22,
    "a": 0x41,
    "c": 0x43,
    "f": 0x46,
    "v": 0x56,
    "x": 0x58,
    "y": 0x59,
    "z": 0x5A,
}
DESKTOP_SAFE_KEY_DISPLAY = {
    "enter": "Enter",
    "tab": "Tab",
    "esc": "Escape",
    "escape": "Escape",
    "space": "Space",
    "backspace": "Backspace",
    "delete": "Delete",
    "del": "Delete",
    "up": "ArrowUp",
    "arrowup": "ArrowUp",
    "down": "ArrowDown",
    "arrowdown": "ArrowDown",
    "left": "ArrowLeft",
    "arrowleft": "ArrowLeft",
    "right": "ArrowRight",
    "arrowright": "ArrowRight",
    "home": "Home",
    "end": "End",
    "insert": "Insert",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "a": "A",
    "c": "C",
    "f": "F",
    "v": "V",
    "x": "X",
    "y": "Y",
    "z": "Z",
}
DESKTOP_SAFE_MODIFIER_VK = {
    "ctrl": 0x11,
    "control": 0x11,
    "shift": 0x10,
}
DESKTOP_SAFE_MODIFIER_DISPLAY = {
    "ctrl": "Ctrl",
    "control": "Ctrl",
    "shift": "Shift",
}

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SW_RESTORE = 9
SW_SHOW = 5
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120
DWMWA_CLOAKED = 14
PROCESS_PER_MONITOR_DPI_AWARE = 2
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4


_DPI_AWARENESS_LOCK = threading.RLock()
_DPI_AWARENESS_STATE: Dict[str, Any] = {}


user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
try:
    dwmapi = ctypes.windll.dwmapi
except Exception:
    dwmapi = None
try:
    shcore = ctypes.windll.shcore
except Exception:
    shcore = None


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_long),
        ("y", ctypes.c_long),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", RGBQUAD * 1),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_uint16),
        ("wScan", ctypes.c_uint16),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_uint32),
        ("wParamL", ctypes.c_uint16),
        ("wParamH", ctypes.c_uint16),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("union", INPUT_UNION),
    ]


EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

user32.EnumWindows.argtypes = [EnumWindowsProc, ctypes.c_void_p]
user32.EnumWindows.restype = ctypes.c_bool
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.IsWindow.argtypes = [ctypes.c_void_p]
user32.IsWindow.restype = ctypes.c_bool
user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
user32.IsWindowVisible.restype = ctypes.c_bool
user32.IsIconic.argtypes = [ctypes.c_void_p]
user32.IsIconic.restype = ctypes.c_bool
user32.IsZoomed.argtypes = [ctypes.c_void_p]
user32.IsZoomed.restype = ctypes.c_bool
user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
user32.FindWindowW.restype = ctypes.c_void_p
user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = ctypes.c_bool
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
user32.GetWindowThreadProcessId.restype = ctypes.c_uint32
user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.ShowWindow.restype = ctypes.c_bool
user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
user32.BringWindowToTop.restype = ctypes.c_bool
user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
user32.SetForegroundWindow.restype = ctypes.c_bool
user32.SetFocus.argtypes = [ctypes.c_void_p]
user32.SetFocus.restype = ctypes.c_void_p
user32.AttachThreadInput.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_bool]
user32.AttachThreadInput.restype = ctypes.c_bool
user32.GetDC.argtypes = [ctypes.c_void_p]
user32.GetDC.restype = ctypes.c_void_p
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.ReleaseDC.restype = ctypes.c_int
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = ctypes.c_bool
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = ctypes.c_bool
user32.MapVirtualKeyW.argtypes = [ctypes.c_uint, ctypes.c_uint]
user32.MapVirtualKeyW.restype = ctypes.c_uint
user32.SendInput.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]
user32.SendInput.restype = ctypes.c_uint

kernel32.GetCurrentThreadId.restype = ctypes.c_uint32
kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
kernel32.QueryFullProcessImageNameW.restype = ctypes.c_bool
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.CloseHandle.restype = ctypes.c_bool

gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint32]
gdi32.BitBlt.restype = ctypes.c_bool
gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), ctypes.c_uint]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteObject.restype = ctypes.c_bool
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.restype = ctypes.c_bool


def _dpi_awareness_pointer(value: int) -> ctypes.c_void_p:
    bits = ctypes.sizeof(ctypes.c_void_p) * 8
    return ctypes.c_void_p(((1 << bits) + value) if value < 0 else value)


def _ensure_process_dpi_awareness() -> Dict[str, Any]:
    global _DPI_AWARENESS_STATE
    with _DPI_AWARENESS_LOCK:
        if _DPI_AWARENESS_STATE:
            return dict(_DPI_AWARENESS_STATE)
        state = {
            "enabled": False,
            "method": "unsupported",
            "reason": "unsupported",
            "summary": "Per-monitor DPI awareness is unavailable.",
        }
        try:
            set_awareness_context = getattr(user32, "SetProcessDpiAwarenessContext", None)
        except Exception:
            set_awareness_context = None
        if callable(set_awareness_context):
            try:
                result = bool(set_awareness_context(_dpi_awareness_pointer(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)))
                if result:
                    state = {
                        "enabled": True,
                        "method": "user32.SetProcessDpiAwarenessContext",
                        "reason": "active",
                        "summary": "Enabled per-monitor DPI awareness v2 for desktop mapping.",
                    }
                    _DPI_AWARENESS_STATE = dict(state)
                    return dict(_DPI_AWARENESS_STATE)
            except Exception:
                pass
        if shcore is not None:
            try:
                result = int(shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE))
                if result in {0, 0x80070005}:
                    state = {
                        "enabled": True,
                        "method": "shcore.SetProcessDpiAwareness",
                        "reason": "active",
                        "summary": "Using process-level per-monitor DPI awareness for desktop mapping.",
                    }
                    _DPI_AWARENESS_STATE = dict(state)
                    return dict(_DPI_AWARENESS_STATE)
            except Exception:
                pass
        try:
            set_process_dpi_aware = getattr(user32, "SetProcessDPIAware", None)
        except Exception:
            set_process_dpi_aware = None
        if callable(set_process_dpi_aware):
            try:
                if bool(set_process_dpi_aware()):
                    state = {
                        "enabled": True,
                        "method": "user32.SetProcessDPIAware",
                        "reason": "active",
                        "summary": "Using system DPI awareness for desktop mapping.",
                    }
            except Exception:
                pass
        _DPI_AWARENESS_STATE = dict(state)
        return dict(_DPI_AWARENESS_STATE)


_ensure_process_dpi_awareness()


_DESKTOP_OBSERVATIONS: Dict[str, Dict[str, Any]] = {}
_OBSERVATION_LOCK = threading.RLock()
_OBSERVATION_COUNTER = 0
_BACKEND_LOCK = threading.RLock()
_WINDOW_BACKEND = None
_SCREENSHOT_BACKEND = None
_UI_EVIDENCE_BACKEND = None


def _trim_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _coerce_int(value: Any, default: int, *, minimum: int = 0, maximum: int = 10_000) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _hex_hwnd(hwnd: int) -> str:
    try:
        return f"0x{int(hwnd):08X}"
    except Exception:
        return ""


def _parse_hwnd(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        if text.lower().startswith("0x"):
            return int(text, 16)
        return int(text)
    except Exception:
        return 0


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _get_window_text(hwnd: int) -> str:
    try:
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(max(1, length + 1))
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return _trim_text(buffer.value, limit=180)
    except Exception:
        return ""


def _get_class_name(hwnd: int) -> str:
    try:
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buffer, len(buffer))
        return _trim_text(buffer.value, limit=120)
    except Exception:
        return ""


def _get_process_name(pid: int) -> str:
    if pid <= 0:
        return ""
    handle = None
    try:
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        buffer_size = ctypes.c_uint32(512)
        buffer = ctypes.create_unicode_buffer(buffer_size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(buffer_size)):
            return _trim_text(Path(buffer.value).name, limit=120)
    except Exception:
        return ""
    finally:
        if handle:
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass
    return ""


def _is_window_cloaked(hwnd: int) -> bool:
    if dwmapi is None:
        return False
    cloaked = ctypes.c_int(0)
    try:
        result = dwmapi.DwmGetWindowAttribute(
            ctypes.c_void_p(hwnd),
            ctypes.c_uint(DWMWA_CLOAKED),
            ctypes.byref(cloaked),
            ctypes.sizeof(cloaked),
        )
        return result == 0 and bool(cloaked.value)
    except Exception:
        return False


def _window_rect(hwnd: int) -> Dict[str, int]:
    rect = RECT()
    if not user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
        return {"x": 0, "y": 0, "width": 0, "height": 0}
    width = max(0, int(rect.right - rect.left))
    height = max(0, int(rect.bottom - rect.top))
    return {
        "x": int(rect.left),
        "y": int(rect.top),
        "width": width,
        "height": height,
    }


def _window_info(hwnd: int, *, display: Dict[str, Any] | None = None) -> Dict[str, Any]:
    handle = int(hwnd or 0)
    if handle <= 0 or not user32.IsWindow(ctypes.c_void_p(handle)):
        return {}

    title = _get_window_text(handle)
    rect = _window_rect(handle)
    monitor_metadata = _window_monitor_metadata(rect, display=display)
    pid = ctypes.c_uint32(0)
    user32.GetWindowThreadProcessId(ctypes.c_void_p(handle), ctypes.byref(pid))
    active_hwnd = int(user32.GetForegroundWindow() or 0)
    return {
        "window_id": _hex_hwnd(handle),
        "title": title,
        "class_name": _get_class_name(handle),
        "pid": int(pid.value),
        "process_name": _get_process_name(int(pid.value)),
        "rect": rect,
        "is_active": handle == active_hwnd,
        "is_visible": bool(user32.IsWindowVisible(ctypes.c_void_p(handle))),
        "is_minimized": bool(user32.IsIconic(ctypes.c_void_p(handle))),
        "is_maximized": bool(user32.IsZoomed(ctypes.c_void_p(handle))),
        "is_cloaked": _is_window_cloaked(handle),
        **monitor_metadata,
    }


def _window_is_listable(hwnd: int, *, include_minimized: bool, include_hidden: bool = False) -> bool:
    handle = int(hwnd or 0)
    if handle <= 0:
        return False
    visible = bool(user32.IsWindowVisible(ctypes.c_void_p(handle)))
    cloaked = _is_window_cloaked(handle)
    if not include_hidden and not visible:
        return False
    if not include_hidden and cloaked:
        return False
    if not include_minimized and user32.IsIconic(ctypes.c_void_p(handle)):
        return False
    title = _get_window_text(handle)
    if not title:
        return False
    rect = _window_rect(handle)
    if include_hidden:
        return True
    return rect["width"] >= 40 and rect["height"] >= 30


def _enum_windows_native(
    *,
    include_minimized: bool = False,
    include_hidden: bool = False,
    limit: int = DESKTOP_DEFAULT_WINDOW_LIMIT,
) -> List[Dict[str, Any]]:
    windows: List[Dict[str, Any]] = []
    display = _display_metadata()

    @EnumWindowsProc
    def callback(hwnd, _lparam):
        if len(windows) >= limit:
            return False
        if _window_is_listable(int(hwnd), include_minimized=include_minimized, include_hidden=include_hidden):
            info = _window_info(int(hwnd), display=display)
            if info:
                windows.append(info)
        return True

    user32.EnumWindows(callback, 0)
    windows.sort(key=lambda item: (not item.get("is_active", False), item.get("title", "").lower()))
    return windows[:limit]


def _active_window_info_native() -> Dict[str, Any]:
    hwnd = int(user32.GetForegroundWindow() or 0)
    return _window_info(hwnd, display=_display_metadata())


def _get_window_backend():
    global _WINDOW_BACKEND
    with _BACKEND_LOCK:
        if _WINDOW_BACKEND is None:
            _WINDOW_BACKEND = create_window_backend(
                list_delegate=_enum_windows_native,
                active_delegate=_active_window_info_native,
                focus_delegate=_focus_window_handle_native,
            )
        return _WINDOW_BACKEND


def _get_screenshot_backend():
    global _SCREENSHOT_BACKEND
    with _BACKEND_LOCK:
        if _SCREENSHOT_BACKEND is None:
            _SCREENSHOT_BACKEND = create_screenshot_backend(capture_delegate=_capture_bitmap_native)
        return _SCREENSHOT_BACKEND


def _get_ui_evidence_backend():
    global _UI_EVIDENCE_BACKEND
    with _BACKEND_LOCK:
        if _UI_EVIDENCE_BACKEND is None:
            _UI_EVIDENCE_BACKEND = create_ui_evidence_backend()
        return _UI_EVIDENCE_BACKEND


def get_desktop_backend_status() -> Dict[str, Any]:
    status = describe_backends(
        window_backend=_get_window_backend(),
        screenshot_backend=_get_screenshot_backend(),
        ui_evidence_backend=_get_ui_evidence_backend(),
    )
    status["display"] = _display_metadata()
    status["mapping"] = {
        "dpi_awareness": _ensure_process_dpi_awareness(),
        "coordinate_space": "physical_pixels",
        "primary_monitor_policy": "full_primary_first",
    }
    try:
        store = get_desktop_evidence_store()
        status["evidence_store"] = {
            **store.status_snapshot(),
            "recent_summaries": store.recent_summaries(limit=4),
        }
    except Exception:
        status["evidence_store"] = {"bundle_count": 0, "root": "", "latest": {}}
    return status


def probe_ui_evidence(*, target: str = "active_window", limit: int = 8) -> Dict[str, Any]:
    return _get_ui_evidence_backend().probe(target=target, limit=limit)


def _window_probe_target(window: Dict[str, Any], fallback: str = "active_window") -> str:
    if not isinstance(window, dict):
        return fallback
    title = str(window.get("title", "")).strip()
    return title or fallback


def _metadata_readiness_for_window(window: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(window, dict) or not window.get("window_id"):
        return {}
    title = str(window.get("title", "")).strip()
    rect = window.get("rect", {}) if isinstance(window.get("rect", {}), dict) else {}
    width = max(0, int(rect.get("width", 0) or 0))
    height = max(0, int(rect.get("height", 0) or 0))
    visible = bool(window.get("is_visible", False))
    minimized = bool(window.get("is_minimized", False))
    hidden = bool((not visible) or bool(window.get("is_cloaked", False)))
    withdrawn = bool(hidden and width <= 4 and height <= 4)
    if minimized:
        return normalize_desktop_window_readiness(
            {
                "state": "not_ready",
                "ready": False,
                "visible": visible,
                "enabled": False,
                "focused": bool(window.get("is_active", False)),
                "interactable": False,
                "target": _window_probe_target(window),
                "target_window_id": str(window.get("window_id", "")).strip(),
                "window_title": title,
                "control_count": 0,
                "backend": "window_metadata",
                "reason": "target_minimized",
                "summary": f"'{title or 'The target window'}' is minimized, so bounded readiness probing should restore it before deeper inspection.",
            }
        )
    if withdrawn:
        return normalize_desktop_window_readiness(
            {
                "state": "missing",
                "ready": False,
                "visible": False,
                "enabled": False,
                "focused": False,
                "interactable": False,
                "target": _window_probe_target(window),
                "target_window_id": str(window.get("window_id", "")).strip(),
                "window_title": title,
                "control_count": 0,
                "backend": "window_metadata",
                "reason": "target_withdrawn",
                "summary": f"'{title or 'The target window'}' looks withdrawn or tray-like, so bounded readiness probing should stop and report it instead of waiting.",
            }
        )
    if hidden:
        return normalize_desktop_window_readiness(
            {
                "state": "not_ready",
                "ready": False,
                "visible": False,
                "enabled": False,
                "focused": bool(window.get("is_active", False)),
                "interactable": False,
                "target": _window_probe_target(window),
                "target_window_id": str(window.get("window_id", "")).strip(),
                "window_title": title,
                "control_count": 0,
                "backend": "window_metadata",
                "reason": "target_hidden",
                "summary": f"'{title or 'The target window'}' is hidden or cloaked, so bounded readiness probing should recover it before deeper inspection.",
            }
        )
    return {}


def _readiness_probe_for_window(window: Dict[str, Any], *, limit: int = 8) -> Dict[str, Any]:
    if not isinstance(window, dict) or not window.get("window_id"):
        return {}
    metadata_readiness = _metadata_readiness_for_window(window)
    if metadata_readiness:
        return metadata_readiness
    result = probe_window_readiness(
        target=_window_probe_target(window),
        window_id=str(window.get("window_id", "")).strip(),
        limit=max(1, min(12, int(limit or 8))),
    )
    return result.get("data", {}) if isinstance(result.get("data", {}), dict) else {}


def _visual_stability_for_window(window: Dict[str, Any], *, samples: int = 3, interval_ms: int = 120) -> Dict[str, Any]:
    if not isinstance(window, dict):
        return {}
    rect = window.get("rect", {}) if isinstance(window.get("rect", {}), dict) else {}
    width = max(0, int(rect.get("width", 0) or 0))
    height = max(0, int(rect.get("height", 0) or 0))
    if width <= 0 or height <= 0:
        return {}
    result = probe_visual_stability(
        x=int(rect.get("x", 0) or 0),
        y=int(rect.get("y", 0) or 0),
        width=min(width, DESKTOP_DEFAULT_CAPTURE_MAX_WIDTH),
        height=min(height, DESKTOP_DEFAULT_CAPTURE_MAX_HEIGHT),
        samples=max(2, min(4, int(samples or 3))),
        interval_ms=max(40, min(400, int(interval_ms or 120))),
    )
    return result.get("data", {}) if isinstance(result.get("data", {}), dict) else {}


def _process_context_for_window(window: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(window, dict):
        return {}
    result = probe_process_context(
        pid=_coerce_int(window.get("pid", 0), 0, minimum=0, maximum=10_000_000),
        process_name=str(window.get("process_name", "")).strip(),
    )
    return result.get("data", {}) if isinstance(result.get("data", {}), dict) else {}


def _inspect_window_state_internal(
    args: Dict[str, Any],
    *,
    source_action: str,
    include_ui_evidence: bool = True,
    include_visual_stability: bool = True,
    readiness_mode: str = "full",
) -> Dict[str, Any]:
    limit = max(8, _coerce_int(args.get("limit", 16), 16, minimum=4, maximum=30))
    target_window, candidates, lookup_error, match_info = _find_window(args)
    visible_windows = _enum_windows(limit=min(limit, DESKTOP_DEFAULT_WINDOW_LIMIT))
    active_window = _active_window_info()
    probe_window = target_window or active_window
    normalized_readiness_mode = str(readiness_mode or "full").strip().lower()
    if normalized_readiness_mode == "metadata_only":
        readiness = _metadata_readiness_for_window(probe_window)
    else:
        readiness = _readiness_probe_for_window(
            probe_window,
            limit=_coerce_int(args.get("ui_limit", 8), 8, minimum=1, maximum=16),
        )
    visual_stability = (
        _visual_stability_for_window(
            probe_window,
            samples=_coerce_int(args.get("stability_samples", 3), 3, minimum=2, maximum=4),
            interval_ms=_coerce_int(args.get("stability_interval_ms", 120), 120, minimum=40, maximum=400),
        )
        if include_visual_stability
        else {}
    )
    fallback_process_context = match_info.get("process_context", {}) if isinstance(match_info.get("process_context", {}), dict) else {}
    process_context = fallback_process_context if fallback_process_context and not target_window else _process_context_for_window(target_window or active_window)
    observation = _register_observation(active_window=active_window, windows=visible_windows)
    errors = [lookup_error] if lookup_error else []
    evidence_bundle, evidence_ref = _record_desktop_evidence(
        source_action=source_action,
        active_window=active_window,
        windows=visible_windows,
        observation_token=str(observation.get("observation_token", "")).strip(),
        target_window=target_window,
        include_ui_evidence=include_ui_evidence,
        ui_limit=_coerce_int(args.get("ui_limit", 8), 8, minimum=1, maximum=16),
        errors=errors,
    )
    recovery = classify_window_recovery_state(
        requested_title=str(args.get("title", "") or args.get("match", "")).strip(),
        requested_window_id=str(args.get("window_id", "")).strip(),
        target_window=target_window,
        active_window=active_window,
        candidate_count=len(candidates),
        candidate_preview=match_info.get("candidate_preview", []),
        readiness=readiness,
        visual_stability=visual_stability,
        expected_window_id=str(args.get("expected_window_id", "")).strip(),
        expected_window_title=str(args.get("expected_window_title", "")).strip(),
        match_score=int(match_info.get("top_score", 0) or 0),
        match_confidence=str(match_info.get("confidence", "")).strip(),
        match_kind=str(match_info.get("match_kind", "")).strip(),
        match_engine=str(match_info.get("match_engine", "")).strip(),
        match_reason=str(match_info.get("summary", "")).strip(),
        backend=str(get_desktop_backend_status().get("window", {}).get("active", "desktop") or "desktop"),
    )
    recovery_strategy = select_window_recovery_strategy(
        recovery,
        attempt_count=_coerce_int(args.get("attempt_count", 0), 0, minimum=0, maximum=8),
        max_attempts=_coerce_int(args.get("max_attempts", 2), 2, minimum=0, maximum=4),
    )
    recovery_view = dict(recovery)
    recovery_view.update(recovery_strategy)
    process_summary = str(process_context.get("summary", "")).strip()
    recovery_summary = str(recovery_view.get("summary", "")).strip()
    if process_summary and process_summary not in recovery_summary:
        recovery_view["summary"] = f"{recovery_summary} Process check: {process_summary}".strip()
    evidence_summary = summarize_evidence_bundle(evidence_bundle) if evidence_bundle else {}
    try:
        recent_summaries = get_desktop_evidence_store().recent_context_summaries(
            limit=3,
            active_window_title=str((target_window or active_window).get("title", "")).strip(),
            checkpoint_target=str(args.get("title", "") or args.get("match", "")).strip(),
        )
    except Exception:
        recent_summaries = []
    scene = interpret_desktop_scene(
        selected_summary=evidence_summary,
        recent_summaries=recent_summaries,
        purpose="desktop_investigation",
        prompt_text="",
        assessment={},
        recovery=recovery_view,
        readiness=readiness,
        visual_stability=visual_stability,
        process_context=process_context,
        pending_tool="",
        checkpoint_pending=False,
    )
    return {
        "target_window": target_window,
        "candidates": candidates,
        "lookup_error": lookup_error,
        "active_window": active_window,
        "windows": visible_windows,
        "observation": observation,
        "evidence_bundle": evidence_bundle,
        "evidence_ref": evidence_ref,
        "readiness": readiness,
        "visual_stability": visual_stability,
        "process_context": process_context,
        "match_info": match_info,
        "recovery": recovery_view,
        "scene": scene,
    }


def _latest_evidence_ref_for_observation(token: str) -> Dict[str, Any]:
    try:
        return get_desktop_evidence_store().find_by_observation_token(token)
    except Exception:
        return {}


def _evidence_ref_has_screenshot(evidence_ref: Dict[str, Any] | None) -> bool:
    ref = evidence_ref if isinstance(evidence_ref, dict) else {}
    if bool(ref.get("has_screenshot", False) or ref.get("has_artifact", False)):
        return True
    evidence_id = str(ref.get("evidence_id", "")).strip()
    if not evidence_id:
        return False
    try:
        bundle = get_desktop_evidence_store().load_bundle(evidence_id)
    except Exception:
        bundle = {}
    if not isinstance(bundle, dict):
        return False
    screenshot = bundle.get("screenshot", {}) if isinstance(bundle.get("screenshot", {}), dict) else {}
    artifacts = bundle.get("artifacts", {}) if isinstance(bundle.get("artifacts", {}), dict) else {}
    return bool(str(screenshot.get("path", "")).strip() or str(artifacts.get("screenshot_path", "")).strip())


def _record_desktop_evidence(
    *,
    source_action: str,
    active_window: Dict[str, Any],
    windows: List[Dict[str, Any]],
    observation_token: str = "",
    screenshot: Dict[str, Any] | None = None,
    target_window: Dict[str, Any] | None = None,
    include_ui_evidence: bool = False,
    ui_limit: int = 8,
    errors: List[str] | None = None,
    bundle_metadata: Dict[str, Any] | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    ui_result: Dict[str, Any] = {}
    collected_errors = list(errors or [])
    if include_ui_evidence:
        probe_target = str(active_window.get("title", "") or "active_window").strip() or "active_window"
        ui_result = probe_ui_evidence(target=probe_target, limit=ui_limit)
        if not ui_result.get("ok", False) and ui_result.get("error"):
            collected_errors.append(str(ui_result.get("error", "")).strip())

    screen = collect_display_metadata(_virtual_screen_rect())
    bundle = build_desktop_evidence_bundle(
        source_action=source_action,
        active_window=active_window,
        windows=windows,
        observation_token=observation_token,
        screenshot=screenshot or {},
        ui_evidence=(ui_result.get("data", {}) if isinstance(ui_result.get("data", {}), dict) else {}),
        target_window=target_window or {},
        screen=screen,
        errors=collected_errors,
        capture_mode=str((bundle_metadata or {}).get("capture_mode", "")).strip(),
        importance=str((bundle_metadata or {}).get("importance", "")).strip(),
        importance_reason=str((bundle_metadata or {}).get("importance_reason", "")).strip(),
        state_scope_id=str((bundle_metadata or {}).get("state_scope_id", "")).strip(),
        task_id=str((bundle_metadata or {}).get("task_id", "")).strip(),
        task_status=str((bundle_metadata or {}).get("task_status", "")).strip(),
        checkpoint_pending=bool((bundle_metadata or {}).get("checkpoint_pending", False)),
        checkpoint_tool=str((bundle_metadata or {}).get("checkpoint_tool", "")).strip(),
        checkpoint_target=str((bundle_metadata or {}).get("checkpoint_target", "")).strip(),
        capture_signature=str((bundle_metadata or {}).get("capture_signature", "")).strip(),
    )
    try:
        evidence_ref = get_desktop_evidence_store().record_bundle(bundle)
        bundle["bundle_path"] = evidence_ref.get("bundle_path", "")
        bundle["artifacts"] = {
            **(bundle.get("artifacts", {}) if isinstance(bundle.get("artifacts", {}), dict) else {}),
            "bundle_path": evidence_ref.get("bundle_path", ""),
        }
        return bundle, evidence_ref
    except Exception:
        return bundle, {}


def _enum_windows(
    *,
    include_minimized: bool = False,
    include_hidden: bool = False,
    limit: int = DESKTOP_DEFAULT_WINDOW_LIMIT,
) -> List[Dict[str, Any]]:
    display = _display_metadata()
    result = _get_window_backend().list_windows(include_minimized=include_minimized, limit=limit)
    data = result.get("data", {}) if isinstance(result, dict) else {}
    windows = data.get("windows", []) if isinstance(data, dict) else []
    filtered: List[Dict[str, Any]] = []
    if isinstance(windows, list) and windows:
        for item in windows:
            if not isinstance(item, dict):
                continue
            if not include_hidden and not bool(item.get("is_visible", False)):
                continue
            if not include_hidden and bool(item.get("is_cloaked", False)):
                continue
            filtered.append(_enrich_window_monitor_metadata(item, display=display))

    if include_hidden:
        native_windows = _enum_windows_native(
            include_minimized=include_minimized,
            include_hidden=True,
            limit=max(limit, len(filtered) + 6),
        )
        merged: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        for source in (native_windows, filtered):
            for item in source:
                if not isinstance(item, dict):
                    continue
                window_id = str(item.get("window_id", "")).strip()
                dedupe_key = window_id or f"{item.get('title', '')}|{item.get('pid', '')}"
                if dedupe_key in seen_ids:
                    continue
                seen_ids.add(dedupe_key)
                merged.append(_enrich_window_monitor_metadata(item, display=display))
        merged.sort(
            key=lambda item: (
                not bool(item.get("is_active", False)),
                not bool(item.get("is_visible", False)),
                bool(item.get("is_minimized", False)),
                item.get("title", "").lower(),
            )
        )
        return merged[:limit]

    if filtered:
        return filtered[:limit]
    return _enum_windows_native(include_minimized=include_minimized, include_hidden=False, limit=limit)


def _find_window_by_exact_title_native(title: str) -> Dict[str, Any]:
    requested = str(title or "").strip()
    if not requested:
        return {}
    try:
        hwnd = int(user32.FindWindowW(None, requested) or 0)
    except Exception:
        hwnd = 0
    return _window_info(hwnd) if hwnd > 0 else {}


def _active_window_info() -> Dict[str, Any]:
    display = _display_metadata()
    result = _get_window_backend().get_active_window()
    data = result.get("data", {}) if isinstance(result, dict) else {}
    active_window = data.get("active_window", {}) if isinstance(data, dict) else {}
    if not isinstance(active_window, dict):
        return {}
    return _enrich_window_monitor_metadata(active_window, display=display)


def _find_window(args: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]], str, Dict[str, Any]]:
    window_id = _parse_hwnd(args.get("window_id", ""))
    requested_title = str(args.get("title", "") or args.get("match", "")).strip()
    requested_process_name = str(args.get("process_name", "") or args.get("expected_process_name", "")).strip()
    requested_class_name = str(args.get("class_name", "") or args.get("expected_class_name", "")).strip()
    exact = _coerce_bool(args.get("exact", False), False)
    requested_limit = _coerce_int(args.get("limit", 16), 16, minimum=4, maximum=30)
    candidates = _enum_windows(
        include_minimized=True,
        include_hidden=True,
        limit=max(12, min(64, requested_limit * 3)),
    )

    if window_id:
        for item in candidates:
            if _parse_hwnd(item.get("window_id", "")) == window_id:
                return item, candidates, "", {"reason": "matched", "top_score": 100, "confidence": "high", "match_kind": "window_id", "match_engine": "builtin"}
        info = _window_info(window_id)
        if info:
            return info, candidates, "", {"reason": "matched", "top_score": 100, "confidence": "high", "match_kind": "window_id", "match_engine": "builtin"}
        return {}, candidates, f"Could not find a window with id {_hex_hwnd(window_id)}.", {"reason": "target_not_found", "candidate_preview": []}

    if not requested_title:
        return {}, candidates, "Provide a window title or window_id.", {"reason": "invalid_input", "candidate_preview": []}

    if exact:
        direct_match = _find_window_by_exact_title_native(requested_title)
        direct_id = str(direct_match.get("window_id", "")).strip()
        if direct_id and not any(str(item.get("window_id", "")).strip() == direct_id for item in candidates):
            candidates = [direct_match, *candidates]
    selection = select_window_candidate(
        candidates,
        requested_title=requested_title,
        requested_window_id="",
        expected_process_name=requested_process_name,
        expected_class_name=requested_class_name,
        exact=exact,
    )
    selected = selection.get("selected", {}) if isinstance(selection.get("selected", {}), dict) else {}
    if selected.get("window_id"):
        return selected, candidates, "", selection

    process_context: Dict[str, Any] = {}
    if requested_process_name:
        process_result = probe_process_context(process_name=requested_process_name)
        process_context = process_result.get("data", {}) if isinstance(process_result.get("data", {}), dict) else {}
        selection["process_context"] = process_context

    preview = selection.get("candidate_preview", []) if isinstance(selection.get("candidate_preview", []), list) else []
    if selection.get("reason") == "candidate_ambiguous":
        labels = ", ".join(
            f"{item.get('title', 'window')} ({item.get('score', 0)})"
            for item in preview[:4]
            if isinstance(item, dict)
        )
        return {}, candidates, f"Multiple windows matched '{requested_title}' with similar confidence: {labels}", selection

    process_note = ""
    if process_context.get("running", False) and requested_process_name:
        process_note = f" Process '{requested_process_name}' is still running but not exposing a clearly matchable surfaced window."
    return {}, candidates, (
        f"Could not find a surfaced window matching '{requested_title}'. "
        "It may be withdrawn, tray-like, or not exposed as a visible top-level window."
        f"{process_note}"
    ), selection


def _virtual_screen_rect() -> Dict[str, int]:
    x = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
    y = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    width = max(1, int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)))
    height = max(1, int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)))
    return {"x": x, "y": y, "width": width, "height": height}


def _display_metadata() -> Dict[str, Any]:
    return collect_display_metadata(_virtual_screen_rect())


def _monitor_rect(monitor: Dict[str, Any]) -> Dict[str, int]:
    return {
        "x": int(monitor.get("left", 0) or 0),
        "y": int(monitor.get("top", 0) or 0),
        "width": max(0, int(monitor.get("width", 0) or 0)),
        "height": max(0, int(monitor.get("height", 0) or 0)),
    }


def _primary_monitor_info(display: Dict[str, Any] | None = None) -> Dict[str, Any]:
    metadata = display if isinstance(display, dict) else _display_metadata()
    primary = metadata.get("primary_monitor", {}) if isinstance(metadata.get("primary_monitor", {}), dict) else {}
    if primary:
        return primary
    monitors = metadata.get("monitors", []) if isinstance(metadata.get("monitors", []), list) else []
    for item in monitors:
        if isinstance(item, dict) and item.get("is_primary", False):
            return item
    return monitors[0] if monitors and isinstance(monitors[0], dict) else {}


def _rect_intersection(a: Dict[str, int], b: Dict[str, int]) -> Dict[str, int]:
    left = max(int(a.get("x", 0) or 0), int(b.get("x", 0) or 0))
    top = max(int(a.get("y", 0) or 0), int(b.get("y", 0) or 0))
    right = min(int(a.get("x", 0) or 0) + int(a.get("width", 0) or 0), int(b.get("x", 0) or 0) + int(b.get("width", 0) or 0))
    bottom = min(int(a.get("y", 0) or 0) + int(a.get("height", 0) or 0), int(b.get("y", 0) or 0) + int(b.get("height", 0) or 0))
    return {"x": left, "y": top, "width": max(0, right - left), "height": max(0, bottom - top)}


def _rect_area(rect: Dict[str, int]) -> int:
    return max(0, int(rect.get("width", 0) or 0)) * max(0, int(rect.get("height", 0) or 0))


def _window_monitor_metadata(rect: Dict[str, int], *, display: Dict[str, Any] | None = None) -> Dict[str, Any]:
    metadata = display if isinstance(display, dict) else _display_metadata()
    monitors = metadata.get("monitors", []) if isinstance(metadata.get("monitors", []), list) else []
    if not monitors:
        return {}
    best: Dict[str, Any] = {}
    best_area = -1
    for monitor in monitors:
        if not isinstance(monitor, dict):
            continue
        overlap = _rect_intersection(rect, _monitor_rect(monitor))
        area = _rect_area(overlap)
        if area > best_area:
            best = dict(monitor)
            best_area = area
    if not best:
        return {}
    return {
        "monitor_id": str(best.get("monitor_id", "")).strip(),
        "monitor_index": int(best.get("index", 0) or 0),
        "monitor_device_name": str(best.get("device_name", "")).strip(),
        "is_on_primary_monitor": bool(best.get("is_primary", False)),
        "dpi_x": int(best.get("dpi_x", 96) or 96),
        "dpi_y": int(best.get("dpi_y", 96) or 96),
        "scale_x": float(best.get("scale_x", 1.0) or 1.0),
        "scale_y": float(best.get("scale_y", 1.0) or 1.0),
    }


def _enrich_window_monitor_metadata(window: Dict[str, Any], *, display: Dict[str, Any] | None = None) -> Dict[str, Any]:
    if not isinstance(window, dict):
        return {}
    rect = window.get("rect", {}) if isinstance(window.get("rect", {}), dict) else {}
    metadata = _window_monitor_metadata(rect, display=display)
    if not metadata:
        return dict(window)
    enriched = dict(window)
    enriched.update(metadata)
    return enriched


def _window_is_on_primary_monitor(window: Dict[str, Any]) -> bool:
    return bool(window.get("is_on_primary_monitor", False))


def _point_in_rect(x: int, y: int, rect: Dict[str, int]) -> bool:
    return (
        x >= int(rect.get("x", 0))
        and y >= int(rect.get("y", 0))
        and x < int(rect.get("x", 0)) + int(rect.get("width", 0))
        and y < int(rect.get("y", 0)) + int(rect.get("height", 0))
    )


def _register_observation(
    *,
    active_window: Dict[str, Any],
    windows: List[Dict[str, Any]],
    screenshot_path: str = "",
    screenshot_scope: str = "",
    screenshot_bounds: Dict[str, Any] | None = None,
    screen: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    global _OBSERVATION_COUNTER
    display = screen if isinstance(screen, dict) else _display_metadata()
    primary_monitor = _primary_monitor_info(display)
    capture_bounds = (
        {
            "x": int((screenshot_bounds or {}).get("x", 0) or 0),
            "y": int((screenshot_bounds or {}).get("y", 0) or 0),
            "width": max(0, int((screenshot_bounds or {}).get("width", 0) or 0)),
            "height": max(0, int((screenshot_bounds or {}).get("height", 0) or 0)),
        }
        if isinstance(screenshot_bounds, dict)
        else {}
    )
    capture_monitor = monitor_for_rect(display, capture_bounds) if capture_bounds.get("width", 0) > 0 and capture_bounds.get("height", 0) > 0 else {}

    with _OBSERVATION_LOCK:
        _OBSERVATION_COUNTER += 1
        token = f"desktop-{int(time.time() * 1000)}-{_OBSERVATION_COUNTER}"
        created_at = time.time()
        _DESKTOP_OBSERVATIONS[token] = {
            "created_at": created_at,
            "active_window_id": str(active_window.get("window_id", "")).strip(),
            "active_window_title": str(active_window.get("title", "")).strip(),
            "screenshot_path": str(screenshot_path).strip(),
            "screenshot_scope": str(screenshot_scope).strip(),
            "primary_monitor_id": str(primary_monitor.get("monitor_id", "")).strip(),
            "screenshot_bounds": capture_bounds,
            "capture_monitor_id": str(capture_monitor.get("monitor_id", "")).strip(),
            "capture_monitor_index": int(capture_monitor.get("index", 0) or 0),
            "coordinate_space": "physical_pixels",
        }
        if len(_DESKTOP_OBSERVATIONS) > DESKTOP_OBSERVATION_LIMIT:
            ordered = sorted(_DESKTOP_OBSERVATIONS.items(), key=lambda item: item[1].get("created_at", 0.0))
            for stale_token, _value in ordered[:-DESKTOP_OBSERVATION_LIMIT]:
                _DESKTOP_OBSERVATIONS.pop(stale_token, None)

    return {
        "observation_token": token,
        "observed_at": _timestamp(),
        "active_window": active_window,
        "window_count": len(windows),
        "windows": windows[:DESKTOP_DEFAULT_WINDOW_LIMIT],
        "screenshot_path": screenshot_path,
        "screenshot_scope": screenshot_scope,
        "screenshot_bounds": capture_bounds,
        "capture_monitor_id": str(capture_monitor.get("monitor_id", "")).strip(),
        "capture_monitor_index": int(capture_monitor.get("index", 0) or 0),
        "coordinate_space": "physical_pixels",
        "primary_monitor": primary_monitor,
    }


def _lookup_observation(token: str) -> Dict[str, Any]:
    with _OBSERVATION_LOCK:
        return dict(_DESKTOP_OBSERVATIONS.get(str(token).strip(), {}))


def shutdown_desktop_runtime():
    global _WINDOW_BACKEND, _SCREENSHOT_BACKEND, _UI_EVIDENCE_BACKEND
    with _OBSERVATION_LOCK:
        _DESKTOP_OBSERVATIONS.clear()
    with _BACKEND_LOCK:
        for backend in (_WINDOW_BACKEND, _SCREENSHOT_BACKEND, _UI_EVIDENCE_BACKEND):
            if backend is None:
                continue
            try:
                backend.shutdown()
            except Exception:
                pass
        _WINDOW_BACKEND = None
        _SCREENSHOT_BACKEND = None
        _UI_EVIDENCE_BACKEND = None


def _desktop_result(
    *,
    ok: bool,
    action: str,
    summary: str,
    desktop_state: Dict[str, Any] | None = None,
    error: str = "",
    paused: bool = False,
    approval_required: bool = False,
    approval_status: str = "",
    checkpoint_required: bool = False,
    checkpoint_reason: str = "",
    checkpoint_tool: str = "",
    checkpoint_target: str = "",
    checkpoint_resume_args: Dict[str, Any] | None = None,
    workflow_resumed: bool = False,
    point: Dict[str, int] | None = None,
    typed_text_preview: str = "",
    key_sequence_preview: str = "",
    mouse_action: Dict[str, Any] | None = None,
    process_action: Dict[str, Any] | None = None,
    command_result: Dict[str, Any] | None = None,
    processes: List[Dict[str, Any]] | None = None,
    desktop_evidence: Dict[str, Any] | None = None,
    desktop_evidence_ref: Dict[str, Any] | None = None,
    target_window: Dict[str, Any] | None = None,
    recovery: Dict[str, Any] | None = None,
    recovery_attempts: List[Dict[str, Any]] | None = None,
    window_readiness: Dict[str, Any] | None = None,
    visual_stability: Dict[str, Any] | None = None,
    process_context: Dict[str, Any] | None = None,
    scene: Dict[str, Any] | None = None,
    desktop_strategy: Dict[str, Any] | None = None,
    desktop_verification: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    state = desktop_state if isinstance(desktop_state, dict) else {}
    evidence = desktop_evidence if isinstance(desktop_evidence, dict) else {}
    evidence_ref = desktop_evidence_ref if isinstance(desktop_evidence_ref, dict) else {}
    target = target_window if isinstance(target_window, dict) else {}
    recovery_view = recovery if isinstance(recovery, dict) else {}
    readiness = window_readiness if isinstance(window_readiness, dict) else {}
    stability = visual_stability if isinstance(visual_stability, dict) else {}
    process_view = process_context if isinstance(process_context, dict) else {}
    scene_view = scene if isinstance(scene, dict) else {}
    strategy_view = desktop_strategy if isinstance(desktop_strategy, dict) else {}
    verification_view = desktop_verification if isinstance(desktop_verification, dict) else {}
    pointer_view = normalize_desktop_pointer_action(mouse_action if isinstance(mouse_action, dict) else {})
    process_action_view = normalize_desktop_process_action(process_action if isinstance(process_action, dict) else {})
    command_view = normalize_desktop_command_result(command_result if isinstance(command_result, dict) else {})
    process_items = [dict(item) for item in list(processes or [])[:12] if isinstance(item, dict)]
    return {
        "ok": bool(ok),
        "action": action,
        "summary": _trim_text(summary, limit=320),
        "error": _trim_text(error, limit=320),
        "paused": bool(paused),
        "approval_required": bool(approval_required),
        "approval_status": _trim_text(approval_status, limit=40),
        "checkpoint_required": bool(checkpoint_required),
        "checkpoint_reason": _trim_text(checkpoint_reason, limit=220),
        "checkpoint_tool": _trim_text(checkpoint_tool or action, limit=80),
        "checkpoint_target": _trim_text(checkpoint_target, limit=180),
        "checkpoint_resume_args": dict(checkpoint_resume_args or {}),
        "workflow_resumed": bool(workflow_resumed),
        "desktop_state": state,
        "observation_token": _trim_text(state.get("observation_token", ""), limit=120),
        "observed_at": _trim_text(state.get("observed_at", ""), limit=40),
        "active_window": state.get("active_window", {}) if isinstance(state.get("active_window", {}), dict) else {},
        "windows": state.get("windows", []) if isinstance(state.get("windows", []), list) else [],
        "window_count": int(state.get("window_count", 0) or 0),
        "screenshot_path": _trim_text(state.get("screenshot_path", ""), limit=260),
        "screenshot_scope": _trim_text(state.get("screenshot_scope", ""), limit=40),
        "last_desktop_action": _trim_text(summary, limit=220),
        "point": point if isinstance(point, dict) else {},
        "typed_text_preview": _trim_text(typed_text_preview, limit=80),
        "key_sequence_preview": _trim_text(key_sequence_preview, limit=80),
        "mouse_action": pointer_view,
        "process_action": process_action_view,
        "command_result": command_view,
        "processes": process_items,
        "desktop_evidence": evidence,
        "desktop_evidence_ref": evidence_ref,
        "evidence_id": _trim_text(evidence_ref.get("evidence_id", "") or evidence.get("evidence_id", ""), limit=80),
        "evidence_summary": _trim_text(evidence_ref.get("summary", "") or evidence.get("summary", ""), limit=240),
        "target_window": target,
        "window_readiness": readiness,
        "visual_stability": stability,
        "process_context": process_view,
        "scene": scene_view,
        "desktop_strategy": strategy_view,
        "desktop_verification": verification_view,
        "recovery": recovery_view,
        "recovery_attempts": [dict(item) for item in list(recovery_attempts or [])[:6] if isinstance(item, dict)],
    }


def _desktop_strategy_view(
    args: Dict[str, Any],
    *,
    action: str,
    default_strategy_family: str = "",
    default_validator_family: str = "",
) -> Dict[str, Any]:
    safe_args = args if isinstance(args, dict) else {}
    strategy_family = str(safe_args.get("strategy_family", "") or default_strategy_family).strip()
    validator_family = str(safe_args.get("validator_family", "") or default_validator_family).strip()
    target_signature = _trim_text(safe_args.get("target_signature", ""), limit=220).lower()
    return {
        "desktop_intent": validator_family or action,
        "strategy_family": strategy_family,
        "validator_family": validator_family,
        "target_signature": target_signature,
        "pre_action_recovery": bool(safe_args.get("pre_action_recovery", False)),
        "force_strategy_switch": bool(safe_args.get("force_strategy_switch", False)),
    }


def _normalize_expected_process_names(*values: Any) -> List[str]:
    normalized: List[str] = []
    for value in values:
        if isinstance(value, list):
            items = value
        else:
            items = [value]
        for item in items:
            token = str(item or "").strip().lower()
            if not token or token in normalized:
                continue
            normalized.append(token)
    return normalized[:6]


def _window_expectation_score(
    window: Dict[str, Any],
    *,
    expected_title: str = "",
    expected_window_id: str = "",
    expected_process_names: List[str] | None = None,
) -> Dict[str, Any]:
    if not isinstance(window, dict):
        return {"score": 0, "reasons": []}
    title = str(window.get("title", "")).strip().lower()
    window_id = str(window.get("window_id", "")).strip().lower()
    process_name = str(window.get("process_name", "")).strip().lower()
    expected_title_text = str(expected_title or "").strip().lower()
    expected_window_id_text = str(expected_window_id or "").strip().lower()
    process_hints = _normalize_expected_process_names(expected_process_names or [])
    score = 0
    reasons: List[str] = []
    if expected_window_id_text and window_id == expected_window_id_text:
        score += 92
        reasons.append("window_id_match")
    if expected_title_text:
        if title == expected_title_text:
            score += 84
            reasons.append("title_exact")
        elif expected_title_text in title:
            score += 68
            reasons.append("title_contains")
    for hint in process_hints:
        if process_name == hint:
            score += 54
            reasons.append(f"process_exact:{hint}")
            break
        if hint and hint in process_name:
            score += 34
            reasons.append(f"process_contains:{hint}")
            break
    if bool(window.get("is_active", False)) and score > 0:
        score += 8
        reasons.append("active_window")
    if bool(window.get("is_visible", False)) and score > 0:
        score += 4
        reasons.append("visible_window")
    return {"score": min(score, 100), "reasons": reasons[:4]}


def _best_desktop_window_candidate(
    windows: List[Dict[str, Any]],
    *,
    expected_title: str = "",
    expected_window_id: str = "",
    expected_process_names: List[str] | None = None,
) -> Dict[str, Any]:
    best_window: Dict[str, Any] = {}
    best_score = 0
    best_reasons: List[str] = []
    for window in list(windows or []):
        scored = _window_expectation_score(
            window,
            expected_title=expected_title,
            expected_window_id=expected_window_id,
            expected_process_names=expected_process_names,
        )
        score = int(scored.get("score", 0) or 0)
        if score <= best_score:
            continue
        best_window = dict(window)
        best_score = score
        best_reasons = list(scored.get("reasons", [])) if isinstance(scored.get("reasons", []), list) else []
    if not best_window:
        return {}
    return {**best_window, "match_score": best_score, "match_reasons": best_reasons[:4]}


def _probe_expected_process(expected_process_names: List[str], *, launched_pid: int = 0) -> Dict[str, Any]:
    if launched_pid > 0:
        result = probe_process_context(pid=launched_pid)
        data = result.get("data", {}) if isinstance(result.get("data", {}), dict) else {}
        if data:
            return data
    for process_name in _normalize_expected_process_names(expected_process_names):
        result = probe_process_context(process_name=process_name)
        data = result.get("data", {}) if isinstance(result.get("data", {}), dict) else {}
        if data.get("running", False):
            return data
    return {}


def _sample_desktop_action_verification(
    *,
    action: str,
    validator_family: str,
    strategy_family: str,
    before_active_window: Dict[str, Any],
    before_windows: List[Dict[str, Any]],
    expected_title: str = "",
    expected_window_id: str = "",
    expected_process_names: List[str] | None = None,
    target_description: str = "",
    launched_pid: int = 0,
    sample_count: int = DESKTOP_DEFAULT_VERIFICATION_SAMPLES,
    interval_ms: int = DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS,
) -> Dict[str, Any]:
    bounded_samples = max(2, min(4, int(sample_count or DESKTOP_DEFAULT_VERIFICATION_SAMPLES)))
    bounded_interval = max(80, min(320, int(interval_ms or DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS))) / 1000.0
    normalized_process_hints = _normalize_expected_process_names(expected_process_names or [])
    before_ids = {
        str(item.get("window_id", "")).strip()
        for item in list(before_windows or [])
        if isinstance(item, dict) and str(item.get("window_id", "")).strip()
    }
    before_active_id = str(before_active_window.get("window_id", "")).strip()
    before_active_title = str(before_active_window.get("title", "")).strip()
    before_active_process = str(before_active_window.get("process_name", "")).strip()
    before_candidate = _best_desktop_window_candidate(
        _dedupe_windows([before_active_window], before_windows),
        expected_title=expected_title,
        expected_window_id=expected_window_id,
        expected_process_names=normalized_process_hints,
    )
    before_match_score = int(before_candidate.get("match_score", 0) or 0)
    best_candidate: Dict[str, Any] = {}
    samples: List[Dict[str, Any]] = []
    process_snapshot: Dict[str, Any] = {}
    saw_new_window = False
    saw_active_change = False
    saw_target_active = False
    saw_target_match_improved = False
    saw_process_detected = False

    for index in range(bounded_samples):
        if index > 0:
            time.sleep(bounded_interval)
        active_window = _active_window_info()
        visible_windows = _enum_windows(include_minimized=True, include_hidden=True, limit=24)
        candidate = _best_desktop_window_candidate(
            _dedupe_windows([active_window], visible_windows),
            expected_title=expected_title,
            expected_window_id=expected_window_id,
            expected_process_names=normalized_process_hints,
        )
        process_snapshot = _probe_expected_process(normalized_process_hints, launched_pid=launched_pid) or process_snapshot
        candidate_score = int(candidate.get("match_score", 0) or 0)
        candidate_window_id = str(candidate.get("window_id", "")).strip()
        active_window_id = str(active_window.get("window_id", "")).strip()
        active_window_title = str(active_window.get("title", "")).strip()
        active_window_process = str(active_window.get("process_name", "")).strip()
        if candidate_score > int(best_candidate.get("match_score", 0) or 0):
            best_candidate = dict(candidate)
        if active_window_id and active_window_id != before_active_id:
            saw_active_change = True
        elif active_window_title and active_window_title != before_active_title:
            saw_active_change = True
        elif active_window_process and active_window_process != before_active_process:
            saw_active_change = True
        if candidate_window_id and candidate_window_id not in before_ids and candidate_score >= 68:
            saw_new_window = True
        if candidate_score >= max(70, before_match_score + 6):
            saw_target_match_improved = True
        if candidate_score >= 74 and (
            bool(candidate.get("is_active", False))
            or (candidate_window_id and candidate_window_id == active_window_id)
        ):
            saw_target_active = True
        if process_snapshot.get("running", False):
            saw_process_detected = True
        samples.append(
            {
                "active_window_title": _trim_text(active_window_title, limit=140),
                "active_window_process": _trim_text(active_window_process, limit=80),
                "candidate_title": _trim_text(candidate.get("title", ""), limit=140),
                "candidate_process": _trim_text(candidate.get("process_name", ""), limit=80),
                "candidate_window_id": _trim_text(candidate_window_id, limit=40),
                "candidate_score": candidate_score,
            }
        )

    best_score = int(best_candidate.get("match_score", 0) or 0)
    observed_signals: List[str] = []
    if saw_new_window:
        observed_signals.append("new_window_detected")
    if saw_active_change:
        observed_signals.append("active_window_changed")
    if saw_target_match_improved:
        observed_signals.append("target_match_improved")
    if saw_target_active:
        observed_signals.append("target_foreground")
    if saw_process_detected:
        observed_signals.append("process_detected")

    status = "timing_expired"
    confidence = "low"
    note = "The bounded verification window ended without enough evidence to confirm the intended desktop result."
    if validator_family == "focus_switch":
        if saw_target_active:
            status = "verified_focus"
            confidence = "high" if expected_window_id else "medium"
            note = "The requested target window became the foreground window."
        elif best_score >= 70 and saw_target_match_improved:
            status = "focus_improved"
            confidence = "medium"
            note = "The focus result moved closer to the requested target, but a full foreground switch was not clearly proven."
        elif best_score >= 70:
            status = "target_visible_not_foreground"
            confidence = "low"
            note = "The target window was detected, but it did not clearly become foreground."
        else:
            status = "no_focus_change"
            note = "The active window did not move toward the requested target during the bounded verification window."
    elif validator_family == "click_navigation":
        if saw_new_window or saw_active_change or saw_target_match_improved:
            status = "verified_navigation_change"
            confidence = "high" if saw_new_window or saw_target_active else "medium"
            note = "The click-like interaction produced a visible desktop navigation or window-state change."
        elif best_score >= 70 and strategy_family.startswith("focus_recovery"):
            status = "focus_reacquired_only"
            confidence = "low"
            note = "The retry appears to have reacquired the intended surface, but no stronger navigation proof appeared."
        else:
            status = "no_visible_change"
            note = "The interaction ran, but no visible desktop navigation change was confirmed."
    elif validator_family == "text_input":
        if saw_new_window or saw_active_change or saw_target_match_improved:
            status = "verified_input_change"
            confidence = "medium"
            note = "The text or keyboard input produced a visible desktop state change."
        elif best_score >= 70 and saw_target_active:
            status = "focus_confirmed_only"
            confidence = "low"
            note = "Input focus stayed on the intended surface, but the visible content change could not be confirmed."
        elif best_score >= 70:
            status = "focus_lost_or_unverified"
            confidence = "low"
            note = "The intended input surface was detected, but focus or visible input proof remained too weak."
        else:
            status = "no_visible_change"
            note = "The input action ran, but no visible field or window change was confirmed."
    elif validator_family == "open_launch":
        if saw_new_window or (best_score >= 70 and saw_target_active):
            status = "verified_launch_visible"
            confidence = "high" if saw_new_window else "medium"
            note = "A visible app or document surface appeared during the bounded launch verification window."
        elif best_score >= 70:
            status = "launch_likely_background"
            confidence = "low"
            note = "A matching app surface was detected, but it did not clearly come to the foreground."
        elif saw_process_detected:
            status = "process_started_only"
            confidence = "low"
            note = "The expected process appeared, but a visible surface was not clearly confirmed."
        else:
            status = "no_visible_change"
            note = "The launch-like action completed, but the expected app or document surface was not visibly confirmed."

    expected_signals: List[str]
    if validator_family == "focus_switch":
        expected_signals = ["target_foreground"]
    elif validator_family == "click_navigation":
        expected_signals = ["visible_navigation_change"]
    elif validator_family == "text_input":
        expected_signals = ["visible_input_change"]
    elif validator_family == "open_launch":
        expected_signals = ["visible_launch_change"]
    else:
        expected_signals = ["visible_desktop_change"]
    missing_signals = [signal for signal in expected_signals if signal not in observed_signals]

    return {
        "status": status,
        "confidence": confidence,
        "note": note,
        "action": _trim_text(action, limit=60),
        "validator_family": _trim_text(validator_family, limit=60),
        "strategy_family": _trim_text(strategy_family, limit=60),
        "target_description": _trim_text(target_description, limit=160),
        "expected_window_title": _trim_text(expected_title, limit=160),
        "expected_window_id": _trim_text(expected_window_id, limit=60),
        "matched_window_title": _trim_text(best_candidate.get("title", ""), limit=180),
        "matched_window_id": _trim_text(best_candidate.get("window_id", ""), limit=40),
        "matched_process_name": _trim_text(best_candidate.get("process_name", ""), limit=120),
        "match_score": best_score,
        "observed_signals": observed_signals,
        "missing_signals": missing_signals,
        "process_detected": saw_process_detected,
        "timing_expired": status == "timing_expired",
        "sample_count": bounded_samples,
        "interval_ms": int(interval_ms or DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS),
        "samples": samples[:4],
    }


def _prepare_desktop_strategy_context(
    args: Dict[str, Any],
    *,
    action_name: str,
    default_strategy_family: str,
    default_validator_family: str,
) -> Dict[str, Any]:
    strategy_view = _desktop_strategy_view(
        args,
        action=action_name,
        default_strategy_family=default_strategy_family,
        default_validator_family=default_validator_family,
    )
    if not (
        strategy_view.get("pre_action_recovery", False)
        or str(strategy_view.get("strategy_family", "")).startswith("focus_recovery")
    ):
        return {"ok": True, "args": dict(args), "strategy": strategy_view}

    recovery_args = dict(args)
    if not any(str(recovery_args.get(key, "")).strip() for key in ("title", "match", "window_id")):
        if str(recovery_args.get("expected_window_id", "")).strip():
            recovery_args["window_id"] = str(recovery_args.get("expected_window_id", "")).strip()
        elif str(recovery_args.get("expected_window_title", "")).strip():
            recovery_args["title"] = str(recovery_args.get("expected_window_title", "")).strip()
            recovery_args.setdefault("exact", True)

    recovered = _execute_window_recovery(recovery_args, action_name=action_name)
    recovery = recovered.get("recovery", {}) if isinstance(recovered.get("recovery", {}), dict) else {}
    target_window = recovered.get("target_window", {}) if isinstance(recovered.get("target_window", {}), dict) else {}
    observation = recovered.get("observation", {}) if isinstance(recovered.get("observation", {}), dict) else {}
    if recovery.get("state") != "ready":
        summary = str(recovery.get("summary", "")).strip() or "Could not recover the target window before the bounded desktop action."
        return {
            "ok": False,
            "result": _desktop_result(
                ok=False,
                action=action_name,
                summary=summary,
                desktop_state=observation,
                error=summary,
                desktop_evidence=recovered.get("evidence_bundle", {}),
                desktop_evidence_ref=recovered.get("evidence_ref", {}),
                target_window=target_window,
                recovery=recovery,
                recovery_attempts=recovered.get("recovery_attempts", []),
                window_readiness=recovered.get("readiness", {}),
                visual_stability=recovered.get("visual_stability", {}),
                process_context=recovered.get("process_context", {}),
                scene=recovered.get("scene", {}),
                desktop_strategy=strategy_view,
            ),
        }

    adjusted_args = dict(args)
    observation_token = str(observation.get("observation_token", "")).strip()
    if observation_token:
        adjusted_args["observation_token"] = observation_token
    if str(target_window.get("window_id", "")).strip():
        adjusted_args.setdefault("expected_window_id", str(target_window.get("window_id", "")).strip())
    if str(target_window.get("title", "")).strip():
        adjusted_args.setdefault("expected_window_title", str(target_window.get("title", "")).strip())
    return {"ok": True, "args": adjusted_args, "strategy": strategy_view, "recovered": recovered}


def _approval_granted(args: Dict[str, Any]) -> bool:
    return str(args.get("approval_status", "")).strip().lower() == "approved"


def _sensitive_field_label(field_label: str) -> bool:
    lowered = str(field_label or "").strip().lower()
    if not lowered:
        return False
    return any(term in lowered for term in DESKTOP_SENSITIVE_FIELD_TERMS)


def _validate_fresh_observation(args: Dict[str, Any]) -> Tuple[str, Dict[str, Any], str]:
    token = str(args.get("observation_token", "")).strip()
    if not token:
        return "", {}, "A fresh desktop observation is required before acting. Inspect windows or capture a screenshot first."
    observation = _lookup_observation(token)
    if not observation:
        return token, {}, "The desktop observation token is missing or expired. Inspect windows or capture a screenshot again before acting."
    max_age = _coerce_int(
        args.get("max_observation_age_seconds", DESKTOP_DEFAULT_MAX_OBSERVATION_AGE_SECONDS),
        DESKTOP_DEFAULT_MAX_OBSERVATION_AGE_SECONDS,
        minimum=5,
        maximum=300,
    )
    age_seconds = max(0.0, time.time() - float(observation.get("created_at", 0.0) or 0.0))
    if age_seconds > max_age:
        return token, observation, "The desktop observation is too old to trust for a real action. Capture fresh desktop state first."
    return token, observation, ""


def _foreground_window_matches(observation: Dict[str, Any], current_active_window: Dict[str, Any]) -> bool:
    expected_window_id = str(observation.get("active_window_id", "")).strip()
    current_window_id = str(current_active_window.get("window_id", "")).strip()
    if not expected_window_id:
        return bool(current_window_id)
    return expected_window_id == current_window_id


def _current_cursor_point() -> Dict[str, int]:
    point = POINT()
    try:
        if user32.GetCursorPos(ctypes.byref(point)):
            return {"x": int(point.x), "y": int(point.y)}
    except Exception:
        pass
    return {"x": 0, "y": 0}


def _window_center_point(window: Dict[str, Any]) -> Dict[str, int]:
    rect = window.get("rect", {}) if isinstance(window.get("rect", {}), dict) else {}
    return {
        "x": int(rect.get("x", 0) or 0) + max(1, int(rect.get("width", 0) or 0) // 2),
        "y": int(rect.get("y", 0) or 0) + max(1, int(rect.get("height", 0) or 0) // 2),
    }


def _normalize_mouse_button(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token not in {"left", "right"}:
        return "left"
    return token


def _click_button_flags(button: str) -> Tuple[int, int]:
    normalized = _normalize_mouse_button(button)
    if normalized == "right":
        return MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
    return MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP


def _resolve_pointer_target_window(args: Dict[str, Any], active_window: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    explicit_target = any(str(args.get(key, "")).strip() for key in ("title", "match", "window_id"))
    if not explicit_target:
        if isinstance(active_window, dict) and active_window.get("window_id"):
            return active_window, ""
        return {}, "No active window is available for the bounded desktop pointer action."

    target_window, _candidates, lookup_error, _match_info = _find_window(args)
    if lookup_error:
        return {}, lookup_error
    if not isinstance(target_window, dict) or not target_window.get("window_id"):
        return {}, "Could not resolve the requested target window for the bounded desktop pointer action."
    active_window_id = str(active_window.get("window_id", "")).strip()
    target_window_id = str(target_window.get("window_id", "")).strip()
    if active_window_id and target_window_id and active_window_id != target_window_id:
        return target_window, "The target window is not active. Focus or recover it before using a real bounded pointer action."
    return target_window, ""


def _resolve_pointer_point(
    args: Dict[str, Any],
    *,
    active_window: Dict[str, Any],
    observation: Dict[str, Any] | None = None,
    display: Dict[str, Any] | None = None,
    allow_default_center: bool = False,
) -> Tuple[Dict[str, int], Dict[str, Any], Dict[str, Any], str]:
    coordinate_mode = str(args.get("coordinate_mode", "")).strip().lower()
    if not coordinate_mode:
        if any(key in args for key in ("capture_x", "capture_y")):
            coordinate_mode = "capture_relative"
        else:
            coordinate_mode = "window_relative" if any(key in args for key in ("relative_x", "relative_y")) else "absolute"
    target_window, target_error = _resolve_pointer_target_window(args, active_window)
    if target_error and coordinate_mode == "window_relative":
        return {}, target_window, {}, target_error

    if coordinate_mode == "window_relative":
        point_x_raw = args.get("relative_x", args.get("x", None))
        point_y_raw = args.get("relative_y", args.get("y", None))
    elif coordinate_mode == "capture_relative":
        point_x_raw = args.get("capture_x", args.get("x", None))
        point_y_raw = args.get("capture_y", args.get("y", None))
    else:
        point_x_raw = args.get("x", None)
        point_y_raw = args.get("y", None)
    if point_x_raw in {None, ""} or point_y_raw in {None, ""}:
        if allow_default_center and target_window.get("window_id"):
            center = _window_center_point(target_window)
            mapping = build_desktop_coordinate_mapping(
                coordinate_mode="absolute",
                requested_point=center,
                display=display or _display_metadata(),
                target_window=target_window,
                observation=observation,
            )
            return center, target_window, mapping, ""
        return {}, target_window, {}, "Provide bounded pointer coordinates before using this desktop pointer tool."

    point_x = _coerce_int(point_x_raw, 0, minimum=-20_000, maximum=20_000)
    point_y = _coerce_int(point_y_raw, 0, minimum=-20_000, maximum=20_000)
    mapping = build_desktop_coordinate_mapping(
        coordinate_mode=coordinate_mode,
        requested_point={"x": point_x, "y": point_y},
        display=display or _display_metadata(),
        target_window=(target_window if isinstance(target_window, dict) and target_window.get("window_id") else active_window),
        observation=observation,
    )
    absolute_point, mapping_error = action_point_from_mapping(mapping)
    if mapping_error:
        return {}, target_window, mapping, mapping_error

    screen_rect = _virtual_screen_rect()
    if not rect_contains_point(screen_rect, int(absolute_point.get("x", 0)), int(absolute_point.get("y", 0))):
        return {}, target_window, mapping, f"The point ({absolute_point.get('x', 0)}, {absolute_point.get('y', 0)}) is outside the visible desktop."
    if target_window.get("window_id"):
        target_rect = target_window.get("rect", {}) if isinstance(target_window.get("rect", {}), dict) else {}
        if not rect_contains_point(target_rect, int(absolute_point.get("x", 0)), int(absolute_point.get("y", 0))):
            return {}, target_window, mapping, (
                f"The point ({absolute_point.get('x', 0)}, {absolute_point.get('y', 0)}) is outside "
                f"the target window '{target_window.get('title', 'window')}'."
            )
    return absolute_point, target_window, mapping, target_error


def _send_mouse_click(button: str, click_count: int) -> bool:
    down_flag, up_flag = _click_button_flags(button)
    bounded_click_count = _coerce_int(click_count, 1, minimum=1, maximum=2)
    inputs: List[INPUT] = []
    for _ in range(bounded_click_count):
        inputs.append(INPUT(type=INPUT_MOUSE, union=INPUT_UNION(mi=MOUSEINPUT(0, 0, 0, down_flag, 0, None))))
        inputs.append(INPUT(type=INPUT_MOUSE, union=INPUT_UNION(mi=MOUSEINPUT(0, 0, 0, up_flag, 0, None))))
    payload = (INPUT * len(inputs))(*inputs)
    sent = int(user32.SendInput(len(inputs), ctypes.byref(payload), ctypes.sizeof(INPUT)) or 0)
    return sent == len(inputs)


def _send_mouse_scroll(direction: str, scroll_units: int) -> bool:
    normalized_direction = str(direction or "").strip().lower()
    if normalized_direction not in {"up", "down"}:
        return False
    bounded_units = _coerce_int(scroll_units, DESKTOP_DEFAULT_SCROLL_UNITS, minimum=1, maximum=DESKTOP_MAX_SCROLL_UNITS)
    signed_delta = WHEEL_DELTA * bounded_units * (1 if normalized_direction == "up" else -1)
    inputs = (INPUT * 1)(
        INPUT(type=INPUT_MOUSE, union=INPUT_UNION(mi=MOUSEINPUT(0, 0, ctypes.c_uint32(signed_delta & 0xFFFFFFFF).value, MOUSEEVENTF_WHEEL, 0, None)))
    )
    sent = int(user32.SendInput(1, ctypes.byref(inputs), ctypes.sizeof(INPUT)) or 0)
    return sent == 1


def _focus_window_handle_native(hwnd: int) -> Tuple[bool, str]:
    handle = int(hwnd or 0)
    if handle <= 0 or not user32.IsWindow(ctypes.c_void_p(handle)):
        return False, "The requested window no longer exists."

    if user32.IsIconic(ctypes.c_void_p(handle)):
        try:
            user32.ShowWindow(ctypes.c_void_p(handle), SW_RESTORE)
        except Exception:
            pass

    current_thread_id = int(kernel32.GetCurrentThreadId() or 0)
    foreground_hwnd = int(user32.GetForegroundWindow() or 0)
    foreground_thread_id = int(user32.GetWindowThreadProcessId(ctypes.c_void_p(foreground_hwnd), None) or 0) if foreground_hwnd else 0
    target_thread_id = int(user32.GetWindowThreadProcessId(ctypes.c_void_p(handle), None) or 0)
    attached_threads: List[Tuple[int, int]] = []

    try:
        for thread_id in {foreground_thread_id, target_thread_id}:
            if thread_id and thread_id != current_thread_id:
                if user32.AttachThreadInput(current_thread_id, thread_id, True):
                    attached_threads.append((current_thread_id, thread_id))
        user32.BringWindowToTop(ctypes.c_void_p(handle))
        user32.SetForegroundWindow(ctypes.c_void_p(handle))
        user32.SetFocus(ctypes.c_void_p(handle))
        time.sleep(0.08)
    finally:
        for left, right in attached_threads:
            try:
                user32.AttachThreadInput(left, right, False)
            except Exception:
                pass

    active = _active_window_info_native()
    if str(active.get("window_id", "")).strip() == _hex_hwnd(handle):
        return True, ""
    return False, "Windows did not grant foreground focus to the requested window."


def _restore_window_handle_native(hwnd: int) -> Tuple[bool, str]:
    handle = int(hwnd or 0)
    if handle <= 0 or not user32.IsWindow(ctypes.c_void_p(handle)):
        return False, "The requested window no longer exists."
    try:
        user32.ShowWindow(ctypes.c_void_p(handle), SW_RESTORE)
        time.sleep(0.08)
        return True, ""
    except Exception as exc:
        return False, _trim_text(exc, limit=220)


def _show_window_handle_native(hwnd: int) -> Tuple[bool, str]:
    handle = int(hwnd or 0)
    if handle <= 0 or not user32.IsWindow(ctypes.c_void_p(handle)):
        return False, "The requested window no longer exists."
    try:
        user32.ShowWindow(ctypes.c_void_p(handle), SW_SHOW)
        time.sleep(0.08)
        return True, ""
    except Exception as exc:
        return False, _trim_text(exc, limit=220)


def _capture_bitmap_native(path: Path, *, x: int, y: int, width: int, height: int) -> Tuple[bool, str]:
    if width <= 0 or height <= 0:
        return False, "Screenshot bounds were empty."

    source_dc = mem_dc = bitmap = previous_object = None
    try:
        source_dc = user32.GetDC(0)
        if not source_dc:
            return False, "Could not open the screen device context."
        mem_dc = gdi32.CreateCompatibleDC(source_dc)
        if not mem_dc:
            return False, "Could not create a compatible device context."
        bitmap = gdi32.CreateCompatibleBitmap(source_dc, width, height)
        if not bitmap:
            return False, "Could not create a compatible bitmap."
        previous_object = gdi32.SelectObject(mem_dc, bitmap)
        if not gdi32.BitBlt(mem_dc, 0, 0, width, height, source_dc, x, y, SRCCOPY):
            return False, "Could not copy screen pixels into the screenshot buffer."

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = height
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        pixel_bytes = width * height * 4
        bmi.bmiHeader.biSizeImage = pixel_bytes
        pixel_buffer = (ctypes.c_ubyte * pixel_bytes)()
        rows = gdi32.GetDIBits(
            mem_dc,
            bitmap,
            0,
            height,
            ctypes.byref(pixel_buffer),
            ctypes.byref(bmi),
            DIB_RGB_COLORS,
        )
        if rows != height:
            return False, "Could not read screenshot pixels from the desktop buffer."

        file_header = struct.pack(
            "<2sIHHI",
            b"BM",
            14 + ctypes.sizeof(BITMAPINFOHEADER) + pixel_bytes,
            0,
            0,
            14 + ctypes.sizeof(BITMAPINFOHEADER),
        )
        with path.open("wb") as handle:
            handle.write(file_header)
            handle.write(bytes(bmi.bmiHeader))
            handle.write(bytes(pixel_buffer))
        return True, ""
    except Exception as exc:
        return False, f"Could not save the screenshot: {exc}"
    finally:
        if previous_object and mem_dc:
            try:
                gdi32.SelectObject(mem_dc, previous_object)
            except Exception:
                pass
        if bitmap:
            try:
                gdi32.DeleteObject(bitmap)
            except Exception:
                pass
        if mem_dc:
            try:
                gdi32.DeleteDC(mem_dc)
            except Exception:
                pass
        if source_dc:
            try:
                user32.ReleaseDC(0, source_dc)
            except Exception:
                pass


def _focus_window_handle(hwnd: int) -> Tuple[bool, str]:
    ok, error = _focus_window_handle_native(hwnd)
    if ok:
        return True, ""
    return False, str(error or "Could not focus the requested window.").strip()


def _wait_for_window_ready(
    args: Dict[str, Any],
    *,
    action_name: str,
    attempt_count: int = 0,
) -> Dict[str, Any]:
    wait_seconds = max(0.2, min(3.0, float(args.get("wait_seconds", 1.2) or 1.2)))
    interval_seconds = max(0.08, min(0.4, float(args.get("poll_interval_seconds", 0.16) or 0.16)))
    deadline = time.time() + wait_seconds
    last_view: Dict[str, Any] = {}
    include_ui_evidence = action_name not in {"desktop_focus_window"}
    include_visual_stability = action_name not in {"desktop_focus_window"}
    readiness_mode = "metadata_only" if action_name == "desktop_focus_window" else "full"

    while time.time() < deadline:
        current_args = dict(args)
        current_args["attempt_count"] = attempt_count
        inspected = _inspect_window_state_internal(
            current_args,
            source_action=action_name,
            include_ui_evidence=include_ui_evidence,
            include_visual_stability=include_visual_stability,
            readiness_mode=readiness_mode,
        )
        last_view = inspected
        recovery = inspected.get("recovery", {}) if isinstance(inspected.get("recovery", {}), dict) else {}
        if recovery.get("state") == "ready":
            return inspected
        if recovery.get("state") != "waiting":
            return inspected
        time.sleep(interval_seconds)

    return last_view


def _execute_window_recovery(args: Dict[str, Any], *, action_name: str) -> Dict[str, Any]:
    include_ui_evidence = action_name not in {"desktop_focus_window"}
    include_visual_stability = action_name not in {"desktop_focus_window"}
    readiness_mode = "metadata_only" if action_name == "desktop_focus_window" else "full"
    inspected = _inspect_window_state_internal(
        args,
        source_action=action_name,
        include_ui_evidence=include_ui_evidence,
        include_visual_stability=include_visual_stability,
        readiness_mode=readiness_mode,
    )
    recovery = inspected.get("recovery", {}) if isinstance(inspected.get("recovery", {}), dict) else {}
    target_window = inspected.get("target_window", {}) if isinstance(inspected.get("target_window", {}), dict) else {}
    max_attempts = _coerce_int(args.get("max_attempts", 2), 2, minimum=0, maximum=4)
    attempts: List[Dict[str, Any]] = []
    current = dict(recovery)

    for attempt_index in range(max_attempts):
        strategy = select_window_recovery_strategy(current, attempt_count=attempt_index, max_attempts=max_attempts)
        attempts.append(
            {
                "attempt": attempt_index + 1,
                "strategy": strategy.get("strategy", ""),
                "reason": current.get("reason", ""),
                "summary": strategy.get("summary", ""),
            }
        )
        current["strategy"] = strategy.get("strategy", "")
        current["attempt_count"] = attempt_index + 1
        current["max_attempts"] = max_attempts

        if strategy.get("strategy") in {"no_action", "report_missing_target", "stop_and_report", "inspect_only"}:
            break

        handle = _parse_hwnd(target_window.get("window_id", ""))
        if handle <= 0:
            break

        if strategy.get("strategy") == "restore_then_focus":
            _restore_window_handle_native(handle)
            _focus_window_handle(handle)
        elif strategy.get("strategy") == "show_then_focus":
            _show_window_handle_native(handle)
            _focus_window_handle(handle)
        elif strategy.get("strategy") == "focus_then_verify":
            _focus_window_handle(handle)
        elif strategy.get("strategy") == "wait_for_readiness":
            waited_args = dict(args)
            waited_args["window_id"] = target_window.get("window_id", "")
            waited_args["attempt_count"] = attempt_index + 1
            waited = _wait_for_window_ready(waited_args, action_name=action_name, attempt_count=attempt_index + 1)
            current = waited.get("recovery", {}) if isinstance(waited.get("recovery", {}), dict) else current
            inspected = waited
            if current.get("state") == "ready":
                break
            continue

        refreshed_args = dict(args)
        refreshed_args["window_id"] = target_window.get("window_id", "")
        refreshed_args["attempt_count"] = attempt_index + 1
        inspected = _inspect_window_state_internal(
            refreshed_args,
            source_action=action_name,
            include_ui_evidence=include_ui_evidence,
            include_visual_stability=include_visual_stability,
            readiness_mode=readiness_mode,
        )
        target_window = inspected.get("target_window", {}) if isinstance(inspected.get("target_window", {}), dict) else target_window
        current = inspected.get("recovery", {}) if isinstance(inspected.get("recovery", {}), dict) else current
        if current.get("state") == "ready":
            break

    return {
        **inspected,
        "recovery": current,
        "recovery_attempts": attempts,
    }


def _capture_with_backend(
    path: Path,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    scope: str,
    active_window_title: str = "",
) -> Dict[str, Any]:
    return _get_screenshot_backend().capture(
        path,
        x=x,
        y=y,
        width=width,
        height=height,
        scope=scope,
        active_window_title=active_window_title,
    )


def _capture_path(args: Dict[str, Any], *, evidence_id: str = "") -> Path:
    if evidence_id:
        extension = str(getattr(_get_screenshot_backend(), "file_extension", ".bmp") or ".bmp").strip()
        if not extension.startswith("."):
            extension = f".{extension}"
        return get_desktop_evidence_store().artifact_path(evidence_id, extension=extension)

    name = str(args.get("name", "") or args.get("output_name", "")).strip()
    safe_name = "".join(ch for ch in name if ch.isalnum() or ch in {"-", "_"}).strip("._")
    if not safe_name:
        safe_name = f"desktop_capture_{int(time.time() * 1000)}"
    extension = str(getattr(_get_screenshot_backend(), "file_extension", ".bmp") or ".bmp").strip()
    if not extension.startswith("."):
        extension = f".{extension}"
    root = Path.cwd() / "data" / "desktop_captures"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{safe_name}{extension}"


def _safe_unlink(path: str | Path):
    try:
        candidate = Path(path)
    except Exception:
        return
    try:
        if candidate.exists() and candidate.is_file():
            candidate.unlink()
    except Exception:
        return


def _file_sha1(path: str | Path) -> str:
    try:
        candidate = Path(path)
    except Exception:
        return ""
    if not candidate.exists() or not candidate.is_file():
        return ""
    digest = hashlib.sha1()
    try:
        with candidate.open("rb") as handle:
            while True:
                chunk = handle.read(65_536)
                if not chunk:
                    break
                digest.update(chunk)
    except Exception:
        return ""
    return digest.hexdigest()


def _primary_monitor_bounds(display: Dict[str, Any] | None = None) -> Tuple[Dict[str, int], Dict[str, Any]]:
    metadata = display if isinstance(display, dict) else _display_metadata()
    primary = _primary_monitor_info(metadata)
    if isinstance(primary, dict) and int(primary.get("width", 0) or 0) > 0 and int(primary.get("height", 0) or 0) > 0:
        return _monitor_rect(primary), primary
    return _virtual_screen_rect(), {}


def _clip_bounds_to_rect(bounds: Dict[str, int], clip_rect: Dict[str, int]) -> Dict[str, int]:
    return _rect_intersection(bounds, clip_rect)


def _capture_derived_active_window_crop(
    *,
    captured_path: str,
    active_window: Dict[str, Any],
    primary_bounds: Dict[str, int],
    active_window_title: str,
) -> Dict[str, Any]:
    window_rect = active_window.get("rect", {}) if isinstance(active_window.get("rect", {}), dict) else {}
    clipped = _clip_bounds_to_rect(
        {
            "x": int(window_rect.get("x", 0) or 0),
            "y": int(window_rect.get("y", 0) or 0),
            "width": int(window_rect.get("width", 0) or 0),
            "height": int(window_rect.get("height", 0) or 0),
        },
        primary_bounds,
    )
    if _rect_area(clipped) <= 0:
        return {}
    base_path = Path(str(captured_path).strip())
    if not str(base_path):
        return {}
    derived_path = base_path.with_name(f"{base_path.stem}-active-window{base_path.suffix}")
    derived = _capture_with_backend(
        derived_path,
        x=int(clipped.get("x", 0) or 0),
        y=int(clipped.get("y", 0) or 0),
        width=max(1, min(int(clipped.get("width", 0) or 0), DESKTOP_DEFAULT_CAPTURE_MAX_WIDTH)),
        height=max(1, min(int(clipped.get("height", 0) or 0), DESKTOP_DEFAULT_CAPTURE_MAX_HEIGHT)),
        scope="derived_active_window",
        active_window_title=active_window_title,
    )
    derived_data = derived.get("data", {}) if isinstance(derived.get("data", {}), dict) else {}
    if not bool(derived.get("ok", False)):
        return {}
    return {
        "path": str(derived_data.get("path", "") or derived_path),
        "bounds": clipped,
        "scope": "derived_active_window",
    }


def _primary_monitor_activity_error(action: str, active_window: Dict[str, Any], *, windows: List[Dict[str, Any]], desktop_evidence_ref: Dict[str, Any] | None = None) -> Dict[str, Any]:
    state = _register_observation(active_window=active_window, windows=windows)
    window_title = str(active_window.get("title", "") or "the active window").strip()
    message = (
        f"Bounded desktop activity currently stays on the Windows primary monitor. "
        f"'{window_title}' is not on the primary display, so {action.replace('_', ' ')} was skipped."
    )
    return _desktop_result(
        ok=False,
        action=action,
        summary=message,
        desktop_state=state,
        error=message,
        desktop_evidence_ref=desktop_evidence_ref or {},
        target_window=active_window,
    )


def capture_desktop_evidence_frame(
    *,
    scope: str = DESKTOP_DEFAULT_CAPTURE_SCOPE,
    source_action: str = "desktop_capture_screenshot",
    limit: int = DESKTOP_DEFAULT_WINDOW_LIMIT,
    capture_name: str = "",
    include_ui_evidence: bool = False,
    ui_limit: int = 8,
    capture_mode: str = "",
    importance: str = "",
    importance_reason: str = "",
    state_scope_id: str = "",
    task_id: str = "",
    task_status: str = "",
    checkpoint_pending: bool = False,
    checkpoint_tool: str = "",
    checkpoint_target: str = "",
    record_on_error: bool = True,
    record_evidence: bool = True,
) -> Dict[str, Any]:
    requested_scope = str(scope or DESKTOP_DEFAULT_CAPTURE_SCOPE).strip().lower()
    active_window = _active_window_info()
    windows = _enum_windows(
        limit=_coerce_int(limit, DESKTOP_DEFAULT_WINDOW_LIMIT, minimum=1, maximum=20)
    )
    display = _display_metadata()
    primary_bounds, primary_monitor = _primary_monitor_bounds(display)
    evidence_id = get_desktop_evidence_store().next_evidence_id()
    args: Dict[str, Any] = {}
    if capture_name:
        args["name"] = capture_name

    if requested_scope == "desktop":
        bounds = _virtual_screen_rect()
        capture_label = "desktop"
        target_window: Dict[str, Any] = {}
        capture_scope = "desktop"
    elif requested_scope in {"primary", "primary_display", "primary_monitor", "screen", "full_screen"}:
        bounds = dict(primary_bounds)
        capture_label = "primary display"
        target_window = dict(active_window) if _window_is_on_primary_monitor(active_window) else {}
        capture_scope = "primary_monitor"
    else:
        capture_scope = "active_window"
        if not active_window:
            observation = _register_observation(active_window=active_window, windows=windows)
            evidence_bundle: Dict[str, Any] = {}
            evidence_ref: Dict[str, Any] = {}
            if record_evidence and record_on_error:
                evidence_bundle, evidence_ref = _record_desktop_evidence(
                    source_action=source_action,
                    active_window=active_window,
                    windows=windows,
                    observation_token=str(observation.get("observation_token", "")).strip(),
                    include_ui_evidence=False,
                    errors=["Could not capture the active window because no active window was detected."],
                    bundle_metadata={
                        "capture_mode": capture_mode,
                        "importance": importance,
                        "importance_reason": importance_reason,
                        "state_scope_id": state_scope_id,
                        "task_id": task_id,
                        "task_status": task_status,
                        "checkpoint_pending": checkpoint_pending,
                        "checkpoint_tool": checkpoint_tool,
                        "checkpoint_target": checkpoint_target,
                    },
                )
            return {
                "ok": False,
                "error": "Could not capture the active window because no active window was detected.",
                "capture_label": "active window",
                "active_window": active_window,
                "windows": windows,
                "observation": observation,
                "screenshot": {},
                "evidence_bundle": evidence_bundle,
                "evidence_ref": evidence_ref,
                "screenshot_path": "",
                "screenshot_scope": capture_scope,
                "capture_signature": "",
                "target_window": {},
            }
        bounds = dict(active_window.get("rect", {}))
        capture_label = f"active window '{active_window.get('title', 'window')}'"
        target_window = dict(active_window)

    width = min(max(1, int(bounds.get("width", 0) or 0)), DESKTOP_DEFAULT_CAPTURE_MAX_WIDTH)
    height = min(max(1, int(bounds.get("height", 0) or 0)), DESKTOP_DEFAULT_CAPTURE_MAX_HEIGHT)
    capture_x = int(bounds.get("x", 0))
    capture_y = int(bounds.get("y", 0))
    path = _capture_path(args, evidence_id=evidence_id)
    capture_result = _capture_with_backend(
        path,
        x=capture_x,
        y=capture_y,
        width=width,
        height=height,
        scope=capture_scope,
        active_window_title=str(active_window.get("title", "") or ""),
    )
    ok = bool(capture_result.get("ok", False))
    error = str(capture_result.get("error", "") or "").strip()
    capture_data = capture_result.get("data", {}) if isinstance(capture_result.get("data", {}), dict) else {}
    captured_path = str(capture_data.get("path", "") or "").strip() or str(path)
    capture_metadata = dict(capture_data.get("metadata", {})) if isinstance(capture_data.get("metadata", {}), dict) else {}
    capture_metadata["capture_policy"] = "full_primary_first" if capture_scope == "primary_monitor" else "explicit_scope"
    capture_metadata["coordinate_space"] = "physical_pixels"
    if primary_monitor:
        capture_metadata["primary_monitor_id"] = str(primary_monitor.get("monitor_id", "")).strip()
        capture_metadata["primary_monitor_device_name"] = str(primary_monitor.get("device_name", "")).strip()
    capture_monitor = monitor_for_rect(display, bounds)
    if capture_monitor:
        capture_metadata["capture_monitor_id"] = str(capture_monitor.get("monitor_id", "")).strip()
        capture_metadata["capture_monitor_index"] = int(capture_monitor.get("index", 0) or 0)
        capture_metadata["capture_monitor_device_name"] = str(capture_monitor.get("device_name", "")).strip()
        capture_metadata["capture_dpi_x"] = int(capture_monitor.get("dpi_x", 96) or 96)
        capture_metadata["capture_dpi_y"] = int(capture_monitor.get("dpi_y", 96) or 96)
        capture_metadata["capture_scale_x"] = float(capture_monitor.get("scale_x", 1.0) or 1.0)
        capture_metadata["capture_scale_y"] = float(capture_monitor.get("scale_y", 1.0) or 1.0)
    if (
        ok
        and capture_scope == "primary_monitor"
        and active_window
        and _window_is_on_primary_monitor(active_window)
        and (capture_mode == "manual" or checkpoint_pending)
    ):
        derived_crop = _capture_derived_active_window_crop(
            captured_path=captured_path,
            active_window=active_window,
            primary_bounds=primary_bounds,
            active_window_title=str(active_window.get("title", "") or ""),
        )
        if derived_crop:
            capture_metadata["derived_active_window_crop"] = derived_crop
    capture_signature = _file_sha1(captured_path) if ok else ""
    observation = _register_observation(
        active_window=active_window,
        windows=windows,
        screenshot_path=captured_path if ok else "",
        screenshot_scope=capture_scope,
        screenshot_bounds={"x": capture_x, "y": capture_y, "width": width, "height": height},
        screen=display,
    )
    evidence_bundle: Dict[str, Any] = {}
    evidence_ref: Dict[str, Any] = {}
    if record_evidence and (ok or record_on_error):
        evidence_bundle, evidence_ref = _record_desktop_evidence(
            source_action=source_action,
            active_window=active_window,
            windows=windows,
            observation_token=str(observation.get("observation_token", "")).strip(),
            screenshot={
                **capture_data,
                "path": captured_path if ok else "",
                "scope": capture_scope,
                "bounds": {"x": capture_x, "y": capture_y, "width": width, "height": height},
                "active_window_title": str(active_window.get("title", "") or ""),
                "metadata": capture_metadata,
            },
            target_window=target_window,
            include_ui_evidence=include_ui_evidence,
            ui_limit=_coerce_int(ui_limit, 8, minimum=1, maximum=12),
            errors=[error] if error else [],
            bundle_metadata={
                "capture_mode": capture_mode,
                "importance": importance,
                "importance_reason": importance_reason,
                "state_scope_id": state_scope_id,
                "task_id": task_id,
                "task_status": task_status,
                "checkpoint_pending": checkpoint_pending,
                "checkpoint_tool": checkpoint_tool,
                "checkpoint_target": checkpoint_target,
                "capture_signature": capture_signature,
                "primary_monitor_id": str(primary_monitor.get("monitor_id", "")).strip(),
            },
        )
    return {
        "ok": ok,
        "error": error,
        "capture_label": capture_label,
        "active_window": active_window,
        "windows": windows,
        "observation": observation,
        "screenshot": {
            **capture_data,
            "path": captured_path if ok else "",
            "scope": capture_scope,
            "bounds": {"x": capture_x, "y": capture_y, "width": width, "height": height},
            "active_window_title": str(active_window.get("title", "") or ""),
            "metadata": capture_metadata,
        },
        "evidence_bundle": evidence_bundle,
        "evidence_ref": evidence_ref,
        "screenshot_path": captured_path if ok else "",
        "screenshot_scope": capture_scope,
        "capture_signature": capture_signature,
        "target_window": target_window,
        "screen": display,
    }


def record_captured_desktop_evidence(
    *,
    source_action: str,
    active_window: Dict[str, Any],
    windows: List[Dict[str, Any]],
    observation: Dict[str, Any],
    screenshot: Dict[str, Any],
    target_window: Dict[str, Any] | None = None,
    include_ui_evidence: bool = False,
    ui_limit: int = 8,
    errors: List[str] | None = None,
    bundle_metadata: Dict[str, Any] | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    return _record_desktop_evidence(
        source_action=source_action,
        active_window=active_window,
        windows=windows,
        observation_token=str(observation.get("observation_token", "")).strip(),
        screenshot=screenshot,
        target_window=target_window or {},
        include_ui_evidence=include_ui_evidence,
        ui_limit=ui_limit,
        errors=errors,
        bundle_metadata=bundle_metadata,
    )


def desktop_list_windows(args: Dict[str, Any]) -> Dict[str, Any]:
    limit = _coerce_int(args.get("limit", DESKTOP_DEFAULT_WINDOW_LIMIT), DESKTOP_DEFAULT_WINDOW_LIMIT, minimum=1, maximum=20)
    windows = _enum_windows(limit=limit)
    active_window = _active_window_info()
    observation = _register_observation(active_window=active_window, windows=windows)
    evidence_bundle, evidence_ref = _record_desktop_evidence(
        source_action="desktop_list_windows",
        active_window=active_window,
        windows=windows,
        observation_token=str(observation.get("observation_token", "")).strip(),
        include_ui_evidence=False,
    )
    titles = ", ".join(item.get("title", "") for item in windows[:4] if item.get("title"))
    summary = f"Found {len(windows)} visible window(s)."
    if active_window.get("title"):
        summary += f" Active window: {active_window.get('title', '')}."
    if titles:
        summary += f" Top windows: {titles}."
    return _desktop_result(
        ok=True,
        action="desktop_list_windows",
        summary=summary,
        desktop_state=observation,
        desktop_evidence=evidence_bundle,
        desktop_evidence_ref=evidence_ref,
    )


def desktop_get_active_window(args: Dict[str, Any]) -> Dict[str, Any]:
    windows = _enum_windows(limit=_coerce_int(args.get("limit", DESKTOP_DEFAULT_WINDOW_LIMIT), DESKTOP_DEFAULT_WINDOW_LIMIT, minimum=1, maximum=20))
    active_window = _active_window_info()
    observation = _register_observation(active_window=active_window, windows=windows)
    evidence_bundle, evidence_ref = _record_desktop_evidence(
        source_action="desktop_get_active_window",
        active_window=active_window,
        windows=windows,
        observation_token=str(observation.get("observation_token", "")).strip(),
        include_ui_evidence=False,
    )
    if not active_window:
        return _desktop_result(
            ok=False,
            action="desktop_get_active_window",
            summary="Could not determine the active window.",
            desktop_state=observation,
            error="Could not determine the active window.",
            desktop_evidence=evidence_bundle,
            desktop_evidence_ref=evidence_ref,
        )
    return _desktop_result(
        ok=True,
        action="desktop_get_active_window",
        summary=f"The active window is '{active_window.get('title', 'unknown window')}'.",
        desktop_state=observation,
        desktop_evidence=evidence_bundle,
        desktop_evidence_ref=evidence_ref,
    )


def desktop_inspect_window_state(args: Dict[str, Any]) -> Dict[str, Any]:
    inspected = _inspect_window_state_internal(
        args,
        source_action="desktop_inspect_window_state",
        include_ui_evidence=True,
        include_visual_stability=_coerce_bool(args.get("check_visual_stability", True), True),
    )
    recovery = inspected.get("recovery", {}) if isinstance(inspected.get("recovery", {}), dict) else {}
    summary = str(recovery.get("summary", "") or "").strip() or "Inspected the current desktop window state."
    ok = recovery.get("state") == "ready"
    error = "" if ok else summary
    return _desktop_result(
        ok=ok,
        action="desktop_inspect_window_state",
        summary=summary,
        desktop_state=inspected.get("observation", {}),
        error=error,
        desktop_evidence=inspected.get("evidence_bundle", {}),
        desktop_evidence_ref=inspected.get("evidence_ref", {}),
        target_window=inspected.get("target_window", {}),
        recovery=recovery,
        window_readiness=inspected.get("readiness", {}),
        visual_stability=inspected.get("visual_stability", {}),
        process_context=inspected.get("process_context", {}),
        scene=inspected.get("scene", {}),
    )


def desktop_wait_for_window_ready(args: Dict[str, Any]) -> Dict[str, Any]:
    waited = _wait_for_window_ready(args, action_name="desktop_wait_for_window_ready")
    recovery = waited.get("recovery", {}) if isinstance(waited.get("recovery", {}), dict) else {}
    ok = recovery.get("state") == "ready"
    summary = str(recovery.get("summary", "") or "").strip() or "Finished the bounded window readiness check."
    error = "" if ok else summary
    return _desktop_result(
        ok=ok,
        action="desktop_wait_for_window_ready",
        summary=summary,
        desktop_state=waited.get("observation", {}),
        error=error,
        desktop_evidence=waited.get("evidence_bundle", {}),
        desktop_evidence_ref=waited.get("evidence_ref", {}),
        target_window=waited.get("target_window", {}),
        recovery=recovery,
        window_readiness=waited.get("readiness", {}),
        visual_stability=waited.get("visual_stability", {}),
        process_context=waited.get("process_context", {}),
        scene=waited.get("scene", {}),
    )


def desktop_recover_window(args: Dict[str, Any]) -> Dict[str, Any]:
    recovered = _execute_window_recovery(args, action_name="desktop_recover_window")
    recovery = recovered.get("recovery", {}) if isinstance(recovered.get("recovery", {}), dict) else {}
    ok = recovery.get("state") == "ready"
    summary = str(recovery.get("summary", "") or "").strip() or "Completed the bounded desktop recovery attempt."
    error = "" if ok else summary
    return _desktop_result(
        ok=ok,
        action="desktop_recover_window",
        summary=summary,
        desktop_state=recovered.get("observation", {}),
        error=error,
        desktop_evidence=recovered.get("evidence_bundle", {}),
        desktop_evidence_ref=recovered.get("evidence_ref", {}),
        target_window=recovered.get("target_window", {}),
        recovery=recovery,
        recovery_attempts=recovered.get("recovery_attempts", []),
        window_readiness=recovered.get("readiness", {}),
        visual_stability=recovered.get("visual_stability", {}),
        process_context=recovered.get("process_context", {}),
        scene=recovered.get("scene", {}),
    )


def desktop_focus_window(args: Dict[str, Any]) -> Dict[str, Any]:
    before_active_window, before_windows, _before_observation = _current_desktop_context(limit=20)
    strategy_view = _desktop_strategy_view(
        args,
        action="desktop_focus_window",
        default_strategy_family="focus_recovery_window",
        default_validator_family="focus_switch",
    )
    recovered = _execute_window_recovery(args, action_name="desktop_focus_window")
    recovery = recovered.get("recovery", {}) if isinstance(recovered.get("recovery", {}), dict) else {}
    ok = recovery.get("state") == "ready"
    target_window = recovered.get("target_window", {}) if isinstance(recovered.get("target_window", {}), dict) else {}
    verification = (
        _sample_desktop_action_verification(
            action="desktop_focus_window",
            validator_family=str(strategy_view.get("validator_family", "") or "focus_switch"),
            strategy_family=str(strategy_view.get("strategy_family", "") or "focus_recovery_window"),
            before_active_window=before_active_window,
            before_windows=before_windows,
            expected_title=str(target_window.get("title", "") or args.get("title", "") or args.get("expected_window_title", "")).strip(),
            expected_window_id=str(target_window.get("window_id", "") or args.get("window_id", "") or args.get("expected_window_id", "")).strip(),
            expected_process_names=[str(target_window.get("process_name", "")).strip()],
            target_description=str(target_window.get("title", "") or args.get("title", "") or "requested window").strip(),
            sample_count=_coerce_int(args.get("verification_samples", DESKTOP_DEFAULT_VERIFICATION_SAMPLES), DESKTOP_DEFAULT_VERIFICATION_SAMPLES, minimum=2, maximum=4),
            interval_ms=_coerce_int(args.get("verification_interval_ms", DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS), DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS, minimum=80, maximum=320),
        )
        if ok and (target_window or args.get("title") or args.get("expected_window_title") or args.get("window_id") or args.get("expected_window_id"))
        else {}
    )
    if ok:
        summary = f"Focused '{recovery.get('active_window', {}).get('title', '') or target_window.get('title', 'the requested window')}'."
        error = ""
    else:
        summary = str(recovery.get("summary", "") or "").strip() or f"Could not focus '{target_window.get('title', 'the requested window')}'."
        error = summary
    return _desktop_result(
        ok=ok,
        action="desktop_focus_window",
        summary=summary,
        desktop_state=recovered.get("observation", {}),
        error=error,
        desktop_evidence=recovered.get("evidence_bundle", {}),
        desktop_evidence_ref=recovered.get("evidence_ref", {}),
        target_window=target_window,
        recovery=recovery,
        recovery_attempts=recovered.get("recovery_attempts", []),
        window_readiness=recovered.get("readiness", {}),
        visual_stability=recovered.get("visual_stability", {}),
        process_context=recovered.get("process_context", {}),
        scene=recovered.get("scene", {}),
        desktop_strategy=strategy_view,
        desktop_verification=verification,
    )


def desktop_capture_screenshot(args: Dict[str, Any]) -> Dict[str, Any]:
    scope = str(args.get("scope", DESKTOP_DEFAULT_CAPTURE_SCOPE)).strip().lower() or DESKTOP_DEFAULT_CAPTURE_SCOPE
    capture = capture_desktop_evidence_frame(
        scope=scope,
        source_action="desktop_capture_screenshot",
        limit=_coerce_int(args.get("limit", DESKTOP_DEFAULT_WINDOW_LIMIT), DESKTOP_DEFAULT_WINDOW_LIMIT, minimum=1, maximum=20),
        capture_name=str(args.get("name", "") or args.get("output_name", "")).strip(),
        include_ui_evidence=True,
        ui_limit=_coerce_int(args.get("ui_limit", 8), 8, minimum=1, maximum=12),
        capture_mode="manual",
        importance="manual",
        importance_reason="manual_capture",
    )
    ok = bool(capture.get("ok", False))
    observation = capture.get("observation", {}) if isinstance(capture.get("observation", {}), dict) else {}
    evidence_bundle = capture.get("evidence_bundle", {}) if isinstance(capture.get("evidence_bundle", {}), dict) else {}
    evidence_ref = capture.get("evidence_ref", {}) if isinstance(capture.get("evidence_ref", {}), dict) else {}
    if not ok:
        return _desktop_result(
            ok=False,
            action="desktop_capture_screenshot",
            summary=f"Could not capture a screenshot of the {capture.get('capture_label', 'desktop')}.",
            desktop_state=observation,
            error=str(capture.get("error", "")).strip(),
            desktop_evidence=evidence_bundle,
            desktop_evidence_ref=evidence_ref,
        )
    return _desktop_result(
        ok=True,
        action="desktop_capture_screenshot",
        summary=f"Captured a screenshot of the {capture.get('capture_label', 'desktop')}.",
        desktop_state=observation,
        desktop_evidence=evidence_bundle,
        desktop_evidence_ref=evidence_ref,
    )


def _pause_desktop_action(
    *,
    action: str,
    summary: str,
    active_window: Dict[str, Any],
    windows: List[Dict[str, Any]],
    checkpoint_reason: str,
    checkpoint_target: str,
    checkpoint_resume_args: Dict[str, Any],
    point: Dict[str, int] | None = None,
    mouse_action: Dict[str, Any] | None = None,
    desktop_evidence_ref: Dict[str, Any] | None = None,
    key_sequence_preview: str = "",
) -> Dict[str, Any]:
    observation = _register_observation(active_window=active_window, windows=windows)
    resume_args = dict(checkpoint_resume_args)
    resume_args.setdefault("observation_token", observation.get("observation_token", ""))
    resume_args.setdefault("max_observation_age_seconds", 120)
    return _desktop_result(
        ok=False,
        action=action,
        summary=summary,
        desktop_state=observation,
        paused=True,
        approval_required=True,
        approval_status="not approved",
        checkpoint_required=True,
        checkpoint_reason=checkpoint_reason,
        checkpoint_tool=action,
        checkpoint_target=checkpoint_target,
        checkpoint_resume_args=resume_args,
        point=point,
        mouse_action=mouse_action,
        key_sequence_preview=key_sequence_preview,
        desktop_evidence_ref=desktop_evidence_ref,
    )


def _prepare_pointer_action_context(
    args: Dict[str, Any],
    *,
    action: str,
    allow_default_center: bool = False,
) -> Dict[str, Any]:
    token, observation, observation_error = _validate_fresh_observation(args)
    evidence_ref = _latest_evidence_ref_for_observation(token)
    display = _display_metadata()
    active_window = _active_window_info()
    windows = _enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT)
    if observation_error:
        state = _register_observation(active_window=active_window, windows=windows)
        return {
            "ok": False,
            "result": _desktop_result(
                ok=False,
                action=action,
                summary=observation_error,
                desktop_state=state,
                error=observation_error,
                desktop_evidence_ref=evidence_ref,
            ),
        }
    if not active_window or not _foreground_window_matches(observation, active_window):
        state = _register_observation(active_window=active_window, windows=windows)
        message = "The previously inspected target window is no longer active. Focus the window and inspect desktop state again before using a real desktop pointer action."
        return {
            "ok": False,
            "result": _desktop_result(
                ok=False,
                action=action,
                summary=message,
                desktop_state=state,
                error=message,
                desktop_evidence_ref=evidence_ref,
            ),
        }
    if active_window and not _window_is_on_primary_monitor(active_window) and _evidence_ref_has_screenshot(evidence_ref):
        return {
            "ok": False,
            "result": _primary_monitor_activity_error(
                action,
                active_window,
                windows=windows,
                desktop_evidence_ref=evidence_ref,
            ),
        }
    point, target_window, coordinate_mapping, point_error = _resolve_pointer_point(
        args,
        active_window=active_window,
        observation=observation,
        display=display,
        allow_default_center=allow_default_center,
    )
    if point_error:
        state = _register_observation(active_window=active_window, windows=windows)
        return {
            "ok": False,
            "result": _desktop_result(
                ok=False,
                action=action,
                summary=point_error,
                desktop_state=state,
                error=point_error,
                point=point if isinstance(point, dict) else {},
                target_window=target_window,
                mouse_action={
                    "action": (
                        "scroll"
                        if action == "desktop_scroll"
                        else "click"
                        if action in {"desktop_click_mouse", "desktop_click_point"}
                        else "hover"
                        if action == "desktop_hover_point"
                        else "move"
                    ),
                    "point": point if isinstance(point, dict) else {},
                    "coordinate_mode": str(coordinate_mapping.get("mode", "") or args.get("coordinate_mode", "absolute")).strip().lower() or "absolute",
                    "coordinate_mapping": coordinate_mapping,
                    "reason": "error",
                    "summary": point_error,
                },
                desktop_evidence_ref=evidence_ref,
            ),
        }
    return {
        "ok": True,
        "token": token,
        "observation": observation,
        "evidence_ref": evidence_ref,
        "active_window": active_window,
        "windows": windows,
        "point": point,
        "coordinate_mapping": coordinate_mapping,
        "target_window": target_window if isinstance(target_window, dict) and target_window.get("window_id") else active_window,
    }


def _pointer_checkpoint_resume_args(
    *,
    point: Dict[str, Any],
    token: str,
    active_window: Dict[str, Any],
    evidence_ref: Dict[str, Any],
    extra: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    resume_args = {
        "x": int(point.get("x", 0) or 0),
        "y": int(point.get("y", 0) or 0),
        "coordinate_mode": "absolute",
        "observation_token": token,
        "expected_window_id": active_window.get("window_id", ""),
        "expected_window_title": active_window.get("title", ""),
        "evidence_id": evidence_ref.get("evidence_id", ""),
    }
    if isinstance(extra, dict):
        resume_args.update(extra)
    return resume_args


def desktop_move_mouse(args: Dict[str, Any]) -> Dict[str, Any]:
    context = _prepare_pointer_action_context(args, action="desktop_move_mouse")
    if not context.get("ok", False):
        return context.get("result", {})

    point = context["point"]
    active_window = context["active_window"]
    windows = context["windows"]
    evidence_ref = context["evidence_ref"]
    target_window = context["target_window"]
    coordinate_mapping = context["coordinate_mapping"]
    checkpoint_target = f"{target_window.get('title', active_window.get('title', 'active window'))} @ ({point.get('x')}, {point.get('y')}) :: move mouse"
    checkpoint_reason = str(args.get("checkpoint_reason", "")).strip() or (
        f"Moving the desktop cursor to ({point.get('x')}, {point.get('y')}) in '{target_window.get('title', active_window.get('title', 'the active window'))}' requires explicit approval in this bounded control pass."
    )
    if not _approval_granted(args):
        if not _evidence_ref_has_screenshot(evidence_ref):
            state = _register_observation(active_window=active_window, windows=windows)
            message = "Approval-gated desktop mouse movement needs a screenshot-backed inspection of the active window first."
            return _desktop_result(
                ok=False,
                action="desktop_move_mouse",
                summary=message,
                desktop_state=state,
                error=message,
                point=point,
                desktop_evidence_ref=evidence_ref,
                target_window=target_window,
            )
        return _pause_desktop_action(
            action="desktop_move_mouse",
            summary=f"Approval required before moving the mouse to ({point.get('x')}, {point.get('y')}) in '{target_window.get('title', active_window.get('title', 'the active window'))}'.",
            active_window=active_window,
            windows=windows,
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=checkpoint_target,
            checkpoint_resume_args=_pointer_checkpoint_resume_args(
                point=point,
                token=context["token"],
                active_window=active_window,
                evidence_ref=evidence_ref,
            ),
            point=point,
            mouse_action={
                "action": "move",
                "point": point,
                "window_title": str(target_window.get("title", "") or active_window.get("title", "")),
                "coordinate_mode": str(args.get("coordinate_mode", "absolute")).strip().lower() or "absolute",
                "coordinate_mapping": coordinate_mapping,
                "reason": "mouse_moved",
                "summary": f"Prepared a bounded mouse move to ({point.get('x')}, {point.get('y')}).",
            },
            desktop_evidence_ref=evidence_ref,
        )

    moved = bool(user32.SetCursorPos(int(point.get("x", 0) or 0), int(point.get("y", 0) or 0)))
    active_after = _active_window_info()
    observation_after = _register_observation(active_window=active_after, windows=_enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT))
    mouse_action = {
        "action": "move",
        "point": point,
        "window_title": str(target_window.get("title", "") or active_window.get("title", "")),
        "coordinate_mode": str(args.get("coordinate_mode", "absolute")).strip().lower() or "absolute",
        "coordinate_mapping": coordinate_mapping,
        "reason": "mouse_moved" if moved else "error",
        "summary": (
            f"Moved the mouse to ({point.get('x')}, {point.get('y')}) in '{active_after.get('title', target_window.get('title', 'the active window'))}'."
            if moved
            else "Could not move the mouse to the requested bounded point."
        ),
    }
    return _desktop_result(
        ok=moved,
        action="desktop_move_mouse",
        summary=mouse_action["summary"],
        desktop_state=observation_after,
        error="" if moved else "Could not move the mouse to the requested bounded point.",
        approval_status="approved",
        workflow_resumed=_coerce_bool(args.get("resume_from_checkpoint", False), False),
        point=point,
        mouse_action=mouse_action,
        desktop_evidence_ref=evidence_ref,
        target_window=target_window,
    )


def desktop_hover_point(args: Dict[str, Any]) -> Dict[str, Any]:
    context = _prepare_pointer_action_context(args, action="desktop_hover_point")
    if not context.get("ok", False):
        return context.get("result", {})

    point = context["point"]
    active_window = context["active_window"]
    windows = context["windows"]
    evidence_ref = context["evidence_ref"]
    target_window = context["target_window"]
    coordinate_mapping = context["coordinate_mapping"]
    hover_ms = _coerce_int(args.get("hover_ms", DESKTOP_DEFAULT_HOVER_MS), DESKTOP_DEFAULT_HOVER_MS, minimum=120, maximum=DESKTOP_MAX_HOVER_MS)
    checkpoint_target = f"{target_window.get('title', active_window.get('title', 'active window'))} @ ({point.get('x')}, {point.get('y')}) :: hover"
    checkpoint_reason = str(args.get("checkpoint_reason", "")).strip() or (
        f"Hovering the desktop cursor over ({point.get('x')}, {point.get('y')}) in '{target_window.get('title', active_window.get('title', 'the active window'))}' requires explicit approval in this bounded control pass."
    )
    if not _approval_granted(args):
        if not _evidence_ref_has_screenshot(evidence_ref):
            state = _register_observation(active_window=active_window, windows=windows)
            message = "Approval-gated desktop hovering needs a screenshot-backed inspection of the active window first."
            return _desktop_result(
                ok=False,
                action="desktop_hover_point",
                summary=message,
                desktop_state=state,
                error=message,
                point=point,
                desktop_evidence_ref=evidence_ref,
                target_window=target_window,
            )
        return _pause_desktop_action(
            action="desktop_hover_point",
            summary=f"Approval required before hovering over ({point.get('x')}, {point.get('y')}) in '{target_window.get('title', active_window.get('title', 'the active window'))}'.",
            active_window=active_window,
            windows=windows,
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=checkpoint_target,
            checkpoint_resume_args=_pointer_checkpoint_resume_args(
                point=point,
                token=context["token"],
                active_window=active_window,
                evidence_ref=evidence_ref,
                extra={"hover_ms": hover_ms},
            ),
            point=point,
            mouse_action={
                "action": "hover",
                "point": point,
                "window_title": str(target_window.get("title", "") or active_window.get("title", "")),
                "coordinate_mode": str(args.get("coordinate_mode", "absolute")).strip().lower() or "absolute",
                "coordinate_mapping": coordinate_mapping,
                "hover_ms": hover_ms,
                "reason": "hovered",
                "summary": f"Prepared a bounded hover at ({point.get('x')}, {point.get('y')}).",
            },
            desktop_evidence_ref=evidence_ref,
        )

    moved = bool(user32.SetCursorPos(int(point.get("x", 0) or 0), int(point.get("y", 0) or 0)))
    if moved:
        time.sleep(max(0.12, hover_ms / 1000.0))
    active_after = _active_window_info()
    observation_after = _register_observation(active_window=active_after, windows=_enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT))
    mouse_action = {
        "action": "hover",
        "point": point,
        "window_title": str(target_window.get("title", "") or active_window.get("title", "")),
        "coordinate_mode": str(args.get("coordinate_mode", "absolute")).strip().lower() or "absolute",
        "coordinate_mapping": coordinate_mapping,
        "hover_ms": hover_ms,
        "reason": "hovered" if moved else "error",
        "summary": (
            f"Hovered over ({point.get('x')}, {point.get('y')}) in '{active_after.get('title', target_window.get('title', 'the active window'))}' for {hover_ms} ms."
            if moved
            else "Could not hover over the requested bounded desktop point."
        ),
    }
    return _desktop_result(
        ok=moved,
        action="desktop_hover_point",
        summary=mouse_action["summary"],
        desktop_state=observation_after,
        error="" if moved else "Could not hover over the requested bounded desktop point.",
        approval_status="approved",
        workflow_resumed=_coerce_bool(args.get("resume_from_checkpoint", False), False),
        point=point,
        mouse_action=mouse_action,
        desktop_evidence_ref=evidence_ref,
        target_window=target_window,
    )


def desktop_click_mouse(args: Dict[str, Any]) -> Dict[str, Any]:
    strategy_context = _prepare_desktop_strategy_context(
        args,
        action_name="desktop_click_mouse",
        default_strategy_family="direct_interaction",
        default_validator_family="click_navigation",
    )
    if not strategy_context.get("ok", False):
        return strategy_context.get("result", {})
    action_args = strategy_context.get("args", args) if isinstance(strategy_context.get("args", args), dict) else dict(args)
    strategy_view = strategy_context.get("strategy", {}) if isinstance(strategy_context.get("strategy", {}), dict) else {}
    recovered = strategy_context.get("recovered", {}) if isinstance(strategy_context.get("recovered", {}), dict) else {}

    context = _prepare_pointer_action_context(action_args, action="desktop_click_mouse")
    if not context.get("ok", False):
        return context.get("result", {})

    point = context["point"]
    active_window = context["active_window"]
    windows = context["windows"]
    evidence_ref = context["evidence_ref"]
    target_window = context["target_window"]
    coordinate_mapping = context["coordinate_mapping"]
    button = _normalize_mouse_button(action_args.get("button", "left"))
    click_count = 2 if _coerce_bool(action_args.get("double_click", False), False) else _coerce_int(action_args.get("click_count", 1), 1, minimum=1, maximum=2)
    click_label = f"{button} {'double-click' if click_count == 2 else 'click'}"
    checkpoint_target = f"{target_window.get('title', active_window.get('title', 'active window'))} @ ({point.get('x')}, {point.get('y')}) :: {click_label}"
    checkpoint_reason = str(action_args.get("checkpoint_reason", "")).strip() or (
        f"{click_label.title()}ing at ({point.get('x')}, {point.get('y')}) in '{target_window.get('title', active_window.get('title', 'the active window'))}' requires explicit approval in this bounded control pass."
    )
    if not _approval_granted(action_args):
        if not _evidence_ref_has_screenshot(evidence_ref):
            state = _register_observation(active_window=active_window, windows=windows)
            message = "Approval-gated desktop mouse clicks need a screenshot-backed inspection of the active window first."
            return _desktop_result(
                ok=False,
                action="desktop_click_mouse",
                summary=message,
                desktop_state=state,
                error=message,
                point=point,
                desktop_evidence_ref=evidence_ref,
                target_window=target_window,
            )
        return _pause_desktop_action(
            action="desktop_click_mouse",
            summary=f"Approval required before performing a {click_label} at ({point.get('x')}, {point.get('y')}) in '{target_window.get('title', active_window.get('title', 'the active window'))}'.",
            active_window=active_window,
            windows=windows,
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=checkpoint_target,
            checkpoint_resume_args=_pointer_checkpoint_resume_args(
                point=point,
                token=context["token"],
                active_window=active_window,
                evidence_ref=evidence_ref,
                extra={"button": button, "click_count": click_count},
            ),
            point=point,
            mouse_action={
                "action": "click",
                "button": button,
                "click_count": click_count,
                "point": point,
                "window_title": str(target_window.get("title", "") or active_window.get("title", "")),
                "coordinate_mode": str(action_args.get("coordinate_mode", "absolute")).strip().lower() or "absolute",
                "coordinate_mapping": coordinate_mapping,
                "reason": "clicked",
                "summary": f"Prepared a bounded {click_label} at ({point.get('x')}, {point.get('y')}).",
            },
            desktop_evidence_ref=evidence_ref,
        )

    original_point = _current_cursor_point()
    moved = bool(user32.SetCursorPos(int(point.get("x", 0) or 0), int(point.get("y", 0) or 0)))
    clicked = moved and _send_mouse_click(button, click_count)
    try:
        user32.SetCursorPos(int(original_point.get("x", 0) or 0), int(original_point.get("y", 0) or 0))
    except Exception:
        pass
    active_after = _active_window_info()
    observation_after = _register_observation(active_window=active_after, windows=_enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT))
    verification = (
        _sample_desktop_action_verification(
            action="desktop_click_mouse",
            validator_family=str(strategy_view.get("validator_family", "") or "click_navigation"),
            strategy_family=str(strategy_view.get("strategy_family", "") or "direct_interaction"),
            before_active_window=active_window,
            before_windows=windows,
            expected_title=str(target_window.get("title", "") or action_args.get("expected_window_title", "")).strip(),
            expected_window_id=str(target_window.get("window_id", "") or action_args.get("expected_window_id", "")).strip(),
            expected_process_names=[str(target_window.get("process_name", "")).strip()],
            target_description=f"{click_label} in {target_window.get('title', active_window.get('title', 'the active window'))}",
            sample_count=_coerce_int(action_args.get("verification_samples", DESKTOP_DEFAULT_VERIFICATION_SAMPLES), DESKTOP_DEFAULT_VERIFICATION_SAMPLES, minimum=2, maximum=4),
            interval_ms=_coerce_int(action_args.get("verification_interval_ms", DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS), DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS, minimum=80, maximum=320),
        )
        if clicked
        else {}
    )
    mouse_action = {
        "action": "click",
        "button": button,
        "click_count": click_count,
        "point": point,
        "window_title": str(target_window.get("title", "") or active_window.get("title", "")),
        "coordinate_mode": str(action_args.get("coordinate_mode", "absolute")).strip().lower() or "absolute",
        "coordinate_mapping": coordinate_mapping,
        "reason": "clicked" if clicked else "error",
        "summary": (
            f"Performed a {click_label} at ({point.get('x')}, {point.get('y')}) in '{active_after.get('title', target_window.get('title', 'the active window'))}'."
            if clicked
            else f"Could not perform the requested bounded {click_label}."
        ),
    }
    return _desktop_result(
        ok=clicked,
        action="desktop_click_mouse",
        summary=mouse_action["summary"],
        desktop_state=observation_after,
        error="" if clicked else f"Could not perform the requested bounded {click_label}.",
        approval_status="approved",
        workflow_resumed=_coerce_bool(action_args.get("resume_from_checkpoint", False), False),
        point=point,
        mouse_action=mouse_action,
        desktop_evidence_ref=evidence_ref,
        target_window=target_window,
        recovery=recovered.get("recovery", {}),
        recovery_attempts=recovered.get("recovery_attempts", []),
        window_readiness=recovered.get("readiness", {}),
        visual_stability=recovered.get("visual_stability", {}),
        process_context=recovered.get("process_context", {}),
        scene=recovered.get("scene", {}),
        desktop_strategy=strategy_view,
        desktop_verification=verification,
    )


def desktop_click_point(args: Dict[str, Any]) -> Dict[str, Any]:
    delegated_args = dict(args)
    delegated_args.setdefault("button", "left")
    delegated_args.setdefault("click_count", 1)
    result = desktop_click_mouse(delegated_args)
    if not isinstance(result, dict):
        return result
    result["action"] = "desktop_click_point"
    if str(result.get("checkpoint_tool", "")).strip() == "desktop_click_mouse":
        result["checkpoint_tool"] = "desktop_click_point"
    if isinstance(result.get("checkpoint_resume_args", {}), dict):
        result["checkpoint_resume_args"].pop("click_count", None)
        result["checkpoint_resume_args"].pop("button", None)
    if result.get("summary"):
        result["summary"] = str(result.get("summary", "")).replace("bounded left click", "desktop click")
    return result


def desktop_scroll(args: Dict[str, Any]) -> Dict[str, Any]:
    strategy_context = _prepare_desktop_strategy_context(
        args,
        action_name="desktop_scroll",
        default_strategy_family="direct_interaction",
        default_validator_family="click_navigation",
    )
    if not strategy_context.get("ok", False):
        return strategy_context.get("result", {})
    action_args = strategy_context.get("args", args) if isinstance(strategy_context.get("args", args), dict) else dict(args)
    strategy_view = strategy_context.get("strategy", {}) if isinstance(strategy_context.get("strategy", {}), dict) else {}
    recovered = strategy_context.get("recovered", {}) if isinstance(strategy_context.get("recovered", {}), dict) else {}

    context = _prepare_pointer_action_context(action_args, action="desktop_scroll", allow_default_center=True)
    if not context.get("ok", False):
        return context.get("result", {})

    point = context["point"]
    active_window = context["active_window"]
    windows = context["windows"]
    evidence_ref = context["evidence_ref"]
    target_window = context["target_window"]
    coordinate_mapping = context["coordinate_mapping"]
    direction = str(action_args.get("direction", "down")).strip().lower()
    if direction not in {"up", "down"}:
        state = _register_observation(active_window=active_window, windows=windows)
        message = "desktop_scroll only supports bounded 'up' or 'down' directions."
        return _desktop_result(
            ok=False,
            action="desktop_scroll",
            summary=message,
            desktop_state=state,
            error=message,
            point=point,
            desktop_evidence_ref=evidence_ref,
            target_window=target_window,
        )
    scroll_units = _coerce_int(action_args.get("scroll_units", action_args.get("lines", DESKTOP_DEFAULT_SCROLL_UNITS)), DESKTOP_DEFAULT_SCROLL_UNITS, minimum=1, maximum=DESKTOP_MAX_SCROLL_UNITS)
    checkpoint_target = f"{target_window.get('title', active_window.get('title', 'active window'))} :: scroll {direction} x{scroll_units}"
    checkpoint_reason = str(action_args.get("checkpoint_reason", "")).strip() or (
        f"Scrolling {direction} by {scroll_units} unit(s) in '{target_window.get('title', active_window.get('title', 'the active window'))}' requires explicit approval in this bounded control pass."
    )
    if not _approval_granted(action_args):
        if not _evidence_ref_has_screenshot(evidence_ref):
            state = _register_observation(active_window=active_window, windows=windows)
            message = "Approval-gated desktop scrolling needs a screenshot-backed inspection of the active window first."
            return _desktop_result(
                ok=False,
                action="desktop_scroll",
                summary=message,
                desktop_state=state,
                error=message,
                point=point,
                desktop_evidence_ref=evidence_ref,
                target_window=target_window,
            )
        return _pause_desktop_action(
            action="desktop_scroll",
            summary=f"Approval required before scrolling {direction} by {scroll_units} unit(s) in '{target_window.get('title', active_window.get('title', 'the active window'))}'.",
            active_window=active_window,
            windows=windows,
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=checkpoint_target,
            checkpoint_resume_args=_pointer_checkpoint_resume_args(
                point=point,
                token=context["token"],
                active_window=active_window,
                evidence_ref=evidence_ref,
                extra={"direction": direction, "scroll_units": scroll_units},
            ),
            point=point,
            mouse_action={
                "action": "scroll",
                "point": point,
                "window_title": str(target_window.get("title", "") or active_window.get("title", "")),
                "coordinate_mode": str(action_args.get("coordinate_mode", "absolute")).strip().lower() or "absolute",
                "coordinate_mapping": coordinate_mapping,
                "scroll_direction": direction,
                "scroll_units": scroll_units,
                "reason": "scrolled",
                "summary": f"Prepared a bounded scroll {direction} by {scroll_units} unit(s).",
            },
            desktop_evidence_ref=evidence_ref,
        )

    original_point = _current_cursor_point()
    moved = bool(user32.SetCursorPos(int(point.get("x", 0) or 0), int(point.get("y", 0) or 0)))
    scrolled = moved and _send_mouse_scroll(direction, scroll_units)
    try:
        user32.SetCursorPos(int(original_point.get("x", 0) or 0), int(original_point.get("y", 0) or 0))
    except Exception:
        pass
    active_after = _active_window_info()
    observation_after = _register_observation(active_window=active_after, windows=_enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT))
    verification = (
        _sample_desktop_action_verification(
            action="desktop_scroll",
            validator_family=str(strategy_view.get("validator_family", "") or "click_navigation"),
            strategy_family=str(strategy_view.get("strategy_family", "") or "direct_interaction"),
            before_active_window=active_window,
            before_windows=windows,
            expected_title=str(target_window.get("title", "") or action_args.get("expected_window_title", "")).strip(),
            expected_window_id=str(target_window.get("window_id", "") or action_args.get("expected_window_id", "")).strip(),
            expected_process_names=[str(target_window.get("process_name", "")).strip()],
            target_description=f"scroll {direction} in {target_window.get('title', active_window.get('title', 'the active window'))}",
            sample_count=_coerce_int(action_args.get("verification_samples", DESKTOP_DEFAULT_VERIFICATION_SAMPLES), DESKTOP_DEFAULT_VERIFICATION_SAMPLES, minimum=2, maximum=4),
            interval_ms=_coerce_int(action_args.get("verification_interval_ms", DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS), DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS, minimum=80, maximum=320),
        )
        if scrolled
        else {}
    )
    mouse_action = {
        "action": "scroll",
        "point": point,
        "window_title": str(target_window.get("title", "") or active_window.get("title", "")),
        "coordinate_mode": str(action_args.get("coordinate_mode", "absolute")).strip().lower() or "absolute",
        "coordinate_mapping": coordinate_mapping,
        "scroll_direction": direction,
        "scroll_units": scroll_units,
        "reason": "scrolled" if scrolled else "error",
        "summary": (
            f"Scrolled {direction} by {scroll_units} unit(s) in '{active_after.get('title', target_window.get('title', 'the active window'))}'."
            if scrolled
            else "Could not perform the bounded desktop scroll."
        ),
    }
    return _desktop_result(
        ok=scrolled,
        action="desktop_scroll",
        summary=mouse_action["summary"],
        desktop_state=observation_after,
        error="" if scrolled else "Could not perform the bounded desktop scroll.",
        approval_status="approved",
        workflow_resumed=_coerce_bool(action_args.get("resume_from_checkpoint", False), False),
        point=point,
        mouse_action=mouse_action,
        desktop_evidence_ref=evidence_ref,
        target_window=target_window,
        recovery=recovered.get("recovery", {}),
        recovery_attempts=recovered.get("recovery_attempts", []),
        window_readiness=recovered.get("readiness", {}),
        visual_stability=recovered.get("visual_stability", {}),
        process_context=recovered.get("process_context", {}),
        scene=recovered.get("scene", {}),
        desktop_strategy=strategy_view,
        desktop_verification=verification,
    )


def _send_text(value: str) -> bool:
    inputs: List[INPUT] = []
    for char in value:
        codepoint = ord(char)
        inputs.append(INPUT(type=INPUT_KEYBOARD, union=INPUT_UNION(ki=KEYBDINPUT(0, codepoint, KEYEVENTF_UNICODE, 0, None))))
        inputs.append(INPUT(type=INPUT_KEYBOARD, union=INPUT_UNION(ki=KEYBDINPUT(0, codepoint, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None))))
    if not inputs:
        return True
    payload = (INPUT * len(inputs))(*inputs)
    sent = int(user32.SendInput(len(inputs), ctypes.byref(payload), ctypes.sizeof(INPUT)) or 0)
    return sent == len(inputs)


def _normalize_modifier_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    normalized: List[str] = []
    for item in value:
        token = str(item or "").strip().lower()
        if token not in DESKTOP_SAFE_MODIFIER_VK:
            continue
        canonical = "ctrl" if token == "control" else token
        if canonical not in normalized:
            normalized.append(canonical)
    return normalized[:2]


def _normalize_key_name(value: Any) -> str:
    token = " ".join(str(value or "").strip().lower().split())
    token = token.replace("page up", "pageup").replace("page down", "pagedown")
    token = token.replace("arrow up", "arrowup").replace("arrow down", "arrowdown")
    token = token.replace("arrow left", "arrowleft").replace("arrow right", "arrowright")
    if token == "escape":
        token = "esc"
    if token == "del":
        token = "delete"
    return token


def _is_modifier_shortcut_only(key_name: str, modifiers: List[str]) -> bool:
    return len(key_name) == 1 and key_name.isalpha() and "ctrl" in modifiers


def _validate_desktop_key_request(key_name: str, modifiers: List[str]) -> str:
    if key_name not in DESKTOP_SAFE_KEY_VK:
        return (
            "This bounded desktop keyboard tool only supports safe navigation keys and a small allowlist "
            "of Ctrl-based shortcuts."
        )
    if len(key_name) == 1 and key_name.isalpha() and not _is_modifier_shortcut_only(key_name, modifiers):
        return (
            "Single letter key presses are outside the safe desktop scope unless they are a bounded Ctrl-based shortcut. "
            "Use desktop_type_text for plain text entry."
        )
    if any(modifier not in {"ctrl", "shift"} for modifier in modifiers):
        return "Only Ctrl and Shift modifiers are allowed in this bounded desktop keyboard tool."
    return ""


def _desktop_key_sequence_preview(key_name: str, modifiers: List[str], repeat: int = 1) -> str:
    sequence = [DESKTOP_SAFE_MODIFIER_DISPLAY.get(item, item.title()) for item in modifiers]
    sequence.append(DESKTOP_SAFE_KEY_DISPLAY.get(key_name, key_name.title()))
    preview = "+".join(sequence)
    if repeat > 1:
        preview = f"{preview} x{repeat}"
    return preview


def _normalize_desktop_key_sequence(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: List[Dict[str, Any]] = []
    for raw_item in value[:DESKTOP_MAX_KEY_SEQUENCE_STEPS]:
        if not isinstance(raw_item, dict):
            continue
        key_name = _normalize_key_name(raw_item.get("key", ""))
        modifiers = _normalize_modifier_list(raw_item.get("modifiers", []))
        repeat = _coerce_int(raw_item.get("repeat", 1), 1, minimum=1, maximum=DESKTOP_MAX_KEY_REPEAT)
        if not key_name:
            continue
        items.append({"key": key_name, "modifiers": modifiers, "repeat": repeat})
    return items


def _validate_desktop_key_sequence(sequence_items: List[Dict[str, Any]]) -> str:
    if not sequence_items:
        return "Provide a bounded key sequence before using this desktop keyboard tool."
    for item in sequence_items:
        validation_error = _validate_desktop_key_request(
            str(item.get("key", "")).strip(),
            item.get("modifiers", []) if isinstance(item.get("modifiers", []), list) else [],
        )
        if validation_error:
            return validation_error
    return ""


def _desktop_key_sequence_chain_preview(sequence_items: List[Dict[str, Any]]) -> str:
    previews = [
        _desktop_key_sequence_preview(
            str(item.get("key", "")).strip(),
            item.get("modifiers", []) if isinstance(item.get("modifiers", []), list) else [],
            int(item.get("repeat", 1) or 1),
        )
        for item in sequence_items
        if isinstance(item, dict)
    ]
    return " -> ".join(part for part in previews if part)[:180]


def _send_key_sequence(key_name: str, modifiers: List[str], repeat: int = 1) -> bool:
    vk = DESKTOP_SAFE_KEY_VK.get(key_name)
    if not vk:
        return False
    repeat_count = _coerce_int(repeat, DESKTOP_DEFAULT_KEY_REPEAT, minimum=1, maximum=DESKTOP_MAX_KEY_REPEAT)
    inputs: List[INPUT] = []

    for modifier in modifiers:
        modifier_vk = DESKTOP_SAFE_MODIFIER_VK.get(modifier)
        if not modifier_vk:
            continue
        scan = int(user32.MapVirtualKeyW(modifier_vk, 0) or 0)
        inputs.append(INPUT(type=INPUT_KEYBOARD, union=INPUT_UNION(ki=KEYBDINPUT(modifier_vk, scan, 0, 0, None))))

    scan = int(user32.MapVirtualKeyW(vk, 0) or 0)
    for _ in range(repeat_count):
        inputs.append(INPUT(type=INPUT_KEYBOARD, union=INPUT_UNION(ki=KEYBDINPUT(vk, scan, 0, 0, None))))
        inputs.append(INPUT(type=INPUT_KEYBOARD, union=INPUT_UNION(ki=KEYBDINPUT(vk, scan, KEYEVENTF_KEYUP, 0, None))))

    for modifier in reversed(modifiers):
        modifier_vk = DESKTOP_SAFE_MODIFIER_VK.get(modifier)
        if not modifier_vk:
            continue
        scan = int(user32.MapVirtualKeyW(modifier_vk, 0) or 0)
        inputs.append(INPUT(type=INPUT_KEYBOARD, union=INPUT_UNION(ki=KEYBDINPUT(modifier_vk, scan, KEYEVENTF_KEYUP, 0, None))))

    if not inputs:
        return False
    payload = (INPUT * len(inputs))(*inputs)
    sent = int(user32.SendInput(len(inputs), ctypes.byref(payload), ctypes.sizeof(INPUT)) or 0)
    return sent == len(inputs)


def _send_key_sequence_chain(sequence_items: List[Dict[str, Any]]) -> bool:
    for index, item in enumerate(sequence_items):
        ok = _send_key_sequence(
            str(item.get("key", "")).strip(),
            item.get("modifiers", []) if isinstance(item.get("modifiers", []), list) else [],
            int(item.get("repeat", 1) or 1),
        )
        if not ok:
            return False
        if index < len(sequence_items) - 1:
            time.sleep(0.05)
    return True


def desktop_type_text(args: Dict[str, Any]) -> Dict[str, Any]:
    field_label = str(args.get("field_label", "")).strip()
    if not field_label:
        windows = _enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT)
        active_window = _active_window_info()
        observation = _register_observation(active_window=active_window, windows=windows)
        return _desktop_result(
            ok=False,
            action="desktop_type_text",
            summary="Provide a non-sensitive field_label before typing into the focused window.",
            desktop_state=observation,
            error="Provide a non-sensitive field_label before typing into the focused window.",
        )

    if _sensitive_field_label(field_label):
        windows = _enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT)
        active_window = _active_window_info()
        observation = _register_observation(active_window=active_window, windows=windows)
        return _desktop_result(
            ok=False,
            action="desktop_type_text",
            summary=f"Typing into '{field_label}' is outside the safe desktop scope for this pass.",
            desktop_state=observation,
            error=f"Typing into '{field_label}' is outside the safe desktop scope for this pass.",
        )

    value = str(args.get("value", ""))
    max_chars = _coerce_int(args.get("max_text_length", DESKTOP_DEFAULT_TYPE_MAX_CHARS), DESKTOP_DEFAULT_TYPE_MAX_CHARS, minimum=1, maximum=DESKTOP_DEFAULT_TYPE_MAX_CHARS)
    if not value.strip():
        windows = _enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT)
        active_window = _active_window_info()
        observation = _register_observation(active_window=active_window, windows=windows)
        return _desktop_result(
            ok=False,
            action="desktop_type_text",
            summary="Provide non-empty text before typing into the focused window.",
            desktop_state=observation,
            error="Provide non-empty text before typing into the focused window.",
        )
    if len(value) > max_chars:
        windows = _enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT)
        active_window = _active_window_info()
        observation = _register_observation(active_window=active_window, windows=windows)
        return _desktop_result(
            ok=False,
            action="desktop_type_text",
            summary=f"Text is too long for this bounded desktop typing tool (max {max_chars} characters).",
            desktop_state=observation,
            error=f"Text is too long for this bounded desktop typing tool (max {max_chars} characters).",
            typed_text_preview=_trim_text(value, limit=60),
        )

    strategy_context = _prepare_desktop_strategy_context(
        args,
        action_name="desktop_type_text",
        default_strategy_family="direct_input",
        default_validator_family="text_input",
    )
    if not strategy_context.get("ok", False):
        return strategy_context.get("result", {})
    action_args = strategy_context.get("args", args) if isinstance(strategy_context.get("args", args), dict) else dict(args)
    strategy_view = strategy_context.get("strategy", {}) if isinstance(strategy_context.get("strategy", {}), dict) else {}
    recovered = strategy_context.get("recovered", {}) if isinstance(strategy_context.get("recovered", {}), dict) else {}

    token, observation, observation_error = _validate_fresh_observation(action_args)
    evidence_ref = _latest_evidence_ref_for_observation(token)
    active_window = _active_window_info()
    windows = _enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT)
    if observation_error:
        state = _register_observation(active_window=active_window, windows=windows)
        return _desktop_result(
            ok=False,
            action="desktop_type_text",
            summary=observation_error,
            desktop_state=state,
            error=observation_error,
            typed_text_preview=_trim_text(value, limit=60),
            desktop_evidence_ref=evidence_ref,
        )

    if not active_window or not _foreground_window_matches(observation, active_window):
        state = _register_observation(active_window=active_window, windows=windows)
        message = "The previously inspected target window is no longer active. Focus the window and inspect desktop state again before typing."
        return _desktop_result(
            ok=False,
            action="desktop_type_text",
            summary=message,
            desktop_state=state,
            error=message,
            typed_text_preview=_trim_text(value, limit=60),
            desktop_evidence_ref=evidence_ref,
        )
    if active_window and not _window_is_on_primary_monitor(active_window):
        return _primary_monitor_activity_error(
            "desktop_type_text",
            active_window,
            windows=windows,
            desktop_evidence_ref=evidence_ref,
        )

    checkpoint_reason = str(action_args.get("checkpoint_reason", "")).strip() or (
        f"Typing into '{field_label}' in '{active_window.get('title', 'the active window')}' requires explicit approval in this bounded control pass."
    )
    checkpoint_target = field_label
    if not _approval_granted(action_args):
        if not _evidence_ref_has_screenshot(evidence_ref):
            state = _register_observation(active_window=active_window, windows=windows)
            message = "Approval-gated desktop typing needs a screenshot-backed inspection of the active window first."
            return _desktop_result(
                ok=False,
                action="desktop_type_text",
                summary=message,
                desktop_state=state,
                error=message,
                typed_text_preview=_trim_text(value, limit=60),
                desktop_evidence_ref=evidence_ref,
            )
        return _pause_desktop_action(
            action="desktop_type_text",
            summary=f"Approval required before typing into '{field_label}' in '{active_window.get('title', 'the active window')}'.",
            active_window=active_window,
            windows=windows,
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=checkpoint_target,
            checkpoint_resume_args={
                "value": value,
                "field_label": field_label,
                "observation_token": token,
                "expected_window_id": active_window.get("window_id", ""),
                "expected_window_title": active_window.get("title", ""),
                "evidence_id": evidence_ref.get("evidence_id", ""),
            },
            desktop_evidence_ref=evidence_ref,
        )

    ok = _send_text(value)
    active_after = _active_window_info()
    observation_after = _register_observation(active_window=active_after, windows=_enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT))
    verification = (
        _sample_desktop_action_verification(
            action="desktop_type_text",
            validator_family=str(strategy_view.get("validator_family", "") or "text_input"),
            strategy_family=str(strategy_view.get("strategy_family", "") or "direct_input"),
            before_active_window=active_window,
            before_windows=windows,
            expected_title=str(action_args.get("expected_window_title", "") or active_window.get("title", "")).strip(),
            expected_window_id=str(action_args.get("expected_window_id", "") or active_window.get("window_id", "")).strip(),
            expected_process_names=[str(active_window.get("process_name", "")).strip()],
            target_description=field_label,
            sample_count=_coerce_int(action_args.get("verification_samples", DESKTOP_DEFAULT_VERIFICATION_SAMPLES), DESKTOP_DEFAULT_VERIFICATION_SAMPLES, minimum=2, maximum=4),
            interval_ms=_coerce_int(action_args.get("verification_interval_ms", DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS), DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS, minimum=80, maximum=320),
        )
        if ok
        else {}
    )
    if not ok:
        return _desktop_result(
            ok=False,
            action="desktop_type_text",
            summary=f"Could not type into '{field_label}' in '{active_after.get('title', active_window.get('title', 'the active window'))}'.",
            desktop_state=observation_after,
            error=f"Could not type into '{field_label}' in '{active_window.get('title', 'the active window')}'.",
            approval_status="approved",
            typed_text_preview=_trim_text(value, limit=60),
            desktop_evidence_ref=evidence_ref,
            recovery=recovered.get("recovery", {}),
            recovery_attempts=recovered.get("recovery_attempts", []),
            window_readiness=recovered.get("readiness", {}),
            visual_stability=recovered.get("visual_stability", {}),
            process_context=recovered.get("process_context", {}),
            scene=recovered.get("scene", {}),
            desktop_strategy=strategy_view,
        )
    return _desktop_result(
        ok=True,
        action="desktop_type_text",
        summary=f"Typed into '{field_label}' in '{active_after.get('title', active_window.get('title', 'the active window'))}'.",
        desktop_state=observation_after,
        approval_status="approved",
        workflow_resumed=_coerce_bool(action_args.get("resume_from_checkpoint", False), False),
        typed_text_preview=_trim_text(value, limit=60),
        desktop_evidence_ref=evidence_ref,
        recovery=recovered.get("recovery", {}),
        recovery_attempts=recovered.get("recovery_attempts", []),
        window_readiness=recovered.get("readiness", {}),
        visual_stability=recovered.get("visual_stability", {}),
        process_context=recovered.get("process_context", {}),
        scene=recovered.get("scene", {}),
        desktop_strategy=strategy_view,
        desktop_verification=verification,
    )


def desktop_press_key(args: Dict[str, Any]) -> Dict[str, Any]:
    key_name = _normalize_key_name(args.get("key", ""))
    modifiers = _normalize_modifier_list(args.get("modifiers", []))
    repeat = _coerce_int(
        args.get("repeat", DESKTOP_DEFAULT_KEY_REPEAT),
        DESKTOP_DEFAULT_KEY_REPEAT,
        minimum=1,
        maximum=DESKTOP_MAX_KEY_REPEAT,
    )
    key_preview = _desktop_key_sequence_preview(key_name, modifiers, repeat) if key_name else ""
    validation_error = _validate_desktop_key_request(key_name, modifiers)
    if validation_error:
        windows = _enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT)
        active_window = _active_window_info()
        observation = _register_observation(active_window=active_window, windows=windows)
        return _desktop_result(
            ok=False,
            action="desktop_press_key",
            summary=validation_error,
            desktop_state=observation,
            error=validation_error,
            key_sequence_preview=key_preview,
        )

    strategy_context = _prepare_desktop_strategy_context(
        args,
        action_name="desktop_press_key",
        default_strategy_family="direct_input",
        default_validator_family="text_input",
    )
    if not strategy_context.get("ok", False):
        return strategy_context.get("result", {})
    action_args = strategy_context.get("args", args) if isinstance(strategy_context.get("args", args), dict) else dict(args)
    strategy_view = strategy_context.get("strategy", {}) if isinstance(strategy_context.get("strategy", {}), dict) else {}
    recovered = strategy_context.get("recovered", {}) if isinstance(strategy_context.get("recovered", {}), dict) else {}

    token, observation, observation_error = _validate_fresh_observation(action_args)
    evidence_ref = _latest_evidence_ref_for_observation(token)
    active_window = _active_window_info()
    windows = _enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT)
    if observation_error:
        state = _register_observation(active_window=active_window, windows=windows)
        return _desktop_result(
            ok=False,
            action="desktop_press_key",
            summary=observation_error,
            desktop_state=state,
            error=observation_error,
            key_sequence_preview=key_preview,
            desktop_evidence_ref=evidence_ref,
        )

    if not active_window or not _foreground_window_matches(observation, active_window):
        state = _register_observation(active_window=active_window, windows=windows)
        message = "The previously inspected target window is no longer active. Focus the window and inspect desktop state again before pressing a key."
        return _desktop_result(
            ok=False,
            action="desktop_press_key",
            summary=message,
            desktop_state=state,
            error=message,
            key_sequence_preview=key_preview,
            desktop_evidence_ref=evidence_ref,
        )
    if active_window and not _window_is_on_primary_monitor(active_window):
        return _primary_monitor_activity_error(
            "desktop_press_key",
            active_window,
            windows=windows,
            desktop_evidence_ref=evidence_ref,
        )

    checkpoint_reason = str(action_args.get("checkpoint_reason", "")).strip() or (
        f"Pressing {key_preview} in '{active_window.get('title', 'the active window')}' requires explicit approval in this bounded control pass."
    )
    checkpoint_target = active_window.get("title", "") or "active window"
    if not _approval_granted(action_args):
        if not _evidence_ref_has_screenshot(evidence_ref):
            state = _register_observation(active_window=active_window, windows=windows)
            message = "Approval-gated desktop key presses need a screenshot-backed inspection of the active window first."
            return _desktop_result(
                ok=False,
                action="desktop_press_key",
                summary=message,
                desktop_state=state,
                error=message,
                key_sequence_preview=key_preview,
                desktop_evidence_ref=evidence_ref,
            )
        return _pause_desktop_action(
            action="desktop_press_key",
            summary=f"Approval required before pressing {key_preview} in '{checkpoint_target}'.",
            active_window=active_window,
            windows=windows,
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=f"{checkpoint_target} :: {key_preview}",
            checkpoint_resume_args={
                "key": DESKTOP_SAFE_KEY_DISPLAY.get(key_name, key_name),
                "modifiers": modifiers,
                "repeat": repeat,
                "observation_token": token,
                "expected_window_id": active_window.get("window_id", ""),
                "expected_window_title": active_window.get("title", ""),
                "evidence_id": evidence_ref.get("evidence_id", ""),
            },
            key_sequence_preview=key_preview,
            desktop_evidence_ref=evidence_ref,
        )

    ok = _send_key_sequence(key_name, modifiers, repeat)
    active_after = _active_window_info()
    observation_after = _register_observation(active_window=active_after, windows=_enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT))
    verification = (
        _sample_desktop_action_verification(
            action="desktop_press_key",
            validator_family=str(strategy_view.get("validator_family", "") or "text_input"),
            strategy_family=str(strategy_view.get("strategy_family", "") or "direct_input"),
            before_active_window=active_window,
            before_windows=windows,
            expected_title=str(action_args.get("expected_window_title", "") or active_window.get("title", "")).strip(),
            expected_window_id=str(action_args.get("expected_window_id", "") or active_window.get("window_id", "")).strip(),
            expected_process_names=[str(active_window.get("process_name", "")).strip()],
            target_description=key_preview or "bounded key press",
            sample_count=_coerce_int(action_args.get("verification_samples", DESKTOP_DEFAULT_VERIFICATION_SAMPLES), DESKTOP_DEFAULT_VERIFICATION_SAMPLES, minimum=2, maximum=4),
            interval_ms=_coerce_int(action_args.get("verification_interval_ms", DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS), DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS, minimum=80, maximum=320),
        )
        if ok
        else {}
    )
    if not ok:
        return _desktop_result(
            ok=False,
            action="desktop_press_key",
            summary=f"Could not press {key_preview} in '{active_after.get('title', active_window.get('title', 'the active window'))}'.",
            desktop_state=observation_after,
            error=f"Could not press {key_preview} in '{active_window.get('title', 'the active window')}'.",
            approval_status="approved",
            workflow_resumed=_coerce_bool(action_args.get("resume_from_checkpoint", False), False),
            key_sequence_preview=key_preview,
            desktop_evidence_ref=evidence_ref,
            recovery=recovered.get("recovery", {}),
            recovery_attempts=recovered.get("recovery_attempts", []),
            window_readiness=recovered.get("readiness", {}),
            visual_stability=recovered.get("visual_stability", {}),
            process_context=recovered.get("process_context", {}),
            scene=recovered.get("scene", {}),
            desktop_strategy=strategy_view,
        )
    return _desktop_result(
        ok=True,
        action="desktop_press_key",
        summary=f"Pressed {key_preview} in '{active_after.get('title', active_window.get('title', 'the active window'))}'.",
        desktop_state=observation_after,
        approval_status="approved",
        workflow_resumed=_coerce_bool(action_args.get("resume_from_checkpoint", False), False),
        key_sequence_preview=key_preview,
        desktop_evidence_ref=evidence_ref,
        recovery=recovered.get("recovery", {}),
        recovery_attempts=recovered.get("recovery_attempts", []),
        window_readiness=recovered.get("readiness", {}),
        visual_stability=recovered.get("visual_stability", {}),
        process_context=recovered.get("process_context", {}),
        scene=recovered.get("scene", {}),
        desktop_strategy=strategy_view,
        desktop_verification=verification,
    )


def _current_desktop_context(*, limit: int = DESKTOP_DEFAULT_WINDOW_LIMIT) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]]:
    active_window = _active_window_info()
    windows = _enum_windows(limit=limit)
    observation = _register_observation(active_window=active_window, windows=windows)
    return active_window, windows, observation


def _dedupe_windows(*windows_groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for group in windows_groups:
        for item in list(group or []):
            if not isinstance(item, dict):
                continue
            window_id = str(item.get("window_id", "")).strip()
            dedupe_key = window_id or f"{item.get('title', '')}|{item.get('pid', '')}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            merged.append(item)
    return merged


def _open_match_score(window: Dict[str, Any], target_info: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(window, dict) or not isinstance(target_info, dict):
        return {"score": 0, "reasons": []}

    title = str(window.get("title", "")).strip().lower()
    process_name = str(window.get("process_name", "")).strip().lower()
    class_name = str(window.get("class_name", "")).strip().lower()
    basename = str(target_info.get("basename", "")).strip().lower()
    stem = str(target_info.get("stem", "")).strip().lower()
    parent_name = str(target_info.get("parent_name", "")).strip().lower()
    target_class = str(target_info.get("target_classification", "")).strip().lower()
    title_hints = [str(item).strip().lower() for item in list(target_info.get("viewer_title_hints", [])) if str(item).strip()]
    process_hints = [str(item).strip().lower() for item in list(target_info.get("viewer_process_hints", [])) if str(item).strip()]

    score = 0
    reasons: List[str] = []
    if basename and basename in title:
        score += 85
        reasons.append("basename_in_title")
    elif stem and stem in title:
        score += 62
        reasons.append("stem_in_title")
    elif parent_name and parent_name in title and target_class == "folder_directory":
        score += 62
        reasons.append("folder_name_in_title")
    elif parent_name and parent_name in title and target_class in {"document_file", "image_media_file", "text_code_file"}:
        score += 24
        reasons.append("parent_in_title")

    for hint in title_hints:
        if hint and hint in title:
            score += 18
            reasons.append(f"title_hint:{hint}")
            break

    for hint in process_hints:
        if hint and (process_name == hint or hint in process_name):
            score += 34
            reasons.append(f"process_hint:{hint}")
            break

    if target_class == "folder_directory":
        if process_name == "explorer.exe":
            score += 24
            reasons.append("explorer_process")
        if "cabinetwclass" in class_name or "explorer" in title:
            score += 12
            reasons.append("explorer_window")
    elif target_class == "url_web_resource" and process_name in {"msedge.exe", "chrome.exe", "firefox.exe"}:
        score += 22
        reasons.append("browser_process")
    elif target_class == "executable_program":
        expected_process = f"{stem}.exe" if stem and not basename.endswith(".exe") else basename
        if expected_process and process_name == expected_process:
            score += 82
            reasons.append("exact_process")
        elif stem and stem in process_name:
            score += 54
            reasons.append("stem_in_process")

    if bool(window.get("is_active", False)) and score > 0:
        score += 8
        reasons.append("active_window")
    if bool(window.get("is_visible", False)) and score > 0:
        score += 4
        reasons.append("visible_window")
    return {"score": min(score, 100), "reasons": reasons}


def _best_open_window_candidate(windows: List[Dict[str, Any]], target_info: Dict[str, Any]) -> Dict[str, Any]:
    best_window: Dict[str, Any] = {}
    best_score = 0
    best_reasons: List[str] = []
    for window in list(windows or []):
        scored = _open_match_score(window, target_info)
        score = int(scored.get("score", 0) or 0)
        if score <= best_score:
            continue
        best_window = dict(window)
        best_score = score
        best_reasons = list(scored.get("reasons", [])) if isinstance(scored.get("reasons", []), list) else []
    if not best_window:
        return {}
    return {
        **best_window,
        "match_score": best_score,
        "match_reasons": best_reasons[:4],
    }


def _process_hint_snapshot(target_info: Dict[str, Any], *, launched_pid: int = 0) -> Dict[str, Any]:
    if launched_pid > 0:
        result = probe_process_context(pid=launched_pid)
        data = result.get("data", {}) if isinstance(result.get("data", {}), dict) else {}
        if data:
            return data
    for process_name in list(target_info.get("viewer_process_hints", []))[:3]:
        result = probe_process_context(process_name=str(process_name).strip())
        data = result.get("data", {}) if isinstance(result.get("data", {}), dict) else {}
        if data.get("running", False):
            return data
    return {}


def _sample_open_verification(
    target_info: Dict[str, Any],
    *,
    strategy_family: str,
    before_active_window: Dict[str, Any],
    before_windows: List[Dict[str, Any]],
    launched_pid: int = 0,
    sample_count: int = 3,
    interval_ms: int = 180,
) -> Dict[str, Any]:
    bounded_samples = max(2, min(4, int(sample_count or 3)))
    bounded_interval = max(80, min(320, int(interval_ms or 180))) / 1000.0
    before_ids = {
        str(item.get("window_id", "")).strip()
        for item in list(before_windows or [])
        if isinstance(item, dict) and str(item.get("window_id", "")).strip()
    }
    before_active_id = str(before_active_window.get("window_id", "")).strip()
    before_active_title = str(before_active_window.get("title", "")).strip()
    best_candidate: Dict[str, Any] = {}
    process_snapshot: Dict[str, Any] = {}
    samples: List[Dict[str, Any]] = []
    saw_new_match = False
    saw_existing_match = False
    saw_active_match = False
    saw_brief_match = False

    for index in range(bounded_samples):
        if index > 0:
            time.sleep(bounded_interval)
        active_window = _active_window_info()
        visible_windows = _enum_windows(include_minimized=True, include_hidden=True, limit=24)
        candidate = _best_open_window_candidate(_dedupe_windows([active_window], visible_windows), target_info)
        process_snapshot = _process_hint_snapshot(target_info, launched_pid=launched_pid) or process_snapshot
        candidate_score = int(candidate.get("match_score", 0) or 0)
        window_id = str(candidate.get("window_id", "")).strip()
        if candidate_score > int(best_candidate.get("match_score", 0) or 0):
            best_candidate = dict(candidate)
        if candidate_score >= 65:
            saw_brief_match = True
        if candidate_score >= 78:
            if window_id and window_id not in before_ids:
                saw_new_match = True
            elif window_id:
                saw_existing_match = True
            if bool(candidate.get("is_active", False)) or (window_id and window_id == str(active_window.get("window_id", "")).strip()):
                saw_active_match = True
        samples.append(
            {
                "active_window_title": _trim_text(active_window.get("title", ""), limit=140),
                "active_window_process": _trim_text(active_window.get("process_name", ""), limit=80),
                "candidate_title": _trim_text(candidate.get("title", ""), limit=140),
                "candidate_process": _trim_text(candidate.get("process_name", ""), limit=80),
                "candidate_window_id": _trim_text(candidate.get("window_id", ""), limit=40),
                "candidate_score": candidate_score,
            }
        )

    active_window_after = _active_window_info()
    active_window_changed = bool(
        str(active_window_after.get("window_id", "")).strip()
        and str(active_window_after.get("window_id", "")).strip() != before_active_id
    ) or bool(
        str(active_window_after.get("title", "")).strip()
        and str(active_window_after.get("title", "")).strip() != before_active_title
    )
    matched_window = bool(best_candidate)
    matched_existing_window = matched_window and str(best_candidate.get("window_id", "")).strip() in before_ids
    matched_active_window = matched_window and bool(best_candidate.get("is_active", False))
    process_detected = bool(process_snapshot.get("running", False))

    status = "not_observed"
    confidence = "low"
    note = "No clear window or process change confirmed that the target opened."
    if saw_new_match and (saw_active_match or active_window_changed):
        status = "verified_new_window"
        confidence = "high"
        note = "A new matching window surfaced and became active after the open attempt."
    elif saw_new_match:
        status = "verified_new_window"
        confidence = "medium"
        note = "A new matching window surfaced after the open attempt."
    elif matched_existing_window and saw_active_match:
        status = "verified_reused_window"
        confidence = "medium"
        note = "A matching existing viewer window appears to have been reused and surfaced."
    elif matched_existing_window:
        status = "likely_opened_background"
        confidence = "low"
        note = "A matching existing window was detected, but it did not clearly surface to the foreground."
    elif process_detected and str(target_info.get("target_classification", "")).strip() == "executable_program":
        status = "process_started_only"
        confidence = "low"
        note = "The target process started, but a visible window was not clearly confirmed."
    elif saw_brief_match:
        status = "brief_signal_only"
        confidence = "low"
        note = "A brief matching window signal appeared, but the result was not stable enough to confirm success."

    return {
        "status": status,
        "confidence": confidence,
        "note": note,
        "matched_window": matched_window,
        "matched_existing_window": matched_existing_window,
        "matched_active_window": matched_active_window,
        "likely_opened_behind": matched_existing_window and not saw_active_match,
        "process_detected": process_detected,
        "active_window_changed": active_window_changed,
        "matched_window_title": _trim_text(best_candidate.get("title", ""), limit=180),
        "matched_window_id": _trim_text(best_candidate.get("window_id", ""), limit=40),
        "matched_process_name": _trim_text(best_candidate.get("process_name", ""), limit=120),
        "match_score": int(best_candidate.get("match_score", 0) or 0),
        "strategy_family": _trim_text(strategy_family, limit=60),
        "samples": samples[:4],
    }


def _open_target_display(target_info: Dict[str, Any]) -> str:
    return str(target_info.get("basename", "") or target_info.get("target", "")).strip() or "target"


def _open_target_summary(target_info: Dict[str, Any], strategy_family: str, verification: Dict[str, Any]) -> str:
    target_display = _open_target_display(target_info)
    verification_note = str((verification or {}).get("note", "")).strip()
    if strategy_family == "focus_existing_window":
        return f"Focused the existing window for '{target_display}'."
    if verification_note:
        return f"Attempted to open '{target_display}'. {verification_note}"
    return f"Attempted to open '{target_display}' via {strategy_family.replace('_', ' ')}."


def _active_window_process_target(active_window: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(active_window, dict):
        return {}
    return {
        "pid": _coerce_int(active_window.get("pid", 0), 0, minimum=0, maximum=10_000_000),
        "process_name": str(active_window.get("process_name", "")).strip(),
    }


def desktop_press_key_sequence(args: Dict[str, Any]) -> Dict[str, Any]:
    sequence_items = _normalize_desktop_key_sequence(args.get("sequence", []))
    key_preview = _desktop_key_sequence_chain_preview(sequence_items)
    validation_error = _validate_desktop_key_sequence(sequence_items)
    if validation_error:
        active_window, windows, observation = _current_desktop_context()
        return _desktop_result(
            ok=False,
            action="desktop_press_key_sequence",
            summary=validation_error,
            desktop_state=observation,
            error=validation_error,
            key_sequence_preview=key_preview,
            target_window=active_window,
        )

    strategy_context = _prepare_desktop_strategy_context(
        args,
        action_name="desktop_press_key_sequence",
        default_strategy_family="direct_input",
        default_validator_family="text_input",
    )
    if not strategy_context.get("ok", False):
        return strategy_context.get("result", {})
    action_args = strategy_context.get("args", args) if isinstance(strategy_context.get("args", args), dict) else dict(args)
    strategy_view = strategy_context.get("strategy", {}) if isinstance(strategy_context.get("strategy", {}), dict) else {}
    recovered = strategy_context.get("recovered", {}) if isinstance(strategy_context.get("recovered", {}), dict) else {}

    token, observation, observation_error = _validate_fresh_observation(action_args)
    evidence_ref = _latest_evidence_ref_for_observation(token)
    active_window, windows, current_observation = _current_desktop_context()
    if observation_error:
        return _desktop_result(
            ok=False,
            action="desktop_press_key_sequence",
            summary=observation_error,
            desktop_state=current_observation,
            error=observation_error,
            key_sequence_preview=key_preview,
            desktop_evidence_ref=evidence_ref,
            target_window=active_window,
        )

    if not active_window or not _foreground_window_matches(observation, active_window):
        message = "The previously inspected target window is no longer active. Focus the window and inspect desktop state again before sending a bounded key sequence."
        return _desktop_result(
            ok=False,
            action="desktop_press_key_sequence",
            summary=message,
            desktop_state=current_observation,
            error=message,
            key_sequence_preview=key_preview,
            desktop_evidence_ref=evidence_ref,
            target_window=active_window,
        )
    if active_window and not _window_is_on_primary_monitor(active_window):
        return _primary_monitor_activity_error(
            "desktop_press_key_sequence",
            active_window,
            windows=windows,
            desktop_evidence_ref=evidence_ref,
        )

    checkpoint_reason = str(action_args.get("checkpoint_reason", "")).strip() or (
        f"Pressing the bounded key sequence {key_preview} in '{active_window.get('title', 'the active window')}' requires explicit approval in this control pass."
    )
    checkpoint_target = active_window.get("title", "") or "active window"
    if not _approval_granted(action_args):
        if not _evidence_ref_has_screenshot(evidence_ref):
            message = "Approval-gated desktop key sequences need a screenshot-backed inspection of the active window first."
            return _desktop_result(
                ok=False,
                action="desktop_press_key_sequence",
                summary=message,
                desktop_state=current_observation,
                error=message,
                key_sequence_preview=key_preview,
                desktop_evidence_ref=evidence_ref,
                target_window=active_window,
            )
        return _pause_desktop_action(
            action="desktop_press_key_sequence",
            summary=f"Approval required before pressing {key_preview} in '{checkpoint_target}'.",
            active_window=active_window,
            windows=windows,
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=f"{checkpoint_target} :: {key_preview}",
            checkpoint_resume_args={
                "sequence": sequence_items,
                "observation_token": token,
                "expected_window_id": active_window.get("window_id", ""),
                "expected_window_title": active_window.get("title", ""),
                "evidence_id": evidence_ref.get("evidence_id", ""),
            },
            key_sequence_preview=key_preview,
            desktop_evidence_ref=evidence_ref,
        )

    ok = _send_key_sequence_chain(sequence_items)
    active_after, visible_after, observation_after = _current_desktop_context()
    verification = (
        _sample_desktop_action_verification(
            action="desktop_press_key_sequence",
            validator_family=str(strategy_view.get("validator_family", "") or "text_input"),
            strategy_family=str(strategy_view.get("strategy_family", "") or "direct_input"),
            before_active_window=active_window,
            before_windows=windows,
            expected_title=str(action_args.get("expected_window_title", "") or active_window.get("title", "")).strip(),
            expected_window_id=str(action_args.get("expected_window_id", "") or active_window.get("window_id", "")).strip(),
            expected_process_names=[str(active_window.get("process_name", "")).strip()],
            target_description=key_preview or "bounded key sequence",
            sample_count=_coerce_int(action_args.get("verification_samples", DESKTOP_DEFAULT_VERIFICATION_SAMPLES), DESKTOP_DEFAULT_VERIFICATION_SAMPLES, minimum=2, maximum=4),
            interval_ms=_coerce_int(action_args.get("verification_interval_ms", DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS), DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS, minimum=80, maximum=320),
        )
        if ok
        else {}
    )
    return _desktop_result(
        ok=ok,
        action="desktop_press_key_sequence",
        summary=(
            f"Pressed {key_preview} in '{active_after.get('title', active_window.get('title', 'the active window'))}'."
            if ok
            else f"Could not press the bounded key sequence {key_preview} in '{active_window.get('title', 'the active window')}'."
        ),
        desktop_state=observation_after,
        error="" if ok else f"Could not press the bounded key sequence {key_preview}.",
        approval_status="approved",
        workflow_resumed=_coerce_bool(action_args.get("resume_from_checkpoint", False), False),
        key_sequence_preview=key_preview,
        desktop_evidence_ref=evidence_ref,
        target_window=active_after or active_window,
        recovery=recovered.get("recovery", {}),
        recovery_attempts=recovered.get("recovery_attempts", []),
        window_readiness=recovered.get("readiness", {}),
        visual_stability=recovered.get("visual_stability", {}),
        process_context=recovered.get("process_context", {}),
        scene=recovered.get("scene", {}),
        desktop_strategy=strategy_view,
        desktop_verification=verification,
    )


def desktop_list_processes(args: Dict[str, Any]) -> Dict[str, Any]:
    active_window, windows, observation = _current_desktop_context()
    query = str(args.get("query", "")).strip()
    limit = _coerce_int(
        args.get("limit", DESKTOP_DEFAULT_PROCESS_LIMIT),
        DESKTOP_DEFAULT_PROCESS_LIMIT,
        minimum=1,
        maximum=DESKTOP_MAX_PROCESS_LIMIT,
    )
    include_background = _coerce_bool(args.get("include_background", True), True)
    process_result = list_process_contexts(query=query, limit=limit, include_background=include_background)
    payload = process_result.get("data", {}) if isinstance(process_result.get("data", {}), dict) else {}
    processes = payload.get("processes", []) if isinstance(payload.get("processes", []), list) else []
    summary = str(process_result.get("message", "")).strip() or (
        f"Listed {len(processes)} bounded process candidates."
        if processes
        else "No bounded desktop processes matched the current query."
    )
    return _desktop_result(
        ok=bool(process_result.get("ok", False)),
        action="desktop_list_processes",
        summary=summary,
        desktop_state=observation,
        error=str(process_result.get("error", "")).strip(),
        processes=processes,
        process_action={
            "action": "list",
            "reason": str(process_result.get("reason", "process_inspected")).strip() or "process_inspected",
            "summary": summary,
        },
        process_context=processes[0] if len(processes) == 1 and isinstance(processes[0], dict) else {},
        target_window=active_window,
    )


def desktop_inspect_process(args: Dict[str, Any]) -> Dict[str, Any]:
    active_window, windows, observation = _current_desktop_context()
    pid = _coerce_int(args.get("pid", 0), 0, minimum=0, maximum=10_000_000)
    process_name = str(args.get("process_name", "")).strip()
    owned_label = str(args.get("owned_label", "")).strip()
    if pid <= 0 and not process_name and not owned_label:
        active_target = _active_window_process_target(active_window)
        pid = int(active_target.get("pid", 0) or 0)
        process_name = str(active_target.get("process_name", "")).strip()
    if pid <= 0 and not process_name and not owned_label:
        message = "No bounded desktop process target was available. Provide pid, process_name, owned_label, or inspect a surfaced window first."
        return _desktop_result(
            ok=False,
            action="desktop_inspect_process",
            summary=message,
            desktop_state=observation,
            error=message,
            target_window=active_window,
        )

    child_limit = _coerce_int(args.get("child_limit", 4), 4, minimum=0, maximum=8)
    process_result = inspect_process_details(pid=pid, process_name=process_name or owned_label, child_limit=child_limit)
    payload = process_result.get("data", {}) if isinstance(process_result.get("data", {}), dict) else {}
    process_context = payload.get("process", {}) if isinstance(payload.get("process", {}), dict) else {}
    children = payload.get("children", []) if isinstance(payload.get("children", []), list) else []
    summary = str(process_result.get("message", "")).strip() or str(process_context.get("summary", "")).strip() or "Inspected the requested bounded process context."
    return _desktop_result(
        ok=bool(process_result.get("ok", False)),
        action="desktop_inspect_process",
        summary=summary,
        desktop_state=observation,
        error=str(process_result.get("error", "")).strip(),
        process_context=process_context,
        processes=children,
        process_action={
            "action": "inspect",
            "pid": int(process_context.get("pid", pid) or pid),
            "process_name": str(process_context.get("process_name", "") or process_name or owned_label),
            "owned": bool(payload.get("owned", False)),
            "owned_label": str(payload.get("owned_label", "")).strip(),
            "reason": str(process_result.get("reason", "process_inspected")).strip() or "process_inspected",
            "summary": summary,
        },
        target_window=active_window,
    )


def desktop_start_process(args: Dict[str, Any]) -> Dict[str, Any]:
    action_args = dict(args)
    strategy_view = _desktop_strategy_view(
        action_args,
        action="desktop_start_process",
        default_strategy_family="direct_launch",
        default_validator_family="open_launch",
    )
    active_window, windows, observation = _current_desktop_context()
    token = str(action_args.get("observation_token", "")).strip()
    evidence_ref = _latest_evidence_ref_for_observation(token) if token else {}
    executable = str(action_args.get("executable", "")).strip()
    arguments = action_args.get("arguments", [])
    if not isinstance(arguments, list):
        arguments = []
    bounded_arguments = [_trim_text(item, limit=180) for item in arguments[:8] if _trim_text(item, limit=180)]
    owned_label = str(action_args.get("owned_label", "")).strip() or Path(executable).stem
    checkpoint_target = owned_label or executable or "bounded desktop process"
    checkpoint_reason = str(action_args.get("checkpoint_reason", "")).strip() or (
        f"Starting the bounded process '{checkpoint_target}' requires explicit approval in this control pass."
    )
    if not executable:
        message = "Provide an executable path before starting a bounded desktop process."
        return _desktop_result(
            ok=False,
            action="desktop_start_process",
            summary=message,
            desktop_state=observation,
            error=message,
            desktop_evidence_ref=evidence_ref,
            target_window=active_window,
        )
    if not _approval_granted(action_args):
        return _pause_desktop_action(
            action="desktop_start_process",
            summary=f"Approval required before starting '{checkpoint_target}'.",
            active_window=active_window,
            windows=windows,
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=checkpoint_target,
            checkpoint_resume_args={
                "executable": executable,
                "arguments": bounded_arguments,
                "cwd": str(action_args.get("cwd", "")).strip(),
                "owned_label": owned_label,
                "shell_kind": str(action_args.get("shell_kind", "")).strip(),
                "observation_token": token,
                "evidence_id": evidence_ref.get("evidence_id", ""),
            },
            desktop_evidence_ref=evidence_ref,
        )

    process_result = start_owned_process(
        executable=executable,
        args=bounded_arguments,
        cwd=str(action_args.get("cwd", "")).strip(),
        env=action_args.get("env", {}) if isinstance(action_args.get("env", {}), dict) else {},
        owned_label=owned_label,
    )
    payload = process_result.get("data", {}) if isinstance(process_result.get("data", {}), dict) else {}
    process_context = payload.get("process", {}) if isinstance(payload.get("process", {}), dict) else {}
    observation_after = _register_observation(active_window=_active_window_info(), windows=_enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT))
    summary = str(process_result.get("message", "")).strip() or "Started the requested bounded process."
    verification = (
        _sample_desktop_action_verification(
            action="desktop_start_process",
            validator_family=str(strategy_view.get("validator_family", "") or "open_launch"),
            strategy_family=str(strategy_view.get("strategy_family", "") or "direct_launch"),
            before_active_window=active_window,
            before_windows=windows,
            expected_title=Path(executable).stem,
            expected_process_names=[str(process_context.get("process_name", "")).strip() or Path(executable).name.lower()],
            target_description=checkpoint_target,
            launched_pid=int(process_context.get("pid", 0) or 0),
            sample_count=_coerce_int(action_args.get("verification_samples", DESKTOP_DEFAULT_VERIFICATION_SAMPLES), DESKTOP_DEFAULT_VERIFICATION_SAMPLES, minimum=2, maximum=4),
            interval_ms=_coerce_int(action_args.get("verification_interval_ms", DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS), DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS, minimum=80, maximum=320),
        )
        if bool(process_result.get("ok", False))
        else {}
    )
    return _desktop_result(
        ok=bool(process_result.get("ok", False)),
        action="desktop_start_process",
        summary=summary,
        desktop_state=observation_after,
        error=str(process_result.get("error", "")).strip(),
        approval_status="approved",
        workflow_resumed=_coerce_bool(action_args.get("resume_from_checkpoint", False), False),
        process_context=process_context,
        process_action={
            "action": "start",
            "pid": int(process_context.get("pid", 0) or 0),
            "process_name": str(process_context.get("process_name", "")).strip() or Path(executable).name,
            "owned": bool(payload.get("owned", False)),
            "owned_label": str(payload.get("owned_label", "")).strip(),
            "reason": str(process_result.get("reason", "process_started")).strip() or "process_started",
            "summary": summary,
        },
        target_window=active_window,
        desktop_strategy=strategy_view,
        desktop_verification=verification,
    )


def desktop_stop_process(args: Dict[str, Any]) -> Dict[str, Any]:
    active_window, windows, observation = _current_desktop_context()
    token = str(args.get("observation_token", "")).strip()
    evidence_ref = _latest_evidence_ref_for_observation(token) if token else {}
    pid = _coerce_int(args.get("pid", 0), 0, minimum=0, maximum=10_000_000)
    owned_label = str(args.get("owned_label", "")).strip()
    if pid <= 0 and not owned_label:
        active_target = _active_window_process_target(active_window)
        pid = int(active_target.get("pid", 0) or 0)
    checkpoint_target = owned_label or (str(pid) if pid > 0 else "owned bounded process")
    checkpoint_reason = str(args.get("checkpoint_reason", "")).strip() or (
        f"Stopping the bounded owned process '{checkpoint_target}' requires explicit approval in this control pass."
    )
    if pid <= 0 and not owned_label:
        message = "Provide an owned process pid or owned_label before stopping a bounded desktop process."
        return _desktop_result(
            ok=False,
            action="desktop_stop_process",
            summary=message,
            desktop_state=observation,
            error=message,
            desktop_evidence_ref=evidence_ref,
            target_window=active_window,
        )
    if not _approval_granted(args):
        return _pause_desktop_action(
            action="desktop_stop_process",
            summary=f"Approval required before stopping '{checkpoint_target}'.",
            active_window=active_window,
            windows=windows,
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=checkpoint_target,
            checkpoint_resume_args={
                "pid": pid,
                "owned_label": owned_label,
                "wait_seconds": _coerce_int(args.get("wait_seconds", 2), 2, minimum=1, maximum=5),
                "observation_token": token,
                "evidence_id": evidence_ref.get("evidence_id", ""),
            },
            desktop_evidence_ref=evidence_ref,
        )

    process_result = stop_owned_process(
        pid=pid,
        owned_label=owned_label,
        wait_seconds=float(_coerce_int(args.get("wait_seconds", 2), 2, minimum=1, maximum=5)),
    )
    payload = process_result.get("data", {}) if isinstance(process_result.get("data", {}), dict) else {}
    process_context = payload.get("process", {}) if isinstance(payload.get("process", {}), dict) else {}
    observation_after = _register_observation(active_window=_active_window_info(), windows=_enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT))
    summary = str(process_result.get("message", "")).strip() or "Stopped the requested bounded owned process."
    return _desktop_result(
        ok=bool(process_result.get("ok", False)),
        action="desktop_stop_process",
        summary=summary,
        desktop_state=observation_after,
        error=str(process_result.get("error", "")).strip(),
        approval_status="approved",
        workflow_resumed=_coerce_bool(args.get("resume_from_checkpoint", False), False),
        process_context=process_context,
        process_action={
            "action": "stop",
            "pid": int(process_context.get("pid", pid) or pid),
            "process_name": str(process_context.get("process_name", "")).strip() or str(checkpoint_target).strip(),
            "owned": bool(payload.get("owned", False)),
            "owned_label": str(payload.get("owned_label", "")).strip(),
            "reason": str(process_result.get("reason", "process_stopped")).strip() or "process_stopped",
            "summary": summary,
        },
        target_window=active_window,
    )


def desktop_run_command(args: Dict[str, Any]) -> Dict[str, Any]:
    action_args = dict(args)
    strategy_view = _desktop_strategy_view(
        action_args,
        action="desktop_run_command",
        default_strategy_family="command_open" if str(action_args.get("validator_family", "")).strip() == "open_launch" else "bounded_command",
        default_validator_family=str(action_args.get("validator_family", "")).strip(),
    )
    active_window, windows, observation = _current_desktop_context()
    token = str(action_args.get("observation_token", "")).strip()
    evidence_ref = _latest_evidence_ref_for_observation(token) if token else {}
    command = str(action_args.get("command", "")).strip()
    shell_kind = str(action_args.get("shell_kind", "powershell")).strip().lower() or "powershell"
    timeout_seconds = _coerce_int(
        action_args.get("timeout_seconds", DESKTOP_DEFAULT_COMMAND_TIMEOUT_SECONDS),
        DESKTOP_DEFAULT_COMMAND_TIMEOUT_SECONDS,
        minimum=1,
        maximum=DESKTOP_MAX_COMMAND_TIMEOUT_SECONDS,
    )
    checkpoint_reason = str(action_args.get("checkpoint_reason", "")).strip() or (
        "Running the requested bounded local command requires explicit approval in this control pass."
    )
    checkpoint_target = _trim_text(command, limit=120) or "bounded command"
    if not command:
        message = "Provide a bounded command string before running a local desktop command."
        return _desktop_result(
            ok=False,
            action="desktop_run_command",
            summary=message,
            desktop_state=observation,
            error=message,
            desktop_evidence_ref=evidence_ref,
            target_window=active_window,
        )
    if not _approval_granted(action_args):
        return _pause_desktop_action(
            action="desktop_run_command",
            summary=f"Approval required before running '{checkpoint_target}'.",
            active_window=active_window,
            windows=windows,
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=checkpoint_target,
            checkpoint_resume_args={
                "command": command,
                "cwd": str(action_args.get("cwd", "")).strip(),
                "shell_kind": shell_kind,
                "timeout_seconds": timeout_seconds,
                "observation_token": token,
                "evidence_id": evidence_ref.get("evidence_id", ""),
            },
            desktop_evidence_ref=evidence_ref,
        )

    command_result = run_bounded_command(
        command=command,
        cwd=str(action_args.get("cwd", "")).strip(),
        env=action_args.get("env", {}) if isinstance(action_args.get("env", {}), dict) else {},
        timeout_seconds=float(timeout_seconds),
        shell_kind=shell_kind,
    )
    payload = command_result.get("data", {}) if isinstance(command_result.get("data", {}), dict) else {}
    observation_after = _register_observation(active_window=_active_window_info(), windows=_enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT))
    summary = str(command_result.get("message", "")).strip() or str(payload.get("summary", "")).strip() or "Ran the bounded local command."
    verification_family = str(strategy_view.get("validator_family", "")).strip()
    verification = (
        _sample_desktop_action_verification(
            action="desktop_run_command",
            validator_family=verification_family,
            strategy_family=str(strategy_view.get("strategy_family", "") or "command_open"),
            before_active_window=active_window,
            before_windows=windows,
            target_description=checkpoint_target,
            sample_count=_coerce_int(action_args.get("verification_samples", DESKTOP_DEFAULT_VERIFICATION_SAMPLES), DESKTOP_DEFAULT_VERIFICATION_SAMPLES, minimum=2, maximum=4),
            interval_ms=_coerce_int(action_args.get("verification_interval_ms", DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS), DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS, minimum=80, maximum=320),
        )
        if bool(command_result.get("ok", False)) and verification_family == "open_launch"
        else {}
    )
    return _desktop_result(
        ok=bool(command_result.get("ok", False)),
        action="desktop_run_command",
        summary=summary,
        desktop_state=observation_after,
        error=str(command_result.get("error", "")).strip(),
        approval_status="approved",
        workflow_resumed=_coerce_bool(action_args.get("resume_from_checkpoint", False), False),
        command_result=payload,
        target_window=active_window,
        desktop_strategy=strategy_view,
        desktop_verification=verification,
    )


def desktop_open_target(args: Dict[str, Any]) -> Dict[str, Any]:
    active_window, windows, observation = _current_desktop_context(limit=20)
    token = str(args.get("observation_token", "")).strip()
    evidence_ref = _latest_evidence_ref_for_observation(token) if token else {}
    target = str(args.get("target", "")).strip()
    explicit_target_type = str(args.get("target_type", "")).strip()
    cwd = str(args.get("cwd", "")).strip()
    planning_goal = str(args.get("planning_goal", "") or args.get("goal", "")).strip()
    requested_method = str(args.get("preferred_method", "") or args.get("requested_method", "")).strip()
    bounded_arguments = [
        _trim_text(item, limit=180)
        for item in list(args.get("arguments", []))[:8]
        if _trim_text(item, limit=180)
    ]

    if not target:
        message = "Provide a Windows file, folder, URL, or executable target before trying to open it."
        result = _desktop_result(
            ok=False,
            action="desktop_open_target",
            summary=message,
            desktop_state=observation,
            error=message,
            desktop_evidence_ref=evidence_ref,
            target_window=active_window,
        )
        result["open_target"] = {
            "target": "",
            "target_classification": "unknown_ambiguous_path",
            "target_signature": "",
        }
        return result

    target_info = classify_open_target(target, cwd=cwd, explicit_target_type=explicit_target_type)
    request_preferences = infer_open_request_preferences(
        " ".join(part for part in (planning_goal, requested_method, explicit_target_type) if str(part).strip()),
        args,
    )
    existing_window = _best_open_window_candidate(_dedupe_windows([active_window], windows), target_info)
    existing_window_match = int(existing_window.get("match_score", 0) or 0) >= 78
    avoid_strategy_families = [
        str(item).strip()
        for item in list(args.get("avoid_strategy_families", []))
        if str(item).strip()
    ]
    strategy = choose_windows_open_strategy(
        target_info,
        preferred_method=request_preferences.get("preferred_method", "") or requested_method,
        avoid_strategy_families=avoid_strategy_families,
        existing_window_match=existing_window_match,
        force_strategy_switch=bool(
            request_preferences.get("force_strategy_switch", False) or _coerce_bool(args.get("force_strategy_switch", False), False)
        ),
    )
    strategy_family = str(strategy.get("strategy_family", "")).strip()
    if strategy_family == "focus_existing_window" and not existing_window_match:
        strategy = choose_windows_open_strategy(
            target_info,
            preferred_method="",
            avoid_strategy_families=[*avoid_strategy_families, "focus_existing_window"],
            existing_window_match=False,
            force_strategy_switch=True,
        )
        strategy_family = str(strategy.get("strategy_family", "")).strip()

    target_display = _open_target_display(target_info)
    checkpoint_reason = str(args.get("checkpoint_reason", "")).strip() or (
        f"Opening '{target_display}' with the Windows {strategy_family.replace('_', ' ')} path requires explicit approval in this control pass."
    )
    checkpoint_target = target_display
    if not _approval_granted(args):
        paused = _pause_desktop_action(
            action="desktop_open_target",
            summary=f"Approval required before opening '{target_display}'.",
            active_window=active_window,
            windows=windows,
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=checkpoint_target,
            checkpoint_resume_args={
                "target": target,
                "target_type": explicit_target_type,
                "preferred_method": requested_method or request_preferences.get("preferred_method", ""),
                "force_strategy_switch": bool(
                    request_preferences.get("force_strategy_switch", False)
                    or _coerce_bool(args.get("force_strategy_switch", False), False)
                ),
                "cwd": cwd,
                "arguments": bounded_arguments,
                "env": args.get("env", {}) if isinstance(args.get("env", {}), dict) else {},
                "avoid_strategy_families": avoid_strategy_families,
                "verification_samples": _coerce_int(args.get("verification_samples", 3), 3, minimum=2, maximum=4),
                "verification_interval_ms": _coerce_int(args.get("verification_interval_ms", 180), 180, minimum=80, maximum=320),
                "observation_token": token,
                "evidence_id": evidence_ref.get("evidence_id", ""),
            },
            desktop_evidence_ref=evidence_ref,
        )
        paused["open_target"] = target_info
        paused["open_strategy"] = {
            **strategy,
            "existing_window_match": existing_window_match,
            "existing_window": existing_window,
        }
        return paused

    verification_samples = _coerce_int(args.get("verification_samples", 3), 3, minimum=2, maximum=4)
    verification_interval_ms = _coerce_int(args.get("verification_interval_ms", 180), 180, minimum=80, maximum=320)
    process_context: Dict[str, Any] = {}
    target_window: Dict[str, Any] = dict(existing_window) if existing_window else {}
    recovery: Dict[str, Any] = {}
    recovery_attempts: List[Dict[str, Any]] = []
    window_readiness: Dict[str, Any] = {}
    visual_stability: Dict[str, Any] = {}
    scene: Dict[str, Any] = {}
    open_payload: Dict[str, Any] = {}
    launched_pid = 0

    if strategy_family == "focus_existing_window":
        recovery_result = _execute_window_recovery(
            {
                "window_id": str(existing_window.get("window_id", "")).strip(),
                "title": str(existing_window.get("title", "")).strip(),
                "expected_window_id": str(existing_window.get("window_id", "")).strip(),
                "expected_window_title": str(existing_window.get("title", "")).strip(),
                "exact": True,
                "limit": 16,
                "ui_limit": 6,
                "max_attempts": 1,
                "wait_seconds": 1.4,
                "poll_interval_seconds": 0.14,
                "stability_samples": 2,
                "stability_interval_ms": 120,
            },
            action_name="desktop_open_target",
        )
        recovery = recovery_result.get("recovery", {}) if isinstance(recovery_result.get("recovery", {}), dict) else {}
        recovery_attempts = recovery_result.get("recovery_attempts", []) if isinstance(recovery_result.get("recovery_attempts", []), list) else []
        window_readiness = recovery_result.get("readiness", {}) if isinstance(recovery_result.get("readiness", {}), dict) else {}
        visual_stability = recovery_result.get("visual_stability", {}) if isinstance(recovery_result.get("visual_stability", {}), dict) else {}
        process_context = recovery_result.get("process_context", {}) if isinstance(recovery_result.get("process_context", {}), dict) else {}
        scene = recovery_result.get("scene", {}) if isinstance(recovery_result.get("scene", {}), dict) else {}
        target_window = recovery_result.get("target_window", {}) if isinstance(recovery_result.get("target_window", {}), dict) else target_window
        open_payload = {
            "ok": recovery.get("state") == "ready",
            "backend": "window_recovery",
            "reason": _trim_text(recovery.get("reason", "") or "existing_window_focus", limit=80),
            "message": _trim_text(recovery.get("summary", "") or f"Focused the existing window for '{target_display}'.", limit=220),
            "error": "" if recovery.get("state") == "ready" else _trim_text(recovery.get("summary", "") or "Could not focus the matching existing window.", limit=220),
            "data": {
                "target": target_info.get("normalized_target", "") or target_info.get("target", ""),
                "window_id": str(target_window.get("window_id", "")).strip(),
                "process": process_context,
            },
        }
    elif strategy_family == "executable_launch":
        open_payload = launch_unowned_process(
            executable=str(target_info.get("normalized_target", "") or target),
            args=bounded_arguments,
            cwd=cwd,
            env=args.get("env", {}) if isinstance(args.get("env", {}), dict) else {},
        )
    elif strategy_family == "association_open":
        open_payload = open_path_with_association(target=str(target_info.get("normalized_target", "") or target))
    elif strategy_family == "url_browser":
        open_payload = open_url_with_shell(target=str(target_info.get("target", "") or target))
    else:
        open_payload = open_in_explorer(
            target=str(target_info.get("normalized_target", "") or target),
            select_target=bool(target_info.get("is_file", False)),
        )

    payload = open_payload.get("data", {}) if isinstance(open_payload.get("data", {}), dict) else {}
    if not process_context:
        process_context = payload.get("process", {}) if isinstance(payload.get("process", {}), dict) else {}
    launched_pid = _coerce_int(payload.get("pid", 0), 0, minimum=0, maximum=10_000_000)
    backend_reason = _trim_text(open_payload.get("reason", ""), limit=80).lower()
    should_verify_open = bool(open_payload.get("ok", False)) or backend_reason in {
        "association_opened",
        "existing_window_focus",
        "explorer_opened",
        "process_started",
        "url_opened",
    }
    if should_verify_open:
        verification = _sample_open_verification(
            target_info,
            strategy_family=strategy_family,
            before_active_window=active_window,
            before_windows=windows,
            launched_pid=launched_pid,
            sample_count=verification_samples,
            interval_ms=verification_interval_ms,
        )
    else:
        verification_note = (
            "The target does not exist, so Windows never attempted the open request."
            if backend_reason == "target_missing"
            else _trim_text(open_payload.get("message", "") or open_payload.get("error", ""), limit=220)
            or "The open request did not reach a real Windows launch or association path."
        )
        verification = {
            "status": "not_attempted_missing_target" if backend_reason == "target_missing" else "launcher_failed",
            "confidence": "high",
            "note": verification_note,
            "matched_window": False,
            "matched_existing_window": False,
            "matched_active_window": False,
            "likely_opened_behind": False,
            "process_detected": False,
            "active_window_changed": False,
            "matched_window_title": "",
            "matched_window_id": "",
            "matched_process_name": "",
            "match_score": 0,
            "strategy_family": _trim_text(strategy_family, limit=60),
            "samples": [],
        }
    if not target_window:
        target_window = {
            "window_id": str(verification.get("matched_window_id", "")).strip(),
            "title": str(verification.get("matched_window_title", "")).strip(),
            "process_name": str(verification.get("matched_process_name", "")).strip(),
            "is_active": bool(verification.get("matched_active_window", False)),
        }
    if not process_context and verification.get("matched_process_name"):
        process_context = {
            "process_name": str(verification.get("matched_process_name", "")).strip(),
            "running": bool(verification.get("process_detected", False)),
        }

    observation_after = _register_observation(
        active_window=_active_window_info(),
        windows=_enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT),
    )
    summary = _trim_text(open_payload.get("message", ""), limit=220) or _open_target_summary(target_info, strategy_family, verification)
    error = str(open_payload.get("error", "")).strip()
    if not open_payload.get("ok", False) and not error:
        error = str(open_payload.get("message", "")).strip() or summary

    result = _desktop_result(
        ok=bool(open_payload.get("ok", False)),
        action="desktop_open_target",
        summary=summary,
        desktop_state=observation_after,
        error=error,
        approval_status="approved",
        workflow_resumed=_coerce_bool(args.get("resume_from_checkpoint", False), False),
        desktop_evidence_ref=evidence_ref,
        target_window=target_window,
        recovery=recovery,
        recovery_attempts=recovery_attempts,
        window_readiness=window_readiness,
        visual_stability=visual_stability,
        process_context=process_context,
        scene=scene,
    )
    result["open_target"] = target_info
    result["open_strategy"] = {
        **strategy,
        "strategy_family": strategy_family,
        "existing_window_match": existing_window_match,
        "existing_window": existing_window,
        "requested_method": requested_method or request_preferences.get("preferred_method", ""),
    }
    result["open_verification"] = verification
    result["open_result"] = {
        "backend": _trim_text(open_payload.get("backend", ""), limit=40),
        "reason": _trim_text(open_payload.get("reason", ""), limit=80),
        "message": _trim_text(open_payload.get("message", ""), limit=220),
        "data": payload,
    }
    return result


DESKTOP_LIST_WINDOWS_TOOL = {
    "name": "desktop_list_windows",
    "description": (
        "List visible top-level Windows desktop windows in a bounded way, including compact titles, ids, "
        "and which window is currently active. This is read-only desktop inspection."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "additionalProperties": False,
    },
    "func": desktop_list_windows,
}


DESKTOP_GET_ACTIVE_WINDOW_TOOL = {
    "name": "desktop_get_active_window",
    "description": (
        "Return the currently active desktop window in a bounded, read-only way, including its title, id, "
        "process name, and bounds."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "additionalProperties": False,
    },
    "func": desktop_get_active_window,
}


DESKTOP_FOCUS_WINDOW_TOOL = {
    "name": "desktop_focus_window",
    "description": (
        "Bring a specific desktop window to the foreground by exact or partial title match, "
        "or by window_id. This bounded tool can restore or show the window first when needed, "
        "then verify whether Windows actually confirmed foreground focus."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "match": {"type": "string"},
            "window_id": {"type": "string"},
            "exact": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 4, "maximum": 30},
        },
        "additionalProperties": False,
    },
    "func": desktop_focus_window,
}


DESKTOP_INSPECT_WINDOW_STATE_TOOL = {
    "name": "desktop_inspect_window_state",
    "description": (
        "Inspect the current state of a target desktop window in a bounded, read-only way. "
        "Use it to diagnose cases like minimized, hidden, tray/background, wrong foreground window, "
        "loading/not-ready state, or visually unstable UI before taking any desktop action."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "match": {"type": "string"},
            "window_id": {"type": "string"},
            "expected_window_title": {"type": "string"},
            "expected_window_id": {"type": "string"},
            "exact": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 4, "maximum": 30},
            "ui_limit": {"type": "integer", "minimum": 1, "maximum": 16},
            "check_visual_stability": {"type": "boolean"},
            "stability_samples": {"type": "integer", "minimum": 2, "maximum": 4},
            "stability_interval_ms": {"type": "integer", "minimum": 40, "maximum": 400},
        },
        "additionalProperties": False,
    },
    "func": desktop_inspect_window_state,
}


DESKTOP_RECOVER_WINDOW_TOOL = {
    "name": "desktop_recover_window",
    "description": (
        "Attempt one bounded recovery path for a target desktop window. "
        "This tool can inspect, restore, show, refocus, or briefly wait for readiness, "
        "then report normalized recovery reasons and outcomes without clicking or typing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "match": {"type": "string"},
            "window_id": {"type": "string"},
            "expected_window_title": {"type": "string"},
            "expected_window_id": {"type": "string"},
            "exact": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 4, "maximum": 30},
            "ui_limit": {"type": "integer", "minimum": 1, "maximum": 16},
            "max_attempts": {"type": "integer", "minimum": 0, "maximum": 4},
            "wait_seconds": {"type": "number", "minimum": 0.2, "maximum": 3.0},
            "poll_interval_seconds": {"type": "number", "minimum": 0.08, "maximum": 0.4},
            "stability_samples": {"type": "integer", "minimum": 2, "maximum": 4},
            "stability_interval_ms": {"type": "integer", "minimum": 40, "maximum": 400},
        },
        "additionalProperties": False,
    },
    "func": desktop_recover_window,
}


DESKTOP_WAIT_FOR_WINDOW_READY_TOOL = {
    "name": "desktop_wait_for_window_ready",
    "description": (
        "Wait briefly and inspect whether a target desktop window becomes ready and visually stable "
        "enough for bounded work. This is read-only and intended for loading or animated desktop states."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "match": {"type": "string"},
            "window_id": {"type": "string"},
            "expected_window_title": {"type": "string"},
            "expected_window_id": {"type": "string"},
            "exact": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 4, "maximum": 30},
            "ui_limit": {"type": "integer", "minimum": 1, "maximum": 16},
            "wait_seconds": {"type": "number", "minimum": 0.2, "maximum": 3.0},
            "poll_interval_seconds": {"type": "number", "minimum": 0.08, "maximum": 0.4},
            "stability_samples": {"type": "integer", "minimum": 2, "maximum": 4},
            "stability_interval_ms": {"type": "integer", "minimum": 40, "maximum": 400},
        },
        "additionalProperties": False,
    },
    "func": desktop_wait_for_window_ready,
}


DESKTOP_CAPTURE_SCREENSHOT_TOOL = {
    "name": "desktop_capture_screenshot",
    "description": (
        "Capture a bounded screenshot of the Windows primary display, the active window, or the full virtual desktop "
        "and return the saved file path plus compact desktop state metadata. Reliability-first captures prefer the "
        "primary display and can attach a derived active-window crop."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "scope": {"type": "string", "enum": ["primary_monitor", "active_window", "desktop"]},
            "name": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "additionalProperties": False,
    },
    "func": desktop_capture_screenshot,
}


DESKTOP_MOVE_MOUSE_TOOL = {
    "name": "desktop_move_mouse",
    "description": (
        "Move the mouse cursor to one bounded absolute point, one point relative to the active target window, "
        "or one point relative to the latest screenshot-backed capture. Requires explicit approval_status=approved, "
        "a fresh observation_token, and exact visible coordinates."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "relative_x": {"type": "integer"},
            "relative_y": {"type": "integer"},
            "capture_x": {"type": "integer"},
            "capture_y": {"type": "integer"},
            "coordinate_mode": {"type": "string", "enum": ["absolute", "window_relative", "capture_relative"]},
            "title": {"type": "string"},
            "match": {"type": "string"},
            "window_id": {"type": "string"},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
            "max_observation_age_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
        },
        "additionalProperties": False,
    },
    "func": desktop_move_mouse,
}


DESKTOP_HOVER_POINT_TOOL = {
    "name": "desktop_hover_point",
    "description": (
        "Move the mouse to one bounded point and hover briefly over it inside the active target window. "
        "Requires explicit approval_status=approved and a fresh observation_token."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "relative_x": {"type": "integer"},
            "relative_y": {"type": "integer"},
            "capture_x": {"type": "integer"},
            "capture_y": {"type": "integer"},
            "coordinate_mode": {"type": "string", "enum": ["absolute", "window_relative", "capture_relative"]},
            "title": {"type": "string"},
            "match": {"type": "string"},
            "window_id": {"type": "string"},
            "hover_ms": {"type": "integer", "minimum": 120, "maximum": DESKTOP_MAX_HOVER_MS},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
            "max_observation_age_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
        },
        "additionalProperties": False,
    },
    "func": desktop_hover_point,
}


DESKTOP_CLICK_MOUSE_TOOL = {
    "name": "desktop_click_mouse",
    "description": (
        "Perform one bounded mouse click at an exact visible point in the active target window. "
        "Supports left click, right click, and a bounded double click with explicit approval."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "relative_x": {"type": "integer"},
            "relative_y": {"type": "integer"},
            "capture_x": {"type": "integer"},
            "capture_y": {"type": "integer"},
            "coordinate_mode": {"type": "string", "enum": ["absolute", "window_relative", "capture_relative"]},
            "title": {"type": "string"},
            "match": {"type": "string"},
            "window_id": {"type": "string"},
            "button": {"type": "string", "enum": ["left", "right"]},
            "click_count": {"type": "integer", "minimum": 1, "maximum": 2},
            "double_click": {"type": "boolean"},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
            "max_observation_age_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
        },
        "additionalProperties": False,
    },
    "func": desktop_click_mouse,
}


DESKTOP_CLICK_POINT_TOOL = {
    "name": "desktop_click_point",
    "description": (
        "Click one exact screen coordinate inside the currently active desktop window. "
        "Requires explicit approval_status=approved, a fresh observation_token from recent desktop inspection, "
        "and exact visible coordinates. If the window state changed, inspect or recover the window first. "
        "This tool performs one bounded click only."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "relative_x": {"type": "integer"},
            "relative_y": {"type": "integer"},
            "capture_x": {"type": "integer"},
            "capture_y": {"type": "integer"},
            "coordinate_mode": {"type": "string", "enum": ["absolute", "window_relative", "capture_relative"]},
            "title": {"type": "string"},
            "match": {"type": "string"},
            "window_id": {"type": "string"},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
            "max_observation_age_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
        },
        "additionalProperties": False,
    },
    "func": desktop_click_point,
}


DESKTOP_SCROLL_TOOL = {
    "name": "desktop_scroll",
    "description": (
        "Scroll one bounded amount up or down in the active target window. "
        "Requires explicit approval_status=approved, a fresh observation_token, and stays limited to one bounded scroll step."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "direction": {"type": "string", "enum": ["up", "down"]},
            "scroll_units": {"type": "integer", "minimum": 1, "maximum": DESKTOP_MAX_SCROLL_UNITS},
            "lines": {"type": "integer", "minimum": 1, "maximum": DESKTOP_MAX_SCROLL_UNITS},
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "relative_x": {"type": "integer"},
            "relative_y": {"type": "integer"},
            "capture_x": {"type": "integer"},
            "capture_y": {"type": "integer"},
            "coordinate_mode": {"type": "string", "enum": ["absolute", "window_relative", "capture_relative"]},
            "title": {"type": "string"},
            "match": {"type": "string"},
            "window_id": {"type": "string"},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
            "max_observation_age_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
        },
        "additionalProperties": False,
    },
    "func": desktop_scroll,
}


DESKTOP_PRESS_KEY_TOOL = {
    "name": "desktop_press_key",
    "description": (
        "Press one bounded safe keyboard key or Ctrl/Shift shortcut in the currently active desktop window. "
        "Requires explicit approval_status=approved, a fresh observation_token from recent desktop inspection, "
        "and stays limited to safe navigation keys and a small allowlist of Ctrl-based shortcuts. "
        "This tool does not send system keys, the Windows key, or unrestricted hotkeys."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "modifiers": {
                "type": "array",
                "items": {"type": "string", "enum": ["ctrl", "control", "shift"]},
                "minItems": 0,
                "maxItems": 2,
            },
            "repeat": {"type": "integer", "minimum": 1, "maximum": DESKTOP_MAX_KEY_REPEAT},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
            "max_observation_age_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
        },
        "required": ["key"],
        "additionalProperties": False,
    },
    "func": desktop_press_key,
}


DESKTOP_PRESS_KEY_SEQUENCE_TOOL = {
    "name": "desktop_press_key_sequence",
    "description": (
        "Press a short bounded sequence of safe desktop key combinations in the active window. "
        "Requires explicit approval_status=approved, a fresh observation_token, and stays inside the safe key allowlist."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sequence": {
                "type": "array",
                "minItems": 1,
                "maxItems": DESKTOP_MAX_KEY_SEQUENCE_STEPS,
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "modifiers": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["ctrl", "control", "shift"]},
                            "minItems": 0,
                            "maxItems": 2,
                        },
                        "repeat": {"type": "integer", "minimum": 1, "maximum": DESKTOP_MAX_KEY_REPEAT},
                    },
                    "required": ["key"],
                    "additionalProperties": False,
                },
            },
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
            "max_observation_age_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
        },
        "required": ["sequence"],
        "additionalProperties": False,
    },
    "func": desktop_press_key_sequence,
}


DESKTOP_TYPE_TEXT_TOOL = {
    "name": "desktop_type_text",
    "description": (
        "Type bounded plain text into the currently focused field in the active desktop window. "
        "Requires explicit approval_status=approved, a fresh observation_token from recent desktop inspection, "
        "and a non-sensitive field_label. If the target window is not ready, inspect or recover it first. "
        "This tool does not send Enter or submit forms."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "value": {"type": "string"},
            "field_label": {"type": "string"},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
            "max_text_length": {"type": "integer", "minimum": 1, "maximum": DESKTOP_DEFAULT_TYPE_MAX_CHARS},
            "max_observation_age_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
        },
        "required": ["value", "field_label"],
        "additionalProperties": False,
    },
    "func": desktop_type_text,
}


DESKTOP_LIST_PROCESSES_TOOL = {
    "name": "desktop_list_processes",
    "description": (
        "List a bounded set of local desktop processes with compact diagnostics such as pid, status, background-candidate state, "
        "and whether the process is one of this operator's owned processes."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": DESKTOP_MAX_PROCESS_LIMIT},
            "include_background": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    "func": desktop_list_processes,
}


DESKTOP_INSPECT_PROCESS_TOOL = {
    "name": "desktop_inspect_process",
    "description": (
        "Inspect one bounded local process in more detail, including command line excerpt, working directory, "
        "child processes, and owned-process metadata when available."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pid": {"type": "integer", "minimum": 0, "maximum": 10000000},
            "process_name": {"type": "string"},
            "owned_label": {"type": "string"},
            "child_limit": {"type": "integer", "minimum": 0, "maximum": 8},
        },
        "additionalProperties": False,
    },
    "func": desktop_inspect_process,
}


DESKTOP_START_PROCESS_TOOL = {
    "name": "desktop_start_process",
    "description": (
        "Start one bounded owned local process with explicit approval. "
        "This is intended for safe test helpers or known local apps, not broad process spawning."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "executable": {"type": "string"},
            "arguments": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
            "cwd": {"type": "string"},
            "env": {"type": "object", "additionalProperties": {"type": "string"}},
            "owned_label": {"type": "string"},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
        },
        "required": ["executable"],
        "additionalProperties": False,
    },
    "func": desktop_start_process,
}


DESKTOP_STOP_PROCESS_TOOL = {
    "name": "desktop_stop_process",
    "description": (
        "Stop one bounded owned local process with explicit approval. "
        "This tool only stops processes that were started as owned bounded processes."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pid": {"type": "integer", "minimum": 0, "maximum": 10000000},
            "owned_label": {"type": "string"},
            "wait_seconds": {"type": "integer", "minimum": 1, "maximum": 5},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
        },
        "additionalProperties": False,
    },
    "func": desktop_stop_process,
}


DESKTOP_RUN_COMMAND_TOOL = {
    "name": "desktop_run_command",
    "description": (
        "Run one bounded local command with explicit approval, a capped timeout, and captured stdout/stderr excerpts. "
        "This is a controlled command surface, not a general autonomous shell."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "cwd": {"type": "string"},
            "env": {"type": "object", "additionalProperties": {"type": "string"}},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": DESKTOP_MAX_COMMAND_TIMEOUT_SECONDS},
            "shell_kind": {"type": "string", "enum": ["powershell", "cmd"]},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
    "func": desktop_run_command,
}


DESKTOP_OPEN_TARGET_TOOL = {
    "name": "desktop_open_target",
    "description": (
        "Open one Windows target in a bounded, strategy-aware way. "
        "Use this for files, folders, URLs, documents, images, and executable programs so the operator can choose "
        "the right Windows open semantics, switch strategy after failures, and verify whether the target really surfaced."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "target": {"type": "string"},
            "target_type": {
                "type": "string",
                "enum": [
                    "document_file",
                    "executable_program",
                    "folder_directory",
                    "image_media_file",
                    "text_code_file",
                    "unknown_ambiguous_path",
                    "url_web_resource",
                ],
            },
            "preferred_method": {
                "type": "string",
                "enum": [
                    "association_open",
                    "bounded_fallback",
                    "executable_launch",
                    "explorer_assisted_ui",
                    "focus_existing_window",
                    "url_browser",
                ],
            },
            "requested_method": {
                "type": "string",
                "enum": [
                    "association_open",
                    "bounded_fallback",
                    "executable_launch",
                    "explorer_assisted_ui",
                    "focus_existing_window",
                    "url_browser",
                ],
            },
            "force_strategy_switch": {"type": "boolean"},
            "avoid_strategy_families": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "association_open",
                        "bounded_fallback",
                        "executable_launch",
                        "explorer_assisted_ui",
                        "focus_existing_window",
                        "url_browser",
                    ],
                },
                "maxItems": 4,
            },
            "arguments": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
            "cwd": {"type": "string"},
            "env": {"type": "object", "additionalProperties": {"type": "string"}},
            "verification_samples": {"type": "integer", "minimum": 2, "maximum": 4},
            "verification_interval_ms": {"type": "integer", "minimum": 80, "maximum": 320},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
        },
        "required": ["target"],
        "additionalProperties": False,
    },
    "func": desktop_open_target,
}
