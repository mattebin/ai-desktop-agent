from __future__ import annotations

import re
import time

from core.operator_intelligence import (
    apply_outcome_evaluation,
    capture_action_context,
    guard_repeated_failed_action,
    guard_repeated_failed_desktop_strategy,
    guard_repeated_failed_open_family,
    refresh_operator_intelligence_context,
)
from core.safety import stop_requested
from core.tool_runtime import ToolRuntime


NON_MUTATING_REPEAT_GUARD_TOOLS = {
    "compare_files",
    "inspect_project",
    "suggest_commands",
}
_BROWSER_URL_PATTERN = re.compile(r"(?:file|https?)://\S+", re.IGNORECASE)
_BROWSER_BOOTSTRAP_TERMS = ("open", "follow", "click", "type", "fill", "submit", "page", "link", "browser")
_BROWSER_CHECKPOINT_PAUSE_TERMS = ("pause before", "pause right before", "submit-like click", "approval checkpoint")
_DESKTOP_CLICK_COORD_PATTERN = re.compile(r"\(\s*(\d{1,5})\s*,\s*(\d{1,5})\s*\)")
_DESKTOP_TYPE_VALUE_PATTERNS = (
    re.compile(r"type the exact text ['\"]([^'\"]{1,200})['\"]", re.IGNORECASE),
    re.compile(r"type the text ['\"]([^'\"]{1,200})['\"]", re.IGNORECASE),
    re.compile(r"type ['\"]([^'\"]{1,200})['\"]", re.IGNORECASE),
)
_DESKTOP_KEY_SEQUENCE_PATTERN = re.compile(
    r"(?:press|hit|send)(?: the)?(?: key| shortcut)?\s+['\"]?([^'\".,;]{1,40})['\"]?(?:\s+in\b|\s+on\b|$)",
    re.IGNORECASE,
)
_DESKTOP_FIELD_LABEL_PATTERN = re.compile(r"field label(?:ed|led)? ['\"]([^'\"]{1,120})['\"]", re.IGNORECASE)
_DESKTOP_WINDOW_TITLE_PATTERN = re.compile(r"window titled ['\"]([^'\"]{1,180})['\"]", re.IGNORECASE)
_DESKTOP_INSPECT_TOOLS = {
    "desktop_list_windows",
    "desktop_get_active_window",
    "desktop_capture_screenshot",
    "desktop_inspect_window_state",
    "desktop_recover_window",
    "desktop_wait_for_window_ready",
}
_DESKTOP_RECOVERY_TOOLS = {
    "desktop_focus_window",
    "desktop_inspect_window_state",
    "desktop_recover_window",
    "desktop_wait_for_window_ready",
}
_DESKTOP_MUTATING_TOOLS = {
    "desktop_focus_window",
    "desktop_move_mouse",
    "desktop_hover_point",
    "desktop_click_mouse",
    "desktop_click_point",
    "desktop_scroll",
    "desktop_press_key",
    "desktop_press_key_sequence",
    "desktop_type_text",
}
_DESKTOP_PROPOSAL_APPROVAL_CONTROLLED_TOOLS = {
    "desktop_click_mouse",
    "desktop_click_point",
    "desktop_scroll",
    "desktop_press_key",
    "desktop_press_key_sequence",
    "desktop_type_text",
}
_DESKTOP_ACTION_PACING_SECONDS = 0.05
_DESKTOP_ACTION_PACING_WINDOW_SECONDS = 0.35
FINALIZE_MESSAGE_TIMEOUT_SECONDS = 30


def _persist_session_state(session_store, task_state):
    if session_store is None:
        return
    session_store.save(task_state)


def _emit_progress(progress_callback, stage: str, *, detail: str = "", tool_name: str = "", result_status: str = ""):
    if not callable(progress_callback):
        return
    try:
        progress_callback(
            stage,
            detail=detail,
            tool_name=tool_name,
            result_status=result_status,
        )
    except Exception:
        pass


def _fallback_finalize_message(task_state) -> str:
    status = str(getattr(task_state, "status", "")).strip().lower()
    desktop_snapshot = {}
    pending_approval = {}
    try:
        control_snapshot = task_state.get_control_snapshot()
        desktop_snapshot = control_snapshot.get("desktop", {}) if isinstance(control_snapshot, dict) else {}
        pending_approval = control_snapshot.get("pending_approval", {}) if isinstance(control_snapshot, dict) else {}
    except Exception:
        desktop_snapshot = {}
        pending_approval = {}
    desktop_outcome = desktop_snapshot.get("run_outcome", {}) if isinstance(desktop_snapshot.get("run_outcome", {}), dict) else {}
    outcome_summary = str(desktop_outcome.get("summary", "")).strip()
    active_window = str(getattr(task_state, "desktop_active_window_title", "")).strip()
    target_window = str(desktop_outcome.get("target_window_title", "")).strip() or str(getattr(task_state, "desktop_last_target_window", "")).strip()
    screenshot_path = str(getattr(task_state, "desktop_last_screenshot_path", "")).strip()
    notes = [str(item).strip() for item in list(getattr(task_state, "memory_notes", []))[-3:] if str(item).strip()]
    summary = str(getattr(task_state, "last_summary", "")).strip()
    outcome_reason = str(desktop_outcome.get("reason", "")).strip().lower()
    outcome_name = str(desktop_outcome.get("outcome", "")).strip().lower()
    pending_kind = str((pending_approval or {}).get("kind", "")).strip().lower()
    pending_tool = str((pending_approval or {}).get("tool", "")).strip()
    pending_target = str((pending_approval or {}).get("target", "")).strip()
    next_step = ""

    if status == "completed":
        lead_target = target_window or active_window
        lead = f"I completed the bounded desktop run for '{lead_target}'." if lead_target else "I completed the bounded desktop run."
        details: list[str] = []
        if outcome_summary:
            details.append(outcome_summary)
        if screenshot_path:
            if active_window:
                details.append(f"I captured a screenshot of the active window '{active_window}'.")
            else:
                details.append("I captured a screenshot of the active window.")
    elif status == "paused":
        if pending_kind == "desktop_action":
            tool_label = pending_tool or "the requested desktop action"
            target_label = pending_target or target_window or active_window
            lead = f"I paused at a desktop approval checkpoint before {tool_label}."
            if target_label:
                lead = f"{lead} The current target is '{target_label}'."
            next_step = "Reply yes to approve the paused desktop action or no to reject it."
        else:
            lead = "I paused at an approval checkpoint and need your decision before continuing."
        details = [outcome_summary] if outcome_summary else []
    elif status == "blocked":
        lead = (
            f"I stopped because the bounded desktop path could not continue safely for '{target_window}'."
            if target_window
            else "I stopped because the bounded desktop path could not continue safely."
        )
        details = [outcome_summary] if outcome_summary else []
    elif status == "incomplete":
        lead = (
            f"I reached a bounded stopping point for '{target_window}' without fully completing the desktop run."
            if target_window
            else "I reached a bounded stopping point without fully completing the desktop run."
        )
        details = [outcome_summary] if outcome_summary else []
    else:
        lead = "I finished the bounded operator run."
        details = [outcome_summary] if outcome_summary else []

    if active_window and active_window != target_window and status in {"blocked", "incomplete"}:
        details.append(f"The active window I could still observe was '{active_window}'.")
    if not details and notes:
        details.extend(notes[:2])
    if not details and summary:
        details.append(summary)
    if screenshot_path and status in {"completed", "paused"}:
        details.append(f"Latest screenshot evidence: {screenshot_path}.")

    if not next_step and outcome_name in {
        "unrecoverable_missing_target",
        "unrecoverable_tray_background",
        "unrecoverable_withdrawn",
    }:
        if target_window:
            next_step = f"The next step is to bring '{target_window}' back visibly or reopen it, then ask me to re-check."
        else:
            next_step = "The next step is to bring the requested window back visibly or reopen it, then ask me to re-check."
    elif not next_step and outcome_name == "recovery_exhausted":
        next_step = "The next step is to narrow the desktop target or put the intended window into a visibly ready state before retrying."
    elif not next_step and status == "blocked" and outcome_reason in {"post_result_timeout", "loop_entry_timeout", "first_progress_timeout"}:
        next_step = "The next step is to retry once the window is visibly ready and the bounded desktop path can progress cleanly."
    if next_step:
        details.append(next_step)

    rendered = " ".join(part.strip() for part in [lead, *details] if part and part.strip())
    rendered = re.sub(r"\s+", " ", rendered).strip()
    return rendered or lead


def _should_short_circuit_desktop_finalization(task_state) -> bool:
    status = str(getattr(task_state, "status", "")).strip().lower()
    if status not in {"paused", "blocked", "incomplete"}:
        return False
    try:
        control_snapshot = task_state.get_control_snapshot()
    except Exception:
        control_snapshot = {}
    pending_approval = control_snapshot.get("pending_approval", {}) if isinstance(control_snapshot, dict) else {}
    if status == "paused" and str((pending_approval or {}).get("kind", "")).strip().lower() == "desktop_action":
        return True
    desktop_snapshot = control_snapshot.get("desktop", {}) if isinstance(control_snapshot, dict) else {}
    desktop_outcome = desktop_snapshot.get("run_outcome", {}) if isinstance(desktop_snapshot.get("run_outcome", {}), dict) else {}
    return bool(desktop_outcome.get("terminal", False))


def _finalize_message(llm, task_state, *, progress_callback=None) -> str:
    _emit_progress(
        progress_callback,
        "final_reply_rendering",
        detail="Rendering the authoritative final reply from the bounded task state.",
    )
    if _should_short_circuit_desktop_finalization(task_state):
        _emit_progress(
            progress_callback,
            "final_reply_rendering",
            detail="Using compact grounded fallback for a bounded desktop terminal or approval state.",
        )
        return _fallback_finalize_message(task_state)
    try:
        return llm.finalize(
            task_state.goal,
            task_state.steps,
            task_state.get_observation(),
            task_state.get_final_context(),
            desktop_vision=task_state.get_desktop_vision_context(
                purpose="desktop_final",
                prompt_text=task_state.goal,
                prefer_before_after=True,
            ),
            timeout_seconds=FINALIZE_MESSAGE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        _emit_progress(
            progress_callback,
            "final_reply_rendering",
            detail=f"Final reply rendering failed ({type(exc).__name__}); using compact fallback.",
        )
        return _fallback_finalize_message(task_state)


def _finalize_control_request(llm, task_state, request, *, session_store=None, progress_callback=None):
    action = str((request or {}).get("action", "")).strip().lower()
    reason = str((request or {}).get("reason", "")).strip()
    replacement_task_id = str((request or {}).get("replacement_task_id", "")).strip()
    replacement_goal = str((request or {}).get("replacement_goal", "")).strip()

    if action == "stop":
        status = "stopped"
        event = "stopped"
        note = reason or "Stopped the current task by explicit operator control."
        resume_available = False
    elif action == "defer":
        status = "deferred"
        event = "deferred"
        note = reason or "Deferred the current task for later resumption."
        resume_available = True
    elif action == "supersede":
        status = "superseded"
        event = "superseded"
        note = reason or "Superseded the current task with newer operator work."
        resume_available = False
    else:
        return None

    task_state.add_step(
        {
            "type": "system",
            "status": action or "control",
            "message": note,
            "replacement_task_id": replacement_task_id,
        }
    )
    task_state.set_task_control(
        event=event,
        reason=note,
        resume_available=resume_available,
        replacement_task_id=replacement_task_id,
        replacement_goal=replacement_goal,
    )
    task_state.add_note(note)
    recent_notes = task_state.memory_notes[-6:]
    if recent_notes:
        task_state.set_summary(" | ".join(recent_notes))
    task_state.status = status
    _persist_session_state(session_store, task_state)
    return {
        "ok": True,
        "status": status,
        "message": _finalize_message(llm, task_state, progress_callback=progress_callback),
        "steps": task_state.steps,
    }


def _record_tool_result(task_state, tool_name, args, result, *, before_context=None):
    step_status = "paused" if result.get("paused", False) else ("completed" if result.get("ok", False) else "failed")
    target_proposals = _desktop_active_target_proposals(task_state) if tool_name.startswith("desktop_") else {}
    step = {
        "type": "tool",
        "status": step_status,
        "tool": tool_name,
        "args": args,
        "result": result,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "target_proposals": target_proposals if isinstance(target_proposals, dict) else {},
    }
    task_state.add_step(step)
    apply_outcome_evaluation(task_state, tool_name, args, result, before_context=before_context)
    task_state.update_memory_from_tool(tool_name, result)
    task_state.add_note(task_state.summarize_result_for_memory(tool_name, result))

    recent_notes = task_state.memory_notes[-6:]
    if recent_notes:
        task_state.set_summary(" | ".join(recent_notes))


def _finalize_guarded_completion(llm, task_state, note: str, *, tool_name="", args=None, session_store=None, progress_callback=None):
    task_state.add_step(
        {
            "type": "system",
            "status": "guarded",
            "message": note,
            "tool": tool_name,
            "args": args or {},
        }
    )
    task_state.add_note(note)
    recent_notes = task_state.memory_notes[-6:]
    if recent_notes:
        task_state.set_summary(" | ".join(recent_notes))
    task_state.status = "completed"
    _persist_session_state(session_store, task_state)
    return {
        "ok": True,
        "status": "completed",
        "message": _finalize_message(llm, task_state, progress_callback=progress_callback),
        "steps": task_state.steps,
    }


def _maybe_finalize_operator_retry_stop(
    llm,
    task_state,
    tool_name: str,
    result: dict,
    *,
    session_store=None,
    progress_callback=None,
):
    evaluation = result.get("evaluation", {}) if isinstance(result.get("evaluation", {}), dict) else {}
    retry = evaluation.get("retry", {}) if isinstance(evaluation.get("retry", {}), dict) else {}
    if not bool(retry.get("stop_run", False)):
        return None
    if result.get("paused", False):
        return None

    summary = str(retry.get("explanation", "") or evaluation.get("summary", "") or result.get("summary", "") or result.get("error", "")).strip()
    if not summary:
        summary = f"Stopped after the latest {tool_name} outcome because the bounded retry budget is exhausted."
    task_state.add_step(
        {
            "type": "system",
            "status": "blocked",
            "tool": "operator_intelligence",
            "message": summary,
        }
    )
    task_state.add_note(summary)
    recent_notes = task_state.memory_notes[-6:]
    if recent_notes:
        task_state.set_summary(" | ".join(recent_notes))
    task_state.status = "blocked" if str(evaluation.get("status", "")).strip() == "blocked" else "incomplete"
    _persist_session_state(session_store, task_state)
    return {
        "ok": False,
        "status": task_state.status,
        "message": _finalize_message(llm, task_state, progress_callback=progress_callback),
        "steps": task_state.steps,
    }


def _is_repeated_non_mutating_plan(task_state, tool_name, args) -> bool:
    if tool_name not in NON_MUTATING_REPEAT_GUARD_TOOLS:
        return False
    if not task_state.steps:
        return False

    last_step = task_state.steps[-1]
    if last_step.get("type") != "tool":
        return False
    if last_step.get("status") != "completed":
        return False
    if last_step.get("tool") != tool_name:
        return False

    last_args = last_step.get("args", {}) if isinstance(last_step.get("args", {}), dict) else {}
    current_args = args if isinstance(args, dict) else {}

    if tool_name == "suggest_commands":
        return True

    if tool_name == "compare_files":
        return (
            str(last_args.get("path_a", "")).strip() == str(current_args.get("path_a", "")).strip()
            and str(last_args.get("path_b", "")).strip() == str(current_args.get("path_b", "")).strip()
        )

    if tool_name == "inspect_project":
        return (
            str(last_args.get("path", "")).strip() == str(current_args.get("path", "")).strip()
            and str(last_args.get("focus", "")).strip() == str(current_args.get("focus", "")).strip()
            and bool(last_args.get("refresh", False)) == bool(current_args.get("refresh", False))
        )

    return last_args == current_args


def _finalize_repeated_non_mutating_plan(llm, task_state, tool_name, args, session_store=None):
    note = f"Stopped repeated identical {tool_name} call and finalized from collected evidence."
    return _finalize_guarded_completion(
        llm,
        task_state,
        note,
        tool_name=tool_name,
        args=args,
        session_store=session_store,
    )


def _has_completed_approved_browser_click(task_state) -> bool:
    for step in reversed(task_state.steps):
        if step.get("type") != "tool":
            continue
        if step.get("tool") != "browser_click" or step.get("status") != "completed":
            continue

        args = step.get("args", {}) if isinstance(step.get("args", {}), dict) else {}
        result = step.get("result", {}) if isinstance(step.get("result", {}), dict) else {}
        approval_status = str(args.get("approval_status", result.get("approval_status", ""))).strip().lower()
        if approval_status == "approved":
            return True
    return False


def _is_redundant_browser_follow_up(task_state, tool_name, args) -> bool:
    if getattr(task_state, "browser_checkpoint_pending", False):
        return False

    current_args = args if isinstance(args, dict) else {}
    if tool_name == "browser_click":
        approval_status = str(current_args.get("approval_status", "")).strip().lower()
        return approval_status == "approved" and _has_completed_approved_browser_click(task_state)

    if tool_name == "browser_inspect_page":
        if not _has_completed_approved_browser_click(task_state):
            return False
        if not str(getattr(task_state, "browser_last_text_excerpt", "")).strip():
            return False
        if not task_state.steps:
            return False

        last_step = task_state.steps[-1]
        return (
            last_step.get("type") == "tool"
            and last_step.get("status") == "completed"
            and last_step.get("tool") == "browser_inspect_page"
        )

    return False


def _finalize_redundant_browser_follow_up(llm, task_state, tool_name, args, session_store=None):
    if tool_name == "browser_click":
        note = "Stopped repeating an approved browser click after the action had already succeeded and finalized from confirmed browser state."
    else:
        note = "Stopped repeating browser inspection after the changed browser state was already confirmed and finalized from collected browser evidence."
    return _finalize_guarded_completion(
        llm,
        task_state,
        note,
        tool_name=tool_name,
        args=args,
        session_store=session_store,
    )


def _has_browser_step(task_state) -> bool:
    return any(str(step.get("tool", "")).startswith("browser_") for step in task_state.steps)


def _has_completed_tool(task_state, tool_name: str) -> bool:
    for step in reversed(task_state.steps):
        if step.get("type") != "tool":
            continue
        if str(step.get("tool", "")).strip() != tool_name:
            continue
        if str(step.get("status", "")).strip() == "completed":
            return True
    return False


def _has_any_tool_step(task_state, tool_name: str) -> bool:
    for step in reversed(task_state.steps):
        if step.get("type") != "tool":
            continue
        if str(step.get("tool", "")).strip() == tool_name:
            return True
    return False


def _has_completed_or_paused_tool_step(task_state, tool_name: str) -> bool:
    for step in reversed(task_state.steps):
        if step.get("type") != "tool":
            continue
        if str(step.get("tool", "")).strip() != tool_name:
            continue
        if str(step.get("status", "")).strip() in {"completed", "paused"}:
            return True
    return False


def _latest_tool_result(task_state, tool_names, *, allowed_statuses=None):
    if isinstance(tool_names, str):
        tool_name_set = {tool_names}
    else:
        tool_name_set = {str(name).strip() for name in list(tool_names or []) if str(name).strip()}
    if not tool_name_set:
        return None

    statuses = set(allowed_statuses or {"completed", "failed", "paused"})
    for step in reversed(task_state.steps):
        if step.get("type") != "tool":
            continue
        if str(step.get("tool", "")).strip() not in tool_name_set:
            continue
        if str(step.get("status", "")).strip() not in statuses:
            continue
        result = step.get("result", {})
        if isinstance(result, dict):
            return result
    return None


def _desktop_has_completed_focus_context(task_state) -> bool:
    return _has_completed_tool(task_state, "desktop_focus_window") or _has_completed_tool(task_state, "desktop_recover_window")


def _desktop_target_seed_args(task_state, planner_goal: str) -> dict:
    target_title = _goal_desktop_window_title(planner_goal)
    checkpoint_args = getattr(task_state, "desktop_checkpoint_resume_args", {})
    if not target_title and isinstance(checkpoint_args, dict):
        target_title = str(checkpoint_args.get("expected_window_title", "")).strip()
    if not target_title:
        target_title = str(getattr(task_state, "desktop_last_target_window", "")).strip()

    seed_args = {
        "limit": 20,
        "ui_limit": 8,
        "wait_seconds": 1.6,
        "poll_interval_seconds": 0.12,
        "stability_samples": 3,
        "stability_interval_ms": 120,
        "max_attempts": 2,
    }
    if target_title:
        seed_args["title"] = target_title
        seed_args["expected_window_title"] = target_title
        seed_args["exact"] = True
    elif getattr(task_state, "desktop_active_window_title", ""):
        active_title = str(getattr(task_state, "desktop_active_window_title", "")).strip()
        seed_args["title"] = active_title
        seed_args["expected_window_title"] = active_title
        seed_args["exact"] = False

    if isinstance(checkpoint_args, dict):
        expected_window_id = str(checkpoint_args.get("expected_window_id", "")).strip()
        expected_window_title = str(checkpoint_args.get("expected_window_title", "")).strip()
        if expected_window_id:
            seed_args.setdefault("window_id", expected_window_id)
            seed_args.setdefault("expected_window_id", expected_window_id)
        if expected_window_title:
            seed_args.setdefault("expected_window_title", expected_window_title)
            seed_args.setdefault("title", expected_window_title)
            seed_args.setdefault("exact", True)

    return seed_args


def _desktop_latest_recovery(task_state) -> dict:
    result = _latest_tool_result(task_state, _DESKTOP_RECOVERY_TOOLS)
    if not isinstance(result, dict):
        return {}
    recovery = result.get("recovery", {})
    return recovery if isinstance(recovery, dict) else {}


def _desktop_latest_window_readiness(task_state) -> dict:
    result = _latest_tool_result(task_state, _DESKTOP_RECOVERY_TOOLS)
    if not isinstance(result, dict):
        return {}
    readiness = result.get("window_readiness", {})
    return readiness if isinstance(readiness, dict) else {}


def _desktop_latest_visual_stability(task_state) -> dict:
    result = _latest_tool_result(task_state, _DESKTOP_RECOVERY_TOOLS)
    if not isinstance(result, dict):
        return {}
    stability = result.get("visual_stability", {})
    return stability if isinstance(stability, dict) else {}


def _desktop_partial_evidence_allows_checkpoint(task_state, planner_goal: str, *, require_screenshot: bool = False) -> bool:
    desktop_activity = task_state._collect_desktop_activity(limit=4)
    selected = desktop_activity.get("selected_evidence", {}) if isinstance(desktop_activity.get("selected_evidence", {}), dict) else {}
    if require_screenshot and not bool(selected.get("has_screenshot", False)):
        return False
    if not _desktop_target_window_ready(task_state, planner_goal):
        return False

    recovery_state = str(_desktop_latest_recovery(task_state).get("state", "")).strip().lower()
    if recovery_state != "ready":
        return False

    readiness_state = str(_desktop_latest_window_readiness(task_state).get("state", "")).strip().lower()
    if readiness_state in {"missing", "not_ready", "loading"}:
        return False

    visual_state = str(_desktop_latest_visual_stability(task_state).get("state", "")).strip().lower()
    if visual_state == "unstable":
        return False

    return True


def _execute_desktop_tool_step(
    tool_runtime,
    task_state,
    tool_name: str,
    seed_args: dict,
    planner_goal: str,
    *,
    session_store=None,
    progress_callback=None,
):
    before_context = capture_action_context(task_state, tool_name, seed_args)
    args = tool_runtime.prepare_args(tool_name, seed_args, task_state, planning_goal=planner_goal)
    if tool_name == "desktop_open_target":
        open_guard = guard_repeated_failed_open_family(task_state, args)
        if open_guard:
            _emit_progress(
                progress_callback,
                "tool_step_attempted",
                detail=f"Blocked repeated Windows open strategy family before execution: {tool_name}.",
                tool_name=tool_name,
            )
            _record_tool_result(task_state, tool_name, args, open_guard, before_context=before_context)
            _emit_progress(
                progress_callback,
                "tool_result_recorded",
                detail=f"Recorded guarded result for bounded tool step: {tool_name}.",
                tool_name=tool_name,
                result_status="failed",
            )
            _persist_session_state(session_store, task_state)
            return args, open_guard
    elif tool_name in {
        "desktop_click_mouse",
        "desktop_click_point",
        "desktop_scroll",
        "desktop_type_text",
        "desktop_press_key",
        "desktop_press_key_sequence",
        "desktop_start_process",
        "desktop_run_command",
    }:
        strategy_guard = guard_repeated_failed_desktop_strategy(task_state, tool_name, args)
        if strategy_guard:
            _emit_progress(
                progress_callback,
                "tool_step_attempted",
                detail=f"Blocked repeated desktop strategy family before execution: {tool_name}.",
                tool_name=tool_name,
            )
            _record_tool_result(task_state, tool_name, args, strategy_guard, before_context=before_context)
            _emit_progress(
                progress_callback,
                "tool_result_recorded",
                detail=f"Recorded guarded result for bounded tool step: {tool_name}.",
                tool_name=tool_name,
                result_status="failed",
            )
            _persist_session_state(session_store, task_state)
            return args, strategy_guard
    guarded_result = _maybe_guard_desktop_action(tool_runtime, task_state, planner_goal, tool_name, args)
    if guarded_result is not None:
        _emit_progress(
            progress_callback,
            "tool_step_attempted",
            detail=f"Blocked bounded tool step before execution: {tool_name}.",
            tool_name=tool_name,
        )
        _record_tool_result(task_state, tool_name, args, guarded_result, before_context=before_context)
        _emit_progress(
            progress_callback,
            "tool_result_recorded",
            detail=f"Recorded guarded result for bounded tool step: {tool_name}.",
            tool_name=tool_name,
            result_status="failed",
        )
        _persist_session_state(session_store, task_state)
        return args, guarded_result

    if tool_name in _DESKTOP_MUTATING_TOOLS:
        _maybe_pace_desktop_action(task_state, tool_name)

    _emit_progress(
        progress_callback,
        "tool_step_attempted",
        detail=f"Attempting bounded tool step: {tool_name}.",
        tool_name=tool_name,
    )
    result = tool_runtime.execute(tool_name, args)
    _record_tool_result(task_state, tool_name, args, result, before_context=before_context)
    _emit_progress(
        progress_callback,
        "tool_result_recorded",
        detail=f"Recorded result from bounded tool step: {tool_name}.",
        tool_name=tool_name,
        result_status="paused" if result.get("paused", False) else ("completed" if result.get("ok", False) else "failed"),
    )
    _persist_session_state(session_store, task_state)
    return args, result


def _goal_mentions_desktop_focus(goal: str) -> bool:
    return "focus" in str(goal or "").strip().lower()


def _goal_mentions_desktop_screenshot(goal: str) -> bool:
    lowered = str(goal or "").strip().lower()
    return "screenshot" in lowered or "capture" in lowered


def _goal_desktop_window_title(goal: str) -> str:
    match = _DESKTOP_WINDOW_TITLE_PATTERN.search(str(goal or ""))
    return str(match.group(1)).strip() if match else ""


def _goal_desktop_click_point(goal: str) -> tuple[int, int] | None:
    lowered = str(goal or "").strip().lower()
    if "click" not in lowered:
        return None
    matches = _DESKTOP_CLICK_COORD_PATTERN.findall(str(goal or ""))
    if not matches:
        return None
    last_x, last_y = matches[-1]
    return int(last_x), int(last_y)


def _goal_desktop_type_request(goal: str) -> dict | None:
    lowered = str(goal or "").strip().lower()
    if "type" not in lowered:
        return None
    value = ""
    for pattern in _DESKTOP_TYPE_VALUE_PATTERNS:
        match = pattern.search(str(goal or ""))
        if match:
            value = str(match.group(1)).strip()
            break
    label_match = _DESKTOP_FIELD_LABEL_PATTERN.search(str(goal or ""))
    field_label = str(label_match.group(1)).strip() if label_match else ""
    if not value or not field_label:
        return None
    return {"value": value, "field_label": field_label}


def _goal_desktop_key_request(goal: str) -> dict | None:
    lowered = str(goal or "").strip().lower()
    if not any(term in lowered for term in ("press", "hit", "send")):
        return None
    match = _DESKTOP_KEY_SEQUENCE_PATTERN.search(str(goal or ""))
    if not match:
        return None

    raw_sequence = " ".join(str(match.group(1) or "").strip().lower().split())
    raw_sequence = raw_sequence.replace("control", "ctrl").replace("plus", "+")
    raw_sequence = raw_sequence.replace("page up", "pageup").replace("page down", "pagedown")
    raw_sequence = raw_sequence.replace("arrow up", "up").replace("arrow down", "down")
    raw_sequence = raw_sequence.replace("arrow left", "left").replace("arrow right", "right")
    raw_sequence = raw_sequence.replace("escape", "esc").replace("del", "delete")
    parts = [part for part in re.split(r"\s*\+\s*|\s+", raw_sequence) if part]
    if not parts:
        return None

    modifiers: list[str] = []
    key = ""
    for index, token in enumerate(parts):
        if token in {"ctrl", "shift"} and index < len(parts) - 1:
            if token not in modifiers:
                modifiers.append(token)
            continue
        key = token
        break

    if not key:
        return None
    if modifiers and len(modifiers) != len(parts) - 1:
        return None
    return {"key": key, "modifiers": modifiers, "repeat": 1}


def _goal_is_desktop_related(goal: str, task_state) -> bool:
    lowered = str(goal or "").strip().lower()
    if any(
        term in lowered
        for term in (
            "desktop",
            "window",
            "foreground",
            "active window",
            "screenshot",
            "click",
            "type",
            "press",
            "focus",
            "hidden",
            "minimized",
            "tray",
            "loading",
            "unstable",
        )
    ):
        return True
    return bool(
        getattr(task_state, "desktop_observation_token", "")
        or getattr(task_state, "desktop_last_evidence_id", "")
        or getattr(task_state, "desktop_checkpoint_pending", False)
    )


def _desktop_vision_purpose(goal: str, task_state) -> str:
    if getattr(task_state, "desktop_checkpoint_pending", False):
        return "desktop_approval"
    if _goal_desktop_click_point(goal) is not None or _goal_desktop_key_request(goal) is not None or _goal_desktop_type_request(goal) is not None:
        return "desktop_action_prepare"
    return "desktop_investigation"


def _desktop_has_inspection_context(task_state) -> bool:
    return any(_has_completed_tool(task_state, tool_name) for tool_name in _DESKTOP_INSPECT_TOOLS)


def _desktop_target_window_ready(task_state, planner_goal: str) -> bool:
    try:
        selected_scene = task_state._collect_desktop_activity(limit=4).get("selected_scene", {})
    except Exception:
        selected_scene = {}
    if isinstance(selected_scene, dict):
        readiness_state = str(selected_scene.get("readiness_state", "")).strip().lower()
        workflow_state = str(selected_scene.get("workflow_state", "")).strip().lower()
        if readiness_state in {"loading", "not_ready", "unstable", "background", "missing"}:
            return False
        if workflow_state in {"loading", "recovering", "blocked", "attention_needed"}:
            return False
    latest_recovery = _desktop_latest_recovery(task_state)
    latest_state = str(latest_recovery.get("state", "")).strip().lower()
    if latest_state == "ready":
        expected_title = _goal_desktop_window_title(planner_goal).lower()
        if not expected_title:
            return True
        target_window = latest_recovery.get("target_window", {}) if isinstance(latest_recovery.get("target_window", {}), dict) else {}
        active_window = latest_recovery.get("active_window", {}) if isinstance(latest_recovery.get("active_window", {}), dict) else {}
        candidate_titles = {
            str(target_window.get("title", "")).strip().lower(),
            str(active_window.get("title", "")).strip().lower(),
            str(getattr(task_state, "desktop_active_window_title", "")).strip().lower(),
        }
        return any(title and (expected_title in title or title in expected_title) for title in candidate_titles)
    if latest_state in {"needs_recovery", "waiting", "missing"}:
        return False

    active_title = str(getattr(task_state, "desktop_active_window_title", "")).strip()
    if not active_title:
        return False
    expected_title = _goal_desktop_window_title(planner_goal)
    if not expected_title:
        return True
    return expected_title.lower() in active_title.lower()


def _desktop_evidence_assessment(
    task_state,
    *,
    purpose: str,
    target_window_title: str = "",
    require_screenshot: bool = False,
):
    try:
        from core.desktop_evidence import assess_desktop_evidence

        desktop_activity = task_state._collect_desktop_activity(limit=4)
        return assess_desktop_evidence(
            desktop_activity.get("selected_evidence", {}),
            purpose=purpose,
            target_window_title=target_window_title or desktop_activity.get("last_target_window", ""),
            require_screenshot=require_screenshot,
            max_age_seconds=240 if purpose == "desktop_investigation" else 120,
        )
    except Exception:
        return {}


def _desktop_selected_scene(task_state) -> dict:
    try:
        desktop_activity = task_state._collect_desktop_activity(limit=4)
        scene = desktop_activity.get("selected_scene", {})
        return scene if isinstance(scene, dict) else {}
    except Exception:
        return {}


def _desktop_activity_snapshot(task_state) -> dict:
    try:
        activity = task_state._collect_desktop_activity(limit=4)
        return activity if isinstance(activity, dict) else {}
    except Exception:
        return {}


def _desktop_build_run_outcome(
    task_state,
    *,
    outcome: str,
    status: str,
    terminal: bool,
    reason: str,
    summary: str,
):
    from core.backend_schemas import normalize_desktop_run_outcome

    activity = _desktop_activity_snapshot(task_state)
    scene = activity.get("selected_scene", {}) if isinstance(activity.get("selected_scene", {}), dict) else {}
    recovery = activity.get("latest_recovery", {}) if isinstance(activity.get("latest_recovery", {}), dict) else {}
    assessment = (
        activity.get("checkpoint_evidence_assessment", {})
        if status == "paused"
        else activity.get("selected_evidence_assessment", {})
    )
    if not isinstance(assessment, dict):
        assessment = {}
    return normalize_desktop_run_outcome(
        {
            "outcome": outcome,
            "status": status,
            "terminal": terminal,
            "reason": reason,
            "summary": summary,
            "target_window_title": str(activity.get("checkpoint_target", "") or activity.get("last_target_window", "")).strip(),
            "active_window_title": str(activity.get("active_window_title", "")).strip(),
            "scene_class": str(scene.get("scene_class", "")).strip(),
            "workflow_state": str(scene.get("workflow_state", "")).strip(),
            "readiness_state": str(scene.get("readiness_state", "")).strip(),
            "evidence_state": str(assessment.get("state", "")).strip(),
            "evidence_reason": str(assessment.get("reason", "")).strip(),
            "recovery_state": str(recovery.get("state", "")).strip(),
            "recovery_reason": str(recovery.get("reason", "")).strip(),
            "recovery_strategy": str(recovery.get("strategy", "")).strip(),
            "attempt_count": int(recovery.get("attempt_count", 0) or 0),
            "max_attempts": int(recovery.get("max_attempts", 0) or 0),
            "scene_changed": bool(scene.get("scene_changed", False)),
            "checkpoint_pending": bool(activity.get("checkpoint_pending", False)),
            "evidence_id": str(activity.get("checkpoint_evidence_id", "") or activity.get("evidence_id", "")).strip(),
            "timestamp": str(activity.get("evidence_timestamp", "") or activity.get("observed_at", "")).strip(),
        }
    )


def _desktop_terminal_outcome(task_state, planner_goal: str) -> dict:
    if not _goal_is_desktop_related(planner_goal, task_state):
        return {}

    activity = _desktop_activity_snapshot(task_state)
    if not activity or bool(activity.get("checkpoint_pending", False)):
        return {}

    recovery = activity.get("latest_recovery", {}) if isinstance(activity.get("latest_recovery", {}), dict) else {}
    process_context = activity.get("latest_process_context", {}) if isinstance(activity.get("latest_process_context", {}), dict) else {}
    scene = activity.get("selected_scene", {}) if isinstance(activity.get("selected_scene", {}), dict) else {}
    assessment = activity.get("selected_evidence_assessment", {}) if isinstance(activity.get("selected_evidence_assessment", {}), dict) else {}
    recovery_state = str(recovery.get("state", "")).strip().lower()
    recovery_reason = str(recovery.get("reason", "")).strip().lower()
    scene_class = str(scene.get("scene_class", "")).strip().lower()
    workflow_state = str(scene.get("workflow_state", "")).strip().lower()
    attempt_count = int(recovery.get("attempt_count", 0) or 0)
    max_attempts = int(recovery.get("max_attempts", 0) or 0)
    exhausted = max_attempts > 0 and attempt_count >= max_attempts and recovery_state in {"missing", "needs_recovery", "waiting"}
    background_candidate = bool(process_context.get("background_candidate", False) or process_context.get("running", False))
    summary = str(recovery.get("summary", "") or scene.get("summary", "") or assessment.get("summary", "")).strip()

    if recovery_reason == "target_withdrawn" and recovery_state == "missing":
        return _desktop_build_run_outcome(
            task_state,
            outcome="unrecoverable_withdrawn",
            status="incomplete",
            terminal=True,
            reason="unrecoverable_withdrawn",
            summary=summary or "The target window appears withdrawn or tray-like and is not visibly recoverable in the current bounded desktop pass.",
        )

    if recovery_reason in {"tray_or_background_state"} and recovery_state == "missing":
        return _desktop_build_run_outcome(
            task_state,
            outcome="unrecoverable_tray_background",
            status="incomplete",
            terminal=True,
            reason="unrecoverable_tray_background",
            summary=summary or "The target window is not visibly surfaced and appears to be only in the tray or background.",
        )

    if recovery_reason == "target_not_found" and recovery_state == "missing":
        return _desktop_build_run_outcome(
            task_state,
            outcome="unrecoverable_tray_background" if background_candidate else "unrecoverable_missing_target",
            status="incomplete",
            terminal=True,
            reason="unrecoverable_tray_background" if background_candidate else "unrecoverable_missing_target",
            summary=summary or (
                "The target process still appears alive, but the target window is not visibly surfaced."
                if background_candidate
                else "The target window is not visibly present and could not be found through the bounded desktop path."
            ),
        )

    if exhausted or (
        recovery_state in {"missing", "needs_recovery", "waiting"}
        and recovery_reason in {"foreground_not_confirmed", "target_hidden", "target_minimized", "target_mismatch", "target_loading", "target_not_ready", "visual_state_unstable"}
        and max_attempts > 0
        and attempt_count >= max_attempts
    ):
        return _desktop_build_run_outcome(
            task_state,
            outcome="recovery_exhausted",
            status="incomplete",
            terminal=True,
            reason="recovery_exhausted",
            summary=summary or "The bounded desktop recovery budget is exhausted, so the operator should stop and report the current window state.",
        )

    if recovery_state == "missing" and scene_class == "background" and workflow_state in {"recovering", "blocked", "attention_needed"}:
        return _desktop_build_run_outcome(
            task_state,
            outcome="unrecoverable_tray_background",
            status="incomplete",
            terminal=True,
            reason="unrecoverable_tray_background",
            summary=summary or "The target remains background-like and not visibly recoverable in the current bounded desktop pass.",
        )

    return {}


def _desktop_goal_mentions_changed_state(planner_goal: str) -> bool:
    try:
        from core.desktop_evidence import _desktop_changed_state_goal

        return _desktop_changed_state_goal(str(planner_goal or "").strip().lower())
    except Exception:
        text = str(planner_goal or "").strip().lower()
        return any(term in text for term in {"changed", "before", "after", "compare", "what happened"})


def _desktop_active_target_proposals(task_state) -> dict:
    desktop_activity = task_state._collect_desktop_activity(limit=4)
    checkpoint = desktop_activity.get("checkpoint_target_proposals", {}) if isinstance(desktop_activity.get("checkpoint_target_proposals", {}), dict) else {}
    selected = desktop_activity.get("selected_target_proposals", {}) if isinstance(desktop_activity.get("selected_target_proposals", {}), dict) else {}
    if getattr(task_state, "desktop_checkpoint_pending", False) and checkpoint.get("proposal_count", 0):
        return checkpoint
    return checkpoint if checkpoint.get("state") == "approval_context" and checkpoint.get("proposal_count", 0) else selected


def _desktop_action_signature(tool_name: str, args: dict, proposal_context: dict) -> str:
    normalized_args = args if isinstance(args, dict) else {}
    top_proposals = proposal_context.get("proposals", []) if isinstance(proposal_context.get("proposals", []), list) else []
    top_targets = "|".join(
        str(item.get("target_id", "")).strip()
        for item in top_proposals[:2]
        if isinstance(item, dict) and str(item.get("target_id", "")).strip()
    )
    salient = [
        str(normalized_args.get("x", "")).strip(),
        str(normalized_args.get("y", "")).strip(),
        str(normalized_args.get("direction", "")).strip(),
        str(normalized_args.get("scroll_units", "")).strip(),
        str(normalized_args.get("key", "")).strip(),
        ",".join(str(item).strip() for item in normalized_args.get("modifiers", []) if str(item).strip()) if isinstance(normalized_args.get("modifiers", []), list) else "",
        str(normalized_args.get("value", "")).strip(),
        str(normalized_args.get("field_label", "")).strip(),
        str(normalized_args.get("title", "")).strip(),
        str(normalized_args.get("window_id", "")).strip(),
        str(proposal_context.get("state", "")).strip(),
        str(proposal_context.get("reason", "")).strip(),
        str(top_targets).strip(),
    ]
    return "::".join([tool_name, *salient])


def _proposal_supports_desktop_action(proposal: dict, tool_name: str) -> bool:
    if not isinstance(proposal, dict):
        return False
    suggested = [str(item).strip() for item in list(proposal.get("suggested_next_actions", [])) if str(item).strip()]
    if tool_name in suggested:
        return True
    target_kind = str(proposal.get("target_kind", "")).strip().lower()
    if tool_name == "desktop_focus_window" and target_kind in {"focus_candidate", "recovery_candidate", "window", "ui_like_area"}:
        return True
    return False


def _desktop_target_explicitly_approved(tool_runtime, task_state, planner_goal: str, args: dict) -> bool:
    if tool_runtime.goal_has_explicit_desktop_approval(planner_goal):
        return True
    if str(getattr(task_state, "desktop_checkpoint_approval_status", "")).strip().lower() == "approved":
        return True
    return str((args or {}).get("approval_status", "")).strip().lower() == "approved"


def _desktop_action_guard_result(task_state, tool_name: str, proposal_context: dict, *, kind: str, summary: str, terminal: bool = False) -> dict:
    latest_recovery = _desktop_latest_recovery(task_state)
    latest_readiness = _desktop_latest_window_readiness(task_state)
    latest_scene = _desktop_selected_scene(task_state)
    result = {
        "ok": False,
        "error": summary,
        "summary": summary,
        "proposal_guard": {
            "kind": kind,
            "terminal": bool(terminal),
            "tool": tool_name,
            "proposal_state": str(proposal_context.get("state", "")).strip(),
            "proposal_reason": str(proposal_context.get("reason", "")).strip(),
            "proposal_count": int(proposal_context.get("proposal_count", 0) or 0),
        },
    }
    if latest_recovery:
        result["recovery"] = latest_recovery
    if latest_readiness:
        result["window_readiness"] = latest_readiness
    if latest_scene:
        result["scene"] = latest_scene
    return result


def _desktop_action_repeat_guard(task_state, tool_name: str, args: dict, proposal_context: dict) -> bool:
    signature = _desktop_action_signature(tool_name, args, proposal_context)
    matching_failures = 0
    for step in reversed(task_state.steps[-6:]):
        if step.get("type") != "tool":
            continue
        if str(step.get("tool", "")).strip() != tool_name:
            continue
        previous_context = step.get("target_proposals", {}) if isinstance(step.get("target_proposals", {}), dict) else {}
        previous_args = step.get("args", {}) if isinstance(step.get("args", {}), dict) else {}
        if _desktop_action_signature(tool_name, previous_args, previous_context) != signature:
            continue
        if str(step.get("status", "")).strip() not in {"failed", "paused"}:
            continue
        matching_failures += 1
        if matching_failures >= 1:
            return True
    return False


def _maybe_guard_desktop_action(tool_runtime, task_state, planner_goal: str, tool_name: str, args: dict) -> dict | None:
    if tool_name not in _DESKTOP_MUTATING_TOOLS:
        return None

    proposal_context = _desktop_active_target_proposals(task_state)
    proposal_state = str(proposal_context.get("state", "")).strip().lower()
    explicit_approval = _desktop_target_explicitly_approved(tool_runtime, task_state, planner_goal, args)
    supporting = [
        item
        for item in list(proposal_context.get("proposals", []))
        if isinstance(item, dict) and _proposal_supports_desktop_action(item, tool_name)
    ]
    best_score = max((int(item.get("confidence_score", 0) or 0) for item in supporting), default=0)

    if proposal_state == "no_safe_target":
        summary = str(proposal_context.get("summary", "")).strip() or "There is no safe visible desktop target to act on yet."
        return _desktop_action_guard_result(task_state, tool_name, proposal_context, kind="no_safe_target", summary=summary, terminal=True)

    if _desktop_action_repeat_guard(task_state, tool_name, args, proposal_context):
        return _desktop_action_guard_result(
            task_state,
            tool_name,
            proposal_context,
            kind="unchanged_target_proposal",
            summary="Stopped retrying the same bounded desktop action because the target proposal and evidence have not changed meaningfully.",
            terminal=True,
        )

    if tool_name in _DESKTOP_PROPOSAL_APPROVAL_CONTROLLED_TOOLS and not explicit_approval:
        return None

    if supporting and best_score >= 82:
        return None
    if explicit_approval:
        return None

    summary = str(proposal_context.get("summary", "")).strip() or "The current desktop target proposals are not confident enough to execute that action without explicit approval."
    return _desktop_action_guard_result(task_state, tool_name, proposal_context, kind="low_confidence_target", summary=summary, terminal=False)


def _maybe_finalize_desktop_action_guard(llm, task_state, result: dict, *, session_store=None, progress_callback=None):
    guard = result.get("proposal_guard", {}) if isinstance(result.get("proposal_guard", {}), dict) else {}
    if not guard:
        return None
    if not bool(guard.get("terminal", False)):
        return None
    summary = str(result.get("summary", "") or result.get("error", "")).strip() or "Stopped the bounded desktop action because the target remained unchanged or unsafe."
    outcome = _desktop_build_run_outcome(
        task_state,
        outcome="blocked" if str(guard.get("kind", "")).strip() == "no_safe_target" else "incomplete",
        status="blocked" if str(guard.get("kind", "")).strip() == "no_safe_target" else "incomplete",
        terminal=True,
        reason=str(guard.get("kind", "")).strip() or "desktop_action_guard",
        summary=summary,
    )
    return _finalize_desktop_run_outcome(
        llm,
        task_state,
        outcome,
        session_store=session_store,
        progress_callback=progress_callback,
    )


def _maybe_pace_desktop_action(task_state, tool_name: str):
    now = time.monotonic()
    last_attempt = float(getattr(task_state, "_desktop_last_mutation_attempt_at", 0.0) or 0.0)
    last_tool = str(getattr(task_state, "_desktop_last_mutation_tool", "")).strip()
    setattr(task_state, "_desktop_last_mutation_attempt_at", now)
    setattr(task_state, "_desktop_last_mutation_tool", tool_name)
    if last_attempt <= 0:
        return
    delta = now - last_attempt
    if delta >= _DESKTOP_ACTION_PACING_WINDOW_SECONDS:
        return
    if last_tool and last_tool != tool_name:
        time.sleep(min(_DESKTOP_ACTION_PACING_SECONDS * 0.5, 0.05))
        return
    time.sleep(_DESKTOP_ACTION_PACING_SECONDS)


def _is_redundant_desktop_observation(task_state, tool_name, planner_goal: str) -> bool:
    if tool_name not in _DESKTOP_INSPECT_TOOLS:
        return False
    if getattr(task_state, "desktop_checkpoint_pending", False):
        return False
    if not task_state.steps:
        return False
    last_step = task_state.steps[-1]
    if last_step.get("type") != "tool" or last_step.get("status") != "completed":
        return False
    if str(last_step.get("tool", "")).strip() != tool_name:
        return False

    require_screenshot = tool_name == "desktop_capture_screenshot"
    assessment = _desktop_evidence_assessment(
        task_state,
        purpose="desktop_investigation",
        target_window_title=_goal_desktop_window_title(planner_goal),
        require_screenshot=require_screenshot,
    )
    if not assessment.get("sufficient", False):
        return False
    selected_scene = _desktop_selected_scene(task_state)
    if bool(selected_scene.get("scene_changed", False)) and _desktop_goal_mentions_changed_state(planner_goal):
        return False
    if str(selected_scene.get("reason", "")).strip().lower() in {"scene_ambiguous", "loading_scene", "blocked_scene"}:
        return False
    if require_screenshot and not str(getattr(task_state, "desktop_last_screenshot_path", "")).strip():
        return False
    return True


def _maybe_prepare_desktop_recovery_context(
    llm,
    tool_runtime,
    task_state,
    planner_goal: str,
    *,
    require_screenshot: bool = False,
    session_store=None,
    progress_callback=None,
):
    seed_args = _desktop_target_seed_args(task_state, planner_goal)
    if not seed_args.get("title") and not seed_args.get("window_id"):
        return None

    latest_recovery = _desktop_latest_recovery(task_state)
    recovery_state = str(latest_recovery.get("state", "")).strip().lower()
    executed_any = False

    if recovery_state not in {"ready", "needs_recovery", "waiting", "missing"}:
        _, inspect_result = _execute_desktop_tool_step(
            tool_runtime,
            task_state,
            "desktop_inspect_window_state",
            seed_args,
            planner_goal,
            session_store=session_store,
            progress_callback=progress_callback,
        )
        executed_any = True
        latest_recovery = inspect_result.get("recovery", {}) if isinstance(inspect_result.get("recovery", {}), dict) else {}
        recovery_state = str(latest_recovery.get("state", "")).strip().lower()

    if recovery_state == "needs_recovery":
        _, recover_result = _execute_desktop_tool_step(
            tool_runtime,
            task_state,
            "desktop_recover_window",
            seed_args,
            planner_goal,
            session_store=session_store,
            progress_callback=progress_callback,
        )
        executed_any = True
        latest_recovery = recover_result.get("recovery", {}) if isinstance(recover_result.get("recovery", {}), dict) else {}
        recovery_state = str(latest_recovery.get("state", "")).strip().lower()

    if recovery_state == "waiting":
        _, waited_result = _execute_desktop_tool_step(
            tool_runtime,
            task_state,
            "desktop_wait_for_window_ready",
            seed_args,
            planner_goal,
            session_store=session_store,
            progress_callback=progress_callback,
        )
        executed_any = True
        latest_recovery = waited_result.get("recovery", {}) if isinstance(waited_result.get("recovery", {}), dict) else {}
        recovery_state = str(latest_recovery.get("state", "")).strip().lower()

    if recovery_state != "ready":
        return None

    selected_assessment = _desktop_evidence_assessment(
        task_state,
        purpose="desktop_action_prepare",
        target_window_title=_goal_desktop_window_title(planner_goal),
        require_screenshot=require_screenshot,
    )
    selected_scene = _desktop_selected_scene(task_state)
    if str(selected_scene.get("readiness_state", "")).strip().lower() in {"loading", "not_ready", "unstable"}:
        _, waited_result = _execute_desktop_tool_step(
            tool_runtime,
            task_state,
            "desktop_wait_for_window_ready",
            seed_args,
            planner_goal,
            session_store=session_store,
            progress_callback=progress_callback,
        )
        executed_any = True
        latest_recovery = waited_result.get("recovery", {}) if isinstance(waited_result.get("recovery", {}), dict) else latest_recovery
        recovery_state = str(latest_recovery.get("state", "")).strip().lower()
        if recovery_state != "ready":
            return None
        selected_assessment = _desktop_evidence_assessment(
            task_state,
            purpose="desktop_action_prepare",
            target_window_title=_goal_desktop_window_title(planner_goal),
            require_screenshot=require_screenshot,
        )
    if not selected_assessment.get("sufficient", False):
        _, capture_result = _execute_desktop_tool_step(
            tool_runtime,
            task_state,
            "desktop_capture_screenshot",
            {"scope": "active_window"},
            planner_goal,
            session_store=session_store,
            progress_callback=progress_callback,
        )
        executed_any = True
        if not capture_result.get("ok", False):
            return None

    if executed_any:
        return {"continue_loop": True}
    return None


def _finalize_redundant_desktop_observation(llm, task_state, tool_name, session_store=None, progress_callback=None):
    note = f"Stopped repeating {tool_name} after the current desktop evidence was already sufficient and finalized from the latest desktop observation."
    return _finalize_guarded_completion(
        llm,
        task_state,
        note,
        tool_name=tool_name,
        args={},
        session_store=session_store,
        progress_callback=progress_callback,
    )


def _finalize_desktop_run_outcome(llm, task_state, outcome: dict, *, session_store=None, progress_callback=None):
    if not isinstance(outcome, dict) or not str(outcome.get("outcome", "")).strip():
        return None
    task_state.set_desktop_run_outcome(outcome)
    note = str(outcome.get("summary", "")).strip() or "Desktop run ended."
    status = str(outcome.get("status", "")).strip() or "incomplete"
    task_state.add_step(
        {
            "type": "system",
            "status": status,
            "message": note,
            "tool": "desktop_run_outcome",
            "result": {"desktop_run_outcome": dict(outcome)},
        }
    )
    task_state.add_note(note)
    recent_notes = task_state.memory_notes[-6:]
    if recent_notes:
        task_state.set_summary(" | ".join(recent_notes))
    task_state.status = status
    _persist_session_state(session_store, task_state)
    return {
        "ok": status == "completed",
        "status": status,
        "message": _finalize_message(llm, task_state, progress_callback=progress_callback),
        "steps": task_state.steps,
        "desktop_run_outcome": dict(outcome),
    }


def _maybe_finalize_desktop_terminal_outcome(llm, task_state, planner_goal: str, *, session_store=None, progress_callback=None):
    outcome = _desktop_terminal_outcome(task_state, planner_goal)
    if not outcome.get("terminal", False):
        return None
    return _finalize_desktop_run_outcome(
        llm,
        task_state,
        outcome,
        session_store=session_store,
        progress_callback=progress_callback,
    )


def _finalize_synthesized_desktop_pause(
    llm,
    task_state,
    *,
    tool_name: str,
    checkpoint_reason: str,
    checkpoint_target: str,
    resume_args: dict,
    note: str,
    session_store=None,
    progress_callback=None,
):
    evidence_id = str(getattr(task_state, "desktop_last_evidence_id", "")).strip()
    if resume_args.get("x") is not None and resume_args.get("y") is not None:
        task_state.desktop_last_point = f"({resume_args.get('x')}, {resume_args.get('y')})"[:80]
    key_name = str(resume_args.get("key", "")).strip()
    modifiers = resume_args.get("modifiers", []) if isinstance(resume_args.get("modifiers", []), list) else []
    repeat = int(resume_args.get("repeat", 1) or 1)
    if key_name:
        modifier_prefix = "+".join(str(part).title() for part in modifiers)
        normalized_key = key_name.title()
        key_preview = f"{modifier_prefix}+{normalized_key}" if modifier_prefix else normalized_key
        if repeat > 1:
            key_preview = f"{key_preview} x{repeat}"
        task_state.desktop_last_key_sequence = key_preview[:80]
    task_state.set_desktop_checkpoint(
        reason=checkpoint_reason,
        tool=tool_name,
        target=checkpoint_target,
        evidence_id=evidence_id,
        approval_status="not approved",
        resume_args=resume_args,
    )
    task_state.set_desktop_run_outcome(
        _desktop_build_run_outcome(
            task_state,
            outcome="approval_needed",
            status="paused",
            terminal=False,
            reason="approval_needed",
            summary=note,
        )
    )
    task_state.status = "paused"
    task_state.desktop_last_action = note[:220]
    task_state.add_step(
        {
            "type": "system",
            "status": "paused",
            "message": note,
            "tool": tool_name,
            "args": dict(resume_args),
            "result": {
                "paused": True,
                "approval_required": True,
                "approval_status": "not approved",
                "checkpoint_required": True,
                "checkpoint_reason": checkpoint_reason,
                "checkpoint_tool": tool_name,
                "checkpoint_target": checkpoint_target,
                "checkpoint_resume_args": dict(resume_args),
                "summary": note,
            },
        }
    )
    task_state.add_note(note)
    recent_notes = task_state.memory_notes[-6:]
    if recent_notes:
        task_state.set_summary(" | ".join(recent_notes))
    _persist_session_state(session_store, task_state)
    return {
        "ok": False,
        "status": "paused",
        "message": _finalize_message(llm, task_state, progress_callback=progress_callback),
        "steps": task_state.steps,
    }


def _extract_browser_goal_url(text: str) -> str:
    match = _BROWSER_URL_PATTERN.search(str(text or ""))
    return match.group(0).strip() if match else ""


def _goal_requests_browser_bootstrap(goal: str) -> bool:
    lowered = str(goal or "").strip().lower()
    if not lowered:
        return False
    return any(term in lowered for term in _BROWSER_BOOTSTRAP_TERMS)


def _maybe_bootstrap_browser_open(tool_runtime, task_state, planner_goal: str, session_store=None) -> bool:
    if not tool_runtime.has_tool("browser_open_page"):
        return False
    if getattr(task_state, "browser_current_url", ""):
        return False
    if _has_browser_step(task_state):
        return False
    if not _goal_requests_browser_bootstrap(planner_goal):
        return False

    goal_url = _extract_browser_goal_url(planner_goal)
    if not goal_url:
        return False

    args = tool_runtime.prepare_args("browser_open_page", {"url": goal_url}, task_state, planning_goal=planner_goal)
    before_context = capture_action_context(task_state, "browser_open_page", args)
    result = tool_runtime.execute("browser_open_page", args)
    _record_tool_result(task_state, "browser_open_page", args, result, before_context=before_context)
    _persist_session_state(session_store, task_state)
    return True


def _next_browser_checkpoint_step(task_state) -> str:
    return str(
        getattr(task_state, "browser_task_next_step", "")
        or getattr(task_state, "browser_workflow_next_step", "")
    ).strip()


def _goal_requests_preclick_pause(goal: str) -> bool:
    lowered = str(goal or "").strip().lower()
    if not lowered:
        return False
    return any(term in lowered for term in _BROWSER_CHECKPOINT_PAUSE_TERMS)


def _infer_browser_checkpoint_target(task_state, planner_goal: str) -> str:
    combined = " ".join(
        part
        for part in (
            _next_browser_checkpoint_step(task_state),
            getattr(task_state, "browser_last_text_excerpt", ""),
            getattr(task_state, "browser_current_title", ""),
            planner_goal,
        )
        if str(part).strip()
    ).lower()
    if "submit" in combined:
        return "Submit"
    if "confirm" in combined:
        return "Confirm"
    return str(getattr(task_state, "browser_current_title", "")).strip()[:160]


def _maybe_pause_for_browser_checkpoint(llm, tool_runtime, task_state, planner_goal: str, session_store=None):
    if getattr(task_state, "browser_checkpoint_pending", False):
        return None
    if tool_runtime.goal_has_explicit_browser_approval(planner_goal):
        return None
    if not getattr(task_state, "browser_current_url", ""):
        return None

    next_step = _next_browser_checkpoint_step(task_state)
    if not next_step:
        return None

    next_step_lower = next_step.lower()
    if "pause before" not in next_step_lower and "click" not in next_step_lower and "submit" not in next_step_lower:
        return None

    combined = " ".join([next_step, planner_goal]).lower()
    if "submit" not in combined and "click" not in combined:
        return None
    if not _goal_requests_preclick_pause(planner_goal) and "pause before" not in next_step.lower():
        return None

    checkpoint_step = next_step[:120]
    checkpoint_target = _infer_browser_checkpoint_target(task_state, planner_goal)
    checkpoint_reason = (
        "Reached the requested pause point before the submit-like click. "
        "Explicit approval is still required before continuing that browser action."
    )
    resume_args = {}
    if getattr(task_state, "browser_current_url", ""):
        resume_args["url"] = str(task_state.browser_current_url).strip()
    if checkpoint_target:
        resume_args["text"] = checkpoint_target
    if getattr(task_state, "browser_current_title", ""):
        resume_args["expected_title_contains"] = str(task_state.browser_current_title).strip()[:120]
    if getattr(task_state, "browser_last_successful_tool", "") == "browser_type":
        last_args = task_state._latest_step_args()
        for key in ("label", "text", "selector", "placeholder", "name", "name_attr", "value"):
            value = str(last_args.get(key, "")).strip()
            if value:
                resume_args[f"resume_{key}"] = value

    task_state.set_browser_checkpoint(
        reason=checkpoint_reason,
        step=checkpoint_step,
        tool="browser_click",
        target=checkpoint_target,
        approval_status="not approved",
        resume_args=resume_args,
    )
    task_state.status = "paused"

    note = f"Paused before {checkpoint_step or 'the next browser action'} pending explicit approval."
    task_state.browser_last_action = note[:220]
    task_state.add_step(
        {
            "type": "system",
            "status": "paused",
            "message": note,
            "tool": "browser_click",
            "args": resume_args,
            "result": {
                "paused": True,
                "approval_required": True,
                "approval_status": "not approved",
                "checkpoint_required": True,
                "checkpoint_reason": checkpoint_reason,
                "checkpoint_step": checkpoint_step,
                "checkpoint_tool": "browser_click",
                "checkpoint_target": checkpoint_target,
                "checkpoint_resume_args": resume_args,
                "summary": note,
            },
        }
    )
    task_state.add_note(note)
    recent_notes = task_state.memory_notes[-6:]
    if recent_notes:
        task_state.set_summary(" | ".join(recent_notes))
    _persist_session_state(session_store, task_state)
    return {
        "ok": False,
        "status": "paused",
        "message": _finalize_message(llm, task_state),
        "steps": task_state.steps,
    }


def _maybe_pause_for_desktop_action(llm, tool_runtime, task_state, planner_goal: str, session_store=None, *, allow_recovery: bool = True, progress_callback=None):
    if getattr(task_state, "desktop_checkpoint_pending", False):
        return None
    if tool_runtime.goal_has_explicit_desktop_approval(planner_goal):
        return None
    if not getattr(task_state, "desktop_observation_token", ""):
        return None

    click_point = _goal_desktop_click_point(planner_goal)
    key_request = _goal_desktop_key_request(planner_goal)
    type_request = _goal_desktop_type_request(planner_goal)
    if click_point is None and key_request is None and type_request is None:
        return None

    latest_recovery_state = str(_desktop_latest_recovery(task_state).get("state", "")).strip().lower()
    should_attempt_recovery = (not _desktop_target_window_ready(task_state, planner_goal)) or latest_recovery_state in {
        "needs_recovery",
        "waiting",
        "missing",
    }
    if (
        allow_recovery
        and should_attempt_recovery
        and callable(getattr(tool_runtime, "prepare_args", None))
        and callable(getattr(tool_runtime, "execute", None))
    ):
        recovery_prepared = _maybe_prepare_desktop_recovery_context(
            llm,
            tool_runtime,
            task_state,
            planner_goal,
            require_screenshot=bool(click_point),
            session_store=session_store,
            progress_callback=progress_callback,
        )
        if recovery_prepared is not None and recovery_prepared.get("continue_loop", False):
            return _maybe_pause_for_desktop_action(
                llm,
                tool_runtime,
                task_state,
                planner_goal,
                session_store=session_store,
                allow_recovery=False,
                progress_callback=progress_callback,
            )

    if _goal_mentions_desktop_focus(planner_goal) and not _desktop_has_completed_focus_context(task_state):
        return None
    if _goal_mentions_desktop_screenshot(planner_goal) and not _has_completed_tool(task_state, "desktop_capture_screenshot"):
        return None
    if not _desktop_has_inspection_context(task_state):
        return None
    if not _desktop_target_window_ready(task_state, planner_goal):
        return None

    if click_point and not _has_completed_or_paused_tool_step(task_state, "desktop_click_point"):
        desktop_activity = task_state._collect_desktop_activity(limit=4)
        selected_assessment = _desktop_evidence_assessment(
            task_state,
            purpose="desktop_action_prepare",
            target_window_title=_goal_desktop_window_title(planner_goal),
            require_screenshot=True,
        )
        if not selected_assessment.get("sufficient", False):
            if not (
                str(selected_assessment.get("reason", "")).strip().lower() == "partial_evidence"
                and _desktop_partial_evidence_allows_checkpoint(
                    task_state,
                    planner_goal,
                    require_screenshot=True,
                )
            ):
                return None
            selected_assessment = {
                **selected_assessment,
                "state": "partial",
                "sufficient": True,
                "needs_refresh": False,
                "reason": "partial_but_answerable",
                "summary": "Current desktop evidence is partial but approval-ready for a bounded desktop checkpoint.",
            }
        if not selected_assessment.get("sufficient", False):
            return None
        x, y = click_point
        evidence_summary = str(desktop_activity.get("selected_evidence", {}).get("summary", "")).strip()
        checkpoint_reason = (
            f"Ready to click the known visible button center at ({x}, {y}) in the active window "
            f"'{getattr(task_state, 'desktop_active_window_title', 'the active window')}'. "
            "Awaiting explicit user approval before performing the real desktop click."
        )
        if evidence_summary:
            checkpoint_reason += f" Evidence basis: {evidence_summary}"
        checkpoint_target = f"{getattr(task_state, 'desktop_active_window_title', 'desktop target')} @ ({x}, {y})"
        resume_args = {
            "x": x,
            "y": y,
            "observation_token": str(getattr(task_state, "desktop_observation_token", "")).strip(),
            "expected_window_title": str(getattr(task_state, "desktop_active_window_title", "")).strip(),
            "expected_window_id": str(getattr(task_state, "desktop_active_window_id", "")).strip(),
            "checkpoint_reason": checkpoint_reason,
        }
        note = f"Paused before desktop click at ({x}, {y}) pending explicit approval."
        return _finalize_synthesized_desktop_pause(
            llm,
            task_state,
            tool_name="desktop_click_point",
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=checkpoint_target,
            resume_args=resume_args,
            note=note,
            session_store=session_store,
            progress_callback=progress_callback,
        )

    if key_request and not _has_completed_or_paused_tool_step(task_state, "desktop_press_key"):
        desktop_activity = task_state._collect_desktop_activity(limit=4)
        selected_assessment = _desktop_evidence_assessment(
            task_state,
            purpose="desktop_action_prepare",
            target_window_title=_goal_desktop_window_title(planner_goal),
            require_screenshot=False,
        )
        if not selected_assessment.get("sufficient", False):
            if not (
                str(selected_assessment.get("reason", "")).strip().lower() == "partial_evidence"
                and _desktop_partial_evidence_allows_checkpoint(
                    task_state,
                    planner_goal,
                    require_screenshot=False,
                )
            ):
                return None
            selected_assessment = {
                **selected_assessment,
                "state": "partial",
                "sufficient": True,
                "needs_refresh": False,
                "reason": "partial_but_answerable",
                "summary": "Current desktop evidence is partial but approval-ready for a bounded desktop keyboard checkpoint.",
            }
        if not selected_assessment.get("sufficient", False):
            return None
        key = str(key_request.get("key", "")).strip()
        modifiers = key_request.get("modifiers", []) if isinstance(key_request.get("modifiers", []), list) else []
        repeat = int(key_request.get("repeat", 1) or 1)
        modifier_text = "+".join(part.title() for part in modifiers)
        key_preview = f"{modifier_text}+{key.title()}" if modifier_text else key.title()
        if repeat > 1:
            key_preview = f"{key_preview} x{repeat}"
        evidence_summary = str(desktop_activity.get("selected_evidence", {}).get("summary", "")).strip()
        checkpoint_reason = (
            f"Ready to press {key_preview} in the active window "
            f"'{getattr(task_state, 'desktop_active_window_title', 'the active window')}'. "
            "Awaiting explicit user approval before performing the real desktop key press."
        )
        if evidence_summary:
            checkpoint_reason += f" Evidence basis: {evidence_summary}"
        checkpoint_target = f"{getattr(task_state, 'desktop_active_window_title', 'desktop target')} :: {key_preview}"
        resume_args = {
            "key": key,
            "modifiers": modifiers,
            "repeat": repeat,
            "observation_token": str(getattr(task_state, "desktop_observation_token", "")).strip(),
            "expected_window_title": str(getattr(task_state, "desktop_active_window_title", "")).strip(),
            "expected_window_id": str(getattr(task_state, "desktop_active_window_id", "")).strip(),
            "checkpoint_reason": checkpoint_reason,
        }
        note = f"Paused before desktop key press {key_preview} pending explicit approval."
        return _finalize_synthesized_desktop_pause(
            llm,
            task_state,
            tool_name="desktop_press_key",
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=checkpoint_target,
            resume_args=resume_args,
            note=note,
            session_store=session_store,
            progress_callback=progress_callback,
        )

    if type_request and not _has_completed_or_paused_tool_step(task_state, "desktop_type_text"):
        desktop_activity = task_state._collect_desktop_activity(limit=4)
        selected_assessment = _desktop_evidence_assessment(
            task_state,
            purpose="desktop_action_prepare",
            target_window_title=_goal_desktop_window_title(planner_goal),
            require_screenshot=False,
        )
        if not selected_assessment.get("sufficient", False):
            if not (
                str(selected_assessment.get("reason", "")).strip().lower() == "partial_evidence"
                and _desktop_partial_evidence_allows_checkpoint(
                    task_state,
                    planner_goal,
                    require_screenshot=False,
                )
            ):
                return None
            selected_assessment = {
                **selected_assessment,
                "state": "partial",
                "sufficient": True,
                "needs_refresh": False,
                "reason": "partial_but_answerable",
                "summary": "Current desktop evidence is partial but approval-ready for a bounded desktop checkpoint.",
            }
        if not selected_assessment.get("sufficient", False):
            return None
        value = str(type_request.get("value", "")).strip()
        field_label = str(type_request.get("field_label", "")).strip()
        evidence_summary = str(desktop_activity.get("selected_evidence", {}).get("summary", "")).strip()
        checkpoint_reason = (
            f"Ready to type bounded text into '{field_label}' in the active window "
            f"'{getattr(task_state, 'desktop_active_window_title', 'the active window')}'. "
            "Awaiting explicit user approval before performing the real desktop typing action."
        )
        if evidence_summary:
            checkpoint_reason += f" Evidence basis: {evidence_summary}"
        checkpoint_target = f"{field_label} in {getattr(task_state, 'desktop_active_window_title', 'the active window')}"
        resume_args = {
            "value": value,
            "field_label": field_label,
            "observation_token": str(getattr(task_state, "desktop_observation_token", "")).strip(),
            "expected_window_title": str(getattr(task_state, "desktop_active_window_title", "")).strip(),
            "expected_window_id": str(getattr(task_state, "desktop_active_window_id", "")).strip(),
            "checkpoint_reason": checkpoint_reason,
        }
        note = f"Paused before desktop typing into '{field_label}' pending explicit approval."
        return _finalize_synthesized_desktop_pause(
            llm,
            task_state,
            tool_name="desktop_type_text",
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=checkpoint_target,
            resume_args=resume_args,
            note=note,
            session_store=session_store,
            progress_callback=progress_callback,
        )
    return None


def _maybe_recover_desktop_action_failure(
    llm,
    tool_runtime,
    task_state,
    planner_goal: str,
    tool_name: str,
    result,
    session_store=None,
    progress_callback=None,
):
    if tool_name not in {
        "desktop_inspect_window_state",
        "desktop_focus_window",
        "desktop_move_mouse",
        "desktop_hover_point",
        "desktop_click_mouse",
        "desktop_click_point",
        "desktop_scroll",
        "desktop_press_key",
        "desktop_press_key_sequence",
        "desktop_type_text",
    }:
        return None
    if not isinstance(result, dict) or result.get("ok", False) or result.get("paused", False):
        return None

    detail_text = " ".join(
        str(result.get(key, "")).strip().lower()
        for key in ("error", "summary", "message")
        if str(result.get(key, "")).strip()
    )
    recovery_view = result.get("recovery", {}) if isinstance(result.get("recovery", {}), dict) else {}
    recovery_state = str(recovery_view.get("state", "")).strip().lower()
    refresh_needed = any(
        phrase in detail_text
        for phrase in (
            "fresh desktop observation is required before acting",
            "missing or expired",
            "capture a screenshot again",
            "inspect windows or capture a screenshot first",
            "focus the window and inspect desktop state again",
            "no longer active",
            "foreground",
            "not ready",
            "loading",
            "hidden",
            "minimized",
        )
    )
    recovery_needed = refresh_needed or recovery_state in {"needs_recovery", "waiting", "missing"}
    if not recovery_needed:
        return None

    recovery_progress = _maybe_prepare_desktop_recovery_context(
        llm,
        tool_runtime,
        task_state,
        planner_goal,
        require_screenshot=tool_name in {"desktop_click_mouse", "desktop_click_point", "desktop_scroll", "desktop_move_mouse", "desktop_hover_point"},
        session_store=session_store,
        progress_callback=progress_callback,
    )
    if recovery_progress is None and refresh_needed:
        refresh_tool = "desktop_capture_screenshot" if tool_name in {"desktop_click_mouse", "desktop_click_point", "desktop_scroll", "desktop_move_mouse", "desktop_hover_point"} else "desktop_get_active_window"
        refresh_seed_args = {"scope": "active_window"} if refresh_tool == "desktop_capture_screenshot" else {}
        refresh_args, refresh_result = _execute_desktop_tool_step(
            tool_runtime,
            task_state,
            refresh_tool,
            refresh_seed_args,
            planner_goal,
            session_store=session_store,
            progress_callback=progress_callback,
        )
        if refresh_result.get("ok", False):
            recovery_progress = {"continue_loop": True}
    if recovery_progress is None:
        return None

    synthesized_desktop_pause = _maybe_pause_for_desktop_action(
        llm,
        tool_runtime,
        task_state,
        planner_goal,
        session_store=session_store,
        allow_recovery=False,
    )
    if synthesized_desktop_pause is not None:
        return synthesized_desktop_pause

    if recovery_progress.get("continue_loop", False):
        return {"continue_loop": True}
    return None


def _maybe_resume_desktop_checkpoint(llm, tool_runtime, task_state, planner_goal: str, session_store=None, progress_callback=None):
    if not getattr(task_state, "desktop_checkpoint_pending", False):
        return None
    if not tool_runtime.goal_has_explicit_desktop_approval(planner_goal):
        return None

    tool_name = str(getattr(task_state, "desktop_checkpoint_tool", "")).strip()
    if tool_name not in {
        "desktop_move_mouse",
        "desktop_hover_point",
        "desktop_click_mouse",
        "desktop_click_point",
        "desktop_scroll",
        "desktop_press_key",
        "desktop_press_key_sequence",
        "desktop_type_text",
        "desktop_open_target",
        "desktop_start_process",
        "desktop_stop_process",
        "desktop_run_command",
    }:
        return None

    args = tool_runtime.prepare_args(tool_name, {}, task_state, planning_goal=planner_goal)
    before_context = capture_action_context(task_state, tool_name, args)
    result = tool_runtime.execute(tool_name, args)
    _record_tool_result(task_state, tool_name, args, result, before_context=before_context)
    _persist_session_state(session_store, task_state)

    if result.get("paused", False):
        task_state.set_desktop_run_outcome(
            _desktop_build_run_outcome(
                task_state,
                outcome="approval_needed",
                status="paused",
                terminal=False,
                reason="approval_needed",
                summary=str(result.get("summary", "") or result.get("checkpoint_reason", "") or "Desktop approval is still required.").strip(),
            )
        )
        task_state.status = "paused"
        _persist_session_state(session_store, task_state)
        return {
            "ok": False,
            "status": "paused",
            "message": _finalize_message(llm, task_state, progress_callback=progress_callback),
            "steps": task_state.steps,
        }

    if result.get("ok", False):
        task_state.set_desktop_run_outcome(
            _desktop_build_run_outcome(
                task_state,
                outcome="completed",
                status="completed",
                terminal=True,
                reason="completed",
                summary=str(result.get("summary", "") or result.get("message", "") or "Completed the approved bounded desktop step.").strip(),
            )
        )
        task_state.status = "completed"
        _persist_session_state(session_store, task_state)
        return {
            "ok": True,
            "status": "completed",
            "message": _finalize_message(llm, task_state, progress_callback=progress_callback),
            "steps": task_state.steps,
        }

    return None


def run_task_loop(
    llm,
    tools,
    task_state,
    settings,
    session_store=None,
    planning_goal: str | None = None,
    control_callback=None,
    progress_callback=None,
):
    tool_runtime = tools if isinstance(tools, ToolRuntime) else ToolRuntime(tools)
    max_iterations = int(settings.get("max_iterations", 12))
    planner_goal = str(planning_goal or task_state.goal).strip() or task_state.goal
    _emit_progress(progress_callback, "loop_entered", detail="Entered the bounded operator loop.")

    for _ in range(max_iterations):
        refresh_operator_intelligence_context(task_state)
        if callable(control_callback):
            control_result = _finalize_control_request(
                llm,
                task_state,
                control_callback(),
                session_store=session_store,
                progress_callback=progress_callback,
            )
            if control_result is not None:
                return control_result

        if stop_requested():
            task_state.status = "stopped"
            task_state.add_step(
                {
                    "type": "system",
                    "status": "stopped",
                    "message": "Emergency stop requested.",
                }
            )
            _persist_session_state(session_store, task_state)
            return {
                "ok": False,
                "status": "stopped",
                "steps": task_state.steps,
            }

        resumed_desktop_action = _maybe_resume_desktop_checkpoint(
            llm,
            tool_runtime,
            task_state,
            planner_goal,
            session_store=session_store,
            progress_callback=progress_callback,
        )
        if resumed_desktop_action is not None:
            return resumed_desktop_action

        synthesized_pause = _maybe_pause_for_browser_checkpoint(
            llm,
            tool_runtime,
            task_state,
            planner_goal,
            session_store=session_store,
        )
        if synthesized_pause is not None:
            return synthesized_pause

        synthesized_desktop_pause = _maybe_pause_for_desktop_action(
            llm,
            tool_runtime,
            task_state,
            planner_goal,
            session_store=session_store,
            progress_callback=progress_callback,
        )
        if synthesized_desktop_pause is not None:
            return synthesized_desktop_pause

        terminal_desktop_outcome = _maybe_finalize_desktop_terminal_outcome(
            llm,
            task_state,
            planner_goal,
            session_store=session_store,
            progress_callback=progress_callback,
        )
        if terminal_desktop_outcome is not None:
            return terminal_desktop_outcome

        if _maybe_bootstrap_browser_open(tool_runtime, task_state, planner_goal, session_store=session_store):
            continue

        observation = task_state.get_observation()
        desktop_vision = (
            task_state.get_desktop_vision_context(
                purpose=_desktop_vision_purpose(planner_goal, task_state),
                prompt_text=planner_goal,
                prefer_before_after=True,
            )
            if _goal_is_desktop_related(planner_goal, task_state)
            else {}
        )
        _emit_progress(progress_callback, "planning_started", detail="Started planning the next bounded step.")
        plan = llm.plan_next_action(
            planner_goal,
            observation,
            tool_runtime.planner_tools(),
            desktop_vision=desktop_vision,
        )

        if plan.get("done"):
            if _goal_is_desktop_related(planner_goal, task_state):
                task_state.set_desktop_run_outcome(
                    _desktop_build_run_outcome(
                        task_state,
                        outcome="completed",
                        status="completed",
                        terminal=True,
                        reason="completed",
                        summary="Completed the bounded desktop run from the current scene, evidence, and recovery context.",
                    )
                )
            task_state.status = "completed"
            _persist_session_state(session_store, task_state)
            return {
                "ok": True,
                "status": "completed",
                "message": _finalize_message(llm, task_state, progress_callback=progress_callback),
                "steps": task_state.steps,
            }

        tool_name = str(plan.get("tool", "")).strip()
        args = tool_runtime.prepare_args(tool_name, plan.get("args", {}), task_state, planning_goal=planner_goal)

        if not tool_runtime.has_tool(tool_name):
            task_state.add_step(
                {
                    "type": "tool",
                    "status": "failed",
                    "tool": tool_name,
                    "args": args,
                    "result": "Unknown tool",
                }
            )
            _persist_session_state(session_store, task_state)
            break

        if _is_repeated_non_mutating_plan(task_state, tool_name, args):
            return _finalize_repeated_non_mutating_plan(llm, task_state, tool_name, args, session_store=session_store)

        if _is_redundant_browser_follow_up(task_state, tool_name, args):
            return _finalize_redundant_browser_follow_up(llm, task_state, tool_name, args, session_store=session_store)

        if _is_redundant_desktop_observation(task_state, tool_name, planner_goal):
            return _finalize_redundant_desktop_observation(
                llm,
                task_state,
                tool_name,
                session_store=session_store,
                progress_callback=progress_callback,
            )

        if tool_name.startswith("desktop_"):
            args, result = _execute_desktop_tool_step(
                tool_runtime,
                task_state,
                tool_name,
                args,
                planner_goal,
                session_store=session_store,
                progress_callback=progress_callback,
            )
        else:
            generic_guard = guard_repeated_failed_action(task_state, tool_name, args)
            before_context = capture_action_context(task_state, tool_name, args)
            _emit_progress(
                progress_callback,
                "tool_step_attempted",
                detail=f"Attempting bounded tool step: {tool_name}.",
                tool_name=tool_name,
            )
            if generic_guard:
                result = generic_guard
            else:
                result = tool_runtime.execute(tool_name, args)
            _record_tool_result(task_state, tool_name, args, result, before_context=before_context)
            _emit_progress(
                progress_callback,
                "tool_result_recorded",
                detail=f"Recorded result from bounded tool step: {tool_name}.",
                tool_name=tool_name,
                result_status="paused" if result.get("paused", False) else ("completed" if result.get("ok", False) else "failed"),
            )
        operator_retry_stop = _maybe_finalize_operator_retry_stop(
            llm,
            task_state,
            tool_name,
            result,
            session_store=session_store,
            progress_callback=progress_callback,
        )
        if operator_retry_stop is not None:
            return operator_retry_stop
        if result.get("paused", False):
            if tool_name.startswith("desktop_"):
                task_state.set_desktop_run_outcome(
                    _desktop_build_run_outcome(
                        task_state,
                        outcome="approval_needed",
                        status="paused",
                        terminal=False,
                        reason="approval_needed",
                        summary=str(result.get("summary", "") or result.get("checkpoint_reason", "") or "Desktop approval is required before the next bounded action.").strip(),
                    )
                )
            task_state.status = "paused"
            _persist_session_state(session_store, task_state)
            return {
                "ok": False,
                "status": "paused",
                "message": _finalize_message(llm, task_state, progress_callback=progress_callback),
                "steps": task_state.steps,
            }
        desktop_guard_terminal = _maybe_finalize_desktop_action_guard(
            llm,
            task_state,
            result,
            session_store=session_store,
            progress_callback=progress_callback,
        )
        if desktop_guard_terminal is not None:
            return desktop_guard_terminal
        desktop_recovery = _maybe_recover_desktop_action_failure(
            llm,
            tool_runtime,
            task_state,
            planner_goal,
            tool_name,
            result,
            session_store=session_store,
            progress_callback=progress_callback,
        )
        if desktop_recovery is not None:
            if desktop_recovery.get("continue_loop", False):
                continue
            return desktop_recovery
        terminal_desktop_outcome = _maybe_finalize_desktop_terminal_outcome(
            llm,
            task_state,
            planner_goal,
            session_store=session_store,
            progress_callback=progress_callback,
        )
        if terminal_desktop_outcome is not None:
            return terminal_desktop_outcome
        synthesized_pause = _maybe_pause_for_browser_checkpoint(
            llm,
            tool_runtime,
            task_state,
            planner_goal,
            session_store=session_store,
        )
        if synthesized_pause is not None:
            return synthesized_pause
        synthesized_desktop_pause = _maybe_pause_for_desktop_action(
            llm,
            tool_runtime,
            task_state,
            planner_goal,
            session_store=session_store,
            progress_callback=progress_callback,
        )
        if synthesized_desktop_pause is not None:
            return synthesized_desktop_pause
        if callable(control_callback):
            control_result = _finalize_control_request(
                llm,
                task_state,
                control_callback(),
                session_store=session_store,
                progress_callback=progress_callback,
            )
            if control_result is not None:
                return control_result
        _persist_session_state(session_store, task_state)

    terminal_desktop_outcome = _maybe_finalize_desktop_terminal_outcome(
        llm,
        task_state,
        planner_goal,
        session_store=session_store,
        progress_callback=progress_callback,
    )
    if terminal_desktop_outcome is not None:
        return terminal_desktop_outcome
    if _goal_is_desktop_related(planner_goal, task_state):
        task_state.set_desktop_run_outcome(
            _desktop_build_run_outcome(
                task_state,
                outcome="incomplete",
                status="incomplete",
                terminal=True,
                reason="recovery_exhausted",
                summary="The bounded desktop run reached its iteration budget without a safe completion or actionable approval checkpoint.",
            )
        )
    task_state.status = "incomplete"
    _persist_session_state(session_store, task_state)
    return {
        "ok": False,
        "status": "incomplete",
        "message": _finalize_message(llm, task_state, progress_callback=progress_callback),
        "steps": task_state.steps,
    }

