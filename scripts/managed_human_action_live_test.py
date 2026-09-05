#!/usr/bin/env python3
"""Two sovereign MCP endpoints; private native fixtures and effect reads only."""
from __future__ import annotations

import asyncio
import json
import time

from control_worker_mcp_live_test import (
    bridge_operation, current_decision, mcp_tool, runtime_context,
)


def exercise_human_actions(workers, host):
    peer = next(worker for worker in workers if worker["instance_id"] != host["instance_id"])
    seats = [host, peer]

    def native(seat, operation="semantic_snapshot", **arguments):
        result = bridge_operation(seat["network"]["mcp_container_name"], operation, **arguments)
        assert result.get("ok"), result
        return result

    def call(seat, tool, **arguments):
        for _ in range(20):
            result = asyncio.run(mcp_tool(seat["network"]["mcp_url"], tool, arguments))
            if tool != "smac_choices" or arguments.get("kind") == "interaction" or result.get("error", {}).get("code") != "wrong_choice_phase":
                break
            # Re-observe late native upkeep notices; this retries only a read.
            finish_opening()
            time.sleep(0.1)
        assert result.get("ok"), result
        return result

    def execute(seat, frame, predicate):
        choice = next((row for row in frame.get("choices", ()) if predicate(row)), None)
        assert choice is not None, frame
        return call(seat, "smac_execute_choice", decision_id=frame["decision_id"], choice_id=choice["choice_id"])

    def observe(seat):
        return native(seat)["snapshot"]

    def finish_opening():
        for _ in range(40):
            complete = True
            for seat in seats:
                state = observe(seat)
                if state["interaction"]["kind"] in {"turn", "waiting"}:
                    continue
                if state["interaction"]["kind"] == "human_diplomacy":
                    complete = False
                    continue
                frame = asyncio.run(current_decision(seat["network"]["mcp_url"]))
                assert frame.get("ok"), frame
                if frame.get("phase") in {"turn", "wait"}:
                    continue
                assert frame.get("phase") == "interaction", frame
                execute(seat, frame, lambda row: bool(row.get("choice_id")))
                complete = False
            if complete:
                return
            time.sleep(0.1)
        raise AssertionError("opening interactions did not settle")

    def world_state(seat, ref, field="state"):
        result = call(seat, "smac_world", mode="global", subject_refs=[ref], detail="deep")
        row = next(item for item in result["items"] if item["object_ref"] == ref)
        value = row["fields"][field]
        assert value["epistemic_status"] == "current", value
        return value["value"]

    episodes = []
    try:
        for seat in seats:
            episode = "acceptance-human-" + seat["instance_id"]
            lease = runtime_context(seat["network"]["mcp_container_name"], episode)
            assert lease.get("ok"), lease
            episodes.append((seat, episode))
            decision = asyncio.run(current_decision(seat["network"]["mcp_url"]))
            assert decision.get("ok"), decision
        finish_opening()
        donor = observe(host)["faction"]["id"]
        recipient = observe(peer)["faction"]["id"]
        evidence = {}
        for phase in ("energy", "technology", "pact"):
            # Both game replicas receive identical controlled starting inputs.
            fixtures = [native(seat, "test_lan_diplomacy_fixture", faction_id=donor,
                               counterpart_faction_id=recipient,
                               initial_relationship="treaty",
                               trade_fixture="" if phase == "pact" else phase)
                        for seat in seats]
            before = [observe(seat)["faction"]["energy_credits"] for seat in seats]
            opened = None
            for attempt in range(4):
                frame = call(host, "smac_choices", kind="diplomacy")
                opened = execute(host, frame, lambda row: row.get("human_controlled")
                                 and row.get("faction_id") == recipient)
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    state = observe(host)
                    if state["interaction"]["kind"] == "human_diplomacy":
                        break
                    action = state.get("last_deferred_action", {})
                    if action.get("action_id") == opened.get("action_id") and action.get("status") in {"completed", "rejected"}:
                        break
                    time.sleep(0.1)
                if observe(host)["interaction"]["kind"] == "human_diplomacy":
                    break
                # Retry only a completed opening collision, never an unknown
                # mutation outcome or a submitted agreement.
                action = observe(host).get("last_deferred_action", {})
                assert action.get("action_id") == opened.get("action_id") and action.get("status") in {"completed", "rejected"}, action
            else:
                raise AssertionError("paired human window did not open")
            frame = call(host, "smac_choices", kind="interaction")
            if phase == "energy":
                preparation = next(row for row in frame["preparations"] if row["purpose"] == "energy_amount")
                frame = call(host, "smac_choices", kind="interaction", preparation_ref=preparation["preparation_ref"], amount=37)
                execute(host, frame, lambda row: row.get("amount") == 37)
            elif phase == "technology":
                execute(host, frame, lambda row: row.get("technology_name") == fixtures[0]["technology_name"])
            else:
                execute(host, frame, lambda row: row.get("relationship") == "pact")
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                frame = call(peer, "smac_choices", kind="interaction")
                clauses = [clause for info in frame.get("information", ()) for clause in info.get("clauses", ())]
                if clauses:
                    break
                time.sleep(0.1)
            assert clauses, frame
            if phase == "energy":
                assert any(row.get("clause") == "energy" and row.get("energy_credits") == 37 for row in clauses), clauses
            elif phase == "technology":
                assert fixtures[0]["technology_name"] in str(clauses), clauses
            else:
                assert "pact" in str(clauses).lower(), clauses
            execute(peer, frame, lambda row: row.get("response") == "accept")
            for seat in seats:
                if observe(seat)["interaction"]["kind"] == "human_diplomacy":
                    frame = call(seat, "smac_choices", kind="interaction")
                    execute(seat, frame, lambda row: "close this human diplomacy window" in row.get("meaning", ""))
            finish_opening()
            if phase == "energy":
                after = [world_state(seat, "global-economy")["energy_credits"] for seat in seats]
                assert after == [before[0] - 37, before[1] + 37], (before, after)
            elif phase == "technology":
                assert fixtures[0]["technology_name"] in str(world_state(peer, "global-owned-technologies", "technologies"))
            else:
                for seat, counterpart in ((host, recipient), (peer, donor)):
                    factions = native(seat, "list_factions")["items"]
                    row = next(row for row in factions if row["id"] == counterpart)
                    assert row["relations"]["pact"] is True, row
            evidence[phase] = {"managed_offer": True, "recipient_terms": True,
                               "managed_acceptance": True, "effect_verified": True}
            print(json.dumps({"event": "managed_human_action_verified", "capability": phase}), flush=True)
        return evidence
    finally:
        for seat, episode in episodes:
            runtime_context(seat["network"]["mcp_container_name"], episode, end=True)
