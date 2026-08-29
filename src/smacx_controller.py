"""Linux-side controller for the SMACX in-process agent bridge."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time
from typing import Any, Mapping, Sequence
import re
import uuid

from smacx_store import MemoryScope, SmacxStore, StoreError


PROJECT = Path(__file__).resolve().parents[1]
RUNTIME = Path(os.environ.get("SMACX_RUNTIME_ROOT", PROJECT / "runtime"))
GAME = Path(os.environ.get("SMACX_GAME_PATH", RUNTIME / "game"))
TOKEN_FILE = Path(os.environ.get("SMACX_AGENT_TOKEN_FILE", RUNTIME / "agent-token"))
BRIDGE_HOST = os.environ.get("SMACX_BRIDGE_HOST", "127.0.0.1")
try:
    BRIDGE_PORT = int(os.environ.get("SMACX_BRIDGE_PORT", "47813"))
except ValueError as exc:
    raise RuntimeError("invalid_smacx_bridge_port") from exc
if not 1 <= BRIDGE_PORT <= 65535:
    raise RuntimeError("invalid_smacx_bridge_port")
STEAM_ROOT = Path.home() / ".local/share/Steam"
PRESSURE_VESSEL = STEAM_ROOT / "steamapps/common/SteamLinuxRuntime_4/run"
PROTON = STEAM_ROOT / "steamapps/common/Proton - Experimental/proton"
COMPAT_DATA = RUNTIME / "compatdata"
LOG_FILE = RUNTIME / "game-launch.log"
KNOWLEDGE_ROOT = Path(os.environ.get(
    "SMACX_LEGACY_KNOWLEDGE_ROOT",
    Path.home() / "Documents/ai/SidMeiers/games",
))
PLATFORM_DB_PATH = Path(os.environ.get(
    "SMACX_DB_PATH",
    Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    / "smacx-agent" / "smacx.sqlite3",
))
DEFAULT_AGENT_ID = os.environ.get("SMACX_AGENT_ID", "agent-default")
IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
SLOT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
KNOWLEDGE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
KNOWLEDGE_CATEGORY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
_knowledge_lock = threading.Lock()
_store_lock = threading.Lock()
_store_instance: SmacxStore | None = None
_store_instance_path: Path | None = None


class BridgeUnavailable(ConnectionError):
    pass


def _store() -> SmacxStore:
    global _store_instance, _store_instance_path
    path = PLATFORM_DB_PATH.expanduser().resolve()
    with _store_lock:
        if _store_instance is None or _store_instance_path != path:
            _store_instance = SmacxStore(path)
            _store_instance_path = path
        return _store_instance


def _token() -> str:
    return TOKEN_FILE.read_text(encoding="ascii").strip()


def bridge_request_to(host: str, port: int, token: str, operation: str,
                      timeout: float = 8.0, **arguments: Any) -> dict[str, Any]:
    if not isinstance(host, str) or not host or len(host) > 255 or "\x00" in host:
        raise BridgeUnavailable("Invalid explicit bridge host.")
    if not 1 <= int(port) <= 65535:
        raise BridgeUnavailable("Invalid explicit bridge port.")
    if not isinstance(token, str) or not token or len(token) > 4096 or "\x00" in token:
        raise BridgeUnavailable("Invalid explicit bridge token.")
    request = {"op": operation, "token": token, **arguments}
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as connection:
            connection.settimeout(timeout)
            connection.sendall(json.dumps(request, separators=(",", ":")).encode() + b"\n")
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    raise BridgeUnavailable("The game bridge closed the connection without a response.")
                newline = chunk.find(b"\n")
                if newline >= 0:
                    chunks.append(chunk[:newline])
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > 4_000_000:
                    raise BridgeUnavailable("The game bridge response exceeded the safety limit.")
        return json.loads(b"".join(chunks))
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeUnavailable(f"SMACX bridge is unavailable at {host}:{port}: {exc}") from exc


def bridge_request(operation: str, timeout: float = 8.0, **arguments: Any) -> dict[str, Any]:
    return bridge_request_to(
        BRIDGE_HOST, BRIDGE_PORT, _token(), operation, timeout=timeout, **arguments,
    )


def bridge_available() -> bool:
    try:
        return bool(bridge_request("ping", timeout=1.0).get("ok"))
    except BridgeUnavailable:
        return False


def _isolated_process_pids(game_only: bool = False) -> list[int]:
    """Return only processes belonging to this project's isolated game launch."""
    matches: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "cwd").resolve() != GAME.resolve():
                continue
            raw_command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            executable = raw_command.split(" ", 1)[0]
            is_game = Path(executable).name.lower() == "terranx.exe"
            is_wrapper = str(GAME / "thinker.exe") in raw_command
            if is_game or (is_wrapper and not game_only):
                matches.append(int(entry.name))
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return matches


def _game_pids() -> list[int]:
    return _isolated_process_pids(game_only=True)


def _terminate_isolated_game(wait_seconds: int = 8) -> dict[str, Any]:
    pids = _isolated_process_pids()
    if not pids:
        return {"ok": True, "stopped": False, "reason": "not_running"}
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + min(max(wait_seconds, 1), 15)
    while time.monotonic() < deadline:
        remaining = _isolated_process_pids()
        if not remaining:
            return {"ok": True, "stopped": True, "method": "isolated_process_sigterm", "pids": pids}
        time.sleep(0.25)
    return {"ok": False, "error": "process_close_timeout", "pids": _isolated_process_pids()}


def _read_match_manifest(match_id: str) -> dict[str, Any]:
    manifest_path = KNOWLEDGE_ROOT / match_id / "match.json"
    if not manifest_path.exists():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return manifest if isinstance(manifest, dict) and manifest.get("match_id") == match_id else {}


def _write_match_manifest(match_id: str, manifest: dict[str, Any]) -> Path:
    match_dir = KNOWLEDGE_ROOT / match_id
    match_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = match_dir / "match.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return match_dir


def _ensure_platform_identity(
    match_id: str,
    *,
    session_id: str | None = None,
    agent_id: str | None = None,
    perspective_id: str | None = None,
    instance_id: str | None = None,
    faction_id: int | None = None,
    faction_name: str | None = None,
    mode: str = "unknown",
    loaded_save: str | None = None,
    start_session: bool = False,
) -> dict[str, Any]:
    """Create or recover the durable platform identity for one native session."""
    if not IDENTITY_PATTERN.fullmatch(match_id):
        raise StoreError("invalid_match_id")
    manifest = _read_match_manifest(match_id)
    platform = manifest.get("platform_identity")
    if not isinstance(platform, dict):
        platform = {}
    agent_id = agent_id or str(platform.get("agent_id") or DEFAULT_AGENT_ID)
    perspective_id = perspective_id or str(platform.get("perspective_id") or "") or None
    instance_id = instance_id or None
    store = _store()
    store.ensure_agent(
        agent_id,
        os.environ.get("SMACX_AGENT_NAME", agent_id),
        profile_ref=os.environ.get("SMACX_HERMES_PROFILE") or None,
    )
    store.create_match(
        match_id=match_id,
        display_name=str(manifest.get("display_name") or match_id),
        mode=mode,
        metadata={"legacy_manifest_path": str(KNOWLEDGE_ROOT / match_id / "match.json")},
    )
    if perspective_id is None:
        candidates = [scope for scope in store.scopes_for_match(match_id, active_only=True) if scope.agent_id == agent_id]
        if len(candidates) == 1:
            perspective_id = candidates[0].perspective_id
        else:
            perspective_id = f"perspective-{uuid.uuid4().hex}"
    perspective = store.create_perspective(
        match_id,
        agent_id,
        perspective_id=perspective_id,
        faction_id=faction_id,
        faction_name=faction_name,
        controller_kind="agent",
    )
    scope = MemoryScope(match_id, agent_id, str(perspective["perspective_id"]))
    session_record: dict[str, Any] | None = None
    if session_id:
        existing_session = store.get_session(session_id)
        if existing_session:
            existing_scope = store.scope_for_session(session_id)
            if existing_scope != scope:
                raise StoreError("session_scope_mismatch")
            session_record = existing_session
            instance_id = str(existing_session["instance_id"])
        elif start_session:
            instance = store.register_instance(
                instance_id=instance_id,
                worker_kind=os.environ.get("SMACX_WORKER_KIND", "native-linux"),
                scope=scope,
                bridge_host=BRIDGE_HOST,
                bridge_port=BRIDGE_PORT,
                runtime_root=str(RUNTIME),
            )
            instance_id = str(instance["instance_id"])
            session_record = store.start_session(
                scope,
                instance_id,
                session_id=session_id,
                loaded_save=loaded_save,
            )
    platform_identity = {
        "installation_id": store.installation_id(),
        "match_id": match_id,
        "agent_id": agent_id,
        "perspective_id": scope.perspective_id,
        "instance_id": instance_id,
        "session_id": session_id,
        "graph_namespace": store.graph_namespace(scope),
        "database_path": str(store.path),
    }
    manifest.update({
        "match_id": match_id,
        "platform_identity": {
            "installation_id": platform_identity["installation_id"],
            "agent_id": agent_id,
            "perspective_id": scope.perspective_id,
        },
    })
    _write_match_manifest(match_id, manifest)
    return {
        "scope": scope,
        "session": session_record,
        "identity": platform_identity,
    }


def _scope_for_match(
    match_id: str,
    *,
    session_id: str | None = None,
    agent_id: str | None = None,
    perspective_id: str | None = None,
    create_legacy_session: bool = False,
) -> MemoryScope | None:
    store = _store()
    if session_id:
        scope = store.scope_for_session(session_id)
        if scope:
            if scope.match_id != match_id:
                raise StoreError("session_scope_mismatch")
            if agent_id and scope.agent_id != agent_id:
                raise StoreError("agent_scope_mismatch")
            if perspective_id and scope.perspective_id != perspective_id:
                raise StoreError("perspective_scope_mismatch")
            _import_legacy_knowledge_if_needed(scope)
            return scope
    scopes = store.scopes_for_match(match_id) if IDENTITY_PATTERN.fullmatch(match_id) else []
    filtered = [
        scope for scope in scopes
        if (not agent_id or scope.agent_id == agent_id)
        and (not perspective_id or scope.perspective_id == perspective_id)
    ]
    if len(filtered) == 1 and not (session_id and create_legacy_session):
        _import_legacy_knowledge_if_needed(filtered[0])
        return filtered[0]
    manifest = _read_match_manifest(match_id)
    if not manifest:
        return None
    context = _ensure_platform_identity(
        match_id,
        session_id=session_id,
        agent_id=agent_id,
        perspective_id=perspective_id,
        mode="legacy",
        start_session=bool(session_id and create_legacy_session),
    )
    scope = context["scope"]
    _import_legacy_knowledge_if_needed(scope)
    return scope


def _knowledge_path(match_id: str) -> Path | None:
    if not IDENTITY_PATTERN.fullmatch(match_id):
        return None
    manifest = _read_match_manifest(match_id)
    if manifest.get("match_id") != match_id:
        return None
    return KNOWLEDGE_ROOT / match_id / "knowledge.json"


def _read_knowledge_file(match_id: str) -> dict[str, Any]:
    path = _knowledge_path(match_id)
    if path is None or not path.exists():
        return {"version": 1, "match_id": match_id, "entries": {}, "history": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("version") != 1 \
    or data.get("match_id") != match_id \
    or not isinstance(data.get("entries"), dict) \
    or not isinstance(data.get("history"), list):
        return {}
    return data


def _import_legacy_knowledge_if_needed(scope: MemoryScope) -> dict[str, Any] | None:
    """One-way import from the prototype JSON ledger into the scoped store."""
    path = KNOWLEDGE_ROOT / scope.match_id / "knowledge.json"
    if not path.is_file() or _store().get_facts(scope, include_history=True, limit=1):
        return None
    try:
        content = path.read_bytes()
        ledger = json.loads(content)
        if not isinstance(ledger, dict):
            return None
        return _store().import_legacy_knowledge(
            scope,
            source_path=str(path.resolve()),
            content_sha256=hashlib.sha256(content).hexdigest(),
            ledger=ledger,
        )
    except (OSError, json.JSONDecodeError, StoreError):
        return None


def _write_knowledge_file(match_id: str, data: dict[str, Any]) -> Path:
    path = KNOWLEDGE_ROOT / match_id / "knowledge.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def _legacy_knowledge_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "knowledge_revision": int(row["fact_revision"]),
        "value": row["value"],
        "category": row["category"],
        "subject": row["subject"],
        "observed_turn": row.get("observed_turn"),
        "observed_year": row.get("observed_year"),
        "session_id": row.get("session_id"),
        "observed_revision": row.get("observed_revision"),
        "recorded_unix": row.get("created_unix"),
    }


def _export_legacy_knowledge(scope: MemoryScope) -> Path:
    """Write a compatibility mirror; SQLite remains authoritative."""
    rows = _store().get_facts(scope, include_history=True, limit=10000)
    entries: dict[str, Any] = {}
    history: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: (str(item["fact_key"]), int(item["fact_revision"]))):
        record = _legacy_knowledge_record(row)
        history.append({"key": row["fact_key"], **record})
        if row["status"] == "current":
            entries[str(row["fact_key"])] = record
    return _write_knowledge_file(scope.match_id, {
        "version": 1,
        "match_id": scope.match_id,
        "entries": entries,
        "history": history,
        "updated_unix": time.time(),
        "authoritative_store": str(_store().path),
    })


def _active_match_identity() -> dict[str, Any]:
    if not bridge_available():
        return {}
    status = bridge_request("status", timeout=2)
    identity = status.get("identity")
    return identity if isinstance(identity, dict) else {}


def read_match_knowledge(
    match_id: str,
    *,
    key: str = "",
    include_history: bool = False,
    agent_id: str = "",
    perspective_id: str = "",
) -> dict[str, Any]:
    path = _knowledge_path(match_id)
    if path is None:
        return {"ok": False, "error": "unknown_or_invalid_match_id"}
    active = _active_match_identity()
    if active and active.get("match_id") != match_id:
        return {
            "ok": False,
            "error": "wrong_active_match",
            "message": "Stop the running game or use its current match_id before reading match knowledge.",
            "current_match_id": active.get("match_id"),
        }
    if key and not KNOWLEDGE_KEY_PATTERN.fullmatch(key):
        return {"ok": False, "error": "invalid_knowledge_key"}
    try:
        scope = _scope_for_match(
            match_id,
            agent_id=agent_id or None,
            perspective_id=perspective_id or None,
        )
    except StoreError as exc:
        return {"ok": False, "error": str(exc)}
    if scope is None:
        with _knowledge_lock:
            data = _read_knowledge_file(match_id)
        if not data:
            return {"ok": False, "error": "invalid_knowledge_ledger"}
        entries = data["entries"]
        if key:
            entry = entries.get(key)
            if not isinstance(entry, dict):
                return {"ok": False, "error": "knowledge_key_not_found", "key": key}
            result: dict[str, Any] = {"ok": True, "match_id": match_id, "key": key, "entry": entry}
            if include_history:
                result["history"] = [
                    item for item in data["history"]
                    if isinstance(item, dict) and item.get("key") == key
                ]
            return result
        items = [
            {"key": stored_key, **entry}
            for stored_key, entry in sorted(entries.items())
            if isinstance(entry, dict)
        ]
        return {
            "ok": True, "match_id": match_id, "entries": items,
            "entry_count": len(items), "history_count": len(data["history"]),
            "storage": "legacy_json",
        }
    rows = _store().get_facts(scope, fact_key=key or None, include_history=include_history)
    if key:
        if not rows:
            return {"ok": False, "error": "knowledge_key_not_found", "key": key}
        current_rows = [row for row in rows if row["status"] == "current"]
        if not current_rows:
            return {"ok": False, "error": "knowledge_key_not_found", "key": key}
        result: dict[str, Any] = {
            "ok": True,
            "match_id": match_id,
            "agent_id": scope.agent_id,
            "perspective_id": scope.perspective_id,
            "key": key,
            "entry": _legacy_knowledge_record(current_rows[0]),
            "storage": "sqlite",
        }
        if include_history:
            result["history"] = [
                {"key": key, **_legacy_knowledge_record(row)}
                for row in reversed(rows)
            ]
        return result
    items = [
        {"key": row["fact_key"], **_legacy_knowledge_record(row)}
        for row in rows if row["status"] == "current"
    ]
    return {
        "ok": True,
        "match_id": match_id,
        "agent_id": scope.agent_id,
        "perspective_id": scope.perspective_id,
        "entries": items,
        "entry_count": len(items),
        "history_count": len(_store().get_facts(scope, include_history=True, limit=10000)),
        "storage": "sqlite",
    }


def put_match_knowledge(
    match_id: str,
    session_id: str,
    observed_revision: str,
    key: str,
    value: str,
    *,
    category: str = "general",
    subject: str = "",
    agent_id: str = "",
    perspective_id: str = "",
) -> dict[str, Any]:
    if _knowledge_path(match_id) is None:
        return {"ok": False, "error": "unknown_or_invalid_match_id"}
    if not IDENTITY_PATTERN.fullmatch(session_id) or not observed_revision:
        return {"ok": False, "error": "missing_knowledge_observation_guard"}
    if not KNOWLEDGE_KEY_PATTERN.fullmatch(key):
        return {"ok": False, "error": "invalid_knowledge_key"}
    if not KNOWLEDGE_CATEGORY_PATTERN.fullmatch(category):
        return {"ok": False, "error": "invalid_knowledge_category"}
    if not value.strip() or len(value) > 4000 or len(subject) > 160:
        return {"ok": False, "error": "invalid_knowledge_content"}
    try:
        envelope = bridge_request("semantic_snapshot", timeout=5)
    except BridgeUnavailable:
        return {
            "ok": False,
            "error": "game_not_connected",
            "message": "Knowledge writes require the running match and the exact observation being recorded.",
        }
    snapshot = envelope.get("snapshot")
    if not isinstance(snapshot, dict):
        return {"ok": False, "error": "game_not_in_semantic_match"}
    if snapshot.get("match_id") != match_id or snapshot.get("session_id") != session_id:
        return {
            "ok": False,
            "error": "wrong_game_identity",
            "current_match_id": snapshot.get("match_id"),
            "current_session_id": snapshot.get("session_id"),
        }
    if str(snapshot.get("revision", "")) != observed_revision:
        return {
            "ok": False,
            "error": "stale_knowledge_observation",
            "message": "Take a fresh snapshot before recording a newly learned fact.",
            "current_revision": snapshot.get("revision"),
        }
    try:
        scope = _scope_for_match(
            match_id,
            session_id=session_id,
            agent_id=agent_id or None,
            perspective_id=perspective_id or None,
            create_legacy_session=True,
        )
        if scope is None:
            return {"ok": False, "error": "unknown_or_invalid_match_id"}
        fact = _store().put_fact(
            scope,
            session_id,
            observed_revision,
            key,
            value,
            category=category,
            subject=subject,
            observed_turn=snapshot.get("turn"),
            observed_year=snapshot.get("year"),
        )
        with _knowledge_lock:
            path = _export_legacy_knowledge(scope)
    except StoreError as exc:
        return {"ok": False, "error": str(exc)}
    entry = _legacy_knowledge_record(fact)
    return {
        "ok": True,
        "match_id": match_id,
        "agent_id": scope.agent_id,
        "perspective_id": scope.perspective_id,
        "key": key,
        "entry": entry,
        "updated_existing": bool(fact["updated_existing"]),
        "ledger_path": str(path),
        "database_path": str(_store().path),
        "event_id": fact["event_id"],
    }


def _platform_scope_identity(scope: MemoryScope, session_id: str | None = None) -> dict[str, Any]:
    store = _store()
    return {
        "installation_id": store.installation_id(),
        "match_id": scope.match_id,
        "agent_id": scope.agent_id,
        "perspective_id": scope.perspective_id,
        "session_id": session_id,
        "graph_namespace": store.graph_namespace(scope),
    }


def _guard_platform_observation(
    match_id: str,
    session_id: str,
    observed_revision: str,
    *,
    agent_id: str = "",
    perspective_id: str = "",
) -> tuple[MemoryScope, dict[str, Any]]:
    if not IDENTITY_PATTERN.fullmatch(match_id) or not IDENTITY_PATTERN.fullmatch(session_id):
        raise StoreError("invalid_game_identity")
    if not observed_revision:
        raise StoreError("missing_memory_observation_guard")
    envelope = bridge_request("semantic_snapshot", timeout=5)
    snapshot = envelope.get("snapshot")
    if not isinstance(snapshot, dict):
        raise StoreError("game_not_in_semantic_match")
    if snapshot.get("match_id") != match_id or snapshot.get("session_id") != session_id:
        raise StoreError("wrong_game_identity")
    if str(snapshot.get("revision") or "") != observed_revision:
        raise StoreError("stale_memory_observation")
    scope = _scope_for_match(
        match_id,
        session_id=session_id,
        agent_id=agent_id or None,
        perspective_id=perspective_id or None,
        create_legacy_session=True,
    )
    if scope is None:
        raise StoreError("unknown_or_invalid_match_id")
    return scope, snapshot


def write_platform_memory(
    action: str,
    match_id: str,
    session_id: str,
    observed_revision: str,
    record: Mapping[str, Any],
    *,
    agent_id: str = "",
    perspective_id: str = "",
) -> dict[str, Any]:
    """Write one typed memory projection behind a fresh fair-play observation guard."""
    try:
        scope, snapshot = _guard_platform_observation(
            match_id,
            session_id,
            observed_revision,
            agent_id=agent_id,
            perspective_id=perspective_id,
        )
        store = _store()
        turn = snapshot.get("turn")
        year = snapshot.get("year")
        source_event_id = str(record.get("source_event_id") or "") or None
        if action == "claim":
            status = str(record.get("status") or "unverified")
            if status not in {"unverified", "corroborated", "disputed", "false", "retracted"}:
                raise StoreError("invalid_claim_status")
            stored = store.record_claim(
                scope,
                str(record.get("topic") or ""),
                str(record.get("content") or ""),
                session_id=session_id,
                asserted_by_actor_id=str(record.get("asserted_by_actor_id") or "") or None,
                about_actor_id=str(record.get("about_actor_id") or "") or None,
                confidence=float(record.get("confidence", 0.5)),
                status=status,
                source_event_id=source_event_id,
                turn=turn,
                year=year,
            )
        elif action == "belief":
            evidence_value = record.get("evidence", [])
            if not isinstance(evidence_value, list):
                raise StoreError("invalid_belief_evidence")
            evidence: list[tuple[str, str, float]] = []
            for item in evidence_value:
                if not isinstance(item, Mapping):
                    raise StoreError("invalid_belief_evidence")
                evidence.append((
                    str(item.get("event_id") or ""),
                    str(item.get("stance") or "supports"),
                    float(item.get("weight", 0.5)),
                ))
            stored = store.set_belief(
                scope,
                str(record.get("topic") or ""),
                str(record.get("content") or ""),
                confidence=float(record.get("confidence", 0.5)),
                evidence=evidence,
                session_id=session_id,
                turn=turn,
                year=year,
            )
        elif action == "relationship":
            reasons_value = record.get("reasons", [])
            if not isinstance(reasons_value, list):
                raise StoreError("invalid_relationship_reasons")
            stored = store.set_relationship(
                scope,
                str(record.get("actor_id") or ""),
                affinity=int(record.get("affinity", 0)),
                trust=int(record.get("trust", 0)),
                respect=int(record.get("respect", 0)),
                threat=int(record.get("threat", 0)),
                grievance=int(record.get("grievance", 0)),
                obligation=int(record.get("obligation", 0)),
                confidence=float(record.get("confidence", 0.5)),
                reasons=[str(reason) for reason in reasons_value],
                source_event_id=source_event_id,
                session_id=session_id,
                turn=turn,
                year=year,
            )
        elif action == "commitment":
            status = str(record.get("status") or "proposed")
            if status not in {"proposed", "accepted", "fulfilled", "broken", "expired", "cancelled"}:
                raise StoreError("invalid_commitment_status")
            parties_value = record.get("parties", [])
            if not isinstance(parties_value, list):
                raise StoreError("invalid_commitment_parties")
            parties: list[tuple[str, str]] = []
            for item in parties_value:
                if not isinstance(item, Mapping):
                    raise StoreError("invalid_commitment_parties")
                parties.append((str(item.get("actor_id") or ""), str(item.get("role") or "counterparty")))
            stored = store.put_commitment(
                scope,
                str(record.get("commitment_key") or ""),
                str(record.get("title") or ""),
                str(record.get("terms") or ""),
                status=status,
                parties=parties,
                due_turn=record.get("due_turn"),
                due_year=record.get("due_year"),
                source_event_id=source_event_id,
                resolution_event_id=str(record.get("resolution_event_id") or "") or None,
                session_id=session_id,
                turn=turn,
                year=year,
            )
        elif action == "goal":
            status = str(record.get("status") or "active")
            if status not in {"active", "paused", "completed", "abandoned"}:
                raise StoreError("invalid_goal_status")
            trigger = record.get("trigger")
            if trigger is not None and not isinstance(trigger, Mapping):
                raise StoreError("invalid_goal_trigger")
            stored = store.add_goal(
                scope,
                str(record.get("title") or ""),
                str(record.get("description") or ""),
                goal_key=str(record.get("goal_key") or "") or None,
                priority=int(record.get("priority", 50)),
                status=status,
                due_turn=record.get("due_turn"),
                due_year=record.get("due_year"),
                trigger=trigger,
                parent_goal_id=str(record.get("parent_goal_id") or "") or None,
                source_event_id=source_event_id,
                session_id=session_id,
                turn=turn,
                year=year,
            )
        elif action == "summary":
            section = str(record.get("section") or "")
            if section not in {"situation", "relationships", "goals", "commitments", "recent_events", "chat"}:
                raise StoreError("invalid_summary_section")
            stored = store.add_summary(
                scope,
                section,
                str(record.get("content") or ""),
                through_event_id=str(record.get("through_event_id") or "") or None,
                session_id=session_id,
                turn=turn,
                year=year,
            )
        else:
            return {"ok": False, "error": "invalid_memory_update_action"}
        return {
            "ok": True,
            "identity": _platform_scope_identity(scope, session_id),
            "action": action,
            "record": stored,
            "observed_revision": observed_revision,
            "observed_turn": turn,
            "observed_year": year,
        }
    except BridgeUnavailable:
        return {"ok": False, "error": "game_not_connected"}
    except (StoreError, TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def read_platform_memory(
    action: str,
    match_id: str,
    *,
    session_id: str = "",
    agent_id: str = "",
    perspective_id: str = "",
    query: str = "",
    document_kinds: Sequence[str] = (),
    queries: Sequence[Mapping[str, Any]] = (),
    total_token_budget: int = 2000,
    include_history: bool = False,
    unread_only: bool = False,
    acknowledge: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    """Read one allowlisted, perspective-scoped durable-memory view."""
    try:
        scope = _scope_for_match(
            match_id,
            session_id=session_id or None,
            agent_id=agent_id or None,
            perspective_id=perspective_id or None,
        )
        if scope is None:
            return {"ok": False, "error": "unknown_or_invalid_match_id"}
        store = _store()
        identity = _platform_scope_identity(scope, session_id or None)
        if action == "working_set":
            return {"ok": True, "identity": identity, "memory": store.current_memory(scope)}
        if action == "search":
            return {
                "ok": True,
                "identity": identity,
                "query": query,
                "results": store.search(scope, query, document_kinds=document_kinds, limit=limit),
            }
        if action == "recall":
            return {
                "ok": True,
                "identity": identity,
                "recall": store.recall_many(scope, queries, total_token_budget=total_token_budget),
            }
        if action == "chat":
            return {
                "ok": True,
                "identity": identity,
                "messages": store.list_chat(
                    scope,
                    unread_only=unread_only,
                    mark_acknowledged=acknowledge,
                    limit=limit,
                ),
                "untrusted_in_game_speech": True,
            }
        if action == "events":
            return {
                "ok": True,
                "identity": identity,
                "events": store.list_events(scope, limit=limit),
            }
        if action == "graph_status":
            return {
                "ok": True,
                "identity": identity,
                "projection": store.projection_cursor(scope, "graphiti-v1"),
                "sqlite_authoritative": True,
                "graphiti_optional": True,
            }
        projection = {
            "claims": "claims",
            "beliefs": "beliefs",
            "relationships": "relationships",
            "commitments": "commitments",
            "goals": "goals",
            "summaries": "summaries",
        }.get(action)
        if projection:
            return {
                "ok": True,
                "identity": identity,
                "records": store.list_projection_records(
                    scope, projection, include_history=include_history, limit=limit,
                ),
            }
        return {"ok": False, "error": "invalid_memory_action"}
    except StoreError as exc:
        return {"ok": False, "error": str(exc)}


def _persist_chat_envelope(
    result: dict[str, Any],
    *,
    match_id: str,
    session_id: str,
    agent_id: str = "",
    perspective_id: str = "",
) -> dict[str, Any]:
    """Persist native chat using only identity and participants visible to this seat."""
    if not result.get("ok"):
        return result
    try:
        scope = _scope_for_match(
            match_id,
            session_id=session_id,
            agent_id=agent_id or None,
            perspective_id=perspective_id or None,
            create_legacy_session=True,
        )
        if scope is None:
            return {**result, "durable_warning": "unknown_platform_scope"}
        store = _store()
        participants = result.get("participants")
        if not isinstance(participants, list):
            participants = []
        actor_by_faction: dict[int, dict[str, Any]] = {}
        for raw in participants:
            if not isinstance(raw, dict):
                continue
            try:
                faction_id = int(raw.get("faction_id"))
            except (TypeError, ValueError):
                continue
            player_id = str(raw.get("player_id") or "")
            player_name = str(raw.get("player_name") or "").strip()
            faction_name = str(raw.get("faction_name") or "").strip()
            stable_key = f"network:{player_id}" if player_id else f"faction:{faction_id}"
            actor_by_faction[faction_id] = store.upsert_actor(
                scope.match_id,
                stable_key,
                player_name or faction_name or f"Faction {faction_id}",
                controller_kind="agent" if raw.get("local") else "human",
                controller_ref=scope.agent_id if raw.get("local") else None,
                faction_id=faction_id,
                faction_name=faction_name or None,
                network_player_id=player_id or None,
                network_player_name=player_name or None,
                metadata={
                    "network_player_index": raw.get("network_player_index"),
                    "local": bool(raw.get("local")),
                },
            )

        raw_messages: list[dict[str, Any]] = []
        messages = result.get("messages")
        if isinstance(messages, list):
            raw_messages.extend(item for item in messages if isinstance(item, dict))
        event = result.get("event")
        if isinstance(event, dict):
            raw_messages.append(event)
        persisted: list[dict[str, Any]] = []
        for raw in raw_messages:
            direction = str(raw.get("direction") or "inbound")
            channel = str(raw.get("channel") or "received")
            sender_faction = raw.get("sender_faction_id")
            recipient_faction = raw.get("recipient_faction_id")
            try:
                sender_faction_id = int(sender_faction) if sender_faction is not None else None
                recipient_faction_id = int(recipient_faction) if recipient_faction is not None else None
            except (TypeError, ValueError):
                continue
            sequence = raw.get("sequence")
            client_message_id = str(raw.get("client_message_id") or "")
            if sequence is not None:
                message_uid = f"native:{session_id}:{sequence}"
            elif client_message_id:
                message_uid = f"client:{session_id}:{client_message_id}"
            else:
                continue
            sender_actor = actor_by_faction.get(sender_faction_id) if sender_faction_id is not None else None
            recipient_actor = actor_by_faction.get(recipient_faction_id) if recipient_faction_id is not None else None
            persisted.append(store.record_chat(
                scope,
                message_uid,
                str(raw.get("text") or ""),
                session_id=session_id,
                direction=direction,
                channel=channel,
                sender_actor_id=str(sender_actor["actor_id"]) if sender_actor else None,
                recipient_actor_id=str(recipient_actor["actor_id"]) if recipient_actor else None,
                sender_faction_id=sender_faction_id,
                recipient_faction_id=recipient_faction_id,
                turn=raw.get("turn"),
                metadata={
                    "native_sequence": sequence,
                    "client_message_id": client_message_id or None,
                    "sender_player_name": sender_actor.get("network_player_name") if sender_actor else None,
                    "sender_faction_name": sender_actor.get("faction_name") if sender_actor else None,
                },
            ))
        return {
            **result,
            "durable": {
                "identity": _platform_scope_identity(scope, session_id),
                "participants": list(actor_by_faction.values()),
                "messages_persisted": len(persisted),
                "database_path": str(store.path),
            },
        }
    except StoreError as exc:
        return {**result, "durable_warning": str(exc)}


def semantic_chat(
    action: str,
    *,
    match_id: str = "",
    session_id: str = "",
    client_message_id: str = "",
    text: str = "",
    recipient_faction_id: int = 0,
    after_sequence: int = 0,
    agent_id: str = "",
    perspective_id: str = "",
    acknowledge: bool = True,
) -> dict[str, Any]:
    """Call native chat, persist its fair-play envelope, and return durable attention."""
    result = bridge_request(
        "semantic_chat",
        action=action,
        match_id=match_id,
        session_id=session_id,
        client_message_id=client_message_id,
        text=text,
        recipient_faction_id=recipient_faction_id,
        after_sequence=max(0, after_sequence),
    )
    identity = result.get("identity")
    if isinstance(identity, dict):
        match_id = str(identity.get("match_id") or match_id)
        session_id = str(identity.get("session_id") or session_id)
    if not match_id or not session_id:
        return result
    result = _persist_chat_envelope(
        result,
        match_id=match_id,
        session_id=session_id,
        agent_id=agent_id,
        perspective_id=perspective_id,
    )
    durable = result.get("durable")
    if isinstance(durable, dict):
        attention = read_platform_memory(
            "chat",
            match_id,
            session_id=session_id,
            agent_id=agent_id,
            perspective_id=perspective_id,
            unread_only=True,
            acknowledge=acknowledge,
            limit=100,
        )
        if attention.get("ok"):
            durable["attention"] = attention.get("messages", [])
            durable["attention_acknowledged"] = acknowledge
            durable["untrusted_in_game_speech"] = True
    return result


def chat_attention(
    match_id: str,
    session_id: str,
    *,
    agent_id: str = "",
    perspective_id: str = "",
) -> dict[str, Any]:
    """Poll native chat and return each newly delivered message exactly once."""
    try:
        result = semantic_chat(
            "list",
            match_id=match_id,
            session_id=session_id,
            agent_id=agent_id,
            perspective_id=perspective_id,
            acknowledge=True,
        )
    except BridgeUnavailable as exc:
        return {"ok": False, "error": "game_not_connected", "message": str(exc)}
    durable = result.get("durable")
    messages = durable.get("attention", []) if isinstance(durable, dict) else []
    return {
        "ok": bool(result.get("ok")),
        "messages": messages,
        "participants": result.get("participants", []),
        "latest_sequence": result.get("latest_sequence"),
        "untrusted_in_game_speech": True,
    }


def launch_game(
    wait_seconds: int = 30,
    *,
    autostart: bool = False,
    difficulty: int = 0,
    world_size: int = 0,
    faction_id: int = 1,
    blind_research: bool = True,
    initial_research_priority: int = 1,
    initial_tech_id: int = -1,
    narrative_ui: bool = False,
    tutorial_ui: bool = False,
    match_id: str | None = None,
    session_id: str | None = None,
    agent_id: str | None = None,
    perspective_id: str | None = None,
    instance_id: str | None = None,
    startup_save: str | None = None,
) -> dict[str, Any]:
    if bridge_available():
        result: dict[str, Any] = {"ok": True, "launched": False, "reason": "already_running"}
    else:
        match_id = match_id or f"match-{uuid.uuid4().hex}"
        session_id = session_id or f"session-{uuid.uuid4().hex}"
        if not IDENTITY_PATTERN.fullmatch(match_id) or not IDENTITY_PATTERN.fullmatch(session_id):
            return {"ok": False, "error": "invalid_game_identity"}
        if autostart and startup_save:
            return {"ok": False, "error": "conflicting_startup_modes"}
        missing = [str(path) for path in (PRESSURE_VESSEL, PROTON, GAME / "thinker.exe", GAME / "thinker.dll") if not path.exists()]
        if missing:
            return {"ok": False, "error": "missing_runtime", "paths": missing}
        try:
            platform_context = _ensure_platform_identity(
                match_id,
                session_id=session_id,
                agent_id=agent_id,
                perspective_id=perspective_id,
                instance_id=instance_id,
                faction_id=faction_id if autostart else None,
                mode="singleplayer" if autostart else ("load" if startup_save else "interactive"),
                loaded_save=startup_save,
                start_session=True,
            )
        except StoreError as exc:
            return {"ok": False, "error": "platform_identity_error", "message": str(exc)}
        environment = os.environ.copy()
        environment.update(
            {
                "DISPLAY": environment.get("DISPLAY", ":0"),
                "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(STEAM_ROOT),
                "STEAM_COMPAT_DATA_PATH": str(COMPAT_DATA),
                "SMACX_AGENT_ENABLE": "1",
                "SMACX_AGENT_TOKEN": _token(),
                "SMACX_AGENT_PORT": str(BRIDGE_PORT),
                "SMACX_AGENT_MATCH_ID": match_id,
                "SMACX_AGENT_SESSION_ID": session_id,
            }
        )
        if autostart:
            environment.update(
                {
                    "SMACX_AGENT_AUTOSTART": "1",
                    "SMACX_AGENT_DIFFICULTY": str(min(max(difficulty, 0), 5)),
                    "SMACX_AGENT_WORLD_SIZE": str(min(max(world_size, 0), 4)),
                    "SMACX_AGENT_FACTION_ID": str(min(max(faction_id, 1), 7)),
                    "SMACX_AGENT_BLIND_RESEARCH": "1" if blind_research else "0",
                    "SMACX_AGENT_INITIAL_RESEARCH_PRIORITY": str(min(max(initial_research_priority, 0), 3)),
                    "SMACX_AGENT_NARRATIVE_UI": "1" if narrative_ui else "0",
                    "SMACX_AGENT_TUTORIAL_UI": "1" if tutorial_ui else "0",
                }
            )
            if not blind_research and initial_tech_id >= 0:
                environment["SMACX_AGENT_INITIAL_TECH_ID"] = str(initial_tech_id)
        command = [str(PRESSURE_VESSEL), "--", str(PROTON), "run", str(GAME / "thinker.exe"), "-windowed"]
        if startup_save:
            command.append(startup_save)
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("ab", buffering=0) as log:
            subprocess.Popen(
                command,
                cwd=GAME,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        deadline = time.monotonic() + min(max(wait_seconds, 1), 60)
        while time.monotonic() < deadline and not bridge_available():
            time.sleep(0.25)
        if not bridge_available():
            try:
                _store().close_session(session_id, status="launch_failed")
            except StoreError:
                pass
            return {"ok": False, "error": "launch_timeout", "log": str(LOG_FILE)}
        now = time.time()
        manifest = _read_match_manifest(match_id)
        sessions = manifest.get("sessions")
        if not isinstance(sessions, list):
            sessions = []
        session_record: dict[str, Any] = {
            "session_id": session_id,
            "started_unix": now,
            "status": "running",
        }
        if startup_save:
            session_record["loaded_save"] = startup_save
        sessions.append(session_record)
        manifest.update({
            "match_id": match_id,
            "current_session_id": session_id,
            "created_unix": manifest.get("created_unix", now),
            "last_started_unix": now,
            "status": "running",
            "sessions": sessions,
            "knowledge_scope": "Only record information legitimately observed by the player faction in this match.",
            "platform_identity": {
                key: platform_context["identity"][key]
                for key in ("installation_id", "agent_id", "perspective_id")
            },
        })
        if startup_save:
            manifest["last_loaded_save"] = startup_save
        match_dir = _write_match_manifest(match_id, manifest)
        result = {
            "ok": True,
            "launched": True,
            "bridge_port": BRIDGE_PORT,
            "identity": platform_context["identity"],
            "knowledge_directory": str(match_dir),
            "database_path": str(_store().path),
        }
    result["state"] = bridge_request("status")
    return result


def new_game(
    wait_seconds: int = 90,
    difficulty: int = 0,
    world_size: int = 0,
    faction_id: int = 1,
    blind_research: bool = True,
    initial_research_priority: int = 1,
    initial_tech_id: int = -1,
    narrative_ui: bool = False,
    tutorial_ui: bool = False,
    match_id: str | None = None,
    agent_id: str | None = None,
    perspective_id: str | None = None,
    instance_id: str | None = None,
) -> dict[str, Any]:
    if bridge_available():
        state = bridge_request("status")
        return {
            "ok": False,
            "error": "game_already_running",
            "message": "Stop the existing spectator game before starting a new one.",
            "state": state,
        }
    match_id = match_id or f"match-{uuid.uuid4().hex}"
    session_id = f"session-{uuid.uuid4().hex}"
    launched = launch_game(
        wait_seconds=min(max(wait_seconds, 5), 120),
        autostart=True,
        difficulty=difficulty,
        world_size=world_size,
        faction_id=faction_id,
        blind_research=blind_research,
        initial_research_priority=initial_research_priority,
        initial_tech_id=initial_tech_id,
        narrative_ui=narrative_ui,
        tutorial_ui=tutorial_ui,
        match_id=match_id,
        session_id=session_id,
        agent_id=agent_id,
        perspective_id=perspective_id,
        instance_id=instance_id,
    )
    if not launched.get("ok"):
        return launched
    deadline = time.monotonic() + min(max(wait_seconds, 5), 120)
    last: dict[str, Any] = launched.get("state", {})
    while time.monotonic() < deadline:
        try:
            last = bridge_request("semantic_snapshot")
        except BridgeUnavailable:
            time.sleep(0.5)
            continue
        snapshot = last.get("snapshot", {})
        if snapshot:
            return {
                "ok": True,
                "launched": True,
                "setup": {
                    "difficulty": difficulty,
                    "world_size": world_size,
                    "faction_id": faction_id,
                    "blind_research": blind_research,
                    "initial_research_priority": initial_research_priority if blind_research else None,
                    "initial_tech_id": initial_tech_id if not blind_research else None,
                    "narrative_ui": narrative_ui,
                    "tutorial_ui": tutorial_ui,
                    "input_method": "native_noninteractive_setup",
                },
                "identity": launched["identity"],
                "knowledge_directory": str(KNOWLEDGE_ROOT / match_id),
                "database_path": str(_store().path),
                "snapshot": last,
            }
        time.sleep(0.5)
    return {"ok": False, "error": "semantic_setup_timeout", "last_state": last, "log": str(LOG_FILE)}


def stop_game(wait_seconds: int = 10) -> dict[str, Any]:
    # Do not synthesize Ctrl+Q/Enter or send desktop-window commands.  A modal
    # game could consume those on the wrong screen.  This project owns an
    # isolated Proton launch, so terminate only processes whose /proc cwd is
    # the copied runtime/game directory.
    identity: dict[str, Any] = {}
    try:
        state = bridge_request("status", timeout=2)
        if isinstance(state.get("identity"), dict):
            identity = state["identity"]
    except BridgeUnavailable:
        pass
    result = _terminate_isolated_game(wait_seconds)
    match_id = identity.get("match_id")
    session_id = identity.get("session_id")
    if result.get("stopped") and isinstance(match_id, str) \
    and isinstance(session_id, str) and IDENTITY_PATTERN.fullmatch(match_id):
        manifest = _read_match_manifest(match_id)
        if manifest:
            now = time.time()
            manifest["status"] = "stopped"
            manifest["last_stopped_unix"] = now
            sessions = manifest.get("sessions", [])
            if isinstance(sessions, list):
                for session in reversed(sessions):
                    if isinstance(session, dict) and session.get("session_id") == session_id:
                        session["status"] = "stopped"
                        session["stopped_unix"] = now
                        break
            _write_match_manifest(match_id, manifest)
        try:
            scope = _store().scope_for_session(session_id)
            if scope:
                _store().append_event(
                    scope,
                    "lifecycle.session_stopped",
                    {"method": result.get("method"), "stopped": True},
                    session_id=session_id,
                    importance=40,
                    search_text="Native SMACX session stopped cleanly",
                )
                _store().close_session(session_id)
        except StoreError as exc:
            result["platform_warning"] = str(exc)
        result["identity"] = identity
    return result


def _save_path(match_id: str, slot: str) -> Path | None:
    if not IDENTITY_PATTERN.fullmatch(match_id) or not SLOT_PATTERN.fullmatch(slot):
        return None
    return GAME / "saves" / "agent" / match_id / f"{slot}.sav"


def list_saved_games(match_id: str) -> dict[str, Any]:
    if not IDENTITY_PATTERN.fullmatch(match_id):
        return {"ok": False, "error": "invalid_match_id"}
    directory = GAME / "saves" / "agent" / match_id
    saves: list[dict[str, Any]] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.sav")):
            if not SLOT_PATTERN.fullmatch(path.stem):
                continue
            stat = path.stat()
            saves.append({
                "slot": path.stem,
                "bytes": stat.st_size,
                "modified_unix": stat.st_mtime,
            })
    return {"ok": True, "match_id": match_id, "saves": saves}


def load_saved_game(
    match_id: str,
    slot: str,
    wait_seconds: int = 90,
    *,
    agent_id: str | None = None,
    perspective_id: str | None = None,
    instance_id: str | None = None,
) -> dict[str, Any]:
    path = _save_path(match_id, slot)
    if path is None:
        return {"ok": False, "error": "invalid_save_identity_or_slot"}
    manifest_path = KNOWLEDGE_ROOT / match_id / "match.json"
    if not manifest_path.exists():
        return {"ok": False, "error": "unknown_match_id"}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ok": False, "error": "invalid_match_manifest"}
    if manifest.get("match_id") != match_id:
        return {"ok": False, "error": "match_manifest_mismatch"}
    if not path.is_file():
        return {"ok": False, "error": "save_not_found", "slot": slot, "match_id": match_id}
    if bridge_available():
        return {
            "ok": False,
            "error": "game_already_running",
            "message": "Stop the current spectator game before loading; this prevents accidental loss of unsaved state.",
        }
    session_id = f"session-{uuid.uuid4().hex}"
    platform = manifest.get("platform_identity")
    if not isinstance(platform, dict):
        platform = {}
    relative_windows_path = f"saves\\agent\\{match_id}\\{slot}.sav"
    launched = launch_game(
        wait_seconds=min(max(wait_seconds, 5), 120),
        match_id=match_id,
        session_id=session_id,
        agent_id=agent_id or str(platform.get("agent_id") or "") or None,
        perspective_id=perspective_id or str(platform.get("perspective_id") or "") or None,
        instance_id=instance_id,
        startup_save=relative_windows_path,
    )
    if not launched.get("ok"):
        return launched
    deadline = time.monotonic() + min(max(wait_seconds, 5), 120)
    last: dict[str, Any] = launched.get("state", {})
    while time.monotonic() < deadline:
        try:
            last = bridge_request("semantic_snapshot")
        except BridgeUnavailable:
            time.sleep(0.5)
            continue
        snapshot = last.get("snapshot", {})
        if snapshot:
            return {
                "ok": True,
                "loaded": True,
                "slot": slot,
                "identity": launched["identity"],
                "knowledge_directory": str(KNOWLEDGE_ROOT / match_id),
                "database_path": str(_store().path),
                "snapshot": last,
            }
        time.sleep(0.5)
    return {"ok": False, "error": "semantic_load_timeout", "last_state": last, "log": str(LOG_FILE)}
