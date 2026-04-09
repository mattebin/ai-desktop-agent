from __future__ import annotations

import ctypes
import threading
from typing import Any, Dict


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


_DESKTOP_OBSERVATIONS: Dict[str, Dict[str, Any]] = {}
_OBSERVATION_LOCK = threading.RLock()
_OBSERVATION_COUNTER = 0
_BACKEND_LOCK = threading.RLock()
_WINDOW_BACKEND = None
_SCREENSHOT_BACKEND = None
_UI_EVIDENCE_BACKEND = None
