#!/usr/bin/env python3
"""Bounded direct-reference document and collection regression."""

from __future__ import annotations

import json

import smacx_reference


def main() -> int:
    original = smacx_reference._request
    documents = [
        {"document_id": f"doc-{index}", "title": f"Rule {index}",
         "description": "x" * 300, "body": "y" * 12_000}
        for index in range(40)
    ]

    def fixture(path: str, *_args: object, **_kwargs: object) -> dict:
        if path.startswith("/api/documents/"):
            return dict(documents[0])
        if path.startswith("/api/collections/"):
            return {"documents": documents}
        if path.startswith("/api/tree"):
            return {"collections": documents}
        raise AssertionError(path)

    smacx_reference._request = fixture
    try:
        first = smacx_reference.read_reference(
            None, "get", document_id="doc-0", max_content_tokens=512,
        )
        assert first["ok"] and first["continuation"].startswith("body-")
        assert first["result_token_estimate"] <= 512
        second = smacx_reference.read_reference(
            None, "get", document_id="doc-0", max_content_tokens=512,
            continuation=first["continuation"],
        )
        assert second["ok"]
        assert second["document"]["body_offset"] > first["document"]["body_offset"]
        assert second["result_token_estimate"] <= 512

        page = smacx_reference.read_reference(
            None, "collection_documents", collection_id="rules", limit=30,
            max_content_tokens=512,
        )
        assert page["ok"] and page["continuation"] not in {None, "cursor-0"}
        assert page["result_token_estimate"] <= 512
        next_page = smacx_reference.read_reference(
            None, "collection_documents", collection_id="rules", limit=30,
            max_content_tokens=512, continuation=page["continuation"],
        )
        assert next_page["ok"]
        assert next_page["documents"][0]["document_id"] \
            != page["documents"][0]["document_id"]
    finally:
        smacx_reference._request = original

    print(json.dumps({"event": "pass", "payload": {
        "giant_document_chunked": True,
        "collection_enumeration_bounded": True,
        "continuations_advance": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
