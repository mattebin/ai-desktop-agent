from __future__ import annotations

import copy
import difflib
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple


TEXT_FILE_EXTENSIONS = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".pyi",
    ".rst",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

HIGH_SIGNAL_FILES = {
    "agents.md": 55,
    "config.json": 35,
    "config.yaml": 35,
    "config.yml": 35,
    "dockerfile": 35,
    "license": 20,
    "main.py": 50,
    "makefile": 30,
    "package.json": 45,
    "pyproject.toml": 45,
    "readme": 45,
    "readme.md": 55,
    "requirements.txt": 50,
    "settings.json": 35,
    "settings.yaml": 45,
    "settings.yml": 45,
}

HIGH_SIGNAL_DIRS = {
    "app",
    "config",
    "core",
    "docs",
    "lib",
    "scripts",
    "src",
    "tests",
    "tools",
}

SKIP_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}

GOAL_TERM_STOPWORDS = {
    "a",
    "an",
    "and",
    "be",
    "better",
    "current",
    "file",
    "files",
    "for",
    "from",
    "goal",
    "inspect",
    "inspection",
    "project",
    "read",
    "same",
    "session",
    "task",
    "the",
    "to",
    "use",
    "with",
}

PATH_TERM_STOPWORDS = GOAL_TERM_STOPWORDS | {
    "cfg",
    "ini",
    "json",
    "md",
    "ps1",
    "py",
    "pyi",
    "rst",
    "toml",
    "txt",
    "yaml",
    "yml",
}

ARCHITECTURE_HINT_TERMS = {
    "agent",
    "config",
    "core",
    "files",
    "llm",
    "loop",
    "main",
    "memory",
    "registry",
    "safety",
    "settings",
    "shell",
    "state",
    "tool",
    "tools",
}

GOAL_TERM_ALIASES = {
    "agent": ["main", "loop", "state"],
    "config": ["settings"],
    "memory": ["state"],
    "settings": ["config"],
    "task": ["loop", "state"],
    "tool": ["files", "registry", "shell", "tools"],
}

INSPECT_PROJECT_CACHE_TTL_SECONDS = 45
INSPECT_PROJECT_CACHE_MAX_ENTRIES = 24
INSPECT_PROJECT_CACHE: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
COMPARE_FILES_DEFAULT_MAX_BYTES = 4000
COMPARE_FILES_DIFF_PREVIEW_LINES = 14
APPLY_APPROVED_EDITS_DEFAULT_MAX_FILES = 4
APPLY_APPROVED_EDITS_DEFAULT_MAX_BYTES = 60_000
APPLY_APPROVED_EDITS_DIFF_PREVIEW_LINES = 12
APPLY_APPROVED_EDITS_MAX_OCCURRENCES = 20


def _coerce_int(
    value: Any,
    default: int,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default

    if parsed < minimum:
        parsed = minimum
    if maximum is not None and parsed > maximum:
        parsed = maximum
    return parsed


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if value is None:
        return default
    return bool(value)


def _entry_for_path(path: Path) -> Dict[str, Any]:
    return {
        "name": path.name,
        "path": str(path),
        "type": "dir" if path.is_dir() else "file",
    }


def _normalize_cache_path(path: Path) -> str:
    try:
        return str(path.resolve())
    except Exception:
        return str(path)


def _extract_rank_terms(
    raw_text: str,
    *,
    stopwords: set[str] | None = None,
    limit: int = 12,
) -> List[str]:
    terms: List[str] = []
    for match in re.findall(r"[a-z0-9]+", raw_text.lower()):
        if len(match) < 2:
            continue
        if stopwords and match in stopwords:
            continue
        if match in terms:
            continue
        terms.append(match)
        if len(terms) >= limit:
            break
    return terms


def _parse_focus_terms(raw_focus: str) -> List[str]:
    return _extract_rank_terms(raw_focus, stopwords=GOAL_TERM_STOPWORDS, limit=6)


def _build_inspect_project_cache_key(
    path: Path,
    focus: str,
    goal: str,
    max_depth: int,
    max_entries: int,
    max_files_to_read: int,
    max_bytes_per_file: int,
    top_k_relevant: int,
) -> Tuple[Any, ...]:
    return (
        _normalize_cache_path(path),
        focus.strip().lower(),
        goal.strip().lower(),
        max_depth,
        max_entries,
        max_files_to_read,
        max_bytes_per_file,
        top_k_relevant,
    )


def _build_cache_metadata(*, hit: bool, age_seconds: float) -> Dict[str, Any]:
    age_seconds = max(0.0, age_seconds)
    return {
        "hit": hit,
        "age_seconds": round(age_seconds, 2),
        "ttl_seconds": INSPECT_PROJECT_CACHE_TTL_SECONDS,
        "expires_in_seconds": max(
            0.0,
            round(INSPECT_PROJECT_CACHE_TTL_SECONDS - age_seconds, 2),
        ),
    }


def _prune_inspect_project_cache(now: float):
    expired_keys = []
    for key, entry in INSPECT_PROJECT_CACHE.items():
        created_at = float(entry.get("created_at", 0))
        if now - created_at > INSPECT_PROJECT_CACHE_TTL_SECONDS:
            expired_keys.append(key)

    for key in expired_keys:
        INSPECT_PROJECT_CACHE.pop(key, None)

    if len(INSPECT_PROJECT_CACHE) <= INSPECT_PROJECT_CACHE_MAX_ENTRIES:
        return

    ordered_keys = sorted(
        INSPECT_PROJECT_CACHE,
        key=lambda key: float(INSPECT_PROJECT_CACHE[key].get("last_used_at", 0)),
    )
    overflow = len(INSPECT_PROJECT_CACHE) - INSPECT_PROJECT_CACHE_MAX_ENTRIES
    for key in ordered_keys[:overflow]:
        INSPECT_PROJECT_CACHE.pop(key, None)


def _get_cached_inspection(cache_key: Tuple[Any, ...], now: float) -> Dict[str, Any] | None:
    _prune_inspect_project_cache(now)
    entry = INSPECT_PROJECT_CACHE.get(cache_key)
    if not entry:
        return None

    created_at = float(entry.get("created_at", now))
    age_seconds = now - created_at
    entry["last_used_at"] = now

    result = copy.deepcopy(entry["result"])
    result["from_cache"] = True
    result["cache"] = _build_cache_metadata(hit=True, age_seconds=age_seconds)
    return result


def _store_cached_inspection(cache_key: Tuple[Any, ...], result: Dict[str, Any], now: float):
    cached_result = copy.deepcopy(result)
    cached_result["from_cache"] = False
    cached_result["cache"] = _build_cache_metadata(hit=False, age_seconds=0.0)
    INSPECT_PROJECT_CACHE[cache_key] = {
        "created_at": now,
        "last_used_at": now,
        "result": cached_result,
    }
    _prune_inspect_project_cache(now)


def _collect_project_snapshot(root: Path, max_entries: int, max_depth: int) -> Dict[str, Any]:
    queue: List[Tuple[Path, int]] = [(root, 0)]
    top_level: List[Dict[str, Any]] = []
    directories: List[Dict[str, Any]] = []
    skipped_directories: List[str] = []
    file_candidates: List[Path] = []

    scanned_entries = 0
    discovered_dirs = 0
    discovered_files = 0

    while queue and scanned_entries < max_entries:
        current, depth = queue.pop(0)
        try:
            children = sorted(
                current.iterdir(),
                key=lambda item: (item.is_file(), item.name.lower()),
            )
        except Exception:
            continue

        if current == root:
            for child in children[:20]:
                try:
                    top_level.append(_entry_for_path(child))
                except Exception:
                    continue

        for child in children:
            if scanned_entries >= max_entries:
                break

            try:
                is_dir = child.is_dir()
            except Exception:
                continue

            scanned_entries += 1

            if is_dir:
                discovered_dirs += 1
                if len(directories) < 20:
                    directories.append(
                        {
                            "name": child.name,
                            "path": str(child),
                        }
                    )

                if child.name.lower() in SKIP_DIR_NAMES:
                    if len(skipped_directories) < 10:
                        skipped_directories.append(str(child))
                    continue

                if depth < max_depth:
                    queue.append((child, depth + 1))
                continue

            discovered_files += 1
            file_candidates.append(child)

    return {
        "top_level": top_level,
        "directories": directories,
        "file_candidates": file_candidates,
        "skipped_directories": skipped_directories,
        "scanned_entries": scanned_entries,
        "discovered_dirs": discovered_dirs,
        "discovered_files": discovered_files,
        "truncated": bool(queue) or scanned_entries >= max_entries,
    }


def _build_architecture_terms(snapshot: Dict[str, Any]) -> List[str]:
    terms: List[str] = []
    for term in sorted(ARCHITECTURE_HINT_TERMS):
        if term not in terms:
            terms.append(term)

    for entry in snapshot.get("top_level", [])[:16]:
        if str(entry.get("type", "")) != "dir":
            continue
        name = str(entry.get("name", "")).strip()
        for term in _extract_rank_terms(name, stopwords=PATH_TERM_STOPWORDS, limit=4):
            if term not in terms:
                terms.append(term)
            if len(terms) >= 20:
                return terms

    for entry in snapshot.get("directories", [])[:16]:
        name = str(entry.get("name", "")).strip()
        for term in _extract_rank_terms(name, stopwords=PATH_TERM_STOPWORDS, limit=4):
            if term not in terms:
                terms.append(term)
            if len(terms) >= 20:
                return terms

    return terms


def _score_candidate_file(root: Path, path: Path, focus_terms: List[str]) -> Tuple[int, List[str]]:
    try:
        relative_path = path.relative_to(root)
    except ValueError:
        relative_path = path

    parts = [part.lower() for part in relative_path.parts]
    name = path.name.lower()
    ext = path.suffix.lower()
    relative_text = "/".join(parts)
    matched_terms = [term for term in focus_terms if term in relative_text]

    is_text_like = name in HIGH_SIGNAL_FILES or ext in TEXT_FILE_EXTENSIONS or bool(matched_terms)
    if not is_text_like:
        return 0, []

    score = 0
    reasons: List[str] = []

    file_bonus = HIGH_SIGNAL_FILES.get(name, 0)
    if file_bonus:
        score += file_bonus
        reasons.append("important filename")

    if ext in TEXT_FILE_EXTENSIONS:
        score += 20
        reasons.append("readable text file")

    if len(parts) == 1:
        score += 16
        reasons.append("top-level")
    elif len(parts) == 2:
        score += 8

    if any(part in HIGH_SIGNAL_DIRS for part in parts[:-1]):
        score += 12
        reasons.append("inside key folder")

    if matched_terms:
        score += 14 * len(matched_terms[:2])
        reasons.append(f"matches focus: {', '.join(matched_terms[:2])}")

    try:
        size = path.stat().st_size
    except Exception:
        size = 0

    if 0 < size <= 16_000:
        score += 6
    elif size > 200_000:
        score -= 20

    if name.startswith("."):
        score -= 8
    if "test" in name or any(part == "tests" for part in parts[:-1]):
        score -= 4

    return score, reasons[:3]


def _score_relevance_candidate(
    relative_path: str,
    goal_terms: List[str],
    architecture_terms: List[str],
) -> Tuple[int, List[str]]:
    path_text = relative_path.lower().replace("\\", "/")
    path_terms = _extract_rank_terms(path_text, stopwords=PATH_TERM_STOPWORDS, limit=24)
    path_term_set = set(path_terms)
    stem_terms = set(_extract_rank_terms(Path(relative_path).stem, stopwords=PATH_TERM_STOPWORDS, limit=8))

    score = 0
    reasons: List[str] = []

    filename_matches = [term for term in goal_terms if term in stem_terms]
    path_matches = [term for term in goal_terms if term in path_term_set and term not in filename_matches]
    fuzzy_matches = [
        term for term in goal_terms
        if term not in filename_matches and term not in path_matches and term in path_text
    ]

    alias_matches: List[str] = []
    for term in goal_terms:
        for alias in GOAL_TERM_ALIASES.get(term, []):
            if alias in path_term_set and alias not in alias_matches:
                alias_matches.append(alias)

    if filename_matches:
        score += 28 * min(2, len(filename_matches))
        reasons.append(f"filename matches goal: {', '.join(filename_matches[:2])}")

    if path_matches:
        score += 18 * min(2, len(path_matches))
        reasons.append(f"path matches goal: {', '.join(path_matches[:2])}")

    if fuzzy_matches:
        score += 10 * min(2, len(fuzzy_matches))
        reasons.append(f"partial goal match: {', '.join(fuzzy_matches[:2])}")

    if alias_matches:
        score += 12 * min(2, len(alias_matches))
        reasons.append(f"related match: {', '.join(alias_matches[:2])}")

    if goal_terms and not (filename_matches or path_matches or fuzzy_matches or alias_matches):
        score -= 18
        reasons.append("deprioritized by goal")

    architecture_matches = [term for term in architecture_terms if term in path_term_set and term not in goal_terms]
    if architecture_matches:
        score += 4 * min(2, len(architecture_matches))
        reasons.append(f"fits architecture: {', '.join(architecture_matches[:2])}")

    relative_parts = [part.lower() for part in Path(relative_path).parts]
    if any(part in {"test", "tests"} for part in relative_parts) and not any(
        term in {"test", "tests"} for term in goal_terms
    ):
        score -= 12
        reasons.append("deprioritized test path")

    if any(part in SKIP_DIR_NAMES for part in relative_parts):
        score -= 20

    if Path(relative_path).name.lower().startswith("."):
        score -= 8

    return score, reasons[:3]


def _select_relevant_files(
    root: Path,
    ranked_candidates: List[Tuple[int, int, str, Path, List[str]]],
    goal: str,
    snapshot: Dict[str, Any],
    top_k_relevant: int,
) -> List[Dict[str, Any]]:
    goal_terms = _extract_rank_terms(goal, stopwords=GOAL_TERM_STOPWORDS, limit=10)
    architecture_terms = _build_architecture_terms(snapshot)

    selected: List[Tuple[int, int, str, Path, List[str]]] = []
    for base_score, depth, sort_name, candidate, reasons in ranked_candidates[:20]:
        try:
            relative_path = str(candidate.relative_to(root))
        except ValueError:
            relative_path = candidate.name

        relevance_score, relevance_reasons = _score_relevance_candidate(
            relative_path,
            goal_terms,
            architecture_terms,
        )
        total_score = base_score + relevance_score

        if goal_terms and relevance_score <= 0 and base_score < 70:
            continue

        why_parts: List[str] = []
        for reason in relevance_reasons + reasons:
            if reason and reason not in why_parts:
                why_parts.append(reason)

        selected.append((total_score, depth, sort_name, candidate, why_parts[:3] or reasons or ["candidate file"]))

    if not selected:
        selected = ranked_candidates[:max(1, top_k_relevant)]

    selected.sort(key=lambda item: (-item[0], item[1], item[2]))

    relevant_files: List[Dict[str, Any]] = []
    for score, _, _, candidate, reasons in selected[:top_k_relevant]:
        try:
            relative_path = str(candidate.relative_to(root))
        except ValueError:
            relative_path = candidate.name

        relevant_files.append(
            {
                "path": str(candidate),
                "relative_path": relative_path,
                "score": score,
                "why": "; ".join(reasons[:3]) if reasons else "candidate file",
            }
        )

    return relevant_files


def _read_preview(root: Path, path: Path, max_bytes: int) -> Dict[str, Any] | None:
    try:
        with path.open("rb") as f:
            data = f.read(max_bytes + 1)
    except Exception:
        return None

    if b"\x00" in data[:1024]:
        return None

    preview = data[:max_bytes].decode(errors="replace").strip()
    if not preview:
        return None

    try:
        relative_path = str(path.relative_to(root))
    except ValueError:
        relative_path = path.name

    return {
        "path": str(path),
        "relative_path": relative_path,
        "preview": preview,
        "truncated": len(data) > max_bytes,
    }


def _build_inspection_summary(
    root: Path,
    focus: str,
    likely_files: List[Dict[str, Any]],
    recommended_files: List[Dict[str, Any]],
    snapshot: Dict[str, Any],
) -> str:
    top_level_names = ", ".join(entry["name"] for entry in snapshot["top_level"][:6]) or "(empty)"
    likely_names = ", ".join(item["relative_path"] for item in likely_files[:3]) or "none"
    recommended_names = ", ".join(item["relative_path"] for item in recommended_files[:3]) or "none"

    parts = [
        (
            f"Inspected {root}: scanned {snapshot['scanned_entries']} entries "
            f"({snapshot['discovered_dirs']} dirs, {snapshot['discovered_files']} files)."
        ),
        f"Top-level: {top_level_names}.",
        f"Likely files: {likely_names}.",
        f"Recommended first reads: {recommended_names}.",
    ]
    if focus:
        parts.append(f"Focus: {focus}.")
    if snapshot["truncated"]:
        parts.append("Scan truncated by limits.")
    return " ".join(parts)


def _build_selection_summary(goal: str, recommended_files: List[Dict[str, Any]]) -> str:
    names = ", ".join(item["relative_path"] for item in recommended_files[:3]) or "none"
    if not recommended_files:
        return "No high-confidence files selected."

    goal_text = " ".join(goal.strip().split())
    if len(goal_text) > 80:
        goal_text = goal_text[:77] + "..."

    if goal_text:
        return f"For goal '{goal_text}', read {names} first."
    return f"Read {names} first."


def _read_compare_sample(path: Path, max_bytes: int) -> Tuple[bytes, bool]:
    with path.open("rb") as f:
        data = f.read(max_bytes + 1)
    return data[:max_bytes], len(data) > max_bytes


def _is_probably_text_bytes(data: bytes) -> bool:
    return b"\x00" not in data[:1024]


def _trim_diff_preview_line(line: str, limit: int = 180) -> str:
    text = line.rstrip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _build_compact_diff_preview(
    path_a: Path,
    path_b: Path,
    text_a: str,
    text_b: str,
    max_lines: int = COMPARE_FILES_DIFF_PREVIEW_LINES,
) -> List[str]:
    diff_lines = difflib.unified_diff(
        text_a.splitlines(),
        text_b.splitlines(),
        fromfile=path_a.name,
        tofile=path_b.name,
        lineterm="",
    )

    preview: List[str] = []
    for line in diff_lines:
        if not line.startswith(("---", "+++", "@@", "-", "+")):
            continue
        preview.append(_trim_diff_preview_line(line))
        if len(preview) >= max_lines:
            break
    return preview


def _collect_diff_stats(lines_a: List[str], lines_b: List[str]) -> Dict[str, int]:
    matcher = difflib.SequenceMatcher(a=lines_a, b=lines_b)
    changed_sections = 0
    added_lines = 0
    removed_lines = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        changed_sections += 1
        if tag in {"replace", "delete"}:
            removed_lines += i2 - i1
        if tag in {"replace", "insert"}:
            added_lines += j2 - j1

    return {
        "changed_sections": changed_sections,
        "added_lines": added_lines,
        "removed_lines": removed_lines,
    }


def _files_are_identical(path_a: Path, path_b: Path, chunk_size: int = 8192) -> bool:
    with path_a.open("rb") as file_a, path_b.open("rb") as file_b:
        while True:
            chunk_a = file_a.read(chunk_size)
            chunk_b = file_b.read(chunk_size)
            if chunk_a != chunk_b:
                return False
            if not chunk_a:
                return True



def _display_local_path(path: Path | str) -> str:
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


def _normalize_path_list(values: Any, *, limit: int = 8) -> List[str]:
    if not isinstance(values, list):
        return []

    normalized: List[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in normalized:
            continue
        normalized.append(text)
        if len(normalized) >= limit:
            break
    return normalized


def _normalize_preview_lines(values: Any, *, limit: int = APPLY_APPROVED_EDITS_DIFF_PREVIEW_LINES) -> List[str]:
    if not isinstance(values, list):
        return []

    lines: List[str] = []
    for value in values:
        text = str(value).rstrip()
        if not text.strip():
            continue
        if len(text) > 220:
            text = text[:217].rstrip() + "..."
        lines.append(text)
        if len(lines) >= limit:
            break
    return lines


def _normalize_approved_edit_entries(values: Any, *, limit: int = 6) -> List[Dict[str, Any]]:
    if not isinstance(values, list):
        return []

    normalized: List[Dict[str, Any]] = []
    seen_paths: set[str] = set()

    for value in values[:limit]:
        if not isinstance(value, dict):
            continue

        path_text = str(value.get("path", "")).strip()
        display_path = str(value.get("display_path", "")).strip() or _display_local_path(path_text) or path_text
        dedupe_key = path_text or display_path
        if dedupe_key and dedupe_key in seen_paths:
            continue
        if dedupe_key:
            seen_paths.add(dedupe_key)

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
        if replace_text is None:
            replace_text = ""
        else:
            replace_text = str(replace_text)

        mode = str(value.get("mode", "")).strip().lower()
        if not mode:
            if updated_text is not None:
                mode = "replace_entire_file"
            elif search_text is not None:
                mode = "search_replace"

        normalized.append(
            {
                "path": path_text,
                "display_path": display_path,
                "mode": mode,
                "updated_text": updated_text,
                "expected_current_text": expected_current_text,
                "search": search_text,
                "replace": replace_text,
                "expected_occurrences": _coerce_int(
                    value.get("expected_occurrences", 1),
                    1,
                    minimum=1,
                    maximum=APPLY_APPROVED_EDITS_MAX_OCCURRENCES,
                ),
                "reason_for_change": str(value.get("reason_for_change", "")).strip(),
                "proposed_edit_description": str(value.get("proposed_edit_description", "")).strip(),
                "draft_diff_preview": _normalize_preview_lines(value.get("draft_diff_preview", [])),
                "confidence": str(value.get("confidence", "")).strip().lower(),
                "uncertainties": _normalize_path_list(value.get("uncertainties", []), limit=3),
            }
        )

    return normalized


def _collect_bundle_target_paths(bundle: Dict[str, Any], *, limit: int = 8) -> List[str]:
    targets = _normalize_path_list(bundle.get("target_files", []), limit=limit)
    files = bundle.get("files", [])
    if isinstance(files, list):
        for entry in files[:limit]:
            if not isinstance(entry, dict):
                continue
            path_text = str(entry.get("path", "")).strip()
            if path_text and path_text not in targets:
                targets.append(path_text)
                if len(targets) >= limit:
                    break
    return targets[:limit]


def _is_within_workspace(path: Path) -> bool:
    try:
        path.resolve().relative_to(Path.cwd().resolve())
        return True
    except Exception:
        return False


def _read_editable_text_file(path: Path, max_bytes: int) -> tuple[bytes | None, str | None, str | None]:
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        return None, None, f"Could not read file: {exc}"

    if len(raw_bytes) > max_bytes:
        return raw_bytes, None, f"File exceeds max_bytes_per_file ({max_bytes} bytes)."
    if b"\x00" in raw_bytes:
        return raw_bytes, None, "Binary files are not supported for approved edits."

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return raw_bytes, None, "Only UTF-8 text files are supported for approved edits."

    return raw_bytes, text, None


def _build_apply_diff_preview(path: Path, before_text: str, after_text: str) -> List[str]:
    diff_lines = list(
        difflib.unified_diff(
            before_text.splitlines(),
            after_text.splitlines(),
            fromfile=_display_local_path(path) or str(path),
            tofile=f"{_display_local_path(path) or str(path)} (updated)",
            lineterm="",
        )
    )
    return _normalize_preview_lines(diff_lines, limit=APPLY_APPROVED_EDITS_DIFF_PREVIEW_LINES)


def _build_applied_changes_summary(applied_files: List[Dict[str, Any]], unchanged_files: List[Dict[str, Any]]) -> str:
    if not applied_files and not unchanged_files:
        return "No approved edits were applied."

    changed_paths = ", ".join(item.get("display_path", item.get("path", "")) for item in applied_files[:2])
    if applied_files:
        summary = f"Applied {len(applied_files)} approved edit(s)"
        if changed_paths:
            summary += f" to {changed_paths}"
        summary += " and created .bak backups first."
    else:
        summary = "No file content changes were needed; the approved content already matched the current files."

    if unchanged_files:
        summary += f" {len(unchanged_files)} file(s) already matched the approved content."
    return summary


def apply_approved_edits(args: Dict[str, Any]) -> Dict[str, Any]:
    max_files = _coerce_int(
        args.get("max_files", APPLY_APPROVED_EDITS_DEFAULT_MAX_FILES),
        APPLY_APPROVED_EDITS_DEFAULT_MAX_FILES,
        minimum=1,
        maximum=6,
    )
    max_bytes_per_file = _coerce_int(
        args.get("max_bytes_per_file", APPLY_APPROVED_EDITS_DEFAULT_MAX_BYTES),
        APPLY_APPROVED_EDITS_DEFAULT_MAX_BYTES,
        minimum=500,
        maximum=200_000,
    )

    review_bundle = args.get("review_bundle", {}) if isinstance(args.get("review_bundle", {}), dict) else {}
    top_level_status = str(args.get("approval_status", "")).strip().lower()
    bundle_status = str(review_bundle.get("approval_status", "")).strip().lower() if review_bundle else ""

    if review_bundle and top_level_status and top_level_status != bundle_status:
        return {
            "ok": False,
            "executed": False,
            "approval_status": bundle_status or top_level_status or "not approved",
            "error": "approval_status did not match the supplied review_bundle.",
        }

    if review_bundle:
        if bundle_status != "approved":
            return {
                "ok": False,
                "executed": False,
                "approval_status": bundle_status or "not approved",
                "error": "review_bundle approval_status must be 'approved' before applying edits.",
            }
        source_edits = args.get("approved_edits")
        if source_edits is None:
            source_edits = args.get("approved_payload")
        if source_edits is None:
            source_edits = review_bundle.get("approved_edits")
        if source_edits is None:
            source_edits = review_bundle.get("proposed_edits")
        if source_edits is None:
            source_edits = review_bundle.get("files")
        allowed_targets = _collect_bundle_target_paths(review_bundle, limit=max_files + 2)
        approval_status = bundle_status
    else:
        if top_level_status != "approved":
            return {
                "ok": False,
                "executed": False,
                "approval_status": top_level_status or "not approved",
                "error": "Explicit approval_status='approved' is required before applying edits.",
            }
        source_edits = args.get("approved_edits")
        if source_edits is None:
            source_edits = args.get("approved_payload")
        allowed_targets = _normalize_path_list(args.get("target_files", []), limit=max_files + 2)
        approval_status = top_level_status

    approved_edits = _normalize_approved_edit_entries(source_edits, limit=max_files)
    if not approved_edits:
        return {
            "ok": False,
            "executed": False,
            "approval_status": approval_status,
            "error": (
                "No exact approved edits were provided. Supply approved_edits with either "
                "search/replace fields or updated_text plus expected_current_text."
            ),
        }

    allowed_target_resolved: set[str] = set()
    for target in allowed_targets:
        try:
            allowed_target_resolved.add(str(Path(target).resolve()))
        except Exception:
            allowed_target_resolved.add(target)

    validations: List[Dict[str, Any]] = []
    validation_errors: List[str] = []

    for edit in approved_edits:
        raw_path = str(edit.get("path", "")).strip()
        display_path = str(edit.get("display_path", "")).strip() or _display_local_path(raw_path) or raw_path
        mode = str(edit.get("mode", "")).strip().lower()

        if not raw_path:
            validation_errors.append("Approved edit is missing a target file path.")
            continue

        path = Path(raw_path)
        try:
            resolved_path = path.resolve()
        except Exception:
            validation_errors.append(f"Invalid path: {raw_path}")
            continue

        resolved_text = str(resolved_path)
        if allowed_target_resolved and resolved_text not in allowed_target_resolved and raw_path not in allowed_targets:
            validation_errors.append(f"Edit target is not part of the approved target set: {raw_path}")
            continue
        if not _is_within_workspace(resolved_path):
            validation_errors.append(f"Refusing to edit outside the workspace: {raw_path}")
            continue
        if not resolved_path.exists():
            validation_errors.append(f"File not found: {raw_path}")
            continue
        if not resolved_path.is_file():
            validation_errors.append(f"Not a file: {raw_path}")
            continue

        original_bytes, original_text, read_error = _read_editable_text_file(resolved_path, max_bytes_per_file)
        if read_error:
            validation_errors.append(f"{raw_path}: {read_error}")
            continue

        if mode == "replace_entire_file":
            updated_text = edit.get("updated_text")
            expected_current_text = edit.get("expected_current_text")
            if not isinstance(updated_text, str):
                validation_errors.append(f"{raw_path}: replace_entire_file requires updated_text.")
                continue
            if not isinstance(expected_current_text, str):
                validation_errors.append(f"{raw_path}: replace_entire_file requires expected_current_text for safe application.")
                continue
            if original_text != expected_current_text:
                validation_errors.append(f"{raw_path}: current file content did not match expected_current_text.")
                continue
            new_text = updated_text
            apply_summary = "Replaced the full file after exact current-text validation."
        elif mode == "search_replace":
            search_text = edit.get("search")
            replace_text = edit.get("replace", "")
            if not isinstance(search_text, str) or not search_text:
                validation_errors.append(f"{raw_path}: search_replace requires a non-empty search string.")
                continue
            occurrences = original_text.count(search_text)
            expected_occurrences = _coerce_int(edit.get("expected_occurrences", 1), 1, minimum=1, maximum=APPLY_APPROVED_EDITS_MAX_OCCURRENCES)
            if occurrences != expected_occurrences:
                validation_errors.append(
                    f"{raw_path}: expected {expected_occurrences} occurrence(s) of the search text, found {occurrences}."
                )
                continue
            new_text = original_text.replace(search_text, str(replace_text))
            apply_summary = f"Applied an exact search/replace update ({occurrences} occurrence(s))."
        else:
            validation_errors.append(f"{raw_path}: unsupported approved edit mode '{mode or 'missing'}'.")
            continue

        new_bytes = new_text.encode("utf-8")
        if len(new_bytes) > max_bytes_per_file:
            validation_errors.append(f"{raw_path}: updated content exceeds max_bytes_per_file ({max_bytes_per_file} bytes).")
            continue

        validations.append(
            {
                "path": resolved_path,
                "display_path": display_path,
                "mode": mode,
                "original_bytes": original_bytes,
                "original_text": original_text,
                "new_bytes": new_bytes,
                "new_text": new_text,
                "changed": original_bytes != new_bytes,
                "backup_path": resolved_path.with_name(resolved_path.name + ".bak"),
                "reason_for_change": str(edit.get("reason_for_change", "")).strip(),
                "proposed_edit_description": str(edit.get("proposed_edit_description", "")).strip(),
                "draft_diff_preview": _normalize_preview_lines(edit.get("draft_diff_preview", [])),
                "confidence": str(edit.get("confidence", "")).strip() or "unknown",
                "uncertainties": _normalize_path_list(edit.get("uncertainties", []), limit=3),
                "apply_summary": apply_summary,
            }
        )

    if validation_errors:
        return {
            "ok": False,
            "executed": False,
            "approval_status": approval_status,
            "error": validation_errors[0],
            "validation_errors": validation_errors[:6],
        }

    applied_files: List[Dict[str, Any]] = []
    unchanged_files: List[Dict[str, Any]] = []

    try:
        for item in validations:
            path = item["path"]
            display_path = item["display_path"]
            if not item["changed"]:
                unchanged_files.append(
                    {
                        "path": str(path),
                        "display_path": display_path,
                        "mode": item["mode"],
                        "applied": False,
                        "backup_path": "",
                        "summary": "Approved content already matched the current file. No write was needed.",
                        "diff_preview": [],
                    }
                )
                continue

            backup_path = item["backup_path"]
            backup_path.write_bytes(item["original_bytes"])
            path.write_bytes(item["new_bytes"])

            diff_preview = _build_apply_diff_preview(path, item["original_text"], item["new_text"])
            applied_files.append(
                {
                    "path": str(path),
                    "display_path": display_path,
                    "mode": item["mode"],
                    "applied": True,
                    "backup_path": str(backup_path),
                    "reason_for_change": item["reason_for_change"],
                    "proposed_edit_description": item["proposed_edit_description"],
                    "summary": item["apply_summary"],
                    "diff_preview": diff_preview,
                    "confidence": item["confidence"],
                    "uncertainties": item["uncertainties"],
                }
            )
    except Exception as exc:
        rollback_errors: List[str] = []
        for applied in reversed(applied_files):
            try:
                Path(applied["path"]).write_bytes(Path(applied["backup_path"]).read_bytes())
            except Exception as rollback_exc:
                rollback_errors.append(f"{applied['path']}: {rollback_exc}")
        return {
            "ok": False,
            "executed": False,
            "approval_status": approval_status,
            "error": f"Failed while applying approved edits: {exc}",
            "rollback_errors": rollback_errors[:4],
        }

    summary = _build_applied_changes_summary(applied_files, unchanged_files)
    return {
        "ok": True,
        "executed": bool(applied_files),
        "approval_status": approval_status,
        "summary": summary,
        "applied_count": len(applied_files),
        "unchanged_count": len(unchanged_files),
        "applied_files": applied_files,
        "unchanged_files": unchanged_files,
        "backups_created": [item["backup_path"] for item in applied_files if item.get("backup_path")],
        "target_files": [item["path"] for item in applied_files + unchanged_files],
    }

def read_file(args: Dict[str, Any]) -> Dict[str, Any]:
    raw_path = str(args.get("path", "")).strip()
    if not raw_path:
        return {"ok": False, "error": "Missing path"}

    path = Path(raw_path)
    if not path.exists():
        return {"ok": False, "error": f"File not found: {raw_path}"}
    if not path.is_file():
        return {"ok": False, "error": f"Not a file: {raw_path}"}

    max_bytes = int(args.get("max_bytes", 5000))
    data = path.read_bytes()[:max_bytes]

    return {
        "ok": True,
        "path": str(path),
        "content": data.decode(errors="replace"),
        "truncated": len(data) == max_bytes,
    }


def compare_files(args: Dict[str, Any]) -> Dict[str, Any]:
    raw_path_a = str(args.get("path_a", "")).strip()
    raw_path_b = str(args.get("path_b", "")).strip()

    if not raw_path_a:
        return {"ok": False, "error": "Missing path_a", "path_a": raw_path_a, "path_b": raw_path_b}
    if not raw_path_b:
        return {"ok": False, "error": "Missing path_b", "path_a": raw_path_a, "path_b": raw_path_b}

    path_a = Path(raw_path_a)
    path_b = Path(raw_path_b)

    if not path_a.exists():
        return {"ok": False, "error": f"File not found: {raw_path_a}", "path_a": raw_path_a, "path_b": raw_path_b}
    if not path_b.exists():
        return {"ok": False, "error": f"File not found: {raw_path_b}", "path_a": raw_path_a, "path_b": raw_path_b}
    if not path_a.is_file():
        return {"ok": False, "error": f"Not a file: {raw_path_a}", "path_a": raw_path_a, "path_b": raw_path_b}
    if not path_b.is_file():
        return {"ok": False, "error": f"Not a file: {raw_path_b}", "path_a": raw_path_a, "path_b": raw_path_b}

    max_bytes = _coerce_int(
        args.get("max_bytes", COMPARE_FILES_DEFAULT_MAX_BYTES),
        COMPARE_FILES_DEFAULT_MAX_BYTES,
        minimum=400,
        maximum=12_000,
    )

    try:
        size_a = path_a.stat().st_size
        size_b = path_b.stat().st_size
        sample_a, truncated_a = _read_compare_sample(path_a, max_bytes)
        sample_b, truncated_b = _read_compare_sample(path_b, max_bytes)
        same_file = path_a.resolve() == path_b.resolve()
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Could not compare files: {exc}",
            "path_a": str(path_a),
            "path_b": str(path_b),
        }

    preview_truncated = truncated_a or truncated_b
    text_comparable = _is_probably_text_bytes(sample_a) and _is_probably_text_bytes(sample_b)

    if same_file:
        differ = False
    elif size_a != size_b:
        differ = True
    else:
        try:
            differ = not _files_are_identical(path_a, path_b)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Could not compare files: {exc}",
                "path_a": str(path_a),
                "path_b": str(path_b),
            }

    diff_preview: List[str] = []
    diff_stats = {
        "changed_sections": 0,
        "added_lines": 0,
        "removed_lines": 0,
    }

    if text_comparable:
        text_a = sample_a.decode(errors="replace")
        text_b = sample_b.decode(errors="replace")
        lines_a = text_a.splitlines()
        lines_b = text_b.splitlines()
        diff_stats = _collect_diff_stats(lines_a, lines_b)
        if differ:
            diff_preview = _build_compact_diff_preview(path_a, path_b, text_a, text_b)

    if same_file:
        summary = "Both inputs point to the same file."
    elif not differ:
        summary = "Files are identical."
    elif text_comparable:
        if diff_preview:
            summary = (
                f"Files differ: {diff_stats['changed_sections']} changed section(s), "
                f"{diff_stats['added_lines']} added line(s), and {diff_stats['removed_lines']} removed line(s)."
            )
        else:
            summary = f"Files differ, but the first {max_bytes} bytes did not include the changed lines."
        if preview_truncated:
            summary += f" Preview limited to first {max_bytes} bytes per file."
    else:
        summary = "Files differ, but they appear binary or non-text so no line diff preview was produced."
        if preview_truncated:
            summary += f" Preview limited to first {max_bytes} bytes per file."

    return {
        "ok": True,
        "path_a": str(path_a),
        "path_b": str(path_b),
        "differ": differ,
        "summary": summary,
        "diff_preview": diff_preview,
        "comparison": {
            "same_file": same_file,
            "text_comparable": text_comparable,
            "preview_truncated": preview_truncated,
            "size_a": size_a,
            "size_b": size_b,
            "max_bytes": max_bytes,
            "changed_sections": diff_stats["changed_sections"],
            "added_lines": diff_stats["added_lines"],
            "removed_lines": diff_stats["removed_lines"],
        },
    }


def list_files(args: Dict[str, Any]) -> Dict[str, Any]:
    raw_path = str(args.get("path", "")).strip()
    if not raw_path:
        return {"ok": False, "error": "Missing path"}

    path = Path(raw_path)
    if not path.exists():
        return {"ok": False, "error": f"Path not found: {raw_path}"}
    if not path.is_dir():
        return {"ok": False, "error": f"Not a directory: {raw_path}"}

    recursive = _coerce_bool(args.get("recursive", False))
    max_entries = int(args.get("max_entries", 200))

    if recursive:
        items = list(path.rglob("*"))
    else:
        items = list(path.iterdir())

    items = items[:max_entries]

    entries: List[Dict[str, Any]] = []
    for item in items:
        try:
            entries.append(
                {
                    "name": item.name,
                    "path": str(item),
                    "type": "dir" if item.is_dir() else "file",
                }
            )
        except Exception:
            continue

    return {
        "ok": True,
        "path": str(path),
        "recursive": recursive,
        "count": len(entries),
        "entries": entries,
        "truncated": len(items) == max_entries,
    }


def search_files(args: Dict[str, Any]) -> Dict[str, Any]:
    raw_path = str(args.get("path", "")).strip()
    query = str(args.get("query", "")).strip()

    if not raw_path:
        return {"ok": False, "error": "Missing path"}
    if not query:
        return {"ok": False, "error": "Missing query"}

    path = Path(raw_path)
    if not path.exists():
        return {"ok": False, "error": f"Path not found: {raw_path}"}
    if not path.is_dir():
        return {"ok": False, "error": f"Not a directory: {raw_path}"}

    max_results = int(args.get("max_results", 100))
    lowered = query.lower()

    matches: List[Dict[str, Any]] = []
    for item in path.rglob("*"):
        if len(matches) >= max_results:
            break
        try:
            if lowered in item.name.lower():
                matches.append(
                    {
                        "name": item.name,
                        "path": str(item),
                        "type": "dir" if item.is_dir() else "file",
                    }
                )
        except Exception:
            continue

    return {
        "ok": True,
        "path": str(path),
        "query": query,
        "count": len(matches),
        "matches": matches,
        "truncated": len(matches) >= max_results,
    }


def inspect_project(args: Dict[str, Any]) -> Dict[str, Any]:
    raw_path = str(args.get("path", "")).strip()
    focus = str(args.get("focus", "")).strip()
    goal = str(args.get("goal", "")).strip()

    if not raw_path:
        return {"ok": False, "error": "Missing path"}

    path = Path(raw_path)
    if not path.exists():
        return {"ok": False, "error": f"Path not found: {raw_path}"}
    if not path.is_dir():
        return {"ok": False, "error": f"Not a directory: {raw_path}"}

    max_depth = _coerce_int(args.get("max_depth", 2), 2, minimum=0, maximum=6)
    max_entries = _coerce_int(args.get("max_entries", 120), 120, minimum=20, maximum=500)
    max_files_to_read = _coerce_int(args.get("max_files_to_read", 3), 3, minimum=0, maximum=8)
    max_bytes_per_file = _coerce_int(
        args.get("max_bytes_per_file", 1200),
        1200,
        minimum=200,
        maximum=4000,
    )
    top_k_relevant = _coerce_int(args.get("top_k_relevant", 3), 3, minimum=1, maximum=6)
    refresh = _coerce_bool(args.get("refresh", False))

    cache_key = _build_inspect_project_cache_key(
        path,
        focus,
        goal,
        max_depth,
        max_entries,
        max_files_to_read,
        max_bytes_per_file,
        top_k_relevant,
    )
    now = time.time()

    if not refresh:
        cached_result = _get_cached_inspection(cache_key, now)
        if cached_result is not None:
            return cached_result

    snapshot = _collect_project_snapshot(path, max_entries=max_entries, max_depth=max_depth)
    focus_terms = _parse_focus_terms(focus)

    ranked_candidates: List[Tuple[int, int, str, Path, List[str]]] = []
    for candidate in snapshot["file_candidates"]:
        score, reasons = _score_candidate_file(path, candidate, focus_terms)
        if score <= 0:
            continue

        try:
            depth = len(candidate.relative_to(path).parts)
        except ValueError:
            depth = 99

        ranked_candidates.append((score, depth, candidate.name.lower(), candidate, reasons))

    ranked_candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

    likely_files: List[Dict[str, Any]] = []
    for score, _, _, candidate, reasons in ranked_candidates[:6]:
        try:
            relative_path = str(candidate.relative_to(path))
        except ValueError:
            relative_path = candidate.name

        likely_files.append(
            {
                "path": str(candidate),
                "relative_path": relative_path,
                "score": score,
                "why": "; ".join(reasons) if reasons else "text file",
            }
        )

    recommended_files = _select_relevant_files(
        path,
        ranked_candidates,
        goal,
        snapshot,
        top_k_relevant,
    )

    preview_targets: List[Path] = []
    preview_seen: set[str] = set()
    for entry in recommended_files[:max_files_to_read]:
        preview_path = Path(str(entry.get("path", "")).strip())
        if str(preview_path) and str(preview_path) not in preview_seen:
            preview_targets.append(preview_path)
            preview_seen.add(str(preview_path))

    if not preview_targets:
        for _, _, _, candidate, _ in ranked_candidates[:max_files_to_read]:
            if str(candidate) in preview_seen:
                continue
            preview_targets.append(candidate)
            preview_seen.add(str(candidate))

    file_previews: List[Dict[str, Any]] = []
    for preview_target in preview_targets[:max_files_to_read]:
        preview = _read_preview(path, preview_target, max_bytes_per_file)
        if preview is not None:
            file_previews.append(preview)

    summary = _build_inspection_summary(
        path,
        focus,
        likely_files,
        recommended_files,
        snapshot,
    )
    selection_summary = _build_selection_summary(goal, recommended_files)

    result = {
        "ok": True,
        "path": str(path),
        "focus": focus,
        "goal": goal,
        "from_cache": False,
        "summary": summary,
        "selection_summary": selection_summary,
        "top_level": snapshot["top_level"],
        "sampled_directories": snapshot["directories"],
        "likely_files": likely_files,
        "recommended_files": recommended_files,
        "file_previews": file_previews,
        "skipped_directories": snapshot["skipped_directories"],
        "stats": {
            "scanned_entries": snapshot["scanned_entries"],
            "discovered_directories": snapshot["discovered_dirs"],
            "discovered_files": snapshot["discovered_files"],
            "max_depth": max_depth,
            "max_entries": max_entries,
            "top_k_relevant": top_k_relevant,
            "truncated": snapshot["truncated"],
        },
        "cache": _build_cache_metadata(hit=False, age_seconds=0.0),
    }
    _store_cached_inspection(cache_key, result, now)
    return result


READ_FILE_TOOL = {
    "name": "read_file",
    "description": "Read a local text file.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "max_bytes": {"type": "integer"}
        },
        "required": ["path"]
    },
    "func": read_file,
}

COMPARE_FILES_TOOL = {
    "name": "compare_files",
    "description": (
        "Compare two local files in a read-only way and return whether they differ, "
        "a short summary, and a compact diff preview for text files."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path_a": {"type": "string"},
            "path_b": {"type": "string"},
            "max_bytes": {"type": "integer"}
        },
        "required": ["path_a", "path_b"]
    },
    "func": compare_files,
}

LIST_FILES_TOOL = {
    "name": "list_files",
    "description": "List files and folders in a directory.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "recursive": {"type": "boolean"},
            "max_entries": {"type": "integer"}
        },
        "required": ["path"]
    },
    "func": list_files,
}

SEARCH_FILES_TOOL = {
    "name": "search_files",
    "description": "Search for files or folders by name inside a directory.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "query": {"type": "string"},
            "max_results": {"type": "integer"}
        },
        "required": ["path", "query"]
    },
    "func": search_files,
}

APPLY_APPROVED_EDITS_TOOL = {
    "name": "apply_approved_edits",
    "description": (
        "Apply exact approved text edits to existing workspace files only after explicit approval. "
        "Requires approval_status=approved, creates .bak backups before each write, and rejects unapproved or invalid edit payloads."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "review_bundle": {"type": "object"},
            "approved_edits": {
                "type": "array",
                "items": {"type": "object"}
            },
            "approved_payload": {
                "type": "array",
                "items": {"type": "object"}
            },
            "approval_status": {"type": "string"},
            "target_files": {
                "type": "array",
                "items": {"type": "string"}
            },
            "max_files": {"type": "integer"},
            "max_bytes_per_file": {"type": "integer"}
        },
        "required": []
    },
    "func": apply_approved_edits,
}


INSPECT_PROJECT_TOOL = {
    "name": "inspect_project",
    "description": (
        "Inspect a local project or folder in one read-only pass. "
        "Lists top-level entries, identifies likely files, recommends the most relevant files for the current goal, "
        "reads short previews for a few top candidates, and reuses a short in-session cache unless refresh=true."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "focus": {"type": "string"},
            "goal": {"type": "string"},
            "max_depth": {"type": "integer"},
            "max_entries": {"type": "integer"},
            "max_files_to_read": {"type": "integer"},
            "max_bytes_per_file": {"type": "integer"},
            "top_k_relevant": {"type": "integer"},
            "refresh": {"type": "boolean"}
        },
        "required": ["path"]
    },
    "func": inspect_project,
}

def _trim_cache_text(value: Any, limit: int = 600) -> str:
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _compact_cache_entry_list(values: Any, limit: int) -> List[Dict[str, Any]]:
    if not isinstance(values, list):
        return []

    items: List[Dict[str, Any]] = []
    for value in values[:limit]:
        if not isinstance(value, dict):
            continue

        item: Dict[str, Any] = {}
        for key in ("name", "path", "type", "relative_path", "why"):
            if key in value:
                item[key] = _trim_cache_text(value.get(key, ""), limit=280)
        if "preview" in value:
            item["preview"] = _trim_cache_text(value.get("preview", ""), limit=500)
        if "score" in value:
            try:
                item["score"] = int(value.get("score", 0))
            except (TypeError, ValueError):
                item["score"] = 0
        if "truncated" in value:
            item["truncated"] = bool(value.get("truncated", False))
        if item:
            items.append(item)
    return items


def _compact_inspection_result_for_persistence(result: Any) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {"ok": False}

    stats = result.get("stats", {}) if isinstance(result.get("stats", {}), dict) else {}
    cache = result.get("cache", {}) if isinstance(result.get("cache", {}), dict) else {}

    return {
        "ok": bool(result.get("ok", False)),
        "path": _trim_cache_text(result.get("path", ""), limit=280),
        "focus": _trim_cache_text(result.get("focus", ""), limit=200),
        "goal": _trim_cache_text(result.get("goal", ""), limit=240),
        "from_cache": False,
        "summary": _trim_cache_text(result.get("summary", ""), limit=500),
        "selection_summary": _trim_cache_text(result.get("selection_summary", ""), limit=260),
        "top_level": _compact_cache_entry_list(result.get("top_level", []), limit=12),
        "sampled_directories": _compact_cache_entry_list(result.get("sampled_directories", []), limit=12),
        "likely_files": _compact_cache_entry_list(result.get("likely_files", []), limit=6),
        "recommended_files": _compact_cache_entry_list(result.get("recommended_files", []), limit=6),
        "file_previews": _compact_cache_entry_list(result.get("file_previews", []), limit=3),
        "skipped_directories": [
            _trim_cache_text(value, limit=280)
            for value in result.get("skipped_directories", [])[:8]
        ],
        "stats": {
            "scanned_entries": _coerce_int(stats.get("scanned_entries", 0), 0, minimum=0, maximum=1000),
            "discovered_directories": _coerce_int(stats.get("discovered_directories", 0), 0, minimum=0, maximum=1000),
            "discovered_files": _coerce_int(stats.get("discovered_files", 0), 0, minimum=0, maximum=2000),
            "max_depth": _coerce_int(stats.get("max_depth", 2), 2, minimum=0, maximum=6),
            "max_entries": _coerce_int(stats.get("max_entries", 120), 120, minimum=20, maximum=500),
            "top_k_relevant": _coerce_int(stats.get("top_k_relevant", 3), 3, minimum=1, maximum=6),
            "truncated": bool(stats.get("truncated", False)),
        },
        "cache": {
            "hit": bool(cache.get("hit", False)),
            "age_seconds": float(cache.get("age_seconds", 0.0) or 0.0),
            "ttl_seconds": _coerce_int(cache.get("ttl_seconds", INSPECT_PROJECT_CACHE_TTL_SECONDS), INSPECT_PROJECT_CACHE_TTL_SECONDS, minimum=1, maximum=INSPECT_PROJECT_CACHE_TTL_SECONDS),
            "expires_in_seconds": float(cache.get("expires_in_seconds", INSPECT_PROJECT_CACHE_TTL_SECONDS) or INSPECT_PROJECT_CACHE_TTL_SECONDS),
        },
    }


def export_inspect_project_cache(max_entries: int = 8) -> List[Dict[str, Any]]:
    max_entries = _coerce_int(max_entries, 8, minimum=1, maximum=12)
    now = time.time()
    _prune_inspect_project_cache(now)

    exported: List[Dict[str, Any]] = []
    ordered_items = sorted(
        INSPECT_PROJECT_CACHE.items(),
        key=lambda item: float(item[1].get("last_used_at", 0)),
        reverse=True,
    )

    for key, entry in ordered_items[:max_entries]:
        (
            path,
            focus,
            goal,
            max_depth,
            max_entries_value,
            max_files_to_read,
            max_bytes_per_file,
            top_k_relevant,
        ) = key
        exported.append(
            {
                "path": str(path),
                "focus": str(focus),
                "goal": str(goal),
                "max_depth": int(max_depth),
                "max_entries": int(max_entries_value),
                "max_files_to_read": int(max_files_to_read),
                "max_bytes_per_file": int(max_bytes_per_file),
                "top_k_relevant": int(top_k_relevant),
                "created_at": float(entry.get("created_at", now)),
                "last_used_at": float(entry.get("last_used_at", now)),
                "result": _compact_inspection_result_for_persistence(entry.get("result", {})),
            }
        )

    return exported


def import_inspect_project_cache(entries: Any) -> int:
    if not isinstance(entries, list):
        return 0

    now = time.time()
    loaded = 0

    for value in entries[:12]:
        if not isinstance(value, dict):
            continue

        path_text = str(value.get("path", "")).strip()
        if not path_text:
            continue

        created_at = float(value.get("created_at", now) or now)
        if now - created_at > INSPECT_PROJECT_CACHE_TTL_SECONDS:
            continue

        result = _compact_inspection_result_for_persistence(value.get("result", {}))
        if not result.get("ok", False):
            continue

        max_depth = _coerce_int(value.get("max_depth", result.get("stats", {}).get("max_depth", 2)), 2, minimum=0, maximum=6)
        max_entries_value = _coerce_int(value.get("max_entries", result.get("stats", {}).get("max_entries", 120)), 120, minimum=20, maximum=500)
        max_files_to_read = _coerce_int(value.get("max_files_to_read", len(result.get("file_previews", [])) or 3), 3, minimum=0, maximum=8)
        max_bytes_per_file = _coerce_int(value.get("max_bytes_per_file", 1200), 1200, minimum=200, maximum=4000)
        top_k_relevant = _coerce_int(value.get("top_k_relevant", result.get("stats", {}).get("top_k_relevant", 3)), 3, minimum=1, maximum=6)

        cache_key = _build_inspect_project_cache_key(
            Path(path_text),
            str(value.get("focus", "")),
            str(value.get("goal", "")),
            max_depth,
            max_entries_value,
            max_files_to_read,
            max_bytes_per_file,
            top_k_relevant,
        )
        INSPECT_PROJECT_CACHE[cache_key] = {
            "created_at": created_at,
            "last_used_at": float(value.get("last_used_at", created_at) or created_at),
            "result": result,
        }
        loaded += 1

    _prune_inspect_project_cache(now)
    return loaded


def clear_inspect_project_cache():
    INSPECT_PROJECT_CACHE.clear()