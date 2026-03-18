from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network
from typing import Dict, List, Optional, Sequence
import copy


def parse_ip(value: str) -> IPv4Address:
    return ip_address(value)


def parse_network(value: str) -> IPv4Network:
    return ip_network(value, strict=False)


@dataclass
class ConnectedPrefix:
    prefix: IPv4Network
    advertise_ospf: bool = False
    advertise_bgp: bool = False
    description: str = ""
    kind: str = "service"

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "ConnectedPrefix":
        return cls(
            prefix=parse_network(str(data["prefix"])),
            advertise_ospf=bool(data.get("advertise_ospf", False)),
            advertise_bgp=bool(data.get("advertise_bgp", False)),
            description=str(data.get("description", "")),
            kind=str(data.get("kind", "service")),
        )


@dataclass
class Router:
    name: str
    asn: int
    router_id: IPv4Address
    connected_prefixes: List[ConnectedPrefix] = field(default_factory=list)
    description: str = ""

    @property
    def loopback_prefix(self) -> IPv4Network:
        return parse_network("{0}/32".format(self.router_id))

    def all_connected_prefixes(self) -> List[ConnectedPrefix]:
        return [
            ConnectedPrefix(
                prefix=self.loopback_prefix,
                advertise_ospf=True,
                advertise_bgp=False,
                description="router loopback",
                kind="loopback",
            )
        ] + list(self.connected_prefixes)

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "Router":
        connected = [
            ConnectedPrefix.from_dict(item)
            for item in data.get("connected_prefixes", [])
        ]
        return cls(
            name=str(data["name"]),
            asn=int(data["asn"]),
            router_id=parse_ip(str(data["router_id"])),
            connected_prefixes=connected,
            description=str(data.get("description", "")),
        )


@dataclass
class Link:
    a: str
    b: str
    network: IPv4Network
    addresses: Dict[str, IPv4Address]
    metric: int = 10
    ospf: bool = True
    up: bool = True
    description: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "Link":
        endpoints = data["endpoints"]
        if not isinstance(endpoints, Sequence) or len(endpoints) != 2:
            raise ValueError("Link endpoints must have exactly two router names")
        addresses_data = data["addresses"]
        if not isinstance(addresses_data, dict):
            raise ValueError("Link addresses must be a mapping of router to IP")
        return cls(
            a=str(endpoints[0]),
            b=str(endpoints[1]),
            network=parse_network(str(data["network"])),
            addresses={
                str(name): parse_ip(str(address))
                for name, address in addresses_data.items()
            },
            metric=int(data.get("metric", 10)),
            ospf=bool(data.get("ospf", True)),
            up=bool(data.get("up", True)),
            description=str(data.get("description", "")),
        )

    def other(self, router: str) -> str:
        if router == self.a:
            return self.b
        if router == self.b:
            return self.a
        raise KeyError("{0} is not attached to link {1}<->{2}".format(router, self.a, self.b))

    def connects(self, left: str, right: str) -> bool:
        return {self.a, self.b} == {left, right}


@dataclass
class BGPSession:
    local: str
    peer: str
    import_local_pref: Optional[int] = None
    import_prefixes: Optional[List[IPv4Network]] = None
    export_prefixes: Optional[List[IPv4Network]] = None
    next_hop_self: bool = False
    up: bool = True
    description: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "BGPSession":
        def parse_prefix_list(values: object) -> Optional[List[IPv4Network]]:
            if values is None:
                return None
            if not isinstance(values, list):
                raise ValueError("Prefix filters must be arrays of CIDRs")
            return [parse_network(str(item)) for item in values]

        return cls(
            local=str(data["local"]),
            peer=str(data["peer"]),
            import_local_pref=(
                int(data["import_local_pref"])
                if data.get("import_local_pref") is not None
                else None
            ),
            import_prefixes=parse_prefix_list(data.get("import_prefixes")),
            export_prefixes=parse_prefix_list(data.get("export_prefixes")),
            next_hop_self=bool(data.get("next_hop_self", False)),
            up=bool(data.get("up", True)),
            description=str(data.get("description", "")),
        )

    def is_ebgp(self, topology: "Topology") -> bool:
        return topology.routers[self.local].asn != topology.routers[self.peer].asn


@dataclass
class Probe:
    name: str
    source: str
    destination: IPv4Address

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "Probe":
        return cls(
            name=str(data["name"]),
            source=str(data["source"]),
            destination=parse_ip(str(data["destination"])),
        )


@dataclass
class Event:
    kind: str
    left: Optional[str] = None
    right: Optional[str] = None
    router: Optional[str] = None
    prefix: Optional[IPv4Network] = None

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "Event":
        kind = str(data["type"])
        prefix = data.get("prefix")
        return cls(
            kind=kind,
            left=str(data["left"]) if data.get("left") is not None else None,
            right=str(data["right"]) if data.get("right") is not None else None,
            router=str(data["router"]) if data.get("router") is not None else None,
            prefix=parse_network(str(prefix)) if prefix is not None else None,
        )


@dataclass
class Topology:
    name: str
    routers: Dict[str, Router]
    links: List[Link]
    bgp_sessions: List[BGPSession]
    probes: List[Probe]

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "Topology":
        routers: Dict[str, Router] = {}
        for router in (Router.from_dict(item) for item in data.get("routers", [])):
            if router.name in routers:
                raise ValueError("Duplicate router name: {0}".format(router.name))
            routers[router.name] = router
        topology = cls(
            name=str(data["name"]),
            routers=routers,
            links=[Link.from_dict(item) for item in data.get("links", [])],
            bgp_sessions=[BGPSession.from_dict(item) for item in data.get("bgp_sessions", [])],
            probes=[Probe.from_dict(item) for item in data.get("probes", [])],
        )
        topology.validate()
        return topology

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("Topology name must not be empty")
        if not self.routers:
            raise ValueError("Topology must define at least one router")

        router_ids: Dict[IPv4Address, str] = {}
        probe_names = set()
        bgp_sessions = set()
        links = set()

        for router in self.routers.values():
            if router.asn <= 0:
                raise ValueError("Router {0} has invalid ASN {1}".format(router.name, router.asn))
            existing_router = router_ids.get(router.router_id)
            if existing_router is not None:
                raise ValueError(
                    "Duplicate router ID {0} on {1} and {2}".format(
                        router.router_id, existing_router, router.name
                    )
                )
            router_ids[router.router_id] = router.name

            prefixes = set()
            for connected in router.connected_prefixes:
                if connected.prefix in prefixes:
                    raise ValueError(
                        "Duplicate connected prefix {0} on router {1}".format(
                            connected.prefix, router.name
                        )
                    )
                prefixes.add(connected.prefix)

        for link in self.links:
            if link.a not in self.routers or link.b not in self.routers:
                raise ValueError("Unknown router in link {0}<->{1}".format(link.a, link.b))
            if link.a == link.b:
                raise ValueError("Link endpoints must be distinct: {0}".format(link.a))
            if set(link.addresses.keys()) != {link.a, link.b}:
                raise ValueError(
                    "Link {0}<->{1} must define addresses for both endpoints".format(
                        link.a, link.b
                    )
                )
            if link.metric <= 0:
                raise ValueError(
                    "Link {0}<->{1} must have a positive metric".format(link.a, link.b)
                )
            link_key = tuple(sorted((link.a, link.b)))
            if link_key in links:
                raise ValueError(
                    "Duplicate link defined between {0} and {1}".format(link.a, link.b)
                )
            links.add(link_key)

            addresses = list(link.addresses.values())
            if len(set(addresses)) != 2:
                raise ValueError(
                    "Link {0}<->{1} must use unique interface addresses".format(
                        link.a, link.b
                    )
                )
            for router_name, address in link.addresses.items():
                if address not in link.network:
                    raise ValueError(
                        "Address {0} for router {1} is not in link network {2}".format(
                            address, router_name, link.network
                        )
                    )

        for session in self.bgp_sessions:
            if session.local not in self.routers or session.peer not in self.routers:
                raise ValueError(
                    "Unknown router in BGP session {0}->{1}".format(
                        session.local, session.peer
                    )
                )
            if session.local == session.peer:
                raise ValueError(
                    "BGP session must use two distinct routers: {0}".format(session.local)
                )
            session_key = (session.local, session.peer)
            if session_key in bgp_sessions:
                raise ValueError(
                    "Duplicate directed BGP session {0}->{1}".format(
                        session.local, session.peer
                    )
                )
            bgp_sessions.add(session_key)
        for probe in self.probes:
            if probe.source not in self.routers:
                raise ValueError("Unknown probe source router {0}".format(probe.source))
            if probe.name in probe_names:
                raise ValueError("Duplicate probe name: {0}".format(probe.name))
            probe_names.add(probe.name)

    def clone(self) -> "Topology":
        return copy.deepcopy(self)

    def neighbors(self, router: str, ospf_only: bool = False) -> List[Link]:
        links = []
        for link in self.links:
            if not link.up:
                continue
            if ospf_only and not link.ospf:
                continue
            if link.a == router or link.b == router:
                links.append(link)
        return links

    def find_link(self, left: str, right: str) -> Optional[Link]:
        for link in self.links:
            if link.connects(left, right):
                return link
        return None

    def connected_prefix_owner(self, prefix: IPv4Network) -> Optional[str]:
        for router in self.routers.values():
            for connected in router.all_connected_prefixes():
                if connected.prefix == prefix:
                    return router.name
        return None
