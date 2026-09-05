#!/usr/bin/env python3
"""Deterministic 64K/256K and Huge-map semantic-footprint benchmark."""

from __future__ import annotations

import json
import time

from smacx_topology import KnownSquare
from smacx_world_model import SemanticLodProjector, estimate_tokens


def field(value):
    return {"value": value, "epistemic_status": "current", "source": "direct_sight",
            "last_verified_turn": 100, "provenance_ref": "benchmark"}


def grid(width: int, height: int):
    return [KnownSquare(f"location-{(x + width*y)//2}", x, y, "land", True)
            for y in range(height) for x in range(y & 1, width, 2)]


def obj(ref: str, kind: str, at: str | None = None, **values):
    return {"object_ref": ref, "kind": kind, "location_ref": at, "status": "active",
            "fields": {name: field(value) for name, value in values.items()}}


def projection(width: int, height: int, objects):
    squares = grid(width, height)
    return {"identity": {"match_id": "match-benchmark",
                         "perspective_id": "perspective-benchmark",
                         "timeline_id": "timeline-main", "world_epoch": "world-benchmark"},
            "world_revision": 100, "observation_cursor": 500, "turn": 100, "year": 2200,
            "map_shape": {"width": width, "height": height, "horizontal_wrap": True},
            "known_squares": squares, "objects": objects(squares)}


def fragmented_projection(width: int, height: int):
    squares = [KnownSquare(f"island-{x}-{y}", x, y, "land", True)
               for y in range(0, height, 4) for x in range(0, width, 4)]
    return {"identity": {"match_id": "match-benchmark",
                         "perspective_id": "perspective-benchmark",
                         "timeline_id": "timeline-main", "world_epoch": "world-benchmark"},
            "world_revision": 100, "observation_cursor": 500, "turn": 100, "year": 2200,
            "map_shape": {"width": width, "height": height, "horizontal_wrap": False},
            "known_squares": squares, "objects": quiet(squares)}


def quiet(squares):
    return [obj("base-home", "base", squares[0].location_ref, name="Home", population=8),
            obj("faction-us", "faction", None, faction_name="Us", relations={}),
            obj("research", "research_state", None, state={"technology": "Doctrine"})]


def chaotic(squares):
    result = quiet(squares)
    for index in range(420):
        result.append(obj(f"contact-{index}", "foreign_contact",
                          squares[index * 137 % len(squares)].location_ref,
                          owner_ref=f"faction-{2 + index % 6}", hp=7 + index % 4))
    for index in range(120):
        result.append(obj(f"base-{index}", "base",
                          squares[index * 211 % len(squares)].location_ref,
                          name=f"Base {index}", owner_ref=f"faction-{1 + index % 7}",
                          population=1 + index % 12))
    for index in range(40):
        result.append(obj(f"project-{index}", "project", None,
                          name=f"Project {index}", state="announced"))
    return result


def timed(tier: str, data):
    started = time.perf_counter()
    value = SemanticLodProjector(context_tier=tier).build(data)
    return value, (time.perf_counter() - started) * 1000


def facts(anchor):
    return {(item.get("kind"), item.get("object_ref"))
            for item in anchor.get("strategic_objects", [])}


def main() -> int:
    small = projection(32, 16, quiet)
    huge = projection(320, 160, quiet)
    huge_busy = projection(320, 160, chaotic)
    fragmented = fragmented_projection(320, 160)
    small64, small64ms = timed("64k", small)
    huge64, huge64ms = timed("64k", huge)
    huge256, huge256ms = timed("256k", huge)
    chaos64, chaos64ms = timed("64k", huge_busy)
    chaos256, chaos256ms = timed("256k", huge_busy)
    fragmented64, fragmented64ms = timed("64k", fragmented)
    quiet_growth = (huge64["token_estimate"] / small64["token_estimate"] - 1) * 100
    assert quiet_growth <= 15
    assert huge64["token_estimate"] <= 6000 and chaos64["token_estimate"] <= 6000
    assert fragmented64["token_estimate"] <= 6000
    assert fragmented64["region_overflow"]["omitted_count"] > 0
    assert facts(huge64) <= facts(huge256)
    assert facts(chaos64) <= facts(chaos256)
    payload = {
        "schema": "smacx.world-benchmark.v1",
        "method": "deterministic semantic token estimator: canonical UTF-8 bytes / 4",
        "profiles": {
            "small_quiet_64k": {"tokens": small64["token_estimate"], "milliseconds": small64ms},
            "huge_quiet_64k": {"tokens": huge64["token_estimate"], "milliseconds": huge64ms},
            "huge_quiet_256k": {"tokens": huge256["token_estimate"], "milliseconds": huge256ms},
            "huge_chaotic_64k": {"tokens": chaos64["token_estimate"], "milliseconds": chaos64ms,
                                 "regions_omitted": chaos64["region_overflow"]["omitted_count"],
                                 "strategic_objects_truncated": chaos64["lod"]["strategic_objects_truncated"]},
            "huge_chaotic_256k": {"tokens": chaos256["token_estimate"], "milliseconds": chaos256ms,
                                   "regions_omitted": chaos256["region_overflow"]["omitted_count"],
                                   "strategic_objects_truncated": chaos256["lod"]["strategic_objects_truncated"]},
            "huge_fragmented_64k": {"tokens": fragmented64["token_estimate"],
                                     "milliseconds": fragmented64ms,
                                     "regions_omitted": fragmented64["region_overflow"]["omitted_count"]},
        },
        "huge_quiet_growth_percent": quiet_growth,
        "underlying_fact_equivalence": True,
        "tile_count": {"small": len(small["known_squares"]), "huge": len(huge["known_squares"])},
        "passed": True,
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
