"""Synchronize only Env38 edge endpoints to their prescribed node Points.

Execution is a dry-run unless ``--apply`` is supplied. Canonical Env39 edge
identity is authoritative, while either unambiguous working LineString
direction is accepted and preserved. No internal vertex, topology attribute,
node geometry, CRS, crossing, or near miss is modified by this utility.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from typing import Any
import uuid

import geopandas as gpd
import pandas as pd
from pandas.testing import assert_frame_equal
from shapely.geometry import LineString, Point

import validate_env38_topology as validator


DEFAULT_INPUT = validator.DEFAULT_INPUT
DEFAULT_CANONICAL = validator.DEFAULT_CANONICAL
SCIENTIFIC_NODE_FIELDS = ("node_id",)
SCIENTIFIC_EDGE_FIELDS = ("edge_id", "u", "v", "key")


class SynchronizationError(RuntimeError):
    """Raised when synchronization cannot be performed without ambiguity."""


class AmbiguousOrientationError(SynchronizationError):
    """Raised when working endpoints cannot be assigned safely to u and v."""


@dataclass(frozen=True)
class EndpointChange:
    edge_id: str
    endpoint: str
    node_id: str
    old_coordinate: tuple[float, float]
    required_coordinate: tuple[float, float]
    distance_changed: float


@dataclass(frozen=True)
class EdgeOrientation:
    edge_id: str
    orientation: str
    classification: str
    forward_assignment_cost: float
    reverse_assignment_cost: float
    endpoint_synchronization_required: bool


@dataclass
class SyncPlan:
    input_path: Path
    canonical_path: Path
    canonical_sha256: str
    edges_total: int
    edges_already_match: int
    affected_edge_ids: list[str]
    affected_node_ids: list[str]
    changes: list[EndpointChange]
    orientations: list[EdgeOrientation]
    replacement_geometries: dict[str, LineString]
    topology_signature_sha256: str

    @property
    def endpoint_count(self) -> int:
        return len(self.changes)


def _point_xy(geometry: Any, node_id: str, source: str) -> tuple[float, float]:
    if geometry is None or geometry.is_empty or not isinstance(geometry, Point) or not geometry.is_valid:
        raise SynchronizationError(f"{source} node {node_id} is not a valid, non-empty Point.")
    return float(geometry.x), float(geometry.y)


def _line_coordinates(geometry: Any, edge_id: str, source: str) -> tuple[tuple[float, ...], ...]:
    if geometry is None or geometry.is_empty or not isinstance(geometry, LineString) or not geometry.is_valid:
        raise SynchronizationError(f"{source} edge {edge_id} is not a valid, non-empty LineString.")
    coordinates = tuple(tuple(float(value) for value in coordinate) for coordinate in geometry.coords)
    if len(coordinates) < 2 or geometry.length <= 0 or not geometry.is_simple:
        raise SynchronizationError(f"{source} edge {edge_id} is not a positive-length simple LineString.")
    return coordinates


def _xy(coordinate: tuple[float, ...]) -> tuple[float, float]:
    return coordinate[0], coordinate[1]


def _distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _classify_orientation(edge_id: str, forward_cost: float, reverse_cost: float) -> str:
    """Return the clearly cheaper endpoint assignment, failing on a near tie."""
    if math.isclose(forward_cost, reverse_cost, rel_tol=1e-12, abs_tol=1e-12):
        raise AmbiguousOrientationError(
            f"Working edge {edge_id} has ambiguous endpoint assignment: "
            f"forward cost={forward_cost:.17g}, reverse cost={reverse_cost:.17g}."
        )
    return "forward" if forward_cost < reverse_cost else "reversed"


def _records_by_id(frame: gpd.GeoDataFrame, field: str, layer: str) -> dict[str, Any]:
    values = frame[field].astype(str).tolist()
    duplicates = sorted(value for value, count in pd.Series(values).value_counts().items() if count > 1)
    if duplicates:
        raise SynchronizationError(f"{layer} has duplicate {field} values: {duplicates}")
    return {str(getattr(row, field)): row for row in frame.itertuples()}


def _assert_preflight_topology(input_path: Path, canonical_path: Path) -> dict[str, Any]:
    result = validator.validate_candidate(input_path, canonical_path)
    report = result.report
    if report["topology_control_status"] != "PASS":
        failed = sorted(name for name, passed in report["topology_control"]["checks"].items() if not passed)
        raise SynchronizationError(f"Preflight topology control failed: {failed}")
    topology = report["topology_control"]
    if (
        report["candidate"]["node_count"] != validator.EXPECTED_NODE_COUNT
        or report["candidate"]["edge_count"] != validator.EXPECTED_EDGE_COUNT
        or topology["component_count"] != 1
        or topology["degree_distribution"] != {"1": 10, "3": 16, "4": 20}
        or topology["topology_signature_sha256"] != validator.EXPECTED_TOPOLOGY_SIGNATURE
    ):
        raise SynchronizationError("Preflight topology invariants do not match frozen Phase 6A control.")
    return report


def build_sync_plan(
    input_path: Path = DEFAULT_INPUT,
    canonical_path: Path = DEFAULT_CANONICAL,
) -> SyncPlan:
    input_path, canonical_path = Path(input_path), Path(canonical_path)
    canonical_hash = validator.sha256_file(canonical_path)
    if canonical_hash != validator.EXPECTED_CANONICAL_SHA256:
        raise SynchronizationError(
            f"Frozen canonical SHA-256 mismatch: expected {validator.EXPECTED_CANONICAL_SHA256}, observed {canonical_hash}."
        )
    preflight = _assert_preflight_topology(input_path, canonical_path)
    working_nodes, working_edges = validator._read_layers(input_path)
    canonical_nodes, canonical_edges = validator._read_layers(canonical_path)
    working_node_rows = _records_by_id(working_nodes, "node_id", "working nodes")
    working_edge_rows = _records_by_id(working_edges, "edge_id", "working edges")
    canonical_node_rows = _records_by_id(canonical_nodes, "node_id", "canonical nodes")
    canonical_edge_rows = _records_by_id(canonical_edges, "edge_id", "canonical edges")

    changes: list[EndpointChange] = []
    orientations: list[EdgeOrientation] = []
    replacements: dict[str, LineString] = {}
    matching_edges = 0
    for edge_id in sorted(canonical_edge_rows):
        canonical_edge = canonical_edge_rows[edge_id]
        working_edge = working_edge_rows[edge_id]
        if (str(working_edge.u), str(working_edge.v), int(working_edge.key)) != (
            str(canonical_edge.u), str(canonical_edge.v), int(canonical_edge.key)
        ):
            raise SynchronizationError(f"Scientific connectivity differs for {edge_id} despite topology preflight.")
        u, v = str(canonical_edge.u), str(canonical_edge.v)
        canonical_u = _point_xy(canonical_node_rows[u].geometry, u, "canonical")
        canonical_v = _point_xy(canonical_node_rows[v].geometry, v, "canonical")
        canonical_coordinates = _line_coordinates(canonical_edge.geometry, edge_id, "canonical")
        canonical_start, canonical_end = _xy(canonical_coordinates[0]), _xy(canonical_coordinates[-1])
        if not (
            (canonical_start == canonical_u and canonical_end == canonical_v)
            or (canonical_start == canonical_v and canonical_end == canonical_u)
        ):
            raise SynchronizationError(f"Canonical edge {edge_id} endpoints do not exactly match canonical nodes.")

        current_coordinates = _line_coordinates(working_edge.geometry, edge_id, "working")
        current_start, current_end = _xy(current_coordinates[0]), _xy(current_coordinates[-1])
        working_u = _point_xy(working_node_rows[u].geometry, u, "working")
        working_v = _point_xy(working_node_rows[v].geometry, v, "working")
        targets = {u: working_u, v: working_v}
        forward_cost = _distance(current_start, working_u) + _distance(current_end, working_v)
        reverse_cost = _distance(current_start, working_v) + _distance(current_end, working_u)
        orientation = _classify_orientation(edge_id, forward_cost, reverse_cost)
        start_role, end_role = (u, v) if orientation == "forward" else (v, u)

        required_start, required_end = targets[start_role], targets[end_role]
        edge_changes: list[EndpointChange] = []
        if current_start != required_start:
            edge_changes.append(EndpointChange(edge_id, "first", start_role, current_start, required_start, _distance(current_start, required_start)))
        if current_end != required_end:
            edge_changes.append(EndpointChange(edge_id, "last", end_role, current_end, required_end, _distance(current_end, required_end)))
        orientations.append(
            EdgeOrientation(
                edge_id=edge_id,
                orientation=orientation,
                classification=f"{orientation}/unambiguous",
                forward_assignment_cost=forward_cost,
                reverse_assignment_cost=reverse_cost,
                endpoint_synchronization_required=bool(edge_changes),
            )
        )
        if not edge_changes:
            matching_edges += 1
            continue
        replacement_coordinates = list(current_coordinates)
        replacement_coordinates[0] = required_start
        replacement_coordinates[-1] = required_end
        replacement = LineString(replacement_coordinates)
        if tuple(tuple(value for value in coordinate) for coordinate in replacement.coords[1:-1]) != current_coordinates[1:-1]:
            raise AssertionError(f"Internal coordinates changed while planning {edge_id}.")
        replacements[edge_id] = replacement
        changes.extend(edge_changes)

    if validator.sha256_file(canonical_path) != canonical_hash:
        raise SynchronizationError("Frozen canonical Env39 changed during dry-run planning.")
    changes.sort(key=lambda item: (item.edge_id, item.endpoint))
    return SyncPlan(
        input_path=input_path,
        canonical_path=canonical_path,
        canonical_sha256=canonical_hash,
        edges_total=len(working_edges),
        edges_already_match=matching_edges,
        affected_edge_ids=sorted(replacements),
        affected_node_ids=sorted({item.node_id for item in changes}),
        changes=changes,
        orientations=orientations,
        replacement_geometries=replacements,
        topology_signature_sha256=preflight["topology_control"]["topology_signature_sha256"],
    )


def _layer_names(path: Path) -> list[str]:
    return gpd.list_layers(path)["name"].astype(str).tolist()


def _fid_map(path: Path, layer: str, id_field: str) -> dict[str, int]:
    if not layer.replace("_", "").isalnum() or not id_field.replace("_", "").isalnum():
        raise SynchronizationError("Unsafe GeoPackage table or field name.")
    with closing(sqlite3.connect(path)) as connection:
        table_info = connection.execute(f'PRAGMA table_info("{layer}")').fetchall()
        primary_keys = [row[1] for row in table_info if row[5] == 1]
        if len(primary_keys) != 1:
            return {}
        fid = primary_keys[0]
        return {str(identifier): int(value) for value, identifier in connection.execute(f'SELECT "{fid}", "{id_field}" FROM "{layer}"')}


def _assert_frames_unchanged(before: gpd.GeoDataFrame, after: gpd.GeoDataFrame, layer: str) -> None:
    if list(before.columns) != list(after.columns) or before.crs != after.crs:
        raise SynchronizationError(f"Candidate changed {layer} fields, order, or CRS.")
    before_values = before.drop(columns=before.geometry.name).reset_index(drop=True)
    after_values = after.drop(columns=after.geometry.name).reset_index(drop=True)
    try:
        assert_frame_equal(before_values, after_values, check_dtype=True, check_exact=True)
    except AssertionError as exc:
        raise SynchronizationError(f"Candidate changed {layer} attributes: {exc}") from exc


def _verify_candidate(
    source_path: Path,
    candidate_path: Path,
    canonical_path: Path,
    plan: SyncPlan,
    original_nodes: gpd.GeoDataFrame,
    original_edges: gpd.GeoDataFrame,
) -> None:
    if sorted(_layer_names(candidate_path)) != sorted(_layer_names(source_path)):
        raise SynchronizationError("Candidate did not preserve the complete GeoPackage layer set.")
    candidate_nodes, candidate_edges = validator._read_layers(candidate_path)
    _assert_frames_unchanged(original_nodes, candidate_nodes, "node")
    _assert_frames_unchanged(original_edges, candidate_edges, "edge")
    if [geometry.wkb for geometry in original_nodes.geometry] != [geometry.wkb for geometry in candidate_nodes.geometry]:
        raise SynchronizationError("Candidate changed one or more node geometries.")
    original_by_id = {str(row.edge_id): row.geometry for row in original_edges.itertuples()}
    candidate_by_id = {str(row.edge_id): row.geometry for row in candidate_edges.itertuples()}
    for edge_id in sorted(original_by_id):
        old_coordinates = tuple(original_by_id[edge_id].coords)
        new_coordinates = tuple(candidate_by_id[edge_id].coords)
        if old_coordinates[1:-1] != new_coordinates[1:-1]:
            raise SynchronizationError(f"Candidate changed internal coordinates for {edge_id}.")
        if edge_id not in plan.affected_edge_ids and old_coordinates != new_coordinates:
            raise SynchronizationError(f"Candidate changed unrelated geometry for {edge_id}.")
    for field in SCIENTIFIC_NODE_FIELDS:
        if original_nodes[field].tolist() != candidate_nodes[field].tolist():
            raise SynchronizationError(f"Candidate changed node field {field}.")
    for field in SCIENTIFIC_EDGE_FIELDS:
        if original_edges[field].tolist() != candidate_edges[field].tolist():
            raise SynchronizationError(f"Candidate changed edge field {field}.")
    for layer, field in (("nodes", "node_id"), ("edges", "edge_id")):
        before_fids, after_fids = _fid_map(source_path, layer, field), _fid_map(candidate_path, layer, field)
        if before_fids and before_fids != after_fids:
            raise SynchronizationError(f"Candidate did not preserve {layer} GeoPackage FIDs.")
    result = validator.validate_candidate(candidate_path, canonical_path)
    if result.report["topology_control_status"] != "PASS" or result.report["geometric_realization_qa_status"] != "PASS":
        raise SynchronizationError(
            "Candidate failed Phase 6A validation: "
            f"topology={result.report['topology_control_status']}, geometry={result.report['geometric_realization_qa_status']}."
        )


def apply_sync_plan(plan: SyncPlan) -> dict[str, Any]:
    input_path, canonical_path = plan.input_path, plan.canonical_path
    input_hash_before = validator.sha256_file(input_path)
    canonical_hash_before = validator.sha256_file(canonical_path)
    if canonical_hash_before != plan.canonical_sha256:
        raise SynchronizationError("Canonical Env39 changed after dry-run planning.")
    fresh_plan = build_sync_plan(input_path, canonical_path)
    if [asdict(item) for item in fresh_plan.changes] != [asdict(item) for item in plan.changes]:
        raise SynchronizationError("Working Env38 changed after dry-run planning; refusing stale apply.")
    if [asdict(item) for item in fresh_plan.orientations] != [asdict(item) for item in plan.orientations]:
        raise SynchronizationError("Working Env38 edge orientations changed after dry-run planning; refusing stale apply.")
    if not plan.changes:
        final = validator.validate_candidate(input_path, canonical_path)
        return {
            "applied": False,
            "reason": "all endpoints already exact",
            "input_sha256_before": input_hash_before,
            "input_sha256_after": input_hash_before,
            "canonical_sha256_before": canonical_hash_before,
            "canonical_sha256_after": canonical_hash_before,
            "topology_control_status": final.report["topology_control_status"],
            "geometric_realization_qa_status": final.report["geometric_realization_qa_status"],
        }

    original_nodes, original_edges = validator._read_layers(input_path)
    candidate_path = input_path.with_name(f".{input_path.stem}.endpoint-sync-{uuid.uuid4().hex}.gpkg")
    backup_path = input_path.with_name(f".{input_path.stem}.endpoint-sync-backup-{uuid.uuid4().hex}.gpkg")
    replaced = False
    try:
        shutil.copy2(input_path, candidate_path)
        candidate_edges = original_edges.copy()
        index_by_id = {str(value): index for index, value in candidate_edges["edge_id"].items()}
        for edge_id, geometry in plan.replacement_geometries.items():
            candidate_edges.at[index_by_id[edge_id], candidate_edges.geometry.name] = geometry
        candidate_edges.to_file(candidate_path, layer="edges", driver="GPKG", mode="w", index=False)
        _verify_candidate(input_path, candidate_path, canonical_path, plan, original_nodes, original_edges)
        if validator.sha256_file(input_path) != input_hash_before:
            raise SynchronizationError("Working Env38 changed while its candidate was being validated.")
        if validator.sha256_file(canonical_path) != canonical_hash_before:
            raise SynchronizationError("Canonical Env39 changed while candidate was being validated.")
        shutil.copy2(input_path, backup_path)
        os.replace(candidate_path, input_path)
        replaced = True
        final = validator.validate_candidate(input_path, canonical_path)
        if final.report["topology_control_status"] != "PASS" or final.report["geometric_realization_qa_status"] != "PASS":
            raise SynchronizationError("Atomic result unexpectedly failed final Phase 6A validation.")
        canonical_hash_after = validator.sha256_file(canonical_path)
        if canonical_hash_after != canonical_hash_before:
            raise SynchronizationError("Canonical Env39 changed during synchronization.")
    except Exception:
        if replaced and backup_path.exists():
            os.replace(backup_path, input_path)
        raise
    finally:
        if candidate_path.exists():
            candidate_path.unlink()
        if backup_path.exists():
            backup_path.unlink()
    return {
        "applied": True,
        "input_sha256_before": input_hash_before,
        "input_sha256_after": validator.sha256_file(input_path),
        "canonical_sha256_before": canonical_hash_before,
        "canonical_sha256_after": canonical_hash_after,
        "edges_changed": len(plan.affected_edge_ids),
        "endpoints_changed": plan.endpoint_count,
        "topology_control_status": final.report["topology_control_status"],
        "geometric_realization_qa_status": final.report["geometric_realization_qa_status"],
        "topology_signature_sha256": final.report["topology_control"]["topology_signature_sha256"],
    }


def plan_report(plan: SyncPlan) -> dict[str, Any]:
    return {
        "mode": "dry-run",
        "input": str(plan.input_path),
        "canonical": str(plan.canonical_path),
        "canonical_sha256": plan.canonical_sha256,
        "topology_signature_sha256": plan.topology_signature_sha256,
        "edges_total": plan.edges_total,
        "edges_already_match": plan.edges_already_match,
        "edges_requiring_synchronization": len(plan.affected_edge_ids),
        "endpoints_requiring_synchronization": plan.endpoint_count,
        "affected_edge_ids": plan.affected_edge_ids,
        "affected_node_ids": plan.affected_node_ids,
        "changes": [asdict(item) for item in plan.changes],
        "edge_orientations": [asdict(item) for item in plan.orientations],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--canonical", type=Path, default=DEFAULT_CANONICAL)
    parser.add_argument("--apply", action="store_true", help="Atomically replace the input only after candidate validation passes.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        plan = build_sync_plan(args.input, args.canonical)
        print(json.dumps(plan_report(plan), indent=2, ensure_ascii=False, allow_nan=False))
        if args.apply:
            print(json.dumps({"apply_result": apply_sync_plan(plan)}, indent=2, ensure_ascii=False, allow_nan=False))
        else:
            print("DRY-RUN ONLY: no file was modified. Use --apply to synchronize listed endpoints.")
        return 0
    except (SynchronizationError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
