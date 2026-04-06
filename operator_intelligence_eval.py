import json
import shutil
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

from core.local_api import LocalOperatorApiServer
from core.operator_intelligence import (
    OperatorMemoryStore,
    apply_outcome_evaluation,
    build_environment_awareness,
    guard_repeated_failed_action,
)
from core.run_history import RunHistoryStore
from core.state import TaskState


ROOT = Path(__file__).resolve().parent
TEMP_ROOT = ROOT / "data" / "evals" / "operator_intelligence_eval_temp"
REPORT_PATH = ROOT / "data" / "evals" / "operator_intelligence_eval_report.json"


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    group: str = "general"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "group": self.group,
        }


def _append(results: List[CheckResult], name: str, passed: bool, detail: str, *, group: str = "general"):
    results.append(CheckResult(name=name, passed=passed, detail=detail, group=group))


def _write_report(results: List[CheckResult], *, extra: Dict[str, Any] | None = None):
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "passed": all(item.passed for item in results),
                "check_count": len(results),
                "passed_count": sum(1 for item in results if item.passed),
                "failed_count": sum(1 for item in results if not item.passed),
                "checks": [item.to_dict() for item in results],
                "extra": extra or {},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _request_json(base_url: str, path: str, *, method: str = "GET", payload: Dict[str, Any] | None = None) -> Tuple[int, Dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        base_url + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read().decode("utf-8")
        return int(response.status), json.loads(body) if body else {}


def _state(memory_store: OperatorMemoryStore, *, goal: str = "eval goal") -> TaskState:
    state = TaskState(goal)
    setattr(state, "_operator_memory_store", memory_store)
    setattr(
        state,
        "_environment_awareness",
        build_environment_awareness(
            settings={"_settings_version": "eval"},
            email_status={"enabled": True, "configured": True, "authenticated": True},
            execution_profile="safe_bounded",
            lab_armed=False,
        ),
    )
    return state


def _record_tool_step(
    state: TaskState,
    memory_store: OperatorMemoryStore,
    *,
    tool: str,
    args: Dict[str, Any],
    result: Dict[str, Any],
    before: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    step_status = "paused" if result.get("paused", False) else ("completed" if result.get("ok", False) else "failed")
    state.add_step(
        {
            "type": "tool",
            "status": step_status,
            "tool": tool,
            "args": args,
            "result": result,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
    )
    return apply_outcome_evaluation(state, tool, args, result, before_context=before)


def _lab_settings() -> Dict[str, Any]:
    return {
        "session_state_path": str(TEMP_ROOT / "session_state.json"),
        "run_history_path": str(TEMP_ROOT / "run_history.json"),
        "queue_state_path": str(TEMP_ROOT / "task_queue.json"),
        "scheduled_task_state_path": str(TEMP_ROOT / "scheduled_tasks.json"),
        "watch_state_path": str(TEMP_ROOT / "watch_state.json"),
        "alert_state_path": str(TEMP_ROOT / "alert_history.json"),
        "desktop_evidence_root": str(TEMP_ROOT / "desktop_evidence"),
        "desktop_auto_capture_enabled": False,
        "local_api_event_poll_seconds": 0.25,
        "local_api_event_heartbeat_seconds": 2.0,
        "lab_shell_root": str(TEMP_ROOT / "lab_root"),
        "lab_shell_timeout_seconds": 8,
    }


def main() -> int:
    shutil.rmtree(TEMP_ROOT, ignore_errors=True)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    memory_store = OperatorMemoryStore(TEMP_ROOT / "operator_memory.json")
    results: List[CheckResult] = []
    server: LocalOperatorApiServer | None = None
    thread: threading.Thread | None = None

    try:
        desktop_state = _state(memory_store, goal="Focus Notepad")
        before_focus = {
            "active_window_title": "Explorer",
            "active_window_process": "explorer.exe",
            "desktop_target_window_title": "Notepad",
            "desktop_evidence_id": "ev-before",
        }
        desktop_state.desktop_active_window_title = "Notepad - notes.txt"
        desktop_state.desktop_active_window_process = "notepad.exe"
        desktop_state.desktop_last_evidence_id = "ev-after"
        focus_eval = _record_tool_step(
            desktop_state,
            memory_store,
            tool="desktop_focus_window",
            args={"title": "Notepad"},
            result={
                "ok": True,
                "summary": "Focused 'Notepad - notes.txt'.",
                "recovery": {"state": "ready", "active_window": {"title": "Notepad - notes.txt"}},
                "target_window": {"title": "Notepad"},
            },
            before=before_focus,
        )
        _append(
            results,
            "desktop_focus_success_classified",
            focus_eval.get("status") == "success",
            json.dumps(focus_eval, ensure_ascii=False),
            group="desktop",
        )

        desktop_uncertain_state = _state(memory_store, goal="Open a file in Notepad")
        before_open = {
            "active_window_title": "Explorer",
            "active_window_process": "explorer.exe",
            "desktop_evidence_id": "same-evidence",
        }
        desktop_uncertain_state.desktop_active_window_title = "Explorer"
        desktop_uncertain_state.desktop_active_window_process = "explorer.exe"
        desktop_uncertain_state.desktop_last_evidence_id = "same-evidence"
        open_eval = _record_tool_step(
            desktop_uncertain_state,
            memory_store,
            tool="desktop_run_command",
            args={"command": "notepad notes.txt", "shell_kind": "powershell"},
            result={
                "ok": True,
                "summary": "Ran the bounded local command.",
                "command": "notepad notes.txt",
                "command_result": {"returncode": 0, "stdout_excerpt": ""},
            },
            before=before_open,
        )
        _append(
            results,
            "desktop_open_without_visible_change_is_uncertain",
            open_eval.get("status") == "uncertain",
            json.dumps(open_eval, ensure_ascii=False),
            group="desktop",
        )

        gmail_state = _state(memory_store, goal="Reply to this Gmail thread")
        gmail_partial = _record_tool_step(
            gmail_state,
            memory_store,
            tool="email_prepare_reply_draft",
            args={"thread_id": "thread-1"},
            result={
                "ok": True,
                "provider": "gmail",
                "disposition": "needs_context",
                "needs_context": True,
                "summary": "Need one pricing detail before drafting.",
                "questions": ["Which pricing tier should I mention?"],
            },
        )
        _append(
            results,
            "gmail_needs_context_is_partial",
            gmail_partial.get("status") == "partial_success" and str((gmail_partial.get("retry", {}) or {}).get("action", "")) == "ask_user",
            json.dumps(gmail_partial, ensure_ascii=False),
            group="gmail",
        )

        gmail_send = _record_tool_step(
            gmail_state,
            memory_store,
            tool="email_send_draft",
            args={"draft_id": "draft-1"},
            result={
                "ok": True,
                "provider": "gmail",
                "summary": "Sent the approved Gmail draft.",
                "sent": {"message_id": "msg-123", "thread_id": "thread-1"},
            },
        )
        _append(
            results,
            "gmail_send_success_classified",
            gmail_send.get("status") == "success",
            json.dumps(gmail_send, ensure_ascii=False),
            group="gmail",
        )

        repeat_state = _state(memory_store, goal="Retry a failing action")
        for _ in range(2):
            _record_tool_step(
                repeat_state,
                memory_store,
                tool="email_prepare_forward_draft",
                args={"thread_id": "thread-repeat", "to": ["ops@example.com"]},
                result={"ok": False, "error": "Draft generation failed.", "summary": "Draft generation failed."},
            )
        repeat_guard = guard_repeated_failed_action(
            repeat_state,
            "email_prepare_forward_draft",
            {"thread_id": "thread-repeat", "to": ["ops@example.com"]},
        )
        _append(
            results,
            "repeat_guard_blocks_same_failed_action",
            bool(repeat_guard.get("blocked", False)) and str(repeat_guard.get("reason", "")) == "repeat_budget_exhausted",
            json.dumps(repeat_guard, ensure_ascii=False),
            group="retry",
        )

        hints = memory_store.lookup_patterns(domain="desktop", tool_name="desktop_focus_window", target_signature=focus_eval.get("target_signature", ""), goal="Focus Notepad")
        _append(
            results,
            "memory_reuses_recent_success_pattern",
            bool(hints.get("prefer")),
            json.dumps(hints, ensure_ascii=False),
            group="memory",
        )

        failure_hints = memory_store.lookup_patterns(domain="gmail", tool_name="email_prepare_forward_draft", goal="Retry a failing action")
        _append(
            results,
            "memory_records_recent_failed_approach",
            bool(failure_hints.get("avoid")),
            json.dumps(failure_hints, ensure_ascii=False),
            group="memory",
        )

        environment_facts = build_environment_awareness(
            settings={"_settings_version": "eval"},
            email_status={"enabled": True, "configured": True, "authenticated": True},
            execution_profile="safe_bounded",
            lab_armed=False,
        )
        _append(
            results,
            "environment_awareness_available",
            bool(environment_facts.get("os")) and isinstance(environment_facts.get("available_shells", []), list),
            json.dumps(environment_facts, ensure_ascii=False),
            group="environment",
        )

        history_store = RunHistoryStore(TEMP_ROOT / "run_history_direct.json")
        history_entry = history_store.record_run(
            run_id=history_store.next_run_id(),
            goal="Focus Notepad",
            started_at=time.time() - 1,
            ended_at=time.time(),
            final_status="completed",
            final_summary="Focused Notepad.",
            result_message="Focused Notepad.",
            steps=desktop_state.steps,
            task_state=desktop_state,
            source="goal_run",
            session_id="eval-session",
            state_scope_id="eval-scope",
        )
        stored_run = history_store.get_run(str(history_entry.get("run_id", "")), session_id="eval-session", state_scope_id="eval-scope")
        first_step_eval = ((stored_run.get("steps", [{}]) or [{}])[0] if isinstance(stored_run.get("steps", []), list) else {}).get("evaluation", {})
        _append(
            results,
            "run_history_contains_evaluation_trace",
            isinstance(first_step_eval, dict) and str(first_step_eval.get("status", "")) == "success",
            json.dumps(stored_run, ensure_ascii=False)[:500],
            group="history",
        )

        server = LocalOperatorApiServer(host="127.0.0.1", port=0, settings=_lab_settings())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.port}"

        status_code, status_payload = _request_json(base_url, "/status")
        runtime = (status_payload.get("data", {}) if isinstance(status_payload.get("data", {}), dict) else {}).get("runtime", {})
        _append(
            results,
            "runtime_exposes_environment_awareness",
            status_code == 200 and isinstance(runtime, dict) and isinstance(runtime.get("environment_awareness", {}), dict),
            json.dumps(runtime, ensure_ascii=False),
            group="environment",
        )

        arm_status, arm_payload = _request_json(base_url, "/lab/arm", method="POST", payload={"confirmation": "ENABLE LAB"})
        _append(
            results,
            "lab_can_arm_for_live_eval",
            arm_status == 200 and bool((arm_payload.get("data", {}) or {}).get("lab", {}).get("armed", False)),
            json.dumps(arm_payload, ensure_ascii=False),
            group="lab",
        )

        run_status, run_payload = _request_json(
            base_url,
            "/lab/commands/run",
            method="POST",
            payload={"command": "Get-Location", "shell_kind": "powershell"},
        )
        run_data = run_payload.get("data", {}) if isinstance(run_payload.get("data", {}), dict) else {}
        run_result = run_data.get("result", {}) if isinstance(run_data.get("result", {}), dict) else {}
        lab_status = run_data.get("lab", {}) if isinstance(run_data.get("lab", {}), dict) else {}
        run_id = str(((lab_status.get("recent_runs", []) or [{}])[0] if isinstance(lab_status.get("recent_runs", []), list) else {}).get("run_id", "")).strip()
        _append(
            results,
            "lab_safe_command_evaluated_successfully",
            run_status == 200 and str((run_result.get("evaluation", {}) if isinstance(run_result.get("evaluation", {}), dict) else {}).get("status", "")) == "success",
            json.dumps(run_result, ensure_ascii=False),
            group="lab",
        )

        if run_id:
            _, run_detail_payload = _request_json(base_url, f"/runs/{run_id}", method="GET", payload=None)
            run_detail = (run_detail_payload.get("data", {}) if isinstance(run_detail_payload.get("data", {}), dict) else {}).get("run", {})
            steps = run_detail.get("steps", []) if isinstance(run_detail.get("steps", []), list) else []
            last_step = steps[-1] if steps else {}
            _append(
                results,
                "lab_run_replay_has_evaluation",
                isinstance(last_step.get("evaluation", {}), dict) and str(last_step.get("evaluation", {}).get("status", "")) == "success",
                json.dumps(last_step, ensure_ascii=False),
                group="lab",
            )
        else:
            _append(results, "lab_run_replay_has_evaluation", False, "No lab run id was recorded.", group="lab")

    finally:
        if server is not None:
            server.shutdown()
        if thread is not None:
            thread.join(timeout=2)

    _write_report(results)
    passed = sum(1 for item in results if item.passed)
    failed = len(results) - passed
    print(f"Operator intelligence eval: {passed}/{len(results)} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
