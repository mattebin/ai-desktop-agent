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
- title drift between expected and observed window labels

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

## Adaptive recovery budget

Recovery attempts are now bounded per-reason instead of using one global retry limit.

Current per-reason budgets:

- `target_minimized`: 1
- `target_hidden`: 2
- `foreground_not_confirmed`: 2
- `target_mismatch`: 2
- `target_loading`: 4
- `target_not_ready`: 4
- `visual_state_unstable`: 4

This prevents wasting retries on conditions that rarely self-resolve (minimized windows) while allowing more patience for conditions that often do (loading screens, visual instability).

## Hung process detection

The recovery layer now detects windows that have stopped processing messages using the Win32 `IsHungAppWindow` API. When a target window's process has hung windows, this information is surfaced in the process context so the operator can distinguish between a slow window and a frozen application.

## Post-action verification

After any desktop mutating tool (click, type, focus) reports success, the loop now performs an independent Win32 `GetForegroundWindow` / `GetWindowTextW` check that bypasses all tool layers. This catches cases where a tool reports success but the foreground window changed unexpectedly, surfacing an `independent_verification_mismatch` classification.

## Uncertainty surfacing

The retry policy now distinguishes between `uncertain` outcomes and hard `failure` / `no_progress` outcomes:

- `uncertain`: asks the user for guidance instead of retrying blindly
- `failure` / `no_progress`: stops after budget exhaustion

This prevents the operator from confidently retrying something it does not actually understand.

## Readiness and stability

Readiness is currently bounded and local-only:

- window metadata via PyWinCtl/native state
- read-only UI probing via pywinauto
- lightweight visual stability checks via perceptual hashing (dHash) instead of pixel-exact comparison
- minimized, hidden, or withdrawn-like targets can short-circuit to metadata-backed not-ready or missing states before deeper UIA probing
- pywinauto control-tree probes are bounded lazily so they do not materialize an entire descendant tree before slicing

Perceptual hashing (via `imagehash.dhash()`) tolerates ClearType, anti-aliasing, and compression differences that would cause pixel-exact SHA1 comparisons to report false instability.

The system should prefer one bounded recovery pass and then a clear report over repeated blind retries.

## Matching and diagnostics

Recovery no longer relies on plain substring matching alone.

The bounded matcher now contributes:

- exact and containment matching first
- bounded fuzzy title matching for small drift
- optional process/class hints
- candidate previews with scores and confidence
- explicit match engine and match-reason diagnostics

This keeps recovery explainable without turning the desktop path into fuzzy chaos.

## How to test locally

- run `python smoke_test.py`
- inspect the desktop tool registry and recovery reason coverage
- verify missing/minimized/loading classifications in local deterministic tests
- verify bounded fuzzy title-drift and candidate-ranking diagnostics in local deterministic tests
- run `python live_agent_eval.py --scenario desktop_recovery_grounding --report-path data/evals/live_agent_eval_desktop_recovery_grounding_report.json`

## Current integration status

The recovery layer now participates in the real bounded desktop flow:

- planner guidance can choose `desktop_inspect_window_state`, `desktop_recover_window`, and `desktop_wait_for_window_ready`
- the desktop loop can use recovery/readiness before approval-gated click/type checkpoints
- grouped desktop failure recovery can re-inspect, recover, wait briefly, and then refresh evidence once when needed
- compact recovery/readiness diagnostics are exposed through state and local API snapshots

## Terminal desktop outcomes

The bounded desktop stack now stops explicitly when it already knows continued silent progress is not appropriate.

Current terminal or near-terminal desktop outcomes include:

- `completed`
- `approval_needed`
- `blocked`
- `incomplete`
- `unrecoverable_missing_target`
- `unrecoverable_tray_background`
- `unrecoverable_withdrawn`
- `recovery_exhausted`

Preferred behavior:

- pause only for real actionable approvals
- finalize as `incomplete` when the target is missing, withdrawn, tray/background-like, or otherwise not visibly recoverable in the current bounded pass
- avoid leaving known non-actionable desktop runs stuck in `running`
- let follow-up desktop runs start from a clean queued/running lifecycle boundary after a terminal desktop outcome
- route failed desktop inspection through the same bounded recovery/finalization seam as failed focus/action steps when the recovery state already shows `needs_recovery`, `waiting`, or `missing`
- expose compact lifecycle reasons so queued -> running -> paused/terminal handoffs are debuggable across sequential runs
- when a desktop run is already paused or terminal in a grounded way, prefer a compact deterministic final reply over another long desktop-specific model round trip

This is the foundation that the next bounded primitive should build on.

## Current live-validation boundary

Recent focused live validation confirmed the improved minimized / wrong-foreground recovery path and evidence-backed screenshot capture path.

The main remaining live boundary is fully withdrawn or tray-like hidden windows. In that state, the bounded stack now tries a stricter native exact-title lookup and relaxed hidden enumeration first. If Windows still only surfaces a withdrawn-like handle with no visible recoverable state, the recovery model now classifies that as `target_withdrawn` and stops with a clear tray/background-style report instead of guessing.

That limitation is explicit and currently preferred over guessing or widening control.

The next narrow live blocker after these lifecycle/readiness improvements is the approval-path focus entry seam: a follow-up desktop approval run can still hang at the first `desktop_focus_window` tool attempt before it reaches a clean paused checkpoint.

## What this prepares

This recovery layer prepares the project for safer later desktop growth by making window-state failures explicit, inspectable, and recoverable before broader automation/control expansion.
