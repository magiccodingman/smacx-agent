"""Legally provenance-tracked, locally searchable game-reference corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from smacx_store import SmacxStore


DEFAULT_CORPUS = Path(__file__).resolve().parents[1] / "knowledge" / "core.json"
DEFAULT_SOURCES = Path(__file__).resolve().parents[1] / "knowledge" / "sources.json"


def seed_reference_corpus(store: SmacxStore, path: Path | str = DEFAULT_CORPUS) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file() or source.is_symlink():
        return {"ok": False, "error": "reference_corpus_unavailable", "path": str(source)}
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != "smacx.reference-corpus.v1":
        raise ValueError("invalid_reference_corpus")
    documents = payload.get("documents")
    if not isinstance(documents, list) or len(documents) > 10_000:
        raise ValueError("invalid_reference_documents")
    source_manifest_path = source.parent / "sources.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if source_manifest.get("schema") != "smacx.reference-sources.v1" \
            or source_manifest.get("policy") != "citation_metadata_only_no_remote_content":
        raise ValueError("invalid_reference_source_manifest")
    citation_sources = source_manifest.get("sources")
    if not isinstance(citation_sources, list):
        raise ValueError("invalid_reference_source_manifest")
    citations: dict[str, dict[str, str]] = {}
    for citation in citation_sources:
        if not isinstance(citation, dict):
            raise ValueError("invalid_reference_source")
        canonical_url = str(citation.get("canonical_url", ""))
        archive_url = str(citation.get("archive_url", ""))
        timestamp = str(citation.get("archive_timestamp", ""))
        if not canonical_url.startswith("https://") \
                or not archive_url.startswith(f"https://web.archive.org/web/{timestamp}id_/") \
                or not str(citation.get("archive_digest", "")):
            raise ValueError("invalid_reference_source")
        citations[canonical_url] = {str(key): str(value) for key, value in citation.items()}
    stored = 0
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("invalid_reference_document")
        source_url = str(document["source_url"]) if document.get("source_url") else None
        citation = citations.get(source_url or "", {})
        document_metadata = dict(document.get("metadata", {}))
        if citation:
            document_metadata["citation_archive"] = {
                "verified_at": str(source_manifest.get("verified_at", "")),
                "verified_via": str(source_manifest.get("verified_via", "")),
                "digest": citation["archive_digest"],
            }
        store.upsert_reference_document(
            str(document.get("document_id", "")), topic=str(document.get("topic", "")),
            title=str(document.get("title", "")), summary=str(document.get("summary", "")),
            body=str(document.get("body", "")), tags=document.get("tags", ()),
            source_url=source_url,
            archive_url=(citation.get("archive_url") or None),
            archive_timestamp=(citation.get("archive_timestamp") or None),
            source_title=(str(document["source_title"])
                          if document.get("source_title") else None),
            source_license=str(document.get("source_license", "")),
            provenance=str(document.get("provenance", "")),
            entity_kind=(str(document["entity_kind"])
                         if document.get("entity_kind") else None),
            entity_key=(str(document["entity_key"])
                        if document.get("entity_key") else None),
            ruleset_id=str(document.get("ruleset_id", "smacx")),
            source_priority=int(document.get("source_priority", 0)),
            metadata=document_metadata,
        )
        stored += 1
    return {"ok": True, "stored": stored, "path": str(source),
            "topics": store.list_reference_topics()}


def read_reference(store: SmacxStore, action: str, *, query: str = "",
                   topic: str = "", document_id: str = "", limit: int = 8,
                   include_body: bool = False,
                   private_prefix: str | None = None,
                   entity_kind: str = "", entity_key: str = "",
                   entities: list[dict[str, str]] | None = None,
                   ruleset_id: str = "smacx") -> dict[str, Any]:
    if action == "topics":
        return {"ok": True, "topics": store.list_reference_topics(
            private_prefix=private_prefix,
        )}
    if action == "get":
        if not document_id:
            return {"ok": False, "error": "reference_document_id_required"}
        return {"ok": True, "document": store.get_reference_document(
            document_id, private_prefix=private_prefix,
        )}
    if action == "search":
        if not query.strip():
            return {"ok": False, "error": "reference_query_required"}
        return {"ok": True, "query": query, "topic": topic or None,
                "results": store.search_reference(
                    query, topic=topic or None, limit=limit, include_body=include_body,
                    private_prefix=private_prefix,
                )}
    if action == "lookup":
        requested = entities or ([{"kind": entity_kind, "key": entity_key}]
                                 if entity_kind and entity_key else [])
        pairs = [(str(item.get("kind", "")), str(item.get("key", "")))
                 for item in requested if isinstance(item, dict)]
        if not pairs:
            return {"ok": False, "error": "reference_entity_required"}
        return {
            "ok": True, "ruleset_id": ruleset_id,
            "entities": store.lookup_reference_entities(
                pairs, ruleset_id=ruleset_id, private_prefix=private_prefix,
                include_body=include_body,
            ),
            "requested": [{"kind": kind, "key": key} for kind, key in pairs],
        }
    if action == "related":
        if not entity_kind or not entity_key:
            return {"ok": False, "error": "reference_entity_required"}
        root = store.lookup_reference_entities(
            [(entity_kind, entity_key)], ruleset_id=ruleset_id,
            private_prefix=private_prefix, include_body=include_body,
        )
        if not root:
            return {"ok": True, "entity": None, "related": []}
        relations = root[0].get("metadata", {}).get("related", [])
        pairs = [
            (str(item.get("kind", "")), str(item.get("key", "")))
            for item in relations[:30] if isinstance(item, dict)
        ]
        return {
            "ok": True, "entity": root[0],
            "related": (store.lookup_reference_entities(
                pairs, ruleset_id=ruleset_id, private_prefix=private_prefix,
                include_body=include_body,
            ) if pairs else []),
        }
    return {"ok": False, "error": "invalid_reference_action"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--query")
    arguments = parser.parse_args()
    store = SmacxStore(arguments.database)
    result = seed_reference_corpus(store, arguments.corpus)
    if arguments.query:
        result["search"] = store.search_reference(arguments.query)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
