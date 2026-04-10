# Graveyard — Removed Architecture Components

**Date:** 2026-04-10
**Reason:** The operator's execution layer was designed like enterprise banking compliance software — mandatory per-action evidence gathering, approval gates, visual verification, observation tokens, strategy selection, outcome evaluation, intelligence refresh. A simple "open file.png" took 30-90 seconds and 3+ LLM calls. Redesigned to follow the Claude Code model: LLM as direct tool-caller, tools as thin wrappers around OS functions.

---

## What Was Removed

### 1. Enterprise Execution Loop (`loop.py` — was 2,700 lines)

**Before:** Every action went through:
- `refresh_operator_intelligence_context()` — memory hints, environment awareness, outcome patterns
- `llm.plan_next_action()` — LLM call #1 (planning)
- `_execute_desktop_tool_step()` — guards, approval checks, pacing
- `_record_tool_result()` + `apply_outcome_evaluation()` — classify outcomes, record lessons
- `llm.plan_next_action()` — LLM call #2 (check if done)
- `_finalize_message()` → `llm.finalize()` — LLM call #3 (synthesize response)
- Plus: checkpoint handling, browser/desktop guard chains, recovery pipelines

**After:** ~80-line loop: fast path → plan → execute → record step → check done.

### 2. Desktop Tool Safety Pipeline

**Before:** Each desktop tool (open, click, type, press key, screenshot) was 100-270 lines:
- `_current_desktop_context()` — enumerate all windows
- `_latest_evidence_ref_for_observation()` — lookup evidence chain
- `classify_open_target()` / `choose_windows_open_strategy()` — target+strategy AI
- `_desktop()._approval_granted()` → `_pause_desktop_action()` — approval checkpoint
- `_sample_open_verification()` — multi-sample window polling (3 samples, 180ms interval)
- `_desktop()._register_observation()` — post-action observation snapshot
- `_desktop()._desktop_result()` — massive result dict with evidence/recovery/stability

**After:** 5-15 line wrappers around core OS functions (`os.startfile`, `SendInput`, `mss`).

### 3. Guard & Checkpoint System

Removed from loop per-iteration:
- `_maybe_guard_desktop_action()` — confidence/proposal scoring
- `_maybe_pause_for_browser_checkpoint()` — pre-click browser approval
- `_maybe_pause_for_desktop_action()` — pre-action desktop approval
- `_maybe_finalize_desktop_action_guard()` — terminal guard results
- `_maybe_recover_desktop_action_failure()` — automated failure recovery
- `guard_repeated_failed_action()` — generic retry guard
- `guard_repeated_failed_desktop_strategy()` — strategy family retry guard
- `guard_repeated_failed_open_family()` — open target retry guard

### 4. Observation Token & Evidence System

- Observation tokens with freshness validation (`_validate_fresh_observation`)
- Evidence bundles with screenshots, UI elements, window hierarchy
- Evidence reference chains across actions
- `capture_desktop_evidence_frame()` — full evidence capture per action

### 5. Per-Action Intelligence

- `refresh_operator_intelligence_context()` — rebuilt per iteration
- `apply_outcome_evaluation()` + `evaluate_action_outcome()` — ML-style outcome classification
- `StrategyExplorationInventory` — track which strategies tried per target
- `OperatorMemoryStore` hints — prefer/avoid patterns
- `ProblemRecordStore` lessons — failure categorization

---

## What Was Kept

- **Core Win32 backends** — `SendInput`, `_send_key_sequence`, `_send_text`, `open_path_with_association`, `mss` capture, `_enum_windows`, `_focus_window_handle_native`
- **Fast path pattern matcher** — `core/fast_path.py`
- **Task state basics** — goal, steps, status, session management
- **Execution manager** — queue, scheduling, workers, lifecycle
- **Chat session layer** — routing, conversation, goal composition
- **API + UI** — unchanged
- **Tool schemas/registry** — tool definitions for LLM
- **Command blocklist** — dangerous command detection (from lab_shell)

---

## Performance Impact

| Metric | Before | After |
|--------|--------|-------|
| "open file.png" | 30-90s | <1s |
| LLM calls per simple action | 3 | 0 (fast path) or 1 |
| LLM calls per complex action | 3-6 | 1-2 |
| Per-iteration overhead | ~5s (intelligence + evidence + evaluation) | ~0s |
| Loop code | 2,700 lines | ~80 lines |
| desktop_open_target | 270 lines | 15 lines |
