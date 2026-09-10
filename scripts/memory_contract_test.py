#!/usr/bin/env python3
"""Typed contracts through managed tools, canonical replacement and failure stages."""
import asyncio
import copy
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import smacx_controller as c
import smacx_mcp as m
from smacx_store import SmacxStore, MemoryScope
from smacx_journal import CampaignJournal, JournalError
from smacx_memory_contract import SCHEMAS, KEYS, contract, validate_record

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    store = SmacxStore(root / "state.sqlite3")
    store.ensure_agent("agent-contract", "Contract")
    store.create_match(match_id="match-contract", display_name="Contract", mode="solo")
    store.create_perspective("match-contract", "agent-contract", perspective_id="perspective-contract")
    scope = MemoryScope("match-contract", "agent-contract", "perspective-contract")
    store.register_instance(instance_id="instance-contract", scope=scope)
    store.start_session(scope, "instance-contract", session_id="session-contract")
    actor = store.upsert_actor(scope.match_id, "known-contact", "Known contact")["actor_id"]
    journal = CampaignJournal(root / "campaigns")
    snapshot = {"turn": 4, "year": 2104, "revision": "r4", "match_id": scope.match_id,
                "session_id": "session-contract", "ready_unit_refs": []}
    with patch.object(c, "_store", return_value=store), patch.object(c, "_journal", return_value=journal), \
         patch.object(c, "_scope_for_match", return_value=scope), \
         patch.object(c, "bridge_request", return_value={"ok": True, "snapshot": snapshot}), \
         patch.object(m, "_bound_scope_identity", return_value=(scope.match_id, "session-contract", scope.agent_id, scope.perspective_id)):
        def write(kind, record, revision="r4"):
            return m.smac_memory_update(kind, scope.match_id, "session-contract", revision, json.dumps(record))

        def read(kind, key):
            return m.smac_memory("editable_record", scope.match_id, record_kind=kind, key=key)

        seeds = {
            "claim": {"topic": "contact", "content": "Unverified report", "confidence": .3},
            "belief": {"topic": "contact", "content": "Conditional interpretation", "confidence": .4},
            "relationship": {"actor_id": actor, "trust": 35, "reasons": ["Observed agreement"]},
            "commitment": {"commitment_key": "agreement", "title": "Agreement", "terms": "Keep border clear",
                           "parties": [{"actor_id": actor, "role": "counterparty"}]},
            "goal": {"title": "Explore", "description": "Find viable land", "trigger": {"intent_horizon": "persistent_goal"}},
            "plan": {"plan_key": "explore", "title": "Explore", "objective": "Learn the terrain",
                     "participants": [{"ref": "own-unit-3", "intended_role": "scout"}],
                     "timing": {"intent_horizon": "this_turn_preferred"}},
            "summary": {"section": "situation", "content": "Contact is uncertain."},
        }
        for kind, seed in seeds.items():
            saved = write(kind, seed)
            assert saved["ok"], saved
            key = saved["record"][KEYS[kind]]
            if KEYS[kind] in seed:
                duplicate = write(kind, seed)
                assert duplicate["ok"] and duplicate["changed"] is False, duplicate
                assert duplicate["journal_event_id"] == saved["journal_event_id"]
            fetched = read(kind, key)
            assert fetched["ok"] and fetched["authority"] == "campaign_journal", fetched
            editable = json.loads(fetched["record_json"])
            assert not validate_record(kind, editable, 4), (kind, editable)
            before = copy.deepcopy(editable)
            field = "status" if kind in {"plan", "goal", "commitment"} else ("trust" if kind == "relationship" else "content")
            editable[field] = "completed" if kind in {"plan", "goal"} else "fulfilled" if kind == "commitment" else 36 if kind == "relationship" else "Still conditional."
            revised = write(kind, editable)
            assert revised["ok"], revised
            after = json.loads(read(kind, key)["record_json"])
            assert {k:v for k,v in after.items() if k != field} == {k:v for k,v in before.items() if k != field}, (kind, before, after)
            assert m.smac_memory("contract", scope.match_id, record_kind=kind)["contract"] == contract(kind)

        before = journal.replay(scope)
        malformed = {"plan_key": "bad", "participants": ["own-unit-3", {"ref": "own-unit-4", "exclusive": "yes"}],
                     "reconciliation": {"turn": 4}, "timing": {"intent_horizon": "forever"}}
        rejected = write("plan", malformed)
        paths = {item["field"] for item in rejected["validation"]["errors"]}
        assert {"record_json.title", "record_json.objective", "record_json.participants[0]",
                "record_json.participants[1].exclusive", "record_json.reconciliation", "record_json.timing.intent_horizon"} <= paths, rejected
        assert rejected["persistence"]["journal_committed"] is False
        assert journal.replay(scope) == before
        for kind, seed in seeds.items():
            bad = write(kind, {**seed, "unknown_field": "must not vanish"})
            assert not bad["ok"] and bad["persistence"]["stage"] == "not_started", bad
        for value in ("35", 35.1, True):
            assert not write("relationship", {"actor_id": actor, "trust": value})["ok"]
        assert not write("belief", {**seeds["belief"], "confidence": float("nan")})["ok"]
        assert not write("plan", {**seeds["plan"], "participants": [{"ref": "own-unit-3", "timing": {"start_turn": 5, "end_turn": 4}}]})["ok"]
        assert journal.replay(scope) == before
        abstract = write("plan", {**seeds["plan"], "participants": [], "timing": {}})
        assert abstract["ok"] and abstract["record"]["participants"] == [], abstract
        # A full replacement really resets omitted optional fields; discovery says so.
        reset = write("plan", {"plan_key": "explore", "title": "Explore", "objective": "Abstract intent"})
        assert reset["ok"] and reset["record"]["timing"] == {} and reset["record"]["participants"] == []
        assert "reset" in read("plan", "explore")["update_semantics"]["omission"]
        assert read("plan", "absent")["error"] == "memory_record_not_found"
        many = write("plan", {**seeds["plan"], **{f"unknown_{n}": n for n in range(40)}})
        assert len(many["validation"]["errors"]) == 32 and many["validation"]["possibly_more_errors"]
        journal.append(scope, "memory.plan", {"record": {"plan_key": "legacy-large", "title": "Legacy",
            "objective": "x" * 70000}})
        large = read("plan", "legacy-large")
        assert large["error"] == "legacy_memory_record_exceeds_edit_budget" and "record_json" not in large
        # Stale guards still take precedence over shape; no automatic correction/write.
        with patch.dict("os.environ", {"SMACX_MEMORY_REPAIR_CONTEXT": "0"}):
            stale = write("plan", malformed, "r3")
        assert stale["error"] == "stale_memory_observation", stale
        invalid_json = m.smac_memory_update("plan", scope.match_id, "session-contract", "r4", "[")
        assert invalid_json["persistence"]["journal_committed"] is False
        duplicate = m.smac_memory_update("plan", scope.match_id, "session-contract", "r4", '{"title":"A","title":"B"}')
        assert duplicate["error"] == "invalid_memory_record_json" and duplicate["persistence"]["journal_committed"] is False
        # Failure after SQL but around journal append is explicitly uncertain.
        with patch.object(journal, "append", side_effect=JournalError("injected_append_failure")):
            failed = write("plan", seeds["plan"])
        assert failed["persistence"]["stage"] == "sqlite_projection_written" and failed["persistence"]["journal_committed"] is None, failed
        # If the canonical event committed but context building failed, never say nothing saved.
        with patch.object(journal, "project_state", side_effect=JournalError("injected_projection_failure")):
            failed = write("plan", seeds["plan"])
        assert failed["persistence"]["stage"] == "journal_committed" and failed["persistence"]["journal_committed"] is True, failed
        # Reopen journal to verify editable data comes from durable authority, not an SQL edit.
        with patch.object(c, "_journal", return_value=CampaignJournal(root / "campaigns")):
            assert json.loads(read("plan", "explore")["record_json"])["participants"] == seeds["plan"]["participants"]
        with patch.object(c, "_scope_for_match", return_value=None):
            assert not read("plan", "explore")["ok"]

    listed = asyncio.run(m.mcp.list_tools())
    memory = next(tool for tool in listed if tool.name == "smac_memory")
    assert {"record_kind", "key"} <= set(memory.input_schema["properties"])
    assert "editable_record" in memory.input_schema["properties"]["action"]["enum"]
print(json.dumps({"passed": True, "record_families": len(SCHEMAS), "controlled_native_guard": True,
                  "managed_read_edit_write": True, "rejected_writes_unchanged": True,
                  "journal_reopen": True, "failure_stages": True}))
