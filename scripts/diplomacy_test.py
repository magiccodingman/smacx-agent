#!/usr/bin/env python3
"""Contained regression for nonvisual native diplomacy menus."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from smacx_controller import BridgeUnavailable, bridge_request, new_game
from semantic_playthrough import choose_land_move, handle_interaction, move_and_confirm


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


def choose_interaction(snapshot: dict[str, Any], option: str) -> dict[str, Any]:
    choices = bridge_request("semantic_choices", kind="interaction")
    item = next((item for item in choices.get("choices", []) if item.get("option") == option), None)
    if item is None:
        return {"ok": False, "error": "missing_option", "wanted": option, "choices": choices}
    return command(choices, command="choose_diplomacy_option", option=option)


def main() -> int:
    discovery_option = os.environ.get("SMACX_TEST_DIPLO_DISCOVERY_OPTION", "")
    discovery_counter = os.environ.get("SMACX_TEST_DIPLO_DISCOVERY_COUNTER", "")
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

    deadline = time.monotonic() + 100
    opened = False
    saw_main_menu = False
    saw_proposal_menu = False
    saw_target_selector = False
    cancelled_proposal = False
    target_faction_id = -1
    failed_moves: set[tuple[int, int, int]] = set()
    visited_tiles: set[int] = set()
    diplomacy_ui_deadline = 0.0
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
        interaction = snapshot["interaction"]
        kind = interaction["kind"]
        label = interaction.get("popup_label", "")

        if kind == "turn" and not opened:
            choices = bridge_request("semantic_choices", kind="diplomacy")
            target = next((item for item in choices.get("choices", [])
                           if item.get("supported") and not item.get("alien")), None)
            if target is None:
                units = bridge_request("list_units", scope="own", limit=100).get("items", [])
                ready = next(
                    (unit for unit in units
                     if unit.get("ready") and unit.get("roles", {}).get("combat")),
                    None,
                )
                if ready:
                    unit_choices = bridge_request(
                        "semantic_choices", kind="unit_actions", unit_id=ready["id"],
                    )
                    move = choose_land_move(
                        unit_choices.get("choices", []), ready, failed_moves,
                        visited_tiles,
                    )
                    if move:
                        move_key = (
                            ready["id"], ready["tile_id"], move["target_tile_id"],
                        )
                        result, moved = move_and_confirm(unit_choices, ready, move)
                        if moved:
                            visited_tiles.add(int(move["target_tile_id"]))
                            continue
                        failed_moves.add(move_key)
                    fresh = bridge_request(
                        "semantic_choices", kind="unit_actions", unit_id=ready["id"],
                    )
                    if fresh.get("ok"):
                        command(fresh, command="skip_unit", unit_id=ready["id"])
                    continue
                any_ready = next((unit for unit in units if unit.get("ready")), None)
                if any_ready:
                    unit_choices = bridge_request(
                        "semantic_choices", kind="unit_actions", unit_id=any_ready["id"],
                    )
                    command(unit_choices, command="skip_unit", unit_id=any_ready["id"])
                    continue
                result = command(snapshot, command="end_turn")
                if not result.get("ok") and result.get("error", {}).get("code") not in {
                    "stale_state", "wrong_choice_phase", "units_still_ready",
                }:
                    emit("failure", {"reason": "exploration_turn_failed", "result": result})
                    return 4
                continue
            target_faction_id = int(target["faction_id"])
            result = command(choices, command="open_diplomacy", faction_id=target_faction_id)
            emit("open", result)
            if not result.get("ok"):
                return 5
            opened = True
            diplomacy_ui_deadline = time.monotonic() + 5.0
            continue

        if kind in ("waiting_for_engine", "waiting_for_turn"):
            time.sleep(0.1)
            continue

        if kind == "popup" and label in ("COMM", "COMMDIPLO"):
            choices = bridge_request("semantic_choices", kind="interaction")
            result = command(choices, command="respond_to_contact", response="accept")
            emit("pre_menu", result)
            if not result.get("ok"):
                return 6
            continue

        if kind == "popup" and label.startswith("INTRO"):
            choices = bridge_request("semantic_choices", kind="interaction")
            result = command(choices, command="continue_diplomacy")
            emit("greeting", result)
            if not result.get("ok"):
                return 6
            continue

        if kind == "popup" and label.startswith("TRADETECH"):
            choices = bridge_request("semantic_choices", kind="interaction")
            terms = next((item for item in choices.get("choices", [])
                          if item.get("offer_type")), None)
            if not terms or not terms.get("terms"):
                emit("failure", {"reason": "trade_terms_missing", "choices": choices})
                return 6
            result = command(choices, command="respond_to_diplomatic_offer", response="reject")
            emit("technology_offer", {"result": result, "terms": terms})
            if not result.get("ok"):
                return 6
            continue

        if kind == "popup" and label.startswith("DEMANDTECH"):
            choices = bridge_request("semantic_choices", kind="interaction")
            terms = next((item for item in choices.get("choices", [])
                          if item.get("offer_type")), None)
            result = command(choices, command="respond_to_diplomatic_offer", response="reject")
            emit("technology_demand", {"result": result, "terms": terms})
            if not result.get("ok"):
                return 6
            continue

        if kind == "popup" and label in {
            "FACTIONTREATY", "FACTIONTRUCE", "ALIENFACTIONTREATY", "ALIENFACTIONTRUCE"
        }:
            choices = bridge_request("semantic_choices", kind="interaction")
            terms = next((item for item in choices.get("choices", [])
                          if item.get("offer_type")), None)
            result = command(choices, command="respond_to_diplomatic_offer", response="accept")
            emit("relationship_offer", {"result": result, "terms": terms})
            if not result.get("ok"):
                return 6
            continue

        if kind == "popup" and label == "DIPLO":
            saw_main_menu = True
            wanted = "finish" if cancelled_proposal else "make_proposal"
            result = choose_interaction(snapshot, wanted)
            emit("main_menu", result)
            if not result.get("ok"):
                return 7
            if wanted == "finish":
                continue
            time.sleep(0.1)
            continue

        if kind == "popup" and label == "PROPOSAL":
            saw_proposal_menu = True
            proposal_choices = bridge_request("semantic_choices", kind="interaction")
            expected_native_ids = {
                "cancel": 0, "give_gift": 1, "propose_pact": 2,
                "propose_treaty": 3, "request_research": 4,
                "buy_prototype": 5, "request_commlink": 13,
                "request_energy": 6, "repay_loan": 11, "trade_maps": 7,
                "propose_joint_attack": 8, "demand_base": 9,
            }
            bad_mapping = [
                item for item in proposal_choices.get("choices", [])
                if item.get("option") in expected_native_ids
                and int(item.get("native_option_id", -1))
                != expected_native_ids[item["option"]]
            ]
            if bad_mapping:
                emit("failure", {"reason": "proposal_native_id_mapping",
                                 "items": bad_mapping})
                return 8
            if discovery_option:
                discovery = next(
                    (item for item in proposal_choices.get("choices", [])
                     if item.get("option") == discovery_option), None,
                )
                if discovery is None:
                    emit("failure", {"reason": "missing_discovery_option",
                                     "option": discovery_option,
                                     "choices": proposal_choices})
                    return 8
                result = command(
                    proposal_choices, command="choose_diplomacy_option",
                    option=discovery_option,
                )
                emit("discovery_selection", {"option": discovery_option,
                                             "result": result})
                if not result.get("ok"):
                    return 8
                time.sleep(0.2)
                discovered = bridge_request("semantic_snapshot", timeout=5)
                discovered_choices = bridge_request("semantic_choices", kind="interaction")
                emit("discovered_interaction", {
                    "option": discovery_option,
                    "snapshot": discovered.get("snapshot", {}).get("interaction", {}),
                    "choices": discovered_choices,
                })
                if discovery_counter and discovered.get("snapshot", {}).get(
                    "interaction", {}
                ).get("popup_label") == "COUNTER1":
                    result = command(
                        discovered_choices, command="choose_diplomacy_option",
                        option=discovery_counter,
                    )
                    emit("discovery_counter_selection", {
                        "option": discovery_counter, "result": result,
                    })
                    if not result.get("ok"):
                        return 8
                    time.sleep(0.2)
                    next_state = bridge_request("semantic_snapshot", timeout=5)
                    next_choices = bridge_request("semantic_choices", kind="interaction")
                    emit("discovered_counter_interaction", {
                        "option": discovery_counter,
                        "snapshot": next_state.get("snapshot", {}).get("interaction", {}),
                        "choices": next_choices,
                    })
                return 0
            commlink_option = next(
                (item for item in proposal_choices.get("choices", [])
                 if item.get("option") == "request_commlink"), None,
            )
            if commlink_option and not saw_target_selector:
                result = command(
                    proposal_choices, command="choose_diplomacy_option",
                    option="request_commlink",
                )
                emit("proposal_commlink", result)
                if not result.get("ok"):
                    return 8
                continue
            result = choose_interaction(snapshot, "cancel")
            emit("proposal_menu", result)
            if not result.get("ok"):
                return 8
            cancelled_proposal = True
            continue

        if kind == "popup" and label == "PROPOSECOMMLINK":
            target_choices = bridge_request("semantic_choices", kind="interaction")
            targets = [item for item in target_choices.get("choices", [])
                       if item.get("command") == "choose_diplomacy_target"]
            cancel = next((item for item in target_choices.get("choices", [])
                           if item.get("command") == "cancel_diplomacy_selection"), None)
            if not targets or not cancel:
                emit("failure", {"reason": "semantic_commlink_targets",
                                 "choices": target_choices})
                return 8
            result = command(target_choices, command="cancel_diplomacy_selection")
            emit("commlink_target_selector", {"targets": targets, "result": result})
            if not result.get("ok"):
                return 8
            saw_target_selector = True
            cancelled_proposal = True
            continue

        if kind == "turn" and opened and cancelled_proposal:
            if not (saw_main_menu and saw_proposal_menu):
                emit("failure", {"reason": "menus_not_exercised", "snapshot": snapshot})
                return 9
            emit("pass", {
                "target_faction_id": target_faction_id,
                "native_main_menu": True,
                "native_proposal_menu": True,
                "native_target_selector": saw_target_selector,
                "pixels_or_ui_input_used": False,
                "protocol_phase": snapshot["protocol"]["phase"],
            })
            return 0

        if kind == "turn" and opened and not saw_main_menu \
        and time.monotonic() < diplomacy_ui_deadline:
            time.sleep(0.08)
            continue

        handled, result = handle_interaction(snapshot)
        emit("other_interaction", result)
        if not handled:
            return 10

    emit("failure", {"reason": "deadline"})
    return 11


if __name__ == "__main__":
    sys.exit(main())
