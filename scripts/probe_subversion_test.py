#!/usr/bin/env python3
"""Contained regression for quoted semantic probe unit subversion."""
import json, sys, time
from semantic_playthrough import handle_interaction
from smacx_controller import bridge_request, new_game

def emit(event, payload):
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)

def main():
    started = new_game(wait_seconds=60, difficulty=0, world_size=0, faction_id=1)
    emit("new_game", started)
    if not started.get("ok"): return 2
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        snap = bridge_request("semantic_snapshot").get("snapshot", {})
        if snap.get("interaction", {}).get("kind") != "turn":
            handled, outcome = handle_interaction(snap)
            if not handled: emit("failure", outcome); return 3
            continue
        units = bridge_request("list_units", scope="visible").get("items", [])
        probe = next((u for u in units if u.get("owner") == 1 and u.get("roles", {}).get("probe")), None)
        if not probe:
            for unit in units:
                if unit.get("owner") != snap.get("faction", {}).get("id") or not unit.get("ready"): continue
                choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=unit["id"])
                bridge_request("semantic_command", command="skip_unit", match_id=choices["match_id"],
                    session_id=choices["session_id"], expected_revision=choices["revision"], unit_id=unit["id"])
            fresh = bridge_request("semantic_snapshot").get("snapshot", {})
            if fresh.get("interaction", {}).get("kind") == "turn":
                bridge_request("semantic_command", command="end_turn", match_id=fresh["match_id"],
                    session_id=fresh["session_id"], expected_revision=fresh["revision"])
            time.sleep(.2); continue
        choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=probe["id"])
        choice = next((c for c in choices.get("choices", [])
                       if c.get("command") == "execute_probe_subversion" and c.get("enhanced") == 1), None)
        if not choice: emit("failure", choices); return 5
        result = bridge_request("semantic_command", timeout=12,
            command="execute_probe_subversion", match_id=choices["match_id"],
            session_id=choices["session_id"], expected_revision=choices["revision"],
            unit_id=probe["id"], target_unit_id=choice["target_unit_id"],
            target_tile_id=choice["target_tile_id"], enhanced=1,
            confirm_probe_incident=1)
        emit("queued", {"choice": choice, "result": result})
        if not result.get("ok"): return 6
        action_id = result["action_id"]
        while time.monotonic() < deadline:
            status = bridge_request("action_status", action_id=action_id)
            action = status.get("action", {})
            if action.get("status") == "pending": time.sleep(.1); continue
            emit("completed", status)
            visible = bridge_request("list_units", scope="visible").get("items", [])
            captured = next((u for u in visible if u.get("name") == choice["target_unit_name"]
                             and u.get("owner") == 1), None)
            emit("pass", {"quoted_cost": choice["energy_cost"], "captured": bool(captured),
                           "native_result": action.get("native_result"), "pixels_used": False})
            return 0 if action.get("status") == "completed" and captured else 7
    return 8

if __name__ == "__main__": sys.exit(main())
