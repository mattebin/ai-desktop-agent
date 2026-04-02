from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Tuple

from core.backend_schemas import (
    normalize_desktop_coordinate_mapping,
    normalize_desktop_evidence_assessment,
    normalize_desktop_evidence_summary,
    normalize_desktop_pointer_action,
    normalize_desktop_process_context,
    normalize_desktop_recovery_outcome,
    normalize_desktop_scene,
    normalize_desktop_target_proposal,
    normalize_desktop_target_proposal_context,
    normalize_desktop_visual_stability,
    normalize_desktop_window_readiness,
)
from core.desktop_mapping import build_desktop_coordinate_mapping
from core.desktop_matching import describe_title_match


TargetProposer = Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]
TARGET_PROPOSER_KINDS = ("generic", "workflow", "app")
_TARGET_PROPOSERS: Dict[str, List[Tuple[str, TargetProposer]]] = {kind: [] for kind in TARGET_PROPOSER_KINDS}


def _trim_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _coerce_int(value: Any, default: int, *, minimum: int = 0, maximum: int = 100_000) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _coerce_float(value: Any, default: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return round(parsed, 3)


def _confidence_label(score: int) -> str:
    if score >= 82:
        return "high"
    if score >= 58:
        return "medium"
    return "low"


def _rect(value: Any) -> Dict[str, int]:
    rect = value if isinstance(value, dict) else {}
    return {
        "x": _coerce_int(rect.get("x", 0), 0, minimum=-100_000, maximum=100_000),
        "y": _coerce_int(rect.get("y", 0), 0, minimum=-100_000, maximum=100_000),
        "width": _coerce_int(rect.get("width", 0), 0, minimum=0, maximum=100_000),
        "height": _coerce_int(rect.get("height", 0), 0, minimum=0, maximum=100_000),
    }


def _window_center(summary: Dict[str, Any]) -> Dict[str, int]:
    rect = _rect(summary.get("active_window_rect", {}))
    width = int(rect.get("width", 0) or 0)
    height = int(rect.get("height", 0) or 0)
    if width <= 0 or height <= 0:
        return {}
    return {
        "x": int(rect.get("x", 0) or 0) + width // 2,
        "y": int(rect.get("y", 0) or 0) + height // 2,
    }


def _window_region(summary: Dict[str, Any]) -> Dict[str, int]:
    rect = _rect(summary.get("active_window_rect", {}))
    if int(rect.get("width", 0) or 0) <= 0 or int(rect.get("height", 0) or 0) <= 0:
        return {}
    return rect


def _summary_display(summary: Dict[str, Any]) -> Dict[str, Any]:
    primary = summary.get("primary_monitor", {}) if isinstance(summary.get("primary_monitor", {}), dict) else {}
    monitors = [primary] if primary else []
    return {
        "primary_monitor": primary,
        "monitors": monitors,
    }


def _summary_observation(summary: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "screenshot_scope": summary.get("screenshot_scope", ""),
        "screenshot_bounds": summary.get("capture_bounds", {}),
        "capture_monitor_id": summary.get("capture_monitor_id", ""),
        "capture_monitor_index": summary.get("capture_monitor_index", 0),
        "primary_monitor_id": summary.get("primary_monitor", {}).get("monitor_id", "") if isinstance(summary.get("primary_monitor", {}), dict) else "",
    }


def _summary_target_window(summary: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "window_id": "",
        "title": _trim_text(summary.get("active_window_title", ""), limit=180),
        "rect": _rect(summary.get("active_window_rect", {})),
    }


def _mapping_for_absolute_point(summary: Dict[str, Any], point: Dict[str, Any], *, reason: str) -> Dict[str, Any]:
    if not isinstance(point, dict) or "x" not in point or "y" not in point:
        return {}
    mapping = build_desktop_coordinate_mapping(
        coordinate_mode="absolute",
        requested_point={"x": point.get("x", 0), "y": point.get("y", 0)},
        display=_summary_display(summary),
        target_window=_summary_target_window(summary),
        observation=_summary_observation(summary),
    )
    mapping["reason"] = _trim_text(reason or mapping.get("reason", ""), limit=80)
    return normalize_desktop_coordinate_mapping(mapping)


def _title_similarity(expected_title: str, observed_title: str) -> int:
    match = describe_title_match(expected_title, observed_title, exact=False)
    return int(match.get("score", 0) or 0)


def _goal_actions(prompt_text: str, pending_tool: str) -> List[str]:
    tool = _trim_text(pending_tool, limit=80)
    if tool:
        return [tool]
    prompt = str(prompt_text or "").strip().lower()
    actions: List[str] = []
    if any(token in prompt for token in ("focus", "bring to front", "activate")):
        actions.append("desktop_focus_window")
    if any(token in prompt for token in ("click", "select", "press the button", "open the item")):
        actions.append("desktop_click_mouse")
    if any(token in prompt for token in ("type", "enter text", "fill", "subject", "body", "write")):
        actions.append("desktop_type_text")
    if any(token in prompt for token in ("scroll", "down the list", "move down", "move up")):
        actions.append("desktop_scroll")
    if any(token in prompt for token in ("hover", "inspect this area")):
        actions.append("desktop_hover_point")
    if not actions:
        actions.extend(["desktop_inspect_window_state", "desktop_focus_window"])
    unique: List[str] = []
    for item in actions:
        text = _trim_text(item, limit=80)
        if text and text not in unique:
            unique.append(text)
        if len(unique) >= 4:
            break
    return unique


def _merge_proposals(existing: List[Dict[str, Any]], additions: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged = [normalize_desktop_target_proposal(item) for item in existing if isinstance(item, dict)]
    by_id = {item.get("target_id", ""): item for item in merged if item.get("target_id")}
    for item in additions:
        if not isinstance(item, dict):
            continue
        normalized = normalize_desktop_target_proposal(item)
        target_id = str(normalized.get("target_id", "")).strip()
        if not target_id:
            continue
        current = by_id.get(target_id)
        if current is None or int(normalized.get("confidence_score", 0) or 0) > int(current.get("confidence_score", 0) or 0):
            by_id[target_id] = normalized
    proposals = list(by_id.values())
    proposals.sort(
        key=lambda item: (
            int(item.get("confidence_score", 0) or 0),
            1 if item.get("approval_required", False) else 0,
            str(item.get("target_kind", "")),
            str(item.get("target_id", "")),
        ),
        reverse=True,
    )
    return proposals[:4]


def _proposal(
    *,
    target_id: str,
    target_kind: str,
    summary: str,
    source_summary: Dict[str, Any],
    confidence_score: int,
    reason: str,
    suggested_next_actions: Iterable[str],
    approval_required: bool,
    point: Dict[str, Any] | None = None,
    region: Dict[str, Any] | None = None,
    coordinate_mapping: Dict[str, Any] | None = None,
    window_title: str = "",
    window_process: str = "",
) -> Dict[str, Any]:
    summary_value = normalize_desktop_evidence_summary(source_summary)
    point_value = point if isinstance(point, dict) else {}
    region_value = region if isinstance(region, dict) else {}
    return normalize_desktop_target_proposal(
        {
            "target_id": target_id,
            "target_kind": target_kind,
            "source_evidence_id": summary_value.get("evidence_id", ""),
            "source_evidence_summary": summary_value.get("summary", ""),
            "source_selection_reason": summary_value.get("selection_reason", ""),
            "window_title": window_title or summary_value.get("active_window_title", ""),
            "window_process": window_process or summary_value.get("active_window_process", ""),
            "point": point_value,
            "region": region_value,
            "coordinate_mapping": coordinate_mapping if isinstance(coordinate_mapping, dict) else {},
            "confidence": _confidence_label(confidence_score),
            "confidence_score": confidence_score,
            "reason": reason,
            "summary": summary,
            "suggested_next_actions": list(suggested_next_actions),
            "approval_required": bool(approval_required),
        }
    )


def register_target_proposer(kind: str, name: str, handler: TargetProposer) -> None:
    normalized_kind = _trim_text(kind, limit=40).lower()
    normalized_name = _trim_text(name, limit=60)
    if normalized_kind not in TARGET_PROPOSER_KINDS or not normalized_name or not callable(handler):
        return
    bucket = _TARGET_PROPOSERS[normalized_kind]
    if any(existing_name == normalized_name for existing_name, _ in bucket):
        return
    bucket.append((normalized_name, handler))


def list_target_proposers(kind: str | None = None) -> Dict[str, List[str]]:
    if kind:
        normalized_kind = _trim_text(kind, limit=40).lower()
        return {normalized_kind: [name for name, _ in _TARGET_PROPOSERS.get(normalized_kind, [])]}
    return {bucket: [name for name, _ in handlers] for bucket, handlers in _TARGET_PROPOSERS.items()}


def _primary_context(
    *,
    selected_summary: Dict[str, Any],
    checkpoint_summary: Dict[str, Any],
    selected_assessment: Dict[str, Any],
    checkpoint_assessment: Dict[str, Any],
    selected_scene: Dict[str, Any],
    checkpoint_scene: Dict[str, Any],
    purpose_text: str,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    primary_summary = checkpoint_summary if purpose_text == "desktop_approval" and checkpoint_summary.get("evidence_id") else selected_summary
    if not primary_summary.get("evidence_id"):
        primary_summary = selected_summary if selected_summary.get("evidence_id") else checkpoint_summary
    primary_assessment = checkpoint_assessment if primary_summary.get("evidence_id") == checkpoint_summary.get("evidence_id") and checkpoint_summary.get("evidence_id") else selected_assessment
    primary_scene = checkpoint_scene if primary_summary.get("evidence_id") == checkpoint_summary.get("evidence_id") and checkpoint_summary.get("evidence_id") else selected_scene
    return primary_summary, primary_assessment, primary_scene


def _generic_blocked_or_recovery_proposer(result: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    primary = context.get("primary_summary", {})
    assessment = context.get("primary_assessment", {})
    scene = context.get("primary_scene", {})
    recovery = context.get("recovery", {})
    readiness = context.get("readiness", {})
    stability = context.get("visual_stability", {})
    process_context = context.get("process_context", {})
    target_window_title = _trim_text(
        primary.get("target_window_title", "") or context.get("checkpoint_target", "") or context.get("remembered_target_title", ""),
        limit=180,
    )
    active_window_title = _trim_text(primary.get("active_window_title", ""), limit=180)
    process_name = _trim_text(process_context.get("process_name", "") or primary.get("active_window_process", ""), limit=120)
    if not primary.get("evidence_id"):
        return {
            "state": "no_safe_target",
            "reason": "proposal_no_safe_target",
            "summary": "No current desktop evidence is available to propose a safe next target.",
            "confidence_score": 24,
            "proposals": [],
        }

    recovery_reason = _trim_text(recovery.get("reason", ""), limit=80).lower()
    recovery_state = _trim_text(recovery.get("state", ""), limit=40).lower()
    readiness_state = _trim_text(readiness.get("state", scene.get("readiness_state", "")), limit=40).lower()
    workflow_state = _trim_text(scene.get("workflow_state", ""), limit=40).lower()

    if recovery_reason in {"tray_or_background_state", "target_withdrawn"}:
        process_label = process_name or "The target process"
        return {
            "state": "no_safe_target",
            "reason": "proposal_no_safe_target",
            "summary": _trim_text(
                recovery.get("summary", "")
                or f"{process_label} still looks backgrounded, tray-like, or withdrawn, so there is no safe visible desktop target to act on yet.",
                limit=220,
            ),
            "confidence_score": 86,
            "proposals": [],
        }

    if recovery_state in {"needs_recovery", "missing"} or recovery_reason in {"foreground_not_confirmed", "target_minimized", "target_hidden", "target_mismatch"}:
        target_label = target_window_title or active_window_title or process_name or "the intended window"
        score = 90 if recovery_reason in {"target_minimized", "target_hidden"} else 84
        suggested = ["desktop_recover_window", "desktop_focus_window"]
        proposal = _proposal(
            target_id=f"recovery:{target_label.lower()[:80]}",
            target_kind="recovery_candidate",
            summary=_trim_text(
                recovery.get("summary", "") or f"Recover or refocus '{target_label}' before attempting another bounded desktop action.",
                limit=220,
            ),
            source_summary=primary,
            confidence_score=score,
            reason="proposal_recovery_first",
            suggested_next_actions=suggested,
            approval_required=False,
            window_title=target_label,
            window_process=process_name,
        )
        return {
            "state": "recovery_first",
            "reason": "proposal_recovery_first",
            "summary": proposal.get("summary", ""),
            "confidence_score": score,
            "proposals": [proposal],
        }

    if readiness_state in {"loading", "not_ready", "unstable"} or workflow_state in {"loading", "settling", "blocked"} or stability.get("state") == "unstable":
        details = (
            readiness.get("summary", "")
            or stability.get("summary", "")
            or scene.get("summary", "")
            or assessment.get("summary", "")
        )
        return {
            "state": "blocked",
            "reason": "proposal_blocked",
            "summary": _trim_text(details or "The current desktop scene is not stable enough for a safe bounded target proposal yet.", limit=220),
            "confidence_score": 80,
            "proposals": [],
        }

    return {}


def _workflow_approval_proposer(result: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    if not bool(context.get("checkpoint_pending", False)):
        return {}
    checkpoint_summary = context.get("checkpoint_summary", {})
    primary = context.get("primary_summary", {})
    checkpoint_scene = context.get("checkpoint_scene", {})
    latest_mouse_action = context.get("latest_mouse_action", {})
    pending_tool = _trim_text(context.get("pending_tool", ""), limit=80)
    source = checkpoint_summary if checkpoint_summary.get("evidence_id") else primary
    target_label = _trim_text(
        context.get("checkpoint_target", "") or source.get("target_window_title", "") or source.get("active_window_title", ""),
        limit=180,
    ) or "the bounded desktop target"
    proposals: List[Dict[str, Any]] = []
    summary = f"Approval is still required before acting on '{target_label}'."
    score = 82

    if pending_tool in {"desktop_click_mouse", "desktop_click_point", "desktop_move_mouse", "desktop_hover_point", "desktop_scroll"}:
        mouse_action = normalize_desktop_pointer_action(latest_mouse_action if isinstance(latest_mouse_action, dict) else {})
        point = mouse_action.get("point", {}) if isinstance(mouse_action.get("point", {}), dict) else {}
        if int(point.get("x", 0) or 0) or int(point.get("y", 0) or 0):
            proposals.append(
                _proposal(
                    target_id=f"approval-point:{target_label.lower()[:72]}",
                    target_kind="point",
                    summary=_trim_text(
                        mouse_action.get("summary", "") or f"Use the reviewed bounded point for '{target_label}' once approval is granted.",
                        limit=220,
                    ),
                    source_summary=source,
                    confidence_score=86,
                    reason="proposal_approval_context",
                    suggested_next_actions=[pending_tool],
                    approval_required=True,
                    point=point,
                    coordinate_mapping=mouse_action.get("coordinate_mapping", {}),
                    window_title=source.get("active_window_title", ""),
                    window_process=source.get("active_window_process", ""),
                )
            )
            score = 86

    if not proposals:
        region = _window_region(source)
        proposals.append(
            _proposal(
                target_id=f"approval-window:{target_label.lower()[:72]}",
                target_kind="ui_area",
                summary=_trim_text(
                    checkpoint_scene.get("summary", "") or f"Review the linked desktop evidence for '{target_label}' before approving the next bounded action.",
                    limit=220,
                ),
                source_summary=source,
                confidence_score=78,
                reason="proposal_approval_context",
                suggested_next_actions=[pending_tool] if pending_tool else ["desktop_focus_window"],
                approval_required=True,
                region=region,
                coordinate_mapping=_mapping_for_absolute_point(source, _window_center(source), reason="approval_window_surface") if region else {},
                window_title=source.get("active_window_title", ""),
                window_process=source.get("active_window_process", ""),
            )
        )

    return {
        "state": "approval_context",
        "reason": "proposal_approval_context",
        "summary": summary,
        "confidence_score": score,
        "proposals": proposals,
    }


def _generic_ready_surface_proposer(result: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    if str(result.get("state", "")).strip() in {"blocked", "recovery_first"}:
        return {}
    primary = context.get("primary_summary", {})
    scene = context.get("primary_scene", {})
    assessment = context.get("primary_assessment", {})
    recovery = context.get("recovery", {})
    if not primary.get("evidence_id") or not primary.get("active_window_title"):
        return {}
    if (
        bool(scene.get("background_like", False))
        or str(scene.get("workflow_state", "")).strip().lower() in {"recovering", "blocked", "loading", "settling"}
        or str(recovery.get("reason", "")).strip().lower() in {"tray_or_background_state", "target_withdrawn", "foreground_not_confirmed", "target_hidden", "target_minimized", "target_mismatch"}
    ):
        return {}
    rect = _window_region(primary)
    center = _window_center(primary)
    if not rect or not center:
        return {}

    target_title = _trim_text(primary.get("active_window_title", ""), limit=180)
    process_name = _trim_text(primary.get("active_window_process", ""), limit=120)
    suggested_actions = _goal_actions(context.get("prompt_text", ""), context.get("pending_tool", ""))
    confidence = 88 if assessment.get("sufficient", False) and not scene.get("scene_changed", False) else 72
    focus_summary = f"'{target_title}' is visible on the primary desktop path and looks ready for a bounded next step."
    interaction_summary = (
        f"The visible interaction surface for '{target_title}' looks stable enough for a bounded reviewed action."
        if assessment.get("has_screenshot", False)
        else f"'{target_title}' is the best current focus target, but exact click coordinates still need stronger screenshot evidence."
    )
    proposals = [
        _proposal(
            target_id=f"focus:{target_title.lower()[:80]}",
            target_kind="focus_candidate",
            summary=_trim_text(focus_summary, limit=220),
            source_summary=primary,
            confidence_score=confidence,
            reason="proposal_ready",
            suggested_next_actions=["desktop_focus_window", *suggested_actions[:2]],
            approval_required=False,
            window_title=target_title,
            window_process=process_name,
        ),
        _proposal(
            target_id=f"surface:{target_title.lower()[:80]}",
            target_kind="region",
            summary=_trim_text(interaction_summary, limit=220),
            source_summary=primary,
            confidence_score=max(56, confidence - 10),
            reason="proposal_ready",
            suggested_next_actions=suggested_actions,
            approval_required=any(action in {"desktop_click_mouse", "desktop_click_point", "desktop_move_mouse", "desktop_hover_point", "desktop_scroll", "desktop_type_text"} for action in suggested_actions),
            point=center,
            region=rect,
            coordinate_mapping=_mapping_for_absolute_point(primary, center, reason="window_surface_center"),
            window_title=target_title,
            window_process=process_name,
        ),
    ]
    return {
        "state": "ready",
        "reason": "proposal_ready",
        "summary": _trim_text(scene.get("summary", "") or focus_summary, limit=220),
        "confidence_score": confidence,
        "proposals": proposals,
    }


def _app_text_entry_proposer(result: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    if str(result.get("state", "")).strip() in {"blocked", "recovery_first"}:
        return {}
    primary = context.get("primary_summary", {})
    scene = context.get("primary_scene", {})
    recovery = context.get("recovery", {})
    prompt_text = str(context.get("prompt_text", "") or "").strip().lower()
    app_class = _trim_text(scene.get("app_class", ""), limit=40).lower()
    if app_class not in {"mail", "editor"}:
        return {}
    if bool(scene.get("background_like", False)) or str(recovery.get("reason", "")).strip().lower() in {"tray_or_background_state", "target_withdrawn"}:
        return {}
    if not any(token in prompt_text for token in ("type", "enter", "write", "subject", "body", "message", "text")):
        return {}
    rect = _window_region(primary)
    if not rect:
        return {}
    center = _window_center(primary)
    title = _trim_text(primary.get("active_window_title", ""), limit=180) or "the active window"
    summary = f"'{title}' looks like a {app_class} workflow where bounded text entry is a plausible next step after focus."
    proposal = _proposal(
        target_id=f"text-area:{title.lower()[:72]}",
        target_kind="ui_area",
        summary=summary,
        source_summary=primary,
        confidence_score=74,
        reason="proposal_ready",
        suggested_next_actions=["desktop_focus_window", "desktop_type_text"],
        approval_required=True,
        point=center,
        region=rect,
        coordinate_mapping=_mapping_for_absolute_point(primary, center, reason="text_entry_surface"),
        window_title=title,
        window_process=_trim_text(primary.get("active_window_process", ""), limit=120),
    )
    return {
        "proposals": [proposal],
    }


for _kind, _name, _handler in (
    ("generic", "blocked_or_recovery", _generic_blocked_or_recovery_proposer),
    ("workflow", "approval_context", _workflow_approval_proposer),
    ("generic", "ready_surface", _generic_ready_surface_proposer),
    ("app", "text_entry_affinity", _app_text_entry_proposer),
):
    register_target_proposer(_kind, _name, _handler)


def propose_desktop_targets(
    *,
    selected_summary: Dict[str, Any] | None = None,
    checkpoint_summary: Dict[str, Any] | None = None,
    recent_summaries: Iterable[Dict[str, Any]] | None = None,
    purpose: str = "desktop_investigation",
    prompt_text: str = "",
    assessment: Dict[str, Any] | None = None,
    checkpoint_assessment: Dict[str, Any] | None = None,
    selected_scene: Dict[str, Any] | None = None,
    checkpoint_scene: Dict[str, Any] | None = None,
    recovery: Dict[str, Any] | None = None,
    readiness: Dict[str, Any] | None = None,
    visual_stability: Dict[str, Any] | None = None,
    process_context: Dict[str, Any] | None = None,
    latest_mouse_action: Dict[str, Any] | None = None,
    pending_tool: str = "",
    checkpoint_pending: bool = False,
    checkpoint_target: str = "",
    remembered_target_title: str = "",
) -> Dict[str, Any]:
    selected = normalize_desktop_evidence_summary(selected_summary if isinstance(selected_summary, dict) else {})
    checkpoint = normalize_desktop_evidence_summary(checkpoint_summary if isinstance(checkpoint_summary, dict) else {})
    selected_assessment = normalize_desktop_evidence_assessment(assessment if isinstance(assessment, dict) else {})
    approval_assessment = normalize_desktop_evidence_assessment(checkpoint_assessment if isinstance(checkpoint_assessment, dict) else {})
    selected_scene = normalize_desktop_scene(selected_scene if isinstance(selected_scene, dict) else {})
    checkpoint_scene = normalize_desktop_scene(checkpoint_scene if isinstance(checkpoint_scene, dict) else {})
    normalized_recovery = normalize_desktop_recovery_outcome(recovery if isinstance(recovery, dict) else {})
    normalized_readiness = normalize_desktop_window_readiness(readiness if isinstance(readiness, dict) else {})
    normalized_stability = normalize_desktop_visual_stability(visual_stability if isinstance(visual_stability, dict) else {})
    normalized_process = normalize_desktop_process_context(process_context if isinstance(process_context, dict) else {})
    normalized_mouse = normalize_desktop_pointer_action(latest_mouse_action if isinstance(latest_mouse_action, dict) else {})
    purpose_text = _trim_text(purpose or "desktop_investigation", limit=60).lower() or "desktop_investigation"
    primary, primary_assessment, primary_scene = _primary_context(
        selected_summary=selected,
        checkpoint_summary=checkpoint,
        selected_assessment=selected_assessment,
        checkpoint_assessment=approval_assessment,
        selected_scene=selected_scene,
        checkpoint_scene=checkpoint_scene,
        purpose_text=purpose_text,
    )
    active_title = _trim_text(primary.get("active_window_title", ""), limit=180)
    target_title = _trim_text(primary.get("target_window_title", "") or checkpoint_target or remembered_target_title, limit=180)
    target_match_score = _title_similarity(target_title, active_title) if target_title and active_title else 0
    base_context: Dict[str, Any] = {
        "purpose_text": purpose_text,
        "prompt_text": _trim_text(prompt_text, limit=600),
        "selected_summary": selected,
        "checkpoint_summary": checkpoint,
        "primary_summary": primary,
        "selected_assessment": selected_assessment,
        "checkpoint_assessment": approval_assessment,
        "primary_assessment": primary_assessment,
        "selected_scene": selected_scene,
        "checkpoint_scene": checkpoint_scene,
        "primary_scene": primary_scene,
        "recovery": normalized_recovery,
        "readiness": normalized_readiness,
        "visual_stability": normalized_stability,
        "process_context": normalized_process,
        "latest_mouse_action": normalized_mouse,
        "pending_tool": _trim_text(pending_tool, limit=80),
        "checkpoint_pending": bool(checkpoint_pending),
        "checkpoint_target": _trim_text(checkpoint_target, limit=180),
        "remembered_target_title": _trim_text(remembered_target_title, limit=180),
        "target_match_score": target_match_score,
        "recent_summaries": [normalize_desktop_evidence_summary(item) for item in list(recent_summaries or []) if isinstance(item, dict)][:4],
    }
    result: Dict[str, Any] = {
        "purpose": purpose_text,
        "state": "no_safe_target",
        "reason": "proposal_no_safe_target",
        "summary": "No safe bounded desktop target could be proposed yet.",
        "confidence": "low",
        "confidence_score": 20,
        "scene_class": _trim_text(primary_scene.get("scene_class", ""), limit=40),
        "workflow_state": _trim_text(primary_scene.get("workflow_state", ""), limit=40),
        "readiness_state": _trim_text(primary_scene.get("readiness_state", ""), limit=40),
        "active_window_title": active_title,
        "target_window_title": target_title,
        "primary_evidence_id": _trim_text(primary.get("evidence_id", ""), limit=80),
        "comparison_evidence_id": _trim_text(primary_scene.get("comparison_evidence_id", ""), limit=80),
        "pending_tool": _trim_text(pending_tool, limit=80),
        "checkpoint_pending": bool(checkpoint_pending),
        "target_match_score": target_match_score,
        "proposer_names": [],
        "proposals": [],
    }

    for kind in TARGET_PROPOSER_KINDS:
        for name, handler in _TARGET_PROPOSERS.get(kind, []):
            fragment = handler(result, base_context)
            if not isinstance(fragment, dict):
                continue
            if name not in result["proposer_names"]:
                result["proposer_names"].append(name)
            result["proposals"] = _merge_proposals(result.get("proposals", []), fragment.get("proposals", []))
            fragment_score = _coerce_int(fragment.get("confidence_score", result.get("confidence_score", 0)), int(result.get("confidence_score", 0) or 0), minimum=0, maximum=100)
            if fragment.get("state") and (
                not result.get("proposals")
                or fragment_score >= int(result.get("confidence_score", 0) or 0)
                or str(fragment.get("state", "")).strip() == "approval_context"
            ):
                result["state"] = _trim_text(fragment.get("state", result.get("state", "")), limit=40)
                result["reason"] = _trim_text(fragment.get("reason", result.get("reason", "")), limit=80)
                result["summary"] = _trim_text(fragment.get("summary", result.get("summary", "")), limit=240)
                result["confidence_score"] = fragment_score

    if result.get("proposals"):
        if str(result.get("state", "")).strip() == "no_safe_target":
            result["state"] = "ready"
            result["reason"] = "proposal_ready"
            result["summary"] = _trim_text(result.get("summary", "") or "Prepared a compact ranked set of bounded desktop targets from the current evidence and scene state.", limit=240)
            result["confidence_score"] = max(int(result.get("confidence_score", 0) or 0), int(result["proposals"][0].get("confidence_score", 0) or 0))
    result["proposal_count"] = len(result.get("proposals", []))
    result["confidence"] = _confidence_label(int(result.get("confidence_score", 0) or 0))
    return normalize_desktop_target_proposal_context(result)
