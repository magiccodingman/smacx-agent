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

    def _native_stage_path(self, scope: MemoryScope, timeline_id: str) -> Path:
        return (self.root / "native-observation-staging" / scope.match_id
                / scope.agent_id / scope.perspective_id / f"{timeline_id}.json")

    def _native_publication_path(self, scope: MemoryScope, timeline_id: str) -> Path:
        return self._native_stage_path(scope, timeline_id).with_suffix(".publication.json")

    @staticmethod
    def _atomic_private_json(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".native-stage-", dir=path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(canonical_json(value))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def load_native_observation_stage(
        self, scope: MemoryScope, timeline_id: str, *, include_publication: bool = True,
    ) -> dict[str, Any]:
        """Load provider-inaccessible two-phase native observation staging."""
        path = self._native_stage_path(scope, timeline_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            value = {}
        except (OSError, json.JSONDecodeError) as exc:
            raise WorldStoreError("native_observation_stage_invalid") from exc
        if value and value.get("schema") != "smacx.private-native-stage.v1":
            raise WorldStoreError("native_observation_stage_schema_mismatch")
        publication = value.get("publication_package")
        publication_manifest = value.get("publication_manifest")
        # Normalize an in-flight publication written by the earlier embedded
        # package format. Recovery must still acknowledge the exact frozen
        # source boundary rather than falling back to the mutable stage.
        if isinstance(publication, Mapping) and not isinstance(publication_manifest, Mapping):
            publication_manifest = {
                "publication_hash": str(publication.get("publication_hash") or ""),
                "source_through_sequence": int(
                    publication.get("source_through_sequence") or 0
                ),
                "observation_cursor": int(
                    publication.get("observation_cursor")
                    or value.get("publication_observation_cursor") or 0
                ),
            }
        if publication is None and isinstance(publication_manifest, Mapping) \
                and include_publication:
            try:
                publication = json.loads(self._native_publication_path(
                    scope, timeline_id,
                ).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise WorldStoreError("native_observation_publication_missing") from exc
        if publication is not None and include_publication:
            if not isinstance(publication, Mapping):
                raise WorldStoreError("native_observation_publication_invalid")
            publication = dict(publication)
            supplied_hash = str(publication.get("publication_hash") or "")
            hash_input = {key: item for key, item in publication.items()
                          if key != "publication_hash"}
            if not supplied_hash or content_hash(hash_input) != supplied_hash:
                raise WorldStoreError("native_observation_publication_hash_mismatch")
            if isinstance(publication_manifest, Mapping) and supplied_hash != str(
                    publication_manifest.get("publication_hash") or ""):
                raise WorldStoreError("native_observation_publication_manifest_mismatch")
        return {
            "schema": "smacx.private-native-stage.v1",
            "staged_after_sequence": int(value.get("staged_after_sequence") or 0),
            "committed_after_sequence": int(value.get("committed_after_sequence") or 0),
            "publication_observation_cursor": value.get("publication_observation_cursor"),
            "publication_package": publication,
            "publication_manifest": dict(publication_manifest)
                if isinstance(publication_manifest, Mapping) else None,
            "events": [dict(item) for item in value.get("events", [])
                       if isinstance(item, Mapping)],
            "episode_state": dict(value.get("episode_state") or {}),
            "continuity_gaps": list(value.get("continuity_gaps") or []),
            "continuity_gap": dict(value["continuity_gap"])
                if isinstance(value.get("continuity_gap"), Mapping) else None,
        }

    def stage_native_observation_feed(
        self, scope: MemoryScope, timeline_id: str, *, events: Iterable[Mapping[str, Any]],
        next_sequence: int, continuity_gap: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        stage = self.load_native_observation_stage(
            scope, timeline_id, include_publication=False,
        )
        if stage.get("publication_manifest") is not None \
                or stage.get("publication_observation_cursor") is not None:
            raise WorldStoreError("native_observation_publication_in_progress")
        by_sequence = {
            int(item["native_sequence"]): dict(item)
            for item in stage["events"] if isinstance(item.get("native_sequence"), int)
        }
        for item in events:
            sequence = item.get("native_sequence")
            if isinstance(sequence, int) and sequence > 0:
                by_sequence[sequence] = dict(item)
        # Never discard already drained transient evidence to satisfy a memory
        # cap. This provider-inaccessible disk stage may span many native-ring
        # windows while publication is retrying. A hard failure before cursor
        # advancement is safer than silently converting observed history into
        # absence; the later ring read will then produce an explicit gap if it
        # has genuinely overflowed.
        if len(by_sequence) > 65_536:
            raise WorldStoreError("native_observation_stage_capacity_exceeded")
        stage["events"] = [by_sequence[key] for key in sorted(by_sequence)]
        stage["staged_after_sequence"] = max(
            int(stage["staged_after_sequence"]), int(next_sequence),
        )
        if continuity_gap is not None:
            stage["continuity_gap"] = dict(continuity_gap)
            if dict(continuity_gap) not in stage["continuity_gaps"]:
                stage["continuity_gaps"].append(dict(continuity_gap))
        self._atomic_private_json(self._native_stage_path(scope, timeline_id), stage)
        return stage

    def begin_native_observation_publication(
        self, scope: MemoryScope, timeline_id: str, observation_cursor: int,
        publication_package: Mapping[str, Any],
    ) -> dict[str, Any]:
        stage = self.load_native_observation_stage(
            scope, timeline_id, include_publication=False,
        )
        existing = stage.get("publication_observation_cursor")
        if existing is not None and int(existing) != int(observation_cursor):
            raise WorldStoreError("native_observation_publication_cursor_mismatch")
        package = dict(publication_package)
        package["observation_cursor"] = int(observation_cursor)
        package.pop("publication_hash", None)
        package["publication_hash"] = content_hash(package)
        prior_manifest = stage.get("publication_manifest")
        if isinstance(prior_manifest, Mapping) and str(
                prior_manifest.get("publication_hash") or "") != package["publication_hash"]:
            raise WorldStoreError("native_observation_publication_package_mismatch")
        self._atomic_private_json(
            self._native_publication_path(scope, timeline_id), package,
        )
        stage["publication_observation_cursor"] = int(observation_cursor)
        stage["publication_package"] = None
        stage["publication_manifest"] = {
            "publication_hash": package["publication_hash"],
            "source_through_sequence": int(package.get("source_through_sequence") or 0),
            "observation_cursor": int(observation_cursor),
            "episode_state": package.get("episode_state", stage.get("episode_state", {})),
        }
        self._atomic_private_json(self._native_stage_path(scope, timeline_id), stage)
        stage["publication_package"] = package
        return stage

    def acknowledge_native_observation_publication(
        self, scope: MemoryScope, timeline_id: str, observation_cursor: int,
    ) -> dict[str, Any]:
        stage = self.load_native_observation_stage(
            scope, timeline_id, include_publication=False,
        )
        if int(stage.get("publication_observation_cursor") or -1) != int(observation_cursor):
            raise WorldStoreError("native_observation_ack_cursor_mismatch")
        publication = stage.get("publication_manifest")
        if not isinstance(publication, Mapping):
            raise WorldStoreError("native_observation_ack_package_missing")
        through_sequence = int(publication.get("source_through_sequence") or 0)
        stage["committed_after_sequence"] = max(
            int(stage["committed_after_sequence"]), through_sequence,
        )
        stage["staged_after_sequence"] = max(
            int(stage["committed_after_sequence"]), through_sequence,
        )
        stage["events"] = [
            item for item in stage["events"]
            if int(item.get("native_sequence") or 0) > through_sequence
        ]
        stage["episode_state"] = dict(publication.get("episode_state") or stage.get("episode_state") or {})
        stage["continuity_gaps"] = []
        stage["continuity_gap"] = None
        stage["publication_observation_cursor"] = None
        stage["publication_package"] = None
        stage["publication_manifest"] = None
        self._atomic_private_json(self._native_stage_path(scope, timeline_id), stage)
        self._native_publication_path(scope, timeline_id).unlink(missing_ok=True)
        return stage

    @staticmethod
    def _scope_tuple(scope: MemoryScope, timeline_id: str) -> tuple[str, str, str, str]:
        return scope.match_id, scope.agent_id, scope.perspective_id, timeline_id

    def load(self, scope: MemoryScope, timeline_id: str) -> dict[str, Any] | None:
        self.store.require_scope(scope)
        with self.store._connect() as connection:  # projection-private API
            connection.execute("BEGIN")  # head and objects belong to one SQLite read snapshot
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
            existing_rows = {
                str(row["object_ref"]): str(row["dependency_hash"])
                for row in connection.execute(
                    "SELECT object_ref,dependency_hash FROM world_objects WHERE "
                    "match_id=? AND agent_id=? AND perspective_id=? AND timeline_id=?", key,
                ).fetchall()
            }
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
            incoming = {str(item["object_ref"]): item for item in rows}
            removed_refs = sorted(set(existing_rows) - set(incoming))
            epoch_changed = bool(previous and previous["world_epoch"] != identity.world_epoch)
            changed_rows = [
                item for item in rows
                if epoch_changed
                or existing_rows.get(str(item["object_ref"])) != content_hash(item)
            ]
            if removed_refs:
                connection.executemany(
                    "DELETE FROM world_objects WHERE match_id=? AND agent_id=? "
                    "AND perspective_id=? AND timeline_id=? AND object_ref=?",
                    [(*key, object_ref) for object_ref in removed_refs],
                )
            connection.executemany(
                "INSERT OR REPLACE INTO world_objects(match_id,agent_id,perspective_id,timeline_id,world_epoch," \
                "object_ref,object_kind,location_ref,parent_ref,status,payload_json,dependency_hash," \
                "updated_revision,updated_unix) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(*key, identity.world_epoch, item["object_ref"], item["kind"],
                  item.get("location_ref"), item.get("parent_ref"), item.get("status", "active"),
                  canonical_json(item), content_hash(item), revision, now) for item in changed_rows],
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
            "projection_rows_written": len(changed_rows) + len(removed_refs) + 1,
            "projection_object_rows_written": len(changed_rows) + len(removed_refs),
        }

    def record_observation_projection(
        self, scope: MemoryScope, timeline_id: str, observation: Mapping[str, Any],
        journal_event_id: str,
    ) -> None:
        self.record_observation_projections(scope, timeline_id, [(observation, journal_event_id)])

    def record_observation_projections(
        self, scope: MemoryScope, timeline_id: str,
        observations: Iterable[tuple[Mapping[str, Any], str]],
    ) -> None:
        """Publish journal-backed cache rows with one SQLite commit per batch.

        Callers append canonical events before entering this transaction;
        replaying a frozen publication reconstructs these disposable rows.
        """
        with self.store.transaction() as connection:
            connection.executemany(
                "INSERT OR IGNORE INTO world_observation_projection(" \
                "match_id,agent_id,perspective_id,timeline_id,observation_sequence," \
                "journal_event_id,observation_kind,turn,payload_hash,payload_json,continuity) " \
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ((*self._scope_tuple(scope, timeline_id), int(observation["sequence"]),
                 journal_event_id, str(observation["kind"]), observation.get("turn"),
                 content_hash(observation.get("payload", {})),
                 canonical_json(observation.get("payload", {})),
                 str(observation.get("continuity", "complete")))
                 for observation, journal_event_id in observations),
            )

    def committed_cursor(self, scope: MemoryScope, timeline_id: str, connection=None) -> int:
        if connection is None:
            with self.store._connect() as connection:
                return self.committed_cursor(scope, timeline_id, connection)
        row = connection.execute("SELECT observation_cursor FROM world_heads WHERE match_id=? "
            "AND agent_id=? AND perspective_id=? AND timeline_id=?",
            self._scope_tuple(scope, timeline_id)).fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def event_visible(event: Mapping[str, Any], committed_cursor: int) -> bool:
        payload = event.get("payload") or {}
        cursor = payload.get("observation_cursor", payload.get("observation_sequence", 0))
        return int(cursor or 0) <= committed_cursor

    def changes_since(self, scope: MemoryScope, timeline_id: str, since_cursor: int,
                      *, limit: int = 512, through_cursor: int | None = None) -> list[dict[str, Any]]:
        row_limit = min(max(int(limit), 1), 2048)
        with self.store._connect() as connection:
            connection.execute("BEGIN")
            cap = self.committed_cursor(scope, timeline_id, connection)
            if through_cursor is not None: cap = min(cap, int(through_cursor))
            rows = connection.execute(
                "SELECT observation_sequence,journal_event_id,turn,payload_json,continuity "
                "FROM world_observation_projection WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? AND observation_kind IN "
                "('world_object','world_batch') "
                "AND observation_sequence>? AND observation_sequence<=? ORDER BY observation_sequence,rowid LIMIT ?",
                (*self._scope_tuple(scope, timeline_id), max(0, int(since_cursor)), cap,
                 row_limit),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            deltas = payload.get("deltas") if isinstance(payload, Mapping) else None
            values = deltas if isinstance(deltas, list) else [payload]
            for delta in values:
                if not isinstance(delta, Mapping):
                    continue
                result.append({
                    "observation_cursor": int(row["observation_sequence"]),
                    "journal_event_id": str(row["journal_event_id"]),
                    "turn": row["turn"], "continuity": str(row["continuity"]),
                    "delta": dict(delta),
                })
                if len(result) >= row_limit:
                    return result
        return result

    def temporal_events_since(self, scope: MemoryScope, timeline_id: str,
                              since_cursor: int, *, limit: int = 256, through_cursor: int | None = None) -> list[dict[str, Any]]:
        """Return bounded provider-safe semantic history, never native feed rows."""
        row_limit = min(max(int(limit), 1), 1024)
        with self.store._connect() as connection:
            connection.execute("BEGIN")
            cap = self.committed_cursor(scope, timeline_id, connection)
            if through_cursor is not None: cap = min(cap, int(through_cursor))
            rows = connection.execute(
                "SELECT observation_sequence,journal_event_id,turn,payload_json,continuity "
                "FROM world_observation_projection WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? AND observation_kind IN "
                "('semantic_event','semantic_batch') "
                "AND observation_sequence>? AND observation_sequence<=? ORDER BY observation_sequence,rowid LIMIT ?",
                (*self._scope_tuple(scope, timeline_id), max(0, int(since_cursor)), cap,
                 row_limit),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            events = payload.get("events") if isinstance(payload, Mapping) else None
            values = events if isinstance(events, list) else [payload]
            for event in values:
                if not isinstance(event, Mapping):
                    continue
                result.append({
                    "observation_cursor": int(row["observation_sequence"]),
                    "journal_event_id": str(row["journal_event_id"]), "turn": row["turn"],
                    "continuity": str(row["continuity"]), "event": dict(event),
                })
                if len(result) >= row_limit:
                    return result
        return result

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
            int(row["lineage_birth_revision"]),
        ) for row in rows]

    def save_regions(self, scope: MemoryScope, timeline_id: str,
                     regions: Iterable[Region], world_revision: int,
                     mobility_profiles: Iterable[str] = ()) -> None:
        values = list(regions)
        profiles = sorted({item.mobility_profile_ref for item in values}
                          | set(map(str, mobility_profiles)))
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
                "location_refs_json,supersedes_json,lineage_birth_revision,updated_world_revision) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(*self._scope_tuple(scope, timeline_id), item.mobility_profile_ref,
                  item.region_ref, item.lineage_ref, item.version, item.anchor_location_ref,
                  canonical_json(sorted(item.location_refs)), canonical_json(item.supersedes),
                  int(item.lineage_birth_revision),
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

    def prune_query_cache(self, scope: MemoryScope, timeline_id: str, world_epoch: str, *, recent: int = 64) -> dict[str, int]:
        """Retain recent queries and explicit active intent, not campaign history.

        This disposable cache needs no schema migration. SQL extracts only
        derived handles for retention; full results/dependencies are parsed
        only after pruning. The canonical journal owns plan pins.
        """
        from smacx_journal import CampaignJournal
        journal = getattr(self.store, "_query_journal", None)
        if journal is None:
            journal = CampaignJournal(self.store.path.parent / "campaigns", timeline_resolver=self.store.active_timeline_id)
            self.store._query_journal = journal
        key = self._scope_tuple(scope, timeline_id)
        pins = set()
        def references(value):
            if isinstance(value, str): pins.add(value)
            elif isinstance(value, Mapping):
                for child in value.values(): references(child)
            elif isinstance(value, (list, tuple)):
                for child in value: references(child)
        for plan in journal._current_records(journal.replay(scope, sections=("plans",)), "plans"):
            if plan.get("status", "active") != "active":
                continue
            references(plan.get("dependencies", []))
            references(plan.get("target_refs", []))
            references(plan.get("participants", []))
        with self.store.transaction() as connection:
            for row in connection.execute("SELECT subject_refs_json,typed_predicate_json FROM world_watches "
                    "WHERE match_id=? AND agent_id=? AND perspective_id=? AND timeline_id=? AND world_epoch=? AND status='active'", (*key,world_epoch)):
                references(json.loads(row[0])); references(json.loads(row[1]))
            for row in connection.execute("SELECT referenced_world_objects_json FROM cognitive_operations "
                    "WHERE match_id=? AND agent_id=? AND perspective_id=? AND timeline_id=? AND source_world_epoch=? "
                    "AND status IN ('active','stale')", (*key,world_epoch)):
                references(json.loads(row[0]))
            rows = connection.execute("SELECT query_fingerprint,json_extract(result_json,'$.route.route_ref') AS route_ref, "
                "(SELECT json_group_array(json_extract(value,'$.rendezvous_ref')) FROM json_each(result_json,'$.items') "
                "WHERE type='object' AND json_extract(value,'$.rendezvous_ref') IS NOT NULL) AS rendezvous_refs "
                "FROM world_query_cache WHERE match_id=? AND agent_id=? AND perspective_id=? AND timeline_id=? AND world_epoch=? "
                "ORDER BY COALESCE(last_hit_unix,created_unix) DESC,query_fingerprint", (*key,world_epoch)).fetchall()
            removed = [row['query_fingerprint'] for index,row in enumerate(rows) if index >= recent
                       and row['route_ref'] not in pins and not pins.intersection(json.loads(row['rendezvous_refs'] or '[]'))]
            connection.executemany("DELETE FROM world_query_cache WHERE query_fingerprint=?",[(ref,) for ref in removed])
        return {"rows_before":len(rows), "removed":len(removed), "retained":len(rows)-len(removed)}

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
        result.pop("_inspection", None)
        result["cache"] = {"hit": True, "query_fingerprint": fingerprint}
        return result

    def record_inspection(self, fingerprint: str, world_revision: int, action_revision: str | None) -> None:
        with self.store.transaction() as connection:
            connection.execute("UPDATE world_query_cache SET result_json=json_set(result_json,'$._inspection',json(?)) WHERE query_fingerprint=?",
                (canonical_json({"world_revision":world_revision,"action_revision":action_revision,"validated_unix":time.time()}),fingerprint))

    def put_cached_query(
        self, scope: MemoryScope, identity: WorldIdentity, *, world_revision: int,
        observation_cursor: int, ruleset_hash: str, calculator_version: str,
        dependency_hash: str, request: Mapping[str, Any], result: Mapping[str, Any],
        token_estimate: int, action_revision: str | None = None,
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
                 canonical_json({**result,"_inspection":{"world_revision":world_revision,
                    "action_revision":(result.get("valid_while") or {}).get("action_revision"),"validated_unix":time.time()}}), token_estimate, time.time()),
            )
        self.prune_query_cache(scope, identity.timeline_id, identity.world_epoch)
        return fingerprint

    def recent_inspection_refs(
        self, scope: MemoryScope, timeline_id: str, world_revision: int, *, limit: int = 8,
    ) -> list[str]:
        """Compatibility adapter; the query service owns dependency validity."""
        from smacx_world import WorldService
        projection = self.load(scope, timeline_id)
        if not projection or int(projection["world_revision"]) != int(world_revision):
            return []
        return WorldService(self, scope).recent_inspection_refs(projection, limit=limit)

    def recent_material_refs(
        self, scope: MemoryScope, timeline_id: str, observation_cursor: int,
        current_turn: int | None, *, sequence_window: int = 8, limit: int = 64,
    ) -> list[str]:
        """Return a short-lived bounded promotion set from authoritative evidence.

        Attention acknowledgement must not immediately erase local strategic
        resolution.  This window is derived from journal-backed observation
        projections and naturally expires as observations/turns advance.
        """
        since = max(0, int(observation_cursor) - max(1, min(sequence_window, 32)))
        rows = [
            *self.changes_since(scope, timeline_id, since, limit=min(limit, 128), through_cursor=observation_cursor),
            *self.temporal_events_since(scope, timeline_id, since, limit=min(limit, 128), through_cursor=observation_cursor),
        ]
        refs: list[str] = []

        def remember(value: Any) -> None:
            if not value:
                return
            text = str(value)
            if text not in refs:
                refs.append(text)

        for row in sorted(rows, key=lambda value: int(value.get("observation_cursor", 0)),
                          reverse=True):
            turn = row.get("turn")
            if current_turn is not None and isinstance(turn, int) and turn < current_turn - 1:
                continue
            payload = row.get("delta") if isinstance(row.get("delta"), Mapping) \
                else row.get("event") if isinstance(row.get("event"), Mapping) else {}
            current = payload.get("current") if isinstance(payload.get("current"), Mapping) else {}
            for key in (
                "object_ref", "location_ref", "from_location_ref", "to_location_ref",
                "contact_ref", "base_ref", "unit_ref", "system_ref",
            ):
                remember(payload.get(key))
            remember(current.get("object_ref"))
            remember(current.get("location_ref"))
            if len(refs) >= max(1, min(limit, 128)):
                break
        return refs[:max(1, min(limit, 128))]

    def snapshot(
        self, scope: MemoryScope, identity: WorldIdentity, *, journal_head_hash: str,
        journal_sequence: int, calculator_versions: Mapping[str, str],
        pin_owner: tuple[str, str] | None = None,
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
                scope, identity.timeline_id, 0, limit=1024, through_cursor=int(projection["observation_cursor"]),
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
        if pin_owner is not None and pin_owner[0] not in {
                "specialist_mission", "checkpoint", "recovery"}:
            raise WorldStoreError("invalid_snapshot_pin_owner")
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
            if pin_owner is not None:
                connection.execute(
                    "INSERT OR IGNORE INTO world_snapshot_pins("
                    "snapshot_id,owner_kind,owner_id,pinned_unix) VALUES(?,?,?,?)",
                    (snapshot_id, pin_owner[0], pin_owner[1], time.time()),
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
        self.gc_snapshot_if_unpinned(snapshot_id)

    def gc_snapshot_if_unpinned(self, snapshot_id: str) -> bool:
        """Delete a derived snapshot only after its final owner releases it."""
        path: Path | None = None
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT content_path FROM world_snapshots WHERE snapshot_id=? AND NOT EXISTS "
                "(SELECT 1 FROM world_snapshot_pins WHERE snapshot_id=?)",
                (snapshot_id, snapshot_id),
            ).fetchone()
            if not row:
                return False
            path = Path(str(row["content_path"])).resolve()
            try:
                path.relative_to(self.root)
            except ValueError as exc:
                raise WorldStoreError("world_snapshot_path_outside_root") from exc
            connection.execute("DELETE FROM world_snapshots WHERE snapshot_id=?", (snapshot_id,))
        if path is not None:
            path.unlink(missing_ok=True)
            for parent in (path.parent, path.parent.parent, path.parent.parent.parent):
                if parent == self.root or self.root not in parent.parents:
                    break
                try:
                    parent.rmdir()
                except OSError:
                    break
        return True

    def gc_unpinned_snapshots(
        self, *, scope: MemoryScope | None = None, exclude_timeline_id: str | None = None,
    ) -> int:
        """Collect unowned accelerators, optionally restricted to obsolete timelines."""
        where = ["NOT EXISTS (SELECT 1 FROM world_snapshot_pins p WHERE "
                 "p.snapshot_id=world_snapshots.snapshot_id)"]
        params: list[Any] = []
        if scope is not None:
            where.extend(["match_id=?", "agent_id=?", "perspective_id=?"])
            params.extend([scope.match_id, scope.agent_id, scope.perspective_id])
        if exclude_timeline_id:
            where.append("timeline_id<>?")
            params.append(exclude_timeline_id)
        with self.store._connect() as connection:
            ids = [str(row["snapshot_id"]) for row in connection.execute(
                "SELECT snapshot_id FROM world_snapshots WHERE " + " AND ".join(where),
                tuple(params),
            ).fetchall()]
        return sum(1 for snapshot_id in ids if self.gc_snapshot_if_unpinned(snapshot_id))

    def gc_orphaned_specialist_snapshot_pins(self) -> int:
        """Release snapshot pins whose specialist mission was never committed.

        Snapshot content and its pin are committed atomically before the
        mission row so a crash can never expose an unpinned mission snapshot.
        The inverse crash window can leave a pin whose owner mission does not
        exist; supervisor startup deterministically repairs that condition.
        """
        with self.store.transaction() as connection:
            snapshot_ids = [str(row["snapshot_id"]) for row in connection.execute(
                "SELECT DISTINCT p.snapshot_id FROM world_snapshot_pins p "
                "LEFT JOIN specialist_missions m ON m.mission_id=p.owner_id "
                "WHERE p.owner_kind='specialist_mission' AND m.mission_id IS NULL"
            ).fetchall()]
            connection.execute(
                "DELETE FROM world_snapshot_pins WHERE owner_kind='specialist_mission' "
                "AND NOT EXISTS (SELECT 1 FROM specialist_missions m "
                "WHERE m.mission_id=world_snapshot_pins.owner_id)"
            )
        for snapshot_id in snapshot_ids:
            self.gc_snapshot_if_unpinned(snapshot_id)
        return len(snapshot_ids)

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
        self.gc_unpinned_snapshots(scope=scope, exclude_timeline_id=active_timeline_id)

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
