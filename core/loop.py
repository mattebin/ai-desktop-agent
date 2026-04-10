"""Lean operator execution loop.

Redesigned to follow the Claude Code model:
- LLM is the planner/interpreter, called once per step
- Tools are thin wrappers around OS functions
- No mandatory evidence, approval gates, or verification per action
- Fast path skips LLM entirely for simple pattern-matched actions

Previous version (2,700 lines) archived at _graveyard/loop_v1.py.
"""

from __future__ import annotations

import time

from core.fast_path import try_direct_action, build_fast_result_message
from core.safety import stop_requested
from core.tool_runtime import ToolRuntime


# ── helpers ─────────────────────────────────────────────────────

def _persist_session_state(session_store, task_state):
    if session_store is None:
        return
    session_store.save(task_state)


def _emit_progress(progress_callback, stage: str, *, detail: str = "", tool_name: str = "", result_status: str = ""):
    if not callable(progress_callback):
        return
    try:
        progress_callback(stage, detail=detail, tool_name=tool_name, result_status=result_status)
    except Exception:
        pass


def _record_step(task_state, tool_name, args, result):
    """Record a tool execution as a step on the task state."""
    step_status = "paused" if result.get("paused", False) else ("completed" if result.get("ok", False) else "failed")
    step = {
        "type": "tool",
        "status": step_status,
        "tool": tool_name,
        "args": args,
        "result": result,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    task_state.add_step(step)
    # Lightweight memory: keep a rolling summary from recent results
    message = str(result.get("message", "") or result.get("summary", "")).strip()
    if message:
        task_state.add_note(f"{tool_name}: {message[:200]}")
        recent_notes = task_state.memory_notes[-6:]
        if recent_notes:
            task_state.set_summary(" | ".join(recent_notes))


def _lean_finalize_message(task_state) -> str:
    """Build a completion message from the last step — no LLM call."""
    if task_state.steps:
        last = task_state.steps[-1]
        result = last.get("result", {})
        if isinstance(result, dict):
            msg = str(result.get("message", "") or result.get("summary", "")).strip()
            if msg:
                return msg
        elif isinstance(result, str) and result.strip():
            return result.strip()
    return "Completed."


# ── lean desktop executor ───────────────────────────────────────
# Calls core OS functions directly. No evidence, no approval, no
# observation tokens, no verification sampling.

def _execute_lean_desktop_action(tool_name: str, args: dict) -> dict | None:
    """Execute a desktop tool using core functions only.

    Returns a result dict, or None if unsupported (fall through).
    """
    try:
        if tool_name == "desktop_open_target":
            from tools.desktop_backends import open_path_with_association
            target = str(args.get("target", "")).strip()
            if not target:
                return {"ok": False, "error": "No target provided.", "message": "No target provided."}
            result = open_path_with_association(target=target)
            return {
                "ok": bool(result.get("ok", False)),
                "message": str(result.get("message", "")).strip() or ("Opened." if result.get("ok") else "Failed to open."),
                "error": str(result.get("error", "")).strip(),
            }

        if tool_name == "desktop_press_key":
            from tools.desktop_input import _send_key_sequence
            key = str(args.get("key", "")).strip()
            modifiers = list(args.get("modifiers", []))
            if not key:
                return {"ok": False, "error": "No key specified.", "message": "No key specified."}
            ok = _send_key_sequence(key, modifiers, repeat=1)
            combo = "+".join(modifiers + [key])
            return {"ok": ok, "message": f"Pressed {combo}." if ok else f"Could not press {combo}."}

        if tool_name == "desktop_type_text":
            from tools.desktop_input import _send_text
            value = str(args.get("value", "")).strip()
            if not value:
                return {"ok": False, "error": "No text to type.", "message": "No text to type."}
            ok = _send_text(value)
            preview = value[:40] + ("..." if len(value) > 40 else "")
            return {"ok": ok, "message": f'Typed "{preview}".' if ok else "Could not type text."}

        if tool_name == "desktop_list_windows":
            from tools.desktop_windows import _enum_windows
            windows = _enum_windows(limit=int(args.get("limit", 20)))
            summaries = []
            for w in windows[:20]:
                title = str(w.get("title", "")).strip()
                proc = str(w.get("process_name", "")).strip()
                if title:
                    summaries.append(f"{title} ({proc})" if proc else title)
            return {"ok": True, "message": f"Found {len(windows)} window{'s' if len(windows) != 1 else ''}.", "windows": summaries}

        if tool_name == "desktop_capture_screenshot":
            from pathlib import Path
            try:
                import mss
                from mss import tools as mss_tools
            except ImportError:
                return {"ok": False, "error": "mss not installed.", "message": "Screenshot library (mss) is not available."}
            data_dir = Path("data/screenshots")
            data_dir.mkdir(parents=True, exist_ok=True)
            filename = f"screenshot_{int(time.time())}.png"
            path = data_dir / filename
            with mss.mss() as capture:
                shot = capture.grab(capture.monitors[1])
                mss_tools.to_png(shot.rgb, shot.size, output=str(path))
            return {"ok": True, "message": f"Screenshot saved to {path}.", "path": str(path)}

        if tool_name == "desktop_focus_window":
            from tools.desktop_windows import _enum_windows
            from tools.desktop_input import _focus_window_handle_native
            title_query = str(args.get("title", "")).strip().lower()
            if not title_query:
                return {"ok": False, "error": "No window title.", "message": "No window title specified."}
            windows = _enum_windows(limit=30)
            for w in windows:
                wtitle = str(w.get("title", "")).strip()
                if title_query in wtitle.lower():
                    hwnd = int(w.get("window_id", 0) or 0)
                    if hwnd > 0:
                        ok, msg = _focus_window_handle_native(hwnd)
                        return {"ok": ok, "message": f"Focused: {wtitle}." if ok else msg}
            return {"ok": False, "message": f"No window matching '{title_query}'."}

        if tool_name == "desktop_list_processes":
            from tools.desktop_windows import _enum_windows
            windows = _enum_windows(limit=30, include_minimized=True)
            procs = {}
            for w in windows:
                proc = str(w.get("process_name", "")).strip()
                if proc and proc not in procs:
                    procs[proc] = str(w.get("title", "")).strip()
            return {"ok": True, "message": f"Found {len(procs)} process{'es' if len(procs) != 1 else ''}.", "processes": list(procs.keys())}

    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "message": f"Failed: {exc}"}

    return None  # unsupported — fall through


# ── main loop ───────────────────────────────────────────────────

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
    """Lean execution loop — LLM plans, tools execute, repeat until done."""
    tool_runtime = tools if isinstance(tools, ToolRuntime) else ToolRuntime(tools)
    max_iterations = int(settings.get("max_iterations", 12))
    planner_goal = str(planning_goal or task_state.goal).strip() or task_state.goal
    _emit_progress(progress_callback, "loop_entered", detail="Entered the lean operator loop.")

    # ── fast path: pattern-match simple actions, skip LLM entirely ──
    raw_message = str(getattr(task_state, "raw_user_message", "")).strip()
    fast_path_input = raw_message or planner_goal
    if not task_state.steps:
        fast_plan = try_direct_action(fast_path_input)
        if fast_plan is not None:
            _emit_progress(progress_callback, "fast_path_matched", detail=f"Fast path: {fast_plan['tool']}")
            fast_tool = str(fast_plan["tool"]).strip()
            fast_args = dict(fast_plan.get("args", {}))

            # Try lean desktop execution first, fall through to tool runtime
            fast_result = _execute_lean_desktop_action(fast_tool, fast_args) if fast_tool.startswith("desktop_") else None
            if fast_result is None and tool_runtime.has_tool(fast_tool):
                fast_result = tool_runtime.execute(fast_tool, fast_args)
            if fast_result is None:
                fast_result = {"ok": False, "message": f"Tool {fast_tool} not available."}

            _record_step(task_state, fast_tool, fast_args, fast_result)
            _persist_session_state(session_store, task_state)

            if fast_result.get("ok", False):
                task_state.status = "completed"
                _persist_session_state(session_store, task_state)
                return {
                    "ok": True,
                    "status": "completed",
                    "message": build_fast_result_message(fast_tool, fast_args, fast_result),
                    "steps": task_state.steps,
                }
            # Fast path failed — fall through to LLM loop

    # ── normal path: LLM plans, tools execute ───────────────────
    for iteration in range(max_iterations):
        # Check for stop / control requests
        if stop_requested():
            task_state.status = "stopped"
            task_state.add_step({"type": "system", "status": "stopped", "message": "Emergency stop."})
            _persist_session_state(session_store, task_state)
            return {"ok": False, "status": "stopped", "message": "Stopped.", "steps": task_state.steps}

        if callable(control_callback):
            ctrl = control_callback()
            action = str((ctrl or {}).get("action", "")).strip().lower()
            if action == "stop":
                task_state.status = "stopped"
                task_state.add_step({"type": "system", "status": "stopped", "message": "Stopped by control request."})
                _persist_session_state(session_store, task_state)
                return {"ok": False, "status": "stopped", "message": "Stopped.", "steps": task_state.steps}

        # Ask LLM what to do next
        _emit_progress(progress_callback, "planning_started", detail=f"Planning step {iteration + 1}.")
        observation = task_state.get_observation()
        plan = llm.plan_next_action(
            planner_goal,
            observation,
            tool_runtime.planner_tools(task_state),
        )

        # LLM says we're done
        if plan.get("done"):
            task_state.status = "completed"
            message = str(plan.get("message", "")).strip() or _lean_finalize_message(task_state)
            _persist_session_state(session_store, task_state)
            return {"ok": True, "status": "completed", "message": message, "steps": task_state.steps}

        # Extract tool call from plan
        tool_name = str(plan.get("tool", "")).strip()
        args = dict(plan.get("args", {})) if isinstance(plan.get("args"), dict) else {}

        if not tool_name:
            task_state.add_step({"type": "system", "status": "failed", "message": "LLM returned no tool."})
            _persist_session_state(session_store, task_state)
            continue

        # Execute: lean desktop path first, then tool runtime
        _emit_progress(progress_callback, "tool_step_attempted", detail=f"Executing: {tool_name}.", tool_name=tool_name)

        lean_result = _execute_lean_desktop_action(tool_name, args) if tool_name.startswith("desktop_") else None
        if lean_result is not None:
            result = lean_result
        elif tool_runtime.has_tool(tool_name):
            prepared_args = tool_runtime.prepare_args(tool_name, args, task_state, planning_goal=planner_goal)
            result = tool_runtime.execute(tool_name, prepared_args)
            args = prepared_args
        else:
            result = {"ok": False, "error": f"Unknown tool: {tool_name}", "message": f"Unknown tool: {tool_name}"}

        # Record and persist
        _record_step(task_state, tool_name, args, result)
        _persist_session_state(session_store, task_state)

        result_status = "paused" if result.get("paused") else ("completed" if result.get("ok") else "failed")
        _emit_progress(progress_callback, "tool_result_recorded", detail=f"Result: {tool_name}.", tool_name=tool_name, result_status=result_status)

        # Handle paused state (approval still works if user opts in)
        if result.get("paused", False):
            task_state.status = "paused"
            _persist_session_state(session_store, task_state)
            return {"ok": False, "status": "paused", "message": _lean_finalize_message(task_state), "steps": task_state.steps}

    # Hit iteration limit
    task_state.status = "incomplete"
    _persist_session_state(session_store, task_state)
    return {
        "ok": False,
        "status": "incomplete",
        "message": _lean_finalize_message(task_state) or "Reached the iteration limit without completing.",
        "steps": task_state.steps,
    }


# ── backward-compat stubs for smoke_test.py imports ─────────────
# These functions existed in the enterprise loop (v1) and are imported
# by the eval script.  They are no-ops in the lean architecture.

def _execute_desktop_tool_step(tool_runtime, task_state, tool_name, seed_args, planner_goal, **kw):
    """Stub — lean loop uses _execute_lean_desktop_action instead."""
    result = _execute_lean_desktop_action(tool_name, seed_args)
    if result is None:
        args = tool_runtime.prepare_args(tool_name, seed_args, task_state, planning_goal=planner_goal)
        result = tool_runtime.execute(tool_name, args)
    return seed_args, result


def _finalize_message(llm, task_state, **kw):
    """Stub — lean loop uses _lean_finalize_message instead."""
    return _lean_finalize_message(task_state)


def _is_redundant_desktop_observation(task_state, tool_name, planner_goal):
    """Stub — no longer relevant."""
    return False


def _maybe_finalize_desktop_terminal_outcome(llm, task_state, planner_goal, **kw):
    """Stub — no longer relevant."""
    return None


def _maybe_pause_for_desktop_action(llm, tool_runtime, task_state, planner_goal, **kw):
    """Stub — no longer relevant."""
    return None


def _maybe_recover_desktop_action_failure(llm, tool_runtime, task_state, planner_goal, tool_name, result, **kw):
    """Stub — no longer relevant."""
    return None
