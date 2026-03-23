from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4


RUN_HISTORY_VERSION = 1
DEFAULT_RUN_HISTORY_PATH = "data/run_history.json"
DEFAULT_MAX_RUNS = 25
DEFAULT_MAX_STEPS_PER_RUN = 40
DEFAULT_MAX_RECENT_RUNS = 6

SENSITIVE_EXACT_KEYS = {
    "value",
    "password",
    "secret",
    "token",
    "api_key",
    "authorization",
    "cookie",
    "new_text",
    "replace_text",
    "search_text",
    "expected_current_text",
    "draft_diff_preview",
    "content",
    "stdout",
    "stderr",
}
SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "authorization",
    "cookie",
)


def _trim_text(value: Any, limit: int = 240) -> str:
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _iso_timestamp(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    except Exception:
        return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _redacted_text(value: Any) -> str:
    text = str(value).strip()
    if not text:
        return "[redacted]"
    return f"[redacted {len(text)} chars]"


def _is_sensitive_key(key: str) -> bool:
    lowered = str(key).strip().lower()
    if lowered in SENSITIVE_EXACT_KEYS:
        return True
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _sanitize_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if _is_sensitive_key(key):
        return _redacted_text(value)

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        return _trim_text(value, limit=180)

    if isinstance(value, list):
        if depth >= 2:
            return f"[{len(value)} item(s)]"
        items = [_sanitize_value(item, key=key, depth=depth + 1) for item in value[:6]]
        if len(value) > 6:
            items.append(f"... ({len(value) - 6} more)")
        return items

    if isinstance(value, dict):
        if depth >= 2:
            return f"{{{len(value)} key(s)}}"
        items: Dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:12]:
            clean_key = _trim_text(raw_key, limit=80)
            if not clean_key:
                continue
            items[clean_key] = _sanitize_value(raw_value, key=clean_key, depth=depth + 1)
        return items

    return _trim_text(value, limit=180)


def _sanitize_args(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    items: Dict[str, Any] = {}
    for raw_key, raw_value in list(value.items())[:16]:
        key = _trim_text(raw_key, limit=80)
        if not key:
            continue
        items[key] = _sanitize_value(raw_value, key=key)
    return items


def _string_list(value: Any, *, limit: int = 4, text_limit: int = 180) -> List[str]:
    if not isinstance(value, list):
        return []

    items: List[str] = []
    for item in value:
        text = _trim_text(item, limit=text_limit)
        if not text or text in items:
            continue
        items.append(text)
        if len(items) >= limit:
            break
    return items


def _browser_transition(step: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    args = step.get("args", {}) if isinstance(step.get("args", {}), dict) else {}
    browser_state = result.get("browser_state", {}) if isinstance(result.get("browser_state", {}), dict) else {}

    payload = {
        "task_name": _trim_text(result.get("browser_task_name", args.get("browser_task_name", "")), limit=80),
        "task_step": _trim_text(result.get("browser_task_step", args.get("browser_task_step", "")), limit=120),
        "task_next_step": _trim_text(result.get("browser_task_next_step", args.get("browser_task_next_step", "")), limit=120),
        "workflow_name": _trim_text(result.get("workflow_name", args.get("workflow_name", "")), limit=120),
        "workflow_step": _trim_text(result.get("workflow_step", args.get("workflow_step", "")), limit=120),
        "workflow_next_step": _trim_text(result.get("workflow_next_step", args.get("workflow_next_step", "")), limit=120),
        "workflow_status": _trim_text(result.get("workflow_status", ""), limit=40),
        "checkpoint_step": _trim_text(result.get("checkpoint_step", ""), limit=120),
        "checkpoint_target": _trim_text(result.get("checkpoint_target", ""), limit=140),
        "current_url": _trim_text(browser_state.get("current_url", result.get("current_url", "")), limit=220),
        "current_title": _trim_text(browser_state.get("current_title", result.get("current_title", "")), limit=160),
    }

    compact = {key: value for key, value in payload.items() if value}
    if result.get("paused", False):
        compact["paused"] = True
    if result.get("workflow_resumed", False):
        compact["resumed"] = True
    if result.get("approval_required", False):
        compact["approval_required"] = True
    return compact


def _approval_payload(step: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    args = step.get("args", {}) if isinstance(step.get("args", {}), dict) else {}
    step_status = str(step.get("status", "")).strip()
    approval_status = _trim_text(args.get("approval_status", result.get("approval_status", step_status)), limit=40)

    payload = {
        "status": approval_status,
        "paused": bool(result.get("paused", False) or step_status == "paused"),
        "resumed": bool(result.get("workflow_resumed", False)),
        "required": bool(
            result.get("approval_required", False)
            or result.get("checkpoint_required", False)
            or args.get("checkpoint_required", False)
            or step_status == "paused"
        ),
        "reason": _trim_text(result.get("checkpoint_reason", step.get("message", "")), limit=180),
    }

    return {
        key: value
        for key, value in payload.items()
        if value not in {"", False} and value is not None
    }


def _recovery_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    retry_count = max(0, _safe_int(result.get("retry_count", 0)))
    fallback_attempts = max(0, _safe_int(result.get("fallback_attempts", 0)))
    notes = _string_list(result.get("recovery_notes", []), limit=4, text_limit=180)

    payload = {
        "retry_count": retry_count,
        "fallback_attempts": fallback_attempts,
        "notes": notes,
    }
    return {
        key: value
        for key, value in payload.items()
        if value not in (0, "", None) and value != []
    }


def _step_summary(step: Dict[str, Any], result: Dict[str, Any]) -> str:
    if result:
        summary = str(result.get("summary", "") or result.get("error", "")).strip()
        if summary:
            return _trim_text(summary, limit=220)
    message = str(step.get("message", "")).strip()
    return _trim_text(message, limit=220)


def _step_entry(step: Dict[str, Any], *, index: int) -> Dict[str, Any]:
    result = step.get("result", {}) if isinstance(step.get("result", {}), dict) else {}
    tool_name = str(step.get("tool", step.get("type", "unknown"))).strip() or "unknown"
    entry = {
        "index": index,
        "type": str(step.get("type", "")).strip() or "unknown",
        "tool": tool_name,
        "status": str(step.get("status", "")).strip() or "unknown",
        "prepared_args": _sanitize_args(step.get("args", {})),
        "result_summary": _step_summary(step, result),
    }

    approval = _approval_payload(step, result)
    if approval:
        entry["approval"] = approval

    browser = _browser_transition(step, result)
    if browser:
        entry["browser_transition"] = browser

    recovery = _recovery_payload(result)
    if recovery:
        entry["recovery"] = recovery

    message = _trim_text(step.get("message", ""), limit=220)
    if message:
        entry["message"] = message

    return entry


def _end_state(task_state) -> Dict[str, Any]:
    control_snapshot = task_state.get_control_snapshot() if hasattr(task_state, "get_control_snapshot") else {}
    behavior = control_snapshot.get("behavior", {}) if isinstance(control_snapshot, dict) else {}
    task_control = control_snapshot.get("task_control", {}) if isinstance(control_snapshot, dict) else {}
    payload = {
        "state_scope_id": _trim_text(getattr(task_state, "state_scope_id", ""), limit=120),
        "status": _trim_text(getattr(task_state, "status", ""), limit=40),
        "summary": _trim_text(getattr(task_state, "last_summary", ""), limit=280),
        "mode": _trim_text(behavior.get("mode", ""), limit=80),
        "task_phase": _trim_text(behavior.get("task_phase", ""), limit=80),
        "task_control_event": _trim_text(task_control.get("event", ""), limit=60),
        "task_control_reason": _trim_text(task_control.get("reason", ""), limit=220),
        "browser_task_name": _trim_text(getattr(task_state, "browser_task_name", ""), limit=80),
        "browser_task_status": _trim_text(getattr(task_state, "browser_task_status", ""), limit=40),
        "browser_workflow_name": _trim_text(getattr(task_state, "browser_workflow_name", ""), limit=120),
        "browser_workflow_status": _trim_text(getattr(task_state, "browser_workflow_status", ""), limit=40),
        "browser_checkpoint_pending": bool(getattr(task_state, "browser_checkpoint_pending", False)),
        "browser_current_url": _trim_text(getattr(task_state, "browser_current_url", ""), limit=220),
        "browser_current_title": _trim_text(getattr(task_state, "browser_current_title", ""), limit=160),
    }
    return {
        key: value
        for key, value in payload.items()
        if value not in {"", False} and value is not None
    }


class RunHistoryStore:
    def __init__(self, path: str | Path, *, max_runs: int = DEFAULT_MAX_RUNS, max_steps_per_run: int = DEFAULT_MAX_STEPS_PER_RUN):
        self.path = Path(path)
        self.max_runs = max(1, int(max_runs))
        self.max_steps_per_run = max(1, int(max_steps_per_run))

    def _normalize_filter(self, value: Any, *, limit: int = 120) -> str:
        return _trim_text(value, limit=limit)

    def _run_matches(self, run: Dict[str, Any], *, session_id: str = "", state_scope_id: str = "") -> bool:
        normalized_session_id = self._normalize_filter(session_id, limit=80)
        normalized_scope_id = self._normalize_filter(state_scope_id, limit=120)
        if normalized_session_id and _trim_text(run.get("session_id", ""), limit=80) != normalized_session_id:
            return False
        if normalized_scope_id and _trim_text(run.get("state_scope_id", ""), limit=120) != normalized_scope_id:
            return False
        return True

    def next_run_id(self) -> str:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"run-{timestamp}-{uuid4().hex[:8]}"

    def load_runs(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []

        if not isinstance(payload, dict):
            return []
        runs = payload.get("runs", [])
        if not isinstance(runs, list):
            return []
        return [run for run in runs if isinstance(run, dict)]

    def get_recent_runs(
        self,
        limit: int = DEFAULT_MAX_RECENT_RUNS,
        *,
        session_id: str = "",
        state_scope_id: str = "",
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        safe_limit = max(1, int(limit))
        for run in self.load_runs():
            if not self._run_matches(run, session_id=session_id, state_scope_id=state_scope_id):
                continue
            items.append(
                {
                    "run_id": _trim_text(run.get("run_id", ""), limit=40),
                    "source": _trim_text(run.get("source", ""), limit=40),
                    "goal": _trim_text(run.get("goal", ""), limit=180),
                    "session_id": _trim_text(run.get("session_id", ""), limit=80),
                    "state_scope_id": _trim_text(run.get("state_scope_id", ""), limit=120),
                    "started_at": _trim_text(run.get("started_at", ""), limit=40),
                    "ended_at": _trim_text(run.get("ended_at", ""), limit=40),
                    "final_status": _trim_text(run.get("final_status", ""), limit=40),
                    "duration_seconds": run.get("duration_seconds", 0),
                    "step_count": _safe_int(run.get("step_count", 0)),
                    "final_summary": _trim_text(run.get("final_summary", ""), limit=220),
                }
            )
            if len(items) >= safe_limit:
                break
        return items

    def get_latest_run(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        for run in self.load_runs():
            if self._run_matches(run, session_id=session_id, state_scope_id=state_scope_id):
                return run
        return {}

    def record_run(
        self,
        *,
        run_id: str,
        goal: str,
        started_at: float,
        ended_at: float,
        final_status: str,
        final_summary: str,
        result_message: str,
        steps: List[Dict[str, Any]],
        task_state,
        source: str = "goal_run",
        step_offset: int = 0,
        session_id: str = "",
        state_scope_id: str = "",
    ) -> Dict[str, Any]:
        step_entries = [
            _step_entry(step, index=step_offset + offset)
            for offset, step in enumerate((steps or [])[: self.max_steps_per_run])
            if isinstance(step, dict)
        ]
        entry = {
            "run_id": _trim_text(run_id, limit=60),
            "source": _trim_text(source, limit=40),
            "goal": _trim_text(goal, limit=500),
            "session_id": _trim_text(session_id, limit=80),
            "state_scope_id": _trim_text(state_scope_id, limit=120),
            "started_at": _iso_timestamp(started_at),
            "ended_at": _iso_timestamp(ended_at),
            "duration_seconds": round(max(0.0, float(ended_at) - float(started_at)), 2),
            "final_status": _trim_text(final_status, limit=40),
            "final_summary": _trim_text(final_summary or result_message, limit=320),
            "result_message": _trim_text(result_message, limit=12000),
            "step_count": len(step_entries),
            "steps": step_entries,
            "end_state": _end_state(task_state),
        }
        self.append_run(entry)
        return entry

    def append_run(self, entry: Dict[str, Any]) -> bool:
        runs = self.load_runs()
        runs.insert(0, entry)
        runs = runs[: self.max_runs]
        payload = {
            "version": RUN_HISTORY_VERSION,
            "updated_at": _iso_timestamp(time.time()),
            "runs": runs,
        }

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            return False
        return True
