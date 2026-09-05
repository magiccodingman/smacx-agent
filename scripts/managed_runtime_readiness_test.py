#!/usr/bin/env python3
"""Cold observation must finish before the managed runtime listener opens."""
import json
import os
import threading
from types import SimpleNamespace
from unittest.mock import patch

import smacx_mcp as mcp
from smacx_worker_manager import WorkerManager


def main():
    collecting, release = threading.Event(), threading.Event()
    events, failures = [], []

    def refresh(*, background=False):
        if background:
            events.append("background_started")
        else:
            collecting.set()
            assert release.wait(5), "test failed to release cold collection"
            events.append("initial_projection_reconciled")
        return {"ok": True}

    def start():
        try:
            mcp._start_managed_runtime()
        except BaseException as exc:
            failures.append(exc)

    with patch.object(mcp, "_refresh_managed_world", side_effect=refresh), \
            patch.object(mcp, "_start_runtime_context_server", side_effect=lambda: events.append("listener_open")):
        thread = threading.Thread(target=start)
        thread.start()
        try:
            assert collecting.wait(5)
            assert not events, "readiness admitted a request during cold collection"
        finally:
            release.set()
            thread.join(5)
        assert not thread.is_alive() and not failures, failures
        assert events == ["initial_projection_reconciled", "background_started", "listener_open"], events

    # A LAN lobby has no world yet; its observer must remain alive so that the
    # ordinary guarded request can reconcile once the native game starts.
    with patch.object(mcp, "_refresh_managed_world", return_value={"ok": False, "error": "world_page_summary_failed"}) as refresh_mock, \
            patch.object(mcp, "_start_runtime_context_server") as listener:
        mcp._start_managed_runtime()
        assert refresh_mock.call_args_list[1].kwargs == {"background": True}
        listener.assert_called_once()
    manager = object.__new__(WorkerManager)
    manager.control = SimpleNamespace(get_runtime=lambda _: {"runtime_kind": "wine"})
    spec = {key: "fixture" for key in ("runtime_id", "match_id", "agent_id", "perspective_id", "instance_id")}
    spec.update(network={}, autostart={"enabled": True, "difficulty": 0, "world_size": 4,
        "faction_id": 1, "blind_research": False, "initial_research_priority": 0,
        "narrative_ui": False, "tutorial_ui": False, "game_settings": {}})
    for test_mode, lan_flag in (("0", "1"), ("1", "0"), ("1", "1")):
        with patch.dict(os.environ, {"SMACX_AGENT_TEST_MODE": test_mode, "SMACX_AGENT_TEST_LAN_HOST": lan_flag}), \
                patch("smacx_worker_manager.game_settings_environment", return_value={}):
            environment = manager._worker_environment(spec, "session-fixture")
        assert ("SMACX_AGENT_TEST_LAN_HOST=1" in environment) == (test_mode == lan_flag == "1")
    print(json.dumps({"passed": True, "cold_projection_before_listener": True,
                      "lobby_observer_remains_available": True,
                      "LAN_fixture_requires_both_explicit_test_flags": True}))


if __name__ == "__main__":
    main()
