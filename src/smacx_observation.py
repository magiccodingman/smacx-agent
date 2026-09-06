"""Asynchronous bridge observation draining and journal-backed reconciliation."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any, Callable, Mapping

from smacx_journal import CampaignJournal
from smacx_store import MemoryScope
from smacx_world_model import PerspectiveProjector, net_deltas
from smacx_world_store import WorldStore
from smacx_world_types import provider_safe, WorldIdentity, canonical_json, content_hash
from smacx_attention import AttentionService
from smacx_entitlements import PerspectiveEntitlements, sanitize_bundle


LOG = logging.getLogger("smacx.observation")


def _sequence_content_hash(values: list[Mapping[str, Any]]) -> str:
    """Merkle-like ordered hash without one unbounded JSON encoder hold."""
    digest = hashlib.sha256()
    digest.update(str(len(values)).encode("ascii"))
    for index, item in enumerate(values):
        digest.update(index.to_bytes(8, "big"))
        digest.update(bytes.fromhex(content_hash(item)))
    return digest.hexdigest()


def _delta_attention(delta: Mapping[str, Any]) -> tuple[bool, int] | None:
    """Classify material deltas without turning routine bookkeeping into alarms."""
    current = delta.get("current") if isinstance(delta.get("current"), Mapping) else {}
    kind = str(current.get("kind") or delta.get("prior_kind") or "world_change")
    change = str(delta.get("change") or "changed")
    fields = current.get("fields") if isinstance(current.get("fields"), Mapping) else {}
    values = {name: item.get("value") for name, item in fields.items()
              if isinstance(item, Mapping)}
    if kind == "ecology_state":
        state = values.get("state")
        previous = delta.get("previous")
        prior_state = _field_value(previous, "state") if isinstance(previous, Mapping) else None
        if isinstance(state, Mapping) and isinstance(prior_state, Mapping):
            duration, prior_duration = state.get("sunspot_duration"), prior_state.get("sunspot_duration")
            if type(duration) is int and type(prior_duration) is int:
                # gameturn.cpp decrements this counter even while inactive
                # (negative). Countdowns do not imply a new ecology incident.
                # Preserve starts/ends and every other state-field change.
                material = {key: value for key, value in state.items() if key != "sunspot_duration"}
                prior_material = {key: value for key, value in prior_state.items() if key != "sunspot_duration"}
                if material == prior_material and (duration > 0) == (prior_duration > 0):
                    return None
        return True, 95
    if kind in {"victory", "victory_state", "victory_posture", "global_event",
                "council_state", "planetary_state"}:
        return True, 95
    if kind == "foreign_contact":
        return True, 90
    if kind == "base":
        if change in {"appeared", "removed"}:
            return True, 90
        previous = delta.get("previous")
        if isinstance(previous, Mapping):
            meaningful = {"owner_ref", "population", "drone_riots", "threatened", "facilities",
                          "production_name", "production_queue", "governor", "nerve_stapling", "eco_damage"}
            if delta.get("production_occurrence"):
                meaningful -= {"production_name", "production_queue"}
            changed = {name for name in meaningful
                       if _field_value(current, name) != _field_value(previous, name)}
            if not changed:
                return None
            if "owner_ref" in changed or any(name in changed and values.get(name) for name in ("threatened", "drone_riots")):
                return True, 90
        elif values.get("threatened") or values.get("drone_riots"):
            return True, 90
        return False, 55
    if kind in {"project", "project_state", "project_race_state", "orbital_state",
                "governor_state", "intelligence_entitlement_state",
                "scenario_rules", "game_settings"}:
        return True, 85
    if kind in {"faction", "economy_state", "research_state", "social_state",
                "technology_state"}:
        return False, 65
    if kind in {"own_unit", "location"}:
        # Routine owned-unit and terrain projection changes remain in the
        # authoritative journal and can trigger explicit watches. They are not
        # independently useful attention interrupts.
        return None
    return False, 45


def _bounded_batches(values: list[dict[str, Any]], *, byte_limit: int = 200_000,
                     item_limit: int = 256) -> list[list[dict[str, Any]]]:
    """Chunk journal/projection payloads below the journal's hard event limit."""
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_bytes = 64
    for value in values:
        size = len(canonical_json(value).encode("utf-8")) + 1
        if current and (len(current) >= item_limit or current_bytes + size > byte_limit):
            batches.append(current)
            current, current_bytes = [], 64
        current.append(value)
        current_bytes += size
    if current:
        batches.append(current)
    return batches


def _field_value(item: Mapping[str, Any], name: str) -> Any:
    fields = item.get("fields") if isinstance(item.get("fields"), Mapping) else {}
    value = fields.get(name)
    return value.get("value") if isinstance(value, Mapping) else None


def _provider_safe_temporal_events(
    deltas: list[dict[str, Any]], projected: list[dict[str, Any]],
    *, turn: int | None,
) -> list[dict[str, Any]]:
    """Convert observed transitions into semantic history with no native identity."""
    events = [dict(item) for item in projected if isinstance(item, Mapping)]
    discovered_count, discovered_refs, discovered_features = 0, [], {}
    for delta in deltas:
        current = delta.get("current") if isinstance(delta.get("current"), Mapping) else {}
        previous = delta.get("previous") if isinstance(delta.get("previous"), Mapping) else {}
        kind = str(current.get("kind") or delta.get("prior_kind") or previous.get("kind") or "")
        change = str(delta.get("change") or "changed")
        object_ref = str(delta.get("object_ref") or current.get("object_ref") or "")
        if kind == "base":
            if change == "appeared":
                event_kind = "base_founded"
            elif change == "removed":
                event_kind = "base_destroyed"
            elif _field_value(current, "owner_ref") != _field_value(previous, "owner_ref") \
                    and _field_value(previous, "owner_ref") is not None:
                event_kind = "base_captured"
            else:
                continue
            events.append({
                "event_kind": event_kind, "base_ref": object_ref,
                "location_ref": current.get("location_ref") or previous.get("location_ref"),
                "owner_ref": _field_value(current, "owner_ref"), "turn": turn,
            })
        elif kind == "foreign_contact" and change == "changed":
            prior_hp, current_hp = _field_value(previous, "hp"), _field_value(current, "hp")
            if isinstance(prior_hp, (int, float)) and isinstance(current_hp, (int, float)) \
                    and current_hp < prior_hp:
                events.append({
                    "event_kind": "contact_damaged", "contact_ref": object_ref,
                    "location_ref": current.get("location_ref"),
                    "observed_hp_before": prior_hp, "observed_hp_after": current_hp,
                    "turn": turn,
                })
        elif kind == "location" and change == "appeared":
            discovered_count += 1
            if len(discovered_refs) < 8: discovered_refs.append(object_ref)
            feature_field = current.get("fields", {}).get("features", {})
            freshness = feature_field.get("epistemic_status", "unknown")
            for feature in feature_field.get("value", ()) or ():
                key = str(feature) + ":" + str(freshness)
                discovered_features[key] = discovered_features.get(key, 0) + 1
        elif kind == "location" and change == "changed":
            changed_fields = [name for name in ("terrain", "features", "owner_ref")
                              if _field_value(current, name) != _field_value(previous, name)]
            if changed_fields:
                events.append({
                    "event_kind": "terrain_or_improvement_changed",
                    "location_ref": object_ref, "changed_fields": changed_fields,
                    "turn": turn,
                    "change_basis": "observed_values_changed" if all(
                        previous.get("fields", {}).get(name, {}).get("epistemic_status") == "current"
                        and current.get("fields", {}).get(name, {}).get("epistemic_status") == "current"
                        for name in changed_fields) else "knowledge_refresh",
                    "cause": "not_determined", "occurrence_time": "between_observations",
                })
        elif kind in {
            "game_settings", "scenario_rules", "council_state", "victory_state",
            "project_state", "project_race_state", "orbital_state", "governor_state",
            "intelligence_entitlement_state",
            "ecology_state", "planetary_state", "victory_posture", "global_event",
        }:
            before_fields = previous.get("fields") if isinstance(previous.get("fields"), Mapping) else {}
            after_fields = current.get("fields") if isinstance(current.get("fields"), Mapping) else {}
            changed_fields = sorted({*before_fields, *after_fields})
            events.append({
                "event_kind": "global_state_changed",
                "system_ref": object_ref,
                "system_kind": kind,
                "change": change,
                "changed_fields": changed_fields[:32],
                "turn": turn,
            })
    if discovered_count:
        events.append({"event_kind": "known_extent_increased", "turn": turn,
            "newly_known_location_count": discovered_count, "sample_location_refs": discovered_refs,
            "newly_known_features_by_freshness": dict(sorted(discovered_features.items())[:32]),
            "change_basis": "new_to_perspective", "physical_creation_inferred": False})
    # Exact duplicates can arise when an appearance also has an ordinary
    # projection delta.  Keep one deterministic semantic occurrence.
    unique: dict[str, dict[str, Any]] = {}
    for item in events:
        unique[content_hash(item)] = item
    return list(unique.values())


class ObservationCollectorError(RuntimeError):
    pass


BridgeCall = Callable[..., dict[str, Any]]


class ObservationCollector:
    """Drain the bounded native feed independently of sovereign tool polling."""

    def __init__(
        self, *, scope: MemoryScope, session_id: str, bridge_call: BridgeCall,
        journal: CampaignJournal, world_store: WorldStore, poll_seconds: float = 0.5,
        attention: AttentionService | None = None,
        chat_capture: Callable[[], Any] | None = None,
    ) -> None:
        self.scope = scope
        self.session_id = session_id
        self.bridge_call = bridge_call
        self.journal = journal
        self.world_store = world_store
        self.attention = attention
        self.chat_capture = chat_capture
        self.poll_seconds = min(max(float(poll_seconds), 0.1), 10.0)
        self.timeline_id = journal.timeline_id(scope)
        self.observation_cursor = 0
        self.native_after_sequence = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._collect_lock = threading.Lock()
        self._last_action_revision = ""
        # Collector-private continuously-visible paths.  They are consumed by
        # exactly one reconciliation and never become provider-facing state.
        self._continuous_contact_moves: dict[str, list[dict[str, Any]]] = {}
        self._contact_identity_reset = False
        self._pending_native_events: list[dict[str, Any]] = []
        self._collection_metrics: dict[str, Any] = {}
        self._restore_native_stage()

    def _restore_native_stage(self) -> dict[str, Any]:
        stage = self.world_store.load_native_observation_stage(
            self.scope, self.timeline_id,
        )
        self.native_after_sequence = int(stage["staged_after_sequence"])
        self._pending_native_events = list(stage["events"])
        self._continuous_contact_moves.clear()
        self._contact_identity_reset = stage.get("continuity_gap") is not None
        for payload in self._pending_native_events:
            kind = str(payload.get("native_kind") or "")
            handle = payload.get("subject_a")
            key = f"vehicle-handle-{handle}" if isinstance(handle, int) else ""
            if kind == "visible_unit_moved" and payload.get("continuous_visibility") \
                    and isinstance(payload.get("from_tile_id"), int) \
                    and isinstance(payload.get("to_tile_id"), int):
                self._continuous_contact_moves.setdefault(key, []).append({
                    "from": f"location-{payload['from_tile_id']}",
                    "to": f"location-{payload['to_tile_id']}",
                    "native_sequence": payload["native_sequence"],
                    "relationship_at_occurrence": payload.get("relationship_at_occurrence", "unknown"),
                })
            elif kind in {"visible_unit_lost", "visible_unit_destroyed"} and key:
                self._continuous_contact_moves.pop(key, None)
            elif kind == "contact_identity_reset":
                self._continuous_contact_moves.clear()
                self._contact_identity_reset = True
        return stage

    def _bridge(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        self._collection_metrics["bridge_calls"] = int(
            self._collection_metrics.get("bridge_calls", 0)
        ) + 1
        return self.bridge_call(operation, **kwargs)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"smacx-observer-{self.scope.perspective_id}", daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(min(max(timeout, 0.0), 10.0))

    def _run(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            try:
                self.collect_once()
            except Exception as exc:  # keep observation alive; incident is durable below where possible
                LOG.warning("observation collection failed: %s", type(exc).__name__)

    def _page(self, domain: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        cursor = 0
        head: dict[str, Any] | None = None
        items: list[dict[str, Any]] = []
        while True:
            # The native adapter caps pages at 256 POD rows. Use the full
            # bounded page so Huge-map reconciliation spends half as many
            # serialized UI-thread round trips without enlarging any single
            # native request beyond its reviewed ceiling.
            page = self._bridge("perspective_world_page", domain=domain,
                                cursor=cursor, limit=256)
            if not page.get("ok"):
                raise ObservationCollectorError(f"world_page_{domain}_failed")
            if head is None:
                head = page
            elif page.get("action_revision") != head.get("action_revision"):
                raise ObservationCollectorError("world_changed_during_pagination")
            values = page.get("items")
            if not isinstance(values, list):
                raise ObservationCollectorError("invalid_world_page")
            items.extend(item for item in values if isinstance(item, dict))
            next_cursor = page.get("next_cursor")
            if next_cursor is None:
                return head, items
            next_cursor = int(next_cursor)
            if next_cursor <= cursor:
                raise ObservationCollectorError("world_page_cursor_stalled")
            cursor = next_cursor

    def _semantic_items(self, operation: str, *, limit: int,
                        extra: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        """Drain an existing bounded fair-play semantic endpoint."""
        offset = 0
        result: list[dict[str, Any]] = []
        while True:
            page = self._bridge(operation, offset=offset, limit=limit, **dict(extra or {}))
            if not page.get("ok") or not isinstance(page.get("items"), list):
                raise ObservationCollectorError(f"semantic_{operation}_failed")
            result.extend(item for item in page["items"] if isinstance(item, dict))
            next_offset = page.get("next_offset", -1)
            if not isinstance(next_offset, int) or next_offset < 0:
                return result
            if next_offset <= offset:
                raise ObservationCollectorError(f"semantic_{operation}_cursor_stalled")
            offset = next_offset

    def _bundle(self) -> dict[str, Any]:
        summary, _ = self._page("summary")
        revision = str(summary.get("action_revision") or "")
        collected: dict[str, list[dict[str, Any]]] = {}
        for domain in ("tiles", "bases", "units", "factions"):
            head, items = self._page(domain)
            if str(head.get("action_revision") or "") != revision:
                raise ObservationCollectorError("world_changed_during_collection")
            collected[domain] = items
        # Reuse the mature semantic adapters for rich owned-state, logistics,
        # citizen, facility, combat, diplomacy, debt, and technology detail.
        # The paged world feed remains the fair-play completeness source for
        # foreign visible objects and remembered geography.
        rich_bases = self._semantic_items("list_bases", limit=200)
        rich_units = self._semantic_items("list_units", limit=300, extra={"scope": "visible"})
        rich_factions = self._bridge("list_factions")
        technologies = self._bridge("list_technologies")
        rich_base_by_id = {int(item["id"]): item for item in rich_bases if "id" in item}
        collected["bases"] = [
            ({**item, **rich_base_by_id[int(item["id"])]}
             if item.get("owned") and int(item.get("id", -1)) in rich_base_by_id else item)
            for item in collected["bases"]
        ]
        rich_unit_by_id = {int(item["id"]): item for item in rich_units if "id" in item}
        own_ref_by_native_id = {
            int(item["id"]): str(item["own_unit_ref"])
            for item in collected["units"]
            if item.get("owned") and "id" in item and item.get("own_unit_ref")
        }
        normalized_units = []
        for item in collected["units"]:
            native_id = int(item.get("id", -1))
            rich = rich_unit_by_id.get(native_id)
            if rich is None:
                normalized_units.append(item)
                continue
            merged = {**item, **rich,
                      "roles": {**dict(item.get("roles") or {}),
                                **dict(rich.get("roles") or {})},
                      "owned": bool(item.get("owned")),
                      "owner_ref": f"faction-{rich.get('owner')}"}
            home_base_id = rich.get("home_base_id")
            if merged["owned"] and isinstance(home_base_id, int) \
                    and home_base_id in rich_base_by_id:
                merged["home_base_ref"] = str(
                    rich_base_by_id[home_base_id].get("base_ref")
                    or f"base-location-{rich_base_by_id[home_base_id].get('tile_id')}"
                )
            transport_id = rich.get("transport_unit_id")
            if merged["owned"] and isinstance(transport_id, int) and transport_id >= 0:
                merged["transport_unit_ref"] = own_ref_by_native_id.get(transport_id)
            if merged["owned"] and merged.get("convoy_resource"):
                merged["convoy_source_location_ref"] = f"location-{merged['tile_id']}"
                merged["convoy_base_effect"] = {
                    "resource": str(merged["convoy_resource"]),
                    "amount": int(merged.get("convoy_amount", 0) or 0),
                }
                if isinstance(home_base_id, int) and home_base_id in rich_base_by_id:
                    destination = rich_base_by_id[home_base_id]
                    merged["convoy_destination_base_ref"] = str(
                        destination.get("base_ref")
                        or f"base-location-{destination.get('tile_id')}"
                    )
            normalized_units.append(merged)
        collected["units"] = normalized_units
        if rich_factions.get("ok") and isinstance(rich_factions.get("items"), list):
            full = {int(item["id"]): item for item in rich_factions["items"]
                    if isinstance(item, Mapping) and "id" in item}
            collected["factions"] = [
                {**item, **full.get(int(item.get("id", -1)), {})}
                for item in collected["factions"]
            ]
        envelope = self._bridge("semantic_snapshot")
        snapshot = envelope.get("snapshot") if isinstance(envelope, Mapping) else None
        if not isinstance(snapshot, Mapping):
            raise ObservationCollectorError("semantic_snapshot_unavailable")
        if str(snapshot.get("revision") or "") != revision:
            raise ObservationCollectorError("world_changed_during_collection")
        global_objects = []
        if isinstance(summary.get("repair_rules"), Mapping):
            global_objects.append({
                "object_ref": "global-repair-rules", "kind": "repair_rules",
                "source": "owned_state", "state": dict(summary["repair_rules"]),
            })
        for key, kind, source in (
            ("game_settings", "game_settings", "owned_state"),
            ("scenario", "scenario_rules", "scenario"),
            ("economy", "economy_state", "owned_state"),
            ("research", "research_state", "owned_state"),
            ("social_engineering", "social_state", "owned_state"),
            ("last_council_result", "council_state", "public_report"),
            ("outcome", "victory_state", "public_report"),
            ("public_projects", "project_state", "public_report"),
            ("known_project_races", "project_race_state", "public_report"),
            ("own_orbitals", "orbital_state", "owned_state"),
            ("governor_faction_id", "governor_state", "public_report"),
            ("intelligence_entitlements", "intelligence_entitlement_state", "owned_state"),
            ("movement_rules", "movement_rules", "owned_state"),
            ("ecology", "ecology_state", "public_report"),
            ("own_planetary_state", "planetary_state", "owned_state"),
            ("victory_posture", "victory_posture", "owned_state"),
        ):
            value = snapshot.get(key)
            if value is None:
                continue
            global_objects.append({
                "object_ref": f"global-{key.replace('_', '-')}",
                "kind": kind, "source": source, "state": value,
            })
        if technologies.get("ok") and isinstance(technologies.get("items"), list):
            global_objects.append({
                "object_ref": "global-owned-technologies", "kind": "technology_state",
                "source": "owned_state", "technologies": technologies["items"],
            })
        bundle = {
            "turn": summary.get("turn"), "year": summary.get("year"),
            "action_revision": revision, "map": summary.get("map", {}),
            "own_faction_ref": f"faction-{summary.get('faction_id')}",
            "global": global_objects, **collected,
            "_continuous_visible_contact_moves": {
                key: list(value) for key, value in self._continuous_contact_moves.items()
            },
            "_contact_identity_reset": self._contact_identity_reset,
            "_broken_contact_handles": sorted({
                f"vehicle-handle-{item['subject_a']}"
                for item in self._pending_native_events
                if item.get("native_kind") == "visible_unit_lost"
                and isinstance(item.get("subject_a"), int)
            }),
            "_confirmed_destroyed_handles": sorted({
                f"vehicle-handle-{item['subject_a']}"
                for item in self._pending_native_events
                if item.get("native_kind") == "visible_unit_destroyed"
                and isinstance(item.get("subject_a"), int)
            }),
        }
        # All native rows are already perspective-filtered.  The explicit
        # entitlement pass is retained as an independently testable boundary
        # for richer Pact/infiltration/Governor/satellite adapters.
        own_ref = str(bundle["own_faction_ref"])
        pact = frozenset(
            str(item.get("faction_ref") or f"faction-{item.get('id')}")
            for item in collected["factions"]
            if isinstance(item.get("relations"), Mapping)
            and item["relations"].get("pact") is True
        )
        infiltrated = frozenset(
            str(item.get("faction_ref") or f"faction-{item.get('id')}")
            for item in collected["factions"]
            if isinstance(item.get("relations"), Mapping)
            and item["relations"].get("infiltrated") is True
        )
        return sanitize_bundle(bundle, PerspectiveEntitlements(
            faction_ref=own_ref,
            unity_survey=bool(summary.get("unity_survey", False)),
            governor=bool(summary.get("is_governor", False)),
            project_intelligence=bool(
                isinstance(snapshot.get("intelligence_entitlements"), Mapping)
                and snapshot["intelligence_entitlements"].get("empath_guild_reports") is True
            ),
            pact_factions=pact,
            infiltrated_factions=infiltrated,
        ))

    def _world_epoch(self, bundle: Mapping[str, Any],
                     current: Mapping[str, Any] | None) -> str:
        # A world epoch identifies the loaded world, not the process that happens
        # to expose it. Preserve it across bridge/Proton restarts and rollback
        # timelines; a match gets a new identity when it starts a new world.
        if current:
            current_identity = current.get("identity")
            if isinstance(current_identity, Mapping) and current_identity.get("world_epoch"):
                return str(current_identity["world_epoch"])
        material = canonical_json({
            "match_id": self.scope.match_id,
            "map": bundle.get("map", {}),
        })
        return "world-" + hashlib.sha256(material.encode()).hexdigest()[:32]

    def _preserve_project_report_history(
        self, bundle: dict[str, Any], current: Mapping[str, Any] | None,
    ) -> None:
        """Carry legitimately observed builders from journal authority."""
        global_rows = bundle.get("global")
        if not isinstance(global_rows, list):
            return
        row = next((item for item in global_rows
                    if isinstance(item, dict)
                    and item.get("object_ref") == "global-known-project-races"), None)
        races = row.get("state") if isinstance(row, dict) else None
        if not isinstance(races, list):
            return
        prior_field: Mapping[str, Any] = {}
        prior_by_project: dict[int, Mapping[str, Any]] = {}
        replayed = self.journal.replay(self.scope, self.timeline_id, sections=("project_reports",))
        project_reports = replayed.get("project_reports") \
            if isinstance(replayed.get("project_reports"), Mapping) else {}
        for project_ref, report in project_reports.items():
            if not isinstance(report, Mapping) or not str(project_ref).startswith("project-"):
                continue
            try:
                prior_by_project[int(str(project_ref).split("-", 1)[1])] = report
            except ValueError:
                continue
        # The prior projection is an accelerator/bootstrap for installations
        # created before semantic Project reports existed. Once a report has
        # been journalled, replayed ``project_reports`` is the sole authority.
        prior_object = None
        if not prior_by_project and current:
            prior_object = next((item for item in current.get("objects", ())
                                 if isinstance(item, Mapping)
                                 and item.get("object_ref") ==
                                 "global-known-project-races"), None)
        if isinstance(prior_object, Mapping):
            candidate_field = prior_object.get("fields", {}).get("state") \
                if isinstance(prior_object.get("fields"), Mapping) else None
            prior_values = candidate_field.get("value") \
                if isinstance(candidate_field, Mapping) else None
            if isinstance(candidate_field, Mapping):
                prior_field = candidate_field
            if isinstance(prior_values, list):
                prior_by_project.update({
                    int(item["project_id"]): item for item in prior_values
                    if isinstance(item, Mapping)
                    and isinstance(item.get("project_id"), int)
                    and item.get("builder_ref")
                })
        halted_in_publication = {
            int(item["subject_a"]) for item in self._pending_native_events
            if item.get("native_kind") == "project_race_halted"
            and isinstance(item.get("subject_a"), int)
        }
        for race in races:
            if not isinstance(race, dict) or not isinstance(race.get("project_id"), int):
                continue
            if race.get("builder_ref"):
                race["builder_epistemic_status"] = "current"
                race["builder_provenance"] = "native_public_report"
                continue
            if int(race["project_id"]) in halted_in_publication:
                continue
            prior = prior_by_project.get(int(race["project_id"]))
            if prior is None:
                continue
            race["builder_ref"] = prior["builder_ref"]
            race["builder_identity"] = "observed_report"
            race["builder_epistemic_status"] = "stale"
            race["builder_last_verified_turn"] = prior.get(
                "builder_last_verified_turn", prior_field.get("last_verified_turn")
            )
            race["builder_provenance"] = prior.get(
                "builder_provenance", prior_field.get("provenance_ref")
            )

    def _append_native_feed(self, feed: Mapping[str, Any]) -> None:
        saw_inbound_chat = False
        staged_events: list[dict[str, Any]] = []
        continuity_gap = None
        if feed.get("continuity") == "incomplete":
            continuity_gap = {
                "before_native_sequence": min((int(item["sequence"]) for item in feed.get("events", ())
                    if isinstance(item, Mapping)), default=int(feed.get("next_sequence") or self.native_after_sequence)+1),
                "native_after_sequence": self.native_after_sequence,
                "native_next_sequence": int(feed.get("next_sequence") or 0),
                "lost_after_observation_sequence": feed.get("lost_after_observation_sequence"),
                "reconciliation_required": True,
            }
        for item in feed.get("events", ()):
            if not isinstance(item, Mapping):
                continue
            payload = {
                "native_sequence": int(item["sequence"]),
                "native_kind": str(item.get("kind") or "unknown"),
                "subject_a": item.get("subject_a"), "subject_b": item.get("subject_b"),
                "from_tile_id": item.get("from_tile_id"),
                "to_tile_id": item.get("to_tile_id"),
                "value_before": item.get("value_before"),
                "value_after": item.get("value_after"),
                "item_name": str(item.get("item_name") or "")[:64],
                "continuous_visibility": bool(item.get("continuous_visibility", False)),
                "relationship_at_occurrence": str(item.get("relationship_at_occurrence") or "unknown"),
            }
            saw_inbound_chat = saw_inbound_chat or payload["native_kind"] == "chat_inbound"
            staged_events.append({**payload, "turn": item.get("turn")})
        self.world_store.stage_native_observation_feed(
            self.scope, self.timeline_id, events=staged_events,
            next_sequence=int(feed.get("next_sequence") or 0),
            continuity_gap=continuity_gap,
        )
        # Only the durable private stage advances the ring-drain cursor. A
        # process failure before semantic publication therefore replays the
        # same staged rows instead of silently consuming them.
        self._restore_native_stage()
        if saw_inbound_chat and self.chat_capture is not None:
            self.chat_capture()

    def _drain_native_feed(self) -> dict[str, Any]:
        """Drain every currently available ring page without blocking native UI."""
        first: dict[str, Any] | None = None
        pages = 0
        event_count = 0
        while True:
            feed = self._bridge(
                "observation_feed", after_sequence=self.native_after_sequence, limit=256,
            )
            if not feed.get("ok"):
                raise ObservationCollectorError("native_observation_feed_failed")
            first = first or dict(feed)
            event_count += len(feed.get("events", ())) \
                if isinstance(feed.get("events"), list) else 0
            self._append_native_feed(feed)
            pages += 1
            if not feed.get("has_more"):
                first["action_revision"] = feed.get("action_revision")
                first["continuity"] = (
                    "incomplete" if first.get("continuity") == "incomplete"
                    or feed.get("continuity") == "incomplete" else "complete"
                )
                first["drained_pages"] = pages
                first["drained_event_count"] = event_count
                return first
            if pages >= 4:  # native ring capacity is exactly 1024 events
                raise ObservationCollectorError("native_observation_ring_drain_stalled")

    def _coalesce_native_events(
        self, *, current_objects: list[dict[str, Any]],
        prior_objects: list[Mapping[str, Any]], turn: int | None,
        world_epoch: str | None = None,
        episode_assignments: Mapping[str, str] | None = None,
        episode_terminal: Mapping[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Translate private POD transitions into authoritative semantic events."""
        prior_handle_to_ref: dict[str, str] = {}
        current_handle_to_ref: dict[str, str] = {}
        ref_kinds: dict[str, str] = {}
        for collection, destination in (
            (prior_objects, prior_handle_to_ref),
            (current_objects, current_handle_to_ref),
        ):
            for item in collection:
                if not isinstance(item, Mapping):
                    continue
                metadata = item.get("metadata") \
                    if isinstance(item.get("metadata"), Mapping) else {}
                key = metadata.get("native_observation_key")
                if key and (destination is prior_handle_to_ref or item.get("status", "active") == "active"):
                    destination[str(key)] = str(item.get("object_ref"))
                    ref_kinds[str(item.get("object_ref"))] = str(
                        item.get("kind") or ""
                    )
                if item.get("kind") == "own_unit" \
                        and metadata.get("native_handle") is not None:
                    destination[
                        f"vehicle-handle-{metadata['native_handle']}"
                    ] = str(item.get("object_ref"))
                    ref_kinds[str(item.get("object_ref"))] = "own_unit"
        events: list[dict[str, Any]] = []
        move_by_contact: dict[str, dict[str, Any]] = {}
        base_capture_history: dict[str, dict[str, Any]] = {}
        broken_handles: set[str] = set()
        episode_refs = dict(prior_handle_to_ref)
        terminal = {}
        last_close = {}
        reset_index = -1
        following_boundary = {}
        confirmed_loss = set()
        for index in range(len(self._pending_native_events)-1, -1, -1):
            raw = self._pending_native_events[index]
            kind, handle = raw.get("native_kind"), raw.get("subject_a")
            if kind == "contact_identity_reset":
                reset_index = max(reset_index, index)
                following_boundary.clear()
            if kind in {"visible_unit_lost", "visible_unit_destroyed"}:
                last_close[handle] = max(last_close.get(handle, -1), index)
            if kind == "visible_unit_lost" and following_boundary.get(handle) == "visible_unit_destroyed":
                confirmed_loss.add(index)
            if kind in {"visible_unit_appeared", "visible_unit_destroyed"}:
                following_boundary[handle] = kind
        for index, raw in enumerate(self._pending_native_events):
            kind = str(raw.get("native_kind") or "")
            handle = raw.get("subject_a")
            handle_key = f"vehicle-handle-{handle}"
            if kind == "contact_identity_reset":
                for key, ref in list(episode_refs.items()):
                    if ref_kinds.get(ref) == "foreign_contact":
                        episode_refs.pop(key, None)
                        terminal[ref] = "unknown"
                        broken_handles.add(key)
                continue
            if kind == "visible_unit_appeared":
                candidate = current_handle_to_ref.get(handle_key)
                survives = index > max(last_close.get(handle, -1), reset_index)
                if handle_key not in episode_refs or handle_key in broken_handles:
                    if candidate and survives and (handle_key not in broken_handles or candidate != prior_handle_to_ref.get(handle_key)):
                        episode_refs[handle_key] = candidate
                    elif type(raw.get("native_sequence")) is int:
                        # A temporal episode need not survive either snapshot.
                        # The private native handle is salted into a scoped hash,
                        # never exposed and never reused as cross-gap identity.
                        episode_refs[handle_key] = "contact-episode-" + content_hash({
                            "match": self.scope.match_id, "perspective": self.scope.perspective_id,
                            "timeline": self.timeline_id, "epoch": world_epoch,
                            "handle": handle, "start": raw["native_sequence"]})[:32]
                        ref_kinds[episode_refs[handle_key]] = "foreign_contact"
            unit_ref = episode_refs.get(handle_key) or (
                current_handle_to_ref.get(handle_key) if handle_key not in broken_handles else None)
            if episode_assignments is not None and str(raw.get("native_sequence")) in episode_assignments:
                unit_ref = episode_assignments[str(raw["native_sequence"])]
                ref_kinds[unit_ref] = "own_unit" if unit_ref.startswith("own-unit-") else "foreign_contact"
            at = raw.get("to_tile_id") if isinstance(raw.get("to_tile_id"), int) \
                and raw.get("to_tile_id", -1) >= 0 else raw.get("from_tile_id")
            location = f"location-{at}" if isinstance(at, int) and at >= 0 else None
            if kind == "visible_unit_moved" and unit_ref and raw.get("continuous_visibility") is True:
                if handle_key in broken_handles and unit_ref == prior_handle_to_ref.get(handle_key):
                    continue
                own = ref_kinds.get(unit_ref) == "own_unit"
                row = move_by_contact.setdefault(unit_ref, {
                    "event_kind": "unit_moved" if own else "contact_moved",
                    ("unit_ref" if own else "contact_ref"): unit_ref,
                    "path": [], "turn": raw.get("turn", turn),
                })
                before, after = raw.get("from_tile_id"), raw.get("to_tile_id")
                if isinstance(before, int) and isinstance(after, int):
                    row["path"].append({
                        "from_location_ref": f"location-{before}",
                        "to_location_ref": f"location-{after}",
                        "evidence_kind": "observed_native_movement",
                        "continuous_visibility": True,
                        "occurrence_sequence": raw["native_sequence"],
                        "relationship": {"value": raw.get("relationship_at_occurrence", "unknown"),
                                         "epistemic_status": "current_at_occurrence" if raw.get("relationship_at_occurrence")
                                         in {"self", "hostile", "allied", "neutral"} else "unknown",
                                         "source": "native_visible_transition"},
                    })
                    row["from_location_ref"] = row["path"][0]["from_location_ref"]
                    row["to_location_ref"] = row["path"][-1]["to_location_ref"]
            elif kind in {"visible_unit_appeared", "visible_unit_lost",
                          "visible_unit_destroyed", "visible_unit_damaged"} and unit_ref:
                if kind == "visible_unit_lost":
                    broken_handles.add(handle_key)
                    terminal[unit_ref] = "unknown"
                    if index in confirmed_loss:
                        continue
                    episode_refs.pop(handle_key, None)
                elif kind == "visible_unit_destroyed":
                    terminal[unit_ref] = "confirmed_destroyed"
                    episode_refs.pop(handle_key, None)
                    broken_handles.add(handle_key)
                own = ref_kinds.get(unit_ref) == "own_unit"
                semantic_kind = {
                    "visible_unit_appeared": "unit_appeared" if own else "contact_appeared",
                    "visible_unit_lost": "unit_lost" if own else "contact_lost",
                    "visible_unit_destroyed": "unit_destroyed" if own else "contact_destroyed",
                    "visible_unit_damaged": "unit_damaged" if own else "contact_damaged",
                }[kind]
                event = {"event_kind": semantic_kind,
                         ("unit_ref" if own else "contact_ref"): unit_ref,
                         "location_ref": location, "turn": raw.get("turn", turn)}
                if kind == "visible_unit_damaged":
                    event.update({"observed_hp_before": raw.get("value_before"),
                                  "observed_hp_after": raw.get("value_after")})
                events.append(event)
            elif kind in {"owned_production_completed", "owned_queue_advanced", "owned_queue_exhausted",
                          "owned_production_repeat", "owned_production_fallback", "owned_project_interrupted"} and location:
                event = {
                    "event_kind": {"owned_production_completed": "production_completed",
                                   "owned_queue_advanced": "production_queue_advanced",
                                   "owned_queue_exhausted": "production_queue_exhausted",
                                   "owned_production_repeat": "production_repeat_selected",
                                   "owned_production_fallback": "production_fallback_selected",
                                   "owned_project_interrupted": "production_interrupted"}[kind],
                    "base_ref": f"base-{location}", "location_ref": location,
                    "item_name": str(raw.get("item_name") or "")[:64],
                    "turn": raw.get("turn", turn),
                    "occurrence_ref": "production-" + content_hash({
                        "session": self.session_id, "timeline": self.timeline_id,
                        "sequence": raw.get("native_sequence"), "kind": kind,
                    })[:24],
                    "evidence_kind": "owned_native_occurrence",
                }
                if kind == "owned_production_completed":
                    event["item_kind"] = {0: "unit", 1: "facility", 2: "secret_project"}.get(
                        raw.get("value_before"), "unknown")
                    handle = raw.get("value_after")
                    completed_unit = current_handle_to_ref.get(f"vehicle-handle-{handle}")
                    if event["item_kind"] == "unit" and type(handle) is int and handle >= 0:
                        # The owned-only birth hook already issued this stable
                        # identity. Preserve it even when the unit was destroyed
                        # before reconciliation; never substitute a compacted row.
                        completed_unit = (episode_assignments or {}).get(str(raw.get("native_sequence"))) or f"own-unit-{handle}"
                    if completed_unit:
                        event["unit_ref"] = completed_unit
                elif kind == "owned_project_interrupted":
                    event["item_kind"] = "secret_project"
                    event["reason"] = "project_no_longer_unbuilt"
                else:
                    event["queued_items_remaining"] = raw.get("value_before")
                events.append(event)
            elif kind.startswith("visible_base_"):
                semantic_kind = {
                    "visible_base_founded": "base_founded",
                    "visible_base_destroyed": "base_destroyed",
                    "visible_base_captured": "base_captured",
                }.get(kind)
                if semantic_kind and location:
                    base_ref = f"base-{location}"
                    event = {"event_kind": semantic_kind,
                             "base_ref": base_ref,
                             "location_ref": location, "turn": raw.get("turn", turn)}
                    if kind == "visible_base_captured":
                        prior_owner = f"faction-{raw.get('value_before')}"
                        owner = f"faction-{raw.get('value_after')}"
                        history = base_capture_history.get(base_ref)
                        if history is None:
                            history = {"initial_owner_ref": prior_owner, "capture_count": 0}
                            base_capture_history[base_ref] = history
                        history["capture_count"] = int(history["capture_count"]) + 1
                        if history["capture_count"] > 1 \
                                and owner == history["initial_owner_ref"]:
                            event["event_kind"] = "base_recaptured"
                        event.update({"prior_owner_ref": prior_owner,
                                      "owner_ref": owner,
                                      "capture_sequence": history["capture_count"]})
                    events.append(event)
            elif kind == "known_tile_changed" and location:
                events.append({"event_kind": "terrain_or_improvement_changed",
                               "location_ref": location,
                               "change_basis": "known_tile_cache_changed",
                               "cause": "not_determined",
                               "turn": raw.get("turn", turn)})
            elif kind == "known_tile_visibility" and location:
                events.append({"event_kind": "visibility_changed",
                               "location_ref": location,
                               "visible_now": bool(raw.get("value_after")),
                               "turn": raw.get("turn", turn)})
            elif kind in {
                "project_race_started", "project_race_changed", "project_race_halted",
                "project_race_continued", "project_race_nearing_completion",
            } and isinstance(raw.get("subject_a"), int):
                event = {
                    "event_kind": kind,
                    "project_ref": f"project-{raw['subject_a']}",
                    "turn": raw.get("turn", turn),
                    "provenance": "native_public_report",
                }
                if isinstance(raw.get("subject_b"), int) and raw["subject_b"] >= 1:
                    event["builder_ref"] = f"faction-{raw['subject_b']}"
                if kind == "project_race_changed" \
                        and isinstance(raw.get("value_before"), int) \
                        and raw["value_before"] >= 0:
                    event["prior_project_ref"] = f"project-{raw['value_before']}"
                events.append(event)
        terminal.update(episode_terminal or {})
        for row in move_by_contact.values():
            ref = row.get("contact_ref")
            if ref in terminal:
                row["current_whereabouts"] = terminal[ref]
        events.extend(move_by_contact.values())
        # Exact native duplicates can occur alongside reconciliation deltas.
        unique = {content_hash(item): item for item in events}
        return list(unique.values())

    def _publish_frozen_observation(
        self, package: Mapping[str, Any], *,
        prepared_projection: list[Any] | None = None,
        prepared_deltas: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Replay one immutable native observation publication idempotently.

        The private stage owns this package until projection replacement and
        acknowledgement complete.  Nothing in this method consults live native
        state, so a crash/retry cannot change an already-started publication.
        """
        publication_key = str(package["publication_hash"])
        cursor = int(package["observation_cursor"])
        self.observation_cursor = cursor
        turn = package.get("turn")
        year = package.get("year")
        continuity = str(package.get("continuity") or "complete")
        action_revision = str(package.get("action_revision") or "")
        temporal_events = [dict(item) for item in package.get("temporal_events", ())
                           if isinstance(item, Mapping)]
        identity_raw = package.get("identity")
        if not isinstance(identity_raw, Mapping):
            raise ObservationCollectorError("frozen_publication_identity_missing")
        identity = WorldIdentity(
            str(identity_raw["match_id"]), str(identity_raw["perspective_id"]),
            str(identity_raw["timeline_id"]), str(identity_raw["world_epoch"]),
        )
        frozen_bundle = package.get("projection_input")
        if not isinstance(frozen_bundle, Mapping):
            raise ObservationCollectorError("frozen_publication_input_missing")
        prior = self.world_store.load(self.scope, self.timeline_id)
        if prepared_projection is None:
            projected = PerspectiveProjector(identity, prior_projection=prior).project(
                frozen_bundle, observation_sequence=cursor,
            )
            projection_objects = list(projected["objects"])
        else:
            projection_objects = prepared_projection
        current_objects = [item.as_dict(provider_safe=False) for item in projection_objects]
        if _sequence_content_hash(current_objects) != str(
                package.get("projection_hash") or ""):
            raise ObservationCollectorError("frozen_publication_projection_mismatch")
        prior_objects = prior.get("objects", ()) if prior else ()
        deltas = prepared_deltas if prepared_deltas is not None \
            else net_deltas(prior_objects, current_objects)
        if _sequence_content_hash(deltas) != str(package.get("world_delta_hash") or "") \
                or len(deltas) != int(package.get("world_delta_count") or 0):
            raise ObservationCollectorError("frozen_publication_delta_mismatch")
        candidate = {**(prior or {}), "identity": identity.as_dict(), "objects": current_objects,
                     "world_revision": int((prior or {}).get("world_revision", 0)) + int(bool(deltas)),
                     "observation_cursor": cursor, "action_revision": action_revision,
                     "continuity": continuity}
        journal_events_written = 0
        observation_rows_written = 0
        continuity_gap = package.get("continuity_gap")
        if isinstance(continuity_gap, Mapping):
            gap_payload = {**dict(continuity_gap), "observation_sequence": cursor}
            event = self.journal.append(
                self.scope, "observation.continuity_gap", gap_payload,
                session_id=self.session_id,
                idempotency_key=f"native-publication:{publication_key}:continuity",
            )
            self.world_store.record_observation_projection(
                self.scope, self.timeline_id, {
                    "sequence": cursor, "kind": "continuity_gap", "turn": None,
                    "payload": gap_payload, "continuity": "incomplete",
                }, event["event_id"],
            )
            journal_events_written += 1
            observation_rows_written += 1
            if self.attention is not None:
                self.attention.enqueue(
                    "observation_continuity_gap", gap_payload,
                    observation_cursor=cursor, priority=100, critical=True,
                    session_id=self.session_id,
                    dedupe_key=f"continuity:{publication_key}",
                )
        world_cache_rows = []
        for batch_index, batch in enumerate(_bounded_batches([
                {**delta, "observation_sequence": cursor} for delta in deltas])):
            event = self.journal.append(
                self.scope, "observation.world_batch", {
                    "observation_sequence": cursor,
                    "batch_index": batch_index, "deltas": batch,
                }, session_id=self.session_id, turn=turn, year=year,
                idempotency_key=f"native-publication:{publication_key}:world:{batch_index}",
            )
            world_cache_rows.append((
                {"sequence": cursor, "kind": "world_batch", "turn": turn,
                 "payload": {"deltas": batch}, "continuity": continuity}, event["event_id"]))
            journal_events_written += 1
            observation_rows_written += 1

        if world_cache_rows:
            self.world_store.record_observation_projections(self.scope, self.timeline_id, world_cache_rows)
        prior_by_ref = {str(item["object_ref"]): provider_safe(item) for item in prior_objects}
        production_bases = {str(event.get("base_ref")) for event in temporal_events
                            if str(event.get("event_kind") or "").startswith("production_")}
        attention_groups: dict[tuple[bool, int], list[dict[str, Any]]] = {}
        for delta in deltas:
            if self.attention is None:
                break
            classification = _delta_attention({**delta,
                "previous": prior_by_ref.get(str(delta.get("object_ref"))),
                "production_occurrence": str(delta.get("object_ref")) in production_bases})
            if classification is not None:
                attention_groups.setdefault(classification, []).append(delta)
        if self.attention is not None:
            for (critical, priority), values in attention_groups.items():
                for batch in _bounded_batches(values, byte_limit=96_000, item_limit=64):
                    payload = {"delta": batch[0]} if len(batch) == 1 else {"deltas": batch}
                    self.attention.enqueue(
                        "world_change" if len(batch) == 1 else "world_changes", payload,
                        observation_cursor=cursor, priority=priority, critical=critical,
                        turn=turn, session_id=self.session_id,
                        dedupe_key=content_hash({"observation_cursor": cursor, "deltas": batch}),
                    )
        semantic_payloads = [{
            **semantic, "observation_sequence": cursor,
            "provenance": "direct_observation",
        } for semantic in temporal_events]
        semantic_cache_rows = []
        for batch_index, batch in enumerate(_bounded_batches(semantic_payloads)):
            event = self.journal.append(
                self.scope, "observation.semantic_batch", {
                    "observation_sequence": cursor,
                    "batch_index": batch_index, "events": batch,
                }, session_id=self.session_id, turn=turn, year=year,
                idempotency_key=f"native-publication:{publication_key}:semantic:{batch_index}",
            )
            semantic_cache_rows.append((
                {"sequence": cursor, "kind": "semantic_batch", "turn": turn,
                 "payload": {"events": batch}, "continuity": continuity}, event["event_id"]))
            journal_events_written += 1
            observation_rows_written += 1
        if semantic_cache_rows:
            self.world_store.record_observation_projections(self.scope, self.timeline_id, semantic_cache_rows)
        if self.attention is not None and (deltas or temporal_events):
            self.attention.capture_production_attention(
                temporal_events, observation_cursor=cursor, turn=turn, session_id=self.session_id)
            self.attention.evaluate_watches(
                [{**delta, **({"previous": prior_by_ref[str(delta["object_ref"])]}
                              if str(delta["object_ref"]) in prior_by_ref else {})}
                 for delta in deltas], temporal_events=temporal_events,
                observation_cursor=cursor, turn=turn, session_id=self.session_id,
                publication_projection=candidate,
            )
        reconciled = self.journal.append(
            self.scope, "observation.reconciled", {
                "observation_sequence": cursor, "continuity": continuity,
                "action_revision": action_revision,
                "object_count": len(projection_objects), "delta_count": len(deltas),
            }, session_id=self.session_id, turn=turn, year=year,
            idempotency_key=f"native-publication:{publication_key}:reconciled",
        )
        manifest = self.journal.replay(self.scope, sections=("manifest",))["manifest"]
        stored = self.world_store.replace_projection(
            self.scope, identity, projection_objects,
            observation_cursor=cursor, action_revision=action_revision,
            continuity=continuity, journal_head_hash=str(manifest["head_hash"]),
        )
        if self.attention is not None:
            # Availability is evaluated against the just-published current
            # projection. Keep the frozen package until its transition notice
            # is durable, so a crash can retry without losing the wakeup.
            self.attention.capture_current_plan_dependencies()
        self.world_store.acknowledge_native_observation_publication(
            self.scope, self.timeline_id, cursor,
        )
        self._restore_native_stage()
        self._continuous_contact_moves.clear()
        self._contact_identity_reset = False
        self._collection_metrics.update({
            "reconciled": True, "world_objects": len(projection_objects),
            "material_deltas": len(deltas), "semantic_events": len(temporal_events),
            "projection_rows_written": int(stored.get("projection_rows_written") or 0),
            "projection_object_rows_written": int(
                stored.get("projection_object_rows_written") or 0),
            "journal_events_written": journal_events_written + 1,
            "observation_rows_written": observation_rows_written,
            "attention_batches_written": sum(
                len(_bounded_batches(values, byte_limit=96_000, item_limit=64))
                for values in attention_groups.values()),
        })
        self._last_action_revision = action_revision
        return {"ok": True, "changed": stored["changed"], "deltas": len(deltas),
                "world_revision": stored["world_revision"],
                "observation_cursor": cursor,
                "journal_event_id": reconciled["event_id"],
                "publication_hash": publication_key}

    def collect_once(self) -> dict[str, Any]:
        """Serialize background and request-triggered reconciliation per perspective."""
        with self._collect_lock:
            started = time.perf_counter()
            self._collection_metrics = {
                "bridge_calls": 0,
                "native_backlog_before": len(self._pending_native_events),
            }
            try:
                result = self._collect_once_locked()
            except Exception as exc:
                self._collection_metrics["failed"] = True
                self._collection_metrics["failure_kind"] = type(exc).__name__
                # Rebuild all transient correlation state from the durable
                # private stage before an in-process retry.
                self._restore_native_stage()
                raise
            finally:
                self._collection_metrics["wall_ms"] = round(
                    (time.perf_counter() - started) * 1000, 3,
                )
                self._collection_metrics["native_backlog_after"] = len(
                    self._pending_native_events
                )
                try:
                    self.world_store.telemetry(
                        "observation_collector", "reconciliation_wall_ms",
                        float(self._collection_metrics["wall_ms"]), scope=self.scope,
                        timeline_id=self.timeline_id,
                        dimensions=dict(self._collection_metrics),
                    )
                except Exception:  # telemetry must not interrupt observation authority
                    LOG.warning("observation telemetry write failed", exc_info=True)
            return {**result, "collector_metrics": dict(self._collection_metrics)}

    def _collect_once_locked(self) -> dict[str, Any]:
        active_timeline = self.journal.timeline_id(self.scope)
        if active_timeline != self.timeline_id:
            self.timeline_id = active_timeline
            current_for_timeline = self.world_store.load(self.scope, self.timeline_id)
            self.observation_cursor = int(
                current_for_timeline.get("observation_cursor", 0)
                if current_for_timeline else 0
            )
            self._last_action_revision = ""
            self._restore_native_stage()
        elif self.observation_cursor == 0:
            current_for_timeline = self.world_store.load(self.scope, self.timeline_id)
            if current_for_timeline:
                self.observation_cursor = int(current_for_timeline["observation_cursor"])
        current = self.world_store.load(self.scope, self.timeline_id)
        stage = self.world_store.load_native_observation_stage(
            self.scope, self.timeline_id,
        )
        publication_cursor = stage.get("publication_observation_cursor")
        if publication_cursor is not None and current is not None \
                and int(current.get("observation_cursor") or 0) >= int(publication_cursor):
            # Head installation proves pre-head effects completed, not that
            # post-head dependency attention completed. Finish N before any N+1
            # native drain, including when native state reversed during downtime.
            package = stage.get("publication_package")
            if not isinstance(package, Mapping) or int(current["observation_cursor"]) != int(publication_cursor):
                raise ObservationCollectorError("pending_publication_head_mismatch")
            if current.get("identity") != package.get("identity") or current.get("action_revision") != package.get("action_revision"):
                raise ObservationCollectorError("pending_publication_head_identity_mismatch")
            if self.attention is not None:
                self.attention.capture_current_plan_dependencies()
            self.world_store.acknowledge_native_observation_publication(
                self.scope, self.timeline_id, int(publication_cursor))
            self._restore_native_stage()
            self._last_action_revision = str(package.get("action_revision") or "")
            return {"ok": True, "publication_recovered": True, "observation_cursor": int(publication_cursor),
                    "world_revision": current["world_revision"], "changed": False}
        elif publication_cursor is not None:
            package = stage.get("publication_package")
            if not isinstance(package, Mapping):
                raise ObservationCollectorError("native_publication_package_missing")
            # Finish publication N from its immutable private package. Native
            # activity that happened after the freeze remains in the bridge
            # ring until a later collector pass creates publication N+1.
            self._collection_metrics.update({
                "native_feed_pages": 0, "native_events_drained": 0,
                "native_continuity_incomplete": (
                    str(package.get("continuity") or "complete") == "incomplete"
                ), "publication_recovered": True,
            })
            return self._publish_frozen_observation(package)
        feed = self._drain_native_feed()
        self._collection_metrics.update({
            "native_feed_pages": int(feed.get("drained_pages") or 0),
            "native_events_drained": int(feed.get("drained_event_count") or 0),
            "native_continuity_incomplete": feed.get("continuity") == "incomplete",
        })
        action_revision = str(feed.get("action_revision") or "")
        current = self.world_store.load(self.scope, self.timeline_id)
        should_reconcile = action_revision != self._last_action_revision \
            or feed.get("reconciliation_required") is True \
            or int(feed.get("drained_event_count") or 0) > 0 \
            or bool(self._pending_native_events) or current is None
        if not should_reconcile:
            self._collection_metrics.update({
                "reconciled": False, "projection_rows_written": 0,
                "journal_events_written": 0, "observation_rows_written": 0,
            })
            return {"ok": True, "changed": False, "observation_cursor": self.observation_cursor}
        # State may move while bounded pages are drained. Retry a small fixed
        # number; never wait inside the native request path.
        last_error: Exception | None = None
        for _ in range(3):
            try:
                bundle = self._bundle()
                self._preserve_project_report_history(bundle, current)
                break
            except ObservationCollectorError as exc:
                last_error = exc
                time.sleep(0.05)
        else:
            raise last_error or ObservationCollectorError("world_reconciliation_failed")
        # Reconciliation itself is one material observation. Every object delta
        # emitted from it shares this cursor, while journal event IDs preserve
        # the individual facts at that observation boundary.
        stage = self.world_store.load_native_observation_stage(
            self.scope, self.timeline_id,
        )
        assigned_cursor = self.observation_cursor + 1
        self.observation_cursor = int(assigned_cursor)
        world_epoch = self._world_epoch(bundle, current)
        identity = WorldIdentity(self.scope.match_id, self.scope.perspective_id,
                                 self.timeline_id, world_epoch)
        from smacx_temporal_episodes import advance_episodes
        prior_objects = current.get("objects", ()) if current else ()
        gaps = list(stage.get("continuity_gaps") or [])
        if not gaps and stage.get("continuity_gap"):
            gaps = [{**stage["continuity_gap"], "before_native_sequence": min(
                (int(row["native_sequence"]) for row in stage["events"]), default=0)}]
        episode_state, episode_assignments, episode_terminal = advance_episodes(
            identity=identity, prior_objects=prior_objects, state=stage.get("episode_state", {}),
            events=stage["events"], gaps=gaps,
            owned_keys={str(row.get("native_observation_key") or "vehicle-handle-"+str(row.get("own_unit_ref") or "").removeprefix("own-unit-"))
                        for row in bundle.get("units", ()) if row.get("owned")})
        # This read does not consume or acknowledge later events. An empty,
        # complete feed proves the cut stayed stable through snapshot collection.
        probe = self._bridge("observation_feed", after_sequence=self.native_after_sequence, limit=1)
        stable_cut = (probe.get("ok") is True and not probe.get("events") and not probe.get("has_more")
                      and probe.get("continuity") == "complete"
                      and int(probe.get("next_sequence", -1)) == self.native_after_sequence
                      and str(probe.get("action_revision") or "") == str(bundle.get("action_revision") or ""))
        bundle["_native_temporal_authority"] = True
        bundle["_temporal_contact_refs"] = {}
        if not stable_cut:
            bundle["_contact_identity_reset"] = True
            bundle["_continuous_visible_contact_moves"] = {}
        else:
            for unit in bundle.get("units", ()):
                key = str(unit.get("native_observation_key") or unit.get("id"))
                episode = episode_state["open"].get(key)
                if not unit.get("owned") and episode and episode.get("location") == f"location-{unit.get('tile_id')}":
                    bundle["_temporal_contact_refs"][key] = episode["ref"]
        projector = PerspectiveProjector(identity, prior_projection=current)
        projection = projector.project(bundle, observation_sequence=self.observation_cursor)
        if stable_cut:
            for item in projection["objects"]:
                key = item.metadata.get("native_observation_key")
                if item.kind == "foreign_contact" and item.status == "active" and key not in episode_state["open"]:
                    episode_state["open"][key] = {"ref":item.object_ref, "location":item.location_ref}
        self._continuous_contact_moves.clear()
        self._contact_identity_reset = False
        prior_objects = current.get("objects", ()) if current else ()
        # Reconciliation and native-handle translation operate behind the
        # provider boundary and require collector-private stable handles.  The
        # journal/world-result serializers sanitize recursively at their own
        # boundary; stripping here broke first-frame native-event correlation.
        current_objects = [item.as_dict(provider_safe=False)
                           for item in projection["objects"]]
        deltas = net_deltas(prior_objects, current_objects)
        prior_by_ref = {str(item.get("object_ref")): item for item in prior_objects
                        if isinstance(item, Mapping) and item.get("object_ref")}
        semantic_deltas = [
            {**delta, **({"previous": prior_by_ref[str(delta["object_ref"])]}
                         if str(delta.get("object_ref")) in prior_by_ref else {})}
            for delta in deltas
        ]
        coalesce_started = time.perf_counter()
        native_events = self._coalesce_native_events(
            current_objects=current_objects, prior_objects=list(prior_objects),
            turn=bundle.get("turn"), world_epoch=world_epoch,
            episode_assignments=episode_assignments, episode_terminal=episode_terminal,
        )
        self._collection_metrics["native_coalesce_ms"] = round(
            (time.perf_counter() - coalesce_started) * 1000, 3,
        )
        temporal_events = _provider_safe_temporal_events(
            semantic_deltas, [*list(projection.get("temporal_events", ())), *native_events],
            turn=bundle.get("turn"),
        )
        publication = {
            "schema": "smacx.private-observation-publication.v1",
            "identity": identity.as_dict(),
            "source_through_sequence": int(stage["staged_after_sequence"]),
            "source_native_sequences": [
                int(item["native_sequence"]) for item in stage["events"]
                if isinstance(item.get("native_sequence"), int)
            ],
            "action_revision": str(bundle.get("action_revision") or ""),
            "turn": bundle.get("turn"), "year": bundle.get("year"),
            "continuity": ("incomplete" if feed.get("continuity") == "incomplete"
                           else "complete"),
            "continuity_gap": stage.get("continuity_gap"),
            # The entitlement-filtered bundle is the compact immutable
            # projection candidate. Projection and delta hashes make replay a
            # checked deterministic derivation without duplicating every Huge
            # map object twice in the private stage.
            "projection_input": bundle,
            "projection_hash": _sequence_content_hash(current_objects),
            "world_delta_hash": _sequence_content_hash(deltas),
            "world_delta_count": len(deltas),
            "temporal_events": temporal_events,
            "episode_state": episode_state,
        }
        frozen = self.world_store.begin_native_observation_publication(
            self.scope, self.timeline_id, self.observation_cursor, publication,
        )
        package = frozen.get("publication_package")
        if not isinstance(package, Mapping):
            raise ObservationCollectorError("native_publication_freeze_failed")
        return self._publish_frozen_observation(
            package, prepared_projection=list(projection["objects"]),
            prepared_deltas=deltas,
        )
