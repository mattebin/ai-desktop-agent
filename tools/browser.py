from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List
from urllib.parse import urljoin, urlparse


BROWSER_DEFAULT_SESSION_ID = "default"
BROWSER_DEFAULT_TIMEOUT_MS = 6000
BROWSER_DEFAULT_MAX_TEXT_CHARS = 1400
BROWSER_DEFAULT_MAX_ELEMENTS = 6
BROWSER_DEFAULT_MAX_RETRIES = 1
BROWSER_HISTORY_LIMIT = 8
BROWSER_ALLOWED_URL_SCHEMES = {"about", "data", "file", "http", "https"}
RISKY_CLICK_TERMS = {
    "buy",
    "checkout",
    "confirm",
    "create account",
    "delete",
    "login",
    "log in",
    "pay",
    "place order",
    "purchase",
    "save",
    "send",
    "sign in",
    "sign up",
    "submit",
}
RISKY_NAVIGATION_TERMS = {
    "account",
    "billing",
    "checkout",
    "confirm",
    "delete",
    "login",
    "log in",
    "logout",
    "log out",
    "password",
    "pay",
    "payment",
    "profile",
    "purchase",
    "save",
    "settings",
    "sign in",
    "sign out",
    "submit",
}
BROWSER_CHECKPOINT_RESUME_KEYS = {
    "allow_reload",
    "allow_reinspect",
    "checkpoint_reason",
    "exact",
    "expect_navigation",
    "expected_target",
    "expected_text_contains",
    "expected_title_contains",
    "expected_url_contains",
    "headless",
    "index",
    "label",
    "max_elements",
    "max_retries",
    "max_text_chars",
    "name",
    "name_attr",
    "placeholder",
    "role",
    "selector",
    "session_id",
    "text",
    "timeout_ms",
    "url",
    "workflow_name",
    "workflow_next_step",
    "workflow_pattern",
    "workflow_step",
    "browser_task_name",
    "browser_task_next_step",
    "browser_task_step",
    "resume_label",
    "resume_selector",
    "resume_value",
}
BROWSER_TOOL_NAMES = {
    "browser_open_page",
    "browser_inspect_page",
    "browser_click",
    "browser_type",
    "browser_extract_text",
    "browser_follow_link",
}


@dataclass
class BrowserSession:
    session_id: str
    browser: Any
    context: Any
    page: Any
    launched_with: str
    headless: bool
    owner_thread_id: int = field(default_factory=threading.get_ident)
    created_at: float = field(default_factory=time.time)
    last_action_at: float = field(default_factory=time.time)
    history: List[str] = field(default_factory=list)


_PLAYWRIGHT_RUNTIMES: Dict[int, Any] = {}
_PLAYWRIGHT_TIMEOUT_ERROR = Exception
_BROWSER_SESSIONS: Dict[str, BrowserSession] = {}


def _trim_text(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value).split())
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _coerce_int(value: Any, default: int, *, minimum: int = 0, maximum: int = 20) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default

    if parsed < minimum:
        parsed = minimum
    if parsed > maximum:
        parsed = maximum
    return parsed


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _css_quote(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def _normalize_visible_text(value: Any, limit: int) -> str:
    return _trim_text(str(value or "").replace("\r", " ").replace("\n", " "), limit=limit)


def _mask_typed_value(value: str, input_type: str) -> str:
    if input_type == "password":
        return f"(masked {len(value)} chars)"
    return _trim_text(value, limit=80)


def _playwright_components():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
    except Exception as exc:
        return None, exc
    return (sync_playwright, PlaywrightTimeoutError), None


def _ensure_runtime():
    global _PLAYWRIGHT_TIMEOUT_ERROR

    thread_id = threading.get_ident()
    runtime = _PLAYWRIGHT_RUNTIMES.get(thread_id)
    if runtime is not None:
        return runtime, None

    components, error = _playwright_components()
    if error is not None:
        return None, (
            "Playwright is not installed. Add it with 'pip install playwright' inside the project venv "
            "before using browser tools."
        )

    sync_playwright, playwright_timeout_error = components
    try:
        runtime = sync_playwright().start()
        _PLAYWRIGHT_TIMEOUT_ERROR = playwright_timeout_error
    except Exception as exc:
        return None, f"Could not start Playwright: {exc}"
    _PLAYWRIGHT_RUNTIMES[thread_id] = runtime
    return runtime, None


def _launch_browser(runtime, *, headless: bool):
    launch_errors: List[str] = []
    attempts = [
        ("msedge", {"channel": "msedge", "headless": headless}),
        ("chromium", {"headless": headless}),
    ]

    for label, kwargs in attempts:
        try:
            browser = runtime.chromium.launch(**kwargs)
            return browser, label, launch_errors
        except Exception as exc:
            launch_errors.append(f"{label}: {exc}")

    return None, "", launch_errors


def _ensure_session(session_id: str, *, headless: bool):
    session = _BROWSER_SESSIONS.get(session_id)
    if session is not None:
        try:
            if session.owner_thread_id == threading.get_ident() and not session.page.is_closed():
                session.last_action_at = time.time()
                return session, ""
        except Exception:
            pass
        try:
            session.context.close()
        except Exception:
            pass
        try:
            session.browser.close()
        except Exception:
            pass
        _BROWSER_SESSIONS.pop(session_id, None)

    runtime, error = _ensure_runtime()
    if error:
        return None, error

    browser, launched_with, launch_errors = _launch_browser(runtime, headless=headless)
    if browser is None:
        joined = " | ".join(launch_errors) if launch_errors else "no launch attempts succeeded"
        return None, f"Could not launch a supported browser: {joined}"

    try:
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
    except Exception as exc:
        try:
            browser.close()
        except Exception:
            pass
        return None, f"Could not create a browser page: {exc}"

    session = BrowserSession(
        session_id=session_id,
        browser=browser,
        context=context,
        page=page,
        launched_with=launched_with,
        headless=headless,
    )
    _BROWSER_SESSIONS[session_id] = session
    return session, ""


def _get_existing_session(session_id: str):
    session = _BROWSER_SESSIONS.get(session_id)
    if session is None:
        return None, "No browser session is open yet. Start with browser_open_page."
    if session.owner_thread_id != threading.get_ident():
        _BROWSER_SESSIONS.pop(session_id, None)
        return None, "The previous browser session cannot be reused from this worker thread. Re-open the page before continuing."
    try:
        if session.page.is_closed():
            _BROWSER_SESSIONS.pop(session_id, None)
            return None, "The browser session is no longer active. Re-open a page first."
    except Exception:
        _BROWSER_SESSIONS.pop(session_id, None)
        return None, "The browser session is no longer active. Re-open a page first."
    session.last_action_at = time.time()
    return session, ""


def _normalize_url(url: str, *, base_url: str = ""):
    raw_url = str(url).strip()
    if not raw_url:
        return "", "Missing url"

    resolved_url = urljoin(base_url, raw_url) if base_url else raw_url
    if resolved_url == "about:blank":
        return resolved_url, ""

    parsed = urlparse(resolved_url)
    if parsed.scheme.lower() not in BROWSER_ALLOWED_URL_SCHEMES:
        return "", f"Blocked URL scheme: {parsed.scheme or 'missing'}"
    return resolved_url, ""


def _selector_hint(details: Dict[str, Any]) -> str:
    tag = str(details.get("tag", "")).strip() or "element"
    if details.get("id"):
        return f"#{details['id']}"
    if details.get("name"):
        return f"{tag}[name='{_css_quote(details['name'])}']"
    if tag in {"input", "textarea", "select"} and details.get("placeholder"):
        return f"{tag}[placeholder='{_css_quote(details['placeholder'])}']"
    if tag == "a" and details.get("href") and len(str(details["href"])) < 120:
        return f"a[href='{_css_quote(details['href'])}']"
    return tag


def _element_details(locator) -> Dict[str, Any]:
    try:
        data = locator.evaluate(
            """
            el => ({
                tag: (el.tagName || '').toLowerCase(),
                id: el.id || '',
                name: el.getAttribute('name') || '',
                type: (el.getAttribute('type') || '').toLowerCase(),
                href: el.getAttribute('href') || '',
                placeholder: el.getAttribute('placeholder') || '',
                role: el.getAttribute('role') || '',
                formAction: el.form ? (el.form.getAttribute('action') || '') : '',
                text: ((el.innerText || el.textContent || '').replace(/\\s+/g, ' ')).trim(),
            })
            """
        )
    except Exception:
        data = {
            "tag": "",
            "id": "",
            "name": "",
            "type": "",
            "href": "",
            "placeholder": "",
            "role": "",
            "formAction": "",
            "text": "",
        }

    details = {
        "tag": str(data.get("tag", "")).strip(),
        "id": str(data.get("id", "")).strip(),
        "name": str(data.get("name", "")).strip(),
        "type": str(data.get("type", "")).strip(),
        "href": str(data.get("href", "")).strip(),
        "placeholder": str(data.get("placeholder", "")).strip(),
        "role": str(data.get("role", "")).strip(),
        "form_action": str(data.get("formAction", "")).strip(),
        "text": _trim_text(data.get("text", ""), limit=120),
    }
    details["selector_hint"] = _selector_hint(details)
    return details


def _collect_elements(page, selector: str, kind: str, max_elements: int) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    try:
        locator = page.locator(selector)
        count = locator.count()
    except Exception:
        return items

    for index in range(min(count, max_elements)):
        details = _element_details(locator.nth(index))
        details["kind"] = kind
        items.append(details)
    return items


def _snapshot_page(page, *, max_text_chars: int, max_elements: int) -> Dict[str, Any]:
    try:
        title = page.title()
    except Exception:
        title = ""

    visible_text = ""
    try:
        visible_text = page.locator("body").inner_text(timeout=1000)
    except Exception:
        try:
            visible_text = page.locator("body").text_content(timeout=1000) or ""
        except Exception:
            visible_text = ""

    return {
        "url": str(page.url),
        "title": _trim_text(title, limit=140),
        "visible_text_excerpt": _normalize_visible_text(visible_text, limit=max_text_chars),
        "links": _collect_elements(page, "a:visible", "link", max_elements),
        "buttons": _collect_elements(page, "button:visible, input[type=button]:visible, input[type=submit]:visible", "button", max_elements),
        "inputs": _collect_elements(page, "input, textarea, select", "input", max_elements),
    }


def _record_history(session: BrowserSession, summary: str, *, url: str):
    entry = _trim_text(f"{summary} @ {url}", limit=220)
    session.history.append(entry)
    if len(session.history) > BROWSER_HISTORY_LIMIT:
        del session.history[:-BROWSER_HISTORY_LIMIT]
    session.last_action_at = time.time()


def _candidate_locator_specs(args: Dict[str, Any], action_kind: str):
    selector = str(args.get("selector", "")).strip()
    text = str(args.get("text", "")).strip()
    label = str(args.get("label", "")).strip()
    placeholder = str(args.get("placeholder", "")).strip()
    role = str(args.get("role", "")).strip()
    role_name = str(args.get("name", "")).strip()
    name_attr = str(args.get("name_attr", "")).strip()
    exact = _coerce_bool(args.get("exact", False), False)

    specs = []
    if selector:
        specs.append((f"selector={selector}", lambda page, selector=selector: page.locator(selector)))
    if role and role_name:
        specs.append((f"role={role} name={role_name}", lambda page, role=role, role_name=role_name, exact=exact: page.get_by_role(role, name=role_name, exact=exact)))
    if label:
        specs.append((f"label={label}", lambda page, label=label, exact=exact: page.get_by_label(label, exact=exact)))
    if placeholder:
        specs.append((f"placeholder={placeholder}", lambda page, placeholder=placeholder, exact=exact: page.get_by_placeholder(placeholder, exact=exact)))
    if name_attr:
        specs.append((f"name_attr={name_attr}", lambda page, name_attr=name_attr: page.locator(f"[name='{_css_quote(name_attr)}']")))
    if text:
        if action_kind in {"click", "follow_link"}:
            specs.append((f"link text={text}", lambda page, text=text, exact=exact: page.get_by_role("link", name=text, exact=exact)))
        if action_kind == "click":
            specs.append((f"button text={text}", lambda page, text=text, exact=exact: page.get_by_role("button", name=text, exact=exact)))
        if action_kind == "type":
            specs.append((f"textbox label={text}", lambda page, text=text, exact=exact: page.get_by_label(text, exact=exact)))
            specs.append((f"textbox placeholder={text}", lambda page, text=text, exact=exact: page.get_by_placeholder(text, exact=exact)))
        specs.append((f"text={text}", lambda page, text=text, exact=exact: page.get_by_text(text, exact=exact)))
    return specs


def _resolve_locator(page, args: Dict[str, Any], action_kind: str):
    index = _coerce_int(args.get("index", 0), 0, minimum=0, maximum=20)
    attempts: List[str] = []

    for description, builder in _candidate_locator_specs(args, action_kind):
        try:
            locator = builder(page)
            count = locator.count()
        except Exception as exc:
            attempts.append(f"{description}: error {exc}")
            continue

        attempts.append(f"{description}: {count} match(es)")
        if count <= 0:
            continue

        selected_index = index if count > index else 0
        return locator.nth(selected_index), {
            "strategy": description,
            "matches": count,
            "selected_index": selected_index,
        }, attempts

    return None, {}, attempts


def _session_payload(session: BrowserSession | None) -> Dict[str, Any]:
    if session is None:
        return {}
    return {
        "session_id": session.session_id,
        "launched_with": session.launched_with,
        "headless": session.headless,
        "history": session.history[-BROWSER_HISTORY_LIMIT:],
    }


def _build_result(
    *,
    ok: bool,
    action: str,
    session: BrowserSession | None = None,
    summary: str = "",
    page: Dict[str, Any] | None = None,
    **extra: Any,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "ok": ok,
        "action": action,
        "summary": summary.strip(),
    }
    result.update(_session_payload(session))
    if page is not None:
        result["page"] = page
        result["current_url"] = page.get("url", "")
        result["current_title"] = page.get("title", "")
    result.update(extra)
    return result


def _page_label(page: Dict[str, Any] | None, fallback_url: str = "") -> str:
    if isinstance(page, dict):
        title = _trim_text(str(page.get("title", "")).strip(), limit=140)
        url = _trim_text(str(page.get("url", "")).strip(), limit=160)
        if title and url:
            return f"{title} ({url})"
        if title:
            return title
        if url:
            return url
    return _trim_text(fallback_url.strip(), limit=160)


def _safe_page_snapshot(session: BrowserSession, *, max_text_chars: int, max_elements: int) -> Dict[str, Any]:
    try:
        return _snapshot_page(
            session.page,
            max_text_chars=max_text_chars,
            max_elements=max_elements,
        )
    except Exception:
        return {
            "url": str(session.page.url),
            "title": "",
            "visible_text_excerpt": "",
            "links": [],
            "buttons": [],
            "inputs": [],
        }


def _wait_for_page_settle(page, timeout_ms: int, *, extra_wait_ms: int = 200):
    settle_timeout = min(timeout_ms, 2500)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=settle_timeout)
    except Exception:
        pass
    try:
        wait_ms = max(0, min(int(extra_wait_ms), settle_timeout))
    except (TypeError, ValueError):
        wait_ms = 200
    try:
        if wait_ms:
            page.wait_for_timeout(wait_ms)
    except Exception:
        pass


def _navigate_page(page, url: str, timeout_ms: int):
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except _PLAYWRIGHT_TIMEOUT_ERROR:
        return False, f"Timed out while opening {url}"
    except Exception as exc:
        return False, f"Could not open {url}: {exc}"
    _wait_for_page_settle(page, timeout_ms)
    return True, ""


def _extract_text_from_locator(locator, timeout_ms: int) -> str:
    try:
        return locator.inner_text(timeout=timeout_ms)
    except Exception:
        try:
            return locator.text_content(timeout=timeout_ms) or ""
        except Exception:
            return ""


def _extract_link_href(locator) -> str:
    try:
        href = locator.evaluate(
            """
            el => {
                const anchor = el.closest('a');
                return anchor ? (anchor.getAttribute('href') || '') : '';
            }
            """
        )
    except Exception:
        href = ""
    return str(href or "").strip()


def _approval_granted(args: Dict[str, Any]) -> bool:
    return str(args.get("approval_status", "")).strip().lower() == "approved"


def _checkpoint_requested(args: Dict[str, Any]) -> bool:
    return _coerce_bool(args.get("checkpoint_required", False), False)


def _checkpoint_resume_requested(args: Dict[str, Any]) -> bool:
    return _coerce_bool(args.get("resume_from_checkpoint", False), False)


def _headless_setting(args: Dict[str, Any]) -> bool:
    return _coerce_bool(args.get("headless", True), True)


def _checkpoint_reason(args: Dict[str, Any], *, default: str) -> str:
    explicit = str(args.get("checkpoint_reason", "")).strip()
    return _trim_text(explicit or default, limit=180)


def _checkpoint_resume_args(args: Dict[str, Any]) -> Dict[str, Any]:
    resume_args: Dict[str, Any] = {}
    for key in BROWSER_CHECKPOINT_RESUME_KEYS:
        if key not in args:
            continue
        value = args.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        resume_args[key] = value
    resume_args["checkpoint_required"] = False
    resume_args.pop("approval_status", None)
    resume_args.pop("resume_from_checkpoint", None)
    return resume_args


def shutdown_browser_runtime():
    for session_id, session in list(_BROWSER_SESSIONS.items()):
        try:
            session.context.close()
        except Exception:
            pass
        try:
            session.browser.close()
        except Exception:
            pass
        _BROWSER_SESSIONS.pop(session_id, None)

    for thread_id, runtime in list(_PLAYWRIGHT_RUNTIMES.items()):
        try:
            runtime.stop()
        except Exception:
            pass
        _PLAYWRIGHT_RUNTIMES.pop(thread_id, None)


def _recover_checkpoint_session(
    args: Dict[str, Any],
    settings: Dict[str, Any],
    recovery: Dict[str, Any],
    *,
    original_error: str,
) -> tuple[BrowserSession | None, str]:
    if not _checkpoint_resume_requested(args):
        return None, original_error

    reopen_url = _safe_reopen_url(str(args.get("url", "")).strip())
    if not reopen_url:
        return None, original_error

    _record_recovery_attempt(
        recovery,
        "Recovered the paused browser session by reopening the checkpoint page in a fresh local session.",
        fallback="checkpoint_reopen",
    )
    session, session_error = _ensure_session(settings["session_id"], headless=_headless_setting(args))
    if session_error:
        return None, f"{original_error} Could not reopen the checkpoint page: {session_error}"

    ok, navigation_error = _navigate_page(session.page, reopen_url, settings["timeout_ms"])
    if not ok:
        return None, f"{original_error} Could not reopen the checkpoint page: {navigation_error}"

    resume_value = str(args.get("resume_value", "")).strip()
    if resume_value:
        restore_args: Dict[str, Any] = {
            "session_id": settings["session_id"],
            "value": resume_value,
            "timeout_ms": settings["timeout_ms"],
            "max_text_chars": settings["max_text_chars"],
            "max_elements": settings["max_elements"],
            "max_retries": settings["max_retries"],
            "allow_reinspect": True,
            "allow_reload": False,
            "expected_title_contains": str(args.get("expected_title_contains", "")).strip(),
        }
        resume_selector = str(args.get("resume_selector", "")).strip()
        resume_label = str(args.get("resume_label", "")).strip()
        if resume_selector:
            restore_args["selector"] = resume_selector
        elif resume_label:
            restore_args["label"] = resume_label
        restore_result = browser_type(restore_args)
        if not restore_result.get("ok", False):
            restore_error = str(restore_result.get("error", "") or restore_result.get("summary", "")).strip()
            return None, f"{original_error} Could not restore the approved field state: {restore_error or 'unknown error'}"
        _add_recovery_note(
            recovery,
            "Restored the previously typed field state before resuming the paused browser click.",
            fallback="checkpoint_restore",
        )

    return session, ""


def _is_risky_navigation(target_text: str, target_url: str, expected_state: Dict[str, Any]) -> bool:
    navigation_text = " ".join(
        [
            str(target_text or ""),
            str(target_url or ""),
            str(expected_state.get("target", "") or ""),
            str(expected_state.get("url_contains", "") or ""),
            str(expected_state.get("title_contains", "") or ""),
            str(expected_state.get("text_contains", "") or ""),
        ]
    ).lower()
    return any(term in navigation_text for term in RISKY_NAVIGATION_TERMS)


def _checkpoint_pause_result(
    *,
    action: str,
    session: BrowserSession | None,
    summary: str,
    page: Dict[str, Any] | None,
    expected_state: Dict[str, Any],
    recovery: Dict[str, Any],
    args: Dict[str, Any],
    checkpoint_reason: str,
    checkpoint_target: str,
    checkpoint_step: str,
    last_action: str,
    checkpoint_category: str = "approval",
    risky_action: bool = False,
    **extra: Any,
) -> Dict[str, Any]:
    resume_args = _checkpoint_resume_args(args)
    if checkpoint_step:
        resume_args.setdefault("workflow_step", checkpoint_step)
    return _browser_result(
        ok=False,
        action=action,
        session=session,
        summary=summary,
        page=page,
        expected_state=expected_state,
        recovery=recovery,
        error=f"Approval required before continuing: {checkpoint_reason}",
        approval_required=True,
        approval_status="not approved",
        paused=True,
        checkpoint_required=True,
        checkpoint_reason=checkpoint_reason,
        checkpoint_target=_trim_text(checkpoint_target, limit=140),
        checkpoint_step=checkpoint_step[:120],
        resume_step=checkpoint_step[:120],
        checkpoint_category=checkpoint_category,
        checkpoint_tool=action,
        checkpoint_resume_args=resume_args,
        workflow_status="paused",
        risky_action=risky_action,
        last_action=last_action,
        **extra,
    )


def _is_risky_click(details: Dict[str, Any]) -> bool:
    target_text = " ".join(
        [
            str(details.get("tag", "")),
            str(details.get("type", "")),
            str(details.get("text", "")),
            str(details.get("name", "")),
            str(details.get("id", "")),
            str(details.get("form_action", "")),
        ]
    ).lower()
    if details.get("type") == "submit":
        return True
    return any(term in target_text for term in RISKY_CLICK_TERMS)


def _is_submit_like_target(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return any(term in text for term in RISKY_CLICK_TERMS)


def _button_locator_fallback_args(args: Dict[str, Any], page: Dict[str, Any]) -> tuple[Dict[str, Any], str]:
    buttons = page.get("buttons", []) if isinstance(page, dict) else []
    if not isinstance(buttons, list):
        return {}, ""

    target_text = " ".join(
        str(args.get(key, "")).strip()
        for key in ("selector", "text", "label", "name", "placeholder")
        if str(args.get(key, "")).strip()
    ).lower()
    if not _is_submit_like_target(target_text):
        return {}, ""

    candidates: List[Dict[str, Any]] = []
    for button in buttons[:6]:
        if not isinstance(button, dict):
            continue
        candidate_text = " ".join(
            str(button.get(key, "")).strip()
            for key in ("text", "id", "name", "type", "selector_hint")
            if str(button.get(key, "")).strip()
        )
        if _is_submit_like_target(candidate_text):
            candidates.append(button)

    if not candidates:
        return {}, ""

    chosen: Dict[str, Any] | None = None
    if len(candidates) == 1:
        chosen = candidates[0]
    elif "submit" in target_text:
        for candidate in candidates:
            candidate_text = " ".join(
                str(candidate.get(key, "")).strip().lower()
                for key in ("text", "id", "name", "type", "selector_hint")
            )
            if "submit" in candidate_text:
                chosen = candidate
                break

    if chosen is None:
        return {}, ""

    button_text = str(chosen.get("text", "")).strip()
    if button_text:
        return (
            {
                "selector": "",
                "text": button_text,
                "label": "",
                "placeholder": "",
                "role": "",
                "name": "",
                "name_attr": "",
                "exact": True,
            },
            f"Used visible button text '{button_text}' from the current page as a fallback locator.",
        )

    selector_hint = str(chosen.get("selector_hint", "")).strip()
    if selector_hint.startswith("#"):
        return (
            {
                "selector": selector_hint,
                "text": "",
                "label": "",
                "placeholder": "",
                "role": "",
                "name": "",
                "name_attr": "",
            },
            f"Used visible button selector '{selector_hint}' from the current page as a fallback locator.",
        )

    return {}, ""


def _selector_summary(args: Dict[str, Any]) -> str:
    for key in ("selector", "text", "label", "placeholder", "role", "name", "name_attr"):
        value = str(args.get(key, "")).strip()
        if value:
            return f"{key}={value}"
    return "target element"


def _browser_settings(args: Dict[str, Any], *, include_headless: bool = False) -> Dict[str, Any]:
    settings = {
        "session_id": str(args.get("session_id", BROWSER_DEFAULT_SESSION_ID)).strip() or BROWSER_DEFAULT_SESSION_ID,
        "timeout_ms": _coerce_int(args.get("timeout_ms", BROWSER_DEFAULT_TIMEOUT_MS), BROWSER_DEFAULT_TIMEOUT_MS, minimum=1000, maximum=30000),
        "max_text_chars": _coerce_int(args.get("max_text_chars", BROWSER_DEFAULT_MAX_TEXT_CHARS), BROWSER_DEFAULT_MAX_TEXT_CHARS, minimum=200, maximum=4000),
        "max_elements": _coerce_int(args.get("max_elements", BROWSER_DEFAULT_MAX_ELEMENTS), BROWSER_DEFAULT_MAX_ELEMENTS, minimum=1, maximum=12),
        "max_retries": _coerce_int(args.get("max_retries", BROWSER_DEFAULT_MAX_RETRIES), BROWSER_DEFAULT_MAX_RETRIES, minimum=0, maximum=2),
        "allow_reinspect": _coerce_bool(args.get("allow_reinspect", True), True),
        "allow_reload": _coerce_bool(args.get("allow_reload", True), True),
    }
    if include_headless:
        settings["headless"] = _coerce_bool(args.get("headless", True), True)
    return settings


def _build_expected_state(args: Dict[str, Any], *, default_target: str = "") -> Dict[str, Any]:
    target = str(args.get("expected_target", "")).strip() or default_target
    return {
        "target": _trim_text(target, limit=120),
        "url_contains": str(args.get("expected_url_contains", "")).strip(),
        "title_contains": str(args.get("expected_title_contains", "")).strip(),
        "text_contains": str(args.get("expected_text_contains", "")).strip(),
        "expect_navigation": _coerce_bool(args.get("expect_navigation", False), False),
    }


def _has_expected_state(expected_state: Dict[str, Any]) -> bool:
    return any(
        [
            str(expected_state.get("url_contains", "")).strip(),
            str(expected_state.get("title_contains", "")).strip(),
            str(expected_state.get("text_contains", "")).strip(),
            bool(expected_state.get("expect_navigation", False)),
        ]
    )


def _page_looks_empty(page: Dict[str, Any]) -> bool:
    return not any(
        [
            str(page.get("visible_text_excerpt", "")).strip(),
            page.get("links", []),
            page.get("buttons", []),
            page.get("inputs", []),
        ]
    )


def _page_expectation_issues(page: Dict[str, Any], expected_state: Dict[str, Any], *, before_url: str = "") -> List[str]:
    issues: List[str] = []
    current_url = str(page.get("url", "")).strip()
    current_title = str(page.get("title", "")).strip()
    visible_text = str(page.get("visible_text_excerpt", "")).strip()

    if _page_looks_empty(page):
        issues.append("page inspection looked empty")

    expected_url = str(expected_state.get("url_contains", "")).strip().lower()
    if expected_url and expected_url not in current_url.lower():
        issues.append(f"current URL did not match '{expected_state['url_contains']}'")

    expected_title = str(expected_state.get("title_contains", "")).strip().lower()
    if expected_title and expected_title not in current_title.lower():
        issues.append(f"page title did not match '{expected_state['title_contains']}'")

    expected_text = str(expected_state.get("text_contains", "")).strip().lower()
    if expected_text and expected_text not in visible_text.lower():
        issues.append(f"page text did not match '{expected_state['text_contains']}'")

    if expected_state.get("expect_navigation") and before_url and current_url == before_url:
        issues.append("navigation did not occur")

    return issues


def _new_recovery_state(settings: Dict[str, Any], *, reason: str) -> Dict[str, Any]:
    return {
        "reason": reason,
        "max_retries": int(settings.get("max_retries", 0) or 0),
        "attempt_count": 0,
        "fallbacks_used": [],
        "notes": [],
    }


def _add_recovery_note(recovery: Dict[str, Any], note: str, *, fallback: str = ""):
    text = _trim_text(note, limit=180)
    if text and text not in recovery["notes"]:
        recovery["notes"].append(text)
        if len(recovery["notes"]) > 6:
            del recovery["notes"][:-6]
    if fallback and fallback not in recovery["fallbacks_used"]:
        recovery["fallbacks_used"].append(fallback)


def _record_recovery_attempt(recovery: Dict[str, Any], note: str, *, fallback: str = ""):
    recovery["attempt_count"] += 1
    _add_recovery_note(recovery, note, fallback=fallback)


def _recovery_payload(recovery: Dict[str, Any], *, recovered: bool) -> Dict[str, Any]:
    attempt_count = int(recovery.get("attempt_count", 0) or 0)
    fallback_count = len(recovery.get("fallbacks_used", []))
    if attempt_count <= 0:
        status = "none"
    elif recovered:
        status = "recovered"
    else:
        status = "failed"
    return {
        "reason": str(recovery.get("reason", "")).strip(),
        "max_retries": int(recovery.get("max_retries", 0) or 0),
        "attempt_count": attempt_count,
        "fallback_count": fallback_count,
        "fallbacks_used": list(recovery.get("fallbacks_used", [])),
        "notes": list(recovery.get("notes", [])),
        "recovered": recovered and attempt_count > 0,
        "status": status,
    }


def _recovery_summary(recovery_payload: Dict[str, Any]) -> str:
    attempt_count = int(recovery_payload.get("attempt_count", 0) or 0)
    if attempt_count <= 0:
        return ""
    fallbacks = list(recovery_payload.get("fallbacks_used", []))
    if fallbacks:
        return f"Used {attempt_count} recovery step(s): {', '.join(fallbacks)}."
    return f"Used {attempt_count} recovery step(s)."


def _summary_with_recovery(summary: str, recovery_payload: Dict[str, Any]) -> str:
    attempt_count = int(recovery_payload.get("attempt_count", 0) or 0)
    if attempt_count <= 0:
        return summary
    return _trim_text(f"{summary} (after {attempt_count} recovery step(s))", limit=220)


def _issues_summary(issues: List[str]) -> str:
    cleaned = [str(issue).strip() for issue in issues if str(issue).strip()]
    if not cleaned:
        return "browser state did not match the expected result"
    return "; ".join(cleaned[:2])


def _browser_result(
    *,
    ok: bool,
    action: str,
    session: BrowserSession | None,
    summary: str,
    page: Dict[str, Any] | None,
    expected_state: Dict[str, Any] | None,
    recovery: Dict[str, Any],
    expectation_issues: List[str] | None = None,
    last_action: str = "",
    error: str = "",
    **extra: Any,
) -> Dict[str, Any]:
    issues = [str(issue).strip() for issue in (expectation_issues or []) if str(issue).strip()]
    recovery_payload = _recovery_payload(recovery, recovered=ok and not issues)
    final_summary = _summary_with_recovery(summary.strip(), recovery_payload) if summary else ""
    result = _build_result(ok=ok, action=action, session=session, summary=final_summary, page=page, **extra)
    if error:
        result["error"] = error.strip()
    if expected_state is not None:
        result["expected_state"] = expected_state
        result["expected_state_met"] = not issues
    if issues:
        result["expectation_issues"] = issues[:3]
    result["recovery"] = recovery_payload
    result["retry_count"] = recovery_payload["attempt_count"]
    result["fallback_attempts"] = recovery_payload["fallback_count"]
    result["recovery_notes"] = recovery_payload["notes"]
    result["recovery_status"] = recovery_payload["status"]
    recovery_summary = _recovery_summary(recovery_payload)
    if recovery_summary:
        result["recovery_summary"] = recovery_summary
    result["last_browser_action"] = last_action or final_summary or action
    result["browser_state"] = {
        "session_id": result.get("session_id", ""),
        "current_url": result.get("current_url", ""),
        "current_title": result.get("current_title", ""),
        "expected_state": expected_state or {},
        "expected_state_met": not issues,
        "last_action": result["last_browser_action"],
        "retry_count": recovery_payload["attempt_count"],
        "fallback_attempts": recovery_payload["fallback_count"],
    }
    return result


def _snapshot_with_issues(session: BrowserSession, settings: Dict[str, Any], expected_state: Dict[str, Any], *, before_url: str = ""):
    page = _safe_page_snapshot(session, max_text_chars=settings["max_text_chars"], max_elements=settings["max_elements"])
    issues = _page_expectation_issues(page, expected_state, before_url=before_url)
    return page, issues


def _safe_reopen_url(url: str) -> str:
    normalized_url, url_error = _normalize_url(str(url).strip())
    if url_error:
        return ""
    return normalized_url


def _should_try_reopen(page: Dict[str, Any], issues: List[str], expected_state: Dict[str, Any]) -> bool:
    if not issues:
        return False
    if _page_looks_empty(page):
        return True
    if bool(expected_state.get("expect_navigation", False)):
        return True
    return any(
        issue == "navigation did not occur" or issue.startswith("current URL did not match")
        for issue in issues
    )


def _recover_page_state(
    session: BrowserSession,
    settings: Dict[str, Any],
    recovery: Dict[str, Any],
    expected_state: Dict[str, Any],
    *,
    before_url: str = "",
    reopen_url: str = "",
    reason: str,
):
    page, issues = _snapshot_with_issues(session, settings, expected_state, before_url=before_url)
    if not issues or settings["max_retries"] <= 0:
        return page, issues

    safe_reopen_url = _safe_reopen_url(reopen_url)
    for attempt_index in range(settings["max_retries"]):
        if settings["allow_reinspect"]:
            _record_recovery_attempt(recovery, f"Re-inspected the page while {reason}.", fallback="reinspect")
            wait_ms = min(settings["timeout_ms"], 250 + ((attempt_index + 1) * 250))
            _wait_for_page_settle(session.page, settings["timeout_ms"], extra_wait_ms=wait_ms)
            page, issues = _snapshot_with_issues(session, settings, expected_state, before_url=before_url)
            if not issues:
                return page, issues

        should_reopen = settings["allow_reload"] and safe_reopen_url and _should_try_reopen(page, issues, expected_state)
        if should_reopen:
            _record_recovery_attempt(recovery, f"Re-opened {safe_reopen_url} while {reason}.", fallback="reopen")
            ok, navigation_error = _navigate_page(session.page, safe_reopen_url, settings["timeout_ms"])
            if not ok:
                _add_recovery_note(recovery, f"Re-open failed during browser recovery: {navigation_error}")
            page, issues = _snapshot_with_issues(session, settings, expected_state, before_url=before_url)
            if ok and not issues:
                return page, issues

    return page, issues


def _resolve_locator_with_recovery(
    session: BrowserSession,
    args: Dict[str, Any],
    action_kind: str,
    settings: Dict[str, Any],
    recovery: Dict[str, Any],
    *,
    target_label: str,
):
    locator, locator_info, attempts = _resolve_locator(session.page, args, action_kind)
    if locator is not None:
        page = _safe_page_snapshot(session, max_text_chars=settings["max_text_chars"], max_elements=settings["max_elements"])
        return locator, locator_info, attempts, page

    page = _safe_page_snapshot(session, max_text_chars=settings["max_text_chars"], max_elements=settings["max_elements"])
    current_url = _safe_reopen_url(page.get("url", ""))
    all_attempts = list(attempts)

    for attempt_index in range(settings["max_retries"]):
        if settings["allow_reinspect"]:
            _record_recovery_attempt(recovery, f"Re-inspected the page to locate {target_label}.", fallback="reinspect")
            wait_ms = min(settings["timeout_ms"], 250 + ((attempt_index + 1) * 250))
            _wait_for_page_settle(session.page, settings["timeout_ms"], extra_wait_ms=wait_ms)
            locator, locator_info, retry_attempts = _resolve_locator(session.page, args, action_kind)
            all_attempts.extend([f"after re-inspect: {item}" for item in retry_attempts])
            page = _safe_page_snapshot(session, max_text_chars=settings["max_text_chars"], max_elements=settings["max_elements"])
            if locator is not None:
                return locator, locator_info, all_attempts, page

        if settings["allow_reload"] and current_url and _page_looks_empty(page):
            _record_recovery_attempt(recovery, f"Re-opened {current_url} to locate {target_label}.", fallback="reopen")
            ok, navigation_error = _navigate_page(session.page, current_url, settings["timeout_ms"])
            if not ok:
                _add_recovery_note(recovery, f"Re-open failed while locating {target_label}: {navigation_error}")
            locator, locator_info, retry_attempts = _resolve_locator(session.page, args, action_kind)
            all_attempts.extend([f"after reopen: {item}" for item in retry_attempts])
            page = _safe_page_snapshot(session, max_text_chars=settings["max_text_chars"], max_elements=settings["max_elements"])
            if locator is not None:
                return locator, locator_info, all_attempts, page

    return None, {}, all_attempts, page


def _try_link_navigation_fallback(
    session: BrowserSession,
    element: Dict[str, Any],
    settings: Dict[str, Any],
    recovery: Dict[str, Any],
    expected_state: Dict[str, Any],
    *,
    before_url: str,
):
    href = str(element.get("href", "")).strip()
    if not href or str(element.get("tag", "")).strip().lower() != "a":
        return _snapshot_with_issues(session, settings, expected_state, before_url=before_url)

    normalized_url, url_error = _normalize_url(href, base_url=before_url)
    if url_error:
        _add_recovery_note(recovery, f"Could not use direct link fallback: {url_error}")
        return _snapshot_with_issues(session, settings, expected_state, before_url=before_url)

    _record_recovery_attempt(recovery, f"Opened {normalized_url} directly after click recovery.", fallback="follow-link")
    ok, navigation_error = _navigate_page(session.page, normalized_url, settings["timeout_ms"])
    if not ok:
        _add_recovery_note(recovery, f"Direct link fallback failed: {navigation_error}")
    return _snapshot_with_issues(session, settings, expected_state, before_url=before_url)


def _page_mismatch_error(action_name: str, issues: List[str]) -> str:
    return f"{action_name} did not reach the expected browser state: {_issues_summary(issues)}"


def browser_open_page(args: Dict[str, Any]) -> Dict[str, Any]:
    settings = _browser_settings(args, include_headless=True)
    session_id = settings["session_id"]
    expected_state = _build_expected_state(args, default_target=str(args.get("url", "")).strip())
    recovery = _new_recovery_state(settings, reason="opening the page")

    session, error = _ensure_session(session_id, headless=settings["headless"])
    if error:
        return _browser_result(
            ok=False,
            action="browser_open_page",
            session=None,
            summary="Browser open failed.",
            page=None,
            expected_state=expected_state,
            recovery=recovery,
            error=error,
            session_id=session_id,
            last_action="Browser open failed.",
        )

    base_url = ""
    try:
        existing_url = str(session.page.url or "").strip()
        if existing_url and existing_url != "about:blank":
            base_url = existing_url
    except Exception:
        base_url = ""

    normalized_url, url_error = _normalize_url(str(args.get("url", "")).strip(), base_url=base_url)
    if url_error:
        return _browser_result(
            ok=False,
            action="browser_open_page",
            session=session,
            summary="Browser open failed.",
            page=None,
            expected_state=expected_state,
            recovery=recovery,
            error=url_error,
            session_id=session_id,
            last_action="Browser open failed.",
        )

    if _checkpoint_requested(args) and not _approval_granted(args):
        current_page = _safe_page_snapshot(session, max_text_chars=settings["max_text_chars"], max_elements=settings["max_elements"])
        checkpoint_step = str(args.get("workflow_step", "")).strip() or "open page"
        checkpoint_reason = _checkpoint_reason(
            args,
            default=f"Opening '{normalized_url}' was marked as approval-required.",
        )
        return _checkpoint_pause_result(
            action="browser_open_page",
            session=session,
            summary=f"Approval required before opening '{normalized_url}'.",
            page=current_page,
            expected_state=expected_state,
            recovery=recovery,
            args=args,
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=normalized_url,
            checkpoint_step=checkpoint_step,
            checkpoint_category="navigation",
            last_action=f"Approval required before opening '{normalized_url}'.",
            session_id=session_id,
            requested_url=normalized_url,
            attempted_url=normalized_url,
        )

    approval_status = "approved" if _checkpoint_requested(args) and _approval_granted(args) else "not needed"
    workflow_resumed = _checkpoint_resume_requested(args) and _approval_granted(args)

    ok, navigation_error = _navigate_page(session.page, normalized_url, settings["timeout_ms"])
    page = _safe_page_snapshot(session, max_text_chars=settings["max_text_chars"], max_elements=settings["max_elements"])
    if not ok:
        return _browser_result(
            ok=False,
            action="browser_open_page",
            session=session,
            summary="Browser open failed.",
            page=page,
            expected_state=expected_state,
            recovery=recovery,
            error=navigation_error,
            session_id=session_id,
            requested_url=normalized_url,
            attempted_url=normalized_url,
            approval_status=approval_status,
            workflow_resumed=workflow_resumed,
            last_action="Browser open failed.",
        )

    page, issues = _recover_page_state(session, settings, recovery, expected_state, reopen_url=normalized_url, reason="opening the page")
    if issues:
        return _browser_result(
            ok=False,
            action="browser_open_page",
            session=session,
            summary="Opened the page but did not reach the expected browser state.",
            page=page,
            expected_state=expected_state,
            recovery=recovery,
            expectation_issues=issues,
            error=_page_mismatch_error("browser_open_page", issues),
            session_id=session_id,
            requested_url=normalized_url,
            attempted_url=normalized_url,
            approval_status=approval_status,
            workflow_resumed=workflow_resumed,
            last_action="Opened the page but expected browser state was not reached.",
        )

    summary = f"Opened {_page_label(page, normalized_url)}"
    _record_history(session, summary, url=page.get("url", normalized_url))
    return _browser_result(
        ok=True,
        action="browser_open_page",
        session=session,
        summary=summary,
        page=page,
        expected_state=expected_state,
        recovery=recovery,
        session_id=session_id,
        requested_url=normalized_url,
        approval_status=approval_status,
        workflow_resumed=workflow_resumed,
        last_action=summary,
    )
def browser_inspect_page(args: Dict[str, Any]) -> Dict[str, Any]:
    settings = _browser_settings(args, include_headless=True)
    session_id = settings["session_id"]
    requested_url = str(args.get("url", "")).strip()
    expected_state = _build_expected_state(args, default_target=requested_url or "current page")
    recovery = _new_recovery_state(settings, reason="inspecting the page")

    if requested_url:
        session, error = _ensure_session(session_id, headless=settings["headless"])
        if error:
            return _browser_result(
                ok=False,
                action="browser_inspect_page",
                session=None,
                summary="Browser inspect failed.",
                page=None,
                expected_state=expected_state,
                recovery=recovery,
                error=error,
                session_id=session_id,
                last_action="Browser inspect failed.",
            )

        base_url = ""
        try:
            existing_url = str(session.page.url or "").strip()
            if existing_url and existing_url != "about:blank":
                base_url = existing_url
        except Exception:
            base_url = ""

        normalized_url, url_error = _normalize_url(requested_url, base_url=base_url)
        if url_error:
            return _browser_result(
                ok=False,
                action="browser_inspect_page",
                session=session,
                summary="Browser inspect failed.",
                page=None,
                expected_state=expected_state,
                recovery=recovery,
                error=url_error,
                session_id=session_id,
                last_action="Browser inspect failed.",
            )

        if existing_url and existing_url == normalized_url:
            _wait_for_page_settle(session.page, settings["timeout_ms"])
        else:
            ok, navigation_error = _navigate_page(session.page, normalized_url, settings["timeout_ms"])
            page = _safe_page_snapshot(session, max_text_chars=settings["max_text_chars"], max_elements=settings["max_elements"])
            if not ok:
                return _browser_result(
                    ok=False,
                    action="browser_inspect_page",
                    session=session,
                    summary="Browser inspect failed.",
                    page=page,
                    expected_state=expected_state,
                    recovery=recovery,
                    error=navigation_error,
                    session_id=session_id,
                    attempted_url=normalized_url,
                    last_action="Browser inspect failed.",
                )
        reopen_url = normalized_url
    else:
        session, error = _get_existing_session(session_id)
        if error:
            return _browser_result(
                ok=False,
                action="browser_inspect_page",
                session=None,
                summary=error,
                page=None,
                expected_state=expected_state,
                recovery=recovery,
                error=error,
                session_id=session_id,
                last_action="Browser inspect failed.",
            )
        reopen_url = str(session.page.url)

    page, issues = _recover_page_state(session, settings, recovery, expected_state, reopen_url=reopen_url, reason="inspecting the page")
    if issues:
        return _browser_result(
            ok=False,
            action="browser_inspect_page",
            session=session,
            summary="Inspected the page but did not reach the expected browser state.",
            page=page,
            expected_state=expected_state,
            recovery=recovery,
            expectation_issues=issues,
            error=_page_mismatch_error("browser_inspect_page", issues),
            session_id=session_id,
            attempted_url=reopen_url,
            last_action="Browser inspect did not reach the expected state.",
        )

    summary = f"Inspected {_page_label(page)}"
    _record_history(session, summary, url=page.get("url", ""))
    return _browser_result(
        ok=True,
        action="browser_inspect_page",
        session=session,
        summary=summary,
        page=page,
        expected_state=expected_state,
        recovery=recovery,
        session_id=session_id,
        last_action=summary,
    )


def browser_click(args: Dict[str, Any]) -> Dict[str, Any]:
    settings = _browser_settings(args)
    session_id = settings["session_id"]
    target_label = _selector_summary(args)
    expected_state = _build_expected_state(args, default_target=target_label)
    recovery = _new_recovery_state(settings, reason=f"clicking {target_label}")

    session, error = _get_existing_session(session_id)
    if error and _checkpoint_resume_requested(args):
        recovered_session, recovered_error = _recover_checkpoint_session(
            args,
            settings,
            recovery,
            original_error=error,
        )
        if recovered_session is not None:
            session = recovered_session
            error = ""
        elif recovered_error:
            error = recovered_error
    if error:
        return _browser_result(
            ok=False,
            action="browser_click",
            session=None,
            summary=error,
            page=None,
            expected_state=expected_state,
            recovery=recovery,
            error=error,
            session_id=session_id,
            last_action=f"Browser click failed for {target_label}.",
        )

    locator, locator_info, attempts, page = _resolve_locator_with_recovery(session, args, "click", settings, recovery, target_label=target_label)
    if locator is None:
        fallback_args, fallback_note = _button_locator_fallback_args(args, page)
        if fallback_args:
            _record_recovery_attempt(recovery, fallback_note, fallback="button_locator")
            combined_args = dict(args)
            combined_args.update(fallback_args)
            locator, locator_info, retry_attempts = _resolve_locator(session.page, combined_args, "click")
            attempts.extend([f"after visible button fallback: {item}" for item in retry_attempts])
            page = _safe_page_snapshot(session, max_text_chars=settings["max_text_chars"], max_elements=settings["max_elements"])
            if locator is not None:
                args = combined_args
                target_label = _selector_summary(args)

    if locator is None:
        return _browser_result(
            ok=False,
            action="browser_click",
            session=session,
            summary=f"Could not find a clickable element for {target_label}.",
            page=page,
            expected_state=expected_state,
            recovery=recovery,
            error=f"Could not find a clickable element for {target_label}.",
            session_id=session_id,
            locator_attempts=attempts,
            locator_info=locator_info,
            last_action=f"Browser click failed for {target_label}.",
        )

    element = _element_details(locator)
    target = element.get("text") or element.get("selector_hint") or target_label
    risky_click = _is_risky_click(element)
    checkpoint_required = _checkpoint_requested(args) or risky_click
    checkpoint_reason = _checkpoint_reason(
        args,
        default=(
            f"Clicking '{target}' looks submit-like and may change remote state."
            if risky_click
            else f"Clicking '{target}' was marked as approval-required."
        ),
    )
    if checkpoint_required and not _approval_granted(args):
        checkpoint_step = str(args.get("workflow_step", "")).strip() or "click element"
        return _checkpoint_pause_result(
            action="browser_click",
            session=session,
            summary=f"Approval required before clicking '{target}'.",
            page=page,
            expected_state=expected_state,
            recovery=recovery,
            args=args,
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=target,
            checkpoint_step=checkpoint_step,
            checkpoint_category="risky_click" if risky_click else "manual",
            risky_action=risky_click,
            last_action=f"Approval required before clicking '{target}'.",
            session_id=session_id,
            element=element,
            locator_attempts=attempts,
            locator_info=locator_info,
        )

    approval_status = "approved" if checkpoint_required and _approval_granted(args) else "not needed"
    workflow_resumed = _checkpoint_resume_requested(args) and _approval_granted(args)

    before_url = str(session.page.url)
    try:
        locator.click(timeout=settings["timeout_ms"])
    except _PLAYWRIGHT_TIMEOUT_ERROR:
        page = _safe_page_snapshot(session, max_text_chars=settings["max_text_chars"], max_elements=settings["max_elements"])
        return _browser_result(
            ok=False,
            action="browser_click",
            session=session,
            summary="Timed out while clicking the target element.",
            page=page,
            expected_state=expected_state,
            recovery=recovery,
            error="Timed out while clicking the target element.",
            session_id=session_id,
            element=element,
            locator_attempts=attempts,
            locator_info=locator_info,
            approval_status=approval_status,
            workflow_resumed=workflow_resumed,
            last_action=f"Browser click timed out for {target_label}.",
        )
    except Exception as exc:
        page = _safe_page_snapshot(session, max_text_chars=settings["max_text_chars"], max_elements=settings["max_elements"])
        return _browser_result(
            ok=False,
            action="browser_click",
            session=session,
            summary="Could not click the target element.",
            page=page,
            expected_state=expected_state,
            recovery=recovery,
            error=f"Could not click the target element: {exc}",
            session_id=session_id,
            element=element,
            locator_attempts=attempts,
            locator_info=locator_info,
            approval_status=approval_status,
            workflow_resumed=workflow_resumed,
            last_action=f"Browser click failed for {target_label}.",
        )

    page, issues = _recover_page_state(
        session,
        settings,
        recovery,
        expected_state,
        before_url=before_url,
        reopen_url=str(session.page.url),
        reason=f"checking the result of clicking {target_label}",
    )
    if issues and (expected_state.get("expect_navigation") or expected_state.get("url_contains")):
        page, issues = _try_link_navigation_fallback(session, element, settings, recovery, expected_state, before_url=before_url)

    if issues:
        return _browser_result(
            ok=False,
            action="browser_click",
            session=session,
            summary=f"Clicked '{target}' but did not reach the expected browser state.",
            page=page,
            expected_state=expected_state,
            recovery=recovery,
            expectation_issues=issues,
            error=_page_mismatch_error("browser_click", issues),
            session_id=session_id,
            element=element,
            locator_attempts=attempts,
            locator_info=locator_info,
            approval_status=approval_status,
            workflow_resumed=workflow_resumed,
            last_action=f"Clicked '{target}' but expected browser state was not reached.",
        )

    after_url = str(page.get("url", "")).strip()
    if after_url and after_url != before_url:
        summary = f"Clicked '{target}' and navigated to {_page_label(page)}"
    else:
        summary = f"Clicked '{target}' on {_page_label(page, before_url)}"
    _record_history(session, summary, url=after_url or before_url)
    return _browser_result(
        ok=True,
        action="browser_click",
        session=session,
        summary=summary,
        page=page,
        expected_state=expected_state,
        recovery=recovery,
        session_id=session_id,
        element=element,
        locator_attempts=attempts,
        locator_info=locator_info,
        approval_status=approval_status,
        workflow_resumed=workflow_resumed,
        last_action=summary,
    )
def browser_type(args: Dict[str, Any]) -> Dict[str, Any]:
    settings = _browser_settings(args)
    session_id = settings["session_id"]
    target_label = _selector_summary(args)
    expected_state = _build_expected_state(args, default_target=target_label)
    recovery = _new_recovery_state(settings, reason=f"typing into {target_label}")
    value = str(args.get("value", ""))

    session, error = _get_existing_session(session_id)
    if error:
        return _browser_result(
            ok=False,
            action="browser_type",
            session=None,
            summary=error,
            page=None,
            expected_state=expected_state,
            recovery=recovery,
            error=error,
            session_id=session_id,
            last_action=f"Browser type failed for {target_label}.",
        )

    locator, locator_info, attempts, page = _resolve_locator_with_recovery(session, args, "type", settings, recovery, target_label=target_label)
    if locator is None:
        return _browser_result(
            ok=False,
            action="browser_type",
            session=session,
            summary=f"Could not find an input field for {target_label}.",
            page=page,
            expected_state=expected_state,
            recovery=recovery,
            error=f"Could not find an input field for {target_label}.",
            session_id=session_id,
            locator_attempts=attempts,
            locator_info=locator_info,
            last_action=f"Browser type failed for {target_label}.",
        )

    field = _element_details(locator)
    try:
        locator.fill(value, timeout=settings["timeout_ms"])
    except _PLAYWRIGHT_TIMEOUT_ERROR:
        page = _safe_page_snapshot(session, max_text_chars=settings["max_text_chars"], max_elements=settings["max_elements"])
        return _browser_result(
            ok=False,
            action="browser_type",
            session=session,
            summary="Timed out while typing into the target field.",
            page=page,
            expected_state=expected_state,
            recovery=recovery,
            error="Timed out while typing into the target field.",
            session_id=session_id,
            field=field,
            locator_attempts=attempts,
            locator_info=locator_info,
            last_action=f"Browser type timed out for {target_label}.",
        )
    except Exception as exc:
        page = _safe_page_snapshot(session, max_text_chars=settings["max_text_chars"], max_elements=settings["max_elements"])
        return _browser_result(
            ok=False,
            action="browser_type",
            session=session,
            summary="Could not type into the target field.",
            page=page,
            expected_state=expected_state,
            recovery=recovery,
            error=f"Could not type into the target field: {exc}",
            session_id=session_id,
            field=field,
            locator_attempts=attempts,
            locator_info=locator_info,
            last_action=f"Browser type failed for {target_label}.",
        )

    page, issues = _recover_page_state(session, settings, recovery, expected_state, reopen_url=str(session.page.url), reason=f"checking the result of typing into {target_label}")
    if issues:
        return _browser_result(
            ok=False,
            action="browser_type",
            session=session,
            summary=f"Typed into '{field.get('name') or field.get('placeholder') or target_label}' but did not reach the expected browser state.",
            page=page,
            expected_state=expected_state,
            recovery=recovery,
            expectation_issues=issues,
            error=_page_mismatch_error("browser_type", issues),
            session_id=session_id,
            field=field,
            locator_attempts=attempts,
            locator_info=locator_info,
            typed_preview=_mask_typed_value(value, field.get("type", "")),
            value_length=len(value),
            last_action=f"Typed into {target_label} but expected browser state was not reached.",
        )

    typed_preview = _mask_typed_value(value, field.get("type", ""))
    target = field.get("name") or field.get("placeholder") or field.get("selector_hint") or "input"
    summary = f"Typed into '{target}' on {_page_label(page)}"
    _record_history(session, summary, url=page.get("url", ""))
    return _browser_result(
        ok=True,
        action="browser_type",
        session=session,
        summary=summary,
        page=page,
        expected_state=expected_state,
        recovery=recovery,
        session_id=session_id,
        field=field,
        locator_attempts=attempts,
        locator_info=locator_info,
        typed_preview=typed_preview,
        value_length=len(value),
        last_action=summary,
    )


def browser_extract_text(args: Dict[str, Any]) -> Dict[str, Any]:
    settings = _browser_settings(args)
    session_id = settings["session_id"]
    wants_target = any(str(args.get(key, "")).strip() for key in ("selector", "text", "label", "placeholder", "role", "name", "name_attr"))
    target_label = _selector_summary(args) if wants_target else "current page text"
    expected_state = _build_expected_state(args, default_target=target_label)
    recovery = _new_recovery_state(settings, reason=f"extracting text from {target_label}")

    session, error = _get_existing_session(session_id)
    if error:
        return _browser_result(
            ok=False,
            action="browser_extract_text",
            session=None,
            summary=error,
            page=None,
            expected_state=expected_state,
            recovery=recovery,
            error=error,
            session_id=session_id,
            last_action=f"Browser text extraction failed for {target_label}.",
        )

    locator_info: Dict[str, Any] = {}
    attempts: List[str] = []
    locator = None
    if wants_target:
        locator, locator_info, attempts, page = _resolve_locator_with_recovery(session, args, "extract", settings, recovery, target_label=target_label)
        if locator is None:
            return _browser_result(
                ok=False,
                action="browser_extract_text",
                session=session,
                summary=f"Could not find a target element for {target_label}.",
                page=page,
                expected_state=expected_state,
                recovery=recovery,
                error=f"Could not find a target element for {target_label}.",
                session_id=session_id,
                locator_attempts=attempts,
                locator_info=locator_info,
                last_action=f"Browser text extraction failed for {target_label}.",
            )

    source = "element" if wants_target else "page"
    current_url = str(session.page.url)
    extracted_text = ""
    raw_text = ""
    page: Dict[str, Any] = {}
    issues: List[str] = []

    for attempt_index in range(settings["max_retries"] + 1):
        if wants_target:
            if attempt_index > 0:
                locator, locator_info, retry_attempts = _resolve_locator(session.page, args, "extract")
                attempts.extend([f"re-extract: {item}" for item in retry_attempts])
                if locator is None:
                    issues = [f"Could not find a target element for {target_label}."]
                    page = _safe_page_snapshot(session, max_text_chars=settings["max_text_chars"], max_elements=settings["max_elements"])
                    break
            raw_text = _extract_text_from_locator(locator, settings["timeout_ms"])
        else:
            raw_text = _extract_text_from_locator(session.page.locator("body"), settings["timeout_ms"])

        extracted_text = _normalize_visible_text(raw_text, settings["max_text_chars"])
        page = _safe_page_snapshot(session, max_text_chars=settings["max_text_chars"], max_elements=settings["max_elements"])
        issues = _page_expectation_issues(page, {**expected_state, "text_contains": ""})
        if not extracted_text:
            issues.append("extracted text was empty")
        expected_text = str(expected_state.get("text_contains", "")).strip().lower()
        if expected_text and expected_text not in extracted_text.lower():
            issues.append(f"extracted text did not match '{expected_state['text_contains']}'")
        if not issues:
            break
        if attempt_index >= settings["max_retries"]:
            break

        if settings["allow_reinspect"]:
            _record_recovery_attempt(recovery, f"Re-inspected the page while extracting text from {target_label}.", fallback="reinspect")
            _wait_for_page_settle(session.page, settings["timeout_ms"])
            continue

        if settings["allow_reload"]:
            safe_url = _safe_reopen_url(current_url)
            if safe_url:
                _record_recovery_attempt(recovery, f"Re-opened {safe_url} while extracting text from {target_label}.", fallback="reopen")
                ok, navigation_error = _navigate_page(session.page, safe_url, settings["timeout_ms"])
                if not ok:
                    _add_recovery_note(recovery, f"Re-open failed during text extraction: {navigation_error}")
                continue
        break

    if issues:
        return _browser_result(
            ok=False,
            action="browser_extract_text",
            session=session,
            summary=f"Could not extract the expected text from {target_label}.",
            page=page,
            expected_state=expected_state,
            recovery=recovery,
            expectation_issues=issues,
            error=_page_mismatch_error("browser_extract_text", issues),
            session_id=session_id,
            source=source,
            text=extracted_text,
            truncated=len(str(raw_text or "")) > len(extracted_text),
            locator_attempts=attempts,
            locator_info=locator_info,
            last_action=f"Browser text extraction did not reach the expected state for {target_label}.",
        )

    summary = f"Extracted visible text from {_page_label(page)}"
    _record_history(session, summary, url=page.get("url", ""))
    return _browser_result(
        ok=True,
        action="browser_extract_text",
        session=session,
        summary=summary,
        page=page,
        expected_state=expected_state,
        recovery=recovery,
        session_id=session_id,
        source=source,
        text=extracted_text,
        truncated=len(str(raw_text or "")) > len(extracted_text),
        locator_attempts=attempts,
        locator_info=locator_info,
        last_action=summary,
    )


def browser_follow_link(args: Dict[str, Any]) -> Dict[str, Any]:
    settings = _browser_settings(args)
    session_id = settings["session_id"]
    target_label = _selector_summary(args)
    expected_state = _build_expected_state(args, default_target=target_label)
    if "expect_navigation" not in args:
        expected_state["expect_navigation"] = True
    recovery = _new_recovery_state(settings, reason=f"following {target_label}")

    session, error = _get_existing_session(session_id)
    if error:
        return _browser_result(
            ok=False,
            action="browser_follow_link",
            session=None,
            summary=error,
            page=None,
            expected_state=expected_state,
            recovery=recovery,
            error=error,
            session_id=session_id,
            last_action=f"Browser follow link failed for {target_label}.",
        )

    locator, locator_info, attempts, page = _resolve_locator_with_recovery(session, args, "follow_link", settings, recovery, target_label=target_label)
    if locator is None:
        return _browser_result(
            ok=False,
            action="browser_follow_link",
            session=session,
            summary=f"Could not find a link for {target_label}.",
            page=page,
            expected_state=expected_state,
            recovery=recovery,
            error=f"Could not find a link for {target_label}.",
            session_id=session_id,
            locator_attempts=attempts,
            locator_info=locator_info,
            last_action=f"Browser follow link failed for {target_label}.",
        )

    element = _element_details(locator)
    href = element.get("href", "") or _extract_link_href(locator)
    if not href:
        return _browser_result(
            ok=False,
            action="browser_follow_link",
            session=session,
            summary="The matched element does not expose a followable link target.",
            page=page,
            expected_state=expected_state,
            recovery=recovery,
            error="The matched element does not expose a followable link target.",
            session_id=session_id,
            element=element,
            locator_attempts=attempts,
            locator_info=locator_info,
            last_action=f"Browser follow link failed for {target_label}.",
        )

    before_url = str(session.page.url)
    normalized_url, url_error = _normalize_url(href, base_url=before_url)
    if url_error:
        return _browser_result(
            ok=False,
            action="browser_follow_link",
            session=session,
            summary="Could not follow the link.",
            page=page,
            expected_state=expected_state,
            recovery=recovery,
            error=url_error,
            session_id=session_id,
            element=element,
            locator_attempts=attempts,
            locator_info=locator_info,
            last_action=f"Browser follow link failed for {target_label}.",
        )

    target = element.get("text") or element.get("selector_hint") or "link"
    risky_navigation = _is_risky_navigation(target, normalized_url, expected_state)
    checkpoint_required = _checkpoint_requested(args) or risky_navigation
    checkpoint_reason = _checkpoint_reason(
        args,
        default=(
            f"Following '{target}' may change account or session state."
            if risky_navigation
            else f"Following '{target}' was marked as approval-required."
        ),
    )
    if checkpoint_required and not _approval_granted(args):
        checkpoint_step = str(args.get("workflow_step", "")).strip() or "follow link"
        return _checkpoint_pause_result(
            action="browser_follow_link",
            session=session,
            summary=f"Approval required before following '{target}'.",
            page=page,
            expected_state=expected_state,
            recovery=recovery,
            args=args,
            checkpoint_reason=checkpoint_reason,
            checkpoint_target=normalized_url,
            checkpoint_step=checkpoint_step,
            checkpoint_category="risky_navigation" if risky_navigation else "manual",
            risky_action=risky_navigation,
            last_action=f"Approval required before following '{target}'.",
            session_id=session_id,
            followed_url=normalized_url,
            element=element,
            locator_attempts=attempts,
            locator_info=locator_info,
        )

    approval_status = "approved" if checkpoint_required and _approval_granted(args) else "not needed"
    workflow_resumed = _checkpoint_resume_requested(args) and _approval_granted(args)

    ok, navigation_error = _navigate_page(session.page, normalized_url, settings["timeout_ms"])
    page = _safe_page_snapshot(session, max_text_chars=settings["max_text_chars"], max_elements=settings["max_elements"])
    if not ok:
        return _browser_result(
            ok=False,
            action="browser_follow_link",
            session=session,
            summary="Could not follow the link.",
            page=page,
            expected_state=expected_state,
            recovery=recovery,
            error=navigation_error,
            session_id=session_id,
            followed_url=normalized_url,
            element=element,
            locator_attempts=attempts,
            locator_info=locator_info,
            approval_status=approval_status,
            workflow_resumed=workflow_resumed,
            last_action=f"Browser follow link failed for {target_label}.",
        )

    page, issues = _recover_page_state(session, settings, recovery, expected_state, before_url=before_url, reopen_url=normalized_url, reason=f"checking the result of following {target_label}")
    if issues:
        return _browser_result(
            ok=False,
            action="browser_follow_link",
            session=session,
            summary=f"Followed '{target}' but did not reach the expected browser state.",
            page=page,
            expected_state=expected_state,
            recovery=recovery,
            expectation_issues=issues,
            error=_page_mismatch_error("browser_follow_link", issues),
            session_id=session_id,
            followed_url=normalized_url,
            element=element,
            locator_attempts=attempts,
            locator_info=locator_info,
            approval_status=approval_status,
            workflow_resumed=workflow_resumed,
            last_action=f"Followed '{target}' but expected browser state was not reached.",
        )

    summary = f"Followed '{target}' to {_page_label(page, normalized_url)}"
    _record_history(session, summary, url=page.get("url", normalized_url))
    return _browser_result(
        ok=True,
        action="browser_follow_link",
        session=session,
        summary=summary,
        page=page,
        expected_state=expected_state,
        recovery=recovery,
        session_id=session_id,
        followed_url=normalized_url,
        element=element,
        locator_attempts=attempts,
        locator_info=locator_info,
        approval_status=approval_status,
        workflow_resumed=workflow_resumed,
        last_action=summary,
    )
BROWSER_OPEN_PAGE_TOOL = {
    "name": "browser_open_page",
    "description": (
        "Open a page in a bounded browser-only session, capture a compact page snapshot, "
        "use limited automatic recovery when the page stays empty or misses the expected state, "
        "and support explicit approval checkpoints before important navigation."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "url": {"type": "string"},
            "headless": {"type": "boolean"},
            "approval_status": {"type": "string"},
            "checkpoint_required": {"type": "boolean"},
            "checkpoint_reason": {"type": "string"},
            "timeout_ms": {"type": "integer"},
            "max_text_chars": {"type": "integer"},
            "max_elements": {"type": "integer"},
            "expected_target": {"type": "string"},
            "expected_url_contains": {"type": "string"},
            "expected_title_contains": {"type": "string"},
            "expected_text_contains": {"type": "string"},
            "expect_navigation": {"type": "boolean"},
            "max_retries": {"type": "integer"},
            "allow_reinspect": {"type": "boolean"},
            "allow_reload": {"type": "boolean"},
            "workflow_name": {"type": "string"},
            "workflow_pattern": {"type": "string"},
            "workflow_step": {"type": "string"},
            "workflow_next_step": {"type": "string"},
            "browser_task_name": {"type": "string"},
            "browser_task_step": {"type": "string"},
            "browser_task_next_step": {"type": "string"}
        },
        "required": ["url"]
    },
    "func": browser_open_page,
}
BROWSER_INSPECT_PAGE_TOOL = {
    "name": "browser_inspect_page",
    "description": (
        "Inspect the current browser page, or optionally open a URL first, and return a compact snapshot "
        "with visible text, links, buttons, and inputs plus bounded recovery for stale or empty results."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "url": {"type": "string"},
            "headless": {"type": "boolean"},
            "timeout_ms": {"type": "integer"},
            "max_text_chars": {"type": "integer"},
            "max_elements": {"type": "integer"},
            "expected_target": {"type": "string"},
            "expected_url_contains": {"type": "string"},
            "expected_title_contains": {"type": "string"},
            "expected_text_contains": {"type": "string"},
            "expect_navigation": {"type": "boolean"},
            "max_retries": {"type": "integer"},
            "allow_reinspect": {"type": "boolean"},
            "allow_reload": {"type": "boolean"},
            "workflow_name": {"type": "string"},
            "workflow_pattern": {"type": "string"},
            "workflow_step": {"type": "string"},
            "workflow_next_step": {"type": "string"},
            "browser_task_name": {"type": "string"},
            "browser_task_step": {"type": "string"},
            "browser_task_next_step": {"type": "string"}
        },
        "required": []
    },
    "func": browser_inspect_page,
}


BROWSER_CLICK_TOOL = {
    "name": "browser_click",
    "description": (
        "Click a visible browser element using a bounded locator. "
        "Requires explicit approval_status=approved for submit-like or other checkpointed actions and uses bounded recovery for missing elements or unmet expected state."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "selector": {"type": "string"},
            "text": {"type": "string"},
            "label": {"type": "string"},
            "placeholder": {"type": "string"},
            "role": {"type": "string"},
            "name": {"type": "string"},
            "name_attr": {"type": "string"},
            "index": {"type": "integer"},
            "exact": {"type": "boolean"},
            "approval_status": {"type": "string"},
            "checkpoint_required": {"type": "boolean"},
            "checkpoint_reason": {"type": "string"},
            "timeout_ms": {"type": "integer"},
            "max_text_chars": {"type": "integer"},
            "max_elements": {"type": "integer"},
            "expected_target": {"type": "string"},
            "expected_url_contains": {"type": "string"},
            "expected_title_contains": {"type": "string"},
            "expected_text_contains": {"type": "string"},
            "expect_navigation": {"type": "boolean"},
            "max_retries": {"type": "integer"},
            "allow_reinspect": {"type": "boolean"},
            "allow_reload": {"type": "boolean"},
            "workflow_name": {"type": "string"},
            "workflow_pattern": {"type": "string"},
            "workflow_step": {"type": "string"},
            "workflow_next_step": {"type": "string"},
            "browser_task_name": {"type": "string"},
            "browser_task_step": {"type": "string"},
            "browser_task_next_step": {"type": "string"}
        },
        "required": []
    },
    "func": browser_click,
}
BROWSER_TYPE_TOOL = {
    "name": "browser_type",
    "description": (
        "Type text into a visible browser input field without submitting the form, "
        "with bounded recovery for delayed inputs or stale page state."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "selector": {"type": "string"},
            "text": {"type": "string"},
            "label": {"type": "string"},
            "placeholder": {"type": "string"},
            "role": {"type": "string"},
            "name": {"type": "string"},
            "name_attr": {"type": "string"},
            "index": {"type": "integer"},
            "exact": {"type": "boolean"},
            "value": {"type": "string"},
            "timeout_ms": {"type": "integer"},
            "max_text_chars": {"type": "integer"},
            "max_elements": {"type": "integer"},
            "expected_target": {"type": "string"},
            "expected_url_contains": {"type": "string"},
            "expected_title_contains": {"type": "string"},
            "expected_text_contains": {"type": "string"},
            "expect_navigation": {"type": "boolean"},
            "max_retries": {"type": "integer"},
            "allow_reinspect": {"type": "boolean"},
            "allow_reload": {"type": "boolean"},
            "workflow_name": {"type": "string"},
            "workflow_pattern": {"type": "string"},
            "workflow_step": {"type": "string"},
            "workflow_next_step": {"type": "string"},
            "browser_task_name": {"type": "string"},
            "browser_task_step": {"type": "string"},
            "browser_task_next_step": {"type": "string"}
        },
        "required": ["value"]
    },
    "func": browser_type,
}


BROWSER_EXTRACT_TEXT_TOOL = {
    "name": "browser_extract_text",
    "description": (
        "Extract a bounded amount of visible text from the current browser page or a targeted visible element, "
        "with bounded recovery for stale or empty text results."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "selector": {"type": "string"},
            "text": {"type": "string"},
            "label": {"type": "string"},
            "placeholder": {"type": "string"},
            "role": {"type": "string"},
            "name": {"type": "string"},
            "name_attr": {"type": "string"},
            "index": {"type": "integer"},
            "exact": {"type": "boolean"},
            "timeout_ms": {"type": "integer"},
            "max_text_chars": {"type": "integer"},
            "max_elements": {"type": "integer"},
            "expected_target": {"type": "string"},
            "expected_url_contains": {"type": "string"},
            "expected_title_contains": {"type": "string"},
            "expected_text_contains": {"type": "string"},
            "expect_navigation": {"type": "boolean"},
            "max_retries": {"type": "integer"},
            "allow_reinspect": {"type": "boolean"},
            "allow_reload": {"type": "boolean"},
            "workflow_name": {"type": "string"},
            "workflow_pattern": {"type": "string"},
            "workflow_step": {"type": "string"},
            "workflow_next_step": {"type": "string"},
            "browser_task_name": {"type": "string"},
            "browser_task_step": {"type": "string"},
            "browser_task_next_step": {"type": "string"}
        },
        "required": []
    },
    "func": browser_extract_text,
}


BROWSER_FOLLOW_LINK_TOOL = {
    "name": "browser_follow_link",
    "description": (
        "Follow a visible link in the current browser page using a bounded locator and safe URL validation, "
        "with bounded recovery for delayed links or unmet navigation state, and require explicit approval_status=approved for risky or checkpointed transitions."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "selector": {"type": "string"},
            "text": {"type": "string"},
            "label": {"type": "string"},
            "role": {"type": "string"},
            "name": {"type": "string"},
            "name_attr": {"type": "string"},
            "index": {"type": "integer"},
            "exact": {"type": "boolean"},
            "approval_status": {"type": "string"},
            "checkpoint_required": {"type": "boolean"},
            "checkpoint_reason": {"type": "string"},
            "timeout_ms": {"type": "integer"},
            "max_text_chars": {"type": "integer"},
            "max_elements": {"type": "integer"},
            "expected_target": {"type": "string"},
            "expected_url_contains": {"type": "string"},
            "expected_title_contains": {"type": "string"},
            "expected_text_contains": {"type": "string"},
            "expect_navigation": {"type": "boolean"},
            "max_retries": {"type": "integer"},
            "allow_reinspect": {"type": "boolean"},
            "allow_reload": {"type": "boolean"},
            "workflow_name": {"type": "string"},
            "workflow_pattern": {"type": "string"},
            "workflow_step": {"type": "string"},
            "workflow_next_step": {"type": "string"},
            "browser_task_name": {"type": "string"},
            "browser_task_step": {"type": "string"},
            "browser_task_next_step": {"type": "string"}
        },
        "required": []
    },
    "func": browser_follow_link,
}


