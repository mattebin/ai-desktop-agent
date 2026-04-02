from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Dict, Iterable, Set

from core.backend_schemas import backend_status, normalize_scheduler_job

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:
    BackgroundScheduler = None  # type: ignore[assignment]


def _trim_text(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _parse_local_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_local_now().tzinfo)
    return parsed.astimezone()


class BaseSchedulerBackend:
    name = "polling"

    def __init__(self, *, preferred_backend: str):
        self.preferred_backend = preferred_backend
        self._wake_event = threading.Event()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._last_message = ""

    def sync_scheduled_tasks(self, scheduled_tasks: Iterable[Dict[str, Any]]):
        self._jobs = {
            str(task.get("scheduled_id", "")).strip(): normalize_scheduler_job(task, backend=self.name, reason="scheduled")
            for task in scheduled_tasks
            if isinstance(task, dict) and str(task.get("scheduled_id", "")).strip()
        }
        self.notify_state_changed()

    def drain_due_scheduled_ids(self) -> Set[str]:
        return set()

    def wait(self, timeout_seconds: int):
        self._wake_event.wait(max(1, int(timeout_seconds or 1)))
        self._wake_event.clear()

    def notify_state_changed(self):
        self._wake_event.set()

    def status_snapshot(self) -> Dict[str, Any]:
        return backend_status(
            "scheduler",
            preferred=self.preferred_backend,
            active=self.name,
            available=True,
            reason="fallback_active" if self.name == "polling" and self.preferred_backend != self.name else "active",
            message=self._last_message or ("Using polling scheduler fallback." if self.name == "polling" else "Scheduler backend active."),
            metadata={
                "job_count": len(self._jobs),
                "jobs": list(self._jobs.values())[:12],
            },
        )

    def shutdown(self):
        self._wake_event.set()


class APSchedulerBackend(BaseSchedulerBackend):
    name = "apscheduler"

    def __init__(self, *, preferred_backend: str):
        super().__init__(preferred_backend=preferred_backend)
        self._lock = threading.RLock()
        self._due_ids: Set[str] = set()
        self._job_signatures: Dict[str, str] = {}
        self._scheduler = BackgroundScheduler(timezone=_local_now().tzinfo)
        self._scheduler.start(paused=False)
        self._last_message = "Using APScheduler for local scheduled jobs."

    def _mark_due(self, scheduled_id: str):
        with self._lock:
            if scheduled_id:
                self._due_ids.add(scheduled_id)
        self.notify_state_changed()

    def sync_scheduled_tasks(self, scheduled_tasks: Iterable[Dict[str, Any]]):
        scheduled_list = [task for task in scheduled_tasks if isinstance(task, dict)]
        wanted: Dict[str, str] = {}
        for task in scheduled_list:
            scheduled_id = str(task.get("scheduled_id", "")).strip()
            if not scheduled_id:
                continue
            normalized = normalize_scheduler_job(task, backend=self.name, reason="scheduled")
            self._jobs[scheduled_id] = normalized
            if str(task.get("status", "")).strip().lower() != "scheduled":
                continue
            next_run_at = _parse_local_datetime(task.get("next_run_at", ""))
            if next_run_at is None:
                continue
            signature = f"{task.get('status','')}|{task.get('next_run_at','')}|{task.get('recurrence','')}"
            wanted[scheduled_id] = signature
            if self._job_signatures.get(scheduled_id) == signature:
                continue
            self._scheduler.add_job(
                self._mark_due,
                trigger="date",
                run_date=next_run_at,
                kwargs={"scheduled_id": scheduled_id},
                id=scheduled_id,
                replace_existing=True,
                coalesce=True,
                misfire_grace_time=120,
                max_instances=1,
            )
            self._job_signatures[scheduled_id] = signature

        for scheduled_id in list(self._job_signatures.keys()):
            if scheduled_id in wanted:
                continue
            try:
                self._scheduler.remove_job(scheduled_id)
            except Exception:
                pass
            self._job_signatures.pop(scheduled_id, None)

        self._jobs = {
            scheduled_id: data
            for scheduled_id, data in self._jobs.items()
            if scheduled_id in wanted or scheduled_id in {str(task.get("scheduled_id", "")).strip() for task in scheduled_list}
        }
        self.notify_state_changed()

    def drain_due_scheduled_ids(self) -> Set[str]:
        with self._lock:
            due_ids = set(self._due_ids)
            self._due_ids.clear()
        return due_ids

    def shutdown(self):
        try:
            self._scheduler.shutdown(wait=False)
        except Exception:
            pass
        super().shutdown()


def create_scheduler_backend(settings: Dict[str, Any] | None = None) -> BaseSchedulerBackend:
    source_settings = settings if isinstance(settings, dict) else {}
    preferred = _trim_text(source_settings.get("scheduler_backend", "apscheduler"), limit=40).lower() or "apscheduler"
    if preferred == "apscheduler" and BackgroundScheduler is not None:
        try:
            return APSchedulerBackend(preferred_backend=preferred)
        except Exception:
            fallback = BaseSchedulerBackend(preferred_backend=preferred)
            fallback._last_message = "APScheduler could not start, so the polling scheduler fallback is active."
            return fallback

    backend = BaseSchedulerBackend(preferred_backend=preferred)
    if preferred != backend.name:
        backend._last_message = f"{preferred} is unavailable, so the polling scheduler fallback is active."
    else:
        backend._last_message = "Using polling scheduler fallback."
    return backend
