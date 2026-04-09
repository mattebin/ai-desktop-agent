from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import urlparse


WINDOWS_EXECUTABLE_EXTENSIONS = {
    ".bat",
    ".cmd",
    ".com",
    ".exe",
    ".lnk",
    ".msi",
    ".ps1",
    ".psm1",
    ".vbs",
}
WINDOWS_DOCUMENT_EXTENSIONS = {
    ".csv",
    ".doc",
    ".docm",
    ".docx",
    ".odp",
    ".ods",
    ".odt",
    ".pdf",
    ".ppt",
    ".pptm",
    ".pptx",
    ".rtf",
    ".xls",
    ".xlsm",
    ".xlsx",
}
WINDOWS_IMAGE_MEDIA_EXTENSIONS = {
    ".avi",
    ".bmp",
    ".gif",
    ".heic",
    ".jfif",
    ".jpeg",
    ".jpg",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".png",
    ".svg",
    ".tif",
    ".tiff",
    ".wav",
    ".webm",
    ".webp",
}
WINDOWS_TEXT_CODE_EXTENSIONS = {
    ".cfg",
    ".conf",
    ".css",
    ".htm",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".log",
    ".md",
    ".ps1",
    ".py",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
WINDOWS_URL_SCHEMES = {"file", "http", "https", "mailto"}

TARGET_TYPE_ALIASES = {
    "app": "executable_program",
    "application": "executable_program",
    "directory": "folder_directory",
    "document": "document_file",
    "exe": "executable_program",
    "executable": "executable_program",
    "file": "unknown_ambiguous_path",
    "folder": "folder_directory",
    "image": "image_media_file",
    "media": "image_media_file",
    "program": "executable_program",
    "text": "text_code_file",
    "text_file": "text_code_file",
    "text/code": "text_code_file",
    "url": "url_web_resource",
    "web": "url_web_resource",
}
VALID_TARGET_TYPES = {
    "document_file",
    "executable_program",
    "folder_directory",
    "image_media_file",
    "text_code_file",
    "unknown_ambiguous_path",
    "url_web_resource",
}
VALID_STRATEGY_FAMILIES = {
    "association_open",
    "bounded_fallback",
    "executable_launch",
    "explorer_assisted_ui",
    "focus_existing_window",
    "url_browser",
}

_VIEWER_HINTS = {
    ".csv": {"processes": ["excel.exe", "notepad.exe", "code.exe"], "titles": ["Excel", "Notepad", "Visual Studio Code"]},
    ".doc": {"processes": ["winword.exe", "wordpad.exe", "soffice.bin"], "titles": ["Word", "WordPad", "LibreOffice Writer"]},
    ".docx": {"processes": ["winword.exe", "wordpad.exe", "soffice.bin"], "titles": ["Word", "WordPad", "LibreOffice Writer"]},
    ".gif": {"processes": ["microsoft.photos.exe", "photos.exe", "mspaint.exe"], "titles": ["Photos", "Paint"]},
    ".jpeg": {"processes": ["microsoft.photos.exe", "photos.exe", "mspaint.exe"], "titles": ["Photos", "Paint"]},
    ".jpg": {"processes": ["microsoft.photos.exe", "photos.exe", "mspaint.exe"], "titles": ["Photos", "Paint"]},
    ".json": {"processes": ["code.exe", "notepad.exe", "notepad++.exe"], "titles": ["Visual Studio Code", "Notepad", "Notepad++"]},
    ".md": {"processes": ["code.exe", "notepad.exe", "notepad++.exe"], "titles": ["Visual Studio Code", "Notepad", "Notepad++"]},
    ".pdf": {"processes": ["acrord32.exe", "msedge.exe", "sumatrapdf.exe", "foxitpdfreader.exe"], "titles": ["Adobe", "Edge", "PDF", "Sumatra", "Foxit"]},
    ".png": {"processes": ["microsoft.photos.exe", "photos.exe", "mspaint.exe"], "titles": ["Photos", "Paint"]},
    ".ppt": {"processes": ["powerpnt.exe", "soffice.bin"], "titles": ["PowerPoint", "LibreOffice Impress"]},
    ".pptx": {"processes": ["powerpnt.exe", "soffice.bin"], "titles": ["PowerPoint", "LibreOffice Impress"]},
    ".py": {"processes": ["code.exe", "notepad.exe", "pycharm64.exe"], "titles": ["Visual Studio Code", "Notepad", "PyCharm"]},
    ".svg": {"processes": ["microsoft.photos.exe", "photos.exe", "msedge.exe", "mspaint.exe"], "titles": ["Photos", "Edge", "Paint"]},
    ".txt": {"processes": ["notepad.exe", "code.exe", "notepad++.exe"], "titles": ["Notepad", "Visual Studio Code", "Notepad++"]},
    ".xls": {"processes": ["excel.exe", "soffice.bin"], "titles": ["Excel", "LibreOffice Calc"]},
    ".xlsx": {"processes": ["excel.exe", "soffice.bin"], "titles": ["Excel", "LibreOffice Calc"]},
}


def _trim_text(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _normalized_words(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _pathish(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return any(token in text for token in ("\\", "/", ":")) or bool(Path(text).suffix)


def _normalize_target_type(value: Any) -> str:
    text = _normalized_words(value).replace("-", "_").replace("/", "_").replace(" ", "_")
    normalized = TARGET_TYPE_ALIASES.get(text, text)
    return normalized if normalized in VALID_TARGET_TYPES else ""


def _normalize_strategy_family(value: Any) -> str:
    text = _normalized_words(value).replace("-", "_").replace("/", "_").replace(" ", "_")
    aliases = {
        "association": "association_open",
        "association_open": "association_open",
        "auto": "",
        "browser": "url_browser",
        "desktop_ui": "explorer_assisted_ui",
        "explorer": "explorer_assisted_ui",
        "fallback": "bounded_fallback",
        "focus": "focus_existing_window",
        "focus_existing": "focus_existing_window",
        "focus_existing_window": "focus_existing_window",
        "launch": "executable_launch",
        "program_launch": "executable_launch",
        "ui": "explorer_assisted_ui",
        "url": "url_browser",
    }
    normalized = aliases.get(text, text)
    return normalized if normalized in VALID_STRATEGY_FAMILIES else ""


def _viewer_hints_for_suffix(suffix: str) -> Dict[str, List[str]]:
    hints = _VIEWER_HINTS.get(str(suffix or "").lower(), {})
    processes = [str(item).strip().lower() for item in list(hints.get("processes", [])) if str(item).strip()]
    titles = [str(item).strip() for item in list(hints.get("titles", [])) if str(item).strip()]
    return {"processes": processes[:6], "titles": titles[:6]}


def classify_open_target(
    target: str,
    *,
    cwd: str = "",
    explicit_target_type: str = "",
) -> Dict[str, Any]:
    rendered_target = str(target or "").strip()
    if not rendered_target:
        return {
            "target": "",
            "normalized_target": "",
            "target_classification": "unknown_ambiguous_path",
            "exists": False,
            "missing": True,
            "is_file": False,
            "is_directory": False,
            "suffix": "",
            "basename": "",
            "stem": "",
            "path_kind": "empty",
            "viewer_process_hints": [],
            "viewer_title_hints": [],
            "target_signature": "",
        }

    explicit = _normalize_target_type(explicit_target_type)
    parsed = urlparse(rendered_target)
    if parsed.scheme.lower() in WINDOWS_URL_SCHEMES:
        url_target = rendered_target
        return {
            "target": rendered_target,
            "normalized_target": url_target,
            "target_classification": explicit or "url_web_resource",
            "exists": True,
            "missing": False,
            "is_file": False,
            "is_directory": False,
            "suffix": "",
            "basename": _trim_text(Path(parsed.path).name or parsed.netloc or rendered_target, limit=160),
            "stem": _trim_text(Path(parsed.path).stem or parsed.netloc or rendered_target, limit=160),
            "parent_name": "",
            "path_kind": "url",
            "viewer_process_hints": ["msedge.exe", "chrome.exe", "firefox.exe"],
            "viewer_title_hints": ["Edge", "Chrome", "Firefox"],
            "target_signature": url_target.lower(),
        }

    raw_path = Path(rendered_target)
    if not raw_path.is_absolute() and cwd:
        raw_path = Path(cwd) / raw_path
    normalized_path = str(raw_path.expanduser().resolve())
    exists = raw_path.exists()
    is_directory = exists and raw_path.is_dir()
    is_file = exists and raw_path.is_file()
    suffix = raw_path.suffix.lower()
    basename = raw_path.name or rendered_target
    stem = raw_path.stem or basename
    parent_name = raw_path.parent.name if raw_path.parent and raw_path.parent.name else ""
    viewer_hints = _viewer_hints_for_suffix(suffix)

    if explicit:
        target_classification = explicit
    elif is_directory:
        target_classification = "folder_directory"
    elif suffix in WINDOWS_EXECUTABLE_EXTENSIONS:
        target_classification = "executable_program"
    elif suffix in WINDOWS_IMAGE_MEDIA_EXTENSIONS:
        target_classification = "image_media_file"
    elif suffix in WINDOWS_TEXT_CODE_EXTENSIONS:
        target_classification = "text_code_file"
    elif suffix in WINDOWS_DOCUMENT_EXTENSIONS:
        target_classification = "document_file"
    elif is_file:
        target_classification = "document_file"
    else:
        target_classification = "unknown_ambiguous_path"

    if target_classification == "folder_directory":
        viewer_hints = {"processes": ["explorer.exe"], "titles": ["File Explorer", basename, stem]}
    elif target_classification == "executable_program":
        exe_name = basename.lower()
        viewer_hints = {
            "processes": [exe_name if exe_name.endswith(".exe") else f"{stem.lower()}.exe"],
            "titles": [stem],
        }
    elif target_classification == "url_web_resource":
        viewer_hints = {"processes": ["msedge.exe", "chrome.exe", "firefox.exe"], "titles": ["Edge", "Chrome", "Firefox"]}

    title_hints = [hint for hint in [basename, stem, *viewer_hints.get("titles", [])] if str(hint).strip()]
    process_hints = [hint for hint in viewer_hints.get("processes", []) if str(hint).strip()]
    return {
        "target": rendered_target,
        "normalized_target": normalized_path,
        "target_classification": target_classification,
        "exists": bool(exists),
        "missing": not bool(exists),
        "is_file": bool(is_file),
        "is_directory": bool(is_directory),
        "suffix": suffix,
        "basename": basename,
        "stem": stem,
        "parent_name": parent_name,
        "path_kind": "directory" if is_directory else "file" if is_file else "path" if _pathish(rendered_target) else "ambiguous",
        "viewer_process_hints": process_hints[:6],
        "viewer_title_hints": title_hints[:6],
        "target_signature": normalized_path.lower(),
    }


def open_target_signature(target_info: Dict[str, Any] | None) -> str:
    safe_target = target_info if isinstance(target_info, dict) else {}
    return _trim_text(
        safe_target.get("target_signature", "")
        or safe_target.get("normalized_target", "")
        or safe_target.get("target", ""),
        limit=240,
    ).lower()


def infer_open_request_preferences(goal: str, args: Dict[str, Any] | None = None) -> Dict[str, Any]:
    safe_args = args if isinstance(args, dict) else {}
    explicit_method = _normalize_strategy_family(
        safe_args.get("preferred_method")
        or safe_args.get("requested_method")
        or safe_args.get("strategy_family")
    )
    explicit_target_type = _normalize_target_type(safe_args.get("target_type") or safe_args.get("target_classification"))
    normalized_goal = _normalized_words(goal)
    force_strategy_switch = bool(safe_args.get("force_strategy_switch", False))
    if any(
        phrase in normalized_goal
        for phrase in (
            "another method",
            "another way",
            "different method",
            "different way",
            "desktop path",
            "ui path",
            "through explorer",
            "through file explorer",
            "instead",
            "fallback",
        )
    ):
        force_strategy_switch = True

    preferred_method = explicit_method
    if not preferred_method:
        if "explorer" in normalized_goal or "file explorer" in normalized_goal or "desktop ui" in normalized_goal:
            preferred_method = "explorer_assisted_ui"
        elif "focus existing" in normalized_goal or "switch back to" in normalized_goal or "bring back" in normalized_goal:
            preferred_method = "focus_existing_window"
        elif "default app" in normalized_goal or "associated app" in normalized_goal or "open with" in normalized_goal:
            preferred_method = "association_open"
        elif "launch" in normalized_goal or "run the exe" in normalized_goal or "start the app" in normalized_goal:
            preferred_method = "executable_launch"
        elif "browser" in normalized_goal or "url" in normalized_goal:
            preferred_method = "url_browser"

    return {
        "preferred_method": preferred_method,
        "target_type": explicit_target_type,
        "force_strategy_switch": force_strategy_switch,
    }


def choose_windows_open_strategy(
    target_info: Dict[str, Any] | None,
    *,
    preferred_method: str = "",
    avoid_strategy_families: Iterable[str] | None = None,
    existing_window_match: bool = False,
    force_strategy_switch: bool = False,
) -> Dict[str, Any]:
    safe_target = target_info if isinstance(target_info, dict) else {}
    target_class = _trim_text(safe_target.get("target_classification", ""), limit=80)
    normalized_preference = _normalize_strategy_family(preferred_method)
    avoid = {
        value
        for value in (
            _normalize_strategy_family(item)
            for item in list(avoid_strategy_families or [])
        )
        if value
    }

    candidate_order: List[str]
    if target_class == "executable_program":
        candidate_order = ["focus_existing_window", "executable_launch", "explorer_assisted_ui", "bounded_fallback"]
    elif target_class == "folder_directory":
        candidate_order = ["focus_existing_window", "explorer_assisted_ui", "bounded_fallback"]
    elif target_class == "url_web_resource":
        candidate_order = ["focus_existing_window", "url_browser", "association_open", "bounded_fallback"]
    elif target_class in {"document_file", "image_media_file", "text_code_file"}:
        candidate_order = ["focus_existing_window", "association_open", "explorer_assisted_ui", "bounded_fallback"]
    else:
        candidate_order = ["focus_existing_window", "association_open", "explorer_assisted_ui", "bounded_fallback"]

    if not existing_window_match:
        candidate_order = [item for item in candidate_order if item != "focus_existing_window"] + ["focus_existing_window"]

    if target_class == "executable_program" and not safe_target.get("exists", False):
        candidate_order = ["executable_launch", "bounded_fallback"]

    if normalized_preference:
        candidate_order = [normalized_preference, *[item for item in candidate_order if item != normalized_preference]]

    if force_strategy_switch:
        candidate_order = [item for item in candidate_order if item not in avoid] + [item for item in candidate_order if item in avoid]

    selected = ""
    for family in candidate_order:
        if family in avoid:
            continue
        if family == "focus_existing_window" and not existing_window_match:
            continue
        if family == "url_browser" and target_class != "url_web_resource":
            continue
        if family == "executable_launch" and target_class != "executable_program":
            continue
        if family == "association_open" and target_class == "folder_directory":
            continue
        selected = family
        break

    if not selected:
        selected = normalized_preference or candidate_order[0]

    reason = {
        "association_open": "Use Windows file association semantics for non-executable content.",
        "bounded_fallback": "Use a materially different bounded fallback after prior open-path issues.",
        "executable_launch": "Launch the executable program directly.",
        "explorer_assisted_ui": "Use Explorer to surface the target in a visible Windows shell flow.",
        "focus_existing_window": "Prefer a likely existing viewer or app window before starting another open path.",
        "url_browser": "Use the system browser association for the URL target.",
    }.get(selected, "Choose the safest bounded Windows open path for the target.")
    return {
        "strategy_family": selected,
        "candidate_families": candidate_order,
        "avoided_families": sorted(avoid),
        "reason": reason,
        "preferred_method": normalized_preference,
        "force_strategy_switch": bool(force_strategy_switch),
    }
