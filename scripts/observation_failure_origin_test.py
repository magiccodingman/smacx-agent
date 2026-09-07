#!/usr/bin/env python3
"""Collector telemetry retains original fault origin without locals or raw text."""
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock
from smacx_observation import ObservationCollector


def original_projection_fault():
    raise ValueError("invalid_tile_id")


def main():
    collector = object.__new__(ObservationCollector)
    collector._collect_lock = threading.Lock()
    collector._pending_native_events = []
    collector._collect_once_locked = original_projection_fault
    collector._restore_native_stage = Mock()
    collector.world_store = SimpleNamespace(telemetry=Mock())
    collector.scope = "fixture"
    collector.timeline_id = "timeline-fixture"
    try: collector.collect_once()
    except ValueError as exc: assert str(exc) == "invalid_tile_id"
    else: raise AssertionError("collector swallowed original failure")
    collector._restore_native_stage.assert_called_once()
    metrics = collector.world_store.telemetry.call_args.kwargs["dimensions"]
    assert metrics["failure_code"] == "invalid_tile_id"
    assert metrics["failure_stack"][-1]["function"] == "original_projection_fault"
    assert all(set(frame) == {"file", "line", "function"} for frame in metrics["failure_stack"])
    collector._collect_once_locked = lambda: (_ for _ in ()).throw(ValueError("private-secret detail"))
    try: collector.collect_once()
    except ValueError: pass
    assert "private-secret" not in json.dumps(collector.world_store.telemetry.call_args.kwargs)
    print(json.dumps({"passed": True, "original_stack_retained": True,
        "stage_restore_and_failure_preserved": True, "raw_text_and_locals_excluded": True}))


if __name__ == "__main__":main()
