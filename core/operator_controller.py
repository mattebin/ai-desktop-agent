from __future__ import annotations

from typing import Any, Dict

from core.agent import Agent
from core.execution_manager import ExecutionManager


class OperatorController:
    def __init__(self, agent: Agent | None = None, *, settings: Dict[str, Any] | None = None):
        effective_agent = agent or Agent(settings=settings)
        self.manager = ExecutionManager(agent=effective_agent)

    def start_goal(self, goal: str, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        return self.manager.start_goal(goal, session_id=session_id, state_scope_id=state_scope_id)

    def enqueue_goal(self, goal: str, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        return self.manager.enqueue_goal(goal, source="queued_goal", start_if_idle=False, session_id=session_id, state_scope_id=state_scope_id)

    def replace_goal(self, goal: str, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        return self.manager.replace_goal(goal, session_id=session_id, state_scope_id=state_scope_id)

    def stop_task(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        return self.manager.stop_task(session_id=session_id, state_scope_id=state_scope_id)

    def defer_task(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        return self.manager.defer_task(session_id=session_id, state_scope_id=state_scope_id)

    def resume_task(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        return self.manager.resume_task(session_id=session_id, state_scope_id=state_scope_id)

    def retry_task(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        return self.manager.retry_task(session_id=session_id, state_scope_id=state_scope_id)

    def schedule_goal(self, goal: str, run_at: str, recurrence: str = "once") -> Dict[str, Any]:
        return self.manager.schedule_goal(goal, run_at, recurrence=recurrence)

    def create_watch(
        self,
        goal: str,
        condition_type: str,
        target: str,
        match_text: str = "",
        interval_seconds: int = 10,
        allow_repeat: bool = False,
    ) -> Dict[str, Any]:
        return self.manager.create_watch(
            goal,
            condition_type,
            target,
            match_text,
            interval_seconds=interval_seconds,
            allow_repeat=allow_repeat,
        )

    def start_next(self) -> Dict[str, Any]:
        return self.manager.start_next(auto_trigger=False)

    def approve_pending(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        return self.manager.approve_pending(session_id=session_id, state_scope_id=state_scope_id)

    def reject_pending(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        return self.manager.reject_pending(session_id=session_id, state_scope_id=state_scope_id)

    def get_snapshot(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        return self.manager.get_snapshot(session_id=session_id, state_scope_id=state_scope_id)

    def get_active_task(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        return self.get_snapshot(session_id=session_id, state_scope_id=state_scope_id).get("active_task", {})

    def get_recent_runs(self, limit: int = 6, *, session_id: str = "", state_scope_id: str = "") -> list[Dict[str, Any]]:
        safe_limit = max(1, min(int(limit or 6), 25))
        return self.manager.agent.history_store.get_recent_runs(limit=safe_limit, session_id=session_id, state_scope_id=state_scope_id)

    def get_alerts(self, limit: int = 12, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        safe_limit = max(1, min(int(limit or 12), 40))
        alerts = dict(self.get_snapshot(session_id=session_id, state_scope_id=state_scope_id).get("alerts", {}))
        alerts["items"] = list(alerts.get("items", []))[:safe_limit]
        return alerts

    def get_runtime_config(self) -> Dict[str, Any]:
        return self.manager.agent.get_runtime_config()

    def get_tool_catalog(self) -> list[Dict[str, Any]]:
        return self.manager.agent.tools.tool_catalog()

    def get_email_status(self) -> Dict[str, Any]:
        return self.manager.agent.get_email_status()

    def list_email_threads(self, *, limit: int = 10, query: str = "", label_ids: list[str] | None = None) -> Dict[str, Any]:
        return self.manager.agent.list_email_threads(limit=limit, query=query, label_ids=label_ids)

    def read_email_thread(self, thread_id: str, *, max_messages: int = 8) -> Dict[str, Any]:
        return self.manager.agent.read_email_thread(thread_id, max_messages=max_messages)

    def list_email_drafts(self, *, status: str = "", limit: int = 24) -> Dict[str, Any]:
        return self.manager.agent.list_email_drafts(status=status, limit=limit)

    def prepare_email_reply_draft(self, *, thread_id: str, guidance: str = "", user_context: str = "") -> Dict[str, Any]:
        return self.manager.agent.prepare_email_reply_draft(thread_id=thread_id, guidance=guidance, user_context=user_context)

    def prepare_email_forward_draft(self, *, thread_id: str, to: list[str] | None = None, note: str = "") -> Dict[str, Any]:
        return self.manager.agent.prepare_email_forward_draft(thread_id=thread_id, to=to, note=note)

    def send_email_draft(self, draft_id: str, *, approved: bool = False) -> Dict[str, Any]:
        return self.manager.agent.send_email_draft(draft_id, approved=approved)

    def reject_email_draft(self, draft_id: str, *, reason: str = "Rejected by operator.") -> Dict[str, Any]:
        return self.manager.agent.reject_email_draft(draft_id, reason=reason)

    def connect_gmail(self) -> Dict[str, Any]:
        return self.manager.agent.connect_gmail()

    def get_queue_state(self) -> Dict[str, Any]:
        return self.get_snapshot().get("queue", {})

    def get_scheduled_state(self) -> Dict[str, Any]:
        return self.get_snapshot().get("scheduled", {})

    def get_watch_state(self) -> Dict[str, Any]:
        return self.get_snapshot().get("watches", {})

    def shutdown(self):
        self.manager.shutdown()
