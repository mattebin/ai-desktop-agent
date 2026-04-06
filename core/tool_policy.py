from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List


READ_ONLY_TOOLS = {
    "inspect_project",
    "compare_files",
    "read_file",
    "list_files",
    "search_files",
    "suggest_commands",
    "plan_patch",
    "draft_proposed_edits",
    "build_review_bundle",
    "browser_inspect_page",
    "browser_extract_text",
    "desktop_list_windows",
    "desktop_get_active_window",
    "desktop_focus_window",
    "desktop_inspect_window_state",
    "desktop_recover_window",
    "desktop_wait_for_window_ready",
    "desktop_capture_screenshot",
    "desktop_list_processes",
    "desktop_inspect_process",
    "email_list_threads",
    "email_read_thread",
}

CONDITIONAL_APPROVAL_TOOLS = {
    "browser_open_page",
    "browser_click",
    "browser_type",
    "browser_follow_link",
    "run_shell",
    "email_connect_gmail",
    "email_prepare_reply_draft",
    "email_prepare_forward_draft",
    "lab_run_shell",
}

EXPLICIT_APPROVAL_TOOLS = {
    "apply_approved_edits",
    "desktop_move_mouse",
    "desktop_hover_point",
    "desktop_click_mouse",
    "desktop_click_point",
    "desktop_scroll",
    "desktop_press_key",
    "desktop_press_key_sequence",
    "desktop_type_text",
    "desktop_start_process",
    "desktop_stop_process",
    "desktop_run_command",
    "email_send_draft",
}

FILE_MUTATION_TOOLS = {"apply_approved_edits"}
SHELL_HAZARD_TOOLS = {"run_shell", "desktop_run_command", "lab_run_shell"}
PROCESS_CONTROL_TOOLS = {"desktop_start_process", "desktop_stop_process"}
DESTRUCTIVE_SHELL_PATTERNS = (
    r"\brm\b",
    r"\bdel\b",
    r"\brmdir\b",
    r"\bformat\b",
    r"\bshutdown\b",
    r"\bremove-item\b",
    r"\bmove-item\b",
    r"\brename-item\b",
    r"\bgit\s+reset\s+--hard\b",
)


def _shell_hazard(command_text: Any) -> str:
    rendered = " ".join(str(command_text or "").strip().lower().split())
    if not rendered:
        return ""
    for pattern in DESTRUCTIVE_SHELL_PATTERNS:
        if re.search(pattern, rendered):
            return pattern.replace(r"\b", "").replace(r"\s+", " ").strip("\\")
    return ""


def classify_tool_risk(tool_name: str, args: Dict[str, Any] | None = None) -> Dict[str, Any]:
    name = str(tool_name or "").strip()
    if not name:
        return {
            "tool": "",
            "area": "unknown",
            "risk_level": "medium",
            "approval_mode": "conditional",
            "mutation_target": "unknown",
            "summary": "Unknown tools should be treated conservatively.",
            "planner_note": "Conservative policy: verify scope and require human review before risky actions.",
            "shell_hazard": "",
        }

    area = (
        "browser"
        if name.startswith("browser_")
        else "desktop"
        if name.startswith("desktop_")
        else "email"
        if name.startswith("email_")
        else "files"
        if name in FILE_MUTATION_TOOLS or name in READ_ONLY_TOOLS
        else "shell"
        if name in SHELL_HAZARD_TOOLS
        else "planning"
    )

    if name in EXPLICIT_APPROVAL_TOOLS:
        mutation_target = (
            "filesystem"
            if name in FILE_MUTATION_TOOLS
            else "email_remote_state"
            if name.startswith("email_")
            else "local_process"
            if name in PROCESS_CONTROL_TOOLS
            else "desktop_state"
            if name.startswith("desktop_")
            else "unknown"
        )
        summary = "Explicit approval is required before executing this bounded mutation."
        if name == "desktop_run_command":
            summary = "Explicit approval is required before running a bounded local command."
        if name == "email_send_draft":
            summary = "Explicit approval is required before sending the prepared email draft."
        return {
            "tool": name,
            "area": area,
            "risk_level": "high",
            "approval_mode": "explicit",
            "mutation_target": mutation_target,
            "summary": summary,
            "planner_note": "Approval required: stop for explicit human approval before executing this action.",
            "shell_hazard": _shell_hazard((args or {}).get("command", "")) if name in SHELL_HAZARD_TOOLS else "",
        }

    if name in CONDITIONAL_APPROVAL_TOOLS:
        summary = "This bounded action may proceed automatically, but risky transitions still require approval."
        if name == "run_shell":
            summary = "Use only for safe read-only inspection. Do not execute destructive or mutating shell commands."
        if name == "lab_run_shell":
            summary = (
                "Experimental lab-only shell lane. Commands stay in a disposable workspace, use layered policy checks, "
                "block catastrophic categories, and pause for explicit approval on mutable or uncertain actions."
            )
        if name == "email_connect_gmail":
            summary = "This opens the Google OAuth sign-in flow for Gmail and stores local credentials."
        if name in {"email_prepare_reply_draft", "email_prepare_forward_draft"}:
            summary = "This prepares a local email draft for later review without sending anything."
        return {
            "tool": name,
            "area": area,
            "risk_level": "medium",
            "approval_mode": "conditional",
            "mutation_target": (
                "remote_state"
                if name.startswith("browser_")
                else "email_auth"
                if name == "email_connect_gmail"
                else "local_draft"
                if name.startswith("email_")
                else "lab_workspace"
                if name == "lab_run_shell"
                else "local_shell"
            ),
            "summary": summary,
            "planner_note": (
                "Conditional approval: continue only inside the safe policy and pause before risky transitions."
                if name != "lab_run_shell"
                else "Experimental lab tool: use only in sandboxed_full_access_lab mode, keep execution inside the disposable lab workspace, and never bypass a block."
            ),
            "shell_hazard": _shell_hazard((args or {}).get("command", "")) if name in SHELL_HAZARD_TOOLS else "",
        }

    if name in READ_ONLY_TOOLS:
        return {
            "tool": name,
            "area": area,
            "risk_level": "low",
            "approval_mode": "auto",
            "mutation_target": "none",
            "summary": "Read-only inspection and analysis can proceed automatically inside the current scope.",
            "planner_note": "Read-only tool: safe to use for bounded inspection and analysis.",
            "shell_hazard": "",
        }

    return {
        "tool": name,
        "area": area,
        "risk_level": "medium",
        "approval_mode": "conditional",
        "mutation_target": "unknown",
        "summary": "Treat this tool conservatively and pause if the action could change state.",
        "planner_note": "Conservative policy: verify the target and stop for approval before any risky mutation.",
        "shell_hazard": "",
    }


def build_tool_policy_snapshot(tool_names: Iterable[str]) -> Dict[str, Any]:
    read_only: List[str] = []
    conditional: List[str] = []
    explicit: List[str] = []
    shell_hazard_tools: List[str] = []
    file_mutation_tools: List[str] = []

    for raw_name in tool_names:
        name = str(raw_name or "").strip()
        if not name:
            continue
        policy = classify_tool_risk(name)
        approval_mode = str(policy.get("approval_mode", "")).strip()
        if approval_mode == "auto":
            read_only.append(name)
        elif approval_mode == "explicit":
            explicit.append(name)
        else:
            conditional.append(name)
        if name in SHELL_HAZARD_TOOLS:
            shell_hazard_tools.append(name)
        if name in FILE_MUTATION_TOOLS:
            file_mutation_tools.append(name)

    return {
        "summary": (
            "Read-only inspection may proceed automatically. Browser transitions and shell work stay conditional. "
            "Desktop mutations, process control, command execution, file edits, and email sending require explicit approval. "
            "Experimental lab shell access remains separately gated and fail-closed."
        ),
        "read_only_tools": read_only,
        "conditional_approval_tools": conditional,
        "explicit_approval_tools": explicit,
        "shell_hazard_tools": shell_hazard_tools,
        "file_mutation_tools": file_mutation_tools,
        "notes": [
            "Conditional tools must still pause before risky remote-state changes or uncertain shell actions.",
            "Explicit-approval tools should not execute until the current task clearly carries approval.",
        ],
    }
