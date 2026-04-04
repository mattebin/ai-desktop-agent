from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from core.local_api import LocalOperatorApiServer


ROOT = Path(__file__).resolve().parent
EXTENSIONS_DIR = ROOT / ".agents" / "extensions"
SETTINGS_LOCAL_PATH = ROOT / "config" / "settings.local.yaml"
REPORT_PATH = ROOT / "data" / "evals" / "hybrid_eval_report.json"


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
        }


def _request_json(base_url: str, path: str, *, method: str = "GET", payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        base_url + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _unwrap_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data", {})
    items = data.get("items", [])
    return items if isinstance(items, list) else []


def _unwrap_execution(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = payload.get("data", {})
    execution = data.get("execution", {})
    return execution if isinstance(execution, dict) else {}


def _append_check(results: List[CheckResult], name: str, passed: bool, detail: str):
    results.append(CheckResult(name=name, passed=passed, detail=detail))


def _stream_next_event(stream, *, timeout_seconds: float = 8.0) -> Dict[str, Any]:
    deadline = time.time() + timeout_seconds
    event_name = ""
    event_id = ""
    data_lines: List[str] = []
    while time.time() < deadline:
        raw = stream.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if event_name:
                payload = json.loads("\n".join(data_lines)) if data_lines else {}
                if isinstance(payload, dict):
                    return {
                        "event": event_name,
                        "event_id": event_id,
                        **payload,
                    }
                return {"event": event_name, "event_id": event_id, "data": payload}
            event_name = ""
            event_id = ""
            data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("id:"):
            event_id = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].strip())
    return {}


def _write_report(results: List[CheckResult], *, extra: Dict[str, Any] | None = None):
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "passed": all(item.passed for item in results),
        "check_count": len(results),
        "passed_count": sum(1 for item in results if item.passed),
        "failed_count": sum(1 for item in results if not item.passed),
        "checks": [item.to_dict() for item in results],
        "extra": extra or {},
    }
    REPORT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    results: List[CheckResult] = []
    extension_path = EXTENSIONS_DIR / "hybrid_eval_extension.yaml"
    extension_backup = extension_path.read_text(encoding="utf-8") if extension_path.exists() else None
    settings_backup = SETTINGS_LOCAL_PATH.read_text(encoding="utf-8") if SETTINGS_LOCAL_PATH.exists() else None
    server: LocalOperatorApiServer | None = None
    thread: threading.Thread | None = None
    stream = None
    seen_events: List[str] = []

    EXTENSIONS_DIR.mkdir(parents=True, exist_ok=True)
    extension_path.write_text(
        "\n".join(
            [
                "title: Hybrid Eval Helpers",
                "description: Temporary extension manifest used during hybrid evaluation.",
                "commands:",
                "  - type: prompt",
                "    name: hybrid-eval-check",
                "    description: Temporary prompt command for eval verification.",
                "    prompt: Inspect the current workspace and confirm the hybrid eval extension is loaded.",
                "  - type: local",
                "    name: hybrid-eval-runtime",
                "    description: Temporary runtime shortcut for eval verification.",
                "    action: show-runtime",
                "",
            ]
        ),
        encoding="utf-8",
    )

    try:
        server = LocalOperatorApiServer(
            host="127.0.0.1",
            port=0,
            settings={
                "local_api_event_poll_seconds": 0.25,
                "local_api_event_heartbeat_seconds": 2.0,
            },
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.port}"

        commands_payload = _request_json(base_url, "/commands")
        skills_payload = _request_json(base_url, "/skills")
        tools_payload = _request_json(base_url, "/tools")
        extensions_payload = _request_json(base_url, "/extensions")
        status_payload = _request_json(base_url, "/status")

        commands = _unwrap_items(commands_payload)
        skills = _unwrap_items(skills_payload)
        tools = _unwrap_items(tools_payload)
        extensions = _unwrap_items(extensions_payload)
        status = status_payload.get("data", {})

        command_names = {str(item.get("name", "")).strip() for item in commands}
        skill_commands = {str(item.get("commandName", "")).strip() for item in skills}

        _append_check(
            results,
            "command_catalog_has_hybrid_entries",
            {"runtime", "tools", "extensions"}.issubset(command_names),
            f"Command names include: {sorted(name for name in command_names if name)[:16]}",
        )
        _append_check(
            results,
            "skill_catalog_loaded",
            bool(skill_commands),
            f"Skill-backed slash commands: {sorted(name for name in skill_commands if name)[:8]}",
        )
        _append_check(
            results,
            "tool_catalog_loaded",
            len(tools) >= 10,
            f"Tool count: {len(tools)}",
        )
        _append_check(
            results,
            "extension_catalog_loaded",
            any(str(item.get("title", "")).strip() == "Hybrid Eval Helpers" for item in extensions),
            f"Extensions: {[item.get('title', '') for item in extensions]}",
        )
        _append_check(
            results,
            "runtime_status_exposed",
            isinstance(status.get("runtime", {}), dict) and bool(status.get("runtime", {}).get("active_model", "")),
            f"Runtime: {status.get('runtime', {})}",
        )
        _append_check(
            results,
            "infrastructure_status_exposed",
            isinstance(status.get("infrastructure", {}), dict) and bool(status.get("infrastructure", {})),
            f"Infrastructure keys: {sorted((status.get('infrastructure', {}) or {}).keys())}",
        )

        runtime_execution = _unwrap_execution(_request_json(base_url, "/commands/execute", method="POST", payload={"input": "/runtime"}))
        tools_execution = _unwrap_execution(_request_json(base_url, "/commands/execute", method="POST", payload={"input": "/tools"}))
        extensions_execution = _unwrap_execution(_request_json(base_url, "/commands/execute", method="POST", payload={"input": "/extensions"}))
        architecture_execution = _unwrap_execution(
            _request_json(base_url, "/commands/execute", method="POST", payload={"input": "/architecture hybrid runtime"})
        )
        extension_command_execution = _unwrap_execution(
            _request_json(base_url, "/commands/execute", method="POST", payload={"input": "/hybrid-eval-check live context"})
        )
        approval_execution = _unwrap_execution(_request_json(base_url, "/commands/execute", method="POST", payload={"input": "/approve"}))

        _append_check(
            results,
            "runtime_command_executes",
            runtime_execution.get("kind") == "activity" and "Model:" in str(runtime_execution.get("detail", "")),
            f"/runtime result: {runtime_execution}",
        )
        _append_check(
            results,
            "tools_command_executes",
            tools_execution.get("kind") == "activity" and len(str(tools_execution.get("detail", "")).splitlines()) >= 5,
            f"/tools result title: {tools_execution.get('title', '')}",
        )
        _append_check(
            results,
            "extensions_command_executes",
            extensions_execution.get("kind") == "activity" and "Hybrid Eval Helpers" in str(extensions_execution.get("detail", "")),
            f"/extensions result: {extensions_execution}",
        )
        _append_check(
            results,
            "prompt_command_executes_via_backend",
            architecture_execution.get("kind") == "prompt" and "Additional context: hybrid runtime" in str(architecture_execution.get("prompt_text", "")),
            f"/architecture result: {architecture_execution}",
        )
        _append_check(
            results,
            "extension_prompt_executes_via_backend",
            extension_command_execution.get("kind") == "prompt" and "hybrid eval extension is loaded" in str(extension_command_execution.get("prompt_text", "")).lower(),
            f"/hybrid-eval-check result: {extension_command_execution}",
        )
        _append_check(
            results,
            "approval_command_routes_through_backend",
            approval_execution.get("kind") == "operator_action" and "no active task waiting for approval" in str(approval_execution.get("detail", "")).lower(),
            f"/approve result: {approval_execution}",
        )

        create_session_payload = _request_json(base_url, "/sessions", method="POST", payload={"title": "Hybrid eval"})
        session = (create_session_payload.get("data", {}) or {}).get("session", {})
        session_id = str(session.get("session_id", "")).strip()
        _append_check(results, "session_created_for_stream_eval", bool(session_id), f"Session id: {session_id or '[missing]'}")

        if session_id:
            stream_url = base_url + "/events/stream?" + urllib.parse.urlencode({"session_id": session_id})
            request = urllib.request.Request(stream_url, headers={"Accept": "text/event-stream"})
            stream = urllib.request.urlopen(request, timeout=20)

            # Prime the stream.
            for _ in range(3):
                event = _stream_next_event(stream, timeout_seconds=6.0)
                if not event:
                    break
                seen_events.append(str(event.get("event", "")).strip())
                if event.get("event") in {"session.sync", "session.frame"}:
                    break

            SETTINGS_LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_LOCAL_PATH.write_text("model: gpt-5.4-mini\nreasoning:\n  effort: low\n", encoding="utf-8")

            runtime_seen = False
            runtime_deadline = time.time() + 10.0
            while time.time() < runtime_deadline and not runtime_seen:
                event = _stream_next_event(stream, timeout_seconds=2.5)
                if not event:
                    continue
                seen_events.append(str(event.get("event", "")).strip())
                if event.get("event") == "runtime.updated":
                    runtime_seen = True
                    break
                if event.get("event") == "session.frame":
                    changed = ((event.get("data", {}) or {}).get("changed", []) if isinstance(event.get("data", {}), dict) else [])
                    if "runtime" in changed:
                        runtime_seen = True
                        break

            server.controller.manager.scheduler_backend._last_message = "Hybrid eval infrastructure update."

            infrastructure_seen = False
            infrastructure_deadline = time.time() + 10.0
            while time.time() < infrastructure_deadline and not infrastructure_seen:
                event = _stream_next_event(stream, timeout_seconds=2.5)
                if not event:
                    continue
                seen_events.append(str(event.get("event", "")).strip())
                if event.get("event") == "infrastructure.updated":
                    infrastructure_seen = True
                    break
                if event.get("event") == "session.frame":
                    changed = ((event.get("data", {}) or {}).get("changed", []) if isinstance(event.get("data", {}), dict) else [])
                    if "infrastructure" in changed:
                        infrastructure_seen = True
                        break

            _append_check(
                results,
                "runtime_stream_update_visible",
                runtime_seen,
                f"Observed events: {seen_events}",
            )
            _append_check(
                results,
                "infrastructure_stream_update_visible",
                infrastructure_seen,
                f"Observed events: {seen_events}",
            )

        _write_report(
            results,
            extra={
                "base_url": base_url,
                "observed_events": seen_events,
            },
        )
    finally:
        try:
            if stream is not None:
                stream.close()
        except Exception:
            pass
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
        if thread is not None:
            thread.join(timeout=3)
        if extension_backup is None:
            try:
                extension_path.unlink()
            except FileNotFoundError:
                pass
        else:
            extension_path.write_text(extension_backup, encoding="utf-8")
        if settings_backup is None:
            try:
                SETTINGS_LOCAL_PATH.unlink()
            except FileNotFoundError:
                pass
        else:
            SETTINGS_LOCAL_PATH.write_text(settings_backup, encoding="utf-8")

    failures = [item for item in results if not item.passed]
    if failures:
        for failure in failures:
            print(f"[FAIL] {failure.name}: {failure.detail}")
        print(f"[INFO] Wrote report to {REPORT_PATH}")
        return 1

    print(f"[OK] Hybrid eval passed with {len(results)} checks.")
    print(f"[INFO] Wrote report to {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
