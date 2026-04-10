from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List
from uuid import uuid4

from core.llm_client import HostedLLMClient
from core.operator_behavior import (
    CHAT_MODE_APPROVAL,
    CHAT_MODE_FINAL,
    CHAT_MODE_NORMAL,
    CHAT_MODE_PAUSED,
    CHAT_MODE_WORKFLOW,
    SESSION_ACTIVE_STATUSES,
    SESSION_TERMINAL_STATUSES,
    classify_chat_turn,
    operator_goal_preamble,
)
from core.operator_controller import OperatorController


CHAT_SESSION_STATE_VERSION = 1
DEFAULT_CHAT_SESSION_STATE_PATH = "data/chat_sessions.json"
DEFAULT_MAX_CHAT_SESSIONS = 16
DEFAULT_MAX_CHAT_MESSAGES = 40
DEFAULT_MAX_CHAT_MESSAGE_CHARS = 12000
DEFAULT_MAX_OPERATOR_LATEST_MESSAGE_CHARS = 1600
AUTHORITATIVE_MESSAGE_KINDS = {"final", "result", "error", "message"}
TRANSIENT_ASSISTANT_KINDS = {"status", "system", "progress"}
CONTROL_STOP_TERMS = {"stop", "cancel", "abort", "never mind", "forget it", "drop it"}
CONTROL_DEFER_TERMS = {"defer", "pause this for later", "hold this for later", "park this", "come back to this later"}
CONTROL_RESUME_TERMS = {"resume", "resume that", "pick that back up", "continue that", "continue the deferred task"}
CONTROL_RETRY_TERMS = {"retry", "try again", "rerun that", "run that again", "retry that"}
CONTROL_REPLACE_PATTERNS = (
    r"^(?:actually\s*,?\s*)?instead[:,]?\s+(?P<goal>.+)$",
    r"^(?:replace|swap)\s+(?:it|that|this)(?:\s+with)?\s+(?P<goal>.+)$",
    r"^(?:stop|cancel|forget)\s+(?:it|that|this)\s+and\s+(?P<goal>.+)$",
    r"^(?P<goal>.+?)\s+instead$",
)
CONTEXT_REFERENCE_TERMS = {"that", "this", "it", "those", "them", "previous", "earlier", "same task", "same page", "that result", "that task"}


def _route_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _contains_any(text: str, phrases: set[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _extract_replace_goal(text: str) -> str:
    normalized = _route_text(text)
    if not normalized:
        return ""
    for pattern in CONTROL_REPLACE_PATTERNS:
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        goal = str(match.groupdict().get("goal", "")).strip()
        if len(goal) >= 8:
            return goal
    return ""


def _looks_like_path_or_url(text: str) -> bool:
    lowered = str(text or "").lower()
    return (
        ":\\" in lowered
        or "file://" in lowered
        or "http://" in lowered
        or "https://" in lowered
        or bool(re.search(r"\b[\w\-.]+\.(py|md|txt|json|yaml|yml|html|css|js|ts|tsx|jsx)\b", lowered))
    )


def _references_existing_context(text: str) -> bool:
    normalized = _route_text(text)
    return any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", normalized) for term in CONTEXT_REFERENCE_TERMS)


def _trim_text(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _trim_body(value: Any, limit: int = DEFAULT_MAX_CHAT_MESSAGE_CHARS) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _iso_timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _normalize_string_list(value: Any, *, limit: int, item_limit: int = 80) -> List[str]:
    if not isinstance(value, list):
        return []

    items: List[str] = []
    for raw_item in value:
        item = _trim_text(raw_item, limit=item_limit)
        if not item or item in items:
            continue
        items.append(item)
        if len(items) >= limit:
            break
    return items


def _normalize_pending(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {"kind": "", "reason": "", "summary": ""}
    return {
        "kind": _trim_text(value.get("kind", ""), limit=80),
        "reason": _trim_text(value.get("reason", ""), limit=180),
        "summary": _trim_text(value.get("summary", ""), limit=180),
    }


def _normalize_message(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    message_id = _trim_text(value.get("message_id", ""), limit=60)
    if not message_id:
        return {}

    return {
        "message_id": message_id,
        "created_at": _trim_text(value.get("created_at", ""), limit=40) or _iso_timestamp(),
        "role": _trim_text(value.get("role", "assistant"), limit=20) or "assistant",
        "kind": _trim_text(value.get("kind", "message"), limit=40) or "message",
        "content": _trim_body(value.get("content", "")),
        "task_id": _trim_text(value.get("task_id", ""), limit=60),
        "run_id": _trim_text(value.get("run_id", ""), limit=60),
        "status": _trim_text(value.get("status", ""), limit=40),
    }


def _normalize_observed_tasks(value: Any, *, limit: int = 12) -> Dict[str, Dict[str, str]]:
    if not isinstance(value, dict):
        return {}

    observed: Dict[str, Dict[str, str]] = {}
    for raw_task_id, raw_task in value.items():
        task_id = _trim_text(raw_task_id, limit=60)
        if not task_id or task_id in observed or not isinstance(raw_task, dict):
            continue
        observed[task_id] = {
            "status": _trim_text(raw_task.get("status", ""), limit=40),
            "last_message": _trim_text(raw_task.get("last_message", ""), limit=280),
            "last_rendered_message": _trim_body(raw_task.get("last_rendered_message", raw_task.get("last_message", ""))),
            "run_id": _trim_text(raw_task.get("run_id", ""), limit=60),
        }
        if len(observed) >= limit:
            break
    return observed


def _normalize_session(value: Any, *, max_messages: int) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    session_id = _trim_text(value.get("session_id", ""), limit=60)
    if not session_id:
        return {}

    messages: List[Dict[str, Any]] = []
    for raw_message in value.get("messages", []):
        message = _normalize_message(raw_message)
        if not message:
            continue
        messages.append(message)
        if len(messages) > max_messages:
            messages = messages[-max_messages:]

    title = _trim_text(value.get("title", ""), limit=120)
    if not title:
        first_user = next((item for item in messages if item.get("role") == "user" and item.get("content")), None)
        title = _trim_text((first_user or {}).get("content", "New session"), limit=120) or "New session"

    return {
        "session_id": session_id,
        "created_at": _trim_text(value.get("created_at", ""), limit=40) or _iso_timestamp(),
        "updated_at": _trim_text(value.get("updated_at", ""), limit=40) or _iso_timestamp(),
        "title": title,
        "status": _trim_text(value.get("status", "idle"), limit=40) or "idle",
        "summary": _trim_text(value.get("summary", ""), limit=280),
        "current_task_id": _trim_text(value.get("current_task_id", ""), limit=60),
        "task_ids": _normalize_string_list(value.get("task_ids", []), limit=12, item_limit=60),
        "latest_run_id": _trim_text(value.get("latest_run_id", ""), limit=60),
        "latest_user_message": _trim_body(value.get("latest_user_message", ""), limit=4000),
        "last_result_message": _trim_body(value.get("last_result_message", "")),
        "last_result_status": _trim_text(value.get("last_result_status", ""), limit=40),
        "pending_approval": _normalize_pending(value.get("pending_approval", {})),
        "messages": messages[-max_messages:],
        "observed_tasks": _normalize_observed_tasks(value.get("observed_tasks", {})),
    }


class ChatSessionStore:
    def __init__(self, path: str | Path, *, max_sessions: int = DEFAULT_MAX_CHAT_SESSIONS, max_messages: int = DEFAULT_MAX_CHAT_MESSAGES):
        self.path = Path(path)
        self.max_sessions = max(1, int(max_sessions))
        self.max_messages = max(8, int(max_messages))

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"sessions": []}

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"sessions": []}

        if not isinstance(payload, dict):
            return {"sessions": []}

        sessions: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        for raw_session in payload.get("sessions", []):
            session = _normalize_session(raw_session, max_messages=self.max_messages)
            session_id = session.get("session_id", "")
            if not session_id or session_id in seen_ids:
                continue
            seen_ids.add(session_id)
            sessions.append(session)

        return {"sessions": self._trim_sessions(sessions)}

    def save(self, sessions: List[Dict[str, Any]]) -> bool:
        payload = {
            "version": CHAT_SESSION_STATE_VERSION,
            "saved_at": _iso_timestamp(),
            "sessions": self._trim_sessions(sessions),
        }

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            return False
        return True

    def _trim_sessions(self, sessions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = [_normalize_session(session, max_messages=self.max_messages) for session in sessions]
        normalized = [session for session in normalized if session]
        if len(normalized) <= self.max_sessions:
            return normalized

        active = [session for session in normalized if session.get("status") in SESSION_ACTIVE_STATUSES]
        terminal = [session for session in normalized if session.get("status") not in SESSION_ACTIVE_STATUSES]
        active_ids = {session.get("session_id", "") for session in active}
        remaining = max(0, self.max_sessions - len(active))
        terminal_tail = terminal[-remaining:] if remaining else []
        allowed_ids = active_ids | {session.get("session_id", "") for session in terminal_tail}
        return [session for session in normalized if session.get("session_id", "") in allowed_ids]


class ChatSessionManager:
    def __init__(
        self,
        controller: OperatorController | None = None,
        *,
        path: str | Path = DEFAULT_CHAT_SESSION_STATE_PATH,
        max_sessions: int = DEFAULT_MAX_CHAT_SESSIONS,
        max_messages: int = DEFAULT_MAX_CHAT_MESSAGES,
        chat_client_factory: Callable[[], Any] | None = None,
    ):
        self.controller = controller or OperatorController()
        self.store = ChatSessionStore(path, max_sessions=max_sessions, max_messages=max_messages)
        self.max_messages = max(8, int(max_messages))
        self._chat_client_factory = chat_client_factory or HostedLLMClient
        self._chat_client = None
        self._lock = threading.RLock()
        self._sessions: List[Dict[str, Any]] = list(self.store.load().get("sessions", []))

    def _persist_locked(self) -> bool:
        return self.store.save(self._sessions)

    def _find_session_locked(self, session_id: str) -> Dict[str, Any] | None:
        lookup = str(session_id).strip()
        if not lookup:
            return None
        for session in self._sessions:
            if session.get("session_id") == lookup:
                return session
        return None

    def _new_session_locked(self, title: str = "") -> Dict[str, Any]:
        return {
            "session_id": f"session-{uuid4().hex[:12]}",
            "created_at": _iso_timestamp(),
            "updated_at": _iso_timestamp(),
            "title": _trim_text(title, limit=120) or "New session",
            "status": "idle",
            "summary": "",
            "current_task_id": "",
            "task_ids": [],
            "latest_run_id": "",
            "latest_user_message": "",
            "last_result_message": "",
            "last_result_status": "",
            "pending_approval": {"kind": "", "reason": "", "summary": ""},
            "messages": [],
            "observed_tasks": {},
        }

    def _append_message_locked(
        self,
        session: Dict[str, Any],
        *,
        role: str,
        kind: str,
        content: str,
        task_id: str = "",
        run_id: str = "",
        status: str = "",
    ) -> Dict[str, Any] | None:
        rendered = _trim_body(content)
        if not rendered:
            return None

        last_message = session.get("messages", [])[-1] if session.get("messages") else {}
        if (
            last_message.get("role") == role
            and last_message.get("kind") == kind
            and last_message.get("content") == rendered
            and last_message.get("task_id") == _trim_text(task_id, limit=60)
            and last_message.get("status") == _trim_text(status, limit=40)
        ):
            return last_message

        message = {
            "message_id": f"msg-{uuid4().hex[:12]}",
            "created_at": _iso_timestamp(),
            "role": role,
            "kind": kind,
            "content": rendered,
            "task_id": _trim_text(task_id, limit=60),
            "run_id": _trim_text(run_id, limit=60),
            "status": _trim_text(status, limit=40),
        }
        session.setdefault("messages", []).append(message)
        session["messages"] = session["messages"][-self.max_messages:]
        session["updated_at"] = _iso_timestamp()
        return message

    def _add_task_id_locked(self, session: Dict[str, Any], task_id: str):
        trimmed = _trim_text(task_id, limit=60)
        if not trimmed:
            return
        task_ids = [task for task in session.get("task_ids", []) if task != trimmed]
        task_ids.append(trimmed)
        session["task_ids"] = task_ids[-12:]
        session["current_task_id"] = trimmed

    def _pending_payload(self, pending: Dict[str, Any]) -> Dict[str, str]:
        return {
            "kind": _trim_text(pending.get("kind", ""), limit=80),
            "reason": _trim_text(pending.get("reason", ""), limit=180),
            "summary": _trim_text(pending.get("summary", ""), limit=180),
        }

    def _current_pending_locked(self, session: Dict[str, Any], snapshot: Dict[str, Any]) -> Dict[str, Any]:
        session_pending = session.get("pending_approval", {})
        if isinstance(session_pending, dict) and _trim_text(session_pending.get("kind", ""), limit=80):
            return session_pending
        snapshot_pending = snapshot.get("pending_approval", {})
        return snapshot_pending if isinstance(snapshot_pending, dict) else {}

    def _get_chat_client_locked(self):
        if self._chat_client is None:
            self._chat_client = self._chat_client_factory()
        return self._chat_client

    def _recent_conversation_items_locked(self, session: Dict[str, Any], *, limit: int = 6, include_transient: bool = False) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for item in session.get("messages", []):
            role = str(item.get("role", "")).strip().lower()
            kind = str(item.get("kind", "")).strip().lower()
            if role not in {"user", "assistant"} or not item.get("content"):
                continue
            if role == "assistant" and not include_transient and kind in TRANSIENT_ASSISTANT_KINDS | {"approval", "approval_needed"}:
                continue
            items.append(item)
        return items[-limit:]

    def _conversation_lines(self, session: Dict[str, Any], latest_message: str, *, include_recent: bool = True) -> List[str]:
        lines: List[str] = []
        selected = self._recent_conversation_items_locked(session, limit=4, include_transient=False) if include_recent else []
        if selected:
            lines.append("Recent conversation:")
            for item in selected:
                role = "User" if item.get("role") == "user" else "Assistant"
                lines.append(f"- {role}: {_trim_text(item.get('content', ''), limit=220)}")
        pending = session.get("pending_approval", {})
        if pending.get("kind"):
            lines.append(
                "Current approval state: "
                f"{pending.get('kind')} | reason: {pending.get('reason') or pending.get('summary') or 'approval required'}"
            )
        if session.get("summary"):
            lines.append(f"Session summary: {_trim_text(session.get('summary', ''), limit=220)}")
        lines.append("Approval rule: do not treat this chat message as approval for a paused checkpoint or review bundle.")
        lines.append("Latest user message:")
        lines.append(_trim_text(latest_message, limit=DEFAULT_MAX_OPERATOR_LATEST_MESSAGE_CHARS))
        return lines

    def _compose_goal_locked(self, session: Dict[str, Any], latest_message: str, route: Dict[str, str] | None = None) -> str:
        mode = str((route or {}).get("mode", "")).strip()
        reason = str((route or {}).get("reason", "")).strip().lower()
        pending = session.get("pending_approval", {})
        include_recent = bool(
            reason != "operator_request"
            or _references_existing_context(latest_message)
            or str(session.get("status", "")).strip().lower() in {"running", "paused", "queued", "deferred"}
            or str(pending.get("kind", "")).strip()
        )
        parts = [
            f"Conversation session: {session.get('session_id', '')}",
            operator_goal_preamble(mode),
            (
                "Treat the latest user message as the primary instruction. "
                "Use older conversation only when it is still relevant and does not conflict."
                if not include_recent
                else "Continue the same local operator conversation using the recent context when it helps."
            ),
            *self._conversation_lines(session, latest_message, include_recent=include_recent),
        ]
        return "\n\n".join([part for part in parts if part]).strip()

    def _latest_meaningful_assistant_message_locked(self, session: Dict[str, Any]) -> Dict[str, Any]:
        for message in reversed(session.get("messages", [])):
            if str(message.get("role", "")).strip().lower() != "assistant":
                continue
            kind = str(message.get("kind", "")).strip().lower()
            if kind in TRANSIENT_ASSISTANT_KINDS:
                continue
            if message.get("content"):
                return message
        for message in reversed(session.get("messages", [])):
            if str(message.get("role", "")).strip().lower() == "assistant" and message.get("content"):
                return message
        return {}

    def _has_context_for_detail_locked(self, session: Dict[str, Any], snapshot: Dict[str, Any]) -> bool:
        authoritative = self._authoritative_reply_payload(session)
        return bool(
            authoritative.get("content")
            or session.get("last_result_message")
            or _trim_text(snapshot.get("result_message", ""), limit=160)
            or session.get("task_ids")
        )

    def _route_message_locked(self, session: Dict[str, Any], snapshot: Dict[str, Any], latest_message: str) -> Dict[str, str]:
        pending = self._current_pending_locked(session, snapshot)
        session_status = str(session.get("status", "") or snapshot.get("status", "idle") or "idle")
        has_context = self._has_context_for_detail_locked(session, snapshot)
        return classify_chat_turn(
            latest_message,
            session_status=session_status,
            has_context=has_context,
            pending_kind=str(pending.get("kind", "")).strip(),
        )

    def _control_intent_locked(self, session: Dict[str, Any], snapshot: Dict[str, Any], latest_message: str) -> Dict[str, str]:
        text = _route_text(latest_message)
        active_task = snapshot.get("active_task", {}) if isinstance(snapshot.get("active_task", {}), dict) else {}
        active_status = str(active_task.get("status", "") or snapshot.get("status", "")).strip().lower()
        has_control_target = bool(active_status in {"running", "paused", "queued", "deferred", "blocked", "failed", "incomplete", "stopped", "superseded"} or session.get("current_task_id"))

        replacement_goal = _extract_replace_goal(latest_message)
        if replacement_goal:
            return {"action": "replace", "goal": replacement_goal}
        if active_status == "deferred" and text in {"continue", "continue it", "keep going"}:
            return {"action": "resume"}
        if has_control_target and (text in CONTROL_STOP_TERMS or any(text.startswith(f"{term} ") for term in CONTROL_STOP_TERMS)):
            return {"action": "stop"}
        if has_control_target and (_contains_any(text, CONTROL_DEFER_TERMS) or text.startswith("defer ")):
            return {"action": "defer"}
        if has_control_target and (_contains_any(text, CONTROL_RESUME_TERMS) or text.startswith("resume ")):
            return {"action": "resume"}
        if has_control_target and (_contains_any(text, CONTROL_RETRY_TERMS) or text.startswith("retry ")):
            return {"action": "retry"}
        return {}

    def _apply_control_intent_locked(
        self,
        session: Dict[str, Any],
        snapshot: Dict[str, Any],
        latest_message: str,
        intent: Dict[str, str],
    ) -> Dict[str, Any]:
        action = str(intent.get("action", "")).strip().lower()
        session_id = session.get("session_id", "")
        if action == "replace":
            result = self.controller.replace_goal(intent.get("goal", ""), session_id=session_id)
        elif action == "stop":
            result = self.controller.stop_task(session_id=session_id)
        elif action == "defer":
            result = self.controller.defer_task(session_id=session_id)
        elif action == "resume":
            result = self.controller.resume_task(session_id=session_id)
        elif action == "retry":
            result = self.controller.retry_task(session_id=session_id)
        else:
            return {"ok": False, "message": "Unknown control action."}

        if result.get("ok") and _trim_text(result.get("task_id", ""), limit=60):
            self._add_task_id_locked(session, _trim_text(result.get("task_id", ""), limit=60))

        post_snapshot = self.controller.get_snapshot(session_id=session_id)
        self._sync_session_locked(session, post_snapshot)
        assistant_status = _trim_text(result.get("status", post_snapshot.get("result_status", "")), limit=40)
        terminal_authoritative_reply = {}
        if result.get("ok") and assistant_status in SESSION_TERMINAL_STATUSES:
            terminal_authoritative_reply = self._authoritative_reply_payload(session)
            if (
                terminal_authoritative_reply.get("content")
                and (
                    not _trim_text(result.get("task_id", ""), limit=60)
                    or terminal_authoritative_reply.get("task_id", "") == _trim_text(result.get("task_id", ""), limit=60)
                )
                and (
                    not assistant_status
                    or _trim_text(terminal_authoritative_reply.get("status", ""), limit=40) == assistant_status
                )
            ):
                self._update_summary_locked(session)
                self._persist_locked()
                return {
                    "ok": bool(result.get("ok", False)),
                    "session": self._session_detail_payload(session, post_snapshot),
                    "reply": terminal_authoritative_reply,
                    "reply_mode": str(post_snapshot.get("behavior", {}).get("mode", "")).strip() or CHAT_MODE_NORMAL,
                    "result": result,
                }
        if not result.get("ok"):
            assistant_kind = "error"
        elif assistant_status in {"queued", "running", "paused", "needs_attention"} or result.get("requested"):
            assistant_kind = "status"
        else:
            assistant_kind = "result"
        reply_text = str(result.get("message", "")).strip() or "Applied the requested control action."
        reply = self._append_message_locked(
            session,
            role="assistant",
            kind=assistant_kind,
            content=reply_text,
            status=assistant_status,
            task_id=_trim_text(result.get("task_id", ""), limit=60) or session.get("current_task_id", ""),
        )
        self._update_summary_locked(session)
        self._persist_locked()
        return {
            "ok": bool(result.get("ok", False)),
            "session": self._session_detail_payload(session, post_snapshot),
            "reply": reply or {"role": "assistant", "kind": assistant_kind, "content": reply_text},
            "reply_mode": str(post_snapshot.get("behavior", {}).get("mode", "")).strip() or CHAT_MODE_NORMAL,
            "result": result,
        }

    def _should_minimize_chat_context_locked(self, latest_message: str, route: Dict[str, str]) -> bool:
        mode = str((route or {}).get("mode", "")).strip()
        reason = str((route or {}).get("reason", "")).strip().lower()
        if mode != CHAT_MODE_NORMAL:
            return False
        if reason not in {"normal_conversation", "simple_conversation"}:
            return False
        if _references_existing_context(latest_message):
            return False
        return True

    def _chat_context_lines_locked(self, session: Dict[str, Any], snapshot: Dict[str, Any], latest_message: str, route: Dict[str, str]) -> str:
        lines: List[str] = [
            f"Session id: {session.get('session_id', '')}",
            f"Reply mode: {route.get('mode', 'chat')}",
        ]

        if self._should_minimize_chat_context_locked(latest_message, route):
            lines.append(
                "Background operator activity may exist, but the latest user message should be answered directly unless it explicitly asks about earlier work."
            )
            lines.append("Latest user message:")
            lines.append(_trim_text(latest_message, limit=900))
            return "\n".join([line for line in lines if line]).strip()

        if session.get("summary"):
            lines.append(f"Session summary: {_trim_text(session.get('summary', ''), limit=240)}")

        behavior = snapshot.get("behavior", {}) if isinstance(snapshot.get("behavior", {}), dict) else {}
        if behavior.get("mode_label") or behavior.get("task_phase_label"):
            lines.append(
                "Operator state: "
                f"{_trim_text(behavior.get('mode_label', '-'), limit=80)} | "
                f"{_trim_text(behavior.get('task_phase_label', '-'), limit=80)}"
            )

        authoritative = self._authoritative_reply_payload(session)
        if authoritative.get("content"):
            lines.append("Latest authoritative reply:")
            lines.append(_trim_body(authoritative.get("content", ""), limit=2600))

        pending = self._current_pending_locked(session, snapshot)
        if pending.get("kind"):
            lines.append(
                "Pending approval: "
                f"{pending.get('kind')} | {pending.get('reason') or pending.get('summary') or 'approval required'}"
            )

        human_control = snapshot.get("human_control", {}) if isinstance(snapshot.get("human_control", {}), dict) else {}
        if human_control.get("next_action"):
            lines.append(f"Human control: {_trim_text(human_control.get('next_action', ''), limit=180)}")

        current_step = _trim_text(snapshot.get("current_step", ""), limit=180)
        if current_step:
            lines.append(f"Current operator step: {current_step}")

        browser = snapshot.get("browser", {}) if isinstance(snapshot.get("browser", {}), dict) else {}
        if browser.get("workflow_name") or browser.get("current_title") or browser.get("current_url"):
            lines.append(
                "Browser context: "
                f"{_trim_text(browser.get('workflow_name', ''), limit=80) or '-'} | "
                f"{_trim_text(browser.get('current_title') or browser.get('current_url') or '-', limit=140)}"
            )

        desktop = snapshot.get("desktop", {}) if isinstance(snapshot.get("desktop", {}), dict) else {}
        selected_scene = desktop.get("selected_scene", {}) if isinstance(desktop.get("selected_scene", {}), dict) else {}
        checkpoint_scene = desktop.get("checkpoint_scene", {}) if isinstance(desktop.get("checkpoint_scene", {}), dict) else {}
        selected_targets = desktop.get("selected_target_proposals", {}) if isinstance(desktop.get("selected_target_proposals", {}), dict) else {}
        checkpoint_targets = desktop.get("checkpoint_target_proposals", {}) if isinstance(desktop.get("checkpoint_target_proposals", {}), dict) else {}
        selected_scene_summary = _trim_text(selected_scene.get("summary", ""), limit=220)
        checkpoint_scene_summary = _trim_text(checkpoint_scene.get("summary", ""), limit=220)
        selected_targets_summary = _trim_text(selected_targets.get("summary", ""), limit=220)
        checkpoint_targets_summary = _trim_text(checkpoint_targets.get("summary", ""), limit=220)
        if selected_scene_summary:
            lines.append(f"Selected desktop scene: {selected_scene_summary}")
        if checkpoint_scene_summary:
            lines.append(f"Checkpoint desktop scene: {checkpoint_scene_summary}")
        if selected_targets_summary:
            lines.append(f"Selected desktop target proposals: {selected_targets_summary}")
        if checkpoint_targets_summary:
            lines.append(f"Checkpoint desktop target proposals: {checkpoint_targets_summary}")

        recent_items = self._recent_conversation_items_locked(session, limit=4, include_transient=False)
        if recent_items:
            lines.append("Recent conversation:")
            for item in recent_items:
                role = "User" if item.get("role") == "user" else "Assistant"
                lines.append(f"- {role}: {_trim_text(item.get('content', ''), limit=220)}")

        lines.append("Latest user message:")
        lines.append(_trim_text(latest_message, limit=900))
        return "\n".join([line for line in lines if line]).strip()

    def _fallback_chat_reply_locked(self, session: Dict[str, Any], snapshot: Dict[str, Any], route: Dict[str, str]) -> str:
        pending = self._current_pending_locked(session, snapshot)
        if route.get("mode") == CHAT_MODE_APPROVAL and pending.get("kind"):
            reason = pending.get("reason") or pending.get("summary") or "Approval is required before I can continue."
            return f"I'm paused and waiting for explicit approval before continuing. {reason}"

        status = _trim_text(session.get("status") or snapshot.get("status") or "idle", limit=40).lower()
        current_step = _trim_text(snapshot.get("current_step", ""), limit=180)
        if route.get("mode") == CHAT_MODE_WORKFLOW and status in SESSION_ACTIVE_STATUSES:
            if current_step:
                return f"I'm already working on it. Current step: {current_step}"
            return f"I'm already working on it and the task is currently {status}."
        if route.get("mode") == CHAT_MODE_PAUSED:
            if current_step:
                return f"I'm currently paused at {current_step}. Tell me how you'd like to proceed."
            return "I'm currently paused and waiting for direction before continuing."
        if route.get("mode") == CHAT_MODE_FINAL:
            authoritative = self._authoritative_reply_payload(session).get("content", "")
            if authoritative:
                return authoritative

        authoritative = self._authoritative_reply_payload(session).get("content", "")
        if authoritative:
            return authoritative
        if session.get("last_result_message"):
            return _trim_body(session.get("last_result_message", ""), limit=2200)
        if snapshot.get("result_message"):
            return _trim_body(snapshot.get("result_message", ""), limit=2200)
        if route.get("mode") == CHAT_MODE_NORMAL:
            return "I can answer naturally here, or take on operator work when you give me a concrete task."
        return "I can keep the conversation going naturally, or continue the operator task when you ask me to."

    def _direct_reply_locked(self, session: Dict[str, Any], snapshot: Dict[str, Any], latest_message: str, route: Dict[str, str]) -> str:
        session_context = self._chat_context_lines_locked(session, snapshot, latest_message, route)
        desktop_vision: Dict[str, Any] = {}
        try:
            desktop = snapshot.get("desktop", {}) if isinstance(snapshot.get("desktop", {}), dict) else {}
            from core.desktop_evidence import get_desktop_evidence_store

            store = get_desktop_evidence_store()
            desktop_vision = store.select_vision_context(
                selected_summary=desktop.get("selected_evidence", {}),
                checkpoint_summary=desktop.get("checkpoint_evidence", {}),
                recent_summaries=desktop.get("recent_context_evidence", []),
                purpose="desktop_approval" if str(route.get("mode", "")).strip() == CHAT_MODE_APPROVAL else "desktop_investigation",
                prompt_text=latest_message,
                assessment=desktop.get("selected_evidence_assessment", {}),
                checkpoint_assessment=desktop.get("checkpoint_evidence_assessment", {}),
                selected_scene=desktop.get("selected_scene", {}),
                checkpoint_scene=desktop.get("checkpoint_scene", {}),
                prefer_before_after=True,
            )
        except Exception:
            desktop_vision = {}
        try:
            reply = self._get_chat_client_locked().reply_in_chat(
                latest_message,
                session_context=session_context,
                mode=route.get("mode", "chat"),
                desktop_vision=desktop_vision,
            )
        except Exception:
            reply = self._fallback_chat_reply_locked(session, snapshot, route)
        reply = _trim_body(reply, limit=DEFAULT_MAX_CHAT_MESSAGE_CHARS)
        if reply:
            return reply
        return self._fallback_chat_reply_locked(session, snapshot, route)

    def _task_priority(self, task_id: str, status: str, positions: Dict[str, int]) -> tuple[int, int]:
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
        return (status_order.get(status, 50), -positions.get(task_id, -1))

    def _related_tasks(self, snapshot: Dict[str, Any], task_ids: List[str]) -> List[Dict[str, Any]]:
        if not task_ids:
            return []

        lookup: Dict[str, Dict[str, Any]] = {}
        candidates: List[Dict[str, Any]] = []
        active_task = snapshot.get("active_task", {})
        if isinstance(active_task, dict):
            candidates.append(active_task)
        queue = snapshot.get("queue", {})
        candidates.extend(queue.get("queued_tasks", []))
        candidates.extend(queue.get("recent_tasks", []))

        for task in candidates:
            if not isinstance(task, dict):
                continue
            task_id = _trim_text(task.get("task_id", ""), limit=60)
            if not task_id or task_id not in task_ids or task_id in lookup:
                continue
            lookup[task_id] = {
                "task_id": task_id,
                "goal": _trim_text(task.get("goal", ""), limit=220),
                "status": _trim_text(task.get("status", ""), limit=40),
                "last_message": _trim_text(task.get("last_message", ""), limit=280),
                "approval_needed": bool(task.get("approval_needed", False)),
                "approval_reason": _trim_text(task.get("approval_reason", ""), limit=180),
                "run_id": _trim_text(task.get("run_id", ""), limit=60),
            }
        return list(lookup.values())

    def _select_primary_task(self, session: Dict[str, Any], related_tasks: List[Dict[str, Any]]) -> Dict[str, Any] | None:
        if not related_tasks:
            return None
        positions = {task_id: index for index, task_id in enumerate(session.get("task_ids", []))}
        ordered = sorted(related_tasks, key=lambda item: self._task_priority(item.get("task_id", ""), item.get("status", ""), positions))
        return ordered[0] if ordered else None

    def _update_summary_locked(self, session: Dict[str, Any], *, touch_updated: bool = True):
        latest_user = next((item for item in reversed(session.get("messages", [])) if item.get("role") == "user" and item.get("content")), {})
        latest_assistant = self._latest_meaningful_assistant_message_locked(session)
        parts: List[str] = []
        if latest_user.get("content"):
            parts.append(f"User: {_trim_text(latest_user.get('content', ''), limit=120)}")
        if latest_assistant.get("content"):
            parts.append(f"Latest: {_trim_text(latest_assistant.get('content', ''), limit=120)}")
        pending = session.get("pending_approval", {})
        if pending.get("kind"):
            parts.append(f"Approval: {pending.get('kind')}")
        session["summary"] = " | ".join(parts[:3])
        if touch_updated:
            session["updated_at"] = _iso_timestamp()
        if session.get("messages") and (not session.get("title") or session.get("title") == "New session"):
            first_user = next((item for item in session.get("messages", []) if item.get("role") == "user"), {})
            session["title"] = _trim_text(first_user.get("content", "New session"), limit=120) or "New session"

    def _latest_authoritative_message_locked(self, session: Dict[str, Any]) -> Dict[str, Any]:
        for message in reversed(session.get("messages", [])):
            if str(message.get("role", "")).strip().lower() != "assistant":
                continue
            kind = str(message.get("kind", "")).strip().lower()
            status = str(message.get("status", "")).strip().lower()
            if kind in AUTHORITATIVE_MESSAGE_KINDS or status in SESSION_TERMINAL_STATUSES:
                return message
        return {}

    def _authoritative_reply_payload(self, session: Dict[str, Any]) -> Dict[str, Any]:
        message = self._latest_authoritative_message_locked(session)
        if not message:
            return {}
        return {
            "message_id": message.get("message_id", ""),
            "created_at": message.get("created_at", ""),
            "kind": message.get("kind", ""),
            "status": message.get("status", ""),
            "task_id": message.get("task_id", ""),
            "run_id": message.get("run_id", ""),
            "content": message.get("content", ""),
            "preview": _trim_text(message.get("content", ""), limit=320),
        }

    def _resolve_task_result_message_locked(self, snapshot: Dict[str, Any], primary_task: Dict[str, Any], task_status: str, task_preview: str) -> tuple[str, str]:
        task_run_id = _trim_text(primary_task.get("run_id", ""), limit=60)
        latest_run = snapshot.get("latest_run", {}) if isinstance(snapshot.get("latest_run", {}), dict) else {}
        latest_run_id = _trim_text(latest_run.get("run_id", ""), limit=60)
        latest_run_status = _trim_text(latest_run.get("final_status", ""), limit=40)
        latest_run_message = _trim_body(latest_run.get("result_message", ""))

        if latest_run_message and latest_run_id and latest_run_id == task_run_id:
            return latest_run_message, latest_run_id
        if latest_run_message and latest_run_status == task_status:
            return latest_run_message, latest_run_id or task_run_id

        snapshot_result_message = _trim_body(snapshot.get("result_message", ""), limit=4000)
        snapshot_result_status = _trim_text(snapshot.get("result_status", ""), limit=40)
        if snapshot_result_message and snapshot_result_status == task_status:
            return snapshot_result_message, task_run_id

        preview = _trim_body(task_preview, limit=4000)
        if preview:
            return preview, task_run_id
        return f"Task finished with status {task_status}.", task_run_id

    def _session_summary_payload(self, session: Dict[str, Any], snapshot: Dict[str, Any] | None = None) -> Dict[str, Any]:
        latest_message = session.get("messages", [])[-1] if session.get("messages") else {}
        authoritative_reply = self._authoritative_reply_payload(session)
        operator_snapshot = snapshot if isinstance(snapshot, dict) else {}
        return {
            "session_id": session.get("session_id", ""),
            "created_at": session.get("created_at", ""),
            "updated_at": session.get("updated_at", ""),
            "title": session.get("title", ""),
            "status": session.get("status", "idle"),
            "summary": session.get("summary", ""),
            "current_task_id": session.get("current_task_id", ""),
            "latest_run_id": session.get("latest_run_id", ""),
            "pending_approval": session.get("pending_approval", {}),
            "message_count": len(session.get("messages", [])),
            "latest_message": {
                "role": latest_message.get("role", ""),
                "kind": latest_message.get("kind", ""),
                "content": _trim_text(latest_message.get("content", ""), limit=220),
                "status": latest_message.get("status", ""),
            },
            "last_result_status": session.get("last_result_status", ""),
            "last_result_message_preview": _trim_text(session.get("last_result_message", ""), limit=320),
            "authoritative_reply_available": bool(authoritative_reply),
            "behavior": operator_snapshot.get("behavior", {}),
            "human_control": operator_snapshot.get("human_control", {}),
        }

    def _session_detail_payload(self, session: Dict[str, Any], snapshot: Dict[str, Any]) -> Dict[str, Any]:
        return {
            **self._session_summary_payload(session, snapshot),
            "latest_user_message": session.get("latest_user_message", ""),
            "last_result_message": session.get("last_result_message", ""),
            "authoritative_reply": self._authoritative_reply_payload(session),
            "messages": session.get("messages", [])[-12:],
            "operator": {
                "status": _trim_text(snapshot.get("status", ""), limit=40),
                "running": bool(snapshot.get("running", False)),
                "paused": bool(snapshot.get("paused", False)),
                "run_phase": _trim_text(snapshot.get("run_phase", "idle"), limit=40),
                "run_focus": snapshot.get("run_focus", {}),
                "active_task": snapshot.get("active_task", {}),
                "pending_approval": snapshot.get("pending_approval", {}),
                "result_status": _trim_text(snapshot.get("result_status", ""), limit=40),
                "result_message_preview": _trim_text(snapshot.get("result_message", ""), limit=280),
                "browser": snapshot.get("browser", {}),
                "desktop": snapshot.get("desktop", {}),
                "behavior": snapshot.get("behavior", {}),
                "human_control": snapshot.get("human_control", {}),
                "action_policy": snapshot.get("action_policy", {}),
                "task_control": snapshot.get("task_control", {}),
            },
        }

    def _sync_session_locked(self, session: Dict[str, Any], snapshot: Dict[str, Any], *, touch_updated: bool = True):
        related_tasks = self._related_tasks(snapshot, session.get("task_ids", []))
        primary_task = self._select_primary_task(session, related_tasks)
        pending = {"kind": "", "reason": "", "summary": ""}

        if primary_task is not None:
            task_id = primary_task.get("task_id", "")
            task_status = primary_task.get("status", "") or session.get("status", "idle")
            task_preview = str(primary_task.get("last_message", "")).strip()
            task_run_id = _trim_text(primary_task.get("run_id", ""), limit=60)
            session["current_task_id"] = task_id
            session["status"] = task_status
            observed_tasks = session.setdefault("observed_tasks", {})
            observed = observed_tasks.get(task_id, {}) if isinstance(observed_tasks.get(task_id, {}), dict) else {}

            active_task = snapshot.get("active_task", {})
            if isinstance(active_task, dict) and active_task.get("task_id") == task_id and task_status == "paused":
                pending = self._pending_payload(snapshot.get("pending_approval", {}))
            session["pending_approval"] = pending

            rendered_message = task_preview
            rendered_run_id = task_run_id
            message_kind = "status"

            if task_status in SESSION_TERMINAL_STATUSES:
                rendered_message, rendered_run_id = self._resolve_task_result_message_locked(snapshot, primary_task, task_status, task_preview)
                rendered_message = _trim_body(rendered_message)
                session["last_result_message"] = rendered_message or session.get("last_result_message", "")
                session["last_result_status"] = task_status
                message_kind = "final"
            elif task_status == "paused":
                rendered_message, rendered_run_id = self._resolve_task_result_message_locked(snapshot, primary_task, task_status, task_preview)
                rendered_message = _trim_body(
                    rendered_message or pending.get("reason") or pending.get("summary") or task_preview or "Paused and waiting for approval."
                )
                message_kind = "result"
            elif task_status in {"running", "queued"}:
                rendered_message = task_preview or f"Task status changed to {task_status}."
            else:
                rendered_message = task_preview or session.get("last_result_message", "")

            latest_run = snapshot.get("latest_run", {}) if isinstance(snapshot.get("latest_run", {}), dict) else {}
            latest_run_id = _trim_text(latest_run.get("run_id", ""), limit=60)
            latest_run_status = _trim_text(latest_run.get("final_status", ""), limit=40)
            if task_status in SESSION_TERMINAL_STATUSES and latest_run_id and latest_run_status == task_status:
                session["latest_run_id"] = latest_run_id
            elif rendered_run_id:
                session["latest_run_id"] = rendered_run_id

            should_emit = False
            if task_status in SESSION_TERMINAL_STATUSES:
                should_emit = (
                    observed.get("status") != task_status
                    or observed.get("run_id") != rendered_run_id
                    or observed.get("last_rendered_message") != rendered_message
                )
            elif task_status == "paused":
                should_emit = observed.get("status") != task_status or observed.get("last_rendered_message") != rendered_message
            elif task_status in {"running", "queued"}:
                should_emit = not observed or (observed.get("status") == "paused" and task_status == "running")

            if should_emit and rendered_message:
                self._append_message_locked(
                    session,
                    role="assistant",
                    kind=message_kind,
                    content=rendered_message,
                    task_id=task_id,
                    run_id=rendered_run_id or task_run_id,
                    status=task_status,
                )

            observed_tasks[task_id] = {
                "status": task_status,
                "last_message": _trim_text(task_preview, limit=280),
                "last_rendered_message": _trim_body(rendered_message),
                "run_id": rendered_run_id or task_run_id,
            }
        else:
            session["pending_approval"] = pending

        self._update_summary_locked(session, touch_updated=touch_updated)

    def create_session(self, title: str = "", message: str = "") -> Dict[str, Any]:
        session_id = ""
        with self._lock:
            session = self._new_session_locked(title=title)
            self._sessions.append(session)
            self._persist_locked()
            session_id = session.get("session_id", "")
            if not str(message).strip():
                snapshot = self.controller.get_snapshot(session_id=session_id)
                self._sync_session_locked(session, snapshot)
                self._persist_locked()
                return {
                    "ok": True,
                    "session": self._session_detail_payload(session, snapshot),
                }
        result = self.send_message(session_id, str(message).strip())
        result["created"] = True
        return result

    def list_sessions(self, limit: int = 10) -> Dict[str, Any]:
        safe_limit = max(1, min(int(limit or 10), 50))
        with self._lock:
            ordered = sorted(self._sessions, key=lambda item: item.get("updated_at", ""), reverse=True)
            visible_sessions = ordered[:safe_limit]
            items = [self._session_summary_payload(session, {}) for session in visible_sessions]
        return {"items": items, "sessions": items}

    def get_session(self, session_id: str) -> Dict[str, Any]:
        with self._lock:
            session = self._find_session_locked(session_id)
            if session is None:
                return {"ok": False, "message": f"Unknown session: {session_id}"}
            snapshot = self.controller.get_snapshot(session_id=session_id)
            self._sync_session_locked(session, snapshot)
            self._persist_locked()
            return {"ok": True, "session": self._session_detail_payload(session, snapshot)}

    def get_session_messages(self, session_id: str, limit: int = 20) -> Dict[str, Any]:
        safe_limit = max(1, min(int(limit or 20), 60))
        with self._lock:
            session = self._find_session_locked(session_id)
            if session is None:
                return {"ok": False, "message": f"Unknown session: {session_id}"}
            snapshot = self.controller.get_snapshot(session_id=session_id)
            self._sync_session_locked(session, snapshot)
            self._persist_locked()
            messages = session.get("messages", [])[-safe_limit:]
            return {
                "ok": True,
                "session": self._session_summary_payload(session, snapshot),
                "items": messages,
                "messages": messages,
            }

    def peek_stream_view(self, session_id: str, limit: int = 24) -> Dict[str, Any]:
        safe_limit = max(1, min(int(limit or 24), 80))
        with self._lock:
            session = self._find_session_locked(session_id)
            if session is None:
                return {"ok": False, "message": f"Unknown session: {session_id}"}
            messages = session.get("messages", [])[-safe_limit:]
            return {
                "ok": True,
                "session": self._session_summary_payload(session),
                "messages": messages,
            }

    def get_stream_view(self, session_id: str, limit: int = 24) -> Dict[str, Any]:
        safe_limit = max(1, min(int(limit or 24), 80))
        with self._lock:
            session = self._find_session_locked(session_id)
            if session is None:
                return {"ok": False, "message": f"Unknown session: {session_id}"}
            snapshot = self.controller.get_snapshot(session_id=session_id)
            before = json.dumps(session, sort_keys=True, ensure_ascii=False)
            self._sync_session_locked(session, snapshot, touch_updated=False)
            after = json.dumps(session, sort_keys=True, ensure_ascii=False)
            if before != after:
                self._persist_locked()
            messages = session.get("messages", [])[-safe_limit:]
            return {
                "ok": True,
                "session": self._session_summary_payload(session, snapshot),
                "messages": messages,
                "snapshot": {
                    "status": _trim_text(snapshot.get("status", ""), limit=40),
                    "running": bool(snapshot.get("running", False)),
                    "paused": bool(snapshot.get("paused", False)),
                    "run_phase": _trim_text(snapshot.get("run_phase", "idle"), limit=40),
                    "run_focus": snapshot.get("run_focus", {}),
                    "current_step": _trim_text(snapshot.get("current_step", ""), limit=160),
                    "result_status": _trim_text(snapshot.get("result_status", ""), limit=40),
                    "result_message": _trim_text(snapshot.get("result_message", ""), limit=280),
                    "active_task": snapshot.get("active_task", {}),
                    "pending_approval": snapshot.get("pending_approval", {}),
                    "browser": snapshot.get("browser", {}),
                    "desktop": snapshot.get("desktop", {}),
                    "runtime": snapshot.get("runtime", {}),
                    "infrastructure": snapshot.get("infrastructure", {}),
                    "behavior": snapshot.get("behavior", {}),
                    "human_control": snapshot.get("human_control", {}),
                    "task_control": snapshot.get("task_control", {}),
                },
            }

    def send_message(self, session_id: str, message: str) -> Dict[str, Any]:
        rendered = _trim_body(message, limit=4000)
        if not rendered:
            return {"ok": False, "message": "Field 'message' is required."}

        with self._lock:
            session = self._find_session_locked(session_id)
            if session is None:
                return {"ok": False, "message": f"Unknown session: {session_id}"}

            before_snapshot = self.controller.get_snapshot(session_id=session_id)
            self._sync_session_locked(session, before_snapshot)
            self._append_message_locked(session, role="user", kind="message", content=rendered)
            session["latest_user_message"] = rendered
            control_intent = self._control_intent_locked(session, before_snapshot, rendered)
            if control_intent:
                return self._apply_control_intent_locked(session, before_snapshot, rendered, control_intent)
            route = self._route_message_locked(session, before_snapshot, rendered)

            if route.get("dispatch") != "operator":
                reply_kind = "result" if route.get("mode") == CHAT_MODE_FINAL else "message"
                reply_text = self._direct_reply_locked(session, before_snapshot, rendered, route)
                reply = self._append_message_locked(
                    session,
                    role="assistant",
                    kind=reply_kind,
                    content=reply_text,
                    status=_trim_text(session.get("status", ""), limit=40),
                )
                self._update_summary_locked(session)
                self._persist_locked()
                return {
                    "ok": True,
                    "session": self._session_detail_payload(session, before_snapshot),
                    "reply": reply or {"role": "assistant", "kind": "message", "content": reply_text},
                    "reply_mode": route.get("mode", "chat"),
                }

            composed_goal = self._compose_goal_locked(session, rendered, route)
            dispatch = self.controller.start_goal(composed_goal, session_id=session_id, raw_user_message=rendered)

            if not dispatch.get("ok"):
                self._append_message_locked(
                    session,
                    role="assistant",
                    kind="error",
                    content=dispatch.get("message", "Unable to accept the message."),
                    status="error",
                )
                self._update_summary_locked(session)
                self._persist_locked()
                return {
                    "ok": False,
                    "message": dispatch.get("message", "Unable to accept the message."),
                    "session": self._session_summary_payload(session),
                }

            task_id = _trim_text(dispatch.get("task_id", ""), limit=60)
            if task_id:
                self._add_task_id_locked(session, task_id)

            post_snapshot = self.controller.get_snapshot(session_id=session_id)
            related_tasks = self._related_tasks(post_snapshot, session.get("task_ids", []))
            started = bool(dispatch.get("started", False))
            task_status = "running" if started else "queued"
            task_message = dispatch.get("message", "")
            primary_task = self._select_primary_task(session, related_tasks)
            if primary_task is not None and primary_task.get("task_id") == task_id:
                task_status = primary_task.get("status", task_status) or task_status
                task_message = primary_task.get("last_message", task_message) or task_message
            if task_id:
                session.setdefault("observed_tasks", {})[task_id] = {
                    "status": task_status,
                    "last_message": _trim_text(task_message, limit=280),
                }

            if route.get("mode") == "read_only_investigation":
                reply_text = "Started investigating that." if started else "Queued that investigation behind the current operator work."
            else:
                reply_text = "Started working on that." if started else "Queued that message behind the current operator work."
            if post_snapshot.get("pending_approval", {}).get("kind") and not started:
                reply_text = "Queued that message while the operator is paused for approval."
            reply = self._append_message_locked(
                session,
                role="assistant",
                kind="status",
                content=reply_text,
                task_id=task_id,
                status=task_status,
            )
            self._sync_session_locked(session, post_snapshot)
            self._persist_locked()
            return {
                "ok": True,
                "session": self._session_detail_payload(session, post_snapshot),
                "dispatch": dispatch,
                "reply": reply or {"role": "assistant", "kind": "status", "content": reply_text, "status": task_status},
                "reply_mode": route.get("mode", "operator"),
            }

    def record_approval_action(
        self,
        approved: bool,
        result: Dict[str, Any],
        *,
        session_id: str = "",
        before_snapshot: Dict[str, Any] | None = None,
        after_snapshot: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        with self._lock:
            target_session = self._find_session_locked(session_id) if session_id else None
            if target_session is None and isinstance(before_snapshot, dict):
                active_task_id = _trim_text(before_snapshot.get("active_task", {}).get("task_id", ""), limit=60)
                if active_task_id:
                    for session in self._sessions:
                        if active_task_id in session.get("task_ids", []):
                            target_session = session
                            break

            if target_session is None:
                return {"ok": result.get("ok", False), "message": result.get("message", ""), "session": {}}

            action_text = "Approved the pending action." if approved else "Rejected the pending action."
            self._append_message_locked(target_session, role="user", kind="approval", content=action_text)
            self._append_message_locked(
                target_session,
                role="assistant",
                kind="status" if result.get("ok") else "error",
                content=result.get("message", "Approval action processed."),
                status=_trim_text(result.get("status", ""), limit=40),
            )
            snapshot = (
                after_snapshot
                if isinstance(after_snapshot, dict) and after_snapshot
                else self.controller.get_snapshot(session_id=target_session.get("session_id", ""))
            )
            self._sync_session_locked(target_session, snapshot)
            self._persist_locked()
            return {
                "ok": bool(result.get("ok", False)),
                "message": result.get("message", ""),
                "session": self._session_detail_payload(target_session, snapshot),
            }






