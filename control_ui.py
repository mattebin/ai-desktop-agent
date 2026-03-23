from __future__ import annotations

import queue
import re
import threading
import time
import webbrowser
import json
from typing import Any, Dict, List

from core.config import load_settings
from core.local_api import DEFAULT_LOCAL_API_HOST, DEFAULT_LOCAL_API_PORT, LocalOperatorApiServer
from core.local_api_client import LocalOperatorApiClient, LocalOperatorApiClientError


UI_SESSION_TIMELINE_LIMIT = 60


def _trim_text(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _ui_fingerprint(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value)


def _normalize_transcript_text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")


def _parse_inline_markdown_segments(value: Any) -> List[Dict[str, str]]:
    text = str(value or "")
    if not text:
        return []

    pattern = re.compile(
        r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)|`([^`\n]+)`|\*\*([^\n*][^`\n]*?)\*\*|(https?://[^\s]+)",
        re.IGNORECASE,
    )
    segments: List[Dict[str, str]] = []
    cursor = 0
    for match in pattern.finditer(text):
        start, end = match.span()
        if start > cursor:
            segments.append({"kind": "text", "text": text[cursor:start]})
        if match.group(1) and match.group(2):
            segments.append({"kind": "link", "text": match.group(1), "url": match.group(2)})
        elif match.group(3):
            segments.append({"kind": "code", "text": match.group(3)})
        elif match.group(4):
            segments.append({"kind": "bold", "text": match.group(4)})
        elif match.group(5):
            segments.append({"kind": "link", "text": match.group(5), "url": match.group(5)})
        cursor = end
    if cursor < len(text):
        segments.append({"kind": "text", "text": text[cursor:]})
    return segments or [{"kind": "text", "text": text}]


def _parse_rich_text_blocks(value: Any) -> List[Dict[str, Any]]:
    text = _normalize_transcript_text(value)
    if not text:
        return []

    lines = text.split("\n")
    blocks: List[Dict[str, Any]] = []
    index = 0
    in_code = False
    code_lang = ""
    code_lines: List[str] = []

    def flush_code():
        nonlocal code_lines, code_lang
        blocks.append({"kind": "code", "language": _trim_text(code_lang, limit=24), "text": "\n".join(code_lines).rstrip("\n")})
        code_lines = []
        code_lang = ""

    while index < len(lines):
        raw_line = lines[index]
        stripped = raw_line.strip()

        if stripped.startswith("```"):
            if in_code:
                flush_code()
                in_code = False
            else:
                in_code = True
                code_lang = stripped[3:].strip()
                code_lines = []
            index += 1
            continue

        if in_code:
            code_lines.append(raw_line.rstrip("\n"))
            index += 1
            continue

        if not stripped:
            index += 1
            continue

        heading_match = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading_match:
            level = len(heading_match.group(1))
            blocks.append({"kind": f"heading_{level}", "text": heading_match.group(2).strip()})
            index += 1
            continue

        if stripped in {"---", "***", "___"}:
            blocks.append({"kind": "rule", "text": ""})
            index += 1
            continue

        if stripped.startswith(">"):
            quote_lines: List[str] = []
            while index < len(lines):
                candidate = lines[index].strip()
                if not candidate.startswith(">"):
                    break
                quote_lines.append(candidate[1:].lstrip())
                index += 1
            blocks.append({"kind": "quote", "text": "\n".join(quote_lines).strip()})
            continue

        bullet_match = re.match(r"^[-*]\s+(.+)$", stripped)
        numbered_match = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if bullet_match or numbered_match:
            list_kind = "ordered_list" if numbered_match else "bullet_list"
            items: List[str] = []
            while index < len(lines):
                candidate = lines[index].strip()
                if list_kind == "ordered_list":
                    current_match = re.match(r"^\d+\.\s+(.+)$", candidate)
                else:
                    current_match = re.match(r"^[-*]\s+(.+)$", candidate)
                if not current_match:
                    break
                items.append(current_match.group(1).strip())
                index += 1
            blocks.append({"kind": list_kind, "items": items})
            continue

        paragraph_lines: List[str] = []
        while index < len(lines):
            candidate = lines[index]
            candidate_stripped = candidate.strip()
            if not candidate_stripped:
                break
            if candidate_stripped.startswith("```"):
                break
            if re.match(r"^(#{1,3})\s+(.+)$", candidate_stripped):
                break
            if candidate_stripped in {"---", "***", "___"}:
                break
            if candidate_stripped.startswith(">"):
                break
            if re.match(r"^[-*]\s+.+$", candidate_stripped) or re.match(r"^\d+\.\s+.+$", candidate_stripped):
                break
            paragraph_lines.append(candidate_stripped)
            index += 1
        if paragraph_lines:
            blocks.append({"kind": "paragraph", "text": " ".join(paragraph_lines).strip()})
            continue
        index += 1

    if in_code and code_lines:
        flush_code()

    return blocks


def _session_matches_query(session: Dict[str, Any], query: str) -> bool:
    normalized_query = " ".join(str(query or "").strip().lower().split())
    if not normalized_query:
        return True
    haystack = " ".join(
        [
            str(session.get("title", "")),
            str(session.get("status", "")),
            str(session.get("summary", "")),
            str((session.get("pending_approval", {}) or {}).get("kind", "")),
            str((session.get("latest_message", {}) or {}).get("content", "")),
        ]
    ).lower()
    return all(token in haystack for token in normalized_query.split())


def _timeline_entry(label: str, detail: str = "", *, timestamp: str = "", source: str = "") -> Dict[str, str]:
    return {
        "label": _trim_text(label, limit=80),
        "detail": _trim_text(detail, limit=220),
        "timestamp": _trim_text(timestamp, limit=40),
        "source": _trim_text(source, limit=40),
    }


def _timeline_entry_from_message(message: Dict[str, Any]) -> Dict[str, str]:
    kind = str(message.get("kind", "message")).strip().lower()
    status = str(message.get("status", "")).strip().lower()
    content = _trim_text(message.get("content", ""), limit=220)
    if not content:
        return {}
    if kind in {"approval_needed", "checkpoint", "review_bundle", "approval"}:
        label = "Approval"
    elif kind in {"final", "result"} or status == "completed":
        label = "Final answer"
    elif kind == "error" or status in {"failed", "blocked", "stopped", "incomplete"}:
        label = "Error"
    elif kind in {"status", "system", "progress"}:
        label = "Activity"
    elif str(message.get("role", "")).strip().lower() == "user":
        label = "You"
    else:
        label = "Reply"
    return _timeline_entry(label, content, timestamp=message.get("created_at", ""), source="message")


def _timeline_entry_from_event(payload: Dict[str, Any]) -> Dict[str, str]:
    event_name = str(payload.get("event", "")).strip().lower()
    data = payload.get("data", {}) if isinstance(payload.get("data", {}), dict) else {}
    emitted_at = payload.get("emitted_at", "")
    if event_name in {"stream.hello", "stream.heartbeat", "session.sync", "session.updated"}:
        return {}
    if event_name == "session.message":
        return _timeline_entry_from_message(data.get("message", {}))
    if event_name.startswith("task."):
        label = {
            "task.started": "Task started",
            "task.progress": "Task progress",
            "task.paused": "Task paused",
            "task.resumed": "Task resumed",
            "task.completed": "Task completed",
            "task.failed": "Task failed",
            "task.blocked": "Task blocked",
            "task.queued": "Task queued",
            "task.updated": "Task updated",
        }.get(event_name, "Task update")
        detail = _trim_text(
            data.get("current_step")
            or (data.get("task", {}) or {}).get("last_message", "")
            or (data.get("task", {}) or {}).get("goal", ""),
            limit=220,
        )
        return _timeline_entry(label, detail, timestamp=emitted_at, source="task")
    if event_name.startswith("approval."):
        label = {
            "approval.needed": "Approval needed",
            "approval.cleared": "Approval cleared",
            "approval.approved": "Approved",
            "approval.rejected": "Rejected",
            "approval.updated": "Approval update",
        }.get(event_name, "Approval")
        detail = _trim_text(
            (data.get("pending_approval", {}) or {}).get("reason")
            or (data.get("pending_approval", {}) or {}).get("summary")
            or (data.get("message", {}) or {}).get("content", ""),
            limit=220,
        )
        return _timeline_entry(label, detail, timestamp=emitted_at, source="approval")
    if event_name == "browser.workflow":
        browser = data.get("browser", {}) if isinstance(data.get("browser", {}), dict) else {}
        detail = _trim_text(
            browser.get("workflow_step")
            or browser.get("task_step")
            or browser.get("current_title")
            or browser.get("current_url", ""),
            limit=220,
        )
        return _timeline_entry("Browser update", detail, timestamp=emitted_at, source="browser")
    if event_name == "alert":
        alert = data.get("alert", {}) if isinstance(data.get("alert", {}), dict) else {}
        return _timeline_entry(
            f"Alert: {alert.get('severity', 'info')}",
            alert.get("title") or alert.get("message", ""),
            timestamp=alert.get("created_at", emitted_at),
            source="alert",
        )
    if event_name == "stream.reset":
        return _timeline_entry("Stream reset", (data or {}).get("reason", ""), timestamp=emitted_at, source="stream")
    return {}


def _load_settings() -> Dict[str, Any]:
    return load_settings()


def _build_api_bridge(settings: Dict[str, Any]) -> tuple[LocalOperatorApiClient, LocalOperatorApiServer | None]:
    host = str(settings.get("local_api_host", DEFAULT_LOCAL_API_HOST)).strip() or DEFAULT_LOCAL_API_HOST
    port = int(settings.get("local_api_port", DEFAULT_LOCAL_API_PORT) or DEFAULT_LOCAL_API_PORT)
    client = LocalOperatorApiClient(f"http://{host}:{port}")
    try:
        client.health()
        return client, None
    except LocalOperatorApiClientError:
        pass

    server = LocalOperatorApiServer(host=host, port=port, settings=settings)
    server.start_in_thread()
    deadline = time.time() + 4.0
    last_error = ""
    while time.time() < deadline:
        try:
            client.health()
            return client, server
        except LocalOperatorApiClientError as exc:
            last_error = str(exc)
            time.sleep(0.15)
    raise RuntimeError(last_error or "Unable to start the local operator API.")


def launch_control_ui(settings: Dict[str, Any] | None = None):
    try:
        import tkinter as tk
        from tkinter import messagebox, scrolledtext, ttk
    except Exception as exc:
        raise RuntimeError(f"Tkinter is not available: {exc}") from exc

    effective_settings = settings if isinstance(settings, dict) else _load_settings()
    client, embedded_server = _build_api_bridge(effective_settings)

    palette = {
        "app": "#F6F8FB",
        "sidebar": "#EFF3F8",
        "panel": "#FFFFFF",
        "panel_alt": "#FBFCFE",
        "border": "#DCE4EE",
        "text": "#162033",
        "muted": "#667487",
        "accent": "#0B57D0",
        "user_bg": "#E3EEFF",
        "assistant_bg": "#FFFFFF",
        "status_bg": "#F2F5F8",
        "approval_bg": "#FFF4D6",
        "activity_bg": "#F3F5F8",
    }

    root = tk.Tk()
    root.title("AI Operator")
    root.geometry("1440x920")
    root.minsize(1180, 760)
    root.configure(background=palette["app"])

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("TFrame", background=palette["app"])
    style.configure("Sidebar.TFrame", background=palette["sidebar"])
    style.configure("Card.TLabelframe", background=palette["panel"], bordercolor=palette["border"], relief="solid")
    style.configure("Card.TLabelframe.Label", background=palette["panel"], foreground=palette["text"], font=("Segoe UI Semibold", 10))
    style.configure("Accent.TButton", font=("Segoe UI Semibold", 10), padding=(14, 8))
    style.configure("Plain.TButton", font=("Segoe UI", 10), padding=(10, 8))
    style.configure("Plain.TNotebook", background=palette["panel_alt"], borderwidth=0)
    style.configure("Plain.TNotebook.Tab", font=("Segoe UI", 9), padding=(10, 6))

    selected_session_id = {"value": ""}
    session_index: list[str] = []
    sessions_cache: list[Dict[str, Any]] = []
    session_timelines: dict[str, list[Dict[str, str]]] = {}
    current_session_cache = {"session": {}, "messages": []}
    details_visible = {"value": False}
    poll_handle = {"value": None}
    event_handle = {"value": None}
    refresh_handle = {"value": None}
    fetch_thread = {"value": None}
    fetch_inflight = {"value": False}
    stream_thread = {"value": None}
    stream_stop = {"value": None}
    active_stream = {"value": None}
    stream_connected = {"value": False}
    event_queue = queue.Queue()
    last_event_ids: dict[str, str] = {}
    dynamic_transcript_tags: list[str] = []
    link_tag_counter = {"value": 0}
    render_cache = {
        "sessions": "",
        "session_id": "",
        "messages": "",
        "summary": "",
        "status": "",
        "workflow": "",
        "approval": "",
        "operator": "",
        "alerts": "",
        "timeline": "",
        "runs": "",
        "queue": "",
        "scheduled": "",
        "watches": "",
        "button_state": "",
        "copy_enabled": "",
        "approval_visible": "",
        "alerts_visible": "",
    }
    session_search_var = tk.StringVar(value="")
    details_toggle_text = tk.StringVar(value="Show Details")
    connection_var = tk.StringVar(value="Connected to local operator API")
    title_var = tk.StringVar(value="New conversation")
    summary_var = tk.StringVar(value="Start with a natural-language request. The operator will continue until it needs approval or finishes.")
    status_var = tk.StringVar(value="Idle")
    workflow_var = tk.StringVar(value="No active task yet.")
    hint_var = tk.StringVar(value="Enter to send. Shift+Enter for a new line.")

    root.columnconfigure(1, weight=1)
    root.rowconfigure(0, weight=1)

    sidebar = ttk.Frame(root, style="Sidebar.TFrame", padding=(18, 18, 14, 18), width=260)
    sidebar.grid(row=0, column=0, sticky="nsew")
    sidebar.grid_propagate(False)
    sidebar.columnconfigure(0, weight=1)
    sidebar.rowconfigure(4, weight=1)

    main = ttk.Frame(root, padding=(18, 18, 18, 18))
    main.grid(row=0, column=1, sticky="nsew")
    main.columnconfigure(0, weight=1)
    main.rowconfigure(1, weight=1)

    sidepanel = ttk.Frame(root, padding=(0, 18, 18, 18), width=320)
    sidepanel.grid(row=0, column=2, sticky="nsew")
    sidepanel.grid_propagate(False)
    sidepanel.columnconfigure(0, weight=1)
    sidepanel.rowconfigure(3, weight=1)

    tk.Label(sidebar, text="AI Operator", bg=palette["sidebar"], fg=palette["text"], font=("Segoe UI Semibold", 19), anchor="w").grid(row=0, column=0, sticky="w")
    tk.Label(sidebar, text="Conversation-first operator surface", bg=palette["sidebar"], fg=palette["muted"], font=("Segoe UI", 9), anchor="w").grid(row=1, column=0, sticky="w", pady=(2, 12))

    sidebar_actions = ttk.Frame(sidebar, style="Sidebar.TFrame")
    sidebar_actions.grid(row=2, column=0, sticky="ew", pady=(0, 12))
    sidebar_actions.columnconfigure(0, weight=1)
    sidebar_actions.columnconfigure(1, weight=1)
    new_chat_button = ttk.Button(sidebar_actions, text="New Chat", style="Accent.TButton")
    new_chat_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
    refresh_button = ttk.Button(sidebar_actions, text="Refresh", style="Plain.TButton")
    refresh_button.grid(row=0, column=1, sticky="ew")

    session_search = tk.Entry(
        sidebar,
        textvariable=session_search_var,
        background="#FFFFFF",
        foreground=palette["text"],
        relief="flat",
        borderwidth=0,
        highlightthickness=1,
        highlightbackground=palette["border"],
        highlightcolor=palette["accent"],
        font=("Segoe UI", 10),
        insertbackground=palette["text"],
    )
    session_search.grid(row=3, column=0, sticky="ew", pady=(0, 10), ipady=6)

    sessions_list = tk.Listbox(
        sidebar,
        background=palette["sidebar"],
        foreground=palette["text"],
        selectbackground=palette["accent"],
        selectforeground="#FFFFFF",
        relief="flat",
        borderwidth=0,
        highlightthickness=0,
        activestyle="none",
        font=("Segoe UI", 10),
    )
    sessions_list.grid(row=4, column=0, sticky="nsew")

    header = tk.Frame(main, background=palette["panel_alt"], highlightbackground=palette["border"], highlightthickness=1, padx=16, pady=14)
    header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
    header.columnconfigure(0, weight=1)
    tk.Label(header, textvariable=title_var, bg=palette["panel_alt"], fg=palette["text"], font=("Segoe UI Semibold", 18), anchor="w").grid(row=0, column=0, sticky="w")
    tk.Label(header, textvariable=summary_var, bg=palette["panel_alt"], fg=palette["muted"], font=("Segoe UI", 10), justify="left", wraplength=760).grid(row=1, column=0, sticky="w", pady=(6, 0))
    header_actions = ttk.Frame(header)
    header_actions.grid(row=0, column=1, rowspan=2, sticky="ne", padx=(12, 0))
    copy_reply_button = ttk.Button(header_actions, text="Copy Reply", style="Plain.TButton")
    copy_reply_button.grid(row=0, column=0, sticky="e", padx=(0, 8))
    header_refresh_button = ttk.Button(header_actions, text="Refresh", style="Plain.TButton")
    header_refresh_button.grid(row=0, column=1, sticky="e")
    tk.Label(header, textvariable=status_var, bg=palette["panel_alt"], fg=palette["text"], font=("Segoe UI", 9), anchor="w").grid(row=2, column=0, sticky="w", pady=(10, 0))
    tk.Label(header, textvariable=workflow_var, bg=palette["panel_alt"], fg=palette["muted"], font=("Segoe UI", 9), anchor="w").grid(row=3, column=0, sticky="w", pady=(4, 0))
    tk.Label(header, textvariable=connection_var, bg=palette["panel_alt"], fg=palette["muted"], font=("Segoe UI", 8), anchor="e").grid(row=3, column=1, sticky="e", pady=(4, 0))

    transcript = scrolledtext.ScrolledText(
        main,
        wrap="word",
        background=palette["panel_alt"],
        foreground=palette["text"],
        relief="flat",
        borderwidth=0,
        highlightthickness=0,
        padx=18,
        pady=18,
        font=("Segoe UI", 10),
    )
    transcript.grid(row=1, column=0, sticky="nsew")
    transcript.configure(state="disabled")

    composer_card = tk.Frame(main, background=palette["panel"], highlightbackground=palette["border"], highlightthickness=1, padx=14, pady=14)
    composer_card.grid(row=2, column=0, sticky="ew", pady=(14, 0))
    composer_card.columnconfigure(0, weight=1)
    composer = tk.Text(
        composer_card,
        height=4,
        wrap="word",
        background="#FFFFFF",
        foreground=palette["text"],
        relief="flat",
        borderwidth=0,
        highlightthickness=0,
        font=("Segoe UI", 10),
        insertbackground=palette["text"],
    )
    composer.grid(row=0, column=0, sticky="ew")
    send_button = ttk.Button(composer_card, text="Send", style="Accent.TButton")
    send_button.grid(row=0, column=1, sticky="ns", padx=(14, 0))
    tk.Label(composer_card, textvariable=hint_var, bg=palette["panel"], fg=palette["muted"], font=("Segoe UI", 8), anchor="w").grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))

    approval_frame = ttk.LabelFrame(sidepanel, text="Needs Approval", style="Card.TLabelframe", padding=(12, 12, 12, 12))
    approval_frame.grid(row=0, column=0, sticky="ew", pady=(0, 12))
    approval_frame.columnconfigure(0, weight=1)
    approval_text = scrolledtext.ScrolledText(approval_frame, wrap="word", height=7, background=palette["panel"], foreground=palette["text"], relief="flat", borderwidth=0, highlightthickness=0, font=("Segoe UI", 9))
    approval_text.grid(row=0, column=0, columnspan=2, sticky="ew")
    approve_button = ttk.Button(approval_frame, text="Approve", style="Accent.TButton")
    approve_button.grid(row=1, column=0, sticky="ew", pady=(10, 0), padx=(0, 6))
    reject_button = ttk.Button(approval_frame, text="Reject", style="Plain.TButton")
    reject_button.grid(row=1, column=1, sticky="ew", pady=(10, 0))

    operator_frame = ttk.LabelFrame(sidepanel, text="Live Activity", style="Card.TLabelframe", padding=(12, 12, 12, 12))
    operator_frame.grid(row=1, column=0, sticky="ew", pady=(0, 12))
    operator_frame.columnconfigure(0, weight=1)
    operator_text = scrolledtext.ScrolledText(operator_frame, wrap="word", height=10, background=palette["panel"], foreground=palette["text"], relief="flat", borderwidth=0, highlightthickness=0, font=("Segoe UI", 9))
    operator_text.grid(row=0, column=0, sticky="ew")

    alerts_frame = ttk.LabelFrame(sidepanel, text="Recent Signals", style="Card.TLabelframe", padding=(12, 12, 12, 12))
    alerts_frame.grid(row=2, column=0, sticky="ew", pady=(0, 12))
    alerts_frame.columnconfigure(0, weight=1)
    alerts_text = scrolledtext.ScrolledText(alerts_frame, wrap="word", height=9, background=palette["panel"], foreground=palette["text"], relief="flat", borderwidth=0, highlightthickness=0, font=("Segoe UI", 9))
    alerts_text.grid(row=0, column=0, sticky="ew")

    details_frame = ttk.Frame(sidepanel, style="TFrame")
    details_frame.grid(row=3, column=0, sticky="nsew")
    details_frame.columnconfigure(0, weight=1)
    details_frame.rowconfigure(1, weight=1)
    details_header = ttk.Frame(details_frame)
    details_header.grid(row=0, column=0, sticky="ew")
    details_header.columnconfigure(0, weight=1)
    tk.Label(details_header, text="Background", bg=palette["app"], fg=palette["text"], font=("Segoe UI Semibold", 10), anchor="w").grid(row=0, column=0, sticky="w")
    details_toggle = ttk.Button(details_header, textvariable=details_toggle_text, style="Plain.TButton")
    details_toggle.grid(row=0, column=1, sticky="e")

    details_body = ttk.Frame(details_frame)
    details_notebook = ttk.Notebook(details_body, style="Plain.TNotebook")
    timeline_text = scrolledtext.ScrolledText(details_notebook, wrap="word", background=palette["panel"], foreground=palette["text"], relief="flat", borderwidth=0, highlightthickness=0, font=("Segoe UI", 9))
    run_text = scrolledtext.ScrolledText(details_notebook, wrap="word", background=palette["panel"], foreground=palette["text"], relief="flat", borderwidth=0, highlightthickness=0, font=("Segoe UI", 9))
    queue_text = scrolledtext.ScrolledText(details_notebook, wrap="word", background=palette["panel"], foreground=palette["text"], relief="flat", borderwidth=0, highlightthickness=0, font=("Segoe UI", 9))
    scheduled_text = scrolledtext.ScrolledText(details_notebook, wrap="word", background=palette["panel"], foreground=palette["text"], relief="flat", borderwidth=0, highlightthickness=0, font=("Segoe UI", 9))
    watches_text = scrolledtext.ScrolledText(details_notebook, wrap="word", background=palette["panel"], foreground=palette["text"], relief="flat", borderwidth=0, highlightthickness=0, font=("Segoe UI", 9))
    details_notebook.add(timeline_text, text="Timeline")
    details_notebook.add(run_text, text="Run")
    details_notebook.add(queue_text, text="Queue")
    details_notebook.add(scheduled_text, text="Scheduled")
    details_notebook.add(watches_text, text="Watches")

    for widget in (approval_text, operator_text, alerts_text, timeline_text, run_text, queue_text, scheduled_text, watches_text):
        widget.configure(state="disabled")

    transcript.tag_configure("assistant_meta", foreground=palette["muted"], font=("Segoe UI", 8, "bold"), spacing1=10, spacing3=2)
    transcript.tag_configure("assistant_body", foreground=palette["text"], background=palette["assistant_bg"], lmargin1=18, lmargin2=18, rmargin=24, spacing3=10)
    transcript.tag_configure("final_meta", foreground=palette["accent"], font=("Segoe UI", 8, "bold"), spacing1=10, spacing3=2)
    transcript.tag_configure("final_body", foreground=palette["text"], background="#EEF4FF", lmargin1=18, lmargin2=18, rmargin=24, spacing3=12)
    transcript.tag_configure("error_meta", foreground="#9B1C1C", font=("Segoe UI", 8, "bold"), spacing1=10, spacing3=2)
    transcript.tag_configure("error_body", foreground=palette["text"], background="#FDECEC", lmargin1=18, lmargin2=18, rmargin=24, spacing3=10)
    transcript.tag_configure("user_meta", foreground="#315C8A", font=("Segoe UI", 8, "bold"), justify="right", spacing1=10, spacing3=2)
    transcript.tag_configure("user_body", foreground=palette["text"], background=palette["user_bg"], justify="right", lmargin1=120, lmargin2=120, rmargin=18, spacing3=10)
    transcript.tag_configure("status_meta", foreground="#29613A", font=("Segoe UI", 8, "bold"), spacing1=10, spacing3=2)
    transcript.tag_configure("status_body", foreground=palette["text"], background=palette["status_bg"], lmargin1=18, lmargin2=18, rmargin=24, spacing3=10)
    transcript.tag_configure("approval_meta", foreground="#8A6116", font=("Segoe UI", 8, "bold"), spacing1=10, spacing3=2)
    transcript.tag_configure("approval_body", foreground=palette["text"], background=palette["approval_bg"], lmargin1=18, lmargin2=18, rmargin=24, spacing3=10)
    transcript.tag_configure("activity_meta", foreground=palette["muted"], font=("Segoe UI", 8, "bold"), spacing1=8, spacing3=2)
    transcript.tag_configure("activity_body", foreground=palette["muted"], background=palette["activity_bg"], lmargin1=18, lmargin2=18, rmargin=24, spacing3=8)
    transcript.tag_configure("block_paragraph", spacing1=2, spacing3=10)
    transcript.tag_configure("block_heading_1", font=("Segoe UI Semibold", 15), spacing1=10, spacing3=6)
    transcript.tag_configure("block_heading_2", font=("Segoe UI Semibold", 13), spacing1=10, spacing3=5)
    transcript.tag_configure("block_heading_3", font=("Segoe UI Semibold", 11), spacing1=8, spacing3=4)
    transcript.tag_configure("block_list", lmargin1=36, lmargin2=56, spacing1=2, spacing3=4)
    transcript.tag_configure("block_quote", foreground=palette["muted"], lmargin1=36, lmargin2=48, rmargin=30, spacing1=4, spacing3=8)
    transcript.tag_configure("block_rule", foreground=palette["border"], justify="center", spacing1=8, spacing3=8)
    transcript.tag_configure("block_code_meta", foreground=palette["muted"], font=("Consolas", 8, "bold"), lmargin1=32, lmargin2=32, spacing1=4, spacing3=2)
    transcript.tag_configure("block_code", font=("Consolas", 10), background="#EEF2F7", foreground=palette["text"], lmargin1=32, lmargin2=32, rmargin=28, spacing1=2, spacing3=10)
    transcript.tag_configure("inline_bold", font=("Segoe UI Semibold", 10))
    transcript.tag_configure("inline_code", font=("Consolas", 10), background="#EEF2F7", foreground=palette["text"])
    transcript.tag_configure("inline_link", foreground=palette["accent"], underline=True)
    transcript.tag_raise("block_heading_1")
    transcript.tag_raise("block_heading_2")
    transcript.tag_raise("block_heading_3")
    transcript.tag_raise("block_quote")
    transcript.tag_raise("block_rule")
    transcript.tag_raise("block_code_meta")
    transcript.tag_raise("block_code")
    transcript.tag_raise("inline_bold")
    transcript.tag_raise("inline_code")
    transcript.tag_raise("inline_link")
    def set_text(widget, text: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text.strip() or "-")
        widget.configure(state="disabled")

    def format_status(session: Dict[str, Any], snapshot: Dict[str, Any]) -> str:
        pending = session.get("pending_approval", {}) or snapshot.get("pending_approval", {})
        parts = [f"Status: {_trim_text(session.get('status') or snapshot.get('status') or 'idle', limit=40)}"]
        if session.get("current_task_id"):
            parts.append(f"Task: {session.get('current_task_id')}")
        if pending.get("kind"):
            parts.append(f"Approval: {_trim_text(pending.get('kind'), limit=40)}")
        return "   |   ".join(parts)

    def format_summary(session: Dict[str, Any], snapshot: Dict[str, Any]) -> str:
        authoritative = session.get("authoritative_reply", {})
        if authoritative.get("preview") or authoritative.get("content"):
            return _trim_text(authoritative.get("preview") or authoritative.get("content"), limit=340)

        pending = session.get("pending_approval", {}) or snapshot.get("pending_approval", {})
        if pending.get("kind"):
            return _trim_text(
                pending.get("reason") or pending.get("summary") or "Waiting for approval before continuing.",
                limit=340,
            )

        current_step = _trim_text(snapshot.get("current_step", ""), limit=220)
        if current_step and str(snapshot.get("status", "")).strip().lower() in {"running", "queued", "paused"}:
            return _trim_text(f"Current progress: {current_step}", limit=340)

        if session.get("last_result_message"):
            return _trim_text(session.get("last_result_message", ""), limit=340)
        if session.get("summary"):
            return _trim_text(session.get("summary", ""), limit=340)
        if session.get("latest_user_message"):
            return _trim_text(session.get("latest_user_message", ""), limit=340)
        return "Start with a natural-language request. The operator will continue until it needs approval or finishes."

    def format_workflow(snapshot: Dict[str, Any]) -> str:
        browser = snapshot.get("browser", {})
        parts = []
        if snapshot.get("current_step"):
            parts.append(_trim_text(snapshot.get("current_step"), limit=120))
        if browser.get("workflow_name"):
            parts.append(_trim_text(browser.get("workflow_name"), limit=120))
        if browser.get("current_title") or browser.get("current_url"):
            parts.append(_trim_text(browser.get("current_title") or browser.get("current_url"), limit=120))
        return "   |   ".join(parts) or "Waiting for a new request."

    def format_approval(snapshot: Dict[str, Any]) -> str:
        pending = snapshot.get("pending_approval", {})
        if not pending.get("kind"):
            return "No approvals pending. The operator will continue automatically until a checkpoint or policy gate requires a pause."
        lines = [f"Kind: {_trim_text(pending.get('kind'), limit=60)}"]
        if pending.get("reason"):
            lines.append(f"Reason: {_trim_text(pending.get('reason'), limit=220)}")
        if pending.get("summary"):
            lines.append(f"Summary: {_trim_text(pending.get('summary'), limit=220)}")
        if pending.get("step"):
            lines.append(f"Step: {_trim_text(pending.get('step'), limit=120)}")
        return "\n".join(lines)

    def format_operator(snapshot: Dict[str, Any]) -> str:
        browser = snapshot.get("browser", {})
        lines = [
            f"Status: {_trim_text(snapshot.get('status') or 'idle', limit=40)}",
            f"Current step: {_trim_text(snapshot.get('current_step') or '-', limit=140)}",
            f"Browser task: {_trim_text(browser.get('task_label') or browser.get('task_name') or '-', limit=120)}",
            f"Workflow: {_trim_text(browser.get('workflow_name') or '-', limit=120)}",
            f"Page: {_trim_text(browser.get('current_title') or browser.get('current_url') or '-', limit=140)}",
        ]
        recovery = snapshot.get("recovery_notes", [])[:4]
        if recovery:
            lines.append("")
            lines.append("Recovery notes:")
            lines.extend(f"- {_trim_text(item, limit=140)}" for item in recovery)
        return "\n".join(lines)

    def compress_transcript_messages(messages: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
        rendered: list[Dict[str, Any]] = []
        activity_group: list[Dict[str, Any]] = []

        def flush_activity():
            if not activity_group:
                return
            lines: list[str] = []
            for item in activity_group[-4:]:
                snippet = _trim_text(item.get("content", ""), limit=180)
                if snippet and snippet not in lines:
                    lines.append(snippet)
            if lines:
                rendered.append(
                    {
                        "role": "assistant",
                        "kind": "activity",
                        "content": "\n".join(f"- {line}" for line in lines),
                        "status": activity_group[-1].get("status", ""),
                    }
                )
            activity_group.clear()

        for message in messages:
            kind = str(message.get("kind", "message")).strip().lower() or "message"
            if kind in {"status", "system", "progress"}:
                activity_group.append(message)
                continue
            flush_activity()
            rendered.append(message)

        flush_activity()
        return rendered

    def format_alerts(payload: Dict[str, Any]) -> str:
        items = payload.get("items", [])
        if not items:
            return "No alerts yet."
        lines = []
        for item in items[:5]:
            lines.append(f"[{str(item.get('severity', 'info')).upper()}] {_trim_text(item.get('title') or '-', limit=80)}")
            if item.get("message"):
                lines.append(_trim_text(item.get("message"), limit=140))
            if item.get("source"):
                lines.append(f"source: {item.get('source')}")
            lines.append("")
        return "\n".join(lines).rstrip()

    def format_runs(payload: Dict[str, Any]) -> str:
        items = payload.get("items", [])
        if not items:
            return "No recent run history for this session yet."
        lines = []
        for item in items[:6]:
            lines.append(f"{item.get('run_id') or '-'} [{item.get('final_status') or '-'}]")
            lines.append(f"goal: {_trim_text(item.get('goal') or '-', limit=140)}")
            lines.append(f"summary: {_trim_text(item.get('final_summary') or '-', limit=180)}")
            lines.append("")
        return "\n".join(lines).rstrip()

    def format_queue(payload: Dict[str, Any]) -> str:
        counts = payload.get("counts", {})
        lines = [f"queued={counts.get('queued', 0)} running={counts.get('running', 0)} paused={counts.get('paused', 0)}"]
        for item in payload.get("queued_tasks", [])[:4]:
            lines.append(f"- {_trim_text(item.get('goal') or '-', limit=120)} [{item.get('status') or '-'}]")
        return "\n".join(lines)

    def format_scheduled(payload: Dict[str, Any]) -> str:
        counts = payload.get("counts", {})
        lines = [f"scheduled={counts.get('scheduled', 0)} queued={counts.get('queued', 0)} paused={counts.get('paused', 0)}"]
        for item in payload.get("tasks", [])[:4]:
            lines.append(f"- {_trim_text(item.get('goal') or '-', limit=120)} [{item.get('status') or '-'}]")
            lines.append(f"  next: {item.get('next_run_at') or '-'}")
        return "\n".join(lines)

    def format_watches(payload: Dict[str, Any]) -> str:
        counts = payload.get("counts", {})
        lines = [f"watching={counts.get('watching', 0)} triggered={counts.get('triggered', 0)} paused={counts.get('paused', 0)}"]
        for item in payload.get("tasks", [])[:4]:
            lines.append(f"- {_trim_text(item.get('condition_label') or item.get('condition_type') or '-', limit=120)} [{item.get('status') or '-'}]")
        return "\n".join(lines)

    def append_timeline_entry(session_id: str, entry: Dict[str, str]):
        target_session_id = _trim_text(session_id, limit=80)
        if not target_session_id or not entry:
            return
        timeline = session_timelines.setdefault(target_session_id, [])
        compact_entry = {
            "label": _trim_text(entry.get("label", ""), limit=80),
            "detail": _trim_text(entry.get("detail", ""), limit=220),
            "timestamp": _trim_text(entry.get("timestamp", ""), limit=40),
            "source": _trim_text(entry.get("source", ""), limit=40),
        }
        if not compact_entry["label"] and not compact_entry["detail"]:
            return
        if timeline:
            last_entry = timeline[-1]
            if last_entry.get("label") == compact_entry["label"] and last_entry.get("detail") == compact_entry["detail"]:
                timeline[-1] = compact_entry
                return
        timeline.append(compact_entry)
        if len(timeline) > UI_SESSION_TIMELINE_LIMIT:
            del timeline[:-UI_SESSION_TIMELINE_LIMIT]

    def seed_timeline(session: Dict[str, Any], messages: list[Dict[str, Any]], alerts_payload: Dict[str, Any], runs_payload: Dict[str, Any], snapshot: Dict[str, Any]):
        session_id = _trim_text(session.get("session_id", ""), limit=80)
        if not session_id:
            return
        existing = session_timelines.setdefault(session_id, [])
        if existing:
            return
        for message in messages[-8:]:
            entry = _timeline_entry_from_message(message)
            if entry and entry.get("label") not in {"You", "Reply"}:
                append_timeline_entry(session_id, entry)
        latest_run = next(iter(runs_payload.get("items", [])), {})
        if latest_run:
            append_timeline_entry(
                session_id,
                _timeline_entry(
                    f"Run {latest_run.get('final_status', 'finished')}",
                    latest_run.get("final_summary") or latest_run.get("goal", ""),
                    timestamp=latest_run.get("ended_at", ""),
                    source="run",
                ),
            )
        for alert in alerts_payload.get("items", [])[:3]:
            append_timeline_entry(
                session_id,
                _timeline_entry(
                    f"Alert: {alert.get('severity', 'info')}",
                    alert.get("title") or alert.get("message", ""),
                    timestamp=alert.get("created_at", ""),
                    source="alert",
                ),
            )
        if snapshot.get("status") in {"running", "queued", "paused"} or snapshot.get("current_step"):
            append_timeline_entry(
                session_id,
                _timeline_entry(
                    f"Task {snapshot.get('status', 'idle')}",
                    snapshot.get("current_step") or snapshot.get("result_message", ""),
                    source="snapshot",
                ),
            )

    def format_timeline(session_id: str) -> str:
        items = session_timelines.get(_trim_text(session_id, limit=80), [])
        if not items:
            return "No recent activity for this session yet."
        lines: List[str] = []
        for entry in items[-12:]:
            prefix = f"{entry.get('timestamp')} " if entry.get("timestamp") else ""
            lines.append(f"{prefix}{entry.get('label') or 'Activity'}")
            if entry.get("detail"):
                lines.append(entry.get("detail", ""))
            lines.append("")
        return "\n".join(lines).rstrip()

    def latest_reply_text(session: Dict[str, Any], messages: list[Dict[str, Any]]) -> str:
        authoritative = session.get("authoritative_reply", {})
        if authoritative.get("content"):
            return str(authoritative.get("content", "")).strip()
        if session.get("last_result_message"):
            return str(session.get("last_result_message", "")).strip()
        for message in reversed(messages):
            if str(message.get("role", "")).strip().lower() != "assistant":
                continue
            content = str(message.get("content", "")).strip()
            if content:
                return content
        return ""

    def clear_dynamic_transcript_tags():
        while dynamic_transcript_tags:
            tag_name = dynamic_transcript_tags.pop()
            try:
                transcript.tag_delete(tag_name)
            except Exception:
                pass

    def insert_inline_text(text: str, *tags: str):
        content = str(text or "")
        if not content:
            return
        for segment in _parse_inline_markdown_segments(content):
            kind = segment.get("kind", "text")
            segment_text = str(segment.get("text", ""))
            if not segment_text:
                continue
            if kind == "bold":
                transcript.insert("end", segment_text, tags + ("inline_bold",))
                continue
            if kind == "code":
                transcript.insert("end", segment_text, tags + ("inline_code",))
                continue
            if kind == "link":
                link_tag_counter["value"] += 1
                link_tag = f"inline_link_{link_tag_counter['value']}"
                dynamic_transcript_tags.append(link_tag)
                url = str(segment.get("url", segment_text)).strip()
                transcript.tag_configure(link_tag, foreground=palette["accent"], underline=True)
                transcript.tag_bind(link_tag, "<Button-1>", lambda _event, target=url: webbrowser.open_new_tab(target))
                transcript.tag_bind(link_tag, "<Enter>", lambda _event: transcript.configure(cursor="hand2"))
                transcript.tag_bind(link_tag, "<Leave>", lambda _event: transcript.configure(cursor=""))
                transcript.insert("end", segment_text, tags + ("inline_link", link_tag))
                continue
            transcript.insert("end", segment_text, tags)

    def insert_rich_body(body: str, base_tag: str, *, compact: bool = False):
        blocks = _parse_rich_text_blocks(body)
        if not blocks:
            transcript.insert("end", "-\n\n", (base_tag, "block_paragraph"))
            return

        for block in blocks:
            kind = block.get("kind", "paragraph")
            if kind.startswith("heading_"):
                insert_inline_text(block.get("text", "").strip(), base_tag, kind)
                transcript.insert("end", "\n")
                transcript.insert("end", "\n")
                continue
            if kind == "bullet_list":
                for item in block.get("items", []):
                    transcript.insert("end", u"\u2022 ", (base_tag, "block_list"))
                    insert_inline_text(str(item).strip(), base_tag, "block_list")
                    transcript.insert("end", "\n")
                transcript.insert("end", "\n")
                continue
            if kind == "ordered_list":
                for idx, item in enumerate(block.get("items", []), start=1):
                    transcript.insert("end", f"{idx}. ", (base_tag, "block_list"))
                    insert_inline_text(str(item).strip(), base_tag, "block_list")
                    transcript.insert("end", "\n")
                transcript.insert("end", "\n")
                continue
            if kind == "quote":
                for line in str(block.get("text", "")).splitlines():
                    transcript.insert("end", u"\u2502 ", (base_tag, "block_quote"))
                    insert_inline_text(line.strip(), base_tag, "block_quote")
                    transcript.insert("end", "\n")
                transcript.insert("end", "\n")
                continue
            if kind == "rule":
                transcript.insert("end", "........................\n\n", ("block_rule",))
                continue
            if kind == "code":
                language = str(block.get("language", "")).strip()
                if language:
                    transcript.insert("end", language.upper() + "\n", ("block_code_meta",))
                code_text = str(block.get("text", "")).rstrip("\n") or " "
                transcript.insert("end", code_text + "\n\n", ("block_code",))
                continue

            paragraph_text = str(block.get("text", "")).strip()
            if not paragraph_text:
                continue
            if compact:
                paragraph_text = _trim_text(paragraph_text, limit=260)
            insert_inline_text(paragraph_text, base_tag, "block_paragraph")
            transcript.insert("end", "\n\n")

    def render_sessions(items: list[Dict[str, Any]]):
        session_index.clear()
        sessions_list.delete(0, "end")
        filtered_items = [session for session in items if _session_matches_query(session, session_search_var.get())]
        for session in filtered_items:
            session_id = str(session.get("session_id", "")).strip()
            if not session_id:
                continue
            session_index.append(session_id)
            preview = session.get("pending_approval", {}).get("kind") or session.get("status") or "idle"
            label = f"{_trim_text(session.get('title') or 'New session', limit=30)} | {_trim_text(preview, limit=16)}"
            sessions_list.insert("end", label)
        if session_index and selected_session_id["value"] in session_index:
            idx = session_index.index(selected_session_id["value"])
            sessions_list.selection_clear(0, "end")
            sessions_list.selection_set(idx)
            sessions_list.activate(idx)

    def stream_key(session_id: str = "", state_scope_id: str = "") -> str:
        if state_scope_id:
            return _trim_text(state_scope_id, limit=120)
        if session_id:
            return f"chat:{_trim_text(session_id, limit=80)}"
        return "__operator__"

    def render_transcript(messages: list[Dict[str, Any]]):
        messages = compress_transcript_messages(messages)
        transcript.configure(state="normal")
        clear_dynamic_transcript_tags()
        transcript.delete("1.0", "end")
        if not messages:
            insert_rich_body(
                "This conversation is ready. Ask the operator to inspect code, compare files, suggest commands, or continue a browser workflow.",
                "assistant_body",
            )
        for message in messages:
            role = str(message.get("role", "assistant")).strip().lower() or "assistant"
            kind = str(message.get("kind", "message")).strip().lower() or "message"
            status = str(message.get("status", "")).strip().lower()
            compact = False
            if role == "user":
                meta_tag, body_tag = "user_meta", "user_body"
                label = "You"
            elif kind == "activity":
                meta_tag, body_tag = "activity_meta", "activity_body"
                label = "Activity"
                compact = True
            elif kind in {"status", "system", "progress"}:
                meta_tag, body_tag = "status_meta", "status_body"
                label = "Activity"
                compact = True
            elif kind in {"approval_needed", "checkpoint", "review_bundle"}:
                meta_tag, body_tag = "approval_meta", "approval_body"
                label = "Approval Needed"
            elif kind in {"final", "result"} or status in {"completed", "failed", "blocked", "needs_attention", "stopped", "incomplete"}:
                if status in {"failed", "blocked", "stopped", "incomplete"}:
                    meta_tag, body_tag = "error_meta", "error_body"
                else:
                    meta_tag, body_tag = "final_meta", "final_body"
                label = "Final Answer" if status == "completed" else f"Operator Update ({status or 'done'})"
            elif kind == "error":
                meta_tag, body_tag = "error_meta", "error_body"
                label = "Operator Error"
            else:
                meta_tag, body_tag = "assistant_meta", "assistant_body"
                label = "Operator Reply"
            body = str(message.get("content", "")).strip() or "-"
            transcript.insert("end", label + "\n", meta_tag)
            insert_rich_body(body, body_tag, compact=compact)
        transcript.configure(state="disabled")
        transcript.see("end")

    def fetch_payloads(requested_session_id: str, *, include_details: bool):
        sessions_payload = client.list_sessions(limit=int(effective_settings.get("max_chat_sessions", 16) or 16))
        sessions = list(sessions_payload.get("sessions", sessions_payload.get("items", [])))
        resolved_session_id = _trim_text(requested_session_id, limit=80)
        known_ids = {str(item.get("session_id", "")).strip() for item in sessions if str(item.get("session_id", "")).strip()}
        if not resolved_session_id and sessions:
            resolved_session_id = str(sessions[0].get("session_id", "")).strip()
        if resolved_session_id and resolved_session_id not in known_ids:
            resolved_session_id = str(sessions[0].get("session_id", "")).strip() if sessions else ""

        session_payload = {"session": {}}
        messages_payload = {"messages": []}
        if resolved_session_id:
            session_payload = client.get_session(resolved_session_id)
            messages_payload = client.get_session_messages(resolved_session_id, limit=int(effective_settings.get("max_chat_messages_per_session", 40) or 40))
            snapshot = client.get_snapshot(session_id=resolved_session_id)
            runs_payload = client.get_recent_runs(limit=6, session_id=resolved_session_id)
        else:
            snapshot = client.get_snapshot()
            runs_payload = {"items": []}

        alerts_payload = client.get_alerts(limit=6, session_id=resolved_session_id) if resolved_session_id else client.get_alerts(limit=6)
        if include_details:
            queue_payload = client.get_queue()
            scheduled_payload = client.get_scheduled()
            watches_payload = client.get_watches()
        else:
            queue_payload = {"counts": {}, "queued_tasks": []}
            scheduled_payload = {"counts": {}, "tasks": []}
            watches_payload = {"counts": {}, "tasks": []}
        return resolved_session_id, sessions, session_payload.get("session", {}), list(messages_payload.get("messages", messages_payload.get("items", []))), snapshot, alerts_payload, runs_payload, queue_payload, scheduled_payload, watches_payload

    def rerender_sessions(*_args):
        render_sessions(sessions_cache)

    def apply_view_payload(payload):
        resolved_session_id, sessions, session, messages, snapshot, alerts_payload, runs_payload, queue_payload, scheduled_payload, watches_payload = payload
        previous_session_id = selected_session_id["value"]
        if previous_session_id != resolved_session_id:
            selected_session_id["value"] = resolved_session_id
        sessions_cache[:] = sessions
        current_session_cache["session"] = session
        current_session_cache["messages"] = messages
        seed_timeline(session, messages, alerts_payload, runs_payload, snapshot)

        sessions_fp = _ui_fingerprint([(item.get("session_id", ""), item.get("title", ""), item.get("status", ""), (item.get("pending_approval", {}) or {}).get("kind", "")) for item in sessions])
        if render_cache["sessions"] != sessions_fp:
            render_cache["sessions"] = sessions_fp
            render_sessions(sessions)

        session_id = _trim_text(session.get("session_id", ""), limit=80)
        messages_fp = _ui_fingerprint(messages)
        if render_cache["session_id"] != session_id or render_cache["messages"] != messages_fp:
            render_cache["session_id"] = session_id
            render_cache["messages"] = messages_fp
            render_transcript(messages)

        title_value = _trim_text(session.get("title") or "New conversation", limit=90)
        summary_value = format_summary(session, snapshot)
        status_value = format_status(session, snapshot)
        workflow_value = format_workflow(snapshot)
        if title_var.get() != title_value:
            title_var.set(title_value)
        if render_cache["summary"] != summary_value:
            render_cache["summary"] = summary_value
            summary_var.set(summary_value)
        if render_cache["status"] != status_value:
            render_cache["status"] = status_value
            status_var.set(status_value)
        if render_cache["workflow"] != workflow_value:
            render_cache["workflow"] = workflow_value
            workflow_var.set(workflow_value)

        connection_var.set("Live updates connected" if stream_connected["value"] else "Connected to local operator API")
        approval_value = format_approval(snapshot)
        operator_value = format_operator(snapshot)
        alerts_value = format_alerts(alerts_payload)
        timeline_value = format_timeline(selected_session_id["value"])
        runs_value = format_runs(runs_payload)
        queue_value = format_queue(queue_payload)
        scheduled_value = format_scheduled(scheduled_payload)
        watches_value = format_watches(watches_payload)
        if render_cache["approval"] != approval_value:
            render_cache["approval"] = approval_value
            set_text(approval_text, approval_value)
        if render_cache["operator"] != operator_value:
            render_cache["operator"] = operator_value
            set_text(operator_text, operator_value)
        if render_cache["alerts"] != alerts_value:
            render_cache["alerts"] = alerts_value
            set_text(alerts_text, alerts_value)
        if render_cache["timeline"] != timeline_value:
            render_cache["timeline"] = timeline_value
            set_text(timeline_text, timeline_value)
        if render_cache["runs"] != runs_value:
            render_cache["runs"] = runs_value
            set_text(run_text, runs_value)
        if render_cache["queue"] != queue_value:
            render_cache["queue"] = queue_value
            set_text(queue_text, queue_value)
        if render_cache["scheduled"] != scheduled_value:
            render_cache["scheduled"] = scheduled_value
            set_text(scheduled_text, scheduled_value)
        if render_cache["watches"] != watches_value:
            render_cache["watches"] = watches_value
            set_text(watches_text, watches_value)

        button_state = "normal" if snapshot.get("pending_approval", {}).get("kind") else "disabled"
        if render_cache["button_state"] != button_state:
            render_cache["button_state"] = button_state
            approve_button.configure(state=button_state)
            reject_button.configure(state=button_state)
        copy_enabled = "normal" if latest_reply_text(session, messages) else "disabled"
        if render_cache["copy_enabled"] != copy_enabled:
            render_cache["copy_enabled"] = copy_enabled
            copy_reply_button.configure(state=copy_enabled)

        approval_visible = "shown" if snapshot.get("pending_approval", {}).get("kind") else "hidden"
        if render_cache["approval_visible"] != approval_visible:
            render_cache["approval_visible"] = approval_visible
            if approval_visible == "shown":
                approval_frame.grid()
            else:
                approval_frame.grid_remove()

        alerts_visible = "shown" if alerts_payload.get("items", []) else "hidden"
        if render_cache["alerts_visible"] != alerts_visible:
            render_cache["alerts_visible"] = alerts_visible
            if alerts_visible == "shown":
                alerts_frame.grid()
            else:
                alerts_frame.grid_remove()

        if previous_session_id != resolved_session_id:
            root.after(0, start_event_stream)

    def refresh_view_async():
        if fetch_inflight["value"]:
            return
        fetch_inflight["value"] = True
        connection_var.set("Loading conversation...")

        def worker():
            try:
                payload = fetch_payloads(selected_session_id["value"], include_details=details_visible["value"])
                root.after(0, lambda: finish_refresh(payload=payload, error=""))
            except LocalOperatorApiClientError as exc:
                root.after(0, lambda: finish_refresh(payload=None, error=f"Local API unavailable: {_trim_text(exc, limit=140)}"))
            except Exception as exc:
                root.after(0, lambda: finish_refresh(payload=None, error=f"UI refresh failed: {_trim_text(exc, limit=140)}"))

        thread = threading.Thread(target=worker, name="ui-refresh", daemon=True)
        fetch_thread["value"] = thread
        thread.start()

    def finish_refresh(*, payload=None, error: str = ""):
        fetch_inflight["value"] = False
        fetch_thread["value"] = None
        if error:
            connection_var.set(error)
            return
        if payload is not None:
            apply_view_payload(payload)

    def refresh_view():
        refresh_view_async()

    def copy_latest_reply():
        session = current_session_cache.get("session", {}) if isinstance(current_session_cache.get("session", {}), dict) else {}
        messages = current_session_cache.get("messages", []) if isinstance(current_session_cache.get("messages", []), list) else []
        content = latest_reply_text(session, messages)
        if not content:
            messagebox.showinfo("Copy Reply", "There is no reply to copy yet.")
            return
        try:
            root.clipboard_clear()
            root.clipboard_append(content)
            root.update_idletasks()
            connection_var.set("Copied latest reply")
        except Exception as exc:
            messagebox.showwarning("Copy Reply", f"Unable to copy the reply: {exc}")

    def request_refresh(delay_ms: int = 80):
        if refresh_handle["value"] is not None:
            return
        refresh_handle["value"] = root.after(delay_ms, run_requested_refresh)

    def run_requested_refresh():
        refresh_handle["value"] = None
        refresh_view()

    def schedule_refresh():
        if poll_handle["value"] is not None:
            try:
                root.after_cancel(poll_handle["value"])
            except Exception:
                pass
        poll_handle["value"] = root.after(15000, poll_refresh)

    def poll_refresh():
        refresh_view()
        schedule_refresh()

    def stop_event_stream():
        stop = stream_stop.get("value")
        if stop is not None:
            stop.set()
        stream = active_stream.get("value")
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        stream_stop["value"] = None
        active_stream["value"] = None
        stream_thread["value"] = None
        stream_connected["value"] = False

    def stream_loop(session_id: str, state_scope_id: str, stop_event):
        channel_key = stream_key(session_id, state_scope_id)
        while not stop_event.is_set():
            stream = None
            try:
                last_event_id = _trim_text(last_event_ids.get(channel_key, ""), limit=80)
                stream = client.open_event_stream(session_id=session_id, state_scope_id=state_scope_id, last_event_id=last_event_id)
                active_stream["value"] = stream
                event_queue.put({"event": "stream.connected", "session_id": session_id, "state_scope_id": state_scope_id})
                for payload in stream.iter_events():
                    if stop_event.is_set():
                        break
                    if isinstance(payload, dict):
                        event_queue.put(payload)
            except LocalOperatorApiClientError as exc:
                if stop_event.is_set():
                    break
                event_queue.put({"event": "stream.disconnected", "session_id": session_id, "state_scope_id": state_scope_id, "data": {"message": _trim_text(exc, limit=180)}})
            except Exception as exc:
                if stop_event.is_set():
                    break
                event_queue.put({"event": "stream.disconnected", "session_id": session_id, "state_scope_id": state_scope_id, "data": {"message": _trim_text(exc, limit=180)}})
            finally:
                active_stream["value"] = None
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
            if stop_event.is_set():
                break
            time.sleep(1.5)

    def start_event_stream():
        stop_event_stream()
        session_id = selected_session_id["value"]
        state_scope_id = f"chat:{session_id}" if session_id else ""
        stop_event = threading.Event()
        stream_stop["value"] = stop_event
        thread = threading.Thread(target=stream_loop, args=(session_id, state_scope_id, stop_event), name="ui-event-stream", daemon=True)
        stream_thread["value"] = thread
        thread.start()

    def process_event_queue():
        should_refresh = False
        current_session_id = selected_session_id["value"]
        while True:
            try:
                payload = event_queue.get_nowait()
            except queue.Empty:
                break
            event_name = str(payload.get("event", "")).strip()
            event_session_id = str(payload.get("session_id", "")).strip()
            event_state_scope_id = str(payload.get("state_scope_id", "")).strip()
            event_channel_key = stream_key(event_session_id, event_state_scope_id)
            effective_session_id = event_session_id or current_session_id
            timeline_entry = _timeline_entry_from_event(payload)
            if effective_session_id and timeline_entry:
                append_timeline_entry(effective_session_id, timeline_entry)
            if event_session_id and current_session_id and event_session_id != current_session_id:
                continue
            if event_name == "stream.connected":
                stream_connected["value"] = True
                connection_var.set("Live updates connected")
                should_refresh = True
                continue
            if event_name == "stream.disconnected":
                stream_connected["value"] = False
                message = _trim_text(payload.get("data", {}).get("message", "Live updates reconnecting..."), limit=160)
                connection_var.set(message or "Live updates reconnecting...")
                continue
            if event_name == "stream.reset":
                last_event_ids[event_channel_key] = ""
                stream_connected["value"] = True
                should_refresh = True
                continue
            if payload.get("event_id"):
                last_event_ids[event_channel_key] = _trim_text(payload.get("event_id", ""), limit=80)
            if event_name == "stream.heartbeat":
                stream_connected["value"] = True
                continue
            stream_connected["value"] = True
            should_refresh = True
        if should_refresh:
            request_refresh(50)
        event_handle["value"] = root.after(180, process_event_queue)

    def select_session(_event=None):
        selection = sessions_list.curselection()
        if not selection:
            return
        index = int(selection[0])
        if 0 <= index < len(session_index):
            selected_session_id["value"] = session_index[index]
            refresh_view()
            start_event_stream()

    def create_session():
        try:
            result = client.create_session()
        except LocalOperatorApiClientError as exc:
            messagebox.showwarning("New Chat", str(exc))
            return
        selected_session_id["value"] = str(result.get("session", {}).get("session_id", "")).strip()
        composer.delete("1.0", "end")
        refresh_view()
        start_event_stream()
        composer.focus_set()

    def send_message():
        text = composer.get("1.0", "end").strip()
        if not text:
            return
        try:
            if selected_session_id["value"]:
                result = client.send_message(selected_session_id["value"], text)
            else:
                result = client.create_session(message=text)
            selected_session_id["value"] = str(result.get("session", {}).get("session_id", selected_session_id["value"])).strip()
        except LocalOperatorApiClientError as exc:
            messagebox.showwarning("Send Message", str(exc))
            return
        composer.delete("1.0", "end")
        refresh_view()
        start_event_stream()
        composer.focus_set()

    def approve_pending():
        try:
            client.approve_pending(session_id=selected_session_id["value"])
        except LocalOperatorApiClientError as exc:
            messagebox.showwarning("Approve", str(exc))
            return
        refresh_view()

    def reject_pending():
        try:
            client.reject_pending(session_id=selected_session_id["value"])
        except LocalOperatorApiClientError as exc:
            messagebox.showwarning("Reject", str(exc))
            return
        refresh_view()

    def toggle_details():
        details_visible["value"] = not details_visible["value"]
        if details_visible["value"]:
            details_toggle_text.set("Hide Details")
            details_body.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
            details_body.columnconfigure(0, weight=1)
            details_body.rowconfigure(0, weight=1)
            details_notebook.grid(row=0, column=0, sticky="nsew")
        else:
            details_toggle_text.set("Show Details")
            details_body.grid_forget()
        refresh_view()

    def on_enter(event):
        if (event.state & 0x1) != 0:
            return None
        send_message()
        return "break"

    def close_ui():
        if poll_handle["value"] is not None:
            try:
                root.after_cancel(poll_handle["value"])
            except Exception:
                pass
        if event_handle["value"] is not None:
            try:
                root.after_cancel(event_handle["value"])
            except Exception:
                pass
        if refresh_handle["value"] is not None:
            try:
                root.after_cancel(refresh_handle["value"])
            except Exception:
                pass
        stop_event_stream()
        if embedded_server is not None:
            try:
                embedded_server.shutdown()
            except Exception:
                pass
        root.destroy()

    new_chat_button.configure(command=create_session)
    refresh_button.configure(command=refresh_view)
    header_refresh_button.configure(command=refresh_view)
    copy_reply_button.configure(command=copy_latest_reply)
    send_button.configure(command=send_message)
    approve_button.configure(command=approve_pending)
    reject_button.configure(command=reject_pending)
    details_toggle.configure(command=toggle_details)
    sessions_list.bind("<<ListboxSelect>>", select_session)
    composer.bind("<Return>", on_enter)
    session_search_var.trace_add("write", rerender_sessions)
    root.protocol("WM_DELETE_WINDOW", close_ui)

    root.after(10, refresh_view)
    root.after(30, schedule_refresh)
    root.after(50, process_event_queue)
    root.after(70, composer.focus_set)
    root.mainloop()







