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
                    "You are a Windows desktop operator. "
                    "Complete the user's goal using the available tools. Be efficient — use as few tool calls as possible. "
                    "Pick the right tool for the job: desktop_open_target for files/URLs/folders, "
                    "desktop_press_key for keyboard input, desktop_type_text for text entry, "
                    "desktop_run_command for shell commands, desktop_capture_screenshot for visual state. "
                    "When the user gives a name without a full path (e.g. 'open Downloads', 'open my project'), "
                    "resolve it using the Home directory from the observation, common Windows paths "
                    "(Desktop, Documents, Downloads, Pictures, Videos, Music are under Home), "
                    "or use desktop_run_command with 'dir' or 'where' to find it. "
                    "If the goal is already done or you have enough info, stop and return your answer. "
                    "Do not over-inspect — act directly when the action is obvious."
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
                    "You are a Windows desktop operator assistant. "
                    "Reply naturally and directly. Answer the user's question using the session context provided. "
                    "Be concise. Do not repeat raw tool output or internal state."
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
                    "You are a Windows desktop operator. "
                    "Provide a concise final answer for the completed task. "
                    "Start with a direct answer, not a heading. Be brief. "
                    "If the task failed or is incomplete, say what happened and what the user can do next."
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
