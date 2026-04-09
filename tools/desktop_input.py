from __future__ import annotations

import ctypes
import hashlib
import struct
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from core.desktop_evidence import get_desktop_evidence_store
from core.desktop_mapping import (
    action_point_from_mapping,
    build_desktop_coordinate_mapping,
    monitor_for_rect,
    rect_contains_point,
)
from core.desktop_recovery import select_window_recovery_strategy
from tools.desktop_constants import (
    BITMAPINFO,
    BITMAPINFOHEADER,
    BI_RGB,
    DESKTOP_DEFAULT_CAPTURE_MAX_HEIGHT,
    DESKTOP_DEFAULT_CAPTURE_MAX_WIDTH,
    DESKTOP_DEFAULT_CAPTURE_SCOPE,
    DESKTOP_DEFAULT_HOVER_MS,
    DESKTOP_DEFAULT_KEY_REPEAT,
    DESKTOP_DEFAULT_MAX_OBSERVATION_AGE_SECONDS,
    DESKTOP_DEFAULT_SCROLL_UNITS,
    DESKTOP_DEFAULT_TYPE_MAX_CHARS,
    DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS,
    DESKTOP_DEFAULT_VERIFICATION_SAMPLES,
    DESKTOP_DEFAULT_WINDOW_LIMIT,
    DESKTOP_MAX_HOVER_MS,
    DESKTOP_MAX_KEY_REPEAT,
    DESKTOP_MAX_KEY_SEQUENCE_STEPS,
    DESKTOP_MAX_SCROLL_UNITS,
    DESKTOP_OBSERVATION_LIMIT,
    DESKTOP_SAFE_KEY_DISPLAY,
    DESKTOP_SAFE_KEY_VK,
    DESKTOP_SAFE_MODIFIER_DISPLAY,
    DESKTOP_SAFE_MODIFIER_VK,
    DESKTOP_SENSITIVE_FIELD_TERMS,
    DIB_RGB_COLORS,
    INPUT,
    INPUT_KEYBOARD,
    INPUT_MOUSE,
    INPUT_UNION,
    KEYBDINPUT,
    KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE,
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_RIGHTDOWN,
    MOUSEEVENTF_RIGHTUP,
    MOUSEEVENTF_WHEEL,
    MOUSEINPUT,
    POINT,
    SRCCOPY,
    SW_RESTORE,
    SW_SHOW,
    WHEEL_DELTA,
    gdi32,
    kernel32,
    user32,
)


# ---------------------------------------------------------------------------
# Lazy imports – these come from tools.desktop which holds the remaining
# internal helpers.  We import lazily inside each function that needs them
# so that we break the circular-import chain (desktop.py will later import
# from this module).
# ---------------------------------------------------------------------------

def _desktop():
    """Lazy accessor for the tools.desktop module (avoids circular imports)."""
    import tools.desktop as _mod
    return _mod


# ── mouse / pointer helpers ───────────────────────────────────────────────

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

    target_window, _candidates, lookup_error, _match_info = _desktop()._find_window(args)
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
    _mod = _desktop()
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
                display=display or _mod._display_metadata(),
                target_window=target_window,
                observation=observation,
            )
            return center, target_window, mapping, ""
        return {}, target_window, {}, "Provide bounded pointer coordinates before using this desktop pointer tool."

    point_x = _mod._coerce_int(point_x_raw, 0, minimum=-20_000, maximum=20_000)
    point_y = _mod._coerce_int(point_y_raw, 0, minimum=-20_000, maximum=20_000)
    mapping = build_desktop_coordinate_mapping(
        coordinate_mode=coordinate_mode,
        requested_point={"x": point_x, "y": point_y},
        display=display or _mod._display_metadata(),
        target_window=(target_window if isinstance(target_window, dict) and target_window.get("window_id") else active_window),
        observation=observation,
    )
    absolute_point, mapping_error = action_point_from_mapping(mapping)
    if mapping_error:
        return {}, target_window, mapping, mapping_error

    screen_rect = _mod._virtual_screen_rect()
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
    _mod = _desktop()
    down_flag, up_flag = _click_button_flags(button)
    bounded_click_count = _mod._coerce_int(click_count, 1, minimum=1, maximum=2)
    inputs: List[INPUT] = []
    for _ in range(bounded_click_count):
        inputs.append(INPUT(type=INPUT_MOUSE, union=INPUT_UNION(mi=MOUSEINPUT(0, 0, 0, down_flag, 0, None))))
        inputs.append(INPUT(type=INPUT_MOUSE, union=INPUT_UNION(mi=MOUSEINPUT(0, 0, 0, up_flag, 0, None))))
    payload = (INPUT * len(inputs))(*inputs)
    sent = int(user32.SendInput(len(inputs), ctypes.byref(payload), ctypes.sizeof(INPUT)) or 0)
    return sent == len(inputs)


def _send_mouse_scroll(direction: str, scroll_units: int) -> bool:
    _mod = _desktop()
    normalized_direction = str(direction or "").strip().lower()
    if normalized_direction not in {"up", "down"}:
        return False
    bounded_units = _mod._coerce_int(scroll_units, DESKTOP_DEFAULT_SCROLL_UNITS, minimum=1, maximum=DESKTOP_MAX_SCROLL_UNITS)
    signed_delta = WHEEL_DELTA * bounded_units * (1 if normalized_direction == "up" else -1)
    inputs = (INPUT * 1)(
        INPUT(type=INPUT_MOUSE, union=INPUT_UNION(mi=MOUSEINPUT(0, 0, ctypes.c_uint32(signed_delta & 0xFFFFFFFF).value, MOUSEEVENTF_WHEEL, 0, None)))
    )
    sent = int(user32.SendInput(1, ctypes.byref(inputs), ctypes.sizeof(INPUT)) or 0)
    return sent == 1


def _focus_window_handle_native(hwnd: int) -> Tuple[bool, str]:
    _mod = _desktop()
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

    active = _mod._active_window_info_native()
    if str(active.get("window_id", "")).strip() == _mod._hex_hwnd(handle):
        return True, ""
    return False, "Windows did not grant foreground focus to the requested window."


def _restore_window_handle_native(hwnd: int) -> Tuple[bool, str]:
    _mod = _desktop()
    handle = int(hwnd or 0)
    if handle <= 0 or not user32.IsWindow(ctypes.c_void_p(handle)):
        return False, "The requested window no longer exists."
    try:
        user32.ShowWindow(ctypes.c_void_p(handle), SW_RESTORE)
        time.sleep(0.08)
        return True, ""
    except Exception as exc:
        return False, _mod._trim_text(exc, limit=220)


def _show_window_handle_native(hwnd: int) -> Tuple[bool, str]:
    _mod = _desktop()
    handle = int(hwnd or 0)
    if handle <= 0 or not user32.IsWindow(ctypes.c_void_p(handle)):
        return False, "The requested window no longer exists."
    try:
        user32.ShowWindow(ctypes.c_void_p(handle), SW_SHOW)
        time.sleep(0.08)
        return True, ""
    except Exception as exc:
        return False, _mod._trim_text(exc, limit=220)


# ── capture / evidence ────────────────────────────────────────────────────

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
    _mod = _desktop()
    ok, error = _mod._focus_window_handle_native(hwnd)
    if ok:
        return True, ""
    return False, str(error or "Could not focus the requested window.").strip()


def _wait_for_window_ready(
    args: Dict[str, Any],
    *,
    action_name: str,
    attempt_count: int = 0,
) -> Dict[str, Any]:
    _mod = _desktop()
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
        inspected = _mod._inspect_window_state_internal(
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
    _mod = _desktop()
    include_ui_evidence = action_name not in {"desktop_focus_window"}
    include_visual_stability = action_name not in {"desktop_focus_window"}
    readiness_mode = "metadata_only" if action_name == "desktop_focus_window" else "full"
    inspected = _mod._inspect_window_state_internal(
        args,
        source_action=action_name,
        include_ui_evidence=include_ui_evidence,
        include_visual_stability=include_visual_stability,
        readiness_mode=readiness_mode,
    )
    recovery = inspected.get("recovery", {}) if isinstance(inspected.get("recovery", {}), dict) else {}
    target_window = inspected.get("target_window", {}) if isinstance(inspected.get("target_window", {}), dict) else {}
    max_attempts = _mod._coerce_int(args.get("max_attempts", 2), 2, minimum=0, maximum=4)
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

        handle = _mod._parse_hwnd(target_window.get("window_id", ""))
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
        inspected = _mod._inspect_window_state_internal(
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
    return _desktop()._get_screenshot_backend().capture(
        path,
        x=x,
        y=y,
        width=width,
        height=height,
        scope=scope,
        active_window_title=active_window_title,
    )


def _capture_path(args: Dict[str, Any], *, evidence_id: str = "") -> Path:
    _mod = _desktop()
    if evidence_id:
        extension = str(getattr(_mod._get_screenshot_backend(), "file_extension", ".bmp") or ".bmp").strip()
        if not extension.startswith("."):
            extension = f".{extension}"
        return get_desktop_evidence_store().artifact_path(evidence_id, extension=extension)

    name = str(args.get("name", "") or args.get("output_name", "")).strip()
    safe_name = "".join(ch for ch in name if ch.isalnum() or ch in {"-", "_"}).strip("._")
    if not safe_name:
        safe_name = f"desktop_capture_{int(time.time() * 1000)}"
    extension = str(getattr(_mod._get_screenshot_backend(), "file_extension", ".bmp") or ".bmp").strip()
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
    _mod = _desktop()
    metadata = display if isinstance(display, dict) else _mod._display_metadata()
    primary = _mod._primary_monitor_info(metadata)
    if isinstance(primary, dict) and int(primary.get("width", 0) or 0) > 0 and int(primary.get("height", 0) or 0) > 0:
        return _mod._monitor_rect(primary), primary
    return _mod._virtual_screen_rect(), {}


def _clip_bounds_to_rect(bounds: Dict[str, int], clip_rect: Dict[str, int]) -> Dict[str, int]:
    return _desktop()._rect_intersection(bounds, clip_rect)


def _capture_derived_active_window_crop(
    *,
    captured_path: str,
    active_window: Dict[str, Any],
    primary_bounds: Dict[str, int],
    active_window_title: str,
) -> Dict[str, Any]:
    _mod = _desktop()
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
    if _mod._rect_area(clipped) <= 0:
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
    _mod = _desktop()
    state = _mod._register_observation(active_window=active_window, windows=windows)
    window_title = str(active_window.get("title", "") or "the active window").strip()
    message = (
        f"Bounded desktop activity currently stays on the Windows primary monitor. "
        f"'{window_title}' is not on the primary display, so {action.replace('_', ' ')} was skipped."
    )
    return _mod._desktop_result(
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
    _mod = _desktop()
    requested_scope = str(scope or DESKTOP_DEFAULT_CAPTURE_SCOPE).strip().lower()
    active_window = _mod._active_window_info()
    windows = _mod._enum_windows(
        limit=_mod._coerce_int(limit, DESKTOP_DEFAULT_WINDOW_LIMIT, minimum=1, maximum=20)
    )
    display = _mod._display_metadata()
    primary_bounds, primary_monitor = _primary_monitor_bounds(display)
    evidence_id = get_desktop_evidence_store().next_evidence_id()
    args: Dict[str, Any] = {}
    if capture_name:
        args["name"] = capture_name

    if requested_scope == "desktop":
        bounds = _mod._virtual_screen_rect()
        capture_label = "desktop"
        target_window: Dict[str, Any] = {}
        capture_scope = "desktop"
    elif requested_scope in {"primary", "primary_display", "primary_monitor", "screen", "full_screen"}:
        bounds = dict(primary_bounds)
        capture_label = "primary display"
        target_window = dict(active_window) if _mod._window_is_on_primary_monitor(active_window) else {}
        capture_scope = "primary_monitor"
    else:
        capture_scope = "active_window"
        if not active_window:
            observation = _mod._register_observation(active_window=active_window, windows=windows)
            evidence_bundle: Dict[str, Any] = {}
            evidence_ref: Dict[str, Any] = {}
            if record_evidence and record_on_error:
                evidence_bundle, evidence_ref = _mod._record_desktop_evidence(
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
        and _mod._window_is_on_primary_monitor(active_window)
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
    observation = _mod._register_observation(
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
        evidence_bundle, evidence_ref = _mod._record_desktop_evidence(
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
            ui_limit=_mod._coerce_int(ui_limit, 8, minimum=1, maximum=12),
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
    return _desktop()._record_desktop_evidence(
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


# ── public tool functions for windows ─────────────────────────────────────

def desktop_list_windows(args: Dict[str, Any]) -> Dict[str, Any]:
    _mod = _desktop()
    limit = _mod._coerce_int(args.get("limit", DESKTOP_DEFAULT_WINDOW_LIMIT), DESKTOP_DEFAULT_WINDOW_LIMIT, minimum=1, maximum=20)
    windows = _mod._enum_windows(limit=limit)
    active_window = _mod._active_window_info()
    observation = _mod._register_observation(active_window=active_window, windows=windows)
    evidence_bundle, evidence_ref = _mod._record_desktop_evidence(
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
    return _mod._desktop_result(
        ok=True,
        action="desktop_list_windows",
        summary=summary,
        desktop_state=observation,
        desktop_evidence=evidence_bundle,
        desktop_evidence_ref=evidence_ref,
    )


def desktop_get_active_window(args: Dict[str, Any]) -> Dict[str, Any]:
    _mod = _desktop()
    windows = _mod._enum_windows(limit=_mod._coerce_int(args.get("limit", DESKTOP_DEFAULT_WINDOW_LIMIT), DESKTOP_DEFAULT_WINDOW_LIMIT, minimum=1, maximum=20))
    active_window = _mod._active_window_info()
    observation = _mod._register_observation(active_window=active_window, windows=windows)
    evidence_bundle, evidence_ref = _mod._record_desktop_evidence(
        source_action="desktop_get_active_window",
        active_window=active_window,
        windows=windows,
        observation_token=str(observation.get("observation_token", "")).strip(),
        include_ui_evidence=False,
    )
    if not active_window:
        return _mod._desktop_result(
            ok=False,
            action="desktop_get_active_window",
            summary="Could not determine the active window.",
            desktop_state=observation,
            error="Could not determine the active window.",
            desktop_evidence=evidence_bundle,
            desktop_evidence_ref=evidence_ref,
        )
    return _mod._desktop_result(
        ok=True,
        action="desktop_get_active_window",
        summary=f"The active window is '{active_window.get('title', 'unknown window')}'.",
        desktop_state=observation,
        desktop_evidence=evidence_bundle,
        desktop_evidence_ref=evidence_ref,
    )


def desktop_inspect_window_state(args: Dict[str, Any]) -> Dict[str, Any]:
    _mod = _desktop()
    inspected = _mod._inspect_window_state_internal(
        args,
        source_action="desktop_inspect_window_state",
        include_ui_evidence=True,
        include_visual_stability=_mod._coerce_bool(args.get("check_visual_stability", True), True),
    )
    recovery = inspected.get("recovery", {}) if isinstance(inspected.get("recovery", {}), dict) else {}
    summary = str(recovery.get("summary", "") or "").strip() or "Inspected the current desktop window state."
    ok = recovery.get("state") == "ready"
    error = "" if ok else summary
    return _mod._desktop_result(
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
    _mod = _desktop()
    waited = _wait_for_window_ready(args, action_name="desktop_wait_for_window_ready")
    recovery = waited.get("recovery", {}) if isinstance(waited.get("recovery", {}), dict) else {}
    ok = recovery.get("state") == "ready"
    summary = str(recovery.get("summary", "") or "").strip() or "Finished the bounded window readiness check."
    error = "" if ok else summary
    return _mod._desktop_result(
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
    _mod = _desktop()
    recovered = _execute_window_recovery(args, action_name="desktop_recover_window")
    recovery = recovered.get("recovery", {}) if isinstance(recovered.get("recovery", {}), dict) else {}
    ok = recovery.get("state") == "ready"
    summary = str(recovery.get("summary", "") or "").strip() or "Completed the bounded desktop recovery attempt."
    error = "" if ok else summary
    return _mod._desktop_result(
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
    _mod = _desktop()
    before_active_window, before_windows, _before_observation = _mod._current_desktop_context(limit=20)
    strategy_view = _mod._desktop_strategy_view(
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
        _mod._sample_desktop_action_verification(
            action="desktop_focus_window",
            validator_family=str(strategy_view.get("validator_family", "") or "focus_switch"),
            strategy_family=str(strategy_view.get("strategy_family", "") or "focus_recovery_window"),
            before_active_window=before_active_window,
            before_windows=before_windows,
            expected_title=str(target_window.get("title", "") or args.get("title", "") or args.get("expected_window_title", "")).strip(),
            expected_window_id=str(target_window.get("window_id", "") or args.get("window_id", "") or args.get("expected_window_id", "")).strip(),
            expected_process_names=[str(target_window.get("process_name", "")).strip()],
            target_description=str(target_window.get("title", "") or args.get("title", "") or "requested window").strip(),
            sample_count=_mod._coerce_int(args.get("verification_samples", DESKTOP_DEFAULT_VERIFICATION_SAMPLES), DESKTOP_DEFAULT_VERIFICATION_SAMPLES, minimum=2, maximum=4),
            interval_ms=_mod._coerce_int(args.get("verification_interval_ms", DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS), DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS, minimum=80, maximum=320),
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
    return _mod._desktop_result(
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
    _mod = _desktop()
    scope = str(args.get("scope", DESKTOP_DEFAULT_CAPTURE_SCOPE)).strip().lower() or DESKTOP_DEFAULT_CAPTURE_SCOPE
    capture = capture_desktop_evidence_frame(
        scope=scope,
        source_action="desktop_capture_screenshot",
        limit=_mod._coerce_int(args.get("limit", DESKTOP_DEFAULT_WINDOW_LIMIT), DESKTOP_DEFAULT_WINDOW_LIMIT, minimum=1, maximum=20),
        capture_name=str(args.get("name", "") or args.get("output_name", "")).strip(),
        include_ui_evidence=True,
        ui_limit=_mod._coerce_int(args.get("ui_limit", 8), 8, minimum=1, maximum=12),
        capture_mode="manual",
        importance="manual",
        importance_reason="manual_capture",
    )
    ok = bool(capture.get("ok", False))
    observation = capture.get("observation", {}) if isinstance(capture.get("observation", {}), dict) else {}
    evidence_bundle = capture.get("evidence_bundle", {}) if isinstance(capture.get("evidence_bundle", {}), dict) else {}
    evidence_ref = capture.get("evidence_ref", {}) if isinstance(capture.get("evidence_ref", {}), dict) else {}
    if not ok:
        return _mod._desktop_result(
            ok=False,
            action="desktop_capture_screenshot",
            summary=f"Could not capture a screenshot of the {capture.get('capture_label', 'desktop')}.",
            desktop_state=observation,
            error=str(capture.get("error", "")).strip(),
            desktop_evidence=evidence_bundle,
            desktop_evidence_ref=evidence_ref,
        )
    return _mod._desktop_result(
        ok=True,
        action="desktop_capture_screenshot",
        summary=f"Captured a screenshot of the {capture.get('capture_label', 'desktop')}.",
        desktop_state=observation,
        desktop_evidence=evidence_bundle,
        desktop_evidence_ref=evidence_ref,
    )


# ── pointer action infrastructure ─────────────────────────────────────────

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
    _mod = _desktop()
    observation = _mod._register_observation(active_window=active_window, windows=windows)
    resume_args = dict(checkpoint_resume_args)
    resume_args.setdefault("observation_token", observation.get("observation_token", ""))
    resume_args.setdefault("max_observation_age_seconds", 120)
    return _mod._desktop_result(
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
    _mod = _desktop()
    token, observation, observation_error = _mod._validate_fresh_observation(args)
    evidence_ref = _mod._latest_evidence_ref_for_observation(token)
    display = _mod._display_metadata()
    active_window = _mod._active_window_info()
    windows = _mod._enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT)
    if observation_error:
        state = _mod._register_observation(active_window=active_window, windows=windows)
        return {
            "ok": False,
            "result": _mod._desktop_result(
                ok=False,
                action=action,
                summary=observation_error,
                desktop_state=state,
                error=observation_error,
                desktop_evidence_ref=evidence_ref,
            ),
        }
    if not active_window or not _mod._foreground_window_matches(observation, active_window):
        state = _mod._register_observation(active_window=active_window, windows=windows)
        message = "The previously inspected target window is no longer active. Focus the window and inspect desktop state again before using a real desktop pointer action."
        return {
            "ok": False,
            "result": _mod._desktop_result(
                ok=False,
                action=action,
                summary=message,
                desktop_state=state,
                error=message,
                desktop_evidence_ref=evidence_ref,
            ),
        }
    if active_window and not _mod._window_is_on_primary_monitor(active_window) and _mod._evidence_ref_has_screenshot(evidence_ref):
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
        state = _mod._register_observation(active_window=active_window, windows=windows)
        return {
            "ok": False,
            "result": _mod._desktop_result(
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


# ── pointer tool functions ────────────────────────────────────────────────

def desktop_move_mouse(args: Dict[str, Any]) -> Dict[str, Any]:
    _mod = _desktop()
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
    if not _mod._approval_granted(args):
        if not _mod._evidence_ref_has_screenshot(evidence_ref):
            state = _mod._register_observation(active_window=active_window, windows=windows)
            message = "Approval-gated desktop mouse movement needs a screenshot-backed inspection of the active window first."
            return _mod._desktop_result(
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
    active_after = _mod._active_window_info()
    observation_after = _mod._register_observation(active_window=active_after, windows=_mod._enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT))
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
    return _mod._desktop_result(
        ok=moved,
        action="desktop_move_mouse",
        summary=mouse_action["summary"],
        desktop_state=observation_after,
        error="" if moved else "Could not move the mouse to the requested bounded point.",
        approval_status="approved",
        workflow_resumed=_mod._coerce_bool(args.get("resume_from_checkpoint", False), False),
        point=point,
        mouse_action=mouse_action,
        desktop_evidence_ref=evidence_ref,
        target_window=target_window,
    )


def desktop_hover_point(args: Dict[str, Any]) -> Dict[str, Any]:
    _mod = _desktop()
    context = _prepare_pointer_action_context(args, action="desktop_hover_point")
    if not context.get("ok", False):
        return context.get("result", {})

    point = context["point"]
    active_window = context["active_window"]
    windows = context["windows"]
    evidence_ref = context["evidence_ref"]
    target_window = context["target_window"]
    coordinate_mapping = context["coordinate_mapping"]
    hover_ms = _mod._coerce_int(args.get("hover_ms", DESKTOP_DEFAULT_HOVER_MS), DESKTOP_DEFAULT_HOVER_MS, minimum=120, maximum=DESKTOP_MAX_HOVER_MS)
    checkpoint_target = f"{target_window.get('title', active_window.get('title', 'active window'))} @ ({point.get('x')}, {point.get('y')}) :: hover"
    checkpoint_reason = str(args.get("checkpoint_reason", "")).strip() or (
        f"Hovering the desktop cursor over ({point.get('x')}, {point.get('y')}) in '{target_window.get('title', active_window.get('title', 'the active window'))}' requires explicit approval in this bounded control pass."
    )
    if not _mod._approval_granted(args):
        if not _mod._evidence_ref_has_screenshot(evidence_ref):
            state = _mod._register_observation(active_window=active_window, windows=windows)
            message = "Approval-gated desktop hovering needs a screenshot-backed inspection of the active window first."
            return _mod._desktop_result(
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
    active_after = _mod._active_window_info()
    observation_after = _mod._register_observation(active_window=active_after, windows=_mod._enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT))
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
    return _mod._desktop_result(
        ok=moved,
        action="desktop_hover_point",
        summary=mouse_action["summary"],
        desktop_state=observation_after,
        error="" if moved else "Could not hover over the requested bounded desktop point.",
        approval_status="approved",
        workflow_resumed=_mod._coerce_bool(args.get("resume_from_checkpoint", False), False),
        point=point,
        mouse_action=mouse_action,
        desktop_evidence_ref=evidence_ref,
        target_window=target_window,
    )


def desktop_click_mouse(args: Dict[str, Any]) -> Dict[str, Any]:
    _mod = _desktop()
    strategy_context = _mod._prepare_desktop_strategy_context(
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
    click_count = 2 if _mod._coerce_bool(action_args.get("double_click", False), False) else _mod._coerce_int(action_args.get("click_count", 1), 1, minimum=1, maximum=2)
    click_label = f"{button} {'double-click' if click_count == 2 else 'click'}"
    checkpoint_target = f"{target_window.get('title', active_window.get('title', 'active window'))} @ ({point.get('x')}, {point.get('y')}) :: {click_label}"
    checkpoint_reason = str(action_args.get("checkpoint_reason", "")).strip() or (
        f"{click_label.title()}ing at ({point.get('x')}, {point.get('y')}) in '{target_window.get('title', active_window.get('title', 'the active window'))}' requires explicit approval in this bounded control pass."
    )
    if not _mod._approval_granted(action_args):
        if not _mod._evidence_ref_has_screenshot(evidence_ref):
            state = _mod._register_observation(active_window=active_window, windows=windows)
            message = "Approval-gated desktop mouse clicks need a screenshot-backed inspection of the active window first."
            return _mod._desktop_result(
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
    active_after = _mod._active_window_info()
    observation_after = _mod._register_observation(active_window=active_after, windows=_mod._enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT))
    verification = (
        _mod._sample_desktop_action_verification(
            action="desktop_click_mouse",
            validator_family=str(strategy_view.get("validator_family", "") or "click_navigation"),
            strategy_family=str(strategy_view.get("strategy_family", "") or "direct_interaction"),
            before_active_window=active_window,
            before_windows=windows,
            expected_title=str(target_window.get("title", "") or action_args.get("expected_window_title", "")).strip(),
            expected_window_id=str(target_window.get("window_id", "") or action_args.get("expected_window_id", "")).strip(),
            expected_process_names=[str(target_window.get("process_name", "")).strip()],
            target_description=f"{click_label} in {target_window.get('title', active_window.get('title', 'the active window'))}",
            sample_count=_mod._coerce_int(action_args.get("verification_samples", DESKTOP_DEFAULT_VERIFICATION_SAMPLES), DESKTOP_DEFAULT_VERIFICATION_SAMPLES, minimum=2, maximum=4),
            interval_ms=_mod._coerce_int(action_args.get("verification_interval_ms", DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS), DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS, minimum=80, maximum=320),
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
    return _mod._desktop_result(
        ok=clicked,
        action="desktop_click_mouse",
        summary=mouse_action["summary"],
        desktop_state=observation_after,
        error="" if clicked else f"Could not perform the requested bounded {click_label}.",
        approval_status="approved",
        workflow_resumed=_mod._coerce_bool(action_args.get("resume_from_checkpoint", False), False),
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
    _mod = _desktop()
    strategy_context = _mod._prepare_desktop_strategy_context(
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
        state = _mod._register_observation(active_window=active_window, windows=windows)
        message = "desktop_scroll only supports bounded 'up' or 'down' directions."
        return _mod._desktop_result(
            ok=False,
            action="desktop_scroll",
            summary=message,
            desktop_state=state,
            error=message,
            point=point,
            desktop_evidence_ref=evidence_ref,
            target_window=target_window,
        )
    scroll_units = _mod._coerce_int(action_args.get("scroll_units", action_args.get("lines", DESKTOP_DEFAULT_SCROLL_UNITS)), DESKTOP_DEFAULT_SCROLL_UNITS, minimum=1, maximum=DESKTOP_MAX_SCROLL_UNITS)
    checkpoint_target = f"{target_window.get('title', active_window.get('title', 'active window'))} :: scroll {direction} x{scroll_units}"
    checkpoint_reason = str(action_args.get("checkpoint_reason", "")).strip() or (
        f"Scrolling {direction} by {scroll_units} unit(s) in '{target_window.get('title', active_window.get('title', 'the active window'))}' requires explicit approval in this bounded control pass."
    )
    if not _mod._approval_granted(action_args):
        if not _mod._evidence_ref_has_screenshot(evidence_ref):
            state = _mod._register_observation(active_window=active_window, windows=windows)
            message = "Approval-gated desktop scrolling needs a screenshot-backed inspection of the active window first."
            return _mod._desktop_result(
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
    active_after = _mod._active_window_info()
    observation_after = _mod._register_observation(active_window=active_after, windows=_mod._enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT))
    verification = (
        _mod._sample_desktop_action_verification(
            action="desktop_scroll",
            validator_family=str(strategy_view.get("validator_family", "") or "click_navigation"),
            strategy_family=str(strategy_view.get("strategy_family", "") or "direct_interaction"),
            before_active_window=active_window,
            before_windows=windows,
            expected_title=str(target_window.get("title", "") or action_args.get("expected_window_title", "")).strip(),
            expected_window_id=str(target_window.get("window_id", "") or action_args.get("expected_window_id", "")).strip(),
            expected_process_names=[str(target_window.get("process_name", "")).strip()],
            target_description=f"scroll {direction} in {target_window.get('title', active_window.get('title', 'the active window'))}",
            sample_count=_mod._coerce_int(action_args.get("verification_samples", DESKTOP_DEFAULT_VERIFICATION_SAMPLES), DESKTOP_DEFAULT_VERIFICATION_SAMPLES, minimum=2, maximum=4),
            interval_ms=_mod._coerce_int(action_args.get("verification_interval_ms", DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS), DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS, minimum=80, maximum=320),
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
    return _mod._desktop_result(
        ok=scrolled,
        action="desktop_scroll",
        summary=mouse_action["summary"],
        desktop_state=observation_after,
        error="" if scrolled else "Could not perform the bounded desktop scroll.",
        approval_status="approved",
        workflow_resumed=_mod._coerce_bool(action_args.get("resume_from_checkpoint", False), False),
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


# ── keyboard functions ────────────────────────────────────────────────────

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
    _mod = _desktop()
    if not isinstance(value, list):
        return []
    items: List[Dict[str, Any]] = []
    for raw_item in value[:DESKTOP_MAX_KEY_SEQUENCE_STEPS]:
        if not isinstance(raw_item, dict):
            continue
        key_name = _normalize_key_name(raw_item.get("key", ""))
        modifiers = _normalize_modifier_list(raw_item.get("modifiers", []))
        repeat = _mod._coerce_int(raw_item.get("repeat", 1), 1, minimum=1, maximum=DESKTOP_MAX_KEY_REPEAT)
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
    _mod = _desktop()
    vk = DESKTOP_SAFE_KEY_VK.get(key_name)
    if not vk:
        return False
    repeat_count = _mod._coerce_int(repeat, DESKTOP_DEFAULT_KEY_REPEAT, minimum=1, maximum=DESKTOP_MAX_KEY_REPEAT)
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
    _mod = _desktop()
    field_label = str(args.get("field_label", "")).strip()
    if not field_label:
        windows = _mod._enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT)
        active_window = _mod._active_window_info()
        observation = _mod._register_observation(active_window=active_window, windows=windows)
        return _mod._desktop_result(
            ok=False,
            action="desktop_type_text",
            summary="Provide a non-sensitive field_label before typing into the focused window.",
            desktop_state=observation,
            error="Provide a non-sensitive field_label before typing into the focused window.",
        )

    if _mod._sensitive_field_label(field_label):
        windows = _mod._enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT)
        active_window = _mod._active_window_info()
        observation = _mod._register_observation(active_window=active_window, windows=windows)
        return _mod._desktop_result(
            ok=False,
            action="desktop_type_text",
            summary=f"Typing into '{field_label}' is outside the safe desktop scope for this pass.",
            desktop_state=observation,
            error=f"Typing into '{field_label}' is outside the safe desktop scope for this pass.",
        )

    value = str(args.get("value", ""))
    max_chars = _mod._coerce_int(args.get("max_text_length", DESKTOP_DEFAULT_TYPE_MAX_CHARS), DESKTOP_DEFAULT_TYPE_MAX_CHARS, minimum=1, maximum=DESKTOP_DEFAULT_TYPE_MAX_CHARS)
    if not value.strip():
        windows = _mod._enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT)
        active_window = _mod._active_window_info()
        observation = _mod._register_observation(active_window=active_window, windows=windows)
        return _mod._desktop_result(
            ok=False,
            action="desktop_type_text",
            summary="Provide non-empty text before typing into the focused window.",
            desktop_state=observation,
            error="Provide non-empty text before typing into the focused window.",
        )
    if len(value) > max_chars:
        windows = _mod._enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT)
        active_window = _mod._active_window_info()
        observation = _mod._register_observation(active_window=active_window, windows=windows)
        return _mod._desktop_result(
            ok=False,
            action="desktop_type_text",
            summary=f"Text is too long for this bounded desktop typing tool (max {max_chars} characters).",
            desktop_state=observation,
            error=f"Text is too long for this bounded desktop typing tool (max {max_chars} characters).",
            typed_text_preview=_mod._trim_text(value, limit=60),
        )

    strategy_context = _mod._prepare_desktop_strategy_context(
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

    token, observation, observation_error = _mod._validate_fresh_observation(action_args)
    evidence_ref = _mod._latest_evidence_ref_for_observation(token)
    active_window = _mod._active_window_info()
    windows = _mod._enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT)
    if observation_error:
        state = _mod._register_observation(active_window=active_window, windows=windows)
        return _mod._desktop_result(
            ok=False,
            action="desktop_type_text",
            summary=observation_error,
            desktop_state=state,
            error=observation_error,
            typed_text_preview=_mod._trim_text(value, limit=60),
            desktop_evidence_ref=evidence_ref,
        )

    if not active_window or not _mod._foreground_window_matches(observation, active_window):
        state = _mod._register_observation(active_window=active_window, windows=windows)
        message = "The previously inspected target window is no longer active. Focus the window and inspect desktop state again before typing."
        return _mod._desktop_result(
            ok=False,
            action="desktop_type_text",
            summary=message,
            desktop_state=state,
            error=message,
            typed_text_preview=_mod._trim_text(value, limit=60),
            desktop_evidence_ref=evidence_ref,
        )
    if active_window and not _mod._window_is_on_primary_monitor(active_window):
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
    if not _mod._approval_granted(action_args):
        if not _mod._evidence_ref_has_screenshot(evidence_ref):
            state = _mod._register_observation(active_window=active_window, windows=windows)
            message = "Approval-gated desktop typing needs a screenshot-backed inspection of the active window first."
            return _mod._desktop_result(
                ok=False,
                action="desktop_type_text",
                summary=message,
                desktop_state=state,
                error=message,
                typed_text_preview=_mod._trim_text(value, limit=60),
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
    active_after = _mod._active_window_info()
    observation_after = _mod._register_observation(active_window=active_after, windows=_mod._enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT))
    verification = (
        _mod._sample_desktop_action_verification(
            action="desktop_type_text",
            validator_family=str(strategy_view.get("validator_family", "") or "text_input"),
            strategy_family=str(strategy_view.get("strategy_family", "") or "direct_input"),
            before_active_window=active_window,
            before_windows=windows,
            expected_title=str(action_args.get("expected_window_title", "") or active_window.get("title", "")).strip(),
            expected_window_id=str(action_args.get("expected_window_id", "") or active_window.get("window_id", "")).strip(),
            expected_process_names=[str(active_window.get("process_name", "")).strip()],
            target_description=field_label,
            sample_count=_mod._coerce_int(action_args.get("verification_samples", DESKTOP_DEFAULT_VERIFICATION_SAMPLES), DESKTOP_DEFAULT_VERIFICATION_SAMPLES, minimum=2, maximum=4),
            interval_ms=_mod._coerce_int(action_args.get("verification_interval_ms", DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS), DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS, minimum=80, maximum=320),
        )
        if ok
        else {}
    )
    if not ok:
        return _mod._desktop_result(
            ok=False,
            action="desktop_type_text",
            summary=f"Could not type into '{field_label}' in '{active_after.get('title', active_window.get('title', 'the active window'))}'.",
            desktop_state=observation_after,
            error=f"Could not type into '{field_label}' in '{active_window.get('title', 'the active window')}'.",
            approval_status="approved",
            typed_text_preview=_mod._trim_text(value, limit=60),
            desktop_evidence_ref=evidence_ref,
            recovery=recovered.get("recovery", {}),
            recovery_attempts=recovered.get("recovery_attempts", []),
            window_readiness=recovered.get("readiness", {}),
            visual_stability=recovered.get("visual_stability", {}),
            process_context=recovered.get("process_context", {}),
            scene=recovered.get("scene", {}),
            desktop_strategy=strategy_view,
        )
    return _mod._desktop_result(
        ok=True,
        action="desktop_type_text",
        summary=f"Typed into '{field_label}' in '{active_after.get('title', active_window.get('title', 'the active window'))}'.",
        desktop_state=observation_after,
        approval_status="approved",
        workflow_resumed=_mod._coerce_bool(action_args.get("resume_from_checkpoint", False), False),
        typed_text_preview=_mod._trim_text(value, limit=60),
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
    _mod = _desktop()
    key_name = _normalize_key_name(args.get("key", ""))
    modifiers = _normalize_modifier_list(args.get("modifiers", []))
    repeat = _mod._coerce_int(
        args.get("repeat", DESKTOP_DEFAULT_KEY_REPEAT),
        DESKTOP_DEFAULT_KEY_REPEAT,
        minimum=1,
        maximum=DESKTOP_MAX_KEY_REPEAT,
    )
    key_preview = _desktop_key_sequence_preview(key_name, modifiers, repeat) if key_name else ""
    validation_error = _validate_desktop_key_request(key_name, modifiers)
    if validation_error:
        windows = _mod._enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT)
        active_window = _mod._active_window_info()
        observation = _mod._register_observation(active_window=active_window, windows=windows)
        return _mod._desktop_result(
            ok=False,
            action="desktop_press_key",
            summary=validation_error,
            desktop_state=observation,
            error=validation_error,
            key_sequence_preview=key_preview,
        )

    strategy_context = _mod._prepare_desktop_strategy_context(
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

    token, observation, observation_error = _mod._validate_fresh_observation(action_args)
    evidence_ref = _mod._latest_evidence_ref_for_observation(token)
    active_window = _mod._active_window_info()
    windows = _mod._enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT)
    if observation_error:
        state = _mod._register_observation(active_window=active_window, windows=windows)
        return _mod._desktop_result(
            ok=False,
            action="desktop_press_key",
            summary=observation_error,
            desktop_state=state,
            error=observation_error,
            key_sequence_preview=key_preview,
            desktop_evidence_ref=evidence_ref,
        )

    if not active_window or not _mod._foreground_window_matches(observation, active_window):
        state = _mod._register_observation(active_window=active_window, windows=windows)
        message = "The previously inspected target window is no longer active. Focus the window and inspect desktop state again before pressing a key."
        return _mod._desktop_result(
            ok=False,
            action="desktop_press_key",
            summary=message,
            desktop_state=state,
            error=message,
            key_sequence_preview=key_preview,
            desktop_evidence_ref=evidence_ref,
        )
    if active_window and not _mod._window_is_on_primary_monitor(active_window):
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
    if not _mod._approval_granted(action_args):
        if not _mod._evidence_ref_has_screenshot(evidence_ref):
            state = _mod._register_observation(active_window=active_window, windows=windows)
            message = "Approval-gated desktop key presses need a screenshot-backed inspection of the active window first."
            return _mod._desktop_result(
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
    active_after = _mod._active_window_info()
    observation_after = _mod._register_observation(active_window=active_after, windows=_mod._enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT))
    verification = (
        _mod._sample_desktop_action_verification(
            action="desktop_press_key",
            validator_family=str(strategy_view.get("validator_family", "") or "text_input"),
            strategy_family=str(strategy_view.get("strategy_family", "") or "direct_input"),
            before_active_window=active_window,
            before_windows=windows,
            expected_title=str(action_args.get("expected_window_title", "") or active_window.get("title", "")).strip(),
            expected_window_id=str(action_args.get("expected_window_id", "") or active_window.get("window_id", "")).strip(),
            expected_process_names=[str(active_window.get("process_name", "")).strip()],
            target_description=key_preview or "bounded key press",
            sample_count=_mod._coerce_int(action_args.get("verification_samples", DESKTOP_DEFAULT_VERIFICATION_SAMPLES), DESKTOP_DEFAULT_VERIFICATION_SAMPLES, minimum=2, maximum=4),
            interval_ms=_mod._coerce_int(action_args.get("verification_interval_ms", DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS), DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS, minimum=80, maximum=320),
        )
        if ok
        else {}
    )
    if not ok:
        return _mod._desktop_result(
            ok=False,
            action="desktop_press_key",
            summary=f"Could not press {key_preview} in '{active_after.get('title', active_window.get('title', 'the active window'))}'.",
            desktop_state=observation_after,
            error=f"Could not press {key_preview} in '{active_window.get('title', 'the active window')}'.",
            approval_status="approved",
            workflow_resumed=_mod._coerce_bool(action_args.get("resume_from_checkpoint", False), False),
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
    return _mod._desktop_result(
        ok=True,
        action="desktop_press_key",
        summary=f"Pressed {key_preview} in '{active_after.get('title', active_window.get('title', 'the active window'))}'.",
        desktop_state=observation_after,
        approval_status="approved",
        workflow_resumed=_mod._coerce_bool(action_args.get("resume_from_checkpoint", False), False),
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
