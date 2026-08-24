"""Reproducible topology-only metrics for canonical street networks."""

from __future__ import annotations

from collections import Counter
import csv
from dataclasses import dataclass
import importlib.metadata
import json
from pathlib import Path
import statistics
from typing import Any, Iterable

import networkx as nx
import osmnx as ox
import yaml

from .graph import (
    AnalysisGraphError,
    CanonicalSelection,
    build_networkx_multigraph,
    build_osmnx_multidigraph,
    load_canonical_geopackage,
    resolve_canonical_run,
    validate_canonical_for_analysis,
    validate_graph_correspondence,
)
from .io import sha256_file, write_json
from .versioned import git_provenance, software_environment


SCHEMA_VERSION = "1.0.0"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUMMARY_FIELDS = [
    "metric_name",
    "value",
    "units",
    "library",
    "function",
    "graph_representation",
    "weighting",
    "interpretation",
]
NODE_FIELDS = [
    "node_id",
    "degree",
    "streets_per_node_osmnx",
    "degree_centrality",
    "betweenness_unweighted",
    "betweenness_length",
    "closeness_unweighted",
    "closeness_length",
    "eccentricity_hops",
    "mean_distance_local",
    "is_articulation_point",
]
EDGE_FIELDS = [
    "edge_id",
    "u",
    "v",
    "key",
    "length_local",
    "betweenness_unweighted",
    "betweenness_length",
    "is_bridge",
]


class TopologyMetricError(RuntimeError):
    """Raised when topology metric assumptions or provenance checks fail."""


@dataclass(frozen=True)
class TopologyConfig:
    schema_version: str
    physical_length_attribute: str
    osmnx_intersection_min_streets: int
    disconnected_policy: str
    centrality_normalized: bool
    path: Path
    file_sha256: str


@dataclass(frozen=True)
class TopologyCalculation:
    simple_graph: nx.Graph
    summary_rows: tuple[dict[str, Any], ...]
    node_rows: tuple[dict[str, Any], ...]
    edge_rows: tuple[dict[str, Any], ...]
    osmnx_crosscheck: dict[str, Any]
    methodology: dict[str, Any]

    def metric(self, name: str) -> Any:
        """Return one scalar summary value by stable metric name."""
        matches = [row["value"] for row in self.summary_rows if row["metric_name"] == name]
        if len(matches) != 1:
            raise KeyError(f"Expected exactly one summary metric named {name!r}.")
        return matches[0]


@dataclass(frozen=True)
class TopologyAnalysisResult:
    selection: CanonicalSelection
    physical_graph: nx.MultiGraph
    osmnx_graph: nx.MultiDiGraph
    calculation: TopologyCalculation
    output_dir: Path
    manifest_path: Path
    manifest: dict[str, Any]


def _display_path(path: Path, project_root: Path) -> str:
    resolved, root = Path(path).resolve(strict=False), Path(project_root).resolve(strict=False)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved.as_posix()


def load_topology_config(
    path: Path | None = None,
    *,
    project_root: Path = PROJECT_ROOT,
) -> TopologyConfig:
    """Load and strictly validate the small versioned metric configuration."""
    selected = Path(path) if path is not None else Path(project_root) / "config" / "metrics.yaml"
    if not selected.is_file():
        raise TopologyMetricError(f"Metrics configuration does not exist: {selected}")
    try:
        raw = yaml.safe_load(selected.read_text(encoding="utf-8"))
        topology = raw["topology"]
    except (OSError, TypeError, KeyError, yaml.YAMLError) as exc:
        raise TopologyMetricError(f"Invalid topology metrics configuration: {exc}") from exc
    expected_topology = {
        "physical_length_attribute",
        "osmnx_intersection_min_streets",
        "disconnected_policy",
        "centrality",
    }
    if set(raw) != {"schema_version", "topology"} or set(topology) != expected_topology:
        raise TopologyMetricError("Metrics configuration has unknown or missing top-level topology fields.")
    centrality = topology["centrality"]
    if set(centrality) != {"normalized"}:
        raise TopologyMetricError("Metrics centrality configuration must contain only 'normalized'.")
    if str(raw["schema_version"]) != SCHEMA_VERSION:
        raise TopologyMetricError(f"Unsupported metrics schema_version: {raw['schema_version']}")
    if topology["physical_length_attribute"] != "length":
        raise TopologyMetricError("Phase 7F requires physical_length_attribute: length.")
    minimum = topology["osmnx_intersection_min_streets"]
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1:
        raise TopologyMetricError("osmnx_intersection_min_streets must be a positive integer.")
    if topology["disconnected_policy"] != "error":
        raise TopologyMetricError("Phase 7F supports only disconnected_policy: error.")
    if not isinstance(centrality["normalized"], bool):
        raise TopologyMetricError("centrality.normalized must be boolean.")
    return TopologyConfig(
        schema_version=SCHEMA_VERSION,
        physical_length_attribute="length",
        osmnx_intersection_min_streets=minimum,
        disconnected_policy="error",
        centrality_normalized=centrality["normalized"],
        path=selected.resolve(),
        file_sha256=sha256_file(selected),
    )


def parallel_physical_edge_count(graph: nx.MultiGraph) -> int:
    """Count physical edges beyond the first edge for every unordered node pair."""
    pair_counts = Counter(frozenset((str(u), str(v))) for u, v in graph.edges())
    return sum(max(0, count - 1) for count in pair_counts.values())


def validated_simple_graph(graph: nx.MultiGraph) -> nx.Graph:
    """Create a simple view only after proving no topology would be collapsed."""
    if not isinstance(graph, nx.MultiGraph) or graph.is_directed():
        raise TopologyMetricError("G_physical must be an undirected NetworkX MultiGraph.")
    loops = nx.number_of_selfloops(graph)
    parallels = parallel_physical_edge_count(graph)
    if loops:
        raise TopologyMetricError(f"Cannot create G_simple: physical graph has {loops} self-loop(s).")
    if parallels:
        raise TopologyMetricError(
            f"Cannot create G_simple: physical graph has {parallels} parallel physical edge(s)."
        )
    simple = nx.Graph(graph)
    if simple.number_of_nodes() != graph.number_of_nodes():
        raise TopologyMetricError("G_simple conversion changed the node count.")
    if simple.number_of_edges() != graph.number_of_edges():
        raise TopologyMetricError("G_simple conversion changed the physical edge count.")
    source = {
        (frozenset((str(u), str(v))), str(data.get("edge_id")))
        for u, v, data in graph.edges(data=True)
    }
    converted = {
        (frozenset((str(u), str(v))), str(data.get("edge_id")))
        for u, v, data in simple.edges(data=True)
    }
    if source != converted:
        raise TopologyMetricError("G_simple conversion did not preserve endpoint and edge identities.")
    return simple


def osmnx_street_crosscheck(
    physical_graph: nx.MultiGraph,
    osmnx_graph: nx.MultiDiGraph,
    *,
    min_streets: int,
) -> dict[str, Any]:
    """Run only semantically valid OSMnx street-incidence functions."""
    counts = {str(node): int(value) for node, value in ox.stats.count_streets_per_node(osmnx_graph).items()}
    nx_counts = {str(node): int(value) for node, value in physical_graph.degree()}
    mismatches = {
        node: {"networkx_degree": nx_counts.get(node), "osmnx_street_count": counts.get(node)}
        for node in sorted(set(nx_counts) | set(counts))
        if nx_counts.get(node) != counts.get(node)
    }
    if mismatches:
        raise TopologyMetricError(f"OSMnx/NetworkX node-by-node street-count mismatch: {mismatches}")
    nx.set_node_attributes(osmnx_graph, counts, "street_count")
    retrieved = {str(node): int(value) for node, value in ox.stats.streets_per_node(osmnx_graph).items()}
    if retrieved != counts:
        raise TopologyMetricError("OSMnx streets_per_node did not retrieve the populated street_count values.")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS",
        "graph_types": {
            "street_incidence": type(osmnx_graph).__name__,
            "street_segment_count": type(physical_graph).__name__,
            "self_loop_proportion": type(physical_graph).__name__,
        },
        "node_by_node": [
            {"node_id": node, "networkx_degree": nx_counts[node], "osmnx_street_count": counts[node]}
            for node in sorted(nx_counts)
        ],
        "streets_per_node": dict(sorted(counts.items())),
        "streets_per_node_average": float(ox.stats.streets_per_node_avg(osmnx_graph)),
        "streets_per_node_counts": {
            str(key): int(value)
            for key, value in sorted(ox.stats.streets_per_node_counts(osmnx_graph).items())
        },
        "streets_per_node_proportions": {
            str(key): float(value)
            for key, value in sorted(ox.stats.streets_per_node_proportions(osmnx_graph).items())
        },
        "intersection_min_streets": min_streets,
        "intersection_count": int(
            ox.stats.intersection_count(osmnx_graph, min_streets=min_streets)
        ),
        "physical_street_segment_count": int(ox.stats.street_segment_count(physical_graph)),
        "self_loop_proportion": float(ox.stats.self_loop_proportion(physical_graph)),
        "directed_arc_count_not_physical_streets": osmnx_graph.number_of_edges(),
    }


def _metric(
    name: str,
    value: Any,
    units: str,
    library: str,
    function: str,
    graph: str,
    weighting: str,
    interpretation: str,
) -> dict[str, Any]:
    return dict(zip(SUMMARY_FIELDS, (name, value, units, library, function, graph, weighting, interpretation)))


def calculate_topology_metrics(
    physical_graph: nx.MultiGraph,
    osmnx_graph: nx.MultiDiGraph,
    config: TopologyConfig,
) -> TopologyCalculation:
    """Calculate the focused Phase 7F metric family without geographic operations."""
    simple = validated_simple_graph(physical_graph)
    components = nx.number_connected_components(simple)
    if config.disconnected_policy == "error" and not nx.is_connected(simple):
        raise TopologyMetricError(
            "Connected-only topology metrics require one connected component; configuration policy is error."
        )
    length = config.physical_length_attribute
    missing = [data.get("edge_id", "<unknown>") for *_, data in simple.edges(data=True) if length not in data]
    nonpositive = [data.get("edge_id", "<unknown>") for *_, data in simple.edges(data=True) if length in data and float(data[length]) <= 0]
    if missing or nonpositive:
        raise TopologyMetricError(f"Every physical edge requires a strictly positive '{length}' value; missing={missing}, nonpositive={nonpositive}")

    node_count, edge_count = simple.number_of_nodes(), simple.number_of_edges()
    degrees = {str(node): int(value) for node, value in physical_graph.degree()}
    degree_sum = sum(degrees.values())
    if degree_sum != 2 * edge_count:
        raise TopologyMetricError(f"Handshake theorem failed: sum(degree)={degree_sum}, 2E={2 * edge_count}.")
    degree_counts = Counter(degrees.values())
    degree_proportions = {key: value / node_count for key, value in degree_counts.items()}
    crosscheck = osmnx_street_crosscheck(
        physical_graph,
        osmnx_graph,
        min_streets=config.osmnx_intersection_min_streets,
    )

    bridges = {str(simple[u][v]["edge_id"]) for u, v in nx.bridges(simple)}
    articulation = {str(node) for node in nx.articulation_points(simple)}
    eccentricity = {str(node): int(value) for node, value in nx.eccentricity(simple).items()}
    degree_centrality = {str(node): float(value) for node, value in nx.degree_centrality(simple).items()}
    between_unweighted = {
        str(node): float(value)
        for node, value in nx.betweenness_centrality(
            simple, weight=None, normalized=config.centrality_normalized
        ).items()
    }
    between_length = {
        str(node): float(value)
        for node, value in nx.betweenness_centrality(
            simple, weight=length, normalized=config.centrality_normalized
        ).items()
    }
    close_unweighted = {
        str(node): float(value)
        for node, value in nx.closeness_centrality(simple, distance=None).items()
    }
    close_length = {
        str(node): float(value)
        for node, value in nx.closeness_centrality(simple, distance=length).items()
    }
    edge_between_unweighted = nx.edge_betweenness_centrality(
        simple, weight=None, normalized=config.centrality_normalized
    )
    edge_between_length = nx.edge_betweenness_centrality(
        simple, weight=length, normalized=config.centrality_normalized
    )
    edge_by_id: dict[str, tuple[str, str, dict[str, Any]]] = {}
    edge_unweighted_by_id: dict[str, float] = {}
    edge_length_by_id: dict[str, float] = {}
    for u, v, data in simple.edges(data=True):
        edge_id = str(data["edge_id"])
        edge_by_id[edge_id] = (str(u), str(v), data)
        edge_unweighted_by_id[edge_id] = float(edge_between_unweighted[(u, v)])
        edge_length_by_id[edge_id] = float(edge_between_length[(u, v)])

    mean_distance_local: dict[str, float] = {}
    for node in simple.nodes:
        distances = nx.single_source_dijkstra_path_length(simple, node, weight=length)
        mean_distance_local[str(node)] = float(sum(distances.values()) / (node_count - 1))

    cycle_rank = edge_count - node_count + components
    degree_values = list(degrees.values())
    rows = [
        _metric("node_count", node_count, "nodes", "NetworkX", "Graph.number_of_nodes", "G_physical MultiGraph", "unweighted", "canonical physical nodes"),
        _metric("physical_edge_count", edge_count, "physical streets", "NetworkX", "Graph.number_of_edges", "G_physical MultiGraph", "unweighted", "canonical physical streets, not reciprocal arcs"),
        _metric("connected_component_count", components, "components", "NetworkX", "number_connected_components", "G_simple Graph", "unweighted", "maximal connected node sets"),
        _metric("is_connected", bool(nx.is_connected(simple)), "boolean", "NetworkX", "is_connected", "G_simple Graph", "unweighted", "whether every pair of nodes has a path"),
        _metric("self_loop_count", nx.number_of_selfloops(physical_graph), "physical streets", "NetworkX", "number_of_selfloops", "G_physical MultiGraph", "unweighted", "physical streets whose endpoints are identical"),
        _metric("parallel_physical_edge_count", parallel_physical_edge_count(physical_graph), "physical streets beyond first per pair", "custom", "parallel_physical_edge_count", "G_physical MultiGraph", "unweighted", "edges that would be lost by simple conversion"),
        _metric("degree_sum", degree_sum, "incidences", "NetworkX", "Graph.degree", "G_physical MultiGraph", "unweighted", "sum of physical street incidences"),
        _metric("twice_physical_edge_count", 2 * edge_count, "incidences", "custom", "2 * E", "G_physical MultiGraph", "unweighted", "independent handshake-theorem comparator"),
        _metric("degree_min", min(degree_values), "physical streets per node", "Python", "min", "G_physical MultiGraph", "unweighted", "minimum node degree"),
        _metric("degree_max", max(degree_values), "physical streets per node", "Python", "max", "G_physical MultiGraph", "unweighted", "maximum node degree"),
        _metric("degree_mean", float(statistics.mean(degree_values)), "physical streets per node", "Python", "statistics.mean", "G_physical MultiGraph", "unweighted", "mean node degree"),
        _metric("degree_median", float(statistics.median(degree_values)), "physical streets per node", "Python", "statistics.median", "G_physical MultiGraph", "unweighted", "median node degree"),
        _metric("dead_end_count", degree_counts.get(1, 0), "nodes", "custom", "degree == 1", "G_physical MultiGraph", "unweighted", "nodes incident to one physical street"),
        _metric("dead_end_proportion", degree_proportions.get(1, 0.0), "proportion", "custom", "degree == 1", "G_physical MultiGraph", "unweighted", "fraction of nodes that are dead ends"),
        _metric("degree_3_count", degree_counts.get(3, 0), "nodes", "custom", "degree == 3", "G_physical MultiGraph", "unweighted", "three-street junction nodes"),
        _metric("degree_3_proportion", degree_proportions.get(3, 0.0), "proportion", "custom", "degree == 3", "G_physical MultiGraph", "unweighted", "fraction of three-street junctions"),
        _metric("degree_4_count", degree_counts.get(4, 0), "nodes", "custom", "degree == 4", "G_physical MultiGraph", "unweighted", "four-street junction nodes"),
        _metric("degree_4_proportion", degree_proportions.get(4, 0.0), "proportion", "custom", "degree == 4", "G_physical MultiGraph", "unweighted", "fraction of four-street junctions"),
        _metric("osmnx_streets_per_node_average", crosscheck["streets_per_node_average"], "physical streets per node", "OSMnx", "stats.streets_per_node_avg", "G_ox MultiDiGraph", "unweighted", "average physical street incidence after reciprocal-arc de-duplication"),
        _metric("osmnx_intersection_count", crosscheck["intersection_count"], "nodes", "OSMnx", "stats.intersection_count", "G_ox MultiDiGraph", "unweighted", f"nodes with at least {config.osmnx_intersection_min_streets} physical streets"),
        _metric("osmnx_street_segment_count", crosscheck["physical_street_segment_count"], "physical streets", "OSMnx", "stats.street_segment_count", "G_physical MultiGraph", "unweighted", "undirected physical street count"),
        _metric("osmnx_self_loop_proportion", crosscheck["self_loop_proportion"], "proportion", "OSMnx", "stats.self_loop_proportion", "G_physical MultiGraph", "unweighted", "fraction of physical streets that are self-loops"),
        _metric("cycle_rank", cycle_rank, "independent cycles", "custom", "E - V + C", "G_simple Graph", "unweighted", "cyclomatic number of the physical topology"),
        _metric("bridge_count", len(bridges), "physical streets", "NetworkX", "bridges", "G_simple Graph", "unweighted", "streets whose removal increases component count"),
        _metric("bridge_proportion", len(bridges) / edge_count, "proportion", "NetworkX", "bridges", "G_simple Graph", "unweighted", "fraction of physical streets that are bridges"),
        _metric("articulation_point_count", len(articulation), "nodes", "NetworkX", "articulation_points", "G_simple Graph", "unweighted", "nodes whose removal increases component count"),
        _metric("articulation_point_proportion", len(articulation) / node_count, "proportion", "NetworkX", "articulation_points", "G_simple Graph", "unweighted", "fraction of nodes that are articulation points"),
        _metric("node_connectivity", int(nx.node_connectivity(simple)), "nodes", "NetworkX", "node_connectivity", "G_simple Graph", "unweighted", "minimum nodes whose removal disconnects the graph"),
        _metric("edge_connectivity", int(nx.edge_connectivity(simple)), "physical streets", "NetworkX", "edge_connectivity", "G_simple Graph", "unweighted", "minimum streets whose removal disconnects the graph"),
        _metric("average_shortest_path_hops", float(nx.average_shortest_path_length(simple, weight=None)), "hops", "NetworkX", "average_shortest_path_length", "G_simple Graph", "edge cost = 1", "mean minimum physical-edge count between node pairs"),
        _metric("diameter_hops", int(nx.diameter(simple)), "hops", "NetworkX", "diameter", "G_simple Graph", "edge cost = 1", "largest node eccentricity"),
        _metric("radius_hops", int(nx.radius(simple)), "hops", "NetworkX", "radius", "G_simple Graph", "edge cost = 1", "smallest node eccentricity"),
        _metric("average_shortest_path_length_local", float(nx.average_shortest_path_length(simple, weight=length)), "local units", "NetworkX", "average_shortest_path_length", "G_simple Graph", "weight = length", "mean geometry-length-weighted shortest-path distance"),
    ]
    for degree in sorted(degree_counts):
        rows.append(_metric(f"degree_count_{degree}", degree_counts[degree], "nodes", "custom", "Counter(Graph.degree)", "G_physical MultiGraph", "unweighted", f"nodes of physical degree {degree}"))
        rows.append(_metric(f"degree_proportion_{degree}", degree_proportions[degree], "proportion", "custom", "Counter(Graph.degree) / V", "G_physical MultiGraph", "unweighted", f"fraction of nodes of physical degree {degree}"))

    node_rows = tuple(
        {
            "node_id": node,
            "degree": degrees[node],
            "streets_per_node_osmnx": crosscheck["streets_per_node"][node],
            "degree_centrality": degree_centrality[node],
            "betweenness_unweighted": between_unweighted[node],
            "betweenness_length": between_length[node],
            "closeness_unweighted": close_unweighted[node],
            "closeness_length": close_length[node],
            "eccentricity_hops": eccentricity[node],
            "mean_distance_local": mean_distance_local[node],
            "is_articulation_point": node in articulation,
        }
        for node in sorted(degrees)
    )
    edge_rows = tuple(
        {
            "edge_id": edge_id,
            "u": str(edge_by_id[edge_id][2].get("canonical_u", edge_by_id[edge_id][0])),
            "v": str(edge_by_id[edge_id][2].get("canonical_v", edge_by_id[edge_id][1])),
            "key": int(edge_by_id[edge_id][2].get("canonical_key", edge_by_id[edge_id][2].get("key", 0))),
            "length_local": float(edge_by_id[edge_id][2][length]),
            "betweenness_unweighted": edge_unweighted_by_id[edge_id],
            "betweenness_length": edge_length_by_id[edge_id],
            "is_bridge": edge_id in bridges,
        }
        for edge_id in sorted(edge_by_id)
    )

    methodology = {
        "schema_version": SCHEMA_VERSION,
        "scope": "topological and network-structure metrics only",
        "graph_representations": {
            "G_physical": "undirected MultiGraph with one edge per canonical physical street",
            "G_ox": "MultiDiGraph with two reciprocal arcs per canonical physical street",
            "G_simple": "Graph created only after zero self-loop and zero parallel-edge assertions; node, edge, endpoint and canonical edge identities rechecked",
        },
        "authoritative_distance": {"attribute": length, "units": "local units", "strictly_positive": True},
        "connected_graph_policy": config.disconnected_policy,
        "centrality": {
            "degree": "NetworkX degree_centrality normalized by n-1",
            "betweenness_unweighted": "NetworkX betweenness_centrality with weight=None",
            "betweenness_length": "NetworkX betweenness_centrality with weight='length'; weight is path cost",
            "closeness_unweighted": "NetworkX closeness_centrality with distance=None",
            "closeness_length": "NetworkX closeness_centrality with distance='length'; units are 1 / local unit",
            "edge_betweenness": "NetworkX edge_betweenness_centrality mapped deterministically to canonical edge_id",
            "betweenness_normalized": config.centrality_normalized,
        },
        "networkx_functions_used": {
            "Graph.degree": "G_physical MultiGraph; physical incidence and handshake theorem",
            "number_connected_components / is_connected": "G_simple Graph; component identity and connected-only guard",
            "number_of_selfloops": "G_physical MultiGraph; simple-conversion safety",
            "bridges / articulation_points": "G_simple Graph; topological vulnerability with canonical ID mapping",
            "node_connectivity / edge_connectivity": "G_simple Graph; exact minimum disconnecting sets",
            "average_shortest_path_length": "G_simple Graph; weight=None for hops and weight='length' for local-unit distance",
            "diameter / radius / eccentricity": "G_simple Graph; unweighted hop distance",
            "single_source_dijkstra_path_length": "G_simple Graph; per-node mean distance with weight='length'",
            "degree_centrality": "G_simple Graph; degree divided by n-1",
            "betweenness_centrality": "G_simple Graph; weight=None and weight='length' as path cost",
            "closeness_centrality": "G_simple Graph; distance=None and distance='length'",
            "edge_betweenness_centrality": "G_simple Graph; weight=None and weight='length', then canonical edge_id mapping",
        },
        "osmnx_functions_used": {
            "stats.count_streets_per_node": "G_ox MultiDiGraph; physical street incidence per node",
            "stats.streets_per_node": "G_ox MultiDiGraph after explicit street_count population",
            "stats.streets_per_node_avg": "G_ox MultiDiGraph; average incidence",
            "stats.streets_per_node_counts": "G_ox MultiDiGraph; degree-frequency cross-check",
            "stats.streets_per_node_proportions": "G_ox MultiDiGraph; degree-proportion cross-check",
            "stats.intersection_count": f"G_ox MultiDiGraph; nodes with at least {config.osmnx_intersection_min_streets} streets",
            "stats.street_segment_count": "G_physical MultiGraph; 69 physical edges rather than 138 arcs",
            "stats.self_loop_proportion": "G_physical MultiGraph; physical self-loop fraction",
        },
        "osmnx_functions_not_used": {
            "stats.basic_stats": "not used because it bundles density and geometry semantics inappropriate for this local synthetic network",
            "stats.edge_length_total": "not used because OSMnx documents geographic/metric units while this network uses local units",
            "stats.street_length_total": "not used because Phase 7F defers geometric length summaries and this network uses local units",
            "distance.add_edge_lengths": "forbidden: would calculate great-circle distance on invented geographic coordinates",
            "bearing.add_edge_bearings": "forbidden: orientation belongs to Phase 7G and requires geographic assumptions",
        },
        "excluded_phase_7g_metrics": ["circuity", "edge chord geometry", "orientation", "orientation entropy", "orientation order", "rose diagrams", "curvature"],
        "software_versions": {
            "NetworkX": importlib.metadata.version("networkx"),
            "OSMnx": importlib.metadata.version("osmnx"),
        },
    }
    return TopologyCalculation(simple, tuple(rows), node_rows, edge_rows, crosscheck, methodology)


def _write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def publish_topology_metrics(
    selection: CanonicalSelection,
    calculation: TopologyCalculation,
    config: TopologyConfig,
    physical_graph: nx.MultiGraph,
    osmnx_graph: nx.MultiDiGraph,
    *,
    output_root: Path,
    project_root: Path = PROJECT_ROOT,
) -> tuple[Path, Path, dict[str, Any]]:
    """Publish stable metric tables beside, without replacing, Phase 7D provenance."""
    output_dir = Path(output_root) / selection.environment / selection.run_id / "topology"
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "topology_metrics_summary_csv": output_dir / "topology_metrics_summary.csv",
        "topology_metrics_summary_json": output_dir / "topology_metrics_summary.json",
        "node_topology_metrics_csv": output_dir / "node_topology_metrics.csv",
        "edge_topology_metrics_csv": output_dir / "edge_topology_metrics.csv",
        "osmnx_crosscheck_json": output_dir / "osmnx_crosscheck.json",
        "topology_methodology_json": output_dir / "topology_methodology.json",
    }
    _write_csv(paths["topology_metrics_summary_csv"], SUMMARY_FIELDS, calculation.summary_rows)
    write_json(paths["topology_metrics_summary_json"], {
        "schema_version": SCHEMA_VERSION,
        "metrics": list(calculation.summary_rows),
        "degree_counts": calculation.osmnx_crosscheck["streets_per_node_counts"],
        "degree_proportions": calculation.osmnx_crosscheck["streets_per_node_proportions"],
    })
    _write_csv(paths["node_topology_metrics_csv"], NODE_FIELDS, calculation.node_rows)
    _write_csv(paths["edge_topology_metrics_csv"], EDGE_FIELDS, calculation.edge_rows)
    write_json(paths["osmnx_crosscheck_json"], calculation.osmnx_crosscheck)
    methodology = {
        **calculation.methodology,
        "configuration": {
            "path": _display_path(config.path, project_root),
            "file_sha256": config.file_sha256,
            "schema_version": config.schema_version,
            "physical_length_attribute": config.physical_length_attribute,
            "osmnx_intersection_min_streets": config.osmnx_intersection_min_streets,
            "disconnected_policy": config.disconnected_policy,
            "centrality_normalized": config.centrality_normalized,
        },
    }
    write_json(paths["topology_methodology_json"], methodology)
    artifacts = {
        name: {"path": _display_path(path, project_root), "file_sha256": sha256_file(path)}
        for name, path in paths.items()
    }
    canonical_git = None if selection.manifest is None else selection.manifest.get("git_provenance")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "analysis_type": "Phase 7F topology metrics",
        "environment": selection.environment,
        "canonical_run_id": selection.run_id,
        "canonical": {
            "path": _display_path(selection.canonical_path, project_root),
            "file_sha256": selection.file_sha256,
            "scientific_content_signature": selection.scientific_content_signature,
            "topology_signature": selection.topology_signature,
            "publication_git_provenance": canonical_git,
        },
        "analysis_git_provenance": git_provenance(project_root),
        "metrics_configuration": {
            "path": _display_path(config.path, project_root),
            "file_sha256": config.file_sha256,
            "schema_version": config.schema_version,
        },
        "graph_identity": {
            "physical_graph_type": type(physical_graph).__name__,
            "node_count": physical_graph.number_of_nodes(),
            "physical_edge_count": physical_graph.number_of_edges(),
            "osmnx_graph_type": type(osmnx_graph).__name__,
            "directed_arc_count": osmnx_graph.number_of_edges(),
        },
        "software_environment": software_environment(),
        "artifacts": artifacts,
        "phase_7d_manifest_preserved": {},
    }
    phase_7d_manifest = Path(output_root) / selection.environment / selection.run_id / "analysis_graph_manifest.json"
    manifest["phase_7d_manifest_preserved"] = {
        "path": _display_path(phase_7d_manifest, project_root),
        "available": phase_7d_manifest.is_file(),
        "file_sha256": sha256_file(phase_7d_manifest) if phase_7d_manifest.is_file() else None,
    }
    manifest_path = output_dir / "topology_analysis_manifest.json"
    write_json(manifest_path, manifest)
    return output_dir, manifest_path, manifest


def analyze_topology(
    environment: str,
    *,
    canonical_run: str = "latest",
    canonical_path: Path | None = None,
    latest_path: Path | None = None,
    config_path: Path | None = None,
    output_root: Path | None = None,
    project_root: Path = PROJECT_ROOT,
    publish: bool = True,
) -> TopologyAnalysisResult:
    """Resolve, verify, adapt, calculate, and optionally publish topology metrics."""
    project_root = Path(project_root).resolve()
    selection = resolve_canonical_run(
        environment,
        canonical_run=canonical_run,
        canonical_path=canonical_path,
        latest_path=latest_path,
        project_root=project_root,
    )
    nodes, edges = load_canonical_geopackage(selection.canonical_path)
    canonical = validate_canonical_for_analysis(nodes, edges)
    physical = build_networkx_multigraph(canonical, selection)
    directed = build_osmnx_multidigraph(canonical, selection)
    validate_graph_correspondence(canonical, physical, directed, selection)
    config = load_topology_config(config_path, project_root=project_root)
    calculation = calculate_topology_metrics(physical, directed, config)
    chosen_output = Path(output_root) if output_root is not None else project_root / "results" / "analysis"
    if publish:
        output_dir, manifest_path, manifest = publish_topology_metrics(
            selection,
            calculation,
            config,
            physical,
            directed,
            output_root=chosen_output,
            project_root=project_root,
        )
    else:
        output_dir = chosen_output / environment / selection.run_id / "topology"
        manifest_path = output_dir / "topology_analysis_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    return TopologyAnalysisResult(selection, physical, directed, calculation, output_dir, manifest_path, manifest)
