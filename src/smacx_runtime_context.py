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
            "counterpart": interaction.get("faction_id"),
            "base": interaction.get("base_id"),
            "unit": interaction.get("unit_id"),
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
        return {
            "focus_id": "focus-unit-" + str(unit.get("id") or "unknown"),
            "kind": "ready_unit", "mandatory": False,
            "unit": {key: unit.get(key) for key in ("id", "name", "location_ref")
                     if unit.get(key) is not None},
            "ready_count": len(ready), "action_revision": snapshot.get("revision"),
        }
    return {
        "focus_id": "focus-phase-" + phase, "kind": phase,
        "mandatory": phase in {"wait", "capability_gap"},
        "required_action": protocol.get("required_action"),
        "action_revision": snapshot.get("revision"),
    }


def _cognition(working: Mapping[str, Any], *, token_budget: int) -> dict[str, Any]:
    """Keep interpretations and intent; mechanical history stays in the world."""
    sections = working.get("sections") if isinstance(working.get("sections"), Mapping) else {}
    situation = sections.get("situation") \
        if isinstance(sections.get("situation"), Mapping) else {}
    result = {
        "goals": list(sections.get("goals") or [])[:12],
        "plans": list(sections.get("plans") or [])[:12],
        "commitments": list(sections.get("commitments") or [])[:12],
        "relationships": list(sections.get("relationships") or [])[:12],
        "beliefs": list(sections.get("beliefs") or [])[:12],
        "summaries": list(situation.get("summaries") or [])[:6],
    }
    # Durable cognition can be verbose on disk. Runtime carries the most recent
    # bounded interpretation/intent, never an unbounded notebook projection.
    removal_order = ("summaries", "relationships", "beliefs", "goals", "plans")
    while estimate_tokens(result) > token_budget:
        reduced = False
        for section in removal_order:
            values = result[section]
            if len(values) > 1:
                values.pop(0)
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
        active = self.attention.runtime_state(
            current_world_revision=int(projection["world_revision"]),
            current_world_epoch=projection_identity.world_epoch,
            object_dependency_hashes={
                str(item["object_ref"]): content_hash(item)
                for item in projection.get("objects", [])
            }, current_turn=turn,
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
        focus_ref = ""
        focus_unit = focus.get("unit") if isinstance(focus.get("unit"), Mapping) else {}
        if focus_unit.get("id") is not None:
            focus_ref = f"own-unit-{focus_unit['id']}"
        anchor = self.world.anchor(
            context_length=context_length, focus_ref=focus_ref or None,
            operation_refs=operation_refs, triggered_watch_refs=triggered_watch_refs,
        )
        cognition = _cognition(
            self.working_state(), token_budget=3000 if context_length < 131072 else 7000,
        )
        operations = _operation_context(
            active["operations"], token_budget=1600 if context_length < 131072 else 4000,
        )
        attention_context = _bounded_attention(
            attention_lease, token_budget=2048 if context_length < 131072 else 6000,
        )
        recall_context: dict[str, Any] | None = None
        recall_terms = []
        for item in attention_context.get("items", []):
            if item.get("attention_kind") != "chat":
                continue
            message = item.get("payload", {}).get("message", {})
            if isinstance(message, Mapping) and message.get("content"):
                recall_terms.append(str(message["content"]))
        if recall_terms and self.interpretive_recall is not None:
            recalled = self.interpretive_recall("\n".join(recall_terms[-4:]))
            if recalled.get("ok") and isinstance(recalled.get("facts"), list):
                recall_context = {
                    "source": "optional_graphiti_interpretive_recall",
                    "facts": recalled["facts"][:8],
                    "authority": "fallible_history_not_current_mechanical_truth",
                }
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
            "world": {
                "world_anchor_id": anchor["world_anchor_id"],
                "world_anchor_revision": anchor["world_anchor_revision"],
                "anchor_observation_cursor": anchor["anchor_observation_cursor"],
                "anchor": anchor["payload"],
                "net_deltas": anchor.get("net_deltas", []),
                "net_deltas_truncated": bool(anchor.get("net_deltas_truncated")),
            },
            "focus": focus,
            "attention": attention_context,
            "working_cognition": cognition,
            "operations": operations,
            "watch_summary": {"active_count": active["active_watch_count"]},
            "generated_unix": time.time(),
        }
        if recall_context is not None:
            payload["interpretive_recall"] = recall_context
        # The authoritative anchor/focus, binding commitments, and critical
        # attention are pinned. Optional interpretive recall is the first
        # runtime component discarded under pressure.
        runtime_cap = min(16_000, max(8_000, int(context_length * 0.20)))
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
        payload["token_composition"] = {
            "anchor": estimate_tokens(payload["world"]["anchor"]),
            "deltas": estimate_tokens(payload["world"]["net_deltas"]),
            "focus": estimate_tokens(payload["focus"]),
            "attention": estimate_tokens(payload["attention"]),
            "cognition": estimate_tokens(cognition),
            "operations": estimate_tokens(payload["operations"]),
            "interpretive_recall": estimate_tokens(recall_context or {}),
        }
        payload["token_estimate"] = estimate_tokens(payload)
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
