"""Client for the locally built SemanticKnowledge mechanics encyclopedia."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


REFERENCE_URL = os.environ.get("SMACX_REFERENCE_URL", "http://knowledge-service:8090").rstrip("/")


def _request(path: str, payload: dict[str, Any] | None = None, *, timeout: float = 15.0) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = Request(
        REFERENCE_URL + path,
        data=body,
        headers={"Accept": "application/json", **({"Content-Type": "application/json"} if body else {})},
        method="POST" if body is not None else "GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read(4_000_001)
        if len(data) > 4_000_000:
            return {"ok": False, "error": "reference_response_too_large"}
        result = json.loads(data)
        return result if isinstance(result, dict) else {"ok": False, "error": "invalid_reference_response"}
    except HTTPError as exc:
        return {"ok": False, "error": f"reference_http_{exc.code}"}
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        return {"ok": False, "error": "reference_service_unavailable"}


def _search(query: str, *, topic: str = "", limit: int = 8, include_body: bool = False,
            max_query_tokens: int = 1_024) -> dict[str, Any]:
    result = _request("/api/search", {
        "query": query,
        "topic": topic or None,
        "top": min(max(int(limit), 1), 30),
        "maxContentTokens": 16_000 if include_body else 4_000,
        "includeContent": include_body,
        "maxQueryTokens": min(max(int(max_query_tokens), 32), 4_096),
    })
    if result.get("error"):
        return {"ok": False, "error": str(result["error"])}
    return {"ok": True, **result}


def read_reference(_store: Any, action: str, *, query: str = "", topic: str = "",
                   document_id: str = "", collection_id: str = "", limit: int = 8,
                   include_body: bool = False, include_documents: bool = False,
                   max_query_tokens: int = 1_024,
                   private_prefix: str | None = None, entity_kind: str = "",
                   entity_key: str = "", entities: list[dict[str, str]] | None = None,
                   ruleset_id: str = "smacx") -> dict[str, Any]:
    _ = private_prefix
    if action == "status":
        result = _request("/api/status", timeout=5.0)
        return {"ok": not bool(result.get("error")), **result}
    if action == "topics":
        result = _request("/api/topics")
        return {"ok": not bool(result.get("error")), **result}
    if action == "tree":
        result = _request("/api/tree" + ("?includeDocuments=true" if include_documents else ""))
        return {"ok": not bool(result.get("error")), **result}
    if action == "collection_documents":
        requested_collection = collection_id or document_id
        if not requested_collection:
            return {"ok": False, "error": "reference_collection_id_required"}
        result = _request("/api/collections/" + quote(requested_collection, safe="") + "/documents")
        return {"ok": not bool(result.get("error")), **result}
    if action == "get":
        if not document_id:
            return {"ok": False, "error": "reference_document_id_required"}
        result = _request("/api/documents/" + quote(document_id, safe=""))
        return {"ok": not bool(result.get("error")), "document": result}
    if action == "search":
        if not query.strip():
            return {"ok": False, "error": "reference_query_required"}
        return _search(query, topic=topic, limit=limit, include_body=include_body,
                       max_query_tokens=max_query_tokens)
    if action == "lookup":
        requested = entities or ([{"kind": entity_kind, "key": entity_key}]
                                 if entity_kind and entity_key else [])
        if not requested:
            return {"ok": False, "error": "reference_entity_required"}
        found = []
        for item in requested[:30]:
            kind = str(item.get("kind", "")); key = str(item.get("key", ""))
            result = _search(f"{kind} {key}", limit=2, include_body=include_body)
            found.append({"kind": kind, "key": key, "matches": result.get("results", []),
                          "evidence": result.get("evidence", [])})
        return {"ok": True, "ruleset_id": ruleset_id, "entities": found, "semantic_lookup": True}
    if action == "related":
        if not entity_kind or not entity_key:
            return {"ok": False, "error": "reference_entity_required"}
        result = _search(f"{entity_kind} {entity_key} prerequisites effects unlocks related",
                         limit=limit, include_body=include_body)
        return {"ok": result.get("ok", False), "entity": {"kind": entity_kind, "key": entity_key},
                "related": result.get("results", []), "evidence": result.get("evidence", [])}
    return {"ok": False, "error": "invalid_reference_action"}
