import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from core.capability_profiles import SAFE_BOUNDED_PROFILE, SANDBOXED_FULL_ACCESS_LAB_PROFILE
from core.operator_intelligence import build_environment_awareness
from core.state import TaskState
from core.tool_runtime import ToolRuntime
from tools.registry import get_tools


ROOT = Path(__file__).resolve().parent
TEMP_ROOT = ROOT / "data" / "evals" / "workflow_bridge_eval_temp"
REPORT_PATH = ROOT / "data" / "evals" / "workflow_bridge_eval_report.json"


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


def _write_report(results: List[CheckResult]):
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
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class _MemoryHints:
    def __init__(self, hints: Dict[str, Any] | None = None):
        self.hints = hints or {}

    def lookup_patterns(self, **_kwargs):
        return dict(self.hints)


def _state(
    *,
    goal: str,
    execution_profile: str = SAFE_BOUNDED_PROFILE,
    lab_armed: bool = False,
    memory_hints: Dict[str, Any] | None = None,
    control_snapshot: Dict[str, Any] | None = None,
) -> TaskState:
    state = TaskState(goal)
    state.set_execution_profile(execution_profile)
    setattr(state, "_operator_memory_store", _MemoryHints(memory_hints))
    setattr(
        state,
        "_environment_awareness",
        build_environment_awareness(
            settings={"_settings_version": "workflow-bridge-eval"},
            email_status={"enabled": True, "configured": True, "authenticated": True},
            execution_profile=execution_profile,
            lab_armed=lab_armed,
        ),
    )
    if isinstance(control_snapshot, dict):
        setattr(state, "_last_control_snapshot", control_snapshot)
    return state


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def main() -> int:
    shutil.rmtree(TEMP_ROOT, ignore_errors=True)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    results: List[CheckResult] = []
    runtime = ToolRuntime(get_tools())

    try:
        safe_state = _state(goal="Inspect the desktop safely.")
        safe_tools = [item.get("name", "") for item in runtime.planner_tools(safe_state)]
        _append(
            results,
            "safe_profile_hides_lab_shell_tool",
            "lab_run_shell" not in safe_tools,
            _json({"planner_tools": safe_tools}),
            group="lab",
        )

        lab_state = _state(
            goal="Inspect this workspace in the lab and summarize the files.",
            execution_profile=SANDBOXED_FULL_ACCESS_LAB_PROFILE,
            lab_armed=True,
            control_snapshot={"lab": {"workspace_id": "workspace-eval", "summary": "armed lab workspace"}},
        )
        lab_tools = [item.get("name", "") for item in runtime.planner_tools(lab_state)]
        _append(
            results,
            "armed_lab_profile_exposes_lab_shell_tool",
            "lab_run_shell" in lab_tools,
            _json({"planner_tools": lab_tools}),
            group="lab",
        )

        prepared_lab_args = runtime.prepare_args(
            "lab_run_shell",
            {"command": "Get-ChildItem"},
            lab_state,
            planning_goal="List files in the current lab workspace.",
        )
        _append(
            results,
            "lab_prepare_args_inherit_profile_and_workspace",
            prepared_lab_args.get("execution_profile") == SANDBOXED_FULL_ACCESS_LAB_PROFILE
            and prepared_lab_args.get("lab_armed") is True
            and prepared_lab_args.get("workspace_id") == "workspace-eval",
            _json(prepared_lab_args),
            group="lab",
        )

        email_state = _state(
            goal="Reply to the latest customer email, then send it once approved.",
            control_snapshot={
                "email": {
                    "thread_id": "thread-123",
                    "draft_id": "draft-456",
                    "summary": "Prepared a reply draft for the latest support thread.",
                    "draft_status": "ready_for_review",
                }
            },
        )
        reply_args = runtime.prepare_args("email_prepare_reply_draft", {}, email_state)
        send_args = runtime.prepare_args("email_send_draft", {}, email_state)
        _append(
            results,
            "email_reply_reuses_latest_thread_context",
            reply_args.get("thread_id") == "thread-123",
            _json(reply_args),
            group="email",
        )
        _append(
            results,
            "email_send_reuses_latest_draft_context",
            send_args.get("draft_id") == "draft-456",
            _json(send_args),
            group="email",
        )

        browser_state = _state(goal="Continue the open browser workflow and inspect the destination page.")
        browser_state.browser_session_id = "browser-session-1"
        browser_state.browser_task_name = "follow_and_summarize"
        browser_state.browser_task_status = "running"
        browser_state.browser_task_next_step = "inspect destination"
        browser_state.browser_workflow_name = "Follow and summarize"
        browser_state.browser_workflow_pattern = "navigation_extract_flow"
        browser_state.browser_workflow_next_step = "inspect destination"
        browser_state.browser_last_successful_action = "followed the release notes link"
        browser_args = runtime.prepare_args("browser_inspect_page", {}, browser_state)
        _append(
            results,
            "browser_args_preserve_workflow_continuity",
            browser_args.get("session_id") == "browser-session-1"
            and browser_args.get("browser_task_name") == "follow_and_summarize"
            and browser_args.get("browser_task_step") == "inspect destination"
            and browser_args.get("workflow_pattern") == "navigation_extract_flow",
            _json(browser_args),
            group="workflow",
        )

        desktop_state = _state(
            goal="Click Save in the current settings dialog.",
            control_snapshot={
                "desktop": {
                    "selected_target_proposals": {
                        "proposals": [
                            {
                                "summary": "Save button area in Settings",
                                "window_title": "Settings",
                                "window_process": "systemsettings.exe",
                                "target_kind": "ui_area",
                                "suggested_next_actions": ["desktop_click_mouse"],
                            }
                        ]
                    }
                }
            },
        )
        desktop_args = runtime.prepare_args(
            "desktop_click_mouse",
            {"x": 480, "y": 220, "coordinate_mode": "absolute"},
            desktop_state,
            planning_goal="Click Save in the Settings dialog.",
        )
        _append(
            results,
            "desktop_click_reuses_target_proposal_hints",
            desktop_args.get("expected_window_title") == "Settings"
            and "systemsettings.exe" in list(desktop_args.get("expected_process_names", []))
            and desktop_args.get("target_description") == "Save button area in Settings",
            _json(desktop_args),
            group="desktop",
        )

        remembered_failure_state = _state(
            goal="Click Save in the settings dialog.",
            memory_hints={
                "avoid": [{"strategy_family": "direct_interaction"}],
                "lessons": [
                    {
                        "category": "no_visible_progress_after_action",
                        "strategy_family": "direct_interaction",
                    }
                ],
            },
        )
        remembered_failure_state.desktop_active_window_title = "Settings"
        switched_args = runtime.prepare_args(
            "desktop_click_mouse",
            {"x": 480, "y": 220, "coordinate_mode": "absolute"},
            remembered_failure_state,
            planning_goal="Click Save in the settings dialog.",
        )
        _append(
            results,
            "desktop_click_switches_strategy_family_after_failed_pattern",
            switched_args.get("strategy_family") == "focus_recovery_interaction"
            and "direct_interaction" in list(switched_args.get("avoid_strategy_families", [])),
            _json(switched_args),
            group="desktop",
        )

    finally:
        _write_report(results)

    passed = sum(1 for item in results if item.passed)
    total = len(results)
    print(f"workflow_bridge_eval: {passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
