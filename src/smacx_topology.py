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
    movement_points: float = 1
    movement_remaining: float | None = None
    fungus_cost: float = 3
    rough_cost: float = 2
    forest_cost: float = 2
    fungus_connects_to_road: bool = False
    ignores_rough_movement: bool = False
    road_cost: float = 1 / 3
    magtube_cost: float = 0.0
    ignores_zoc: bool = False
    can_embark: bool = False
    can_airdrop: bool = False
    airdrop_origin_ref: str | None = None
    air_safe_range: int | None = None
    air_full_safe_range: int | None = None
    air_origin_refuels: bool = False
    refuel_location_refs: frozenset[str] = field(default_factory=frozenset)
    mobile_refuel_location_refs: frozenset[str] = field(default_factory=frozenset)
    special_connections: tuple[tuple[str, str, float, str], ...] = ()
    airdrop_destination_refs: frozenset[str] = field(default_factory=frozenset)
    # A current owned unit may carry a native-enumerated legal target receipt.
    # Hypothetical/foreign profiles never claim that stronger authority.
    airdrop_targets_native_guarded: bool = False
    airdrop_targets_complete: bool = False
    abilities: frozenset[str] = field(default_factory=frozenset)
    known: bool = True
    # Perspective-derived ZOC/occupancy is exact only for the sovereign's own
    # units. Foreign/allied subjects receive a conservative minimum with an
    # explicit conditional marker instead of inheriting our blockers.
    constraint_mode: str = "sovereign_exact"

    def __post_init__(self) -> None:
        if self.triad not in {"land", "sea", "air"} or self.movement_points <= 0 \
                or self.constraint_mode not in {"sovereign_exact", "subject_unknown"}:
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
    blocking_contact_occupied: bool = False
    altitude: int | None = None

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
    eta_kind: str = "exact_known_state"
    latest_turns: int | None = None
    arrival_turn: int | None = None
    arrival_movement_spent: float | None = None
    arrival_movement_remaining: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "reachable": self.reachable,
            "path": list(self.path),
            "movement_cost": self.movement_cost,
            "turns": self.turns,
            "uncertainty": list(self.uncertainty),
            "dependency_hash": self.dependency_hash,
            "eta_kind": self.eta_kind,
            "latest_turns": self.latest_turns,
            "arrival_state": {
                "turn": self.arrival_turn,
                "movement_spent": self.arrival_movement_spent,
                "movement_remaining": self.arrival_movement_remaining,
            } if self.arrival_turn is not None else None,
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

    def potential_physical_connection(self, origin_ref: str, target_ref: str) -> bool:
        """Possibility graph: matching known terrain and unknown cells only.

        No hidden terrain is read. Known opposite-domain squares block the
        search, even when each endpoint touches a different pocket of fog.
        """
        origin, target = self.by_ref[origin_ref], self.by_ref[target_ref]
        if origin.terrain not in {"land", "ocean"} or origin.terrain != target.terrain:
            return False
        pending = [(origin.x, origin.y)]
        seen = set(pending)
        goal = (target.x, target.y)
        while pending:
            position = pending.pop()
            if position == goal:
                return True
            for neighbor in self.shape.neighbors(position).values():
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                square = self.by_position.get(neighbor)
                if square is None or square.terrain in {"unknown", origin.terrain}:
                    pending.append(neighbor)
        return False

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
        return not square.ocean

    def _cost(self, origin: KnownSquare, target: KnownSquare,
              profile: MobilityProfile) -> float:
        if profile.triad == "air":
            return 1.0
        if profile.triad == "land" and ("magtube" in origin.features or "base" in origin.features) \
                and ("magtube" in target.features or "base" in target.features):
            return profile.magtube_cost
        if profile.triad == "land" and ("road" in origin.features or "base" in origin.features
                or (profile.fungus_connects_to_road and "fungus" in origin.features)) \
                and ("road" in target.features or "base" in target.features):
            return profile.road_cost
        raw_dx = abs(origin.x - target.x)
        dx = min(raw_dx, self.shape.width - raw_dx) if self.shape.horizontal_wrap else raw_dx
        if profile.triad == "land" and "river" in origin.features and "river" in target.features \
                and dx == 1 and abs(origin.y - target.y) == 1:
            return profile.road_cost
        if profile.triad == "sea" and "fungus" in target.features:
            # Ocean fungus slows conventional naval units only on shelf
            # squares. Unknown remembered altitude is conservatively bounded
            # by the profile's shelf cost and reported as uncertainty below.
            return float(profile.fungus_cost) if target.altitude in {None, 2} else 1.0
        if "fungus" in target.features:
            return float(profile.fungus_cost)
        cost = 1.0
        if not profile.ignores_rough_movement \
                and (target.terrain in {"rocky", "mountain"} or "rocky" in target.features):
            cost += max(0.0, float(profile.rough_cost) - 1.0)
        if not profile.ignores_rough_movement and "forest" in target.features:
            cost += max(0.0, float(profile.forest_cost) - 1.0)
        return cost

    def _edges(self, current: KnownSquare, profile: MobilityProfile) -> list[tuple[KnownSquare, float, str]]:
        edges = [(neighbor, self._cost(current, neighbor, profile), "surface")
                 for neighbor in self.adjacent(current.location_ref).values()
                 if self._passable(neighbor, profile)
                 # A coastal base is a sea-passable endpoint, not a naval
                 # road across adjacent land-base tiles. Native sea movement
                 # must enter or leave it through an ocean edge.
                 and not (profile.triad == "sea" and not current.ocean
                          and not neighbor.ocean)]
        for origin, target, cost, kind in profile.special_connections:
            if origin != current.location_ref or target not in self.by_ref:
                continue
            edges.append((self.by_ref[target], max(0.0, float(cost)), str(kind)))
        if profile.can_airdrop and current.location_ref == profile.airdrop_origin_ref:
            for target in sorted(profile.airdrop_destination_refs):
                if target in self.by_ref:
                    edges.append((self.by_ref[target], 1.0, "airdrop"))
        return edges

    def _dependency_hash(self, profile: MobilityProfile) -> str:
        return content_hash({
            "shape": self.shape.__dict__, "profile": {
                **profile.__dict__, "abilities": sorted(profile.abilities),
                "refuel_location_refs": sorted(profile.refuel_location_refs),
                "mobile_refuel_location_refs": sorted(profile.mobile_refuel_location_refs),
                "airdrop_destination_refs": sorted(profile.airdrop_destination_refs),
                "special_connections": [list(item) for item in profile.special_connections],
            },
            "squares": [{"ref": item.location_ref, "x": item.x, "y": item.y,
                         "terrain": item.terrain, "features": sorted(item.features),
                         "zoc": item.hostile_zoc,
                         "blocking_contact_occupied": item.blocking_contact_occupied,
                         "altitude": item.altitude}
                        for item in sorted(self.by_ref.values(), key=lambda row: row.location_ref)],
        })

    def _surface_shortest_path(self, origin_ref: str, target_ref: str,
                               profile: MobilityProfile) -> tuple[str, ...] | None:
        """Shortest known-square path used by the fuel-constrained air planner."""
        queue = [origin_ref]
        parents: dict[str, str] = {}
        seen = {origin_ref}
        while queue:
            current_ref = queue.pop(0)
            if current_ref == target_ref:
                break
            for neighbor, _cost, edge_kind in self._edges(self.by_ref[current_ref], profile):
                if edge_kind != "surface" or neighbor.location_ref in seen:
                    continue
                seen.add(neighbor.location_ref)
                parents[neighbor.location_ref] = current_ref
                queue.append(neighbor.location_ref)
        if target_ref not in seen:
            return None
        path = [target_ref]
        while path[-1] != origin_ref:
            path.append(parents[path[-1]])
        return tuple(reversed(path))

    @staticmethod
    def _air_leg_turns(distance: int, movement_points: float,
                       first_turn_remaining: float) -> int:
        if distance <= 0:
            return 0
        remaining = max(0.0, first_turn_remaining)
        if distance <= remaining + 1e-9:
            return 1
        residual = distance - remaining
        return (1 if remaining > 1e-9 else 0) + int(
            (residual + movement_points - 1e-9) // movement_points
        )

    def _air_route(self, origin_ref: str, target_ref: str,
                   profile: MobilityProfile, dependency: str) -> RouteResult:
        """Fuel-safe route through stationary known refuelling points.

        Aircraft refuel only after ending a turn at a base/airbase/carrier. The
        planner therefore searches a small waypoint graph rather than treating
        refuelling squares as ordinary zero-cost pass-through tiles.
        """
        initial_fuel = profile.air_safe_range
        if initial_fuel is None:
            return self._route_surface(origin_ref, target_ref, profile, dependency)
        if profile.constraint_mode == "subject_unknown":
            # The perspective cannot enumerate a foreign faction's carriers,
            # bases, gates, or diplomatic access.  Absence of an observed
            # refuelling chain is therefore not proof that a threat is
            # impossible. Return the physically shortest conservative minimum
            # and make the unknown logistics explicit; never borrow sovereign
            # infrastructure or label the result exact.
            plausible = self._route_surface(origin_ref, target_ref, profile, dependency)
            if not plausible.reachable:
                return plausible
            return RouteResult(
                True, plausible.path, plausible.movement_cost, plausible.turns,
                tuple(dict.fromkeys((*plausible.uncertainty,
                    "Foreign refuelling and access infrastructure is not fully observed; "
                    "fuel feasibility is unknown and this route is only a conservative "
                    "minimum-arrival possibility."))),
                dependency, "conditional_minimum", None,
            )
        full_fuel = profile.air_full_safe_range or initial_fuel
        refuels = {ref for ref in profile.refuel_location_refs if ref in self.by_ref}
        waypoints = sorted(refuels | {origin_ref, target_ref})
        paths: dict[tuple[str, str], tuple[str, ...]] = {}
        for left in waypoints:
            for right in waypoints:
                if left == right:
                    continue
                path = self._surface_shortest_path(left, right, profile)
                if path is not None:
                    paths[(left, right)] = path
        return_distance: dict[str, int | None] = {}
        for ref in waypoints:
            candidates = [len(path) - 1 for (left, right), path in paths.items()
                          if left == ref and right in refuels]
            return_distance[ref] = min(candidates) if candidates else None

        initial_remaining = profile.movement_points if profile.movement_remaining is None \
            else min(max(float(profile.movement_remaining), 0.0), float(profile.movement_points))
        # State records arrival turn, cumulative distance, waypoint and the
        # first-turn movement available on the next leg. A refuel stop always
        # resumes on the following game turn with a full movement/fuel clock.
        queue: list[tuple[int, int, str]] = [(0, 0, origin_ref)]
        best: dict[str, tuple[int, int]] = {origin_ref: (0, 0)}
        parents: dict[str, str] = {}
        while queue:
            arrived_turn, distance_so_far, current = heappop(queue)
            if (arrived_turn, distance_so_far) != best.get(current):
                continue
            if current == target_ref:
                break
            fuel = initial_fuel if current == origin_ref else full_fuel
            movement = initial_remaining if current == origin_ref else profile.movement_points
            for candidate in sorted(refuels | {target_ref}):
                if candidate == current or (current, candidate) not in paths:
                    continue
                leg = paths[(current, candidate)]
                distance = len(leg) - 1
                if distance > fuel:
                    continue
                if candidate == target_ref and candidate not in refuels:
                    recovery = return_distance.get(candidate)
                    if recovery is None or distance + recovery > fuel:
                        continue
                leg_turns = self._air_leg_turns(distance, profile.movement_points, movement)
                # Leaving a refuelling waypoint requires the next game turn;
                # origin is already in the current actionable turn.
                candidate_turn = arrived_turn + leg_turns
                score = (candidate_turn, distance_so_far + distance)
                if score < best.get(candidate, (10**9, 10**9)):
                    best[candidate] = score
                    parents[candidate] = current
                    heappush(queue, (score[0], score[1], candidate))
        if target_ref not in best:
            return RouteResult(
                False, (), None, None,
                ("No fuel-safe route and recovery path exists through current known refuelling points.",),
                dependency,
            )
        waypoint_path = [target_ref]
        while waypoint_path[-1] != origin_ref:
            waypoint_path.append(parents[waypoint_path[-1]])
        waypoint_path.reverse()
        path: list[str] = [origin_ref]
        for left, right in zip(waypoint_path, waypoint_path[1:]):
            path.extend(paths[(left, right)][1:])
        uncertainty = []
        if any(ref in profile.mobile_refuel_location_refs for ref in waypoint_path):
            uncertainty.append(
                "Fuel safety depends on a currently observed carrier remaining at its cited location; re-query before assignment."
            )
        if any(not self.by_ref[ref].current for ref in path):
            uncertainty.append("Route crosses stale remembered geography; revalidate before a consequential action.")
        return RouteResult(
            True, tuple(path), float(best[target_ref][1]), best[target_ref][0],
            tuple(uncertainty), dependency,
            "conditional_known_state" if uncertainty else "exact_known_state",
            best[target_ref][0],
        )

    def route(self, origin_ref: str, target_ref: str, profile: MobilityProfile) -> RouteResult:
        origin = self.by_ref.get(origin_ref)
        target = self.by_ref.get(target_ref)
        if origin is None or target is None:
            raise WorldContractError("unknown_route_endpoint")
        if not profile.known:
            return RouteResult(
                False, (), None, None,
                ("Observed mobility is unknown; no fictitious land/1 ETA was calculated.",),
                content_hash({"origin": origin_ref, "target": target_ref,
                              "profile": profile.profile_ref, "known": False}),
            )
        dependency = self._dependency_hash(profile)
        if profile.triad == "air":
            return self._air_route(origin_ref, target_ref, profile, dependency)
        return self._route_surface(origin_ref, target_ref, profile, dependency)

    def _route_surface(self, origin_ref: str, target_ref: str,
                       profile: MobilityProfile, dependency: str) -> RouteResult:
        state = self._surface_arrival_state(origin_ref, profile)
        labels = state["labels"]
        raw_costs = state["raw_costs"]
        parents = state["parents"]
        parent_kinds = state["parent_kinds"]
        stochastic_paths = state["stochastic_paths"]
        if target_ref not in labels:
            uncertainty = [
                "No route exists in the currently known world; unknown geography may change this."
            ]
            if profile.can_airdrop and not profile.airdrop_targets_complete:
                uncertainty.append(
                    "Airdrop target coverage is incomplete; absence from the available target set is not proof of native illegality."
                )
            return RouteResult(False, (), None, None, tuple(uncertainty), dependency)
        path = [target_ref]
        while path[-1] != origin_ref:
            path.append(parents[path[-1]])
        path.reverse()
        movement_cost = raw_costs[target_ref]
        arrival_turn, arrival_spent = labels[target_ref]
        turns = arrival_turn
        uncertainty = self._arrival_uncertainty(path, parent_kinds, stochastic_paths,
                                                target_ref, profile)
        stochastic = stochastic_paths.get(target_ref, False)
        airdrop_used = any(parent_kinds.get(ref) == "airdrop" for ref in path[1:])
        conditional = profile.constraint_mode == "subject_unknown" \
            or (airdrop_used and not profile.airdrop_targets_native_guarded)
        return RouteResult(
            True, tuple(path), movement_cost, turns, tuple(uncertainty), dependency,
            "stochastic_earliest" if stochastic else
            ("conditional_minimum" if profile.constraint_mode == "subject_unknown"
             else "conditional_known_state" if conditional else "exact_known_state"),
            None if stochastic or conditional else turns,
            int(arrival_turn), float(arrival_spent),
            max(0.0, float(profile.movement_points) - float(arrival_spent)),
        )

    def _surface_arrival_state(
        self, origin_ref: str, profile: MobilityProfile, *, max_turns: int | None = None,
    ) -> dict[str, Any]:
        """One label-setting engine for route, reachability, and envelopes."""
        if origin_ref not in self.by_ref:
            raise WorldContractError("invalid_reachability_origin")
        # Label-setting state machine. A label is (turn in which the unit is
        # moving, movement spent in that turn). Native over-cost entry may
        # exhaust/overspend a non-empty turn (and can be stochastic for land),
        # while a unit with no movement left waits. Tubes remain zero-cost.
        # This avoids scalar ceil errors at terrain and turn boundaries.
        initial_remaining = profile.movement_points if profile.movement_remaining is None \
            else min(max(float(profile.movement_remaining), 0.0), float(profile.movement_points))
        initial_spent = float(profile.movement_points) - initial_remaining
        queue: list[tuple[int, float, str]] = [(0, initial_spent, origin_ref)]
        labels = {origin_ref: (0, initial_spent)}
        raw_costs = {origin_ref: 0.0}
        parents: dict[str, str] = {}
        parent_kinds: dict[str, str] = {}
        stochastic_paths = {origin_ref: False}
        uncertainty_flags: dict[str, frozenset[str]] = {
            origin_ref: frozenset({"stale"}) if not self.by_ref[origin_ref].current
            else frozenset()
        }
        while queue:
            turn_no, spent, current_ref = heappop(queue)
            if (turn_no, spent) != labels[current_ref]:
                continue
            current = self.by_ref[current_ref]
            for neighbor, step_cost, edge_kind in self._edges(current, profile):
                # A currently observed non-allied combat unit blocks transit.
                # It may still be the explicit route target (for an attack),
                # and a hostile unit may route outward from its own origin for
                # mechanical threat/response calculations.
                exact_constraints = profile.constraint_mode == "sovereign_exact"
                occupied_terminal = exact_constraints and profile.triad != "air" \
                    and neighbor.blocking_contact_occupied
                if exact_constraints and current.hostile_zoc and neighbor.hostile_zoc \
                        and not profile.ignores_zoc:
                    continue
                effective = float(step_cost)
                next_turn = max(1, turn_no)
                next_spent = spent
                stochastic_entry = False
                if effective > 0:
                    remaining = max(0.0, profile.movement_points - next_spent)
                    if remaining <= 1e-9:
                        next_turn += 1
                        remaining = float(profile.movement_points)
                        next_spent = 0.0
                    if effective <= remaining + 1e-9:
                        next_spent += effective
                    else:
                        # Native veh_action permits an over-cost move whenever
                        # any movement remains.  For land units it performs a
                        # random failure check when less than one ordinary MP
                        # remains, and also for conventional fungus entry.
                        # Non-fungus land entry with >=1 MP, plus sea/air
                        # overspend, is deterministic and exhausts the turn.
                        stochastic_entry = profile.triad == "land" and (
                            remaining < 1.0 - 1e-9
                            or "fungus" in neighbor.features
                        )
                        next_spent = float(profile.movement_points)
                if edge_kind in {"airdrop", "psi_gate"}:
                    # Both native actions require an otherwise-unmoved unit.
                    # If a route reaches the source after moving, the earliest
                    # legal use is the next game turn.
                    if spent > 1e-9:
                        next_turn = max(1, turn_no) + 1
                    next_spent = float(profile.movement_points)
                candidate = (next_turn, next_spent)
                if max_turns is not None and next_turn > max_turns:
                    continue
                if candidate < labels.get(neighbor.location_ref, (10**9, inf)):
                    labels[neighbor.location_ref] = candidate
                    raw_costs[neighbor.location_ref] = raw_costs[current_ref] + step_cost
                    parents[neighbor.location_ref] = current_ref
                    parent_kinds[neighbor.location_ref] = edge_kind
                    stochastic_paths[neighbor.location_ref] = (
                        stochastic_paths.get(current_ref, False) or stochastic_entry
                    )
                    flags = set(uncertainty_flags.get(current_ref, ()))
                    if edge_kind == "airdrop":
                        flags.add("airdrop")
                    if not neighbor.current:
                        flags.add("stale")
                    if profile.triad == "sea" and "fungus" in neighbor.features \
                            and neighbor.altitude is None:
                        flags.add("unknown_sea_fungus_depth")
                    if stochastic_entry:
                        flags.add("stochastic")
                    uncertainty_flags[neighbor.location_ref] = frozenset(flags)
                    # A known blocking combat unit can be attacked as a final
                    # destination, but the route cannot pass through it.
                    if not occupied_terminal:
                        heappush(queue, (next_turn, next_spent, neighbor.location_ref))
        return {
            "labels": labels, "raw_costs": raw_costs, "parents": parents,
            "parent_kinds": parent_kinds, "stochastic_paths": stochastic_paths,
            "uncertainty_flags": uncertainty_flags,
        }

    @staticmethod
    def _flag_uncertainty(flags: Iterable[str], profile: MobilityProfile) -> list[str]:
        values = set(flags)
        uncertainty: list[str] = []
        if "airdrop" in values:
            if profile.airdrop_targets_native_guarded:
                uncertainty.append(
                    "Airdrop destination was present in the current native rule-validated target receipt; execution still revalidates freshness."
                )
            else:
                uncertainty.append(
                    "Airdrop is a perspective-safe possibility only; obtain a fresh native-guarded destination choice before execution."
                )
            if not profile.airdrop_targets_complete:
                uncertainty.append(
                    "Airdrop target coverage is incomplete; absence from this set is not proof of illegality."
                )
        if "stale" in values:
            uncertainty.append(
                "Route crosses stale remembered geography; revalidate before a consequential action."
            )
        if "unknown_sea_fungus_depth" in values:
            uncertainty.append(
                "A remembered sea-fungus square has unknown shelf depth; ETA uses the slower shelf bound."
            )
        if "stochastic" in values:
            uncertainty.append(
                "This route includes a fungus entry whose native random movement check can fail; "
                "eta_turns is the earliest successful arrival and has no finite guaranteed upper bound."
            )
        if profile.constraint_mode == "subject_unknown":
            uncertainty.append(
                "Subject-relative ZOC, occupancy, and foreign diplomatic access are not fully known; "
                "this is a conservative minimum, not an exact arrival guarantee."
            )
        return uncertainty

    def _arrival_uncertainty(
        self, path: list[str], parent_kinds: Mapping[str, str],
        stochastic_paths: Mapping[str, bool], target_ref: str,
        profile: MobilityProfile,
    ) -> list[str]:
        uncertainty: list[str] = []
        if any(parent_kinds.get(ref) == "airdrop" for ref in path[1:]):
            if profile.airdrop_targets_native_guarded:
                uncertainty.append(
                    "Airdrop destination was present in the current native rule-validated target receipt; execution still revalidates freshness."
                )
            else:
                uncertainty.append(
                    "Airdrop is a perspective-safe possibility only; obtain a fresh native-guarded destination choice before execution."
                )
            if not profile.airdrop_targets_complete:
                uncertainty.append(
                    "Airdrop target coverage is incomplete; absence from this set is not proof of illegality."
                )
        if any(not self.by_ref[ref].current for ref in path):
            uncertainty.append("Route crosses stale remembered geography; revalidate before a consequential action.")
        if profile.triad == "sea" and any(
            "fungus" in self.by_ref[ref].features and self.by_ref[ref].altitude is None
            for ref in path[1:]
        ):
            uncertainty.append(
                "A remembered sea-fungus square has unknown shelf depth; ETA uses the slower shelf bound."
            )
        stochastic = stochastic_paths.get(target_ref, False)
        if stochastic:
            uncertainty.append(
                "This route includes a fungus entry whose native random movement check can fail; "
                "eta_turns is the earliest successful arrival and has no finite guaranteed upper bound."
            )
        if profile.constraint_mode == "subject_unknown":
            uncertainty.append(
                "Subject-relative ZOC, occupancy, and foreign diplomatic access are not fully known; this is a conservative minimum, not an exact arrival guarantee."
            )
        return uncertainty

    def arrival_map(self, origin_ref: str, profile: MobilityProfile,
                    *, max_turns: int | None) -> dict[str, dict[str, Any]]:
        """Earliest stateful arrivals using the same transitions as route()."""
        if max_turns is not None and max_turns < 0:
            raise WorldContractError("invalid_reachability_turn_bound")
        if not profile.known:
            return {}
        if profile.triad == "air" and profile.air_safe_range is not None:
            rows: dict[str, dict[str, Any]] = {}
            for ref in sorted(self.by_ref):
                route = self.route(origin_ref, ref, profile)
                if route.reachable and route.turns is not None \
                        and (max_turns is None or route.turns <= max_turns):
                    rows[ref] = route.as_dict()
            return rows
        dependency = self._dependency_hash(profile)
        state = self._surface_arrival_state(origin_ref, profile, max_turns=max_turns)
        rows = {}
        for ref, label in state["labels"].items():
            stochastic = bool(state["stochastic_paths"].get(ref, False))
            uncertainty = self._flag_uncertainty(
                state["uncertainty_flags"].get(ref, ()), profile,
            )
            rows[ref] = RouteResult(
                # Arrival maps deliberately omit every path.  Reconstructing a
                # path per square is quadratic on Huge maps; callers request a
                # single authoritative route only for selected endpoints.
                True, (), float(state["raw_costs"][ref]), int(label[0]),
                tuple(uncertainty), dependency,
                "stochastic_earliest" if stochastic else
                ("conditional_minimum" if profile.constraint_mode == "subject_unknown"
                 else "conditional_known_state"
                 if "airdrop" in state["uncertainty_flags"].get(ref, ())
                 and not profile.airdrop_targets_native_guarded
                 else "exact_known_state"),
                None if stochastic or profile.constraint_mode == "subject_unknown"
                or ("airdrop" in state["uncertainty_flags"].get(ref, ())
                    and not profile.airdrop_targets_native_guarded)
                else int(label[0]),
                int(label[0]), float(label[1]),
                max(0.0, float(profile.movement_points) - float(label[1])),
            ).as_dict()
        return rows

    def reachable_costs(self, origin_ref: str, profile: MobilityProfile,
                        *, max_cost: float) -> dict[str, float]:
        """Compatibility wrapper over the stateful arrival engine."""
        if origin_ref not in self.by_ref or max_cost < 0:
            raise WorldContractError("invalid_reachability_origin")
        turns = int(max_cost // profile.movement_points)
        if max_cost % profile.movement_points > 1e-9:
            turns += 1
        return {
            ref: float(item["movement_cost"])
            for ref, item in self.arrival_map(
                origin_ref, profile, max_turns=max(0, turns),
            ).items()
        }

    def connected_components(self, profile: MobilityProfile) -> list[set[str]]:
        unseen = {ref for ref, square in self.by_ref.items()
                  if self._passable(square, profile)
                  and not square.blocking_contact_occupied}
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
                    if ref in unseen and self._passable(neighbor, profile) \
                            and not neighbor.blocking_contact_occupied:
                        unseen.remove(ref)
                        component.add(ref)
                        frontier.append(ref)
            components.append(component)
        return components
