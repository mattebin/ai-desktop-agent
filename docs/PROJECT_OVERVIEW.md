# Project Overview

## What this project is

AI Desktop Operator is a local Windows 11 chat-first operator project.

The goal is not just a chatbot with tools and not just a coding assistant. The goal is a real operator that:

- feels chat-first like ChatGPT/Codex
- uses the local API as the real control surface
- can plan and execute multi-step work
- uses browser, file, and operator tools safely
- pauses only when approval or policy requires it
- stays understandable and controllable by the user
- can eventually gain bounded direct control, but only after behavior is solid

## Core architecture

Main shape:

- local API is the front door
- thin API-backed UI sits on top
- OperatorController / ExecutionManager / agent loop drive execution
- ToolRuntime handles tools/runtime orchestration
- state model is session-aware and task-aware
- browser operations are Playwright-only
- approval-gated safety is preserved

## Major components

### Core operator/backend

Main backend responsibilities include:

- task lifecycle and execution control
- session/task memory
- persistence
- queue/scheduler/watch/alerts integration
- approval-gated actions
- final-answer shaping
- structured task control semantics

Important backend areas include:

- `core/operator_behavior.py`
- `core/state.py`
- `core/loop.py`
- `core/agent.py`
- `core/execution_manager.py`
- `core/operator_controller.py`
- `core/chat_sessions.py`
- `core/local_api.py`
- `core/llm_client.py`

### UI

The desktop UI is now the main product direction.

Current shape:

- Tauri host
- React frontend
- chat-first layout
- thin API-backed UI
- desktop/runtime host separated from operator core

### Browser stack

Browser operations use Playwright only.

Implemented browser capability includes things like:

- open page
- inspect page
- click
- type
- extract text
- follow link
- workflow/checkpoint handling
- approval-aware pause/resume flows

### Desktop control

Desktop control is intentionally bounded.

Currently implemented:

- list visible windows
- get active window
- focus a specific window
- capture a bounded screenshot
- approval-gated single click
- approval-gated bounded text entry

Not implemented:

- drag/drop
- arbitrary hotkeys
- unrestricted keyboard/mouse control
- autonomous desktop navigation loops
- broad dangerous desktop autonomy

## Behavior model

The operator has an explicit behavior contract.

Operator modes include:

- normal_chat
- read_only_investigation
- workflow_execution
- approval_needed_action
- paused_waiting
- final_report

Task phases include:

- idle
- queued
- investigating
- executing
- approval_gate
- paused
- completed
- blocked
- failed
- needs_attention
- stopped
- incomplete

The operator also supports explicit control semantics such as:

- stop
- defer
- resume
- retry
- replace goal
- supersession handling

## Current strengths

The project is now strong in these areas:

- chat-first product feel
- explicit behavior and lifecycle semantics
- approval-gated safety
- realistic end-to-end eval coverage
- final-answer quality shaping
- stale-context reduction
- bounded desktop-control integration
- safer runtime/process ownership handling

## Current limits

The project is not yet:

- a broad autonomous desktop-control agent
- a production-hardened general computer-use system
- a frontier-level operator
- done

## Current priorities

Highest current priorities are:

1. improve operator usefulness and judgment quality
2. improve recommendation and final-answer consistency
3. improve realistic scenario performance
4. expand bounded desktop evidence carefully before broader action expansion
5. preserve safety and controllability while adding capability

## Constraints to preserve

These should remain true unless there is a very strong reason otherwise:

- preserve current architecture
- preserve local API as main control surface
- preserve thin API-backed UI
- preserve approval-gated safety
- preserve Playwright-only browser scope
- do not do broad rewrites
- do not widen dangerous autonomy casually
- do not add broad desktop control too early
- do not turn the product into a dashboard
