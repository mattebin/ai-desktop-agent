from __future__ import annotations

import importlib
import json
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
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
    "core.desktop_evidence",
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
from core.desktop_evidence import (
    DesktopEvidenceStore,
    assess_desktop_evidence,
    build_desktop_evidence_bundle,
    compact_evidence_preview,
    select_checkpoint_evidence,
    select_recent_evidence,
    select_task_evidence,
    summarize_evidence_bundle,
)
from core.chat_sessions import ChatSessionManager
from core.execution_manager import ScheduledTaskStore, TaskQueueStore
from core.file_watch_backend import create_file_watch_backend
from core.llm_client import _goal_requests_brief_answer, _goal_requests_single_recommendation
from core.local_api import LocalOperatorApiServer, _status_payload
from core.local_api_client import LocalOperatorApiClient, wait_for_local_api_status
from core.loop import _is_redundant_desktop_observation, _maybe_pause_for_desktop_action, _maybe_recover_desktop_action_failure
from core.operator_behavior import classify_chat_turn, looks_like_simple_conversation_turn
from core.operator_controller import OperatorController
from core.run_history import RunHistoryStore
from core.scheduler_backend import create_scheduler_backend
from core.session_store import DEFAULT_STATE_SCOPE_ID, SessionStore
from core.state import TaskState
from core.tool_runtime import ToolRuntime
from core.watchers import WatchStore
from control_ui import _parse_inline_markdown_segments, _parse_rich_text_blocks, _session_matches_query, _timeline_entry_from_event
from live_agent_eval import SCENARIO_NAMES, _golden_final_answer_checks, _interpreter_has_playwright, _latest_new_run, _project_venv_python
from tools.browser import shutdown_browser_runtime
from tools.desktop import (
    desktop_capture_screenshot,
    desktop_click_point,
    desktop_list_windows,
    desktop_type_text,
    get_desktop_backend_status,
    probe_ui_evidence,
    shutdown_desktop_runtime,
)
from tools.registry import get_tools

controller = OperatorController()
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
runtime = snapshot.get("runtime", {})
if runtime.get("active_model") != "gpt-5.4" or runtime.get("reasoning_effort") != "medium":
    raise SystemExit("OperatorController.get_snapshot() did not expose the expected runtime model configuration.")
print("[OK] operator controller snapshot")

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
watch_probe_file.write_text("changed", encoding="utf-8")
watch_signal = False
for _ in range(20):
    if file_watch_backend.has_recent_signal(str(watch_probe_file), since_timestamp=0.0):
        watch_signal = True
        break
    time.sleep(0.1)
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
    "desktop_capture_screenshot",
    "desktop_click_point",
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
    click_preview = desktop_click_point({"x": test_x, "y": test_y, "observation_token": observation_token})
    if not click_preview.get("paused", False) or not click_preview.get("approval_required", False) or click_preview.get("checkpoint_tool") != "desktop_click_point":
        raise SystemExit("desktop_click_point() did not require approval in the expected bounded way.")
    type_preview = desktop_type_text(
        {
            "value": "desktop smoke text",
            "field_label": "desktop smoke input",
            "observation_token": observation_token,
        }
    )
    if not type_preview.get("paused", False) or not type_preview.get("approval_required", False) or type_preview.get("checkpoint_tool") != "desktop_type_text":
        raise SystemExit("desktop_type_text() did not require approval in the expected bounded way.")
desktop_backend_status = get_desktop_backend_status()
for required_backend in ("window", "screenshot", "ui_evidence"):
    if not isinstance(desktop_backend_status.get(required_backend, {}), dict):
        raise SystemExit("Desktop backend status did not include the expected backend sections.")
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

cors_server = LocalOperatorApiServer(port=0)
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
temp_evidence_store = DesktopEvidenceStore(temp_evidence_root, max_items=2)
evidence_now = datetime.now().astimezone()
first_timestamp = (evidence_now - timedelta(seconds=30)).isoformat(timespec="seconds")
second_timestamp = (evidence_now - timedelta(seconds=20)).isoformat(timespec="seconds")
third_timestamp = (evidence_now - timedelta(seconds=10)).isoformat(timespec="seconds")

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
)
third_bundle["evidence_id"] = "desk-smoke-3"
third_bundle["timestamp"] = third_timestamp
third_ref = temp_evidence_store.record_bundle(third_bundle)
if temp_evidence_store.load_bundle("desk-smoke-1"):
    raise SystemExit("DesktopEvidenceStore did not prune an older bundle when retention was exceeded.")
if first_capture_path.exists():
    raise SystemExit("DesktopEvidenceStore did not prune an older screenshot artifact when retention was exceeded.")
if len(temp_evidence_store.recent_refs(limit=8)) != 2:
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

recent_summaries = temp_evidence_store.recent_summaries(limit=8)
if len(recent_summaries) != 2:
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
desktop_state.update_memory_from_tool(
    "desktop_capture_screenshot",
    {
        "ok": True,
        "summary": "Captured a screenshot of the active window.",
        "screenshot_path": third_ref.get("screenshot_path", ""),
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
    },
)
desktop_evidence_snapshot = desktop_state.get_control_snapshot()
if desktop_evidence_snapshot.get("desktop", {}).get("evidence_id") != "desk-smoke-3":
    raise SystemExit("TaskState did not surface desktop evidence in the authoritative control snapshot.")
if desktop_evidence_snapshot.get("desktop", {}).get("evidence_bundle_path", "") != third_ref.get("bundle_path", ""):
    raise SystemExit("TaskState did not preserve the desktop evidence bundle path.")
if desktop_evidence_snapshot.get("desktop", {}).get("selected_evidence", {}).get("evidence_id") != "desk-smoke-3":
    raise SystemExit("TaskState did not surface the selected desktop evidence summary.")
if not desktop_evidence_snapshot.get("desktop", {}).get("selected_evidence_assessment", {}).get("sufficient", False):
    raise SystemExit("TaskState did not surface selected desktop evidence sufficiency in the authoritative control snapshot.")
desktop_observation_text = desktop_state.get_observation()
if "Selected desktop evidence assessment:" not in desktop_observation_text:
    raise SystemExit("TaskState.get_observation() did not include compact selected desktop evidence grounding lines.")

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
checkpoint_final_context = desktop_checkpoint_summary_state.get_final_context()
if "Checkpoint desktop evidence assessment:" not in checkpoint_final_context:
    raise SystemExit("TaskState.get_final_context() did not include checkpoint desktop evidence grounding lines.")

status_payload = _status_payload(checkpoint_snapshot)
if status_payload.get("pending_approval", {}).get("evidence_assessment", {}).get("state") != "sufficient":
    raise SystemExit("Local API status compaction did not expose checkpoint evidence assessment.")
if status_payload.get("desktop", {}).get("checkpoint_evidence_assessment", {}).get("state") != "sufficient":
    raise SystemExit("Local API status compaction did not expose desktop checkpoint evidence assessment.")


class _DesktopApprovalStubLLM:
    def finalize(self, goal, steps, observation="", final_context=""):
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
if [call[1] for call in desktop_recovery_runtime.calls if call[0] == "execute"] != ["desktop_capture_screenshot"]:
    raise SystemExit("Desktop action recovery did not use the expected bounded screenshot refresh tool.")

evidence_server = LocalOperatorApiServer(port=0)
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

    with urlopen(f"http://127.0.0.1:{evidence_server.port}/desktop/evidence/desk-smoke-1/artifact", timeout=5) as pruned_artifact_response:
        pruned_payload = json.loads(pruned_artifact_response.read().decode("utf-8")).get("data", {}).get("artifact", {})
        if pruned_payload.get("availability_state") not in {"pruned", "not_found"}:
            raise SystemExit("Local API did not surface the expected pruned/missing state for a removed evidence artifact.")
finally:
    evidence_server.shutdown()
    desktop_evidence_module._STORE = original_evidence_store
    shutil.rmtree(temp_evidence_root, ignore_errors=True)

print("[OK] desktop evidence layer")

project_venv_python = _project_venv_python(Path.cwd())
if not str(project_venv_python).lower().endswith(".venv\\scripts\\python.exe"):
    raise SystemExit("live_agent_eval did not resolve the expected project venv Python path.")
if not _interpreter_has_playwright(project_venv_python):
    raise SystemExit("live_agent_eval did not detect Playwright in the project venv runtime.")
for expected_scenario in {"outcome_style_corpus", "continuity_quality", "brief_answer_quality", "desktop_evidence_grounding"}:
    if expected_scenario not in SCENARIO_NAMES:
        raise SystemExit(f"live_agent_eval is missing the expected scenario: {expected_scenario}")
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
if 'THEME_STORAGE_KEY' not in desktop_app_source or 'Theme:' not in desktop_app_source or 'data-theme="dark"' not in (desktop_ui_root / "src" / "styles.css").read_text(encoding="utf-8"):
    raise SystemExit("Desktop UI is missing the expected theme-toggle implementation.")
if 'Model: {runtimeModel}' not in desktop_app_source or 'Reasoning: {runtimeEffortLabel}' not in desktop_app_source:
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
    def reply_in_chat(self, user_message, *, session_context="", mode="chat"):
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
    "desktop_click_point",
    {
        "ok": False,
        "paused": True,
        "approval_required": True,
        "checkpoint_required": True,
        "checkpoint_reason": "Approval required before clicking the desktop point.",
        "checkpoint_tool": "desktop_click_point",
        "checkpoint_target": "Desktop Eval Window @ (120, 140)",
        "checkpoint_resume_args": {
            "x": 120,
            "y": 140,
            "expected_window_title": "Desktop Eval Window",
            "observation_token": "desktop-smoke-token",
        },
        "summary": "Approval required before clicking the desktop point.",
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
if desktop_checkpoint_state.to_session_snapshot().get("desktop_checkpoint_tool", "") != "desktop_click_point":
    raise SystemExit("TaskState did not persist desktop checkpoint state.")

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


