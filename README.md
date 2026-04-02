# AI Desktop Operator

Local chat-first AI desktop operator for Windows that analyzes, plans, and executes tasks with controlled actions.

## What this is

This project is not a chatbot and not a collection of scripts.

It is a structured local operator system that:
- observes the desktop, apps, and browser
- interprets what is happening
- plans multi-step actions
- executes them in a controlled and observable way
- requires approval for sensitive actions

## Key features

- local API as the main control surface
- chat-first desktop UI
- evidence-based desktop reasoning
- scene interpretation and workflow awareness
- approval-gated desktop and browser actions
- bounded desktop control primitives
- local-first architecture with controlled runtime behavior

## Why it’s different

Most automation systems rely on fragile scripts, blind execution, or broad unsafe control.

This system is built around:
- explicit state and task lifecycle
- evidence selection and approval grounding
- recovery and readiness checks
- bounded action scope
- controllable local execution

## Tech stack

- Python for core logic
- Playwright for browser automation
- Tauri + React for desktop UI
- local API control layer
- optional local infrastructure backends for scheduling, file watching, window metadata, and screenshot capture

## Project status

Actively developed. Core architecture is in place, with current work focused on reliability, grounded desktop control, and better end-to-end operator usefulness.

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
- a bounded desktop scene interpretation layer for app/workflow/readiness understanding
- evidence-aware desktop reasoning for bounded investigations and approval grounding
- a bounded desktop recovery and readiness layer for minimized, hidden, tray/background, loading, and unstable window conditions
- a per-monitor-DPI-safe desktop coordinate mapping layer from capture space to action space
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
- approval-gated mouse move / hover / click / right click / double click
- approval-gated bounded scroll
- approval-gated bounded safe key press and short key sequences
- approval-gated bounded text entry
- bounded process listing / inspection
- approval-gated owned-process start / stop
- approval-gated bounded local command execution with timeout and captured output

Not included:

- drag/drop
- arbitrary hotkeys or unrestricted macro playback
- unrestricted keyboard/mouse control
- broad kill-anything process control
- giant shell-agent behavior
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
- `RapidFuzz`
  Bounded title/candidate matching helper for window-title drift and recovery ranking.
- `dxcam` / `bettercam` (optional)
  Future-facing higher-performance screenshot backends behind the shared capture boundary.

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

Recent reliability work also added a shared coordinate-mapping layer so screenshot evidence, primary-monitor capture, window-relative targeting, and final input coordinates all use one bounded physical-pixel model. On mixed-monitor Windows setups, the runtime now prefers full-primary-screen capture, carries per-monitor DPI/scale metadata, and maps capture-relative points back into action space explicitly instead of letting each action improvise its own coordinate math.

Recent architecture work also strengthened the plug-in seams around:

- capture backend selection and fallback
- coordinate mapping and per-monitor DPI normalization
- bounded title/process/class matching
- process/background diagnostics
- readiness and control-state probing
- deterministic direct-vision packaging

Those summaries now feed compact UI/client presentation too:

- desktop approvals show linked evidence context
- active task/status surfaces show selected/checkpoint evidence previews
- secondary details surfaces can inspect recent evidence summaries without raw bundle spam
- retained evidence artifacts can be viewed on demand from those summary surfaces without auto-expanding screenshots into the main experience

Recent validation work also tightened the desktop live-eval wait path and confirmed the main screenshot-backed approval-grounding path locally plus in one bounded desktop-grounding live scenario, while leaving stale-evidence follow-up behavior as the next targeted validation slice.

## Desktop scene interpretation layer

The desktop evidence/viewing stack now also includes a bounded scene interpretation layer that can compactly classify:

- probable scene/app class
- probable workflow state
- loading / ready / blocked / unstable state
- dialog/prompt/fullscreen/background-like characteristics
- meaningful scene changes across recent evidence history

This layer plugs into:

- bounded vision selection
- desktop recovery/readiness reasoning
- approval grounding
- desktop-state answers
- final desktop context packaging

It does not replace the evidence store, and it does not introduce broad OCR-heavy automation. Its job is to make the existing evidence stack more understandable and more reusable for future bounded desktop capability.

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
See [docs/DESKTOP_EVIDENCE_ARCHITECTURE.md](docs/DESKTOP_EVIDENCE_ARCHITECTURE.md) for the current semi-final subsystem shape.

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

## Links

- Repository: https://github.com/mattebin/ai-desktop-agent

## Notes

This repository is shared publicly for demonstration and portfolio purposes.
