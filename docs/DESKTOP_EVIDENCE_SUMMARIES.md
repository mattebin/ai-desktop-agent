# Desktop Evidence Summaries

This document explains the compact desktop evidence summary, selection, and bounded reasoning-grounding layer built on top of the read-only desktop evidence store.

## Purpose

The summary layer exists to make recent desktop evidence easier to inspect and reference without requiring raw bundle or screenshot fetches everywhere.

It provides:

- compact evidence summaries
- deterministic recent-evidence selection
- checkpoint/task-linked evidence lookup
- API/state-friendly previews for future approval and investigation grounding
- bounded evidence sufficiency / refresh assessment for desktop investigations and approvals

## Summary shape

Each summary includes compact, serialization-friendly fields such as:

- `evidence_id`
- `timestamp`
- `source_action`
- `evidence_kind`
- `reason`
- active/target window titles and process info
- screenshot presence and scope
- artifact presence
- UI evidence presence and control count
- compact window/screen summaries
- `recency_seconds`
- a compact human-readable `summary`

## Selection heuristics

Current selection is fully local-only and deterministic.

Supported bounded strategies include:

- latest evidence
- latest evidence with screenshot
- latest partial evidence
- latest full evidence
- latest evidence matching an active/target window title
- checkpoint-linked evidence by `evidence_id`
- task-linked evidence by `evidence_id` or `observation_token`

Heuristics currently prioritize:

- explicit linkage first
- title match next
- screenshot presence when the strategy asks for it
- recency as the fallback/default ordering

## Current non-goals

- no OCR-heavy interpretation
- no new desktop action capability
- no broad UI automation

## Bounded reasoning use

The current operator now uses these compact summaries in a bounded way:

- selected evidence can ground read-only desktop investigations
- checkpoint evidence can ground paused desktop approval explanations
- desktop action preparation can decide whether existing evidence is sufficient or whether one fresh observation is needed first

The current rules stay intentionally narrow:

- use compact summaries only, never raw screenshots or full bundle blobs in prompts
- prefer deterministic selection over model-side relevance scoring
- treat stale, partial, or mismatched evidence as refresh-needed
- keep screenshot-backed grounding especially important for click preparation

## UI and client presentation

Current UI/client surfaces use the existing authoritative local API fields rather than recomputing evidence relevance in the frontend.

Evidence now appears in compact form in:

- pending desktop approval surfaces via linked checkpoint evidence
- active task/status surfaces via selected evidence and checkpoint evidence previews
- secondary details surfaces via recent compact evidence summaries
- on-demand artifact viewing from selected, checkpoint, and recent evidence entries when a retained screenshot is available

Presentation rules:

- keep summaries compact and calm
- prefer selected/checkpoint evidence references over raw bundles
- keep retained artifact paths secondary
- handle missing, partial, or pruned evidence explicitly without inflating the main chat surface
- only open retained artifacts on demand; do not auto-expand screenshots into the main task surface

## Why this matters

This prepares future approval and investigation flows to refer to the best recent desktop evidence bundle explicitly and compactly, without turning the current system into a raw artifact dump.
