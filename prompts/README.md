# Prompts

## What belongs here

This folder stores reusable prompts for Codex or other project workflows.

Examples:

- canonical implementation-pass prompts
- eval-expansion prompts
- architecture-hardening prompts
- desktop-control prompts
- prompt variants for specific bounded project goals

## What this folder is for

The goal is to keep prompts:

- reusable
- versioned
- reviewable
- easy to compare across passes

## Suggested prompt organization

Use one file per prompt when the prompt is stable enough to reuse.

Possible naming examples:

- `desktop_control_minimal.md`
- `operator_usefulness_pass.md`
- `outcome_quality_pass.md`
- `desktop_evidence_pass.md`

## Prompt writing principles

Prompts in this repo should:

- preserve current architecture
- preserve local API as main control surface
- preserve thin API-backed UI
- preserve approval-gated safety
- preserve Playwright-only browser scope for browser work
- avoid broad rewrites
- avoid dangerous autonomy expansion
- ask for coherent substantial passes instead of tiny isolated edits

## Notes

When a prompt becomes outdated after a successful pass, keep it only if it still has reference value. Otherwise replace it with the new current prompt.
