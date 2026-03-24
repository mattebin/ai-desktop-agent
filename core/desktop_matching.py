from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List

try:
    from rapidfuzz import fuzz
except Exception:
    fuzz = None  # type: ignore[assignment]


WINDOW_MATCH_THRESHOLD = 74
WINDOW_STRONG_MATCH_THRESHOLD = 88
WINDOW_CLEAR_GAP = 7


def _trim_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _fuzzy_ratio(left: str, right: str) -> int:
    if not left or not right:
        return 0
    if fuzz is not None:
        try:
            return int(round(float(fuzz.WRatio(left, right))))
        except Exception:
            pass
    return int(round(SequenceMatcher(None, left, right).ratio() * 100))


def _title_match_details(expected: str, actual: str, *, exact: bool = False) -> Dict[str, Any]:
    expected_text = _normalize_text(expected)
    actual_text = _normalize_text(actual)
    engine = "rapidfuzz" if fuzz is not None else "builtin"

    if not expected_text or not actual_text:
        return {
            "matched": False,
            "score": 0,
            "kind": "missing",
            "engine": engine,
            "summary": "Expected or actual title text was missing.",
        }

    if expected_text == actual_text:
        return {
            "matched": True,
            "score": 100,
            "kind": "exact",
            "engine": engine,
            "summary": "Window title matched exactly.",
        }

    if exact:
        return {
            "matched": False,
            "score": 0,
            "kind": "exact_miss",
            "engine": engine,
            "summary": "Exact title matching was requested and the observed title differed.",
        }

    if expected_text in actual_text:
        return {
            "matched": True,
            "score": 96,
            "kind": "contains",
            "engine": engine,
            "summary": "Observed window title contained the requested title.",
        }

    if actual_text in expected_text:
        return {
            "matched": True,
            "score": 92,
            "kind": "contained_by",
            "engine": engine,
            "summary": "Requested title contained the observed window title.",
        }

    score = _fuzzy_ratio(expected_text, actual_text)
    return {
        "matched": score >= WINDOW_MATCH_THRESHOLD,
        "score": score,
        "kind": "fuzzy",
        "engine": engine,
        "summary": "Used bounded fuzzy matching to handle title drift.",
    }


def titles_compatible(expected: str, actual: str, *, exact: bool = False, threshold: int = WINDOW_MATCH_THRESHOLD) -> bool:
    details = _title_match_details(expected, actual, exact=exact)
    if exact:
        return bool(details.get("matched", False))
    return int(details.get("score", 0) or 0) >= max(0, int(threshold or WINDOW_MATCH_THRESHOLD))


def describe_title_match(expected: str, actual: str, *, exact: bool = False) -> Dict[str, Any]:
    return dict(_title_match_details(expected, actual, exact=exact))


def score_window_candidate(
    candidate: Dict[str, Any] | None,
    *,
    requested_title: str = "",
    requested_window_id: str = "",
    expected_process_name: str = "",
    expected_class_name: str = "",
    exact: bool = False,
) -> Dict[str, Any]:
    item = candidate if isinstance(candidate, dict) else {}
    requested_id = _trim_text(requested_window_id, limit=40).lower()
    candidate_id = _trim_text(item.get("window_id", ""), limit=40).lower()
    title = _trim_text(item.get("title", ""), limit=180)
    process_name = _trim_text(item.get("process_name", ""), limit=120)
    class_name = _trim_text(item.get("class_name", ""), limit=120)
    title_match = _title_match_details(requested_title, title, exact=exact)

    process_score = 0
    if expected_process_name:
        process_score = _title_match_details(expected_process_name, process_name, exact=False).get("score", 0)

    class_score = 0
    if expected_class_name:
        class_score = _title_match_details(expected_class_name, class_name, exact=False).get("score", 0)

    id_match = bool(requested_id and candidate_id and requested_id == candidate_id)
    score = int(title_match.get("score", 0) or 0)
    score += int(round(process_score * 0.15))
    score += int(round(class_score * 0.1))
    if id_match:
        score += 25
    if bool(item.get("is_active", False)):
        score += 6
    if bool(item.get("is_visible", False)):
        score += 4
    if bool(item.get("is_minimized", False)):
        score -= 5
    if bool(item.get("is_cloaked", False)):
        score -= 8
    score = max(0, min(140, score))

    confidence = "none"
    if id_match or int(title_match.get("score", 0) or 0) >= 96:
        confidence = "high"
    elif score >= WINDOW_STRONG_MATCH_THRESHOLD:
        confidence = "high"
    elif score >= WINDOW_MATCH_THRESHOLD:
        confidence = "medium"
    elif score >= 60:
        confidence = "low"

    return {
        "window_id": _trim_text(item.get("window_id", ""), limit=40),
        "title": title,
        "process_name": process_name,
        "class_name": class_name,
        "score": score,
        "confidence": confidence,
        "matched": id_match or score >= (100 if exact else WINDOW_MATCH_THRESHOLD),
        "match_kind": "window_id" if id_match else title_match.get("kind", ""),
        "match_engine": title_match.get("engine", ""),
        "title_score": int(title_match.get("score", 0) or 0),
        "process_score": int(process_score or 0),
        "class_score": int(class_score or 0),
        "reason": _trim_text(title_match.get("summary", ""), limit=180),
        "candidate": item,
    }


def _preview_candidates(ranked: Iterable[Dict[str, Any]], *, limit: int = 4) -> List[Dict[str, Any]]:
    preview: List[Dict[str, Any]] = []
    for item in list(ranked or [])[: max(1, int(limit or 4))]:
        preview.append(
            {
                "window_id": _trim_text(item.get("window_id", ""), limit=40),
                "title": _trim_text(item.get("title", ""), limit=180),
                "process_name": _trim_text(item.get("process_name", ""), limit=120),
                "score": int(item.get("score", 0) or 0),
                "confidence": _trim_text(item.get("confidence", ""), limit=20),
                "match_kind": _trim_text(item.get("match_kind", ""), limit=40),
                "match_engine": _trim_text(item.get("match_engine", ""), limit=40),
                "reason": _trim_text(item.get("reason", ""), limit=180),
            }
        )
    return preview


def select_window_candidate(
    candidates: Iterable[Dict[str, Any]],
    *,
    requested_title: str = "",
    requested_window_id: str = "",
    expected_process_name: str = "",
    expected_class_name: str = "",
    exact: bool = False,
) -> Dict[str, Any]:
    ranked = [
        score_window_candidate(
            item,
            requested_title=requested_title,
            requested_window_id=requested_window_id,
            expected_process_name=expected_process_name,
            expected_class_name=expected_class_name,
            exact=exact,
        )
        for item in list(candidates or [])
        if isinstance(item, dict)
    ]
    ranked.sort(
        key=lambda item: (
            -int(item.get("score", 0) or 0),
            not bool(item.get("candidate", {}).get("is_active", False)),
            not bool(item.get("candidate", {}).get("is_visible", False)),
            bool(item.get("candidate", {}).get("is_minimized", False)),
            item.get("title", "").lower(),
        )
    )
    if not ranked:
        return {
            "selected": {},
            "ranked": [],
            "reason": "target_not_found",
            "summary": "No desktop window candidates were available for matching.",
            "confidence": "none",
        }

    top = ranked[0]
    score = int(top.get("score", 0) or 0)
    gap = score - int(ranked[1].get("score", 0) or 0) if len(ranked) > 1 else score
    strong_enough = score >= (100 if exact else WINDOW_MATCH_THRESHOLD)
    match_kind = _trim_text(top.get("match_kind", ""), limit=40)
    clear_winner = bool(
        strong_enough
        and (
            match_kind in {"window_id", "exact", "contains", "contained_by"}
            or (match_kind != "fuzzy" and score >= WINDOW_STRONG_MATCH_THRESHOLD)
            or (match_kind == "fuzzy" and len(ranked) == 1)
            or gap >= WINDOW_CLEAR_GAP
        )
    )
    reason = "matched" if clear_winner else ("candidate_ambiguous" if strong_enough else "no_match")
    summary = (
        f"Selected '{top.get('title', 'window')}' as the best window candidate with score {score}."
        if clear_winner
        else (
            f"Multiple similar windows were available for '{requested_title}'."
            if strong_enough
            else f"No strong window candidate matched '{requested_title}'."
        )
    )
    return {
        "selected": top.get("candidate", {}) if clear_winner else {},
        "ranked": ranked,
        "reason": reason,
        "summary": summary,
        "confidence": top.get("confidence", "none"),
        "top_score": score,
        "score_gap": gap,
        "match_engine": _trim_text(top.get("match_engine", ""), limit=40),
        "match_kind": _trim_text(top.get("match_kind", ""), limit=40),
        "candidate_preview": _preview_candidates(ranked),
    }
