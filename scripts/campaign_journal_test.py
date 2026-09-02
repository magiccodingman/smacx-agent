#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from smacx_journal import CampaignJournal
from smacx_store import MemoryScope


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-journal-") as temporary:
        scope = MemoryScope("match-journal-test", "agent-journal-test", "perspective-journal-test")
        journal = CampaignJournal(Path(temporary))
        opening = journal.append(scope, "memory.goal", {
            "record_input": {"goal_key": "expand", "title": "Expand"},
            "record": {"goal_key": "expand", "goal_revision": 1,
                       "title": "Expand", "status": "active"},
        }, session_id="session-journal-test", turn=1, year=2101)
        journal.notebook(
            scope, "put", collection="suspicions", key="gaia-border",
            title="Gaian border posture", content="Watch the named western frontier.",
            session_id="session-journal-test", turn=1, year=2101,
        )
        final = journal.append(
            scope, "game.action", {"selected_action": "end_turn"},
            session_id="session-journal-test", turn=2, year=2102,
            commit_reason="Complete turn 2",
        )
        verified = journal.verify(scope)
        if not verified["ok"] or verified["events"] != 3:
            raise AssertionError(verified)
        replay = journal.replay(scope)
        if replay["goals"]["expand"]["record"]["status"] != "active":
            raise AssertionError("goal did not replay")
        if replay["notebook"]["suspicions"]["gaia-border"]["revision"] != 1:
            raise AssertionError("notebook did not replay")
        if replay["manifest"]["head_hash"] != final["event_hash"]:
            raise AssertionError("manifest head mismatch")
        working = journal.working_state(scope, token_budgets={"goals": 1000})
        if working["sections"]["goals"][0]["goal_key"] != "expand" \
                or working["journal_head_hash"] != final["event_hash"]:
            raise AssertionError("journal-backed working state did not materialize")
        bounded = journal.working_state(scope, token_budgets={"goals": 1})
        if not bounded["projection_truncated"] \
                or "goals" not in bounded["compaction_required_sections"] \
                or bounded["source_token_estimates"]["goals"] <= 1:
            raise AssertionError("working-state budget pressure was not surfaced")
        projection = journal.rebuild_sqlite_projection(
            scope, Path(temporary) / "disposable-query-cache.sqlite3",
        )
        if not projection["ok"] or projection["records"] != 2:
            raise AssertionError(f"projection did not rebuild: {projection}")
        repository = Path(temporary) / scope.match_id
        if not (repository / ".git").is_dir():
            raise AssertionError("turn-boundary Git audit was not created")
        branch = journal.fork_timeline(
            scope, "timeline-rewind-test",
            native_save_sha256="a" * 64,
            from_event_hash=opening["event_hash"],
        )
        branch_state = journal.replay(scope, "timeline-rewind-test")
        if branch_state["goals"]["expand"]["record"]["status"] != "active" \
                or branch_state["notebook"].get("suspicions") \
                or branch.get("forked_from_event_hash") != opening["event_hash"]:
            raise AssertionError("timeline parent-prefix replay is incorrect")
        print(json.dumps({
            "event": "pass", "payload": {
                "hash_chain": True, "portable_replay": True,
                "notebook_versioned": True, "turn_git_commit": True,
                "sqlite_projection_rebuilt_from_journal": True,
                "working_state_journal_backed": True,
                "working_state_budget_pressure_visible": True,
                "timeline_parent_prefix_replayed": True,
            },
        }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
