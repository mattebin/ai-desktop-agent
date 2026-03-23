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

## Current evidence sources

- bounded window observation via the desktop window backend
- bounded screenshot capture via the screenshot backend
- optional read-only UI evidence via the UI evidence backend

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

## Current non-goals

- no OCR-heavy interpretation
- no broad UI automation
- no new desktop action capability
- no broad desktop autonomy or navigation
- no raw bundle/blob dumping into prompts

## Evidence-aware reasoning rules

The current evidence-aware reasoning integration is intentionally bounded.

- desktop investigations may answer from selected evidence when it is recent and sufficient
- desktop approval grounding uses the checkpoint-linked evidence summary when present
- desktop click preparation prefers screenshot-backed evidence
- desktop typing preparation can rely on recent focused-window evidence without requiring OCR
- stale, partial, or target-mismatched evidence should trigger one fresh observation before action preparation
- repeated identical desktop inspection should stop once current evidence is already sufficient

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
