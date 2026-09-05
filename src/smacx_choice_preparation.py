"""Bounded, disposable semantic parameter selection; never an action executor."""

from __future__ import annotations

import copy
import threading
import time
import uuid
from typing import Any, Mapping


class PreparationError(ValueError):
    pass


class ChoicePreparations:
    """Server-private draft capabilities bound to one exact sovereign frame.

    Providers select issued options or a specifically advertised energy amount.
    Native command names, handles and argument dictionaries are never inputs.
    Drafts have no durable authority and are discarded on process recovery.
    """

    def __init__(self, *, ttl: float = 180, limit: int = 128):
        self.ttl = ttl
        self.limit = limit
        self.rows: dict[str, dict[str, Any]] = {}
        self.lock = threading.RLock()

    @staticmethod
    def scope(identity: Mapping, context: Mapping) -> dict:
        return {"native": dict(identity), "world": dict(context.get("identity") or {})}

    @staticmethod
    def _steps(kind: str, catalog: Mapping) -> list[tuple[str, list[dict]]]:
        if kind == "unit_design":
            if catalog.get("mutations_supported") is not True:
                return []
            if int(catalog.get("custom_slots", {}).get("available", 0)) < 1:
                return []
            catalogs = catalog.get("catalogs", {})
            steps = []
            for field, group in (("chassis_id", "chassis"), ("weapon_id", "weapons"),
                                 ("armor_id", "armor"), ("reactor_id", "reactors"),
                                 ("ability_id_1", "abilities"), ("ability_id_2", "abilities")):
                source_key = "ability_id" if group == "abilities" else field
                options = [{"label": str(row.get("name") or group),
                            "value": row[source_key],
                            "details": {key: row[key] for key in ("triad", "speed", "missile", "offense", "defense", "cost", "power", "abbreviation") if key in row}}
                           for row in catalogs.get(group, ())
                           if isinstance(row, Mapping) and type(row.get(source_key)) is int]
                if group == "abilities":
                    options.insert(0, {"label": "No special ability", "value": -1})
                steps.append((field, options))
            return steps
        if kind == "social_engineering" and catalog.get("enabled") is True:
            categories = {row.get("key"): row for row in catalog.get("categories", ())}
            return [(key, [{"label": str(row["name"]), "value": row["model_id"],
                           "effects": row.get("intrinsic_effects", {})}
                          for row in categories.get(key, {}).get("options", ())])
                    for key in ("politics", "economics", "values", "future")]
        if kind == "energy_allocation":
            return [("economy", [{"label": f"{value * 10}% economy", "value": value}
                                  for value in range(11)]),
                    ("psych", [{"label": f"{value * 10}% psych", "value": value}
                                for value in range(11)])]
        if kind == "unit_upgrade" and catalog.get("mutations_supported") is True:
            prototypes = catalog.get("available_prototypes", ())
            targets = [{"label": row["name"], "value": row["prototype_id"]}
                       for row in prototypes if row.get("custom")]
            return [("source_prototype_id", [
                        {"label": row["name"], "value": row["prototype_id"]}
                        for row in prototypes if (row.get("active_unit_count", 0)
                        or row.get("production_queue_references", 0))
                        and any(target["value"] != row["prototype_id"] for target in targets)]),
                    ("target_prototype_id", targets)]
        return []

    def begin(self, catalog: Mapping, *, identity: Mapping, context: Mapping,
              kind: str, selectors: Mapping) -> list[dict]:
        drafts = []
        steps = self._steps(kind, catalog)
        if steps and all(options for _, options in steps):
            drafts.append({"purpose": kind, "steps": steps, "selected": {}, "index": 0})
        if kind == "unit_design":
            upgrade_steps = self._steps("unit_upgrade", catalog)
            if upgrade_steps and all(options for _, options in upgrade_steps):
                drafts.append({"purpose": "unit_upgrade", "steps": upgrade_steps,
                               "selected": {}, "index": 0})
        for choice in catalog.get("choices", ()):
            if choice.get("command") not in {"give_energy_gift", "propose_human_energy"}:
                continue
            minimum, maximum = choice.get("amount_min"), choice.get("amount_max")
            if type(minimum) is int and type(maximum) is int and 0 < minimum <= maximum:
                offered = choice.get("amount_options")
                if choice["command"] == "give_energy_gift" and offered is None:
                    continue
                if offered is not None and (not isinstance(offered, list) or not offered
                        or any(type(value) is not int or not minimum <= value <= maximum for value in offered)):
                    continue
                drafts.append({"purpose": "energy_amount", "command": choice["command"],
                               "minimum": minimum, "maximum": maximum,
                               "offered": offered,
                               "label": choice.get("meaning") or "Select energy amount"})
        with self.lock:
            return [self._store({**draft, "scope": self.scope(identity, context),
                                 "kind": kind, "selectors": dict(selectors)}) for draft in drafts]

    def _store(self, row: dict) -> dict:
        now = time.monotonic()
        for ref in list(self.rows):
            if self.rows[ref]["expires"] <= now:
                del self.rows[ref]
        while len(self.rows) >= self.limit:
            del self.rows[next(iter(self.rows))]
        ref = "preparation-" + uuid.uuid4().hex
        row = copy.deepcopy(row)
        row["expires"] = now + self.ttl
        public = {"preparation_ref": ref, "purpose": row["purpose"],
                  "read_only": True, "next_tool": "smac_choices"}
        if row["purpose"] == "energy_amount":
            public.update(label=row["label"], amount_input={"minimum": row["minimum"],
                                                         "maximum": row["maximum"],
                                                         "unit": "energy credits"})
            if row.get("offered") is not None:
                public["amount_input"]["allowed_values"] = row["offered"]
        else:
            field, options = row["steps"][row["index"]]
            if field == "psych":
                options = [item for item in options if item["value"] <= 10 - row["selected"]["economy"]]
            if field == "ability_id_2":
                options = [item for item in options if item["value"] < 0
                           or item["value"] != row["selected"]["ability_id_1"]]
            if field == "target_prototype_id":
                options = [item for item in options if item["value"] != row["selected"]["source_prototype_id"]]
            if len(options) > 128:
                raise PreparationError("preparation_catalog_limit")
            row["options"] = {"option-" + uuid.uuid4().hex: item for item in options}
            public.update(step=field.removesuffix("_id").replace("_id_", "_slot_"),
                          options=[{"option_ref": key, "label": item["label"],
                                    **({"effects": item["effects"]} if "effects" in item else {}),
                                    **({"details": item["details"]} if item.get("details") else {})}
                                   for key, item in row["options"].items()],
                          remaining_steps=len(row["steps"]) - row["index"])
        self.rows[ref] = row
        return public

    def advance(self, ref: str, *, option_ref: str, amount: int | None,
                identity: Mapping, context: Mapping, kind: str) -> dict:
        with self.lock:
            row = self.rows.get(ref)
            if row is None or row["expires"] <= time.monotonic():
                self.rows.pop(ref, None)
                raise PreparationError("unknown_or_expired_preparation")
            if row["scope"] != self.scope(identity, context) or kind != row["kind"]:
                raise PreparationError("preparation_scope_or_revision_changed")
            row = copy.deepcopy(row)
            if row["purpose"] == "energy_amount":
                if option_ref or type(amount) is not int or not row["minimum"] <= amount <= row["maximum"]:
                    raise PreparationError("invalid_prepared_energy_amount")
                if row.get("offered") is not None and amount not in row["offered"]:
                    raise PreparationError("energy_amount_not_offered")
                selected = {"amount": amount}
            else:
                if amount is not None or option_ref not in row["options"]:
                    raise PreparationError("invalid_preparation_option")
                field = row["steps"][row["index"]][0]
                row["selected"][field] = row["options"][option_ref]["value"]
                row["index"] += 1
                del self.rows[ref]
                if row["index"] < len(row["steps"]):
                    return {"preparation": self._store(row)}
                selected = row["selected"]
                if kind == "energy_allocation":
                    selected["labs"] = 10 - selected["economy"] - selected["psych"]
            self.rows.pop(ref, None)
            return {"selectors": row["selectors"], "selected": selected,
                    "purpose": row["purpose"], "command": row.get("command")}


def bind_ballot_choices(choices) -> list[dict]:
    """A proposal and its bounded ballot are one exact native commitment."""
    result = []
    for row in choices:
        if not isinstance(row, Mapping):
            continue
        # The reviewed human trade catalog historically names the displayed
        # value technology_id; its native executor consumes tech_id.
        if row.get("command") == "propose_human_technology":
            technology = row.get("tech_id", row.get("technology_id"))
            if type(technology) is not int:
                continue
            row = {**row, "tech_id": technology}
        if row.get("command") != "choose_council_proposal":
            result.append(row)
            continue
        ballot = row.get("ballot", {})
        bound = {key: value for key, value in row.items() if key != "ballot"}
        title = row.get("display_name") or row.get("name") or "Council proposal"
        if ballot.get("type") == "candidate":
            for candidate in ballot.get("candidates", ()):
                if type(candidate.get("faction_id")) is int:
                    result.append({**bound, "candidate_faction_id": candidate["faction_id"],
                                   "candidate_name": candidate["faction_name"],
                                   "label": f"{title}: vote for {candidate['faction_name']}"})
        elif ballot.get("type") == "yea_nay":
            for response in ballot.get("responses", ()):
                if response in {"yea", "nay"}:
                    result.append({**bound, "response": response, "label": f"{title}: {response}"})
        elif type(row.get("candidate_faction_id")) is int or row.get("response") in {"yea", "nay"}:
            result.append(row)
        # An unbound proposal is intentionally withheld.
    return result


def bind_numeric_choices(catalog: Mapping, selected: Mapping) -> list[dict]:
    """Only the two native-advertised credit prompts accept a scalar binding."""
    amount = selected.get("amount")
    if type(amount) is not int:
        return bind_ballot_choices(catalog.get("choices", ()))
    return [{**row, "amount": amount}
            for row in catalog.get("choices", ())
            if row.get("command") in {"give_energy_gift", "propose_human_energy"}
            and (row.get("command") != "give_energy_gift" or isinstance(row.get("amount_options"), list))
            and type(row.get("amount_min")) is int and type(row.get("amount_max")) is int
            and ("amount_options" not in row or amount in row["amount_options"])
            and row["amount_min"] <= amount <= row["amount_max"]]
