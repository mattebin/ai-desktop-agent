"""Tests for core.safety — stop event management."""
from __future__ import annotations

from core.safety import STOP_EVENT, clear_stop, request_stop, stop_requested


class TestSafetyStopEvent:
    def setup_method(self):
        clear_stop()

    def test_initial_state_not_stopped(self):
        assert not stop_requested()

    def test_request_stop_sets_event(self):
        request_stop()
        assert stop_requested()
        assert STOP_EVENT.is_set()

    def test_clear_stop_resets_event(self):
        request_stop()
        clear_stop()
        assert not stop_requested()

    def test_double_request_is_idempotent(self):
        request_stop()
        request_stop()
        assert stop_requested()
        clear_stop()
        assert not stop_requested()
