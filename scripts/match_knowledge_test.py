#!/usr/bin/env python3
"""Regression for match-scoped, observation-guarded knowledge storage."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import smacx_controller as controller


def main() -> int:
    original_root = controller.KNOWLEDGE_ROOT
    original_bridge_request = controller.bridge_request
    match_id = "match-knowledge-test"
    other_match_id = "match-knowledge-other"
    session_id = "session-knowledge-test"
    state = {
        "match_id": match_id,
        "session_id": session_id,
        "revision": "revision-1",
        "turn": 12,
        "year": 2112,
    }

    def fake_bridge_request(operation: str, timeout: float = 8.0, **arguments: object) -> dict:
        del timeout, arguments
        if operation == "ping":
            return {"ok": True}
        if operation == "status":
            return {"ok": True, "identity": {
                "match_id": state["match_id"], "session_id": state["session_id"],
            }}
        if operation == "semantic_snapshot":
            return {"ok": True, "snapshot": dict(state)}
        raise AssertionError(f"unexpected operation: {operation}")

    try:
        with tempfile.TemporaryDirectory(prefix="smacx-knowledge-") as directory:
            controller.KNOWLEDGE_ROOT = Path(directory)
            controller.bridge_request = fake_bridge_request
            controller._write_match_manifest(match_id, {"match_id": match_id, "sessions": []})
            controller._write_match_manifest(other_match_id, {"match_id": other_match_id, "sessions": []})

            first = controller.put_match_knowledge(
                match_id, session_id, "revision-1", "faction-1.intent",
                "Requested a joint attack against faction 3.",
                category="diplomacy", subject="faction-1",
            )
            if not first.get("ok") or first.get("updated_existing") \
                    or first.get("entry", {}).get("observed_turn") != 12:
                raise AssertionError(f"initial write failed: {first}")

            state["revision"] = "revision-2"
            state["turn"] = 13
            stale = controller.put_match_knowledge(
                match_id, session_id, "revision-1", "faction-1.intent",
                "This stale correction must not be written.",
            )
            if stale.get("error") != "stale_knowledge_observation":
                raise AssertionError(f"stale write was not rejected: {stale}")

            corrected = controller.put_match_knowledge(
                match_id, session_id, "revision-2", "faction-1.intent",
                "Later withdrew the joint-attack request.",
                category="diplomacy", subject="faction-1",
            )
            if not corrected.get("ok") or not corrected.get("updated_existing") \
                    or corrected.get("entry", {}).get("knowledge_revision") != 2:
                raise AssertionError(f"correction failed: {corrected}")

            history = controller.read_match_knowledge(
                match_id, key="faction-1.intent", include_history=True,
            )
            if not history.get("ok") or len(history.get("history", [])) != 2 \
                    or history.get("entry", {}).get("observed_turn") != 13:
                raise AssertionError(f"audit history failed: {history}")

            wrong_match = controller.read_match_knowledge(other_match_id)
            if wrong_match.get("error") != "wrong_active_match":
                raise AssertionError(f"cross-match read was not rejected: {wrong_match}")

            ledger_path = Path(str(corrected["ledger_path"]))
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            if ledger.get("match_id") != match_id \
                    or len(ledger.get("entries", {})) != 1 \
                    or len(ledger.get("history", [])) != 2:
                raise AssertionError(f"ledger contents invalid: {ledger}")

            print(json.dumps({
                "event": "pass",
                "payload": {
                    "match_scoped": True,
                    "session_and_revision_guarded": True,
                    "cross_match_read_rejected": True,
                    "correction_history_preserved": True,
                    "arbitrary_paths_exposed": False,
                },
            }, separators=(",", ":")))
            return 0
    finally:
        controller.KNOWLEDGE_ROOT = original_root
        controller.bridge_request = original_bridge_request


if __name__ == "__main__":
    raise SystemExit(main())
