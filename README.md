# AI Desktop Operator

A local Windows 11 chat-first AI operator project focused on becoming a real controllable operator, not just a chatbot with tools.

## Current status

This project is now a serious local operator framework with:

- a strong chat-first desktop UI
- a local API as the main control surface
- explicit operator behavior and task lifecycle
- stop / defer / resume / retry / replace / supersession control
- approval-gated safety
- Playwright-only browser operations
- bounded desktop-control primitives
- optional local infrastructure backends for scheduling, file watching, desktop observation, and future UI evidence
- live eval coverage for core and realistic workflows

## Current architecture

Main shape:

- local API is the front door
- thin API-backed UI
- OperatorController / ExecutionManager / agent loop
- ToolRuntime for tools/runtime orchestration
- session-aware and task-aware state
- approval-gated actions
- browser scope is Playwright-only
- desktop control is intentionally bounded

## Current desktop-control scope

Implemented bounded desktop-control primitives:

- list visible windows
- get active window
- focus a specific window
- capture a bounded screenshot
- approval-gated single click
- approval-gated bounded text entry

Not included:

- drag/drop
- arbitrary hotkeys
- unrestricted keyboard/mouse control
- autonomous desktop navigation loops
- broad dangerous autonomy

## Local-only infrastructure backends

This project now includes optional local backends that strengthen offline/runtime plumbing without widening operator behavior:

- `APScheduler`
  Preferred in-process scheduler backend under the existing queue/scheduler model.
- `watchdog`
  Preferred local filesystem event backend for file-based watch triggers.
- `PyWinCtl`
  Preferred desktop window metadata/focus backend for bounded window observation.
- `mss`
  Preferred bounded desktop screenshot backend.
- `pywinauto`
  Future-facing read-only UI evidence backend scaffold only.

These integrations are intentionally narrow:

- they are optional and fallback-safe
- they do not add broad new control loops
- they do not change model-facing behavior by themselves
- they do not widen the current bounded desktop action scope

## Project philosophy

- keep chat primary
- keep operator internals secondary
- preserve the current architecture
- do not widen dangerous autonomy casually
- do not add desktop control too early or too broadly
- do not turn the app into a dashboard
- build toward a trustworthy operator, not a gimmick

## Current priorities

Highest current priorities:

1. improve operator usefulness and judgment quality
2. improve realistic scenario reliability
3. improve final-answer quality and outcome clarity
4. expand bounded desktop evidence and action grounding carefully
5. preserve safety and controllability while adding capability

## Repository structure

- `core/` - operator core logic
- `tools/` - tool implementations
- `desktop-ui/` - desktop UI
- `docs/` - architecture notes, roadmap, handoff
- `skills/` - reusable skill definitions/instructions
- `agents/` - sub-agent role definitions
- `prompts/` - canonical prompts and pass prompts

## Branching approach

- `main` = stable baseline
- feature branches = current implementation work

## Notes

- do not commit build artifacts
- keep `.gitignore` clean
- use commits and branches for each meaningful pass
