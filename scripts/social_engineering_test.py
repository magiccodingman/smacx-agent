#!/usr/bin/env python3
"""Contained semantic regression for guarded Social Engineering control."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from smacx_controller import BridgeUnavailable, bridge_request, new_game
from semantic_playthrough import handle_interaction


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def command(source: dict[str, Any], **arguments: object) -> dict[str, Any]:
    return bridge_request(
        "semantic_command",
        timeout=10,
        match_id=source["match_id"],
        session_id=source["session_id"],
        expected_revision=source["revision"],
        **arguments,
    )


def selected_parameters(choices: dict[str, Any]) -> dict[str, int]:
    return {
        key: int(value["model_id"])
        for key, value in choices["selected"].items()
    }


def main() -> int:
    # Data Angels begin with Planetary Networks, making Planned economics a
    # legal non-default choice immediately without granting test-only tech.
    started = new_game(
        wait_seconds=60,
        difficulty=1,
        world_size=0,
        faction_id=4,
        blind_research=True,
        initial_research_priority=1,
        narrative_ui=False,
        tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2

    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            envelope = bridge_request("semantic_snapshot", timeout=5)
        except BridgeUnavailable:
            time.sleep(0.15)
            continue
        snapshot = envelope.get("snapshot", {})
        if not snapshot:
            emit("failure", envelope)
            return 3
        if snapshot["interaction"]["kind"] != "turn":
            handled, outcome = handle_interaction(snapshot)
            emit("interaction", outcome)
            if not handled:
                return 4
            continue

        choices = bridge_request("semantic_choices", kind="social_engineering")
        if not choices.get("ok") or not choices.get("enabled"):
            emit("failure", choices)
            return 5
        before_revision = snapshot["revision"]
        before_energy = snapshot["faction"]["energy_credits"]
        desired = selected_parameters(choices)

        invalid = dict(desired)
        invalid["politics"] = 99
        rejected = command(choices, command="set_social_engineering", **invalid)
        after_invalid = bridge_request("semantic_snapshot")["snapshot"]
        if rejected.get("error", {}).get("code") != "unavailable_social_model":
            emit("failure", {"reason": "invalid_not_rejected", "result": rejected})
            return 6
        if (after_invalid["revision"] != before_revision
        or after_invalid["faction"]["energy_credits"] != before_energy):
            emit("failure", {"reason": "invalid_mutated_state", "result": rejected})
            return 7
        emit("invalid_guard", rejected)

        # Refresh because every semantic mutation is deliberately tied to the
        # latest observation, even though the rejected command made no change.
        choices = bridge_request("semantic_choices", kind="social_engineering")
        desired = selected_parameters(choices)
        alternative: tuple[str, dict[str, Any]] | None = None
        for category in choices["categories"]:
            option = next((item for item in category["options"] if not item["selected"]), None)
            if option is not None:
                alternative = (category["key"], option)
                break
        if alternative is None:
            emit("failure", {"reason": "no_normal_unlocked_alternative", "choices": choices})
            return 8

        changed_key, changed_option = alternative
        desired[changed_key] = int(changed_option["model_id"])
        changed = command(choices, command="set_social_engineering", **desired)
        if not changed.get("ok"):
            emit("failure", {"reason": "change_rejected", "result": changed})
            return 9
        after_change = bridge_request("semantic_snapshot")["snapshot"]
        actual = after_change["social_engineering"]["selected"][changed_key]["model_id"]
        expected_energy = before_energy + int(changed["energy_delta"])
        if actual != desired[changed_key] or after_change["faction"]["energy_credits"] != expected_energy:
            emit("failure", {
                "reason": "change_not_applied",
                "result": changed,
                "snapshot": after_change,
            })
            return 10
        emit("policy_changed", changed)

        # Reverting on the same turn exercises the native upheaval refund
        # calculation and leaves the test match at its original policy.
        revert_choices = bridge_request("semantic_choices", kind="social_engineering")
        original = selected_parameters(choices)
        reverted = command(
            revert_choices,
            command="set_social_engineering",
            **original,
        )
        final_snapshot = bridge_request("semantic_snapshot")["snapshot"]
        if not reverted.get("ok") or final_snapshot["faction"]["energy_credits"] != before_energy:
            emit("failure", {
                "reason": "refund_not_applied",
                "result": reverted,
                "snapshot": final_snapshot,
            })
            return 11
        if -int(changed["energy_delta"]) <= 0 or int(reverted["energy_delta"]) <= 0:
            emit("failure", {
                "reason": "positive_charge_and_refund_not_exercised",
                "change": changed,
                "revert": reverted,
            })
            return 12
        emit("pass", {
            "changed_category": changed_key,
            "changed_model": changed_option["name"],
            "charged": -int(changed["energy_delta"]),
            "refunded": int(reverted["energy_delta"]),
            "final_energy": final_snapshot["faction"]["energy_credits"],
        })
        return 0

    emit("failure", {"reason": "deadline"})
    return 13


if __name__ == "__main__":
    sys.exit(main())
