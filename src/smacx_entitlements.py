"""Deterministic information-entitlement boundary for perspective projection.

The native bridge is expected to return perspective-safe rows.  This module is
the second, independently testable boundary: enriched adapters may only add a
field when its declared channel is available to the current player.  It never
infers entitlement from strategic usefulness.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping


class EntitlementChannel(str, Enum):
    OWNED = "owned_state"
    DIRECT = "direct_sight"
    SURVEY = "unity_survey"
    PACT = "pact_shared"
    INFILTRATION = "infiltration"
    GOVERNOR = "governor"
    PROJECT_INTELLIGENCE = "project_intelligence"
    SATELLITE = "satellite_report"
    PUBLIC = "public_report"
    SCENARIO = "scenario"
    PLAYER_REPORT = "player_report"


@dataclass(frozen=True)
class PerspectiveEntitlements:
    faction_ref: str
    unity_survey: bool = False
    governor: bool = False
    project_intelligence: bool = False
    pact_factions: frozenset[str] = frozenset()
    infiltrated_factions: frozenset[str] = frozenset()
    satellite_channels: frozenset[str] = frozenset()
    scenario_channels: frozenset[str] = frozenset()

    def permits(self, channel: str, *, owner_ref: str = "", subject: str = "") -> bool:
        try:
            kind = EntitlementChannel(channel)
        except ValueError:
            return False
        if kind in {EntitlementChannel.PUBLIC, EntitlementChannel.PLAYER_REPORT}:
            return True
        if kind == EntitlementChannel.OWNED:
            return not owner_ref or owner_ref == self.faction_ref
        if kind == EntitlementChannel.DIRECT:
            return True  # the native candidate exists only while directly observable
        if kind == EntitlementChannel.SURVEY:
            return self.unity_survey
        if kind == EntitlementChannel.PACT:
            return owner_ref in self.pact_factions
        if kind == EntitlementChannel.INFILTRATION:
            return owner_ref in self.infiltrated_factions
        if kind == EntitlementChannel.GOVERNOR:
            return self.governor
        if kind == EntitlementChannel.PROJECT_INTELLIGENCE:
            return self.project_intelligence
        if kind == EntitlementChannel.SATELLITE:
            return subject in self.satellite_channels
        if kind == EntitlementChannel.SCENARIO:
            return subject in self.scenario_channels
        return False


def sanitize_enriched_fields(
    row: Mapping[str, Any], entitlements: PerspectiveEntitlements,
) -> dict[str, Any]:
    """Return one row with only explicitly entitled enrichment fields.

    Ordinary bridge fields have already crossed the native perspective filter.
    Optional ``entitled_fields`` entries are deliberately opt-in and shaped as
    ``{name: {value, channel, owner_ref?, subject?}}``.  Hidden/spectator/admin
    payloads are rejected even when nested in an otherwise valid row.
    """
    if any(key in row for key in ("hidden_state", "spectator_state", "admin_state")):
        raise ValueError("forbidden_perspective_payload")
    result = {key: value for key, value in row.items() if key != "entitled_fields"}
    channels: dict[str, str] = {}
    fields = row.get("entitled_fields")
    if not isinstance(fields, Mapping):
        return result
    for name, envelope in fields.items():
        if not isinstance(name, str) or not isinstance(envelope, Mapping):
            continue
        channel = str(envelope.get("channel") or "")
        owner_ref = str(envelope.get("owner_ref") or row.get("owner_ref") or "")
        subject = str(envelope.get("subject") or name)
        if entitlements.permits(channel, owner_ref=owner_ref, subject=subject):
            result[name] = envelope.get("value")
            channels[name] = channel
    if channels:
        # Collector-private provenance metadata.  The projector converts this
        # to field-level evidence and never exposes the raw envelope.
        result["_entitlement_channels"] = channels
    return result


def sanitize_bundle(bundle: Mapping[str, Any], entitlements: PerspectiveEntitlements) -> dict[str, Any]:
    if any(key in bundle for key in ("hidden_state", "spectator_state", "admin_state")):
        raise ValueError("forbidden_world_input")
    result = dict(bundle)
    for collection in ("tiles", "bases", "units", "factions", "global"):
        rows = bundle.get(collection)
        if isinstance(rows, Iterable) and not isinstance(rows, (str, bytes, Mapping)):
            result[collection] = [sanitize_enriched_fields(row, entitlements)
                                  for row in rows if isinstance(row, Mapping)]
    return result
