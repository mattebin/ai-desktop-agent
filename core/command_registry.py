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
