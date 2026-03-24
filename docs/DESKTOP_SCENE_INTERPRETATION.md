# Desktop Scene Interpretation

This layer sits on top of the existing desktop evidence, selection, recovery, and bounded vision stack.

Its job is not to replace the evidence model. Its job is to interpret the current desktop scene in a compact, bounded, reusable way.

## What it produces

The scene layer emits a normalized compact scene object with fields such as:

- scene class
- app class
- workflow state
- readiness state
- presentation style
- confidence
- change / transition summary
- direct-image helpfulness
- bounded before/after preference

## Current interpreter categories

The interpreter registry is intentionally small and plugin-friendly:

- `generic`
- `app`
- `workflow`
- `change`

Current built-in interpreters cover:

- generic scene classification
- prompt/dialog detection
- lightweight app classification
- workflow/history transition interpretation
- scene-change and direct-image usefulness assessment

Future interpreters should register into one of those categories instead of patching core desktop logic directly.

## Current inputs

The scene layer can use:

- selected evidence summary
- checkpoint evidence summary
- recent evidence history
- evidence assessment
- recovery state
- readiness probe
- visual stability
- process context

This keeps scene interpretation grounded in the existing authoritative evidence/state path.

## Current uses

The current stack uses scene interpretation in:

- desktop investigation grounding
- desktop recovery/readiness reasoning
- approval checkpoint grounding
- bounded vision selection
- compact state/API exposure
- final-answer context for desktop runs

## Non-goals

This layer does not:

- replace the evidence store
- stream raw screenshots continuously into the model
- act like OCR-heavy general UI automation
- make desktop actions unbounded
- hardcode app-specific workflows into the core loop

## Future path

The next bounded capabilities should plug into this layer as app/workflow interpreters, for example:

- mail compose scene understanding
- browser auth / permission prompt understanding
- file picker / save dialog understanding
- bounded review/confirmation scene handling
