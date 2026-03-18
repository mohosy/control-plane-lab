# Architecture

## Purpose

Control Plane Lab is structured as a small, inspectable simulation engine. The code is separated so that topology parsing, protocol modeling, command-line presentation, and validation concerns remain independent.

## Module Layout

- `src/control_plane_lab/models.py`
  Defines the topology schema: routers, links, BGP sessions, probes, and scenario events.
- `src/control_plane_lab/loader.py`
  Loads topology and scenario JSON and converts inline event tokens into structured events.
- `src/control_plane_lab/simulation.py`
  Implements route construction, OSPF path computation, BGP propagation, forwarding traces, and incident diffs.
- `src/control_plane_lab/validation.py`
  Runs higher-level checks that are useful operationally but should not block topology parsing outright.
- `src/control_plane_lab/cli.py`
  Exposes the simulator through `summary`, `routes`, `path`, `probes`, `validate`, and `incident` commands.

## Data Flow

1. A topology JSON file is loaded into strongly typed models.
2. Structural validation runs while the topology is being constructed.
3. The simulation engine derives connected, OSPF, and BGP route candidates.
4. Each router selects a single best route per prefix.
5. CLI commands either display the resulting tables, trace reachability, or compare baseline and post-change state.

## OSPF Model

The OSPF model treats the active OSPF-enabled links as a weighted graph:

- Each router is a node.
- Each active OSPF link contributes an undirected edge with a configured metric.
- Dijkstra is run per source router.
- OSPF-advertised connected prefixes are installed using the shortest path to the originating router.

The current implementation assumes a single flat OSPF domain and does not model areas or LSA details.

## BGP Model

The BGP engine iteratively propagates candidate routes across directed sessions:

- Connected prefixes marked `advertise_bgp` originate locally.
- eBGP advertisements prepend the sender ASN and reset administrative distance to an external preference.
- iBGP advertisements preserve AS-path length and can optionally apply `next-hop-self`.
- Import policy supports per-session `local-pref` and prefix filters.
- A router selects a single best BGP route per prefix before installation into the combined routing table.

The route-selection logic is intentionally compact. It aims to be understandable and deterministic rather than exhaustive.

## Forwarding Model

Forwarding traces follow the selected best route hop by hop:

- Connected routes terminate the trace successfully.
- OSPF routes forward directly to the next internal hop.
- BGP routes attempt recursive next-hop resolution.
- If a next hop is neither directly connected nor reachable through the OSPF graph, the trace fails and the destination is reported unreachable.

This is where blackhole conditions and next-hop mistakes become visible, even when route advertisement appears superficially correct.

## Validation Strategy

Validation is intentionally layered:

- Structural validation rejects malformed topologies such as duplicate router names, duplicate router IDs, duplicate probe names, invalid link addressing, or duplicate links.
- Operational validation reports warnings for conditions that can be legitimate but risky, such as multi-origin prefixes, eBGP multihop assumptions, unresolved preserved next hops, and unreachable baseline probes.

This separation is deliberate. Invalid topology data should fail fast; potentially unsafe topology design should remain inspectable.

## Testing Approach

The test suite covers both normal and negative cases:

- OSPF path selection under unequal metrics
- BGP best-path selection using `local-pref`
- Failover between primary and backup peers
- Hard outages caused by fabric failures
- Malformed topology inputs and duplicate identifiers
- Validation warnings for operationally risky but syntactically valid topologies
