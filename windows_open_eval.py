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
    guard_repeated_failed_open_family,
)
from core.problem_records import ProblemRecordStore
from core.state import TaskState
from core.tool_runtime import ToolRuntime
from core.windows_opening import classify_open_target, choose_windows_open_strategy
from tools import desktop as desktop_tools
from tools.registry import get_tools


ROOT = Path(__file__).resolve().parent
TEMP_ROOT = ROOT / "data" / "evals" / "windows_open_eval_temp"
REPORT_PATH = ROOT / "data" / "evals" / "windows_open_eval_report.json"


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
            settings={"_settings_version": "windows-open-eval"},
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
        "pid": 1000 if process_name == "explorer.exe" else 2000,
        "is_active": active,
        "is_visible": True,
        "is_minimized": False,
        "is_cloaked": False,
        "rect": {"x": 10, "y": 10, "width": 1200, "height": 800},
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


def _desktop_harness(
    *,
    association_payload: Dict[str, Any] | None = None,
    launch_payload: Dict[str, Any] | None = None,
    explorer_payload: Dict[str, Any] | None = None,
    url_payload: Dict[str, Any] | None = None,
    verification: Dict[str, Any] | None = None,
    existing_windows: List[Dict[str, Any]] | None = None,
    active_after: Dict[str, Any] | None = None,
) -> tuple[Dict[str, int], Dict[str, Any]]:
    calls = {"association": 0, "launch": 0, "explorer": 0, "url": 0, "focus": 0}
    active_before = _default_window("0x1000", "File Explorer", "explorer.exe", active=True)
    windows_before = list(existing_windows or [active_before])
    active_final = active_after or active_before

    def current_context(limit: int = 20):
        observation = {
            "observation_token": "obs-eval",
            "observed_at": "2026-04-09T12:00:00",
            "active_window": active_before,
            "windows": windows_before[:limit],
            "window_count": len(windows_before),
        }
        return dict(active_before), [dict(item) for item in windows_before[:limit]], observation

    def latest_evidence(_token: str):
        return {}

    def association_open(*, target: str):
        calls["association"] += 1
        if not Path(target).exists():
            return {
                "ok": False,
                "backend": "shell",
                "reason": "target_missing",
                "message": "The requested target path does not exist.",
                "error": "Target path does not exist.",
                "data": {"target": target},
            }
        return dict(
            association_payload
            or {
                "ok": True,
                "backend": "shell",
                "reason": "association_opened",
                "message": f"Opened {Path(target).name} through its associated app.",
                "data": {"target": target, "basename": Path(target).name},
            }
        )

    def launch_open(*, executable: str, args: List[str] | None = None, cwd: str = "", env: Dict[str, str] | None = None):
        calls["launch"] += 1
        if not Path(executable).exists():
            return {
                "ok": False,
                "backend": "subprocess",
                "reason": "target_missing",
                "message": "The requested executable does not exist.",
                "error": "Executable path does not exist.",
                "data": {"target": executable},
            }
        return dict(
            launch_payload
            or {
                "ok": True,
                "backend": "subprocess",
                "reason": "process_started",
                "message": f"Started '{Path(executable).name}'.",
                "data": {"target": executable, "pid": 4242, "arguments": list(args or []), "cwd": cwd},
            }
        )

    def explorer_open(*, target: str, select_target: bool = False):
        calls["explorer"] += 1
        if not Path(target).exists():
            return {
                "ok": False,
                "backend": "explorer",
                "reason": "target_missing",
                "message": "The requested Explorer target does not exist.",
                "error": "Target path does not exist.",
                "data": {"target": target},
            }
        return dict(
            explorer_payload
            or {
                "ok": True,
                "backend": "explorer",
                "reason": "explorer_opened",
                "message": f"Opened '{Path(target).name}' in File Explorer.",
                "data": {"target": target, "pid": 5151, "select_target": bool(select_target)},
            }
        )

    def url_open(*, target: str):
        calls["url"] += 1
        return dict(
            url_payload
            or {
                "ok": True,
                "backend": "shell",
                "reason": "url_opened",
                "message": "Opened the URL in the system browser.",
                "data": {"target": target},
            }
        )

    def execute_recovery(args: Dict[str, Any], *, action_name: str = "desktop_open_target", **_kwargs):
        calls["focus"] += 1
        target_window = _default_window("0xBEEF", str(args.get("title", "") or "Viewer"), "viewer.exe", active=True)
        return {
            "target_window": target_window,
            "recovery": {"state": "ready", "reason": "existing_window_focus", "summary": "Focused the existing window."},
            "recovery_attempts": [{"attempt": 1, "strategy": "focus_then_verify"}],
            "readiness": {"state": "ready"},
            "visual_stability": {"stable": True},
            "process_context": {"process_name": "viewer.exe", "running": True},
            "scene": {"scene_changed": True},
        }

    def sample_verification(
        target_info: Dict[str, Any],
        *,
        strategy_family: str,
        before_active_window: Dict[str, Any],
        before_windows: List[Dict[str, Any]],
        launched_pid: int = 0,
        sample_count: int = 3,
        interval_ms: int = 180,
    ):
        if verification is not None:
            return dict(verification)
        return {
            "status": "verified_new_window",
            "confidence": "high",
            "note": "A new matching window surfaced and became active after the open attempt.",
            "matched_window": True,
            "matched_existing_window": False,
            "matched_active_window": True,
            "likely_opened_behind": False,
            "process_detected": bool(launched_pid),
            "active_window_changed": True,
            "matched_window_title": f"{target_info.get('basename', 'Target')} - Viewer",
            "matched_window_id": "0x2000",
            "matched_process_name": (target_info.get("viewer_process_hints", []) or ["viewer.exe"])[0],
            "match_score": 92,
            "strategy_family": strategy_family,
            "samples": [{"candidate_score": 92}],
        }

    replacements = {
        "_current_desktop_context": current_context,
        "_latest_evidence_ref_for_observation": latest_evidence,
        "_active_window_info": lambda: dict(active_final),
        "_enum_windows": lambda **_kwargs: [dict(item) for item in (existing_windows or [active_final])],
        "open_path_with_association": association_open,
        "launch_unowned_process": launch_open,
        "open_in_explorer": explorer_open,
        "open_url_with_shell": url_open,
        "_execute_window_recovery": execute_recovery,
        "_sample_open_verification": sample_verification,
    }
    return calls, replacements


def main() -> int:
    shutil.rmtree(TEMP_ROOT, ignore_errors=True)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    sample_dir = TEMP_ROOT / "samples"
    sample_dir.mkdir(parents=True, exist_ok=True)
    image_path = sample_dir / "photo.png"
    text_path = sample_dir / "notes.txt"
    exe_path = sample_dir / "viewer.exe"
    folder_path = sample_dir / "nested"
    folder_path.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"png")
    text_path.write_text("notes", encoding="utf-8")
    exe_path.write_bytes(b"MZ")

    results: List[CheckResult] = []
    memory_store = OperatorMemoryStore(TEMP_ROOT / "operator_memory.json")
    problem_store = ProblemRecordStore(TEMP_ROOT / "problem_records.json")
    runtime = ToolRuntime(get_tools())

    image_info = classify_open_target(str(image_path))
    text_info = classify_open_target(str(text_path))
    exe_info = classify_open_target(str(exe_path))
    folder_info = classify_open_target(str(folder_path))
    url_info = classify_open_target("https://example.com/path")

    _append(results, "classifies_image_target", image_info.get("target_classification") == "image_media_file", json.dumps(image_info, ensure_ascii=False), group="classification")
    _append(results, "classifies_text_target", text_info.get("target_classification") == "text_code_file", json.dumps(text_info, ensure_ascii=False), group="classification")
    _append(results, "classifies_executable_target", exe_info.get("target_classification") == "executable_program", json.dumps(exe_info, ensure_ascii=False), group="classification")
    _append(results, "classifies_folder_target", folder_info.get("target_classification") == "folder_directory", json.dumps(folder_info, ensure_ascii=False), group="classification")
    _append(results, "classifies_url_target", url_info.get("target_classification") == "url_web_resource", json.dumps(url_info, ensure_ascii=False), group="classification")

    strategy_for_doc = choose_windows_open_strategy(image_info)
    strategy_for_exe = choose_windows_open_strategy(exe_info)
    _append(results, "document_strategy_defaults_to_association", strategy_for_doc.get("strategy_family") == "association_open", json.dumps(strategy_for_doc, ensure_ascii=False), group="strategy")
    _append(results, "executable_strategy_defaults_to_launch", strategy_for_exe.get("strategy_family") == "executable_launch", json.dumps(strategy_for_exe, ensure_ascii=False), group="strategy")

    image_state = _state(memory_store, problem_store, goal="Open the image file")
    image_calls, image_replacements = _desktop_harness(
        existing_windows=[_default_window("0x1000", "File Explorer", "explorer.exe", active=True)],
        active_after=_default_window("0x2000", "photo.png - Photos", "microsoft.photos.exe", active=True),
    )
    with _patched_desktop(**image_replacements):
        image_result = desktop_tools.desktop_open_target({"target": str(image_path), "approval_status": "approved"})
    image_eval = _record_tool_step(image_state, tool="desktop_open_target", args={"target": str(image_path)}, result=image_result)
    _append(
        results,
        "image_open_uses_association_path",
        image_result.get("open_strategy", {}).get("strategy_family") == "association_open" and image_calls["association"] == 1 and image_calls["launch"] == 0,
        json.dumps({"calls": image_calls, "result": image_result}, ensure_ascii=False),
        group="open",
    )
    _append(
        results,
        "image_open_evaluates_successfully",
        image_eval.get("status") == "success",
        json.dumps(image_eval, ensure_ascii=False),
        group="open",
    )

    exe_state = _state(memory_store, problem_store, goal="Launch the app")
    exe_calls, exe_replacements = _desktop_harness(
        existing_windows=[_default_window("0x1000", "File Explorer", "explorer.exe", active=True)],
        active_after=_default_window("0x3000", "viewer", "viewer.exe", active=True),
    )
    with _patched_desktop(**exe_replacements):
        exe_result = desktop_tools.desktop_open_target({"target": str(exe_path), "approval_status": "approved"})
    exe_eval = _record_tool_step(exe_state, tool="desktop_open_target", args={"target": str(exe_path)}, result=exe_result)
    _append(
        results,
        "executable_open_uses_launch_path",
        exe_result.get("open_strategy", {}).get("strategy_family") == "executable_launch" and exe_calls["launch"] == 1 and exe_calls["association"] == 0,
        json.dumps({"calls": exe_calls, "result": exe_result}, ensure_ascii=False),
        group="open",
    )
    _append(
        results,
        "executable_open_evaluates_successfully",
        exe_eval.get("status") == "success",
        json.dumps(exe_eval, ensure_ascii=False),
        group="open",
    )

    missing_target = sample_dir / "missing.pdf"
    missing_state = _state(memory_store, problem_store, goal="Open a missing document")
    missing_calls, missing_replacements = _desktop_harness()
    with _patched_desktop(**missing_replacements):
        missing_result = desktop_tools.desktop_open_target({"target": str(missing_target), "approval_status": "approved"})
    missing_eval = _record_tool_step(missing_state, tool="desktop_open_target", args={"target": str(missing_target)}, result=missing_result)
    missing_problem = ((missing_state.steps[-1].get("result", {}) if isinstance(missing_state.steps[-1].get("result", {}), dict) else {}).get("problem", {}) if missing_state.steps else {})
    _append(
        results,
        "missing_target_fails_cleanly_without_verifier_noise",
        missing_result.get("open_verification", {}).get("status") == "not_attempted_missing_target" and missing_eval.get("status") == "failure",
        json.dumps({"result": missing_result, "evaluation": missing_eval}, ensure_ascii=False),
        group="failure",
    )
    _append(
        results,
        "missing_target_problem_category_is_useful",
        str(missing_problem.get("failure_category", "")) == "wrong_target_or_bad_target_proposal",
        json.dumps(missing_problem, ensure_ascii=False),
        group="failure",
    )

    launcher_state = _state(memory_store, problem_store, goal="Open the image file")
    launcher_target = classify_open_target(str(image_path))
    launcher_result = {
        "ok": False,
        "action": "desktop_open_target",
        "summary": "Tried to launch the image like a program.",
        "error": "[WinError 193] %1 is not a valid Win32 application",
        "open_target": launcher_target,
        "open_strategy": {
            "strategy_family": "executable_launch",
            "reason": "Launch the executable program directly.",
            "requested_method": "launch",
        },
        "open_verification": {
            "status": "launcher_failed",
            "confidence": "high",
            "note": "The executable launcher path failed before the target could open.",
            "strategy_family": "executable_launch",
            "samples": [],
        },
        "open_result": {
            "backend": "subprocess",
            "reason": "error",
            "message": "Could not start the target.",
            "data": {"target": str(image_path)},
        },
        "command_result": {"returncode": 193, "stderr_excerpt": "[WinError 193] %1 is not a valid Win32 application"},
    }
    launcher_eval = _record_tool_step(launcher_state, tool="desktop_open_target", args={"target": str(image_path)}, result=launcher_result)
    launcher_problem = ((launcher_state.steps[-1].get("result", {}) if isinstance(launcher_state.steps[-1].get("result", {}), dict) else {}).get("problem", {}) if launcher_state.steps else {})
    launcher_hints = memory_store.lookup_patterns(
        domain="desktop",
        tool_name="desktop_open_target",
        target_signature=str(launcher_eval.get("target_signature", "")).strip(),
        goal="Open the image file",
    )
    _append(
        results,
        "launcher_semantic_failure_creates_problem_record",
        str(launcher_problem.get("failure_category", "")) == "launcher_file_open_semantics",
        json.dumps(launcher_problem, ensure_ascii=False),
        group="launcher_failure",
    )
    _append(
        results,
        "launcher_failure_captures_useful_lesson",
        bool(launcher_hints.get("lessons")) and "association-open" in json.dumps(launcher_hints, ensure_ascii=False).lower(),
        json.dumps(launcher_hints, ensure_ascii=False),
        group="launcher_failure",
    )

    uncertain_state = _state(memory_store, problem_store, goal="Open the image file")
    uncertain_result = {
        "ok": True,
        "action": "desktop_open_target",
        "summary": "Requested Windows to open 'photo.png' through its associated app.",
        "open_target": launcher_target,
        "open_strategy": {"strategy_family": "association_open"},
        "open_verification": {
            "status": "likely_opened_background",
            "confidence": "low",
            "note": "A matching existing window was detected, but it did not clearly surface to the foreground.",
            "matched_window": True,
            "matched_existing_window": True,
            "matched_active_window": False,
            "likely_opened_behind": True,
            "process_detected": False,
            "samples": [],
        },
        "open_result": {"backend": "shell", "reason": "association_opened", "message": "Requested Windows to open the image."},
    }
    uncertain_eval = _record_tool_step(uncertain_state, tool="desktop_open_target", args={"target": str(image_path)}, result=uncertain_result)
    _append(
        results,
        "uncertain_background_open_is_not_false_success",
        uncertain_eval.get("status") == "uncertain" and str(uncertain_eval.get("verification_status", "")) == "likely_opened_background",
        json.dumps(uncertain_eval, ensure_ascii=False),
        group="verification",
    )

    repeat_state = _state(memory_store, problem_store, goal="Open the image file")
    repeat_failure_result = {
        "ok": True,
        "action": "desktop_open_target",
        "summary": "Requested Windows to open the image.",
        "open_target": launcher_target,
        "open_strategy": {"strategy_family": "association_open"},
        "open_verification": {
            "status": "brief_signal_only",
            "confidence": "low",
            "note": "A brief matching window signal appeared, but it was not stable enough to confirm success.",
            "matched_window": False,
            "matched_existing_window": False,
            "matched_active_window": False,
            "likely_opened_behind": False,
            "process_detected": False,
            "samples": [],
        },
        "open_result": {"backend": "shell", "reason": "association_opened", "message": "Requested Windows to open the image."},
    }
    _record_tool_step(repeat_state, tool="desktop_open_target", args={"target": str(image_path)}, result=repeat_failure_result)
    _record_tool_step(repeat_state, tool="desktop_open_target", args={"target": str(image_path)}, result=repeat_failure_result)
    prepared_repeat_args = runtime.prepare_args("desktop_open_target", {"target": str(image_path)}, repeat_state, planning_goal="Open the image file")
    repeat_calls, repeat_replacements = _desktop_harness(
        explorer_payload={
            "ok": True,
            "backend": "explorer",
            "reason": "explorer_opened",
            "message": f"Opened File Explorer and selected '{image_path.name}'.",
            "data": {"target": str(image_path), "pid": 6161, "select_target": True},
        },
        verification={
            "status": "verified_new_window",
            "confidence": "medium",
            "note": "Explorer surfaced for the target.",
            "matched_window": True,
            "matched_existing_window": False,
            "matched_active_window": True,
            "likely_opened_behind": False,
            "process_detected": False,
            "active_window_changed": True,
            "matched_window_title": "File Explorer",
            "matched_window_id": "0x4000",
            "matched_process_name": "explorer.exe",
            "match_score": 88,
            "strategy_family": "explorer_assisted_ui",
            "samples": [{"candidate_score": 88}],
        },
    )
    with _patched_desktop(**repeat_replacements):
        repeat_result = desktop_tools.desktop_open_target({**prepared_repeat_args, "approval_status": "approved"})
    _append(
        results,
        "repeated_failure_prefers_improved_strategy",
        "association_open" in list(prepared_repeat_args.get("avoid_strategy_families", [])) and repeat_result.get("open_strategy", {}).get("strategy_family") == "explorer_assisted_ui" and repeat_calls["explorer"] == 1,
        json.dumps({"prepared_args": prepared_repeat_args, "result": repeat_result, "calls": repeat_calls}, ensure_ascii=False),
        group="retry",
    )

    alternate_state = _state(memory_store, problem_store, goal="Use another method through File Explorer to open the image file")
    _record_tool_step(alternate_state, tool="desktop_open_target", args={"target": str(image_path)}, result=repeat_failure_result)
    prepared_alt_args = runtime.prepare_args(
        "desktop_open_target",
        {"target": str(image_path)},
        alternate_state,
        planning_goal="Use another method through File Explorer to open the image file",
    )
    same_family_alt_args = {
        "target": str(image_path),
        "target_signature": str(prepared_alt_args.get("target_signature", "")).strip(),
        "target_type": str(prepared_alt_args.get("target_type", "")).strip(),
        "preferred_method": "association_open",
        "force_strategy_switch": True,
    }
    alt_guard = guard_repeated_failed_open_family(alternate_state, same_family_alt_args)
    alt_calls, alt_replacements = _desktop_harness(
        explorer_payload={
            "ok": True,
            "backend": "explorer",
            "reason": "explorer_opened",
            "message": f"Opened File Explorer and selected '{image_path.name}'.",
            "data": {"target": str(image_path), "pid": 7171, "select_target": True},
        }
    )
    with _patched_desktop(**alt_replacements):
        alt_result = desktop_tools.desktop_open_target({**prepared_alt_args, "approval_status": "approved"})
    _append(
        results,
        "alternate_method_request_forces_material_strategy_switch",
        bool(prepared_alt_args.get("force_strategy_switch", False)) and alt_result.get("open_strategy", {}).get("strategy_family") != "association_open" and alt_calls["explorer"] == 1,
        json.dumps({"prepared_args": prepared_alt_args, "result": alt_result, "calls": alt_calls}, ensure_ascii=False),
        group="retry",
    )
    _append(
        results,
        "force_switch_guard_blocks_same_failed_family",
        bool(alt_guard.get("blocked", False)) and str(alt_guard.get("reason", "")) == "open_strategy_family_exhausted",
        json.dumps({"guard_args": same_family_alt_args, "guard_result": alt_guard}, ensure_ascii=False),
        group="retry",
    )

    _write_report(results)
    passed = sum(1 for item in results if item.passed)
    failed = len(results) - passed
    print(f"Windows open eval: {passed}/{len(results)} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
