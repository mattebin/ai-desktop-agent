# Desktop Recovery

## Purpose

Use this when a desktop task fails because the target window is in the wrong state.

## Use it when

- the wrong window is focused
- the target is minimized
- the target is hidden
- the target may be in the tray or background
- the target exists but is not foreground
- the UI looks loading or visually unstable

## Do not use it for

- unrestricted desktop navigation
- repeated blind retries
- broad input automation
- OCR-heavy interpretation

## Workflow

1. Inspect current window state first.
2. Classify the failure mode explicitly.
3. Choose one bounded recovery strategy.
4. Re-check whether recovery actually succeeded.
5. If the target is still not ready, stop and report clearly.

## Decision rules

- If minimized: restore, then verify focus.
- If hidden: show if possible, then verify focus.
- If not foreground: retry focus once in a bounded way, then verify.
- If loading or visually unstable: wait briefly, then re-inspect once.
- If not visibly present: report that it may be in the tray or background state.

## Output shape

Return:

- recovery reason
- chosen strategy
- whether recovery succeeded
- active and target window summary
- any readiness or stability caveat
