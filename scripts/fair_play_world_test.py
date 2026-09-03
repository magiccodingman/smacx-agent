#!/usr/bin/env python3
"""Adversarial differential tests for the player perspective boundary."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from smacx_entitlements import PerspectiveEntitlements, sanitize_bundle
from smacx_attention import AttentionService
from smacx_journal import CampaignJournal
from smacx_regions import RegionBuilder
from smacx_semantic_map import render_svg
from smacx_store import MemoryScope, SmacxStore
from smacx_specialists import SpecialistService
from smacx_topology import KnownSquare, MapShape, MobilityProfile, PerspectiveTopology
from smacx_world import WorldService
from smacx_world_model import PerspectiveProjector, SemanticLodProjector
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity, canonical_json


def visible_bundle(honeytoken: str) -> dict:
    return {
        "turn": 40, "year": 2240, "action_revision": "r1",
        "map": {"width": 16, "height": 8, "horizontal_wrap": True},
        "tiles": [
            {"tile_id": 0, "x": 0, "y": 0, "visible_now": True, "terrain": "land"},
            {"tile_id": 1, "x": 2, "y": 0, "visible_now": True, "terrain": "land"},
            {"tile_id": 8, "x": 1, "y": 1, "visible_now": False, "terrain": "land"},
        ],
        "bases": [{"id": 0, "base_ref": "base-home", "tile_id": 0,
                   "owned": True, "name": "Home", "owner_ref": "faction-1"}],
        "units": [{"id": 1, "own_unit_ref": "own-unit-1", "tile_id": 0,
                   "owned": True, "name": "Scout", "owner_ref": "faction-1"}],
        "factions": [
            {"id": 1, "faction_ref": "faction-1", "owned": True, "name": "Us"},
            {"id": 2, "faction_ref": "faction-2", "owned": False, "name": "Them",
             "entitled_fields": {
                 "public_score": {"value": 10, "channel": "public_report"},
                 "secret_plan": {"value": honeytoken, "channel": "infiltration",
                                 "owner_ref": "faction-2"},
             }},
        ],
    }


def projected(bundle: dict, root: Path) -> tuple[dict, dict, str, dict, dict]:
    perspective = "perspective-fair"
    store = SmacxStore(root / "world.sqlite3")
    agent = "agent-fair"
    store.ensure_agent(agent, perspective)
    store.create_match(match_id="match-fair", display_name="Fair", mode="solo")
    store.create_perspective("match-fair", agent, perspective_id=perspective)
    scope = MemoryScope("match-fair", agent, perspective)
    journal = CampaignJournal(root / perspective, timeline_resolver=store.active_timeline_id)
    world_store = WorldStore(store, root / f"snap-{perspective}")
    identity = WorldIdentity("match-fair", perspective, journal.timeline_id(scope), "world-fair")
    result = PerspectiveProjector(identity).project(bundle, observation_sequence=1)
    world_store.replace_projection(scope, identity, result["objects"], observation_cursor=1,
                                   action_revision="r1", continuity="complete",
                                   journal_head_hash="0" * 64)
    service = WorldService(world_store, scope)
    anchor = service.anchor(context_length=65536)
    route = service.query(mode="route", origin_ref="base-home", target_ref="location-1",
                          context_length=65536)
    topology = service._topology(world_store.load(scope, identity.timeline_id) or {})
    objects = service._objects(world_store.load(scope, identity.timeline_id) or {})
    rendering = render_svg(topology, objects, max_cells=100)
    specialist = SpecialistService(store, world_store, scope).create(
        kind="world_analyst", question="Describe only supplied strategic evidence.",
        evidence=[{"evidence_ref": "base-home", "value": objects["base-home"]}],
    )
    for key in ("specialist_job_id", "identity"):
        specialist.pop(key, None)
    attention = AttentionService(store, journal, scope)
    queued = attention.enqueue(
        "world_change", {"delta": {"object_ref": "base-home", "change": "appeared",
                                    "current": objects["base-home"]}},
        observation_cursor=1, critical=True,
    )
    lease = attention.lease("episode-fair")
    attention_payload = lease["items"][0]["payload"]
    assert lease["items"][0]["attention_id"] == queued["attention_id"]
    return anchor, route, rendering, specialist, attention_payload


def main() -> int:
    entitlements = PerspectiveEntitlements("faction-1")
    first = sanitize_bundle(visible_bundle("HONEY-A"), entitlements)
    second = sanitize_bundle(visible_bundle("HONEY-B"), entitlements)
    assert canonical_json(first) == canonical_json(second)
    assert "HONEY" not in canonical_json(first)

    infiltrated = sanitize_bundle(visible_bundle("ENTITLED"), PerspectiveEntitlements(
        "faction-1", infiltrated_factions=frozenset({"faction-2"})))
    assert infiltrated["factions"][1]["secret_plan"] == "ENTITLED"
    assert sanitize_bundle({"factions": [{"owner_ref": "faction-2", "entitled_fields": {
        "shared": {"value": 7, "channel": "pact_shared"}}}]},
        PerspectiveEntitlements("faction-1", pact_factions=frozenset({"faction-2"}))) \
        ["factions"][0]["shared"] == 7
    entitled_global = sanitize_bundle({"global": [{"entitled_fields": {
        "vote": {"value": 4, "channel": "governor"},
        "orbit": {"value": 2, "channel": "satellite_report", "subject": "orbital"},
        "objective": {"value": "x", "channel": "scenario", "subject": "objective"},
    }}]}, PerspectiveEntitlements("faction-1", governor=True,
        satellite_channels=frozenset({"orbital"}), scenario_channels=frozenset({"objective"})))
    assert {key: entitled_global["global"][0][key]
            for key in ("vote", "orbit", "objective")} == {
                "vote": 4, "orbit": 2, "objective": "x",
            }
    assert entitled_global["global"][0]["_entitlement_channels"] == {
        "vote": "governor", "orbit": "satellite_report", "objective": "scenario",
    }
    source_projection = PerspectiveProjector(WorldIdentity(
        "match-source", "perspective-source", "timeline-source", "world-source",
    )).project(infiltrated, observation_sequence=1)
    foreign_faction = next(item for item in source_projection["objects"]
                           if item.object_ref == "faction-2")
    assert foreign_faction.fields["secret_plan"].source.value == "infiltration"
    global_source_bundle = first | {"global": entitled_global["global"]}
    global_projection = PerspectiveProjector(WorldIdentity(
        "match-global", "perspective-global", "timeline-global", "world-global",
    )).project(global_source_bundle, observation_sequence=1)
    global_object = next(item for item in global_projection["objects"]
                         if item.kind == "global_system")
    assert global_object.fields["vote"].source.value == "governor"
    assert global_object.fields["orbit"].source.value == "satellite"
    assert global_object.fields["objective"].source.value == "scenario"

    try:
        sanitize_bundle({"spectator_state": {"honey": True}}, entitlements)
        raise AssertionError("spectator state crossed player projection")
    except ValueError:
        pass

    with tempfile.TemporaryDirectory(prefix="smacx-fair-") as temporary:
        root = Path(temporary)
        a = projected(first, root / "a")
        b = projected(second, root / "b")
        # Scope identities and content-addressed IDs legitimately differ.  The
        # semantic bodies must remain byte-identical after removing scope IDs.
        for anchor in (a[0], b[0]):
            anchor.pop("world_anchor_id", None)
            anchor["payload"].pop("identity", None)
        for route in (a[1], b[1]):
            route.pop("identity", None); route.pop("cache", None)
        assert canonical_json(a) == canonical_json(b)
        assert "HONEY" not in canonical_json(a)

    # Regions/routes never bridge an unknown square.
    topology = PerspectiveTopology(MapShape(16, 8, False), [
        KnownSquare("a", 0, 0, "land"), KnownSquare("c", 4, 0, "land")])
    assert not topology.route("a", "c", MobilityProfile("land", "land")).reachable
    assert len(RegionBuilder().build(topology, MobilityProfile("land", "land"),
                                     world_revision=1)[0]) == 2
    print(json.dumps({"event": "pass", "payload": {
        "hidden_honeytoken_differential": True,
        "spectator_admin_isolation": True,
        "pact_infiltration_governor_satellite_scenario": True,
        "hidden_route_region_boundary": True,
        "provider_anchor_route_render_identical": True,
        "specialist_inputs_identical": True,
        "attention_payloads_identical": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
