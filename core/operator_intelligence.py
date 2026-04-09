from __future__ import annotations

import hashlib
import json
import platform
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List

from core.capability_profiles import normalize_execution_profile
from core.lab_shell import lab_status_snapshot
from core.problem_records import ProblemRecordStore, build_problem_record
from core.windows_opening import StrategyExplorationInventory, choose_windows_open_strategy


DEFAULT_OPERATOR_MEMORY_PATH = Path(__file__).resolve().parents[1] / "data" / "operator_memory.json"
_FAIL_LIKE_OUTCOMES = {"failure", "uncertain", "blocked", "no_progress"}
_MAX_HEURISTIC_ENTRIES = 120
_MAX_HINT_ITEMS = 2
_MAX_LESSON_ENTRIES = 24


def _trim_text(value: Any, *, limit: int = 220) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in {0, "", None}:
        return False
    return bool(value)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _normalized_lines(value: Any, *, limit: int = 4, text_limit: int = 180) -> List[str]:
    if not isinstance(value, list):
        return []
    items: List[str] = []
    for raw in value:
        text = _trim_text(raw, limit=text_limit)
        if not text or text in items:
            continue
        items.append(text)
        if len(items) >= limit:
            break
    return items


def _stable_hash(payload: Any) -> str:
    try:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
    except Exception:
        encoded = repr(payload).encode("utf-8", errors="replace")
    return hashlib.sha1(encoded).hexdigest()[:12]


def action_domain(tool_name: str) -> str:
    text = str(tool_name or "").strip().lower()
    if text.startswith("desktop_"):
        return "desktop"
    if text.startswith("email_"):
        return "gmail"
    if text == "lab_run_shell":
        return "lab"
    if text.startswith("browser_"):
        return "browser"
    if text.startswith("schedule_") or text.startswith("watch_"):
        return "automation"
    return "general"


def _command_looks_like_open_intent(command: str) -> bool:
    lowered = " ".join(str(command or "").strip().lower().split())
    if not lowered:
        return False
    return any(
        token in lowered
        for token in (
            " start ",
            " start-process",
            " invoke-item",
            " notepad",
            " explorer ",
            ".txt",
            ".pdf",
            ".doc",
            ".docx",
            ".png",
            ".jpg",
            ".jpeg",
            ".xlsx",
            ".ppt",
            ".pptx",
        )
    ) or lowered.startswith("start ") or lowered.startswith("ii ")


def _compact_args(args: Dict[str, Any]) -> Dict[str, str]:
    if not isinstance(args, dict):
        return {}
    keys = [
        "title",
        "window_id",
        "field_label",
        "value",
        "url",
        "expected_url_contains",
        "expected_title_contains",
        "expected_text_contains",
        "path",
        "query",
        "thread_id",
        "draft_id",
        "subject",
        "command",
        "shell_kind",
        "cwd",
        "condition_type",
        "target",
    ]
    payload: Dict[str, str] = {}
    for key in keys:
        raw = args.get(key, "")
        if isinstance(raw, list):
            text = ",".join(_trim_text(item, limit=60) for item in raw if _trim_text(item, limit=60))
        elif isinstance(raw, dict):
            text = _trim_text(json.dumps(raw, sort_keys=True, default=str), limit=120)
        else:
            text = _trim_text(raw, limit=120)
        if text:
            payload[key] = text
    if not payload and args:
        payload["hash"] = _stable_hash(args)
    return payload


def build_action_signature(tool_name: str, args: Dict[str, Any] | None = None) -> str:
    compact_args = _compact_args(args if isinstance(args, dict) else {})
    parts = [str(tool_name or "").strip().lower()]
    for key in sorted(compact_args):
        parts.append(f"{key}={compact_args[key]}")
    return "::".join(parts)


def _infer_target_signature(
    tool_name: str,
    args: Dict[str, Any],
    result: Dict[str, Any],
    before_context: Dict[str, Any],
    after_context: Dict[str, Any],
) -> str:
    domain = action_domain(tool_name)
    if domain == "desktop":
        explicit_target_signature = _trim_text(
            (
                result.get("desktop_strategy", {}) if isinstance(result.get("desktop_strategy", {}), dict) else {}
            ).get("target_signature", "")
            or args.get("target_signature", ""),
            limit=220,
        ).lower()
        if explicit_target_signature:
            return explicit_target_signature
        if tool_name == "desktop_open_target":
            open_target = result.get("open_target", {}) if isinstance(result.get("open_target", {}), dict) else {}
            value = _trim_text(
                open_target.get("target_signature", "")
                or open_target.get("normalized_target", "")
                or args.get("target", ""),
                limit=220,
            ).lower()
            return value or _trim_text(after_context.get("active_window_title", ""), limit=80)
        parts = [
            _trim_text(args.get("title", ""), limit=80),
            _trim_text(args.get("window_id", ""), limit=40),
            _trim_text(before_context.get("desktop_target_window_title", ""), limit=80),
            _trim_text(after_context.get("desktop_target_window_title", ""), limit=80),
            _trim_text(before_context.get("active_window_process", ""), limit=60),
            _trim_text(after_context.get("active_window_process", ""), limit=60),
        ]
        value = "|".join(part for part in parts if part)
        return value or _trim_text(after_context.get("active_window_title", ""), limit=80)
    if domain == "gmail":
        parts = [
            _trim_text(args.get("thread_id", ""), limit=80),
            _trim_text(args.get("draft_id", ""), limit=80),
            _trim_text(result.get("thread", {}).get("thread_id", "") if isinstance(result.get("thread", {}), dict) else "", limit=80),
            _trim_text(result.get("draft", {}).get("thread_id", "") if isinstance(result.get("draft", {}), dict) else "", limit=80),
            _trim_text(result.get("subject", ""), limit=120),
        ]
        return "|".join(part for part in parts if part) or _trim_text(args.get("query", ""), limit=120)
    if domain == "lab":
        parts = [
            _trim_text(result.get("shell_kind", "") or args.get("shell_kind", ""), limit=20),
            _trim_text((result.get("policy", {}) if isinstance(result.get("policy", {}), dict) else {}).get("intent", ""), limit=40),
            _trim_text(result.get("command", "") or args.get("command", ""), limit=120),
        ]
        return "|".join(part for part in parts if part)
    if domain == "browser":
        parts = [
            _trim_text(args.get("url", ""), limit=120),
            _trim_text(args.get("expected_url_contains", ""), limit=120),
            _trim_text(after_context.get("browser_current_url", ""), limit=120),
            _trim_text(args.get("selector", ""), limit=120),
        ]
        return "|".join(part for part in parts if part)
    return _trim_text(args.get("path", "") or args.get("target", "") or args.get("query", ""), limit=120)


def capture_action_context(task_state, tool_name: str = "", args: Dict[str, Any] | None = None) -> Dict[str, Any]:
    snapshot = task_state.get_control_snapshot() if hasattr(task_state, "get_control_snapshot") else {}
    desktop = snapshot.get("desktop", {}) if isinstance(snapshot.get("desktop", {}), dict) else {}
    lab = snapshot.get("lab", {}) if isinstance(snapshot.get("lab", {}), dict) else {}
    browser = snapshot.get("browser", {}) if isinstance(snapshot.get("browser", {}), dict) else {}
    pending = snapshot.get("pending_approval", {}) if isinstance(snapshot.get("pending_approval", {}), dict) else {}
    selected_target = (
        desktop.get("selected_target_proposals", {})
        if isinstance(desktop.get("selected_target_proposals", {}), dict)
        else {}
    )
    environment = getattr(task_state, "_environment_awareness", {})
    if not isinstance(environment, dict):
        environment = {}

    return {
        "captured_at": _iso_now(),
        "tool": str(tool_name or "").strip(),
        "args": _compact_args(args if isinstance(args, dict) else {}),
        "status": _trim_text(getattr(task_state, "status", ""), limit=40),
        "active_window_title": _trim_text(desktop.get("active_window_title", ""), limit=180),
        "active_window_process": _trim_text(desktop.get("active_window_process", ""), limit=80),
        "desktop_target_window_title": _trim_text(selected_target.get("target_window_title", ""), limit=180),
        "desktop_evidence_id": _trim_text(desktop.get("evidence_id", ""), limit=80),
        "desktop_selected_target_state": _trim_text(selected_target.get("state", ""), limit=40),
        "desktop_selected_target_reason": _trim_text(selected_target.get("reason", ""), limit=120),
        "browser_current_url": _trim_text(browser.get("current_url", ""), limit=220),
        "browser_current_title": _trim_text(browser.get("current_title", ""), limit=180),
        "pending_approval_kind": _trim_text(pending.get("kind", ""), limit=40),
        "lab_command": _trim_text(lab.get("command", ""), limit=220),
        "lab_workspace_id": _trim_text(lab.get("workspace_id", ""), limit=80),
        "execution_profile": _trim_text(getattr(task_state, "execution_profile", ""), limit=80),
        "gmail_authenticated": bool(environment.get("gmail_authenticated", False)),
        "lab_armed": bool(environment.get("lab_armed", False)),
    }


def _result_summary(result: Dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return ""
    return _trim_text(
        result.get("summary", "")
        or result.get("message", "")
        or result.get("error", ""),
        limit=220,
    )


def _previous_attempts(task_state, action_signature: str) -> int:
    if not action_signature:
        return 0
    count = 0
    for step in reversed(list(getattr(task_state, "steps", []))):
        if step.get("type") != "tool":
            continue
        result = step.get("result", {}) if isinstance(step.get("result", {}), dict) else {}
        evaluation = result.get("evaluation", {}) if isinstance(result.get("evaluation", {}), dict) else {}
        if str(evaluation.get("action_signature", "")).strip() != action_signature:
            continue
        count += 1
    return count


def _previous_failures(task_state, action_signature: str) -> int:
    if not action_signature:
        return 0
    count = 0
    for step in reversed(list(getattr(task_state, "steps", []))):
        if step.get("type") != "tool":
            continue
        result = step.get("result", {}) if isinstance(step.get("result", {}), dict) else {}
        evaluation = result.get("evaluation", {}) if isinstance(result.get("evaluation", {}), dict) else {}
        if str(evaluation.get("action_signature", "")).strip() != action_signature:
            continue
        if str(evaluation.get("status", "")).strip() in _FAIL_LIKE_OUTCOMES:
            count += 1
    return count


def _read_only_desktop_tool(tool_name: str) -> bool:
    return str(tool_name or "").strip() in {
        "desktop_list_windows",
        "desktop_get_active_window",
        "desktop_inspect_window_state",
        "desktop_capture_screenshot",
        "desktop_list_processes",
        "desktop_inspect_process",
        "desktop_wait_for_window_ready",
        "desktop_recover_window",
    }


def _classify_desktop_open_target(
    args: Dict[str, Any],
    result: Dict[str, Any],
) -> Dict[str, Any]:
    summary = _result_summary(result)
    open_target = result.get("open_target", {}) if isinstance(result.get("open_target", {}), dict) else {}
    open_strategy = result.get("open_strategy", {}) if isinstance(result.get("open_strategy", {}), dict) else {}
    open_verification = result.get("open_verification", {}) if isinstance(result.get("open_verification", {}), dict) else {}
    open_result = result.get("open_result", {}) if isinstance(result.get("open_result", {}), dict) else {}

    target_display = _trim_text(open_target.get("basename", "") or open_target.get("target", "") or args.get("target", ""), limit=160)
    target_class = _trim_text(open_target.get("target_classification", ""), limit=60)
    strategy_family = _trim_text(open_strategy.get("strategy_family", ""), limit=60)
    verification_status = _trim_text(open_verification.get("status", ""), limit=60)
    verification_note = _trim_text(open_verification.get("note", ""), limit=220)
    matched_window_title = _trim_text(open_verification.get("matched_window_title", ""), limit=160)
    matched_process_name = _trim_text(open_verification.get("matched_process_name", ""), limit=80)
    process_detected = bool(open_verification.get("process_detected", False))
    observed = matched_window_title or matched_process_name or verification_note
    expected = f"'{target_display or 'target'}' opens or becomes visible in the intended Windows surface."

    if result.get("paused", False):
        return {
            "status": "blocked",
            "reason": "approval_required",
            "summary": summary or f"Paused before opening '{target_display or 'the target'}' because the action needs approval.",
            "progress_made": False,
            "expected_change": expected,
            "observed_change": "",
            "confidence": "high",
            "strategy_family": strategy_family,
            "target_classification": target_class,
            "verification_status": verification_status,
        }

    if not result.get("ok", False):
        result_reason = _trim_text(open_result.get("reason", "") or result.get("error", "") or "open_target_failed", limit=80).lower()
        status = "failure"
        if verification_status in {"likely_opened_background", "brief_signal_only"}:
            status = "uncertain"
        return {
            "status": status,
            "reason": result_reason or "open_target_failed",
            "summary": summary or verification_note or f"Could not open '{target_display or 'the target'}'.",
            "progress_made": False,
            "expected_change": expected,
            "observed_change": observed or _trim_text(result.get("error", ""), limit=160),
            "confidence": "medium" if status == "failure" else "low",
            "strategy_family": strategy_family,
            "target_classification": target_class,
            "verification_status": verification_status,
        }

    if verification_status == "verified_new_window":
        return {
            "status": "success",
            "reason": "open_verified_new_window",
            "summary": summary or f"Opened '{target_display or 'the target'}' in a new visible Windows surface.",
            "progress_made": True,
            "expected_change": expected,
            "observed_change": observed or matched_window_title,
            "confidence": "high" if bool(open_verification.get("matched_active_window", False)) else "medium",
            "strategy_family": strategy_family,
            "target_classification": target_class,
            "verification_status": verification_status,
        }

    if verification_status == "verified_reused_window":
        return {
            "status": "success",
            "reason": "open_verified_reused_window",
            "summary": summary or f"Opened '{target_display or 'the target'}' by resurfacing an existing viewer window.",
            "progress_made": True,
            "expected_change": expected,
            "observed_change": observed or matched_window_title,
            "confidence": "medium",
            "strategy_family": strategy_family,
            "target_classification": target_class,
            "verification_status": verification_status,
        }

    if strategy_family == "explorer_assisted_ui" and result.get("ok", False):
        return {
            "status": "partial_success" if target_class != "folder_directory" else "success",
            "reason": "explorer_target_visible" if target_class != "folder_directory" else "folder_opened_in_explorer",
            "summary": summary or (
                f"Surfaced '{target_display or 'the target'}' in Explorer for a bounded UI fallback."
                if target_class != "folder_directory"
                else f"Opened '{target_display or 'the folder'}' in Explorer."
            ),
            "progress_made": True,
            "expected_change": expected,
            "observed_change": observed or verification_note or "Explorer opened for the target.",
            "confidence": "medium" if verification_status in {"verified_new_window", "verified_reused_window"} else "low",
            "strategy_family": strategy_family,
            "target_classification": target_class,
            "verification_status": verification_status or "explorer_visible",
        }

    if verification_status == "likely_opened_background":
        return {
            "status": "uncertain",
            "reason": "open_likely_background",
            "summary": summary or f"'{target_display or 'The target'}' may have opened behind another window or reused a background viewer.",
            "progress_made": False,
            "expected_change": expected,
            "observed_change": observed or matched_window_title,
            "confidence": "low",
            "strategy_family": strategy_family,
            "target_classification": target_class,
            "verification_status": verification_status,
        }

    if verification_status == "process_started_only":
        return {
            "status": "uncertain",
            "reason": "open_process_started_only",
            "summary": summary or f"The target process started for '{target_display or 'the target'}', but a visible window was not clearly confirmed.",
            "progress_made": False,
            "expected_change": expected,
            "observed_change": observed or matched_process_name,
            "confidence": "low",
            "strategy_family": strategy_family,
            "target_classification": target_class,
            "verification_status": verification_status,
        }

    if verification_status == "brief_signal_only":
        return {
            "status": "uncertain",
            "reason": "open_brief_signal_only",
            "summary": summary or f"Only a brief signal appeared for '{target_display or 'the target'}', so the open result is still uncertain.",
            "progress_made": False,
            "expected_change": expected,
            "observed_change": observed or verification_note,
            "confidence": "low",
            "strategy_family": strategy_family,
            "target_classification": target_class,
            "verification_status": verification_status,
        }

    if process_detected and target_class == "executable_program":
        return {
            "status": "uncertain",
            "reason": "open_process_detected_only",
            "summary": summary or f"Detected process activity for '{target_display or 'the executable'}', but the visible app surface was not clearly confirmed.",
            "progress_made": False,
            "expected_change": expected,
            "observed_change": observed or matched_process_name,
            "confidence": "low",
            "strategy_family": strategy_family,
            "target_classification": target_class,
            "verification_status": verification_status or "process_detected_only",
        }

    return {
        "status": "uncertain",
        "reason": "open_unverified",
        "summary": summary or f"The Windows open request for '{target_display or 'the target'}' returned success, but the intended result could not be confirmed.",
        "progress_made": False,
        "expected_change": expected,
        "observed_change": observed or "No visible open signal was confirmed.",
        "confidence": "low",
        "strategy_family": strategy_family,
        "target_classification": target_class,
        "verification_status": verification_status or "not_observed",
    }


def _desktop_strategy_family(args: Dict[str, Any], result: Dict[str, Any]) -> str:
    desktop_strategy = result.get("desktop_strategy", {}) if isinstance(result.get("desktop_strategy", {}), dict) else {}
    return _trim_text(
        desktop_strategy.get("strategy_family", "")
        or args.get("strategy_family", ""),
        limit=60,
    )


def _desktop_validator_family(args: Dict[str, Any], result: Dict[str, Any]) -> str:
    desktop_verification = result.get("desktop_verification", {}) if isinstance(result.get("desktop_verification", {}), dict) else {}
    desktop_strategy = result.get("desktop_strategy", {}) if isinstance(result.get("desktop_strategy", {}), dict) else {}
    return _trim_text(
        desktop_verification.get("validator_family", "")
        or desktop_strategy.get("validator_family", "")
        or args.get("validator_family", ""),
        limit=60,
    )


def _classify_desktop_with_verification(
    tool_name: str,
    args: Dict[str, Any],
    result: Dict[str, Any],
) -> Dict[str, Any]:
    desktop_verification = result.get("desktop_verification", {}) if isinstance(result.get("desktop_verification", {}), dict) else {}
    if not desktop_verification:
        return {}
    validator_family = _desktop_validator_family(args, result)
    verification_status = _trim_text(desktop_verification.get("status", ""), limit=60)
    if not validator_family or not verification_status:
        return {}
    strategy_family = _desktop_strategy_family(args, result)
    target_description = _trim_text(desktop_verification.get("target_description", ""), limit=160)
    observed = _trim_text(
        desktop_verification.get("matched_window_title", "")
        or desktop_verification.get("matched_process_name", "")
        or desktop_verification.get("note", ""),
        limit=180,
    )
    confidence = _trim_text(desktop_verification.get("confidence", ""), limit=20) or "low"

    if validator_family == "focus_switch":
        if verification_status == "verified_focus":
            return {
                "status": "success",
                "reason": "focus_verified",
                "summary": _trim_text(desktop_verification.get("note", ""), limit=220) or "The target window became active.",
                "progress_made": True,
                "expected_change": target_description or "Target window becomes foreground.",
                "observed_change": observed,
                "confidence": confidence,
                "strategy_family": strategy_family,
                "validator_family": validator_family,
                "verification_status": verification_status,
            }
        if verification_status == "focus_improved":
            return {
                "status": "partial_success",
                "reason": "focus_improved",
                "summary": _trim_text(desktop_verification.get("note", ""), limit=220) or "The focus moved toward the requested target, but the result is not fully confirmed.",
                "progress_made": True,
                "expected_change": target_description or "Target window becomes foreground.",
                "observed_change": observed,
                "confidence": confidence,
                "strategy_family": strategy_family,
                "validator_family": validator_family,
                "verification_status": verification_status,
            }
        if verification_status == "target_visible_not_foreground":
            return {
                "status": "uncertain",
                "reason": "focus_not_foreground",
                "summary": _trim_text(desktop_verification.get("note", ""), limit=220) or "The target window was detected but did not clearly become foreground.",
                "progress_made": False,
                "expected_change": target_description or "Target window becomes foreground.",
                "observed_change": observed,
                "confidence": confidence,
                "strategy_family": strategy_family,
                "validator_family": validator_family,
                "verification_status": verification_status,
            }
        if verification_status in {"no_focus_change", "timing_expired"}:
            return {
                "status": "no_progress" if verification_status == "no_focus_change" else "uncertain",
                "reason": "focus_no_progress" if verification_status == "no_focus_change" else "focus_timing_expired",
                "summary": _trim_text(desktop_verification.get("note", ""), limit=220) or "The focus request did not produce enough proof.",
                "progress_made": False,
                "expected_change": target_description or "Target window becomes foreground.",
                "observed_change": observed or "No clear focus change detected.",
                "confidence": confidence,
                "strategy_family": strategy_family,
                "validator_family": validator_family,
                "verification_status": verification_status,
            }

    if validator_family == "click_navigation":
        if verification_status == "verified_navigation_change":
            return {
                "status": "success",
                "reason": "navigation_verified",
                "summary": _trim_text(desktop_verification.get("note", ""), limit=220) or "The bounded interaction produced a visible desktop change.",
                "progress_made": True,
                "expected_change": target_description or "Visible desktop navigation or target-state change.",
                "observed_change": observed,
                "confidence": confidence,
                "strategy_family": strategy_family,
                "validator_family": validator_family,
                "verification_status": verification_status,
            }
        if verification_status == "focus_reacquired_only":
            return {
                "status": "partial_success",
                "reason": "focus_reacquired_only",
                "summary": _trim_text(desktop_verification.get("note", ""), limit=220) or "The retry reacquired the intended surface, but no stronger visible progress was confirmed.",
                "progress_made": True,
                "expected_change": target_description or "Visible desktop navigation or target-state change.",
                "observed_change": observed,
                "confidence": confidence,
                "strategy_family": strategy_family,
                "validator_family": validator_family,
                "verification_status": verification_status,
            }
        if verification_status in {"no_visible_change", "timing_expired"}:
            return {
                "status": "no_progress" if verification_status == "no_visible_change" else "uncertain",
                "reason": "navigation_no_visible_change" if verification_status == "no_visible_change" else "navigation_timing_expired",
                "summary": _trim_text(desktop_verification.get("note", ""), limit=220) or "The interaction ran, but the visible result could not be confirmed.",
                "progress_made": False,
                "expected_change": target_description or "Visible desktop navigation or target-state change.",
                "observed_change": observed or "No visible change detected.",
                "confidence": confidence,
                "strategy_family": strategy_family,
                "validator_family": validator_family,
                "verification_status": verification_status,
            }

    if validator_family == "text_input":
        if verification_status == "verified_input_change":
            return {
                "status": "success",
                "reason": "input_verified",
                "summary": _trim_text(desktop_verification.get("note", ""), limit=220) or "The bounded input step produced a visible desktop change.",
                "progress_made": True,
                "expected_change": target_description or "Visible input or field-state change.",
                "observed_change": observed,
                "confidence": confidence,
                "strategy_family": strategy_family,
                "validator_family": validator_family,
                "verification_status": verification_status,
            }
        if verification_status == "focus_confirmed_only":
            return {
                "status": "partial_success",
                "reason": "input_focus_confirmed_only",
                "summary": _trim_text(desktop_verification.get("note", ""), limit=220) or "The target kept focus, but the visible input result could not be confirmed.",
                "progress_made": True,
                "expected_change": target_description or "Visible input or field-state change.",
                "observed_change": observed,
                "confidence": confidence,
                "strategy_family": strategy_family,
                "validator_family": validator_family,
                "verification_status": verification_status,
            }
        if verification_status == "focus_lost_or_unverified":
            return {
                "status": "failure",
                "reason": "input_focus_unverified",
                "summary": _trim_text(desktop_verification.get("note", ""), limit=220) or "The intended input surface could not be verified after the action.",
                "progress_made": False,
                "expected_change": target_description or "Visible input or field-state change.",
                "observed_change": observed,
                "confidence": confidence,
                "strategy_family": strategy_family,
                "validator_family": validator_family,
                "verification_status": verification_status,
            }
        if verification_status in {"no_visible_change", "timing_expired"}:
            return {
                "status": "uncertain",
                "reason": "input_no_visible_change" if verification_status == "no_visible_change" else "input_timing_expired",
                "summary": _trim_text(desktop_verification.get("note", ""), limit=220) or "The input step ran, but the intended visible change could not be confirmed.",
                "progress_made": False,
                "expected_change": target_description or "Visible input or field-state change.",
                "observed_change": observed or "No visible change detected.",
                "confidence": confidence,
                "strategy_family": strategy_family,
                "validator_family": validator_family,
                "verification_status": verification_status,
            }

    if validator_family == "open_launch":
        if verification_status == "verified_launch_visible":
            return {
                "status": "success",
                "reason": "launch_verified",
                "summary": _trim_text(desktop_verification.get("note", ""), limit=220) or "The launch-like action produced a visible desktop surface.",
                "progress_made": True,
                "expected_change": target_description or "Visible app or document surface opens.",
                "observed_change": observed,
                "confidence": confidence,
                "strategy_family": strategy_family,
                "validator_family": validator_family,
                "verification_status": verification_status,
            }
        if verification_status in {"launch_likely_background", "process_started_only", "timing_expired"}:
            return {
                "status": "uncertain",
                "reason": "launch_likely_background" if verification_status == "launch_likely_background" else "launch_process_started_only" if verification_status == "process_started_only" else "launch_timing_expired",
                "summary": _trim_text(desktop_verification.get("note", ""), limit=220) or "The launch-like step returned, but the visible surface could not be confirmed.",
                "progress_made": False,
                "expected_change": target_description or "Visible app or document surface opens.",
                "observed_change": observed or "No clear visible launch signal was confirmed.",
                "confidence": confidence,
                "strategy_family": strategy_family,
                "validator_family": validator_family,
                "verification_status": verification_status,
            }
        if verification_status == "no_visible_change":
            return {
                "status": "failure",
                "reason": "launch_no_visible_change",
                "summary": _trim_text(desktop_verification.get("note", ""), limit=220) or "The launch-like step completed, but the expected app or document surface did not appear.",
                "progress_made": False,
                "expected_change": target_description or "Visible app or document surface opens.",
                "observed_change": observed or "No clear visible launch signal was confirmed.",
                "confidence": confidence,
                "strategy_family": strategy_family,
                "validator_family": validator_family,
                "verification_status": verification_status,
            }

    return {}


def _classify_desktop(
    tool_name: str,
    args: Dict[str, Any],
    result: Dict[str, Any],
    before_context: Dict[str, Any],
    after_context: Dict[str, Any],
) -> Dict[str, Any]:
    summary = _result_summary(result)
    recovery = result.get("recovery", {}) if isinstance(result.get("recovery", {}), dict) else {}
    scene = result.get("scene", {}) if isinstance(result.get("scene", {}), dict) else {}
    target_window = result.get("target_window", {}) if isinstance(result.get("target_window", {}), dict) else {}
    command_result = result.get("command_result", {}) if isinstance(result.get("command_result", {}), dict) else {}
    strategy_family = _desktop_strategy_family(args, result)
    validator_family = _desktop_validator_family(args, result)

    before_window = _trim_text(before_context.get("active_window_title", ""), limit=180)
    after_window = _trim_text(after_context.get("active_window_title", ""), limit=180)
    before_process = _trim_text(before_context.get("active_window_process", ""), limit=80)
    after_process = _trim_text(after_context.get("active_window_process", ""), limit=80)
    evidence_before = _trim_text(before_context.get("desktop_evidence_id", ""), limit=80)
    evidence_after = _trim_text(after_context.get("desktop_evidence_id", ""), limit=80)
    target_title = _trim_text(target_window.get("title", "") or args.get("title", ""), limit=180)
    recovery_state = _trim_text(recovery.get("state", ""), limit=40).lower()
    recovery_reason = _trim_text(recovery.get("reason", ""), limit=80).lower()
    scene_changed = _safe_bool(scene.get("scene_changed", False))
    window_changed = bool(after_window and after_window != before_window)
    process_changed = bool(after_process and after_process != before_process)
    evidence_changed = bool(evidence_after and evidence_after != evidence_before)
    active_matches_target = bool(target_title and target_title.lower() in after_window.lower())

    if tool_name == "desktop_open_target":
        return _classify_desktop_open_target(args, result)

    if result.get("paused", False):
        return {
            "status": "blocked",
            "reason": "approval_required",
            "summary": summary or "Paused for explicit approval before the desktop action can continue.",
            "progress_made": False,
            "expected_change": "Desktop state change after approval.",
            "observed_change": "",
            "confidence": "high",
        }

    if not result.get("ok", False):
        reason = recovery_reason or "desktop_action_failed"
        status = "no_progress" if "not found" in summary.lower() or recovery_state == "missing" else "failure"
        return {
            "status": status,
            "reason": reason,
            "summary": summary or "Desktop action did not complete successfully.",
            "progress_made": False,
            "expected_change": target_title or "Visible desktop change.",
            "observed_change": after_window or after_process,
            "confidence": "medium",
            "strategy_family": strategy_family,
            "validator_family": validator_family,
        }

    post_verification = result.get("post_action_verification", {})
    if isinstance(post_verification, dict) and post_verification.get("verified") and not post_verification.get("consistent_with_tool", True):
        return {
            "status": "uncertain",
            "reason": "independent_verification_mismatch",
            "summary": f"The tool reported success, but independent verification shows the foreground window is '{_trim_text(post_verification.get('foreground_title', ''), limit=120)}', which may not match the expected target.",
            "progress_made": False,
            "expected_change": target_title or "Visible desktop change.",
            "observed_change": _trim_text(post_verification.get("foreground_title", ""), limit=120),
            "confidence": "low",
            "strategy_family": strategy_family,
            "validator_family": validator_family,
        }

    verified = _classify_desktop_with_verification(tool_name, args, result)
    if verified:
        return verified

    post_verification = result.get("post_action_verification", {}) if isinstance(result.get("post_action_verification", {}), dict) else {}
    if post_verification.get("verified") and not post_verification.get("consistent_with_tool", True):
        fg_title = _trim_text(post_verification.get("foreground_title", ""), limit=180)
        return {
            "status": "uncertain",
            "reason": "independent_verification_mismatch",
            "summary": (
                f"The tool reported success, but an independent check found "
                f"'{fg_title or 'a different window'}' in the foreground instead of the expected target."
            ),
            "progress_made": False,
            "expected_change": target_title or "Target window remains active.",
            "observed_change": fg_title,
            "confidence": "high",
            "strategy_family": strategy_family,
            "validator_family": validator_family,
        }

    if tool_name == "desktop_focus_window":
        if recovery_state == "ready" or active_matches_target or window_changed or process_changed:
            observed = after_window or after_process or target_title
            return {
                "status": "success",
                "reason": "window_focused",
                "summary": summary or f"Focused {observed or 'the requested window'}.",
                "progress_made": True,
                "expected_change": target_title or "Target window becomes active.",
                "observed_change": observed,
                "confidence": "high" if active_matches_target or recovery_state == "ready" else "medium",
                "strategy_family": strategy_family,
                "validator_family": validator_family,
            }
        return {
            "status": "uncertain",
            "reason": "focus_unverified",
            "summary": summary or "The focus action returned success, but the active window did not clearly change.",
            "progress_made": False,
            "expected_change": target_title or "Target window becomes active.",
            "observed_change": after_window or after_process,
            "confidence": "low",
            "strategy_family": strategy_family,
            "validator_family": validator_family,
        }

    if tool_name == "desktop_run_command":
        exit_code = _safe_int(command_result.get("returncode", command_result.get("exit_code", 0)), 0)
        command = _trim_text(result.get("command", "") or args.get("command", ""), limit=220)
        expected = "Command completes inside the bounded desktop context."
        if _command_looks_like_open_intent(command):
            expected = "Visible file, app, or window opens."
            if window_changed or process_changed or evidence_changed or scene_changed:
                observed = after_window or after_process or evidence_after
                return {
                    "status": "success",
                    "reason": "open_command_verified",
                    "summary": summary or "The bounded desktop command appears to have opened the expected surface.",
                "progress_made": True,
                "expected_change": expected,
                "observed_change": observed,
                "confidence": "medium",
                "strategy_family": strategy_family,
                "validator_family": validator_family,
            }
            return {
                "status": "uncertain",
                "reason": "open_command_unverified",
                "summary": summary or "The command returned successfully, but no visible desktop change confirmed that the file or app opened.",
                "progress_made": False,
                "expected_change": expected,
                "observed_change": after_window or after_process or "No visible change detected.",
                "confidence": "low",
                "strategy_family": strategy_family,
                "validator_family": validator_family,
            }
        if exit_code == 0:
            return {
                "status": "success",
                "reason": "command_completed",
                "summary": summary or "The bounded desktop command completed successfully.",
                "progress_made": True,
                "expected_change": expected,
                "observed_change": _trim_text(command_result.get("stdout_excerpt", ""), limit=160),
                "confidence": "medium",
                "strategy_family": strategy_family,
                "validator_family": validator_family,
            }
        return {
            "status": "failure",
            "reason": "command_failed",
            "summary": summary or "The bounded desktop command failed.",
            "progress_made": False,
            "expected_change": expected,
            "observed_change": _trim_text(command_result.get("stderr_excerpt", ""), limit=160),
            "confidence": "medium",
            "strategy_family": strategy_family,
            "validator_family": validator_family,
        }

    if _read_only_desktop_tool(tool_name):
        observed = after_window or evidence_after or _trim_text(result.get("summary", ""), limit=160)
        return {
            "status": "success",
            "reason": "observation_recorded",
            "summary": summary or "Captured fresh desktop state.",
            "progress_made": True,
            "expected_change": "Fresh desktop evidence or state.",
            "observed_change": observed,
            "confidence": "high" if evidence_changed or tool_name == "desktop_capture_screenshot" else "medium",
            "strategy_family": strategy_family,
            "validator_family": validator_family,
        }

    if evidence_changed or scene_changed or window_changed or process_changed:
        observed = after_window or evidence_after or after_process
        return {
            "status": "success",
            "reason": "desktop_state_changed",
            "summary": summary or "The desktop action appears to have changed the bounded desktop state.",
            "progress_made": True,
            "expected_change": target_title or "Desktop state changes in the intended direction.",
            "observed_change": observed,
            "confidence": "medium",
            "strategy_family": strategy_family,
            "validator_family": validator_family,
        }

    return {
        "status": "uncertain",
        "reason": "desktop_change_unverified",
        "summary": summary or "The desktop action returned success, but the visible state change could not be confirmed.",
        "progress_made": False,
        "expected_change": target_title or "Visible desktop change.",
        "observed_change": after_window or after_process or "No visible change detected.",
        "confidence": "low",
        "strategy_family": strategy_family,
        "validator_family": validator_family,
    }


def _classify_gmail(tool_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    summary = _result_summary(result)
    if result.get("paused", False) and result.get("approval_required", False):
        return {
            "status": "blocked",
            "reason": "approval_required",
            "summary": summary or "Prepared Gmail action is waiting for explicit approval.",
            "progress_made": True,
            "expected_change": "Send only after explicit approval.",
            "observed_change": _trim_text(result.get("draft_id", ""), limit=80),
            "confidence": "high",
        }
    if tool_name == "email_prepare_reply_draft":
        draft = result.get("draft", {}) if isinstance(result.get("draft", {}), dict) else {}
        if draft:
            return {
                "status": "success",
                "reason": "draft_prepared",
                "summary": summary or "Prepared a frozen Gmail reply draft.",
                "progress_made": True,
                "expected_change": "Prepared local draft exists for review.",
                "observed_change": _trim_text(draft.get("draft_id", ""), limit=80),
                "confidence": "high",
            }
        if result.get("needs_context", False):
            return {
                "status": "partial_success",
                "reason": "needs_context",
                "summary": summary or "The thread was analyzed, but a reply needs user context before drafting.",
                "progress_made": True,
                "expected_change": "Prepared draft or clear question list.",
                "observed_change": ", ".join(_normalized_lines(result.get("questions", []), limit=3, text_limit=80)),
                "confidence": "medium",
            }
        if str(result.get("disposition", "")).strip().lower() == "no_reply":
            return {
                "status": "success",
                "reason": "no_reply_needed",
                "summary": summary or "The Gmail thread was triaged as not requiring a reply.",
                "progress_made": True,
                "expected_change": "Thread is classified for no reply.",
                "observed_change": "no_reply",
                "confidence": "medium",
            }
    if tool_name == "email_prepare_forward_draft":
        draft = result.get("draft", {}) if isinstance(result.get("draft", {}), dict) else {}
        if draft:
            return {
                "status": "success",
                "reason": "forward_prepared",
                "summary": summary or "Prepared a frozen Gmail forward draft.",
                "progress_made": True,
                "expected_change": "Prepared forward draft exists for review.",
                "observed_change": _trim_text(draft.get("draft_id", ""), limit=80),
                "confidence": "high",
            }
    if tool_name == "email_send_draft":
        sent = result.get("sent", {}) if isinstance(result.get("sent", {}), dict) else {}
        if result.get("ok", False) and _trim_text(sent.get("message_id", ""), limit=80):
            return {
                "status": "success",
                "reason": "email_sent",
                "summary": summary or "Sent the approved Gmail draft.",
                "progress_made": True,
                "expected_change": "Draft transitions to sent.",
                "observed_change": _trim_text(sent.get("message_id", ""), limit=80),
                "confidence": "high",
            }
    if tool_name == "email_list_threads":
        threads = list(result.get("threads", [])) if isinstance(result.get("threads", []), list) else []
        return {
            "status": "success",
            "reason": "threads_loaded",
            "summary": summary or "Loaded Gmail threads.",
            "progress_made": True,
            "expected_change": "Thread list becomes available.",
            "observed_change": f"{len(threads)} threads",
            "confidence": "high",
        }
    if tool_name == "email_read_thread":
        thread = result.get("thread", {}) if isinstance(result.get("thread", {}), dict) else {}
        messages = list(thread.get("messages", [])) if isinstance(thread.get("messages", []), list) else []
        if thread:
            return {
                "status": "success",
                "reason": "thread_loaded",
                "summary": summary or "Loaded the Gmail thread.",
                "progress_made": True,
                "expected_change": "Thread detail becomes available.",
                "observed_change": f"{len(messages)} messages",
                "confidence": "high",
            }
    if result.get("ok", False):
        return {
            "status": "success",
            "reason": "gmail_action_completed",
            "summary": summary or "Completed the Gmail action.",
            "progress_made": True,
            "expected_change": "Gmail state changes in the intended direction.",
            "observed_change": "",
            "confidence": "medium",
        }
    return {
        "status": "failure",
        "reason": _trim_text(result.get("error", "gmail_action_failed"), limit=80).lower() or "gmail_action_failed",
        "summary": summary or "The Gmail action did not complete successfully.",
        "progress_made": False,
        "expected_change": "Gmail thread or draft state changes.",
        "observed_change": "",
        "confidence": "medium",
    }


def _classify_lab(result: Dict[str, Any]) -> Dict[str, Any]:
    summary = _result_summary(result)
    policy = result.get("policy", {}) if isinstance(result.get("policy", {}), dict) else {}
    decision = _trim_text(policy.get("decision", ""), limit=40).lower()
    if result.get("blocked", False) or decision == "block":
        return {
            "status": "blocked",
            "reason": "policy_blocked",
            "summary": summary or "Blocked the experimental lab command before execution.",
            "progress_made": False,
            "expected_change": "No command should run.",
            "observed_change": ", ".join(_normalized_lines(policy.get("blocked_categories", []), limit=3, text_limit=40)),
            "confidence": "high",
        }
    if result.get("paused", False) and result.get("approval_required", False):
        return {
            "status": "blocked",
            "reason": "approval_required",
            "summary": summary or "The experimental lab command is waiting for explicit approval.",
            "progress_made": False,
            "expected_change": "No command should run before approval.",
            "observed_change": "",
            "confidence": "high",
        }
    exit_code = _safe_int(result.get("exit_code", 0), 0)
    if result.get("ok", False) and exit_code == 0:
        return {
            "status": "success",
            "reason": "lab_command_completed",
            "summary": summary or "The experimental lab command completed inside the lab workspace.",
            "progress_made": True,
            "expected_change": "Lab command produces the expected bounded output or state change.",
            "observed_change": _trim_text(result.get("stdout_excerpt", ""), limit=160),
            "confidence": "high",
        }
    if result.get("ok", False):
        return {
            "status": "partial_success",
            "reason": "lab_command_nonzero",
            "summary": summary or "The experimental lab command returned a non-zero or ambiguous result.",
            "progress_made": True,
            "expected_change": "Lab command completes cleanly.",
            "observed_change": _trim_text(result.get("stderr_excerpt", "") or result.get("stdout_excerpt", ""), limit=160),
            "confidence": "medium",
        }
    return {
        "status": "failure",
        "reason": "lab_command_failed",
        "summary": summary or "The experimental lab command failed.",
        "progress_made": False,
        "expected_change": "Lab command completes cleanly.",
        "observed_change": _trim_text(result.get("stderr_excerpt", ""), limit=160),
        "confidence": "medium",
    }


def _classify_generic(tool_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    summary = _result_summary(result)
    if result.get("paused", False):
        return {
            "status": "blocked",
            "reason": "approval_required",
            "summary": summary or "The operator paused for explicit approval.",
            "progress_made": False,
            "expected_change": "Action continues only after approval.",
            "observed_change": "",
            "confidence": "high",
        }
    if result.get("ok", False):
        return {
            "status": "success",
            "reason": "tool_completed",
            "summary": summary or f"Completed {tool_name}.",
            "progress_made": True,
            "expected_change": "Bounded tool result becomes available.",
            "observed_change": "",
            "confidence": "medium",
        }
    failure_summary = summary.lower()
    status = "no_progress" if any(term in failure_summary for term in ("not found", "unchanged", "no matches", "no result")) else "failure"
    return {
        "status": status,
        "reason": _trim_text(result.get("error", "tool_failed"), limit=80).lower() or "tool_failed",
        "summary": summary or f"{tool_name} failed.",
        "progress_made": False,
        "expected_change": "Bounded tool result becomes available.",
        "observed_change": "",
        "confidence": "medium",
    }


def _build_retry_policy(
    *,
    domain: str,
    status: str,
    reason: str,
    summary: str,
    attempts: int,
) -> Dict[str, Any]:
    max_attempts = 2 if domain in {"desktop", "gmail", "lab", "browser"} else 1
    normalized_reason = str(reason or "").strip().lower()
    action = "none"
    stop_run = False
    explanation = ""

    if status == "success":
        explanation = "No retry is needed because the latest action outcome looks successful."
    elif status == "partial_success":
        if normalized_reason == "needs_context":
            action = "ask_user"
            stop_run = True
            explanation = "Stop and ask for the missing context instead of guessing."
        else:
            action = "retry_with_variation" if attempts < max_attempts else "stop"
            stop_run = attempts >= max_attempts
            explanation = "Some progress was made, but the next step should vary rather than repeating blindly."
    elif status == "blocked":
        if normalized_reason == "approval_required":
            action = "await_approval"
            explanation = "Wait for approval instead of retrying."
        else:
            action = "stop"
            stop_run = True
            explanation = "Blocked outcomes should stop rather than retrying."
    elif status == "uncertain":
        if attempts >= max_attempts:
            action = "ask_user"
            stop_run = True
            explanation = "The outcome is uncertain — I could not verify whether the action actually worked. Please check the current state and tell me whether to continue or try a different approach."
        elif domain == "desktop":
            action = "recovery_first"
            explanation = "Recover or refresh the bounded desktop context, then re-verify the uncertain outcome."
        else:
            action = "retry_with_variation"
            explanation = "The outcome is uncertain — retry with a variation that produces a verifiable result."
    elif status in {"failure", "no_progress"}:
        if attempts >= max_attempts:
            action = "stop"
            stop_run = True
            explanation = "The retry budget is exhausted, so the operator should stop instead of repeating the same move."
        elif domain == "desktop":
            action = "recovery_first"
            explanation = "Recover or refresh the bounded desktop context before trying again."
        elif domain in {"gmail", "lab"}:
            action = "retry_with_variation"
            explanation = "Retry only with a meaningfully different safe path, not the exact same action."
        else:
            action = "retry_with_variation"
            explanation = "Retry with variation or a more grounded follow-up, not the exact same action."

    return {
        "action": action,
        "attempt_number": max(1, attempts),
        "max_attempts": max_attempts,
        "exhausted": attempts >= max_attempts and action == "stop",
        "stop_run": stop_run,
        "explanation": _trim_text(explanation or summary, limit=220),
    }


def evaluate_action_outcome(
    task_state,
    tool_name: str,
    args: Dict[str, Any] | None,
    result: Dict[str, Any] | None,
    *,
    before_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    safe_args = args if isinstance(args, dict) else {}
    safe_result = result if isinstance(result, dict) else {}
    context_before = before_context if isinstance(before_context, dict) else capture_action_context(task_state, tool_name, safe_args)
    context_after = capture_action_context(task_state, tool_name, safe_args)
    domain = action_domain(tool_name)
    action_signature = build_action_signature(tool_name, safe_args)
    if domain == "desktop":
        desktop_strategy = safe_result.get("desktop_strategy", {}) if isinstance(safe_result.get("desktop_strategy", {}), dict) else {}
        desktop_verification = safe_result.get("desktop_verification", {}) if isinstance(safe_result.get("desktop_verification", {}), dict) else {}
        open_target = safe_result.get("open_target", {}) if isinstance(safe_result.get("open_target", {}), dict) else {}
        open_strategy = safe_result.get("open_strategy", {}) if isinstance(safe_result.get("open_strategy", {}), dict) else {}
        target_class = _trim_text(
            open_target.get("target_classification", safe_args.get("target_type", "")),
            limit=60,
        )
        strategy_family = _trim_text(
            open_strategy.get("strategy_family", "")
            or desktop_strategy.get("strategy_family", "")
            or safe_args.get("strategy_family", "")
            or safe_args.get("preferred_method", ""),
            limit=60,
        )
        validator_family = _trim_text(
            desktop_verification.get("validator_family", "")
            or desktop_strategy.get("validator_family", "")
            or safe_args.get("validator_family", ""),
            limit=60,
        )
        extra_parts = []
        if target_class:
            extra_parts.append(f"target_class={target_class}")
        if strategy_family:
            extra_parts.append(f"strategy_family={strategy_family}")
        if validator_family:
            extra_parts.append(f"validator_family={validator_family}")
        if extra_parts:
            action_signature = "::".join([action_signature, *extra_parts])
    target_signature = _infer_target_signature(tool_name, safe_args, safe_result, context_before, context_after)
    attempts = _previous_attempts(task_state, action_signature) + 1

    if domain == "desktop":
        base = _classify_desktop(tool_name, safe_args, safe_result, context_before, context_after)
    elif domain == "gmail":
        base = _classify_gmail(tool_name, safe_result)
    elif domain == "lab":
        base = _classify_lab(safe_result)
    else:
        base = _classify_generic(tool_name, safe_result)

    retry = _build_retry_policy(
        domain=domain,
        status=str(base.get("status", "uncertain")).strip(),
        reason=str(base.get("reason", "")).strip(),
        summary=str(base.get("summary", "")).strip(),
        attempts=attempts,
    )

    return {
        "domain": domain,
        "tool": _trim_text(tool_name, limit=80),
        "status": _trim_text(base.get("status", "uncertain"), limit=40),
        "reason": _trim_text(base.get("reason", "unclassified"), limit=80),
        "summary": _trim_text(base.get("summary", ""), limit=220),
        "confidence": _trim_text(base.get("confidence", "medium"), limit=20),
        "progress_made": bool(base.get("progress_made", False)),
        "expected_change": _trim_text(base.get("expected_change", ""), limit=160),
        "observed_change": _trim_text(base.get("observed_change", ""), limit=160),
        "action_signature": action_signature,
        "target_signature": _trim_text(target_signature, limit=220),
        "attempt_number": attempts,
        "strategy_family": _trim_text(base.get("strategy_family", ""), limit=60),
        "validator_family": _trim_text(base.get("validator_family", ""), limit=60),
        "target_classification": _trim_text(base.get("target_classification", ""), limit=60),
        "verification_status": _trim_text(base.get("verification_status", ""), limit=60),
        "retry": retry,
        "before": {
            "active_window_title": _trim_text(context_before.get("active_window_title", ""), limit=120),
            "active_window_process": _trim_text(context_before.get("active_window_process", ""), limit=60),
            "desktop_evidence_id": _trim_text(context_before.get("desktop_evidence_id", ""), limit=80),
            "browser_current_url": _trim_text(context_before.get("browser_current_url", ""), limit=120),
            "lab_workspace_id": _trim_text(context_before.get("lab_workspace_id", ""), limit=80),
        },
        "after": {
            "active_window_title": _trim_text(context_after.get("active_window_title", ""), limit=120),
            "active_window_process": _trim_text(context_after.get("active_window_process", ""), limit=60),
            "desktop_evidence_id": _trim_text(context_after.get("desktop_evidence_id", ""), limit=80),
            "browser_current_url": _trim_text(context_after.get("browser_current_url", ""), limit=120),
            "lab_workspace_id": _trim_text(context_after.get("lab_workspace_id", ""), limit=80),
        },
        "evaluated_at": _iso_now(),
    }


class OperatorMemoryStore:
    def __init__(self, path: str | Path, *, max_entries: int = _MAX_HEURISTIC_ENTRIES):
        self.path = Path(path)
        self.max_entries = max(20, int(max_entries))

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"heuristics": [], "environment": {}, "lessons": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"heuristics": [], "environment": {}, "lessons": []}
        if not isinstance(payload, dict):
            return {"heuristics": [], "environment": {}, "lessons": []}
        heuristics = payload.get("heuristics", [])
        environment = payload.get("environment", {})
        lessons = payload.get("lessons", [])
        return {
            "heuristics": heuristics if isinstance(heuristics, list) else [],
            "environment": environment if isinstance(environment, dict) else {},
            "lessons": lessons if isinstance(lessons, list) else [],
        }

    def _save(self, payload: Dict[str, Any]) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            return False
        return True

    def remember_environment(self, facts: Dict[str, Any]) -> bool:
        safe_facts = facts if isinstance(facts, dict) else {}
        payload = self._load()
        payload["environment"] = {
            "updated_at": _iso_now(),
            "facts": safe_facts,
        }
        return self._save(payload)

    def record_outcome(self, evaluation: Dict[str, Any], *, goal: str = "") -> bool:
        if not isinstance(evaluation, dict) or not evaluation.get("tool"):
            return False
        payload = self._load()
        heuristics = payload.get("heuristics", [])
        if not isinstance(heuristics, list):
            heuristics = []
        heuristics.insert(
            0,
            {
                "recorded_at": _iso_now(),
                "domain": _trim_text(evaluation.get("domain", ""), limit=40),
                "tool": _trim_text(evaluation.get("tool", ""), limit=80),
                "status": _trim_text(evaluation.get("status", ""), limit=40),
                "reason": _trim_text(evaluation.get("reason", ""), limit=80),
                "summary": _trim_text(evaluation.get("summary", ""), limit=220),
                "action_signature": _trim_text(evaluation.get("action_signature", ""), limit=220),
                "target_signature": _trim_text(evaluation.get("target_signature", ""), limit=220),
                "strategy_family": _trim_text(evaluation.get("strategy_family", ""), limit=60),
                "validator_family": _trim_text(evaluation.get("validator_family", ""), limit=60),
                "target_classification": _trim_text(evaluation.get("target_classification", ""), limit=60),
                "verification_status": _trim_text(evaluation.get("verification_status", ""), limit=60),
                "goal": _trim_text(goal, limit=220),
                "retry_action": _trim_text((evaluation.get("retry", {}) if isinstance(evaluation.get("retry", {}), dict) else {}).get("action", ""), limit=40),
                "expected_change": _trim_text(evaluation.get("expected_change", ""), limit=120),
                "observed_change": _trim_text(evaluation.get("observed_change", ""), limit=120),
            },
        )
        payload["heuristics"] = heuristics[: self.max_entries]
        return self._save(payload)

    def lookup_patterns(
        self,
        *,
        domain: str = "",
        tool_name: str = "",
        target_signature: str = "",
        goal: str = "",
    ) -> Dict[str, Any]:
        payload = self._load()
        heuristics = payload.get("heuristics", [])
        if not isinstance(heuristics, list):
            heuristics = []
        lessons = payload.get("lessons", [])
        if not isinstance(lessons, list):
            lessons = []

        normalized_domain = _trim_text(domain, limit=40)
        normalized_tool = _trim_text(tool_name, limit=80)
        normalized_target = _trim_text(target_signature, limit=220)
        normalized_goal = _trim_text(goal, limit=220).lower()

        prefer: List[Dict[str, Any]] = []
        avoid: List[Dict[str, Any]] = []
        for entry in heuristics:
            if not isinstance(entry, dict):
                continue
            if normalized_domain and _trim_text(entry.get("domain", ""), limit=40) != normalized_domain:
                continue
            if normalized_tool and _trim_text(entry.get("tool", ""), limit=80) != normalized_tool:
                continue
            entry_target = _trim_text(entry.get("target_signature", ""), limit=220)
            if normalized_target and entry_target and normalized_target != entry_target:
                continue
            entry_goal = _trim_text(entry.get("goal", ""), limit=220).lower()
            if normalized_goal and entry_goal and normalized_goal not in entry_goal and entry_goal not in normalized_goal:
                if normalized_target:
                    continue
            compact = {
                "tool": _trim_text(entry.get("tool", ""), limit=80),
                "status": _trim_text(entry.get("status", ""), limit=40),
                "reason": _trim_text(entry.get("reason", ""), limit=80),
                "summary": _trim_text(entry.get("summary", ""), limit=180),
                "recorded_at": _trim_text(entry.get("recorded_at", ""), limit=40),
                "target_signature": entry_target,
                "retry_action": _trim_text(entry.get("retry_action", ""), limit=40),
                "strategy_family": _trim_text(entry.get("strategy_family", ""), limit=60),
                "validator_family": _trim_text(entry.get("validator_family", ""), limit=60),
                "target_classification": _trim_text(entry.get("target_classification", ""), limit=60),
            }
            if compact["status"] == "success" and len(prefer) < _MAX_HINT_ITEMS and compact not in prefer:
                prefer.append(compact)
            if compact["status"] in _FAIL_LIKE_OUTCOMES and len(avoid) < _MAX_HINT_ITEMS and compact not in avoid:
                avoid.append(compact)
            if len(prefer) >= _MAX_HINT_ITEMS and len(avoid) >= _MAX_HINT_ITEMS:
                break

        matching_lessons: List[Dict[str, Any]] = []
        for lesson in lessons:
            if not isinstance(lesson, dict):
                continue
            lesson_tool = _trim_text(lesson.get("tool", ""), limit=80)
            lesson_domain = _trim_text(lesson.get("domain", ""), limit=40)
            if normalized_domain and lesson_domain and lesson_domain != normalized_domain:
                continue
            if normalized_tool and lesson_tool and lesson_tool != normalized_tool:
                continue
            matching_lessons.append(
                {
                    "lesson": _trim_text(lesson.get("lesson", ""), limit=220),
                    "category": _trim_text(lesson.get("category", ""), limit=80),
                    "tool": lesson_tool,
                    "strategy_family": _trim_text(lesson.get("strategy_family", ""), limit=60),
                    "validator_family": _trim_text(lesson.get("validator_family", ""), limit=60),
                    "recorded_at": _trim_text(lesson.get("recorded_at", ""), limit=40),
                    "problem_key": _trim_text(lesson.get("problem_key", ""), limit=80),
                }
            )
            if len(matching_lessons) >= _MAX_HINT_ITEMS:
                break

        return {
            "prefer": prefer,
            "avoid": avoid,
            "lessons": matching_lessons,
            "environment": payload.get("environment", {}) if isinstance(payload.get("environment", {}), dict) else {},
        }

    def record_lesson(self, lesson: Dict[str, Any] | None) -> bool:
        safe_lesson = lesson if isinstance(lesson, dict) else {}
        lesson_key = _trim_text(safe_lesson.get("lesson_key", ""), limit=80)
        lesson_text = _trim_text(safe_lesson.get("lesson", ""), limit=220)
        if not lesson_key or not lesson_text:
            return False
        payload = self._load()
        lessons = payload.get("lessons", [])
        if not isinstance(lessons, list):
            lessons = []
        next_entry = {
            "lesson_key": lesson_key,
            "lesson": lesson_text,
            "category": _trim_text(safe_lesson.get("category", ""), limit=80),
            "tool": _trim_text(safe_lesson.get("tool", ""), limit=80),
            "domain": _trim_text(safe_lesson.get("domain", ""), limit=40),
            "strategy_family": _trim_text(safe_lesson.get("strategy_family", ""), limit=60),
            "validator_family": _trim_text(safe_lesson.get("validator_family", ""), limit=60),
            "problem_key": _trim_text(safe_lesson.get("problem_key", ""), limit=80),
            "recorded_at": _iso_now(),
        }
        lessons = [item for item in lessons if not isinstance(item, dict) or _trim_text(item.get("lesson_key", ""), limit=80) != lesson_key]
        lessons.insert(0, next_entry)
        payload["lessons"] = lessons[:_MAX_LESSON_ENTRIES]
        return self._save(payload)


def _recent_evaluation_items(task_state, *, limit: int = 6) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for step in reversed(list(getattr(task_state, "steps", []))):
        if step.get("type") != "tool":
            continue
        result = step.get("result", {}) if isinstance(step.get("result", {}), dict) else {}
        evaluation = result.get("evaluation", {}) if isinstance(result.get("evaluation", {}), dict) else {}
        if not evaluation:
            continue
        items.append(
            {
                "tool": _trim_text(evaluation.get("tool", step.get("tool", "")), limit=80),
                "domain": _trim_text(evaluation.get("domain", ""), limit=40),
                "status": _trim_text(evaluation.get("status", ""), limit=40),
                "reason": _trim_text(evaluation.get("reason", ""), limit=80),
                "summary": _trim_text(evaluation.get("summary", ""), limit=180),
                "confidence": _trim_text(evaluation.get("confidence", ""), limit=20),
                "progress_made": bool(evaluation.get("progress_made", False)),
                "retry": evaluation.get("retry", {}) if isinstance(evaluation.get("retry", {}), dict) else {},
                "target_signature": _trim_text(evaluation.get("target_signature", ""), limit=180),
                "strategy_family": _trim_text(evaluation.get("strategy_family", ""), limit=60),
                "validator_family": _trim_text(evaluation.get("validator_family", ""), limit=60),
                "observed_change": _trim_text(evaluation.get("observed_change", ""), limit=120),
                "evaluated_at": _trim_text(evaluation.get("evaluated_at", ""), limit=40),
            }
        )
        if len(items) >= limit:
            break
    return items


def _alternate_strategy_attempted(task_state, evaluation: Dict[str, Any] | None) -> bool:
    safe_evaluation = evaluation if isinstance(evaluation, dict) else {}
    target_signature = _trim_text(safe_evaluation.get("target_signature", ""), limit=220)
    action_signature = _trim_text(safe_evaluation.get("action_signature", ""), limit=220)
    if not target_signature:
        return False
    for item in _recent_evaluation_items(task_state, limit=8):
        if _trim_text(item.get("target_signature", ""), limit=220) != target_signature:
            continue
        same_tool = _trim_text(item.get("tool", ""), limit=80) == _trim_text(safe_evaluation.get("tool", ""), limit=80)
        same_strategy = _trim_text(item.get("strategy_family", ""), limit=60) == _trim_text(safe_evaluation.get("strategy_family", ""), limit=60)
        if same_tool and same_strategy:
            continue
        if _trim_text(item.get("status", ""), limit=40) in _FAIL_LIKE_OUTCOMES | {"partial_success"}:
            return True
        prior_retry = item.get("retry", {}) if isinstance(item.get("retry", {}), dict) else {}
        if _trim_text(prior_retry.get("action", ""), limit=40) in {"retry_with_variation", "alternate_target", "recovery_first"}:
            return True
    return False


def refresh_operator_intelligence_context(task_state) -> Dict[str, Any]:
    recent = _recent_evaluation_items(task_state, limit=6)
    last = recent[0] if recent else {}
    store = getattr(task_state, "_operator_memory_store", None)
    hints: Dict[str, Any] = {"prefer": [], "avoid": [], "environment": {}}
    if isinstance(store, OperatorMemoryStore):
        hints = store.lookup_patterns(
            domain=str(last.get("domain", "")).strip(),
            tool_name=str(last.get("tool", "")).strip(),
            target_signature=str(last.get("target_signature", "")).strip(),
            goal=str(getattr(task_state, "goal", "")).strip(),
        )
    environment = getattr(task_state, "_environment_awareness", {})
    if not isinstance(environment, dict):
        environment = {}

    execution_memory = {
        "attempted_actions": len(recent),
        "success_count": sum(1 for item in recent if str(item.get("status", "")).strip() == "success"),
        "failure_count": sum(1 for item in recent if str(item.get("status", "")).strip() in _FAIL_LIKE_OUTCOMES),
        "recent_recoveries": [
            _trim_text(item.get("summary", ""), limit=140)
            for item in recent
            if str((item.get("retry", {}) if isinstance(item.get("retry", {}), dict) else {}).get("action", "")).strip()
            in {"recovery_first", "retry_with_variation", "alternate_target"}
        ][:3],
    }
    last_problem = getattr(task_state, "_last_problem_record", {})
    if not isinstance(last_problem, dict):
        last_problem = {}
    known_problems: list[dict[str, Any]] = []
    problem_store = getattr(task_state, "_problem_store", None)
    if isinstance(problem_store, ProblemRecordStore):
        try:
            known_problems = problem_store.recall_relevant(
                goal=str(getattr(task_state, "goal", "")).strip(),
                tool=str(last.get("tool", "")).strip(),
                domain=str(last.get("domain", "")).strip(),
                limit=4,
            )
        except Exception:
            known_problems = []
    context = {
        "last_outcome": last,
        "recent_outcomes": recent,
        "retry": last.get("retry", {}) if isinstance(last.get("retry", {}), dict) else {},
        "memory_hints": {
            "prefer": list(hints.get("prefer", [])) if isinstance(hints.get("prefer", []), list) else [],
            "avoid": list(hints.get("avoid", [])) if isinstance(hints.get("avoid", []), list) else [],
            "lessons": list(hints.get("lessons", [])) if isinstance(hints.get("lessons", []), list) else [],
        },
        "execution_memory": execution_memory,
        "environment": environment,
        "last_problem": last_problem,
        "known_problems": known_problems,
    }
    inventory = getattr(task_state, "_strategy_inventory", None)
    if isinstance(inventory, StrategyExplorationInventory):
        last_target = str(last.get("target_signature", "")).strip()
        if last_target:
            context["strategy_exploration"] = inventory.summary(last_target)
    setattr(task_state, "_operator_intelligence_context", context)
    return context


def apply_outcome_evaluation(
    task_state,
    tool_name: str,
    args: Dict[str, Any] | None,
    result: Dict[str, Any] | None,
    *,
    before_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    evaluation = evaluate_action_outcome(task_state, tool_name, args, result, before_context=before_context)
    result["evaluation"] = evaluation
    strategy_family = str(evaluation.get("strategy_family", "") or "").strip()
    target_sig = str(evaluation.get("target_signature", "") or "").strip()
    eval_status = str(evaluation.get("status", "") or "").strip()
    if strategy_family and target_sig and eval_status:
        inventory = getattr(task_state, "_strategy_inventory", None)
        if not isinstance(inventory, StrategyExplorationInventory):
            inventory = StrategyExplorationInventory()
            setattr(task_state, "_strategy_inventory", inventory)
        inventory.record_attempt(target_sig, strategy_family, eval_status)
    store = getattr(task_state, "_operator_memory_store", None)
    alternate_strategy_attempted = _alternate_strategy_attempted(task_state, evaluation)
    problem = build_problem_record(
        task_state=task_state,
        tool_name=tool_name,
        args=args if isinstance(args, dict) else {},
        result=result,
        evaluation=evaluation,
        alternate_strategy_attempted=alternate_strategy_attempted,
    )
    if problem:
        result["problem"] = problem
        setattr(task_state, "_last_problem_record", problem)
    if isinstance(store, OperatorMemoryStore):
        store.record_outcome(evaluation, goal=str(getattr(task_state, "goal", "")).strip())
        if problem and problem.get("lesson_key"):
            store.record_lesson(
                {
                    "lesson_key": problem.get("lesson_key", ""),
                    "lesson": problem.get("stored_lesson", ""),
                    "category": problem.get("failure_category", ""),
                    "tool": problem.get("tool", ""),
                    "domain": problem.get("domain", ""),
                    "strategy_family": problem.get("desktop_strategy_family", "") or problem.get("open_strategy_family", ""),
                    "validator_family": problem.get("desktop_validator_family", ""),
                    "problem_key": problem.get("problem_key", ""),
                }
            )
    refresh_operator_intelligence_context(task_state)
    return evaluation


def guard_repeated_failed_action(task_state, tool_name: str, args: Dict[str, Any] | None = None) -> Dict[str, Any]:
    action_signature = build_action_signature(tool_name, args if isinstance(args, dict) else {})
    failure_count = _previous_failures(task_state, action_signature)
    if failure_count < 2:
        return {}
    summary = "Stopped repeating the same action because it recently failed or made no progress multiple times without a confirming success."
    return {
        "ok": False,
        "blocked": True,
        "reason": "repeat_budget_exhausted",
        "summary": summary,
        "message": summary,
        "policy": {
            "decision": "block",
            "summary": summary,
            "reasons": [
                "The recent run history for this exact action signature already shows repeated failure or no progress.",
            ],
        },
    }


def guard_repeated_failed_desktop_strategy(task_state, tool_name: str, args: Dict[str, Any] | None = None) -> Dict[str, Any]:
    safe_args = args if isinstance(args, dict) else {}
    target_signature = _trim_text(safe_args.get("target_signature", ""), limit=220)
    strategy_family = _trim_text(safe_args.get("strategy_family", ""), limit=60)
    validator_family = _trim_text(safe_args.get("validator_family", ""), limit=60)
    if not target_signature or not strategy_family:
        return {}
    force_switch = bool(safe_args.get("force_strategy_switch", False))
    recent_failures = 0
    for item in _recent_evaluation_items(task_state, limit=10):
        if _trim_text(item.get("tool", ""), limit=80) != _trim_text(tool_name, limit=80):
            continue
        if _trim_text(item.get("target_signature", ""), limit=220) != target_signature:
            continue
        if _trim_text(item.get("strategy_family", ""), limit=60) != strategy_family:
            continue
        if _trim_text(item.get("status", ""), limit=40) in _FAIL_LIKE_OUTCOMES:
            recent_failures += 1
    if recent_failures < (1 if force_switch else 2):
        return {}
    summary = (
        "Stopped the bounded desktop step because the same strategy family already failed for this target and the next attempt needs a materially different method."
        if force_switch
        else "Stopped repeating the same bounded desktop strategy family after repeated failure or no visible progress for this target."
    )
    return {
        "ok": False,
        "blocked": True,
        "reason": "desktop_strategy_family_exhausted",
        "summary": summary,
        "message": summary,
        "policy": {
            "decision": "block",
            "summary": summary,
            "reasons": [
                "The recent run history for this desktop target already shows repeated failure or no progress for the same strategy family.",
            ],
        },
        "desktop_strategy": {
            "strategy_family": strategy_family,
            "validator_family": validator_family,
            "target_signature": target_signature,
            "force_strategy_switch": force_switch,
        },
    }


def guard_repeated_failed_open_family(task_state, args: Dict[str, Any] | None = None) -> Dict[str, Any]:
    safe_args = args if isinstance(args, dict) else {}
    target_signature = _trim_text(safe_args.get("target_signature", ""), limit=220)
    predicted_strategy = choose_windows_open_strategy(
        {
            "target_classification": _trim_text(
                safe_args.get("target_type", "") or safe_args.get("target_classification", ""),
                limit=80,
            ),
            "exists": not bool(safe_args.get("target_missing", False)),
        },
        preferred_method=_trim_text(
            safe_args.get("preferred_method", "")
            or safe_args.get("requested_method", "")
            or safe_args.get("strategy_family", ""),
            limit=60,
        ),
        avoid_strategy_families=[
            _trim_text(item, limit=60)
            for item in list(safe_args.get("avoid_strategy_families", []))
            if _trim_text(item, limit=60)
        ],
        existing_window_match=False,
        force_strategy_switch=bool(safe_args.get("force_strategy_switch", False)),
    )
    strategy_family = _trim_text(
        safe_args.get("strategy_family", "") or predicted_strategy.get("strategy_family", ""),
        limit=60,
    )
    if not target_signature or not strategy_family:
        return {}

    force_switch = bool(safe_args.get("force_strategy_switch", False))
    recent_failures = 0
    for item in _recent_evaluation_items(task_state, limit=10):
        if _trim_text(item.get("tool", ""), limit=80) != "desktop_open_target":
            continue
        if _trim_text(item.get("target_signature", ""), limit=220) != target_signature:
            continue
        if _trim_text(item.get("strategy_family", ""), limit=60) != strategy_family:
            continue
        if _trim_text(item.get("status", ""), limit=40) in _FAIL_LIKE_OUTCOMES:
            recent_failures += 1

    if recent_failures < (1 if force_switch else 2):
        return {}

    summary = (
        "Stopped the Windows open request because the same strategy family already failed for this target and the next attempt needs a materially different method."
        if force_switch
        else "Stopped repeating the same Windows open strategy family after repeated failure or no visible progress for this target."
    )
    return {
        "ok": False,
        "blocked": True,
        "reason": "open_strategy_family_exhausted",
        "summary": summary,
        "message": summary,
        "open_target": {
            "target": _trim_text(safe_args.get("target", ""), limit=240),
            "target_signature": target_signature,
            "target_classification": _trim_text(
                safe_args.get("target_type", "") or safe_args.get("target_classification", ""),
                limit=80,
            ),
        },
        "open_strategy": {
            "strategy_family": strategy_family,
            "requested_method": _trim_text(
                safe_args.get("preferred_method", "") or safe_args.get("requested_method", ""),
                limit=80,
            ),
            "force_strategy_switch": force_switch,
        },
        "open_verification": {
            "status": "not_attempted_blocked_repeat",
            "confidence": "high",
            "note": summary,
            "strategy_family": strategy_family,
        },
        "policy": {
            "decision": "block",
            "summary": summary,
            "reasons": [
                f"Recent open attempts for the same target already exhausted the '{strategy_family}' strategy family.",
            ],
        },
    }


def build_environment_awareness(
    *,
    settings: Dict[str, Any] | None = None,
    email_status: Dict[str, Any] | None = None,
    execution_profile: str = "",
    lab_armed: bool = False,
) -> Dict[str, Any]:
    effective_settings = settings if isinstance(settings, dict) else {}
    safe_email = email_status if isinstance(email_status, dict) else {}
    capability_profile = normalize_execution_profile(execution_profile or effective_settings.get("execution_profile", "safe_bounded"))
    shells = [shell for shell in ("powershell", "cmd") if shutil.which("powershell.exe" if shell == "powershell" else "cmd.exe")]
    return {
        "os": platform.system(),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "execution_profile": capability_profile,
        "runtime_mode": "lab" if capability_profile == "sandboxed_full_access_lab" else "bounded",
        "available_shells": shells,
        "lab_armed": bool(lab_armed),
        "lab_constraints": lab_status_snapshot(settings=effective_settings, armed=lab_armed).get("constraints", []),
        "gmail_enabled": bool(safe_email.get("enabled", False)),
        "gmail_authenticated": bool(safe_email.get("authenticated", False)),
        "gmail_configured": bool(safe_email.get("configured", False)),
        "automations_enabled": True,
        "settings_version": _trim_text(effective_settings.get("_settings_version", ""), limit=80),
    }
