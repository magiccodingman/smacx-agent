"""Optional semantic rendering from the same perspective-safe world projection."""

from __future__ import annotations

from html import escape
from typing import Any, Mapping

from smacx_topology import PerspectiveTopology


def render_svg(topology: PerspectiveTopology, objects: Mapping[str, Mapping[str, Any]],
               *, max_cells: int = 1000) -> dict[str, Any]:
    squares = sorted(topology.by_ref.values(), key=lambda item: (item.y, item.x))[:max_cells]
    overlays: dict[str, list[Mapping[str, Any]]] = {}
    for item in objects.values():
        if item.get("location_ref"):
            overlays.setdefault(str(item["location_ref"]), []).append(item)
    cells: list[str] = []
    for square in squares:
        fill = "#315b7d" if square.ocean else "#536f39"
        opacity = "1" if square.current else ".45"
        title = [square.location_ref, square.terrain,
                 "current" if square.current else "stale"]
        kinds = sorted(str(item.get("kind")) for item in overlays.get(square.location_ref, ()))
        if kinds:
            title.append(",".join(kinds))
        cells.append(
            f'<g><title>{escape(" · ".join(title))}</title>'
            f'<rect x="{square.x * 8}" y="{square.y * 8}" width="7" height="7" '
            f'fill="{fill}" opacity="{opacity}"/></g>'
        )
    return {
        "kind": "semantic_svg", "authoritative": False,
        "derived_from_perspective_projection": True,
        "coverage": {"rendered_cells": len(squares),
                     "known_cells": len(topology.by_ref),
                     "truncated": len(squares) < len(topology.by_ref)},
        "svg": '<svg xmlns="http://www.w3.org/2000/svg" role="img" '
               'aria-label="Fair-play semantic map">' + "".join(cells) + "</svg>",
    }
