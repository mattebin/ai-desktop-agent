from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List

from core.backend_schemas import (
    backend_status,
    normalize_screenshot_observation,
    normalize_ui_evidence_observation,
    normalize_window_descriptor,
    result_envelope,
)
from core.config import load_settings

try:
    import pywinctl
except Exception:
    pywinctl = None  # type: ignore[assignment]

try:
    import mss
    from mss import tools as mss_tools
except Exception:
    mss = None  # type: ignore[assignment]
    mss_tools = None  # type: ignore[assignment]

try:
    from pywinauto import Desktop as PyWinAutoDesktop
except Exception:
    PyWinAutoDesktop = None  # type: ignore[assignment]


WindowListDelegate = Callable[..., List[Dict[str, Any]]]
WindowInfoDelegate = Callable[[], Dict[str, Any]]
FocusDelegate = Callable[[int], tuple[bool, str]]
CaptureDelegate = Callable[[Path], tuple[bool, str]]


def _trim_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


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


def _normalize_rect(x: Any, y: Any, width: Any, height: Any) -> Dict[str, int]:
    return {
        "x": _coerce_int(x, 0, minimum=-100_000, maximum=100_000),
        "y": _coerce_int(y, 0, minimum=-100_000, maximum=100_000),
        "width": _coerce_int(width, 0, minimum=0, maximum=100_000),
        "height": _coerce_int(height, 0, minimum=0, maximum=100_000),
    }


def _rect_from_window(window: Any) -> Dict[str, int]:
    return _normalize_rect(
        getattr(window, "left", 0),
        getattr(window, "top", 0),
        getattr(window, "width", 0),
        getattr(window, "height", 0),
    )


def _normalize_path_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return str(Path(text).resolve())
    except Exception:
        return text


def _window_title_matches(title: str, requested_title: str, *, exact: bool) -> bool:
    normalized_title = str(title or "").strip().lower()
    requested = str(requested_title or "").strip().lower()
    if not normalized_title or not requested:
        return False
    if exact:
        return normalized_title == requested
    return requested in normalized_title


class NativeWindowBackend:
    name = "native"

    def __init__(self, *, list_delegate: WindowListDelegate, active_delegate: WindowInfoDelegate, focus_delegate: FocusDelegate):
        self._list_delegate = list_delegate
        self._active_delegate = active_delegate
        self._focus_delegate = focus_delegate

    def list_windows(self, *, include_minimized: bool = False, limit: int = 12) -> Dict[str, Any]:
        windows = [normalize_window_descriptor(item, backend=self.name) for item in self._list_delegate(include_minimized=include_minimized, limit=limit)]
        active_window = normalize_window_descriptor(self._active_delegate() or {}, backend=self.name)
        return result_envelope(
            "desktop_window_observation",
            ok=True,
            backend=self.name,
            reason="inspected",
            message="Enumerated desktop windows using the native backend.",
            data={"windows": windows, "active_window": active_window, "window_count": len(windows)},
        )

    def get_active_window(self) -> Dict[str, Any]:
        active_window = normalize_window_descriptor(self._active_delegate() or {}, backend=self.name)
        return result_envelope(
            "desktop_window_observation",
            ok=bool(active_window.get("window_id")),
            backend=self.name,
            reason="inspected" if active_window.get("window_id") else "not_found",
            message="Resolved the active desktop window." if active_window.get("window_id") else "Could not resolve the active desktop window.",
            error="" if active_window.get("window_id") else "No active desktop window was available.",
            data={"active_window": active_window},
        )

    def focus_window(self, *, window_id: str) -> Dict[str, Any]:
        try:
            handle = int(str(window_id).strip(), 16) if str(window_id).strip().lower().startswith("0x") else int(str(window_id).strip())
        except Exception:
            handle = 0
        ok, error = self._focus_delegate(handle)
        active_window = normalize_window_descriptor(self._active_delegate() or {}, backend=self.name, reason="focused" if ok else "error")
        return result_envelope(
            "desktop_window_observation",
            ok=ok,
            backend=self.name,
            reason="focused" if ok else "error",
            message="Focused the requested window." if ok else "Could not focus the requested window.",
            error=error,
            data={"active_window": active_window},
        )

    def status_snapshot(self, preferred_backend: str) -> Dict[str, Any]:
        return backend_status(
            "desktop_window",
            preferred=preferred_backend,
            active=self.name,
            available=True,
            reason="fallback_active" if preferred_backend != self.name else "active",
            message="Using the native desktop window backend.",
            metadata={},
        )

    def shutdown(self):
        return


class PyWinCtlWindowBackend(NativeWindowBackend):
    name = "pywinctl"

    def __init__(self, *, fallback_backend: NativeWindowBackend):
        self._fallback_backend = fallback_backend

    def _serialize_window(self, window: Any, *, active_handle: int) -> Dict[str, Any]:
        handle = 0
        try:
            handle_value = window.getHandle() if callable(getattr(window, "getHandle", None)) else getattr(window, "getHandle", 0)
            handle = int(handle_value or 0)
        except Exception:
            handle = 0
        title = _trim_text(getattr(window, "title", ""), limit=180)
        process_name = ""
        try:
            process_name = _trim_text(getattr(window, "getAppName", lambda: "")() or "", limit=120)
        except Exception:
            process_name = ""
        return normalize_window_descriptor(
            {
                "window_id": f"0x{handle:08X}" if handle else "",
                "title": title,
                "class_name": "",
                "pid": 0,
                "process_name": process_name,
                "rect": _rect_from_window(window),
                "is_active": handle == active_handle and handle > 0,
                "is_visible": bool(getattr(window, "isVisible", True)),
                "is_minimized": bool(getattr(window, "isMinimized", False)),
            },
            backend=self.name,
        )

    def list_windows(self, *, include_minimized: bool = False, limit: int = 12) -> Dict[str, Any]:
        try:
            active = pywinctl.getActiveWindow()
            active_handle = int(active.getHandle()) if active else 0
            windows: List[Dict[str, Any]] = []
            for window in list(pywinctl.getAllWindows() or []):
                title = str(getattr(window, "title", "") or "").strip()
                if not title:
                    continue
                if not bool(getattr(window, "isVisible", True)):
                    continue
                if not include_minimized and bool(getattr(window, "isMinimized", False)):
                    continue
                windows.append(self._serialize_window(window, active_handle=active_handle))
                if len(windows) >= limit:
                    break
            active_window = self._serialize_window(active, active_handle=active_handle) if active else {}
            return result_envelope(
                "desktop_window_observation",
                ok=True,
                backend=self.name,
                reason="inspected",
                message="Enumerated desktop windows using PyWinCtl.",
                data={"windows": windows, "active_window": active_window, "window_count": len(windows)},
            )
        except Exception as exc:
            fallback = self._fallback_backend.list_windows(include_minimized=include_minimized, limit=limit)
            fallback["metadata"] = dict(fallback.get("metadata", {}))
            fallback["metadata"]["fallback_from"] = self.name
            fallback["metadata"]["fallback_error"] = _trim_text(exc, limit=180)
            return fallback

    def get_active_window(self) -> Dict[str, Any]:
        try:
            active = pywinctl.getActiveWindow()
            active_handle = int(active.getHandle()) if active else 0
            active_window = self._serialize_window(active, active_handle=active_handle) if active else {}
            return result_envelope(
                "desktop_window_observation",
                ok=bool(active_window.get("window_id")),
                backend=self.name,
                reason="inspected" if active_window.get("window_id") else "not_found",
                message="Resolved the active desktop window using PyWinCtl." if active_window.get("window_id") else "Could not resolve the active desktop window.",
                error="" if active_window.get("window_id") else "No active desktop window was available.",
                data={"active_window": active_window},
            )
        except Exception as exc:
            fallback = self._fallback_backend.get_active_window()
            fallback["metadata"] = dict(fallback.get("metadata", {}))
            fallback["metadata"]["fallback_from"] = self.name
            fallback["metadata"]["fallback_error"] = _trim_text(exc, limit=180)
            return fallback

    def focus_window(self, *, window_id: str = "", title: str = "", exact: bool = False) -> Dict[str, Any]:
        try:
            target = None
            normalized_id = str(window_id or "").strip().lower()
            if normalized_id:
                for window in list(pywinctl.getAllWindows() or []):
                    handle = 0
                    try:
                        handle = int(window.getHandle())
                    except Exception:
                        handle = 0
                    candidate = f"0x{handle:08X}".lower() if handle else ""
                    if candidate == normalized_id:
                        target = window
                        break
            elif title:
                windows = list(pywinctl.getWindowsWithTitle(title) or [])
                if exact:
                    windows = [window for window in windows if _window_title_matches(getattr(window, "title", ""), title, exact=True)]
                target = windows[0] if windows else None

            if target is None:
                return result_envelope(
                    "desktop_window_observation",
                    ok=False,
                    backend=self.name,
                    reason="not_found",
                    message="Could not find the requested window.",
                    error="Requested window not found.",
                    data={"active_window": {}},
                )

            if bool(getattr(target, "isMinimized", False)) and callable(getattr(target, "restore", None)):
                target.restore()
            if callable(getattr(target, "activate", None)):
                target.activate()
            active = pywinctl.getActiveWindow()
            active_handle = int(active.getHandle()) if active else 0
            active_window = self._serialize_window(active, active_handle=active_handle) if active else {}
            return result_envelope(
                "desktop_window_observation",
                ok=bool(active_window.get("window_id")),
                backend=self.name,
                reason="focused" if active_window.get("window_id") else "error",
                message="Focused the requested window." if active_window.get("window_id") else "Could not focus the requested window.",
                error="" if active_window.get("window_id") else "PyWinCtl did not activate the requested window.",
                data={"active_window": active_window},
            )
        except Exception as exc:
            fallback = self._fallback_backend.focus_window(window_id=window_id)
            fallback["metadata"] = dict(fallback.get("metadata", {}))
            fallback["metadata"]["fallback_from"] = self.name
            fallback["metadata"]["fallback_error"] = _trim_text(exc, limit=180)
            return fallback

    def status_snapshot(self, preferred_backend: str) -> Dict[str, Any]:
        return backend_status(
            "desktop_window",
            preferred=preferred_backend,
            active=self.name,
            available=True,
            reason="active",
            message="Using PyWinCtl for desktop window enumeration and focus metadata.",
            metadata={},
        )


class NativeScreenshotBackend:
    name = "native"
    file_extension = ".bmp"

    def __init__(self, *, capture_delegate: Callable[[Path, int, int, int, int], tuple[bool, str]]):
        self._capture_delegate = capture_delegate

    def capture(self, path: Path, *, x: int, y: int, width: int, height: int, scope: str, active_window_title: str = "") -> Dict[str, Any]:
        ok, error = self._capture_delegate(path, x=x, y=y, width=width, height=height)
        observation = normalize_screenshot_observation(
            backend=self.name,
            path=str(path) if ok else "",
            scope=scope,
            bounds={"x": x, "y": y, "width": width, "height": height},
            active_window_title=active_window_title,
            reason="captured" if ok else "error",
        )
        return result_envelope(
            "screenshot_observation",
            ok=ok,
            backend=self.name,
            reason="captured" if ok else "error",
            message="Captured the screenshot using the native backend." if ok else "Could not capture the screenshot.",
            error=error,
            data=observation,
        )

    def status_snapshot(self, preferred_backend: str) -> Dict[str, Any]:
        return backend_status(
            "screenshot",
            preferred=preferred_backend,
            active=self.name,
            available=True,
            reason="fallback_active" if preferred_backend != self.name else "active",
            message="Using the native screenshot backend.",
            metadata={"extension": self.file_extension},
        )

    def shutdown(self):
        return


class MssScreenshotBackend(NativeScreenshotBackend):
    name = "mss"
    file_extension = ".png"

    def __init__(self, *, fallback_backend: NativeScreenshotBackend):
        self._fallback_backend = fallback_backend

    def capture(self, path: Path, *, x: int, y: int, width: int, height: int, scope: str, active_window_title: str = "") -> Dict[str, Any]:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with mss.mss() as capture:
                shot = capture.grab({"left": x, "top": y, "width": width, "height": height})
                mss_tools.to_png(shot.rgb, shot.size, output=str(path))
            observation = normalize_screenshot_observation(
                backend=self.name,
                path=str(path),
                scope=scope,
                bounds={"x": x, "y": y, "width": width, "height": height},
                active_window_title=active_window_title,
                reason="captured",
                metadata={"format": "png"},
            )
            return result_envelope(
                "screenshot_observation",
                ok=True,
                backend=self.name,
                reason="captured",
                message="Captured the screenshot using mss.",
                data=observation,
            )
        except Exception as exc:
            fallback = self._fallback_backend.capture(
                path.with_suffix(self._fallback_backend.file_extension),
                x=x,
                y=y,
                width=width,
                height=height,
                scope=scope,
                active_window_title=active_window_title,
            )
            fallback["metadata"] = dict(fallback.get("metadata", {}))
            fallback["metadata"]["fallback_from"] = self.name
            fallback["metadata"]["fallback_error"] = _trim_text(exc, limit=180)
            return fallback

    def status_snapshot(self, preferred_backend: str) -> Dict[str, Any]:
        return backend_status(
            "screenshot",
            preferred=preferred_backend,
            active=self.name,
            available=True,
            reason="active",
            message="Using mss for desktop screenshot capture.",
            metadata={"extension": self.file_extension},
        )


class StubUiEvidenceBackend:
    name = "stub"

    def probe(self, *, target: str = "active_window", limit: int = 8) -> Dict[str, Any]:
        observation = normalize_ui_evidence_observation(
            backend=self.name,
            target=target,
            controls=[],
            reason="unsupported",
            metadata={"limit": limit},
        )
        return result_envelope(
            "ui_evidence_observation",
            ok=False,
            backend=self.name,
            reason="unsupported",
            message="Read-only UI evidence probing is not active.",
            error="No read-only UI evidence backend is active.",
            data=observation,
        )

    def status_snapshot(self, preferred_backend: str) -> Dict[str, Any]:
        return backend_status(
            "ui_evidence",
            preferred=preferred_backend,
            active=self.name,
            available=False,
            reason="unsupported",
            message="UI evidence probing is stubbed for future read-only use.",
            metadata={},
        )

    def shutdown(self):
        return


class PyWinAutoEvidenceBackend(StubUiEvidenceBackend):
    name = "pywinauto"

    def probe(self, *, target: str = "active_window", limit: int = 8) -> Dict[str, Any]:
        try:
            desktop = PyWinAutoDesktop(backend="uia")
            windows = desktop.windows()
            target_window = windows[0] if windows else None
            if target == "active_window":
                for window in windows:
                    try:
                        if window.has_keyboard_focus():
                            target_window = window
                            break
                    except Exception:
                        continue
            if target_window is None:
                observation = normalize_ui_evidence_observation(backend=self.name, target=target, controls=[], reason="not_found")
                return result_envelope(
                    "ui_evidence_observation",
                    ok=False,
                    backend=self.name,
                    reason="not_found",
                    message="No target window was available for read-only UI evidence.",
                    error="No target window was available for read-only UI evidence.",
                    data=observation,
                )

            descendants: List[Dict[str, Any]] = []
            try:
                for child in list(target_window.descendants())[:max(1, int(limit or 8))]:
                    descendants.append(
                        {
                            "name": _trim_text(getattr(child.element_info, "name", ""), limit=160),
                            "control_type": _trim_text(getattr(child.element_info, "control_type", ""), limit=80),
                            "automation_id": _trim_text(getattr(child.element_info, "automation_id", ""), limit=120),
                            "text": _trim_text(getattr(child, "window_text", lambda: "")() or "", limit=220),
                        }
                    )
            except Exception:
                descendants = []

            target_name = ""
            try:
                target_name = _trim_text(target_window.window_text(), limit=180)
            except Exception:
                target_name = _trim_text(target, limit=180)
            observation = normalize_ui_evidence_observation(
                backend=self.name,
                target=target_name or target,
                controls=descendants,
                reason="inspected",
                metadata={"limit": max(1, int(limit or 8))},
            )
            return result_envelope(
                "ui_evidence_observation",
                ok=True,
                backend=self.name,
                reason="inspected",
                message="Collected read-only UI evidence using pywinauto.",
                data=observation,
            )
        except Exception as exc:
            observation = normalize_ui_evidence_observation(backend=self.name, target=target, controls=[], reason="error")
            return result_envelope(
                "ui_evidence_observation",
                ok=False,
                backend=self.name,
                reason="error",
                message="Could not collect read-only UI evidence.",
                error=_trim_text(exc, limit=240),
                data=observation,
            )

    def status_snapshot(self, preferred_backend: str) -> Dict[str, Any]:
        return backend_status(
            "ui_evidence",
            preferred=preferred_backend,
            active=self.name,
            available=True,
            reason="active",
            message="Using pywinauto as a future-facing read-only UI evidence backend.",
            metadata={},
        )


def _load_backend_preferences() -> Dict[str, str]:
    settings = load_settings()
    return {
        "desktop_window_backend": _trim_text(settings.get("desktop_window_backend", "pywinctl"), limit=40).lower() or "pywinctl",
        "desktop_screenshot_backend": _trim_text(settings.get("desktop_screenshot_backend", "mss"), limit=40).lower() or "mss",
        "ui_evidence_backend": _trim_text(settings.get("ui_evidence_backend", "pywinauto"), limit=40).lower() or "pywinauto",
    }


def create_window_backend(
    *,
    list_delegate: WindowListDelegate,
    active_delegate: WindowInfoDelegate,
    focus_delegate: FocusDelegate,
) -> NativeWindowBackend:
    preferences = _load_backend_preferences()
    fallback = NativeWindowBackend(
        list_delegate=list_delegate,
        active_delegate=active_delegate,
        focus_delegate=focus_delegate,
    )
    if preferences["desktop_window_backend"] == "pywinctl" and pywinctl is not None:
        return PyWinCtlWindowBackend(fallback_backend=fallback)
    return fallback


def create_screenshot_backend(
    *,
    capture_delegate: Callable[[Path, int, int, int, int], tuple[bool, str]],
) -> NativeScreenshotBackend:
    preferences = _load_backend_preferences()
    fallback = NativeScreenshotBackend(capture_delegate=capture_delegate)
    if preferences["desktop_screenshot_backend"] == "mss" and mss is not None and mss_tools is not None:
        return MssScreenshotBackend(fallback_backend=fallback)
    return fallback


def create_ui_evidence_backend() -> StubUiEvidenceBackend:
    preferences = _load_backend_preferences()
    if preferences["ui_evidence_backend"] == "pywinauto" and PyWinAutoDesktop is not None:
        return PyWinAutoEvidenceBackend()
    return StubUiEvidenceBackend()


def describe_backends(
    *,
    window_backend: NativeWindowBackend,
    screenshot_backend: NativeScreenshotBackend,
    ui_evidence_backend: StubUiEvidenceBackend,
) -> Dict[str, Any]:
    preferences = _load_backend_preferences()
    return {
        "window": window_backend.status_snapshot(preferences["desktop_window_backend"]),
        "screenshot": screenshot_backend.status_snapshot(preferences["desktop_screenshot_backend"]),
        "ui_evidence": ui_evidence_backend.status_snapshot(preferences["ui_evidence_backend"]),
    }
