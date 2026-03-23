from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List
from uuid import uuid4

from core.backend_schemas import (
    normalize_desktop_evidence_ref,
    normalize_screen_observation,
    normalize_screenshot_observation,
    normalize_ui_evidence_observation,
    normalize_window_descriptor,
    result_envelope,
)
from core.config import load_settings

try:
    import mss
except Exception:
    mss = None  # type: ignore[assignment]


DEFAULT_DESKTOP_EVIDENCE_ROOT = "data/desktop_evidence"
DEFAULT_MAX_DESKTOP_EVIDENCE_ITEMS = 32

_STORE_LOCK = threading.RLock()
_STORE: "DesktopEvidenceStore | None" = None


def _trim_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _iso_timestamp() -> str:
    try:
        return datetime.now().astimezone().isoformat(timespec="seconds")
    except Exception:
        return ""


def _coerce_int(value: Any, default: int, *, minimum: int = 0, maximum: int = 100_000) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _sanitize_controls(value: Any, *, limit: int = 12) -> List[Dict[str, Any]]:
    controls = value if isinstance(value, list) else []
    normalized: List[Dict[str, Any]] = []
    for item in controls[:limit]:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "name": _trim_text(item.get("name", ""), limit=160),
                "control_type": _trim_text(item.get("control_type", ""), limit=80),
                "automation_id": _trim_text(item.get("automation_id", ""), limit=120),
                "text": _trim_text(item.get("text", ""), limit=220),
            }
        )
    return normalized


def _sanitize_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
    active_window = normalize_window_descriptor(bundle.get("active_window", {}), backend=str(bundle.get("window_backend", "")), reason="inspected")
    target_window_raw = bundle.get("target_window", {})
    target_window = normalize_window_descriptor(target_window_raw, backend=str(bundle.get("window_backend", "")), reason="inspected") if isinstance(target_window_raw, dict) and target_window_raw else {}
    windows = [
        normalize_window_descriptor(item, backend=str(bundle.get("window_backend", "")), reason="inspected")
        for item in list(bundle.get("windows", []))[:12]
        if isinstance(item, dict)
    ]
    screenshot = bundle.get("screenshot", {}) if isinstance(bundle.get("screenshot", {}), dict) else {}
    normalized_screenshot = normalize_screenshot_observation(
        backend=str(screenshot.get("backend", bundle.get("screenshot_backend", ""))),
        path=str(screenshot.get("path", "")).strip(),
        scope=str(screenshot.get("scope", "")).strip(),
        bounds=screenshot.get("bounds", {}),
        active_window_title=str(screenshot.get("active_window_title", "") or active_window.get("title", "")),
        reason=str(screenshot.get("reason", bundle.get("reason", "partial"))),
        metadata=screenshot.get("metadata", {}),
    )
    ui_evidence = bundle.get("ui_evidence", {}) if isinstance(bundle.get("ui_evidence", {}), dict) else {}
    normalized_ui = normalize_ui_evidence_observation(
        backend=str(ui_evidence.get("backend", bundle.get("ui_evidence_backend", ""))),
        target=str(ui_evidence.get("target", "") or active_window.get("title", "")),
        controls=_sanitize_controls(ui_evidence.get("controls", [])),
        reason=str(ui_evidence.get("reason", bundle.get("reason", "partial"))),
        metadata=ui_evidence.get("metadata", {}),
    )
    screen = bundle.get("screen", {}) if isinstance(bundle.get("screen", {}), dict) else {}
    normalized_screen = normalize_screen_observation(
        virtual_screen=screen.get("virtual_screen", {}),
        monitors=screen.get("monitors", []),
        backend=str(screen.get("backend", "")),
        reason=str(screen.get("reason", "inspected")),
        metadata=screen.get("metadata", {}),
    )
    evidence_id = _trim_text(bundle.get("evidence_id", ""), limit=80) or f"desk-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
    reason = str(bundle.get("reason", "collected" if normalized_screenshot.get("path") else "partial")).strip().lower().replace(" ", "_")
    summary = _trim_text(
        bundle.get("summary", "")
        or (
            f"Collected desktop evidence for {active_window.get('title', 'desktop')}."
            if normalized_screenshot.get("path")
            else f"Collected partial desktop evidence for {active_window.get('title', 'desktop')}."
        ),
        limit=280,
    )
    bundle_path = _trim_text(bundle.get("bundle_path", ""), limit=320)
    source_action = _trim_text(bundle.get("source_action", ""), limit=80)
    observation_token = _trim_text(bundle.get("observation_token", ""), limit=120)
    window_backend = _trim_text(bundle.get("window_backend", active_window.get("backend", "")), limit=60)
    screenshot_backend = _trim_text(bundle.get("screenshot_backend", normalized_screenshot.get("backend", "")), limit=60)
    ui_backend = _trim_text(bundle.get("ui_evidence_backend", normalized_ui.get("backend", "")), limit=60)

    return {
        "evidence_id": evidence_id,
        "timestamp": _trim_text(bundle.get("timestamp", ""), limit=40) or _iso_timestamp(),
        "reason": reason,
        "summary": summary,
        "source_action": source_action,
        "observation_token": observation_token,
        "bundle_path": bundle_path,
        "active_window": active_window,
        "target_window": target_window,
        "windows": windows,
        "window_count": len(windows),
        "screen": normalized_screen,
        "screenshot": normalized_screenshot,
        "ui_evidence": normalized_ui,
        "window_backend": window_backend,
        "screenshot_backend": screenshot_backend,
        "ui_evidence_backend": ui_backend,
        "artifacts": {
            "bundle_path": bundle_path,
            "screenshot_path": _trim_text(normalized_screenshot.get("path", ""), limit=320),
        },
        "errors": [_trim_text(item, limit=220) for item in list(bundle.get("errors", []))[:6] if str(item).strip()],
        "metadata": {
            "partial": reason == "partial",
            "screen_monitor_count": int(normalized_screen.get("monitor_count", 0) or 0),
        },
    }


def bundle_ref(bundle: Dict[str, Any]) -> Dict[str, Any]:
    return normalize_desktop_evidence_ref(
        {
            "evidence_id": bundle.get("evidence_id", ""),
            "timestamp": bundle.get("timestamp", ""),
            "reason": bundle.get("reason", ""),
            "summary": bundle.get("summary", ""),
            "bundle_path": bundle.get("bundle_path", "") or bundle.get("artifacts", {}).get("bundle_path", ""),
            "screenshot_path": bundle.get("artifacts", {}).get("screenshot_path", ""),
            "observation_token": bundle.get("observation_token", ""),
            "active_window_title": bundle.get("active_window", {}).get("title", "") if isinstance(bundle.get("active_window", {}), dict) else "",
            "backend": " / ".join(
                value for value in [
                    _trim_text(bundle.get("window_backend", ""), limit=60),
                    _trim_text(bundle.get("screenshot_backend", ""), limit=60),
                    _trim_text(bundle.get("ui_evidence_backend", ""), limit=60),
                ] if value
            ),
        }
    )


def collect_display_metadata(virtual_screen: Dict[str, Any]) -> Dict[str, Any]:
    monitors: List[Dict[str, Any]] = []
    backend = "native"
    if mss is not None:
        try:
            with mss.mss() as capture:
                backend = "mss"
                for monitor in list(capture.monitors[1:])[:8]:
                    if not isinstance(monitor, dict):
                        continue
                    monitors.append(
                        {
                            "left": _coerce_int(monitor.get("left", 0), 0, minimum=-100_000, maximum=100_000),
                            "top": _coerce_int(monitor.get("top", 0), 0, minimum=-100_000, maximum=100_000),
                            "width": _coerce_int(monitor.get("width", 0), 0, minimum=0, maximum=100_000),
                            "height": _coerce_int(monitor.get("height", 0), 0, minimum=0, maximum=100_000),
                        }
                    )
        except Exception:
            monitors = []
            backend = "native"
    return normalize_screen_observation(
        virtual_screen=virtual_screen,
        monitors=monitors,
        backend=backend,
        reason="inspected",
    )


def build_desktop_evidence_bundle(
    *,
    source_action: str,
    active_window: Dict[str, Any],
    windows: Iterable[Dict[str, Any]],
    observation_token: str = "",
    screenshot: Dict[str, Any] | None = None,
    ui_evidence: Dict[str, Any] | None = None,
    target_window: Dict[str, Any] | None = None,
    screen: Dict[str, Any] | None = None,
    errors: Iterable[str] | None = None,
) -> Dict[str, Any]:
    normalized_screenshot = screenshot if isinstance(screenshot, dict) else {}
    normalized_ui = ui_evidence if isinstance(ui_evidence, dict) else {}
    error_items = [_trim_text(item, limit=220) for item in list(errors or [])[:6] if str(item).strip()]
    screenshot_path = str(normalized_screenshot.get("path", "")).strip()
    ui_controls = normalized_ui.get("controls", []) if isinstance(normalized_ui.get("controls", []), list) else []
    reason = "collected"
    if not screenshot_path or error_items or (normalized_ui and not ui_controls and str(normalized_ui.get("reason", "")).strip() not in {"", "inspected"}):
        reason = "partial"
    bundle = _sanitize_bundle(
        {
            "evidence_id": "",
            "timestamp": _iso_timestamp(),
            "reason": reason,
            "summary": "",
            "source_action": source_action,
            "observation_token": observation_token,
            "active_window": active_window,
            "target_window": target_window or {},
            "windows": list(windows)[:12],
            "screen": screen or {},
            "screenshot": normalized_screenshot,
            "ui_evidence": normalized_ui,
            "window_backend": str(active_window.get("backend", "")).strip(),
            "screenshot_backend": str(normalized_screenshot.get("backend", "")).strip(),
            "ui_evidence_backend": str(normalized_ui.get("backend", "")).strip(),
            "errors": error_items,
        }
    )
    return bundle


def evidence_collection_result(
    bundle: Dict[str, Any],
    *,
    ok: bool,
    message: str = "",
    error: str = "",
) -> Dict[str, Any]:
    normalized_bundle = _sanitize_bundle(bundle)
    return result_envelope(
        "desktop_evidence_bundle",
        ok=ok,
        backend="desktop_evidence",
        reason=str(normalized_bundle.get("reason", "partial")),
        message=message or normalized_bundle.get("summary", ""),
        error=error,
        data={"bundle": normalized_bundle, "reference": bundle_ref(normalized_bundle)},
    )


class DesktopEvidenceStore:
    def __init__(self, root: str | Path, *, max_items: int = DEFAULT_MAX_DESKTOP_EVIDENCE_ITEMS):
        self.root = Path(root)
        self.max_items = max(1, int(max_items))
        self.bundles_dir = self.root / "bundles"
        self.captures_dir = self.root / "captures"
        self.index_path = self.root / "index.json"
        self._lock = threading.RLock()

    def _read_index(self) -> Dict[str, Any]:
        if not self.index_path.exists():
            return {"bundles": []}
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return {"bundles": []}
        if not isinstance(payload, dict):
            return {"bundles": []}
        bundles = payload.get("bundles", [])
        return {"bundles": [normalize_desktop_evidence_ref(item) for item in bundles if isinstance(item, dict)]}

    def _write_index(self, refs: List[Dict[str, Any]]):
        payload = {
            "version": 1,
            "updated_at": _iso_timestamp(),
            "bundles": [normalize_desktop_evidence_ref(item) for item in refs],
        }
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def next_evidence_id(self) -> str:
        return f"desk-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"

    def artifact_path(self, evidence_id: str, *, extension: str = ".png") -> Path:
        suffix = str(extension or ".png").strip() or ".png"
        if not suffix.startswith("."):
            suffix = f".{suffix}"
        self.captures_dir.mkdir(parents=True, exist_ok=True)
        return self.captures_dir / f"{_trim_text(evidence_id, limit=80)}{suffix}"

    def bundle_path(self, evidence_id: str) -> Path:
        self.bundles_dir.mkdir(parents=True, exist_ok=True)
        return self.bundles_dir / f"{_trim_text(evidence_id, limit=80)}.json"

    def record_bundle(self, bundle: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            evidence_id = _trim_text(bundle.get("evidence_id", ""), limit=80) or self.next_evidence_id()
            bundle_copy = dict(bundle)
            bundle_copy["evidence_id"] = evidence_id
            bundle_copy["bundle_path"] = str(self.bundle_path(evidence_id))
            if isinstance(bundle_copy.get("artifacts", {}), dict):
                artifacts = dict(bundle_copy.get("artifacts", {}))
                artifacts["bundle_path"] = bundle_copy["bundle_path"]
                bundle_copy["artifacts"] = artifacts
            normalized = _sanitize_bundle(bundle_copy)
            bundle_file = self.bundle_path(evidence_id)
            self.root.mkdir(parents=True, exist_ok=True)
            bundle_file.write_text(json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8")

            refs = [item for item in self._read_index().get("bundles", []) if item.get("evidence_id") != evidence_id]
            refs.append(bundle_ref(normalized))
            refs = refs[-self.max_items :]
            self._write_index(refs)
            self._prune_locked(refs)
            return normalize_desktop_evidence_ref(refs[-1] if refs else {})

    def _prune_locked(self, refs: List[Dict[str, Any]]):
        keep_bundle_names = {Path(item.get("bundle_path", "")).name for item in refs if item.get("bundle_path")}
        keep_capture_names = {Path(item.get("screenshot_path", "")).name for item in refs if item.get("screenshot_path")}
        if self.bundles_dir.exists():
            for file in self.bundles_dir.iterdir():
                if not file.is_file():
                    continue
                if file.name not in keep_bundle_names:
                    try:
                        file.unlink()
                    except Exception:
                        pass
        if self.captures_dir.exists():
            for file in self.captures_dir.iterdir():
                if not file.is_file():
                    continue
                if file.name not in keep_capture_names:
                    try:
                        file.unlink()
                    except Exception:
                        pass

    def load_bundle(self, evidence_id: str) -> Dict[str, Any]:
        lookup = _trim_text(evidence_id, limit=80)
        if not lookup:
            return {}
        path = self.bundle_path(lookup)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        return _sanitize_bundle(payload)

    def recent_refs(self, limit: int = 8) -> List[Dict[str, Any]]:
        refs = self._read_index().get("bundles", [])
        return [normalize_desktop_evidence_ref(item) for item in refs[-max(1, int(limit or 1)) :]]

    def find_by_observation_token(self, token: str) -> Dict[str, Any]:
        lookup = _trim_text(token, limit=120)
        if not lookup:
            return {}
        for ref in reversed(self._read_index().get("bundles", [])):
            if ref.get("observation_token") == lookup:
                return normalize_desktop_evidence_ref(ref)
        return {}

    def status_snapshot(self) -> Dict[str, Any]:
        refs = self._read_index().get("bundles", [])
        return {
            "root": str(self.root),
            "bundle_count": len(refs),
            "max_items": self.max_items,
            "latest": normalize_desktop_evidence_ref(refs[-1] if refs else {}),
        }


def get_desktop_evidence_store(settings: Dict[str, Any] | None = None) -> DesktopEvidenceStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is not None:
            return _STORE
        source_settings = settings if isinstance(settings, dict) else load_settings()
        root = source_settings.get("desktop_evidence_root", DEFAULT_DESKTOP_EVIDENCE_ROOT)
        max_items = _coerce_int(
            source_settings.get("max_desktop_evidence_entries", DEFAULT_MAX_DESKTOP_EVIDENCE_ITEMS),
            DEFAULT_MAX_DESKTOP_EVIDENCE_ITEMS,
            minimum=4,
            maximum=256,
        )
        _STORE = DesktopEvidenceStore(root, max_items=max_items)
        return _STORE
