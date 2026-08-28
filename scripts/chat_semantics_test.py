#!/usr/bin/env python3
"""Contained regression for native chat capture and multiplayer-only sends."""

from __future__ import annotations

import json
import os
import sys

from smacx_controller import bridge_request, new_game


def main() -> int:
    os.environ["SMACX_AGENT_TEST_MODE"] = "1"
    os.environ["SMACX_AGENT_TEST_CHAT"] = "1"
    started = new_game(
        wait_seconds=60, difficulty=0, world_size=0, faction_id=1,
        blind_research=True, initial_research_priority=1,
        narrative_ui=False, tutorial_ui=False,
    )
    if not started.get("ok"):
        raise AssertionError(f"new game failed: {started}")

    initial = bridge_request("semantic_chat", action="list")
    if not initial.get("ok") or initial.get("multiplayer_active") \
            or initial.get("can_send") or initial.get("messages"):
        raise AssertionError(f"initial chat state is malformed: {initial}")

    injected_text = "Provost: native receive boundary test"
    captured = bridge_request(
        "test_chat_fixture", sender_faction_id=2, text=injected_text,
    )
    messages = captured.get("messages", [])
    if len(messages) != 1:
        raise AssertionError(f"fixture did not yield exactly one event: {captured}")
    event = messages[0]
    expected = {
        "sequence": 1,
        "direction": "inbound",
        "channel": "received",
        "sender_faction_id": 2,
        "recipient_faction_id": None,
        "text": injected_text,
    }
    for key, value in expected.items():
        if event.get(key) != value:
            raise AssertionError(f"captured event differs at {key}: {captured}")

    delta = bridge_request("semantic_chat", action="list", after_sequence=1)
    if delta.get("messages") != [] or delta.get("latest_sequence") != 1:
        raise AssertionError(f"after-sequence filter failed: {delta}")

    identity = captured["identity"]
    rejected = bridge_request(
        "semantic_chat", action="send",
        match_id=identity["match_id"], session_id=identity["session_id"],
        client_message_id="contained-send-1", text="must not leave process",
        recipient_faction_id=0,
    )
    if rejected.get("ok") \
            or rejected.get("error", {}).get("code") != "multiplayer_chat_unavailable":
        raise AssertionError(f"single-player send was not mechanically blocked: {rejected}")

    print(json.dumps({
        "event": "pass",
        "native_receive_wrapper_exercised": True,
        "captured_sender_and_text": True,
        "session_scoped_sequence_filter": True,
        "single_player_send_blocked": True,
        "real_lan_send_tested": False,
        "pixels_or_ui_input_used": False,
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
