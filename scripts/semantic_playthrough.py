#!/usr/bin/env python3
"""Meaningful contained SMACX playthrough using semantic state/actions only.

This is an integration regression, not a competent game-playing bot. It proves
that a client can configure its economy and production, explore, improve land,
found another base, advance research, and survive ordinary game notifications
without screenshots, coordinates from pixels, or simulated input.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from smacx_controller import BridgeUnavailable, bridge_request, new_game

TRACE_ACTIONS = False

def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def snapshot_guard(snapshot: dict[str, Any]) -> dict[str, str]:
    return {
        "match_id": snapshot["match_id"],
        "session_id": snapshot["session_id"],
        "expected_revision": snapshot["revision"],
    }


def response_guard(response: dict[str, Any]) -> dict[str, str]:
    return {
        "match_id": response["match_id"],
        "session_id": response["session_id"],
        "expected_revision": response["revision"],
    }


def retryable(result: dict[str, Any]) -> bool:
    return result.get("error", {}).get("code") in {
        "stale_state", "not_actionable", "wrong_choice_phase", "game_timeout",
        "superseded_request", "unit_not_ready", "native_action_rejected",
        "end_turn_transition_pending",
    }


def command(choice_source: dict[str, Any], command_name: str, **arguments: object) -> dict[str, Any]:
    if not choice_source.get("ok"):
        if TRACE_ACTIONS:
            emit("choice_unavailable", {"command": command_name, "result": choice_source})
        return choice_source
    result = bridge_request(
        "semantic_command", timeout=10, command=command_name,
        **response_guard(choice_source), **arguments,
    )
    if TRACE_ACTIONS or not result.get("ok"):
        emit("command", {"command": command_name, "arguments": arguments, "result": result})
    return result


def handle_interaction(snapshot: dict[str, Any]) -> tuple[bool, str]:
    kind = snapshot["interaction"]["kind"]
    if kind in {"waiting_for_turn", "waiting_for_engine"}:
        time.sleep(0.08)
        return True, "waiting"
    if kind == "first_base_name":
        choices = bridge_request("semantic_choices", kind="interaction")
        result = command(choices, "set_first_base_name", name="Semantic Dawn")
        return result.get("ok", False), "first_base_name"
    if kind in {"research_priority", "research_choice"}:
        choices = bridge_request("semantic_choices", kind="interaction")
        actions = [item for item in choices.get("choices", []) if item.get("command")]
        if not actions:
            return False, "research_interaction_without_choice"
        selected = next((item for item in actions if item.get("priority") == 1), actions[0])
        parameters = {key: selected[key] for key in ("priority", "tech_id") if key in selected}
        result = command(choices, selected["command"], **parameters)
        return result.get("ok", False), "research_interaction"
    if kind != "popup":
        return False, f"unsupported_interaction:{kind}"

    choices = bridge_request("semantic_choices", kind="interaction")
    actions = [item for item in choices.get("choices", []) if item.get("command")]
    acknowledgement = next((item for item in actions if item["command"] == "acknowledge_popup"), None)
    if acknowledgement:
        result = command(choices, "acknowledge_popup")
        return result.get("ok", False), "popup_acknowledged"
    decline = next(
        (item for item in actions if item["command"] == "respond_to_contact"
         and item.get("response") == "decline"),
        None,
    )
    if decline:
        result = command(choices, "respond_to_contact", response="decline")
        return result.get("ok", False), "contact_declined"
    diplomatic_option = next(
        (item for item in actions if item["command"] == "choose_diplomacy_option"
         and item.get("option") in {"finish", "cancel"}),
        None,
    )
    if diplomatic_option:
        result = command(
            choices,
            "choose_diplomacy_option",
            option=diplomatic_option["option"],
        )
        return result.get("ok", False), f"diplomacy_{diplomatic_option['option']}"
    diplomatic_reject = next(
        (item for item in actions if item["command"] == "respond_to_diplomatic_offer"
         and item.get("response") == "reject"),
        None,
    )
    if diplomatic_reject:
        result = command(choices, "respond_to_diplomatic_offer", response="reject")
        return result.get("ok", False), "diplomatic_offer_rejected"
    incoming_vote_reject = next(
        (item for item in actions if item["command"] == "respond_to_incoming_vote_offer"
         and item.get("response") == "reject"),
        None,
    )
    if incoming_vote_reject:
        result = command(choices, "respond_to_incoming_vote_offer", response="reject")
        return result.get("ok", False), "incoming_vote_offer_rejected"
    design_decline = next(
        (item for item in actions if item["command"] == "respond_to_design_offer"
         and item.get("response") == "decline"),
        None,
    )
    if design_decline:
        result = command(choices, "respond_to_design_offer", response="decline")
        return result.get("ok", False), "design_offer_declined"
    monolith = next(
        (item for item in actions if item["command"] == "respond_to_monolith"
         and item.get("response") == "investigate"),
        None,
    )
    if monolith:
        result = command(choices, "respond_to_monolith", response="investigate")
        return result.get("ok", False), "monolith_investigated"
    probe_forgiveness = next(
        (item for item in actions if item["command"] == "respond_to_probe_incident"
         and item.get("response") in {"forgive", "tolerate"}),
        None,
    )
    if probe_forgiveness:
        result = command(
            choices,
            "respond_to_probe_incident",
            response=probe_forgiveness["response"],
        )
        return result.get("ok", False), "probe_incident_forgiven"
    conventional_attack = next(
        (item for item in actions if item["command"] == "respond_to_nerve_gas"
         and item.get("response") == "conventional"),
        None,
    )
    if conventional_attack:
        result = command(
            choices, "respond_to_nerve_gas", response="conventional",
        )
        return result.get("ok", False), "nerve_gas_withheld"
    combat_cancel = next(
        (item for item in actions if item["command"] == "respond_to_combat_confirmation"
         and item.get("response") == "cancel"),
        None,
    )
    if combat_cancel:
        result = command(
            choices, "respond_to_combat_confirmation", response="cancel",
        )
        return result.get("ok", False), "combat_cancelled_after_odds_warning"
    end_turn_proceed = next(
        (item for item in actions if item["command"] == "respond_to_end_turn_confirmation"
         and item.get("response") == "proceed"),
        None,
    )
    if end_turn_proceed:
        result = command(
            choices, "respond_to_end_turn_confirmation", response="proceed",
        )
        return result.get("ok", False), "end_turn_confirmed"
    accession = next(
        (item for item in actions if item["command"] == "respond_to_supreme_leader"
         and item.get("response") == "accede"),
        None,
    )
    if accession:
        result = command(choices, "respond_to_supreme_leader", response="accede")
        return result.get("ok", False), "supreme_leader_acceded"
    endgame_advance = next(
        (item for item in actions
         if item["command"] == "advance_endgame_presentation"),
        None,
    )
    if endgame_advance:
        result = command(
            choices, "advance_endgame_presentation",
            phase=endgame_advance["phase"],
        )
        return result.get("ok", False), f"endgame_{endgame_advance['phase']}_advanced"
    finish_game = next(
        (item for item in actions if item["command"] == "respond_to_game_over"
         and item.get("response") == "finish"),
        None,
    )
    if finish_game:
        result = command(choices, "respond_to_game_over", response="finish")
        return result.get("ok", False), "game_finished"
    council_vote = next(
        (item for item in actions if item["command"] == "cast_council_vote"
         and item.get("candidate_faction_id") == snapshot["faction"]["id"]),
        None,
    ) or next(
        (item for item in actions if item["command"] == "cast_council_vote"
         and item.get("response") == "yea"),
        None,
    ) or next(
        (item for item in actions if item["command"] == "cast_council_vote"),
        None,
    )
    if council_vote:
        parameters = {
            key: council_vote[key]
            for key in ("candidate_faction_id", "response") if key in council_vote
        }
        result = command(choices, "cast_council_vote", **parameters)
        return result.get("ok", False), "council_ballot_cast"
    safe_incident = next(
        (item for item in actions
         if item["command"] == "respond_to_territorial_incident"
         and item.get("response") in {"cancel", "withdraw"}),
        None,
    )
    if safe_incident:
        result = command(
            choices,
            "respond_to_territorial_incident",
            response=safe_incident["response"],
        )
        return result.get("ok", False), f"territorial_incident_{safe_incident['response']}"
    return False, f"unsupported_popup:{snapshot['interaction'].get('popup_label', '')}"


def choose_land_move(
    choices: list[dict[str, Any]], unit: dict[str, Any],
    failed_moves: set[tuple[int, int, int]], visited_tiles: set[int],
) -> dict[str, Any] | None:
    moves = [
        item for item in choices if item.get("command") == "move_unit"
        and (unit["id"], unit["tile_id"], item["target_tile_id"]) not in failed_moves
    ]
    safe = [
        item for item in moves
        if item.get("visible_now") and not item.get("is_ocean")
        and "base" not in item.get("features", [])
        and "vehicle" not in item.get("features", [])
    ]
    if not safe:
        safe = [item for item in moves if item.get("known") and not item.get("is_ocean", False)]
    if not safe:
        safe = moves
    if not safe:
        return None
    unvisited = [item for item in safe if int(item["target_tile_id"]) not in visited_tiles]
    return min(unvisited or safe, key=lambda item: int(item["direction_id"]))


def wait_for_unit_move(
    unit_id: int, origin_tile_id: int, target_tile_id: int, timeout: float = 2.5,
) -> tuple[bool, dict[str, Any] | None]:
    """Confirm that a queued native move actually changed observable game state."""
    deadline = time.monotonic() + timeout
    last_unit: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        units = bridge_request("list_units", scope="own", limit=300).get("items", [])
        last_unit = next((unit for unit in units if unit.get("id") == unit_id), None)
        if last_unit is None:
            return True, None
        position = int(last_unit["tile_id"])
        if position == target_tile_id or position != origin_tile_id:
            return True, last_unit
        time.sleep(0.06)
    return False, last_unit


def move_and_confirm(
    choices: dict[str, Any], unit: dict[str, Any], move: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    origin = int(unit["tile_id"])
    target = int(move["target_tile_id"])
    result = command(
        choices, "move_unit", unit_id=unit["id"], target_tile_id=target,
    )
    if not result.get("ok") or not result.get("queued"):
        return result, False
    action_id = result.get("action_id")
    if isinstance(action_id, int):
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            status = bridge_request("action_status", action_id=action_id, timeout=5)
            action = status.get("action", {})
            if action.get("status") == "rejected":
                return {
                    "ok": False,
                    "error": {"code": "native_action_rejected"},
                    "execution": action,
                }, False
            if action.get("status") == "completed":
                return result, True
            time.sleep(0.04)
    moved, observed = wait_for_unit_move(unit["id"], origin, target)
    if not moved:
        emit("move_not_completed", {
            "unit_id": unit["id"], "origin": origin, "target": target,
            "observed": observed,
        })
    return result, moved


def requirements_met(metrics: dict[str, Any]) -> bool:
    return bool(
        metrics["allocation_set"] and metrics["production_set"]
        and metrics["unit_moves"] > 0 and metrics["terraform_orders"] > 0
        and metrics["second_base_founded"] and metrics["research_advanced"]
    )


def main() -> int:
    global TRACE_ACTIONS
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-turn", type=int, default=35)
    parser.add_argument("--deadline", type=int, default=210)
    parser.add_argument("--trace-actions", action="store_true")
    args = parser.parse_args()
    TRACE_ACTIONS = args.trace_actions

    started = new_game(
        wait_seconds=60, difficulty=0, world_size=0, faction_id=1,
        blind_research=True, initial_research_priority=0,
        narrative_ui=False, tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2

    deadline = time.monotonic() + args.deadline
    metrics: dict[str, Any] = {
        "allocation_set": False,
        "production_set": {},
        "former_queued": False,
        "unit_moves": 0,
        "terraform_orders": 0,
        "second_base_founded": False,
        "research_advanced": False,
        "initial_research_accumulated": None,
    }
    failed_terraform: set[tuple[int, int, int]] = set()
    failed_moves: set[tuple[int, int, int]] = set()
    visited_tiles: set[int] = set()
    former_checked_turn = -1
    last_turn = -1

    while time.monotonic() < deadline:
        try:
            envelope = bridge_request("semantic_snapshot", timeout=5)
        except BridgeUnavailable as exc:
            emit("bridge_wait", str(exc))
            time.sleep(0.2)
            continue
        snapshot = envelope.get("snapshot", {})
        if not snapshot:
            emit("failure", envelope)
            return 3
        turn = snapshot["turn"]
        if turn != last_turn:
            emit("turn", snapshot)
            last_turn = turn

        if turn >= args.target_turn and requirements_met(metrics):
            serializable = dict(metrics)
            serializable["production_set"] = dict(metrics["production_set"])
            emit("passed", {"turn": turn, "metrics": serializable, "snapshot": snapshot})
            return 0

        kind = snapshot["interaction"]["kind"]
        if kind != "turn":
            if TRACE_ACTIONS:
                emit("interaction", {
                    "kind": kind,
                    "popup_label": snapshot["interaction"].get("popup_label", ""),
                    "turn": turn,
                    "revision": snapshot.get("revision"),
                })
            ok, outcome = handle_interaction(snapshot)
            if not ok:
                summary = dict(metrics)
                summary["production_set"] = dict(metrics["production_set"])
                emit("capability_gap", {"outcome": outcome, "metrics": summary, "snapshot": snapshot})
                return 4
            if outcome == "game_finished":
                summary = dict(metrics)
                summary["production_set"] = dict(metrics["production_set"])
                emit("game_finished", {"turn": turn, "metrics": summary, "snapshot": snapshot})
                return 0
            continue

        research = snapshot["research"]
        if metrics["initial_research_accumulated"] is None:
            metrics["initial_research_accumulated"] = research.get("accumulated", 0)
        if research.get("accumulated", 0) > metrics["initial_research_accumulated"]:
            metrics["research_advanced"] = True

        if not metrics["allocation_set"]:
            choices = bridge_request("semantic_choices", kind="energy_allocation")
            result = command(choices, "set_energy_allocation", economy=4, psych=0, labs=6)
            if not result.get("ok"):
                if retryable(result):
                    continue
                return 5
            metrics["allocation_set"] = True
            continue

        bases = bridge_request("list_bases", limit=200).get("items", [])
        if len(bases) >= 2:
            metrics["second_base_founded"] = True

        units = bridge_request("list_units", scope="own", limit=300).get("items", [])
        has_former = any(unit.get("roles", {}).get("former") for unit in units)
        if bases and not has_former and not metrics["former_queued"] and former_checked_turn != turn:
            former_checked_turn = turn
            choices = bridge_request("semantic_choices", kind="production", base_id=bases[0]["id"])
            former = next((item for item in choices.get("choices", []) if "Former" in item.get("name", "")), None)
            if former:
                result = command(
                    choices, "set_production", base_id=bases[0]["id"], item_id=former["item_id"],
                )
                if result.get("ok"):
                    metrics["former_queued"] = True
                    metrics["production_set"][bases[0]["id"]] = former["item_id"]
                    continue

        unconfigured = next((base for base in bases if base["id"] not in metrics["production_set"]), None)
        if unconfigured:
            choices = bridge_request("semantic_choices", kind="production", base_id=unconfigured["id"])
            options = choices.get("choices", [])
            selected = next((item for item in options if "Former" in item.get("name", "")), None)
            if not selected:
                selected = next((item for item in options if "Scout" in item.get("name", "")), None)
            if not selected and options:
                selected = min(options, key=lambda item: item.get("mineral_cost", 999999))
            if not selected:
                emit("capability_gap", {"outcome": "no_production_choice", "base": unconfigured})
                return 4
            result = command(
                choices, "set_production", base_id=unconfigured["id"], item_id=selected["item_id"],
            )
            if not result.get("ok"):
                if retryable(result):
                    continue
                return 5
            metrics["production_set"][unconfigured["id"]] = selected["item_id"]
            continue

        ready_units = [unit for unit in units if unit.get("ready")]
        ready_units.sort(key=lambda unit: (
            0 if unit.get("roles", {}).get("colony") and not metrics["second_base_founded"] else
            1 if unit.get("roles", {}).get("former") and metrics["terraform_orders"] == 0 else
            2 if unit.get("roles", {}).get("combat") else 3,
            unit["id"],
        ))
        ready = ready_units[0] if ready_units else None
        if ready:
            choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=ready["id"])
            options = choices.get("choices", [])
            roles = ready.get("roles", {})

            if roles.get("colony") and not metrics["second_base_founded"]:
                found = next((item for item in options if item.get("command") == "found_base"), None)
                if found:
                    result = command(choices, "found_base", unit_id=ready["id"], name="Guarded Horizon")
                    if result.get("ok"):
                        metrics["second_base_founded"] = True
                        continue
                move = choose_land_move(
                    options, ready, failed_moves, visited_tiles,
                )
                if move:
                    move_key = (ready["id"], ready["tile_id"], move["target_tile_id"])
                    result, moved = move_and_confirm(choices, ready, move)
                    if moved:
                        visited_tiles.add(int(move["target_tile_id"]))
                        metrics["unit_moves"] += 1
                        continue
                    failed_moves.add(move_key)
                    if not result.get("ok") and not retryable(result):
                        return 5
                    continue

            if roles.get("former") and metrics["terraform_orders"] == 0:
                terraform = [item for item in options if item.get("command") == "terraform"]
                terraform.sort(key=lambda item: (
                    0 if "Farm" in item.get("name", "") else
                    1 if "Road" in item.get("name", "") else
                    2 if "Forest" in item.get("name", "") else 3
                ))
                selected = next(
                    (item for item in terraform
                     if (turn, ready["id"], item["former_id"]) not in failed_terraform),
                    None,
                )
                if selected:
                    result = command(
                        choices, "terraform", unit_id=ready["id"], former_id=selected["former_id"],
                    )
                    if result.get("ok") and result.get("accepted"):
                        metrics["terraform_orders"] += 1
                        continue
                    failed_terraform.add((turn, ready["id"], selected["former_id"]))
                    continue

                move = choose_land_move(
                    options, ready, failed_moves, visited_tiles,
                )
                if move:
                    move_key = (ready["id"], ready["tile_id"], move["target_tile_id"])
                    result, moved = move_and_confirm(choices, ready, move)
                    if moved:
                        visited_tiles.add(int(move["target_tile_id"]))
                        metrics["unit_moves"] += 1
                        continue
                    failed_moves.add(move_key)
                    if not result.get("ok") and not retryable(result):
                        return 5
                    continue

            move = choose_land_move(
                options, ready, failed_moves, visited_tiles,
            )
            if roles.get("combat") and move:
                move_key = (ready["id"], ready["tile_id"], move["target_tile_id"])
                result, moved = move_and_confirm(choices, ready, move)
                if moved:
                    visited_tiles.add(int(move["target_tile_id"]))
                    metrics["unit_moves"] += 1
                    continue
                failed_moves.add(move_key)
                if not result.get("ok") and not retryable(result):
                    return 5
                continue

            result = command(choices, "skip_unit", unit_id=ready["id"])
            if not result.get("ok") and not retryable(result):
                return 5
            continue

        try:
            choices = bridge_request("semantic_choices", kind="game_management")
            # semantic_snapshot and semantic_choices are separate reads.  The
            # native game can advance between them (for example, AI movement
            # can finish and make several human units ready).  Never diagnose
            # a missing capability from a choice family belonging to a newer
            # state: discard the old plan and observe again.
            if choices.get("revision") != snapshot.get("revision"):
                if TRACE_ACTIONS:
                    emit("state_changed", {
                        "while": "selecting_end_turn",
                        "snapshot_revision": snapshot.get("revision"),
                        "choices_revision": choices.get("revision"),
                    })
                continue
            end_turn = next(
                (item for item in choices.get("choices", [])
                 if item.get("command") == "end_turn"), None,
            )
            if not end_turn:
                transition_pending = next(
                    (item for item in choices.get("choices", [])
                     if item.get("id") == "turn:transition_pending"), None,
                )
                if transition_pending:
                    time.sleep(0.08)
                    continue
                end_blocked = next(
                    (item for item in choices.get("choices", [])
                     if item.get("id") == "turn:end_blocked"), None,
                )
                if end_blocked:
                    # This is an informative, non-executable choice.  Its
                    # ready-unit count tells the client why it must re-plan.
                    if TRACE_ACTIONS:
                        emit("end_turn_blocked", end_blocked)
                    continue
                emit("capability_gap", {
                    "outcome": "end_turn_choice_missing",
                    "choices": choices,
                    "snapshot": snapshot,
                })
                return 4
            result = command(choices, "end_turn")
            if not result.get("ok") and not retryable(result):
                return 5
        except BridgeUnavailable as exc:
            emit("turn_transition", str(exc))
            time.sleep(0.2)

    serializable = dict(metrics)
    serializable["production_set"] = dict(metrics["production_set"])
    emit("deadline", {"last_turn": last_turn, "metrics": serializable})
    return 6


if __name__ == "__main__":
    sys.exit(main())
