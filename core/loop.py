from __future__ import annotations

import re

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
_DESKTOP_FIELD_LABEL_PATTERN = re.compile(r"field label(?:ed|led)? ['\"]([^'\"]{1,120})['\"]", re.IGNORECASE)
_DESKTOP_WINDOW_TITLE_PATTERN = re.compile(r"window titled ['\"]([^'\"]{1,180})['\"]", re.IGNORECASE)
_DESKTOP_INSPECT_TOOLS = {"desktop_list_windows", "desktop_get_active_window", "desktop_capture_screenshot"}


def _persist_session_state(session_store, task_state):
    if session_store is None:
        return
    session_store.save(task_state)


def _finalize_message(llm, task_state) -> str:
    return llm.finalize(
        task_state.goal,
        task_state.steps,
        task_state.get_observation(),
        task_state.get_final_context(),
    )


def _finalize_control_request(llm, task_state, request, *, session_store=None):
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
        "message": _finalize_message(llm, task_state),
        "steps": task_state.steps,
    }


def _record_tool_result(task_state, tool_name, args, result):
    step_status = "paused" if result.get("paused", False) else ("completed" if result.get("ok", False) else "failed")
    step = {
        "type": "tool",
        "status": step_status,
        "tool": tool_name,
        "args": args,
        "result": result,
    }
    task_state.add_step(step)
    task_state.update_memory_from_tool(tool_name, result)
    task_state.add_note(task_state.summarize_result_for_memory(tool_name, result))

    recent_notes = task_state.memory_notes[-6:]
    if recent_notes:
        task_state.set_summary(" | ".join(recent_notes))


def _finalize_guarded_completion(llm, task_state, note: str, *, tool_name="", args=None, session_store=None):
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
        "message": _finalize_message(llm, task_state),
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


def _desktop_has_inspection_context(task_state) -> bool:
    return any(_has_completed_tool(task_state, tool_name) for tool_name in _DESKTOP_INSPECT_TOOLS)


def _desktop_target_window_ready(task_state, planner_goal: str) -> bool:
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
    if require_screenshot and not str(getattr(task_state, "desktop_last_screenshot_path", "")).strip():
        return False
    return True


def _finalize_redundant_desktop_observation(llm, task_state, tool_name, session_store=None):
    note = f"Stopped repeating {tool_name} after the current desktop evidence was already sufficient and finalized from the latest desktop observation."
    return _finalize_guarded_completion(
        llm,
        task_state,
        note,
        tool_name=tool_name,
        args={},
        session_store=session_store,
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
):
    task_state.set_desktop_checkpoint(
        reason=checkpoint_reason,
        tool=tool_name,
        target=checkpoint_target,
        approval_status="not approved",
        resume_args=resume_args,
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
        "message": _finalize_message(llm, task_state),
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
    result = tool_runtime.execute("browser_open_page", args)
    _record_tool_result(task_state, "browser_open_page", args, result)
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


def _maybe_pause_for_desktop_action(llm, tool_runtime, task_state, planner_goal: str, session_store=None):
    if getattr(task_state, "desktop_checkpoint_pending", False):
        return None
    if tool_runtime.goal_has_explicit_desktop_approval(planner_goal):
        return None
    if not getattr(task_state, "desktop_observation_token", ""):
        return None
    if _goal_mentions_desktop_focus(planner_goal) and not _has_completed_tool(task_state, "desktop_focus_window"):
        return None
    if _goal_mentions_desktop_screenshot(planner_goal) and not _has_completed_tool(task_state, "desktop_capture_screenshot"):
        return None
    if not _desktop_has_inspection_context(task_state):
        return None
    if not _desktop_target_window_ready(task_state, planner_goal):
        return None

    click_point = _goal_desktop_click_point(planner_goal)
    if click_point and not _has_any_tool_step(task_state, "desktop_click_point"):
        desktop_activity = task_state._collect_desktop_activity(limit=4)
        selected_assessment = _desktop_evidence_assessment(
            task_state,
            purpose="desktop_action_prepare",
            target_window_title=_goal_desktop_window_title(planner_goal),
            require_screenshot=True,
        )
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
        )

    type_request = _goal_desktop_type_request(planner_goal)
    if type_request and not _has_any_tool_step(task_state, "desktop_type_text"):
        desktop_activity = task_state._collect_desktop_activity(limit=4)
        selected_assessment = _desktop_evidence_assessment(
            task_state,
            purpose="desktop_action_prepare",
            target_window_title=_goal_desktop_window_title(planner_goal),
            require_screenshot=False,
        )
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
        )
    return None


def _maybe_resume_desktop_checkpoint(llm, tool_runtime, task_state, planner_goal: str, session_store=None):
    if not getattr(task_state, "desktop_checkpoint_pending", False):
        return None
    if not tool_runtime.goal_has_explicit_desktop_approval(planner_goal):
        return None

    tool_name = str(getattr(task_state, "desktop_checkpoint_tool", "")).strip()
    if tool_name not in {"desktop_click_point", "desktop_type_text"}:
        return None

    args = tool_runtime.prepare_args(tool_name, {}, task_state, planning_goal=planner_goal)
    result = tool_runtime.execute(tool_name, args)
    _record_tool_result(task_state, tool_name, args, result)
    _persist_session_state(session_store, task_state)

    if result.get("paused", False):
        task_state.status = "paused"
        _persist_session_state(session_store, task_state)
        return {
            "ok": False,
            "status": "paused",
            "message": _finalize_message(llm, task_state),
            "steps": task_state.steps,
        }

    if result.get("ok", False):
        task_state.status = "completed"
        _persist_session_state(session_store, task_state)
        return {
            "ok": True,
            "status": "completed",
            "message": _finalize_message(llm, task_state),
            "steps": task_state.steps,
        }

    return None


def run_task_loop(llm, tools, task_state, settings, session_store=None, planning_goal: str | None = None, control_callback=None):
    tool_runtime = tools if isinstance(tools, ToolRuntime) else ToolRuntime(tools)
    max_iterations = int(settings.get("max_iterations", 12))
    planner_goal = str(planning_goal or task_state.goal).strip() or task_state.goal

    for _ in range(max_iterations):
        if callable(control_callback):
            control_result = _finalize_control_request(llm, task_state, control_callback(), session_store=session_store)
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
        )
        if synthesized_desktop_pause is not None:
            return synthesized_desktop_pause

        if _maybe_bootstrap_browser_open(tool_runtime, task_state, planner_goal, session_store=session_store):
            continue

        observation = task_state.get_observation()
        plan = llm.plan_next_action(
            planner_goal,
            observation,
            tool_runtime.planner_tools(),
        )

        if plan.get("done"):
            task_state.status = "completed"
            _persist_session_state(session_store, task_state)
            return {
                "ok": True,
                "status": "completed",
                "message": _finalize_message(llm, task_state),
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
            return _finalize_redundant_desktop_observation(llm, task_state, tool_name, session_store=session_store)

        result = tool_runtime.execute(tool_name, args)
        _record_tool_result(task_state, tool_name, args, result)
        if result.get("paused", False):
            task_state.status = "paused"
            _persist_session_state(session_store, task_state)
            return {
                "ok": False,
                "status": "paused",
                "message": _finalize_message(llm, task_state),
                "steps": task_state.steps,
            }
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
        )
        if synthesized_desktop_pause is not None:
            return synthesized_desktop_pause
        if callable(control_callback):
            control_result = _finalize_control_request(llm, task_state, control_callback(), session_store=session_store)
            if control_result is not None:
                return control_result
        _persist_session_state(session_store, task_state)

    task_state.status = "incomplete"
    _persist_session_state(session_store, task_state)
    return {
        "ok": False,
        "status": "incomplete",
        "message": _finalize_message(llm, task_state),
        "steps": task_state.steps,
    }

