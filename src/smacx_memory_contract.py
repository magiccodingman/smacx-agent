"""Managed typed-memory input contracts, shared by discovery and validation.

These validate shape, not truth. Scope, evidence and native freshness guards remain
the writer's responsibility. Historical records are never rewritten by this module.
"""
from __future__ import annotations

import copy
import json
import math
import re
from typing import Any

from smacx_store import ID_PATTERN, KEY_PATTERN, MEMORY_STATUS_VALUES
from smacx_intent import HORIZONS


def text(maximum=8000, **kw):
    return {"type": "string", "maxLength": maximum, **kw}


def obj(properties, required=(), *, extensions=False):
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": extensions}


def array(items):
    return {"type": "array", "items": items, "maxItems": 128, "default": []}


KEY = text(128, pattern=KEY_PATTERN.pattern)
REF = text(96, pattern=ID_PATTERN.pattern)
ACTOR = text(160, pattern=r"^(?:faction-[0-9]+|[A-Za-z0-9_-]{8,96})$")
CONFIDENCE = {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.5}
TURN = {"type": "integer", "minimum": 0}
OPTIONAL_EVENT = {**REF, "nullable": True, "default": None}
RECONCILIATION = obj({"turn": TURN, "disposition": text(enum=["deferred", "blocked"]),
                      "reason": text(600, minLength=1)}, ("turn", "disposition", "reason"))
WINDOW = obj({"start_turn": TURN, "end_turn": TURN}, ("start_turn", "end_turn"))
INTENT = obj({"intent_horizon": text(enum=sorted(HORIZONS)), "intent_turn": TURN,
              "reconciliation": RECONCILIATION, "start_turn": TURN, "end_turn": TURN},
             extensions=True)
INTENT["default"] = {}
INTENT["description"] = "Other metadata is preserved as interpretation, not mechanically evaluated. Current-turn horizons default intent_turn to the guarded current turn."
PARTICIPANT = obj({"ref": text(160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$"),
                   "intended_role": text(1000), "target_ref": text(160, minLength=1),
                   "exclusive": {"type": "boolean"}, "production_item": text(1000),
                   "energy_credits": TURN, "timing": WINDOW}, ("ref",))
CONFIRMATION = obj({"dependency_values": array(obj({"ref": text(160, minLength=1),
    "field": text(160, minLength=1), "value": {}}, ("ref", "field", "value")))}, extensions=True)
CONFIRMATION["default"] = {}
CONFIRMATION["description"] = "Additional confirmation metadata is preserved, not interpreted as a mechanical predicate."

SCHEMAS = {
    "claim": obj({"topic": KEY, "content": text(minLength=1),
        "asserted_by_actor_id": {**ACTOR, "nullable": True, "default": None},
        "about_actor_id": {**ACTOR, "nullable": True, "default": None},
        "confidence": CONFIDENCE, "source_event_id": OPTIONAL_EVENT}, ("topic", "content")),
    "belief": obj({"topic": KEY, "content": text(minLength=1), "confidence": CONFIDENCE,
        "evidence": array(obj({"event_id": REF, "stance": {**KEY, "default": "supports"},
                               "weight": CONFIDENCE}, ("event_id",)))}, ("topic", "content")),
    "relationship": obj({"actor_id": ACTOR, **{name: {"type": "integer", "minimum": low,
        "maximum": 100, "default": 0} for name, low in (("affinity", -100), ("trust", -100),
        ("respect", -100), ("threat", -100), ("grievance", 0), ("obligation", -100))},
        "confidence": CONFIDENCE, "reasons": array(text()), "source_event_id": OPTIONAL_EVENT}, ("actor_id",)),
    "commitment": obj({"commitment_key": KEY, "title": text(200, minLength=1), "terms": text(minLength=1),
        "parties": array(obj({"actor_id": ACTOR, "role": {**KEY, "default": "counterparty"}}, ("actor_id",))),
        "due_turn": {**TURN, "nullable": True, "default": None},
        "due_year": {**TURN, "nullable": True, "default": None},
        "source_event_id": OPTIONAL_EVENT, "resolution_event_id": OPTIONAL_EVENT}, ("commitment_key", "title", "terms")),
    "goal": obj({"goal_key": {**KEY, "description": "Omission creates a new key; include the current goal_key to revise."},
        "title": text(200, minLength=1), "description": text(4000, minLength=1),
        "priority": {"type": "integer", "minimum": 0, "maximum": 100, "default": 50},
        "trigger": INTENT, "due_turn": {**TURN, "nullable": True, "default": None},
        "due_year": {**TURN, "nullable": True, "default": None},
        "parent_goal_id": OPTIONAL_EVENT, "source_event_id": OPTIONAL_EVENT}, ("title", "description")),
    "plan": obj({"plan_key": KEY, "title": text(200, minLength=1), "objective": text(minLength=1),
        "target_refs": array(REF), "participants": array(PARTICIPANT), "timing": INTENT,
        "dependencies": array(REF), "intended_role": text(1000, default=""),
        "contingencies": array(text()), "last_confirmation": CONFIRMATION,
        "linked_commitments": array(REF), "contradictory_evidence": array(text()),
        "source_event_id": OPTIONAL_EVENT}, ("plan_key", "title", "objective")),
    "summary": obj({"section": text(enum=["situation", "relationships", "goals", "plans", "commitments", "recent_events", "chat"]),
        "content": text(24000, minLength=1), "through_event_id": OPTIONAL_EVENT}, ("section", "content")),
}
for kind, default in (("claim", "unverified"), ("commitment", "proposed"), ("goal", "active"), ("plan", "active")):
    SCHEMAS[kind]["properties"]["status"] = text(enum=list(MEMORY_STATUS_VALUES[kind]), default=default)
for name in ("participants", "dependencies"):
    SCHEMAS["plan"]["properties"][name]["maxItems"] = 64
CONFIRMATION["properties"]["dependency_values"]["maxItems"] = 16

KEYS = dict(claim="topic", belief="topic", relationship="actor_id", commitment="commitment_key",
            goal="goal_key", plan="plan_key", summary="section")
COLLECTIONS = dict(claim="claims", belief="beliefs", relationship="relationships", commitment="commitments",
                   goal="goals", plan="plans", summary="summaries")


def contract(kind: str) -> dict:
    if kind not in SCHEMAS:
        raise ValueError("invalid_memory_record_kind")
    return {"schema": "smacx.memory-contract.v1", "record_kind": kind, "identity_field": KEYS[kind],
            "operation": "append_claim_current_view_by_topic" if kind == "claim" else "replace_current_record",
            "omission": "Optional fields reset to their defaults or absence; never merged. Explicit empty lists clear bindings.",
            "maximum_record_characters": 65536,
            "binding_evidence": "Accepted syntax does not prove a reference is currently present or executable. Plan health reports bounded current applicability; stale or unresolved participants remain declared intent, not verified assignments.",
            "retry": "Read editable_record by record_kind/key, edit its record_json, then explicitly submit with a fresh decision guard. Reading does not reserve a revision; reconsider if the world changes.",
            "input": copy.deepcopy(SCHEMAS[kind])}


def tool_guidance() -> str:
    rows = []
    for kind, schema in SCHEMAS.items():
        rows.append(kind + "={" + ",".join(name + ("" if name in schema["required"] else "?")
                                          for name in schema["properties"]) + "}")
    return "record_json fields: " + "; ".join(rows) + ". "


def parse_record_json(raw: str) -> dict:
    def unique_fields(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError("duplicate_memory_record_field")
            result[name] = value
        return result
    parsed = json.loads(raw, object_pairs_hook=unique_fields)
    if not isinstance(parsed, dict):
        raise ValueError("invalid_memory_record_json")
    return parsed


def validate_record(kind: str, record: Any, turn: int) -> list[dict]:
    """Collect up to 32 shape errors in one pass without normalizing input."""
    errors = []
    if kind not in SCHEMAS:
        return [{"field": "action", "code": "invalid_memory_update_action", "allowed_values": list(SCHEMAS),
                 "message": "Choose a supported typed record kind."}]
    try:
        encoded = json.dumps(record, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return [{"field": "record_json", "code": "invalid_memory_record_json",
                 "message": "Use finite JSON values and bounded nesting."}]
    if len(encoded) > 65536:
        return [{"field": "record_json", "code": "memory_record_too_large",
                 "message": "One typed record is limited to 65536 characters; use focused records and references."}]

    def fail(path, schema, code="invalid_memory_record", message="Value does not match the input contract."):
        if len(errors) < 32:
            errors.append({"field": path, "code": code, "message": message,
                **{("allowed_values" if k == "enum" else k): v for k, v in schema.items()
                   if k in {"type", "enum", "minimum", "maximum", "minLength", "maxLength", "maxItems", "pattern"}}})

    def walk(value, schema, path):
        if len(errors) >= 32:
            return
        if value is None and schema.get("nullable"):
            return
        typ = schema.get("type")
        valid = {"string": isinstance(value, str), "object": isinstance(value, dict),
                 "array": isinstance(value, list), "integer": type(value) is int,
                 "number": type(value) is int or (type(value) is float and math.isfinite(value)),
                 "boolean": type(value) is bool}.get(typ, True)
        if not valid:
            fail(path, schema)
            return
        if "enum" in schema and value not in schema["enum"]:
            fail(path, schema)
        if typ == "string":
            if not schema.get("minLength", 0) <= len(value.strip()) <= schema.get("maxLength", 8000) or (
                    "pattern" in schema and not re.fullmatch(schema["pattern"], value)):
                fail(path, schema)
        elif typ in {"integer", "number"}:
            if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
                fail(path, schema)
        elif typ == "array":
            if len(value) > schema["maxItems"]:
                fail(path, schema)
            for index, item in enumerate(value[:schema["maxItems"]]):
                walk(item, schema["items"], f"{path}[{index}]")
        elif typ == "object":
            props = schema["properties"]
            for name in schema["required"]:
                if name not in value:
                    fail(path + "." + name, props[name], message="Required field is missing; revisions replace the full record.")
            for name, item in value.items():
                if name in props:
                    walk(item, props[name], path + "." + name)
                elif not schema["additionalProperties"]:
                    fail(path + "." + str(name), {}, message="Unknown or misplaced field; nothing is silently discarded.")
            if type(value.get("start_turn")) is int and type(value.get("end_turn")) is int and value["start_turn"] > value["end_turn"]:
                fail(path + ".end_turn", {"minimum": value["start_turn"]}, message="Reservation ends before it starts.")
            if schema is INTENT and ("start_turn" in value) != ("end_turn" in value):
                fail(path, {}, message="A reservation needs both start_turn and end_turn; omit both for untimed intent.")
            if path.endswith(".reconciliation") and type(value.get("turn")) is int and value["turn"] > turn:
                fail(path + ".turn", {"maximum": turn}, "intent_reconciliation_cannot_be_future")
    walk(record, SCHEMAS[kind], "record_json")
    if kind == "plan" and isinstance(record, dict):
        confirmation = record.get("last_confirmation")
        if isinstance(confirmation, dict) and isinstance(confirmation.get("dependency_values"), list):
            for index, expectation in enumerate(confirmation["dependency_values"][:16]):
                if isinstance(expectation, dict) and isinstance(expectation.get("ref"), str) and isinstance(record.get("dependencies", []), list) and expectation["ref"] not in record.get("dependencies", []):
                    fail(f"record_json.last_confirmation.dependency_values[{index}].ref", {},
                         message="Mechanical expectations must reference an explicitly declared dependency.")
    return errors


def editable_record(kind: str, stored: dict) -> dict:
    """Strip storage bookkeeping, retaining every writable field and its defaults."""
    result = {}
    for name, spec in SCHEMAS[kind]["properties"].items():
        if name in stored:
            value = stored[name]
            if value is None and not spec.get("nullable") and "default" in spec:
                value = spec["default"]
            result[name] = copy.deepcopy(value)
        elif "default" in spec:
            result[name] = copy.deepcopy(spec["default"])
    return result
