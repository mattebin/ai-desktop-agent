from __future__ import annotations

import json
from typing import Any, Dict
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_LOCAL_API_TIMEOUT_SECONDS = 4.0
DEFAULT_LOCAL_API_STREAM_TIMEOUT_SECONDS = 45.0


class LocalOperatorApiClientError(RuntimeError):
    pass


def _trim_text(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


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
            self._response = urlopen(request, timeout=self.timeout_seconds)
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
