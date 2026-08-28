#!/usr/bin/env python3
"""Contained native-effect regression for prototype and commlink purchases."""

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
        "semantic_command", timeout=10,
        match_id=source["match_id"], session_id=source["session_id"],
        expected_revision=source["revision"], **arguments,
    )


def snapshot(deadline: float) -> dict[str, Any]:
    while time.monotonic() < deadline:
        try:
            value = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        except BridgeUnavailable:
            time.sleep(0.1)
            continue
        if value:
            return value
    return {}


def wait_for_popup(deadline: float, wanted: str) -> dict[str, Any]:
    while time.monotonic() < deadline:
        current = snapshot(deadline)
        label = current.get("interaction", {}).get("popup_label")
        if label == wanted:
            return current
        if label:
            handled, result = handle_interaction(current)
            if not handled:
                emit("failure", {"stage": f"waiting_for_{wanted}", "label": label,
                                 "result": result})
                return {}
        time.sleep(0.05)
    return {}


def main() -> int:
    started = new_game(
        wait_seconds=60, difficulty=1, world_size=0, faction_id=4,
        blind_research=True, initial_research_priority=1,
        narrative_ui=False, tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    deadline = time.monotonic() + 150

    prototype_popup = wait_for_popup(deadline, "BUYPROTO0")
    if not prototype_popup:
        emit("failure", {"stage": "prototype_popup"})
        return 3
    prototype_choices = bridge_request("semantic_choices", kind="interaction")
    prototype_terms = next((item for item in prototype_choices.get("choices", [])
                            if item.get("offer_type") == "prototype_purchase"), None)
    prototype_accept = next((item for item in prototype_choices.get("choices", [])
                             if item.get("id") == "prototype_purchase:accept"), None)
    if not prototype_terms or not prototype_accept or not prototype_accept.get("affordable"):
        emit("failure", {"stage": "prototype_choices", "choices": prototype_choices})
        return 4
    prototype_name = str(prototype_terms.get("prototype_name", ""))
    prototype_price = int(prototype_terms.get("energy_credits", -1))
    energy_before = int(prototype_popup.get("faction", {}).get("energy_credits", -1))
    purchased = command(
        prototype_choices, command="respond_to_diplomatic_offer", response="accept",
    )
    if not purchased.get("ok"):
        emit("failure", {"stage": "prototype_submission", "result": purchased})
        return 5

    commlink_popup = wait_for_popup(deadline, "BUYCOMMLINK0")
    if not commlink_popup:
        emit("failure", {"stage": "commlink_popup"})
        return 6
    energy_between = int(commlink_popup.get("faction", {}).get("energy_credits", -1))
    if energy_before - energy_between != prototype_price:
        emit("failure", {"stage": "prototype_native_payment", "before": energy_before,
                         "after": energy_between, "price": prototype_price})
        return 7
    commlink_choices = bridge_request("semantic_choices", kind="interaction")
    commlink_terms = next((item for item in commlink_choices.get("choices", [])
                           if item.get("offer_type") == "commlink_purchase"), None)
    commlink_accept = next((item for item in commlink_choices.get("choices", [])
                            if item.get("id") == "commlink_purchase:accept"), None)
    if not commlink_terms or not commlink_accept or not commlink_accept.get("affordable"):
        emit("failure", {"stage": "commlink_choices", "choices": commlink_choices})
        return 8
    target_id = int(commlink_terms.get("target_faction_id", -1))
    commlink_price = int(commlink_terms.get("energy_credits", -1))
    acquired = command(
        commlink_choices, command="respond_to_diplomatic_offer", response="accept",
    )
    if not acquired.get("ok"):
        emit("failure", {"stage": "commlink_submission", "result": acquired})
        return 9

    while time.monotonic() < deadline:
        current = snapshot(deadline)
        label = current.get("interaction", {}).get("popup_label")
        if label:
            handled, result = handle_interaction(current)
            if not handled:
                emit("failure", {"stage": "final_interaction", "label": label,
                                 "result": result})
                return 10
            continue
        if current.get("interaction", {}).get("kind") != "turn":
            time.sleep(0.05)
            continue
        faction_items = bridge_request("list_factions").get("items", [])
        target_visible = any(int(item.get("id", -1)) == target_id for item in faction_items)
        designs = bridge_request("semantic_choices", kind="unit_design")
        prototypes = designs.get("available_prototypes", [])
        prototype_visible = any(item.get("name") == prototype_name for item in prototypes)
        energy_after = int(current.get("faction", {}).get("energy_credits", -1))
        if target_visible and prototype_visible \
                and energy_between - energy_after == commlink_price:
            emit("pass", {
                "native_prototype_transfer": True,
                "native_commlink_transfer": True,
                "native_energy_payments": True,
                "prototype_name": prototype_name,
                "prototype_price": prototype_price,
                "target_faction_id": target_id,
                "commlink_price": commlink_price,
                "pixels_or_ui_input_used": False,
            })
            return 0
        emit("failure", {
            "stage": "native_effects", "target_visible": target_visible,
            "prototype_visible": prototype_visible, "energy_before": energy_between,
            "energy_after": energy_after, "commlink_price": commlink_price,
            "offered_prototype_name": prototype_name,
            "prototype_names": [item.get("name") for item in prototypes],
        })
        return 11
    return 12


if __name__ == "__main__":
    sys.exit(main())
