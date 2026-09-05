"""Request-only sovereign runtime-context assembly.

This module deliberately returns data, never provider messages. The Hermes
wire hook owns the trusted terminal insertion boundary so none of this state
can enter durable transcript storage by accident.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable, Mapping

from smacx_attention import AttentionService
from smacx_store import MemoryScope
from smacx_world import WorldService
from smacx_world_model import estimate_tokens
from smacx_world_types import canonical_json, content_hash


RUNTIME_CONTEXT_SCHEMA = "smacx.runtime-context.v1"

# One allocator owns the whole request-only envelope.  Component ceilings are
# reservations, not independent promises: their actual use determines the
# space offered to the anchor.  The rich envelope remains bounded at one eighth
# of a 256K context and can still contain a maximal 16K semantic anchor.
RUNTIME_BUDGETS = {
    "64k": {
        "total": 13_107, "anchor": 6_000, "cognition": 2_600,
        "operations": 1_200, "attention": 1_800, "recall": 0,
        "delta_reserve": 512,
    },
    "256k": {
        "total": 32_768, "anchor": 16_000, "cognition": 6_000,
        "operations": 2_500, "attention": 4_000, "recall": 1_000,
        "delta_reserve": 1_500,
    },
}


def _field(item: Mapping[str, Any], name: str, default: Any = None) -> Any:
    value = item.get("fields", {}).get(name) if isinstance(item.get("fields"), Mapping) else None
    return value.get("value", default) if isinstance(value, Mapping) else default


def _focus(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    protocol = snapshot.get("protocol") if isinstance(snapshot.get("protocol"), Mapping) else {}
    phase = str(protocol.get("phase") or "unknown")
    interaction = snapshot.get("interaction") \
        if isinstance(snapshot.get("interaction"), Mapping) else {}
    if phase == "interaction":
        material = canonical_json({
            "kind": interaction.get("kind"),
            "label": interaction.get("popup_label"),
            "required_action": protocol.get("required_action"),
            "counterpart_ref": interaction.get("counterpart_faction_ref"),
            "base_ref": interaction.get("base_ref"),
            "unit_ref": interaction.get("own_unit_ref") or interaction.get("contact_ref"),
        })
        return {
            # Action revisions can change for unrelated animation/network ticks.
            # Focus identity changes only when the semantic concern changes.
            "focus_id": "focus-interaction-" + hashlib.sha256(
                material.encode()).hexdigest()[:24],
            "kind": "blocking_interaction", "mandatory": True,
            "label": str(interaction.get("popup_label") or "interaction"),
            "required_action": protocol.get("required_action"),
            "action_revision": snapshot.get("revision"),
        }
    ready = snapshot.get("ready_unit_refs") if isinstance(snapshot.get("ready_unit_refs"), list) else []
    if ready:
        unit = ready[0] if isinstance(ready[0], Mapping) else {}
        own_unit_ref = str(unit.get("own_unit_ref") or "unknown")
        return {
            "focus_id": "focus-unit-" + own_unit_ref,
            "kind": "ready_unit", "mandatory": False,
            "unit": {key: unit.get(key) for key in
                     ("own_unit_ref", "name", "location_ref", "roles")
                     if unit.get(key) is not None},
            "ready_count": len(ready), "action_revision": snapshot.get("revision"),
        }
    return {
        "focus_id": "focus-phase-" + phase, "kind": phase,
        "mandatory": phase in {"wait", "capability_gap"},
        "required_action": protocol.get("required_action"),
        "action_revision": snapshot.get("revision"),
    }


def _compact_cognition_record(item: Mapping[str, Any], *, text_limit: int = 1200) -> dict[str, Any]:
    """Bound one routine runtime projection without changing durable truth."""
    result = dict(item)
    for field in ("terms", "description", "objective", "content", "reasons"):
        value = result.get(field)
        if isinstance(value, str) and len(value) > text_limit:
            result[field] = value[:text_limit - 3] + "..."
            result[f"{field}_truncated"] = True
    return result


def _cognition(working: Mapping[str, Any], *, token_budget: int,
               current_turn: int | None = None,
               current_year: int | None = None) -> dict[str, Any]:
    """Keep interpretations and intent; mechanical history stays in the world."""
    sections = working.get("sections") if isinstance(working.get("sections"), Mapping) else {}
    situation = sections.get("situation") \
        if isinstance(sections.get("situation"), Mapping) else {}
    def rows(name: str) -> list[dict[str, Any]]:
        value = sections.get(name)
        return [_compact_cognition_record(item) for item in (value or ())
                if isinstance(item, Mapping)]

    goals = [item for item in rows("goals")
             if str(item.get("status") or "active") in {"active", "paused"}]
    goals.sort(key=lambda item: (
        -int(item.get("priority") or 0),
        int(item.get("due_turn")) if item.get("due_turn") is not None else 10**9,
        -float(item.get("created_unix") or item.get("updated_unix") or 0),
    ))
    plans = [item for item in rows("plans")
             if str(item.get("status") or "active") in {"proposed", "active", "paused"}]
    commitments = [item for item in rows("commitments")
                   if str(item.get("status") or "proposed") in {"proposed", "accepted"}]

    def commitment_salience(item: Mapping[str, Any]) -> tuple[Any, ...]:
        status = str(item.get("status") or "proposed")
        due_turn = item.get("due_turn")
        due_year = item.get("due_year")
        if due_turn is not None and current_turn is not None:
            urgency = abs(int(due_turn) - int(current_turn))
        elif due_year is not None and current_year is not None:
            urgency = abs(int(due_year) - int(current_year))
        else:
            urgency = 10**9
        # Accepted promises are binding even when old.  Proposed commitments
        # follow, then urgency and recency break ties.
        return (0 if status == "accepted" else 1, urgency,
                -float(item.get("created_unix") or item.get("updated_unix") or 0))

    commitments.sort(key=commitment_salience)
    result = {
        "goals": goals[:12],
        "plans": plans[:12],
        "commitments": commitments[:12],
        "relationships": rows("relationships")[:12],
        "beliefs": rows("beliefs")[:12],
        "summaries": [_compact_cognition_record(item) for item in
                      (situation.get("summaries") or ())
                      if isinstance(item, Mapping)][:6],
    }
    # Durable cognition can be verbose on disk. Runtime carries the most recent
    # bounded interpretation/intent, never an unbounded notebook projection.
    removal_order = ("summaries", "relationships", "beliefs", "goals", "plans")
    while estimate_tokens(result) > token_budget:
        reduced = False
        for section in removal_order:
            values = result[section]
            if len(values) > 1:
                # Journal projections are valuable-first (newest first, or
                # highest-priority first for goals/commitments).  Remove the
                # least valuable tail rather than the head.
                values.pop()
                reduced = True
                break
        if not reduced:
            break
    if estimate_tokens(result) > token_budget:
        raise RuntimeError("context_budget_exhausted:cognition_and_commitments")
    return result


def _operation_context(operations: list[dict[str, Any]], *, token_budget: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in operations:
        compact = dict(item)
        if not compact.get("foreground"):
            compact = {key: compact.get(key) for key in (
                "operation_id", "operation_kind", "objective", "status",
                "linked_plan_id", "linked_goal_id", "last_renewed_turn",
            ) if compact.get(key) is not None}
            if len(str(compact.get("objective") or "")) > 300:
                compact["objective"] = str(compact["objective"])[:297] + "..."
        if estimate_tokens([*result, compact]) > token_budget:
            break
        result.append(compact)
    return result


def _attention_payload(item: Mapping[str, Any]) -> dict[str, Any]:
    payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
    if item.get("attention_kind") == "world_change":
        delta = payload.get("delta") if isinstance(payload.get("delta"), Mapping) else {}
        current = delta.get("current") if isinstance(delta.get("current"), Mapping) else {}
        fields = current.get("fields") if isinstance(current.get("fields"), Mapping) else {}
        compact_fields = {
            name: value for name, value in fields.items()
            if name in {"name", "owner_ref", "threatened", "relations", "state",
                        "population", "production_name", "hp", "max_hp"}
        }
        return {"delta": {
            "object_ref": delta.get("object_ref"), "change": delta.get("change"),
            "kind": current.get("kind"), "location_ref": current.get("location_ref"),
            "fields": compact_fields,
        }}
    if estimate_tokens(payload) <= 768:
        return dict(payload)
    if item.get("attention_kind") == "watch_trigger":
        matches = []
        for match in payload.get("matches", ()):
            if not isinstance(match, Mapping):
                continue
            milestone = match.get("milestone")
            if isinstance(milestone, Mapping):
                matches.append({"milestone": {key: milestone.get(key) for key in
                                               ("state", "ready_count", "required_count")}})
            event = match.get("temporal_event")
            if isinstance(event, Mapping):
                matches.append({"temporal_event": {key: event.get(key) for key in
                                                    ("event_kind", "contact_ref", "unit_ref", "base_ref", "item_name", "turn")
                                                    if event.get(key) is not None}})
        return {"watch_id": payload.get("watch_id"), "watch_kind": payload.get("watch_kind"),
                "subject_refs": list(payload.get("subject_refs") or ())[:8],
                "matches": matches[:4], "detail_truncated": True,
                "detail": "Inspect the watch and its referenced world objects for qualified detail."}
    if item.get("attention_kind") == "production_progress":
        return {"event_count": payload.get("event_count"), "details_truncated": True,
                "events": [{key: event.get(key) for key in ("event_kind", "base_ref", "item_name", "turn")}
                           for event in list(payload.get("events") or ())[:4]
                           if isinstance(event, Mapping)]}
    return {"payload_hash": content_hash(payload), "keys": sorted(payload)[:24],
            "detail": "Use smac_world/semantic chat recall for bounded detail."}


def _bounded_attention(lease: Mapping[str, Any], *, token_budget: int) -> dict[str, Any]:
    result = {key: lease.get(key) for key in
              ("attention_lease_id", "through_cursor", "status", "reused")
              if lease.get(key) is not None}
    result["items"] = []
    for raw in lease.get("items", ()):
        if not isinstance(raw, Mapping):
            continue
        item = {key: raw.get(key) for key in (
            "attention_id", "attention_kind", "observation_cursor", "priority",
            "attention_sequence", "critical", "redelivered", "captured_unix",
        ) if raw.get(key) is not None}
        item["payload"] = _attention_payload(raw)
        candidate = {**result, "items": [*result["items"], item]}
        if estimate_tokens(candidate) > token_budget:
            result["truncated"] = True
            result["remaining_count"] = len(lease.get("items", ())) - len(result["items"])
            break
        result["items"].append(item)
    return result


class RuntimeContextAssembler:
    def __init__(
        self, *, scope: MemoryScope, world: WorldService, attention: AttentionService,
        snapshot: Callable[[], Mapping[str, Any]],
        working_state: Callable[[], Mapping[str, Any]],
        interpretive_recall: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> None:
        self.scope = scope
        self.world = world
        self.attention = attention
        self.snapshot = snapshot
        self.working_state = working_state
        self.interpretive_recall = interpretive_recall

    def build(self, *, episode_id: str, episode_mode: str,
              context_length: int) -> dict[str, Any]:
        if episode_mode not in {"gameplay", "communication", "recovery"}:
            raise ValueError("invalid_episode_mode")
        snapshot = dict(self.snapshot())
        projection_identity, projection = self.world._projection()
        turn_state = next((item for item in projection.get("objects", [])
                           if item.get("kind") == "turn_state"), {})
        turn = _field(turn_state, "turn", snapshot.get("turn"))
        attention_lease = self.attention.lease(episode_id, limit=32)
        dependencies = self.attention.semantic_dependency_hashes(projection)
        active = self.attention.runtime_state(
            current_world_revision=int(projection["world_revision"]),
            current_world_epoch=projection_identity.world_epoch,
            object_dependency_hashes=dependencies,
            current_turn=turn,
        )
        focus = _focus(snapshot)
        operation_refs = [
            str(ref) for operation in active["operations"]
            for ref in operation.get("referenced_world_objects", ())
        ]
        triggered_watch_refs = [
            str(ref)
            for item in attention_lease.get("items", ())
            if isinstance(item, Mapping) and item.get("attention_kind") == "watch_trigger"
            for ref in (item.get("payload", {}).get("subject_refs", ())
                        if isinstance(item.get("payload"), Mapping) else ())
        ]
        for item in attention_lease.get("items", ()):
            if not isinstance(item, Mapping) or item.get("attention_kind") != "watch_trigger":
                continue
            payload = item.get("payload", {})
            if not isinstance(payload, Mapping):
                continue
            for match in payload.get("matches", ()):
                if not isinstance(match, Mapping):
                    continue
                event = match.get("temporal_event", {})
                if isinstance(event, Mapping):
                    triggered_watch_refs.extend(str(event[key]) for key in
                                                ("contact_ref", "unit_ref", "base_ref", "location_ref")
                                                if event.get(key))
                milestone = match.get("milestone", {})
                if isinstance(milestone, Mapping):
                    triggered_watch_refs.extend(str(row["ref"]) for row in milestone.get("requirements", ())
                                                if isinstance(row, Mapping) and row.get("ref"))
        triggered_watch_refs = list(dict.fromkeys(triggered_watch_refs))[:64]
        focus_ref = ""
        focus_unit = focus.get("unit") if isinstance(focus.get("unit"), Mapping) else {}
        if focus_unit.get("own_unit_ref"):
            focus_ref = str(focus_unit["own_unit_ref"])
        tier = "64k" if context_length < 131072 else "256k"
        budgets = RUNTIME_BUDGETS[tier]
        cognition = _cognition(
            self.working_state(), token_budget=budgets["cognition"],
            current_turn=int(turn) if turn is not None else None,
            current_year=int(snapshot["year"]) if snapshot.get("year") is not None else None,
        )
        active_plan_refs = [
            str(ref) for plan in cognition.get("plans", ())
            if isinstance(plan, Mapping)
            for ref in [*plan.get("target_refs", ()),
                        *(item.get("ref") for item in plan.get("participants", ())
                          if isinstance(item, Mapping) and item.get("ref"))]
        ][:64]
        operations = _operation_context(
            active["operations"], token_budget=budgets["operations"],
        )
        attention_context = _bounded_attention(
            attention_lease, token_budget=budgets["attention"],
        )
        recent_material_refs: list[str] = []
        for attention_item in attention_context.get("items", ()):
            if not isinstance(attention_item, Mapping) \
                    or attention_item.get("attention_kind") != "world_change":
                continue
            delta = attention_item.get("payload", {}).get("delta", {})
            if not isinstance(delta, Mapping):
                continue
            recent_material_refs.extend(
                str(value) for value in (delta.get("object_ref"), delta.get("location_ref"))
                if value
            )
        recent_material_refs = list(dict.fromkeys(recent_material_refs))[:64]
        recall_context: dict[str, Any] | None = None
        recall_terms = []
        for item in attention_context.get("items", []):
            if item.get("attention_kind") != "chat":
                continue
            message = item.get("payload", {}).get("message", {})
            if isinstance(message, Mapping) and message.get("content"):
                recall_terms.append(str(message["content"]))
        if budgets["recall"] and recall_terms and self.interpretive_recall is not None:
            recalled = self.interpretive_recall("\n".join(recall_terms[-4:]))
            if recalled.get("ok") and isinstance(recalled.get("facts"), list):
                recall_context = {
                    "source": "optional_graphiti_interpretive_recall",
                    "facts": recalled["facts"][:8],
                    "authority": "fallible_history_not_current_mechanical_truth",
                }
        # Reserve all non-anchor mandatory/current cognition first.  Semantic
        # LOD receives only the remaining coherent envelope budget.
        payload = {
            "schema": RUNTIME_CONTEXT_SCHEMA,
            "episode": {"episode_id": episode_id, "mode": episode_mode,
                        "mutation_authority": episode_mode == "gameplay"},
            "identity": {
                **projection_identity.as_dict(),
                "world_revision": int(projection["world_revision"]),
                "observation_cursor": int(projection["observation_cursor"]),
                "action_revision": snapshot.get("revision"),
                "continuity": projection.get("continuity", "incomplete"),
            },
            "world": {},
            "focus": focus,
            "attention": attention_context,
            "working_cognition": cognition,
            "operations": operations,
            "watch_summary": {"active_count": active["active_watch_count"]},
            "plan_health": self.attention.plan_health(
                projection, active["operations"],
                [str(item.get("own_unit_ref")) for item in snapshot.get("ready_unit_refs", [])
                 if isinstance(item, Mapping)], dependencies),
            "generated_unix": time.time(),
        }
        if recall_context is not None:
            payload["interpretive_recall"] = recall_context
        non_anchor_tokens = estimate_tokens(payload)
        anchor_cap = min(
            budgets["anchor"],
            budgets["total"] - non_anchor_tokens - budgets["delta_reserve"],
        )
        if anchor_cap < 512:
            raise RuntimeError("context_budget_exhausted:mandatory_runtime_context")
        anchor = self.world.anchor(
            context_length=context_length, focus_ref=focus_ref or None,
            operation_refs=operation_refs, triggered_watch_refs=triggered_watch_refs,
            active_plan_refs=active_plan_refs,
            recent_material_refs=recent_material_refs,
            token_cap=anchor_cap,
        )
        payload["world"] = {
            "world_anchor_id": anchor["world_anchor_id"],
            "world_anchor_revision": anchor["world_anchor_revision"],
            "anchor_observation_cursor": anchor["anchor_observation_cursor"],
            "anchor": anchor["payload"],
            "net_deltas": anchor.get("net_deltas", []),
            "net_deltas_truncated": bool(anchor.get("net_deltas_truncated")),
        }
        # The authoritative anchor/focus, binding commitments, and critical
        # attention are pinned. Optional interpretive recall is the first
        # runtime component discarded under pressure.
        runtime_cap = budgets["total"]
        if estimate_tokens(payload) > runtime_cap:
            payload.pop("interpretive_recall", None)
            recall_context = None
        if estimate_tokens(payload) > runtime_cap:
            payload["operations"] = [item for item in operations if item.get("foreground")][:1]
        if estimate_tokens(payload) > runtime_cap:
            items = payload["attention"].get("items", [])
            payload["attention"]["items"] = [item for item in items if item.get("critical")]
            payload["attention"]["truncated"] = len(items) != len(payload["attention"]["items"])
        if estimate_tokens(payload) > runtime_cap:
            raise RuntimeError("context_budget_exhausted:pinned_runtime_context")
        # Only attention that is present in this final serialized envelope may
        # transition to placed.  Anything removed by either local attention
        # budgeting or whole-envelope pressure is detached and requeued with
        # its original stable attention ID.
        original_lease_count = len(attention_lease.get("items", ()))
        placement = self.attention.restrict_for_placement(
            str(payload["attention"]["attention_lease_id"]),
            [str(item["attention_id"]) for item in payload["attention"].get("items", ())],
        )
        payload["attention"]["through_cursor"] = placement["through_cursor"]
        if placement["requeued_ids"]:
            payload["attention"]["truncated"] = True
        # Compute once from the original lease and the final serialized IDs.
        # _bounded_attention and whole-envelope pressure can both omit rows;
        # summing their counters double-counts the same item.
        remaining = original_lease_count - len(payload["attention"].get("items", ()))
        if remaining:
            payload["attention"]["remaining_count"] = remaining
        else:
            payload["attention"].pop("remaining_count", None)
        payload["token_composition"] = {
            "anchor": estimate_tokens(payload["world"]["anchor"]),
            "deltas": estimate_tokens(payload["world"]["net_deltas"]),
            "focus": estimate_tokens(payload["focus"]),
            "attention": estimate_tokens(payload["attention"]),
            "cognition": estimate_tokens(cognition),
            "operations": estimate_tokens(payload["operations"]),
            "interpretive_recall": estimate_tokens(recall_context or {}),
        }
        payload["budget"] = {
            "tier": tier, "total": runtime_cap, "anchor_cap": anchor_cap,
        }
        payload["token_estimate"] = 0
        # Reach a fixed point after serializing the estimate itself.  Comparing
        # only the pre-value estimate can undercount a request exactly at the
        # envelope boundary by the digits added to this field.
        for _ in range(4):
            actual_tokens = estimate_tokens(payload)
            if payload["token_estimate"] == actual_tokens:
                break
            payload["token_estimate"] = actual_tokens
        actual_tokens = estimate_tokens(payload)
        payload["token_estimate"] = actual_tokens
        if actual_tokens > runtime_cap:
            raise RuntimeError("context_budget_exhausted:runtime_metadata")
        for component, value in payload["token_composition"].items():
            self.world.store.telemetry(
                "runtime_context", f"tokens_{component}", value, scope=self.scope,
                timeline_id=projection_identity.timeline_id,
                dimensions={"episode_mode": episode_mode},
            )
        self.world.store.telemetry(
            "runtime_context", "tokens_total", payload["token_estimate"], scope=self.scope,
            timeline_id=projection_identity.timeline_id,
            dimensions={"episode_mode": episode_mode},
        )
        return payload


def envelope(payload: Mapping[str, Any]) -> str:
    return (
        '<SMACX_RUNTIME_CONTEXT schema="smacx.runtime-context.v1">\n'
        + canonical_json(payload)
        + "\n</SMACX_RUNTIME_CONTEXT>"
    )
