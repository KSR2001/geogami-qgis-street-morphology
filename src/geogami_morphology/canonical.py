"""Safe publication pipeline for topology-preserving QGIS geometry edits."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import csv
import math
from pathlib import Path
import tempfile
from typing import Any

import geopandas as gpd
from shapely.geometry import LineString, Point

from .io import atomic_publish, read_network, sha256_file, write_json, write_network
from .identity import scientific_content_signature
from .validation import TOPOLOGY_SERIALIZATION, graph_statistics, intersection_errors


DEFAULT_ENDPOINT_TOLERANCE = 1e-6
IDENTITY_NODE_FIELDS = ("node_id",)
IDENTITY_EDGE_FIELDS = ("edge_id", "u", "v", "key")


class PipelineError(RuntimeError):
    """A scientifically invalid input or candidate prevented publication."""

    def __init__(self, message: str, report: dict[str, Any] | None = None):
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class PipelineResult:
    output: Path
    diagnostics_dir: Path
    report: dict[str, Any]


def _duplicates(values: list[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)

def _issue(domain: str, issue_type: str, message: str, **details: Any) -> dict[str, Any]:
    return {"domain": domain, "issue_type": issue_type, "severity": "error", "message": message, **details}


def _point_xy(value: Any, node_id: str, issues: list[dict[str, Any]], source: str) -> tuple[float, float] | None:
    if value is None or not isinstance(value, Point) or value.is_empty or not value.is_valid:
        issues.append(_issue("geometry", "invalid_node_geometry", f"{source} node is not a valid non-empty Point.", node_id=node_id))
        return None
    if value.has_z:
        issues.append(_issue("geometry", "unsupported_z_coordinate", f"{source} node must be two-dimensional.", node_id=node_id))
        return None
    return float(value.x), float(value.y)


def _records(frame: gpd.GeoDataFrame, field: str) -> tuple[dict[str, Any], list[str]]:
    if field not in frame:
        return {}, []
    values = frame[field].astype(str).tolist()
    return {str(getattr(row, field)): row for row in frame.itertuples()}, _duplicates(values)


def _distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _write_reports(report: dict[str, Any], diagnostics_dir: Path) -> None:
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    write_json(diagnostics_dir / "canonical_pipeline_report.json", report)
    diagnostics = report.get("diagnostics", [])
    columns = ["domain", "issue_type", "severity", "message", "node_id", "edge_id", "edge_id_2", "distance_local_units"]
    with (diagnostics_dir / "canonical_pipeline_diagnostics.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(diagnostics)
    topology = report.get("topology", {})
    lines = [
        f"GeoGami canonical pipeline: {report['final_result']}",
        f"Environment: {report['environment']}",
        f"Mode: {report['mode']}",
        f"Source: {report['source']['path']}",
        f"Source SHA-256: {report['source']['sha256_before']}",
        f"Reference: {report['reference']['path']}",
        f"Reference SHA-256: {report['reference']['sha256_before']}",
        f"Candidate SHA-256: {report.get('candidate', {}).get('sha256')}",
        f"Nodes / edges / components: {topology.get('node_count')} / {topology.get('edge_count')} / {topology.get('connected_components')}",
        f"Degree distribution: {topology.get('degree_distribution')}",
        f"Topology signature: {topology.get('topology_signature')}",
        f"Endpoint corrections: {len(report.get('endpoint_corrections', []))}",
        f"Topology errors: {report.get('error_counts', {}).get('topology', 0)}",
        f"Geometry errors: {report.get('error_counts', {}).get('geometry', 0)}",
    ]
    (diagnostics_dir / "canonical_pipeline_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _base_report(environment: str, input_path: Path, reference_path: Path, output: Path, source_hash: str, reference_hash: str) -> dict[str, Any]:
    return {
        "phase": "7B",
        "environment": environment,
        "mode": "preserve-topology",
        "final_result": "FAIL",
        "published": False,
        "source": {"path": str(input_path), "sha256_before": source_hash, "sha256_after": None, "unchanged": None},
        "reference": {"path": str(reference_path), "sha256_before": reference_hash, "sha256_after": None, "unchanged": None},
        "output": str(output),
        "candidate": {"sha256": None, "scientific_content_sha256": None},
        "topology": {},
        "reference_topology": {},
        "endpoint_tolerance_local_units": DEFAULT_ENDPOINT_TOLERANCE,
        "endpoint_corrections": [],
        "diagnostics": [],
        "error_counts": {"topology": 0, "geometry": 0, "io": 0},
        "methodology": {"topology_serialization": TOPOLOGY_SERIALIZATION, "arbitrary_snapping": False, "source_modified": False},
    }


def run_preserve_topology(
    environment: str,
    input_path: Path,
    reference_path: Path,
    output: Path,
    diagnostics_dir: Path | None = None,
    endpoint_tolerance: float = DEFAULT_ENDPOINT_TOLERANCE,
) -> PipelineResult:
    input_path, reference_path, output = map(Path, (input_path, reference_path, output))
    diagnostics_dir = Path(diagnostics_dir) if diagnostics_dir else output.parent / f"{output.stem}_qa"
    if input_path.resolve() == output.resolve() or reference_path.resolve() == output.resolve():
        raise PipelineError("Output must differ from editable input and reference canonical paths.")
    if endpoint_tolerance < 0 or not math.isfinite(endpoint_tolerance):
        raise PipelineError("Endpoint tolerance must be a finite non-negative value.")
    source_hash, reference_hash = sha256_file(input_path), sha256_file(reference_path)
    report = _base_report(environment, input_path, reference_path, output, source_hash, reference_hash)
    report["endpoint_tolerance_local_units"] = endpoint_tolerance
    issues: list[dict[str, Any]] = report["diagnostics"]
    candidate_path: Path | None = None
    try:
        source_nodes, source_edges = read_network(input_path)
        ref_nodes, ref_edges = read_network(reference_path)
        for frame, fields, label in ((source_nodes, IDENTITY_NODE_FIELDS, "editable nodes"), (source_edges, IDENTITY_EDGE_FIELDS, "editable edges"), (ref_nodes, IDENTITY_NODE_FIELDS, "reference nodes"), (ref_edges, IDENTITY_EDGE_FIELDS, "reference edges")):
            missing = [field for field in fields if field not in frame]
            if missing:
                issues.append(_issue("topology", "missing_identity_fields", f"{label} lacks {missing}."))
        if issues:
            raise PipelineError("Required canonical identity fields are missing.", report)
        if source_nodes.crs is None or source_edges.crs is None or ref_nodes.crs is None or ref_edges.crs is None:
            issues.append(_issue("geometry", "missing_crs", "Every nodes and edges layer must declare the local engineering CRS."))
        elif source_nodes.crs != source_edges.crs or source_nodes.crs != ref_nodes.crs or ref_nodes.crs != ref_edges.crs:
            issues.append(_issue("geometry", "crs_mismatch", "Editable and reference nodes/edges CRS definitions must be identical."))
        elif source_nodes.crs.is_geographic:
            issues.append(_issue("geometry", "geographic_crs_forbidden", "Preserve-topology output requires the local engineering CRS, not a geographic CRS."))

        source_node_rows, duplicate_source_nodes = _records(source_nodes, "node_id")
        source_edge_rows, duplicate_source_edges = _records(source_edges, "edge_id")
        ref_node_rows, duplicate_ref_nodes = _records(ref_nodes, "node_id")
        ref_edge_rows, duplicate_ref_edges = _records(ref_edges, "edge_id")
        for label, values in (("editable node_id", duplicate_source_nodes), ("editable edge_id", duplicate_source_edges), ("reference node_id", duplicate_ref_nodes), ("reference edge_id", duplicate_ref_edges)):
            if values:
                issues.append(_issue("topology", "duplicate_canonical_id", f"Duplicate {label} values: {values}"))

        source_node_ids, ref_node_ids = set(source_node_rows), set(ref_node_rows)
        source_edge_ids, ref_edge_ids = set(source_edge_rows), set(ref_edge_rows)
        for value in sorted(ref_node_ids - source_node_ids):
            issues.append(_issue("topology", "missing_node", "Reference node is missing.", node_id=value))
        for value in sorted(source_node_ids - ref_node_ids):
            issues.append(_issue("topology", "extra_node", "Noncanonical node is present.", node_id=value))
        for value in sorted(ref_edge_ids - source_edge_ids):
            issues.append(_issue("topology", "missing_edge", "Reference edge is missing.", edge_id=value))
        for value in sorted(source_edge_ids - ref_edge_ids):
            issues.append(_issue("topology", "extra_edge", "Noncanonical edge is present.", edge_id=value))

        def connectivity(rows: dict[str, Any], label: str) -> dict[str, tuple[str, str, int]]:
            values: dict[str, tuple[str, str, int]] = {}
            for edge_id, row in rows.items():
                try:
                    key_value = int(row.key)
                    if float(row.key) != key_value:
                        raise ValueError
                    values[edge_id] = (str(row.u), str(row.v), key_value)
                except (TypeError, ValueError, OverflowError):
                    issues.append(_issue("topology", "invalid_key", f"{label} key is not an integer.", edge_id=edge_id))
            return values

        source_connectivity = connectivity(source_edge_rows, "Editable")
        ref_connectivity = connectivity(ref_edge_rows, "Reference")
        for edge_id in sorted(source_edge_ids & ref_edge_ids):
            observed, expected = source_connectivity.get(edge_id), ref_connectivity.get(edge_id)
            if observed is not None and expected is not None and observed != expected:
                field = "changed_key" if observed[:2] == expected[:2] else "changed_connectivity"
                issues.append(_issue("topology", field, f"Expected {expected}, observed {observed}.", edge_id=edge_id))
        for edge_id, (u, v, _key) in source_connectivity.items():
            if u not in source_node_ids or v not in source_node_ids:
                issues.append(_issue("topology", "unknown_endpoint_node", f"Edge endpoints {u}, {v} are not both present.", edge_id=edge_id))

        source_stats = graph_statistics(source_node_ids, source_connectivity.values())
        ref_stats = graph_statistics(ref_node_ids, ref_connectivity.values())
        report["topology"] = {key: value for key, value in source_stats.items() if key != "degrees"}
        report["reference_topology"] = {key: value for key, value in ref_stats.items() if key != "degrees"}
        for field in ("node_count", "edge_count", "connected_components", "degree_distribution", "topology_signature"):
            if source_stats[field] != ref_stats[field]:
                issues.append(_issue("topology", f"{field}_mismatch", f"Expected {ref_stats[field]}, observed {source_stats[field]}."))

        node_points: dict[str, Point] = {}
        for node_id in sorted(source_node_ids):
            coordinate = _point_xy(source_node_rows[node_id].geometry, node_id, issues, "Editable")
            if coordinate is not None:
                node_points[node_id] = Point(coordinate)
        ref_points: dict[str, Point] = {}
        for node_id in sorted(ref_node_ids):
            coordinate = _point_xy(ref_node_rows[node_id].geometry, node_id, issues, "Reference")
            if coordinate is not None:
                ref_points[node_id] = Point(coordinate)

        candidate_geometries: dict[str, LineString] = {}
        endpoint_roles: dict[str, tuple[str, str]] = {}
        for edge_id in sorted(source_edge_ids & ref_edge_ids):
            row, ref_row = source_edge_rows[edge_id], ref_edge_rows[edge_id]
            geometry, reference_geometry = row.geometry, ref_row.geometry
            if geometry is None or not isinstance(geometry, LineString) or geometry.is_empty or not geometry.is_valid or geometry.length <= 0:
                issues.append(_issue("geometry", "invalid_edge_geometry", "Editable edge must be a valid, positive-length LineString.", edge_id=edge_id))
                continue
            if geometry.has_z:
                issues.append(_issue("geometry", "unsupported_z_coordinate", "Editable edge must be two-dimensional.", edge_id=edge_id))
                continue
            if not geometry.is_simple:
                issues.append(_issue("geometry", "self_intersection", "Editable edge is self-intersecting.", edge_id=edge_id))
                continue
            if reference_geometry is None or not isinstance(reference_geometry, LineString) or reference_geometry.is_empty:
                issues.append(_issue("geometry", "invalid_reference_edge", "Reference edge is not a non-empty LineString.", edge_id=edge_id))
                continue
            expected = ref_connectivity.get(edge_id)
            if expected is None or expected[0] not in ref_points or expected[1] not in ref_points or expected[0] not in node_points or expected[1] not in node_points:
                continue
            u, v, _key = expected
            ref_start, ref_end = Point(reference_geometry.coords[0][:2]), Point(reference_geometry.coords[-1][:2])
            if ref_start.wkb == ref_points[u].wkb and ref_end.wkb == ref_points[v].wkb:
                start_role, end_role = u, v
            elif ref_start.wkb == ref_points[v].wkb and ref_end.wkb == ref_points[u].wkb:
                start_role, end_role = v, u
            else:
                issues.append(_issue("geometry", "invalid_reference_endpoints", "Reference edge endpoints do not exactly match its prescribed nodes.", edge_id=edge_id))
                continue
            endpoint_roles[edge_id] = (u, v)
            coordinates = [tuple(map(float, coordinate[:2])) for coordinate in geometry.coords]
            current_start, current_end = coordinates[0], coordinates[-1]
            target_start = (float(node_points[start_role].x), float(node_points[start_role].y))
            target_end = (float(node_points[end_role].x), float(node_points[end_role].y))
            expected_cost = _distance(current_start, target_start) + _distance(current_end, target_end)
            reverse_cost = _distance(current_start, target_end) + _distance(current_end, target_start)
            if reverse_cost < expected_cost:
                issues.append(_issue("geometry", "reversed_edge_orientation", "Editable LineString is reversed relative to canonical provenance orientation.", edge_id=edge_id))
                continue
            endpoint_failed = False
            for position, current, target, node_id in (("first", current_start, target_start, start_role), ("last", current_end, target_end, end_role)):
                distance = _distance(current, target)
                if distance > endpoint_tolerance:
                    issues.append(_issue("geometry", "endpoint_mismatch", f"Endpoint is {distance:.17g} local units from prescribed node; no arbitrary snapping was performed.", edge_id=edge_id, node_id=node_id, distance_local_units=distance))
                    endpoint_failed = True
                elif distance > 0:
                    coordinates[0 if position == "first" else -1] = target
                    report["endpoint_corrections"].append({"edge_id": edge_id, "endpoint": position, "node_id": node_id, "from": list(current), "to": list(target), "distance_local_units": distance})
            if endpoint_failed:
                continue
            candidate = LineString(coordinates)
            if not candidate.is_valid or not candidate.is_simple or candidate.length <= 0:
                issues.append(_issue("geometry", "invalid_after_endpoint_correction", "Safe endpoint synchronization would create invalid geometry.", edge_id=edge_id))
                continue
            candidate_geometries[edge_id] = candidate

        if len(candidate_geometries) == len(source_edge_ids) and len(node_points) == len(source_node_ids):
            issues.extend(intersection_errors(candidate_geometries, endpoint_roles, node_points))

        topology_error_count = sum(item["domain"] == "topology" for item in issues)
        geometry_error_count = sum(item["domain"] == "geometry" for item in issues)
        report["error_counts"] = {"topology": topology_error_count, "geometry": geometry_error_count, "io": 0}
        if issues:
            raise PipelineError("Edited network failed preserve-topology validation.", report)

        node_extras = [column for column in source_nodes.columns if column not in {"node_id", "x", "y", "degree", source_nodes.geometry.name}]
        edge_extras = [column for column in source_edges.columns if column not in {"edge_id", "u", "v", "key", "length_local", "vertex_count", source_edges.geometry.name}]
        node_records = []
        for node_id in sorted(source_node_ids):
            row, point = source_node_rows[node_id], node_points[node_id]
            node_records.append({"node_id": node_id, "x": float(point.x), "y": float(point.y), "degree": int(source_stats["degrees"][node_id]), **{column: getattr(row, column) for column in node_extras}, "geometry": point})
        edge_records = []
        for edge_id in sorted(source_edge_ids):
            row, geometry = source_edge_rows[edge_id], candidate_geometries[edge_id]
            u, v, key = source_connectivity[edge_id]
            edge_records.append({"edge_id": edge_id, "u": u, "v": v, "key": key, **{column: getattr(row, column) for column in edge_extras}, "length_local": float(geometry.length), "vertex_count": len(geometry.coords), "geometry": geometry})
        candidate_nodes = gpd.GeoDataFrame(node_records, geometry="geometry", crs=source_nodes.crs)
        candidate_edges = gpd.GeoDataFrame(edge_records, geometry="geometry", crs=source_edges.crs)

        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=f".{output.stem}.candidate-", suffix=".gpkg", dir=output.parent, delete=False) as stream:
            candidate_path = Path(stream.name)
        candidate_path.unlink()
        write_network(candidate_path, candidate_nodes, candidate_edges)
        # A complete reread verifies the actual serialized candidate, not only memory objects.
        published_nodes, published_edges = read_network(candidate_path)
        report["candidate"] = {
            "sha256": sha256_file(candidate_path),
            "scientific_content_sha256": scientific_content_signature(published_nodes, published_edges)[0],
        }
        if sha256_file(input_path) != source_hash or sha256_file(reference_path) != reference_hash:
            issues.append(_issue("io", "input_changed_during_run", "Editable input or reference changed while the candidate was being built."))
            report["error_counts"]["io"] = 1
            raise PipelineError("An input changed during pipeline execution.", report)
        atomic_publish(candidate_path, output)
        candidate_path = None
        report["published"] = True
        report["final_result"] = "PASS"
        report["source"].update({"sha256_after": sha256_file(input_path), "unchanged": True})
        report["reference"].update({"sha256_after": sha256_file(reference_path), "unchanged": True})
        _write_reports(report, diagnostics_dir)
        return PipelineResult(output, diagnostics_dir, report)
    except Exception as exc:
        if not isinstance(exc, PipelineError):
            issues.append(_issue("io", "pipeline_exception", str(exc)))
            report["error_counts"]["io"] = report["error_counts"].get("io", 0) + 1
        report["source"].update({"sha256_after": sha256_file(input_path), "unchanged": sha256_file(input_path) == source_hash})
        report["reference"].update({"sha256_after": sha256_file(reference_path), "unchanged": sha256_file(reference_path) == reference_hash})
        _write_reports(report, diagnostics_dir)
        raise PipelineError(str(exc), report) from exc
    finally:
        if candidate_path is not None and candidate_path.exists():
            candidate_path.unlink()
