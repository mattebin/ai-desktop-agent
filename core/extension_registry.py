from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXTENSION_DIRS = [
    PROJECT_ROOT / ".agents" / "extensions",
]
SUPPORTED_MANIFEST_SUFFIXES = {".json", ".yaml", ".yml"}
SUPPORTED_LOCAL_ACTIONS = {
    "approve",
    "help",
    "new-chat",
    "refresh",
    "reject",
    "connect-gmail",
    "show-email-drafts",
    "show-email-status",
    "show-extensions",
    "show-runtime",
    "show-skills",
    "show-inbox",
    "show-tools",
    "toggle-details",
    "toggle-theme",
}


def _trim_text(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _slugify(value: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    return slug.strip("-")


def _titleize(value: str) -> str:
    return " ".join(part.capitalize() for part in value.replace("-", " ").replace("_", " ").split()).strip()


def _normalize_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _read_manifest(path: Path) -> Dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    try:
        if path.suffix.lower() == ".json":
            payload = json.loads(text)
        else:
            payload = yaml.safe_load(text)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _iter_manifest_paths() -> Iterable[Path]:
    for directory in EXTENSION_DIRS:
        if not directory.exists():
            continue
        for path in sorted(directory.iterdir()):
            if not path.is_file():
                continue
            if path.name.lower() == "readme.md":
                continue
            if path.suffix.lower() not in SUPPORTED_MANIFEST_SUFFIXES:
                continue
            yield path


def _normalize_command(
    value: Any,
    *,
    extension_slug: str,
    relative_path: str,
    default_category: str,
) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    command_type = _trim_text(value.get("type", "prompt"), limit=20).lower() or "prompt"
    name = _slugify(value.get("name") or value.get("command") or value.get("title"))
    if not name:
        return {}

    description = _trim_text(value.get("description", "") or value.get("summary", ""), limit=220)
    aliases = _normalize_list(value.get("aliases"))
    argument_hint = _trim_text(value.get("argumentHint", "") or value.get("argument_hint", ""), limit=80)
    category = _trim_text(value.get("category", "") or default_category, limit=40) or "extension"

    payload: Dict[str, Any] = {
        "type": command_type,
        "name": name,
        "description": description or f"Local extension command from {extension_slug}.",
        "aliases": aliases,
        "argumentHint": argument_hint,
        "category": category,
        "source": "local_extension",
        "extensionSlug": extension_slug,
        "relativePath": relative_path,
    }

    if command_type == "local":
        action = _trim_text(value.get("action", ""), limit=40)
        if action not in SUPPORTED_LOCAL_ACTIONS:
            return {}
        payload["action"] = action
        return payload

    prompt_text = _trim_text(value.get("promptText", "") or value.get("prompt", ""), limit=4000)
    if not prompt_text:
        return {}
    payload["type"] = "prompt"
    payload["promptText"] = prompt_text
    return payload


def _extension_payload(path: Path) -> Dict[str, Any]:
    manifest = _read_manifest(path)
    if not manifest or manifest.get("enabled", True) is False:
        return {}

    relative_path = str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
    slug = _slugify(manifest.get("slug") or path.stem)
    if not slug:
        return {}

    title = _trim_text(manifest.get("title", "") or _titleize(slug), limit=120)
    description = _trim_text(manifest.get("description", "") or manifest.get("summary", ""), limit=240)
    category = _trim_text(manifest.get("category", "extension"), limit=40) or "extension"

    commands: List[Dict[str, Any]] = []
    seen_names: set[str] = set()
    for raw_command in list(manifest.get("commands", [])):
        command = _normalize_command(
            raw_command,
            extension_slug=slug,
            relative_path=relative_path,
            default_category=category,
        )
        command_name = str(command.get("name", "")).strip().lower()
        if not command_name or command_name in seen_names:
            continue
        seen_names.add(command_name)
        commands.append(command)

    if not commands:
        return {}

    return {
        "slug": slug,
        "title": title or _titleize(slug),
        "description": description or f"Local extension manifest from {relative_path}.",
        "path": str(path),
        "relativePath": relative_path,
        "source": "local_extension",
        "commandCount": len(commands),
        "commands": commands,
    }


def list_extension_catalog() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for path in _iter_manifest_paths():
        payload = _extension_payload(path)
        if payload:
            items.append(payload)
    return items


def list_extension_commands() -> List[Dict[str, Any]]:
    commands: List[Dict[str, Any]] = []
    for extension in list_extension_catalog():
        commands.extend(list(extension.get("commands", [])))
    return commands
