"""Phase 5A read-only geometry and canonical-topology audit for Env39.

The source GeoPackage is never opened for writing. Exact, unrounded LineString
endpoint tuples define graph-node identity. Tolerances are used only for
diagnostic classification and never for topology construction.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
import math
from numbers import Integral
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely.errors import GEOSException
from shapely.geometry import GeometryCollection, LineString, MultiPoint, Point


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "data" / "baselines" / "grid" / "network_v2.gpkg"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "results" / "baselines" / "grid" / "geometry_qa"
DEFAULT_LAYER = "edges"
EXPECTED_EDGE_COUNT = 76
EXPECTED_NODE_COUNT = 53
EXPECTED_COMPONENT_COUNT = 1
EXPECTED_DEGREE_DISTRIBUTION = {1: 10, 2: 7, 3: 16, 4: 20}
EXPECTED_STRUCTURAL_INTERSECTION_COUNT = 0
EXPECTED_NEAR_MISS_COUNT = 0
NEAR_MISS_TOLERANCE = 0.01
STRAIGHT_CONTINUATION_TOLERANCE_DEGREES = 1.0
COLLINEAR_EPSILON_MULTIPLIER = 64.0


@dataclass
class AuditIssue:
    issue_type: str
    severity: str
    description: str
    fid_1: Any = None
    fid_2: Any = None
    x: float | None = None
    y: float | None = None
    issue_id: str = ""

    def sort_key(self) -> tuple[Any, ...]:
        return (
            self.issue_type,
            _fid_key(self.fid_1),
            _fid_key(self.fid_2),
            math.inf if self.x is None else self.x,
            math.inf if self.y is None else self.y,
            self.description,
        )

    def record(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "issue_type": self.issue_type,
            "severity": self.severity,
            "fid_1": _json_scalar(self.fid_1),
            "fid_2": _json_scalar(self.fid_2),
            "x": self.x,
            "y": self.y,
            "description": self.description,
        }


@dataclass
class AuditResult:
    source_frame: gpd.GeoDataFrame
    report: dict[str, Any]
    edge_records: list[dict[str, Any]]
    node_records: list[dict[str, Any]]
    degree2_records: list[dict[str, Any]]
    multivertex_records: list[dict[str, Any]]
    non_axis_records: list[dict[str, Any]]
    issues: list[AuditIssue] = field(default_factory=list)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _json_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return _json_scalar(value.item())
    return str(value)


def _fid_key(fid: Any) -> tuple[int, Any]:
    if isinstance(fid, Integral) and not isinstance(fid, bool):
        return (0, int(fid))
    return (1, str(fid))


def _coord(coordinate: Iterable[float]) -> tuple[float, ...]:
    return tuple(float(value) for value in coordinate)


def _xy(coordinate: tuple[float, ...]) -> tuple[float, float]:
    return (coordinate[0], coordinate[1])


def _canonical_endpoint_pair(
    start: tuple[float, ...], end: tuple[float, ...]
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    return (start, end) if start <= end else (end, start)


def _read_edges(path: Path, layer: str) -> gpd.GeoDataFrame:
    layers = gpd.list_layers(path)["name"].astype(str).tolist()
    if layer not in layers:
        raise ValueError(f'Required layer "{layer}" is absent; available layers: {layers}')
    frame = gpd.read_file(path, layer=layer, engine="pyogrio", fid_as_index=True)
    if frame.index.name != "fid":
        raise ValueError("The source feature identifier could not be preserved as the fid index.")
    return frame


def _distinct_consecutive(coordinates: tuple[tuple[float, ...], ...]) -> tuple[tuple[float, ...], ...]:
    if not coordinates:
        return ()
    result = [coordinates[0]]
    for coordinate in coordinates[1:]:
        if coordinate != result[-1]:
            result.append(coordinate)
    return tuple(result)


def _orientation(dx: float, dy: float) -> float | None:
    if dx == 0 and dy == 0:
        return None
    value = math.degrees(math.atan2(dy, dx)) % 180.0
    return 0.0 if value == 180.0 else value


def _axis_deviation(orientation: float | None) -> float | None:
    if orientation is None:
        return None
    return min(orientation, abs(orientation - 90.0), abs(180.0 - orientation))


def _numerical_collinearity_tolerance(
    coordinates: tuple[tuple[float, ...], ...], chord_length: float
) -> float:
    scale = max(
        1.0,
        chord_length,
        *(abs(value) for coordinate in coordinates for value in coordinate[:2]),
    )
    return COLLINEAR_EPSILON_MULTIPLIER * sys.float_info.epsilon * scale


def _point_to_infinite_line_distance(
    point: tuple[float, ...], start: tuple[float, ...], end: tuple[float, ...]
) -> float | None:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    denominator = math.hypot(dx, dy)
    if denominator == 0:
        return None
    numerator = abs(dx * (start[1] - point[1]) - (start[0] - point[0]) * dy)
    return numerator / denominator


def _angle_between(vector_1: tuple[float, float], vector_2: tuple[float, float]) -> float | None:
    magnitude_1 = math.hypot(*vector_1)
    magnitude_2 = math.hypot(*vector_2)
    if magnitude_1 == 0 or magnitude_2 == 0:
        return None
    cosine = (vector_1[0] * vector_2[0] + vector_1[1] * vector_2[1]) / (
        magnitude_1 * magnitude_2
    )
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def internal_turn_diagnostics(
    coordinates: tuple[tuple[float, ...], ...]
) -> dict[str, Any]:
    """Return direction-change angles after collapsing consecutive duplicates.

    Zero degrees means straight continuation; 180 degrees means reversal.
    Collapsing is an in-memory diagnostic operation and does not mutate geometry.
    """
    distinct = _distinct_consecutive(coordinates)
    turns: list[dict[str, Any]] = []
    for index in range(1, len(distinct) - 1):
        previous, current, following = distinct[index - 1 : index + 2]
        incoming = (current[0] - previous[0], current[1] - previous[1])
        outgoing = (following[0] - current[0], following[1] - current[1])
        angle = _angle_between(incoming, outgoing)
        turns.append(
            {
                "distinct_vertex_index": index,
                "x": current[0],
                "y": current[1],
                "direction_change_degrees": angle,
            }
        )
    numeric_angles = [item["direction_change_degrees"] for item in turns if item["direction_change_degrees"] is not None]
    return {
        "consecutive_duplicate_vertex_count": len(coordinates) - len(distinct),
        "distinct_vertex_count": len(distinct),
        "turns": turns,
        "maximum_direction_change_degrees": max(numeric_angles, default=None),
    }


def analyze_edge(fid: Any, geometry: LineString) -> dict[str, Any]:
    coordinates = tuple(_coord(coordinate) for coordinate in geometry.coords)
    start, end = coordinates[0], coordinates[-1]
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    chord_length = math.hypot(dx, dy)
    geometry_length = float(geometry.length)
    orientation = _orientation(dx, dy)
    all_x_equal = all(coordinate[0] == coordinates[0][0] for coordinate in coordinates)
    all_y_equal = all(coordinate[1] == coordinates[0][1] for coordinate in coordinates)
    tolerance = _numerical_collinearity_tolerance(coordinates, chord_length)
    deviations = [
        _point_to_infinite_line_distance(coordinate, start, end)
        for coordinate in coordinates[1:-1]
    ]
    finite_deviations = [value for value in deviations if value is not None]
    max_deviation = max(finite_deviations, default=0.0 if chord_length > 0 else None)
    collinear = (
        chord_length > 0
        and max_deviation is not None
        and max_deviation <= tolerance
    )
    turns = internal_turn_diagnostics(coordinates)
    return {
        "source_fid": _json_scalar(fid),
        "vertex_count": len(coordinates),
        "geometry_length_local_units": geometry_length,
        "chord_length_local_units": chord_length,
        "geometry_chord_ratio": None if chord_length == 0 else geometry_length / chord_length,
        "endpoint_orientation_mod_180_degrees": orientation,
        "axis_deviation_degrees": _axis_deviation(orientation),
        "is_exactly_horizontal": bool(chord_length > 0 and all_y_equal),
        "is_exactly_vertical": bool(chord_length > 0 and all_x_equal),
        "is_straight_non_axis_aligned": bool(
            chord_length > 0 and collinear and not all_y_equal and not all_x_equal
        ),
        "has_more_than_two_vertices": len(coordinates) > 2,
        "internal_vertex_count": max(0, len(coordinates) - 2),
        "maximum_intermediate_deviation_local_units": max_deviation,
        "collinearity_tolerance_local_units": tolerance,
        "all_vertices_collinear_within_numerical_precision": bool(collinear),
        "consecutive_duplicate_vertex_count": turns["consecutive_duplicate_vertex_count"],
        "distinct_vertex_count": turns["distinct_vertex_count"],
        "internal_turn_diagnostics": turns["turns"],
        "maximum_internal_direction_change_degrees": turns["maximum_direction_change_degrees"],
    }


def _direction_away_from_node(
    geometry: LineString, node: tuple[float, ...]
) -> tuple[float, float] | None:
    coordinates = tuple(_coord(coordinate) for coordinate in geometry.coords)
    if coordinates[0] == node:
        candidates = coordinates[1:]
    elif coordinates[-1] == node:
        candidates = tuple(reversed(coordinates[:-1]))
    else:
        return None
    for coordinate in candidates:
        vector = (coordinate[0] - node[0], coordinate[1] - node[1])
        if vector != (0.0, 0.0):
            return vector
    return None


def classify_degree2_angle(
    angle_degrees: float | None,
    tolerance_degrees: float = STRAIGHT_CONTINUATION_TOLERANCE_DEGREES,
) -> str:
    if angle_degrees is None or angle_degrees <= tolerance_degrees:
        return "other_configuration"
    if 180.0 - angle_degrees <= tolerance_degrees:
        return "approximately_straight_continuation"
    return "angular_bend"


def derive_nodes_and_graph(
    line_rows: list[tuple[Any, LineString, dict[str, Any]]]
) -> tuple[nx.MultiGraph, list[dict[str, Any]], list[dict[str, Any]]]:
    graph = nx.MultiGraph()
    for fid, geometry, edge_record in line_rows:
        coordinates = tuple(_coord(coordinate) for coordinate in geometry.coords)
        start, end = coordinates[0], coordinates[-1]
        graph.add_edge(
            start,
            end,
            key=fid,
            source_fid=_json_scalar(fid),
            geometry=geometry,
            length=edge_record["geometry_length_local_units"],
        )

    temporary_ids = {
        node: f"tmp_node_{index:03d}"
        for index, node in enumerate(sorted(graph.nodes), start=1)
    }
    node_records: list[dict[str, Any]] = []
    degree2_records: list[dict[str, Any]] = []
    for node in sorted(graph.nodes):
        incident = sorted(
            graph.edges(node, keys=True, data=True),
            key=lambda item: _fid_key(item[3]["source_fid"]),
        )
        incident_fids = [item[3]["source_fid"] for item in incident]
        node_record = {
            "temporary_node_id": temporary_ids[node],
            "x": node[0],
            "y": node[1],
            "degree": int(graph.degree(node)),
            "incident_source_fids": incident_fids,
            "incident_edge_count": len(incident),
        }
        node_records.append(node_record)
        if graph.degree(node) != 2 or len(incident) != 2:
            continue
        first, second = incident
        vector_1 = _direction_away_from_node(first[3]["geometry"], node)
        vector_2 = _direction_away_from_node(second[3]["geometry"], node)
        angle = None if vector_1 is None or vector_2 is None else _angle_between(vector_1, vector_2)
        degree2_records.append(
            {
                "temporary_node_id": temporary_ids[node],
                "x": node[0],
                "y": node[1],
                "incident_source_fid_1": first[3]["source_fid"],
                "incident_source_fid_2": second[3]["source_fid"],
                "incident_edge_length_1_local_units": first[3]["length"],
                "incident_edge_length_2_local_units": second[3]["length"],
                "angle_between_outgoing_directions_degrees": angle,
                "classification": classify_degree2_angle(angle),
            }
        )
    return graph, node_records, degree2_records


def _iter_intersection_points(geometry: Any) -> Iterable[Point]:
    if geometry is None or geometry.is_empty:
        return
    if isinstance(geometry, Point):
        yield geometry
    elif isinstance(geometry, (MultiPoint, GeometryCollection)):
        for part in geometry.geoms:
            yield from _iter_intersection_points(part)


def _is_endpoint_xy(point: Point, endpoints: tuple[tuple[float, ...], tuple[float, ...]]) -> bool:
    point_xy = (float(point.x), float(point.y))
    return any(_xy(endpoint) == point_xy for endpoint in endpoints)


def structural_intersections(
    lines: list[tuple[Any, LineString, dict[str, Any]]]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for index, (fid_1, geometry_1, _record_1) in enumerate(lines):
        endpoints_1 = (_coord(geometry_1.coords[0]), _coord(geometry_1.coords[-1]))
        for fid_2, geometry_2, _record_2 in lines[index + 1 :]:
            endpoints_2 = (_coord(geometry_2.coords[0]), _coord(geometry_2.coords[-1]))
            try:
                intersection = geometry_1.intersection(geometry_2)
            except GEOSException as exc:
                findings.append(
                    {
                        "fid_1": _json_scalar(fid_1),
                        "fid_2": _json_scalar(fid_2),
                        "type": "intersection_evaluation_error",
                        "description": str(exc),
                    }
                )
                continue
            for point in _iter_intersection_points(intersection):
                endpoint_1 = _is_endpoint_xy(point, endpoints_1)
                endpoint_2 = _is_endpoint_xy(point, endpoints_2)
                if endpoint_1 and endpoint_2:
                    continue
                findings.append(
                    {
                        "fid_1": _json_scalar(fid_1),
                        "fid_2": _json_scalar(fid_2),
                        "type": (
                            "endpoint_interior_unsplit_intersection"
                            if endpoint_1 != endpoint_2
                            else "interior_interior_unsplit_intersection"
                        ),
                        "x": float(point.x),
                        "y": float(point.y),
                    }
                )
    findings.sort(key=lambda item: (item["type"], _fid_key(item["fid_1"]), _fid_key(item["fid_2"]), item.get("x", math.inf), item.get("y", math.inf)))
    return findings


def near_miss_pairs(graph: nx.MultiGraph, tolerance: float = NEAR_MISS_TOLERANCE) -> list[dict[str, Any]]:
    nodes = sorted(graph.nodes)
    findings: list[dict[str, Any]] = []
    for index, node_1 in enumerate(nodes):
        x1, y1 = _xy(node_1)
        for node_2 in nodes[index + 1 :]:
            x2, y2 = _xy(node_2)
            if abs(x2 - x1) > tolerance or abs(y2 - y1) > tolerance:
                continue
            distance = math.hypot(x2 - x1, y2 - y1)
            if 0 < distance <= tolerance:
                findings.append(
                    {
                        "endpoint_1": list(node_1),
                        "endpoint_2": list(node_2),
                        "distance_local_units": distance,
                    }
                )
    findings.sort(key=lambda item: (item["distance_local_units"], item["endpoint_1"], item["endpoint_2"]))
    return findings


def _add_issue(
    issues: list[AuditIssue],
    issue_type: str,
    severity: str,
    description: str,
    fid_1: Any = None,
    fid_2: Any = None,
    xy: tuple[float, float] | None = None,
) -> None:
    issues.append(
        AuditIssue(
            issue_type=issue_type,
            severity=severity,
            description=description,
            fid_1=fid_1,
            fid_2=fid_2,
            x=None if xy is None else xy[0],
            y=None if xy is None else xy[1],
        )
    )


def _geometry_pair_checks(
    lines: list[tuple[Any, LineString, dict[str, Any]]], issues: list[AuditIssue]
) -> None:
    endpoint_groups: dict[tuple[tuple[float, ...], tuple[float, ...]], list[Any]] = {}
    for fid, geometry, _record in lines:
        coordinates = tuple(_coord(coordinate) for coordinate in geometry.coords)
        endpoint_groups.setdefault(
            _canonical_endpoint_pair(coordinates[0], coordinates[-1]), []
        ).append(fid)
    for members in endpoint_groups.values():
        members.sort(key=_fid_key)
        for index, fid_1 in enumerate(members):
            for fid_2 in members[index + 1 :]:
                _add_issue(
                    issues,
                    "duplicate_endpoint_pair",
                    "warning",
                    "Two edges share the same exact unordered endpoint pair; manual review is required.",
                    fid_1,
                    fid_2,
                )

    for index, (fid_1, geometry_1, _record_1) in enumerate(lines):
        coordinates_1 = tuple(_coord(coordinate) for coordinate in geometry_1.coords)
        for fid_2, geometry_2, _record_2 in lines[index + 1 :]:
            coordinates_2 = tuple(_coord(coordinate) for coordinate in geometry_2.coords)
            if coordinates_1 == coordinates_2:
                duplicate_type = "identical_duplicate_geometry"
            elif coordinates_1 == tuple(reversed(coordinates_2)):
                duplicate_type = "reversed_duplicate_geometry"
            elif geometry_1.equals(geometry_2):
                duplicate_type = "topologically_duplicate_geometry"
            else:
                continue
            _add_issue(
                issues,
                duplicate_type,
                "error",
                "Two source features represent the same complete physical geometry.",
                fid_1,
                fid_2,
            )


def _assert_invariant(
    issues: list[AuditIssue], name: str, actual: Any, expected: Any
) -> None:
    if actual != expected:
        _add_issue(
            issues,
            "phase4_invariant_changed",
            "error",
            f"{name} changed: expected {expected!r}, observed {actual!r}.",
        )


def audit_source(source_path: Path, layer: str = DEFAULT_LAYER) -> AuditResult:
    source_frame = _read_edges(source_path, layer)
    issues: list[AuditIssue] = []
    lines: list[tuple[Any, LineString, dict[str, Any]]] = []
    edge_records: list[dict[str, Any]] = []

    for fid, row in source_frame.iterrows():
        geometry = row.geometry
        if geometry is None:
            _add_issue(issues, "empty_geometry", "error", "Source geometry is null.", fid)
            continue
        if geometry.is_empty:
            _add_issue(issues, "empty_geometry", "error", "Source geometry is empty.", fid)
            continue
        if geometry.geom_type != "LineString":
            _add_issue(issues, "unexpected_geometry_type", "error", f"Expected LineString, found {geometry.geom_type}.", fid)
            continue
        if not geometry.is_valid:
            _add_issue(issues, "invalid_geometry", "error", "Source LineString is invalid.", fid)
        if not geometry.is_simple:
            _add_issue(issues, "self_intersection", "error", "Source LineString is not simple and self-intersects.", fid)
        edge_record = analyze_edge(fid, geometry)
        if edge_record["geometry_length_local_units"] <= 0:
            _add_issue(issues, "zero_length_edge", "error", "Source edge has non-positive planar length.", fid)
        coordinates = tuple(_coord(coordinate) for coordinate in geometry.coords)
        if coordinates[0] == coordinates[-1]:
            _add_issue(issues, "self_loop", "error", "Exact start and end endpoint tuples are equal.", fid)
        if edge_record["consecutive_duplicate_vertex_count"] > 0:
            _add_issue(
                issues,
                "consecutive_duplicate_vertices",
                "warning",
                f"Geometry contains {edge_record['consecutive_duplicate_vertex_count']} repeated consecutive coordinate(s).",
                fid,
            )
        edge_records.append(edge_record)
        lines.append((fid, geometry, edge_record))

    lines.sort(key=lambda item: _fid_key(item[0]))
    edge_records.sort(key=lambda item: _fid_key(item["source_fid"]))
    graph, node_records, degree2_records = derive_nodes_and_graph(lines)
    _geometry_pair_checks(lines, issues)

    degree_distribution_counter = Counter(dict(graph.degree()).values())
    degree_distribution = dict(sorted(degree_distribution_counter.items()))
    for node, degree in sorted(graph.degree(), key=lambda item: item[0]):
        if degree not in {1, 2, 3, 4}:
            _add_issue(
                issues,
                "unexpected_degree",
                "error",
                f"Exact graph node degree {degree} is outside the expected range 1-4.",
                xy=_xy(node),
            )

    intersections = structural_intersections(lines)
    misses = near_miss_pairs(graph)
    components = sorted(
        (sorted(component) for component in nx.connected_components(graph)),
        key=lambda component: (-len(component), component),
    )
    _assert_invariant(issues, "edge count", graph.number_of_edges(), EXPECTED_EDGE_COUNT)
    _assert_invariant(issues, "exact node count", graph.number_of_nodes(), EXPECTED_NODE_COUNT)
    _assert_invariant(issues, "connected component count", len(components), EXPECTED_COMPONENT_COUNT)
    _assert_invariant(issues, "degree distribution", degree_distribution, EXPECTED_DEGREE_DISTRIBUTION)
    _assert_invariant(issues, "structural unsplit intersection count", len(intersections), EXPECTED_STRUCTURAL_INTERSECTION_COUNT)
    _assert_invariant(issues, "near-miss count", len(misses), EXPECTED_NEAR_MISS_COUNT)

    multivertex_records = [record for record in edge_records if record["has_more_than_two_vertices"]]
    non_axis_records = [
        record
        for record in edge_records
        if not record["is_exactly_horizontal"] and not record["is_exactly_vertical"]
    ]
    report = {
        "audit": "GeoGami Env39 Phase 5A read-only geometry and canonical-topology audit",
        "source": {
            "path": source_path.resolve().as_posix(),
            "layer": layer,
            "crs": None if source_frame.crs is None else str(source_frame.crs),
            "crs_wkt": None if source_frame.crs is None else source_frame.crs.to_wkt(),
            "is_geographic": None if source_frame.crs is None else bool(source_frame.crs.is_geographic),
        },
        "methodology": {
            "node_identity": "unique exact unrounded full LineString endpoint tuples",
            "topology_tolerance": None,
            "near_miss_tolerance_local_units": NEAR_MISS_TOLERANCE,
            "near_miss_use": "reporting only; never node construction",
            "degree2_angle": "angle from the two outward incident-edge directions at the node, in [0, 180] degrees",
            "approximately_straight_tolerance_degrees": STRAIGHT_CONTINUATION_TOLERANCE_DEGREES,
            "degree2_classification": {
                "approximately_straight_continuation": "angle is within 1 degree of 180",
                "angular_bend": "angle is greater than 1 and less than 179 degrees",
                "other_configuration": "angle unavailable or within 1 degree of 0",
            },
            "turn_angle": "direction change after collapsing consecutive duplicate coordinates in memory; 0 is straight and 180 is reversal",
            "collinearity": "maximum intermediate-vertex distance from infinite endpoint chord <= 64 * machine epsilon * coordinate/chord scale",
            "source_geometry_mutation": "none",
        },
        "phase4_expected_invariants": {
            "edge_count": EXPECTED_EDGE_COUNT,
            "exact_node_count": EXPECTED_NODE_COUNT,
            "component_count": EXPECTED_COMPONENT_COUNT,
            "degree_distribution": {str(key): value for key, value in EXPECTED_DEGREE_DISTRIBUTION.items()},
            "structural_unsplit_intersection_count": EXPECTED_STRUCTURAL_INTERSECTION_COUNT,
            "near_miss_count": EXPECTED_NEAR_MISS_COUNT,
        },
        "topology": {
            "edge_count": graph.number_of_edges(),
            "exact_node_count": graph.number_of_nodes(),
            "component_count": len(components),
            "component_sizes": [len(component) for component in components],
            "degree_distribution": {str(key): value for key, value in degree_distribution.items()},
            "structural_unsplit_intersection_count": len(intersections),
            "structural_unsplit_intersections": intersections,
            "near_miss_count": len(misses),
            "near_misses": misses,
        },
        "summary": {
            "degree2_node_count": len(degree2_records),
            "total_planar_length_local_units": sum(
                record["geometry_length_local_units"] for record in edge_records
            ),
            "exact_horizontal_edge_count": sum(record["is_exactly_horizontal"] for record in edge_records),
            "exact_vertical_edge_count": sum(record["is_exactly_vertical"] for record in edge_records),
            "straight_non_axis_aligned_edge_count": sum(
                record["is_straight_non_axis_aligned"] for record in edge_records
            ),
            "multivertex_edge_count": len(multivertex_records),
            "genuinely_non_collinear_multivertex_edge_count": sum(
                not record["all_vertices_collinear_within_numerical_precision"]
                for record in multivertex_records
            ),
            "empty_geometry_count": sum(issue.issue_type == "empty_geometry" for issue in issues),
            "invalid_geometry_count": sum(issue.issue_type == "invalid_geometry" for issue in issues),
            "zero_length_edge_count": sum(issue.issue_type == "zero_length_edge" for issue in issues),
            "self_intersection_count": sum(issue.issue_type == "self_intersection" for issue in issues),
        },
        "nodes": node_records,
        "degree2_nodes": degree2_records,
        "edges": edge_records,
        "multivertex_edges": multivertex_records,
        "non_axis_aligned_edges": non_axis_records,
    }
    return AuditResult(
        source_frame=source_frame,
        report=report,
        edge_records=edge_records,
        node_records=node_records,
        degree2_records=degree2_records,
        multivertex_records=multivertex_records,
        non_axis_records=non_axis_records,
        issues=issues,
    )


def finalize_result(result: AuditResult) -> None:
    result.issues.sort(key=AuditIssue.sort_key)
    for index, issue in enumerate(result.issues, start=1):
        issue.issue_id = f"GQA-{index:04d}"
    issue_counts = Counter(issue.issue_type for issue in result.issues)
    error_count = sum(issue.severity == "error" for issue in result.issues)
    warning_count = sum(issue.severity == "warning" for issue in result.issues)
    result.report["issues"] = [issue.record() for issue in result.issues]
    result.report["issue_counts_by_type"] = dict(sorted(issue_counts.items()))
    result.report["error_count"] = error_count
    result.report["warning_count"] = warning_count
    result.report["final_result"] = "FAIL" if error_count else "PASS"


def _csv_ready(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for source in records:
        record = dict(source)
        for key, value in list(record.items()):
            if isinstance(value, (list, dict)):
                record[key] = json.dumps(value, sort_keys=True, separators=(",", ":"))
        prepared.append(record)
    return prepared


def _write_csv(path: Path, records: list[dict[str, Any]], columns: list[str] | None = None) -> None:
    frame = pd.DataFrame(_csv_ready(records), columns=columns)
    frame.to_csv(path, index=False, lineterminator="\n")


def _spatial_records(
    records: list[dict[str, Any]], geometries: list[Any], crs: Any
) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(_csv_ready(records), geometry=geometries, crs=crs)


def _stabilize_gpkg_metadata(path: Path) -> None:
    """Replace generated-layer timestamps in the derived GeoPackage only."""
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE gpkg_contents SET last_change = '2000-01-01T00:00:00.000Z'"
        )
        connection.commit()
        connection.execute("VACUUM")


def write_outputs(result: AuditResult, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "geometry_qa_json": output_dir / "geometry_qa.json",
        "edge_geometry_audit_csv": output_dir / "edge_geometry_audit.csv",
        "node_audit_csv": output_dir / "node_audit.csv",
        "degree2_nodes_csv": output_dir / "degree2_nodes.csv",
        "multivertex_edges_csv": output_dir / "multivertex_edges.csv",
        "non_axis_aligned_edges_csv": output_dir / "non_axis_aligned_edges.csv",
        "geometry_qa_gpkg": output_dir / "geometry_qa.gpkg",
    }
    with paths["geometry_qa_json"].open("w", encoding="utf-8", newline="\n") as file:
        json.dump(result.report, file, indent=2, ensure_ascii=False, allow_nan=False)
        file.write("\n")
    _write_csv(paths["edge_geometry_audit_csv"], result.edge_records)
    _write_csv(paths["node_audit_csv"], result.node_records)
    _write_csv(paths["degree2_nodes_csv"], result.degree2_records)
    _write_csv(paths["multivertex_edges_csv"], result.multivertex_records)
    _write_csv(paths["non_axis_aligned_edges_csv"], result.non_axis_records)

    gpkg_path = paths["geometry_qa_gpkg"]
    if gpkg_path.exists():
        gpkg_path.unlink()
    fid_to_geometry = {
        _json_scalar(fid): geometry
        for fid, geometry in result.source_frame.geometry.items()
    }
    crs = result.source_frame.crs
    node_geometry = [Point(record["x"], record["y"]) for record in result.node_records]
    _spatial_records(result.node_records, node_geometry, crs).to_file(
        gpkg_path, layer="derived_nodes", driver="GPKG", index=False
    )
    degree2_geometry = [Point(record["x"], record["y"]) for record in result.degree2_records]
    _spatial_records(result.degree2_records, degree2_geometry, crs).to_file(
        gpkg_path, layer="degree2_nodes", driver="GPKG", mode="a", index=False
    )
    non_axis_geometry = [fid_to_geometry[record["source_fid"]] for record in result.non_axis_records]
    _spatial_records(result.non_axis_records, non_axis_geometry, crs).to_file(
        gpkg_path, layer="non_axis_aligned_edges", driver="GPKG", mode="a", index=False
    )
    multivertex_geometry = [fid_to_geometry[record["source_fid"]] for record in result.multivertex_records]
    _spatial_records(result.multivertex_records, multivertex_geometry, crs).to_file(
        gpkg_path, layer="multivertex_edges", driver="GPKG", mode="a", index=False
    )
    _stabilize_gpkg_metadata(gpkg_path)
    return paths


def print_report(result: AuditResult, paths: dict[str, Path]) -> None:
    summary = result.report["summary"]
    topology = result.report["topology"]
    integrity = result.report["source_integrity"]
    print("=" * 60)
    print("GeoGami Env39 - Phase 5A Geometry and Topology Audit")
    print("=" * 60)
    print(f"Source SHA-256 before: {integrity['sha256_before']}")
    print(f"Source SHA-256 after:  {integrity['sha256_after']}")
    print(f"Source unchanged:      {integrity['unchanged']}")
    print()
    print(f"Edges / exact nodes / components: {topology['edge_count']} / {topology['exact_node_count']} / {topology['component_count']}")
    print(f"Degree-2 nodes: {summary['degree2_node_count']}")
    for record in result.degree2_records:
        print(
            f"  {record['temporary_node_id']} ({record['x']}, {record['y']}): "
            f"{record['classification']} ({record['angle_between_outgoing_directions_degrees']} degrees)"
        )
    print(f"Exact horizontal edges: {summary['exact_horizontal_edge_count']}")
    print(f"Exact vertical edges: {summary['exact_vertical_edge_count']}")
    print(f"Straight non-axis-aligned edges: {summary['straight_non_axis_aligned_edge_count']}")
    print(f"Multi-vertex edges: {summary['multivertex_edge_count']}")
    print(f"Genuinely non-collinear multi-vertex edges: {summary['genuinely_non_collinear_multivertex_edge_count']}")
    print(f"Geometry errors: empty={summary['empty_geometry_count']}, invalid={summary['invalid_geometry_count']}, zero_length={summary['zero_length_edge_count']}, self_intersection={summary['self_intersection_count']}")
    print()
    print(f"FINAL RESULT: {result.report['final_result']}")
    print("Outputs:")
    for name, path in paths.items():
        print(f"  {name}: {path}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--layer", default=DEFAULT_LAYER)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.source.is_file():
        raise FileNotFoundError(f"Source GeoPackage does not exist: {args.source}")
    hash_before = sha256_file(args.source)
    result = audit_source(args.source, args.layer)
    hash_after = sha256_file(args.source)
    result.report["source_integrity"] = {
        "sha256_before": hash_before,
        "sha256_after": hash_after,
        "unchanged": hash_before == hash_after,
    }
    if hash_before != hash_after:
        _add_issue(
            result.issues,
            "source_hash_changed",
            "error",
            "Source GeoPackage SHA-256 changed during read-only analysis.",
        )
    finalize_result(result)
    paths = write_outputs(result, args.output_dir)

    final_hash = sha256_file(args.source)
    if final_hash != hash_before:
        result.report["source_integrity"]["sha256_after"] = final_hash
        result.report["source_integrity"]["unchanged"] = False
        if not any(issue.issue_type == "source_hash_changed" for issue in result.issues):
            _add_issue(
                result.issues,
                "source_hash_changed",
                "error",
                "Source GeoPackage SHA-256 changed during output generation.",
            )
        finalize_result(result)
        write_outputs(result, args.output_dir)

    print_report(result, paths)
    return 0 if result.report["final_result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
