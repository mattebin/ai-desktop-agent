from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from tools.desktop_backends import run_bounded_command


BLOCKED_COMMAND_TERMS = [
    "shutdown",
    "restart-computer",
    "stop-computer",
    "remove-item",
    "del ",
    "erase ",
    "format ",
]
SAFE_SHELL_CHAIN_PATTERN = re.compile(r"(?:&&|\|\||;)")
SAFE_SHELL_HOST_PATH_PATTERN = re.compile(r"(?i)(?:[a-z]:\\|\\\\|%userprofile%|%appdata%|\$env:(?:userprofile|appdata|localappdata|programdata|windir|systemroot|temp|tmp))")
SAFE_SHELL_NESTED_PATTERN = re.compile(
    r"(?i)\b(?:powershell(?:\.exe)?|pwsh(?:\.exe)?|cmd(?:\.exe)?|bash(?:\.exe)?|wscript(?:\.exe)?|cscript(?:\.exe)?|mshta(?:\.exe)?|rundll32(?:\.exe)?|invoke-expression|\biex\b|start-process)\b"
)
SAFE_SHELL_READONLY_PATTERN = re.compile(
    r"(?i)^\s*(?:dir|tree|type|more|findstr|git\s+(?:status|diff|log|show)|get-childitem|ls|dir|pwd|get-location|get-item|test-path|get-content|type|cat|select-string|resolve-path|get-process|get-date)(?:\b.*)?$"
)

COMMAND_SUGGESTION_DEFAULT_MAX = 3
PATCH_PLAN_DEFAULT_MAX_FILES = 4
DRAFT_PROPOSED_EDITS_DEFAULT_MAX_FILES = 3
DRAFT_DIFF_PREVIEW_MAX_LINES = 12
DRAFT_FILE_READ_MAX_BYTES = 4000
REVIEW_BUNDLE_DEFAULT_MAX_FILES = 4
REVIEW_BUNDLE_DEFAULT_MAX_COMMANDS = 3
GOAL_TERM_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "be",
    "by",
    "command",
    "commands",
    "exact",
    "find",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "manual",
    "next",
    "of",
    "on",
    "or",
    "run",
    "safe",
    "suggest",
    "suggested",
    "the",
    "to",
    "verification",
    "verify",
    "what",
    "which",
    "with",
}
PATCH_PLAN_STOPWORDS = GOAL_TERM_STOPWORDS | {
    "change",
    "changes",
    "code",
    "edit",
    "edits",
    "file",
    "files",
    "fix",
    "implement",
    "implementation",
    "patch",
    "planned",
    "planning",
    "propose",
    "proposed",
    "refactor",
    "update",
}
COMPARE_GOAL_TERMS = {
    "break",
    "broke",
    "broken",
    "change",
    "changed",
    "changes",
    "compare",
    "comparison",
    "diff",
    "difference",
    "differences",
    "regression",
    "regressions",
}
PLAN_GOAL_TERMS = {
    "fix",
    "implement",
    "patch",
    "plan",
    "refactor",
    "update",
}
CORE_FILE_EDIT_HINTS = {
    "tools/shell.py": {
        "order": 10,
        "summary": "Add or refine the safe non-executing planning logic and output structure.",
    },
    "tools/files.py": {
        "order": 10,
        "summary": "Implement or adjust the underlying read-only tool behavior.",
    },
    "tools/registry.py": {
        "order": 20,
        "summary": "Register the planning-related tool so the agent can call it.",
    },
    "core/loop.py": {
        "order": 30,
        "summary": "Pass current goal, comparisons, and recent evidence into the planner defaults.",
    },
    "core/state.py": {
        "order": 40,
        "summary": "Track planned changes in task memory and final synthesis without treating them as executed.",
    },
    "core/llm_client.py": {
        "order": 50,
        "summary": "Update planner or final-answer prompts to surface the patch plan clearly.",
    },
}


def _coerce_int(value: Any, default: int, *, minimum: int = 1, maximum: int = 6) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default

    if parsed < minimum:
        parsed = minimum
    if parsed > maximum:
        parsed = maximum
    return parsed


def _normalize_text_list(values: Any, *, limit: int = 8, text_limit: int = 280) -> List[str]:
    if not isinstance(values, list):
        return []

    normalized: List[str] = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        if len(text) > text_limit:
            text = text[: text_limit - 3].rstrip() + "..."
        if text in normalized:
            continue
        normalized.append(text)
        if len(normalized) >= limit:
            break
    return normalized


def _normalize_dict_list(values: Any, *, limit: int = 6) -> List[Dict[str, Any]]:
    if not isinstance(values, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for value in values[:limit]:
        if isinstance(value, dict):
            normalized.append(value)
    return normalized


def _extract_goal_terms(text: str, limit: int = 4, stopwords: set[str] | None = None) -> List[str]:
    blocked_terms = stopwords or GOAL_TERM_STOPWORDS
    terms: List[str] = []
    for match in re.findall(r"[a-z0-9_]+", str(text).lower()):
        if len(match) < 2 or match in blocked_terms:
            continue
        if match in terms:
            continue
        terms.append(match)
        if len(terms) >= limit:
            break
    return terms


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _display_path(path: str) -> str:
    raw = str(path).strip()
    if not raw:
        return ""
    try:
        candidate = Path(raw)
        if candidate.is_absolute():
            try:
                return str(candidate.relative_to(Path.cwd())).replace("\\", "/")
            except ValueError:
                return raw
    except Exception:
        return raw
    return raw.replace("\\", "/")


def _build_suggestion(
    *,
    command: str,
    purpose: str,
    why_relevant: str,
    target_paths: List[str] | None = None,
    risk_level: str = "low",
) -> Dict[str, Any]:
    return {
        "shell": "powershell",
        "command": command,
        "purpose": purpose,
        "risk_level": risk_level,
        "why_relevant": why_relevant,
        "target_paths": [str(path).strip() for path in (target_paths or []) if str(path).strip()],
    }


def _dedupe_suggestions(suggestions: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    unique: List[Dict[str, Any]] = []
    seen_commands: set[str] = set()

    for suggestion in suggestions:
        command = str(suggestion.get("command", "")).strip()
        if not command or command in seen_commands:
            continue
        seen_commands.add(command)
        unique.append(suggestion)
        if len(unique) >= limit:
            break

    return unique


def _select_compare_candidates(
    path_a: str,
    path_b: str,
    goal: str,
    priority_files: List[str],
    known_files: List[str],
) -> tuple[str, str] | tuple[None, None]:
    if path_a and path_b:
        return path_a, path_b

    lowered_goal = goal.lower()
    if not any(term in lowered_goal for term in COMPARE_GOAL_TERMS):
        return None, None

    ranked_files = priority_files + [path for path in known_files if path not in priority_files]
    if len(ranked_files) < 2:
        return None, None

    return ranked_files[0], ranked_files[1]


def _build_patch_plan_summary(goal: str, files_to_change: List[Dict[str, Any]], confidence: str) -> str:
    count = len(files_to_change)
    if count == 0:
        return "No patch plan could be built from current evidence. No files were modified."

    first_paths = ", ".join(item.get("display_path", item.get("path", "")) for item in files_to_change[:2])
    goal_text = " ".join(goal.split())
    if len(goal_text) > 80:
        goal_text = goal_text[:77] + "..."

    if goal_text:
        return (
            f"Planned {count} file change(s) for '{goal_text}' with {confidence} confidence. "
            f"Start with {first_paths}. No files were modified."
        )
    return f"Planned {count} file change(s) with {confidence} confidence. No files were modified."


def _goal_style(goal: str) -> str:
    lowered = goal.lower()
    if any(term in lowered for term in {"fix", "bug", "broke", "broken", "regression"}):
        return "fix"
    if any(term in lowered for term in {"implement", "add", "support", "introduce"}):
        return "implement"
    if any(term in lowered for term in {"refactor", "cleanup", "restructure"}):
        return "refactor"
    return "update"


def _fallback_candidate_files(base_path: str) -> List[str]:
    if not base_path:
        return []

    root = Path(base_path)
    if not root.exists() or not root.is_dir():
        return []

    fallbacks = [
        root / "tools" / "shell.py",
        root / "tools" / "files.py",
        root / "tools" / "registry.py",
        root / "core" / "loop.py",
        root / "core" / "state.py",
        root / "core" / "llm_client.py",
    ]
    return [str(path) for path in fallbacks if path.exists() and path.is_file()]


def _score_patch_candidate(
    path: str,
    goal_terms: List[str],
    priority_files: List[str],
    compare_targets: set[str],
    command_targets: set[str],
    recent_notes: List[str],
) -> tuple[int, List[str]]:
    lowered_path = path.lower().replace("\\", "/")
    display_path = _display_path(path) or path
    score = 0
    reasons: List[str] = []

    if path in priority_files:
        score += 40
        reasons.append("current priority file")

    if path in compare_targets:
        score += 35
        reasons.append("recent comparison target")

    if path in command_targets:
        score += 18
        reasons.append("appears in a suggested verification command")

    goal_matches = [term for term in goal_terms if term in lowered_path]
    if goal_matches:
        score += 14 * min(2, len(goal_matches))
        reasons.append(f"matches goal terms: {', '.join(goal_matches[:2])}")

    file_hint = CORE_FILE_EDIT_HINTS.get(display_path)
    if file_hint:
        score += 12
        reasons.append("fits the current architecture change pattern")

    basename = Path(display_path).name.lower()
    for note in recent_notes:
        lowered_note = note.lower()
        if basename and basename in lowered_note:
            score += 10
            reasons.append("mentioned in recent evidence")
            break
        if display_path.lower() in lowered_note:
            score += 10
            reasons.append("mentioned in recent evidence")
            break

    if "/tests/" in lowered_path or lowered_path.endswith("/tests"):
        score -= 8

    return score, reasons[:3]


def _build_patch_edit_summary(path: str, goal: str, files_differ: Any, command_targets: set[str]) -> str:
    display_path = _display_path(path) or path
    path_key = display_path.lower()
    style = _goal_style(goal)
    goal_text = " ".join(goal.split())
    if len(goal_text) > 90:
        goal_text = goal_text[:87] + "..."

    if path_key in CORE_FILE_EDIT_HINTS:
        return CORE_FILE_EDIT_HINTS[path_key]["summary"]

    if path in command_targets and files_differ is True:
        return "Adjust this file based on the differences already identified and the manual verification steps suggested."
    if files_differ is True:
        return "Update this file to resolve the behavior difference already identified during comparison."
    if style == "fix":
        return f"Adjust this file to fix the issue around: {goal_text}."
    if style == "implement":
        return f"Add or extend logic in this file to support: {goal_text}."
    if style == "refactor":
        return f"Refactor this file to support the planned structure for: {goal_text}."
    return f"Update this file to support: {goal_text}."


def suggest_commands(args: Dict[str, Any]) -> Dict[str, Any]:
    goal = str(args.get("goal", "")).strip()
    base_path = str(args.get("base_path", "")).strip()
    path_a = str(args.get("path_a", "")).strip()
    path_b = str(args.get("path_b", "")).strip()
    max_suggestions = _coerce_int(
        args.get("max_suggestions", COMMAND_SUGGESTION_DEFAULT_MAX),
        COMMAND_SUGGESTION_DEFAULT_MAX,
        minimum=1,
        maximum=4,
    )

    priority_files = _normalize_text_list(args.get("priority_files", []), limit=6)
    known_files = _normalize_text_list(args.get("known_files", []), limit=10)
    known_dirs = _normalize_text_list(args.get("known_dirs", []), limit=6)
    recent_notes = _normalize_text_list(args.get("recent_notes", []), limit=6, text_limit=180)
    goal_terms = _extract_goal_terms(goal, limit=4)
    files_differ = args.get("files_differ")

    suggestions: List[Dict[str, Any]] = []

    compare_a, compare_b = _select_compare_candidates(path_a, path_b, goal, priority_files, known_files)
    if compare_a and compare_b:
        if files_differ is True:
            compare_reason = "Recent evidence already found differences between these files, so this lets you review the exact changed lines manually."
        elif files_differ is False:
            compare_reason = "Recent evidence compared these files already; this re-checks them directly in PowerShell."
        else:
            compare_reason = "The current goal is about differences or regressions, and these are the best current file candidates to compare."

        suggestions.append(
            _build_suggestion(
                command=(
                    f"Compare-Object (Get-Content -LiteralPath {_ps_quote(compare_a)}) "
                    f"(Get-Content -LiteralPath {_ps_quote(compare_b)}) | Select-Object -First 40"
                ),
                purpose=(
                    f"Review line-level differences between {_display_path(compare_a) or compare_a} "
                    f"and {_display_path(compare_b) or compare_b}."
                ),
                why_relevant=compare_reason,
                target_paths=[compare_a, compare_b],
            )
        )

    if priority_files:
        top_priority = priority_files[0]
        why_priority = "This file is currently the top priority file for the goal."
        if recent_notes:
            why_priority += " Recent notes point to it as relevant evidence."

        suggestions.append(
            _build_suggestion(
                command=f"Get-Content -LiteralPath {_ps_quote(top_priority)} -TotalCount 120",
                purpose=f"Inspect {_display_path(top_priority) or top_priority} directly.",
                why_relevant=why_priority,
                target_paths=[top_priority],
            )
        )

    search_root = base_path or (known_dirs[0] if known_dirs else str(Path.cwd()))
    search_terms = [term for term in goal_terms if term not in COMPARE_GOAL_TERMS][:2]
    if not search_terms:
        search_terms = goal_terms[:2]
    if search_terms:
        pattern = "|".join(search_terms)
        term_text = ", ".join(search_terms)
        suggestions.append(
            _build_suggestion(
                command=(
                    f"Get-ChildItem -LiteralPath {_ps_quote(search_root)} -Recurse -File | "
                    f"Select-String -Pattern {_ps_quote(pattern)} | Select-Object -First 40"
                ),
                purpose=(
                    f"Search {_display_path(search_root) or search_root} for goal terms before reading more files."
                ),
                why_relevant=f"The goal mentions {term_text}, and current evidence suggests searching for those terms across the project.",
                target_paths=[search_root],
            )
        )

    if len(priority_files) > 1:
        second_priority = priority_files[1]
        suggestions.append(
            _build_suggestion(
                command=f"Get-Content -LiteralPath {_ps_quote(second_priority)} -TotalCount 120",
                purpose=f"Inspect the next likely file, {_display_path(second_priority) or second_priority}.",
                why_relevant="This is the next priority file if the first file does not fully answer the goal.",
                target_paths=[second_priority],
            )
        )

    if not suggestions:
        fallback_root = base_path or (known_dirs[0] if known_dirs else str(Path.cwd()))
        suggestions.append(
            _build_suggestion(
                command=f"Get-ChildItem -LiteralPath {_ps_quote(fallback_root)} -Force",
                purpose=f"List the current workspace at {_display_path(fallback_root) or fallback_root}.",
                why_relevant="No stronger file evidence was available, so start with a safe directory listing.",
                target_paths=[fallback_root],
            )
        )

    suggestions = _dedupe_suggestions(suggestions, max_suggestions)

    return {
        "ok": True,
        "executed": False,
        "goal": goal,
        "summary": (
            f"Suggested {len(suggestions)} read-only PowerShell command(s) based on the current goal and evidence. "
            "Commands were not run automatically."
        ),
        "suggestions": suggestions,
    }


def plan_patch(args: Dict[str, Any]) -> Dict[str, Any]:
    goal = str(args.get("goal", "")).strip()
    base_path = str(args.get("base_path", "")).strip()
    path_a = str(args.get("path_a", "")).strip()
    path_b = str(args.get("path_b", "")).strip()
    compare_summary = str(args.get("compare_summary", "")).strip()
    files_differ = args.get("files_differ")
    max_files_to_change = _coerce_int(
        args.get("max_files_to_change", PATCH_PLAN_DEFAULT_MAX_FILES),
        PATCH_PLAN_DEFAULT_MAX_FILES,
        minimum=1,
        maximum=5,
    )

    priority_files = _normalize_text_list(args.get("priority_files", []), limit=6)
    known_files = _normalize_text_list(args.get("known_files", []), limit=12)
    recent_notes = _normalize_text_list(args.get("recent_notes", []), limit=8, text_limit=180)
    suggested_commands = _normalize_dict_list(args.get("suggested_commands", []), limit=5)
    goal_terms = _extract_goal_terms(goal, limit=5, stopwords=PATCH_PLAN_STOPWORDS)

    command_targets: List[str] = []
    for suggestion in suggested_commands:
        command_targets.extend(_normalize_text_list(suggestion.get("target_paths", []), limit=3))

    compare_targets = [path for path in [path_a, path_b] if path]
    candidate_paths = priority_files + compare_targets + command_targets + known_files
    if not candidate_paths:
        candidate_paths = _fallback_candidate_files(base_path)

    unique_candidates: List[str] = []
    for path in candidate_paths:
        raw_path = str(path).strip()
        if not raw_path or raw_path in unique_candidates:
            continue
        try:
            candidate = Path(raw_path)
            if candidate.exists() and candidate.is_dir():
                continue
        except Exception:
            pass
        unique_candidates.append(raw_path)

    compare_target_set = set(compare_targets)
    command_target_set = set(command_targets)

    scored_candidates: List[Dict[str, Any]] = []
    for path in unique_candidates:
        score, reasons = _score_patch_candidate(
            path,
            goal_terms,
            priority_files,
            compare_target_set,
            command_target_set,
            recent_notes,
        )
        if score <= 0 and unique_candidates.index(path) >= max_files_to_change:
            continue
        display_path = _display_path(path) or path
        hint = CORE_FILE_EDIT_HINTS.get(display_path, {})
        scored_candidates.append(
            {
                "path": path,
                "display_path": display_path,
                "score": score,
                "order_weight": int(hint.get("order", 90)),
                "why": reasons,
            }
        )

    if not scored_candidates and base_path:
        for path in _fallback_candidate_files(base_path)[:max_files_to_change]:
            display_path = _display_path(path) or path
            hint = CORE_FILE_EDIT_HINTS.get(display_path, {})
            scored_candidates.append(
                {
                    "path": path,
                    "display_path": display_path,
                    "score": 1,
                    "order_weight": int(hint.get("order", 90)),
                    "why": ["fallback architecture file"],
                }
            )

    scored_candidates.sort(key=lambda item: (-item["score"], item["order_weight"], item["display_path"]))
    selected_candidates = scored_candidates[:max_files_to_change]
    selected_candidates.sort(key=lambda item: (item["order_weight"], -item["score"], item["display_path"]))

    files_to_change: List[Dict[str, Any]] = []
    for index, item in enumerate(selected_candidates, start=1):
        why_parts = list(item["why"])
        if compare_summary and item["path"] in compare_target_set:
            why_parts.append(compare_summary)

        deduped_why: List[str] = []
        for reason in why_parts:
            cleaned = str(reason).strip()
            if cleaned and cleaned not in deduped_why:
                deduped_why.append(cleaned)

        files_to_change.append(
            {
                "path": item["path"],
                "display_path": item["display_path"],
                "order": index,
                "why": "; ".join(deduped_why[:3]) or "relevant to the current goal",
                "proposed_edit_summary": _build_patch_edit_summary(
                    item["path"],
                    goal,
                    files_differ,
                    command_target_set,
                ),
            }
        )

    evidence_sources = 0
    if priority_files:
        evidence_sources += 1
    if compare_targets:
        evidence_sources += 1
    if suggested_commands:
        evidence_sources += 1
    if recent_notes:
        evidence_sources += 1

    if evidence_sources >= 3 and len(files_to_change) >= 2:
        confidence = "high"
    elif evidence_sources >= 2 and files_to_change:
        confidence = "moderate"
    else:
        confidence = "low"

    uncertainties: List[str] = []
    if not compare_targets and any(term in goal.lower() for term in COMPARE_GOAL_TERMS):
        uncertainties.append("No direct file comparison was available, so the file order is heuristic.")
    if not priority_files:
        uncertainties.append("Priority files were limited, so the plan leans on known files and architecture patterns.")
    if not suggested_commands:
        uncertainties.append("No suggested commands were available to confirm a manual verification path.")
    if len(scored_candidates) > max_files_to_change:
        uncertainties.append("Additional files may be needed after the first pass if the initial edits are not enough.")
    uncertainties = uncertainties[:3]

    return {
        "ok": True,
        "executed": False,
        "goal": goal,
        "summary": _build_patch_plan_summary(goal, files_to_change, confidence),
        "files_to_change": files_to_change,
        "recommended_order": [item["path"] for item in files_to_change],
        "confidence": confidence,
        "uncertainties": uncertainties,
    }



def _trim_text(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value).split())
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _normalize_planned_files(values: Any, *, limit: int = 6) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    seen_paths: set[str] = set()

    for value in _normalize_dict_list(values, limit=limit):
        path = str(value.get("path", "")).strip()
        if not path or path in seen_paths:
            continue
        seen_paths.add(path)
        normalized.append(
            {
                "path": path,
                "display_path": str(value.get("display_path", "")).strip() or _display_path(path) or path,
                "order": _coerce_int(value.get("order", len(normalized) + 1), len(normalized) + 1, minimum=1, maximum=9),
                "why": _trim_text(value.get("why", ""), limit=220),
                "proposed_edit_summary": _trim_text(value.get("proposed_edit_summary", ""), limit=220),
            }
        )

    return normalized


def _safe_read_text(path: Path, max_bytes: int = DRAFT_FILE_READ_MAX_BYTES) -> tuple[str | None, bool]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError:
        return None, False

    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]
    if b"\x00" in raw:
        return None, truncated
    return raw.decode("utf-8", errors="replace"), truncated


def _derive_feature_context(goal: str) -> Dict[str, str]:
    lowered = goal.lower()
    if any(
        term in lowered
        for term in {
            "proposed-edit",
            "proposed edit",
            "diff-draft",
            "draft diff",
            "draft preview",
            "reviewable edit",
            "reviewable edits",
        }
    ):
        return {
            "tool_name": "draft_proposed_edits",
            "tool_constant": "DRAFT_PROPOSED_EDITS_TOOL",
            "section_title": "Proposed Edits (Not Applied)",
        }
    if any(term in lowered for term in {"patch plan", "planned changes", "planned change", "implementation order"}):
        return {
            "tool_name": "plan_patch",
            "tool_constant": "PLAN_PATCH_TOOL",
            "section_title": "Planned Changes (Not Applied)",
        }
    if any(term in lowered for term in {"suggested command", "command suggestion", "suggest command", "safe command"}):
        return {
            "tool_name": "suggest_commands",
            "tool_constant": "SUGGEST_COMMANDS_TOOL",
            "section_title": "Suggested Commands (Not Run)",
        }

    goal_terms = _extract_goal_terms(goal, limit=3, stopwords=PATCH_PLAN_STOPWORDS)
    slug = "_".join(goal_terms) if goal_terms else "proposed_change"
    return {
        "tool_name": slug,
        "tool_constant": f"{slug.upper()}_TOOL",
        "section_title": "Proposed Changes (Not Applied)",
    }


def _build_draft_description(
    display_path: str,
    goal: str,
    feature_context: Dict[str, str],
    proposed_edit_summary: str,
) -> str:
    summary = _trim_text(proposed_edit_summary, limit=180) if proposed_edit_summary else ""
    tool_name = feature_context.get("tool_name", "proposed_change")
    tool_constant = feature_context.get("tool_constant", "PROPOSED_CHANGE_TOOL")
    section_title = feature_context.get("section_title", "Proposed Changes (Not Applied)")

    if display_path == "tools/shell.py":
        return f"Add the {tool_name} tool and bounded helper logic for safe draft previews."
    if display_path == "tools/registry.py":
        return f"Import and register {tool_constant} so the agent can call {tool_name}."
    if display_path == "core/loop.py":
        return f"Populate {tool_name} defaults from the latest goal evidence and planning results."
    if display_path == "core/state.py":
        return f"Track {section_title.lower()} in task memory and final synthesis without treating drafts as applied."
    if display_path == "core/llm_client.py":
        return f"Update planner and final-answer prompts to mention {tool_name} and keep drafts clearly not applied."
    if summary:
        return summary
    return _build_patch_edit_summary(display_path, goal, None, set())


def _clip_preview_lines(lines: List[str], limit: int = DRAFT_DIFF_PREVIEW_MAX_LINES) -> List[str]:
    clipped = [line.rstrip() for line in lines if str(line).strip()]
    if len(clipped) > limit:
        clipped = clipped[: limit - 1] + ["+..."]
    return clipped


def _build_role_specific_draft_preview(
    display_path: str,
    feature_context: Dict[str, str],
    description: str,
    reason_for_change: str,
) -> tuple[List[str], bool]:
    tool_name = feature_context.get("tool_name", "proposed_change")
    tool_constant = feature_context.get("tool_constant", "PROPOSED_CHANGE_TOOL")
    section_title = feature_context.get("section_title", "Proposed Changes (Not Applied)")

    if display_path == "tools/shell.py":
        return (
            _clip_preview_lines(
                [
                    f"--- {display_path}",
                    f"+++ {display_path} (draft)",
                    "@@",
                    f"+def {tool_name}(args: Dict[str, Any]) -> Dict[str, Any]:",
                    f"+    # Build bounded, reviewable drafts for {tool_name} without modifying files.",
                    "+    ...",
                    "@@",
                    f"+{tool_constant} = {{",
                    f"+    \"name\": \"{tool_name}\",",
                    "+    ...",
                    "+}",
                ]
            ),
            True,
        )

    if display_path == "tools/registry.py":
        return (
            _clip_preview_lines(
                [
                    f"--- {display_path}",
                    f"+++ {display_path} (draft)",
                    "@@",
                    "-from tools.shell import PLAN_PATCH_TOOL, RUN_SHELL_TOOL, SUGGEST_COMMANDS_TOOL",
                    f"+from tools.shell import {tool_constant}, PLAN_PATCH_TOOL, RUN_SHELL_TOOL, SUGGEST_COMMANDS_TOOL",
                    "@@",
                    "         SUGGEST_COMMANDS_TOOL,",
                    "         PLAN_PATCH_TOOL,",
                    f"+        {tool_constant},",
                    "         RUN_SHELL_TOOL,",
                ]
            ),
            True,
        )

    if display_path == "core/loop.py":
        return (
            _clip_preview_lines(
                [
                    f"--- {display_path}",
                    f"+++ {display_path} (draft)",
                    "@@",
                    f"+        if tool_name == \"{tool_name}\":",
                    "+            args = dict(args)",
                    "+            args.setdefault(\"goal\", task_state.goal)",
                    "+            args.setdefault(\"planned_files\", latest_plan.get(\"files_to_change\", [])[:4])",
                    "+            args.setdefault(\"suggested_commands\", latest_suggestions.get(\"suggestions\", [])[:3])",
                    "+            ...",
                ]
            ),
            True,
        )

    if display_path == "core/state.py":
        return (
            _clip_preview_lines(
                [
                    f"--- {display_path}",
                    f"+++ {display_path} (draft)",
                    "@@",
                    "+    def _collect_proposed_edits(self, limit: int = 3) -> Dict[str, Any]:",
                    "+        ...",
                    "@@",
                    f"+        lines.append(\"{section_title}:\")",
                    f"+        elif tool_name == \"{tool_name}\":",
                    "+            ...",
                ]
            ),
            True,
        )

    if display_path == "core/llm_client.py":
        return (
            _clip_preview_lines(
                [
                    f"--- {display_path}",
                    f"+++ {display_path} (draft)",
                    "@@",
                    f"+\"Use {tool_name} when the user wants reviewable edit drafts or patch-style previews without modifying files. \"",
                    f"+\"Add {section_title} when the final context includes drafted edits that materially help. \"",
                    "+\"Never describe drafted edits as completed or applied. \"",
                ]
            ),
            True,
        )

    generic_lines = [
        f"--- {display_path or 'target file'}",
        f"+++ {display_path or 'target file'} (draft)",
        "@@",
        f"+Draft change: {description}",
        f"+Reason: {reason_for_change or 'relevant to the current goal'}",
        "+Not applied; review before any real edit.",
    ]
    return _clip_preview_lines(generic_lines), False


def _build_draft_summary(goal: str, drafts: List[Dict[str, Any]], confidence: str) -> str:
    if not drafts:
        return "No proposed edits could be drafted from current evidence. Nothing was applied."

    goal_text = " ".join(goal.split())
    if len(goal_text) > 80:
        goal_text = goal_text[:77] + "..."
    top_paths = ", ".join(item.get("display_path", item.get("path", "")) for item in drafts[:2])

    if goal_text:
        return (
            f"Drafted {len(drafts)} proposed edit(s) for '{goal_text}' with {confidence} confidence. "
            f"Start with {top_paths}. Nothing was applied."
        )
    return f"Drafted {len(drafts)} proposed edit(s) with {confidence} confidence. Nothing was applied."


def draft_proposed_edits(args: Dict[str, Any]) -> Dict[str, Any]:
    goal = str(args.get("goal", "")).strip()
    base_path = str(args.get("base_path", "")).strip()
    path_a = str(args.get("path_a", "")).strip()
    path_b = str(args.get("path_b", "")).strip()
    compare_summary = _trim_text(args.get("compare_summary", ""), limit=220)
    files_differ = args.get("files_differ")
    max_files_to_draft = _coerce_int(
        args.get("max_files_to_draft", DRAFT_PROPOSED_EDITS_DEFAULT_MAX_FILES),
        DRAFT_PROPOSED_EDITS_DEFAULT_MAX_FILES,
        minimum=1,
        maximum=4,
    )

    planned_files = _normalize_planned_files(args.get("planned_files", []), limit=6)
    priority_files = _normalize_text_list(args.get("priority_files", []), limit=6)
    known_files = _normalize_text_list(args.get("known_files", []), limit=12)
    recent_notes = _normalize_text_list(args.get("recent_notes", []), limit=8, text_limit=180)
    suggested_commands = _normalize_dict_list(args.get("suggested_commands", []), limit=5)
    plan_confidence = str(args.get("plan_confidence", "")).strip().lower()
    plan_uncertainties = _normalize_text_list(args.get("plan_uncertainties", []), limit=3, text_limit=180)
    feature_context = _derive_feature_context(goal)

    command_targets: List[str] = []
    for suggestion in suggested_commands:
        command_targets.extend(_normalize_text_list(suggestion.get("target_paths", []), limit=3))

    compare_targets = [path for path in [path_a, path_b] if path]
    planned_lookup = {entry["path"]: entry for entry in planned_files}
    candidate_paths = [entry["path"] for entry in planned_files] + priority_files + compare_targets + command_targets + known_files
    if not candidate_paths:
        candidate_paths = _fallback_candidate_files(base_path)

    unique_candidates: List[str] = []
    for raw_path in candidate_paths:
        path = str(raw_path).strip()
        if not path or path in unique_candidates:
            continue
        candidate = Path(path)
        if candidate.exists() and candidate.is_dir():
            continue
        if not candidate.exists() and path not in planned_lookup:
            continue
        unique_candidates.append(path)

    compare_target_set = set(compare_targets)
    command_target_set = set(command_targets)
    goal_terms = _extract_goal_terms(goal, limit=5, stopwords=PATCH_PLAN_STOPWORDS)

    scored_candidates: List[Dict[str, Any]] = []
    for path in unique_candidates:
        plan_entry = planned_lookup.get(path, {})
        display_path = str(plan_entry.get("display_path", "")).strip() or _display_path(path) or path
        hint = CORE_FILE_EDIT_HINTS.get(display_path, {})
        score, reasons = _score_patch_candidate(
            path,
            goal_terms,
            priority_files,
            compare_target_set,
            command_target_set,
            recent_notes,
        )
        if plan_entry:
            score += 50
            if plan_entry.get("why"):
                reasons = [plan_entry["why"]] + [reason for reason in reasons if reason != plan_entry["why"]]

        scored_candidates.append(
            {
                "path": path,
                "display_path": display_path,
                "score": score,
                "planned": bool(plan_entry),
                "order_weight": int(plan_entry.get("order", hint.get("order", 90))),
                "why": reasons[:3],
                "proposed_edit_summary": str(plan_entry.get("proposed_edit_summary", "")).strip(),
            }
        )

    scored_candidates.sort(key=lambda item: (0 if item["planned"] else 1, item["order_weight"], -item["score"], item["display_path"]))
    selected_candidates = scored_candidates[:max_files_to_draft]

    evidence_sources = 0
    if planned_files:
        evidence_sources += 1
    if compare_targets:
        evidence_sources += 1
    if suggested_commands:
        evidence_sources += 1
    if priority_files or known_files:
        evidence_sources += 1

    if plan_confidence in {"high", "moderate", "low"}:
        confidence = plan_confidence
    elif evidence_sources >= 3 and len(selected_candidates) >= 2:
        confidence = "high"
    elif evidence_sources >= 2 and selected_candidates:
        confidence = "moderate"
    else:
        confidence = "low"

    drafts: List[Dict[str, Any]] = []
    top_uncertainties: List[str] = []
    for uncertainty in plan_uncertainties:
        lowered_uncertainty = str(uncertainty).lower()
        if "no suggested commands" in lowered_uncertainty and suggested_commands:
            continue
        if "no direct file comparison" in lowered_uncertainty and compare_targets:
            continue
        top_uncertainties.append(str(uncertainty))

    for item in selected_candidates:
        path = item["path"]
        display_path = item["display_path"]
        file_path = Path(path)
        file_exists = file_path.exists() and file_path.is_file()
        file_text = None
        truncated = False
        if file_exists:
            file_text, truncated = _safe_read_text(file_path)

        reason_parts: List[str] = []
        for reason in item.get("why", []):
            for part in str(reason).split(";"):
                cleaned = _trim_text(part, limit=180)
                if cleaned and cleaned not in reason_parts:
                    reason_parts.append(cleaned)
        if compare_summary and path in compare_target_set and compare_summary not in reason_parts:
            reason_parts.append(compare_summary)
        reason_for_change = "; ".join(reason_parts[:3]) or "relevant to the current goal"

        description = _build_draft_description(
            display_path,
            goal,
            feature_context,
            item.get("proposed_edit_summary", ""),
        )
        preview_lines, specific_preview = _build_role_specific_draft_preview(
            display_path,
            feature_context,
            description,
            reason_for_change,
        )

        item_uncertainties: List[str] = []
        if not file_exists:
            item_uncertainties.append("File not found; this draft is based only on the current plan and evidence.")
        elif file_text is None:
            item_uncertainties.append("The file could not be read as text, so the preview is schematic.")
        elif truncated:
            item_uncertainties.append("Only a bounded text sample was read, so nearby context may be incomplete.")
        if path in compare_target_set and files_differ is None:
            item_uncertainties.append("Comparison context was limited, so verify the exact changed lines before applying any edit.")

        item_confidence = confidence
        if not file_exists:
            item_confidence = "low"
        elif item_confidence == "high" and not specific_preview and not item.get("planned"):
            item_confidence = "moderate"
        elif item_confidence == "low" and item.get("planned") and specific_preview:
            item_confidence = "moderate"

        drafts.append(
            {
                "path": path,
                "display_path": display_path,
                "reason_for_change": reason_for_change,
                "proposed_edit_description": description,
                "draft_diff_preview": preview_lines,
                "confidence": item_confidence,
                "uncertainties": item_uncertainties[:3],
                "executed": False,
            }
        )

        for uncertainty in item_uncertainties:
            if uncertainty and uncertainty not in top_uncertainties:
                top_uncertainties.append(uncertainty)

    if not planned_files:
        top_uncertainties.append("No patch plan was available, so the draft selection is heuristic.")
    if len(scored_candidates) > max_files_to_draft:
        top_uncertainties.append("Additional files may still need draft edits after the first pass.")

    deduped_uncertainties: List[str] = []
    for uncertainty in top_uncertainties:
        cleaned = _trim_text(uncertainty, limit=180)
        if cleaned and cleaned not in deduped_uncertainties:
            deduped_uncertainties.append(cleaned)
        if len(deduped_uncertainties) >= 3:
            break

    return {
        "ok": True,
        "executed": False,
        "goal": goal,
        "summary": _build_draft_summary(goal, drafts, confidence),
        "drafts": drafts,
        "recommended_order": [item["path"] for item in drafts],
        "confidence": confidence,
        "uncertainties": deduped_uncertainties,
    }


def _normalize_review_bundle_drafts(values: Any, *, limit: int = 6) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    seen_keys: set[str] = set()

    for value in _normalize_dict_list(values, limit=limit):
        path = str(value.get("path", "")).strip()
        display_path = str(value.get("display_path", "")).strip() or _display_path(path) or path
        key = path or display_path
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        updated_text = value.get("updated_text")
        if updated_text is not None:
            updated_text = str(updated_text)
        expected_current_text = value.get("expected_current_text")
        if expected_current_text is not None:
            expected_current_text = str(expected_current_text)
        search_text = value.get("search")
        if search_text is not None:
            search_text = str(search_text)
        replace_text = value.get("replace")
        if replace_text is not None:
            replace_text = str(replace_text)

        normalized.append(
            {
                "path": path,
                "display_path": display_path,
                "reason_for_change": _trim_text(value.get("reason_for_change", ""), limit=220),
                "proposed_edit_description": _trim_text(value.get("proposed_edit_description", ""), limit=220),
                "draft_diff_preview": _normalize_text_list(
                    value.get("draft_diff_preview", []),
                    limit=DRAFT_DIFF_PREVIEW_MAX_LINES,
                    text_limit=200,
                ),
                "confidence": str(value.get("confidence", "")).strip().lower(),
                "uncertainties": _normalize_text_list(value.get("uncertainties", []), limit=3, text_limit=180),
                "mode": str(value.get("mode", "")).strip().lower(),
                "updated_text": updated_text,
                "expected_current_text": expected_current_text,
                "search": search_text,
                "replace": replace_text,
                "expected_occurrences": _coerce_int(value.get("expected_occurrences", 1), 1, minimum=1, maximum=20),
            }
        )

    return normalized


def _build_review_bundle_summary(goal: str, files: List[Dict[str, Any]], command_count: int, confidence: str) -> str:
    if not files:
        return "No review bundle could be built from current evidence. Nothing was approved or applied."

    goal_text = " ".join(goal.split())
    if len(goal_text) > 80:
        goal_text = goal_text[:77] + "..."
    top_paths = ", ".join(item.get("display_path", item.get("path", "")) for item in files[:2])
    command_text = f" Includes {command_count} suggested command(s) for manual review." if command_count else ""

    if goal_text:
        return (
            f"Prepared a review bundle for {len(files)} file(s) for '{goal_text}' with {confidence} confidence. "
            f"Focus on {top_paths}. Approval is still needed before any edits.{command_text}"
        )
    return (
        f"Prepared a review bundle for {len(files)} file(s) with {confidence} confidence. "
        f"Approval is still needed before any edits.{command_text}"
    )


def build_review_bundle(args: Dict[str, Any]) -> Dict[str, Any]:
    goal = str(args.get("goal", "")).strip()
    planned_changes = _normalize_planned_files(args.get("planned_changes", []), limit=6)
    proposed_edits = _normalize_review_bundle_drafts(args.get("proposed_edits", []), limit=6)
    suggested_commands = _normalize_dict_list(args.get("suggested_commands", []), limit=6)
    recent_notes = _normalize_text_list(args.get("recent_notes", []), limit=6, text_limit=180)
    plan_confidence = str(args.get("plan_confidence", "")).strip().lower()
    draft_confidence = str(args.get("draft_confidence", "")).strip().lower()
    plan_uncertainties = _normalize_text_list(args.get("plan_uncertainties", []), limit=4, text_limit=180)
    draft_uncertainties = _normalize_text_list(args.get("draft_uncertainties", []), limit=4, text_limit=180)
    max_files = _coerce_int(
        args.get("max_files", REVIEW_BUNDLE_DEFAULT_MAX_FILES),
        REVIEW_BUNDLE_DEFAULT_MAX_FILES,
        minimum=1,
        maximum=5,
    )
    max_commands = _coerce_int(
        args.get("max_commands", REVIEW_BUNDLE_DEFAULT_MAX_COMMANDS),
        REVIEW_BUNDLE_DEFAULT_MAX_COMMANDS,
        minimum=1,
        maximum=4,
    )

    plan_lookup = {entry["path"]: entry for entry in planned_changes if entry.get("path")}
    draft_lookup = {entry["path"]: entry for entry in proposed_edits if entry.get("path")}
    candidate_paths = [entry["path"] for entry in proposed_edits if entry.get("path")] + [
        entry["path"] for entry in planned_changes if entry.get("path")
    ]
    if not candidate_paths:
        for suggestion in suggested_commands:
            candidate_paths.extend(_normalize_text_list(suggestion.get("target_paths", []), limit=2))

    unique_paths: List[str] = []
    for raw_path in candidate_paths:
        path = str(raw_path).strip()
        if not path or path in unique_paths:
            continue
        unique_paths.append(path)
        if len(unique_paths) >= max_files:
            break

    files: List[Dict[str, Any]] = []
    confidence_rank = {"low": 0, "moderate": 1, "high": 2}
    top_uncertainties: List[str] = []
    for uncertainty in plan_uncertainties + draft_uncertainties:
        lowered_uncertainty = str(uncertainty).lower()
        if "no suggested commands" in lowered_uncertainty and suggested_commands:
            continue
        if ("no planned changes" in lowered_uncertainty or "no patch plan" in lowered_uncertainty) and planned_changes:
            continue
        if "no proposed edits" in lowered_uncertainty and proposed_edits:
            continue
        cleaned = _trim_text(uncertainty, limit=180)
        if cleaned and cleaned not in top_uncertainties:
            top_uncertainties.append(cleaned)

    for path in unique_paths:
        plan_entry = plan_lookup.get(path, {})
        draft_entry = draft_lookup.get(path, {})
        display_path = str(draft_entry.get("display_path", "")).strip() or str(plan_entry.get("display_path", "")).strip() or _display_path(path) or path

        reason_parts: List[str] = []
        for raw_reason in [draft_entry.get("reason_for_change", ""), plan_entry.get("why", "")]:
            for part in str(raw_reason).split(";"):
                cleaned = _trim_text(part, limit=180)
                if cleaned and cleaned not in reason_parts:
                    reason_parts.append(cleaned)
        why_would_change = "; ".join(reason_parts[:3]) or "relevant to the current goal"

        proposed_edit_description = str(draft_entry.get("proposed_edit_description", "")).strip() or str(plan_entry.get("proposed_edit_summary", "")).strip() or "Review the planned change before approval."
        preview_lines = _normalize_text_list(
            draft_entry.get("draft_diff_preview", []),
            limit=DRAFT_DIFF_PREVIEW_MAX_LINES,
            text_limit=200,
        )
        item_uncertainties = list(_normalize_text_list(draft_entry.get("uncertainties", []), limit=3, text_limit=180))

        file_confidence = str(draft_entry.get("confidence", "")).strip().lower()
        if file_confidence not in confidence_rank:
            file_confidence = draft_confidence if draft_confidence in confidence_rank else plan_confidence if plan_confidence in confidence_rank else "low"

        candidate = Path(path)
        if not path:
            item_uncertainties.append("Missing target file path; review is based only on current metadata.")
            file_confidence = "low"
        elif not candidate.exists():
            item_uncertainties.append("File not found; approval is based only on plan and draft metadata.")
            file_confidence = "low"
        elif candidate.is_dir():
            item_uncertainties.append("Target path is a directory, not a file.")
            file_confidence = "low"

        deduped_item_uncertainties: List[str] = []
        for uncertainty in item_uncertainties:
            cleaned = _trim_text(uncertainty, limit=180)
            if cleaned and cleaned not in deduped_item_uncertainties:
                deduped_item_uncertainties.append(cleaned)
            if len(deduped_item_uncertainties) >= 3:
                break

        files.append(
            {
                "path": path,
                "display_path": display_path,
                "why_would_change": why_would_change,
                "proposed_edit_description": _trim_text(proposed_edit_description, limit=220),
                "draft_diff_preview": preview_lines,
                "confidence": file_confidence,
                "uncertainties": deduped_item_uncertainties,
            }
        )

        for uncertainty in deduped_item_uncertainties:
            if uncertainty not in top_uncertainties:
                top_uncertainties.append(uncertainty)

    bundle_commands: List[Dict[str, Any]] = []
    for entry in suggested_commands[:max_commands]:
        command = str(entry.get("command", "")).strip()
        if not command:
            continue
        bundle_commands.append(
            {
                "command": command,
                "purpose": _trim_text(entry.get("purpose", ""), limit=180),
                "risk_level": str(entry.get("risk_level", "low")).strip() or "low",
                "why_relevant": _trim_text(entry.get("why_relevant", ""), limit=200),
            }
        )

    confidence_values = [
        value for value in [draft_confidence, plan_confidence] if value in confidence_rank
    ] + [item.get("confidence", "") for item in files if item.get("confidence", "") in confidence_rank]
    if confidence_values:
        confidence = min(confidence_values, key=lambda value: confidence_rank[value])
    elif files and proposed_edits and planned_changes:
        confidence = "moderate"
    elif files:
        confidence = "low"
    else:
        confidence = "low"

    if not planned_changes:
        top_uncertainties.append("No planned changes were available, so the bundle leans on current drafts and evidence.")
    if not proposed_edits:
        top_uncertainties.append("No proposed edits were available, so some files may lack diff-style previews.")
    if not bundle_commands:
        top_uncertainties.append("No suggested commands were included for manual verification.")
    if recent_notes and not files:
        top_uncertainties.append("Recent notes exist, but they were not specific enough to package into target files.")

    deduped_uncertainties: List[str] = []
    for uncertainty in top_uncertainties:
        cleaned = _trim_text(uncertainty, limit=180)
        if cleaned and cleaned not in deduped_uncertainties:
            deduped_uncertainties.append(cleaned)
        if len(deduped_uncertainties) >= 4:
            break

    return {
        "ok": True,
        "executed": False,
        "approval_status": "not approved",
        "goal": goal,
        "summary": _build_review_bundle_summary(goal, files, len(bundle_commands), confidence),
        "target_files": [item["path"] for item in files if item.get("path")],
        "files": files,
        "planned_changes": planned_changes[:max_files],
        "proposed_edits": proposed_edits[:max_files],
        "suggested_commands": bundle_commands,
        "confidence": confidence,
        "uncertainties": deduped_uncertainties,
    }

def run_shell(args):
    command = str(args.get("command", "")).strip()
    if not command:
        return {"ok": False, "error": "Missing command"}

    lowered = command.lower()
    if any(term in lowered for term in BLOCKED_COMMAND_TERMS):
        return {"ok": False, "error": f"Blocked command: {command}"}
    if SAFE_SHELL_CHAIN_PATTERN.search(command):
        return {"ok": False, "error": "Blocked shell command chain. Use one read-only inspection command at a time."}
    if SAFE_SHELL_HOST_PATH_PATTERN.search(command):
        return {"ok": False, "error": "Blocked shell command that references host-specific paths or environment shortcuts."}
    nested_match = SAFE_SHELL_NESTED_PATTERN.search(command)
    if nested_match and nested_match.group(0).lower() not in {"type", "more"}:
        return {"ok": False, "error": "Blocked nested shell or indirect execution helper in read-only shell mode."}
    if not SAFE_SHELL_READONLY_PATTERN.match(command):
        return {"ok": False, "error": "run_shell only allows compact read-only inspection commands."}

    backend_result = run_bounded_command(
        command=command,
        cwd=str(Path.cwd()),
        timeout_seconds=6.0,
        shell_kind="powershell",
    )
    backend_data = backend_result.get("data", {}) if isinstance(backend_result.get("data", {}), dict) else {}
    return {
        "ok": bool(backend_result.get("ok", False)),
        "command": command,
        "stdout": str(backend_data.get("stdout_excerpt", "")).strip(),
        "stderr": str(backend_data.get("stderr_excerpt", "")).strip(),
        "returncode": int(backend_data.get("exit_code", -1) or -1),
    }


SUGGEST_COMMANDS_TOOL = {
    "name": "suggest_commands",
    "description": (
        "Suggest compact read-only PowerShell commands based on the current goal and evidence. "
        "This tool never executes the commands."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "goal": {"type": "string"},
            "base_path": {"type": "string"},
            "path_a": {"type": "string"},
            "path_b": {"type": "string"},
            "files_differ": {"type": "boolean"},
            "priority_files": {
                "type": "array",
                "items": {"type": "string"}
            },
            "known_files": {
                "type": "array",
                "items": {"type": "string"}
            },
            "known_dirs": {
                "type": "array",
                "items": {"type": "string"}
            },
            "recent_notes": {
                "type": "array",
                "items": {"type": "string"}
            },
            "max_suggestions": {"type": "integer"}
        },
        "required": []
    },
    "func": suggest_commands,
}


PLAN_PATCH_TOOL = {
    "name": "plan_patch",
    "description": (
        "Plan a compact set of code or file changes based on the current evidence without modifying files. "
        "Returns files to change, why, order, proposed edit summaries, and uncertainties."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "goal": {"type": "string"},
            "base_path": {"type": "string"},
            "path_a": {"type": "string"},
            "path_b": {"type": "string"},
            "files_differ": {"type": "boolean"},
            "compare_summary": {"type": "string"},
            "priority_files": {
                "type": "array",
                "items": {"type": "string"}
            },
            "known_files": {
                "type": "array",
                "items": {"type": "string"}
            },
            "recent_notes": {
                "type": "array",
                "items": {"type": "string"}
            },
            "suggested_commands": {
                "type": "array",
                "items": {"type": "object"}
            },
            "max_files_to_change": {"type": "integer"}
        },
        "required": []
    },
    "func": plan_patch,
}


DRAFT_PROPOSED_EDITS_TOOL = {
    "name": "draft_proposed_edits",
    "description": (
        "Draft compact, reviewable proposed edits for likely target files without modifying anything. "
        "Returns per-file reasons, edit descriptions, bounded patch-style previews, confidence, and uncertainties."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "goal": {"type": "string"},
            "base_path": {"type": "string"},
            "path_a": {"type": "string"},
            "path_b": {"type": "string"},
            "files_differ": {"type": "boolean"},
            "compare_summary": {"type": "string"},
            "planned_files": {
                "type": "array",
                "items": {"type": "object"}
            },
            "priority_files": {
                "type": "array",
                "items": {"type": "string"}
            },
            "known_files": {
                "type": "array",
                "items": {"type": "string"}
            },
            "recent_notes": {
                "type": "array",
                "items": {"type": "string"}
            },
            "suggested_commands": {
                "type": "array",
                "items": {"type": "object"}
            },
            "plan_confidence": {"type": "string"},
            "plan_uncertainties": {
                "type": "array",
                "items": {"type": "string"}
            },
            "max_files_to_draft": {"type": "integer"}
        },
        "required": []
    },
    "func": draft_proposed_edits,
}


REVIEW_BUNDLE_TOOL = {
    "name": "build_review_bundle",
    "description": (
        "Package planned changes, proposed edits, and suggested commands into a compact review bundle without modifying anything. "
        "Returns approval-ready target files, draft previews, confidence, uncertainties, and approval_status=not approved."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "goal": {"type": "string"},
            "planned_changes": {
                "type": "array",
                "items": {"type": "object"}
            },
            "proposed_edits": {
                "type": "array",
                "items": {"type": "object"}
            },
            "suggested_commands": {
                "type": "array",
                "items": {"type": "object"}
            },
            "recent_notes": {
                "type": "array",
                "items": {"type": "string"}
            },
            "plan_confidence": {"type": "string"},
            "draft_confidence": {"type": "string"},
            "plan_uncertainties": {
                "type": "array",
                "items": {"type": "string"}
            },
            "draft_uncertainties": {
                "type": "array",
                "items": {"type": "string"}
            },
            "max_files": {"type": "integer"},
            "max_commands": {"type": "integer"}
        },
        "required": []
    },
    "func": build_review_bundle,
}


RUN_SHELL_TOOL = {
    "name": "run_shell",
    "description": "Run a safe read-only shell command.",
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string"}
        },
        "required": ["command"]
    },
    "func": run_shell,
}
