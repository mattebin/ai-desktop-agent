---
title: Window State Inspection
command: window-state
aliases:
  - window-check
description: Inspect the current and target desktop window state before acting.
prompt: Use the repo-local skill in `skills/window_state_inspection.md` to inspect the current and target desktop window state before any action. Return a compact target summary, active window summary, normalized reason code, and whether the state is sufficient.
tags:
  - desktop
  - windows
  - inspection
---

# Window State Inspection

## Purpose

Use this to inspect a desktop window before acting.

## Check for

- title and window id match
- active vs expected window mismatch
- minimized state
- hidden or cloaked state
- visible bounds
- loading / not ready state
- visual instability

## Important rules

- prefer read-only inspection first
- keep the result compact
- avoid assuming focus succeeded unless the OS confirms it
- distinguish not found from not ready
- if the target may be in the tray, say so explicitly

## Bounded outputs

Include:

- target summary
- active window summary
- normalized reason code
- whether the current state is sufficient
