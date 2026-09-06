#!/usr/bin/env python3
"""Actual private HTTP handler retries only pre-lease collection revision races."""
import json
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock, patch
from urllib.request import urlopen
from urllib.error import HTTPError
import smacx_mcp as mcp


def main():
    transient = {"ok": False, "error": "world_changed_during_collection"}
    for outcomes, expected_status, expected_attempts in (
        ([transient, transient, {"ok": True}], 200, 3),
        ([transient] * 3, 409, 3),
        ([{"ok": False, "error": "native_observation_feed_failed"}], 409, 1),
    ):
        assembler, attention = MagicMock(), MagicMock()
        assembler.build.return_value = {"identity": {}, "attention": {
            "attention_lease_id": "attention-test", "status": "leased"}}
        server = ThreadingHTTPServer(("127.0.0.1", 0), mcp._RuntimeContextHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        def refresh():
            # A failed attempt must never issue runtime/attention state.
            assembler.build.assert_not_called()
            attention.acquire_sovereign.assert_not_called()
            return outcomes.pop(0)
        with patch.object(mcp._RuntimeContextHandler, "_authorized", return_value=True), \
                patch.object(mcp, "_refresh_managed_world", side_effect=refresh) as collect, \
                patch.object(mcp, "_managed_scope_identity", return_value=("match", "session", "agent", "perspective")), \
                patch.object(mcp, "controller_chat_attention"), \
                patch.object(mcp, "_runtime_services", return_value=(assembler, attention)) as services, \
                patch.object(mcp, "RUNTIME_EPISODE_TOKENS", {}), \
                patch.object(mcp, "diagnostic_record") as record:
            thread.start()
            try:
                try:
                    response = urlopen(f"http://127.0.0.1:{server.server_port}/runtime-context?episode_id=episode-test", timeout=5)
                except HTTPError as exc:
                    response = exc
                with response:
                    payload = json.load(response)
                    assert response.status == expected_status
                assert collect.call_count == expected_attempts
                if expected_status == 200:
                    assert payload["ok"]
                    attention.acquire_sovereign.assert_called_once()
                    attention.placed.assert_called_once_with("attention-test")
                    assembler.build.assert_called_once()
                    assert sum(call.args[0] == "runtime_context_deferred" for call in record.call_args_list) == 2
                    assert not any(call.args[0] == "runtime_context_failed" for call in record.call_args_list)
                else:
                    assert not payload["ok"]
                    services.assert_not_called()
                    attention.acquire_sovereign.assert_not_called()
                    assert any(call.args[0] == "runtime_context_failed" for call in record.call_args_list)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(5)
    print(json.dumps({"passed": True, "actual_http_handler": True,
        "transient_retry_before_context_or_lease": True,
        "persistent_and_unrelated_errors_fail_closed": True}))


if __name__ == "__main__":
    main()
