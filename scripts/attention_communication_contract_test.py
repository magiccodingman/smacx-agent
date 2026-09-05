#!/usr/bin/env python3
"""At-least-once attention and serialized communication acceptance contracts."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from smacx_attention import AttentionError, AttentionService
from smacx_journal import CampaignJournal
from smacx_store import MemoryScope, SmacxStore
from smacx_world_model import PerspectiveProjector
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity, content_hash


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-attention-") as raw:
        root = Path(raw)
        store = SmacxStore(root / "state.sqlite3")
        store.ensure_agent("agent-attention", "Attention")
        store.create_match(match_id="match-attention", display_name="Attention", mode="lan")
        store.create_perspective("match-attention", "agent-attention",
                                 perspective_id="perspective-attention")
        scope = MemoryScope("match-attention", "agent-attention", "perspective-attention")
        journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
        worlds = WorldStore(store, root / "world-snapshots")
        identity = WorldIdentity(
            scope.match_id, scope.perspective_id, journal.timeline_id(scope), "world-attention",
        )
        projection = PerspectiveProjector(identity).project({
            "turn": 1,
            "map": {"width": 8, "height": 4, "horizontal_wrap": False},
            "tiles": [{"tile_id": 0, "x": 0, "y": 0, "visible_now": True,
                       "terrain": "land"}],
            "bases": [], "units": [], "factions": [], "global": [],
        }, observation_sequence=1)
        worlds.replace_projection(
            scope, identity, projection["objects"], observation_cursor=1,
            action_revision="r1", continuity="complete", journal_head_hash="0" * 64,
        )
        attention = AttentionService(store, journal, scope)

        first = attention.enqueue(
            "world_change", {"change": "base_threat"}, observation_cursor=40,
            priority=95, critical=True, dedupe_key="world:40:base-threat",
        )
        duplicate = attention.enqueue(
            "world_change", {"change": "base_threat"}, observation_cursor=40,
            priority=95, critical=True, dedupe_key="world:40:base-threat",
        )
        assert duplicate["deduplicated"] and duplicate["attention_id"] == first["attention_id"]

        gameplay = attention.acquire_sovereign("episode-gameplay", "gameplay")
        lease = attention.lease("episode-gameplay")
        assert [item["attention_id"] for item in lease["items"]] == [first["attention_id"]]
        attention.placed(lease["attention_lease_id"])

        # A native chat event arriving during provider work is captured but is
        # never smuggled into the already assembled provider request.
        chat = attention.enqueue(
            "chat", {"message": {"message_uid": "chat-mid-provider", "text": "hello"},
                     "untrusted_in_game_speech": True},
            observation_cursor=41, priority=85, critical=True,
            dedupe_key="chat-mid-provider",
        )
        reused = attention.lease("episode-gameplay")
        assert [item["attention_id"] for item in reused["items"]] == [first["attention_id"]]

        # Placement and an aborted provider call do not consume cognition.
        try:
            attention.acknowledge(lease["attention_lease_id"], through_cursor=1)
            raise AssertionError("placed attention was acknowledged without a response")
        except AttentionError:
            pass
        attention.abandon(lease["attention_lease_id"])
        attention.release_sovereign(gameplay, committed=False)

        # Reopening the durable database after a crash redelivers the same IDs.
        restarted = AttentionService(
            SmacxStore(root / "state.sqlite3"),
            CampaignJournal(root / "campaigns",
                            timeline_resolver=store.active_timeline_id), scope,
        )
        communication = restarted.acquire_sovereign("episode-communication", "communication")
        redelivered = restarted.lease("episode-communication")
        ids = [item["attention_id"] for item in redelivered["items"]]
        assert first["attention_id"] in ids and chat["attention_id"] in ids
        assert next(item for item in redelivered["items"]
                    if item["attention_id"] == first["attention_id"])["redelivered"]
        state = restarted.sovereign_state()
        assert state and state["episode_mode"] == "communication"
        try:
            restarted.acquire_sovereign("episode-gameplay-overlap", "gameplay")
            raise AssertionError("communication and gameplay sovereigns overlapped")
        except AttentionError:
            pass

        restarted.placed(redelivered["attention_lease_id"])
        restarted.responded(redelivered["attention_lease_id"])
        store.ensure_agent("agent-attention-other", "Other attention")
        store.create_perspective(
            "match-attention", "agent-attention-other",
            perspective_id="perspective-attention-other",
        )
        other_scope = MemoryScope(
            "match-attention", "agent-attention-other", "perspective-attention-other",
        )
        other = AttentionService(store, journal, other_scope)
        try:
            other.acknowledge(
                redelivered["attention_lease_id"],
                through_cursor=redelivered["through_cursor"],
            )
            raise AssertionError("cross-perspective attention lease was accepted")
        except AttentionError:
            pass
        empty_dependency_hash = content_hash({})
        operation = restarted.upsert_operation(
            operation_id=None, kind="front_defense", objective="Hold the current front",
            referenced_world_objects=[], source_world_revision=1,
            source_world_epoch="world-attention", source_dependency_hash=empty_dependency_hash,
            current_turn=1,
        )
        try:
            other.upsert_operation(
                operation_id=operation["operation_id"], kind="front_defense",
                objective="Cross-scope overwrite", referenced_world_objects=[],
                source_world_revision=1, source_world_epoch="world-other",
                source_dependency_hash=empty_dependency_hash, current_turn=1,
            )
            raise AssertionError("cross-perspective operation update was accepted")
        except AttentionError:
            pass
        acknowledged = restarted.acknowledge(
            redelivered["attention_lease_id"],
            through_cursor=redelivered["through_cursor"],
        )
        assert set(acknowledged["acknowledged_ids"]) == {first["attention_id"], chat["attention_id"]}
        assert acknowledged["attention_cursor"] == 2
        restarted.release_sovereign(communication, committed=True)
        assert restarted.pending_summary()["count"] == 0

        # A later event is independent from both the native observation cursor
        # and the acknowledged attention cursor.
        later = restarted.enqueue("chat", {"message": {"message_uid": "chat-later"}},
                                  observation_cursor=99, critical=True)
        assert later["attention_sequence"] == 3
        later_lease = restarted.lease("episode-later")
        restarted.placed(later_lease["attention_lease_id"])
        restarted.responded(later_lease["attention_lease_id"])
        restarted.acknowledge(
            later_lease["attention_lease_id"],
            through_cursor=later_lease["through_cursor"],
        )

        specialist = restarted.enqueue(
            "specialist_completion", {
                "mission_id": "mission-attention-contract", "faculty": "world",
                "status": "accepted", "preview": "bounded result ready",
            }, observation_cursor=100, priority=80,
            dedupe_key="specialist-completion-contract",
        )
        specialist_token = restarted.acquire_sovereign(
            "episode-specialist-delivery", "gameplay", ttl_seconds=30,
        )
        specialist_lease = restarted.lease("episode-specialist-delivery")
        restarted.placed(specialist_lease["attention_lease_id"])
        restarted.responded(specialist_lease["attention_lease_id"])
        # Simulate a controller/provider process dying after a response but
        # before explicit cognition acknowledgement.
        with store.transaction() as connection:
            connection.execute(
                "UPDATE sovereign_leases SET expires_unix=0 WHERE lease_token_hash IS NOT NULL "
                "AND status='active'",
            )
        after_crash = AttentionService(store, journal, scope)
        recovery_token = after_crash.acquire_sovereign(
            "episode-after-specialist-crash", "gameplay",
        )
        specialist_redelivery = after_crash.lease("episode-after-specialist-crash")
        specialist_item = next(
            item for item in specialist_redelivery["items"]
            if item["attention_id"] == specialist["attention_id"]
        )
        assert specialist_item["redelivered"] is True
        after_crash.placed(specialist_redelivery["attention_lease_id"])
        after_crash.responded(specialist_redelivery["attention_lease_id"])
        after_crash.acknowledge(
            specialist_redelivery["attention_lease_id"],
            through_cursor=specialist_redelivery["through_cursor"],
        )
        after_crash.release_sovereign(recovery_token, committed=True)
        # The dead process token is intentionally unusable after replacement.
        try:
            restarted.release_sovereign(specialist_token, committed=True)
            raise AssertionError("expired sovereign token remained valid")
        except AttentionError:
            pass

        # Oversized runtime burst: leasing is generous, but only the exact
        # serialized subset may be placed. Omitted IDs return to the queue;
        # partially processed placed IDs redeliver with identity intact.
        burst_ids = []
        for index in range(32):
            queued = restarted.enqueue(
                "world_change", {"index": index, "detail": "x" * 2048},
                observation_cursor=100 + index, priority=50 - (index % 10),
                dedupe_key=f"burst-{index}",
            )
            burst_ids.append(queued["attention_id"])
        burst = restarted.lease("episode-burst", limit=64)
        visible = [item["attention_id"] for item in burst["items"][:5]]
        restricted = restarted.restrict_for_placement(
            burst["attention_lease_id"], visible,
        )
        assert restricted["visible_ids"] == visible
        assert set(restricted["requeued_ids"]) == set(burst_ids) - set(visible)
        restarted.placed(burst["attention_lease_id"])
        restarted.responded(burst["attention_lease_id"])
        partial = restarted.acknowledge(
            burst["attention_lease_id"], through_cursor=0,
            acknowledged_ids=visible[:2],
        )
        assert partial["acknowledged_ids"] == visible[:2]
        next_burst = restarted.lease("episode-burst-next", limit=64)
        next_ids = [item["attention_id"] for item in next_burst["items"]]
        assert set(next_ids) == set(burst_ids) - set(visible[:2])
        assert all(item["redelivered"] for item in next_burst["items"]
                   if item["attention_id"] in visible[2:])
        assert all(not item["redelivered"] for item in next_burst["items"]
                   if item["attention_id"] in set(burst_ids) - set(visible))

    print(json.dumps({"event": "pass", "payload": {
        "mid_provider_capture_isolated": True,
        "failed_invocation_redelivers_same_ids": True,
        "restart_redelivery": True,
        "batched_acknowledgement": True,
        "independent_attention_cursor": True,
        "communication_serializes_sovereign": True,
        "chat_deduplication": True,
        "attention_operation_scope_isolation": True,
        "oversized_burst_only_visible_placed": True,
        "partial_ack_requeues_remainder": True,
        "specialist_completion_redelivery": True,
        "responded_without_ack_restart_redelivery": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
