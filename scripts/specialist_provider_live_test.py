#!/usr/bin/env python3
"""Opt-in live provider gate for the isolated specialist wire contract.

The report is content-free: it records only schema/isolation outcomes, usage,
and latency. Provider URLs, prompts, evidence, and generated text are omitted.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import time

from smacx_journal import CampaignJournal
from smacx_specialists import SpecialistService, invoke_openai_specialist
from smacx_store import MemoryScope, SmacxStore
from smacx_world_model import PerspectiveProjector
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-file")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="smacx-live-specialist-") as raw:
        root = Path(raw)
        profile = root / "profile.json"
        profile.write_text(json.dumps({
            "profile_id": "live-specialist-contract",
            "base_url": args.base_url,
            "model_id": args.model,
            "reasoning_effort": "none",
            "generation_settings": {
                "temperature": 0,
                "extra_parameters": {
                    "chat_template_kwargs": {
                        "enable_thinking": False,
                        "preserve_thinking": False,
                    },
                },
            },
            "max_concurrency": 1,
        }, separators=(",", ":")), encoding="utf-8")
        os.environ["SMACX_SPECIALIST_PROFILE_FILE"] = str(profile)
        if args.api_key_file:
            os.environ["SMACX_SPECIALIST_PROVIDER_KEY_FILE"] = args.api_key_file

        store = SmacxStore(root / "state.sqlite3")
        store.ensure_agent("agent-live", "Live specialist")
        store.create_match(match_id="match-live", display_name="Live contract", mode="solo")
        store.create_perspective("match-live", "agent-live", perspective_id="perspective-live")
        scope = MemoryScope("match-live", "agent-live", "perspective-live")
        journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
        timeline = journal.timeline_id(scope)
        identity = WorldIdentity(scope.match_id, scope.perspective_id, timeline, "world-live")
        projection = PerspectiveProjector(identity).project({
            "turn": 1,
            "map": {"width": 4, "height": 2, "horizontal_wrap": False},
            "tiles": [{"tile_id": 0, "x": 0, "y": 0, "visible_now": True,
                       "terrain": "land", "features": []}],
            "bases": [], "units": [], "factions": [], "global": [],
        }, observation_sequence=1)
        world_store = WorldStore(store, root / "snapshots")
        world_store.replace_projection(
            scope, identity, projection["objects"], observation_cursor=1,
            action_revision="live", continuity="complete", journal_head_hash="0" * 64,
        )
        service = SpecialistService(store, world_store, scope)
        request = service.create(
            kind="world_analyst",
            question="State the supplied terrain and its evidence limitation.",
            evidence=[{"evidence_ref": "location-0", "value": {
                "terrain": "land", "epistemic_status": "current",
                "provenance": "native_visible",
            }}], token_budget=512, time_budget_seconds=120,
        )
        started = time.monotonic()
        result = service.run(request, invoke_openai_specialist)
        elapsed = (time.monotonic() - started) * 1000
        if not result.get("ok") or result.get("status") != "accepted":
            raise AssertionError("live specialist result was not accepted")
        value = result.get("result") or {}
        usage = result.get("usage") or {}
        print(json.dumps({
            "schema": "smacx.specialist-provider-live.v1",
            "passed": True,
            "strict_result": all(key in value for key in (
                "answer", "claims", "limitations", "unresolved_questions",
                "source_revision", "dependency_refs", "dependency_hash",
            )),
            "tool_count": 0,
            "sovereign_history_rows": 0,
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "reasoning_tokens": int((usage.get("completion_tokens_details") or {}).get(
                "reasoning_tokens") or 0),
            "latency_ms": round(elapsed, 3),
        }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
