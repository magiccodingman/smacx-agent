#!/usr/bin/env python3
"""Contained semantic-bridge soak test; never uses pixels or input simulation."""

from __future__ import annotations

import argparse
import json
import sys
import time

from smacx_controller import BridgeUnavailable, bridge_request, new_game
from semantic_playthrough import handle_interaction


def show(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-turn", type=int, default=25)
    parser.add_argument("--deadline", type=int, default=150)
    parser.add_argument("--pause-on-gap", type=int, default=0)
    args = parser.parse_args()

    started = new_game(
        wait_seconds=60,
        difficulty=0,
        world_size=0,
        faction_id=1,
        blind_research=True,
        initial_research_priority=1,
        narrative_ui=False,
        tutorial_ui=False,
    )
    show("new_game", started)
    if not started.get("ok"):
        return 2

    deadline = time.monotonic() + args.deadline
    previous_turn = -1
    guards_tested = False
    allocation_tested = False
    while time.monotonic() < deadline:
        try:
            response = bridge_request("semantic_snapshot", timeout=4)
        except BridgeUnavailable as exc:
            show("bridge_unavailable", str(exc))
            time.sleep(0.25)
            continue
        snapshot = response.get("snapshot", {})
        turn = snapshot.get("turn", -1)
        interaction = snapshot.get("interaction", {})
        kind = interaction.get("kind")
        guard = {
            "match_id": snapshot.get("match_id", ""),
            "session_id": snapshot.get("session_id", ""),
            "expected_revision": snapshot.get("revision", ""),
        }
        if turn != previous_turn:
            show("turn", snapshot)
            previous_turn = turn
        if turn >= args.target_turn:
            show("passed", {"target_turn": args.target_turn, "snapshot": snapshot})
            return 0

        if kind == "popup":
            if not guards_tested and interaction.get("popup_label") == "PLANETFALL":
                wrong_session = bridge_request(
                    "semantic_command",
                    command="acknowledge_popup",
                    match_id=guard["match_id"],
                    session_id="session-does-not-exist",
                    expected_revision=guard["expected_revision"],
                )
                stale = bridge_request(
                    "semantic_command",
                    command="acknowledge_popup",
                    match_id=guard["match_id"],
                    session_id=guard["session_id"],
                    expected_revision="stale-revision",
                )
                show("guard_wrong_session", wrong_session)
                show("guard_stale_revision", stale)
                wrong_code = wrong_session.get("error", {}).get("code")
                stale_code = stale.get("error", {}).get("code")
                if wrong_code != "wrong_game_identity" or stale_code != "stale_state":
                    show("contract_failure", {"wrong_code": wrong_code, "stale_code": stale_code})
                    return 6
                guards_tested = True
            choices = bridge_request("semantic_choices", kind="interaction")
            show("interaction_choices", choices)
            options = choices.get("choices", [])
            commands = [option for option in options if option.get("command")]
            if len(commands) == 1 and commands[0].get("command") == "acknowledge_popup":
                show("command", bridge_request("semantic_command", command="acknowledge_popup", **guard))
                continue
            decline = next(
                (option for option in commands if option.get("command") == "respond_to_contact"
                 and option.get("response") == "decline"),
                None,
            )
            if decline is not None:
                show(
                    "command",
                    bridge_request("semantic_command", command="respond_to_contact", response="decline", **guard),
                )
                continue
            diplomacy_continue = next(
                (option for option in commands if option.get("command") == "continue_diplomacy"),
                None,
            )
            if diplomacy_continue is not None:
                show(
                    "command",
                    bridge_request("semantic_command", command="continue_diplomacy", **guard),
                )
                continue
            handled, result = handle_interaction(snapshot)
            show("interaction_result", {"handled": handled, "result": result})
            if handled:
                continue
            show("gap", {"reason": "popup_without_reviewed_semantic_policy",
                         "result": result, "snapshot": snapshot})
            return 3

        if kind == "first_base_name":
            show(
                "command",
                bridge_request("semantic_command", command="set_first_base_name", name="Semantic Dawn", **guard),
            )
            continue

        if kind in {"waiting_for_turn", "waiting_for_engine"}:
            time.sleep(0.1)
            continue

        if kind != "turn":
            show("gap", {"reason": "unsupported_interaction", "snapshot": snapshot})
            if args.pause_on_gap:
                time.sleep(args.pause_on_gap)
            return 3

        if not allocation_tested:
            choices = bridge_request("semantic_choices", kind="energy_allocation")
            show("energy_allocation_choices", choices)
            invalid = bridge_request(
                "semantic_command", command="set_energy_allocation",
                economy=4, psych=0, labs=5, **guard,
            )
            show("invalid_energy_allocation", invalid)
            if invalid.get("error", {}).get("code") != "invalid_energy_allocation":
                show("contract_failure", {"invalid_allocation": invalid})
                return 6
            result = bridge_request(
                "semantic_command", command="set_energy_allocation",
                economy=4, psych=0, labs=6, **guard,
            )
            show("set_energy_allocation", result)
            if not result.get("ok"):
                if result.get("error", {}).get("code") == "stale_state":
                    continue
                return 4
            allocation_tested = True
            continue

        units = bridge_request("list_units", scope="own", limit=300).get("items", [])
        ready = next((unit for unit in units if unit.get("ready")), None)
        if ready is not None:
            try:
                result = bridge_request(
                    "semantic_command", timeout=8, command="skip_unit", unit_id=ready["id"], **guard
                )
                if not result.get("ok"):
                    if result.get("error", {}).get("code") == "stale_state":
                        continue
                    show("command_error", result)
                    return 4
            except BridgeUnavailable as exc:
                # A final unit can synchronously advance the turn. Re-observe;
                # the main-thread request may have completed after the socket timeout.
                show("turn_transition_timeout", str(exc))
                time.sleep(0.25)
            continue

        if snapshot.get("faction", {}).get("ready_units", 0):
            show("gap", {"reason": "ready_unit_not_semantically_selected", "snapshot": snapshot, "units": units})
            return 3
        try:
            result = bridge_request("semantic_command", timeout=8, command="end_turn", **guard)
            if not result.get("ok"):
                if result.get("error", {}).get("code") == "stale_state":
                    continue
                show("command_error", result)
                return 4
        except BridgeUnavailable as exc:
            show("turn_transition_timeout", str(exc))
            time.sleep(0.25)

    show("deadline", {"target_turn": args.target_turn, "last_turn": previous_turn})
    return 5


if __name__ == "__main__":
    sys.exit(main())
