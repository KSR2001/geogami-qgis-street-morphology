"""Canonical GeoPackage to NetworkX and OSMnx analysis-graph adapter.

The canonical graph is an undirected physical-street ``MultiGraph``. The OSMnx
graph is a reciprocal ``MultiDiGraph`` with two directed arcs per physical
street. All lengths come from LineString geometry in GeoGami local units: this
module deliberately does not call geographic bearing or great-circle helpers.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
from typing import Any, Iterable
import uuid

import geopandas as gpd
import networkx as nx
import osmnx as ox
from pyproj import CRS
from shapely.geometry import LineString, Point

from .identity import network_identity, scientific_content_signature
from .io import read_network, sha256_file, write_json
from .validation import normalized_tuple, topology_signature


ANALYSIS_MANIFEST_SCHEMA_VERSION = "1.0.0"
DEFAULT_DERIVED_TOLERANCE = 1e-9
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class AnalysisGraphError(RuntimeError):
    """Raised when canonical selection, graph construction, or publication fails."""


@dataclass(frozen=True)
class CanonicalSelection:
    environment: str
    run_id: str
    canonical_path: Path
    manifest_path: Path | None
    file_sha256: str
    scientific_content_signature: str
    topology_signature: str
    manifest: dict[str, Any] | None


@dataclass(frozen=True)
class CanonicalData:
    nodes: gpd.GeoDataFrame
    edges: gpd.GeoDataFrame
    identity: dict[str, Any]
    derived_disagreements: tuple[str, ...]


@dataclass(frozen=True)
class AnalysisBuildResult:
    selection: CanonicalSelection
    output_dir: Path
    graphml_path: Path
    manifest_path: Path
    graphml_sha256: str
    manifest: dict[str, Any]
    networkx_graph: nx.MultiGraph
    osmnx_graph: nx.MultiDiGraph
    roundtrip_graph: nx.MultiDiGraph


def _utc_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisGraphError(f"Cannot read {label} JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AnalysisGraphError(f"{label} must contain a JSON object: {path}")
    return value


def _resolve_path(value: str | Path, project_root: Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def _display_path(path: Path, project_root: Path) -> str:
    resolved, root = path.resolve(), project_root.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved.as_posix()


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError:
        return False
    return True


def _require_equal(label: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        raise AnalysisGraphError(f"{label} mismatch: calculated {actual!r}, recorded {expected!r}.")


def _actual_identities(canonical_path: Path) -> tuple[str, str, str]:
    nodes, edges = read_network(canonical_path)
    identity = network_identity(nodes, edges)
    return (
        sha256_file(canonical_path),
        identity["scientific_content_signature"],
        identity["topology_signature"],
    )


def _selection_from_manifest(
    manifest_path: Path,
    environment: str,
    project_root: Path,
    *,
    latest: dict[str, Any] | None = None,
    latest_path: Path | None = None,
) -> CanonicalSelection:
    if not manifest_path.is_file():
        raise AnalysisGraphError(f"Canonical manifest does not exist: {manifest_path}")
    manifest = _read_json(manifest_path, "canonical manifest")
    try:
        identity = manifest["identity"]
        published = manifest["published_canonical"]
        validation = manifest["validation"]
        network_record = manifest["network_identity"]
        artifacts = manifest["artifacts"]
        run_id = str(identity["run_id"])
        manifest_environment = str(identity["environment"])
        canonical_path = _resolve_path(published["path"], project_root)
    except (KeyError, TypeError) as exc:
        raise AnalysisGraphError(f"Canonical manifest is missing required selection fields: {exc}") from exc
    _require_equal("Canonical manifest environment", manifest_environment, environment)
    _require_equal("Canonical manifest validation status", validation.get("final_status"), "PASS")
    if not canonical_path.is_file():
        raise AnalysisGraphError(f"Published canonical GeoPackage does not exist: {canonical_path}")

    try:
        validation_path = _resolve_path(artifacts["canonical_validation"], project_root)
    except (KeyError, TypeError) as exc:
        raise AnalysisGraphError(f"Canonical validation artifact is not declared: {exc}") from exc
    if not validation_path.is_file():
        raise AnalysisGraphError(f"Canonical validation artifact does not exist: {validation_path}")
    validation_document = _read_json(validation_path, "canonical validation")
    _require_equal("Canonical validation final result", validation_document.get("final_result"), "PASS")

    file_digest, scientific_digest, topology_digest = _actual_identities(canonical_path)
    _require_equal("Canonical file SHA-256", file_digest, published.get("file_sha256"))
    _require_equal(
        "Canonical scientific-content signature",
        scientific_digest,
        published.get("scientific_content_signature"),
    )
    _require_equal("Canonical topology signature", topology_digest, published.get("topology_signature"))
    _require_equal(
        "Manifest network scientific-content signature",
        network_record.get("scientific_content_signature"),
        scientific_digest,
    )
    _require_equal(
        "Manifest network topology signature",
        network_record.get("topology_signature"),
        topology_digest,
    )

    required_artifacts = {
        "canonical_geopackage": canonical_path,
        "canonical_manifest": manifest_path.resolve(),
        "canonical_validation": validation_path,
        "canonical_nodes_csv": None,
        "canonical_edges_csv": None,
        "topology_signature": None,
        "scientific_content_signature": None,
        "endpoint_adjustments_csv": None,
        "diagnostics_csv": None,
    }
    resolved_artifacts: dict[str, Path] = {}
    for name, expected_path in required_artifacts.items():
        try:
            declaration = artifacts[name]
            declared_path = declaration["path"] if isinstance(declaration, dict) else declaration
            resolved = _resolve_path(declared_path, project_root)
        except (KeyError, TypeError) as exc:
            raise AnalysisGraphError(f"Canonical manifest artifact {name!r} is invalid: {exc}") from exc
        if not resolved.is_file():
            raise AnalysisGraphError(f"Canonical manifest artifact {name!r} does not exist: {resolved}")
        if expected_path is not None:
            _require_equal(f"Canonical artifact path {name}", resolved, expected_path)
        resolved_artifacts[name] = resolved
    _require_equal(
        "Standalone scientific-content signature",
        resolved_artifacts["scientific_content_signature"].read_text(encoding="utf-8").strip(),
        scientific_digest,
    )
    _require_equal(
        "Standalone topology signature",
        resolved_artifacts["topology_signature"].read_text(encoding="utf-8").strip(),
        topology_digest,
    )

    if latest is not None:
        if latest_path is None:  # pragma: no cover - internal contract
            raise AssertionError("latest_path is required with latest metadata")
        required_latest = {
            "environment",
            "run_id",
            "canonical_path",
            "manifest_path",
            "canonical_file_sha256",
            "scientific_content_signature",
            "topology_signature",
            "created_at_utc",
        }
        missing = required_latest - set(latest)
        if missing:
            raise AnalysisGraphError(f"Latest pointer is missing fields: {', '.join(sorted(missing))}")
        _require_equal("Latest environment", latest["environment"], environment)
        _require_equal("Latest run ID", latest["run_id"], run_id)
        _require_equal(
            "Latest manifest path",
            _resolve_path(latest["manifest_path"], project_root),
            manifest_path.resolve(),
        )
        _require_equal(
            "Latest canonical path",
            _resolve_path(latest["canonical_path"], project_root),
            canonical_path,
        )
        _require_equal("Latest canonical file SHA-256", latest["canonical_file_sha256"], file_digest)
        _require_equal(
            "Latest scientific-content signature",
            latest["scientific_content_signature"],
            scientific_digest,
        )
        _require_equal("Latest topology signature", latest["topology_signature"], topology_digest)

    return CanonicalSelection(
        environment=environment,
        run_id=run_id,
        canonical_path=canonical_path,
        manifest_path=manifest_path.resolve(),
        file_sha256=file_digest,
        scientific_content_signature=scientific_digest,
        topology_signature=topology_digest,
        manifest=manifest,
    )


def resolve_canonical_run(
    environment: str,
    *,
    canonical_run: str = "latest",
    canonical_path: Path | None = None,
    latest_path: Path | None = None,
    project_root: Path = PROJECT_ROOT,
) -> CanonicalSelection:
    """Resolve and independently verify a Phase 7C run or explicit canonical file."""
    project_root = Path(project_root).resolve()
    if canonical_path is not None:
        if canonical_run != "latest":
            raise AnalysisGraphError("Specify canonical_path or canonical_run, not both.")
        explicit = _resolve_path(canonical_path, project_root)
        if not explicit.is_file():
            raise AnalysisGraphError(f"Explicit canonical GeoPackage does not exist: {explicit}")
        prohibited_roots = (
            project_root / "data" / "editable",
            project_root / "data" / "baselines",
        )
        if any(_is_within(explicit, root) for root in prohibited_roots):
            raise AnalysisGraphError(
                "Analysis input must be an accepted canonical GeoPackage, not editable or baseline source data."
            )
        sibling_manifest = explicit.parent / "canonical_manifest.json"
        if sibling_manifest.is_file():
            selection = _selection_from_manifest(sibling_manifest, environment, project_root)
            _require_equal("Explicit canonical path", selection.canonical_path, explicit)
            return selection
        file_digest, scientific_digest, topology_digest = _actual_identities(explicit)
        safe_environment = re.sub(r"[^a-zA-Z0-9_-]+", "-", environment).strip("-") or "environment"
        return CanonicalSelection(
            environment=environment,
            run_id=f"explicit_{safe_environment}_{scientific_digest[:12].lower()}",
            canonical_path=explicit,
            manifest_path=None,
            file_sha256=file_digest,
            scientific_content_signature=scientific_digest,
            topology_signature=topology_digest,
            manifest=None,
        )

    if canonical_run == "latest":
        selected_latest = (
            Path(latest_path).resolve()
            if latest_path is not None
            else project_root / "data" / "canonical" / "grid" / "latest.json"
        )
        if not selected_latest.is_file():
            raise AnalysisGraphError(f"Latest canonical pointer does not exist: {selected_latest}")
        latest = _read_json(selected_latest, "latest canonical pointer")
        try:
            manifest_path = _resolve_path(latest["manifest_path"], project_root)
        except KeyError as exc:
            raise AnalysisGraphError("Latest canonical pointer has no manifest_path.") from exc
        return _selection_from_manifest(
            manifest_path,
            environment,
            project_root,
            latest=latest,
            latest_path=selected_latest,
        )

    run_manifest = (
        project_root
        / "data"
        / "canonical"
        / "grid"
        / "runs"
        / canonical_run
        / "canonical_manifest.json"
    )
    return _selection_from_manifest(run_manifest, environment, project_root)


def load_canonical_geopackage(path: Path) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Load canonical node and edge layers without altering their source file."""
    try:
        return read_network(Path(path))
    except Exception as exc:
        raise AnalysisGraphError(f"Cannot load canonical GeoPackage {path}: {exc}") from exc


def _is_close(first: float, second: float, tolerance: float) -> bool:
    return math.isclose(float(first), float(second), rel_tol=1e-12, abs_tol=tolerance)


def _missing_values(frame: gpd.GeoDataFrame, fields: Iterable[str]) -> list[str]:
    return [field for field in fields if field in frame and bool(frame[field].isna().any())]


def validate_canonical_for_analysis(
    nodes: gpd.GeoDataFrame,
    edges: gpd.GeoDataFrame,
    *,
    derived_tolerance: float = DEFAULT_DERIVED_TOLERANCE,
) -> CanonicalData:
    """Validate canonical schema/geometry and recompute all derived graph values."""
    nodes, edges = nodes.copy(), edges.copy()
    missing_node_fields = {"node_id", "geometry"} - set(nodes.columns)
    missing_edge_fields = {"edge_id", "u", "v", "key", "geometry"} - set(edges.columns)
    if missing_node_fields or missing_edge_fields:
        raise AnalysisGraphError(
            f"Canonical fields missing: nodes={sorted(missing_node_fields)}, "
            f"edges={sorted(missing_edge_fields)}"
        )
    null_fields = _missing_values(nodes, ["node_id", "geometry"]) + _missing_values(
        edges, ["edge_id", "u", "v", "key", "geometry"]
    )
    if null_fields:
        raise AnalysisGraphError(f"Canonical required fields contain nulls: {sorted(set(null_fields))}")
    if nodes.crs is None or edges.crs is None or nodes.crs != edges.crs:
        raise AnalysisGraphError("Canonical node and edge layers require one identical declared CRS.")
    crs = CRS.from_user_input(nodes.crs)
    if crs.is_geographic or crs.to_epsg() == 4326:
        raise AnalysisGraphError("GeoGami canonical analysis requires its local Cartesian CRS, not EPSG:4326.")

    nodes["node_id"] = nodes["node_id"].astype(str)
    edges["edge_id"] = edges["edge_id"].astype(str)
    edges["u"] = edges["u"].astype(str)
    edges["v"] = edges["v"].astype(str)
    try:
        edges["key"] = edges["key"].map(int)
    except (TypeError, ValueError) as exc:
        raise AnalysisGraphError(f"Canonical edge keys must be integers: {exc}") from exc
    if nodes["node_id"].duplicated().any():
        raise AnalysisGraphError("Canonical node_id values must be unique.")
    if edges["edge_id"].duplicated().any():
        raise AnalysisGraphError("Canonical edge_id values must be unique.")

    errors: list[str] = []
    disagreements: list[str] = []
    node_points: dict[str, Point] = {}
    for row in nodes.itertuples():
        geometry = row.geometry
        if not isinstance(geometry, Point) or geometry.is_empty or not geometry.is_valid:
            errors.append(f"node {row.node_id} geometry is not a valid non-empty Point")
            continue
        node_points[row.node_id] = geometry
        if "x" in nodes and not _is_close(getattr(row, "x"), geometry.x, derived_tolerance):
            disagreements.append(f"node {row.node_id} stored x differs from Point.x")
        if "y" in nodes and not _is_close(getattr(row, "y"), geometry.y, derived_tolerance):
            disagreements.append(f"node {row.node_id} stored y differs from Point.y")

    topology_tuples: list[tuple[str, str, int]] = []
    seen_topology: set[tuple[str, str, int]] = set()
    for row in edges.itertuples():
        geometry = row.geometry
        if not isinstance(geometry, LineString) or geometry.is_empty or not geometry.is_valid:
            errors.append(f"edge {row.edge_id} geometry is not a valid non-empty LineString")
            continue
        if row.u not in node_points or row.v not in node_points:
            errors.append(f"edge {row.edge_id} references a missing node")
            continue
        if row.u == row.v:
            errors.append(f"edge {row.edge_id} is a self-loop")
        normalized = normalized_tuple(row.u, row.v, row.key)
        if normalized in seen_topology:
            errors.append(f"edge {row.edge_id} duplicates canonical topology tuple {normalized}")
        seen_topology.add(normalized)
        topology_tuples.append(normalized)
        start, end = tuple(geometry.coords[0]), tuple(geometry.coords[-1])
        u_coordinate, v_coordinate = tuple(node_points[row.u].coords[0]), tuple(node_points[row.v].coords[0])
        if not (
            (start == u_coordinate and end == v_coordinate)
            or (start == v_coordinate and end == u_coordinate)
        ):
            errors.append(
                f"edge {row.edge_id} geometry endpoints do not exactly match canonical nodes "
                f"u={row.u} and v={row.v} in either LineString direction"
            )
        actual_length = float(geometry.length)
        if "length_local" in edges and not _is_close(
            getattr(row, "length_local"), actual_length, derived_tolerance
        ):
            disagreements.append(f"edge {row.edge_id} stored length_local differs from geometry length")
        if "vertex_count" in edges and int(getattr(row, "vertex_count")) != len(geometry.coords):
            disagreements.append(f"edge {row.edge_id} stored vertex_count differs from geometry")

    graph = nx.MultiGraph()
    graph.add_nodes_from(nodes["node_id"])
    for row in edges.itertuples():
        graph.add_edge(row.u, row.v, key=row.key)
    if "degree" in nodes:
        degrees = dict(graph.degree())
        for row in nodes.itertuples():
            if int(row.degree) != degrees[row.node_id]:
                disagreements.append(f"node {row.node_id} stored degree differs from canonical topology")

    if errors or disagreements:
        details = errors + disagreements
        raise AnalysisGraphError("Canonical analysis validation failed:\n- " + "\n- ".join(details))

    nodes["x"] = nodes.geometry.x.astype(float)
    nodes["y"] = nodes.geometry.y.astype(float)
    nodes["degree"] = nodes["node_id"].map(dict(graph.degree())).astype(int)
    edges["length_local"] = edges.geometry.length.astype(float)
    edges["vertex_count"] = edges.geometry.map(lambda geometry: len(geometry.coords)).astype(int)
    identity = network_identity(nodes, edges)
    calculated_topology, _ = topology_signature(topology_tuples)
    _require_equal("Validated topology signature", identity["topology_signature"], calculated_topology)
    return CanonicalData(nodes, edges, identity, tuple(disagreements))


def _attribute_value(value: Any) -> Any:
    return value.item() if hasattr(value, "item") else value


def _graph_metadata(selection: CanonicalSelection, semantics: str, crs: Any) -> dict[str, Any]:
    return {
        "crs": crs,
        "environment": selection.environment,
        "canonical_run_id": selection.run_id,
        "canonical_file_sha256": selection.file_sha256,
        "scientific_content_signature": selection.scientific_content_signature,
        "topology_signature": selection.topology_signature,
        "graph_semantics": semantics,
        "coordinate_system": "GeoGami Local Cartesian",
        "coordinate_units": "local_units",
        "length_units": "local_units",
    }


def build_networkx_multigraph(
    canonical: CanonicalData,
    selection: CanonicalSelection,
) -> nx.MultiGraph:
    """Build one undirected graph edge per canonical physical street."""
    graph = nx.MultiGraph(
        **_graph_metadata(selection, "undirected physical street MultiGraph", canonical.nodes.crs)
    )
    geometry_name = canonical.nodes.geometry.name
    for _, row in canonical.nodes.sort_values("node_id", kind="stable").iterrows():
        attributes = {
            column: _attribute_value(row[column])
            for column in canonical.nodes.columns
            if column != geometry_name
        }
        attributes.update(
            node_id=str(row.node_id),
            x=float(row.geometry.x),
            y=float(row.geometry.y),
            geometry=row.geometry,
        )
        graph.add_node(str(row.node_id), **attributes)
    edge_geometry_name = canonical.edges.geometry.name
    for _, row in canonical.edges.sort_values("edge_id", kind="stable").iterrows():
        attributes = {
            column: _attribute_value(row[column])
            for column in canonical.edges.columns
            if column not in {edge_geometry_name, "u", "v", "key"}
        }
        attributes.update(
            edge_id=str(row.edge_id),
            canonical_u=str(row.u),
            canonical_v=str(row.v),
            canonical_key=int(row.key),
            u=str(row.u),
            v=str(row.v),
            geometry=row.geometry,
            length=float(row.geometry.length),
            length_local=float(row.geometry.length),
            vertex_count=len(row.geometry.coords),
            length_units="local_units",
        )
        graph.add_edge(str(row.u), str(row.v), key=int(row.key), **attributes)
        graph[str(row.u)][str(row.v)][int(row.key)]["key"] = int(row.key)
    return graph


def prepare_osmnx_node_gdf(canonical: CanonicalData) -> gpd.GeoDataFrame:
    """Prepare OSMnx 2.1.1 nodes indexed by canonical string ID as ``osmid``."""
    nodes = canonical.nodes.copy()
    nodes["node_id"] = nodes["node_id"].astype(str)
    nodes["x"] = nodes.geometry.x.astype(float)
    nodes["y"] = nodes.geometry.y.astype(float)
    nodes = nodes.set_index("node_id", drop=False)
    nodes.index.name = "osmid"
    if not nodes.index.is_unique:
        raise AnalysisGraphError("OSMnx node GeoDataFrame index is not unique.")
    return nodes


def prepare_osmnx_edge_gdf(canonical: CanonicalData) -> gpd.GeoDataFrame:
    """Create exactly two geometry-oriented directed records per physical edge."""
    records: list[dict[str, Any]] = []
    node_points = canonical.nodes.set_index("node_id").geometry
    for row in canonical.edges.sort_values("edge_id", kind="stable").itertuples():
        stored_coordinates = tuple(row.geometry.coords)
        u_coordinate = tuple(node_points[str(row.u)].coords[0])
        forward_coordinates = (
            stored_coordinates
            if tuple(stored_coordinates[0]) == u_coordinate
            else tuple(reversed(stored_coordinates))
        )
        forward_geometry = LineString(forward_coordinates)
        reverse_geometry = LineString(tuple(reversed(forward_coordinates)))
        common = {
            "canonical_edge_id": str(row.edge_id),
            "edge_id": str(row.edge_id),
            "canonical_u": str(row.u),
            "canonical_v": str(row.v),
            "canonical_key": int(row.key),
            "length": float(row.geometry.length),
            "length_local": float(row.geometry.length),
            "vertex_count": len(row.geometry.coords),
            "length_units": "local_units",
        }
        records.append(
            {
                "u": str(row.u),
                "v": str(row.v),
                "key": int(row.key),
                **common,
                "arc_direction": "forward",
                "geometry": forward_geometry,
            }
        )
        records.append(
            {
                "u": str(row.v),
                "v": str(row.u),
                "key": int(row.key),
                **common,
                "arc_direction": "reverse",
                "geometry": reverse_geometry,
            }
        )
    edges = gpd.GeoDataFrame(records, geometry="geometry", crs=canonical.edges.crs)
    edges = edges.set_index(["u", "v", "key"])
    if not edges.index.is_unique:
        raise AnalysisGraphError("OSMnx directed (u,v,key) edge index is not unique.")
    return edges


def build_osmnx_multidigraph(
    canonical: CanonicalData,
    selection: CanonicalSelection,
) -> nx.MultiDiGraph:
    """Build the reciprocal graph through OSMnx's public GeoDataFrame API."""
    graph = ox.convert.graph_from_gdfs(
        prepare_osmnx_node_gdf(canonical),
        prepare_osmnx_edge_gdf(canonical),
        graph_attrs=_graph_metadata(
            selection,
            "reciprocal directed representation of canonical physical streets",
            canonical.nodes.crs,
        ),
    )
    if not isinstance(graph, nx.MultiDiGraph):  # pragma: no cover - public API contract
        raise AnalysisGraphError(f"OSMnx returned unexpected graph type: {type(graph).__name__}")
    return graph


def physical_street_count(graph: nx.Graph) -> int:
    """Count unique canonical physical IDs without conflating them with arcs."""
    attribute = "canonical_edge_id" if graph.is_directed() else "edge_id"
    return len({str(data[attribute]) for *_, data in graph.edges(data=True) if attribute in data})


def directed_arc_count(graph: nx.Graph) -> int:
    """Return directed representation size and reject undirected misuse."""
    if not graph.is_directed():
        raise AnalysisGraphError("Directed arc count is undefined for an undirected physical graph.")
    return graph.number_of_edges()


def _coordinates(geometry: LineString) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(float(component) for component in coordinate) for coordinate in geometry.coords)


def validate_graph_correspondence(
    canonical: CanonicalData,
    scientific_graph: nx.MultiGraph,
    osmnx_graph: nx.MultiDiGraph,
    selection: CanonicalSelection,
    *,
    length_tolerance: float = DEFAULT_DERIVED_TOLERANCE,
) -> dict[str, Any]:
    """Strictly prove canonical, physical-graph, and reciprocal-graph equivalence."""
    canonical_nodes = set(canonical.nodes["node_id"].astype(str))
    canonical_edges = {str(row.edge_id): row for row in canonical.edges.itertuples()}
    if set(scientific_graph.nodes) != canonical_nodes or set(osmnx_graph.nodes) != canonical_nodes:
        raise AnalysisGraphError("Graph node ID sets do not exactly match the canonical node IDs.")
    if not isinstance(scientific_graph, nx.MultiGraph) or scientific_graph.is_directed():
        raise AnalysisGraphError("Scientific graph must be an undirected NetworkX MultiGraph.")
    if not isinstance(osmnx_graph, nx.MultiDiGraph):
        raise AnalysisGraphError("OSMnx graph must be a NetworkX MultiDiGraph.")

    physical_records: dict[str, tuple[str, str, int, dict[str, Any]]] = {}
    for u, v, key, data in scientific_graph.edges(keys=True, data=True):
        edge_id = str(data.get("edge_id"))
        if edge_id in physical_records:
            raise AnalysisGraphError(f"Scientific graph duplicates physical edge ID {edge_id}.")
        physical_records[edge_id] = (str(u), str(v), int(key), data)
    if set(physical_records) != set(canonical_edges):
        raise AnalysisGraphError("Scientific graph canonical edge ID set does not match the GeoPackage.")
    if scientific_graph.number_of_edges() != len(canonical_edges):
        raise AnalysisGraphError("Scientific graph does not contain exactly one edge per physical street.")

    arcs: dict[str, list[tuple[str, str, int, dict[str, Any]]]] = defaultdict(list)
    for u, v, key, data in osmnx_graph.edges(keys=True, data=True):
        edge_id = str(data.get("canonical_edge_id"))
        arcs[edge_id].append((str(u), str(v), int(key), data))
    if set(arcs) != set(canonical_edges):
        raise AnalysisGraphError("OSMnx graph physical canonical edge ID set does not match the GeoPackage.")

    for edge_id, canonical_edge in canonical_edges.items():
        edge_arcs = arcs[edge_id]
        if len(edge_arcs) != 2:
            raise AnalysisGraphError(f"Physical edge {edge_id} has {len(edge_arcs)} arcs instead of two.")
        by_direction = {str(data.get("arc_direction")): (u, v, key, data) for u, v, key, data in edge_arcs}
        if set(by_direction) != {"forward", "reverse"}:
            raise AnalysisGraphError(f"Physical edge {edge_id} lacks one explicit forward/reverse arc.")
        forward = by_direction["forward"]
        reverse = by_direction["reverse"]
        expected_forward = (str(canonical_edge.u), str(canonical_edge.v))
        if forward[:2] != expected_forward or reverse[:2] != tuple(reversed(expected_forward)):
            raise AnalysisGraphError(f"Physical edge {edge_id} reciprocal arc endpoints are incorrect.")
        if int(forward[2]) != int(canonical_edge.key) or int(reverse[2]) != int(canonical_edge.key):
            raise AnalysisGraphError(f"Physical edge {edge_id} arc keys do not preserve canonical key.")
        forward_geometry, reverse_geometry = forward[3].get("geometry"), reverse[3].get("geometry")
        if not isinstance(forward_geometry, LineString) or not isinstance(reverse_geometry, LineString):
            raise AnalysisGraphError(f"Physical edge {edge_id} arc geometry is not LineString.")
        canonical_coordinates = _coordinates(canonical_edge.geometry)
        node_lookup = canonical.nodes.set_index("node_id").geometry
        canonical_u = tuple(node_lookup[str(canonical_edge.u)].coords[0])
        expected_forward_geometry = (
            canonical_coordinates
            if canonical_coordinates[0] == canonical_u
            else tuple(reversed(canonical_coordinates))
        )
        if _coordinates(forward_geometry) != expected_forward_geometry:
            raise AnalysisGraphError(f"Physical edge {edge_id} forward geometry does not run from u to v.")
        if _coordinates(reverse_geometry) != tuple(reversed(expected_forward_geometry)):
            raise AnalysisGraphError(f"Physical edge {edge_id} reverse geometry is not exact coordinate reversal.")
        canonical_length = float(canonical_edge.geometry.length)
        for _, _, _, data in (forward, reverse):
            if not _is_close(float(data["length"]), canonical_length, length_tolerance):
                raise AnalysisGraphError(f"Physical edge {edge_id} arc length differs from geometry length.")
        if not _is_close(float(forward[3]["length"]), float(reverse[3]["length"]), length_tolerance):
            raise AnalysisGraphError(f"Physical edge {edge_id} reciprocal lengths disagree.")

    reconstructed, _ = topology_signature(
        normalized_tuple(data["u"], data["v"], data["key"])
        for _, _, _, data in physical_records.values()
    )
    _require_equal("Graph topology signature", reconstructed, selection.topology_signature)
    degree_distribution = dict(sorted(Counter(dict(scientific_graph.degree()).values()).items()))
    degree_sum = sum(dict(scientific_graph.degree()).values())
    if degree_sum != 2 * scientific_graph.number_of_edges():
        raise AnalysisGraphError("Scientific graph violates the undirected handshake theorem.")
    return {
        "node_count": len(canonical_nodes),
        "physical_edge_count": len(canonical_edges),
        "directed_arc_count": osmnx_graph.number_of_edges(),
        "physical_canonical_edge_count": len(arcs),
        "component_count": nx.number_connected_components(scientific_graph),
        "degree_distribution": degree_distribution,
        "degree_sum": degree_sum,
        "twice_physical_edges": 2 * scientific_graph.number_of_edges(),
        "topology_signature": reconstructed,
    }


def load_analysis_graphml(path: Path) -> nx.MultiDiGraph:
    """Reload an adapter GraphML using OSMnx's public typed loader."""
    return ox.io.load_graphml(
        filepath=Path(path),
        node_dtypes={"osmid": str, "node_id": str, "x": float, "y": float, "degree": int},
        edge_dtypes={
            "canonical_key": int,
            "length": float,
            "length_local": float,
            "vertex_count": int,
        },
    )


def validate_graphml_roundtrip(
    canonical: CanonicalData,
    scientific_graph: nx.MultiGraph,
    graph: nx.MultiDiGraph,
    selection: CanonicalSelection,
) -> dict[str, Any]:
    """Validate typed GraphML reload including geometry, metadata, and local CRS."""
    correspondence = validate_graph_correspondence(canonical, scientific_graph, graph, selection)
    node_lookup = canonical.nodes.set_index("node_id")
    for node_id, data in graph.nodes(data=True):
        source = node_lookup.loc[str(node_id)]
        if not _is_close(data["x"], source.geometry.x, DEFAULT_DERIVED_TOLERANCE):
            raise AnalysisGraphError(f"GraphML node {node_id} x did not round-trip numerically.")
        if not _is_close(data["y"], source.geometry.y, DEFAULT_DERIVED_TOLERANCE):
            raise AnalysisGraphError(f"GraphML node {node_id} y did not round-trip numerically.")
    for field, expected in {
        "environment": selection.environment,
        "canonical_run_id": selection.run_id,
        "canonical_file_sha256": selection.file_sha256,
        "scientific_content_signature": selection.scientific_content_signature,
        "topology_signature": selection.topology_signature,
        "coordinate_system": "GeoGami Local Cartesian",
        "coordinate_units": "local_units",
        "length_units": "local_units",
    }.items():
        _require_equal(f"GraphML metadata {field}", graph.graph.get(field), expected)
    loaded_crs = CRS.from_user_input(graph.graph.get("crs"))
    if loaded_crs != CRS.from_user_input(canonical.nodes.crs):
        raise AnalysisGraphError("GraphML did not preserve the canonical local CRS.")
    if loaded_crs.is_geographic or loaded_crs.to_epsg() == 4326:
        raise AnalysisGraphError("GraphML incorrectly substituted a geographic CRS.")
    return correspondence


def _software_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {"Python": platform.python_version()}
    for label, distribution in {
        "NetworkX": "networkx",
        "OSMnx": "osmnx",
        "GeoPandas": "geopandas",
        "Shapely": "shapely",
        "pyproj": "pyproj",
    }.items():
        try:
            versions[label] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:  # pragma: no cover - required runtime deps
            versions[label] = None
    return versions


def save_analysis_graphs(
    canonical: CanonicalData,
    scientific_graph: nx.MultiGraph,
    osmnx_graph: nx.MultiDiGraph,
    selection: CanonicalSelection,
    *,
    output_root: Path,
    project_root: Path = PROJECT_ROOT,
) -> AnalysisBuildResult:
    """Save, reload, validate, and atomically publish portable GraphML artifacts."""
    project_root, output_root = Path(project_root).resolve(), Path(output_root).resolve()
    final_dir = output_root / selection.environment / selection.run_id
    if final_dir.exists():
        raise AnalysisGraphError(f"Analysis output already exists and will not be overwritten: {final_dir}")
    scratch_parent = output_root / ".scratch"
    scratch = scratch_parent / f"{selection.run_id}-{uuid.uuid4().hex}"
    graphs_dir = scratch / "graphs"
    graphs_dir.mkdir(parents=True, exist_ok=False)
    graphml_name = f"{selection.environment}_osmnx.graphml"
    scratch_graphml = graphs_dir / graphml_name
    final_graphml = final_dir / "graphs" / graphml_name
    final_manifest = final_dir / "analysis_graph_manifest.json"
    try:
        ox.io.save_graphml(osmnx_graph, filepath=scratch_graphml)
        if not scratch_graphml.is_file():  # pragma: no cover - public API contract
            raise AnalysisGraphError("OSMnx GraphML saver did not create the requested file.")
        graphml_digest = sha256_file(scratch_graphml)
        roundtrip = load_analysis_graphml(scratch_graphml)
        correspondence = validate_graphml_roundtrip(
            canonical, scientific_graph, roundtrip, selection
        )
        manifest = {
            "schema_version": ANALYSIS_MANIFEST_SCHEMA_VERSION,
            "created_at_utc": _utc_text(),
            "environment": selection.environment,
            "canonical_run_id": selection.run_id,
            "canonical": {
                "path": _display_path(selection.canonical_path, project_root),
                "file_sha256": selection.file_sha256,
                "scientific_content_signature": selection.scientific_content_signature,
                "topology_signature": selection.topology_signature,
            },
            "networkx_graph": {
                "graph_type": "MultiGraph",
                "node_count": scientific_graph.number_of_nodes(),
                "physical_edge_count": scientific_graph.number_of_edges(),
                "component_count": nx.number_connected_components(scientific_graph),
                "degree_distribution": {
                    str(key): value for key, value in correspondence["degree_distribution"].items()
                },
                "handshake_degree_sum": correspondence["degree_sum"],
            },
            "osmnx_graph": {
                "graph_type": "MultiDiGraph",
                "node_count": osmnx_graph.number_of_nodes(),
                "directed_arc_count": directed_arc_count(osmnx_graph),
                "physical_canonical_edge_count": physical_street_count(osmnx_graph),
            },
            "semantics": {
                "canonical_graph": "undirected physical street MultiGraph",
                "osmnx_graph": "reciprocal directed representation",
                "coordinate_system": "GeoGami Local Cartesian",
                "coordinate_units": "local_units",
                "length_units": "local_units",
                "authoritative_length": "LineString.length in canonical local Cartesian coordinates",
                "directed_arcs_are_physical_streets": False,
            },
            "software": _software_versions(),
            "artifacts": {
                "graphml_path": _display_path(final_graphml, project_root),
                "graphml_sha256": graphml_digest,
            },
            "roundtrip_validation": {
                "status": "PASS",
                "node_count": roundtrip.number_of_nodes(),
                "directed_arc_count": roundtrip.number_of_edges(),
                "physical_canonical_edge_count": physical_street_count(roundtrip),
                "geometry_orientation_verified": True,
                "numeric_attributes_verified": True,
                "local_crs_verified": True,
            },
        }
        write_json(scratch / "analysis_graph_manifest.json", manifest)
        if sha256_file(scratch_graphml) != graphml_digest:
            raise AnalysisGraphError("GraphML bytes changed after round-trip verification.")
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(scratch, final_dir)
        try:
            scratch_parent.rmdir()
        except OSError:
            pass
        return AnalysisBuildResult(
            selection=selection,
            output_dir=final_dir,
            graphml_path=final_graphml,
            manifest_path=final_manifest,
            graphml_sha256=graphml_digest,
            manifest=manifest,
            networkx_graph=scientific_graph,
            osmnx_graph=osmnx_graph,
            roundtrip_graph=roundtrip,
        )
    except Exception as exc:
        if scratch.exists():
            shutil.rmtree(scratch)
        try:
            scratch_parent.rmdir()
        except OSError:
            pass
        if isinstance(exc, AnalysisGraphError):
            raise
        raise AnalysisGraphError(f"Analysis graph publication failed: {exc}") from exc


def build_analysis_graphs(
    environment: str,
    *,
    canonical_run: str = "latest",
    canonical_path: Path | None = None,
    latest_path: Path | None = None,
    output_root: Path | None = None,
    project_root: Path = PROJECT_ROOT,
) -> AnalysisBuildResult:
    """Resolve, validate, construct, correspond, and publish both graph semantics."""
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
    _require_equal(
        "Loaded canonical scientific-content signature",
        scientific_content_signature(canonical.nodes, canonical.edges)[0],
        selection.scientific_content_signature,
    )
    _require_equal(
        "Loaded canonical topology signature",
        canonical.identity["topology_signature"],
        selection.topology_signature,
    )
    scientific_graph = build_networkx_multigraph(canonical, selection)
    osmnx_graph = build_osmnx_multidigraph(canonical, selection)
    validate_graph_correspondence(canonical, scientific_graph, osmnx_graph, selection)
    selected_output = output_root or project_root / "results" / "analysis"
    return save_analysis_graphs(
        canonical,
        scientific_graph,
        osmnx_graph,
        selection,
        output_root=Path(selected_output),
        project_root=project_root,
    )
