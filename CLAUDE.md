# AI Desktop Operator — Claude Code Instructions

## DO NOT TOUCH
- `.codex/` directory and any files with "codex" in the name
- `docs/` directory
- `README.md` and other `.md` files (unless explicitly asked)
- API contract (endpoints, request/response formats)
- Do not remove existing features

## Git
- Do not commit, push, or run any git commands — the user handles all git operations
- At the end of a work pass, suggest a short commit message

## Testing
- Run `python -m pytest tests/ -v` after code changes to verify nothing breaks
- Do not modify eval scripts (`*_eval.py`, `smoke_test.py`) unless explicitly asked

## Project Structure
- `core/` — agent runtime, execution, state, operator intelligence
- `tools/` — browser, desktop (split into sub-modules), files, shell, email, registry
- `desktop-ui/` — Tauri 2.0 + React frontend
- `tools/desktop.py` is a facade — actual code lives in `desktop_constants.py`, `desktop_windows.py`, `desktop_observation.py`, `desktop_input.py`, `desktop_process.py`, `desktop_schemas.py`

## Code Style
- Python 3.13, type hints via `from __future__ import annotations`
- Respect existing patterns: approval-gating, evidence capture, bounded recovery
