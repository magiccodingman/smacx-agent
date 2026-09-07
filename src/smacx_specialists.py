"""Durable Hermes specialist missions over isolated immutable evidence views.

SMACX owns mission/attempt lifecycle, capabilities, dependencies, validation,
attention delivery, cancellation, and traces. Hermes owns the disposable
model/tool loop. No specialist transcript is copied into sovereign history.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any, Iterable, Mapping, Sequence
import uuid

from smacx_attention import AttentionService
from smacx_journal import CampaignJournal
from smacx_store import MemoryScope, SmacxStore
from smacx_world_model import CALCULATOR_VERSION, estimate_tokens
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity, canonical_json, content_hash, material_hash


REFERENCE_PROMPT_VERSION = "smacx.reference-specialist.v5"
WORLD_PROMPT_VERSION = "smacx.world-specialist.v5"
TOOL_CONTRACT_VERSION = "smacx.specialist-instruments.v3"
FACULTIES = frozenset({"reference", "world"})
ATTEMPT_FAILURES = frozenset({
    "provider_failed", "mcp_failed", "invalid_schema", "token_budget_exhausted",
    "tool_budget_exhausted", "timed_out", "orphaned", "cancelled",
})
TRANSIENT_ATTEMPT_FAILURES = frozenset({"provider_failed", "mcp_failed", "orphaned"})
CANCELLATION_REASONS = frozenset({
    "cancelled_by_parent", "cancelled_by_operation", "cancelled_by_rollback",
    "cancelled_by_world_epoch", "installation_shutdown",
})
JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL | re.IGNORECASE)


REFERENCE_SYSTEM_PROMPT = """You are a disposable SMACX mechanics researcher, not the player.
Use only reference_query. Retrieve iteratively, follow material terminology,
distinguish mechanics evidence from strategy, cite consequential claims, report
conflicts and uncertainty, stop at diminishing value, and never infer match
hidden state. Only top-level evidence_refs/citation_receipts values returned by
reference_query are valid citations. Copy one exactly for every material
non-unknown claims[] fact. Document IDs, collection IDs, and other subject IDs
are never citations. Never invent or transform a citation. An empty claims[] is
valid: keep useful uncited synthesis in answer or limitations.
Each result reports remaining_evidence_calls; synthesize before that bounded
lease reaches zero rather than attempting another retrieval.
Return only the required JSON result. The sovereign player alone
chooses policy and strategy. You do not send chat, write memory, delegate,
access terminal/files/the web, or mutate the game."""

WORLD_SYSTEM_PROMPT = """You are a disposable SMACX mechanical world analyst, not the player.
Use only world_query against the immutable perspective supplied to this mission.
Prefer deterministic evidence; preserve current/stale/reported/derived/estimated/
unknown status; validate geometry, timing, and dependencies; compare feasible
alternatives and report constraints. Never infer hidden geography, unseen units,
foreign identity continuity, or intentions as fact. Return only the required JSON
result. Only top-level evidence_refs/citation_receipt values returned by
world_query are valid citations. Copy citation_receipt exactly for every
material non-unknown claims[] fact. object_ref, location_ref, region_ref, and
other subject IDs are never citations. Never invent or transform a citation.
An empty claims[] is valid: keep useful uncited synthesis in answer or
limitations. You provide evidence; the sovereign alone chooses strategy and actions.
Each result reports remaining_evidence_calls; synthesize before that bounded
lease reaches zero rather than attempting another query.
Do not use terminal/files/web, chat, memory, delegation, or gameplay mutation."""

RESULT_CONTRACT = {
    "mission_id": "string", "answer": "string",
    "claims": [{"claim": "string", "citations": ["evidence-ref"],
                "epistemic_status": "current|stale|reported|derived|estimated|unknown"}],
    "limitations": ["string"], "unresolved_questions": ["string"],
}


def default_specialist_policy() -> dict[str, Any]:
    """Return an independent copy of the bounded installation policy."""
    return {
        "seat_concurrency": 1,
        "installation_concurrency": 2,
        "synthesis": {"tool_budget": 4, "provider_call_budget": 4,
                      "provider_token_budget": 96000, "context_token_ceiling": 65536,
                      "output_token_budget": 1500, "wall_seconds": 90},
        "investigation": {"tool_budget": 24, "provider_call_budget": 16,
                          "provider_token_budget": 512000, "context_token_ceiling": 262144,
                          "output_token_budget": 4000, "wall_seconds": 300},
        "automatic_retries": 1, "schema_repairs": 1,
        "manual_retry_retention_seconds": 86_400,
        "trace_capture": True, "trace_success_generations": 10,
        "trace_failed_generations": 25, "trace_byte_ceiling": 2_147_483_648,
        "trace_high_retention": False,
    }


def normalize_specialist_policy(configured: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Merge and clamp administrator policy at every trust boundary."""
    defaults = default_specialist_policy()
    source = configured if isinstance(configured, Mapping) else {}
    result = dict(defaults)
    for key in (
        "seat_concurrency", "installation_concurrency", "automatic_retries",
        "schema_repairs", "trace_capture", "trace_success_generations",
        "trace_failed_generations", "trace_byte_ceiling", "trace_high_retention",
        "manual_retry_retention_seconds",
    ):
        if key in source:
            result[key] = source[key]
    workload_limits = {
        "tool_budget": (1, 128), "provider_call_budget": (1, 64),
        "provider_token_budget": (1_000, 1_000_000),
        "context_token_ceiling": (8_192, 1_048_576),
        "output_token_budget": (128, 4_096), "wall_seconds": (5, 3_600),
    }
    for workload in ("synthesis", "investigation"):
        requested = source.get(workload)
        merged = {**defaults[workload], **(dict(requested)
                                          if isinstance(requested, Mapping) else {})}
        result[workload] = {
            key: min(max(int(merged[key]), minimum), maximum)
            for key, (minimum, maximum) in workload_limits.items()
        }
        result[workload]["output_token_budget"] = min(
            result[workload]["output_token_budget"],
            result[workload]["provider_token_budget"],
        )
        result[workload]["context_token_ceiling"] = max(
            result[workload]["context_token_ceiling"], 65_536,
        )
    # Initial locked architecture: at most one disposable child per sovereign.
    result["seat_concurrency"] = 1
    result["installation_concurrency"] = min(
        max(int(result["installation_concurrency"]), 1), 16,
    )
    result["automatic_retries"] = min(max(int(result["automatic_retries"]), 0), 2)
    result["schema_repairs"] = min(max(int(result["schema_repairs"]), 0), 1)
    result["manual_retry_retention_seconds"] = min(max(
        int(result["manual_retry_retention_seconds"]), 300,
    ), 604_800)
    result["trace_success_generations"] = min(
        max(int(result["trace_success_generations"]), 0), 10_000,
    )
    result["trace_failed_generations"] = min(
        max(int(result["trace_failed_generations"]),
            int(result["trace_success_generations"])), 10_000,
    )
    result["trace_byte_ceiling"] = min(
        max(int(result["trace_byte_ceiling"]), 16 * 1024 * 1024),
        1024 * 1024 * 1024 * 1024,
    )
    result["trace_capture"] = bool(result["trace_capture"])
    result["trace_high_retention"] = bool(result["trace_high_retention"])
    return result


class SpecialistError(RuntimeError):
    pass


def system_prompt(faculty: str) -> tuple[str, str]:
    if faculty == "reference":
        return REFERENCE_PROMPT_VERSION, REFERENCE_SYSTEM_PROMPT
    if faculty == "world":
        return WORLD_PROMPT_VERSION, WORLD_SYSTEM_PROMPT
    raise SpecialistError("invalid_specialist_faculty")


def extract_result(content: str) -> dict[str, Any]:
    match = JSON_FENCE.search(content)
    candidate = match.group(1) if match else content.strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            raise SpecialistError("specialist_returned_non_json")
        try:
            value = json.loads(candidate[start:end + 1])
        except json.JSONDecodeError as exc:
            raise SpecialistError("specialist_returned_non_json") from exc
    if not isinstance(value, dict):
        raise SpecialistError("specialist_returned_non_object")
    return value


def _normalized_objective(value: str) -> str:
    result = " ".join(str(value).split())
    if not result or len(result) > 4000:
        raise SpecialistError("invalid_specialist_objective")
    return result


class SpecialistTraceStore:
    """Compressed diagnostic traces; never authoritative or model-visible."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def write(self, mission: Mapping[str, Any], attempt_id: str,
              rows: Sequence[Mapping[str, Any]], *, outcome: str,
              generation: int) -> dict[str, Any]:
        directory = (self.root / str(mission["match_id"]) / str(mission["timeline_id"])
                     / str(mission["mission_id"]))
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{attempt_id}.jsonl.zst"
        def scrub(value: Any, key: str = "") -> Any:
            lowered = key.casefold()
            if any(marker in lowered for marker in (
                    "api_key", "authorization", "secret", "password", "bearer")):
                return "[REDACTED]"
            if isinstance(value, Mapping):
                return {str(child): scrub(item, str(child)) for child, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [scrub(item) for item in value]
            if isinstance(value, str) and (
                    value.startswith("Bearer ") or re.match(r"^(?:sk-|sk_|eyJ)[A-Za-z0-9._-]{16,}$", value)):
                return "[REDACTED]"
            return value

        scrubbed = []
        for row in rows:
            clean = scrub({**dict(row), "actor": str(mission.get("faculty", "unknown")) + "-specialist",
                "mission_id": mission["mission_id"], "attempt_id": attempt_id,
                "parent_episode_id": mission.get("parent_episode_id"),
                "match_id": mission["match_id"], "timeline_id": mission["timeline_id"]})
            scrubbed.append(canonical_json(clean))
        raw = ("\n".join(scrubbed) + "\n").encode()
        with tempfile.NamedTemporaryFile(prefix="smacx-specialist-trace-", delete=False) as stream:
            stream.write(raw)
            temporary = Path(stream.name)
        try:
            completed = subprocess.run(
                ["zstd", "-q", "-f", "-10", str(temporary), "-o", str(target)],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if completed.returncode != 0:
                raise SpecialistError("specialist_trace_compression_failed")
        finally:
            temporary.unlink(missing_ok=True)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        return {"content_path": str(target), "content_sha256": digest,
                "bytes": target.stat().st_size, "outcome_class": outcome,
                "checkpoint_generation": int(generation)}

    def gc(self, store: SmacxStore, *, success_generations: int = 10,
           failed_generations: int = 25, byte_ceiling: int = 2_147_483_648,
           high_retention: bool = False) -> dict[str, Any]:
        """Prune diagnostic traces without deleting protected recent failures."""
        with store._connect() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT * FROM specialist_trace_manifests ORDER BY created_unix"
            ).fetchall()]
        if high_retention:
            return {"ok": True, "removed": 0, "bytes_removed": 0,
                    "bytes_retained": sum(int(row["bytes"]) for row in rows),
                    "warning": None, "high_retention": True}
        newest_by_match: dict[str, int] = {}
        with store._connect() as connection:
            for row in connection.execute(
                    "SELECT match_id,generation FROM campaign_checkpoint_generations"):
                newest_by_match[str(row["match_id"])] = int(row["generation"] or 0)
        removable: list[dict[str, Any]] = []
        protected_failures: list[dict[str, Any]] = []
        recent_successes: list[dict[str, Any]] = []
        for row in rows:
            with store._connect() as connection:
                match_row = connection.execute(
                    "SELECT match_id FROM specialist_missions WHERE mission_id=?",
                    (row["mission_id"],),
                ).fetchone()
            match_id = str(match_row["match_id"]) if match_row else ""
            age = newest_by_match.get(match_id, 0) - int(row["checkpoint_generation"])
            failed = str(row["outcome_class"]) not in {"accepted", "completed"}
            horizon = failed_generations if failed else success_generations
            if age >= horizon:
                removable.append(row)
            elif failed:
                protected_failures.append(row)
            else:
                recent_successes.append(row)
        # The byte ceiling may collect oldest successful traces early. Recent
        # failures retain their explicit longer floor and can instead disable
        # future full-trace capture with a visible warning.
        retained_total = sum(int(row["bytes"]) for row in
                             (*protected_failures, *recent_successes))
        for row in recent_successes:
            if retained_total <= max(1, int(byte_ceiling)):
                break
            removable.append(row)
            retained_total -= int(row["bytes"])
        removed_bytes = 0
        for row in removable:
            try:
                Path(str(row["content_path"])).unlink(missing_ok=True)
            except OSError:
                continue
            removed_bytes += int(row["bytes"])
            with store.transaction() as connection:
                connection.execute(
                    "DELETE FROM specialist_trace_manifests WHERE attempt_id=?",
                    (row["attempt_id"],),
                )
                connection.execute(
                    "UPDATE specialist_attempts SET trace_path=NULL,trace_hash=NULL,trace_bytes=NULL "
                    "WHERE attempt_id=?", (row["attempt_id"],),
                )
        removed_ids = {str(row["attempt_id"]) for row in removable}
        retained_rows = [row for row in (*protected_failures, *recent_successes)
                         if str(row["attempt_id"]) not in removed_ids]
        retained = sum(int(row["bytes"]) for row in retained_rows)
        warning = None
        if retained > max(1, int(byte_ceiling)):
            warning = "specialist_trace_byte_ceiling_blocked_by_protected_failed_or_recent_traces"
            with store.transaction() as connection:
                connection.execute(
                    "INSERT INTO control_settings(setting_key,value_json,updated_unix) VALUES"
                    "('specialist.trace_warning',?,?) ON CONFLICT(setting_key) DO UPDATE SET "
                    "value_json=excluded.value_json,updated_unix=excluded.updated_unix",
                    (canonical_json({"code": warning, "bytes": retained,
                                     "ceiling": int(byte_ceiling)}), time.time()),
                )
        else:
            with store.transaction() as connection:
                connection.execute(
                    "DELETE FROM control_settings WHERE setting_key='specialist.trace_warning'"
                )
        return {"ok": True, "removed": len(removable),
                "bytes_removed": removed_bytes, "bytes_retained": retained,
                "warning": warning, "high_retention": False}


class SpecialistService:
    """Mission authority. Execution is supplied by the isolated supervisor."""

    def __init__(self, store: SmacxStore, world_store: WorldStore,
                 scope: MemoryScope, *, journal: CampaignJournal | None = None,
                 attention: AttentionService | None = None) -> None:
        self.store = store
        self.world_store = world_store
        self.scope = scope
        self.journal = journal or CampaignJournal(
            store.path.parent / "campaigns", timeline_resolver=store.active_timeline_id,
        )
        self.attention = attention or AttentionService(store, self.journal, scope)

    def _policy(self) -> dict[str, Any]:
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM control_settings WHERE setting_key='specialist.policy'"
            ).fetchone()
        if not row:
            return normalize_specialist_policy()
        try:
            configured = json.loads(row["value_json"])
        except json.JSONDecodeError:
            return normalize_specialist_policy()
        return normalize_specialist_policy(
            configured if isinstance(configured, Mapping) else None,
        )

    def commission(self, *, faculty: str, objective: str,
                   parent_episode_id: str | None = None,
                   operation_id: str | None = None,
                   subject_refs: Iterable[str] = (),
                   model_profile_revision: str = "installation-helper",
                   corpus_revision: str | None = None,
                   execution_class: str | None = None,
                   result_scope: str | None = None) -> dict[str, Any]:
        if faculty not in FACULTIES:
            raise SpecialistError("invalid_specialist_faculty")
        objective = _normalized_objective(objective)
        subjects = tuple(dict.fromkeys(str(value) for value in subject_refs))[:64]
        timeline = self.store.active_timeline_id(self.scope)
        projection = self.world_store.load(self.scope, timeline)
        if not projection:
            raise SpecialistError("world_projection_unavailable")
        identity = WorldIdentity(**projection["identity"])
        if faculty == "world":
            snapshot_refs = {str(item["object_ref"]) for item in projection.get("objects", ())}
            if any(ref not in snapshot_refs for ref in subjects):
                # Disposable snapshots retain objects, not live scope/watch or
                # query-cache handles. Do not accept an inert child input.
                raise SpecialistError("specialist_subject_requires_immutable_world_object")
        if operation_id:
            with self.store._connect() as connection:
                operation = connection.execute(
                    "SELECT status FROM cognitive_operations WHERE operation_id=? AND match_id=? "
                    "AND agent_id=? AND perspective_id=? AND timeline_id=?",
                    (operation_id, self.scope.match_id, self.scope.agent_id,
                     self.scope.perspective_id, timeline),
                ).fetchone()
            if not operation or operation["status"] not in {"active", "stale"}:
                raise SpecialistError("linked_operation_unavailable")
        execution = execution_class or (
            "synthesis" if len(objective) <= 300 and len(subjects) <= 4 else "investigation"
        )
        if execution not in {"synthesis", "investigation"}:
            raise SpecialistError("invalid_specialist_execution_class")
        prompt_version, prompt = system_prompt(faculty)
        # This digest is consumed by the fail-closed Hermes prompt loader,
        # which validates the exact UTF-8 file bytes rather than a JSON
        # serialization of the string value.
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        tool_hash = content_hash({"version": TOOL_CONTRACT_VERSION, "faculty": faculty})
        corpus_revision = (str(corpus_revision) if corpus_revision
                           else os.environ.get("SMACX_CORPUS_REVISION", "current"))
        idempotency = content_hash({
            "faculty": faculty, "objective": objective.casefold(), "subjects": subjects,
            "operation": operation_id, "timeline": timeline,
            "world_epoch": identity.world_epoch,
            "world_revision": projection["world_revision"] if faculty == "world" else None,
            "corpus_revision": corpus_revision,
        })
        policy = self._policy()
        budget = policy[execution]
        with self.store._connect() as connection:
            configured_profile = connection.execute(
                "SELECT value_json FROM control_settings WHERE setting_key='specialist.profile'"
            ).fetchone()
            harness_profile = connection.execute(
                "SELECT * FROM harness_profiles WHERE harness_profile_id=? AND agent_id=?",
                (model_profile_revision, self.scope.agent_id),
            ).fetchone()
        if configured_profile:
            try:
                pinned_profile = json.loads(configured_profile["value_json"])
            except json.JSONDecodeError:
                pinned_profile = {}
        elif harness_profile:
            row = dict(harness_profile)
            metadata = json.loads(row.get("metadata_json") or "{}")
            pinned_profile = {
                "profile_id": row["harness_profile_id"],
                "profile_fingerprint": content_hash({
                    "provider_id": row.get("provider_id"), "model_id": row.get("model_id"),
                    "reasoning_effort": row.get("reasoning_effort"),
                    "context_length": row.get("context_length"),
                    "generation_settings": metadata.get("generation_settings", {}),
                }),
                "provider_id": row.get("provider_id"), "model_id": row.get("model_id"),
                "reasoning_effort": row.get("reasoning_effort"),
                "context_length": row.get("context_length"),
                "generation_settings": metadata.get("generation_settings", {}),
            }
        else:
            pinned_profile = {"profile_id": model_profile_revision}
        if not isinstance(pinned_profile, Mapping):
            raise SpecialistError("specialist_helper_profile_invalid")
        pinned_profile = dict(pinned_profile)
        model_profile_revision = content_hash(pinned_profile)
        now = time.time()
        mission_id = "mission-" + uuid.uuid4().hex
        journal_manifest = self.journal.replay(self.scope)["manifest"]
        checkpoint_generation = self.store.checkpoint_generation(self.scope.match_id)
        # Admission precedes materializing a potentially Huge immutable world.
        # This avoids creating an unowned full snapshot for an idempotent retry
        # or a request rejected by the bounded queue.
        with self.store._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM specialist_missions WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? AND idempotency_key=?",
                (self.scope.match_id, self.scope.agent_id, self.scope.perspective_id,
                 timeline, idempotency),
            ).fetchone()
            if existing:
                return self._public_mission(dict(existing), deduplicated=True)
            installation_outstanding = int(connection.execute(
                "SELECT COUNT(*) FROM specialist_missions WHERE status IN ('queued','active','retry_wait')"
            ).fetchone()[0])
            seat_outstanding = int(connection.execute(
                "SELECT COUNT(*) FROM specialist_missions WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? AND status IN ('queued','active','retry_wait')",
                (self.scope.match_id, self.scope.agent_id,
                 self.scope.perspective_id, timeline),
            ).fetchone()[0])
        if installation_outstanding >= max(64, policy["installation_concurrency"] * 16):
            raise SpecialistError("specialist_installation_queue_limit")
        if seat_outstanding >= max(16, policy["seat_concurrency"] * 8):
            raise SpecialistError("specialist_seat_queue_limit")
        snapshot_id = world_view_hash = None
        reference_snapshot_path = reference_snapshot_hash = None
        if faculty == "world":
            snapshot = self.world_store.snapshot(
                self.scope, identity,
                journal_head_hash=str(journal_manifest["head_hash"]),
                journal_sequence=int(journal_manifest["sequence"]),
                calculator_versions={"world": CALCULATOR_VERSION},
                pin_owner=("specialist_mission", mission_id),
            )
            snapshot_id = str(snapshot["snapshot_id"])
            world_view_hash = str(snapshot["content_sha256"])
        elif faculty == "reference":
            if not corpus_revision:
                raise SpecialistError("reference_corpus_revision_unavailable")
            from smacx_reference import freeze_reference_corpus
            try:
                frozen_reference = freeze_reference_corpus(
                    corpus_revision, self.store.path.parent / "reference-snapshots",
                )
            except RuntimeError as exc:
                raise SpecialistError(str(exc)) from exc
            reference_snapshot_path = str(frozen_reference["content_path"])
            reference_snapshot_hash = str(frozen_reference["content_sha256"])
        duplicate: dict[str, Any] | None = None
        try:
            with self.store.transaction() as connection:
                existing = connection.execute(
                    "SELECT * FROM specialist_missions WHERE match_id=? AND agent_id=? "
                    "AND perspective_id=? AND timeline_id=? AND idempotency_key=?",
                    (self.scope.match_id, self.scope.agent_id, self.scope.perspective_id,
                     timeline, idempotency),
                ).fetchone()
                if existing:
                    duplicate = dict(existing)
                else:
                    connection.execute(
                        "INSERT INTO specialist_missions(mission_id,match_id,agent_id,perspective_id,"
                        "timeline_id,world_epoch,source_world_revision,observation_cursor,world_snapshot_id,"
                        "world_view_hash,faculty,normalized_objective,subject_refs_json,linked_operation_id,"
                        "parent_episode_id,corpus_revision,reference_snapshot_path,reference_snapshot_hash,"
                        "system_prompt_version,system_prompt_hash,"
                        "tool_contract_version,tool_contract_hash,execution_class,model_profile_revision,"
                        "model_profile_json,"
                        "idempotency_key,attempt_count,associated_checkpoint_generation,tool_budget,"
                        "provider_call_budget,provider_token_budget,context_token_ceiling,output_token_budget,"
                        "deadline_unix,status,result_scope,created_unix,updated_unix) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,'queued',?,?,?)",
                        (mission_id, self.scope.match_id, self.scope.agent_id,
                         self.scope.perspective_id, timeline, identity.world_epoch,
                         int(projection["world_revision"]), int(projection["observation_cursor"]),
                         snapshot_id, world_view_hash, faculty, objective, canonical_json(subjects),
                         operation_id, parent_episode_id, corpus_revision,
                         reference_snapshot_path, reference_snapshot_hash,
                         prompt_version, prompt_hash,
                         TOOL_CONTRACT_VERSION, tool_hash, execution, model_profile_revision,
                         canonical_json(pinned_profile), idempotency,
                         checkpoint_generation,
                         int(budget["tool_budget"]), int(budget["provider_call_budget"]),
                         int(budget["provider_token_budget"]), int(budget["context_token_ceiling"]),
                         int(budget["output_token_budget"]), now + int(budget["wall_seconds"]),
                         result_scope or ("operation" if operation_id else "query"), now, now),
                    )
        except Exception:
            if snapshot_id:
                self.world_store.unpin_snapshot(
                    snapshot_id, "specialist_mission", mission_id,
                )
            raise
        if duplicate is not None:
            if snapshot_id:
                self.world_store.unpin_snapshot(
                    snapshot_id, "specialist_mission", mission_id,
                )
            return self._public_mission(duplicate, deduplicated=True)
        event = self.journal.append(self.scope, "specialist.mission_commissioned", {
            "mission_id": mission_id, "faculty": faculty, "objective_hash": content_hash(objective),
            "operation_id": operation_id, "world_snapshot_id": snapshot_id,
            "source_world_revision": int(projection["world_revision"]),
        })
        return {"ok": True, "status": "mission_pending", "mission_id": mission_id,
                "faculty": faculty, "execution_class": execution,
                "journal_event_id": event["event_id"], "deduplicated": False}

    @staticmethod
    def _public_mission(row: Mapping[str, Any], *, deduplicated: bool = False) -> dict[str, Any]:
        result = {key: row.get(key) for key in (
            "mission_id", "faculty", "status", "execution_class", "result_scope",
            "result_preview", "stale_reason", "cancellation_reason", "attempt_count",
            "created_unix", "updated_unix",
        ) if row.get(key) is not None}
        result.update({"ok": row.get("status") == "accepted", "deduplicated": deduplicated})
        if row.get("status") in {"queued", "active", "retry_wait"}:
            result["status"] = "mission_pending"
        return result

    def get(self, mission_id: str, *, include_result: bool = True) -> dict[str, Any]:
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT * FROM specialist_missions WHERE mission_id=? AND match_id=? "
                "AND agent_id=? AND perspective_id=?",
                (mission_id, self.scope.match_id, self.scope.agent_id,
                 self.scope.perspective_id),
            ).fetchone()
        if not row:
            raise SpecialistError("unknown_specialist_mission")
        if str(row["timeline_id"]) != self.store.active_timeline_id(self.scope):
            raise SpecialistError("specialist_result_historical_timeline")
        public = self._public_mission(dict(row))
        if include_result and row["status"] in {"accepted", "stale"} and row["result_json"]:
            public["result"] = json.loads(row["result_json"])
            public["result_hash"] = row["result_hash"]
            if row["result_receipt_json"]:
                public["provenance_receipt"] = json.loads(row["result_receipt_json"])
        return public

    def cancel(self, mission_id: str, reason: str = "cancelled_by_parent", *,
               authoritative: bool = False) -> dict[str, Any]:
        if reason not in CANCELLATION_REASONS:
            raise SpecialistError("invalid_specialist_cancellation_reason")
        now = time.time()
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT world_snapshot_id,timeline_id FROM specialist_missions WHERE mission_id=? "
                "AND match_id=? AND agent_id=? AND perspective_id=?",
                (mission_id, self.scope.match_id, self.scope.agent_id,
                 self.scope.perspective_id),
            ).fetchone()
            if not row:
                raise SpecialistError("unknown_specialist_mission")
            allow_prepared = authoritative and reason in {
                "cancelled_by_rollback", "cancelled_by_world_epoch",
            }
            changed = connection.execute(
                "UPDATE specialist_missions SET status='cancelled',cancellation_reason=?,updated_unix=? "
                "WHERE mission_id=? AND status IN ('queued','active','retry_wait') "
                + ("" if allow_prepared else "AND accepted_attempt_id IS NULL"),
                (reason, now, mission_id),
            ).rowcount
            if changed != 1:
                raise SpecialistError("specialist_mission_not_cancellable")
            connection.execute(
                "UPDATE specialist_attempts SET status='cancelled',failure_reason=?,completed_unix=? "
                "WHERE mission_id=? AND status IN ('starting','running','validating')",
                (reason, now, mission_id),
            )
        if row["world_snapshot_id"]:
            self.world_store.unpin_snapshot(
                str(row["world_snapshot_id"]), "specialist_mission", mission_id,
            )
        self.journal.append(self.scope, "specialist.mission_cancelled", {
            "mission_id": mission_id, "reason": reason,
        }, timeline_id=str(row["timeline_id"]))
        return {"ok": True, "mission_id": mission_id, "status": "cancelled"}

    def retry(self, mission_id: str) -> dict[str, Any]:
        now = time.time()
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT * FROM specialist_missions WHERE mission_id=? AND match_id=? "
                "AND agent_id=? AND perspective_id=?",
                (mission_id, self.scope.match_id, self.scope.agent_id,
                 self.scope.perspective_id),
            ).fetchone()
        if not row or row["status"] != "failed":
            # A stale result belongs to an obsolete dependency set. Re-running
            # the same frozen mission cannot make it current; the sovereign
            # must commission a new revision-bound mission instead.
            raise SpecialistError("specialist_mission_not_retriable")
        if float(row["deadline_unix"]) <= now:
            raise SpecialistError("specialist_retry_window_expired")
        mission = dict(row)
        current_timeline = self.store.active_timeline_id(self.scope)
        current = self.world_store.load(self.scope, current_timeline)
        if current_timeline != mission["timeline_id"]:
            raise SpecialistError("specialist_retry_timeline_changed")
        if not current or current["identity"]["world_epoch"] != mission["world_epoch"]:
            raise SpecialistError("specialist_retry_world_epoch_changed")
        snapshot_id = str(mission.get("world_snapshot_id") or "")
        if mission["faculty"] == "world":
            if not snapshot_id:
                raise SpecialistError("specialist_retry_snapshot_unavailable")
            try:
                self.world_store.load_snapshot_content(snapshot_id)
            except Exception as exc:
                raise SpecialistError("specialist_retry_snapshot_unavailable") from exc
        wall_seconds = int(self._policy()[str(mission["execution_class"])]["wall_seconds"])
        with self.store.transaction() as connection:
            transaction_now = time.time()
            changed = connection.execute(
                "UPDATE specialist_missions SET status='queued',stale_reason=NULL,"
                "cancellation_reason=NULL,deadline_unix=?,updated_unix=? "
                "WHERE mission_id=? AND match_id=? AND agent_id=? AND perspective_id=? "
                "AND status='failed' AND deadline_unix>? "
                "AND COALESCE(world_snapshot_id,'')=?",
                (transaction_now + wall_seconds, transaction_now, mission_id,
                 self.scope.match_id, self.scope.agent_id,
                 self.scope.perspective_id, transaction_now, snapshot_id,
                ),
            ).rowcount
            if changed == 1 and snapshot_id:
                # Reclaim retry authority and the immutable-view pin in the
                # same transaction.  This cannot race the supervisor's
                # deadline GC into accepting a retry whose view was released.
                connection.execute(
                    "INSERT OR IGNORE INTO world_snapshot_pins("
                    "snapshot_id,owner_kind,owner_id,pinned_unix) VALUES(?,?,?,?)",
                    (snapshot_id, "specialist_mission", mission_id, transaction_now),
                )
        if changed != 1:
            raise SpecialistError("specialist_mission_not_retriable")
        return {"ok": True, "mission_id": mission_id, "status": "mission_pending"}

    def begin_attempt(self, mission_id: str, runtime_owner: str,
                      *, heartbeat_seconds: int = 30) -> dict[str, Any]:
        now = time.time()
        with self.store.transaction() as connection:
            mission = connection.execute(
                "SELECT * FROM specialist_missions WHERE mission_id=? AND status IN ('queued','retry_wait') "
                "AND deadline_unix>?", (mission_id, now),
            ).fetchone()
            if not mission:
                raise SpecialistError("specialist_mission_not_queued")
            attempt_number = int(mission["attempt_count"]) + 1
            prior_failure = connection.execute(
                "SELECT failure_reason FROM specialist_attempts WHERE mission_id=? "
                "ORDER BY attempt_number DESC LIMIT 1", (mission_id,),
            ).fetchone()
            attempt_id = "attempt-" + uuid.uuid4().hex
            connection.execute(
                "INSERT INTO specialist_attempts(attempt_id,mission_id,attempt_number,status,"
                "runtime_owner,heartbeat_expires_unix,started_unix) VALUES(?,?,?,'starting',?,?,?)",
                (attempt_id, mission_id, attempt_number, runtime_owner,
                 now + min(max(int(heartbeat_seconds), 10), 300), now),
            )
            connection.execute(
                "UPDATE specialist_missions SET status='active',attempt_count=?,updated_unix=? "
                "WHERE mission_id=? AND status IN ('queued','retry_wait')",
                (attempt_number, now, mission_id),
            )
        return {**dict(mission), "attempt_id": attempt_id,
                "attempt_number": attempt_number,
                "prior_failure_reason": (
                    str(prior_failure["failure_reason"] or "") if prior_failure else ""
                )}

    def heartbeat_attempt(self, attempt_id: str, runtime_owner: str, *,
                          process_id: int | None = None,
                          heartbeat_seconds: int = 30) -> bool:
        with self.store.transaction() as connection:
            changed = connection.execute(
                "UPDATE specialist_attempts SET heartbeat_expires_unix=?,process_id=COALESCE(?,process_id) "
                "WHERE attempt_id=? AND runtime_owner=? AND status IN ('starting','running','validating')",
                (time.time() + min(max(int(heartbeat_seconds), 10), 300), process_id,
                 attempt_id, runtime_owner),
            ).rowcount
        return changed == 1

    def claim_tool_call(self, attempt_id: str) -> int:
        """Atomically claim one MCP call under the durable mission leash."""
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT a.status,a.tool_calls,m.tool_budget,m.deadline_unix,"
                "m.status AS mission_status FROM specialist_attempts a JOIN "
                "specialist_missions m ON m.mission_id=a.mission_id WHERE "
                "a.attempt_id=? AND m.match_id=? AND m.agent_id=? AND m.perspective_id=?",
                (attempt_id, self.scope.match_id, self.scope.agent_id,
                 self.scope.perspective_id),
            ).fetchone()
            if not row or row["status"] not in {"starting", "running"} \
                    or row["mission_status"] != "active":
                raise SpecialistError("specialist_attempt_not_active")
            if time.time() >= float(row["deadline_unix"]):
                raise SpecialistError("specialist_deadline_exhausted")
            sequence = int(row["tool_calls"]) + 1
            if sequence > int(row["tool_budget"]):
                raise SpecialistError("specialist_tool_budget_exhausted")
            connection.execute(
                "UPDATE specialist_attempts SET status='running',tool_calls=?,"
                "heartbeat_expires_unix=? WHERE attempt_id=?",
                (sequence, time.time() + 30, attempt_id),
            )
        return sequence

    def mark_validating(self, attempt_id: str, runtime_owner: str) -> None:
        with self.store.transaction() as connection:
            changed = connection.execute(
                "UPDATE specialist_attempts SET status='validating',heartbeat_expires_unix=? "
                "WHERE attempt_id=? AND runtime_owner=? AND status IN ('starting','running')",
                (time.time() + 30, attempt_id, runtime_owner),
            ).rowcount
        if changed != 1:
            raise SpecialistError("specialist_attempt_not_active")

    def record_dependencies(self, attempt_id: str, call_sequence: int,
                            dependency_rows: Iterable[Mapping[str, Any]]) -> None:
        with self.store.transaction() as connection:
            attempt = connection.execute(
                "SELECT mission_id,status FROM specialist_attempts WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if not attempt or attempt["status"] not in {"starting", "running", "validating"}:
                raise SpecialistError("specialist_attempt_not_active")
            for row in dependency_rows:
                kind, ref, digest = (str(row.get(name) or "") for name in ("kind", "ref", "hash"))
                if not kind or not ref or not digest:
                    raise SpecialistError("invalid_specialist_dependency")
                payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
                connection.execute(
                    "INSERT INTO specialist_dependencies(mission_id,attempt_id,dependency_kind,"
                    "dependency_ref,dependency_hash,dependency_payload_json,source_call_sequence) "
                    "VALUES(?,?,?,?,?,?,?) "
                    "ON CONFLICT(attempt_id,dependency_kind,dependency_ref) DO UPDATE SET "
                    "dependency_hash=excluded.dependency_hash,"
                    "dependency_payload_json=excluded.dependency_payload_json,"
                    "source_call_sequence=excluded.source_call_sequence",
                    (attempt["mission_id"], attempt_id, kind, ref, digest,
                     canonical_json(payload), int(call_sequence)),
                )
            connection.execute(
                "UPDATE specialist_attempts SET status='running',tool_calls=MAX(tool_calls,?) "
                "WHERE attempt_id=?", (int(call_sequence), attempt_id),
            )

    def _validate_result(self, mission: Mapping[str, Any], attempt_id: str,
                         raw: Mapping[str, Any]) -> dict[str, Any]:
        required = {"mission_id", "answer", "claims", "limitations", "unresolved_questions"}
        if not required.issubset(raw) or raw.get("mission_id") != mission["mission_id"]:
            raise SpecialistError("invalid_specialist_result_schema")
        answer = str(raw.get("answer") or "").strip()
        if not answer or len(answer.encode()) > 16_384:
            raise SpecialistError("invalid_specialist_result_size")
        if estimate_tokens(raw) > int(mission["output_token_budget"]):
            raise SpecialistError("specialist_output_budget_exhausted")
        with self.store._connect() as connection:
            dependencies = connection.execute(
                "SELECT dependency_ref FROM specialist_dependencies WHERE attempt_id=?",
                (attempt_id,),
            ).fetchall()
        allowed = {str(row["dependency_ref"]) for row in dependencies}
        claims = raw.get("claims")
        if not isinstance(claims, list) or len(claims) > 64:
            raise SpecialistError("invalid_specialist_claims")
        for claim in claims:
            if not isinstance(claim, Mapping) or not isinstance(claim.get("claim"), str):
                raise SpecialistError("invalid_specialist_claim_schema")
            if claim.get("epistemic_status") not in {
                    "current", "stale", "reported", "derived", "estimated", "unknown",
            }:
                raise SpecialistError("invalid_specialist_epistemic_status")
            if not isinstance(claim.get("citations"), list) or not all(
                    isinstance(value, str) for value in claim["citations"]):
                raise SpecialistError("invalid_specialist_claim_citations")
            if not set(claim["citations"]).issubset(allowed):
                raise SpecialistError("specialist_claim_uses_unretrieved_evidence")
            if claim.get("epistemic_status") != "unknown" and not claim["citations"]:
                raise SpecialistError("specialist_claim_missing_evidence")
        for name in ("limitations", "unresolved_questions"):
            if not isinstance(raw.get(name), list) \
                    or not all(isinstance(value, str) for value in raw[name]):
                raise SpecialistError("invalid_specialist_result_schema")
        return {key: raw[key] for key in required}

    def _finalize_prepared_result(
        self, mission: Mapping[str, Any], attempt_id: str,
        result: Mapping[str, Any], *, status: str, preview: str,
        journal_sequence: int,
    ) -> bool:
        """Publish a journal-authorized result into rebuildable projections.

        ``status='active'`` plus a non-null ``accepted_attempt_id`` is the
        internal committing state. It avoids adding a public lifecycle state
        while ensuring the model-visible accepted/stale state never precedes
        its canonical journal event. This operation is idempotent so startup
        reconciliation can close either side of the filesystem/SQLite crash
        window.
        """
        now = time.time()
        result_hash = content_hash(result)
        with self.store.transaction() as connection:
            changed = connection.execute(
                "UPDATE specialist_missions SET status=?,completion_journal_sequence=?,"
                "updated_unix=? WHERE mission_id=? AND status='active' "
                "AND accepted_attempt_id=? AND result_hash=?",
                (status, int(journal_sequence), now, mission["mission_id"],
                 attempt_id, result_hash),
            ).rowcount
            if changed != 1:
                existing = connection.execute(
                    "SELECT status,accepted_attempt_id,result_hash,completion_journal_sequence "
                    "FROM specialist_missions WHERE mission_id=?",
                    (mission["mission_id"],),
                ).fetchone()
                if not existing or existing["status"] != status \
                        or existing["accepted_attempt_id"] != attempt_id \
                        or existing["result_hash"] != result_hash \
                        or int(existing["completion_journal_sequence"] or 0) \
                        != int(journal_sequence):
                    raise SpecialistError("specialist_result_finalize_conflict")
                return False
            connection.execute(
                "UPDATE specialist_attempts SET status='completed',completed_unix=COALESCE("
                "completed_unix,?) WHERE attempt_id=? AND mission_id=? "
                "AND status IN ('starting','running','validating')",
                (now, attempt_id, mission["mission_id"]),
            )
            if mission.get("linked_operation_id"):
                operation = connection.execute(
                    "SELECT specialist_result_receipts_json FROM cognitive_operations "
                    "WHERE operation_id=? AND match_id=? AND agent_id=? AND perspective_id=? "
                    "AND timeline_id=? AND status IN ('active','stale')",
                    (mission["linked_operation_id"], mission["match_id"], mission["agent_id"],
                     mission["perspective_id"], mission["timeline_id"]),
                ).fetchone()
                if operation:
                    receipts = json.loads(operation["specialist_result_receipts_json"] or "[]")
                    if not any(item.get("mission_id") == mission["mission_id"]
                               for item in receipts if isinstance(item, Mapping)):
                        receipts.append({
                            "mission_id": mission["mission_id"], "faculty": mission["faculty"],
                            "status": status, "result_hash": result_hash,
                            "preview": preview[:240],
                        })
                        connection.execute(
                            "UPDATE cognitive_operations SET specialist_result_receipts_json=?,"
                            "updated_unix=? WHERE operation_id=?",
                            (canonical_json(receipts[-8:]), now,
                             mission["linked_operation_id"]),
                        )
        return True

    def accept_attempt(self, mission_id: str, attempt_id: str,
                       raw_result: Mapping[str, Any], *, usage: Mapping[str, Any],
                       trace: Mapping[str, Any] | None = None,
                       current_corpus_revision: str | None = None) -> dict[str, Any]:
        with self.store._connect() as connection:
            mission_row = connection.execute(
                "SELECT * FROM specialist_missions WHERE mission_id=?", (mission_id,),
            ).fetchone()
        if not mission_row or mission_row["status"] != "active":
            raise SpecialistError("specialist_late_result_rejected")
        mission = dict(mission_row)
        with self.store._connect() as connection:
            attempt_row = connection.execute(
                "SELECT status,heartbeat_expires_unix,started_unix FROM specialist_attempts "
                "WHERE attempt_id=? AND mission_id=?", (attempt_id, mission_id),
            ).fetchone()
        if not attempt_row or attempt_row["status"] not in {"starting", "running", "validating"} \
                or float(attempt_row["heartbeat_expires_unix"] or 0) <= time.time():
            raise SpecialistError("specialist_attempt_lease_expired")
        current_timeline = self.store.active_timeline_id(self.scope)
        current = self.world_store.load(self.scope, current_timeline)
        stale_reason = None
        if current_timeline != mission["timeline_id"]:
            self.cancel(mission_id, "cancelled_by_rollback", authoritative=True)
            raise SpecialistError("specialist_late_result_rejected")
        elif not current or current["identity"]["world_epoch"] != mission["world_epoch"]:
            self.cancel(mission_id, "cancelled_by_world_epoch", authoritative=True)
            raise SpecialistError("specialist_late_result_rejected")
        elif mission["faculty"] == "reference" and str(
                mission.get("corpus_revision") or "current") != str(
                    current_corpus_revision or os.environ.get(
                        "SMACX_CORPUS_REVISION", "current")):
            stale_reason = "reference_corpus_changed"
        result = self._validate_result(mission, attempt_id, raw_result)
        with self.store._connect() as connection:
            deps = [dict(row) for row in connection.execute(
                "SELECT dependency_kind,dependency_ref,dependency_hash,dependency_payload_json "
                "FROM specialist_dependencies "
                "WHERE attempt_id=?", (attempt_id,),
            ).fetchall()]
        if not stale_reason and mission["faculty"] == "world" and current:
            objects = {str(item["object_ref"]): item for item in current.get("objects", ())}
            for dep in deps:
                if dep["dependency_kind"] == "world_object" \
                        and material_hash(objects.get(str(dep["dependency_ref"]), {})) \
                        != dep["dependency_hash"]:
                    stale_reason = "world_dependency_changed"
                    break
                if dep["dependency_kind"] == "calculator" \
                        and dep["dependency_hash"] != CALCULATOR_VERSION:
                    stale_reason = "world_calculator_changed"
                    break
                if dep["dependency_kind"] in {"world_query", "coverage", "topology"}:
                    try:
                        payload = json.loads(dep.get("dependency_payload_json") or "{}")
                    except json.JSONDecodeError:
                        payload = {}
                    if dep["dependency_kind"] == "world_query" and isinstance(payload, Mapping):
                        from smacx_world import WorldService
                        try:
                            replayed = WorldService(self.world_store, self.scope).query(
                                mode=str(payload.get("mode") or ""),
                                subject_refs=payload.get("subject_refs") or (),
                                origin_ref=str(payload.get("origin_ref") or ""),
                                target_ref=str(payload.get("target_ref") or ""),
                                movement_profile_ref=str(payload.get("movement_profile_ref")
                                                         or "mobility-land-default"),
                                radius=int(payload.get("radius") or 0),
                                since_cursor=int(payload.get("since_cursor") or 0),
                                detail=str(payload.get("detail") or "standard"),
                                continuation=str(payload.get("continuation") or ""),
                                context_length=int(mission["context_token_ceiling"]),
                            )
                        except Exception:
                            stale_reason = "world_query_dependency_unavailable"
                            break
                        if str(replayed.get("dependency_hash") or "") != dep["dependency_hash"]:
                            stale_reason = "world_query_dependency_changed"
                            break
                    elif int(current["world_revision"]) != int(mission["source_world_revision"]):
                        stale_reason = "world_coverage_changed"
                        break
        status = "stale" if stale_reason else "accepted"
        now = time.time()
        preview = " ".join(result["answer"].split())[:480]
        result_json = canonical_json(result)
        result_hash = content_hash(result)
        dependency_rows = sorted(({
            "kind": str(dep["dependency_kind"]),
            "ref": str(dep["dependency_ref"]),
            "hash": str(dep["dependency_hash"]),
        } for dep in deps), key=lambda item: (item["kind"], item["ref"]))
        receipt = {
            "schema": "smacx.specialist-result-receipt.v1",
            "source": {
                "timeline_id": str(mission["timeline_id"]),
                "world_epoch": str(mission["world_epoch"]),
                "world_revision": int(mission["source_world_revision"]),
                "observation_cursor": int(mission["observation_cursor"]),
                "world_snapshot_id": mission.get("world_snapshot_id"),
                "corpus_revision": mission.get("corpus_revision"),
            },
            "dependency_hash": content_hash(dependency_rows),
            "representative_evidence_refs": [item["ref"] for item in dependency_rows[:16]],
            "reference_document_hashes": [
                {
                    "evidence_ref": str(dep["dependency_ref"]),
                    "hash": str(dep["dependency_hash"]),
                    "document_id": str((json.loads(
                        dep.get("dependency_payload_json") or "{}"
                    ) or {}).get("document_id") or ""),
                }
                for dep in deps if dep["dependency_kind"] == "reference_document"
            ][:16],
            "usage": {
                "provider_calls": int(usage.get("api_calls") or usage.get("provider_calls") or 0),
                "provider_tokens": int(usage.get("total_tokens") or usage.get("provider_tokens") or 0),
                "peak_context_tokens": int(usage.get("peak_context_tokens") or 0),
                "tool_calls": len(deps),
            },
            "latency_seconds": max(0.0, now - float(attempt_row["started_unix"])),
            "result_hash": result_hash,
            "status": status,
            "stale_reason": stale_reason,
            "limitations_present": bool(result["limitations"]),
        }
        receipt_json = canonical_json(receipt)
        termination_generation = self.store.checkpoint_generation(self.scope.match_id)
        with self.store.transaction() as connection:
            changed = connection.execute(
                "UPDATE specialist_missions SET result_json=?,result_receipt_json=?,result_hash=?,result_preview=?,"
                "accepted_attempt_id=?,stale_reason=?,completion_journal_sequence=NULL,updated_unix=? "
                "WHERE mission_id=? AND status='active' AND accepted_attempt_id IS NULL",
                (result_json, receipt_json, result_hash, preview, attempt_id, stale_reason,
                 now, mission_id),
            ).rowcount
            if changed != 1:
                raise SpecialistError("specialist_late_result_rejected")
            connection.execute(
                "UPDATE specialist_attempts SET provider_calls=?,provider_tokens=?,"
                "peak_context_tokens=?,result_bytes=? WHERE attempt_id=? "
                "AND status IN ('starting','running','validating')",
                (int(usage.get("api_calls") or usage.get("provider_calls") or 0),
                 int(usage.get("total_tokens") or usage.get("provider_tokens") or 0),
                 int(usage.get("peak_context_tokens") or 0), len(result_json.encode()),
                 attempt_id),
            )
            if trace:
                connection.execute(
                    "INSERT OR REPLACE INTO specialist_trace_manifests(attempt_id,mission_id,"
                    "timeline_id,checkpoint_generation,outcome_class,content_path,content_sha256,"
                    "bytes,model_visible,rolled_back,created_unix) VALUES(?,?,?,?,?,?,?,?,0,0,?)",
                    (attempt_id, mission_id, mission["timeline_id"],
                     termination_generation,
                     status, trace["content_path"],
                     trace["content_sha256"], int(trace["bytes"]), now),
                )
                connection.execute(
                    "UPDATE specialist_attempts SET trace_path=?,trace_hash=?,trace_bytes=? "
                    "WHERE attempt_id=?",
                    (trace["content_path"], trace["content_sha256"],
                     int(trace["bytes"]), attempt_id),
                )
        event = self.journal.append(self.scope, "specialist.result_accepted", {
            "mission_id": mission_id, "attempt_id": attempt_id, "status": status,
            "result_hash": result_hash, "dependency_count": len(deps),
            "stale_reason": stale_reason,
        }, idempotency_key=f"specialist-result:{mission_id}")
        self._finalize_prepared_result(
            mission, attempt_id, result, status=status, preview=preview,
            journal_sequence=int(event["sequence"]),
        )
        self._enqueue_terminal_attention(
            mission, status=status, preview=preview,
            limited=bool(result["limitations"]), result_hash=result_hash,
        )
        if mission["world_snapshot_id"]:
            self.world_store.unpin_snapshot(
                str(mission["world_snapshot_id"]), "specialist_mission", mission_id,
            )
        self.world_store.telemetry(
            "specialist", "result_tokens", estimate_tokens(result), scope=self.scope,
            timeline_id=str(mission["timeline_id"]),
            dimensions={"faculty": mission["faculty"], "status": status},
        )
        return {"ok": status == "accepted", "status": status, "mission_id": mission_id,
                "result": result, "result_hash": result_hash,
                "provenance_receipt": receipt,
                "journal_event_id": event["event_id"], "stale_reason": stale_reason}

    def _enqueue_terminal_attention(self, mission: Mapping[str, Any], *, status: str,
                                    preview: str, limited: bool,
                                    result_hash: str = "") -> dict[str, Any]:
        if str(mission["timeline_id"]) != self.store.active_timeline_id(self.scope):
            return {"ok": False, "status": "historical_timeline_suppressed"}
        digest = result_hash or content_hash({
            "mission_id": mission["mission_id"], "status": status,
            "reason": mission.get("stale_reason") or mission.get("cancellation_reason") or preview,
        })
        return self.attention.enqueue(
            "specialist_completion", {
                "mission_id": mission["mission_id"], "faculty": mission["faculty"],
                "status": status, "preview": " ".join(preview.split())[:480],
                "limited": bool(limited), "operation_id": mission.get("linked_operation_id"),
            }, observation_cursor=int(mission["observation_cursor"]),
            priority=80 if mission.get("linked_operation_id") else 60,
            critical=False, dedupe_key=f"specialist:{mission['mission_id']}:{digest}",
        )

    def record_trace(self, mission_id: str, attempt_id: str,
                     trace: Mapping[str, Any]) -> None:
        """Attach a non-model-visible diagnostic trace to any terminal attempt."""
        with self.store.transaction() as connection:
            mission = connection.execute(
                "SELECT timeline_id FROM specialist_missions WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if not mission:
                raise SpecialistError("unknown_specialist_mission")
            connection.execute(
                "INSERT OR REPLACE INTO specialist_trace_manifests(attempt_id,mission_id,"
                "timeline_id,checkpoint_generation,outcome_class,content_path,content_sha256,"
                "bytes,model_visible,rolled_back,created_unix) VALUES(?,?,?,?,?,?,?,?,0,0,?)",
                (attempt_id, mission_id, mission["timeline_id"],
                 self.store.checkpoint_generation(self.scope.match_id),
                 str(trace.get("outcome_class") or "failed"), trace["content_path"],
                 trace["content_sha256"], int(trace["bytes"]), time.time()),
            )
            connection.execute(
                "UPDATE specialist_attempts SET trace_path=?,trace_hash=?,trace_bytes=? "
                "WHERE attempt_id=?",
                (trace["content_path"], trace["content_sha256"], int(trace["bytes"]),
                 attempt_id),
            )

    def fail_attempt(self, mission_id: str, attempt_id: str, outcome: str,
                     reason: str, *, allow_retry: bool = True,
                     schema_repair: bool = False,
                     usage: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if outcome not in ATTEMPT_FAILURES:
            raise SpecialistError("invalid_specialist_attempt_outcome")
        policy = self._policy()
        now = time.time()
        with self.store.transaction() as connection:
            mission = connection.execute(
                "SELECT * FROM specialist_missions WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if not mission:
                raise SpecialistError("unknown_specialist_mission")
            if mission["status"] != "active" or mission["accepted_attempt_id"] is not None:
                raise SpecialistError("specialist_late_attempt_outcome_rejected")
            recorded_usage = usage or {}
            attempt_changed = connection.execute(
                "UPDATE specialist_attempts SET status=?,failure_reason=?,completed_unix=?,"
                "provider_calls=?,provider_tokens=?,peak_context_tokens=? "
                "WHERE attempt_id=? AND mission_id=? "
                "AND status IN ('starting','running','validating')",
                (outcome, reason[:1000], now,
                 int(recorded_usage.get("api_calls")
                     or recorded_usage.get("provider_calls") or 0),
                 int(recorded_usage.get("total_tokens")
                     or recorded_usage.get("provider_tokens") or 0),
                 int(recorded_usage.get("peak_context_tokens") or 0),
                 attempt_id, mission_id),
            ).rowcount
            if attempt_changed != 1:
                raise SpecialistError("specialist_late_attempt_outcome_rejected")
            transient_retry = outcome in TRANSIENT_ATTEMPT_FAILURES \
                and int(mission["attempt_count"]) <= int(policy["automatic_retries"])
            repair_retry = outcome == "invalid_schema" and schema_repair \
                and int(mission["attempt_count"]) <= int(policy["schema_repairs"])
            retry = bool(allow_retry and (transient_retry or repair_retry)
                         and mission["status"] == "active")
            workload = policy[str(mission["execution_class"])]
            deadline = now + int(workload["wall_seconds"] if retry else
                                 policy["manual_retry_retention_seconds"])
            connection.execute(
                "UPDATE specialist_missions SET status=?,deadline_unix=?,updated_unix=? "
                "WHERE mission_id=? "
                "AND status='active' AND accepted_attempt_id IS NULL",
                ("retry_wait" if retry else "failed", deadline, now, mission_id),
            )
        status = "retry_wait" if retry else "failed"
        if not retry:
            mission_dict = dict(mission)
            mission_dict["stale_reason"] = reason[:1000]
            self.journal.append(self.scope, "specialist.mission_failed", {
                "mission_id": mission_id, "attempt_id": attempt_id,
                "failure": outcome, "reason": reason[:1000],
            })
            self._enqueue_terminal_attention(
                mission_dict, status=outcome,
                preview=f"Specialist failed: {outcome}. {reason[:320]}", limited=True,
            )
        return {"ok": False, "mission_id": mission_id,
                "status": status, "failure": outcome}

    def expire(self, mission_id: str) -> dict[str, Any]:
        """Fail a queued mission whose durable overall deadline elapsed."""
        now = time.time()
        with self.store.transaction() as connection:
            mission = connection.execute(
                "SELECT * FROM specialist_missions WHERE mission_id=? AND match_id=? "
                "AND agent_id=? AND perspective_id=?",
                (mission_id, self.scope.match_id, self.scope.agent_id,
                 self.scope.perspective_id),
            ).fetchone()
            if not mission:
                raise SpecialistError("unknown_specialist_mission")
            changed = connection.execute(
                "UPDATE specialist_missions SET status='failed',stale_reason=?,"
                "deadline_unix=?,updated_unix=? "
                "WHERE mission_id=? AND status IN ('queued','retry_wait')",
                ("mission_deadline_expired", now + int(
                    self._policy()["manual_retry_retention_seconds"]), now, mission_id),
            ).rowcount
        if changed:
            mission_dict = dict(mission)
            self.journal.append(self.scope, "specialist.mission_failed", {
                "mission_id": mission_id, "failure": "timed_out",
                "reason": "mission_deadline_expired",
            })
            self._enqueue_terminal_attention(
                mission_dict, status="timed_out",
                preview="Specialist mission expired before an attempt could complete.",
                limited=True,
            )
        return {"ok": bool(changed), "mission_id": mission_id,
                "status": "failed" if changed else str(mission["status"])}

    def reconcile_terminal_attention(self) -> int:
        """Idempotently repair completion delivery after process/service crashes."""
        self.reconcile_prepared_results()
        timeline = self.store.active_timeline_id(self.scope)
        with self.store._connect() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT * FROM specialist_missions WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? AND status IN ('accepted','stale','failed')",
                (self.scope.match_id, self.scope.agent_id, self.scope.perspective_id, timeline),
            ).fetchall()]
        for row in rows:
            result = json.loads(row["result_json"]) if row.get("result_json") else {}
            self._enqueue_terminal_attention(
                row, status=str(row["status"]),
                preview=str(row.get("result_preview") or row.get("stale_reason")
                            or "Specialist mission completed."),
                limited=bool(result.get("limitations")) if isinstance(result, Mapping) else True,
                result_hash=str(row.get("result_hash") or ""),
            )
        return len(rows)

    def reconcile_prepared_results(self) -> int:
        """Finish result publications interrupted between SQLite and journal.

        The journal append is idempotent by mission, and final projection
        publication is idempotent by attempt/result/sequence. Therefore this
        repair is safe after a crash at every individual boundary.
        """
        active_timeline = self.store.active_timeline_id(self.scope)
        # Rollback authority precedes crash-window publication. A result that
        # was prepared on an abandoned branch is diagnostic evidence only.
        self.cancel_for_rollback(active_timeline)
        with self.store._connect() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT * FROM specialist_missions WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? AND status='active' "
                "AND accepted_attempt_id IS NOT NULL AND result_json IS NOT NULL",
                (self.scope.match_id, self.scope.agent_id, self.scope.perspective_id,
                 active_timeline),
            ).fetchall()]
        repaired = 0
        for mission in rows:
            try:
                result = json.loads(mission["result_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(result, Mapping):
                continue
            status = "stale" if mission.get("stale_reason") else "accepted"
            attempt_id = str(mission["accepted_attempt_id"])
            with self.store._connect() as connection:
                dependency_count = int(connection.execute(
                    "SELECT COUNT(*) FROM specialist_dependencies WHERE attempt_id=?",
                    (attempt_id,),
                ).fetchone()[0])
            event = self.journal.append(
                self.scope, "specialist.result_accepted", {
                    "mission_id": mission["mission_id"], "attempt_id": attempt_id,
                    "status": status, "result_hash": mission["result_hash"],
                    "dependency_count": dependency_count,
                    "stale_reason": mission.get("stale_reason"),
                }, timeline_id=str(mission["timeline_id"]),
                idempotency_key=f"specialist-result:{mission['mission_id']}",
            )
            finalized = self._finalize_prepared_result(
                mission, attempt_id, result, status=status,
                preview=str(mission.get("result_preview") or ""),
                journal_sequence=int(event["sequence"]),
            )
            if finalized:
                repaired += 1
            self._enqueue_terminal_attention(
                mission, status=status,
                preview=str(mission.get("result_preview") or "Specialist mission completed."),
                limited=bool(result.get("limitations")),
                result_hash=str(mission.get("result_hash") or ""),
            )
            if mission.get("world_snapshot_id"):
                self.world_store.unpin_snapshot(
                    str(mission["world_snapshot_id"]), "specialist_mission",
                    str(mission["mission_id"]),
                )
        return repaired

    def reconcile_orphans(self, runtime_owner: str) -> int:
        now = time.time()
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT a.attempt_id,a.mission_id FROM specialist_attempts a JOIN "
                "specialist_missions m ON m.mission_id=a.mission_id WHERE a.status IN "
                "('starting','running','validating') AND (a.runtime_owner<>? OR "
                "a.heartbeat_expires_unix<=?) AND m.status='active' "
                "AND m.accepted_attempt_id IS NULL",
                (runtime_owner, now),
            ).fetchall()
        for row in rows:
            self.fail_attempt(str(row["mission_id"]), str(row["attempt_id"]),
                              "orphaned", "supervisor_restart_or_heartbeat_expired")
        return len(rows)

    def cancel_for_operation(self, operation_id: str) -> int:
        with self.store._connect() as connection:
            ids = [str(row["mission_id"]) for row in connection.execute(
                "SELECT mission_id FROM specialist_missions WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND linked_operation_id=? AND status IN "
                "('queued','active','retry_wait')",
                (self.scope.match_id, self.scope.agent_id, self.scope.perspective_id,
                 operation_id),
            ).fetchall()]
        for mission_id in ids:
            self.cancel(mission_id, "cancelled_by_operation")
        return len(ids)

    def cancel_for_turn_handoff(self) -> int:
        """Collect disposable unlinked work at the sovereign turn boundary."""
        with self.store._connect() as connection:
            ids = [str(row["mission_id"]) for row in connection.execute(
                "SELECT mission_id FROM specialist_missions WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? AND linked_operation_id IS NULL "
                "AND result_scope IN ('query','turn') AND status IN ('queued','active','retry_wait')",
                (self.scope.match_id, self.scope.agent_id, self.scope.perspective_id,
                 self.store.active_timeline_id(self.scope)),
            ).fetchall()]
        for mission_id in ids:
            self.cancel(mission_id, "cancelled_by_parent")
        return len(ids)

    def cancel_for_rollback(self, active_timeline_id: str) -> int:
        with self.store._connect() as connection:
            ids = [str(row["mission_id"]) for row in connection.execute(
                "SELECT mission_id FROM specialist_missions WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id<>? AND status IN ('queued','active','retry_wait')",
                (self.scope.match_id, self.scope.agent_id, self.scope.perspective_id,
                 active_timeline_id),
            ).fetchall()]
        for mission_id in ids:
            self.cancel(mission_id, "cancelled_by_rollback", authoritative=True)
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE specialist_trace_manifests SET rolled_back=1,model_visible=0 WHERE "
                "mission_id IN (SELECT mission_id FROM specialist_missions WHERE match_id=? "
                "AND agent_id=? AND perspective_id=? AND timeline_id<>?)",
                (self.scope.match_id, self.scope.agent_id, self.scope.perspective_id,
                 active_timeline_id),
            )
        return len(ids)


def mission_prompt(mission: Mapping[str, Any]) -> str:
    payload = {
        "schema": "smacx.specialist-mission.v2",
        "mission_id": mission["mission_id"], "faculty": mission["faculty"],
        "objective": mission["normalized_objective"],
        "subject_refs": json.loads(mission.get("subject_refs_json") or "[]"),
        "immutable_view": {
            "timeline_id": mission["timeline_id"], "world_epoch": mission["world_epoch"],
            "world_revision": mission["source_world_revision"],
            "observation_cursor": mission["observation_cursor"],
            "world_snapshot_id": mission.get("world_snapshot_id"),
            "world_view_hash": mission.get("world_view_hash"),
            "corpus_revision": mission.get("corpus_revision"),
        },
        "result_contract": RESULT_CONTRACT,
        "instruction": (
            "Investigate with your sole instrument, then return only the JSON result. "
            "For every material non-unknown claims[] entry, copy at least one exact "
            "top-level evidence_refs/citation_receipt value from a successful instrument "
            "result into citations[]. Subject/object/document IDs are not citations. "
            "If no exact receipt supports a statement, keep it in answer or limitations "
            "and omit it from claims[]."
        ),
    }
    if int(mission.get("attempt_number") or 1) > 1:
        prior_failure = str(mission.get("prior_failure_reason") or "invalid result")[:320]
        payload["schema_repair"] = (
            f"A prior attempt was rejected: {prior_failure}. Start a fresh investigation and "
            "emit exactly one JSON object matching result_contract: no prose, preamble, "
            "markdown, or code fence. Claim citations must be exact evidence_refs returned "
            "by successful instrument calls in this attempt. Subject/object/document IDs are "
            "not citations. If no exact receipt supports a statement, keep it in answer or "
            "limitations and omit it from claims[]."
        )
    return canonical_json(payload)
