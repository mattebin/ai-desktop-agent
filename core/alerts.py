from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4


DEFAULT_ALERT_HISTORY_PATH = "data/alert_history.json"
DEFAULT_MAX_ALERTS = 40
ALERT_HISTORY_VERSION = 1
ALERT_SEVERITIES = {"info", "success", "warning", "error"}


def _trim_text(value: Any, limit: int = 240) -> str:
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _iso_timestamp(value: float | None = None) -> str:
    try:
        if value is None:
            source = datetime.now().astimezone()
        else:
            source = datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone()
        return source.isoformat(timespec="seconds")
    except Exception:
        return ""


def _normalize_severity(value: Any) -> str:
    text = str(value).strip().lower()
    if text in ALERT_SEVERITIES:
        return text
    return "info"


def _normalize_alert_item(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    alert_id = _trim_text(value.get("alert_id", ""), limit=60)
    if not alert_id:
        return {}

    return {
        "alert_id": alert_id,
        "created_at": _trim_text(value.get("created_at", ""), limit=40) or _iso_timestamp(),
        "severity": _normalize_severity(value.get("severity", "info")),
        "type": _trim_text(value.get("type", ""), limit=60),
        "source": _trim_text(value.get("source", ""), limit=60),
        "title": _trim_text(value.get("title", ""), limit=120),
        "message": _trim_text(value.get("message", ""), limit=320),
        "goal": _trim_text(value.get("goal", ""), limit=220),
        "task_id": _trim_text(value.get("task_id", ""), limit=60),
        "scheduled_id": _trim_text(value.get("scheduled_id", ""), limit=60),
        "watch_id": _trim_text(value.get("watch_id", ""), limit=60),
        "run_id": _trim_text(value.get("run_id", ""), limit=60),
        "session_id": _trim_text(value.get("session_id", ""), limit=80),
        "state_scope_id": _trim_text(value.get("state_scope_id", ""), limit=120),
    }


class AlertStore:
    def __init__(self, path: str | Path, *, max_items: int = DEFAULT_MAX_ALERTS):
        self.path = Path(path)
        self.max_items = max(1, int(max_items))

    def next_alert_id(self) -> str:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"alert-{timestamp}-{uuid4().hex[:8]}"

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"alerts": []}

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"alerts": []}

        if not isinstance(payload, dict):
            return {"alerts": []}

        items: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        for raw_item in payload.get("alerts", []):
            item = _normalize_alert_item(raw_item)
            alert_id = item.get("alert_id", "")
            if not alert_id or alert_id in seen_ids:
                continue
            seen_ids.add(alert_id)
            items.append(item)

        return {"alerts": self._trim_alerts(items)}

    def save(self, alerts: List[Dict[str, Any]]) -> bool:
        payload = {
            "version": ALERT_HISTORY_VERSION,
            "updated_at": _iso_timestamp(),
            "alerts": self._trim_alerts(alerts),
        }

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            return False
        return True

    def _trim_alerts(self, alerts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = [_normalize_alert_item(item) for item in alerts]
        normalized = [item for item in normalized if item]
        return normalized[-self.max_items :]


def alert_counts(alerts: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"total": 0}
    for severity in sorted(ALERT_SEVERITIES):
        counts[severity] = 0

    for item in alerts:
        severity = _normalize_severity(item.get("severity", "info"))
        counts[severity] = counts.get(severity, 0) + 1
        counts["total"] += 1
    return counts


def alert_summary(alert: Dict[str, Any]) -> Dict[str, Any]:
    item = _normalize_alert_item(alert)
    if not item:
        return {}
    return {
        "alert_id": item.get("alert_id", ""),
        "created_at": item.get("created_at", ""),
        "severity": item.get("severity", ""),
        "type": item.get("type", ""),
        "source": item.get("source", ""),
        "title": item.get("title", ""),
        "message": item.get("message", ""),
        "goal": item.get("goal", ""),
        "task_id": item.get("task_id", ""),
        "scheduled_id": item.get("scheduled_id", ""),
        "watch_id": item.get("watch_id", ""),
        "run_id": item.get("run_id", ""),
        "session_id": item.get("session_id", ""),
        "state_scope_id": item.get("state_scope_id", ""),
    }

