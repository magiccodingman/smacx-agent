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
from smacx_world_types import WorldIdentity, canonical_json, content_hash
from smacx_attention import AttentionService
from smacx_entitlements import PerspectiveEntitlements, sanitize_bundle


LOG = logging.getLogger("smacx.observation")


def _delta_attention(delta: Mapping[str, Any]) -> tuple[bool, int] | None:
    """Classify material deltas without turning routine bookkeeping into alarms."""
    current = delta.get("current") if isinstance(delta.get("current"), Mapping) else {}
    kind = str(current.get("kind") or delta.get("prior_kind") or "world_change")
    change = str(delta.get("change") or "changed")
    fields = current.get("fields") if isinstance(current.get("fields"), Mapping) else {}
    values = {name: item.get("value") for name, item in fields.items()
              if isinstance(item, Mapping)}
    if kind in {"victory", "victory_state", "victory_posture", "global_event",
                "council_state", "ecology_state", "planetary_state"}:
        return True, 95
    if kind == "foreign_contact":
        return True, 90
    if kind == "base":
        if change in {"appeared", "removed"} or values.get("threatened") \
                or values.get("drone_riots"):
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
        elif kind == "location" and change == "changed":
            changed_fields = [name for name in ("terrain", "features", "owner_ref")
                              if _field_value(current, name) != _field_value(previous, name)]
            if changed_fields:
                events.append({
                    "event_kind": "terrain_or_improvement_changed",
                    "location_ref": object_ref, "changed_fields": changed_fields,
                    "turn": turn,
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

    def _append_native_feed(self, feed: Mapping[str, Any]) -> None:
        saw_inbound_chat = False
        if feed.get("continuity") == "incomplete":
            self._continuous_contact_moves.clear()
            self._contact_identity_reset = True
            self.observation_cursor += 1
            gap_payload = {
                "observation_sequence": self.observation_cursor,
                "native_after_sequence": self.native_after_sequence,
                "native_next_sequence": int(feed.get("next_sequence") or 0),
                "lost_after_observation_sequence": feed.get("lost_after_observation_sequence"),
                "reconciliation_required": True,
            }
            event = self.journal.append(
                self.scope, "observation.continuity_gap", gap_payload,
                session_id=self.session_id,
            )
            self.world_store.record_observation_projection(
                self.scope, self.timeline_id, {
                    "sequence": self.observation_cursor,
                    "kind": "continuity_gap", "turn": None,
                    "payload": gap_payload, "continuity": "incomplete",
                }, event["event_id"],
            )
            if self.attention is not None:
                self.attention.enqueue(
                    "observation_continuity_gap", gap_payload,
                    observation_cursor=self.observation_cursor,
                    priority=100, critical=True, session_id=self.session_id,
                    dedupe_key=f"continuity:{self.timeline_id}:{self.observation_cursor}",
                )
        for item in feed.get("events", ()):
            if not isinstance(item, Mapping):
                continue
            self.observation_cursor += 1
            payload = {
                "observation_sequence": self.observation_cursor,
                "native_sequence": int(item["sequence"]),
                "native_kind": str(item.get("kind") or "unknown"),
                "subject_a": item.get("subject_a"), "subject_b": item.get("subject_b"),
                "from_tile_id": item.get("from_tile_id"),
                "to_tile_id": item.get("to_tile_id"),
                "value_before": item.get("value_before"),
                "value_after": item.get("value_after"),
                "continuous_visibility": bool(item.get("continuous_visibility", False)),
            }
            if payload["native_kind"] == "visible_unit_moved" \
                    and payload["continuous_visibility"] \
                    and isinstance(payload["subject_a"], int) \
                    and isinstance(payload["from_tile_id"], int) \
                    and isinstance(payload["to_tile_id"], int):
                key = f"vehicle-handle-{payload['subject_a']}"
                self._continuous_contact_moves.setdefault(key, []).append({
                    "from": f"location-{payload['from_tile_id']}",
                    "to": f"location-{payload['to_tile_id']}",
                    "native_sequence": payload["native_sequence"],
                })
            elif payload["native_kind"] in {
                "visible_unit_lost", "visible_unit_destroyed",
            } and isinstance(payload["subject_a"], int):
                self._continuous_contact_moves.pop(
                    f"vehicle-handle-{payload['subject_a']}", None,
                )
            if payload["native_kind"] == "contact_identity_reset":
                self._continuous_contact_moves.clear()
                self._contact_identity_reset = True
            saw_inbound_chat = saw_inbound_chat or payload["native_kind"] == "chat_inbound"
            # Collector-private handles stay in a bounded in-memory staging
            # buffer until reconciliation can translate them to semantic refs.
            self._pending_native_events.append({**payload, "turn": item.get("turn")})
            if len(self._pending_native_events) > 1024:
                self._pending_native_events = self._pending_native_events[-1024:]
        self.native_after_sequence = max(
            self.native_after_sequence, int(feed.get("next_sequence") or 0),
        )
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
    ) -> list[dict[str, Any]]:
        """Translate private POD transitions into authoritative semantic events."""
        handle_to_ref: dict[str, str] = {}
        ref_kinds: dict[str, str] = {}
        for item in [*prior_objects, *current_objects]:
            if not isinstance(item, Mapping):
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
            key = metadata.get("native_observation_key")
            if key:
                handle_to_ref[str(key)] = str(item.get("object_ref"))
                ref_kinds[str(item.get("object_ref"))] = str(item.get("kind") or "")
            if item.get("kind") == "own_unit" and metadata.get("native_handle") is not None:
                handle_to_ref[f"vehicle-handle-{metadata['native_handle']}"] = str(item.get("object_ref"))
                ref_kinds[str(item.get("object_ref"))] = "own_unit"
        events: list[dict[str, Any]] = []
        move_by_contact: dict[str, dict[str, Any]] = {}
        base_capture_history: dict[str, dict[str, Any]] = {}
        for raw in self._pending_native_events:
            kind = str(raw.get("native_kind") or "")
            handle = raw.get("subject_a")
            unit_ref = handle_to_ref.get(f"vehicle-handle-{handle}")
            at = raw.get("to_tile_id") if isinstance(raw.get("to_tile_id"), int) \
                and raw.get("to_tile_id", -1) >= 0 else raw.get("from_tile_id")
            location = f"location-{at}" if isinstance(at, int) and at >= 0 else None
            if kind == "visible_unit_moved" and unit_ref:
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
                    })
                    row["from_location_ref"] = row["path"][0]["from_location_ref"]
                    row["to_location_ref"] = row["path"][-1]["to_location_ref"]
            elif kind in {"visible_unit_appeared", "visible_unit_lost",
                          "visible_unit_destroyed", "visible_unit_damaged"} and unit_ref:
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
        events.extend(move_by_contact.values())
        self._pending_native_events.clear()
        # Exact native duplicates can occur alongside reconciliation deltas.
        unique = {content_hash(item): item for item in events}
        return list(unique.values())

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
            self.native_after_sequence = 0
            self._last_action_revision = ""
            self._continuous_contact_moves.clear()
            self._contact_identity_reset = True
        elif self.observation_cursor == 0:
            current_for_timeline = self.world_store.load(self.scope, self.timeline_id)
            if current_for_timeline:
                self.observation_cursor = int(current_for_timeline["observation_cursor"])
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
            or int(feed.get("drained_event_count") or 0) > 0 or current is None
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
                break
            except ObservationCollectorError as exc:
                last_error = exc
                time.sleep(0.05)
        else:
            raise last_error or ObservationCollectorError("world_reconciliation_failed")
        # Reconciliation itself is one material observation. Every object delta
        # emitted from it shares this cursor, while journal event IDs preserve
        # the individual facts at that observation boundary.
        self.observation_cursor += 1
        world_epoch = self._world_epoch(bundle, current)
        identity = WorldIdentity(self.scope.match_id, self.scope.perspective_id,
                                 self.timeline_id, world_epoch)
        projector = PerspectiveProjector(identity, prior_projection=current)
        projection = projector.project(bundle, observation_sequence=self.observation_cursor)
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
            turn=bundle.get("turn"),
        )
        self._collection_metrics["native_coalesce_ms"] = round(
            (time.perf_counter() - coalesce_started) * 1000, 3,
        )
        temporal_events = _provider_safe_temporal_events(
            semantic_deltas, [*list(projection.get("temporal_events", ())), *native_events],
            turn=bundle.get("turn"),
        )
        journal_events_written = 0
        observation_rows_written = 0
        delta_batches = _bounded_batches([
            {**delta, "observation_sequence": self.observation_cursor}
            for delta in deltas
        ])
        for batch_index, batch in enumerate(delta_batches):
            event = self.journal.append(
                self.scope, "observation.world_batch", {
                    "observation_sequence": self.observation_cursor,
                    "batch_index": batch_index, "deltas": batch,
                }, session_id=self.session_id, turn=bundle.get("turn"), year=bundle.get("year"),
            )
            self.world_store.record_observation_projection(
                self.scope, self.timeline_id,
                {"sequence": self.observation_cursor, "kind": "world_batch",
                 "turn": bundle.get("turn"), "payload": {"deltas": batch},
                 "continuity": str(feed.get("continuity", "complete"))},
                event["event_id"],
            )
            journal_events_written += 1
            observation_rows_written += 1

        attention_groups: dict[tuple[bool, int], list[dict[str, Any]]] = {}
        for delta in deltas:
            if self.attention is not None:
                classification = _delta_attention(delta)
                if classification is None:
                    continue
                critical, priority = classification
                attention_groups.setdefault((critical, priority), []).append(delta)
        if self.attention is not None:
            for (critical, priority), values in attention_groups.items():
                for batch in _bounded_batches(values, byte_limit=96_000, item_limit=64):
                    payload = {"delta": batch[0]} if len(batch) == 1 else {"deltas": batch}
                    self.attention.enqueue(
                        "world_change" if len(batch) == 1 else "world_changes", payload,
                        observation_cursor=self.observation_cursor, priority=priority,
                        critical=critical, turn=bundle.get("turn"), session_id=self.session_id,
                        dedupe_key=content_hash({
                            "observation_cursor": self.observation_cursor,
                            "deltas": batch,
                        }),
                    )
        semantic_payloads = [{
                **semantic, "observation_sequence": self.observation_cursor,
                "provenance": "direct_observation",
            } for semantic in temporal_events]
        for batch_index, batch in enumerate(_bounded_batches(semantic_payloads)):
            event = self.journal.append(
                self.scope, "observation.semantic_batch", {
                    "observation_sequence": self.observation_cursor,
                    "batch_index": batch_index, "events": batch,
                },
                session_id=self.session_id, turn=bundle.get("turn"), year=bundle.get("year"),
            )
            self.world_store.record_observation_projection(
                self.scope, self.timeline_id,
                {"sequence": self.observation_cursor, "kind": "semantic_batch",
                 "turn": bundle.get("turn"), "payload": {"events": batch},
                 "continuity": str(feed.get("continuity", "complete"))},
                event["event_id"],
            )
            journal_events_written += 1
            observation_rows_written += 1
        if self.attention is not None and (deltas or temporal_events):
            self.attention.evaluate_watches(
                deltas, temporal_events=temporal_events,
                observation_cursor=self.observation_cursor,
                turn=bundle.get("turn"), session_id=self.session_id,
            )
        reconciled = self.journal.append(
            self.scope, "observation.reconciled", {
                "observation_sequence": self.observation_cursor,
                "continuity": "complete" if feed.get("continuity") != "incomplete" else "incomplete",
                "action_revision": bundle.get("action_revision"),
                "object_count": len(current_objects), "delta_count": len(deltas),
            }, session_id=self.session_id, turn=bundle.get("turn"), year=bundle.get("year"),
        )
        manifest = self.journal.replay(self.scope)["manifest"]
        stored = self.world_store.replace_projection(
            self.scope, identity, projection["objects"],
            observation_cursor=self.observation_cursor,
            action_revision=str(bundle.get("action_revision") or ""),
            continuity="complete" if feed.get("continuity") != "incomplete" else "incomplete",
            journal_head_hash=str(manifest["head_hash"]),
        )
        self._collection_metrics.update({
            "reconciled": True,
            "world_objects": len(current_objects),
            "material_deltas": len(deltas),
            "semantic_events": len(temporal_events),
            "projection_rows_written": int(stored.get("projection_rows_written") or 0),
            "projection_object_rows_written": int(
                stored.get("projection_object_rows_written") or 0
            ),
            "journal_events_written": journal_events_written + 1,
            "observation_rows_written": observation_rows_written,
            "attention_batches_written": sum(
                len(_bounded_batches(values, byte_limit=96_000, item_limit=64))
                for values in attention_groups.values()
            ),
        })
        self._last_action_revision = str(bundle.get("action_revision") or "")
        return {"ok": True, "changed": stored["changed"], "deltas": len(deltas),
                "world_revision": stored["world_revision"],
                "observation_cursor": self.observation_cursor,
                "journal_event_id": reconciled["event_id"]}
