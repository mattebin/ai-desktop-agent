from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
SETTINGS_PATH = CONFIG_DIR / "settings.yaml"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPERATOR_MODEL = "gpt-5.4"
DEFAULT_REASONING_EFFORT = "medium"


def load_settings() -> Dict[str, Any]:
    if not SETTINGS_PATH.exists():
        return {}

    try:
        loaded = yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}

    return loaded if isinstance(loaded, dict) else {}


def get_runtime_model_config(settings: Dict[str, Any] | None = None) -> Dict[str, str]:
    source_settings = settings if isinstance(settings, dict) else load_settings()
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
        "project_root": str(PROJECT_ROOT),
        "source": "config/settings.yaml",
    }
