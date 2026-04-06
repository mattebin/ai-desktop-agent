from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_LOCAL_API_TIMEOUT_SECONDS = 4.0
DEFAULT_LOCAL_API_STREAM_TIMEOUT_SECONDS = 45.0
LOCAL_API_TERMINAL_STATUSES = {"completed", "failed", "blocked", "incomplete", "stopped", "superseded", "deferred"}


class LocalOperatorApiClientError(RuntimeError):
    pass


def _trim_text(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _authoritative_session_reply(session_payload: Dict[str, Any] | None) -> str:
    payload = session_payload if isinstance(session_payload, dict) else {}
    session = payload.get("session", {}) if isinstance(payload.get("session", {}), dict) else {}
    authoritative = (
        session.get("authoritative_reply", {})
        if isinstance(session.get("authoritative_reply", {}), dict)
        else {}
    )
    content = _trim_text(authoritative.get("content", ""), limit=220)
    if content:
        return content
    last_result = _trim_text(session.get("last_result_message", ""), limit=220)
    if last_result:
        return last_result
    messages = session.get("messages", []) if isinstance(session.get("messages", []), list) else []
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if str(message.get("role", "")).strip().lower() != "assistant":
            continue
        content = _trim_text(message.get("content", ""), limit=220)
        if content:
            return content
    return ""


def _status_wait_debug(snapshot: Dict[str, Any] | None, session_payload: Dict[str, Any] | None = None) -> str:
    current = snapshot if isinstance(snapshot, dict) else {}
    active_task = current.get("active_task", {}) if isinstance(current.get("active_task", {}), dict) else {}
    progress = active_task.get("progress", {}) if isinstance(active_task.get("progress", {}), dict) else {}
    latest_run = current.get("latest_run", {}) if isinstance(current.get("latest_run", {}), dict) else {}
    pending = current.get("pending_approval", {}) if isinstance(current.get("pending_approval", {}), dict) else {}
    parts = [
        f"status={_trim_text(current.get('status', ''), limit=40) or '<none>'}",
        f"running={bool(current.get('running', False))}",
        f"active_task_status={_trim_text(active_task.get('status', ''), limit=40) or '<none>'}",
        f"progress_stage={_trim_text(progress.get('stage', ''), limit=40) or '<none>'}",
        f"latest_run_status={_trim_text(latest_run.get('final_status', ''), limit=40) or '<none>'}",
        f"pending_approval={_trim_text(pending.get('kind', ''), limit=40) or '<none>'}",
    ]
    reply = _authoritative_session_reply(session_payload)
    if reply:
        parts.append(f"reply={_trim_text(reply, limit=120)}")
    return ", ".join(parts)


def wait_for_local_api_status(
    status_getter: Callable[[], Dict[str, Any]],
    statuses: Iterable[str],
    *,
    timeout_seconds: float = 120.0,
    interval_seconds: float = 0.75,
    session_getter: Callable[[], Dict[str, Any]] | None = None,
    session_label: str = "",
) -> Dict[str, Any]:
    wanted = {str(status).strip().lower() for status in statuses if str(status).strip()}
    if not wanted:
        raise ValueError("At least one desired status is required.")

    deadline = time.time() + max(1.0, float(timeout_seconds))
    interval = max(0.05, float(interval_seconds))
    last_snapshot: Dict[str, Any] = {}
    last_session: Dict[str, Any] = {}
    stable_terminal_status = ""
    stable_terminal_polls = 0

    while time.time() < deadline:
        last_snapshot = status_getter() if callable(status_getter) else {}
        current_status = str(last_snapshot.get("status", "")).strip().lower()

        if current_status in wanted:
            if current_status not in LOCAL_API_TERMINAL_STATUSES:
                return last_snapshot

            active_task = last_snapshot.get("active_task", {}) if isinstance(last_snapshot.get("active_task", {}), dict) else {}
            latest_run = last_snapshot.get("latest_run", {}) if isinstance(last_snapshot.get("latest_run", {}), dict) else {}
            active_status = str(active_task.get("status", "")).strip().lower()
            latest_run_status = str(latest_run.get("final_status", "")).strip().lower()
            running = bool(last_snapshot.get("running", False))

            if current_status == stable_terminal_status:
                stable_terminal_polls += 1
            else:
                stable_terminal_status = current_status
                stable_terminal_polls = 1

            terminal_snapshot_ready = (
                not running
                and active_status in {"", current_status}
                and latest_run_status in {"", current_status}
            )
            if terminal_snapshot_ready:
                return last_snapshot

            if stable_terminal_polls >= 2:
                if callable(session_getter):
                    try:
                        last_session = session_getter()
                    except Exception:
                        last_session = {}
                return last_snapshot
        else:
            stable_terminal_status = ""
            stable_terminal_polls = 0

        time.sleep(interval)

    session_suffix = f" for {session_label}" if str(session_label).strip() else ""
    raise TimeoutError(
        f"Timed out waiting for status {sorted(wanted)}{session_suffix}. "
        f"Last snapshot: {_status_wait_debug(last_snapshot, last_session)}"
    )


class LocalOperatorApiClient:
    def __init__(self, base_url: str, *, timeout_seconds: float = DEFAULT_LOCAL_API_TIMEOUT_SECONDS):
        normalized = str(base_url or "").strip().rstrip("/")
        if not normalized:
            raise ValueError("A local API base URL is required.")
        self.base_url = normalized
        self.timeout_seconds = max(0.5, float(timeout_seconds))

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Dict[str, Any] | None = None,
        body: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        request_path = "/" + str(path or "").lstrip("/")
        query_items = {key: value for key, value in (query or {}).items() if value not in ("", None)}
        url = f"{self.base_url}{request_path}"
        if query_items:
            url += "?" + urlencode(query_items)

        headers = {"Accept": "application/json"}
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        request = Request(url=url, data=payload, method=method.upper(), headers=headers)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw_body = response.read()
        except HTTPError as exc:
            raw_error = exc.read()
            message = self._decode_error(raw_error) or f"Local API returned HTTP {exc.code}."
            raise LocalOperatorApiClientError(message) from exc
        except URLError as exc:
            raise LocalOperatorApiClientError(f"Unable to reach the local API at {self.base_url}.") from exc
        except Exception as exc:
            raise LocalOperatorApiClientError("Local API request failed.") from exc

        try:
            parsed = json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            raise LocalOperatorApiClientError("Local API returned invalid JSON.") from exc

        if not isinstance(parsed, dict):
            raise LocalOperatorApiClientError("Local API returned an unexpected response.")
        if not parsed.get("ok", False):
            raise LocalOperatorApiClientError(_trim_text(parsed.get("error", "Local API request failed."), limit=280))
        data = parsed.get("data", {})
        if not isinstance(data, dict):
            raise LocalOperatorApiClientError("Local API returned an unexpected payload.")
        return data

    def _decode_error(self, raw_error: bytes) -> str:
        if not raw_error:
            return ""
        try:
            parsed = json.loads(raw_error.decode("utf-8"))
        except Exception:
            return ""
        if isinstance(parsed, dict):
            return _trim_text(parsed.get("error", ""), limit=280)
        return ""

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health")

    def get_status(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        return self._request("GET", "/status", query={"session_id": session_id, "state_scope_id": state_scope_id})

    def get_snapshot(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        return self._request("GET", "/snapshot", query={"session_id": session_id, "state_scope_id": state_scope_id})

    def get_active_task(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        return self._request("GET", "/tasks/active", query={"session_id": session_id, "state_scope_id": state_scope_id})

    def get_recent_runs(self, *, limit: int = 6, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        return self._request(
            "GET",
            "/runs/recent",
            query={"limit": limit, "session_id": session_id, "state_scope_id": state_scope_id},
        )

    def get_alerts(self, *, limit: int = 10, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        return self._request(
            "GET",
            "/alerts",
            query={"limit": limit, "session_id": session_id, "state_scope_id": state_scope_id},
        )

    def get_queue(self) -> Dict[str, Any]:
        return self._request("GET", "/queue")

    def get_scheduled(self) -> Dict[str, Any]:
        return self._request("GET", "/scheduled")

    def get_watches(self) -> Dict[str, Any]:
        return self._request("GET", "/watches")

    def list_sessions(self, *, limit: int = 12) -> Dict[str, Any]:
        return self._request("GET", "/sessions", query={"limit": limit})

    def create_session(self, *, title: str = "", message: str = "") -> Dict[str, Any]:
        return self._request("POST", "/sessions", body={"title": title, "message": message})

    def get_session(self, session_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/sessions/{session_id}")

    def get_session_messages(self, session_id: str, *, limit: int = 40) -> Dict[str, Any]:
        return self._request("GET", f"/sessions/{session_id}/messages", query={"limit": limit})

    def send_message(self, session_id: str, message: str) -> Dict[str, Any]:
        return self._request("POST", f"/sessions/{session_id}/messages", body={"message": message})

    def start_goal(self, goal: str, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        return self._request(
            "POST",
            "/goals/start",
            body={"goal": goal, "session_id": session_id, "state_scope_id": state_scope_id},
        )

    def queue_goal(self, goal: str, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        return self._request(
            "POST",
            "/goals/queue",
            body={"goal": goal, "session_id": session_id, "state_scope_id": state_scope_id},
        )

    def approve_pending(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        return self._request(
            "POST",
            "/approval/approve",
            body={"session_id": session_id, "state_scope_id": state_scope_id},
        )

    def reject_pending(self, *, session_id: str = "", state_scope_id: str = "") -> Dict[str, Any]:
        return self._request(
            "POST",
            "/approval/reject",
            body={"session_id": session_id, "state_scope_id": state_scope_id},
        )

    def wait_for_status(
        self,
        session_id: str,
        statuses: Iterable[str],
        *,
        state_scope_id: str = "",
        timeout_seconds: float = 120.0,
        interval_seconds: float = 0.75,
    ) -> Dict[str, Any]:
        session_id_value = str(session_id or "").strip()
        state_scope_value = str(state_scope_id or "").strip()
        return wait_for_local_api_status(
            lambda: self.get_status(session_id=session_id_value, state_scope_id=state_scope_value),
            statuses,
            timeout_seconds=timeout_seconds,
            interval_seconds=interval_seconds,
            session_getter=(lambda: self.get_session(session_id_value)) if session_id_value else None,
            session_label=session_id_value or state_scope_value,
        )

    def open_event_stream(
        self,
        *,
        session_id: str = "",
        state_scope_id: str = "",
        last_event_id: str = "",
        timeout_seconds: float = DEFAULT_LOCAL_API_STREAM_TIMEOUT_SECONDS,
    ) -> "LocalOperatorApiEventStream":
        query_items = {key: value for key, value in {"session_id": session_id, "state_scope_id": state_scope_id}.items() if value not in ("", None)}
        url = f"{self.base_url}/events/stream"
        if query_items:
            url += "?" + urlencode(query_items)
        return LocalOperatorApiEventStream(url, last_event_id=last_event_id, timeout_seconds=timeout_seconds)


class LocalOperatorApiEventStream:
    def __init__(self, url: str, *, last_event_id: str = "", timeout_seconds: float = DEFAULT_LOCAL_API_STREAM_TIMEOUT_SECONDS):
        self.url = str(url or "").strip()
        self.last_event_id = _trim_text(last_event_id, limit=80)
        self.timeout_seconds = max(5.0, float(timeout_seconds))
        self._response = None
        self._closed = False

    def open(self):
        if self._response is not None:
            return self
        self._closed = False
        headers = {"Accept": "text/event-stream"}
        if self.last_event_id:
            headers["Last-Event-ID"] = self.last_event_id
        request = Request(url=self.url, method="GET", headers=headers)
        try:
            # SSE streams are long-lived and already self-regulate via heartbeats.
            # A short socket read timeout makes healthy streams look broken when the
            # server is busy bootstrapping an initial snapshot or between frames.
            self._response = urlopen(request, timeout=max(60.0, self.timeout_seconds))
        except HTTPError as exc:
            raw_error = exc.read()
            message = "Local API event stream failed."
            if raw_error:
                try:
                    parsed = json.loads(raw_error.decode("utf-8"))
                    if isinstance(parsed, dict):
                        message = _trim_text(parsed.get("error", message), limit=280)
                except Exception:
                    pass
            raise LocalOperatorApiClientError(message) from exc
        except URLError as exc:
            raise LocalOperatorApiClientError(f"Unable to reach the local API at {self.url}.") from exc
        except Exception as exc:
            raise LocalOperatorApiClientError("Unable to open the local API event stream.") from exc
        return self

    def close(self):
        self._closed = True
        if self._response is not None:
            try:
                self._response.close()
            except Exception:
                pass
            self._response = None

    def iter_events(self):
        self.open()
        event_name = "message"
        event_id = ""
        data_lines = []
        while not self._closed and self._response is not None:
            raw_line = self._response.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8").rstrip("\r\n")
            if not line:
                if data_lines:
                    raw_payload = "\n".join(data_lines)
                    try:
                        payload = json.loads(raw_payload)
                    except Exception:
                        payload = {"event": event_name, "data_text": _trim_text(raw_payload, limit=1200)}
                    if isinstance(payload, dict):
                        payload.setdefault("event", event_name)
                        if event_id and not payload.get("event_id"):
                            payload["event_id"] = event_id
                        if payload.get("event_id"):
                            self.last_event_id = _trim_text(payload.get("event_id", ""), limit=80)
                        yield payload
                event_name = "message"
                event_id = ""
                data_lines = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip() or "message"
                continue
            if line.startswith("id:"):
                event_id = line[3:].strip()
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines and not self._closed:
            raw_payload = "\n".join(data_lines)
            try:
                payload = json.loads(raw_payload)
            except Exception:
                payload = {"event": event_name, "data_text": _trim_text(raw_payload, limit=1200)}
            if isinstance(payload, dict):
                payload.setdefault("event", event_name)
                if event_id and not payload.get("event_id"):
                    payload["event_id"] = event_id
                if payload.get("event_id"):
                    self.last_event_id = _trim_text(payload.get("event_id", ""), limit=80)
                yield payload
        self.close()
