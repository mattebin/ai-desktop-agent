# Desktop Evidence Architecture

This document describes the current semi-final architecture for bounded desktop evidence, viewing, and recovery.

## What stays

The project keeps its custom core architecture:

- desktop evidence bundles remain the source of truth
- evidence summaries and deterministic selection remain authoritative
- direct image grounding stays bounded and selective
- approval checkpoints stay linked to evidence
- task/runtime state stays authoritative through the local API
- bounded desktop actions remain explicit and approval-gated

This is intentional. The project is not migrating to a broad external desktop-agent framework.

## Current subsystem boundaries

### 1. Window and process observation

Primary files:

- `tools/desktop.py`
- `tools/desktop_backends.py`
- `core/desktop_matching.py`
- `core/desktop_recovery.py`

Responsibilities:

- enumerate windows
- resolve the active window
- inspect focus/visibility/minimized/hidden/withdrawn state
- probe process/background context with `psutil`
- rank candidate windows with bounded title/process/class matching

### 2. Capture backends

Primary files:

- `tools/desktop_backends.py`
- `tools/desktop.py`

Current contract:

- the capture backend is a plug-in behind the shared screenshot interface
- the evidence store remains authoritative regardless of capture backend
- `mss` remains a first-class supported backend
- optional desktop-duplication backends can plug in without rewriting the evidence stack

Current supported preference path:

- `auto`
- `dxcam` when installed
- `bettercam` when installed
- `mss`
- native fallback

The capture backend may change; the evidence model should not.

### 2a. Coordinate mapping and DPI normalization

Primary files:

- `core/desktop_mapping.py`
- `tools/desktop.py`
- `core/desktop_evidence.py`
- `core/backend_schemas.py`

Responsibilities:

- define one bounded coordinate-mapping model for:
  - full-screen capture space
  - monitor space
  - window-relative space
  - final input/action space
- preserve per-monitor DPI and scale metadata in desktop evidence and window metadata
- make pointer approvals and resumes use the exact reviewed absolute point instead of re-deriving relative coordinates later
- expose compact mapping diagnostics through desktop results, state, and local API snapshots

Current guardrails:

- full-primary-screen capture remains the reliability-first source of truth
- active-window crops are derived artifacts, not the only coordinate reference
- pointer actions remain bounded to visible desktop points and, when targeted, to the intended active window
- `capture_relative` coordinates are only trusted when the observation includes usable screenshot bounds
- the desktop runtime enables per-monitor DPI awareness so capture, window bounds, and `SetCursorPos(...)` share one physical-pixel coordinate space on mixed-resolution setups

### 3. Readiness and control-state probes

Primary file:

- `tools/desktop_backends.py`

Responsibilities:

- bounded pywinauto readiness checks
- read-only UI/control-tree evidence
- lightweight visual-stability checks

Current guardrails:

- deep pywinauto descendant walks are bounded lazily instead of materializing the full tree first
- minimized, hidden, and withdrawn-like windows can short-circuit to metadata-backed readiness results instead of attempting deeper UIA probing first
- `desktop_wait_for_window_ready` now returns early for non-waiting recovery states so recovery/finalization can take over instead of polling a state that already needs recovery

### 4. Evidence store and selection

Primary file:

- `core/desktop_evidence.py`

Responsibilities:

- bundle creation and retention
- compact summaries
- deterministic recent/checkpoint/task selection
- sufficiency assessment
- bounded direct-vision packaging

### 5. Model-facing packaging

Primary files:

- `core/state.py`
- `core/llm_client.py`
- `core/chat_sessions.py`
- `core/execution_manager.py`

Responsibilities:

- compact evidence summaries by default
- direct screenshot input only when selection logic says it is worth using
- bounded image count
- explicit approval/checkpoint grounding

### 6. Scene interpretation

Primary files:

- `core/desktop_scene.py`
- `core/state.py`
- `core/desktop_evidence.py`

Responsibilities:

- classify the probable scene/app/workflow state
- detect prompt/dialog/fullscreen/background-like characteristics
- interpret bounded loading/ready/blocked/unstable state
- compare recent evidence history for compact scene-change / transition summaries
- tell the bounded vision selector when direct image grounding is worth using

This layer is plugin-friendly by design. Future app/workflow interpreters should register into the scene registry instead of patching loop logic directly.

### 6a. Workflow-aware target proposal

Primary files:

- `core/desktop_targets.py`
- `core/state.py`
- `core/local_api.py`
- `core/local_api_events.py`

Responsibilities:

- bridge evidence + interpreted scene + recovery/readiness into a bounded ranked set of candidate next targets
- keep target proposals serialization-friendly and compact enough for state, API, UI, and model-facing context
- surface explicit proposal states such as:
  - `ready`
  - `recovery_first`
  - `blocked`
  - `approval_context`
  - `no_safe_target`
- rank conservative target kinds such as:
  - `focus_candidate`
  - `recovery_candidate`
  - bounded window/region/point/UI-area candidates
- preserve coordinate mapping only where it is actually justified by current evidence

Current guardrails:

- weak or unstable evidence should degrade to low-confidence proposals or no safe target
- target proposals do not bypass approval; they only nominate the next bounded action surface
- target proposals are advisory and inspectable, not autonomous loops
- app-specific proposal behavior should register into the proposal registry instead of patching loop logic directly

Connection to the rest of the stack:

- evidence -> scene/recovery/readiness -> target proposal -> approval/action selection
- selected/checkpoint target proposals are surfaced through task state and local API snapshots
- the model consumes compact proposal summaries as part of the same authoritative desktop state it already uses for evidence and scene reasoning

Non-goals:

- no OCR-heavy UI targeting
- no unrestricted cursor autonomy
- no replacing the evidence or scene layers with a separate planner framework

### 7. Desktop run lifecycle and terminal outcomes

Primary files:

- `core/loop.py`
- `core/state.py`
- `core/local_api.py`
- `core/local_api_events.py`
- `core/run_history.py`

Responsibilities:

- convert scene/recovery/evidence state into a bounded terminal desktop outcome when continuation is no longer appropriate
- keep approval-needed desktop checkpoints non-terminal and explicit
- expose a compact desktop run outcome through state, local API, event stream, and run history
- reset stale queued/running state cleanly when a follow-up desktop run starts after a terminal outcome
- treat sequential desktop runs as first-class lifecycle transitions instead of implicit task reuse
- surface compact lifecycle events so queued -> running -> paused/terminal handoffs are debuggable without dumping raw queue state
- short-circuit terminal desktop reply rendering to a compact grounded fallback when a paused or terminal desktop state is already authoritative enough

Current normalized desktop outcomes:

- `completed`
- `approval_needed`
- `blocked`
- `incomplete`
- `needs_refresh`
- `unrecoverable_missing_target`
- `unrecoverable_tray_background`
- `unrecoverable_withdrawn`
- `recovery_exhausted`

This is important for future bounded primitives. Step 3 primitives should plug into this outcome model instead of inventing their own retry/finalization behavior.

Sequential handoff rules:

- terminal desktop outcomes end the current run boundary; the next desktop run must start from a fresh queued/running lifecycle state
- desktop evidence, recent scene history, and approval context may carry forward when relevant
- stale `active_task_id`, stale queued task views, and stale running snapshots should not carry forward across a new run boundary

### 8. Bounded control surface

Primary files:

- `tools/desktop.py`
- `tools/desktop_backends.py`
- `core/tool_runtime.py`
- `core/state.py`
- `core/local_api.py`
- `core/local_api_events.py`
- `core/loop.py`

Responsibilities:

- expose one integrated bounded control layer instead of isolated primitives
- keep risky controls approval-gated and checkpoint-linked from day one
- surface mouse/process/command results through the same state and local API path as evidence and recovery
- make future bounded controls plug into the same approval, evidence, scene, and run-outcome model

Current bounded control suites:

- mouse:
  - move
  - hover
  - left click
  - right click
  - double click
  - bounded scroll
- keyboard:
  - safe navigation keys
  - bounded Ctrl/Shift shortcuts
  - short safe key sequences
  - bounded text entry
- process:
  - list relevant processes
  - inspect one process
  - start one owned bounded process
  - stop one owned bounded process
- command:
  - run one bounded local command with timeout and captured output

Guardrails:

- no drag/drop
- no unrestricted global hotkeys
- no freeform macro playback
- no kill-anything process control
- no giant shell-agent behavior
- no autonomous desktop navigation loops

## Bounded matching model

The matching subsystem is deliberately constrained.

It uses:

- exact title match first
- containment/inclusion match second
- bounded fuzzy match third
- optional process/class hints as small scoring inputs, not a free-for-all search

RapidFuzz is used when available to absorb small title drift such as:

- compose window suffix changes
- theme or mode labels
- small punctuation/order drift

The matcher exposes:

- score
- confidence
- match kind
- match engine
- candidate preview

This keeps candidate selection explainable and debuggable.

## Capture plugin path

The project now has an explicit capture plugin seam.

Current guidance:

- keep the evidence layer authoritative
- treat `dxcam` / `bettercam` as optional capture engines
- do not restructure the evidence system around a capture library
- preserve fallback to `mss` and then native capture

This is the preferred future path for higher-performance screenshot acquisition.

## Future plugin guidance

Future bounded desktop capabilities should plug into one of these seams:

- matching
- capture
- readiness/control-state probing
- evidence selection
- scene interpretation
- direct vision packaging
- explicit action primitives

They should not bypass the evidence store or invent a parallel desktop memory path.

## Current non-goals

- no broad unrestricted desktop control
- no broad OCR-first automation rewrite
- no broad framework migration
- no raw screenshot streaming into the model
- no parallel evidence source of truth
