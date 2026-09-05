"""Client for the locally built SemanticKnowledge mechanics encyclopedia."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


REFERENCE_URL = os.environ.get("SMACX_REFERENCE_URL", "http://knowledge-service:8090").rstrip("/")


def _token_estimate(value: Any) -> int:
    return max(1, (len(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":")).encode("utf-8")) + 3) // 4)


def _body_page(document: dict[str, Any], continuation: str,
               max_content_tokens: int) -> dict[str, Any]:
    """Bound one direct document read without losing resumability."""
    match = re.fullmatch(r"body-(\d+)", continuation or "body-0")
    if not match:
        return {"ok": False, "error": "invalid_reference_continuation"}
    start = int(match.group(1))
    body = str(document.get("body") or "")
    if start > len(body):
        return {"ok": False, "error": "invalid_reference_continuation"}
    ceiling = min(max(int(max_content_tokens), 256), 8192)
    # Reserve space for title/provenance and then tighten against the complete
    # returned envelope. Four UTF-8 bytes/token is the repository's conservative
    # content-free proxy; provider-specific exact gates run separately.
    end = min(len(body), start + max(256, (ceiling - 192) * 4))
    bounded = dict(document)
    bounded["body"] = body[start:end]
    bounded["body_offset"] = start
    bounded["body_complete"] = end >= len(body)
    result = {
        "ok": True, "document": bounded, "bounded": True,
        "continuation": None if end >= len(body) else f"body-{end}",
    }
    while _token_estimate(result) > ceiling and end > start:
        end = start + max(0, (end - start) * 3 // 4)
        bounded["body"] = body[start:end]
        bounded["body_complete"] = end >= len(body)
        result["continuation"] = None if end >= len(body) else f"body-{end}"
    result["result_token_estimate"] = _token_estimate(result)
    return result


def _collection_page(result: dict[str, Any], continuation: str, limit: int,
                     max_content_tokens: int) -> dict[str, Any]:
    """Bound collection/tree enumeration returned by different service versions."""
    match = re.fullmatch(r"cursor-(\d+)", continuation or "cursor-0")
    if not match:
        return {"ok": False, "error": "invalid_reference_continuation"}
    start = int(match.group(1))
    key = next((name for name in ("documents", "collections", "topics", "items")
                if isinstance(result.get(name), list)), None)
    if key is None:
        # A service response with no enumerable body is already bounded.
        return {"ok": True, **result, "bounded": True, "continuation": None,
                "result_token_estimate": _token_estimate(result)}
    source = list(result[key])
    page_limit = min(max(int(limit), 1), 30)
    page = []
    for raw in source[start:start + page_limit]:
        if not isinstance(raw, dict):
            page.append(raw)
            continue
        # Enumeration is navigation metadata, never an implicit document read.
        # Older knowledge-service versions may accidentally include bodies.
        item = {name: value for name, value in raw.items()
                if name not in {"body", "content", "text", "values"}}
        if "description" in item:
            item["description"] = str(item["description"] or "")[:480]
        page.append(item)
    ceiling = min(max(int(max_content_tokens), 256), 8192)
    bounded = {name: value for name, value in result.items() if name != key}
    bounded.update({"ok": True, key: page, "bounded": True})
    next_cursor = start + len(page)
    bounded["continuation"] = None if next_cursor >= len(source) else f"cursor-{next_cursor}"
    while page and _token_estimate(bounded) > ceiling:
        page.pop()
        next_cursor = start + len(page)
        bounded["continuation"] = f"cursor-{next_cursor}"
    if not page and start < len(source):
        return {"ok": False, "error": "reference_item_exceeds_budget",
                "continuation": f"cursor-{start + 1}", "bounded": True}
    bounded["result_token_estimate"] = _token_estimate(bounded)
    return bounded


def freeze_reference_corpus(revision: str, root: Path) -> dict[str, Any]:
    """Materialize one immutable, content-verified mechanics-corpus revision."""
    if not revision or len(revision) > 256:
        raise RuntimeError("invalid_reference_corpus_revision")
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    request = Request(
        REFERENCE_URL + "/api/export/" + quote(revision, safe=""),
        headers={"Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=60.0) as response:
            raw = response.read(64_000_001)
    except HTTPError as exc:
        if exc.code == 409:
            raise RuntimeError("reference_corpus_revision_changed") from exc
        raise RuntimeError("reference_corpus_snapshot_unavailable") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("reference_corpus_snapshot_unavailable") from exc
    if len(raw) > 64_000_000:
        raise RuntimeError("reference_corpus_snapshot_too_large")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("reference_corpus_snapshot_invalid") from exc
    if not isinstance(value, dict) or str(value.get("revision") or "") != revision \
            or not isinstance(value.get("documents"), list) \
            or not isinstance(value.get("collections"), list):
        raise RuntimeError("reference_corpus_snapshot_invalid")
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    target = root / f"{digest}.json"
    if not target.exists():
        descriptor, temporary_name = tempfile.mkstemp(prefix=".reference-", dir=root)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(canonical)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, target)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    return {"revision": revision, "content_path": str(target),
            "content_sha256": digest, "documents": len(value["documents"])}


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
            max_query_tokens: int = 1_024, max_content_tokens: int | None = None) -> dict[str, Any]:
    result = _request("/api/search", {
        "query": query,
        "topic": topic or None,
        "top": min(max(int(limit), 1), 30),
        "maxContentTokens": min(max(int(max_content_tokens or (
            16_000 if include_body else 4_000)), 256), 8_192),
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
                   max_content_tokens: int | None = None,
                   continuation: str = "",
                   private_prefix: str | None = None, entity_kind: str = "",
                   entity_key: str = "", entities: list[dict[str, str]] | None = None,
                   ruleset_id: str = "smacx") -> dict[str, Any]:
    _ = private_prefix
    if action == "status":
        result = _request("/api/status", timeout=5.0)
        return {"ok": not bool(result.get("error")), **result}
    if action == "audit":
        result = _request("/api/audit", timeout=15.0)
        return {"ok": not bool(result.get("error")), **result}
    if action == "topics":
        result = _request("/api/topics")
        return {"ok": not bool(result.get("error")), **result}
    if action == "tree":
        result = _request("/api/tree" + ("?includeDocuments=true" if include_documents else ""))
        if result.get("error"):
            return {"ok": False, **result}
        return _collection_page(result, continuation, limit, max_content_tokens or 2048)
    if action == "collection_documents":
        requested_collection = collection_id or document_id
        if not requested_collection:
            return {"ok": False, "error": "reference_collection_id_required"}
        result = _request("/api/collections/" + quote(requested_collection, safe="") + "/documents")
        if result.get("error"):
            return {"ok": False, **result}
        return _collection_page(result, continuation, limit, max_content_tokens or 2048)
    if action == "get":
        if not document_id:
            return {"ok": False, "error": "reference_document_id_required"}
        result = _request("/api/documents/" + quote(document_id, safe=""))
        if result.get("error"):
            return {"ok": False, **result}
        return _body_page(result, continuation, max_content_tokens or 2048)
    if action == "search":
        if not query.strip():
            return {"ok": False, "error": "reference_query_required"}
        return _search(query, topic=topic, limit=limit, include_body=include_body,
                       max_query_tokens=max_query_tokens,
                       max_content_tokens=max_content_tokens)
    if action == "lookup":
        requested = entities or ([{"kind": entity_kind, "key": entity_key}]
                                 if entity_kind and entity_key else [])
        if not requested:
            return {"ok": False, "error": "reference_entity_required"}
        found = []
        for item in requested[:30]:
            kind = str(item.get("kind", "")); key = str(item.get("key", ""))
            result = _search(f"{kind} {key}", limit=2, include_body=include_body,
                             max_content_tokens=max_content_tokens)
            found.append({"kind": kind, "key": key, "matches": result.get("results", []),
                          "evidence": result.get("evidence", [])})
        return {"ok": True, "ruleset_id": ruleset_id, "entities": found, "semantic_lookup": True}
    if action == "related":
        if not entity_kind or not entity_key:
            return {"ok": False, "error": "reference_entity_required"}
        result = _search(f"{entity_kind} {entity_key} prerequisites effects unlocks related",
                         limit=limit, include_body=include_body,
                         max_content_tokens=max_content_tokens)
        return {"ok": result.get("ok", False), "entity": {"kind": entity_kind, "key": entity_key},
                "related": result.get("results", []), "evidence": result.get("evidence", [])}
    return {"ok": False, "error": "invalid_reference_action"}
