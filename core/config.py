from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Dict

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
SETTINGS_PATH = CONFIG_DIR / "settings.yaml"
SETTINGS_LOCAL_PATH = CONFIG_DIR / "settings.local.yaml"
SECRETS_PATH = CONFIG_DIR / "secrets.yaml"
SETTINGS_LAYER_PATHS = (SETTINGS_PATH, SETTINGS_LOCAL_PATH, SECRETS_PATH)
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPERATOR_MODEL = "gpt-5.4"
DEFAULT_REASONING_EFFORT = "medium"
_SETTINGS_LOCK = RLock()
_SETTINGS_CACHE: Dict[str, Any] = {
    "cache_key": "",
    "loaded_at": "",
    "reload_count": 0,
    "settings": {},
    "settings_sources": [],
    "version": "",
}


def _read_yaml_dict(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}

    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}

    return loaded if isinstance(loaded, dict) else {}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _path_cache_part(path: Path) -> str:
    try:
        stat = path.stat()
        return f"{path}:{int(path.exists())}:{stat.st_mtime_ns}:{stat.st_size}"
    except FileNotFoundError:
        return f"{path}:0:0:0"
    except Exception:
        return f"{path}:1:0:0"


def _settings_cache_key() -> str:
    payload = "|".join(_path_cache_part(path) for path in SETTINGS_LAYER_PATHS).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:16]


def _reload_settings_snapshot_locked(*, cache_key: str) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    settings_sources: list[Path] = []
    for path in SETTINGS_LAYER_PATHS:
        if path.exists():
            settings_sources.append(path)
        merged = _deep_merge(merged, _read_yaml_dict(path))

    loaded_at = datetime.now().astimezone().isoformat(timespec="seconds")
    reload_count = int(_SETTINGS_CACHE.get("reload_count", 0) or 0) + 1
    version = hashlib.sha1(f"{cache_key}:{reload_count}:{loaded_at}".encode("utf-8")).hexdigest()[:16]
    _SETTINGS_CACHE.update(
        {
            "cache_key": cache_key,
            "loaded_at": loaded_at,
            "reload_count": reload_count,
            "settings": merged,
            "settings_sources": list(settings_sources),
            "version": version,
        }
    )
    return {
        "settings": dict(merged),
        "settings_sources": list(settings_sources),
        "loaded_at": loaded_at,
        "reload_count": reload_count,
        "version": version,
    }


def get_settings_snapshot(*, force: bool = False) -> Dict[str, Any]:
    cache_key = _settings_cache_key()
    with _SETTINGS_LOCK:
        if force or not _SETTINGS_CACHE.get("cache_key") or _SETTINGS_CACHE.get("cache_key") != cache_key:
            return _reload_settings_snapshot_locked(cache_key=cache_key)
        return {
            "settings": dict(_SETTINGS_CACHE.get("settings", {})),
            "settings_sources": list(_SETTINGS_CACHE.get("settings_sources", [])),
            "loaded_at": str(_SETTINGS_CACHE.get("loaded_at", "")).strip(),
            "reload_count": int(_SETTINGS_CACHE.get("reload_count", 0) or 0),
            "version": str(_SETTINGS_CACHE.get("version", "")).strip(),
        }


def get_settings_sources(*, force: bool = False) -> list[Path]:
    snapshot = get_settings_snapshot(force=force)
    return list(snapshot.get("settings_sources", []))


def load_settings(*, force: bool = False) -> Dict[str, Any]:
    snapshot = get_settings_snapshot(force=force)
    settings = snapshot.get("settings", {})
    return dict(settings) if isinstance(settings, dict) else {}


def _format_source_label(paths: list[Path]) -> str:
    if not paths:
        return "config/settings.yaml"
    return " + ".join(str(path.relative_to(PROJECT_ROOT)).replace("\\", "/") for path in paths)


def get_runtime_model_config(settings: Dict[str, Any] | None = None) -> Dict[str, Any]:
    snapshot = get_settings_snapshot()
    source_settings = settings if isinstance(settings, dict) else dict(snapshot.get("settings", {}))
    settings_sources = list(snapshot.get("settings_sources", []))
    reasoning = source_settings.get("reasoning") or {}

    active_model = str(source_settings.get("model", DEFAULT_OPERATOR_MODEL)).strip() or DEFAULT_OPERATOR_MODEL
    reasoning_effort = str(
        reasoning.get("effort")
        or source_settings.get("reasoning_effort")
        or DEFAULT_REASONING_EFFORT
    ).strip() or DEFAULT_REASONING_EFFORT
    base_url = str(source_settings.get("base_url", DEFAULT_OPENAI_BASE_URL)).strip() or DEFAULT_OPENAI_BASE_URL

    return {
        "active_model": active_model,
        "reasoning_effort": reasoning_effort,
        "base_url": base_url,
        "settings_path": str(SETTINGS_PATH),
        "settings_sources": [str(path) for path in settings_sources],
        "settings_version": str(snapshot.get("version", "")).strip(),
        "settings_loaded_at": str(snapshot.get("loaded_at", "")).strip(),
        "settings_reload_count": int(snapshot.get("reload_count", 0) or 0),
        "project_root": str(PROJECT_ROOT),
        "source": _format_source_label(settings_sources),
    }
