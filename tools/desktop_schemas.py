"""Lean tool-schema dictionaries for every ``desktop_*`` tool."""
from __future__ import annotations

# ── Caps constants ──────────────────────────────────────────────────────
DESKTOP_DEFAULT_TYPE_MAX_CHARS = 160
DESKTOP_MAX_KEY_REPEAT = 4
DESKTOP_MAX_KEY_SEQUENCE_STEPS = 3
DESKTOP_MAX_HOVER_MS = 2_000
DESKTOP_MAX_SCROLL_UNITS = 8
DESKTOP_MAX_PROCESS_LIMIT = 16
DESKTOP_MAX_COMMAND_TIMEOUT_SECONDS = 30

# ── Tool schemas ────────────────────────────────────────────────────────

DESKTOP_LIST_WINDOWS_TOOL = {
    "name": "desktop_list_windows",
    "description": "List visible desktop windows with titles and IDs.",
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
    "description": "Get the currently active window's title, ID, and process.",
    "input_schema": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}

DESKTOP_FOCUS_WINDOW_TOOL = {
    "name": "desktop_focus_window",
    "description": "Bring a window to the foreground by title or window_id.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "window_id": {"type": "string"},
        },
        "additionalProperties": False,
    },
}

DESKTOP_INSPECT_WINDOW_STATE_TOOL = {
    "name": "desktop_inspect_window_state",
    "description": "Inspect a window's state (minimized, hidden, loading, etc.).",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "window_id": {"type": "string"},
        },
        "additionalProperties": False,
    },
}

DESKTOP_RECOVER_WINDOW_TOOL = {
    "name": "desktop_recover_window",
    "description": "Attempt to restore/show/refocus a window that isn't responding normally.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "window_id": {"type": "string"},
        },
        "additionalProperties": False,
    },
}

DESKTOP_WAIT_FOR_WINDOW_READY_TOOL = {
    "name": "desktop_wait_for_window_ready",
    "description": "Wait briefly for a window to finish loading.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "window_id": {"type": "string"},
            "wait_seconds": {"type": "number", "minimum": 0.2, "maximum": 3.0},
        },
        "additionalProperties": False,
    },
}

DESKTOP_CAPTURE_SCREENSHOT_TOOL = {
    "name": "desktop_capture_screenshot",
    "description": "Take a screenshot of the primary monitor. Returns the saved file path.",
    "input_schema": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}

DESKTOP_MOVE_MOUSE_TOOL = {
    "name": "desktop_move_mouse",
    "description": "Move the mouse cursor to absolute screen coordinates.",
    "input_schema": {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
        },
        "required": ["x", "y"],
        "additionalProperties": False,
    },
}

DESKTOP_HOVER_POINT_TOOL = {
    "name": "desktop_hover_point",
    "description": "Move the mouse to a point and hover briefly.",
    "input_schema": {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "hover_ms": {"type": "integer", "minimum": 120, "maximum": DESKTOP_MAX_HOVER_MS},
        },
        "required": ["x", "y"],
        "additionalProperties": False,
    },
}

DESKTOP_CLICK_MOUSE_TOOL = {
    "name": "desktop_click_mouse",
    "description": "Click at screen coordinates. Supports left/right click and double click.",
    "input_schema": {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "button": {"type": "string", "enum": ["left", "right"]},
            "double_click": {"type": "boolean"},
        },
        "required": ["x", "y"],
        "additionalProperties": False,
    },
}

DESKTOP_CLICK_POINT_TOOL = {
    "name": "desktop_click_point",
    "description": "Click one exact screen coordinate.",
    "input_schema": {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
        },
        "required": ["x", "y"],
        "additionalProperties": False,
    },
}

DESKTOP_SCROLL_TOOL = {
    "name": "desktop_scroll",
    "description": "Scroll up or down in the active window.",
    "input_schema": {
        "type": "object",
        "properties": {
            "direction": {"type": "string", "enum": ["up", "down"]},
            "lines": {"type": "integer", "minimum": 1, "maximum": DESKTOP_MAX_SCROLL_UNITS},
        },
        "required": ["direction"],
        "additionalProperties": False,
    },
}

DESKTOP_PRESS_KEY_TOOL = {
    "name": "desktop_press_key",
    "description": "Press a key or key combination (e.g. enter, tab, ctrl+c).",
    "input_schema": {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "modifiers": {
                "type": "array",
                "items": {"type": "string", "enum": ["ctrl", "shift"]},
                "maxItems": 2,
            },
            "repeat": {"type": "integer", "minimum": 1, "maximum": DESKTOP_MAX_KEY_REPEAT},
        },
        "required": ["key"],
        "additionalProperties": False,
    },
}

DESKTOP_PRESS_KEY_SEQUENCE_TOOL = {
    "name": "desktop_press_key_sequence",
    "description": "Press a sequence of key combinations in order.",
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
                            "items": {"type": "string", "enum": ["ctrl", "shift"]},
                            "maxItems": 2,
                        },
                        "repeat": {"type": "integer", "minimum": 1, "maximum": DESKTOP_MAX_KEY_REPEAT},
                    },
                    "required": ["key"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["sequence"],
        "additionalProperties": False,
    },
}

DESKTOP_TYPE_TEXT_TOOL = {
    "name": "desktop_type_text",
    "description": "Type text into the currently focused field.",
    "input_schema": {
        "type": "object",
        "properties": {
            "value": {"type": "string"},
        },
        "required": ["value"],
        "additionalProperties": False,
    },
}

DESKTOP_LIST_PROCESSES_TOOL = {
    "name": "desktop_list_processes",
    "description": "List running processes with PID and status.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": DESKTOP_MAX_PROCESS_LIMIT},
        },
        "additionalProperties": False,
    },
}

DESKTOP_INSPECT_PROCESS_TOOL = {
    "name": "desktop_inspect_process",
    "description": "Get details about a specific process by PID or name.",
    "input_schema": {
        "type": "object",
        "properties": {
            "pid": {"type": "integer"},
            "process_name": {"type": "string"},
        },
        "additionalProperties": False,
    },
}

DESKTOP_START_PROCESS_TOOL = {
    "name": "desktop_start_process",
    "description": "Start a local process.",
    "input_schema": {
        "type": "object",
        "properties": {
            "executable": {"type": "string"},
            "arguments": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
            "cwd": {"type": "string"},
        },
        "required": ["executable"],
        "additionalProperties": False,
    },
}

DESKTOP_STOP_PROCESS_TOOL = {
    "name": "desktop_stop_process",
    "description": "Stop a running process by PID.",
    "input_schema": {
        "type": "object",
        "properties": {
            "pid": {"type": "integer"},
        },
        "required": ["pid"],
        "additionalProperties": False,
    },
}

DESKTOP_RUN_COMMAND_TOOL = {
    "name": "desktop_run_command",
    "description": "Run a shell command (powershell or cmd) and return stdout/stderr.",
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "cwd": {"type": "string"},
            "shell_kind": {"type": "string", "enum": ["powershell", "cmd"]},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": DESKTOP_MAX_COMMAND_TIMEOUT_SECONDS},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
}

DESKTOP_OPEN_TARGET_TOOL = {
    "name": "desktop_open_target",
    "description": "Open a file, folder, URL, or application using Windows default associations.",
    "input_schema": {
        "type": "object",
        "properties": {
            "target": {"type": "string"},
        },
        "required": ["target"],
        "additionalProperties": False,
    },
}
