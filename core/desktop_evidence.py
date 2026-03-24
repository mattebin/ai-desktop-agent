from __future__ import annotations

import json
import mimetypes
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List
from uuid import uuid4

from core.backend_schemas import (
    normalize_desktop_evidence_ref,
    normalize_desktop_evidence_assessment,
    normalize_desktop_evidence_artifact,
    normalize_desktop_evidence_summary,
    normalize_desktop_vision_context,
    normalize_desktop_vision_image,
    normalize_screen_observation,
    normalize_screenshot_observation,
    normalize_ui_evidence_observation,
    normalize_window_descriptor,
    result_envelope,
)
from core.config import load_settings
from core.desktop_matching import WINDOW_MATCH_THRESHOLD, describe_title_match

try:
    import mss
except Exception:
    mss = None  # type: ignore[assignment]


DEFAULT_DESKTOP_EVIDENCE_ROOT = "data/desktop_evidence"
DEFAULT_MAX_DESKTOP_EVIDENCE_ITEMS = 32

_STORE_LOCK = threading.RLock()
_STORE: "DesktopEvidenceStore | None" = None


def _trim_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _iso_timestamp() -> str:
    try:
        return datetime.now().astimezone().isoformat(timespec="seconds")
    except Exception:
        return ""


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _coerce_int(value: Any, default: int, *, minimum: int = 0, maximum: int = 100_000) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _normalize_capture_mode(value: Any) -> str:
    text = _trim_text(value, limit=40).lower()
    if text in {"auto", "manual", "checkpoint", "recovery"}:
        return text
    return ""


def _normalize_importance(value: Any) -> str:
    text = _trim_text(value, limit=40).lower()
    if text in {"normal", "important", "checkpoint", "manual"}:
        return text
    return ""


def _importance_rank(summary: Dict[str, Any]) -> int:
    importance = _normalize_importance(summary.get("importance", ""))
    capture_mode = _normalize_capture_mode(summary.get("capture_mode", ""))
    if importance == "checkpoint":
        return 4
    if importance == "manual" or capture_mode == "manual":
        return 3
    if importance == "important":
        return 2
    return 1


def _sanitize_controls(value: Any, *, limit: int = 12) -> List[Dict[str, Any]]:
    controls = value if isinstance(value, list) else []
    normalized: List[Dict[str, Any]] = []
    for item in controls[:limit]:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "name": _trim_text(item.get("name", ""), limit=160),
                "control_type": _trim_text(item.get("control_type", ""), limit=80),
                "automation_id": _trim_text(item.get("automation_id", ""), limit=120),
                "text": _trim_text(item.get("text", ""), limit=220),
            }
        )
    return normalized


def _sanitize_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
    active_window = normalize_window_descriptor(bundle.get("active_window", {}), backend=str(bundle.get("window_backend", "")), reason="inspected")
    target_window_raw = bundle.get("target_window", {})
    target_window = normalize_window_descriptor(target_window_raw, backend=str(bundle.get("window_backend", "")), reason="inspected") if isinstance(target_window_raw, dict) and target_window_raw else {}
    windows = [
        normalize_window_descriptor(item, backend=str(bundle.get("window_backend", "")), reason="inspected")
        for item in list(bundle.get("windows", []))[:12]
        if isinstance(item, dict)
    ]
    screenshot = bundle.get("screenshot", {}) if isinstance(bundle.get("screenshot", {}), dict) else {}
    normalized_screenshot = normalize_screenshot_observation(
        backend=str(screenshot.get("backend", bundle.get("screenshot_backend", ""))),
        path=str(screenshot.get("path", "")).strip(),
        scope=str(screenshot.get("scope", "")).strip(),
        bounds=screenshot.get("bounds", {}),
        active_window_title=str(screenshot.get("active_window_title", "") or active_window.get("title", "")),
        reason=str(screenshot.get("reason", bundle.get("reason", "partial"))),
        metadata=screenshot.get("metadata", {}),
    )
    ui_evidence = bundle.get("ui_evidence", {}) if isinstance(bundle.get("ui_evidence", {}), dict) else {}
    normalized_ui = normalize_ui_evidence_observation(
        backend=str(ui_evidence.get("backend", bundle.get("ui_evidence_backend", ""))),
        target=str(ui_evidence.get("target", "") or active_window.get("title", "")),
        controls=_sanitize_controls(ui_evidence.get("controls", [])),
        reason=str(ui_evidence.get("reason", bundle.get("reason", "partial"))),
        metadata=ui_evidence.get("metadata", {}),
    )
    screen = bundle.get("screen", {}) if isinstance(bundle.get("screen", {}), dict) else {}
    normalized_screen = normalize_screen_observation(
        virtual_screen=screen.get("virtual_screen", {}),
        monitors=screen.get("monitors", []),
        backend=str(screen.get("backend", "")),
        reason=str(screen.get("reason", "inspected")),
        metadata=screen.get("metadata", {}),
    )
    evidence_id = _trim_text(bundle.get("evidence_id", ""), limit=80) or f"desk-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
    reason = str(bundle.get("reason", "collected" if normalized_screenshot.get("path") else "partial")).strip().lower().replace(" ", "_")
    summary = _trim_text(
        bundle.get("summary", "")
        or (
            f"Collected desktop evidence for {active_window.get('title', 'desktop')}."
            if normalized_screenshot.get("path")
            else f"Collected partial desktop evidence for {active_window.get('title', 'desktop')}."
        ),
        limit=280,
    )
    bundle_path = _trim_text(bundle.get("bundle_path", ""), limit=320)
    source_action = _trim_text(bundle.get("source_action", ""), limit=80)
    observation_token = _trim_text(bundle.get("observation_token", ""), limit=120)
    window_backend = _trim_text(bundle.get("window_backend", active_window.get("backend", "")), limit=60)
    screenshot_backend = _trim_text(bundle.get("screenshot_backend", normalized_screenshot.get("backend", "")), limit=60)
    ui_backend = _trim_text(bundle.get("ui_evidence_backend", normalized_ui.get("backend", "")), limit=60)
    capture_mode = _normalize_capture_mode(
        bundle.get("capture_mode", "manual" if source_action == "desktop_capture_screenshot" else "auto" if source_action == "desktop_auto_capture" else "")
    )
    importance = _normalize_importance(
        bundle.get(
            "importance",
            "manual"
            if capture_mode == "manual"
            else "checkpoint"
            if bundle.get("checkpoint_pending")
            else "normal",
        )
    )
    importance_reason = _trim_text(bundle.get("importance_reason", ""), limit=120)
    state_scope_id = _trim_text(bundle.get("state_scope_id", ""), limit=120)
    task_id = _trim_text(bundle.get("task_id", ""), limit=60)
    task_status = _trim_text(bundle.get("task_status", ""), limit=40)
    checkpoint_pending = bool(bundle.get("checkpoint_pending", False))
    checkpoint_tool = _trim_text(bundle.get("checkpoint_tool", ""), limit=80)
    checkpoint_target = _trim_text(bundle.get("checkpoint_target", ""), limit=180)
    capture_signature = _trim_text(bundle.get("capture_signature", ""), limit=120)
    active_window_id = _trim_text(active_window.get("window_id", ""), limit=40)

    return {
        "evidence_id": evidence_id,
        "timestamp": _trim_text(bundle.get("timestamp", ""), limit=40) or _iso_timestamp(),
        "reason": reason,
        "summary": summary,
        "source_action": source_action,
        "observation_token": observation_token,
        "bundle_path": bundle_path,
        "active_window": active_window,
        "target_window": target_window,
        "windows": windows,
        "window_count": len(windows),
        "screen": normalized_screen,
        "screenshot": normalized_screenshot,
        "ui_evidence": normalized_ui,
        "window_backend": window_backend,
        "screenshot_backend": screenshot_backend,
        "ui_evidence_backend": ui_backend,
        "capture_mode": capture_mode,
        "importance": importance,
        "importance_reason": importance_reason,
        "state_scope_id": state_scope_id,
        "task_id": task_id,
        "task_status": task_status,
        "checkpoint_pending": checkpoint_pending,
        "checkpoint_tool": checkpoint_tool,
        "checkpoint_target": checkpoint_target,
        "active_window_id": active_window_id,
        "artifacts": {
            "bundle_path": bundle_path,
            "screenshot_path": _trim_text(normalized_screenshot.get("path", ""), limit=320),
        },
        "errors": [_trim_text(item, limit=220) for item in list(bundle.get("errors", []))[:6] if str(item).strip()],
        "metadata": {
            "partial": reason == "partial",
            "screen_monitor_count": int(normalized_screen.get("monitor_count", 0) or 0),
            "capture_signature": capture_signature,
        },
    }


def bundle_ref(bundle: Dict[str, Any]) -> Dict[str, Any]:
    return normalize_desktop_evidence_ref(
        {
            "evidence_id": bundle.get("evidence_id", ""),
            "timestamp": bundle.get("timestamp", ""),
            "reason": bundle.get("reason", ""),
            "summary": bundle.get("summary", ""),
            "bundle_path": bundle.get("bundle_path", "") or bundle.get("artifacts", {}).get("bundle_path", ""),
            "screenshot_path": bundle.get("artifacts", {}).get("screenshot_path", ""),
            "observation_token": bundle.get("observation_token", ""),
            "active_window_title": bundle.get("active_window", {}).get("title", "") if isinstance(bundle.get("active_window", {}), dict) else "",
            "backend": " / ".join(
                value for value in [
                    _trim_text(bundle.get("window_backend", ""), limit=60),
                    _trim_text(bundle.get("screenshot_backend", ""), limit=60),
                    _trim_text(bundle.get("ui_evidence_backend", ""), limit=60),
                ] if value
            ),
        }
    )


def summarize_evidence_bundle(bundle: Dict[str, Any], *, now: datetime | None = None) -> Dict[str, Any]:
    normalized = _sanitize_bundle(bundle)
    active_window = normalized.get("active_window", {}) if isinstance(normalized.get("active_window", {}), dict) else {}
    target_window = normalized.get("target_window", {}) if isinstance(normalized.get("target_window", {}), dict) else {}
    screenshot = normalized.get("screenshot", {}) if isinstance(normalized.get("screenshot", {}), dict) else {}
    ui_evidence = normalized.get("ui_evidence", {}) if isinstance(normalized.get("ui_evidence", {}), dict) else {}
    screen = normalized.get("screen", {}) if isinstance(normalized.get("screen", {}), dict) else {}
    artifacts = normalized.get("artifacts", {}) if isinstance(normalized.get("artifacts", {}), dict) else {}

    source_action = _trim_text(normalized.get("source_action", ""), limit=80)
    has_screenshot = bool(str(screenshot.get("path", "")).strip())
    has_artifact = bool(str(artifacts.get("screenshot_path", "")).strip() or str(screenshot.get("path", "")).strip())
    ui_controls = ui_evidence.get("controls", []) if isinstance(ui_evidence.get("controls", []), list) else []
    active_title = _trim_text(active_window.get("title", ""), limit=180)
    target_title = _trim_text(target_window.get("title", ""), limit=180)
    screen_virtual = screen.get("virtual_screen", {}) if isinstance(screen.get("virtual_screen", {}), dict) else {}
    width = _coerce_int(screen_virtual.get("width", 0), 0, minimum=0, maximum=100_000)
    height = _coerce_int(screen_virtual.get("height", 0), 0, minimum=0, maximum=100_000)
    backend_label = " / ".join(
        value
        for value in [
            _trim_text(normalized.get("window_backend", ""), limit=60),
            _trim_text(normalized.get("screenshot_backend", ""), limit=60),
            _trim_text(normalized.get("ui_evidence_backend", ""), limit=60),
        ]
        if value
    )

    if source_action == "desktop_capture_screenshot" or has_screenshot:
        evidence_kind = "desktop_capture"
    elif source_action == "desktop_focus_window":
        evidence_kind = "window_focus"
    elif source_action in {"desktop_get_active_window", "desktop_list_windows"}:
        evidence_kind = "window_observation"
    else:
        evidence_kind = "desktop_observation"

    timestamp_text = _trim_text(normalized.get("timestamp", ""), limit=40)
    now_value = now if isinstance(now, datetime) else datetime.now().astimezone()
    recorded_at = _parse_timestamp(timestamp_text)
    if recorded_at is None:
        recency_seconds = 0
    else:
        try:
            recency_seconds = max(0, int((now_value - recorded_at).total_seconds()))
        except Exception:
            recency_seconds = 0

    window_summary = _trim_text(
        f"{int(normalized.get('window_count', 0) or 0)} visible windows; active '{active_title or 'desktop'}'",
        limit=180,
    )
    if width and height:
        screen_summary = _trim_text(
            f"{width}x{height} across {int(screen.get('monitor_count', 0) or 0)} monitor(s)",
            limit=180,
        )
    else:
        screen_summary = _trim_text(
            f"{int(screen.get('monitor_count', 0) or 0)} monitor(s) observed",
            limit=180,
        )

    summary_text = _trim_text(
        normalized.get("summary", "")
        or (
            f"Collected {'partial ' if normalized.get('reason') == 'partial' else ''}desktop evidence for {active_title or 'the desktop'}."
        ),
        limit=240,
    )
    capture_mode = _normalize_capture_mode(normalized.get("capture_mode", ""))
    importance = _normalize_importance(normalized.get("importance", ""))
    return normalize_desktop_evidence_summary(
        {
            "evidence_id": normalized.get("evidence_id", ""),
            "timestamp": timestamp_text,
            "source_action": source_action,
            "evidence_kind": evidence_kind,
            "reason": normalized.get("reason", "partial"),
            "summary": summary_text,
            "active_window_title": active_title,
            "active_window_class_name": _trim_text(active_window.get("class_name", ""), limit=120),
            "active_window_process": _trim_text(active_window.get("process_name", ""), limit=120),
            "target_window_title": target_title,
            "window_count": int(normalized.get("window_count", 0) or 0),
            "monitor_count": int(screen.get("monitor_count", 0) or 0),
            "screen_size": {"width": width, "height": height},
            "window_summary": window_summary,
            "screen_summary": screen_summary,
            "has_screenshot": has_screenshot,
            "has_artifact": has_artifact,
            "screenshot_scope": _trim_text(screenshot.get("scope", ""), limit=60),
            "screenshot_backend": _trim_text(screenshot.get("backend", ""), limit=60),
            "screenshot_path": _trim_text(artifacts.get("screenshot_path", "") or screenshot.get("path", ""), limit=320),
            "bundle_path": _trim_text(artifacts.get("bundle_path", "") or normalized.get("bundle_path", ""), limit=320),
            "ui_evidence_present": bool(ui_controls),
            "ui_control_count": len(ui_controls),
            "observation_token": _trim_text(normalized.get("observation_token", ""), limit=120),
            "is_partial": str(normalized.get("reason", "")).strip().lower() == "partial",
            "recency_seconds": recency_seconds,
            "backend": backend_label,
            "selection_reason": "selected",
            "capture_mode": capture_mode,
            "importance": importance,
            "importance_reason": _trim_text(normalized.get("importance_reason", ""), limit=120),
            "state_scope_id": _trim_text(normalized.get("state_scope_id", ""), limit=120),
            "task_id": _trim_text(normalized.get("task_id", ""), limit=60),
            "task_status": _trim_text(normalized.get("task_status", ""), limit=40),
            "checkpoint_pending": bool(normalized.get("checkpoint_pending", False)),
            "checkpoint_tool": _trim_text(normalized.get("checkpoint_tool", ""), limit=80),
        }
    )


def compact_evidence_preview(value: Dict[str, Any] | None) -> Dict[str, Any]:
    summary = value if isinstance(value, dict) else {}
    normalized = normalize_desktop_evidence_summary(summary)
    return {
        "evidence_id": normalized.get("evidence_id", ""),
        "timestamp": normalized.get("timestamp", ""),
        "evidence_kind": normalized.get("evidence_kind", ""),
        "reason": normalized.get("reason", ""),
        "summary": normalized.get("summary", ""),
        "active_window_title": normalized.get("active_window_title", ""),
        "active_window_process": normalized.get("active_window_process", ""),
        "target_window_title": normalized.get("target_window_title", ""),
        "has_screenshot": bool(normalized.get("has_screenshot", False)),
        "has_artifact": bool(normalized.get("has_artifact", False)),
        "screenshot_scope": normalized.get("screenshot_scope", ""),
        "ui_evidence_present": bool(normalized.get("ui_evidence_present", False)),
        "ui_control_count": int(normalized.get("ui_control_count", 0) or 0),
        "is_partial": bool(normalized.get("is_partial", False)),
        "recency_seconds": int(normalized.get("recency_seconds", 0) or 0),
        "selection_reason": normalized.get("selection_reason", ""),
        "capture_mode": normalized.get("capture_mode", ""),
        "importance": normalized.get("importance", ""),
        "importance_reason": normalized.get("importance_reason", ""),
        "task_id": normalized.get("task_id", ""),
        "task_status": normalized.get("task_status", ""),
        "checkpoint_pending": bool(normalized.get("checkpoint_pending", False)),
    }


def assess_desktop_evidence(
    summary: Dict[str, Any] | None,
    *,
    purpose: str = "desktop_investigation",
    target_window_title: str = "",
    require_screenshot: bool = False,
    max_age_seconds: int = 180,
) -> Dict[str, Any]:
    normalized = normalize_desktop_evidence_summary(summary if isinstance(summary, dict) else {})
    purpose_text = _trim_text(purpose or "desktop_investigation", limit=80) or "desktop_investigation"
    target_text = _trim_text(target_window_title, limit=180)
    evidence_id = _trim_text(normalized.get("evidence_id", ""), limit=80)
    recency_seconds = _coerce_int(normalized.get("recency_seconds", 0), 0, minimum=0, maximum=10_000_000)
    age_limit = _coerce_int(max_age_seconds, 180, minimum=15, maximum=86_400)
    active_title = _trim_text(normalized.get("active_window_title", ""), limit=180).lower()
    selected_target = _trim_text(normalized.get("target_window_title", ""), limit=180).lower()
    desired_target = target_text.lower()
    target_match = True
    if desired_target:
        target_match = (
            desired_target in active_title
            or desired_target in selected_target
            or (active_title and active_title in desired_target)
            or (selected_target and selected_target in desired_target)
        )

    has_summary = bool(evidence_id or normalized.get("summary", ""))
    has_screenshot = bool(normalized.get("has_screenshot", False))
    is_partial = bool(normalized.get("is_partial", False))
    stale = recency_seconds > age_limit if has_summary else False

    state = "sufficient"
    sufficient = True
    needs_refresh = False
    reason = "current_evidence"

    if not has_summary:
        state = "missing"
        sufficient = False
        needs_refresh = True
        reason = "no_evidence"
    elif not target_match:
        state = "needs_refresh"
        sufficient = False
        needs_refresh = True
        reason = "target_window_mismatch"
    elif require_screenshot and not has_screenshot:
        state = "needs_refresh"
        sufficient = False
        needs_refresh = True
        reason = "missing_screenshot"
    elif stale:
        state = "needs_refresh"
        sufficient = False
        needs_refresh = True
        reason = "stale_evidence"
    elif is_partial:
        if purpose_text == "desktop_investigation":
            state = "partial"
            sufficient = True
            needs_refresh = False
            reason = "partial_but_answerable"
        else:
            state = "needs_refresh"
            sufficient = False
            needs_refresh = True
            reason = "partial_evidence"

    if state == "sufficient":
        summary_text = (
            f"Current desktop evidence is sufficient for {purpose_text.replace('_', ' ')}."
            if evidence_id
            else "Current desktop evidence is sufficient."
        )
    elif state == "partial":
        summary_text = (
            f"Current desktop evidence is partial but likely sufficient for {purpose_text.replace('_', ' ')}."
        )
    elif state == "missing":
        summary_text = "No relevant desktop evidence is available yet."
    elif reason == "target_window_mismatch":
        summary_text = "Current desktop evidence does not match the intended target window."
    elif reason == "missing_screenshot":
        summary_text = "A fresh desktop screenshot is recommended before this action."
    elif reason == "stale_evidence":
        summary_text = "Current desktop evidence is stale and should be refreshed."
    else:
        summary_text = "Current desktop evidence should be refreshed before proceeding."

    return normalize_desktop_evidence_assessment(
        {
            "evidence_id": evidence_id,
            "purpose": purpose_text,
            "state": state,
            "sufficient": sufficient,
            "needs_refresh": needs_refresh,
            "reason": reason,
            "summary": summary_text,
            "target_window_title": target_text,
            "target_window_match": target_match,
            "has_screenshot": has_screenshot,
            "is_partial": is_partial,
            "recency_seconds": recency_seconds,
            "stale": stale,
            "selection_reason": normalized.get("selection_reason", ""),
        }
    )


def describe_evidence_artifact(
    bundle: Dict[str, Any] | None,
    *,
    summary: Dict[str, Any] | None = None,
    content_path: str = "",
    evidence_id: str = "",
) -> Dict[str, Any]:
    normalized_summary = normalize_desktop_evidence_summary(summary if isinstance(summary, dict) else {})
    normalized_bundle = _sanitize_bundle(bundle if isinstance(bundle, dict) else {}) if isinstance(bundle, dict) and bundle else {}
    artifact_path = ""
    bundle_path = ""
    artifact_type = ""
    summary_text = ""
    artifact_available = False
    availability_state = "unavailable"
    reason = "unavailable"

    if normalized_bundle:
        artifacts = normalized_bundle.get("artifacts", {}) if isinstance(normalized_bundle.get("artifacts", {}), dict) else {}
        screenshot = normalized_bundle.get("screenshot", {}) if isinstance(normalized_bundle.get("screenshot", {}), dict) else {}
        artifact_path = _trim_text(artifacts.get("screenshot_path", "") or screenshot.get("path", ""), limit=320)
        bundle_path = _trim_text(artifacts.get("bundle_path", "") or normalized_bundle.get("bundle_path", ""), limit=320)
        summary_text = _trim_text(normalized_bundle.get("summary", ""), limit=220)
        if artifact_path:
            candidate = Path(artifact_path)
            artifact_available = candidate.exists() and candidate.is_file()
            artifact_type = _trim_text(mimetypes.guess_type(candidate.name)[0] or "image/png", limit=80)
            if artifact_available:
                availability_state = "available"
                reason = "available"
            else:
                availability_state = "missing"
                reason = "missing_artifact"
        else:
            availability_state = "unavailable"
            reason = "unavailable"
    else:
        artifact_path = _trim_text(normalized_summary.get("screenshot_path", ""), limit=320)
        bundle_path = _trim_text(normalized_summary.get("bundle_path", ""), limit=320)
        summary_text = _trim_text(normalized_summary.get("summary", ""), limit=220)
        if artifact_path or bundle_path:
            availability_state = "pruned"
            reason = "pruned"
        elif _trim_text(evidence_id or normalized_summary.get("evidence_id", ""), limit=80):
            availability_state = "not_found"
            reason = "not_found"

    return normalize_desktop_evidence_artifact(
        {
            "evidence_id": _trim_text(evidence_id or normalized_bundle.get("evidence_id", "") or normalized_summary.get("evidence_id", ""), limit=80),
            "artifact_available": artifact_available,
            "artifact_type": artifact_type,
            "artifact_path": artifact_path,
            "artifact_name": Path(artifact_path).name if artifact_path else "",
            "availability_state": availability_state,
            "reason": reason,
            "can_preview": bool(artifact_available and str(artifact_type).startswith("image/") and str(content_path).strip()),
            "content_path": _trim_text(content_path, limit=240),
            "bundle_path": bundle_path,
            "summary": summary_text,
        }
    )


def _artifact_available_from_summary(summary: Dict[str, Any] | None) -> bool:
    summary = summary if isinstance(summary, dict) else {}
    artifact_path = _trim_text(summary.get("screenshot_path", ""), limit=320)
    if not artifact_path:
        return False
    try:
        candidate = Path(artifact_path)
        return candidate.exists() and candidate.is_file()
    except Exception:
        return False


def _vision_image_from_summary(summary: Dict[str, Any] | None, *, role: str, selection_reason: str) -> Dict[str, Any]:
    summary = normalize_desktop_evidence_summary(summary if isinstance(summary, dict) else {})
    artifact_path = _trim_text(summary.get("screenshot_path", ""), limit=320)
    artifact_available = _artifact_available_from_summary(summary)
    artifact_type = _trim_text(mimetypes.guess_type(artifact_path)[0] or "image/png", limit=80) if artifact_path else ""
    return normalize_desktop_vision_image(
        {
            "evidence_id": summary.get("evidence_id", ""),
            "role": role,
            "selection_reason": selection_reason,
            "summary": summary.get("summary", ""),
            "active_window_title": summary.get("active_window_title", ""),
            "screenshot_scope": summary.get("screenshot_scope", ""),
            "timestamp": summary.get("timestamp", ""),
            "artifact_available": artifact_available,
            "artifact_path": artifact_path,
            "artifact_type": artifact_type,
            "availability_state": "available" if artifact_available else ("pruned" if artifact_path else "unavailable"),
        }
    )


def _desktop_visual_goal(prompt_text: str) -> bool:
    lowered = str(prompt_text or "").strip().lower()
    if not lowered:
        return False
    phrases = (
        "what do you see",
        "look like",
        "looks like",
        "what is on",
        "what's on",
        "button",
        "field label",
        "entry label",
        "icon",
        "dialog",
        "color",
        "theme",
        "appearance",
        "visual",
        "screen",
        "screenshot",
        "image",
        "read the",
        "text on",
        "visible text",
    )
    return any(phrase in lowered for phrase in phrases)


def _desktop_changed_state_goal(prompt_text: str) -> bool:
    lowered = str(prompt_text or "").strip().lower()
    if not lowered:
        return False
    phrases = (
        "changed",
        "different",
        "before",
        "after",
        "compare",
        "what happened",
        "loading",
        "unstable",
        "settling",
        "stabilizing",
    )
    return any(phrase in lowered for phrase in phrases)


def select_desktop_vision_context(
    *,
    selected_summary: Dict[str, Any] | None = None,
    checkpoint_summary: Dict[str, Any] | None = None,
    recent_summaries: Iterable[Dict[str, Any]] | None = None,
    purpose: str = "desktop_investigation",
    prompt_text: str = "",
    assessment: Dict[str, Any] | None = None,
    checkpoint_assessment: Dict[str, Any] | None = None,
    prefer_before_after: bool = False,
) -> Dict[str, Any]:
    selected = normalize_desktop_evidence_summary(selected_summary if isinstance(selected_summary, dict) else {})
    checkpoint = normalize_desktop_evidence_summary(checkpoint_summary if isinstance(checkpoint_summary, dict) else {})
    assessment = normalize_desktop_evidence_assessment(assessment if isinstance(assessment, dict) else {})
    checkpoint_assessment = normalize_desktop_evidence_assessment(checkpoint_assessment if isinstance(checkpoint_assessment, dict) else {})
    recents = [
        normalize_desktop_evidence_summary(item)
        for item in list(recent_summaries or [])
        if isinstance(item, dict)
    ]
    prompt_lower = str(prompt_text or "").strip().lower()
    purpose_text = _trim_text(purpose or "desktop_investigation", limit=60).lower() or "desktop_investigation"

    def _artifact_summary(summary: Dict[str, Any]) -> bool:
        return bool(summary.get("has_artifact", False) and _artifact_available_from_summary(summary))

    primary = checkpoint if purpose_text == "desktop_approval" and checkpoint.get("evidence_id") else selected
    if not primary.get("evidence_id") and checkpoint.get("evidence_id"):
        primary = checkpoint
    if not primary.get("evidence_id"):
        for item in recents:
            if item.get("evidence_id"):
                primary = item
                break

    visual_goal = _desktop_visual_goal(prompt_lower)
    changed_state_goal = prefer_before_after or _desktop_changed_state_goal(prompt_lower)
    image_required = False
    reason = "summary_only"
    summary_text = "Compact desktop evidence summaries were sufficient for this desktop turn."

    if purpose_text in {"desktop_approval", "desktop_action_prepare"}:
        if _artifact_summary(checkpoint if checkpoint.get("evidence_id") else primary):
            image_required = True
            reason = "direct_image_needed"
            summary_text = "Attached the most relevant screenshot-backed desktop evidence to ground the pending bounded desktop action."
            primary = checkpoint if checkpoint.get("evidence_id") else primary
    elif visual_goal and _artifact_summary(primary):
        image_required = True
        reason = "direct_image_needed"
        summary_text = "Attached the most relevant current desktop screenshot because the request depends on visible UI details."
    elif changed_state_goal and _artifact_summary(primary):
        image_required = True
        reason = "direct_image_needed"
        summary_text = "Attached the most relevant screenshot-backed desktop evidence because the request depends on changed desktop state."
    elif assessment.get("reason") in {"partial_but_answerable", "partial_evidence"} and _artifact_summary(primary):
        image_required = True
        reason = "direct_image_needed"
        summary_text = "Attached the most relevant screenshot because compact metadata alone was only partially conclusive."

    images: List[Dict[str, Any]] = []
    primary_reason = "checkpoint" if primary.get("evidence_id") == checkpoint.get("evidence_id") and checkpoint.get("evidence_id") else "selected"
    if image_required and _artifact_summary(primary):
        role = "checkpoint" if primary_reason == "checkpoint" else "current"
        images.append(_vision_image_from_summary(primary, role=role, selection_reason=primary_reason))

    if changed_state_goal and images:
        comparison_candidates = []
        primary_id = str(primary.get("evidence_id", "")).strip()
        primary_title = str(primary.get("active_window_title", "")).strip().lower()
        for item in recents:
            evidence_id = str(item.get("evidence_id", "")).strip()
            if not evidence_id or evidence_id == primary_id or not _artifact_summary(item):
                continue
            title = str(item.get("active_window_title", "")).strip().lower()
            score = _importance_rank(item)
            if primary_title and title and (primary_title in title or title in primary_title):
                score += 4
            comparison_candidates.append(
                (
                    score,
                    -int(item.get("recency_seconds", 0) or 0),
                    item,
                )
            )
        comparison_candidates.sort(reverse=True)
        if comparison_candidates:
            before_item = comparison_candidates[0][2]
            images = [
                _vision_image_from_summary(before_item, role="before", selection_reason="recent_context"),
                _vision_image_from_summary(primary, role="after", selection_reason=primary_reason),
            ]
            reason = "image_pair_selected"
            summary_text = "Attached a bounded before/after screenshot pair because the desktop turn depends on changed visual state."

    mode = "summary_only"
    if len(images) >= 2:
        mode = "before_after_pair"
    elif images:
        mode = "single_image"

    if purpose_text == "desktop_approval" and checkpoint_assessment.get("summary") and not images:
        summary_text = checkpoint_assessment.get("summary", "") or summary_text
    elif assessment.get("summary") and not images:
        summary_text = assessment.get("summary", "") or summary_text

    return normalize_desktop_vision_context(
        {
            "purpose": purpose_text,
            "mode": mode,
            "needs_direct_image": bool(images),
            "reason": reason if images else "summary_only",
            "summary": summary_text,
            "image_count": len(images),
            "primary_evidence_id": primary.get("evidence_id", ""),
            "comparison_evidence_id": images[0].get("evidence_id", "") if len(images) >= 2 else "",
            "images": images,
        }
    )


def _title_match_score(summary: Dict[str, Any], text: str) -> int:
    query = str(text or "").strip()
    if not query:
        return 0
    active_match = describe_title_match(query, summary.get("active_window_title", ""), exact=False)
    target_match = describe_title_match(query, summary.get("target_window_title", ""), exact=False)
    return max(int(active_match.get("score", 0) or 0), int(target_match.get("score", 0) or 0))


def _rank_recent_summaries(summaries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items = [normalize_desktop_evidence_summary(item) for item in summaries if isinstance(item, dict)]
    items.sort(
        key=lambda item: (
            -int(item.get("recency_seconds", 0) or 0),
            item.get("timestamp", ""),
            item.get("evidence_id", ""),
        )
    )
    return items


def _recent_first_summaries(summaries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        [normalize_desktop_evidence_summary(item) for item in summaries if isinstance(item, dict)],
        key=lambda item: (
            int(item.get("recency_seconds", 0) or 0),
            item.get("timestamp", ""),
            item.get("evidence_id", ""),
        ),
    )


def select_recent_evidence(
    summaries: Iterable[Dict[str, Any]],
    *,
    strategy: str = "latest",
    evidence_id: str = "",
    observation_token: str = "",
    active_window_title: str = "",
    target_window_title: str = "",
) -> Dict[str, Any]:
    items = _recent_first_summaries(summaries)
    if not items:
        return {"strategy": strategy, "reason": "no_match", "selected": {}, "candidate_count": 0}

    lookup_id = _trim_text(evidence_id, limit=80)
    if lookup_id:
        for item in items:
            if item.get("evidence_id") == lookup_id:
                selected = dict(item)
                selected["selection_reason"] = "linked"
                return {"strategy": strategy, "reason": "linked", "selected": normalize_desktop_evidence_summary(selected), "candidate_count": len(items)}
        return {"strategy": strategy, "reason": "no_match", "selected": {}, "candidate_count": len(items)}

    lookup_token = _trim_text(observation_token, limit=120)
    if lookup_token:
        for item in items:
            if item.get("observation_token") == lookup_token:
                selected = dict(item)
                selected["selection_reason"] = "linked"
                return {"strategy": strategy, "reason": "linked", "selected": normalize_desktop_evidence_summary(selected), "candidate_count": len(items)}

    strategy_text = _trim_text(strategy or "latest", limit=80).lower()
    selected_item: Dict[str, Any] = {}
    selection_reason = "selected"

    if strategy_text == "latest_with_screenshot":
        for item in items:
            if item.get("has_screenshot"):
                selected_item = item
                selection_reason = "matched"
                break
    elif strategy_text == "latest_partial":
        for item in items:
            if item.get("is_partial"):
                selected_item = item
                selection_reason = "matched"
                break
    elif strategy_text == "latest_full":
        for item in items:
            if not item.get("is_partial"):
                selected_item = item
                selection_reason = "matched"
                break
    elif strategy_text in {"window_title", "active_window_title"}:
        scored = [
            (item, max(_title_match_score(item, active_window_title), _title_match_score(item, target_window_title)))
            for item in items
        ]
        scored = [entry for entry in scored if entry[1] >= WINDOW_MATCH_THRESHOLD]
        if scored:
            scored.sort(
                key=lambda entry: (
                    -entry[1],
                    0 if entry[0].get("has_screenshot") else 1,
                    int(entry[0].get("recency_seconds", 0) or 0),
                )
            )
            selected_item = scored[0][0]
            selection_reason = "matched"

    if not selected_item:
        selected_item = items[0]

    selected = dict(selected_item)
    selected["selection_reason"] = selection_reason
    return {
        "strategy": strategy_text or "latest",
        "reason": selection_reason,
        "selected": normalize_desktop_evidence_summary(selected),
        "candidate_count": len(items),
    }


def select_checkpoint_evidence(
    summaries: Iterable[Dict[str, Any]],
    *,
    checkpoint_evidence_id: str = "",
    checkpoint_target: str = "",
    active_window_title: str = "",
) -> Dict[str, Any]:
    if _trim_text(checkpoint_evidence_id, limit=80):
        return select_recent_evidence(summaries, strategy="checkpoint", evidence_id=checkpoint_evidence_id)

    window_title = _trim_text(checkpoint_target, limit=180) or _trim_text(active_window_title, limit=180)
    if window_title:
        result = select_recent_evidence(
            summaries,
            strategy="window_title",
            active_window_title=window_title,
            target_window_title=window_title,
        )
        if result.get("selected"):
            return result
    return select_recent_evidence(summaries, strategy="latest_with_screenshot")


def select_task_evidence(
    summaries: Iterable[Dict[str, Any]],
    *,
    task_evidence_id: str = "",
    observation_token: str = "",
    active_window_title: str = "",
    target_window_title: str = "",
) -> Dict[str, Any]:
    if _trim_text(task_evidence_id, limit=80):
        return select_recent_evidence(summaries, strategy="task", evidence_id=task_evidence_id)
    if _trim_text(observation_token, limit=120):
        result = select_recent_evidence(summaries, strategy="task", observation_token=observation_token)
        if result.get("selected"):
            return result
    if _trim_text(active_window_title, limit=180) or _trim_text(target_window_title, limit=180):
        result = select_recent_evidence(
            summaries,
            strategy="window_title",
            active_window_title=active_window_title,
            target_window_title=target_window_title,
        )
        if result.get("selected"):
            return result
    return select_recent_evidence(summaries, strategy="latest")


def collect_display_metadata(virtual_screen: Dict[str, Any]) -> Dict[str, Any]:
    monitors: List[Dict[str, Any]] = []
    backend = "native"
    if mss is not None:
        try:
            with mss.mss() as capture:
                backend = "mss"
                for monitor in list(capture.monitors[1:])[:8]:
                    if not isinstance(monitor, dict):
                        continue
                    monitors.append(
                        {
                            "left": _coerce_int(monitor.get("left", 0), 0, minimum=-100_000, maximum=100_000),
                            "top": _coerce_int(monitor.get("top", 0), 0, minimum=-100_000, maximum=100_000),
                            "width": _coerce_int(monitor.get("width", 0), 0, minimum=0, maximum=100_000),
                            "height": _coerce_int(monitor.get("height", 0), 0, minimum=0, maximum=100_000),
                        }
                    )
        except Exception:
            monitors = []
            backend = "native"
    return normalize_screen_observation(
        virtual_screen=virtual_screen,
        monitors=monitors,
        backend=backend,
        reason="inspected",
    )


def build_desktop_evidence_bundle(
    *,
    source_action: str,
    active_window: Dict[str, Any],
    windows: Iterable[Dict[str, Any]],
    observation_token: str = "",
    screenshot: Dict[str, Any] | None = None,
    ui_evidence: Dict[str, Any] | None = None,
    target_window: Dict[str, Any] | None = None,
    screen: Dict[str, Any] | None = None,
    errors: Iterable[str] | None = None,
    capture_mode: str = "",
    importance: str = "",
    importance_reason: str = "",
    state_scope_id: str = "",
    task_id: str = "",
    task_status: str = "",
    checkpoint_pending: bool = False,
    checkpoint_tool: str = "",
    checkpoint_target: str = "",
    capture_signature: str = "",
) -> Dict[str, Any]:
    normalized_screenshot = screenshot if isinstance(screenshot, dict) else {}
    normalized_ui = ui_evidence if isinstance(ui_evidence, dict) else {}
    error_items = [_trim_text(item, limit=220) for item in list(errors or [])[:6] if str(item).strip()]
    screenshot_path = str(normalized_screenshot.get("path", "")).strip()
    ui_controls = normalized_ui.get("controls", []) if isinstance(normalized_ui.get("controls", []), list) else []
    reason = "collected"
    if not screenshot_path or error_items or (normalized_ui and not ui_controls and str(normalized_ui.get("reason", "")).strip() not in {"", "inspected"}):
        reason = "partial"
    bundle = _sanitize_bundle(
        {
            "evidence_id": "",
            "timestamp": _iso_timestamp(),
            "reason": reason,
            "summary": "",
            "source_action": source_action,
            "observation_token": observation_token,
            "active_window": active_window,
            "target_window": target_window or {},
            "windows": list(windows)[:12],
            "screen": screen or {},
            "screenshot": normalized_screenshot,
            "ui_evidence": normalized_ui,
            "window_backend": str(active_window.get("backend", "")).strip(),
            "screenshot_backend": str(normalized_screenshot.get("backend", "")).strip(),
            "ui_evidence_backend": str(normalized_ui.get("backend", "")).strip(),
            "capture_mode": capture_mode,
            "importance": importance,
            "importance_reason": importance_reason,
            "state_scope_id": state_scope_id,
            "task_id": task_id,
            "task_status": task_status,
            "checkpoint_pending": bool(checkpoint_pending),
            "checkpoint_tool": checkpoint_tool,
            "checkpoint_target": checkpoint_target,
            "capture_signature": capture_signature,
            "errors": error_items,
        }
    )
    return bundle


def evidence_collection_result(
    bundle: Dict[str, Any],
    *,
    ok: bool,
    message: str = "",
    error: str = "",
) -> Dict[str, Any]:
    normalized_bundle = _sanitize_bundle(bundle)
    return result_envelope(
        "desktop_evidence_bundle",
        ok=ok,
        backend="desktop_evidence",
        reason=str(normalized_bundle.get("reason", "partial")),
        message=message or normalized_bundle.get("summary", ""),
        error=error,
        data={"bundle": normalized_bundle, "reference": bundle_ref(normalized_bundle)},
    )


class DesktopEvidenceStore:
    def __init__(self, root: str | Path, *, max_items: int = DEFAULT_MAX_DESKTOP_EVIDENCE_ITEMS):
        self.root = Path(root)
        self.max_items = max(1, int(max_items))
        self.bundles_dir = self.root / "bundles"
        self.captures_dir = self.root / "captures"
        self.index_path = self.root / "index.json"
        self._lock = threading.RLock()

    def _read_index(self) -> Dict[str, Any]:
        if not self.index_path.exists():
            return {"bundles": []}
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return {"bundles": []}
        if not isinstance(payload, dict):
            return {"bundles": []}
        bundles = payload.get("bundles", [])
        normalized: List[Dict[str, Any]] = []
        for item in bundles:
            if not isinstance(item, dict):
                continue
            if item.get("evidence_kind") or item.get("has_screenshot") is not None and "summary" in item and "window_count" in item:
                normalized.append(normalize_desktop_evidence_summary(item))
                continue
            evidence_id = _trim_text(item.get("evidence_id", ""), limit=80)
            if evidence_id:
                bundle = self.load_bundle(evidence_id)
                if bundle:
                    normalized.append(summarize_evidence_bundle(bundle))
                    continue
            normalized.append(normalize_desktop_evidence_summary(item))
        return {"bundles": normalized}

    def _write_index(self, refs: List[Dict[str, Any]]):
        payload = {
            "version": 1,
            "updated_at": _iso_timestamp(),
            "bundles": [normalize_desktop_evidence_summary(item) for item in refs],
        }
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def next_evidence_id(self) -> str:
        return f"desk-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"

    def artifact_path(self, evidence_id: str, *, extension: str = ".png") -> Path:
        suffix = str(extension or ".png").strip() or ".png"
        if not suffix.startswith("."):
            suffix = f".{suffix}"
        self.captures_dir.mkdir(parents=True, exist_ok=True)
        return self.captures_dir / f"{_trim_text(evidence_id, limit=80)}{suffix}"

    def bundle_path(self, evidence_id: str) -> Path:
        self.bundles_dir.mkdir(parents=True, exist_ok=True)
        return self.bundles_dir / f"{_trim_text(evidence_id, limit=80)}.json"

    def record_bundle(self, bundle: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            evidence_id = _trim_text(bundle.get("evidence_id", ""), limit=80) or self.next_evidence_id()
            bundle_copy = dict(bundle)
            bundle_copy["evidence_id"] = evidence_id
            bundle_copy["bundle_path"] = str(self.bundle_path(evidence_id))
            if isinstance(bundle_copy.get("artifacts", {}), dict):
                artifacts = dict(bundle_copy.get("artifacts", {}))
                artifacts["bundle_path"] = bundle_copy["bundle_path"]
                bundle_copy["artifacts"] = artifacts
            normalized = _sanitize_bundle(bundle_copy)
            bundle_file = self.bundle_path(evidence_id)
            self.root.mkdir(parents=True, exist_ok=True)
            bundle_file.write_text(json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8")

            summary = summarize_evidence_bundle(normalized)
            refs = [item for item in self._read_index().get("bundles", []) if item.get("evidence_id") != evidence_id]
            refs.append(summary)
            refs = self._retain_recent_refs(refs)
            self._write_index(refs)
            self._prune_locked(refs)
            return normalize_desktop_evidence_ref(refs[-1] if refs else {})

    def _retain_recent_refs(self, refs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized_refs = [normalize_desktop_evidence_summary(item) for item in refs if isinstance(item, dict)]
        if len(normalized_refs) <= self.max_items:
            return normalized_refs

        pinned = [item for item in normalized_refs if _importance_rank(item) >= 2]
        unpinned = [item for item in normalized_refs if _importance_rank(item) < 2]

        kept: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()

        for source in (pinned[-self.max_items :], unpinned[-self.max_items :]):
            for item in source:
                evidence_id = _trim_text(item.get("evidence_id", ""), limit=80)
                if not evidence_id or evidence_id in seen_ids:
                    continue
                kept.append(item)
                seen_ids.add(evidence_id)
                if len(kept) >= self.max_items:
                    break
            if len(kept) >= self.max_items:
                break

        kept_ids = {_trim_text(item.get("evidence_id", ""), limit=80) for item in kept}
        return [item for item in normalized_refs if _trim_text(item.get("evidence_id", ""), limit=80) in kept_ids][-self.max_items :]

    def _prune_locked(self, refs: List[Dict[str, Any]]):
        keep_bundle_names = {Path(item.get("bundle_path", "")).name for item in refs if item.get("bundle_path")}
        keep_capture_names = {Path(item.get("screenshot_path", "")).name for item in refs if item.get("screenshot_path")}
        if self.bundles_dir.exists():
            for file in self.bundles_dir.iterdir():
                if not file.is_file():
                    continue
                if file.name not in keep_bundle_names:
                    try:
                        file.unlink()
                    except Exception:
                        pass
        if self.captures_dir.exists():
            for file in self.captures_dir.iterdir():
                if not file.is_file():
                    continue
                if file.name not in keep_capture_names:
                    try:
                        file.unlink()
                    except Exception:
                        pass

    def load_bundle(self, evidence_id: str) -> Dict[str, Any]:
        lookup = _trim_text(evidence_id, limit=80)
        if not lookup:
            return {}
        path = self.bundle_path(lookup)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        return _sanitize_bundle(payload)

    def recent_refs(self, limit: int = 8) -> List[Dict[str, Any]]:
        refs = self._read_index().get("bundles", [])
        return [normalize_desktop_evidence_ref(item) for item in refs[-max(1, int(limit or 1)) :]]

    def recent_summaries(self, limit: int = 8) -> List[Dict[str, Any]]:
        summaries = self._read_index().get("bundles", [])
        safe_limit = max(1, int(limit or 1))
        items = summaries[-safe_limit:]
        return [normalize_desktop_evidence_summary(item) for item in items]

    def recent_context_summaries(
        self,
        *,
        limit: int = 4,
        state_scope_id: str = "",
        task_id: str = "",
        active_window_title: str = "",
        checkpoint_target: str = "",
    ) -> List[Dict[str, Any]]:
        summaries = list(self.recent_summaries(limit=self.max_items))
        if not summaries:
            return []

        scope_lookup = _trim_text(state_scope_id, limit=120)
        task_lookup = _trim_text(task_id, limit=60)
        title_lookup = _trim_text(active_window_title, limit=180).lower()
        checkpoint_lookup = _trim_text(checkpoint_target, limit=180).lower()

        scored: List[tuple[tuple[int, int, int], Dict[str, Any]]] = []
        for item in summaries:
            score = 0
            if scope_lookup and _trim_text(item.get("state_scope_id", ""), limit=120) == scope_lookup:
                score += 6
            if task_lookup and _trim_text(item.get("task_id", ""), limit=60) == task_lookup:
                score += 8
            active_title = _trim_text(item.get("active_window_title", ""), limit=180).lower()
            target_title = _trim_text(item.get("target_window_title", ""), limit=180).lower()
            if checkpoint_lookup and checkpoint_lookup in " ".join([active_title, target_title]):
                score += 5
            if title_lookup and title_lookup in " ".join([active_title, target_title]):
                score += 3
            if bool(item.get("checkpoint_pending", False)):
                score += 4
            if bool(item.get("has_screenshot", False)):
                score += 2
            score += _importance_rank(item)
            if score <= 0:
                continue
            scored.append(
                (
                    (
                        score,
                        -int(item.get("recency_seconds", 0) or 0),
                        1 if bool(item.get("has_screenshot", False)) else 0,
                    ),
                    item,
                )
            )

        scored.sort(reverse=True, key=lambda item: item[0])
        selected: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        for _score, item in scored:
            evidence_id = _trim_text(item.get("evidence_id", ""), limit=80)
            if not evidence_id or evidence_id in seen_ids:
                continue
            seen_ids.add(evidence_id)
            selected.append(compact_evidence_preview(item))
            if len(selected) >= max(1, int(limit or 1)):
                break
        return selected

    def summary_for(self, evidence_id: str, *, now: datetime | None = None) -> Dict[str, Any]:
        lookup = _trim_text(evidence_id, limit=80)
        if not lookup:
            return {}
        for item in reversed(self._read_index().get("bundles", [])):
            if item.get("evidence_id") == lookup:
                return normalize_desktop_evidence_summary(item)
        bundle = self.load_bundle(lookup)
        if not bundle:
            return {}
        return summarize_evidence_bundle(bundle, now=now)

    def select_summary(
        self,
        *,
        strategy: str = "latest",
        evidence_id: str = "",
        observation_token: str = "",
        active_window_title: str = "",
        target_window_title: str = "",
        checkpoint_evidence_id: str = "",
        checkpoint_target: str = "",
        task_evidence_id: str = "",
    ) -> Dict[str, Any]:
        recent = self.recent_summaries(limit=self.max_items)
        if _trim_text(checkpoint_evidence_id, limit=80) or _trim_text(checkpoint_target, limit=180):
            return select_checkpoint_evidence(
                recent,
                checkpoint_evidence_id=checkpoint_evidence_id,
                checkpoint_target=checkpoint_target,
                active_window_title=active_window_title,
            )
        if _trim_text(task_evidence_id, limit=80) or _trim_text(observation_token, limit=120):
            return select_task_evidence(
                recent,
                task_evidence_id=task_evidence_id,
                observation_token=observation_token,
                active_window_title=active_window_title,
                target_window_title=target_window_title,
            )
        return select_recent_evidence(
            recent,
            strategy=strategy,
            evidence_id=evidence_id,
            observation_token=observation_token,
            active_window_title=active_window_title,
            target_window_title=target_window_title,
        )

    def select_vision_context(
        self,
        *,
        selected_summary: Dict[str, Any] | None = None,
        checkpoint_summary: Dict[str, Any] | None = None,
        recent_summaries: Iterable[Dict[str, Any]] | None = None,
        purpose: str = "desktop_investigation",
        prompt_text: str = "",
        assessment: Dict[str, Any] | None = None,
        checkpoint_assessment: Dict[str, Any] | None = None,
        prefer_before_after: bool = False,
    ) -> Dict[str, Any]:
        return select_desktop_vision_context(
            selected_summary=selected_summary,
            checkpoint_summary=checkpoint_summary,
            recent_summaries=recent_summaries,
            purpose=purpose,
            prompt_text=prompt_text,
            assessment=assessment,
            checkpoint_assessment=checkpoint_assessment,
            prefer_before_after=prefer_before_after,
        )

    def assess_summary(
        self,
        *,
        summary: Dict[str, Any] | None = None,
        evidence_id: str = "",
        purpose: str = "desktop_investigation",
        target_window_title: str = "",
        require_screenshot: bool = False,
        max_age_seconds: int = 180,
    ) -> Dict[str, Any]:
        resolved_summary = summary if isinstance(summary, dict) else {}
        if not resolved_summary and _trim_text(evidence_id, limit=80):
            resolved_summary = self.summary_for(evidence_id)
        return assess_desktop_evidence(
            resolved_summary,
            purpose=purpose,
            target_window_title=target_window_title,
            require_screenshot=require_screenshot,
            max_age_seconds=max_age_seconds,
        )

    def artifact_metadata(self, evidence_id: str, *, content_path: str = "") -> Dict[str, Any]:
        lookup = _trim_text(evidence_id, limit=80)
        if not lookup:
            return describe_evidence_artifact({}, content_path=content_path, evidence_id="")
        bundle = self.load_bundle(lookup)
        summary = self.summary_for(lookup)
        return describe_evidence_artifact(bundle, summary=summary, content_path=content_path, evidence_id=lookup)

    def artifact_file_path(self, evidence_id: str) -> Path | None:
        metadata = self.artifact_metadata(evidence_id)
        if not metadata.get("artifact_available", False):
            return None
        candidate = Path(str(metadata.get("artifact_path", "")).strip())
        if not candidate.exists() or not candidate.is_file():
            return None
        return candidate

    def find_by_observation_token(self, token: str) -> Dict[str, Any]:
        lookup = _trim_text(token, limit=120)
        if not lookup:
            return {}
        for ref in reversed(self._read_index().get("bundles", [])):
            if ref.get("observation_token") == lookup:
                return normalize_desktop_evidence_ref(ref)
        return {}

    def status_snapshot(self) -> Dict[str, Any]:
        refs = self._read_index().get("bundles", [])
        important_count = sum(1 for item in refs if _importance_rank(item) >= 2)
        auto_count = sum(1 for item in refs if _normalize_capture_mode(item.get("capture_mode", "")) == "auto")
        return {
            "root": str(self.root),
            "bundle_count": len(refs),
            "max_items": self.max_items,
            "important_count": important_count,
            "auto_capture_count": auto_count,
            "latest": normalize_desktop_evidence_ref(refs[-1] if refs else {}),
            "latest_summary": compact_evidence_preview(refs[-1] if refs else {}),
        }


def get_desktop_evidence_store(settings: Dict[str, Any] | None = None) -> DesktopEvidenceStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is not None:
            return _STORE
        source_settings = settings if isinstance(settings, dict) else load_settings()
        root = source_settings.get("desktop_evidence_root", DEFAULT_DESKTOP_EVIDENCE_ROOT)
        max_items = _coerce_int(
            source_settings.get("max_desktop_evidence_entries", DEFAULT_MAX_DESKTOP_EVIDENCE_ITEMS),
            DEFAULT_MAX_DESKTOP_EVIDENCE_ITEMS,
            minimum=4,
            maximum=256,
        )
        _STORE = DesktopEvidenceStore(root, max_items=max_items)
        return _STORE


def reset_desktop_evidence_store(settings: Dict[str, Any] | None = None) -> DesktopEvidenceStore:
    global _STORE
    with _STORE_LOCK:
        _STORE = None
    return get_desktop_evidence_store(settings=settings)
