#!/usr/bin/env python3
"""Contained regression for controller identity, chat, memory, and legacy import."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

import smacx_controller as controller


def main() -> int:
    originals = {
        "root": controller.KNOWLEDGE_ROOT,
        "database": controller.PLATFORM_DB_PATH,
        "store": controller._store_instance,
        "store_path": controller._store_instance_path,
        "journal": controller._journal_instance,
        "journal_root": controller._journal_instance_root,
        "bridge": controller.bridge_request,
    }
    match_id = "match-platform-controller"
    session_id = "session-platform-controller"
    geographic_speech = "Near Gaia's Landing, west of the ridge; location-peer-only is my local label."
    chat_packets = []
    state = {
        "match_id": match_id,
        "session_id": session_id,
        "revision": "revision-controller-1",
        "turn": 18,
        "year": 2180,
    }

    def fake_bridge(operation: str, timeout: float = 8.0, **arguments: object) -> dict:
        del timeout
        if operation == "ping":
            return {"ok": True}
        if operation == "status":
            return {"ok": True, "identity": {
                "match_id": state["match_id"], "session_id": state["session_id"],
            }}
        if operation == "semantic_snapshot":
            return {"ok": True, "snapshot": dict(state)}
        if operation == "semantic_chat":
            chat_packets.append(dict(arguments))
            if arguments.get("action") == "send":
                return {
                    "ok": True,
                    "sent": True,
                    "event": {
                        "sequence": 2,
                        "direction": "outbound",
                        "channel": "private",
                        "sender_faction_id": 1,
                        "recipient_faction_id": 2,
                        "turn": 18,
                        "client_message_id": arguments.get("client_message_id"),
                        "text": arguments.get("text"),
                    },
                }
            return {
                "ok": True,
                "identity": {"match_id": match_id, "session_id": session_id},
                "multiplayer_active": True,
                "latest_sequence": 1,
                "participants": [
                    {
                        "network_player_index": 1, "player_id": 101, "player_name": "Qwen",
                        "faction_id": 1, "faction_name": "University", "local": True,
                    },
                    {
                        "network_player_index": 2, "player_id": 202, "player_name": "MorganPlayer",
                        "faction_id": 2, "faction_name": "Morganites", "local": False,
                    },
                ],
                "messages": [{
                    "sequence": 1,
                    "direction": "inbound",
                    "channel": "received",
                    "sender_faction_id": 2,
                    "recipient_faction_id": None,
                    "turn": 18,
                    "text": geographic_speech,
                }],
            }
        raise AssertionError(f"unexpected bridge operation: {operation}")

    try:
        with tempfile.TemporaryDirectory(prefix="smacx-platform-controller-") as temporary:
            root = Path(temporary)
            controller.KNOWLEDGE_ROOT = root / "legacy"
            controller.PLATFORM_DB_PATH = root / "state" / "smacx.sqlite3"
            controller._store_instance = None
            controller._store_instance_path = None
            controller._journal_instance = None
            controller._journal_instance_root = None
            controller.bridge_request = fake_bridge
            controller._write_match_manifest(match_id, {"match_id": match_id, "sessions": []})
            context = controller._ensure_platform_identity(
                match_id,
                session_id=session_id,
                agent_id="agent-controller-test",
                perspective_id="perspective-controller-test",
                instance_id="instance-controller-test",
                faction_id=1,
                faction_name="University",
                mode="lan",
                start_session=True,
            )
            if not context["identity"]["graph_namespace"].startswith("smacx_"):
                raise AssertionError("platform identity omitted isolated graph namespace")

            chat = controller.semantic_chat("list", match_id=match_id, session_id=session_id)
            attention = chat.get("durable", {}).get("attention", [])
            participants = chat.get("durable", {}).get("participants", [])
            remote = next((actor for actor in participants if actor.get("network_player_id") == "202"), None)
            if len(attention) != 1 or not remote or remote.get("faction_name") != "Morganites":
                raise AssertionError(f"chat identity persistence failed: {chat}")
            assert chat["durable"]["untrusted_in_game_speech"] is True
            assert attention[0]["content"] == geographic_speech
            # Location-like text has no structured map/reference authority.
            assert set(attention[0].get("metadata", {})) <= {
                "native_sequence", "client_message_id", "sender_player_name", "sender_faction_name"}
            repeated = controller.semantic_chat("list", match_id=match_id, session_id=session_id)
            if repeated.get("durable", {}).get("attention"):
                raise AssertionError("acknowledged native chat was delivered twice")

            relationship = controller.write_platform_memory(
                "relationship",
                match_id,
                session_id,
                state["revision"],
                {
                    "actor_id": remote["actor_id"],
                    "affinity": 20,
                    "trust": 15,
                    "respect": 10,
                    "threat": 5,
                    "grievance": 0,
                    "obligation": 10,
                    "confidence": 0.65,
                    "reasons": ["Promised to honor the western-border agreement"],
                },
            )
            goal = controller.write_platform_memory(
                "goal",
                match_id,
                session_id,
                state["revision"],
                {
                    "goal_key": "secure-western-border",
                    "title": "Secure western border",
                    "description": "Verify Morgan's promise while expanding elsewhere.",
                    "priority": 75,
                    "status": "active",
                },
            )
            if not relationship.get("ok") or not goal.get("ok"):
                raise AssertionError(f"typed memory write failed: {relationship} / {goal}")
            working = controller.read_platform_memory(
                "working_set", match_id, session_id=session_id,
            )
            sections = working.get("memory", {}).get("sections", {})
            if len(sections.get("relationships", [])) != 1 or len(sections.get("goals", [])) != 1:
                raise AssertionError(f"working set omitted projections: {working}")
            # A fresh process must reconstruct the successful write from journal
            # authority, without relying on the in-process working-state cache.
            controller._journal_instance = None
            controller._journal_instance_root = None
            recovered = controller.read_platform_memory(
                "working_set", match_id, session_id=session_id,
            )
            recovered_goals = recovered.get("memory", {}).get("sections", {}).get("goals", [])
            assert any(row.get("goal_key") == "secure-western-border" for row in recovered_goals), recovered
            assert controller._journal().verify(controller._scope_for_match(
                match_id, session_id=session_id))["ok"]
            searched = controller.read_platform_memory(
                "search", match_id, session_id=session_id, query="western border",
            )
            if not searched.get("items"):
                raise AssertionError(f"scoped search failed: {searched}")

            sent = controller.semantic_chat(
                "send",
                match_id=match_id,
                session_id=session_id,
                client_message_id="controller-send-1",
                text="Agreed. I will hold the western line.",
                recipient_faction_id=2,
            )
            stored_chat = controller.read_platform_memory(
                "chat", match_id, session_id=session_id, limit=10,
            )
            if not sent.get("ok") or len(stored_chat.get("items", [])) != 2:
                raise AssertionError(f"outbound chat was not persisted: {sent} / {stored_chat}")
            assert all(set(packet) == {"action", "match_id", "session_id", "client_message_id",
                       "text", "recipient_faction_id", "after_sequence"} for packet in chat_packets)
            if any(
                row.get("direction") == "outbound" and row.get("acknowledged_unix") is None
                for row in stored_chat["items"]
            ):
                raise AssertionError("outbound chat incorrectly entered unread attention")

            legacy_match = "match-legacy-import"
            state.update({"match_id": legacy_match, "session_id": "session-legacy-import"})
            legacy_directory = controller.KNOWLEDGE_ROOT / legacy_match
            controller._write_match_manifest(legacy_match, {"match_id": legacy_match, "sessions": []})
            legacy_ledger = {
                "version": 1,
                "match_id": legacy_match,
                "entries": {
                    "morgan.intent": {
                        "knowledge_revision": 2,
                        "value": "Morgan later favored cooperation.",
                        "category": "diplomacy",
                        "subject": "Morgan",
                        "observed_turn": 9,
                        "observed_year": 2160,
                    },
                },
                "history": [
                    {
                        "key": "morgan.intent", "knowledge_revision": 1,
                        "value": "Morgan appeared guarded.", "category": "diplomacy",
                        "subject": "Morgan", "observed_turn": 7, "observed_year": 2140,
                    },
                    {
                        "key": "morgan.intent", "knowledge_revision": 2,
                        "value": "Morgan later favored cooperation.", "category": "diplomacy",
                        "subject": "Morgan", "observed_turn": 9, "observed_year": 2160,
                    },
                ],
            }
            (legacy_directory / "knowledge.json").write_text(
                json.dumps(legacy_ledger), encoding="utf-8",
            )
            imported = controller.read_match_knowledge(
                legacy_match, key="morgan.intent", include_history=True,
            )
            if imported.get("authority") != "campaign_journal" \
                    or imported.get("storage") != "sqlite_query_projection" \
                    or len(imported.get("history", [])) != 2:
                raise AssertionError(f"legacy history import failed: {imported}")

            print(json.dumps({
                "event": "pass",
                "payload": {
                    "chat_player_faction_mapping": True,
                    "geographic_speech_is_untrusted_text_without_map_payload": True,
                    "chat_exactly_once_attention": True,
                    "outbound_chat_persisted": True,
                    "typed_memory_guarded": True,
                    "working_set_and_search": True,
                    "legacy_history_imported": True,
                },
            }, separators=(",", ":")))
            return 0
    finally:
        controller.KNOWLEDGE_ROOT = originals["root"]
        controller.PLATFORM_DB_PATH = originals["database"]
        controller._store_instance = originals["store"]
        controller._store_instance_path = originals["store_path"]
        controller._journal_instance = originals["journal"]
        controller._journal_instance_root = originals["journal_root"]
        controller.bridge_request = originals["bridge"]


if __name__ == "__main__":
    raise SystemExit(main())
