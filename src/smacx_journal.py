"""Canonical, append-only campaign journals and compact AI notebook state.

The platform database coordinates processes and provides rebuildable query
indexes.  The files managed here are the portable authority for one agent's
fair-play perspective and timeline.  Native saves and provider transcripts are
referenced by digest; they are deliberately not committed to the journal Git
repository.
"""

from __future__ import annotations

from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tarfile
import tempfile
import threading
import time
from typing import Any, Iterator, Mapping
import uuid

from smacx_store import MemoryScope


IDENTITY = re.compile(r"^[A-Za-z0-9_-]{8,96}$")
KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
COLLECTION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MAX_EVENT_BYTES = 256_000
MAX_NOTE_BYTES = 24_000


class JournalError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _safe_identity(value: str, name: str) -> str:
    if not IDENTITY.fullmatch(str(value or "")):
        raise JournalError(f"invalid_{name}")
    return str(value)


def _safe_key(value: str, name: str = "key") -> str:
    if not KEY.fullmatch(str(value or "")):
        raise JournalError(f"invalid_{name}")
    return str(value)


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class CampaignJournal:
    """One installation's durable, timeline-aware campaign journal tree."""

    schema = "smacx.campaign-journal.v1"

    def __init__(self, root: Path | None = None) -> None:
        configured = os.environ.get("SMACX_CAMPAIGN_ROOT", "")
        if root is None:
            if configured:
                root = Path(configured)
            else:
                database = Path(os.environ.get("SMACX_DB_PATH", "/var/lib/smacx/smacx.sqlite3"))
                root = database.parent / "campaigns"
        self.root = root.expanduser().resolve()
        self._replay_cache: dict[str, dict[str, Any]] = {}
        self._cache_lock = threading.RLock()

    def timeline_id(self, scope: MemoryScope, requested: str = "") -> str:
        _safe_identity(scope.match_id, "match_id")
        _safe_identity(scope.agent_id, "agent_id")
        _safe_identity(scope.perspective_id, "perspective_id")
        value = requested or os.environ.get("SMACX_TIMELINE_ID", "timeline-main")
        return _safe_identity(value, "timeline_id")

    def perspective_root(self, scope: MemoryScope, timeline_id: str = "") -> Path:
        timeline = self.timeline_id(scope, timeline_id)
        return (self.root / scope.match_id / "perspectives" / scope.agent_id
                / scope.perspective_id / "timelines" / timeline)

    @contextmanager
    def _locked(self, path: Path, *, shared: bool = False) -> Iterator[None]:
        path.mkdir(parents=True, exist_ok=True)
        lock_path = path / ".journal.lock"
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _load(path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def _manifest(self, scope: MemoryScope, timeline_id: str) -> dict[str, Any]:
        path = self.perspective_root(scope, timeline_id)
        manifest = self._load(path / "manifest.json", {})
        if isinstance(manifest, dict) and manifest.get("schema") == self.schema:
            return manifest
        now = time.time()
        return {
            "schema": self.schema,
            "match_id": scope.match_id,
            "agent_id": scope.agent_id,
            "perspective_id": scope.perspective_id,
            "timeline_id": self.timeline_id(scope, timeline_id),
            "parent_timeline_id": None,
            "forked_from_event_hash": None,
            "sequence": 0,
            "head_hash": "0" * 64,
            "last_turn": None,
            "last_year": None,
            "created_unix": now,
            "updated_unix": now,
        }

    def append(
        self,
        scope: MemoryScope,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        session_id: str | None = None,
        turn: int | None = None,
        year: int | None = None,
        timeline_id: str = "",
        commit_reason: str = "",
    ) -> dict[str, Any]:
        event_type = _safe_key(event_type, "journal_event_type")
        if session_id:
            _safe_identity(session_id, "session_id")
        timeline = self.timeline_id(scope, timeline_id)
        path = self.perspective_root(scope, timeline)
        candidate = dict(payload)
        if len(_canonical(candidate)) > MAX_EVENT_BYTES:
            raise JournalError("journal_event_too_large")
        # Backups take the exclusive form of the root lock. An archive can
        # therefore never split an event write from its manifest/head update.
        with self._locked(self.root, shared=True), self._locked(path):
            manifest = self._manifest(scope, timeline)
            sequence = int(manifest.get("sequence") or 0) + 1
            previous = str(manifest.get("head_hash") or "0" * 64)
            body = {
                "schema": "smacx.campaign-event.v1",
                "event_id": "journal-" + uuid.uuid4().hex,
                "sequence": sequence,
                "event_type": event_type,
                "match_id": scope.match_id,
                "agent_id": scope.agent_id,
                "perspective_id": scope.perspective_id,
                "timeline_id": timeline,
                "session_id": session_id,
                "turn": turn,
                "year": year,
                "recorded_unix": time.time(),
                "previous_hash": previous,
                "payload": candidate,
            }
            body["event_hash"] = hashlib.sha256(previous.encode("ascii") + _canonical(body)).hexdigest()
            event_path = path / "events" / f"{sequence:012d}-{body['event_id']}.json"
            _atomic_json(event_path, body)
            manifest.update({
                "sequence": sequence,
                "head_hash": body["event_hash"],
                "last_event_id": body["event_id"],
                "last_turn": turn if turn is not None else manifest.get("last_turn"),
                "last_year": year if year is not None else manifest.get("last_year"),
                "updated_unix": body["recorded_unix"],
            })
            _atomic_json(path / "manifest.json", manifest)
            self._update_catalog(scope, timeline, manifest)
            cache_key = str(path)
            with self._cache_lock:
                cached = self._replay_cache.get(cache_key)
                if isinstance(cached, dict) \
                        and cached.get("manifest", {}).get("head_hash") == previous:
                    self._apply_event(cached, body)
                    cached["manifest"] = dict(manifest)
            if commit_reason:
                self._git_commit(scope.match_id, f"{commit_reason} [{timeline} #{sequence}]")
            return body

    def project_state(
        self,
        scope: MemoryScope,
        state: Mapping[str, Any],
        *,
        timeline_id: str = "",
    ) -> Path:
        path = self.perspective_root(scope, timeline_id) / "state" / "working-state.json"
        payload = {
            "schema": "smacx.campaign-working-state.v1",
            "scope": {
                "match_id": scope.match_id,
                "agent_id": scope.agent_id,
                "perspective_id": scope.perspective_id,
                "timeline_id": self.timeline_id(scope, timeline_id),
            },
            "projected_unix": time.time(),
            "state": dict(state),
        }
        with self._locked(self.root, shared=True):
            _atomic_json(path, payload)
        return path

    def notebook(
        self,
        scope: MemoryScope,
        action: str,
        *,
        collection: str = "notes",
        key: str = "",
        title: str = "",
        content: str = "",
        tags: list[str] | None = None,
        status: str = "active",
        turn: int | None = None,
        year: int | None = None,
        session_id: str | None = None,
        timeline_id: str = "",
    ) -> dict[str, Any]:
        if not COLLECTION.fullmatch(collection):
            raise JournalError("invalid_notebook_collection")
        base = self.perspective_root(scope, timeline_id) / "notebook" / collection
        replayed = self.replay(scope, timeline_id)
        entries = replayed.get("notebook", {}).get(collection, {})
        if not isinstance(entries, dict):
            entries = {}
        if action == "list":
            items = [dict(value) for value in entries.values()
                     if isinstance(value, dict) and value.get("status") != "deleted"][:500]
            return {"ok": True, "collection": collection, "items": items}
        key = _safe_key(key, "notebook_key")
        path = base / f"{key}.json"
        previous = entries.get(key, {})
        if action == "get":
            return {"ok": bool(previous), "collection": collection, "item": previous or None}
        if action not in {"put", "delete"}:
            raise JournalError("invalid_notebook_action")
        if action == "put":
            if not title.strip() or not content.strip():
                raise JournalError("notebook_title_and_content_required")
            if len(content.encode("utf-8")) > MAX_NOTE_BYTES:
                raise JournalError("notebook_content_too_large")
        revision = int(previous.get("revision") or 0) + 1
        item = {
            "schema": "smacx.notebook-entry.v1",
            "collection": collection,
            "key": key,
            "revision": revision,
            "title": title.strip()[:240] if action == "put" else str(previous.get("title") or key),
            "content": content.strip() if action == "put" else "",
            "tags": sorted({str(tag).strip()[:80] for tag in (tags or []) if str(tag).strip()})[:32],
            "status": status if action == "put" else "deleted",
            "turn": turn,
            "year": year,
            "updated_unix": time.time(),
        }
        event = self.append(
            scope, f"notebook.{action}", {"entry": item}, session_id=session_id,
            turn=turn, year=year, timeline_id=timeline_id,
        )
        # Human-readable entry files are a convenience projection. The event
        # above is the authority and can recreate this file after interruption.
        _atomic_json(path, item)
        return {"ok": True, "collection": collection, "item": item,
                "journal_event_id": event["event_id"]}

    def fork_timeline(
        self,
        scope: MemoryScope,
        new_timeline_id: str,
        *,
        native_save_sha256: str,
        from_event_hash: str = "",
    ) -> dict[str, Any]:
        new_timeline_id = _safe_identity(new_timeline_id, "timeline_id")
        if not re.fullmatch(r"[0-9a-f]{64}", native_save_sha256):
            raise JournalError("invalid_native_save_digest")
        path = self.perspective_root(scope, new_timeline_id)
        if path.exists():
            raise JournalError("timeline_already_exists")
        current = self._manifest(scope, "timeline-main")
        fork_hash = from_event_hash or str(current["head_hash"])
        # Refuse dangling branch points. A future rewind may choose any
        # checkpoint event in the parent, but the child must always have a
        # fully reconstructable ancestry before it is advertised.
        self._materialize_timeline(
            scope, "timeline-main", target_hash=fork_hash, visited=set(),
        )
        manifest = self._manifest(scope, new_timeline_id)
        manifest.update({
            "parent_timeline_id": current["timeline_id"],
            "forked_from_event_hash": fork_hash,
            "native_save_sha256": native_save_sha256,
        })
        _atomic_json(path / "manifest.json", manifest)
        self._update_catalog(scope, new_timeline_id, manifest)
        self._git_commit(scope.match_id, f"Fork timeline {new_timeline_id}")
        return manifest

    def verify(self, scope: MemoryScope, timeline_id: str = "") -> dict[str, Any]:
        path = self.perspective_root(scope, timeline_id)
        previous = "0" * 64
        count = 0
        errors: list[str] = []
        for event_path in sorted((path / "events").glob("*.json")) if (path / "events").is_dir() else []:
            event = self._load(event_path, None)
            if not isinstance(event, dict):
                errors.append(f"invalid_json:{event_path.name}")
                continue
            claimed = str(event.pop("event_hash", ""))
            actual = hashlib.sha256(previous.encode("ascii") + _canonical(event)).hexdigest()
            event["event_hash"] = claimed
            if event.get("previous_hash") != previous or claimed != actual:
                errors.append(f"hash_chain:{event_path.name}")
                break
            previous = claimed
            count += 1
        manifest = self._manifest(scope, timeline_id)
        if previous != manifest.get("head_hash") or count != int(manifest.get("sequence") or 0):
            errors.append("manifest_head_mismatch")
        return {"ok": not errors, "events": count, "head_hash": previous, "errors": errors}

    def events_after(
        self, scope: MemoryScope, event_id: str | None = None, *,
        timeline_id: str = "", limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read canonical events after one projector watermark in journal order."""
        path = self.perspective_root(scope, timeline_id) / "events"
        rows: list[dict[str, Any]] = []
        found = event_id is None
        for event_path in sorted(path.glob("*.json")) if path.is_dir() else []:
            event = self._load(event_path, None)
            if not isinstance(event, dict):
                continue
            if not found:
                found = event.get("event_id") == event_id
                continue
            event["created_unix"] = event.get("recorded_unix")
            event["source"] = "campaign_journal"
            rows.append(event)
            if len(rows) >= min(max(int(limit), 1), 500):
                break
        if event_id is not None and not found:
            raise JournalError("journal_projection_cursor_not_found")
        return rows

    def latest_events(
        self, scope: MemoryScope, *, timeline_id: str = "", limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read the newest canonical events without replaying the full history."""
        path = self.perspective_root(scope, timeline_id) / "events"
        result: list[dict[str, Any]] = []
        files = sorted(path.glob("*.json"), reverse=True) if path.is_dir() else []
        for event_path in files[:min(max(int(limit), 1), 500)]:
            event = self._load(event_path, None)
            if isinstance(event, dict):
                event["created_unix"] = event.get("recorded_unix")
                event["source"] = "campaign_journal"
                result.append(event)
        return result

    def replay(self, scope: MemoryScope, timeline_id: str = "") -> dict[str, Any]:
        """Materialize a portable cache seed using only canonical journal files."""
        path = self.perspective_root(scope, timeline_id)
        manifest = self._manifest(scope, timeline_id)
        cache_key = str(path)
        with self._cache_lock:
            cached = self._replay_cache.get(cache_key)
            if isinstance(cached, dict) \
                    and cached.get("manifest", {}).get("head_hash") == manifest.get("head_hash"):
                return copy.deepcopy(cached)
        verified = self.verify(scope, timeline_id)
        if not verified["ok"]:
            raise JournalError("journal_integrity_failed")
        state = self._materialize_timeline(
            scope, self.timeline_id(scope, timeline_id),
            target_hash=str(manifest.get("head_hash") or "0" * 64), visited=set(),
        )
        state["manifest"] = manifest
        with self._cache_lock:
            self._replay_cache[cache_key] = state
        return copy.deepcopy(state)

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "facts": {}, "claims": {}, "beliefs": {}, "relationships": {},
            "commitments": {}, "goals": {}, "summaries": {}, "notebook": {},
            "chat": {}, "recent_actions": [], "lifecycle": [],
        }

    def _materialize_timeline(
        self, scope: MemoryScope, timeline_id: str, *, target_hash: str,
        visited: set[str],
    ) -> dict[str, Any]:
        """Replay a timeline plus its immutable parent prefix to one event hash."""
        if timeline_id in visited:
            raise JournalError("journal_timeline_cycle")
        visited.add(timeline_id)
        manifest = self._manifest(scope, timeline_id)
        parent = manifest.get("parent_timeline_id")
        fork_hash = manifest.get("forked_from_event_hash")
        if parent:
            parent_id = _safe_identity(str(parent), "parent_timeline_id")
            if not isinstance(fork_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", fork_hash):
                raise JournalError("invalid_journal_fork_hash")
            state = self._materialize_timeline(
                scope, parent_id, target_hash=fork_hash, visited=visited,
            )
        else:
            state = self._empty_state()
        if target_hash == "0" * 64:
            visited.remove(timeline_id)
            return state
        path = self.perspective_root(scope, timeline_id)
        found = False
        for event_path in sorted((path / "events").glob("*.json")):
            event = self._load(event_path, {})
            if isinstance(event, dict):
                self._apply_event(state, event)
                if event.get("event_hash") == target_hash:
                    found = True
                    break
        visited.remove(timeline_id)
        if not found:
            raise JournalError("journal_timeline_target_not_found")
        return state

    @staticmethod
    def _apply_event(state: dict[str, Any], event: Mapping[str, Any]) -> None:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return
        kind = str(event.get("event_type") or "")
        if kind == "memory.fact":
            state["facts"][str(payload.get("key") or event["event_id"])] = payload
        elif kind.startswith("memory."):
            memory_kind = kind.split(".", 1)[1]
            memory_kind = {
                "claim": "claims", "belief": "beliefs",
                "relationship": "relationships", "commitment": "commitments",
                "goal": "goals", "summary": "summaries",
            }.get(memory_kind, memory_kind)
            record = payload.get("record")
            supplied = payload.get("record_input")
            if memory_kind in state and isinstance(record, dict):
                stable = next((str(record.get(name)) for name in (
                    "goal_key", "commitment_key", "actor_id", "section", "topic",
                ) if record.get(name) is not None), str(event["event_id"]))
                state[memory_kind][stable] = {
                    "input": supplied, "record": record,
                    "journal_event_id": event.get("event_id"),
                }
        elif kind.startswith("notebook."):
            entry = payload.get("entry")
            if isinstance(entry, dict):
                collection = str(entry.get("collection") or "notes")
                key = str(entry.get("key") or event["event_id"])
                state["notebook"].setdefault(collection, {})[key] = entry
        elif kind == "chat.message":
            state["chat"][str(payload.get("message_uid") or event["event_id"])] = payload
        elif kind == "game.action":
            state["recent_actions"].append(payload)
            state["recent_actions"] = state["recent_actions"][-100:]
        elif kind.startswith(("agent.", "checkpoint.", "incident.",
                              "lifecycle.", "recovery.")):
            state["lifecycle"].append({
                "event_type": kind,
                "journal_event_id": event.get("event_id"),
                "turn": event.get("turn"),
                "year": event.get("year"),
                "payload": payload,
            })
            state["lifecycle"] = state["lifecycle"][-100:]

    def working_state(
        self, scope: MemoryScope, *, timeline_id: str = "",
        token_budgets: Mapping[str, int] | None = None,
    ) -> dict[str, Any]:
        """Build the bounded strategic projection from canonical journal data."""
        replayed = self.replay(scope, timeline_id)

        def records(kind: str) -> list[dict[str, Any]]:
            values = replayed.get(kind, {})
            if not isinstance(values, Mapping):
                return []
            result = []
            for item in values.values():
                if not isinstance(item, Mapping):
                    continue
                record = item.get("record")
                result.append(dict(record) if isinstance(record, Mapping) else dict(item))
            return sorted(result, key=lambda item: float(
                item.get("updated_unix") or item.get("created_unix") or 0
            ), reverse=True)

        facts = []
        for item in replayed.get("facts", {}).values():
            if not isinstance(item, Mapping):
                continue
            record = item.get("record")
            facts.append(dict(record) if isinstance(record, Mapping) else dict(item))
        facts.sort(key=lambda item: float(
            item.get("updated_unix") or item.get("created_unix") or 0
        ), reverse=True)
        goals = sorted(records("goals"), key=lambda item: (
            -int(item.get("priority") or 0), -float(item.get("created_unix") or 0),
        ))[:100]
        commitments = records("commitments")[:100]
        relationships = records("relationships")[:100]
        summaries = records("summaries")
        chat = list(reversed(list(replayed.get("chat", {}).values())[-50:]))
        sections: dict[str, Any] = {
            "situation": {"summaries": summaries, "facts": facts[:200]},
            "relationships": relationships,
            "goals": goals,
            "commitments": commitments,
            "recent_events": list(reversed(replayed.get("recent_actions", [])[-50:])),
            "chat": chat,
        }
        budgets = {str(key): int(value) for key, value in (token_budgets or {}).items()}
        source_estimates = {
            name: max(1, (len(json.dumps(value, ensure_ascii=False,
                                         separators=(",", ":"))) + 3) // 4)
            for name, value in sections.items()
        }
        over_budget = [
            name for name, estimate in source_estimates.items()
            if estimate > budgets.get(name, estimate)
        ]

        def estimate(value: Any) -> int:
            return max(1, (len(json.dumps(
                value, ensure_ascii=False, separators=(",", ":"),
            )) + 3) // 4)

        # The raw journal is never pruned here. Only this provider-facing
        # materialization is shortened, newest/highest-priority first, so a
        # neglected memory section cannot silently consume the model window.
        for name, value in sections.items():
            budget = budgets.get(name)
            if not budget or estimate(value) <= budget:
                continue
            if isinstance(value, list):
                while len(value) > 1 and estimate(value) > budget:
                    value.pop()
            elif name == "situation" and isinstance(value, dict):
                facts_value = value.get("facts") if isinstance(value.get("facts"), list) else []
                summaries_value = value.get("summaries") \
                    if isinstance(value.get("summaries"), list) else []
                while estimate(value) > budget and (len(facts_value) > 1 or len(summaries_value) > 1):
                    target = facts_value if len(facts_value) >= len(summaries_value) \
                        and len(facts_value) > 1 else summaries_value
                    target.pop()
        estimates = {name: estimate(value) for name, value in sections.items()}
        return {
            "scope": {
                "match_id": scope.match_id, "agent_id": scope.agent_id,
                "perspective_id": scope.perspective_id,
                "timeline_id": self.timeline_id(scope, timeline_id),
            },
            "sections": sections,
            "token_estimates": estimates,
            "source_token_estimates": source_estimates,
            "token_budgets": budgets,
            "compaction_required": bool(over_budget),
            "compaction_required_sections": over_budget,
            "projection_truncated": bool(over_budget),
            "journal_head_hash": replayed["manifest"]["head_hash"],
        }

    def rebuild_sqlite_projection(
        self, scope: MemoryScope, target: Path, timeline_id: str = "",
    ) -> dict[str, Any]:
        """Recreate a disposable query cache solely from the journal."""
        state = self.replay(scope, timeline_id)
        target = target.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        connection = sqlite3.connect(temporary)
        try:
            connection.executescript("""
                PRAGMA journal_mode=DELETE;
                CREATE TABLE records(kind TEXT NOT NULL, stable_key TEXT NOT NULL,
                                     payload_json TEXT NOT NULL,
                                     PRIMARY KEY(kind, stable_key));
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
                CREATE VIRTUAL TABLE records_fts USING fts5(kind, stable_key, body);
            """)
            count = 0
            for kind in ("facts", "claims", "beliefs", "relationships", "commitments",
                         "goals", "summaries", "chat"):
                values = state.get(kind, {})
                if not isinstance(values, dict):
                    continue
                for stable_key, payload in values.items():
                    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":"))
                    connection.execute(
                        "INSERT INTO records(kind,stable_key,payload_json) VALUES(?,?,?)",
                        (kind, stable_key, encoded),
                    )
                    connection.execute(
                        "INSERT INTO records_fts(kind,stable_key,body) VALUES(?,?,?)",
                        (kind, stable_key, encoded),
                    )
                    count += 1
            for index, payload in enumerate(state.get("lifecycle", [])):
                key = f"{index:06d}:{payload.get('journal_event_id', '')}"
                encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"))
                connection.execute(
                    "INSERT INTO records(kind,stable_key,payload_json) VALUES('lifecycle',?,?)",
                    (key, encoded),
                )
                connection.execute(
                    "INSERT INTO records_fts(kind,stable_key,body) VALUES('lifecycle',?,?)",
                    (key, encoded),
                )
                count += 1
            for collection, entries in state.get("notebook", {}).items():
                for stable_key, payload in entries.items():
                    if not isinstance(payload, dict) or payload.get("status") == "deleted":
                        continue
                    key = f"{collection}:{stable_key}"
                    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":"))
                    connection.execute(
                        "INSERT INTO records(kind,stable_key,payload_json) VALUES('notebook',?,?)",
                        (key, encoded),
                    )
                    connection.execute(
                        "INSERT INTO records_fts(kind,stable_key,body) VALUES('notebook',?,?)",
                        (key, encoded),
                    )
                    count += 1
            connection.execute(
                "INSERT INTO metadata(key,value_json) VALUES('manifest',?)",
                (json.dumps(state["manifest"], separators=(",", ":")),),
            )
            connection.commit()
        finally:
            connection.close()
        os.replace(temporary, target)
        return {"ok": True, "target": str(target), "records": count,
                "head_hash": state["manifest"]["head_hash"]}

    def archive_to(self, target: Path) -> dict[str, Any]:
        """Write one consistent compressed snapshot of every campaign timeline."""
        target = target.expanduser().resolve()
        if target == self.root or self.root in target.parents:
            raise JournalError("campaign_archive_must_be_outside_campaign_root")
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", dir=target.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)

        def safe_member(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
            if info.name.endswith("/.journal.lock") \
                    or info.name == "campaigns/.journal.lock":
                return None
            if not (info.isfile() or info.isdir()):
                return None
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            return info

        try:
            with self._locked(self.root):
                with tarfile.open(temporary, "w:gz", compresslevel=6) as archive:
                    archive.add(
                        self.root, arcname="campaigns", recursive=True,
                        filter=safe_member,
                    )
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        return {
            "path": str(target), "sha256": digest,
            "size_bytes": target.stat().st_size,
        }

    def _update_catalog(self, scope: MemoryScope, timeline_id: str,
                        manifest: Mapping[str, Any]) -> None:
        match_root = self.root / scope.match_id
        path = match_root / "campaign.json"
        # Different agents have different perspective locks but share this
        # catalog. Serialize the read/modify/write so neither can erase the
        # other's timeline head.
        with self._locked(match_root):
            catalog = self._load(path, {})
            if not isinstance(catalog, dict):
                catalog = {}
            catalog.update({
                "schema": "smacx.campaign.v1", "match_id": scope.match_id,
                "updated_unix": time.time(),
            })
            perspectives = catalog.setdefault("perspectives", {})
            identity = f"{scope.agent_id}/{scope.perspective_id}"
            row = perspectives.setdefault(identity, {"timelines": {}})
            row["timelines"][timeline_id] = {
                "head_hash": manifest.get("head_hash"),
                "sequence": manifest.get("sequence"),
                "last_turn": manifest.get("last_turn"),
                "last_year": manifest.get("last_year"),
            }
            _atomic_json(path, catalog)

    def _git_commit(self, match_id: str, message: str) -> None:
        match_id = _safe_identity(match_id, "match_id")
        repository = self.root / match_id
        repository.mkdir(parents=True, exist_ok=True)
        environment = {
            **os.environ,
            "GIT_AUTHOR_NAME": "SMACX Agent",
            "GIT_AUTHOR_EMAIL": "smacx-agent@localhost",
            "GIT_COMMITTER_NAME": "SMACX Agent",
            "GIT_COMMITTER_EMAIL": "smacx-agent@localhost",
        }
        try:
            with self._locked(repository):
                if not (repository / ".git").is_dir():
                    subprocess.run(["git", "init", "--quiet"], cwd=repository,
                                   env=environment, check=True, timeout=10)
                ignore = repository / ".gitignore"
                if not ignore.exists():
                    ignore.write_text(".journal.lock\n**/.journal.lock\n*.sav\n*.sav.zst\n*.sqlite*\n",
                                      encoding="utf-8")
                subprocess.run(["git", "add", ".gitignore", "campaign.json", "perspectives"],
                               cwd=repository, env=environment, check=True, timeout=10)
                changed = subprocess.run(
                    ["git", "diff", "--cached", "--quiet"], cwd=repository,
                    env=environment, timeout=10,
                ).returncode != 0
                if changed:
                    subprocess.run(
                        ["git", "commit", "--quiet", "-m", message[:240]],
                        cwd=repository, env=environment, check=True, timeout=15,
                    )
        except (OSError, subprocess.SubprocessError) as exc:
            raise JournalError(f"journal_git_failed:{type(exc).__name__}") from exc


_instance: CampaignJournal | None = None


def campaign_journal() -> CampaignJournal:
    global _instance
    if _instance is None:
        _instance = CampaignJournal()
    return _instance
