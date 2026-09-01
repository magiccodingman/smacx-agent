#!/usr/bin/env python3
"""Contained regression for durable identity, scope, and SQLite memory rules."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile

from smacx_store import InvalidRecord, MemoryScope, ScopeViolation, SmacxStore, StoreError


def expect_error(error_type: type[Exception], function, code: str) -> None:
    try:
        function()
    except error_type as exc:
        if str(exc) != code:
            raise AssertionError(f"expected {code}, got {exc}") from exc
    else:
        raise AssertionError(f"expected {error_type.__name__}: {code}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-store-test-") as temporary:
        concurrent_database = Path(temporary) / "concurrent" / "smacx.sqlite3"
        with ThreadPoolExecutor(max_workers=8) as executor:
            concurrent_results = list(executor.map(
                lambda _: SmacxStore(concurrent_database).schema_version(), range(8),
            ))
        if concurrent_results != [1] * 8:
            raise AssertionError("concurrent canonical schema initialization failed")
        incompatible_database = Path(temporary) / "incompatible" / "smacx.sqlite3"
        incompatible_database.parent.mkdir(parents=True)
        with sqlite3.connect(incompatible_database) as connection:
            connection.execute("PRAGMA user_version=4")
        expect_error(
            StoreError, lambda: SmacxStore(incompatible_database),
            "unsupported_prerelease_schema_recreate_database",
        )
        stale_canonical = Path(temporary) / "stale-canonical" / "smacx.sqlite3"
        stale_canonical.parent.mkdir(parents=True)
        with sqlite3.connect(stale_canonical) as connection:
            connection.execute("CREATE TABLE installations(singleton INTEGER PRIMARY KEY)")
            connection.execute("PRAGMA user_version=1")
        expect_error(
            StoreError, lambda: SmacxStore(stale_canonical),
            "unsupported_prerelease_schema_recreate_database",
        )
        database = Path(temporary) / "state" / "smacx.sqlite3"
        store = SmacxStore(database)
        if store.schema_version() != 1:
            raise AssertionError("unexpected schema version")
        installation_id = store.installation_id()

        store.ensure_agent("agent-alpha", "Alpha")
        store.ensure_agent("agent-beta", "Beta")
        renamed = store.ensure_agent("agent-alpha", "Alpha Prime")
        if renamed["display_name"] != "Alpha Prime":
            raise AssertionError("existing agent identity was not edited in place")
        store.create_match(match_id="match-foundation", display_name="Foundation", mode="lan")
        alpha_perspective = store.create_perspective(
            "match-foundation", "agent-alpha", perspective_id="perspective-alpha",
            faction_id=1, faction_name="Gaians",
        )
        beta_perspective = store.create_perspective(
            "match-foundation", "agent-beta", perspective_id="perspective-beta",
            faction_id=2, faction_name="Hive",
        )
        alpha = MemoryScope("match-foundation", "agent-alpha", alpha_perspective["perspective_id"])
        beta = MemoryScope("match-foundation", "agent-beta", beta_perspective["perspective_id"])

        alpha_instance = store.register_instance(instance_id="instance-alpha", scope=alpha, bridge_port=48001)
        beta_instance = store.register_instance(instance_id="instance-beta", scope=beta, bridge_port=48002)
        alpha_session = store.start_session(alpha, alpha_instance["instance_id"], session_id="session-alpha")
        beta_session = store.start_session(beta, beta_instance["instance_id"], session_id="session-beta")

        briefing_hash = "a" * 64
        if store.match_briefing_acknowledged(alpha, "session-alpha", briefing_hash):
            raise AssertionError("match briefing began pre-acknowledged")
        acknowledgement = store.acknowledge_match_briefing(
            alpha, "session-alpha", briefing_hash,
        )
        if acknowledgement["briefing_hash"] != briefing_hash \
                or not store.match_briefing_acknowledged(alpha, "session-alpha", briefing_hash):
            raise AssertionError("exact match briefing acknowledgement was not durable")
        if store.match_briefing_acknowledged(alpha, "session-alpha", "b" * 64) \
                or store.match_briefing_acknowledged(beta, "session-beta", briefing_hash):
            raise AssertionError("briefing acknowledgement crossed a hash or perspective boundary")
        expect_error(
            ScopeViolation,
            lambda: store.acknowledge_match_briefing(beta, "session-alpha", briefing_hash),
            "session_scope_mismatch",
        )

        if store.graph_namespace(alpha) == store.graph_namespace(beta):
            raise AssertionError("Graph namespaces crossed perspectives")
        if not store.graph_namespace(alpha).startswith("smacx_") or len(store.graph_namespace(alpha)) != 54:
            raise AssertionError("Graph namespace was not installation scoped")

        alpha_event = store.append_event(
            alpha, "diplomacy.treaty_signed", {"counterpart": "Deirdre"},
            session_id=alpha_session["session_id"], turn=12, year=2220,
            dedupe_key="treaty-deirdre-2220", importance=85,
            search_text="Signed a Treaty of Friendship with Deirdre",
        )
        duplicate = store.append_event(
            alpha, "diplomacy.treaty_signed", {"counterpart": "Deirdre"},
            session_id=alpha_session["session_id"], dedupe_key="treaty-deirdre-2220",
        )
        if duplicate != alpha_event:
            raise AssertionError("event deduplication did not return the original event")
        store.append_event(
            beta, "strategy.secret", {"intent": "attack"},
            session_id=beta_session["session_id"], search_text="Secret Hive attack plan",
        )
        if len(store.list_events(alpha)) != 1 or len(store.list_events(beta)) != 1:
            raise AssertionError("event scope isolation failed")

        with sqlite3.connect(database) as raw:
            try:
                raw.execute("UPDATE events SET search_text = 'tampered' WHERE event_id = ?", (alpha_event,))
            except sqlite3.IntegrityError as exc:
                if "immutable" not in str(exc):
                    raise
            else:
                raise AssertionError("immutable event update was accepted")

        first_fact = store.put_fact(
            alpha, "session-alpha", "revision-1", "deirdre.intent", "Deirdre seeks peace",
            category="diplomacy", subject="Deirdre", confidence=0.7, observed_turn=12, observed_year=2220,
            source_event_id=alpha_event,
        )
        second_fact = store.put_fact(
            alpha, "session-alpha", "revision-2", "deirdre.intent", "Deirdre seeks a durable alliance",
            category="diplomacy", subject="Deirdre", confidence=0.8, observed_turn=13, observed_year=2221,
        )
        history = store.get_facts(alpha, fact_key="deirdre.intent", include_history=True)
        if first_fact["fact_revision"] != 1 or second_fact["fact_revision"] != 2 or len(history) != 2:
            raise AssertionError("fact correction history was not preserved")
        if len(store.get_facts(alpha, fact_key="deirdre.intent")) != 1:
            raise AssertionError("current fact projection is ambiguous")
        if store.get_facts(beta):
            raise AssertionError("facts leaked into another perspective")
        expect_error(
            InvalidRecord,
            lambda: store.put_fact(
                alpha, "session-alpha", "revision-3", "bad.reference", "move unit id 26 next",
            ),
            "session_local_knowledge_reference",
        )
        expect_error(
            ScopeViolation,
            lambda: store.put_fact(beta, "session-alpha", "revision-3", "cross.scope", "forbidden"),
            "session_scope_mismatch",
        )

        deirdre = store.upsert_actor(
            "match-foundation", "network-player-deirdre", "Deirdre",
            controller_kind="human", faction_id=1, faction_name="Gaians",
            network_player_id="player-7", network_player_name="Alice",
        )
        relationship = store.set_relationship(
            alpha, deirdre["actor_id"], affinity=25, trust=15, respect=30,
            threat=5, grievance=0, obligation=10, confidence=0.75,
            reasons=["Honored a technology exchange", "Signed a treaty"],
        )
        if relationship["relationship_revision"] != 1:
            raise AssertionError("relationship version did not start at one")
        relationship_2 = store.set_relationship(
            alpha, deirdre["actor_id"], affinity=35, trust=25, respect=32,
            threat=4, grievance=0, obligation=15, confidence=0.8,
            reasons=["Followed through on the treaty"], source_event_id=alpha_event,
        )
        if relationship_2["relationship_revision"] != 2:
            raise AssertionError("relationship history was not versioned")

        claim = store.record_claim(
            alpha, "deirdre.future_intent", "Deirdre says she will defend the western border",
            session_id="session-alpha", asserted_by_actor_id=deirdre["actor_id"],
            about_actor_id=deirdre["actor_id"], confidence=0.5, source_event_id=alpha_event,
            turn=13, year=2221,
        )
        belief_1 = store.set_belief(
            alpha, "deirdre.reliability", "Deirdre is probably reliable", confidence=0.65,
            evidence=[(alpha_event, "supports", 0.7)],
        )
        belief_2 = store.set_belief(
            alpha, "deirdre.reliability", "Deirdre has been reliable so far", confidence=0.78,
            evidence=[(alpha_event, "supports", 0.8)],
        )
        if claim["status"] != "unverified" or belief_1["belief_revision"] != 1 \
        or belief_2["belief_revision"] != 2:
            raise AssertionError("claims or beliefs were not recorded correctly")
        expect_error(
            ScopeViolation,
            lambda: store.set_belief(
                beta, "stolen.evidence", "This must not import Alpha evidence", confidence=0.9,
                evidence=[(alpha_event, "supports", 1.0)],
            ),
            "evidence_event_scope_mismatch",
        )

        commitment_1 = store.put_commitment(
            alpha, "defend-west", "Mutual western defense", "Respond if either border is attacked",
            status="accepted", parties=[(deirdre["actor_id"], "counterparty")], due_turn=30,
            source_event_id=alpha_event,
        )
        commitment_2 = store.put_commitment(
            alpha, "defend-west", "Mutual western defense", "The defense agreement was fulfilled",
            status="fulfilled", parties=[(deirdre["actor_id"], "counterparty")], due_turn=30,
            resolution_event_id=alpha_event,
        )
        if commitment_1["commitment_revision"] != 1 or commitment_2["commitment_revision"] != 2:
            raise AssertionError("commitment history was not versioned")

        first_chat = store.record_chat(
            alpha, "native-message-1", "Shall we coordinate against the Hive?",
            session_id="session-alpha", direction="incoming", channel="private",
            sender_actor_id=deirdre["actor_id"], sender_faction_id=1, recipient_faction_id=2,
            turn=14, year=2222,
        )
        duplicate_chat = store.record_chat(
            alpha, "native-message-1", "Shall we coordinate against the Hive?",
            session_id="session-alpha", direction="incoming", channel="private",
        )
        if not duplicate_chat.get("deduplicated") or duplicate_chat["chat_id"] != first_chat["chat_id"]:
            raise AssertionError("chat deduplication failed")
        unread = store.list_chat(alpha, unread_only=True, mark_acknowledged=True)
        if len(unread) != 1 or store.list_chat(alpha, unread_only=True):
            raise AssertionError("chat acknowledgment state failed")
        if store.list_chat(beta):
            raise AssertionError("chat leaked into another perspective")

        group = store.create_chat_group(
            alpha, "Western Council", 1,
            [
                {"faction_id": 1, "display_name": "Deirdre", "faction_name": "Gaians"},
                {"faction_id": 2, "display_name": "Yang", "faction_name": "Hive"},
            ],
        )
        if group["status"] != "inviting" or group["viewer_status"] != "accepted":
            raise AssertionError("group consent did not begin in the inviting state")
        accepted = store.respond_chat_group(beta, group["group_id"], 2, "accepted")
        if accepted["status"] != "active" or len(store.list_chat_groups(beta, 2)) != 1:
            raise AssertionError("group did not activate after every member consented")
        logical = store.begin_group_message(
            alpha, group["group_id"], 1, "Coordinate the western defense.",
            turn=14, year=2222,
        )
        if logical["recipients"] != [2]:
            raise AssertionError("logical group fanout did not preserve the recipient set")
        store.complete_group_delivery(
            logical["logical_message_id"], 2, delivered=True,
            native_message_uid="native-group-message-1",
        )
        with sqlite3.connect(database) as raw:
            delivery = raw.execute(
                "SELECT status, native_message_uid FROM chat_group_deliveries "
                "WHERE logical_message_id=? AND recipient_faction_id=2",
                (logical["logical_message_id"],),
            ).fetchone()
        if delivery != ("delivered", "native-group-message-1"):
            raise AssertionError("logical group delivery was not durably acknowledged")

        goal = store.add_goal(
            alpha, "Secure western border", "Reach a defensible agreement with Deirdre",
            goal_key="secure-west", priority=80, due_turn=20,
        )
        if goal["status"] != "active":
            raise AssertionError("goal was not created active")
        completed_goal = store.add_goal(
            alpha, "Secure western border", "A durable agreement was reached",
            goal_key="secure-west", priority=80, status="completed", due_turn=20,
        )
        if completed_goal["goal_revision"] != 2:
            raise AssertionError("goal history was not versioned")
        summary = store.add_summary(alpha, "situation", "x" * 5000, through_event_id=alpha_event)
        if not summary["compaction_required"]:
            raise AssertionError("oversized summary did not request compaction")

        treaty_results = store.search(alpha, "Deirdre treaty")
        if not treaty_results:
            raise AssertionError("FTS/BM25 did not find scoped memory")
        if store.search(beta, "Deirdre treaty"):
            raise AssertionError("search leaked across perspectives")
        recall = store.recall_many(
            alpha,
            [
                {"query": "Deirdre treaty", "limit": 5},
                {"query": "western defense", "document_kinds": ["goal", "commitment"], "limit": 5},
            ],
            total_token_budget=1000,
        )
        if not recall["groups"] or recall["estimated_tokens"] > recall["token_budget"]:
            raise AssertionError("batched recall did not honor its budget")
        working = store.current_memory(alpha)
        if not working["compaction_required"] or "situation" not in working["compaction_required_sections"]:
            raise AssertionError("working-memory compaction signal was not propagated")
        if len(store.list_projection_records(alpha, "beliefs")) != 1:
            raise AssertionError("current belief projection is ambiguous")
        if len(store.list_projection_records(alpha, "beliefs", include_history=True)) != 2:
            raise AssertionError("belief history was not retained")

        store.close_session("session-alpha")
        expect_error(
            ScopeViolation,
            lambda: store.put_fact(alpha, "session-alpha", "revision-4", "closed.session", "forbidden"),
            "session_not_running",
        )

        reopened = SmacxStore(database)
        if reopened.installation_id() != installation_id or reopened.schema_version() != 1:
            raise AssertionError("database identity or initialization was not durable")
        if len(reopened.get_facts(alpha, fact_key="deirdre.intent", include_history=True)) != 2:
            raise AssertionError("facts did not survive reopening")

        print(json.dumps({
            "event": "pass",
            "payload": {
                "schema_version": reopened.schema_version(),
                "concurrent_initialization_safe": True,
                "incompatible_prerelease_schema_rejected": True,
                "stale_same_revision_schema_rejected": True,
                "installation_stable": True,
                "immutable_events": True,
                "perspective_isolation": True,
                "session_scope_enforced": True,
                "briefing_acknowledgement_exact_and_isolated": True,
                "fact_history_preserved": True,
                "claims_beliefs_relationships_versioned": True,
                "goals_commitments_versioned": True,
                "chat_deduplicated_and_acknowledged": True,
                "consent_group_chat_logical_delivery": True,
                "graph_namespace_isolated": True,
                "fts5_bm25_scoped": True,
                "batched_recall_budgeted": True,
                "compaction_signal": True,
            },
        }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
