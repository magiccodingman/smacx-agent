"""Read-only, disposable evidence specialist job contracts.

Provider execution is injected by orchestration. This layer guarantees that a
child never receives sovereign memory, mutation tools, chat-send authority, or
an unpinned live perspective.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid

from smacx_store import MemoryScope, SmacxStore
from smacx_world_store import WorldStore
from smacx_world_types import canonical_json, content_hash


SPECIALIST_SYSTEM_PROMPT = """You are a disposable SMACX evidence specialist, not the player.
Use only the immutable evidence in this request. You are read-only: never make
game actions, send chat, write memory, invent hidden state, choose strategy, or
claim sovereignty. Return one JSON object and no prose or Markdown. Its exact
shape is:
{"specialist_job_id":string,"answer":string,"claims":[{"claim":string,
"evidence_refs":[string],"epistemic_status":"current|stale|reported|derived|
estimated|unknown"}],"limitations":[string],"unresolved_questions":[string],
"source_revision":integer,"dependency_refs":[string],"dependency_hash":string}.
Echo specialist_job_id, identity.world_revision, dependency_refs, and
dependency_hash exactly from the request. Every claim must cite only provided
evidence_ref values and preserve limitations, freshness, dependencies, and
unresolved ambiguity. Your answer is evidence for the sovereign, never an
authoritative strategy or decision."""

SPECIALIST_KINDS = frozenset({"reference_researcher", "world_analyst"})
JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL | re.IGNORECASE)


class SpecialistError(RuntimeError):
    pass


def _provider_profile(path: Path | None = None) -> dict[str, Any]:
    source = path or Path(os.environ.get(
        "SMACX_SPECIALIST_PROFILE_FILE", "/run/secrets/specialist-provider.json",
    ))
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpecialistError("specialist_provider_not_configured") from exc
    if not isinstance(value, dict) or not isinstance(value.get("base_url"), str) \
            or not isinstance(value.get("model_id"), str):
        raise SpecialistError("invalid_specialist_provider_profile")
    return value


def _extract_json(content: str) -> dict[str, Any]:
    match = JSON_FENCE.search(content)
    candidate = match.group(1) if match else content.strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        try:
            value = json.loads(candidate[start:end + 1])
        except (json.JSONDecodeError, ValueError) as exc:
            raise SpecialistError("specialist_provider_returned_non_json") from exc
    if not isinstance(value, dict):
        raise SpecialistError("specialist_provider_returned_non_object")
    return value


def invoke_openai_specialist(system_prompt: str, request_payload: Mapping[str, Any],
                             *, profile_path: Path | None = None) -> Mapping[str, Any]:
    """Invoke an OpenAI-compatible child with no tools or sovereign state."""
    profile = _provider_profile(profile_path)
    body: dict[str, Any] = {
        "model": str(profile["model_id"]),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": canonical_json(request_payload)},
        ],
        "stream": False,
        "max_tokens": min(int(request_payload.get("token_budget", 4096)), 8192),
    }
    settings = profile.get("generation_settings")
    if isinstance(settings, Mapping):
        for key in ("temperature", "top_p", "presence_penalty", "frequency_penalty", "seed"):
            if key in settings:
                body[key] = settings[key]
        extras = settings.get("extra_parameters")
        if isinstance(extras, Mapping):
            body.update({str(key): value for key, value in extras.items()
                         if key not in {"model", "messages", "tools", "tool_choice", "stream"}})
    effort = str(profile.get("reasoning_effort") or "none")
    if effort != "none":
        body["reasoning_effort"] = effort
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    key_path = Path(os.environ.get(
        "SMACX_SPECIALIST_PROVIDER_KEY_FILE", "/run/secrets/specialist-provider-key",
    ))
    try:
        api_key = key_path.read_text(encoding="utf-8").strip()
    except OSError:
        api_key = ""
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    request = Request(
        str(profile["base_url"]).rstrip("/") + "/chat/completions",
        data=canonical_json(body).encode(), headers=headers, method="POST",
    )
    timeout = min(max(int(request_payload.get("time_budget_seconds", 60)), 5), 120)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(4_000_001)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise SpecialistError("specialist_provider_unavailable") from exc
    if len(raw) > 4_000_000:
        raise SpecialistError("specialist_provider_response_too_large")
    try:
        provider_response = json.loads(raw)
        content = str(provider_response["choices"][0]["message"].get("content") or "")
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise SpecialistError("invalid_specialist_provider_response") from exc
    result = _extract_json(content)
    result["usage"] = provider_response.get("usage", {})
    result["provider_profile"] = {
        "profile_id": profile.get("profile_id"), "model_id": profile.get("model_id"),
    }
    return result


class SpecialistService:
    def __init__(self, store: SmacxStore, world_store: WorldStore,
                 scope: MemoryScope) -> None:
        self.store = store
        self.world_store = world_store
        self.scope = scope

    @staticmethod
    def _installation_cap() -> int:
        try:
            return min(max(int(_provider_profile().get("max_concurrency", 2)), 1), 16)
        except SpecialistError:
            return 2

    def create(self, *, kind: str, question: str, evidence: Sequence[Mapping[str, Any]],
               corpus_revision: str | None = None, token_budget: int = 4096,
               time_budget_seconds: int = 60) -> dict[str, Any]:
        if kind not in SPECIALIST_KINDS:
            raise SpecialistError("invalid_specialist_kind")
        if not question.strip() or len(question) > 4000 or not 512 <= token_budget <= 8192:
            raise SpecialistError("invalid_specialist_request")
        timeline = self.store.active_timeline_id(self.scope)
        projection = self.world_store.load(self.scope, timeline)
        if not projection:
            raise SpecialistError("world_projection_unavailable")
        refs = []
        immutable = []
        for item in evidence[:256]:
            if not isinstance(item, Mapping) or not isinstance(item.get("evidence_ref"), str):
                raise SpecialistError("invalid_specialist_evidence")
            refs.append(str(item["evidence_ref"]))
            immutable.append(dict(item))
        job_id = "specialist-" + uuid.uuid4().hex
        identity = projection["identity"]
        world_objects = {str(item["object_ref"]): item
                         for item in projection.get("objects", ())}
        dependency_refs = sorted({ref for ref in refs if ref in world_objects})
        dependency_hash = content_hash({ref: world_objects[ref] for ref in dependency_refs})
        request = {
            "schema": "smacx.specialist-job.v1", "specialist_job_id": job_id,
            "specialist_kind": kind, "question": question.strip(),
            "identity": {**identity, "world_revision": projection["world_revision"],
                         "observation_cursor": projection["observation_cursor"]},
            "immutable_evidence": immutable, "corpus_revision": corpus_revision,
            "dependency_refs": dependency_refs, "dependency_hash": dependency_hash,
            "token_budget": token_budget,
            "time_budget_seconds": min(max(time_budget_seconds, 5), 120),
        }
        now = time.time()
        with self.store.transaction() as connection:
            installation_running = int(connection.execute(
                "SELECT COUNT(*) AS count FROM specialist_jobs WHERE status IN ('queued','running')"
            ).fetchone()["count"])
            if installation_running >= self._installation_cap():
                raise SpecialistError("specialist_installation_concurrency_limit")
            running = int(connection.execute(
                "SELECT COUNT(*) AS count FROM specialist_jobs WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=? AND status IN ('queued','running')",
                (self.scope.match_id, self.scope.agent_id, self.scope.perspective_id, timeline),
            ).fetchone()["count"])
            if running:
                raise SpecialistError("specialist_concurrency_limit")
            connection.execute(
                "INSERT INTO specialist_jobs(specialist_job_id,match_id,agent_id,perspective_id,"
                "timeline_id,world_epoch,world_revision,observation_cursor,specialist_kind,"
                "question,evidence_refs_json,request_json,corpus_revision,input_hash,dependency_hash,status,"
                "usage_json,provider_profile_json,created_unix,updated_unix) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'queued','{}','{}',?,?)",
                (job_id, self.scope.match_id, self.scope.agent_id, self.scope.perspective_id,
                 timeline, identity["world_epoch"], projection["world_revision"],
                 projection["observation_cursor"], kind, question.strip(), canonical_json(refs),
                 canonical_json(request), corpus_revision, content_hash(request), dependency_hash,
                 now, now),
            )
        return request

    def run(self, request: Mapping[str, Any],
            invoke: Callable[[str, Mapping[str, Any]], Mapping[str, Any]]) -> dict[str, Any]:
        job_id = str(request.get("specialist_job_id") or "")
        started = time.monotonic()
        with self.store.transaction() as connection:
            changed = connection.execute(
                "UPDATE specialist_jobs SET status='running',updated_unix=? WHERE "
                "specialist_job_id=? AND status='queued'", (time.time(), job_id),
            ).rowcount
        if changed != 1:
            raise SpecialistError("specialist_job_not_queued")
        try:
            raw = invoke(SPECIALIST_SYSTEM_PROMPT, request)
            result = self._validate_result(job_id, request, raw)
            current = self.world_store.load(
                self.scope, self.store.active_timeline_id(self.scope),
            )
            pinned = request.get("identity", {})
            stale = not current or any((
                current["identity"].get("timeline_id") != pinned.get("timeline_id"),
                current["identity"].get("world_epoch") != pinned.get("world_epoch"),
                int(current["observation_cursor"]) < int(pinned.get("observation_cursor", -1)),
            ))
            stale_reason = "timeline_or_world_epoch_changed" if stale else None
            if not stale and int(current["world_revision"]) != int(pinned.get("world_revision", -1)):
                current_objects = {str(item["object_ref"]): item
                                   for item in current.get("objects", ())}
                dependency_refs = [str(ref) for ref in request.get("dependency_refs", ())]
                current_dependency = content_hash({
                    ref: current_objects.get(ref) for ref in dependency_refs
                })
                stale = current_dependency != request.get("dependency_hash")
                stale_reason = "world_dependency_changed" if stale else None
            status = "stale" if stale else "accepted"
            latency = (time.monotonic() - started) * 1000
            profile = raw.get("provider_profile", {}) if isinstance(raw, Mapping) else {}
            with self.store.transaction() as connection:
                connection.execute(
                    "UPDATE specialist_jobs SET result_json=?,result_hash=?,status=?,usage_json=?,"
                    "provider_profile_json=?,latency_ms=?,updated_unix=? WHERE specialist_job_id=?",
                    (canonical_json(result), content_hash(result), status,
                     canonical_json(raw.get("usage", {}) if isinstance(raw, Mapping) else {}),
                     canonical_json(profile), latency, time.time(), job_id),
                )
            self.world_store.telemetry(
                "specialist", "latency_ms", latency, scope=self.scope,
                timeline_id=str(pinned.get("timeline_id") or ""),
                dimensions={"kind": request.get("specialist_kind"), "status": status,
                            "profile_id": profile.get("profile_id"),
                            "model_id": profile.get("model_id")},
            )
            input_tokens = max(1, len(canonical_json(request.get("immutable_evidence", ()))) // 4)
            output_tokens = max(1, len(canonical_json(result)) // 4)
            self.world_store.telemetry(
                "specialist", "estimated_sovereign_tokens_avoided",
                max(0, input_tokens - output_tokens), scope=self.scope,
                timeline_id=str(pinned.get("timeline_id") or ""),
                dimensions={"kind": request.get("specialist_kind"),
                            "profile_id": profile.get("profile_id"),
                            "model_id": profile.get("model_id")},
            )
            return {"ok": not stale, "status": status, "result": result,
                    "stale_reason": stale_reason,
                    "usage": raw.get("usage", {}) if isinstance(raw, Mapping) else {},
                    "latency_ms": latency, "result_hash": content_hash(result)}
        except Exception:
            with self.store.transaction() as connection:
                connection.execute(
                    "UPDATE specialist_jobs SET status='failed',latency_ms=?,updated_unix=? "
                    "WHERE specialist_job_id=?",
                    ((time.monotonic() - started) * 1000, time.time(), job_id),
                )
            raise

    @staticmethod
    def _validate_result(job_id: str, request: Mapping[str, Any],
                         raw: Mapping[str, Any]) -> dict[str, Any]:
        required = {"specialist_job_id", "answer", "claims", "limitations",
                    "unresolved_questions", "source_revision", "dependency_refs",
                    "dependency_hash"}
        if not isinstance(raw, Mapping) or not required.issubset(raw) \
                or raw.get("specialist_job_id") != job_id:
            raise SpecialistError("invalid_specialist_result_schema")
        if not isinstance(raw["claims"], list) or len(raw["claims"]) > 64:
            raise SpecialistError("invalid_specialist_claims")
        if not isinstance(raw["answer"], str) \
                or not isinstance(raw["limitations"], list) \
                or not all(isinstance(item, str) for item in raw["limitations"]) \
                or not isinstance(raw["unresolved_questions"], list) \
                or not all(isinstance(item, str) for item in raw["unresolved_questions"]):
            raise SpecialistError("invalid_specialist_result_schema")
        allowed_evidence = {str(item.get("evidence_ref"))
                            for item in request.get("immutable_evidence", ())
                            if isinstance(item, Mapping)}
        for claim in raw["claims"]:
            if not isinstance(claim, Mapping) or not isinstance(claim.get("claim"), str) \
                    or not isinstance(claim.get("evidence_refs"), list) \
                    or claim.get("epistemic_status") not in {
                        "current", "stale", "reported", "derived", "estimated", "unknown",
                    }:
                raise SpecialistError("invalid_specialist_claim")
            if not set(map(str, claim["evidence_refs"])).issubset(allowed_evidence):
                raise SpecialistError("specialist_claim_uses_unprovided_evidence")
        identity = request.get("identity") if isinstance(request.get("identity"), Mapping) else {}
        if int(raw.get("source_revision", -1)) != int(identity.get("world_revision", -2)) \
                or list(raw.get("dependency_refs") or []) != list(request.get("dependency_refs") or []) \
                or raw.get("dependency_hash") != request.get("dependency_hash"):
            raise SpecialistError("specialist_result_dependency_mismatch")
        return {key: raw[key] for key in required}

    def load_request(self, specialist_job_id: str) -> dict[str, Any]:
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT request_json FROM specialist_jobs WHERE specialist_job_id=? "
                "AND match_id=? AND agent_id=? AND perspective_id=? AND timeline_id=?",
                (specialist_job_id, self.scope.match_id, self.scope.agent_id,
                 self.scope.perspective_id, self.store.active_timeline_id(self.scope)),
            ).fetchone()
        if not row:
            raise SpecialistError("unknown_specialist_job")
        value = json.loads(row["request_json"])
        if not isinstance(value, dict):
            raise SpecialistError("invalid_specialist_job_request")
        return value

    def retry(self, specialist_job_id: str) -> None:
        with self.store.transaction() as connection:
            changed = connection.execute(
                "UPDATE specialist_jobs SET status='queued',updated_unix=? WHERE "
                "specialist_job_id=? AND match_id=? AND agent_id=? AND perspective_id=? "
                "AND timeline_id=? AND status IN ('failed','abandoned')",
                (time.time(), specialist_job_id, self.scope.match_id, self.scope.agent_id,
                 self.scope.perspective_id, self.store.active_timeline_id(self.scope)),
            ).rowcount
        if changed != 1:
            raise SpecialistError("specialist_job_not_retriable")

    def abandon_running(self) -> int:
        with self.store.transaction() as connection:
            return connection.execute(
                "UPDATE specialist_jobs SET status='abandoned',updated_unix=? WHERE match_id=? "
                "AND agent_id=? AND perspective_id=? AND timeline_id=? AND status='running'",
                (time.time(), self.scope.match_id, self.scope.agent_id,
                 self.scope.perspective_id, self.store.active_timeline_id(self.scope)),
            ).rowcount
