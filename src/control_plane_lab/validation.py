from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import Topology
from .simulation import Route, SimulationResult, analyze_topology, run_probes


@dataclass(frozen=True)
class ValidationMessage:
    severity: str
    code: str
    message: str


@dataclass
class ValidationReport:
    topology: str
    errors: List[ValidationMessage] = field(default_factory=list)
    warnings: List[ValidationMessage] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.errors and not self.warnings


def validate_topology(topology: Topology) -> ValidationReport:
    report = ValidationReport(topology=topology.name)
    _warn_on_multi_origin_prefixes(topology, report)
    _warn_on_ebgp_multihop_assumptions(topology, report)

    result = analyze_topology(topology)
    _warn_on_ibgp_next_hop_risks(result, report)
    _warn_on_unresolved_bgp_next_hops(result, report)
    _warn_on_unreachable_probes(result, report)
    return report


def _warn_on_multi_origin_prefixes(topology: Topology, report: ValidationReport) -> None:
    prefix_origins: Dict[str, List[str]] = {}
    for router in topology.routers.values():
        for connected in router.connected_prefixes:
            if not connected.advertise_bgp:
                continue
            key = str(connected.prefix)
            prefix_origins.setdefault(key, []).append(router.name)

    for prefix, routers in sorted(prefix_origins.items()):
        if len(routers) < 2:
            continue
        report.warnings.append(
            ValidationMessage(
                severity="warning",
                code="multi-origin-prefix",
                message=(
                    "Prefix {0} is originated by multiple routers ({1}); best-path "
                    "selection will depend on policy and topology state."
                ).format(prefix, ", ".join(sorted(routers))),
            )
        )


def _warn_on_ebgp_multihop_assumptions(topology: Topology, report: ValidationReport) -> None:
    for session in topology.bgp_sessions:
        if not session.up or not session.is_ebgp(topology):
            continue
        if topology.find_link(session.local, session.peer) is not None:
            continue
        report.warnings.append(
            ValidationMessage(
                severity="warning",
                code="ebgp-multihop-assumption",
                message=(
                    "Active eBGP session {0}->{1} has no direct physical link in the "
                    "topology model; validation assumes multihop reachability."
                ).format(session.local, session.peer),
            )
        )


def _warn_on_unresolved_bgp_next_hops(
    result: SimulationResult, report: ValidationReport
) -> None:
    seen = set()
    for router_name, routes in result.routing_table.items():
        for route in routes.values():
            if route.protocol != "bgp":
                continue
            if _resolve_forwarding_target(result, router_name, route) is not None:
                continue
            key = (router_name, str(route.prefix), route.next_hop_router)
            if key in seen:
                continue
            seen.add(key)
            report.warnings.append(
                ValidationMessage(
                    severity="warning",
                    code="unresolved-next-hop",
                    message=(
                        "Router {0} selects BGP route {1} via next hop {2}, but the "
                        "next hop cannot be resolved through a direct link or OSPF."
                    ).format(router_name, route.prefix, route.next_hop_router or "unknown"),
                )
            )


def _warn_on_ibgp_next_hop_risks(result: SimulationResult, report: ValidationReport) -> None:
    seen = set()
    topology = result.topology
    for session in topology.bgp_sessions:
        if not session.up or session.next_hop_self or session.is_ebgp(topology):
            continue
        sender_routes = result.routing_table.get(session.local, {})
        for route in sender_routes.values():
            if route.protocol != "bgp" or route.next_hop_router in {None, session.local}:
                continue
            if _resolve_named_next_hop(result, session.peer, route.next_hop_router) is not None:
                continue
            key = (session.local, session.peer, str(route.prefix), route.next_hop_router)
            if key in seen:
                continue
            seen.add(key)
            report.warnings.append(
                ValidationMessage(
                    severity="warning",
                    code="unresolved-next-hop",
                    message=(
                        "iBGP session {0}->{1} preserves next hop {2} for prefix {3}, "
                        "but {1} has no direct or OSPF path to that next hop."
                    ).format(
                        session.local,
                        session.peer,
                        route.next_hop_router,
                        route.prefix,
                    ),
                )
            )


def _warn_on_unreachable_probes(result: SimulationResult, report: ValidationReport) -> None:
    for probe, trace in run_probes(result, result.topology.probes):
        if trace.reachable:
            continue
        report.warnings.append(
            ValidationMessage(
                severity="warning",
                code="probe-unreachable",
                message=(
                    "Probe '{0}' is unreachable in the baseline topology: {1}."
                ).format(probe.name, trace.reason),
            )
        )


def _resolve_forwarding_target(
    result: SimulationResult, router: str, route: Route
) -> Optional[str]:
    if route.next_hop_router is None:
        return None
    if route.next_hop_router == router:
        return route.origin_router if route.origin_router != router else None

    link = result.topology.find_link(router, route.next_hop_router)
    if link is not None and link.up:
        return route.next_hop_router

    return _resolve_named_next_hop(result, router, route.next_hop_router)


def _resolve_named_next_hop(
    result: SimulationResult, router: str, next_hop_router: str
) -> Optional[str]:
    return result.ospf.first_hops.get(router, {}).get(next_hop_router)
