"""Rebuildable SQLite and content-addressed snapshot storage for world projections."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Iterable, Mapping
import uuid

from smacx_store import MemoryScope, SmacxStore
from smacx_world_types import (
    WorldIdentity, WorldObject, canonical_json, content_hash, material_hash,
)
from smacx_regions import Region


class WorldStoreError(RuntimeError):
    pass


class WorldStore:
    """Projection storage. The campaign journal remains the temporal authority."""

    def __init__(self, store: SmacxStore, root: Path | None = None) -> None:
        self.store = store
        configured = os.environ.get("SMACX_WORLD_SNAPSHOT_ROOT", "")
        self.root = (root or (Path(configured) if configured else store.path.parent / "world-snapshots")) \
            .expanduser().resolve()

    @staticmethod
    def _scope_tuple(scope: MemoryScope, timeline_id: str) -> tuple[str, str, str, str]:
        return scope.match_id, scope.agent_id, scope.perspective_id, timeline_id

    def load(self, scope: MemoryScope, timeline_id: str) -> dict[str, Any] | None:
        self.store.require_scope(scope)
        with self.store._connect() as connection:  # projection-private API
            head = connection.execute(
                "SELECT * FROM world_heads WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=?",
                self._scope_tuple(scope, timeline_id),
            ).fetchone()
            if not head:
                return None
            rows = connection.execute(
                "SELECT payload_json FROM world_objects WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? ORDER BY object_ref",
                self._scope_tuple(scope, timeline_id),
            ).fetchall()
        return {
            "identity": {
                "match_id": scope.match_id, "perspective_id": scope.perspective_id,
                "timeline_id": timeline_id, "world_epoch": head["world_epoch"],
            },
            "world_revision": int(head["world_revision"]),
            "action_revision": head["action_revision"],
            "observation_cursor": int(head["observation_cursor"]),
            "continuity": head["continuity"],
            "journal_head_hash": head["journal_head_hash"],
            "projection_checksum": head["projection_checksum"],
            "material_checksum": head["material_checksum"],
            "objects": [json.loads(row["payload_json"]) for row in rows],
        }

    def replace_projection(
        self,
        scope: MemoryScope,
        identity: WorldIdentity,
        objects: Iterable[WorldObject],
        *,
        observation_cursor: int,
        action_revision: str | None,
        continuity: str,
        journal_head_hash: str | None,
    ) -> dict[str, Any]:
        if identity.match_id != scope.match_id or identity.perspective_id != scope.perspective_id:
            raise WorldStoreError("world_scope_mismatch")
        if continuity not in {"complete", "incomplete"} or observation_cursor < 0:
            raise WorldStoreError("invalid_world_head")
        rows = [item.as_dict(provider_safe=False) for item in objects]
        rows.sort(key=lambda item: item["object_ref"])
        checksum = content_hash(rows)
        material_checksum = content_hash({
            str(item["object_ref"]): material_hash(item) for item in rows
        })
        now = time.time()
        key = self._scope_tuple(scope, identity.timeline_id)
        with self.store.transaction() as connection:
            self.store.require_scope(scope, connection=connection)
            previous = connection.execute(
                "SELECT world_epoch,world_revision,projection_checksum,material_checksum FROM world_heads "
                "WHERE match_id=? AND agent_id=? AND perspective_id=? AND timeline_id=?", key,
            ).fetchone()
            revision = 1
            if previous:
                revision = int(previous["world_revision"])
                if previous["world_epoch"] != identity.world_epoch \
                        or previous["material_checksum"] != material_checksum:
                    revision += 1
            connection.execute(
                "INSERT INTO world_heads(match_id,agent_id,perspective_id,timeline_id,world_epoch," \
                "world_revision,action_revision,observation_cursor,continuity,journal_head_hash," \
                "projection_checksum,material_checksum,updated_unix) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) " \
                "ON CONFLICT(match_id,agent_id,perspective_id,timeline_id) DO UPDATE SET " \
                "world_epoch=excluded.world_epoch,world_revision=excluded.world_revision," \
                "action_revision=excluded.action_revision,observation_cursor=excluded.observation_cursor," \
                "continuity=excluded.continuity,journal_head_hash=excluded.journal_head_hash," \
                "projection_checksum=excluded.projection_checksum," \
                "material_checksum=excluded.material_checksum,updated_unix=excluded.updated_unix",
                (*key, identity.world_epoch, revision, action_revision, observation_cursor,
                 continuity, journal_head_hash, checksum, material_checksum, now),
            )
            connection.execute(
                "DELETE FROM world_objects WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=?", key,
            )
            connection.executemany(
                "INSERT INTO world_objects(match_id,agent_id,perspective_id,timeline_id,world_epoch," \
                "object_ref,object_kind,location_ref,parent_ref,status,payload_json,dependency_hash," \
                "updated_revision,updated_unix) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(*key, identity.world_epoch, item["object_ref"], item["kind"],
                  item.get("location_ref"), item.get("parent_ref"), item.get("status", "active"),
                  canonical_json(item), content_hash(item), revision, now) for item in rows],
            )
            # Query rows carry their own dependency hash. Unrelated world
            # changes must not defeat reusable evidence; request-time lookup
            # rejects stale dependencies and lazily replaces that fingerprint.
        return {
            "world_revision": revision, "observation_cursor": observation_cursor,
            "projection_checksum": checksum, "material_checksum": material_checksum,
            "changed": not previous
            or previous["world_epoch"] != identity.world_epoch
            or previous["material_checksum"] != material_checksum,
        }

    def record_observation_projection(
        self, scope: MemoryScope, timeline_id: str, observation: Mapping[str, Any],
        journal_event_id: str,
    ) -> None:
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO world_observation_projection(" \
                "match_id,agent_id,perspective_id,timeline_id,observation_sequence," \
                "journal_event_id,observation_kind,turn,payload_hash,payload_json,continuity) " \
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (*self._scope_tuple(scope, timeline_id), int(observation["sequence"]),
                 journal_event_id, str(observation["kind"]), observation.get("turn"),
                 content_hash(observation.get("payload", {})),
                 canonical_json(observation.get("payload", {})),
                 str(observation.get("continuity", "complete"))),
            )

    def changes_since(self, scope: MemoryScope, timeline_id: str, since_cursor: int,
                      *, limit: int = 512) -> list[dict[str, Any]]:
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT observation_sequence,journal_event_id,turn,payload_json,continuity "
                "FROM world_observation_projection WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? AND observation_kind='world_object' "
                "AND observation_sequence>? ORDER BY observation_sequence,journal_event_id LIMIT ?",
                (*self._scope_tuple(scope, timeline_id), max(0, int(since_cursor)),
                 min(max(int(limit), 1), 2048)),
            ).fetchall()
        return [{
            "observation_cursor": int(row["observation_sequence"]),
            "journal_event_id": str(row["journal_event_id"]),
            "turn": row["turn"], "continuity": str(row["continuity"]),
            "delta": json.loads(row["payload_json"]),
        } for row in rows]

    def temporal_events_since(self, scope: MemoryScope, timeline_id: str,
                              since_cursor: int, *, limit: int = 256) -> list[dict[str, Any]]:
        """Return bounded provider-safe semantic history, never native feed rows."""
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT observation_sequence,journal_event_id,turn,payload_json,continuity "
                "FROM world_observation_projection WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? AND observation_kind='semantic_event' "
                "AND observation_sequence>? ORDER BY observation_sequence,journal_event_id LIMIT ?",
                (*self._scope_tuple(scope, timeline_id), max(0, int(since_cursor)),
                 min(max(int(limit), 1), 1024)),
            ).fetchall()
        return [{
            "observation_cursor": int(row["observation_sequence"]),
            "journal_event_id": str(row["journal_event_id"]), "turn": row["turn"],
            "continuity": str(row["continuity"]),
            "event": json.loads(row["payload_json"]),
        } for row in rows]

    def current_anchor(self, scope: MemoryScope, timeline_id: str, context_tier: str) -> dict[str, Any] | None:
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT * FROM world_anchors WHERE match_id=? AND agent_id=? AND perspective_id=? "
                "AND timeline_id=? AND context_tier=? AND status='current'",
                (*self._scope_tuple(scope, timeline_id), context_tier),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def load_regions(self, scope: MemoryScope, timeline_id: str,
                     mobility_profile_ref: str) -> list[Region]:
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM world_regions WHERE match_id=? AND agent_id=? AND "
                "perspective_id=? AND timeline_id=? AND mobility_profile_ref=?",
                (*self._scope_tuple(scope, timeline_id), mobility_profile_ref),
            ).fetchall()
        return [Region(
            str(row["region_ref"]), str(row["lineage_ref"]), int(row["version"]),
            str(row["mobility_profile_ref"]), str(row["anchor_location_ref"]),
            frozenset(json.loads(row["location_refs_json"])),
            tuple(json.loads(row["supersedes_json"])),
        ) for row in rows]

    def save_regions(self, scope: MemoryScope, timeline_id: str,
                     regions: Iterable[Region], world_revision: int) -> None:
        values = list(regions)
        profiles = sorted({item.mobility_profile_ref for item in values})
        with self.store.transaction() as connection:
            for profile in profiles:
                connection.execute(
                    "DELETE FROM world_regions WHERE match_id=? AND agent_id=? AND "
                    "perspective_id=? AND timeline_id=? AND mobility_profile_ref=?",
                    (*self._scope_tuple(scope, timeline_id), profile),
                )
            connection.executemany(
                "INSERT INTO world_regions(match_id,agent_id,perspective_id,timeline_id,"
                "mobility_profile_ref,region_ref,lineage_ref,version,anchor_location_ref,"
                "location_refs_json,supersedes_json,updated_world_revision) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                [(*self._scope_tuple(scope, timeline_id), item.mobility_profile_ref,
                  item.region_ref, item.lineage_ref, item.version, item.anchor_location_ref,
                  canonical_json(sorted(item.location_refs)), canonical_json(item.supersedes),
                  int(world_revision)) for item in values],
            )

    def save_anchor(
        self, scope: MemoryScope, identity: WorldIdentity, *, world_revision: int,
        observation_cursor: int, context_tier: str, payload: Mapping[str, Any],
        token_estimate: int, object_hashes: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        anchor_id = "anchor-" + uuid.uuid4().hex
        integrity = content_hash(payload)
        now = time.time()
        key = self._scope_tuple(scope, identity.timeline_id)
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE world_anchors SET status='superseded',superseded_unix=? WHERE " \
                "match_id=? AND agent_id=? AND perspective_id=? AND timeline_id=? " \
                "AND context_tier=? AND status='current'", (now, *key, context_tier),
            )
            connection.execute(
                "INSERT INTO world_anchors(world_anchor_id,match_id,agent_id,perspective_id," \
                "timeline_id,world_epoch,world_anchor_revision,anchor_observation_cursor," \
                "context_tier,projection_integrity_hash,payload_json,token_estimate,status," \
                "created_unix) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'current',?)",
                (anchor_id, *key, identity.world_epoch, world_revision, observation_cursor,
                 context_tier, integrity, canonical_json(payload), token_estimate, now),
            )
            if object_hashes:
                connection.executemany(
                    "INSERT INTO world_anchor_baselines(world_anchor_id,object_ref,object_hash) "
                    "VALUES(?,?,?)",
                    [(anchor_id, ref, digest) for ref, digest in sorted(object_hashes.items())],
                )
            # Anchors are provider-context checkpoints, not a second historical
            # timeline.  The campaign journal owns history; retain exactly one
            # materialized anchor per perspective/tier and let FK cascades remove
            # its obsolete baseline rows.
            connection.execute(
                "DELETE FROM world_anchors WHERE match_id=? AND agent_id=? AND "
                "perspective_id=? AND timeline_id=? AND context_tier=? "
                "AND world_anchor_id<>?",
                (*key, context_tier, anchor_id),
            )
        return {
            "world_anchor_id": anchor_id, "world_anchor_revision": world_revision,
            "anchor_observation_cursor": observation_cursor, "context_tier": context_tier,
            "projection_integrity_hash": integrity, "payload": dict(payload),
            "token_estimate": token_estimate,
        }

    def anchor_baseline(self, world_anchor_id: str) -> dict[str, str]:
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT object_ref,object_hash FROM world_anchor_baselines WHERE world_anchor_id=?",
                (world_anchor_id,),
            ).fetchall()
        return {str(row["object_ref"]): str(row["object_hash"]) for row in rows}

    def cached_query(self, fingerprint: str, dependency_hash: str) -> dict[str, Any] | None:
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT result_json,token_estimate FROM world_query_cache " \
                "WHERE query_fingerprint=? AND dependency_hash=?", (fingerprint, dependency_hash),
            ).fetchone()
            if not row:
                return None
            connection.execute(
                "UPDATE world_query_cache SET hit_count=hit_count+1,last_hit_unix=? " \
                "WHERE query_fingerprint=?", (time.time(), fingerprint),
            )
        result = json.loads(row["result_json"])
        result["cache"] = {"hit": True, "query_fingerprint": fingerprint}
        return result

    def put_cached_query(
        self, scope: MemoryScope, identity: WorldIdentity, *, world_revision: int,
        observation_cursor: int, ruleset_hash: str, calculator_version: str,
        dependency_hash: str, request: Mapping[str, Any], result: Mapping[str, Any],
        token_estimate: int,
    ) -> str:
        fingerprint = content_hash({
            "scope": identity.as_dict(),
            "ruleset_hash": ruleset_hash, "calculator_version": calculator_version,
            "request": request,
        })
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO world_query_cache(query_fingerprint,match_id,agent_id," \
                "perspective_id,timeline_id,world_epoch,world_revision,observation_cursor," \
                "ruleset_hash,calculator_version,dependency_hash,request_json,result_json," \
                "token_estimate,created_unix) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (fingerprint, scope.match_id, scope.agent_id, scope.perspective_id,
                 identity.timeline_id, identity.world_epoch, world_revision, observation_cursor,
                 ruleset_hash, calculator_version, dependency_hash, canonical_json(request),
                 canonical_json(result), token_estimate, time.time()),
            )
        return fingerprint

    def snapshot(
        self, scope: MemoryScope, identity: WorldIdentity, *, journal_head_hash: str,
        journal_sequence: int, calculator_versions: Mapping[str, str],
    ) -> dict[str, Any]:
        projection = self.load(scope, identity.timeline_id)
        if not projection:
            raise WorldStoreError("world_projection_missing")
        if projection["identity"]["world_epoch"] != identity.world_epoch:
            raise WorldStoreError("snapshot_world_epoch_mismatch")
        payload = {
            "schema": "smacx.world-snapshot.v1", "identity": identity.as_dict(),
            "world_revision": projection["world_revision"],
            "journal_head_hash": journal_head_hash, "journal_sequence": journal_sequence,
            "observation_cursor": projection["observation_cursor"],
            "projection_checksum": projection["projection_checksum"],
            "calculator_versions": dict(calculator_versions), "projection": projection,
            # The frozen analyst view includes semantic, provider-safe temporal
            # evidence only. Collector-private native rows never enter it.
            "temporal_events": self.temporal_events_since(
                scope, identity.timeline_id, 0, limit=1024,
            ),
        }
        digest = content_hash(payload)
        directory = self.root / scope.match_id / scope.perspective_id / identity.timeline_id
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{digest}.json"
        if not target.exists():
            descriptor, name = tempfile.mkstemp(prefix=".snapshot-", dir=directory)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(canonical_json(payload))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, target)
            finally:
                try:
                    os.unlink(name)
                except FileNotFoundError:
                    pass
        snapshot_id = "snapshot-" + digest[:48]
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO world_snapshots(snapshot_id,match_id,agent_id," \
                "perspective_id,timeline_id,world_epoch,world_revision,journal_head_hash," \
                "journal_sequence,observation_cursor,projection_checksum,calculator_versions_json," \
                "content_path,content_sha256,created_unix) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (snapshot_id, scope.match_id, scope.agent_id, scope.perspective_id,
                 identity.timeline_id, identity.world_epoch, projection["world_revision"],
                 journal_head_hash, journal_sequence, projection["observation_cursor"],
                 projection["projection_checksum"], canonical_json(calculator_versions),
                 str(target), digest, time.time()),
            )
        return {
            "snapshot_id": snapshot_id, "content_sha256": digest, "path": str(target),
            "match_id": scope.match_id, "agent_id": scope.agent_id,
            "perspective_id": scope.perspective_id, "timeline_id": identity.timeline_id,
            "world_epoch": identity.world_epoch,
            "journal_head_hash": journal_head_hash, "journal_sequence": journal_sequence,
        }

    def verify_snapshot(self, snapshot_id: str, *, journal_head_hash: str,
                        journal_sequence: int) -> dict[str, Any]:
        """Load a derived accelerator only when its exact journal head matches."""
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT * FROM world_snapshots WHERE snapshot_id=?", (snapshot_id,),
            ).fetchone()
        if not row:
            raise WorldStoreError("world_snapshot_missing")
        if row["journal_head_hash"] != journal_head_hash \
                or int(row["journal_sequence"]) != int(journal_sequence):
            raise WorldStoreError("world_snapshot_journal_head_mismatch")
        path = Path(str(row["content_path"])).resolve()
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise WorldStoreError("world_snapshot_content_missing") from exc
        digest = hashlib.sha256(raw).hexdigest()
        if digest != row["content_sha256"]:
            raise WorldStoreError("world_snapshot_integrity_failure")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WorldStoreError("world_snapshot_invalid") from exc
        if payload.get("journal_head_hash") != journal_head_hash \
                or int(payload.get("journal_sequence", -1)) != int(journal_sequence) \
                or payload.get("projection_checksum") != row["projection_checksum"]:
            raise WorldStoreError("world_snapshot_manifest_mismatch")
        return payload

    def pin_snapshot(self, snapshot_id: str, owner_kind: str, owner_id: str) -> None:
        if owner_kind not in {"specialist_mission", "checkpoint", "recovery"}:
            raise WorldStoreError("invalid_snapshot_pin_owner")
        with self.store.transaction() as connection:
            if not connection.execute(
                "SELECT 1 FROM world_snapshots WHERE snapshot_id=?", (snapshot_id,),
            ).fetchone():
                raise WorldStoreError("world_snapshot_missing")
            connection.execute(
                "INSERT OR IGNORE INTO world_snapshot_pins(snapshot_id,owner_kind,owner_id,pinned_unix) "
                "VALUES(?,?,?,?)", (snapshot_id, owner_kind, owner_id, time.time()),
            )

    def unpin_snapshot(self, snapshot_id: str, owner_kind: str, owner_id: str) -> None:
        with self.store.transaction() as connection:
            connection.execute(
                "DELETE FROM world_snapshot_pins WHERE snapshot_id=? AND owner_kind=? AND owner_id=?",
                (snapshot_id, owner_kind, owner_id),
            )

    def load_snapshot_content(self, snapshot_id: str) -> dict[str, Any]:
        """Load a pinned immutable view without consulting the live projection."""
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT content_path,content_sha256 FROM world_snapshots WHERE snapshot_id=?",
                (snapshot_id,),
            ).fetchone()
        if not row:
            raise WorldStoreError("world_snapshot_missing")
        try:
            raw = Path(str(row["content_path"])).read_bytes()
        except OSError as exc:
            raise WorldStoreError("world_snapshot_content_missing") from exc
        if hashlib.sha256(raw).hexdigest() != str(row["content_sha256"]):
            raise WorldStoreError("world_snapshot_integrity_failure")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WorldStoreError("world_snapshot_invalid") from exc
        if not isinstance(value, dict):
            raise WorldStoreError("world_snapshot_invalid")
        return value

    def restore_projection_from_snapshot(
        self, scope: MemoryScope, payload: Mapping[str, Any], *,
        target_timeline_id: str, journal_head_hash: str,
    ) -> dict[str, Any]:
        """Materialize a verified accelerator into a new rollback timeline.

        The caller must first verify the snapshot against the selected parent
        journal head. This method validates the projection again and rewrites
        only timeline identity; canonical temporal authority remains the
        forked journal prefix.
        """
        if payload.get("schema") != "smacx.world-snapshot.v1":
            raise WorldStoreError("world_snapshot_schema_mismatch")
        source_identity = payload.get("identity")
        projection = payload.get("projection")
        if not isinstance(source_identity, Mapping) or not isinstance(projection, Mapping):
            raise WorldStoreError("world_snapshot_projection_missing")
        if source_identity.get("match_id") != scope.match_id \
                or source_identity.get("perspective_id") != scope.perspective_id:
            raise WorldStoreError("world_snapshot_scope_mismatch")
        raw_objects = projection.get("objects")
        if not isinstance(raw_objects, list):
            raise WorldStoreError("world_snapshot_objects_missing")
        objects = [WorldObject.from_dict(item) for item in raw_objects
                   if isinstance(item, Mapping)]
        if len(objects) != len(raw_objects) \
                or content_hash([item.as_dict(provider_safe=False) for item in objects]) \
                != str(payload.get("projection_checksum") or ""):
            raise WorldStoreError("world_snapshot_projection_checksum_mismatch")
        identity = WorldIdentity(
            scope.match_id, scope.perspective_id, target_timeline_id,
            str(source_identity.get("world_epoch") or ""),
        )
        restored = self.replace_projection(
            scope, identity, objects,
            observation_cursor=int(payload.get("observation_cursor") or 0),
            action_revision=(str(projection.get("action_revision"))
                             if projection.get("action_revision") is not None else None),
            continuity=str(projection.get("continuity") or "complete"),
            journal_head_hash=journal_head_hash,
        )
        if restored["projection_checksum"] != payload.get("projection_checksum"):
            raise WorldStoreError("world_snapshot_restore_checksum_mismatch")
        return {**restored, "world_epoch": identity.world_epoch,
                "timeline_id": target_timeline_id}

    def discard_future(self, scope: MemoryScope, active_timeline_id: str) -> None:
        """Remove rebuildable artifacts outside the active rollback timeline."""
        with self.store.transaction() as connection:
            key = (scope.match_id, scope.agent_id, scope.perspective_id, active_timeline_id)
            # Dependency order matters: lease items reference attention items,
            # and world objects reference world heads.
            # Content-addressed checkpoint snapshots are deliberately retained:
            # a repeated restore of the same advertised checkpoint must remain
            # possible and deterministic. Checkpoint-retention GC owns them.
            # Specialist missions/attempts/traces are diagnostic evidence and
            # are never erased by rollback.  They are cancelled/marked
            # non-model-visible by the specialist lifecycle reconciler.
            for table in ("attention_leases", "attention_items", "attention_heads", "world_query_cache",
                          "world_anchors", "world_observation_projection", "world_regions",
                          "world_watches", "cognitive_operations",
                          "sovereign_leases", "world_heads"):
                connection.execute(
                    f"DELETE FROM {table} WHERE match_id=? AND agent_id=? AND perspective_id=? "
                    "AND timeline_id<>?", key,
                )

    def telemetry(
        self, category: str, metric: str, value: float | None,
        *, scope: MemoryScope | None = None, timeline_id: str | None = None,
        dimensions: Mapping[str, Any] | None = None,
    ) -> None:
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO world_telemetry(telemetry_id,match_id,agent_id,perspective_id," \
                "timeline_id,category,metric,value_real,dimensions_json,recorded_unix) " \
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("telemetry-" + uuid.uuid4().hex,
                 scope.match_id if scope else None, scope.agent_id if scope else None,
                 scope.perspective_id if scope else None, timeline_id, category, metric,
                 value, canonical_json(dimensions or {}), time.time()),
            )
