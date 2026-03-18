from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network
import heapq
from typing import Dict, List, Optional, Sequence, Tuple

from .models import BGPSession, ConnectedPrefix, Event, Link, Probe, Topology


@dataclass(frozen=True)
class Route:
    prefix: IPv4Network
    protocol: str
    origin_router: str
    next_hop_router: Optional[str]
    admin_distance: int
    metric: int = 0
    local_pref: int = 0
    as_path: Tuple[int, ...] = ()
    bgp_type: Optional[str] = None
    learned_from: Optional[str] = None
    description: str = ""

    def sort_key(self) -> Tuple[int, int, int, int, str]:
        bgp_preference = 0 if self.bgp_type == "ebgp" else 1
        as_len = len(self.as_path) if self.protocol == "bgp" else 0
        local_pref_rank = -self.local_pref if self.protocol == "bgp" else 0
        return (
            self.admin_distance,
            local_pref_rank,
            self.metric if self.protocol != "bgp" else as_len,
            bgp_preference,
            self.origin_router,
        )


@dataclass
class OSPFState:
    distances: Dict[str, Dict[str, int]]
    first_hops: Dict[str, Dict[str, Optional[str]]]
    routes: Dict[str, Dict[IPv4Network, Route]]


@dataclass
class TraceStep:
    router: str
    route: Route
    forwarding_to: Optional[str]


@dataclass
class PathTrace:
    source: str
    destination: IPv4Address
    reachable: bool
    steps: List[TraceStep] = field(default_factory=list)
    reason: str = ""

    def routers(self) -> List[str]:
        return [step.router for step in self.steps]


@dataclass
class SimulationResult:
    topology: Topology
    ospf: OSPFState
    routing_table: Dict[str, Dict[IPv4Network, Route]]

    def best_route_for_ip(self, router: str, destination: IPv4Address) -> Optional[Route]:
        best: Optional[Route] = None
        for prefix, route in self.routing_table[router].items():
            if destination not in prefix:
                continue
            if best is None:
                best = route
                continue
            if prefix.prefixlen > best.prefix.prefixlen:
                best = route
                continue
            if prefix.prefixlen == best.prefix.prefixlen and route.sort_key() < best.sort_key():
                best = route
        return best


@dataclass
class IncidentDelta:
    changed_routes: int
    impacted_routers: List[str]
    changed_prefixes: List[str]
    probe_deltas: List[Tuple[Probe, PathTrace, PathTrace]]


def analyze_topology(topology: Topology) -> SimulationResult:
    connected_routes = _build_connected_routes(topology)
    ospf = _compute_ospf(topology)
    bgp_routes = _compute_bgp(topology, ospf)

    routing_table: Dict[str, Dict[IPv4Network, Route]] = {}
    for router_name in topology.routers:
        candidates: Dict[IPv4Network, List[Route]] = {}
        for route in connected_routes[router_name].values():
            candidates.setdefault(route.prefix, []).append(route)
        for route in ospf.routes[router_name].values():
            candidates.setdefault(route.prefix, []).append(route)
        for route in bgp_routes[router_name].values():
            candidates.setdefault(route.prefix, []).append(route)

        best_by_prefix: Dict[IPv4Network, Route] = {}
        for prefix, prefix_routes in candidates.items():
            best_by_prefix[prefix] = sorted(prefix_routes, key=lambda item: item.sort_key())[0]
        routing_table[router_name] = best_by_prefix

    return SimulationResult(topology=topology, ospf=ospf, routing_table=routing_table)


def apply_events(topology: Topology, events: Sequence[Event]) -> Topology:
    mutated = topology.clone()
    for event in events:
        if event.kind in {"link-down", "link-up"}:
            if not event.left or not event.right:
                raise ValueError("Link event requires left and right routers")
            link = mutated.find_link(event.left, event.right)
            if link is None:
                raise ValueError(
                    "No link exists between {0} and {1}".format(event.left, event.right)
                )
            link.up = event.kind == "link-up"
            continue

        if event.kind in {"bgp-down", "bgp-up"}:
            if not event.left or not event.right:
                raise ValueError("BGP event requires left and right routers")
            changed = False
            for session in mutated.bgp_sessions:
                if {session.local, session.peer} == {event.left, event.right}:
                    session.up = event.kind == "bgp-up"
                    changed = True
            if not changed:
                raise ValueError(
                    "No BGP peering exists between {0} and {1}".format(
                        event.left, event.right
                    )
                )
            continue

        if event.kind in {"withdraw-prefix", "restore-prefix"}:
            if not event.router or event.prefix is None:
                raise ValueError("Prefix event requires router and prefix")
            router = mutated.routers[event.router]
            existing = [item for item in router.connected_prefixes if item.prefix == event.prefix]
            if event.kind == "withdraw-prefix":
                router.connected_prefixes = [
                    item for item in router.connected_prefixes if item.prefix != event.prefix
                ]
            elif not existing:
                router.connected_prefixes.append(
                    ConnectedPrefix(
                        prefix=event.prefix,
                        advertise_ospf=False,
                        advertise_bgp=True,
                        description="restored by scenario",
                    )
                )
            continue

        raise ValueError("Unsupported event type: {0}".format(event.kind))
    return mutated


def trace_path(result: SimulationResult, source: str, destination: IPv4Address) -> PathTrace:
    topology = result.topology
    current = source
    visited = set()
    steps: List[TraceStep] = []

    while True:
        if current in visited:
            return PathTrace(
                source=source,
                destination=destination,
                reachable=False,
                steps=steps,
                reason="routing loop detected at {0}".format(current),
            )
        visited.add(current)

        route = result.best_route_for_ip(current, destination)
        if route is None:
            return PathTrace(
                source=source,
                destination=destination,
                reachable=False,
                steps=steps,
                reason="no route to destination from {0}".format(current),
            )

        if route.protocol == "connected":
            steps.append(TraceStep(router=current, route=route, forwarding_to=None))
            return PathTrace(
                source=source,
                destination=destination,
                reachable=True,
                steps=steps,
                reason="destination reached on connected prefix",
            )

        forwarding_to = _resolve_next_router(result, current, route)
        steps.append(TraceStep(router=current, route=route, forwarding_to=forwarding_to))
        if forwarding_to is None:
            return PathTrace(
                source=source,
                destination=destination,
                reachable=False,
                steps=steps,
                reason="unable to resolve next hop for {0}".format(route.prefix),
            )

        current = forwarding_to


def run_probes(result: SimulationResult, probes: Sequence[Probe]) -> List[Tuple[Probe, PathTrace]]:
    return [(probe, trace_path(result, probe.source, probe.destination)) for probe in probes]


def diff_incident(
    baseline: SimulationResult,
    after: SimulationResult,
    probes: Sequence[Probe],
) -> IncidentDelta:
    impacted_routers = set()
    changed_prefixes = set()
    changed_routes = 0

    routers = sorted(set(baseline.topology.routers) | set(after.topology.routers))
    for router in routers:
        before_routes = baseline.routing_table.get(router, {})
        after_routes = after.routing_table.get(router, {})
        prefixes = set(before_routes) | set(after_routes)
        for prefix in prefixes:
            if _route_signature(before_routes.get(prefix)) != _route_signature(after_routes.get(prefix)):
                changed_routes += 1
                impacted_routers.add(router)
                changed_prefixes.add(str(prefix))

    before_probes = {probe.name: trace for probe, trace in run_probes(baseline, probes)}
    after_probes = {probe.name: trace for probe, trace in run_probes(after, probes)}
    probe_deltas = [
        (
            probe,
            before_probes[probe.name],
            after_probes[probe.name],
        )
        for probe in probes
        if _trace_signature(before_probes[probe.name]) != _trace_signature(after_probes[probe.name])
    ]

    return IncidentDelta(
        changed_routes=changed_routes,
        impacted_routers=sorted(impacted_routers),
        changed_prefixes=sorted(changed_prefixes),
        probe_deltas=probe_deltas,
    )


def _route_signature(route: Optional[Route]) -> Optional[Tuple[object, ...]]:
    if route is None:
        return None
    return (
        str(route.prefix),
        route.protocol,
        route.next_hop_router,
        route.origin_router,
        route.admin_distance,
        route.metric,
        route.local_pref,
        route.as_path,
        route.bgp_type,
    )


def _trace_signature(trace: PathTrace) -> Tuple[bool, str, Tuple[str, ...]]:
    return (
        trace.reachable,
        trace.reason,
        tuple(
            "{0}:{1}:{2}".format(
                step.router,
                step.route.prefix,
                step.forwarding_to or "-",
            )
            for step in trace.steps
        ),
    )


def _build_connected_routes(topology: Topology) -> Dict[str, Dict[IPv4Network, Route]]:
    routes: Dict[str, Dict[IPv4Network, Route]] = {}
    for router in topology.routers.values():
        router_routes: Dict[IPv4Network, Route] = {}
        for connected in router.all_connected_prefixes():
            router_routes[connected.prefix] = Route(
                prefix=connected.prefix,
                protocol="connected",
                origin_router=router.name,
                next_hop_router=None,
                admin_distance=0,
                metric=0,
                description=connected.description or connected.kind,
            )
        routes[router.name] = router_routes
    return routes


def _compute_ospf(topology: Topology) -> OSPFState:
    distances: Dict[str, Dict[str, int]] = {}
    first_hops: Dict[str, Dict[str, Optional[str]]] = {}
    routes: Dict[str, Dict[IPv4Network, Route]] = {router: {} for router in topology.routers}

    ospf_origins: Dict[str, List[ConnectedPrefix]] = {}
    for router in topology.routers.values():
        ospf_origins[router.name] = [
            prefix for prefix in router.all_connected_prefixes() if prefix.advertise_ospf
        ]

    for source in topology.routers:
        distance_map, previous = _shortest_paths(topology, source)
        distances[source] = distance_map
        first_hops[source] = {}
        for target in topology.routers:
            if source == target:
                first_hops[source][target] = None
                continue
            first_hops[source][target] = _first_hop(source, target, previous)

        for target, distance in distance_map.items():
            if source == target:
                continue
            next_hop = first_hops[source][target]
            if next_hop is None:
                continue
            for connected in ospf_origins[target]:
                routes[source][connected.prefix] = Route(
                    prefix=connected.prefix,
                    protocol="ospf",
                    origin_router=target,
                    next_hop_router=next_hop,
                    admin_distance=110,
                    metric=distance,
                    description=connected.description or connected.kind,
                )

    return OSPFState(distances=distances, first_hops=first_hops, routes=routes)


def _shortest_paths(
    topology: Topology, source: str
) -> Tuple[Dict[str, int], Dict[str, str]]:
    distances: Dict[str, int] = {source: 0}
    previous: Dict[str, str] = {}
    queue: List[Tuple[int, str]] = [(0, source)]

    while queue:
        current_distance, router = heapq.heappop(queue)
        if current_distance > distances.get(router, 1 << 30):
            continue
        for link in topology.neighbors(router, ospf_only=True):
            neighbor = link.other(router)
            tentative = current_distance + link.metric
            if tentative < distances.get(neighbor, 1 << 30):
                distances[neighbor] = tentative
                previous[neighbor] = router
                heapq.heappush(queue, (tentative, neighbor))
    return distances, previous


def _first_hop(source: str, target: str, previous: Dict[str, str]) -> Optional[str]:
    current = target
    hop = previous.get(current)
    if hop is None:
        return None
    while hop != source:
        current = hop
        hop = previous.get(current)
        if hop is None:
            return None
    return current


def _compute_bgp(topology: Topology, ospf: OSPFState) -> Dict[str, Dict[IPv4Network, Route]]:
    best: Dict[str, Dict[IPv4Network, Route]] = {router: {} for router in topology.routers}

    originated = _originated_bgp_routes(topology)
    for router, routes in originated.items():
        best[router].update(routes)

    for _ in range(len(topology.routers) * max(1, len(topology.bgp_sessions)) + 2):
        candidates: Dict[str, Dict[IPv4Network, List[Route]]] = {
            router: {} for router in topology.routers
        }
        for router, routes in originated.items():
            for route in routes.values():
                candidates[router].setdefault(route.prefix, []).append(route)

        for session in topology.bgp_sessions:
            if not session.up:
                continue
            local_routes = best.get(session.local, {})
            for route in local_routes.values():
                if not _can_advertise(route, session, topology):
                    continue
                transformed = _transform_bgp_route(route, session, topology)
                if transformed is None:
                    continue
                if transformed.next_hop_router not in topology.routers:
                    continue
                if transformed.bgp_type == "ibgp":
                    target = transformed.next_hop_router
                    if target != session.peer and target not in ospf.distances[session.peer]:
                        continue
                candidates[session.peer].setdefault(transformed.prefix, []).append(transformed)

        new_best: Dict[str, Dict[IPv4Network, Route]] = {router: {} for router in topology.routers}
        for router, routes_by_prefix in candidates.items():
            for prefix, prefix_routes in routes_by_prefix.items():
                new_best[router][prefix] = _best_bgp_route(prefix_routes)

        if _same_bgp_tables(best, new_best):
            return new_best
        best = new_best

    return best


def _originated_bgp_routes(topology: Topology) -> Dict[str, Dict[IPv4Network, Route]]:
    routes: Dict[str, Dict[IPv4Network, Route]] = {router: {} for router in topology.routers}
    for router in topology.routers.values():
        for connected in router.all_connected_prefixes():
            if not connected.advertise_bgp:
                continue
            routes[router.name][connected.prefix] = Route(
                prefix=connected.prefix,
                protocol="bgp",
                origin_router=router.name,
                next_hop_router=router.name,
                admin_distance=200,
                metric=0,
                local_pref=100,
                as_path=(),
                bgp_type="local",
                learned_from=None,
                description=connected.description or connected.kind,
            )
    return routes


def _can_advertise(route: Route, session: BGPSession, topology: Topology) -> bool:
    if session.export_prefixes and route.prefix not in session.export_prefixes:
        return False
    same_as = topology.routers[session.local].asn == topology.routers[session.peer].asn
    if same_as and route.bgp_type == "ibgp" and route.origin_router != session.local:
        return False
    return True


def _transform_bgp_route(
    route: Route, session: BGPSession, topology: Topology
) -> Optional[Route]:
    sender = topology.routers[session.local]
    receiver = topology.routers[session.peer]
    ebgp = sender.asn != receiver.asn

    if session.import_prefixes and route.prefix not in session.import_prefixes:
        return None

    candidate_as_path = route.as_path
    if ebgp:
        candidate_as_path = (sender.asn,) + candidate_as_path
    if receiver.asn in candidate_as_path:
        return None

    next_hop_router = sender.name if ebgp or session.next_hop_self else route.next_hop_router
    local_pref = session.import_local_pref
    if local_pref is None:
        local_pref = 100 if ebgp else route.local_pref

    return Route(
        prefix=route.prefix,
        protocol="bgp",
        origin_router=route.origin_router,
        next_hop_router=next_hop_router,
        admin_distance=20 if ebgp else 200,
        metric=0,
        local_pref=local_pref,
        as_path=candidate_as_path,
        bgp_type="ebgp" if ebgp else "ibgp",
        learned_from=sender.name,
        description=route.description,
    )


def _best_bgp_route(routes: Sequence[Route]) -> Route:
    def key(route: Route) -> Tuple[int, int, int, int, str]:
        return (
            -route.local_pref,
            len(route.as_path),
            0 if route.bgp_type == "ebgp" else 1,
            route.admin_distance,
            route.origin_router,
        )

    return sorted(routes, key=key)[0]


def _same_bgp_tables(
    left: Dict[str, Dict[IPv4Network, Route]], right: Dict[str, Dict[IPv4Network, Route]]
) -> bool:
    if set(left) != set(right):
        return False
    for router in left:
        if set(left[router]) != set(right[router]):
            return False
        for prefix in left[router]:
            if _route_signature(left[router][prefix]) != _route_signature(right[router][prefix]):
                return False
    return True


def _resolve_next_router(
    result: SimulationResult, current: str, route: Route
) -> Optional[str]:
    if route.next_hop_router is None:
        return None
    if route.next_hop_router == current:
        return route.origin_router if route.origin_router != current else None

    link = result.topology.find_link(current, route.next_hop_router)
    if link is not None and link.up:
        return route.next_hop_router

    return result.ospf.first_hops.get(current, {}).get(route.next_hop_router)
