# Desktop Evidence Layer

This document explains the current read-only desktop evidence layer.

## Purpose

The desktop evidence layer creates a bounded, serialization-friendly evidence bundle that combines:

- active-window metadata
- visible-window observations
- bounded screenshot artifact metadata
- optional read-only UI evidence probes

It is intended to strengthen inspection, auditability, and future action grounding without widening the current desktop action scope.

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
- no model-facing planner/routing/final-answer changes in this pass

## Local testing

Current deterministic local coverage checks:

- evidence bundle assembly
- partial evidence handling when some sources are missing
- artifact retention/pruning behavior
- evidence serialization
- state snapshot visibility
- local API evidence exposure
