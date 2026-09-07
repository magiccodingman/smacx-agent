"""Typed current-turn review over existing journal goals and plans."""
from __future__ import annotations
from typing import Mapping, Any

HORIZONS = {"this_turn_required", "this_turn_preferred", "next_opportunity",
            "persistent_goal", "monitor", "backlog"}


def validate_intent(metadata: Mapping[str, Any], turn: int) -> dict:
    result = dict(metadata)
    horizon = result.get("intent_horizon")
    if horizon is not None and horizon not in HORIZONS:
        raise ValueError("invalid_intent_horizon")
    if horizon in {"this_turn_required", "this_turn_preferred"}:
        result.setdefault("intent_turn", turn)
        if type(result["intent_turn"]) is not int or result["intent_turn"] < 0:
            raise ValueError("invalid_intent_turn")
    review = result.get("reconciliation")
    if review is not None:
        if not isinstance(review, Mapping) or review.get("disposition") not in {"deferred", "blocked"}:
            raise ValueError("invalid_intent_reconciliation")
        if type(review.get("turn")) is not int or not 0 <= review["turn"] <= turn:
            raise ValueError("intent_reconciliation_cannot_be_future")
        reason = review.get("reason")
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 600:
            raise ValueError("intent_reconciliation_reason_required")
        result["reconciliation"] = {"turn": review["turn"], "disposition": review["disposition"],
                                    "reason": reason.strip()}
    return result


def pending_intents(replayed: Mapping[str, Any], turn: int, *, limit: int = 8) -> dict:
    """Read unbudgeted canonical records; no interpretation of handoff prose."""
    pending = []
    for kind, metadata_key in (("goal", "trigger"), ("plan", "timing")):
        for entry in replayed.get(kind + "s", {}).values():
            record = entry.get("record", {})
            if record.get("status", "active") not in {"active", "proposed"}:
                continue
            meta = record.get(metadata_key) or {}
            horizon = meta.get("intent_horizon")
            if horizon not in {"this_turn_required", "this_turn_preferred"}:
                continue
            intended_turn = meta.get("intent_turn")
            if type(intended_turn) is not int or intended_turn > turn:
                continue
            review = meta.get("reconciliation") or {}
            if review.get("turn") == turn and review.get("disposition") in {"deferred", "blocked"}:
                continue
            pending.append({"action": kind, "key": record.get(kind + "_key"),
                "record_id": record.get(kind + "_id"), "title": str(record.get("title", ""))[:200],
                "intent_horizon": horizon, "intent_turn": intended_turn,
                "status": record.get("status"), "actionability": "not_inferred_from_intent",
                "metadata_field": metadata_key})
    pending.sort(key=lambda row: (row["intent_horizon"] != "this_turn_required", row["intent_turn"], str(row["key"])))
    return {"turn": turn, "total_pending": len(pending), "items": pending[:limit],
            "remaining_count": max(0, len(pending)-limit),
            "resolution_options": {
                "resolve_now": "Perform the intended action, verify its effect, then update the same goal/plan status.",
                "defer_intentionally": "Update the same record's trigger/timing.reconciliation with current turn, disposition=deferred and reason.",
                "mark_blocked": "Update the same record's trigger/timing.reconciliation with current turn, disposition=blocked and reason.",
                "cancel": "Update the same goal or plan to abandoned; preserve the reason in its description/objective."},
            "write_tool": "smac_memory_update", "preserve_existing_record_fields": True,
            "long_horizon_intent_does_not_block": True}


def may_close_turn(command: str, snapshot: Mapping[str, Any], arguments: Mapping[str, Any]) -> bool:
    if command in {"end_turn", "skip_all_ready_units"}:
        return True
    if command == "respond_to_end_turn_confirmation":
        return arguments.get("response") == "proceed"
    # Native preferences can finish the turn while resolving the final unit.
    # Management actions remain reachable while reconciliation is pending.
    ready = snapshot.get("ready_unit_refs")
    return isinstance(ready, list) and len(ready) <= 1 and int(arguments.get("unit_id", -1)) >= 0
