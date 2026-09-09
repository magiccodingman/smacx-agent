"""Bounded geographic evidence; never strategic ranking or hidden-map inference."""
from collections import Counter


def geographic_context(topology, refs, objects):
    refs = set(refs) & set(topology.by_ref)
    squares = [topology.by_ref[ref] for ref in sorted(refs)]
    if not squares:
        return {"known_location_count": 0}
    boundary = set()
    for square in squares:
        known = {n.location_ref for n in topology.adjacent(square.location_ref).values()
                 if n.terrain != "unknown"}
        if len(known) < len(topology.shape.neighbors((square.x, square.y))):
            boundary.add(square.location_ref)
    features = Counter(f for s in squares for f in s.features)
    xs = sorted({s.x for s in squares})
    if topology.shape.horizontal_wrap:
        gaps = [(xs[(i+1) % len(xs)] - x) % topology.shape.width for i, x in enumerate(xs)]
        horizontal_span = 0 if len(xs) == 1 else topology.shape.width-max(gaps)
    else:
        horizontal_span = xs[-1]-xs[0]
    anchors = []
    bases = [o for o in objects.values() if o.get("kind") == "base" and o.get("status", "active") == "active"]
    bases.sort(key=lambda o: (o.get("location_ref") not in refs, str(o.get("object_ref"))))
    for obj in bases[:8]:
        origin = topology.by_ref.get(obj.get("location_ref"))
        if origin is None:
            continue
        nearest = min(squares, key=lambda s: (topology.shape.distance((origin.x, origin.y), (s.x, s.y)), s.location_ref))
        anchors.append({"base_ref": obj["object_ref"],
                        "base_owner": obj.get("fields", {}).get("owner_ref", {}),
                        "base_inside_area": origin.location_ref in refs,
                        "nearest_area_location_ref": nearest.location_ref,
                        "geometric_range": topology.shape.distance((origin.x, origin.y), (nearest.x, nearest.y)),
                        "bearing_to_nearest_area": topology.shape.bearing((origin.x, origin.y), (nearest.x, nearest.y))})
    anchors.sort(key=lambda a: (a["geometric_range"], a["base_ref"]))
    return {"known_location_count": len(refs),
            "currently_visible_location_count": sum(bool(s.current) for s in squares),
            "not_currently_visible_location_count": sum(not s.current for s in squares),
            "unknown_boundary_location_count": len(boundary),
            "unobserved_extent": "unknown" if boundary else "no_missing_adjacent_tiles_in_this_scope",
            "north_south_coordinate_span": max(s.y for s in squares) - min(s.y for s in squares),
            "horizontal_wrap": topology.shape.horizontal_wrap,
            "minimum_horizontal_coordinate_span": horizontal_span,
            "extent_units": "native coordinate spans between known tile centers, not tile counts or travel distances",
            "feature_counts": dict(sorted(features.items())),
            "feature_evidence": "Known-square features may be remembered; use location field envelopes before asserting current improvements or rewards.",
            "nearby_base_relations": anchors[:8], "base_relations_omitted": max(0, len(bases)-8),
            "base_selection": "inside area first, then stable reference; not globally nearest bases",
            "meaning": "Known area only. Boundary size does not estimate unseen land, rewards or faction probability. Bearings/ranges are geometric, not travel. River counts do not establish a connected river corridor. Base locations and owners retain observation freshness in the world record."}


def connector_evidence(topology, profile, *, origin_ref="", objects=None, limit=24):
    """Iterative articulation search; bounded separation details for relevant cuts.

    Surface terrain graph only: occupancy, ZOC and special transit require routes.
    Entire component fog qualifies each cut, not just the neck's immediate fog.
    """
    objects = objects or {}
    graph = {ref: set() for ref, s in topology.by_ref.items()
             if s.terrain != "unknown" and topology._passable(s, profile)}
    for ref in graph:
        graph[ref] = {n.location_ref for n in topology.adjacent(ref).values() if n.location_ref in graph}
    disc, low, parent, children, components, cuts = {}, {}, {}, {}, {}, set()
    for root in sorted(graph):
        if root in disc:
            continue
        component = set()
        disc[root] = low[root] = len(disc)
        parent[root], children[root] = None, 0
        stack = [(root, iter(sorted(graph[root])))]
        while stack:
            node, edges = stack[-1]
            component.add(node)
            neighbor = next(edges, None)
            if neighbor is None:
                stack.pop()
                p = parent[node]
                if p is None:
                    if children[node] > 1: cuts.add(node)
                else:
                    low[p] = min(low[p], low[node])
                    if parent[p] is not None and low[node] >= disc[p]: cuts.add(p)
                continue
            if neighbor not in disc:
                parent[neighbor] = node
                children[node] += 1
                children[neighbor] = 0
                disc[neighbor] = low[neighbor] = len(disc)
                stack.append((neighbor, iter(sorted(graph[neighbor]))))
            elif neighbor != parent[node]:
                low[node] = min(low[node], disc[neighbor])
        for ref in component: components[ref] = component
    origin = topology.by_ref.get(origin_ref)
    def relevance(ref):
        s = topology.by_ref[ref]
        return (0 if origin_ref in components[ref] else 1,
                topology.shape.distance((origin.x, origin.y), (s.x, s.y)) if origin else 0, ref)
    ordered = sorted(cuts, key=relevance)
    rows = []
    for ref in ordered[:limit]:
        component = components[ref]
        remaining, sides = component - {ref}, []
        while remaining:
            seed = min(remaining)
            seen, stack = {seed}, [seed]
            remaining.remove(seed)
            while stack:
                for n in graph[stack.pop()] & remaining:
                    remaining.remove(n); seen.add(n); stack.append(n)
            side = geographic_context(topology, seen, objects)
            side["representative_location_ref"] = seed
            side["contains_query_origin"] = origin_ref in seen
            side["known_base_refs"] = sorted(o["object_ref"] for o in objects.values()
                if o.get("kind") == "base" and o.get("status", "active") == "active" and o.get("location_ref") in seen)[:16]
            sides.append(side)
        fog = any(s["unknown_boundary_location_count"] for s in sides)
        neck = topology.by_ref[ref]
        fog |= len(topology.adjacent(ref)) < len(topology.shape.neighbors((neck.x, neck.y)))
        rows.append({"location_ref": ref, "kind": "narrow_connector", "passage_width": 1,
                     "mobility_profile_ref": profile.profile_ref,
                     "known_component_location_count": len(component),
                     "separated_areas": sorted(sides, key=lambda s: (-s["known_location_count"], s["representative_location_ref"])),
                     "unknown_geography_may_provide_alternates": bool(fog),
                     "epistemic_status": "known_map_derivation",
                     "meaning": "One-tile surface cut, not a continent or guaranteed defensive position. Side counts exclude passage. No known surface alternative in this graph; fog, air, transports and special transit may bypass it."})
    return {"items": rows, "total_single_tile_connectors": len(cuts),
            "omitted_count": max(0, len(cuts)-len(rows)),
            "selection": "query component then geometric proximity; no strategic ranking",
            "wider_passages": "not_calculated", "graph_scope": "known terrain passability; not occupancy or ZOC"}


def wider_passage_evidence(topology, profile, origin_ref, *, max_nodes=4096, max_pairs=32):
    """Demand-only adjacent two-tile cuts, with explicit graph/candidate limits."""
    graph = {r: set() for r, s in topology.by_ref.items()
             if s.terrain != "unknown" and topology._passable(s, profile)}
    if len(graph) > max_nodes or origin_ref not in graph:
        return {"items": [], "status": "not_calculated", "reason": "graph_bound_or_missing_origin",
                "node_limit": max_nodes, "known_node_count": len(graph)}
    for r in graph:
        graph[r] = {n.location_ref for n in topology.adjacent(r).values() if n.location_ref in graph}
    component, stack = {origin_ref}, [origin_ref]
    while stack:
        for n in graph[stack.pop()] - component: component.add(n); stack.append(n)
    def parts(removed):
        remaining, sizes = component - set(removed), []
        while remaining:
            seed = min(remaining); remaining.remove(seed)
            group, stack = {seed}, [seed]
            while stack:
                for n in graph[stack.pop()] & remaining:
                    remaining.remove(n); group.add(n); stack.append(n)
            sizes.append(group)
        return sizes
    origin = topology.by_ref[origin_ref]
    def range_to(r):
        s = topology.by_ref[r]
        return topology.shape.distance((origin.x, origin.y), (s.x, s.y))
    candidates = sorted({tuple(sorted((r, n))) for r in component for n in graph[r]},
                        key=lambda pair: (min(map(range_to, pair)), pair))
    single_cache, rows = {}, []
    for pair in candidates[:max_pairs]:
        for r in pair:
            if r not in single_cache: single_cache[r] = len(parts([r])) > 1
        if any(single_cache[r] for r in pair): continue
        sides = parts(pair)
        if len(sides) < 2: continue
        rows.append({"passage_location_refs": list(pair), "passage_tile_count": 2,
                     "kind": "adjacent_two_tile_surface_cut",
                     "separated_known_area_sizes": sorted(map(len, sides), reverse=True),
                     "unknown_geography_may_provide_alternates": any(
                         len(topology.adjacent(r)) < len(topology.shape.neighbors((topology.by_ref[r].x, topology.by_ref[r].y))) for r in component),
                     "meaning": "Minimal two-tile cut in known terrain graph, not measured corridor width or a strategic verdict."})
    return {"items": rows[:8], "qualifying_candidates_omitted": max(0, len(rows)-8),
            "tested_pair_count": min(len(candidates), max_pairs), "total_candidate_pairs": len(candidates),
            "candidate_coverage_complete": len(candidates) <= max_pairs,
            "selection": "adjacent pairs nearest query origin", "status": "bounded_known_map_derivation"}


def frontier_access(topology, objects, frontier):
    from smacx_mechanics import mobility_profile
    boundary = sorted(set(frontier.get('boundary_refs', ())) & set(topology.by_ref))
    units = [u for u in objects.values() if u.get('kind') == 'own_unit'
             and u.get('status', 'active') == 'active' and u.get('location_ref') in topology.by_ref]
    def distance(u, ref):
        a, b = topology.by_ref[u['location_ref']], topology.by_ref[ref]
        return topology.shape.distance((a.x, a.y), (b.x, b.y))
    units.sort(key=lambda u: (min((distance(u, r) for r in boundary), default=10**9), u['object_ref']))
    rows = []
    for unit in units[:8]:
        targets = sorted(boundary, key=lambda r: (distance(unit, r), r))[:8]
        profile = mobility_profile(objects, 'frontier-candidate', subject_ref=unit['object_ref'], topology=topology)
        routes = [(topology.route(unit['location_ref'], r, profile), r) for r in targets]
        reached = [(route, r) for route, r in routes if route.reachable]
        if not reached: continue
        route, ref = min(reached, key=lambda v: (v[0].turns is None, v[0].turns or 0, v[0].movement_cost, v[1]))
        rows.append({'scout_ref': unit['object_ref'], 'candidate_kind': 'owned_unit_not_assigned_scout',
                     'roles': unit.get('fields', {}).get('roles', {}),
                     'observed_order': unit.get('fields', {}).get('order_name', {}),
                     'movement_triad': profile.triad, 'frontier_location_ref': ref,
                     'arrival_turns': route.turns, 'movement_cost': route.movement_cost,
                     'eta_kind': route.eta_kind, 'uncertainty': list(route.uncertainty)})
    rows.sort(key=lambda r: (r['arrival_turns'] is None, r['arrival_turns'] or 0, r['scout_ref']))
    return {'reachable_scouts': rows, 'nearest_scout_arrival_turns': rows[0]['arrival_turns'] if rows else None,
            'known_land_route_available': any(r['movement_triad'] == 'land' for r in rows),
            'transport_dependency': None if rows else 'unknown_after_bounded_search',
            'sampled_boundary_count': min(8, len(boundary)), 'total_boundary_count': len(boundary),
            'sampled_unit_count': min(8, len(units)), 'total_candidate_units': len(units),
            'candidate_coverage_complete': len(boundary) <= 8 and len(units) <= 8,
            'calculation_scope': 'lazy_query_only',
            'meaning': 'At most 64 subject-relative routes, geometrically nearby candidates first. Candidates are not assignments. Compare roles and orders; a bounded miss does not prove transport is required. Boundary size does not estimate unseen land or rewards.'}
