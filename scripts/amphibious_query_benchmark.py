#!/usr/bin/env python3
"""Bounded Huge-map transport-planning regression, separate from collection."""

from __future__ import annotations

import json
import time

from smacx_mechanics import transport_route
from smacx_topology import KnownSquare, MapShape, PerspectiveTopology


def field(value):
    return {"value": value, "epistemic_status": "current", "source": "owned_state"}


def unit(ref: str, location: str, triad: str, roles: dict, **extra):
    values = {"owner_ref": "faction-1", "triad": triad,
              "movement_points": 6 if triad == "sea" else 3,
              "movement_scale": 3, "moves_remaining": 6 if triad == "sea" else 3,
              "roles": roles, **extra}
    return {"object_ref": ref, "kind": "own_unit", "status": "active",
            "location_ref": location,
            "fields": {key: field(value) for key, value in values.items()}}


def main() -> int:
    width, height = 128, 64
    squares = []
    for y in range(height):
        for x in range(y & 1, width, 2):
            ocean = 40 <= x < 88
            squares.append(KnownSquare(
                f"location-{(x + width * y) // 2}", x, y,
                "ocean" if ocean else "land",
            ))
    topology = PerspectiveTopology(MapShape(width, height, False), squares)
    origin = "location-0"
    transport_location = f"location-{40 // 2}"
    target = f"location-{126 // 2}"
    objects = {
        "passenger": unit("passenger", origin, "land", {"combat": True}),
        "transport": unit(
            "transport", transport_location, "sea", {"transport": True},
            cargo={"capacity": 4, "loaded": 0},
        ),
    }
    started = time.perf_counter()
    route = transport_route(topology, objects, "passenger", target)
    elapsed_ms = (time.perf_counter() - started) * 1000
    if route is None or elapsed_ms > 5_000:
        raise AssertionError({"route": route, "elapsed_ms": elapsed_ms})
    print(json.dumps({"event": "pass", "payload": {
        "known_squares": len(squares), "elapsed_ms": round(elapsed_ms, 3),
        "bounded_embark_frontier": 4, "bounded_landing_frontier": 8,
        "route_found": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
