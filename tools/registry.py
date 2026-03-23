from __future__ import annotations

from tools.browser import (
    BROWSER_CLICK_TOOL,
    BROWSER_EXTRACT_TEXT_TOOL,
    BROWSER_FOLLOW_LINK_TOOL,
    BROWSER_INSPECT_PAGE_TOOL,
    BROWSER_OPEN_PAGE_TOOL,
    BROWSER_TYPE_TOOL,
)
from tools.desktop import (
    DESKTOP_CAPTURE_SCREENSHOT_TOOL,
    DESKTOP_CLICK_POINT_TOOL,
    DESKTOP_FOCUS_WINDOW_TOOL,
    DESKTOP_GET_ACTIVE_WINDOW_TOOL,
    DESKTOP_LIST_WINDOWS_TOOL,
    DESKTOP_TYPE_TEXT_TOOL,
)
from tools.files import APPLY_APPROVED_EDITS_TOOL, COMPARE_FILES_TOOL, INSPECT_PROJECT_TOOL, LIST_FILES_TOOL, READ_FILE_TOOL, SEARCH_FILES_TOOL
from tools.shell import DRAFT_PROPOSED_EDITS_TOOL, PLAN_PATCH_TOOL, REVIEW_BUNDLE_TOOL, RUN_SHELL_TOOL, SUGGEST_COMMANDS_TOOL


def get_browser_tools():
    return [
        BROWSER_OPEN_PAGE_TOOL,
        BROWSER_INSPECT_PAGE_TOOL,
        BROWSER_CLICK_TOOL,
        BROWSER_TYPE_TOOL,
        BROWSER_EXTRACT_TEXT_TOOL,
        BROWSER_FOLLOW_LINK_TOOL,
    ]


def get_desktop_tools():
    return [
        DESKTOP_LIST_WINDOWS_TOOL,
        DESKTOP_GET_ACTIVE_WINDOW_TOOL,
        DESKTOP_FOCUS_WINDOW_TOOL,
        DESKTOP_CAPTURE_SCREENSHOT_TOOL,
        DESKTOP_CLICK_POINT_TOOL,
        DESKTOP_TYPE_TEXT_TOOL,
    ]


def get_project_inspection_tools():
    return [
        INSPECT_PROJECT_TOOL,
        COMPARE_FILES_TOOL,
        READ_FILE_TOOL,
        LIST_FILES_TOOL,
        SEARCH_FILES_TOOL,
    ]


def get_planning_tools():
    return [
        APPLY_APPROVED_EDITS_TOOL,
        SUGGEST_COMMANDS_TOOL,
        PLAN_PATCH_TOOL,
        DRAFT_PROPOSED_EDITS_TOOL,
        REVIEW_BUNDLE_TOOL,
        RUN_SHELL_TOOL,
    ]


def get_tools():
    inspection_tools = get_project_inspection_tools()
    return [
        inspection_tools[0],
        *get_browser_tools(),
        *get_desktop_tools(),
        *inspection_tools[1:],
        *get_planning_tools(),
    ]
