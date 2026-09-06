#!/usr/bin/env python3
"""One-purpose stdio MCP instrument for a single specialist attempt."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Literal, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from smacx_mcp_validation import StrictMCPServer

from smacx_specialists import SpecialistError, SpecialistService
from smacx_store import MemoryScope, SmacxStore
from smacx_world import WORLD_MODES, WorldQueryError, WorldService
from smacx_world_model import CALCULATOR_VERSION
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity, WorldObject, canonical_json, content_hash, material_hash


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SpecialistError(f"missing_{name.casefold()}")
    return value


STORE = SmacxStore(_required("SMACX_DB_PATH"))
MISSION_ID = _required("SMACX_SPECIALIST_MISSION_ID")
ATTEMPT_ID = _required("SMACX_SPECIALIST_ATTEMPT_ID")
CAPABILITY_FILE = Path(_required("SMACX_SPECIALIST_CAPABILITY_FILE"))
CAPABILITY_RAW = CAPABILITY_FILE.read_text(encoding="utf-8").strip()
if hashlib.sha256(CAPABILITY_RAW.encode()).hexdigest() != _required("SMACX_SPECIALIST_CAPABILITY_HASH"):
    raise SpecialistError("specialist_capability_integrity_failure")
try:
    CAPABILITY = json.loads(CAPABILITY_RAW)
except json.JSONDecodeError as exc:
    raise SpecialistError("specialist_capability_invalid") from exc

with STORE._connect() as connection:
    _MISSION_ROW = connection.execute(
        "SELECT * FROM specialist_missions WHERE mission_id=?", (MISSION_ID,),
    ).fetchone()
if not _MISSION_ROW:
    raise SpecialistError("unknown_specialist_mission")
MISSION = dict(_MISSION_ROW)
SCOPE = MemoryScope(MISSION["match_id"], MISSION["agent_id"], MISSION["perspective_id"])
SERVICE = SpecialistService(STORE, WorldStore(STORE), SCOPE)
EXPECTED_CAPABILITY = {
    "mission_id": MISSION_ID, "attempt_id": ATTEMPT_ID,
    "faculty": MISSION["faculty"], "match_id": MISSION["match_id"],
    "agent_id": MISSION["agent_id"], "perspective_id": MISSION["perspective_id"],
    "timeline_id": MISSION["timeline_id"], "world_epoch": MISSION["world_epoch"],
    "world_revision": int(MISSION["source_world_revision"]),
    "observation_cursor": int(MISSION["observation_cursor"]),
    "world_snapshot_id": MISSION["world_snapshot_id"],
    "world_view_hash": MISSION["world_view_hash"],
}
if not isinstance(CAPABILITY, Mapping) or any(
        CAPABILITY.get(key) != value for key, value in EXPECTED_CAPABILITY.items()):
    raise SpecialistError("specialist_capability_scope_mismatch")
if CAPABILITY.get("instrument") != f"{MISSION['faculty']}_query":
    raise SpecialistError("specialist_capability_instrument_mismatch")
EVENT_LOG = Path(_required("SMACX_SPECIALIST_EVENT_LOG"))


def _trace(kind: str, payload: Mapping[str, Any]) -> None:
    row = {"timestamp_unix": time.time(), "kind": kind, "payload": dict(payload)}
    with EVENT_LOG.open("a", encoding="utf-8") as stream:
        stream.write(canonical_json(row) + "\n")


def _claim_call() -> int:
    return SERVICE.claim_tool_call(ATTEMPT_ID)


def _remaining_calls(sequence: int) -> int:
    return max(0, int(MISSION["tool_budget"]) - int(sequence))


def _world_evidence_view(result: Mapping[str, Any], evidence_ref: str) -> dict[str, Any]:
    """Return the bounded evidence page the disposable child actually needs.

    The frozen world service returns cache and dependency-integrity metadata
    intended for a sovereign caller.  A specialist attempt records that full
    dependency graph durably below, so echoing every object ref, material hash,
    query fingerprint, and immutable identity through the provider transcript
    only duplicates authority and makes the context grow with the number of
    queried objects.  The child receives one opaque, exact citation receipt;
    result validation resolves it against the complete server-held graph.
    """
    omitted = {
        "cache", "dependency_hash", "dependency_refs", "dependency_ref_count",
        "dependency_refs_truncated", "identity", "observation_cursor",
        "retention_class", "valid_while", "world_revision",
    }
    view = {str(key): value for key, value in result.items() if key not in omitted}
    view["evidence_refs"] = [evidence_ref]
    view["citation_receipt"] = evidence_ref
    view["citation_rule"] = (
        "For a non-unknown claims[] fact based on this result, copy "
        "citation_receipt exactly into citations[]. object_ref, location_ref, "
        "region_ref, and other subject IDs are not citations."
    )
    view["evidence_scope"] = "immutable_frozen_world_query"
    view["result_token_estimate"] = max(1, len(canonical_json(view)) // 4)
    return view


def _http_json(method: str, path: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
    base = _required("SMACX_REFERENCE_URL").rstrip("/")
    data = canonical_json(body).encode() if body is not None else None
    request = Request(base + path, data=data, method=method, headers={
        "Accept": "application/json", "Content-Type": "application/json",
    })
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read(1_000_001)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise SpecialistError("reference_query_unavailable") from exc
    if len(raw) > 1_000_000:
        raise SpecialistError("reference_query_result_too_large")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise SpecialistError("reference_query_invalid_result")
    return value


def _reference_dependencies(result: Mapping[str, Any], action: str,
                            call_sequence: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    stack: list[Any] = [result]
    while stack:
        value = stack.pop()
        if isinstance(value, Mapping):
            document_id = ((value.get("document_id") or value.get("id"))
                           if any(key in value for key in
                                  ("title", "content", "body", "document_id")) else None)
            if document_id:
                digest = content_hash(value)
                receipt = "evidence-reference-" + content_hash({
                    "attempt_id": ATTEMPT_ID, "call_sequence": call_sequence,
                    "document_id": str(document_id), "hash": digest,
                })[:24]
                rows.append({"kind": "reference_document", "ref": receipt,
                             "hash": digest,
                             "payload": {"document_id": str(document_id),
                                         "document_hash": digest,
                                         "corpus_revision": MISSION.get("corpus_revision")}})
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    if action in {"search", "tree", "topics", "related"}:
        digest = str(MISSION.get("corpus_revision") or "current")
        receipt = "evidence-reference-" + content_hash({
            "attempt_id": ATTEMPT_ID, "call_sequence": call_sequence,
            "coverage": action, "revision": digest,
        })[:24]
        rows.append({"kind": "reference_coverage", "ref": receipt,
                     "hash": digest,
                     "payload": {"coverage_action": action,
                                 "corpus_revision": digest}})
    unique = {(row["kind"], row["ref"]): row for row in rows}
    return list(unique.values())


def _frozen_reference() -> dict[str, Any]:
    path = Path(str(MISSION.get("reference_snapshot_path") or "")).resolve()
    expected_root = (STORE.path.parent / "reference-snapshots").resolve()
    try:
        path.relative_to(expected_root)
    except ValueError as exc:
        raise SpecialistError("reference_snapshot_path_invalid") from exc
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SpecialistError("reference_snapshot_unavailable") from exc
    if hashlib.sha256(raw).hexdigest() != str(MISSION.get("reference_snapshot_hash") or ""):
        raise SpecialistError("reference_snapshot_integrity_failure")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SpecialistError("reference_snapshot_invalid") from exc
    if not isinstance(value, dict) or value.get("revision") != MISSION.get("corpus_revision"):
        raise SpecialistError("reference_snapshot_revision_mismatch")
    return value


def _reference_page(action: str, *, query: str, document_id: str,
                    collection_id: str, limit: int, max_content_tokens: int,
                    continuation: str) -> dict[str, Any]:
    snapshot = _frozen_reference()
    documents = [item for item in snapshot.get("documents", ()) if isinstance(item, Mapping)]
    collections = [item for item in snapshot.get("collections", ()) if isinstance(item, Mapping)]
    limit = min(max(int(limit), 1), 16)
    ceiling = min(max(int(max_content_tokens), 256), 4096)
    if action == "get":
        document = next((dict(item) for item in documents
                         if str(item.get("document_id") or "") == document_id), None)
        if document is None:
            raise SpecialistError("reference_document_not_found")
        body = str(document.get("body") or "")
        match = re.fullmatch(r"body-(\d+)", continuation or "body-0")
        if not match:
            raise SpecialistError("invalid_reference_continuation")
        start = int(match.group(1)); character_budget = max(256, ceiling * 4)
        end = min(len(body), start + character_budget)
        document["body"] = body[start:end]
        document["body_offset"] = start
        document["body_complete"] = end >= len(body)
        return {"revision": snapshot["revision"], "document": document,
                "continuation": None if end >= len(body) else f"body-{end}"}
    if action == "search":
        terms = set(re.findall(r"[a-z0-9]{2,}", query.casefold()))
        if not terms:
            raise SpecialistError("reference_query_required")
        ranked = []
        for item in documents:
            title = str(item.get("title") or "")
            description = str(item.get("description") or "")
            body = str(item.get("body") or "")
            title_terms = set(re.findall(r"[a-z0-9]{2,}", title.casefold()))
            text_terms = set(re.findall(r"[a-z0-9]{2,}", (description + " " + body).casefold()))
            score = 8 * len(terms & title_terms) + len(terms & text_terms)
            if score:
                ranked.append((score, title.casefold(), item))
        rows = []
        remaining = ceiling * 4
        for _, _, item in sorted(ranked, key=lambda row: (-row[0], row[1]))[:limit]:
            excerpt = str(item.get("body") or "")[:max(0, min(1200, remaining))]
            row = {key: item.get(key) for key in (
                "document_id", "title", "description", "collection_id",
                "collection_path", "source_hash",
            )}
            row["excerpt"] = excerpt
            remaining -= len(excerpt)
            rows.append(row)
            if remaining <= 0:
                break
        return {"revision": snapshot["revision"], "results": rows,
                "continuation": None, "search_kind": "immutable_lexical"}
    source = collections if action in {"topics", "tree"} else [
        item for item in documents if str(item.get("collection_id") or "") == collection_id
    ]
    match = re.fullmatch(r"cursor-(\d+)", continuation or "cursor-0")
    if not match:
        raise SpecialistError("invalid_reference_continuation")
    start = int(match.group(1)); page = [dict(item) for item in source[start:start + limit]]
    next_cursor = start + len(page)
    return {"revision": snapshot["revision"],
            "collections" if action in {"topics", "tree"} else "documents": page,
            "continuation": None if next_cursor >= len(source) else f"cursor-{next_cursor}"}


def _frozen_world() -> tuple[WorldService, tempfile.TemporaryDirectory[str]]:
    snapshot_id = str(MISSION.get("world_snapshot_id") or "")
    if not snapshot_id:
        raise SpecialistError("world_specialist_snapshot_missing")
    payload = WorldStore(STORE).load_snapshot_content(snapshot_id)
    temporary = tempfile.TemporaryDirectory(prefix="smacx-frozen-world-")
    frozen_store = SmacxStore(Path(temporary.name) / "world.sqlite3")
    frozen_store.ensure_agent(SCOPE.agent_id, "Disposable world analyst")
    frozen_store.create_match(
        match_id=SCOPE.match_id, display_name="Frozen specialist view", mode="specialist",
        metadata={"active_memory_timeline": MISSION["timeline_id"]},
    )
    frozen_store.create_perspective(
        SCOPE.match_id, SCOPE.agent_id, perspective_id=SCOPE.perspective_id,
    )
    projection = payload["projection"]
    identity = WorldIdentity(**projection["identity"])
    world_store = WorldStore(frozen_store, Path(temporary.name) / "snapshots")
    world_store.replace_projection(
        SCOPE, identity, [WorldObject.from_dict(item) for item in projection["objects"]],
        observation_cursor=int(projection["observation_cursor"]),
        action_revision=projection.get("action_revision"),
        continuity=str(projection.get("continuity") or "complete"),
        journal_head_hash=str(payload["journal_head_hash"]),
    )
    for row in payload.get("temporal_events", ()):
        if not isinstance(row, Mapping) or not isinstance(row.get("event"), Mapping):
            continue
        world_store.record_observation_projection(
            SCOPE, identity.timeline_id,
            {"sequence": int(row["observation_cursor"]), "kind": "semantic_event",
             "turn": row.get("turn"), "payload": row["event"],
             "continuity": str(row.get("continuity") or "complete")},
            str(row["journal_event_id"]),
        )
    return WorldService(world_store, SCOPE), temporary


class SpecialistMCPServer(StrictMCPServer):
    def record_argument_rejection(self, payload):
        _trace("managed_tool_validation_rejected", payload)


if MISSION["faculty"] == "reference":
    mcp = SpecialistMCPServer(
        "reference-specialist", title="SMACX reference researcher",
        description="Bounded mechanics retrieval for one immutable specialist mission.",
        version="2.0.0",
    )

    @mcp.tool(description="Search/read the local mechanics corpus for this mission. Results are bounded and citations are recorded mechanically.")
    def reference_query(
        action: Literal["topics", "tree", "collection_documents", "search", "get"],
        query: str = "", document_id: str = "", collection_id: str = "",
        limit: int = 8, max_content_tokens: int = 2048, continuation: str = "",
    ) -> dict[str, Any]:
        sequence = _claim_call()
        result = _reference_page(
            action, query=query[:2000], document_id=document_id,
            collection_id=collection_id, limit=limit,
            max_content_tokens=max_content_tokens, continuation=continuation,
        )
        dependencies = _reference_dependencies(result, action, sequence)
        SERVICE.record_dependencies(ATTEMPT_ID, sequence, dependencies)
        _trace("mcp_call", {"sequence": sequence, "instrument": "reference_query",
                            "arguments": {"action": action, "query": query,
                                          "document_id": document_id,
                                          "collection_id": collection_id, "limit": limit,
                                          "continuation": continuation},
                            "result": result, "dependencies": dependencies})
        evidence_refs = [row["ref"] for row in dependencies]
        return {"ok": True, "evidence_refs": evidence_refs,
                "citation_receipts": evidence_refs,
                "citation_rule": (
                    "For a non-unknown claims[] fact based on this result, copy at least "
                    "one citation_receipts value exactly into citations[]. Document IDs, "
                    "collection IDs, and other subject IDs are not citations."
                ), "result": result, "bounded": True,
                "remaining_evidence_calls": _remaining_calls(sequence)}

else:
    mcp = SpecialistMCPServer(
        "world-specialist", title="SMACX frozen world analyst",
        description="Deterministic read-only analysis of one pinned perspective revision.",
        version="2.0.0",
    )
    WORLD, _WORLD_TEMP = _frozen_world()

    @mcp.tool(description="Query the frozen fair-play world view. It never refreshes live state and cannot mutate gameplay.")
    def world_query(
        mode: Literal["overview", "area", "relation", "route", "reachability", "compare",
                      "base", "forces", "logistics", "intel", "changes", "global", "render"],
        subject_refs: list[str] | None = None, origin_ref: str = "", target_ref: str = "",
        movement_profile_ref: str = "mobility-land-default", radius: int = 3,
        since_cursor: int = 0, detail: Literal["compact", "standard", "deep"] = "standard",
        continuation: str = "",
    ) -> dict[str, Any]:
        sequence = _claim_call()
        query_payload = {
            "mode": mode, "subject_refs": subject_refs or (),
            "origin_ref": origin_ref, "target_ref": target_ref,
            "movement_profile_ref": movement_profile_ref, "radius": radius,
            "since_cursor": since_cursor, "detail": detail,
            "continuation": continuation,
        }
        try:
            # A disposable child is a context reducer, not a second sovereign
            # carrying the full rich-tier world envelope.  Keep each frozen
            # evidence page at the 64K world-budget tier even when the child
            # model itself has a larger context window; it can use continuation
            # or another focused query when more evidence is material.
            specialist_world_context = min(
                int(MISSION["context_token_ceiling"]), 65_536,
            )
            result = WORLD.query(
                mode=mode, subject_refs=subject_refs or (), origin_ref=origin_ref,
                target_ref=target_ref, movement_profile_ref=movement_profile_ref,
                radius=radius, since_cursor=since_cursor, detail=detail,
                continuation=continuation,
                context_length=specialist_world_context,
            )
        except (WorldQueryError, ValueError) as exc:
            failed = {"ok": False, "error": str(exc),
                      "valid_modes": sorted(WORLD_MODES)}
            _trace("mcp_call", {
                "sequence": sequence, "instrument": "world_query",
                "arguments": {"mode": mode, "subject_refs": subject_refs or [],
                              "origin_ref": origin_ref, "target_ref": target_ref,
                              "movement_profile_ref": movement_profile_ref,
                              "radius": radius, "since_cursor": since_cursor,
                              "detail": detail},
                "result": failed, "dependencies": [],
            })
            return {**failed, "remaining_evidence_calls": _remaining_calls(sequence)}
        if not result.get("ok"):
            # A typed failed lookup is feedback to the disposable analyst, not
            # evidence about the frozen world.  Recording it as a world-query
            # dependency would later replay an error without a dependency hash
            # and falsely stale an otherwise valid mission result.
            _trace("mcp_call", {
                "sequence": sequence, "instrument": "world_query",
                "arguments": query_payload, "result": result,
                "dependencies": [],
            })
            return {**result, "evidence_refs": [],
                    "immutable_world_view": str(MISSION["world_snapshot_id"]),
                    "remaining_evidence_calls": _remaining_calls(sequence)}
        snapshot_ref = str(MISSION["world_snapshot_id"])
        dependencies = [{"kind": "world_snapshot", "ref": snapshot_ref,
                         "hash": str(MISSION["world_view_hash"])}]
        query_ref = f"world-query:{MISSION_ID}:{sequence}"
        dependencies.append({
            "kind": "world_query",
            "ref": query_ref,
            "hash": str(result.get("dependency_hash") or content_hash(result)),
            "payload": query_payload,
        })
        objects = {str(item["object_ref"]): item for item in
                   WORLD._projection()[1].get("objects", ())}
        for ref in result.get("dependency_refs", ()):
            if str(ref) in objects:
                dependencies.append({"kind": "world_object", "ref": str(ref),
                                     "hash": material_hash(objects[str(ref)])})
        dependencies.append({"kind": "calculator", "ref": "world-calculator",
                             "hash": CALCULATOR_VERSION})
        SERVICE.record_dependencies(ATTEMPT_ID, sequence, dependencies)
        _trace("mcp_call", {"sequence": sequence, "instrument": "world_query",
                            "arguments": query_payload,
                            "result": result, "dependencies": dependencies})
        return {**_world_evidence_view(result, query_ref),
                "immutable_world_view": snapshot_ref,
                "remaining_evidence_calls": _remaining_calls(sequence)}


if __name__ == "__main__":
    mcp.run("stdio")
