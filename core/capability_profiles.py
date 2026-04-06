from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


SAFE_BOUNDED_PROFILE = "safe_bounded"
SANDBOXED_FULL_ACCESS_LAB_PROFILE = "sandboxed_full_access_lab"
DEFAULT_EXECUTION_PROFILE = SAFE_BOUNDED_PROFILE
LAB_STATE_SCOPE_ID = "lab:console"


def normalize_execution_profile(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text == SANDBOXED_FULL_ACCESS_LAB_PROFILE:
        return SANDBOXED_FULL_ACCESS_LAB_PROFILE
    return SAFE_BOUNDED_PROFILE


def is_lab_profile(value: Any) -> bool:
    return normalize_execution_profile(value) == SANDBOXED_FULL_ACCESS_LAB_PROFILE


def lab_state_scope_id(session_id: str = "") -> str:
    text = str(session_id or "").strip()[:80]
    if not text:
        return LAB_STATE_SCOPE_ID
    return f"lab:{text}"


def profile_metadata(profile: str, *, settings: Dict[str, Any] | None = None) -> Dict[str, Any]:
    normalized = normalize_execution_profile(profile)
    effective_settings = settings if isinstance(settings, dict) else {}
    if normalized == SANDBOXED_FULL_ACCESS_LAB_PROFILE:
        return {
            "profile": normalized,
            "label": "Sandboxed full access lab",
            "experimental": True,
            "safe_by_default": False,
            "requires_explicit_entry": True,
            "approval_required_for_risky_actions": True,
            "lab_root": str(Path(effective_settings.get("lab_shell_root", "data/lab_shell")).resolve()),
            "notes": [
                "Commands execute only through the experimental lab lane.",
                "Policy decisions fail closed and preserve auditability.",
                "The current phase uses a disposable workspace and sanitized environment, not a VM-grade sandbox.",
            ],
        }
    return {
        "profile": SAFE_BOUNDED_PROFILE,
        "label": "Safe bounded",
        "experimental": False,
        "safe_by_default": True,
        "requires_explicit_entry": False,
        "approval_required_for_risky_actions": True,
        "notes": [
            "Use the normal bounded operator tools.",
            "Shell execution stays read-only and policy-constrained.",
        ],
    }
