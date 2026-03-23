from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List


BACKEND_REASON_CODES = {
    "ok",
    "available",
    "active",
    "scheduled",
    "triggered",
    "collected",
    "partial",
    "retained",
    "pruned",
    "filesystem_event",
    "state_changed",
    "captured",
    "inspected",
    "focused",
    "fallback",
    "fallback_active",
    "unavailable",
    "missing_dependency",
    "disabled",
    "unsupported",
    "invalid_input",
    "not_found",
    "stale",
    "missing_artifact",
    "error",
}


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


def _normalize_reason(reason: Any, default: str = "ok") -> str:
    text = str(reason or "").strip().lower().replace(" ", "_")
    if text in BACKEND_REASON_CODES:
        return text
    fallback = str(default or "ok").strip().lower().replace(" ", "_")
    return fallback if fallback in BACKEND_REASON_CODES else "ok"


def _sanitize_metadata(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    sanitized: Dict[str, Any] = {}
    for key, raw_value in value.items():
        normalized_key = _trim_text(key, limit=80)
        if not normalized_key:
            continue
        if isinstance(raw_value, dict):
            sanitized[normalized_key] = _sanitize_metadata(raw_value)
        elif isinstance(raw_value, list):
            items: List[Any] = []
            for item in raw_value[:16]:
                if isinstance(item, dict):
                    items.append(_sanitize_metadata(item))
                else:
                    items.append(_trim_text(item, limit=180))
            sanitized[normalized_key] = items
        elif isinstance(raw_value, Path):
            sanitized[normalized_key] = str(raw_value)
        elif isinstance(raw_value, (bool, int, float)):
            sanitized[normalized_key] = raw_value
        elif raw_value is None:
            sanitized[normalized_key] = ""
        else:
            sanitized[normalized_key] = _trim_text(raw_value, limit=240)
    return sanitized


def result_envelope(
    kind: str,
    *,
    ok: bool,
    backend: str,
    reason: str,
    message: str = "",
    error: str = "",
    metadata: Dict[str, Any] | None = None,
    data: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "ok": bool(ok),
        "kind": _trim_text(kind, limit=80),
        "backend": _trim_text(backend, limit=60),
        "reason": _normalize_reason(reason),
        "message": _trim_text(message, limit=320),
        "error": _trim_text(error, limit=320),
        "timestamp": _iso_timestamp(),
        "metadata": _sanitize_metadata(metadata or {}),
        "data": _sanitize_metadata(data or {}),
    }


def backend_status(
    name: str,
    *,
    preferred: str,
    active: str,
    available: bool,
    reason: str,
    message: str = "",
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "name": _trim_text(name, limit=60),
        "preferred": _trim_text(preferred, limit=60),
        "active": _trim_text(active, limit=60),
        "available": bool(available),
        "reason": _normalize_reason(reason, default="available" if available else "unavailable"),
        "message": _trim_text(message, limit=240),
        "metadata": _sanitize_metadata(metadata or {}),
        "timestamp": _iso_timestamp(),
    }


def normalize_scheduler_job(job: Dict[str, Any], *, backend: str, reason: str = "scheduled") -> Dict[str, Any]:
    return {
        "scheduled_id": _trim_text(job.get("scheduled_id", ""), limit=60),
        "goal": _trim_text(job.get("goal", ""), limit=220),
        "status": _trim_text(job.get("status", ""), limit=40),
        "recurrence": _trim_text(job.get("recurrence", ""), limit=40),
        "scheduled_for": _trim_text(job.get("scheduled_for", ""), limit=40),
        "next_run_at": _trim_text(job.get("next_run_at", ""), limit=40),
        "backend": _trim_text(backend, limit=60),
        "reason": _normalize_reason(reason, default="scheduled"),
    }


def normalize_file_watch_event(
    *,
    backend: str,
    event_type: str,
    src_path: str,
    dest_path: str = "",
    target_path: str = "",
    is_directory: bool = False,
    reason: str = "filesystem_event",
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "event_type": _trim_text(event_type, limit=60),
        "src_path": _trim_text(src_path, limit=320),
        "dest_path": _trim_text(dest_path, limit=320),
        "target_path": _trim_text(target_path, limit=320),
        "is_directory": bool(is_directory),
        "backend": _trim_text(backend, limit=60),
        "reason": _normalize_reason(reason, default="filesystem_event"),
        "timestamp": _iso_timestamp(),
        "metadata": _sanitize_metadata(metadata or {}),
    }


def normalize_window_descriptor(window: Dict[str, Any], *, backend: str, reason: str = "inspected") -> Dict[str, Any]:
    rect = window.get("rect", {}) if isinstance(window.get("rect", {}), dict) else {}
    return {
        "window_id": _trim_text(window.get("window_id", ""), limit=40),
        "title": _trim_text(window.get("title", ""), limit=180),
        "class_name": _trim_text(window.get("class_name", ""), limit=120),
        "pid": _coerce_int(window.get("pid", 0), 0, minimum=0, maximum=10_000_000),
        "process_name": _trim_text(window.get("process_name", ""), limit=120),
        "rect": {
            "x": _coerce_int(rect.get("x", 0), 0, minimum=-100_000, maximum=100_000),
            "y": _coerce_int(rect.get("y", 0), 0, minimum=-100_000, maximum=100_000),
            "width": _coerce_int(rect.get("width", 0), 0, minimum=0, maximum=100_000),
            "height": _coerce_int(rect.get("height", 0), 0, minimum=0, maximum=100_000),
        },
        "is_active": _coerce_bool(window.get("is_active", False), False),
        "is_visible": _coerce_bool(window.get("is_visible", False), False),
        "is_minimized": _coerce_bool(window.get("is_minimized", False), False),
        "backend": _trim_text(backend, limit=60),
        "reason": _normalize_reason(reason, default="inspected"),
    }


def normalize_screenshot_observation(
    *,
    backend: str,
    path: str,
    scope: str,
    bounds: Dict[str, Any],
    active_window_title: str = "",
    reason: str = "captured",
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    rect = bounds if isinstance(bounds, dict) else {}
    return {
        "path": _trim_text(path, limit=320),
        "scope": _trim_text(scope, limit=60),
        "bounds": {
            "x": _coerce_int(rect.get("x", 0), 0, minimum=-100_000, maximum=100_000),
            "y": _coerce_int(rect.get("y", 0), 0, minimum=-100_000, maximum=100_000),
            "width": _coerce_int(rect.get("width", 0), 0, minimum=0, maximum=100_000),
            "height": _coerce_int(rect.get("height", 0), 0, minimum=0, maximum=100_000),
        },
        "active_window_title": _trim_text(active_window_title, limit=180),
        "backend": _trim_text(backend, limit=60),
        "reason": _normalize_reason(reason, default="captured"),
        "timestamp": _iso_timestamp(),
        "metadata": _sanitize_metadata(metadata or {}),
    }


def normalize_ui_evidence_observation(
    *,
    backend: str,
    target: str,
    controls: Iterable[Dict[str, Any]] | None = None,
    reason: str = "inspected",
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    normalized_controls: List[Dict[str, Any]] = []
    for item in list(controls or [])[:12]:
        if not isinstance(item, dict):
            continue
        normalized_controls.append(
            {
                "name": _trim_text(item.get("name", ""), limit=160),
                "control_type": _trim_text(item.get("control_type", ""), limit=80),
                "automation_id": _trim_text(item.get("automation_id", ""), limit=120),
                "text": _trim_text(item.get("text", ""), limit=220),
            }
        )
    return {
        "target": _trim_text(target, limit=220),
        "controls": normalized_controls,
        "backend": _trim_text(backend, limit=60),
        "reason": _normalize_reason(reason, default="inspected"),
        "timestamp": _iso_timestamp(),
        "metadata": _sanitize_metadata(metadata or {}),
    }


def normalize_screen_observation(
    *,
    virtual_screen: Dict[str, Any] | None = None,
    monitors: Iterable[Dict[str, Any]] | None = None,
    backend: str = "",
    reason: str = "inspected",
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    rect = virtual_screen if isinstance(virtual_screen, dict) else {}
    normalized_monitors: List[Dict[str, Any]] = []
    for item in list(monitors or [])[:8]:
        if not isinstance(item, dict):
            continue
        normalized_monitors.append(
            {
                "left": _coerce_int(item.get("left", 0), 0, minimum=-100_000, maximum=100_000),
                "top": _coerce_int(item.get("top", 0), 0, minimum=-100_000, maximum=100_000),
                "width": _coerce_int(item.get("width", 0), 0, minimum=0, maximum=100_000),
                "height": _coerce_int(item.get("height", 0), 0, minimum=0, maximum=100_000),
            }
        )
    return {
        "virtual_screen": {
            "x": _coerce_int(rect.get("x", 0), 0, minimum=-100_000, maximum=100_000),
            "y": _coerce_int(rect.get("y", 0), 0, minimum=-100_000, maximum=100_000),
            "width": _coerce_int(rect.get("width", 0), 0, minimum=0, maximum=100_000),
            "height": _coerce_int(rect.get("height", 0), 0, minimum=0, maximum=100_000),
        },
        "monitor_count": len(normalized_monitors),
        "monitors": normalized_monitors,
        "backend": _trim_text(backend, limit=60),
        "reason": _normalize_reason(reason, default="inspected"),
        "metadata": _sanitize_metadata(metadata or {}),
    }


def normalize_desktop_evidence_ref(value: Dict[str, Any] | None) -> Dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    return {
        "evidence_id": _trim_text(value.get("evidence_id", ""), limit=80),
        "timestamp": _trim_text(value.get("timestamp", ""), limit=40),
        "reason": _normalize_reason(value.get("reason", "collected"), default="collected"),
        "summary": _trim_text(value.get("summary", ""), limit=240),
        "bundle_path": _trim_text(value.get("bundle_path", ""), limit=320),
        "screenshot_path": _trim_text(value.get("screenshot_path", ""), limit=320),
        "observation_token": _trim_text(value.get("observation_token", ""), limit=120),
        "active_window_title": _trim_text(value.get("active_window_title", ""), limit=180),
        "backend": _trim_text(value.get("backend", ""), limit=120),
    }
