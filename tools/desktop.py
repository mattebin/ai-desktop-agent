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
from core.desktop_matching import select_window_candidate
from core.desktop_recovery import classify_window_recovery_state, select_window_recovery_strategy
from core.desktop_scene import interpret_desktop_scene
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
    run_bounded_command,
    start_owned_process,
    stop_owned_process,
)


DESKTOP_DEFAULT_WINDOW_LIMIT = 12
DESKTOP_DEFAULT_MAX_OBSERVATION_AGE_SECONDS = 45
DESKTOP_DEFAULT_TYPE_MAX_CHARS = 160
DESKTOP_DEFAULT_CAPTURE_MAX_WIDTH = 2200
DESKTOP_DEFAULT_CAPTURE_MAX_HEIGHT = 1600
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


user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
try:
    dwmapi = ctypes.windll.dwmapi
except Exception:
    dwmapi = None


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


def _window_info(hwnd: int) -> Dict[str, Any]:
    handle = int(hwnd or 0)
    if handle <= 0 or not user32.IsWindow(ctypes.c_void_p(handle)):
        return {}

    title = _get_window_text(handle)
    rect = _window_rect(handle)
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

    @EnumWindowsProc
    def callback(hwnd, _lparam):
        if len(windows) >= limit:
            return False
        if _window_is_listable(int(hwnd), include_minimized=include_minimized, include_hidden=include_hidden):
            info = _window_info(int(hwnd))
            if info:
                windows.append(info)
        return True

    user32.EnumWindows(callback, 0)
    windows.sort(key=lambda item: (not item.get("is_active", False), item.get("title", "").lower()))
    return windows[:limit]


def _active_window_info_native() -> Dict[str, Any]:
    hwnd = int(user32.GetForegroundWindow() or 0)
    return _window_info(hwnd)


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
            filtered.append(item)

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
                merged.append(item)
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
    result = _get_window_backend().get_active_window()
    data = result.get("data", {}) if isinstance(result, dict) else {}
    active_window = data.get("active_window", {}) if isinstance(data, dict) else {}
    return active_window if isinstance(active_window, dict) else {}


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


def _point_in_rect(x: int, y: int, rect: Dict[str, int]) -> bool:
    return (
        x >= int(rect.get("x", 0))
        and y >= int(rect.get("y", 0))
        and x < int(rect.get("x", 0)) + int(rect.get("width", 0))
        and y < int(rect.get("y", 0)) + int(rect.get("height", 0))
    )


def _register_observation(*, active_window: Dict[str, Any], windows: List[Dict[str, Any]], screenshot_path: str = "", screenshot_scope: str = "") -> Dict[str, Any]:
    global _OBSERVATION_COUNTER

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
        "recovery": recovery_view,
        "recovery_attempts": [dict(item) for item in list(recovery_attempts or [])[:6] if isinstance(item, dict)],
    }


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
    allow_default_center: bool = False,
) -> Tuple[Dict[str, int], Dict[str, Any], str]:
    coordinate_mode = str(args.get("coordinate_mode", "")).strip().lower()
    if not coordinate_mode:
        coordinate_mode = "window_relative" if any(key in args for key in ("relative_x", "relative_y")) else "absolute"
    target_window, target_error = _resolve_pointer_target_window(args, active_window)
    if target_error and coordinate_mode == "window_relative":
        return {}, target_window, target_error

    point_x_raw = args.get("x", args.get("relative_x", None))
    point_y_raw = args.get("y", args.get("relative_y", None))
    if point_x_raw in {None, ""} or point_y_raw in {None, ""}:
        if allow_default_center and target_window.get("window_id"):
            center = _window_center_point(target_window)
            return center, target_window, ""
        return {}, target_window, "Provide bounded pointer coordinates before using this desktop pointer tool."

    point_x = _coerce_int(point_x_raw, 0, minimum=-20_000, maximum=20_000)
    point_y = _coerce_int(point_y_raw, 0, minimum=-20_000, maximum=20_000)
    if coordinate_mode == "window_relative":
        rect = target_window.get("rect", {}) if isinstance(target_window.get("rect", {}), dict) else {}
        width = int(rect.get("width", 0) or 0)
        height = int(rect.get("height", 0) or 0)
        if width <= 0 or height <= 0:
            return {}, target_window, "The target window does not expose usable visible bounds for a relative pointer action."
        absolute_point = {
            "x": int(rect.get("x", 0) or 0) + point_x,
            "y": int(rect.get("y", 0) or 0) + point_y,
        }
    else:
        absolute_point = {"x": point_x, "y": point_y}

    screen_rect = _virtual_screen_rect()
    if not _point_in_rect(int(absolute_point.get("x", 0)), int(absolute_point.get("y", 0)), screen_rect):
        return {}, target_window, f"The point ({absolute_point.get('x', 0)}, {absolute_point.get('y', 0)}) is outside the visible desktop."
    if target_window.get("window_id"):
        target_rect = target_window.get("rect", {}) if isinstance(target_window.get("rect", {}), dict) else {}
        if not _point_in_rect(int(absolute_point.get("x", 0)), int(absolute_point.get("y", 0)), target_rect):
            return {}, target_window, (
                f"The point ({absolute_point.get('x', 0)}, {absolute_point.get('y', 0)}) is outside "
                f"the target window '{target_window.get('title', 'window')}'."
            )
    return absolute_point, target_window, target_error


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


def capture_desktop_evidence_frame(
    *,
    scope: str = "active_window",
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
    requested_scope = str(scope or "active_window").strip().lower()
    active_window = _active_window_info()
    windows = _enum_windows(
        limit=_coerce_int(limit, DESKTOP_DEFAULT_WINDOW_LIMIT, minimum=1, maximum=20)
    )
    evidence_id = get_desktop_evidence_store().next_evidence_id()
    args: Dict[str, Any] = {}
    if capture_name:
        args["name"] = capture_name

    if requested_scope == "desktop":
        bounds = _virtual_screen_rect()
        capture_label = "desktop"
        target_window: Dict[str, Any] = {}
        capture_scope = "desktop"
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
    capture_signature = _file_sha1(captured_path) if ok else ""
    observation = _register_observation(
        active_window=active_window,
        windows=windows,
        screenshot_path=captured_path if ok else "",
        screenshot_scope=capture_scope,
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
        },
        "evidence_bundle": evidence_bundle,
        "evidence_ref": evidence_ref,
        "screenshot_path": captured_path if ok else "",
        "screenshot_scope": capture_scope,
        "capture_signature": capture_signature,
        "target_window": target_window,
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
    recovered = _execute_window_recovery(args, action_name="desktop_focus_window")
    recovery = recovered.get("recovery", {}) if isinstance(recovered.get("recovery", {}), dict) else {}
    ok = recovery.get("state") == "ready"
    target_window = recovered.get("target_window", {}) if isinstance(recovered.get("target_window", {}), dict) else {}
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
    )


def desktop_capture_screenshot(args: Dict[str, Any]) -> Dict[str, Any]:
    scope = str(args.get("scope", "active_window")).strip().lower()
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
    point, target_window, point_error = _resolve_pointer_point(
        args,
        active_window=active_window,
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
        "target_window": target_window if isinstance(target_window, dict) and target_window.get("window_id") else active_window,
    }


def desktop_move_mouse(args: Dict[str, Any]) -> Dict[str, Any]:
    context = _prepare_pointer_action_context(args, action="desktop_move_mouse")
    if not context.get("ok", False):
        return context.get("result", {})

    point = context["point"]
    active_window = context["active_window"]
    windows = context["windows"]
    evidence_ref = context["evidence_ref"]
    target_window = context["target_window"]
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
            checkpoint_resume_args={
                "x": int(point.get("x", 0) or 0),
                "y": int(point.get("y", 0) or 0),
                "coordinate_mode": str(args.get("coordinate_mode", "absolute")).strip().lower() or "absolute",
                "observation_token": context["token"],
                "expected_window_id": active_window.get("window_id", ""),
                "expected_window_title": active_window.get("title", ""),
                "evidence_id": evidence_ref.get("evidence_id", ""),
            },
            point=point,
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
            checkpoint_resume_args={
                "x": int(point.get("x", 0) or 0),
                "y": int(point.get("y", 0) or 0),
                "hover_ms": hover_ms,
                "coordinate_mode": str(args.get("coordinate_mode", "absolute")).strip().lower() or "absolute",
                "observation_token": context["token"],
                "expected_window_id": active_window.get("window_id", ""),
                "expected_window_title": active_window.get("title", ""),
                "evidence_id": evidence_ref.get("evidence_id", ""),
            },
            point=point,
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
    context = _prepare_pointer_action_context(args, action="desktop_click_mouse")
    if not context.get("ok", False):
        return context.get("result", {})

    point = context["point"]
    active_window = context["active_window"]
    windows = context["windows"]
    evidence_ref = context["evidence_ref"]
    target_window = context["target_window"]
    button = _normalize_mouse_button(args.get("button", "left"))
    click_count = 2 if _coerce_bool(args.get("double_click", False), False) else _coerce_int(args.get("click_count", 1), 1, minimum=1, maximum=2)
    click_label = f"{button} {'double-click' if click_count == 2 else 'click'}"
    checkpoint_target = f"{target_window.get('title', active_window.get('title', 'active window'))} @ ({point.get('x')}, {point.get('y')}) :: {click_label}"
    checkpoint_reason = str(args.get("checkpoint_reason", "")).strip() or (
        f"{click_label.title()}ing at ({point.get('x')}, {point.get('y')}) in '{target_window.get('title', active_window.get('title', 'the active window'))}' requires explicit approval in this bounded control pass."
    )
    if not _approval_granted(args):
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
            checkpoint_resume_args={
                "x": int(point.get("x", 0) or 0),
                "y": int(point.get("y", 0) or 0),
                "button": button,
                "click_count": click_count,
                "coordinate_mode": str(args.get("coordinate_mode", "absolute")).strip().lower() or "absolute",
                "observation_token": context["token"],
                "expected_window_id": active_window.get("window_id", ""),
                "expected_window_title": active_window.get("title", ""),
                "evidence_id": evidence_ref.get("evidence_id", ""),
            },
            point=point,
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
    mouse_action = {
        "action": "click",
        "button": button,
        "click_count": click_count,
        "point": point,
        "window_title": str(target_window.get("title", "") or active_window.get("title", "")),
        "coordinate_mode": str(args.get("coordinate_mode", "absolute")).strip().lower() or "absolute",
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
        workflow_resumed=_coerce_bool(args.get("resume_from_checkpoint", False), False),
        point=point,
        mouse_action=mouse_action,
        desktop_evidence_ref=evidence_ref,
        target_window=target_window,
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
    context = _prepare_pointer_action_context(args, action="desktop_scroll", allow_default_center=True)
    if not context.get("ok", False):
        return context.get("result", {})

    point = context["point"]
    active_window = context["active_window"]
    windows = context["windows"]
    evidence_ref = context["evidence_ref"]
    target_window = context["target_window"]
    direction = str(args.get("direction", "down")).strip().lower()
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
    scroll_units = _coerce_int(args.get("scroll_units", args.get("lines", DESKTOP_DEFAULT_SCROLL_UNITS)), DESKTOP_DEFAULT_SCROLL_UNITS, minimum=1, maximum=DESKTOP_MAX_SCROLL_UNITS)
    checkpoint_target = f"{target_window.get('title', active_window.get('title', 'active window'))} :: scroll {direction} x{scroll_units}"
    checkpoint_reason = str(args.get("checkpoint_reason", "")).strip() or (
        f"Scrolling {direction} by {scroll_units} unit(s) in '{target_window.get('title', active_window.get('title', 'the active window'))}' requires explicit approval in this bounded control pass."
    )
    if not _approval_granted(args):
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
            checkpoint_resume_args={
                "x": int(point.get("x", 0) or 0),
                "y": int(point.get("y", 0) or 0),
                "direction": direction,
                "scroll_units": scroll_units,
                "coordinate_mode": str(args.get("coordinate_mode", "absolute")).strip().lower() or "absolute",
                "observation_token": context["token"],
                "expected_window_id": active_window.get("window_id", ""),
                "expected_window_title": active_window.get("title", ""),
                "evidence_id": evidence_ref.get("evidence_id", ""),
            },
            point=point,
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
    mouse_action = {
        "action": "scroll",
        "point": point,
        "window_title": str(target_window.get("title", "") or active_window.get("title", "")),
        "coordinate_mode": str(args.get("coordinate_mode", "absolute")).strip().lower() or "absolute",
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
        workflow_resumed=_coerce_bool(args.get("resume_from_checkpoint", False), False),
        point=point,
        mouse_action=mouse_action,
        desktop_evidence_ref=evidence_ref,
        target_window=target_window,
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

    token, observation, observation_error = _validate_fresh_observation(args)
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

    checkpoint_reason = str(args.get("checkpoint_reason", "")).strip() or (
        f"Typing into '{field_label}' in '{active_window.get('title', 'the active window')}' requires explicit approval in this bounded control pass."
    )
    checkpoint_target = field_label
    if not _approval_granted(args):
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
        )
    return _desktop_result(
        ok=True,
        action="desktop_type_text",
        summary=f"Typed into '{field_label}' in '{active_after.get('title', active_window.get('title', 'the active window'))}'.",
        desktop_state=observation_after,
        approval_status="approved",
        workflow_resumed=_coerce_bool(args.get("resume_from_checkpoint", False), False),
        typed_text_preview=_trim_text(value, limit=60),
        desktop_evidence_ref=evidence_ref,
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

    token, observation, observation_error = _validate_fresh_observation(args)
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

    checkpoint_reason = str(args.get("checkpoint_reason", "")).strip() or (
        f"Pressing {key_preview} in '{active_window.get('title', 'the active window')}' requires explicit approval in this bounded control pass."
    )
    checkpoint_target = active_window.get("title", "") or "active window"
    if not _approval_granted(args):
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
    if not ok:
        return _desktop_result(
            ok=False,
            action="desktop_press_key",
            summary=f"Could not press {key_preview} in '{active_after.get('title', active_window.get('title', 'the active window'))}'.",
            desktop_state=observation_after,
            error=f"Could not press {key_preview} in '{active_window.get('title', 'the active window')}'.",
            approval_status="approved",
            workflow_resumed=_coerce_bool(args.get("resume_from_checkpoint", False), False),
            key_sequence_preview=key_preview,
            desktop_evidence_ref=evidence_ref,
        )
    return _desktop_result(
        ok=True,
        action="desktop_press_key",
        summary=f"Pressed {key_preview} in '{active_after.get('title', active_window.get('title', 'the active window'))}'.",
        desktop_state=observation_after,
        approval_status="approved",
        workflow_resumed=_coerce_bool(args.get("resume_from_checkpoint", False), False),
        key_sequence_preview=key_preview,
        desktop_evidence_ref=evidence_ref,
    )


def _current_desktop_context(*, limit: int = DESKTOP_DEFAULT_WINDOW_LIMIT) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]]:
    active_window = _active_window_info()
    windows = _enum_windows(limit=limit)
    observation = _register_observation(active_window=active_window, windows=windows)
    return active_window, windows, observation


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

    token, observation, observation_error = _validate_fresh_observation(args)
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

    checkpoint_reason = str(args.get("checkpoint_reason", "")).strip() or (
        f"Pressing the bounded key sequence {key_preview} in '{active_window.get('title', 'the active window')}' requires explicit approval in this control pass."
    )
    checkpoint_target = active_window.get("title", "") or "active window"
    if not _approval_granted(args):
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
        workflow_resumed=_coerce_bool(args.get("resume_from_checkpoint", False), False),
        key_sequence_preview=key_preview,
        desktop_evidence_ref=evidence_ref,
        target_window=active_after or active_window,
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
    active_window, windows, observation = _current_desktop_context()
    token = str(args.get("observation_token", "")).strip()
    evidence_ref = _latest_evidence_ref_for_observation(token) if token else {}
    executable = str(args.get("executable", "")).strip()
    arguments = args.get("arguments", [])
    if not isinstance(arguments, list):
        arguments = []
    bounded_arguments = [_trim_text(item, limit=180) for item in arguments[:8] if _trim_text(item, limit=180)]
    owned_label = str(args.get("owned_label", "")).strip() or Path(executable).stem
    checkpoint_target = owned_label or executable or "bounded desktop process"
    checkpoint_reason = str(args.get("checkpoint_reason", "")).strip() or (
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
    if not _approval_granted(args):
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
                "cwd": str(args.get("cwd", "")).strip(),
                "owned_label": owned_label,
                "shell_kind": str(args.get("shell_kind", "")).strip(),
                "observation_token": token,
                "evidence_id": evidence_ref.get("evidence_id", ""),
            },
            desktop_evidence_ref=evidence_ref,
        )

    process_result = start_owned_process(
        executable=executable,
        args=bounded_arguments,
        cwd=str(args.get("cwd", "")).strip(),
        env=args.get("env", {}) if isinstance(args.get("env", {}), dict) else {},
        owned_label=owned_label,
    )
    payload = process_result.get("data", {}) if isinstance(process_result.get("data", {}), dict) else {}
    process_context = payload.get("process", {}) if isinstance(payload.get("process", {}), dict) else {}
    observation_after = _register_observation(active_window=_active_window_info(), windows=_enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT))
    summary = str(process_result.get("message", "")).strip() or "Started the requested bounded process."
    return _desktop_result(
        ok=bool(process_result.get("ok", False)),
        action="desktop_start_process",
        summary=summary,
        desktop_state=observation_after,
        error=str(process_result.get("error", "")).strip(),
        approval_status="approved",
        workflow_resumed=_coerce_bool(args.get("resume_from_checkpoint", False), False),
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
    active_window, windows, observation = _current_desktop_context()
    token = str(args.get("observation_token", "")).strip()
    evidence_ref = _latest_evidence_ref_for_observation(token) if token else {}
    command = str(args.get("command", "")).strip()
    shell_kind = str(args.get("shell_kind", "powershell")).strip().lower() or "powershell"
    timeout_seconds = _coerce_int(
        args.get("timeout_seconds", DESKTOP_DEFAULT_COMMAND_TIMEOUT_SECONDS),
        DESKTOP_DEFAULT_COMMAND_TIMEOUT_SECONDS,
        minimum=1,
        maximum=DESKTOP_MAX_COMMAND_TIMEOUT_SECONDS,
    )
    checkpoint_reason = str(args.get("checkpoint_reason", "")).strip() or (
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
    if not _approval_granted(args):
        return _pause_desktop_action(
            action="desktop_run_command",
            summary=f"Approval required before running '{checkpoint_target}'.",
            active_window=active_window,
            windows=windows,
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=checkpoint_target,
            checkpoint_resume_args={
                "command": command,
                "cwd": str(args.get("cwd", "")).strip(),
                "shell_kind": shell_kind,
                "timeout_seconds": timeout_seconds,
                "observation_token": token,
                "evidence_id": evidence_ref.get("evidence_id", ""),
            },
            desktop_evidence_ref=evidence_ref,
        )

    command_result = run_bounded_command(
        command=command,
        cwd=str(args.get("cwd", "")).strip(),
        env=args.get("env", {}) if isinstance(args.get("env", {}), dict) else {},
        timeout_seconds=float(timeout_seconds),
        shell_kind=shell_kind,
    )
    payload = command_result.get("data", {}) if isinstance(command_result.get("data", {}), dict) else {}
    observation_after = _register_observation(active_window=_active_window_info(), windows=_enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT))
    summary = str(command_result.get("message", "")).strip() or str(payload.get("summary", "")).strip() or "Ran the bounded local command."
    return _desktop_result(
        ok=bool(command_result.get("ok", False)),
        action="desktop_run_command",
        summary=summary,
        desktop_state=observation_after,
        error=str(command_result.get("error", "")).strip(),
        approval_status="approved",
        workflow_resumed=_coerce_bool(args.get("resume_from_checkpoint", False), False),
        command_result=payload,
        target_window=active_window,
    )


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
        "Capture a bounded screenshot of the active window or full desktop and return the saved file path plus "
        "compact desktop state metadata. This is read-only inspection; it does not interpret pixels."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "scope": {"type": "string", "enum": ["active_window", "desktop"]},
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
        "Move the mouse cursor to one bounded absolute point or one bounded point relative to the active target window. "
        "Requires explicit approval_status=approved, a fresh observation_token, and exact visible coordinates."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "relative_x": {"type": "integer"},
            "relative_y": {"type": "integer"},
            "coordinate_mode": {"type": "string", "enum": ["absolute", "window_relative"]},
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
            "coordinate_mode": {"type": "string", "enum": ["absolute", "window_relative"]},
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
            "coordinate_mode": {"type": "string", "enum": ["absolute", "window_relative"]},
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
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
            "max_observation_age_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
        },
        "required": ["x", "y"],
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
            "coordinate_mode": {"type": "string", "enum": ["absolute", "window_relative"]},
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
