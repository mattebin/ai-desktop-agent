from __future__ import annotations

from typing import Any, Dict, List

from core.skill_registry import list_skill_catalog


def _builtin_commands() -> List[Dict[str, Any]]:
    return [
        {
            "type": "local",
            "name": "new",
            "aliases": ["new-chat"],
            "description": "Create a new conversation and focus it immediately.",
            "action": "new-chat",
            "category": "session",
            "source": "built_in",
        },
        {
            "type": "local",
            "name": "refresh",
            "aliases": ["reload"],
            "description": "Refresh conversations, status, commands, and the current operator view.",
            "action": "refresh",
            "category": "session",
            "source": "built_in",
        },
        {
            "type": "local",
            "name": "details",
            "description": "Show, hide, or toggle the right-side operator details rail.",
            "argumentHint": "[show|hide|toggle]",
            "action": "toggle-details",
            "category": "view",
            "source": "built_in",
        },
        {
            "type": "local",
            "name": "theme",
            "description": "Switch the desktop UI theme.",
            "argumentHint": "[light|dark|toggle]",
            "action": "toggle-theme",
            "category": "view",
            "source": "built_in",
        },
        {
            "type": "local",
            "name": "approve",
            "description": "Approve the current blocked step if an approval is waiting.",
            "action": "approve",
            "category": "approval",
            "source": "built_in",
        },
        {
            "type": "local",
            "name": "reject",
            "aliases": ["deny"],
            "description": "Reject the current blocked step if an approval is waiting.",
            "action": "reject",
            "category": "approval",
            "source": "built_in",
        },
        {
            "type": "local",
            "name": "skills",
            "description": "Show the repo-local skills and their slash aliases.",
            "action": "show-skills",
            "category": "catalog",
            "source": "built_in",
        },
        {
            "type": "local",
            "name": "runtime",
            "description": "Show the active runtime model, effort, and merged config sources.",
            "action": "show-runtime",
            "category": "catalog",
            "source": "built_in",
        },
        {
            "type": "local",
            "name": "tools",
            "description": "Show the registered tools and their approval policy levels.",
            "action": "show-tools",
            "category": "catalog",
            "source": "built_in",
        },
        {
            "type": "local",
            "name": "help",
            "aliases": ["commands"],
            "description": "Show a quick summary of the available slash commands.",
            "action": "help",
            "category": "catalog",
            "source": "built_in",
        },
        {
            "type": "prompt",
            "name": "architecture",
            "aliases": ["arch"],
            "description": "Ask the operator for a high-level architecture walkthrough.",
            "promptText": "Inspect this project and explain the main architecture.",
            "category": "inspection",
            "source": "built_in",
        },
        {
            "type": "prompt",
            "name": "compare-loop",
            "aliases": ["compare"],
            "description": "Ask the operator to compare the loop and agent implementation.",
            "promptText": "Compare the main loop and agent files and summarize the differences.",
            "category": "inspection",
            "source": "built_in",
        },
        {
            "type": "prompt",
            "name": "operator-state",
            "aliases": ["state"],
            "description": "Ask for concrete read-only commands to inspect runtime state.",
            "promptText": "Suggest exact read-only commands to inspect the operator state.",
            "category": "inspection",
            "source": "built_in",
        },
    ]


def list_slash_commands() -> List[Dict[str, Any]]:
    commands = _builtin_commands()
    existing_names = {str(command.get("name", "")).strip().lower() for command in commands}
    for skill in list_skill_catalog():
        command_name = str(skill.get("commandName", "")).strip()
        if not command_name or command_name.lower() in existing_names:
            continue
        commands.append(
            {
                "type": "prompt",
                "name": command_name,
                "aliases": list(skill.get("aliases", [])),
                "description": str(skill.get("description", "")).strip() or str(skill.get("title", "")).strip(),
                "argumentHint": str(skill.get("argumentHint", "")).strip(),
                "promptText": str(skill.get("promptText", "")).strip(),
                "category": "skill",
                "source": "repo_skill",
                "skillSlug": str(skill.get("slug", "")).strip(),
                "relativePath": str(skill.get("relativePath", "")).strip(),
            }
        )
        existing_names.add(command_name.lower())
    return commands


def _trim_text(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _parse_slash_command_input(text: str) -> Dict[str, str] | None:
    rendered = str(text or "")
    if not rendered.startswith("/"):
        return None
    raw = rendered[1:]
    trimmed = raw.strip()
    if not trimmed:
        return {"raw": raw, "query": "", "args": ""}
    first_space = raw.find(" ")
    if first_space < 0:
        return {"raw": raw, "query": raw.strip(), "args": ""}
    return {
        "raw": raw,
        "query": raw[:first_space].strip(),
        "args": raw[first_space + 1 :].strip(),
    }


def _find_slash_command(query: str, commands: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    normalized = str(query or "").strip().lower()
    if not normalized:
        return None
    for command in commands:
        name = str(command.get("name", "")).strip().lower()
        aliases = [str(alias).strip().lower() for alias in list(command.get("aliases", []))]
        if normalized == name or normalized in aliases:
            return command
    return None


def _resolve_prompt_command(command: Dict[str, Any], args: str) -> str:
    base_prompt = str(command.get("promptText", "")).strip()
    trimmed_args = str(args or "").strip()
    if not base_prompt:
        return ""
    if not trimmed_args:
        return base_prompt
    return f"{base_prompt}\n\nAdditional context: {trimmed_args}"


def _slash_command_help_text(commands: List[Dict[str, Any]]) -> str:
    return "\n".join(
        f"/{str(command.get('name', '')).strip()}"
        f"{(' ' + str(command.get('argumentHint', '')).strip()) if str(command.get('argumentHint', '')).strip() else ''} - "
        f"{str(command.get('description', '')).strip()}"
        for command in commands
        if str(command.get("name", "")).strip()
    )


def _format_skill_catalog_detail() -> str:
    skills = list_skill_catalog()
    if not skills:
        return "No repo-local skills are available right now."
    return "\n".join(
        f"/{str(skill.get('commandName', '') or skill.get('slug', '') or skill.get('title', 'skill')).strip()} - "
        f"{str(skill.get('description', '') or skill.get('purpose', '') or 'Repo-local skill').strip()}"
        for skill in skills
    )


def _format_runtime_detail(controller: Any) -> str:
    runtime = controller.get_runtime_config() if controller is not None else {}
    model = str(runtime.get("active_model", "unknown")).strip()
    effort = str(runtime.get("reasoning_effort", "default")).strip()
    sources = runtime.get("settings_sources", [])
    if isinstance(sources, list) and sources:
        sources_text = "\n".join(str(item).strip() for item in sources if str(item).strip())
    else:
        sources_text = str(runtime.get("source", "config/settings.yaml")).strip()
    version = str(runtime.get("settings_version", "")).strip()
    loaded_at = str(runtime.get("settings_loaded_at", "")).strip()
    reload_count = int(runtime.get("settings_reload_count", 0) or 0)
    policy_summary = str((runtime.get("tool_policy", {}) or {}).get("summary", "")).strip()
    lines = [
        f"Model: {model}",
        f"Reasoning effort: {effort}",
        f"Reload count: {reload_count or 1}",
    ]
    if version:
        lines.append(f"Settings version: {version}")
    if loaded_at:
        lines.append(f"Loaded at: {loaded_at}")
    if policy_summary:
        lines.append(f"Policy: {policy_summary}")
    lines.append("Sources:")
    lines.append(sources_text or "config/settings.yaml")
    return "\n".join(lines)


def _format_tool_catalog_detail(controller: Any) -> str:
    tools = controller.get_tool_catalog() if controller is not None else []
    if not tools:
        return "No tools are registered right now."
    lines: List[str] = []
    for tool in tools:
        name = str(tool.get("name", "")).strip() or "tool"
        policy = tool.get("policy", {}) if isinstance(tool.get("policy", {}), dict) else {}
        risk = str(policy.get("risk_level", "unknown")).strip() or "unknown"
        approval = str(policy.get("approval_mode", "unknown")).strip() or "unknown"
        summary = _trim_text(policy.get("summary", "") or tool.get("description", ""), limit=140)
        lines.append(f"{name} [{risk}/{approval}] - {summary}")
    return "\n".join(lines)


def execute_slash_command(
    input_text: str,
    *,
    controller: Any,
    session_id: str = "",
    state_scope_id: str = "",
) -> Dict[str, Any]:
    commands = list_slash_commands()
    parsed = _parse_slash_command_input(input_text)
    if not parsed:
        return {"kind": "none"}

    if not parsed.get("query") and not parsed.get("args"):
        return {
            "kind": "activity",
            "title": "Slash commands",
            "detail": _slash_command_help_text(commands),
            "tone": "info",
            "clear_draft": False,
        }

    command = _find_slash_command(parsed.get("query", ""), commands)
    if command is None:
        query = str(parsed.get("query", "")).strip()
        return {
            "kind": "activity",
            "title": "Unknown command",
            "detail": f"No slash command matches /{query}." if query else "Type a command name after /.",
            "tone": "warning",
            "clear_draft": False,
        }

    command_type = str(command.get("type", "")).strip()
    if command_type == "prompt":
        prompt_text = _resolve_prompt_command(command, parsed.get("args", ""))
        if not prompt_text:
            return {
                "kind": "activity",
                "title": "Command unavailable",
                "detail": f"/{str(command.get('name', '')).strip()} is missing prompt text.",
                "tone": "warning",
                "clear_draft": False,
            }
        return {
            "kind": "prompt",
            "prompt_text": prompt_text,
            "success_message": f"Sent the {str(command.get('name', '')).strip()} prompt.",
            "clear_draft": True,
        }

    action = str(command.get("action", "")).strip()
    if action == "help":
        return {
            "kind": "activity",
            "title": "Slash commands",
            "detail": _slash_command_help_text(commands),
            "tone": "info",
            "clear_draft": True,
        }
    if action == "show-skills":
        return {
            "kind": "activity",
            "title": "Available skills",
            "detail": _format_skill_catalog_detail(),
            "tone": "info",
            "clear_draft": True,
        }
    if action == "show-runtime":
        return {
            "kind": "activity",
            "title": "Runtime config",
            "detail": _format_runtime_detail(controller),
            "tone": "info",
            "clear_draft": True,
        }
    if action == "show-tools":
        return {
            "kind": "activity",
            "title": "Registered tools",
            "detail": _format_tool_catalog_detail(controller),
            "tone": "info",
            "clear_draft": True,
        }
    if action == "approve":
        return {
            "kind": "operator_request",
            "action": action,
            "clear_draft": True,
        }
    if action == "reject":
        return {
            "kind": "operator_request",
            "action": action,
            "clear_draft": True,
        }

    return {
        "kind": "client_action",
        "action": action,
        "args": str(parsed.get("args", "")).strip(),
        "clear_draft": True,
    }
