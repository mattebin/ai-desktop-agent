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
