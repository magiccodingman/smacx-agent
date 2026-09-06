#!/usr/bin/env python3
"""Every managed durable-memory read remains bounded on a mature campaign."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

import smacx_controller
from smacx_journal import CampaignJournal
from smacx_store import MemoryScope, SmacxStore


def _tokens(value: object) -> int:
    return max(1, (len(json.dumps(value, ensure_ascii=False,
                                  separators=(",", ":"))) + 3) // 4)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-managed-memory-scale-") as raw:
        root = Path(raw)
        store = SmacxStore(root / "state.sqlite3")
        store.ensure_agent("agent-memory", "Memory")
        store.create_match(match_id="match-memory", display_name="Memory", mode="solo")
        store.create_perspective(
            "match-memory", "agent-memory", perspective_id="perspective-memory",
        )
        scope = MemoryScope("match-memory", "agent-memory", "perspective-memory")
        journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
        body = "bounded durable strategic cognition " * 90
        for index in range(120):
            records = {
                "belief": {"topic": f"belief-{index:03d}", "content": body,
                           "confidence": "uncertain"},
                "claim": {"topic": f"claim-{index:03d}", "content": body,
                          "status": "unverified"},
                "relationship": {"actor_id": f"actor-{index:03d}", "reasons": [body]},
                "commitment": {"commitment_key": f"promise-{index:03d}",
                               "title": body, "status": "accepted"},
                "goal": {"goal_key": f"goal-{index:03d}", "title": body,
                         "status": "active", "priority": index % 10},
                "plan": {"plan_key": f"plan-{index:03d}", "title": body,
                         "status": "active"},
                "summary": {"section": f"section-{index:03d}", "content": body},
            }
            for kind, record in records.items():
                journal.append(scope, f"memory.{kind}", {
                    "record": record, "record_input": record,
                }, turn=index)
            journal.append(scope, "chat.message", {
                "message_uid": f"message-{index:03d}", "direction": "inbound",
                "sender_actor_id": f"actor-{index:03d}", "content": body,
            }, turn=index)
            journal.append(scope, "game.action", {
                "summary": body, "stable_ref": f"base-safe-{index:03d}",
            }, turn=index)
        journal.append(scope, "observation.native_event", {
            "native_vehicle_id": 987654, "engine_id": 456789,
            "private_marker": "collector-private-honeytoken",
        }, turn=121)
        journal.append(scope, "observation.semantic_event", {
            "event_kind": "contact_lost", "contact_ref": "contact-safe",
            "location_ref": "location-safe", "provenance": "direct_observation",
        }, turn=121)
        journal.append(scope, "game.action", {
            "summary": "historical exploration receipt", "choice_id": "choice-public",
            "choice_parameters": {"command": "auto_explore_unit", "id": "private-selector-honeytoken",
                "unit_id": 987654, "target_tile_id": 456789, "source_prototype_id": 777777,
                "native_option_id": 8, "own_unit_ref": "own-unit-safe", "target_location_ref": "location-safe"},
            "native_result": {"private_marker": "private-result-honeytoken"},
        }, turn=122)

        smacx_controller.PLATFORM_DB_PATH = store.path
        smacx_controller._store_instance = None
        smacx_controller._store_instance_path = None
        smacx_controller._journal_instance = None
        smacx_controller._journal_instance_root = None

        common = {
            "session_id": "", "agent_id": scope.agent_id,
            "perspective_id": scope.perspective_id,
        }
        actions = (
            "claims", "beliefs", "relationships", "commitments", "goals",
            "plans", "summaries", "chat", "events",
        )
        measured: dict[str, int] = {}
        for action in actions:
            result = smacx_controller.read_platform_memory(
                action, scope.match_id, limit=1000, **common,
            )
            assert result.get("ok"), (action, result)
            assert result.get("result_token_ceiling") == 2048, (action, result)
            measured[action] = _tokens(result)
            assert measured[action] <= 2300, (action, measured[action])
            assert result.get("truncated") is True and result.get("next_cursor")
        events = smacx_controller.read_platform_memory(
            "events", scope.match_id, limit=1000, **common,
        )
        serialized_events = json.dumps(events, separators=(",", ":"))
        assert "collector-private-honeytoken" not in serialized_events
        assert "native_vehicle_id" not in serialized_events and "engine_id" not in serialized_events

        searched = smacx_controller.read_platform_memory(
            "search", scope.match_id, query="bounded durable strategic cognition",
            limit=1000, **common,
        )
        assert searched.get("ok") and searched.get("result_token_ceiling") == 2048
        assert _tokens(searched) <= 2300 and searched.get("next_cursor")
        assert all(len(str(item.get("abstract") or "")) <= 240
                   for item in searched.get("items", ()))
        recalled = smacx_controller.read_platform_memory(
            "recall", scope.match_id,
            queries=({"query": "bounded durable strategic cognition", "limit": 100},),
            total_token_budget=2048, limit=1000, **common,
        )
        assert recalled.get("ok")
        recall = recalled["recall"]
        assert int(recall["estimated_tokens"]) <= 2048
        assert int(recall["token_budget"]) == 2048 and recall["truncated"] is True
        working = smacx_controller.read_platform_memory(
            "working_set", scope.match_id, **common,
        )
        assert working.get("ok") and working.get("authority") == "campaign_journal"
        # The working set is independently section-budgeted. Raw campaign
        # storage may be large without becoming proportional provider context.
        assert _tokens(working) < 16_000
        public_history = [working, events,
            smacx_controller.read_platform_memory("search", scope.match_id,
                query="historical exploration receipt", **common),
            smacx_controller.read_platform_memory("recall", scope.match_id,
                queries=({"query":"historical exploration receipt"},), **common)]
        for view in public_history:
            rendered = json.dumps(view)
            for private in ["private-selector-honeytoken", "private-result-honeytoken",
                            "987654", "456789", "777777", "native_option_id"]:
                assert private not in rendered, (private, view)
            assert "choice-public" in rendered
        assert "own-unit-safe" in json.dumps(working)
        assert "location-safe" in json.dumps(working)
        # Filtering occurs before search matching, not just after rendering.
        assert journal.search(scope, "private-selector-honeytoken")
        hidden_search = smacx_controller.read_platform_memory("search", scope.match_id,
            query="private-selector-honeytoken", **common)
        assert hidden_search["items"] == [], hidden_search
        hidden_recall = smacx_controller.read_platform_memory("recall", scope.match_id,
            queries=({"query":"private-selector-honeytoken"},), **common)
        assert hidden_recall["recall"]["groups"][0]["matches"] == []
        # Provider projection must never rewrite the authoritative old record.
        assert journal.replay(scope)["recent_actions"][-1]["choice_parameters"]["unit_id"] == 987654

    print(json.dumps({"event": "pass", "payload": {
        "records_per_projection": 120,
        "all_typed_projections_bounded": True,
        "chat_bounded": True, "events_bounded_and_provider_safe": True,
        "search_abstract_only": True, "recall_shared_budget": True,
        "working_set_not_proportional_to_storage": True,
        "maximum_read_tokens": max(measured.values()),
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
