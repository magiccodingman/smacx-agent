#!/usr/bin/env python3
"""One-purpose stdio MCP instrument for a single specialist attempt."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Literal, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from mcp.server import MCPServer

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


def _reference_dependencies(result: Mapping[str, Any], action: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    stack: list[Any] = [result]
    while stack:
        value = stack.pop()
        if isinstance(value, Mapping):
            document_id = ((value.get("document_id") or value.get("id"))
                           if any(key in value for key in
                                  ("title", "content", "body", "document_id")) else None)
            if document_id:
                rows.append({"kind": "reference_document", "ref": str(document_id),
                             "hash": content_hash(value)})
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    if action in {"search", "tree", "topics", "related"}:
        rows.append({"kind": "reference_coverage",
                     "ref": f"corpus:{MISSION.get('corpus_revision') or 'current'}:{action}",
                     "hash": str(MISSION.get("corpus_revision") or "current")})
    unique = {(row["kind"], row["ref"]): row for row in rows}
    return list(unique.values())


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


if MISSION["faculty"] == "reference":
    mcp = MCPServer(
        "reference-specialist", title="SMACX reference researcher",
        description="Bounded mechanics retrieval for one immutable specialist mission.",
        version="2.0.0",
    )

    @mcp.tool(description="Search/read the local mechanics corpus for this mission. Results are bounded and citations are recorded mechanically.")
    def reference_query(
        action: Literal["topics", "tree", "collection_documents", "search", "get"],
        query: str = "", document_id: str = "", collection_id: str = "",
        limit: int = 8, max_content_tokens: int = 2048,
    ) -> dict[str, Any]:
        sequence = _claim_call()
        if action == "search":
            result = _http_json("POST", "/api/search", {
                "query": query[:2000], "top": min(max(limit, 1), 16),
                "maxContentTokens": min(max(max_content_tokens, 256), 4096),
                "includeContent": True,
                "maxQueryTokens": 1024,
            })
        elif action == "get":
            result = _http_json("GET", "/api/documents/" + quote(document_id, safe=""))
        elif action == "collection_documents":
            result = _http_json("GET", "/api/collections/" + quote(collection_id, safe="")
                                + "/documents")
        elif action == "tree":
            result = _http_json("GET", "/api/tree?includeDocuments=false")
        else:
            result = _http_json("GET", "/api/topics")
        dependencies = _reference_dependencies(result, action)
        SERVICE.record_dependencies(ATTEMPT_ID, sequence, dependencies)
        _trace("mcp_call", {"sequence": sequence, "instrument": "reference_query",
                            "arguments": {"action": action, "query": query,
                                          "document_id": document_id,
                                          "collection_id": collection_id, "limit": limit},
                            "result": result, "dependencies": dependencies})
        return {"ok": True, "evidence_refs": [row["ref"] for row in dependencies],
                "result": result, "bounded": True}

else:
    mcp = MCPServer(
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
            result = WORLD.query(
                mode=mode, subject_refs=subject_refs or (), origin_ref=origin_ref,
                target_ref=target_ref, movement_profile_ref=movement_profile_ref,
                radius=radius, since_cursor=since_cursor, detail=detail,
                continuation=continuation,
                context_length=int(MISSION["context_token_ceiling"]),
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
            return failed
        snapshot_ref = str(MISSION["world_snapshot_id"])
        dependencies = [{"kind": "world_snapshot", "ref": snapshot_ref,
                         "hash": str(MISSION["world_view_hash"])}]
        dependencies.append({
            "kind": "world_query",
            "ref": f"world-query:{MISSION_ID}:{sequence}",
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
        return {**result, "evidence_refs": [row["ref"] for row in dependencies],
                "immutable_world_view": snapshot_ref}


if __name__ == "__main__":
    mcp.run("stdio")
