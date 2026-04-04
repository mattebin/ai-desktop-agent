from __future__ import annotations

from typing import Any, Dict, List

from core.email_service import get_email_service


def _coerce_int(value: Any, default: int, *, minimum: int = 1, maximum: int = 50) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def email_connect_gmail(args: Dict[str, Any]) -> Dict[str, Any]:
    return get_email_service().connect_gmail()


def email_list_threads(args: Dict[str, Any]) -> Dict[str, Any]:
    return get_email_service().list_threads(
        limit=_coerce_int(args.get("limit", 10), 10),
        query=str(args.get("query", "")).strip(),
        label_ids=_string_list(args.get("label_ids", ["INBOX"])),
    )


def email_read_thread(args: Dict[str, Any]) -> Dict[str, Any]:
    return get_email_service().read_thread(
        str(args.get("thread_id", "")).strip(),
        max_messages=_coerce_int(args.get("max_messages", 8), 8, maximum=40),
    )


def email_prepare_reply_draft(args: Dict[str, Any]) -> Dict[str, Any]:
    return get_email_service().prepare_reply_draft(
        thread_id=str(args.get("thread_id", "")).strip(),
        guidance=str(args.get("guidance", "")).strip(),
        user_context=str(args.get("user_context", "")).strip(),
    )


def email_prepare_forward_draft(args: Dict[str, Any]) -> Dict[str, Any]:
    return get_email_service().prepare_forward_draft(
        thread_id=str(args.get("thread_id", "")).strip(),
        to=_string_list(args.get("to", [])),
        note=str(args.get("note", "")).strip(),
    )


def email_send_draft(args: Dict[str, Any]) -> Dict[str, Any]:
    approval_status = str(args.get("approval_status", "")).strip().lower()
    return get_email_service().send_draft(
        str(args.get("draft_id", "")).strip(),
        approved=(approval_status == "approved"),
    )


EMAIL_CONNECT_GMAIL_TOOL = {
    "name": "email_connect_gmail",
    "description": "Connect Gmail using the configured Desktop OAuth client so inbox and drafting tools can run.",
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    "func": email_connect_gmail,
}

EMAIL_LIST_THREADS_TOOL = {
    "name": "email_list_threads",
    "description": "List recent Gmail inbox threads in a read-only way.",
    "input_schema": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer"},
            "query": {"type": "string"},
            "label_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": [],
    },
    "func": email_list_threads,
}

EMAIL_READ_THREAD_TOOL = {
    "name": "email_read_thread",
    "description": "Read a Gmail thread with recent plain-text message bodies and headers.",
    "input_schema": {
        "type": "object",
        "properties": {
            "thread_id": {"type": "string"},
            "max_messages": {"type": "integer"},
        },
        "required": ["thread_id"],
    },
    "func": email_read_thread,
}

EMAIL_PREPARE_REPLY_DRAFT_TOOL = {
    "name": "email_prepare_reply_draft",
    "description": "Prepare a Gmail reply draft with thread context, without sending it.",
    "input_schema": {
        "type": "object",
        "properties": {
            "thread_id": {"type": "string"},
            "guidance": {"type": "string"},
            "user_context": {"type": "string"},
        },
        "required": ["thread_id"],
    },
    "func": email_prepare_reply_draft,
}

EMAIL_PREPARE_FORWARD_DRAFT_TOOL = {
    "name": "email_prepare_forward_draft",
    "description": "Prepare a Gmail forward draft for the latest message in a thread, without sending it.",
    "input_schema": {
        "type": "object",
        "properties": {
            "thread_id": {"type": "string"},
            "to": {"type": "array", "items": {"type": "string"}},
            "note": {"type": "string"},
        },
        "required": ["thread_id", "to"],
    },
    "func": email_prepare_forward_draft,
}

EMAIL_SEND_DRAFT_TOOL = {
    "name": "email_send_draft",
    "description": (
        "Send a prepared Gmail draft only after explicit approval. Without approval_status=approved, "
        "this tool pauses and exposes the exact frozen draft for review."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "draft_id": {"type": "string"},
            "approval_status": {"type": "string"},
        },
        "required": ["draft_id"],
    },
    "func": email_send_draft,
}
