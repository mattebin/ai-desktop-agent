from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

from core.capability_profiles import SANDBOXED_FULL_ACCESS_LAB_PROFILE
from tools.desktop_backends import run_bounded_command


DEFAULT_LAB_SHELL_ROOT = "data/lab_shell"
DEFAULT_LAB_SHELL_TIMEOUT_SECONDS = 10
LAB_WORKSPACE_VERSION = 1
SUPPORTED_LAB_SHELLS = {"powershell", "cmd"}
LAB_APPROVAL_REQUIRED = "approval_required"
LAB_ALLOWED = "allow"
LAB_BLOCKED = "block"

_HOST_PATH_PATTERN = re.compile(
    r"(?i)(?:[a-z]:\\|\\\\|hklm:|hkcu:|registry::|%userprofile%|%appdata%|%localappdata%|%programdata%|%windir%|%systemroot%|\$env:(?:userprofile|appdata|localappdata|programdata|windir|systemroot|temp|tmp))"
)
_SHORT_PATH_PATTERN = re.compile(r"(?i)\b[A-Z][A-Z0-9]{0,5}~[0-9]\b")
_DOTNET_DOWNLOAD_PATTERN = re.compile(
    r"(?i)(?:net\.webclient|net\.webrequest|httpwebrequest|httpclient|webclient)\b.*(?:download|upload|send|get(?:response|string)|openread)"
)
_DANGEROUS_UTILITY_PATTERN = re.compile(
    r"(?i)\b(?:certutil|bitsadmin)\b.*(?:-urlcache|-f\b|/transfer)"
)
_PARENT_TRAVERSAL_PATTERN = re.compile(r"(?<![A-Za-z0-9_])\.\.(?![A-Za-z0-9_])")
_CHAINED_COMMAND_PATTERN = re.compile(r"(?:&&|\|\||;)")
_NESTED_EXECUTION_PATTERN = re.compile(
    r"(?i)\b(?:powershell(?:\.exe)?|pwsh(?:\.exe)?|cmd(?:\.exe)?|bash(?:\.exe)?|wscript(?:\.exe)?|cscript(?:\.exe)?|mshta(?:\.exe)?|rundll32(?:\.exe)?|regsvr32(?:\.exe)?|invoke-command|start-process)\b"
)
_ENCODED_EXECUTION_PATTERN = re.compile(r"(?i)(?:-encodedcommand|-enc\b|frombase64string|invoke-expression|\biex\b)")

_AUTO_INSPECTION_PATTERNS = (
    re.compile(r"(?i)^\s*(?:dir|tree)(?:\s+[^\r\n]+)?\s*$"),
    re.compile(r"(?i)^\s*(?:cd|chdir)\s*$"),
    re.compile(r"(?i)^\s*(?:type|more|findstr)\b.*$"),
    re.compile(r"(?i)^\s*(?:ver|set)\s*$"),
    re.compile(r"(?i)^\s*(?:get-childitem|ls|dir|pwd|get-location|get-item|test-path|get-content|type|cat|select-string|resolve-path|get-process|get-date)(?:\b.*)?$"),
)
_AUTO_DIRECTORY_PATTERNS = (
    re.compile(r"(?i)^\s*(?:mkdir|md)\s+[^\r\n]+$"),
    re.compile(r"(?i)^\s*new-item\b.*-itemtype\s+directory\b.*$"),
)
_APPROVAL_MUTATION_PATTERNS = (
    re.compile(r"(?i)^\s*(?:copy|move|ren|rename)\b.*$"),
    re.compile(r"(?i)^\s*(?:copy-item|move-item|rename-item|set-content|add-content|out-file)\b.*$"),
    re.compile(r"(?i)^\s*new-item\b.*-itemtype\s+(?:file|directory)\b.*$"),
    re.compile(r"(?i)^\s*echo\s+.+(?:>>|>)\s*.+$"),
)
_SIMPLE_DELETE_PATTERNS = (
    re.compile(r"(?i)^\s*(?:del|erase)\s+[^\r\n]+$"),
    re.compile(r"(?i)^\s*remove-item\b(?!.*-recurse)(?!.*-force).*$"),
)

_BLOCK_CATEGORY_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "destructive_filesystem_wipe": (
        re.compile(r"(?i)\b(?:rm|remove-item)\b.*(?:-recurse|-force)"),
        re.compile(r"(?i)\bget-childitem\b.*(?:-recurse|\brecurse\b).*\|\s*.*\b(?:rm|remove-item|del|erase)\b"),
        re.compile(r"(?i)\b(?:del|erase)\b.*(?:/s|/f)"),
        re.compile(r"(?i)\b(?:rd|rmdir)\b.*(?:/s|/q)"),
        re.compile(r"(?i)\b(?:format|cipher\s+/w|diskpart|clear-disk|vssadmin\s+delete)\b"),
    ),
    "credential_theft_or_exfiltration": (
        re.compile(r"(?i)\b(?:invoke-webrequest|invoke-restmethod|curl|wget|bitsadmin|ftp|scp|sftp|net\s+use)\b"),
        re.compile(r"(?i)\b(?:mimikatz|lsass|sam|securityaccountmanager|browser[-_\s]?credentials?|cookies?)\b"),
        re.compile(r"(?i)\b(?:password|secret|token|credential)\b.*\b(?:export|send|upload|post|copy)\b"),
        re.compile(r"(?i)(?:net\.webclient|net\.webrequest|httpwebrequest|httpclient|webclient)\b.*(?:download|upload|send|getresponse|getstring|openread)"),
        re.compile(r"(?i)\b(?:certutil)\b.*(?:-urlcache|-f\b)"),
    ),
    "security_control_disabling": (
        re.compile(r"(?i)\b(?:set-mppreference|disable[-_\s]?realtime|disable[-_\s]?behavior|netsh\s+advfirewall|sc\s+(?:stop|config)\s+windefend|set-service\b.*disabled)\b"),
    ),
    "persistence_boot_registry_damage": (
        re.compile(r"(?i)\b(?:reg\s+(?:add|delete)|schtasks|bcdedit|new-service|sc\s+create|register-scheduledtask)\b"),
    ),
    "ransomware_like_mass_rewrite": (
        re.compile(r"(?i)get-childitem\b.*\brecurse\b.*\|\s*.*\b(?:set-content|add-content|out-file|rename-item)\b"),
        re.compile(r"(?i)\b(?:encrypt|protect-cmsmessage)\b"),
    ),
    "destructive_process_control": (
        re.compile(r"(?i)\btaskkill\b.*(?:/f|\b/im\b|\b/pid\b)"),
        re.compile(r"(?i)\bstop-process\b.*(?:-force|\b-id\b|\b-name\b|\b-processname\b)"),
        re.compile(r"(?i)\bwmic\b.*\bprocess\b.*\bdelete\b"),
    ),
    "machine_locking_or_resource_destruction": (
        re.compile(r"(?i)\bwhile\s*\(\s*\$?true\s*\)"),
        re.compile(r"(?i)\bfor\s*/l\b"),
        re.compile(r"(?i):\(\)\s*\{\s*:\|\:&\s*\};:"),
        re.compile(r"(?i)\bstart-job\b|\bstart-process\b.*\b(?:hidden|minimized)\b"),
    ),
}


def _trim_text(value: Any, limit: int = 320) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _normalize_shell_kind(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text not in SUPPORTED_LAB_SHELLS:
        return "powershell"
    return text


def _lab_root_path(settings: Dict[str, Any] | None = None) -> Path:
    effective_settings = settings if isinstance(settings, dict) else {}
    return Path(str(effective_settings.get("lab_shell_root", DEFAULT_LAB_SHELL_ROOT))).resolve()


def _sanitize_env(settings: Dict[str, Any] | None, workspace_root: Path) -> Dict[str, str]:
    import os

    allowed_keys = {
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "PATH",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "OS",
    }
    env: Dict[str, str] = {}
    for key, value in os.environ.items():
        upper_key = str(key or "").upper()
        if upper_key in allowed_keys:
            env[upper_key] = str(value)

    temp_root = workspace_root / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "TEMP": str(temp_root),
            "TMP": str(temp_root),
            "HOME": str(workspace_root),
            "USERPROFILE": str(workspace_root),
            "LAB_SHELL_WORKSPACE": str(workspace_root),
            "AI_OPERATOR_EXECUTION_PROFILE": SANDBOXED_FULL_ACCESS_LAB_PROFILE,
        }
    )

    extra_env = settings.get("lab_shell_env", {}) if isinstance(settings, dict) else {}
    if isinstance(extra_env, dict):
        for raw_key, raw_value in list(extra_env.items())[:20]:
            key = str(raw_key or "").strip()
            if not key:
                continue
            env[key] = str(raw_value or "")
    return env


def _workspace_id(value: Any) -> str:
    text = re.sub(r"[^a-zA-Z0-9._-]", "", str(value or "").strip())
    return text[:80]


def ensure_lab_workspace(
    *,
    settings: Dict[str, Any] | None = None,
    workspace_id: str = "",
) -> Dict[str, Any]:
    root = _lab_root_path(settings)
    lab_id = _workspace_id(workspace_id) or f"lab-{uuid4().hex[:10]}"
    workspace_root = root / "workspaces" / lab_id
    cwd = workspace_root / "cwd"
    temp_root = workspace_root / "temp"
    workspace_root.mkdir(parents=True, exist_ok=True)
    cwd.mkdir(parents=True, exist_ok=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    metadata = {
        "version": LAB_WORKSPACE_VERSION,
        "workspace_id": lab_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "cwd": str(cwd),
        "temp": str(temp_root),
        "sandbox": "disposable_workspace",
        "constraints": [
            "cwd pinned to disposable lab workspace",
            "temp and userprofile redirected into lab workspace",
            "commands blocked if they reference host paths, registry hives, or dangerous execution patterns",
            "network isolation is not VM-enforced in this phase",
        ],
    }
    metadata_path = workspace_root / "workspace.json"
    try:
        metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return {
        "workspace_id": lab_id,
        "root": str(workspace_root),
        "cwd": str(cwd),
        "temp": str(temp_root),
        "metadata_path": str(metadata_path),
        "sandbox": "disposable_workspace",
        "isolation_level": "workspace_only",
        "network_isolation": "not_enforced",
        "environment_sanitized": True,
    }


def _path_scope_reasons(command_text: str) -> List[str]:
    reasons: List[str] = []
    if _HOST_PATH_PATTERN.search(command_text):
        reasons.append("Command references host paths, registry hives, or environment shortcuts outside the disposable lab workspace.")
    if _PARENT_TRAVERSAL_PATTERN.search(command_text):
        reasons.append("Command attempts parent-directory traversal outside the disposable lab workspace.")
    if _SHORT_PATH_PATTERN.search(command_text):
        reasons.append("Command contains a Windows 8.3 short path name that could alias a host directory.")
    return reasons


def _catastrophic_hits(command_text: str) -> Dict[str, List[str]]:
    hits: Dict[str, List[str]] = {}
    for category, patterns in _BLOCK_CATEGORY_PATTERNS.items():
        matched: List[str] = []
        for pattern in patterns:
            if pattern.search(command_text):
                matched.append(pattern.pattern)
        if matched:
            hits[category] = matched
    return hits


def classify_lab_command(
    command: str,
    *,
    shell_kind: str = "powershell",
    settings: Dict[str, Any] | None = None,
    workspace_id: str = "",
) -> Dict[str, Any]:
    raw_command = str(command or "")
    normalized_command = " ".join(raw_command.split())
    normalized_shell = _normalize_shell_kind(shell_kind)
    workspace = ensure_lab_workspace(settings=settings, workspace_id=workspace_id)
    reasons: List[str] = []
    warnings: List[str] = []
    blocked_categories: List[str] = []
    category_hits = _catastrophic_hits(normalized_command)
    blocked_categories.extend(sorted(category_hits.keys()))
    lowered_command = normalized_command.lower()

    if "|" in normalized_command:
        pipe_rhs = normalized_command.split("|", 1)[1].strip().lower() if "|" in normalized_command else ""
        mutation_tokens = ("remove-item", "del", "erase", "set-content", "add-content", "out-file", "rename-item", "taskkill", "stop-process")
        if any(pipe_rhs.startswith(tok) or f" {tok}" in lowered_command for tok in mutation_tokens):
            blocked_categories.append("indirect_mutation_pipeline")
            reasons.append("Indirect shell pipelines that mix inspection with mutation are blocked in lab mode.")

    if not normalized_command:
        reasons.append("A command is required before lab execution can be evaluated.")
        return {
            "decision": LAB_BLOCKED,
            "risk_level": "high",
            "intent": "invalid_input",
            "summary": "Blocked empty lab command.",
            "reasons": reasons,
            "warnings": warnings,
            "blocked_categories": blocked_categories,
            "shell_kind": normalized_shell,
            "normalized_command": normalized_command,
            "workspace": workspace,
        }

    if len(raw_command) > 1200:
        blocked_categories.append("oversized_command")
        reasons.append("Command text is too large for safe experimental review.")

    if _CHAINED_COMMAND_PATTERN.search(normalized_command):
        blocked_categories.append("chained_execution")
        reasons.append("Chained command execution is blocked in lab mode so one review maps to one bounded action.")

    if _ENCODED_EXECUTION_PATTERN.search(normalized_command):
        blocked_categories.append("encoded_or_dynamic_execution")
        reasons.append("Encoded or dynamically evaluated command content is blocked.")

    nested_match = _NESTED_EXECUTION_PATTERN.search(normalized_command)
    if nested_match:
        matched = nested_match.group(0).lower()
        if matched not in {normalized_shell, f"{normalized_shell}.exe"}:
            blocked_categories.append("nested_shell_or_launcher")
            reasons.append("Nested shells, process launchers, and indirect execution helpers are blocked.")

    path_scope_reasons = _path_scope_reasons(normalized_command)
    if path_scope_reasons:
        blocked_categories.append("host_scope_reference")
        reasons.extend(path_scope_reasons)

    if category_hits:
        for category in sorted(category_hits):
            reasons.append(f"Blocked catastrophic category detected: {category.replace('_', ' ')}.")

    intent = "unknown"
    decision = LAB_APPROVAL_REQUIRED
    risk_level = "medium"

    if not reasons:
        if any(pattern.search(normalized_command) for pattern in _AUTO_INSPECTION_PATTERNS):
            intent = "inspection"
            decision = LAB_ALLOWED
            risk_level = "low"
            reasons.append("Read-only inspection inside the disposable lab workspace can run automatically.")
        elif any(pattern.search(normalized_command) for pattern in _AUTO_DIRECTORY_PATTERNS):
            intent = "lab_directory_mutation"
            decision = LAB_ALLOWED
            risk_level = "low"
            reasons.append("Creating a directory inside the disposable lab workspace is allowed automatically.")
        elif any(pattern.search(normalized_command) for pattern in _APPROVAL_MUTATION_PATTERNS):
            intent = "lab_file_mutation"
            decision = LAB_APPROVAL_REQUIRED
            risk_level = "medium"
            reasons.append("Mutable file operations inside the disposable lab workspace require explicit approval.")
        elif any(pattern.search(normalized_command) for pattern in _SIMPLE_DELETE_PATTERNS):
            intent = "lab_delete"
            decision = LAB_APPROVAL_REQUIRED
            risk_level = "high"
            reasons.append("Delete operations are not catastrophic here, but they still require explicit review.")
        else:
            warnings.append("The command did not match a trusted read-only or low-risk lab pattern.")
            reasons.append("Uncertain shell intent fails closed and requires explicit review.")

    if decision != LAB_BLOCKED and blocked_categories:
        decision = LAB_BLOCKED
        risk_level = "high"

    if decision == LAB_ALLOWED and warnings:
        risk_level = "medium"

    summary = {
        LAB_ALLOWED: "Allowed inside the disposable lab workspace.",
        LAB_APPROVAL_REQUIRED: "Requires explicit review before running inside the disposable lab workspace.",
        LAB_BLOCKED: "Blocked by the catastrophic-action prevention policy.",
    }[decision]

    return {
        "decision": decision,
        "risk_level": risk_level,
        "intent": intent,
        "summary": summary,
        "reasons": reasons,
        "warnings": warnings,
        "blocked_categories": sorted(set(blocked_categories)),
        "shell_kind": normalized_shell,
        "normalized_command": normalized_command,
        "workspace": workspace,
    }


def execute_lab_command(
    command: str,
    *,
    shell_kind: str = "powershell",
    approval_status: str = "",
    settings: Dict[str, Any] | None = None,
    workspace_id: str = "",
) -> Dict[str, Any]:
    effective_settings = settings if isinstance(settings, dict) else {}
    classification = classify_lab_command(
        command,
        shell_kind=shell_kind,
        settings=effective_settings,
        workspace_id=workspace_id,
    )
    workspace = classification.get("workspace", {}) if isinstance(classification.get("workspace", {}), dict) else {}
    normalized_command = str(classification.get("normalized_command", "")).strip()
    normalized_shell = str(classification.get("shell_kind", "powershell")).strip() or "powershell"
    decision = str(classification.get("decision", LAB_BLOCKED)).strip()
    risk_level = str(classification.get("risk_level", "high")).strip()
    approval_value = str(approval_status or "").strip().lower()
    environment = {
        "workspace_id": str(workspace.get("workspace_id", "")).strip(),
        "lab_root": str(workspace.get("root", "")).strip(),
        "cwd": str(workspace.get("cwd", "")).strip(),
        "temp": str(workspace.get("temp", "")).strip(),
        "sandbox": str(workspace.get("sandbox", "disposable_workspace")).strip(),
        "isolation_level": str(workspace.get("isolation_level", "workspace_only")).strip(),
        "network_isolation": str(workspace.get("network_isolation", "not_enforced")).strip(),
        "environment_sanitized": bool(workspace.get("environment_sanitized", True)),
        "profile": SANDBOXED_FULL_ACCESS_LAB_PROFILE,
    }
    result = {
        "ok": False,
        "tool": "lab_run_shell",
        "kind": "lab_shell_result",
        "experimental": True,
        "profile": SANDBOXED_FULL_ACCESS_LAB_PROFILE,
        "shell_kind": normalized_shell,
        "command": _trim_text(command, limit=1200),
        "proposed_command": _trim_text(command, limit=1200),
        "normalized_command": normalized_command,
        "plan_summary": str(classification.get("summary", "")).strip(),
        "policy": {
            "decision": decision,
            "risk_level": risk_level,
            "intent": str(classification.get("intent", "unknown")).strip(),
            "summary": str(classification.get("summary", "")).strip(),
            "reasons": list(classification.get("reasons", [])),
            "warnings": list(classification.get("warnings", [])),
            "blocked_categories": list(classification.get("blocked_categories", [])),
        },
        "environment": environment,
        "approval_required": False,
        "approval_status": approval_value or "",
        "paused": False,
        "blocked": False,
        "summary": "",
    }

    if decision == LAB_BLOCKED:
        message = "Blocked the experimental shell command before execution."
        result.update(
            {
                "blocked": True,
                "reason": "policy_blocked",
                "error": message,
                "message": message,
                "summary": f"{message} {str(classification.get('summary', '')).strip()}",
            }
        )
        return result

    if decision == LAB_APPROVAL_REQUIRED and approval_value != "approved":
        reason = "Explicit approval is required before this mutable or uncertain lab command can run."
        result.update(
            {
                "approval_required": True,
                "approval_status": "not approved",
                "paused": True,
                "reason": "approval_needed",
                "message": reason,
                "summary": reason,
                "checkpoint_required": True,
                "checkpoint_reason": reason,
                "checkpoint_tool": "lab_run_shell",
                "checkpoint_target": environment.get("lab_root", ""),
                "checkpoint_resume_args": {
                    "command": command,
                    "shell_kind": normalized_shell,
                    "approval_status": "approved",
                    "workspace_id": environment.get("workspace_id", ""),
                },
            }
        )
        return result

    timeout_seconds = max(
        1.0,
        min(
            20.0,
            float(effective_settings.get("lab_shell_timeout_seconds", DEFAULT_LAB_SHELL_TIMEOUT_SECONDS) or DEFAULT_LAB_SHELL_TIMEOUT_SECONDS),
        ),
    )
    workspace_root = Path(str(environment.get("lab_root", "")).strip())
    pre_snapshot = _snapshot_workspace(workspace_root) if workspace_root.is_dir() else {}
    backend_result = run_bounded_command(
        command=command,
        cwd=str(environment.get("cwd", "")).strip(),
        env=_sanitize_env(effective_settings, Path(str(environment.get("lab_root", "")).strip())),
        timeout_seconds=timeout_seconds,
        shell_kind=normalized_shell,
    )
    backend_data = backend_result.get("data", {}) if isinstance(backend_result.get("data", {}), dict) else {}
    ok = bool(backend_result.get("ok", False))
    message = str(backend_result.get("message", "")).strip() or ("Lab command completed." if ok else "Lab command failed.")
    stdout_excerpt = _trim_text(backend_data.get("stdout_excerpt", ""), limit=1200)
    stderr_excerpt = _trim_text(backend_data.get("stderr_excerpt", ""), limit=1200)
    raw_exit_code = backend_data.get("exit_code", -1)
    try:
        exit_code = int(raw_exit_code)
    except (TypeError, ValueError):
        exit_code = -1
    summary = f"Executed {normalized_shell} in the disposable lab workspace with exit code {exit_code}."
    if not ok and stderr_excerpt:
        summary = f"{summary} stderr: {stderr_excerpt}"
    elif ok and stdout_excerpt:
        summary = f"{summary} stdout: {stdout_excerpt}"

    result.update(
        {
            "ok": ok,
            "blocked": False,
            "approval_required": False,
            "approval_status": "approved" if approval_value == "approved" or decision == LAB_ALLOWED else approval_value,
            "paused": False,
            "reason": str(backend_result.get("reason", "command_executed")).strip() or "command_executed",
            "message": message,
            "summary": _trim_text(summary, limit=320),
            "exit_code": exit_code,
            "timed_out": bool(backend_data.get("timed_out", False)),
            "timeout_seconds": int(timeout_seconds),
            "duration_ms": int(backend_data.get("duration_ms", 0) or 0),
            "stdout_excerpt": stdout_excerpt,
            "stderr_excerpt": stderr_excerpt,
        }
    )
    post_snapshot = _snapshot_workspace(workspace_root) if workspace_root.is_dir() else {}
    if pre_snapshot or post_snapshot:
        result["workspace_audit"] = audit_workspace_changes(pre_snapshot, post_snapshot)
    return result


def _snapshot_workspace(workspace_root: Path) -> Dict[str, str]:
    """Capture a hash-map of every file in the workspace for diffing."""
    import hashlib

    snapshot: Dict[str, str] = {}
    try:
        for item in workspace_root.rglob("*"):
            if item.is_file():
                rel = str(item.relative_to(workspace_root))
                try:
                    snapshot[rel] = hashlib.sha256(item.read_bytes()).hexdigest()[:16]
                except Exception:
                    snapshot[rel] = "unreadable"
    except Exception:
        pass
    return snapshot


def audit_workspace_changes(before: Dict[str, str], after: Dict[str, str]) -> Dict[str, Any]:
    """Compare workspace snapshots and classify changes."""
    created = sorted(set(after) - set(before))
    deleted = sorted(set(before) - set(after))
    modified = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    return {
        "created": created,
        "deleted": deleted,
        "modified": modified,
        "total_changes": len(created) + len(deleted) + len(modified),
        "workspace_only": True,
    }


def lab_status_snapshot(*, settings: Dict[str, Any] | None = None, armed: bool = False) -> Dict[str, Any]:
    effective_settings = settings if isinstance(settings, dict) else {}
    root = _lab_root_path(effective_settings)
    return {
        "profile": SANDBOXED_FULL_ACCESS_LAB_PROFILE,
        "experimental": True,
        "armed": bool(armed),
        "sandbox": "disposable_workspace",
        "isolation_level": "workspace_only",
        "network_isolation": "not_enforced",
        "root": str(root),
        "supported_shells": ["powershell", "cmd"],
        "constraints": [
            "blocked if a command references host paths, registry hives, or dangerous launch patterns",
            "mutable or uncertain commands require approval",
            "catastrophic categories are hard-blocked",
        ],
    }
