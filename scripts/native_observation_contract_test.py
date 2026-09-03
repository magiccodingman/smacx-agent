#!/usr/bin/env python3
"""Bounded native observation-ring and continuity-gap integration contract."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import time

from smacx_attention import AttentionService
from smacx_journal import CampaignJournal
from smacx_observation import ObservationCollector
from smacx_store import MemoryScope, SmacxStore
from smacx_world_store import WorldStore


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    source = (ROOT / "bridge/src/agent_bridge.cpp").read_text(encoding="utf-8")
    collector_source = (ROOT / "src/smacx_observation.py").read_text(encoding="utf-8")
    manager_source = (ROOT / "src/smacx_worker_manager.py").read_text(encoding="utf-8")
    required = (
        "const size_t MaxObservationEvents = 1024;",
        "bool incomplete = after && after + 1 < oldest;",
        "reconciliation_required",
        'append_observation_event("perspective_changed"',
        'append_observation_event(outbound ? "chat_outbound" : "chat_inbound"',
        'append_observation_event("deferred_action_queued"',
        'if (op == "observation_feed") return observation_feed_response(request);',
    )
    if any(value not in source for value in required):
        raise AssertionError("native observation ring or overflow contract drifted")
    if any(value not in collector_source for value in (
        'self._semantic_items("list_bases"',
        'self._semantic_items("list_units"',
        'self.bridge_call("list_factions")',
        'self.bridge_call("list_technologies")',
    )):
        raise AssertionError("world reconciliation no longer uses valid bridge operation names")
    if any(value not in manager_source for value in (
        'get("io.smacx.session")',
        'f"SMACX_AGENT_MATCH_ID={spec[\'match_id\']}"',
        'f"SMACX_AGENT_SESSION_ID={session_id}"',
    )):
        raise AssertionError("MCP sidecar identity wiring drifted")

    with tempfile.TemporaryDirectory(prefix="smacx-native-observation-") as raw:
        root = Path(raw)
        store = SmacxStore(root / "state.sqlite3")
        store.ensure_agent("agent-observation", "Observation")
        store.create_match(match_id="match-observation", display_name="Observation", mode="solo")
        store.create_perspective("match-observation", "agent-observation",
                                 perspective_id="perspective-observation")
        scope = MemoryScope("match-observation", "agent-observation",
                            "perspective-observation")
        journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
        worlds = WorldStore(store, root / "world-snapshots")
        attention = AttentionService(store, journal, scope)
        collector = ObservationCollector(
            scope=scope, session_id="session-observation",
            bridge_call=lambda *_args, **_kwargs: {}, journal=journal,
            world_store=worlds, attention=attention,
        )
        collector._append_native_feed({
            "continuity": "incomplete", "next_sequence": 1500,
            "lost_after_observation_sequence": 475,
            "events": [{"sequence": 1499, "kind": "chat_inbound", "turn": 30,
                        "subject_a": 2, "subject_b": 1}],
        })
        replay = journal.events_after(scope)
        kinds = [event["event_type"] for event in replay]
        assert "observation.continuity_gap" in kinds
        assert "observation.native_event" in kinds
        assert collector.native_after_sequence == 1500
        with store._connect() as connection:
            projected = connection.execute(
                "SELECT payload_json FROM world_observation_projection ORDER BY "
                "observation_sequence",
            ).fetchall()
        assert len(projected) == 2
        assert json.loads(projected[0]["payload_json"])["reconciliation_required"] is True
        assert attention.pending_summary()["has_critical"] is True

        # A provider request may demand a fresh projection while the observer
        # thread is polling. Both paths must share one serialized collector.
        active = 0
        maximum_active = 0
        guard = threading.Lock()

        def simulated_collection() -> dict:
            nonlocal active, maximum_active
            with guard:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.03)
            with guard:
                active -= 1
            return {"ok": True}

        collector._collect_once_locked = simulated_collection  # type: ignore[method-assign]
        threads = [threading.Thread(target=collector.collect_once) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(1)
        assert all(not thread.is_alive() for thread in threads)
        assert maximum_active == 1

    print(json.dumps({"event": "pass", "payload": {
        "bounded_native_ring": True,
        "overflow_explicit": True,
        "continuity_gap_journaled": True,
        "projection_marks_reconciliation": True,
        "overflow_enters_critical_attention": True,
        "chat_and_deferred_actions_emit_events": True,
        "collector_bridge_operations_valid": True,
        "managed_sidecar_identity_explicit": True,
        "collector_refresh_serialized": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
