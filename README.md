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
- a bounded read-only desktop evidence layer for screenshots, window metadata, and future UI probes
- a compact desktop evidence summary/selection layer for recent evidence lookup
- bounded automatic active-window capture into the same desktop evidence layer
- evidence-aware desktop reasoning for bounded investigations and approval grounding
- a bounded desktop recovery and readiness layer for minimized, hidden, tray/background, loading, and unstable window conditions
- live eval coverage for core and realistic workflows
- a tighter live-eval client/harness wait path for desktop-grounding validation

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

## Read-only desktop evidence layer

This project now includes a bounded read-only desktop evidence layer that assembles:

- active-window metadata
- visible-window observations
- bounded screenshot artifact references
- optional read-only UI evidence probes

Current role:

- provide a normalized, serialization-friendly desktop evidence bundle
- retain bounded evidence artifacts under `data/desktop_evidence/`
- expose recent evidence references through the authoritative local API
- support future approval grounding without adding new action capability in this pass

The evidence layer now also exposes compact recent summaries, deterministic selection helpers, and bounded evidence sufficiency assessment for the most relevant recent desktop evidence bundle.

The local runtime can now also record bounded automatic active-window captures into that same evidence store. Those frames are deduped when unchanged and promoted when task, checkpoint, or active-window context meaningfully changes, so older important screenshots can stay available as context instead of being replaced by a simple stream of duplicates.

The desktop stack now also includes bounded recovery/readiness helpers so desktop tasks can diagnose and recover from messy window states without relying on one fragile foreground-only path.

Those summaries now feed compact UI/client presentation too:

- desktop approvals show linked evidence context
- active task/status surfaces show selected/checkpoint evidence previews
- secondary details surfaces can inspect recent evidence summaries without raw bundle spam
- retained evidence artifacts can be viewed on demand from those summary surfaces without auto-expanding screenshots into the main experience

Recent validation work also tightened the desktop live-eval wait path and confirmed the main screenshot-backed approval-grounding path locally plus in one bounded desktop-grounding live scenario, while leaving stale-evidence follow-up behavior as the next targeted validation slice.

Current non-goals:

- no OCR-heavy desktop interpretation
- no broad UI automation
- no new desktop actions beyond the existing bounded set
- no broad autonomous desktop navigation or OCR-heavy desktop reasoning

## Desktop recovery layer

The bounded desktop recovery layer now supports:

- richer window-state inspection
- minimized / hidden / tray-background classification
- foreground confirmation checks
- bounded restore/show/focus recovery
- bounded readiness and visual-stability checks

See [docs/DESKTOP_RECOVERY_MODEL.md](docs/DESKTOP_RECOVERY_MODEL.md) for the current model and non-goals.

Recent integration work also wired those recovery tools into the real desktop loop and added a focused `desktop_recovery_grounding` live scenario for minimized, wrong-foreground, loading, and unstable states. The main remaining live validation limitation is fully withdrawn or tray-like hidden windows, which are still reported clearly as not visibly present instead of being force-recovered.

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
