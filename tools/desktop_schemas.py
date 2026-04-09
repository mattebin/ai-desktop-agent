"""Tool-schema dictionaries for every ``desktop_*`` tool.

Extracted from ``tools/desktop.py`` so the heavy implementation module
does not need to be imported just to read tool metadata.

These are plain ``dict`` literals.  The only module-level values they
reference are the handful of ``DESKTOP_*`` caps constants duplicated
below; no other imports are required.
"""
from __future__ import annotations

# ── Caps constants referenced inside the schemas ──────────────────────
DESKTOP_DEFAULT_TYPE_MAX_CHARS = 160
DESKTOP_MAX_KEY_REPEAT = 4
DESKTOP_MAX_KEY_SEQUENCE_STEPS = 3
DESKTOP_MAX_HOVER_MS = 2_000
DESKTOP_MAX_SCROLL_UNITS = 8
DESKTOP_MAX_PROCESS_LIMIT = 16
DESKTOP_MAX_COMMAND_TIMEOUT_SECONDS = 20

# ── Tool schemas ──────────────────────────────────────────────────────

DESKTOP_LIST_WINDOWS_TOOL = {
    "name": "desktop_list_windows",
    "description": (
        "List visible top-level Windows desktop windows in a bounded way, including compact titles, ids, "
        "and which window is currently active. This is read-only desktop inspection."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "additionalProperties": False,
    },
}


DESKTOP_GET_ACTIVE_WINDOW_TOOL = {
    "name": "desktop_get_active_window",
    "description": (
        "Return the currently active desktop window in a bounded, read-only way, including its title, id, "
        "process name, and bounds."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "additionalProperties": False,
    },
}


DESKTOP_FOCUS_WINDOW_TOOL = {
    "name": "desktop_focus_window",
    "description": (
        "Bring a specific desktop window to the foreground by exact or partial title match, "
        "or by window_id. This bounded tool can restore or show the window first when needed, "
        "then verify whether Windows actually confirmed foreground focus."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "match": {"type": "string"},
            "window_id": {"type": "string"},
            "exact": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 4, "maximum": 30},
        },
        "additionalProperties": False,
    },
}


DESKTOP_INSPECT_WINDOW_STATE_TOOL = {
    "name": "desktop_inspect_window_state",
    "description": (
        "Inspect the current state of a target desktop window in a bounded, read-only way. "
        "Use it to diagnose cases like minimized, hidden, tray/background, wrong foreground window, "
        "loading/not-ready state, or visually unstable UI before taking any desktop action."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "match": {"type": "string"},
            "window_id": {"type": "string"},
            "expected_window_title": {"type": "string"},
            "expected_window_id": {"type": "string"},
            "exact": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 4, "maximum": 30},
            "ui_limit": {"type": "integer", "minimum": 1, "maximum": 16},
            "check_visual_stability": {"type": "boolean"},
            "stability_samples": {"type": "integer", "minimum": 2, "maximum": 4},
            "stability_interval_ms": {"type": "integer", "minimum": 40, "maximum": 400},
        },
        "additionalProperties": False,
    },
}


DESKTOP_RECOVER_WINDOW_TOOL = {
    "name": "desktop_recover_window",
    "description": (
        "Attempt one bounded recovery path for a target desktop window. "
        "This tool can inspect, restore, show, refocus, or briefly wait for readiness, "
        "then report normalized recovery reasons and outcomes without clicking or typing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "match": {"type": "string"},
            "window_id": {"type": "string"},
            "expected_window_title": {"type": "string"},
            "expected_window_id": {"type": "string"},
            "exact": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 4, "maximum": 30},
            "ui_limit": {"type": "integer", "minimum": 1, "maximum": 16},
            "max_attempts": {"type": "integer", "minimum": 0, "maximum": 4},
            "wait_seconds": {"type": "number", "minimum": 0.2, "maximum": 3.0},
            "poll_interval_seconds": {"type": "number", "minimum": 0.08, "maximum": 0.4},
            "stability_samples": {"type": "integer", "minimum": 2, "maximum": 4},
            "stability_interval_ms": {"type": "integer", "minimum": 40, "maximum": 400},
        },
        "additionalProperties": False,
    },
}


DESKTOP_WAIT_FOR_WINDOW_READY_TOOL = {
    "name": "desktop_wait_for_window_ready",
    "description": (
        "Wait briefly and inspect whether a target desktop window becomes ready and visually stable "
        "enough for bounded work. This is read-only and intended for loading or animated desktop states."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "match": {"type": "string"},
            "window_id": {"type": "string"},
            "expected_window_title": {"type": "string"},
            "expected_window_id": {"type": "string"},
            "exact": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 4, "maximum": 30},
            "ui_limit": {"type": "integer", "minimum": 1, "maximum": 16},
            "wait_seconds": {"type": "number", "minimum": 0.2, "maximum": 3.0},
            "poll_interval_seconds": {"type": "number", "minimum": 0.08, "maximum": 0.4},
            "stability_samples": {"type": "integer", "minimum": 2, "maximum": 4},
            "stability_interval_ms": {"type": "integer", "minimum": 40, "maximum": 400},
        },
        "additionalProperties": False,
    },
}


DESKTOP_CAPTURE_SCREENSHOT_TOOL = {
    "name": "desktop_capture_screenshot",
    "description": (
        "Capture a bounded screenshot of the Windows primary display, the active window, or the full virtual desktop "
        "and return the saved file path plus compact desktop state metadata. Reliability-first captures prefer the "
        "primary display and can attach a derived active-window crop."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "scope": {"type": "string", "enum": ["primary_monitor", "active_window", "desktop"]},
            "name": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "additionalProperties": False,
    },
}


DESKTOP_MOVE_MOUSE_TOOL = {
    "name": "desktop_move_mouse",
    "description": (
        "Move the mouse cursor to one bounded absolute point, one point relative to the active target window, "
        "or one point relative to the latest screenshot-backed capture. Requires explicit approval_status=approved, "
        "a fresh observation_token, and exact visible coordinates."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "relative_x": {"type": "integer"},
            "relative_y": {"type": "integer"},
            "capture_x": {"type": "integer"},
            "capture_y": {"type": "integer"},
            "coordinate_mode": {"type": "string", "enum": ["absolute", "window_relative", "capture_relative"]},
            "title": {"type": "string"},
            "match": {"type": "string"},
            "window_id": {"type": "string"},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
            "max_observation_age_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
        },
        "additionalProperties": False,
    },
}


DESKTOP_HOVER_POINT_TOOL = {
    "name": "desktop_hover_point",
    "description": (
        "Move the mouse to one bounded point and hover briefly over it inside the active target window. "
        "Requires explicit approval_status=approved and a fresh observation_token."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "relative_x": {"type": "integer"},
            "relative_y": {"type": "integer"},
            "capture_x": {"type": "integer"},
            "capture_y": {"type": "integer"},
            "coordinate_mode": {"type": "string", "enum": ["absolute", "window_relative", "capture_relative"]},
            "title": {"type": "string"},
            "match": {"type": "string"},
            "window_id": {"type": "string"},
            "hover_ms": {"type": "integer", "minimum": 120, "maximum": DESKTOP_MAX_HOVER_MS},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
            "max_observation_age_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
        },
        "additionalProperties": False,
    },
}


DESKTOP_CLICK_MOUSE_TOOL = {
    "name": "desktop_click_mouse",
    "description": (
        "Perform one bounded mouse click at an exact visible point in the active target window. "
        "Supports left click, right click, and a bounded double click with explicit approval."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "relative_x": {"type": "integer"},
            "relative_y": {"type": "integer"},
            "capture_x": {"type": "integer"},
            "capture_y": {"type": "integer"},
            "coordinate_mode": {"type": "string", "enum": ["absolute", "window_relative", "capture_relative"]},
            "title": {"type": "string"},
            "match": {"type": "string"},
            "window_id": {"type": "string"},
            "button": {"type": "string", "enum": ["left", "right"]},
            "click_count": {"type": "integer", "minimum": 1, "maximum": 2},
            "double_click": {"type": "boolean"},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
            "max_observation_age_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
        },
        "additionalProperties": False,
    },
}


DESKTOP_CLICK_POINT_TOOL = {
    "name": "desktop_click_point",
    "description": (
        "Click one exact screen coordinate inside the currently active desktop window. "
        "Requires explicit approval_status=approved, a fresh observation_token from recent desktop inspection, "
        "and exact visible coordinates. If the window state changed, inspect or recover the window first. "
        "This tool performs one bounded click only."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "relative_x": {"type": "integer"},
            "relative_y": {"type": "integer"},
            "capture_x": {"type": "integer"},
            "capture_y": {"type": "integer"},
            "coordinate_mode": {"type": "string", "enum": ["absolute", "window_relative", "capture_relative"]},
            "title": {"type": "string"},
            "match": {"type": "string"},
            "window_id": {"type": "string"},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
            "max_observation_age_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
        },
        "additionalProperties": False,
    },
}


DESKTOP_SCROLL_TOOL = {
    "name": "desktop_scroll",
    "description": (
        "Scroll one bounded amount up or down in the active target window. "
        "Requires explicit approval_status=approved, a fresh observation_token, and stays limited to one bounded scroll step."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "direction": {"type": "string", "enum": ["up", "down"]},
            "scroll_units": {"type": "integer", "minimum": 1, "maximum": DESKTOP_MAX_SCROLL_UNITS},
            "lines": {"type": "integer", "minimum": 1, "maximum": DESKTOP_MAX_SCROLL_UNITS},
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "relative_x": {"type": "integer"},
            "relative_y": {"type": "integer"},
            "capture_x": {"type": "integer"},
            "capture_y": {"type": "integer"},
            "coordinate_mode": {"type": "string", "enum": ["absolute", "window_relative", "capture_relative"]},
            "title": {"type": "string"},
            "match": {"type": "string"},
            "window_id": {"type": "string"},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
            "max_observation_age_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
        },
        "additionalProperties": False,
    },
}


DESKTOP_PRESS_KEY_TOOL = {
    "name": "desktop_press_key",
    "description": (
        "Press one bounded safe keyboard key or Ctrl/Shift shortcut in the currently active desktop window. "
        "Requires explicit approval_status=approved, a fresh observation_token from recent desktop inspection, "
        "and stays limited to safe navigation keys and a small allowlist of Ctrl-based shortcuts. "
        "This tool does not send system keys, the Windows key, or unrestricted hotkeys."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "modifiers": {
                "type": "array",
                "items": {"type": "string", "enum": ["ctrl", "control", "shift"]},
                "minItems": 0,
                "maxItems": 2,
            },
            "repeat": {"type": "integer", "minimum": 1, "maximum": DESKTOP_MAX_KEY_REPEAT},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
            "max_observation_age_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
        },
        "required": ["key"],
        "additionalProperties": False,
    },
}


DESKTOP_PRESS_KEY_SEQUENCE_TOOL = {
    "name": "desktop_press_key_sequence",
    "description": (
        "Press a short bounded sequence of safe desktop key combinations in the active window. "
        "Requires explicit approval_status=approved, a fresh observation_token, and stays inside the safe key allowlist."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sequence": {
                "type": "array",
                "minItems": 1,
                "maxItems": DESKTOP_MAX_KEY_SEQUENCE_STEPS,
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "modifiers": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["ctrl", "control", "shift"]},
                            "minItems": 0,
                            "maxItems": 2,
                        },
                        "repeat": {"type": "integer", "minimum": 1, "maximum": DESKTOP_MAX_KEY_REPEAT},
                    },
                    "required": ["key"],
                    "additionalProperties": False,
                },
            },
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
            "max_observation_age_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
        },
        "required": ["sequence"],
        "additionalProperties": False,
    },
}


DESKTOP_TYPE_TEXT_TOOL = {
    "name": "desktop_type_text",
    "description": (
        "Type bounded plain text into the currently focused field in the active desktop window. "
        "Requires explicit approval_status=approved, a fresh observation_token from recent desktop inspection, "
        "and a non-sensitive field_label. If the target window is not ready, inspect or recover it first. "
        "This tool does not send Enter or submit forms."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "value": {"type": "string"},
            "field_label": {"type": "string"},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
            "max_text_length": {"type": "integer", "minimum": 1, "maximum": DESKTOP_DEFAULT_TYPE_MAX_CHARS},
            "max_observation_age_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
        },
        "required": ["value", "field_label"],
        "additionalProperties": False,
    },
}


DESKTOP_LIST_PROCESSES_TOOL = {
    "name": "desktop_list_processes",
    "description": (
        "List a bounded set of local desktop processes with compact diagnostics such as pid, status, background-candidate state, "
        "and whether the process is one of this operator's owned processes."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": DESKTOP_MAX_PROCESS_LIMIT},
            "include_background": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
}


DESKTOP_INSPECT_PROCESS_TOOL = {
    "name": "desktop_inspect_process",
    "description": (
        "Inspect one bounded local process in more detail, including command line excerpt, working directory, "
        "child processes, and owned-process metadata when available."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pid": {"type": "integer", "minimum": 0, "maximum": 10000000},
            "process_name": {"type": "string"},
            "owned_label": {"type": "string"},
            "child_limit": {"type": "integer", "minimum": 0, "maximum": 8},
        },
        "additionalProperties": False,
    },
}


DESKTOP_START_PROCESS_TOOL = {
    "name": "desktop_start_process",
    "description": (
        "Start one bounded owned local process with explicit approval. "
        "This is intended for safe test helpers or known local apps, not broad process spawning."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "executable": {"type": "string"},
            "arguments": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
            "cwd": {"type": "string"},
            "env": {"type": "object", "additionalProperties": {"type": "string"}},
            "owned_label": {"type": "string"},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
        },
        "required": ["executable"],
        "additionalProperties": False,
    },
}


DESKTOP_STOP_PROCESS_TOOL = {
    "name": "desktop_stop_process",
    "description": (
        "Stop one bounded owned local process with explicit approval. "
        "This tool only stops processes that were started as owned bounded processes."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pid": {"type": "integer", "minimum": 0, "maximum": 10000000},
            "owned_label": {"type": "string"},
            "wait_seconds": {"type": "integer", "minimum": 1, "maximum": 5},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
        },
        "additionalProperties": False,
    },
}


DESKTOP_RUN_COMMAND_TOOL = {
    "name": "desktop_run_command",
    "description": (
        "Run one bounded local command with explicit approval, a capped timeout, and captured stdout/stderr excerpts. "
        "This is a controlled command surface, not a general autonomous shell."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "cwd": {"type": "string"},
            "env": {"type": "object", "additionalProperties": {"type": "string"}},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": DESKTOP_MAX_COMMAND_TIMEOUT_SECONDS},
            "shell_kind": {"type": "string", "enum": ["powershell", "cmd"]},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
}


DESKTOP_OPEN_TARGET_TOOL = {
    "name": "desktop_open_target",
    "description": (
        "Open one Windows target in a bounded, strategy-aware way. "
        "Use this for files, folders, URLs, documents, images, and executable programs so the operator can choose "
        "the right Windows open semantics, switch strategy after failures, and verify whether the target really surfaced."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "target": {"type": "string"},
            "target_type": {
                "type": "string",
                "enum": [
                    "document_file",
                    "executable_program",
                    "folder_directory",
                    "image_media_file",
                    "text_code_file",
                    "unknown_ambiguous_path",
                    "url_web_resource",
                ],
            },
            "preferred_method": {
                "type": "string",
                "enum": [
                    "association_open",
                    "bounded_fallback",
                    "executable_launch",
                    "explorer_assisted_ui",
                    "focus_existing_window",
                    "url_browser",
                ],
            },
            "requested_method": {
                "type": "string",
                "enum": [
                    "association_open",
                    "bounded_fallback",
                    "executable_launch",
                    "explorer_assisted_ui",
                    "focus_existing_window",
                    "url_browser",
                ],
            },
            "force_strategy_switch": {"type": "boolean"},
            "avoid_strategy_families": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "association_open",
                        "bounded_fallback",
                        "executable_launch",
                        "explorer_assisted_ui",
                        "focus_existing_window",
                        "url_browser",
                    ],
                },
                "maxItems": 4,
            },
            "arguments": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
            "cwd": {"type": "string"},
            "env": {"type": "object", "additionalProperties": {"type": "string"}},
            "verification_samples": {"type": "integer", "minimum": 2, "maximum": 4},
            "verification_interval_ms": {"type": "integer", "minimum": 80, "maximum": 320},
            "observation_token": {"type": "string"},
            "approval_status": {"type": "string", "enum": ["approved", "not approved"]},
            "checkpoint_reason": {"type": "string"},
        },
        "required": ["target"],
        "additionalProperties": False,
    },
}
