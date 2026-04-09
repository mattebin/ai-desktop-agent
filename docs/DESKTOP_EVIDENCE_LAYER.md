# Desktop Evidence Layer

This document explains the current read-only desktop evidence layer.

## Purpose

The desktop evidence layer creates a bounded, serialization-friendly evidence bundle that combines:

- active-window metadata
- visible-window observations
- bounded screenshot artifact metadata
- optional read-only UI evidence probes

It is intended to strengthen inspection, auditability, and future action grounding without widening the current desktop action scope.

The layer now also includes a compact summary/index/selection helper on top of raw bundles so recent evidence can be referenced without fetching full bundle JSON everywhere.

It also includes an on-demand artifact-viewer path so retained screenshots can be opened from selected/checkpoint/recent evidence references without turning the normal UI into a screenshot browser.

The operator now uses the same compact selected/checkpoint desktop evidence as model-facing grounding for bounded desktop investigations and desktop approval preparation.

The desktop runtime can now also take bounded automatic active-window screenshots while the local operator is running. Those automatic captures feed the same evidence store instead of a parallel cache.

Desktop recovery and readiness now sit alongside the evidence layer so the operator can tell the difference between:

- evidence is present but the window is minimized
- evidence is present but the target is not foreground
- the target exists but is hidden or cloaked
- the target exists but still looks loading or visually unstable

The evidence layer now also sits behind clearer subsystem seams:

- capture backends
- bounded window/title matching
- process/background diagnostics
- readiness and control-state probing
- scene interpretation
- evidence summarization and selection
- bounded direct-image packaging

The scene interpretation layer now uses the same selected/checkpoint/recent evidence path to produce compact scene/app/workflow summaries instead of inventing a second desktop memory system.

## Current evidence sources

- bounded window observation via the desktop window backend
- bounded screenshot capture via the screenshot backend
- optional read-only UI evidence via the UI evidence backend (enriched with per-control enabled/visible/rect/states metadata)
- bounded title/process/class matching via the desktop matching subsystem
- optional OCR text extraction from captured screenshots via winocr

## Evidence bundle shape

Each evidence bundle includes:

- evidence bundle ID
- timestamp
- active-window descriptor
- visible-window descriptors
- screen and monitor metadata where available
- screenshot metadata and retained artifact path
- optional UI evidence entries
- backend/source metadata
- stable reason codes and bounded error fields

## Artifact retention

Artifacts are retained under:

- `data/desktop_evidence/index.json`
- `data/desktop_evidence/bundles/`
- `data/desktop_evidence/captures/`

Retention is bounded by `max_desktop_evidence_entries` in `config/settings.yaml`. Older bundle and capture artifacts are pruned automatically.

Automatic captures are not retained with simple FIFO alone anymore:

- manual captures are preserved preferentially
- checkpoint-bound captures are preserved preferentially
- task/window change captures can be promoted as important context
- unchanged duplicate automatic frames are skipped

This keeps the retained history more useful for later context and “what happened” reconstruction.

## Automatic capture policy

The current automatic capture layer is intentionally bounded.

- it captures the active window, not the whole desktop by default
- it runs on a bounded interval while the local operator runtime is active
- it suppresses unchanged duplicate frames
- it promotes captures when task, checkpoint, or active-window context changes
- it reuses the same evidence bundle / summary / artifact / API flow as manual captures

Current settings live in `config/settings.yaml`:

- `desktop_auto_capture_enabled`
- `desktop_auto_capture_interval_seconds`
- `desktop_auto_capture_scope`
- `desktop_auto_capture_max_events`

## Capture backend path

The evidence layer remains authoritative even as capture backends change.

Current supported capture preference path:

- `auto`
- `dxcam` when installed
- `bettercam` when installed
- `mss`
- native fallback

`mss` remains a valid supported backend. Optional desktop-duplication backends are helpers, not replacements for the evidence architecture.

## OCR text extraction

Screenshot captures can now include OCR-extracted text via the Windows Runtime `winocr` backend. This is supplementary evidence, not a primary automation driver.

Current behavior:

- when winocr is available, captured screenshots are passed through `recognize_pil()` to extract visible text
- the extracted text is stored as `ocr_text` in the normalized screenshot observation
- text is bounded to 4000 characters to prevent bloat
- if winocr is unavailable, the field is empty and no error is raised

## Visual stability via perceptual hashing

Visual stability checks now use perceptual hashing (dHash via `imagehash`) instead of pixel-exact SHA1 comparison. This tolerates ClearType rendering differences, anti-aliasing variations, and minor compression artifacts that previously caused false instability reports.

## Current non-goals

- no OCR-heavy autonomous interpretation or OCR-first automation
- no broad UI automation
- no new desktop action capability
- no broad desktop autonomy or navigation
- no raw bundle/blob dumping into prompts
- no continuous full-desktop video stream

## Evidence-aware reasoning rules

The current evidence-aware reasoning integration is intentionally bounded.

- desktop investigations may answer from selected evidence when it is recent and sufficient
- desktop approval grounding uses the checkpoint-linked evidence summary when present
- desktop click preparation prefers screenshot-backed evidence
- desktop typing preparation can rely on recent focused-window evidence without requiring OCR
- stale, partial, or target-mismatched evidence should trigger one fresh observation before action preparation
- repeated identical desktop inspection should stop once current evidence is already sufficient
- direct screenshot input should only be attached when summaries are not enough and the selected evidence is screenshot-backed

## Later live validation

The desktop live-eval harness wait path was tightened so terminal waiting now follows the authoritative top-level session status and reports the last corroborating fields on timeout instead of blocking behind stale ancillary fields.

A bounded desktop evidence-grounding live pass has now validated these paths:

- sufficient screenshot-backed evidence can ground a direct desktop investigation answer
- partial desktop evidence can be refreshed into screenshot-backed evidence before an approval-gated click pause
- desktop action pauses can retain linked evidence preview and assessment in state/API

The next targeted live validation should still re-check:

- desktop-state questions answered from current evidence when no refresh is needed
- one fresh observation collected when current desktop evidence is partial, stale, or mismatched
- desktop approval requests that clearly reference the evidence they are based on
- desktop final answers that mention evidence compactly without turning into bundle dumps

## Local testing

Current deterministic local coverage checks:

- evidence bundle assembly
- partial evidence handling when some sources are missing
- artifact retention/pruning behavior
- evidence serialization
- state snapshot visibility
- local API evidence exposure
- compact summary generation and selection heuristics
- evidence sufficiency / refresh assessment
- checkpoint and selected evidence grounding in state/API
- automatic capture duplicate suppression
- important/manual evidence retention under bounded pruning
- recent desktop context summary selection
- desktop recovery classification and strategy selection
- bounded readiness / visual-stability diagnostics
- bounded fuzzy title drift matching and candidate ranking
- capture backend selection and fallback diagnostics
