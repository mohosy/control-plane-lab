from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from .models import Event, Topology


def load_topology(path: str) -> Topology:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return Topology.from_dict(payload)


def load_scenario(path: str) -> List[Event]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return [Event.from_dict(item) for item in payload.get("events", [])]


def parse_event_token(token: str) -> Event:
    parts = token.split(":")
    kind = parts[0]
    if kind in {"link-down", "link-up", "bgp-down", "bgp-up"} and len(parts) == 3:
        return Event(kind=kind, left=parts[1], right=parts[2])
    if kind in {"withdraw-prefix", "restore-prefix"} and len(parts) == 3:
        from .models import parse_network

        return Event(kind=kind, router=parts[1], prefix=parse_network(parts[2]))
    raise ValueError(
        "Invalid event token '{0}'. Expected e.g. link-down:r1:r2".format(token)
    )


def resolve_path(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return str(Path(value).expanduser().resolve())
