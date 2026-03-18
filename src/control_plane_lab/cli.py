from __future__ import annotations

import argparse
import json
from ipaddress import ip_address
from typing import List

from .loader import load_scenario, load_topology, parse_event_token
from .simulation import (
    IncidentDelta,
    SimulationResult,
    analyze_topology,
    apply_events,
    diff_incident,
    run_probes,
    trace_path,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cplab",
        description="Control Plane Lab: simulate routing behavior and incident blast radius.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    summary = subparsers.add_parser("summary", help="Summarize a topology")
    _add_topology_argument(summary)
    summary.add_argument("--json", action="store_true", help="Emit machine-readable output")

    routes = subparsers.add_parser("routes", help="Show best routes for a router")
    _add_topology_argument(routes)
    routes.add_argument("router", help="Router name")
    routes.add_argument("--json", action="store_true", help="Emit machine-readable output")

    path = subparsers.add_parser("path", help="Trace forwarding to a destination IP")
    _add_topology_argument(path)
    path.add_argument("router", help="Source router")
    path.add_argument("destination", help="Destination IPv4 address")
    path.add_argument("--json", action="store_true", help="Emit machine-readable output")

    probes = subparsers.add_parser("probes", help="Run the topology's built-in probes")
    _add_topology_argument(probes)
    probes.add_argument("--json", action="store_true", help="Emit machine-readable output")

    incident = subparsers.add_parser("incident", help="Apply events and report the delta")
    _add_topology_argument(incident)
    incident.add_argument("--scenario", help="Path to a JSON scenario file")
    incident.add_argument(
        "--event",
        action="append",
        default=[],
        help="Inline event, e.g. bgp-down:edge-a:isp-a",
    )
    incident.add_argument("--json", action="store_true", help="Emit machine-readable output")

    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    topology = load_topology(args.topology)

    if args.command == "summary":
        result = analyze_topology(topology)
        payload = _summary_payload(result)
        _emit(payload, args.json, _render_summary)
        return 0

    if args.command == "routes":
        result = analyze_topology(topology)
        if args.router not in topology.routers:
            parser.error("Unknown router: {0}".format(args.router))
        payload = _routes_payload(result, args.router)
        _emit(payload, args.json, _render_routes)
        return 0

    if args.command == "path":
        result = analyze_topology(topology)
        if args.router not in topology.routers:
            parser.error("Unknown router: {0}".format(args.router))
        trace = trace_path(result, args.router, ip_address(args.destination))
        payload = _trace_payload(trace)
        _emit(payload, args.json, _render_trace)
        return 0

    if args.command == "probes":
        result = analyze_topology(topology)
        payload = {
            "topology": topology.name,
            "probes": [
                {
                    "name": probe.name,
                    **_trace_payload(trace),
                }
                for probe, trace in run_probes(result, topology.probes)
            ],
        }
        _emit(payload, args.json, _render_probes)
        return 0

    if args.command == "incident":
        events = []
        if args.scenario:
            events.extend(load_scenario(args.scenario))
        events.extend(parse_event_token(token) for token in args.event)

        baseline = analyze_topology(topology)
        after_topology = apply_events(topology, events)
        after = analyze_topology(after_topology)
        delta = diff_incident(baseline, after, topology.probes)
        payload = _incident_payload(topology.name, events, delta)
        _emit(payload, args.json, _render_incident)
        return 0

    return 1


def _add_topology_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("topology", help="Path to a topology JSON file")


def _emit(payload, as_json: bool, renderer) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=False))
    else:
        print(renderer(payload))


def _summary_payload(result: SimulationResult):
    topology = result.topology
    active_links = len([link for link in topology.links if link.up])
    active_bgp = len([session for session in topology.bgp_sessions if session.up])
    routers = []
    for router_name, router in sorted(topology.routers.items()):
        routes = result.routing_table[router_name]
        routers.append(
            {
                "name": router_name,
                "asn": router.asn,
                "connected": len(
                    [route for route in routes.values() if route.protocol == "connected"]
                ),
                "ospf": len([route for route in routes.values() if route.protocol == "ospf"]),
                "bgp": len([route for route in routes.values() if route.protocol == "bgp"]),
                "total": len(routes),
            }
        )

    return {
        "topology": topology.name,
        "routers": len(topology.routers),
        "links": {"active": active_links, "total": len(topology.links)},
        "bgp_sessions": {"active": active_bgp, "total": len(topology.bgp_sessions)},
        "configured_probes": len(topology.probes),
        "router_stats": routers,
    }


def _routes_payload(result: SimulationResult, router: str):
    routes = sorted(
        result.routing_table[router].values(),
        key=lambda item: (-item.prefix.prefixlen, str(item.prefix)),
    )
    return {
        "router": router,
        "routes": [
            {
                "prefix": str(route.prefix),
                "protocol": route.protocol,
                "origin": route.origin_router,
                "next_hop": route.next_hop_router,
                "admin_distance": route.admin_distance,
                "metric": route.metric,
                "local_pref": route.local_pref,
                "as_path": list(route.as_path),
                "bgp_type": route.bgp_type,
                "description": route.description,
            }
            for route in routes
        ],
    }


def _trace_payload(trace):
    return {
        "source": trace.source,
        "destination": str(trace.destination),
        "reachable": trace.reachable,
        "reason": trace.reason,
        "steps": [
            {
                "router": step.router,
                "prefix": str(step.route.prefix),
                "protocol": step.route.protocol,
                "next_hop": step.route.next_hop_router,
                "forwarding_to": step.forwarding_to,
                "origin": step.route.origin_router,
                "local_pref": step.route.local_pref,
                "as_path": list(step.route.as_path),
            }
            for step in trace.steps
        ],
    }


def _incident_payload(name: str, events, delta: IncidentDelta):
    return {
        "topology": name,
        "events": [_render_event(event) for event in events],
        "changed_routes": delta.changed_routes,
        "impacted_routers": delta.impacted_routers,
        "changed_prefixes": delta.changed_prefixes,
        "probe_deltas": [
            {
                "name": probe.name,
                "before": _trace_payload(before),
                "after": _trace_payload(after),
            }
            for probe, before, after in delta.probe_deltas
        ],
    }


def _render_summary(payload) -> str:
    lines = [
        "Topology: {0}".format(payload["topology"]),
        "Routers: {0}".format(payload["routers"]),
        "Links: {0}/{1} active".format(
            payload["links"]["active"], payload["links"]["total"]
        ),
        "BGP sessions: {0}/{1} active".format(
            payload["bgp_sessions"]["active"], payload["bgp_sessions"]["total"]
        ),
        "Configured probes: {0}".format(payload["configured_probes"]),
        "",
        "{0:<14} {1:<8} {2:<10} {3:<6} {4:<6} {5:<6}".format(
            "Router", "ASN", "Connected", "OSPF", "BGP", "Total"
        ),
    ]
    for router in payload["router_stats"]:
        lines.append(
            "{0:<14} {1:<8} {2:<10} {3:<6} {4:<6} {5:<6}".format(
                router["name"],
                router["asn"],
                router["connected"],
                router["ospf"],
                router["bgp"],
                router["total"],
            )
        )
    return "\n".join(lines)


def _render_routes(payload) -> str:
    lines = [
        "Best routes for {0}".format(payload["router"]),
        "",
        "{0:<18} {1:<10} {2:<14} {3:<6} {4:<6} {5}".format(
            "Prefix", "Protocol", "Next Hop", "AD", "Metric", "Details"
        ),
    ]
    for route in payload["routes"]:
        details = "origin={0}".format(route["origin"])
        if route["protocol"] == "bgp":
            details = "{0} lp={1} as={2}".format(
                details,
                route["local_pref"],
                " ".join(str(item) for item in route["as_path"]) or "local",
            )
        lines.append(
            "{0:<18} {1:<10} {2:<14} {3:<6} {4:<6} {5}".format(
                route["prefix"],
                route["protocol"],
                route["next_hop"] or "-",
                route["admin_distance"],
                route["metric"],
                details,
            )
        )
    return "\n".join(lines)


def _render_trace(payload) -> str:
    lines = [
        "Path: {0} -> {1}".format(payload["source"], payload["destination"]),
        "Reachable: {0}".format("yes" if payload["reachable"] else "no"),
        "Reason: {0}".format(payload["reason"]),
        "",
    ]
    for index, step in enumerate(payload["steps"], start=1):
        extra = "origin={0}".format(step["origin"])
        if step["protocol"] == "bgp":
            extra = "{0} lp={1} as={2}".format(
                extra,
                step["local_pref"],
                " ".join(str(item) for item in step["as_path"]) or "local",
            )
        lines.append(
            "{0}. {1} uses {2} via {3} forwarding {4} ({5})".format(
                index,
                step["router"],
                step["prefix"],
                step["protocol"],
                step["forwarding_to"] or "local",
                extra,
            )
        )
    return "\n".join(lines)


def _render_probes(payload) -> str:
    lines = ["Probe results for {0}".format(payload["topology"]), ""]
    for probe in payload["probes"]:
        lines.append(
            "- {0}: {1}".format(
                probe["name"], "reachable" if probe["reachable"] else "unreachable"
            )
        )
        if probe["steps"]:
            lines.append(
                "  path: {0}".format(
                    " -> ".join(step["router"] for step in probe["steps"])
                )
            )
    return "\n".join(lines)


def _render_incident(payload) -> str:
    lines = [
        "Incident report for {0}".format(payload["topology"]),
        "",
        "Events:",
    ]
    for event in payload["events"]:
        lines.append("- {0}".format(event))
    lines.extend(
        [
            "",
            "Changed best routes: {0}".format(payload["changed_routes"]),
            "Impacted routers: {0}".format(", ".join(payload["impacted_routers"]) or "none"),
            "Changed prefixes: {0}".format(", ".join(payload["changed_prefixes"]) or "none"),
        ]
    )
    if payload["probe_deltas"]:
        lines.append("")
        lines.append("Probe deltas:")
        for probe in payload["probe_deltas"]:
            before_path = " -> ".join(step["router"] for step in probe["before"]["steps"])
            after_path = " -> ".join(step["router"] for step in probe["after"]["steps"])
            lines.append(
                "- {0}: {1} -> {2}".format(
                    probe["name"],
                    "reachable" if probe["before"]["reachable"] else "unreachable",
                    "reachable" if probe["after"]["reachable"] else "unreachable",
                )
            )
            lines.append("  before: {0}".format(before_path or probe["before"]["reason"]))
            lines.append("  after: {0}".format(after_path or probe["after"]["reason"]))
    return "\n".join(lines)


def _render_event(event) -> str:
    if event.kind in {"link-down", "link-up", "bgp-down", "bgp-up"}:
        return "{0} {1}<->{2}".format(event.kind, event.left, event.right)
    if event.prefix is not None:
        return "{0} {1} {2}".format(event.kind, event.router, event.prefix)
    return event.kind
