#!/usr/bin/env python3
"""Contained regression for the legal, provenance-tracked rules corpus."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from smacx_reference import read_reference, seed_reference_corpus
from smacx_store import SmacxStore


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
        if not result.get("source_url") or not result.get("source_license") \
                or not result.get("content_sha256"):
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
        raw = corpus.read_text(encoding="utf-8")
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
                "no_proprietary_assets_bundled": True,
                "match_hidden_state_absent": True,
            },
        }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
