from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Mapping

from core.browser_tasks import (
    browser_task_workflow_pattern,
    infer_browser_task_name,
    infer_browser_task_step,
)


ToolFunc = Callable[[Dict[str, Any]], Dict[str, Any]]

BROWSER_APPROVAL_CONTROLLED_TOOLS = {
    "browser_open_page",
    "browser_click",
    "browser_follow_link",
}
DESKTOP_APPROVAL_CONTROLLED_TOOLS = {
    "desktop_click_point",
    "desktop_press_key",
    "desktop_type_text",
}
BROWSER_APPROVAL_POSITIVE_PHRASES = (
    "approval granted",
    "approval_status=approved",
    "approved to continue",
    "explicit approval",
    "explicitly approve",
    "explicitly approved",
    "is now approved",
    "is now explicitly approved",
    "now approved",
    "resume that exact paused",
    "resume the paused",
)
BROWSER_APPROVAL_OVERRIDE_PHRASES = (
    "the paused browser checkpoint is now explicitly approved",
    "resume the exact paused browser tool immediately with approval_status=approved",
    "operator control: the paused browser checkpoint is now explicitly approved",
)
BROWSER_APPROVAL_NEGATIVE_PHRASES = (
    "approval required",
    "do not approve",
    "needs approval",
    "not approved",
    "pending approval",
    "wait for approval",
    "without approval",
)
DESKTOP_APPROVAL_POSITIVE_PHRASES = (
    "approval granted",
    "approval_status=approved",
    "approved to continue",
    "approved to click",
    "approved to press",
    "approved to type",
    "desktop action is now approved",
    "desktop step is now approved",
    "explicitly approve the paused desktop action",
    "resume the exact paused desktop tool immediately with approval_status=approved",
)
DESKTOP_APPROVAL_OVERRIDE_PHRASES = (
    "the paused desktop action is now explicitly approved",
    "resume the exact paused desktop tool immediately with approval_status=approved",
)
DESKTOP_APPROVAL_NEGATIVE_PHRASES = (
    "approval required",
    "do not approve",
    "needs approval",
    "not approved",
    "pending approval",
    "wait for approval",
    "without approval",
)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Dict[str, Any]
    func: ToolFunc

    @classmethod
    def from_value(cls, value: Mapping[str, Any] | "ToolSpec") -> "ToolSpec":
        if isinstance(value, cls):
            return value

        name = str(value.get("name", "")).strip()
        description = str(value.get("description", "")).strip()
        input_schema = value.get("input_schema", {})
        func = value.get("func")

        if not name:
            raise ValueError("Tool is missing a name.")
        if not description:
            raise ValueError(f"Tool '{name}' is missing a description.")
        if not isinstance(input_schema, dict):
            raise ValueError(f"Tool '{name}' has an invalid input_schema.")
        if not callable(func):
            raise ValueError(f"Tool '{name}' has an invalid func.")

        return cls(
            name=name,
            description=description,
            input_schema=dict(input_schema),
            func=func,
        )

    def to_planner_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": dict(self.input_schema),
        }


class ToolRuntime:
    def __init__(self, tools: Iterable[Mapping[str, Any] | ToolSpec]):
        self.tools = [ToolSpec.from_value(tool) for tool in tools]
        self.tool_map: Dict[str, ToolSpec] = {}
        for tool in self.tools:
            if tool.name in self.tool_map:
                raise ValueError(f"Duplicate tool name registered: {tool.name}")
            self.tool_map[tool.name] = tool

    def planner_tools(self) -> list[Dict[str, Any]]:
        return [tool.to_planner_dict() for tool in self.tools]

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self.tool_map

    def execute(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        return self.tool_map[tool_name].func(args)

    def latest_completed_result(self, task_state, tool_name: str) -> Dict[str, Any] | None:
        for step in reversed(task_state.steps):
            if step.get("tool") != tool_name or step.get("status") != "completed":
                continue
            result = step.get("result", {})
            if isinstance(result, dict):
                return result
        return None

    def prepare_args(self, tool_name: str, args: Any, task_state, planning_goal: str | None = None) -> Dict[str, Any]:
        prepared_args = dict(args) if isinstance(args, dict) else {}

        if tool_name.startswith("browser_"):
            self._prepare_browser_args(tool_name, task_state, prepared_args, planning_goal=planning_goal)
        elif tool_name.startswith("desktop_"):
            self._prepare_desktop_args(tool_name, task_state, prepared_args, planning_goal=planning_goal)

        preparer = getattr(self, f"_prepare_{tool_name}_args", None)
        if callable(preparer):
            preparer(task_state, prepared_args)

        return prepared_args

    def _goal_has_explicit_browser_approval(self, goal: str) -> bool:
        text = " ".join(str(goal or "").strip().lower().split())
        if not text:
            return False
        if any(phrase in text for phrase in BROWSER_APPROVAL_OVERRIDE_PHRASES):
            return True
        if any(phrase in text for phrase in BROWSER_APPROVAL_NEGATIVE_PHRASES):
            return False
        return any(phrase in text for phrase in BROWSER_APPROVAL_POSITIVE_PHRASES)

    def goal_has_explicit_browser_approval(self, goal: str) -> bool:
        return self._goal_has_explicit_browser_approval(goal)

    def _goal_has_explicit_desktop_approval(self, goal: str) -> bool:
        text = " ".join(str(goal or "").strip().lower().split())
        if not text:
            return False
        if any(phrase in text for phrase in DESKTOP_APPROVAL_OVERRIDE_PHRASES):
            return True
        if any(phrase in text for phrase in DESKTOP_APPROVAL_NEGATIVE_PHRASES):
            return False
        return any(phrase in text for phrase in DESKTOP_APPROVAL_POSITIVE_PHRASES)

    def goal_has_explicit_desktop_approval(self, goal: str) -> bool:
        return self._goal_has_explicit_desktop_approval(goal)

    def _prepare_browser_args(self, tool_name: str, task_state, args: Dict[str, Any], planning_goal: str | None = None):
        args.setdefault("session_id", task_state.browser_session_id or "default")
        args.setdefault("timeout_ms", 6000)
        args.setdefault("max_text_chars", 1400)
        args.setdefault("max_elements", 6)
        args.setdefault("max_retries", 1)
        args.setdefault("allow_reinspect", True)
        args.setdefault("allow_reload", True)

        if tool_name != "browser_open_page":
            if getattr(task_state, "browser_workflow_name", ""):
                args.setdefault("workflow_name", task_state.browser_workflow_name)
            if getattr(task_state, "browser_workflow_pattern", ""):
                args.setdefault("workflow_pattern", task_state.browser_workflow_pattern)
            if getattr(task_state, "browser_workflow_next_step", ""):
                args.setdefault("workflow_step", task_state.browser_workflow_next_step)
            if getattr(task_state, "browser_last_successful_action", ""):
                args.setdefault("workflow_last_successful_action", task_state.browser_last_successful_action)
        else:
            args.pop("workflow_step", None)

        checkpoint_pending = bool(getattr(task_state, "browser_checkpoint_pending", False))
        checkpoint_tool = getattr(task_state, "browser_checkpoint_tool", "")
        checkpoint_reason = getattr(task_state, "browser_checkpoint_reason", "")
        checkpoint_step = getattr(task_state, "browser_checkpoint_step", "")
        checkpoint_target = getattr(task_state, "browser_checkpoint_target", "")
        checkpoint_args = getattr(task_state, "browser_checkpoint_resume_args", {})
        goal_text = planning_goal if isinstance(planning_goal, str) and planning_goal.strip() else getattr(task_state, "goal", "")
        goal_has_explicit_approval = self._goal_has_explicit_browser_approval(goal_text)

        if tool_name in BROWSER_APPROVAL_CONTROLLED_TOOLS:
            approval_status = str(args.get("approval_status", "")).strip().lower()
            if approval_status == "approved" and not goal_has_explicit_approval:
                args.pop("approval_status", None)

        if checkpoint_pending:
            if checkpoint_reason:
                args.setdefault("checkpoint_reason", checkpoint_reason)
            if tool_name == checkpoint_tool:
                if checkpoint_step:
                    args.setdefault("workflow_step", checkpoint_step)

                approval_status = str(args.get("approval_status", "")).strip().lower()
                if not approval_status and goal_has_explicit_approval and tool_name in BROWSER_APPROVAL_CONTROLLED_TOOLS:
                    args["approval_status"] = "approved"
                    approval_status = "approved"

                if approval_status == "approved" and isinstance(checkpoint_args, dict):
                    for key, value in checkpoint_args.items():
                        args[key] = value
                    if checkpoint_step:
                        args["workflow_step"] = checkpoint_step
                    args["resume_from_checkpoint"] = True
                    args["checkpoint_required"] = False
                else:
                    args.setdefault("checkpoint_required", True)
            elif tool_name in {"browser_open_page", "browser_inspect_page"}:
                if getattr(task_state, "browser_current_url", ""):
                    args.setdefault("url", task_state.browser_current_url)
                if checkpoint_target:
                    args.setdefault("expected_target", checkpoint_target)

        current_task_name = getattr(task_state, "browser_task_name", "")
        current_task_status = str(getattr(task_state, "browser_task_status", "")).strip().lower()
        if current_task_status in {"completed", "blocked"} and not any(
            str(args.get(key, "")).strip() for key in ("browser_task_name", "task_name")
        ):
            current_task_name = ""

        task_name = infer_browser_task_name(
            tool_name,
            args,
            current_task_name=current_task_name,
            goal=getattr(task_state, "goal", ""),
        )
        if task_name:
            args.setdefault("browser_task_name", task_name)
            if not args.get("browser_task_step"):
                continued_task_step = ""
                if (
                    getattr(task_state, "browser_task_name", "") == task_name
                    and getattr(task_state, "browser_task_next_step", "")
                    and tool_name in {"browser_inspect_page", "browser_extract_text", "browser_follow_link"}
                ):
                    continued_task_step = task_state.browser_task_next_step
                args.setdefault(
                    "browser_task_step",
                    continued_task_step or infer_browser_task_step(task_name, tool_name, args.get("browser_task_step", "")),
                )

            task_pattern = browser_task_workflow_pattern(task_name)
            if task_pattern:
                args.setdefault("workflow_pattern", task_pattern)

        if not args.get("workflow_pattern"):
            if tool_name in {"browser_type", "browser_click"}:
                args.setdefault("workflow_pattern", "form_flow")
            elif tool_name in {"browser_follow_link", "browser_extract_text"}:
                args.setdefault("workflow_pattern", "navigation_extract_flow")

        if tool_name in {"browser_open_page", "browser_inspect_page"}:
            args.setdefault("headless", True)

    def _prepare_desktop_args(self, tool_name: str, task_state, args: Dict[str, Any], planning_goal: str | None = None):
        goal_text = planning_goal if isinstance(planning_goal, str) and planning_goal.strip() else getattr(task_state, "goal", "")
        goal_has_explicit_approval = self._goal_has_explicit_desktop_approval(goal_text)
        targeted_window_tool = tool_name in {
            "desktop_focus_window",
            "desktop_inspect_window_state",
            "desktop_recover_window",
            "desktop_wait_for_window_ready",
        }
        has_explicit_window_target = any(
            str(args.get(key, "")).strip()
            for key in ("title", "match", "window_id", "expected_window_title", "expected_window_id")
        )

        if getattr(task_state, "desktop_observation_token", ""):
            args.setdefault("observation_token", task_state.desktop_observation_token)
        if getattr(task_state, "desktop_active_window_title", "") and not (targeted_window_tool and has_explicit_window_target):
            args.setdefault("expected_window_title", task_state.desktop_active_window_title)
        if getattr(task_state, "desktop_active_window_id", "") and not (targeted_window_tool and has_explicit_window_target):
            args.setdefault("expected_window_id", task_state.desktop_active_window_id)

        if tool_name == "desktop_list_windows":
            args.setdefault("limit", 12)
        elif tool_name == "desktop_get_active_window":
            args.setdefault("limit", 12)
        elif tool_name == "desktop_inspect_window_state":
            args.setdefault("limit", 12)
            args.setdefault("ui_limit", 8)
            args.setdefault("check_visual_stability", True)
            args.setdefault("stability_samples", 3)
            args.setdefault("stability_interval_ms", 120)
        elif tool_name == "desktop_recover_window":
            args.setdefault("limit", 12)
            args.setdefault("ui_limit", 8)
            args.setdefault("max_attempts", 2)
            args.setdefault("wait_seconds", 2.2)
            args.setdefault("poll_interval_seconds", 0.16)
            args.setdefault("stability_samples", 3)
            args.setdefault("stability_interval_ms", 120)
        elif tool_name == "desktop_wait_for_window_ready":
            args.setdefault("limit", 12)
            args.setdefault("ui_limit", 8)
            args.setdefault("wait_seconds", 2.2)
            args.setdefault("poll_interval_seconds", 0.16)
            args.setdefault("stability_samples", 3)
            args.setdefault("stability_interval_ms", 120)
        elif tool_name == "desktop_capture_screenshot":
            args.setdefault("scope", "active_window")
            args.setdefault("limit", 12)
        elif tool_name == "desktop_click_point":
            args.setdefault("max_observation_age_seconds", 45)
        elif tool_name == "desktop_press_key":
            args.setdefault("repeat", 1)
            args.setdefault("max_observation_age_seconds", 45)
        elif tool_name == "desktop_type_text":
            args.setdefault("max_text_length", 160)
            args.setdefault("max_observation_age_seconds", 45)

        if tool_name in DESKTOP_APPROVAL_CONTROLLED_TOOLS:
            approval_status = str(args.get("approval_status", "")).strip().lower()
            if approval_status == "approved" and not goal_has_explicit_approval:
                args.pop("approval_status", None)

        checkpoint_pending = bool(getattr(task_state, "desktop_checkpoint_pending", False))
        checkpoint_tool = getattr(task_state, "desktop_checkpoint_tool", "")
        checkpoint_reason = getattr(task_state, "desktop_checkpoint_reason", "")
        checkpoint_target = getattr(task_state, "desktop_checkpoint_target", "")
        checkpoint_args = getattr(task_state, "desktop_checkpoint_resume_args", {})

        if checkpoint_pending:
            if checkpoint_reason:
                args.setdefault("checkpoint_reason", checkpoint_reason)
            if tool_name == checkpoint_tool:
                approval_status = str(args.get("approval_status", "")).strip().lower()
                if not approval_status and goal_has_explicit_approval and tool_name in DESKTOP_APPROVAL_CONTROLLED_TOOLS:
                    args["approval_status"] = "approved"
                    approval_status = "approved"

                if approval_status == "approved" and isinstance(checkpoint_args, dict):
                    for key, value in checkpoint_args.items():
                        args[key] = value
                    args["resume_from_checkpoint"] = True
                    args["checkpoint_required"] = False
                else:
                    args.setdefault("checkpoint_required", True)
            elif tool_name in {
                "desktop_focus_window",
                "desktop_inspect_window_state",
                "desktop_recover_window",
                "desktop_wait_for_window_ready",
                "desktop_capture_screenshot",
                "desktop_get_active_window",
                "desktop_list_windows",
            }:
                if checkpoint_target:
                    args.setdefault("target_window", checkpoint_target)

    def _prepare_inspect_project_args(self, task_state, args: Dict[str, Any]):
        args.setdefault("goal", task_state.goal)
        args.setdefault("top_k_relevant", 3)

    def _prepare_suggest_commands_args(self, task_state, args: Dict[str, Any]):
        args.setdefault("goal", task_state.goal)
        args.setdefault("priority_files", task_state.priority_files[:4])
        args.setdefault("known_files", task_state.known_files[-8:])
        args.setdefault("known_dirs", task_state.known_dirs[-6:])
        args.setdefault("recent_notes", task_state.memory_notes[-6:])
        args.setdefault("max_suggestions", 3)

        latest_compare = self.latest_completed_result(task_state, "compare_files")
        self._apply_compare_context(args, latest_compare)
        self._apply_base_path_context(args, task_state)

    def _prepare_plan_patch_args(self, task_state, args: Dict[str, Any]):
        args.setdefault("goal", task_state.goal)
        args.setdefault("priority_files", task_state.priority_files[:5])
        args.setdefault("known_files", task_state.known_files[-10:])
        args.setdefault("recent_notes", task_state.memory_notes[-6:])
        args.setdefault("max_files_to_change", 4)

        latest_compare = self.latest_completed_result(task_state, "compare_files")
        self._apply_compare_context(args, latest_compare)

        latest_suggestions = self.latest_completed_result(task_state, "suggest_commands")
        if latest_suggestions:
            args.setdefault("suggested_commands", latest_suggestions.get("suggestions", [])[:4])

        self._apply_base_path_context(args, task_state)

    def _prepare_draft_proposed_edits_args(self, task_state, args: Dict[str, Any]):
        args.setdefault("goal", task_state.goal)
        args.setdefault("priority_files", task_state.priority_files[:5])
        args.setdefault("known_files", task_state.known_files[-10:])
        args.setdefault("recent_notes", task_state.memory_notes[-6:])
        args.setdefault("max_files_to_draft", 3)

        latest_compare = self.latest_completed_result(task_state, "compare_files")
        self._apply_compare_context(args, latest_compare)

        latest_suggestions = self.latest_completed_result(task_state, "suggest_commands")
        if latest_suggestions:
            args.setdefault("suggested_commands", latest_suggestions.get("suggestions", [])[:4])

        latest_plan = self.latest_completed_result(task_state, "plan_patch")
        if latest_plan:
            args.setdefault("planned_files", latest_plan.get("files_to_change", [])[:4])
            args.setdefault("plan_confidence", str(latest_plan.get("confidence", "")).strip())
            args.setdefault("plan_uncertainties", latest_plan.get("uncertainties", [])[:3])

        self._apply_base_path_context(args, task_state)

    def _prepare_build_review_bundle_args(self, task_state, args: Dict[str, Any]):
        args.setdefault("goal", task_state.goal)
        args.setdefault("recent_notes", task_state.memory_notes[-6:])
        args.setdefault("max_files", 4)
        args.setdefault("max_commands", 3)

        latest_plan = self.latest_completed_result(task_state, "plan_patch")
        if latest_plan:
            args.setdefault("planned_changes", latest_plan.get("files_to_change", [])[:4])
            args.setdefault("plan_confidence", str(latest_plan.get("confidence", "")).strip())
            args.setdefault("plan_uncertainties", latest_plan.get("uncertainties", [])[:3])

        latest_drafts = self.latest_completed_result(task_state, "draft_proposed_edits")
        if latest_drafts:
            args.setdefault("proposed_edits", latest_drafts.get("drafts", [])[:4])
            args.setdefault("draft_confidence", str(latest_drafts.get("confidence", "")).strip())
            args.setdefault("draft_uncertainties", latest_drafts.get("uncertainties", [])[:3])

        latest_suggestions = self.latest_completed_result(task_state, "suggest_commands")
        if latest_suggestions:
            args.setdefault("suggested_commands", latest_suggestions.get("suggestions", [])[:4])

    def _prepare_apply_approved_edits_args(self, task_state, args: Dict[str, Any]):
        args.setdefault("max_files", 4)
        args.setdefault("max_bytes_per_file", 60_000)

        latest_bundle = self.latest_completed_result(task_state, "build_review_bundle")
        if latest_bundle:
            args.setdefault("review_bundle", latest_bundle)

    def _apply_compare_context(self, args: Dict[str, Any], latest_compare: Dict[str, Any] | None):
        if not latest_compare:
            return

        args.setdefault("path_a", str(latest_compare.get("path_a", "")).strip())
        args.setdefault("path_b", str(latest_compare.get("path_b", "")).strip())
        args.setdefault("compare_summary", str(latest_compare.get("summary", "")).strip())
        if "files_differ" not in args and isinstance(latest_compare.get("differ"), bool):
            args["files_differ"] = bool(latest_compare.get("differ"))

    def _apply_base_path_context(self, args: Dict[str, Any], task_state):
        latest_inspection = self.latest_completed_result(task_state, "inspect_project")
        if latest_inspection:
            args.setdefault("base_path", str(latest_inspection.get("path", "")).strip())
        elif task_state.known_dirs:
            args.setdefault("base_path", task_state.known_dirs[-1])

