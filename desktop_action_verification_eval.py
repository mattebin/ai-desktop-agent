import json
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from core.operator_intelligence import (
    OperatorMemoryStore,
    apply_outcome_evaluation,
    build_environment_awareness,
    guard_repeated_failed_desktop_strategy,
)
from core.problem_records import ProblemRecordStore
from core.state import TaskState
from core.tool_runtime import ToolRuntime
from tools import desktop as desktop_tools
from tools.registry import get_tools


ROOT = Path(__file__).resolve().parent
TEMP_ROOT = ROOT / "data" / "evals" / "desktop_action_verification_eval_temp"
REPORT_PATH = ROOT / "data" / "evals" / "desktop_action_verification_eval_report.json"


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


def _state(memory_store: OperatorMemoryStore, problem_store: ProblemRecordStore, *, goal: str) -> TaskState:
    state = TaskState(goal)
    setattr(state, "_operator_memory_store", memory_store)
    setattr(state, "_problem_store", problem_store)
    setattr(
        state,
        "_environment_awareness",
        build_environment_awareness(
            settings={"_settings_version": "desktop-action-eval"},
            email_status={"enabled": True, "configured": True, "authenticated": True},
            execution_profile="safe_bounded",
            lab_armed=False,
        ),
    )
    return state


def _record_tool_step(
    state: TaskState,
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


def _default_window(window_id: str, title: str, process_name: str, *, active: bool = False) -> Dict[str, Any]:
    return {
        "window_id": window_id,
        "title": title,
        "process_name": process_name,
        "pid": 2000 if process_name == "notepad.exe" else 1000,
        "is_active": active,
        "is_visible": True,
        "is_minimized": False,
        "is_cloaked": False,
        "rect": {"x": 20, "y": 20, "width": 1280, "height": 860},
    }


@contextmanager
def _patched_desktop(**replacements):
    originals = {name: getattr(desktop_tools, name) for name in replacements}
    try:
        for name, value in replacements.items():
            setattr(desktop_tools, name, value)
        yield
    finally:
        for name, value in originals.items():
            setattr(desktop_tools, name, value)


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def main() -> int:
    shutil.rmtree(TEMP_ROOT, ignore_errors=True)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    memory_store = OperatorMemoryStore(TEMP_ROOT / "operator_memory.json")
    problem_store = ProblemRecordStore(TEMP_ROOT / "problem_records.json")
    runtime = ToolRuntime(get_tools())
    results: List[CheckResult] = []

    try:
        before_active = _default_window("0x100", "File Explorer", "explorer.exe", active=True)
        target_active = _default_window("0x200", "Notepad - notes.txt", "notepad.exe", active=True)
        active_samples = [before_active, target_active, target_active]
        window_samples = [
            [before_active],
            [before_active, target_active],
            [before_active, target_active],
        ]
        sample_index = {"value": 0}

        def active_window_info():
            idx = min(sample_index["value"], len(active_samples) - 1)
            return dict(active_samples[idx])

        def enum_windows(include_minimized: bool = True, include_hidden: bool = True, limit: int = 24):
            idx = min(sample_index["value"], len(window_samples) - 1)
            sample_index["value"] += 1
            return [dict(item) for item in window_samples[idx][:limit]]

        with _patched_desktop(
            _active_window_info=active_window_info,
            _enum_windows=enum_windows,
            _probe_expected_process=lambda expected_process_names, launched_pid=0: {},
        ):
            verification = desktop_tools._sample_desktop_action_verification(
                action="desktop_focus_window",
                validator_family="focus_switch",
                strategy_family="focus_recovery_window",
                before_active_window=before_active,
                before_windows=[before_active],
                expected_title="Notepad - notes.txt",
                expected_window_id="0x200",
                expected_process_names=["notepad.exe"],
                target_description="Focus Notepad",
                sample_count=3,
                interval_ms=80,
            )
        _append(
            results,
            "bounded_focus_verifier_catches_delayed_success",
            verification.get("status") == "verified_focus"
            and "target_foreground" in list(verification.get("observed_signals", [])),
            _json(verification),
            group="verification",
        )

        input_state = _state(memory_store, problem_store, goal="Type into the focused field")
        input_eval = _record_tool_step(
            input_state,
            tool="desktop_type_text",
            args={
                "field_label": "Message",
                "value": "Hello world",
                "target_signature": "desktop_type_text|message-field",
                "strategy_family": "direct_input",
                "validator_family": "text_input",
            },
            result={
                "ok": True,
                "summary": "Typed text into the focused field.",
                "desktop_strategy": {
                    "desktop_intent": "type_text",
                    "strategy_family": "direct_input",
                    "validator_family": "text_input",
                    "target_signature": "desktop_type_text|message-field",
                },
                "desktop_verification": {
                    "status": "focus_confirmed_only",
                    "validator_family": "text_input",
                    "strategy_family": "direct_input",
                    "target_description": "Message input",
                    "note": "Input focus stayed on the message field, but no visible text diff was confirmed.",
                    "observed_signals": ["target_foreground"],
                    "missing_signals": ["visible_input_change"],
                },
            },
            before={
                "active_window_title": "Chat",
                "active_window_process": "chat.exe",
                "desktop_evidence_id": "ev-input-before",
            },
        )
        _append(
            results,
            "text_input_focus_only_stays_partial",
            input_eval.get("status") == "partial_success"
            and input_eval.get("validator_family") == "text_input",
            _json(input_eval),
            group="classification",
        )

        click_state = _state(memory_store, problem_store, goal="Click Save in Settings")
        click_args = {
            "x": 480,
            "y": 220,
            "coordinate_mode": "absolute",
            "expected_window_title": "Settings",
            "target_signature": "desktop_click_mouse|settings|save",
            "strategy_family": "direct_interaction",
            "validator_family": "click_navigation",
        }
        click_result = {
            "ok": True,
            "summary": "Performed a bounded left click.",
            "desktop_strategy": {
                "desktop_intent": "click_mouse",
                "strategy_family": "direct_interaction",
                "validator_family": "click_navigation",
                "target_signature": "desktop_click_mouse|settings|save",
            },
            "desktop_verification": {
                "status": "no_visible_change",
                "validator_family": "click_navigation",
                "strategy_family": "direct_interaction",
                "target_description": "Click Save in Settings",
                "note": "The interaction ran, but no visible desktop navigation change was confirmed.",
                "observed_signals": [],
                "missing_signals": ["visible_navigation_change"],
            },
        }
        click_eval = _record_tool_step(
            click_state,
            tool="desktop_click_mouse",
            args=click_args,
            result=click_result,
            before={
                "active_window_title": "Settings",
                "active_window_process": "SystemSettings.exe",
                "desktop_evidence_id": "ev-click-before",
            },
        )
        _append(
            results,
            "click_without_visible_change_is_no_progress",
            click_eval.get("status") == "no_progress"
            and click_eval.get("verification_status") == "no_visible_change",
            _json(click_eval),
            group="classification",
        )

        launch_state = _state(memory_store, problem_store, goal="Open Calculator")
        launch_eval = _record_tool_step(
            launch_state,
            tool="desktop_start_process",
            args={
                "executable": "C:\\Windows\\System32\\calc.exe",
                "owned_label": "Calculator",
                "target_signature": "desktop_start_process|calc",
                "strategy_family": "direct_launch",
                "validator_family": "open_launch",
            },
            result={
                "ok": True,
                "summary": "Started the bounded desktop process.",
                "desktop_strategy": {
                    "desktop_intent": "start_process",
                    "strategy_family": "direct_launch",
                    "validator_family": "open_launch",
                    "target_signature": "desktop_start_process|calc",
                },
                "desktop_verification": {
                    "status": "process_started_only",
                    "validator_family": "open_launch",
                    "strategy_family": "direct_launch",
                    "target_description": "Open Calculator",
                    "note": "The expected process appeared, but a visible surface was not clearly confirmed.",
                    "observed_signals": ["process_detected"],
                    "missing_signals": ["visible_launch_change"],
                    "process_detected": True,
                },
            },
            before={
                "active_window_title": "Desktop",
                "active_window_process": "explorer.exe",
                "desktop_evidence_id": "ev-launch-before",
            },
        )
        _append(
            results,
            "launch_process_started_only_stays_uncertain",
            launch_eval.get("status") == "uncertain"
            and launch_eval.get("verification_status") == "process_started_only",
            _json(launch_eval),
            group="classification",
        )

        strategy_state = _state(memory_store, problem_store, goal="Click Save in Settings")
        _record_tool_step(
            strategy_state,
            tool="desktop_click_mouse",
            args=click_args,
            result=click_result,
            before={
                "active_window_title": "Settings",
                "active_window_process": "SystemSettings.exe",
                "desktop_evidence_id": "ev-click-before",
            },
        )
        prepared_click_args = runtime.prepare_args(
            "desktop_click_mouse",
            {
                "x": 480,
                "y": 220,
                "coordinate_mode": "absolute",
                "expected_window_title": "Settings",
            },
            strategy_state,
            planning_goal="Try another method to click Save in Settings",
        )
        _append(
            results,
            "strategy_switch_moves_clicks_into_focus_recovery",
            prepared_click_args.get("strategy_family") == "focus_recovery_interaction"
            and bool(prepared_click_args.get("pre_action_recovery", False)),
            _json(prepared_click_args),
            group="strategy",
        )

        guard_state = _state(memory_store, problem_store, goal="Retry a stubborn click")
        for _ in range(2):
            _record_tool_step(
                guard_state,
                tool="desktop_click_mouse",
                args=click_args,
                result=click_result,
                before={
                    "active_window_title": "Settings",
                    "active_window_process": "SystemSettings.exe",
                    "desktop_evidence_id": "ev-click-before",
                },
            )
        blocked = guard_repeated_failed_desktop_strategy(guard_state, "desktop_click_mouse", click_args)
        _append(
            results,
            "repeated_failed_desktop_strategy_is_blocked",
            bool(blocked.get("blocked", False)) and blocked.get("reason") == "desktop_strategy_family_exhausted",
            _json(blocked),
            group="strategy",
        )

        latest_problem = {}
        if guard_state.steps:
            last_result = guard_state.steps[-1].get("result", {}) if isinstance(guard_state.steps[-1].get("result", {}), dict) else {}
            latest_problem = last_result.get("problem", {}) if isinstance(last_result.get("problem", {}), dict) else {}
        _append(
            results,
            "problem_records_capture_desktop_validator_detail",
            latest_problem.get("desktop_strategy_family") == "direct_interaction"
            and latest_problem.get("desktop_validator_family") == "click_navigation"
            and latest_problem.get("desktop_verification_status") == "no_visible_change"
            and bool(latest_problem.get("stored_lesson", "")),
            _json(latest_problem),
            group="problems",
        )
    finally:
        _write_report(results)

    passed = all(item.passed for item in results)
    print(f"desktop_action_verification_eval: {sum(1 for item in results if item.passed)}/{len(results)} passed")
    if not passed:
        for item in results:
            if not item.passed:
                print(f"- FAIL {item.name}: {item.detail}")
    print(f"report: {REPORT_PATH}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
