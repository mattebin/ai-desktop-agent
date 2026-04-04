from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from pathlib import Path

import requests

from core.config import get_runtime_model_config, load_settings


def _extract_final_context_section(final_context: str, heading: str) -> list[str]:
    lines = str(final_context or "").splitlines()
    target = heading.strip().lower()
    capture = False
    collected: list[str] = []

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not capture:
            if stripped.lower() == target:
                capture = True
            continue

        if stripped and stripped.endswith(":") and not stripped.startswith("- "):
            break
        if stripped:
            collected.append(line)

    return collected


def _extract_final_context_value(final_context: str, prefix: str) -> str:
    target = prefix.strip().lower()
    for raw_line in str(final_context or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith(target):
            return line[len(prefix) :].strip()
    return ""


def _append_markdown_section(message: str, title: str, lines: list[str]) -> str:
    rendered = str(message or "").strip()
    safe_lines = [str(line).rstrip() for line in lines if str(line).strip()]
    if not safe_lines:
        return rendered
    section = f"## {title}\n" + "\n".join(safe_lines)
    return rendered.rstrip() + ("\n\n" if rendered else "") + section


def _contains_any_phrase(text: str, phrases: set[str] | list[str] | tuple[str, ...]) -> bool:
    lowered = re.sub(r"\s+", " ", str(text or "").strip().lower())
    return any(str(phrase).strip().lower() in lowered for phrase in phrases if str(phrase).strip())


def _normalize_sentence(text: str) -> str:
    rendered = str(text or "").strip()
    if not rendered:
        return ""
    rendered = re.sub(r"\s+", " ", rendered)
    if rendered[:1].islower():
        rendered = rendered[:1].upper() + rendered[1:]
    if rendered[-1] not in ".!?":
        rendered += "."
    return rendered


def _extract_first_section_item(final_context: str, heading: str) -> str:
    lines = _extract_final_context_section(final_context, heading)
    for line in lines:
        stripped = str(line or "").strip()
        if stripped.startswith("- "):
            return stripped[2:].strip()
        if stripped:
            return stripped
    return ""


def _desktop_vision_requested(vision: dict | None) -> bool:
    return bool(isinstance(vision, dict) and vision.get("needs_direct_image", False) and isinstance(vision.get("images", []), list))


def _image_data_url(path_text: str) -> str:
    path = Path(str(path_text or "").strip())
    if not path.exists() or not path.is_file():
        return ""
    try:
        payload = path.read_bytes()
    except Exception:
        return ""
    if not payload:
        return ""
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _content_with_desktop_vision(text: str, desktop_vision: dict | None):
    if not _desktop_vision_requested(desktop_vision):
        return text

    images = []
    image_lines = []
    for index, item in enumerate(list(desktop_vision.get("images", []))[:2]):
        if not isinstance(item, dict):
            continue
        data_url = _image_data_url(item.get("artifact_path", ""))
        if not data_url:
            continue
        role = str(item.get("role", "")).strip() or f"image {index + 1}"
        detail = str(item.get("summary", "") or item.get("active_window_title", "")).strip()
        image_lines.append(f"- Attached desktop image {index + 1} ({role}): {detail or 'selected screenshot evidence'}")
        images.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": data_url,
                    "detail": "low",
                },
            }
        )

    if not images:
        return text

    guidance = str(desktop_vision.get("summary", "")).strip()
    combined_text = str(text or "").strip()
    if guidance:
        combined_text += ("\n\n" if combined_text else "") + f"Desktop vision guidance:\n{guidance}"
    if image_lines:
        combined_text += ("\n\n" if combined_text else "") + "Bounded attached desktop image evidence:\n" + "\n".join(image_lines)
    return [{"type": "text", "text": combined_text}, *images]


def _extract_outcome_state(final_context: str) -> str:
    control_event = _extract_final_context_value(final_context, "Task control state:")
    if control_event:
        normalized_event = control_event.strip().lower()
        if normalized_event == "rejected":
            return "rejected"
    task_phase = _extract_final_context_value(final_context, "Task phase:")
    normalized_phase = task_phase.strip().lower()
    if "approval" in normalized_phase:
        return "approval_gate"
    if "completed" in normalized_phase:
        return "completed"
    if "blocked" in normalized_phase:
        return "blocked"
    if "incomplete" in normalized_phase:
        return "incomplete"
    if "deferred" in normalized_phase:
        return "deferred"
    if "superseded" in normalized_phase:
        return "superseded"
    if "stopped" in normalized_phase:
        return "stopped"
    if "failed" in normalized_phase:
        return "failed"
    if "paused" in normalized_phase:
        return "paused"
    if "queued" in normalized_phase:
        return "queued"
    return normalized_phase or "unknown"


def _goal_text(goal: str) -> str:
    return re.sub(r"\s+", " ", str(goal or "").strip().lower())


def _goal_requests_single_recommendation(goal: str) -> bool:
    return _contains_any_phrase(
        goal,
        {
            "most important next step",
            "single most important next",
            "single most important next implementation step",
            "single best recommendation",
            "one best recommendation",
            "one clear next step",
            "pick the single best",
            "top recommendation",
            "best next step",
            "what should i do next",
            "what is the next step",
        },
    )


def _goal_requests_brief_answer(goal: str) -> bool:
    return _contains_any_phrase(
        goal,
        {
            "briefly",
            "answer briefly",
            "brief answer",
            "keep the answer brief",
            "keep it brief",
            "short answer",
            "one sentence",
            "one short paragraph",
            "very short",
            "crisp answer",
            "just answer",
        },
    )


_SUPPORT_SECTION_TITLES = {
    "most relevant files used",
    "what i'm confident about",
    "uncertainties / next files to inspect",
    "suggested commands (not run)",
    "suggested commands (not executed)",
    "browser actions / observations",
    "desktop actions / observations",
    "review bundle / approval needed",
    "proposed edits (not applied)",
    "planned changes (not applied)",
    "applied changes",
}


def _first_meaningful_paragraph(message: str) -> str:
    paragraphs = re.split(r"\n\s*\n", str(message or "").strip())
    for paragraph in paragraphs:
        stripped = paragraph.strip()
        if not stripped:
            continue
        if re.match(r"^#{1,6}\s+", stripped):
            continue
        if stripped.lower() in {
            "most relevant files used",
            "what i'm confident about",
            "uncertainties / next files to inspect",
            "suggested commands (not run)",
            "browser actions / observations",
            "desktop actions / observations",
            "review bundle / approval needed",
            "proposed edits (not applied)",
            "planned changes (not applied)",
            "applied changes",
        }:
            continue
        if stripped.startswith(("- ", "* ", "1. ", "2. ", "3. ")):
            continue
        return stripped
    return ""


def _canonical_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").strip().lower()).strip()


def _dedupe_repeated_opening(message: str) -> str:
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", str(message or "").strip()) if paragraph.strip()]
    if len(paragraphs) < 2:
        return str(message or "").strip()
    first = paragraphs[0]
    second = paragraphs[1]
    if second.startswith("## "):
        return str(message or "").strip()

    first_key = _canonical_text(first)
    second_key = _canonical_text(second)
    if not first_key or not second_key:
        return str(message or "").strip()
    if first_key == second_key or first_key in second_key or second_key in first_key:
        kept = [first, *paragraphs[2:]]
        return "\n\n".join(kept).strip()
    return str(message or "").strip()


def _support_section_heading(line: str) -> bool:
    stripped = str(line or "").strip()
    if not stripped:
        return False
    if stripped.startswith("## "):
        heading = stripped[3:].strip().rstrip(":").lower()
        return heading in _SUPPORT_SECTION_TITLES
    bold_heading = re.match(r"^\*\*(.+?)\*\*\s*:?\s*$", stripped)
    if bold_heading:
        heading = bold_heading.group(1).strip().rstrip(":").lower()
        return heading in _SUPPORT_SECTION_TITLES
    return False


def _trim_supporting_sections_for_brief_goal(message: str, goal: str, final_context: str) -> str:
    if not _goal_requests_brief_answer(goal):
        return str(message or "").strip()
    if _extract_final_context_section(final_context, "Suggested commands (not executed):"):
        return str(message or "").strip()

    kept_lines: list[str] = []
    for raw_line in str(message or "").splitlines():
        if _support_section_heading(raw_line):
            break
        kept_lines.append(raw_line)
    trimmed = "\n".join(kept_lines).strip()
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", trimmed) if paragraph.strip()]
    if len(paragraphs) > 2:
        trimmed = "\n\n".join(paragraphs[:2]).strip()
    return trimmed or str(message or "").strip()


def _split_support_sections(message: str) -> tuple[str, str]:
    main_lines: list[str] = []
    support_lines: list[str] = []
    in_support = False
    for raw_line in str(message or "").splitlines():
        if not in_support and _support_section_heading(raw_line):
            in_support = True
        if in_support:
            support_lines.append(raw_line)
        else:
            main_lines.append(raw_line)
    return "\n".join(main_lines).strip(), "\n".join(support_lines).strip()


def _extract_recommendation_sentence(message: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", str(message or "").strip())
    for sentence in sentences:
        stripped = sentence.strip()
        if not stripped:
            continue
        if _contains_any_phrase(
            stripped,
            {
                "most important next step",
                "single most important next",
                "single most important next implementation step",
                "i recommend",
                "my recommendation",
                "highest-priority next step",
                "top recommendation",
                "priority should be",
            },
        ):
            return _normalize_sentence(stripped)
    return ""


def _reshape_recommendation_answer(message: str, goal: str) -> str:
    if not _goal_requests_single_recommendation(goal):
        return str(message or "").strip()

    main_body, support_body = _split_support_sections(message)
    recommendation = _extract_recommendation_sentence(main_body)
    if not recommendation:
        return str(message or "").strip()

    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", main_body) if paragraph.strip()]
    rebuilt: list[str] = [recommendation]
    seen = {_canonical_text(recommendation)}
    for paragraph in paragraphs:
        if re.search(r"^\s*(?:[-*]|\d+\.)\s+", paragraph, flags=re.MULTILINE):
            continue
        cleaned = paragraph.replace(recommendation, "").strip(" \n:-")
        key = _canonical_text(cleaned)
        if not cleaned or not key or key in seen:
            continue
        rebuilt.append(cleaned)
        seen.add(key)
        if len(rebuilt) >= 2:
            break

    rendered = "\n\n".join([part for part in rebuilt if part]).strip()
    if support_body:
        rendered = f"{rendered}\n\n{support_body}".strip()
    return rendered or str(message or "").strip()


def _strip_direct_answer_heading(message: str) -> str:
    rendered = str(message or "").strip()
    rendered = re.sub(r"^\s*#{1,6}\s*direct answer\s*\n+", "", rendered, flags=re.IGNORECASE)
    rendered = re.sub(r"^\s*direct answer\s*:?\s*\n+", "", rendered, flags=re.IGNORECASE)
    return _dedupe_repeated_opening(rendered.strip())


def _synthesize_direct_lead(final_context: str, *, goal: str = "") -> str:
    outcome = _extract_outcome_state(final_context)
    rolling_summary = _normalize_sentence(_extract_final_context_value(final_context, "Rolling summary:"))
    behavior_summary = _normalize_sentence(_extract_final_context_value(final_context, "Behavior summary:"))
    waiting_for = _normalize_sentence(_extract_final_context_value(final_context, "Waiting for:"))
    next_action = _normalize_sentence(_extract_final_context_value(final_context, "Next human action:"))
    control_reason = _normalize_sentence(_extract_final_context_value(final_context, "Task control reason:"))
    replacement_goal = _normalize_sentence(_extract_final_context_value(final_context, "Replacement goal:"))
    first_evidence = _normalize_sentence(_extract_first_section_item(final_context, "Recent evidence notes:"))

    if outcome == "completed":
        if _goal_requests_single_recommendation(goal):
            return rolling_summary or first_evidence or "I have enough evidence to recommend one primary next step."
        return rolling_summary or first_evidence or "I completed the requested work."
    if outcome == "approval_gate":
        return control_reason or waiting_for or "I paused at an approval gate and I'm waiting for your decision."
    if outcome == "paused":
        return control_reason or waiting_for or "I paused the task and I'm waiting for your next instruction."
    if outcome == "blocked":
        return control_reason or behavior_summary or next_action or "The task is blocked and needs a human decision before it can continue."
    if outcome == "rejected":
        return control_reason or "The approval was rejected, so I did not carry out the blocked step."
    if outcome == "incomplete":
        return control_reason or behavior_summary or next_action or "I made partial progress, but the task is still incomplete."
    if outcome == "deferred":
        return control_reason or next_action or "I deferred the task, so it is not running right now."
    if outcome == "superseded":
        return replacement_goal or control_reason or "I replaced the earlier task with newer work, so the original task did not continue."
    if outcome == "stopped":
        return control_reason or "I stopped the task before it finished."
    if outcome == "failed":
        return control_reason or behavior_summary or "The task failed before it could complete."
    if outcome == "queued":
        return waiting_for or "The task is queued and waiting to run."
    return rolling_summary or behavior_summary or first_evidence


def _ensure_direct_lead(message: str, final_context: str, *, goal: str = "") -> str:
    rendered = _strip_direct_answer_heading(message)
    if not rendered:
        return _synthesize_direct_lead(final_context, goal=goal)

    first_paragraph = _first_meaningful_paragraph(rendered)
    if first_paragraph and not re.match(r"^#{1,6}\s+", first_paragraph):
        return rendered

    lead = _synthesize_direct_lead(final_context, goal=goal)
    if not lead:
        return rendered
    if lead.lower() in rendered.lower():
        return rendered
    return f"{lead}\n\n{rendered}".strip()


def _message_bullet_count(message: str) -> int:
    return len(
        [
            line
            for line in str(message or "").splitlines()
            if re.match(r"^\s*(?:[-*]|\d+\.)\s+", line.strip())
        ]
    )


def _looks_like_laundry_list(message: str) -> bool:
    return _message_bullet_count(message) >= 3 or _contains_any_phrase(
        message,
        {
            "here are a few",
            "here are several",
            "several next steps",
            "multiple next steps",
            "a few options",
        },
    )


def _has_next_step_language(message: str) -> bool:
    return _contains_any_phrase(
        message,
        {
            "next step",
            "next human action",
            "if you want",
            "approve",
            "retry",
            "resume",
            "replace",
            "continue by",
            "provide",
        },
    )


def _ensure_outcome_handoff(message: str, final_context: str, *, goal: str = "") -> str:
    rendered = str(message or "").strip()
    if not rendered:
        return rendered

    outcome = _extract_outcome_state(final_context)
    waiting_for = _normalize_sentence(_extract_final_context_value(final_context, "Waiting for:"))
    next_action = _normalize_sentence(_extract_final_context_value(final_context, "Next human action:"))
    control_reason = _normalize_sentence(_extract_final_context_value(final_context, "Task control reason:"))

    follow_up = ""
    if outcome in {"approval_gate", "paused"} and not _contains_any_phrase(rendered, {"approval", "approve", "waiting"}):
        follow_up = waiting_for or next_action or control_reason
    elif outcome in {"blocked", "incomplete", "deferred", "failed", "rejected"} and not _has_next_step_language(rendered):
        follow_up = next_action or waiting_for or control_reason
    elif outcome == "completed" and _goal_requests_single_recommendation(goal) and _looks_like_laundry_list(rendered):
        follow_up = _normalize_sentence(
            _extract_first_section_item(final_context, "Recent evidence notes:")
            or _extract_final_context_value(final_context, "Rolling summary:")
        )

    if not follow_up:
        return rendered
    if _canonical_text(follow_up) in _canonical_text(rendered):
        return rendered
    return f"{rendered}\n\n{follow_up}".strip()


def _should_include_supporting_sections(goal: str, final_context: str) -> bool:
    outcome = _extract_outcome_state(final_context)
    if outcome in {"approval_gate", "paused", "deferred", "superseded", "stopped", "rejected"}:
        return False
    if outcome == "completed" and _goal_requests_brief_answer(goal):
        return False
    return outcome in {"completed", "incomplete", "blocked", "failed", "unknown", ""}


def _ensure_section_from_context(message: str, final_context: str, *, heading: str, rendered_title: str) -> str:
    rendered = str(message or "").strip()
    if rendered_title.lower() in rendered.lower():
        return rendered
    section_lines = _extract_final_context_section(final_context, heading)
    if not section_lines:
        return rendered
    return _append_markdown_section(rendered, rendered_title, section_lines)


def _ensure_suggested_commands_section(message: str, final_context: str) -> str:
    rendered = str(message or "").strip()
    if not rendered:
        return rendered
    if "suggested commands" in rendered.lower():
        return rendered

    command_lines = _extract_final_context_section(final_context, "Suggested commands (not executed):")
    if not command_lines:
        return rendered

    return rendered.rstrip() + "\n\n## Suggested Commands (Not Run)\n" + "\n".join(command_lines)


def _ensure_confidence_section(message: str, final_context: str) -> str:
    rendered = str(message or "").strip()
    if "what i'm confident about" in rendered.lower():
        return rendered
    confidence_summary = _extract_final_context_value(final_context, "Confidence summary:")
    if not confidence_summary:
        return rendered
    return _append_markdown_section(rendered, "What I'm Confident About", [confidence_summary])


def _ensure_uncertainties_section(message: str, final_context: str) -> str:
    rendered = str(message or "").strip()
    if "uncertainties / next files to inspect" in rendered.lower():
        return rendered
    next_files = _extract_final_context_section(final_context, "Next files to inspect if needed:")
    if not next_files:
        return rendered
    return _append_markdown_section(rendered, "Uncertainties / Next Files to Inspect", next_files)


def _ensure_core_final_sections(message: str, goal: str, final_context: str) -> str:
    rendered = _ensure_direct_lead(message, final_context, goal=goal)
    rendered = _ensure_outcome_handoff(rendered, final_context, goal=goal)
    rendered = _reshape_recommendation_answer(rendered, goal)
    rendered = _trim_supporting_sections_for_brief_goal(rendered, goal, final_context)
    if not rendered:
        return rendered
    if _should_include_supporting_sections(goal, final_context):
        rendered = _ensure_section_from_context(
            rendered,
            final_context,
            heading="Most relevant files used:",
            rendered_title="Most Relevant Files Used",
        )
        rendered = _ensure_section_from_context(
            rendered,
            final_context,
            heading="Desktop Actions / Observations:",
            rendered_title="Desktop Actions / Observations",
        )
        rendered = _ensure_confidence_section(rendered, final_context)
        rendered = _ensure_uncertainties_section(rendered, final_context)
    rendered = _ensure_suggested_commands_section(rendered, final_context)
    return rendered


class HostedLLMClient:
    def __init__(self, settings: dict[str, object] | None = None):
        settings = settings if isinstance(settings, dict) else load_settings()
        runtime = get_runtime_model_config(settings)
        self.api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set.")
        self._apply_runtime(runtime)

    def _apply_runtime(self, runtime: dict[str, object]):
        self.base_url = str(runtime.get("base_url", "")).strip()
        self.model = str(runtime.get("active_model", "")).strip()
        self.reasoning_effort = str(runtime.get("reasoning_effort", "")).strip()
        self.reasoning_effort_applies_to_tool_calls = False
        self.settings_path = str(runtime.get("settings_path", "")).strip()
        self.settings_sources = runtime.get("settings_sources", [self.settings_path])
        self.source = runtime.get("source", "config/settings.yaml")
        self.settings_version = str(runtime.get("settings_version", "")).strip()
        self.settings_loaded_at = str(runtime.get("settings_loaded_at", "")).strip()
        self.settings_reload_count = int(runtime.get("settings_reload_count", 0) or 0)

    def reload_settings(self, settings: dict[str, object] | None = None) -> dict[str, object]:
        safe_settings = settings if isinstance(settings, dict) else load_settings()
        runtime = get_runtime_model_config(safe_settings)
        self._apply_runtime(runtime)
        return runtime

    def get_runtime_config(self) -> dict[str, object]:
        return {
            "active_model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "reasoning_scope": "non_tool_turns" if self.reasoning_effort else "disabled",
            "reasoning_effort_applies_to_tool_calls": self.reasoning_effort_applies_to_tool_calls,
            "base_url": self.base_url,
            "settings_path": self.settings_path,
            "settings_sources": self.settings_sources,
            "source": self.source,
            "settings_version": self.settings_version,
            "settings_loaded_at": self.settings_loaded_at,
            "settings_reload_count": self.settings_reload_count,
        }

    def _call(self, messages, tools=None, *, timeout_seconds=None):
        self.reload_settings()
        payload = {
            "model": self.model,
            "messages": messages,
        }
        if self.reasoning_effort and not tools:
            payload["reasoning_effort"] = self.reasoning_effort
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        has_images = any(
            isinstance(message.get("content"), list)
            and any(isinstance(part, dict) and part.get("type") == "image_url" for part in message.get("content", []))
            for message in messages
            if isinstance(message, dict)
        )

        timeout = 120 if has_images else 60
        try:
            if timeout_seconds is not None:
                timeout = max(5, float(timeout_seconds))
        except (TypeError, ValueError):
            timeout = 120 if has_images else 60

        r = requests.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()

    def plan_next_action(self, goal, observation, tools, *, desktop_vision=None):
        messages = [
            {
                "role": "developer",
                "content": (
                    "You are a careful Windows file-inspection agent. "
                    "Your job is to complete the goal with as few tool calls as reasonably possible. "
                    "Prefer using memory in the observation instead of re-reading the same files. "
                    "Use inspect_project first for project-level or folder-level understanding when it can replace multiple smaller tool calls. "
                    "inspect_project can reuse a recent in-session cache for the same path and scan settings. "
                    "Only set refresh=true when you specifically need a fresh rescan because the folder may have changed. "
                    "After inspect_project, prefer the recommended_files and Priority files for current goal list before calling read_file. "
                    "When the goal is about differences, changes, regressions, what broke, or whether two files match, prefer compare_files over reading both files separately. "
                    "After one successful compare_files call for the same pair, do not call compare_files again with unchanged paths; use the result and finalize unless a specific unanswered question remains. "
                    "Use suggest_commands when the user wants exact commands, manual verification steps, or safe next actions to run themselves. Never auto-run suggested commands. "
                    "After one successful suggest_commands call for the same goal and evidence, do not call suggest_commands again with unchanged inputs; finalize unless the user explicitly asked for a broader or different command set. "
                    "Use plan_patch when the user wants proposed code or file changes, implementation order, or a safe patch plan without modifying files. Never describe planned changes as executed. "
                    "Use draft_proposed_edits when the user wants reviewable edit drafts or patch-style previews without modifying files, ideally after you already have a patch plan or strong evidence. Never describe drafted edits as applied. "
                    "Use build_review_bundle when the user wants an approval-ready review bundle that groups planned changes, proposed edits, and suggested commands before any real editing. Never describe a review bundle as approved or applied by default. "
                    "Use apply_approved_edits only when the user has explicitly approved exact edits and the tool input already contains approval_status=approved plus concrete edit instructions. Never infer approval or auto-apply proposed edits. "
                    "Use the browser_* tools only for browser pages or web-app tasks; keep them separate from desktop control. "
                    "Use the desktop_* tools only for bounded native Windows desktop inspection or one-step interaction. "
                    "Use the email_* tools for Gmail-only inbox work. Use email_connect_gmail once when the user explicitly wants Gmail access and the account is not connected yet. "
                    "Use email_list_threads and email_read_thread for read-only inbox and thread inspection, email_prepare_reply_draft and email_prepare_forward_draft to prepare frozen local drafts without sending, and email_send_draft only after a draft already exists. "
                    "email_send_draft is approval-gated: if you call it without approval_status=approved, it will intentionally pause so the operator can review the exact draft and approve or reject it. "
                    "For desktop work, inspect first: use desktop_list_windows or desktop_get_active_window for quick window state, desktop_inspect_window_state when the target may be minimized, hidden, loading, wrong-foreground, or visually unstable, and desktop_capture_screenshot for bounded screenshot evidence. "
                    "Use desktop_focus_window for normal bounded focus, desktop_recover_window when the target needs restore/show/refocus recovery, and desktop_wait_for_window_ready when the window exists but still looks loading, not ready, or visually unstable. "
                    "desktop_capture_screenshot only captures bounded state; it does not interpret pixels or do OCR. "
                    "Use the Selected desktop evidence, Checkpoint desktop evidence, Selected desktop scene, and Checkpoint desktop scene lines in the observation as the authoritative compact grounding for desktop reasoning; do not ignore them or invent a second desktop state narrative. "
                    "Use the Selected desktop target proposals and Checkpoint desktop target proposals lines as the authoritative compact next-target candidates for bounded desktop planning; prefer high-confidence proposals, respect recovery-first or no-safe-target states, and do not invent a more certain target than the evidence supports. "
                    "When bounded desktop image evidence is attached, treat it as the authoritative direct visual grounding for the selected desktop window or checkpoint. "
                    "If a desktop scene line says the scene is loading, blocked, prompt-like, dialog-like, or changed, respect that compact interpretation before planning more desktop actions. "
                    "Use attached images only when compact summaries are not enough, and do not ask for another identical screenshot if the attached image already answers the desktop question. "
                    "If the selected desktop evidence assessment says the current evidence is sufficient for a read-only desktop answer, answer from that evidence instead of collecting another identical observation. "
                    "If the desktop evidence assessment says refresh is needed before a desktop action, collect one fresh desktop observation or screenshot before planning the paused desktop action. Do not loop on repeated refreshes. "
                    "When a paused desktop approval exists, ground the approval explanation in the linked checkpoint desktop evidence summary and assessment. "
                    "Use desktop_move_mouse, desktop_hover_point, desktop_click_mouse, and desktop_scroll for bounded mouse control when exact coordinates or a bounded relative target are already established from prior inspected desktop evidence. "
                    "Use desktop_press_key for one safe key combo, desktop_press_key_sequence for a short bounded chain of safe combos, desktop_type_text for bounded field text entry, desktop_list_processes and desktop_inspect_process for local process diagnostics, desktop_start_process and desktop_stop_process only for bounded owned processes, and desktop_run_command only for one bounded local command with timeout. "
                    "desktop_click_point, desktop_move_mouse, desktop_hover_point, desktop_click_mouse, desktop_scroll, desktop_press_key, desktop_press_key_sequence, desktop_type_text, desktop_start_process, desktop_stop_process, and desktop_run_command are approval-gated in this pass. Only pass approval_status=approved when the current goal explicitly says the paused desktop action is now approved. Never invent approval_status=approved on your own. "
                    "If the user wants a real desktop mouse action, bounded key action, desktop type action, process action, or bounded command execution that is not yet approved, you must still plan the corresponding desktop_* tool call without approval_status=approved so the tool can create the structured paused checkpoint. Do not replace that with a prose-only approval request. "
                    "After the safe inspect/focus steps succeed, prefer the actual paused desktop tool call over finishing early when the goal explicitly asks for one bounded click or one bounded text entry. "
                    "Never invent desktop coordinates. Only use exact coordinates provided by the user or already established in prior observed desktop state. "
                    "desktop_press_key is only for safe navigation keys and a small allowlist of Ctrl-based shortcuts in the currently active window. Do not use it for unrestricted hotkeys, Windows/system keys, or global shortcuts. "
                    "desktop_press_key_sequence is only for short bounded combinations from that same safe allowlist. Do not turn it into freeform macro playback. "
                    "desktop_type_text is only for bounded text into the currently focused desktop field in the active window. Do not use it for passwords, secrets, or long freeform text. "
                    "desktop_start_process, desktop_stop_process, and desktop_run_command are for bounded local control and diagnostics only. Do not treat them as unrestricted system management or a general shell agent. "
                    "If the user already gave the exact non-sensitive field label and text to type, you may rely on that user-provided field_label once the intended window is focused; do not demand screenshot or OCR proof of the field label before creating the paused desktop_type_text checkpoint. "
                    "Keep desktop control to one bounded action at a time. No hotkeys outside the safe allowlist, drag-and-drop, repeated clicking loops, autonomous desktop navigation, or generalized mouse/keyboard control. "
                    "If the requested desktop target window is missing, say that clearly instead of guessing. "
                    "If no browser page is currently open, use browser_open_page first. Do not call browser_inspect_page, browser_click, browser_type, browser_extract_text, or browser_follow_link before a page is open. "
                    "Once a page is open, use browser_inspect_page for page-level understanding before clicking or typing when the structure is unknown. "
                    "Use browser_click for safe element clicks, browser_type for typing into fields without submitting, browser_extract_text for visible text, and browser_follow_link for safe link navigation. "
                    "If a browser click looks like it may submit, confirm, log in, pay, save, or otherwise change remote state, only pass approval_status=approved when the user has explicitly approved it. Never invent approval_status=approved on your own; only use it when the current goal explicitly says the risky step is now approved. Browser tools may also return a paused approval checkpoint when a risky or explicitly checkpointed step should stop before continuing. "
                    "If an element is missing, inspect the page again and use the returned links, buttons, or inputs to try an alternative locator instead of guessing. Do not keep repeating the same failing locator when the latest page inspection already exposed better placeholder, label, name, or selector-hint evidence. "
                    "For form fields, prefer exact placeholders, labels, names, or selector hints returned by browser_inspect_page over invented CSS selectors. If the latest page inspection shows a usable input or button, reuse that evidence directly. "
                    "For reusable browser work, keep a bounded browser_task_name such as open_and_inspect, search_and_extract, fill_form_until_checkpoint, or follow_and_summarize. Carry browser_task_step forward so the active browser task can continue coherently across steps instead of replanning from scratch. "
                    "Use open_and_inspect for quick page understanding, search_and_extract for search-page flows, fill_form_until_checkpoint for form entry that should pause before risky submit-like transitions, and follow_and_summarize for link-following plus extraction. Only switch browser_task_name when the goal changes or the prior browser task clearly completed, blocked, or no longer fits. "
                    "For approval-gated form tasks, the normal safe order is: browser_open_page, browser_inspect_page, browser_type, browser_click, then inspect or extract confirming text if needed. "
                    "If the goal explicitly asks you to follow a link, inspect a destination page, or summarize destination text, do not finalize after the first page inspection if a safe follow-link step is available. Continue until the requested destination page has been reached, inspected, and summarized, or until a concrete blocker appears. "
                    "If the goal explicitly asks you to fill a form and pause before a submit-like click, you must still complete the safe pre-checkpoint steps first: open the page, inspect it, and type into the approved fields. Do not finalize early just because the last risky click is still unapproved; stop only at the actual approval checkpoint or on a concrete blocker. "
                    "For multi-step browser tasks, use the Browser context in the observation, especially the browser task pattern, current browser task step, next browser task step, task status, workflow, current workflow step, next workflow step, last successful browser action, expected next state, browser recovery notes, and any pending approval checkpoint, before deciding the next step. If the workflow is paused, do not continue the paused step unless the user has explicitly approved it. "
                    "When the goal is to fill a form until a checkpoint, once typing succeeds, prefer browser_click on the actual submit-like control so the workflow can pause cleanly at approval instead of looping on more inspections. If only the submit-like click is unapproved, do not place the approval checkpoint on browser_open_page, browser_inspect_page, or browser_type. "
                    "When a browser checkpoint is paused and the user explicitly approves it, resume the same paused browser tool immediately with approval_status=approved before any other browser tool. Do not reinterpret the paused target as a different field, link, or action, and do not invent a new selector or fallback target when the paused step already exists. After an approved or resumed risky browser click succeeds, do not click the same risky control again unless the user explicitly asks to repeat it, and do not keep re-inspecting once one confirming inspection or text extraction already shows the changed page state. "
                    "When a browser action should reach a specific page, title, or visible content, pass expected_target, expected_url_contains, expected_title_contains, expected_text_contains, or expect_navigation so the bounded recovery layer can verify the result. For repeated browser tasks, keep a bounded workflow_name or workflow_pattern such as form_flow or navigation_extract_flow, and carry workflow_step and workflow_next_step forward so the task can resume coherently across steps. Use checkpoint_required and checkpoint_reason for important browser transitions that should pause for review before continuing. If explicit approval exists for a paused step, resume that same browser tool with approval_status=approved; if browser context may be stale, prefer browser_open_page or browser_inspect_page first to re-establish the page safely. "
                    "Browser tools support limited recovery with max_retries, allow_reinspect, and allow_reload. Keep retries bounded, and after repeated failures prefer browser_inspect_page or a different locator instead of repeating the exact same action blindly. "
                    "Read only the top one or two relevant files first, and avoid low-signal files like tests or cache/build output unless the goal points there. "
                    "Use browser_open_page, browser_inspect_page, browser_click, browser_type, browser_extract_text, and browser_follow_link for bounded browser-only work. "
                    "Use desktop_list_windows, desktop_get_active_window, desktop_inspect_window_state, desktop_focus_window, desktop_recover_window, desktop_wait_for_window_ready, and desktop_capture_screenshot for bounded desktop inspection and recovery, and use desktop_move_mouse, desktop_hover_point, desktop_click_mouse, desktop_click_point, desktop_scroll, desktop_press_key, desktop_press_key_sequence, desktop_type_text, desktop_start_process, desktop_stop_process, or desktop_run_command only after explicit approval when a single real bounded control action is required. "
                    "Use email_connect_gmail for one-time Gmail setup, email_list_threads and email_read_thread for read-only email context, email_prepare_reply_draft and email_prepare_forward_draft for frozen draft creation, and email_send_draft for the final approval-gated send step. "
                    "Use list_files to inspect folders, search_files to find likely targets, compare_files to compare two candidate files, suggest_commands for non-executing PowerShell suggestions, plan_patch for non-executing change plans, draft_proposed_edits for non-executing reviewable edit drafts, build_review_bundle for approval-ready review packaging, apply_approved_edits for explicit approved file writes with backups, read_file only for relevant files, "
                    "and run_shell only for safe read-only inspection when execution is explicitly necessary. "
                    "Respect the Operator mode, Task phase, Waiting for, Next human action, and Action policy lines in the observation. "
                    "If the user asks for the most important next step, gather enough evidence to make one primary recommendation rather than a long list. "
                    "If the user asks for a brief answer or a single recommendation, stop as soon as the evidence supports one grounded answer instead of collecting extra low-value detail. "
                    "After a task has been superseded, stopped, deferred, or rejected, do not keep pursuing the old goal unless the user explicitly asks to resume or retry it. "
                    "For safe browser research or information-gathering work, continue automatically inside the current safe scope and do not pause unless a step would clearly change remote state. "
                    "If you already have enough evidence for a crisp answer, stop inspecting and finalize instead of continuing to browse or reread."
                    " If enough information is already available, do not call a tool."
                ),
            },
            {
                "role": "user",
                "content": _content_with_desktop_vision(
                    f"Goal:\n{goal}\n\nObservation:\n{observation}",
                    desktop_vision,
                ),
            },
        ]

        api_tools = []
        for t in tools:
            api_tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            })

        data = self._call(messages, tools=api_tools)
        msg = data["choices"][0]["message"]

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            return {"done": True, "message": msg.get("content", "No tool call returned.")}

        call = tool_calls[0]
        name = call["function"]["name"]
        try:
            args = json.loads(call["function"]["arguments"])
        except Exception:
            args = {}

        return {"tool": name, "args": args}

    def reply_in_chat(self, user_message, *, session_context="", mode="chat", desktop_vision=None):
        messages = [
            {
                "role": "developer",
                "content": (
                    "You are the conversational surface for a local AI operator. "
                    "Reply naturally, directly, and helpfully. "
                    "Treat any operator status, workflow, approval, file, or browser details as background context, not as a report template. "
                    "For normal conversational turns, answer like a polished chat assistant rather than an internal dashboard. "
                    "The reply mode may be normal_chat, read_only_investigation, workflow_execution, approval_needed_action, paused_waiting, or final_report. "
                    "Match the tone and content to that mode instead of defaulting to a generic operator summary. "
                    "If the user is asking what happened, what changed, what you are waiting for, or for clarification, explain the current state clearly without pretending to start new work. "
                    "If the latest user message is a simple standalone question, answer it directly and do not drag prior operator context into the reply unless the user clearly asks about that prior work. "
                    "If a simple casual question appears inside a busy session, answer only that question and keep any operator context out of the reply unless the user explicitly asks about it. "
                    "If older operator context conflicts with the latest user turn, follow the latest user turn and do not let stale task details bleed into the answer. "
                    "If the prior task was stopped, explicitly say it was stopped or halted before completion, and say whether any meaningful work had already happened. "
                    "If the user asks what the most important next step is after finished or interrupted work, give one primary recommendation grounded in the provided context instead of a long list. "
                    "If approval is required, make that explicit and brief, and tell the user exactly what decision is blocking progress. "
                    "If a desktop approval or desktop investigation context includes compact evidence summary lines or scene lines, use that evidence compactly instead of vague desktop narration. "
                    "When bounded desktop image evidence is attached, use it as the direct visual grounding for the current desktop reply instead of speculating from text alone. "
                    "If the mode is final_report, answer from the completed work as one authoritative reply rather than a stream of status updates. "
                    "Do not claim you started, applied, approved, clicked, typed, or changed anything unless the provided context explicitly says it already happened. "
                    "Keep the response calm and conversation-first. Avoid headings unless they clearly help."
                ),
            },
            {
                "role": "user",
                "content": _content_with_desktop_vision(
                    (
                        f"Reply mode: {mode}\n\n"
                        f"Session Context:\n{session_context}\n\n"
                        f"Latest User Message:\n{user_message}"
                    ),
                    desktop_vision,
                ),
            },
        ]
        data = self._call(messages)
        return data["choices"][0]["message"]["content"].strip()

    def finalize(self, goal, steps, observation="", final_context="", *, desktop_vision=None, timeout_seconds=None):
        messages = [
            {
                "role": "developer",
                "content": (
                    "You are the final answer surface for a local AI operator. "
                    "Provide exactly one authoritative final answer for this turn. "
                    "Start with a direct natural-language answer sentence, not a markdown heading. "
                    "Answer the user's goal directly in natural prose first. "
                    "Use the provided final context and steps as evidence, but do not turn the reply into an internal report unless the evidence truly requires it. "
                    "Keep the main answer complete, confident, and calm. "
                    "If the task is paused, blocked, incomplete, stopped, deferred, superseded, or needs_attention, lead with the current state, what is grounded, and the exact next human action needed. "
                    "If the task is completed, make the main answer feel like a finished chat response, not a status digest. "
                    "If the task is only partially successful, clearly separate what completed successfully from what remains unresolved, and do not make it sound like a total failure. "
                    "If the goal asks for a next step or recommendation, prefer one primary recommendation over a laundry list unless the evidence clearly supports multiple equally important next steps. "
                    "If the goal asks for a brief answer, keep the main answer brief and skip supporting sections unless they materially improve trust or safety. "
                    "If the task was superseded or stopped, say that clearly and do not imply the original work finished. "
                    "Ignore stale or superseded goal details unless they are needed to explain the current outcome cleanly. "
                    "If a required approval was rejected, make that explicit and explain the sane next options. "
                    "Avoid repeating raw tool output, progress chatter, or long verbatim excerpts. "
                    "After the main answer, add only the compact sections that materially help. "
                    "Prefer one short paragraph plus at most a few compact sections over a long multi-heading report. "
                    "Use these section titles when relevant: Most Relevant Files Used, What I'm Confident About, Suggested Commands (Not Run), Browser Actions / Observations, Desktop Actions / Observations, Review Bundle / Approval Needed, Proposed Edits (Not Applied), Planned Changes (Not Applied), Applied Changes, Uncertainties / Next Files to Inspect. "
                    "Only include Most Relevant Files Used and What I'm Confident About when they materially help the user trust or act on the answer. "
                    "If the final context includes command suggestions, always include Suggested Commands (Not Run) and make it explicit that they were not executed. "
                    "Only describe changes as applied when apply_approved_edits completed successfully. Keep Applied Changes separate from Review Bundle / Approval Needed, Proposed Edits (Not Applied), and Planned Changes (Not Applied). "
                    "Never describe review bundles as approved or applied unless explicit approval exists. "
                    "Never describe drafted edits or planned changes as completed or applied. "
                    "Never imply blocked or paused browser actions succeeded; state clearly what was observed, clicked, typed, followed, paused pending approval, resumed after approval, or blocked pending approval. "
                    "Never imply paused or rejected desktop actions succeeded; state clearly what windows were inspected, which window was focused, whether a screenshot was captured, and whether a desktop click or type action was approved, executed, blocked, or rejected. "
                    "When compact desktop evidence summaries, desktop evidence assessment lines, or desktop scene lines are present, use them to ground desktop-related conclusions and approval explanations in one or two calm sentences instead of vague desktop prose. "
                    "When bounded desktop image evidence is attached, use it as the direct visual grounding for desktop conclusions, changed-state interpretation, and approval explanations instead of pretending the summaries alone proved everything. "
                    "If desktop evidence was sufficient, say what evidence you relied on in compact form when that materially improves trust. If desktop evidence was partial, stale, or missing, say that clearly without implying the desktop state is fully confirmed. "
                    "When browser task-library or workflow state is present, summarize it cleanly instead of dumping internal labels. "
                    "Use markdown sparingly and only when it improves scanability."
                ),
            },
            {
                "role": "user",
                "content": _content_with_desktop_vision(
                    (
                        f"Goal:\n{goal}\n\n"
                        f"Final Context:\n{final_context}\n\n"
                        f"Observation:\n{observation}\n\n"
                        f"Steps:\n{json.dumps(steps, indent=2, ensure_ascii=False)}"
                    ),
                    desktop_vision,
                ),
            },
        ]
        data = self._call(messages, timeout_seconds=timeout_seconds)
        message = data["choices"][0]["message"]["content"].strip()
        return _ensure_core_final_sections(message, goal, final_context)
