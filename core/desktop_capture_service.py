from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

from core.backend_schemas import backend_status
from tools.desktop import capture_desktop_evidence_frame, record_captured_desktop_evidence


DEFAULT_DESKTOP_AUTO_CAPTURE_INTERVAL_SECONDS = 3.0
DEFAULT_DESKTOP_AUTO_CAPTURE_MAX_EVENTS = 18
DEFAULT_DESKTOP_AUTO_CAPTURE_SCOPE = "primary_monitor"


def _trim_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _coerce_float(value: Any, default: float, *, minimum: float = 0.25, maximum: float = 300.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _safe_unlink(path: str):
    candidate = Path(str(path or "").strip())
    if not candidate:
        return
    try:
        if candidate.exists() and candidate.is_file():
            candidate.unlink()
    except Exception:
        return


class DesktopCaptureService:
    def __init__(self, settings: Dict[str, Any] | None = None, *, context_getter: Callable[[], Dict[str, Any]] | None = None):
        self.settings = dict(settings or {})
        self.context_getter = context_getter
        self.enabled = bool(self.settings.get("desktop_auto_capture_enabled", False))
        self.interval_seconds = _coerce_float(
            self.settings.get("desktop_auto_capture_interval_seconds", DEFAULT_DESKTOP_AUTO_CAPTURE_INTERVAL_SECONDS),
            DEFAULT_DESKTOP_AUTO_CAPTURE_INTERVAL_SECONDS,
            minimum=0.5,
            maximum=60.0,
        )
        self.scope = str(self.settings.get("desktop_auto_capture_scope", DEFAULT_DESKTOP_AUTO_CAPTURE_SCOPE)).strip().lower() or DEFAULT_DESKTOP_AUTO_CAPTURE_SCOPE
        self.max_events = max(4, min(int(self.settings.get("desktop_auto_capture_max_events", DEFAULT_DESKTOP_AUTO_CAPTURE_MAX_EVENTS) or DEFAULT_DESKTOP_AUTO_CAPTURE_MAX_EVENTS), 64))
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_signature = ""
        self._last_window_id = ""
        self._last_state_scope_id = ""
        self._last_task_id = ""
        self._last_checkpoint_key = ""
        self._last_capture_at = ""
        self._last_result: Dict[str, Any] = {}
        self._recent_events: List[Dict[str, Any]] = []
        self._capture_count = 0
        self._recorded_count = 0
        self._duplicate_count = 0

    def start(self):
        if not self.enabled:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._runner, name="desktop-auto-capture", daemon=True)
            self._thread.start()

    def shutdown(self):
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(1.0, min(self.interval_seconds * 2.0, 4.0)))

    def status_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            last_result = dict(self._last_result)
            recent_events = [dict(item) for item in self._recent_events[-8:]]
            latest = dict(recent_events[-1]) if recent_events else {}
            return {
                **backend_status(
                    "desktop_auto_capture",
                    preferred="desktop_auto_capture",
                    active="desktop_auto_capture" if self.enabled else "disabled",
                    available=self.enabled,
                    reason=str(latest.get("reason", "active" if running else "disabled")),
                    message=str(latest.get("summary", "Continuous desktop capture is active." if running else "Continuous desktop capture is disabled.")),
                    metadata={
                        "interval_seconds": self.interval_seconds,
                        "scope": self.scope,
                        "running": running,
                        "captures": self._capture_count,
                        "recorded": self._recorded_count,
                        "duplicates_skipped": self._duplicate_count,
                    },
                ),
                "enabled": self.enabled,
                "running": running,
                "latest": latest,
                "recent_events": recent_events,
                "last_result": last_result,
            }

    def _current_context(self) -> Dict[str, Any]:
        if not callable(self.context_getter):
            return {}
        try:
            context = self.context_getter() or {}
        except Exception:
            return {}
        return context if isinstance(context, dict) else {}

    def _record_event_locked(self, *, reason: str, summary: str, context: Dict[str, Any], evidence_id: str = "", recorded: bool = False, duplicate: bool = False):
        event = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "reason": _trim_text(reason, limit=60),
            "summary": _trim_text(summary, limit=220),
            "state_scope_id": _trim_text(context.get("state_scope_id", ""), limit=120),
            "task_id": _trim_text(context.get("task_id", ""), limit=60),
            "task_status": _trim_text(context.get("task_status", ""), limit=40),
            "checkpoint_pending": bool(context.get("checkpoint_pending", False)),
            "checkpoint_tool": _trim_text(context.get("checkpoint_tool", ""), limit=80),
            "checkpoint_target": _trim_text(context.get("checkpoint_target", ""), limit=180),
            "active_window_title": _trim_text(context.get("active_window_title", ""), limit=180),
            "evidence_id": _trim_text(evidence_id, limit=80),
            "recorded": bool(recorded),
            "duplicate": bool(duplicate),
        }
        self._recent_events.append(event)
        if len(self._recent_events) > self.max_events:
            del self._recent_events[:-self.max_events]
        self._last_result = event

    def _runner(self):
        while not self._stop.is_set():
            try:
                self.capture_once()
            except Exception as exc:
                with self._lock:
                    self._record_event_locked(
                        reason="error",
                        summary=f"Automatic desktop capture failed: {_trim_text(exc, limit=180)}",
                        context={},
                    )
            self._stop.wait(self.interval_seconds)

    def capture_once(self) -> Dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "reason": "disabled"}

        context = self._current_context()
        with self._lock:
            self._capture_count += 1

        capture = capture_desktop_evidence_frame(
            scope=self.scope,
            source_action="desktop_auto_capture",
            limit=12,
            include_ui_evidence=False,
            ui_limit=4,
            capture_mode="auto",
            state_scope_id=str(context.get("state_scope_id", "")).strip(),
            task_id=str(context.get("task_id", "")).strip(),
            task_status=str(context.get("task_status", "")).strip(),
            checkpoint_pending=bool(context.get("checkpoint_pending", False)),
            checkpoint_tool=str(context.get("checkpoint_tool", "")).strip(),
            checkpoint_target=str(context.get("checkpoint_target", "")).strip(),
            record_on_error=False,
            record_evidence=False,
        )
        if not capture.get("ok", False) or not str(capture.get("screenshot_path", "")).strip():
            with self._lock:
                self._record_event_locked(
                    reason="waiting",
                    summary=str(capture.get("error", "")).strip() or "Automatic desktop capture is waiting for a capturable active window.",
                    context=context,
                )
            return {"ok": False, "reason": "waiting"}

        capture_signature = _trim_text(capture.get("capture_signature", ""), limit=120)
        active_window = capture.get("active_window", {}) if isinstance(capture.get("active_window", {}), dict) else {}
        active_window_id = _trim_text(active_window.get("window_id", ""), limit=40)
        state_scope_id = _trim_text(context.get("state_scope_id", ""), limit=120)
        task_id = _trim_text(context.get("task_id", ""), limit=60)
        checkpoint_key = "|".join(
            [
                "1" if bool(context.get("checkpoint_pending", False)) else "0",
                _trim_text(context.get("checkpoint_tool", ""), limit=80),
                _trim_text(context.get("checkpoint_target", ""), limit=180),
            ]
        )

        with self._lock:
            duplicate = (
                bool(capture_signature)
                and capture_signature == self._last_signature
                and active_window_id == self._last_window_id
                and state_scope_id == self._last_state_scope_id
                and task_id == self._last_task_id
                and checkpoint_key == self._last_checkpoint_key
            )
            if duplicate:
                self._duplicate_count += 1
                self._record_event_locked(
                    reason="duplicate_frame",
                    summary=f"Skipped an unchanged automatic desktop capture for {_trim_text(active_window.get('title', 'the active window'), limit=160)}.",
                    context={**context, "active_window_title": active_window.get("title", "")},
                    duplicate=True,
                )
        if duplicate:
            _safe_unlink(str(capture.get("screenshot_path", "")).strip())
            return {"ok": True, "recorded": False, "reason": "duplicate_frame"}

        importance = "normal"
        importance_reason = "state_changed"
        if bool(context.get("checkpoint_pending", False)):
            importance = "checkpoint"
            importance_reason = "checkpoint_pending"
        elif not self._last_signature:
            importance = "important"
            importance_reason = "initial_context"
        elif task_id and task_id != self._last_task_id:
            importance = "important"
            importance_reason = "task_changed"
        elif state_scope_id and state_scope_id != self._last_state_scope_id:
            importance = "important"
            importance_reason = "session_scope_changed"
        elif active_window_id and active_window_id != self._last_window_id:
            importance = "important"
            importance_reason = "window_changed"

        screenshot = capture.get("screenshot", {}) if isinstance(capture.get("screenshot", {}), dict) else {}
        observation = capture.get("observation", {}) if isinstance(capture.get("observation", {}), dict) else {}
        target_window = capture.get("target_window", {}) if isinstance(capture.get("target_window", {}), dict) else {}
        evidence_bundle, evidence_ref = record_captured_desktop_evidence(
            source_action="desktop_auto_capture",
            active_window=active_window,
            windows=capture.get("windows", []) if isinstance(capture.get("windows", []), list) else [],
            observation=observation,
            screenshot=screenshot,
            target_window=target_window,
            include_ui_evidence=False,
            ui_limit=4,
            errors=[],
            bundle_metadata={
                "capture_mode": "auto",
                "importance": importance,
                "importance_reason": importance_reason,
                "state_scope_id": state_scope_id,
                "task_id": task_id,
                "task_status": _trim_text(context.get("task_status", ""), limit=40),
                "checkpoint_pending": bool(context.get("checkpoint_pending", False)),
                "checkpoint_tool": _trim_text(context.get("checkpoint_tool", ""), limit=80),
                "checkpoint_target": _trim_text(context.get("checkpoint_target", ""), limit=180),
                "capture_signature": capture_signature,
            },
        )

        evidence_id = _trim_text(evidence_ref.get("evidence_id", "") or evidence_bundle.get("evidence_id", ""), limit=80)
        with self._lock:
            self._last_signature = capture_signature
            self._last_window_id = active_window_id
            self._last_state_scope_id = state_scope_id
            self._last_task_id = task_id
            self._last_checkpoint_key = checkpoint_key
            self._last_capture_at = _trim_text(evidence_ref.get("timestamp", "") or evidence_bundle.get("timestamp", ""), limit=40)
            self._recorded_count += 1
            self._record_event_locked(
                reason="captured",
                summary=str(evidence_ref.get("summary", "") or evidence_bundle.get("summary", "")).strip() or "Recorded an automatic desktop capture.",
                context={**context, "active_window_title": active_window.get("title", "")},
                evidence_id=evidence_id,
                recorded=True,
            )
        return {"ok": True, "recorded": True, "evidence_id": evidence_id}
