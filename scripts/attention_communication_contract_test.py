#!/usr/bin/env python3
"""At-least-once attention and serialized communication acceptance contracts."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from smacx_attention import AttentionError, AttentionService
from smacx_journal import CampaignJournal
from smacx_store import MemoryScope, SmacxStore


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
        operation = restarted.upsert_operation(
            operation_id=None, kind="front_defense", objective="Hold the current front",
            referenced_world_objects=[], source_world_revision=1,
            source_world_epoch="world-attention", source_dependency_hash="empty",
            current_turn=1,
        )
        try:
            other.upsert_operation(
                operation_id=operation["operation_id"], kind="front_defense",
                objective="Cross-scope overwrite", referenced_world_objects=[],
                source_world_revision=1, source_world_epoch="world-other",
                source_dependency_hash="empty", current_turn=1,
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

    print(json.dumps({"event": "pass", "payload": {
        "mid_provider_capture_isolated": True,
        "failed_invocation_redelivers_same_ids": True,
        "restart_redelivery": True,
        "batched_acknowledgement": True,
        "independent_attention_cursor": True,
        "communication_serializes_sovereign": True,
        "chat_deduplication": True,
        "attention_operation_scope_isolation": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
