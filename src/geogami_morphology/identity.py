"""Deterministic scientific and network identity helpers.

Scientific-content serialization is UTF-8 JSON with sorted object keys and no
insignificant whitespace. Nodes are ordered by ``node_id``; edges are ordered by
``(edge_id, u, v, key)``. Coordinates use Python ``float.hex()`` so the exact
binary floating-point values and LineString direction are represented without
decimal-formatting ambiguity.

Included fields are the CRS WKT, node IDs and Point coordinates, and edge IDs,
``u``, ``v``, ``key`` and ordered LineString coordinate sequences. Derived
attributes (x, y, degree, length and vertex count), GeoPackage FIDs, and Phase 5
source-FID provenance are deliberately excluded.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from typing import Any

import geopandas as gpd
import networkx as nx
from shapely.geometry import LineString, Point

from .validation import topology_signature, normalized_tuple


SCIENTIFIC_CONTENT_SCHEMA_VERSION = "1.0.0"
SCIENTIFIC_CONTENT_SERIALIZATION = (
    "UTF-8 canonical JSON; sorted object keys; nodes by node_id; edges by "
    "(edge_id,u,v,key); exact float.hex coordinates; ordered geometry vertices"
)


def _hex_coordinate(coordinate: Any) -> list[str]:
    return [float(component).hex() for component in coordinate]


def scientific_content_document(
    nodes: gpd.GeoDataFrame, edges: gpd.GeoDataFrame
) -> dict[str, Any]:
    missing_nodes = {"node_id"} - set(nodes.columns)
    missing_edges = {"edge_id", "u", "v", "key"} - set(edges.columns)
    if missing_nodes or missing_edges:
        raise ValueError(
            f"Scientific signature fields missing: nodes={sorted(missing_nodes)}, "
            f"edges={sorted(missing_edges)}"
        )
    if nodes.crs is None or edges.crs is None or nodes.crs != edges.crs:
        raise ValueError("Scientific signature requires one identical declared CRS for both layers.")

    node_records: list[dict[str, Any]] = []
    for row in nodes.sort_values("node_id", kind="stable").itertuples():
        geometry = row.geometry
        if not isinstance(geometry, Point) or geometry.is_empty:
            raise ValueError(f"Node {row.node_id} is not a non-empty Point.")
        node_records.append(
            {
                "node_id": str(row.node_id),
                "coordinates": _hex_coordinate(geometry.coords[0]),
            }
        )

    edge_records: list[dict[str, Any]] = []
    ordered_edges = edges.assign(
        __edge_id=edges["edge_id"].astype(str),
        __u=edges["u"].astype(str),
        __v=edges["v"].astype(str),
        __key=edges["key"].map(int),
    ).sort_values(["__edge_id", "__u", "__v", "__key"], kind="stable")
    for row in ordered_edges.itertuples():
        geometry = row.geometry
        if not isinstance(geometry, LineString) or geometry.is_empty:
            raise ValueError(f"Edge {row.edge_id} is not a non-empty LineString.")
        edge_records.append(
            {
                "edge_id": str(row.edge_id),
                "u": str(row.u),
                "v": str(row.v),
                "key": int(row.key),
                "coordinates": [_hex_coordinate(coordinate) for coordinate in geometry.coords],
            }
        )

    return {
        "schema_version": SCIENTIFIC_CONTENT_SCHEMA_VERSION,
        "crs_wkt": nodes.crs.to_wkt(),
        "nodes": node_records,
        "edges": edge_records,
    }


def scientific_content_signature(
    nodes: gpd.GeoDataFrame, edges: gpd.GeoDataFrame
) -> tuple[str, str]:
    document = scientific_content_document(nodes, edges)
    serialized = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest().upper()
    return digest, serialized


def network_identity(nodes: gpd.GeoDataFrame, edges: gpd.GeoDataFrame) -> dict[str, Any]:
    node_ids = nodes["node_id"].astype(str).tolist()
    graph = nx.MultiGraph()
    graph.add_nodes_from(node_ids)
    tuples: list[tuple[str, str, int]] = []
    for row in edges.itertuples():
        u, v, key = str(row.u), str(row.v), int(row.key)
        graph.add_edge(u, v, key=f"{key}:{row.edge_id}")
        tuples.append(normalized_tuple(u, v, key))
    topology_digest, _ = topology_signature(tuples)
    scientific_digest, _ = scientific_content_signature(nodes, edges)
    return {
        "crs": str(nodes.crs),
        "crs_wkt": nodes.crs.to_wkt() if nodes.crs else None,
        "coordinate_system": "GeoGami Local Cartesian",
        "coordinate_units": "local units",
        "node_count": len(nodes),
        "physical_edge_count": len(edges),
        "component_count": nx.number_connected_components(graph) if graph.number_of_nodes() else 0,
        "degree_distribution": {
            str(degree): count
            for degree, count in sorted(Counter(dict(graph.degree()).values()).items())
        },
        "total_geometry_length_local": math.fsum(float(geometry.length) for geometry in edges.geometry),
        "topology_signature": topology_digest,
        "scientific_content_signature": scientific_digest,
    }
