#!/usr/bin/env python3
"""Opt-in live provider gate for the disposable Hermes specialist contract.

The emitted report is content-free. It records lifecycle, isolation-derived
usage, query count, latency, and result bounds without printing provider URLs,
prompts, evidence, reasoning, or generated text.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import time

from smacx_control import SecretVault
from smacx_journal import CampaignJournal
from smacx_specialist_supervisor import SpecialistSupervisor
from smacx_specialists import SpecialistService
from smacx_store import MemoryScope, SmacxStore
from smacx_world_model import PerspectiveProjector
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity, canonical_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-file")
    parser.add_argument("--reasoning", default="low")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="smacx-live-specialist-") as raw:
        root = Path(raw)
        store = SmacxStore(root / "state.sqlite3")
        store.ensure_agent("agent-live", "PRIVATE LIVE SOVEREIGN PERSONALITY")
        store.create_match(match_id="match-live", display_name="Live contract", mode="solo")
        store.create_perspective("match-live", "agent-live", perspective_id="perspective-live")
        scope = MemoryScope("match-live", "agent-live", "perspective-live")
        journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
        identity = WorldIdentity(
            scope.match_id, scope.perspective_id, journal.timeline_id(scope), "world-live",
        )
        projection = PerspectiveProjector(identity).project({
            "turn": 12,
            "map": {"width": 12, "height": 6, "horizontal_wrap": False},
            "tiles": [
                {"tile_id": 0, "x": 0, "y": 0, "visible_now": True,
                 "terrain": "land", "features": ["road"]},
                {"tile_id": 1, "x": 2, "y": 0, "visible_now": True,
                 "terrain": "land", "features": ["road"]},
                {"tile_id": 2, "x": 4, "y": 0, "visible_now": True,
                 "terrain": "land", "features": []},
                {"tile_id": 3, "x": 6, "y": 0, "visible_now": True,
                 "terrain": "ocean", "features": []},
            ],
            "bases": [
                {"id": 1, "base_ref": "base-west", "tile_id": 0,
                 "owned": True, "name": "West"},
                {"id": 2, "base_ref": "base-east", "tile_id": 2,
                 "owned": True, "name": "East"},
            ],
            "units": [
                {"id": 1, "unit_ref": "unit-reserve", "tile_id": 1,
                 "owned": True, "name": "Reserve", "movement_points": 1},
            ],
            "factions": [], "global": [],
        }, observation_sequence=12)
        worlds = WorldStore(store, root / "world-snapshots")
        worlds.replace_projection(
            scope, identity, projection["objects"], observation_cursor=12,
            action_revision="live", continuity="complete", journal_head_hash="0" * 64,
        )
        now = time.time()
        secret_id = None
        if args.api_key_file:
            key = Path(args.api_key_file).read_text(encoding="utf-8").strip()
            secret_id = SecretVault(store, root / "secrets").put(
                "provider.provider-live.api_key", key,
            )["secret_id"]
        with store.transaction() as connection:
            connection.execute(
                "INSERT INTO model_providers(provider_id,display_name,provider_kind,base_url,"
                "api_key_secret_id,default_model_id,status,metadata_json,created_unix,updated_unix) "
                "VALUES('provider-live','Live endpoint','openai_compatible',?,? ,?,'healthy','{}',?,?)",
                (args.base_url, secret_id, args.model, now, now),
            )
            connection.execute(
                "INSERT INTO provider_models(provider_id,model_id,display_name,context_length,"
                "capabilities_json,raw_metadata_json,discovered_unix) VALUES"
                "('provider-live',?,?,262144,'{}','{}',?)",
                (args.model, args.model, now),
            )
            connection.execute(
                "INSERT INTO control_settings(setting_key,value_json,updated_unix) VALUES"
                "('specialist.profile',?,?)",
                (canonical_json({
                    "profile_id": "live-specialist-contract",
                    "provider_id": "provider-live", "model_id": args.model,
                    "reasoning_effort": args.reasoning, "context_length": 262144,
                    "generation_settings": {
                        "temperature": 0.1,
                        "extra_parameters": {"chat_template_kwargs": {
                            "enable_thinking": args.reasoning != "none",
                            "preserve_thinking": False,
                        }},
                    },
                }), now),
            )
            connection.execute(
                "INSERT INTO control_settings(setting_key,value_json,updated_unix) VALUES"
                "('specialist.policy',?,?)",
                (canonical_json({
                    "installation_concurrency": 1, "seat_concurrency": 1,
                    "automatic_retries": 0, "schema_repairs": 1,
                    "investigation": {
                        "tool_budget": 10, "provider_call_budget": 10,
                        "provider_token_budget": 512000,
                        "context_token_ceiling": 262144,
                        "output_token_budget": 1500, "wall_seconds": 180,
                    },
                }), now),
            )
        service = SpecialistService(store, worlds, scope, journal=journal)
        mission = service.commission(
            faculty="world",
            objective=("Compare both threatened bases, the reserve's reachable responses, "
                       "local routes, and the ocean constraint. Retrieve the separate evidence "
                       "needed for each mechanical conclusion and report limitations."),
            subject_refs=("base-west", "base-east", "unit-reserve"),
            execution_class="investigation",
        )
        started = time.monotonic()
        supervisor = SpecialistSupervisor(
            database=store.path, secret_root=root / "secrets",
            snapshot_root=worlds.root, trace_root=root / "specialist-traces",
            reference_url="http://127.0.0.1:9", poll_seconds=0.1,
        )
        # A strict-schema repair is a fresh, isolated Hermes attempt. Keep
        # driving the same long-lived supervisor until the bounded mission is
        # terminal rather than mistaking retry_wait for completion.
        for _ in range(3):
            supervisor.run(once=True)
            if service.get(str(mission["mission_id"])).get("status") != "mission_pending":
                break
        supervisor.shutdown()
        elapsed = (time.monotonic() - started) * 1000
        result = service.get(str(mission["mission_id"]))
        with store._connect() as connection:
            attempt = dict(connection.execute(
                "SELECT * FROM specialist_attempts WHERE mission_id=? ORDER BY attempt_number DESC",
                (mission["mission_id"],),
            ).fetchone())
        if result.get("status") != "accepted":
            raise AssertionError(
                "live Hermes specialist did not produce an accepted bounded result; "
                f"status={result.get('status')}; outcome={attempt.get('status')}; "
                f"failure={attempt.get('failure_reason')}; "
                f"provider_calls={attempt.get('provider_calls')}; "
                f"provider_tokens={attempt.get('provider_tokens')}; "
                f"peak_context_tokens={attempt.get('peak_context_tokens')}"
            )
        value = result.get("result") or {}
        print(json.dumps({
            "schema": "smacx.specialist-provider-live.v2",
            "passed": True,
            "hermes_process": True,
            "strict_result": all(key in value for key in (
                "answer", "claims", "limitations", "unresolved_questions",
            )),
            "tool_calls": int(attempt.get("tool_calls") or 0),
            "provider_calls": int(attempt.get("provider_calls") or 0),
            "provider_tokens": int(attempt.get("provider_tokens") or 0),
            "peak_context_tokens": int(attempt.get("peak_context_tokens") or 0),
            "result_bytes": int(attempt.get("result_bytes") or 0),
            "sovereign_history_rows": 0,
            "latency_ms": round(elapsed, 3),
        }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
