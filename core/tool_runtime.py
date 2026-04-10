from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Mapping

from core.browser_tasks import (
    browser_task_workflow_pattern,
    infer_browser_task_name,
    infer_browser_task_step,
)
from core.capability_profiles import is_lab_profile, normalize_execution_profile
from core.tool_policy import build_tool_policy_snapshot, classify_tool_risk
from core.windows_opening import classify_open_target, infer_open_request_preferences, open_target_signature


ToolFunc = Callable[[Dict[str, Any]], Dict[str, Any]]

BROWSER_APPROVAL_CONTROLLED_TOOLS = {
    "browser_open_page",
    "browser_click",
    "browser_follow_link",
}
DESKTOP_APPROVAL_CONTROLLED_TOOLS = {
    "desktop_click_point",
    "desktop_move_mouse",
    "desktop_hover_point",
    "desktop_click_mouse",
    "desktop_scroll",
    "desktop_press_key",
    "desktop_press_key_sequence",
    "desktop_type_text",
    "desktop_start_process",
    "desktop_stop_process",
    "desktop_run_command",
    "desktop_open_target",
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
    "approved to scroll",
    "approved to hover",
    "approved to move the mouse",
    "approved to press",
    "approved to type",
    "approved to open",
    "approved to open the target",
    "approved to run the command",
    "approved to start the process",
    "approved to stop the process",
    "desktop action is now approved",
    "desktop open is now approved",
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

    def planner_tools(self, task_state=None) -> list[Dict[str, Any]]:
        planner_tools: list[Dict[str, Any]] = []
        lab_enabled = self._lab_mode_enabled(task_state)
        for tool in self.tools:
            if tool.name == "lab_run_shell" and not lab_enabled:
                continue
            tool_dict = tool.to_planner_dict()
            policy = classify_tool_risk(tool.name)
            planner_note = str(policy.get("planner_note", "")).strip()
            if planner_note:
                tool_dict["description"] = f"{tool_dict['description']} Policy: {planner_note}"
            planner_tools.append(tool_dict)
        return planner_tools

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self.tool_map

    def tool_risk(self, tool_name: str, args: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return classify_tool_risk(tool_name, args=args)

    def tool_policy_snapshot(self) -> Dict[str, Any]:
        return build_tool_policy_snapshot(tool.name for tool in self.tools)

    def tool_catalog(self) -> list[Dict[str, Any]]:
        items: list[Dict[str, Any]] = []
        for tool in self.tools:
            items.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": dict(tool.input_schema),
                    "policy": self.tool_risk(tool.name),
                }
            )
        return items

    def execute(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        return self.tool_map[tool_name].func(args)

    def _control_snapshot(self, task_state) -> Dict[str, Any]:
        cached = getattr(task_state, "_last_control_snapshot", None)
        if isinstance(cached, dict) and cached:
            return cached
        getter = getattr(task_state, "get_control_snapshot", None)
        if not callable(getter):
            return {}
        try:
            snapshot = getter()
        except Exception:
            return {}
        return snapshot if isinstance(snapshot, dict) else {}

    def _environment_awareness(self, task_state) -> Dict[str, Any]:
        awareness = getattr(task_state, "_environment_awareness", None)
        return awareness if isinstance(awareness, dict) else {}

    def _lab_mode_enabled(self, task_state) -> bool:
        if task_state is None:
            return False
        awareness = self._environment_awareness(task_state)
        profile = normalize_execution_profile(
            getattr(task_state, "execution_profile", "") or awareness.get("execution_profile", "")
        )
        return bool(is_lab_profile(profile) and awareness.get("lab_armed", False))

    def _proposal_matches_tool(self, proposal: Dict[str, Any], tool_name: str) -> bool:
        actions = [
            str(item).strip()
            for item in list(proposal.get("suggested_next_actions", []))
            if str(item).strip()
        ]
        if tool_name in actions:
            return True
        target_kind = str(proposal.get("target_kind", "")).strip().lower()
        if tool_name in {"desktop_focus_window", "desktop_recover_window", "desktop_wait_for_window_ready"}:
            return target_kind in {"focus_candidate", "recovery_candidate", "ui_area", "region"}
        if tool_name in {"desktop_click_mouse", "desktop_click_point", "desktop_scroll"}:
            return target_kind in {"point", "region", "ui_area"}
        if tool_name in {"desktop_type_text", "desktop_press_key", "desktop_press_key_sequence"}:
            return target_kind in {"ui_area", "region", "focus_candidate"}
        return False

    def _latest_desktop_target_hint(self, task_state, tool_name: str) -> Dict[str, Any]:
        snapshot = self._control_snapshot(task_state)
        desktop = snapshot.get("desktop", {}) if isinstance(snapshot.get("desktop", {}), dict) else {}
        for key in ("selected_target_proposals", "checkpoint_target_proposals"):
            context = desktop.get(key, {}) if isinstance(desktop.get(key, {}), dict) else {}
            proposals = context.get("proposals", []) if isinstance(context.get("proposals", []), list) else []
            if not proposals:
                continue
            fallback: Dict[str, Any] = {}
            for proposal in proposals:
                if not isinstance(proposal, dict):
                    continue
                if not fallback:
                    fallback = proposal
                if self._proposal_matches_tool(proposal, tool_name):
                    return proposal
            if fallback:
                return fallback
        return {}

    def _latest_email_activity(self, task_state) -> Dict[str, Any]:
        snapshot = self._control_snapshot(task_state)
        email = snapshot.get("email", {}) if isinstance(snapshot.get("email", {}), dict) else {}
        return email

    def _prepare_email_read_thread_args(self, task_state, args: Dict[str, Any]):
        email = self._latest_email_activity(task_state)
        if not str(args.get("thread_id", "")).strip():
            thread_id = str(email.get("thread_id", "")).strip()
            if thread_id:
                args["thread_id"] = thread_id
        args.setdefault("max_messages", 8)

    def _prepare_email_prepare_reply_draft_args(self, task_state, args: Dict[str, Any]):
        email = self._latest_email_activity(task_state)
        if not str(args.get("thread_id", "")).strip():
            thread_id = str(email.get("thread_id", "")).strip()
            if thread_id:
                args["thread_id"] = thread_id

    def _prepare_email_prepare_forward_draft_args(self, task_state, args: Dict[str, Any]):
        email = self._latest_email_activity(task_state)
        if not str(args.get("thread_id", "")).strip():
            thread_id = str(email.get("thread_id", "")).strip()
            if thread_id:
                args["thread_id"] = thread_id

    def _prepare_email_send_draft_args(self, task_state, args: Dict[str, Any]):
        email = self._latest_email_activity(task_state)
        if not str(args.get("draft_id", "")).strip():
            draft_id = str(email.get("draft_id", "")).strip()
            if draft_id:
                args["draft_id"] = draft_id

    def _prepare_lab_run_shell_args(self, task_state, args: Dict[str, Any]):
        awareness = self._environment_awareness(task_state)
        execution_profile = normalize_execution_profile(
            str(args.get("execution_profile", "")).strip()
            or getattr(task_state, "execution_profile", "")
            or awareness.get("execution_profile", "")
        )
        args.setdefault("execution_profile", execution_profile)
        args.setdefault("lab_armed", bool(awareness.get("lab_armed", False)))
        args.setdefault("shell_kind", "powershell")
        snapshot = self._control_snapshot(task_state)
        lab = snapshot.get("lab", {}) if isinstance(snapshot.get("lab", {}), dict) else {}
        if not str(args.get("workspace_id", "")).strip():
            workspace_id = str(lab.get("workspace_id", "")).strip()
            if workspace_id:
                args["workspace_id"] = workspace_id

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

    def _goal_requests_desktop_strategy_switch(self, goal: str) -> bool:
        text = " ".join(str(goal or "").strip().lower().split())
        if not text:
            return False
        return any(
            phrase in text
            for phrase in (
                "another method",
                "another way",
                "different method",
                "different way",
                "focus first",
                "recover first",
                "reacquire first",
                "try a different desktop path",
                "use the desktop ui",
                "use a ui path",
                "instead",
                "fallback",
            )
        )

    def _command_looks_like_open_intent(self, command: str) -> bool:
        text = " ".join(str(command or "").strip().lower().split())
        if not text:
            return False
        return any(token in text for token in (".exe", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".pdf", ".txt", ".md", "start ", "explorer ", "notepad ", "code "))

    def _desktop_validator_family(self, tool_name: str, args: Dict[str, Any]) -> str:
        if tool_name == "desktop_focus_window":
            return "focus_switch"
        if tool_name in {
            "desktop_click_mouse",
            "desktop_click_point",
            "desktop_scroll",
        }:
            return "click_navigation"
        if tool_name in {"desktop_type_text", "desktop_press_key", "desktop_press_key_sequence"}:
            return "text_input"
        if tool_name in {"desktop_start_process", "desktop_open_target"}:
            return "open_launch"
        if tool_name == "desktop_run_command" and self._command_looks_like_open_intent(str(args.get("command", ""))):
            return "open_launch"
        return ""

    def _desktop_default_strategy_family(self, tool_name: str, args: Dict[str, Any]) -> str:
        if tool_name == "desktop_focus_window":
            return "focus_recovery_window"
        if tool_name in {
            "desktop_click_mouse",
            "desktop_click_point",
            "desktop_scroll",
        }:
            return "direct_interaction"
        if tool_name in {"desktop_type_text", "desktop_press_key", "desktop_press_key_sequence"}:
            return "direct_input"
        if tool_name == "desktop_start_process":
            return "direct_launch"
        if tool_name == "desktop_run_command":
            return "command_open" if self._command_looks_like_open_intent(str(args.get("command", ""))) else "bounded_command"
        return ""

    def _desktop_alternate_strategy_family(self, tool_name: str, current: str) -> str:
        if tool_name in {
            "desktop_click_mouse",
            "desktop_click_point",
            "desktop_scroll",
        }:
            return "focus_recovery_interaction" if current != "focus_recovery_interaction" else "direct_interaction"
        if tool_name in {"desktop_type_text", "desktop_press_key", "desktop_press_key_sequence"}:
            return "focus_recovery_input" if current != "focus_recovery_input" else "direct_input"
        return ""

    def _desktop_target_signature(self, tool_name: str, task_state, args: Dict[str, Any]) -> str:
        explicit = str(args.get("target_signature", "")).strip().lower()
        if explicit:
            return explicit[:220]
        parts = [tool_name]
        for key in (
            "title",
            "window_id",
            "expected_window_title",
            "expected_window_id",
            "field_label",
            "process_name",
            "owned_label",
            "executable",
            "target",
        ):
            value = " ".join(str(args.get(key, "")).strip().lower().split())
            if value:
                parts.append(value)
        if tool_name == "desktop_run_command":
            command = " ".join(str(args.get("command", "")).strip().lower().split())
            if command:
                parts.append(command[:120])
        if tool_name in {"desktop_click_mouse", "desktop_click_point", "desktop_move_mouse", "desktop_hover_point", "desktop_scroll"}:
            coord = "|".join(
                str(args.get(key, "")).strip().lower()
                for key in ("coordinate_mode", "x", "y", "relative_x", "relative_y", "capture_x", "capture_y", "direction")
                if str(args.get(key, "")).strip()
            )
            if coord:
                parts.append(coord[:120])
        checkpoint_target = str(getattr(task_state, "desktop_checkpoint_target", "")).strip().lower()
        if checkpoint_target:
            parts.append(checkpoint_target[:120])
        active_title = str(getattr(task_state, "desktop_active_window_title", "")).strip().lower()
        if active_title and len(parts) == 1:
            parts.append(active_title[:120])
        return "|".join(part for part in parts if part)[:220]

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
        checkpoint_target = str(getattr(task_state, "desktop_checkpoint_target", "")).strip()
        remembered_target_title = checkpoint_target or str(getattr(task_state, "desktop_last_target_window", "")).strip()
        target_hint = self._latest_desktop_target_hint(task_state, tool_name)
        hinted_title = str(target_hint.get("window_title", "")).strip()
        hinted_process = str(target_hint.get("window_process", "")).strip()
        hinted_summary = str(target_hint.get("summary", "")).strip()

        if getattr(task_state, "desktop_observation_token", ""):
            args.setdefault("observation_token", task_state.desktop_observation_token)
        if targeted_window_tool and not has_explicit_window_target and remembered_target_title:
            args.setdefault("title", remembered_target_title)
            args.setdefault("expected_window_title", remembered_target_title)
            args.setdefault("exact", True)
        elif targeted_window_tool and not has_explicit_window_target and hinted_title:
            args.setdefault("title", hinted_title)
            args.setdefault("expected_window_title", hinted_title)
            args.setdefault("exact", True)
        elif hinted_title and not str(args.get("expected_window_title", "")).strip():
            args.setdefault("expected_window_title", hinted_title)
        elif getattr(task_state, "desktop_active_window_title", "") and not (targeted_window_tool and has_explicit_window_target):
            args.setdefault("expected_window_title", task_state.desktop_active_window_title)
        if getattr(task_state, "desktop_active_window_id", "") and not (
            targeted_window_tool and (has_explicit_window_target or remembered_target_title)
        ):
            args.setdefault("expected_window_id", task_state.desktop_active_window_id)
        if hinted_process:
            expected_process_names = [
                str(item).strip()
                for item in list(args.get("expected_process_names", []))
                if str(item).strip()
            ]
            if hinted_process and hinted_process not in expected_process_names:
                expected_process_names.append(hinted_process)
            if expected_process_names:
                args["expected_process_names"] = expected_process_names[:3]
        if hinted_summary and not str(args.get("target_description", "")).strip():
            args["target_description"] = hinted_summary

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
        elif tool_name in {"desktop_move_mouse", "desktop_hover_point", "desktop_click_mouse", "desktop_scroll"}:
            args.setdefault("max_observation_age_seconds", 45)
            if tool_name == "desktop_hover_point":
                args.setdefault("hover_ms", 600)
            if tool_name == "desktop_scroll":
                args.setdefault("direction", "down")
                args.setdefault("scroll_units", 3)
        elif tool_name == "desktop_click_point":
            args.setdefault("max_observation_age_seconds", 45)
        elif tool_name == "desktop_press_key":
            args.setdefault("repeat", 1)
            args.setdefault("max_observation_age_seconds", 45)
        elif tool_name == "desktop_press_key_sequence":
            args.setdefault("max_observation_age_seconds", 45)
        elif tool_name == "desktop_type_text":
            args.setdefault("max_text_length", 160)
            args.setdefault("max_observation_age_seconds", 45)
        elif tool_name == "desktop_list_processes":
            args.setdefault("limit", 8)
            args.setdefault("include_background", True)
        elif tool_name == "desktop_inspect_process":
            args.setdefault("child_limit", 4)
            if not any(str(args.get(key, "")).strip() for key in ("pid", "process_name", "owned_label")):
                active_process_name = str(getattr(task_state, "desktop_active_window_process", "")).strip()
                if active_process_name:
                    args.setdefault("process_name", active_process_name)
        elif tool_name == "desktop_start_process":
            args.setdefault("owned_label", "")
        elif tool_name == "desktop_stop_process":
            args.setdefault("wait_seconds", 2)
        elif tool_name == "desktop_run_command":
            args.setdefault("timeout_seconds", 8)
            args.setdefault("shell_kind", "powershell")
        elif tool_name == "desktop_open_target":
            args.setdefault("cwd", "")
            args.setdefault("verification_samples", 3)
            args.setdefault("verification_interval_ms", 180)
            request_preferences = infer_open_request_preferences(goal_text, args)
            if request_preferences.get("target_type") and not str(args.get("target_type", "")).strip():
                args["target_type"] = request_preferences.get("target_type", "")
            if request_preferences.get("preferred_method") and not str(args.get("preferred_method", "")).strip():
                args["preferred_method"] = request_preferences.get("preferred_method", "")
            if request_preferences.get("force_strategy_switch", False) and "force_strategy_switch" not in args:
                args["force_strategy_switch"] = True

            target_text = str(args.get("target", "")).strip()
            if target_text:
                target_info = classify_open_target(
                    target_text,
                    cwd=str(args.get("cwd", "")).strip(),
                    explicit_target_type=str(args.get("target_type", "")).strip(),
                )
                target_signature = open_target_signature(target_info)
                if target_signature:
                    args.setdefault("target_signature", target_signature)
                if target_info.get("target_classification") and not str(args.get("target_type", "")).strip():
                    args["target_type"] = str(target_info.get("target_classification", "")).strip()

                avoid_families = [
                    str(item).strip()
                    for item in list(args.get("avoid_strategy_families", []))
                    if str(item).strip()
                ]
                store = getattr(task_state, "_operator_memory_store", None)
                if store is not None and hasattr(store, "lookup_patterns"):
                    try:
                        hints = store.lookup_patterns(
                            domain="desktop",
                            tool_name="desktop_open_target",
                            target_signature=target_signature,
                            goal=goal_text,
                        )
                    except Exception:
                        hints = {}
                    prefer = hints.get("prefer", []) if isinstance(hints.get("prefer", []), list) else []
                    avoid = hints.get("avoid", []) if isinstance(hints.get("avoid", []), list) else []
                    lessons = hints.get("lessons", []) if isinstance(hints.get("lessons", []), list) else []
                    if not str(args.get("preferred_method", "")).strip():
                        for item in prefer:
                            strategy_family = str((item or {}).get("strategy_family", "")).strip()
                            if strategy_family:
                                args["preferred_method"] = strategy_family
                                break
                    for item in avoid:
                        strategy_family = str((item or {}).get("strategy_family", "")).strip()
                        if strategy_family and strategy_family not in avoid_families:
                            avoid_families.append(strategy_family)
                    for lesson in lessons:
                        category = str((lesson or {}).get("category", "")).strip().lower()
                        lesson_strategy = str((lesson or {}).get("strategy_family", "")).strip()
                        if category == "launcher_file_open_semantics" and "executable_launch" not in avoid_families:
                            avoid_families.append("executable_launch")
                        if lesson_strategy and lesson_strategy not in avoid_families:
                            avoid_families.append(lesson_strategy)
                if avoid_families:
                    args["avoid_strategy_families"] = avoid_families[:4]

        if tool_name != "desktop_open_target":
            validator_family = self._desktop_validator_family(tool_name, args)
            default_strategy_family = self._desktop_default_strategy_family(tool_name, args)
            if validator_family:
                args.setdefault("validator_family", validator_family)
                args.setdefault(
                    "verification_samples",
                    3 if validator_family != "open_launch" else 3,
                )
                args.setdefault(
                    "verification_interval_ms",
                    140 if validator_family in {"focus_switch", "click_navigation", "text_input"} else 180,
                )

            target_signature = self._desktop_target_signature(tool_name, task_state, args)
            if target_signature:
                args.setdefault("target_signature", target_signature)

            store = getattr(task_state, "_operator_memory_store", None)
            hints: Dict[str, Any] = {}
            if store is not None and hasattr(store, "lookup_patterns") and target_signature:
                try:
                    hints = store.lookup_patterns(
                        domain="desktop",
                        tool_name=tool_name,
                        target_signature=target_signature,
                        goal=goal_text,
                    )
                except Exception:
                    hints = {}

            avoid_families = [
                str(item).strip()
                for item in list(args.get("avoid_strategy_families", []))
                if str(item).strip()
            ]
            prefer = hints.get("prefer", []) if isinstance(hints.get("prefer", []), list) else []
            avoid = hints.get("avoid", []) if isinstance(hints.get("avoid", []), list) else []
            lessons = hints.get("lessons", []) if isinstance(hints.get("lessons", []), list) else []

            for item in avoid:
                strategy_family = str((item or {}).get("strategy_family", "")).strip()
                if strategy_family and strategy_family not in avoid_families:
                    avoid_families.append(strategy_family)
            for lesson in lessons:
                category = str((lesson or {}).get("category", "")).strip().lower()
                lesson_strategy = str((lesson or {}).get("strategy_family", "")).strip()
                if lesson_strategy and lesson_strategy not in avoid_families:
                    avoid_families.append(lesson_strategy)
                if category in {"focus_recovery_issue", "no_visible_progress_after_action", "strategy_reuse_after_failure"}:
                    if default_strategy_family and default_strategy_family not in avoid_families:
                        avoid_families.append(default_strategy_family)

            preferred_strategy_family = ""
            for item in prefer:
                strategy_family = str((item or {}).get("strategy_family", "")).strip()
                if strategy_family and strategy_family not in avoid_families:
                    preferred_strategy_family = strategy_family
                    break

            force_strategy_switch = bool(args.get("force_strategy_switch", False) or self._goal_requests_desktop_strategy_switch(goal_text))
            chosen_strategy_family = str(args.get("strategy_family", "")).strip() or default_strategy_family
            alternate_strategy_family = self._desktop_alternate_strategy_family(tool_name, chosen_strategy_family)
            if force_strategy_switch and alternate_strategy_family:
                chosen_strategy_family = alternate_strategy_family
            elif chosen_strategy_family in avoid_families and alternate_strategy_family:
                chosen_strategy_family = alternate_strategy_family
            elif not str(args.get("strategy_family", "")).strip() and preferred_strategy_family:
                chosen_strategy_family = preferred_strategy_family

            if chosen_strategy_family:
                args["strategy_family"] = chosen_strategy_family
            if avoid_families:
                args["avoid_strategy_families"] = avoid_families[:4]
            if chosen_strategy_family in {"focus_recovery_interaction", "focus_recovery_input"}:
                args["pre_action_recovery"] = True
                if not any(str(args.get(key, "")).strip() for key in ("title", "match", "window_id")):
                    if str(args.get("expected_window_id", "")).strip():
                        args.setdefault("window_id", str(args.get("expected_window_id", "")).strip())
                    elif str(args.get("expected_window_title", "")).strip():
                        args.setdefault("title", str(args.get("expected_window_title", "")).strip())
                        args.setdefault("exact", True)

        if tool_name in DESKTOP_APPROVAL_CONTROLLED_TOOLS:
            approval_status = str(args.get("approval_status", "")).strip().lower()
            if approval_status == "approved" and not goal_has_explicit_approval:
                args.pop("approval_status", None)

        checkpoint_pending = bool(getattr(task_state, "desktop_checkpoint_pending", False))
        checkpoint_tool = getattr(task_state, "desktop_checkpoint_tool", "")
        checkpoint_reason = getattr(task_state, "desktop_checkpoint_reason", "")
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

