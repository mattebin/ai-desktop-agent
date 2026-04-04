from __future__ import annotations

import os
import time
from typing import Any, Dict, List


class StartupProfiler:
    def __init__(self, label: str, *, enabled: bool | None = None):
        self.label = str(label or "startup").strip() or "startup"
        self.enabled = bool(enabled) if enabled is not None else str(os.environ.get("AI_OPERATOR_PROFILE_STARTUP", "")).strip() == "1"
        self._started = time.perf_counter()
        self._last = self._started
        self._events: List[Dict[str, Any]] = []

    def mark(self, event: str, detail: str = ""):
        if not self.enabled:
            return
        now = time.perf_counter()
        self._events.append(
            {
                "event": str(event or "").strip() or "step",
                "detail": str(detail or "").strip(),
                "elapsed_ms": round((now - self._started) * 1000, 1),
                "delta_ms": round((now - self._last) * 1000, 1),
            }
        )
        self._last = now

    def snapshot(self) -> Dict[str, Any]:
        total_ms = round((time.perf_counter() - self._started) * 1000, 1)
        return {
            "label": self.label,
            "enabled": self.enabled,
            "total_ms": total_ms,
            "events": list(self._events),
        }

    def emit(self):
        if not self.enabled:
            return
        snapshot = self.snapshot()
        print(f"[STARTUP] {snapshot['label']} total={snapshot['total_ms']}ms")
        for event in snapshot.get("events", []):
            detail = f" ({event.get('detail', '')})" if event.get("detail") else ""
            print(
                f"[STARTUP]  - {event.get('event', '')}: "
                f"+{event.get('delta_ms', 0)}ms / {event.get('elapsed_ms', 0)}ms{detail}"
            )
