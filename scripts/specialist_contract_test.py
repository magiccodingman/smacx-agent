#!/usr/bin/env python3
"""Deterministic isolation, staleness, concurrency, and lifecycle gates."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from smacx_journal import CampaignJournal
from smacx_specialists import SpecialistError, SpecialistService
from smacx_store import MemoryScope, SmacxStore
from smacx_world_model import PerspectiveProjector
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-specialist-") as raw:
        root = Path(raw)
        store = SmacxStore(root / "state.sqlite3")
        store.ensure_agent("agent-specialist", "Specialist")
        store.create_match(match_id="match-specialist", display_name="Test", mode="solo")
        store.create_perspective("match-specialist", "agent-specialist",
                                 perspective_id="perspective-specialist")
        scope = MemoryScope("match-specialist", "agent-specialist", "perspective-specialist")
        journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
        timeline = journal.timeline_id(scope)
        identity = WorldIdentity(scope.match_id, scope.perspective_id, timeline, "world-specialist")
        projection = PerspectiveProjector(identity).project({
            "turn": 1, "map": {"width": 4, "height": 2, "horizontal_wrap": False},
            "tiles": [{"tile_id": 0, "x": 0, "y": 0, "visible_now": True,
                       "terrain": "land", "features": []}],
            "bases": [], "units": [], "factions": [], "global": [],
        }, observation_sequence=1)
        world_store = WorldStore(store, root / "snapshots")
        world_store.replace_projection(
            scope, identity, projection["objects"], observation_cursor=1,
            action_revision="a", continuity="complete", journal_head_hash="0" * 64,
        )
        service = SpecialistService(store, world_store, scope)
        request = service.create(
            kind="world_analyst", question="Summarize the supplied location mechanics.",
            evidence=[{"evidence_ref": "location-0", "value": {"terrain": "land"}}],
        )
        try:
            service.create(kind="world_analyst", question="Concurrent", evidence=[])
            raise AssertionError("second live child was admitted")
        except SpecialistError as exc:
            assert str(exc) == "specialist_concurrency_limit"

        captured = {}
        def invoke(prompt, payload):
            captured["prompt"] = prompt
            captured["payload"] = payload
            assert "personality" not in prompt.lower()
            assert "honeytoken" not in json.dumps(payload).lower()
            return {
                "specialist_job_id": payload["specialist_job_id"],
                "answer": "One currently supplied known land location.",
                "claims": [{"claim": "The supplied location is land.",
                            "evidence_refs": ["location-0"],
                            "epistemic_status": "current"}],
                "limitations": ["Only supplied evidence was considered."],
                "unresolved_questions": [],
                "source_revision": payload["identity"]["world_revision"],
                "dependency_refs": payload["dependency_refs"],
                "dependency_hash": payload["dependency_hash"],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                "provider_profile": {"profile_id": "test-helper", "model_id": "test-model"},
            }
        result = service.run(request, invoke)
        assert result["ok"] and result["status"] == "accepted"
        assert set(captured["payload"]) >= {"immutable_evidence", "identity", "token_budget"}

        future = service.create(
            kind="world_analyst", question="This result must become stale.",
            evidence=[{"evidence_ref": "location-0", "value": {"terrain": "land"}}],
        )
        changed = PerspectiveProjector(identity).project({
            "turn": 1, "map": {"width": 4, "height": 2, "horizontal_wrap": False},
            "tiles": [{"tile_id": 0, "x": 0, "y": 0, "visible_now": True,
                       "terrain": "ocean", "features": []}],
            "bases": [], "units": [], "factions": [], "global": [],
        }, observation_sequence=2)
        world_store.replace_projection(
            scope, identity, changed["objects"], observation_cursor=2,
            action_revision="b", continuity="complete", journal_head_hash="1" * 64,
        )
        stale = service.run(future, invoke)
        assert not stale["ok"] and stale["status"] == "stale"

        retryable = service.create(
            kind="reference_researcher", question="Exercise crash and retry.",
            evidence=[{"evidence_ref": "location-0", "value": {"terrain": "ocean"}}],
        )
        try:
            service.run(retryable, lambda _prompt, _payload: (_ for _ in ()).throw(
                RuntimeError("simulated child crash")))
            raise AssertionError("specialist crash did not fail the job")
        except RuntimeError as exc:
            assert str(exc) == "simulated child crash"
        service.retry(retryable["specialist_job_id"])
        retried = service.run(retryable, invoke)
        assert retried["ok"] and retried["status"] == "accepted"

    print(json.dumps({"event": "pass", "payload": {
        "read_only_child_prompt": True, "immutable_evidence": True,
        "single_child_limit": True, "strict_citations": True,
        "dependency_staleness": True, "durable_job_result": True,
        "child_crash_retry": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
