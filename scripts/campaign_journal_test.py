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
        journal.append(scope, "chat.message", {
            "message_uid": "native:session-journal-test:1",
            "direction": "inbound", "channel": "global",
            "content": "The western frontier is peaceful.",
        }, session_id="session-journal-test", turn=1, year=2101)
        final = journal.append(
            scope, "game.action", {"selected_action": "end_turn"},
            session_id="session-journal-test", turn=2, year=2102,
            commit_reason="Complete turn 2",
        )
        verified = journal.verify(scope)
        if not verified["ok"] or verified["events"] != 4:
            raise AssertionError(verified)
        replay = journal.replay(scope)
        selected = journal.replay(scope, sections=("goals", "manifest"))
        assert selected == {key: replay[key] for key in ("goals", "manifest")}
        selected["goals"].clear()
        assert journal.replay(scope, sections=("goals",))["goals"], "narrow replay leaked mutable authority"
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
        if not projection["ok"] or projection["records"] != 3:
            raise AssertionError(f"projection did not rebuild: {projection}")
        search = journal.search(scope, "western frontier")
        if not search or search[0]["authority"] != "campaign_journal":
            raise AssertionError("active-timeline search did not find journal memory")
        chat = journal.chat_messages(scope, unread_only=True, acknowledge=True)
        if len(chat) != 1 or journal.chat_messages(scope, unread_only=True):
            raise AssertionError("journal-backed chat acknowledgement failed")
        repository = Path(temporary) / scope.match_id
        if not (repository / ".git").is_dir():
            raise AssertionError("turn-boundary Git audit was not created")
        branch = journal.fork_timeline(
            scope, "timeline-recovery-test",
            native_save_sha256="a" * 64,
            from_event_hash=opening["event_hash"],
        )
        branch_state = journal.replay(scope, "timeline-recovery-test")
        if branch_state["goals"]["expand"]["record"]["status"] != "active" \
                or branch_state["notebook"].get("suspicions") \
                or branch.get("forked_from_event_hash") != opening["event_hash"]:
            raise AssertionError("timeline parent-prefix replay is incorrect")
        restored = CampaignJournal(
            Path(temporary), timeline_resolver=lambda _scope: "timeline-recovery-test",
        )
        if restored.search(scope, "western frontier"):
            raise AssertionError("post-checkpoint search memory leaked into restored timeline")

        # Exercise the real journal projection rather than a handcrafted
        # runtime fixture: newer dead records must not evict old still-live
        # cognition before provider section budgeting, and plan_key is the
        # canonical identity across repeated revisions.
        pressure_scope = MemoryScope(
            "match-journal-pressure", "agent-journal-pressure",
            "perspective-journal-pressure",
        )
        journal.append(pressure_scope, "memory.commitment", {
            "record": {"commitment_key": "old-binding", "title": "Defend our ally",
                       "terms": "Hold the rendezvous", "status": "accepted",
                       "created_unix": 1},
        })
        journal.append(pressure_scope, "memory.goal", {
            "record": {"goal_key": "live-goal", "title": "Hold the peninsula",
                       "status": "active", "priority": 100, "created_unix": 1},
        })
        journal.append(pressure_scope, "memory.plan", {
            "record": {"plan_key": "reserve-plan", "title": "First revision",
                       "objective": "Hold one reserve", "status": "active",
                       "created_unix": 1},
        })
        for index in range(125):
            journal.append(pressure_scope, "memory.commitment", {
                "record": {"commitment_key": f"resolved-{index}", "title": "Resolved",
                           "terms": "historical", "status": "fulfilled",
                           "created_unix": 1000 + index},
            })
            journal.append(pressure_scope, "memory.goal", {
                "record": {"goal_key": f"dead-{index}", "title": "Dead",
                           "status": "completed", "priority": 100,
                           "created_unix": 1000 + index},
            })
        journal.append(pressure_scope, "memory.plan", {
            "record": {"plan_key": "reserve-plan", "title": "Current revision",
                       "objective": "Hold two reserves", "status": "active",
                       "created_unix": 9999},
        })
        pressured = journal.working_state(
            pressure_scope,
            token_budgets={"commitments": 250, "goals": 250, "plans": 250},
        )["sections"]
        assert [item["commitment_key"] for item in pressured["commitments"]] == ["old-binding"]
        assert [item["goal_key"] for item in pressured["goals"]] == ["live-goal"]
        assert len(pressured["plans"]) == 1
        assert pressured["plans"][0]["title"] == "Current revision"
        journal.append(pressure_scope, "memory.plan", {"record": {
            "plan_id": "plan-completed-newer", "plan_key": "completed-newer", "title": "Resolved",
            "status": "completed", "created_unix": 10000}})
        assert journal.projection_records(pressure_scope, "plans", limit=1, statuses={"active"})[0]["plan_key"] == "reserve-plan"
        assert journal.projection_records(pressure_scope, "plans", limit=1,
                                          record_ids={"plan-completed-newer"})[0]["status"] == "completed"
        assert journal.search(pressure_scope, "historical", document_kinds=("commitment",))
        print(json.dumps({
            "event": "pass", "payload": {
                "hash_chain": True, "portable_replay": True,
                "notebook_versioned": True, "turn_git_commit": True,
                "sqlite_projection_rebuilt_from_journal": True,
                "working_state_journal_backed": True,
                "working_state_budget_pressure_visible": True,
                "timeline_parent_prefix_replayed": True,
                "search_and_chat_journal_authoritative": True,
                "post_checkpoint_search_memory_excluded": True,
                "live_filter_before_real_budget": True,
                "canonical_plan_key_projection": True,
                "dead_history_searchable": True,
            },
        }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
