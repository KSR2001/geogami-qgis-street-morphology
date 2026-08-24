"""Generic preserve-topology identity, graph, and geometry validation."""

from __future__ import annotations

from collections import Counter
import hashlib
from typing import Any, Iterable

import networkx as nx
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPoint, Point
from shapely.strtree import STRtree


TOPOLOGY_SERIALIZATION = "UTF-8 lines, sorted normalized tuples as u|v|key followed by LF; no header"


def normalized_tuple(u: Any, v: Any, key: Any) -> tuple[str, str, int]:
    first, second = str(u), str(v)
    integer_key = int(key)
    return (first, second, integer_key) if first <= second else (second, first, integer_key)


def topology_signature(tuples: Iterable[tuple[str, str, int]]) -> tuple[str, str]:
    text = "".join(f"{u}|{v}|{key}\n" for u, v, key in sorted(tuples))
    return hashlib.sha256(text.encode("utf-8")).hexdigest().upper(), text


def graph_statistics(node_ids: Iterable[str], edge_records: Iterable[tuple[str, str, int]]) -> dict[str, Any]:
    graph = nx.MultiGraph()
    graph.add_nodes_from(node_ids)
    tuples = list(edge_records)
    for index, (u, v, key) in enumerate(tuples):
        graph.add_edge(u, v, key=f"{key}:{index}")
    degree_distribution = dict(sorted(Counter(dict(graph.degree()).values()).items()))
    signature, _ = topology_signature(normalized_tuple(u, v, key) for u, v, key in tuples)
    return {
        "node_count": graph.number_of_nodes(),
        "edge_count": graph.number_of_edges(),
        "connected_components": nx.number_connected_components(graph) if graph.number_of_nodes() else 0,
        "degree_distribution": degree_distribution,
        "topology_signature": signature,
        "degrees": dict(graph.degree()),
    }


def geometry_parts(geometry: Any, kind: type) -> Iterable[Any]:
    if geometry is None or geometry.is_empty:
        return
    if isinstance(geometry, kind):
        yield geometry
    elif isinstance(geometry, (GeometryCollection, MultiPoint, MultiLineString)):
        for part in geometry.geoms:
            yield from geometry_parts(part, kind)


def intersection_errors(
    edge_geometries: dict[str, LineString],
    edge_endpoints: dict[str, tuple[str, str]],
    node_points: dict[str, Point],
) -> list[dict[str, Any]]:
    """Reject every intersection except the exact prescribed shared node."""
    errors: list[dict[str, Any]] = []
    edge_ids = sorted(edge_geometries)
    geometries = [edge_geometries[edge_id] for edge_id in edge_ids]
    tree = STRtree(geometries)
    for index, first_id in enumerate(edge_ids):
        first = geometries[index]
        # The spatial index avoids expensive GEOS intersections for the many
        # edge pairs whose envelopes are disjoint.
        for second_index in sorted(int(value) for value in tree.query(first) if int(value) > index):
            second_id = edge_ids[second_index]
            second = geometries[second_index]
            intersection = first.intersection(second)
            if intersection.is_empty:
                continue
            shared = set(edge_endpoints[first_id]) & set(edge_endpoints[second_id])
            allowed_points = {node_points[node_id].wkb for node_id in shared}
            line_parts = list(geometry_parts(intersection, LineString))
            point_parts = list(geometry_parts(intersection, Point))
            invalid_points = [point for point in point_parts if point.wkb not in allowed_points]
            if line_parts or invalid_points or not point_parts:
                errors.append(
                    {
                        "domain": "geometry",
                        "issue_type": "unintended_edge_intersection",
                        "edge_id": first_id,
                        "edge_id_2": second_id,
                        "message": intersection.wkt,
                    }
                )
    return errors
