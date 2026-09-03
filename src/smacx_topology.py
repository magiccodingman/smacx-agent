"""Correct perspective-known SMAC isometric-square topology and routing."""

from __future__ import annotations

from dataclasses import dataclass, field
from heapq import heappop, heappush
from math import inf
from typing import Any, Iterable, Mapping

from smacx_world_types import WorldContractError, content_hash


DIRECTION_OFFSETS: dict[str, tuple[int, int]] = {
    "N": (0, -2), "NE": (1, -1), "E": (2, 0), "SE": (1, 1),
    "S": (0, 2), "SW": (-1, 1), "W": (-2, 0), "NW": (-1, -1),
}


@dataclass(frozen=True)
class MapShape:
    width: int
    height: int
    horizontal_wrap: bool = True

    def __post_init__(self) -> None:
        if self.width < 2 or self.height < 1 or self.width % 2:
            raise WorldContractError("invalid_map_shape")

    def normalize(self, position: tuple[int, int]) -> tuple[int, int] | None:
        x, y = position
        if self.horizontal_wrap:
            x %= self.width
        if x < 0 or x >= self.width or y < 0 or y >= self.height or (x + y) % 2:
            return None
        return x, y

    def neighbor(self, position: tuple[int, int], direction: str) -> tuple[int, int] | None:
        if direction not in DIRECTION_OFFSETS:
            raise WorldContractError("invalid_bearing")
        dx, dy = DIRECTION_OFFSETS[direction]
        return self.normalize((position[0] + dx, position[1] + dy))

    def neighbors(self, position: tuple[int, int]) -> dict[str, tuple[int, int]]:
        result: dict[str, tuple[int, int]] = {}
        for direction in DIRECTION_OFFSETS:
            neighbor = self.neighbor(position, direction)
            if neighbor is not None:
                result[direction] = neighbor
        return result

    @staticmethod
    def _logical(position: tuple[int, int]) -> tuple[int, int]:
        x, y = position
        return (x + y) // 2, (x - y) // 2

    def distance(self, origin: tuple[int, int], target: tuple[int, int]) -> int:
        origin = self.normalize(origin) or (_ for _ in ()).throw(
            WorldContractError("invalid_origin"))
        target = self.normalize(target) or (_ for _ in ()).throw(
            WorldContractError("invalid_target"))
        shifts = (0, -self.width, self.width) if self.horizontal_wrap else (0,)
        oa, ob = self._logical(origin)
        return min(max(abs(self._logical((target[0] + shift, target[1]))[0] - oa),
                       abs(self._logical((target[0] + shift, target[1]))[1] - ob))
                   for shift in shifts)

    def bearing(self, origin: tuple[int, int], target: tuple[int, int]) -> str:
        if origin == target:
            return "HERE"
        best = min(
            ((self.distance(candidate, target), direction)
             for direction, candidate in self.neighbors(origin).items()),
            key=lambda item: (item[0], tuple(DIRECTION_OFFSETS).index(item[1])),
        )
        return best[1]


@dataclass(frozen=True)
class MobilityProfile:
    profile_ref: str
    triad: str
    movement_points: int = 1
    fungus_cost: int = 2
    rough_cost: int = 2
    road_cost: float = 1 / 3
    magtube_cost: float = 0.0
    ignores_zoc: bool = False
    can_embark: bool = False
    can_airdrop: bool = False
    max_air_turns: int | None = None
    refuel_location_refs: frozenset[str] = field(default_factory=frozenset)
    special_connections: tuple[tuple[str, str, float, str], ...] = ()
    airdrop_destination_refs: frozenset[str] = field(default_factory=frozenset)
    abilities: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.triad not in {"land", "sea", "air"} or self.movement_points <= 0:
            raise WorldContractError("invalid_mobility_profile")


@dataclass(frozen=True)
class KnownSquare:
    location_ref: str
    x: int
    y: int
    terrain: str
    current: bool = True
    features: frozenset[str] = field(default_factory=frozenset)
    owner_ref: str | None = None
    hostile_zoc: bool = False

    @property
    def ocean(self) -> bool:
        return self.terrain == "ocean"


@dataclass(frozen=True)
class RouteResult:
    reachable: bool
    path: tuple[str, ...]
    movement_cost: float | None
    turns: int | None
    uncertainty: tuple[str, ...]
    dependency_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "reachable": self.reachable,
            "path": list(self.path),
            "movement_cost": self.movement_cost,
            "turns": self.turns,
            "uncertainty": list(self.uncertainty),
            "dependency_hash": self.dependency_hash,
        }


class PerspectiveTopology:
    """A graph containing known squares only; unknown terrain creates uncertainty."""

    def __init__(self, shape: MapShape, squares: Iterable[KnownSquare]) -> None:
        self.shape = shape
        self.by_ref = {square.location_ref: square for square in squares}
        self.by_position = {(square.x, square.y): square for square in squares}
        if len(self.by_ref) != len(self.by_position):
            raise WorldContractError("duplicate_known_square")
        for square in self.by_ref.values():
            if shape.normalize((square.x, square.y)) != (square.x, square.y):
                raise WorldContractError("known_square_outside_map")

    def adjacent(self, location_ref: str) -> dict[str, KnownSquare]:
        square = self.by_ref.get(location_ref)
        if square is None:
            raise WorldContractError("unknown_location_ref")
        return {direction: self.by_position[position]
                for direction, position in self.shape.neighbors((square.x, square.y)).items()
                if position in self.by_position}

    @staticmethod
    def _passable(square: KnownSquare, profile: MobilityProfile) -> bool:
        if square.terrain == "unknown":
            return False
        if profile.triad == "air":
            return True
        if profile.triad == "sea":
            return square.ocean or "base" in square.features
        # Combined land+transport timing is calculated by the logistics layer;
        # a bare land mobility profile must never route itself across an ocean.
        return not square.ocean or "base" in square.features

    @staticmethod
    def _cost(origin: KnownSquare, target: KnownSquare, profile: MobilityProfile) -> float:
        if profile.triad == "air":
            return 1.0
        if "magtube" in origin.features and "magtube" in target.features:
            return profile.magtube_cost
        if "road" in origin.features and "road" in target.features:
            return profile.road_cost
        if "fungus" in target.features:
            return float(profile.fungus_cost)
        if target.terrain in {"rocky", "mountain"}:
            return float(profile.rough_cost)
        return 1.0

    def _edges(self, current: KnownSquare, profile: MobilityProfile) -> list[tuple[KnownSquare, float, str]]:
        edges = [(neighbor, self._cost(current, neighbor, profile), "surface")
                 for neighbor in self.adjacent(current.location_ref).values()
                 if self._passable(neighbor, profile)]
        for origin, target, cost, kind in profile.special_connections:
            if origin != current.location_ref or target not in self.by_ref:
                continue
            edges.append((self.by_ref[target], max(0.0, float(cost)), str(kind)))
        if profile.can_airdrop and current.location_ref not in profile.airdrop_destination_refs:
            for target in sorted(profile.airdrop_destination_refs):
                if target in self.by_ref:
                    edges.append((self.by_ref[target], 1.0, "airdrop"))
        return edges

    def route(self, origin_ref: str, target_ref: str, profile: MobilityProfile) -> RouteResult:
        origin = self.by_ref.get(origin_ref)
        target = self.by_ref.get(target_ref)
        if origin is None or target is None:
            raise WorldContractError("unknown_route_endpoint")
        queue: list[tuple[float, str]] = [(0.0, origin_ref)]
        costs = {origin_ref: 0.0}
        parents: dict[str, str] = {}
        while queue:
            cost, current_ref = heappop(queue)
            if cost != costs[current_ref]:
                continue
            if current_ref == target_ref:
                break
            current = self.by_ref[current_ref]
            for neighbor, step_cost, _edge_kind in self._edges(current, profile):
                if neighbor.hostile_zoc and not profile.ignores_zoc and current_ref != origin_ref:
                    continue
                candidate = cost + step_cost
                if candidate < costs.get(neighbor.location_ref, inf):
                    costs[neighbor.location_ref] = candidate
                    parents[neighbor.location_ref] = current_ref
                    heappush(queue, (candidate, neighbor.location_ref))
        dependency = content_hash({
            "shape": self.shape.__dict__, "profile": {
                **profile.__dict__, "abilities": sorted(profile.abilities),
                "refuel_location_refs": sorted(profile.refuel_location_refs),
                "airdrop_destination_refs": sorted(profile.airdrop_destination_refs),
                "special_connections": [list(item) for item in profile.special_connections],
            },
            "squares": [{"ref": item.location_ref, "x": item.x, "y": item.y,
                         "terrain": item.terrain, "features": sorted(item.features),
                         "zoc": item.hostile_zoc}
                        for item in sorted(self.by_ref.values(), key=lambda row: row.location_ref)],
        })
        if target_ref not in costs:
            return RouteResult(False, (), None, None,
                               ("No route exists in the currently known world; unknown geography may change this.",),
                               dependency)
        path = [target_ref]
        while path[-1] != origin_ref:
            path.append(parents[path[-1]])
        path.reverse()
        movement_cost = costs[target_ref]
        turns = max(0, int((movement_cost + profile.movement_points - 1)
                           // profile.movement_points))
        uncertainty: list[str] = []
        if any(not self.by_ref[ref].current for ref in path):
            uncertainty.append("Route crosses stale remembered geography; revalidate before a consequential action.")
        if profile.triad == "air" and profile.max_air_turns is not None:
            # An air leg must reach a known recovery location within its fuel
            # envelope. This is a conservative known-world planner, not native
            # hidden-state pathfinding.
            since_refuel = 0
            for ref in path[1:]:
                since_refuel += 1
                if ref in profile.refuel_location_refs:
                    since_refuel = 0
                if since_refuel > profile.max_air_turns * profile.movement_points:
                    return RouteResult(False, (), None, None,
                                       ("No fuel-safe route exists through known recovery locations.",),
                                       dependency)
        return RouteResult(True, tuple(path), movement_cost, turns, tuple(uncertainty), dependency)

    def reachable_costs(self, origin_ref: str, profile: MobilityProfile,
                        *, max_cost: float) -> dict[str, float]:
        """Return exact minimum known-world movement costs within a bound."""
        if origin_ref not in self.by_ref or max_cost < 0:
            raise WorldContractError("invalid_reachability_origin")
        costs = {origin_ref: 0.0}
        queue: list[tuple[float, str]] = [(0.0, origin_ref)]
        while queue:
            cost, current_ref = heappop(queue)
            if cost != costs[current_ref]:
                continue
            current = self.by_ref[current_ref]
            for neighbor, step_cost, _kind in self._edges(current, profile):
                if neighbor.hostile_zoc and not profile.ignores_zoc and current_ref != origin_ref:
                    continue
                candidate = cost + step_cost
                if candidate <= max_cost and candidate < costs.get(neighbor.location_ref, inf):
                    costs[neighbor.location_ref] = candidate
                    heappush(queue, (candidate, neighbor.location_ref))
        return costs

    def connected_components(self, profile: MobilityProfile) -> list[set[str]]:
        unseen = {ref for ref, square in self.by_ref.items() if self._passable(square, profile)}
        components: list[set[str]] = []
        while unseen:
            seed = min(unseen)
            component = {seed}
            frontier = [seed]
            unseen.remove(seed)
            while frontier:
                current = frontier.pop()
                for neighbor in self.adjacent(current).values():
                    ref = neighbor.location_ref
                    if ref in unseen and self._passable(neighbor, profile):
                        unseen.remove(ref)
                        component.add(ref)
                        frontier.append(ref)
            components.append(component)
        return components
