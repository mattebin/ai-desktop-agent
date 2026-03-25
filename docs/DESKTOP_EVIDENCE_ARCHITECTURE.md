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

### 3. Readiness and control-state probes

Primary file:

- `tools/desktop_backends.py`

Responsibilities:

- bounded pywinauto readiness checks
- read-only UI/control-tree evidence
- lightweight visual-stability checks

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
