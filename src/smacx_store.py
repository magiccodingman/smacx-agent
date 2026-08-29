"""Authoritative durable identities, events, chat, and memory for SMACX Agent.

SQLite is the source of truth.  External memory systems such as Graphiti are
downstream projections and must never be required to read or mutate this store.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Iterator, Mapping, Sequence
import uuid


ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,96}$")
KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
SESSION_LOCAL_REFERENCE = re.compile(
    r"(?:\b(?:unit|vehicle|base|prototype)[ _-]?ids?\b"
    r"|\(\s*id\s*[:=#-]?\s*\d+\s*\)"
    r"|\bbase\s+#?\d+\b)",
    re.IGNORECASE,
)


class StoreError(RuntimeError):
    """Base class for durable-store failures safe to expose as error codes."""


class InvalidIdentity(StoreError):
    pass


class ScopeViolation(StoreError):
    pass


class InvalidRecord(StoreError):
    pass


@dataclass(frozen=True)
class MemoryScope:
    match_id: str
    agent_id: str
    perspective_id: str


INITIAL_SCHEMA_FOUNDATION = r"""
CREATE TABLE installations (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    installation_id TEXT NOT NULL UNIQUE,
    created_unix REAL NOT NULL
);

CREATE TABLE agents (
    agent_id TEXT PRIMARY KEY,
    installation_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    profile_ref TEXT,
    personality_ref TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_unix REAL NOT NULL,
    updated_unix REAL NOT NULL
);

CREATE TABLE matches (
    match_id TEXT PRIMARY KEY,
    installation_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    ruleset_id TEXT,
    host_instance_id TEXT,
    last_turn INTEGER,
    last_year INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_unix REAL NOT NULL,
    updated_unix REAL NOT NULL
);

CREATE TABLE perspectives (
    perspective_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL REFERENCES matches(match_id),
    agent_id TEXT NOT NULL REFERENCES agents(agent_id),
    faction_id INTEGER,
    faction_name TEXT,
    controller_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    created_unix REAL NOT NULL,
    closed_unix REAL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (perspective_id, match_id, agent_id)
);

CREATE TABLE instances (
    instance_id TEXT PRIMARY KEY,
    installation_id TEXT NOT NULL,
    match_id TEXT REFERENCES matches(match_id),
    agent_id TEXT REFERENCES agents(agent_id),
    perspective_id TEXT REFERENCES perspectives(perspective_id),
    worker_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    bridge_host TEXT,
    bridge_port INTEGER,
    runtime_root TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_unix REAL NOT NULL,
    updated_unix REAL NOT NULL
);

CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL REFERENCES matches(match_id),
    agent_id TEXT NOT NULL REFERENCES agents(agent_id),
    perspective_id TEXT NOT NULL REFERENCES perspectives(perspective_id),
    instance_id TEXT NOT NULL REFERENCES instances(instance_id),
    status TEXT NOT NULL,
    loaded_save TEXT,
    started_unix REAL NOT NULL,
    stopped_unix REAL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE actors (
    actor_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL REFERENCES matches(match_id),
    stable_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    controller_kind TEXT NOT NULL,
    controller_ref TEXT,
    faction_id INTEGER,
    faction_name TEXT,
    network_player_id TEXT,
    network_player_name TEXT,
    first_observed_unix REAL NOT NULL,
    last_observed_unix REAL NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (match_id, stable_key)
);

CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL REFERENCES matches(match_id),
    agent_id TEXT NOT NULL REFERENCES agents(agent_id),
    perspective_id TEXT NOT NULL REFERENCES perspectives(perspective_id),
    session_id TEXT REFERENCES sessions(session_id),
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    turn INTEGER,
    year INTEGER,
    actor_id TEXT REFERENCES actors(actor_id),
    subject_id TEXT,
    correlation_id TEXT,
    dedupe_key TEXT,
    importance INTEGER NOT NULL DEFAULT 50 CHECK (importance BETWEEN 0 AND 100),
    payload_json TEXT NOT NULL,
    search_text TEXT NOT NULL DEFAULT '',
    observed_unix REAL NOT NULL,
    created_unix REAL NOT NULL
);
CREATE UNIQUE INDEX events_dedupe
    ON events(match_id, agent_id, perspective_id, dedupe_key)
    WHERE dedupe_key IS NOT NULL;
CREATE INDEX events_scope_time
    ON events(match_id, agent_id, perspective_id, created_unix DESC);
CREATE INDEX events_scope_turn
    ON events(match_id, agent_id, perspective_id, turn, event_type);
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are immutable');
END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are immutable');
END;

CREATE TABLE chat_messages (
    chat_id TEXT PRIMARY KEY,
    message_uid TEXT NOT NULL,
    match_id TEXT NOT NULL REFERENCES matches(match_id),
    agent_id TEXT NOT NULL REFERENCES agents(agent_id),
    perspective_id TEXT NOT NULL REFERENCES perspectives(perspective_id),
    session_id TEXT REFERENCES sessions(session_id),
    direction TEXT NOT NULL,
    channel TEXT NOT NULL,
    sender_actor_id TEXT REFERENCES actors(actor_id),
    recipient_actor_id TEXT REFERENCES actors(actor_id),
    sender_faction_id INTEGER,
    recipient_faction_id INTEGER,
    content TEXT NOT NULL,
    turn INTEGER,
    year INTEGER,
    received_unix REAL NOT NULL,
    acknowledged_unix REAL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (match_id, agent_id, perspective_id, message_uid)
);
CREATE INDEX chat_unread
    ON chat_messages(match_id, agent_id, perspective_id, acknowledged_unix, received_unix);

CREATE TABLE facts (
    fact_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL REFERENCES matches(match_id),
    agent_id TEXT NOT NULL REFERENCES agents(agent_id),
    perspective_id TEXT NOT NULL REFERENCES perspectives(perspective_id),
    session_id TEXT REFERENCES sessions(session_id),
    fact_key TEXT NOT NULL,
    fact_revision INTEGER NOT NULL,
    category TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    value TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0 CHECK (confidence BETWEEN 0.0 AND 1.0),
    status TEXT NOT NULL,
    observed_revision TEXT,
    observed_turn INTEGER,
    observed_year INTEGER,
    source_event_id TEXT REFERENCES events(event_id),
    supersedes_fact_id TEXT REFERENCES facts(fact_id),
    superseded_by_fact_id TEXT REFERENCES facts(fact_id),
    created_unix REAL NOT NULL,
    UNIQUE (match_id, agent_id, perspective_id, fact_key, fact_revision)
);
CREATE UNIQUE INDEX facts_one_current
    ON facts(match_id, agent_id, perspective_id, fact_key)
    WHERE status = 'current';
CREATE INDEX facts_scope_category
    ON facts(match_id, agent_id, perspective_id, status, category, created_unix DESC);

CREATE TABLE claims (
    claim_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL REFERENCES matches(match_id),
    agent_id TEXT NOT NULL REFERENCES agents(agent_id),
    perspective_id TEXT NOT NULL REFERENCES perspectives(perspective_id),
    session_id TEXT REFERENCES sessions(session_id),
    asserted_by_actor_id TEXT REFERENCES actors(actor_id),
    about_actor_id TEXT REFERENCES actors(actor_id),
    topic TEXT NOT NULL,
    content TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
    status TEXT NOT NULL,
    source_event_id TEXT REFERENCES events(event_id),
    turn INTEGER,
    year INTEGER,
    created_unix REAL NOT NULL
);

CREATE TABLE beliefs (
    belief_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL REFERENCES matches(match_id),
    agent_id TEXT NOT NULL REFERENCES agents(agent_id),
    perspective_id TEXT NOT NULL REFERENCES perspectives(perspective_id),
    topic TEXT NOT NULL,
    belief_revision INTEGER NOT NULL,
    content TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
    status TEXT NOT NULL,
    supersedes_belief_id TEXT REFERENCES beliefs(belief_id),
    superseded_by_belief_id TEXT REFERENCES beliefs(belief_id),
    created_unix REAL NOT NULL,
    UNIQUE (match_id, agent_id, perspective_id, topic, belief_revision)
);
CREATE UNIQUE INDEX beliefs_one_current
    ON beliefs(match_id, agent_id, perspective_id, topic)
    WHERE status = 'current';
CREATE TABLE belief_evidence (
    belief_id TEXT NOT NULL REFERENCES beliefs(belief_id),
    event_id TEXT NOT NULL REFERENCES events(event_id),
    stance TEXT NOT NULL,
    weight REAL NOT NULL CHECK (weight BETWEEN 0.0 AND 1.0),
    PRIMARY KEY (belief_id, event_id)
);

CREATE TABLE relationships (
    relationship_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL REFERENCES matches(match_id),
    agent_id TEXT NOT NULL REFERENCES agents(agent_id),
    perspective_id TEXT NOT NULL REFERENCES perspectives(perspective_id),
    actor_id TEXT NOT NULL REFERENCES actors(actor_id),
    relationship_revision INTEGER NOT NULL,
    affinity INTEGER NOT NULL CHECK (affinity BETWEEN -100 AND 100),
    trust INTEGER NOT NULL CHECK (trust BETWEEN -100 AND 100),
    respect INTEGER NOT NULL CHECK (respect BETWEEN -100 AND 100),
    threat INTEGER NOT NULL CHECK (threat BETWEEN -100 AND 100),
    grievance INTEGER NOT NULL CHECK (grievance BETWEEN 0 AND 100),
    obligation INTEGER NOT NULL CHECK (obligation BETWEEN -100 AND 100),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
    reasons_json TEXT NOT NULL,
    status TEXT NOT NULL,
    supersedes_relationship_id TEXT REFERENCES relationships(relationship_id),
    superseded_by_relationship_id TEXT REFERENCES relationships(relationship_id),
    source_event_id TEXT REFERENCES events(event_id),
    created_unix REAL NOT NULL,
    UNIQUE (match_id, agent_id, perspective_id, actor_id, relationship_revision)
);
CREATE UNIQUE INDEX relationships_one_current
    ON relationships(match_id, agent_id, perspective_id, actor_id)
    WHERE status = 'current';

CREATE TABLE commitments (
    commitment_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL REFERENCES matches(match_id),
    agent_id TEXT NOT NULL REFERENCES agents(agent_id),
    perspective_id TEXT NOT NULL REFERENCES perspectives(perspective_id),
    commitment_key TEXT NOT NULL,
    commitment_revision INTEGER NOT NULL,
    title TEXT NOT NULL,
    terms TEXT NOT NULL,
    status TEXT NOT NULL,
    due_turn INTEGER,
    due_year INTEGER,
    source_event_id TEXT REFERENCES events(event_id),
    resolution_event_id TEXT REFERENCES events(event_id),
    supersedes_commitment_id TEXT REFERENCES commitments(commitment_id),
    superseded_by_commitment_id TEXT REFERENCES commitments(commitment_id),
    created_unix REAL NOT NULL,
    UNIQUE (match_id, agent_id, perspective_id, commitment_key, commitment_revision)
);
CREATE UNIQUE INDEX commitments_one_current
    ON commitments(match_id, agent_id, perspective_id, commitment_key)
    WHERE status NOT IN ('superseded');
CREATE TABLE commitment_parties (
    commitment_id TEXT NOT NULL REFERENCES commitments(commitment_id),
    actor_id TEXT NOT NULL REFERENCES actors(actor_id),
    role TEXT NOT NULL,
    PRIMARY KEY (commitment_id, actor_id, role)
);

CREATE TABLE goals (
    goal_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL REFERENCES matches(match_id),
    agent_id TEXT NOT NULL REFERENCES agents(agent_id),
    perspective_id TEXT NOT NULL REFERENCES perspectives(perspective_id),
    goal_key TEXT NOT NULL,
    goal_revision INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    priority INTEGER NOT NULL CHECK (priority BETWEEN 0 AND 100),
    status TEXT NOT NULL,
    due_turn INTEGER,
    due_year INTEGER,
    trigger_json TEXT NOT NULL DEFAULT '{}',
    parent_goal_id TEXT REFERENCES goals(goal_id),
    source_event_id TEXT REFERENCES events(event_id),
    supersedes_goal_id TEXT REFERENCES goals(goal_id),
    superseded_by_goal_id TEXT REFERENCES goals(goal_id),
    created_unix REAL NOT NULL,
    UNIQUE (match_id, agent_id, perspective_id, goal_key, goal_revision)
);
CREATE UNIQUE INDEX goals_one_current
    ON goals(match_id, agent_id, perspective_id, goal_key)
    WHERE status NOT IN ('superseded');
CREATE INDEX goals_active
    ON goals(match_id, agent_id, perspective_id, status, priority DESC);

CREATE TABLE summaries (
    summary_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL REFERENCES matches(match_id),
    agent_id TEXT NOT NULL REFERENCES agents(agent_id),
    perspective_id TEXT NOT NULL REFERENCES perspectives(perspective_id),
    section TEXT NOT NULL,
    content TEXT NOT NULL,
    token_estimate INTEGER NOT NULL,
    status TEXT NOT NULL,
    through_event_id TEXT REFERENCES events(event_id),
    supersedes_summary_id TEXT REFERENCES summaries(summary_id),
    superseded_by_summary_id TEXT REFERENCES summaries(summary_id),
    created_unix REAL NOT NULL
);
CREATE UNIQUE INDEX summaries_one_current
    ON summaries(match_id, agent_id, perspective_id, section)
    WHERE status = 'current';

CREATE TABLE memory_budgets (
    section TEXT PRIMARY KEY,
    max_tokens INTEGER NOT NULL CHECK (max_tokens > 0)
);
INSERT INTO memory_budgets(section, max_tokens) VALUES
    ('situation', 1200),
    ('relationships', 1200),
    ('goals', 800),
    ('commitments', 800),
    ('recent_events', 1600),
    ('chat', 1200);

CREATE TABLE search_documents (
    document_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    perspective_id TEXT NOT NULL,
    document_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    importance INTEGER NOT NULL DEFAULT 50,
    created_unix REAL NOT NULL,
    UNIQUE (match_id, agent_id, perspective_id, document_kind, source_id)
);
CREATE INDEX search_documents_scope
    ON search_documents(match_id, agent_id, perspective_id, document_kind, created_unix DESC);
CREATE VIRTUAL TABLE search_fts USING fts5(
    title,
    body,
    tags,
    content='search_documents',
    content_rowid='rowid',
    tokenize='porter unicode61 remove_diacritics 2'
);
CREATE TRIGGER search_documents_ai AFTER INSERT ON search_documents BEGIN
    INSERT INTO search_fts(rowid, title, body, tags)
    VALUES (new.rowid, new.title, new.body, new.tags);
END;
CREATE TRIGGER search_documents_ad AFTER DELETE ON search_documents BEGIN
    INSERT INTO search_fts(search_fts, rowid, title, body, tags)
    VALUES ('delete', old.rowid, old.title, old.body, old.tags);
END;
CREATE TRIGGER search_documents_au AFTER UPDATE ON search_documents BEGIN
    INSERT INTO search_fts(search_fts, rowid, title, body, tags)
    VALUES ('delete', old.rowid, old.title, old.body, old.tags);
    INSERT INTO search_fts(rowid, title, body, tags)
    VALUES (new.rowid, new.title, new.body, new.tags);
END;

CREATE TABLE projection_cursors (
    projector TEXT NOT NULL,
    namespace TEXT NOT NULL,
    match_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    perspective_id TEXT NOT NULL,
    last_event_id TEXT,
    last_created_unix REAL,
    status TEXT NOT NULL,
    last_error TEXT,
    updated_unix REAL NOT NULL,
    PRIMARY KEY (projector, namespace)
);

CREATE TABLE legacy_imports (
    source_path TEXT PRIMARY KEY,
    match_id TEXT NOT NULL,
    imported_unix REAL NOT NULL,
    content_sha256 TEXT NOT NULL,
    result_json TEXT NOT NULL
);
"""

INITIAL_SCHEMA_CONTROL = r"""
CREATE TABLE control_settings (
    setting_key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_unix REAL NOT NULL
);

CREATE TABLE control_admins (
    admin_id TEXT PRIMARY KEY,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_salt BLOB NOT NULL,
    password_hash BLOB NOT NULL,
    password_parameters_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'disabled')),
    created_unix REAL NOT NULL,
    updated_unix REAL NOT NULL
);

CREATE TABLE control_sessions (
    auth_session_id TEXT PRIMARY KEY,
    admin_id TEXT NOT NULL REFERENCES control_admins(admin_id),
    token_hash BLOB NOT NULL UNIQUE,
    csrf_hash BLOB NOT NULL,
    created_unix REAL NOT NULL,
    last_seen_unix REAL NOT NULL,
    expires_unix REAL NOT NULL,
    revoked_unix REAL
);
CREATE INDEX control_sessions_active
    ON control_sessions(token_hash, expires_unix, revoked_unix);

CREATE TABLE secret_refs (
    secret_id TEXT PRIMARY KEY,
    purpose TEXT NOT NULL,
    relative_path TEXT NOT NULL UNIQUE,
    fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
    created_unix REAL NOT NULL,
    updated_unix REAL NOT NULL
);

CREATE TABLE model_providers (
    provider_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL UNIQUE,
    provider_kind TEXT NOT NULL,
    base_url TEXT NOT NULL,
    api_key_secret_id TEXT REFERENCES secret_refs(secret_id),
    default_model_id TEXT,
    context_length_override INTEGER CHECK (
        context_length_override IS NULL OR context_length_override BETWEEN 1024 AND 16777216
    ),
    status TEXT NOT NULL CHECK (status IN ('configured', 'healthy', 'unreachable', 'disabled')),
    last_error TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    discovered_unix REAL,
    created_unix REAL NOT NULL,
    updated_unix REAL NOT NULL
);

CREATE TABLE provider_models (
    provider_id TEXT NOT NULL REFERENCES model_providers(provider_id),
    model_id TEXT NOT NULL,
    display_name TEXT,
    context_length INTEGER,
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    raw_metadata_json TEXT NOT NULL DEFAULT '{}',
    discovered_unix REAL NOT NULL,
    PRIMARY KEY (provider_id, model_id)
);

CREATE TABLE game_sources (
    game_source_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    host_path TEXT NOT NULL UNIQUE,
    executable_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('validated', 'unavailable', 'disabled')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    validated_unix REAL,
    created_unix REAL NOT NULL,
    updated_unix REAL NOT NULL
);

CREATE TABLE runtime_assets (
    runtime_id TEXT PRIMARY KEY,
    runtime_kind TEXT NOT NULL,
    display_name TEXT NOT NULL,
    source_path TEXT,
    storage_kind TEXT NOT NULL,
    storage_ref TEXT NOT NULL,
    content_fingerprint TEXT,
    status TEXT NOT NULL CHECK (status IN ('importing', 'ready', 'invalid', 'disabled')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_unix REAL NOT NULL,
    updated_unix REAL NOT NULL,
    UNIQUE (runtime_kind, storage_kind, storage_ref)
);

CREATE TABLE harness_profiles (
    harness_profile_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    adapter_kind TEXT NOT NULL,
    agent_id TEXT REFERENCES agents(agent_id),
    external_profile_id TEXT,
    provider_id TEXT REFERENCES model_providers(provider_id),
    model_id TEXT,
    reasoning_effort TEXT NOT NULL DEFAULT 'low',
    context_length INTEGER,
    workspace_path TEXT,
    system_prompt TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('configured', 'ready', 'running', 'stopped', 'error', 'disabled')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_unix REAL NOT NULL,
    updated_unix REAL NOT NULL
);
CREATE UNIQUE INDEX harness_profile_agent_adapter
    ON harness_profiles(agent_id, adapter_kind)
    WHERE agent_id IS NOT NULL;

CREATE TABLE worker_specs (
    instance_id TEXT PRIMARY KEY REFERENCES instances(instance_id),
    game_source_id TEXT NOT NULL REFERENCES game_sources(game_source_id),
    runtime_id TEXT NOT NULL REFERENCES runtime_assets(runtime_id),
    image_ref TEXT NOT NULL,
    container_name TEXT NOT NULL UNIQUE,
    data_volume TEXT NOT NULL UNIQUE,
    bridge_secret_id TEXT NOT NULL REFERENCES secret_refs(secret_id),
    view_secret_id TEXT REFERENCES secret_refs(secret_id),
    desired_status TEXT NOT NULL CHECK (desired_status IN ('stopped', 'running', 'parked', 'retired')),
    observed_status TEXT NOT NULL,
    autostart_json TEXT NOT NULL DEFAULT '{}',
    network_json TEXT NOT NULL DEFAULT '{}',
    last_error TEXT,
    created_unix REAL NOT NULL,
    updated_unix REAL NOT NULL
);

CREATE TABLE seat_assignments (
    seat_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL REFERENCES matches(match_id),
    seat_index INTEGER NOT NULL CHECK (seat_index BETWEEN 0 AND 7),
    controller_kind TEXT NOT NULL,
    agent_id TEXT REFERENCES agents(agent_id),
    perspective_id TEXT REFERENCES perspectives(perspective_id),
    instance_id TEXT REFERENCES instances(instance_id),
    harness_profile_id TEXT REFERENCES harness_profiles(harness_profile_id),
    faction_id INTEGER,
    faction_name TEXT,
    status TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_unix REAL NOT NULL,
    updated_unix REAL NOT NULL,
    UNIQUE (match_id, seat_index)
);

CREATE TABLE control_audit (
    audit_id TEXT PRIMARY KEY,
    admin_id TEXT REFERENCES control_admins(admin_id),
    action TEXT NOT NULL,
    object_kind TEXT NOT NULL,
    object_id TEXT,
    remote_address TEXT,
    outcome TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_unix REAL NOT NULL
);
CREATE INDEX control_audit_time ON control_audit(created_unix DESC);
CREATE TRIGGER control_audit_no_update BEFORE UPDATE ON control_audit
BEGIN
    SELECT RAISE(ABORT, 'control audit is immutable');
END;
CREATE TRIGGER control_audit_no_delete BEFORE DELETE ON control_audit
BEGIN
    SELECT RAISE(ABORT, 'control audit is immutable');
END;
"""

INITIAL_SCHEMA_HARNESS = r"""
CREATE TABLE harness_runs (
    run_id TEXT PRIMARY KEY,
    harness_profile_id TEXT NOT NULL REFERENCES harness_profiles(harness_profile_id),
    match_id TEXT NOT NULL REFERENCES matches(match_id),
    agent_id TEXT NOT NULL REFERENCES agents(agent_id),
    perspective_id TEXT NOT NULL REFERENCES perspectives(perspective_id),
    instance_id TEXT NOT NULL REFERENCES instances(instance_id),
    native_session_id TEXT REFERENCES sessions(session_id),
    external_session_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'stopped', 'completed', 'error')),
    initial_prompt TEXT NOT NULL DEFAULT '',
    last_error TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_unix REAL NOT NULL,
    started_unix REAL,
    stopped_unix REAL,
    updated_unix REAL NOT NULL
);
CREATE INDEX harness_runs_scope_time
    ON harness_runs(match_id, agent_id, perspective_id, created_unix DESC);
CREATE UNIQUE INDEX harness_one_live_run_per_perspective
    ON harness_runs(match_id, agent_id, perspective_id)
    WHERE status IN ('queued', 'running');

CREATE TABLE operation_schedules (
    schedule_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    operation_kind TEXT NOT NULL CHECK (
        operation_kind IN ('backup', 'checkpoint', 'match_start', 'match_resume')
    ),
    target_kind TEXT NOT NULL CHECK (target_kind IN ('installation', 'match')),
    target_id TEXT,
    interval_seconds INTEGER NOT NULL CHECK (interval_seconds BETWEEN 60 AND 2592000),
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL CHECK (status IN ('active', 'paused', 'disabled')),
    next_run_unix REAL NOT NULL,
    last_run_unix REAL,
    last_outcome TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_unix REAL NOT NULL,
    updated_unix REAL NOT NULL
);
CREATE INDEX operation_schedules_due
    ON operation_schedules(status, next_run_unix);

CREATE TABLE operation_runs (
    operation_run_id TEXT PRIMARY KEY,
    schedule_id TEXT REFERENCES operation_schedules(schedule_id),
    operation_kind TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'skipped')),
    result_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT,
    started_unix REAL NOT NULL,
    finished_unix REAL
);
CREATE INDEX operation_runs_time
    ON operation_runs(started_unix DESC);
CREATE TRIGGER operation_runs_no_update_after_finish BEFORE UPDATE ON operation_runs
WHEN OLD.finished_unix IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'finished operation run is immutable');
END;
CREATE TRIGGER operation_runs_no_delete BEFORE DELETE ON operation_runs
BEGIN
    SELECT RAISE(ABORT, 'operation run is immutable');
END;

CREATE TABLE backup_sets (
    backup_id TEXT PRIMARY KEY,
    installation_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('creating', 'complete', 'invalid', 'restored')),
    relative_path TEXT NOT NULL UNIQUE,
    manifest_sha256 TEXT,
    database_sha256 TEXT,
    includes_secrets INTEGER NOT NULL CHECK (includes_secrets IN (0, 1)),
    worker_count INTEGER NOT NULL DEFAULT 0,
    size_bytes INTEGER,
    last_error TEXT,
    created_unix REAL NOT NULL,
    completed_unix REAL,
    restored_unix REAL
);

CREATE TABLE supervision_incidents (
    incident_id TEXT PRIMARY KEY,
    match_id TEXT REFERENCES matches(match_id),
    instance_id TEXT REFERENCES instances(instance_id),
    incident_kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'recovered', 'operator_required', 'closed')),
    details_json TEXT NOT NULL DEFAULT '{}',
    first_seen_unix REAL NOT NULL,
    last_seen_unix REAL NOT NULL,
    recovered_unix REAL
);
CREATE INDEX supervision_incidents_open
    ON supervision_incidents(status, last_seen_unix DESC);
"""

INITIAL_SCHEMA = "\n".join((
    INITIAL_SCHEMA_FOUNDATION,
    INITIAL_SCHEMA_CONTROL,
    INITIAL_SCHEMA_HARNESS,
))
SCHEMA_REVISION = 1


def _new_id(kind: str) -> str:
    return f"{kind}-{uuid.uuid4().hex}"


def _require_id(value: str, field: str) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise InvalidIdentity(f"invalid_{field}")
    return value


def _require_key(value: str, field: str) -> str:
    if not isinstance(value, str) or not KEY_PATTERN.fullmatch(value):
        raise InvalidRecord(f"invalid_{field}")
    return value


def _json(value: Mapping[str, Any] | Sequence[Any] | None) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _bounded_text(value: str, field: str, maximum: int, *, required: bool = True) -> str:
    text = value.strip()
    if (required and not text) or len(text) > maximum:
        raise InvalidRecord(f"invalid_{field}")
    return text


class SmacxStore:
    """Thread-safe connection factory and scoped persistence API."""

    def __init__(self, path: Path | str | None = None) -> None:
        configured = os.environ.get("SMACX_DB_PATH")
        if path is None and configured:
            path = configured
        if path is None:
            data_root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
            path = data_root / "smacx-agent" / "smacx.sqlite3"
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialization_lock = threading.Lock()
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        # journal_mode is itself a write-like schema operation.  Multiple
        # fresh control/MCP processes can open a brand-new database together,
        # so honor the busy window explicitly instead of assuming the sqlite3
        # connection timeout also retries this PRAGMA on every build.
        deadline = time.monotonic() + 10.0
        while True:
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    connection.close()
                    raise
                time.sleep(0.025)
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        """Create the one canonical pre-release schema; never upgrade in place."""
        with self._initialization_lock:
            connection = self._connect()
            try:
                # The exclusive SQLite transaction is also the cross-process
                # initialization lock. Re-read state only after it is held.
                connection.execute("BEGIN EXCLUSIVE")
                revision = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if revision == SCHEMA_REVISION:
                    connection.commit()
                    return
                if revision != 0:
                    raise StoreError("unsupported_prerelease_schema_recreate_database")
                existing_tables = {
                    str(row["name"])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
                if existing_tables:
                    raise StoreError("unversioned_prerelease_schema_recreate_database")
                statement = ""
                for line in INITIAL_SCHEMA.splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        if statement.strip():
                            connection.execute(statement)
                        statement = ""
                if statement.strip():
                    raise StoreError("incomplete_initial_schema")
                connection.execute(f"PRAGMA user_version = {SCHEMA_REVISION}")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def schema_version(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def installation_id(self) -> str:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT installation_id FROM installations WHERE singleton = 1"
            ).fetchone()
            if row:
                return str(row["installation_id"])
            installation_id = _new_id("installation")
            connection.execute(
                "INSERT INTO installations(singleton, installation_id, created_unix) VALUES (1, ?, ?)",
                (installation_id, time.time()),
            )
            return installation_id

    def graph_namespace(self, scope: MemoryScope) -> str:
        self.require_scope(scope)
        return ":".join(("smacx", self.installation_id(), scope.match_id, scope.agent_id, scope.perspective_id))

    def ensure_agent(
        self,
        agent_id: str,
        display_name: str,
        *,
        profile_ref: str | None = None,
        personality_ref: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        _require_id(agent_id, "agent_id")
        display_name = _bounded_text(display_name, "agent_name", 160)
        now = time.time()
        installation_id = self.installation_id()
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
            if row:
                if row["installation_id"] != installation_id:
                    raise ScopeViolation("agent_belongs_to_another_installation")
                return dict(row)
            connection.execute(
                "INSERT INTO agents(agent_id, installation_id, display_name, profile_ref, personality_ref, "
                "metadata_json, created_unix, updated_unix) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (agent_id, installation_id, display_name, profile_ref, personality_ref, _json(metadata), now, now),
            )
            return dict(connection.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone())

    def create_match(
        self,
        *,
        match_id: str | None = None,
        display_name: str = "Untitled match",
        mode: str = "unknown",
        ruleset_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        match_id = match_id or _new_id("match")
        _require_id(match_id, "match_id")
        display_name = _bounded_text(display_name, "match_name", 200)
        mode = _require_key(mode, "match_mode")
        now = time.time()
        installation_id = self.installation_id()
        with self.transaction() as connection:
            existing = connection.execute("SELECT * FROM matches WHERE match_id = ?", (match_id,)).fetchone()
            if existing:
                if existing["installation_id"] != installation_id:
                    raise ScopeViolation("match_belongs_to_another_installation")
                return dict(existing)
            connection.execute(
                "INSERT INTO matches(match_id, installation_id, display_name, mode, status, ruleset_id, "
                "metadata_json, created_unix, updated_unix) VALUES (?, ?, ?, ?, 'created', ?, ?, ?, ?)",
                (match_id, installation_id, display_name, mode, ruleset_id, _json(metadata), now, now),
            )
            return dict(connection.execute("SELECT * FROM matches WHERE match_id = ?", (match_id,)).fetchone())

    def create_perspective(
        self,
        match_id: str,
        agent_id: str,
        *,
        perspective_id: str | None = None,
        faction_id: int | None = None,
        faction_name: str | None = None,
        controller_kind: str = "agent",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        _require_id(match_id, "match_id")
        _require_id(agent_id, "agent_id")
        perspective_id = perspective_id or _new_id("perspective")
        _require_id(perspective_id, "perspective_id")
        controller_kind = _require_key(controller_kind, "controller_kind")
        now = time.time()
        with self.transaction() as connection:
            if not connection.execute("SELECT 1 FROM matches WHERE match_id = ?", (match_id,)).fetchone():
                raise ScopeViolation("unknown_match_id")
            if not connection.execute("SELECT 1 FROM agents WHERE agent_id = ?", (agent_id,)).fetchone():
                raise ScopeViolation("unknown_agent_id")
            existing = connection.execute(
                "SELECT * FROM perspectives WHERE perspective_id = ?", (perspective_id,)
            ).fetchone()
            if existing:
                if existing["match_id"] != match_id or existing["agent_id"] != agent_id:
                    raise ScopeViolation("perspective_scope_mismatch")
                return dict(existing)
            connection.execute(
                "INSERT INTO perspectives(perspective_id, match_id, agent_id, faction_id, faction_name, "
                "controller_kind, status, created_unix, metadata_json) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                (perspective_id, match_id, agent_id, faction_id, faction_name, controller_kind, now, _json(metadata)),
            )
            return dict(connection.execute(
                "SELECT * FROM perspectives WHERE perspective_id = ?", (perspective_id,)
            ).fetchone())

    def require_scope(self, scope: MemoryScope, *, connection: sqlite3.Connection | None = None) -> None:
        _require_id(scope.match_id, "match_id")
        _require_id(scope.agent_id, "agent_id")
        _require_id(scope.perspective_id, "perspective_id")
        own_connection = connection is None
        connection = connection or self._connect()
        try:
            row = connection.execute(
                "SELECT 1 FROM perspectives WHERE perspective_id = ? AND match_id = ? AND agent_id = ?",
                (scope.perspective_id, scope.match_id, scope.agent_id),
            ).fetchone()
            if not row:
                raise ScopeViolation("unknown_or_mismatched_memory_scope")
        finally:
            if own_connection:
                connection.close()

    def register_instance(
        self,
        *,
        instance_id: str | None = None,
        worker_kind: str = "native-linux",
        scope: MemoryScope | None = None,
        bridge_host: str | None = None,
        bridge_port: int | None = None,
        runtime_root: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        instance_id = instance_id or _new_id("instance")
        _require_id(instance_id, "instance_id")
        worker_kind = _require_key(worker_kind, "worker_kind")
        if bridge_port is not None and not 1 <= bridge_port <= 65535:
            raise InvalidRecord("invalid_bridge_port")
        now = time.time()
        installation_id = self.installation_id()
        with self.transaction() as connection:
            if scope:
                self.require_scope(scope, connection=connection)
            connection.execute(
                "INSERT INTO instances(instance_id, installation_id, match_id, agent_id, perspective_id, "
                "worker_kind, status, bridge_host, bridge_port, runtime_root, metadata_json, created_unix, updated_unix) "
                "VALUES (?, ?, ?, ?, ?, ?, 'available', ?, ?, ?, ?, ?, ?)",
                (
                    instance_id, installation_id,
                    scope.match_id if scope else None,
                    scope.agent_id if scope else None,
                    scope.perspective_id if scope else None,
                    worker_kind, bridge_host, bridge_port, runtime_root, _json(metadata), now, now,
                ),
            )
            return dict(connection.execute("SELECT * FROM instances WHERE instance_id = ?", (instance_id,)).fetchone())

    def start_session(
        self,
        scope: MemoryScope,
        instance_id: str,
        *,
        session_id: str | None = None,
        loaded_save: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_id = session_id or _new_id("session")
        _require_id(session_id, "session_id")
        _require_id(instance_id, "instance_id")
        now = time.time()
        with self.transaction() as connection:
            self.require_scope(scope, connection=connection)
            instance = connection.execute(
                "SELECT * FROM instances WHERE instance_id = ?", (instance_id,)
            ).fetchone()
            if not instance:
                raise ScopeViolation("unknown_instance_id")
            for field, expected in (
                ("match_id", scope.match_id), ("agent_id", scope.agent_id),
                ("perspective_id", scope.perspective_id),
            ):
                if instance[field] not in (None, expected):
                    raise ScopeViolation("instance_scope_mismatch")
            connection.execute(
                "UPDATE instances SET match_id = ?, agent_id = ?, perspective_id = ?, status = 'running', "
                "updated_unix = ? WHERE instance_id = ?",
                (scope.match_id, scope.agent_id, scope.perspective_id, now, instance_id),
            )
            connection.execute(
                "INSERT INTO sessions(session_id, match_id, agent_id, perspective_id, instance_id, status, "
                "loaded_save, started_unix, metadata_json) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?)",
                (session_id, scope.match_id, scope.agent_id, scope.perspective_id, instance_id, loaded_save, now, _json(metadata)),
            )
            connection.execute(
                "UPDATE matches SET status = 'running', updated_unix = ? WHERE match_id = ?",
                (now, scope.match_id),
            )
            return dict(connection.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone())

    def close_session(self, session_id: str, *, status: str = "stopped") -> dict[str, Any]:
        _require_id(session_id, "session_id")
        status = _require_key(status, "session_status")
        now = time.time()
        with self.transaction() as connection:
            session = connection.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            if not session:
                raise ScopeViolation("unknown_session_id")
            connection.execute(
                "UPDATE sessions SET status = ?, stopped_unix = ? WHERE session_id = ?",
                (status, now, session_id),
            )
            connection.execute(
                "UPDATE instances SET status = 'available', updated_unix = ? WHERE instance_id = ?",
                (now, session["instance_id"]),
            )
            return dict(connection.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone())

    def require_session(
        self,
        scope: MemoryScope,
        session_id: str,
        *,
        connection: sqlite3.Connection,
        require_running: bool = True,
    ) -> sqlite3.Row:
        _require_id(session_id, "session_id")
        row = connection.execute(
            "SELECT * FROM sessions WHERE session_id = ? AND match_id = ? AND agent_id = ? AND perspective_id = ?",
            (session_id, scope.match_id, scope.agent_id, scope.perspective_id),
        ).fetchone()
        if not row:
            raise ScopeViolation("session_scope_mismatch")
        if require_running and row["status"] != "running":
            raise ScopeViolation("session_not_running")
        return row

    def scope_for_session(self, session_id: str) -> MemoryScope | None:
        _require_id(session_id, "session_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT match_id, agent_id, perspective_id FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        return MemoryScope(str(row["match_id"]), str(row["agent_id"]), str(row["perspective_id"]))

    def scopes_for_match(self, match_id: str, *, active_only: bool = False) -> list[MemoryScope]:
        _require_id(match_id, "match_id")
        query = "SELECT match_id, agent_id, perspective_id FROM perspectives WHERE match_id = ?"
        parameters: tuple[Any, ...] = (match_id,)
        if active_only:
            query += " AND status = 'active'"
        query += " ORDER BY created_unix"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            MemoryScope(str(row["match_id"]), str(row["agent_id"]), str(row["perspective_id"]))
            for row in rows
        ]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        _require_id(session_id, "session_id")
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    def _index_document(
        self,
        connection: sqlite3.Connection,
        scope: MemoryScope,
        *,
        document_kind: str,
        source_id: str,
        title: str,
        body: str,
        tags: str = "",
        importance: int = 50,
        created_unix: float | None = None,
    ) -> None:
        document_id = f"{document_kind}:{source_id}"
        connection.execute(
            "INSERT INTO search_documents(document_id, match_id, agent_id, perspective_id, document_kind, "
            "source_id, title, body, tags, importance, created_unix) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(document_id) DO UPDATE SET title = excluded.title, body = excluded.body, "
            "tags = excluded.tags, importance = excluded.importance",
            (
                document_id, scope.match_id, scope.agent_id, scope.perspective_id,
                document_kind, source_id, title, body, tags, importance, created_unix or time.time(),
            ),
        )

    def _require_event_scope(
        self,
        connection: sqlite3.Connection,
        scope: MemoryScope,
        event_id: str | None,
        field: str = "source_event_id",
    ) -> None:
        if not event_id:
            return
        _require_id(event_id, field)
        if not connection.execute(
            "SELECT 1 FROM events WHERE event_id = ? AND match_id = ? AND agent_id = ? AND perspective_id = ?",
            (event_id, scope.match_id, scope.agent_id, scope.perspective_id),
        ).fetchone():
            raise ScopeViolation("evidence_event_scope_mismatch")

    def _append_event(
        self,
        connection: sqlite3.Connection,
        scope: MemoryScope,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        session_id: str | None = None,
        source: str = "platform",
        turn: int | None = None,
        year: int | None = None,
        actor_id: str | None = None,
        subject_id: str | None = None,
        correlation_id: str | None = None,
        dedupe_key: str | None = None,
        importance: int = 50,
        search_text: str = "",
        observed_unix: float | None = None,
    ) -> str:
        self.require_scope(scope, connection=connection)
        event_type = _require_key(event_type, "event_type")
        source = _require_key(source, "event_source")
        if not 0 <= importance <= 100:
            raise InvalidRecord("invalid_event_importance")
        if session_id:
            self.require_session(scope, session_id, connection=connection, require_running=False)
        event_id = _new_id("event")
        now = time.time()
        try:
            connection.execute(
                "INSERT INTO events(event_id, match_id, agent_id, perspective_id, session_id, event_type, source, "
                "turn, year, actor_id, subject_id, correlation_id, dedupe_key, importance, payload_json, search_text, "
                "observed_unix, created_unix) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id, scope.match_id, scope.agent_id, scope.perspective_id, session_id,
                    event_type, source, turn, year, actor_id, subject_id, correlation_id, dedupe_key,
                    importance, _json(payload), search_text.strip(), observed_unix or now, now,
                ),
            )
        except sqlite3.IntegrityError:
            if not dedupe_key:
                raise
            row = connection.execute(
                "SELECT event_id FROM events WHERE match_id = ? AND agent_id = ? AND perspective_id = ? "
                "AND dedupe_key = ?",
                (scope.match_id, scope.agent_id, scope.perspective_id, dedupe_key),
            ).fetchone()
            if not row:
                raise
            return str(row["event_id"])
        if search_text.strip():
            self._index_document(
                connection, scope, document_kind="event", source_id=event_id,
                title=event_type, body=search_text.strip(), tags=source,
                importance=importance, created_unix=now,
            )
        return event_id

    def append_event(self, scope: MemoryScope, event_type: str, payload: Mapping[str, Any], **kwargs: Any) -> str:
        with self.transaction() as connection:
            return self._append_event(connection, scope, event_type, payload, **kwargs)

    def list_events(
        self,
        scope: MemoryScope,
        *,
        event_types: Sequence[str] = (),
        after_unix: float | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.require_scope(scope)
        limit = min(max(limit, 1), 500)
        clauses = ["match_id = ?", "agent_id = ?", "perspective_id = ?"]
        parameters: list[Any] = [scope.match_id, scope.agent_id, scope.perspective_id]
        if event_types:
            clauses.append("event_type IN (" + ",".join("?" for _ in event_types) + ")")
            parameters.extend(event_types)
        if after_unix is not None:
            clauses.append("created_unix > ?")
            parameters.append(after_unix)
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE " + " AND ".join(clauses) + " ORDER BY created_unix DESC LIMIT ?",
                parameters,
            ).fetchall()
        return [self._decode_row(row, "payload_json") for row in rows]

    def projection_cursor(self, scope: MemoryScope, projector: str) -> dict[str, Any]:
        """Return a projector watermark for exactly one derived graph namespace."""
        projector = _require_key(projector, "projector")
        namespace = self.graph_namespace(scope)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM projection_cursors WHERE projector = ? AND namespace = ?",
                (projector, namespace),
            ).fetchone()
        if row:
            return dict(row)
        return {
            "projector": projector,
            "namespace": namespace,
            "match_id": scope.match_id,
            "agent_id": scope.agent_id,
            "perspective_id": scope.perspective_id,
            "last_event_id": None,
            "last_created_unix": None,
            "status": "new",
            "last_error": None,
        }

    def events_after_projection_cursor(
        self,
        scope: MemoryScope,
        projector: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read immutable events after a stable (time,event-id) watermark."""
        cursor = self.projection_cursor(scope, projector)
        clauses = ["match_id = ?", "agent_id = ?", "perspective_id = ?"]
        parameters: list[Any] = [scope.match_id, scope.agent_id, scope.perspective_id]
        created = cursor.get("last_created_unix")
        event_id = cursor.get("last_event_id")
        if created is not None and event_id:
            clauses.append("(created_unix > ? OR (created_unix = ? AND event_id > ?))")
            parameters.extend((created, created, event_id))
        parameters.append(min(max(limit, 1), 500))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE " + " AND ".join(clauses)
                + " ORDER BY created_unix ASC, event_id ASC LIMIT ?",
                parameters,
            ).fetchall()
        return [self._decode_row(row, "payload_json") for row in rows]

    def advance_projection_cursor(
        self,
        scope: MemoryScope,
        projector: str,
        event: Mapping[str, Any] | None,
        *,
        status: str = "ready",
        last_error: str | None = None,
    ) -> dict[str, Any]:
        """Advance after a completed projection, or record a failure without advancing."""
        projector = _require_key(projector, "projector")
        status = _require_key(status, "projection_status")
        namespace = self.graph_namespace(scope)
        event_id: str | None = None
        created_unix: float | None = None
        if event is not None:
            event_id = str(event.get("event_id") or "")
            _require_id(event_id, "event_id")
            created_unix = float(event.get("created_unix"))
            if event.get("match_id") != scope.match_id \
                    or event.get("agent_id") != scope.agent_id \
                    or event.get("perspective_id") != scope.perspective_id:
                raise ScopeViolation("projection_event_scope_mismatch")
        now = time.time()
        with self.transaction() as connection:
            self.require_scope(scope, connection=connection)
            existing = connection.execute(
                "SELECT * FROM projection_cursors WHERE projector = ? AND namespace = ?",
                (projector, namespace),
            ).fetchone()
            if event is None and existing:
                event_id = existing["last_event_id"]
                created_unix = existing["last_created_unix"]
            connection.execute(
                "INSERT INTO projection_cursors(projector, namespace, match_id, agent_id, perspective_id, "
                "last_event_id, last_created_unix, status, last_error, updated_unix) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(projector, namespace) DO UPDATE SET "
                "last_event_id = excluded.last_event_id, last_created_unix = excluded.last_created_unix, "
                "status = excluded.status, last_error = excluded.last_error, updated_unix = excluded.updated_unix",
                (
                    projector, namespace, scope.match_id, scope.agent_id, scope.perspective_id,
                    event_id, created_unix, status, last_error, now,
                ),
            )
            return dict(connection.execute(
                "SELECT * FROM projection_cursors WHERE projector = ? AND namespace = ?",
                (projector, namespace),
            ).fetchone())

    def reset_projection_cursor(self, scope: MemoryScope, projector: str) -> bool:
        """Forget only the local watermark; the external graph is never mutated here."""
        projector = _require_key(projector, "projector")
        namespace = self.graph_namespace(scope)
        with self.transaction() as connection:
            self.require_scope(scope, connection=connection)
            cursor = connection.execute(
                "DELETE FROM projection_cursors WHERE projector = ? AND namespace = ?",
                (projector, namespace),
            )
            return cursor.rowcount > 0

    def put_fact(
        self,
        scope: MemoryScope,
        session_id: str,
        observed_revision: str,
        fact_key: str,
        value: str,
        *,
        category: str = "general",
        subject: str = "",
        confidence: float = 1.0,
        observed_turn: int | None = None,
        observed_year: int | None = None,
        source_event_id: str | None = None,
    ) -> dict[str, Any]:
        fact_key = _require_key(fact_key, "fact_key")
        category = _require_key(category, "fact_category")
        value = _bounded_text(value, "fact_value", 8000)
        subject = _bounded_text(subject, "fact_subject", 240, required=False)
        if SESSION_LOCAL_REFERENCE.search(value):
            raise InvalidRecord("session_local_knowledge_reference")
        if not observed_revision or len(observed_revision) > 160:
            raise InvalidRecord("invalid_observed_revision")
        if not 0.0 <= confidence <= 1.0:
            raise InvalidRecord("invalid_confidence")
        now = time.time()
        with self.transaction() as connection:
            self.require_scope(scope, connection=connection)
            self.require_session(scope, session_id, connection=connection)
            self._require_event_scope(connection, scope, source_event_id)
            current = connection.execute(
                "SELECT * FROM facts WHERE match_id = ? AND agent_id = ? AND perspective_id = ? "
                "AND fact_key = ? AND status = 'current'",
                (scope.match_id, scope.agent_id, scope.perspective_id, fact_key),
            ).fetchone()
            fact_revision = int(current["fact_revision"]) + 1 if current else 1
            fact_id = _new_id("fact")
            if current:
                connection.execute(
                    "UPDATE facts SET status = 'superseded' WHERE fact_id = ?",
                    (current["fact_id"],),
                )
            connection.execute(
                "INSERT INTO facts(fact_id, match_id, agent_id, perspective_id, session_id, fact_key, "
                "fact_revision, category, subject, value, confidence, status, observed_revision, observed_turn, "
                "observed_year, source_event_id, supersedes_fact_id, created_unix) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'current', ?, ?, ?, ?, ?, ?)",
                (
                    fact_id, scope.match_id, scope.agent_id, scope.perspective_id, session_id,
                    fact_key, fact_revision, category, subject, value, confidence, observed_revision,
                    observed_turn, observed_year, source_event_id,
                    current["fact_id"] if current else None, now,
                ),
            )
            if current:
                connection.execute(
                    "UPDATE facts SET superseded_by_fact_id = ? WHERE fact_id = ?",
                    (fact_id, current["fact_id"]),
                )
            event_id = self._append_event(
                connection, scope, "memory.fact_recorded",
                {"fact_id": fact_id, "fact_key": fact_key, "fact_revision": fact_revision, "category": category},
                session_id=session_id, turn=observed_turn, year=observed_year,
                subject_id=fact_id, importance=60, search_text=f"{fact_key} {subject} {value}",
            )
            self._index_document(
                connection, scope, document_kind="fact", source_id=fact_id,
                title=f"{category}: {fact_key}", body=f"{subject}\n{value}".strip(),
                tags=f"fact {category}", importance=60, created_unix=now,
            )
            row = connection.execute("SELECT * FROM facts WHERE fact_id = ?", (fact_id,)).fetchone()
            result = dict(row)
            result["event_id"] = event_id
            result["updated_existing"] = current is not None
            return result

    def get_facts(
        self,
        scope: MemoryScope,
        *,
        fact_key: str | None = None,
        include_history: bool = False,
        category: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        self.require_scope(scope)
        clauses = ["match_id = ?", "agent_id = ?", "perspective_id = ?"]
        parameters: list[Any] = [scope.match_id, scope.agent_id, scope.perspective_id]
        if not include_history:
            clauses.append("status = 'current'")
        if fact_key:
            clauses.append("fact_key = ?")
            parameters.append(_require_key(fact_key, "fact_key"))
        if category:
            clauses.append("category = ?")
            parameters.append(_require_key(category, "fact_category"))
        parameters.append(min(max(limit, 1), 2000))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM facts WHERE " + " AND ".join(clauses)
                + " ORDER BY fact_key, fact_revision DESC LIMIT ?", parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def import_legacy_knowledge(
        self,
        scope: MemoryScope,
        *,
        source_path: str,
        content_sha256: str,
        ledger: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Import the former JSON ledger once while preserving its revision history."""
        source_path = _bounded_text(source_path, "legacy_source_path", 4096)
        content_sha256 = _bounded_text(content_sha256, "legacy_content_sha256", 128)
        if ledger.get("version") != 1 or ledger.get("match_id") != scope.match_id:
            raise InvalidRecord("invalid_legacy_knowledge_ledger")
        raw_entries = ledger.get("entries")
        raw_history = ledger.get("history")
        if not isinstance(raw_entries, Mapping) or not isinstance(raw_history, list):
            raise InvalidRecord("invalid_legacy_knowledge_ledger")

        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for raw in raw_history:
            if not isinstance(raw, Mapping) or not isinstance(raw.get("key"), str):
                continue
            grouped.setdefault(str(raw["key"]), []).append(raw)
        for key, raw in raw_entries.items():
            if isinstance(key, str) and isinstance(raw, Mapping) and key not in grouped:
                grouped[key] = [raw]

        imported = 0
        skipped = 0
        now = time.time()
        with self.transaction() as connection:
            self.require_scope(scope, connection=connection)
            previous_import = connection.execute(
                "SELECT result_json, content_sha256 FROM legacy_imports WHERE source_path = ?",
                (source_path,),
            ).fetchone()
            if previous_import:
                result = json.loads(str(previous_import["result_json"]))
                result["already_imported"] = True
                result["source_changed_since_import"] = previous_import["content_sha256"] != content_sha256
                return result
            existing_count = int(connection.execute(
                "SELECT COUNT(*) FROM facts WHERE match_id = ? AND agent_id = ? AND perspective_id = ?",
                (scope.match_id, scope.agent_id, scope.perspective_id),
            ).fetchone()[0])
            if existing_count:
                raise InvalidRecord("legacy_import_requires_empty_fact_scope")

            for raw_key, raw_revisions in sorted(grouped.items()):
                imported_before_key = imported
                connection.execute("SAVEPOINT legacy_fact_key")
                try:
                    fact_key = _require_key(raw_key, "fact_key")
                    ordered = sorted(
                        raw_revisions,
                        key=lambda item: (
                            int(item.get("knowledge_revision", 0) or 0),
                            float(item.get("recorded_unix", 0) or 0),
                        ),
                    )
                    previous_fact_id: str | None = None
                    for index, raw in enumerate(ordered, start=1):
                        category = _require_key(str(raw.get("category") or "general"), "fact_category")
                        value = _bounded_text(str(raw.get("value") or ""), "fact_value", 8000)
                        subject = _bounded_text(
                            str(raw.get("subject") or ""), "fact_subject", 240, required=False,
                        )
                        if SESSION_LOCAL_REFERENCE.search(value):
                            raise InvalidRecord("session_local_knowledge_reference")
                        fact_id = _new_id("fact")
                        is_current = index == len(ordered)
                        created_unix = float(raw.get("recorded_unix") or now)
                        connection.execute(
                            "INSERT INTO facts(fact_id, match_id, agent_id, perspective_id, session_id, fact_key, "
                            "fact_revision, category, subject, value, confidence, status, observed_revision, "
                            "observed_turn, observed_year, supersedes_fact_id, created_unix) "
                            "VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, 1.0, ?, ?, ?, ?, ?, ?)",
                            (
                                fact_id, scope.match_id, scope.agent_id, scope.perspective_id,
                                fact_key, index, category, subject, value,
                                "current" if is_current else "superseded",
                                str(raw.get("observed_revision") or "legacy-import"),
                                raw.get("observed_turn"), raw.get("observed_year"),
                                previous_fact_id, created_unix,
                            ),
                        )
                        if previous_fact_id:
                            connection.execute(
                                "UPDATE facts SET superseded_by_fact_id = ? WHERE fact_id = ?",
                                (fact_id, previous_fact_id),
                            )
                        self._append_event(
                            connection,
                            scope,
                            "legacy_import.fact_imported",
                            {
                                "fact_id": fact_id,
                                "fact_key": fact_key,
                                "fact_revision": index,
                                "legacy_session_id": raw.get("session_id"),
                            },
                            source="legacy_import",
                            turn=raw.get("observed_turn"),
                            year=raw.get("observed_year"),
                            dedupe_key=f"legacy-fact:{fact_key}:{index}:{content_sha256[:16]}",
                            importance=50,
                            search_text=f"{fact_key} {subject} {value}",
                            observed_unix=created_unix,
                        )
                        if is_current:
                            self._index_document(
                                connection,
                                scope,
                                document_kind="fact",
                                source_id=fact_id,
                                title=f"{category}: {fact_key}",
                                body=f"{subject}\n{value}".strip(),
                                tags="fact legacy_import",
                                importance=60,
                                created_unix=created_unix,
                            )
                        previous_fact_id = fact_id
                        imported += 1
                    connection.execute("RELEASE SAVEPOINT legacy_fact_key")
                except (InvalidRecord, TypeError, ValueError):
                    connection.execute("ROLLBACK TO SAVEPOINT legacy_fact_key")
                    connection.execute("RELEASE SAVEPOINT legacy_fact_key")
                    imported = imported_before_key
                    skipped += len(raw_revisions)

            result = {
                "ok": True,
                "source_path": source_path,
                "content_sha256": content_sha256,
                "facts_imported": imported,
                "records_skipped": skipped,
                "already_imported": False,
            }
            connection.execute(
                "INSERT INTO legacy_imports(source_path, match_id, imported_unix, content_sha256, result_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (source_path, scope.match_id, now, content_sha256, _json(result)),
            )
            self._append_event(
                connection,
                scope,
                "legacy_import.knowledge_completed",
                result,
                source="legacy_import",
                dedupe_key=f"legacy-ledger:{content_sha256}",
                importance=40,
            )
            return result

    def upsert_actor(
        self,
        match_id: str,
        stable_key: str,
        display_name: str,
        *,
        actor_id: str | None = None,
        controller_kind: str = "unknown",
        controller_ref: str | None = None,
        faction_id: int | None = None,
        faction_name: str | None = None,
        network_player_id: str | None = None,
        network_player_name: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        _require_id(match_id, "match_id")
        stable_key = _require_key(stable_key, "actor_stable_key")
        display_name = _bounded_text(display_name, "actor_name", 160)
        controller_kind = _require_key(controller_kind, "controller_kind")
        now = time.time()
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM actors WHERE match_id = ? AND stable_key = ?", (match_id, stable_key)
            ).fetchone()
            if existing:
                connection.execute(
                    "UPDATE actors SET display_name = ?, controller_kind = ?, controller_ref = ?, faction_id = ?, "
                    "faction_name = ?, network_player_id = ?, network_player_name = ?, last_observed_unix = ?, "
                    "metadata_json = ? WHERE actor_id = ?",
                    (
                        display_name, controller_kind, controller_ref, faction_id, faction_name,
                        network_player_id, network_player_name, now, _json(metadata), existing["actor_id"],
                    ),
                )
                actor_id = str(existing["actor_id"])
            else:
                actor_id = actor_id or _new_id("actor")
                _require_id(actor_id, "actor_id")
                connection.execute(
                    "INSERT INTO actors(actor_id, match_id, stable_key, display_name, controller_kind, controller_ref, "
                    "faction_id, faction_name, network_player_id, network_player_name, first_observed_unix, "
                    "last_observed_unix, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        actor_id, match_id, stable_key, display_name, controller_kind, controller_ref,
                        faction_id, faction_name, network_player_id, network_player_name, now, now, _json(metadata),
                    ),
                )
            return dict(connection.execute("SELECT * FROM actors WHERE actor_id = ?", (actor_id,)).fetchone())

    def record_chat(
        self,
        scope: MemoryScope,
        message_uid: str,
        content: str,
        *,
        session_id: str | None,
        direction: str,
        channel: str,
        sender_actor_id: str | None = None,
        recipient_actor_id: str | None = None,
        sender_faction_id: int | None = None,
        recipient_faction_id: int | None = None,
        turn: int | None = None,
        year: int | None = None,
        received_unix: float | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        message_uid = _bounded_text(message_uid, "message_uid", 160)
        content = _bounded_text(content, "chat_content", 4000)
        direction = _require_key(direction, "chat_direction")
        channel = _require_key(channel, "chat_channel")
        now = received_unix or time.time()
        with self.transaction() as connection:
            self.require_scope(scope, connection=connection)
            if session_id:
                self.require_session(scope, session_id, connection=connection, require_running=False)
            existing = connection.execute(
                "SELECT * FROM chat_messages WHERE match_id = ? AND agent_id = ? AND perspective_id = ? "
                "AND message_uid = ?",
                (scope.match_id, scope.agent_id, scope.perspective_id, message_uid),
            ).fetchone()
            if existing:
                result = dict(existing)
                result["deduplicated"] = True
                return result
            chat_id = _new_id("chat")
            connection.execute(
                "INSERT INTO chat_messages(chat_id, message_uid, match_id, agent_id, perspective_id, session_id, "
                "direction, channel, sender_actor_id, recipient_actor_id, sender_faction_id, recipient_faction_id, "
                "content, turn, year, received_unix, acknowledged_unix, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    chat_id, message_uid, scope.match_id, scope.agent_id, scope.perspective_id, session_id,
                    direction, channel, sender_actor_id, recipient_actor_id, sender_faction_id,
                    recipient_faction_id, content, turn, year, now,
                    now if direction in {"outbound", "outgoing"} else None,
                    _json(metadata),
                ),
            )
            event_id = self._append_event(
                connection, scope, "chat.message", {"chat_id": chat_id, "direction": direction, "channel": channel},
                session_id=session_id, turn=turn, year=year, actor_id=sender_actor_id,
                subject_id=recipient_actor_id, dedupe_key=f"chat:{message_uid}", importance=70,
                search_text=content, observed_unix=now,
            )
            self._index_document(
                connection, scope, document_kind="chat", source_id=chat_id,
                title=f"{direction} {channel} chat", body=content,
                tags="chat untrusted_in_game_speech", importance=70, created_unix=now,
            )
            result = dict(connection.execute("SELECT * FROM chat_messages WHERE chat_id = ?", (chat_id,)).fetchone())
            result["event_id"] = event_id
            result["untrusted_in_game_speech"] = True
            return result

    def list_chat(
        self,
        scope: MemoryScope,
        *,
        unread_only: bool = False,
        mark_acknowledged: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.require_scope(scope)
        limit = min(max(limit, 1), 500)
        clauses = ["match_id = ?", "agent_id = ?", "perspective_id = ?"]
        parameters: list[Any] = [scope.match_id, scope.agent_id, scope.perspective_id]
        if unread_only:
            clauses.append("acknowledged_unix IS NULL")
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM chat_messages WHERE " + " AND ".join(clauses)
                + " ORDER BY received_unix ASC LIMIT ?", (*parameters, limit),
            ).fetchall()
            if mark_acknowledged and rows:
                now = time.time()
                connection.executemany(
                    "UPDATE chat_messages SET acknowledged_unix = COALESCE(acknowledged_unix, ?) WHERE chat_id = ?",
                    ((now, row["chat_id"]) for row in rows),
                )
        return [self._decode_row(row, "metadata_json") for row in rows]

    def set_relationship(
        self,
        scope: MemoryScope,
        actor_id: str,
        *,
        affinity: int = 0,
        trust: int = 0,
        respect: int = 0,
        threat: int = 0,
        grievance: int = 0,
        obligation: int = 0,
        confidence: float = 0.5,
        reasons: Sequence[str] = (),
        source_event_id: str | None = None,
        session_id: str | None = None,
        turn: int | None = None,
        year: int | None = None,
    ) -> dict[str, Any]:
        _require_id(actor_id, "actor_id")
        metrics = (affinity, trust, respect, threat, obligation)
        if any(value < -100 or value > 100 for value in metrics) or not 0 <= grievance <= 100:
            raise InvalidRecord("invalid_relationship_metric")
        if not 0.0 <= confidence <= 1.0:
            raise InvalidRecord("invalid_confidence")
        now = time.time()
        with self.transaction() as connection:
            self.require_scope(scope, connection=connection)
            if session_id:
                self.require_session(scope, session_id, connection=connection)
            self._require_event_scope(connection, scope, source_event_id)
            actor = connection.execute(
                "SELECT 1 FROM actors WHERE actor_id = ? AND match_id = ?", (actor_id, scope.match_id)
            ).fetchone()
            if not actor:
                raise ScopeViolation("actor_scope_mismatch")
            current = connection.execute(
                "SELECT * FROM relationships WHERE match_id = ? AND agent_id = ? AND perspective_id = ? "
                "AND actor_id = ? AND status = 'current'",
                (scope.match_id, scope.agent_id, scope.perspective_id, actor_id),
            ).fetchone()
            revision = int(current["relationship_revision"]) + 1 if current else 1
            relationship_id = _new_id("relationship")
            if current:
                connection.execute(
                    "UPDATE relationships SET status = 'superseded' WHERE relationship_id = ?",
                    (current["relationship_id"],),
                )
            connection.execute(
                "INSERT INTO relationships(relationship_id, match_id, agent_id, perspective_id, actor_id, "
                "relationship_revision, affinity, trust, respect, threat, grievance, obligation, confidence, "
                "reasons_json, status, supersedes_relationship_id, source_event_id, created_unix) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'current', ?, ?, ?)",
                (
                    relationship_id, scope.match_id, scope.agent_id, scope.perspective_id, actor_id,
                    revision, affinity, trust, respect, threat, grievance, obligation, confidence,
                    _json(list(reasons)), current["relationship_id"] if current else None, source_event_id, now,
                ),
            )
            if current:
                connection.execute(
                    "UPDATE relationships SET superseded_by_relationship_id = ? WHERE relationship_id = ?",
                    (relationship_id, current["relationship_id"]),
                )
            self._append_event(
                connection, scope, "memory.relationship_updated",
                {"relationship_id": relationship_id, "actor_id": actor_id, "revision": revision},
                session_id=session_id, turn=turn, year=year, actor_id=actor_id,
                importance=70, search_text=" ".join(reasons),
            )
            self._index_document(
                connection, scope, document_kind="relationship", source_id=relationship_id,
                title=f"Relationship with {actor_id}", body="\n".join(reasons),
                tags="relationship trust affinity respect threat grievance obligation", importance=70,
            )
            return self._decode_row(
                connection.execute(
                    "SELECT * FROM relationships WHERE relationship_id = ?", (relationship_id,)
                ).fetchone(), "reasons_json",
            )

    def record_claim(
        self,
        scope: MemoryScope,
        topic: str,
        content: str,
        *,
        session_id: str | None = None,
        asserted_by_actor_id: str | None = None,
        about_actor_id: str | None = None,
        confidence: float = 0.5,
        status: str = "unverified",
        source_event_id: str | None = None,
        turn: int | None = None,
        year: int | None = None,
    ) -> dict[str, Any]:
        topic = _require_key(topic, "claim_topic")
        content = _bounded_text(content, "claim_content", 8000)
        status = _require_key(status, "claim_status")
        if not 0.0 <= confidence <= 1.0:
            raise InvalidRecord("invalid_confidence")
        claim_id = _new_id("claim")
        now = time.time()
        with self.transaction() as connection:
            self.require_scope(scope, connection=connection)
            if session_id:
                self.require_session(scope, session_id, connection=connection, require_running=False)
            self._require_event_scope(connection, scope, source_event_id)
            for actor_id in (asserted_by_actor_id, about_actor_id):
                if actor_id and not connection.execute(
                    "SELECT 1 FROM actors WHERE actor_id = ? AND match_id = ?",
                    (actor_id, scope.match_id),
                ).fetchone():
                    raise ScopeViolation("actor_scope_mismatch")
            connection.execute(
                "INSERT INTO claims(claim_id, match_id, agent_id, perspective_id, session_id, "
                "asserted_by_actor_id, about_actor_id, topic, content, confidence, status, source_event_id, "
                "turn, year, created_unix) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    claim_id, scope.match_id, scope.agent_id, scope.perspective_id, session_id,
                    asserted_by_actor_id, about_actor_id, topic, content, confidence, status,
                    source_event_id, turn, year, now,
                ),
            )
            event_id = self._append_event(
                connection, scope, "memory.claim_recorded",
                {"claim_id": claim_id, "topic": topic, "status": status},
                session_id=session_id, turn=turn, year=year, actor_id=asserted_by_actor_id,
                subject_id=about_actor_id, importance=55, search_text=f"{topic} {content}",
            )
            self._index_document(
                connection, scope, document_kind="claim", source_id=claim_id,
                title=f"Claim: {topic}", body=content,
                tags=f"claim {status} untrusted_assertion", importance=55, created_unix=now,
            )
            result = dict(connection.execute("SELECT * FROM claims WHERE claim_id = ?", (claim_id,)).fetchone())
            result["event_id"] = event_id
            return result

    def set_belief(
        self,
        scope: MemoryScope,
        topic: str,
        content: str,
        *,
        confidence: float,
        evidence: Sequence[tuple[str, str, float]] = (),
        session_id: str | None = None,
        turn: int | None = None,
        year: int | None = None,
    ) -> dict[str, Any]:
        topic = _require_key(topic, "belief_topic")
        content = _bounded_text(content, "belief_content", 8000)
        if not 0.0 <= confidence <= 1.0:
            raise InvalidRecord("invalid_confidence")
        belief_id = _new_id("belief")
        now = time.time()
        with self.transaction() as connection:
            self.require_scope(scope, connection=connection)
            if session_id:
                self.require_session(scope, session_id, connection=connection)
            current = connection.execute(
                "SELECT * FROM beliefs WHERE match_id = ? AND agent_id = ? AND perspective_id = ? "
                "AND topic = ? AND status = 'current'",
                (scope.match_id, scope.agent_id, scope.perspective_id, topic),
            ).fetchone()
            revision = int(current["belief_revision"]) + 1 if current else 1
            if current:
                connection.execute(
                    "UPDATE beliefs SET status = 'superseded' WHERE belief_id = ?",
                    (current["belief_id"],),
                )
            connection.execute(
                "INSERT INTO beliefs(belief_id, match_id, agent_id, perspective_id, topic, belief_revision, "
                "content, confidence, status, supersedes_belief_id, created_unix) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'current', ?, ?)",
                (
                    belief_id, scope.match_id, scope.agent_id, scope.perspective_id, topic,
                    revision, content, confidence, current["belief_id"] if current else None, now,
                ),
            )
            if current:
                connection.execute(
                    "UPDATE beliefs SET superseded_by_belief_id = ? WHERE belief_id = ?",
                    (belief_id, current["belief_id"]),
                )
            for event_id, stance, weight in evidence:
                _require_id(event_id, "event_id")
                stance = _require_key(stance, "evidence_stance")
                if not 0.0 <= weight <= 1.0:
                    raise InvalidRecord("invalid_evidence_weight")
                self._require_event_scope(connection, scope, event_id, "event_id")
                connection.execute(
                    "INSERT INTO belief_evidence(belief_id, event_id, stance, weight) VALUES (?, ?, ?, ?)",
                    (belief_id, event_id, stance, weight),
                )
            self._append_event(
                connection, scope, "memory.belief_updated",
                {"belief_id": belief_id, "topic": topic, "revision": revision, "confidence": confidence},
                session_id=session_id, turn=turn, year=year, subject_id=belief_id,
                importance=65, search_text=f"{topic} {content}",
            )
            self._index_document(
                connection, scope, document_kind="belief", source_id=belief_id,
                title=f"Belief: {topic}", body=content,
                tags="belief inference suspicion", importance=65, created_unix=now,
            )
            result = dict(connection.execute("SELECT * FROM beliefs WHERE belief_id = ?", (belief_id,)).fetchone())
            result["evidence"] = [
                dict(row) for row in connection.execute(
                    "SELECT event_id, stance, weight FROM belief_evidence WHERE belief_id = ? ORDER BY event_id",
                    (belief_id,),
                ).fetchall()
            ]
            return result

    def put_commitment(
        self,
        scope: MemoryScope,
        commitment_key: str,
        title: str,
        terms: str,
        *,
        status: str = "proposed",
        parties: Sequence[tuple[str, str]] = (),
        due_turn: int | None = None,
        due_year: int | None = None,
        source_event_id: str | None = None,
        resolution_event_id: str | None = None,
        session_id: str | None = None,
        turn: int | None = None,
        year: int | None = None,
    ) -> dict[str, Any]:
        commitment_key = _require_key(commitment_key, "commitment_key")
        title = _bounded_text(title, "commitment_title", 200)
        terms = _bounded_text(terms, "commitment_terms", 8000)
        status = _require_key(status, "commitment_status")
        commitment_id = _new_id("commitment")
        now = time.time()
        with self.transaction() as connection:
            self.require_scope(scope, connection=connection)
            if session_id:
                self.require_session(scope, session_id, connection=connection)
            self._require_event_scope(connection, scope, source_event_id)
            self._require_event_scope(connection, scope, resolution_event_id, "resolution_event_id")
            current = connection.execute(
                "SELECT * FROM commitments WHERE match_id = ? AND agent_id = ? AND perspective_id = ? "
                "AND commitment_key = ? AND status != 'superseded'",
                (scope.match_id, scope.agent_id, scope.perspective_id, commitment_key),
            ).fetchone()
            revision = int(current["commitment_revision"]) + 1 if current else 1
            if current:
                connection.execute(
                    "UPDATE commitments SET status = 'superseded' WHERE commitment_id = ?",
                    (current["commitment_id"],),
                )
            connection.execute(
                "INSERT INTO commitments(commitment_id, match_id, agent_id, perspective_id, commitment_key, "
                "commitment_revision, title, terms, status, due_turn, due_year, source_event_id, "
                "resolution_event_id, supersedes_commitment_id, created_unix) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    commitment_id, scope.match_id, scope.agent_id, scope.perspective_id, commitment_key,
                    revision, title, terms, status, due_turn, due_year, source_event_id,
                    resolution_event_id, current["commitment_id"] if current else None, now,
                ),
            )
            if current:
                connection.execute(
                    "UPDATE commitments SET superseded_by_commitment_id = ? WHERE commitment_id = ?",
                    (commitment_id, current["commitment_id"]),
                )
            for actor_id, role in parties:
                _require_id(actor_id, "actor_id")
                role = _require_key(role, "commitment_party_role")
                if not connection.execute(
                    "SELECT 1 FROM actors WHERE actor_id = ? AND match_id = ?", (actor_id, scope.match_id)
                ).fetchone():
                    raise ScopeViolation("actor_scope_mismatch")
                connection.execute(
                    "INSERT INTO commitment_parties(commitment_id, actor_id, role) VALUES (?, ?, ?)",
                    (commitment_id, actor_id, role),
                )
            self._append_event(
                connection, scope, "memory.commitment_updated",
                {"commitment_id": commitment_id, "commitment_key": commitment_key, "revision": revision, "status": status},
                session_id=session_id, turn=turn, year=year, subject_id=commitment_id,
                importance=75, search_text=f"{title} {terms}",
            )
            self._index_document(
                connection, scope, document_kind="commitment", source_id=commitment_id,
                title=title, body=terms, tags=f"commitment {status}", importance=75, created_unix=now,
            )
            result = dict(connection.execute(
                "SELECT * FROM commitments WHERE commitment_id = ?", (commitment_id,)
            ).fetchone())
            result["parties"] = [
                dict(row) for row in connection.execute(
                    "SELECT actor_id, role FROM commitment_parties WHERE commitment_id = ? ORDER BY actor_id, role",
                    (commitment_id,),
                ).fetchall()
            ]
            return result

    def add_goal(
        self,
        scope: MemoryScope,
        title: str,
        description: str,
        *,
        goal_key: str | None = None,
        priority: int = 50,
        status: str = "active",
        due_turn: int | None = None,
        due_year: int | None = None,
        trigger: Mapping[str, Any] | None = None,
        parent_goal_id: str | None = None,
        source_event_id: str | None = None,
        session_id: str | None = None,
        turn: int | None = None,
        year: int | None = None,
    ) -> dict[str, Any]:
        title = _bounded_text(title, "goal_title", 200)
        description = _bounded_text(description, "goal_description", 4000)
        if not 0 <= priority <= 100:
            raise InvalidRecord("invalid_goal_priority")
        goal_key = _require_key(goal_key or _new_id("goalkey"), "goal_key")
        status = _require_key(status, "goal_status")
        goal_id = _new_id("goal")
        now = time.time()
        with self.transaction() as connection:
            self.require_scope(scope, connection=connection)
            if session_id:
                self.require_session(scope, session_id, connection=connection)
            self._require_event_scope(connection, scope, source_event_id)
            if parent_goal_id:
                _require_id(parent_goal_id, "parent_goal_id")
                if not connection.execute(
                    "SELECT 1 FROM goals WHERE goal_id = ? AND match_id = ? AND agent_id = ? AND perspective_id = ?",
                    (parent_goal_id, scope.match_id, scope.agent_id, scope.perspective_id),
                ).fetchone():
                    raise ScopeViolation("parent_goal_scope_mismatch")
            current = connection.execute(
                "SELECT * FROM goals WHERE match_id = ? AND agent_id = ? AND perspective_id = ? "
                "AND goal_key = ? AND status != 'superseded'",
                (scope.match_id, scope.agent_id, scope.perspective_id, goal_key),
            ).fetchone()
            revision = int(current["goal_revision"]) + 1 if current else 1
            if current:
                connection.execute(
                    "UPDATE goals SET status = 'superseded' WHERE goal_id = ?", (current["goal_id"],)
                )
            connection.execute(
                "INSERT INTO goals(goal_id, match_id, agent_id, perspective_id, goal_key, goal_revision, title, "
                "description, priority, status, due_turn, due_year, trigger_json, parent_goal_id, source_event_id, "
                "supersedes_goal_id, created_unix) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    goal_id, scope.match_id, scope.agent_id, scope.perspective_id, goal_key, revision,
                    title, description, priority, status, due_turn, due_year, _json(trigger), parent_goal_id,
                    source_event_id, current["goal_id"] if current else None, now,
                ),
            )
            if current:
                connection.execute(
                    "UPDATE goals SET superseded_by_goal_id = ? WHERE goal_id = ?",
                    (goal_id, current["goal_id"]),
                )
            self._append_event(
                connection, scope, "memory.goal_updated",
                {"goal_id": goal_id, "goal_key": goal_key, "revision": revision, "priority": priority, "status": status},
                session_id=session_id, turn=turn, year=year, subject_id=goal_id, importance=priority,
                search_text=f"{title} {description}",
            )
            self._index_document(
                connection, scope, document_kind="goal", source_id=goal_id,
                title=title, body=description, tags=f"goal {status}", importance=priority, created_unix=now,
            )
            return self._decode_row(
                connection.execute("SELECT * FROM goals WHERE goal_id = ?", (goal_id,)).fetchone(),
                "trigger_json",
            )

    def add_summary(
        self,
        scope: MemoryScope,
        section: str,
        content: str,
        *,
        through_event_id: str | None = None,
        session_id: str | None = None,
        turn: int | None = None,
        year: int | None = None,
    ) -> dict[str, Any]:
        section = _require_key(section, "summary_section")
        content = _bounded_text(content, "summary_content", 24000)
        token_estimate = max(1, (len(content) + 3) // 4)
        summary_id = _new_id("summary")
        now = time.time()
        with self.transaction() as connection:
            self.require_scope(scope, connection=connection)
            if session_id:
                self.require_session(scope, session_id, connection=connection)
            self._require_event_scope(connection, scope, through_event_id, "through_event_id")
            current = connection.execute(
                "SELECT * FROM summaries WHERE match_id = ? AND agent_id = ? AND perspective_id = ? "
                "AND section = ? AND status = 'current'",
                (scope.match_id, scope.agent_id, scope.perspective_id, section),
            ).fetchone()
            if current:
                connection.execute(
                    "UPDATE summaries SET status = 'superseded' WHERE summary_id = ?",
                    (current["summary_id"],),
                )
            connection.execute(
                "INSERT INTO summaries(summary_id, match_id, agent_id, perspective_id, section, content, "
                "token_estimate, status, through_event_id, supersedes_summary_id, created_unix) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'current', ?, ?, ?)",
                (
                    summary_id, scope.match_id, scope.agent_id, scope.perspective_id, section, content,
                    token_estimate, through_event_id, current["summary_id"] if current else None, now,
                ),
            )
            if current:
                connection.execute(
                    "UPDATE summaries SET superseded_by_summary_id = ? WHERE summary_id = ?",
                    (summary_id, current["summary_id"]),
                )
            self._index_document(
                connection, scope, document_kind="summary", source_id=summary_id,
                title=f"{section} summary", body=content, tags=f"summary {section}", importance=80,
                created_unix=now,
            )
            event_id = self._append_event(
                connection,
                scope,
                "memory.summary_updated",
                {"summary_id": summary_id, "section": section, "through_event_id": through_event_id},
                session_id=session_id,
                turn=turn,
                year=year,
                subject_id=summary_id,
                importance=80,
                search_text=f"{section} {content}",
            )
            budget = connection.execute(
                "SELECT max_tokens FROM memory_budgets WHERE section = ?", (section,)
            ).fetchone()
            result = dict(connection.execute(
                "SELECT * FROM summaries WHERE summary_id = ?", (summary_id,)
            ).fetchone())
            result["event_id"] = event_id
            if budget and token_estimate > int(budget["max_tokens"]):
                result["compaction_required"] = True
                result["max_tokens"] = int(budget["max_tokens"])
            else:
                result["compaction_required"] = False
            return result

    def current_memory(self, scope: MemoryScope) -> dict[str, Any]:
        """Return the bounded structured working set for one fair-play scope."""
        self.require_scope(scope)
        parameters = (scope.match_id, scope.agent_id, scope.perspective_id)
        with self._connect() as connection:
            budgets = {
                str(row["section"]): int(row["max_tokens"])
                for row in connection.execute("SELECT section, max_tokens FROM memory_budgets")
            }
            facts = [
                dict(row) for row in connection.execute(
                    "SELECT fact_key, category, subject, value, confidence, observed_turn, observed_year, created_unix "
                    "FROM facts WHERE match_id = ? AND agent_id = ? AND perspective_id = ? AND status = 'current' "
                    "ORDER BY created_unix DESC LIMIT 200", parameters,
                ).fetchall()
            ]
            relationships = [
                self._decode_row(row, "reasons_json") for row in connection.execute(
                    "SELECT r.*, a.display_name, a.faction_name, a.controller_kind FROM relationships r "
                    "JOIN actors a ON a.actor_id = r.actor_id WHERE r.match_id = ? AND r.agent_id = ? "
                    "AND r.perspective_id = ? AND r.status = 'current' ORDER BY r.created_unix DESC LIMIT 100",
                    parameters,
                ).fetchall()
            ]
            goals = [
                self._decode_row(row, "trigger_json") for row in connection.execute(
                    "SELECT * FROM goals WHERE match_id = ? AND agent_id = ? AND perspective_id = ? "
                    "AND status != 'superseded' ORDER BY priority DESC, created_unix DESC LIMIT 100",
                    parameters,
                ).fetchall()
            ]
            commitments = [
                dict(row) for row in connection.execute(
                    "SELECT * FROM commitments WHERE match_id = ? AND agent_id = ? AND perspective_id = ? "
                    "AND status != 'superseded' ORDER BY created_unix DESC LIMIT 100", parameters,
                ).fetchall()
            ]
            summaries = [
                dict(row) for row in connection.execute(
                    "SELECT section, content, token_estimate, through_event_id, created_unix FROM summaries "
                    "WHERE match_id = ? AND agent_id = ? AND perspective_id = ? AND status = 'current' "
                    "ORDER BY section", parameters,
                ).fetchall()
            ]
            events = [
                self._decode_row(row, "payload_json") for row in connection.execute(
                    "SELECT event_id, event_type, source, turn, year, actor_id, subject_id, importance, "
                    "payload_json, observed_unix, created_unix FROM events WHERE match_id = ? AND agent_id = ? "
                    "AND perspective_id = ? ORDER BY created_unix DESC LIMIT 50", parameters,
                ).fetchall()
            ]
            chat = [
                self._decode_row(row, "metadata_json") for row in connection.execute(
                    "SELECT * FROM chat_messages WHERE match_id = ? AND agent_id = ? AND perspective_id = ? "
                    "ORDER BY (acknowledged_unix IS NULL) DESC, received_unix DESC LIMIT 50", parameters,
                ).fetchall()
            ]
        sections: dict[str, Any] = {
            "situation": {"summaries": summaries, "facts": facts},
            "relationships": relationships,
            "goals": goals,
            "commitments": commitments,
            "recent_events": events,
            "chat": chat,
        }
        estimates = {
            name: max(1, (len(json.dumps(value, ensure_ascii=False, separators=(",", ":"))) + 3) // 4)
            for name, value in sections.items()
        }
        over_budget = [
            name for name, estimate in estimates.items()
            if estimate > budgets.get(name, estimate)
        ]
        return {
            "scope": {
                "match_id": scope.match_id,
                "agent_id": scope.agent_id,
                "perspective_id": scope.perspective_id,
            },
            "sections": sections,
            "token_estimates": estimates,
            "token_budgets": budgets,
            "compaction_required": bool(over_budget),
            "compaction_required_sections": over_budget,
        }

    def recall_many(
        self,
        scope: MemoryScope,
        queries: Sequence[Mapping[str, Any]],
        *,
        total_token_budget: int = 2000,
    ) -> dict[str, Any]:
        """Run several scoped FTS queries under one output budget."""
        if not queries or len(queries) > 12:
            raise InvalidRecord("invalid_recall_query_count")
        total_token_budget = min(max(total_token_budget, 128), 12000)
        used = 0
        seen: set[str] = set()
        groups: list[dict[str, Any]] = []
        truncated = False
        for request in queries:
            query = str(request.get("query", ""))
            kinds_value = request.get("document_kinds", ())
            kinds = tuple(str(item) for item in kinds_value) if isinstance(kinds_value, (list, tuple)) else ()
            requested_limit = int(request.get("limit", 10))
            matches: list[dict[str, Any]] = []
            for result in self.search(scope, query, document_kinds=kinds, limit=requested_limit):
                if result["document_id"] in seen:
                    continue
                estimate = max(1, (len(result["title"]) + len(result["body"]) + 3) // 4)
                if used + estimate > total_token_budget:
                    truncated = True
                    break
                seen.add(result["document_id"])
                used += estimate
                matches.append(result)
            groups.append({"query": query, "matches": matches})
            if truncated:
                break
        return {
            "scope": {
                "match_id": scope.match_id,
                "agent_id": scope.agent_id,
                "perspective_id": scope.perspective_id,
            },
            "groups": groups,
            "estimated_tokens": used,
            "token_budget": total_token_budget,
            "truncated": truncated,
        }

    def list_projection_records(
        self,
        scope: MemoryScope,
        table: str,
        *,
        include_history: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Read an allowlisted structured projection without arbitrary SQL access."""
        allowed = {
            "claims": ("created_unix", None),
            "beliefs": ("created_unix", "status = 'current'"),
            "relationships": ("created_unix", "status = 'current'"),
            "commitments": ("created_unix", "status != 'superseded'"),
            "goals": ("created_unix", "status != 'superseded'"),
            "summaries": ("created_unix", "status = 'current'"),
        }
        if table not in allowed:
            raise InvalidRecord("invalid_projection_kind")
        self.require_scope(scope)
        order_column, current_clause = allowed[table]
        clauses = ["match_id = ?", "agent_id = ?", "perspective_id = ?"]
        if current_clause and not include_history:
            clauses.append(current_clause)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM {table} WHERE " + " AND ".join(clauses)
                + f" ORDER BY {order_column} DESC LIMIT ?",
                (scope.match_id, scope.agent_id, scope.perspective_id, min(max(limit, 1), 1000)),
            ).fetchall()
        json_fields = {
            "relationships": ("reasons_json",),
            "goals": ("trigger_json",),
        }.get(table, ())
        return [self._decode_row(row, *json_fields) for row in rows]

    def search(
        self,
        scope: MemoryScope,
        query: str,
        *,
        document_kinds: Sequence[str] = (),
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self.require_scope(scope)
        terms = re.findall(r"[\w'-]+", query, flags=re.UNICODE)
        if not terms:
            raise InvalidRecord("empty_search_query")
        fts_query = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms[:16])
        clauses = ["d.match_id = ?", "d.agent_id = ?", "d.perspective_id = ?", "search_fts MATCH ?"]
        parameters: list[Any] = [scope.match_id, scope.agent_id, scope.perspective_id, fts_query]
        if document_kinds:
            clauses.append("d.document_kind IN (" + ",".join("?" for _ in document_kinds) + ")")
            parameters.extend(document_kinds)
        parameters.append(min(max(limit, 1), 100))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT d.document_id, d.document_kind, d.source_id, d.title, d.body, d.tags, d.importance, "
                "d.created_unix, bm25(search_fts, 4.0, 1.0, 2.0) AS rank "
                "FROM search_fts JOIN search_documents d ON d.rowid = search_fts.rowid WHERE "
                + " AND ".join(clauses) + " ORDER BY rank ASC, d.importance DESC, d.created_unix DESC LIMIT ?",
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _decode_row(row: sqlite3.Row, *json_fields: str) -> dict[str, Any]:
        result = dict(row)
        for field in json_fields:
            raw = result.get(field)
            if isinstance(raw, str):
                try:
                    result[field.removesuffix("_json")] = json.loads(raw)
                except json.JSONDecodeError:
                    result[field.removesuffix("_json")] = None
        return result
