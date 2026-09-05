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

from smacx_game_settings import game_settings_environment, normalize_game_settings
from smacx_journal import CampaignJournal, JournalError
from smacx_store import MemoryScope, SmacxStore, StoreError
from smacx_reference import read_reference as read_reference_store
from smacx_attention import AttentionService
from smacx_observation import ObservationCollector
from smacx_world import WorldService
from smacx_world_store import WorldStore


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
COMPAT_DATA = Path(os.environ.get("SMACX_COMPAT_DATA", RUNTIME / "compatdata"))
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
GAME_SOURCE_ID = os.environ.get("SMACX_GAME_SOURCE_ID", "")
IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
SLOT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
KNOWLEDGE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
KNOWLEDGE_CATEGORY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
_knowledge_lock = threading.Lock()
_store_lock = threading.Lock()
_store_instance: SmacxStore | None = None
_store_instance_path: Path | None = None
_journal_instance: CampaignJournal | None = None
_journal_instance_root: Path | None = None
_observation_collectors: dict[tuple[str, str, str], ObservationCollector] = {}
_observation_collectors_lock = threading.Lock()


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


def _journal() -> CampaignJournal:
    """Keep the portable campaign authority beside the active platform store."""
    global _journal_instance, _journal_instance_root
    root = (_store().path.parent / "campaigns").resolve()
    with _store_lock:
        if _journal_instance is None or _journal_instance_root != root:
            _journal_instance = CampaignJournal(root)
            _journal_instance_root = root
        return _journal_instance


def _journal_working_state(scope: MemoryScope) -> dict[str, Any]:
    store = _store()
    with store.transaction() as connection:
        budgets = {
            str(row["section"]): int(row["max_tokens"])
            for row in connection.execute("SELECT section, max_tokens FROM memory_budgets")
        }
    return _journal().working_state(scope, token_budgets=budgets)


def world_service(
    match_id: str, *, session_id: str = "", agent_id: str = "",
    perspective_id: str = "",
) -> tuple[MemoryScope, WorldService, AttentionService]:
    """Resolve one exact seat's world/attention services without widening scope."""
    scope = _scope_for_match(
        match_id, session_id=session_id or None, agent_id=agent_id or None,
        perspective_id=perspective_id or None,
    )
    if scope is None:
        raise StoreError("unknown_or_invalid_match_id")
    world_store = WorldStore(_store())
    return scope, WorldService(world_store, scope), AttentionService(_store(), _journal(), scope)


def collect_perspective_world(
    match_id: str, session_id: str, *, agent_id: str = "", perspective_id: str = "",
    background: bool = False,
) -> dict[str, Any]:
    """Start or run the external observer; it never executes on the native UI stack."""
    scope = _scope_for_match(
        match_id, session_id=session_id, agent_id=agent_id or None,
        perspective_id=perspective_id or None, create_legacy_session=True,
    )
    if scope is None:
        raise StoreError("unknown_or_invalid_match_id")
    key = (scope.match_id, scope.agent_id, scope.perspective_id)
    with _observation_collectors_lock:
        collector = _observation_collectors.get(key)
        if collector is None or collector.session_id != session_id:
            if collector is not None:
                collector.stop()
            collector = ObservationCollector(
                scope=scope, session_id=session_id, bridge_call=bridge_request,
                journal=_journal(), world_store=WorldStore(_store()),
                attention=AttentionService(_store(), _journal(), scope),
                chat_capture=lambda: chat_attention(
                    match_id, session_id, agent_id=scope.agent_id,
                    perspective_id=scope.perspective_id,
                ),
            )
            _observation_collectors[key] = collector
        if background:
            collector.start()
            return {"ok": True, "background": True, "scope": {
                "match_id": scope.match_id, "agent_id": scope.agent_id,
                "perspective_id": scope.perspective_id,
            }}
    return collector.collect_once()


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
            _journal().append(
                scope, "lifecycle.session_started", {
                    "instance_id": instance_id, "worker_kind": "legacy",
                    "loaded_save": loaded_save,
                }, session_id=session_id, commit_reason="Start native session",
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
    if manifest.get("match_id") == match_id:
        return KNOWLEDGE_ROOT / match_id / "knowledge.json"
    # Managed workers register their authoritative match and perspective in the
    # shared platform store.  They do not need a legacy match.json mirror in the
    # per-worker volume before durable knowledge can be used.
    try:
        if _store().scopes_for_match(match_id):
            return KNOWLEDGE_ROOT / match_id / "knowledge.json"
    except StoreError:
        pass
    return None


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
    path.parent.mkdir(parents=True, exist_ok=True)
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
    """Write a compatibility mirror; the campaign journal remains authoritative."""
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
            "authority": "campaign_journal",
            "storage": "sqlite_query_projection",
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
        "authority": "campaign_journal",
        "storage": "sqlite_query_projection",
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
        journal = _journal()
        journal_event = journal.append(
            scope, "memory.fact", {
                "operation": "put", "key": key, "category": category,
                "subject": subject, "value": value, "record": fact,
                "observed_revision": observed_revision,
            }, session_id=session_id, turn=snapshot.get("turn"), year=snapshot.get("year"),
        )
        journal.project_state(scope, _journal_working_state(scope))
    except (StoreError, JournalError) as exc:
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
        "journal_event_id": journal_event["event_id"],
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
        encoded_record = json.dumps(record, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"))
        mechanical_keys = {
            "tiles", "units", "bases", "snapshot", "world", "net_deltas",
            "action_revision", "world_revision", "observation_cursor", "fields",
            "epistemic_status", "provenance_ref", "ready_unit_refs",
        }
        seen_keys: list[str] = []

        def inspect_memory_shape(value: object) -> None:
            if isinstance(value, Mapping):
                for key, child in value.items():
                    seen_keys.append(str(key))
                    inspect_memory_shape(child)
            elif isinstance(value, list):
                for child in value[:512]:
                    inspect_memory_shape(child)

        inspect_memory_shape(record)
        mechanical_hits = sum(key in mechanical_keys for key in seen_keys)
        hygiene = "clean"
        if len(encoded_record) > 8_000 and mechanical_hits >= 4:
            WorldStore(store).telemetry(
                "cognition_hygiene", "rejected_mechanical_copy", len(encoded_record),
                scope=scope, timeline_id=store.active_timeline_id(scope),
                dimensions={"action": action, "mechanical_key_hits": mechanical_hits},
            )
            raise StoreError(
                "mechanical_world_copy_rejected_use_world_references_and_interpretation"
            )
        if len(encoded_record) > 3_000 and mechanical_hits:
            hygiene = "flagged_possible_mechanical_duplication"
            WorldStore(store).telemetry(
                "cognition_hygiene", "flagged_possible_copy", len(encoded_record),
                scope=scope, timeline_id=store.active_timeline_id(scope),
                dimensions={"action": action, "mechanical_key_hits": mechanical_hits},
            )
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
        elif action == "plan":
            status = str(record.get("status") or "active")
            sequence_fields = (
                "target_refs", "participants", "dependencies", "contingencies",
                "linked_commitments", "contradictory_evidence",
            )
            for field in sequence_fields:
                if not isinstance(record.get(field, []), list):
                    raise StoreError(f"invalid_plan_{field}")
            for field in ("timing", "last_confirmation"):
                if not isinstance(record.get(field, {}), Mapping):
                    raise StoreError(f"invalid_plan_{field}")
            stored = store.put_plan(
                scope, str(record.get("plan_key") or ""),
                str(record.get("title") or ""), str(record.get("objective") or ""),
                status=status, target_refs=[str(value) for value in record.get("target_refs", [])],
                participants=[dict(value) for value in record.get("participants", [])
                              if isinstance(value, Mapping)],
                timing=dict(record.get("timing", {})),
                dependencies=[str(value) for value in record.get("dependencies", [])],
                intended_role=str(record.get("intended_role") or ""),
                contingencies=[str(value) for value in record.get("contingencies", [])],
                last_confirmation=dict(record.get("last_confirmation", {})),
                linked_commitments=[str(value) for value in record.get("linked_commitments", [])],
                contradictory_evidence=[str(value) for value in record.get("contradictory_evidence", [])],
                source_event_id=source_event_id, session_id=session_id, turn=turn, year=year,
            )
        elif action == "summary":
            section = str(record.get("section") or "")
            if section not in {"situation", "relationships", "goals", "plans", "commitments", "recent_events", "chat"}:
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
        journal = _journal()
        journal_event = journal.append(
            scope, f"memory.{action}", {
                "operation": "upsert", "record_input": dict(record),
                "record": stored, "observed_revision": observed_revision,
            }, session_id=session_id, turn=turn, year=year,
        )
        journal.project_state(scope, _journal_working_state(scope))
        return {
            "ok": True,
            "identity": _platform_scope_identity(scope, session_id),
            "action": action,
            "record": stored,
            "observed_revision": observed_revision,
            "observed_turn": turn,
            "observed_year": year,
            "journal_event_id": journal_event["event_id"],
            "cognition_hygiene": hygiene,
        }
    except BridgeUnavailable:
        return {"ok": False, "error": "game_not_connected"}
    except (StoreError, JournalError, TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def campaign_notebook(
    action: str,
    match_id: str,
    *,
    collection: str = "notes",
    key: str = "",
    title: str = "",
    content: str = "",
    tags: Sequence[str] = (),
    status: str = "active",
    session_id: str = "",
    observed_revision: str = "",
    agent_id: str = "",
    perspective_id: str = "",
    cursor: str = "",
    limit: int = 24,
    query: str = "",
) -> dict[str, Any]:
    """Read or mutate the canonical match-scoped AI notebook."""
    try:
        snapshot: dict[str, Any] = {}
        if action in {"put", "delete"}:
            scope, snapshot = _guard_platform_observation(
                match_id, session_id, observed_revision,
                agent_id=agent_id, perspective_id=perspective_id,
            )
        else:
            scope = _scope_for_match(
                match_id, session_id=session_id or None,
                agent_id=agent_id or None, perspective_id=perspective_id or None,
            )
            if scope is None:
                raise StoreError("unknown_or_invalid_match_id")
        result = _journal().notebook(
                scope, action, collection=collection, key=key, title=title,
                content=content, tags=list(tags), status=status,
                turn=snapshot.get("turn"), year=snapshot.get("year"),
                session_id=session_id or None,
                cursor=cursor, limit=limit, query=query,
            )
        if action in {"put", "delete"} and result.get("ok"):
            _store().append_event(
                scope, f"notebook.{action}", {"entry": result.get("item")},
                session_id=session_id, turn=snapshot.get("turn"), year=snapshot.get("year"),
                importance=65,
                search_text=f"{title} {content}".strip(),
            )
            _journal().project_state(scope, _journal_working_state(scope))
        return {
            **result,
            "identity": _platform_scope_identity(scope, session_id or None),
            "authority": "campaign_journal",
        }
    except (BridgeUnavailable, StoreError, JournalError, TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def record_campaign_action(
    match_id: str,
    session_id: str,
    payload: Mapping[str, Any],
    *,
    turn: int | None = None,
    year: int | None = None,
    commit_reason: str = "",
    agent_id: str = "",
    perspective_id: str = "",
) -> dict[str, Any]:
    """Journal one completed native decision without exposing journal paths to the model."""
    try:
        scope = _scope_for_match(
            match_id, session_id=session_id,
            agent_id=agent_id or None, perspective_id=perspective_id or None,
        )
        if scope is None:
            raise StoreError("unknown_or_invalid_match_id")
        journal = _journal()
        event = journal.append(
            scope, "game.action", payload, session_id=session_id,
            turn=turn, year=year, commit_reason=commit_reason,
        )
        journal.project_state(scope, _journal_working_state(scope))
        return {"ok": True, "journal_event_id": event["event_id"],
                "event_hash": event["event_hash"]}
    except (StoreError, JournalError, TypeError, ValueError) as exc:
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
    cursor: str = "",
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
        journal = _journal()
        identity = _platform_scope_identity(scope, session_id or None)
        if action == "working_set":
            memory = _journal_working_state(scope)
            journal.project_state(scope, memory)
            return {"ok": True, "identity": identity, "memory": memory,
                    "authority": "campaign_journal", "sqlite_role": "query_projection"}
        try:
            offset = int(cursor.removeprefix("offset-")) if cursor else 0
        except ValueError as exc:
            raise StoreError("invalid_memory_cursor") from exc
        if offset < 0 or offset > 1_000_000:
            raise StoreError("invalid_memory_cursor")

        def bounded_page(values: Sequence[Mapping[str, Any]], *, ceiling: int = 2048,
                         page_limit: int = 24) -> dict[str, Any]:
            rows = [dict(item) for item in values]
            selected: list[dict[str, Any]] = []
            maximum = min(max(int(limit), 1), page_limit)
            for item in rows[offset:offset + maximum]:
                candidate = [*selected, item]
                if max(1, (len(json.dumps(candidate, ensure_ascii=False,
                                          separators=(",", ":"))) + 3) // 4) > ceiling:
                    break
                selected.append(item)
            consumed = offset + len(selected)
            return {
                "items": selected, "cursor": cursor or None,
                "next_cursor": f"offset-{consumed}" if consumed < len(rows) else None,
                "result_token_ceiling": ceiling,
                "truncated": consumed < len(rows), "total_count": len(rows),
            }

        def provider_safe(value: Any) -> Any:
            if isinstance(value, Mapping):
                return {str(key): provider_safe(item) for key, item in value.items()
                        if not str(key).startswith("native_") and str(key) not in
                        {"engine_id", "hidden_id", "subject_a", "subject_b"}}
            if isinstance(value, list):
                return [provider_safe(item) for item in value]
            return value

        def safe_search_results(*, search_query: str,
                                kinds: Sequence[str], search_limit: int) -> list[dict[str, Any]]:
            """Sanitize journal search bodies before any provider-visible rendering.

            CampaignJournal.search is an internal reconstruction/query primitive and
            intentionally retains diagnostic fields.  Managed provider reads must
            never render those fields, even in an abstract.
            """
            results: list[dict[str, Any]] = []
            for item in journal.search(
                    scope, search_query, document_kinds=tuple(kinds),
                    limit=min(max(int(search_limit), 1), 100)):
                try:
                    parsed = json.loads(str(item.get("body") or "{}"))
                except json.JSONDecodeError:
                    parsed = {"summary": str(item.get("body") or "")}
                safe_body = provider_safe(parsed)
                rendered = json.dumps(
                    safe_body, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                )
                results.append({
                    key: item.get(key) for key in (
                        "document_id", "document_kind", "source_id", "title", "tags",
                        "importance", "created_unix", "rank", "authority",
                    ) if item.get(key) is not None
                } | {"body": rendered})
            return results

        if action == "search":
            raw_results = safe_search_results(
                search_query=query, kinds=document_kinds, search_limit=100,
            )
            summaries = []
            for item in raw_results:
                body = " ".join(str(item.get("body") or "").split())
                summaries.append({key: item.get(key) for key in (
                    "document_id", "document_kind", "source_id", "title", "tags",
                    "importance", "created_unix", "rank", "authority",
                ) if item.get(key) is not None} | {
                    "abstract": body[:237] + "..." if len(body) > 240 else body,
                })
            return {
                "ok": True,
                "identity": identity,
                "query": query,
                **bounded_page(summaries, ceiling=2048, page_limit=24),
                "authority": "campaign_journal",
            }
        if action == "recall":
            if not queries or len(queries) > 12:
                raise StoreError("invalid_recall_query_count")
            budget = min(max(int(total_token_budget), 128), 12000)
            used = 0
            seen: set[str] = set()
            groups: list[dict[str, Any]] = []
            truncated = False
            for request in queries:
                requested_kinds = request.get("document_kinds", ())
                normalized_kinds = tuple(str(item) for item in requested_kinds) \
                    if isinstance(requested_kinds, (list, tuple)) else ()
                matches: list[dict[str, Any]] = []
                for result in safe_search_results(
                        search_query=str(request.get("query") or ""),
                        kinds=normalized_kinds,
                        search_limit=int(request.get("limit", 10))):
                    document_id = str(result.get("document_id") or "")
                    if document_id in seen:
                        continue
                    estimate = max(1, (len(str(result.get("title") or ""))
                                       + len(str(result.get("body") or "")) + 3) // 4)
                    if used + estimate > budget:
                        truncated = True
                        break
                    seen.add(document_id)
                    used += estimate
                    matches.append(result)
                groups.append({
                    "query": str(request.get("query") or ""),
                    "matches": matches,
                })
                if truncated:
                    break
            return {
                "ok": True,
                "identity": identity,
                "recall": {
                    "scope": {
                        "match_id": scope.match_id, "agent_id": scope.agent_id,
                        "perspective_id": scope.perspective_id,
                        "timeline_id": journal.timeline_id(scope),
                    },
                    "groups": groups, "estimated_tokens": used,
                    "token_budget": budget, "truncated": truncated,
                    "authority": "campaign_journal",
                },
                "authority": "campaign_journal",
            }
        if action == "chat":
            values = journal.chat_messages(
                scope, unread_only=unread_only, acknowledge=acknowledge, limit=500,
            )
            return {
                "ok": True,
                "identity": identity,
                **bounded_page([provider_safe(item) for item in values],
                               ceiling=2048, page_limit=32),
                "untrusted_in_game_speech": True,
                "authority": "campaign_journal",
            }
        if action == "events":
            raw_events = journal.latest_events(scope, limit=500)
            from smacx_world_store import WorldStore
            committed_cursor = WorldStore(store).committed_cursor(scope, store.active_timeline_id(scope))
            safe_events = []
            for event in raw_events:
                if not WorldStore.event_visible(event, committed_cursor):
                    continue
                event_type = str(event.get("event_type") or "")
                if event_type == "observation.native_event":
                    continue
                compact = provider_safe(event)
                payload = compact.get("payload") if isinstance(compact.get("payload"), Mapping) else {}
                safe_events.append({
                    "event_id": compact.get("event_id"), "event_type": event_type,
                    "turn": compact.get("turn"), "year": compact.get("year"),
                    "recorded_unix": compact.get("recorded_unix"),
                    "payload": payload,
                })
            return {
                "ok": True,
                "identity": identity,
                **bounded_page(safe_events, ceiling=2048, page_limit=32),
                "authority": "campaign_journal",
            }
        if action == "graph_status":
            return {
                "ok": True,
                "identity": identity,
                "projection": store.projection_cursor(scope, "graphiti-v1"),
                "journal_authoritative": True,
                "sqlite_role": "projection_cursor_cache",
                "graphiti_optional": True,
            }
        projection = {
            "claims": "claims",
            "beliefs": "beliefs",
            "relationships": "relationships",
            "commitments": "commitments",
            "goals": "goals",
            "plans": "plans",
            "summaries": "summaries",
        }.get(action)
        if projection:
            values = journal.projection_records(scope, projection, limit=1000)
            return {
                "ok": True,
                "identity": identity,
                **bounded_page([provider_safe(item) for item in values],
                               ceiling=2048, page_limit=32),
                "authority": "campaign_journal",
                "history_mode": "active_timeline_current_projection",
                "include_history_requested": bool(include_history),
            }
        return {"ok": False, "error": "invalid_memory_action"}
    except StoreError as exc:
        return {"ok": False, "error": str(exc)}


def read_game_reference(action: str, *, query: str = "", topic: str = "",
                        document_id: str = "", collection_id: str = "", limit: int = 8,
                        include_body: bool = False, include_documents: bool = False,
                        entity_kind: str = "",
                        entity_key: str = "", entities: list[dict[str, str]] | None = None,
                        ruleset_id: str = "smacx", max_content_tokens: int | None = None,
                        max_query_tokens: int = 1_024,
                        continuation: str = "") -> dict[str, Any]:
    """Read global mechanics knowledge; it contains no match-hidden state."""
    try:
        return read_reference_store(
            _store(), action, query=query, topic=topic, document_id=document_id,
            collection_id=collection_id, limit=limit, include_body=include_body,
            include_documents=include_documents,
            private_prefix=(f"private.{GAME_SOURCE_ID}." if GAME_SOURCE_ID else None),
            entity_kind=entity_kind, entity_key=entity_key, entities=entities,
            ruleset_id=ruleset_id, max_content_tokens=max_content_tokens,
            max_query_tokens=max_query_tokens, continuation=continuation,
        )
    except (StoreError, JournalError, ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}


def match_briefing_context(match_id: str, session_id: str) -> dict[str, Any]:
    """Return control-plane context visible to this exact managed seat."""
    try:
        scope = _scope_for_match(match_id, session_id=session_id)
        if scope is None:
            raise StoreError("unknown_match_scope")
        store = _store()
        with store.transaction() as connection:
            match = connection.execute(
                "SELECT display_name, mode, ruleset_id, status, metadata_json FROM matches "
                "WHERE match_id=?", (match_id,),
            ).fetchone()
            seat = connection.execute(
                "SELECT seat_index, controller_kind, faction_id, faction_name, instance_id, "
                "metadata_json FROM seat_assignments WHERE match_id=? AND agent_id=? "
                "AND perspective_id=?",
                (match_id, scope.agent_id, scope.perspective_id),
            ).fetchone()
            worker = connection.execute(
                "SELECT game_source_id, autostart_json FROM worker_specs WHERE instance_id=?",
                (seat["instance_id"],),
            ).fetchone() if seat and seat["instance_id"] else None
            source = connection.execute(
                "SELECT display_name, executable_sha256 FROM game_sources WHERE game_source_id=?",
                (worker["game_source_id"],),
            ).fetchone() if worker else None
        if not match or not seat:
            raise StoreError("match_briefing_context_incomplete")
        match_metadata = json.loads(str(match["metadata_json"]))
        requested_settings = match_metadata.get("game_settings")
        if requested_settings is None and worker:
            autostart = json.loads(str(worker["autostart_json"]))
            requested_settings = autostart.get("game_settings")
        reference = read_reference_store(store, "topics")
        return {
            "ok": True,
            "scope": _platform_scope_identity(scope, session_id),
            "match": {
                "display_name": match["display_name"], "mode": match["mode"],
                "ruleset_id": match["ruleset_id"], "status": match["status"],
            },
            "seat": {
                "seat_index": int(seat["seat_index"]),
                "controller_kind": seat["controller_kind"],
                "assigned_faction_id": seat["faction_id"],
                "assigned_faction_name": seat["faction_name"],
            },
            "policy": {
                key: match_metadata[key]
                for key in (
                    "host_controller_kind", "graphiti_enabled", "lan_profile",
                    "scenario_id", "ranking_mode", "managed_clients_only",
                ) if key in match_metadata
            },
            "requested_settings": requested_settings,
            "game_source": ({
                "game_source_id": worker["game_source_id"],
                "display_name": source["display_name"] if source else None,
                "executable_sha256": source["executable_sha256"] if source else None,
            } if worker else None),
            "reference_topics": reference.get("topics", []) if reference.get("ok") else [],
            "reference_status": "ready" if reference.get("ok") else "unavailable",
        }
    except (StoreError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": str(exc)}


def acknowledge_match_briefing(
    match_id: str, session_id: str, briefing_hash: str,
) -> dict[str, Any]:
    try:
        scope = _scope_for_match(match_id, session_id=session_id)
        if scope is None:
            raise StoreError("unknown_match_scope")
        record = _store().acknowledge_match_briefing(scope, session_id, briefing_hash)
        return {"ok": True, "acknowledgement": record}
    except StoreError as exc:
        return {"ok": False, "error": str(exc)}


def match_briefing_is_acknowledged(
    match_id: str, session_id: str, briefing_hash: str,
) -> bool:
    try:
        scope = _scope_for_match(match_id, session_id=session_id)
        return bool(scope and _store().match_briefing_acknowledged(
            scope, session_id, briefing_hash, across_sessions=True,
        ))
    except StoreError:
        return False


def match_briefing_acknowledgement_status(
    match_id: str, session_id: str, briefing_hash: str,
) -> dict[str, Any]:
    try:
        scope = _scope_for_match(match_id, session_id=session_id)
        if scope is None:
            raise StoreError("unknown_match_scope")
        return {
            "ok": True,
            **_store().match_briefing_acknowledgement_status(
                scope, session_id, briefing_hash,
            ),
        }
    except StoreError as exc:
        return {"ok": False, "error": str(exc), "acknowledged": False}


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
            stored_chat = store.record_chat(
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
            )
            persisted.append(stored_chat)
            if not stored_chat.get("deduplicated"):
                _journal().append(
                    scope, "chat.message", {
                        "message_uid": message_uid,
                        "direction": direction,
                        "channel": channel,
                        "sender_actor_id": str(sender_actor["actor_id"]) if sender_actor else None,
                        "recipient_actor_id": str(recipient_actor["actor_id"]) if recipient_actor else None,
                        "sender_faction_id": sender_faction_id,
                        "recipient_faction_id": recipient_faction_id,
                        "content": str(raw.get("text") or ""),
                        "metadata": {
                            "native_sequence": sequence,
                            "client_message_id": client_message_id or None,
                            "sender_player_name": sender_actor.get("network_player_name") if sender_actor else None,
                            "sender_faction_name": sender_actor.get("faction_name") if sender_actor else None,
                        },
                    }, session_id=session_id, turn=raw.get("turn"), year=raw.get("year"),
                )
        return {
            **result,
            "durable": {
                "identity": _platform_scope_identity(scope, session_id),
                "participants": list(actor_by_faction.values()),
                "messages_persisted": len(persisted),
                "database_path": str(store.path),
            },
        }
    except (StoreError, JournalError) as exc:
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
            # Managed memory pages use the common bounded ``items`` envelope.
            # Keep the controller's long-standing ``durable.attention`` alias
            # for non-managed callers without bypassing the managed MCP's
            # explicit attention acknowledgement contract.
            durable["attention"] = attention.get("items", [])
            durable["attention_acknowledged"] = acknowledge
            durable["untrusted_in_game_speech"] = True
    return result


def semantic_group_chat(
    action: str, *, match_id: str = "", session_id: str = "",
    group_id: str = "", display_name: str = "",
    member_faction_ids: list[int] | None = None, response: str = "",
    text: str = "", agent_id: str = "", perspective_id: str = "",
) -> dict[str, Any]:
    """Manage consent groups while delivering every message over native chat."""
    if action not in {"list", "create", "respond", "send", "leave"}:
        return {"ok": False, "error": "invalid_group_chat_action"}
    native = semantic_chat(
        "list", match_id=match_id, session_id=session_id,
        agent_id=agent_id, perspective_id=perspective_id, acknowledge=False,
    )
    if not native.get("ok"):
        return native
    identity = native.get("identity")
    if not isinstance(identity, dict):
        return {"ok": False, "error": "native_chat_identity_unavailable"}
    match_id = str(identity.get("match_id") or match_id)
    session_id = str(identity.get("session_id") or session_id)
    participants = [item for item in native.get("participants", [])
                    if isinstance(item, dict)]
    local = next((item for item in participants if item.get("local") is True), None)
    if local is None:
        return {"ok": False, "error": "local_chat_faction_unavailable"}
    local_faction_id = int(local["faction_id"])
    try:
        scope = _scope_for_match(
            match_id, session_id=session_id, agent_id=agent_id or None,
            perspective_id=perspective_id or None, create_legacy_session=True,
        )
        if scope is None:
            raise StoreError("unknown_match_scope")
        store = _store()
        journal = _journal()

        def snapshot_groups() -> None:
            journal.append(
                scope, "chat.groups_snapshot",
                {"groups": store.export_chat_groups(match_id)},
                session_id=session_id,
                turn=(int(native["turn"]) if native.get("turn") is not None else None),
                year=(int(native["year"]) if native.get("year") is not None else None),
            )

        if action == "list":
            projected = journal.chat_groups(scope)
            if projected["snapshot_seen"]:
                groups = []
                for group in projected["groups"]:
                    membership = next((
                        item for item in group.get("members", [])
                        if int(item.get("faction_id", -1)) == local_faction_id
                    ), None)
                    if isinstance(membership, Mapping) and membership.get("status") != "left":
                        groups.append({**group, "viewer_status": membership.get("status")})
            else:
                groups = store.list_chat_groups(scope, local_faction_id)
            return {"ok": True, "groups": groups, "participants": participants,
                "logical_delivery": True, "untrusted_in_game_speech": True}
        if action == "create":
            requested = {int(value) for value in (member_faction_ids or [])}
            requested.add(local_faction_id)
            selected = [item for item in participants
                        if int(item.get("faction_id", -1)) in requested]
            if {int(item["faction_id"]) for item in selected} != requested:
                raise InvalidRecord("unknown_chat_group_member")
            if any(item.get("local") is not True and
                   item.get("private_eligible") is not True for item in selected):
                raise InvalidRecord("chat_group_requires_mutual_commlink")
            group = store.create_chat_group(
                scope, display_name, local_faction_id,
                [{"faction_id": int(item["faction_id"]),
                  "display_name": item.get("player_name") or item.get("faction_name")
                      or f"Faction {item['faction_id']}",
                  "faction_name": item.get("faction_name")}
                 for item in selected],
            )
            snapshot_groups()
            deliveries = []
            for faction_id in sorted(requested - {local_faction_id}):
                invitation = semantic_chat(
                    "send", match_id=match_id, session_id=session_id,
                    client_message_id=f"{group['group_id']}-invite-{faction_id}",
                    text=(f"[SMACX group invitation: {group['display_name']}; "
                          f"id {group['group_id']}. Use group chat respond to accept or reject.]"),
                    recipient_faction_id=faction_id, agent_id=agent_id,
                    perspective_id=perspective_id, acknowledge=False,
                )
                deliveries.append({"recipient_faction_id": faction_id,
                                   "delivered": bool(invitation.get("ok"))})
            return {"ok": True, "group": group, "deliveries": deliveries,
                    "logical_delivery": True}
        if action in {"respond", "leave"}:
            desired = "left" if action == "leave" else response
            if desired not in {"accepted", "rejected", "left"}:
                raise InvalidRecord("invalid_chat_group_response")
            group = store.respond_chat_group(
                scope, group_id, local_faction_id, desired,
            )
            snapshot_groups()
            creator = int(group["created_by_faction_id"])
            delivery = None
            if creator != local_faction_id:
                delivery = semantic_chat(
                    "send", match_id=match_id, session_id=session_id,
                    client_message_id=f"{group_id}-response-{local_faction_id}-{group['version']}",
                    text=f"[SMACX group {group_id}: faction {local_faction_id} {desired}.]",
                    recipient_faction_id=creator, agent_id=agent_id,
                    perspective_id=perspective_id, acknowledge=False,
                )
            return {"ok": True, "group": group,
                    "native_notice_delivered": delivery is None or bool(delivery.get("ok"))}
        message = store.begin_group_message(
            scope, group_id, local_faction_id, text,
            turn=native.get("turn"), year=native.get("year"),
        )
        deliveries = []
        prefix = f"[Group: {message['group']['display_name']}] "
        for faction_id in message["recipients"]:
            sent = semantic_chat(
                "send", match_id=match_id, session_id=session_id,
                client_message_id=f"{message['logical_message_id']}-f{faction_id}",
                text=prefix + message["content"], recipient_faction_id=faction_id,
                agent_id=agent_id, perspective_id=perspective_id,
                acknowledge=False,
            )
            delivered = bool(sent.get("ok") and sent.get("sent"))
            event = sent.get("event") if isinstance(sent.get("event"), dict) else {}
            store.complete_group_delivery(
                message["logical_message_id"], faction_id,
                delivered=delivered,
                native_message_uid=(str(event.get("client_message_id"))
                                    if event.get("client_message_id") else None),
            )
            deliveries.append({"recipient_faction_id": faction_id,
                               "status": "delivered" if delivered else "failed"})
        return {"ok": all(item["status"] == "delivered" for item in deliveries),
                "logical_message_id": message["logical_message_id"],
                "group_id": group_id, "content": message["content"],
                "deliveries": deliveries, "logical_delivery": True,
                "native_echoes_collapsed": True}
    except (StoreError, JournalError, ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}


def chat_attention(
    match_id: str,
    session_id: str,
    *,
    agent_id: str = "",
    perspective_id: str = "",
) -> dict[str, Any]:
    """Capture native chat into durable at-least-once sovereign attention."""
    try:
        result = semantic_chat(
            "list",
            match_id=match_id,
            session_id=session_id,
            agent_id=agent_id,
            perspective_id=perspective_id,
            acknowledge=False,
        )
    except BridgeUnavailable as exc:
        return {"ok": False, "error": "game_not_connected", "message": str(exc)}
    durable = result.get("durable")
    messages = durable.get("attention", []) if isinstance(durable, dict) else []
    queued = []
    try:
        scope = _scope_for_match(
            match_id, session_id=session_id, agent_id=agent_id or None,
            perspective_id=perspective_id or None,
        )
        if scope is not None:
            service = AttentionService(_store(), _journal(), scope)
            for message in messages:
                if not isinstance(message, Mapping):
                    continue
                sequence = int(message.get("metadata", {}).get("native_sequence") or 0) \
                    if isinstance(message.get("metadata"), Mapping) else 0
                uid = str(message.get("message_uid") or "")
                if not uid:
                    continue
                item = service.enqueue(
                    "chat", {"message": dict(message), "untrusted_in_game_speech": True},
                    observation_cursor=sequence, priority=90,
                    critical=str(message.get("direction") or "") == "inbound",
                    turn=message.get("turn"), session_id=session_id, dedupe_key=uid,
                )
                queued.append(item["attention_id"])
    except (StoreError, JournalError, ValueError):
        pass
    return {
        "ok": bool(result.get("ok")),
        "messages": messages,
        "participants": result.get("participants", []),
        "latest_sequence": result.get("latest_sequence"),
        "attention_ids": queued,
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
    startup_scenario: str | None = None,
    game_settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if bridge_available():
        result: dict[str, Any] = {"ok": True, "launched": False, "reason": "already_running"}
    else:
        match_id = match_id or f"match-{uuid.uuid4().hex}"
        session_id = session_id or f"session-{uuid.uuid4().hex}"
        if not IDENTITY_PATTERN.fullmatch(match_id) or not IDENTITY_PATTERN.fullmatch(session_id):
            return {"ok": False, "error": "invalid_game_identity"}
        if autostart and (startup_save or startup_scenario):
            return {"ok": False, "error": "conflicting_startup_modes"}
        if startup_save and startup_scenario:
            return {"ok": False, "error": "conflicting_startup_modes"}
        scenario_argument: str | None = None
        if startup_scenario:
            parts = startup_scenario.split("/")
            if not parts or len(startup_scenario) > 512 \
                    or not startup_scenario.upper().endswith(".SC") \
                    or any(part in ("", ".", "..") or not re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9 _.'()-]{0,95}", part,
                    ) for part in parts):
                return {"ok": False, "error": "invalid_scenario_id"}
            path = GAME / "scenarios"
            for part in parts:
                path /= part
            resolved = path.resolve()
            if not resolved.is_file() or GAME.resolve() not in resolved.parents:
                return {"ok": False, "error": "scenario_unavailable"}
            scenario_argument = "scenarios\\" + "\\".join(parts)
        normalized_settings: dict[str, Any] = {}
        if autostart:
            settings_input = dict(game_settings or {})
            settings_input.setdefault("world_size", world_size)
            settings_input.setdefault("blind_research", blind_research)
            try:
                normalized_settings = normalize_game_settings(
                    settings_input, default_blind_research=blind_research,
                )
            except StoreError as exc:
                return {"ok": False, "error": "invalid_game_settings", "message": str(exc)}
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
                mode=("singleplayer" if autostart else "scenario" if startup_scenario
                      else "load" if startup_save else "interactive"),
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
            environment.update(game_settings_environment(normalized_settings))
        if startup_scenario:
            environment.update({
                "SMACX_AGENT_STARTUP_SCENARIO": startup_scenario,
                "SMACX_AGENT_DIFFICULTY": str(min(max(difficulty, 0), 5)),
                "SMACX_AGENT_FACTION_ID": str(min(max(faction_id, 1), 7)),
                "SMACX_AGENT_NARRATIVE_UI": "1" if narrative_ui else "0",
                "SMACX_AGENT_TUTORIAL_UI": "1" if tutorial_ui else "0",
            })
        command = [str(PRESSURE_VESSEL), "--", str(PROTON), "run", str(GAME / "thinker.exe"), "-windowed"]
        if startup_save:
            command.append(startup_save)
        if startup_scenario:
            command.append(str(scenario_argument))
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
        if startup_scenario:
            session_record["scenario_id"] = startup_scenario
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
        if startup_scenario:
            manifest["scenario_id"] = startup_scenario
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
    game_settings: Mapping[str, Any] | None = None,
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
        game_settings=game_settings,
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
                    "game_settings": normalize_game_settings(
                        {
                            **dict(game_settings or {}),
                            "world_size": dict(game_settings or {}).get("world_size", world_size),
                            "blind_research": dict(game_settings or {}).get(
                                "blind_research", blind_research,
                            ),
                        },
                        default_blind_research=blind_research,
                    ),
                },
                "identity": launched["identity"],
                "knowledge_directory": str(KNOWLEDGE_ROOT / match_id),
                "database_path": str(_store().path),
                "snapshot": last,
            }
        time.sleep(0.5)
    return {"ok": False, "error": "semantic_setup_timeout", "last_state": last, "log": str(LOG_FILE)}


def scenario_game(
    scenario_id: str,
    wait_seconds: int = 90,
    difficulty: int = 0,
    faction_id: int = 1,
    narrative_ui: bool = False,
    tutorial_ui: bool = False,
    match_id: str | None = None,
    agent_id: str | None = None,
    perspective_id: str | None = None,
    instance_id: str | None = None,
) -> dict[str, Any]:
    if bridge_available():
        return {"ok": False, "error": "game_already_running"}
    match_id = match_id or f"match-{uuid.uuid4().hex}"
    launched = launch_game(
        wait_seconds=min(max(wait_seconds, 5), 120),
        difficulty=difficulty, faction_id=faction_id,
        narrative_ui=narrative_ui, tutorial_ui=tutorial_ui,
        match_id=match_id, session_id=f"session-{uuid.uuid4().hex}",
        agent_id=agent_id, perspective_id=perspective_id, instance_id=instance_id,
        startup_scenario=scenario_id,
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
        if snapshot.get("scenario", {}).get("active") is True:
            return {"ok": True, "launched": True, "scenario_id": scenario_id,
                    "identity": launched["identity"], "snapshot": last}
        time.sleep(0.5)
    return {"ok": False, "error": "semantic_scenario_timeout", "last_state": last}


def list_scenarios() -> dict[str, Any]:
    root = GAME / "scenarios"
    scenarios: list[dict[str, Any]] = []
    if not root.is_dir() or root.is_symlink():
        return {"ok": True, "scenarios": scenarios}
    for path in sorted(root.rglob("*.SC"), key=lambda item: item.as_posix().casefold()):
        relative = path.relative_to(root)
        if path.is_symlink() or not path.is_file() or len(relative.parts) > 6 \
                or not all(re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9 _.'()-]{0,95}", part,
                ) for part in relative.parts):
            continue
        scenarios.append({
            "scenario_id": relative.as_posix(), "display_name": path.stem,
            "size_bytes": path.stat().st_size,
        })
        if len(scenarios) >= 256:
            break
    return {"ok": True, "scenarios": scenarios, "assets_distributed": False}


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
                _journal().append(
                    scope, "lifecycle.session_stopped",
                    {"method": result.get("method"), "stopped": True},
                    session_id=session_id, commit_reason="Stop native session",
                )
                _store().close_session(session_id)
        except (StoreError, JournalError) as exc:
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
