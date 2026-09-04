"""Durable at-least-once sovereign attention, watches, operations, and leases."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any, Iterable, Mapping
import uuid

from smacx_journal import CampaignJournal
from smacx_store import MemoryScope, SmacxStore
from smacx_world_types import canonical_json, content_hash, material_hash, provider_safe, require_ref
from smacx_world_store import WorldStore


WATCH_KINDS = frozenset({
    "base_status", "base_threat", "faction_relationship", "faction_activity",
    "region_entry", "region_exit", "frontier_contact", "rendezvous_progress",
    "route_disruption", "project_progress", "global_system_progress",
    "resource_threshold", "economic_threshold",
})
OPERATION_STATUSES = frozenset({"active", "stale", "completed", "expired", "invalid"})


class AttentionError(ValueError):
    pass


class AttentionService:
    def __init__(self, store: SmacxStore, journal: CampaignJournal, scope: MemoryScope) -> None:
        self.store = store
        self.journal = journal
        self.scope = scope
        self.world_store = WorldStore(store)

    @property
    def timeline_id(self) -> str:
        return self.store.active_timeline_id(self.scope)

    def enqueue(
        self, kind: str, payload: Mapping[str, Any], *, observation_cursor: int,
        priority: int = 50, critical: bool = False, turn: int | None = None,
        session_id: str | None = None, dedupe_key: str = "",
    ) -> dict[str, Any]:
        priority = min(max(int(priority), 0), 100)
        timeline = self.timeline_id
        normalized = {"kind": kind, "payload": payload, "cursor": observation_cursor,
                      "dedupe_key": dedupe_key}
        dependency = content_hash(normalized)
        if dedupe_key:
            with self.store._connect() as connection:
                duplicate = connection.execute(
                    "SELECT * FROM attention_items WHERE match_id=? AND agent_id=? "
                    "AND perspective_id=? AND timeline_id=? AND dependency_hash=? "
                    "AND status IN ('queued','leased','responded','acknowledged') "
                    "ORDER BY captured_unix DESC LIMIT 1",
                    (self.scope.match_id, self.scope.agent_id, self.scope.perspective_id,
                     timeline, dependency),
                ).fetchone()
            if duplicate:
                return {**dict(duplicate), "deduplicated": True}
        captured = time.time()
        journal_event = self.journal.append(
            self.scope, "attention.captured", {
                "kind": kind, "payload": dict(payload), "observation_cursor": observation_cursor,
                "priority": priority, "critical": bool(critical), "dependency_hash": dependency,
            }, session_id=session_id, turn=turn,
        )
        attention_id = "attention-" + uuid.uuid4().hex
        with self.store.transaction() as connection:
            head = connection.execute(
                "SELECT next_sequence FROM attention_heads WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=?", self._key(timeline),
            ).fetchone()
            attention_sequence = int(head["next_sequence"]) if head else 1
            connection.execute(
                "INSERT INTO attention_heads(match_id,agent_id,perspective_id,timeline_id," \
                "next_sequence,acknowledged_cursor,updated_unix) VALUES(?,?,?,?,?,0,?) " \
                "ON CONFLICT(match_id,agent_id,perspective_id,timeline_id) DO UPDATE SET " \
                "next_sequence=excluded.next_sequence,updated_unix=excluded.updated_unix",
                (*self._key(timeline), attention_sequence + 1, captured),
            )
            connection.execute(
                "INSERT INTO attention_items(attention_id,match_id,agent_id,perspective_id," \
                "timeline_id,attention_sequence,observation_cursor,attention_kind,priority,critical,payload_json," \
                "dependency_hash,captured_unix,persisted_unix,status) " \
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'queued')",
                (attention_id, self.scope.match_id, self.scope.agent_id,
                 self.scope.perspective_id, timeline, attention_sequence,
                 int(observation_cursor), kind, priority,
                 int(bool(critical)), canonical_json(payload), dependency, captured,
                 journal_event["recorded_unix"]),
            )
            queue_depth = int(connection.execute(
                "SELECT COUNT(*) AS count FROM attention_items WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? AND status='queued'",
                self._key(timeline),
            ).fetchone()["count"])
        self.world_store.telemetry(
            "attention", "queue_depth", queue_depth, scope=self.scope,
            timeline_id=timeline, dimensions={"kind": kind, "critical": bool(critical)},
        )
        return {"attention_id": attention_id, "status": "queued",
                "attention_sequence": attention_sequence,
                "journal_event_id": journal_event["event_id"], "deduplicated": False}

    def lease(self, episode_id: str, *, limit: int = 32, ttl_seconds: int = 300) -> dict[str, Any]:
        require_ref(episode_id, "episode_id")
        timeline = self.timeline_id
        now = time.time()
        lease_id = "attention-lease-" + uuid.uuid4().hex
        with self.store.transaction() as connection:
            # Process loss makes prior placed/leased items eligible for
            # redelivery without changing their attention identity.
            expired = connection.execute(
                "SELECT attention_lease_id FROM attention_leases WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? AND status IN ('leased','placed') "
                "AND expires_unix<=?", (*self._key(timeline), now),
            ).fetchall()
            for row in expired:
                self._abandon_locked(connection, str(row["attention_lease_id"]))
            existing = connection.execute(
                "SELECT * FROM attention_leases WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? AND episode_id=? "
                "AND status IN ('leased','placed','responded') AND expires_unix>? "
                "ORDER BY leased_unix DESC LIMIT 1",
                (*self._key(timeline), episode_id, now),
            ).fetchone()
            if existing:
                rows = connection.execute(
                    "SELECT i.*,li.redelivery_count FROM attention_items i JOIN "
                    "attention_lease_items li ON li.attention_id=i.attention_id "
                    "WHERE li.attention_lease_id=? ORDER BY i.critical DESC,i.priority DESC,"
                    "i.attention_sequence", (existing["attention_lease_id"],),
                ).fetchall()
                items = []
                for row in rows:
                    item = dict(row)
                    item["payload"] = json.loads(item.pop("payload_json"))
                    item["redelivered"] = int(item.pop("redelivery_count")) > 0
                    items.append(item)
                return {
                    "attention_lease_id": str(existing["attention_lease_id"]),
                    "through_cursor": int(existing["through_cursor"]),
                    "items": items, "status": str(existing["status"]), "reused": True,
                }
            rows = connection.execute(
                "SELECT * FROM attention_items WHERE match_id=? AND agent_id=? AND perspective_id=? "
                "AND timeline_id=? AND status='queued' ORDER BY critical DESC,priority DESC,"
                "attention_sequence ASC LIMIT ?", (*self._key(timeline), min(max(limit, 1), 64)),
            ).fetchall()
            through = max((int(row["attention_sequence"]) for row in rows), default=0)
            connection.execute(
                "INSERT INTO attention_leases(attention_lease_id,match_id,agent_id,perspective_id," \
                "timeline_id,episode_id,through_cursor,status,leased_unix,expires_unix) " \
                "VALUES(?,?,?,?,?,?,?,'leased',?,?)",
                (lease_id, *self._key(timeline), episode_id, through, now,
                 now + min(max(ttl_seconds, 30), 3600)),
            )
            for row in rows:
                prior_count = connection.execute(
                    "SELECT COALESCE(MAX(redelivery_count),-1) AS value FROM attention_lease_items "
                    "WHERE attention_id=?", (row["attention_id"],),
                ).fetchone()["value"]
                connection.execute(
                    "INSERT INTO attention_lease_items(attention_lease_id,attention_id,redelivery_count) "
                    "VALUES(?,?,?)", (lease_id, row["attention_id"], int(prior_count) + 1),
                )
                connection.execute("UPDATE attention_items SET status='leased' WHERE attention_id=?",
                                   (row["attention_id"],))
        items = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            item["redelivered"] = self._redelivery_count(lease_id, item["attention_id"]) > 0
            items.append(item)
        redeliveries = sum(1 for item in items if item["redelivered"])
        self.world_store.telemetry(
            "attention", "lease_size", len(items), scope=self.scope, timeline_id=timeline,
            dimensions={"redeliveries": redeliveries},
        )
        return {"attention_lease_id": lease_id, "through_cursor": through, "items": items}

    def runtime_state(self, *, current_world_revision: int | None = None,
                      current_world_epoch: str | None = None,
                      object_dependency_hashes: Mapping[str, str] | None = None,
                      current_turn: int | None = None) -> dict[str, Any]:
        """Return only active, bounded cognition that belongs in runtime context."""
        timeline = self.timeline_id
        if current_turn is not None:
            self.gc_watches(current_turn)
        with self.store.transaction() as connection:
            operations = connection.execute(
                "SELECT operation_id,operation_kind,objective,referenced_world_objects_json,"
                "linked_plan_id,linked_goal_id,last_renewed_turn,source_world_revision,status,"
                "foreground,compact_outcome,specialist_result_receipts_json,"
                "source_dependency_hash,source_world_epoch FROM cognitive_operations WHERE match_id=? "
                "AND agent_id=? AND perspective_id=? AND timeline_id=? "
                "AND status IN ('active','stale') ORDER BY foreground DESC,updated_unix DESC LIMIT 8",
                self._key(timeline),
            ).fetchall()
            watch_count = int(connection.execute(
                "SELECT COUNT(*) AS count FROM world_watches WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? AND status='active'",
                self._key(timeline),
            ).fetchone()["count"])
        values = []
        for row in operations:
            item = dict(row)
            item["referenced_world_objects"] = json.loads(
                item.pop("referenced_world_objects_json"),
            )
            item["specialist_result_receipts"] = json.loads(
                item.pop("specialist_result_receipts_json") or "[]",
            )[-8:]
            current_dependency = content_hash({
                ref: (object_dependency_hashes or {}).get(ref)
                for ref in item["referenced_world_objects"]
            })
            missing_dependency = object_dependency_hashes is not None and any(
                ref not in object_dependency_hashes
                for ref in item["referenced_world_objects"]
            )
            if current_world_epoch is not None and item.pop("source_world_epoch") != current_world_epoch \
                    or missing_dependency:
                item["status"] = "invalid"
                with self.store.transaction() as connection:
                    connection.execute(
                        "UPDATE cognitive_operations SET status='invalid',foreground=0,updated_unix=? "
                        "WHERE operation_id=?", (time.time(), item["operation_id"]),
                    )
                # Invalid operations are collected immediately. They describe
                # a different loaded world and must never survive into the
                # provider-facing runtime merely as an annotated stale row.
                continue
            elif item["status"] == "active" and object_dependency_hashes is not None \
                    and current_dependency != item.pop("source_dependency_hash"):
                item["status"] = "stale"
                with self.store.transaction() as connection:
                    connection.execute(
                        "UPDATE cognitive_operations SET status='stale',updated_unix=? "
                        "WHERE operation_id=?", (time.time(), item["operation_id"]),
                    )
            else:
                item.pop("source_dependency_hash", None)
            item["foreground"] = bool(item["foreground"])
            values.append(item)
        return {"operations": values, "active_watch_count": watch_count}

    def pending_summary(self) -> dict[str, Any]:
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT attention_kind,critical,COUNT(*) AS count,MAX(priority) AS priority "
                "FROM attention_items WHERE match_id=? AND agent_id=? AND perspective_id=? "
                "AND timeline_id=? AND status='queued' GROUP BY attention_kind,critical",
                self._key(self.timeline_id),
            ).fetchall()
        groups = [dict(row) for row in rows]
        return {"count": sum(int(row["count"]) for row in groups), "groups": groups,
                "has_chat": any(row["attention_kind"] == "chat" for row in groups),
                "has_critical": any(bool(row["critical"]) for row in groups)}

    def _key(self, timeline: str) -> tuple[str, str, str, str]:
        return self.scope.match_id, self.scope.agent_id, self.scope.perspective_id, timeline

    def _redelivery_count(self, lease_id: str, attention_id: str) -> int:
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT redelivery_count FROM attention_lease_items "
                "WHERE attention_lease_id=? AND attention_id=?", (lease_id, attention_id),
            ).fetchone()
        return int(row["redelivery_count"]) if row else 0

    def placed(self, lease_id: str) -> None:
        self._transition_lease(lease_id, "leased", "placed", "placed_unix")

    def restrict_for_placement(self, lease_id: str,
                               visible_attention_ids: Iterable[str]) -> dict[str, Any]:
        """Detach anything omitted by provider-context budgeting before placement.

        Leasing is deliberately generous so critical/high-priority events can be
        considered together.  Runtime serialization is the authority on what
        the provider actually receives.  Omitted rows retain their identity and
        return to the queue; the lease cursor is reduced to the visible set.
        """
        visible = tuple(dict.fromkeys(str(value) for value in visible_attention_ids))
        with self.store.transaction() as connection:
            lease = connection.execute(
                "SELECT status FROM attention_leases WHERE attention_lease_id=? "
                "AND match_id=? AND agent_id=? AND perspective_id=? AND timeline_id=?",
                (lease_id, *self._key(self.timeline_id)),
            ).fetchone()
            if not lease or lease["status"] not in {"leased", "placed", "responded"}:
                raise AttentionError("invalid_attention_lease_transition")
            rows = connection.execute(
                "SELECT i.attention_id,i.attention_sequence FROM attention_items i JOIN "
                "attention_lease_items li ON li.attention_id=i.attention_id "
                "WHERE li.attention_lease_id=? ORDER BY i.attention_sequence", (lease_id,),
            ).fetchall()
            leased_ids = {str(row["attention_id"]) for row in rows}
            if not set(visible).issubset(leased_ids):
                raise AttentionError("attention_placement_scope_mismatch")
            omitted = sorted(leased_ids - set(visible))
            if omitted:
                placeholders = ",".join("?" for _ in omitted)
                connection.execute(
                    f"UPDATE attention_items SET status='queued' WHERE attention_id IN ({placeholders}) "
                    "AND status='leased'", tuple(omitted),
                )
                connection.execute(
                    f"DELETE FROM attention_lease_items WHERE attention_lease_id=? "
                    f"AND attention_id IN ({placeholders})", (lease_id, *omitted),
                )
            through = max((int(row["attention_sequence"]) for row in rows
                           if str(row["attention_id"]) in set(visible)), default=0)
            connection.execute(
                "UPDATE attention_leases SET through_cursor=? WHERE attention_lease_id=?",
                (through, lease_id),
            )
        return {"attention_lease_id": lease_id, "through_cursor": through,
                "visible_ids": list(visible), "requeued_ids": omitted}

    def responded(self, lease_id: str) -> None:
        self._transition_lease(lease_id, "placed", "responded", "responded_unix")
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE attention_items SET status='responded' WHERE attention_id IN "
                "(SELECT attention_id FROM attention_lease_items WHERE attention_lease_id=?)",
                (lease_id,),
            )

    def _transition_lease(self, lease_id: str, expected: str, status: str, timestamp: str) -> None:
        with self.store.transaction() as connection:
            changed = connection.execute(
                f"UPDATE attention_leases SET status=?,{timestamp}=? WHERE attention_lease_id=? "
                "AND match_id=? AND agent_id=? AND perspective_id=? AND timeline_id=? "
                "AND status=?", (status, time.time(), lease_id,
                                  *self._key(self.timeline_id), expected),
            ).rowcount
            if changed != 1:
                raise AttentionError("invalid_attention_lease_transition")

    def acknowledge(self, lease_id: str, *, through_cursor: int,
                    acknowledged_ids: Iterable[str] = ()) -> dict[str, Any]:
        ids = set(str(value) for value in acknowledged_ids)
        now = time.time()
        with self.store.transaction() as connection:
            lease = connection.execute(
                "SELECT * FROM attention_leases WHERE attention_lease_id=? AND match_id=? "
                "AND agent_id=? AND perspective_id=? AND timeline_id=?",
                (lease_id, *self._key(self.timeline_id)),
            ).fetchone()
            if not lease or lease["status"] not in {"responded", "acknowledged"}:
                raise AttentionError("attention_not_cognitively_responded")
            rows = connection.execute(
                "SELECT i.attention_id,i.attention_sequence,i.observation_cursor," \
                "i.attention_kind,i.payload_json,i.captured_unix FROM attention_items i JOIN "
                "attention_lease_items li ON li.attention_id=i.attention_id "
                "WHERE li.attention_lease_id=? ORDER BY i.attention_sequence", (lease_id,),
            ).fetchall()
            eligible = [str(row["attention_id"]) for row in rows
                        if int(row["attention_sequence"]) <= int(through_cursor)
                        or str(row["attention_id"]) in ids]
            if eligible:
                placeholders = ",".join("?" for _ in eligible)
                connection.execute(
                    f"UPDATE attention_items SET status='acknowledged',acknowledged_unix=? "
                    f"WHERE attention_id IN ({placeholders})", (now, *eligible),
                )
            remainder = [str(row["attention_id"]) for row in rows
                         if str(row["attention_id"]) not in eligible]
            if remainder:
                placeholders = ",".join("?" for _ in remainder)
                connection.execute(
                    f"UPDATE attention_items SET status='queued' WHERE attention_id IN ({placeholders}) "
                    "AND status IN ('leased','responded')", tuple(remainder),
                )
            connection.execute(
                "UPDATE attention_leases SET status='acknowledged',acknowledged_unix=? "
                "WHERE attention_lease_id=?", (now, lease_id),
            )
            head = connection.execute(
                "SELECT acknowledged_cursor FROM attention_heads WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=?", self._key(str(lease["timeline_id"])),
            ).fetchone()
            contiguous = int(head["acknowledged_cursor"]) if head else 0
            while True:
                following = connection.execute(
                    "SELECT status FROM attention_items WHERE match_id=? AND agent_id=? "
                    "AND perspective_id=? AND timeline_id=? AND attention_sequence=?",
                    (*self._key(str(lease["timeline_id"])), contiguous + 1),
                ).fetchone()
                if not following or following["status"] not in {"acknowledged", "superseded"}:
                    break
                contiguous += 1
            connection.execute(
                "UPDATE attention_heads SET acknowledged_cursor=?,updated_unix=? WHERE " \
                "match_id=? AND agent_id=? AND perspective_id=? AND timeline_id=?",
                (contiguous, now, *self._key(str(lease["timeline_id"]))),
            )
        self.journal.append(self.scope, "attention.acknowledged", {
            "attention_lease_id": lease_id, "through_cursor": int(through_cursor),
            "acknowledged_ids": eligible,
        })
        chat_uids = []
        for row in rows:
            if str(row["attention_id"]) not in eligible or row["attention_kind"] != "chat":
                continue
            value = json.loads(row["payload_json"])
            message = value.get("message") if isinstance(value, Mapping) else None
            if isinstance(message, Mapping) and message.get("message_uid"):
                chat_uids.append(str(message["message_uid"]))
        if chat_uids:
            self.journal.append(self.scope, "chat.acknowledged", {
                "message_uids": sorted(set(chat_uids)),
                "attention_lease_id": lease_id,
            })
        acknowledged_rows = [row for row in rows if str(row["attention_id"]) in eligible]
        lag = max((now - float(row["captured_unix"]) for row in acknowledged_rows), default=0.0)
        self.world_store.telemetry(
            "attention", "acknowledgement_lag_seconds", lag, scope=self.scope,
            timeline_id=str(lease["timeline_id"]),
            dimensions={"count": len(eligible), "attention_cursor": contiguous},
        )
        return {"ok": True, "attention_lease_id": lease_id,
                "acknowledged_ids": eligible, "through_cursor": int(through_cursor),
                "attention_cursor": contiguous}

    def abandon(self, lease_id: str) -> None:
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT 1 FROM attention_leases WHERE attention_lease_id=? AND match_id=? "
                "AND agent_id=? AND perspective_id=? AND timeline_id=?",
                (lease_id, *self._key(self.timeline_id)),
            ).fetchone()
            if not row:
                raise AttentionError("unknown_attention_lease")
            self._abandon_locked(connection, lease_id)

    @staticmethod
    def _abandon_locked(connection, lease_id: str) -> None:  # noqa: ANN001
        connection.execute(
            "UPDATE attention_items SET status='queued' WHERE attention_id IN "
            "(SELECT attention_id FROM attention_lease_items WHERE attention_lease_id=?) "
            "AND status IN ('leased','responded')", (lease_id,),
        )
        connection.execute(
            "UPDATE attention_leases SET status='abandoned' WHERE attention_lease_id=? "
            "AND status IN ('leased','placed','responded')", (lease_id,),
        )

    def create_watch(
        self, watch_kind: str, subject_refs: Iterable[str], predicate: Mapping[str, Any],
        *, priority: int = 50, current_turn: int | None = None,
        expires_turn: int | None = None, linked_goal_id: str | None = None,
        linked_plan_id: str | None = None,
    ) -> dict[str, Any]:
        if watch_kind not in WATCH_KINDS:
            raise AttentionError("invalid_watch_kind")
        subjects = tuple(sorted(set(str(item) for item in subject_refs)))
        if not subjects or len(subjects) > 16:
            raise AttentionError("invalid_watch_subjects")
        timeline = self.timeline_id
        projection = self.world_store.load(self.scope, timeline)
        if not projection:
            raise AttentionError("world_projection_unavailable")
        world_epoch = str(projection["identity"]["world_epoch"])
        objects = {str(item.get("object_ref")) for item in projection.get("objects", ())
                   if isinstance(item, Mapping) and item.get("object_ref")}
        regions = [
            *self.world_store.load_regions(self.scope, timeline, "mobility-land-default"),
            *self.world_store.load_regions(self.scope, timeline, "mobility-sea-default"),
        ]
        region_refs = {item.region_ref for item in regions}
        region_aliases = {old: item.region_ref for item in regions for old in item.supersedes}
        normalized_subjects = tuple(region_aliases.get(item, item) for item in subjects)
        registry = self._semantic_registry(projection, regions)
        semantic_refs = set(objects) | region_refs | set(registry)
        if any(item not in semantic_refs for item in normalized_subjects):
            raise AttentionError("unknown_or_cross_perspective_subject_ref")
        subjects = tuple(sorted(set(normalized_subjects)))
        predicate = dict(predicate)
        if any(str(key).startswith("_") for key in predicate):
            raise AttentionError("private_watch_predicate_key")
        resolved_locations = sorted({
            location for subject in subjects
            for location in registry.get(subject, {}).get("location_refs", ())
            if registry.get(subject, {}).get("kind") != "region"
        })
        if resolved_locations:
            predicate["_subject_location_refs"] = resolved_locations
        with self.store._connect() as connection:
            if linked_plan_id and not connection.execute(
                "SELECT 1 FROM plans WHERE plan_id=? AND match_id=? AND agent_id=? "
                "AND perspective_id=? AND status='active'",
                (linked_plan_id, self.scope.match_id, self.scope.agent_id,
                 self.scope.perspective_id),
            ).fetchone():
                raise AttentionError("linked_plan_not_active")
            if linked_goal_id and not connection.execute(
                "SELECT 1 FROM goals WHERE goal_id=? AND match_id=? AND agent_id=? "
                "AND perspective_id=? AND status='active'",
                (linked_goal_id, self.scope.match_id, self.scope.agent_id,
                 self.scope.perspective_id),
            ).fetchone():
                raise AttentionError("linked_goal_not_active")
        # Watches are attention preferences, not permanent automation. Even a
        # caller-supplied far-future expiry must be renewed within ten turns.
        renewal_ceiling = current_turn + 10 if current_turn is not None else None
        expires = expires_turn if expires_turn is not None else renewal_ceiling
        if renewal_ceiling is not None:
            expires = min(int(expires), renewal_ceiling) if expires is not None \
                else renewal_ceiling
        normalized = content_hash({"kind": watch_kind, "subjects": subjects,
                                   "predicate": predicate, "goal": linked_goal_id,
                                   "plan": linked_plan_id})
        with self.store.transaction() as connection:
            count = int(connection.execute(
                "SELECT COUNT(*) AS count FROM world_watches WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? AND status='active'",
                self._key(timeline),
            ).fetchone()["count"])
            existing = connection.execute(
                "SELECT * FROM world_watches WHERE match_id=? AND agent_id=? AND perspective_id=? "
                "AND timeline_id=? AND normalized_hash=? AND status='active'",
                (*self._key(timeline), normalized),
            ).fetchone()
            if existing:
                connection.execute(
                    "UPDATE world_watches SET expires_turn=?,last_renewed_turn=?,updated_unix=? "
                    "WHERE watch_id=?", (expires, current_turn, time.time(), existing["watch_id"]),
                )
                return {"watch_id": existing["watch_id"], "merged": True}
            if count >= 32:
                raise AttentionError("watch_limit_reached")
            watch_id = "watch-" + uuid.uuid4().hex
            now = time.time()
            connection.execute(
                "INSERT INTO world_watches(watch_id,match_id,agent_id,perspective_id,timeline_id,world_epoch," \
                "watch_kind,subject_refs_json,typed_predicate_json,priority,created_turn,expires_turn," \
                "last_renewed_turn,linked_goal_id,linked_plan_id,status,normalized_hash,created_unix," \
                "updated_unix) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?,?,?)",
                (watch_id, *self._key(timeline), world_epoch, watch_kind, canonical_json(subjects),
                 canonical_json(predicate), min(max(priority, 0), 100), current_turn, expires,
                 current_turn, linked_goal_id, linked_plan_id, normalized, now, now),
            )
        self.journal.append(self.scope, "attention.watch_created", {
            "watch_id": watch_id, "watch_kind": watch_kind, "subject_refs": subjects,
            "typed_predicate": dict(predicate), "expires_turn": expires,
            "linked_goal_id": linked_goal_id, "linked_plan_id": linked_plan_id,
        }, turn=current_turn)
        return {"watch_id": watch_id, "merged": False, "expires_turn": expires}

    def _semantic_registry(self, projection: Mapping[str, Any],
                           regions: Iterable[Any]) -> dict[str, dict[str, Any]]:
        """Resolve provider-safe derived handles actually issued to this perspective."""
        registry: dict[str, dict[str, Any]] = {}
        from smacx_regions import PHYSICAL_LAND_PROFILE, PHYSICAL_OCEAN_PROFILE
        region_rows = [*regions,
            *self.world_store.load_regions(self.scope, self.timeline_id, PHYSICAL_LAND_PROFILE),
            *self.world_store.load_regions(self.scope, self.timeline_id, PHYSICAL_OCEAN_PROFILE)]
        for region in region_rows:
            registry[region.region_ref] = {
                "kind": "region", "location_refs": sorted(region.location_refs),
            }
        anchor = self.world_store.current_anchor(
            self.scope, self.timeline_id, "256k",
        ) or self.world_store.current_anchor(self.scope, self.timeline_id, "64k")
        payload = anchor.get("payload", {}) if anchor else {}
        if isinstance(payload, Mapping):
            for frontier in payload.get("frontiers", ()):
                if isinstance(frontier, Mapping) and frontier.get("frontier_ref"):
                    registry[str(frontier["frontier_ref"])] = {
                        "kind": "frontier",
                        "location_refs": list(frontier.get("boundary_refs") or ()),
                    }
            regions_by_ref = {item.region_ref: item for item in region_rows}
            for theater in payload.get("active_theaters", ()):
                if not isinstance(theater, Mapping) or not theater.get("theater_ref"):
                    continue
                locations = {
                    location
                    for ref in theater.get("region_refs", ())
                    for location in getattr(regions_by_ref.get(str(ref)), "location_refs", ())
                }
                registry[str(theater["theater_ref"])] = {
                    "kind": "theater", "location_refs": sorted(locations),
                    "subject_refs": list(theater.get("subject_refs") or ()),
                }
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT result_json FROM world_query_cache WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? AND world_epoch=? AND world_revision=?",
                (*self._key(self.timeline_id), str(projection["identity"]["world_epoch"]),
                 int(projection["world_revision"])),
            ).fetchall()
        for row in rows:
            result = json.loads(row["result_json"])
            route = result.get("route")
            if isinstance(route, Mapping) and route.get("route_ref"):
                registry[str(route["route_ref"])] = {
                    "kind": "route", "location_refs": list(route.get("path") or ()),
                }
            for item in result.get("items", ()) if isinstance(result.get("items"), list) else ():
                if isinstance(item, Mapping) and item.get("rendezvous_ref"):
                    registry[str(item["rendezvous_ref"])] = {
                        "kind": "rendezvous",
                        "location_refs": [str(item.get("candidate_ref"))]
                        if item.get("candidate_ref") else [],
                        "subject_refs": [str(value.get("participant_ref"))
                                         for value in item.get("arrivals", ())
                                         if isinstance(value, Mapping)],
                    }
        return registry

    def semantic_dependency_hashes(
        self, projection: Mapping[str, Any] | None = None,
    ) -> dict[str, str]:
        """Hash the current issued object/derived-ref registry materially.

        Operations and watches may refer to both durable world objects and
        ephemeral issued handles such as routes, rendezvous, frontiers,
        theaters, and versioned regions. Query-cache history is not authority:
        only handles reproducible at the active world revision are returned.
        """
        projection = projection or self.world_store.load(self.scope, self.timeline_id)
        if not projection:
            return {}
        regions = [
            *self.world_store.load_regions(self.scope, self.timeline_id,
                                           "mobility-land-default"),
            *self.world_store.load_regions(self.scope, self.timeline_id,
                                           "mobility-sea-default"),
        ]
        result = {
            str(item["object_ref"]): material_hash(item)
            for item in projection.get("objects", ())
            if isinstance(item, Mapping) and item.get("object_ref")
        }
        for ref, descriptor in self._semantic_registry(projection, regions).items():
            result[str(ref)] = content_hash(provider_safe(descriptor))
        return result

    def gc_watches(self, current_turn: int) -> int:
        projection = self.world_store.load(self.scope, self.timeline_id)
        current_epoch = str(projection["identity"]["world_epoch"]) if projection else ""
        # Region identities are versioned. Migrate an active watch through a
        # deterministic one-to-one supersession; ambiguous split/merge cases
        # remain on the old ref and are invalidated for sovereign review.
        regions = [
            *self.world_store.load_regions(self.scope, self.timeline_id, "mobility-land-default"),
            *self.world_store.load_regions(self.scope, self.timeline_id, "mobility-sea-default"),
        ]
        aliases: dict[str, list[str]] = {}
        for region in regions:
            for old in region.supersedes:
                aliases.setdefault(old, []).append(region.region_ref)
        registry = self._semantic_registry(projection, regions) if projection else {}
        object_refs = {str(item.get("object_ref")) for item in projection.get("objects", ())
                       if isinstance(item, Mapping)} if projection else set()
        valid_refs = object_refs | {item.region_ref for item in regions} | set(registry)
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT watch_id,subject_refs_json FROM world_watches WHERE match_id=? "
                "AND agent_id=? AND perspective_id=? AND timeline_id=? AND status='active'",
                self._key(self.timeline_id),
            ).fetchall()
            for row in rows:
                subjects = list(json.loads(row["subject_refs_json"]))
                migrated, ambiguous = [], False
                for subject in subjects:
                    targets = aliases.get(str(subject), [])
                    if len(targets) > 1:
                        ambiguous = True
                    migrated.append(targets[0] if len(targets) == 1 else subject)
                if ambiguous:
                    connection.execute(
                        "UPDATE world_watches SET status='invalid',updated_unix=? WHERE watch_id=?",
                        (time.time(), row["watch_id"]),
                    )
                elif migrated != subjects:
                    connection.execute(
                        "UPDATE world_watches SET subject_refs_json=?,updated_unix=? WHERE watch_id=?",
                        (canonical_json(sorted(set(migrated))), time.time(), row["watch_id"]),
                    )
                elif any(str(subject) not in valid_refs for subject in migrated):
                    connection.execute(
                        "UPDATE world_watches SET status='invalid',updated_unix=? WHERE watch_id=?",
                        (time.time(), row["watch_id"]),
                    )
        with self.store.transaction() as connection:
            expired = connection.execute(
                "UPDATE world_watches SET status='expired',updated_unix=? WHERE match_id=? "
                "AND agent_id=? AND perspective_id=? AND timeline_id=? AND status='active' "
                "AND (world_epoch<>? OR "
                "(expires_turn IS NOT NULL AND expires_turn<?) OR "
                "(last_renewed_turn IS NOT NULL AND last_renewed_turn+10<?) OR "
                "(linked_plan_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM plans p "
                " WHERE p.plan_id=world_watches.linked_plan_id AND p.status='active')) OR "
                "(linked_goal_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM goals g "
                " WHERE g.goal_id=world_watches.linked_goal_id AND g.status='active')))",
                (time.time(), *self._key(self.timeline_id), current_epoch,
                 int(current_turn), int(current_turn)),
            ).rowcount
        if expired:
            self.journal.append(self.scope, "attention.watches_expired", {
                "count": expired, "through_turn": int(current_turn),
            }, turn=current_turn)
        with self.store._connect() as connection:
            lifecycle = connection.execute(
                "SELECT watch_id,status,subject_refs_json FROM world_watches w WHERE match_id=? "
                "AND agent_id=? AND perspective_id=? AND timeline_id=? AND status IN ('expired','invalid') "
                "AND NOT EXISTS (SELECT 1 FROM attention_items a WHERE a.match_id=w.match_id "
                "AND a.agent_id=w.agent_id AND a.perspective_id=w.perspective_id AND a.timeline_id=w.timeline_id "
                "AND a.attention_kind='watch_lifecycle' AND json_extract(a.payload_json,'$.watch_id')=w.watch_id) LIMIT 32",
                self._key(self.timeline_id)).fetchall()
        for row in lifecycle:
            self.enqueue("watch_lifecycle", {"watch_id":row["watch_id"], "status":row["status"],
                "subject_refs":json.loads(row["subject_refs_json"]),
                "meaning":"This watch no longer provides vigilance; sovereign review is required."},
                observation_cursor=0, priority=60, turn=current_turn,
                dedupe_key="lifecycle:"+row["watch_id"])
        return expired

    def close_watch(self, watch_id: str, *, current_turn: int | None = None) -> None:
        with self.store.transaction() as connection:
            changed = connection.execute(
                "UPDATE world_watches SET status='closed',updated_unix=? WHERE watch_id=? "
                "AND match_id=? AND agent_id=? AND perspective_id=? AND timeline_id=? "
                "AND status='active'",
                (time.time(), watch_id, *self._key(self.timeline_id)),
            ).rowcount
        if changed != 1:
            raise AttentionError("unknown_watch")
        self.journal.append(self.scope, "attention.watch_closed", {
            "watch_id": watch_id,
        }, turn=current_turn)

    @staticmethod
    def _watch_predicate_matches(predicate: Mapping[str, Any], delta: Mapping[str, Any]) -> bool:
        change = str(delta.get("change") or "")
        requested_change = predicate.get("change")
        if requested_change is not None and str(requested_change) != change:
            return False
        field = str(predicate.get("field") or "")
        if not field:
            return True
        current = delta.get("current") if isinstance(delta.get("current"), Mapping) else {}
        fields = current.get("fields") if isinstance(current.get("fields"), Mapping) else {}
        envelope = fields.get(field) if isinstance(fields.get(field), Mapping) else None
        missing = object()
        actual = envelope.get("value", missing) if envelope is not None else current.get(field, missing)
        expected = predicate.get("value", predicate.get("equals"))
        operator = str(predicate.get("operator") or "eq")
        if operator == "changed":
            previous = delta.get("previous")
            if change != "changed" or not isinstance(previous, Mapping):
                return False
            previous_fields = previous.get("fields", {})
            prior = previous_fields.get(field) if isinstance(previous_fields, Mapping) else None
            prior_value = prior.get("value", missing) if isinstance(prior, Mapping) else previous.get(field, missing)
            return prior_value != actual
        if operator == "exists":
            return actual is not missing and actual is not None
        if actual is missing:
            return False
        try:
            import operator as operators
            compare = {"eq": operators.eq, "ne": operators.ne,
                       "gt": operators.gt, "gte": operators.ge,
                       "lt": operators.lt, "lte": operators.le}[operator]
            return compare(actual, expected)
        except (KeyError, TypeError):
            return False

    def evaluate_watches(self, deltas: Iterable[Mapping[str, Any]], *,
                         temporal_events: Iterable[Mapping[str, Any]] = (),
                         observation_cursor: int, turn: int | None,
                         session_id: str | None = None) -> list[dict[str, Any]]:
        """Elevate matching perspective-safe changes; never performs automation."""
        timeline = self.timeline_id
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM world_watches WHERE match_id=? AND agent_id=? AND "
                "perspective_id=? AND timeline_id=? AND status='active' ORDER BY priority DESC",
                self._key(timeline),
            ).fetchall()
        changes = [dict(item) for item in deltas if isinstance(item, Mapping)]
        temporal = [dict(item) for item in temporal_events if isinstance(item, Mapping)]
        regions = {
            item.region_ref: item for item in (
                *self.world_store.load_regions(self.scope, timeline, "mobility-land-default"),
                *self.world_store.load_regions(self.scope, timeline, "mobility-sea-default"),
            )
        }
        projection = self.world_store.load(self.scope, timeline) or {}
        current_objects = {str(item["object_ref"]): item for item in projection.get("objects", ())}
        triggered: list[dict[str, Any]] = []
        for row in rows:
            subjects = set(json.loads(row["subject_refs_json"]))
            predicate = json.loads(row["typed_predicate_json"])
            matches = []
            watch_kind = str(row["watch_kind"])
            if watch_kind in {"region_entry", "region_exit", "frontier_contact",
                              "route_disruption", "rendezvous_progress"}:
                watched_locations = set(map(str, predicate.get("_subject_location_refs") or ()))
                for subject in subjects:
                    region = regions.get(subject)
                    if region:
                        watched_locations.update(region.location_refs)
                    elif subject.startswith("location-"):
                        watched_locations.add(subject)
                for event in temporal:
                    path = event.get("path") if isinstance(event.get("path"), list) else []
                    segments = [(str(step.get("from_location_ref") or ""),
                                 str(step.get("to_location_ref") or ""))
                                for step in path if isinstance(step, Mapping)]
                    if not segments:
                        segments = [(str(event.get("from_location_ref") or ""),
                                     str(event.get("to_location_ref")
                                         or event.get("location_ref") or ""))]
                    entered = any(before not in watched_locations and after in watched_locations
                                  for before, after in segments)
                    exited = any(before in watched_locations and after not in watched_locations
                                 for before, after in segments)
                    direct_refs = {str(event.get(key) or "") for key in
                                   ("contact_ref", "base_ref", "location_ref", "frontier_ref",
                                    "theater_ref", "route_ref", "rendezvous_ref")}
                    event_locations = {
                        str(event.get("from_location_ref") or ""),
                        str(event.get("to_location_ref") or ""),
                        str(event.get("location_ref") or ""),
                        *(str(item) for item in event.get("affected_location_refs", ())
                          if item),
                    }
                    matched = (
                        watch_kind == "region_entry" and entered
                        or watch_kind == "region_exit" and exited
                        or watch_kind == "frontier_contact" and (entered or bool(subjects & direct_refs))
                        or watch_kind == "route_disruption" and event.get("event_kind")
                           == "terrain_or_improvement_changed"
                           and (bool(subjects & direct_refs)
                                or bool(watched_locations & event_locations))
                        or watch_kind == "rendezvous_progress"
                           and (bool(subjects & direct_refs)
                                or bool(watched_locations & event_locations))
                    )
                    if predicate.get("relationship") == "hostile":
                        from smacx_mechanics import relationship_class, field_is_current
                        contact = current_objects.get(str(event.get("contact_ref") or event.get("unit_ref") or ""), {})
                        matched = matched and contact.get("kind") == "foreign_contact" and contact.get("status") == "active" and field_is_current(contact, "relationship") and relationship_class(contact) == "hostile"
                    if matched and self._watch_predicate_matches(predicate, {
                            "change": str(event.get("event_kind") or "changed"),
                            "current": event}):
                        matches.append({"temporal_event": event})
            for delta in changes if watch_kind not in {"region_entry", "region_exit"} else ():
                current = delta.get("current") if isinstance(delta.get("current"), Mapping) else {}
                candidate_refs = {
                    str(delta.get("object_ref") or ""),
                    str(current.get("location_ref") or ""),
                    str(current.get("parent_ref") or ""),
                }
                if subjects.isdisjoint(candidate_refs):
                    continue
                if self._watch_predicate_matches(predicate, delta):
                    matches.append(delta)
            if not matches:
                continue
            prior_cursor = row["last_triggered_cursor"]
            if prior_cursor is not None and int(prior_cursor) >= int(observation_cursor):
                continue
            payload = {
                "watch_id": str(row["watch_id"]), "watch_kind": str(row["watch_kind"]),
                "subject_refs": sorted(subjects), "matches": matches[:8],
            }
            queued = self.enqueue(
                "watch_trigger", payload, observation_cursor=observation_cursor,
                priority=int(row["priority"]), critical=False, turn=turn,
                session_id=session_id,
                dedupe_key=f"{row['watch_id']}:{observation_cursor}",
            )
            with self.store.transaction() as connection:
                connection.execute(
                    "UPDATE world_watches SET last_triggered_cursor=?,updated_unix=? "
                    "WHERE watch_id=? AND status='active'",
                    (int(observation_cursor), time.time(), row["watch_id"]),
                )
            triggered.append({**payload, "attention_id": queued["attention_id"]})
        return triggered

    def upsert_operation(
        self, *, operation_id: str | None, kind: str, objective: str,
        referenced_world_objects: Iterable[str], source_world_revision: int,
        source_world_epoch: str,
        source_dependency_hash: str,
        current_turn: int | None, linked_plan_id: str | None = None,
        linked_goal_id: str | None = None, foreground: bool = True,
    ) -> dict[str, Any]:
        refs = tuple(dict.fromkeys(str(item) for item in referenced_world_objects))[:64]
        timeline = self.timeline_id
        now = time.time()
        projection = self.world_store.load(self.scope, timeline)
        if not projection:
            raise AttentionError("world_projection_unavailable")
        if str(projection["identity"]["world_epoch"]) != str(source_world_epoch) \
                or int(projection["world_revision"]) != int(source_world_revision):
            raise AttentionError("stale_operation_world_revision")
        dependencies = self.semantic_dependency_hashes(projection)
        if any(ref not in dependencies for ref in refs):
            raise AttentionError("unknown_or_superseded_operation_ref")
        expected_dependency_hash = content_hash({ref: dependencies[ref] for ref in refs})
        if str(source_dependency_hash) != expected_dependency_hash:
            raise AttentionError("stale_operation_dependency_hash")
        with self.store.transaction() as connection:
            if linked_plan_id and not connection.execute(
                "SELECT 1 FROM plans WHERE plan_id=? AND match_id=? AND agent_id=? "
                "AND perspective_id=? AND status='active'",
                (linked_plan_id, self.scope.match_id, self.scope.agent_id,
                 self.scope.perspective_id),
            ).fetchone():
                raise AttentionError("linked_plan_not_active")
            if linked_goal_id and not connection.execute(
                "SELECT 1 FROM goals WHERE goal_id=? AND match_id=? AND agent_id=? "
                "AND perspective_id=? AND status='active'",
                (linked_goal_id, self.scope.match_id, self.scope.agent_id,
                 self.scope.perspective_id),
            ).fetchone():
                raise AttentionError("linked_goal_not_active")
            if operation_id is None:
                count = int(connection.execute(
                    "SELECT COUNT(*) AS count FROM cognitive_operations WHERE match_id=? "
                    "AND agent_id=? AND perspective_id=? AND timeline_id=? "
                    "AND status IN ('active','stale')", self._key(timeline),
                ).fetchone()["count"])
                if count >= 8:
                    raise AttentionError("operation_limit_reached")
                operation_id = "operation-" + uuid.uuid4().hex
            else:
                existing = connection.execute(
                    "SELECT match_id,agent_id,perspective_id,timeline_id FROM "
                    "cognitive_operations WHERE operation_id=?", (operation_id,),
                ).fetchone()
                if not existing or tuple(existing) != self._key(timeline):
                    raise AttentionError("unknown_operation")
            if foreground:
                connection.execute(
                    "UPDATE cognitive_operations SET foreground=0 WHERE match_id=? AND agent_id=? "
                    "AND perspective_id=? AND timeline_id=?", self._key(timeline),
                )
            connection.execute(
                "INSERT INTO cognitive_operations(operation_id,match_id,agent_id,perspective_id," \
                "timeline_id,operation_kind,objective,referenced_world_objects_json,linked_plan_id," \
                "linked_goal_id,created_turn,last_renewed_turn,source_world_revision," \
                "source_world_epoch,source_dependency_hash,status,foreground,created_unix,updated_unix) " \
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?,?,?) " \
                "ON CONFLICT(operation_id) DO UPDATE SET objective=excluded.objective," \
                "referenced_world_objects_json=excluded.referenced_world_objects_json," \
                "linked_plan_id=excluded.linked_plan_id,linked_goal_id=excluded.linked_goal_id," \
                "last_renewed_turn=excluded.last_renewed_turn," \
                "source_world_revision=excluded.source_world_revision," \
                "source_world_epoch=excluded.source_world_epoch," \
                "source_dependency_hash=excluded.source_dependency_hash,status='active'," \
                "foreground=excluded.foreground,updated_unix=excluded.updated_unix",
                (operation_id, *self._key(timeline), kind, objective[:2000], canonical_json(refs),
                 linked_plan_id, linked_goal_id, current_turn, current_turn,
                 int(source_world_revision), source_world_epoch, source_dependency_hash,
                 int(foreground), now, now),
            )
        self.journal.append(self.scope, "cognition.operation_upserted", {
            "operation_id": operation_id, "kind": kind, "objective": objective[:2000],
            "referenced_world_objects": list(refs), "linked_plan_id": linked_plan_id,
            "linked_goal_id": linked_goal_id, "source_world_revision": source_world_revision,
            "source_world_epoch": source_world_epoch,
            "source_dependency_hash": source_dependency_hash, "foreground": bool(foreground),
        }, turn=current_turn)
        return {"operation_id": operation_id, "status": "active", "foreground": foreground}

    def complete_operation(self, operation_id: str, outcome: str = "") -> None:
        with self.store.transaction() as connection:
            changed = connection.execute(
                "UPDATE cognitive_operations SET status='completed',foreground=0,"
                "compact_outcome=?,updated_unix=? WHERE operation_id=? AND match_id=? "
                "AND agent_id=? AND perspective_id=? AND timeline_id=?",
                (outcome[:2000], time.time(), operation_id, *self._key(self.timeline_id)),
            ).rowcount
        if changed != 1:
            raise AttentionError("unknown_operation")
        self.journal.append(self.scope, "cognition.operation_completed", {
            "operation_id": operation_id, "compact_outcome": outcome[:2000],
        })

    def turn_handoff(self, current_turn: int) -> dict[str, int]:
        """Expire disposable work; retain only renewed plan-linked summaries."""
        now = time.time()
        with self.store.transaction() as connection:
            expired = connection.execute(
                "UPDATE cognitive_operations SET status='expired',foreground=0,updated_unix=? "
                "WHERE match_id=? AND agent_id=? AND perspective_id=? AND timeline_id=? "
                "AND status IN ('active','stale') AND linked_plan_id IS NULL",
                (now, *self._key(self.timeline_id)),
            ).rowcount
            demoted = connection.execute(
                "UPDATE cognitive_operations SET foreground=0,updated_unix=? WHERE match_id=? "
                "AND agent_id=? AND perspective_id=? AND timeline_id=? AND status IN "
                "('active','stale') AND linked_plan_id IS NOT NULL AND last_renewed_turn<?",
                (now, *self._key(self.timeline_id), int(current_turn)),
            ).rowcount
            linked_expired = connection.execute(
                "UPDATE cognitive_operations SET status='expired',foreground=0,updated_unix=? "
                "WHERE match_id=? AND agent_id=? AND perspective_id=? AND timeline_id=? "
                "AND status IN ('active','stale') AND ((linked_plan_id IS NOT NULL AND "
                "NOT EXISTS (SELECT 1 FROM plans p WHERE p.plan_id=cognitive_operations.linked_plan_id "
                "AND p.status='active')) OR (linked_goal_id IS NOT NULL AND NOT EXISTS "
                "(SELECT 1 FROM goals g WHERE g.goal_id=cognitive_operations.linked_goal_id "
                "AND g.status='active')) OR (last_renewed_turn IS NOT NULL AND "
                "last_renewed_turn+10<?))",
                (now, *self._key(self.timeline_id), int(current_turn)),
            ).rowcount
        watches = self.gc_watches(current_turn)
        if expired or demoted or linked_expired:
            self.journal.append(self.scope, "cognition.turn_scope_collected", {
                "expired_operations": expired + linked_expired,
                "demoted_operations": demoted,
            }, turn=current_turn)
        return {"expired_operations": expired + linked_expired,
                "demoted_operations": demoted,
                "expired_watches": watches}

    def acquire_sovereign(self, episode_id: str, episode_mode: str,
                          *, ttl_seconds: int = 900) -> str:
        if episode_mode not in {"gameplay", "communication", "recovery"}:
            raise AttentionError("invalid_episode_mode")
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        now = time.time()
        key = self._key(self.timeline_id)
        with self.store.transaction() as connection:
            current = connection.execute(
                "SELECT episode_id,episode_mode,status,expires_unix FROM sovereign_leases WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=?", key,
            ).fetchone()
            if current and current["status"] == "active" and float(current["expires_unix"]) > now:
                if current["episode_id"] != episode_id or current["episode_mode"] != episode_mode:
                    raise AttentionError("sovereign_invocation_already_active")
            if current and current["status"] == "active" \
                    and float(current["expires_unix"]) <= now:
                expired_leases = connection.execute(
                    "SELECT attention_lease_id FROM attention_leases WHERE match_id=? "
                    "AND agent_id=? AND perspective_id=? AND timeline_id=? AND episode_id=? "
                    "AND status IN ('leased','placed','responded')",
                    (*key, current["episode_id"]),
                ).fetchall()
                for lease in expired_leases:
                    self._abandon_locked(connection, str(lease["attention_lease_id"]))
            connection.execute(
                "INSERT OR REPLACE INTO sovereign_leases(match_id,agent_id,perspective_id," \
                "timeline_id,episode_id,episode_mode,lease_token_hash,status,acquired_unix," \
                "expires_unix) VALUES(?,?,?,?,?,?,?,'active',?,?)",
                (*key, episode_id, episode_mode, digest, now,
                 now + min(max(ttl_seconds, 30), 3600)),
            )
        return token

    def sovereign_state(self) -> dict[str, Any] | None:
        """Return the active writer lease without exposing its capability token."""
        now = time.time()
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE sovereign_leases SET status='expired' WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? AND status='active' AND expires_unix<=?",
                (*self._key(self.timeline_id), now),
            )
            row = connection.execute(
                "SELECT episode_id,episode_mode,status,acquired_unix,expires_unix FROM "
                "sovereign_leases WHERE match_id=? AND agent_id=? AND perspective_id=? "
                "AND timeline_id=? AND status='active'",
                self._key(self.timeline_id),
            ).fetchone()
        return dict(row) if row else None

    def release_sovereign(self, token: str, *, committed: bool) -> None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.store.transaction() as connection:
            current = connection.execute(
                "SELECT episode_id FROM sovereign_leases WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? AND lease_token_hash=? AND status='active'",
                (*self._key(self.timeline_id), digest),
            ).fetchone()
            changed = connection.execute(
                "UPDATE sovereign_leases SET status=? WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? AND lease_token_hash=? AND status='active'",
                ("committed" if committed else "cancelled", *self._key(self.timeline_id), digest),
            ).rowcount
            if current:
                leases = connection.execute(
                    "SELECT attention_lease_id FROM attention_leases WHERE match_id=? AND agent_id=? "
                    "AND perspective_id=? AND timeline_id=? AND episode_id=? "
                    "AND status IN ('leased','placed','responded')",
                    (*self._key(self.timeline_id), current["episode_id"]),
                ).fetchall()
                for lease in leases:
                    self._abandon_locked(connection, str(lease["attention_lease_id"]))
        if changed != 1:
            raise AttentionError("invalid_sovereign_lease")

    def cancel_active_sovereign(self, reason: str) -> bool:
        """Operator recovery hook, called only after the provider process is stopped."""
        with self.store.transaction() as connection:
            active = connection.execute(
                "SELECT episode_id FROM sovereign_leases WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? AND status='active'",
                self._key(self.timeline_id),
            ).fetchone()
            changed = connection.execute(
                "UPDATE sovereign_leases SET status='cancelled' WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? AND status='active'",
                self._key(self.timeline_id),
            ).rowcount
            if active:
                leases = connection.execute(
                    "SELECT attention_lease_id FROM attention_leases WHERE match_id=? AND agent_id=? "
                    "AND perspective_id=? AND timeline_id=? AND episode_id=? "
                    "AND status IN ('leased','placed','responded')",
                    (*self._key(self.timeline_id), active["episode_id"]),
                ).fetchall()
                for lease in leases:
                    self._abandon_locked(connection, str(lease["attention_lease_id"]))
        if changed:
            self.journal.append(self.scope, "agent.sovereign_lease_cancelled", {
                "reason": str(reason)[:500], "timeline_id": self.timeline_id,
            })
        return bool(changed)
