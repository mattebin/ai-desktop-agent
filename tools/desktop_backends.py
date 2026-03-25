from __future__ import annotations

import hashlib
import time
from itertools import islice
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List

from core.backend_schemas import (
    backend_status,
    normalize_desktop_process_context,
    normalize_desktop_visual_stability,
    normalize_desktop_window_readiness,
    normalize_screenshot_observation,
    normalize_ui_evidence_observation,
    normalize_window_descriptor,
    result_envelope,
)
from core.config import load_settings
from core.desktop_matching import WINDOW_MATCH_THRESHOLD, WINDOW_STRONG_MATCH_THRESHOLD, fuzz as rapidfuzz_fuzz
from core.desktop_matching import select_window_candidate, titles_compatible
from core.desktop_recovery import assess_visual_sample_signatures

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

try:
    import dxcam
except Exception:
    dxcam = None  # type: ignore[assignment]

try:
    import bettercam
except Exception:
    bettercam = None  # type: ignore[assignment]

try:
    import psutil
except Exception:
    psutil = None  # type: ignore[assignment]


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


def _bounded_descendants(window: Any, *, limit: int) -> List[Any]:
    bounded_limit = max(1, int(limit or 1))
    try:
        return list(islice(window.descendants(), bounded_limit))
    except Exception:
        return []


def _window_title_matches(title: str, requested_title: str, *, exact: bool) -> bool:
    return titles_compatible(requested_title, title, exact=exact)


def _available_capture_backends() -> List[str]:
    available: List[str] = []
    if dxcam is not None:
        available.append("dxcam")
    if bettercam is not None:
        available.append("bettercam")
    if mss is not None and mss_tools is not None:
        available.append("mss")
    available.append("native")
    return available


def _frame_to_rgb_bytes(frame: Any, *, backend_name: str) -> tuple[bytes, int, int]:
    shape = getattr(frame, "shape", None)
    if not isinstance(shape, tuple) or len(shape) < 2:
        raise RuntimeError(f"{backend_name} returned a frame without a usable shape.")
    height = int(shape[0] or 0)
    width = int(shape[1] or 0)
    channels = int(shape[2] or 0) if len(shape) > 2 else 0
    if width <= 0 or height <= 0:
        raise RuntimeError(f"{backend_name} returned a frame with invalid bounds.")
    raw = bytes(frame.tobytes()) if hasattr(frame, "tobytes") else b""
    if not raw:
        raise RuntimeError(f"{backend_name} returned an empty frame.")
    if channels == 3:
        return raw, width, height
    if channels == 4:
        rgb = bytearray(width * height * 3)
        rgb[0::3] = raw[0::4]
        rgb[1::3] = raw[1::4]
        rgb[2::3] = raw[2::4]
        return bytes(rgb), width, height
    raise RuntimeError(f"{backend_name} returned an unsupported channel count ({channels}).")


def _hex_hwnd(value: Any) -> str:
    try:
        return f"0x{int(value or 0):08X}"
    except Exception:
        return ""


def probe_process_context(*, pid: int = 0, process_name: str = "") -> Dict[str, Any]:
    normalized_name = _trim_text(process_name, limit=120)
    normalized_pid = _coerce_int(pid, 0, minimum=0, maximum=10_000_000)
    if psutil is None:
        context = normalize_desktop_process_context(
            {
                "pid": normalized_pid,
                "process_name": normalized_name,
                "present": False,
                "running": False,
                "background_candidate": False,
                "backend": "stub",
                "reason": "unsupported",
                "summary": "Process diagnostics are unavailable because psutil is not active.",
            }
        )
        return result_envelope(
            "desktop_process_context",
            ok=False,
            backend="stub",
            reason="unsupported",
            message=context.get("summary", ""),
            error="psutil is not available.",
            data=context,
        )

    process = None
    if normalized_pid > 0:
        try:
            process = psutil.Process(normalized_pid)
        except Exception:
            process = None
    elif normalized_name:
        try:
            lowered = normalized_name.lower()
            for item in psutil.process_iter(["pid", "name", "status", "ppid"]):
                try:
                    name = str(item.info.get("name", "") or "").strip()
                except Exception:
                    name = ""
                if name and name.lower() == lowered:
                    process = item
                    break
        except Exception:
            process = None

    if process is None:
        context = normalize_desktop_process_context(
            {
                "pid": normalized_pid,
                "process_name": normalized_name,
                "present": False,
                "running": False,
                "background_candidate": False,
                "backend": "psutil",
                "reason": "target_not_found",
                "summary": "No matching desktop process was available for bounded diagnostics.",
            }
        )
        return result_envelope(
            "desktop_process_context",
            ok=False,
            backend="psutil",
            reason="target_not_found",
            message=context.get("summary", ""),
            error="No matching desktop process was available.",
            data=context,
        )

    try:
        status = _trim_text(process.status(), limit=60)
    except Exception:
        status = ""
    try:
        name = _trim_text(process.name(), limit=120)
    except Exception:
        name = normalized_name
    try:
        exe = _trim_text(process.exe(), limit=320)
    except Exception:
        exe = ""
    try:
        running = bool(process.is_running()) and status.lower() != "zombie"
    except Exception:
        running = False
    try:
        parent = process.parent()
    except Exception:
        parent = None
    background_candidate = running and status.lower() in {"sleeping", "idle", "stopped"}
    context = normalize_desktop_process_context(
        {
            "pid": int(getattr(process, "pid", normalized_pid) or normalized_pid),
            "process_name": name or normalized_name,
            "status": status,
            "exe": exe,
            "parent_pid": int(getattr(parent, "pid", 0) or 0) if parent is not None else 0,
            "parent_name": _trim_text(parent.name(), limit=120) if parent is not None else "",
            "present": True,
            "running": running,
            "background_candidate": background_candidate,
            "backend": "psutil",
            "reason": "inspected",
            "summary": (
                f"Process '{name or normalized_name or 'unknown'}' is running with status '{status or 'unknown'}'."
                if running
                else f"Process '{name or normalized_name or 'unknown'}' is present but does not look runnable."
            ),
        }
    )
    return result_envelope(
        "desktop_process_context",
        ok=running,
        backend="psutil",
        reason="inspected",
        message=context.get("summary", ""),
        data=context,
    )


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
                "is_maximized": bool(getattr(window, "isMaximized", False)),
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
                active = pywinctl.getActiveWindow()
                active_handle = int(active.getHandle()) if active else 0
                windows = list(pywinctl.getAllWindows() or [])
                candidates = [self._serialize_window(window, active_handle=active_handle) for window in windows]
                selected = select_window_candidate(candidates, requested_title=title, exact=exact)
                selected_id = str(selected.get("selected", {}).get("window_id", "")).strip().lower()
                if selected_id:
                    for window in windows:
                        handle = 0
                        try:
                            handle = int(window.getHandle())
                        except Exception:
                            handle = 0
                        candidate = f"0x{handle:08X}".lower() if handle else ""
                        if candidate == selected_id:
                            target = window
                            break

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
            metadata={"supports_restore": True, "supports_activate": True},
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
            reason="capture_backend_fallback" if preferred_backend != self.name else "capture_backend_selected",
            message="Using the native screenshot backend.",
            metadata={"extension": self.file_extension, "available_backends": _available_capture_backends()},
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
            reason="capture_backend_selected" if preferred_backend in {"auto", self.name} else "capture_backend_fallback",
            message="Using mss for desktop screenshot capture.",
            metadata={"extension": self.file_extension, "available_backends": _available_capture_backends()},
        )


class DesktopDuplicationScreenshotBackend(NativeScreenshotBackend):
    file_extension = ".png"

    def __init__(self, *, backend_name: str, module: Any, fallback_backend: NativeScreenshotBackend):
        self.name = backend_name
        self._module = module
        self._fallback_backend = fallback_backend
        self._camera = None

    def _camera_instance(self):
        if self._camera is not None:
            return self._camera
        create = getattr(self._module, "create", None)
        if not callable(create):
            raise RuntimeError(f"{self.name} does not expose create().")
        try:
            self._camera = create(output_color="RGB")
        except TypeError:
            self._camera = create()
        return self._camera

    def capture(self, path: Path, *, x: int, y: int, width: int, height: int, scope: str, active_window_title: str = "") -> Dict[str, Any]:
        try:
            if mss_tools is None:
                raise RuntimeError("mss_tools is required to serialize capture-plugin frames to PNG.")
            region = (int(x), int(y), int(x) + int(width), int(y) + int(height))
            camera = self._camera_instance()
            frame = camera.grab(region=region)
            if frame is None:
                raise RuntimeError(f"{self.name} did not return a fresh frame for the requested region.")
            rgb_bytes, frame_width, frame_height = _frame_to_rgb_bytes(frame, backend_name=self.name)
            path.parent.mkdir(parents=True, exist_ok=True)
            mss_tools.to_png(rgb_bytes, (frame_width, frame_height), output=str(path))
            observation = normalize_screenshot_observation(
                backend=self.name,
                path=str(path),
                scope=scope,
                bounds={"x": x, "y": y, "width": frame_width, "height": frame_height},
                active_window_title=active_window_title,
                reason="captured",
                metadata={"format": "png", "plugin": self.name},
            )
            return result_envelope(
                "screenshot_observation",
                ok=True,
                backend=self.name,
                reason="captured",
                message=f"Captured the screenshot using {self.name}.",
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
            reason="capture_backend_selected" if preferred_backend in {"auto", self.name} else "capture_backend_fallback",
            message=f"Using {self.name} as the desktop duplication screenshot backend.",
            metadata={"extension": self.file_extension, "available_backends": _available_capture_backends(), "plugin": self.name},
        )

    def shutdown(self):
        camera = self._camera
        self._camera = None
        if camera is None:
            return
        stop = getattr(camera, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                return


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
                for child in _bounded_descendants(target_window, limit=max(1, int(limit or 8))):
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


def _pywinauto_target_window(*, target: str = "active_window", window_id: str = "") -> Any:
    if PyWinAutoDesktop is None:
        return None

    desktop = PyWinAutoDesktop(backend="uia")
    windows = list(desktop.windows() or [])
    normalized_window_id = str(window_id or "").strip().lower()
    if normalized_window_id:
        for window in windows:
            try:
                handle = getattr(window.element_info, "handle", 0)
            except Exception:
                handle = 0
            if _hex_hwnd(handle).lower() == normalized_window_id:
                return window

    if target == "active_window":
        for window in windows:
            try:
                if window.has_keyboard_focus():
                    return window
            except Exception:
                continue
        return windows[0] if windows else None

    normalized_target = str(target or "").strip()
    if not normalized_target:
        return windows[0] if windows else None

    candidates: List[Dict[str, Any]] = []
    window_index: Dict[str, Any] = {}
    for window in windows:
        title = ""
        try:
            title = str(window.window_text() or "").strip()
        except Exception:
            title = ""
        handle = 0
        try:
            handle = getattr(window.element_info, "handle", 0)
        except Exception:
            handle = 0
        candidate = {
            "window_id": _hex_hwnd(handle),
            "title": title,
            "is_active": False,
            "is_visible": True,
            "is_minimized": False,
            "is_cloaked": False,
        }
        candidates.append(candidate)
        if candidate["window_id"]:
            window_index[candidate["window_id"].lower()] = window
    selected = select_window_candidate(candidates, requested_title=normalized_target, exact=False)
    selected_id = str(selected.get("selected", {}).get("window_id", "")).strip().lower()
    if selected_id and selected_id in window_index:
        return window_index[selected_id]
    return None


def probe_window_readiness(*, target: str = "active_window", window_id: str = "", limit: int = 8) -> Dict[str, Any]:
    if PyWinAutoDesktop is None:
        readiness = normalize_desktop_window_readiness(
            {
                "state": "unsupported",
                "ready": False,
                "target": target,
                "target_window_id": window_id,
                "backend": "stub",
                "reason": "unsupported",
                "summary": "Read-only readiness probing is not available because pywinauto is not active.",
            }
        )
        return result_envelope(
            "desktop_window_readiness",
            ok=False,
            backend="stub",
            reason="unsupported",
            message=readiness.get("summary", ""),
            error="pywinauto is not available.",
            data=readiness,
        )

    try:
        target_window = _pywinauto_target_window(target=target, window_id=window_id)
        if target_window is None:
            readiness = normalize_desktop_window_readiness(
                {
                    "state": "missing",
                    "ready": False,
                    "target": target,
                    "target_window_id": window_id,
                    "backend": "pywinauto",
                    "reason": "target_not_found",
                    "summary": "The requested window was not available for read-only readiness probing.",
                }
            )
            return result_envelope(
                "desktop_window_readiness",
                ok=False,
                backend="pywinauto",
                reason="target_not_found",
                message=readiness.get("summary", ""),
                error="Requested window not found for readiness probing.",
                data=readiness,
            )

        try:
            target_title = _trim_text(target_window.window_text(), limit=180)
        except Exception:
            target_title = _trim_text(target, limit=180)
        try:
            handle = getattr(target_window.element_info, "handle", 0)
        except Exception:
            handle = 0
        try:
            visible = bool(target_window.is_visible())
        except Exception:
            visible = False
        try:
            enabled = bool(target_window.is_enabled())
        except Exception:
            enabled = False
        try:
            focused = bool(target_window.has_keyboard_focus())
        except Exception:
            focused = False

        control_count = 0
        try:
            control_count = len(_bounded_descendants(target_window, limit=max(1, int(limit or 8))))
        except Exception:
            control_count = 0

        if not visible:
            state = "not_ready"
            reason = "target_hidden"
            summary = f"'{target_title or 'The target window'}' is present but not visible to pywinauto yet."
        elif not enabled:
            state = "not_ready"
            reason = "target_not_ready"
            summary = f"'{target_title or 'The target window'}' is visible but not interactable yet."
        elif control_count <= 0:
            state = "loading"
            reason = "target_loading"
            summary = f"'{target_title or 'The target window'}' is visible, but its control tree still looks empty."
        else:
            state = "ready"
            reason = "ready"
            summary = f"'{target_title or 'The target window'}' looks visible and ready for bounded desktop work."

        readiness = normalize_desktop_window_readiness(
            {
                "state": state,
                "ready": state == "ready",
                "loading": state == "loading",
                "visible": visible,
                "enabled": enabled,
                "focused": focused,
                "interactable": visible and enabled,
                "target": target,
                "target_window_id": _hex_hwnd(handle),
                "window_title": target_title,
                "control_count": control_count,
                "backend": "pywinauto",
                "reason": reason,
                "summary": summary,
            }
        )
        return result_envelope(
            "desktop_window_readiness",
            ok=state == "ready",
            backend="pywinauto",
            reason=reason,
            message=summary,
            data=readiness,
        )
    except Exception as exc:
        readiness = normalize_desktop_window_readiness(
            {
                "state": "missing",
                "ready": False,
                "target": target,
                "target_window_id": window_id,
                "backend": "pywinauto",
                "reason": "error",
                "summary": "Could not collect read-only readiness evidence.",
            }
        )
        return result_envelope(
            "desktop_window_readiness",
            ok=False,
            backend="pywinauto",
            reason="error",
            message="Could not collect read-only readiness evidence.",
            error=_trim_text(exc, limit=240),
            data=readiness,
        )


def probe_visual_stability(*, x: int, y: int, width: int, height: int, samples: int = 3, interval_ms: int = 120) -> Dict[str, Any]:
    if mss is None:
        stability = normalize_desktop_visual_stability(
            {
                "state": "unsupported",
                "stable": False,
                "sample_count": 0,
                "distinct_sample_count": 0,
                "changed": False,
                "backend": "stub",
                "reason": "unsupported",
                "summary": "Visual stability checks are not available because mss is not active.",
            }
        )
        return result_envelope(
            "desktop_visual_stability",
            ok=False,
            backend="stub",
            reason="unsupported",
            message=stability.get("summary", ""),
            error="mss is not available.",
            data=stability,
        )

    if int(width or 0) <= 0 or int(height or 0) <= 0:
        stability = normalize_desktop_visual_stability(
            {
                "state": "missing",
                "stable": False,
                "sample_count": 0,
                "distinct_sample_count": 0,
                "changed": False,
                "backend": "mss",
                "reason": "invalid_input",
                "summary": "Visual stability checks need positive bounds.",
            }
        )
        return result_envelope(
            "desktop_visual_stability",
            ok=False,
            backend="mss",
            reason="invalid_input",
            message=stability.get("summary", ""),
            error="Visual stability checks need positive bounds.",
            data=stability,
        )

    signatures: List[str] = []
    sample_total = max(2, min(4, int(samples or 3)))
    interval_seconds = max(0.03, min(0.4, int(interval_ms or 120) / 1000.0))
    try:
        with mss.mss() as capture:
            for index in range(sample_total):
                shot = capture.grab({"left": int(x), "top": int(y), "width": int(width), "height": int(height)})
                signatures.append(hashlib.sha1(bytes(shot.rgb)).hexdigest()[:20])
                if index < sample_total - 1:
                    time.sleep(interval_seconds)
        stability = assess_visual_sample_signatures(signatures, backend="mss")
        return result_envelope(
            "desktop_visual_stability",
            ok=bool(stability.get("stable", False)),
            backend="mss",
            reason=stability.get("reason", "inspected"),
            message=stability.get("summary", ""),
            data=stability,
        )
    except Exception as exc:
        stability = normalize_desktop_visual_stability(
            {
                "state": "missing",
                "stable": False,
                "sample_count": len(signatures),
                "distinct_sample_count": len(set(signatures)),
                "changed": False,
                "backend": "mss",
                "reason": "error",
                "summary": "Could not collect bounded visual stability samples.",
            }
        )
        return result_envelope(
            "desktop_visual_stability",
            ok=False,
            backend="mss",
            reason="error",
            message="Could not collect bounded visual stability samples.",
            error=_trim_text(exc, limit=240),
            data=stability,
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
    preferred = preferences["desktop_screenshot_backend"]
    ordered_preferences = {
        "auto": ["dxcam", "bettercam", "mss", "native"],
        "dxcam": ["dxcam", "bettercam", "mss", "native"],
        "bettercam": ["bettercam", "dxcam", "mss", "native"],
        "mss": ["mss", "native"],
        "native": ["native"],
    }.get(preferred, ["mss", "native"])
    for name in ordered_preferences:
        if name == "dxcam" and dxcam is not None:
            return DesktopDuplicationScreenshotBackend(backend_name="dxcam", module=dxcam, fallback_backend=fallback)
        if name == "bettercam" and bettercam is not None:
            return DesktopDuplicationScreenshotBackend(backend_name="bettercam", module=bettercam, fallback_backend=fallback)
        if name == "mss" and mss is not None and mss_tools is not None:
            return MssScreenshotBackend(fallback_backend=fallback)
        if name == "native":
            return fallback
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
        "recovery": {
            "window_strategies": ["restore_then_focus", "show_then_focus", "focus_then_verify", "wait_for_readiness"],
            "readiness_backend": "pywinauto" if PyWinAutoDesktop is not None else "stub",
            "visual_stability_backend": "mss" if mss is not None else "stub",
            "process_backend": "psutil" if psutil is not None else "stub",
        },
        "matching": {
            "title_matching": "rapidfuzz" if rapidfuzz_fuzz is not None else "builtin",
            "window_match_threshold": WINDOW_MATCH_THRESHOLD,
            "window_strong_match_threshold": WINDOW_STRONG_MATCH_THRESHOLD,
        },
    }
