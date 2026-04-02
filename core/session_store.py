from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

from tools.files import export_inspect_project_cache, import_inspect_project_cache


SESSION_STATE_VERSION = 1
DEFAULT_SESSION_STATE_PATH = "data/session_state.json"
DEFAULT_STATE_SCOPE_ID = "default"


def _trim_text(value: Any, limit: int = 240) -> str:
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _normalize_string_list(value: Any, limit: int) -> List[str]:
    if not isinstance(value, list):
        return []

    items: List[str] = []
    for item in value:
        text = _trim_text(item)
        if not text or text in items:
            continue
        items.append(text)
        if len(items) >= limit:
            break
    return items


def _normalize_small_dict(value: Any, limit: int = 20) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    items: Dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = _trim_text(raw_key, limit=80)
        if not key:
            continue
        if isinstance(raw_value, bool):
            items[key] = raw_value
        elif isinstance(raw_value, int):
            items[key] = raw_value
        else:
            text = _trim_text(raw_value, limit=240)
            if not text:
                continue
            items[key] = text
        if len(items) >= limit:
            break
    return items


def _normalize_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


class SessionStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _normalize_scope_id(self, value: Any) -> str:
        text = _trim_text(value, limit=120)
        return text or DEFAULT_STATE_SCOPE_ID

    def _load_payload(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

        if not isinstance(payload, dict):
            return {}
        return payload

    def _extract_task_states(self, payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        task_states: Dict[str, Dict[str, Any]] = {}

        if isinstance(payload.get("task_states", {}), dict):
            for raw_scope_id, raw_state in payload.get("task_states", {}).items():
                scope_id = self._normalize_scope_id(raw_scope_id)
                normalized = self._normalize_task_state(raw_state)
                if normalized:
                    task_states[scope_id] = normalized

        legacy_task_state = self._normalize_task_state(payload.get("task_state", {}))
        if legacy_task_state:
            task_states.setdefault(DEFAULT_STATE_SCOPE_ID, legacy_task_state)

        return task_states

    def load(self, scope_id: str = DEFAULT_STATE_SCOPE_ID) -> Dict[str, Any]:
        payload = self._load_payload()
        task_states = self._extract_task_states(payload)
        normalized_scope_id = self._normalize_scope_id(scope_id)
        task_state = dict(task_states.get(normalized_scope_id, {}))
        cache_entries_loaded = import_inspect_project_cache(payload.get("inspect_project_cache", []))

        has_state = any(
            task_state.get(key)
            for key in (
                "goal",
                "known_files",
                "known_dirs",
                "priority_files",
                "memory_notes",
                "last_summary",
                "browser_current_url",
                "browser_workflow_name",
                "browser_task_name",
                "browser_checkpoint_pending",
            )
        )
        loaded = bool(has_state or (cache_entries_loaded and normalized_scope_id == DEFAULT_STATE_SCOPE_ID))

        return {
            "loaded": loaded,
            "task_state": task_state,
            "loaded_message": "Loaded persisted session state." if loaded else "",
        }

    def save(self, task_state, scope_id: str | None = None) -> bool:
        existing_payload = self._load_payload()
        task_states = self._extract_task_states(existing_payload)
        normalized_scope_id = self._normalize_scope_id(scope_id or getattr(task_state, "state_scope_id", DEFAULT_STATE_SCOPE_ID))
        task_states[normalized_scope_id] = task_state.to_session_snapshot()

        payload = {
            "version": SESSION_STATE_VERSION,
            "saved_at": int(time.time()),
            "task_state": task_states.get(DEFAULT_STATE_SCOPE_ID, {}),
            "task_states": task_states,
            "inspect_project_cache": export_inspect_project_cache(),
        }

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            return False
        return True

    def clear(self, scope_id: str | None = None) -> bool:
        normalized_scope_id = self._normalize_scope_id(scope_id or "") if scope_id else ""
        if normalized_scope_id:
            existing_payload = self._load_payload()
            task_states = self._extract_task_states(existing_payload)
            if normalized_scope_id in task_states:
                del task_states[normalized_scope_id]

            if not task_states:
                try:
                    if self.path.exists():
                        self.path.unlink()
                except Exception:
                    return False
                return True

            payload = {
                "version": SESSION_STATE_VERSION,
                "saved_at": int(time.time()),
                "task_state": task_states.get(DEFAULT_STATE_SCOPE_ID, {}),
                "task_states": task_states,
                "inspect_project_cache": export_inspect_project_cache(),
            }

            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception:
                return False
            return True

        try:
            if self.path.exists():
                self.path.unlink()
        except Exception:
            return False
        return True

    def _normalize_task_state(self, value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return {}

        return {
            "state_scope_id": self._normalize_scope_id(value.get("state_scope_id", DEFAULT_STATE_SCOPE_ID)),
            "goal": _trim_text(value.get("goal", ""), limit=500),
            "status": _trim_text(value.get("status", ""), limit=40),
            "known_files": _normalize_string_list(value.get("known_files", []), limit=30),
            "known_dirs": _normalize_string_list(value.get("known_dirs", []), limit=30),
            "priority_files": _normalize_string_list(value.get("priority_files", []), limit=6),
            "memory_notes": _normalize_string_list(value.get("memory_notes", []), limit=20),
            "last_summary": _trim_text(value.get("last_summary", ""), limit=600),
            "task_control_event": _trim_text(value.get("task_control_event", ""), limit=60),
            "task_control_reason": _trim_text(value.get("task_control_reason", ""), limit=240),
            "task_resume_available": bool(value.get("task_resume_available", False)),
            "task_replacement_task_id": _trim_text(value.get("task_replacement_task_id", ""), limit=60),
            "task_replacement_goal": _trim_text(value.get("task_replacement_goal", ""), limit=2000),
            "browser_session_id": _trim_text(value.get("browser_session_id", ""), limit=80),
            "browser_current_url": _trim_text(value.get("browser_current_url", ""), limit=240),
            "browser_current_title": _trim_text(value.get("browser_current_title", ""), limit=200),
            "browser_last_text_excerpt": _trim_text(value.get("browser_last_text_excerpt", ""), limit=400),
            "browser_last_action": _trim_text(value.get("browser_last_action", ""), limit=220),
            "browser_last_successful_action": _trim_text(value.get("browser_last_successful_action", ""), limit=220),
            "browser_last_successful_tool": _trim_text(value.get("browser_last_successful_tool", ""), limit=80),
            "browser_expected_target": _trim_text(value.get("browser_expected_target", ""), limit=120),
            "browser_expected_url_contains": _trim_text(value.get("browser_expected_url_contains", ""), limit=160),
            "browser_expected_title_contains": _trim_text(value.get("browser_expected_title_contains", ""), limit=160),
            "browser_expected_text_contains": _trim_text(value.get("browser_expected_text_contains", ""), limit=160),
            "browser_expect_navigation": bool(value.get("browser_expect_navigation", False)),
            "browser_retry_count": _normalize_int(value.get("browser_retry_count", 0)),
            "browser_fallback_attempts": _normalize_int(value.get("browser_fallback_attempts", 0)),
            "browser_recovery_notes": _normalize_string_list(value.get("browser_recovery_notes", []), limit=6),
            "browser_workflow_name": _trim_text(value.get("browser_workflow_name", ""), limit=120),
            "browser_workflow_pattern": _trim_text(value.get("browser_workflow_pattern", ""), limit=80),
            "browser_workflow_current_step": _trim_text(value.get("browser_workflow_current_step", ""), limit=120),
            "browser_workflow_next_step": _trim_text(value.get("browser_workflow_next_step", ""), limit=120),
            "browser_workflow_status": _trim_text(value.get("browser_workflow_status", ""), limit=40),
            "browser_task_name": _trim_text(value.get("browser_task_name", ""), limit=80),
            "browser_task_current_step": _trim_text(value.get("browser_task_current_step", ""), limit=120),
            "browser_task_next_step": _trim_text(value.get("browser_task_next_step", ""), limit=120),
            "browser_task_status": _trim_text(value.get("browser_task_status", ""), limit=40),
            "browser_workflow_history": _normalize_string_list(value.get("browser_workflow_history", []), limit=6),
            "browser_workflow_recovery_history": _normalize_string_list(value.get("browser_workflow_recovery_history", []), limit=6),
            "browser_checkpoint_pending": bool(value.get("browser_checkpoint_pending", False)),
            "browser_checkpoint_reason": _trim_text(value.get("browser_checkpoint_reason", ""), limit=180),
            "browser_checkpoint_step": _trim_text(value.get("browser_checkpoint_step", ""), limit=120),
            "browser_checkpoint_tool": _trim_text(value.get("browser_checkpoint_tool", ""), limit=80),
            "browser_checkpoint_target": _trim_text(value.get("browser_checkpoint_target", ""), limit=160),
            "browser_checkpoint_approval_status": _trim_text(value.get("browser_checkpoint_approval_status", ""), limit=40),
            "browser_checkpoint_resume_args": _normalize_small_dict(value.get("browser_checkpoint_resume_args", {})),
            "desktop_windows": _normalize_string_list(value.get("desktop_windows", []), limit=10),
            "desktop_active_window_title": _trim_text(value.get("desktop_active_window_title", ""), limit=180),
            "desktop_active_window_id": _trim_text(value.get("desktop_active_window_id", ""), limit=40),
            "desktop_active_window_process": _trim_text(value.get("desktop_active_window_process", ""), limit=120),
            "desktop_last_screenshot_path": _trim_text(value.get("desktop_last_screenshot_path", ""), limit=260),
            "desktop_last_screenshot_scope": _trim_text(value.get("desktop_last_screenshot_scope", ""), limit=40),
            "desktop_last_evidence_id": _trim_text(value.get("desktop_last_evidence_id", ""), limit=80),
            "desktop_last_evidence_summary": _trim_text(value.get("desktop_last_evidence_summary", ""), limit=240),
            "desktop_last_evidence_bundle_path": _trim_text(value.get("desktop_last_evidence_bundle_path", ""), limit=320),
            "desktop_last_evidence_reason": _trim_text(value.get("desktop_last_evidence_reason", ""), limit=40),
            "desktop_last_evidence_timestamp": _trim_text(value.get("desktop_last_evidence_timestamp", ""), limit=40),
            "desktop_observation_token": _trim_text(value.get("desktop_observation_token", ""), limit=120),
            "desktop_observed_at": _trim_text(value.get("desktop_observed_at", ""), limit=40),
            "desktop_recent_actions": _normalize_string_list(value.get("desktop_recent_actions", []), limit=8),
            "desktop_last_action": _trim_text(value.get("desktop_last_action", ""), limit=220),
            "desktop_last_target_window": _trim_text(value.get("desktop_last_target_window", ""), limit=180),
            "desktop_last_typed_text_preview": _trim_text(value.get("desktop_last_typed_text_preview", ""), limit=80),
            "desktop_last_key_sequence": _trim_text(value.get("desktop_last_key_sequence", ""), limit=80),
            "desktop_last_point": _trim_text(value.get("desktop_last_point", ""), limit=80),
            "desktop_checkpoint_pending": bool(value.get("desktop_checkpoint_pending", False)),
            "desktop_checkpoint_reason": _trim_text(value.get("desktop_checkpoint_reason", ""), limit=180),
            "desktop_checkpoint_tool": _trim_text(value.get("desktop_checkpoint_tool", ""), limit=80),
            "desktop_checkpoint_target": _trim_text(value.get("desktop_checkpoint_target", ""), limit=180),
            "desktop_checkpoint_evidence_id": _trim_text(value.get("desktop_checkpoint_evidence_id", ""), limit=80),
            "desktop_checkpoint_approval_status": _trim_text(value.get("desktop_checkpoint_approval_status", ""), limit=40),
            "desktop_checkpoint_resume_args": _normalize_small_dict(value.get("desktop_checkpoint_resume_args", {})),
        }
