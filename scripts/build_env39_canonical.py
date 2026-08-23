"""Build the frozen canonical Env39 graph from immutable network_v2.gpkg.

Phase 5B suppresses exactly seven scientifically approved degree-2 nodes. The
source is opened read-only, exact endpoint tuples define topology, and no
tolerance is used to construct or merge graph nodes.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely.geometry import LineString, Point

import audit_env39_geometry as phase5a


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "data" / "baselines" / "grid" / "network_v2.gpkg"
DEFAULT_CANONICAL_GPKG = PROJECT_ROOT / "data" / "canonical" / "grid" / "env39_canonical.gpkg"
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "results" / "baselines" / "grid" / "canonical"
SOURCE_LAYER = "edges"
EXPECTED_SOURCE_SHA256 = "7698544A01EED008E5451F8368703AFD0DBE232D10C9D7ADAA58C13DA57E0289"
EXPECTED_SOURCE_LENGTH = 10945.657681153116
EXPECTED_CANONICAL_NODE_COUNT = 46
EXPECTED_CANONICAL_EDGE_COUNT = 69
EXPECTED_CANONICAL_DEGREES = {1: 10, 3: 16, 4: 20}
LENGTH_ABSOLUTE_TOLERANCE = 1e-9
TOPOLOGY_SERIALIZATION = "UTF-8 lines, one normalized tuple per line as u|v|key\\n, sorted lexicographically by (u,v,key), no header"


@dataclass(frozen=True)
class SuppressionApproval:
    temporary_node_id: str
    coordinate: tuple[float, float]
    expected_source_fids: tuple[int, int]
    classification: str
    scientific_decision: str = "approved_for_canonical_suppression"


APPROVED_SUPPRESSIONS = (
    SuppressionApproval("tmp_node_010", (162.17220703245619, 139.73570825812487), (35, 42), "angular_bend"),
    SuppressionApproval("tmp_node_015", (338.86501833193904, 1072.6503559407909), (8, 12), "angular_bend"),
    SuppressionApproval("tmp_node_018", (458.5779871431296, 762.8344262635142), (30, 31), "straight_continuation"),
    SuppressionApproval("tmp_node_033", (723.4098306454775, 387.24328503832237), (47, 72), "straight_continuation"),
    SuppressionApproval("tmp_node_035", (723.4098306454775, 635.014685751152), (46, 76), "straight_continuation"),
    SuppressionApproval("tmp_node_037", (723.4098306454775, 762.1306686397638), (45, 74), "straight_continuation"),
    SuppressionApproval("tmp_node_048", (863.2286215961212, 237.95184909085344), (59, 60), "straight_continuation"),
)


@dataclass
class CanonicalBuild:
    source_frame: gpd.GeoDataFrame
    source_graph: nx.MultiGraph
    canonical_graph: nx.MultiGraph
    node_records: list[dict[str, Any]]
    edge_records: list[dict[str, Any]]
    edge_geometries: dict[str, LineString]
    suppression_records: list[dict[str, Any]]
    duplicate_coordinates_removed: int
    topology_signature_sha256: str
    topology_signature_text: str
    source_total_length: float
    canonical_total_length: float
    validation: dict[str, Any]


def _coord(value: Iterable[float]) -> tuple[float, ...]:
    return tuple(float(component) for component in value)


def _fid_sort_key(fid: Any) -> tuple[int, Any]:
    if isinstance(fid, int) and not isinstance(fid, bool):
        return (0, fid)
    if hasattr(fid, "item"):
        item = fid.item()
        if isinstance(item, int) and not isinstance(item, bool):
            return (0, item)
    return (1, str(fid))


def remove_consecutive_duplicate_coordinates(
    coordinates: Iterable[Iterable[float]],
) -> tuple[tuple[tuple[float, ...], ...], int]:
    cleaned: list[tuple[float, ...]] = []
    removed = 0
    for raw_coordinate in coordinates:
        coordinate = _coord(raw_coordinate)
        if cleaned and coordinate == cleaned[-1]:
            removed += 1
        else:
            cleaned.append(coordinate)
    return tuple(cleaned), removed


def build_source_graph(
    frame: gpd.GeoDataFrame,
) -> tuple[nx.MultiGraph, int, float]:
    graph = nx.MultiGraph()
    removed_total = 0
    source_lengths: list[float] = []
    for fid, row in frame.sort_index().iterrows():
        geometry = row.geometry
        if geometry is None or geometry.is_empty or geometry.geom_type != "LineString":
            raise ValueError(f"Source FID {fid} is not a non-empty LineString.")
        raw_coordinates = tuple(_coord(coordinate) for coordinate in geometry.coords)
        cleaned_coordinates, removed = remove_consecutive_duplicate_coordinates(raw_coordinates)
        if len(cleaned_coordinates) < 2:
            raise ValueError(f"Source FID {fid} has fewer than two distinct consecutive coordinates.")
        start, end = raw_coordinates[0], raw_coordinates[-1]
        if cleaned_coordinates[0] != start or cleaned_coordinates[-1] != end:
            raise AssertionError("Consecutive duplicate cleanup unexpectedly changed an endpoint.")
        length = float(geometry.length)
        cleaned_length = float(LineString(cleaned_coordinates).length)
        if not math.isclose(length, cleaned_length, rel_tol=0.0, abs_tol=LENGTH_ABSOLUTE_TOLERANCE):
            raise ValueError(f"Duplicate-coordinate cleanup changed FID {fid} length by {cleaned_length - length}.")
        graph.add_edge(
            start,
            end,
            key=int(fid),
            source_fid=int(fid),
            raw_coordinates=raw_coordinates,
            coordinates=cleaned_coordinates,
            source_length=length,
            source_attributes={
                str(column): (None if pd.isna(value) else value.item() if hasattr(value, "item") else value)
                for column, value in row.items()
                if column != "geometry"
            },
        )
        removed_total += removed
        source_lengths.append(length)
    return graph, removed_total, math.fsum(source_lengths)


def _incident_edges(graph: nx.MultiGraph, node: tuple[float, ...]) -> list[tuple[tuple[float, ...], tuple[float, ...], Any, dict[str, Any]]]:
    edges = list(graph.edges(node, keys=True, data=True))
    edges.sort(key=lambda edge: (_fid_sort_key(edge[3]["source_fid"]), edge[2]))
    return edges


def _other_endpoint(
    edge: tuple[tuple[float, ...], tuple[float, ...], Any, dict[str, Any]],
    node: tuple[float, ...],
) -> tuple[float, ...]:
    first, second = edge[0], edge[1]
    if first == node and second != node:
        return second
    if second == node and first != node:
        return first
    raise ValueError("Self-loop or incident-edge inconsistency encountered during suppression.")


def _orient_coordinates(
    coordinates: tuple[tuple[float, ...], ...],
    start: tuple[float, ...],
    end: tuple[float, ...],
) -> tuple[tuple[float, ...], ...]:
    if coordinates[0] == start and coordinates[-1] == end:
        return coordinates
    if coordinates[0] == end and coordinates[-1] == start:
        return tuple(reversed(coordinates))
    raise ValueError("Source geometry endpoints disagree with exact graph endpoints.")


def verify_approvals(graph: nx.MultiGraph) -> dict[tuple[float, ...], SuppressionApproval]:
    approved = {tuple(item.coordinate): item for item in APPROVED_SUPPRESSIONS}
    observed_degree2 = {node for node, degree in graph.degree() if degree == 2}
    if observed_degree2 != set(approved):
        missing = sorted(set(approved) - observed_degree2)
        unexpected = sorted(observed_degree2 - set(approved))
        raise ValueError(f"Approved degree-2 set contradicts source graph; missing={missing}, unexpected={unexpected}")
    for coordinate, approval in approved.items():
        incident_fids = tuple(
            sorted((int(edge[3]["source_fid"]) for edge in _incident_edges(graph, coordinate)))
        )
        if incident_fids != approval.expected_source_fids:
            raise ValueError(
                f"{approval.temporary_node_id} provenance changed: expected FIDs "
                f"{approval.expected_source_fids}, observed {incident_fids}."
            )
    return approved


def _path_candidates(
    source_graph: nx.MultiGraph,
    approved: dict[tuple[float, ...], SuppressionApproval],
) -> list[dict[str, Any]]:
    surviving_nodes = set(source_graph.nodes) - set(approved)
    visited: set[Any] = set()
    candidates: list[dict[str, Any]] = []
    for path_start in sorted(surviving_nodes):
        for initial_edge in _incident_edges(source_graph, path_start):
            initial_key = initial_edge[2]
            if initial_key in visited:
                continue
            current_node = path_start
            current_edge = initial_edge
            path_coordinates: list[tuple[float, ...]] = []
            source_fids_ordered: list[int] = []
            suppressed_coordinates: list[tuple[float, ...]] = []
            while True:
                edge_key = current_edge[2]
                if edge_key in visited:
                    raise ValueError("Traversal encountered an already consumed source edge.")
                visited.add(edge_key)
                next_node = _other_endpoint(current_edge, current_node)
                data = current_edge[3]
                oriented = _orient_coordinates(data["coordinates"], current_node, next_node)
                if not path_coordinates:
                    path_coordinates.extend(oriented)
                else:
                    if path_coordinates[-1] != oriented[0]:
                        raise ValueError("Exact geometry continuity failed during edge concatenation.")
                    path_coordinates.extend(oriented[1:])
                source_fids_ordered.append(int(data["source_fid"]))
                if next_node in surviving_nodes:
                    path_end = next_node
                    break
                if next_node not in approved:
                    raise ValueError("Traversal reached a non-surviving, non-approved node.")
                suppressed_coordinates.append(next_node)
                remaining = [edge for edge in _incident_edges(source_graph, next_node) if edge[2] not in visited]
                if len(remaining) != 1:
                    raise ValueError(
                        f"Approved node {approved[next_node].temporary_node_id} did not have exactly one onward edge."
                    )
                current_node = next_node
                current_edge = remaining[0]

            final_coordinates, removed_after_concat = remove_consecutive_duplicate_coordinates(path_coordinates)
            if removed_after_concat:
                raise ValueError("Concatenation introduced consecutive duplicate coordinates unexpectedly.")
            candidates.append(
                {
                    "start_coordinate": path_start,
                    "end_coordinate": path_end,
                    "coordinates": final_coordinates,
                    "source_fids_ordered": source_fids_ordered,
                    "suppressed_coordinates": suppressed_coordinates,
                }
            )
    expected_edge_keys = {key for _u, _v, key in source_graph.edges(keys=True)}
    if visited != expected_edge_keys:
        raise ValueError(f"Canonical traversal did not consume every source edge: {sorted(expected_edge_keys - visited)}")
    return candidates


def assign_canonical_node_ids(nodes: Iterable[tuple[float, ...]]) -> dict[tuple[float, ...], str]:
    ordered = sorted(nodes, key=lambda coordinate: (-coordinate[1], coordinate[0], coordinate[2:]))
    return {coordinate: f"N{index:03d}" for index, coordinate in enumerate(ordered, start=1)}


def assign_canonical_edges(
    candidates: list[dict[str, Any]],
    node_ids: dict[tuple[float, ...], str],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for candidate in candidates:
        start = candidate["start_coordinate"]
        end = candidate["end_coordinate"]
        start_id = node_ids[start]
        end_id = node_ids[end]
        coordinates = candidate["coordinates"]
        ordered_fids = list(candidate["source_fids_ordered"])
        if start_id <= end_id:
            u, v = start_id, end_id
            oriented_coordinates = coordinates
            oriented_fids = ordered_fids
        else:
            u, v = end_id, start_id
            oriented_coordinates = tuple(reversed(coordinates))
            oriented_fids = list(reversed(ordered_fids))
        normalized.append(
            {
                "u": u,
                "v": v,
                "coordinates": oriented_coordinates,
                "source_fids_ordered": oriented_fids,
                "source_fids": sorted(ordered_fids),
                "suppressed_coordinates": list(candidate["suppressed_coordinates"]),
            }
        )

    by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate in normalized:
        by_pair[(candidate["u"], candidate["v"])].append(candidate)
    for pair, members in by_pair.items():
        members.sort(key=lambda item: item["coordinates"])
        for key, member in enumerate(members):
            member["key"] = key

    normalized.sort(key=lambda item: (item["u"], item["v"], item["key"]))
    for index, record in enumerate(normalized, start=1):
        record["edge_id"] = f"E{index:03d}"
        record["length_local"] = float(LineString(record["coordinates"]).length)
    return normalized


def topology_signature(edge_records: list[dict[str, Any]]) -> tuple[str, str]:
    tuples = sorted((record["u"], record["v"], int(record["key"])) for record in edge_records)
    serialized = "".join(f"{u}|{v}|{key}\n" for u, v, key in tuples)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest().upper()
    return digest, serialized


def _canonical_content_signature(
    node_records: list[dict[str, Any]], edge_records: list[dict[str, Any]]
) -> str:
    content = {
        "nodes": node_records,
        "edges": [
            {
                **{key: value for key, value in record.items() if key not in {"suppressed_coordinates"}},
                "coordinates": [list(coordinate) for coordinate in record["coordinates"]],
            }
            for record in edge_records
        ],
    }
    serialized = json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest().upper()


def build_canonical(source_path: Path = DEFAULT_SOURCE) -> CanonicalBuild:
    if phase5a.sha256_file(source_path) != EXPECTED_SOURCE_SHA256:
        raise ValueError("Immutable source SHA-256 does not match the frozen Phase 5A hash.")
    phase5a_result = phase5a.audit_source(source_path, SOURCE_LAYER)
    phase5a.finalize_result(phase5a_result)
    if phase5a_result.report["final_result"] != "PASS":
        raise ValueError("Phase 5A source audit no longer passes; canonicalization stopped.")
    source_frame = phase5a_result.source_frame
    source_graph, duplicates_removed, source_total = build_source_graph(source_frame)
    if not math.isclose(source_total, EXPECTED_SOURCE_LENGTH, rel_tol=0.0, abs_tol=LENGTH_ABSOLUTE_TOLERANCE):
        raise ValueError(f"Source length changed: expected {EXPECTED_SOURCE_LENGTH}, observed {source_total}.")
    approved = verify_approvals(source_graph)
    candidates = _path_candidates(source_graph, approved)
    surviving_nodes = set(source_graph.nodes) - set(approved)
    node_ids = assign_canonical_node_ids(surviving_nodes)
    canonical_edges = assign_canonical_edges(candidates, node_ids)

    canonical_graph = nx.MultiGraph()
    id_to_coordinate = {node_id: coordinate for coordinate, node_id in node_ids.items()}
    for node_id in sorted(id_to_coordinate):
        canonical_graph.add_node(node_id, coordinate=id_to_coordinate[node_id])
    edge_geometries: dict[str, LineString] = {}
    for record in canonical_edges:
        geometry = LineString(record["coordinates"])
        edge_geometries[record["edge_id"]] = geometry
        canonical_graph.add_edge(record["u"], record["v"], key=record["key"], edge_id=record["edge_id"])

    source_incident_fids: dict[tuple[float, ...], list[int]] = defaultdict(list)
    for node in surviving_nodes:
        source_incident_fids[node] = sorted(
            (int(edge[3]["source_fid"]) for edge in _incident_edges(source_graph, node))
        )
    node_records = [
        {
            "node_id": node_id,
            "x": id_to_coordinate[node_id][0],
            "y": id_to_coordinate[node_id][1],
            "degree": int(canonical_graph.degree(node_id)),
            "incident_source_fids": ";".join(str(fid) for fid in source_incident_fids[id_to_coordinate[node_id]]),
        }
        for node_id in sorted(id_to_coordinate)
    ]

    suppression_records: list[dict[str, Any]] = []
    for approval in APPROVED_SUPPRESSIONS:
        coordinate = tuple(approval.coordinate)
        matching_edges = [
            record for record in canonical_edges if coordinate in record["suppressed_coordinates"]
        ]
        if len(matching_edges) != 1:
            raise ValueError(f"Could not map {approval.temporary_node_id} to one canonical edge.")
        resulting_edge = matching_edges[0]
        geometry_vertex_preserved = coordinate in resulting_edge["coordinates"][1:-1]
        suppression_records.append(
            {
                "source_tmp_node_id": approval.temporary_node_id,
                "x": coordinate[0],
                "y": coordinate[1],
                "original_degree": 2,
                "source_fid_1": approval.expected_source_fids[0],
                "source_fid_2": approval.expected_source_fids[1],
                "classification": approval.classification,
                "scientific_decision": approval.scientific_decision,
                "resulting_canonical_edge": resulting_edge["edge_id"],
                "geometry_vertex_preserved": geometry_vertex_preserved,
            }
        )

    signature, signature_text = topology_signature(canonical_edges)
    canonical_total = math.fsum(record["length_local"] for record in canonical_edges)
    length_difference = canonical_total - source_total
    degree_distribution = dict(sorted(Counter(dict(canonical_graph.degree()).values()).items()))
    normalized_tuples = [(record["u"], record["v"], record["key"]) for record in canonical_edges]
    pair_counts = Counter((record["u"], record["v"]) for record in canonical_edges)
    geometry_errors: list[str] = []
    for record in canonical_edges:
        geometry = edge_geometries[record["edge_id"]]
        u_coordinate = id_to_coordinate[record["u"]]
        v_coordinate = id_to_coordinate[record["v"]]
        if geometry.is_empty or not geometry.is_valid or geometry.geom_type != "LineString" or geometry.length <= 0:
            geometry_errors.append(f"{record['edge_id']}: invalid/empty/zero-length LineString")
        coordinates = tuple(_coord(coordinate) for coordinate in geometry.coords)
        if coordinates[0] != u_coordinate or coordinates[-1] != v_coordinate:
            geometry_errors.append(f"{record['edge_id']}: geometry endpoints disagree with u/v")
        if any(first == second for first, second in zip(coordinates, coordinates[1:])):
            geometry_errors.append(f"{record['edge_id']}: consecutive duplicate coordinates")

    checks = {
        "source_feature_count_76": len(source_frame) == 76,
        "source_exact_node_count_53": source_graph.number_of_nodes() == 53,
        "approved_suppressions_7": len(suppression_records) == 7,
        "canonical_node_count_46": canonical_graph.number_of_nodes() == EXPECTED_CANONICAL_NODE_COUNT,
        "canonical_edge_count_69": canonical_graph.number_of_edges() == EXPECTED_CANONICAL_EDGE_COUNT,
        "single_component": nx.number_connected_components(canonical_graph) == 1,
        "degree_distribution": degree_distribution == EXPECTED_CANONICAL_DEGREES,
        "degree2_count_zero": degree_distribution.get(2, 0) == 0,
        "handshake": sum(dict(canonical_graph.degree()).values()) == 2 * canonical_graph.number_of_edges() == 138,
        "no_isolated_nodes": all(degree > 0 for _node, degree in canonical_graph.degree()),
        "no_self_loops": nx.number_of_selfloops(canonical_graph) == 0,
        "no_parallel_edges": all(count == 1 for count in pair_counts.values()),
        "unique_normalized_edge_tuples": len(normalized_tuples) == len(set(normalized_tuples)),
        "canonical_geometry_valid": not geometry_errors,
        "all_suppressed_vertices_preserved": all(record["geometry_vertex_preserved"] for record in suppression_records),
        "length_preserved": abs(length_difference) <= LENGTH_ABSOLUTE_TOLERANCE,
        "local_cartesian_crs_preserved": source_frame.crs is not None and not source_frame.crs.is_geographic,
    }
    failed_checks = [name for name, passed in checks.items() if not passed]
    if failed_checks:
        raise ValueError(f"Canonical validation contradicted expectations: {failed_checks}; geometry_errors={geometry_errors}")

    serializable_edges = [
        {
            "edge_id": record["edge_id"],
            "u": record["u"],
            "v": record["v"],
            "key": record["key"],
            "source_fids": ";".join(str(fid) for fid in record["source_fids"]),
            "source_fids_ordered": ";".join(str(fid) for fid in record["source_fids_ordered"]),
            "source_edge_count": len(record["source_fids"]),
            "length_local": record["length_local"],
            "vertex_count": len(record["coordinates"]),
            "coordinates": [list(coordinate) for coordinate in record["coordinates"]],
        }
        for record in canonical_edges
    ]
    content_signature = _canonical_content_signature(node_records, canonical_edges)
    validation = {
        "phase": "5B",
        "final_result": "PASS",
        "source": {
            "path": "data/baselines/grid/network_v2.gpkg",
            "layer": SOURCE_LAYER,
            "expected_sha256": EXPECTED_SOURCE_SHA256,
            "sha256_before": EXPECTED_SOURCE_SHA256,
            "sha256_after": EXPECTED_SOURCE_SHA256,
            "unchanged": True,
            "feature_count": len(source_frame),
            "exact_endpoint_node_count": source_graph.number_of_nodes(),
        },
        "canonical_topology": {
            "node_count": canonical_graph.number_of_nodes(),
            "edge_count": canonical_graph.number_of_edges(),
            "component_count": nx.number_connected_components(canonical_graph),
            "degree_distribution": {str(key): value for key, value in degree_distribution.items()},
            "handshake_degree_sum": sum(dict(canonical_graph.degree()).values()),
            "twice_edge_count": 2 * canonical_graph.number_of_edges(),
        },
        "geometry": {
            "consecutive_duplicate_coordinates_removed_from_derived_geometry": duplicates_removed,
            "source_total_length": source_total,
            "canonical_total_length": canonical_total,
            "absolute_difference": abs(length_difference),
            "relative_difference": abs(length_difference) / source_total,
            "absolute_tolerance_local_units": LENGTH_ABSOLUTE_TOLERANCE,
            "geometry_errors": geometry_errors,
            "tolerance_based_simplification": False,
        },
        "canonical_ids": {
            "node_ordering": "descending exact Y, then ascending exact X; IDs N001..N046",
            "edge_ordering": "normalize canonical node IDs so u<=v; assign deterministic parallel key; sort (u,v,key); IDs E001..E069",
            "qgis_fids_are_canonical_identifiers": False,
        },
        "topology_signature": {
            "serialization_convention": TOPOLOGY_SERIALIZATION,
            "topology_signature_sha256": signature,
        },
        "canonical_scientific_content_sha256": content_signature,
        "determinism": {
            "json_csv_expectation": "byte-identical for identical source input",
            "geopackage_criterion": "byte identity is tested after stabilizing derived gpkg_contents timestamps; scientific content remains the authoritative criterion",
            "scientific_content_fields": "layer schemas, canonical IDs, coordinates, connectivity, exact geometry coordinate sequences, CRS, and attributes",
        },
        "checks": checks,
    }
    return CanonicalBuild(
        source_frame=source_frame,
        source_graph=source_graph,
        canonical_graph=canonical_graph,
        node_records=node_records,
        edge_records=serializable_edges,
        edge_geometries=edge_geometries,
        suppression_records=suppression_records,
        duplicate_coordinates_removed=duplicates_removed,
        topology_signature_sha256=signature,
        topology_signature_text=signature_text,
        source_total_length=source_total,
        canonical_total_length=canonical_total,
        validation=validation,
    )


def _write_csv(path: Path, records: list[dict[str, Any]], columns: list[str] | None = None) -> None:
    pd.DataFrame(records, columns=columns).to_csv(path, index=False, lineterminator="\n")


def _stabilize_gpkg(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE gpkg_contents SET last_change='2000-01-01T00:00:00.000Z'")
        connection.commit()
        connection.execute("VACUUM")


def write_artifacts(
    build: CanonicalBuild,
    canonical_gpkg: Path = DEFAULT_CANONICAL_GPKG,
    results_dir: Path = DEFAULT_RESULTS_DIR,
) -> dict[str, Path]:
    canonical_gpkg.parent.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "canonical_gpkg": canonical_gpkg,
        "canonical_nodes_csv": results_dir / "canonical_nodes.csv",
        "canonical_edges_csv": results_dir / "canonical_edges.csv",
        "canonical_graph_json": results_dir / "canonical_graph.json",
        "degree2_suppression_log_csv": results_dir / "degree2_suppression_log.csv",
        "canonical_validation_json": results_dir / "canonical_validation.json",
    }
    _write_csv(paths["canonical_nodes_csv"], build.node_records)
    edge_csv_records = [
        {**{key: value for key, value in record.items() if key != "coordinates"}, "geometry_wkt": build.edge_geometries[record["edge_id"]].wkt}
        for record in build.edge_records
    ]
    _write_csv(paths["canonical_edges_csv"], edge_csv_records)
    _write_csv(paths["degree2_suppression_log_csv"], build.suppression_records)

    graph_document = {
        "metadata": {
            "phase": "5B",
            "graph_type": "undirected canonical street MultiGraph",
            "crs": str(build.source_frame.crs),
            "node_identity": "canonical N IDs assigned by descending exact Y then ascending exact X",
            "edge_identity": "canonical E IDs assigned after normalized (u,v,key) ordering",
            "source_fids_role": "provenance only",
            "topology_signature_serialization": TOPOLOGY_SERIALIZATION,
            "topology_signature_sha256": build.topology_signature_sha256,
        },
        "nodes": build.node_records,
        "edges": build.edge_records,
    }
    with paths["canonical_graph_json"].open("w", encoding="utf-8", newline="\n") as file:
        json.dump(graph_document, file, indent=2, ensure_ascii=False, allow_nan=False)
        file.write("\n")
    with paths["canonical_validation_json"].open("w", encoding="utf-8", newline="\n") as file:
        json.dump(build.validation, file, indent=2, ensure_ascii=False, allow_nan=False)
        file.write("\n")

    if canonical_gpkg.exists():
        canonical_gpkg.unlink()
    crs = build.source_frame.crs
    nodes_gdf = gpd.GeoDataFrame(
        build.node_records,
        geometry=[Point(record["x"], record["y"]) for record in build.node_records],
        crs=crs,
    )
    edges_gdf = gpd.GeoDataFrame(
        [
            {
                **{key: value for key, value in record.items() if key != "coordinates"},
                "geometry": build.edge_geometries[record["edge_id"]],
            }
            for record in build.edge_records
        ],
        geometry="geometry",
        crs=crs,
    )
    nodes_gdf.to_file(canonical_gpkg, layer="nodes", driver="GPKG", index=False)
    edges_gdf.to_file(canonical_gpkg, layer="edges", driver="GPKG", mode="a", index=False)
    _stabilize_gpkg(canonical_gpkg)
    return paths


def print_summary(build: CanonicalBuild, paths: dict[str, Path]) -> None:
    topology = build.validation["canonical_topology"]
    geometry = build.validation["geometry"]
    print("=" * 58)
    print("GeoGami Env39 - Phase 5B Canonical Graph")
    print("=" * 58)
    print(f"Source SHA-256 before: {build.validation['source']['sha256_before']}")
    print(f"Source SHA-256 after:  {build.validation['source']['sha256_after']}")
    print(f"Source unchanged:      {build.validation['source']['unchanged']}")
    print(f"Suppressed degree-2 nodes: {len(build.suppression_records)}")
    print(f"Canonical nodes / edges / components: {topology['node_count']} / {topology['edge_count']} / {topology['component_count']}")
    print(f"Degree distribution: {topology['degree_distribution']}")
    print(f"Handshake: {topology['handshake_degree_sum']} = {topology['twice_edge_count']}")
    print(f"Source/canonical length: {geometry['source_total_length']} / {geometry['canonical_total_length']}")
    print(f"Absolute length difference: {geometry['absolute_difference']}")
    print(f"Derived consecutive duplicates removed: {build.duplicate_coordinates_removed}")
    print(f"Topology signature SHA-256: {build.topology_signature_sha256}")
    print("FINAL RESULT: PASS")
    print("Artifacts:")
    for name, path in paths.items():
        print(f"  {name}: {path}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--canonical-gpkg", type=Path, default=DEFAULT_CANONICAL_GPKG)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    hash_before = phase5a.sha256_file(args.source)
    if hash_before != EXPECTED_SOURCE_SHA256:
        raise RuntimeError(f"Frozen source hash mismatch before execution: {hash_before}")
    build = build_canonical(args.source)
    hash_after_analysis = phase5a.sha256_file(args.source)
    if hash_after_analysis != hash_before:
        raise RuntimeError("Frozen source hash changed during canonical analysis; no artifacts written.")
    build.validation["source"]["sha256_before"] = hash_before
    build.validation["source"]["sha256_after"] = hash_after_analysis
    build.validation["source"]["unchanged"] = True
    paths = write_artifacts(build, args.canonical_gpkg, args.results_dir)
    hash_after_output = phase5a.sha256_file(args.source)
    if hash_after_output != hash_before:
        raise RuntimeError("Frozen source hash changed during derived artifact generation.")
    print_summary(build, paths)
    return 0


if __name__ == "__main__":
    sys.exit(main())
