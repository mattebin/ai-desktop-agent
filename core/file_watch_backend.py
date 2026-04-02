from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

from core.backend_schemas import backend_status, normalize_file_watch_event

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer
except Exception:
    FileSystemEvent = None  # type: ignore[assignment]
    FileSystemEventHandler = object  # type: ignore[assignment]
    Observer = None  # type: ignore[assignment]


FILE_WATCH_SUPPORTED_CONDITIONS = {"file_exists", "file_changed"}


def _trim_text(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _normalize_path_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return str(Path(text).resolve()).lower()
    except Exception:
        return text.lower()


def _parse_timestamp(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    else:
        parsed = parsed.astimezone()
    return parsed.timestamp()


class BaseFileWatchBackend:
    name = "polling"

    def __init__(self, *, preferred_backend: str):
        self.preferred_backend = preferred_backend
        self._last_message = ""

    def sync_watches(self, watches: Iterable[Dict[str, Any]]):
        return

    def consume_events(self, limit: int = 24) -> List[Dict[str, Any]]:
        return []

    def has_recent_signal(self, target_path: str, *, since_timestamp: float = 0.0) -> bool:
        return False

    def status_snapshot(self) -> Dict[str, Any]:
        return backend_status(
            "file_watch",
            preferred=self.preferred_backend,
            active=self.name,
            available=True,
            reason="fallback_active" if self.name == "polling" and self.preferred_backend != self.name else "active",
            message=self._last_message or ("Using polling file-watch fallback." if self.name == "polling" else "File-watch backend active."),
            metadata={},
        )

    def shutdown(self):
        return


class _WatchdogHandler(FileSystemEventHandler):
    def __init__(self, backend: "WatchdogFileWatchBackend"):
        self.backend = backend

    def on_any_event(self, event: FileSystemEvent):  # type: ignore[override]
        self.backend.record_event(event)


class WatchdogFileWatchBackend(BaseFileWatchBackend):
    name = "watchdog"

    def __init__(self, *, preferred_backend: str):
        super().__init__(preferred_backend=preferred_backend)
        self._lock = threading.RLock()
        self._observer: Observer | None = None
        self._directories: List[str] = []
        self._targets: List[str] = []
        self._events: List[Dict[str, Any]] = []
        self._latest_by_path: Dict[str, float] = {}
        self._last_message = "Using watchdog for local file-watch subscriptions."

    def _restart_observer(self, directories: List[str]):
        observer = self._observer
        if observer is not None:
            try:
                observer.stop()
                observer.join(timeout=2)
            except Exception:
                pass
            self._observer = None

        if not directories:
            self._directories = []
            return

        observer = Observer()
        handler = _WatchdogHandler(self)
        active_directories: List[str] = []
        for directory in directories:
            if not Path(directory).exists():
                continue
            observer.schedule(handler, directory, recursive=False)
            active_directories.append(directory)
        if not active_directories:
            self._directories = []
            return
        observer.start()
        self._observer = observer
        self._directories = list(active_directories)

    def sync_watches(self, watches: Iterable[Dict[str, Any]]):
        targets: List[str] = []
        directories: List[str] = []
        seen_dirs: set[str] = set()

        for watch in watches:
            if not isinstance(watch, dict):
                continue
            condition_type = str(watch.get("condition_type", "")).strip().lower()
            if condition_type not in FILE_WATCH_SUPPORTED_CONDITIONS:
                continue
            target_path = _normalize_path_text(watch.get("target", ""))
            if not target_path:
                continue
            targets.append(target_path)
            try:
                parent = str(Path(target_path).resolve().parent)
            except Exception:
                parent = str(Path(target_path).parent)
            parent = parent.lower()
            if parent and parent not in seen_dirs:
                seen_dirs.add(parent)
                directories.append(parent)

        with self._lock:
            self._targets = targets
            if directories != self._directories:
                self._restart_observer(directories)

    def record_event(self, event: FileSystemEvent):
        src_path = _normalize_path_text(getattr(event, "src_path", ""))
        dest_path = _normalize_path_text(getattr(event, "dest_path", ""))
        if not src_path and not dest_path:
            return
        timestamp = time.time()
        with self._lock:
            target_path = src_path or dest_path
            normalized = normalize_file_watch_event(
                backend=self.name,
                event_type=str(getattr(event, "event_type", "changed") or "changed"),
                src_path=src_path,
                dest_path=dest_path,
                target_path=target_path,
                is_directory=bool(getattr(event, "is_directory", False)),
                reason="filesystem_event",
            )
            normalized["timestamp_unix"] = timestamp
            self._events.append(normalized)
            self._events = self._events[-48:]
            for path in {src_path, dest_path}:
                if path:
                    self._latest_by_path[path] = timestamp

    def consume_events(self, limit: int = 24) -> List[Dict[str, Any]]:
        with self._lock:
            if not self._events:
                return []
            events = list(self._events[-max(1, int(limit or 1)):])
            self._events.clear()
            return events

    def has_recent_signal(self, target_path: str, *, since_timestamp: float = 0.0) -> bool:
        normalized_target = _normalize_path_text(target_path)
        if not normalized_target:
            return False
        with self._lock:
            latest = self._latest_by_path.get(normalized_target, 0.0)
            if latest > since_timestamp:
                return True
            parent = str(Path(normalized_target).parent).lower()
            for path, event_timestamp in self._latest_by_path.items():
                if event_timestamp <= since_timestamp:
                    continue
                if path == normalized_target or path.startswith(parent + "\\"):
                    return True
        return False

    def status_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            metadata = {
                "directory_count": len(self._directories),
                "target_count": len(self._targets),
                "buffered_event_count": len(self._events),
                "directories": list(self._directories)[:8],
            }
        return backend_status(
            "file_watch",
            preferred=self.preferred_backend,
            active=self.name,
            available=True,
            reason="active",
            message=self._last_message,
            metadata=metadata,
        )

    def shutdown(self):
        with self._lock:
            observer = self._observer
            self._observer = None
        if observer is not None:
            try:
                observer.stop()
                observer.join(timeout=2)
            except Exception:
                pass


def create_file_watch_backend(settings: Dict[str, Any] | None = None) -> BaseFileWatchBackend:
    source_settings = settings if isinstance(settings, dict) else {}
    preferred = _trim_text(source_settings.get("file_watch_backend", "watchdog"), limit=40).lower() or "watchdog"
    if preferred == "watchdog" and Observer is not None and FileSystemEvent is not None:
        try:
            return WatchdogFileWatchBackend(preferred_backend=preferred)
        except Exception:
            fallback = BaseFileWatchBackend(preferred_backend=preferred)
            fallback._last_message = "watchdog could not start, so the polling file-watch fallback is active."
            return fallback

    backend = BaseFileWatchBackend(preferred_backend=preferred)
    if preferred != backend.name:
        backend._last_message = f"{preferred} is unavailable, so the polling file-watch fallback is active."
    else:
        backend._last_message = "Using polling file-watch fallback."
    return backend
