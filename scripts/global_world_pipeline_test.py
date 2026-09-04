#!/usr/bin/env python3
"""End-to-end native-shaped global/logistics/entitlement world contract."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))

from observation_collector_benchmark import NativeFixture
from smacx_attention import AttentionService
from smacx_journal import CampaignJournal
from smacx_observation import ObservationCollector
from smacx_runtime_context import RuntimeContextAssembler
from smacx_store import MemoryScope, SmacxStore
from smacx_world import WorldService
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity


class CompleteDomainFixture(NativeFixture):
    def __init__(self) -> None:
        super().__init__(16, 8)
        self.project_builder_ref: str | None = "faction-2"
        for tile in self.tiles:
            tile["visible_now"] = True
        self.bases = [{
            "id": 0, "base_ref": "base-home", "tile_id": 0,
            "owned": True, "visible_now": True, "owner_ref": "faction-1",
            "name": "Home", "population": 5, "mineral_surplus": 8,
            "minerals": {"unit_support_cost": 1},
            "facilities": [{"facility_id": 33, "name": "Psi Gate"}],
            "psi_gate_ready": True,
            "base_radius": [
                {"location_ref": "location-0", "worked": True,
                 "yields": {"nutrients": 2, "minerals": 2, "energy": 1}},
                {"location_ref": "location-1", "worked": False,
                 "yields": {"nutrients": 1, "minerals": 1, "energy": 2}},
            ],
        }]
        self.units = [
            {"id": 0, "own_unit_ref": "own-unit-100", "native_observation_key":
             "vehicle-handle-100", "tile_id": 0, "owned": True, "owner": 1,
             "owner_ref": "faction-1", "name": "Clean Scout", "hp": 10,
             "max_hp": 10, "triad": "land", "movement_points": 3,
             "moves_remaining": 3, "movement_scale": 3,
             "requires_support": False, "home_base_id": 0,
             "roles": {"combat": True}, "abilities": ["clean_reactor"]},
            {"id": 1, "own_unit_ref": "own-unit-101", "native_observation_key":
             "vehicle-handle-101", "tile_id": 1, "owned": True, "owner": 1,
             "owner_ref": "faction-1", "name": "Garrison", "hp": 10,
             "max_hp": 10, "triad": "land", "movement_points": 3,
             "moves_remaining": 3, "movement_scale": 3,
             "requires_support": True, "home_base_id": 0,
             "roles": {"combat": True}},
            {"id": 2, "own_unit_ref": "own-unit-102", "native_observation_key":
             "vehicle-handle-102", "tile_id": 1, "owned": True, "owner": 1,
             "owner_ref": "faction-1", "name": "Supply Crawler", "hp": 10,
             "max_hp": 10, "triad": "land", "movement_points": 3,
             "moves_remaining": 0, "movement_scale": 3,
             "requires_support": False, "home_base_id": 0,
             "roles": {"supply": True}, "order_name": "convoy",
             "convoy_resource": "minerals"},
            {"id": 3, "own_unit_ref": "own-unit-103", "native_observation_key":
             "vehicle-handle-103", "tile_id": 7, "owned": True, "owner": 1,
             "owner_ref": "faction-1", "name": "Transport Foil", "hp": 10,
             "max_hp": 10, "triad": "sea", "movement_points": 6,
             "moves_remaining": 6, "movement_scale": 3,
             "requires_support": True, "home_base_id": 0,
             "roles": {"transport": True}, "cargo": {"capacity": 4, "loaded": 1}},
            {"id": 4, "native_observation_key": "vehicle-handle-200", "tile_id": 2,
             "owned": False, "owner": 0, "owner_ref": "faction-0",
             "name": "Mind Worms", "hp": 10, "max_hp": 10, "triad": "land",
             "movement_points": 3, "movement_scale": 3,
             "roles": {"planet_life": True, "wild_native": True}},
            {"id": 5, "native_observation_key": "vehicle-handle-201", "tile_id": 3,
             "owned": False, "owner": 3, "owner_ref": "faction-3",
             "name": "Resonance Rover", "hp": 10, "max_hp": 10,
             "triad": "land", "movement_points": 6, "movement_scale": 3,
             "roles": {"combat": True, "progenitor_force": True}},
        ]
        self.factions = [
            {"id": 1, "faction_ref": "faction-1", "owned": True,
             "faction_name": "Human Hive", "relations": {}},
            {"id": 2, "faction_ref": "faction-2", "owned": False,
             "faction_name": "Gaia's Stepdaughters", "relations": {"pact": True},
             "entitled_fields": {
                 "pact_shared_vision": {"value": True, "channel": "pact_shared",
                                        "owner_ref": "faction-2"},
                 "foreign_energy_credits": {"value": 222, "channel": "pact_shared",
                                            "owner_ref": "faction-2"},
             }},
            {"id": 3, "faction_ref": "faction-3", "owned": False,
             "faction_name": "Manifold Usurpers",
             "relations": {"vendetta": True, "infiltrated": True},
             "entitled_fields": {
                 "foreign_energy_credits": {"value": 321, "channel": "infiltration",
                                            "owner_ref": "faction-3"},
                 "foreign_research_technology_id": {"value": 42,
                     "channel": "infiltration", "owner_ref": "faction-3"},
                 "foreign_satellites": {"value": {"nutrient": 2, "mineral": 1,
                     "energy": 3, "orbital_defense": 1}, "channel": "infiltration",
                     "owner_ref": "faction-3"},
             }},
            {"id": 4, "faction_ref": "faction-4", "owned": False,
             "faction_name": "University of Planet", "relations": {},
             "entitled_fields": {
                 "foreign_research_technology_id": {"value": 17,
                     "channel": "project_intelligence", "owner_ref": "faction-4"},
             }},
        ]

    def __call__(self, operation: str, **kwargs):
        if operation == "list_bases":
            return {"ok": True, "items": self.bases, "next_offset": -1}
        if operation == "list_units":
            return {"ok": True, "items": self.units, "next_offset": -1}
        if operation == "list_factions":
            return {"ok": True, "items": self.factions}
        if operation == "list_technologies":
            return {"ok": True, "items": [
                {"technology_ref": "technology-42", "name": "Secrets of Alpha Centauri"},
            ]}
        if operation == "semantic_snapshot":
            revision = f"benchmark-{self.revision}"
            return {"ok": True, "snapshot": {
                "revision": revision,
                "game_settings": {"turn_clock": "none", "map_size": "standard"},
                "scenario": {"active": True, "technology_trading": True,
                             "native_life": True, "progenitor_objective": "resonance"},
                "economy": {"energy": 200}, "research": {"labs": 40},
                "social_engineering": {"economics": "Green"},
                "last_council_result": {"proposal": "governor", "winner": "faction-2"},
                "outcome": {"status": "in_progress"},
                "public_projects": [{"project_id": 1, "name": "The Weather Paradigm",
                                     "owner_ref": "faction-2"}],
                "known_project_races": [{
                    "project_id": 2, "name": "The Virtual World",
                    **({"builder_ref": self.project_builder_ref,
                        "builder_identity": "observed_report"}
                       if self.project_builder_ref else
                       {"builder_identity": "unknown"}),
                    "source": "public_report",
                }],
                "own_orbitals": {"nutrient": 1, "mineral": 2, "energy": 3,
                                 "orbital_defense": 1},
                "governor_faction_id": 2,
                "intelligence_entitlements": {"empath_guild_reports": True},
                "movement_rules": {"road_movement_scale": 3, "road_edge_cost": 1,
                                   "magtube_edge_cost": 0, "max_airdrop_range": 8},
                "ecology": {"sea_level": 4, "sunspot_duration": 2,
                            "perihelion_active": True, "volcano_erupted": False},
                "own_planetary_state": {"tectonic_detonations": 2,
                                         "random_event_id": 7,
                                         "transcendent_thoughts": 3},
                "victory_posture": {
                    "enabled": {"conquest": True, "economic": True,
                                "diplomatic": True, "transcendence": True,
                                "cooperative": True},
                    "economic": {"active": False, "completion_turn": -1,
                                 "committed_energy": 0},
                },
            }}
        return super().__call__(operation, **kwargs)


def field(item: dict, name: str):
    return item.get("fields", {}).get(name, {}).get("value")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-global-pipeline-") as raw:
        root = Path(raw)
        store = SmacxStore(root / "state.sqlite3")
        store.ensure_agent("agent-domain", "Domain Test")
        store.create_match(match_id="match-domain", display_name="Domain Test", mode="solo")
        store.create_perspective("match-domain", "agent-domain",
                                 perspective_id="perspective-domain")
        scope = MemoryScope("match-domain", "agent-domain", "perspective-domain")
        journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
        worlds = WorldStore(store, root / "snapshots")
        attention = AttentionService(store, journal, scope)
        fixture = CompleteDomainFixture()
        collector = ObservationCollector(
            scope=scope, session_id="session-domain", bridge_call=fixture,
            journal=journal, world_store=worlds, attention=attention,
        )
        collected = collector.collect_once()
        assert collected["changed"]
        temporal = worlds.temporal_events_since(
            scope, journal.timeline_id(scope), 0, limit=256,
        )
        global_events = [row["event"] for row in temporal
                         if row.get("event", {}).get("event_kind")
                         == "global_state_changed"]
        assert any(item.get("system_kind") == "ecology_state" for item in global_events)
        assert any(item.get("system_kind") == "victory_posture" for item in global_events)
        assert attention.pending_summary()["has_critical"] is True
        projection = worlds.load(scope, journal.timeline_id(scope))
        assert projection is not None
        objects = {item["object_ref"]: item for item in projection["objects"]}

        pact = objects["faction-2"]
        infiltrated = objects["faction-3"]
        project_report = objects["faction-4"]
        assert field(pact, "pact_shared_vision") is True
        assert field(pact, "foreign_energy_credits") == 222
        assert field(infiltrated, "foreign_energy_credits") == 321
        assert field(infiltrated, "foreign_research_technology_id") == 42
        assert field(infiltrated, "foreign_satellites")["energy"] == 3
        assert field(project_report, "foreign_research_technology_id") == 17

        expected_global_kinds = {
            "game_settings", "scenario_rules", "economy_state", "research_state",
            "social_state", "council_state", "victory_state", "project_state",
            "project_race_state", "orbital_state", "governor_state",
            "intelligence_entitlement_state",
            "movement_rules", "ecology_state", "victory_posture", "technology_state",
            "planetary_state",
        }
        actual_global_kinds = {item["kind"] for item in projection["objects"]
                               if item["object_ref"].startswith("global-")}
        assert expected_global_kinds <= actual_global_kinds
        initial_race = field(objects["global-known-project-races"], "state")[0]
        assert initial_race["builder_ref"] == "faction-2"
        assert initial_race["builder_epistemic_status"] == "current"

        # Simulate a native process restart: the bridge-local popup memory is
        # empty, while the journal-derived current projection still contains
        # the legitimately observed builder. It remains reported/stale until a
        # newer native report supersedes it.
        fixture.project_builder_ref = None
        fixture.revision += 1
        restarted_collector = ObservationCollector(
            scope=scope, session_id="session-domain-restarted", bridge_call=fixture,
            journal=journal, world_store=worlds, attention=attention,
        )
        restarted_collector.collect_once()
        projection = worlds.load(scope, journal.timeline_id(scope))
        assert projection is not None
        objects = {item["object_ref"]: item for item in projection["objects"]}
        stale_race = field(objects["global-known-project-races"], "state")[0]
        assert stale_race["builder_ref"] == "faction-2"
        assert stale_race["builder_epistemic_status"] == "stale"
        assert stale_race["builder_provenance"]

        fixture.project_builder_ref = "faction-3"
        fixture.revision += 1
        restarted_collector.collect_once()
        projection = worlds.load(scope, journal.timeline_id(scope))
        assert projection is not None
        objects = {item["object_ref"]: item for item in projection["objects"]}
        replacement_race = field(objects["global-known-project-races"], "state")[0]
        assert replacement_race["builder_ref"] == "faction-3"
        assert replacement_race["builder_epistemic_status"] == "current"
        assert field(objects["global-ecology"], "state")["sea_level"] == 4
        assert field(objects["global-own-planetary-state"], "state")["random_event_id"] == 7
        assert field(objects["global-victory-posture"], "state")["enabled"]["cooperative"]

        base = objects["base-home"]
        assert len(field(base, "base_radius")) == 2
        assert field(base, "psi_gate_ready") is True
        own_units = [item for item in objects.values() if item["kind"] == "own_unit"]
        assert sum(field(item, "requires_support") is True for item in own_units) == 2
        native_roles = field(next(item for item in objects.values()
                                  if field(item, "name") == "Mind Worms"), "roles")
        progenitor_roles = field(next(item for item in objects.values()
                                      if field(item, "name") == "Resonance Rover"), "roles")
        assert native_roles["wild_native"] and progenitor_roles["progenitor_force"]

        world = WorldService(worlds, scope)
        global_result = world.query(mode="global", detail="deep", context_length=262144)
        assert global_result["ok"] and expected_global_kinds <= {
            item["kind"] for item in global_result["items"]
        }
        logistics = world.query(mode="logistics", detail="deep", context_length=262144)
        assert logistics["ok"]
        assert logistics["logistics"]["support_by_home_base"] == {"base-home": 2}
        assert logistics["logistics"]["convoys"][0]["resource"] == "minerals"
        assert logistics["logistics"]["transports"][0]["cargo"]["capacity"] == 4
        base_result = world.query(
            mode="base", subject_refs=["base-home"], detail="deep",
            context_length=262144,
        )
        assert base_result["ok"] and field(base_result["objects"][0], "base_radius")

        anchor = world.anchor(context_length=262144)
        assert expected_global_kinds <= {
            item["kind"] for item in anchor["payload"]["strategic_objects"]
        }
        runtime = RuntimeContextAssembler(
            scope=scope, world=world, attention=attention,
            snapshot=lambda: {"turn": 50, "year": 2150,
                              "revision": f"benchmark-{fixture.revision}",
                              "protocol": {"phase": "turn", "required_action": "play"}},
            working_state=lambda: {"sections": {}},
        ).build(episode_id="episode-domain", episode_mode="gameplay",
                context_length=262144)
        assert expected_global_kinds <= {
            item["kind"] for item in runtime["world"]["anchor"]["strategic_objects"]
        }
        assert runtime["attention"]["items"]

        replay = journal.replay(scope)
        head_hash = replay["manifest"]["head_hash"]
        head_sequence = replay["manifest"]["sequence"]
        frozen = worlds.snapshot(
            scope, WorldIdentity(**projection["identity"]),
            journal_head_hash=head_hash, journal_sequence=head_sequence,
            calculator_versions={"world": "test"},
            pin_owner=("specialist_mission", "mission-domain"),
        )
        frozen_content = worlds.load_snapshot_content(frozen["snapshot_id"])
        frozen_kinds = {item["kind"] for item in
                        frozen_content["projection"]["objects"]}
        assert expected_global_kinds <= frozen_kinds
        frozen_text = json.dumps(frozen_content, sort_keys=True)
        assert "99999" not in frozen_text
        worlds.unpin_snapshot(frozen["snapshot_id"], "specialist_mission", "mission-domain")

    print(json.dumps({"event": "pass", "payload": {
        "native_to_projection": True,
        "pact_infiltration_entitlements": True,
        "governor_and_project_intelligence_entitlements": True,
        "global_domains_query_anchor_runtime": True,
        "base_radius_support_convoy_transport": True,
        "wild_native_and_progenitor_ontology": True,
        "project_builder_report_survives_restart_and_is_superseded": True,
        "specialist_snapshot_entitlement_safe": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
