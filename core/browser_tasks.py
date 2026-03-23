from __future__ import annotations

from typing import Any, Dict


BROWSER_TASK_LIBRARY = {
    "open_and_inspect": {
        "label": "Open and inspect",
        "workflow_pattern": "browser_step_sequence",
    },
    "search_and_extract": {
        "label": "Search and extract",
        "workflow_pattern": "form_flow",
    },
    "fill_form_until_checkpoint": {
        "label": "Fill form until checkpoint",
        "workflow_pattern": "form_flow",
    },
    "follow_and_summarize": {
        "label": "Follow and summarize",
        "workflow_pattern": "navigation_extract_flow",
    },
}

BROWSER_TASK_ALIASES = {
    "open_and_inspect": "open_and_inspect",
    "open and inspect": "open_and_inspect",
    "inspect": "open_and_inspect",
    "inspect_page": "open_and_inspect",
    "inspect page": "open_and_inspect",
    "page_inspection": "open_and_inspect",
    "search_and_extract": "search_and_extract",
    "search and extract": "search_and_extract",
    "search": "search_and_extract",
    "search_extract": "search_and_extract",
    "fill_form_until_checkpoint": "fill_form_until_checkpoint",
    "fill form until checkpoint": "fill_form_until_checkpoint",
    "form": "fill_form_until_checkpoint",
    "form_fill": "fill_form_until_checkpoint",
    "follow_and_summarize": "follow_and_summarize",
    "follow and summarize": "follow_and_summarize",
    "follow": "follow_and_summarize",
    "summarize": "follow_and_summarize",
}


def normalize_browser_task_name(value: Any) -> str:
    text = str(value).strip().lower().replace("-", "_")
    if not text:
        return ""
    text = text.replace(" ", "_")
    return BROWSER_TASK_ALIASES.get(text, BROWSER_TASK_ALIASES.get(text.replace("_", " "), ""))


def browser_task_label(task_name: str) -> str:
    task = BROWSER_TASK_LIBRARY.get(task_name, {})
    return str(task.get("label", "")).strip()


def browser_task_workflow_pattern(task_name: str) -> str:
    task = BROWSER_TASK_LIBRARY.get(task_name, {})
    return str(task.get("workflow_pattern", "")).strip()


def infer_browser_task_name(
    tool_name: str,
    args: Dict[str, Any] | None = None,
    *,
    current_task_name: str = "",
    goal: str = "",
) -> str:
    payload = args if isinstance(args, dict) else {}

    explicit = normalize_browser_task_name(payload.get("browser_task_name") or payload.get("task_name"))
    if explicit:
        return explicit

    current = normalize_browser_task_name(current_task_name)
    if current:
        return current

    search_text = " ".join(
        [
            str(goal or ""),
            str(payload.get("text", "") or ""),
            str(payload.get("label", "") or ""),
            str(payload.get("placeholder", "") or ""),
            str(payload.get("name", "") or ""),
            str(payload.get("workflow_name", "") or ""),
        ]
    ).lower()

    if tool_name == "browser_follow_link":
        return "follow_and_summarize"

    if "search" in search_text or "query" in search_text:
        return "search_and_extract"

    if any(term in search_text for term in ("form", "submit", "sign in", "sign up", "log in", "login", "checkout")):
        return "fill_form_until_checkpoint"

    if tool_name in {"browser_type", "browser_click"} and (
        payload.get("checkpoint_required")
        or str(payload.get("checkpoint_reason", "")).strip()
        or any(term in search_text for term in ("email", "password", "field", "input"))
    ):
        return "fill_form_until_checkpoint"

    if tool_name == "browser_extract_text" and any(term in search_text for term in ("article", "summary", "summarize", "headline", "story")):
        return "follow_and_summarize"

    if tool_name in {"browser_open_page", "browser_inspect_page"}:
        return "open_and_inspect"

    return ""


def infer_browser_task_step(task_name: str, tool_name: str, requested_step: Any = "") -> str:
    explicit = str(requested_step).strip()
    if explicit:
        return explicit[:120]

    if task_name == "open_and_inspect":
        if tool_name == "browser_open_page":
            return "open page"
        if tool_name == "browser_inspect_page":
            return "inspect page"

    if task_name == "search_and_extract":
        if tool_name == "browser_open_page":
            return "open page"
        if tool_name == "browser_inspect_page":
            return "inspect search page"
        if tool_name == "browser_type":
            return "type search query"
        if tool_name == "browser_click":
            return "click search action"
        if tool_name == "browser_extract_text":
            return "extract result text"

    if task_name == "fill_form_until_checkpoint":
        if tool_name == "browser_open_page":
            return "open form page"
        if tool_name == "browser_inspect_page":
            return "inspect form"
        if tool_name == "browser_type":
            return "fill form fields"
        if tool_name == "browser_click":
            return "pause at checkpointed action"

    if task_name == "follow_and_summarize":
        if tool_name == "browser_open_page":
            return "open page"
        if tool_name == "browser_inspect_page":
            return "inspect page"
        if tool_name == "browser_follow_link":
            return "follow link"
        if tool_name == "browser_extract_text":
            return "extract summary text"

    labels = {
        "browser_open_page": "open page",
        "browser_inspect_page": "inspect page",
        "browser_click": "click element",
        "browser_type": "type into field",
        "browser_extract_text": "extract text",
        "browser_follow_link": "follow link",
    }
    return labels.get(tool_name, "browser step")


def infer_browser_task_next_step(
    task_name: str,
    tool_name: str,
    current_step: str,
    *,
    ok: bool,
    paused: bool,
    approval_required: bool,
    explicit_next_step: Any = "",
) -> str:
    explicit = str(explicit_next_step).strip()
    if explicit:
        return explicit[:120]

    if paused or approval_required or not ok:
        return current_step[:120]

    if task_name == "open_and_inspect":
        if tool_name == "browser_open_page":
            return "inspect page"
        return ""

    if task_name == "search_and_extract":
        if tool_name == "browser_open_page":
            return "inspect search page"
        if tool_name == "browser_inspect_page":
            current_lower = current_step.lower()
            if "result" in current_lower:
                return "extract result text"
            return "type search query"
        if tool_name == "browser_type":
            return "click search action"
        if tool_name == "browser_click":
            return "inspect search results"
        return ""

    if task_name == "fill_form_until_checkpoint":
        if tool_name == "browser_open_page":
            return "inspect form"
        if tool_name == "browser_inspect_page":
            return "fill form fields"
        if tool_name == "browser_type":
            return "fill next field or click checkpointed action"
        if tool_name == "browser_click":
            return "inspect result after approval"
        return ""

    if task_name == "follow_and_summarize":
        if tool_name == "browser_open_page":
            return "inspect page"
        if tool_name == "browser_inspect_page":
            current_lower = current_step.lower()
            if "destination" in current_lower:
                return "extract summary text"
            return "follow link"
        if tool_name == "browser_follow_link":
            return "inspect destination page"
        return ""

    return ""


def resolve_browser_task_status(
    *,
    ok: bool,
    paused: bool,
    approval_required: bool,
    next_step: str,
    resumed: bool = False,
) -> str:
    if paused:
        return "paused"
    if resumed:
        return "resumed"
    if approval_required:
        return "blocked"
    if not ok:
        return "needs_attention"
    if next_step:
        return "active"
    return "completed"



