"""Legally provenance-tracked, locally searchable game-reference corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from smacx_store import SmacxStore


DEFAULT_CORPUS = Path(__file__).resolve().parents[1] / "knowledge" / "core.json"


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
    stored = 0
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("invalid_reference_document")
        store.upsert_reference_document(
            str(document.get("document_id", "")), topic=str(document.get("topic", "")),
            title=str(document.get("title", "")), summary=str(document.get("summary", "")),
            body=str(document.get("body", "")), tags=document.get("tags", ()),
            source_url=(str(document["source_url"]) if document.get("source_url") else None),
            source_title=(str(document["source_title"])
                          if document.get("source_title") else None),
            source_license=str(document.get("source_license", "")),
            provenance=str(document.get("provenance", "")),
        )
        stored += 1
    return {"ok": True, "stored": stored, "path": str(source),
            "topics": store.list_reference_topics()}


def read_reference(store: SmacxStore, action: str, *, query: str = "",
                   topic: str = "", document_id: str = "", limit: int = 8,
                   include_body: bool = False,
                   private_prefix: str | None = None) -> dict[str, Any]:
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
