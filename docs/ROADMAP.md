# Roadmap

## Now

Current highest-priority work:

1. improve operator usefulness and judgment quality
2. improve final-answer quality and outcome consistency
3. improve recommendation quality
4. improve realistic scenario reliability
5. harden shell-lab classifier toward production integration

Recent completions:

- desktop evidence strengthened: OCR text extraction, perceptual hashing, enriched UI probes, hung detection
- operator intelligence: problem recall, strategy exploration, uncertainty surfacing
- adaptive recovery budgets per failure reason
- post-action verification via independent Win32 checks
- shell-lab classifier hardened with 133 adversarial tests and 4 bypass fixes

## Next

Likely next passes:

- shell-lab production integration: filesystem-level enforcement, network isolation, process escape detection
- better UI grounding for desktop action proposals
- richer realistic eval scenarios for mixed browser/desktop work
- continued approval-threshold tuning
- continuity and routing hardening across messy mixed-intent sessions

## Later

Reasonable later-stage work:

- carefully broaden bounded desktop primitives
- stronger semantic golden-output review
- more realistic long-horizon scenario coverage
- better reusable skills/sub-agent composition
- deeper but still controlled operator workflows

## Not now

Avoid these for now:

- broad desktop-control autonomy
- unrestricted keyboard/mouse control
- arbitrary hotkey systems
- drag/drop automation
- broad rewrites
- turning the product into a dashboard
- adding dangerous powers just because they seem impressive

## Guiding principle

Expand capability only when behavior, observability, approval handling, and final-answer quality remain clear and trustworthy.
