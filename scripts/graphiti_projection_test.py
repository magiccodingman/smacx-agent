#!/usr/bin/env python3
"""Contained regression for optional, per-perspective Graphiti projection."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile

from smacx_graphiti import GraphEpisode, GraphitiProjector
from smacx_journal import CampaignJournal
from smacx_store import MemoryScope, SmacxStore


class FakeSink:
    def __init__(self) -> None:
        self.episodes: list[GraphEpisode] = []
        self.cleared: list[str] = []
        self.fail_on_call: int | None = None
        self.calls = 0

    async def add_episode(self, episode: GraphEpisode) -> None:
        self.calls += 1
        if self.fail_on_call == self.calls:
            raise RuntimeError("contained sink failure")
        self.episodes.append(episode)

    async def clear_group(self, group_id: str) -> None:
        self.cleared.append(group_id)
        self.episodes = [episode for episode in self.episodes if episode.group_id != group_id]

    async def search(self, _group_id: str, _query: str, _limit: int) -> list[dict]:
        return []

    async def close(self) -> None:
        return None


async def exercise() -> dict:
    with tempfile.TemporaryDirectory(prefix="smacx-graphiti-test-") as temporary:
        store = SmacxStore(Path(temporary) / "smacx.sqlite3")
        store.ensure_agent("agent-graph-alpha", "Alpha")
        store.ensure_agent("agent-graph-beta", "Beta")
        store.create_match(match_id="match-graph-test", display_name="Graph Test", mode="lan")
        store.create_perspective(
            "match-graph-test", "agent-graph-alpha", perspective_id="perspective-graph-alpha",
        )
        store.create_perspective(
            "match-graph-test", "agent-graph-beta", perspective_id="perspective-graph-beta",
        )
        alpha = MemoryScope("match-graph-test", "agent-graph-alpha", "perspective-graph-alpha")
        beta = MemoryScope("match-graph-test", "agent-graph-beta", "perspective-graph-beta")
        journal = CampaignJournal(
            Path(temporary) / "campaigns", timeline_resolver=store.active_timeline_id,
        )
        alpha_events = [
            journal.append(
                alpha,
                "diplomacy.contact",
                {"counterpart": "Morgan", "observation": "made contact"},
                turn=1,
                year=2101,
            )["event_id"],
            journal.append(
                alpha,
                "chat.message",
                {"speech": "Morgan claims peaceful intent", "untrusted": True},
                turn=2,
                year=2102,
            )["event_id"],
            journal.append(
                alpha,
                "memory.belief",
                {"belief": "Morgan may be sincere", "confidence": 0.6},
                turn=2,
                year=2102,
            )["event_id"],
        ]
        journal.append(
            alpha, "unit.moved", {"unit": 12, "tile": 44},
            turn=2, year=2102,
        )
        journal.append(
            beta,
            "strategy.secret",
            {"intent": "attack Alpha"},
        )

        sink = FakeSink()
        sink.fail_on_call = 2
        projector = GraphitiProjector(store, sink)
        failed = await projector.run_once(alpha, limit=10)
        if failed.get("projected") != 1 or failed.get("failed_event_id") != alpha_events[1]:
            raise AssertionError(f"projection failure did not stop at exact cursor: {failed}")
        cursor = store.projection_cursor(alpha, projector.projector_name)
        if cursor.get("last_event_id") != alpha_events[0] or cursor.get("status") != "error":
            raise AssertionError(f"failure advanced or lost cursor: {cursor}")

        sink.fail_on_call = None
        resumed = await projector.run_once(alpha, limit=10)
        if not resumed.get("ok") or resumed.get("projected") != 2 or resumed.get("skipped") != 1:
            raise AssertionError(f"projection did not resume: {resumed}")
        if len(sink.episodes) != 3:
            raise AssertionError("projected event count is wrong")
        alpha_namespace = store.graph_namespace(alpha)
        if any(episode.group_id != alpha_namespace for episode in sink.episodes):
            raise AssertionError("episode crossed a Graphiti group namespace")
        bodies = [json.loads(episode.body) for episode in sink.episodes]
        if any(body["fair_play_scope"]["perspective_id"] != alpha.perspective_id for body in bodies):
            raise AssertionError("episode body crossed fair-play perspective")
        if any("Secret Beta attack plan" in episode.body for episode in sink.episodes):
            raise AssertionError("another perspective leaked into Graphiti")

        latest = journal.events_after(alpha, limit=10)[-1]
        deterministic = projector.episode_for_event(alpha, latest)
        deterministic_again = projector.episode_for_event(alpha, latest)
        if deterministic.episode_uuid != deterministic_again.episode_uuid:
            raise AssertionError("episode UUID is not replay-stable")

        rebuilt = await projector.rebuild(alpha, limit=2)
        if not rebuilt.get("ok") or rebuilt.get("projected") != 3 or rebuilt.get("skipped") != 1 \
                or sink.cleared != [alpha_namespace] or len(sink.episodes) != 3:
            raise AssertionError(f"isolated rebuild failed: {rebuilt} / {sink.cleared}")
        if store.projection_cursor(beta, projector.projector_name).get("status") != "new":
            raise AssertionError("Alpha rebuild mutated Beta cursor")

        checkpoint = journal.append(
            alpha, "checkpoint.native", {"turn": 2}, turn=2, year=2102,
        )
        journal.append(
            alpha, "memory.belief", {"belief": "future branch must disappear"},
            turn=3, year=2103,
        )
        restored_timeline = "timeline-graph-restore"
        journal.fork_timeline(
            alpha, restored_timeline, native_save_sha256="b" * 64,
            from_event_hash=checkpoint["event_hash"],
            parent_timeline_id="timeline-main",
        )
        with store.transaction() as connection:
            connection.execute(
                "UPDATE matches SET metadata_json=json_set(metadata_json, "
                "'$.active_memory_timeline', ?) WHERE match_id=?",
                (restored_timeline, alpha.match_id),
            )
        restored_namespace = store.graph_namespace(alpha)
        replaced = await projector.replace_timeline(
            alpha, retired_namespaces=[alpha_namespace], limit=2,
        )
        if not replaced.get("ok") \
                or replaced.get("retired_namespaces_cleared") != [alpha_namespace] \
                or sink.cleared[-2:] != [restored_namespace, alpha_namespace] \
                or any(item.group_id != restored_namespace for item in sink.episodes) \
                or any("future branch must disappear" in item.body for item in sink.episodes):
            raise AssertionError(f"timeline graph replacement was not atomic: {replaced}")

        return {
            "event_scope_isolated": True,
            "failure_does_not_advance": True,
            "resume_from_cursor": True,
            "stable_episode_ids": True,
            "group_rebuild_isolated": True,
            "sqlite_projection_cursor_is_rebuildable": True,
            "routine_events_excluded": True,
            "restored_timeline_rebuilt_before_retired_graph_gc": True,
        }


def main() -> int:
    payload = asyncio.run(exercise())
    print(json.dumps({"event": "pass", "payload": payload}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
