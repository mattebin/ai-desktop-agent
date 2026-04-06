from __future__ import annotations

import base64
import binascii
import html
import json
import re
from datetime import datetime
from email.mime.text import MIMEText
from email.utils import formatdate, parseaddr
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Iterable, List, Tuple

from core.config import PROJECT_ROOT, load_settings


DEFAULT_EMAIL_DRAFTS_PATH = "data/email_drafts.json"
DEFAULT_GMAIL_CLIENT_SECRET_PATH = "config/gmail-client-secret.json"
DEFAULT_GMAIL_TOKEN_PATH = "data/gmail_token.json"
DEFAULT_GMAIL_WATCH_QUERY = "label:inbox newer_than:7d"
DEFAULT_GMAIL_MAX_THREADS = 12
DEFAULT_GMAIL_MAX_THREAD_MESSAGES = 8
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
_EMAIL_SERVICE_LOCK = RLock()
_EMAIL_SERVICE: "EmailService | None" = None


def _trim_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _trim_multiline(value: Any, limit: int = 2000) -> str:
    text = str(value or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _coerce_int(value: Any, default: int, *, minimum: int = 0, maximum: int = 100) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _resolve_project_path(value: Any, *, default_relative_path: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raw = default_relative_path
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    return PROJECT_ROOT / candidate


def _load_json_dict(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_json_dict(path: Path, payload: Dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        return False
    return True


def _gmail_settings(settings: Dict[str, Any] | None = None) -> Dict[str, Any]:
    source = settings if isinstance(settings, dict) else load_settings()
    payload = source.get("gmail", {}) if isinstance(source.get("gmail", {}), dict) else {}
    return dict(payload)


def _dependency_error() -> str:
    return (
        "Gmail support requires google-api-python-client, google-auth-oauthlib, and "
        "google-auth-httplib2. Install the updated requirements first."
    )


def _gmail_dependency_available() -> Tuple[bool, str]:
    try:
        from google.auth.transport.requests import Request  # noqa: F401
        from google.oauth2.credentials import Credentials  # noqa: F401
        from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: F401
        from googleapiclient.discovery import build  # noqa: F401
    except Exception:
        return False, _dependency_error()
    return True, ""


def _normalize_email_address(value: Any) -> str:
    _name, address = parseaddr(str(value or ""))
    return address.strip().lower()


def _format_person(value: Any) -> str:
    name, address = parseaddr(str(value or ""))
    if name and address:
        return f"{name} <{address}>"
    return address or name or str(value or "").strip()


def _header_map(headers: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    mapped: Dict[str, str] = {}
    for item in headers:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip().lower()
        value = str(item.get("value", "")).strip()
        if name and value:
            mapped[name] = value
    return mapped


def _urlsafe_b64decode(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    padding = "=" * ((4 - len(text) % 4) % 4)
    try:
        return base64.urlsafe_b64decode((text + padding).encode("utf-8")).decode("utf-8", errors="replace")
    except (binascii.Error, ValueError):
        return ""


def _strip_html(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", str(value or ""))
    text = re.sub(r"(?is)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p\\s*>", "\n\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_gmail_body(payload: Dict[str, Any] | None) -> str:
    part = payload if isinstance(payload, dict) else {}
    mime_type = str(part.get("mimeType", "")).strip().lower()
    body_data = _urlsafe_b64decode(part.get("body", {}).get("data", "") if isinstance(part.get("body", {}), dict) else "")

    if mime_type == "text/plain" and body_data.strip():
        return body_data.strip()
    if mime_type == "text/html" and body_data.strip():
        return _strip_html(body_data)

    for child in list(part.get("parts", [])):
        if not isinstance(child, dict):
            continue
        nested = _extract_gmail_body(child)
        if nested.strip():
            return nested.strip()

    if body_data.strip():
        return _strip_html(body_data)
    return ""


def _message_summary(message: Dict[str, Any], *, self_address: str = "", body_limit: int = 2400) -> Dict[str, Any]:
    headers = _header_map(message.get("payload", {}).get("headers", []) if isinstance(message.get("payload", {}), dict) else [])
    label_ids = [str(item).strip() for item in list(message.get("labelIds", [])) if str(item).strip()]
    from_value = headers.get("from", "")
    from_address = _normalize_email_address(from_value)
    body_text = _trim_multiline(_extract_gmail_body(message.get("payload", {})), limit=body_limit)
    return {
        "message_id": str(message.get("id", "")).strip(),
        "thread_id": str(message.get("threadId", "")).strip(),
        "subject": headers.get("subject", ""),
        "from": _format_person(from_value),
        "from_address": from_address,
        "to": headers.get("to", ""),
        "cc": headers.get("cc", ""),
        "reply_to": headers.get("reply-to", ""),
        "date": headers.get("date", ""),
        "message_id_header": headers.get("message-id", ""),
        "in_reply_to": headers.get("in-reply-to", ""),
        "references": headers.get("references", ""),
        "snippet": _trim_text(message.get("snippet", ""), limit=180),
        "body_text": body_text,
        "label_ids": label_ids,
        "unread": "UNREAD" in label_ids,
        "sent_by_self": bool(self_address and from_address == self_address),
    }


def _thread_summary(thread: Dict[str, Any], *, self_address: str = "", include_messages: bool = True) -> Dict[str, Any]:
    messages = [
        _message_summary(item, self_address=self_address, body_limit=1200)
        for item in list(thread.get("messages", []))
        if isinstance(item, dict)
    ]
    latest = messages[-1] if messages else {}
    last_external = latest
    if self_address:
        for item in reversed(messages):
            if not item.get("sent_by_self", False):
                last_external = item
                break
    return {
        "thread_id": str(thread.get("id", "")).strip(),
        "history_id": str(thread.get("historyId", "")).strip(),
        "message_count": len(messages),
        "snippet": _trim_text(thread.get("snippet", ""), limit=200) or str(latest.get("snippet", "")).strip(),
        "subject": str(latest.get("subject", "") or last_external.get("subject", "")).strip(),
        "last_from": str(last_external.get("from", "") or latest.get("from", "")).strip(),
        "last_from_address": str(last_external.get("from_address", "") or latest.get("from_address", "")).strip(),
        "last_date": str(latest.get("date", "")).strip(),
        "last_message_id": str(latest.get("message_id", "")).strip(),
        "unread": any(bool(item.get("unread", False)) for item in messages),
        **({"messages": messages} if include_messages else {}),
    }


class EmailDraftStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = RLock()

    def _load_locked(self) -> Dict[str, Any]:
        payload = _load_json_dict(self.path)
        drafts = payload.get("drafts", {})
        if not isinstance(drafts, dict):
            drafts = {}
        return {"drafts": drafts}

    def _save_locked(self, drafts: Dict[str, Any]) -> bool:
        return _save_json_dict(
            self.path,
            {
                "version": 1,
                "updated_at": _iso_now(),
                "drafts": drafts,
            },
        )

    def list(self, *, status: str = "", limit: int = 24) -> List[Dict[str, Any]]:
        safe_limit = _coerce_int(limit, 24, minimum=1, maximum=100)
        normalized_status = str(status or "").strip().lower()
        with self._lock:
            drafts = self._load_locked().get("drafts", {})
            items = [value for value in drafts.values() if isinstance(value, dict)]
        items.sort(key=lambda item: str(item.get("updated_at", "")).strip(), reverse=True)
        if normalized_status:
            items = [item for item in items if str(item.get("status", "")).strip().lower() == normalized_status]
        return items[:safe_limit]

    def get(self, draft_id: str) -> Dict[str, Any]:
        with self._lock:
            drafts = self._load_locked().get("drafts", {})
            value = drafts.get(str(draft_id).strip(), {})
            return dict(value) if isinstance(value, dict) else {}

    def save(self, draft: Dict[str, Any]) -> bool:
        draft_id = str(draft.get("draft_id", "")).strip()
        if not draft_id:
            return False
        with self._lock:
            payload = self._load_locked()
            drafts = payload.get("drafts", {})
            drafts[draft_id] = dict(draft)
            return self._save_locked(drafts)

    def update(self, draft_id: str, **changes: Any) -> Dict[str, Any]:
        safe_id = str(draft_id).strip()
        if not safe_id:
            return {}
        with self._lock:
            payload = self._load_locked()
            drafts = payload.get("drafts", {})
            current = drafts.get(safe_id, {})
            if not isinstance(current, dict):
                return {}
            updated = dict(current)
            updated.update(changes)
            updated["updated_at"] = _iso_now()
            drafts[safe_id] = updated
            if not self._save_locked(drafts):
                return {}
            return updated


class EmailService:
    def __init__(self, settings: Dict[str, Any] | None = None):
        self.settings = dict(settings) if isinstance(settings, dict) else load_settings()
        self._lock = RLock()
        self._draft_store = EmailDraftStore(self._draft_store_path())

    def reload_settings(self, settings: Dict[str, Any] | None = None) -> Dict[str, Any]:
        with self._lock:
            if isinstance(settings, dict):
                self.settings = dict(settings)
            else:
                self.settings = load_settings()
            self._draft_store = EmailDraftStore(self._draft_store_path())
            return dict(self.settings)

    def _gmail_config(self) -> Dict[str, Any]:
        return _gmail_settings(self.settings)

    def _draft_store_path(self) -> Path:
        return _resolve_project_path(
            self._gmail_config().get("draft_state_path", ""),
            default_relative_path=DEFAULT_EMAIL_DRAFTS_PATH,
        )

    def _gmail_client_secret_path(self) -> Path:
        return _resolve_project_path(
            self._gmail_config().get("client_secrets_path", ""),
            default_relative_path=DEFAULT_GMAIL_CLIENT_SECRET_PATH,
        )

    def _gmail_token_path(self) -> Path:
        return _resolve_project_path(
            self._gmail_config().get("token_path", ""),
            default_relative_path=DEFAULT_GMAIL_TOKEN_PATH,
        )

    def _gmail_enabled(self) -> bool:
        return _coerce_bool(self._gmail_config().get("enabled", False))

    def _gmail_scope_list(self) -> List[str]:
        scopes = self._gmail_config().get("scopes", [])
        if isinstance(scopes, list):
            values = [str(item).strip() for item in scopes if str(item).strip()]
        elif isinstance(scopes, str):
            values = [part.strip() for part in scopes.split(",") if part.strip()]
        else:
            values = []
        return values or [GMAIL_SCOPE]

    def _draft_summary(self, draft: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "draft_id": str(draft.get("draft_id", "")).strip(),
            "draft_type": str(draft.get("draft_type", "")).strip(),
            "status": str(draft.get("status", "")).strip(),
            "provider": str(draft.get("provider", "")).strip(),
            "thread_id": str(draft.get("thread_id", "")).strip(),
            "message_id": str(draft.get("message_id", "")).strip(),
            "to": list(draft.get("to", [])) if isinstance(draft.get("to", []), list) else [],
            "cc": list(draft.get("cc", [])) if isinstance(draft.get("cc", []), list) else [],
            "subject": str(draft.get("subject", "")).strip(),
            "summary": str(draft.get("summary", "")).strip(),
            "confidence": str(draft.get("confidence", "")).strip(),
            "needs_context": bool(draft.get("needs_context", False)),
            "questions": [str(item).strip() for item in list(draft.get("questions", [])) if str(item).strip()],
            "updated_at": str(draft.get("updated_at", "")).strip(),
            "created_at": str(draft.get("created_at", "")).strip(),
        }

    def status_snapshot(self) -> Dict[str, Any]:
        dependency_available, dependency_error = _gmail_dependency_available()
        client_secret_path = self._gmail_client_secret_path()
        token_path = self._gmail_token_path()
        drafts = self._draft_store.list(limit=50)
        authenticated = False
        token_present = token_path.exists()
        token_valid = False
        profile_email = ""

        if dependency_available and token_present:
            try:
                from google.oauth2.credentials import Credentials

                credentials = Credentials.from_authorized_user_file(str(token_path), self._gmail_scope_list())
                token_valid = bool(credentials and credentials.valid)
                authenticated = bool(token_valid or credentials.refresh_token)
            except Exception:
                authenticated = False
                token_valid = False
        if dependency_available and authenticated:
            try:
                service = self._gmail_api(interactive_auth=False)
                profile = service.users().getProfile(userId="me").execute()
                profile_email = str(profile.get("emailAddress", "")).strip()
            except Exception:
                profile_email = ""

        return {
            "provider": "gmail",
            "enabled": self._gmail_enabled(),
            "configured": client_secret_path.exists(),
            "dependency_available": dependency_available,
            "dependency_error": dependency_error,
            "client_secrets_path": str(client_secret_path),
            "token_path": str(token_path),
            "token_present": token_present,
            "authenticated": authenticated,
            "token_valid": token_valid,
            "profile_email": profile_email,
            "watch_enabled": _coerce_bool(self._gmail_config().get("watch_enabled", False)),
            "watch_query": str(self._gmail_config().get("watch_query", DEFAULT_GMAIL_WATCH_QUERY)).strip() or DEFAULT_GMAIL_WATCH_QUERY,
            "poll_seconds": _coerce_int(self._gmail_config().get("poll_seconds", 60), 60, minimum=15, maximum=3600),
            "scopes": self._gmail_scope_list(),
            "restricted_scope_notice": (
                "Gmail OAuth scopes such as gmail.modify are restricted by Google and may require OAuth app verification for broad distribution."
            ),
            "draft_counts": {
                "total": len(drafts),
                "prepared": len([item for item in drafts if str(item.get("status", "")).strip() == "prepared"]),
                "sent": len([item for item in drafts if str(item.get("status", "")).strip() == "sent"]),
                "rejected": len([item for item in drafts if str(item.get("status", "")).strip() == "rejected"]),
            },
            "last_checked_at": _iso_now(),
        }

    def _ensure_gmail_available(self, *, interactive_auth: bool = False):
        enabled = self._gmail_enabled()
        if not enabled:
            raise RuntimeError("Gmail support is disabled in config/settings.yaml.")
        dependency_available, dependency_error = _gmail_dependency_available()
        if not dependency_available:
            raise RuntimeError(dependency_error)
        client_secret_path = self._gmail_client_secret_path()
        if not client_secret_path.exists():
            raise RuntimeError(
                f"Gmail OAuth client secrets file is missing at {client_secret_path}. "
                "Add a Desktop OAuth client JSON file there first."
            )

        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        token_path = self._gmail_token_path()
        scopes = self._gmail_scope_list()
        credentials = None
        if token_path.exists():
            try:
                credentials = Credentials.from_authorized_user_file(str(token_path), scopes)
            except Exception:
                credentials = None

        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
                token_path.parent.mkdir(parents=True, exist_ok=True)
                token_path.write_text(credentials.to_json(), encoding="utf-8")
            except Exception:
                credentials = None

        if credentials and credentials.valid:
            return credentials

        if not interactive_auth:
            raise RuntimeError("Gmail is not connected yet. Use /gmail-connect or POST /email/connect first.")

        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), scopes)
        credentials = flow.run_local_server(
            port=0,
            open_browser=True,
            authorization_prompt_message=(
                "Opening your browser to connect Gmail for the local AI operator. Complete the Google sign-in flow there."
            ),
            success_message="Gmail is connected. You can close this tab and return to the app.",
        )
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(credentials.to_json(), encoding="utf-8")
        return credentials

    def _gmail_api(self, *, interactive_auth: bool = False):
        credentials = self._ensure_gmail_available(interactive_auth=interactive_auth)
        from googleapiclient.discovery import build

        return build("gmail", "v1", credentials=credentials, cache_discovery=False)

    def connect_gmail(self) -> Dict[str, Any]:
        try:
            service = self._gmail_api(interactive_auth=True)
            profile = service.users().getProfile(userId="me").execute()
        except Exception as exc:
            return {
                "ok": False,
                "provider": "gmail",
                "error": _trim_text(exc, limit=280),
                "status": self.status_snapshot(),
            }
        status = self.status_snapshot()
        status["authenticated"] = True
        status["token_valid"] = True
        status["profile_email"] = str(profile.get("emailAddress", "")).strip()
        return {
            "ok": True,
            "provider": "gmail",
            "message": "Gmail connected successfully.",
            "status": status,
        }

    def list_threads(self, *, limit: int = DEFAULT_GMAIL_MAX_THREADS, query: str = "", label_ids: List[str] | None = None) -> Dict[str, Any]:
        safe_limit = _coerce_int(limit, DEFAULT_GMAIL_MAX_THREADS, minimum=1, maximum=50)
        labels = [str(item).strip() for item in list(label_ids or ["INBOX"]) if str(item).strip()]
        try:
            service = self._gmail_api(interactive_auth=False)
            profile = service.users().getProfile(userId="me").execute()
            response = service.users().threads().list(
                userId="me",
                maxResults=safe_limit,
                labelIds=labels,
                q=str(query or "").strip(),
            ).execute()
            thread_refs = list(response.get("threads", []))
            items: List[Dict[str, Any]] = []
            for ref in thread_refs:
                thread = service.users().threads().get(
                    userId="me",
                    id=str(ref.get("id", "")).strip(),
                    format="metadata",
                    metadataHeaders=[
                        "Subject",
                        "From",
                        "To",
                        "Cc",
                        "Reply-To",
                        "Date",
                        "Message-ID",
                        "In-Reply-To",
                        "References",
                    ],
                ).execute()
                items.append(_thread_summary(thread, self_address=_normalize_email_address(profile.get("emailAddress", "")), include_messages=False))
        except Exception as exc:
            return {
                "ok": False,
                "provider": "gmail",
                "error": _trim_text(exc, limit=280),
                "items": [],
            }
        return {
            "ok": True,
            "provider": "gmail",
            "profile_email": str(profile.get("emailAddress", "")).strip(),
            "query": str(query or "").strip(),
            "label_ids": labels,
            "items": items,
        }

    def read_thread(self, thread_id: str, *, max_messages: int = DEFAULT_GMAIL_MAX_THREAD_MESSAGES) -> Dict[str, Any]:
        safe_thread_id = str(thread_id or "").strip()
        if not safe_thread_id:
            return {"ok": False, "error": "thread_id is required."}
        safe_max = _coerce_int(max_messages, DEFAULT_GMAIL_MAX_THREAD_MESSAGES, minimum=1, maximum=40)
        try:
            service = self._gmail_api(interactive_auth=False)
            profile = service.users().getProfile(userId="me").execute()
            thread = service.users().threads().get(userId="me", id=safe_thread_id, format="full").execute()
        except Exception as exc:
            return {"ok": False, "error": _trim_text(exc, limit=280)}

        summary = _thread_summary(thread, self_address=_normalize_email_address(profile.get("emailAddress", "")))
        messages = list(summary.get("messages", []))[-safe_max:]
        return {
            "ok": True,
            "provider": "gmail",
            "profile_email": str(profile.get("emailAddress", "")).strip(),
            "thread": {
                **summary,
                "messages": messages,
            },
        }

    def list_drafts(self, *, status: str = "", limit: int = 24) -> Dict[str, Any]:
        return {
            "ok": True,
            "items": [self._draft_summary(item) for item in self._draft_store.list(status=status, limit=limit)],
        }

    def get_draft(self, draft_id: str) -> Dict[str, Any]:
        draft = self._draft_store.get(str(draft_id or "").strip())
        if not draft:
            return {"ok": False, "error": "Draft not found."}
        payload = dict(draft)
        payload["draft"] = dict(draft)
        payload["summary"] = self._draft_summary(draft)
        payload["ok"] = True
        return payload

    def reject_draft(self, draft_id: str, *, reason: str = "Rejected by operator.") -> Dict[str, Any]:
        updated = self._draft_store.update(
            str(draft_id or "").strip(),
            status="rejected",
            rejected_at=_iso_now(),
            rejection_reason=_trim_text(reason, limit=220),
        )
        if not updated:
            return {"ok": False, "error": "Draft not found."}
        return {
            "ok": True,
            "message": "Draft marked as rejected.",
            "draft": updated,
            "summary": self._draft_summary(updated),
        }

    def _reply_target(self, thread: Dict[str, Any], *, self_address: str) -> Dict[str, Any]:
        messages = list(thread.get("messages", []))
        latest = messages[-1] if messages else {}
        reply_candidate = latest
        for item in reversed(messages):
            if not item.get("sent_by_self", False):
                reply_candidate = item
                break
        reply_to = str(reply_candidate.get("reply_to", "")).strip()
        from_value = reply_to or str(reply_candidate.get("from", "")).strip()
        address = _normalize_email_address(from_value)
        return {
            "to": [from_value] if from_value else [],
            "to_address": address,
            "cc": [],
            "subject": str(reply_candidate.get("subject", "")).strip(),
            "message_id_header": str(reply_candidate.get("message_id_header", "")).strip(),
            "references": str(reply_candidate.get("references", "")).strip(),
            "latest_message_id": str(reply_candidate.get("message_id", "")).strip(),
            "self_address": self_address,
        }

    def _get_llm(self):
        from core.llm_client import HostedLLMClient

        return HostedLLMClient(settings=self.settings)

    def _generate_reply_payload(
        self,
        *,
        thread: Dict[str, Any],
        guidance: str = "",
        user_context: str = "",
    ) -> Dict[str, Any]:
        llm = self._get_llm()
        thread_messages = list(thread.get("messages", []))[-6:]
        transcript_lines: List[str] = []
        for message in thread_messages:
            transcript_lines.append(
                "\n".join(
                    [
                        f"From: {message.get('from', '')}",
                        f"To: {message.get('to', '')}",
                        f"Date: {message.get('date', '')}",
                        f"Subject: {message.get('subject', '')}",
                        "Body:",
                        str(message.get("body_text", "")).strip() or str(message.get("snippet", "")).strip(),
                    ]
                )
            )
        prompt = (
            "Draft a plain-text email reply for the following thread.\n"
            "Return valid JSON only with keys: disposition, subject, body, summary, confidence, questions.\n"
            "disposition must be one of: reply, needs_context, no_reply.\n"
            "If context is missing, set disposition to needs_context, leave body empty, and add concise questions.\n"
            "Keep the draft professional, concise, and specific.\n"
        )
        if guidance.strip():
            prompt += f"\nAdditional reply guidance:\n{guidance.strip()}\n"
        if user_context.strip():
            prompt += f"\nKnown user context:\n{user_context.strip()}\n"
        prompt += "\nThread:\n" + "\n\n---\n\n".join(transcript_lines)
        response = llm.reply_in_chat(prompt, mode="chat")
        try:
            parsed = json.loads(str(response).strip())
        except Exception:
            parsed = {
                "disposition": "reply",
                "subject": str(thread.get("subject", "")).strip(),
                "body": str(response).strip(),
                "summary": "Prepared a plain-text reply draft.",
                "confidence": "medium",
                "questions": [],
            }
        if not isinstance(parsed, dict):
            parsed = {}
        disposition = str(parsed.get("disposition", "reply")).strip().lower()
        if disposition not in {"reply", "needs_context", "no_reply"}:
            disposition = "reply"
        questions = [str(item).strip() for item in list(parsed.get("questions", [])) if str(item).strip()]
        subject = str(parsed.get("subject", "")).strip() or str(thread.get("subject", "")).strip()
        if subject and not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        return {
            "disposition": disposition,
            "subject": subject,
            "body": _trim_multiline(parsed.get("body", ""), limit=5000),
            "summary": _trim_text(parsed.get("summary", "") or "Prepared a reply draft.", limit=220),
            "confidence": str(parsed.get("confidence", "medium")).strip() or "medium",
            "questions": questions[:4],
        }

    def prepare_reply_draft(
        self,
        *,
        thread_id: str,
        guidance: str = "",
        user_context: str = "",
    ) -> Dict[str, Any]:
        thread_result = self.read_thread(thread_id, max_messages=DEFAULT_GMAIL_MAX_THREAD_MESSAGES)
        if not thread_result.get("ok", False):
            return thread_result

        thread = thread_result.get("thread", {})
        self_address = _normalize_email_address(thread_result.get("profile_email", ""))
        reply_target = self._reply_target(thread, self_address=self_address)
        try:
            generated = self._generate_reply_payload(thread=thread, guidance=guidance, user_context=user_context)
        except Exception as exc:
            return {"ok": False, "error": _trim_text(exc, limit=280)}

        if generated.get("disposition") != "reply" or not str(generated.get("body", "")).strip():
            return {
                "ok": True,
                "provider": "gmail",
                "thread": thread,
                "disposition": generated.get("disposition", "needs_context"),
                "needs_context": generated.get("disposition") == "needs_context",
                "summary": generated.get("summary", ""),
                "confidence": generated.get("confidence", ""),
                "questions": generated.get("questions", []),
            }

        draft_id = f"gmail-draft-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        draft = {
            "draft_id": draft_id,
            "provider": "gmail",
            "draft_type": "reply",
            "status": "prepared",
            "created_at": _iso_now(),
            "updated_at": _iso_now(),
            "thread_id": str(thread.get("thread_id", "")).strip(),
            "message_id": str(reply_target.get("latest_message_id", "")).strip(),
            "to": list(reply_target.get("to", [])),
            "cc": list(reply_target.get("cc", [])),
            "subject": str(generated.get("subject", "")).strip(),
            "body": str(generated.get("body", "")).strip(),
            "summary": str(generated.get("summary", "")).strip(),
            "confidence": str(generated.get("confidence", "")).strip(),
            "questions": list(generated.get("questions", [])),
            "needs_context": False,
            "reply_headers": {
                "message_id_header": str(reply_target.get("message_id_header", "")).strip(),
                "references": str(reply_target.get("references", "")).strip(),
            },
            "thread_snapshot": thread,
        }
        if not self._draft_store.save(draft):
            return {"ok": False, "error": "Failed to store the prepared Gmail draft."}
        return {
            "ok": True,
            "provider": "gmail",
            "draft": draft,
            "summary": self._draft_summary(draft),
        }

    def prepare_forward_draft(
        self,
        *,
        thread_id: str,
        to: List[str] | None = None,
        note: str = "",
    ) -> Dict[str, Any]:
        recipients = [str(item).strip() for item in list(to or []) if str(item).strip()]
        if not recipients:
            return {"ok": False, "error": "At least one forward recipient is required."}
        thread_result = self.read_thread(thread_id, max_messages=DEFAULT_GMAIL_MAX_THREAD_MESSAGES)
        if not thread_result.get("ok", False):
            return thread_result

        thread = thread_result.get("thread", {})
        messages = list(thread.get("messages", []))
        latest = messages[-1] if messages else {}
        subject = str(latest.get("subject", "")).strip()
        if subject and not subject.lower().startswith("fwd:"):
            subject = f"Fwd: {subject}"
        forwarded_body = str(latest.get("body_text", "")).strip() or str(latest.get("snippet", "")).strip()
        body_parts = []
        if str(note).strip():
            body_parts.append(str(note).strip())
        body_parts.extend(
            [
                "",
                "---------- Forwarded message ---------",
                f"From: {latest.get('from', '')}",
                f"Date: {latest.get('date', '')}",
                f"Subject: {latest.get('subject', '')}",
                f"To: {latest.get('to', '')}",
                "",
                forwarded_body,
            ]
        )
        draft_id = f"gmail-draft-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        draft = {
            "draft_id": draft_id,
            "provider": "gmail",
            "draft_type": "forward",
            "status": "prepared",
            "created_at": _iso_now(),
            "updated_at": _iso_now(),
            "thread_id": str(thread.get("thread_id", "")).strip(),
            "message_id": str(latest.get("message_id", "")).strip(),
            "to": recipients,
            "cc": [],
            "subject": subject,
            "body": "\n".join(body_parts).strip(),
            "summary": "Prepared a forward draft from the latest message in the thread.",
            "confidence": "high",
            "questions": [],
            "needs_context": False,
            "reply_headers": {},
            "thread_snapshot": thread,
        }
        if not self._draft_store.save(draft):
            return {"ok": False, "error": "Failed to store the forward draft."}
        return {
            "ok": True,
            "provider": "gmail",
            "draft": draft,
            "summary": self._draft_summary(draft),
        }

    def _build_message_bytes(self, draft: Dict[str, Any]) -> bytes:
        message = MIMEText(str(draft.get("body", "")).strip(), "plain", "utf-8")
        message["To"] = ", ".join(str(item).strip() for item in list(draft.get("to", [])) if str(item).strip())
        cc_values = [str(item).strip() for item in list(draft.get("cc", [])) if str(item).strip()]
        if cc_values:
            message["Cc"] = ", ".join(cc_values)
        message["Subject"] = str(draft.get("subject", "")).strip()
        message["Date"] = formatdate(localtime=True)
        reply_headers = draft.get("reply_headers", {}) if isinstance(draft.get("reply_headers", {}), dict) else {}
        message_id_header = str(reply_headers.get("message_id_header", "")).strip()
        references = str(reply_headers.get("references", "")).strip()
        if message_id_header:
            message["In-Reply-To"] = message_id_header
            message["References"] = (references + " " + message_id_header).strip() if references else message_id_header
        elif references:
            message["References"] = references
        return message.as_bytes()

    def send_draft(self, draft_id: str, *, approved: bool = False) -> Dict[str, Any]:
        draft = self._draft_store.get(str(draft_id or "").strip())
        if not draft:
            return {"ok": False, "error": "Draft not found."}
        if str(draft.get("status", "")).strip() == "sent":
            return {
                "ok": True,
                "message": "Draft was already sent earlier.",
                "draft": draft,
                "summary": self._draft_summary(draft),
            }
        if not approved:
            return {
                "ok": False,
                "paused": True,
                "approval_required": True,
                "approval_status": "not approved",
                "draft_id": str(draft.get("draft_id", "")).strip(),
                "summary": str(draft.get("summary", "")).strip() or "Prepared Gmail draft is waiting for approval before sending.",
                "reason": "Prepared Gmail draft is waiting for explicit approval before sending.",
                "target": ", ".join(str(item).strip() for item in list(draft.get("to", [])) if str(item).strip()),
                "subject": str(draft.get("subject", "")).strip(),
                "draft": self._draft_summary(draft),
            }

        try:
            service = self._gmail_api(interactive_auth=False)
            raw = base64.urlsafe_b64encode(self._build_message_bytes(draft)).decode("utf-8")
            body: Dict[str, Any] = {"raw": raw}
            thread_id = str(draft.get("thread_id", "")).strip()
            if thread_id:
                body["threadId"] = thread_id
            sent = service.users().messages().send(userId="me", body=body).execute()
        except Exception as exc:
            return {
                "ok": False,
                "error": _trim_text(exc, limit=280),
                "draft": self._draft_summary(draft),
            }

        updated = self._draft_store.update(
            str(draft.get("draft_id", "")).strip(),
            status="sent",
            sent_at=_iso_now(),
            sent_message_id=str(sent.get("id", "")).strip(),
            approval_status="approved",
        )
        safe_draft = updated or draft
        return {
            "ok": True,
            "provider": "gmail",
            "message": "Sent the Gmail draft successfully.",
            "summary": str(safe_draft.get("summary", "")).strip() or "Sent the approved Gmail draft.",
            "draft": self._draft_summary(safe_draft),
            "sent": {
                "message_id": str(sent.get("id", "")).strip(),
                "thread_id": str(sent.get("threadId", "")).strip(),
                "label_ids": [str(item).strip() for item in list(sent.get("labelIds", [])) if str(item).strip()],
            },
        }


def get_email_service(settings: Dict[str, Any] | None = None) -> EmailService:
    global _EMAIL_SERVICE
    with _EMAIL_SERVICE_LOCK:
        if _EMAIL_SERVICE is None:
            _EMAIL_SERVICE = EmailService(settings=settings)
        elif settings is not None:
            _EMAIL_SERVICE.reload_settings(settings=settings)
        else:
            _EMAIL_SERVICE.reload_settings()
        return _EMAIL_SERVICE
