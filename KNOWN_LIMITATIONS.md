# Known Limitations

Control Plane Lab is intentionally scoped as a compact control-plane simulator. It is useful for studying routing behavior and failure handling, but it does not attempt to model every detail of a production network stack.

## Protocol Scope

- OSPF is modeled as a single-area shortest-path graph. The simulator does not implement areas, ABRs, NSSA behavior, LSA types, or timer-driven convergence.
- BGP path selection is intentionally simplified. The model supports `local-pref`, AS-path length, and a basic eBGP versus iBGP preference, but it does not implement MED, origin codes, communities, route reflectors, confederations, or full route-map semantics.
- ECMP is not modeled. The simulator selects a single best path per prefix.
- The project does not model BFD, BGP keepalive timers, dampening, flap suppression, or control-plane timers.

## Forwarding and Data Plane

- This is not a packet-level emulator. It does not model queues, interface throughput, packet loss, latency, MTU, fragmentation, or line-rate behavior.
- L2 behavior is out of scope. ARP, VLANs, MAC learning, STP, MLAG, and interface state machines are not represented.
- Recursive forwarding is modeled at the next-hop resolution level only. The simulator does not build a FIB or represent hardware forwarding pipelines.

## Topology and Policy

- Topologies are static JSON inputs. There is no live device configuration parsing, no vendor syntax ingestion, and no reconciliation against real hardware state.
- Route import and export filters are prefix-list style only. The simulator does not support policy chaining, route-map ordering, or community-based policy decisions.
- External peers are modeled as routers in the same topology file. This is useful for analysis, but it is still an abstraction of real provider behavior.

## Validation Model

- Validation warnings are heuristics rather than formal proofs. A clean validation report means the topology passed the checks currently implemented, not that every failure mode has been eliminated.
- Some operational risks are intentionally surfaced as warnings instead of hard errors. For example, multi-origin BGP prefixes can be legitimate, but they still deserve attention.

## Intended Use

This project is best used for:

- Exploring routing-policy tradeoffs
- Demonstrating control-plane reasoning
- Reproducing simplified failover scenarios
- Building automation around topology validation and incident comparison

It should not be treated as a substitute for device-level testing, packet capture, or validation against production hardware and configurations.
