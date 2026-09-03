#!/usr/bin/env python3
"""Mature-campaign notebook reads remain bounded and explicitly paged."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from smacx_journal import CampaignJournal
from smacx_store import MemoryScope, SmacxStore


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-notebook-scale-") as raw:
        root = Path(raw)
        store = SmacxStore(root / "state.sqlite3")
        store.ensure_agent("agent-notes", "Notes")
        store.create_match(match_id="match-notes", display_name="Notes", mode="solo")
        store.create_perspective("match-notes", "agent-notes",
                                 perspective_id="perspective-notes")
        scope = MemoryScope("match-notes", "agent-notes", "perspective-notes")
        journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
        for index in range(240):
            journal.notebook(
                scope, "put", collection="notes", key=f"note-{index:03d}",
                title=f"Frontier note {index:03d}",
                content=(f"marker-{index:03d} " + "large durable note body " * 1000)[:23_900],
                tags=["frontier", f"group-{index % 8}"], turn=index,
            )
        first = journal.notebook(scope, "list", collection="notes", limit=50)
        encoded = json.dumps(first, separators=(",", ":"), ensure_ascii=False)
        assert first["total_count"] == 240 and len(first["items"]) <= 50
        assert first["next_cursor"] and len(encoded.encode()) <= 2048 * 4
        assert all("content" not in item and len(item.get("abstract", "")) <= 240
                   for item in first["items"])
        searched = journal.notebook(
            scope, "list", collection="notes", query="marker-117", limit=24,
        )
        assert searched["total_count"] == 1 and searched["items"][0]["key"] == "note-117"
        full = journal.notebook(scope, "get", collection="notes", key="note-117")
        assert "marker-117" in full["item"]["content"]
        assert len(json.dumps(full, separators=(",", ":")).encode()) <= 8192 * 4
    print(json.dumps({"event": "pass", "payload": {
        "large_note_count": 240, "metadata_only_list": True,
        "bounded_pagination": True, "targeted_full_get": True,
        "bounded_search": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
