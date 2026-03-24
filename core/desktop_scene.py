from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Tuple

from core.backend_schemas import (
    normalize_desktop_evidence_assessment,
    normalize_desktop_evidence_summary,
    normalize_desktop_process_context,
    normalize_desktop_recovery_outcome,
    normalize_desktop_scene,
    normalize_desktop_visual_stability,
    normalize_desktop_window_readiness,
)
from core.desktop_matching import describe_title_match


SceneInterpreter = Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]
SCENE_INTERPRETER_KINDS = ("generic", "app", "workflow", "change")
_SCENE_INTERPRETER_KINDS = SCENE_INTERPRETER_KINDS
_SCENE_INTERPRETERS: Dict[str, List[Tuple[str, SceneInterpreter]]] = {kind: [] for kind in _SCENE_INTERPRETER_KINDS}


def _trim_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _rect_ratio(summary: Dict[str, Any]) -> float:
    rect = summary.get("active_window_rect", {}) if isinstance(summary.get("active_window_rect", {}), dict) else {}
    screen = summary.get("screen_size", {}) if isinstance(summary.get("screen_size", {}), dict) else {}
    width = max(0, int(rect.get("width", 0) or 0))
    height = max(0, int(rect.get("height", 0) or 0))
    screen_width = max(1, int(screen.get("width", 0) or 0))
    screen_height = max(1, int(screen.get("height", 0) or 0))
    if not width or not height:
        return 0.0
    return min(width / screen_width, height / screen_height)


def _summary_signature(summary: Dict[str, Any]) -> str:
    return "|".join(
        [
            _trim_text(summary.get("capture_signature", ""), limit=120),
            _trim_text(summary.get("active_window_title", ""), limit=180).lower(),
            _trim_text(summary.get("active_window_process", ""), limit=120).lower(),
            _trim_text(summary.get("screenshot_scope", ""), limit=60).lower(),
            _trim_text(summary.get("reason", ""), limit=40).lower(),
        ]
    )


def _title_tokens(*values: Any) -> str:
    return " ".join(_trim_text(value, limit=180).lower() for value in values if _trim_text(value, limit=180))


def _match_title(query: str, candidate: str) -> int:
    return int(describe_title_match(query, candidate, exact=False).get("score", 0) or 0)


def _select_previous_summary(context: Dict[str, Any]) -> Dict[str, Any]:
    primary = context.get("primary_summary", {})
    primary_id = str(primary.get("evidence_id", "")).strip()
    primary_title = str(primary.get("active_window_title", "")).strip()
    candidates: List[Tuple[int, int, Dict[str, Any]]] = []
    for item in context.get("recent_summaries", []):
        if not isinstance(item, dict):
            continue
        evidence_id = str(item.get("evidence_id", "")).strip()
        if not evidence_id or evidence_id == primary_id:
            continue
        score = 0
        if primary_title:
            score += _match_title(primary_title, item.get("active_window_title", ""))
        if str(item.get("has_screenshot", False)).lower() == "true" or item.get("has_screenshot", False):
            score += 6
        recency = int(item.get("recency_seconds", 0) or 0)
        candidates.append((score, -recency, item))
    if not candidates:
        return {}
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return dict(candidates[0][2])


def register_scene_interpreter(kind: str, name: str, handler: SceneInterpreter) -> None:
    normalized_kind = str(kind or "").strip().lower()
    normalized_name = _trim_text(name, limit=60)
    if normalized_kind not in _SCENE_INTERPRETER_KINDS or not normalized_name or not callable(handler):
        return
    bucket = _SCENE_INTERPRETERS[normalized_kind]
    if any(existing_name == normalized_name for existing_name, _ in bucket):
        return
    bucket.append((normalized_name, handler))


def list_scene_interpreters(kind: str | None = None) -> Dict[str, List[str]]:
    if kind:
        normalized_kind = str(kind or "").strip().lower()
        return {normalized_kind: [name for name, _ in _SCENE_INTERPRETERS.get(normalized_kind, [])]}
    return {bucket: [name for name, _ in handlers] for bucket, handlers in _SCENE_INTERPRETERS.items()}


def _merge_scene_fragment(scene: Dict[str, Any], fragment: Dict[str, Any], *, name: str) -> Dict[str, Any]:
    if not isinstance(fragment, dict):
        return scene
    if name not in scene["interpreters"]:
        scene["interpreters"].append(name)
    for signal in list(fragment.get("signals", []) or []):
        text = _trim_text(signal, limit=120)
        if text and text not in scene["signals"]:
            scene["signals"].append(text)
    for key in (
        "scene_class",
        "app_class",
        "workflow_state",
        "readiness_state",
        "presentation",
        "reason",
        "summary",
        "history_summary",
        "transition_summary",
        "change_reason",
        "pending_tool",
    ):
        value = fragment.get(key)
        if _trim_text(value, limit=240):
            scene[key] = value
    for key in ("scene_changed", "direct_image_helpful", "prefer_before_after", "loading", "modal_like", "prompt_like", "fullscreen_like", "background_like", "unstable", "checkpoint_pending"):
        if key in fragment:
            scene[key] = bool(fragment.get(key))
    score = int(fragment.get("confidence_score", 0) or 0)
    if score >= int(scene.get("confidence_score", 0) or 0):
        scene["confidence_score"] = score
        scene["confidence"] = fragment.get("confidence", scene.get("confidence", "low"))
    return scene


def _app_class_from_process(process_name: str, title: str) -> str:
    tokens = _title_tokens(process_name, title)
    if any(token in tokens for token in {"outlook", "mail", "thunderbird"}):
        return "mail"
    if any(token in tokens for token in {"chrome", "firefox", "edge", "browser"}):
        return "browser"
    if any(token in tokens for token in {"code", "notepad", "editor", "word"}):
        return "editor"
    if any(token in tokens for token in {"powershell", "terminal", "cmd", "console"}):
        return "terminal"
    if any(token in tokens for token in {"settings", "control panel", "task manager", "explorer"}):
        return "system"
    return "unknown"


def _generic_scene_interpreter(scene: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    primary = context.get("primary_summary", {})
    assessment = context.get("primary_assessment", {})
    recovery = context.get("recovery", {})
    readiness = context.get("readiness", {})
    stability = context.get("visual_stability", {})
    process_context = context.get("process_context", {})
    has_recovery = bool(context.get("has_recovery", False))
    has_readiness = bool(context.get("has_readiness", False))
    has_visual_stability = bool(context.get("has_visual_stability", False))

    title = _trim_text(primary.get("active_window_title", ""), limit=180)
    process_name = _trim_text(primary.get("active_window_process", ""), limit=120)
    has_summary = bool(primary.get("evidence_id") or title or process_name)
    rect_ratio = _rect_ratio(primary)

    scene_class = "missing"
    presentation = "unknown"
    readiness_state = "missing"
    workflow_state = "inspecting"
    reason = "scene_interpreted"
    summary = "No relevant desktop scene was available yet."
    confidence_score = 30
    signals: List[str] = []

    if not has_summary:
        return {
            "scene_class": scene_class,
            "presentation": presentation,
            "workflow_state": workflow_state,
            "readiness_state": readiness_state,
            "reason": "scene_ambiguous",
            "summary": summary,
            "confidence": "low",
            "confidence_score": confidence_score,
            "signals": ["missing_evidence"],
        }

    scene_class = "app_window"
    presentation = "windowed"
    readiness_state = "ready"
    workflow_state = "reviewable" if assessment.get("state") == "partial" else "ready"
    reason = "ready_scene"
    summary = f"Observed '{title or process_name or 'the active window'}' in a ready-looking desktop scene."
    confidence_score = 62 if primary.get("has_screenshot", False) else 48
    signals.extend(filter(None, [title, process_name]))

    if process_context.get("background_candidate", False) or (has_recovery and recovery.get("reason") in {"tray_or_background_state", "target_withdrawn"}):
        scene_class = "background"
        presentation = "background_like"
        readiness_state = "background"
        workflow_state = "recovering"
        reason = "background_like"
        summary = "The target appears backgrounded, tray-like, or not visibly surfaced."
        confidence_score = 74
    elif has_recovery and recovery.get("state") in {"needs_recovery", "missing"}:
        workflow_state = "recovering"
        readiness_state = "background" if recovery.get("reason") in {"target_hidden", "foreground_not_confirmed"} else "missing"
        reason = "blocked_scene" if recovery.get("reason") == "target_mismatch" else str(recovery.get("reason", "") or "scene_interpreted")
        summary = _trim_text(recovery.get("summary", "") or summary, limit=220)
        confidence_score = max(confidence_score, 70)
    elif (has_readiness and readiness.get("state") == "loading") or (has_recovery and recovery.get("reason") == "target_loading"):
        readiness_state = "loading"
        workflow_state = "loading"
        reason = "loading_scene"
        summary = _trim_text(readiness.get("summary", "") or recovery.get("summary", "") or f"'{title or process_name or 'The window'}' still looks like it is loading.", limit=220)
        confidence_score = max(confidence_score, 75)
    elif has_readiness and readiness.get("state") == "not_ready":
        readiness_state = "not_ready"
        workflow_state = "attention_needed"
        reason = "blocked_scene"
        summary = _trim_text(readiness.get("summary", "") or f"'{title or process_name or 'The window'}' is visible but not ready for interaction.", limit=220)
        confidence_score = max(confidence_score, 72)
    elif has_visual_stability and stability.get("state") == "unstable":
        readiness_state = "unstable"
        workflow_state = "settling"
        reason = "scene_changed"
        summary = _trim_text(stability.get("summary", "") or f"'{title or process_name or 'The window'}' still appears visually unstable.", limit=220)
        confidence_score = max(confidence_score, 76)

    if rect_ratio >= 0.94 or primary.get("active_window_maximized", False):
        scene_class = "fullscreen"
        presentation = "fullscreen_like"
        reason = "fullscreen_like" if reason == "ready_scene" else reason
        confidence_score = max(confidence_score, 66)
        signals.append("fullscreen_ratio")

    return {
        "scene_class": scene_class,
        "presentation": presentation,
        "workflow_state": workflow_state,
        "readiness_state": readiness_state,
        "reason": reason,
        "summary": summary,
        "confidence": "high" if confidence_score >= 78 else "medium" if confidence_score >= 56 else "low",
        "confidence_score": confidence_score,
        "loading": readiness_state == "loading",
        "background_like": presentation == "background_like",
        "fullscreen_like": presentation == "fullscreen_like",
        "unstable": readiness_state == "unstable",
        "signals": signals,
    }


def _dialog_prompt_interpreter(scene: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    primary = context.get("primary_summary", {})
    title = _title_tokens(primary.get("active_window_title", ""), primary.get("target_window_title", ""), primary.get("summary", ""))
    prompt_terms = ("confirm", "confirmation", "permission", "prompt", "warning", "send?", "send now", "save changes", "allow")
    dialog_terms = ("dialog", "message", "question", "open", "save as", "alert")
    is_prompt = any(term in title for term in prompt_terms)
    is_dialog = is_prompt or any(term in title for term in dialog_terms)
    if not is_dialog:
        return {}
    return {
        "scene_class": "prompt" if is_prompt else "dialog",
        "presentation": "prompt_like" if is_prompt else "dialog_like",
        "workflow_state": "blocked",
        "readiness_state": "ready",
        "reason": "prompt_like" if is_prompt else "dialog_like",
        "summary": "The current desktop scene looks like a prompt/dialog that may block the main workflow until it is handled.",
        "confidence": "high" if is_prompt else "medium",
        "confidence_score": 86 if is_prompt else 74,
        "modal_like": True,
        "prompt_like": is_prompt,
        "direct_image_helpful": True,
        "signals": ["dialog_title_keyword", "prompt_state" if is_prompt else "dialog_state"],
    }


def _app_classifier_interpreter(scene: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    primary = context.get("primary_summary", {})
    process_context = context.get("process_context", {})
    process_name = _trim_text(process_context.get("process_name", "") or primary.get("active_window_process", ""), limit=120)
    title = _trim_text(primary.get("active_window_title", ""), limit=180)
    app_class = _app_class_from_process(process_name, title)
    if app_class == "unknown":
        return {}
    return {
        "app_class": app_class,
        "reason": scene.get("reason", "app_inferred"),
        "confidence_score": max(int(scene.get("confidence_score", 0) or 0), 68),
        "signals": [f"app:{app_class}", process_name or title],
    }


def _workflow_phase_interpreter(scene: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    previous = context.get("previous_summary", {})
    primary = context.get("primary_summary", {})
    if not primary:
        return {}
    if not previous:
        return {
            "history_summary": "No earlier matching desktop evidence was available for workflow comparison.",
            "change_reason": "scene_unchanged",
            "scene_changed": False,
            "signals": ["no_prior_scene"],
        }

    previous_state = str(context.get("previous_readiness_state", "") or "").strip().lower()
    current_state = str(scene.get("readiness_state", "") or "").strip().lower()
    previous_recovery_reason = str(context.get("previous_recovery_reason", "") or "").strip().lower()
    current_reason = str(scene.get("reason", "") or "").strip().lower()
    changed = False
    change_reason = "scene_unchanged"
    transition_summary = ""
    history_summary = ""

    previous_signature = _summary_signature(previous)
    current_signature = _summary_signature(primary)
    if previous_signature and current_signature and previous_signature != current_signature:
        changed = True
        change_reason = "scene_changed"
    if previous_state in {"loading", "not_ready"} and current_state == "ready":
        changed = True
        change_reason = "workflow_transition"
        transition_summary = "Recent desktop evidence suggests the scene moved from loading/not-ready to ready."
    elif previous_recovery_reason in {"foreground_not_confirmed", "target_hidden", "target_minimized"} and current_reason in {"ready_scene", "fullscreen_like"}:
        changed = True
        change_reason = "workflow_transition"
        transition_summary = "Recent desktop evidence suggests the target window was recovered and is now ready."

    if changed and not transition_summary:
        transition_summary = "Recent desktop evidence suggests the visible desktop scene changed in a meaningful way."
    history_summary = (
        f"Previous scene: '{previous.get('active_window_title', '') or previous.get('summary', '')}'."
        if previous.get("evidence_id")
        else ""
    )
    return {
        "scene_changed": changed,
        "change_reason": change_reason,
        "history_summary": history_summary,
        "transition_summary": transition_summary,
        "prefer_before_after": changed and bool(primary.get("has_screenshot", False) and previous.get("has_screenshot", False)),
        "direct_image_helpful": changed,
        "signals": ["history_corroborated", change_reason],
    }


def _scene_change_interpreter(scene: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    assessment = context.get("primary_assessment", {})
    checkpoint_pending = bool(context.get("checkpoint_pending", False))
    direct_image_helpful = bool(scene.get("direct_image_helpful", False))
    if assessment.get("state") == "partial":
        direct_image_helpful = True
    if checkpoint_pending and assessment.get("has_screenshot", False):
        direct_image_helpful = True
    return {
        "direct_image_helpful": direct_image_helpful,
        "checkpoint_pending": checkpoint_pending,
        "pending_tool": _trim_text(context.get("pending_tool", ""), limit=80),
        "signals": ["checkpoint_pending" if checkpoint_pending else ""],
    }


for _kind, _name, _handler in (
    ("generic", "generic_scene", _generic_scene_interpreter),
    ("generic", "dialog_prompt", _dialog_prompt_interpreter),
    ("app", "app_classifier", _app_classifier_interpreter),
    ("workflow", "workflow_phase", _workflow_phase_interpreter),
    ("change", "scene_change", _scene_change_interpreter),
):
    register_scene_interpreter(_kind, _name, _handler)


def interpret_desktop_scene(
    *,
    selected_summary: Dict[str, Any] | None = None,
    checkpoint_summary: Dict[str, Any] | None = None,
    recent_summaries: Iterable[Dict[str, Any]] | None = None,
    purpose: str = "desktop_investigation",
    prompt_text: str = "",
    assessment: Dict[str, Any] | None = None,
    checkpoint_assessment: Dict[str, Any] | None = None,
    recovery: Dict[str, Any] | None = None,
    readiness: Dict[str, Any] | None = None,
    visual_stability: Dict[str, Any] | None = None,
    process_context: Dict[str, Any] | None = None,
    pending_tool: str = "",
    checkpoint_pending: bool = False,
) -> Dict[str, Any]:
    selected = normalize_desktop_evidence_summary(selected_summary if isinstance(selected_summary, dict) else {})
    checkpoint = normalize_desktop_evidence_summary(checkpoint_summary if isinstance(checkpoint_summary, dict) else {})
    selected_assessment = normalize_desktop_evidence_assessment(assessment if isinstance(assessment, dict) else {})
    approval_assessment = normalize_desktop_evidence_assessment(checkpoint_assessment if isinstance(checkpoint_assessment, dict) else {})
    recents = [normalize_desktop_evidence_summary(item) for item in list(recent_summaries or []) if isinstance(item, dict)]
    normalized_recovery = normalize_desktop_recovery_outcome(recovery if isinstance(recovery, dict) else {})
    normalized_readiness = normalize_desktop_window_readiness(readiness if isinstance(readiness, dict) else {})
    normalized_stability = normalize_desktop_visual_stability(visual_stability if isinstance(visual_stability, dict) else {})
    normalized_process = normalize_desktop_process_context(process_context if isinstance(process_context, dict) else {})

    purpose_text = _trim_text(purpose or "desktop_investigation", limit=60).lower() or "desktop_investigation"
    primary = checkpoint if purpose_text == "desktop_approval" and checkpoint.get("evidence_id") else selected
    if not primary.get("evidence_id"):
        primary = selected if selected.get("evidence_id") else checkpoint
    if not primary.get("evidence_id") and recents:
        primary = recents[0]
    primary_assessment = approval_assessment if primary.get("evidence_id") == checkpoint.get("evidence_id") and checkpoint.get("evidence_id") else selected_assessment
    previous = _select_previous_summary({"primary_summary": primary, "recent_summaries": recents})

    scene: Dict[str, Any] = {
        "scene_class": "unknown",
        "app_class": "unknown",
        "workflow_state": "unknown",
        "readiness_state": "unknown",
        "presentation": "unknown",
        "confidence": "low",
        "confidence_score": 0,
        "reason": "scene_interpreted",
        "summary": "",
        "history_summary": "",
        "transition_summary": "",
        "scene_changed": False,
        "change_reason": "scene_unchanged",
        "direct_image_helpful": False,
        "prefer_before_after": False,
        "loading": False,
        "modal_like": False,
        "prompt_like": False,
        "fullscreen_like": False,
        "background_like": False,
        "unstable": False,
        "primary_evidence_id": _trim_text(primary.get("evidence_id", ""), limit=80),
        "comparison_evidence_id": _trim_text(previous.get("evidence_id", ""), limit=80),
        "active_window_title": _trim_text(primary.get("active_window_title", ""), limit=180),
        "active_window_process": _trim_text(primary.get("active_window_process", ""), limit=120),
        "target_window_title": _trim_text(primary.get("target_window_title", ""), limit=180),
        "pending_tool": _trim_text(pending_tool, limit=80),
        "checkpoint_pending": bool(checkpoint_pending),
        "signals": [],
        "interpreters": [],
    }
    context = {
        "purpose": purpose_text,
        "prompt_text": _trim_text(prompt_text, limit=320),
        "selected_summary": selected,
        "checkpoint_summary": checkpoint,
        "primary_summary": primary,
        "primary_assessment": primary_assessment,
        "selected_assessment": selected_assessment,
        "checkpoint_assessment": approval_assessment,
        "recent_summaries": recents,
        "previous_summary": previous,
        "previous_readiness_state": "",
        "previous_recovery_reason": "",
        "recovery": normalized_recovery,
        "readiness": normalized_readiness,
        "visual_stability": normalized_stability,
        "process_context": normalized_process,
        "has_recovery": bool(isinstance(recovery, dict) and recovery),
        "has_readiness": bool(isinstance(readiness, dict) and readiness),
        "has_visual_stability": bool(isinstance(visual_stability, dict) and visual_stability),
        "checkpoint_pending": bool(checkpoint_pending),
        "pending_tool": _trim_text(pending_tool, limit=80),
    }
    previous_title = _trim_text(previous.get("active_window_title", ""), limit=180)
    if previous_title:
        previous_loading = "loading" in previous_title.lower() or "settling" in _trim_text(previous.get("summary", ""), limit=180).lower()
        context["previous_readiness_state"] = "loading" if previous_loading else "ready"
    if purpose_text == "desktop_approval":
        context["previous_recovery_reason"] = str(approval_assessment.get("reason", "") or "").strip()

    for kind in SCENE_INTERPRETER_KINDS:
        for name, handler in _SCENE_INTERPRETERS.get(kind, []):
            try:
                fragment = handler(scene, context)
            except Exception:
                fragment = {}
            scene = _merge_scene_fragment(scene, fragment, name=name)

    if not scene.get("summary"):
        scene["summary"] = "Interpreted the current desktop scene from bounded desktop evidence."
    if context["prompt_text"] and any(term in context["prompt_text"].lower() for term in ("what changed", "changed", "before", "after", "compare")):
        scene["prefer_before_after"] = True
        scene["direct_image_helpful"] = True
    return normalize_desktop_scene(scene)
