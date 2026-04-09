from __future__ import annotations

import ctypes
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from core.backend_schemas import (
    normalize_desktop_window_readiness,
)
from core.desktop_evidence import (
    build_desktop_evidence_bundle,
    collect_display_metadata,
    get_desktop_evidence_store,
    summarize_evidence_bundle,
)
from core.desktop_mapping import monitor_for_rect
from core.desktop_matching import select_window_candidate
from core.desktop_recovery import classify_window_recovery_state, select_window_recovery_strategy
from core.desktop_scene import interpret_desktop_scene
from tools.desktop_backends import (
    create_screenshot_backend,
    create_ui_evidence_backend,
    create_window_backend,
    describe_backends,
)
from tools.desktop_constants import (
    DESKTOP_DEFAULT_CAPTURE_MAX_HEIGHT,
    DESKTOP_DEFAULT_CAPTURE_MAX_WIDTH,
    DESKTOP_DEFAULT_WINDOW_LIMIT,
    DESKTOP_OBSERVATION_LIMIT,
    DWMWA_CLOAKED,
    PROCESS_PER_MONITOR_DPI_AWARE,
    PROCESS_QUERY_LIMITED_INFORMATION,
    _DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
    _DPI_AWARENESS_LOCK,
    _DPI_AWARENESS_STATE,
    EnumWindowsProc,
    RECT,
    dwmapi,
    kernel32,
    shcore,
    user32,
)


_BACKEND_LOCK = threading.RLock()
_WINDOW_BACKEND = None
_SCREENSHOT_BACKEND = None
_UI_EVIDENCE_BACKEND = None


def _desktop():
    """Lazy accessor — resolves names through the desktop facade module."""
    import tools.desktop as _mod
    return _mod


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
    # Import here to avoid circular dependency with desktop.py
    from tools.desktop import _window_monitor_metadata

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
    # Import here to avoid circular dependency with desktop.py
    from tools.desktop import _display_metadata

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
    # Import here to avoid circular dependency with desktop.py
    from tools.desktop import _display_metadata

    hwnd = int(user32.GetForegroundWindow() or 0)
    return _window_info(hwnd, display=_display_metadata())


def _get_window_backend():
    _mod = _desktop()
    global _WINDOW_BACKEND
    with _BACKEND_LOCK:
        if _WINDOW_BACKEND is None:
            _WINDOW_BACKEND = create_window_backend(
                list_delegate=_mod._enum_windows_native,
                active_delegate=_mod._active_window_info_native,
                focus_delegate=_mod._focus_window_handle_native,
            )
        return _WINDOW_BACKEND


def _get_screenshot_backend():
    global _SCREENSHOT_BACKEND
    with _BACKEND_LOCK:
        if _SCREENSHOT_BACKEND is None:
            _SCREENSHOT_BACKEND = create_screenshot_backend(capture_delegate=_desktop()._capture_bitmap_native)
        return _SCREENSHOT_BACKEND


def _get_ui_evidence_backend():
    global _UI_EVIDENCE_BACKEND
    with _BACKEND_LOCK:
        if _UI_EVIDENCE_BACKEND is None:
            _UI_EVIDENCE_BACKEND = create_ui_evidence_backend()
        return _UI_EVIDENCE_BACKEND


def get_desktop_backend_status() -> Dict[str, Any]:
    _mod = _desktop()
    status = describe_backends(
        window_backend=_mod._get_window_backend(),
        screenshot_backend=_mod._get_screenshot_backend(),
        ui_evidence_backend=_mod._get_ui_evidence_backend(),
    )
    status["display"] = _mod._display_metadata()
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
    return _desktop()._get_ui_evidence_backend().probe(target=target, limit=limit)


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
    result = _desktop().probe_window_readiness(
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
    result = _desktop().probe_visual_stability(
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
    result = _desktop().probe_process_context(
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
    _mod = _desktop()
    limit = max(8, _coerce_int(args.get("limit", 16), 16, minimum=4, maximum=30))
    target_window, candidates, lookup_error, match_info = _mod._find_window(args)
    visible_windows = _mod._enum_windows(limit=min(limit, DESKTOP_DEFAULT_WINDOW_LIMIT))
    active_window = _mod._active_window_info()
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
    observation = _mod._register_observation(active_window=active_window, windows=visible_windows)
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
    _mod = _desktop()
    ui_result: Dict[str, Any] = {}
    collected_errors = list(errors or [])
    if include_ui_evidence:
        probe_target = str(active_window.get("title", "") or "active_window").strip() or "active_window"
        ui_result = _mod.probe_ui_evidence(target=probe_target, limit=ui_limit)
        if not ui_result.get("ok", False) and ui_result.get("error"):
            collected_errors.append(str(ui_result.get("error", "")).strip())

    screen = collect_display_metadata(_mod._virtual_screen_rect())
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
    _mod = _desktop()
    display = _mod._display_metadata()
    result = _mod._get_window_backend().list_windows(include_minimized=include_minimized, limit=limit)
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
            filtered.append(_mod._enrich_window_monitor_metadata(item, display=display))

    if include_hidden:
        native_windows = _mod._enum_windows_native(
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
                merged.append(_mod._enrich_window_monitor_metadata(item, display=display))
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
    return _mod._enum_windows_native(include_minimized=include_minimized, include_hidden=False, limit=limit)


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
    _mod = _desktop()
    display = _mod._display_metadata()
    result = _mod._get_window_backend().get_active_window()
    data = result.get("data", {}) if isinstance(result, dict) else {}
    active_window = data.get("active_window", {}) if isinstance(data, dict) else {}
    if not isinstance(active_window, dict):
        return {}
    return _mod._enrich_window_monitor_metadata(active_window, display=display)


def _find_window(args: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]], str, Dict[str, Any]]:
    window_id = _parse_hwnd(args.get("window_id", ""))
    requested_title = str(args.get("title", "") or args.get("match", "")).strip()
    requested_process_name = str(args.get("process_name", "") or args.get("expected_process_name", "")).strip()
    requested_class_name = str(args.get("class_name", "") or args.get("expected_class_name", "")).strip()
    exact = _coerce_bool(args.get("exact", False), False)
    requested_limit = _coerce_int(args.get("limit", 16), 16, minimum=4, maximum=30)
    _mod = _desktop()
    candidates = _mod._enum_windows(
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
        direct_match = _mod._find_window_by_exact_title_native(requested_title)
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
        process_result = _mod.probe_process_context(process_name=requested_process_name)
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
