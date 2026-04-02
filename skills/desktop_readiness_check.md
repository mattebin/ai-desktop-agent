# Desktop Readiness Check

## Purpose

Use this when a desktop window exists but may still be loading or visually unstable.

## Signals

- title is present but controls are not ready
- window is visible but disabled
- UI tree is empty or incomplete
- screen content is still changing across bounded samples

## Workflow

1. Probe read-only readiness.
2. If needed, sample visual stability in a bounded way.
3. Wait briefly once.
4. Re-inspect.
5. Stop and report if the window is still not ready.

## Non-goals

- no OCR-heavy interpretation
- no looping until success
- no blind repeated focus attempts
