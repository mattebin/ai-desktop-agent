from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = PROJECT_ROOT / "skills"
_FRONTMATTER_PATTERN = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)", re.DOTALL)


def _read_skill_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _split_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    if not text:
        return {}, ""
    match = _FRONTMATTER_PATTERN.match(text)
    if not match:
        return {}, text
    try:
        frontmatter = yaml.safe_load(match.group(1)) or {}
    except Exception:
        frontmatter = {}
    if not isinstance(frontmatter, dict):
        frontmatter = {}
    return frontmatter, text[match.end() :]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    return slug.strip("-")


def _titleize(value: str) -> str:
    return " ".join(part.capitalize() for part in value.replace("-", " ").replace("_", " ").split()).strip()


def _heading_title(body: str) -> str:
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def _clean_lines(lines: Iterable[str]) -> List[str]:
    cleaned: List[str] = []
    for raw_line in lines:
        line = str(raw_line).strip()
        if not line:
            continue
        cleaned.append(line)
    return cleaned


def _extract_section(body: str, heading_names: Iterable[str]) -> List[str]:
    targets = {name.strip().lower() for name in heading_names if str(name or "").strip()}
    if not targets:
        return []
    lines = body.splitlines()
    capture = False
    collected: List[str] = []
    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().lower()
            if capture:
                break
            capture = heading in targets
            continue
        if capture:
            collected.append(stripped)
    return _clean_lines(collected)


def _first_paragraph(body: str) -> str:
    paragraph_lines: List[str] = []
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("#"):
            continue
        if not stripped:
            if paragraph_lines:
                break
            continue
        if stripped.startswith("- ") or re.match(r"^\d+\.\s+", stripped):
            if paragraph_lines:
                break
            continue
        paragraph_lines.append(stripped)
    return " ".join(paragraph_lines).strip()


def _normalize_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _normalize_section_items(lines: Iterable[str]) -> List[str]:
    items: List[str] = []
    for raw_line in lines:
        line = str(raw_line).strip()
        if not line:
            continue
        line = re.sub(r"^[-*]\s+", "", line)
        line = re.sub(r"^\d+\.\s+", "", line)
        line = line.strip()
        if line:
            items.append(line)
    return items


def _default_skill_prompt(title: str, relative_path: str, purpose: str) -> str:
    if purpose:
        return (
            f"Use the repo-local skill in `{relative_path}` to guide this task. "
            f"Follow its bounded workflow and focus on: {purpose}"
        )
    return f"Use the repo-local skill in `{relative_path}` to guide this task and follow its bounded workflow."


def _skill_payload(path: Path) -> Dict[str, Any]:
    text = _read_skill_text(path)
    if not text:
        return {}

    frontmatter, body = _split_frontmatter(text)
    relative_path = str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
    slug = _slugify(frontmatter.get("slug") or path.stem.replace("_", "-")) or _slugify(path.stem)
    title = str(frontmatter.get("title") or _heading_title(body) or _titleize(slug)).strip()
    purpose_lines = _extract_section(body, ["Purpose"])
    purpose = str(frontmatter.get("purpose") or " ".join(purpose_lines)).strip()
    description = str(frontmatter.get("description") or frontmatter.get("summary") or _first_paragraph(body) or purpose).strip()
    when_lines = _extract_section(body, ["Use it when", "Signals", "Check for"])
    when_to_use = _normalize_list(frontmatter.get("whenToUse")) or _normalize_section_items(when_lines)
    command_name = _slugify(frontmatter.get("command") or slug) or slug
    aliases = _normalize_list(frontmatter.get("aliases"))
    tags = _normalize_list(frontmatter.get("tags"))
    prompt_text = str(frontmatter.get("prompt") or _default_skill_prompt(title=title, relative_path=relative_path, purpose=purpose)).strip()

    return {
        "slug": slug,
        "title": title,
        "description": description,
        "purpose": purpose,
        "whenToUse": when_to_use,
        "path": str(path),
        "relativePath": relative_path,
        "commandName": command_name,
        "aliases": aliases,
        "promptText": prompt_text,
        "argumentHint": str(frontmatter.get("argumentHint", "")).strip(),
        "tags": tags,
        "source": "repo_skill",
    }


def list_skill_catalog() -> List[Dict[str, Any]]:
    if not SKILLS_DIR.exists():
        return []

    items: List[Dict[str, Any]] = []
    for path in sorted(SKILLS_DIR.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        payload = _skill_payload(path)
        if payload:
            items.append(payload)
    return items
