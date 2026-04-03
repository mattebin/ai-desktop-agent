from __future__ import annotations

from pathlib import Path
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


def get_settings_sources() -> list[Path]:
    return [path for path in SETTINGS_LAYER_PATHS if path.exists()]


def load_settings() -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for path in get_settings_sources():
        merged = _deep_merge(merged, _read_yaml_dict(path))
    return merged


def _format_source_label(paths: list[Path]) -> str:
    if not paths:
        return "config/settings.yaml"
    return " + ".join(str(path.relative_to(PROJECT_ROOT)).replace("\\", "/") for path in paths)


def get_runtime_model_config(settings: Dict[str, Any] | None = None) -> Dict[str, Any]:
    source_settings = settings if isinstance(settings, dict) else load_settings()
    settings_sources = get_settings_sources()
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
        "project_root": str(PROJECT_ROOT),
        "source": _format_source_label(settings_sources),
    }
