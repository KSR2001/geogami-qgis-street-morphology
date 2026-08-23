"""Validate the frozen Environment 39 grid baseline without modifying it.

Topology is constructed exclusively from exact LineString endpoint tuples.  The
near-miss tolerance is diagnostic only and is never used to create graph nodes
or edges.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely.errors import GEOSException
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPoint, Point


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "baselines" / "grid" / "network.gpkg"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "results" / "baselines" / "grid"
DEFAULT_LAYER = "edges"
DEFAULT_NEAR_MISS_TOLERANCE = 0.01
DEFAULT_SHORT_EDGE_THRESHOLD = 1.0
REQUIRED_ATTRIBUTES = ("edge_id", "u", "v", "key", "condition", "notes")
ISSUE_COLUMNS = (
    "issue_id",
    "issue_type",
    "severity",
    "fid_1",
    "fid_2",
    "x",
    "y",
    "distance_if_applicable",
    "description",
)
FAIL_SEVERITIES = {"error"}


def _json_value(value: Any) -> Any:
    """Convert pandas/numpy/path values to deterministic JSON-compatible values."""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return value.as_posix()
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return _json_value(value.item())
    return str(value)


def _fid_sort_key(fid: Any) -> tuple[str, str]:
    return (type(fid).__name__, str(fid))


def _coord_xy(coord: tuple[float, ...]) -> tuple[float, float]:
    return (float(coord[0]), float(coord[1]))


def _coord_for_json(coord: tuple[float, ...]) -> list[float]:
    return [float(value) for value in coord]


def _canonical_pair(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    return (a, b) if a <= b else (b, a)


def _iter_parts(geometry: Any, wanted_type: type) -> Iterable[Any]:
    if geometry is None or geometry.is_empty:
        return
    if isinstance(geometry, wanted_type):
        yield geometry
    elif isinstance(geometry, (GeometryCollection, MultiPoint, MultiLineString)):
        for part in geometry.geoms:
            yield from _iter_parts(part, wanted_type)


def _point_is_exact_endpoint(point: Point, endpoints: tuple[tuple[float, ...], tuple[float, ...]]) -> bool:
    xy = (float(point.x), float(point.y))
    return any(_coord_xy(endpoint) == xy for endpoint in endpoints)


@dataclass
class Issue:
    issue_type: str
    severity: str
    description: str
    fid_1: Any = None
    fid_2: Any = None
    x: float | None = None
    y: float | None = None
    distance: float | None = None
    geometry: Any = field(default=None, repr=False)
    issue_id: str = ""

    def sort_key(self) -> tuple[Any, ...]:
        return (
            self.issue_type,
            _fid_sort_key(self.fid_1),
            _fid_sort_key(self.fid_2),
            float("inf") if self.x is None else self.x,
            float("inf") if self.y is None else self.y,
            self.description,
        )

    def as_record(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "issue_type": self.issue_type,
            "severity": self.severity,
            "fid_1": _json_value(self.fid_1),
            "fid_2": _json_value(self.fid_2),
            "x": self.x,
            "y": self.y,
            "distance_if_applicable": self.distance,
            "description": self.description,
        }


def _add_issue(
    issues: list[Issue],
    issue_type: str,
    severity: str,
    description: str,
    fid_1: Any = None,
    fid_2: Any = None,
    geometry: Any = None,
    distance: float | None = None,
    xy: tuple[float, float] | None = None,
) -> None:
    if xy is None and geometry is not None and not geometry.is_empty:
        representative = geometry if isinstance(geometry, Point) else geometry.representative_point()
        xy = (float(representative.x), float(representative.y))
    issues.append(
        Issue(
            issue_type=issue_type,
            severity=severity,
            description=description,
            fid_1=fid_1,
            fid_2=fid_2,
            x=None if xy is None else float(xy[0]),
            y=None if xy is None else float(xy[1]),
            distance=None if distance is None else float(distance),
            geometry=geometry,
        )
    )


def _read_layer(path: Path, layer: str) -> gpd.GeoDataFrame:
    """Read with the GeoPackage feature id as the index when pyogrio is available."""
    try:
        frame = gpd.read_file(path, layer=layer, engine="pyogrio", fid_as_index=True)
    except (ImportError, ModuleNotFoundError):
        frame = gpd.read_file(path, layer=layer)
    if frame.index.name != "fid":
        frame.index = pd.Index(frame.index, name="fid")
    return frame


def _list_layers(path: Path) -> list[str]:
    if hasattr(gpd, "list_layers"):
        return gpd.list_layers(path)["name"].astype(str).tolist()
    import fiona

    return list(fiona.listlayers(path))


def _source_attributes(row: pd.Series) -> dict[str, Any]:
    return {
        str(column): _json_value(value)
        for column, value in row.items()
        if column != "geometry"
    }


def validate(
    input_path: Path,
    layer: str = DEFAULT_LAYER,
    near_miss_tolerance: float = DEFAULT_NEAR_MISS_TOLERANCE,
    short_edge_threshold: float = DEFAULT_SHORT_EDGE_THRESHOLD,
) -> tuple[dict[str, Any], list[Issue], gpd.GeoDataFrame | None]:
    issues: list[Issue] = []
    report: dict[str, Any] = {
        "validator": "GeoGami Env39 Grid Baseline Phase 4 QA",
        "input": {
            "path": input_path.resolve().as_posix(),
            "layer": layer,
            "exists": input_path.is_file(),
        },
        "configuration": {
            "topology_identity": "exact unrounded LineString endpoint tuples",
            "distance_model": "planar local Cartesian units",
            "near_miss_tolerance_local_units": near_miss_tolerance,
            "near_miss_tolerance_usage": "diagnostic reporting only; never topology construction",
            "extremely_short_edge_threshold_local_units": short_edge_threshold,
        },
    }

    if not input_path.is_file():
        _add_issue(issues, "missing_input", "error", f"GeoPackage does not exist: {input_path}")
        report.update(_empty_results())
        return _finalize_report(report, issues), issues, None

    try:
        layers = _list_layers(input_path)
    except Exception as exc:
        _add_issue(issues, "unreadable_input", "error", f"Could not inspect GeoPackage layers: {exc}")
        report.update(_empty_results())
        return _finalize_report(report, issues), issues, None

    report["input"]["available_layers"] = sorted(layers)
    if layer not in layers:
        _add_issue(issues, "missing_layer", "error", f'Required layer "{layer}" is absent.')
        report.update(_empty_results())
        return _finalize_report(report, issues), issues, None

    try:
        frame = _read_layer(input_path, layer)
    except Exception as exc:
        _add_issue(issues, "unreadable_layer", "error", f'Could not read layer "{layer}": {exc}')
        report.update(_empty_results())
        return _finalize_report(report, issues), issues, None

    crs = frame.crs
    crs_metadata = {
        "text": None if crs is None else str(crs),
        "wkt": None if crs is None else crs.to_wkt(),
        "is_geographic": None if crs is None else bool(crs.is_geographic),
    }
    report["input"].update(
        {
            "feature_count": int(len(frame)),
            "source_fid_index_name": str(frame.index.name),
            "columns": [str(column) for column in frame.columns],
            "crs": crs_metadata,
        }
    )
    if crs is None:
        _add_issue(issues, "missing_crs", "warning", "Layer has no CRS metadata.")
    elif crs.is_geographic:
        _add_issue(
            issues,
            "geographic_crs",
            "error",
            "Layer CRS is geographic; this validator permits planar local Cartesian data only.",
        )

    missing_attributes = [name for name in REQUIRED_ATTRIBUTES if name not in frame.columns]
    for attribute in missing_attributes:
        _add_issue(issues, "missing_attribute", "error", f'Required source attribute "{attribute}" is absent.')

    if "condition" in frame.columns:
        for fid, value in frame["condition"].items():
            if value != "grid_baseline":
                _add_issue(
                    issues,
                    "unexpected_condition",
                    "error",
                    f'Expected condition="grid_baseline"; found {value!r}.',
                    fid_1=fid,
                )
    unexpected_keys: list[dict[str, Any]] = []
    if "key" in frame.columns:
        counts = frame["key"].value_counts(dropna=False, sort=False)
        for value, count in sorted(counts.items(), key=lambda item: str(item[0])):
            if value != 0:
                unexpected_keys.append({"value": _json_value(value), "count": int(count)})
                for fid in frame.index[frame["key"].isna() if pd.isna(value) else frame["key"].eq(value)]:
                    _add_issue(
                        issues,
                        "unexpected_key",
                        "warning",
                        f"Expected key=0; found {value!r}.",
                        fid_1=fid,
                    )

    graph = nx.MultiGraph()
    valid_lines: list[tuple[Any, LineString, tuple[tuple[float, ...], tuple[float, ...]]]] = []
    lengths: list[float] = []
    grid_counts = Counter(horizontal=0, vertical=0, other_straight=0, multi_vertex=0)

    for fid, row in frame.iterrows():
        geometry = row.geometry
        if geometry is None:
            _add_issue(issues, "null_geometry", "error", "Geometry is null.", fid_1=fid)
            continue
        if geometry.is_empty:
            _add_issue(issues, "empty_geometry", "error", "Geometry is empty.", fid_1=fid)
            continue
        if geometry.geom_type != "LineString":
            _add_issue(
                issues,
                "invalid_geometry_type",
                "error",
                f"Expected LineString; found {geometry.geom_type}.",
                fid_1=fid,
                geometry=geometry,
            )
            continue
        if not geometry.is_valid:
            _add_issue(issues, "invalid_geometry", "error", "LineString geometry is invalid.", fid_1=fid, geometry=geometry)

        coordinates = tuple(tuple(float(value) for value in coordinate) for coordinate in geometry.coords)
        if len(coordinates) < 2:
            _add_issue(issues, "too_few_coordinates", "error", "LineString has fewer than two coordinates.", fid_1=fid)
            continue

        length = float(geometry.length)
        lengths.append(length)
        if length <= 0:
            _add_issue(issues, "zero_length_edge", "error", "LineString has non-positive planar length.", fid_1=fid, geometry=geometry)
        elif length < short_edge_threshold:
            _add_issue(
                issues,
                "extremely_short_edge",
                "warning",
                f"Edge length {length:.12g} is below {short_edge_threshold:.12g} local units.",
                fid_1=fid,
                geometry=geometry,
                distance=length,
            )

        start, end = coordinates[0], coordinates[-1]
        endpoints = (start, end)
        if start == end:
            _add_issue(issues, "self_loop", "error", "Exact start and end endpoint tuples are identical.", fid_1=fid, geometry=geometry)
        graph.add_edge(
            start,
            end,
            key=fid,
            source_fid=_json_value(fid),
            source_attributes=_source_attributes(row),
            length=length,
        )
        valid_lines.append((fid, geometry, endpoints))

        if len(coordinates) > 2:
            grid_counts["multi_vertex"] += 1
        else:
            dx = end[0] - start[0]
            dy = end[1] - start[1]
            if dy == 0:
                grid_counts["horizontal"] += 1
            elif dx == 0:
                grid_counts["vertical"] += 1
            else:
                grid_counts["other_straight"] += 1

    valid_lines.sort(key=lambda item: _fid_sort_key(item[0]))
    _detect_pairwise_geometry_issues(valid_lines, issues)
    _detect_parallel_edges(valid_lines, issues)
    near_misses = _detect_near_misses(valid_lines, near_miss_tolerance, issues)

    components = [sorted(component) for component in nx.connected_components(graph)]
    components.sort(key=lambda component: (-len(component), component))
    if len(components) > 1:
        _add_issue(
            issues,
            "disconnected_graph",
            "error",
            f"Exact-endpoint graph has {len(components)} connected components.",
        )

    degree_distribution = Counter(dict(graph.degree()).values())
    for node, degree in sorted(graph.degree(), key=lambda item: item[0]):
        if degree > 4:
            _add_issue(
                issues,
                "high_degree_node",
                "warning",
                f"Exact node degree is {degree}, greater than 4.",
                geometry=Point(_coord_xy(node)),
            )
        if degree == 0:
            _add_issue(
                issues,
                "isolated_node",
                "warning",
                "Exact graph node has degree 0.",
                geometry=Point(_coord_xy(node)),
            )

    component_details = []
    for number, component in enumerate(components, start=1):
        component_details.append(
            {
                "component_id": number,
                "node_count": len(component),
                "edge_count": int(graph.subgraph(component).number_of_edges()),
                "nodes": [_coord_for_json(node) for node in component],
            }
        )

    node_count = graph.number_of_nodes()
    edge_count = graph.number_of_edges()
    total_length = float(sum(lengths))
    statistics = {
        "source_feature_count": int(len(frame)),
        "exact_derived_node_count": int(node_count),
        "graph_edge_count": int(edge_count),
        "connected_component_count": len(components),
        "component_sizes_nodes": [len(component) for component in components],
        "components": component_details,
        "degree_distribution": {str(degree): int(count) for degree, count in sorted(degree_distribution.items())},
        "degree_1_count": int(degree_distribution.get(1, 0)),
        "degree_2_count": int(degree_distribution.get(2, 0)),
        "degree_3_count": int(degree_distribution.get(3, 0)),
        "degree_4_count": int(degree_distribution.get(4, 0)),
        "degree_greater_than_4_count": int(sum(count for degree, count in degree_distribution.items() if degree > 4)),
        "isolated_node_count": int(degree_distribution.get(0, 0)),
        "average_degree_streets_per_node": None if node_count == 0 else float(sum(dict(graph.degree()).values()) / node_count),
        "total_planar_street_length": total_length,
        "mean_edge_length": None if not lengths else float(pd.Series(lengths).mean()),
        "median_edge_length": None if not lengths else float(pd.Series(lengths).median()),
        "multiple_edge_node_pair_count": 0,
    }
    multiplicities = Counter(
        _canonical_pair(start, end)
        for start, end, _key in graph.edges(keys=True)
    )
    statistics["multiple_edge_node_pair_count"] = sum(
        multiplicity > 1 for multiplicity in multiplicities.values()
    )
    geometry_qa = {
        "exact_horizontal_edge_count": int(grid_counts["horizontal"]),
        "exact_vertical_edge_count": int(grid_counts["vertical"]),
        "other_straight_edge_count": int(grid_counts["other_straight"]),
        "multi_vertex_linestring_count": int(grid_counts["multi_vertex"]),
        "extremely_short_edge_count": sum(issue.issue_type == "extremely_short_edge" for issue in issues),
    }
    report.update(
        {
            "schema_validation": {
                "required_attributes": list(REQUIRED_ATTRIBUTES),
                "missing_attributes": missing_attributes,
                "unexpected_key_values": unexpected_keys,
            },
            "graph_statistics": statistics,
            "grid_geometry_qa": geometry_qa,
            "near_miss_diagnostics": {
                "pair_count": len(near_misses),
                "pairs": near_misses,
            },
            "exact_graph": {
                "nodes": [
                    {
                        "coordinate": _coord_for_json(node),
                        "degree": int(graph.degree(node)),
                    }
                    for node in sorted(graph.nodes)
                ],
                "edges": [
                    {
                        "start": _coord_for_json(start),
                        "end": _coord_for_json(end),
                        "graph_key": _json_value(key),
                        **data,
                    }
                    for start, end, key, data in sorted(
                        graph.edges(keys=True, data=True),
                        key=lambda edge: (
                            edge[0],
                            edge[1],
                            _fid_sort_key(edge[2]),
                        ),
                    )
                ],
            },
        }
    )
    return _finalize_report(report, issues), issues, frame


def _detect_pairwise_geometry_issues(
    lines: list[tuple[Any, LineString, tuple[tuple[float, ...], tuple[float, ...]]]],
    issues: list[Issue],
) -> None:
    for index, (fid_1, geometry_1, endpoints_1) in enumerate(lines):
        coords_1 = tuple(geometry_1.coords)
        for fid_2, geometry_2, endpoints_2 in lines[index + 1 :]:
            coords_2 = tuple(geometry_2.coords)
            if coords_1 == coords_2:
                _add_issue(
                    issues,
                    "identical_duplicate_geometry",
                    "error",
                    "Two features have identical ordered coordinate sequences.",
                    fid_1,
                    fid_2,
                    geometry_1,
                )
            elif coords_1 == tuple(reversed(coords_2)):
                _add_issue(
                    issues,
                    "reversed_duplicate_geometry",
                    "error",
                    "Two features have identical coordinate sequences in reverse order.",
                    fid_1,
                    fid_2,
                    geometry_1,
                )

            try:
                intersection = geometry_1.intersection(geometry_2)
            except GEOSException as exc:
                _add_issue(
                    issues,
                    "intersection_evaluation_error",
                    "error",
                    f"Could not evaluate pairwise intersection: {exc}",
                    fid_1,
                    fid_2,
                )
                continue
            if intersection.is_empty:
                continue

            line_parts = list(_iter_parts(intersection, LineString))
            overlap_length = sum(float(part.length) for part in line_parts)
            if overlap_length > 0:
                topologically_equal = geometry_1.equals(geometry_2)
                issue_type = "exact_overlapping_linestring" if topologically_equal else "partial_collinear_overlap"
                description = (
                    "Two features occupy the same complete physical LineString."
                    if topologically_equal
                    else f"Two features overlap along {overlap_length:.12g} local units."
                )
                overlap_geometry = line_parts[0] if len(line_parts) == 1 else MultiLineString(line_parts)
                _add_issue(issues, issue_type, "error", description, fid_1, fid_2, overlap_geometry, overlap_length)

            for point in _iter_parts(intersection, Point):
                endpoint_1 = _point_is_exact_endpoint(point, endpoints_1)
                endpoint_2 = _point_is_exact_endpoint(point, endpoints_2)
                if endpoint_1 and endpoint_2:
                    continue
                if endpoint_1 != endpoint_2:
                    issue_type = "endpoint_interior_unsplit_intersection"
                    description = "An edge endpoint meets the interior of another unsplit edge."
                else:
                    issue_type = "interior_interior_unsplit_intersection"
                    description = "Two edge interiors intersect without a shared analytical endpoint."
                _add_issue(issues, issue_type, "error", description, fid_1, fid_2, point)


def _detect_parallel_edges(
    lines: list[tuple[Any, LineString, tuple[tuple[float, ...], tuple[float, ...]]]],
    issues: list[Issue],
) -> None:
    endpoint_groups: dict[tuple[tuple[float, ...], tuple[float, ...]], list[tuple[Any, LineString]]] = {}
    for fid, geometry, (start, end) in lines:
        endpoint_groups.setdefault(_canonical_pair(start, end), []).append((fid, geometry))
    for pair, members in sorted(endpoint_groups.items(), key=lambda item: item[0]):
        if len(members) < 2:
            continue
        members.sort(key=lambda item: _fid_sort_key(item[0]))
        for index, (fid_1, geometry_1) in enumerate(members):
            for fid_2, _ in members[index + 1 :]:
                _add_issue(
                    issues,
                    "duplicate_endpoint_pair",
                    "warning",
                    "Multiple physical-edge candidates share the same exact unordered endpoint pair; review as possible parallel edges.",
                    fid_1,
                    fid_2,
                    geometry_1,
                )


def _detect_near_misses(
    lines: list[tuple[Any, LineString, tuple[tuple[float, ...], tuple[float, ...]]]],
    tolerance: float,
    issues: list[Issue],
) -> list[dict[str, Any]]:
    endpoint_uses: dict[tuple[float, ...], set[Any]] = {}
    for fid, _geometry, endpoints in lines:
        for endpoint in endpoints:
            endpoint_uses.setdefault(endpoint, set()).add(fid)
    endpoints = sorted(endpoint_uses)
    near_misses: list[dict[str, Any]] = []
    if tolerance <= 0:
        return near_misses
    for index, endpoint_1 in enumerate(endpoints):
        x1, y1 = _coord_xy(endpoint_1)
        for endpoint_2 in endpoints[index + 1 :]:
            x2, y2 = _coord_xy(endpoint_2)
            if abs(x2 - x1) > tolerance or abs(y2 - y1) > tolerance:
                continue
            distance = math.hypot(x2 - x1, y2 - y1)
            if not 0 < distance <= tolerance:
                continue
            fids_1 = sorted(endpoint_uses[endpoint_1], key=_fid_sort_key)
            fids_2 = sorted(endpoint_uses[endpoint_2], key=_fid_sort_key)
            connector = LineString([(x1, y1), (x2, y2)])
            midpoint = ((x1 + x2) / 2, (y1 + y2) / 2)
            _add_issue(
                issues,
                "endpoint_near_miss",
                "warning",
                f"Distinct exact endpoints are {distance:.12g} local units apart; no snapping was applied.",
                fids_1[0],
                fids_2[0],
                connector,
                distance,
                midpoint,
            )
            near_misses.append(
                {
                    "endpoint_1": _coord_for_json(endpoint_1),
                    "endpoint_2": _coord_for_json(endpoint_2),
                    "fids_1": [_json_value(fid) for fid in fids_1],
                    "fids_2": [_json_value(fid) for fid in fids_2],
                    "distance_local_units": distance,
                }
            )
    near_misses.sort(key=lambda item: (item["distance_local_units"], item["endpoint_1"], item["endpoint_2"]))
    return near_misses


def _empty_results() -> dict[str, Any]:
    return {
        "schema_validation": {
            "required_attributes": list(REQUIRED_ATTRIBUTES),
            "missing_attributes": list(REQUIRED_ATTRIBUTES),
            "unexpected_key_values": [],
        },
        "graph_statistics": {
            "source_feature_count": 0,
            "exact_derived_node_count": 0,
            "graph_edge_count": 0,
            "connected_component_count": 0,
            "component_sizes_nodes": [],
            "components": [],
            "degree_distribution": {},
            "degree_1_count": 0,
            "degree_2_count": 0,
            "degree_3_count": 0,
            "degree_4_count": 0,
            "degree_greater_than_4_count": 0,
            "isolated_node_count": 0,
            "average_degree_streets_per_node": None,
            "total_planar_street_length": 0.0,
            "mean_edge_length": None,
            "median_edge_length": None,
            "multiple_edge_node_pair_count": 0,
        },
        "grid_geometry_qa": {
            "exact_horizontal_edge_count": 0,
            "exact_vertical_edge_count": 0,
            "other_straight_edge_count": 0,
            "multi_vertex_linestring_count": 0,
            "extremely_short_edge_count": 0,
        },
        "near_miss_diagnostics": {"pair_count": 0, "pairs": []},
        "exact_graph": {"nodes": [], "edges": []},
    }


def _finalize_report(report: dict[str, Any], issues: list[Issue]) -> dict[str, Any]:
    issues.sort(key=Issue.sort_key)
    for number, issue in enumerate(issues, start=1):
        issue.issue_id = f"QA-{number:04d}"
    issue_counts = Counter(issue.issue_type for issue in issues)
    error_count = sum(issue.severity in FAIL_SEVERITIES for issue in issues)
    warning_count = sum(issue.severity == "warning" for issue in issues)
    report["issues"] = [issue.as_record() for issue in issues]
    report["issue_counts_by_type"] = dict(sorted(issue_counts.items()))
    report["error_count"] = error_count
    report["warning_count"] = warning_count
    report["final_result"] = "FAIL" if error_count else "PASS"
    return report


def write_outputs(
    report: dict[str, Any],
    issues: list[Issue],
    source_frame: gpd.GeoDataFrame | None,
    output_dir: Path,
) -> dict[str, Path | None]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "topology_qa.json"
    summary_path = output_dir / "topology_qa_summary.csv"
    issues_path = output_dir / "topology_qa_issues.csv"
    spatial_path = output_dir / "qa_issues.gpkg"

    with json_path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(report, file, indent=2, ensure_ascii=False, allow_nan=False)
        file.write("\n")

    stats = report["graph_statistics"]
    grid = report["grid_geometry_qa"]
    summary_record = {
        "final_result": report["final_result"],
        "source_feature_count": stats["source_feature_count"],
        "exact_derived_node_count": stats["exact_derived_node_count"],
        "graph_edge_count": stats["graph_edge_count"],
        "connected_component_count": stats["connected_component_count"],
        "component_sizes_nodes": json.dumps(stats["component_sizes_nodes"], separators=(",", ":")),
        "degree_distribution": json.dumps(stats["degree_distribution"], sort_keys=True, separators=(",", ":")),
        "degree_1_count": stats["degree_1_count"],
        "degree_2_count": stats["degree_2_count"],
        "degree_3_count": stats["degree_3_count"],
        "degree_4_count": stats["degree_4_count"],
        "degree_greater_than_4_count": stats["degree_greater_than_4_count"],
        "average_degree_streets_per_node": stats["average_degree_streets_per_node"],
        "total_planar_street_length": stats["total_planar_street_length"],
        "mean_edge_length": stats["mean_edge_length"],
        "median_edge_length": stats["median_edge_length"],
        "multiple_edge_node_pair_count": stats["multiple_edge_node_pair_count"],
        "exact_horizontal_edge_count": grid["exact_horizontal_edge_count"],
        "exact_vertical_edge_count": grid["exact_vertical_edge_count"],
        "other_straight_edge_count": grid["other_straight_edge_count"],
        "multi_vertex_linestring_count": grid["multi_vertex_linestring_count"],
        "extremely_short_edge_count": grid["extremely_short_edge_count"],
        "structural_error_count": report["error_count"],
        "warning_count": report["warning_count"],
        "near_miss_warning_count": report["near_miss_diagnostics"]["pair_count"],
    }
    pd.DataFrame([summary_record]).to_csv(summary_path, index=False, lineterminator="\n")

    issue_frame = pd.DataFrame([issue.as_record() for issue in issues], columns=ISSUE_COLUMNS)
    issue_frame.to_csv(issues_path, index=False, lineterminator="\n")

    if spatial_path.exists():
        spatial_path.unlink()
    spatial_written = False
    crs = None if source_frame is None else source_frame.crs
    spatial_issues = [issue for issue in issues if issue.geometry is not None and not issue.geometry.is_empty]
    point_issues = [issue for issue in spatial_issues if isinstance(issue.geometry, Point)]
    line_issues = [issue for issue in spatial_issues if isinstance(issue.geometry, (LineString, MultiLineString))]
    if point_issues:
        _write_spatial_layer(point_issues, spatial_path, "issue_points", crs, mode="w")
        spatial_written = True
    if line_issues:
        _write_spatial_layer(line_issues, spatial_path, "issue_lines", crs, mode="a" if spatial_written else "w")
        spatial_written = True

    return {
        "json": json_path,
        "summary_csv": summary_path,
        "issues_csv": issues_path,
        "spatial_gpkg": spatial_path if spatial_written else None,
    }


def _write_spatial_layer(
    issues: list[Issue], path: Path, layer: str, crs: Any, mode: str
) -> None:
    records = [issue.as_record() for issue in issues]
    for record in records:
        record.pop("x")
        record.pop("y")
    spatial_frame = gpd.GeoDataFrame(
        records,
        geometry=[issue.geometry for issue in issues],
        crs=crs,
    )
    spatial_frame.to_file(path, layer=layer, driver="GPKG", mode=mode, index=False)


def print_terminal_report(report: dict[str, Any], paths: dict[str, Path | None]) -> None:
    stats = report["graph_statistics"]
    grid = report["grid_geometry_qa"]
    structural_counts = {
        issue_type: count
        for issue_type, count in report["issue_counts_by_type"].items()
        if any(issue["issue_type"] == issue_type and issue["severity"] == "error" for issue in report["issues"])
    }
    print("=" * 46)
    print("GeoGami Env39 Grid Baseline - Phase 4 QA")
    print("=" * 46)
    print()
    print(f"Input feature count: {stats['source_feature_count']}")
    print(f"Exact derived nodes: {stats['exact_derived_node_count']}")
    print(f"Graph edges: {stats['graph_edge_count']}")
    print(f"Components: {stats['connected_component_count']} ({stats['component_sizes_nodes']})")
    print()
    print("Degree distribution:")
    print(f"  {stats['degree_distribution']}")
    print(f"  Average degree / streets per node: {stats['average_degree_streets_per_node']}")
    print()
    print("Geometry:")
    print(f"  Exact horizontal: {grid['exact_horizontal_edge_count']}")
    print(f"  Exact vertical: {grid['exact_vertical_edge_count']}")
    print(f"  Other straight: {grid['other_straight_edge_count']}")
    print(f"  Multi-vertex LineStrings: {grid['multi_vertex_linestring_count']}")
    print(f"  Total planar street length: {stats['total_planar_street_length']}")
    print()
    print(f"Structural errors: {report['error_count']}")
    print(f"  {structural_counts or 'none'}")
    print(f"Near-miss warnings: {report['near_miss_diagnostics']['pair_count']}")
    print()
    print("FINAL RESULT:")
    print(report["final_result"])
    print()
    print("Outputs:")
    for name, path in paths.items():
        if path is not None:
            print(f"  {name}: {path}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Input GeoPackage path.")
    parser.add_argument("--layer", default=DEFAULT_LAYER, help='Input layer name (default: "edges").')
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory.")
    parser.add_argument(
        "--near-miss-tolerance",
        type=float,
        default=DEFAULT_NEAR_MISS_TOLERANCE,
        help="Diagnostic-only endpoint distance in local units (default: 0.01).",
    )
    parser.add_argument(
        "--short-edge-threshold",
        type=float,
        default=DEFAULT_SHORT_EDGE_THRESHOLD,
        help="Report positive edges shorter than this many local units (default: 1.0).",
    )
    args = parser.parse_args(argv)
    if args.near_miss_tolerance < 0:
        parser.error("--near-miss-tolerance must be non-negative")
    if args.short_edge_threshold < 0:
        parser.error("--short-edge-threshold must be non-negative")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report, issues, source_frame = validate(
        input_path=args.input,
        layer=args.layer,
        near_miss_tolerance=args.near_miss_tolerance,
        short_edge_threshold=args.short_edge_threshold,
    )
    paths = write_outputs(report, issues, source_frame, args.output_dir)
    print_terminal_report(report, paths)
    return 0 if report["final_result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
