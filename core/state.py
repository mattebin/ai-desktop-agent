from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from core.browser_tasks import (
    browser_task_label,
    infer_browser_task_name,
    infer_browser_task_next_step,
    infer_browser_task_step,
    resolve_browser_task_status,
)
from core.operator_behavior import behavior_context_lines, derive_behavior_contract


BROWSER_WORKFLOW_PATTERN_LABELS = {
    "browser_step_sequence": "Browser workflow",
    "form_flow": "Form flow",
    "navigation_extract_flow": "Navigation and extract flow",
}
BROWSER_WORKFLOW_PATTERN_ALIASES = {
    "browser_step_sequence": "browser_step_sequence",
    "browser_workflow": "browser_step_sequence",
    "sequence": "browser_step_sequence",
    "form": "form_flow",
    "form_flow": "form_flow",
    "form_entry": "form_flow",
    "open_inspect_type_click_inspect": "form_flow",
    "open-inspect-type-click-inspect": "form_flow",
    "navigation": "navigation_extract_flow",
    "navigation_extract": "navigation_extract_flow",
    "navigation_extract_flow": "navigation_extract_flow",
    "link_extract": "navigation_extract_flow",
    "open_follow_inspect_extract": "navigation_extract_flow",
    "open-follow-inspect-extract": "navigation_extract_flow",
}
BROWSER_TOOL_STEP_LABELS = {
    "browser_open_page": "open page",
    "browser_inspect_page": "inspect page",
    "browser_click": "click element",
    "browser_type": "type into field",
    "browser_extract_text": "extract text",
    "browser_follow_link": "follow link",
}
DESKTOP_TOOL_STEP_LABELS = {
    "desktop_list_windows": "list visible windows",
    "desktop_get_active_window": "get active window",
    "desktop_focus_window": "focus window",
    "desktop_capture_screenshot": "capture screenshot",
    "desktop_click_point": "click point",
    "desktop_type_text": "type text",
}
MAX_TASK_GOAL_CHARS = 4000
MAX_TASK_REPLACEMENT_GOAL_CHARS = 2000


class TaskState:
    def __init__(
        self,
        goal: str,
        session_state: Dict[str, Any] | None = None,
        loaded_message: str = "",
        state_scope_id: str = "default",
    ):
        provided_goal = str(goal).strip()[:MAX_TASK_GOAL_CHARS]
        self.goal = provided_goal
        self.state_scope_id = str(state_scope_id).strip()[:120] or "default"
        self.steps: List[Dict[str, Any]] = []
        self.status = "running"

        self.known_files: List[str] = []
        self.known_dirs: List[str] = []
        self.priority_files: List[str] = []
        self.memory_notes: List[str] = []
        self.last_summary: str = ""
        self.task_control_event: str = ""
        self.task_control_reason: str = ""
        self.task_resume_available: bool = False
        self.task_replacement_task_id: str = ""
        self.task_replacement_goal: str = ""

        self.browser_session_id: str = ""
        self.browser_current_url: str = ""
        self.browser_current_title: str = ""
        self.browser_last_text_excerpt: str = ""
        self.browser_recent_actions: List[str] = []
        self.browser_last_action: str = ""
        self.browser_expected_target: str = ""
        self.browser_expected_url_contains: str = ""
        self.browser_expected_title_contains: str = ""
        self.browser_expected_text_contains: str = ""
        self.browser_expect_navigation: bool = False
        self.browser_retry_count: int = 0
        self.browser_fallback_attempts: int = 0
        self.browser_recovery_notes: List[str] = []
        self.browser_workflow_name: str = ""
        self.browser_workflow_pattern: str = ""
        self.browser_workflow_current_step: str = ""
        self.browser_workflow_next_step: str = ""
        self.browser_workflow_status: str = ""
        self.browser_task_name: str = ""
        self.browser_task_current_step: str = ""
        self.browser_task_next_step: str = ""
        self.browser_task_status: str = ""
        self.browser_last_successful_action: str = ""
        self.browser_last_successful_tool: str = ""
        self.browser_last_input_label: str = ""
        self.browser_last_input_selector: str = ""
        self.browser_last_input_value: str = ""
        self.browser_workflow_history: List[str] = []
        self.browser_workflow_recovery_history: List[str] = []
        self.browser_checkpoint_pending: bool = False
        self.browser_checkpoint_reason: str = ""
        self.browser_checkpoint_step: str = ""
        self.browser_checkpoint_tool: str = ""
        self.browser_checkpoint_target: str = ""
        self.browser_checkpoint_approval_status: str = ""
        self.browser_checkpoint_resume_args: Dict[str, Any] = {}
        self.desktop_windows: List[str] = []
        self.desktop_active_window_title: str = ""
        self.desktop_active_window_id: str = ""
        self.desktop_active_window_process: str = ""
        self.desktop_last_screenshot_path: str = ""
        self.desktop_last_screenshot_scope: str = ""
        self.desktop_last_evidence_id: str = ""
        self.desktop_last_evidence_summary: str = ""
        self.desktop_last_evidence_bundle_path: str = ""
        self.desktop_last_evidence_reason: str = ""
        self.desktop_last_evidence_timestamp: str = ""
        self.desktop_observation_token: str = ""
        self.desktop_observed_at: str = ""
        self.desktop_recent_actions: List[str] = []
        self.desktop_last_action: str = ""
        self.desktop_last_target_window: str = ""
        self.desktop_last_typed_text_preview: str = ""
        self.desktop_last_point: str = ""
        self.desktop_checkpoint_pending: bool = False
        self.desktop_checkpoint_reason: str = ""
        self.desktop_checkpoint_tool: str = ""
        self.desktop_checkpoint_target: str = ""
        self.desktop_checkpoint_evidence_id: str = ""
        self.desktop_checkpoint_approval_status: str = ""
        self.desktop_checkpoint_resume_args: Dict[str, Any] = {}
        if isinstance(session_state, dict):
            self._restore_session_state(session_state)

        if provided_goal:
            self.goal = provided_goal

        if loaded_message:
            self.add_note(loaded_message)
            if self.memory_notes:
                self.set_summary(" | ".join(self.memory_notes[-6:]))

    def add_step(self, step: Dict[str, Any]):
        self.steps.append(step)

    def _normalize_values(self, values: Any, limit: int = 30, text_limit: int = 260) -> List[str]:
        if not isinstance(values, list):
            return []

        unique: List[str] = []
        for value in values:
            text = str(value).strip()
            if not text:
                continue
            if len(text) > text_limit:
                text = text[: text_limit - 3].rstrip() + "..."
            if text in unique:
                continue
            unique.append(text)
            if len(unique) >= limit:
                break
        return unique

    def _normalize_checkpoint_args(self, value: Any, limit: int = 20) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return {}

        items: Dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key).strip()[:80]
            if not key:
                continue

            if isinstance(raw_value, bool):
                items[key] = raw_value
            elif isinstance(raw_value, int):
                items[key] = raw_value
            else:
                text = str(raw_value).strip()
                if not text:
                    continue
                if len(text) > 240:
                    text = text[:237].rstrip() + "..."
                items[key] = text

            if len(items) >= limit:
                break
        return items
    def _restore_session_state(self, session_state: Dict[str, Any]):
        restored_scope_id = str(session_state.get("state_scope_id", "")).strip()[:120]
        if restored_scope_id:
            self.state_scope_id = restored_scope_id
        restored_goal = str(session_state.get("goal", "")).strip()[:MAX_TASK_GOAL_CHARS]
        if restored_goal:
            self.goal = restored_goal
        restored_status = str(session_state.get("status", "")).strip()[:40]
        if restored_status:
            self.status = restored_status

        self.known_files = self._normalize_values(session_state.get("known_files", []), limit=30)
        self.known_dirs = self._normalize_values(session_state.get("known_dirs", []), limit=30)
        self.priority_files = self._normalize_values(session_state.get("priority_files", []), limit=6)
        self.memory_notes = self._normalize_values(session_state.get("memory_notes", []), limit=20, text_limit=320)
        self.last_summary = str(session_state.get("last_summary", "")).strip()[:600]
        self.task_control_event = str(session_state.get("task_control_event", "")).strip()[:60]
        self.task_control_reason = str(session_state.get("task_control_reason", "")).strip()[:240]
        self.task_resume_available = bool(session_state.get("task_resume_available", False))
        self.task_replacement_task_id = str(session_state.get("task_replacement_task_id", "")).strip()[:60]
        self.task_replacement_goal = str(session_state.get("task_replacement_goal", "")).strip()[:MAX_TASK_REPLACEMENT_GOAL_CHARS]

        self.browser_session_id = str(session_state.get("browser_session_id", "")).strip()[:80]
        self.browser_current_url = str(session_state.get("browser_current_url", "")).strip()[:240]
        self.browser_current_title = str(session_state.get("browser_current_title", "")).strip()[:200]
        self.browser_last_text_excerpt = str(session_state.get("browser_last_text_excerpt", "")).strip()[:400]
        self.browser_last_action = str(session_state.get("browser_last_action", "")).strip()[:220]
        self.browser_last_successful_action = str(session_state.get("browser_last_successful_action", "")).strip()[:220]
        self.browser_last_successful_tool = str(session_state.get("browser_last_successful_tool", "")).strip()[:80]
        self.browser_last_input_label = str(session_state.get("browser_last_input_label", "")).strip()[:120]
        self.browser_last_input_selector = str(session_state.get("browser_last_input_selector", "")).strip()[:120]
        self.browser_last_input_value = str(session_state.get("browser_last_input_value", "")).strip()[:240]
        self.browser_expected_target = str(session_state.get("browser_expected_target", "")).strip()[:120]
        self.browser_expected_url_contains = str(session_state.get("browser_expected_url_contains", "")).strip()[:160]
        self.browser_expected_title_contains = str(session_state.get("browser_expected_title_contains", "")).strip()[:160]
        self.browser_expected_text_contains = str(session_state.get("browser_expected_text_contains", "")).strip()[:160]
        self.browser_expect_navigation = bool(session_state.get("browser_expect_navigation", False))
        self.browser_recovery_notes = self._normalize_values(session_state.get("browser_recovery_notes", []), limit=6, text_limit=220)
        self.browser_workflow_name = str(session_state.get("browser_workflow_name", "")).strip()[:120]
        self.browser_workflow_pattern = str(session_state.get("browser_workflow_pattern", "")).strip()[:80]
        self.browser_workflow_current_step = str(session_state.get("browser_workflow_current_step", "")).strip()[:120]
        self.browser_workflow_next_step = str(session_state.get("browser_workflow_next_step", "")).strip()[:120]
        self.browser_workflow_status = str(session_state.get("browser_workflow_status", "")).strip()[:40]
        self.browser_task_name = str(session_state.get("browser_task_name", "")).strip()[:80]
        self.browser_task_current_step = str(session_state.get("browser_task_current_step", "")).strip()[:120]
        self.browser_task_next_step = str(session_state.get("browser_task_next_step", "")).strip()[:120]
        self.browser_task_status = str(session_state.get("browser_task_status", "")).strip()[:40]
        self.browser_workflow_history = self._normalize_values(session_state.get("browser_workflow_history", []), limit=6, text_limit=220)
        self.browser_workflow_recovery_history = self._normalize_values(session_state.get("browser_workflow_recovery_history", []), limit=6, text_limit=220)
        self.browser_checkpoint_pending = bool(session_state.get("browser_checkpoint_pending", False))
        self.browser_checkpoint_reason = str(session_state.get("browser_checkpoint_reason", "")).strip()[:180]
        self.browser_checkpoint_step = str(session_state.get("browser_checkpoint_step", "")).strip()[:120]
        self.browser_checkpoint_tool = str(session_state.get("browser_checkpoint_tool", "")).strip()[:80]
        self.browser_checkpoint_target = str(session_state.get("browser_checkpoint_target", "")).strip()[:160]
        self.browser_checkpoint_approval_status = str(session_state.get("browser_checkpoint_approval_status", "")).strip()[:40]
        self.browser_checkpoint_resume_args = self._normalize_checkpoint_args(session_state.get("browser_checkpoint_resume_args", {}))
        self.desktop_windows = self._normalize_values(session_state.get("desktop_windows", []), limit=10, text_limit=180)
        self.desktop_active_window_title = str(session_state.get("desktop_active_window_title", "")).strip()[:180]
        self.desktop_active_window_id = str(session_state.get("desktop_active_window_id", "")).strip()[:40]
        self.desktop_active_window_process = str(session_state.get("desktop_active_window_process", "")).strip()[:120]
        self.desktop_last_screenshot_path = str(session_state.get("desktop_last_screenshot_path", "")).strip()[:260]
        self.desktop_last_screenshot_scope = str(session_state.get("desktop_last_screenshot_scope", "")).strip()[:40]
        self.desktop_last_evidence_id = str(session_state.get("desktop_last_evidence_id", "")).strip()[:80]
        self.desktop_last_evidence_summary = str(session_state.get("desktop_last_evidence_summary", "")).strip()[:240]
        self.desktop_last_evidence_bundle_path = str(session_state.get("desktop_last_evidence_bundle_path", "")).strip()[:320]
        self.desktop_last_evidence_reason = str(session_state.get("desktop_last_evidence_reason", "")).strip()[:40]
        self.desktop_last_evidence_timestamp = str(session_state.get("desktop_last_evidence_timestamp", "")).strip()[:40]
        self.desktop_observation_token = str(session_state.get("desktop_observation_token", "")).strip()[:120]
        self.desktop_observed_at = str(session_state.get("desktop_observed_at", "")).strip()[:40]
        self.desktop_recent_actions = self._normalize_values(session_state.get("desktop_recent_actions", []), limit=8, text_limit=220)
        self.desktop_last_action = str(session_state.get("desktop_last_action", "")).strip()[:220]
        self.desktop_last_target_window = str(session_state.get("desktop_last_target_window", "")).strip()[:180]
        self.desktop_last_typed_text_preview = str(session_state.get("desktop_last_typed_text_preview", "")).strip()[:80]
        self.desktop_last_point = str(session_state.get("desktop_last_point", "")).strip()[:80]
        self.desktop_checkpoint_pending = bool(session_state.get("desktop_checkpoint_pending", False))
        self.desktop_checkpoint_reason = str(session_state.get("desktop_checkpoint_reason", "")).strip()[:180]
        self.desktop_checkpoint_tool = str(session_state.get("desktop_checkpoint_tool", "")).strip()[:80]
        self.desktop_checkpoint_target = str(session_state.get("desktop_checkpoint_target", "")).strip()[:180]
        self.desktop_checkpoint_evidence_id = str(session_state.get("desktop_checkpoint_evidence_id", "")).strip()[:80]
        self.desktop_checkpoint_approval_status = str(session_state.get("desktop_checkpoint_approval_status", "")).strip()[:40]
        self.desktop_checkpoint_resume_args = self._normalize_checkpoint_args(session_state.get("desktop_checkpoint_resume_args", {}))

        try:
            self.browser_retry_count = max(0, int(session_state.get("browser_retry_count", 0)))
        except (TypeError, ValueError):
            self.browser_retry_count = 0
        try:
            self.browser_fallback_attempts = max(0, int(session_state.get("browser_fallback_attempts", 0)))
        except (TypeError, ValueError):
            self.browser_fallback_attempts = 0

    def to_session_snapshot(self) -> Dict[str, Any]:
        return {
            "state_scope_id": self.state_scope_id[:120] or "default",
            "goal": str(self.goal).strip()[:MAX_TASK_GOAL_CHARS],
            "status": str(self.status).strip()[:40],
            "known_files": self._normalize_values(self.known_files, limit=30),
            "known_dirs": self._normalize_values(self.known_dirs, limit=30),
            "priority_files": self._normalize_values(self.priority_files, limit=6),
            "memory_notes": self._normalize_values(self.memory_notes, limit=20, text_limit=320),
            "last_summary": str(self.last_summary).strip()[:600],
            "task_control_event": self.task_control_event[:60],
            "task_control_reason": self.task_control_reason[:240],
            "task_resume_available": bool(self.task_resume_available),
            "task_replacement_task_id": self.task_replacement_task_id[:60],
            "task_replacement_goal": self.task_replacement_goal[:MAX_TASK_REPLACEMENT_GOAL_CHARS],
            "browser_session_id": self.browser_session_id[:80],
            "browser_current_url": self.browser_current_url[:240],
            "browser_current_title": self.browser_current_title[:200],
            "browser_last_text_excerpt": self.browser_last_text_excerpt[:400],
            "browser_last_action": self.browser_last_action[:220],
            "browser_last_successful_action": self.browser_last_successful_action[:220],
            "browser_last_successful_tool": self.browser_last_successful_tool[:80],
            "browser_last_input_label": self.browser_last_input_label[:120],
            "browser_last_input_selector": self.browser_last_input_selector[:120],
            "browser_last_input_value": self.browser_last_input_value[:240],
            "browser_expected_target": self.browser_expected_target[:120],
            "browser_expected_url_contains": self.browser_expected_url_contains[:160],
            "browser_expected_title_contains": self.browser_expected_title_contains[:160],
            "browser_expected_text_contains": self.browser_expected_text_contains[:160],
            "browser_expect_navigation": bool(self.browser_expect_navigation),
            "browser_retry_count": max(0, int(self.browser_retry_count)),
            "browser_fallback_attempts": max(0, int(self.browser_fallback_attempts)),
            "browser_recovery_notes": self._normalize_values(self.browser_recovery_notes, limit=6, text_limit=220),
            "browser_workflow_name": self.browser_workflow_name[:120],
            "browser_workflow_pattern": self.browser_workflow_pattern[:80],
            "browser_workflow_current_step": self.browser_workflow_current_step[:120],
            "browser_workflow_next_step": self.browser_workflow_next_step[:120],
            "browser_workflow_status": self.browser_workflow_status[:40],
            "browser_task_name": self.browser_task_name[:80],
            "browser_task_current_step": self.browser_task_current_step[:120],
            "browser_task_next_step": self.browser_task_next_step[:120],
            "browser_task_status": self.browser_task_status[:40],
            "browser_workflow_history": self._normalize_values(self.browser_workflow_history, limit=6, text_limit=220),
            "browser_workflow_recovery_history": self._normalize_values(self.browser_workflow_recovery_history, limit=6, text_limit=220),
            "browser_checkpoint_pending": bool(self.browser_checkpoint_pending),
            "browser_checkpoint_reason": self.browser_checkpoint_reason[:180],
            "browser_checkpoint_step": self.browser_checkpoint_step[:120],
            "browser_checkpoint_tool": self.browser_checkpoint_tool[:80],
            "browser_checkpoint_target": self.browser_checkpoint_target[:160],
            "browser_checkpoint_approval_status": self.browser_checkpoint_approval_status[:40],
            "browser_checkpoint_resume_args": self._normalize_checkpoint_args(self.browser_checkpoint_resume_args),
            "desktop_windows": self._normalize_values(self.desktop_windows, limit=10, text_limit=180),
            "desktop_active_window_title": self.desktop_active_window_title[:180],
            "desktop_active_window_id": self.desktop_active_window_id[:40],
            "desktop_active_window_process": self.desktop_active_window_process[:120],
            "desktop_last_screenshot_path": self.desktop_last_screenshot_path[:260],
            "desktop_last_screenshot_scope": self.desktop_last_screenshot_scope[:40],
            "desktop_last_evidence_id": self.desktop_last_evidence_id[:80],
            "desktop_last_evidence_summary": self.desktop_last_evidence_summary[:240],
            "desktop_last_evidence_bundle_path": self.desktop_last_evidence_bundle_path[:320],
            "desktop_last_evidence_reason": self.desktop_last_evidence_reason[:40],
            "desktop_last_evidence_timestamp": self.desktop_last_evidence_timestamp[:40],
            "desktop_observation_token": self.desktop_observation_token[:120],
            "desktop_observed_at": self.desktop_observed_at[:40],
            "desktop_recent_actions": self._normalize_values(self.desktop_recent_actions, limit=8, text_limit=220),
            "desktop_last_action": self.desktop_last_action[:220],
            "desktop_last_target_window": self.desktop_last_target_window[:180],
            "desktop_last_typed_text_preview": self.desktop_last_typed_text_preview[:80],
            "desktop_last_point": self.desktop_last_point[:80],
            "desktop_checkpoint_pending": bool(self.desktop_checkpoint_pending),
            "desktop_checkpoint_reason": self.desktop_checkpoint_reason[:180],
            "desktop_checkpoint_tool": self.desktop_checkpoint_tool[:80],
            "desktop_checkpoint_target": self.desktop_checkpoint_target[:180],
            "desktop_checkpoint_evidence_id": self.desktop_checkpoint_evidence_id[:80],
            "desktop_checkpoint_approval_status": self.desktop_checkpoint_approval_status[:40],
            "desktop_checkpoint_resume_args": self._normalize_checkpoint_args(self.desktop_checkpoint_resume_args),
        }

    def _push_unique(self, target: List[str], value: str, limit: int = 30):
        if value and value not in target:
            target.append(value)
        if len(target) > limit:
            del target[:-limit]

    def _set_priority_files(self, values: List[str], limit: int = 6):
        self.priority_files = self._normalize_values(values, limit=limit)

    def _add_browser_action(self, action: str, limit: int = 8):
        text = str(action).strip()
        if not text:
            return
        if text in self.browser_recent_actions:
            self.browser_recent_actions.remove(text)
        self.browser_recent_actions.append(text)
        if len(self.browser_recent_actions) > limit:
            del self.browser_recent_actions[:-limit]

    def _add_browser_recovery_note(self, note: str, limit: int = 6):
        text = str(note).strip()
        if not text:
            return
        if text in self.browser_recovery_notes:
            self.browser_recovery_notes.remove(text)
        self.browser_recovery_notes.append(text)
        if len(self.browser_recovery_notes) > limit:
            del self.browser_recovery_notes[:-limit]

    def _add_browser_workflow_history(self, note: str, limit: int = 6):
        text = str(note).strip()
        if not text:
            return
        if text in self.browser_workflow_history:
            self.browser_workflow_history.remove(text)
        self.browser_workflow_history.append(text)
        if len(self.browser_workflow_history) > limit:
            del self.browser_workflow_history[:-limit]

    def _add_browser_workflow_recovery(self, note: str, limit: int = 6):
        text = str(note).strip()
        if not text:
            return
        if text in self.browser_workflow_recovery_history:
            self.browser_workflow_recovery_history.remove(text)
        self.browser_workflow_recovery_history.append(text)
        if len(self.browser_workflow_recovery_history) > limit:
            del self.browser_workflow_recovery_history[:-limit]

    def _add_desktop_action(self, action: str, limit: int = 8):
        text = str(action).strip()
        if not text:
            return
        if text in self.desktop_recent_actions:
            self.desktop_recent_actions.remove(text)
        self.desktop_recent_actions.append(text)
        if len(self.desktop_recent_actions) > limit:
            del self.desktop_recent_actions[:-limit]

    def _clear_browser_checkpoint(self):
        self.browser_checkpoint_pending = False
        self.browser_checkpoint_reason = ""
        self.browser_checkpoint_step = ""
        self.browser_checkpoint_tool = ""
        self.browser_checkpoint_target = ""
        self.browser_checkpoint_approval_status = ""
        self.browser_checkpoint_resume_args = {}

    def _clear_desktop_checkpoint(self):
        self.desktop_checkpoint_pending = False
        self.desktop_checkpoint_reason = ""
        self.desktop_checkpoint_tool = ""
        self.desktop_checkpoint_target = ""
        self.desktop_checkpoint_evidence_id = ""
        self.desktop_checkpoint_approval_status = ""
        self.desktop_checkpoint_resume_args = {}

    def clear_browser_checkpoint(self):
        self._clear_browser_checkpoint()

    def clear_desktop_checkpoint(self):
        self._clear_desktop_checkpoint()

    def set_browser_checkpoint(
        self,
        *,
        reason: str,
        step: str,
        tool: str,
        target: str = "",
        approval_status: str = "not approved",
        resume_args: Dict[str, Any] | None = None,
    ):
        self.browser_checkpoint_pending = True
        self.browser_checkpoint_reason = str(reason).strip()[:180]
        self.browser_checkpoint_step = str(step).strip()[:120]
        self.browser_checkpoint_tool = str(tool).strip()[:80]
        self.browser_checkpoint_target = str(target).strip()[:160]
        self.browser_checkpoint_approval_status = str(approval_status).strip()[:40] or "not approved"
        self.browser_checkpoint_resume_args = self._normalize_checkpoint_args(resume_args or {})
        if self.browser_task_name:
            self.browser_task_status = "paused"
        if self.browser_workflow_name:
            self.browser_workflow_status = "paused"

    def set_desktop_checkpoint(
        self,
        *,
        reason: str,
        tool: str,
        target: str = "",
        evidence_id: str = "",
        approval_status: str = "not approved",
        resume_args: Dict[str, Any] | None = None,
    ):
        self.desktop_checkpoint_pending = True
        self.desktop_checkpoint_reason = str(reason).strip()[:180]
        self.desktop_checkpoint_tool = str(tool).strip()[:80]
        self.desktop_checkpoint_target = str(target).strip()[:180]
        self.desktop_checkpoint_evidence_id = str(evidence_id).strip()[:80]
        self.desktop_checkpoint_approval_status = str(approval_status).strip()[:40] or "not approved"
        self.desktop_checkpoint_resume_args = self._normalize_checkpoint_args(resume_args or {})

    def _latest_step_args(self) -> Dict[str, Any]:
        if not self.steps:
            return {}
        args = self.steps[-1].get("args", {})
        return args if isinstance(args, dict) else {}

    def _normalize_browser_workflow_pattern(self, value: Any) -> str:
        text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        return BROWSER_WORKFLOW_PATTERN_ALIASES.get(text, "")

    def _browser_step_label(self, tool_name: str) -> str:
        return BROWSER_TOOL_STEP_LABELS.get(tool_name, "browser step")

    def _browser_workflow_label(self, pattern: str = "", workflow_name: str = "") -> str:
        explicit_name = str(workflow_name).strip()
        if explicit_name:
            return explicit_name[:120]
        if self.browser_workflow_name:
            return self.browser_workflow_name[:120]
        resolved_pattern = self._normalize_browser_workflow_pattern(pattern) or self.browser_workflow_pattern
        if resolved_pattern:
            return BROWSER_WORKFLOW_PATTERN_LABELS.get(resolved_pattern, "Browser workflow")
        return ""

    def _resolve_browser_workflow_pattern(self, tool_name: str, args: Dict[str, Any]) -> str:
        explicit = self._normalize_browser_workflow_pattern(args.get("workflow_pattern", ""))
        if explicit and explicit != "browser_step_sequence":
            return explicit

        if tool_name == "browser_open_page":
            return explicit or "browser_step_sequence"

        if tool_name in {"browser_type", "browser_click"}:
            return "form_flow"

        if tool_name in {"browser_follow_link", "browser_extract_text"}:
            return "navigation_extract_flow"

        current = self._normalize_browser_workflow_pattern(self.browser_workflow_pattern)
        if current and current != "browser_step_sequence":
            return current

        if tool_name == "browser_inspect_page":
            if self.browser_last_successful_tool in {"browser_type", "browser_click"}:
                return "form_flow"
            if self.browser_last_successful_tool in {"browser_follow_link", "browser_extract_text"}:
                return "navigation_extract_flow"

        return explicit or current or "browser_step_sequence"

    def _resolve_browser_workflow_step(self, tool_name: str, args: Dict[str, Any]) -> str:
        requested = str(args.get("workflow_step", "")).strip()
        if requested:
            return requested[:120]
        return self._browser_step_label(tool_name)

    def _infer_browser_workflow_next_step(self, pattern: str, tool_name: str, current_step: str) -> str:
        current_label = current_step.lower()

        if pattern == "form_flow":
            if tool_name == "browser_open_page":
                return "inspect page"
            if tool_name == "browser_inspect_page":
                if current_label == "inspect result":
                    return ""
                return "type into field"
            if tool_name == "browser_type":
                return "click element"
            if tool_name == "browser_click":
                return "inspect result"
            return ""

        if pattern == "navigation_extract_flow":
            if tool_name == "browser_open_page":
                return "follow link"
            if tool_name == "browser_follow_link":
                return "inspect page"
            if tool_name == "browser_inspect_page":
                return "extract text"
            return ""

        if tool_name in {"browser_open_page", "browser_follow_link", "browser_click"}:
            return "inspect page"
        if tool_name == "browser_type":
            return "click element"
        return ""

    def _resolve_browser_workflow_status(
        self,
        *,
        ok: bool,
        approval_required: bool,
        next_step: str,
        paused: bool = False,
        resumed: bool = False,
    ) -> str:
        if paused:
            return "paused"
        if resumed:
            return "resumed"
        if approval_required:
            return "blocked"
        if not ok:
            return "needs_attention"
        if next_step:
            return "active"
        return "completed"
    def _clear_browser_expectation(self):
        self.browser_expected_target = ""
        self.browser_expected_url_contains = ""
        self.browser_expected_title_contains = ""
        self.browser_expected_text_contains = ""
        self.browser_expect_navigation = False

    def _set_browser_expected_state(self, expected_state: Dict[str, Any], *, met: bool):
        if not isinstance(expected_state, dict) or met:
            self._clear_browser_expectation()
            return

        self.browser_expected_target = str(expected_state.get("target", "")).strip()[:120]
        self.browser_expected_url_contains = str(expected_state.get("url_contains", "")).strip()[:160]
        self.browser_expected_title_contains = str(expected_state.get("title_contains", "")).strip()[:160]
        self.browser_expected_text_contains = str(expected_state.get("text_contains", "")).strip()[:160]
        self.browser_expect_navigation = bool(expected_state.get("expect_navigation", False))

    def _browser_expected_state_label(self) -> str:
        parts: List[str] = []
        if self.browser_expected_target:
            parts.append(f"target={self.browser_expected_target}")
        if self.browser_expected_url_contains:
            parts.append(f"url~{self.browser_expected_url_contains}")
        if self.browser_expected_title_contains:
            parts.append(f"title~{self.browser_expected_title_contains}")
        if self.browser_expected_text_contains:
            parts.append(f"text~{self.browser_expected_text_contains}")
        if self.browser_expect_navigation:
            parts.append("expects navigation")
        return ", ".join(parts)

    def _update_browser_context(self, tool_name: str, result: Dict[str, Any]):
        if not isinstance(result, dict):
            return

        args = self._latest_step_args()
        browser_state = result.get("browser_state", {}) if isinstance(result.get("browser_state", {}), dict) else {}
        session_id = str(browser_state.get("session_id", "") or result.get("session_id", "")).strip()
        if session_id:
            self.browser_session_id = session_id

        page = result.get("page", {}) if isinstance(result.get("page", {}), dict) else {}
        current_url = str(page.get("url", "") or browser_state.get("current_url", "") or result.get("current_url", "")).strip()
        current_title = str(page.get("title", "") or browser_state.get("current_title", "") or result.get("current_title", "")).strip()
        excerpt = str(page.get("visible_text_excerpt", "")).strip()
        extracted_text = str(result.get("text", "")).strip()

        if current_url:
            self.browser_current_url = current_url
        if current_title:
            self.browser_current_title = current_title
        if excerpt:
            self.browser_last_text_excerpt = excerpt[:400]
        elif extracted_text:
            self.browser_last_text_excerpt = extracted_text[:400]

        summary = str(result.get("summary", "")).strip()
        last_action = str(result.get("last_browser_action", "") or browser_state.get("last_action", "")).strip() or summary
        if last_action:
            self.browser_last_action = last_action[:220]
        if summary:
            self._add_browser_action(summary)
        elif last_action:
            self._add_browser_action(last_action)

        expected_state = result.get("expected_state", browser_state.get("expected_state", {}))
        expected_met = bool(result.get("expected_state_met", browser_state.get("expected_state_met", True)))
        self._set_browser_expected_state(expected_state if isinstance(expected_state, dict) else {}, met=expected_met)

        recovery = result.get("recovery", {}) if isinstance(result.get("recovery", {}), dict) else {}
        retry_count = result.get("retry_count", browser_state.get("retry_count", recovery.get("attempt_count", 0)))
        fallback_attempts = result.get("fallback_attempts", browser_state.get("fallback_attempts", recovery.get("fallback_count", 0)))
        try:
            self.browser_retry_count = max(0, int(retry_count))
        except (TypeError, ValueError):
            self.browser_retry_count = 0
        try:
            self.browser_fallback_attempts = max(0, int(fallback_attempts))
        except (TypeError, ValueError):
            self.browser_fallback_attempts = 0

        recovery_notes = result.get("recovery_notes", recovery.get("notes", []))
        if isinstance(recovery_notes, list):
            for note in recovery_notes[:4]:
                self._add_browser_recovery_note(note)

        ok = bool(result.get("ok", False))
        paused = bool(result.get("paused", False))
        resumed = bool(result.get("workflow_resumed", False))
        approval_required = bool(result.get("approval_required", False))
        approval_status = str(result.get("approval_status", "")).strip()
        checkpoint_required = bool(result.get("checkpoint_required", False))
        checkpoint_reason = str(result.get("checkpoint_reason", "")).strip()
        checkpoint_step = str(result.get("checkpoint_step", "") or args.get("workflow_step", "")).strip()
        checkpoint_target = str(result.get("checkpoint_target", "")).strip()
        checkpoint_tool = str(result.get("checkpoint_tool", "") or tool_name).strip()
        checkpoint_resume_args = self._normalize_checkpoint_args(result.get("checkpoint_resume_args", {}))
        previous_checkpoint_tool = self.browser_checkpoint_tool

        if tool_name == "browser_type" and ok:
            field = result.get("field", {}) if isinstance(result.get("field", {}), dict) else {}
            field_type = str(field.get("type", "")).strip().lower()
            if field_type != "password":
                selector_hint = str(field.get("selector_hint", "")).strip()
                input_label = (
                    str(field.get("name", "")).strip()
                    or str(field.get("placeholder", "")).strip()
                    or str(args.get("label", "")).strip()
                    or str(args.get("name", "")).strip()
                    or str(args.get("placeholder", "")).strip()
                    or selector_hint
                    or "input"
                )
                input_value = str(args.get("value", "")).strip()
                self.browser_last_input_label = input_label[:120]
                self.browser_last_input_selector = selector_hint[:120]
                self.browser_last_input_value = input_value[:240]
            else:
                self.browser_last_input_label = ""
                self.browser_last_input_selector = ""
                self.browser_last_input_value = ""

        if paused or (checkpoint_required and approval_required):
            if self.browser_last_input_value and "resume_value" not in checkpoint_resume_args:
                checkpoint_resume_args["resume_value"] = self.browser_last_input_value[:240]
                if self.browser_last_input_label:
                    checkpoint_resume_args["resume_label"] = self.browser_last_input_label[:120]
                if self.browser_last_input_selector:
                    checkpoint_resume_args["resume_selector"] = self.browser_last_input_selector[:120]
            self.browser_checkpoint_pending = True
            self.browser_checkpoint_reason = checkpoint_reason[:180]
            self.browser_checkpoint_step = checkpoint_step[:120]
            self.browser_checkpoint_tool = checkpoint_tool[:80]
            self.browser_checkpoint_target = checkpoint_target[:160]
            self.browser_checkpoint_approval_status = approval_status or "not approved"
            self.browser_checkpoint_resume_args = checkpoint_resume_args
        elif resumed or (approval_status == "approved" and previous_checkpoint_tool and tool_name == previous_checkpoint_tool):
            self._clear_browser_checkpoint()

        workflow_pattern = self._resolve_browser_workflow_pattern(tool_name, args)
        workflow_name = str(args.get("workflow_name", "")).strip()
        current_step = self._resolve_browser_workflow_step(tool_name, args) or self._browser_step_label(tool_name)
        next_step = str(args.get("workflow_next_step", "")).strip()[:120]

        if paused:
            next_step = (checkpoint_step or current_step)[:120]
        elif ok and not next_step:
            next_step = self._infer_browser_workflow_next_step(workflow_pattern, tool_name, current_step)
        elif not ok:
            next_step = next_step or current_step

        self.browser_workflow_pattern = workflow_pattern
        self.browser_workflow_name = self._browser_workflow_label(workflow_pattern, workflow_name)
        self.browser_workflow_current_step = current_step[:120]
        self.browser_workflow_next_step = next_step[:120]
        self.browser_workflow_status = self._resolve_browser_workflow_status(
            ok=ok,
            approval_required=approval_required,
            next_step=self.browser_workflow_next_step,
            paused=paused,
            resumed=resumed,
        )

        browser_task_name = infer_browser_task_name(
            tool_name,
            args,
            current_task_name=self.browser_task_name,
            goal=self.goal,
        )
        if browser_task_name:
            requested_task_step = str(args.get("browser_task_step", "")).strip() or current_step
            task_current_step = infer_browser_task_step(browser_task_name, tool_name, requested_task_step)
            task_next_step = infer_browser_task_next_step(
                browser_task_name,
                tool_name,
                task_current_step,
                ok=ok,
                paused=paused,
                approval_required=approval_required,
                explicit_next_step=args.get("browser_task_next_step", ""),
            )
            self.browser_task_name = browser_task_name[:80]
            self.browser_task_current_step = task_current_step[:120]
            self.browser_task_next_step = task_next_step[:120]
            self.browser_task_status = resolve_browser_task_status(
                ok=ok,
                paused=paused,
                approval_required=approval_required,
                next_step=self.browser_task_next_step,
                resumed=resumed,
            )

        step_label = self.browser_workflow_current_step or self._browser_step_label(tool_name)
        recovery_summary = str(result.get("recovery_summary", "")).strip()
        if ok and last_action:
            self.browser_last_successful_action = last_action[:220]
            self.browser_last_successful_tool = tool_name
            if resumed:
                self._add_browser_workflow_history(f"{step_label}: resumed after approval - {summary or last_action}")
                self._add_browser_workflow_recovery(f"{step_label}: resumed after approval.")
            else:
                self._add_browser_workflow_history(f"{step_label}: {summary or last_action}")
        elif paused:
            reason_text = checkpoint_reason or summary or str(result.get("error", "approval required")).strip()
            self._add_browser_workflow_history(f"{step_label}: paused for approval ({reason_text[:180]})")
        elif approval_required:
            blocked_text = summary or str(result.get("error", "approval required")).strip()
            self._add_browser_workflow_history(f"{step_label}: blocked pending approval ({blocked_text[:180]})")
        else:
            failure = str(result.get("error", summary or "browser action failed")).strip()
            self._add_browser_workflow_history(f"{step_label}: failed - {failure[:180]}")

    def _update_desktop_context(self, tool_name: str, result: Dict[str, Any]):
        if not isinstance(result, dict):
            return

        desktop_state = result.get("desktop_state", {}) if isinstance(result.get("desktop_state", {}), dict) else {}
        active_window = result.get("active_window", desktop_state.get("active_window", {}))
        if not isinstance(active_window, dict):
            active_window = {}
        windows = result.get("windows", desktop_state.get("windows", []))
        if not isinstance(windows, list):
            windows = []

        active_title = str(active_window.get("title", "")).strip()
        active_id = str(active_window.get("window_id", "")).strip()
        active_process = str(active_window.get("process_name", "")).strip()
        if active_title:
            self.desktop_active_window_title = active_title[:180]
        if active_id:
            self.desktop_active_window_id = active_id[:40]
        if active_process:
            self.desktop_active_window_process = active_process[:120]

        titles = [
            str(item.get("title", "")).strip()[:180]
            for item in windows
            if isinstance(item, dict) and str(item.get("title", "")).strip()
        ]
        if titles:
            self.desktop_windows = self._normalize_values(titles, limit=10, text_limit=180)

        screenshot_path = str(result.get("screenshot_path", "") or desktop_state.get("screenshot_path", "")).strip()
        screenshot_scope = str(result.get("screenshot_scope", "") or desktop_state.get("screenshot_scope", "")).strip()
        observation_token = str(result.get("observation_token", "") or desktop_state.get("observation_token", "")).strip()
        observed_at = str(result.get("observed_at", "") or desktop_state.get("observed_at", "")).strip()
        if screenshot_path:
            self.desktop_last_screenshot_path = screenshot_path[:260]
        if screenshot_scope:
            self.desktop_last_screenshot_scope = screenshot_scope[:40]

        evidence_ref = result.get("desktop_evidence_ref", {}) if isinstance(result.get("desktop_evidence_ref", {}), dict) else {}
        evidence_bundle = result.get("desktop_evidence", {}) if isinstance(result.get("desktop_evidence", {}), dict) else {}
        evidence_id = str(evidence_ref.get("evidence_id", "") or evidence_bundle.get("evidence_id", "")).strip()
        evidence_summary = str(evidence_ref.get("summary", "") or evidence_bundle.get("summary", "")).strip()
        evidence_bundle_path = str(evidence_ref.get("bundle_path", "") or evidence_bundle.get("bundle_path", "")).strip()
        evidence_reason = str(evidence_ref.get("reason", "") or evidence_bundle.get("reason", "")).strip()
        evidence_timestamp = str(evidence_ref.get("timestamp", "") or evidence_bundle.get("timestamp", "")).strip()
        if evidence_id:
            self.desktop_last_evidence_id = evidence_id[:80]
        if evidence_summary:
            self.desktop_last_evidence_summary = evidence_summary[:240]
        if evidence_bundle_path:
            self.desktop_last_evidence_bundle_path = evidence_bundle_path[:320]
        if evidence_reason:
            self.desktop_last_evidence_reason = evidence_reason[:40]
        if evidence_timestamp:
            self.desktop_last_evidence_timestamp = evidence_timestamp[:40]
        if observation_token:
            self.desktop_observation_token = observation_token[:120]
        if observed_at:
            self.desktop_observed_at = observed_at[:40]

        summary = str(result.get("summary", "")).strip()
        last_action = str(result.get("last_desktop_action", "")).strip() or summary
        if last_action:
            self.desktop_last_action = last_action[:220]
        if summary:
            self._add_desktop_action(summary)
        elif last_action:
            self._add_desktop_action(last_action)

        point = result.get("point", {}) if isinstance(result.get("point", {}), dict) else {}
        if point:
            self.desktop_last_point = f"({point.get('x', '')}, {point.get('y', '')})"[:80]

        typed_preview = str(result.get("typed_text_preview", "")).strip()
        if typed_preview:
            self.desktop_last_typed_text_preview = typed_preview[:80]

        checkpoint_target = str(result.get("checkpoint_target", "")).strip()
        if checkpoint_target:
            self.desktop_last_target_window = checkpoint_target[:180]
        elif active_title:
            self.desktop_last_target_window = active_title[:180]

        paused = bool(result.get("paused", False))
        approval_required = bool(result.get("approval_required", False))
        approval_status = str(result.get("approval_status", "")).strip()
        checkpoint_required = bool(result.get("checkpoint_required", False))
        checkpoint_reason = str(result.get("checkpoint_reason", "")).strip()
        checkpoint_tool = str(result.get("checkpoint_tool", "") or tool_name).strip()
        checkpoint_resume_args = self._normalize_checkpoint_args(result.get("checkpoint_resume_args", {}))

        if paused or (checkpoint_required and approval_required):
            self.set_desktop_checkpoint(
                reason=checkpoint_reason or summary or result.get("error", "desktop approval required"),
                tool=checkpoint_tool or tool_name,
                target=checkpoint_target,
                evidence_id=evidence_id,
                approval_status=approval_status or "not approved",
                resume_args=checkpoint_resume_args,
            )
        elif result.get("workflow_resumed") or (approval_status == "approved" and self.desktop_checkpoint_tool and tool_name == self.desktop_checkpoint_tool):
            self._clear_desktop_checkpoint()

    def _collect_desktop_activity(self, limit: int = 4) -> Dict[str, Any]:
        actions = self._normalize_values(self.desktop_recent_actions[-limit:], limit=limit, text_limit=220)
        uncertainties: List[str] = []
        for step in reversed(self.steps):
            tool_name = str(step.get("tool", "")).strip()
            if not tool_name.startswith("desktop_"):
                continue
            if step.get("status") not in {"failed", "paused"}:
                continue
            result = step.get("result", {}) if isinstance(step.get("result", {}), dict) else {}
            text = str(result.get("summary", "") or result.get("error", "")).strip()
            if not text or text in uncertainties:
                continue
            uncertainties.append(text[:220])
            if len(uncertainties) >= 2:
                break

        selected_evidence: Dict[str, Any] = {}
        checkpoint_evidence: Dict[str, Any] = {}
        try:
            from core.desktop_evidence import compact_evidence_preview, get_desktop_evidence_store

            store = get_desktop_evidence_store()
            selected_result = store.select_summary(
                task_evidence_id=self.desktop_last_evidence_id,
                observation_token=self.desktop_observation_token,
                active_window_title=self.desktop_active_window_title,
                target_window_title=self.desktop_last_target_window,
            )
            checkpoint_result = store.select_summary(
                checkpoint_evidence_id=self.desktop_checkpoint_evidence_id,
                checkpoint_target=self.desktop_checkpoint_target,
                active_window_title=self.desktop_active_window_title,
            )
            selected_evidence = compact_evidence_preview(selected_result.get("selected", {}))
            checkpoint_evidence = compact_evidence_preview(checkpoint_result.get("selected", {}))
        except Exception:
            selected_evidence = {}
            checkpoint_evidence = {}

        return {
            "windows": self._normalize_values(self.desktop_windows[-limit:], limit=limit, text_limit=180),
            "active_window_title": self.desktop_active_window_title[:180],
            "active_window_id": self.desktop_active_window_id[:40],
            "active_window_process": self.desktop_active_window_process[:120],
            "last_action": self.desktop_last_action[:220],
            "actions": actions,
            "last_target_window": self.desktop_last_target_window[:180],
            "last_point": self.desktop_last_point[:80],
            "last_typed_text_preview": self.desktop_last_typed_text_preview[:80],
            "observation_token": self.desktop_observation_token[:120],
            "observed_at": self.desktop_observed_at[:40],
            "screenshot_path": self.desktop_last_screenshot_path[:260],
            "screenshot_scope": self.desktop_last_screenshot_scope[:40],
            "evidence_id": self.desktop_last_evidence_id[:80],
            "evidence_summary": self.desktop_last_evidence_summary[:240],
            "evidence_bundle_path": self.desktop_last_evidence_bundle_path[:320],
            "evidence_reason": self.desktop_last_evidence_reason[:40],
            "evidence_timestamp": self.desktop_last_evidence_timestamp[:40],
            "selected_evidence": selected_evidence,
            "checkpoint_pending": self.desktop_checkpoint_pending,
            "checkpoint_reason": self.desktop_checkpoint_reason[:180],
            "checkpoint_tool": self.desktop_checkpoint_tool[:80],
            "checkpoint_target": self.desktop_checkpoint_target[:180],
            "checkpoint_evidence_id": self.desktop_checkpoint_evidence_id[:80],
            "checkpoint_evidence": checkpoint_evidence,
            "checkpoint_approval_status": self.desktop_checkpoint_approval_status[:40],
            "checkpoint_resume_ready": bool(self.desktop_checkpoint_resume_args),
            "uncertainties": uncertainties,
        }

        if recovery_summary:
            self._add_browser_workflow_recovery(f"{step_label}: {recovery_summary}")
    def _collect_browser_activity(self, limit: int = 4) -> Dict[str, Any]:
        actions = self._normalize_values(self.browser_recent_actions[-limit:], limit=limit, text_limit=220)
        recovery_notes = self._normalize_values(self.browser_recovery_notes[-3:], limit=3, text_limit=220)
        workflow_history = self._normalize_values(self.browser_workflow_history[-3:], limit=3, text_limit=220)
        workflow_recovery_history = self._normalize_values(self.browser_workflow_recovery_history[-3:], limit=3, text_limit=220)
        uncertainties: List[str] = []

        for step in reversed(self.steps):
            tool_name = str(step.get("tool", "")).strip()
            if not tool_name.startswith("browser_"):
                continue
            if step.get("status") not in {"failed", "paused"}:
                continue

            result = step.get("result", {}) if isinstance(step.get("result", {}), dict) else {}
            text = str(result.get("summary", "") or result.get("error", "")).strip()
            if not text or text in uncertainties:
                continue
            uncertainties.append(text[:220])
            if len(uncertainties) >= 2:
                break

        return {
            "session_id": self.browser_session_id,
            "current_url": self.browser_current_url,
            "current_title": self.browser_current_title,
            "excerpt": self.browser_last_text_excerpt[:280],
            "last_action": self.browser_last_action[:220],
            "last_successful_action": self.browser_last_successful_action[:220],
            "expected_state": self._browser_expected_state_label(),
            "retry_count": self.browser_retry_count,
            "fallback_attempts": self.browser_fallback_attempts,
            "actions": actions,
            "recovery_notes": recovery_notes,
            "task_name": self.browser_task_name[:80],
            "task_label": (browser_task_label(self.browser_task_name)[:120] if self.browser_task_name else ""),
            "task_step": self.browser_task_current_step[:120],
            "task_next_step": self.browser_task_next_step[:120],
            "task_status": self.browser_task_status,
            "workflow_name": self.browser_workflow_name[:120],
            "workflow_pattern": self.browser_workflow_pattern,
            "workflow_step": self.browser_workflow_current_step[:120],
            "workflow_next_step": self.browser_workflow_next_step[:120],
            "workflow_status": self.browser_workflow_status,
            "workflow_history": workflow_history,
            "workflow_recovery_history": workflow_recovery_history,
            "checkpoint_pending": self.browser_checkpoint_pending,
            "checkpoint_reason": self.browser_checkpoint_reason[:180],
            "checkpoint_step": self.browser_checkpoint_step[:120],
            "checkpoint_tool": self.browser_checkpoint_tool[:80],
            "checkpoint_target": self.browser_checkpoint_target[:160],
            "checkpoint_approval_status": self.browser_checkpoint_approval_status[:40],
            "checkpoint_resume_ready": bool(self.browser_checkpoint_resume_args),
            "uncertainties": uncertainties,
        }
    def _count_completed_tool(self, tool_name: str) -> int:
        return sum(
            1
            for step in self.steps
            if step.get("tool") == tool_name and step.get("status") == "completed"
        )

    def _display_path(self, path: str) -> str:
        raw = str(path).strip()
        if not raw:
            return ""
        try:
            candidate = Path(raw)
            if candidate.is_absolute():
                try:
                    return str(candidate.relative_to(Path.cwd())).replace("\\", "/")
                except ValueError:
                    return raw
        except Exception:
            return raw
        return raw.replace("\\", "/")

    def _collect_relevant_file_evidence(self, limit: int = 4) -> List[Dict[str, str]]:
        evidence: List[Dict[str, str]] = []
        seen_paths: set[str] = set()

        for step in reversed(self.steps):
            if len(evidence) >= limit:
                break

            tool_name = str(step.get("tool", "")).strip()
            result = step.get("result", {}) if isinstance(step.get("result", {}), dict) else {}

            if tool_name == "apply_approved_edits":
                for entry in result.get("applied_files", [])[:limit]:
                    if not isinstance(entry, dict):
                        continue
                    path = str(entry.get("path", "")).strip()
                    if not path or path in seen_paths:
                        continue
                    seen_paths.add(path)
                    evidence.append(
                        {
                            "display": str(entry.get("display_path", "")).strip() or self._display_path(path) or path,
                            "reason": "edited after explicit approval",
                        }
                    )
                    if len(evidence) >= limit:
                        break
                if len(evidence) >= limit:
                    break
                continue

            if tool_name == "read_file":
                path = str(result.get("path", "")).strip()
                if not path or path in seen_paths:
                    continue
                seen_paths.add(path)
                evidence.append(
                    {
                        "display": self._display_path(path),
                        "reason": "read directly",
                    }
                )
                continue

            if tool_name == "compare_files":
                path_a = str(result.get("path_a", "")).strip()
                path_b = str(result.get("path_b", "")).strip()
                pair_key = f"compare:{path_a}:{path_b}"
                if not path_a or not path_b or pair_key in seen_paths:
                    continue
                seen_paths.add(pair_key)
                differ = result.get("differ")
                if differ is True:
                    reason = "compared directly to confirm differences"
                elif differ is False:
                    reason = "compared directly; no differences found"
                else:
                    reason = "compared directly"
                evidence.append(
                    {
                        "display": f"{self._display_path(path_a)} <-> {self._display_path(path_b)}",
                        "reason": reason,
                    }
                )
                continue

            if tool_name != "inspect_project":
                continue

            for entry in result.get("recommended_files", [])[:limit]:
                path = str(entry.get("path", "")).strip()
                if not path or path in seen_paths:
                    continue
                seen_paths.add(path)
                evidence.append(
                    {
                        "display": str(entry.get("relative_path", "")).strip() or path,
                        "reason": str(entry.get("why", "recommended by inspect_project")).strip() or "recommended by inspect_project",
                    }
                )
                if len(evidence) >= limit:
                    break

        return evidence[:limit]

    def _collect_command_suggestions(self, limit: int = 3) -> List[Dict[str, str]]:
        suggestions: List[Dict[str, str]] = []
        seen_commands: set[str] = set()

        for step in reversed(self.steps):
            if step.get("tool") != "suggest_commands" or step.get("status") != "completed":
                continue

            result = step.get("result", {}) if isinstance(step.get("result", {}), dict) else {}
            for entry in result.get("suggestions", []):
                command = str(entry.get("command", "")).strip()
                if not command or command in seen_commands:
                    continue
                seen_commands.add(command)
                suggestions.append(
                    {
                        "command": command,
                        "purpose": str(entry.get("purpose", "")).strip(),
                        "risk_level": str(entry.get("risk_level", "low")).strip() or "low",
                        "why_relevant": str(entry.get("why_relevant", "")).strip(),
                    }
                )
                if len(suggestions) >= limit:
                    return suggestions

        return suggestions



    def _collect_applied_changes(self, limit: int = 4) -> Dict[str, Any]:
        for step in reversed(self.steps):
            if step.get("tool") != "apply_approved_edits" or step.get("status") != "completed":
                continue

            result = step.get("result", {}) if isinstance(step.get("result", {}), dict) else {}
            items: List[Dict[str, str]] = []
            for entry in result.get("applied_files", [])[:limit]:
                if not isinstance(entry, dict):
                    continue
                path = str(entry.get("path", "")).strip()
                display = str(entry.get("display_path", "")).strip() or self._display_path(path) or path
                if not display:
                    continue
                items.append(
                    {
                        "display": display,
                        "summary": str(entry.get("summary", "")).strip(),
                        "backup_path": self._display_path(str(entry.get("backup_path", "")).strip()) or str(entry.get("backup_path", "")).strip(),
                    }
                )

            unchanged = []
            for entry in result.get("unchanged_files", [])[:limit]:
                if not isinstance(entry, dict):
                    continue
                path = str(entry.get("path", "")).strip()
                display = str(entry.get("display_path", "")).strip() or self._display_path(path) or path
                if display:
                    unchanged.append(display)

            return {
                "items": items,
                "summary": str(result.get("summary", "")).strip(),
                "approval_status": str(result.get("approval_status", "")).strip(),
                "unchanged": unchanged,
            }

        return {
            "items": [],
            "summary": "",
            "approval_status": "",
            "unchanged": [],
        }


    def _collect_review_bundle(self, limit: int = 4) -> Dict[str, Any]:
        for step in reversed(self.steps):
            if step.get("tool") != "build_review_bundle" or step.get("status") != "completed":
                continue

            result = step.get("result", {}) if isinstance(step.get("result", {}), dict) else {}
            items: List[Dict[str, str]] = []
            for entry in result.get("files", [])[:limit]:
                if not isinstance(entry, dict):
                    continue
                path = str(entry.get("path", "")).strip()
                display = str(entry.get("display_path", "")).strip() or self._display_path(path) or path
                if not display:
                    continue
                items.append(
                    {
                        "display": display,
                        "why": str(entry.get("why_would_change", "")).strip(),
                        "description": str(entry.get("proposed_edit_description", "")).strip(),
                        "confidence": str(entry.get("confidence", "")).strip() or "unknown",
                    }
                )

            uncertainties = []
            for value in result.get("uncertainties", [])[:4]:
                text = str(value).strip()
                if text:
                    uncertainties.append(text)

            return {
                "items": items,
                "summary": str(result.get("summary", "")).strip(),
                "approval_status": str(result.get("approval_status", "not approved")).strip() or "not approved",
                "confidence": str(result.get("confidence", "")).strip(),
                "command_count": len(result.get("suggested_commands", [])),
                "uncertainties": uncertainties,
            }

        return {
            "items": [],
            "summary": "",
            "approval_status": "",
            "confidence": "",
            "command_count": 0,
            "uncertainties": [],
        }

    def _collect_proposed_edits(self, limit: int = 3) -> Dict[str, Any]:
        for step in reversed(self.steps):
            if step.get("tool") != "draft_proposed_edits" or step.get("status") != "completed":
                continue

            result = step.get("result", {}) if isinstance(step.get("result", {}), dict) else {}
            items: List[Dict[str, str]] = []
            for entry in result.get("drafts", [])[:limit]:
                if not isinstance(entry, dict):
                    continue
                path = str(entry.get("path", "")).strip()
                display = str(entry.get("display_path", "")).strip() or self._display_path(path) or path
                if not display:
                    continue
                items.append(
                    {
                        "display": display,
                        "description": str(entry.get("proposed_edit_description", "")).strip(),
                        "reason": str(entry.get("reason_for_change", "")).strip(),
                        "confidence": str(entry.get("confidence", "")).strip() or "unknown",
                    }
                )

            if not items:
                continue

            uncertainties = []
            for value in result.get("uncertainties", [])[:3]:
                text = str(value).strip()
                if text:
                    uncertainties.append(text)

            return {
                "items": items,
                "confidence": str(result.get("confidence", "")).strip(),
                "uncertainties": uncertainties,
            }

        return {
            "items": [],
            "confidence": "",
            "uncertainties": [],
        }

    def _collect_patch_plan(self, limit: int = 4) -> Dict[str, Any]:
        for step in reversed(self.steps):
            if step.get("tool") != "plan_patch" or step.get("status") != "completed":
                continue

            result = step.get("result", {}) if isinstance(step.get("result", {}), dict) else {}
            items: List[Dict[str, str]] = []
            for entry in result.get("files_to_change", [])[:limit]:
                if not isinstance(entry, dict):
                    continue
                path = str(entry.get("path", "")).strip()
                display = str(entry.get("display_path", "")).strip() or self._display_path(path) or path
                if not display:
                    continue
                items.append(
                    {
                        "display": display,
                        "order": str(entry.get("order", "")).strip() or "?",
                        "why": str(entry.get("why", "")).strip(),
                        "summary": str(entry.get("proposed_edit_summary", "")).strip(),
                    }
                )

            if not items:
                continue

            uncertainties = []
            for value in result.get("uncertainties", [])[:3]:
                text = str(value).strip()
                if text:
                    uncertainties.append(text)

            return {
                "items": items,
                "confidence": str(result.get("confidence", "")).strip(),
                "uncertainties": uncertainties,
            }

        return {
            "items": [],
            "confidence": "",
            "uncertainties": [],
        }

    def get_final_context(self) -> str:
        lines: List[str] = [f"Goal: {self.goal}"]

        control_snapshot = self.get_control_snapshot()
        behavior = control_snapshot.get("behavior", {})
        for line in behavior_context_lines(behavior):
            lines.append(line)

        session_memory = control_snapshot.get("session_memory", {}) if isinstance(control_snapshot.get("session_memory", {}), dict) else {}
        if session_memory.get("state_scope_id"):
            lines.append(f"Session scope: {session_memory.get('state_scope_id', '')}")
        if session_memory.get("rolling_summary") and session_memory.get("rolling_summary") != self.last_summary:
            lines.append(f"Session continuity summary: {session_memory.get('rolling_summary', '')}")
        task_control = control_snapshot.get("task_control", {}) if isinstance(control_snapshot.get("task_control", {}), dict) else {}
        if task_control.get("event"):
            lines.append(f"Task control state: {task_control.get('event', '')}")
        if task_control.get("reason"):
            lines.append(f"Task control reason: {task_control.get('reason', '')}")
        if task_control.get("replacement_goal"):
            lines.append(f"Replacement goal: {task_control.get('replacement_goal', '')}")
        if task_control.get("replacement_task_id"):
            lines.append(f"Replacement task id: {task_control.get('replacement_task_id', '')}")

        if self.last_summary:
            lines.append(f"Rolling summary: {self.last_summary}")

        evidence_files = self._collect_relevant_file_evidence(limit=4)
        if evidence_files:
            lines.append("Most relevant files used:")
            for item in evidence_files:
                lines.append(f"- {item['display']} ({item['reason']})")

        evidence_notes: List[str] = []
        for note in reversed(self.memory_notes):
            if note == "Loaded persisted session state.":
                continue
            if any(note == kept or note in kept for kept in evidence_notes):
                continue
            evidence_notes.append(note)
            if len(evidence_notes) >= 4:
                break

        if evidence_notes:
            lines.append("Recent evidence notes:")
            for note in reversed(evidence_notes):
                lines.append(f"- {note}")

        browser_activity = self._collect_browser_activity(limit=4)
        desktop_activity = self._collect_desktop_activity(limit=4)
        if (
            browser_activity["current_url"]
            or browser_activity["actions"]
            or browser_activity["recovery_notes"]
            or browser_activity["task_name"]
            or browser_activity["workflow_name"]
            or browser_activity["checkpoint_pending"]
        ):
            lines.append("Browser Actions / Observations:")
            if browser_activity["task_label"]:
                lines.append(f"- Browser task pattern: {browser_activity['task_label']}")
            elif browser_activity["task_name"]:
                lines.append(f"- Browser task pattern: {browser_activity['task_name']}")
            if browser_activity["task_step"]:
                lines.append(f"- Current browser task step: {browser_activity['task_step']}")
            if browser_activity["task_next_step"]:
                lines.append(f"- Next browser task step: {browser_activity['task_next_step']}")
            if browser_activity["task_status"]:
                lines.append(f"- Browser task status: {browser_activity['task_status']}")
            if browser_activity["workflow_name"]:
                lines.append(f"- Workflow: {browser_activity['workflow_name']}")
            if browser_activity["workflow_step"]:
                lines.append(f"- Current workflow step: {browser_activity['workflow_step']}")
            if browser_activity["workflow_next_step"]:
                lines.append(f"- Next workflow step: {browser_activity['workflow_next_step']}")
            if browser_activity["workflow_status"]:
                lines.append(f"- Workflow status: {browser_activity['workflow_status']}")
            if browser_activity["checkpoint_pending"]:
                lines.append("- Approval checkpoint pending: yes")
            if browser_activity["checkpoint_step"]:
                lines.append(f"- Approval checkpoint step: {browser_activity['checkpoint_step']}")
            if browser_activity["checkpoint_target"]:
                lines.append(f"- Approval checkpoint target: {browser_activity['checkpoint_target']}")
            if browser_activity["checkpoint_reason"]:
                lines.append(f"- Approval needed because: {browser_activity['checkpoint_reason']}")
            if browser_activity["checkpoint_resume_ready"]:
                lines.append("- Resume bundle available for the paused step")
            if browser_activity["last_successful_action"]:
                lines.append(f"- Last successful browser action: {browser_activity['last_successful_action']}")
            if browser_activity["current_title"] and browser_activity["current_url"]:
                lines.append(f"- Current page: {browser_activity['current_title']} ({browser_activity['current_url']})")
            elif browser_activity["current_url"]:
                lines.append(f"- Current page: {browser_activity['current_url']}")
            elif browser_activity["current_title"]:
                lines.append(f"- Current page: {browser_activity['current_title']}")
            if browser_activity["last_action"]:
                lines.append(f"- Last action: {browser_activity['last_action']}")
            if browser_activity["expected_state"]:
                lines.append(f"- Expected next state: {browser_activity['expected_state']}")
            if browser_activity["excerpt"]:
                lines.append(f"- Visible text excerpt: {browser_activity['excerpt']}")
            if browser_activity["retry_count"] or browser_activity["fallback_attempts"]:
                lines.append(f"- Last recovery: retries={browser_activity['retry_count']}, fallbacks={browser_activity['fallback_attempts']}")
            if browser_activity["workflow_history"]:
                lines.append("Workflow history:")
                for note in browser_activity["workflow_history"]:
                    lines.append(f"- {note}")
            elif browser_activity["actions"]:
                for action in browser_activity["actions"]:
                    lines.append(f"- {action}")
            if browser_activity["workflow_recovery_history"]:
                lines.append("Workflow recovery history:")
                for note in browser_activity["workflow_recovery_history"]:
                    lines.append(f"- {note}")
            if browser_activity["recovery_notes"]:
                lines.append("Browser recovery notes:")
                for note in browser_activity["recovery_notes"]:
                    lines.append(f"- {note}")
            if browser_activity["uncertainties"]:
                lines.append("Browser uncertainties:")
                for note in browser_activity["uncertainties"]:
                    lines.append(f"- {note}")

        if (
            desktop_activity["active_window_title"]
            or desktop_activity["windows"]
            or desktop_activity["actions"]
            or desktop_activity["last_action"]
            or desktop_activity["checkpoint_pending"]
            or desktop_activity["screenshot_path"]
        ):
            lines.append("Desktop Actions / Observations:")
            if desktop_activity["active_window_title"] and desktop_activity["active_window_process"]:
                lines.append(
                    f"- Active window: {desktop_activity['active_window_title']} ({desktop_activity['active_window_process']})"
                )
            elif desktop_activity["active_window_title"]:
                lines.append(f"- Active window: {desktop_activity['active_window_title']}")
            if desktop_activity["active_window_id"]:
                lines.append(f"- Active window id: {desktop_activity['active_window_id']}")
            if desktop_activity["windows"]:
                lines.append("- Visible windows inspected:")
                for title in desktop_activity["windows"]:
                    lines.append(f"- {title}")
            if desktop_activity["last_target_window"]:
                lines.append(f"- Last desktop target: {desktop_activity['last_target_window']}")
            if desktop_activity["checkpoint_pending"]:
                lines.append("- Desktop approval checkpoint pending: yes")
            if desktop_activity["checkpoint_tool"]:
                lines.append(f"- Pending desktop tool: {desktop_activity['checkpoint_tool']}")
            if desktop_activity["checkpoint_target"]:
                lines.append(f"- Pending desktop target: {desktop_activity['checkpoint_target']}")
            if desktop_activity["checkpoint_reason"]:
                lines.append(f"- Desktop approval needed because: {desktop_activity['checkpoint_reason']}")
            if desktop_activity["checkpoint_resume_ready"]:
                lines.append("- Resume bundle available for the paused desktop step")
            if desktop_activity["last_action"]:
                lines.append(f"- Last desktop action: {desktop_activity['last_action']}")
            if desktop_activity["last_point"]:
                lines.append(f"- Last point: {desktop_activity['last_point']}")
            if desktop_activity["last_typed_text_preview"]:
                lines.append(f"- Last typed text preview: {desktop_activity['last_typed_text_preview']}")
            if desktop_activity["screenshot_path"]:
                if desktop_activity["screenshot_scope"]:
                    lines.append(
                        f"- Screenshot captured: {desktop_activity['screenshot_path']} ({desktop_activity['screenshot_scope']})"
                    )
                else:
                    lines.append(f"- Screenshot captured: {desktop_activity['screenshot_path']}")
            if desktop_activity["observed_at"]:
                lines.append(f"- Desktop observed at: {desktop_activity['observed_at']}")
            if desktop_activity["actions"]:
                lines.append("Desktop action history:")
                for action in desktop_activity["actions"]:
                    lines.append(f"- {action}")
            if desktop_activity["uncertainties"]:
                lines.append("Desktop uncertainties:")
                for note in desktop_activity["uncertainties"]:
                    lines.append(f"- {note}")

        applied_changes = self._collect_applied_changes(limit=4)
        if applied_changes["items"] or applied_changes["summary"]:
            lines.append("Applied Changes:")
            if applied_changes["summary"]:
                lines.append(f"- {applied_changes['summary']}")
            if applied_changes["approval_status"]:
                lines.append(f"- Approval status used: {applied_changes['approval_status']}")
            for item in applied_changes["items"]:
                details = item["summary"]
                if item["backup_path"]:
                    details += f" Backup: {item['backup_path']}"
                lines.append(f"- {item['display']} -- {details}")
            if applied_changes["unchanged"]:
                lines.append("Unchanged approved targets:")
                for display in applied_changes["unchanged"][:2]:
                    lines.append(f"- {display}")

        review_bundle = self._collect_review_bundle(limit=4)
        if review_bundle["items"] or review_bundle["summary"]:
            lines.append("Review Bundle / Approval Needed:")
            if review_bundle["summary"]:
                lines.append(f"- {review_bundle['summary']}")
            if review_bundle["approval_status"]:
                lines.append(f"- Approval status: {review_bundle['approval_status']}")
            if applied_changes["items"]:
                lines.append("- Approved/applied changes: see Applied Changes")
            else:
                lines.append("- Approved/applied changes: none")
            for item in review_bundle["items"]:
                details = item["description"]
                if item["why"]:
                    details += f" Why: {item['why']}"
                lines.append(f"- [{item['confidence']}] {item['display']} -- {details}")
            if review_bundle["command_count"]:
                lines.append(f"- Suggested commands bundled: {review_bundle['command_count']}")
            if review_bundle["confidence"]:
                lines.append(f"- Review bundle confidence: {review_bundle['confidence']}")
            if review_bundle["uncertainties"]:
                lines.append("Review bundle uncertainties:")
                for note in review_bundle["uncertainties"][:2]:
                    lines.append(f"- {note}")

        proposed_edits = self._collect_proposed_edits(limit=3)
        if proposed_edits["items"]:
            lines.append("Proposed Edits (Not Applied):")
            for item in proposed_edits["items"]:
                details = item["description"]
                if item["reason"]:
                    details += f" Why: {item['reason']}"
                lines.append(f"- [{item['confidence']}] {item['display']} -- {details}")
            if proposed_edits["confidence"]:
                lines.append(f"Proposed Edit Confidence: {proposed_edits['confidence']}")
            if proposed_edits["uncertainties"]:
                lines.append("Proposed Edit Uncertainties:")
                for note in proposed_edits["uncertainties"][:2]:
                    lines.append(f"- {note}")

        patch_plan = self._collect_patch_plan(limit=4)
        if patch_plan["items"]:
            lines.append("Planned changes (not applied):")
            for item in patch_plan["items"]:
                details = item["summary"]
                if item["why"]:
                    details += f" Why: {item['why']}"
                lines.append(f"- [{item['order']}] {item['display']} -- {details}")
            if patch_plan["confidence"]:
                lines.append(f"Patch plan confidence: {patch_plan['confidence']}")
            if patch_plan["uncertainties"]:
                lines.append("Patch plan uncertainties:")
                for note in patch_plan["uncertainties"][:2]:
                    lines.append(f"- {note}")

        command_suggestions = self._collect_command_suggestions(limit=3)
        if command_suggestions:
            lines.append("Suggested commands (not executed):")
            for item in command_suggestions:
                details = item["purpose"]
                if item["why_relevant"]:
                    details += f" Why: {item['why_relevant']}"
                lines.append(f"- [{item['risk_level']}] {item['command']} -- {details}")

        read_count = self._count_completed_tool("read_file")
        compare_count = self._count_completed_tool("compare_files")
        inspect_count = self._count_completed_tool("inspect_project")
        apply_count = self._count_completed_tool("apply_approved_edits")
        browser_count = sum(
            1
            for step in self.steps
            if str(step.get("tool", "")).startswith("browser_") and step.get("status") == "completed"
        )
        direct_evidence_count = read_count + compare_count
        if apply_count:
            if direct_evidence_count or inspect_count:
                confidence = "High confidence because approved edits were applied successfully with supporting project evidence."
            else:
                confidence = "Moderate confidence because approved edits were applied successfully, but supporting reads or comparisons were limited."
        elif direct_evidence_count >= 2:
            if inspect_count:
                confidence = "High confidence from multiple direct file reads or comparisons plus project inspection."
            else:
                confidence = "High confidence from multiple direct file reads or comparisons."
        elif direct_evidence_count == 1:
            if inspect_count:
                confidence = "Moderate confidence from one direct file read or comparison plus project inspection."
            else:
                confidence = "Moderate confidence from one direct file read or comparison."
        elif inspect_count:
            confidence = "Moderate confidence from project inspection, but direct file reads or comparisons were limited."
        elif browser_count >= 3:
            confidence = "Moderate confidence from repeated browser observations."
        elif browser_count >= 2:
            confidence = "Limited to moderate confidence from multiple browser observations."
        elif browser_count == 1:
            confidence = "Limited confidence from one browser observation."
        else:
            confidence = "Limited confidence because little direct evidence was collected."
        lines.append(f"Confidence summary: {confidence}")

        if self.priority_files:
            lines.append("Next files to inspect if needed:")
            for path in self.priority_files[:3]:
                lines.append(f"- {path}")

        return "\n".join(lines)

    def update_memory_from_tool(self, tool_name: str, result: Dict[str, Any]):
        if not isinstance(result, dict):
            return

        if tool_name.startswith("browser_"):
            self._update_browser_context(tool_name, result)
        elif tool_name.startswith("desktop_"):
            self._update_desktop_context(tool_name, result)

        if tool_name == "read_file":
            path = str(result.get("path", "")).strip()
            if path:
                self._push_unique(self.known_files, path)
                self.priority_files = [p for p in self.priority_files if p != path]

        elif tool_name == "compare_files":
            path_a = str(result.get("path_a", "")).strip()
            path_b = str(result.get("path_b", "")).strip()
            if path_a:
                self._push_unique(self.known_files, path_a)
            if path_b:
                self._push_unique(self.known_files, path_b)

        elif tool_name == "suggest_commands":
            for entry in result.get("suggestions", [])[:4]:
                for raw_target in entry.get("target_paths", [])[:3]:
                    target = str(raw_target).strip()
                    if not target:
                        continue
                    try:
                        if Path(target).is_dir():
                            self._push_unique(self.known_dirs, target)
                        else:
                            self._push_unique(self.known_files, target)
                    except Exception:
                        if Path(target).suffix:
                            self._push_unique(self.known_files, target)
                        else:
                            self._push_unique(self.known_dirs, target)

        elif tool_name == "apply_approved_edits":
            applied_paths: List[str] = []
            for entry in result.get("applied_files", [])[:6]:
                path = str(entry.get("path", "")).strip()
                if not path:
                    continue
                applied_paths.append(path)
                self._push_unique(self.known_files, path)
            for path in applied_paths:
                self.priority_files = [item for item in self.priority_files if item != path]

        elif tool_name == "build_review_bundle":
            for raw_path in result.get("target_files", [])[:6]:
                path = str(raw_path).strip()
                if path:
                    self._push_unique(self.known_files, path)

        elif tool_name == "draft_proposed_edits":
            for entry in result.get("drafts", [])[:6]:
                path = str(entry.get("path", "")).strip()
                if path:
                    self._push_unique(self.known_files, path)

        elif tool_name == "plan_patch":
            for entry in result.get("files_to_change", [])[:6]:
                path = str(entry.get("path", "")).strip()
                if path:
                    self._push_unique(self.known_files, path)

        elif tool_name == "list_files":
            base = str(result.get("path", "")).strip()
            if base:
                self._push_unique(self.known_dirs, base)

            for entry in result.get("entries", [])[:20]:
                p = str(entry.get("path", "")).strip()
                t = str(entry.get("type", "")).strip()
                if not p:
                    continue
                if t == "dir":
                    self._push_unique(self.known_dirs, p)
                else:
                    self._push_unique(self.known_files, p)

        elif tool_name == "search_files":
            base = str(result.get("path", "")).strip()
            query = str(result.get("query", "")).strip()
            if base:
                self._push_unique(self.known_dirs, base)
            if query:
                self.add_note(f"Searched for '{query}' in {base}")

            for entry in result.get("matches", [])[:20]:
                p = str(entry.get("path", "")).strip()
                t = str(entry.get("type", "")).strip()
                if not p:
                    continue
                if t == "dir":
                    self._push_unique(self.known_dirs, p)
                else:
                    self._push_unique(self.known_files, p)

        elif tool_name == "inspect_project":
            base = str(result.get("path", "")).strip()
            focus = str(result.get("focus", "")).strip()
            from_cache = bool(result.get("from_cache", False))
            cache = result.get("cache", {})
            age_seconds = cache.get("age_seconds", 0)
            recommended_paths: List[str] = []

            if base:
                self._push_unique(self.known_dirs, base)
            if from_cache and base:
                self.add_note(f"Reused cached inspection for {base} ({age_seconds}s old)")
            elif focus:
                self.add_note(f"Inspected project focus '{focus}' in {base}")

            for entry in result.get("top_level", [])[:20]:
                p = str(entry.get("path", "")).strip()
                t = str(entry.get("type", "")).strip()
                if not p:
                    continue
                if t == "dir":
                    self._push_unique(self.known_dirs, p)
                else:
                    self._push_unique(self.known_files, p)

            for entry in result.get("sampled_directories", [])[:20]:
                p = str(entry.get("path", "")).strip()
                if p:
                    self._push_unique(self.known_dirs, p)

            for entry in result.get("likely_files", [])[:20]:
                p = str(entry.get("path", "")).strip()
                if p:
                    self._push_unique(self.known_files, p)

            for entry in result.get("recommended_files", [])[:6]:
                p = str(entry.get("path", "")).strip()
                if not p:
                    continue
                recommended_paths.append(p)
                self._push_unique(self.known_files, p)

            self._set_priority_files(recommended_paths)

        elif tool_name == "run_shell":
            cmd = str(result.get("command", "")).strip()
            rc = result.get("returncode")
            if cmd:
                self.add_note(f"Ran shell command: {cmd} (returncode={rc})")

    def add_note(self, note: str, limit: int = 20):
        note = str(note).strip()
        if not note:
            return
        if note not in self.memory_notes:
            self.memory_notes.append(note)
        if len(self.memory_notes) > limit:
            del self.memory_notes[:-limit]

    def set_summary(self, summary: str):
        self.last_summary = str(summary).strip()[:600]

    def set_task_control(
        self,
        *,
        event: str = "",
        reason: str = "",
        resume_available: bool = False,
        replacement_task_id: str = "",
        replacement_goal: str = "",
    ):
        self.task_control_event = str(event).strip()[:60]
        self.task_control_reason = str(reason).strip()[:240]
        self.task_resume_available = bool(resume_available)
        self.task_replacement_task_id = str(replacement_task_id).strip()[:60]
        self.task_replacement_goal = str(replacement_goal).strip()[:MAX_TASK_REPLACEMENT_GOAL_CHARS]

    def clear_task_control(self):
        self.set_task_control()

    def get_behavior_contract(self, *, current_step: str = "", pending_approval: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if pending_approval is None:
            pending_approval = {}
            if self.browser_checkpoint_pending:
                pending_approval = {
                    "kind": "browser_checkpoint",
                    "reason": self.browser_checkpoint_reason,
                    "summary": self._browser_expected_state_label(),
                }
            elif self.desktop_checkpoint_pending:
                pending_approval = {
                    "kind": "desktop_action",
                    "reason": self.desktop_checkpoint_reason,
                    "summary": self.desktop_checkpoint_target or self.desktop_last_action,
                }
        return derive_behavior_contract(
            status=self.status,
            pending_approval=pending_approval,
            current_step=current_step,
            browser_task_name=self.browser_task_name,
            browser_workflow_name=self.browser_workflow_name,
            control_event=self.task_control_event,
            control_reason=self.task_control_reason,
            resume_available=self.task_resume_available,
            replacement_task_id=self.task_replacement_task_id,
        )

    def summarize_result_for_memory(self, tool_name: str, result: Any) -> str:
        if not isinstance(result, dict):
            return f"{tool_name}: completed."

        if tool_name == "read_file":
            path = result.get("path", "")
            truncated = result.get("truncated", False)
            return f"Read file: {path}" + (" (truncated)" if truncated else "")

        if tool_name == "compare_files":
            path_a = self._display_path(result.get("path_a", "")) or str(result.get("path_a", "")).strip()
            path_b = self._display_path(result.get("path_b", "")) or str(result.get("path_b", "")).strip()
            pair_text = f"{path_a} vs {path_b}".strip()
            if not result.get("ok", False):
                error = str(result.get("error", "comparison failed")).strip()
                return f"Compare failed for {pair_text}: {error}" if pair_text else f"Compare failed: {error}"

            summary = str(result.get("summary", "")).strip()
            if summary:
                return f"Compared {pair_text}: {summary}"

            differ = result.get("differ")
            if differ is True:
                return f"Compared {pair_text}: files differ"
            if differ is False:
                return f"Compared {pair_text}: no differences found"
            return f"Compared {pair_text}"

        if tool_name.startswith("desktop_"):
            summary = str(result.get("summary", "")).strip()
            target = self.desktop_last_target_window or self.desktop_active_window_title
            context_label = f" [{target}]" if target else ""
            prefix = f"Desktop{context_label}"
            if result.get("paused"):
                return f"{prefix} paused for approval: {summary or result.get('checkpoint_reason', '') or result.get('error', 'approval required')}"
            if not result.get("ok", False):
                if result.get("approval_required"):
                    return f"{prefix} action blocked pending approval: {summary or result.get('error', 'approval required')}"
                return f"{prefix} action failed: {str(result.get('error', summary or 'desktop action failed')).strip()}"
            if result.get("workflow_resumed"):
                return f"{prefix} resumed after approval: {summary or 'desktop step completed'}"
            if summary:
                return f"{prefix}: {summary}"
            return f"{prefix} step completed."

        if tool_name.startswith("browser_"):
            summary = str(result.get("summary", "")).strip()
            recovery_summary = str(result.get("recovery_summary", "")).strip()
            checkpoint_reason = str(result.get("checkpoint_reason", "")).strip()
            browser_context_parts: List[str] = []
            task_label = browser_task_label(self.browser_task_name)
            if task_label:
                browser_context_parts.append(task_label)
            elif self.browser_workflow_name:
                browser_context_parts.append(self.browser_workflow_name)
            if self.browser_task_current_step:
                browser_context_parts.append(self.browser_task_current_step)
            elif self.browser_workflow_current_step:
                browser_context_parts.append(self.browser_workflow_current_step)
            context_label = f" [{' / '.join(browser_context_parts)}]" if browser_context_parts else ""
            prefix = f"Browser{context_label}"
            if result.get("paused"):
                message = f"{prefix} paused for approval: {summary or checkpoint_reason or result.get('error', 'approval required')}"
                if recovery_summary:
                    message += f" Recovery: {recovery_summary}"
                return message
            if not result.get("ok", False):
                if result.get("approval_required"):
                    blocked = f"{prefix} action blocked pending approval: {summary or result.get('error', 'approval required')}"
                    if recovery_summary:
                        blocked += f" Recovery: {recovery_summary}"
                    return blocked
                error = str(result.get("error", summary or "browser action failed")).strip()
                message = f"{prefix} action failed: {error}"
                if recovery_summary:
                    message += f" Recovery: {recovery_summary}"
                return message
            if result.get("workflow_resumed"):
                message = f"{prefix} resumed after approval: {summary or 'browser step completed'}"
                if recovery_summary and recovery_summary not in message:
                    message += f" Recovery: {recovery_summary}"
                return message
            if summary:
                if recovery_summary and recovery_summary not in summary:
                    return f"{prefix}: {summary} Recovery: {recovery_summary}"
                return f"{prefix}: {summary}"
            if recovery_summary:
                return f"{prefix} step completed. Recovery: {recovery_summary}"
            return f"{prefix} step completed."

        if tool_name == "suggest_commands":
            if not result.get("ok", False):
                error = str(result.get("error", "command suggestion failed")).strip()
                return f"Command suggestion failed: {error}"

            suggestions = result.get("suggestions", [])
            if not suggestions:
                return "Suggested no safe commands."

            first = suggestions[0] if isinstance(suggestions[0], dict) else {}
            purpose = str(first.get("purpose", "manual inspection")).strip() or "manual inspection"
            risk = str(first.get("risk_level", "low")).strip() or "low"
            return f"Suggested {len(suggestions)} read-only command(s) (not run); first [{risk}]: {purpose}"

        if tool_name == "apply_approved_edits":
            if not result.get("ok", False):
                error = str(result.get("error", "approved edit apply failed")).strip()
                return f"Apply approved edits failed: {error}"

            applied_files = result.get("applied_files", [])
            unchanged_count = int(result.get("unchanged_count", 0) or 0)
            if not applied_files:
                return f"Applied no file changes; {unchanged_count} approved target(s) already matched current content."

            first_paths = ", ".join(
                self._display_path(item.get("path", "")) or str(item.get("display_path", "")).strip()
                for item in applied_files[:2]
                if isinstance(item, dict)
            )
            return f"Applied {len(applied_files)} approved edit(s); updated {first_paths} and created .bak backups"

        if tool_name == "build_review_bundle":
            if not result.get("ok", False):
                error = str(result.get("error", "review bundle failed")).strip()
                return f"Review bundle failed: {error}"

            target_files = result.get("target_files", [])
            confidence = str(result.get("confidence", "")).strip() or "unknown"
            approval_status = str(result.get("approval_status", "not approved")).strip() or "not approved"
            if not target_files:
                return f"Prepared no review bundle; approval status {approval_status}."

            first_paths = ", ".join(
                self._display_path(path) or str(path).strip()
                for path in target_files[:2]
                if str(path).strip()
            )
            return f"Prepared review bundle for {len(target_files)} file(s); approval status {approval_status}; start with {first_paths} [{confidence}]"

        if tool_name == "draft_proposed_edits":
            if not result.get("ok", False):
                error = str(result.get("error", "draft edit planning failed")).strip()
                return f"Draft edit planning failed: {error}"

            drafts = result.get("drafts", [])
            if not drafts:
                return "Drafted no proposed edits."

            first_paths = ", ".join(
                self._display_path(item.get("path", "")) or str(item.get("display_path", "")).strip()
                for item in drafts[:2]
                if isinstance(item, dict)
            )
            confidence = str(result.get("confidence", "")).strip() or "unknown"
            return f"Drafted {len(drafts)} proposed edit(s) (not applied); start with {first_paths} [{confidence}]"

        if tool_name == "plan_patch":
            if not result.get("ok", False):
                error = str(result.get("error", "patch planning failed")).strip()
                return f"Patch planning failed: {error}"

            files_to_change = result.get("files_to_change", [])
            if not files_to_change:
                return "Planned no file changes."

            first_paths = ", ".join(
                self._display_path(item.get("path", "")) or str(item.get("display_path", "")).strip()
                for item in files_to_change[:2]
                if isinstance(item, dict)
            )
            confidence = str(result.get("confidence", "")).strip() or "unknown"
            return f"Planned {len(files_to_change)} file change(s) (not applied); start with {first_paths} [{confidence}]"

        if tool_name == "list_files":
            path = result.get("path", "")
            count = result.get("count", 0)
            recursive = result.get("recursive", False)
            return f"Listed {count} entries in {path} (recursive={recursive})"

        if tool_name == "search_files":
            path = result.get("path", "")
            query = result.get("query", "")
            count = result.get("count", 0)
            return f"Found {count} matches for '{query}' in {path}"

        if tool_name == "inspect_project":
            path = result.get("path", "")
            cache = result.get("cache", {})
            recommended_files = result.get("recommended_files", [])
            shortlist = ", ".join(
                item.get("relative_path", "")
                for item in recommended_files[:3]
                if item.get("relative_path")
            )

            if result.get("from_cache", False):
                age_seconds = cache.get("age_seconds", 0)
                if shortlist:
                    return f"Reused cached inspection for {path} ({age_seconds}s old); read {shortlist} first"
                return f"Reused cached inspection for {path} ({age_seconds}s old)"

            if shortlist:
                return f"Inspected project {path}; read {shortlist} first"

            stats = result.get("stats", {})
            likely_files = result.get("likely_files", [])
            scanned = stats.get("scanned_entries", 0)
            return f"Inspected project {path}: scanned {scanned} entries and identified {len(likely_files)} likely files"

        if tool_name == "run_shell":
            cmd = result.get("command", "")
            rc = result.get("returncode", "?")
            return f"Ran shell command '{cmd}' with returncode {rc}"

        return f"{tool_name}: completed."

    def get_control_snapshot(self) -> Dict[str, Any]:
        browser_activity = self._collect_browser_activity(limit=4)
        desktop_activity = self._collect_desktop_activity(limit=4)
        review_bundle = self._collect_review_bundle(limit=3)
        applied_changes = self._collect_applied_changes(limit=2)
        recent_notes = self._normalize_values(self.memory_notes[-6:], limit=6, text_limit=240)
        recovery_notes = self._normalize_values(
            list(browser_activity.get("recovery_notes", []))
            + list(browser_activity.get("workflow_recovery_history", []))
            + list(desktop_activity.get("uncertainties", [])),
            limit=6,
            text_limit=220,
        )

        recent_steps: List[str] = []
        for step in self.steps[-6:]:
            tool = str(step.get("tool", step.get("type", "unknown"))).strip() or "unknown"
            status = str(step.get("status", "unknown")).strip() or "unknown"
            message = str(step.get("message", "")).strip()
            if message and tool == "system":
                recent_steps.append(f"{tool} [{status}] {message[:180]}")
            else:
                recent_steps.append(f"{tool} [{status}]")

        current_step = (
            browser_activity.get("checkpoint_step", "")
            or browser_activity.get("task_step", "")
            or browser_activity.get("workflow_step", "")
            or desktop_activity.get("checkpoint_tool", "")
            or desktop_activity.get("last_action", "")
        )
        if not current_step and self.steps:
            last_step = self.steps[-1]
            current_step = str(last_step.get("tool", last_step.get("type", "waiting"))).strip() or "waiting"

        pending_approval = {
            "kind": "",
            "reason": "",
            "step": "",
            "tool": "",
            "target": "",
            "summary": "",
            "approval_status": "",
            "evidence_id": "",
            "evidence_summary": "",
            "evidence_preview": {},
            "target_files": [],
        }
        if browser_activity.get("checkpoint_pending"):
            pending_approval = {
                "kind": "browser_checkpoint",
                "reason": str(browser_activity.get("checkpoint_reason", "")).strip(),
                "step": str(browser_activity.get("checkpoint_step", "")).strip(),
                "tool": str(browser_activity.get("checkpoint_tool", "")).strip(),
                "target": str(browser_activity.get("checkpoint_target", "")).strip(),
                "summary": str(browser_activity.get("expected_state", "")).strip(),
                "approval_status": str(browser_activity.get("checkpoint_approval_status", "not approved")).strip() or "not approved",
                "evidence_id": "",
                "evidence_summary": "",
                "evidence_preview": {},
                "target_files": [],
            }
        elif desktop_activity.get("checkpoint_pending"):
            pending_approval = {
                "kind": "desktop_action",
                "reason": str(desktop_activity.get("checkpoint_reason", "")).strip(),
                "step": DESKTOP_TOOL_STEP_LABELS.get(
                    str(desktop_activity.get("checkpoint_tool", "")).strip(),
                    str(desktop_activity.get("checkpoint_tool", "")).strip(),
                ),
                "tool": str(desktop_activity.get("checkpoint_tool", "")).strip(),
                "target": str(desktop_activity.get("checkpoint_target", "")).strip(),
                "summary": str(desktop_activity.get("last_action", "") or desktop_activity.get("checkpoint_target", "")).strip(),
                "approval_status": str(desktop_activity.get("checkpoint_approval_status", "not approved")).strip() or "not approved",
                "evidence_id": str(desktop_activity.get("checkpoint_evidence_id", "")).strip(),
                "evidence_summary": str(desktop_activity.get("checkpoint_evidence", {}).get("summary", "")).strip(),
                "evidence_preview": desktop_activity.get("checkpoint_evidence", {}),
                "target_files": [],
            }
        elif (
            self.status in {"running", "paused", "needs_attention"}
            and review_bundle.get("items")
            and review_bundle.get("approval_status") == "not approved"
            and not applied_changes.get("items")
        ):
            pending_approval = {
                "kind": "review_bundle",
                "reason": "Planned and drafted changes are waiting for human approval.",
                "step": "review bundle",
                "tool": "build_review_bundle",
                "target": "",
                "summary": str(review_bundle.get("summary", "")).strip(),
                "approval_status": "not approved",
                "evidence_id": "",
                "evidence_summary": "",
                "evidence_preview": {},
                "target_files": [item.get("display", "") for item in review_bundle.get("items", []) if item.get("display")],
            }

        behavior = derive_behavior_contract(
            status=self.status,
            pending_approval=pending_approval,
            current_step=current_step,
            browser_task_name=browser_activity.get("task_name", ""),
            browser_workflow_name=browser_activity.get("workflow_name", ""),
            control_event=self.task_control_event,
            control_reason=self.task_control_reason,
            resume_available=self.task_resume_available,
            replacement_task_id=self.task_replacement_task_id,
        )
        session_memory = {
            "state_scope_id": self.state_scope_id,
            "known_file_count": len(self.known_files),
            "known_dir_count": len(self.known_dirs),
            "priority_files": self._normalize_values(self.priority_files[:4], limit=4, text_limit=180),
            "memory_note_count": len(self.memory_notes),
            "rolling_summary": self.last_summary,
        }

        return {
            "state_scope_id": self.state_scope_id,
            "goal": self.goal,
            "status": self.status,
            "rolling_summary": self.last_summary,
            "current_step": current_step,
            "paused": bool(
                self.status == "paused"
                or browser_activity.get("checkpoint_pending")
                or desktop_activity.get("checkpoint_pending")
            ),
            "recent_notes": recent_notes,
            "recent_steps": recent_steps,
            "recovery_notes": recovery_notes,
            "browser": browser_activity,
            "desktop": desktop_activity,
            "review_bundle": review_bundle,
            "pending_approval": pending_approval,
            "behavior": behavior,
            "action_policy": behavior.get("action_policy", {}),
            "human_control": behavior.get("human_control", {}),
            "session_memory": session_memory,
            "task_control": {
                "event": self.task_control_event,
                "reason": self.task_control_reason,
                "resume_available": bool(self.task_resume_available),
                "replacement_task_id": self.task_replacement_task_id,
                "replacement_goal": self.task_replacement_goal,
            },
        }

    def get_observation(self) -> str:
        recent_steps = self.steps[-5:]

        lines: List[str] = []
        lines.append(f"Goal: {self.goal}")
        lines.append(f"Status: {self.status}")

        control_snapshot = self.get_control_snapshot()
        behavior = control_snapshot.get("behavior", {})
        for line in behavior_context_lines(behavior):
            lines.append(line)

        session_memory = control_snapshot.get("session_memory", {}) if isinstance(control_snapshot.get("session_memory", {}), dict) else {}
        if session_memory.get("state_scope_id"):
            lines.append(f"Session scope: {session_memory.get('state_scope_id', '')}")
        if session_memory.get("priority_files"):
            lines.append("Session priority files:")
            for path in session_memory.get("priority_files", [])[:4]:
                lines.append(f"- {path}")
        task_control = control_snapshot.get("task_control", {}) if isinstance(control_snapshot.get("task_control", {}), dict) else {}
        if task_control.get("event"):
            lines.append(f"Task control state: {task_control.get('event', '')}")
        if task_control.get("reason"):
            lines.append(f"Task control reason: {task_control.get('reason', '')}")
        if task_control.get("replacement_goal"):
            lines.append(f"Replacement goal: {task_control.get('replacement_goal', '')}")

        if self.last_summary:
            lines.append(f"Rolling summary: {self.last_summary}")

        if self.memory_notes:
            lines.append("Memory notes:")
            for note in self.memory_notes[-8:]:
                lines.append(f"- {note}")

        desktop_activity = control_snapshot.get("desktop", {}) if isinstance(control_snapshot.get("desktop", {}), dict) else {}
        if (
            desktop_activity.get("active_window_title")
            or desktop_activity.get("windows")
            or desktop_activity.get("actions")
            or desktop_activity.get("last_action")
            or desktop_activity.get("checkpoint_pending")
            or desktop_activity.get("screenshot_path")
        ):
            lines.append("Desktop context:")
            if desktop_activity.get("active_window_title"):
                process_label = str(desktop_activity.get("active_window_process", "")).strip()
                if process_label:
                    lines.append(
                        f"- Active window: {desktop_activity.get('active_window_title', '')} ({process_label})"
                    )
                else:
                    lines.append(f"- Active window: {desktop_activity.get('active_window_title', '')}")
            if desktop_activity.get("windows"):
                lines.append("Visible windows:")
                for title in desktop_activity.get("windows", [])[:4]:
                    lines.append(f"- {title}")
            if desktop_activity.get("checkpoint_pending"):
                lines.append("- Desktop approval checkpoint pending: yes")
            if desktop_activity.get("checkpoint_tool"):
                lines.append(f"- Pending desktop tool: {desktop_activity.get('checkpoint_tool', '')}")
            if desktop_activity.get("checkpoint_target"):
                lines.append(f"- Pending desktop target: {desktop_activity.get('checkpoint_target', '')}")
            if desktop_activity.get("checkpoint_reason"):
                lines.append(f"- Approval needed because: {desktop_activity.get('checkpoint_reason', '')}")
            if desktop_activity.get("last_action"):
                lines.append(f"- Last desktop action: {desktop_activity.get('last_action', '')}")
            if desktop_activity.get("last_point"):
                lines.append(f"- Last point: {desktop_activity.get('last_point', '')}")
            if desktop_activity.get("last_typed_text_preview"):
                lines.append(f"- Last typed text preview: {desktop_activity.get('last_typed_text_preview', '')}")
            if desktop_activity.get("screenshot_path"):
                scope = str(desktop_activity.get("screenshot_scope", "")).strip()
                if scope:
                    lines.append(f"- Screenshot: {desktop_activity.get('screenshot_path', '')} ({scope})")
                else:
                    lines.append(f"- Screenshot: {desktop_activity.get('screenshot_path', '')}")
            if desktop_activity.get("evidence_id"):
                lines.append(f"- Evidence bundle: {desktop_activity.get('evidence_id', '')}")
            if desktop_activity.get("evidence_summary"):
                lines.append(f"- Evidence summary: {desktop_activity.get('evidence_summary', '')}")
            if desktop_activity.get("observed_at"):
                lines.append(f"- Desktop observed at: {desktop_activity.get('observed_at', '')}")
            if desktop_activity.get("actions"):
                lines.append("Recent desktop actions:")
                for action in desktop_activity.get("actions", [])[:4]:
                    lines.append(f"- {action}")
            if desktop_activity.get("uncertainties"):
                lines.append("Desktop uncertainties:")
                for note in desktop_activity.get("uncertainties", [])[:3]:
                    lines.append(f"- {note}")

        if (
            self.browser_current_url
            or self.browser_recent_actions
            or self.browser_recovery_notes
            or self.browser_task_name
            or self.browser_workflow_name
            or self.browser_checkpoint_pending
        ):
            lines.append("Browser context:")
            if self.browser_session_id:
                lines.append(f"- Session: {self.browser_session_id}")
            if self.browser_task_name:
                task_label = browser_task_label(self.browser_task_name) or self.browser_task_name
                lines.append(f"- Browser task pattern: {task_label}")
            if self.browser_task_current_step:
                lines.append(f"- Current browser task step: {self.browser_task_current_step}")
            if self.browser_task_next_step:
                lines.append(f"- Next browser task step: {self.browser_task_next_step}")
            if self.browser_task_status:
                lines.append(f"- Browser task status: {self.browser_task_status}")
            if self.browser_workflow_name:
                lines.append(f"- Workflow: {self.browser_workflow_name}")
            if self.browser_workflow_current_step:
                lines.append(f"- Current workflow step: {self.browser_workflow_current_step}")
            if self.browser_workflow_next_step:
                lines.append(f"- Next workflow step: {self.browser_workflow_next_step}")
            if self.browser_workflow_status:
                lines.append(f"- Workflow status: {self.browser_workflow_status}")
            if self.browser_checkpoint_pending:
                lines.append("- Approval checkpoint pending: yes")
            if self.browser_checkpoint_step:
                lines.append(f"- Approval checkpoint step: {self.browser_checkpoint_step}")
            if self.browser_checkpoint_target:
                lines.append(f"- Approval checkpoint target: {self.browser_checkpoint_target}")
            if self.browser_checkpoint_reason:
                lines.append(f"- Approval needed because: {self.browser_checkpoint_reason}")
            if self.browser_last_successful_action:
                lines.append(f"- Last successful browser action: {self.browser_last_successful_action}")
            if self.browser_current_title and self.browser_current_url:
                lines.append(f"- Current page: {self.browser_current_title} ({self.browser_current_url})")
            elif self.browser_current_url:
                lines.append(f"- Current page: {self.browser_current_url}")
            elif self.browser_current_title:
                lines.append(f"- Current page: {self.browser_current_title}")
            if self.browser_last_action:
                lines.append(f"- Last action: {self.browser_last_action}")
            expected_state = self._browser_expected_state_label()
            if expected_state:
                lines.append(f"- Expected next state: {expected_state}")
            if self.browser_last_text_excerpt:
                lines.append(f"- Visible text excerpt: {self.browser_last_text_excerpt[:280]}")
            if self.browser_retry_count or self.browser_fallback_attempts:
                lines.append(f"- Last recovery: retries={self.browser_retry_count}, fallbacks={self.browser_fallback_attempts}")
            if self.browser_workflow_history:
                lines.append("Workflow history:")
                for note in self.browser_workflow_history[-3:]:
                    lines.append(f"- {note}")
            elif self.browser_recent_actions:
                lines.append("Recent browser actions:")
                for action in self.browser_recent_actions[-4:]:
                    lines.append(f"- {action}")
            if self.browser_workflow_recovery_history:
                lines.append("Workflow recovery history:")
                for note in self.browser_workflow_recovery_history[-3:]:
                    lines.append(f"- {note}")
            if self.browser_recovery_notes:
                lines.append("Browser recovery notes:")
                for note in self.browser_recovery_notes[-3:]:
                    lines.append(f"- {note}")

        if self.known_dirs:
            lines.append("Known directories:")
            for d in self.known_dirs[-8:]:
                lines.append(f"- {d}")

        if self.priority_files:
            lines.append("Priority files for current goal:")
            for path in self.priority_files[:6]:
                lines.append(f"- {path}")

        if self.known_files:
            lines.append("Known files:")
            for f in self.known_files[-12:]:
                lines.append(f"- {f}")

        if recent_steps:
            lines.append("Recent steps:")
            for step in recent_steps:
                tool = step.get("tool", step.get("type", "unknown"))
                status = step.get("status", "unknown")
                lines.append(f"- {tool} [{status}]")

        return "\n".join(lines)












