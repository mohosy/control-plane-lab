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
        routers = {
            router.name: router
            for router in (Router.from_dict(item) for item in data.get("routers", []))
        }
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
        for link in self.links:
            if link.a not in self.routers or link.b not in self.routers:
                raise ValueError("Unknown router in link {0}<->{1}".format(link.a, link.b))
            if set(link.addresses.keys()) != {link.a, link.b}:
                raise ValueError(
                    "Link {0}<->{1} must define addresses for both endpoints".format(
                        link.a, link.b
                    )
                )
        for session in self.bgp_sessions:
            if session.local not in self.routers or session.peer not in self.routers:
                raise ValueError(
                    "Unknown router in BGP session {0}->{1}".format(
                        session.local, session.peer
                    )
                )
        for probe in self.probes:
            if probe.source not in self.routers:
                raise ValueError("Unknown probe source router {0}".format(probe.source))

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
