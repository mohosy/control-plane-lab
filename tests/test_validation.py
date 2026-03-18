from __future__ import annotations

import unittest

from control_plane_lab.models import Event, Topology
from control_plane_lab.simulation import analyze_topology, apply_events, trace_path
from control_plane_lab.validation import validate_topology


def unresolved_next_hop_topology() -> Topology:
    return Topology.from_dict(
        {
            "name": "unresolved-next-hop",
            "routers": [
                {"name": "core", "asn": 65000, "router_id": "10.255.0.1"},
                {"name": "edge", "asn": 65000, "router_id": "10.255.0.2"},
                {
                    "name": "market",
                    "asn": 65200,
                    "router_id": "198.51.100.1",
                    "connected_prefixes": [
                        {"prefix": "198.18.10.0/24", "advertise_bgp": True}
                    ],
                },
            ],
            "links": [
                {
                    "endpoints": ["core", "edge"],
                    "network": "10.0.0.0/31",
                    "addresses": {"core": "10.0.0.0", "edge": "10.0.0.1"},
                    "metric": 10,
                    "ospf": True,
                },
                {
                    "endpoints": ["edge", "market"],
                    "network": "198.51.100.8/31",
                    "addresses": {"edge": "198.51.100.8", "market": "198.51.100.9"},
                    "metric": 1,
                    "ospf": False,
                },
            ],
            "bgp_sessions": [
                {"local": "market", "peer": "edge"},
                {"local": "edge", "peer": "core", "next_hop_self": False},
            ],
            "probes": [
                {
                    "name": "core-to-market",
                    "source": "core",
                    "destination": "198.18.10.10",
                }
            ],
        }
    )


class TopologyValidationTests(unittest.TestCase):
    def test_duplicate_router_names_raise(self) -> None:
        with self.assertRaisesRegex(ValueError, "Duplicate router name"):
            Topology.from_dict(
                {
                    "name": "duplicate-router",
                    "routers": [
                        {"name": "r1", "asn": 65000, "router_id": "10.255.0.1"},
                        {"name": "r1", "asn": 65000, "router_id": "10.255.0.2"},
                    ],
                }
            )

    def test_duplicate_router_ids_raise(self) -> None:
        with self.assertRaisesRegex(ValueError, "Duplicate router ID"):
            Topology.from_dict(
                {
                    "name": "duplicate-router-id",
                    "routers": [
                        {"name": "r1", "asn": 65000, "router_id": "10.255.0.1"},
                        {"name": "r2", "asn": 65000, "router_id": "10.255.0.1"},
                    ],
                }
            )

    def test_duplicate_probe_names_raise(self) -> None:
        with self.assertRaisesRegex(ValueError, "Duplicate probe name"):
            Topology.from_dict(
                {
                    "name": "duplicate-probe",
                    "routers": [
                        {"name": "r1", "asn": 65000, "router_id": "10.255.0.1"},
                    ],
                    "probes": [
                        {"name": "probe", "source": "r1", "destination": "10.0.0.1"},
                        {"name": "probe", "source": "r1", "destination": "10.0.0.2"},
                    ],
                }
            )

    def test_link_address_outside_network_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "is not in link network"):
            Topology.from_dict(
                {
                    "name": "bad-link-address",
                    "routers": [
                        {"name": "r1", "asn": 65000, "router_id": "10.255.0.1"},
                        {"name": "r2", "asn": 65000, "router_id": "10.255.0.2"},
                    ],
                    "links": [
                        {
                            "endpoints": ["r1", "r2"],
                            "network": "10.0.0.0/31",
                            "addresses": {"r1": "10.0.0.0", "r2": "10.0.0.10"},
                            "metric": 10,
                        }
                    ],
                }
            )

    def test_invalid_prefix_event_reports_value_error(self) -> None:
        topology = unresolved_next_hop_topology()
        with self.assertRaisesRegex(ValueError, "Unknown router in prefix event"):
            apply_events(
                topology,
                [Event(kind="withdraw-prefix", router="missing", prefix=topology.routers["market"].connected_prefixes[0].prefix)],
            )

    def test_validation_warns_on_multi_origin_prefix(self) -> None:
        topology = Topology.from_dict(
            {
                "name": "multi-origin",
                "routers": [
                    {
                        "name": "a",
                        "asn": 65000,
                        "router_id": "10.255.0.1",
                        "connected_prefixes": [
                            {"prefix": "198.18.10.0/24", "advertise_bgp": True}
                        ],
                    },
                    {
                        "name": "b",
                        "asn": 65001,
                        "router_id": "10.255.0.2",
                        "connected_prefixes": [
                            {"prefix": "198.18.10.0/24", "advertise_bgp": True}
                        ],
                    },
                ],
            }
        )
        report = validate_topology(topology)
        codes = [item.code for item in report.warnings]
        self.assertIn("multi-origin-prefix", codes)

    def test_validation_warns_on_unresolved_bgp_next_hop(self) -> None:
        topology = unresolved_next_hop_topology()
        report = validate_topology(topology)
        codes = [item.code for item in report.warnings]
        self.assertIn("unresolved-next-hop", codes)
        self.assertIn("probe-unreachable", codes)

        result = analyze_topology(topology)
        trace = trace_path(result, "core", result.topology.probes[0].destination)
        self.assertFalse(trace.reachable)


if __name__ == "__main__":
    unittest.main()
