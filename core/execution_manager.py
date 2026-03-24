from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

from core.agent import Agent
from core.alerts import (
    AlertStore,
    DEFAULT_ALERT_HISTORY_PATH,
    DEFAULT_MAX_ALERTS,
    alert_counts,
    alert_summary,
)
from core.desktop_capture_service import DesktopCaptureService
from core.file_watch_backend import FILE_WATCH_SUPPORTED_CONDITIONS, create_file_watch_backend
from core.scheduler_backend import create_scheduler_backend
from core.session_store import DEFAULT_STATE_SCOPE_ID
from core.state import MAX_TASK_GOAL_CHARS, MAX_TASK_REPLACEMENT_GOAL_CHARS, TaskState
from core.watchers import (
    DEFAULT_MAX_WATCH_ITEMS,
    DEFAULT_WATCH_STATE_PATH,
    WATCH_CHANGE_CONDITIONS,
    WatchStore,
    evaluate_watch_condition,
    watch_counts,
    watch_summary,
)


DEFAULT_QUEUE_STATE_PATH = "data/task_queue.json"
DEFAULT_MAX_QUEUE_ITEMS = 24
DEFAULT_SCHEDULED_STATE_PATH = "data/scheduled_tasks.json"
DEFAULT_MAX_SCHEDULED_ITEMS = 24
DEFAULT_SCHEDULER_POLL_SECONDS = 5
QUEUE_ACTIVE_STATUSES = {"running", "paused"}
QUEUE_RESUMABLE_STATUSES = {"paused", "deferred"}
QUEUE_TERMINAL_STATUSES = {"completed", "failed", "blocked", "incomplete", "stopped", "needs_attention", "superseded", "deferred"}
QUEUE_ALLOWED_STATUSES = {"queued"} | QUEUE_ACTIVE_STATUSES | QUEUE_TERMINAL_STATUSES
SCHEDULE_ACTIVE_STATUSES = {"scheduled", "queued", "running", "paused", "deferred"}
SCHEDULE_ALLOWED_STATUSES = {"scheduled"} | QUEUE_ALLOWED_STATUSES
SCHEDULE_RECURRENCE_VALUES = {"once", "daily"}
TASK_CONTROL_ACTIONS = {"stop", "defer", "supersede"}


def _trim_text(value: Any, limit: int = 240) -> str:
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _iso_timestamp(timestamp: float | None = None) -> str:
    source = _local_now() if timestamp is None else datetime.fromtimestamp(float(timestamp), tz=timezone.utc).astimezone()
    try:
        return source.isoformat(timespec="seconds")
    except Exception:
        return ""


def _iso_from_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    try:
        dt = value if value.tzinfo is not None else value.replace(tzinfo=_local_now().tzinfo)
        return dt.astimezone().isoformat(timespec="seconds")
    except Exception:
        return ""


def _parse_local_datetime(value: Any) -> datetime | None:
    text = str(value).strip()
    if not text:
        return None

    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue

    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_local_now().tzinfo)
    return parsed.astimezone()


def _next_daily_occurrence(base_time: datetime, reference: datetime | None = None) -> datetime:
    current = reference or _local_now()
    candidate = base_time if base_time.tzinfo is not None else base_time.replace(tzinfo=current.tzinfo)
    while candidate <= current:
        candidate += timedelta(days=1)
    return candidate


def _normalize_status(value: Any) -> str:
    text = str(value).strip().lower()
    if text in QUEUE_ALLOWED_STATUSES:
        return text
    return "queued"


def _normalize_schedule_status(value: Any) -> str:
    text = str(value).strip().lower()
    if text in SCHEDULE_ALLOWED_STATUSES:
        return text
    return "scheduled"


def _normalize_bool(value: Any) -> bool:
    return bool(value)


def _normalize_recurrence(value: Any) -> str:
    text = str(value).strip().lower()
    if text in SCHEDULE_RECURRENCE_VALUES:
        return text
    return "once"


def _normalize_task_item(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    task_id = _trim_text(value.get("task_id", ""), limit=60)
    if not task_id:
        return {}

    session_id = _trim_text(value.get("session_id", ""), limit=80)
    state_scope_id = _trim_text(value.get("state_scope_id", ""), limit=120) or (f"chat:{session_id}" if session_id else DEFAULT_STATE_SCOPE_ID)

    return {
        "task_id": task_id,
        "session_id": session_id,
        "state_scope_id": state_scope_id,
        "goal": _trim_text(value.get("goal", ""), limit=MAX_TASK_GOAL_CHARS),
        "status": _normalize_status(value.get("status", "queued")),
        "created_at": _trim_text(value.get("created_at", ""), limit=40) or _iso_timestamp(),
        "started_at": _trim_text(value.get("started_at", ""), limit=40),
        "ended_at": _trim_text(value.get("ended_at", ""), limit=40),
        "run_id": _trim_text(value.get("run_id", ""), limit=60),
        "source": _trim_text(value.get("source", "goal_run"), limit=40) or "goal_run",
        "scheduled_task_id": _trim_text(value.get("scheduled_task_id", ""), limit=60),
        "watch_id": _trim_text(value.get("watch_id", ""), limit=60),
        "last_message": _trim_text(value.get("last_message", ""), limit=280),
        "approval_needed": _normalize_bool(value.get("approval_needed", False)),
        "approval_reason": _trim_text(value.get("approval_reason", ""), limit=180),
        "paused": _normalize_bool(value.get("paused", False)),
        "control_event": _trim_text(value.get("control_event", ""), limit=60),
        "control_reason": _trim_text(value.get("control_reason", ""), limit=220),
        "replacement_task_id": _trim_text(value.get("replacement_task_id", ""), limit=60),
        "replacement_goal": _trim_text(value.get("replacement_goal", ""), limit=MAX_TASK_REPLACEMENT_GOAL_CHARS),
        "resume_available": _normalize_bool(value.get("resume_available", False)),
    }


def _normalize_scheduled_item(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    scheduled_id = _trim_text(value.get("scheduled_id", ""), limit=60)
    if not scheduled_id:
        return {}

    scheduled_for = _trim_text(value.get("scheduled_for", ""), limit=40)
    next_run_at = _trim_text(value.get("next_run_at", ""), limit=40) or scheduled_for
    if not scheduled_for:
        scheduled_for = next_run_at or _iso_timestamp()
    if not next_run_at:
        next_run_at = scheduled_for

    return {
        "scheduled_id": scheduled_id,
        "goal": _trim_text(value.get("goal", ""), limit=MAX_TASK_GOAL_CHARS),
        "status": _normalize_schedule_status(value.get("status", "scheduled")),
        "recurrence": _normalize_recurrence(value.get("recurrence", "once")),
        "scheduled_for": scheduled_for,
        "next_run_at": next_run_at,
        "created_at": _trim_text(value.get("created_at", ""), limit=40) or _iso_timestamp(),
        "updated_at": _trim_text(value.get("updated_at", ""), limit=40) or _iso_timestamp(),
        "queued_at": _trim_text(value.get("queued_at", ""), limit=40),
        "started_at": _trim_text(value.get("started_at", ""), limit=40),
        "ended_at": _trim_text(value.get("ended_at", ""), limit=40),
        "linked_task_id": _trim_text(value.get("linked_task_id", ""), limit=60),
        "last_run_id": _trim_text(value.get("last_run_id", ""), limit=60),
        "last_run_status": _trim_text(value.get("last_run_status", ""), limit=40),
        "source": _trim_text(value.get("source", "scheduled_goal"), limit=40) or "scheduled_goal",
        "last_message": _trim_text(value.get("last_message", ""), limit=280),
        "approval_needed": _normalize_bool(value.get("approval_needed", False)),
        "approval_reason": _trim_text(value.get("approval_reason", ""), limit=180),
        "paused": _normalize_bool(value.get("paused", False)),
        "control_event": _trim_text(value.get("control_event", ""), limit=60),
        "control_reason": _trim_text(value.get("control_reason", ""), limit=220),
        "replacement_task_id": _trim_text(value.get("replacement_task_id", ""), limit=60),
        "replacement_goal": _trim_text(value.get("replacement_goal", ""), limit=MAX_TASK_REPLACEMENT_GOAL_CHARS),
        "resume_available": _normalize_bool(value.get("resume_available", False)),
    }


class TaskQueueStore:
    def __init__(self, path: str | Path, *, max_items: int = DEFAULT_MAX_QUEUE_ITEMS):
        self.path = Path(path)
        self.max_items = max(1, int(max_items))

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"tasks": [], "active_task_id": ""}

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"tasks": [], "active_task_id": ""}

        if not isinstance(payload, dict):
            return {"tasks": [], "active_task_id": ""}

        tasks: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        for raw_task in payload.get("tasks", []):
            task = _normalize_task_item(raw_task)
            task_id = task.get("task_id", "")
            if not task_id or task_id in seen_ids:
                continue
            seen_ids.add(task_id)
            tasks.append(task)

        active_task_id = _trim_text(payload.get("active_task_id", ""), limit=60)
        if active_task_id and not any(task.get("task_id") == active_task_id for task in tasks):
            active_task_id = ""

        updated = False
        if active_task_id:
            active_task = next((task for task in tasks if task.get("task_id") == active_task_id), None)
            if active_task is None:
                active_task_id = ""
                updated = True
            elif active_task.get("status") == "running":
                active_task["status"] = "failed"
                active_task["ended_at"] = _iso_timestamp()
                active_task["last_message"] = "Task was interrupted before completion and marked failed on restore."
                active_task["paused"] = False
                active_task["approval_needed"] = False
                active_task["approval_reason"] = ""
                active_task_id = ""
                updated = True
            elif active_task.get("status") not in QUEUE_ACTIVE_STATUSES:
                active_task_id = ""
                updated = True

        tasks = self._trim_tasks(tasks, active_task_id)
        if updated:
            self.save(tasks, active_task_id)

        return {"tasks": tasks, "active_task_id": active_task_id}

    def save(self, tasks: List[Dict[str, Any]], active_task_id: str = "") -> bool:
        trimmed_tasks = self._trim_tasks(tasks, active_task_id)
        payload = {
            "version": 1,
            "updated_at": _iso_timestamp(),
            "active_task_id": _trim_text(active_task_id, limit=60),
            "tasks": trimmed_tasks,
        }

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            return False
        return True

    def _trim_tasks(self, tasks: List[Dict[str, Any]], active_task_id: str) -> List[Dict[str, Any]]:
        normalized = [_normalize_task_item(task) for task in tasks]
        normalized = [task for task in normalized if task]
        if len(normalized) <= self.max_items:
            return normalized

        pending_ids = {
            task.get("task_id", "")
            for task in normalized
            if task.get("status") in QUEUE_ACTIVE_STATUSES or task.get("status") in {"queued", "deferred"} or task.get("task_id") == active_task_id
        }
        kept_ids: set[str] = set()
        ordered: List[Dict[str, Any]] = []

        for task in normalized:
            task_id = task.get("task_id", "")
            if task_id in pending_ids and task_id not in kept_ids:
                ordered.append(task)
                kept_ids.add(task_id)

        remaining = max(0, self.max_items - len(ordered))
        terminal = [task for task in normalized if task.get("task_id", "") not in kept_ids]
        terminal_tail = terminal[-remaining:] if remaining else []
        allowed_ids = kept_ids | {task.get("task_id", "") for task in terminal_tail}
        return [task for task in normalized if task.get("task_id", "") in allowed_ids]


class ScheduledTaskStore:
    def __init__(self, path: str | Path, *, max_items: int = DEFAULT_MAX_SCHEDULED_ITEMS):
        self.path = Path(path)
        self.max_items = max(1, int(max_items))

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"scheduled_tasks": []}

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"scheduled_tasks": []}

        if not isinstance(payload, dict):
            return {"scheduled_tasks": []}

        tasks: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        for raw_task in payload.get("scheduled_tasks", []):
            task = _normalize_scheduled_item(raw_task)
            scheduled_id = task.get("scheduled_id", "")
            if not scheduled_id or scheduled_id in seen_ids:
                continue
            seen_ids.add(scheduled_id)
            tasks.append(task)

        return {"scheduled_tasks": self._trim_tasks(tasks)}

    def save(self, scheduled_tasks: List[Dict[str, Any]]) -> bool:
        payload = {
            "version": 1,
            "updated_at": _iso_timestamp(),
            "scheduled_tasks": self._trim_tasks(scheduled_tasks),
        }

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            return False
        return True

    def _trim_tasks(self, scheduled_tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = [_normalize_scheduled_item(task) for task in scheduled_tasks]
        normalized = [task for task in normalized if task]
        if len(normalized) <= self.max_items:
            return normalized

        keep_ids: set[str] = set()
        ordered: List[Dict[str, Any]] = []
        for task in normalized:
            if task.get("status") in SCHEDULE_ACTIVE_STATUSES or task.get("recurrence") != "once":
                scheduled_id = task.get("scheduled_id", "")
                if scheduled_id and scheduled_id not in keep_ids:
                    keep_ids.add(scheduled_id)
                    ordered.append(task)

        remaining = max(0, self.max_items - len(ordered))
        terminal = [task for task in normalized if task.get("scheduled_id", "") not in keep_ids]
        terminal_tail = terminal[-remaining:] if remaining else []
        allowed_ids = keep_ids | {task.get("scheduled_id", "") for task in terminal_tail}
        return [task for task in normalized if task.get("scheduled_id", "") in allowed_ids]


class ExecutionManager:
    def __init__(self, agent: Agent | None = None):
        self.agent = agent or Agent()
        max_items = int(self.agent.settings.get("max_queue_state_items", DEFAULT_MAX_QUEUE_ITEMS))
        queue_state_path = self.agent.settings.get("queue_state_path", DEFAULT_QUEUE_STATE_PATH)
        self.queue_store = TaskQueueStore(queue_state_path, max_items=max_items)

        max_scheduled_items = int(self.agent.settings.get("max_scheduled_task_entries", DEFAULT_MAX_SCHEDULED_ITEMS))
        scheduled_state_path = self.agent.settings.get("scheduled_task_state_path", DEFAULT_SCHEDULED_STATE_PATH)
        self.scheduled_store = ScheduledTaskStore(scheduled_state_path, max_items=max_scheduled_items)

        max_watch_items = int(self.agent.settings.get("max_watch_entries", DEFAULT_MAX_WATCH_ITEMS))
        watch_state_path = self.agent.settings.get("watch_state_path", DEFAULT_WATCH_STATE_PATH)
        self.watch_store = WatchStore(watch_state_path, max_items=max_watch_items)

        max_alert_items = int(self.agent.settings.get("max_alert_entries", DEFAULT_MAX_ALERTS))
        alert_state_path = self.agent.settings.get("alert_state_path", DEFAULT_ALERT_HISTORY_PATH)
        self.alert_store = AlertStore(alert_state_path, max_items=max_alert_items)
        self.scheduler_poll_seconds = max(1, int(self.agent.settings.get("scheduler_poll_seconds", DEFAULT_SCHEDULER_POLL_SECONDS)))
        self.scheduler_backend = create_scheduler_backend(self.agent.settings)
        self.file_watch_backend = create_file_watch_backend(self.agent.settings)
        self.desktop_capture_service = DesktopCaptureService(
            self.agent.settings,
            context_getter=self._desktop_capture_context,
        )

        loaded = self.queue_store.load()
        self._tasks: List[Dict[str, Any]] = list(loaded.get("tasks", []))
        self._active_task_id: str = str(loaded.get("active_task_id", "")).strip()
        scheduled_loaded = self.scheduled_store.load()
        self._scheduled_tasks: List[Dict[str, Any]] = list(scheduled_loaded.get("scheduled_tasks", []))
        watch_loaded = self.watch_store.load()
        self._watches: List[Dict[str, Any]] = list(watch_loaded.get("watches", []))
        alert_loaded = self.alert_store.load()
        self._alerts: List[Dict[str, Any]] = list(alert_loaded.get("alerts", []))

        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._scheduler_thread: threading.Thread | None = None
        self._scheduler_stop = threading.Event()
        self._control_requests: Dict[str, Dict[str, Any]] = {}
        self._state_scope_id = DEFAULT_STATE_SCOPE_ID
        active_task = self._active_task_locked()
        if active_task is not None:
            self._state_scope_id = self._task_state_scope_id_locked(active_task)
            self._state = self.agent.load_task_state(
                state_scope_id=self._state_scope_id,
                clear_pending_for_new_goal=False,
            )
        else:
            self._state = self.agent.load_task_state(
                state_scope_id=self._state_scope_id,
                clear_pending_for_new_goal=False,
            )
        self._last_result: Dict[str, Any] = {}
        self._last_result_message: str = ""
        self._recent_file_watch_events: List[Dict[str, Any]] = []

        auto_start = False
        with self._lock:
            self.scheduler_backend.sync_scheduled_tasks(self._scheduled_tasks)
            self.file_watch_backend.sync_watches(self._watches)
            changed = self._sync_scheduled_tasks_locked()
            promoted, scheduled_auto_start = self._promote_due_scheduled_tasks_locked()
            watch_changed, watch_auto_start = self._process_watches_locked(force_check=True)
            auto_start = scheduled_auto_start or watch_auto_start
            if changed or promoted or watch_changed:
                self._persist_all_locked()
            else:
                self._persist_queue_locked()
                self._persist_scheduled_locked()
                self._persist_watches_locked()
                self._persist_alerts_locked()

        self._start_scheduler_thread()
        self.desktop_capture_service.start()
        if auto_start:
            self.start_next(auto_trigger=True)

    def shutdown(self):
        self._scheduler_stop.set()
        try:
            self.scheduler_backend.shutdown()
        except Exception:
            pass
        try:
            self.file_watch_backend.shutdown()
        except Exception:
            pass
        try:
            self.desktop_capture_service.shutdown()
        except Exception:
            pass

    def _start_scheduler_thread(self):
        if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
            return

        def runner():
            while not self._scheduler_stop.is_set():
                auto_start = False
                with self._lock:
                    changed = self._sync_scheduled_tasks_locked()
                    promoted, scheduled_auto_start = self._promote_due_scheduled_tasks_locked()
                    watch_changed, watch_auto_start = self._process_watches_locked()
                    auto_start = scheduled_auto_start or watch_auto_start
                    if changed or promoted or watch_changed:
                        self._persist_all_locked()
                if auto_start:
                    self.start_next(auto_trigger=True)
                self._scheduler_stop.wait(self.scheduler_poll_seconds)

        self._scheduler_thread = threading.Thread(target=runner, name="operator-scheduler", daemon=True)
        self._scheduler_thread.start()

    def _desktop_capture_context(self) -> Dict[str, Any]:
        with self._lock:
            active_task = self._active_task_locked()
            active_task_id = str((active_task or {}).get("task_id", "")).strip()
            state = self._state
            return {
                "state_scope_id": str(getattr(state, "state_scope_id", "")).strip(),
                "task_id": active_task_id,
                "task_status": str(getattr(state, "status", "")).strip(),
                "checkpoint_pending": bool(getattr(state, "desktop_checkpoint_pending", False)),
                "checkpoint_tool": str(getattr(state, "desktop_checkpoint_tool", "")).strip(),
                "checkpoint_target": str(getattr(state, "desktop_checkpoint_target", "")).strip(),
                "active_window_title": str(getattr(state, "desktop_active_window_title", "")).strip(),
            }

    def _is_running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def _refresh_summary(self, state: TaskState):
        recent_notes = state.memory_notes[-6:]
        if recent_notes:
            state.set_summary(" | ".join(recent_notes))

    def _set_last_result(self, result: Dict[str, Any] | None):
        self._last_result = result if isinstance(result, dict) else {}
        self._last_result_message = str(self._last_result.get("message", "")).strip()

    def _set_task_control_fields_locked(
        self,
        task: Dict[str, Any],
        *,
        event: str = "",
        reason: str = "",
        replacement_task_id: str = "",
        replacement_goal: str = "",
        resume_available: bool = False,
    ):
        task["control_event"] = _trim_text(event, limit=60)
        task["control_reason"] = _trim_text(reason, limit=220)
        task["replacement_task_id"] = _trim_text(replacement_task_id, limit=60)
        task["replacement_goal"] = _trim_text(replacement_goal, limit=MAX_TASK_REPLACEMENT_GOAL_CHARS)
        task["resume_available"] = bool(resume_available)

    def _task_control_payload_locked(self, task: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(task, dict):
            return {}
        return {
            "event": _trim_text(task.get("control_event", ""), limit=60),
            "reason": _trim_text(task.get("control_reason", ""), limit=220),
            "replacement_task_id": _trim_text(task.get("replacement_task_id", ""), limit=60),
            "replacement_goal": _trim_text(task.get("replacement_goal", ""), limit=MAX_TASK_REPLACEMENT_GOAL_CHARS),
            "resume_available": bool(task.get("resume_available", False)),
        }

    def _normalize_session_id(self, session_id: Any) -> str:
        return _trim_text(session_id, limit=80)

    def _normalize_state_scope_id(self, state_scope_id: Any = "", *, session_id: Any = "") -> str:
        explicit = _trim_text(state_scope_id, limit=120)
        if explicit:
            return explicit
        normalized_session_id = self._normalize_session_id(session_id)
        if normalized_session_id:
            return f"chat:{normalized_session_id}"
        return DEFAULT_STATE_SCOPE_ID

    def _task_session_id_locked(self, task: Dict[str, Any] | None) -> str:
        if not isinstance(task, dict):
            return ""
        return self._normalize_session_id(task.get("session_id", ""))

    def _task_state_scope_id_locked(self, task: Dict[str, Any] | None) -> str:
        if not isinstance(task, dict):
            return DEFAULT_STATE_SCOPE_ID
        return self._normalize_state_scope_id(task.get("state_scope_id", ""), session_id=task.get("session_id", ""))

    def _set_current_state_locked(self, state: TaskState):
        normalized_scope_id = self._normalize_state_scope_id(getattr(state, "state_scope_id", DEFAULT_STATE_SCOPE_ID))
        state.state_scope_id = normalized_scope_id
        self._state = state
        self._state_scope_id = normalized_scope_id

    def _load_state_for_scope_locked(
        self,
        state_scope_id: str,
        *,
        goal: str = "",
        clear_pending_for_new_goal: bool = False,
    ) -> TaskState:
        normalized_scope_id = self._normalize_state_scope_id(state_scope_id)
        if not goal and self._state is not None and self._state_scope_id == normalized_scope_id:
            self._state.state_scope_id = normalized_scope_id
            return self._state
        state = self.agent.load_task_state(
            goal,
            state_scope_id=normalized_scope_id,
            clear_pending_for_new_goal=clear_pending_for_new_goal,
        )
        state.state_scope_id = normalized_scope_id
        return state

    def _state_has_activity_locked(self, state: TaskState) -> bool:
        return bool(
            state.goal
            or state.steps
            or state.memory_notes
            or state.last_summary
            or state.browser_current_url
            or state.browser_current_title
            or state.browser_task_name
            or state.browser_workflow_name
            or state.browser_checkpoint_pending
            or state.desktop_active_window_title
            or state.desktop_windows
            or state.desktop_last_action
            or state.desktop_checkpoint_pending
        )

    def _task_matches_scope_locked(self, task: Dict[str, Any], *, session_id: str = "", state_scope_id: str = "") -> bool:
        if not isinstance(task, dict):
            return False
        normalized_session_id = self._normalize_session_id(session_id)
        normalized_scope_id = self._normalize_state_scope_id(state_scope_id, session_id=normalized_session_id)
        task_session_id = self._task_session_id_locked(task)
        task_scope_id = self._task_state_scope_id_locked(task)
        if normalized_session_id and task_session_id != normalized_session_id:
            return False
        if normalized_scope_id and task_scope_id != normalized_scope_id:
            return False
        return True

    def _matching_tasks_locked(
        self,
        *,
        session_id: str = "",
        state_scope_id: str = "",
        statuses: set[str] | None = None,
    ) -> List[Dict[str, Any]]:
        matched: List[Dict[str, Any]] = []
        for task in self._tasks:
            if not self._task_matches_scope_locked(task, session_id=session_id, state_scope_id=state_scope_id):
                continue
            if statuses and task.get("status") not in statuses:
                continue
            matched.append(task)
        return matched

    def _select_primary_task_locked(self, tasks: List[Dict[str, Any]]) -> Dict[str, Any] | None:
        if not tasks:
            return None
        status_order = {
            "paused": 0,
            "running": 1,
            "queued": 2,
            "deferred": 3,
            "needs_attention": 4,
            "completed": 5,
            "blocked": 6,
            "failed": 7,
            "stopped": 8,
            "superseded": 9,
            "incomplete": 10,
        }
        positions = {task.get("task_id", ""): index for index, task in enumerate(self._tasks)}
        ordered = sorted(
            tasks,
            key=lambda item: (
                status_order.get(str(item.get("status", "")).strip(), 50),
                -positions.get(item.get("task_id", ""), -1),
            ),
        )
        return ordered[0] if ordered else None

    def _find_pending_task_locked(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any] | None:
        if session_id or state_scope_id:
            return self._select_primary_task_locked(
                self._matching_tasks_locked(session_id=session_id, state_scope_id=state_scope_id, statuses={"paused"})
            )
        active_task = self._active_task_locked()
        if active_task is not None and active_task.get("status") == "paused":
            return active_task
        return None

    def _find_controllable_task_locked(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any] | None:
        controllable_statuses = {"running", "paused", "queued", "deferred"}
        if session_id or state_scope_id:
            return self._select_primary_task_locked(
                self._matching_tasks_locked(session_id=session_id, state_scope_id=state_scope_id, statuses=controllable_statuses)
            )
        active_task = self._active_task_locked()
        if active_task is not None and active_task.get("status") in controllable_statuses:
            return active_task
        return self._select_primary_task_locked([task for task in self._tasks if task.get("status") in controllable_statuses])

    def _find_retryable_task_locked(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any] | None:
        retryable_statuses = {"blocked", "failed", "incomplete", "stopped", "superseded"}
        if session_id or state_scope_id:
            return self._select_primary_task_locked(
                self._matching_tasks_locked(session_id=session_id, state_scope_id=state_scope_id, statuses=retryable_statuses)
            )
        return self._select_primary_task_locked([task for task in self._tasks if task.get("status") in retryable_statuses])

    def _set_control_request_locked(self, task: Dict[str, Any], *, action: str, reason: str = "", replacement_goal: str = "", replacement_task_id: str = ""):
        task_id = _trim_text(task.get("task_id", ""), limit=60)
        if not task_id or action not in TASK_CONTROL_ACTIONS:
            return
        self._control_requests[task_id] = {
            "action": action,
            "reason": _trim_text(reason, limit=220),
            "replacement_goal": _trim_text(replacement_goal, limit=MAX_TASK_REPLACEMENT_GOAL_CHARS),
            "replacement_task_id": _trim_text(replacement_task_id, limit=60),
        }

    def _consume_control_request_locked(self, task_id: str) -> Dict[str, Any] | None:
        lookup = _trim_text(task_id, limit=60)
        if not lookup:
            return None
        return self._control_requests.pop(lookup, None)

    def _find_task_locked(self, task_id: str) -> Dict[str, Any] | None:
        lookup = str(task_id).strip()
        if not lookup:
            return None
        for task in self._tasks:
            if task.get("task_id") == lookup:
                return task
        return None

    def _active_task_locked(self) -> Dict[str, Any] | None:
        return self._find_task_locked(self._active_task_id)

    def _has_queued_tasks_locked(self) -> bool:
        return any(task.get("status") == "queued" for task in self._tasks)

    def _persist_queue_locked(self):
        self.queue_store.save(self._tasks, self._active_task_id)

    def _persist_scheduled_locked(self):
        self.scheduled_store.save(self._scheduled_tasks)
        self.scheduler_backend.sync_scheduled_tasks(self._scheduled_tasks)

    def _persist_watches_locked(self):
        self.watch_store.save(self._watches)
        self.file_watch_backend.sync_watches(self._watches)

    def _persist_alerts_locked(self):
        self.alert_store.save(self._alerts)

    def _persist_all_locked(self):
        self._persist_queue_locked()
        self._persist_scheduled_locked()
        self._persist_watches_locked()
        self._persist_alerts_locked()

    def _append_alert_locked(
        self,
        *,
        severity: str,
        alert_type: str,
        source: str,
        title: str,
        message: str,
        goal: str = "",
        task_id: str = "",
        scheduled_id: str = "",
        watch_id: str = "",
        run_id: str = "",
        session_id: str = "",
        state_scope_id: str = "",
    ):
        alert = {
            "alert_id": self.alert_store.next_alert_id(),
            "created_at": _iso_timestamp(),
            "severity": _trim_text(severity, limit=20),
            "type": _trim_text(alert_type, limit=60),
            "source": _trim_text(source, limit=60),
            "title": _trim_text(title, limit=120),
            "message": _trim_text(message, limit=320),
            "goal": _trim_text(goal, limit=220),
            "task_id": _trim_text(task_id, limit=60),
            "scheduled_id": _trim_text(scheduled_id, limit=60),
            "watch_id": _trim_text(watch_id, limit=60),
            "run_id": _trim_text(run_id, limit=60),
            "session_id": self._normalize_session_id(session_id),
            "state_scope_id": self._normalize_state_scope_id(state_scope_id, session_id=session_id),
        }
        self._alerts.append(alert)
        self._alerts = self.alert_store._trim_alerts(self._alerts)
    def _append_task_status_alert_locked(
        self,
        task: Dict[str, Any],
        state: TaskState,
        result: Dict[str, Any],
        *,
        queue_status: str,
        pending: Dict[str, Any],
    ):
        goal = str(state.goal or task.get("goal", "")).strip()
        message = str(result.get("message", "") or state.last_summary or task.get("last_message", "")).strip()
        task_source = str(task.get("source", "goal_run") or "goal_run").strip()
        run_id = str(result.get("run_id", "") or task.get("run_id", "")).strip()
        session_id_value = self._task_session_id_locked(task)
        state_scope_id_value = self._normalize_state_scope_id(getattr(state, "state_scope_id", ""), session_id=session_id_value)

        if queue_status == "completed":
            title = "Task completed"
            if task.get("scheduled_task_id"):
                title = "Scheduled task completed"
            elif task.get("watch_id"):
                title = "Triggered task completed"
            self._append_alert_locked(
                severity="success",
                alert_type="task_completed",
                source=task_source,
                title=title,
                message=message or "The task completed successfully.",
                goal=goal,
                task_id=task.get("task_id", ""),
                scheduled_id=task.get("scheduled_task_id", ""),
                watch_id=task.get("watch_id", ""),
                run_id=run_id,
                session_id=session_id_value,
                state_scope_id=state_scope_id_value,
            )
            return

        if queue_status == "paused":
            if pending.get("kind") == "browser_checkpoint" or state.browser_checkpoint_pending or state.browser_task_status == "paused" or state.browser_workflow_status == "paused":
                reason = str(pending.get("reason", "") or pending.get("summary", "") or message or "Browser workflow paused and needs approval.").strip()
                self._append_alert_locked(
                    severity="warning",
                    alert_type="browser_paused",
                    source="browser",
                    title="Browser workflow paused",
                    message=reason,
                    goal=goal,
                    task_id=task.get("task_id", ""),
                    scheduled_id=task.get("scheduled_task_id", ""),
                    watch_id=task.get("watch_id", ""),
                    run_id=run_id,
                    session_id=session_id_value,
                    state_scope_id=state_scope_id_value,
                )
            elif pending.get("kind") == "desktop_action" or state.desktop_checkpoint_pending:
                reason = str(
                    pending.get("reason", "")
                    or pending.get("summary", "")
                    or message
                    or "Desktop action paused and needs approval."
                ).strip()
                self._append_alert_locked(
                    severity="warning",
                    alert_type="desktop_paused",
                    source="desktop",
                    title="Desktop action paused",
                    message=reason,
                    goal=goal,
                    task_id=task.get("task_id", ""),
                    scheduled_id=task.get("scheduled_task_id", ""),
                    watch_id=task.get("watch_id", ""),
                    run_id=run_id,
                    session_id=session_id_value,
                    state_scope_id=state_scope_id_value,
                )
            elif pending.get("kind"):
                reason = str(pending.get("reason", "") or pending.get("summary", "") or message or "Approval is required before the task can continue.").strip()
                self._append_alert_locked(
                    severity="warning",
                    alert_type="approval_needed",
                    source=str(pending.get("kind", "approval")).strip() or "approval",
                    title="Approval needed",
                    message=reason,
                    goal=goal,
                    task_id=task.get("task_id", ""),
                    scheduled_id=task.get("scheduled_task_id", ""),
                    watch_id=task.get("watch_id", ""),
                    run_id=run_id,
                    session_id=session_id_value,
                    state_scope_id=state_scope_id_value,
                )
            return

        if queue_status == "superseded":
            self._append_alert_locked(
                severity="info",
                alert_type="task_superseded",
                source=task_source,
                title="Task superseded",
                message=message or "Task was replaced by newer work.",
                goal=goal,
                task_id=task.get("task_id", ""),
                scheduled_id=task.get("scheduled_task_id", ""),
                watch_id=task.get("watch_id", ""),
                run_id=run_id,
                session_id=session_id_value,
                state_scope_id=state_scope_id_value,
            )
            return

        if queue_status == "deferred":
            self._append_alert_locked(
                severity="info",
                alert_type="task_deferred",
                source=task_source,
                title="Task deferred",
                message=message or "Task was deferred for later resumption.",
                goal=goal,
                task_id=task.get("task_id", ""),
                scheduled_id=task.get("scheduled_task_id", ""),
                watch_id=task.get("watch_id", ""),
                run_id=run_id,
                session_id=session_id_value,
                state_scope_id=state_scope_id_value,
            )
            return

        if queue_status not in {"failed", "blocked", "incomplete", "stopped"}:
            return

        if state.browser_workflow_status == "blocked" or state.browser_task_status == "blocked":
            title = "Browser workflow blocked"
            alert_type = "browser_blocked"
            source = "browser"
        else:
            title = "Task failed" if queue_status == "failed" else "Task blocked"
            alert_type = "task_failed" if queue_status == "failed" else "task_blocked"
            source = task_source

        self._append_alert_locked(
            severity="error" if queue_status in {"failed", "blocked"} else "warning",
            alert_type=alert_type,
            source=source,
            title=title,
            message=message or f"Task ended with status {queue_status}.",
            goal=goal,
            task_id=task.get("task_id", ""),
            scheduled_id=task.get("scheduled_task_id", ""),
            watch_id=task.get("watch_id", ""),
            run_id=run_id,
            session_id=session_id_value,
            state_scope_id=state_scope_id_value,
        )
    def _append_manual_status_alert_locked(self, task: Dict[str, Any], state: TaskState, *, status: str, message: str):
        task_source = str(task.get("source", "goal_run") or "goal_run").strip()
        goal = str(state.goal or task.get("goal", "")).strip()
        session_id_value = self._task_session_id_locked(task)
        state_scope_id_value = self._normalize_state_scope_id(getattr(state, "state_scope_id", ""), session_id=session_id_value)
        if status == "deferred":
            self._append_alert_locked(
                severity="info",
                alert_type="task_deferred",
                source=task_source,
                title="Task deferred",
                message=message or "Task deferred.",
                goal=goal,
                task_id=task.get("task_id", ""),
                scheduled_id=task.get("scheduled_task_id", ""),
                watch_id=task.get("watch_id", ""),
                run_id=task.get("run_id", ""),
                session_id=session_id_value,
                state_scope_id=state_scope_id_value,
            )
            return
        if status == "superseded":
            self._append_alert_locked(
                severity="info",
                alert_type="task_superseded",
                source=task_source,
                title="Task superseded",
                message=message or "Task superseded.",
                goal=goal,
                task_id=task.get("task_id", ""),
                scheduled_id=task.get("scheduled_task_id", ""),
                watch_id=task.get("watch_id", ""),
                run_id=task.get("run_id", ""),
                session_id=session_id_value,
                state_scope_id=state_scope_id_value,
            )
            return
        if status == "stopped":
            self._append_alert_locked(
                severity="warning",
                alert_type="task_stopped",
                source=task_source,
                title="Task stopped",
                message=message or "Task stopped.",
                goal=goal,
                task_id=task.get("task_id", ""),
                scheduled_id=task.get("scheduled_task_id", ""),
                watch_id=task.get("watch_id", ""),
                run_id=task.get("run_id", ""),
                session_id=session_id_value,
                state_scope_id=state_scope_id_value,
            )
            return
        if status != "blocked":
            return
        if state.browser_workflow_status == "blocked" or state.browser_task_status == "blocked":
            self._append_alert_locked(
                severity="error",
                alert_type="browser_blocked",
                source="browser",
                title="Browser workflow blocked",
                message=message or "Browser workflow was blocked.",
                goal=goal,
                task_id=task.get("task_id", ""),
                scheduled_id=task.get("scheduled_task_id", ""),
                watch_id=task.get("watch_id", ""),
                run_id=task.get("run_id", ""),
                session_id=session_id_value,
                state_scope_id=state_scope_id_value,
            )
            return

        self._append_alert_locked(
            severity="warning",
            alert_type="task_blocked",
            source=task_source,
            title="Task blocked",
            message=message or "Task was blocked.",
            goal=goal,
            task_id=task.get("task_id", ""),
            scheduled_id=task.get("scheduled_task_id", ""),
            watch_id=task.get("watch_id", ""),
            run_id=task.get("run_id", ""),
            session_id=session_id_value,
            state_scope_id=state_scope_id_value,
        )
    def _create_task_locked(
        self,
        goal: str,
        *,
        source: str,
        scheduled_task_id: str = "",
        watch_id: str = "",
        session_id: str = "",
        state_scope_id: str = "",
    ) -> Dict[str, Any]:
        normalized_session_id = self._normalize_session_id(session_id)
        normalized_scope_id = self._normalize_state_scope_id(state_scope_id, session_id=normalized_session_id)
        return {
            "task_id": f"task-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}",
            "session_id": normalized_session_id,
            "state_scope_id": normalized_scope_id,
            "goal": _trim_text(goal, limit=MAX_TASK_GOAL_CHARS),
            "status": "queued",
            "created_at": _iso_timestamp(),
            "started_at": "",
            "ended_at": "",
            "run_id": "",
            "source": _trim_text(source, limit=40) or "goal_run",
            "scheduled_task_id": _trim_text(scheduled_task_id, limit=60),
            "watch_id": _trim_text(watch_id, limit=60),
            "last_message": "Queued.",
            "approval_needed": False,
            "approval_reason": "",
            "paused": False,
            "control_event": "",
            "control_reason": "",
            "replacement_task_id": "",
            "replacement_goal": "",
            "resume_available": False,
        }

    def _create_scheduled_task_locked(self, goal: str, *, run_at: datetime, recurrence: str) -> Dict[str, Any]:
        run_at_text = _iso_from_datetime(run_at)
        return {
            "scheduled_id": f"sched-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}",
            "goal": _trim_text(goal, limit=MAX_TASK_GOAL_CHARS),
            "status": "scheduled",
            "recurrence": _normalize_recurrence(recurrence),
            "scheduled_for": run_at_text,
            "next_run_at": run_at_text,
            "created_at": _iso_timestamp(),
            "updated_at": _iso_timestamp(),
            "queued_at": "",
            "started_at": "",
            "ended_at": "",
            "linked_task_id": "",
            "last_run_id": "",
            "last_run_status": "",
            "source": "scheduled_goal",
            "last_message": f"Scheduled for {run_at_text}.",
            "approval_needed": False,
            "approval_reason": "",
            "paused": False,
        }

    def _create_watch_locked(
        self,
        goal: str,
        *,
        condition_type: str,
        target: str,
        match_text: str,
        interval_seconds: int,
        allow_repeat: bool,
    ) -> Dict[str, Any]:
        return {
            "watch_id": f"watch-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}",
            "goal": _trim_text(goal, limit=MAX_TASK_GOAL_CHARS),
            "status": "watching",
            "condition_type": condition_type,
            "target": _trim_text(target, limit=320),
            "match_text": _trim_text(match_text, limit=240),
            "interval_seconds": max(2, int(interval_seconds)),
            "allow_repeat": bool(allow_repeat),
            "created_at": _iso_timestamp(),
            "updated_at": _iso_timestamp(),
            "last_checked_at": "",
            "last_triggered_at": "",
            "linked_task_id": "",
            "last_run_id": "",
            "last_run_status": "",
            "source": "watch_trigger",
            "last_message": "Watch created.",
            "error": "",
            "baseline_signature": "",
            "last_signature": "",
            "last_trigger_signature": "",
            "last_condition_met": False,
            "trigger_count": 0,
            "pending_enqueue": False,
            "approval_needed": False,
            "approval_reason": "",
        }

    def _can_accept_queue_item_locked(self) -> bool:
        self._tasks = self.queue_store._trim_tasks(self._tasks, self._active_task_id)
        if len(self._tasks) < self.queue_store.max_items:
            return True
        return any(task.get("status") in QUEUE_TERMINAL_STATUSES for task in self._tasks)

    def _enqueue_task_locked(
        self,
        goal: str,
        *,
        source: str,
        scheduled_task_id: str = "",
        watch_id: str = "",
        session_id: str = "",
        state_scope_id: str = "",
    ) -> Dict[str, Any] | None:
        if not self._can_accept_queue_item_locked():
            return None
        task = self._create_task_locked(
            goal,
            source=source,
            scheduled_task_id=scheduled_task_id,
            watch_id=watch_id,
            session_id=session_id,
            state_scope_id=state_scope_id,
        )
        self._tasks.append(task)
        self._tasks = self.queue_store._trim_tasks(self._tasks, self._active_task_id)
        if not any(existing.get("task_id") == task.get("task_id") for existing in self._tasks):
            return None
        return task

    def _can_start_next_locked(self) -> bool:
        if self._is_running():
            return False
        active_task = self._active_task_locked()
        if active_task and active_task.get("status") in QUEUE_ACTIVE_STATUSES:
            return False
        return self._has_queued_tasks_locked()

    def _latest_review_bundle_step(self, state: TaskState):
        for step in reversed(state.steps):
            if step.get("tool") != "build_review_bundle":
                continue
            if step.get("status") != "completed":
                continue
            result = step.get("result", {})
            if isinstance(result, dict):
                return step
        return None

    def _record_manual_history(
        self,
        state: TaskState,
        *,
        started_at: float,
        step_start_index: int,
        result: Dict[str, Any],
        source: str,
        session_id: str = "",
    ):
        entry = self.agent.record_run_history(
            state,
            started_at=started_at,
            step_start_index=step_start_index,
            result=result,
            source=source,
            session_id=session_id,
            state_scope_id=getattr(state, "state_scope_id", DEFAULT_STATE_SCOPE_ID),
        )
        result["run_id"] = entry.get("run_id", "")
        return entry

    def _render_authoritative_manual_reply_locked(self, state: TaskState, fallback_message: str) -> str:
        try:
            rendered = self.agent.llm.finalize(
                state.goal,
                state.steps,
                state.get_observation(),
                state.get_final_context(),
                desktop_vision=state.get_desktop_vision_context(
                    purpose="desktop_final",
                    prompt_text=state.goal,
                    prefer_before_after=True,
                ),
            )
        except Exception:
            rendered = ""
        return str(rendered).strip() or str(fallback_message).strip()

    def _mark_control_requested_locked(
        self,
        task: Dict[str, Any],
        state: TaskState,
        *,
        action: str,
        message: str,
        replacement_goal: str = "",
        replacement_task_id: str = "",
    ):
        event = f"{action}_requested"
        state.set_task_control(
            event=event,
            reason=message,
            resume_available=(action == "defer"),
            replacement_task_id=replacement_task_id,
            replacement_goal=replacement_goal,
        )
        state.add_note(message)
        self._refresh_summary(state)
        self.agent.save_task_state(state, state_scope_id=state.state_scope_id)
        self._set_current_state_locked(state)
        task["last_message"] = _trim_text(message, limit=280)
        self._set_task_control_fields_locked(
            task,
            event=event,
            reason=message,
            replacement_task_id=replacement_task_id,
            replacement_goal=replacement_goal,
            resume_available=(action == "defer"),
        )

    def _finalize_task_control_locked(
        self,
        task: Dict[str, Any],
        *,
        status: str,
        control_event: str,
        message: str,
        replacement_goal: str = "",
        replacement_task_id: str = "",
    ) -> Dict[str, Any]:
        started_at = time.time()
        session_id_value = self._task_session_id_locked(task)
        state = self._load_state_for_scope_locked(self._task_state_scope_id_locked(task), clear_pending_for_new_goal=False)
        history_start_index = len(state.steps)
        if status in {"stopped", "superseded", "deferred"}:
            state.clear_browser_checkpoint()
            state.clear_desktop_checkpoint()
        state.add_step(
            {
                "type": "system",
                "status": control_event,
                "message": message,
                "tool": "operator_control",
                "replacement_task_id": replacement_task_id,
            }
        )
        state.set_task_control(
            event=control_event,
            reason=message,
            resume_available=(status == "deferred"),
            replacement_task_id=replacement_task_id,
            replacement_goal=replacement_goal,
        )
        state.add_note(message)
        self._refresh_summary(state)
        state.status = status
        self.agent.save_task_state(state, state_scope_id=state.state_scope_id)
        self._set_current_state_locked(state)
        reply_message = self._render_authoritative_manual_reply_locked(state, message)
        result_payload = {
            "ok": True,
            "status": status,
            "message": reply_message,
            "steps": state.steps,
        }
        self._record_manual_history(
            state,
            started_at=started_at,
            step_start_index=history_start_index,
            result=result_payload,
            source="control_action",
            session_id=session_id_value,
        )
        self._update_task_after_manual_action_locked(
            task,
            status=status,
            message=reply_message,
            run_id=str(result_payload.get("run_id", "")),
            state=state,
            control_event=control_event,
            replacement_task_id=replacement_task_id,
            replacement_goal=replacement_goal,
            resume_available=(status == "deferred"),
        )
        self._set_last_result(result_payload)
        return result_payload

    def _browser_resume_goal(self, state: TaskState) -> str:
        step = state.browser_checkpoint_step or state.browser_task_current_step or state.browser_workflow_current_step or "paused browser step"
        tool_name = state.browser_checkpoint_tool or "paused browser tool"
        target = state.browser_checkpoint_target or state.browser_expected_target or "current page target"
        reason = state.browser_checkpoint_reason or "explicit operator approval was given"
        checkpoint_args = getattr(state, "browser_checkpoint_resume_args", {}) if isinstance(getattr(state, "browser_checkpoint_resume_args", {}), dict) else {}
        restore_url = str(checkpoint_args.get("url", "")).strip()
        resume_value = str(checkpoint_args.get("resume_value", "")).strip()
        resume_label = str(checkpoint_args.get("resume_label", "")).strip()
        restore_note = ""
        if restore_url or (resume_label and resume_value):
            restore_parts: list[str] = []
            if restore_url:
                restore_parts.append(f"re-open {restore_url}")
            if resume_label and resume_value:
                restore_parts.append(f"restore the already-approved field state by typing {resume_value!r} into {resume_label!r}")
            restore_note = (
                " If the browser page or form state was lost between runs, first "
                + ", then ".join(restore_parts)
                + " before resuming the paused click."
            )
        return (
            f"{state.goal}\n\n"
            "Operator control: The paused browser checkpoint is now explicitly approved. "
            f"Resume the exact paused browser tool immediately with approval_status=approved. Tool: {tool_name}. "
            f"Paused step: {step}. Target: {target}. Approval reason: {reason}. "
            "Do not reinterpret the paused target, do not choose a different risky browser action first, "
            "and do not claim success unless the resumed action actually succeeds."
            f"{restore_note}"
        )

    def _browser_reject_message(self, state: TaskState) -> str:
        step = state.browser_checkpoint_step or state.browser_last_action or "paused browser step"
        reason = state.browser_checkpoint_reason or "approval was not granted"
        return f"Rejected paused browser action at {step}; no browser action was performed. Reason: {reason}"

    def _desktop_resume_goal(self, state: TaskState) -> str:
        tool_name = state.desktop_checkpoint_tool or "paused desktop tool"
        target = state.desktop_checkpoint_target or state.desktop_last_target_window or state.desktop_active_window_title or "active desktop window"
        reason = state.desktop_checkpoint_reason or "explicit operator approval was given"
        checkpoint_args = (
            getattr(state, "desktop_checkpoint_resume_args", {})
            if isinstance(getattr(state, "desktop_checkpoint_resume_args", {}), dict)
            else {}
        )
        restore_title = str(checkpoint_args.get("expected_window_title", "")).strip() or state.desktop_active_window_title
        restore_note = ""
        if restore_title:
            restore_note = (
                " If the active window changed between runs, first focus "
                f"{restore_title!r} before resuming the paused desktop action."
            )
        return (
            f"{state.goal}\n\n"
            "Operator control: The paused desktop checkpoint is now explicitly approved. "
            f"Resume the exact paused desktop tool immediately with approval_status=approved. Tool: {tool_name}. "
            f"Target: {target}. Approval reason: {reason}. "
            "Do not substitute a different desktop action, do not choose new coordinates or text, "
            "and do not claim success unless the resumed desktop action actually succeeds."
            f"{restore_note}"
        )

    def _desktop_reject_message(self, state: TaskState) -> str:
        tool_name = state.desktop_checkpoint_tool or "paused desktop action"
        target = state.desktop_checkpoint_target or state.desktop_last_target_window or state.desktop_active_window_title or "desktop target"
        reason = state.desktop_checkpoint_reason or "approval was not granted"
        return f"Rejected paused desktop action for {target} ({tool_name}); no desktop action was performed. Reason: {reason}"

    def _task_queue_status_from_state(self, state: TaskState, result: Dict[str, Any]) -> tuple[str, bool, Dict[str, Any]]:
        control_snapshot = state.get_control_snapshot()
        pending = control_snapshot.get("pending_approval", {})
        result_status = str(result.get("status", state.status)).strip() or state.status
        if pending.get("kind"):
            return "paused", True, pending
        if result_status == "paused":
            return "paused", True, pending
        if result_status in QUEUE_ALLOWED_STATUSES:
            return result_status, False, pending
        return "failed", False, pending

    def _sync_scheduled_tasks_locked(self) -> bool:
        changed = False
        now = _local_now()

        for scheduled_task in self._scheduled_tasks:
            before = dict(scheduled_task)
            linked_task_id = scheduled_task.get("linked_task_id", "")
            if linked_task_id:
                queue_task = self._find_task_locked(linked_task_id)
                if queue_task is None:
                    if scheduled_task.get("recurrence") == "daily" and scheduled_task.get("status") in {"queued", "running", "paused"}:
                        base_dt = _parse_local_datetime(scheduled_task.get("next_run_at", "")) or _parse_local_datetime(scheduled_task.get("scheduled_for", "")) or now
                        scheduled_task["status"] = "scheduled"
                        scheduled_task["next_run_at"] = _iso_from_datetime(_next_daily_occurrence(base_dt, now))
                        scheduled_task["linked_task_id"] = ""
                        scheduled_task["queued_at"] = ""
                        scheduled_task["started_at"] = ""
                        scheduled_task["ended_at"] = ""
                        scheduled_task["approval_needed"] = False
                        scheduled_task["approval_reason"] = ""
                        scheduled_task["paused"] = False
                        scheduled_task["last_run_status"] = scheduled_task.get("last_run_status") or "failed"
                        scheduled_task["last_message"] = _trim_text(
                            scheduled_task.get("last_message", "") or "Previous scheduled run was not found; kept the next daily occurrence.",
                            limit=280,
                        )
                    if scheduled_task != before:
                        scheduled_task["updated_at"] = _iso_timestamp()
                        changed = True
                    continue

                queue_status = _normalize_status(queue_task.get("status", "queued"))
                scheduled_task["status"] = queue_status
                scheduled_task["queued_at"] = queue_task.get("created_at", "") or scheduled_task.get("queued_at", "")
                scheduled_task["started_at"] = queue_task.get("started_at", "") or scheduled_task.get("started_at", "")
                scheduled_task["ended_at"] = queue_task.get("ended_at", "") or scheduled_task.get("ended_at", "")
                scheduled_task["last_run_id"] = _trim_text(queue_task.get("run_id", ""), limit=60)
                scheduled_task["last_message"] = _trim_text(queue_task.get("last_message", ""), limit=280)
                scheduled_task["approval_needed"] = bool(queue_task.get("approval_needed", False))
                scheduled_task["approval_reason"] = _trim_text(queue_task.get("approval_reason", ""), limit=180)
                scheduled_task["paused"] = bool(queue_task.get("paused", False))

                if queue_status in QUEUE_TERMINAL_STATUSES:
                    scheduled_task["last_run_status"] = queue_status
                    if scheduled_task.get("recurrence") == "daily":
                        base_dt = _parse_local_datetime(scheduled_task.get("next_run_at", "")) or _parse_local_datetime(scheduled_task.get("scheduled_for", "")) or now
                        scheduled_task["next_run_at"] = _iso_from_datetime(_next_daily_occurrence(base_dt, now))
                        scheduled_task["status"] = "scheduled"
                        scheduled_task["linked_task_id"] = ""
                        scheduled_task["queued_at"] = ""
                        scheduled_task["started_at"] = ""
                        scheduled_task["ended_at"] = ""
                        scheduled_task["approval_needed"] = False
                        scheduled_task["approval_reason"] = ""
                        scheduled_task["paused"] = False
                        if not scheduled_task.get("last_message"):
                            scheduled_task["last_message"] = f"Last daily run finished with status {queue_status}."

            if scheduled_task != before:
                scheduled_task["updated_at"] = _iso_timestamp()
                changed = True

        if changed:
            self._scheduled_tasks = self.scheduled_store._trim_tasks(self._scheduled_tasks)
        return changed

    def _promote_due_scheduled_tasks_locked(self) -> tuple[int, bool]:
        promoted = 0
        auto_start = False
        now = _local_now()
        due_ids = self.scheduler_backend.drain_due_scheduled_ids()
        had_queued_before = self._has_queued_tasks_locked()

        for scheduled_task in self._scheduled_tasks:
            if scheduled_task.get("status") != "scheduled":
                continue

            next_run = _parse_local_datetime(scheduled_task.get("next_run_at", ""))
            scheduled_id = str(scheduled_task.get("scheduled_id", "")).strip()
            backend_due = bool(scheduled_id and scheduled_id in due_ids)
            if not backend_due and (next_run is None or next_run > now):
                continue

            queue_task = self._enqueue_task_locked(
                scheduled_task.get("goal", ""),
                source=scheduled_task.get("source", "scheduled_goal") or "scheduled_goal",
                scheduled_task_id=scheduled_task.get("scheduled_id", ""),
            )
            if queue_task is None:
                scheduled_task["last_message"] = "Scheduled task is due but waiting for queue space."
                scheduled_task["updated_at"] = _iso_timestamp()
                continue

            scheduled_task["status"] = "queued"
            scheduled_task["linked_task_id"] = queue_task.get("task_id", "")
            scheduled_task["queued_at"] = _iso_timestamp()
            scheduled_task["started_at"] = ""
            scheduled_task["ended_at"] = ""
            scheduled_task["last_message"] = "Scheduled task became due and entered the queue."
            scheduled_task["approval_needed"] = False
            scheduled_task["approval_reason"] = ""
            scheduled_task["paused"] = False
            scheduled_task["updated_at"] = _iso_timestamp()
            promoted += 1

        if promoted and not had_queued_before and not self._is_running():
            active_task = self._active_task_locked()
            if active_task is None or active_task.get("status") not in QUEUE_ACTIVE_STATUSES:
                auto_start = self._has_queued_tasks_locked()

        if promoted:
            self._scheduled_tasks = self.scheduled_store._trim_tasks(self._scheduled_tasks)
        return promoted, auto_start

    def _watch_due_locked(self, watch: Dict[str, Any], now_ts: float) -> bool:
        last_checked = _parse_local_datetime(watch.get("last_checked_at", ""))
        condition_type = str(watch.get("condition_type", "")).strip().lower()
        target_path = str(watch.get("target", "")).strip()
        if condition_type in FILE_WATCH_SUPPORTED_CONDITIONS and target_path:
            since_timestamp = last_checked.timestamp() if last_checked is not None else 0.0
            if self.file_watch_backend.has_recent_signal(target_path, since_timestamp=since_timestamp):
                return True
        if last_checked is None:
            return True
        interval_seconds = max(2, int(watch.get("interval_seconds", self.scheduler_poll_seconds)))
        return (now_ts - last_checked.timestamp()) >= interval_seconds

    def _watch_should_trigger_locked(self, watch: Dict[str, Any], evaluation: Dict[str, Any]) -> bool:
        if not evaluation.get("ok", False) or not evaluation.get("met", False):
            return False

        signature = str(evaluation.get("signature", "")).strip()
        last_trigger_signature = str(watch.get("last_trigger_signature", "")).strip()
        condition_type = str(watch.get("condition_type", "")).strip()

        if condition_type in WATCH_CHANGE_CONDITIONS:
            baseline_signature = str(watch.get("baseline_signature", "")).strip()
            if not baseline_signature or signature == baseline_signature:
                return False
            if signature and signature == last_trigger_signature:
                return False
            return True

        if not bool(watch.get("last_condition_met", False)):
            return True
        if bool(watch.get("allow_repeat", False)) and signature and signature != last_trigger_signature:
            return True
        return False

    def _process_watches_locked(self, *, force_check: bool = False) -> tuple[bool, bool]:
        changed = False
        auto_start = False
        now_ts = time.time()
        now_text = _iso_timestamp(now_ts)
        backend_events = self.file_watch_backend.consume_events(limit=24)
        if backend_events:
            self._recent_file_watch_events = list(backend_events)[-24:]
        had_queued_before = self._has_queued_tasks_locked()
        state = self._load_state_for_scope_locked(DEFAULT_STATE_SCOPE_ID)

        for watch in self._watches:
            before = dict(watch)
            linked_task_id = str(watch.get("linked_task_id", "")).strip()
            if linked_task_id:
                queue_task = self._find_task_locked(linked_task_id)
                if queue_task is None:
                    watch["linked_task_id"] = ""
                    watch["pending_enqueue"] = False
                    watch["approval_needed"] = False
                    watch["approval_reason"] = ""
                    if bool(watch.get("allow_repeat", False)):
                        watch["status"] = "watching"
                        watch["last_message"] = _trim_text(watch.get("last_message", "") or "Previous triggered task was not found; watch re-armed.", limit=280)
                    else:
                        watch["status"] = watch.get("last_run_status", "failed") or "failed"
                else:
                    queue_status = _normalize_status(queue_task.get("status", "queued"))
                    watch["last_run_id"] = _trim_text(queue_task.get("run_id", ""), limit=60)
                    watch["last_message"] = _trim_text(queue_task.get("last_message", ""), limit=280)
                    watch["approval_needed"] = bool(queue_task.get("approval_needed", False))
                    watch["approval_reason"] = _trim_text(queue_task.get("approval_reason", ""), limit=180)
                    if queue_status in {"queued", "running"}:
                        watch["status"] = "triggered"
                    elif queue_status == "paused":
                        watch["status"] = "paused"
                    else:
                        watch["last_run_status"] = queue_status
                        watch["linked_task_id"] = ""
                        watch["pending_enqueue"] = False
                        watch["approval_needed"] = False
                        watch["approval_reason"] = ""
                        if bool(watch.get("allow_repeat", False)):
                            watch["status"] = "watching"
                        else:
                            watch["status"] = queue_status

            if not str(watch.get("linked_task_id", "")).strip():
                if watch.get("status") == "triggered" and bool(watch.get("pending_enqueue", False)):
                    queue_task = self._enqueue_task_locked(
                        watch.get("goal", ""),
                        source=watch.get("source", "watch_trigger") or "watch_trigger",
                        watch_id=watch.get("watch_id", ""),
                    )
                    if queue_task is None:
                        watch["last_message"] = "Condition met; waiting for queue space."
                    else:
                        watch["linked_task_id"] = queue_task.get("task_id", "")
                        watch["status"] = "triggered"
                        watch["pending_enqueue"] = False
                        watch["approval_needed"] = False
                        watch["approval_reason"] = ""
                        watch["last_message"] = "Condition met and triggered task entered the queue."
                elif watch.get("status") == "watching" and (force_check or self._watch_due_locked(watch, now_ts)):
                    evaluation = evaluate_watch_condition(watch, state)
                    watch["last_checked_at"] = now_text
                    watch["last_signature"] = _trim_text(evaluation.get("signature", ""), limit=80)
                    watch["error"] = _trim_text(evaluation.get("error", ""), limit=200)
                    watch["last_message"] = _trim_text(evaluation.get("message", watch.get("last_message", "")), limit=280)
                    if evaluation.get("baseline_signature") and not str(watch.get("baseline_signature", "")).strip():
                        watch["baseline_signature"] = _trim_text(evaluation.get("baseline_signature", ""), limit=80)

                    if not evaluation.get("ok", False):
                        watch["last_condition_met"] = False
                    else:
                        met = bool(evaluation.get("met", False))
                        should_trigger = self._watch_should_trigger_locked(watch, evaluation)
                        watch["last_condition_met"] = met
                        if should_trigger:
                            signature = _trim_text(evaluation.get("signature", ""), limit=80)
                            watch["trigger_count"] = int(watch.get("trigger_count", 0) or 0) + 1
                            watch["last_triggered_at"] = now_text
                            watch["last_trigger_signature"] = signature
                            if str(watch.get("condition_type", "")).strip() in WATCH_CHANGE_CONDITIONS and signature:
                                watch["baseline_signature"] = signature
                            queue_task = self._enqueue_task_locked(
                                watch.get("goal", ""),
                                source=watch.get("source", "watch_trigger") or "watch_trigger",
                                watch_id=watch.get("watch_id", ""),
                            )
                            if queue_task is None:
                                watch["status"] = "triggered"
                                watch["pending_enqueue"] = True
                                watch["last_message"] = "Condition met; waiting for queue space."
                                self._append_alert_locked(
                                    severity="warning",
                                    alert_type="watch_triggered",
                                    source="watch",
                                    title="Watch triggered and waiting for queue space",
                                    message=watch.get("last_message", ""),
                                    goal=watch.get("goal", ""),
                                    watch_id=watch.get("watch_id", ""),
                                )
                            else:
                                watch["status"] = "triggered"
                                watch["linked_task_id"] = queue_task.get("task_id", "")
                                watch["pending_enqueue"] = False
                                watch["last_message"] = _trim_text(evaluation.get("message", "Condition met and watch triggered."), limit=280)
                                watch["approval_needed"] = False
                                watch["approval_reason"] = ""
                                self._append_alert_locked(
                                    severity="info",
                                    alert_type="watch_triggered",
                                    source="watch",
                                    title="Watch triggered",
                                    message=watch.get("last_message", ""),
                                    goal=watch.get("goal", ""),
                                    task_id=queue_task.get("task_id", ""),
                                    watch_id=watch.get("watch_id", ""),
                                )

            if watch != before:
                watch["updated_at"] = now_text
                changed = True

        if changed:
            self._watches = self.watch_store._trim_watches(self._watches)
        if changed and not had_queued_before and not self._is_running():
            active_task = self._active_task_locked()
            if active_task is None or active_task.get("status") not in QUEUE_ACTIVE_STATUSES:
                auto_start = self._has_queued_tasks_locked()
        return changed, auto_start

    def _update_task_from_result_locked(self, task: Dict[str, Any], state: TaskState, result: Dict[str, Any]):
        queue_status, keep_active, pending = self._task_queue_status_from_state(state, result)
        task["goal"] = _trim_text(state.goal, limit=MAX_TASK_GOAL_CHARS)
        task["status"] = queue_status
        task["run_id"] = _trim_text(result.get("run_id", ""), limit=60)
        task["last_message"] = _trim_text(result.get("message", "") or state.last_summary, limit=280)
        task["approval_needed"] = bool(pending.get("kind"))
        task["approval_reason"] = _trim_text(pending.get("reason", "") or pending.get("summary", ""), limit=180)
        task["paused"] = keep_active
        self._set_task_control_fields_locked(
            task,
            event=getattr(state, "task_control_event", ""),
            reason=getattr(state, "task_control_reason", ""),
            replacement_task_id=getattr(state, "task_replacement_task_id", ""),
            replacement_goal=getattr(state, "task_replacement_goal", ""),
            resume_available=getattr(state, "task_resume_available", False),
        )
        if not task.get("started_at"):
            task["started_at"] = _iso_timestamp()
        if keep_active:
            task["ended_at"] = ""
            self._active_task_id = task.get("task_id", "")
        else:
            task["ended_at"] = _iso_timestamp()
            if self._active_task_id == task.get("task_id", ""):
                self._active_task_id = ""
        self._append_task_status_alert_locked(task, state, result, queue_status=queue_status, pending=pending)
        self._sync_scheduled_tasks_locked()
        self._process_watches_locked(force_check=False)

    def _update_task_after_manual_action_locked(
        self,
        task: Dict[str, Any],
        *,
        status: str,
        message: str,
        run_id: str = "",
        approval_needed: bool = False,
        approval_reason: str = "",
        state: TaskState | None = None,
        control_event: str = "",
        replacement_task_id: str = "",
        replacement_goal: str = "",
        resume_available: bool = False,
    ):
        effective_event = control_event or (getattr(state, "task_control_event", "") if state is not None else "")
        effective_replacement_task_id = replacement_task_id or (getattr(state, "task_replacement_task_id", "") if state is not None else "")
        effective_replacement_goal = replacement_goal or (getattr(state, "task_replacement_goal", "") if state is not None else "")
        effective_resume_available = resume_available or (bool(getattr(state, "task_resume_available", False)) if state is not None else False)
        task["status"] = status
        task["last_message"] = _trim_text(message, limit=280)
        task["approval_needed"] = approval_needed
        task["approval_reason"] = _trim_text(approval_reason, limit=180)
        task["paused"] = approval_needed or status == "paused"
        self._set_task_control_fields_locked(
            task,
            event=effective_event,
            reason=message,
            replacement_task_id=effective_replacement_task_id,
            replacement_goal=effective_replacement_goal,
            resume_available=effective_resume_available,
        )
        if run_id:
            task["run_id"] = _trim_text(run_id, limit=60)
        if status in QUEUE_ACTIVE_STATUSES and task["paused"]:
            task["ended_at"] = ""
            self._active_task_id = task.get("task_id", "")
        elif status in {"queued", "deferred"}:
            task["ended_at"] = ""
            if self._active_task_id == task.get("task_id", ""):
                self._active_task_id = ""
        else:
            task["ended_at"] = _iso_timestamp()
            if self._active_task_id == task.get("task_id", ""):
                self._active_task_id = ""
        if state is not None:
            self._append_manual_status_alert_locked(task, state, status=status, message=message)
        self._sync_scheduled_tasks_locked()
        self._process_watches_locked(force_check=False)

    def _start_worker_locked(
        self,
        state: TaskState,
        *,
        task_id: str,
        planning_goal: str | None = None,
        history_start_index: int | None = None,
        run_source: str = "goal_run",
        session_id: str = "",
    ):
        def control_callback():
            with self._lock:
                return self._consume_control_request_locked(task_id)

        def runner():
            auto_start_next = False
            result: Dict[str, Any] = {}
            try:
                result = self.agent.run_state(
                    state,
                    planning_goal=planning_goal,
                    history_start_index=history_start_index,
                    run_source=run_source,
                    session_id=session_id,
                    control_callback=control_callback,
                )
                with self._lock:
                    self._set_current_state_locked(state)
                    self._set_last_result(result)
                    self._control_requests.pop(task_id, None)
                    task = self._find_task_locked(task_id)
                    if task is not None:
                        self._update_task_from_result_locked(task, state, result)
                        auto_start_next = not task.get("paused", False) and self._has_queued_tasks_locked()
                    self._worker = None
                    self._persist_all_locked()
            except Exception as exc:
                failure_message = f"Execution manager post-processing failed: {type(exc).__name__}: {exc}"
                with self._lock:
                    self._control_requests.pop(task_id, None)
                    state.status = "blocked"
                    state.add_step(
                        {
                            "type": "system",
                            "status": "failed",
                            "message": failure_message,
                            "tool": "execution_manager",
                        }
                    )
                    state.add_note(failure_message)
                    self._refresh_summary(state)
                    try:
                        self.agent.save_task_state(state, state_scope_id=state.state_scope_id)
                    except Exception:
                        pass
                    self._set_current_state_locked(state)
                    task = self._find_task_locked(task_id)
                    if task is not None:
                        task["status"] = "blocked"
                        task["last_message"] = _trim_text(failure_message, limit=280)
                        task["approval_needed"] = False
                        task["approval_reason"] = ""
                        task["paused"] = False
                        task["ended_at"] = _iso_timestamp()
                        if str(result.get("run_id", "")).strip():
                            task["run_id"] = _trim_text(result.get("run_id", ""), limit=60)
                    if self._active_task_id == task_id:
                        self._active_task_id = ""
                    self._set_last_result(
                        {
                            "ok": False,
                            "status": "blocked",
                            "message": failure_message,
                            "error": str(exc),
                            "exception_type": type(exc).__name__,
                            "run_id": _trim_text(result.get("run_id", ""), limit=60),
                            "steps": state.steps,
                        }
                    )
                    self._worker = None
                    self._persist_all_locked()

            if auto_start_next:
                self.start_next(auto_trigger=True)

        worker = threading.Thread(target=runner, name="operator-execution-run", daemon=True)
        self._worker = worker
        worker.start()

    def _start_task_locked(
        self,
        task: Dict[str, Any],
        *,
        run_source: str = "goal_run",
        planning_goal: str | None = None,
        history_start_index: int | None = None,
    ):
        state_scope_id = self._task_state_scope_id_locked(task)
        session_id = self._task_session_id_locked(task)
        state = self.agent.load_task_state(task.get("goal", ""), state_scope_id=state_scope_id)
        state.state_scope_id = state_scope_id
        state.status = "running"
        if hasattr(state, "clear_desktop_run_outcome"):
            state.clear_desktop_run_outcome()
        self.agent.save_task_state(state, state_scope_id=state_scope_id)
        self._set_current_state_locked(state)
        task["status"] = "running"
        task["started_at"] = task.get("started_at") or _iso_timestamp()
        task["ended_at"] = ""
        task["last_message"] = "Running in background."
        task["approval_needed"] = False
        task["approval_reason"] = ""
        task["paused"] = False
        self._set_task_control_fields_locked(task)
        state.clear_task_control()
        self._active_task_id = task.get("task_id", "")
        self._set_last_result({})
        if task.get("scheduled_task_id") or str(task.get("source", "")).strip() == "scheduled_goal":
            self._append_alert_locked(
                severity="info",
                alert_type="scheduled_task_started",
                source="scheduler",
                title="Scheduled task started",
                message=str(task.get("goal", "") or "A scheduled task started running.").strip(),
                goal=task.get("goal", ""),
                task_id=task.get("task_id", ""),
                scheduled_id=task.get("scheduled_task_id", ""),
            )
        self._sync_scheduled_tasks_locked()
        self._process_watches_locked(force_check=False)
        self._persist_all_locked()
        self._start_worker_locked(
            state,
            task_id=task.get("task_id", ""),
            planning_goal=planning_goal,
            history_start_index=(len(state.steps) if history_start_index is None else history_start_index),
            run_source=run_source,
            session_id=session_id,
        )

    def create_watch(
        self,
        goal: str,
        condition_type: str,
        target: str,
        match_text: str = "",
        *,
        interval_seconds: int = 10,
        allow_repeat: bool = False,
    ) -> Dict[str, Any]:
        goal_text = str(goal).strip()
        condition_text = str(condition_type).strip().lower()
        target_text = str(target).strip()
        match_text_value = str(match_text).strip()
        if not goal_text:
            return {"ok": False, "message": "Enter a goal before creating the watch."}
        if not target_text and condition_text != "browser_text_contains":
            return {"ok": False, "message": "Enter a watch target before creating the watch."}
        if condition_text == "browser_text_contains" and not match_text_value:
            return {"ok": False, "message": "Enter expected browser text before creating the watch."}

        interval_value = max(2, int(interval_seconds or 10))
        auto_start = False
        with self._lock:
            watch = self._create_watch_locked(
                goal_text,
                condition_type=condition_text,
                target=target_text,
                match_text=match_text_value,
                interval_seconds=interval_value,
                allow_repeat=allow_repeat,
            )
            self._watches.append(watch)
            self._watches = self.watch_store._trim_watches(self._watches)
            changed, auto_start = self._process_watches_locked(force_check=True)
            if changed:
                self._persist_all_locked()
            else:
                self._persist_watches_locked()

        if auto_start:
            self.start_next(auto_trigger=True)

        if watch.get("linked_task_id") or watch.get("pending_enqueue"):
            return {"ok": True, "watch_id": watch.get("watch_id", ""), "triggered": True, "message": watch.get("last_message", "Watch triggered immediately.")}
        return {"ok": True, "watch_id": watch.get("watch_id", ""), "triggered": False, "message": watch.get("last_message", "Watch created.")}

    def enqueue_goal(
        self,
        goal: str,
        *,
        source: str = "goal_run",
        start_if_idle: bool = False,
        session_id: str = "",
        state_scope_id: str = "",
    ) -> Dict[str, Any]:
        goal_text = str(goal).strip()
        if not goal_text:
            return {"ok": False, "message": "Enter a goal before queueing the task."}

        with self._lock:
            task = self._enqueue_task_locked(
                goal_text,
                source=source,
                session_id=session_id,
                state_scope_id=state_scope_id,
            )
            if task is None:
                return {"ok": False, "message": "The task queue is full. Resolve or clear older tasks before queueing more."}

            started = False
            if start_if_idle and self._can_start_next_locked():
                self._start_task_locked(task, run_source=source)
                started = True
            else:
                self._persist_all_locked()

        response = {
            "ok": True,
            "task_id": task.get("task_id", ""),
            "session_id": self._task_session_id_locked(task),
            "state_scope_id": self._task_state_scope_id_locked(task),
            "started": started,
        }
        if started:
            response["message"] = "Started goal in the background."
        else:
            response["message"] = "Queued goal."
        return response

    def start_goal(self, goal: str, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        return self.enqueue_goal(goal, source="goal_run", start_if_idle=True, session_id=session_id, state_scope_id=state_scope_id)

    def stop_task(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        auto_start = False
        with self._lock:
            task = self._find_controllable_task_locked(session_id=session_id, state_scope_id=state_scope_id)
            if task is None:
                return {"ok": False, "message": "There is no controllable task to stop."}

            message = "Stopped the task by explicit operator request."
            if task.get("status") == "running" and task.get("task_id", "") == self._active_task_id and self._is_running():
                state = self._load_state_for_scope_locked(self._task_state_scope_id_locked(task), clear_pending_for_new_goal=False)
                message = "Stop requested. The operator will stop this task after the current bounded step finishes."
                self._mark_control_requested_locked(task, state, action="stop", message=message)
                self._set_control_request_locked(task, action="stop", reason=message)
                self._persist_all_locked()
                return {"ok": True, "requested": True, "task_id": task.get("task_id", ""), "status": "running", "message": message}

            result = self._finalize_task_control_locked(task, status="stopped", control_event="stopped", message=message)
            self._persist_all_locked()
            auto_start = self._has_queued_tasks_locked()

        if auto_start:
            self.start_next(auto_trigger=True)
        return result

    def defer_task(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        with self._lock:
            task = self._find_controllable_task_locked(session_id=session_id, state_scope_id=state_scope_id)
            if task is None:
                return {"ok": False, "message": "There is no controllable task to defer."}

            message = "Deferred the task for later resumption."
            if task.get("status") == "running" and task.get("task_id", "") == self._active_task_id and self._is_running():
                state = self._load_state_for_scope_locked(self._task_state_scope_id_locked(task), clear_pending_for_new_goal=False)
                message = "Defer requested. The operator will pause this task after the current bounded step finishes."
                self._mark_control_requested_locked(task, state, action="defer", message=message)
                self._set_control_request_locked(task, action="defer", reason=message)
                self._persist_all_locked()
                return {"ok": True, "requested": True, "task_id": task.get("task_id", ""), "status": "running", "message": message}

            result = self._finalize_task_control_locked(task, status="deferred", control_event="deferred", message=message)
            self._persist_all_locked()
            return result

    def resume_task(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        with self._lock:
            task = self._select_primary_task_locked(
                self._matching_tasks_locked(session_id=session_id, state_scope_id=state_scope_id, statuses={"deferred", "paused", "queued"})
            ) if (session_id or state_scope_id) else self._select_primary_task_locked([item for item in self._tasks if item.get("status") in {"deferred", "paused", "queued"}])
            if task is None:
                return {"ok": False, "message": "There is no resumable task for this session."}

            if task.get("status") == "running":
                return {"ok": True, "task_id": task.get("task_id", ""), "status": "running", "message": "That task is already running."}

            state = self._load_state_for_scope_locked(self._task_state_scope_id_locked(task), clear_pending_for_new_goal=False)
            pending = state.get_control_snapshot().get("pending_approval", {})
            if task.get("status") == "paused" and pending.get("kind"):
                return {"ok": False, "message": "That task is paused behind an approval gate. Approve or reject it instead of using resume."}
            if task.get("status") not in {"deferred", "paused", "queued"}:
                return {"ok": False, "message": "That task is not in a resumable state."}

            if task.get("status") == "deferred":
                note = "Resumed the deferred task."
            elif task.get("status") == "paused":
                note = "Resumed the paused task."
            else:
                note = "Kept the queued task active and ready to run."
            state.add_step({"type": "system", "status": "resumed", "message": note, "tool": "operator_control"})
            state.set_task_control(event="resumed", reason=note, resume_available=False)
            state.add_note(note)
            self._refresh_summary(state)
            state.status = "queued"
            self.agent.save_task_state(state, state_scope_id=state.state_scope_id)
            self._set_current_state_locked(state)
            self._update_task_after_manual_action_locked(task, status="queued", message=note, state=state, control_event="resumed")

            started = False
            if self._can_start_next_locked():
                self._start_task_locked(task, run_source=task.get("source", "queued_goal") or "queued_goal")
                started = True
            else:
                self._persist_all_locked()

        return {
            "ok": True,
            "task_id": task.get("task_id", ""),
            "status": "running" if started else "queued",
            "message": "Resumed the task and restarted it." if started else "Resumed the task and returned it to the queue.",
            "started": started,
        }

    def retry_task(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        with self._lock:
            task = self._find_retryable_task_locked(session_id=session_id, state_scope_id=state_scope_id)
            if task is None:
                return {"ok": False, "message": "There is no retryable task for this session."}
            goal_text = str(task.get("goal", "")).strip()
            if not goal_text:
                return {"ok": False, "message": "The retryable task is missing a goal."}

        return self.enqueue_goal(
            goal_text,
            source="retry_goal",
            start_if_idle=True,
            session_id=session_id,
            state_scope_id=state_scope_id,
        )

    def replace_goal(self, goal: str, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        goal_text = str(goal).strip()
        if not goal_text:
            return {"ok": False, "message": "Enter a replacement goal before replacing the current task."}

        auto_start = False
        superseded_ids: List[str] = []
        with self._lock:
            replacement_task = self._enqueue_task_locked(
                goal_text,
                source="replacement_goal",
                session_id=session_id,
                state_scope_id=state_scope_id,
            )
            if replacement_task is None:
                return {"ok": False, "message": "The task queue is full. Resolve or clear older tasks before replacing work."}

            matched = self._matching_tasks_locked(
                session_id=session_id,
                state_scope_id=state_scope_id,
                statuses={"running", "paused", "queued", "deferred"},
            )
            for task in matched:
                if task.get("task_id") == replacement_task.get("task_id"):
                    continue
                superseded_message = "Superseded this task with a newer operator request."
                if task.get("status") == "running" and task.get("task_id", "") == self._active_task_id and self._is_running():
                    state = self._load_state_for_scope_locked(self._task_state_scope_id_locked(task), clear_pending_for_new_goal=False)
                    superseded_message = "Supersede requested. The operator will stop this task after the current bounded step and hand off to the replacement task."
                    self._mark_control_requested_locked(
                        task,
                        state,
                        action="supersede",
                        message=superseded_message,
                        replacement_goal=goal_text,
                        replacement_task_id=replacement_task.get("task_id", ""),
                    )
                    self._set_control_request_locked(
                        task,
                        action="supersede",
                        reason=superseded_message,
                        replacement_goal=goal_text,
                        replacement_task_id=replacement_task.get("task_id", ""),
                    )
                else:
                    self._finalize_task_control_locked(
                        task,
                        status="superseded",
                        control_event="superseded",
                        message=superseded_message,
                        replacement_goal=goal_text,
                        replacement_task_id=replacement_task.get("task_id", ""),
                    )
                superseded_ids.append(task.get("task_id", ""))

            if self._can_start_next_locked():
                self._start_task_locked(replacement_task, run_source="replacement_goal")
                auto_start = True
            else:
                self._persist_all_locked()

        return {
            "ok": True,
            "task_id": replacement_task.get("task_id", ""),
            "session_id": self._task_session_id_locked(replacement_task),
            "state_scope_id": self._task_state_scope_id_locked(replacement_task),
            "started": auto_start,
            "superseded_task_ids": [task_id for task_id in superseded_ids if task_id],
            "message": "Started the replacement goal." if auto_start else "Queued the replacement goal and marked older work as superseded.",
        }

    def schedule_goal(self, goal: str, run_at: str, *, recurrence: str = "once") -> Dict[str, Any]:
        goal_text = str(goal).strip()
        if not goal_text:
            return {"ok": False, "message": "Enter a goal before scheduling the task."}

        run_at_dt = _parse_local_datetime(run_at)
        if run_at_dt is None:
            return {"ok": False, "message": "Enter a valid local run time like 2026-03-10 14:30 or 2026-03-10T14:30."}

        recurrence_value = _normalize_recurrence(recurrence)
        auto_start = False
        with self._lock:
            scheduled_task = self._create_scheduled_task_locked(goal_text, run_at=run_at_dt, recurrence=recurrence_value)
            self._scheduled_tasks.append(scheduled_task)
            self._scheduled_tasks = self.scheduled_store._trim_tasks(self._scheduled_tasks)
            promoted, auto_start = self._promote_due_scheduled_tasks_locked()
            self._persist_all_locked()

        if auto_start:
            self.start_next(auto_trigger=True)

        if promoted:
            return {
                "ok": True,
                "scheduled_id": scheduled_task.get("scheduled_id", ""),
                "queued": True,
                "message": "Scheduled goal was already due, so it entered the queue immediately.",
            }
        return {
            "ok": True,
            "scheduled_id": scheduled_task.get("scheduled_id", ""),
            "queued": False,
            "message": f"Scheduled goal for {scheduled_task.get('next_run_at', '')} ({recurrence_value}).",
        }

    def start_next(self, *, auto_trigger: bool = False) -> Dict[str, Any]:
        with self._lock:
            if self._is_running():
                return {"ok": False, "message": "A background task is already running."}

            active_task = self._active_task_locked()
            if active_task and active_task.get("status") == "paused":
                return {"ok": False, "message": "Resolve the paused approval-required task before starting the next queued goal."}

            next_task = next((task for task in self._tasks if task.get("status") == "queued"), None)
            if next_task is None:
                return {"ok": False, "message": "There is no queued task to start."}

            self._start_task_locked(next_task, run_source=next_task.get("source", "queued_goal") or "queued_goal")
            task_id = next_task.get("task_id", "")

        if auto_trigger:
            return {"ok": True, "task_id": task_id, "started": True, "message": "Started the next queued goal automatically."}
        return {"ok": True, "task_id": task_id, "started": True, "message": "Started the next queued goal."}

    def approve_pending(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        with self._lock:
            if self._is_running():
                return {"ok": False, "message": "Wait for the current run to finish before approving."}

            active_task = self._find_pending_task_locked(session_id=session_id, state_scope_id=state_scope_id)
            if active_task is None:
                if session_id or state_scope_id:
                    return {"ok": False, "message": "There is no paused task waiting for approval for this session."}
                return {"ok": False, "message": "There is no active task waiting for approval."}

            session_id_value = self._task_session_id_locked(active_task)
            state = self._load_state_for_scope_locked(self._task_state_scope_id_locked(active_task), clear_pending_for_new_goal=False)
            pending = state.get_control_snapshot().get("pending_approval", {})
            kind = str(pending.get("kind", "")).strip()
            if not kind:
                return {"ok": False, "message": "There is no pending approval."}

            if kind == "browser_checkpoint":
                history_start_index = len(state.steps)
                note = "Operator approved paused browser checkpoint; resuming exact browser step."
                state.add_step(
                    {
                        "type": "system",
                        "status": "approved",
                        "message": note,
                        "tool": state.browser_checkpoint_tool or "browser_checkpoint",
                    }
                )
                state.add_note(note)
                self._refresh_summary(state)
                state.status = "running"
                state.set_task_control(event="approved", reason=note, resume_available=False)
                if hasattr(state, "clear_desktop_run_outcome"):
                    state.clear_desktop_run_outcome()
                self.agent.save_task_state(state, state_scope_id=state.state_scope_id)
                self._set_current_state_locked(state)
                self._update_task_after_manual_action_locked(active_task, status="running", message=note, approval_needed=False, approval_reason="", state=state)
                self._set_last_result({})
                self._persist_all_locked()
                self._start_worker_locked(
                    state,
                    task_id=active_task.get("task_id", ""),
                    planning_goal=self._browser_resume_goal(state),
                    history_start_index=history_start_index,
                    run_source="approval_resume",
                    session_id=session_id_value,
                )
                return {"ok": True, "message": "Approved paused browser checkpoint. Resuming workflow."}
            if kind == "desktop_action":
                history_start_index = len(state.steps)
                tool_name = state.desktop_checkpoint_tool or "desktop_action"
                note = "Operator approved the paused desktop action; resuming the exact bounded desktop step."
                state.add_step(
                    {
                        "type": "system",
                        "status": "approved",
                        "message": note,
                        "tool": tool_name,
                    }
                )
                state.add_note(note)
                state.status = "running"
                state.set_task_control(event="approved", reason=note, resume_available=False)
                if hasattr(state, "clear_desktop_run_outcome"):
                    state.clear_desktop_run_outcome()
                self._refresh_summary(state)
                self.agent.save_task_state(state, state_scope_id=state.state_scope_id)
                self._set_current_state_locked(state)
                self._update_task_after_manual_action_locked(
                    active_task,
                    status="running",
                    message=note,
                    approval_needed=False,
                    approval_reason="",
                    state=state,
                )
                self._set_last_result({})
                self._persist_all_locked()
                self._start_worker_locked(
                    state,
                    task_id=active_task.get("task_id", ""),
                    planning_goal=self._desktop_resume_goal(state),
                    history_start_index=history_start_index,
                    run_source="approval_resume",
                    session_id=session_id_value,
                )
                return {"ok": True, "message": "Approved paused desktop action. Resuming the bounded desktop step."}

            started_at = time.time()
            history_start_index = len(state.steps)
            if kind == "review_bundle":
                step = self._latest_review_bundle_step(state)
                if step is None:
                    return {"ok": False, "message": "No review bundle is available to approve."}

                result = step.get("result", {})
                result["approval_status"] = "approved"
                step["result"] = result
                state.add_step(
                    {
                        "type": "system",
                        "status": "approved",
                        "message": "Operator approved the review bundle. No changes were applied.",
                        "tool": "build_review_bundle",
                    }
                )
                state.add_note("Operator approved the review bundle; no changes were applied.")
                self._refresh_summary(state)
                state.status = "needs_attention"
                state.set_task_control(
                    event="approved",
                    reason="Operator approved the review bundle; no changes were applied.",
                    resume_available=False,
                )
                self.agent.save_task_state(state, state_scope_id=state.state_scope_id)
                self._set_current_state_locked(state)
                result_payload = {
                    "ok": True,
                    "status": state.status,
                    "message": "Review bundle approved. No changes were applied.",
                    "steps": state.steps,
                }
                self._record_manual_history(
                    state,
                    started_at=started_at,
                    step_start_index=history_start_index,
                    result=result_payload,
                    source="control_action",
                    session_id=session_id_value,
                )
                self._update_task_after_manual_action_locked(
                    active_task,
                    status="needs_attention",
                    message=result_payload["message"],
                    run_id=str(result_payload.get("run_id", "")),
                    state=state,
                )
                self._set_last_result(result_payload)
                self._persist_all_locked()
                auto_start = self._has_queued_tasks_locked()
            else:
                return {"ok": False, "message": f"Unsupported approval type: {kind}"}

        if auto_start:
            self.start_next(auto_trigger=True)
        return result_payload

    def reject_pending(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        with self._lock:
            if self._is_running():
                return {"ok": False, "message": "Wait for the current run to finish before rejecting."}

            active_task = self._find_pending_task_locked(session_id=session_id, state_scope_id=state_scope_id)
            if active_task is None:
                if session_id or state_scope_id:
                    return {"ok": False, "message": "There is no paused task waiting for approval for this session."}
                return {"ok": False, "message": "There is no active task waiting for approval."}

            session_id_value = self._task_session_id_locked(active_task)
            state = self._load_state_for_scope_locked(self._task_state_scope_id_locked(active_task), clear_pending_for_new_goal=False)
            pending = state.get_control_snapshot().get("pending_approval", {})
            kind = str(pending.get("kind", "")).strip()
            if not kind:
                return {"ok": False, "message": "There is no pending approval."}

            started_at = time.time()
            history_start_index = len(state.steps)
            if kind == "browser_checkpoint":
                message = self._browser_reject_message(state)
                state.add_step(
                    {
                        "type": "system",
                        "status": "rejected",
                        "message": message,
                        "tool": state.browser_checkpoint_tool or "browser_checkpoint",
                    }
                )
                state.add_note(message)
                state.browser_last_action = f"Rejected paused action: {state.browser_checkpoint_step or state.browser_checkpoint_tool or 'browser step'}"
                if state.browser_task_name:
                    state.browser_task_status = "blocked"
                if state.browser_workflow_name:
                    state.browser_workflow_status = "blocked"
                state.clear_browser_checkpoint()
                self._refresh_summary(state)
                state.status = "blocked"
                state.set_task_control(event="rejected", reason=message, resume_available=False)
                self.agent.save_task_state(state, state_scope_id=state.state_scope_id)
                self._set_current_state_locked(state)
                reply_message = self._render_authoritative_manual_reply_locked(state, message)
                result_payload = {
                    "ok": True,
                    "status": state.status,
                    "message": reply_message,
                    "steps": state.steps,
                }
                self._record_manual_history(
                    state,
                    started_at=started_at,
                    step_start_index=history_start_index,
                    result=result_payload,
                    source="control_action",
                    session_id=session_id_value,
                )
                self._update_task_after_manual_action_locked(
                    active_task,
                    status="blocked",
                    message=reply_message,
                    run_id=str(result_payload.get("run_id", "")),
                    state=state,
                    control_event="rejected",
                )
                self._set_last_result(result_payload)
                self._persist_all_locked()
                auto_start = self._has_queued_tasks_locked()
            elif kind == "desktop_action":
                message = self._desktop_reject_message(state)
                state.add_step(
                    {
                        "type": "system",
                        "status": "rejected",
                        "message": message,
                        "tool": state.desktop_checkpoint_tool or "desktop_action",
                    }
                )
                state.add_note(message)
                state.desktop_last_action = (
                    f"Rejected paused desktop action: {state.desktop_checkpoint_tool or 'desktop action'}"
                )
                state.clear_desktop_checkpoint()
                self._refresh_summary(state)
                state.status = "blocked"
                state.set_task_control(event="rejected", reason=message, resume_available=False)
                if hasattr(state, "set_desktop_run_outcome"):
                    state.set_desktop_run_outcome(
                        {
                            "outcome": "blocked",
                            "status": "blocked",
                            "terminal": True,
                            "reason": "approval_needed",
                            "summary": message,
                            "target_window_title": getattr(state, "desktop_last_target_window", ""),
                            "active_window_title": getattr(state, "desktop_active_window_title", ""),
                        }
                    )
                self.agent.save_task_state(state, state_scope_id=state.state_scope_id)
                self._set_current_state_locked(state)
                reply_message = self._render_authoritative_manual_reply_locked(state, message)
                result_payload = {
                    "ok": True,
                    "status": state.status,
                    "message": reply_message,
                    "steps": state.steps,
                }
                self._record_manual_history(
                    state,
                    started_at=started_at,
                    step_start_index=history_start_index,
                    result=result_payload,
                    source="control_action",
                    session_id=session_id_value,
                )
                self._update_task_after_manual_action_locked(
                    active_task,
                    status="blocked",
                    message=reply_message,
                    run_id=str(result_payload.get("run_id", "")),
                    state=state,
                    control_event="rejected",
                )
                self._set_last_result(result_payload)
                self._persist_all_locked()
                auto_start = self._has_queued_tasks_locked()
            elif kind == "review_bundle":
                step = self._latest_review_bundle_step(state)
                if step is None:
                    return {"ok": False, "message": "No review bundle is available to reject."}

                result = step.get("result", {})
                result["approval_status"] = "rejected"
                step["result"] = result
                message = "Review bundle rejected. No changes were applied."
                state.add_step(
                    {
                        "type": "system",
                        "status": "rejected",
                        "message": message,
                        "tool": "build_review_bundle",
                    }
                )
                state.add_note(message)
                self._refresh_summary(state)
                state.status = "blocked"
                state.set_task_control(event="rejected", reason=message, resume_available=False)
                self.agent.save_task_state(state, state_scope_id=state.state_scope_id)
                self._set_current_state_locked(state)
                reply_message = self._render_authoritative_manual_reply_locked(state, message)
                result_payload = {
                    "ok": True,
                    "status": state.status,
                    "message": reply_message,
                    "steps": state.steps,
                }
                self._record_manual_history(
                    state,
                    started_at=started_at,
                    step_start_index=history_start_index,
                    result=result_payload,
                    source="control_action",
                    session_id=session_id_value,
                )
                self._update_task_after_manual_action_locked(
                    active_task,
                    status="blocked",
                    message=reply_message,
                    run_id=str(result_payload.get("run_id", "")),
                    state=state,
                    control_event="rejected",
                )
                self._set_last_result(result_payload)
                self._persist_all_locked()
                auto_start = self._has_queued_tasks_locked()
            else:
                return {"ok": False, "message": f"Unsupported approval type: {kind}"}

        if auto_start:
            self.start_next(auto_trigger=True)
        return result_payload

    def _queue_counts_locked(self, tasks: List[Dict[str, Any]] | None = None) -> Dict[str, int]:
        counts: Dict[str, int] = {status: 0 for status in QUEUE_ALLOWED_STATUSES}
        source_tasks = tasks if tasks is not None else self._tasks
        for task in source_tasks:
            counts[_normalize_status(task.get("status", "queued"))] += 1
        return counts

    def _scheduled_counts_locked(self) -> Dict[str, int]:
        counts: Dict[str, int] = {status: 0 for status in SCHEDULE_ALLOWED_STATUSES}
        for task in self._scheduled_tasks:
            counts[_normalize_schedule_status(task.get("status", "scheduled"))] += 1
        return counts

    def _task_summary_locked(self, task: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "task_id": _trim_text(task.get("task_id", ""), limit=60),
            "session_id": self._task_session_id_locked(task),
            "state_scope_id": self._task_state_scope_id_locked(task),
            "goal": _trim_text(task.get("goal", ""), limit=220),
            "status": _normalize_status(task.get("status", "queued")),
            "created_at": _trim_text(task.get("created_at", ""), limit=40),
            "started_at": _trim_text(task.get("started_at", ""), limit=40),
            "ended_at": _trim_text(task.get("ended_at", ""), limit=40),
            "run_id": _trim_text(task.get("run_id", ""), limit=60),
            "source": _trim_text(task.get("source", ""), limit=40),
            "scheduled_task_id": _trim_text(task.get("scheduled_task_id", ""), limit=60),
            "watch_id": _trim_text(task.get("watch_id", ""), limit=60),
            "last_message": _trim_text(task.get("last_message", ""), limit=220),
            "approval_needed": bool(task.get("approval_needed", False)),
            "approval_reason": _trim_text(task.get("approval_reason", ""), limit=180),
            "paused": bool(task.get("paused", False)),
            "control": self._task_control_payload_locked(task),
        }

    def _scheduled_summary_locked(self, task: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "scheduled_id": _trim_text(task.get("scheduled_id", ""), limit=60),
            "goal": _trim_text(task.get("goal", ""), limit=220),
            "status": _normalize_schedule_status(task.get("status", "scheduled")),
            "recurrence": _normalize_recurrence(task.get("recurrence", "once")),
            "scheduled_for": _trim_text(task.get("scheduled_for", ""), limit=40),
            "next_run_at": _trim_text(task.get("next_run_at", ""), limit=40),
            "queued_at": _trim_text(task.get("queued_at", ""), limit=40),
            "started_at": _trim_text(task.get("started_at", ""), limit=40),
            "ended_at": _trim_text(task.get("ended_at", ""), limit=40),
            "linked_task_id": _trim_text(task.get("linked_task_id", ""), limit=60),
            "last_run_id": _trim_text(task.get("last_run_id", ""), limit=60),
            "last_run_status": _trim_text(task.get("last_run_status", ""), limit=40),
            "source": _trim_text(task.get("source", ""), limit=40),
            "last_message": _trim_text(task.get("last_message", ""), limit=220),
            "approval_needed": bool(task.get("approval_needed", False)),
            "approval_reason": _trim_text(task.get("approval_reason", ""), limit=180),
            "paused": bool(task.get("paused", False)),
            "control": self._task_control_payload_locked(task),
        }

    def _queue_snapshot_locked(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        filtered_view = bool(session_id or state_scope_id)
        matched_tasks = self._matching_tasks_locked(session_id=session_id, state_scope_id=state_scope_id) if filtered_view else list(self._tasks)
        active_candidates = [task for task in matched_tasks if task.get("status") in QUEUE_ACTIVE_STATUSES or task.get("status") in {"queued", "deferred"}]
        active_task = self._select_primary_task_locked(active_candidates) if filtered_view else self._active_task_locked()
        if filtered_view and active_task is None:
            active_task = self._select_primary_task_locked(matched_tasks)
        queued_tasks = [self._task_summary_locked(task) for task in matched_tasks if task.get("status") == "queued"]
        recent_tasks = [self._task_summary_locked(task) for task in list(reversed(matched_tasks[-8:]))]
        counts = self._queue_counts_locked(matched_tasks if filtered_view else self._tasks)
        can_start_next = self._can_start_next_locked()
        if filtered_view:
            can_start_next = can_start_next and any(task.get("status") == "queued" for task in matched_tasks)
        return {
            "active_task": self._task_summary_locked(active_task) if active_task else {},
            "queued_tasks": queued_tasks[:8],
            "recent_tasks": recent_tasks,
            "counts": counts,
            "can_start_next": can_start_next,
        }

    def _scheduled_snapshot_locked(self) -> Dict[str, Any]:
        tasks = [self._scheduled_summary_locked(task) for task in list(reversed(self._scheduled_tasks[-10:]))]
        counts = self._scheduled_counts_locked()
        return {
            "tasks": tasks,
            "counts": counts,
        }

    def _watch_snapshot_locked(self) -> Dict[str, Any]:
        tasks = [watch_summary(watch) for watch in list(reversed(self._watches[-10:]))]
        counts = watch_counts(self._watches)
        return {
            "tasks": tasks,
            "counts": counts,
        }

    def _alert_snapshot_locked(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        normalized_session_id = self._normalize_session_id(session_id)
        normalized_scope_id = self._normalize_state_scope_id(state_scope_id, session_id=normalized_session_id)
        alerts = list(self._alerts)
        if normalized_session_id or normalized_scope_id:
            filtered_alerts: List[Dict[str, Any]] = []
            for alert in alerts:
                alert_session_id = self._normalize_session_id(alert.get("session_id", ""))
                alert_scope_id = self._normalize_state_scope_id(alert.get("state_scope_id", ""), session_id=alert_session_id)
                if normalized_session_id and alert_session_id != normalized_session_id:
                    continue
                if normalized_scope_id and alert_scope_id != normalized_scope_id:
                    continue
                filtered_alerts.append(alert)
            alerts = filtered_alerts
        items = [alert_summary(alert) for alert in list(reversed(alerts[-12:]))]
        counts = alert_counts(alerts)
        return {
            "items": items,
            "counts": counts,
            "latest_alert": items[0] if items else {},
        }

    def _infrastructure_snapshot_locked(self) -> Dict[str, Any]:
        try:
            from tools.desktop import get_desktop_backend_status
        except Exception:
            desktop_status = {
                "window": {"active": "unavailable", "reason": "error", "available": False},
                "screenshot": {"active": "unavailable", "reason": "error", "available": False},
                "ui_evidence": {"active": "unavailable", "reason": "error", "available": False},
            }
        else:
            try:
                desktop_status = get_desktop_backend_status()
            except Exception:
                desktop_status = {
                    "window": {"active": "unavailable", "reason": "error", "available": False},
                    "screenshot": {"active": "unavailable", "reason": "error", "available": False},
                    "ui_evidence": {"active": "unavailable", "reason": "error", "available": False},
                }
        return {
            "scheduler": self.scheduler_backend.status_snapshot(),
            "file_watch": {
                **self.file_watch_backend.status_snapshot(),
                "recent_events": list(self._recent_file_watch_events[-12:]),
            },
            "desktop": desktop_status,
            "desktop_capture": self.desktop_capture_service.status_snapshot(),
        }

    def get_snapshot(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        auto_start = False
        filtered_view = bool(str(session_id).strip() or str(state_scope_id).strip())
        normalized_session_id = self._normalize_session_id(session_id)
        normalized_scope_id = self._normalize_state_scope_id(state_scope_id, session_id=normalized_session_id)

        with self._lock:
            changed = self._sync_scheduled_tasks_locked()
            promoted, scheduled_auto_start = self._promote_due_scheduled_tasks_locked()
            watch_changed, watch_auto_start = self._process_watches_locked()
            auto_start = scheduled_auto_start or watch_auto_start
            if changed or promoted or watch_changed:
                self._persist_all_locked()

            queue_snapshot = self._queue_snapshot_locked(
                session_id=normalized_session_id if filtered_view else "",
                state_scope_id=normalized_scope_id if filtered_view else "",
            )
            primary_task = queue_snapshot.get("active_task", {}) if isinstance(queue_snapshot.get("active_task", {}), dict) else {}
            live_active_task = self._active_task_locked()
            if filtered_view:
                state = self._load_state_for_scope_locked(normalized_scope_id, clear_pending_for_new_goal=False)
            else:
                active_scope_id = self._task_state_scope_id_locked(live_active_task) if live_active_task is not None else self._state_scope_id
                normalized_scope_id = self._normalize_state_scope_id(active_scope_id)
                state = self._load_state_for_scope_locked(normalized_scope_id, clear_pending_for_new_goal=False)

            snapshot = state.get_control_snapshot()
            if filtered_view and not primary_task and not self._state_has_activity_locked(state):
                snapshot["status"] = "idle"
                snapshot["current_step"] = ""
                snapshot["paused"] = False

            scheduled_snapshot = self._scheduled_snapshot_locked()
            watch_snapshot = self._watch_snapshot_locked()
            alert_snapshot = self._alert_snapshot_locked(
                session_id=normalized_session_id if filtered_view else "",
                state_scope_id=normalized_scope_id if filtered_view else "",
            )
            is_running = self._is_running()
            live_scope_running = False
            if filtered_view:
                live_scope_running = bool(
                    is_running
                    and live_active_task is not None
                    and self._task_matches_scope_locked(
                        live_active_task,
                        session_id=normalized_session_id,
                        state_scope_id=normalized_scope_id,
                    )
                )
                is_running = live_scope_running
                if live_scope_running:
                    live_summary = self._task_summary_locked(live_active_task)
                    primary_task = live_summary
                    queue_snapshot["active_task"] = live_summary
                    snapshot["status"] = "paused" if live_summary.get("status") == "paused" else "running"
            snapshot["running"] = is_running
            if filtered_view:
                recent_runs = self.agent.history_store.get_recent_runs(
                    limit=6,
                    session_id=normalized_session_id,
                    state_scope_id=normalized_scope_id,
                )
                latest_run = self.agent.history_store.get_latest_run(
                    session_id=normalized_session_id,
                    state_scope_id=normalized_scope_id,
                )
            else:
                recent_runs = self.agent.history_store.get_recent_runs(limit=6)
                latest_run = self.agent.history_store.get_latest_run()
            snapshot["recent_runs"] = recent_runs
            snapshot["latest_run"] = latest_run

            latest_run_status = _trim_text(latest_run.get("final_status", ""), limit=40)
            latest_run_message = _trim_text(latest_run.get("result_message", ""), limit=280)
            snapshot["result_status"] = _trim_text(primary_task.get("status", snapshot.get("status", "")), limit=80) or snapshot.get("status", "")
            if filtered_view and not live_scope_running and latest_run_status:
                if not primary_task or snapshot["result_status"] in QUEUE_TERMINAL_STATUSES:
                    snapshot["result_status"] = latest_run_status
            if not snapshot["result_status"] and not filtered_view:
                snapshot["result_status"] = str(self._last_result.get("status", snapshot.get("status", ""))).strip()

            result_message = _trim_text(primary_task.get("last_message", ""), limit=280)
            if filtered_view and not live_scope_running and latest_run_message:
                primary_run_id = _trim_text(primary_task.get("run_id", ""), limit=60)
                latest_run_id = _trim_text(latest_run.get("run_id", ""), limit=60)
                if (
                    not primary_task
                    or (latest_run_id and latest_run_id == primary_run_id)
                    or (latest_run_status and latest_run_status == snapshot.get("result_status", ""))
                ):
                    result_message = latest_run_message
            if not result_message:
                result_message = _trim_text(snapshot.get("rolling_summary", ""), limit=280)
            if not result_message and not filtered_view:
                result_message = self._last_result_message or snapshot.get("rolling_summary", "")
            snapshot["result_message"] = result_message
            snapshot["runtime"] = self.agent.get_runtime_config()
            snapshot["queue"] = queue_snapshot
            snapshot["active_task"] = queue_snapshot.get("active_task", {})
            snapshot["queued_tasks"] = queue_snapshot.get("queued_tasks", [])
            snapshot["scheduled"] = scheduled_snapshot
            snapshot["scheduled_tasks"] = scheduled_snapshot.get("tasks", [])
            snapshot["watches"] = watch_snapshot
            snapshot["watch_items"] = watch_snapshot.get("tasks", [])
            snapshot["alerts"] = alert_snapshot
            snapshot["alert_items"] = alert_snapshot.get("items", [])
            snapshot["latest_alert"] = alert_snapshot.get("latest_alert", {})
            snapshot["infrastructure"] = self._infrastructure_snapshot_locked()
            snapshot["session_id"] = normalized_session_id
            snapshot["state_scope_id"] = normalized_scope_id
            snapshot["paused"] = bool(snapshot.get("paused", False) or queue_snapshot.get("active_task", {}).get("status") == "paused")

        if auto_start:
            self.start_next(auto_trigger=True)
        return snapshot












