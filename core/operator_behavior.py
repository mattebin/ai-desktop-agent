from __future__ import annotations

import re
from typing import Any, Dict


CHAT_MODE_NORMAL = "normal_chat"
CHAT_MODE_READ_ONLY = "read_only_investigation"
CHAT_MODE_WORKFLOW = "workflow_execution"
CHAT_MODE_APPROVAL = "approval_needed_action"
CHAT_MODE_PAUSED = "paused_waiting"
CHAT_MODE_FINAL = "final_report"

SESSION_ACTIVE_STATUSES = {"queued", "running", "paused"}
SESSION_TERMINAL_STATUSES = {"completed", "failed", "blocked", "needs_attention", "stopped", "incomplete", "superseded", "deferred"}

MODE_LABELS = {
    CHAT_MODE_NORMAL: "Normal chat",
    CHAT_MODE_READ_ONLY: "Read-only investigation",
    CHAT_MODE_WORKFLOW: "Workflow execution",
    CHAT_MODE_APPROVAL: "Approval-needed action",
    CHAT_MODE_PAUSED: "Paused / waiting",
    CHAT_MODE_FINAL: "Final report",
}

PHASE_LABELS = {
    "idle": "Idle",
    "queued": "Queued",
    "investigating": "Investigating",
    "executing": "Executing",
    "approval_gate": "Waiting for approval",
    "paused": "Paused",
    "deferred": "Deferred",
    "completed": "Completed",
    "blocked": "Blocked",
    "failed": "Failed",
    "superseded": "Superseded",
    "needs_attention": "Needs attention",
    "stopped": "Stopped",
    "incomplete": "Incomplete",
}

ROUTER_CONTINUE_TERMS = {
    "continue",
    "keep going",
    "resume",
    "go ahead",
    "proceed",
    "carry on",
    "move forward",
}
ROUTER_STATUS_TERMS = {
    "what happened",
    "what did you find",
    "what changed",
    "what are you waiting for",
    "what are you doing",
    "why did",
    "why are",
    "status",
    "update",
    "progress",
    "waiting for",
    "paused",
    "blocked",
    "stuck",
    "still working",
}
ROUTER_DETAIL_TERMS = {
    "explain",
    "summarize",
    "recap",
    "clarify",
    "walk me through",
    "tell me more",
    "why",
    "how",
}
ROUTER_CONTEXT_TERMS = {
    "that",
    "this",
    "it",
    "those",
    "them",
    "previous",
    "last one",
    "last run",
    "that result",
    "that task",
}
CASUAL_CHAT_TERMS = {
    "thanks",
    "thank you",
    "ok",
    "okay",
    "cool",
    "nice",
    "hello",
    "hi",
    "hey",
}
CASUAL_CHAT_PREFIXES = (
    "what does",
    "also what does",
    "and what does",
    "what is",
    "who is",
    "where is",
    "when is",
    "how many",
    "how much",
    "quick question",
)
CASUAL_CHAT_EXCLUSION_TERMS = {
    "project",
    "repo",
    "repository",
    "codebase",
    "architecture",
    "file",
    "files",
    "browser",
    "workflow",
    "task",
    "session",
    "operator",
    "approval",
    "checkpoint",
}
READ_ONLY_OPERATOR_TERMS = {
    "inspect",
    "compare",
    "read",
    "search",
    "list files",
    "check this project",
    "look through",
    "scan",
    "architecture",
    "codebase",
    "repository",
    "repo",
    "suggest commands",
    "plan patch",
    "draft",
    "review bundle",
}
WORKFLOW_OPERATOR_TERMS = {
    "open page",
    "browse",
    "click",
    "click point",
    "desktop click",
    "desktop focus",
    "desktop screenshot",
    "desktop state",
    "focus window",
    "type",
    "type text",
    "follow link",
    "extract text",
    "navigate",
    "fill form",
    "list visible windows",
    "list windows",
    "active window",
    "capture screenshot",
    "queue",
    "schedule",
    "watch",
    "apply edits",
    "apply approved edits",
}
BROWSER_WORKFLOW_REQUEST_TERMS = {
    "open",
    "follow",
    "navigate",
    "click",
    "type",
    "fill",
    "submit",
    "extract text",
    "link",
}
APPROVAL_TERMS = {
    "approve",
    "approved",
    "reject",
    "resume it",
    "resume that",
    "submit it",
}

READ_ONLY_STEP_HINTS = (
    "inspect_project",
    "read_file",
    "compare_files",
    "search_files",
    "list_files",
    "suggest_commands",
)
WORKFLOW_STEP_HINTS = (
    "browser_",
    "desktop_",
    "apply_approved_edits",
    "plan_patch",
    "draft_proposed_edits",
    "build_review_bundle",
    "run_shell",
)

DESKTOP_ACTION_OPERATOR_TERMS = {
    "click point",
    "desktop click",
    "press key",
    "press enter",
    "press tab",
    "keyboard shortcut",
    "desktop key",
    "desktop type",
    "type text",
    "type the exact text",
    "type into the currently focused field",
    "click the known visible button",
    "click the button",
}

ACTION_POLICY_AUTO_ALLOWED = [
    "Natural chat replies and clarification.",
    "Read-only project, file, compare, and summarization work.",
    "Bounded browser inspection/navigation that stays inside the current safe policy.",
    "Bounded desktop inspection such as listing visible windows, checking the active window, focusing a visible window, and capturing a bounded screenshot.",
]
ACTION_POLICY_APPROVAL_REQUIRED = [
    "Browser submit-like or state-changing transitions.",
    "Paused browser checkpoints and review bundles.",
    "Desktop click, bounded keyboard, and desktop type actions.",
    "Applying file edits or any other real mutation.",
]
ACTION_POLICY_FORBIDDEN = [
    "Desktop drag/drop, system keys, unrestricted hotkeys, unrestricted keyboard or mouse control, and broad autonomous desktop navigation.",
    "Autonomous approval, silent escalation, or unapproved file mutation.",
    "Broad dangerous autonomy outside the current safe operator scope.",
]


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _phrase_matches(text: str, phrase: str) -> bool:
    normalized_text = normalize_text(text)
    normalized_phrase = normalize_text(phrase)
    if not normalized_text or not normalized_phrase:
        return False
    pattern = r"\s+".join(re.escape(part) for part in normalized_phrase.split())
    if not pattern:
        return False
    return bool(re.search(rf"(?<!\w){pattern}(?!\w)", normalized_text))


def contains_any(text: str, phrases: set[str]) -> bool:
    return any(_phrase_matches(text, phrase) for phrase in phrases)


def looks_like_path_or_url(text: str) -> bool:
    lowered = str(text or "").lower()
    return (
        ":\\" in lowered
        or "file://" in lowered
        or "http://" in lowered
        or "https://" in lowered
        or bool(re.search(r"\b[\w\-.]+\.(py|md|txt|json|yaml|yml|html|css|js|ts|tsx|jsx)\b", lowered))
    )


def looks_like_simple_conversation_turn(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized or looks_like_path_or_url(normalized):
        return False

    if contains_any(
        normalized,
        ROUTER_STATUS_TERMS
        | ROUTER_DETAIL_TERMS
        | ROUTER_CONTEXT_TERMS
        | APPROVAL_TERMS
        | READ_ONLY_OPERATOR_TERMS
        | WORKFLOW_OPERATOR_TERMS
        | CASUAL_CHAT_EXCLUSION_TERMS,
    ):
        return False

    if normalized in CASUAL_CHAT_TERMS:
        return True

    if len(normalized.split()) <= 14 and any(normalized.startswith(prefix) for prefix in CASUAL_CHAT_PREFIXES):
        return True

    if len(normalized.split()) <= 10 and normalized.endswith("?") and re.fullmatch(r"[a-z0-9\s\+\-\*\/=?.',!]+", normalized):
        return True

    return False


def _infer_operator_request_mode(text: str, *, has_context: bool) -> str:
    if contains_any(text, DESKTOP_ACTION_OPERATOR_TERMS):
        return CHAT_MODE_WORKFLOW
    if contains_any(
        text,
        {
            "active window",
            "capture screenshot",
            "desktop state",
            "desktop screenshot",
            "desktop tools",
            "desktop tool",
            "get active window",
            "list visible windows",
            "list windows",
            "visible windows",
        },
    ):
        return CHAT_MODE_READ_ONLY
    if looks_like_path_or_url(text) and contains_any(text, BROWSER_WORKFLOW_REQUEST_TERMS):
        return CHAT_MODE_WORKFLOW
    if contains_any(text, WORKFLOW_OPERATOR_TERMS):
        return CHAT_MODE_WORKFLOW
    if looks_like_path_or_url(text):
        return CHAT_MODE_READ_ONLY
    if contains_any(text, READ_ONLY_OPERATOR_TERMS):
        return CHAT_MODE_READ_ONLY
    if not has_context and contains_any(text, {"project", "repo", "repository", "codebase", "architecture"}):
        if contains_any(text, {"inspect", "analyze", "explain", "summarize", "compare", "look through", "check"}):
            return CHAT_MODE_READ_ONLY
    return ""


def classify_chat_turn(
    latest_message: str,
    *,
    session_status: str,
    has_context: bool,
    pending_kind: str = "",
) -> Dict[str, str]:
    text = normalize_text(latest_message)
    status = normalize_text(session_status or "idle") or "idle"
    pending = normalize_text(pending_kind)

    continue_request = text in ROUTER_CONTINUE_TERMS or contains_any(text, ROUTER_CONTINUE_TERMS)
    asks_for_status = contains_any(text, ROUTER_STATUS_TERMS)
    asks_for_detail = contains_any(text, ROUTER_DETAIL_TERMS)
    references_context = contains_any(text, ROUTER_CONTEXT_TERMS)
    explicit_approval = contains_any(text, APPROVAL_TERMS)
    operator_mode = _infer_operator_request_mode(text, has_context=has_context)
    detail_follow_up = asks_for_detail or asks_for_status or references_context
    simple_chat = looks_like_simple_conversation_turn(text)

    if pending and (explicit_approval or continue_request):
        return {"mode": CHAT_MODE_APPROVAL, "dispatch": "chat", "reason": "approval_required"}

    if operator_mode:
        return {"mode": operator_mode, "dispatch": "operator", "reason": "operator_request"}

    if pending and detail_follow_up:
        return {"mode": CHAT_MODE_APPROVAL, "dispatch": "chat", "reason": "approval_follow_up"}

    if simple_chat:
        return {"mode": CHAT_MODE_NORMAL, "dispatch": "chat", "reason": "simple_conversation"}

    if status in {"running", "queued"} and (continue_request or detail_follow_up):
        return {"mode": CHAT_MODE_WORKFLOW, "dispatch": "chat", "reason": "active_task_follow_up"}

    if status == "deferred" and (continue_request or detail_follow_up):
        return {"mode": CHAT_MODE_PAUSED, "dispatch": "chat", "reason": "deferred_follow_up"}

    if status == "paused" and detail_follow_up:
        return {"mode": CHAT_MODE_PAUSED, "dispatch": "chat", "reason": "paused_follow_up"}

    if status in {"blocked", "needs_attention", "incomplete"} and has_context and detail_follow_up:
        return {"mode": CHAT_MODE_PAUSED, "dispatch": "chat", "reason": "unresolved_follow_up"}

    if status in SESSION_TERMINAL_STATUSES and has_context and detail_follow_up:
        return {"mode": CHAT_MODE_FINAL, "dispatch": "chat", "reason": "final_report_follow_up"}

    return {"mode": CHAT_MODE_NORMAL, "dispatch": "chat", "reason": "normal_conversation"}


def operator_goal_preamble(mode: str) -> str:
    if mode == CHAT_MODE_READ_ONLY:
        return (
            "Operator mode: read-only investigation. "
            "Stay inside safe inspection, comparison, explanation, and non-executing planning/suggestion work. "
            "Do not apply changes or cross approval gates unless the user explicitly changes scope later."
        )
    if mode == CHAT_MODE_WORKFLOW:
        return (
            "Operator mode: workflow execution. "
            "Continue goal-directed multi-step work inside the current safe operator scope. "
            "Pause cleanly for explicit approval before risky browser transitions or any real mutation."
        )
    return (
        "Operator mode: constrained operator work. "
        "Keep the work coherent, safe, and inside the current approval-gated operator scope."
    )


def _infer_execution_mode(current_step: str, *, browser_task_name: str = "", browser_workflow_name: str = "") -> str:
    step = normalize_text(current_step)
    if browser_task_name or browser_workflow_name:
        return CHAT_MODE_WORKFLOW
    if any(hint in step for hint in READ_ONLY_STEP_HINTS):
        return CHAT_MODE_READ_ONLY
    if any(hint in step for hint in WORKFLOW_STEP_HINTS):
        return CHAT_MODE_WORKFLOW
    return CHAT_MODE_WORKFLOW if step else CHAT_MODE_NORMAL


def derive_behavior_contract(
    *,
    status: Any,
    pending_approval: Dict[str, Any] | None = None,
    current_step: str = "",
    browser_task_name: str = "",
    browser_workflow_name: str = "",
    control_event: str = "",
    control_reason: str = "",
    resume_available: bool = False,
    replacement_task_id: str = "",
) -> Dict[str, Any]:
    pending = pending_approval if isinstance(pending_approval, dict) else {}
    pending_kind = normalize_text(pending.get("kind", ""))
    status_text = normalize_text(status or "idle") or "idle"
    control_event_text = normalize_text(control_event)
    control_reason_text = str(control_reason or "").strip()

    mode = CHAT_MODE_NORMAL
    task_phase = "idle"
    reason = "No active operator task."
    waiting_for = ""
    next_action = ""

    if pending_kind:
        mode = CHAT_MODE_APPROVAL
        task_phase = "approval_gate"
        reason = "A risky or important action is paused behind an explicit approval gate."
        waiting_for = str(pending.get("reason") or pending.get("summary") or "explicit human approval").strip()
        next_action = "Approve or reject the pending action."
    elif control_event_text in {"stop_requested", "defer_requested", "supersede_requested"} and status_text in {"running", "queued"}:
        mode = _infer_execution_mode(
            current_step,
            browser_task_name=browser_task_name,
            browser_workflow_name=browser_workflow_name,
        )
        task_phase = "executing" if status_text == "running" else "queued"
        reason = control_reason_text or "A control request is waiting to take effect after the current bounded step."
        waiting_for = "the current bounded step to finish"
        next_action = "Wait for the control request to settle or review the replacement work."
    elif status_text == "deferred":
        mode = CHAT_MODE_PAUSED
        task_phase = "deferred"
        reason = control_reason_text or "The task was explicitly deferred and will not continue until it is resumed."
        waiting_for = "an explicit resume or replacement decision"
        next_action = "Resume it later, retry it, or replace it with newer work."
    elif status_text == "superseded":
        mode = CHAT_MODE_FINAL
        task_phase = "superseded"
        reason = control_reason_text or "The task was explicitly replaced by newer work."
        waiting_for = ""
        next_action = "Review the replacement task or start a different task."
    elif status_text == "paused":
        mode = CHAT_MODE_PAUSED
        task_phase = "paused"
        reason = "The operator is paused and waiting before continuing."
        waiting_for = "a human decision or a follow-up instruction"
        next_action = "Review the current task state before resuming."
    elif status_text in {"queued", "running"}:
        mode = _infer_execution_mode(
            current_step,
            browser_task_name=browser_task_name,
            browser_workflow_name=browser_workflow_name,
        )
        task_phase = "queued" if status_text == "queued" else ("investigating" if mode == CHAT_MODE_READ_ONLY else "executing")
        reason = (
            "The operator is carrying out read-only investigation."
            if mode == CHAT_MODE_READ_ONLY
            else "The operator is carrying out a bounded workflow."
        )
        next_action = "Wait for the current task to finish or ask for a concise status update."
    elif status_text == "completed":
        mode = CHAT_MODE_FINAL
        task_phase = "completed"
        reason = "The last operator task finished and should end in one authoritative answer."
        next_action = "Ask follow-up questions or give a new task if needed."
    elif status_text in {"blocked", "needs_attention", "incomplete"}:
        mode = CHAT_MODE_PAUSED
        task_phase = status_text
        if control_event_text == "rejected":
            reason = control_reason_text or "A required approval was rejected, so the task stopped short of completion."
        else:
            reason = "The operator reached a blocked or unresolved state and needs human direction."
        waiting_for = "clarification, approval, or a change in approach"
        next_action = "Review the blocker and decide how the operator should proceed."
    elif status_text in {"failed", "stopped"}:
        mode = CHAT_MODE_FINAL
        task_phase = status_text
        reason = control_reason_text or "The last operator attempt ended without completing the goal."
        next_action = "Review the outcome and decide whether to retry or redirect."

    return {
        "mode": mode,
        "mode_label": MODE_LABELS.get(mode, "Operator state"),
        "task_phase": task_phase,
        "task_phase_label": PHASE_LABELS.get(task_phase, task_phase.replace("_", " ").title()),
        "reason": reason,
        "waiting_for": waiting_for,
        "next_action": next_action,
        "current_focus": str(current_step or "").strip(),
        "control_event": control_event_text,
        "control_reason": control_reason_text,
        "replacement_task_id": str(replacement_task_id or "").strip(),
        "resume_available": bool(resume_available),
        "action_policy": {
            "summary": "Safe chat, read-only investigation, and bounded browser inspection may proceed automatically. Approval gates still control risky transitions and all real mutations.",
            "auto_allowed": list(ACTION_POLICY_AUTO_ALLOWED),
            "approval_required": list(ACTION_POLICY_APPROVAL_REQUIRED),
            "forbidden": list(ACTION_POLICY_FORBIDDEN),
        },
        "human_control": {
            "state": "approval_required" if pending_kind else ("waiting" if task_phase in {"paused", "blocked", "needs_attention", "incomplete"} else "available"),
            "can_approve": bool(pending_kind),
            "can_reject": bool(pending_kind),
            "waiting_for": waiting_for,
            "next_action": next_action,
            "resume_available": bool(resume_available),
        },
    }


def behavior_context_lines(behavior: Dict[str, Any]) -> list[str]:
    if not isinstance(behavior, dict):
        return []

    lines = [
        f"Operator mode: {str(behavior.get('mode_label', '')).strip()}",
        f"Task phase: {str(behavior.get('task_phase_label', '')).strip()}",
    ]
    reason = str(behavior.get("reason", "")).strip()
    if reason:
        lines.append(f"Behavior summary: {reason}")
    focus = str(behavior.get("current_focus", "")).strip()
    if focus:
        lines.append(f"Current focus: {focus}")
    human_control = behavior.get("human_control", {}) if isinstance(behavior.get("human_control", {}), dict) else {}
    waiting_for = str(human_control.get("waiting_for", "")).strip()
    if waiting_for:
        lines.append(f"Waiting for: {waiting_for}")
    next_action = str(human_control.get("next_action", "")).strip()
    if next_action:
        lines.append(f"Next human action: {next_action}")
    action_policy = behavior.get("action_policy", {}) if isinstance(behavior.get("action_policy", {}), dict) else {}
    summary = str(action_policy.get("summary", "")).strip()
    if summary:
        lines.append(f"Action policy: {summary}")
    return [line for line in lines if line.strip()]
