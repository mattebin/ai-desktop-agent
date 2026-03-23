# Project Handoff

## One-sentence summary

This project is now a serious local chat-first AI operator framework with explicit behavior, task control, live eval-backed reliability, a strong desktop UI, and a first bounded desktop-control slice, with current priorities focused on usefulness, judgment quality, and safe capability expansion.

## Project identity

This is a local Windows 11 chat-first AI desktop operator project located at:

`C:\Users\Matte\ai-desktop-agent`

The long-term goal is not just a chatbot with tools and not just a coding assistant. The goal is a real operator that:

- feels chat-first like ChatGPT/Codex
- uses the local API as the real control surface
- can plan and execute multi-step work
- uses browser/file/operator/desktop tools safely
- pauses only when approval or policy requires it
- has explicit task state and control semantics
- stays understandable and controllable by the user
- can eventually gain bounded direct control further, but only after behavior is solid

## Current architecture shape

Core shape:

- local API is the main front door
- thin UI sits on top of the API
- OperatorController / ExecutionManager / Agent loop drive execution
- OpenAI API is used for reasoning/planning
- ToolRuntime handles tool/runtime orchestration
- state model is session-aware and task-aware
- browser ops are Playwright-only
- approval-gated safety is preserved

Desktop/UI shape:

- Tauri host
- React web frontend
- chat-first modern UI
- thin, API-backed
- separate runtime/desktop host layer from operator core

## Major completed milestones

- strong chat-first desktop UI
- explicit behavior contract in `core/operator_behavior.py`
- explicit task lifecycle/state
- explicit stop / defer / resume / retry / replace / supersession control
- live end-to-end eval coverage for core control flows
- runtime/process ownership and shutdown safety hardening
- final-answer quality and recommendation-quality hardening
- first bounded desktop-control slice integrated into the main operator stack

## Current bounded desktop-control scope

Implemented:

- `desktop_list_windows`
- `desktop_get_active_window`
- `desktop_focus_window`
- `desktop_capture_screenshot`
- approval-gated `desktop_click_point`
- approval-gated `desktop_type_text`

Not implemented:

- drag/drop
- arbitrary hotkeys
- unrestricted keyboard/mouse control
- autonomous desktop navigation loops
- broad dangerous desktop autonomy

## Current status

This project is best described as:

It is:

- a serious local chat-first operator framework
- a browser/file/operator/desktop assistant with approvals
- a desktop app with a good chat UI
- a structured operator with explicit state and lifecycle
- an eval-backed operator system

It is not yet:

- a broad autonomous desktop-control operator
- a production-hardened general computer-use agent
- a frontier system
- done

## Current bottlenecks

The main bottlenecks are now:

- operator usefulness and judgment quality
- final-answer consistency across nuanced outcomes
- recommendation quality
- realistic scenario reliability
- stronger desktop evidence before broader action expansion

## Current priorities

Highest current priorities:

1. improve operator usefulness and judgment quality
2. improve recommendation and final-answer consistency
3. improve realistic scenario performance
4. strengthen bounded desktop evidence/observation
5. preserve safety and controllability while adding capability

## Constraints to preserve

These should remain true unless there is a very strong reason otherwise:

- preserve current architecture
- preserve local API as the main control surface
- preserve thin API-backed UI
- preserve approval-gated safety model
- preserve Playwright-only browser scope
- do not do a broad rewrite
- do not widen dangerous autonomy casually
- do not add desktop control too early or too broadly
- do not turn the app into a dashboard

## Recommended next direction

The next strong direction is to improve bounded desktop evidence and UI grounding before broadening desktop actions further.
