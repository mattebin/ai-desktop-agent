from __future__ import annotations

from typing import Any, Dict, Tuple


def _trim_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _coerce_int(value: Any, default: int, *, minimum: int = -100_000, maximum: int = 100_000) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _coerce_float(value: Any, default: float, *, minimum: float = 0.1, maximum: float = 8.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _rect(value: Dict[str, Any] | None) -> Dict[str, int]:
    rect = value if isinstance(value, dict) else {}
    return {
        "x": _coerce_int(rect.get("x", rect.get("left", 0)), 0),
        "y": _coerce_int(rect.get("y", rect.get("top", 0)), 0),
        "width": _coerce_int(rect.get("width", 0), 0, minimum=0, maximum=100_000),
        "height": _coerce_int(rect.get("height", 0), 0, minimum=0, maximum=100_000),
    }


def rect_contains_point(rect: Dict[str, Any] | None, x: int, y: int) -> bool:
    normalized = _rect(rect)
    return (
        x >= int(normalized.get("x", 0))
        and y >= int(normalized.get("y", 0))
        and x < int(normalized.get("x", 0)) + int(normalized.get("width", 0))
        and y < int(normalized.get("y", 0)) + int(normalized.get("height", 0))
    )


def rect_intersection(a: Dict[str, Any] | None, b: Dict[str, Any] | None) -> Dict[str, int]:
    first = _rect(a)
    second = _rect(b)
    left = max(int(first.get("x", 0)), int(second.get("x", 0)))
    top = max(int(first.get("y", 0)), int(second.get("y", 0)))
    right = min(int(first.get("x", 0)) + int(first.get("width", 0)), int(second.get("x", 0)) + int(second.get("width", 0)))
    bottom = min(int(first.get("y", 0)) + int(first.get("height", 0)), int(second.get("y", 0)) + int(second.get("height", 0)))
    return {"x": left, "y": top, "width": max(0, right - left), "height": max(0, bottom - top)}


def rect_area(rect: Dict[str, Any] | None) -> int:
    normalized = _rect(rect)
    return max(0, int(normalized.get("width", 0))) * max(0, int(normalized.get("height", 0)))


def monitor_rect(monitor: Dict[str, Any] | None) -> Dict[str, int]:
    data = monitor if isinstance(monitor, dict) else {}
    return {
        "x": _coerce_int(data.get("left", data.get("x", 0)), 0),
        "y": _coerce_int(data.get("top", data.get("y", 0)), 0),
        "width": _coerce_int(data.get("width", 0), 0, minimum=0, maximum=100_000),
        "height": _coerce_int(data.get("height", 0), 0, minimum=0, maximum=100_000),
    }


def primary_monitor(display: Dict[str, Any] | None) -> Dict[str, Any]:
    metadata = display if isinstance(display, dict) else {}
    primary = metadata.get("primary_monitor", {}) if isinstance(metadata.get("primary_monitor", {}), dict) else {}
    if primary:
        return dict(primary)
    monitors = metadata.get("monitors", []) if isinstance(metadata.get("monitors", []), list) else []
    for item in monitors:
        if isinstance(item, dict) and bool(item.get("is_primary", False)):
            return dict(item)
    return dict(monitors[0]) if monitors and isinstance(monitors[0], dict) else {}


def monitor_for_rect(display: Dict[str, Any] | None, rect: Dict[str, Any] | None) -> Dict[str, Any]:
    metadata = display if isinstance(display, dict) else {}
    monitors = metadata.get("monitors", []) if isinstance(metadata.get("monitors", []), list) else []
    target_rect = _rect(rect)
    best: Dict[str, Any] = {}
    best_area = -1
    for monitor in monitors:
        if not isinstance(monitor, dict):
            continue
        overlap = rect_intersection(target_rect, monitor_rect(monitor))
        area = rect_area(overlap)
        if area > best_area:
            best = dict(monitor)
            best_area = area
    return best


def monitor_for_point(display: Dict[str, Any] | None, x: int, y: int) -> Dict[str, Any]:
    metadata = display if isinstance(display, dict) else {}
    monitors = metadata.get("monitors", []) if isinstance(metadata.get("monitors", []), list) else []
    for monitor in monitors:
        if isinstance(monitor, dict) and rect_contains_point(monitor_rect(monitor), x, y):
            return dict(monitor)
    return {}


def capture_space_from_observation(
    observation: Dict[str, Any] | None,
    *,
    display: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    data = observation if isinstance(observation, dict) else {}
    bounds = _rect(data.get("screenshot_bounds", {}))
    capture_monitor: Dict[str, Any] = {}
    metadata = display if isinstance(display, dict) else {}
    capture_monitor_id = _trim_text(data.get("capture_monitor_id", ""), limit=80)
    capture_monitor_index = _coerce_int(data.get("capture_monitor_index", 0), 0, minimum=0, maximum=16)
    monitors = metadata.get("monitors", []) if isinstance(metadata.get("monitors", []), list) else []
    for item in monitors:
        if not isinstance(item, dict):
            continue
        item_monitor_id = _trim_text(item.get("monitor_id", ""), limit=80)
        item_index = _coerce_int(item.get("index", 0), 0, minimum=0, maximum=16)
        if capture_monitor_id and item_monitor_id == capture_monitor_id:
            capture_monitor = dict(item)
            break
        if capture_monitor_index > 0 and item_index == capture_monitor_index:
            capture_monitor = dict(item)
            break
    if not capture_monitor and bounds.get("width", 0) > 0 and bounds.get("height", 0) > 0:
        capture_monitor = monitor_for_rect(metadata, bounds)
    if not capture_monitor and _trim_text(data.get("primary_monitor_id", ""), limit=80):
        primary = primary_monitor(metadata)
        if primary:
            capture_monitor = dict(primary)
    return {
        "scope": _trim_text(data.get("screenshot_scope", ""), limit=40),
        "bounds": bounds,
        "monitor_id": _trim_text(capture_monitor.get("monitor_id", capture_monitor_id), limit=80),
        "monitor_index": _coerce_int(capture_monitor.get("index", capture_monitor_index), 0, minimum=0, maximum=16),
        "device_name": _trim_text(capture_monitor.get("device_name", ""), limit=120),
        "is_primary": bool(capture_monitor.get("is_primary", False)),
        "dpi_x": _coerce_int(capture_monitor.get("dpi_x", 96), 96, minimum=72, maximum=960),
        "dpi_y": _coerce_int(capture_monitor.get("dpi_y", 96), 96, minimum=72, maximum=960),
        "scale_x": _coerce_float(capture_monitor.get("scale_x", 1.0), 1.0),
        "scale_y": _coerce_float(capture_monitor.get("scale_y", 1.0), 1.0),
        "coordinate_unit": "physical_pixel",
    }


def build_desktop_coordinate_mapping(
    *,
    coordinate_mode: str,
    requested_point: Dict[str, Any],
    display: Dict[str, Any] | None = None,
    target_window: Dict[str, Any] | None = None,
    observation: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    normalized_mode = _trim_text(coordinate_mode, limit=40).lower() or "absolute"
    requested_x = _coerce_int(requested_point.get("x", 0), 0)
    requested_y = _coerce_int(requested_point.get("y", 0), 0)
    metadata = display if isinstance(display, dict) else {}
    target = target_window if isinstance(target_window, dict) else {}
    target_rect = _rect(target.get("rect", {}))
    capture_space = capture_space_from_observation(observation, display=metadata)
    error = ""
    reason = ""
    fallback_reason = ""
    final_x = requested_x
    final_y = requested_y
    if normalized_mode == "window_relative":
        if not target_rect.get("width", 0) or not target_rect.get("height", 0):
            error = "The target window does not expose usable visible bounds for a relative pointer action."
        else:
            final_x = int(target_rect.get("x", 0)) + requested_x
            final_y = int(target_rect.get("y", 0)) + requested_y
            reason = "window_relative_to_target_window"
    elif normalized_mode == "capture_relative":
        capture_bounds = capture_space.get("bounds", {}) if isinstance(capture_space.get("bounds", {}), dict) else {}
        if not capture_bounds.get("width", 0) or not capture_bounds.get("height", 0):
            error = "The selected screenshot observation does not expose usable capture bounds for capture-relative targeting."
        else:
            final_x = int(capture_bounds.get("x", 0)) + requested_x
            final_y = int(capture_bounds.get("y", 0)) + requested_y
            reason = "capture_relative_to_observation"
    else:
        normalized_mode = "absolute"
        reason = "absolute_input_point"

    selected_monitor = monitor_for_point(metadata, final_x, final_y)
    if not selected_monitor and target_rect.get("width", 0) and target_rect.get("height", 0):
        selected_monitor = monitor_for_rect(metadata, target_rect)
        if selected_monitor:
            fallback_reason = "target_window_monitor_fallback"
    if not selected_monitor and capture_space.get("monitor_id"):
        selected_monitor = {
            "monitor_id": capture_space.get("monitor_id", ""),
            "index": capture_space.get("monitor_index", 0),
            "device_name": capture_space.get("device_name", ""),
            "is_primary": bool(capture_space.get("is_primary", False)),
            "left": capture_space.get("bounds", {}).get("x", 0) if isinstance(capture_space.get("bounds", {}), dict) else 0,
            "top": capture_space.get("bounds", {}).get("y", 0) if isinstance(capture_space.get("bounds", {}), dict) else 0,
            "width": capture_space.get("bounds", {}).get("width", 0) if isinstance(capture_space.get("bounds", {}), dict) else 0,
            "height": capture_space.get("bounds", {}).get("height", 0) if isinstance(capture_space.get("bounds", {}), dict) else 0,
            "dpi_x": capture_space.get("dpi_x", 96),
            "dpi_y": capture_space.get("dpi_y", 96),
            "scale_x": capture_space.get("scale_x", 1.0),
            "scale_y": capture_space.get("scale_y", 1.0),
        }
        if selected_monitor:
            fallback_reason = "capture_monitor_fallback"
    if not selected_monitor:
        selected_monitor = primary_monitor(metadata)
        if selected_monitor:
            fallback_reason = "primary_monitor_fallback"

    monitor_bounds = monitor_rect(selected_monitor)
    mapping = {
        "mode": normalized_mode,
        "requested_point": {"x": requested_x, "y": requested_y},
        "capture_space": {
            "scope": _trim_text(capture_space.get("scope", ""), limit=40),
            "bounds": _rect(capture_space.get("bounds", {})),
            "monitor_id": _trim_text(capture_space.get("monitor_id", ""), limit=80),
            "monitor_index": _coerce_int(capture_space.get("monitor_index", 0), 0, minimum=0, maximum=16),
            "device_name": _trim_text(capture_space.get("device_name", ""), limit=120),
            "coordinate_unit": "physical_pixel",
        },
        "window_space": {
            "window_id": _trim_text(target.get("window_id", ""), limit=40),
            "title": _trim_text(target.get("title", ""), limit=180),
            "bounds": target_rect,
        },
        "monitor_space": {
            "monitor_id": _trim_text(selected_monitor.get("monitor_id", ""), limit=80),
            "monitor_index": _coerce_int(selected_monitor.get("index", 0), 0, minimum=0, maximum=16),
            "device_name": _trim_text(selected_monitor.get("device_name", ""), limit=120),
            "is_primary": bool(selected_monitor.get("is_primary", False)),
            "bounds": monitor_bounds,
            "dpi_x": _coerce_int(selected_monitor.get("dpi_x", 96), 96, minimum=72, maximum=960),
            "dpi_y": _coerce_int(selected_monitor.get("dpi_y", 96), 96, minimum=72, maximum=960),
            "scale_x": _coerce_float(selected_monitor.get("scale_x", 1.0), 1.0),
            "scale_y": _coerce_float(selected_monitor.get("scale_y", 1.0), 1.0),
        },
        "input_space": {
            "x": final_x,
            "y": final_y,
            "coordinate_unit": "physical_pixel",
        },
        "reason": _trim_text(reason, limit=80),
        "fallback_reason": _trim_text(fallback_reason, limit=80),
        "summary": "",
    }
    if error:
        mapping["reason"] = "mapping_error"
        mapping["summary"] = error
    else:
        window_title = _trim_text(target.get("title", ""), limit=120)
        monitor_name = _trim_text(selected_monitor.get("device_name", ""), limit=120) or _trim_text(selected_monitor.get("monitor_id", ""), limit=80)
        monitor_scale = _coerce_float(selected_monitor.get("scale_x", 1.0), 1.0)
        mapping["summary"] = _trim_text(
            (
                f"Mapped {normalized_mode.replace('_', ' ')} point ({requested_x}, {requested_y}) to "
                f"physical input point ({final_x}, {final_y})"
                + (f" in '{window_title}'" if window_title else "")
                + (f" on {monitor_name}" if monitor_name else "")
                + (f" at {monitor_scale:.2f}x scale." if monitor_name else ".")
            ),
            limit=240,
        )
    return mapping


def action_point_from_mapping(mapping: Dict[str, Any] | None) -> Tuple[Dict[str, int], str]:
    normalized = mapping if isinstance(mapping, dict) else {}
    input_space = normalized.get("input_space", {}) if isinstance(normalized.get("input_space", {}), dict) else {}
    x = _coerce_int(input_space.get("x", 0), 0)
    y = _coerce_int(input_space.get("y", 0), 0)
    error = ""
    if str(normalized.get("reason", "")).strip() == "mapping_error":
        error = _trim_text(normalized.get("summary", ""), limit=240) or "Could not map the requested bounded desktop point."
    return {"x": x, "y": y}, error
