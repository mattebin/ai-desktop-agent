"""Fast-path direct-action resolver.

Matches simple, unambiguous user goals to a single tool call so the
execution loop can skip the LLM planning round-trip entirely.

Design principles
─────────────────
* **Conservative** — only match when the intent is crystal-clear.
  Any ambiguity → return ``None`` and let the LLM plan.
* **Safe** — the fast path produces a plan dict identical to what
  ``plan_next_action`` returns.  All existing safety guards, approval
  gates, and state recording still run.  We only skip the LLM call.
* **Composable** — each matcher is a small pure function.  Adding a
  new pattern is a one-function change.
"""

from __future__ import annotations

import re
from typing import Any, Dict

# ── helpers ──────────────────────────────────────────────────────

_PATH_RE = re.compile(
    r"""
    (?:                             # drive-letter path
        [A-Za-z]:[\\\/]             # C:\ or C:/
        [^\s"'<>|*?]+               # rest of path
    )
    |(?:                            # UNC path
        \\\\[^\s"'<>|*?]+
    )
    |(?:                            # unix-ish absolute (rarely on Windows but support it)
        (?<![:/])/[^\s"'<>|*?]+    # negative lookbehind avoids matching ://
    )
    """,
    re.VERBOSE,
)

_URL_RE = re.compile(
    r"https?://[^\s\"'<>]+|www\.[^\s\"'<>]+",
    re.IGNORECASE,
)

_KEY_ALIASES: Dict[str, str] = {
    "enter": "Return", "return": "Return",
    "esc": "Escape", "escape": "Escape",
    "tab": "Tab",
    "space": "Space", "spacebar": "Space",
    "backspace": "Backspace", "delete": "Delete", "del": "Delete",
    "home": "Home", "end": "End",
    "pageup": "Page_Up", "page up": "Page_Up",
    "pagedown": "Page_Down", "page down": "Page_Down",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4",
    "f5": "F5", "f6": "F6", "f7": "F7", "f8": "F8",
    "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
}

_MODIFIER_ALIASES: Dict[str, str] = {
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
}

_TARGET_TYPE_MAP: Dict[str, str] = {
    ".png": "image_media_file", ".jpg": "image_media_file",
    ".jpeg": "image_media_file", ".gif": "image_media_file",
    ".bmp": "image_media_file", ".svg": "image_media_file",
    ".webp": "image_media_file", ".ico": "image_media_file",
    ".mp4": "image_media_file", ".mp3": "image_media_file",
    ".wav": "image_media_file", ".avi": "image_media_file",
    ".pdf": "document_file", ".doc": "document_file",
    ".docx": "document_file", ".xls": "document_file",
    ".xlsx": "document_file", ".ppt": "document_file",
    ".pptx": "document_file", ".odt": "document_file",
    ".txt": "text_code_file", ".py": "text_code_file",
    ".js": "text_code_file", ".ts": "text_code_file",
    ".json": "text_code_file", ".xml": "text_code_file",
    ".html": "text_code_file", ".css": "text_code_file",
    ".md": "text_code_file", ".yaml": "text_code_file",
    ".yml": "text_code_file", ".toml": "text_code_file",
    ".ini": "text_code_file", ".cfg": "text_code_file",
    ".log": "text_code_file", ".csv": "text_code_file",
    ".exe": "executable_program", ".msi": "executable_program",
    ".bat": "executable_program", ".cmd": "executable_program",
    ".ps1": "executable_program", ".lnk": "executable_program",
}


def _strip_quotes(text: str) -> str:
    """Remove surrounding quotes and whitespace."""
    text = text.strip()
    if len(text) >= 2 and text[0] in ('"', "'") and text[-1] == text[0]:
        text = text[1:-1].strip()
    return text


def _extract_path(text: str) -> str | None:
    """Try to pull a filesystem path out of *text*."""
    match = _PATH_RE.search(text)
    if match:
        return _strip_quotes(match.group(0).rstrip(".,;:!?)"))
    # Fallback: check for quoted string that looks like a path
    for quote_match in re.finditer(r'"([^"]+)"', text):
        candidate = quote_match.group(1).strip()
        if re.match(r"[A-Za-z]:[\\\/]", candidate) or candidate.startswith("\\\\"):
            return candidate
    return None


def _extract_url(text: str) -> str | None:
    """Try to pull a URL out of *text*."""
    match = _URL_RE.search(text)
    if match:
        url = match.group(0).rstrip(".,;:!?)")
        if not url.startswith("http"):
            url = "https://" + url
        return url
    # Check for bare domain patterns like "google.com" — exclude file extensions
    _FILE_EXTS = {
        "png", "jpg", "jpeg", "gif", "bmp", "svg", "webp", "ico",
        "mp4", "mp3", "wav", "avi", "mov", "mkv", "flac",
        "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt",
        "txt", "py", "js", "ts", "json", "xml", "html", "css",
        "md", "yaml", "yml", "toml", "ini", "cfg", "log", "csv",
        "exe", "msi", "bat", "cmd", "ps1", "lnk", "zip", "tar",
        "gz", "7z", "rar", "dll", "sys", "iso", "dmg",
    }
    bare = re.search(r"\b([a-z0-9-]+\.([a-z]{2,}))(?:/[^\s]*)?", text, re.IGNORECASE)
    if bare:
        tld = bare.group(2).lower()
        if tld not in _FILE_EXTS:
            domain = bare.group(0).rstrip(".,;:!?)")
            return "https://" + domain
    return None


def _guess_target_type(path: str) -> str:
    """Guess ``target_type`` from the file extension."""
    lower = path.lower()
    for ext, kind in _TARGET_TYPE_MAP.items():
        if lower.endswith(ext):
            return kind
    if lower.endswith(("\\", "/")):
        return "folder_directory"
    return "unknown_ambiguous_path"


# ── individual matchers ──────────────────────────────────────────
# Each returns {"tool": ..., "args": {...}} or None.

def _match_open_path(goal: str) -> Dict[str, Any] | None:
    """open <filepath>  /  open: <filepath>  /  open file <filepath>"""
    lower = goal.lower().strip()
    triggers = ("open ", "open: ", "open file ", "launch ", "start ", "run ")
    if not any(lower.startswith(t) for t in triggers):
        return None
    path = _extract_path(goal)
    if not path:
        return None
    # Make sure it's a path, not a URL
    if _URL_RE.search(path):
        return None
    return {
        "tool": "desktop_open_target",
        "args": {
            "target": path,
            "target_type": _guess_target_type(path),
        },
    }


def _match_open_url(goal: str) -> Dict[str, Any] | None:
    """open <url>  /  go to <url>  /  navigate to <url>"""
    lower = goal.lower().strip()
    triggers = ("open ", "open: ", "go to ", "goto ", "navigate to ", "visit ", "browse to ")
    if not any(lower.startswith(t) for t in triggers):
        # Also match if the entire goal IS a URL
        url = _extract_url(goal)
        if url and lower.replace(url.lower(), "").strip() == "":
            return {"tool": "browser_open_page", "args": {"url": url}}
        return None
    url = _extract_url(goal)
    if not url:
        return None
    return {"tool": "browser_open_page", "args": {"url": url}}


def _match_screenshot(goal: str) -> Dict[str, Any] | None:
    """take a screenshot  /  screenshot  /  capture screen"""
    lower = goal.lower().strip()
    keywords = ("screenshot", "screen shot", "capture screen", "take a screenshot",
                "grab screen", "screen capture", "print screen")
    if any(kw in lower for kw in keywords):
        scope = "primary_monitor"
        if "window" in lower or "active" in lower:
            scope = "active_window"
        return {"tool": "desktop_capture_screenshot", "args": {"scope": scope}}
    return None


def _match_list_windows(goal: str) -> Dict[str, Any] | None:
    """list windows  /  show windows  /  what windows are open"""
    lower = goal.lower().strip()
    patterns = ("list windows", "show windows", "what windows", "which windows",
                "open windows", "active windows", "get windows")
    if any(p in lower for p in patterns):
        return {"tool": "desktop_list_windows", "args": {"limit": 20}}
    return None


def _match_list_processes(goal: str) -> Dict[str, Any] | None:
    """list processes  /  what's running  /  show processes"""
    lower = goal.lower().strip()
    patterns = ("list processes", "show processes", "what's running",
                "whats running", "running processes", "active processes",
                "task manager", "get processes")
    if any(p in lower for p in patterns):
        return {"tool": "desktop_list_processes", "args": {}}
    return None


def _match_read_file(goal: str) -> Dict[str, Any] | None:
    """read file <path>  /  cat <path>  /  show file <path>"""
    lower = goal.lower().strip()
    triggers = ("read file ", "read: ", "cat ", "show file ", "view file ",
                "display file ", "print file ")
    if not any(lower.startswith(t) for t in triggers):
        return None
    path = _extract_path(goal)
    if not path:
        return None
    return {"tool": "read_file", "args": {"path": path}}


def _match_list_files(goal: str) -> Dict[str, Any] | None:
    """list files in <path>  /  ls <path>  /  dir <path>"""
    lower = goal.lower().strip()
    triggers = ("list files", "ls ", "dir ")
    if not any(lower.startswith(t) for t in triggers):
        return None
    path = _extract_path(goal)
    if not path:
        # Try to get path after "in" or "of"
        m = re.search(r"(?:in|of|at)\s+(.+)", goal, re.IGNORECASE)
        if m:
            path = _strip_quotes(m.group(1).strip())
        else:
            return None
    if not path:
        return None
    return {"tool": "list_files", "args": {"path": path}}


def _match_press_key(goal: str) -> Dict[str, Any] | None:
    """press enter  /  press ctrl+c  /  hit escape"""
    lower = goal.lower().strip()
    triggers = ("press ", "hit ", "send key ", "key ")
    if not any(lower.startswith(t) for t in triggers):
        return None
    # Remove the trigger word
    for t in triggers:
        if lower.startswith(t):
            key_text = goal[len(t):].strip()
            break
    else:
        return None

    key_text = _strip_quotes(key_text).lower()

    # Parse modifier+key combos like "ctrl+c", "alt+f4", "ctrl+shift+s"
    parts = re.split(r"[+\s]+", key_text)
    modifiers = []
    key_name = None
    for part in parts:
        part_lower = part.strip().lower()
        if part_lower in _MODIFIER_ALIASES:
            modifiers.append(_MODIFIER_ALIASES[part_lower])
        elif part_lower in _KEY_ALIASES:
            key_name = _KEY_ALIASES[part_lower]
        elif len(part_lower) == 1 and part_lower.isalpha():
            key_name = part_lower
        elif part_lower:
            # Unknown key — bail to LLM
            return None

    if key_name is None:
        return None

    args: Dict[str, Any] = {"key": key_name}
    if modifiers:
        args["modifiers"] = modifiers
    return {"tool": "desktop_press_key", "args": args}


def _match_type_text(goal: str) -> Dict[str, Any] | None:
    """type "hello world"  /  type: hello"""
    lower = goal.lower().strip()
    triggers = ("type ", "type: ", "enter text ", "input ")
    if not any(lower.startswith(t) for t in triggers):
        return None
    for t in triggers:
        if lower.startswith(t):
            text_part = goal[len(t):].strip()
            break
    else:
        return None

    text = _strip_quotes(text_part)
    if not text:
        return None
    # Don't match if it looks like a multi-step instruction
    if len(text) > 160:
        return None
    return {
        "tool": "desktop_type_text",
        "args": {"value": text, "field_label": "active field"},
    }


def _match_focus_window(goal: str) -> Dict[str, Any] | None:
    """focus <window>  /  switch to <window>  /  bring up <window>"""
    lower = goal.lower().strip()
    triggers = ("focus ", "focus on ", "switch to ", "bring up ", "activate ")
    if not any(lower.startswith(t) for t in triggers):
        return None
    for t in triggers:
        if lower.startswith(t):
            title_part = goal[len(t):].strip()
            break
    else:
        return None

    title = _strip_quotes(title_part)
    if not title or len(title) < 2:
        return None
    return {"tool": "desktop_focus_window", "args": {"title": title, "match": "contains"}}


# ── ordered matcher chain ────────────────────────────────────────

_MATCHERS = [
    _match_open_url,
    _match_open_path,
    _match_screenshot,
    _match_list_windows,
    _match_list_processes,
    _match_read_file,
    _match_list_files,
    _match_press_key,
    _match_type_text,
    _match_focus_window,
]


# ── public API ───────────────────────────────────────────────────

def try_direct_action(goal: str) -> Dict[str, Any] | None:
    """Match *goal* to a single direct tool call.

    Returns ``{"tool": "<name>", "args": {...}}`` on match, ``None``
    otherwise.  The returned dict is the same shape as what
    ``plan_next_action`` returns, so it drops straight into the
    existing execution pipeline.
    """
    if not goal or not goal.strip():
        return None
    # Skip if the goal looks like a multi-sentence instruction
    sentences = [s.strip() for s in re.split(r"[.!?]\s+", goal.strip()) if s.strip()]
    if len(sentences) > 2:
        return None
    # Skip if it contains planning keywords that signal complexity
    lower = goal.lower()
    complexity_signals = (
        " and then ", " after that ", " next ", " also ",
        " step 1", " step 2", " first ", " finally ",
        "please help", "can you", "how do i", "i want to",
        "i need to", "figure out", "find out",
    )
    if any(sig in lower for sig in complexity_signals):
        return None

    for matcher in _MATCHERS:
        result = matcher(goal)
        if result is not None:
            return result
    return None


def build_fast_result_message(tool_name: str, args: Dict[str, Any], result: Dict[str, Any]) -> str:
    """Build a concise human-readable completion message from a fast-path result.

    Used instead of ``llm.finalize()`` when the fast path handled the action.
    """
    ok = result.get("ok", False)

    if tool_name == "desktop_open_target":
        target = args.get("target", "the target")
        if ok:
            return f"Opened {target}."
        return f"Tried to open {target} but it did not succeed: {result.get('error', 'unknown error')}"

    if tool_name == "browser_open_page":
        url = args.get("url", "the page")
        if ok:
            title = result.get("title", "")
            return f"Opened {url}" + (f" — {title}" if title else "") + "."
        return f"Tried to open {url} but it did not succeed: {result.get('error', 'unknown error')}"

    if tool_name == "desktop_capture_screenshot":
        if ok:
            path = result.get("path", result.get("screenshot_path", ""))
            return f"Captured a screenshot" + (f" at {path}" if path else "") + "."
        return "Screenshot capture did not succeed."

    if tool_name == "desktop_list_windows":
        windows = result.get("windows", [])
        if ok:
            count = len(windows) if isinstance(windows, list) else 0
            return f"Found {count} open window{'s' if count != 1 else ''}."
        return "Could not list windows."

    if tool_name == "desktop_list_processes":
        processes = result.get("processes", [])
        if ok:
            count = len(processes) if isinstance(processes, list) else 0
            return f"Found {count} running process{'es' if count != 1 else ''}."
        return "Could not list processes."

    if tool_name == "read_file":
        path = args.get("path", "the file")
        if ok:
            content = str(result.get("content", ""))
            lines = content.count("\n") + 1
            return f"Read {path} ({lines} line{'s' if lines != 1 else ''})."
        return f"Could not read {path}: {result.get('error', 'unknown error')}"

    if tool_name == "list_files":
        path = args.get("path", "the directory")
        if ok:
            entries = result.get("entries", [])
            count = len(entries) if isinstance(entries, list) else 0
            return f"Listed {count} item{'s' if count != 1 else ''} in {path}."
        return f"Could not list files in {path}: {result.get('error', 'unknown error')}"

    if tool_name == "desktop_press_key":
        key = args.get("key", "")
        mods = args.get("modifiers", [])
        combo = "+".join(list(mods) + [key])
        if ok:
            return f"Pressed {combo}."
        return f"Tried to press {combo} but it did not succeed."

    if tool_name == "desktop_type_text":
        value = args.get("value", "")
        preview = value[:40] + ("..." if len(value) > 40 else "")
        if ok:
            return f'Typed "{preview}".'
        return f"Tried to type text but it did not succeed."

    if tool_name == "desktop_focus_window":
        title = args.get("title", "the window")
        if ok:
            return f"Focused window: {title}."
        return f"Could not focus window: {title}."

    # Generic fallback
    if ok:
        return f"Completed {tool_name}."
    return f"{tool_name} did not succeed: {result.get('error', 'unknown error')}"
