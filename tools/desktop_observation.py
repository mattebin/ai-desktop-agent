from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple

from core.backend_schemas import (
    normalize_desktop_command_result,
    normalize_desktop_pointer_action,
    normalize_desktop_process_action,
)
from core.desktop_evidence import collect_display_metadata
from core.desktop_mapping import monitor_for_rect
from tools.desktop_constants import (
    DESKTOP_DEFAULT_MAX_OBSERVATION_AGE_SECONDS,
    DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS,
    DESKTOP_DEFAULT_VERIFICATION_SAMPLES,
    DESKTOP_DEFAULT_WINDOW_LIMIT,
    DESKTOP_OBSERVATION_LIMIT,
    DESKTOP_SENSITIVE_FIELD_TERMS,
    SM_CXVIRTUALSCREEN,
    SM_CYVIRTUALSCREEN,
    SM_XVIRTUALSCREEN,
    SM_YVIRTUALSCREEN,
    _BACKEND_LOCK,
    _DESKTOP_OBSERVATIONS,
    _OBSERVATION_COUNTER,
    _OBSERVATION_LOCK,
    user32,
)


def _desktop():
    """Lazy accessor — resolves names through the desktop facade module."""
    import tools.desktop as _mod
    return _mod


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
    from tools.desktop import _timestamp

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
    import tools.desktop_constants as _c

    with _OBSERVATION_LOCK:
        _DESKTOP_OBSERVATIONS.clear()
    with _BACKEND_LOCK:
        for backend in (_c._WINDOW_BACKEND, _c._SCREENSHOT_BACKEND, _c._UI_EVIDENCE_BACKEND):
            if backend is None:
                continue
            try:
                backend.shutdown()
            except Exception:
                pass
        _c._WINDOW_BACKEND = None
        _c._SCREENSHOT_BACKEND = None
        _c._UI_EVIDENCE_BACKEND = None


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
    from tools.desktop import _trim_text

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
    from tools.desktop import _trim_text

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
    _mod = _desktop()
    if launched_pid > 0:
        result = _mod.probe_process_context(pid=launched_pid)
        data = result.get("data", {}) if isinstance(result.get("data", {}), dict) else {}
        if data:
            return data
    for process_name in _normalize_expected_process_names(expected_process_names):
        result = _mod.probe_process_context(process_name=process_name)
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
    from tools.desktop import _active_window_info, _dedupe_windows, _enum_windows, _trim_text

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
    from tools.desktop import _execute_window_recovery

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
    from tools.desktop import _coerce_int

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
