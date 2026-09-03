#!/usr/bin/env python3
"""Request-only runtime context, focus, tier, and acknowledgement contracts."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from smacx_attention import AttentionService
from smacx_journal import CampaignJournal
from smacx_runtime_context import RuntimeContextAssembler
from smacx_store import MemoryScope, SmacxStore
from smacx_world import WorldService
from smacx_world_model import PerspectiveProjector
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity, content_hash


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-runtime-context-") as raw:
        root = Path(raw)
        store = SmacxStore(root / "state.sqlite3")
        store.ensure_agent("agent-runtime", "Runtime")
        store.create_match(match_id="match-runtime", display_name="Runtime", mode="solo")
        store.create_perspective("match-runtime", "agent-runtime",
                                 perspective_id="perspective-runtime")
        scope = MemoryScope("match-runtime", "agent-runtime", "perspective-runtime")
        journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
        worlds = WorldStore(store, root / "world-snapshots")
        identity = WorldIdentity(scope.match_id, scope.perspective_id,
                                 journal.timeline_id(scope), "world-runtime")
        bundle = {
            "turn": 9, "year": 2109, "action_revision": "action-9",
            "map": {"width": 16, "height": 8, "horizontal_wrap": True},
            "tiles": [{"tile_id": 0, "x": 0, "y": 0, "terrain": "land",
                       "visible_now": True}],
            "bases": [{"id": 0, "base_ref": "base-home", "tile_id": 0,
                       "owned": True, "name": "Home", "population": 3}],
            "units": [{"id": 7, "own_unit_ref": "own-unit-7", "tile_id": 0,
                       "owned": True, "name": "Scout"}],
            "factions": [{"id": 1, "faction_ref": "faction-1", "owned": True}],
        }
        projected = PerspectiveProjector(identity).project(bundle, observation_sequence=9)
        worlds.replace_projection(scope, identity, projected["objects"], observation_cursor=9,
                                  action_revision="action-9", continuity="complete",
                                  journal_head_hash="0" * 64)
        attention = AttentionService(store, journal, scope)
        operation = attention.upsert_operation(
            operation_id=None, kind="front_defense", objective="Compare reserve response",
            referenced_world_objects=["base-home", "own-unit-7"],
            source_world_revision=1, source_world_epoch="world-runtime",
            source_dependency_hash=content_hash({"base-home": "test", "own-unit-7": "test"}),
            current_turn=9,
        )
        critical = attention.enqueue(
            "chat", {"message": {"message_uid": "message-critical",
                                  "content": "A player claim, not a native fact."}},
            observation_cursor=9, priority=100, critical=True,
        )
        snapshot = {
            "turn": 9, "revision": "action-9",
            "protocol": {"phase": "interaction", "required_action": "respond"},
            "interaction": {"kind": "proposal", "popup_label": "Treaty offer",
                            "faction_id": 2},
        }
        working = {"sections": {
            "goals": [
                {"goal_key": "survive", "title": "Survive", "status": "active",
                 "priority": 100, "description": "Hold under pressure " * 400},
                *({"goal_key": f"dead-{index}", "title": "Dead history",
                   "status": "completed", "priority": 100}
                  for index in range(40)),
            ],
            "plans": [
                {"plan_key": "reserve", "title": "Keep one reserve", "status": "active"},
                *({"plan_key": f"done-{index}", "title": "Done", "status": "completed"}
                  for index in range(40)),
            ],
            "commitments": [
                *({"commitment_key": f"resolved-{index}", "title": "Resolved",
                   "terms": "no longer live", "status": "fulfilled",
                   "created_unix": 1000 + index} for index in range(40)),
                {"commitment_key": "old-binding-promise", "title": "Old binding promise",
                 "terms": "Meet at the frontier and defend the ally.", "status": "accepted",
                 "created_unix": 1, "due_turn": 10},
                {"commitment_key": "new-proposal", "title": "New proposal",
                 "terms": "Possible exchange", "status": "proposed",
                 "created_unix": 9999},
            ],
            "relationships": [
                {"actor_id": f"actor-{index}", "reasons": "r" * 1200,
                 "updated_unix": 1000 - index} for index in range(30)
            ],
            "beliefs": [
                {"topic": f"belief-{index}", "content": "b" * 1200,
                 "updated_unix": 1000 - index} for index in range(30)
            ],
            "situation": {"summaries": [
                {"content": "s" * 1200, "updated_unix": 1000 - index}
                for index in range(20)
            ]},
        }}
        assembler = RuntimeContextAssembler(
            scope=scope, world=WorldService(worlds, scope), attention=attention,
            snapshot=lambda: snapshot, working_state=lambda: working,
            interpretive_recall=lambda _query: {"ok": False, "facts": []},
        )
        compact = assembler.build(episode_id="episode-runtime-64k",
                                  episode_mode="gameplay", context_length=65536)
        rich = assembler.build(episode_id="episode-runtime-256k",
                               episode_mode="gameplay", context_length=262144)
        assert compact["identity"] == rich["identity"]
        assert compact["focus"]["focus_id"] == rich["focus"]["focus_id"]
        assert compact["focus"]["mandatory"] is True
        assert any(item["attention_id"] == critical["attention_id"]
                   for item in compact["attention"]["items"])
        assert compact["token_estimate"] <= 13_107
        assert rich["token_estimate"] <= 32_768
        assert compact["working_cognition"]["commitments"] == \
            rich["working_cognition"]["commitments"]
        cognition_text = json.dumps(compact["working_cognition"])
        assert "old-binding-promise" in cognition_text
        assert "survive" in cognition_text
        assert "resolved-" not in cognition_text and "dead-" not in cognition_text
        assert "actor-0" in cognition_text  # newest head survives tail trimming
        assert compact["operations"][0]["operation_id"] == operation["operation_id"]

        lease_id = compact["attention"]["attention_lease_id"]
        attention.placed(lease_id)
        attention.responded(lease_id)
        attention.acknowledge(lease_id, through_cursor=compact["attention"]["through_cursor"])
        post_ack = assembler.build(episode_id="episode-runtime-after-ack",
                                   episode_mode="gameplay", context_length=65536)
        assert post_ack["attention"]["items"] == []
        assert post_ack["focus"]["focus_id"] == compact["focus"]["focus_id"]
        assert post_ack["focus"]["mandatory"] is True
        attention.complete_operation(operation["operation_id"], "Reserve comparison complete")
        after_operation = assembler.build(episode_id="episode-runtime-operation-complete",
                                          episode_mode="gameplay", context_length=65536)
        assert after_operation["operations"] == []

        communication = assembler.build(episode_id="episode-runtime-communication",
                                        episode_mode="communication", context_length=65536)
        assert communication["episode"]["mutation_authority"] is False
        assert communication["identity"] == compact["identity"]

        # An end-to-end Huge-chaotic rich runtime leaves room for focus,
        # cognition, identity, attention and operations around a maximal anchor.
        width, height = 320, 160
        huge_tiles = [
            {"tile_id": (x + width * y) // 2, "x": x, "y": y,
             "terrain": "land", "visible_now": True}
            for y in range(height) for x in range(y & 1, width, 2)
        ]
        huge_units = [
            {"id": 1000 + index, "native_observation_key": f"enemy-{index}",
             "tile_id": huge_tiles[index * 137 % len(huge_tiles)]["tile_id"],
             "owned": False, "name": f"Contact {index}", "owner_ref": "faction-2"}
            for index in range(420)
        ]
        huge_bases = [
            {"id": index, "base_ref": f"base-chaos-{index}",
             "tile_id": huge_tiles[index * 211 % len(huge_tiles)]["tile_id"],
             "owned": index == 0, "visible_now": True,
             "name": f"Base {index}", "owner_ref": f"faction-{1 + index % 7}"}
            for index in range(120)
        ]
        huge_bundle = {
            **bundle, "map": {"width": width, "height": height,
                               "horizontal_wrap": True},
            "tiles": huge_tiles, "units": huge_units, "bases": huge_bases,
            "global": [{"object_ref": f"project-{index}", "kind": "project",
                        "source": "public_report", "name": f"Project {index}",
                        "state": "announced"} for index in range(40)],
        }
        huge_projection = PerspectiveProjector(identity).project(
            huge_bundle, observation_sequence=10,
        )
        worlds.replace_projection(
            scope, identity, huge_projection["objects"], observation_cursor=10,
            action_revision="action-huge", continuity="complete",
            journal_head_hash="0" * 64,
        )
        huge_runtime = assembler.build(
            episode_id="episode-runtime-huge-chaotic",
            episode_mode="gameplay", context_length=262144,
        )
        assert huge_runtime["token_estimate"] <= huge_runtime["budget"]["total"]
        assert huge_runtime["token_composition"]["anchor"] <= 16_000
        assert huge_runtime["working_cognition"]["commitments"]

    print(json.dumps({"event": "pass", "payload": {
        "64k_and_256k_same_truth": True,
        "tier_specific_detail_budget": True,
        "critical_attention_pinned": True,
        "acknowledgement_does_not_remove_focus": True,
        "durable_commitment_pinned": True,
        "communication_same_sovereign_read_only": True,
        "runtime_token_composition_observed": True,
        "operation_retained_then_collected": True,
        "large_cognition_live_selection": True,
        "huge_chaotic_256k_runtime_bounded": True,
        "runtime_tokens_64k": compact["token_estimate"],
        "runtime_tokens_256k": rich["token_estimate"],
        "huge_chaotic_runtime_tokens_256k": huge_runtime["token_estimate"],
        "huge_chaotic_anchor_tokens_256k":
            huge_runtime["token_composition"]["anchor"],
        "huge_chaotic_cognition_tokens_256k":
            huge_runtime["token_composition"]["cognition"],
        "huge_chaotic_attention_tokens_256k":
            huge_runtime["token_composition"]["attention"],
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
