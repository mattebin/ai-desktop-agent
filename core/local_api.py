from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, unquote, urlparse

from core.chat_sessions import (
    ChatSessionManager,
    DEFAULT_CHAT_SESSION_STATE_PATH,
    DEFAULT_MAX_CHAT_MESSAGES,
    DEFAULT_MAX_CHAT_SESSIONS,
)
from core.agent import Agent
from core.operator_controller import OperatorController
from core.local_api_events import (
    DEFAULT_LOCAL_EVENT_CHANNEL_RETENTION_SECONDS,
    DEFAULT_LOCAL_EVENT_HEARTBEAT_SECONDS,
    DEFAULT_LOCAL_EVENT_MAX_CHANNELS,
    DEFAULT_LOCAL_EVENT_POLL_SECONDS,
    DEFAULT_LOCAL_EVENT_REPLAY_SIZE,
    LocalApiEventStream,
)
from core.desktop_evidence import compact_evidence_preview, get_desktop_evidence_store


DEFAULT_LOCAL_API_HOST = "127.0.0.1"
DEFAULT_LOCAL_API_PORT = 8765
LOCAL_API_ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
LOCAL_API_ALLOWED_CLIENTS = {"127.0.0.1", "::1"}
LOCAL_API_ALLOWED_WEB_ORIGINS = {
    "tauri://localhost",
    "app://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://asset.localhost",
    "https://asset.localhost",
    "http://app.localhost",
    "https://app.localhost",
    "http://localhost",
    "https://localhost",
    "http://127.0.0.1",
    "https://127.0.0.1",
}
LOCAL_API_ALLOWED_WEB_SCHEMES = {"http", "https", "tauri", "app"}
LOCAL_API_ALLOWED_WEB_HOSTS = {"localhost", "127.0.0.1", "::1", "tauri.localhost", "asset.localhost", "app.localhost"}


def _management_payload() -> Dict[str, Any]:
    managed_flag = str(os.environ.get("AI_OPERATOR_DESKTOP_MANAGED", "")).strip() == "1"
    owner_token = _trim_text(os.environ.get("AI_OPERATOR_DESKTOP_OWNER_TOKEN", ""), limit=160)
    owner_pid_raw = str(os.environ.get("AI_OPERATOR_DESKTOP_OWNER_PID", "")).strip()
    owner_pid = _coerce_int(owner_pid_raw, 0, minimum=0, maximum=2**31 - 1) if owner_pid_raw else 0
    return {
        "managed_by_desktop": managed_flag,
        "owner_token": owner_token,
        "owner_pid": owner_pid,
        "api_pid": os.getpid(),
    }


def _desktop_shutdown_allowed(body: Dict[str, Any] | None) -> tuple[bool, str]:
    management = _management_payload()
    if not management.get("managed_by_desktop", False):
        return False, "This local API process is not desktop-managed."
    payload = body if isinstance(body, dict) else {}
    requested_token = _trim_text(payload.get("owner_token", ""), limit=160)
    requested_pid = _coerce_int(payload.get("owner_pid", 0), 0, minimum=0, maximum=2**31 - 1)
    if not requested_token or requested_pid <= 0:
        return False, "Desktop shutdown requires the current owner token and owner pid."
    if requested_token != management.get("owner_token", "") or requested_pid != management.get("owner_pid", 0):
        return False, "Desktop shutdown ownership did not match the running local API process."
    return True, ""


def _trim_text(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _coerce_int(value: Any, default: int, *, minimum: int = 0, maximum: int = 1_048_576) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _coerce_host(host: str | None) -> str:
    normalized = str(host or "").strip() or DEFAULT_LOCAL_API_HOST
    if normalized not in LOCAL_API_ALLOWED_HOSTS:
        raise ValueError(f"Local API host must stay local-only: {normalized}")
    return normalized


def _allowed_web_origin(origin: str | None) -> str:
    normalized = str(origin or "").strip()
    if normalized in LOCAL_API_ALLOWED_WEB_ORIGINS:
        return normalized
    if not normalized:
        return ""
    try:
        parsed = urlparse(normalized)
    except Exception:
        return ""
    scheme = str(parsed.scheme or "").lower()
    hostname = str(parsed.hostname or "").lower()
    if scheme in LOCAL_API_ALLOWED_WEB_SCHEMES and hostname in LOCAL_API_ALLOWED_WEB_HOSTS:
        return normalized
    return ""


def _status_payload(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    queue = snapshot.get("queue", {})
    pending = snapshot.get("pending_approval", {})
    browser = snapshot.get("browser", {})
    desktop = snapshot.get("desktop", {})
    behavior = snapshot.get("behavior", {}) if isinstance(snapshot.get("behavior", {}), dict) else {}
    return {
        "status": str(snapshot.get("status", "")).strip() or "idle",
        "running": bool(snapshot.get("running", False)),
        "paused": bool(snapshot.get("paused", False)),
        "run_phase": _trim_text(snapshot.get("run_phase", "idle"), limit=40),
        "run_focus": snapshot.get("run_focus", {}),
        "current_step": _trim_text(snapshot.get("current_step", ""), limit=140),
        "goal": _trim_text(snapshot.get("goal", ""), limit=240),
        "result_status": _trim_text(snapshot.get("result_status", ""), limit=80),
        "result_message": _trim_text(snapshot.get("result_message", ""), limit=280),
        "mode": _trim_text(behavior.get("mode", ""), limit=80),
        "task_phase": _trim_text(behavior.get("task_phase", ""), limit=80),
        "pending_approval": {
            "kind": _trim_text(pending.get("kind", ""), limit=80),
            "reason": _trim_text(pending.get("reason", ""), limit=180),
            "summary": _trim_text(pending.get("summary", ""), limit=180),
            "step": _trim_text(pending.get("step", ""), limit=120),
            "tool": _trim_text(pending.get("tool", ""), limit=120),
            "target": _trim_text(pending.get("target", ""), limit=180),
            "approval_status": _trim_text(pending.get("approval_status", ""), limit=40),
            "evidence_id": _trim_text(pending.get("evidence_id", ""), limit=80),
            "evidence_summary": _trim_text(pending.get("evidence_summary", ""), limit=220),
            "evidence_preview": _compact_evidence_payload(pending.get("evidence_preview", {})),
            "evidence_assessment": _compact_evidence_assessment(pending.get("evidence_assessment", {})),
            "scene_preview": _compact_scene_payload(pending.get("scene_preview", {})),
            "vision_preview": _compact_vision_payload(pending.get("vision_preview", {})),
        },
        "active_task": snapshot.get("active_task", {}),
        "browser": {
            "task_name": _trim_text(browser.get("task_name", ""), limit=80),
            "task_step": _trim_text(browser.get("task_step", ""), limit=120),
            "task_status": _trim_text(browser.get("task_status", ""), limit=80),
            "workflow_name": _trim_text(browser.get("workflow_name", ""), limit=80),
            "workflow_step": _trim_text(browser.get("workflow_step", ""), limit=120),
            "workflow_status": _trim_text(browser.get("workflow_status", ""), limit=80),
            "current_title": _trim_text(browser.get("current_title", ""), limit=120),
            "current_url": _trim_text(browser.get("current_url", ""), limit=200),
        },
        "desktop": {
            "active_window_title": _trim_text(desktop.get("active_window_title", ""), limit=160),
            "active_window_process": _trim_text(desktop.get("active_window_process", ""), limit=120),
            "last_action": _trim_text(desktop.get("last_action", ""), limit=180),
            "last_target_window": _trim_text(desktop.get("last_target_window", ""), limit=160),
            "checkpoint_pending": bool(desktop.get("checkpoint_pending", False)),
            "checkpoint_tool": _trim_text(desktop.get("checkpoint_tool", ""), limit=80),
            "checkpoint_reason": _trim_text(desktop.get("checkpoint_reason", ""), limit=180),
            "screenshot_path": _trim_text(desktop.get("screenshot_path", ""), limit=220),
            "evidence_id": _trim_text(desktop.get("evidence_id", ""), limit=80),
            "evidence_summary": _trim_text(desktop.get("evidence_summary", ""), limit=220),
            "evidence_bundle_path": _trim_text(desktop.get("evidence_bundle_path", ""), limit=260),
            "checkpoint_evidence_id": _trim_text(desktop.get("checkpoint_evidence_id", ""), limit=80),
            "selected_evidence": _compact_evidence_payload(desktop.get("selected_evidence", {})),
            "selected_evidence_assessment": _compact_evidence_assessment(desktop.get("selected_evidence_assessment", {})),
            "selected_scene": _compact_scene_payload(desktop.get("selected_scene", {})),
            "selected_vision": _compact_vision_payload(desktop.get("selected_vision", {})),
            "selected_target_proposals": _compact_target_proposal_context(desktop.get("selected_target_proposals", {})),
            "checkpoint_evidence": _compact_evidence_payload(desktop.get("checkpoint_evidence", {})),
            "checkpoint_evidence_assessment": _compact_evidence_assessment(desktop.get("checkpoint_evidence_assessment", {})),
            "checkpoint_scene": _compact_scene_payload(desktop.get("checkpoint_scene", {})),
            "checkpoint_vision": _compact_vision_payload(desktop.get("checkpoint_vision", {})),
            "checkpoint_target_proposals": _compact_target_proposal_context(desktop.get("checkpoint_target_proposals", {})),
            "run_outcome": _compact_desktop_outcome(desktop.get("run_outcome", {})),
            "latest_mouse_action": _compact_mouse_action(desktop.get("latest_mouse_action", {})),
            "latest_process_action": _compact_process_action(desktop.get("latest_process_action", {})),
            "latest_command_result": _compact_command_result(desktop.get("latest_command_result", {})),
            "latest_processes": [_compact_process_preview(item) for item in list(desktop.get("latest_processes", []))[:4] if isinstance(item, dict)],
            "recent_context_evidence": [_compact_evidence_payload(item) for item in list(desktop.get("recent_context_evidence", []))[:3] if isinstance(item, dict)],
        },
        "queue_counts": queue.get("counts", {}),
        "latest_alert": snapshot.get("latest_alert", {}),
        "latest_run": snapshot.get("latest_run", {}),
        "lifecycle": snapshot.get("lifecycle", {}),
        "runtime": snapshot.get("runtime", {}),
        "infrastructure": snapshot.get("infrastructure", {}),
        "behavior": behavior,
        "human_control": snapshot.get("human_control", {}),
        "action_policy": snapshot.get("action_policy", {}),
        "task_control": snapshot.get("task_control", {}),
    }


def _active_task_payload(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "active_task": snapshot.get("active_task", {}),
        "pending_approval": snapshot.get("pending_approval", {}),
        "run_phase": _trim_text(snapshot.get("run_phase", "idle"), limit=40),
        "run_focus": snapshot.get("run_focus", {}),
        "browser": snapshot.get("browser", {}),
        "desktop": snapshot.get("desktop", {}),
        "lifecycle": snapshot.get("lifecycle", {}),
        "current_step": _trim_text(snapshot.get("current_step", ""), limit=160),
        "result_status": _trim_text(snapshot.get("result_status", ""), limit=80),
        "result_message": _trim_text(snapshot.get("result_message", ""), limit=280),
        "behavior": snapshot.get("behavior", {}),
        "human_control": snapshot.get("human_control", {}),
        "task_control": snapshot.get("task_control", {}),
        "infrastructure": snapshot.get("infrastructure", {}),
    }


def _queue_payload(queue_state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "counts": queue_state.get("counts", {}),
        "active_task": queue_state.get("active_task", {}),
        "queued_tasks": queue_state.get("queued_tasks", []),
        "recent_tasks": queue_state.get("recent_tasks", []),
        "can_start_next": bool(queue_state.get("can_start_next", False)),
    }


def _scheduled_payload(scheduled_state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "counts": scheduled_state.get("counts", {}),
        "tasks": scheduled_state.get("tasks", []),
    }


def _watch_payload(watch_state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "counts": watch_state.get("counts", {}),
        "tasks": watch_state.get("tasks", []),
    }


def _compact_evidence_payload(value: Dict[str, Any] | None) -> Dict[str, Any]:
    preview = compact_evidence_preview(value if isinstance(value, dict) else {})
    return {
        "evidence_id": _trim_text(preview.get("evidence_id", ""), limit=80),
        "timestamp": _trim_text(preview.get("timestamp", ""), limit=40),
        "evidence_kind": _trim_text(preview.get("evidence_kind", ""), limit=60),
        "reason": _trim_text(preview.get("reason", ""), limit=40),
        "summary": _trim_text(preview.get("summary", ""), limit=220),
        "active_window_title": _trim_text(preview.get("active_window_title", ""), limit=180),
        "active_window_process": _trim_text(preview.get("active_window_process", ""), limit=120),
        "target_window_title": _trim_text(preview.get("target_window_title", ""), limit=180),
        "has_screenshot": bool(preview.get("has_screenshot", False)),
        "has_artifact": bool(preview.get("has_artifact", False)),
        "screenshot_scope": _trim_text(preview.get("screenshot_scope", ""), limit=60),
        "screenshot_backend": _trim_text(preview.get("screenshot_backend", ""), limit=60),
        "monitor_count": _coerce_int(preview.get("monitor_count", 0), 0, minimum=0, maximum=16),
        "primary_monitor_label": _trim_text(preview.get("primary_monitor_label", ""), limit=120),
        "screen_capture_policy": _trim_text(preview.get("screen_capture_policy", ""), limit=60),
        "ui_evidence_present": bool(preview.get("ui_evidence_present", False)),
        "ui_control_count": _coerce_int(preview.get("ui_control_count", 0), 0, minimum=0, maximum=128),
        "is_partial": bool(preview.get("is_partial", False)),
        "recency_seconds": _coerce_int(preview.get("recency_seconds", 0), 0, minimum=0, maximum=10_000_000),
        "selection_reason": _trim_text(preview.get("selection_reason", ""), limit=40),
        "capture_mode": _trim_text(preview.get("capture_mode", ""), limit=40),
        "importance": _trim_text(preview.get("importance", ""), limit=40),
        "importance_reason": _trim_text(preview.get("importance_reason", ""), limit=120),
        "task_id": _trim_text(preview.get("task_id", ""), limit=60),
        "task_status": _trim_text(preview.get("task_status", ""), limit=40),
        "checkpoint_pending": bool(preview.get("checkpoint_pending", False)),
    }


def _compact_evidence_assessment(value: Dict[str, Any] | None) -> Dict[str, Any]:
    assessment = value if isinstance(value, dict) else {}
    return {
        "evidence_id": _trim_text(assessment.get("evidence_id", ""), limit=80),
        "purpose": _trim_text(assessment.get("purpose", ""), limit=80),
        "state": _trim_text(assessment.get("state", ""), limit=40),
        "reason": _trim_text(assessment.get("reason", ""), limit=40),
        "summary": _trim_text(assessment.get("summary", ""), limit=220),
        "sufficient": bool(assessment.get("sufficient", False)),
        "needs_refresh": bool(assessment.get("needs_refresh", False)),
        "target_window_title": _trim_text(assessment.get("target_window_title", ""), limit=180),
        "target_window_match": bool(assessment.get("target_window_match", False)),
        "has_screenshot": bool(assessment.get("has_screenshot", False)),
        "is_partial": bool(assessment.get("is_partial", False)),
        "recency_seconds": _coerce_int(assessment.get("recency_seconds", 0), 0, minimum=0, maximum=10_000_000),
        "stale": bool(assessment.get("stale", False)),
    }


def _compact_vision_payload(value: Dict[str, Any] | None) -> Dict[str, Any]:
    vision = value if isinstance(value, dict) else {}
    images = []
    for item in list(vision.get("images", []))[:2]:
        if not isinstance(item, dict):
            continue
        images.append(
            {
                "evidence_id": _trim_text(item.get("evidence_id", ""), limit=80),
                "role": _trim_text(item.get("role", ""), limit=40),
                "summary": _trim_text(item.get("summary", ""), limit=180),
                "artifact_available": bool(item.get("artifact_available", False)),
            }
        )
    return {
        "purpose": _trim_text(vision.get("purpose", ""), limit=60),
        "mode": _trim_text(vision.get("mode", ""), limit=40),
        "reason": _trim_text(vision.get("reason", ""), limit=40),
        "summary": _trim_text(vision.get("summary", ""), limit=220),
        "needs_direct_image": bool(vision.get("needs_direct_image", False)),
        "image_count": _coerce_int(vision.get("image_count", len(images)), len(images), minimum=0, maximum=2),
        "primary_evidence_id": _trim_text(vision.get("primary_evidence_id", ""), limit=80),
        "comparison_evidence_id": _trim_text(vision.get("comparison_evidence_id", ""), limit=80),
        "images": images,
    }


def _compact_mouse_action(value: Dict[str, Any] | None) -> Dict[str, Any]:
    action = value if isinstance(value, dict) else {}
    return {
        "action": _trim_text(action.get("action", ""), limit=40),
        "button": _trim_text(action.get("button", ""), limit=20),
        "click_count": _coerce_int(action.get("click_count", 0), 0, minimum=0, maximum=4),
        "coordinate_mode": _trim_text(action.get("coordinate_mode", ""), limit=40),
        "mapping_reason": _trim_text(action.get("mapping_reason", ""), limit=80),
        "monitor": _trim_text(action.get("monitor", ""), limit=120),
        "point": _trim_text(action.get("point", ""), limit=80),
        "summary": _trim_text(action.get("summary", ""), limit=220),
    }


def _compact_target_proposal(value: Dict[str, Any] | None) -> Dict[str, Any]:
    proposal = value if isinstance(value, dict) else {}
    actions = [
        _trim_text(item, limit=80)
        for item in list(proposal.get("suggested_next_actions", []))[:3]
        if _trim_text(item, limit=80)
    ]
    point = proposal.get("point", {}) if isinstance(proposal.get("point", {}), dict) else {}
    region = proposal.get("region", {}) if isinstance(proposal.get("region", {}), dict) else {}
    return {
        "target_id": _trim_text(proposal.get("target_id", ""), limit=120),
        "target_kind": _trim_text(proposal.get("target_kind", ""), limit=40),
        "window_title": _trim_text(proposal.get("window_title", ""), limit=180),
        "window_process": _trim_text(proposal.get("window_process", ""), limit=120),
        "source_evidence_id": _trim_text(proposal.get("source_evidence_id", ""), limit=80),
        "confidence": _trim_text(proposal.get("confidence", ""), limit=20),
        "confidence_score": _coerce_int(proposal.get("confidence_score", 0), 0, minimum=0, maximum=100),
        "reason": _trim_text(proposal.get("reason", ""), limit=60),
        "summary": _trim_text(proposal.get("summary", ""), limit=220),
        "approval_required": bool(proposal.get("approval_required", False)),
        "suggested_next_actions": actions,
        "point": {
            "x": _coerce_int(point.get("x", 0), 0, minimum=-100_000, maximum=100_000),
            "y": _coerce_int(point.get("y", 0), 0, minimum=-100_000, maximum=100_000),
        },
        "region": {
            "x": _coerce_int(region.get("x", 0), 0, minimum=-100_000, maximum=100_000),
            "y": _coerce_int(region.get("y", 0), 0, minimum=-100_000, maximum=100_000),
            "width": _coerce_int(region.get("width", 0), 0, minimum=0, maximum=100_000),
            "height": _coerce_int(region.get("height", 0), 0, minimum=0, maximum=100_000),
        },
        "coordinate_mode": _trim_text(proposal.get("coordinate_mapping", {}).get("mode", "") if isinstance(proposal.get("coordinate_mapping", {}), dict) else "", limit=40),
        "mapping_reason": _trim_text(proposal.get("coordinate_mapping", {}).get("reason", "") if isinstance(proposal.get("coordinate_mapping", {}), dict) else "", limit=80),
    }


def _compact_target_proposal_context(value: Dict[str, Any] | None) -> Dict[str, Any]:
    context = value if isinstance(value, dict) else {}
    return {
        "purpose": _trim_text(context.get("purpose", ""), limit=60),
        "state": _trim_text(context.get("state", ""), limit=40),
        "reason": _trim_text(context.get("reason", ""), limit=60),
        "summary": _trim_text(context.get("summary", ""), limit=220),
        "confidence": _trim_text(context.get("confidence", ""), limit=20),
        "confidence_score": _coerce_int(context.get("confidence_score", 0), 0, minimum=0, maximum=100),
        "proposal_count": _coerce_int(context.get("proposal_count", 0), 0, minimum=0, maximum=4),
        "scene_class": _trim_text(context.get("scene_class", ""), limit=40),
        "workflow_state": _trim_text(context.get("workflow_state", ""), limit=40),
        "readiness_state": _trim_text(context.get("readiness_state", ""), limit=40),
        "target_window_title": _trim_text(context.get("target_window_title", ""), limit=180),
        "pending_tool": _trim_text(context.get("pending_tool", ""), limit=80),
        "checkpoint_pending": bool(context.get("checkpoint_pending", False)),
        "target_match_score": _coerce_int(context.get("target_match_score", 0), 0, minimum=0, maximum=100),
        "proposer_names": [_trim_text(item, limit=60) for item in list(context.get("proposer_names", []))[:6] if _trim_text(item, limit=60)],
        "proposals": [_compact_target_proposal(item) for item in list(context.get("proposals", []))[:3] if isinstance(item, dict)],
    }


def _compact_process_preview(value: Dict[str, Any] | None) -> Dict[str, Any]:
    preview = value if isinstance(value, dict) else {}
    return {
        "pid": _coerce_int(preview.get("pid", 0), 0, minimum=0, maximum=10_000_000),
        "process_name": _trim_text(preview.get("process_name", ""), limit=120),
        "status": _trim_text(preview.get("status", ""), limit=60),
        "owned": bool(preview.get("owned", False)),
    }


def _compact_process_action(value: Dict[str, Any] | None) -> Dict[str, Any]:
    action = value if isinstance(value, dict) else {}
    return {
        "action": _trim_text(action.get("action", ""), limit=40),
        "pid": _coerce_int(action.get("pid", 0), 0, minimum=0, maximum=10_000_000),
        "process_name": _trim_text(action.get("process_name", ""), limit=120),
        "owned": bool(action.get("owned", False)),
        "owned_label": _trim_text(action.get("owned_label", ""), limit=120),
        "summary": _trim_text(action.get("summary", ""), limit=220),
    }


def _compact_command_result(value: Dict[str, Any] | None) -> Dict[str, Any]:
    result = value if isinstance(value, dict) else {}
    return {
        "command": _trim_text(result.get("command", ""), limit=220),
        "shell_kind": _trim_text(result.get("shell_kind", ""), limit=40),
        "exit_code": _coerce_int(result.get("exit_code", 0), 0, minimum=-1_000_000, maximum=1_000_000),
        "timed_out": bool(result.get("timed_out", False)),
        "stdout_excerpt": _trim_text(result.get("stdout_excerpt", ""), limit=220),
        "stderr_excerpt": _trim_text(result.get("stderr_excerpt", ""), limit=220),
        "summary": _trim_text(result.get("summary", ""), limit=220),
    }


def _compact_scene_payload(value: Dict[str, Any] | None) -> Dict[str, Any]:
    scene = value if isinstance(value, dict) else {}
    return {
        "scene_class": _trim_text(scene.get("scene_class", ""), limit=40),
        "app_class": _trim_text(scene.get("app_class", ""), limit=40),
        "workflow_state": _trim_text(scene.get("workflow_state", ""), limit=40),
        "readiness_state": _trim_text(scene.get("readiness_state", ""), limit=40),
        "presentation": _trim_text(scene.get("presentation", ""), limit=40),
        "confidence": _trim_text(scene.get("confidence", ""), limit=20),
        "confidence_score": _coerce_int(scene.get("confidence_score", 0), 0, minimum=0, maximum=100),
        "reason": _trim_text(scene.get("reason", ""), limit=40),
        "summary": _trim_text(scene.get("summary", ""), limit=220),
        "transition_summary": _trim_text(scene.get("transition_summary", ""), limit=220),
        "scene_changed": bool(scene.get("scene_changed", False)),
        "change_reason": _trim_text(scene.get("change_reason", ""), limit=40),
        "direct_image_helpful": bool(scene.get("direct_image_helpful", False)),
        "prefer_before_after": bool(scene.get("prefer_before_after", False)),
        "pending_tool": _trim_text(scene.get("pending_tool", ""), limit=80),
    }


def _compact_desktop_outcome(value: Dict[str, Any] | None) -> Dict[str, Any]:
    outcome = value if isinstance(value, dict) else {}
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
        "attempt_count": _coerce_int(outcome.get("attempt_count", 0), 0, minimum=0, maximum=16),
        "max_attempts": _coerce_int(outcome.get("max_attempts", 0), 0, minimum=0, maximum=16),
        "evidence_id": _trim_text(outcome.get("evidence_id", ""), limit=80),
        "target_window_title": _trim_text(outcome.get("target_window_title", ""), limit=180),
        "active_window_title": _trim_text(outcome.get("active_window_title", ""), limit=180),
    }


def _desktop_evidence_payload(limit: int = 8) -> Dict[str, Any]:
    store = get_desktop_evidence_store()
    return {
        "recent": store.recent_refs(limit=limit),
        "recent_summaries": [_compact_evidence_payload(item) for item in store.recent_summaries(limit=limit)],
        "status": store.status_snapshot(),
    }


def _desktop_evidence_selection_payload(parsed) -> Dict[str, Any]:
    query = parse_qs(parsed.query)
    store = get_desktop_evidence_store()
    result = store.select_summary(
        strategy=str(query.get("strategy", ["latest"])[0]).strip(),
        evidence_id=str(query.get("evidence_id", [""])[0]).strip(),
        observation_token=str(query.get("observation_token", [""])[0]).strip(),
        active_window_title=str(query.get("active_window_title", [""])[0]).strip(),
        target_window_title=str(query.get("target_window_title", [""])[0]).strip(),
        checkpoint_evidence_id=str(query.get("checkpoint_evidence_id", [""])[0]).strip(),
        checkpoint_target=str(query.get("checkpoint_target", [""])[0]).strip(),
        task_evidence_id=str(query.get("task_evidence_id", [""])[0]).strip(),
    )
    return {
        "strategy": _trim_text(result.get("strategy", ""), limit=60),
        "reason": _trim_text(result.get("reason", ""), limit=40),
        "candidate_count": _coerce_int(result.get("candidate_count", 0), 0, minimum=0, maximum=10_000),
        "selected": _compact_evidence_payload(result.get("selected", {})),
    }


def _desktop_evidence_artifact_payload(evidence_id: str, *, content_path: str = "") -> Dict[str, Any]:
    store = get_desktop_evidence_store()
    metadata = store.artifact_metadata(evidence_id, content_path=content_path)
    return {
        "artifact": metadata,
    }


class LocalOperatorApiServer:
    def __init__(
        self,
        controller: OperatorController | None = None,
        *,
        host: str | None = None,
        port: int | None = None,
        settings: Dict[str, Any] | None = None,
    ):
        self.settings = settings if isinstance(settings, dict) else {}
        self.controller = controller or OperatorController(agent=Agent(settings=self.settings), settings=self.settings)
        self.chat_manager = ChatSessionManager(
            controller=self.controller,
            path=self.settings.get("chat_session_state_path", DEFAULT_CHAT_SESSION_STATE_PATH),
            max_sessions=_coerce_int(self.settings.get("max_chat_sessions", DEFAULT_MAX_CHAT_SESSIONS), DEFAULT_MAX_CHAT_SESSIONS, minimum=1, maximum=100),
            max_messages=_coerce_int(self.settings.get("max_chat_messages_per_session", DEFAULT_MAX_CHAT_MESSAGES), DEFAULT_MAX_CHAT_MESSAGES, minimum=8, maximum=120),
        )
        self.event_stream = LocalApiEventStream(
            controller=self.controller,
            chat_manager=self.chat_manager,
            poll_seconds=float(self.settings.get("local_api_event_poll_seconds", DEFAULT_LOCAL_EVENT_POLL_SECONDS) or DEFAULT_LOCAL_EVENT_POLL_SECONDS),
            heartbeat_seconds=float(self.settings.get("local_api_event_heartbeat_seconds", DEFAULT_LOCAL_EVENT_HEARTBEAT_SECONDS) or DEFAULT_LOCAL_EVENT_HEARTBEAT_SECONDS),
            replay_size=int(self.settings.get("local_api_event_replay_size", DEFAULT_LOCAL_EVENT_REPLAY_SIZE) or DEFAULT_LOCAL_EVENT_REPLAY_SIZE),
            channel_retention_seconds=float(self.settings.get("local_api_event_channel_retention_seconds", DEFAULT_LOCAL_EVENT_CHANNEL_RETENTION_SECONDS) or DEFAULT_LOCAL_EVENT_CHANNEL_RETENTION_SECONDS),
            max_channels=int(self.settings.get("local_api_event_max_channels", DEFAULT_LOCAL_EVENT_MAX_CHANNELS) or DEFAULT_LOCAL_EVENT_MAX_CHANNELS),
        )
        self.host = _coerce_host(host)
        self.port = int(DEFAULT_LOCAL_API_PORT if port is None else port)
        self._server = ThreadingHTTPServer((self.host, self.port), self._build_handler())
        self._server.daemon_threads = True
        self.port = int(self._server.server_address[1])

    def _build_handler(self):
        server_ref = self

        class LocalApiHandler(BaseHTTPRequestHandler):
            server_version = "AIDesktopAgentLocalAPI/1.2"

            def log_message(self, format: str, *args: Any):
                return

            def handle(self):
                try:
                    super().handle()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return

            def _client_allowed(self) -> bool:
                return str(self.client_address[0]).strip() in LOCAL_API_ALLOWED_CLIENTS

            def _send_json(self, status_code: int, payload: Dict[str, Any]):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(body)

            def _send_file(self, path: Path, *, content_type: str):
                data = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(data)

            def _send_sse_headers(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self._send_cors_headers()
                self.end_headers()

            def _send_cors_headers(self):
                origin = _allowed_web_origin(self.headers.get("Origin", ""))
                if origin:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Last-Event-ID")

            def _send_sse_event(self, payload: Dict[str, Any]):
                event_name = _trim_text(payload.get("event", "message"), limit=80) or "message"
                event_id = _trim_text(payload.get("event_id", ""), limit=80)
                body = json.dumps(payload, ensure_ascii=False)
                chunks = [f"event: {event_name}\n"]
                if event_id:
                    chunks.append(f"id: {event_id}\n")
                for line in body.splitlines() or [body]:
                    chunks.append(f"data: {line}\n")
                chunks.append("\n")
                self.wfile.write("".join(chunks).encode("utf-8"))
                self.wfile.flush()
            def _read_json_body(self) -> Dict[str, Any]:
                length = _coerce_int(self.headers.get("Content-Length", "0"), 0)
                if length <= 0:
                    return {}
                raw_body = self.rfile.read(length)
                if not raw_body:
                    return {}
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                except Exception as exc:
                    raise ValueError("Request body must be valid JSON.") from exc
                if not isinstance(payload, dict):
                    raise ValueError("Request body must be a JSON object.")
                return payload

            def _respond_ok(self, data: Dict[str, Any]):
                self._send_json(200, {"ok": True, "data": data})

            def _respond_error(self, status_code: int, message: str):
                self._send_json(status_code, {"ok": False, "error": _trim_text(message, limit=280)})

            def _query_limit(self, parsed, default: int, maximum: int) -> int:
                values = parse_qs(parsed.query).get("limit", [])
                return _coerce_int(values[0] if values else default, default, minimum=1, maximum=maximum)

            def _goal_from_body(self, body: Dict[str, Any]) -> str:
                goal = str(body.get("goal", "")).strip()
                if not goal:
                    raise ValueError("Field 'goal' is required.")
                return goal

            def _message_from_body(self, body: Dict[str, Any]) -> str:
                message = str(body.get("message", body.get("content", ""))).strip()
                if not message:
                    raise ValueError("Field 'message' is required.")
                return message

            def _session_filters(self, *, parsed=None, body: Dict[str, Any] | None = None) -> tuple[str, str]:
                query = parse_qs(parsed.query) if parsed is not None else {}
                raw_session_id = ""
                raw_state_scope_id = ""
                if isinstance(body, dict):
                    raw_session_id = body.get("session_id", body.get("conversation_id", ""))
                    raw_state_scope_id = body.get("state_scope_id", "")
                if query.get("session_id", []):
                    raw_session_id = query.get("session_id", [raw_session_id])[0]
                if query.get("state_scope_id", []):
                    raw_state_scope_id = query.get("state_scope_id", [raw_state_scope_id])[0]
                return (_trim_text(raw_session_id, limit=80), _trim_text(raw_state_scope_id, limit=120))

            def _last_event_id(self, *, parsed=None) -> str:
                query = parse_qs(parsed.query) if parsed is not None else {}
                raw_last_event_id = self.headers.get("Last-Event-ID", "")
                if query.get("last_event_id", []):
                    raw_last_event_id = query.get("last_event_id", [raw_last_event_id])[0]
                return _trim_text(raw_last_event_id, limit=80)

            def _path_segments(self, path: str) -> list[str]:
                return [unquote(segment) for segment in str(path or "").split("/") if segment]

            def _handle_session_get(self, segments: list[str], parsed):
                if len(segments) == 1:
                    limit = self._query_limit(parsed, default=10, maximum=50)
                    self._respond_ok(server_ref.chat_manager.list_sessions(limit=limit))
                    return True

                session_id = segments[1] if len(segments) >= 2 else ""
                if not session_id:
                    self._respond_error(400, "Session id is required.")
                    return True

                if len(segments) == 2:
                    result = server_ref.chat_manager.get_session(session_id)
                    if result.get("ok"):
                        self._respond_ok(result)
                    else:
                        self._respond_error(404, result.get("message", "Unknown session."))
                    return True

                if len(segments) == 3 and segments[2] == "messages":
                    limit = self._query_limit(parsed, default=20, maximum=60)
                    result = server_ref.chat_manager.get_session_messages(session_id, limit=limit)
                    if result.get("ok"):
                        self._respond_ok(result)
                    else:
                        self._respond_error(404, result.get("message", "Unknown session."))
                    return True

                return False

            def _handle_session_post(self, segments: list[str], body: Dict[str, Any]):
                if len(segments) == 1:
                    title = _trim_text(body.get("title", ""), limit=120)
                    message = str(body.get("message", body.get("content", ""))).strip()
                    result = server_ref.chat_manager.create_session(title=title, message=message)
                    if result.get("ok"):
                        self._respond_ok(result)
                    else:
                        self._respond_error(400, result.get("message", "Unable to create session."))
                    return True

                session_id = segments[1] if len(segments) >= 2 else ""
                if len(segments) == 3 and segments[2] == "messages":
                    result = server_ref.chat_manager.send_message(session_id, self._message_from_body(body))
                    if result.get("ok"):
                        self._respond_ok(result)
                    elif "Unknown session" in str(result.get("message", "")):
                        self._respond_error(404, result.get("message", "Unknown session."))
                    else:
                        self._respond_error(400, result.get("message", "Unable to send message."))
                    return True

                return False

            def _handle_get(self, path: str, parsed):
                segments = self._path_segments(path)
                if segments and segments[0] == "sessions" and self._handle_session_get(segments, parsed):
                    return

                if path == "/health":
                    runtime = server_ref.controller.get_runtime_config()
                    self._respond_ok(
                        {
                            "service": "ai-desktop-agent-local-api",
                            "host": server_ref.host,
                            "port": server_ref.port,
                            "local_only": True,
                            "pid": os.getpid(),
                            "runtime": runtime,
                            "management": _management_payload(),
                        }
                    )
                    return

                if path == "/events/stream":
                    session_id, state_scope_id = self._session_filters(parsed=parsed)
                    last_event_id = self._last_event_id(parsed=parsed)
                    self._send_sse_headers()
                    try:
                        for payload in server_ref.event_stream.iter_events(session_id=session_id, state_scope_id=state_scope_id, last_event_id=last_event_id):
                            self._send_sse_event(payload)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        return
                    except Exception as exc:
                        try:
                            self._send_sse_event(
                                {
                                    "event": "stream.error",
                                    "event_id": "evt-error",
                                    "session_id": session_id,
                                    "state_scope_id": state_scope_id,
                                    "data": {"message": _trim_text(exc, limit=220)},
                                }
                            )
                        except Exception:
                            pass
                    return

                if path == "/status":
                    session_id, state_scope_id = self._session_filters(parsed=parsed)
                    self._respond_ok(_status_payload(server_ref.controller.get_snapshot(session_id=session_id, state_scope_id=state_scope_id)))
                    return

                if path == "/snapshot":
                    session_id, state_scope_id = self._session_filters(parsed=parsed)
                    self._respond_ok(server_ref.controller.get_snapshot(session_id=session_id, state_scope_id=state_scope_id))
                    return

                if path == "/tasks/active":
                    session_id, state_scope_id = self._session_filters(parsed=parsed)
                    self._respond_ok(_active_task_payload(server_ref.controller.get_snapshot(session_id=session_id, state_scope_id=state_scope_id)))
                    return

                if path == "/runs/recent":
                    limit = self._query_limit(parsed, default=6, maximum=25)
                    session_id, state_scope_id = self._session_filters(parsed=parsed)
                    self._respond_ok({"items": server_ref.controller.get_recent_runs(limit=limit, session_id=session_id, state_scope_id=state_scope_id)})
                    return

                if path == "/alerts":
                    limit = self._query_limit(parsed, default=12, maximum=40)
                    session_id, state_scope_id = self._session_filters(parsed=parsed)
                    self._respond_ok(server_ref.controller.get_alerts(limit=limit, session_id=session_id, state_scope_id=state_scope_id))
                    return

                if path == "/queue":
                    self._respond_ok(_queue_payload(server_ref.controller.get_queue_state()))
                    return

                if path == "/scheduled":
                    self._respond_ok(_scheduled_payload(server_ref.controller.get_scheduled_state()))
                    return

                if path == "/watches":
                    self._respond_ok(_watch_payload(server_ref.controller.get_watch_state()))
                    return

                if path == "/desktop/evidence":
                    limit = self._query_limit(parsed, default=8, maximum=24)
                    self._respond_ok(_desktop_evidence_payload(limit=limit))
                    return

                if path == "/desktop/evidence/selected":
                    self._respond_ok(_desktop_evidence_selection_payload(parsed))
                    return

                evidence_segments = self._path_segments(path)
                if len(evidence_segments) == 5 and evidence_segments[0] == "desktop" and evidence_segments[1] == "evidence" and evidence_segments[3] == "artifact" and evidence_segments[4] == "content":
                    evidence_id = unquote(evidence_segments[2])
                    artifact_file = get_desktop_evidence_store().artifact_file_path(evidence_id)
                    if not artifact_file:
                        self._respond_error(404, f"Desktop evidence artifact is unavailable: {evidence_id}")
                        return
                    metadata = get_desktop_evidence_store().artifact_metadata(evidence_id)
                    content_type = _trim_text(metadata.get("artifact_type", ""), limit=80) or "application/octet-stream"
                    try:
                        self._send_file(artifact_file, content_type=content_type)
                    except FileNotFoundError:
                        self._respond_error(404, f"Desktop evidence artifact is unavailable: {evidence_id}")
                    return

                if len(evidence_segments) == 4 and evidence_segments[0] == "desktop" and evidence_segments[1] == "evidence" and evidence_segments[3] == "artifact":
                    evidence_id = unquote(evidence_segments[2])
                    self._respond_ok(
                        _desktop_evidence_artifact_payload(
                            evidence_id,
                            content_path=f"/desktop/evidence/{evidence_id}/artifact/content",
                        )
                    )
                    return

                if len(evidence_segments) == 3 and evidence_segments[0] == "desktop" and evidence_segments[1] == "evidence":
                    evidence_id = unquote(evidence_segments[2])
                    bundle = get_desktop_evidence_store().load_bundle(evidence_id)
                    if not bundle:
                        self._respond_error(404, f"Desktop evidence not found: {evidence_id}")
                        return
                    self._respond_ok({"bundle": bundle})
                    return

                self._respond_error(404, f"Unknown endpoint: {path}")

            def _handle_post(self, path: str):
                body = self._read_json_body()
                segments = self._path_segments(path)
                if segments and segments[0] == "sessions" and self._handle_session_post(segments, body):
                    return

                if path == "/goals/start":
                    goal = self._goal_from_body(body)
                    session_id, state_scope_id = self._session_filters(body=body)
                    result = server_ref.controller.start_goal(goal, session_id=session_id, state_scope_id=state_scope_id)
                    if result.get("ok"):
                        self._respond_ok({"result": result, "status": _status_payload(server_ref.controller.get_snapshot(session_id=session_id, state_scope_id=state_scope_id))})
                    else:
                        self._respond_error(400, result.get("message", "Unable to start goal."))
                    return

                if path == "/goals/queue":
                    goal = self._goal_from_body(body)
                    session_id, state_scope_id = self._session_filters(body=body)
                    result = server_ref.controller.enqueue_goal(goal, session_id=session_id, state_scope_id=state_scope_id)
                    if result.get("ok"):
                        self._respond_ok({"result": result, "queue": _queue_payload(server_ref.controller.get_queue_state())})
                    else:
                        self._respond_error(400, result.get("message", "Unable to queue goal."))
                    return

                if path == "/goals/replace":
                    goal = self._goal_from_body(body)
                    session_id, state_scope_id = self._session_filters(body=body)
                    result = server_ref.controller.replace_goal(goal, session_id=session_id, state_scope_id=state_scope_id)
                    if result.get("ok"):
                        self._respond_ok({"result": result, "status": _status_payload(server_ref.controller.get_snapshot(session_id=session_id, state_scope_id=state_scope_id))})
                    else:
                        self._respond_error(400, result.get("message", "Unable to replace the current goal."))
                    return

                if path == "/tasks/stop":
                    session_id, state_scope_id = self._session_filters(body=body)
                    result = server_ref.controller.stop_task(session_id=session_id, state_scope_id=state_scope_id)
                    if result.get("ok"):
                        self._respond_ok({"result": result, "status": _status_payload(server_ref.controller.get_snapshot(session_id=session_id, state_scope_id=state_scope_id))})
                    else:
                        self._respond_error(400, result.get("message", "Unable to stop the current task."))
                    return

                if path == "/tasks/defer":
                    session_id, state_scope_id = self._session_filters(body=body)
                    result = server_ref.controller.defer_task(session_id=session_id, state_scope_id=state_scope_id)
                    if result.get("ok"):
                        self._respond_ok({"result": result, "status": _status_payload(server_ref.controller.get_snapshot(session_id=session_id, state_scope_id=state_scope_id))})
                    else:
                        self._respond_error(400, result.get("message", "Unable to defer the current task."))
                    return

                if path == "/tasks/resume":
                    session_id, state_scope_id = self._session_filters(body=body)
                    result = server_ref.controller.resume_task(session_id=session_id, state_scope_id=state_scope_id)
                    if result.get("ok"):
                        self._respond_ok({"result": result, "status": _status_payload(server_ref.controller.get_snapshot(session_id=session_id, state_scope_id=state_scope_id))})
                    else:
                        self._respond_error(400, result.get("message", "Unable to resume the task."))
                    return

                if path == "/tasks/retry":
                    session_id, state_scope_id = self._session_filters(body=body)
                    result = server_ref.controller.retry_task(session_id=session_id, state_scope_id=state_scope_id)
                    if result.get("ok"):
                        self._respond_ok({"result": result, "status": _status_payload(server_ref.controller.get_snapshot(session_id=session_id, state_scope_id=state_scope_id))})
                    else:
                        self._respond_error(400, result.get("message", "Unable to retry the task."))
                    return

                if path == "/approval/approve":
                    session_id, state_scope_id = self._session_filters(body=body)
                    before_snapshot = server_ref.controller.get_snapshot(session_id=session_id, state_scope_id=state_scope_id)
                    result = server_ref.controller.approve_pending(session_id=session_id, state_scope_id=state_scope_id)
                    session_update = server_ref.chat_manager.record_approval_action(
                        True,
                        result,
                        session_id=session_id,
                        before_snapshot=before_snapshot,
                    )
                    if result.get("ok"):
                        self._respond_ok({"result": result, "status": _status_payload(server_ref.controller.get_snapshot(session_id=session_id, state_scope_id=state_scope_id)), "session": session_update.get("session", {})})
                    else:
                        self._respond_error(400, result.get("message", "Unable to approve pending action."))
                    return

                if path == "/approval/reject":
                    session_id, state_scope_id = self._session_filters(body=body)
                    before_snapshot = server_ref.controller.get_snapshot(session_id=session_id, state_scope_id=state_scope_id)
                    result = server_ref.controller.reject_pending(session_id=session_id, state_scope_id=state_scope_id)
                    session_update = server_ref.chat_manager.record_approval_action(
                        False,
                        result,
                        session_id=session_id,
                        before_snapshot=before_snapshot,
                    )
                    if result.get("ok"):
                        self._respond_ok({"result": result, "status": _status_payload(server_ref.controller.get_snapshot(session_id=session_id, state_scope_id=state_scope_id)), "session": session_update.get("session", {})})
                    else:
                        self._respond_error(400, result.get("message", "Unable to reject pending action."))
                    return

                if path == "/shutdown":
                    allowed, message = _desktop_shutdown_allowed(body)
                    if not allowed:
                        self._respond_error(403, message)
                        return
                    self._respond_ok({"accepted": True, "management": _management_payload()})
                    threading.Timer(0.05, server_ref.shutdown).start()
                    return

                self._respond_error(404, f"Unknown endpoint: {path}")

            def do_GET(self):
                if not self._client_allowed():
                    self._respond_error(403, "Local API accepts loopback requests only.")
                    return
                parsed = urlparse(self.path)
                self._handle_get(parsed.path.rstrip("/") or "/", parsed)

            def do_POST(self):
                if not self._client_allowed():
                    self._respond_error(403, "Local API accepts loopback requests only.")
                    return
                parsed = urlparse(self.path)
                try:
                    self._handle_post(parsed.path.rstrip("/") or "/")
                except ValueError as exc:
                    self._respond_error(400, str(exc))

            def do_OPTIONS(self):
                if not self._client_allowed():
                    self._respond_error(403, "Local API accepts loopback requests only.")
                    return
                self.send_response(204)
                self._send_cors_headers()
                self.end_headers()

        return LocalApiHandler

    def serve_forever(self):
        try:
            print(f"[LOCAL API] Listening on http://{self.host}:{self.port}")
            self._server.serve_forever()
        finally:
            self.shutdown()

    def start_in_thread(self):
        thread = threading.Thread(target=self.serve_forever, name="local-operator-api", daemon=True)
        thread.start()
        return thread

    def shutdown(self):
        try:
            self._server.shutdown()
        except Exception:
            pass
        try:
            self._server.server_close()
        except Exception:
            pass
        try:
            self.event_stream.shutdown()
        except Exception:
            pass
        shutdown = getattr(self.controller, "shutdown", None)
        if callable(shutdown):
            shutdown()
        try:
            from tools.browser import shutdown_browser_runtime

            shutdown_browser_runtime()
        except Exception:
            pass
        try:
            from tools.desktop import shutdown_desktop_runtime

            shutdown_desktop_runtime()
        except Exception:
            pass


def serve_local_api(*, host: str | None = None, port: int | None = None, settings: Dict[str, Any] | None = None):
    server = LocalOperatorApiServer(host=host, port=port, settings=settings)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[LOCAL API] Stopping local API server.")
    finally:
        server.shutdown()

