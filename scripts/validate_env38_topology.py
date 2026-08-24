"""Validate an editable Env38 realization against frozen canonical Env39.

Topology control uses scientific IDs and normalized ``(u, v, key)`` tuples
only. Geometry coordinates are deliberately excluded from the topology
signature. Geometric-realization QA is a separate, exact check and never
repairs or snaps the candidate dataset.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, asdict
import csv
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable

import geopandas as gpd
import networkx as nx
from shapely.errors import GEOSException
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPoint, Point


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANONICAL = PROJECT_ROOT / "data" / "canonical" / "grid" / "env39_canonical.gpkg"
DEFAULT_INPUT = PROJECT_ROOT / "data" / "working" / "curvilinear" / "env38_working.gpkg"
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "results" / "baselines" / "curvilinear" / "topology_control"
EXPECTED_CANONICAL_SHA256 = "212A2AA583BF77304859DB76639B8AD4B6DC458237EEA24FEB60FC228E5D6847"
EXPECTED_TOPOLOGY_SIGNATURE = "4AF93910532EB430103267DAECF73B3ADC3B3D1599471EDF6B1632B6C5F898CB"
EXPECTED_NODE_COUNT = 46
EXPECTED_EDGE_COUNT = 69
EXPECTED_DEGREE_DISTRIBUTION = {1: 10, 3: 16, 4: 20}
NEAR_MISS_TOLERANCE = 1e-6
TOPOLOGY_SERIALIZATION = (
    "UTF-8, sorted normalized (u,v,key) tuples as u|v|key followed by one LF; no header"
)


@dataclass(frozen=True)
class Diagnostic:
    domain: str
    issue_type: str
    severity: str
    message: str
    node_id: str | None = None
    edge_id: str | None = None
    edge_id_2: str | None = None
    x: float | None = None
    y: float | None = None
    distance_local_units: float | None = None


@dataclass
class ValidationResult:
    report: dict[str, Any]
    comparison_rows: list[dict[str, Any]]
    diagnostics: list[Diagnostic]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def normalize_tuple(u: Any, v: Any, key: Any) -> tuple[str, str, int]:
    first, second = str(u), str(v)
    return (first, second, int(key)) if first <= second else (second, first, int(key))


def topology_signature(tuples: Iterable[tuple[str, str, int]]) -> tuple[str, str]:
    serialized = "".join(f"{u}|{v}|{key}\n" for u, v, key in sorted(tuples))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest().upper(), serialized


def _duplicates(values: Iterable[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


def _parts(geometry: Any, kind: type) -> Iterable[Any]:
    if geometry is None or geometry.is_empty:
        return
    if isinstance(geometry, kind):
        yield geometry
    elif isinstance(geometry, (GeometryCollection, MultiPoint, MultiLineString)):
        for part in geometry.geoms:
            yield from _parts(part, kind)


def _xy(point: Point) -> tuple[float, float]:
    return float(point.x), float(point.y)


def _endpoint_xy(line: LineString) -> tuple[tuple[float, float], tuple[float, float]]:
    coordinates = list(line.coords)
    return tuple(coordinates[0][:2]), tuple(coordinates[-1][:2])


def _comparison(
    rows: list[dict[str, Any]], check: str, expected: Any, observed: Any, passed: bool, details: str = ""
) -> None:
    encode = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"))
    rows.append(
        {
            "check": check,
            "status": "PASS" if passed else "FAIL",
            "expected": encode(expected),
            "observed": encode(observed),
            "details": details,
        }
    )


def _diagnostic(diagnostics: list[Diagnostic], domain: str, issue_type: str, message: str, **kwargs: Any) -> None:
    diagnostics.append(Diagnostic(domain, issue_type, "error", message, **kwargs))


def _read_layers(path: Path) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    layers = set(gpd.list_layers(path)["name"])
    missing = {"nodes", "edges"} - layers
    if missing:
        raise ValueError(f"Missing required layer(s): {', '.join(sorted(missing))}")
    return gpd.read_file(path, layer="nodes"), gpd.read_file(path, layer="edges")


def _canonical_data(path: Path) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, set[str], set[str], set[tuple[str, str, int]], dict[str, tuple[str, str, int]]]:
    nodes, edges = _read_layers(path)
    for column in ("node_id",):
        if column not in nodes:
            raise ValueError(f"Canonical nodes layer lacks {column!r}.")
    for column in ("edge_id", "u", "v", "key"):
        if column not in edges:
            raise ValueError(f"Canonical edges layer lacks {column!r}.")
    node_ids = set(nodes["node_id"].astype(str))
    edge_ids = set(edges["edge_id"].astype(str))
    tuples = {normalize_tuple(row.u, row.v, row.key) for row in edges.itertuples()}
    by_edge = {str(row.edge_id): normalize_tuple(row.u, row.v, row.key) for row in edges.itertuples()}
    return nodes, edges, node_ids, edge_ids, tuples, by_edge


def validate_candidate(
    input_path: Path = DEFAULT_INPUT,
    canonical_path: Path = DEFAULT_CANONICAL,
    near_miss_tolerance: float = NEAR_MISS_TOLERANCE,
) -> ValidationResult:
    diagnostics: list[Diagnostic] = []
    comparisons: list[dict[str, Any]] = []
    canonical_hash_before = sha256_file(canonical_path)
    if canonical_hash_before != EXPECTED_CANONICAL_SHA256:
        raise ValueError(
            f"Frozen canonical SHA-256 mismatch: expected {EXPECTED_CANONICAL_SHA256}, observed {canonical_hash_before}."
        )
    canonical_nodes, canonical_edges, canonical_node_ids, canonical_edge_ids, canonical_tuples, canonical_by_edge = _canonical_data(canonical_path)
    canonical_signature, _ = topology_signature(canonical_tuples)
    if canonical_signature != EXPECTED_TOPOLOGY_SIGNATURE:
        raise ValueError("Canonical scientific connectivity no longer has the frozen Phase-5 topology signature.")
    if not input_path.is_file():
        raise FileNotFoundError(f"Candidate GeoPackage does not exist: {input_path}")
    nodes, edges = _read_layers(input_path)

    missing_node_columns = sorted({"node_id"} - set(nodes.columns))
    missing_edge_columns = sorted({"edge_id", "u", "v", "key"} - set(edges.columns))
    if missing_node_columns or missing_edge_columns:
        raise ValueError(f"Missing scientific attributes: nodes={missing_node_columns}, edges={missing_edge_columns}")

    node_values = nodes["node_id"].astype(str).tolist()
    edge_values = edges["edge_id"].astype(str).tolist()
    node_ids, edge_ids = set(node_values), set(edge_values)
    duplicate_node_ids, duplicate_edge_ids = _duplicates(node_values), _duplicates(edge_values)
    missing_nodes, extra_nodes = sorted(canonical_node_ids - node_ids), sorted(node_ids - canonical_node_ids)
    missing_edges, extra_edges = sorted(canonical_edge_ids - edge_ids), sorted(edge_ids - canonical_edge_ids)
    candidate_records: list[tuple[str, str, str, int]] = []
    invalid_keys: list[str] = []
    for row in edges.itertuples():
        try:
            normalize_tuple(row.u, row.v, row.key)
        except (TypeError, ValueError):
            invalid_keys.append(str(row.edge_id))
    for row in edges.itertuples():
        try:
            u, v, key = normalize_tuple(row.u, row.v, row.key)
            candidate_records.append((str(row.edge_id), u, v, key))
        except (TypeError, ValueError):
            pass
    candidate_tuples_list = [(u, v, key) for _edge_id, u, v, key in candidate_records]
    candidate_tuples = set(candidate_tuples_list)
    duplicate_tuples = sorted(item for item, count in Counter(candidate_tuples_list).items() if count > 1)
    pair_counts = Counter((u, v) for u, v, _key in candidate_tuples_list)
    parallel_pairs = sorted(pair for pair, count in pair_counts.items() if count > 1)
    self_loop_edges = sorted(edge_id for edge_id, u, v, _key in candidate_records if u == v)
    missing_tuples = sorted(canonical_tuples - candidate_tuples)
    extra_tuples = sorted(candidate_tuples - canonical_tuples)
    changed_adjacency: list[dict[str, Any]] = []
    changed_key: list[dict[str, Any]] = []
    candidate_by_edge = {edge_id: (u, v, key) for edge_id, u, v, key in candidate_records}
    for edge_id in sorted(canonical_edge_ids & edge_ids):
        expected = canonical_by_edge[edge_id]
        observed = candidate_by_edge.get(edge_id)
        if observed is None or observed == expected:
            continue
        item = {"edge_id": edge_id, "expected": list(expected), "observed": list(observed)}
        if observed[:2] != expected[:2]:
            changed_adjacency.append(item)
        elif observed[2] != expected[2]:
            changed_key.append(item)

    graph = nx.MultiGraph()
    graph.add_nodes_from(node_ids)
    for edge_id, u, v, key in candidate_records:
        graph.add_edge(u, v, key=f"{key}:{edge_id}", edge_id=edge_id)
    component_count = nx.number_connected_components(graph) if graph.number_of_nodes() else 0
    degrees = dict(sorted(Counter(dict(graph.degree()).values()).items()))
    signature, _ = topology_signature(candidate_tuples_list)

    checks = {
        "node_count": len(nodes) == EXPECTED_NODE_COUNT,
        "canonical_node_id_set": node_ids == canonical_node_ids and not duplicate_node_ids,
        "edge_count": len(edges) == EXPECTED_EDGE_COUNT,
        "canonical_edge_id_set": edge_ids == canonical_edge_ids and not duplicate_edge_ids,
        "valid_integer_keys": not invalid_keys,
        "canonical_connectivity_set": candidate_tuples == canonical_tuples and not duplicate_tuples,
        "single_component": component_count == 1,
        "degree_distribution": degrees == EXPECTED_DEGREE_DISTRIBUTION,
        "no_degree2_nodes": 2 not in degrees,
        "no_self_loops": not self_loop_edges,
        "no_parallel_edges": not parallel_pairs,
        "topology_signature": signature == EXPECTED_TOPOLOGY_SIGNATURE,
    }
    _comparison(comparisons, "node_count", EXPECTED_NODE_COUNT, len(nodes), checks["node_count"])
    _comparison(comparisons, "node_id_set", sorted(canonical_node_ids), sorted(node_ids), checks["canonical_node_id_set"], f"missing={missing_nodes}; extra={extra_nodes}; duplicates={duplicate_node_ids}")
    _comparison(comparisons, "edge_count", EXPECTED_EDGE_COUNT, len(edges), checks["edge_count"])
    _comparison(comparisons, "edge_id_set", sorted(canonical_edge_ids), sorted(edge_ids), checks["canonical_edge_id_set"], f"missing={missing_edges}; extra={extra_edges}; duplicates={duplicate_edge_ids}")
    _comparison(comparisons, "normalized_u_v_key_set", [list(x) for x in sorted(canonical_tuples)], [list(x) for x in sorted(candidate_tuples)], checks["canonical_connectivity_set"], f"missing={missing_tuples}; extra={extra_tuples}; duplicates={duplicate_tuples}")
    _comparison(comparisons, "component_count", 1, component_count, checks["single_component"])
    _comparison(comparisons, "degree_distribution", EXPECTED_DEGREE_DISTRIBUTION, degrees, checks["degree_distribution"])
    _comparison(comparisons, "degree2_node_count", 0, degrees.get(2, 0), checks["no_degree2_nodes"])
    _comparison(comparisons, "self_loops", [], self_loop_edges, checks["no_self_loops"])
    _comparison(comparisons, "parallel_edge_pairs", [], [list(x) for x in parallel_pairs], checks["no_parallel_edges"])
    _comparison(comparisons, "topology_signature_sha256", EXPECTED_TOPOLOGY_SIGNATURE, signature, checks["topology_signature"])

    for value in missing_nodes:
        _diagnostic(diagnostics, "topology", "missing_node", "Canonical node is missing.", node_id=value)
    for value in extra_nodes:
        _diagnostic(diagnostics, "topology", "extra_node", "Unexpected node ID is present.", node_id=value)
    for value in missing_edges:
        _diagnostic(diagnostics, "topology", "missing_edge", "Canonical edge is missing.", edge_id=value)
    for value in extra_edges:
        _diagnostic(diagnostics, "topology", "extra_edge", "Unexpected edge ID is present.", edge_id=value)
    for item in changed_adjacency:
        _diagnostic(diagnostics, "topology", "changed_adjacency", f"Expected {item['expected']}, observed {item['observed']}.", edge_id=item["edge_id"])
    for item in changed_key:
        _diagnostic(diagnostics, "topology", "changed_key", f"Expected {item['expected'][2]}, observed {item['observed'][2]}.", edge_id=item["edge_id"])
    if signature != EXPECTED_TOPOLOGY_SIGNATURE:
        _diagnostic(diagnostics, "topology", "topology_signature_mismatch", f"Observed {signature}.")

    geometry_error_types: Counter[str] = Counter()
    node_points: dict[str, Point] = {}
    for row in nodes.itertuples():
        node_id, geometry = str(row.node_id), row.geometry
        if geometry is None or geometry.is_empty:
            issue = "empty_node_geometry"
        elif not isinstance(geometry, Point) or not geometry.is_valid:
            issue = "invalid_node_geometry"
        else:
            node_points[node_id] = geometry
            continue
        geometry_error_types[issue] += 1
        _diagnostic(diagnostics, "geometry", issue, "Node must have one valid, non-empty Point geometry.", node_id=node_id)

    valid_lines: list[tuple[str, str, str, LineString]] = []
    for row in edges.itertuples():
        edge_id, u, v, geometry = str(row.edge_id), str(row.u), str(row.v), row.geometry
        if geometry is None or geometry.is_empty:
            issue = "empty_edge_geometry"
        elif not isinstance(geometry, LineString) or not geometry.is_valid:
            issue = "invalid_edge_geometry"
        elif geometry.length <= 0:
            issue = "zero_length_edge"
        elif not geometry.is_simple:
            issue = "self_intersecting_linestring"
        else:
            valid_lines.append((edge_id, u, v, geometry))
            issue = ""
        if issue:
            geometry_error_types[issue] += 1
            _diagnostic(diagnostics, "geometry", issue, "Edge must be a valid, simple, non-empty, positive-length LineString.", edge_id=edge_id)

    for edge_id, u, v, geometry in valid_lines:
        start, end = _endpoint_xy(geometry)
        u_point, v_point = node_points.get(u), node_points.get(v)
        if u_point is None or v_point is None:
            geometry_error_types["disconnected_endpoint"] += 1
            _diagnostic(diagnostics, "geometry", "disconnected_endpoint", "Edge references a node without valid Point geometry.", edge_id=edge_id)
            continue
        u_xy, v_xy = _xy(u_point), _xy(v_point)
        forward, reverse = (start == u_xy and end == v_xy), (start == v_xy and end == u_xy)
        if not (forward or reverse):
            geometry_error_types["endpoint_node_mismatch"] += 1
            _diagnostic(diagnostics, "geometry", "endpoint_node_mismatch", f"Endpoints {start}, {end} do not exactly match prescribed nodes {u}={u_xy}, {v}={v_xy}.", edge_id=edge_id)
            for endpoint, node_id, target in ((start, u, u_xy), (end, v, v_xy), (start, v, v_xy), (end, u, u_xy)):
                distance = math.dist(endpoint, target)
                if 0 < distance <= near_miss_tolerance:
                    _diagnostic(diagnostics, "geometry", "near_miss", "Edge endpoint is close to, but not exactly coincident with, its prescribed node; no snapping applied.", node_id=node_id, edge_id=edge_id, x=endpoint[0], y=endpoint[1], distance_local_units=distance)

    valid_lines.sort(key=lambda item: item[0])
    for index, (edge_1, u1, v1, line_1) in enumerate(valid_lines):
        endpoints_1 = set(_endpoint_xy(line_1))
        for edge_2, u2, v2, line_2 in valid_lines[index + 1 :]:
            try:
                intersection = line_1.intersection(line_2)
            except GEOSException as exc:
                geometry_error_types["intersection_evaluation_error"] += 1
                _diagnostic(diagnostics, "geometry", "intersection_evaluation_error", str(exc), edge_id=edge_1, edge_id_2=edge_2)
                continue
            if intersection.is_empty:
                continue
            overlap = sum(float(part.length) for part in _parts(intersection, LineString))
            if overlap > 0:
                geometry_error_types["overlapping_edge_segments"] += 1
                _diagnostic(diagnostics, "geometry", "overlapping_edge_segments", f"Edges overlap along {overlap:.12g} local units.", edge_id=edge_1, edge_id_2=edge_2)
            endpoints_2 = set(_endpoint_xy(line_2))
            shared_ids = {u1, v1} & {u2, v2}
            allowed = {_xy(node_points[node]) for node in shared_ids if node in node_points}
            for point in _parts(intersection, Point):
                point_xy = _xy(point)
                if point_xy in allowed and point_xy in endpoints_1 and point_xy in endpoints_2:
                    continue
                endpoint_1, endpoint_2 = point_xy in endpoints_1, point_xy in endpoints_2
                issue = "endpoint_interior_unsplit_intersection" if endpoint_1 != endpoint_2 else "unintended_edge_crossing"
                geometry_error_types[issue] += 1
                _diagnostic(diagnostics, "geometry", issue, "Edges intersect outside a shared canonical node.", edge_id=edge_1, edge_id_2=edge_2, x=point_xy[0], y=point_xy[1])

    crs_equal = nodes.crs is not None and edges.crs is not None and nodes.crs == canonical_nodes.crs and edges.crs == canonical_edges.crs
    if not crs_equal:
        geometry_error_types["crs_mismatch"] += 1
        _diagnostic(diagnostics, "geometry", "crs_mismatch", "Both candidate layers must preserve the canonical GeoGami Local Cartesian CRS exactly.")
    geometry_checks = {
        "valid_node_points": len(node_points) == len(nodes),
        "valid_edge_linestrings": len(valid_lines) == len(edges),
        "endpoint_node_consistency": geometry_error_types["endpoint_node_mismatch"] == 0 and geometry_error_types["disconnected_endpoint"] == 0,
        "no_self_intersections": geometry_error_types["self_intersecting_linestring"] == 0,
        "no_unintended_intersections": not any(geometry_error_types[name] for name in ("endpoint_interior_unsplit_intersection", "unintended_edge_crossing", "overlapping_edge_segments", "intersection_evaluation_error")),
        "canonical_crs_preserved": crs_equal,
    }
    topology_pass = all(checks.values())
    geometry_pass = all(geometry_checks.values()) and not geometry_error_types
    canonical_hash_after = sha256_file(canonical_path)
    if canonical_hash_after != canonical_hash_before:
        raise RuntimeError("Frozen canonical GeoPackage changed during validation.")
    diagnostics.sort(key=lambda item: (item.domain, item.issue_type, item.node_id or "", item.edge_id or "", item.edge_id_2 or "", item.x if item.x is not None else math.inf, item.y if item.y is not None else math.inf))
    report = {
        "phase": "6A",
        "final_result": "PASS" if topology_pass and geometry_pass else "FAIL",
        "topology_control_status": "PASS" if topology_pass else "FAIL",
        "geometric_realization_qa_status": "PASS" if geometry_pass else "FAIL",
        "candidate": {"path": str(input_path.relative_to(PROJECT_ROOT)) if input_path.is_relative_to(PROJECT_ROOT) else str(input_path), "node_count": len(nodes), "edge_count": len(edges), "crs": str(nodes.crs)},
        "canonical": {"path": str(canonical_path.relative_to(PROJECT_ROOT)), "expected_sha256": EXPECTED_CANONICAL_SHA256, "sha256_before": canonical_hash_before, "sha256_after": canonical_hash_after, "unchanged": canonical_hash_before == canonical_hash_after},
        "methodology": {"topology_serialization": TOPOLOGY_SERIALIZATION, "geometry_in_topology_signature": False, "near_miss_tolerance_local_units": near_miss_tolerance, "near_miss_policy": "diagnostic only; no snapping or automatic repair", "line_direction": "either u-to-v or v-to-u is accepted deterministically"},
        "topology_control": {"checks": checks, "component_count": component_count, "degree_distribution": {str(key): value for key, value in degrees.items()}, "topology_signature_sha256": signature, "expected_topology_signature_sha256": EXPECTED_TOPOLOGY_SIGNATURE, "missing_nodes": missing_nodes, "extra_nodes": extra_nodes, "duplicate_node_ids": duplicate_node_ids, "missing_edges": missing_edges, "extra_edges": extra_edges, "duplicate_edge_ids": duplicate_edge_ids, "missing_normalized_tuples": [list(x) for x in missing_tuples], "extra_normalized_tuples": [list(x) for x in extra_tuples], "duplicate_normalized_tuples": [list(x) for x in duplicate_tuples], "changed_adjacency": changed_adjacency, "changed_key": changed_key, "self_loop_edges": self_loop_edges, "parallel_endpoint_pairs": [list(x) for x in parallel_pairs], "invalid_key_edges": invalid_keys},
        "geometric_realization_qa": {"checks": geometry_checks, "error_counts": dict(sorted(geometry_error_types.items())), "near_miss_count": sum(item.issue_type == "near_miss" for item in diagnostics)},
        "diagnostics": [asdict(item) for item in diagnostics],
    }
    return ValidationResult(report, comparisons, diagnostics)


def write_reports(result: ValidationResult, results_dir: Path = DEFAULT_RESULTS_DIR) -> dict[str, Path]:
    results_dir.mkdir(parents=True, exist_ok=True)
    paths = {"template_validation_json": results_dir / "template_validation.json", "topology_comparison_csv": results_dir / "topology_comparison.csv", "diagnostics_csv": results_dir / "diagnostics.csv"}
    with paths["template_validation_json"].open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(result.report, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    with paths["topology_comparison_csv"].open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["check", "status", "expected", "observed", "details"], lineterminator="\n")
        writer.writeheader(); writer.writerows(result.comparison_rows)
    fields = list(Diagnostic.__dataclass_fields__)
    with paths["diagnostics_csv"].open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(asdict(item) for item in result.diagnostics)
    return paths


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--canonical", type=Path, default=DEFAULT_CANONICAL)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--near-miss-tolerance", type=float, default=NEAR_MISS_TOLERANCE)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = validate_candidate(args.input, args.canonical, args.near_miss_tolerance)
    paths = write_reports(result, args.results_dir)
    topology = result.report["topology_control"]
    print(f"TOPOLOGY CONTROL: {result.report['topology_control_status']}")
    print(f"GEOMETRIC REALIZATION QA: {result.report['geometric_realization_qa_status']}")
    print(f"Nodes / edges / components: {result.report['candidate']['node_count']} / {result.report['candidate']['edge_count']} / {topology['component_count']}")
    print(f"Degree distribution: {topology['degree_distribution']}")
    print(f"Topology signature: {topology['topology_signature_sha256']}")
    print(f"Canonical SHA-256 before/after: {result.report['canonical']['sha256_before']} / {result.report['canonical']['sha256_after']}")
    for name, path in paths.items(): print(f"{name}: {path}")
    print(f"FINAL RESULT: {result.report['final_result']}")
    return 0 if result.report["final_result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
