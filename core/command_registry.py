from __future__ import annotations

from typing import Any, Dict, List

from core.extension_registry import list_extension_catalog, list_extension_commands
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
            "name": "extensions",
            "aliases": ["plugins"],
            "description": "Show the local extension manifests and the commands they add.",
            "action": "show-extensions",
            "category": "catalog",
            "source": "built_in",
        },
        {
            "type": "local",
            "name": "gmail-connect",
            "aliases": ["email-connect"],
            "description": "Run the Gmail OAuth connect flow using the configured Desktop client secret.",
            "action": "connect-gmail",
            "category": "email",
            "source": "built_in",
        },
        {
            "type": "local",
            "name": "email-status",
            "aliases": ["gmail-status"],
            "description": "Show the Gmail provider status, token state, and draft counts.",
            "action": "show-email-status",
            "category": "email",
            "source": "built_in",
        },
        {
            "type": "local",
            "name": "inbox",
            "description": "Show recent Gmail inbox threads from the connected account.",
            "action": "show-inbox",
            "category": "email",
            "source": "built_in",
        },
        {
            "type": "local",
            "name": "drafts",
            "aliases": ["email-drafts"],
            "description": "Show prepared Gmail drafts waiting for review or already sent.",
            "action": "show-email-drafts",
            "category": "email",
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
    for extension_command in list_extension_commands():
        command_name = str(extension_command.get("name", "")).strip()
        if not command_name or command_name.lower() in existing_names:
            continue
        commands.append(dict(extension_command))
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


def _format_extension_catalog_detail() -> str:
    extensions = list_extension_catalog()
    if not extensions:
        return "No local extension manifests are loaded right now."
    lines: List[str] = []
    for extension in extensions:
        title = str(extension.get("title", "") or extension.get("slug", "extension")).strip()
        description = _trim_text(extension.get("description", ""), limit=120)
        commands = list(extension.get("commands", []))
        command_list = ", ".join(
            f"/{str(command.get('name', '')).strip()}"
            for command in commands[:6]
            if str(command.get("name", "")).strip()
        )
        if command_list:
            lines.append(f"{title} - {description or 'Local extension manifest.'}")
            lines.append(f"Commands: {command_list}")
        else:
            lines.append(f"{title} - {description or 'Local extension manifest.'}")
    return "\n".join(lines)


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


def _format_email_status_detail(controller: Any) -> str:
    status = controller.get_email_status() if controller is not None else {}
    lines = [
        f"Provider: {str(status.get('provider', 'gmail')).strip() or 'gmail'}",
        f"Enabled: {'yes' if bool(status.get('enabled', False)) else 'no'}",
        f"Configured: {'yes' if bool(status.get('configured', False)) else 'no'}",
        f"Authenticated: {'yes' if bool(status.get('authenticated', False)) else 'no'}",
        f"Token present: {'yes' if bool(status.get('token_present', False)) else 'no'}",
    ]
    if status.get("profile_email"):
        lines.append(f"Connected as: {str(status.get('profile_email', '')).strip()}")
    if status.get("dependency_error"):
        lines.append(f"Dependency: {str(status.get('dependency_error', '')).strip()}")
    if status.get("restricted_scope_notice"):
        lines.append(str(status.get("restricted_scope_notice", "")).strip())
    draft_counts = status.get("draft_counts", {}) if isinstance(status.get("draft_counts", {}), dict) else {}
    lines.append(
        "Drafts: "
        f"prepared={int(draft_counts.get('prepared', 0) or 0)}, "
        f"sent={int(draft_counts.get('sent', 0) or 0)}, "
        f"rejected={int(draft_counts.get('rejected', 0) or 0)}"
    )
    lines.append(f"Client secrets: {str(status.get('client_secrets_path', '')).strip() or 'missing'}")
    lines.append(f"Token path: {str(status.get('token_path', '')).strip() or 'missing'}")
    return "\n".join(lines)


def _format_inbox_detail(controller: Any) -> str:
    payload = controller.list_email_threads(limit=8, label_ids=["INBOX"]) if controller is not None else {}
    if not payload.get("ok", False):
        return str(payload.get("error", "Gmail inbox is unavailable right now.")).strip()
    items = list(payload.get("items", []))
    if not items:
        return "No inbox threads matched the current Gmail query."
    lines: List[str] = []
    for item in items[:8]:
        subject = str(item.get("subject", "")).strip() or "(no subject)"
        last_from = str(item.get("last_from", "")).strip() or "unknown sender"
        snippet = _trim_text(item.get("snippet", ""), limit=110)
        thread_id = str(item.get("thread_id", "")).strip()
        unread = " unread" if bool(item.get("unread", False)) else ""
        lines.append(f"{subject} - {last_from}{unread}")
        if snippet:
            lines.append(f"Thread {thread_id}: {snippet}")
    return "\n".join(lines)


def _format_email_drafts_detail(controller: Any) -> str:
    payload = controller.list_email_drafts(limit=12) if controller is not None else {}
    if not payload.get("ok", False):
        return str(payload.get("error", "Email drafts are unavailable right now.")).strip()
    items = list(payload.get("items", []))
    if not items:
        return "No Gmail drafts are stored locally right now."
    lines: List[str] = []
    for item in items[:12]:
        draft_id = str(item.get("draft_id", "")).strip()
        subject = str(item.get("subject", "")).strip() or "(no subject)"
        status = str(item.get("status", "")).strip() or "unknown"
        recipients = ", ".join(str(entry).strip() for entry in list(item.get("to", []))[:2] if str(entry).strip())
        summary = _trim_text(item.get("summary", ""), limit=100)
        lines.append(f"{subject} [{status}] - {recipients or 'no recipients'}")
        lines.append(f"Draft {draft_id}: {summary or 'Stored local Gmail draft.'}")
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
    if action == "show-extensions":
        return {
            "kind": "activity",
            "title": "Local extensions",
            "detail": _format_extension_catalog_detail(),
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
    if action == "connect-gmail":
        result = controller.connect_gmail() if controller is not None else {"ok": False, "error": "Controller unavailable."}
        if result.get("ok", False):
            return {
                "kind": "activity",
                "title": "Gmail connected",
                "detail": _format_email_status_detail(controller),
                "tone": "success",
                "clear_draft": True,
            }
        return {
            "kind": "activity",
            "title": "Gmail connect failed",
            "detail": str(result.get("error", "Unable to connect Gmail.")).strip(),
            "tone": "warning",
            "clear_draft": True,
        }
    if action == "show-email-status":
        return {
            "kind": "activity",
            "title": "Gmail status",
            "detail": _format_email_status_detail(controller),
            "tone": "info",
            "clear_draft": True,
        }
    if action == "show-inbox":
        return {
            "kind": "activity",
            "title": "Inbox",
            "detail": _format_inbox_detail(controller),
            "tone": "info",
            "clear_draft": True,
        }
    if action == "show-email-drafts":
        return {
            "kind": "activity",
            "title": "Email drafts",
            "detail": _format_email_drafts_detail(controller),
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
