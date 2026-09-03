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
    if kind in {"victory", "victory_state", "global_event", "council_state"}:
        return True, 95
    if kind == "foreign_contact":
        return True, 90
    if kind == "base":
        if change in {"appeared", "removed"} or values.get("threatened") \
                or values.get("drone_riots"):
            return True, 90
        return False, 55
    if kind in {"project", "scenario_rules", "game_settings"}:
        return True, 85
    if kind in {"faction", "economy_state", "research_state", "social_state",
                "technology_state"}:
        return False, 65
    if kind in {"own_unit", "location"}:
        return False, 40
    return False, 45


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
            page = self.bridge_call("perspective_world_page", domain=domain,
                                    cursor=cursor, limit=128)
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
            page = self.bridge_call(operation, offset=offset, limit=limit, **dict(extra or {}))
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
        rich_factions = self.bridge_call("list_factions")
        technologies = self.bridge_call("list_technologies")
        rich_base_by_id = {int(item["id"]): item for item in rich_bases if "id" in item}
        collected["bases"] = [
            ({**item, **rich_base_by_id[int(item["id"])]}
             if item.get("owned") and int(item.get("id", -1)) in rich_base_by_id else item)
            for item in collected["bases"]
        ]
        rich_unit_by_id = {int(item["id"]): item for item in rich_units if "id" in item}
        normalized_units = []
        for item in collected["units"]:
            native_id = int(item.get("id", -1))
            rich = rich_unit_by_id.get(native_id)
            if rich is None:
                normalized_units.append(item)
                continue
            merged = {**item, **rich, "owned": bool(item.get("owned")),
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
                merged["transport_unit_ref"] = f"own-unit-{transport_id}"
            normalized_units.append(merged)
        collected["units"] = normalized_units
        if rich_factions.get("ok") and isinstance(rich_factions.get("items"), list):
            full = {int(item["id"]): item for item in rich_factions["items"]
                    if isinstance(item, Mapping) and "id" in item}
            collected["factions"] = [
                {**item, **full.get(int(item.get("id", -1)), {})}
                for item in collected["factions"]
            ]
        envelope = self.bridge_call("semantic_snapshot")
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
        }
        # All native rows are already perspective-filtered.  The explicit
        # entitlement pass is retained as an independently testable boundary
        # for richer Pact/infiltration/Governor/satellite adapters.
        own_ref = str(bundle["own_faction_ref"])
        pact = frozenset(
            str(item.get("faction_ref") or f"faction-{item.get('id')}")
            for item in collected["factions"]
            if str(item.get("relationship") or item.get("treaty") or "").lower() == "pact"
        )
        infiltrated = frozenset(
            str(item.get("faction_ref") or f"faction-{item.get('id')}")
            for item in collected["factions"] if item.get("infiltrated") is True
        )
        return sanitize_bundle(bundle, PerspectiveEntitlements(
            faction_ref=own_ref,
            unity_survey=bool(summary.get("unity_survey", False)),
            governor=bool(summary.get("is_governor", False)),
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
            }
            saw_inbound_chat = saw_inbound_chat or payload["native_kind"] == "chat_inbound"
            event = self.journal.append(
                self.scope, "observation.native_event", payload,
                session_id=self.session_id, turn=item.get("turn"),
            )
            self.world_store.record_observation_projection(
                self.scope, self.timeline_id, {"sequence": payload["observation_sequence"],
                "kind": "native_event", "turn": item.get("turn"), "payload": payload,
                "continuity": str(feed.get("continuity", "complete"))}, event["event_id"],
            )
        self.native_after_sequence = max(
            self.native_after_sequence, int(feed.get("next_sequence") or 0),
        )
        if saw_inbound_chat and self.chat_capture is not None:
            self.chat_capture()

    def collect_once(self) -> dict[str, Any]:
        """Serialize background and request-triggered reconciliation per perspective."""
        with self._collect_lock:
            return self._collect_once_locked()

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
        elif self.observation_cursor == 0:
            current_for_timeline = self.world_store.load(self.scope, self.timeline_id)
            if current_for_timeline:
                self.observation_cursor = int(current_for_timeline["observation_cursor"])
        feed = self.bridge_call("observation_feed", after_sequence=self.native_after_sequence,
                                limit=256)
        if not feed.get("ok"):
            raise ObservationCollectorError("native_observation_feed_failed")
        self._append_native_feed(feed)
        action_revision = str(feed.get("action_revision") or "")
        current = self.world_store.load(self.scope, self.timeline_id)
        should_reconcile = action_revision != self._last_action_revision \
            or feed.get("reconciliation_required") is True or current is None
        if not should_reconcile:
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
        prior_objects = current.get("objects", ()) if current else ()
        current_objects = [item.as_dict() for item in projection["objects"]]
        deltas = net_deltas(prior_objects, current_objects)
        for delta in deltas:
            event = self.journal.append(
                self.scope, "observation.world_object", {
                    **delta, "observation_sequence": self.observation_cursor,
                }, session_id=self.session_id, turn=bundle.get("turn"), year=bundle.get("year"),
            )
            self.world_store.record_observation_projection(
                self.scope, self.timeline_id,
                {"sequence": self.observation_cursor, "kind": "world_object",
                 "turn": bundle.get("turn"), "payload": delta,
                 "continuity": str(feed.get("continuity", "complete"))},
                event["event_id"],
            )
            if self.attention is not None:
                classification = _delta_attention(delta)
                if classification is None:
                    continue
                critical, priority = classification
                self.attention.enqueue(
                    "world_change", {"delta": delta},
                    observation_cursor=self.observation_cursor, priority=priority,
                    critical=critical, turn=bundle.get("turn"), session_id=self.session_id,
                    # The same semantic transition may legitimately recur later
                    # (for example, a contact leaves and re-enters visibility).
                    # Cursor scope prevents duplicate delivery for this persisted
                    # observation without suppressing the later event.
                    dedupe_key=content_hash({
                        "observation_cursor": self.observation_cursor,
                        "delta": delta,
                    }),
                )
        if self.attention is not None and deltas:
            self.attention.evaluate_watches(
                deltas, observation_cursor=self.observation_cursor,
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
        self._last_action_revision = str(bundle.get("action_revision") or "")
        return {"ok": True, "changed": stored["changed"], "deltas": len(deltas),
                "world_revision": stored["world_revision"],
                "observation_cursor": self.observation_cursor,
                "journal_event_id": reconciled["event_id"]}
