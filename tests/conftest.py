"""Shared fixtures for the AI Desktop Operator test suite."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def tmp_yaml(tmp_path):
    """Write a temporary YAML file and return its Path."""
    def _write(content: str, name: str = "test.yaml") -> Path:
        p = tmp_path / name
        p.write_text(content, encoding="utf-8")
        return p
    return _write
