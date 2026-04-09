"""Thin facade — re-exports from the split desktop_* sub-modules.

All existing ``from tools.desktop import X`` statements continue to work.
"""
from __future__ import annotations

# ── constants, ctypes structures, Win32 handles, shared state ────────
from tools.desktop_constants import *  # noqa: F401,F403
from tools.desktop_constants import (  # noqa: F401  — underscore names excluded from *
    _DPI_AWARENESS_LOCK,
    _DPI_AWARENESS_STATE,
    _BACKEND_LOCK,
    _WINDOW_BACKEND,
    _SCREENSHOT_BACKEND,
    _UI_EVIDENCE_BACKEND,
    _DESKTOP_OBSERVATIONS,
    _OBSERVATION_LOCK,
    _OBSERVATION_COUNTER,
    _DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
)

# ── window enumeration, info, find, focus, backend access ────────────
from tools.desktop_backends import (  # noqa: F401
    probe_window_readiness,
    probe_visual_stability,
    probe_process_context,
)

from tools.desktop_windows import (  # noqa: F401
    _dpi_awareness_pointer,
    _ensure_process_dpi_awareness,
    _trim_text,
    _coerce_int,
    _coerce_bool,
    _hex_hwnd,
    _parse_hwnd,
    _timestamp,
    _get_window_text,
    _get_class_name,
    _get_process_name,
    _is_window_cloaked,
    _window_rect,
    _window_info,
    _window_is_listable,
    _enum_windows_native,
    _active_window_info_native,
    _get_window_backend,
    _get_screenshot_backend,
    _get_ui_evidence_backend,
    get_desktop_backend_status,
    probe_ui_evidence,
    _window_probe_target,
    _metadata_readiness_for_window,
    _readiness_probe_for_window,
    _visual_stability_for_window,
    _process_context_for_window,
    _inspect_window_state_internal,
    _latest_evidence_ref_for_observation,
    _evidence_ref_has_screenshot,
    _record_desktop_evidence,
    _enum_windows,
    _find_window_by_exact_title_native,
    _active_window_info,
    _find_window,
)

# ── display metadata, observation registry, result builders ──────────
from tools.desktop_observation import (  # noqa: F401
    _virtual_screen_rect,
    _display_metadata,
    _monitor_rect,
    _primary_monitor_info,
    _rect_intersection,
    _rect_area,
    _window_monitor_metadata,
    _enrich_window_monitor_metadata,
    _window_is_on_primary_monitor,
    _point_in_rect,
    _register_observation,
    _lookup_observation,
    shutdown_desktop_runtime,
    _desktop_result,
    _desktop_strategy_view,
    _normalize_expected_process_names,
    _window_expectation_score,
    _best_desktop_window_candidate,
    _probe_expected_process,
    _sample_desktop_action_verification,
    _prepare_desktop_strategy_context,
    _approval_granted,
    _sensitive_field_label,
    _validate_fresh_observation,
    _foreground_window_matches,
)

# ── mouse, keyboard, capture, evidence, window/pointer tool funcs ────
from tools.desktop_input import (  # noqa: F401
    _current_cursor_point,
    _window_center_point,
    _normalize_mouse_button,
    _click_button_flags,
    _resolve_pointer_target_window,
    _resolve_pointer_point,
    _send_mouse_click,
    _send_mouse_scroll,
    _focus_window_handle_native,
    _restore_window_handle_native,
    _show_window_handle_native,
    _capture_bitmap_native,
    _focus_window_handle,
    _wait_for_window_ready,
    _execute_window_recovery,
    _capture_with_backend,
    _capture_path,
    _safe_unlink,
    _file_sha1,
    _primary_monitor_bounds,
    _clip_bounds_to_rect,
    _capture_derived_active_window_crop,
    _primary_monitor_activity_error,
    capture_desktop_evidence_frame,
    record_captured_desktop_evidence,
    desktop_list_windows,
    desktop_get_active_window,
    desktop_inspect_window_state,
    desktop_wait_for_window_ready,
    desktop_recover_window,
    desktop_focus_window,
    desktop_capture_screenshot,
    _pause_desktop_action,
    _prepare_pointer_action_context,
    _pointer_checkpoint_resume_args,
    desktop_move_mouse,
    desktop_hover_point,
    desktop_click_mouse,
    desktop_click_point,
    desktop_scroll,
    _send_text,
    _normalize_modifier_list,
    _normalize_key_name,
    _is_modifier_shortcut_only,
    _validate_desktop_key_request,
    _desktop_key_sequence_preview,
    _normalize_desktop_key_sequence,
    _validate_desktop_key_sequence,
    _desktop_key_sequence_chain_preview,
    _send_key_sequence,
    _send_key_sequence_chain,
    desktop_type_text,
    desktop_press_key,
)

# ── process management, open_target, key sequence tool ───────────────
from tools.desktop_process import (  # noqa: F401
    _current_desktop_context,
    _dedupe_windows,
    _open_match_score,
    _best_open_window_candidate,
    _process_hint_snapshot,
    _sample_open_verification,
    _open_target_display,
    _open_target_summary,
    _active_window_process_target,
    desktop_press_key_sequence,
    desktop_list_processes,
    desktop_inspect_process,
    desktop_start_process,
    desktop_stop_process,
    desktop_run_command,
    desktop_open_target,
)

# ── tool schema dicts (used by tools/registry.py) ───────────────────
from tools.desktop_schemas import (  # noqa: F401
    DESKTOP_LIST_WINDOWS_TOOL,
    DESKTOP_GET_ACTIVE_WINDOW_TOOL,
    DESKTOP_FOCUS_WINDOW_TOOL,
    DESKTOP_INSPECT_WINDOW_STATE_TOOL,
    DESKTOP_RECOVER_WINDOW_TOOL,
    DESKTOP_WAIT_FOR_WINDOW_READY_TOOL,
    DESKTOP_CAPTURE_SCREENSHOT_TOOL,
    DESKTOP_MOVE_MOUSE_TOOL,
    DESKTOP_HOVER_POINT_TOOL,
    DESKTOP_CLICK_MOUSE_TOOL,
    DESKTOP_CLICK_POINT_TOOL,
    DESKTOP_SCROLL_TOOL,
    DESKTOP_PRESS_KEY_TOOL,
    DESKTOP_PRESS_KEY_SEQUENCE_TOOL,
    DESKTOP_TYPE_TEXT_TOOL,
    DESKTOP_LIST_PROCESSES_TOOL,
    DESKTOP_INSPECT_PROCESS_TOOL,
    DESKTOP_START_PROCESS_TOOL,
    DESKTOP_STOP_PROCESS_TOOL,
    DESKTOP_RUN_COMMAND_TOOL,
    DESKTOP_OPEN_TARGET_TOOL,
)

# ── Wire "func" references into schemas (registry needs them) ────────
DESKTOP_LIST_WINDOWS_TOOL["func"] = desktop_list_windows
DESKTOP_GET_ACTIVE_WINDOW_TOOL["func"] = desktop_get_active_window
DESKTOP_FOCUS_WINDOW_TOOL["func"] = desktop_focus_window
DESKTOP_INSPECT_WINDOW_STATE_TOOL["func"] = desktop_inspect_window_state
DESKTOP_RECOVER_WINDOW_TOOL["func"] = desktop_recover_window
DESKTOP_WAIT_FOR_WINDOW_READY_TOOL["func"] = desktop_wait_for_window_ready
DESKTOP_CAPTURE_SCREENSHOT_TOOL["func"] = desktop_capture_screenshot
DESKTOP_MOVE_MOUSE_TOOL["func"] = desktop_move_mouse
DESKTOP_HOVER_POINT_TOOL["func"] = desktop_hover_point
DESKTOP_CLICK_MOUSE_TOOL["func"] = desktop_click_mouse
DESKTOP_CLICK_POINT_TOOL["func"] = desktop_click_point
DESKTOP_SCROLL_TOOL["func"] = desktop_scroll
DESKTOP_PRESS_KEY_TOOL["func"] = desktop_press_key
DESKTOP_PRESS_KEY_SEQUENCE_TOOL["func"] = desktop_press_key_sequence
DESKTOP_TYPE_TEXT_TOOL["func"] = desktop_type_text
DESKTOP_LIST_PROCESSES_TOOL["func"] = desktop_list_processes
DESKTOP_INSPECT_PROCESS_TOOL["func"] = desktop_inspect_process
DESKTOP_START_PROCESS_TOOL["func"] = desktop_start_process
DESKTOP_STOP_PROCESS_TOOL["func"] = desktop_stop_process
DESKTOP_RUN_COMMAND_TOOL["func"] = desktop_run_command
DESKTOP_OPEN_TARGET_TOOL["func"] = desktop_open_target
