from __future__ import annotations

import time
from typing import Any, Dict

from core.capability_profiles import SAFE_BOUNDED_PROFILE, SANDBOXED_FULL_ACCESS_LAB_PROFILE, profile_metadata
from core.config import get_settings_snapshot, load_settings
from core.email_service import get_email_service
from core.llm_client import HostedLLMClient
from core.loop import run_task_loop
from core.operator_intelligence import (
    DEFAULT_OPERATOR_MEMORY_PATH,
    OperatorMemoryStore,
    build_environment_awareness,
    refresh_operator_intelligence_context,
)
from core.problem_records import DEFAULT_PROBLEM_RECORD_PATH, ProblemRecordStore
from core.run_history import DEFAULT_RUN_HISTORY_PATH, RunHistoryStore
from core.session_store import DEFAULT_SESSION_STATE_PATH, DEFAULT_STATE_SCOPE_ID, SessionStore
from core.state import TaskState
from core.tool_runtime import ToolRuntime
from tools.registry import get_tools


class Agent:
    def __init__(self, settings: Dict[str, Any] | None = None):
        self.settings = dict(settings) if isinstance(settings, dict) else load_settings()
        settings_snapshot = get_settings_snapshot()
        self._settings_version = str(settings_snapshot.get("version", "")).strip()
        self.llm = HostedLLMClient(settings=self.settings)
        self.email = get_email_service(self.settings)
        self.tools = ToolRuntime(get_tools())
        session_state_path = self.settings.get("session_state_path", DEFAULT_SESSION_STATE_PATH)
        self.session_store = SessionStore(session_state_path)
        run_history_path = self.settings.get("run_history_path", DEFAULT_RUN_HISTORY_PATH)
        max_runs = int(self.settings.get("max_run_history_entries", 25))
        self.history_store = RunHistoryStore(run_history_path, max_runs=max_runs)
        operator_memory_path = self.settings.get("operator_memory_path", str(DEFAULT_OPERATOR_MEMORY_PATH))
        self.operator_memory_store = OperatorMemoryStore(operator_memory_path)
        problem_record_path = self.settings.get("problem_record_path", str(DEFAULT_PROBLEM_RECORD_PATH))
        max_problem_records = int(self.settings.get("max_problem_records", 120) or 120)
        self.problem_store = ProblemRecordStore(problem_record_path, max_records=max_problem_records)

    def refresh_runtime_settings_if_needed(self, *, force: bool = False) -> bool:
        settings_snapshot = get_settings_snapshot(force=force)
        next_version = str(settings_snapshot.get("version", "")).strip()
        if not force and next_version and next_version == self._settings_version:
            return False

        reloaded_settings = settings_snapshot.get("settings", {})
        if isinstance(reloaded_settings, dict):
            self.settings = dict(reloaded_settings)
        self._settings_version = next_version
        self.llm.reload_settings(self.settings)
        self.email.reload_settings(self.settings)

        max_runs = int(self.settings.get("max_run_history_entries", 25) or 25)
        if getattr(self.history_store, "max_runs", max_runs) != max_runs:
            self.history_store.max_runs = max_runs
        max_problem_records = int(self.settings.get("max_problem_records", 120) or 120)
        if getattr(self.problem_store, "max_records", max_problem_records) != max_problem_records:
            self.problem_store.max_records = max_problem_records
        return True

    def get_runtime_config(self) -> Dict[str, object]:
        self.refresh_runtime_settings_if_needed()
        runtime = dict(self.llm.get_runtime_config())
        runtime["settings_hot_reload"] = {
            "enabled": True,
            "scope": "config/settings.yaml + config/settings.local.yaml + config/secrets.yaml",
            "notes": [
                "Runtime settings are refreshed when config file timestamps change.",
                "Model, base URL, and reasoning effort can update without restarting the local API.",
            ],
        }
        runtime["tool_policy"] = self.tools.tool_policy_snapshot()
        runtime["email"] = self.email.status_snapshot()
        runtime["environment_awareness"] = self.get_environment_awareness()
        runtime["capability_profiles"] = {
            SAFE_BOUNDED_PROFILE: profile_metadata(SAFE_BOUNDED_PROFILE, settings=self.settings),
            SANDBOXED_FULL_ACCESS_LAB_PROFILE: profile_metadata(SANDBOXED_FULL_ACCESS_LAB_PROFILE, settings=self.settings),
        }
        return runtime

    def get_environment_awareness(self, *, execution_profile: str = "", lab_armed: bool = False) -> Dict[str, Any]:
        self.refresh_runtime_settings_if_needed()
        settings_with_version = dict(self.settings)
        settings_with_version["_settings_version"] = self._settings_version
        return build_environment_awareness(
            settings=settings_with_version,
            email_status=self.email.status_snapshot(),
            execution_profile=execution_profile,
            lab_armed=lab_armed,
        )

    def get_recent_problems(self, *, limit: int = 12) -> list[Dict[str, Any]]:
        self.refresh_runtime_settings_if_needed()
        return self.problem_store.get_recent(limit=limit)

    def get_problem_summary(self, *, limit: int = 6) -> Dict[str, Any]:
        self.refresh_runtime_settings_if_needed()
        return self.problem_store.get_summary(limit=limit)

    def get_email_status(self) -> Dict[str, Any]:
        self.refresh_runtime_settings_if_needed()
        return self.email.status_snapshot()

    def list_email_threads(self, *, limit: int = 10, query: str = "", label_ids: list[str] | None = None) -> Dict[str, Any]:
        self.refresh_runtime_settings_if_needed()
        return self.email.list_threads(limit=limit, query=query, label_ids=label_ids)

    def read_email_thread(self, thread_id: str, *, max_messages: int = 8) -> Dict[str, Any]:
        self.refresh_runtime_settings_if_needed()
        return self.email.read_thread(thread_id, max_messages=max_messages)

    def list_email_drafts(self, *, status: str = "", limit: int = 24) -> Dict[str, Any]:
        self.refresh_runtime_settings_if_needed()
        return self.email.list_drafts(status=status, limit=limit)

    def prepare_email_reply_draft(self, *, thread_id: str, guidance: str = "", user_context: str = "") -> Dict[str, Any]:
        self.refresh_runtime_settings_if_needed()
        return self.email.prepare_reply_draft(thread_id=thread_id, guidance=guidance, user_context=user_context)

    def prepare_email_forward_draft(self, *, thread_id: str, to: list[str] | None = None, note: str = "") -> Dict[str, Any]:
        self.refresh_runtime_settings_if_needed()
        return self.email.prepare_forward_draft(thread_id=thread_id, to=to, note=note)

    def send_email_draft(self, draft_id: str, *, approved: bool = False) -> Dict[str, Any]:
        self.refresh_runtime_settings_if_needed()
        return self.email.send_draft(draft_id, approved=approved)

    def reject_email_draft(self, draft_id: str, *, reason: str = "Rejected by operator.") -> Dict[str, Any]:
        self.refresh_runtime_settings_if_needed()
        return self.email.reject_draft(draft_id, reason=reason)

    def connect_gmail(self) -> Dict[str, Any]:
        self.refresh_runtime_settings_if_needed()
        return self.email.connect_gmail()

    def _normalize_state_scope_id(self, state_scope_id: str | None = None) -> str:
        text = str(state_scope_id or "").strip()[:120]
        return text or DEFAULT_STATE_SCOPE_ID

    def _refresh_summary(self, state: TaskState):
        recent_notes = state.memory_notes[-6:]
        if recent_notes:
            state.set_summary(" | ".join(recent_notes))

    def _should_preserve_pending_browser_checkpoint(self, goal: str) -> bool:
        text = " ".join(str(goal or "").strip().lower().split())
        if not text:
            return False
        if not self.tools.goal_has_explicit_browser_approval(text):
            return False
        return any(term in text for term in ("resume", "paused", "checkpoint", "continue"))

    def load_task_state(
        self,
        goal: str = "",
        *,
        state_scope_id: str = DEFAULT_STATE_SCOPE_ID,
        clear_pending_for_new_goal: bool = True,
    ) -> TaskState:
        normalized_scope_id = self._normalize_state_scope_id(state_scope_id)
        persisted = self.session_store.load(scope_id=normalized_scope_id)
        persisted_state = persisted.get("task_state", {})
        previous_goal = str(persisted_state.get("goal", "")).strip()
        requested_goal = str(goal).strip()
        state_goal = requested_goal or previous_goal
        state = TaskState(
            state_goal,
            session_state=persisted_state,
            loaded_message=(persisted.get("loaded_message", "") if not requested_goal else ""),
            state_scope_id=normalized_scope_id,
        )
        state.state_scope_id = normalized_scope_id
        setattr(state, "_operator_memory_store", self.operator_memory_store)
        setattr(state, "_problem_store", self.problem_store)
        environment_awareness = self.get_environment_awareness(execution_profile=getattr(state, "execution_profile", ""))
        setattr(
            state,
            "_environment_awareness",
            environment_awareness,
        )
        self.operator_memory_store.remember_environment(environment_awareness)
        refresh_operator_intelligence_context(state)

        if requested_goal:
            state.goal = requested_goal
            preserve_checkpoint = self._should_preserve_pending_browser_checkpoint(requested_goal)
            if clear_pending_for_new_goal and previous_goal and previous_goal != requested_goal and state.browser_checkpoint_pending:
                if preserve_checkpoint:
                    state.add_note("Preserved pending browser checkpoint for explicit approval resume goal.")
                else:
                    state.clear_browser_checkpoint()
                    state.add_note("Cleared pending browser checkpoint for new goal.")
                self._refresh_summary(state)

        return state

    def save_task_state(self, state: TaskState, *, state_scope_id: str | None = None) -> bool:
        normalized_scope_id = self._normalize_state_scope_id(state_scope_id or getattr(state, "state_scope_id", DEFAULT_STATE_SCOPE_ID))
        state.state_scope_id = normalized_scope_id
        return self.session_store.save(state, scope_id=normalized_scope_id)

    def record_run_history(
        self,
        state: TaskState,
        *,
        started_at: float,
        step_start_index: int,
        result: Dict[str, object] | None,
        source: str,
        goal: str | None = None,
        session_id: str = "",
        state_scope_id: str | None = None,
    ) -> Dict[str, object]:
        safe_step_start = max(0, int(step_start_index))
        result_payload = result if isinstance(result, dict) else {}
        normalized_scope_id = self._normalize_state_scope_id(state_scope_id or getattr(state, "state_scope_id", DEFAULT_STATE_SCOPE_ID))
        return self.history_store.record_run(
            run_id=self.history_store.next_run_id(),
            goal=str(goal or state.goal).strip(),
            started_at=started_at,
            ended_at=time.time(),
            final_status=str(result_payload.get("status", state.status)).strip() or state.status,
            final_summary=str(state.last_summary).strip() or str(result_payload.get("message", "")).strip(),
            result_message=str(result_payload.get("message", "")).strip(),
            steps=state.steps[safe_step_start:],
            task_state=state,
            source=source,
            step_offset=safe_step_start,
            session_id=str(session_id).strip()[:80],
            state_scope_id=normalized_scope_id,
        )

    def run_state(
        self,
        state: TaskState,
        *,
        planning_goal: str | None = None,
        history_start_index: int | None = None,
        run_source: str = "goal_run",
        session_id: str = "",
        control_callback=None,
        progress_callback=None,
    ):
        self.refresh_runtime_settings_if_needed()
        normalized_scope_id = self._normalize_state_scope_id(getattr(state, "state_scope_id", DEFAULT_STATE_SCOPE_ID))
        state.state_scope_id = normalized_scope_id
        step_start_index = len(state.steps) if history_start_index is None else max(0, int(history_start_index))
        started_at = time.time()
        setattr(state, "_operator_memory_store", self.operator_memory_store)
        setattr(state, "_problem_store", self.problem_store)
        environment_awareness = self.get_environment_awareness(
            execution_profile=getattr(state, "execution_profile", ""),
            lab_armed=bool(getattr(self, "_lab_armed", False)),
        )
        setattr(
            state,
            "_environment_awareness",
            environment_awareness,
        )
        self.operator_memory_store.remember_environment(environment_awareness)
        self.session_store.save(state, scope_id=normalized_scope_id)
        if callable(progress_callback):
            try:
                progress_callback("run_state_entered", detail="Entered agent run_state.")
            except Exception:
                pass

        try:
            result = run_task_loop(
                self.llm,
                self.tools,
                state,
                self.settings,
                session_store=self.session_store,
                planning_goal=planning_goal,
                control_callback=control_callback,
                progress_callback=progress_callback,
            )
        except Exception as exc:
            state.status = "blocked"
            state.add_step(
                {
                    "type": "system",
                    "status": "failed",
                    "message": f"Agent run failed: {exc}",
                }
            )
            state.add_note(f"Agent run failed: {exc}")
            self._refresh_summary(state)
            self.session_store.save(state, scope_id=normalized_scope_id)
            result = {
                "ok": False,
                "status": "blocked",
                "message": f"Agent run failed: {exc}",
                "steps": state.steps,
            }

        history_entry = self.record_run_history(
            state,
            started_at=started_at,
            step_start_index=step_start_index,
            result=result,
            source=run_source,
            session_id=session_id,
            state_scope_id=normalized_scope_id,
        )
        result["run_id"] = history_entry.get("run_id", "")
        return result

    def run_task(self, goal: str, *, state_scope_id: str = DEFAULT_STATE_SCOPE_ID):
        state = self.load_task_state(goal, state_scope_id=state_scope_id)
        return self.run_state(state, run_source="goal_run")
