#!/usr/bin/env python3
"""Contained regression for optional, per-perspective Graphiti projection."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile

from smacx_graphiti import GraphEpisode, GraphitiProjector
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
        alpha_events = [
            store.append_event(
                alpha,
                "diplomacy.contact",
                {"counterpart": "Morgan", "observation": "made contact"},
                turn=1,
                year=2101,
                search_text="Contacted Morgan",
            ),
            store.append_event(
                alpha,
                "chat.message",
                {"speech": "Morgan claims peaceful intent", "untrusted": True},
                turn=2,
                year=2102,
                search_text="Morgan claims peaceful intent",
            ),
            store.append_event(
                alpha,
                "memory.belief_updated",
                {"belief": "Morgan may be sincere", "confidence": 0.6},
                turn=2,
                year=2102,
                search_text="Morgan may be sincere",
            ),
        ]
        store.append_event(
            beta,
            "strategy.secret",
            {"intent": "attack Alpha"},
            search_text="Secret Beta attack plan",
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
        if not resumed.get("ok") or resumed.get("projected") != 2:
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

        deterministic = projector.episode_for_event(alpha, store.list_events(alpha, limit=10)[-1])
        deterministic_again = projector.episode_for_event(alpha, store.list_events(alpha, limit=10)[-1])
        if deterministic.episode_uuid != deterministic_again.episode_uuid:
            raise AssertionError("episode UUID is not replay-stable")

        rebuilt = await projector.rebuild(alpha, limit=2)
        if not rebuilt.get("ok") or rebuilt.get("projected") != 3 \
                or sink.cleared != [alpha_namespace] or len(sink.episodes) != 3:
            raise AssertionError(f"isolated rebuild failed: {rebuilt} / {sink.cleared}")
        if store.projection_cursor(beta, projector.projector_name).get("status") != "new":
            raise AssertionError("Alpha rebuild mutated Beta cursor")

        return {
            "event_scope_isolated": True,
            "failure_does_not_advance": True,
            "resume_from_cursor": True,
            "stable_episode_ids": True,
            "group_rebuild_isolated": True,
            "sqlite_remains_authoritative": True,
        }


def main() -> int:
    payload = asyncio.run(exercise())
    print(json.dumps({"event": "pass", "payload": payload}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
