#!/usr/bin/env python3
"""Contained regression for object-targeted carrier recovery and deck safety."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from semantic_playthrough import handle_interaction
from smacx_controller import bridge_request, new_game


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def guarded(source: dict[str, Any], name: str, **arguments: object) -> dict[str, Any]:
    return bridge_request(
        "semantic_command", command=name,
        match_id=source["match_id"], session_id=source["session_id"],
        expected_revision=source["revision"], **arguments,
    )


def choices(unit_id: int, **arguments: object) -> dict[str, Any]:
    return bridge_request(
        "semantic_choices", kind="unit_actions", unit_id=unit_id, **arguments,
    )


def own_units() -> list[dict[str, Any]]:
    return bridge_request("list_units", scope="own", limit=300).get("items", [])


def carrier_and_wings() -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    units = own_units()
    carrier = next((item for item in units if item.get("name") == "Harness Carrier"), None)
    wings = [item for item in units if item.get("name") == "Harness Interceptor"]
    return carrier, wings


def wait_action(action_id: int, deadline: float) -> dict[str, Any]:
    action: dict[str, Any] = {}
    while time.monotonic() < deadline:
        action = bridge_request("action_status", action_id=action_id).get("action", {})
        if action.get("status") != "pending":
            return action
        time.sleep(0.05)
    return action


def resolve_ready_except(excluded: set[int]) -> bool:
    for _ in range(80):
        ready = [item for item in own_units()
                 if item.get("ready") and int(item["id"]) not in excluded]
        if not ready:
            return True
        unit_id = int(ready[0]["id"])
        unit_choices = choices(unit_id)
        commands = [item.get("command") for item in unit_choices.get("choices", [])]
        if "remain_boarded" in commands:
            item = next(item for item in unit_choices["choices"]
                        if item.get("command") == "remain_boarded")
            result = guarded(
                unit_choices, "remain_boarded", unit_id=unit_id,
                transport_unit_id=int(item["transport_unit_id"]),
            )
        elif "skip_unit" in commands:
            result = guarded(unit_choices, "skip_unit", unit_id=unit_id)
        else:
            emit("failure", {"stage": "resolve_ready", "choices": unit_choices})
            return False
        if not result.get("ok"):
            emit("failure", {"stage": "resolve_ready_command", "result": result})
            return False
    return False


def main() -> int:
    started = new_game(
        wait_seconds=60, difficulty=0, world_size=0, faction_id=1,
        blind_research=True, initial_research_priority=1,
        narrative_ui=False, tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2

    deadline = time.monotonic() + 170
    opening: dict[str, Any] = {}
    while time.monotonic() < deadline:
        opening = bridge_request("semantic_snapshot").get("snapshot", {})
        if opening.get("interaction", {}).get("kind") == "turn":
            break
        handled, reason = handle_interaction(opening)
        if not handled:
            emit("failure", {"stage": "opening", "reason": reason,
                             "snapshot": opening})
            return 3
        time.sleep(0.05)
    if opening.get("interaction", {}).get("kind") != "turn":
        return 4

    carrier, wings = carrier_and_wings()
    if carrier is None or len(wings) < 4:
        emit("failure", {"stage": "fixture", "carrier": carrier, "wings": wings})
        return 5
    carrier_id = int(carrier["id"])
    carrier_tile = int(carrier["tile_id"])
    deck = next((item for item in wings if int(item["tile_id"]) == carrier_tile), None)
    staging_groups: dict[int, list[dict[str, Any]]] = {}
    for wing in wings:
        if int(wing["tile_id"]) != carrier_tile:
            staging_groups.setdefault(int(wing["tile_id"]), []).append(wing)
    remotes = next((items for items in staging_groups.values() if len(items) >= 2), [])
    if deck is None or len(remotes) < 2 \
            or carrier.get("roles", {}).get("carrier") is not True \
            or carrier.get("cargo", {}).get("capacity") != 2:
        emit("failure", {"stage": "fixture_layout", "carrier": carrier, "wings": wings})
        return 6

    locked = choices(carrier_id)
    if locked.get("reason") != "carrier_recovery_lock" \
            or locked.get("choices", [{}])[0].get("kind") != "rule_status":
        emit("failure", {"stage": "initial_lock", "choices": locked})
        return 7
    fabricated = guarded(locked, "activate_unit", unit_id=carrier_id)
    if fabricated.get("error", {}).get("code") != "carrier_recovery_locked":
        emit("failure", {"stage": "direct_lock_guard", "result": fabricated})
        return 8

    deck_id = int(deck["id"])
    deck_choices = choices(deck_id, target_unit_id=carrier_id)
    board = next((item for item in deck_choices.get("choices", [])
                  if item.get("command") == "board_carrier"), None)
    if board is None or "x" in board or "y" in board:
        emit("failure", {"stage": "deck_board_choice", "choices": deck_choices})
        return 9
    boarded = guarded(
        deck_choices, "board_carrier", unit_id=deck_id, target_unit_id=carrier_id,
    )
    emit("initial_board", boarded)
    if not boarded.get("ok") or not boarded.get("boarded") \
            or not boarded.get("refueled") or boarded.get("loaded") != 1:
        return 10

    remote_id = int(remotes[0]["id"])
    remote2_id = int(remotes[1]["id"])
    recovery_choices = choices(remote_id, target_unit_id=carrier_id)
    recovery = next((item for item in recovery_choices.get("choices", [])
                     if item.get("command") == "recover_to_carrier"), None)
    if recovery is None or not recovery.get("fuel_safe") \
            or not recovery.get("carrier_will_be_held") \
            or recovery.get("route_kind") != "carrier_recovery":
        emit("failure", {"stage": "recovery_choice", "choices": recovery_choices})
        return 11

    bypass_choices = choices(remote_id, target_tile_id=carrier_tile)
    bypass = next((item for item in bypass_choices.get("choices", [])
                   if item.get("id") == "tile_target:invalid"), {})
    if "recover_to_carrier" not in str(bypass.get("reason", "")) \
            or any(item.get("command") == "go_to"
                   for item in bypass_choices.get("choices", [])):
        emit("failure", {"stage": "generic_route_bypass", "choices": bypass_choices})
        return 12

    recovered = guarded(
        recovery_choices, "recover_to_carrier",
        unit_id=remote_id, target_unit_id=carrier_id,
    )
    emit("recovery_reserved", recovered)
    if not recovered.get("ok") or not recovered.get("carrier_held") \
            or recovered.get("inbound_reserved") != 1:
        return 13

    full_choices = choices(remote2_id, target_unit_id=carrier_id)
    full = next((item for item in full_choices.get("choices", [])
                 if item.get("kind") == "carrier_target_query"), {})
    if full.get("legal") is not False or "capacity" not in str(full.get("reason", "")):
        emit("failure", {"stage": "capacity_reservation", "choices": full_choices})
        return 14
    carrier_locked = choices(carrier_id)
    if carrier_locked.get("reason") != "carrier_recovery_lock":
        emit("failure", {"stage": "inbound_lock", "choices": carrier_locked})
        return 15

    if not resolve_ready_except({remote_id, carrier_id}):
        return 16
    current_carrier, current_wings = carrier_and_wings()
    if current_carrier is None:
        return 17
    carrier = current_carrier
    carrier_id = int(carrier["id"])
    carrier_tile = int(carrier["tile_id"])
    arrived: dict[str, Any] | None = next(
        (item for item in current_wings
         if int(item["tile_id"]) == carrier_tile
         and not item.get("roles", {}).get("boarded")),
        None,
    )
    if arrived is None:
        management = bridge_request("semantic_choices", kind="game_management")
        if not any(item.get("command") == "end_turn"
                   for item in management.get("choices", [])):
            emit("failure", {"stage": "end_turn_choice", "choices": management,
                             "ready": [item for item in own_units() if item.get("ready")]})
            return 18
        source_turn = int(opening.get("turn", -1))
        ended = guarded(management, "end_turn")
        emit("end_turn", ended)
        if not ended.get("ok"):
            return 19
        while time.monotonic() < deadline:
            snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
            interaction = snapshot.get("interaction", {}).get("kind")
            if interaction != "turn":
                handled, _ = handle_interaction(snapshot)
                if not handled:
                    time.sleep(0.1)
                continue
            current_carrier, current_wings = carrier_and_wings()
            if current_carrier is None:
                return 20
            carrier = current_carrier
            carrier_id = int(carrier["id"])
            carrier_tile = int(carrier["tile_id"])
            if int(snapshot.get("turn", source_turn)) > source_turn:
                arrived = next(
                    (item for item in current_wings
                     if int(item["tile_id"]) == carrier_tile
                     and not item.get("roles", {}).get("boarded")),
                    None,
                )
                if arrived is not None:
                    break
            time.sleep(0.1)
    if arrived is None:
        emit("failure", {"stage": "native_arrival", "carrier": carrier,
                         "wings": carrier_and_wings()[1]})
        return 21

    arrived_id = int(arrived["id"])
    arrival_choices = choices(arrived_id, target_unit_id=carrier_id)
    if not any(item.get("command") == "board_carrier"
               for item in arrival_choices.get("choices", [])):
        emit("failure", {"stage": "arrival_board_choice", "choices": arrival_choices})
        return 21
    arrival_boarded = guarded(
        arrival_choices, "board_carrier",
        unit_id=arrived_id, target_unit_id=carrier_id,
    )
    emit("arrival_boarded", arrival_boarded)
    if not arrival_boarded.get("ok") or arrival_boarded.get("loaded") != 2:
        return 22

    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
        if snapshot.get("interaction", {}).get("kind") == "turn":
            break
        handled, _ = handle_interaction(snapshot)
        if not handled:
            time.sleep(0.1)
    if snapshot.get("interaction", {}).get("kind") != "turn":
        return 23
    carrier, _ = carrier_and_wings()
    if carrier is None:
        return 24
    carrier_id = int(carrier["id"])
    carrier_orders = choices(carrier_id)
    if carrier_orders.get("reason") != "persistent_order" \
            or not any(item.get("command") == "activate_unit"
                       for item in carrier_orders.get("choices", [])):
        emit("failure", {"stage": "carrier_release", "choices": carrier_orders})
        return 23
    activated = guarded(carrier_orders, "activate_unit", unit_id=carrier_id)
    if not activated.get("ok"):
        return 24
    move_choices = choices(carrier_id)
    move = next((item for item in move_choices.get("choices", [])
                 if item.get("command") == "move_unit" and item.get("is_ocean")), None)
    if move is None:
        emit("failure", {"stage": "carrier_ocean_move", "choices": move_choices})
        return 25
    moved = guarded(
        move_choices, "move_unit", unit_id=carrier_id,
        target_tile_id=int(move["target_tile_id"]),
    )
    action = wait_action(int(moved.get("action_id", -1)), deadline)
    emit("carrier_move", {"result": moved, "action": action})
    if not moved.get("ok") or action.get("status") != "completed":
        return 26

    final_carrier, _ = carrier_and_wings()
    if final_carrier is None:
        return 27
    final_carrier_id = int(final_carrier["id"])
    final_tile = int(final_carrier["tile_id"])
    final_units = own_units()
    passengers = [item for item in final_units
                  if item.get("name") == "Harness Interceptor"
                  and item.get("roles", {}).get("boarded")
                  and int(item.get("transport_unit_id", -1)) == final_carrier_id]
    if len(passengers) != 2 or any(int(item["tile_id"]) != final_tile for item in passengers) \
            or final_carrier.get("cargo", {}).get("loaded") != 2 \
            or final_carrier.get("cargo", {}).get("recovery_locked"):
        emit("failure", {"stage": "carried_stack", "carrier": final_carrier,
                         "passengers": passengers})
        return 28

    emit("pass", {
        "object_targeted_recovery": True,
        "deck_capacity_reserved": True,
        "carrier_mechanically_locked": True,
        "native_turn_arrival": True,
        "native_boarding_and_refuel": True,
        "boarded_aircraft_moved_with_carrier": True,
        "coordinates_or_pixels_used": False,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
