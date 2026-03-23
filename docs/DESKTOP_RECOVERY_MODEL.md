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
- `target_withdrawn`
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
- run `python live_agent_eval.py --scenario desktop_recovery_grounding --report-path data/evals/live_agent_eval_desktop_recovery_grounding_report.json`

## Current integration status

The recovery layer now participates in the real bounded desktop flow:

- planner guidance can choose `desktop_inspect_window_state`, `desktop_recover_window`, and `desktop_wait_for_window_ready`
- the desktop loop can use recovery/readiness before approval-gated click/type checkpoints
- grouped desktop failure recovery can re-inspect, recover, wait briefly, and then refresh evidence once when needed
- compact recovery/readiness diagnostics are exposed through state and local API snapshots

## Current live-validation boundary

Recent focused live validation confirmed the improved minimized / wrong-foreground recovery path and evidence-backed screenshot capture path.

The main remaining live boundary is fully withdrawn or tray-like hidden windows. In that state, the bounded stack now tries a stricter native exact-title lookup and relaxed hidden enumeration first. If Windows still only surfaces a withdrawn-like handle with no visible recoverable state, the recovery model now classifies that as `target_withdrawn` and stops with a clear tray/background-style report instead of guessing.

That limitation is explicit and currently preferred over guessing or widening control.

## What this prepares

This recovery layer prepares the project for safer later desktop growth by making window-state failures explicit, inspectable, and recoverable before broader automation/control expansion.
