from __future__ import annotations

from typing import Any, Dict

from core.capability_profiles import SANDBOXED_FULL_ACCESS_LAB_PROFILE, is_lab_profile
from core.config import load_settings
from core.lab_shell import execute_lab_command


def _trim_text(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _lab_access_blocked(command: str, shell_kind: str, *, profile: str, lab_armed: bool) -> Dict[str, Any]:
    message = "Experimental lab shell access is only available inside an armed sandboxed full access lab run."
    return {
        "ok": False,
        "blocked": True,
        "tool": "lab_run_shell",
        "kind": "lab_shell",
        "experimental": True,
        "profile": SANDBOXED_FULL_ACCESS_LAB_PROFILE,
        "shell_kind": shell_kind,
        "command": _trim_text(command, limit=1200),
        "proposed_command": _trim_text(command, limit=1200),
        "normalized_command": " ".join(str(command or "").split()),
        "plan_summary": message,
        "policy": {
            "decision": "block",
            "risk_level": "high",
            "intent": "profile_guard",
            "summary": message,
            "reasons": [
                "Lab shell commands are hidden and blocked outside the experimental sandboxed full access lab profile.",
                "The lab must be explicitly armed before the operator can plan or run a lab command.",
            ],
            "warnings": [],
            "blocked_categories": ["lab_profile_required"] if not is_lab_profile(profile) else ["lab_not_armed"],
        },
        "environment": {
            "profile": profile or "safe_bounded",
            "lab_armed": bool(lab_armed),
            "workspace_id": "",
            "lab_root": "",
            "cwd": "",
            "temp": "",
            "sandbox": "unavailable",
            "isolation_level": "not_entered",
            "network_isolation": "not_entered",
            "environment_sanitized": False,
        },
        "approval_required": False,
        "approval_status": "",
        "paused": False,
        "reason": "lab_profile_required" if not is_lab_profile(profile) else "lab_not_armed",
        "message": message,
        "summary": message,
        "checkpoint_required": False,
        "checkpoint_reason": "",
        "checkpoint_tool": "lab_run_shell",
        "checkpoint_target": "",
        "checkpoint_resume_args": {},
    }


def lab_run_shell(args: Dict[str, Any]) -> Dict[str, Any]:
    safe_args = dict(args) if isinstance(args, dict) else {}
    command = str(safe_args.get("command", "")).strip()
    shell_kind = str(safe_args.get("shell_kind", "powershell")).strip() or "powershell"
    approval_status = str(safe_args.get("approval_status", "")).strip()
    execution_profile = str(safe_args.get("execution_profile", "")).strip()
    lab_armed = bool(safe_args.get("lab_armed", False))
    if not is_lab_profile(execution_profile) or not lab_armed:
        return _lab_access_blocked(command, shell_kind, profile=execution_profile, lab_armed=lab_armed)

    workspace_id = str(safe_args.get("workspace_id", "")).strip()
    return execute_lab_command(
        command,
        shell_kind=shell_kind,
        approval_status=approval_status,
        settings=load_settings(),
        workspace_id=workspace_id,
    )


LAB_RUN_SHELL_TOOL = {
    "name": "lab_run_shell",
    "description": (
        "Run a command only inside the explicitly armed experimental sandboxed full access lab profile. "
        "This tool is fail-closed, uses the disposable lab workspace, blocks catastrophic categories, "
        "and pauses for approval on mutable or uncertain commands."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "shell_kind": {"type": "string"},
            "approval_status": {"type": "string"},
        },
        "required": ["command"],
    },
    "func": lab_run_shell,
}
