from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from core.windows_opening import (
    choose_windows_open_strategy,
    classify_open_target,
    infer_open_request_preferences,
)
from tools.desktop_backends import (
    inspect_process_details,
    launch_unowned_process,
    list_process_contexts,
    open_in_explorer,
    open_path_with_association,
    open_url_with_shell,
    probe_process_context,
    run_bounded_command,
    start_owned_process,
    stop_owned_process,
)
from tools.desktop_constants import (
    DESKTOP_DEFAULT_COMMAND_TIMEOUT_SECONDS,
    DESKTOP_DEFAULT_PROCESS_LIMIT,
    DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS,
    DESKTOP_DEFAULT_VERIFICATION_SAMPLES,
    DESKTOP_DEFAULT_WINDOW_LIMIT,
    DESKTOP_MAX_COMMAND_TIMEOUT_SECONDS,
    DESKTOP_MAX_PROCESS_LIMIT,
)


def _desktop():
    """Lazy accessor for the tools.desktop facade (avoids circular imports)."""
    import tools.desktop as _mod
    return _mod


def _current_desktop_context(*, limit: int = DESKTOP_DEFAULT_WINDOW_LIMIT) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]]:
    active_window = _desktop()._active_window_info()
    windows = _desktop()._enum_windows(limit=limit)
    observation = _desktop()._register_observation(active_window=active_window, windows=windows)
    return active_window, windows, observation


def _dedupe_windows(*windows_groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for group in windows_groups:
        for item in list(group or []):
            if not isinstance(item, dict):
                continue
            window_id = str(item.get("window_id", "")).strip()
            dedupe_key = window_id or f"{item.get('title', '')}|{item.get('pid', '')}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            merged.append(item)
    return merged


def _open_match_score(window: Dict[str, Any], target_info: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(window, dict) or not isinstance(target_info, dict):
        return {"score": 0, "reasons": []}

    title = str(window.get("title", "")).strip().lower()
    process_name = str(window.get("process_name", "")).strip().lower()
    class_name = str(window.get("class_name", "")).strip().lower()
    basename = str(target_info.get("basename", "")).strip().lower()
    stem = str(target_info.get("stem", "")).strip().lower()
    parent_name = str(target_info.get("parent_name", "")).strip().lower()
    target_class = str(target_info.get("target_classification", "")).strip().lower()
    title_hints = [str(item).strip().lower() for item in list(target_info.get("viewer_title_hints", [])) if str(item).strip()]
    process_hints = [str(item).strip().lower() for item in list(target_info.get("viewer_process_hints", [])) if str(item).strip()]

    score = 0
    reasons: List[str] = []
    if basename and basename in title:
        score += 85
        reasons.append("basename_in_title")
    elif stem and stem in title:
        score += 62
        reasons.append("stem_in_title")
    elif parent_name and parent_name in title and target_class == "folder_directory":
        score += 62
        reasons.append("folder_name_in_title")
    elif parent_name and parent_name in title and target_class in {"document_file", "image_media_file", "text_code_file"}:
        score += 24
        reasons.append("parent_in_title")

    for hint in title_hints:
        if hint and hint in title:
            score += 18
            reasons.append(f"title_hint:{hint}")
            break

    for hint in process_hints:
        if hint and (process_name == hint or hint in process_name):
            score += 34
            reasons.append(f"process_hint:{hint}")
            break

    if target_class == "folder_directory":
        if process_name == "explorer.exe":
            score += 24
            reasons.append("explorer_process")
        if "cabinetwclass" in class_name or "explorer" in title:
            score += 12
            reasons.append("explorer_window")
    elif target_class == "url_web_resource" and process_name in {"msedge.exe", "chrome.exe", "firefox.exe"}:
        score += 22
        reasons.append("browser_process")
    elif target_class == "executable_program":
        expected_process = f"{stem}.exe" if stem and not basename.endswith(".exe") else basename
        if expected_process and process_name == expected_process:
            score += 82
            reasons.append("exact_process")
        elif stem and stem in process_name:
            score += 54
            reasons.append("stem_in_process")

    if bool(window.get("is_active", False)) and score > 0:
        score += 8
        reasons.append("active_window")
    if bool(window.get("is_visible", False)) and score > 0:
        score += 4
        reasons.append("visible_window")
    return {"score": min(score, 100), "reasons": reasons}


def _best_open_window_candidate(windows: List[Dict[str, Any]], target_info: Dict[str, Any]) -> Dict[str, Any]:
    best_window: Dict[str, Any] = {}
    best_score = 0
    best_reasons: List[str] = []
    for window in list(windows or []):
        scored = _open_match_score(window, target_info)
        score = int(scored.get("score", 0) or 0)
        if score <= best_score:
            continue
        best_window = dict(window)
        best_score = score
        best_reasons = list(scored.get("reasons", [])) if isinstance(scored.get("reasons", []), list) else []
    if not best_window:
        return {}
    return {
        **best_window,
        "match_score": best_score,
        "match_reasons": best_reasons[:4],
    }


def _process_hint_snapshot(target_info: Dict[str, Any], *, launched_pid: int = 0) -> Dict[str, Any]:
    if launched_pid > 0:
        result = probe_process_context(pid=launched_pid)
        data = result.get("data", {}) if isinstance(result.get("data", {}), dict) else {}
        if data:
            return data
    for process_name in list(target_info.get("viewer_process_hints", []))[:3]:
        result = probe_process_context(process_name=str(process_name).strip())
        data = result.get("data", {}) if isinstance(result.get("data", {}), dict) else {}
        if data.get("running", False):
            return data
    return {}


def _sample_open_verification(
    target_info: Dict[str, Any],
    *,
    strategy_family: str,
    before_active_window: Dict[str, Any],
    before_windows: List[Dict[str, Any]],
    launched_pid: int = 0,
    sample_count: int = 3,
    interval_ms: int = 180,
) -> Dict[str, Any]:
    bounded_samples = max(2, min(4, int(sample_count or 3)))
    bounded_interval = max(80, min(320, int(interval_ms or 180))) / 1000.0
    before_ids = {
        str(item.get("window_id", "")).strip()
        for item in list(before_windows or [])
        if isinstance(item, dict) and str(item.get("window_id", "")).strip()
    }
    before_active_id = str(before_active_window.get("window_id", "")).strip()
    before_active_title = str(before_active_window.get("title", "")).strip()
    best_candidate: Dict[str, Any] = {}
    process_snapshot: Dict[str, Any] = {}
    samples: List[Dict[str, Any]] = []
    saw_new_match = False
    saw_existing_match = False
    saw_active_match = False
    saw_brief_match = False

    for index in range(bounded_samples):
        if index > 0:
            time.sleep(bounded_interval)
        active_window = _desktop()._active_window_info()
        visible_windows = _desktop()._enum_windows(include_minimized=True, include_hidden=True, limit=24)
        candidate = _best_open_window_candidate(_dedupe_windows([active_window], visible_windows), target_info)
        process_snapshot = _process_hint_snapshot(target_info, launched_pid=launched_pid) or process_snapshot
        candidate_score = int(candidate.get("match_score", 0) or 0)
        window_id = str(candidate.get("window_id", "")).strip()
        if candidate_score > int(best_candidate.get("match_score", 0) or 0):
            best_candidate = dict(candidate)
        if candidate_score >= 65:
            saw_brief_match = True
        if candidate_score >= 78:
            if window_id and window_id not in before_ids:
                saw_new_match = True
            elif window_id:
                saw_existing_match = True
            if bool(candidate.get("is_active", False)) or (window_id and window_id == str(active_window.get("window_id", "")).strip()):
                saw_active_match = True
        samples.append(
            {
                "active_window_title": _desktop()._trim_text(active_window.get("title", ""), limit=140),
                "active_window_process": _desktop()._trim_text(active_window.get("process_name", ""), limit=80),
                "candidate_title": _desktop()._trim_text(candidate.get("title", ""), limit=140),
                "candidate_process": _desktop()._trim_text(candidate.get("process_name", ""), limit=80),
                "candidate_window_id": _desktop()._trim_text(candidate.get("window_id", ""), limit=40),
                "candidate_score": candidate_score,
            }
        )

    active_window_after = _desktop()._active_window_info()
    active_window_changed = bool(
        str(active_window_after.get("window_id", "")).strip()
        and str(active_window_after.get("window_id", "")).strip() != before_active_id
    ) or bool(
        str(active_window_after.get("title", "")).strip()
        and str(active_window_after.get("title", "")).strip() != before_active_title
    )
    matched_window = bool(best_candidate)
    matched_existing_window = matched_window and str(best_candidate.get("window_id", "")).strip() in before_ids
    matched_active_window = matched_window and bool(best_candidate.get("is_active", False))
    process_detected = bool(process_snapshot.get("running", False))

    status = "not_observed"
    confidence = "low"
    note = "No clear window or process change confirmed that the target opened."
    if saw_new_match and (saw_active_match or active_window_changed):
        status = "verified_new_window"
        confidence = "high"
        note = "A new matching window surfaced and became active after the open attempt."
    elif saw_new_match:
        status = "verified_new_window"
        confidence = "medium"
        note = "A new matching window surfaced after the open attempt."
    elif matched_existing_window and saw_active_match:
        status = "verified_reused_window"
        confidence = "medium"
        note = "A matching existing viewer window appears to have been reused and surfaced."
    elif matched_existing_window:
        status = "likely_opened_background"
        confidence = "low"
        note = "A matching existing window was detected, but it did not clearly surface to the foreground."
    elif process_detected and str(target_info.get("target_classification", "")).strip() == "executable_program":
        status = "process_started_only"
        confidence = "low"
        note = "The target process started, but a visible window was not clearly confirmed."
    elif saw_brief_match:
        status = "brief_signal_only"
        confidence = "low"
        note = "A brief matching window signal appeared, but the result was not stable enough to confirm success."

    return {
        "status": status,
        "confidence": confidence,
        "note": note,
        "matched_window": matched_window,
        "matched_existing_window": matched_existing_window,
        "matched_active_window": matched_active_window,
        "likely_opened_behind": matched_existing_window and not saw_active_match,
        "process_detected": process_detected,
        "active_window_changed": active_window_changed,
        "matched_window_title": _desktop()._trim_text(best_candidate.get("title", ""), limit=180),
        "matched_window_id": _desktop()._trim_text(best_candidate.get("window_id", ""), limit=40),
        "matched_process_name": _desktop()._trim_text(best_candidate.get("process_name", ""), limit=120),
        "match_score": int(best_candidate.get("match_score", 0) or 0),
        "strategy_family": _desktop()._trim_text(strategy_family, limit=60),
        "samples": samples[:4],
    }


def _open_target_display(target_info: Dict[str, Any]) -> str:
    return str(target_info.get("basename", "") or target_info.get("target", "")).strip() or "target"


def _open_target_summary(target_info: Dict[str, Any], strategy_family: str, verification: Dict[str, Any]) -> str:
    target_display = _open_target_display(target_info)
    verification_note = str((verification or {}).get("note", "")).strip()
    if strategy_family == "focus_existing_window":
        return f"Focused the existing window for '{target_display}'."
    if verification_note:
        return f"Attempted to open '{target_display}'. {verification_note}"
    return f"Attempted to open '{target_display}' via {strategy_family.replace('_', ' ')}."


def _active_window_process_target(active_window: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(active_window, dict):
        return {}
    return {
        "pid": _desktop()._coerce_int(active_window.get("pid", 0), 0, minimum=0, maximum=10_000_000),
        "process_name": str(active_window.get("process_name", "")).strip(),
    }


def desktop_press_key_sequence(args: Dict[str, Any]) -> Dict[str, Any]:
    sequence_items = _desktop()._normalize_desktop_key_sequence(args.get("sequence", []))
    key_preview = _desktop()._desktop_key_sequence_chain_preview(sequence_items)
    validation_error = _desktop()._validate_desktop_key_sequence(sequence_items)
    if validation_error:
        active_window, windows, observation = _current_desktop_context()
        return _desktop()._desktop_result(
            ok=False,
            action="desktop_press_key_sequence",
            summary=validation_error,
            desktop_state=observation,
            error=validation_error,
            key_sequence_preview=key_preview,
            target_window=active_window,
        )

    strategy_context = _desktop()._prepare_desktop_strategy_context(
        args,
        action_name="desktop_press_key_sequence",
        default_strategy_family="direct_input",
        default_validator_family="text_input",
    )
    if not strategy_context.get("ok", False):
        return strategy_context.get("result", {})
    action_args = strategy_context.get("args", args) if isinstance(strategy_context.get("args", args), dict) else dict(args)
    strategy_view = strategy_context.get("strategy", {}) if isinstance(strategy_context.get("strategy", {}), dict) else {}
    recovered = strategy_context.get("recovered", {}) if isinstance(strategy_context.get("recovered", {}), dict) else {}

    token, observation, observation_error = _desktop()._validate_fresh_observation(action_args)
    evidence_ref = _desktop()._latest_evidence_ref_for_observation(token)
    active_window, windows, current_observation = _current_desktop_context()
    if observation_error:
        return _desktop()._desktop_result(
            ok=False,
            action="desktop_press_key_sequence",
            summary=observation_error,
            desktop_state=current_observation,
            error=observation_error,
            key_sequence_preview=key_preview,
            desktop_evidence_ref=evidence_ref,
            target_window=active_window,
        )

    if not active_window or not _desktop()._foreground_window_matches(observation, active_window):
        message = "The previously inspected target window is no longer active. Focus the window and inspect desktop state again before sending a bounded key sequence."
        return _desktop()._desktop_result(
            ok=False,
            action="desktop_press_key_sequence",
            summary=message,
            desktop_state=current_observation,
            error=message,
            key_sequence_preview=key_preview,
            desktop_evidence_ref=evidence_ref,
            target_window=active_window,
        )
    if active_window and not _desktop()._window_is_on_primary_monitor(active_window):
        return _desktop()._primary_monitor_activity_error(
            "desktop_press_key_sequence",
            active_window,
            windows=windows,
            desktop_evidence_ref=evidence_ref,
        )

    checkpoint_reason = str(action_args.get("checkpoint_reason", "")).strip() or (
        f"Pressing the bounded key sequence {key_preview} in '{active_window.get('title', 'the active window')}' requires explicit approval in this control pass."
    )
    checkpoint_target = active_window.get("title", "") or "active window"
    if not _desktop()._approval_granted(action_args):
        if not _desktop()._evidence_ref_has_screenshot(evidence_ref):
            message = "Approval-gated desktop key sequences need a screenshot-backed inspection of the active window first."
            return _desktop()._desktop_result(
                ok=False,
                action="desktop_press_key_sequence",
                summary=message,
                desktop_state=current_observation,
                error=message,
                key_sequence_preview=key_preview,
                desktop_evidence_ref=evidence_ref,
                target_window=active_window,
            )
        return _desktop()._pause_desktop_action(
            action="desktop_press_key_sequence",
            summary=f"Approval required before pressing {key_preview} in '{checkpoint_target}'.",
            active_window=active_window,
            windows=windows,
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=f"{checkpoint_target} :: {key_preview}",
            checkpoint_resume_args={
                "sequence": sequence_items,
                "observation_token": token,
                "expected_window_id": active_window.get("window_id", ""),
                "expected_window_title": active_window.get("title", ""),
                "evidence_id": evidence_ref.get("evidence_id", ""),
            },
            key_sequence_preview=key_preview,
            desktop_evidence_ref=evidence_ref,
        )

    ok = _desktop()._send_key_sequence_chain(sequence_items)
    active_after, visible_after, observation_after = _current_desktop_context()
    verification = (
        _desktop()._sample_desktop_action_verification(
            action="desktop_press_key_sequence",
            validator_family=str(strategy_view.get("validator_family", "") or "text_input"),
            strategy_family=str(strategy_view.get("strategy_family", "") or "direct_input"),
            before_active_window=active_window,
            before_windows=windows,
            expected_title=str(action_args.get("expected_window_title", "") or active_window.get("title", "")).strip(),
            expected_window_id=str(action_args.get("expected_window_id", "") or active_window.get("window_id", "")).strip(),
            expected_process_names=[str(active_window.get("process_name", "")).strip()],
            target_description=key_preview or "bounded key sequence",
            sample_count=_desktop()._coerce_int(action_args.get("verification_samples", DESKTOP_DEFAULT_VERIFICATION_SAMPLES), DESKTOP_DEFAULT_VERIFICATION_SAMPLES, minimum=2, maximum=4),
            interval_ms=_desktop()._coerce_int(action_args.get("verification_interval_ms", DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS), DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS, minimum=80, maximum=320),
        )
        if ok
        else {}
    )
    return _desktop()._desktop_result(
        ok=ok,
        action="desktop_press_key_sequence",
        summary=(
            f"Pressed {key_preview} in '{active_after.get('title', active_window.get('title', 'the active window'))}'."
            if ok
            else f"Could not press the bounded key sequence {key_preview} in '{active_window.get('title', 'the active window')}'."
        ),
        desktop_state=observation_after,
        error="" if ok else f"Could not press the bounded key sequence {key_preview}.",
        approval_status="approved",
        workflow_resumed=_desktop()._coerce_bool(action_args.get("resume_from_checkpoint", False), False),
        key_sequence_preview=key_preview,
        desktop_evidence_ref=evidence_ref,
        target_window=active_after or active_window,
        recovery=recovered.get("recovery", {}),
        recovery_attempts=recovered.get("recovery_attempts", []),
        window_readiness=recovered.get("readiness", {}),
        visual_stability=recovered.get("visual_stability", {}),
        process_context=recovered.get("process_context", {}),
        scene=recovered.get("scene", {}),
        desktop_strategy=strategy_view,
        desktop_verification=verification,
    )


def desktop_list_processes(args: Dict[str, Any]) -> Dict[str, Any]:
    active_window, windows, observation = _current_desktop_context()
    query = str(args.get("query", "")).strip()
    limit = _desktop()._coerce_int(
        args.get("limit", DESKTOP_DEFAULT_PROCESS_LIMIT),
        DESKTOP_DEFAULT_PROCESS_LIMIT,
        minimum=1,
        maximum=DESKTOP_MAX_PROCESS_LIMIT,
    )
    include_background = _desktop()._coerce_bool(args.get("include_background", True), True)
    process_result = list_process_contexts(query=query, limit=limit, include_background=include_background)
    payload = process_result.get("data", {}) if isinstance(process_result.get("data", {}), dict) else {}
    processes = payload.get("processes", []) if isinstance(payload.get("processes", []), list) else []
    summary = str(process_result.get("message", "")).strip() or (
        f"Listed {len(processes)} bounded process candidates."
        if processes
        else "No bounded desktop processes matched the current query."
    )
    return _desktop()._desktop_result(
        ok=bool(process_result.get("ok", False)),
        action="desktop_list_processes",
        summary=summary,
        desktop_state=observation,
        error=str(process_result.get("error", "")).strip(),
        processes=processes,
        process_action={
            "action": "list",
            "reason": str(process_result.get("reason", "process_inspected")).strip() or "process_inspected",
            "summary": summary,
        },
        process_context=processes[0] if len(processes) == 1 and isinstance(processes[0], dict) else {},
        target_window=active_window,
    )


def desktop_inspect_process(args: Dict[str, Any]) -> Dict[str, Any]:
    active_window, windows, observation = _current_desktop_context()
    pid = _desktop()._coerce_int(args.get("pid", 0), 0, minimum=0, maximum=10_000_000)
    process_name = str(args.get("process_name", "")).strip()
    owned_label = str(args.get("owned_label", "")).strip()
    if pid <= 0 and not process_name and not owned_label:
        active_target = _active_window_process_target(active_window)
        pid = int(active_target.get("pid", 0) or 0)
        process_name = str(active_target.get("process_name", "")).strip()
    if pid <= 0 and not process_name and not owned_label:
        message = "No bounded desktop process target was available. Provide pid, process_name, owned_label, or inspect a surfaced window first."
        return _desktop()._desktop_result(
            ok=False,
            action="desktop_inspect_process",
            summary=message,
            desktop_state=observation,
            error=message,
            target_window=active_window,
        )

    child_limit = _desktop()._coerce_int(args.get("child_limit", 4), 4, minimum=0, maximum=8)
    process_result = inspect_process_details(pid=pid, process_name=process_name or owned_label, child_limit=child_limit)
    payload = process_result.get("data", {}) if isinstance(process_result.get("data", {}), dict) else {}
    process_context = payload.get("process", {}) if isinstance(payload.get("process", {}), dict) else {}
    children = payload.get("children", []) if isinstance(payload.get("children", []), list) else []
    summary = str(process_result.get("message", "")).strip() or str(process_context.get("summary", "")).strip() or "Inspected the requested bounded process context."
    return _desktop()._desktop_result(
        ok=bool(process_result.get("ok", False)),
        action="desktop_inspect_process",
        summary=summary,
        desktop_state=observation,
        error=str(process_result.get("error", "")).strip(),
        process_context=process_context,
        processes=children,
        process_action={
            "action": "inspect",
            "pid": int(process_context.get("pid", pid) or pid),
            "process_name": str(process_context.get("process_name", "") or process_name or owned_label),
            "owned": bool(payload.get("owned", False)),
            "owned_label": str(payload.get("owned_label", "")).strip(),
            "reason": str(process_result.get("reason", "process_inspected")).strip() or "process_inspected",
            "summary": summary,
        },
        target_window=active_window,
    )


def desktop_start_process(args: Dict[str, Any]) -> Dict[str, Any]:
    action_args = dict(args)
    strategy_view = _desktop()._desktop_strategy_view(
        action_args,
        action="desktop_start_process",
        default_strategy_family="direct_launch",
        default_validator_family="open_launch",
    )
    active_window, windows, observation = _current_desktop_context()
    token = str(action_args.get("observation_token", "")).strip()
    evidence_ref = _desktop()._latest_evidence_ref_for_observation(token) if token else {}
    executable = str(action_args.get("executable", "")).strip()
    arguments = action_args.get("arguments", [])
    if not isinstance(arguments, list):
        arguments = []
    bounded_arguments = [_desktop()._trim_text(item, limit=180) for item in arguments[:8] if _desktop()._trim_text(item, limit=180)]
    owned_label = str(action_args.get("owned_label", "")).strip() or Path(executable).stem
    checkpoint_target = owned_label or executable or "bounded desktop process"
    checkpoint_reason = str(action_args.get("checkpoint_reason", "")).strip() or (
        f"Starting the bounded process '{checkpoint_target}' requires explicit approval in this control pass."
    )
    if not executable:
        message = "Provide an executable path before starting a bounded desktop process."
        return _desktop()._desktop_result(
            ok=False,
            action="desktop_start_process",
            summary=message,
            desktop_state=observation,
            error=message,
            desktop_evidence_ref=evidence_ref,
            target_window=active_window,
        )
    if not _desktop()._approval_granted(action_args):
        return _desktop()._pause_desktop_action(
            action="desktop_start_process",
            summary=f"Approval required before starting '{checkpoint_target}'.",
            active_window=active_window,
            windows=windows,
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=checkpoint_target,
            checkpoint_resume_args={
                "executable": executable,
                "arguments": bounded_arguments,
                "cwd": str(action_args.get("cwd", "")).strip(),
                "owned_label": owned_label,
                "shell_kind": str(action_args.get("shell_kind", "")).strip(),
                "observation_token": token,
                "evidence_id": evidence_ref.get("evidence_id", ""),
            },
            desktop_evidence_ref=evidence_ref,
        )

    process_result = start_owned_process(
        executable=executable,
        args=bounded_arguments,
        cwd=str(action_args.get("cwd", "")).strip(),
        env=action_args.get("env", {}) if isinstance(action_args.get("env", {}), dict) else {},
        owned_label=owned_label,
    )
    payload = process_result.get("data", {}) if isinstance(process_result.get("data", {}), dict) else {}
    process_context = payload.get("process", {}) if isinstance(payload.get("process", {}), dict) else {}
    observation_after = _desktop()._register_observation(active_window=_desktop()._active_window_info(), windows=_desktop()._enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT))
    summary = str(process_result.get("message", "")).strip() or "Started the requested bounded process."
    verification = (
        _desktop()._sample_desktop_action_verification(
            action="desktop_start_process",
            validator_family=str(strategy_view.get("validator_family", "") or "open_launch"),
            strategy_family=str(strategy_view.get("strategy_family", "") or "direct_launch"),
            before_active_window=active_window,
            before_windows=windows,
            expected_title=Path(executable).stem,
            expected_process_names=[str(process_context.get("process_name", "")).strip() or Path(executable).name.lower()],
            target_description=checkpoint_target,
            launched_pid=int(process_context.get("pid", 0) or 0),
            sample_count=_desktop()._coerce_int(action_args.get("verification_samples", DESKTOP_DEFAULT_VERIFICATION_SAMPLES), DESKTOP_DEFAULT_VERIFICATION_SAMPLES, minimum=2, maximum=4),
            interval_ms=_desktop()._coerce_int(action_args.get("verification_interval_ms", DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS), DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS, minimum=80, maximum=320),
        )
        if bool(process_result.get("ok", False))
        else {}
    )
    return _desktop()._desktop_result(
        ok=bool(process_result.get("ok", False)),
        action="desktop_start_process",
        summary=summary,
        desktop_state=observation_after,
        error=str(process_result.get("error", "")).strip(),
        approval_status="approved",
        workflow_resumed=_desktop()._coerce_bool(action_args.get("resume_from_checkpoint", False), False),
        process_context=process_context,
        process_action={
            "action": "start",
            "pid": int(process_context.get("pid", 0) or 0),
            "process_name": str(process_context.get("process_name", "")).strip() or Path(executable).name,
            "owned": bool(payload.get("owned", False)),
            "owned_label": str(payload.get("owned_label", "")).strip(),
            "reason": str(process_result.get("reason", "process_started")).strip() or "process_started",
            "summary": summary,
        },
        target_window=active_window,
        desktop_strategy=strategy_view,
        desktop_verification=verification,
    )


def desktop_stop_process(args: Dict[str, Any]) -> Dict[str, Any]:
    active_window, windows, observation = _current_desktop_context()
    token = str(args.get("observation_token", "")).strip()
    evidence_ref = _desktop()._latest_evidence_ref_for_observation(token) if token else {}
    pid = _desktop()._coerce_int(args.get("pid", 0), 0, minimum=0, maximum=10_000_000)
    owned_label = str(args.get("owned_label", "")).strip()
    if pid <= 0 and not owned_label:
        active_target = _active_window_process_target(active_window)
        pid = int(active_target.get("pid", 0) or 0)
    checkpoint_target = owned_label or (str(pid) if pid > 0 else "owned bounded process")
    checkpoint_reason = str(args.get("checkpoint_reason", "")).strip() or (
        f"Stopping the bounded owned process '{checkpoint_target}' requires explicit approval in this control pass."
    )
    if pid <= 0 and not owned_label:
        message = "Provide an owned process pid or owned_label before stopping a bounded desktop process."
        return _desktop()._desktop_result(
            ok=False,
            action="desktop_stop_process",
            summary=message,
            desktop_state=observation,
            error=message,
            desktop_evidence_ref=evidence_ref,
            target_window=active_window,
        )
    if not _desktop()._approval_granted(args):
        return _desktop()._pause_desktop_action(
            action="desktop_stop_process",
            summary=f"Approval required before stopping '{checkpoint_target}'.",
            active_window=active_window,
            windows=windows,
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=checkpoint_target,
            checkpoint_resume_args={
                "pid": pid,
                "owned_label": owned_label,
                "wait_seconds": _desktop()._coerce_int(args.get("wait_seconds", 2), 2, minimum=1, maximum=5),
                "observation_token": token,
                "evidence_id": evidence_ref.get("evidence_id", ""),
            },
            desktop_evidence_ref=evidence_ref,
        )

    process_result = stop_owned_process(
        pid=pid,
        owned_label=owned_label,
        wait_seconds=float(_desktop()._coerce_int(args.get("wait_seconds", 2), 2, minimum=1, maximum=5)),
    )
    payload = process_result.get("data", {}) if isinstance(process_result.get("data", {}), dict) else {}
    process_context = payload.get("process", {}) if isinstance(payload.get("process", {}), dict) else {}
    observation_after = _desktop()._register_observation(active_window=_desktop()._active_window_info(), windows=_desktop()._enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT))
    summary = str(process_result.get("message", "")).strip() or "Stopped the requested bounded owned process."
    return _desktop()._desktop_result(
        ok=bool(process_result.get("ok", False)),
        action="desktop_stop_process",
        summary=summary,
        desktop_state=observation_after,
        error=str(process_result.get("error", "")).strip(),
        approval_status="approved",
        workflow_resumed=_desktop()._coerce_bool(args.get("resume_from_checkpoint", False), False),
        process_context=process_context,
        process_action={
            "action": "stop",
            "pid": int(process_context.get("pid", pid) or pid),
            "process_name": str(process_context.get("process_name", "")).strip() or str(checkpoint_target).strip(),
            "owned": bool(payload.get("owned", False)),
            "owned_label": str(payload.get("owned_label", "")).strip(),
            "reason": str(process_result.get("reason", "process_stopped")).strip() or "process_stopped",
            "summary": summary,
        },
        target_window=active_window,
    )


def desktop_run_command(args: Dict[str, Any]) -> Dict[str, Any]:
    action_args = dict(args)
    strategy_view = _desktop()._desktop_strategy_view(
        action_args,
        action="desktop_run_command",
        default_strategy_family="command_open" if str(action_args.get("validator_family", "")).strip() == "open_launch" else "bounded_command",
        default_validator_family=str(action_args.get("validator_family", "")).strip(),
    )
    active_window, windows, observation = _current_desktop_context()
    token = str(action_args.get("observation_token", "")).strip()
    evidence_ref = _desktop()._latest_evidence_ref_for_observation(token) if token else {}
    command = str(action_args.get("command", "")).strip()
    shell_kind = str(action_args.get("shell_kind", "powershell")).strip().lower() or "powershell"
    timeout_seconds = _desktop()._coerce_int(
        action_args.get("timeout_seconds", DESKTOP_DEFAULT_COMMAND_TIMEOUT_SECONDS),
        DESKTOP_DEFAULT_COMMAND_TIMEOUT_SECONDS,
        minimum=1,
        maximum=DESKTOP_MAX_COMMAND_TIMEOUT_SECONDS,
    )
    checkpoint_reason = str(action_args.get("checkpoint_reason", "")).strip() or (
        "Running the requested bounded local command requires explicit approval in this control pass."
    )
    checkpoint_target = _desktop()._trim_text(command, limit=120) or "bounded command"
    if not command:
        message = "Provide a bounded command string before running a local desktop command."
        return _desktop()._desktop_result(
            ok=False,
            action="desktop_run_command",
            summary=message,
            desktop_state=observation,
            error=message,
            desktop_evidence_ref=evidence_ref,
            target_window=active_window,
        )
    if not _desktop()._approval_granted(action_args):
        return _desktop()._pause_desktop_action(
            action="desktop_run_command",
            summary=f"Approval required before running '{checkpoint_target}'.",
            active_window=active_window,
            windows=windows,
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=checkpoint_target,
            checkpoint_resume_args={
                "command": command,
                "cwd": str(action_args.get("cwd", "")).strip(),
                "shell_kind": shell_kind,
                "timeout_seconds": timeout_seconds,
                "observation_token": token,
                "evidence_id": evidence_ref.get("evidence_id", ""),
            },
            desktop_evidence_ref=evidence_ref,
        )

    command_result = run_bounded_command(
        command=command,
        cwd=str(action_args.get("cwd", "")).strip(),
        env=action_args.get("env", {}) if isinstance(action_args.get("env", {}), dict) else {},
        timeout_seconds=float(timeout_seconds),
        shell_kind=shell_kind,
    )
    payload = command_result.get("data", {}) if isinstance(command_result.get("data", {}), dict) else {}
    observation_after = _desktop()._register_observation(active_window=_desktop()._active_window_info(), windows=_desktop()._enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT))
    summary = str(command_result.get("message", "")).strip() or str(payload.get("summary", "")).strip() or "Ran the bounded local command."
    verification_family = str(strategy_view.get("validator_family", "")).strip()
    verification = (
        _desktop()._sample_desktop_action_verification(
            action="desktop_run_command",
            validator_family=verification_family,
            strategy_family=str(strategy_view.get("strategy_family", "") or "command_open"),
            before_active_window=active_window,
            before_windows=windows,
            target_description=checkpoint_target,
            sample_count=_desktop()._coerce_int(action_args.get("verification_samples", DESKTOP_DEFAULT_VERIFICATION_SAMPLES), DESKTOP_DEFAULT_VERIFICATION_SAMPLES, minimum=2, maximum=4),
            interval_ms=_desktop()._coerce_int(action_args.get("verification_interval_ms", DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS), DESKTOP_DEFAULT_VERIFICATION_INTERVAL_MS, minimum=80, maximum=320),
        )
        if bool(command_result.get("ok", False)) and verification_family == "open_launch"
        else {}
    )
    return _desktop()._desktop_result(
        ok=bool(command_result.get("ok", False)),
        action="desktop_run_command",
        summary=summary,
        desktop_state=observation_after,
        error=str(command_result.get("error", "")).strip(),
        approval_status="approved",
        workflow_resumed=_desktop()._coerce_bool(action_args.get("resume_from_checkpoint", False), False),
        command_result=payload,
        target_window=active_window,
        desktop_strategy=strategy_view,
        desktop_verification=verification,
    )


def desktop_open_target(args: Dict[str, Any]) -> Dict[str, Any]:
    active_window, windows, observation = _current_desktop_context(limit=20)
    token = str(args.get("observation_token", "")).strip()
    evidence_ref = _desktop()._latest_evidence_ref_for_observation(token) if token else {}
    target = str(args.get("target", "")).strip()
    explicit_target_type = str(args.get("target_type", "")).strip()
    cwd = str(args.get("cwd", "")).strip()
    planning_goal = str(args.get("planning_goal", "") or args.get("goal", "")).strip()
    requested_method = str(args.get("preferred_method", "") or args.get("requested_method", "")).strip()
    bounded_arguments = [
        _desktop()._trim_text(item, limit=180)
        for item in list(args.get("arguments", []))[:8]
        if _desktop()._trim_text(item, limit=180)
    ]

    if not target:
        message = "Provide a Windows file, folder, URL, or executable target before trying to open it."
        result = _desktop()._desktop_result(
            ok=False,
            action="desktop_open_target",
            summary=message,
            desktop_state=observation,
            error=message,
            desktop_evidence_ref=evidence_ref,
            target_window=active_window,
        )
        result["open_target"] = {
            "target": "",
            "target_classification": "unknown_ambiguous_path",
            "target_signature": "",
        }
        return result

    target_info = classify_open_target(target, cwd=cwd, explicit_target_type=explicit_target_type)
    request_preferences = infer_open_request_preferences(
        " ".join(part for part in (planning_goal, requested_method, explicit_target_type) if str(part).strip()),
        args,
    )
    existing_window = _best_open_window_candidate(_dedupe_windows([active_window], windows), target_info)
    existing_window_match = int(existing_window.get("match_score", 0) or 0) >= 78
    avoid_strategy_families = [
        str(item).strip()
        for item in list(args.get("avoid_strategy_families", []))
        if str(item).strip()
    ]
    strategy = choose_windows_open_strategy(
        target_info,
        preferred_method=request_preferences.get("preferred_method", "") or requested_method,
        avoid_strategy_families=avoid_strategy_families,
        existing_window_match=existing_window_match,
        force_strategy_switch=bool(
            request_preferences.get("force_strategy_switch", False) or _desktop()._coerce_bool(args.get("force_strategy_switch", False), False)
        ),
    )
    strategy_family = str(strategy.get("strategy_family", "")).strip()
    if strategy_family == "focus_existing_window" and not existing_window_match:
        strategy = choose_windows_open_strategy(
            target_info,
            preferred_method="",
            avoid_strategy_families=[*avoid_strategy_families, "focus_existing_window"],
            existing_window_match=False,
            force_strategy_switch=True,
        )
        strategy_family = str(strategy.get("strategy_family", "")).strip()

    target_display = _open_target_display(target_info)
    checkpoint_reason = str(args.get("checkpoint_reason", "")).strip() or (
        f"Opening '{target_display}' with the Windows {strategy_family.replace('_', ' ')} path requires explicit approval in this control pass."
    )
    checkpoint_target = target_display
    if not _desktop()._approval_granted(args):
        paused = _desktop()._pause_desktop_action(
            action="desktop_open_target",
            summary=f"Approval required before opening '{target_display}'.",
            active_window=active_window,
            windows=windows,
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=checkpoint_target,
            checkpoint_resume_args={
                "target": target,
                "target_type": explicit_target_type,
                "preferred_method": requested_method or request_preferences.get("preferred_method", ""),
                "force_strategy_switch": bool(
                    request_preferences.get("force_strategy_switch", False)
                    or _desktop()._coerce_bool(args.get("force_strategy_switch", False), False)
                ),
                "cwd": cwd,
                "arguments": bounded_arguments,
                "env": args.get("env", {}) if isinstance(args.get("env", {}), dict) else {},
                "avoid_strategy_families": avoid_strategy_families,
                "verification_samples": _desktop()._coerce_int(args.get("verification_samples", 3), 3, minimum=2, maximum=4),
                "verification_interval_ms": _desktop()._coerce_int(args.get("verification_interval_ms", 180), 180, minimum=80, maximum=320),
                "observation_token": token,
                "evidence_id": evidence_ref.get("evidence_id", ""),
            },
            desktop_evidence_ref=evidence_ref,
        )
        paused["open_target"] = target_info
        paused["open_strategy"] = {
            **strategy,
            "existing_window_match": existing_window_match,
            "existing_window": existing_window,
        }
        return paused

    verification_samples = _desktop()._coerce_int(args.get("verification_samples", 3), 3, minimum=2, maximum=4)
    verification_interval_ms = _desktop()._coerce_int(args.get("verification_interval_ms", 180), 180, minimum=80, maximum=320)
    process_context: Dict[str, Any] = {}
    target_window: Dict[str, Any] = dict(existing_window) if existing_window else {}
    recovery: Dict[str, Any] = {}
    recovery_attempts: List[Dict[str, Any]] = []
    window_readiness: Dict[str, Any] = {}
    visual_stability: Dict[str, Any] = {}
    scene: Dict[str, Any] = {}
    open_payload: Dict[str, Any] = {}
    launched_pid = 0

    if strategy_family == "focus_existing_window":
        recovery_result = _desktop()._execute_window_recovery(
            {
                "window_id": str(existing_window.get("window_id", "")).strip(),
                "title": str(existing_window.get("title", "")).strip(),
                "expected_window_id": str(existing_window.get("window_id", "")).strip(),
                "expected_window_title": str(existing_window.get("title", "")).strip(),
                "exact": True,
                "limit": 16,
                "ui_limit": 6,
                "max_attempts": 1,
                "wait_seconds": 1.4,
                "poll_interval_seconds": 0.14,
                "stability_samples": 2,
                "stability_interval_ms": 120,
            },
            action_name="desktop_open_target",
        )
        recovery = recovery_result.get("recovery", {}) if isinstance(recovery_result.get("recovery", {}), dict) else {}
        recovery_attempts = recovery_result.get("recovery_attempts", []) if isinstance(recovery_result.get("recovery_attempts", []), list) else []
        window_readiness = recovery_result.get("readiness", {}) if isinstance(recovery_result.get("readiness", {}), dict) else {}
        visual_stability = recovery_result.get("visual_stability", {}) if isinstance(recovery_result.get("visual_stability", {}), dict) else {}
        process_context = recovery_result.get("process_context", {}) if isinstance(recovery_result.get("process_context", {}), dict) else {}
        scene = recovery_result.get("scene", {}) if isinstance(recovery_result.get("scene", {}), dict) else {}
        target_window = recovery_result.get("target_window", {}) if isinstance(recovery_result.get("target_window", {}), dict) else target_window
        open_payload = {
            "ok": recovery.get("state") == "ready",
            "backend": "window_recovery",
            "reason": _desktop()._trim_text(recovery.get("reason", "") or "existing_window_focus", limit=80),
            "message": _desktop()._trim_text(recovery.get("summary", "") or f"Focused the existing window for '{target_display}'.", limit=220),
            "error": "" if recovery.get("state") == "ready" else _desktop()._trim_text(recovery.get("summary", "") or "Could not focus the matching existing window.", limit=220),
            "data": {
                "target": target_info.get("normalized_target", "") or target_info.get("target", ""),
                "window_id": str(target_window.get("window_id", "")).strip(),
                "process": process_context,
            },
        }
    elif strategy_family == "executable_launch":
        open_payload = launch_unowned_process(
            executable=str(target_info.get("normalized_target", "") or target),
            args=bounded_arguments,
            cwd=cwd,
            env=args.get("env", {}) if isinstance(args.get("env", {}), dict) else {},
        )
    elif strategy_family == "association_open":
        open_payload = open_path_with_association(target=str(target_info.get("normalized_target", "") or target))
    elif strategy_family == "url_browser":
        open_payload = open_url_with_shell(target=str(target_info.get("target", "") or target))
    else:
        open_payload = open_in_explorer(
            target=str(target_info.get("normalized_target", "") or target),
            select_target=bool(target_info.get("is_file", False)),
        )

    payload = open_payload.get("data", {}) if isinstance(open_payload.get("data", {}), dict) else {}
    if not process_context:
        process_context = payload.get("process", {}) if isinstance(payload.get("process", {}), dict) else {}
    launched_pid = _desktop()._coerce_int(payload.get("pid", 0), 0, minimum=0, maximum=10_000_000)
    backend_reason = _desktop()._trim_text(open_payload.get("reason", ""), limit=80).lower()
    should_verify_open = bool(open_payload.get("ok", False)) or backend_reason in {
        "association_opened",
        "existing_window_focus",
        "explorer_opened",
        "process_started",
        "url_opened",
    }
    if should_verify_open:
        verification = _sample_open_verification(
            target_info,
            strategy_family=strategy_family,
            before_active_window=active_window,
            before_windows=windows,
            launched_pid=launched_pid,
            sample_count=verification_samples,
            interval_ms=verification_interval_ms,
        )
    else:
        verification_note = (
            "The target does not exist, so Windows never attempted the open request."
            if backend_reason == "target_missing"
            else _desktop()._trim_text(open_payload.get("message", "") or open_payload.get("error", ""), limit=220)
            or "The open request did not reach a real Windows launch or association path."
        )
        verification = {
            "status": "not_attempted_missing_target" if backend_reason == "target_missing" else "launcher_failed",
            "confidence": "high",
            "note": verification_note,
            "matched_window": False,
            "matched_existing_window": False,
            "matched_active_window": False,
            "likely_opened_behind": False,
            "process_detected": False,
            "active_window_changed": False,
            "matched_window_title": "",
            "matched_window_id": "",
            "matched_process_name": "",
            "match_score": 0,
            "strategy_family": _desktop()._trim_text(strategy_family, limit=60),
            "samples": [],
        }
    if not target_window:
        target_window = {
            "window_id": str(verification.get("matched_window_id", "")).strip(),
            "title": str(verification.get("matched_window_title", "")).strip(),
            "process_name": str(verification.get("matched_process_name", "")).strip(),
            "is_active": bool(verification.get("matched_active_window", False)),
        }
    if not process_context and verification.get("matched_process_name"):
        process_context = {
            "process_name": str(verification.get("matched_process_name", "")).strip(),
            "running": bool(verification.get("process_detected", False)),
        }

    observation_after = _desktop()._register_observation(
        active_window=_desktop()._active_window_info(),
        windows=_desktop()._enum_windows(limit=DESKTOP_DEFAULT_WINDOW_LIMIT),
    )
    summary = _desktop()._trim_text(open_payload.get("message", ""), limit=220) or _open_target_summary(target_info, strategy_family, verification)
    error = str(open_payload.get("error", "")).strip()
    if not open_payload.get("ok", False) and not error:
        error = str(open_payload.get("message", "")).strip() or summary

    result = _desktop()._desktop_result(
        ok=bool(open_payload.get("ok", False)),
        action="desktop_open_target",
        summary=summary,
        desktop_state=observation_after,
        error=error,
        approval_status="approved",
        workflow_resumed=_desktop()._coerce_bool(args.get("resume_from_checkpoint", False), False),
        desktop_evidence_ref=evidence_ref,
        target_window=target_window,
        recovery=recovery,
        recovery_attempts=recovery_attempts,
        window_readiness=window_readiness,
        visual_stability=visual_stability,
        process_context=process_context,
        scene=scene,
    )
    result["open_target"] = target_info
    result["open_strategy"] = {
        **strategy,
        "strategy_family": strategy_family,
        "existing_window_match": existing_window_match,
        "existing_window": existing_window,
        "requested_method": requested_method or request_preferences.get("preferred_method", ""),
    }
    result["open_verification"] = verification
    result["open_result"] = {
        "backend": _desktop()._trim_text(open_payload.get("backend", ""), limit=40),
        "reason": _desktop()._trim_text(open_payload.get("reason", ""), limit=80),
        "message": _desktop()._trim_text(open_payload.get("message", ""), limit=220),
        "data": payload,
    }
    return result
