# Local Infrastructure Backends

This document explains the current offline/local-only backend integrations that support the operator runtime without widening model-facing behavior.

## Active integrations

### APScheduler

Used as the preferred local scheduling backend behind the existing queue/scheduler model.

Current role:

- schedules bounded in-process due-job callbacks
- wakes scheduled tasks more cleanly than pure time checks
- stays behind the current `ExecutionManager` scheduler flow

Current non-goals:

- no new user-facing cron UX yet
- no distributed scheduling
- no cloud services

### watchdog

Used as the preferred local filesystem watch backend behind the existing watch/trigger model.

Current role:

- watches parent directories for bounded `file_exists` / `file_changed` triggers
- produces normalized file-watch events
- helps file-based watches react faster than interval polling alone

Current non-goals:

- no uncontrolled automation loops
- no broad directory crawling

### PyWinCtl

Used as the preferred bounded desktop window backend where available.

Current role:

- window enumeration metadata
- active-window inspection
- focus-window refinement

Current non-goals:

- no broad desktop action expansion

### mss

Used as the preferred bounded screenshot backend where available.

Current role:

- consistent desktop capture
- normalized screenshot metadata

Current non-goals:

- no OCR-driven autonomy

### pywinauto

Present only as a future-facing read-only UI evidence backend scaffold.

Current role:

- optional read-only evidence probing boundary
- normalized evidence result shape

Current non-goals:

- no broad interaction engine
- no unrestricted desktop automation

## Desktop evidence layer

These backends now feed a bounded read-only desktop evidence layer.

Current role:

- combine active-window metadata, visible-window observations, screenshot metadata, and optional UI probes
- retain bounded evidence artifacts and bundle metadata under `data/desktop_evidence/`
- expose compact recent evidence references through the local API and operator state

Current non-goals:

- no OCR-heavy interpretation
- no model-facing planning changes
- no new desktop action expansion

## Fallback behavior

All new backends are optional.

If a preferred backend is unavailable:

- scheduler falls back to polling
- file-watch falls back to polling
- desktop window observation falls back to native Win32 logic
- screenshot capture falls back to native GDI capture
- UI evidence remains stubbed

## Deterministic local coverage

Current local deterministic coverage checks:

- scheduler backend status and fallback behavior
- file-watch event normalization and fallback behavior
- desktop backend status and screenshot capture plumbing
- read-only UI evidence envelope shape
- desktop evidence bundle assembly, retention, and API exposure

## Current configuration keys

Defined in `config/settings.yaml`:

- `scheduler_backend`
- `file_watch_backend`
- `desktop_window_backend`
- `desktop_screenshot_backend`
- `ui_evidence_backend`
- `desktop_evidence_root`
- `max_desktop_evidence_entries`
