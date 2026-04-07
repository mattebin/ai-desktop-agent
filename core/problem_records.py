from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List


PROBLEM_STORE_VERSION = 1
DEFAULT_PROBLEM_RECORD_PATH = Path(__file__).resolve().parents[1] / "data" / "problem_records.json"
PROBLEM_OUTCOMES = {"failure", "blocked", "uncertain", "no_progress", "partial_success"}
_MAX_RECENT_OCCURRENCES = 5
_MAX_RECENT_IDS = 6


def _trim_text(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _stable_hash(payload: Any) -> str:
    try:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
    except Exception:
        encoded = repr(payload).encode("utf-8", errors="replace")
    return hashlib.sha1(encoded).hexdigest()[:16]


def problem_like_outcome(status: Any) -> bool:
    return _trim_text(status, limit=40) in PROBLEM_OUTCOMES


def extract_error_text(result: Dict[str, Any] | None, evaluation: Dict[str, Any] | None = None) -> str:
    safe_result = result if isinstance(result, dict) else {}
    safe_evaluation = evaluation if isinstance(evaluation, dict) else {}
    candidates = [
        safe_result.get("error"),
        safe_result.get("message"),
        safe_result.get("summary"),
        safe_result.get("stderr"),
        safe_result.get("stdout"),
        safe_evaluation.get("summary"),
        safe_evaluation.get("observed_change"),
    ]
    command_result = safe_result.get("command_result", {}) if isinstance(safe_result.get("command_result", {}), dict) else {}
    candidates.extend(
        [
            command_result.get("stderr_excerpt"),
            command_result.get("stdout_excerpt"),
            command_result.get("stderr"),
            command_result.get("stdout"),
        ]
    )
    for candidate in candidates:
        text = _trim_text(candidate, limit=320)
        if text:
            return text
    return ""


def extract_error_code(result: Dict[str, Any] | None, evaluation: Dict[str, Any] | None = None) -> str:
    safe_result = result if isinstance(result, dict) else {}
    command_result = safe_result.get("command_result", {}) if isinstance(safe_result.get("command_result", {}), dict) else {}
    for key in ("error_code", "code", "errno"):
        text = _trim_text(safe_result.get(key, ""), limit=80)
        if text:
            return text
    for key in ("returncode", "exit_code", "code"):
        value = command_result.get(key)
        if value not in ("", None):
            return f"exit_{value}"
    error_text = extract_error_text(safe_result, evaluation)
    match = re.search(r"(WinError\s+\d+)", error_text, flags=re.IGNORECASE)
    if match:
        return _trim_text(match.group(1), limit=80)
    return ""


def classify_failure_category(
    tool_name: str,
    evaluation: Dict[str, Any] | None,
    result: Dict[str, Any] | None,
    *,
    alternate_strategy_attempted: bool = False,
    approval_involved: bool = False,
) -> str:
    safe_evaluation = evaluation if isinstance(evaluation, dict) else {}
    safe_result = result if isinstance(result, dict) else {}
    status = _trim_text(safe_evaluation.get("status", ""), limit=40)
    reason = _trim_text(safe_evaluation.get("reason", safe_result.get("reason", "")), limit=120).lower()
    summary = _trim_text(safe_evaluation.get("summary", safe_result.get("summary", "")), limit=220).lower()
    error_text = extract_error_text(safe_result, safe_evaluation).lower()
    tool = _trim_text(tool_name, limit=80).lower()

    if "winerror 193" in error_text or ("launcher" in reason and "open" in summary):
        return "launcher_file_open_semantics"
    if tool == "lab_run_shell" and (status == "blocked" or "blocked_category" in safe_result):
        return "shell_lab_policy_block"
    if approval_involved or status == "blocked" or "approval" in reason or "approval" in error_text or "policy" in reason:
        return "policy_approval_block"
    if safe_evaluation.get("domain") == "gmail" or tool.startswith("email_"):
        if "needs_context" in reason or bool(safe_result.get("needs_context", False)):
            return "missing_context_or_user_input"
        return "gmail_workflow_state_problem"
    if alternate_strategy_attempted and status in {"failure", "no_progress", "uncertain"}:
        return "strategy_reuse_after_failure"
    if any(token in reason or token in error_text or token in summary for token in ("focus", "recover", "restore", "foreground", "active window", "window ready")):
        return "focus_recovery_issue"
    if any(token in reason or token in error_text for token in ("target", "selector", "proposal", "window not found", "element not found")):
        return "wrong_target_or_bad_target_proposal"
    if any(token in reason or token in error_text for token in ("not configured", "not authenticated", "disabled", "unsupported", "not available", "missing dependency")):
        return "environment_mismatch"
    if status in {"uncertain", "no_progress"}:
        return "no_visible_progress_after_action"
    if status == "partial_success":
        return "missing_context_or_user_input"
    return "unknown"


def build_improvement_hint(problem: Dict[str, Any] | None) -> str:
    safe_problem = problem if isinstance(problem, dict) else {}
    category = _trim_text(safe_problem.get("failure_category", ""), limit=80)
    if category == "launcher_file_open_semantics":
        return "Open the file through its associated app or shell instead of treating it like an executable."
    if category == "wrong_target_or_bad_target_proposal":
        return "Refresh evidence and choose a stronger target proposal before retrying the action."
    if category == "focus_recovery_issue":
        return "Run a focus or recovery step first, then retry only after the correct window is active."
    if category == "environment_mismatch":
        return "Check feature availability and local environment constraints before repeating the same step."
    if category == "policy_approval_block":
        return "Surface the approval requirement clearly or choose a safer bounded alternative."
    if category == "missing_context_or_user_input":
        return "Ask for the missing detail explicitly instead of guessing and repeating the workflow."
    if category == "gmail_workflow_state_problem":
        return "Refresh Gmail thread or draft state and confirm the workflow stage before sending or retrying."
    if category == "shell_lab_policy_block":
        return "Keep the task in bounded mode or replace it with a safer lab inspection command."
    if category == "no_visible_progress_after_action":
        return "Verify a visible state change happened before marking the action successful or trying it again."
    if category == "strategy_reuse_after_failure":
        return "Do not reuse the same failed path again; switch method or stop and ask for guidance."
    return "Review the exact error and surrounding evidence before retrying the same method."


def build_failure_lesson(problem: Dict[str, Any] | None) -> Dict[str, Any]:
    safe_problem = problem if isinstance(problem, dict) else {}
    category = _trim_text(safe_problem.get("failure_category", ""), limit=80)
    tool = _trim_text(safe_problem.get("tool", ""), limit=80)
    error_code = _trim_text(safe_problem.get("error_code", ""), limit=80)
    if category == "launcher_file_open_semantics":
        lesson_text = "Do not launch non-executable files directly; open them via the associated app or shell."
    elif category == "focus_recovery_issue":
        lesson_text = "Prefer focus or recovery steps before repeating a desktop action against the same window."
    elif category == "strategy_reuse_after_failure" or bool(safe_problem.get("retry_budget_exhausted", False)):
        lesson_text = "Do not repeat the same failed action signature after the retry budget is exhausted."
    elif category == "no_visible_progress_after_action":
        lesson_text = "Treat missing visible state change as no progress, not success."
    else:
        lesson_text = ""
    if not lesson_text:
        return {}
    return {
        "lesson_key": _stable_hash({"category": category, "tool": tool, "error_code": error_code, "lesson": lesson_text}),
        "lesson": _trim_text(lesson_text, limit=220),
        "category": category,
        "tool": tool,
    }


def build_problem_record(
    *,
    task_state,
    tool_name: str,
    args: Dict[str, Any] | None,
    result: Dict[str, Any] | None,
    evaluation: Dict[str, Any] | None,
    alternate_strategy_attempted: bool = False,
) -> Dict[str, Any]:
    safe_result = result if isinstance(result, dict) else {}
    safe_evaluation = evaluation if isinstance(evaluation, dict) else {}
    outcome = _trim_text(safe_evaluation.get("status", ""), limit=40)
    if not problem_like_outcome(outcome):
        return {}

    policy = safe_result.get("policy", {}) if isinstance(safe_result.get("policy", {}), dict) else {}
    retry = safe_evaluation.get("retry", {}) if isinstance(safe_evaluation.get("retry", {}), dict) else {}
    before = safe_evaluation.get("before", {}) if isinstance(safe_evaluation.get("before", {}), dict) else {}
    after = safe_evaluation.get("after", {}) if isinstance(safe_evaluation.get("after", {}), dict) else {}
    approval_involved = bool(
        safe_result.get("approval_required", False)
        or safe_result.get("paused", False)
        or _trim_text(policy.get("decision", ""), limit=40) in {"approval_required", "block"}
        or outcome == "blocked"
    )
    error_text = extract_error_text(safe_result, safe_evaluation)
    error_code = extract_error_code(safe_result, safe_evaluation)
    category = classify_failure_category(
        tool_name,
        safe_evaluation,
        safe_result,
        alternate_strategy_attempted=alternate_strategy_attempted,
        approval_involved=approval_involved,
    )

    evidence_refs = [
        _trim_text(before.get("desktop_evidence_id", ""), limit=80),
        _trim_text(after.get("desktop_evidence_id", ""), limit=80),
        _trim_text(safe_result.get("evidence_id", ""), limit=80),
        _trim_text(safe_result.get("thread_id", ""), limit=80),
        _trim_text(safe_result.get("draft_id", ""), limit=80),
        _trim_text((safe_result.get("environment", {}) if isinstance(safe_result.get("environment", {}), dict) else {}).get("workspace_id", ""), limit=80),
    ]
    evidence_refs = [value for value in evidence_refs if value]

    summary = _trim_text(
        safe_evaluation.get("summary", "")
        or safe_result.get("summary", "")
        or safe_result.get("message", "")
        or error_text,
        limit=240,
    )
    problem = {
        "problem_id": f"prob-{_stable_hash({'tool': tool_name, 'time': _iso_now(), 'summary': summary})}",
        "problem_key": _stable_hash(
            {
                "tool": _trim_text(tool_name, limit=80),
                "category": category,
                "error_code": error_code,
                "action_signature": _trim_text(safe_evaluation.get("action_signature", ""), limit=220),
                "target_signature": _trim_text(safe_evaluation.get("target_signature", ""), limit=220),
                "reason": _trim_text(safe_evaluation.get("reason", ""), limit=120),
            }
        ),
        "run_id": "",
        "session_id": _trim_text(getattr(task_state, "session_id", ""), limit=80),
        "state_scope_id": _trim_text(getattr(task_state, "state_scope_id", ""), limit=120),
        "task_id": _trim_text(getattr(task_state, "task_id", ""), limit=80),
        "timestamp": _trim_text(safe_evaluation.get("evaluated_at", ""), limit=40) or _iso_now(),
        "tool": _trim_text(tool_name, limit=80),
        "domain": _trim_text(safe_evaluation.get("domain", ""), limit=40),
        "user_intent": _trim_text(getattr(task_state, "goal", ""), limit=220),
        "operator_step": _trim_text(
            getattr(task_state, "task_control_reason", "")
            or safe_result.get("step", "")
            or safe_evaluation.get("tool", "")
            or tool_name,
            limit=180,
        ),
        "error_code": error_code,
        "error_text": error_text,
        "outcome_classification": outcome,
        "retry_count": max(0, _safe_int(safe_evaluation.get("attempt_number", 1), default=1) - 1),
        "retry_budget_exhausted": bool(retry.get("exhausted", False)),
        "alternate_strategy_attempted": bool(alternate_strategy_attempted),
        "approval_involved": approval_involved,
        "evidence_refs": evidence_refs[:4],
        "failure_category": category,
        "summary": summary,
        "improvement_hint": "",
        "action_signature": _trim_text(safe_evaluation.get("action_signature", ""), limit=220),
        "target_signature": _trim_text(safe_evaluation.get("target_signature", ""), limit=220),
        "reason": _trim_text(safe_evaluation.get("reason", ""), limit=120),
        "expected_change": _trim_text(safe_evaluation.get("expected_change", ""), limit=160),
        "observed_change": _trim_text(safe_evaluation.get("observed_change", ""), limit=160),
        "exact_error": error_text,
        "policy_decision": _trim_text(policy.get("decision", ""), limit=40),
        "policy_summary": _trim_text(policy.get("summary", ""), limit=180),
        "stored_lesson": "",
    }
    problem["improvement_hint"] = build_improvement_hint(problem)
    lesson = build_failure_lesson(problem)
    if lesson:
        problem["stored_lesson"] = _trim_text(lesson.get("lesson", ""), limit=220)
        problem["lesson_key"] = _trim_text(lesson.get("lesson_key", ""), limit=80)
    return {key: value for key, value in problem.items() if value not in ("", None, [], False)}


def enrich_problem_record(
    problem: Dict[str, Any] | None,
    *,
    run_id: str = "",
    session_id: str = "",
    state_scope_id: str = "",
    task_id: str = "",
) -> Dict[str, Any]:
    safe_problem = dict(problem) if isinstance(problem, dict) else {}
    if not safe_problem:
        return {}
    if run_id:
        safe_problem["run_id"] = _trim_text(run_id, limit=60)
    if session_id:
        safe_problem["session_id"] = _trim_text(session_id, limit=80)
    if state_scope_id:
        safe_problem["state_scope_id"] = _trim_text(state_scope_id, limit=120)
    if task_id:
        safe_problem["task_id"] = _trim_text(task_id, limit=80)
    return safe_problem


class ProblemRecordStore:
    def __init__(self, path: str | Path, *, max_records: int = 120):
        self.path = Path(path)
        self.max_records = max(20, int(max_records))

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"version": PROBLEM_STORE_VERSION, "records": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": PROBLEM_STORE_VERSION, "records": []}
        if not isinstance(payload, dict):
            return {"version": PROBLEM_STORE_VERSION, "records": []}
        records = payload.get("records", [])
        return {
            "version": PROBLEM_STORE_VERSION,
            "updated_at": _trim_text(payload.get("updated_at", ""), limit=40),
            "records": records if isinstance(records, list) else [],
        }

    def _save(self, payload: Dict[str, Any]) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            return False
        return True

    def record_problem(self, problem: Dict[str, Any] | None) -> bool:
        safe_problem = dict(problem) if isinstance(problem, dict) else {}
        if not safe_problem or not safe_problem.get("problem_key"):
            return False
        payload = self._load()
        records = payload.get("records", [])
        if not isinstance(records, list):
            records = []
        key = _trim_text(safe_problem.get("problem_key", ""), limit=80)
        occurrence = {
            "timestamp": _trim_text(safe_problem.get("timestamp", ""), limit=40),
            "run_id": _trim_text(safe_problem.get("run_id", ""), limit=60),
            "session_id": _trim_text(safe_problem.get("session_id", ""), limit=80),
            "state_scope_id": _trim_text(safe_problem.get("state_scope_id", ""), limit=120),
            "task_id": _trim_text(safe_problem.get("task_id", ""), limit=80),
            "summary": _trim_text(safe_problem.get("summary", ""), limit=220),
            "outcome_classification": _trim_text(safe_problem.get("outcome_classification", ""), limit=40),
        }
        matched = False
        for entry in records:
            if not isinstance(entry, dict):
                continue
            if _trim_text(entry.get("problem_key", ""), limit=80) != key:
                continue
            matched = True
            entry["occurrence_count"] = _safe_int(entry.get("occurrence_count", 1), default=1) + 1
            entry["last_seen_at"] = _trim_text(safe_problem.get("timestamp", ""), limit=40) or _iso_now()
            entry["latest"] = safe_problem
            occurrences = entry.get("recent_occurrences", [])
            if not isinstance(occurrences, list):
                occurrences = []
            if occurrence not in occurrences:
                occurrences.insert(0, occurrence)
            entry["recent_occurrences"] = occurrences[:_MAX_RECENT_OCCURRENCES]
            for field, limit in (("run_ids", 60), ("session_ids", 80), ("state_scope_ids", 120)):
                values = entry.get(field, [])
                if not isinstance(values, list):
                    values = []
                if field == "run_ids":
                    source_value = _trim_text(safe_problem.get("run_id", ""), limit=60)
                elif field == "session_ids":
                    source_value = _trim_text(safe_problem.get("session_id", ""), limit=80)
                else:
                    source_value = _trim_text(safe_problem.get("state_scope_id", ""), limit=120)
                if source_value and source_value not in values:
                    values.insert(0, source_value)
                entry[field] = values[:_MAX_RECENT_IDS]
            break
        if not matched:
            records.insert(
                0,
                {
                    "problem_key": key,
                    "occurrence_count": 1,
                    "first_seen_at": _trim_text(safe_problem.get("timestamp", ""), limit=40) or _iso_now(),
                    "last_seen_at": _trim_text(safe_problem.get("timestamp", ""), limit=40) or _iso_now(),
                    "latest": safe_problem,
                    "recent_occurrences": [occurrence],
                    "run_ids": [_trim_text(safe_problem.get("run_id", ""), limit=60)] if safe_problem.get("run_id") else [],
                    "session_ids": [_trim_text(safe_problem.get("session_id", ""), limit=80)] if safe_problem.get("session_id") else [],
                    "state_scope_ids": [_trim_text(safe_problem.get("state_scope_id", ""), limit=120)] if safe_problem.get("state_scope_id") else [],
                },
            )
        records.sort(key=lambda item: _trim_text(item.get("last_seen_at", ""), limit=40), reverse=True)
        payload["records"] = records[: self.max_records]
        payload["updated_at"] = _iso_now()
        return self._save(payload)

    def record_problems(self, problems: List[Dict[str, Any]] | None) -> bool:
        items = problems if isinstance(problems, list) else []
        updated = False
        for problem in items:
            updated = self.record_problem(problem) or updated
        return updated

    def get_recent(self, limit: int = 12) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(int(limit or 12), self.max_records))
        payload = self._load()
        items: List[Dict[str, Any]] = []
        for entry in payload.get("records", []):
            if not isinstance(entry, dict):
                continue
            latest = entry.get("latest", {}) if isinstance(entry.get("latest", {}), dict) else {}
            items.append(
                {
                    **latest,
                    "problem_key": _trim_text(entry.get("problem_key", ""), limit=80),
                    "occurrence_count": _safe_int(entry.get("occurrence_count", 1), default=1),
                    "first_seen_at": _trim_text(entry.get("first_seen_at", ""), limit=40),
                    "last_seen_at": _trim_text(entry.get("last_seen_at", ""), limit=40),
                    "recent_occurrences": entry.get("recent_occurrences", []) if isinstance(entry.get("recent_occurrences", []), list) else [],
                }
            )
            if len(items) >= safe_limit:
                break
        return items

    def get_summary(self, limit: int = 6) -> Dict[str, Any]:
        payload = self._load()
        records = payload.get("records", [])
        categories: Dict[str, int] = {}
        tools: Dict[str, int] = {}
        for entry in records:
            if not isinstance(entry, dict):
                continue
            latest = entry.get("latest", {}) if isinstance(entry.get("latest", {}), dict) else {}
            count = _safe_int(entry.get("occurrence_count", 1), default=1)
            category = _trim_text(latest.get("failure_category", ""), limit=80) or "unknown"
            tool = _trim_text(latest.get("tool", ""), limit=80) or "unknown"
            categories[category] = categories.get(category, 0) + count
            tools[tool] = tools.get(tool, 0) + count
        top_categories = [
            {"category": name, "count": count}
            for name, count in sorted(categories.items(), key=lambda item: (-item[1], item[0]))[: max(1, int(limit or 6))]
        ]
        top_tools = [
            {"tool": name, "count": count}
            for name, count in sorted(tools.items(), key=lambda item: (-item[1], item[0]))[: max(1, int(limit or 6))]
        ]
        return {
            "total_records": len([entry for entry in records if isinstance(entry, dict)]),
            "total_occurrences": sum(_safe_int((entry if isinstance(entry, dict) else {}).get("occurrence_count", 1), default=1) for entry in records if isinstance(entry, dict)),
            "top_categories": top_categories,
            "top_tools": top_tools,
            "updated_at": _trim_text(payload.get("updated_at", ""), limit=40),
        }
