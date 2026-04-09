"""Tests for core.config — YAML loading, deep merge, caching."""
from __future__ import annotations

from core.config import _deep_merge, _read_yaml_dict


class TestDeepMerge:
    def test_flat_override(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        assert _deep_merge(base, override) == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        base = {"x": {"y": 1, "z": 2}}
        override = {"x": {"z": 99}}
        assert _deep_merge(base, override) == {"x": {"y": 1, "z": 99}}

    def test_override_replaces_non_dict_with_dict(self):
        base = {"a": "scalar"}
        override = {"a": {"nested": True}}
        assert _deep_merge(base, override) == {"a": {"nested": True}}

    def test_empty_base(self):
        assert _deep_merge({}, {"key": "val"}) == {"key": "val"}

    def test_empty_override(self):
        assert _deep_merge({"key": "val"}, {}) == {"key": "val"}

    def test_does_not_mutate_inputs(self):
        base = {"a": {"b": 1}}
        override = {"a": {"c": 2}}
        _deep_merge(base, override)
        assert base == {"a": {"b": 1}}
        assert override == {"a": {"c": 2}}


class TestReadYamlDict:
    def test_valid_yaml(self, tmp_yaml):
        path = tmp_yaml("key: value\nnested:\n  a: 1")
        assert _read_yaml_dict(path) == {"key": "value", "nested": {"a": 1}}

    def test_missing_file(self, tmp_path):
        assert _read_yaml_dict(tmp_path / "nope.yaml") == {}

    def test_non_dict_yaml_returns_empty(self, tmp_yaml):
        path = tmp_yaml("- item1\n- item2")
        assert _read_yaml_dict(path) == {}

    def test_empty_file_returns_empty(self, tmp_yaml):
        path = tmp_yaml("")
        assert _read_yaml_dict(path) == {}

    def test_invalid_yaml_returns_empty(self, tmp_yaml):
        path = tmp_yaml(":{bad yaml[")
        assert _read_yaml_dict(path) == {}
