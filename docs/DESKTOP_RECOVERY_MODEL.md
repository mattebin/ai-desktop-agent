# Desktop Recovery Model

This project now includes a bounded desktop recovery layer for messy real-world window conditions.

## Purpose

The recovery layer exists to stop overfitting to one fragile foreground path.

It supports bounded diagnosis and recovery for cases like:

- wrong window focused
- target window minimized
- target hidden or cloaked
- target missing or likely in tray/background state
- target not foreground even after an internal focus request
- target loading or not yet ready
- visually unstable or still animating UI
- expected vs actual window mismatch

## Supported bounded strategies

- `restore_then_focus`
- `show_then_focus`
- `focus_then_verify`
- `wait_for_readiness`
- `reinspect_target`
- `report_missing_target`
- `stop_and_report`

These are explicit and limited.

Current non-goals:

- no broad unrestricted desktop control
- no drag and drop
- no arbitrary hotkeys
- no autonomous navigation loops
- no OCR-heavy desktop interpretation

## Current recovery reason codes

- `target_not_found`
- `target_minimized`
- `target_hidden`
- `foreground_not_confirmed`
- `target_not_ready`
- `target_loading`
- `target_mismatch`
- `tray_or_background_state`
- `visual_state_unstable`
- `recovery_succeeded`
- `recovery_failed`
- `recovery_skipped`

## Readiness and stability

Readiness is currently bounded and local-only:

- window metadata via PyWinCtl/native state
- read-only UI probing via pywinauto
- lightweight visual stability checks via mss sample comparison

The system should prefer one bounded recovery pass and then a clear report over repeated blind retries.

## How to test locally

- run `python smoke_test.py`
- inspect the desktop tool registry and recovery reason coverage
- verify missing/minimized/loading classifications in local deterministic tests

## What this prepares

This recovery layer prepares the project for safer later desktop growth by making window-state failures explicit, inspectable, and recoverable before broader automation/control expansion.
