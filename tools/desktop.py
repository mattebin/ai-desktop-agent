from __future__ import annotations

import ctypes
import struct
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from core.desktop_evidence import (
    build_desktop_evidence_bundle,
    collect_display_metadata,
    get_desktop_evidence_store,
)
from core.desktop_recovery import classify_window_recovery_state, select_window_recovery_strategy
from tools.desktop_backends import (
    create_screenshot_backend,
    create_ui_evidence_backend,
    create_window_backend,
    describe_backends,
    probe_visual_stability,
    probe_window_readiness,
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
    "desktop_type_text",
}
DESKTOP_APPROVAL_TOOL_NAMES = {
    "desktop_click_point",
    "desktop_type_text",
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


def _readiness_probe_for_window(window: Dict[str, Any], *, limit: int = 8) -> Dict[str, Any]:
    if not isinstance(window, dict) or not window.get("window_id"):
        return {}
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


def _inspect_window_state_internal(
    args: Dict[str, Any],
    *,
    source_action: str,
    include_ui_evidence: bool = True,
    include_visual_stability: bool = True,
) -> Dict[str, Any]:
    limit = max(8, _coerce_int(args.get("limit", 16), 16, minimum=4, maximum=30))
    target_window, candidates, lookup_error = _find_window(args)
    visible_windows = _enum_windows(limit=min(limit, DESKTOP_DEFAULT_WINDOW_LIMIT))
    active_window = _active_window_info()
    readiness = _readiness_probe_for_window(
        target_window or active_window,
        limit=_coerce_int(args.get("ui_limit", 8), 8, minimum=1, maximum=16),
    )
    visual_stability = (
        _visual_stability_for_window(
            target_window or active_window,
            samples=_coerce_int(args.get("stability_samples", 3), 3, minimum=2, maximum=4),
            interval_ms=_coerce_int(args.get("stability_interval_ms", 120), 120, minimum=40, maximum=400),
        )
        if include_visual_stability
        else {}
    )
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
        readiness=readiness,
        visual_stability=visual_stability,
        expected_window_id=str(args.get("expected_window_id", "")).strip(),
        expected_window_title=str(args.get("expected_window_title", "")).strip(),
        backend=str(get_desktop_backend_status().get("window", {}).get("active", "desktop") or "desktop"),
    )
    recovery_strategy = select_window_recovery_strategy(
        recovery,
        attempt_count=_coerce_int(args.get("attempt_count", 0), 0, minimum=0, maximum=8),
        max_attempts=_coerce_int(args.get("max_attempts", 2), 2, minimum=0, maximum=4),
    )
    recovery_view = dict(recovery)
    recovery_view.update(recovery_strategy)
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
        "recovery": recovery_view,
    }


def _latest_evidence_ref_for_observation(token: str) -> Dict[str, Any]:
    try:
        return get_desktop_evidence_store().find_by_observation_token(token)
    except Exception:
        return {}


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


def _find_window(args: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]], str]:
    window_id = _parse_hwnd(args.get("window_id", ""))
    requested_title = str(args.get("title", "") or args.get("match", "")).strip()
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
                return item, candidates, ""
        info = _window_info(window_id)
        if info:
            return info, candidates, ""
        return {}, candidates, f"Could not find a window with id {_hex_hwnd(window_id)}."

    if not requested_title:
        return {}, candidates, "Provide a window title or window_id."

    if exact:
        direct_match = _find_window_by_exact_title_native(requested_title)
        direct_id = str(direct_match.get("window_id", "")).strip()
        if direct_id and not any(str(item.get("window_id", "")).strip() == direct_id for item in candidates):
            candidates = [direct_match, *candidates]

    normalized_title = requested_title.lower()
    matches = [
        item
        for item in candidates
        if (
            item.get("title", "").lower() == normalized_title
            if exact
            else normalized_title in item.get("title", "").lower()
        )
    ]
    if len(matches) == 1:
        return matches[0], candidates, ""
    if len(matches) > 1:
        matches.sort(
            key=lambda item: (
                not bool(item.get("is_active", False)),
                not bool(item.get("is_visible", False)),
                bool(item.get("is_minimized", False)),
                item.get("title", "").lower(),
            )
        )
        top = matches[0]
        strong_match = bool(top.get("is_active", False)) or bool(top.get("is_visible", False))
        if strong_match and len(matches) <= 3:
            return top, candidates, ""
        labels = ", ".join(item.get("title", "") for item in matches[:4] if item.get("title"))
        return {}, candidates, f"Multiple windows matched '{requested_title}': {labels}"
    return {}, candidates, (
        f"Could not find a surfaced window matching '{requested_title}'. "
        "It may be withdrawn, tray-like, or not exposed as a visible top-level window."
    )


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
    desktop_evidence: Dict[str, Any] | None = None,
    desktop_evidence_ref: Dict[str, Any] | None = None,
    target_window: Dict[str, Any] | None = None,
    recovery: Dict[str, Any] | None = None,
    recovery_attempts: List[Dict[str, Any]] | None = None,
    window_readiness: Dict[str, Any] | None = None,
    visual_stability: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    state = desktop_state if isinstance(desktop_state, dict) else {}
    evidence = desktop_evidence if isinstance(desktop_evidence, dict) else {}
    evidence_ref = desktop_evidence_ref if isinstance(desktop_evidence_ref, dict) else {}
    target = target_window if isinstance(target_window, dict) else {}
    recovery_view = recovery if isinstance(recovery, dict) else {}
    readiness = window_readiness if isinstance(window_readiness, dict) else {}
    stability = visual_stability if isinstance(visual_stability, dict) else {}
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
        "desktop_evidence": evidence,
        "desktop_evidence_ref": evidence_ref,
        "evidence_id": _trim_text(evidence_ref.get("evidence_id", "") or evidence.get("evidence_id", ""), limit=80),
        "evidence_summary": _trim_text(evidence_ref.get("summary", "") or evidence.get("summary", ""), limit=240),
        "target_window": target,
        "window_readiness": readiness,
        "visual_stability": stability,
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
    result = _get_window_backend().focus_window(window_id=_hex_hwnd(hwnd))
    if not isinstance(result, dict):
        return False, "Could not focus the requested window."
    return bool(result.get("ok", False)), str(result.get("error", "") or "")


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

    while time.time() < deadline:
        current_args = dict(args)
        current_args["attempt_count"] = attempt_count
        inspected = _inspect_window_state_internal(
            current_args,
            source_action=action_name,
            include_ui_evidence=True,
            include_visual_stability=True,
        )
        last_view = inspected
        recovery = inspected.get("recovery", {}) if isinstance(inspected.get("recovery", {}), dict) else {}
        if recovery.get("state") == "ready":
            return inspected
        if recovery.get("reason") in {"target_not_found", "tray_or_background_state", "target_mismatch"}:
            return inspected
        time.sleep(interval_seconds)

    return last_view


def _execute_window_recovery(args: Dict[str, Any], *, action_name: str) -> Dict[str, Any]:
    inspected = _inspect_window_state_internal(
        args,
        source_action=action_name,
        include_ui_evidence=True,
        include_visual_stability=True,
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
            include_ui_evidence=True,
            include_visual_stability=True,
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
    )


def desktop_capture_screenshot(args: Dict[str, Any]) -> Dict[str, Any]:
    scope = str(args.get("scope", "active_window")).strip().lower()
    active_window = _active_window_info()
    windows = _enum_windows(limit=_coerce_int(args.get("limit", DESKTOP_DEFAULT_WINDOW_LIMIT), DESKTOP_DEFAULT_WINDOW_LIMIT, minimum=1, maximum=20))
    evidence_id = get_desktop_evidence_store().next_evidence_id()

    if scope == "desktop":
        bounds = _virtual_screen_rect()
        capture_label = "desktop"
    else:
        scope = "active_window"
        if not active_window:
            observation = _register_observation(active_window=active_window, windows=windows)
            evidence_bundle, evidence_ref = _record_desktop_evidence(
                source_action="desktop_capture_screenshot",
                active_window=active_window,
                windows=windows,
                observation_token=str(observation.get("observation_token", "")).strip(),
                include_ui_evidence=False,
                errors=["Could not capture the active window because no active window was detected."],
            )
            return _desktop_result(
                ok=False,
                action="desktop_capture_screenshot",
                summary="Could not capture the active window because no active window was detected.",
                desktop_state=observation,
                error="Could not capture the active window because no active window was detected.",
                desktop_evidence=evidence_bundle,
                desktop_evidence_ref=evidence_ref,
            )
        bounds = dict(active_window.get("rect", {}))
        capture_label = f"active window '{active_window.get('title', 'window')}'"

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
        scope=scope,
        active_window_title=str(active_window.get("title", "") or ""),
    )
    ok = bool(capture_result.get("ok", False))
    error = str(capture_result.get("error", "") or "").strip()
    capture_data = capture_result.get("data", {}) if isinstance(capture_result.get("data", {}), dict) else {}
    captured_path = str(capture_data.get("path", "") or "").strip() or str(path)
    observation = _register_observation(
        active_window=active_window,
        windows=windows,
        screenshot_path=captured_path if ok else "",
        screenshot_scope=scope,
    )
    evidence_bundle, evidence_ref = _record_desktop_evidence(
        source_action="desktop_capture_screenshot",
        active_window=active_window,
        windows=windows,
        observation_token=str(observation.get("observation_token", "")).strip(),
        screenshot={
            **capture_data,
            "path": captured_path if ok else "",
            "scope": scope,
            "bounds": {"x": capture_x, "y": capture_y, "width": width, "height": height},
            "active_window_title": str(active_window.get("title", "") or ""),
        },
        target_window=active_window if scope == "active_window" else {},
        include_ui_evidence=True,
        ui_limit=_coerce_int(args.get("ui_limit", 8), 8, minimum=1, maximum=12),
        errors=[error] if error else [],
    )
    if not ok:
        return _desktop_result(
            ok=False,
            action="desktop_capture_screenshot",
            summary=f"Could not capture a screenshot of the {capture_label}.",
            desktop_state=observation,
            error=error,
            desktop_evidence=evidence_bundle,
            desktop_evidence_ref=evidence_ref,
        )
    return _desktop_result(
        ok=True,
        action="desktop_capture_screenshot",
        summary=f"Captured a screenshot of the {capture_label}.",
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
        desktop_evidence_ref=desktop_evidence_ref,
    )


def desktop_click_point(args: Dict[str, Any]) -> Dict[str, Any]:
    x = _coerce_int(args.get("x", 0), 0, minimum=-20_000, maximum=20_000)
    y = _coerce_int(args.get("y", 0), 0, minimum=-20_000, maximum=20_000)
    token, observation, observation_error = _validate_fresh_observation(args)
    evidence_ref = _latest_evidence_ref_for_observation(token)
    active_window = _active_window_info()
    windows = _enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT)
    if observation_error:
        state = _register_observation(active_window=active_window, windows=windows)
        return _desktop_result(
            ok=False,
            action="desktop_click_point",
            summary=observation_error,
            desktop_state=state,
            error=observation_error,
            point={"x": x, "y": y},
            desktop_evidence_ref=evidence_ref,
        )
    if not active_window or not _foreground_window_matches(observation, active_window):
        state = _register_observation(active_window=active_window, windows=windows)
        message = "The previously inspected target window is no longer active. Focus the window and inspect desktop state again before clicking."
        return _desktop_result(
            ok=False,
            action="desktop_click_point",
            summary=message,
            desktop_state=state,
            error=message,
            point={"x": x, "y": y},
            desktop_evidence_ref=evidence_ref,
        )

    screen_rect = _virtual_screen_rect()
    if not _point_in_rect(x, y, screen_rect):
        state = _register_observation(active_window=active_window, windows=windows)
        message = f"The point ({x}, {y}) is outside the visible desktop."
        return _desktop_result(
            ok=False,
            action="desktop_click_point",
            summary=message,
            desktop_state=state,
            error=message,
            point={"x": x, "y": y},
            desktop_evidence_ref=evidence_ref,
        )

    active_rect = active_window.get("rect", {}) if isinstance(active_window.get("rect", {}), dict) else {}
    if not _point_in_rect(x, y, active_rect):
        state = _register_observation(active_window=active_window, windows=windows)
        message = f"The point ({x}, {y}) is outside the active window '{active_window.get('title', 'window')}'."
        return _desktop_result(
            ok=False,
            action="desktop_click_point",
            summary=message,
            desktop_state=state,
            error=message,
            point={"x": x, "y": y},
            desktop_evidence_ref=evidence_ref,
        )

    checkpoint_reason = str(args.get("checkpoint_reason", "")).strip() or (
        f"Clicking the desktop at ({x}, {y}) in '{active_window.get('title', 'the active window')}' requires explicit approval in this bounded control pass."
    )
    checkpoint_target = active_window.get("title", "") or "active window"
    if not _approval_granted(args):
        return _pause_desktop_action(
            action="desktop_click_point",
            summary=f"Approval required before clicking ({x}, {y}) in '{checkpoint_target}'.",
            active_window=active_window,
            windows=windows,
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=f"{checkpoint_target} @ ({x}, {y})",
            checkpoint_resume_args={
                "x": x,
                "y": y,
                "observation_token": token,
                "expected_window_id": active_window.get("window_id", ""),
                "expected_window_title": active_window.get("title", ""),
                "evidence_id": evidence_ref.get("evidence_id", ""),
            },
            point={"x": x, "y": y},
            desktop_evidence_ref=evidence_ref,
        )

    original_point = POINT()
    try:
        user32.GetCursorPos(ctypes.byref(original_point))
    except Exception:
        original_point = POINT(x=x, y=y)

    if not user32.SetCursorPos(x, y):
        state = _register_observation(active_window=active_window, windows=windows)
        return _desktop_result(
            ok=False,
            action="desktop_click_point",
            summary="Could not move the cursor to the requested point.",
            desktop_state=state,
            error="Could not move the cursor to the requested point.",
            approval_status="approved",
            point={"x": x, "y": y},
            desktop_evidence_ref=evidence_ref,
        )

    inputs = (INPUT * 2)(
        INPUT(type=INPUT_MOUSE, union=INPUT_UNION(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, None))),
        INPUT(type=INPUT_MOUSE, union=INPUT_UNION(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, None))),
    )
    sent = int(user32.SendInput(2, ctypes.byref(inputs), ctypes.sizeof(INPUT)) or 0)
    try:
        user32.SetCursorPos(original_point.x, original_point.y)
    except Exception:
        pass
    active_after = _active_window_info()
    observation_after = _register_observation(active_window=active_after, windows=_enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT))
    if sent != 2:
        return _desktop_result(
            ok=False,
            action="desktop_click_point",
            summary="The desktop click did not complete.",
            desktop_state=observation_after,
            error="The desktop click did not complete.",
            approval_status="approved",
            point={"x": x, "y": y},
            desktop_evidence_ref=evidence_ref,
        )
    return _desktop_result(
        ok=True,
        action="desktop_click_point",
        summary=f"Clicked ({x}, {y}) in '{active_after.get('title', checkpoint_target)}'.",
        desktop_state=observation_after,
        approval_status="approved",
        workflow_resumed=_coerce_bool(args.get("resume_from_checkpoint", False), False),
        point={"x": x, "y": y},
        desktop_evidence_ref=evidence_ref,
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
