from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from core.state import TaskState
from tools.browser import browser_inspect_page
from tools.files import inspect_project


DEFAULT_WATCH_STATE_PATH = "data/watch_state.json"
DEFAULT_MAX_WATCH_ITEMS = 24
WATCH_ALLOWED_STATUSES = {"watching", "triggered", "paused", "completed", "failed", "blocked", "needs_attention"}
WATCH_ACTIVE_STATUSES = {"watching", "triggered", "paused"}
WATCH_CONDITION_TYPES = {"file_exists", "file_changed", "browser_text_contains", "inspect_project_changed"}
WATCH_CHANGE_CONDITIONS = {"file_changed", "inspect_project_changed"}
WATCH_CONDITION_LABELS = {
    "file_exists": "File exists",
    "file_changed": "File changed",
    "browser_text_contains": "Browser text contains",
    "inspect_project_changed": "Project inspection changed",
}


def _trim_text(value: Any, limit: int = 240) -> str:
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _iso_timestamp() -> str:
    try:
        return datetime.now().astimezone().isoformat(timespec="seconds")
    except Exception:
        return ""


def _coerce_int(value: Any, default: int, *, minimum: int = 1, maximum: int = 3600) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed < minimum:
        parsed = minimum
    if parsed > maximum:
        parsed = maximum
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


def _normalize_watch_status(value: Any) -> str:
    text = str(value).strip().lower()
    if text in WATCH_ALLOWED_STATUSES:
        return text
    return "watching"


def _normalize_condition_type(value: Any) -> str:
    text = str(value).strip().lower()
    if text in WATCH_CONDITION_TYPES:
        return text
    return "file_exists"


def _hash_signature(*parts: Any) -> str:
    joined = "|".join(str(part or "") for part in parts)
    return hashlib.sha1(joined.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _normalize_watch_item(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    watch_id = _trim_text(value.get("watch_id", ""), limit=60)
    if not watch_id:
        return {}

    return {
        "watch_id": watch_id,
        "goal": _trim_text(value.get("goal", ""), limit=500),
        "status": _normalize_watch_status(value.get("status", "watching")),
        "condition_type": _normalize_condition_type(value.get("condition_type", "file_exists")),
        "target": _trim_text(value.get("target", ""), limit=320),
        "match_text": _trim_text(value.get("match_text", ""), limit=240),
        "interval_seconds": _coerce_int(value.get("interval_seconds", 10), 10, minimum=2, maximum=3600),
        "allow_repeat": _coerce_bool(value.get("allow_repeat", False), False),
        "created_at": _trim_text(value.get("created_at", ""), limit=40) or _iso_timestamp(),
        "updated_at": _trim_text(value.get("updated_at", ""), limit=40) or _iso_timestamp(),
        "last_checked_at": _trim_text(value.get("last_checked_at", ""), limit=40),
        "last_triggered_at": _trim_text(value.get("last_triggered_at", ""), limit=40),
        "linked_task_id": _trim_text(value.get("linked_task_id", ""), limit=60),
        "last_run_id": _trim_text(value.get("last_run_id", ""), limit=60),
        "last_run_status": _trim_text(value.get("last_run_status", ""), limit=40),
        "source": _trim_text(value.get("source", "watch_trigger"), limit=40) or "watch_trigger",
        "last_message": _trim_text(value.get("last_message", ""), limit=280),
        "error": _trim_text(value.get("error", ""), limit=200),
        "baseline_signature": _trim_text(value.get("baseline_signature", ""), limit=80),
        "last_signature": _trim_text(value.get("last_signature", ""), limit=80),
        "last_trigger_signature": _trim_text(value.get("last_trigger_signature", ""), limit=80),
        "last_condition_met": _coerce_bool(value.get("last_condition_met", False), False),
        "trigger_count": _coerce_int(value.get("trigger_count", 0), 0, minimum=0, maximum=9999),
        "pending_enqueue": _coerce_bool(value.get("pending_enqueue", False), False),
        "approval_needed": _coerce_bool(value.get("approval_needed", False), False),
        "approval_reason": _trim_text(value.get("approval_reason", ""), limit=180),
    }


class WatchStore:
    def __init__(self, path: str | Path, *, max_items: int = DEFAULT_MAX_WATCH_ITEMS):
        self.path = Path(path)
        self.max_items = max(1, int(max_items))

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"watches": []}

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"watches": []}

        if not isinstance(payload, dict):
            return {"watches": []}

        watches: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        for raw_watch in payload.get("watches", []):
            watch = _normalize_watch_item(raw_watch)
            watch_id = watch.get("watch_id", "")
            if not watch_id or watch_id in seen_ids:
                continue
            seen_ids.add(watch_id)
            watches.append(watch)

        return {"watches": self._trim_watches(watches)}

    def save(self, watches: List[Dict[str, Any]]) -> bool:
        payload = {
            "version": 1,
            "updated_at": _iso_timestamp(),
            "watches": self._trim_watches(watches),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            return False
        return True

    def _trim_watches(self, watches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = [_normalize_watch_item(watch) for watch in watches]
        normalized = [watch for watch in normalized if watch]
        if len(normalized) <= self.max_items:
            return normalized

        keep_ids: set[str] = set()
        ordered: List[Dict[str, Any]] = []
        for watch in normalized:
            if watch.get("status") in WATCH_ACTIVE_STATUSES or watch.get("allow_repeat"):
                watch_id = watch.get("watch_id", "")
                if watch_id and watch_id not in keep_ids:
                    keep_ids.add(watch_id)
                    ordered.append(watch)

        remaining = max(0, self.max_items - len(ordered))
        terminal = [watch for watch in normalized if watch.get("watch_id", "") not in keep_ids]
        terminal_tail = terminal[-remaining:] if remaining else []
        allowed_ids = keep_ids | {watch.get("watch_id", "") for watch in terminal_tail}
        return [watch for watch in normalized if watch.get("watch_id", "") in allowed_ids]


def _file_signature(path: Path) -> str:
    if not path.exists():
        return f"missing:{path}"
    try:
        stat = path.stat()
        return f"{path.resolve()}|{stat.st_mtime_ns}|{stat.st_size}"
    except Exception:
        return f"exists:{path}"


def evaluate_watch_condition(watch: Dict[str, Any], task_state: TaskState) -> Dict[str, Any]:
    condition_type = _normalize_condition_type(watch.get("condition_type", "file_exists"))
    if condition_type == "file_exists":
        return _evaluate_file_exists(watch)
    if condition_type == "file_changed":
        return _evaluate_file_changed(watch)
    if condition_type == "browser_text_contains":
        return _evaluate_browser_text_contains(watch, task_state)
    if condition_type == "inspect_project_changed":
        return _evaluate_inspect_project_changed(watch)
    return {
        "ok": False,
        "met": False,
        "signature": "",
        "message": "Unsupported watch condition.",
        "error": f"Unsupported watch condition: {condition_type}",
        "baseline_signature": "",
    }


def _evaluate_file_exists(watch: Dict[str, Any]) -> Dict[str, Any]:
    target = str(watch.get("target", "")).strip()
    if not target:
        return {"ok": False, "met": False, "signature": "", "message": "Watch target path is missing.", "error": "Missing watch target path.", "baseline_signature": ""}

    path = Path(target)
    exists = path.exists()
    signature = _file_signature(path)
    if exists:
        message = f"{path} exists."
    else:
        message = f"{path} does not exist yet."
    return {"ok": True, "met": exists, "signature": signature, "message": message, "error": "", "baseline_signature": ""}


def _evaluate_file_changed(watch: Dict[str, Any]) -> Dict[str, Any]:
    target = str(watch.get("target", "")).strip()
    if not target:
        return {"ok": False, "met": False, "signature": "", "message": "Watch target path is missing.", "error": "Missing watch target path.", "baseline_signature": ""}

    path = Path(target)
    signature = _file_signature(path)
    baseline = str(watch.get("baseline_signature", "")).strip()
    if not baseline:
        if path.exists():
            message = f"Captured initial file baseline for {path}."
        else:
            message = f"Watching {path}; baseline recorded as missing."
        return {"ok": True, "met": False, "signature": signature, "message": message, "error": "", "baseline_signature": signature}

    met = signature != baseline
    if met:
        message = f"Detected a file change for {path}."
    else:
        message = f"No file change detected for {path}."
    return {"ok": True, "met": met, "signature": signature, "message": message, "error": "", "baseline_signature": ""}


def _evaluate_browser_text_contains(watch: Dict[str, Any], task_state: TaskState) -> Dict[str, Any]:
    expected_text = str(watch.get("match_text", "")).strip()
    if not expected_text:
        return {"ok": False, "met": False, "signature": "", "message": "Expected browser text is missing.", "error": "Missing browser text match value.", "baseline_signature": ""}

    session_id = str(watch.get("target", "")).strip() or str(task_state.browser_session_id or "default")
    result = browser_inspect_page(
        {
            "session_id": session_id,
            "max_text_chars": 1000,
            "max_elements": 4,
            "max_retries": 0,
            "allow_reload": False,
            "allow_reinspect": False,
        }
    )

    page = result.get("page", {}) if isinstance(result, dict) else {}
    if not result.get("ok", False):
        if session_id in {"", "default", str(task_state.browser_session_id or "default")} and (
            task_state.browser_last_text_excerpt or task_state.browser_current_title or task_state.browser_current_url
        ):
            page = {
                "url": task_state.browser_current_url,
                "title": task_state.browser_current_title,
                "visible_text_excerpt": task_state.browser_last_text_excerpt,
            }
        else:
            error = str(result.get("error", "No active browser session is available for this watch.")).strip()
            return {"ok": False, "met": False, "signature": "", "message": error, "error": error, "baseline_signature": ""}

    combined_text = "\n".join(
        [
            str(page.get("title", "") or ""),
            str(page.get("visible_text_excerpt", "") or ""),
            str(page.get("url", "") or ""),
        ]
    )
    signature = _hash_signature(session_id, page.get("url", ""), page.get("title", ""), page.get("visible_text_excerpt", ""))
    met = expected_text.lower() in combined_text.lower()
    label = str(page.get("title", "") or page.get("url", "") or session_id).strip() or session_id
    if met:
        message = f"Browser session {label} contains '{expected_text}'."
    else:
        message = f"Browser session {label} does not contain '{expected_text}' yet."
    return {"ok": True, "met": met, "signature": signature, "message": message, "error": "", "baseline_signature": ""}


def _evaluate_inspect_project_changed(watch: Dict[str, Any]) -> Dict[str, Any]:
    target = str(watch.get("target", "")).strip()
    focus = str(watch.get("match_text", "")).strip()
    if not target:
        return {"ok": False, "met": False, "signature": "", "message": "Project path is missing.", "error": "Missing project path.", "baseline_signature": ""}

    result = inspect_project(
        {
            "path": target,
            "focus": focus,
            "goal": focus,
            "refresh": True,
            "max_depth": 2,
            "max_entries": 100,
            "max_files_to_read": 1,
            "max_bytes_per_file": 400,
            "top_k_relevant": 2,
        }
    )
    if not result.get("ok", False):
        error = str(result.get("error", "inspect_project failed")).strip()
        return {"ok": False, "met": False, "signature": "", "message": error, "error": error, "baseline_signature": ""}

    recommended = [item.get("relative_path", "") for item in result.get("recommended_files", [])[:3] if isinstance(item, dict)]
    previews = [item.get("preview", "") for item in result.get("file_previews", [])[:1] if isinstance(item, dict)]
    signature = _hash_signature(
        result.get("summary", ""),
        result.get("selection_summary", ""),
        "|".join(recommended),
        "|".join(previews),
    )
    baseline = str(watch.get("baseline_signature", "")).strip()
    if not baseline:
        return {
            "ok": True,
            "met": False,
            "signature": signature,
            "message": f"Captured initial project inspection baseline for {target}.",
            "error": "",
            "baseline_signature": signature,
        }

    met = signature != baseline
    if met:
        message = f"Detected a meaningful inspection change in {target}."
    else:
        message = f"No meaningful inspection change detected in {target}."
    return {"ok": True, "met": met, "signature": signature, "message": message, "error": "", "baseline_signature": ""}


def watch_counts(watches: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {status: 0 for status in WATCH_ALLOWED_STATUSES}
    for watch in watches:
        counts[_normalize_watch_status(watch.get("status", "watching"))] += 1
    return counts


def watch_summary(watch: Dict[str, Any]) -> Dict[str, Any]:
    condition_type = _normalize_condition_type(watch.get("condition_type", "file_exists"))
    return {
        "watch_id": _trim_text(watch.get("watch_id", ""), limit=60),
        "goal": _trim_text(watch.get("goal", ""), limit=220),
        "status": _normalize_watch_status(watch.get("status", "watching")),
        "condition_type": condition_type,
        "condition_label": WATCH_CONDITION_LABELS.get(condition_type, condition_type),
        "target": _trim_text(watch.get("target", ""), limit=220),
        "match_text": _trim_text(watch.get("match_text", ""), limit=180),
        "interval_seconds": _coerce_int(watch.get("interval_seconds", 10), 10, minimum=2, maximum=3600),
        "allow_repeat": _coerce_bool(watch.get("allow_repeat", False), False),
        "last_checked_at": _trim_text(watch.get("last_checked_at", ""), limit=40),
        "last_triggered_at": _trim_text(watch.get("last_triggered_at", ""), limit=40),
        "linked_task_id": _trim_text(watch.get("linked_task_id", ""), limit=60),
        "last_run_status": _trim_text(watch.get("last_run_status", ""), limit=40),
        "last_message": _trim_text(watch.get("last_message", ""), limit=220),
        "error": _trim_text(watch.get("error", ""), limit=180),
        "trigger_count": _coerce_int(watch.get("trigger_count", 0), 0, minimum=0, maximum=9999),
        "pending_enqueue": _coerce_bool(watch.get("pending_enqueue", False), False),
        "approval_needed": _coerce_bool(watch.get("approval_needed", False), False),
        "approval_reason": _trim_text(watch.get("approval_reason", ""), limit=180),
    }
