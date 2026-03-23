from __future__ import annotations

from typing import Any, Dict, Iterable

from core.backend_schemas import (
    normalize_desktop_recovery_outcome,
    normalize_desktop_visual_stability,
    normalize_desktop_window_readiness,
    normalize_window_descriptor,
)


def _trim_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


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


def _window_titles_match(expected: str, actual: str) -> bool:
    expected_text = str(expected or "").strip().lower()
    actual_text = str(actual or "").strip().lower()
    if not expected_text or not actual_text:
        return False
    return expected_text == actual_text or expected_text in actual_text or actual_text in expected_text


def assess_visual_sample_signatures(signatures: Iterable[str], *, backend: str = "mss") -> Dict[str, Any]:
    normalized = [_trim_text(item, limit=120) for item in list(signatures or []) if _trim_text(item, limit=120)]
    if not normalized:
        return normalize_desktop_visual_stability(
            {
                "state": "missing",
                "stable": False,
                "sample_count": 0,
                "distinct_sample_count": 0,
                "changed": False,
                "backend": backend,
                "reason": "missing",
                "summary": "No visual samples were available for stability assessment.",
            }
        )

    distinct = len(set(normalized))
    stable = distinct <= 1
    return normalize_desktop_visual_stability(
        {
            "state": "stable" if stable else "unstable",
            "stable": stable,
            "sample_count": len(normalized),
            "distinct_sample_count": distinct,
            "changed": not stable,
            "backend": backend,
            "reason": "inspected" if stable else "visual_state_unstable",
            "summary": (
                "Visual state looked stable across bounded samples."
                if stable
                else "Visual state changed across bounded samples and may still be animating."
            ),
        }
    )


def classify_window_recovery_state(
    *,
    requested_title: str = "",
    requested_window_id: str = "",
    target_window: Dict[str, Any] | None = None,
    active_window: Dict[str, Any] | None = None,
    candidate_count: int = 0,
    readiness: Dict[str, Any] | None = None,
    visual_stability: Dict[str, Any] | None = None,
    expected_window_id: str = "",
    expected_window_title: str = "",
    backend: str = "desktop",
) -> Dict[str, Any]:
    target = normalize_window_descriptor(target_window or {}, backend=backend, reason="inspected")
    active = normalize_window_descriptor(active_window or {}, backend=backend, reason="inspected")
    readiness_view = normalize_desktop_window_readiness(readiness or {})
    visual_view = normalize_desktop_visual_stability(visual_stability or {})

    requested_title_text = _trim_text(requested_title, limit=180)
    requested_window_id_text = _trim_text(requested_window_id, limit=40)
    expected_window_id_text = _trim_text(expected_window_id, limit=40)
    expected_window_title_text = _trim_text(expected_window_title, limit=180)
    target_present = bool(target.get("window_id"))
    foreground_confirmed = bool(target_present and target.get("window_id") == active.get("window_id") and active.get("window_id"))
    target_visible = bool(target.get("is_visible", False)) and not bool(target.get("is_cloaked", False))
    target_minimized = bool(target.get("is_minimized", False))
    target_hidden = bool(target_present and (not target_visible or bool(target.get("is_cloaked", False))))
    target_rect = target.get("rect", {}) if isinstance(target.get("rect", {}), dict) else {}
    target_withdrawn = bool(
        target_hidden
        and int(target_rect.get("width", 0) or 0) <= 4
        and int(target_rect.get("height", 0) or 0) <= 4
    )
    target_loading = readiness_view.get("state") == "loading"
    target_ready = bool(readiness_view.get("ready", False)) or readiness_view.get("state") == "ready"

    target_match = True
    if expected_window_id_text:
        target_match = bool(target_present and target.get("window_id") == expected_window_id_text)
    elif expected_window_title_text:
        target_match = _window_titles_match(expected_window_title_text, target.get("title", ""))
    elif requested_title_text:
        target_match = _window_titles_match(requested_title_text, target.get("title", ""))

    if not target_present:
        reason = "tray_or_background_state" if requested_title_text or requested_window_id_text else "target_not_found"
        state = "missing"
        summary = (
            f"Could not find a visible top-level window for '{requested_title_text or requested_window_id_text}'. "
            "It may be closed, minimized to the tray, or only present in the background."
        )
    elif not target_match:
        reason = "target_mismatch"
        state = "needs_recovery"
        summary = (
            f"The current target window '{target.get('title', 'unknown window')}' does not match the expected window."
        )
    elif target_minimized:
        reason = "target_minimized"
        state = "needs_recovery"
        summary = f"'{target.get('title', 'The target window')}' is minimized and should be restored before interaction."
    elif target_withdrawn:
        reason = "target_withdrawn"
        state = "missing"
        summary = (
            f"'{target.get('title', 'The target window')}' still has a window handle, but it appears withdrawn or tray-like "
            "and is not visibly recoverable through the bounded desktop path."
        )
    elif target_hidden:
        reason = "target_hidden"
        state = "needs_recovery"
        summary = f"'{target.get('title', 'The target window')}' exists but is hidden or cloaked."
    elif readiness_view.get("state") == "not_ready":
        reason = "target_not_ready"
        state = "waiting"
        summary = readiness_view.get("summary", "") or f"'{target.get('title', 'The target window')}' is not ready yet."
    elif target_loading:
        reason = "target_loading"
        state = "waiting"
        summary = readiness_view.get("summary", "") or f"'{target.get('title', 'The target window')}' still looks like it is loading."
    elif visual_view.get("state") == "unstable":
        reason = "visual_state_unstable"
        state = "waiting"
        summary = visual_view.get("summary", "") or f"'{target.get('title', 'The target window')}' is still visually unstable."
    elif not foreground_confirmed:
        reason = "foreground_not_confirmed"
        state = "needs_recovery"
        summary = (
            f"'{target.get('title', 'The target window')}' exists, but the OS is not reporting it as the foreground window."
        )
    else:
        reason = "recovery_succeeded"
        state = "ready"
        summary = f"'{target.get('title', 'The target window')}' is present, foreground, and ready enough for bounded desktop work."

    return normalize_desktop_recovery_outcome(
        {
            "state": state,
            "reason": reason,
            "requested_title": requested_title_text,
            "requested_window_id": requested_window_id_text,
            "target_present": target_present,
            "foreground_confirmed": foreground_confirmed,
            "target_visible": target_visible,
            "target_minimized": target_minimized,
            "target_hidden": target_hidden,
            "target_withdrawn": target_withdrawn,
            "target_loading": target_loading,
            "target_ready": target_ready,
            "target_match": target_match,
            "candidate_count": candidate_count,
            "backend": backend,
            "summary": summary,
            "target_window": target,
            "active_window": active,
            "readiness": readiness_view,
            "visual_stability": visual_view,
        }
    )


def select_window_recovery_strategy(
    classification: Dict[str, Any] | None,
    *,
    attempt_count: int = 0,
    max_attempts: int = 2,
) -> Dict[str, Any]:
    classification = classification if isinstance(classification, dict) else {}
    reason = str(classification.get("reason", "") or "").strip().lower()
    state = str(classification.get("state", "") or "").strip().lower()
    attempts_used = max(0, int(attempt_count or 0))
    attempts_allowed = max(0, int(max_attempts or 0))

    if state == "ready":
        strategy = "no_action"
        summary = "Current window state is already sufficient."
    elif attempts_used >= attempts_allowed:
        strategy = "stop_and_report"
        summary = "The bounded recovery budget is exhausted, so the operator should stop and report the current window state."
    elif reason == "target_minimized":
        strategy = "restore_then_focus"
        summary = "Restore the minimized window, then verify foreground focus."
    elif reason == "target_hidden":
        strategy = "show_then_focus"
        summary = "Show the hidden window if possible, then verify foreground focus."
    elif reason == "target_withdrawn":
        strategy = "report_missing_target"
        summary = "Do not guess. Report that the target looks withdrawn or tray-like and is not visibly recoverable through the bounded desktop path."
    elif reason == "foreground_not_confirmed":
        strategy = "focus_then_verify"
        summary = "Retry a bounded focus request, then confirm the OS foreground window explicitly."
    elif reason in {"target_loading", "target_not_ready", "visual_state_unstable"}:
        strategy = "wait_for_readiness"
        summary = "Wait briefly for readiness or visual stability, then re-inspect once."
    elif reason == "target_mismatch":
        strategy = "reinspect_target"
        summary = "Re-inspect the target window before taking further action."
    elif reason in {"tray_or_background_state", "target_not_found"}:
        strategy = "report_missing_target"
        summary = "Do not guess. Report that the target is not visibly present and may be in the tray or background."
    else:
        strategy = "inspect_only"
        summary = "Collect one bounded fresh observation and then report the result."

    return {
        "strategy": strategy,
        "attempt_count": attempts_used,
        "max_attempts": attempts_allowed,
        "retry_allowed": attempts_used < attempts_allowed and strategy not in {"no_action", "report_missing_target", "stop_and_report"},
        "summary": summary,
    }
