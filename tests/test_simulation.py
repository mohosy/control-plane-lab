from __future__ import annotations

import unittest
from ipaddress import ip_address

from control_plane_lab.models import Event, Topology
from control_plane_lab.simulation import analyze_topology, apply_events, diff_incident, trace_path


def sample_topology() -> Topology:
    return Topology.from_dict(
        {
            "name": "unit-test-fabric",
            "routers": [
                {
                    "name": "core-a",
                    "asn": 65000,
                    "router_id": "10.255.0.1",
                    "connected_prefixes": [
                        {"prefix": "10.10.0.0/24", "advertise_ospf": True}
                    ],
                },
                {
                    "name": "core-b",
                    "asn": 65000,
                    "router_id": "10.255.0.2",
                    "connected_prefixes": [
                        {"prefix": "10.10.1.0/24", "advertise_ospf": True}
                    ],
                },
                {
                    "name": "edge",
                    "asn": 65000,
                    "router_id": "10.255.0.10",
                    "connected_prefixes": [],
                },
                {
                    "name": "market-a",
                    "asn": 65200,
                    "router_id": "198.51.100.1",
                    "connected_prefixes": [
                        {"prefix": "198.18.10.0/24", "advertise_bgp": True}
                    ],
                },
                {
                    "name": "market-b",
                    "asn": 65210,
                    "router_id": "198.51.100.2",
                    "connected_prefixes": [
                        {"prefix": "198.18.10.0/24", "advertise_bgp": True}
                    ],
                },
            ],
            "links": [
                {
                    "endpoints": ["core-a", "edge"],
                    "network": "10.0.0.0/31",
                    "addresses": {"core-a": "10.0.0.0", "edge": "10.0.0.1"},
                    "metric": 20,
                    "ospf": True,
                },
                {
                    "endpoints": ["core-b", "edge"],
                    "network": "10.0.0.2/31",
                    "addresses": {"core-b": "10.0.0.2", "edge": "10.0.0.3"},
                    "metric": 5,
                    "ospf": True,
                },
                {
                    "endpoints": ["core-a", "core-b"],
                    "network": "10.0.0.4/31",
                    "addresses": {"core-a": "10.0.0.4", "core-b": "10.0.0.5"},
                    "metric": 5,
                    "ospf": True,
                },
                {
                    "endpoints": ["edge", "market-a"],
                    "network": "198.51.100.8/31",
                    "addresses": {"edge": "198.51.100.8", "market-a": "198.51.100.9"},
                    "metric": 1,
                    "ospf": False,
                },
                {
                    "endpoints": ["edge", "market-b"],
                    "network": "198.51.100.10/31",
                    "addresses": {"edge": "198.51.100.10", "market-b": "198.51.100.11"},
                    "metric": 1,
                    "ospf": False,
                },
            ],
            "bgp_sessions": [
                {"local": "market-a", "peer": "edge", "import_local_pref": 300},
                {"local": "market-b", "peer": "edge", "import_local_pref": 200},
                {"local": "edge", "peer": "core-a", "next_hop_self": True},
                {"local": "edge", "peer": "core-b", "next_hop_self": True},
            ],
            "probes": [
                {
                    "name": "market feed",
                    "source": "core-a",
                    "destination": "198.18.10.10",
                }
            ],
        }
    )


class SimulationTests(unittest.TestCase):
    def test_ospf_picks_lower_metric_path(self) -> None:
        result = analyze_topology(sample_topology())
        route = result.best_route_for_ip("core-a", ip_address("10.255.0.10"))
        self.assertIsNotNone(route)
        self.assertEqual(route.next_hop_router, "core-b")
        self.assertEqual(route.metric, 10)

    def test_bgp_local_pref_beats_alternative(self) -> None:
        result = analyze_topology(sample_topology())
        route = result.best_route_for_ip("edge", ip_address("198.18.10.10"))
        self.assertIsNotNone(route)
        self.assertEqual(route.origin_router, "market-a")
        self.assertEqual(route.local_pref, 300)

    def test_path_reaches_external_prefix(self) -> None:
        result = analyze_topology(sample_topology())
        trace = trace_path(result, "core-a", ip_address("198.18.10.10"))
        self.assertTrue(trace.reachable)
        self.assertEqual([step.router for step in trace.steps], ["core-a", "edge", "market-a"])

    def test_incident_removes_primary_market_path(self) -> None:
        topology = sample_topology()
        baseline = analyze_topology(topology)
        after = analyze_topology(
            apply_events(
                topology,
                [
                    Event(kind="bgp-down", left="edge", right="market-a")
                ],
            )
        )
        delta = diff_incident(baseline, after, topology.probes)
        self.assertGreater(delta.changed_routes, 0)
        self.assertIn("core-a", delta.impacted_routers)
        trace = trace_path(after, "core-a", ip_address("198.18.10.10"))
        self.assertTrue(trace.reachable)
        self.assertEqual([step.router for step in trace.steps], ["core-a", "edge", "market-b"])

    def test_link_failure_can_blackhole_reachability(self) -> None:
        topology = sample_topology()
        degraded = apply_events(
            topology,
            [
                Event(kind="link-down", left="core-b", right="edge"),
                Event(kind="link-down", left="core-a", right="edge"),
            ],
        )
        result = analyze_topology(degraded)
        trace = trace_path(result, "core-a", ip_address("198.18.10.10"))
        self.assertFalse(trace.reachable)


if __name__ == "__main__":
    unittest.main()
