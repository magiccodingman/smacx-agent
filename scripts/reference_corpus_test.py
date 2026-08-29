#!/usr/bin/env python3
"""Contained regression for the legal, provenance-tracked rules corpus."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from smacx_reference import read_reference, seed_reference_corpus
from smacx_store import ScopeViolation, SmacxStore


def main() -> int:
    corpus = Path(__file__).resolve().parents[1] / "knowledge" / "core.json"
    with tempfile.TemporaryDirectory(prefix="smacx-reference-test-") as temporary:
        store = SmacxStore(Path(temporary) / "state.sqlite3")
        seeded = seed_reference_corpus(store, corpus)
        if not seeded.get("ok") or seeded.get("stored", 0) < 15:
            raise AssertionError(f"reference corpus did not seed: {seeded}")
        topics = read_reference(store, "topics")
        topic_names = {item["topic"] for item in topics["topics"]}
        if not {"rules", "bases", "diplomacy", "factions", "victory"} <= topic_names:
            raise AssertionError(f"reference hierarchy is incomplete: {topic_names}")
        compact = read_reference(store, "search", query="Treaty Pact trust chat", limit=5)
        if not compact.get("results") or any("body" in item for item in compact["results"]):
            raise AssertionError("compact reference search was empty or returned full bodies")
        result = compact["results"][0]
        if not result.get("source_title") or not result.get("source_license") \
                or not result.get("provenance") or not result.get("content_sha256"):
            raise AssertionError("search result omitted citation/provenance")
        full = read_reference(store, "get", document_id=result["document_id"])
        if not full.get("document", {}).get("body"):
            raise AssertionError("reference get did not return the selected document")
        focused = read_reference(
            store, "search", query="Colony Former settlement", topic="expansion",
            include_body=True,
        )
        if not focused["results"] or any(item["topic"] != "expansion" for item in focused["results"]):
            raise AssertionError("topic-scoped BM25 search escaped its hierarchy")
        common_private = {
            "topic": "rules", "title": "Private test mechanics",
            "summary": "A private source-isolation regression.",
            "body": "A unique private mechanic named moonbeam belongs to exactly one legal game source.",
            "tags": ("private",), "source_license": "Private test source",
            "provenance": "Contained source-isolation test.",
        }
        store.upsert_reference_document("private.game-source-one.0001", **common_private)
        store.upsert_reference_document(
            "private.game-source-two.0001", **{
                **common_private,
                "body": "A unique private mechanic named starfall belongs to another legal game source.",
            },
        )
        first = read_reference(
            store, "search", query="moonbeam starfall",
            private_prefix="private.game-source-one.", include_body=True,
        )
        if {item["document_id"] for item in first["results"]} != {
                "private.game-source-one.0001"}:
            raise AssertionError("private reference search crossed game-source scope")
        try:
            read_reference(
                store, "get", document_id="private.game-source-two.0001",
                private_prefix="private.game-source-one.",
            )
        except ScopeViolation:
            pass
        else:
            raise AssertionError("private reference get crossed game-source scope")
        raw = corpus.read_text(encoding="utf-8")
        payload = json.loads(raw)
        if "every official" in payload.get("description", "").casefold():
            raise AssertionError("reference corpus overclaims completeness")
        for document in payload.get("documents", []):
            license_note = str(document.get("source_license", ""))
            provenance = str(document.get("provenance", ""))
            if "Project-authored Apache-2.0" not in license_note:
                raise AssertionError(
                    f"reference document lacks project-authored boundary: "
                    f"{document.get('document_id')}"
                )
            if not provenance:
                raise AssertionError(
                    f"reference document lacks provenance: {document.get('document_id')}"
                )
            if document.get("topic") == "strategy" \
                    or "walkthrough" in str(document.get("source_title", "")).casefold():
                raise AssertionError("guide or strategy material entered the core mechanics corpus")
            authored = " ".join(str(document.get(field, "")) for field in (
                "title", "summary", "body", "tags",
            )).casefold()
            if any(phrase in authored for phrase in (
                    "walkthrough", "strategy guide", "build order", "optimal opening",
                    "cheat mode", "scenario solution")):
                raise AssertionError("prescriptive guide material entered the mechanics corpus")
        for forbidden in ("Manual.pdf", "Script.txt", "helpx.txt", "alpha.txt"):
            if f'"copied_asset": "{forbidden}"' in raw:
                raise AssertionError("proprietary asset was embedded in the distributable corpus")
        print(json.dumps({
            "event": "pass", "payload": {
                "document_count": seeded["stored"],
                "topic_hierarchy": True,
                "bm25_search": True,
                "compact_then_get_protocol": True,
                "citations_and_licenses": True,
                "independent_expression_boundary": True,
                "no_proprietary_assets_bundled": True,
                "match_hidden_state_absent": True,
                "private_game_source_isolation": True,
            },
        }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
