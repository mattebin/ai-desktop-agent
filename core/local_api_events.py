from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Iterator, List


DEFAULT_LOCAL_EVENT_POLL_SECONDS = 0.75
DEFAULT_LOCAL_EVENT_HEARTBEAT_SECONDS = 12.0
DEFAULT_LOCAL_EVENT_MESSAGE_LIMIT = 40
DEFAULT_LOCAL_EVENT_ALERT_LIMIT = 8
DEFAULT_LOCAL_EVENT_REPLAY_SIZE = 80
DEFAULT_LOCAL_EVENT_CHANNEL_RETENTION_SECONDS = 45.0
DEFAULT_LOCAL_EVENT_MAX_CHANNELS = 24
DEFAULT_LOCAL_EVENT_FRAME_MIN_SECONDS = 0.45


def _trim_text(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _json_fingerprint(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value)


def _compact_task(task: Dict[str, Any] | None) -> Dict[str, Any]:
    task = task if isinstance(task, dict) else {}
    progress = task.get("progress", {}) if isinstance(task.get("progress", {}), dict) else {}
    return {
        "task_id": _trim_text(task.get("task_id", ""), limit=60),
        "status": _trim_text(task.get("status", ""), limit=40),
        "goal": _trim_text(task.get("goal", ""), limit=220),
        "last_message": _trim_text(task.get("last_message", ""), limit=240),
        "run_id": _trim_text(task.get("run_id", ""), limit=60),
        "approval_needed": bool(task.get("approval_needed", False)),
        "approval_reason": _trim_text(task.get("approval_reason", ""), limit=180),
        "progress": {
            "stage": _trim_text(progress.get("stage", ""), limit=60),
            "detail": _trim_text(progress.get("detail", ""), limit=220),
            "result_status": _trim_text(progress.get("result_status", ""), limit=40),
            "at": _trim_text(progress.get("at", ""), limit=40),
            "worker_started_at": _trim_text(progress.get("worker_started_at", ""), limit=40),
            "run_state_entered_at": _trim_text(progress.get("run_state_entered_at", ""), limit=40),
            "first_loop_at": _trim_text(progress.get("first_loop_at", ""), limit=40),
            "first_step_at": _trim_text(progress.get("first_step_at", ""), limit=40),
            "first_result_at": _trim_text(progress.get("first_result_at", ""), limit=40),
            "terminal_at": _trim_text(progress.get("terminal_at", ""), limit=40),
            "meaningful_progress": bool(progress.get("meaningful_progress", False)),
        },
    }


def _compact_run_focus(value: Dict[str, Any] | None) -> Dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    return {
        "phase": _trim_text(value.get("phase", "idle"), limit=40),
        "reason": _trim_text(value.get("reason", ""), limit=80),
        "locked": bool(value.get("locked", False)),
        "task_id": _trim_text(value.get("task_id", ""), limit=60),
        "session_id": _trim_text(value.get("session_id", ""), limit=80),
        "run_id": _trim_text(value.get("run_id", ""), limit=60),
        "detail": _trim_text(value.get("detail", ""), limit=220),
    }


def _compact_lifecycle(lifecycle: Dict[str, Any] | None) -> Dict[str, Any]:
    lifecycle = lifecycle if isinstance(lifecycle, dict) else {}
    return {
        "event": _trim_text(lifecycle.get("event", ""), limit=60),
        "task_id": _trim_text(lifecycle.get("task_id", ""), limit=60),
        "session_id": _trim_text(lifecycle.get("session_id", ""), limit=80),
        "state_scope_id": _trim_text(lifecycle.get("state_scope_id", ""), limit=120),
        "reason": _trim_text(lifecycle.get("reason", ""), limit=80),
        "detail": _trim_text(lifecycle.get("detail", ""), limit=220),
        "from_status": _trim_text(lifecycle.get("from_status", ""), limit=40),
        "to_status": _trim_text(lifecycle.get("to_status", ""), limit=40),
        "timestamp": _trim_text(lifecycle.get("timestamp", ""), limit=40),
    }


def _compact_pending(pending: Dict[str, Any] | None) -> Dict[str, Any]:
    pending = pending if isinstance(pending, dict) else {}
    return {
        "kind": _trim_text(pending.get("kind", ""), limit=80),
        "reason": _trim_text(pending.get("reason", ""), limit=180),
        "summary": _trim_text(pending.get("summary", ""), limit=180),
        "step": _trim_text(pending.get("step", ""), limit=120),
        "tool": _trim_text(pending.get("tool", ""), limit=120),
        "target": _trim_text(pending.get("target", ""), limit=180),
        "approval_status": _trim_text(pending.get("approval_status", ""), limit=40),
        "evidence_id": _trim_text(pending.get("evidence_id", ""), limit=80),
        "evidence_summary": _trim_text(pending.get("evidence_summary", ""), limit=220),
        "evidence_assessment": {
            "state": _trim_text(pending.get("evidence_assessment", {}).get("state", "") if isinstance(pending.get("evidence_assessment", {}), dict) else "", limit=40),
            "reason": _trim_text(pending.get("evidence_assessment", {}).get("reason", "") if isinstance(pending.get("evidence_assessment", {}), dict) else "", limit=40),
            "summary": _trim_text(pending.get("evidence_assessment", {}).get("summary", "") if isinstance(pending.get("evidence_assessment", {}), dict) else "", limit=220),
            "sufficient": bool(pending.get("evidence_assessment", {}).get("sufficient", False)) if isinstance(pending.get("evidence_assessment", {}), dict) else False,
            "needs_refresh": bool(pending.get("evidence_assessment", {}).get("needs_refresh", False)) if isinstance(pending.get("evidence_assessment", {}), dict) else False,
        },
        "scene_preview": {
            "scene_class": _trim_text(pending.get("scene_preview", {}).get("scene_class", "") if isinstance(pending.get("scene_preview", {}), dict) else "", limit=40),
            "workflow_state": _trim_text(pending.get("scene_preview", {}).get("workflow_state", "") if isinstance(pending.get("scene_preview", {}), dict) else "", limit=40),
            "reason": _trim_text(pending.get("scene_preview", {}).get("reason", "") if isinstance(pending.get("scene_preview", {}), dict) else "", limit=40),
            "summary": _trim_text(pending.get("scene_preview", {}).get("summary", "") if isinstance(pending.get("scene_preview", {}), dict) else "", limit=220),
            "direct_image_helpful": bool(pending.get("scene_preview", {}).get("direct_image_helpful", False)) if isinstance(pending.get("scene_preview", {}), dict) else False,
        },
    }


def _compact_browser(browser: Dict[str, Any] | None) -> Dict[str, Any]:
    browser = browser if isinstance(browser, dict) else {}
    return {
        "task_name": _trim_text(browser.get("task_name", browser.get("task_label", "")), limit=80),
        "task_step": _trim_text(browser.get("task_step", ""), limit=120),
        "task_status": _trim_text(browser.get("task_status", ""), limit=80),
        "workflow_name": _trim_text(browser.get("workflow_name", ""), limit=80),
        "workflow_step": _trim_text(browser.get("workflow_step", ""), limit=120),
        "workflow_status": _trim_text(browser.get("workflow_status", ""), limit=80),
        "current_title": _trim_text(browser.get("current_title", ""), limit=120),
        "current_url": _trim_text(browser.get("current_url", ""), limit=220),
        "expected_state": _trim_text(browser.get("expected_state", ""), limit=180),
        "last_action": _trim_text(browser.get("last_action", ""), limit=120),
        "last_successful_action": _trim_text(browser.get("last_successful_action", ""), limit=120),
    }


def _compact_desktop(desktop: Dict[str, Any] | None) -> Dict[str, Any]:
    desktop = desktop if isinstance(desktop, dict) else {}
    return {
        "active_window_title": _trim_text(desktop.get("active_window_title", ""), limit=160),
        "active_window_process": _trim_text(desktop.get("active_window_process", ""), limit=120),
        "last_action": _trim_text(desktop.get("last_action", ""), limit=180),
        "last_target_window": _trim_text(desktop.get("last_target_window", ""), limit=160),
        "last_point": _trim_text(desktop.get("last_point", ""), limit=80),
        "last_typed_text_preview": _trim_text(desktop.get("last_typed_text_preview", ""), limit=80),
        "checkpoint_pending": bool(desktop.get("checkpoint_pending", False)),
        "checkpoint_tool": _trim_text(desktop.get("checkpoint_tool", ""), limit=80),
        "checkpoint_reason": _trim_text(desktop.get("checkpoint_reason", ""), limit=180),
        "checkpoint_target": _trim_text(desktop.get("checkpoint_target", ""), limit=180),
        "checkpoint_evidence_id": _trim_text(desktop.get("checkpoint_evidence_id", ""), limit=80),
        "screenshot_path": _trim_text(desktop.get("screenshot_path", ""), limit=220),
        "evidence_id": _trim_text(desktop.get("evidence_id", ""), limit=80),
        "evidence_summary": _trim_text(desktop.get("evidence_summary", ""), limit=220),
        "evidence_bundle_path": _trim_text(desktop.get("evidence_bundle_path", ""), limit=260),
        "evidence_reason": _trim_text(desktop.get("evidence_reason", ""), limit=80),
        "evidence_timestamp": _trim_text(desktop.get("evidence_timestamp", ""), limit=40),
        "selected_evidence": {
            "evidence_id": _trim_text(desktop.get("selected_evidence", {}).get("evidence_id", "") if isinstance(desktop.get("selected_evidence", {}), dict) else "", limit=80),
            "summary": _trim_text(desktop.get("selected_evidence", {}).get("summary", "") if isinstance(desktop.get("selected_evidence", {}), dict) else "", limit=220),
            "reason": _trim_text(desktop.get("selected_evidence", {}).get("reason", "") if isinstance(desktop.get("selected_evidence", {}), dict) else "", limit=40),
            "selection_reason": _trim_text(desktop.get("selected_evidence", {}).get("selection_reason", "") if isinstance(desktop.get("selected_evidence", {}), dict) else "", limit=40),
        },
        "recent_context_evidence": [
            {
                "evidence_id": _trim_text(item.get("evidence_id", ""), limit=80),
                "summary": _trim_text(item.get("summary", ""), limit=180),
                "importance": _trim_text(item.get("importance", ""), limit=40),
                "capture_mode": _trim_text(item.get("capture_mode", ""), limit=40),
            }
            for item in list(desktop.get("recent_context_evidence", []))[:3]
            if isinstance(item, dict)
        ],
        "selected_evidence_assessment": {
            "state": _trim_text(desktop.get("selected_evidence_assessment", {}).get("state", "") if isinstance(desktop.get("selected_evidence_assessment", {}), dict) else "", limit=40),
            "reason": _trim_text(desktop.get("selected_evidence_assessment", {}).get("reason", "") if isinstance(desktop.get("selected_evidence_assessment", {}), dict) else "", limit=40),
            "summary": _trim_text(desktop.get("selected_evidence_assessment", {}).get("summary", "") if isinstance(desktop.get("selected_evidence_assessment", {}), dict) else "", limit=220),
            "sufficient": bool(desktop.get("selected_evidence_assessment", {}).get("sufficient", False)) if isinstance(desktop.get("selected_evidence_assessment", {}), dict) else False,
            "needs_refresh": bool(desktop.get("selected_evidence_assessment", {}).get("needs_refresh", False)) if isinstance(desktop.get("selected_evidence_assessment", {}), dict) else False,
        },
        "selected_scene": {
            "scene_class": _trim_text(desktop.get("selected_scene", {}).get("scene_class", "") if isinstance(desktop.get("selected_scene", {}), dict) else "", limit=40),
            "workflow_state": _trim_text(desktop.get("selected_scene", {}).get("workflow_state", "") if isinstance(desktop.get("selected_scene", {}), dict) else "", limit=40),
            "reason": _trim_text(desktop.get("selected_scene", {}).get("reason", "") if isinstance(desktop.get("selected_scene", {}), dict) else "", limit=40),
            "summary": _trim_text(desktop.get("selected_scene", {}).get("summary", "") if isinstance(desktop.get("selected_scene", {}), dict) else "", limit=220),
            "scene_changed": bool(desktop.get("selected_scene", {}).get("scene_changed", False)) if isinstance(desktop.get("selected_scene", {}), dict) else False,
            "direct_image_helpful": bool(desktop.get("selected_scene", {}).get("direct_image_helpful", False)) if isinstance(desktop.get("selected_scene", {}), dict) else False,
        },
        "selected_vision": {
            "mode": _trim_text(desktop.get("selected_vision", {}).get("mode", "") if isinstance(desktop.get("selected_vision", {}), dict) else "", limit=40),
            "reason": _trim_text(desktop.get("selected_vision", {}).get("reason", "") if isinstance(desktop.get("selected_vision", {}), dict) else "", limit=40),
            "summary": _trim_text(desktop.get("selected_vision", {}).get("summary", "") if isinstance(desktop.get("selected_vision", {}), dict) else "", limit=220),
            "needs_direct_image": bool(desktop.get("selected_vision", {}).get("needs_direct_image", False)) if isinstance(desktop.get("selected_vision", {}), dict) else False,
        },
        "selected_target_proposals": {
            "state": _trim_text(desktop.get("selected_target_proposals", {}).get("state", "") if isinstance(desktop.get("selected_target_proposals", {}), dict) else "", limit=40),
            "reason": _trim_text(desktop.get("selected_target_proposals", {}).get("reason", "") if isinstance(desktop.get("selected_target_proposals", {}), dict) else "", limit=60),
            "summary": _trim_text(desktop.get("selected_target_proposals", {}).get("summary", "") if isinstance(desktop.get("selected_target_proposals", {}), dict) else "", limit=220),
            "proposal_count": int(desktop.get("selected_target_proposals", {}).get("proposal_count", 0) or 0) if isinstance(desktop.get("selected_target_proposals", {}), dict) else 0,
            "top_proposals": [
                {
                    "target_kind": _trim_text(item.get("target_kind", ""), limit=40),
                    "summary": _trim_text(item.get("summary", ""), limit=180),
                    "confidence": _trim_text(item.get("confidence", ""), limit=20),
                    "approval_required": bool(item.get("approval_required", False)),
                    "suggested_next_actions": [_trim_text(action, limit=60) for action in list(item.get("suggested_next_actions", []))[:2] if _trim_text(action, limit=60)],
                }
                for item in list(desktop.get("selected_target_proposals", {}).get("proposals", []))[:2]
                if isinstance(item, dict)
            ],
        },
        "checkpoint_evidence": {
            "evidence_id": _trim_text(desktop.get("checkpoint_evidence", {}).get("evidence_id", "") if isinstance(desktop.get("checkpoint_evidence", {}), dict) else "", limit=80),
            "summary": _trim_text(desktop.get("checkpoint_evidence", {}).get("summary", "") if isinstance(desktop.get("checkpoint_evidence", {}), dict) else "", limit=220),
            "reason": _trim_text(desktop.get("checkpoint_evidence", {}).get("reason", "") if isinstance(desktop.get("checkpoint_evidence", {}), dict) else "", limit=40),
            "selection_reason": _trim_text(desktop.get("checkpoint_evidence", {}).get("selection_reason", "") if isinstance(desktop.get("checkpoint_evidence", {}), dict) else "", limit=40),
        },
        "checkpoint_evidence_assessment": {
            "state": _trim_text(desktop.get("checkpoint_evidence_assessment", {}).get("state", "") if isinstance(desktop.get("checkpoint_evidence_assessment", {}), dict) else "", limit=40),
            "reason": _trim_text(desktop.get("checkpoint_evidence_assessment", {}).get("reason", "") if isinstance(desktop.get("checkpoint_evidence_assessment", {}), dict) else "", limit=40),
            "summary": _trim_text(desktop.get("checkpoint_evidence_assessment", {}).get("summary", "") if isinstance(desktop.get("checkpoint_evidence_assessment", {}), dict) else "", limit=220),
            "sufficient": bool(desktop.get("checkpoint_evidence_assessment", {}).get("sufficient", False)) if isinstance(desktop.get("checkpoint_evidence_assessment", {}), dict) else False,
            "needs_refresh": bool(desktop.get("checkpoint_evidence_assessment", {}).get("needs_refresh", False)) if isinstance(desktop.get("checkpoint_evidence_assessment", {}), dict) else False,
        },
        "checkpoint_scene": {
            "scene_class": _trim_text(desktop.get("checkpoint_scene", {}).get("scene_class", "") if isinstance(desktop.get("checkpoint_scene", {}), dict) else "", limit=40),
            "workflow_state": _trim_text(desktop.get("checkpoint_scene", {}).get("workflow_state", "") if isinstance(desktop.get("checkpoint_scene", {}), dict) else "", limit=40),
            "reason": _trim_text(desktop.get("checkpoint_scene", {}).get("reason", "") if isinstance(desktop.get("checkpoint_scene", {}), dict) else "", limit=40),
            "summary": _trim_text(desktop.get("checkpoint_scene", {}).get("summary", "") if isinstance(desktop.get("checkpoint_scene", {}), dict) else "", limit=220),
            "scene_changed": bool(desktop.get("checkpoint_scene", {}).get("scene_changed", False)) if isinstance(desktop.get("checkpoint_scene", {}), dict) else False,
            "direct_image_helpful": bool(desktop.get("checkpoint_scene", {}).get("direct_image_helpful", False)) if isinstance(desktop.get("checkpoint_scene", {}), dict) else False,
        },
        "checkpoint_vision": {
            "mode": _trim_text(desktop.get("checkpoint_vision", {}).get("mode", "") if isinstance(desktop.get("checkpoint_vision", {}), dict) else "", limit=40),
            "reason": _trim_text(desktop.get("checkpoint_vision", {}).get("reason", "") if isinstance(desktop.get("checkpoint_vision", {}), dict) else "", limit=40),
            "summary": _trim_text(desktop.get("checkpoint_vision", {}).get("summary", "") if isinstance(desktop.get("checkpoint_vision", {}), dict) else "", limit=220),
            "needs_direct_image": bool(desktop.get("checkpoint_vision", {}).get("needs_direct_image", False)) if isinstance(desktop.get("checkpoint_vision", {}), dict) else False,
        },
        "checkpoint_target_proposals": {
            "state": _trim_text(desktop.get("checkpoint_target_proposals", {}).get("state", "") if isinstance(desktop.get("checkpoint_target_proposals", {}), dict) else "", limit=40),
            "reason": _trim_text(desktop.get("checkpoint_target_proposals", {}).get("reason", "") if isinstance(desktop.get("checkpoint_target_proposals", {}), dict) else "", limit=60),
            "summary": _trim_text(desktop.get("checkpoint_target_proposals", {}).get("summary", "") if isinstance(desktop.get("checkpoint_target_proposals", {}), dict) else "", limit=220),
            "proposal_count": int(desktop.get("checkpoint_target_proposals", {}).get("proposal_count", 0) or 0) if isinstance(desktop.get("checkpoint_target_proposals", {}), dict) else 0,
            "top_proposals": [
                {
                    "target_kind": _trim_text(item.get("target_kind", ""), limit=40),
                    "summary": _trim_text(item.get("summary", ""), limit=180),
                    "confidence": _trim_text(item.get("confidence", ""), limit=20),
                    "approval_required": bool(item.get("approval_required", False)),
                    "suggested_next_actions": [_trim_text(action, limit=60) for action in list(item.get("suggested_next_actions", []))[:2] if _trim_text(action, limit=60)],
                }
                for item in list(desktop.get("checkpoint_target_proposals", {}).get("proposals", []))[:2]
                if isinstance(item, dict)
            ],
        },
        "run_outcome": _compact_desktop_outcome(desktop.get("run_outcome", {})),
        "latest_recovery": {
            "state": _trim_text(desktop.get("latest_recovery", {}).get("state", "") if isinstance(desktop.get("latest_recovery", {}), dict) else "", limit=40),
            "reason": _trim_text(desktop.get("latest_recovery", {}).get("reason", "") if isinstance(desktop.get("latest_recovery", {}), dict) else "", limit=60),
            "summary": _trim_text(desktop.get("latest_recovery", {}).get("summary", "") if isinstance(desktop.get("latest_recovery", {}), dict) else "", limit=220),
            "strategy": _trim_text(desktop.get("latest_recovery", {}).get("strategy", "") if isinstance(desktop.get("latest_recovery", {}), dict) else "", limit=60),
        },
        "latest_window_readiness": {
            "state": _trim_text(desktop.get("latest_window_readiness", {}).get("state", "") if isinstance(desktop.get("latest_window_readiness", {}), dict) else "", limit=40),
            "reason": _trim_text(desktop.get("latest_window_readiness", {}).get("reason", "") if isinstance(desktop.get("latest_window_readiness", {}), dict) else "", limit=60),
            "summary": _trim_text(desktop.get("latest_window_readiness", {}).get("summary", "") if isinstance(desktop.get("latest_window_readiness", {}), dict) else "", limit=220),
        },
        "latest_visual_stability": {
            "state": _trim_text(desktop.get("latest_visual_stability", {}).get("state", "") if isinstance(desktop.get("latest_visual_stability", {}), dict) else "", limit=40),
            "reason": _trim_text(desktop.get("latest_visual_stability", {}).get("reason", "") if isinstance(desktop.get("latest_visual_stability", {}), dict) else "", limit=60),
            "summary": _trim_text(desktop.get("latest_visual_stability", {}).get("summary", "") if isinstance(desktop.get("latest_visual_stability", {}), dict) else "", limit=220),
        },
        "latest_process_context": {
            "process_name": _trim_text(desktop.get("latest_process_context", {}).get("process_name", "") if isinstance(desktop.get("latest_process_context", {}), dict) else "", limit=120),
            "status": _trim_text(desktop.get("latest_process_context", {}).get("status", "") if isinstance(desktop.get("latest_process_context", {}), dict) else "", limit=60),
            "running": bool(desktop.get("latest_process_context", {}).get("running", False)) if isinstance(desktop.get("latest_process_context", {}), dict) else False,
            "summary": _trim_text(desktop.get("latest_process_context", {}).get("summary", "") if isinstance(desktop.get("latest_process_context", {}), dict) else "", limit=220),
        },
        "latest_mouse_action": {
            "action": _trim_text(desktop.get("latest_mouse_action", {}).get("action", "") if isinstance(desktop.get("latest_mouse_action", {}), dict) else "", limit=40),
            "button": _trim_text(desktop.get("latest_mouse_action", {}).get("button", "") if isinstance(desktop.get("latest_mouse_action", {}), dict) else "", limit=20),
            "click_count": int(desktop.get("latest_mouse_action", {}).get("click_count", 0) or 0) if isinstance(desktop.get("latest_mouse_action", {}), dict) else 0,
            "coordinate_mode": _trim_text(desktop.get("latest_mouse_action", {}).get("coordinate_mode", "") if isinstance(desktop.get("latest_mouse_action", {}), dict) else "", limit=40),
            "mapping_reason": _trim_text(desktop.get("latest_mouse_action", {}).get("mapping_reason", "") if isinstance(desktop.get("latest_mouse_action", {}), dict) else "", limit=80),
            "monitor": _trim_text(desktop.get("latest_mouse_action", {}).get("monitor", "") if isinstance(desktop.get("latest_mouse_action", {}), dict) else "", limit=120),
            "point": _trim_text(desktop.get("latest_mouse_action", {}).get("point", "") if isinstance(desktop.get("latest_mouse_action", {}), dict) else "", limit=80),
            "summary": _trim_text(desktop.get("latest_mouse_action", {}).get("summary", "") if isinstance(desktop.get("latest_mouse_action", {}), dict) else "", limit=220),
        },
        "latest_process_action": {
            "action": _trim_text(desktop.get("latest_process_action", {}).get("action", "") if isinstance(desktop.get("latest_process_action", {}), dict) else "", limit=40),
            "pid": int(desktop.get("latest_process_action", {}).get("pid", 0) or 0) if isinstance(desktop.get("latest_process_action", {}), dict) else 0,
            "process_name": _trim_text(desktop.get("latest_process_action", {}).get("process_name", "") if isinstance(desktop.get("latest_process_action", {}), dict) else "", limit=120),
            "owned": bool(desktop.get("latest_process_action", {}).get("owned", False)) if isinstance(desktop.get("latest_process_action", {}), dict) else False,
            "owned_label": _trim_text(desktop.get("latest_process_action", {}).get("owned_label", "") if isinstance(desktop.get("latest_process_action", {}), dict) else "", limit=120),
            "summary": _trim_text(desktop.get("latest_process_action", {}).get("summary", "") if isinstance(desktop.get("latest_process_action", {}), dict) else "", limit=220),
        },
        "latest_command_result": {
            "command": _trim_text(desktop.get("latest_command_result", {}).get("command", "") if isinstance(desktop.get("latest_command_result", {}), dict) else "", limit=220),
            "shell_kind": _trim_text(desktop.get("latest_command_result", {}).get("shell_kind", "") if isinstance(desktop.get("latest_command_result", {}), dict) else "", limit=40),
            "exit_code": int(desktop.get("latest_command_result", {}).get("exit_code", 0) or 0) if isinstance(desktop.get("latest_command_result", {}), dict) else 0,
            "timed_out": bool(desktop.get("latest_command_result", {}).get("timed_out", False)) if isinstance(desktop.get("latest_command_result", {}), dict) else False,
            "summary": _trim_text(desktop.get("latest_command_result", {}).get("summary", "") if isinstance(desktop.get("latest_command_result", {}), dict) else "", limit=220),
        },
        "latest_processes": [
            {
                "pid": int(item.get("pid", 0) or 0),
                "process_name": _trim_text(item.get("process_name", ""), limit=120),
                "status": _trim_text(item.get("status", ""), limit=60),
                "owned": bool(item.get("owned", False)),
            }
            for item in list(desktop.get("latest_processes", []))[:4]
            if isinstance(item, dict)
        ],
    }


def _compact_session(session: Dict[str, Any] | None) -> Dict[str, Any]:
    session = session if isinstance(session, dict) else {}
    return {
        "session_id": _trim_text(session.get("session_id", ""), limit=60),
        "title": _trim_text(session.get("title", ""), limit=120),
        "status": _trim_text(session.get("status", "idle"), limit=40),
        "summary": _trim_text(session.get("summary", ""), limit=240),
        "current_task_id": _trim_text(session.get("current_task_id", ""), limit=60),
        "latest_run_id": _trim_text(session.get("latest_run_id", ""), limit=60),
        "pending_approval": _compact_pending(session.get("pending_approval", {})),
        "message_count": int(session.get("message_count", 0) or 0),
        "latest_message": session.get("latest_message", {}),
    }


def _compact_message(message: Dict[str, Any] | None) -> Dict[str, Any]:
    message = message if isinstance(message, dict) else {}
    return {
        "message_id": _trim_text(message.get("message_id", ""), limit=60),
        "created_at": _trim_text(message.get("created_at", ""), limit=40),
        "role": _trim_text(message.get("role", "assistant"), limit=20),
        "kind": _trim_text(message.get("kind", "message"), limit=40),
        "content": _trim_text(message.get("content", ""), limit=12000),
        "task_id": _trim_text(message.get("task_id", ""), limit=60),
        "run_id": _trim_text(message.get("run_id", ""), limit=60),
        "status": _trim_text(message.get("status", ""), limit=40),
    }


def _compact_alert(alert: Dict[str, Any] | None) -> Dict[str, Any]:
    alert = alert if isinstance(alert, dict) else {}
    return {
        "alert_id": _trim_text(alert.get("alert_id", ""), limit=60),
        "created_at": _trim_text(alert.get("created_at", ""), limit=40),
        "severity": _trim_text(alert.get("severity", ""), limit=20),
        "type": _trim_text(alert.get("type", ""), limit=60),
        "source": _trim_text(alert.get("source", ""), limit=60),
        "title": _trim_text(alert.get("title", ""), limit=120),
        "message": _trim_text(alert.get("message", ""), limit=240),
        "goal": _trim_text(alert.get("goal", ""), limit=180),
        "task_id": _trim_text(alert.get("task_id", ""), limit=60),
        "run_id": _trim_text(alert.get("run_id", ""), limit=60),
        "session_id": _trim_text(alert.get("session_id", ""), limit=80),
        "state_scope_id": _trim_text(alert.get("state_scope_id", ""), limit=120),
    }


def _compact_desktop_outcome(outcome: Dict[str, Any] | None) -> Dict[str, Any]:
    outcome = outcome if isinstance(outcome, dict) else {}
    return {
        "outcome": _trim_text(outcome.get("outcome", ""), limit=60),
        "status": _trim_text(outcome.get("status", ""), limit=40),
        "terminal": bool(outcome.get("terminal", False)),
        "reason": _trim_text(outcome.get("reason", ""), limit=60),
        "summary": _trim_text(outcome.get("summary", ""), limit=220),
        "scene_class": _trim_text(outcome.get("scene_class", ""), limit=40),
        "workflow_state": _trim_text(outcome.get("workflow_state", ""), limit=40),
        "readiness_state": _trim_text(outcome.get("readiness_state", ""), limit=40),
        "recovery_state": _trim_text(outcome.get("recovery_state", ""), limit=40),
        "recovery_reason": _trim_text(outcome.get("recovery_reason", ""), limit=60),
        "recovery_strategy": _trim_text(outcome.get("recovery_strategy", ""), limit=80),
        "attempt_count": int(outcome.get("attempt_count", 0) or 0),
        "max_attempts": int(outcome.get("max_attempts", 0) or 0),
    }


def _compact_snapshot(snapshot: Dict[str, Any] | None) -> Dict[str, Any]:
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    latest_run = snapshot.get("latest_run", {}) if isinstance(snapshot.get("latest_run", {}), dict) else {}
    return {
        "status": _trim_text(snapshot.get("status", "idle"), limit=40),
        "running": bool(snapshot.get("running", False)),
        "paused": bool(snapshot.get("paused", False)),
        "run_phase": _trim_text(snapshot.get("run_phase", "idle"), limit=40),
        "run_focus": _compact_run_focus(snapshot.get("run_focus", {})),
        "current_step": _trim_text(snapshot.get("current_step", ""), limit=160),
        "result_status": _trim_text(snapshot.get("result_status", ""), limit=40),
        "result_message": _trim_text(snapshot.get("result_message", ""), limit=240),
        "active_task": _compact_task(snapshot.get("active_task", {})),
        "pending_approval": _compact_pending(snapshot.get("pending_approval", {})),
        "browser": _compact_browser(snapshot.get("browser", {})),
        "desktop": _compact_desktop(snapshot.get("desktop", {})),
        "lifecycle": _compact_lifecycle(snapshot.get("lifecycle", {})),
        "latest_run": {
            "run_id": _trim_text(latest_run.get("run_id", ""), limit=60),
            "final_status": _trim_text(latest_run.get("final_status", ""), limit=40),
        },
    }

def _task_event_type(previous: Dict[str, Any], current: Dict[str, Any], snapshot: Dict[str, Any]) -> str:
    previous_status = str(previous.get("status", "")).strip()
    current_status = str(current.get("status", "")).strip()
    previous_task_id = str(previous.get("task_id", "")).strip()
    current_task_id = str(current.get("task_id", "")).strip()
    if current_status == "queued":
        return "task.queued"
    if current_status == "running":
        if previous_status == "paused":
            return "task.resumed"
        if current_task_id and current_task_id != previous_task_id:
            return "task.started"
        return "task.progress"
    if current_status == "paused":
        return "task.paused"
    if current_status == "completed":
        return "task.completed"
    if current_status in {"failed", "incomplete", "stopped"}:
        return "task.failed"
    if current_status in {"blocked", "needs_attention"}:
        return "task.blocked"
    if _trim_text(snapshot.get("current_step", ""), limit=160):
        return "task.progress"
    return "task.updated"


def _approval_event_type(message: Dict[str, Any]) -> str:
    content = str(message.get("content", "")).strip().lower()
    if "reject" in content:
        return "approval.rejected"
    if "approv" in content:
        return "approval.approved"
    return "approval.updated"


@dataclass
class _ReplayChannel:
    channel_key: str
    channel_id: str
    session_id: str
    state_scope_id: str
    buffer: Deque[Dict[str, Any]]
    sequence: int = 0
    subscribers: int = 0
    last_access_at: float = field(default_factory=time.monotonic)
    last_emit_at: float = field(default_factory=time.monotonic)
    latest_state: Dict[str, Any] = field(default_factory=dict)
    cursor: Dict[str, Any] = field(default_factory=dict)
    last_frame_at: float = field(default_factory=lambda: 0.0)
    last_frame_fingerprint: str = ""


class LocalApiEventStream:
    def __init__(
        self,
        controller,
        chat_manager,
        *,
        poll_seconds: float = DEFAULT_LOCAL_EVENT_POLL_SECONDS,
        heartbeat_seconds: float = DEFAULT_LOCAL_EVENT_HEARTBEAT_SECONDS,
        message_limit: int = DEFAULT_LOCAL_EVENT_MESSAGE_LIMIT,
        alert_limit: int = DEFAULT_LOCAL_EVENT_ALERT_LIMIT,
        replay_size: int = DEFAULT_LOCAL_EVENT_REPLAY_SIZE,
        channel_retention_seconds: float = DEFAULT_LOCAL_EVENT_CHANNEL_RETENTION_SECONDS,
        max_channels: int = DEFAULT_LOCAL_EVENT_MAX_CHANNELS,
        frame_min_seconds: float = DEFAULT_LOCAL_EVENT_FRAME_MIN_SECONDS,
    ):
        self.controller = controller
        self.chat_manager = chat_manager
        self.poll_seconds = max(0.25, float(poll_seconds))
        self.heartbeat_seconds = max(3.0, float(heartbeat_seconds))
        self.message_limit = max(8, int(message_limit))
        self.alert_limit = max(1, int(alert_limit))
        self.replay_size = max(2, int(replay_size))
        self.channel_retention_seconds = max(self.heartbeat_seconds, float(channel_retention_seconds))
        self.max_channels = max(4, int(max_channels))
        self.frame_min_seconds = max(0.1, float(frame_min_seconds))
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._channels: Dict[str, _ReplayChannel] = {}
        self._stop_event = threading.Event()
        self._worker = threading.Thread(target=self._run_publisher, name="local-api-events", daemon=True)
        self._worker.start()

    def shutdown(self):
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()
        try:
            self._worker.join(timeout=1.0)
        except Exception:
            pass

    def _channel_key(self, session_id: str, state_scope_id: str) -> str:
        safe_session_id = _trim_text(session_id, limit=80)
        safe_scope_id = _trim_text(state_scope_id, limit=120)
        if safe_scope_id:
            return f"scope:{safe_scope_id}"
        if safe_session_id:
            return f"session:{safe_session_id}"
        return "operator"

    def _channel_id(self, channel_key: str) -> str:
        return hashlib.sha1(channel_key.encode("utf-8")).hexdigest()[:12]

    def _event_sequence(self, event_id: str, *, expected_channel_id: str = "") -> int:
        text = _trim_text(event_id, limit=80)
        if not text or ":" not in text:
            return 0
        channel_id, sequence_text = text.rsplit(":", 1)
        if expected_channel_id and channel_id != expected_channel_id:
            return 0
        if not sequence_text.isdigit():
            return 0
        return int(sequence_text)

    def _public_event_payload(self, event: Dict[str, Any]) -> Dict[str, Any]:
        return {key: value for key, value in event.items() if key != "_sequence"}

    def _connection_event(self, event_name: str, *, session_id: str, state_scope_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "event": event_name,
            "session_id": session_id,
            "state_scope_id": state_scope_id,
            "emitted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "data": data,
        }

    def _frame_event_name(self, channel: _ReplayChannel) -> str:
        return "session.frame" if channel.session_id else "operator.frame"

    def _maybe_publish_frame_locked(
        self,
        channel: _ReplayChannel,
        *,
        state: Dict[str, Any],
        changed: List[str],
        critical: bool = False,
    ):
        snapshot = state.get("snapshot", {})
        alerts = state.get("alerts", [])
        frame_payload = {
            "session": state.get("session", {}),
            "snapshot": snapshot,
            "alerts": alerts[:3],
            "changed": [_trim_text(item, limit=40) for item in changed if _trim_text(item, limit=40)],
            "critical": bool(critical),
        }
        frame_fingerprint = _json_fingerprint(frame_payload)
        now = time.monotonic()
        if frame_fingerprint == channel.last_frame_fingerprint:
            return
        if not critical and channel.last_frame_at and (now - channel.last_frame_at) < self.frame_min_seconds:
            return
        self._publish_locked(channel, self._frame_event_name(channel), data=frame_payload)
        channel.last_frame_fingerprint = frame_fingerprint
        channel.last_frame_at = now

    def _state_cursor(self, state: Dict[str, Any]) -> Dict[str, Any]:
        snapshot = state.get("snapshot", {})
        return {
            "session": _json_fingerprint(state.get("session", {})),
            "snapshot": _json_fingerprint(
                {
                    "status": snapshot.get("status", "idle"),
                    "run_phase": snapshot.get("run_phase", "idle"),
                    "current_step": snapshot.get("current_step", ""),
                    "result_status": snapshot.get("result_status", ""),
                    "result_message": snapshot.get("result_message", ""),
                }
            ),
            "browser": _json_fingerprint(snapshot.get("browser", {})),
            "pending": _json_fingerprint(snapshot.get("pending_approval", {})),
            "task": snapshot.get("active_task", {}),
            "message_ids": [item.get("message_id", "") for item in state.get("messages", []) if item.get("message_id")],
            "alert_ids": [item.get("alert_id", "") for item in state.get("alerts", []) if item.get("alert_id")],
        }

    def _sync_payload(self, *, session_id: str, state_scope_id: str, state: Dict[str, Any], reason: str = "initial", replay_status: str = "fresh") -> Dict[str, Any]:
        data = {
            "session": state.get("session", {}),
            "snapshot": state.get("snapshot", {}),
            "alerts": state.get("alerts", [])[:3],
            "message_count": len(state.get("messages", [])),
            "reason": reason,
            "replay_status": replay_status,
        }
        event_name = "session.sync" if session_id else "operator.sync"
        return self._connection_event(event_name, session_id=session_id, state_scope_id=state_scope_id, data=data)

    def _read_state(self, *, session_id: str, state_scope_id: str) -> Dict[str, Any]:
        session_payload: Dict[str, Any] = {"ok": False, "session": {}, "messages": [], "snapshot": {}}
        if session_id:
            session_payload = self.chat_manager.get_stream_view(session_id, limit=self.message_limit)

        snapshot = session_payload.get("snapshot", {}) if session_payload.get("ok") else self.controller.get_snapshot(session_id=session_id, state_scope_id=state_scope_id)
        alerts = self.controller.get_alerts(limit=self.alert_limit, session_id=session_id, state_scope_id=state_scope_id)
        return {
            "session": _compact_session(session_payload.get("session", {})),
            "messages": [_compact_message(message) for message in session_payload.get("messages", [])[-self.message_limit :]],
            "snapshot": _compact_snapshot(snapshot),
            "alerts": [_compact_alert(alert) for alert in alerts.get("items", [])[: self.alert_limit]],
        }

    def _cleanup_stale_channels_locked(self, now: float):
        stale_keys = [
            channel_key
            for channel_key, channel in self._channels.items()
            if channel.subscribers <= 0 and (now - channel.last_access_at) > self.channel_retention_seconds
        ]
        for channel_key in stale_keys:
            self._channels.pop(channel_key, None)

    def _ensure_channel_locked(self, session_id: str, state_scope_id: str) -> _ReplayChannel:
        now = time.monotonic()
        self._cleanup_stale_channels_locked(now)
        channel_key = self._channel_key(session_id, state_scope_id)
        channel = self._channels.get(channel_key)
        if channel is None:
            if len(self._channels) >= self.max_channels:
                removable = sorted(
                    (item for item in self._channels.values() if item.subscribers <= 0),
                    key=lambda item: item.last_access_at,
                )
                if removable:
                    self._channels.pop(removable[0].channel_key, None)
            channel = _ReplayChannel(
                channel_key=channel_key,
                channel_id=self._channel_id(channel_key),
                session_id=_trim_text(session_id, limit=80),
                state_scope_id=_trim_text(state_scope_id, limit=120),
                buffer=deque(maxlen=self.replay_size),
            )
            self._channels[channel_key] = channel
        channel.last_access_at = now
        return channel

    def _bootstrap_channel_locked(self, channel: _ReplayChannel, *, state: Dict[str, Any] | None = None) -> Dict[str, Any]:
        effective_state = state if isinstance(state, dict) else (channel.latest_state or self._read_state(session_id=channel.session_id, state_scope_id=channel.state_scope_id))
        channel.latest_state = effective_state
        if not channel.cursor:
            channel.cursor = self._state_cursor(effective_state)
        channel.last_access_at = time.monotonic()
        return effective_state

    def _publish_locked(self, channel: _ReplayChannel, event_name: str, *, data: Dict[str, Any]) -> Dict[str, Any]:
        channel.sequence += 1
        event = {
            "event": event_name,
            "event_id": f"{channel.channel_id}:{channel.sequence}",
            "session_id": channel.session_id,
            "state_scope_id": channel.state_scope_id,
            "emitted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "data": data,
            "_sequence": channel.sequence,
        }
        channel.buffer.append(event)
        channel.last_emit_at = time.monotonic()
        self._condition.notify_all()
        return self._public_event_payload(event)

    def _replay_after_locked(self, channel: _ReplayChannel, last_event_id: str) -> tuple[str, List[Dict[str, Any]]]:
        requested = _trim_text(last_event_id, limit=80)
        if not requested:
            return "fresh", []
        requested_sequence = self._event_sequence(requested, expected_channel_id=channel.channel_id)
        if requested_sequence <= 0:
            return "missing", []
        if not channel.buffer:
            return "stale", []
        oldest_sequence = int(channel.buffer[0].get("_sequence", 0))
        newest_sequence = int(channel.buffer[-1].get("_sequence", 0))
        if requested_sequence < oldest_sequence:
            return "stale", []
        if requested_sequence > newest_sequence:
            return "missing", []
        replayed = [self._public_event_payload(event) for event in channel.buffer if int(event.get("_sequence", 0)) > requested_sequence]
        return "ok", replayed

    def _apply_state_to_channel_locked(self, channel: _ReplayChannel, state: Dict[str, Any]):
        effective_state = self._bootstrap_channel_locked(channel, state=state)
        cursor = channel.cursor
        snapshot = effective_state.get("snapshot", {})
        changed_sections: List[str] = []
        critical_frame = False

        current_session = effective_state.get("session", {})
        session_fp = _json_fingerprint(current_session)
        if session_fp != cursor.get("session", ""):
            cursor["session"] = session_fp
            self._publish_locked(channel, "session.updated", data={"session": current_session})
            changed_sections.append("session")

        current_messages = effective_state.get("messages", [])
        current_message_ids = [item.get("message_id", "") for item in current_messages if item.get("message_id")]
        seen_message_ids = set(cursor.get("message_ids", []))
        for message in current_messages:
            message_id = message.get("message_id", "")
            if not message_id or message_id in seen_message_ids:
                continue
            self._publish_locked(channel, "session.message", data={"message": message})
            if message.get("kind") == "approval":
                self._publish_locked(channel, _approval_event_type(message), data={"message": message})
                critical_frame = True
            changed_sections.append("messages")
        cursor["message_ids"] = current_message_ids[-self.message_limit :]

        current_pending = snapshot.get("pending_approval", {})
        pending_fp = _json_fingerprint(current_pending)
        if pending_fp != cursor.get("pending", ""):
            cursor["pending"] = pending_fp
            self._publish_locked(
                channel,
                "approval.needed" if current_pending.get("kind") else "approval.cleared",
                data={"pending_approval": current_pending},
            )
            changed_sections.append("pending_approval")
            critical_frame = True

        current_task = snapshot.get("active_task", {})
        previous_task = cursor.get("task", {}) if isinstance(cursor.get("task", {}), dict) else {}
        previous_phase = _trim_text(cursor.get("run_phase", ""), limit=40)
        current_phase = _trim_text(snapshot.get("run_phase", "idle"), limit=40)
        if _json_fingerprint(current_task) != _json_fingerprint(previous_task):
            self._publish_locked(
                channel,
                _task_event_type(previous_task, current_task, snapshot),
                data={
                    "task": current_task,
                    "status": snapshot.get("status", "idle"),
                    "run_phase": snapshot.get("run_phase", "idle"),
                    "run_focus": snapshot.get("run_focus", {}),
                    "current_step": snapshot.get("current_step", ""),
                    "result_status": snapshot.get("result_status", ""),
                    "result_message": snapshot.get("result_message", ""),
                },
            )
            cursor["task"] = current_task
            changed_sections.append("task")
            critical_frame = critical_frame or current_phase != previous_phase or str(current_task.get("status", "")).strip() in {"paused", "completed", "failed", "blocked", "incomplete"}
        elif current_task.get("status") == "running":
            snapshot_fp = _json_fingerprint(
                {
                    "run_phase": snapshot.get("run_phase", "idle"),
                    "current_step": snapshot.get("current_step", ""),
                    "result_status": snapshot.get("result_status", ""),
                    "result_message": snapshot.get("result_message", ""),
                }
            )
            if snapshot_fp != cursor.get("snapshot", ""):
                self._publish_locked(
                    channel,
                    "task.progress",
                    data={
                        "task": current_task,
                        "status": snapshot.get("status", "idle"),
                        "run_phase": snapshot.get("run_phase", "idle"),
                        "run_focus": snapshot.get("run_focus", {}),
                        "current_step": snapshot.get("current_step", ""),
                        "result_status": snapshot.get("result_status", ""),
                        "result_message": snapshot.get("result_message", ""),
                    },
                )
                changed_sections.append("task_progress")
                critical_frame = critical_frame or current_phase != previous_phase

        browser_fp = _json_fingerprint(snapshot.get("browser", {}))
        if browser_fp != cursor.get("browser", ""):
            cursor["browser"] = browser_fp
            self._publish_locked(channel, "browser.workflow", data={"browser": snapshot.get("browser", {})})
            changed_sections.append("browser")

        desktop_fp = _json_fingerprint(snapshot.get("desktop", {}))
        if desktop_fp != cursor.get("desktop", ""):
            cursor["desktop"] = desktop_fp
            self._publish_locked(channel, "desktop.state", data={"desktop": snapshot.get("desktop", {})})
            changed_sections.append("desktop")

        current_alerts = effective_state.get("alerts", [])
        current_alert_ids = [item.get("alert_id", "") for item in current_alerts if item.get("alert_id")]
        seen_alert_ids = set(cursor.get("alert_ids", []))
        for alert in current_alerts:
            alert_id = alert.get("alert_id", "")
            if not alert_id or alert_id in seen_alert_ids:
                continue
            self._publish_locked(channel, "alert", data={"alert": alert})
            changed_sections.append("alerts")
            critical_frame = True
        cursor["alert_ids"] = current_alert_ids[-self.alert_limit :]

        cursor["snapshot"] = _json_fingerprint(
            {
                "status": snapshot.get("status", "idle"),
                "run_phase": snapshot.get("run_phase", "idle"),
                "current_step": snapshot.get("current_step", ""),
                "result_status": snapshot.get("result_status", ""),
                "result_message": snapshot.get("result_message", ""),
            }
        )
        cursor["run_phase"] = current_phase
        if changed_sections:
            self._maybe_publish_frame_locked(
                channel,
                state=effective_state,
                changed=changed_sections,
                critical=critical_frame,
            )
        channel.latest_state = effective_state
        channel.last_access_at = time.monotonic()

    def _run_publisher(self):
        while not self._stop_event.wait(self.poll_seconds):
            with self._condition:
                self._cleanup_stale_channels_locked(time.monotonic())
                channels = list(self._channels.values())
            for channel in channels:
                if self._stop_event.is_set():
                    return
                try:
                    state = self._read_state(session_id=channel.session_id, state_scope_id=channel.state_scope_id)
                except Exception:
                    continue
                with self._condition:
                    current = self._channels.get(channel.channel_key)
                    if current is not channel:
                        continue
                    self._apply_state_to_channel_locked(channel, state)

    def iter_events(self, *, session_id: str = "", state_scope_id: str = "", last_event_id: str = "") -> Iterator[Dict[str, Any]]:
        safe_session_id = _trim_text(session_id, limit=80)
        safe_scope_id = _trim_text(state_scope_id, limit=120)
        with self._condition:
            channel = self._ensure_channel_locked(safe_session_id, safe_scope_id)
            state = self._bootstrap_channel_locked(channel)
            channel.subscribers += 1
            replay_status, replay_events = self._replay_after_locked(channel, last_event_id)
            current_sequence = int(channel.buffer[-1].get("_sequence", 0)) if channel.buffer else 0
            last_seen_sequence = current_sequence
            if replay_status == "ok":
                parsed_sequence = self._event_sequence(last_event_id, expected_channel_id=channel.channel_id)
                last_seen_sequence = parsed_sequence or current_sequence
                if replay_events:
                    last_seen_sequence = self._event_sequence(replay_events[-1].get("event_id", ""), expected_channel_id=channel.channel_id)

        try:
            yield self._connection_event(
                "stream.hello",
                session_id=safe_session_id,
                state_scope_id=safe_scope_id,
                data={
                    "session_id": safe_session_id,
                    "state_scope_id": safe_scope_id,
                    "stream": "session" if safe_session_id else "operator",
                    "replay_status": replay_status,
                },
            )

            if replay_status == "ok":
                for event in replay_events:
                    yield event
            else:
                if _trim_text(last_event_id, limit=80):
                    reason = "too_old" if replay_status == "stale" else "missing"
                    yield self._connection_event(
                        "stream.reset",
                        session_id=safe_session_id,
                        state_scope_id=safe_scope_id,
                        data={
                            "reason": reason,
                            "requested_last_event_id": _trim_text(last_event_id, limit=80),
                        },
                    )
                yield self._sync_payload(
                    session_id=safe_session_id,
                    state_scope_id=safe_scope_id,
                    state=state,
                    reason="reconnect" if _trim_text(last_event_id, limit=80) else "initial",
                    replay_status=replay_status,
                )

            while not self._stop_event.is_set():
                pending_events: List[Dict[str, Any]] = []
                reset_event: Dict[str, Any] | None = None
                sync_event: Dict[str, Any] | None = None
                heartbeat_event: Dict[str, Any] | None = None
                with self._condition:
                    if self._stop_event.is_set():
                        break
                    channel = self._channels.get(channel.channel_key)
                    if channel is None:
                        channel = self._ensure_channel_locked(safe_session_id, safe_scope_id)
                        state = self._bootstrap_channel_locked(channel)
                        last_seen_sequence = int(channel.buffer[-1].get("_sequence", 0)) if channel.buffer else 0
                        reset_event = self._connection_event(
                            "stream.reset",
                            session_id=safe_session_id,
                            state_scope_id=safe_scope_id,
                            data={"reason": "expired"},
                        )
                        sync_event = self._sync_payload(
                            session_id=safe_session_id,
                            state_scope_id=safe_scope_id,
                            state=state,
                            reason="expired",
                            replay_status="missing",
                        )
                    else:
                        channel.last_access_at = time.monotonic()
                        buffer_items = list(channel.buffer)
                        if buffer_items:
                            oldest_sequence = int(buffer_items[0].get("_sequence", 0))
                            if last_seen_sequence and last_seen_sequence < oldest_sequence:
                                state = self._bootstrap_channel_locked(channel)
                                last_seen_sequence = int(buffer_items[-1].get("_sequence", 0))
                                reset_event = self._connection_event(
                                    "stream.reset",
                                    session_id=safe_session_id,
                                    state_scope_id=safe_scope_id,
                                    data={"reason": "too_old"},
                                )
                                sync_event = self._sync_payload(
                                    session_id=safe_session_id,
                                    state_scope_id=safe_scope_id,
                                    state=state,
                                    reason="reconnect",
                                    replay_status="stale",
                                )
                            else:
                                pending_events = [
                                    self._public_event_payload(event)
                                    for event in buffer_items
                                    if int(event.get("_sequence", 0)) > last_seen_sequence
                                ]
                                if pending_events:
                                    last_seen_sequence = self._event_sequence(
                                        pending_events[-1].get("event_id", ""),
                                        expected_channel_id=channel.channel_id,
                                    )
                        if not reset_event and not pending_events:
                            state = channel.latest_state or self._bootstrap_channel_locked(channel)
                            woke = self._condition.wait(timeout=self.heartbeat_seconds)
                            if not woke and not self._stop_event.is_set():
                                heartbeat_event = self._connection_event(
                                    "stream.heartbeat",
                                    session_id=safe_session_id,
                                    state_scope_id=safe_scope_id,
                                    data={"status": state.get("snapshot", {}).get("status", "idle")},
                                )
                if reset_event is not None:
                    yield reset_event
                if sync_event is not None:
                    yield sync_event
                if pending_events:
                    for event in pending_events:
                        yield event
                if heartbeat_event is not None:
                    yield heartbeat_event
        finally:
            with self._condition:
                channel = self._channels.get(self._channel_key(safe_session_id, safe_scope_id))
                if channel is not None:
                    channel.subscribers = max(0, channel.subscribers - 1)
                    channel.last_access_at = time.monotonic()
                self._condition.notify_all()

