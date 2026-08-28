#!/usr/bin/env python3
"""Contained regression for structured native diplomacy loans."""

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


def wait_for_turn(deadline: float) -> dict[str, Any] | None:
    while time.monotonic() < deadline:
        try:
            envelope = bridge_request("semantic_snapshot", timeout=5)
        except BridgeUnavailable:
            time.sleep(0.1)
            continue
        snapshot = envelope.get("snapshot", {})
        if snapshot.get("interaction", {}).get("kind") == "turn":
            return snapshot
        handled, result = handle_interaction(snapshot)
        if not handled:
            emit("failure", {"stage": "opening_interaction", "snapshot": snapshot,
                             "result": result})
            return None
        time.sleep(0.05)
    return None


def main() -> int:
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
    deadline = time.monotonic() + 120
    initial = wait_for_turn(deadline)
    if not initial:
        return 3

    factions_before = bridge_request("list_factions")  # initializes contained fixture
    counterpart = next(
        (item for item in factions_before.get("items", []) if item.get("id") != 4), None,
    )
    if not counterpart:
        emit("failure", {"stage": "counterpart", "factions": factions_before})
        return 4

    loan_snapshot: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        envelope = bridge_request("semantic_snapshot", timeout=5)
        snapshot = envelope.get("snapshot", {})
        label = snapshot.get("interaction", {}).get("popup_label", "")
        if label in {"ENERGYLOAN1", "ENERGYLOAN2"}:
            loan_snapshot = snapshot
            break
        time.sleep(0.05)
    if not loan_snapshot:
        emit("failure", {"stage": "native_loan_popup"})
        return 5

    choices = bridge_request("semantic_choices", kind="interaction")
    terms = next(
        (item for item in choices.get("choices", []) if item.get("offer_type") == "loan_offer"),
        None,
    )
    accept = next(
        (item for item in choices.get("choices", []) if item.get("response") == "accept"),
        None,
    )
    if not terms or not accept or terms.get("direction") != "player_borrows":
        emit("failure", {"stage": "structured_terms", "choices": choices})
        return 6
    principal = int(terms.get("principal", 0))
    payment = int(terms.get("payment_per_turn", 0))
    term = int(terms.get("term_turns", 0))
    if principal <= 0 or payment <= 0 or term <= 0 \
            or int(terms.get("scheduled_total", -1)) != payment * term:
        emit("failure", {"stage": "term_values", "terms": terms})
        return 7

    energy_before = int(loan_snapshot.get("faction", {}).get("energy_credits", -1))
    accepted = command(choices, command="respond_to_diplomatic_offer", response="accept")
    emit("accepted", {"terms": terms, "result": accepted})
    if not accepted.get("ok"):
        return 8

    observed: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        if snapshot.get("interaction", {}).get("kind") == "turn":
            listed = bridge_request("list_factions")
            observed = next(
                (item for item in listed.get("items", [])
                 if item.get("id") == counterpart.get("id")), None,
            )
            if observed and observed.get("loans", {}).get("own_balance_owed_to_them", 0) > 0:
                energy_after = int(snapshot.get("faction", {}).get("energy_credits", -1))
                loans = observed["loans"]
                if energy_after - energy_before != principal \
                        or int(loans["own_balance_owed_to_them"]) != payment * term \
                        or int(loans["own_payment_per_turn"]) != payment:
                    emit("failure", {"stage": "native_bookkeeping", "before": energy_before,
                                     "after": energy_after, "terms": terms, "faction": observed})
                    return 9
                emit("pass", {
                    "native_energy_transfer": True,
                    "native_loan_balance": True,
                    "principal": principal,
                    "payment_per_turn": payment,
                    "term_turns": term,
                    "pixels_or_ui_input_used": False,
                })
                return 0
        time.sleep(0.05)

    emit("failure", {"stage": "loan_completion", "observed": observed})
    return 10


if __name__ == "__main__":
    sys.exit(main())
