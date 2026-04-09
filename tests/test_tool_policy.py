"""Tests for core.tool_policy — risk classification and policy snapshots."""
from __future__ import annotations

from core.tool_policy import (
    CONDITIONAL_APPROVAL_TOOLS,
    EXPLICIT_APPROVAL_TOOLS,
    READ_ONLY_TOOLS,
    _shell_hazard,
    build_tool_policy_snapshot,
    classify_tool_risk,
)


class TestClassifyToolRisk:
    def test_read_only_tools_are_auto(self):
        for tool in READ_ONLY_TOOLS:
            result = classify_tool_risk(tool)
            assert result["approval_mode"] == "auto", f"{tool} should be auto"
            assert result["risk_level"] == "low"
            assert result["mutation_target"] == "none"

    def test_explicit_tools_are_high_risk(self):
        for tool in EXPLICIT_APPROVAL_TOOLS:
            result = classify_tool_risk(tool)
            assert result["approval_mode"] == "explicit", f"{tool} should be explicit"
            assert result["risk_level"] == "high"

    def test_conditional_tools_are_medium(self):
        for tool in CONDITIONAL_APPROVAL_TOOLS:
            result = classify_tool_risk(tool)
            assert result["approval_mode"] == "conditional", f"{tool} should be conditional"
            assert result["risk_level"] == "medium"

    def test_unknown_tool_is_conservative(self):
        result = classify_tool_risk("totally_unknown_tool")
        assert result["approval_mode"] == "conditional"
        assert result["risk_level"] == "medium"

    def test_empty_tool_name(self):
        result = classify_tool_risk("")
        assert result["tool"] == ""
        assert result["approval_mode"] == "conditional"

    def test_area_detection(self):
        assert classify_tool_risk("browser_open_page")["area"] == "browser"
        assert classify_tool_risk("desktop_click_mouse")["area"] == "desktop"
        assert classify_tool_risk("email_send_draft")["area"] == "email"
        assert classify_tool_risk("run_shell")["area"] == "shell"

    def test_shell_hazard_detected_for_desktop_run_command(self):
        result = classify_tool_risk("desktop_run_command", {"command": "rm -rf /"})
        assert result["shell_hazard"] != ""

    def test_no_shell_hazard_for_safe_command(self):
        result = classify_tool_risk("desktop_run_command", {"command": "echo hello"})
        assert result["shell_hazard"] == ""


class TestShellHazard:
    def test_rm_detected(self):
        assert _shell_hazard("rm file.txt") != ""

    def test_del_detected(self):
        assert _shell_hazard("del /f something") != ""

    def test_git_reset_hard_detected(self):
        assert _shell_hazard("git reset --hard HEAD~1") != ""

    def test_safe_command_passes(self):
        assert _shell_hazard("ls -la") == ""
        assert _shell_hazard("echo hello") == ""
        assert _shell_hazard("git status") == ""

    def test_empty_input(self):
        assert _shell_hazard("") == ""
        assert _shell_hazard(None) == ""


class TestBuildToolPolicySnapshot:
    def test_snapshot_categorizes_correctly(self):
        tools = ["read_file", "browser_open_page", "desktop_click_mouse"]
        snapshot = build_tool_policy_snapshot(tools)
        assert "read_file" in snapshot["read_only_tools"]
        assert "browser_open_page" in snapshot["conditional_approval_tools"]
        assert "desktop_click_mouse" in snapshot["explicit_approval_tools"]

    def test_empty_input(self):
        snapshot = build_tool_policy_snapshot([])
        assert snapshot["read_only_tools"] == []
        assert snapshot["conditional_approval_tools"] == []
        assert snapshot["explicit_approval_tools"] == []
