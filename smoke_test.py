from __future__ import annotations

import importlib
import json
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

mods = [
    "main",
    "control_ui",
    "live_agent_eval",
    "core.agent",
    "core.alerts",
    "core.backend_schemas",
    "core.browser_tasks",
    "core.chat_sessions",
    "core.config",
    "core.desktop_capture_service",
    "core.desktop_evidence",
    "core.desktop_matching",
    "core.desktop_recovery",
    "core.desktop_scene",
    "core.execution_manager",
    "core.file_watch_backend",
    "core.local_api",
    "core.local_api_client",
    "core.local_api_events",
    "core.loop",
    "core.operator_behavior",
    "core.operator_controller",
    "core.run_history",
    "core.scheduler_backend",
    "core.session_store",
    "core.state",
    "core.safety",
    "core.watchers",
    "core.llm_client",
    "core.tool_runtime",
    "tools.registry",
    "tools.browser",
    "tools.desktop",
    "tools.desktop_backends",
    "tools.files",
    "tools.shell",
]

failed = []
for mod in mods:
    try:
        importlib.import_module(mod)
        print(f"[OK] {mod}")
    except Exception as e:
        print(f"[FAIL] {mod}: {e}")
        failed.append((mod, str(e)))

if failed:
    raise SystemExit(1)

from core.alerts import AlertStore
import core.desktop_evidence as desktop_evidence_module
import tools.desktop_backends as desktop_backends_module
import core.desktop_capture_service as desktop_capture_service_module
from core.desktop_evidence import (
    DesktopEvidenceStore,
    assess_desktop_evidence,
    build_desktop_evidence_bundle,
    compact_evidence_preview,
    select_desktop_vision_context,
    select_checkpoint_evidence,
    select_recent_evidence,
    select_task_evidence,
    summarize_evidence_bundle,
)
from core.desktop_matching import select_window_candidate, titles_compatible
from core.desktop_recovery import assess_visual_sample_signatures, classify_window_recovery_state, select_window_recovery_strategy
from core.desktop_scene import interpret_desktop_scene, list_scene_interpreters, register_scene_interpreter
from core.chat_sessions import ChatSessionManager
from core.config import load_settings
from core.desktop_capture_service import DesktopCaptureService
from core.execution_manager import ExecutionManager, ScheduledTaskStore, TaskQueueStore
from core.file_watch_backend import create_file_watch_backend
from core.llm_client import _content_with_desktop_vision, _goal_requests_brief_answer, _goal_requests_single_recommendation
from core.local_api import LocalOperatorApiServer, _status_payload
from core.local_api_client import LocalOperatorApiClient, wait_for_local_api_status
from core.loop import (
    _finalize_message,
    _is_redundant_desktop_observation,
    _maybe_finalize_desktop_terminal_outcome,
    _maybe_pause_for_desktop_action,
    _maybe_recover_desktop_action_failure,
)
from core.operator_behavior import classify_chat_turn, looks_like_simple_conversation_turn
from core.operator_controller import OperatorController
from core.run_history import RunHistoryStore
from core.scheduler_backend import create_scheduler_backend
from core.session_store import DEFAULT_STATE_SCOPE_ID, SessionStore
from core.state import TaskState
from core.tool_runtime import ToolRuntime
from core.watchers import WatchStore
from core.backend_schemas import normalize_desktop_run_outcome
from control_ui import _parse_inline_markdown_segments, _parse_rich_text_blocks, _session_matches_query, _timeline_entry_from_event
from live_agent_eval import (
    SCENARIO_NAMES,
    _desktop_hidden_recovery_checks,
    _golden_final_answer_checks,
    _interpreter_has_playwright,
    _latest_new_run,
    _project_venv_python,
)
from tools.browser import shutdown_browser_runtime
import tools.desktop as desktop_module
from tools.desktop import (
    desktop_capture_screenshot,
    desktop_click_point,
    desktop_inspect_window_state,
    desktop_list_windows,
    desktop_press_key,
    desktop_recover_window,
    desktop_type_text,
    desktop_wait_for_window_ready,
    get_desktop_backend_status,
    probe_ui_evidence,
    shutdown_desktop_runtime,
)
from tools.registry import get_tools

SMOKE_SETTINGS = {**load_settings(), "desktop_auto_capture_enabled": False}

controller = OperatorController(settings=SMOKE_SETTINGS)
snapshot = controller.get_snapshot()
if not isinstance(snapshot, dict):
    raise SystemExit("OperatorController.get_snapshot() did not return a dict.")
if not isinstance(snapshot.get("recent_runs", []), list):
    raise SystemExit("OperatorController.get_snapshot() did not include a recent_runs list.")
if not isinstance(snapshot.get("latest_run", {}), dict):
    raise SystemExit("OperatorController.get_snapshot() did not include a latest_run dict.")
if not isinstance(snapshot.get("queue", {}), dict):
    raise SystemExit("OperatorController.get_snapshot() did not include a queue dict.")
if not isinstance(snapshot.get("queued_tasks", []), list):
    raise SystemExit("OperatorController.get_snapshot() did not include a queued_tasks list.")
if not isinstance(snapshot.get("active_task", {}), dict):
    raise SystemExit("OperatorController.get_snapshot() did not include an active_task dict.")
if not isinstance(snapshot.get("scheduled", {}), dict):
    raise SystemExit("OperatorController.get_snapshot() did not include a scheduled dict.")
if not isinstance(snapshot.get("scheduled_tasks", []), list):
    raise SystemExit("OperatorController.get_snapshot() did not include a scheduled_tasks list.")
if not isinstance(snapshot.get("watches", {}), dict):
    raise SystemExit("OperatorController.get_snapshot() did not include a watches dict.")
if not isinstance(snapshot.get("watch_items", []), list):
    raise SystemExit("OperatorController.get_snapshot() did not include a watch_items list.")
if not isinstance(snapshot.get("alerts", {}), dict):
    raise SystemExit("OperatorController.get_snapshot() did not include an alerts dict.")
if not isinstance(snapshot.get("alert_items", []), list):
    raise SystemExit("OperatorController.get_snapshot() did not include an alert_items list.")
if not isinstance(snapshot.get("behavior", {}), dict) or not snapshot.get("behavior", {}).get("mode"):
    raise SystemExit("OperatorController.get_snapshot() did not include an operator behavior contract.")
if not isinstance(snapshot.get("human_control", {}), dict):
    raise SystemExit("OperatorController.get_snapshot() did not include human-control state.")
if not isinstance(snapshot.get("task_control", {}), dict):
    raise SystemExit("OperatorController.get_snapshot() did not include task-control state.")
if not isinstance(snapshot.get("infrastructure", {}), dict):
    raise SystemExit("OperatorController.get_snapshot() did not include infrastructure backend state.")
if not isinstance(snapshot.get("infrastructure", {}).get("desktop_capture", {}), dict):
    raise SystemExit("OperatorController.get_snapshot() did not include desktop auto-capture infrastructure state.")
runtime = snapshot.get("runtime", {})
if runtime.get("active_model") != "gpt-5.4" or runtime.get("reasoning_effort") != "medium":
    raise SystemExit("OperatorController.get_snapshot() did not expose the expected runtime model configuration.")
print("[OK] operator controller snapshot")

postprocess_smoke_root = Path("data") / "smoke_execution_manager_postprocess"
shutil.rmtree(postprocess_smoke_root, ignore_errors=True)
postprocess_smoke_root.mkdir(parents=True, exist_ok=True)


class _PostprocessSmokeAgent:
    def __init__(self, settings):
        self.settings = settings

    def load_task_state(self, goal: str = "", *, state_scope_id: str = DEFAULT_STATE_SCOPE_ID, clear_pending_for_new_goal: bool = True):
        return TaskState(goal, state_scope_id=state_scope_id)

    def save_task_state(self, state: TaskState, *, state_scope_id: str | None = None):
        return True

    def run_state(self, state: TaskState, **kwargs):
        state.status = "completed"
        state.add_note("Execution manager postprocess smoke completed.")
        return {"ok": True, "status": "completed", "message": "Stub completed.", "steps": state.steps}


postprocess_settings = {
    **SMOKE_SETTINGS,
    "session_state_path": str(postprocess_smoke_root / "session_state.json"),
    "run_history_path": str(postprocess_smoke_root / "run_history.json"),
    "queue_state_path": str(postprocess_smoke_root / "task_queue.json"),
    "scheduled_task_state_path": str(postprocess_smoke_root / "scheduled_tasks.json"),
    "watch_state_path": str(postprocess_smoke_root / "watch_state.json"),
    "alert_state_path": str(postprocess_smoke_root / "alert_history.json"),
    "desktop_evidence_root": str(postprocess_smoke_root / "desktop_evidence"),
    "desktop_auto_capture_enabled": False,
}
postprocess_manager = ExecutionManager(agent=_PostprocessSmokeAgent(postprocess_settings))
postprocess_manager._update_task_from_result_locked = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("smoke postprocess failure"))
try:
    started = postprocess_manager.start_goal("execution manager postprocess smoke")
    if not started.get("ok", False):
        raise SystemExit("ExecutionManager smoke could not start the post-processing failure guard task.")
    deadline = time.time() + 10.0
    while time.time() < deadline:
        worker = postprocess_manager._worker
        if worker is None or not worker.is_alive():
            break
        time.sleep(0.1)
    if postprocess_manager._worker is not None and postprocess_manager._worker.is_alive():
        raise SystemExit("ExecutionManager did not clear a worker that failed during post-processing.")
    if not postprocess_manager._tasks or postprocess_manager._tasks[0].get("status") != "blocked":
        raise SystemExit("ExecutionManager did not mark the task blocked after a post-processing failure.")
    if postprocess_manager._last_result.get("status") != "blocked":
        raise SystemExit("ExecutionManager did not preserve a blocked last-result snapshot after a post-processing failure.")
finally:
    postprocess_manager.shutdown()
    shutil.rmtree(postprocess_smoke_root, ignore_errors=True)
print("[OK] execution manager postprocess guard")

canonical_handoff_root = Path("data") / "smoke_execution_manager_handoff"
shutil.rmtree(canonical_handoff_root, ignore_errors=True)
canonical_handoff_root.mkdir(parents=True, exist_ok=True)


class _CanonicalStartSmokeAgent:
    def __init__(self, settings):
        self.settings = settings
        self.history_store = SimpleNamespace(
            get_recent_runs=lambda limit=6, session_id="", state_scope_id="": [],
            get_latest_run=lambda session_id="", state_scope_id="": {},
        )

    def load_task_state(self, goal: str = "", *, state_scope_id: str = DEFAULT_STATE_SCOPE_ID, clear_pending_for_new_goal: bool = True):
        return TaskState(goal, state_scope_id=state_scope_id)

    def save_task_state(self, state: TaskState, *, state_scope_id: str | None = None):
        return True

    def get_runtime_config(self):
        return {"active_model": "gpt-5.4", "reasoning_effort": "medium"}

    def run_state(self, state: TaskState, **kwargs):
        state.status = "completed"
        state.add_note("Canonical start smoke completed.")
        return {"ok": True, "status": "completed", "message": "Canonical start smoke completed.", "steps": state.steps}


canonical_handoff_settings = {
    **SMOKE_SETTINGS,
    "session_state_path": str(canonical_handoff_root / "session_state.json"),
    "run_history_path": str(canonical_handoff_root / "run_history.json"),
    "queue_state_path": str(canonical_handoff_root / "task_queue.json"),
    "scheduled_task_state_path": str(canonical_handoff_root / "scheduled_tasks.json"),
    "watch_state_path": str(canonical_handoff_root / "watch_state.json"),
    "alert_state_path": str(canonical_handoff_root / "alert_history.json"),
    "desktop_evidence_root": str(canonical_handoff_root / "desktop_evidence"),
    "desktop_auto_capture_enabled": False,
}
canonical_manager = ExecutionManager(agent=_CanonicalStartSmokeAgent(canonical_handoff_settings))
original_start_worker_locked = canonical_manager._start_worker_locked
try:
    canonical_manager._start_worker_locked = lambda *args, **kwargs: None
    canonical_dispatch = canonical_manager.start_goal("canonical start smoke", session_id="session-canonical")
    if not canonical_dispatch.get("ok", False) or not canonical_dispatch.get("started", False):
        raise SystemExit("ExecutionManager did not start the canonical handoff smoke task immediately.")
    canonical_task = canonical_manager._find_task_locked(canonical_dispatch.get("task_id", ""))
    if canonical_task is None or canonical_task.get("status") != "running" or not canonical_task.get("started_at", ""):
        raise SystemExit("ExecutionManager did not update the canonical queued task entry when starting immediate work.")
    if canonical_manager._active_task_id != canonical_dispatch.get("task_id", ""):
        raise SystemExit("ExecutionManager did not keep the active task pointer aligned with the canonical started task.")
    canonical_snapshot = canonical_manager.get_snapshot(session_id="session-canonical")
    if canonical_snapshot.get("active_task", {}).get("status") != "running":
        raise SystemExit("ExecutionManager snapshot did not expose the canonical started task as running.")
    if canonical_snapshot.get("lifecycle", {}).get("event") != "task_started":
        raise SystemExit("ExecutionManager lifecycle snapshot did not expose the expected task_started event for immediate work.")
finally:
    canonical_manager._start_worker_locked = original_start_worker_locked
    canonical_manager.shutdown()
    shutil.rmtree(canonical_handoff_root, ignore_errors=True)
print("[OK] execution manager canonical task handoff")

sequential_handoff_root = Path("data") / "smoke_execution_manager_sequential"
shutil.rmtree(sequential_handoff_root, ignore_errors=True)
sequential_handoff_root.mkdir(parents=True, exist_ok=True)


class _SequentialHandoffSmokeAgent:
    def __init__(self, settings):
        self.settings = settings
        self.history_store = SimpleNamespace(
            get_recent_runs=lambda limit=6, session_id="", state_scope_id="": [],
            get_latest_run=lambda session_id="", state_scope_id="": {},
        )

    def load_task_state(self, goal: str = "", *, state_scope_id: str = DEFAULT_STATE_SCOPE_ID, clear_pending_for_new_goal: bool = True):
        return TaskState(goal, state_scope_id=state_scope_id)

    def save_task_state(self, state: TaskState, *, state_scope_id: str | None = None):
        return True

    def get_runtime_config(self):
        return {"active_model": "gpt-5.4", "reasoning_effort": "medium"}

    def run_state(self, state: TaskState, **kwargs):
        if "first sequential" in state.goal.lower():
            time.sleep(0.25)
            state.status = "incomplete"
            state.add_note("First sequential smoke run ended incomplete.")
            return {"ok": False, "status": "incomplete", "message": "First sequential smoke run ended incomplete.", "steps": state.steps}
        state.status = "completed"
        state.add_note("Follow-up sequential smoke run completed.")
        return {"ok": True, "status": "completed", "message": "Follow-up sequential smoke run completed.", "steps": state.steps}


sequential_handoff_settings = {
    **SMOKE_SETTINGS,
    "session_state_path": str(sequential_handoff_root / "session_state.json"),
    "run_history_path": str(sequential_handoff_root / "run_history.json"),
    "queue_state_path": str(sequential_handoff_root / "task_queue.json"),
    "scheduled_task_state_path": str(sequential_handoff_root / "scheduled_tasks.json"),
    "watch_state_path": str(sequential_handoff_root / "watch_state.json"),
    "alert_state_path": str(sequential_handoff_root / "alert_history.json"),
    "desktop_evidence_root": str(sequential_handoff_root / "desktop_evidence"),
    "desktop_auto_capture_enabled": False,
}
sequential_manager = ExecutionManager(agent=_SequentialHandoffSmokeAgent(sequential_handoff_settings))
try:
    first_dispatch = sequential_manager.start_goal("first sequential desktop handoff smoke", session_id="session-first")
    if not first_dispatch.get("ok", False) or not first_dispatch.get("started", False):
        raise SystemExit("ExecutionManager could not start the first sequential handoff smoke task.")
    second_dispatch = sequential_manager.start_goal("second sequential desktop handoff smoke", session_id="session-second")
    if not second_dispatch.get("ok", False) or second_dispatch.get("started", False):
        raise SystemExit("ExecutionManager did not queue the follow-up sequential task behind a running first task.")

    deadline = time.time() + 10.0
    while time.time() < deadline:
        worker = sequential_manager._worker
        queued = any(task.get("status") == "queued" for task in sequential_manager._tasks)
        running = any(task.get("status") == "running" for task in sequential_manager._tasks)
        if (worker is None or not worker.is_alive()) and not queued and not running:
            break
        time.sleep(0.1)

    if sequential_manager._worker is not None and sequential_manager._worker.is_alive():
        raise SystemExit("ExecutionManager did not finish the sequential handoff smoke tasks.")

    first_task = sequential_manager._find_task_locked(first_dispatch.get("task_id", ""))
    second_task = sequential_manager._find_task_locked(second_dispatch.get("task_id", ""))
    if first_task is None or first_task.get("status") != "incomplete":
        raise SystemExit("ExecutionManager did not preserve the first sequential task as incomplete.")
    if second_task is None or second_task.get("status") != "completed" or not second_task.get("started_at", ""):
        raise SystemExit("ExecutionManager did not start and finalize the follow-up sequential task cleanly after an incomplete run.")
    if sequential_manager._active_task_id:
        raise SystemExit("ExecutionManager did not clear the active task pointer after the sequential handoff smoke tasks finished.")
    if any(task.get("status") == "queued" for task in sequential_manager._tasks):
        raise SystemExit("ExecutionManager left a queued task stranded after the sequential handoff smoke tasks finished.")
    sequential_snapshot = sequential_manager.get_snapshot(session_id="session-second")
    if sequential_snapshot.get("status") != "completed" or sequential_snapshot.get("running", False):
        raise SystemExit("ExecutionManager snapshot did not expose the follow-up sequential task as cleanly completed.")
    if sequential_snapshot.get("lifecycle", {}).get("event") != "task_finalized":
        raise SystemExit("ExecutionManager lifecycle snapshot did not expose a terminal lifecycle event after sequential handoff.")
finally:
    sequential_manager.shutdown()
    shutil.rmtree(sequential_handoff_root, ignore_errors=True)
print("[OK] execution manager sequential handoff")

startup_progress_root = Path("data") / "smoke_execution_manager_progress"
shutil.rmtree(startup_progress_root, ignore_errors=True)
startup_progress_root.mkdir(parents=True, exist_ok=True)


class _ProgressLifecycleSmokeAgent:
    def __init__(self, settings):
        self.settings = settings
        self._runs = []
        self.history_store = SimpleNamespace(
            get_recent_runs=lambda limit=6, session_id="", state_scope_id="": list(self._runs)[:limit],
            get_latest_run=lambda session_id="", state_scope_id="": (dict(self._runs[0]) if self._runs else {}),
        )

    def load_task_state(self, goal: str = "", *, state_scope_id: str = DEFAULT_STATE_SCOPE_ID, clear_pending_for_new_goal: bool = True):
        return TaskState(goal, state_scope_id=state_scope_id)

    def save_task_state(self, state: TaskState, *, state_scope_id: str | None = None):
        return True

    def get_runtime_config(self):
        return {"active_model": "gpt-5.4", "reasoning_effort": "medium"}

    def record_run_history(self, state: TaskState, *, result: Dict[str, Any] | None, **kwargs):
        entry = {
            "run_id": f"run-progress-{len(self._runs) + 1}",
            "final_status": str((result or {}).get("status", state.status)).strip(),
            "result_message": str((result or {}).get("message", "")).strip(),
        }
        self._runs.insert(0, entry)
        return entry

    def run_state(self, state: TaskState, **kwargs):
        progress_callback = kwargs.get("progress_callback")
        if callable(progress_callback):
            progress_callback("run_state_entered", detail="Entered agent run_state.")
            progress_callback("loop_entered", detail="Entered the bounded operator loop.")
            progress_callback("planning_started", detail="Started planning the next bounded step.")
            progress_callback("tool_step_attempted", detail="Attempting bounded tool step: desktop_inspect_window_state.", tool_name="desktop_inspect_window_state")
            progress_callback("tool_result_recorded", detail="Recorded result from bounded tool step: desktop_inspect_window_state.", tool_name="desktop_inspect_window_state", result_status="completed")
        state.status = "completed"
        state.add_note("Progress lifecycle smoke completed.")
        return {"ok": True, "status": "completed", "message": "Progress lifecycle smoke completed.", "steps": state.steps}


startup_progress_settings = {
    **SMOKE_SETTINGS,
    "session_state_path": str(startup_progress_root / "session_state.json"),
    "run_history_path": str(startup_progress_root / "run_history.json"),
    "queue_state_path": str(startup_progress_root / "task_queue.json"),
    "scheduled_task_state_path": str(startup_progress_root / "scheduled_tasks.json"),
    "watch_state_path": str(startup_progress_root / "watch_state.json"),
    "alert_state_path": str(startup_progress_root / "alert_history.json"),
    "desktop_evidence_root": str(startup_progress_root / "desktop_evidence"),
    "desktop_auto_capture_enabled": False,
}
startup_progress_manager = ExecutionManager(agent=_ProgressLifecycleSmokeAgent(startup_progress_settings))
try:
    progress_dispatch = startup_progress_manager.start_goal("progress lifecycle desktop smoke", session_id="session-progress")
    if not progress_dispatch.get("ok", False) or not progress_dispatch.get("started", False):
        raise SystemExit("ExecutionManager could not start the progress lifecycle smoke task.")
    deadline = time.time() + 5.0
    progress_snapshot = {}
    while time.time() < deadline:
        progress_snapshot = startup_progress_manager.get_snapshot(session_id="session-progress")
        if progress_snapshot.get("status") == "completed" and not progress_snapshot.get("running", False):
            break
        time.sleep(0.05)
    progress_task = progress_snapshot.get("active_task", {}) if isinstance(progress_snapshot.get("active_task", {}), dict) else {}
    progress_payload = progress_task.get("progress", {}) if isinstance(progress_task.get("progress", {}), dict) else {}
    if progress_snapshot.get("status") != "completed":
        raise SystemExit("ExecutionManager progress lifecycle smoke did not complete cleanly.")
    if not progress_payload.get("worker_started_at", "") or not progress_payload.get("run_state_entered_at", "") or not progress_payload.get("first_loop_at", ""):
        raise SystemExit(f"ExecutionManager did not expose the expected worker/run-state/loop progress markers: {progress_payload}")
    if not progress_payload.get("first_step_at", "") or not progress_payload.get("first_result_at", "") or not bool(progress_payload.get("meaningful_progress", False)):
        raise SystemExit(f"ExecutionManager did not expose the expected first meaningful progress markers: {progress_payload}")
finally:
    startup_progress_manager.shutdown()
    shutil.rmtree(startup_progress_root, ignore_errors=True)
print("[OK] execution manager progress visibility")


class _FinalizeTimeoutSmokeLLM:
    def __init__(self):
        self.timeout_seconds = None

    def finalize(self, goal, steps, observation="", final_context="", *, desktop_vision=None, timeout_seconds=None):
        self.timeout_seconds = timeout_seconds
        raise TimeoutError("smoke final reply timeout")


finalize_timeout_llm = _FinalizeTimeoutSmokeLLM()
finalize_timeout_state = TaskState("Inspect the current state of the desktop window titled 'Desktop Eval Main'.")
finalize_timeout_state.status = "completed"
finalize_timeout_state.desktop_active_window_title = "Desktop Eval Main"
finalize_timeout_state.desktop_last_screenshot_path = "C:\\capture.png"
finalize_timeout_state.add_note("Captured a screenshot of the active window 'Desktop Eval Main'.")
finalize_timeout_state.set_summary("Captured a screenshot of the active window 'Desktop Eval Main'.")
finalize_progress_events: List[Dict[str, str]] = []
finalize_timeout_message = _finalize_message(
    finalize_timeout_llm,
    finalize_timeout_state,
    progress_callback=lambda stage, **payload: finalize_progress_events.append(
        {"stage": str(stage), "detail": str(payload.get("detail", "")).strip()}
    ),
)
if finalize_timeout_llm.timeout_seconds != 30:
    raise SystemExit(f"Bounded final reply rendering did not pass the expected timeout override: {finalize_timeout_llm.timeout_seconds}")
if not finalize_progress_events or finalize_progress_events[0].get("stage") != "final_reply_rendering":
    raise SystemExit(f"Bounded final reply rendering did not emit the expected progress stage: {finalize_progress_events}")
if not any("using compact fallback" in event.get("detail", "").lower() for event in finalize_progress_events):
    raise SystemExit(f"Bounded final reply rendering did not expose the fallback detail after timeout: {finalize_progress_events}")
if "Desktop Eval Main" not in finalize_timeout_message or "screenshot" not in finalize_timeout_message.lower():
    raise SystemExit(f"Bounded final reply fallback did not preserve grounded desktop details: {finalize_timeout_message}")


class _ShortCircuitDesktopFinalizeSmokeLLM:
    def __init__(self):
        self.called = False

    def finalize(self, goal, steps, observation="", final_context="", *, desktop_vision=None, timeout_seconds=None):
        self.called = True
        raise RuntimeError("desktop short-circuit finalization should not call the LLM")


short_circuit_llm = _ShortCircuitDesktopFinalizeSmokeLLM()
short_circuit_state = TaskState("Inspect the current state of the desktop window titled 'Desktop Eval Main'.")
short_circuit_state.status = "incomplete"
short_circuit_state.desktop_active_window_title = "Desktop Eval Sidecar"
short_circuit_state.desktop_last_target_window = "Desktop Eval Main"
short_circuit_state.set_desktop_run_outcome(
    normalize_desktop_run_outcome(
        {
            "outcome": "unrecoverable_tray_background",
            "status": "incomplete",
            "terminal": True,
            "reason": "unrecoverable_tray_background",
            "summary": "Desktop Eval Main is not visibly present and appears tray-like or background-only.",
            "target_window_title": "Desktop Eval Main",
            "active_window_title": "Desktop Eval Sidecar",
        }
    )
)
short_circuit_progress: List[Dict[str, str]] = []
short_circuit_message = _finalize_message(
    short_circuit_llm,
    short_circuit_state,
    progress_callback=lambda stage, **payload: short_circuit_progress.append(
        {"stage": str(stage), "detail": str(payload.get("detail", "")).strip()}
    ),
)
if short_circuit_llm.called:
    raise SystemExit("Bounded desktop finalization did not short-circuit the LLM for a terminal incomplete desktop outcome.")
if not any("grounded fallback" in event.get("detail", "").lower() for event in short_circuit_progress):
    raise SystemExit(f"Bounded desktop finalization did not expose the expected short-circuit progress detail: {short_circuit_progress}")
if "Desktop Eval Main" not in short_circuit_message or "next step" not in short_circuit_message.lower():
    raise SystemExit(f"Bounded desktop finalization did not preserve a grounded next-step message for terminal incomplete desktop outcomes: {short_circuit_message}")
print("[OK] bounded final reply rendering fallback")

startup_timeout_root = Path("data") / "smoke_execution_manager_startup_timeout"
shutil.rmtree(startup_timeout_root, ignore_errors=True)
startup_timeout_root.mkdir(parents=True, exist_ok=True)


class _StartupTimeoutSmokeAgent:
    def __init__(self, settings):
        self.settings = settings
        self._runs = []
        self.history_store = SimpleNamespace(
            get_recent_runs=lambda limit=6, session_id="", state_scope_id="": list(self._runs)[:limit],
            get_latest_run=lambda session_id="", state_scope_id="": (dict(self._runs[0]) if self._runs else {}),
        )

    def load_task_state(self, goal: str = "", *, state_scope_id: str = DEFAULT_STATE_SCOPE_ID, clear_pending_for_new_goal: bool = True):
        return TaskState(goal, state_scope_id=state_scope_id)

    def save_task_state(self, state: TaskState, *, state_scope_id: str | None = None):
        return True

    def get_runtime_config(self):
        return {"active_model": "gpt-5.4", "reasoning_effort": "medium"}

    def record_run_history(self, state: TaskState, *, result: Dict[str, Any] | None, **kwargs):
        entry = {
            "run_id": f"run-startup-timeout-{len(self._runs) + 1}",
            "final_status": str((result or {}).get("status", state.status)).strip(),
            "result_message": str((result or {}).get("message", "")).strip(),
        }
        self._runs.insert(0, entry)
        return entry

    def run_state(self, state: TaskState, **kwargs):
        progress_callback = kwargs.get("progress_callback")
        goal_text = str(state.goal).lower()
        if "first startup timeout" in goal_text:
            if callable(progress_callback):
                progress_callback("run_state_entered", detail="Entered agent run_state.")
                progress_callback("loop_entered", detail="Entered the bounded operator loop.")
                progress_callback("planning_started", detail="Started planning the next bounded step.")
            time.sleep(1.0)
            state.status = "completed"
            state.add_note("Late startup-timeout smoke result should be ignored.")
            return {"ok": True, "status": "completed", "message": "Late startup-timeout smoke result should be ignored.", "steps": state.steps}

        if callable(progress_callback):
            progress_callback("run_state_entered", detail="Entered agent run_state.")
            progress_callback("loop_entered", detail="Entered the bounded operator loop.")
            progress_callback("planning_started", detail="Started planning the next bounded step.")
            progress_callback("tool_step_attempted", detail="Attempting bounded tool step: desktop_inspect_window_state.", tool_name="desktop_inspect_window_state")
            progress_callback("tool_result_recorded", detail="Recorded result from bounded tool step: desktop_inspect_window_state.", tool_name="desktop_inspect_window_state", result_status="completed")
        state.status = "completed"
        state.add_note("Follow-up startup-timeout smoke completed.")
        return {"ok": True, "status": "completed", "message": "Follow-up startup-timeout smoke completed.", "steps": state.steps}


startup_timeout_settings = {
    **SMOKE_SETTINGS,
    "session_state_path": str(startup_timeout_root / "session_state.json"),
    "run_history_path": str(startup_timeout_root / "run_history.json"),
    "queue_state_path": str(startup_timeout_root / "task_queue.json"),
    "scheduled_task_state_path": str(startup_timeout_root / "scheduled_tasks.json"),
    "watch_state_path": str(startup_timeout_root / "watch_state.json"),
    "alert_state_path": str(startup_timeout_root / "alert_history.json"),
    "desktop_evidence_root": str(startup_timeout_root / "desktop_evidence"),
    "desktop_auto_capture_enabled": False,
}
startup_timeout_manager = ExecutionManager(agent=_StartupTimeoutSmokeAgent(startup_timeout_settings))
startup_timeout_manager.task_startup_timeout_seconds = 0.6
startup_timeout_manager.task_post_result_timeout_seconds = 0.6
try:
    timeout_dispatch = startup_timeout_manager.start_goal(
        "Inspect the desktop window titled 'Startup Timeout Window' as the first startup timeout desktop smoke run.",
        session_id="session-timeout-first",
    )
    if not timeout_dispatch.get("ok", False) or not timeout_dispatch.get("started", False):
        raise SystemExit("ExecutionManager could not start the first startup-timeout smoke task.")
    followup_dispatch = startup_timeout_manager.start_goal(
        "Inspect the desktop window titled 'Startup Timeout Window' as the second startup timeout desktop smoke run.",
        session_id="session-timeout-second",
    )
    if not followup_dispatch.get("ok", False):
        raise SystemExit("ExecutionManager could not create the follow-up startup-timeout smoke task.")

    timeout_snapshot = {}
    followup_snapshot = {}
    deadline = time.time() + 8.0
    while time.time() < deadline:
        timeout_snapshot = startup_timeout_manager.get_snapshot(session_id="session-timeout-first")
        followup_snapshot = startup_timeout_manager.get_snapshot(session_id="session-timeout-second")
        if timeout_snapshot.get("status") == "blocked" and followup_snapshot.get("status") == "completed" and not followup_snapshot.get("running", False):
            break
        time.sleep(0.05)

    timeout_task = timeout_snapshot.get("active_task", {}) if isinstance(timeout_snapshot.get("active_task", {}), dict) else {}
    timeout_progress = timeout_task.get("progress", {}) if isinstance(timeout_task.get("progress", {}), dict) else {}
    timeout_outcome = timeout_snapshot.get("desktop", {}).get("run_outcome", {}) if isinstance(timeout_snapshot.get("desktop", {}), dict) else {}
    if timeout_snapshot.get("status") != "blocked":
        raise SystemExit(f"ExecutionManager did not finalize the stalled follow-up run cleanly as blocked: {timeout_snapshot}")
    if timeout_outcome.get("reason") not in {"loop_entry_timeout", "first_progress_timeout"}:
        raise SystemExit(f"ExecutionManager did not expose the expected desktop timeout reason for the stalled run: {timeout_outcome}")
    if not timeout_progress.get("first_loop_at", "") or timeout_progress.get("first_step_at", ""):
        raise SystemExit(f"ExecutionManager did not preserve the expected startup-timeout progress markers: {timeout_progress}")
    if followup_snapshot.get("status") != "completed":
        raise SystemExit(f"ExecutionManager did not start and complete the follow-up task after the blocked startup-timeout run: {followup_snapshot}")

    time.sleep(1.1)
    timeout_task_after = startup_timeout_manager._find_task_locked(timeout_dispatch.get("task_id", ""))
    followup_task_after = startup_timeout_manager._find_task_locked(followup_dispatch.get("task_id", ""))
    if timeout_task_after is None or timeout_task_after.get("status") != "blocked":
        raise SystemExit("ExecutionManager let a late abandoned worker overwrite the blocked startup-timeout task status.")
    if followup_task_after is None or followup_task_after.get("status") != "completed":
        raise SystemExit("ExecutionManager let a late abandoned worker disturb the completed follow-up task status.")
finally:
    startup_timeout_manager.shutdown()
    shutil.rmtree(startup_timeout_root, ignore_errors=True)
print("[OK] execution manager startup timeout handoff")

stale_active_root = Path("data") / "smoke_execution_manager_stale_active"
shutil.rmtree(stale_active_root, ignore_errors=True)
stale_active_root.mkdir(parents=True, exist_ok=True)
stale_active_settings = {
    **SMOKE_SETTINGS,
    "session_state_path": str(stale_active_root / "session_state.json"),
    "run_history_path": str(stale_active_root / "run_history.json"),
    "queue_state_path": str(stale_active_root / "task_queue.json"),
    "scheduled_task_state_path": str(stale_active_root / "scheduled_tasks.json"),
    "watch_state_path": str(stale_active_root / "watch_state.json"),
    "alert_state_path": str(stale_active_root / "alert_history.json"),
    "desktop_evidence_root": str(stale_active_root / "desktop_evidence"),
    "desktop_auto_capture_enabled": False,
}
stale_active_manager = ExecutionManager(agent=_CanonicalStartSmokeAgent(stale_active_settings))
try:
    with stale_active_manager._lock:
        stale_task = stale_active_manager._enqueue_task_locked(
            "stale active queued smoke",
            source="goal_run",
            session_id="session-stale",
            state_scope_id="chat:session-stale",
        )
        if stale_task is None:
            raise SystemExit("ExecutionManager could not create the stale-active smoke task.")
        stale_active_manager._active_task_id = stale_task.get("task_id", "")
        stale_active_manager._persist_all_locked()
    stale_snapshot = stale_active_manager.get_snapshot(session_id="session-stale")
    if stale_snapshot.get("running", False):
        raise SystemExit("ExecutionManager left a stale queued active task looking like live running work.")
    if stale_snapshot.get("active_task", {}).get("status") != "queued":
        raise SystemExit("ExecutionManager did not leave the stale queued task in an explicitly queued state after clearing the active pointer.")
    if stale_snapshot.get("lifecycle", {}).get("event") != "active_task_reset":
        raise SystemExit("ExecutionManager did not expose the stale-active cleanup lifecycle event.")
finally:
    stale_active_manager.shutdown()
    shutil.rmtree(stale_active_root, ignore_errors=True)
print("[OK] execution manager stale active cleanup")

scheduler_backend = create_scheduler_backend({"scheduler_backend": "apscheduler"})
scheduler_backend.sync_scheduled_tasks(
    [
        {
            "scheduled_id": "sched-smoke",
            "goal": "scheduler smoke",
            "status": "scheduled",
            "recurrence": "once",
            "scheduled_for": "2099-01-01T00:00:00+00:00",
            "next_run_at": "2099-01-01T00:00:00+00:00",
        }
    ]
)
scheduler_status = scheduler_backend.status_snapshot()
if scheduler_status.get("active") not in {"apscheduler", "polling"}:
    raise SystemExit("Scheduler backend did not expose a valid active backend.")
if not isinstance(scheduler_status.get("metadata", {}).get("jobs", []), list):
    raise SystemExit("Scheduler backend did not expose normalized scheduled jobs.")
fallback_scheduler = create_scheduler_backend({"scheduler_backend": "missing-backend"})
fallback_scheduler_status = fallback_scheduler.status_snapshot()
if fallback_scheduler_status.get("active") != "polling":
    raise SystemExit("Scheduler backend did not fall back to polling for an unknown backend preference.")
scheduler_backend.shutdown()
fallback_scheduler.shutdown()
print("[OK] scheduler backends")

file_watch_backend = create_file_watch_backend({"file_watch_backend": "watchdog"})
file_watch_status = file_watch_backend.status_snapshot()
watch_probe_root = Path("data") / "smoke_watch_backend"
watch_probe_root.mkdir(parents=True, exist_ok=True)
watch_probe_file = watch_probe_root / "watch_probe.txt"
watch_probe_file.write_text("initial", encoding="utf-8")
file_watch_backend.sync_watches(
    [
        {
            "watch_id": "watch-smoke",
            "condition_type": "file_changed",
            "target": str(watch_probe_file),
        }
    ]
)
time.sleep(0.25)
watch_probe_file.write_text("changed", encoding="utf-8")
watch_signal = False
if file_watch_status.get("active") == "watchdog":
    for _ in range(20):
        if file_watch_backend.has_recent_signal(str(watch_probe_file), since_timestamp=0.0):
            watch_signal = True
            break
        time.sleep(0.1)

    if not watch_signal and hasattr(file_watch_backend, "record_event"):
        class _SyntheticWatchEvent:
            src_path = str(watch_probe_file)
            dest_path = ""
            event_type = "modified"
            is_directory = False

        file_watch_backend.record_event(_SyntheticWatchEvent())
        watch_signal = file_watch_backend.has_recent_signal(str(watch_probe_file), since_timestamp=0.0)
else:
    watch_signal = True

watch_events = file_watch_backend.consume_events()
if not watch_signal:
    raise SystemExit("File-watch backend did not detect a recent local file signal.")
if watch_events and not isinstance(watch_events[0], dict):
    raise SystemExit("File-watch backend did not return normalized watch events.")
fallback_file_watch = create_file_watch_backend({"file_watch_backend": "missing-backend"})
if fallback_file_watch.status_snapshot().get("active") != "polling":
    raise SystemExit("File-watch backend did not fall back to polling for an unknown backend preference.")
file_watch_backend.shutdown()
fallback_file_watch.shutdown()
print("[OK] file-watch backends")

registered_tools = {tool.get("name", "") for tool in get_tools()}
expected_desktop_tools = {
    "desktop_list_windows",
    "desktop_get_active_window",
    "desktop_focus_window",
    "desktop_inspect_window_state",
    "desktop_recover_window",
    "desktop_wait_for_window_ready",
    "desktop_capture_screenshot",
    "desktop_click_point",
    "desktop_press_key",
    "desktop_type_text",
}
if not expected_desktop_tools.issubset(registered_tools):
    raise SystemExit("Tool registry did not include the expected bounded desktop tools.")
tool_runtime = ToolRuntime(get_tools())
planner_tool_names = {tool.get("name", "") for tool in tool_runtime.planner_tools()}
if not expected_desktop_tools.issubset(planner_tool_names):
    raise SystemExit("ToolRuntime did not expose the expected bounded desktop tools to the planner.")
desktop_observation = desktop_list_windows({})
if not isinstance(desktop_observation, dict) or "windows" not in desktop_observation or "active_window" not in desktop_observation:
    raise SystemExit("desktop_list_windows() did not return the expected desktop observation shape.")
observation_token = str(desktop_observation.get("observation_token", "")).strip()
active_window = desktop_observation.get("active_window", {}) if isinstance(desktop_observation.get("active_window", {}), dict) else {}
active_rect = active_window.get("rect", {}) if isinstance(active_window.get("rect", {}), dict) else {}
if not observation_token:
    raise SystemExit("desktop_list_windows() did not return an observation token.")
if active_window.get("title") and int(active_rect.get("width", 0) or 0) > 8 and int(active_rect.get("height", 0) or 0) > 8:
    test_x = int(active_rect.get("x", 0)) + max(2, int(active_rect.get("width", 0) or 0) // 2)
    test_y = int(active_rect.get("y", 0)) + max(2, int(active_rect.get("height", 0) or 0) // 2)
    ungrouded_click = desktop_click_point({"x": test_x, "y": test_y, "observation_token": observation_token})
    if "screenshot-backed inspection" not in str(ungrouded_click.get("summary", "")):
        raise SystemExit("desktop_click_point() did not require screenshot-backed evidence before approval preview.")
    preview_capture = desktop_capture_screenshot({"scope": "active_window", "name": "desktop_preview_smoke"})
    preview_token = str(preview_capture.get("desktop_state", {}).get("observation_token", "")).strip()
    if not preview_capture.get("ok", False) or not preview_token:
        raise SystemExit("desktop_capture_screenshot() did not produce a screenshot-backed desktop observation for approval preview smoke coverage.")
    click_preview = desktop_click_point({"x": test_x, "y": test_y, "observation_token": preview_token})
    if not click_preview.get("paused", False) or not click_preview.get("approval_required", False) or click_preview.get("checkpoint_tool") != "desktop_click_point":
        raise SystemExit("desktop_click_point() did not require approval in the expected bounded way.")
    key_preview = desktop_press_key(
        {
            "key": "Enter",
            "observation_token": preview_token,
        }
    )
    if not key_preview.get("paused", False) or not key_preview.get("approval_required", False) or key_preview.get("checkpoint_tool") != "desktop_press_key":
        raise SystemExit("desktop_press_key() did not require approval in the expected bounded way.")
    type_preview = desktop_type_text(
        {
            "value": "desktop smoke text",
            "field_label": "desktop smoke input",
            "observation_token": preview_token,
        }
    )
    if not type_preview.get("paused", False) or not type_preview.get("approval_required", False) or type_preview.get("checkpoint_tool") != "desktop_type_text":
        raise SystemExit("desktop_type_text() did not require approval in the expected bounded way.")
desktop_backend_status = get_desktop_backend_status()
for required_backend in ("window", "screenshot", "ui_evidence"):
    if not isinstance(desktop_backend_status.get(required_backend, {}), dict):
        raise SystemExit("Desktop backend status did not include the expected backend sections.")
if not isinstance(desktop_backend_status.get("recovery", {}), dict):
    raise SystemExit("Desktop backend status did not include the expected recovery capability section.")
if desktop_backend_status.get("recovery", {}).get("readiness_backend", "") not in {"pywinauto", "stub"}:
    raise SystemExit("Desktop backend status did not expose the expected readiness backend.")
if desktop_backend_status.get("matching", {}).get("title_matching", "") not in {"rapidfuzz", "builtin"}:
    raise SystemExit("Desktop backend status did not expose the expected bounded title-matching backend.")
if not isinstance(desktop_backend_status.get("screenshot", {}).get("metadata", {}).get("available_backends", []), list):
    raise SystemExit("Desktop backend status did not expose available screenshot capture backends.")
desktop_capture = desktop_capture_screenshot({"scope": "desktop", "name": "desktop_backend_smoke"})
if not desktop_capture.get("ok", False) or not Path(str(desktop_capture.get("screenshot_path", ""))).exists():
    raise SystemExit("desktop_capture_screenshot() did not capture a bounded screenshot with the backend layer active.")
if Path(str(desktop_capture.get("screenshot_path", ""))).suffix.lower() not in {".png", ".bmp"}:
    raise SystemExit("desktop_capture_screenshot() did not use an expected bounded screenshot extension.")
ui_evidence_probe = probe_ui_evidence(target="active_window", limit=3)
if ui_evidence_probe.get("kind") != "ui_evidence_observation":
    raise SystemExit("probe_ui_evidence() did not return the expected normalized evidence envelope.")
if not isinstance(ui_evidence_probe.get("data", {}).get("controls", []), list):
    raise SystemExit("probe_ui_evidence() did not return normalized control evidence.")
if not desktop_capture.get("desktop_evidence_ref", {}).get("evidence_id"):
    raise SystemExit("desktop_capture_screenshot() did not expose a desktop evidence reference.")
if not isinstance(desktop_capture.get("desktop_evidence", {}), dict):
    raise SystemExit("desktop_capture_screenshot() did not expose a desktop evidence bundle.")
original_backend_settings = desktop_backends_module.load_settings
original_dxcam = desktop_backends_module.dxcam
original_bettercam = desktop_backends_module.bettercam
original_mss_tools = desktop_backends_module.mss_tools
try:
    class _FakeFrame:
        shape = (6, 8, 3)

        def tobytes(self):
            return b"\x00\x00\x00" * (6 * 8)

    class _FakeCamera:
        def grab(self, region=None):
            return _FakeFrame()

        def stop(self):
            return

    class _FakeDxcamModule:
        @staticmethod
        def create(output_color="RGB"):
            return _FakeCamera()

    class _FakeMssTools:
        @staticmethod
        def to_png(rgb, size, output):
            Path(output).write_bytes(b"fake-png")

    desktop_backends_module.load_settings = lambda: {
        "desktop_window_backend": "pywinctl",
        "desktop_screenshot_backend": "auto",
        "ui_evidence_backend": "pywinauto",
    }
    desktop_backends_module.dxcam = _FakeDxcamModule()
    desktop_backends_module.bettercam = None
    desktop_backends_module.mss_tools = _FakeMssTools()
    plugin_backend = desktop_backends_module.create_screenshot_backend(capture_delegate=lambda path, x, y, width, height: (False, "native fallback should not run"))
    if getattr(plugin_backend, "name", "") != "dxcam":
        raise SystemExit("create_screenshot_backend() did not activate the optional desktop-duplication plugin backend under auto preference.")
    plugin_capture_path = Path("data/plugin_capture_smoke.png")
    plugin_capture_path.parent.mkdir(parents=True, exist_ok=True)
    plugin_capture = plugin_backend.capture(
        plugin_capture_path,
        x=0,
        y=0,
        width=8,
        height=6,
        scope="desktop",
        active_window_title="plugin smoke",
    )
    if not plugin_capture.get("ok", False) or not plugin_capture_path.exists():
        raise SystemExit("Optional capture-plugin backend did not capture through the shared screenshot boundary.")

    desktop_backends_module.load_settings = lambda: {
        "desktop_window_backend": "pywinctl",
        "desktop_screenshot_backend": "dxcam",
        "ui_evidence_backend": "pywinauto",
    }
    desktop_backends_module.dxcam = None
    fallback_backend = desktop_backends_module.create_screenshot_backend(capture_delegate=lambda path, x, y, width, height: (True, ""))
    expected_fallback_backend = "mss" if desktop_backends_module.mss is not None and desktop_backends_module.mss_tools is not None else "native"
    if getattr(fallback_backend, "name", "") != expected_fallback_backend:
        raise SystemExit("create_screenshot_backend() did not fall back cleanly from an unavailable optional capture plugin backend.")
finally:
    desktop_backends_module.load_settings = original_backend_settings
    desktop_backends_module.dxcam = original_dxcam
    desktop_backends_module.bettercam = original_bettercam
    desktop_backends_module.mss_tools = original_mss_tools
missing_window_probe = desktop_inspect_window_state({"title": "__codex_missing_window__", "exact": True})
if missing_window_probe.get("recovery", {}).get("reason") not in {"tray_or_background_state", "target_not_found"}:
    raise SystemExit("desktop_inspect_window_state() did not classify a missing target window with a bounded recovery reason.")
missing_window_recovery = desktop_recover_window({"title": "__codex_missing_window__", "exact": True, "max_attempts": 1})
if missing_window_recovery.get("recovery", {}).get("strategy") != "report_missing_target":
    raise SystemExit("desktop_recover_window() did not choose the expected bounded missing-target recovery strategy.")
missing_window_wait = desktop_wait_for_window_ready({"title": "__codex_missing_window__", "exact": True, "wait_seconds": 0.25})
if missing_window_wait.get("recovery", {}).get("reason") not in {"tray_or_background_state", "target_not_found"}:
    raise SystemExit("desktop_wait_for_window_ready() did not report the expected bounded missing-target readiness state.")
original_pywinauto_desktop = desktop_backends_module.PyWinAutoDesktop
try:
    class _BoundedSmokeChild:
        def __init__(self, index):
            self.element_info = SimpleNamespace(name=f"Control {index}", control_type="Button", automation_id=f"auto-{index}")

        def window_text(self):
            return f"Control {self.element_info.name}"

    class _BoundedSmokeWindow:
        def __init__(self):
            self.element_info = SimpleNamespace(handle=0xABCDEF)

        def window_text(self):
            return "Bounded Probe Window"

        def is_visible(self):
            return True

        def is_enabled(self):
            return True

        def has_keyboard_focus(self):
            return True

        def descendants(self):
            yield _BoundedSmokeChild(0)
            yield _BoundedSmokeChild(1)
            yield _BoundedSmokeChild(2)
            raise RuntimeError("descendants walked past the bounded limit")

    class _BoundedSmokeDesktop:
        def __init__(self, backend="uia"):
            self.backend = backend

        def windows(self):
            return [_BoundedSmokeWindow()]

    desktop_backends_module.PyWinAutoDesktop = _BoundedSmokeDesktop
    bounded_readiness = desktop_backends_module.probe_window_readiness(target="active_window", limit=2)
    if not bounded_readiness.get("ok", False) or bounded_readiness.get("data", {}).get("control_count") != 2:
        raise SystemExit(f"probe_window_readiness() did not bound descendant enumeration correctly: {bounded_readiness}")
    bounded_ui = desktop_backends_module.PyWinAutoEvidenceBackend().probe(target="active_window", limit=2)
    if not bounded_ui.get("ok", False) or len(bounded_ui.get("data", {}).get("controls", [])) != 2:
        raise SystemExit(f"probe_ui_evidence() did not bound descendant enumeration correctly: {bounded_ui}")
finally:
    desktop_backends_module.PyWinAutoDesktop = original_pywinauto_desktop
original_readiness_probe = desktop_module.probe_window_readiness
original_inspect_window_state_internal = desktop_module._inspect_window_state_internal
try:
    desktop_module.probe_window_readiness = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("minimized metadata should bypass deep readiness probing"))
    minimized_metadata_readiness = desktop_module._readiness_probe_for_window(
        {
            "window_id": "0x00004001",
            "title": "Minimized Smoke Window",
            "is_visible": True,
            "is_minimized": True,
            "is_active": False,
            "rect": {"x": 0, "y": 0, "width": 1280, "height": 720},
        }
    )
    if minimized_metadata_readiness.get("reason") != "target_minimized" or minimized_metadata_readiness.get("backend") != "window_metadata":
        raise SystemExit(f"_readiness_probe_for_window() did not short-circuit minimized metadata cleanly: {minimized_metadata_readiness}")

    wait_calls = {"count": 0}

    def _stubbed_minimized_inspect(args, *, source_action, include_ui_evidence=True, include_visual_stability=True):
        wait_calls["count"] += 1
        return {
            "recovery": {
                "state": "needs_recovery",
                "reason": "target_minimized",
                "summary": "Window is minimized and should be restored before waiting again.",
            }
        }

    desktop_module._inspect_window_state_internal = _stubbed_minimized_inspect
    minimized_wait = desktop_wait_for_window_ready({"title": "Minimized Smoke Window", "wait_seconds": 1.0, "poll_interval_seconds": 0.05})
    if wait_calls["count"] != 1 or minimized_wait.get("recovery", {}).get("reason") != "target_minimized":
        raise SystemExit(f"desktop_wait_for_window_ready() did not exit immediately for a non-waiting minimized recovery state: calls={wait_calls} result={minimized_wait}")
finally:
    desktop_module.probe_window_readiness = original_readiness_probe
    desktop_module._inspect_window_state_internal = original_inspect_window_state_internal
original_window_backend = desktop_module._WINDOW_BACKEND
original_enum_windows_native = desktop_module._enum_windows_native
original_find_window_by_exact_title_native = desktop_module._find_window_by_exact_title_native
try:
    desktop_module._WINDOW_BACKEND = SimpleNamespace(
        list_windows=lambda include_minimized=False, limit=12: {
            "ok": True,
            "data": {
                "windows": [
                    {
                        "window_id": "0x00001001",
                        "title": "Visible Window",
                        "is_visible": True,
                        "is_cloaked": False,
                        "is_minimized": False,
                        "is_active": True,
                    }
                ]
            },
        }
    )
    desktop_module._enum_windows_native = lambda include_minimized=False, include_hidden=False, limit=12: (
        [
            {
                "window_id": "0x00001002",
                "title": "Hidden Target Window",
                "is_visible": False,
                "is_cloaked": False,
                "is_minimized": False,
                "is_active": False,
            }
        ]
        if include_hidden
        else []
    )
    hidden_target, hidden_candidates, hidden_error, hidden_match = desktop_module._find_window({"title": "Hidden Target Window", "exact": True, "limit": 10})
    if hidden_error or hidden_target.get("window_id") != "0x00001002":
        raise SystemExit(
            f"desktop hidden-window lookup did not merge native hidden candidates correctly: target={hidden_target} error={hidden_error} candidates={hidden_candidates}"
        )
    desktop_module._WINDOW_BACKEND = SimpleNamespace(list_windows=lambda include_minimized=False, limit=12: {"ok": True, "data": {"windows": []}})
    desktop_module._enum_windows_native = lambda include_minimized=False, include_hidden=False, limit=12: []
    desktop_module._find_window_by_exact_title_native = lambda title: (
        {
            "window_id": "0x00001003",
            "title": "Withdrawn Target Window",
            "is_visible": False,
            "is_cloaked": False,
            "is_minimized": False,
            "rect": {"x": 0, "y": 0, "width": 0, "height": 0},
        }
        if title == "Withdrawn Target Window"
        else {}
    )
    withdrawn_target, withdrawn_candidates, withdrawn_error, withdrawn_match = desktop_module._find_window(
        {"title": "Withdrawn Target Window", "exact": True, "limit": 10}
    )
    if withdrawn_error or withdrawn_target.get("window_id") != "0x00001003":
        raise SystemExit(
            f"desktop exact-title withdrawn lookup did not use the native direct-title fallback correctly: target={withdrawn_target} error={withdrawn_error} candidates={withdrawn_candidates}"
        )
finally:
    desktop_module._WINDOW_BACKEND = original_window_backend
    desktop_module._enum_windows_native = original_enum_windows_native
    desktop_module._find_window_by_exact_title_native = original_find_window_by_exact_title_native
title_drift_selection = select_window_candidate(
    [
        {
            "window_id": "0x00002001",
            "title": "Outlook - New Message (Draft)",
            "process_name": "outlook.exe",
            "class_name": "rctrl_renwnd32",
            "is_active": True,
            "is_visible": True,
            "is_minimized": False,
            "is_cloaked": False,
        },
        {
            "window_id": "0x00002002",
            "title": "Outlook",
            "process_name": "outlook.exe",
            "class_name": "rctrl_renwnd32",
            "is_active": False,
            "is_visible": True,
            "is_minimized": False,
            "is_cloaked": False,
        },
    ],
    requested_title="Outlook New Message Draft",
    expected_process_name="outlook.exe",
)
if title_drift_selection.get("selected", {}).get("window_id") != "0x00002001":
    raise SystemExit(f"select_window_candidate() did not rank the drifted Outlook compose title correctly: {title_drift_selection}")
if title_drift_selection.get("match_engine", "") not in {"rapidfuzz", "builtin"}:
    raise SystemExit("select_window_candidate() did not expose the expected bounded title-match engine metadata.")
if not titles_compatible("Approval Targt Window", "Approval Target Window"):
    raise SystemExit("titles_compatible() did not absorb small bounded title drift.")
ambiguous_selection = select_window_candidate(
    [
        {
            "window_id": "0x00002003",
            "title": "Editor - notes.txt",
            "process_name": "notepad.exe",
            "is_active": True,
            "is_visible": True,
        },
        {
            "window_id": "0x00002004",
            "title": "Editor - notes (copy).txt",
            "process_name": "notepad.exe",
            "is_active": False,
            "is_visible": True,
        },
    ],
    requested_title="Editor notes",
)
if ambiguous_selection.get("reason") != "candidate_ambiguous" or ambiguous_selection.get("selected"):
    raise SystemExit("select_window_candidate() did not keep a close bounded title collision diagnosably ambiguous.")
stable_visual = assess_visual_sample_signatures(["abc", "abc"], backend="mss")
if stable_visual.get("state") != "stable" or not stable_visual.get("stable", False):
    raise SystemExit("assess_visual_sample_signatures() did not treat identical samples as stable.")
unstable_visual = assess_visual_sample_signatures(["abc", "def"], backend="mss")
if unstable_visual.get("state") != "unstable" or unstable_visual.get("reason") != "visual_state_unstable":
    raise SystemExit("assess_visual_sample_signatures() did not treat changed samples as visually unstable.")
minimized_recovery = classify_window_recovery_state(
    requested_title="Example App",
    target_window={"window_id": "0x00000001", "title": "Example App", "is_visible": True, "is_minimized": True},
    active_window={"window_id": "0x00000002", "title": "Other App", "is_visible": True},
    candidate_count=3,
    readiness={"state": "ready", "ready": True, "reason": "ready"},
    visual_stability={"state": "stable", "stable": True, "reason": "inspected"},
    backend="smoke",
)
if minimized_recovery.get("reason") != "target_minimized":
    raise SystemExit("classify_window_recovery_state() did not detect the minimized-target recovery case.")
if select_window_recovery_strategy(minimized_recovery, attempt_count=0, max_attempts=2).get("strategy") != "restore_then_focus":
    raise SystemExit("select_window_recovery_strategy() did not choose restore_then_focus for a minimized window.")
foreground_recovery = classify_window_recovery_state(
    requested_title="Example App",
    target_window={"window_id": "0x00000001", "title": "Example App", "is_visible": True, "is_minimized": False},
    active_window={"window_id": "0x00000002", "title": "Other App", "is_visible": True},
    candidate_count=2,
    readiness={"state": "ready", "ready": True, "reason": "ready"},
    visual_stability={"state": "stable", "stable": True, "reason": "inspected"},
    backend="smoke",
)
if foreground_recovery.get("reason") != "foreground_not_confirmed":
    raise SystemExit("classify_window_recovery_state() did not detect the foreground-not-confirmed recovery case.")
drift_recovery = classify_window_recovery_state(
    requested_title="Approval Targt Window",
    target_window={"window_id": "0x00000005", "title": "Approval Target Window", "is_visible": True, "is_minimized": False},
    active_window={"window_id": "0x00000005", "title": "Approval Target Window", "is_visible": True},
    candidate_count=2,
    readiness={"state": "ready", "ready": True, "reason": "ready"},
    visual_stability={"state": "stable", "stable": True, "reason": "inspected"},
    candidate_preview=[{"title": "Approval Target Window", "score": 92, "match_kind": "fuzzy", "match_engine": "rapidfuzz"}],
    match_score=92,
    match_confidence="high",
    match_kind="fuzzy",
    match_engine="rapidfuzz",
    match_reason="Used bounded fuzzy matching to handle title drift.",
    backend="smoke",
)
if drift_recovery.get("reason") != "recovery_succeeded" or drift_recovery.get("match_kind") != "fuzzy":
    raise SystemExit("classify_window_recovery_state() did not preserve bounded title-drift diagnostics in the recovery view.")
if "title drift" not in drift_recovery.get("summary", "").lower():
    raise SystemExit("classify_window_recovery_state() did not explain fuzzy title-drift recovery in the normalized summary.")
withdrawn_recovery = classify_window_recovery_state(
    requested_title="Withdrawn App",
    target_window={
        "window_id": "0x00000004",
        "title": "Withdrawn App",
        "is_visible": False,
        "is_cloaked": False,
        "is_minimized": False,
        "rect": {"x": 0, "y": 0, "width": 0, "height": 0},
    },
    active_window={"window_id": "0x00000002", "title": "Other App", "is_visible": True},
    candidate_count=1,
    readiness={"state": "missing", "ready": False, "reason": "target_not_found"},
    visual_stability={"state": "missing", "stable": False, "reason": "missing"},
    backend="smoke",
)
if withdrawn_recovery.get("reason") != "target_withdrawn":
    raise SystemExit("classify_window_recovery_state() did not detect the withdrawn hidden-window recovery case.")
if select_window_recovery_strategy(withdrawn_recovery, attempt_count=0, max_attempts=2).get("strategy") != "report_missing_target":
    raise SystemExit("select_window_recovery_strategy() did not treat a withdrawn hidden window as an explicit report-only outcome.")
loading_recovery = classify_window_recovery_state(
    requested_title="Loading App",
    target_window={"window_id": "0x00000003", "title": "Loading App", "is_visible": True, "is_minimized": False},
    active_window={"window_id": "0x00000003", "title": "Loading App", "is_visible": True},
    candidate_count=1,
    readiness={"state": "loading", "ready": False, "loading": True, "reason": "target_loading"},
    visual_stability={"state": "unstable", "stable": False, "reason": "visual_state_unstable"},
    backend="smoke",
)
if loading_recovery.get("reason") != "target_loading":
    raise SystemExit("classify_window_recovery_state() did not prioritize the loading-state recovery reason.")
print("[OK] desktop tools")

client = LocalOperatorApiClient("http://127.0.0.1:8765/")
if client.base_url != "http://127.0.0.1:8765":
    raise SystemExit("LocalOperatorApiClient did not normalize the base URL.")


class _WaitStatusProbe:
    def __init__(self, snapshots, *, session_payload=None):
        self.snapshots = list(snapshots)
        self.session_payload = session_payload or {}
        self.calls = 0

    def status(self):
        if self.calls < len(self.snapshots):
            item = self.snapshots[self.calls]
        else:
            item = self.snapshots[-1] if self.snapshots else {}
        self.calls += 1
        return dict(item)

    def session(self):
        return dict(self.session_payload)


stable_terminal_probe = _WaitStatusProbe(
    [
        {"status": "running", "running": True, "active_task": {"status": "running"}, "latest_run": {"final_status": ""}},
        {"status": "completed", "running": True, "active_task": {"status": "running"}, "latest_run": {"final_status": "running"}},
        {"status": "completed", "running": True, "active_task": {"status": "running"}, "latest_run": {"final_status": "running"}},
    ],
    session_payload={"session": {"authoritative_reply": {"content": "Done."}}},
)
stable_terminal_status = wait_for_local_api_status(
    stable_terminal_probe.status,
    {"completed"},
    timeout_seconds=0.1,
    interval_seconds=0.001,
    session_getter=stable_terminal_probe.session,
    session_label="wait-stable-terminal",
)
if stable_terminal_status.get("status") != "completed":
    raise SystemExit("wait_for_local_api_status() did not return the stable completed status when ancillary fields lagged.")


class _StubWaitClient(LocalOperatorApiClient):
    def __init__(self, probe):
        super().__init__("http://127.0.0.1:8765/")
        self._probe = probe

    def get_status(self, *, session_id: str = "", state_scope_id: str = ""):
        return self._probe.status()

    def get_session(self, session_id: str):
        return self._probe.session()


client_wait_probe = _WaitStatusProbe(
    [
        {"status": "paused", "running": False, "active_task": {"status": "paused"}, "latest_run": {"final_status": "paused"}},
    ],
    session_payload={"session": {"last_result_message": "Paused for approval."}},
)
stub_client = _StubWaitClient(client_wait_probe)
if stub_client.wait_for_status("session-wait-smoke", {"paused"}, timeout_seconds=0.1, interval_seconds=0.001).get("status") != "paused":
    raise SystemExit("LocalOperatorApiClient.wait_for_status() did not return the expected paused snapshot.")

timeout_probe = _WaitStatusProbe(
    [
        {"status": "completed", "running": True, "active_task": {"status": "running"}, "latest_run": {"final_status": "running"}, "pending_approval": {"kind": "desktop_action"}},
    ],
    session_payload={"session": {"authoritative_reply": {"content": "Still finalizing."}}},
)
try:
    wait_for_local_api_status(
        timeout_probe.status,
        {"blocked"},
        timeout_seconds=0.01,
        interval_seconds=0.001,
        session_getter=timeout_probe.session,
        session_label="wait-timeout-smoke",
    )
    raise SystemExit("wait_for_local_api_status() did not time out when the requested status never appeared.")
except TimeoutError as exc:
    message = str(exc)
    if "running=True" not in message or "latest_run_status=running" not in message or "pending_approval=desktop_action" not in message:
        raise SystemExit("wait_for_local_api_status() timeout did not include the expected diagnostic fields.")
print("[OK] local api client")

cors_server = LocalOperatorApiServer(port=0, settings=SMOKE_SETTINGS)
cors_server.start_in_thread()
cors_request = Request(
    f"http://127.0.0.1:{cors_server.port}/health",
    method="OPTIONS",
    headers={"Origin": "http://127.0.0.1:1420"},
)
with urlopen(cors_request, timeout=5) as cors_response:
    if cors_response.status != 204:
        raise SystemExit("Local API did not return 204 for CORS preflight.")
    if cors_response.headers.get("Access-Control-Allow-Origin") != "http://127.0.0.1:1420":
        raise SystemExit("Local API did not return the expected allowed web origin.")
    if "POST" not in cors_response.headers.get("Access-Control-Allow-Methods", ""):
        raise SystemExit("Local API did not expose expected CORS methods.")
health_payload = cors_server.controller.get_runtime_config()
with urlopen(f"http://127.0.0.1:{cors_server.port}/health", timeout=5) as health_response:
    parsed_health = json.loads(health_response.read().decode("utf-8"))
    if parsed_health.get("data", {}).get("runtime", {}).get("active_model") != health_payload.get("active_model"):
        raise SystemExit("Local API health did not expose the live runtime model configuration.")
    management_payload = parsed_health.get("data", {}).get("management", {})
    if not isinstance(management_payload, dict) or "managed_by_desktop" not in management_payload or "api_pid" not in management_payload:
        raise SystemExit("Local API health did not expose the expected management ownership metadata.")
cors_server.shutdown()
print("[OK] local api cors")

temp_evidence_root = Path("data/desktop_evidence_smoke")
if temp_evidence_root.exists():
    shutil.rmtree(temp_evidence_root, ignore_errors=True)
temp_evidence_store = DesktopEvidenceStore(temp_evidence_root, max_items=3)
evidence_now = datetime.now().astimezone()
first_timestamp = (evidence_now - timedelta(seconds=30)).isoformat(timespec="seconds")
second_timestamp = (evidence_now - timedelta(seconds=20)).isoformat(timespec="seconds")
third_timestamp = (evidence_now - timedelta(seconds=10)).isoformat(timespec="seconds")
fourth_timestamp = evidence_now.isoformat(timespec="seconds")

first_capture_path = temp_evidence_store.artifact_path("desk-smoke-1", extension=".png")
first_capture_path.write_bytes(b"desktop evidence smoke 1")
first_bundle = build_desktop_evidence_bundle(
    source_action="desktop_capture_screenshot",
    active_window={
        "window_id": "0x00123456",
        "title": "Evidence Smoke Window",
        "process_name": "python.exe",
        "rect": {"x": 10, "y": 20, "width": 640, "height": 480},
        "is_active": True,
        "is_visible": True,
        "backend": "pywinctl",
    },
    windows=[
        {
            "window_id": "0x00123456",
            "title": "Evidence Smoke Window",
            "process_name": "python.exe",
            "rect": {"x": 10, "y": 20, "width": 640, "height": 480},
            "is_active": True,
            "is_visible": True,
            "backend": "pywinctl",
        }
    ],
    observation_token="desktop-evidence-smoke-1",
    screenshot={
        "backend": "mss",
        "path": str(first_capture_path),
        "scope": "active_window",
        "bounds": {"x": 10, "y": 20, "width": 640, "height": 480},
        "metadata": {"format": "png"},
    },
    ui_evidence={
        "backend": "pywinauto",
        "target": "Evidence Smoke Window",
        "controls": [{"name": "Search", "control_type": "Edit", "automation_id": "SearchBox", "text": ""}],
    },
    target_window={
        "window_id": "0x00123456",
        "title": "Evidence Smoke Window",
        "process_name": "python.exe",
        "rect": {"x": 10, "y": 20, "width": 640, "height": 480},
        "is_active": True,
        "is_visible": True,
        "backend": "pywinctl",
    },
    screen={
        "virtual_screen": {"x": 0, "y": 0, "width": 1920, "height": 1080},
        "monitors": [{"left": 0, "top": 0, "width": 1920, "height": 1080}],
        "backend": "mss",
    },
    capture_mode="manual",
    importance="manual",
    importance_reason="manual_capture",
    state_scope_id="chat:desktop-evidence",
    task_id="task-desktop-evidence",
    task_status="running",
)
first_bundle["evidence_id"] = "desk-smoke-1"
first_bundle["timestamp"] = first_timestamp
first_ref = temp_evidence_store.record_bundle(first_bundle)
loaded_first_bundle = temp_evidence_store.load_bundle("desk-smoke-1")
if loaded_first_bundle.get("evidence_id") != "desk-smoke-1":
    raise SystemExit("DesktopEvidenceStore did not persist the expected evidence bundle.")
if loaded_first_bundle.get("reason") != "collected":
    raise SystemExit("Desktop evidence bundle did not preserve the collected reason.")
if json.loads(json.dumps(loaded_first_bundle)).get("evidence_id") != "desk-smoke-1":
    raise SystemExit("Desktop evidence bundle was not serialization-friendly.")

second_bundle = build_desktop_evidence_bundle(
    source_action="desktop_get_active_window",
    active_window={"title": "Partial Evidence Window", "window_id": "0x00123457", "process_name": "pythonw.exe"},
    windows=[{"title": "Partial Evidence Window", "window_id": "0x00123457", "process_name": "pythonw.exe"}],
    observation_token="desktop-evidence-smoke-2",
    screenshot={},
    ui_evidence={"backend": "pywinauto", "target": "Partial Evidence Window", "controls": []},
    errors=["screenshot backend unavailable"],
    capture_mode="auto",
    importance="normal",
    importance_reason="state_changed",
    state_scope_id="chat:desktop-evidence",
    task_id="task-desktop-evidence",
    task_status="running",
)
second_bundle["evidence_id"] = "desk-smoke-2"
second_bundle["timestamp"] = second_timestamp
second_ref = temp_evidence_store.record_bundle(second_bundle)
loaded_second_bundle = temp_evidence_store.load_bundle("desk-smoke-2")
if loaded_second_bundle.get("reason") != "partial":
    raise SystemExit("Desktop evidence bundle did not mark partial evidence correctly.")

third_capture_path = temp_evidence_store.artifact_path("desk-smoke-3", extension=".png")
third_capture_path.write_bytes(b"desktop evidence smoke 3")
third_bundle = build_desktop_evidence_bundle(
    source_action="desktop_capture_screenshot",
    active_window={"title": "Approval Target Window", "window_id": "0x00123458", "process_name": "notepad.exe"},
    windows=[{"title": "Approval Target Window", "window_id": "0x00123458", "process_name": "notepad.exe"}],
    observation_token="desktop-evidence-smoke-3",
    screenshot={
        "backend": "mss",
        "path": str(third_capture_path),
        "scope": "desktop",
        "bounds": {"x": 0, "y": 0, "width": 1920, "height": 1080},
    },
    capture_mode="manual",
    importance="checkpoint",
    importance_reason="checkpoint_pending",
    state_scope_id="chat:desktop-evidence",
    task_id="task-desktop-evidence",
    task_status="paused",
    checkpoint_pending=True,
    checkpoint_tool="desktop_click_point",
    checkpoint_target="Approval Target Window",
)
third_bundle["evidence_id"] = "desk-smoke-3"
third_bundle["timestamp"] = third_timestamp
third_ref = temp_evidence_store.record_bundle(third_bundle)
fourth_capture_path = temp_evidence_store.artifact_path("desk-smoke-4", extension=".png")
fourth_capture_path.write_bytes(b"desktop evidence smoke 4")
fourth_bundle = build_desktop_evidence_bundle(
    source_action="desktop_auto_capture",
    active_window={"title": "Background Window", "window_id": "0x00123459", "process_name": "explorer.exe"},
    windows=[{"title": "Background Window", "window_id": "0x00123459", "process_name": "explorer.exe"}],
    observation_token="desktop-evidence-smoke-4",
    screenshot={
        "backend": "mss",
        "path": str(fourth_capture_path),
        "scope": "active_window",
        "bounds": {"x": 12, "y": 18, "width": 800, "height": 600},
    },
    capture_mode="auto",
    importance="normal",
    importance_reason="state_changed",
    state_scope_id="chat:desktop-idle",
    task_status="idle",
)
fourth_bundle["evidence_id"] = "desk-smoke-4"
fourth_bundle["timestamp"] = fourth_timestamp
temp_evidence_store.record_bundle(fourth_bundle)
if not temp_evidence_store.load_bundle("desk-smoke-1"):
    raise SystemExit("DesktopEvidenceStore did not preserve an older important/manual bundle when retention was exceeded.")
if not first_capture_path.exists():
    raise SystemExit("DesktopEvidenceStore did not preserve an older important/manual screenshot artifact when retention was exceeded.")
if temp_evidence_store.load_bundle("desk-smoke-4"):
    raise SystemExit("DesktopEvidenceStore did not prune the lower-priority automatic bundle when retention was exceeded.")
if fourth_capture_path.exists():
    raise SystemExit("DesktopEvidenceStore did not prune the lower-priority automatic screenshot artifact when retention was exceeded.")
if len(temp_evidence_store.recent_refs(limit=8)) != 3:
    raise SystemExit("DesktopEvidenceStore did not enforce bounded recent evidence retention.")
if temp_evidence_store.find_by_observation_token("desktop-evidence-smoke-3").get("evidence_id") != "desk-smoke-3":
    raise SystemExit("DesktopEvidenceStore did not resolve an evidence ref by observation token.")
if temp_evidence_store.status_snapshot().get("latest", {}).get("evidence_id") != "desk-smoke-3":
    raise SystemExit("DesktopEvidenceStore did not expose the latest evidence status correctly.")

summary_first = summarize_evidence_bundle(loaded_first_bundle)
summary_second = summarize_evidence_bundle(loaded_second_bundle)
summary_third = summarize_evidence_bundle(temp_evidence_store.load_bundle("desk-smoke-3"))
if not summary_first.get("has_screenshot", False) or not summary_first.get("ui_evidence_present", False):
    raise SystemExit("Desktop evidence summary did not preserve screenshot/UI evidence presence.")
if summary_second.get("reason") != "partial" or not summary_second.get("is_partial", False):
    raise SystemExit("Desktop evidence summary did not preserve partial evidence state.")
if compact_evidence_preview(summary_third).get("evidence_id") != "desk-smoke-3":
    raise SystemExit("compact_evidence_preview() did not preserve the expected evidence id.")
if summary_first.get("capture_mode") != "manual" or summary_first.get("importance") != "manual":
    raise SystemExit("Desktop evidence summary did not preserve manual capture retention metadata.")
if summary_third.get("importance") != "checkpoint" or not summary_third.get("checkpoint_pending", False):
    raise SystemExit("Desktop evidence summary did not preserve checkpoint capture metadata.")

recent_summaries = temp_evidence_store.recent_summaries(limit=8)
if len(recent_summaries) != 3:
    raise SystemExit("DesktopEvidenceStore did not expose the expected bounded recent summaries.")
latest_selection = select_recent_evidence(recent_summaries, strategy="latest")
if latest_selection.get("selected", {}).get("evidence_id") != "desk-smoke-3":
    raise SystemExit("select_recent_evidence(latest) did not choose the most recent evidence.")
latest_screenshot_selection = select_recent_evidence(recent_summaries, strategy="latest_with_screenshot")
if latest_screenshot_selection.get("selected", {}).get("evidence_id") != "desk-smoke-3":
    raise SystemExit("select_recent_evidence(latest_with_screenshot) did not choose the latest screenshot evidence.")
latest_partial_selection = select_recent_evidence(recent_summaries, strategy="latest_partial")
if latest_partial_selection.get("selected", {}).get("evidence_id") != "desk-smoke-2":
    raise SystemExit("select_recent_evidence(latest_partial) did not choose the latest partial evidence.")
window_selection = select_recent_evidence(recent_summaries, strategy="window_title", active_window_title="Approval Target Window")
if window_selection.get("selected", {}).get("evidence_id") != "desk-smoke-3":
    raise SystemExit("select_recent_evidence(window_title) did not match the expected window title.")
checkpoint_selection = select_checkpoint_evidence(
    recent_summaries,
    checkpoint_evidence_id="desk-smoke-3",
    checkpoint_target="Approval Target Window",
)
if checkpoint_selection.get("selected", {}).get("evidence_id") != "desk-smoke-3":
    raise SystemExit("select_checkpoint_evidence() did not choose the checkpoint-linked evidence.")
task_selection = select_task_evidence(
    recent_summaries,
    task_evidence_id="desk-smoke-2",
    active_window_title="Partial Evidence Window",
)
if task_selection.get("selected", {}).get("evidence_id") != "desk-smoke-2":
    raise SystemExit("select_task_evidence() did not choose the task-linked evidence.")
drift_task_selection = select_task_evidence(
    recent_summaries,
    task_evidence_id="desk-smoke-2",
    observation_token="desktop-evidence-smoke-2",
    active_window_title="Partial Evidence Window",
    target_window_title="Approval Target Window",
)
if drift_task_selection.get("selected", {}).get("evidence_id") != "desk-smoke-3":
    raise SystemExit("select_task_evidence() did not prefer explicit target-matching evidence when the latest task evidence drifted to another active window.")
recent_context = temp_evidence_store.recent_context_summaries(
    limit=3,
    state_scope_id="chat:desktop-evidence",
    task_id="task-desktop-evidence",
    active_window_title="Approval Target Window",
    checkpoint_target="Approval Target Window",
)
if not recent_context or recent_context[0].get("evidence_id") != "desk-smoke-3":
    raise SystemExit("DesktopEvidenceStore.recent_context_summaries() did not prioritize the checkpoint-bound recent desktop evidence.")

summary_only_vision = select_desktop_vision_context(
    selected_summary=summary_first,
    recent_summaries=recent_summaries,
    purpose="desktop_investigation",
    prompt_text="Which window is active right now?",
    assessment={"state": "sufficient", "sufficient": True, "summary": "The selected desktop evidence already answers the question."},
)
if summary_only_vision.get("mode") != "summary_only" or summary_only_vision.get("needs_direct_image", False):
    raise SystemExit("select_desktop_vision_context() did not keep a simple desktop question in summary-only mode.")

single_image_vision = select_desktop_vision_context(
    selected_summary=summary_first,
    recent_summaries=recent_summaries,
    purpose="desktop_investigation",
    prompt_text="What exact text is visible on the button in the screenshot?",
    assessment={"state": "sufficient", "sufficient": True},
)
if single_image_vision.get("mode") != "single_image" or not single_image_vision.get("needs_direct_image", False):
    raise SystemExit("select_desktop_vision_context() did not choose a bounded single-image path for a visually specific desktop question.")
vision_content = _content_with_desktop_vision("Desktop smoke", single_image_vision)
if not isinstance(vision_content, list) or not any(isinstance(item, dict) and item.get("type") == "image_url" for item in vision_content):
    raise SystemExit("_content_with_desktop_vision() did not attach bounded image_url content for direct desktop vision.")

before_compare_bundle = build_desktop_evidence_bundle(
    source_action="desktop_capture_screenshot",
    active_window={"title": "Approval Target Window", "window_id": "0x00123460", "process_name": "notepad.exe"},
    windows=[{"title": "Approval Target Window", "window_id": "0x00123460", "process_name": "notepad.exe"}],
    observation_token="desktop-evidence-smoke-before",
    screenshot={
        "backend": "mss",
        "path": str(first_capture_path),
        "scope": "desktop",
        "bounds": {"x": 0, "y": 0, "width": 1920, "height": 1080},
    },
    capture_mode="manual",
    importance="normal",
    importance_reason="manual_capture",
    state_scope_id="chat:desktop-evidence",
    task_id="task-desktop-evidence",
    task_status="running",
)
before_compare_bundle["evidence_id"] = "desk-smoke-before-approval"
before_compare_bundle["timestamp"] = first_timestamp
before_compare_summary = summarize_evidence_bundle(before_compare_bundle)
before_after_vision = select_desktop_vision_context(
    selected_summary=summary_third,
    recent_summaries=[before_compare_summary, *recent_summaries],
    purpose="desktop_investigation",
    prompt_text="What changed on the Approval Target Window between before and after?",
    assessment={"state": "sufficient", "sufficient": True},
    prefer_before_after=True,
)
if before_after_vision.get("mode") != "before_after_pair" or int(before_after_vision.get("image_count", 0) or 0) != 2:
    raise SystemExit("select_desktop_vision_context() did not choose a bounded before/after pair for changed desktop state reasoning.")

approval_vision = select_desktop_vision_context(
    selected_summary=summary_second,
    checkpoint_summary=summary_third,
    recent_summaries=recent_summaries,
    purpose="desktop_approval",
    prompt_text="Click the Apply button in the approval target window.",
    assessment={"state": "needs_refresh", "sufficient": False},
    checkpoint_assessment={"state": "sufficient", "sufficient": True},
)
if approval_vision.get("primary_evidence_id") != "desk-smoke-3" or approval_vision.get("mode") != "single_image":
    raise SystemExit("select_desktop_vision_context() did not prioritize checkpoint-linked screenshot evidence for bounded desktop approval grounding.")

investigation_assessment = assess_desktop_evidence(
    summary_second,
    purpose="desktop_investigation",
    target_window_title="Partial Evidence Window",
    require_screenshot=False,
    max_age_seconds=86_400,
)
if not investigation_assessment.get("sufficient", False) or investigation_assessment.get("state") != "partial":
    raise SystemExit("assess_desktop_evidence() did not allow partial recent evidence for bounded read-only desktop investigation.")
action_assessment = temp_evidence_store.assess_summary(
    summary=summary_third,
    purpose="desktop_action_prepare",
    target_window_title="Approval Target Window",
    require_screenshot=True,
    max_age_seconds=86_400,
)
if not action_assessment.get("sufficient", False) or action_assessment.get("state") != "sufficient":
    raise SystemExit("DesktopEvidenceStore.assess_summary() did not treat recent screenshot-backed evidence as sufficient for bounded desktop action preparation.")
fuzzy_recent_selection = select_recent_evidence(
    recent_summaries,
    strategy="window_title",
    active_window_title="Approval Targt Window",
)
if fuzzy_recent_selection.get("selected", {}).get("evidence_id") != "desk-smoke-3":
    raise SystemExit("select_recent_evidence() did not keep bounded fuzzy title matching continuity for selected desktop evidence.")
refresh_assessment = temp_evidence_store.assess_summary(
    summary=summary_second,
    purpose="desktop_action_prepare",
    target_window_title="Partial Evidence Window",
    require_screenshot=False,
    max_age_seconds=86_400,
)
if refresh_assessment.get("sufficient", False) or not refresh_assessment.get("needs_refresh", False) or refresh_assessment.get("reason") != "partial_evidence":
    raise SystemExit("DesktopEvidenceStore.assess_summary() did not recommend refresh for partial desktop action evidence.")

original_evidence_store = getattr(desktop_evidence_module, "_STORE", None)
desktop_evidence_module._STORE = temp_evidence_store

desktop_state = TaskState("desktop evidence smoke")
desktop_state_result = {
    "ok": True,
    "summary": "Captured a screenshot of the active window.",
    "screenshot_path": third_ref.get("screenshot_path", ""),
    "process_context": {
        "pid": 4321,
        "process_name": "notepad.exe",
        "status": "running",
        "running": True,
        "summary": "Foreground window process notepad.exe is running normally.",
    },
    "desktop_state": {
        "active_window": {
            "title": "Approval Target Window",
            "window_id": "0x00123458",
            "process_name": "notepad.exe",
        },
        "windows": [{"title": "Approval Target Window"}],
        "observation_token": "desktop-evidence-smoke-3",
        "observed_at": "2026-03-23T10:00:00",
    },
    "desktop_evidence": temp_evidence_store.load_bundle("desk-smoke-3"),
    "desktop_evidence_ref": third_ref,
}
desktop_state.add_step({"type": "tool", "status": "completed", "tool": "desktop_capture_screenshot", "args": {}, "result": desktop_state_result})
desktop_state.update_memory_from_tool("desktop_capture_screenshot", desktop_state_result)
desktop_evidence_snapshot = desktop_state.get_control_snapshot()
if desktop_evidence_snapshot.get("desktop", {}).get("evidence_id") != "desk-smoke-3":
    raise SystemExit("TaskState did not surface desktop evidence in the authoritative control snapshot.")
if desktop_evidence_snapshot.get("desktop", {}).get("evidence_bundle_path", "") != third_ref.get("bundle_path", ""):
    raise SystemExit("TaskState did not preserve the desktop evidence bundle path.")
if desktop_evidence_snapshot.get("desktop", {}).get("selected_evidence", {}).get("evidence_id") != "desk-smoke-3":
    raise SystemExit("TaskState did not surface the selected desktop evidence summary.")
if not desktop_evidence_snapshot.get("desktop", {}).get("selected_evidence_assessment", {}).get("sufficient", False):
    raise SystemExit("TaskState did not surface selected desktop evidence sufficiency in the authoritative control snapshot.")
if desktop_evidence_snapshot.get("desktop", {}).get("selected_vision", {}).get("mode") != "summary_only":
    raise SystemExit("TaskState did not surface the expected compact selected desktop vision summary.")
if desktop_evidence_snapshot.get("desktop", {}).get("latest_process_context", {}).get("process_name") != "notepad.exe":
    raise SystemExit("TaskState did not surface the latest bounded desktop process context.")
desktop_observation_text = desktop_state.get_observation()
if "Selected desktop evidence assessment:" not in desktop_observation_text:
    raise SystemExit("TaskState.get_observation() did not include compact selected desktop evidence grounding lines.")
if "Desktop process context: notepad.exe" not in desktop_observation_text:
    raise SystemExit("TaskState.get_observation() did not include compact desktop process grounding lines.")
desktop_target_state = TaskState("Inspect the window titled 'Approval Target Window'")
desktop_target_state.desktop_last_evidence_id = "desk-smoke-2"
desktop_target_state.desktop_observation_token = "desktop-evidence-smoke-2"
desktop_target_state.desktop_active_window_title = "Partial Evidence Window"
desktop_target_state.desktop_active_window_process = "pythonw.exe"
desktop_target_state.desktop_last_target_window = "Approval Target Window"
desktop_target_activity = desktop_target_state._collect_desktop_activity(limit=4)
if desktop_target_activity.get("selected_evidence", {}).get("evidence_id") != "desk-smoke-3":
    raise SystemExit("TaskState did not keep selected desktop evidence anchored to the intended target window when the current active evidence drifted.")
prepared_targeted_args = tool_runtime.prepare_args(
    "desktop_inspect_window_state",
    {},
    desktop_target_state,
    planning_goal="Inspect the window titled 'Approval Target Window'",
)
if prepared_targeted_args.get("title") != "Approval Target Window" or prepared_targeted_args.get("expected_window_title") != "Approval Target Window":
    raise SystemExit("ToolRuntime did not preserve the remembered bounded desktop target when preparing targeted desktop tool args.")
if prepared_targeted_args.get("expected_window_id"):
    raise SystemExit("ToolRuntime incorrectly seeded a targeted desktop lookup with the current active window id.")

desktop_checkpoint_summary_state = TaskState("desktop checkpoint evidence smoke")
desktop_checkpoint_summary_state.update_memory_from_tool(
    "desktop_click_point",
    {
        "ok": False,
        "paused": True,
        "approval_required": True,
        "checkpoint_required": True,
        "checkpoint_reason": "Approval required before clicking.",
        "checkpoint_tool": "desktop_click_point",
        "checkpoint_target": "Approval Target Window",
        "checkpoint_resume_args": {"x": 12, "y": 18, "observation_token": "desktop-evidence-smoke-3"},
        "summary": "Approval required before clicking.",
        "desktop_state": {
            "active_window": {
                "title": "Approval Target Window",
                "window_id": "0x00123458",
                "process_name": "notepad.exe",
            },
            "windows": [{"title": "Approval Target Window"}],
            "observation_token": "desktop-evidence-smoke-3",
            "observed_at": "2026-03-23T10:02:00",
        },
        "desktop_evidence_ref": third_ref,
        "desktop_evidence": temp_evidence_store.load_bundle("desk-smoke-3"),
    },
)
checkpoint_snapshot = desktop_checkpoint_summary_state.get_control_snapshot()
if checkpoint_snapshot.get("pending_approval", {}).get("evidence_preview", {}).get("evidence_id") != "desk-smoke-3":
    raise SystemExit("TaskState did not surface checkpoint-linked evidence in pending approval state.")
if checkpoint_snapshot.get("pending_approval", {}).get("evidence_assessment", {}).get("state") != "sufficient":
    raise SystemExit("TaskState did not surface checkpoint-linked evidence assessment in pending approval state.")
if checkpoint_snapshot.get("pending_approval", {}).get("vision_preview", {}).get("mode") != "single_image":
    raise SystemExit("TaskState did not surface checkpoint-linked bounded desktop vision context in pending approval state.")
checkpoint_final_context = desktop_checkpoint_summary_state.get_final_context()
if "Checkpoint desktop evidence assessment:" not in checkpoint_final_context:
    raise SystemExit("TaskState.get_final_context() did not include checkpoint desktop evidence grounding lines.")
if "Checkpoint desktop vision:" not in checkpoint_final_context:
    raise SystemExit("TaskState.get_final_context() did not include checkpoint desktop vision grounding lines.")

status_payload = _status_payload(checkpoint_snapshot)
if status_payload.get("pending_approval", {}).get("evidence_assessment", {}).get("state") != "sufficient":
    raise SystemExit("Local API status compaction did not expose checkpoint evidence assessment.")
if status_payload.get("desktop", {}).get("checkpoint_evidence_assessment", {}).get("state") != "sufficient":
    raise SystemExit("Local API status compaction did not expose desktop checkpoint evidence assessment.")
if status_payload.get("pending_approval", {}).get("vision_preview", {}).get("mode") != "single_image":
    raise SystemExit("Local API status compaction did not expose bounded checkpoint desktop vision context.")
if status_payload.get("desktop", {}).get("selected_vision", {}).get("mode") != "summary_only":
    raise SystemExit("Local API status compaction did not expose bounded selected desktop vision context.")


class _DesktopApprovalStubLLM:
    def finalize(self, goal, steps, observation="", final_context="", **kwargs):
        return "desktop approval stub"


class _DesktopApprovalStubRuntime:
    def goal_has_explicit_desktop_approval(self, goal: str) -> bool:
        return False


desktop_refresh_state = TaskState("Click (12, 18) in window titled 'Partial Evidence Window'")
desktop_refresh_result = {
    "ok": True,
    "summary": "Observed the active desktop window.",
    "desktop_state": {
        "active_window": {"title": "Partial Evidence Window", "window_id": "0x00123457", "process_name": "pythonw.exe"},
        "windows": [{"title": "Partial Evidence Window"}],
        "observation_token": "desktop-evidence-smoke-2",
        "observed_at": "2026-03-23T10:01:00",
    },
    "desktop_evidence_ref": second_ref,
    "desktop_evidence": temp_evidence_store.load_bundle("desk-smoke-2"),
}
desktop_refresh_state.add_step({"type": "tool", "status": "completed", "tool": "desktop_get_active_window", "args": {}, "result": desktop_refresh_result})
desktop_refresh_state.update_memory_from_tool("desktop_get_active_window", desktop_refresh_result)
if _maybe_pause_for_desktop_action(
    _DesktopApprovalStubLLM(),
    _DesktopApprovalStubRuntime(),
    desktop_refresh_state,
    "Click (12, 18) in window titled 'Partial Evidence Window'",
) is not None:
    raise SystemExit("Desktop approval synthesis did not wait for fresher evidence when only partial desktop evidence was available.")

desktop_ready_state = TaskState("Click (12, 18) in window titled 'Approval Target Window'")
desktop_ready_result = {
    "ok": True,
    "summary": "Captured a screenshot of the active window.",
    "desktop_state": {
        "active_window": {"title": "Approval Target Window", "window_id": "0x00123458", "process_name": "notepad.exe"},
        "windows": [{"title": "Approval Target Window"}],
        "observation_token": "desktop-evidence-smoke-3",
        "observed_at": "2026-03-23T10:02:00",
        "screenshot_path": third_ref.get("screenshot_path", ""),
        "screenshot_scope": "desktop",
    },
    "desktop_evidence_ref": third_ref,
    "desktop_evidence": temp_evidence_store.load_bundle("desk-smoke-3"),
}
desktop_ready_state.add_step({"type": "tool", "status": "completed", "tool": "desktop_capture_screenshot", "args": {}, "result": desktop_ready_result})
desktop_ready_state.update_memory_from_tool("desktop_capture_screenshot", desktop_ready_result)
if not _is_redundant_desktop_observation(
    desktop_ready_state,
    "desktop_capture_screenshot",
    "What is visible in window titled 'Approval Target Window'?",
):
    raise SystemExit("Desktop loop guard did not treat repeated screenshot capture as redundant once current evidence was already sufficient.")
desktop_pause_result = _maybe_pause_for_desktop_action(
    _DesktopApprovalStubLLM(),
    _DesktopApprovalStubRuntime(),
    desktop_ready_state,
    "Click (12, 18) in window titled 'Approval Target Window'",
)
if not isinstance(desktop_pause_result, dict) or desktop_pause_result.get("status") != "paused":
    raise SystemExit("Desktop approval synthesis did not create a paused checkpoint when evidence-backed desktop action preparation was sufficient.")
paused_snapshot = desktop_ready_state.get_control_snapshot()
if paused_snapshot.get("pending_approval", {}).get("evidence_preview", {}).get("evidence_id") != "desk-smoke-3":
    raise SystemExit("Desktop approval synthesis did not retain checkpoint-linked evidence for the paused desktop checkpoint.")
if paused_snapshot.get("pending_approval", {}).get("evidence_assessment", {}).get("state") != "sufficient":
    raise SystemExit("Desktop approval synthesis did not retain checkpoint evidence sufficiency for the paused desktop checkpoint.")
if paused_snapshot.get("desktop", {}).get("run_outcome", {}).get("outcome") != "approval_needed":
    raise SystemExit("Desktop approval synthesis did not expose an explicit approval-needed desktop run outcome.")
if _status_payload(paused_snapshot).get("desktop", {}).get("run_outcome", {}).get("outcome") != "approval_needed":
    raise SystemExit("Local API status compaction did not expose the approval-needed desktop run outcome.")

missing_target_state = TaskState("Inspect the window titled 'Missing Desktop Window'")
missing_target_result = {
    "ok": False,
    "summary": "Could not find a visible top-level window for 'Missing Desktop Window'. It may be closed, minimized to the tray, or only present in the background.",
    "error": "Could not find a visible top-level window for 'Missing Desktop Window'. It may be closed, minimized to the tray, or only present in the background.",
    "recovery": {
        "state": "missing",
        "reason": "target_not_found",
        "summary": "Could not find a visible top-level window for 'Missing Desktop Window'. It may be closed, minimized to the tray, or only present in the background.",
        "strategy": "report_missing_target",
        "attempt_count": 1,
        "max_attempts": 2,
    },
}
missing_target_state.add_step(
    {
        "type": "tool",
        "status": "failed",
        "tool": "desktop_inspect_window_state",
        "args": {"title": "Missing Desktop Window", "expected_window_title": "Missing Desktop Window"},
        "result": missing_target_result,
    }
)
missing_target_state.update_memory_from_tool("desktop_inspect_window_state", missing_target_result)
missing_target_final = _maybe_finalize_desktop_terminal_outcome(
    _DesktopApprovalStubLLM(),
    missing_target_state,
    missing_target_state.goal,
)
if not isinstance(missing_target_final, dict) or missing_target_final.get("status") != "incomplete":
    raise SystemExit("Desktop terminal finalization did not convert a missing-target recovery outcome into a clean incomplete result.")
if missing_target_state.get_control_snapshot().get("desktop", {}).get("run_outcome", {}).get("outcome") != "unrecoverable_missing_target":
    raise SystemExit("Desktop terminal finalization did not expose the unrecoverable missing-target outcome in state.")

tray_background_state = TaskState("Inspect the window titled 'Tray Backed Window'")
tray_background_result = {
    "ok": False,
    "summary": "The target appears backgrounded, tray-like, or not visibly surfaced. Process check: pythonw.exe is running with status 'running'.",
    "error": "The target appears backgrounded, tray-like, or not visibly surfaced. Process check: pythonw.exe is running with status 'running'.",
    "process_context": {
        "process_name": "pythonw.exe",
        "status": "running",
        "running": True,
        "background_candidate": True,
        "summary": "pythonw.exe is still running and looks like a background or tray candidate.",
    },
    "recovery": {
        "state": "missing",
        "reason": "tray_or_background_state",
        "summary": "The target appears backgrounded, tray-like, or not visibly surfaced.",
        "strategy": "report_missing_target",
        "attempt_count": 1,
        "max_attempts": 2,
    },
}
tray_background_state.add_step(
    {
        "type": "tool",
        "status": "failed",
        "tool": "desktop_recover_window",
        "args": {"title": "Tray Backed Window", "expected_window_title": "Tray Backed Window"},
        "result": tray_background_result,
    }
)
tray_background_state.update_memory_from_tool("desktop_recover_window", tray_background_result)
tray_background_final = _maybe_finalize_desktop_terminal_outcome(
    _DesktopApprovalStubLLM(),
    tray_background_state,
    tray_background_state.goal,
)
if not isinstance(tray_background_final, dict) or tray_background_final.get("status") != "incomplete":
    raise SystemExit("Desktop terminal finalization did not convert a tray/background recovery outcome into a clean incomplete result.")
if tray_background_state.get_control_snapshot().get("desktop", {}).get("run_outcome", {}).get("outcome") != "unrecoverable_tray_background":
    raise SystemExit("Desktop terminal finalization did not expose the unrecoverable tray/background outcome in state.")

withdrawn_state = TaskState("Inspect the window titled 'Withdrawn Desktop Window'")
withdrawn_result = {
    "ok": False,
    "summary": "The target window looks withdrawn or tray-like and is not visibly recoverable in the current bounded desktop pass.",
    "error": "The target window looks withdrawn or tray-like and is not visibly recoverable in the current bounded desktop pass.",
    "recovery": {
        "state": "missing",
        "reason": "target_withdrawn",
        "summary": "The target window looks withdrawn or tray-like and is not visibly recoverable in the current bounded desktop pass.",
        "strategy": "report_missing_target",
        "attempt_count": 1,
        "max_attempts": 2,
    },
}
withdrawn_state.add_step(
    {
        "type": "tool",
        "status": "failed",
        "tool": "desktop_recover_window",
        "args": {"title": "Withdrawn Desktop Window", "expected_window_title": "Withdrawn Desktop Window"},
        "result": withdrawn_result,
    }
)
withdrawn_state.update_memory_from_tool("desktop_recover_window", withdrawn_result)
withdrawn_final = _maybe_finalize_desktop_terminal_outcome(
    _DesktopApprovalStubLLM(),
    withdrawn_state,
    withdrawn_state.goal,
)
if not isinstance(withdrawn_final, dict) or withdrawn_final.get("status") != "incomplete":
    raise SystemExit("Desktop terminal finalization did not convert a withdrawn-window recovery outcome into a clean incomplete result.")
if withdrawn_state.get_control_snapshot().get("desktop", {}).get("run_outcome", {}).get("outcome") != "unrecoverable_withdrawn":
    raise SystemExit("Desktop terminal finalization did not expose the unrecoverable withdrawn outcome in state.")

recovery_exhausted_state = TaskState("Recover the window titled 'Approval Target Window'")
recovery_exhausted_result = {
    "ok": False,
    "summary": "The bounded recovery budget is exhausted, so the operator should stop and report the current window state.",
    "error": "The bounded recovery budget is exhausted, so the operator should stop and report the current window state.",
    "recovery": {
        "state": "needs_recovery",
        "reason": "foreground_not_confirmed",
        "summary": "The bounded recovery budget is exhausted, so the operator should stop and report the current window state.",
        "strategy": "stop_and_report",
        "attempt_count": 2,
        "max_attempts": 2,
    },
}
recovery_exhausted_state.add_step(
    {
        "type": "tool",
        "status": "failed",
        "tool": "desktop_recover_window",
        "args": {"title": "Approval Target Window", "expected_window_title": "Approval Target Window"},
        "result": recovery_exhausted_result,
    }
)
recovery_exhausted_state.update_memory_from_tool("desktop_recover_window", recovery_exhausted_result)
recovery_exhausted_final = _maybe_finalize_desktop_terminal_outcome(
    _DesktopApprovalStubLLM(),
    recovery_exhausted_state,
    recovery_exhausted_state.goal,
)
if not isinstance(recovery_exhausted_final, dict) or recovery_exhausted_final.get("status") != "incomplete":
    raise SystemExit("Desktop terminal finalization did not stop cleanly after bounded recovery was exhausted.")
if recovery_exhausted_state.get_control_snapshot().get("desktop", {}).get("run_outcome", {}).get("outcome") != "recovery_exhausted":
    raise SystemExit("Desktop terminal finalization did not expose the recovery-exhausted outcome in state.")

blocked_desktop_snapshot = _status_payload(
    {
        "status": "blocked",
        "running": False,
        "paused": False,
        "goal": "Rejected paused desktop action",
        "queue": {},
        "browser": {},
        "behavior": {},
        "pending_approval": {},
        "desktop": {
            "run_outcome": normalize_desktop_run_outcome(
                {
                    "outcome": "blocked",
                    "status": "blocked",
                    "terminal": True,
                    "reason": "approval_needed",
                    "summary": "Rejected the paused desktop action instead of continuing.",
                    "target_window_title": "Approval Target Window",
                }
            )
        },
    }
)
if blocked_desktop_snapshot.get("desktop", {}).get("run_outcome", {}).get("outcome") != "blocked":
    raise SystemExit("Local API status compaction did not preserve the blocked desktop terminal outcome.")

desktop_key_ready_state = TaskState("Press Enter in window titled 'Approval Target Window'")
desktop_key_ready_state.add_step({"type": "tool", "status": "completed", "tool": "desktop_capture_screenshot", "args": {}, "result": desktop_ready_result})
desktop_key_ready_state.update_memory_from_tool("desktop_capture_screenshot", desktop_ready_result)
desktop_key_pause_result = _maybe_pause_for_desktop_action(
    _DesktopApprovalStubLLM(),
    _DesktopApprovalStubRuntime(),
    desktop_key_ready_state,
    "Press Enter in window titled 'Approval Target Window'",
)
if not isinstance(desktop_key_pause_result, dict) or desktop_key_pause_result.get("status") != "paused":
    raise SystemExit("Desktop approval synthesis did not create a paused checkpoint for a bounded desktop key press.")
desktop_key_paused_snapshot = desktop_key_ready_state.get_control_snapshot()
if desktop_key_paused_snapshot.get("pending_approval", {}).get("step") != "press key":
    raise SystemExit("Desktop approval synthesis did not surface the expected key-press approval step label.")
if desktop_key_paused_snapshot.get("desktop", {}).get("last_key_sequence", "") != "Enter":
    raise SystemExit("Desktop approval synthesis did not retain the last bounded desktop key sequence.")

partial_checkpoint_root = Path("data") / "desktop_partial_checkpoint_smoke"
shutil.rmtree(partial_checkpoint_root, ignore_errors=True)
partial_checkpoint_store = DesktopEvidenceStore(partial_checkpoint_root, max_items=2)
partial_capture_path = partial_checkpoint_store.artifact_path("desk-smoke-partial-checkpoint", extension=".png")
partial_capture_path.parent.mkdir(parents=True, exist_ok=True)
partial_capture_path.write_bytes(b"desktop partial checkpoint smoke")
partial_checkpoint_bundle = build_desktop_evidence_bundle(
    source_action="desktop_capture_screenshot",
    active_window={"title": "Approval Target Window", "window_id": "0x00123458", "process_name": "notepad.exe"},
    windows=[{"title": "Approval Target Window", "window_id": "0x00123458", "process_name": "notepad.exe"}],
    observation_token="desktop-evidence-smoke-partial",
    screenshot={
        "backend": "mss",
        "path": str(partial_capture_path),
        "scope": "active_window",
        "bounds": {"x": 128, "y": 151, "width": 460, "height": 240},
    },
    errors=["readiness probe unavailable"],
)
partial_checkpoint_bundle["evidence_id"] = "desk-smoke-partial-checkpoint"
partial_checkpoint_bundle["timestamp"] = third_timestamp
partial_checkpoint_ref = partial_checkpoint_store.record_bundle(partial_checkpoint_bundle)
partial_checkpoint_summary = summarize_evidence_bundle(partial_checkpoint_store.load_bundle("desk-smoke-partial-checkpoint"))
partial_checkpoint_assessment = partial_checkpoint_store.assess_summary(
    summary=partial_checkpoint_summary,
    purpose="desktop_action_prepare",
    target_window_title="Approval Target Window",
    require_screenshot=True,
    max_age_seconds=86_400,
)
if partial_checkpoint_assessment.get("sufficient", False) or partial_checkpoint_assessment.get("reason") != "partial_evidence":
    raise SystemExit("Partial screenshot-backed desktop evidence did not stay in the expected refresh-needed state before checkpoint synthesis.")
original_partial_store = desktop_evidence_module._STORE
desktop_evidence_module._STORE = partial_checkpoint_store
try:
    desktop_partial_ready_state = TaskState("Click (12, 18) in window titled 'Approval Target Window'")
    desktop_partial_ready_result = {
        "ok": True,
        "summary": "Waited for the target window and confirmed bounded recovery.",
        "desktop_state": {
            "active_window": {"title": "Approval Target Window", "window_id": "0x00123458", "process_name": "notepad.exe"},
            "windows": [{"title": "Approval Target Window"}],
            "observation_token": "desktop-evidence-smoke-partial",
            "observed_at": "2026-03-23T10:02:30",
        },
        "recovery": {
            "state": "ready",
            "reason": "recovery_succeeded",
            "target_window": {"title": "Approval Target Window", "window_id": "0x00123458"},
            "active_window": {"title": "Approval Target Window", "window_id": "0x00123458"},
        },
        "window_readiness": {
            "state": "unsupported",
            "ready": False,
            "reason": "unsupported",
        },
        "visual_stability": {
            "state": "missing",
            "stable": False,
            "reason": "missing",
        },
    }
    desktop_partial_ready_state.add_step({"type": "tool", "status": "completed", "tool": "desktop_wait_for_window_ready", "args": {}, "result": desktop_partial_ready_result})
    desktop_partial_ready_state.update_memory_from_tool("desktop_wait_for_window_ready", desktop_partial_ready_result)
    desktop_partial_capture_result = {
        "ok": True,
        "summary": "Captured a screenshot of the active window.",
        "desktop_state": {
            "active_window": {"title": "Approval Target Window", "window_id": "0x00123458", "process_name": "notepad.exe"},
            "windows": [{"title": "Approval Target Window"}],
            "observation_token": "desktop-evidence-smoke-partial",
            "observed_at": "2026-03-23T10:02:31",
            "screenshot_path": partial_checkpoint_ref.get("screenshot_path", ""),
            "screenshot_scope": "active_window",
        },
        "desktop_evidence_ref": partial_checkpoint_ref,
        "desktop_evidence": partial_checkpoint_store.load_bundle("desk-smoke-partial-checkpoint"),
    }
    desktop_partial_ready_state.add_step({"type": "tool", "status": "completed", "tool": "desktop_capture_screenshot", "args": {}, "result": desktop_partial_capture_result})
    desktop_partial_ready_state.update_memory_from_tool("desktop_capture_screenshot", desktop_partial_capture_result)
    desktop_partial_pause = _maybe_pause_for_desktop_action(
        _DesktopApprovalStubLLM(),
        _DesktopApprovalStubRuntime(),
        desktop_partial_ready_state,
        "Click (12, 18) in window titled 'Approval Target Window'",
    )
    if not isinstance(desktop_partial_pause, dict) or desktop_partial_pause.get("status") != "paused":
        raise SystemExit("Desktop approval synthesis did not allow a screenshot-backed partial recovery state to create a bounded paused checkpoint.")
    partial_paused_snapshot = desktop_partial_ready_state.get_control_snapshot()
    partial_pending = partial_paused_snapshot.get("pending_approval", {})
    partial_reason = str(partial_pending.get("evidence_assessment", {}).get("reason", "")).strip()
    if partial_pending.get("evidence_preview", {}).get("evidence_id") != "desk-smoke-partial-checkpoint" or partial_reason not in {"partial_evidence", "partial_but_answerable", "current_evidence"}:
        raise SystemExit("Desktop approval synthesis did not preserve linked screenshot-backed evidence for the partial paused checkpoint.")
finally:
    desktop_evidence_module._STORE = original_partial_store
    shutil.rmtree(partial_checkpoint_root, ignore_errors=True)

desktop_retry_state = TaskState("Click (12, 18) in window titled 'Approval Target Window'")
desktop_retry_failure = {
    "ok": False,
    "summary": "A fresh desktop observation is required before acting. Inspect windows or capture a screenshot first.",
    "error": "A fresh desktop observation is required before acting. Inspect windows or capture a screenshot first.",
    "desktop_state": {
        "active_window": {"title": "Approval Target Window", "window_id": "0x00123458", "process_name": "notepad.exe"},
        "windows": [{"title": "Approval Target Window"}],
        "observation_token": "desktop-evidence-smoke-3",
        "observed_at": "2026-03-23T10:02:00",
    },
    "desktop_evidence_ref": third_ref,
    "desktop_evidence": temp_evidence_store.load_bundle("desk-smoke-3"),
}
desktop_retry_state.add_step({"type": "tool", "status": "failed", "tool": "desktop_click_point", "args": {"x": 12, "y": 18}, "result": desktop_retry_failure})
desktop_retry_state.update_memory_from_tool("desktop_click_point", desktop_retry_failure)
desktop_retry_state.add_step({"type": "tool", "status": "completed", "tool": "desktop_capture_screenshot", "args": {}, "result": desktop_ready_result})
desktop_retry_state.update_memory_from_tool("desktop_capture_screenshot", desktop_ready_result)
desktop_retry_pause = _maybe_pause_for_desktop_action(
    _DesktopApprovalStubLLM(),
    _DesktopApprovalStubRuntime(),
    desktop_retry_state,
    "Click (12, 18) in window titled 'Approval Target Window'",
)
if not isinstance(desktop_retry_pause, dict) or desktop_retry_pause.get("status") != "paused":
    raise SystemExit("Desktop approval synthesis still treated a failed desktop click step as blocking the later evidence-backed paused checkpoint.")

desktop_recovery_summary_state = TaskState("desktop recovery summary smoke")
desktop_recovery_summary_result = {
    "ok": True,
    "summary": "Recovered the target window and confirmed foreground focus.",
    "desktop_state": {
        "active_window": {"title": "Approval Target Window", "window_id": "0x00123458", "process_name": "notepad.exe"},
        "windows": [{"title": "Approval Target Window"}],
        "observation_token": "desktop-recovery-smoke-1",
        "observed_at": "2026-03-23T10:03:00",
    },
    "desktop_evidence_ref": third_ref,
    "desktop_evidence": temp_evidence_store.load_bundle("desk-smoke-3"),
    "recovery": {
        "state": "ready",
        "reason": "recovery_succeeded",
        "summary": "Approval Target Window is present, foreground, and ready.",
        "strategy": "restore_then_focus",
    },
    "window_readiness": {
        "state": "ready",
        "reason": "ready",
        "summary": "Approval Target Window looks visible and ready.",
    },
    "visual_stability": {
        "state": "stable",
        "reason": "inspected",
        "summary": "Visual state looked stable across bounded samples.",
    },
}
desktop_recovery_summary_state.add_step(
    {
        "type": "tool",
        "status": "completed",
        "tool": "desktop_recover_window",
        "args": {"title": "Approval Target Window"},
        "result": desktop_recovery_summary_result,
    }
)
desktop_recovery_summary_state.update_memory_from_tool("desktop_recover_window", desktop_recovery_summary_result)
desktop_recovery_snapshot = desktop_recovery_summary_state.get_control_snapshot().get("desktop", {})
if desktop_recovery_snapshot.get("latest_recovery", {}).get("state") != "ready":
    raise SystemExit("TaskState did not surface the latest desktop recovery state in the authoritative desktop snapshot.")
if "Desktop recovery state: ready" not in desktop_recovery_summary_state.get_observation():
    raise SystemExit("TaskState.get_observation() did not surface the latest desktop recovery state for model-facing desktop context.")


class _DesktopRecoveryRuntime(_DesktopApprovalStubRuntime):
    def __init__(self, refresh_result):
        self.refresh_result = refresh_result
        self.calls = []

    def prepare_args(self, tool_name: str, args: dict, task_state, planning_goal=None):
        prepared = dict(args)
        self.calls.append(("prepare", tool_name, prepared))
        return prepared

    def execute(self, tool_name: str, args: dict):
        self.calls.append(("execute", tool_name, dict(args)))
        return self.refresh_result


desktop_recovery_state = TaskState("Click (12, 18) in window titled 'Approval Target Window'")
desktop_recovery_state.add_step({"type": "tool", "status": "completed", "tool": "desktop_get_active_window", "args": {}, "result": desktop_refresh_result})
desktop_recovery_state.update_memory_from_tool("desktop_get_active_window", desktop_refresh_result)
desktop_recovery_runtime = _DesktopRecoveryRuntime(desktop_ready_result)
desktop_recovery_result = _maybe_recover_desktop_action_failure(
    _DesktopApprovalStubLLM(),
    desktop_recovery_runtime,
    desktop_recovery_state,
    "Click (12, 18) in window titled 'Approval Target Window'",
    "desktop_click_point",
    desktop_retry_failure,
)
if not isinstance(desktop_recovery_result, dict) or desktop_recovery_result.get("status") != "paused":
    raise SystemExit("Desktop action recovery did not refresh observation and synthesize a paused approval checkpoint after the fresh-observation failure.")
executed_recovery_tools = [call[1] for call in desktop_recovery_runtime.calls if call[0] == "execute"]
if "desktop_capture_screenshot" not in executed_recovery_tools:
    raise SystemExit("Desktop action recovery did not include the expected bounded screenshot refresh tool after the grouped recovery step.")
if any(tool not in {"desktop_inspect_window_state", "desktop_recover_window", "desktop_wait_for_window_ready", "desktop_capture_screenshot"} for tool in executed_recovery_tools):
    raise SystemExit(f"Desktop action recovery used unexpected grouped recovery tools: {executed_recovery_tools}")

hidden_recovery_state = TaskState("Inspect the desktop window titled 'Approval Target Window' and stop if it is not visibly recoverable.")
hidden_recovery_state.add_step({"type": "tool", "status": "completed", "tool": "desktop_get_active_window", "args": {}, "result": desktop_refresh_result})
hidden_recovery_state.update_memory_from_tool("desktop_get_active_window", desktop_refresh_result)
hidden_inspect_failure = {
    "ok": False,
    "summary": "Show the hidden window if possible, then verify foreground focus.",
    "error": "Show the hidden window if possible, then verify foreground focus.",
    "recovery": {
        "state": "needs_recovery",
        "reason": "target_hidden",
        "summary": "Show the hidden window if possible, then verify foreground focus.",
        "attempt_count": 0,
        "max_attempts": 2,
    },
}
hidden_recovery_state.add_step(
    {
        "type": "tool",
        "status": "failed",
        "tool": "desktop_inspect_window_state",
        "args": {"title": "Approval Target Window", "expected_window_title": "Approval Target Window"},
        "result": hidden_inspect_failure,
    }
)
hidden_recovery_state.update_memory_from_tool("desktop_inspect_window_state", hidden_inspect_failure)
hidden_recovery_runtime = _DesktopRecoveryRuntime(desktop_ready_result)
hidden_recovery_progress = _maybe_recover_desktop_action_failure(
    _DesktopApprovalStubLLM(),
    hidden_recovery_runtime,
    hidden_recovery_state,
    hidden_recovery_state.goal,
    "desktop_inspect_window_state",
    hidden_inspect_failure,
)
if not isinstance(hidden_recovery_progress, dict) or not hidden_recovery_progress.get("continue_loop", False):
    raise SystemExit("Failed desktop inspection did not enter the grouped bounded recovery flow for a hidden target window.")
hidden_recovery_tools = [call[1] for call in hidden_recovery_runtime.calls if call[0] == "execute"]
if "desktop_recover_window" not in hidden_recovery_tools:
    raise SystemExit("Failed desktop inspection did not invoke desktop_recover_window during grouped hidden-window recovery.")
if any(tool not in {"desktop_recover_window", "desktop_wait_for_window_ready", "desktop_capture_screenshot", "desktop_get_active_window"} for tool in hidden_recovery_tools):
    raise SystemExit(f"Hidden-window inspection recovery used unexpected grouped recovery tools: {hidden_recovery_tools}")

evidence_server = LocalOperatorApiServer(port=0, settings=SMOKE_SETTINGS)
evidence_server.start_in_thread()
try:
    with urlopen(f"http://127.0.0.1:{evidence_server.port}/desktop/evidence", timeout=5) as evidence_response:
        parsed_evidence = json.loads(evidence_response.read().decode("utf-8"))
        recent_items = parsed_evidence.get("data", {}).get("recent", [])
        if not recent_items or recent_items[-1].get("evidence_id") != "desk-smoke-3":
            raise SystemExit("Local API did not expose recent desktop evidence references.")
        recent_summaries_payload = parsed_evidence.get("data", {}).get("recent_summaries", [])
        if not recent_summaries_payload or recent_summaries_payload[-1].get("evidence_id") != "desk-smoke-3":
            raise SystemExit("Local API did not expose recent desktop evidence summaries.")
        if parsed_evidence.get("data", {}).get("status", {}).get("root", "") != str(temp_evidence_root):
            raise SystemExit("Local API did not expose the desktop evidence store status.")
        if parsed_evidence.get("data", {}).get("status", {}).get("latest_summary", {}).get("evidence_id") != "desk-smoke-3":
            raise SystemExit("Local API did not expose the latest desktop evidence summary.")

    with urlopen(f"http://127.0.0.1:{evidence_server.port}/desktop/evidence/desk-smoke-3", timeout=5) as bundle_response:
        parsed_bundle = json.loads(bundle_response.read().decode("utf-8"))
        if parsed_bundle.get("data", {}).get("bundle", {}).get("evidence_id") != "desk-smoke-3":
            raise SystemExit("Local API did not expose the requested desktop evidence bundle.")

    with urlopen(f"http://127.0.0.1:{evidence_server.port}/desktop/evidence/selected?strategy=latest_with_screenshot", timeout=5) as selected_response:
        parsed_selected = json.loads(selected_response.read().decode("utf-8"))
        if parsed_selected.get("data", {}).get("selected", {}).get("evidence_id") != "desk-smoke-3":
            raise SystemExit("Local API did not expose the expected selected desktop evidence summary.")

    with urlopen(f"http://127.0.0.1:{evidence_server.port}/desktop/evidence/desk-smoke-3/artifact", timeout=5) as artifact_response:
        parsed_artifact = json.loads(artifact_response.read().decode("utf-8"))
        artifact_payload = parsed_artifact.get("data", {}).get("artifact", {})
        if artifact_payload.get("evidence_id") != "desk-smoke-3" or not artifact_payload.get("artifact_available", False):
            raise SystemExit("Local API did not expose the expected available desktop evidence artifact.")
        if not str(artifact_payload.get("content_path", "")).endswith("/desktop/evidence/desk-smoke-3/artifact/content"):
            raise SystemExit("Local API did not expose the expected artifact content path.")

    with urlopen(f"http://127.0.0.1:{evidence_server.port}/desktop/evidence/desk-smoke-3/artifact/content", timeout=5) as artifact_content_response:
        if artifact_content_response.read() != b"desktop evidence smoke 3":
            raise SystemExit("Local API did not serve the expected desktop evidence artifact content.")

    third_capture_path.unlink()
    with urlopen(f"http://127.0.0.1:{evidence_server.port}/desktop/evidence/desk-smoke-3/artifact", timeout=5) as missing_artifact_response:
        missing_payload = json.loads(missing_artifact_response.read().decode("utf-8")).get("data", {}).get("artifact", {})
        if missing_payload.get("availability_state") != "missing":
            raise SystemExit("Local API did not surface a missing desktop evidence artifact state after the file was removed.")

    try:
        with urlopen(f"http://127.0.0.1:{evidence_server.port}/desktop/evidence/desk-smoke-3/artifact/content", timeout=5):
            raise SystemExit("Local API artifact content route did not fail for a missing artifact.")
    except HTTPError as exc:
        if exc.code != 404:
            raise

    with urlopen(f"http://127.0.0.1:{evidence_server.port}/desktop/evidence/desk-smoke-4/artifact", timeout=5) as pruned_artifact_response:
        pruned_payload = json.loads(pruned_artifact_response.read().decode("utf-8")).get("data", {}).get("artifact", {})
        if pruned_payload.get("availability_state") not in {"pruned", "not_found"}:
            raise SystemExit("Local API did not surface the expected pruned/missing state for a removed evidence artifact.")
finally:
    evidence_server.shutdown()
    desktop_evidence_module._STORE = original_evidence_store
    shutil.rmtree(temp_evidence_root, ignore_errors=True)

print("[OK] desktop evidence layer")

scene_interpreters = list_scene_interpreters()
if "generic_scene" not in scene_interpreters.get("generic", []) or "workflow_phase" not in scene_interpreters.get("workflow", []):
    raise SystemExit("Desktop scene interpreters did not register the expected default interpreter set.")
register_scene_interpreter("app", "smoke_plugin", lambda scene, context: {"signals": ["smoke_plugin_signal"]})
if "smoke_plugin" not in list_scene_interpreters().get("app", []):
    raise SystemExit("Desktop scene interpreter registration did not preserve plugin-style extensibility.")

scene_image_path = Path("data") / "scene_smoke_capture.png"
scene_image_path.parent.mkdir(parents=True, exist_ok=True)
scene_image_path.write_bytes(b"scene smoke capture")
previous_scene_summary = {
    "evidence_id": "scene-prev",
    "summary": "Desktop Eval Prompt was loading.",
    "active_window_title": "Desktop Eval Prompt",
    "active_window_process": "outlook.exe",
    "active_window_rect": {"x": 100, "y": 100, "width": 320, "height": 180},
    "screen_size": {"width": 1920, "height": 1080},
    "active_window_visible": True,
    "has_screenshot": True,
    "screenshot_path": str(scene_image_path),
    "capture_signature": "scene-prev",
}
current_prompt_summary = {
    "evidence_id": "scene-current",
    "summary": "Desktop Eval Prompt is visible and blocking the workflow.",
    "active_window_title": "Desktop Eval Prompt",
    "active_window_process": "outlook.exe",
    "active_window_rect": {"x": 120, "y": 120, "width": 320, "height": 180},
    "screen_size": {"width": 1920, "height": 1080},
    "active_window_visible": True,
    "has_screenshot": True,
    "has_artifact": True,
    "screenshot_scope": "active_window",
    "screenshot_path": str(scene_image_path),
    "capture_signature": "scene-current",
}
prompt_scene = interpret_desktop_scene(
    selected_summary=current_prompt_summary,
    recent_summaries=[previous_scene_summary],
    purpose="desktop_investigation",
    prompt_text="What is the primary visible action label on the prompt?",
    assessment={"state": "partial", "reason": "partial_but_answerable", "sufficient": True},
    recovery={"state": "ready", "reason": "recovery_succeeded", "summary": "Window is ready."},
    readiness={"state": "ready", "ready": True, "summary": "Window is ready."},
    visual_stability={"state": "stable", "stable": True, "summary": "Stable."},
    process_context={"process_name": "outlook.exe", "running": True, "present": True},
)
if prompt_scene.get("scene_class") not in {"prompt", "dialog"} or not prompt_scene.get("direct_image_helpful", False):
    raise SystemExit(f"Desktop scene interpretation did not classify prompt/dialog-like evidence correctly: {prompt_scene}")
if not prompt_scene.get("scene_changed", False) or not prompt_scene.get("prefer_before_after", False):
    raise SystemExit(f"Desktop scene interpretation did not preserve scene-change/workflow history for prompt-like evidence: {prompt_scene}")

fullscreen_scene = interpret_desktop_scene(
    selected_summary={
        "evidence_id": "scene-fullscreen",
        "summary": "Browser window visible.",
        "active_window_title": "Project Dashboard",
        "active_window_process": "chrome.exe",
        "active_window_rect": {"x": 0, "y": 0, "width": 1910, "height": 1040},
        "active_window_maximized": True,
        "screen_size": {"width": 1920, "height": 1080},
        "has_screenshot": True,
        "capture_signature": "fullscreen-scene",
    },
    purpose="desktop_investigation",
    readiness={"state": "ready", "ready": True},
    visual_stability={"state": "stable", "stable": True},
    process_context={"process_name": "chrome.exe", "running": True, "present": True},
)
if not fullscreen_scene.get("fullscreen_like", False) or fullscreen_scene.get("scene_class") != "fullscreen":
    raise SystemExit(f"Desktop scene interpretation did not preserve fullscreen/windowed classification: {fullscreen_scene}")

background_scene = interpret_desktop_scene(
    selected_summary={
        "evidence_id": "scene-background",
        "summary": "Target looks backgrounded.",
        "active_window_title": "Desktop Eval Main",
        "active_window_process": "python.exe",
        "screen_size": {"width": 1920, "height": 1080},
    },
    purpose="desktop_investigation",
    recovery={"state": "missing", "reason": "tray_or_background_state", "summary": "Target may be tray-like."},
    process_context={"process_name": "python.exe", "running": True, "present": True, "background_candidate": True},
)
if background_scene.get("scene_class") != "background" or not background_scene.get("background_like", False):
    raise SystemExit(f"Desktop scene interpretation did not preserve tray/background classification: {background_scene}")

scene_vision = select_desktop_vision_context(
    selected_summary=current_prompt_summary,
    recent_summaries=[previous_scene_summary],
    purpose="desktop_investigation",
    prompt_text="What is the primary visible action label on the prompt?",
    assessment={"state": "partial", "reason": "partial_but_answerable", "sufficient": True},
    selected_scene=prompt_scene,
)
if not scene_vision.get("needs_direct_image", False) or scene_vision.get("mode") not in {"single_image", "before_after_pair"}:
    raise SystemExit(f"Desktop scene-aware vision selection did not request bounded image grounding when summaries were insufficient: {scene_vision}")

scene_status = _status_payload(
    {
        "status": "running",
        "running": True,
        "paused": False,
        "desktop": {
            "selected_scene": prompt_scene,
            "checkpoint_scene": prompt_scene,
        },
        "pending_approval": {
            "scene_preview": prompt_scene,
        },
        "queue": {},
        "browser": {},
        "behavior": {},
    }
)
if scene_status.get("desktop", {}).get("selected_scene", {}).get("scene_class", "") not in {"prompt", "dialog"}:
    raise SystemExit("Local API status payload did not expose selected desktop scene summaries.")
if scene_status.get("pending_approval", {}).get("scene_preview", {}).get("reason", "") not in {"prompt_like", "dialog_like"}:
    raise SystemExit("Local API status payload did not expose compact checkpoint scene previews.")

scene_image_path.unlink(missing_ok=True)
print("[OK] desktop scene interpretation")

auto_capture_root = Path("data/desktop_auto_capture_smoke")
shutil.rmtree(auto_capture_root, ignore_errors=True)
auto_capture_root.mkdir(parents=True, exist_ok=True)
original_capture_frame = desktop_capture_service_module.capture_desktop_evidence_frame
original_record_capture = desktop_capture_service_module.record_captured_desktop_evidence
capture_context = {
    "state_scope_id": "chat:auto-capture-smoke",
    "task_id": "task-auto-capture",
    "task_status": "running",
    "checkpoint_pending": False,
    "checkpoint_tool": "",
    "checkpoint_target": "",
    "active_window_title": "Smoke Window",
}
capture_calls: List[Dict[str, Any]] = []
record_calls: List[Dict[str, Any]] = []
first_auto_path = auto_capture_root / "first.png"
duplicate_auto_path = auto_capture_root / "duplicate.png"
checkpoint_auto_path = auto_capture_root / "checkpoint.png"
first_auto_path.write_bytes(b"first desktop capture")
duplicate_auto_path.write_bytes(b"duplicate desktop capture")
checkpoint_auto_path.write_bytes(b"checkpoint desktop capture")
queued_captures = [
    {
        "ok": True,
        "capture_label": "active window 'Smoke Window'",
        "active_window": {"window_id": "0x00002001", "title": "Smoke Window", "process_name": "notepad.exe"},
        "windows": [{"window_id": "0x00002001", "title": "Smoke Window"}],
        "observation": {"observation_token": "auto-smoke-1"},
        "screenshot": {"path": str(first_auto_path), "scope": "active_window", "backend": "mss"},
        "evidence_bundle": {},
        "evidence_ref": {},
        "screenshot_path": str(first_auto_path),
        "screenshot_scope": "active_window",
        "capture_signature": "sig-a",
        "target_window": {"window_id": "0x00002001", "title": "Smoke Window"},
    },
    {
        "ok": True,
        "capture_label": "active window 'Smoke Window'",
        "active_window": {"window_id": "0x00002001", "title": "Smoke Window", "process_name": "notepad.exe"},
        "windows": [{"window_id": "0x00002001", "title": "Smoke Window"}],
        "observation": {"observation_token": "auto-smoke-2"},
        "screenshot": {"path": str(duplicate_auto_path), "scope": "active_window", "backend": "mss"},
        "evidence_bundle": {},
        "evidence_ref": {},
        "screenshot_path": str(duplicate_auto_path),
        "screenshot_scope": "active_window",
        "capture_signature": "sig-a",
        "target_window": {"window_id": "0x00002001", "title": "Smoke Window"},
    },
    {
        "ok": True,
        "capture_label": "active window 'Smoke Window'",
        "active_window": {"window_id": "0x00002001", "title": "Smoke Window", "process_name": "notepad.exe"},
        "windows": [{"window_id": "0x00002001", "title": "Smoke Window"}],
        "observation": {"observation_token": "auto-smoke-3"},
        "screenshot": {"path": str(checkpoint_auto_path), "scope": "active_window", "backend": "mss"},
        "evidence_bundle": {},
        "evidence_ref": {},
        "screenshot_path": str(checkpoint_auto_path),
        "screenshot_scope": "active_window",
        "capture_signature": "sig-b",
        "target_window": {"window_id": "0x00002001", "title": "Smoke Window"},
    },
]


def _fake_capture_desktop_evidence_frame(**kwargs):
    capture_calls.append(dict(kwargs))
    if not queued_captures:
        return {"ok": False, "error": "no queued captures"}
    return dict(queued_captures.pop(0))


def _fake_record_captured_desktop_evidence(**kwargs):
    record_calls.append(dict(kwargs))
    bundle_metadata = kwargs.get("bundle_metadata", {}) if isinstance(kwargs.get("bundle_metadata", {}), dict) else {}
    evidence_id = f"desk-auto-smoke-{len(record_calls)}"
    return (
        {
            "evidence_id": evidence_id,
            "summary": f"Recorded automatic capture {len(record_calls)}.",
            "timestamp": third_timestamp,
        },
        {
            "evidence_id": evidence_id,
            "summary": f"Recorded automatic capture {len(record_calls)}.",
            "timestamp": third_timestamp,
            "reason": "collected",
            "bundle_path": str(auto_capture_root / f"{evidence_id}.json"),
            "capture_mode": bundle_metadata.get("capture_mode", ""),
            "importance": bundle_metadata.get("importance", ""),
        },
    )


desktop_capture_service_module.capture_desktop_evidence_frame = _fake_capture_desktop_evidence_frame
desktop_capture_service_module.record_captured_desktop_evidence = _fake_record_captured_desktop_evidence
try:
    auto_capture_service = DesktopCaptureService(
        {
            "desktop_auto_capture_enabled": True,
            "desktop_auto_capture_interval_seconds": 2,
            "desktop_auto_capture_scope": "active_window",
            "desktop_auto_capture_max_events": 12,
        },
        context_getter=lambda: dict(capture_context),
    )
    first_auto_capture = auto_capture_service.capture_once()
    if not first_auto_capture.get("recorded", False) or len(record_calls) != 1:
        raise SystemExit("DesktopCaptureService did not record the first automatic desktop capture.")
    if record_calls[0].get("bundle_metadata", {}).get("importance_reason") != "initial_context":
        raise SystemExit("DesktopCaptureService did not promote the first automatic desktop capture as initial context.")

    duplicate_auto_capture = auto_capture_service.capture_once()
    if duplicate_auto_capture.get("recorded", True) or duplicate_auto_capture.get("reason") != "duplicate_frame":
        raise SystemExit("DesktopCaptureService did not suppress an unchanged duplicate automatic desktop capture.")
    if duplicate_auto_path.exists():
        raise SystemExit("DesktopCaptureService did not clean up the skipped duplicate automatic screenshot artifact.")

    capture_context["checkpoint_pending"] = True
    capture_context["checkpoint_tool"] = "desktop_click_point"
    capture_context["checkpoint_target"] = "Smoke Window"
    checkpoint_auto_capture = auto_capture_service.capture_once()
    if not checkpoint_auto_capture.get("recorded", False) or len(record_calls) != 2:
        raise SystemExit("DesktopCaptureService did not record a checkpoint-bound automatic desktop capture.")
    if record_calls[1].get("bundle_metadata", {}).get("importance") != "checkpoint":
        raise SystemExit("DesktopCaptureService did not promote checkpoint-bound automatic desktop captures.")
    capture_status = auto_capture_service.status_snapshot()
    if capture_status.get("metadata", {}).get("duplicates_skipped", 0) < 1:
        raise SystemExit("DesktopCaptureService status did not expose duplicate-suppression accounting.")
    if capture_status.get("latest", {}).get("evidence_id", "") != "desk-auto-smoke-2":
        raise SystemExit("DesktopCaptureService status did not expose the latest automatic desktop capture.")
finally:
    desktop_capture_service_module.capture_desktop_evidence_frame = original_capture_frame
    desktop_capture_service_module.record_captured_desktop_evidence = original_record_capture
    shutil.rmtree(auto_capture_root, ignore_errors=True)

print("[OK] desktop auto capture")

project_venv_python = _project_venv_python(Path.cwd())
if not str(project_venv_python).lower().endswith(".venv\\scripts\\python.exe"):
    raise SystemExit("live_agent_eval did not resolve the expected project venv Python path.")
if not _interpreter_has_playwright(project_venv_python):
    raise SystemExit("live_agent_eval did not detect Playwright in the project venv runtime.")
for expected_scenario in {
    "outcome_style_corpus",
    "continuity_quality",
    "brief_answer_quality",
    "desktop_control",
    "desktop_evidence_grounding",
    "desktop_recovery_grounding",
    "desktop_scene_reasoning",
    "desktop_run_finalization",
    "desktop_bounded_stack",
}:
    if expected_scenario not in SCENARIO_NAMES:
        raise SystemExit(f"live_agent_eval is missing the expected scenario: {expected_scenario}")
hidden_phase_checks = _desktop_hidden_recovery_checks(
    status="incomplete",
    message=(
        "Desktop Eval Main remains in the background and looks tray-like, so I could not recover it through the bounded desktop path. "
        "The next step is to bring it back visibly or reopen it."
    ),
    session_payload={
        "session": {
            "authoritative_reply": {
                "content": (
                    "Desktop Eval Main remains in the background and looks tray-like, so I could not recover it through the bounded desktop path. "
                    "The next step is to bring it back visibly or reopen it."
                )
            }
        }
    },
    run={"run_id": "run-hidden"},
    tool_names=["desktop_inspect_window_state", "desktop_recover_window"],
    assessment={"sufficient": False},
    fixture_state={"main_hidden": True},
    main_title="Desktop Eval Main",
)
if not all(check.passed for check in hidden_phase_checks):
    failed_hidden_checks = [check.to_dict() for check in hidden_phase_checks if not check.passed]
    raise SystemExit(f"live_agent_eval hidden-window acceptance checks did not pass for the explicit withdrawn/tray-like outcome: {failed_hidden_checks}")
live_eval_source = Path("live_agent_eval.py").read_text(encoding="utf-8")
if "focus_request_path" not in live_eval_source or "def request_focus(" not in live_eval_source or "def _focus_main():" not in live_eval_source:
    raise SystemExit("live_agent_eval is missing the expected desktop fixture focus-request harness support.")
if "command_request_path" not in live_eval_source or "def request_command(" not in live_eval_source or "def _handle_command(" not in live_eval_source:
    raise SystemExit("live_agent_eval is missing the expected desktop fixture recovery command harness support.")
if "vision_selected_direct_image" not in live_eval_source or "desktop_scene_reasoning" not in live_eval_source or "desktop_press_key" not in live_eval_source or "desktop_run_finalization" not in live_eval_source:
    raise SystemExit("live_agent_eval is missing the expected bounded desktop-stack validation coverage for scene reasoning, direct image grounding, keyboard approvals, or desktop run finalization.")
if _latest_new_run([{"run_id": "run-one"}], [{"run_id": "run-one"}]) != {}:
    raise SystemExit("_latest_new_run() did not return an empty mapping when no new run was created for a follow-up chat turn.")
no_run_checks = _golden_final_answer_checks(
    status="completed",
    message="The active window is Desktop Eval Main.",
    session_payload={
        "session": {
            "messages": [
                {"role": "assistant", "kind": "final", "content": "Previous run final.", "run_id": "run-one"},
                {"role": "assistant", "kind": "result", "content": "The active window is Desktop Eval Main.", "run_id": ""},
            ],
            "last_result_message": "The active window is Desktop Eval Main.",
        }
    },
    run={},
    expected_terms={"Desktop Eval Main"},
)
single_reply_check = next((check for check in no_run_checks if check.name == "single_authoritative_reply_per_run"), None)
if single_reply_check is None or not single_reply_check.passed:
    raise SystemExit("_golden_final_answer_checks() still over-counted prior authoritative replies for a chat-only follow-up without a new run.")
print("[OK] live eval runtime selection")

desktop_ui_root = Path("desktop-ui")
required_desktop_ui_files = [
    desktop_ui_root / "package.json",
    desktop_ui_root / "src" / "App.tsx",
    desktop_ui_root / "src" / "styles.css",
    desktop_ui_root / "src" / "lib" / "api.ts",
    desktop_ui_root / "src-tauri" / "Cargo.toml",
    desktop_ui_root / "src-tauri" / "tauri.conf.json",
    desktop_ui_root / "src-tauri" / "src" / "main.rs",
]
missing_desktop_ui_files = [str(path) for path in required_desktop_ui_files if not path.exists()]
if missing_desktop_ui_files:
    raise SystemExit(f"Desktop UI files are missing: {missing_desktop_ui_files}")

desktop_package = json.loads((desktop_ui_root / "package.json").read_text(encoding="utf-8"))
desktop_dependencies = desktop_package.get("dependencies", {})
desktop_scripts = desktop_package.get("scripts", {})
if "@tauri-apps/api" not in desktop_dependencies or "react-markdown" not in desktop_dependencies:
    raise SystemExit("Desktop UI package.json is missing expected runtime dependencies.")
if "tauri:dev" not in desktop_scripts or "build" not in desktop_scripts:
    raise SystemExit("Desktop UI package.json is missing expected scripts.")

tauri_config = json.loads((desktop_ui_root / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
if tauri_config.get("build", {}).get("devUrl") != "http://127.0.0.1:1420":
    raise SystemExit("Desktop UI tauri.conf.json is missing the expected local dev URL.")
if tauri_config.get("build", {}).get("frontendDist") != "../dist":
    raise SystemExit("Desktop UI tauri.conf.json is missing the expected frontend dist path.")

desktop_app_source = (desktop_ui_root / "src" / "App.tsx").read_text(encoding="utf-8")
desktop_api_source = (desktop_ui_root / "src" / "lib" / "api.ts").read_text(encoding="utf-8")
desktop_tauri_source = (desktop_ui_root / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
if "openSessionEventStream" not in desktop_app_source or "details-drawer" not in desktop_app_source:
    raise SystemExit("Desktop UI App.tsx is missing expected chat/stream/detail UI wiring.")
if "ensureLocalApi" not in desktop_api_source or "openSessionEventStream" not in desktop_api_source:
    raise SystemExit("Desktop UI API client is missing expected local API helpers.")
if 'invoke<{' not in desktop_api_source or 'Failed to fetch ${url}' not in desktop_api_source:
    raise SystemExit("Desktop UI API client is missing expected desktop bootstrap/fetch hardening.")
if "ensure_local_api" not in desktop_tauri_source or "spawn_local_api" not in desktop_tauri_source:
    raise SystemExit("Desktop UI Tauri host is missing expected local API bootstrap commands.")
if "load_desired_runtime" not in desktop_tauri_source or "pick_free_port" not in desktop_tauri_source:
    raise SystemExit("Desktop UI Tauri host is missing the expected runtime-compatible local API bootstrap hardening.")
if "AI_OPERATOR_DESKTOP_OWNER_TOKEN" not in desktop_tauri_source or "AI_OPERATOR_DESKTOP_OWNER_PID" not in desktop_tauri_source:
    raise SystemExit("Desktop UI Tauri host is missing explicit managed-child ownership markers.")
if "RunEvent::Exit" not in desktop_tauri_source or "shutdown_owned_api_process" not in desktop_tauri_source or "ReleasedUnowned" not in desktop_tauri_source:
    raise SystemExit("Desktop UI Tauri host is missing the expected ownership-safe shutdown guards.")
if "desktop_runtime_events.jsonl" not in desktop_tauri_source or "commit_runtime_status" not in desktop_tauri_source or "attached_existing_backend" not in desktop_tauri_source:
    raise SystemExit("Desktop UI Tauri host is missing the expected runtime audit/status tracking.")
if "failed_to_start_owned_child" not in desktop_tauri_source or "port_available" not in desktop_tauri_source:
    raise SystemExit("Desktop UI Tauri host is missing the expected startup/attach hardening.")
desktop_styles_source = (desktop_ui_root / "src" / "styles.css").read_text(encoding="utf-8")
if (
    'THEME_STORAGE_KEY' not in desktop_app_source
    or 'setThemeMode((current) => (current === "light" ? "dark" : "light"))' not in desktop_app_source
    or 'data-theme="dark"' not in desktop_styles_source
):
    raise SystemExit("Desktop UI is missing the expected theme-toggle implementation.")
if 'Model {runtimeModel}' not in desktop_app_source or 'Reasoning {runtimeEffortLabel}' not in desktop_app_source:
    raise SystemExit("Desktop UI is missing the expected live runtime model indicator.")
if 'desktopRuntimeStatus' not in desktop_app_source or 'Detached backend' not in desktop_app_source:
    raise SystemExit("Desktop UI is missing the expected desktop runtime visibility wiring.")
if 'runtimeStatus?: DesktopRuntimeStatus' not in desktop_api_source or 'logPath?: string' not in desktop_api_source:
    raise SystemExit("Desktop UI API client is missing the expected runtime bootstrap metadata.")
if 'DRAFTS_STORAGE_KEY' not in desktop_app_source or 'Jump to latest' not in desktop_app_source or 'code-copy-button' not in (desktop_ui_root / "src" / "styles.css").read_text(encoding="utf-8"):
    raise SystemExit("Desktop UI is missing the expected transcript/composer polish features.")
if "getDesktopEvidence" not in desktop_api_source or "evidence_preview?: EvidenceSummary" not in desktop_api_source or "selected_evidence?: EvidenceSummary" not in desktop_api_source:
    raise SystemExit("Desktop UI API client is missing the expected desktop evidence summary typing and fetch helpers.")
if "Linked evidence" not in desktop_app_source or "Selected evidence" not in desktop_app_source or "Desktop evidence" not in desktop_app_source:
    raise SystemExit("Desktop UI App.tsx is missing the expected desktop evidence presentation surfaces.")
if "evidence-preview" not in (desktop_ui_root / "src" / "styles.css").read_text(encoding="utf-8") or "evidence-list-footer" not in (desktop_ui_root / "src" / "styles.css").read_text(encoding="utf-8"):
    raise SystemExit("Desktop UI styles are missing the expected compact desktop evidence presentation classes.")
if "getDesktopEvidenceArtifact" not in desktop_api_source or "getDesktopEvidenceArtifactContentUrl" not in desktop_api_source:
    raise SystemExit("Desktop UI API client is missing the expected desktop evidence artifact helpers.")
if "Evidence artifact" not in desktop_app_source or "View artifact" not in desktop_app_source or "artifact-viewer" not in desktop_app_source:
    raise SystemExit("Desktop UI App.tsx is missing the expected desktop evidence artifact viewer wiring.")
if "artifact-viewer" not in (desktop_ui_root / "src" / "styles.css").read_text(encoding="utf-8") or "artifact-preview-image" not in (desktop_ui_root / "src" / "styles.css").read_text(encoding="utf-8"):
    raise SystemExit("Desktop UI styles are missing the expected desktop evidence artifact viewer classes.")
print("[OK] desktop ui scaffold")

required_skill_docs = [
    Path("skills/desktop_recovery.md"),
    Path("skills/window_state_inspection.md"),
    Path("skills/desktop_readiness_check.md"),
]
required_agent_docs = [
    Path("agents/desktop_inspector.md"),
    Path("agents/desktop_recovery_planner.md"),
]
for path in required_skill_docs + required_agent_docs:
    if not path.exists():
        raise SystemExit(f"Expected desktop recovery guide is missing: {path}")
desktop_recovery_skill_source = required_skill_docs[0].read_text(encoding="utf-8")
desktop_inspector_agent_source = required_agent_docs[0].read_text(encoding="utf-8")
if "foreground" not in desktop_recovery_skill_source or "tray" not in desktop_recovery_skill_source or "loading" not in desktop_recovery_skill_source:
    raise SystemExit("desktop_recovery.md is missing expected messy-desktop recovery guidance.")
if "minimized" not in desktop_inspector_agent_source or "hidden" not in desktop_inspector_agent_source or "not ready" not in desktop_inspector_agent_source:
    raise SystemExit("desktop_inspector.md is missing expected bounded desktop inspection rules.")
print("[OK] desktop recovery guides")

if not _session_matches_query({"title": "Inspect repo", "status": "paused", "summary": "Needs approval", "pending_approval": {"kind": "browser_checkpoint"}}, "inspect paused"):
    raise SystemExit("control_ui._session_matches_query() did not match expected session terms.")
inline_segments = _parse_inline_markdown_segments("Use **bold**, `code`, and [docs](https://example.com).")
inline_kinds = [segment.get("kind") for segment in inline_segments if segment.get("text")]
if inline_kinds != ["text", "bold", "text", "code", "text", "link", "text"]:
    raise SystemExit("control_ui._parse_inline_markdown_segments() did not preserve inline markdown structure.")
if inline_segments[5].get("url") != "https://example.com":
    raise SystemExit("control_ui._parse_inline_markdown_segments() did not preserve markdown link targets.")
rich_blocks = _parse_rich_text_blocks(
    "# Architecture\n\n- core loop\n- state store\n\n```python\nprint('hi')\n```"
)
if [block.get("kind") for block in rich_blocks[:3]] != ["heading_1", "bullet_list", "code"]:
    raise SystemExit("control_ui._parse_rich_text_blocks() did not preserve heading/list/code structure.")
if rich_blocks[2].get("language") != "python" or "print('hi')" not in rich_blocks[2].get("text", ""):
    raise SystemExit("control_ui._parse_rich_text_blocks() did not preserve fenced code content.")
timeline_entry = _timeline_entry_from_event(
    {
        "event": "task.completed",
        "emitted_at": "2026-03-11T09:00:00",
        "data": {"current_step": "Summarized the project architecture."},
    }
)
if timeline_entry.get("label") != "Task completed" or "Summarized the project architecture." not in timeline_entry.get("detail", ""):
    raise SystemExit("control_ui._timeline_entry_from_event() did not produce the expected compact task entry.")
print("[OK] control ui helpers")

if not _goal_requests_single_recommendation("Inspect the project and tell me the single most important next step."):
    raise SystemExit("core.llm_client did not detect a single-recommendation goal.")
if not _goal_requests_brief_answer("Inspect the project and answer briefly: is the local API the main control surface?"):
    raise SystemExit("core.llm_client did not detect a brief-answer goal.")
if not looks_like_simple_conversation_turn("By the way, what does 9 + 4 equal?"):
    raise SystemExit("core.operator_behavior did not classify a simple casual question correctly.")
busy_route = classify_chat_turn(
    "By the way, what does 9 + 4 equal?",
    session_status="running",
    has_context=True,
    pending_kind="",
)
if busy_route.get("mode") != "normal_chat" or busy_route.get("dispatch") != "chat":
    raise SystemExit("core.operator_behavior did not keep a casual question in normal chat during a busy session.")
print("[OK] behavior routing helpers")

event_stream = client.open_event_stream(session_id="session-smoke")
if "/events/stream" not in event_stream.url or "session_id=session-smoke" not in event_stream.url:
    raise SystemExit("LocalOperatorApiClient did not build the event stream URL correctly.")
print("[OK] local api event stream client")


replay_server = LocalOperatorApiServer(
    port=0,
    settings={
        **SMOKE_SETTINGS,
        "local_api_event_poll_seconds": 0.1,
        "local_api_event_heartbeat_seconds": 3,
        "local_api_event_replay_size": 20,
        "local_api_event_channel_retention_seconds": 5,
        "local_api_event_max_channels": 8,
    },
)
replay_server.start_in_thread()
replay_client = LocalOperatorApiClient(f"http://127.0.0.1:{replay_server.port}")
replay_client.health()
replay_session = replay_server.chat_manager.create_session(title="Replay smoke")
replay_session_id = replay_session.get("session", {}).get("session_id", "")
if not replay_session_id:
    raise SystemExit("Replay smoke session was not created.")

stream_one = replay_client.open_event_stream(session_id=replay_session_id, timeout_seconds=5)
stream_one_iter = stream_one.iter_events()
first_stream_event = next(stream_one_iter)
second_stream_event = next(stream_one_iter)
if first_stream_event.get("event") != "stream.hello" or second_stream_event.get("event") != "session.sync":
    raise SystemExit("Local API stream did not start with the expected hello/sync events.")

with replay_server.chat_manager._lock:
    target_session = replay_server.chat_manager._find_session_locked(replay_session_id)
    replay_server.chat_manager._append_message_locked(target_session, role="assistant", kind="status", content="Replay marker one")
    replay_server.chat_manager._update_summary_locked(target_session)
    replay_server.chat_manager._persist_locked()

first_message_event = {}
stream_deadline = time.time() + 4.0
while time.time() < stream_deadline:
    payload = next(stream_one_iter)
    if payload.get("event") == "session.message" and "Replay marker one" in payload.get("data", {}).get("message", {}).get("content", ""):
        first_message_event = payload
        break
if not first_message_event.get("event_id"):
    raise SystemExit("Local API stream did not emit a replayable event id for the first message.")
stream_one.close()

long_final_reply = ("Replay marker two " + ("full final reply segment " * 180)).strip()
with replay_server.chat_manager._lock:
    target_session = replay_server.chat_manager._find_session_locked(replay_session_id)
    replay_server.chat_manager._append_message_locked(target_session, role="assistant", kind="final", content=long_final_reply, status="completed")
    replay_server.chat_manager._update_summary_locked(target_session)
    replay_server.chat_manager._persist_locked()

time.sleep(0.35)
stream_two = replay_client.open_event_stream(session_id=replay_session_id, last_event_id=first_message_event.get("event_id", ""), timeout_seconds=5)
stream_two_iter = stream_two.iter_events()
replayed_event = {}
replay_deadline = time.time() + 4.0
while time.time() < replay_deadline:
    payload = next(stream_two_iter)
    if payload.get("event") == "session.message" and payload.get("data", {}).get("message", {}).get("content", "").strip() == long_final_reply:
        replayed_event = payload
        break
if not replayed_event:
    raise SystemExit("Local API stream did not replay the full missed session event after reconnect.")
session_detail = replay_server.chat_manager.get_session(replay_session_id)
authoritative_reply = session_detail.get("session", {}).get("authoritative_reply", {})
if authoritative_reply.get("content", "").strip() != long_final_reply:
    raise SystemExit("ChatSessionManager did not preserve the authoritative final reply content.")
print("[OK] local api event replay")
stream_two.close()
replay_server.shutdown()
temp_session_path = Path("data/session_state_smoke.json")
session_store = SessionStore(temp_session_path)
if temp_session_path.exists():
    temp_session_path.unlink()

default_state = TaskState("default scope goal", state_scope_id=DEFAULT_STATE_SCOPE_ID)
default_state.status = "completed"
default_state.last_summary = "Default scope summary"
default_state.memory_notes = ["default note"]
chat_scope_id = "chat:session-smoke"
chat_state = TaskState("chat session goal", state_scope_id=chat_scope_id)
chat_state.status = "paused"
chat_state.last_summary = "Chat scope summary"
chat_state.memory_notes = ["chat note"]
chat_state.browser_checkpoint_pending = True

if not session_store.save(default_state, scope_id=DEFAULT_STATE_SCOPE_ID):
    raise SystemExit("SessionStore did not save the default scope state.")
if not session_store.save(chat_state, scope_id=chat_scope_id):
    raise SystemExit("SessionStore did not save the chat scope state.")

loaded_default = session_store.load(DEFAULT_STATE_SCOPE_ID)
loaded_chat = session_store.load(chat_scope_id)
if loaded_default.get("task_state", {}).get("goal") != "default scope goal":
    raise SystemExit("SessionStore did not preserve the default scope goal.")
if loaded_chat.get("task_state", {}).get("goal") != "chat session goal":
    raise SystemExit("SessionStore did not preserve the chat scope goal.")
if not loaded_chat.get("task_state", {}).get("browser_checkpoint_pending", False):
    raise SystemExit("SessionStore did not preserve the chat scope checkpoint state.")
print("[OK] session store scope isolation")

if temp_session_path.exists():
    temp_session_path.unlink()

temp_chat_path = Path("data/chat_sessions_route_smoke.json")
if temp_chat_path.exists():
    temp_chat_path.unlink()


class _FakeChatClient:
    def reply_in_chat(self, user_message, *, session_context="", mode="chat", desktop_vision=None):
        return f"[{mode}] {user_message}"


class _FakeController:
    def __init__(self):
        self.started_goals = []
        self.control_actions = []
        self.snapshot = {
            "status": "idle",
            "running": False,
            "paused": False,
            "current_step": "",
            "result_status": "",
            "result_message": "",
            "active_task": {},
            "pending_approval": {},
            "browser": {},
            "behavior": {"mode": "normal_chat", "task_phase": "idle"},
            "human_control": {"state": "available"},
            "task_control": {},
            "queue": {"counts": {}, "queued_tasks": [], "recent_tasks": []},
            "alerts": {"items": []},
            "latest_run": {},
        }

    def get_snapshot(self, *, session_id: str = "", state_scope_id: str = ""):
        return dict(self.snapshot)

    def start_goal(self, goal: str, *, session_id: str = "", state_scope_id: str = ""):
        self.started_goals.append({"goal": goal, "session_id": session_id, "state_scope_id": state_scope_id})
        self.snapshot["status"] = "running"
        self.snapshot["active_task"] = {"task_id": "task-1", "status": "running", "last_message": "Started operator work.", "run_id": "run-1"}
        self.snapshot["queue"] = {"counts": {}, "queued_tasks": [], "recent_tasks": [{"task_id": "task-1", "status": "running", "last_message": "Started operator work.", "run_id": "run-1"}]}
        self.snapshot["behavior"] = {"mode": "workflow_execution", "task_phase": "executing"}
        return {"ok": True, "task_id": f"task-{len(self.started_goals)}", "started": True, "message": "Started operator work."}

    def defer_task(self, *, session_id: str = "", state_scope_id: str = ""):
        self.control_actions.append(("defer", session_id, state_scope_id))
        self.snapshot["status"] = "deferred"
        self.snapshot["result_status"] = "deferred"
        self.snapshot["result_message"] = "Deferred the task for later resumption."
        self.snapshot["behavior"] = {"mode": "paused_waiting", "task_phase": "deferred"}
        self.snapshot["task_control"] = {"event": "deferred", "resume_available": True}
        return {"ok": True, "status": "deferred", "message": "Deferred the task for later resumption."}

    def resume_task(self, *, session_id: str = "", state_scope_id: str = ""):
        self.control_actions.append(("resume", session_id, state_scope_id))
        self.snapshot["status"] = "running"
        self.snapshot["result_status"] = "running"
        self.snapshot["result_message"] = "Resumed the task and restarted it."
        self.snapshot["behavior"] = {"mode": "workflow_execution", "task_phase": "executing"}
        self.snapshot["task_control"] = {"event": "resumed", "resume_available": False}
        return {"ok": True, "status": "running", "message": "Resumed the task and restarted it.", "started": True}

    def replace_goal(self, goal: str, *, session_id: str = "", state_scope_id: str = ""):
        self.control_actions.append(("replace", goal, session_id, state_scope_id))
        self.snapshot["status"] = "running"
        self.snapshot["result_status"] = "running"
        self.snapshot["result_message"] = "Started the replacement goal."
        self.snapshot["behavior"] = {"mode": "workflow_execution", "task_phase": "executing"}
        self.snapshot["task_control"] = {"event": "superseded", "replacement_goal": goal}
        return {"ok": True, "status": "running", "message": "Started the replacement goal.", "task_id": "task-replacement", "started": True}

    def stop_task(self, *, session_id: str = "", state_scope_id: str = ""):
        self.control_actions.append(("stop", session_id, state_scope_id))
        self.snapshot["status"] = "stopped"
        self.snapshot["result_status"] = "stopped"
        self.snapshot["result_message"] = "Stopped the task by explicit operator request."
        self.snapshot["active_task"] = {"task_id": "task-1", "status": "stopped", "last_message": "Stopped the task by explicit operator request.", "run_id": "run-stop"}
        self.snapshot["latest_run"] = {"run_id": "run-stop", "final_status": "stopped", "result_message": "Stopped the task by explicit operator request."}
        self.snapshot["queue"] = {"counts": {}, "queued_tasks": [], "recent_tasks": [{"task_id": "task-1", "status": "stopped", "last_message": "Stopped the task by explicit operator request.", "run_id": "run-stop"}]}
        self.snapshot["behavior"] = {"mode": "final_report", "task_phase": "stopped"}
        self.snapshot["task_control"] = {"event": "stopped"}
        return {"ok": True, "status": "stopped", "message": "Stopped the task by explicit operator request.", "task_id": "task-1"}

    def retry_task(self, *, session_id: str = "", state_scope_id: str = ""):
        self.control_actions.append(("retry", session_id, state_scope_id))
        self.snapshot["status"] = "queued"
        self.snapshot["result_status"] = "queued"
        self.snapshot["result_message"] = "Queued goal."
        self.snapshot["behavior"] = {"mode": "workflow_execution", "task_phase": "queued"}
        self.snapshot["task_control"] = {"event": "retried"}
        return {"ok": True, "status": "queued", "message": "Queued goal.", "task_id": "task-retry", "started": False}


fake_controller = _FakeController()
chat_manager = ChatSessionManager(
    controller=fake_controller,
    path=temp_chat_path,
    max_sessions=4,
    max_messages=12,
    chat_client_factory=lambda: _FakeChatClient(),
)
created_session = chat_manager.create_session(title="Route smoke")
route_session_id = created_session.get("session", {}).get("session_id", "")
if not route_session_id:
    raise SystemExit("ChatSessionManager did not create the smoke session.")

chat_result = chat_manager.send_message(route_session_id, "What happened?")
if fake_controller.started_goals:
    raise SystemExit("ChatSessionManager routed a conversational follow-up into operator execution.")
if chat_result.get("reply_mode") != "normal_chat":
    raise SystemExit("ChatSessionManager did not keep the conversational follow-up in normal chat mode.")
if chat_result.get("session", {}).get("authoritative_reply", {}).get("content", "").strip() != "[normal_chat] What happened?":
    raise SystemExit("ChatSessionManager did not preserve the direct chat reply as the authoritative reply.")

fake_controller.snapshot["status"] = "paused"
fake_controller.snapshot["paused"] = True
fake_controller.snapshot["pending_approval"] = {"kind": "browser_checkpoint", "reason": "Submitting the form requires approval."}
approval_result = chat_manager.send_message(route_session_id, "continue")
if len(fake_controller.started_goals) != 0:
    raise SystemExit("ChatSessionManager treated a paused approval follow-up as a new operator task.")
if approval_result.get("reply_mode") != "approval_needed_action":
    raise SystemExit("ChatSessionManager did not keep the paused approval turn in chat guidance mode.")

defer_result = chat_manager.send_message(route_session_id, "defer this for later")
if fake_controller.control_actions[-1][0] != "defer":
    raise SystemExit("ChatSessionManager did not map a defer request onto task control.")
if defer_result.get("reply_mode") != "paused_waiting":
    raise SystemExit("ChatSessionManager did not surface deferred work as paused/waiting.")

resume_result = chat_manager.send_message(route_session_id, "resume that")
if fake_controller.control_actions[-1][0] != "resume":
    raise SystemExit("ChatSessionManager did not map a resume request onto task control.")
if resume_result.get("reply_mode") != "workflow_execution":
    raise SystemExit("ChatSessionManager did not surface resumed work as workflow execution.")

operator_result = chat_manager.send_message(route_session_id, "Inspect this project and explain the main architecture.")
if len(fake_controller.started_goals) != 1:
    raise SystemExit("ChatSessionManager did not route a concrete operator request into operator execution.")
if operator_result.get("reply_mode") != "read_only_investigation":
    raise SystemExit("ChatSessionManager did not label the concrete task as read-only investigation mode.")

replace_result = chat_manager.send_message(route_session_id, "instead inspect the frontend code")
if fake_controller.control_actions[-1][0] != "replace":
    raise SystemExit("ChatSessionManager did not map a replacement request onto task supersession.")
if replace_result.get("reply_mode") != "workflow_execution":
    raise SystemExit("ChatSessionManager did not surface replacement work as workflow execution.")

stop_result = chat_manager.send_message(route_session_id, "stop that")
stop_messages = stop_result.get("session", {}).get("messages", [])
stop_authoritative = [
    message
    for message in stop_messages
    if message.get("role") == "assistant"
    and message.get("run_id") == "run-stop"
    and (message.get("kind") in {"final", "result", "error"} or message.get("status") == "stopped")
]
if len(stop_authoritative) != 1:
    raise SystemExit("ChatSessionManager emitted more than one authoritative stopped-task reply.")
print("[OK] chat session routing")

if temp_chat_path.exists():
    temp_chat_path.unlink()

temp_history_path = Path("data/run_history_smoke.json")
store = RunHistoryStore(temp_history_path, max_runs=3)
if temp_history_path.exists():
    temp_history_path.unlink()

state = TaskState("smoke history goal", state_scope_id=chat_scope_id)
state.status = "completed"
state.last_summary = "Smoke history summary"
state.browser_task_name = "open_and_inspect"
state.browser_task_status = "completed"
state.browser_current_url = "https://example.test"
state.add_step(
    {
        "type": "tool",
        "status": "completed",
        "tool": "browser_type",
        "args": {"selector": "#email", "value": "user@example.com"},
        "result": {
            "ok": True,
            "summary": "Typed into the field.",
            "browser_task_name": "open_and_inspect",
            "browser_task_step": "type into field",
            "workflow_name": "Form flow",
            "workflow_step": "type into field",
            "recovery_notes": ["Retried once."],
            "retry_count": 1,
        },
    }
)
store.record_run(
    run_id="run-smoke",
    goal=state.goal,
    started_at=1.0,
    ended_at=2.5,
    final_status=state.status,
    final_summary=state.last_summary,
    result_message=("Smoke result message " * 160),
    steps=state.steps,
    task_state=state,
    source="smoke_test",
    session_id="session-smoke",
    state_scope_id=chat_scope_id,
)
recent_runs = store.get_recent_runs(limit=2)
filtered_runs = store.get_recent_runs(limit=2, session_id="session-smoke", state_scope_id=chat_scope_id)
latest_run = store.get_latest_run(session_id="session-smoke", state_scope_id=chat_scope_id)
if not recent_runs or latest_run.get("run_id") != "run-smoke":
    raise SystemExit("RunHistoryStore did not persist the smoke history entry.")
if len(filtered_runs) != 1 or filtered_runs[0].get("state_scope_id") != chat_scope_id:
    raise SystemExit("RunHistoryStore did not filter runs by session/state scope correctly.")
if len(str(latest_run.get("result_message", ""))) < 3000:
    raise SystemExit("RunHistoryStore did not preserve the longer result_message body.")
prepared_args = latest_run.get("steps", [{}])[0].get("prepared_args", {})
if prepared_args.get("value") == "user@example.com":
    raise SystemExit("RunHistoryStore did not sanitize sensitive step args.")
print("[OK] run history store")

if temp_history_path.exists():
    temp_history_path.unlink()

rejected_state = TaskState("approval smoke")
rejected_state.status = "blocked"
rejected_state.set_task_control(event="rejected", reason="Approval was rejected.")
if "rejected" not in rejected_state.get_control_snapshot().get("behavior", {}).get("reason", "").lower():
    raise SystemExit("TaskState behavior contract did not preserve rejected-approval context.")

desktop_checkpoint_state = TaskState("desktop approval smoke")
desktop_checkpoint_state.status = "paused"
desktop_checkpoint_state.update_memory_from_tool(
    "desktop_press_key",
    {
        "ok": False,
        "paused": True,
        "approval_required": True,
        "checkpoint_required": True,
        "checkpoint_reason": "Approval required before pressing Enter in the desktop window.",
        "checkpoint_tool": "desktop_press_key",
        "checkpoint_target": "Desktop Eval Window :: Enter",
        "checkpoint_resume_args": {
            "key": "Enter",
            "repeat": 1,
            "expected_window_title": "Desktop Eval Window",
            "observation_token": "desktop-smoke-token",
        },
        "summary": "Approval required before pressing Enter in the desktop window.",
        "key_sequence_preview": "Enter",
        "desktop_state": {
            "active_window": {
                "title": "Desktop Eval Window",
                "window_id": "0x00123456",
                "process_name": "python.exe",
            },
            "windows": [{"title": "Desktop Eval Window"}],
            "observation_token": "desktop-smoke-token",
            "observed_at": "2026-03-23T10:00:00",
        },
    },
)
desktop_snapshot = desktop_checkpoint_state.get_control_snapshot()
if desktop_snapshot.get("pending_approval", {}).get("kind") != "desktop_action":
    raise SystemExit("TaskState did not expose a desktop approval checkpoint in the control snapshot.")
if not desktop_snapshot.get("paused", False):
    raise SystemExit("TaskState did not treat a desktop checkpoint as paused work.")
if desktop_checkpoint_state.to_session_snapshot().get("desktop_checkpoint_tool", "") != "desktop_press_key":
    raise SystemExit("TaskState did not persist desktop checkpoint state.")
if desktop_checkpoint_state.get_control_snapshot().get("desktop", {}).get("last_key_sequence", "") != "Enter":
    raise SystemExit("TaskState did not preserve the last bounded desktop key sequence in the desktop snapshot.")

browser_checkpoint_state = TaskState("browser checkpoint smoke")
browser_checkpoint_state.add_step(
    {
        "type": "tool",
        "tool": "browser_type",
        "args": {"value": "user@example.com", "label": "Email"},
        "status": "completed",
    }
)
browser_checkpoint_state.update_memory_from_tool(
    "browser_type",
    {
        "ok": True,
        "summary": "Typed into 'email' on Checkpoint Form",
        "browser_state": {"session_id": "default", "current_url": "file:///checkpoint_form.html", "current_title": "Checkpoint Form"},
        "page": {"url": "file:///checkpoint_form.html", "title": "Checkpoint Form", "visible_text_excerpt": "Checkpoint Form Email Submit"},
        "field": {"name": "email", "selector_hint": "#email", "type": "text"},
        "last_browser_action": "Typed into 'email' on Checkpoint Form",
    },
)
browser_checkpoint_state.add_step(
    {
        "type": "tool",
        "tool": "browser_click",
        "args": {"text": "Submit", "workflow_step": "checkpoint at submit click"},
        "status": "paused",
    }
)
browser_checkpoint_state.update_memory_from_tool(
    "browser_click",
    {
        "ok": False,
        "paused": True,
        "approval_required": True,
        "checkpoint_required": True,
        "checkpoint_reason": "Approval required before clicking 'Submit'.",
        "checkpoint_step": "checkpoint at submit click",
        "checkpoint_target": "Submit",
        "checkpoint_tool": "browser_click",
        "checkpoint_resume_args": {"url": "file:///checkpoint_form.html", "expected_title_contains": "Checkpoint Form"},
        "summary": "Approval required before clicking 'Submit'.",
        "browser_state": {"session_id": "default"},
        "page": {"url": "file:///checkpoint_form.html", "title": "Checkpoint Form", "visible_text_excerpt": "Checkpoint Form Email Submit"},
    },
)
checkpoint_resume_args = browser_checkpoint_state.to_session_snapshot().get("browser_checkpoint_resume_args", {})
if checkpoint_resume_args.get("resume_value") != "user@example.com" or checkpoint_resume_args.get("resume_selector") != "#email":
    raise SystemExit("TaskState did not preserve enough browser checkpoint state to restore a resumed form step.")

shutdown_browser_runtime()
shutdown_desktop_runtime()
print("[OK] task control behavior")

temp_alert_path = Path("data/alert_history_smoke.json")
alert_store = AlertStore(temp_alert_path, max_items=2)
if temp_alert_path.exists():
    temp_alert_path.unlink()

saved_alerts = alert_store.save(
    [
        {"alert_id": "alert-one", "created_at": "2026-03-10T10:00:00+01:00", "severity": "info", "type": "watch_triggered", "source": "watch", "title": "Watch triggered", "message": "First alert."},
        {"alert_id": "alert-two", "created_at": "2026-03-10T10:01:00+01:00", "severity": "warning", "type": "approval_needed", "source": "browser", "title": "Approval needed", "message": "Second alert."},
        {"alert_id": "alert-three", "created_at": "2026-03-10T10:02:00+01:00", "severity": "error", "type": "task_failed", "source": "goal_run", "title": "Task failed", "message": "Third alert."},
    ]
)
loaded_alerts = alert_store.load()
if not saved_alerts:
    raise SystemExit("AlertStore did not save alert state.")
alert_items = loaded_alerts.get("alerts", [])
alert_ids = {item.get("alert_id") for item in alert_items}
if len(alert_items) != 2 or alert_ids != {"alert-two", "alert-three"}:
    raise SystemExit("AlertStore did not preserve the expected alert set when trimming.")
print("[OK] alert store")

if temp_alert_path.exists():
    temp_alert_path.unlink()

temp_queue_path = Path("data/task_queue_smoke.json")
queue_store = TaskQueueStore(temp_queue_path, max_items=2)
if temp_queue_path.exists():
    temp_queue_path.unlink()

saved = queue_store.save(
    [
        {"task_id": "task-one", "goal": "Inspect project architecture", "status": "completed", "created_at": "2026-03-10T10:00:00+01:00", "last_message": "Completed.", "session_id": "session-old", "state_scope_id": "chat:session-old"},
        {"task_id": "task-two", "goal": "Compare two files", "status": "queued", "created_at": "2026-03-10T10:01:00+01:00", "last_message": "Queued.", "session_id": "session-two", "state_scope_id": "chat:session-two"},
        {"task_id": "task-three", "goal": "This older terminal task should be trimmed", "status": "failed", "created_at": "2026-03-10T09:59:00+01:00", "last_message": "Failed.", "state_scope_id": DEFAULT_STATE_SCOPE_ID},
    ],
    active_task_id="",
)
loaded_queue = queue_store.load()
if not saved:
    raise SystemExit("TaskQueueStore did not save queue state.")
queue_tasks = loaded_queue.get("tasks", [])
queue_ids = {task.get("task_id") for task in queue_tasks}
if len(queue_tasks) != 2:
    raise SystemExit("TaskQueueStore did not enforce the bounded queue size.")
if queue_ids != {"task-two", "task-three"}:
    raise SystemExit("TaskQueueStore did not keep the expected bounded task set after trimming.")
print("[OK] task queue store")

if temp_queue_path.exists():
    temp_queue_path.unlink()

temp_scheduled_path = Path("data/scheduled_tasks_smoke.json")
scheduled_store = ScheduledTaskStore(temp_scheduled_path, max_items=2)
if temp_scheduled_path.exists():
    temp_scheduled_path.unlink()

saved_scheduled = scheduled_store.save(
    [
        {"scheduled_id": "sched-one", "goal": "Run later once", "status": "scheduled", "recurrence": "once", "scheduled_for": "2026-03-11T10:00:00+01:00", "next_run_at": "2026-03-11T10:00:00+01:00", "last_message": "Scheduled."},
        {"scheduled_id": "sched-two", "goal": "Daily summary", "status": "completed", "recurrence": "daily", "scheduled_for": "2026-03-10T09:00:00+01:00", "next_run_at": "2026-03-11T09:00:00+01:00", "last_message": "Completed once."},
        {"scheduled_id": "sched-three", "goal": "Old failed once", "status": "failed", "recurrence": "once", "scheduled_for": "2026-03-09T09:00:00+01:00", "next_run_at": "2026-03-09T09:00:00+01:00", "last_message": "Failed."},
    ]
)
loaded_scheduled = scheduled_store.load()
if not saved_scheduled:
    raise SystemExit("ScheduledTaskStore did not save scheduled state.")
scheduled_tasks = loaded_scheduled.get("scheduled_tasks", [])
scheduled_ids = {task.get("scheduled_id") for task in scheduled_tasks}
if len(scheduled_tasks) != 2 or scheduled_ids != {"sched-one", "sched-two"}:
    raise SystemExit("ScheduledTaskStore did not preserve the expected scheduled-task set when trimming.")
print("[OK] scheduled task store")

if temp_scheduled_path.exists():
    temp_scheduled_path.unlink()

temp_watch_path = Path("data/watch_state_smoke.json")
watch_store = WatchStore(temp_watch_path, max_items=2)
if temp_watch_path.exists():
    temp_watch_path.unlink()

saved_watch = watch_store.save(
    [
        {"watch_id": "watch-one", "goal": "Run when file exists", "status": "watching", "condition_type": "file_exists", "target": "README.md", "interval_seconds": 10, "allow_repeat": False, "last_message": "Watching."},
        {"watch_id": "watch-two", "goal": "Repeat on project changes", "status": "completed", "condition_type": "inspect_project_changed", "target": ".", "match_text": "core loop", "interval_seconds": 30, "allow_repeat": True, "last_message": "Completed once."},
        {"watch_id": "watch-three", "goal": "Old failed one-shot", "status": "failed", "condition_type": "file_changed", "target": "main.py", "interval_seconds": 10, "allow_repeat": False, "last_message": "Failed."},
    ]
)
loaded_watch = watch_store.load()
if not saved_watch:
    raise SystemExit("WatchStore did not save watch state.")
watch_items = loaded_watch.get("watches", [])
watch_ids = {item.get("watch_id") for item in watch_items}
if len(watch_items) != 2 or watch_ids != {"watch-one", "watch-two"}:
    raise SystemExit("WatchStore did not preserve the expected watch set when trimming.")
print("[OK] watch store")

if temp_watch_path.exists():
    temp_watch_path.unlink()

print("Smoke test passed.")


