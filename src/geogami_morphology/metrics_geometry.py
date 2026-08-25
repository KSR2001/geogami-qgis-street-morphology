"""Reproducible planar geometry, circuity, and orientation metrics."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import json
import math
from pathlib import Path
import statistics
from typing import Any, Iterable

import geopandas as gpd
from shapely.geometry import LineString, Point
import yaml

from .graph import (
    CanonicalData,
    CanonicalSelection,
    load_canonical_geopackage,
    resolve_canonical_run,
    validate_canonical_for_analysis,
)
from .io import sha256_file, write_json
from .metrics_orientation import (
    axial_orientation_histogram,
    axial_rose_svg,
    fourfold_orientation_order,
    numeric_distribution_svg,
    orientation_histogram_svg,
    planar_axial_orientation,
    shannon_orientation_entropy,
)
from .versioned import git_provenance, software_environment


SCHEMA_VERSION = "1.0.0"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUMMARY_FIELDS = ["metric_name", "value", "units", "method", "weighting", "population", "interpretation"]
EDGE_FIELDS = [
    "edge_id", "u", "v", "key", "geometry_length_local", "chord_length_local",
    "circuity", "excess_length_local", "chord_orientation_deg", "vertex_count",
    "geometry_segment_count", "zero_length_segment_count", "zero_chord",
]
SEGMENT_FIELDS = ["edge_id", "segment_index", "segment_length_local", "segment_orientation_deg"]


class GeometryMetricError(RuntimeError):
    """Raised when planar geometry assumptions or identities fail."""


@dataclass(frozen=True)
class GeometryConfig:
    schema_version: str
    zero_chord_tolerance_local: float
    zero_segment_tolerance_local: float
    length_comparison_tolerance_local: float
    circuity_floating_tolerance: float
    standard_deviation_ddof: int
    orientation_convention: str
    axial_period_degrees: int
    orientation_bins: int
    orientation_boundary_tolerance_degrees: float
    chord_weighting: str
    segment_weighting: str
    entropy_logarithm_base: str
    order_symmetry: int
    path: Path
    file_sha256: str

    @property
    def orientation_bin_width_degrees(self) -> float:
        return self.axial_period_degrees / self.orientation_bins


@dataclass(frozen=True)
class GeometryCalculation:
    geometry_summary_rows: tuple[dict[str, Any], ...]
    orientation_summary_rows: tuple[dict[str, Any], ...]
    edge_rows: tuple[dict[str, Any], ...]
    segment_rows: tuple[dict[str, Any], ...]
    chord_histogram: tuple[dict[str, float | int], ...]
    segment_histogram: tuple[dict[str, float | int], ...]
    methodology: dict[str, Any]

    def metric(self, name: str) -> Any:
        matches = [
            row["value"]
            for row in (*self.geometry_summary_rows, *self.orientation_summary_rows)
            if row["metric_name"] == name
        ]
        if len(matches) != 1:
            raise KeyError(f"Expected exactly one geometry/orientation metric named {name!r}.")
        return matches[0]


@dataclass(frozen=True)
class GeometryAnalysisResult:
    selection: CanonicalSelection
    canonical: CanonicalData
    calculation: GeometryCalculation
    output_dir: Path
    manifest_path: Path
    manifest: dict[str, Any]


def _display_path(path: Path, project_root: Path) -> str:
    resolved, root = Path(path).resolve(strict=False), Path(project_root).resolve(strict=False)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved.as_posix()


def load_geometry_config(path: Path | None = None, *, project_root: Path = PROJECT_ROOT) -> GeometryConfig:
    """Load and strictly validate the Phase 7G configuration sections."""
    selected = Path(path) if path is not None else Path(project_root) / "config" / "metrics.yaml"
    if not selected.is_file():
        raise GeometryMetricError(f"Metrics configuration does not exist: {selected}")
    try:
        raw = yaml.safe_load(selected.read_text(encoding="utf-8"))
        geometry = raw["geometry"]
        orientation = raw["orientation"]
        order = raw["order_parameter"]
    except (OSError, TypeError, KeyError, yaml.YAMLError) as exc:
        raise GeometryMetricError(f"Invalid geometry metrics configuration: {exc}") from exc
    if set(raw) != {"schema_version", "topology", "geometry", "orientation", "order_parameter"}:
        raise GeometryMetricError("Metrics configuration has unknown or missing top-level sections.")
    if set(geometry) != {
        "zero_chord_tolerance_local", "zero_segment_tolerance_local",
        "length_comparison_tolerance_local", "circuity_floating_tolerance",
        "standard_deviation_ddof",
    }:
        raise GeometryMetricError("Geometry configuration has unknown or missing fields.")
    if set(orientation) != {
        "convention", "axial_period_degrees", "bins", "chord_weighting",
        "boundary_tolerance_degrees", "segment_weighting", "entropy_logarithm_base",
    } or set(order) != {"symmetry"}:
        raise GeometryMetricError("Orientation/order configuration has unknown or missing fields.")
    if str(raw["schema_version"]) != SCHEMA_VERSION:
        raise GeometryMetricError(f"Unsupported metrics schema_version: {raw['schema_version']}")
    tolerances = [
        float(geometry["zero_chord_tolerance_local"]),
        float(geometry["zero_segment_tolerance_local"]),
        float(geometry["length_comparison_tolerance_local"]),
        float(geometry["circuity_floating_tolerance"]),
    ]
    if any(not math.isfinite(value) or value < 0 for value in tolerances):
        raise GeometryMetricError("Geometry tolerances must be finite and non-negative.")
    if int(geometry["standard_deviation_ddof"]) != 0:
        raise GeometryMetricError("Phase 7G requires population standard deviation ddof=0.")
    if orientation["convention"] != "north_clockwise" or int(orientation["axial_period_degrees"]) != 180:
        raise GeometryMetricError("Phase 7G requires north_clockwise axial orientation over 180 degrees.")
    bins = int(orientation["bins"])
    if bins <= 1 or 180 % bins != 0:
        raise GeometryMetricError("Orientation bins must evenly divide the 180-degree axial domain.")
    boundary_tolerance = float(orientation["boundary_tolerance_degrees"])
    if not math.isfinite(boundary_tolerance) or not 0 <= boundary_tolerance < (180 / bins) / 2:
        raise GeometryMetricError("Orientation boundary tolerance must be finite, non-negative, and less than half a bin.")
    if orientation["chord_weighting"] != "equal_edge":
        raise GeometryMetricError("Chord orientation weighting must be equal_edge.")
    if orientation["segment_weighting"] != "segment_length_local":
        raise GeometryMetricError("Segment orientation weighting must be segment_length_local.")
    if orientation["entropy_logarithm_base"] != "natural":
        raise GeometryMetricError("Phase 7G entropy uses the natural logarithm.")
    if int(order["symmetry"]) != 4:
        raise GeometryMetricError("Phase 7G orientation order requires fourfold symmetry.")
    return GeometryConfig(
        SCHEMA_VERSION, *tolerances, 0, "north_clockwise", 180, bins, boundary_tolerance,
        "equal_edge", "segment_length_local", "natural", 4,
        selected.resolve(), sha256_file(selected),
    )


def planar_chord_length(first: Point, second: Point) -> float:
    """Return Euclidean distance between canonical endpoint nodes."""
    return math.hypot(float(second.x) - float(first.x), float(second.y) - float(first.y))


def planar_edge_circuity(
    geometry_length: float,
    chord_length: float,
    *,
    zero_chord_tolerance: float,
    floating_tolerance: float,
) -> float | None:
    """Return ``LineString.length / endpoint chord`` or ``None`` for a zero chord."""
    if chord_length <= zero_chord_tolerance:
        return None
    ratio = float(geometry_length) / float(chord_length)
    if ratio < 1.0:
        if 1.0 - ratio <= floating_tolerance:
            return 1.0
        raise GeometryMetricError(f"Geometrically invalid circuity below one: {ratio!r}")
    return ratio


def linestring_segment_records(
    edge_id: str,
    geometry: LineString,
    *,
    zero_tolerance: float,
) -> tuple[tuple[dict[str, Any], ...], int]:
    """Return positive consecutive geometry segments and count excluded zero segments."""
    records: list[dict[str, Any]] = []
    excluded = 0
    coordinates = tuple(geometry.coords)
    for index, (start, end) in enumerate(zip(coordinates[:-1], coordinates[1:], strict=True)):
        length = math.hypot(float(end[0]) - float(start[0]), float(end[1]) - float(start[1]))
        if length <= zero_tolerance:
            excluded += 1
            continue
        records.append({
            "edge_id": str(edge_id),
            "segment_index": index,
            "segment_length_local": length,
            "segment_orientation_deg": planar_axial_orientation(start, end, zero_tolerance=zero_tolerance),
        })
    return tuple(records), excluded


def _metric(name: str, value: Any, units: str, method: str, weighting: str, population: str, interpretation: str) -> dict[str, Any]:
    return dict(zip(SUMMARY_FIELDS, (name, value, units, method, weighting, population, interpretation)))


def _quartiles(values: list[float]) -> tuple[float, float]:
    if len(values) == 1:
        return values[0], values[0]
    first, _, third = statistics.quantiles(values, n=4, method="inclusive")
    return float(first), float(third)


def _descriptive_rows(prefix: str, values: list[float], *, units: str, population: str, interpretation: str) -> list[dict[str, Any]]:
    q1, q3 = _quartiles(values)
    method = "Python statistics; inclusive quartiles; population standard deviation ddof=0"
    return [
        _metric(f"total_{prefix}", math.fsum(values), units, "math.fsum", "unweighted sum", population, f"total {interpretation}"),
        _metric(f"mean_{prefix}", statistics.mean(values), units, method, "equal observation", population, f"mean {interpretation}"),
        _metric(f"median_{prefix}", statistics.median(values), units, method, "equal observation", population, f"median {interpretation}"),
        _metric(f"std_{prefix}", statistics.pstdev(values), units, method, "equal observation", population, f"population standard deviation of {interpretation}"),
        _metric(f"minimum_{prefix}", min(values), units, method, "unweighted", population, f"minimum {interpretation}"),
        _metric(f"maximum_{prefix}", max(values), units, method, "unweighted", population, f"maximum {interpretation}"),
        _metric(f"q1_{prefix}", q1, units, method, "unweighted", population, f"inclusive first quartile of {interpretation}"),
        _metric(f"q3_{prefix}", q3, units, method, "unweighted", population, f"inclusive third quartile of {interpretation}"),
    ]


def calculate_geometry_metrics(
    nodes: gpd.GeoDataFrame,
    edges: gpd.GeoDataFrame,
    config: GeometryConfig,
) -> GeometryCalculation:
    """Calculate all Phase 7G metrics directly from canonical planar geometry."""
    node_points = {str(row.node_id): row.geometry for row in nodes.itertuples()}
    if any(not isinstance(point, Point) for point in node_points.values()):
        raise GeometryMetricError("Canonical nodes must be Points.")
    edge_rows: list[dict[str, Any]] = []
    segment_rows: list[dict[str, Any]] = []
    length_disagreements: list[str] = []
    for row in edges.sort_values("edge_id", kind="stable").itertuples():
        edge_id, u, v, key = str(row.edge_id), str(row.u), str(row.v), int(row.key)
        geometry = row.geometry
        if not isinstance(geometry, LineString) or geometry.is_empty:
            raise GeometryMetricError(f"Edge {edge_id} requires a non-empty LineString.")
        if u not in node_points or v not in node_points:
            raise GeometryMetricError(f"Edge {edge_id} references a missing canonical endpoint node.")
        first, second = node_points[u], node_points[v]
        start, end = tuple(geometry.coords[0]), tuple(geometry.coords[-1])
        first_coordinate, second_coordinate = tuple(first.coords[0]), tuple(second.coords[0])
        if not (
            (start == first_coordinate and end == second_coordinate)
            or (start == second_coordinate and end == first_coordinate)
        ):
            raise GeometryMetricError(f"Edge {edge_id} LineString endpoints do not match canonical u/v nodes.")
        geometry_length = float(geometry.length)
        stored_length = getattr(row, "length_local", geometry_length)
        if not math.isclose(float(stored_length), geometry_length, rel_tol=1e-12, abs_tol=config.length_comparison_tolerance_local):
            length_disagreements.append(edge_id)
        chord = planar_chord_length(first, second)
        circuity = planar_edge_circuity(
            geometry_length, chord,
            zero_chord_tolerance=config.zero_chord_tolerance_local,
            floating_tolerance=config.circuity_floating_tolerance,
        )
        orientation = None if chord <= config.zero_chord_tolerance_local else planar_axial_orientation(
            first.coords[0], second.coords[0], zero_tolerance=config.zero_chord_tolerance_local
        )
        segments, excluded = linestring_segment_records(
            edge_id, geometry, zero_tolerance=config.zero_segment_tolerance_local
        )
        segment_length = math.fsum(float(item["segment_length_local"]) for item in segments)
        if not math.isclose(segment_length, geometry_length, rel_tol=1e-12, abs_tol=config.length_comparison_tolerance_local):
            raise GeometryMetricError(f"Edge {edge_id} positive segment lengths do not sum to LineString.length.")
        segment_rows.extend(segments)
        edge_rows.append({
            "edge_id": edge_id, "u": u, "v": v, "key": key,
            "geometry_length_local": geometry_length,
            "chord_length_local": chord,
            "circuity": circuity,
            "excess_length_local": geometry_length - chord,
            "chord_orientation_deg": orientation,
            "vertex_count": len(geometry.coords),
            "geometry_segment_count": len(segments),
            "zero_length_segment_count": excluded,
            "zero_chord": chord <= config.zero_chord_tolerance_local,
        })
    if length_disagreements:
        raise GeometryMetricError(f"Stored length_local differs from geometry length for edges: {length_disagreements}")
    if not edge_rows:
        raise GeometryMetricError("Geometry metrics require at least one physical edge.")
    lengths = [float(row["geometry_length_local"]) for row in edge_rows]
    chords = [float(row["chord_length_local"]) for row in edge_rows]
    eligible = [row for row in edge_rows if row["circuity"] is not None]
    circuitousness = [float(row["circuity"]) for row in eligible]
    total_segment_length = math.fsum(float(row["segment_length_local"]) for row in segment_rows)
    total_length = math.fsum(lengths)
    if not math.isclose(total_segment_length, total_length, rel_tol=1e-12, abs_tol=config.length_comparison_tolerance_local):
        raise GeometryMetricError("Network segment lengths do not sum to network LineString lengths.")
    chord_angles = [float(row["chord_orientation_deg"]) for row in eligible]
    segment_angles = [float(row["segment_orientation_deg"]) for row in segment_rows]
    segment_weights = [float(row["segment_length_local"]) for row in segment_rows]
    chord_histogram = axial_orientation_histogram(
        chord_angles, bins=config.orientation_bins,
        boundary_tolerance_degrees=config.orientation_boundary_tolerance_degrees,
    )
    segment_histogram = axial_orientation_histogram(
        segment_angles, weights=segment_weights, bins=config.orientation_bins,
        boundary_tolerance_degrees=config.orientation_boundary_tolerance_degrees,
    )
    chord_entropy = shannon_orientation_entropy(chord_histogram)
    segment_entropy = shannon_orientation_entropy(segment_histogram)
    phi_chord = fourfold_orientation_order(chord_angles, symmetry=config.order_symmetry)
    phi_segment = fourfold_orientation_order(
        segment_angles, weights=segment_weights, symmetry=config.order_symmetry
    )

    length_rows = _descriptive_rows("edge_length_local", lengths, units="local units", population="physical canonical edges", interpretation="geometry-derived LineString length")
    length_rows[0]["metric_name"] = "total_network_length_local"
    geometry_summary = [
        _metric("node_count", len(nodes), "nodes", "GeoDataFrame row count", "unweighted", "canonical nodes", "canonical nodes retained"),
        _metric("physical_edge_geometry_count", len(edge_rows), "physical streets", "GeoDataFrame row count", "unweighted", "canonical edges", "one geometry record per physical canonical street"),
        *length_rows,
        *_descriptive_rows("chord_length_local", chords, units="local units", population="physical canonical edges", interpretation="canonical endpoint Euclidean chord length"),
        _metric("zero_chord_edge_count", len(edge_rows) - len(eligible), "physical streets", "D <= configured tolerance", "unweighted", "physical canonical edges", "edges explicitly excluded from circuity and chord orientation"),
        _metric("eligible_circuity_edge_count", len(eligible), "physical streets", "D > configured tolerance", "unweighted", "physical canonical edges", "edges eligible for L/D circuity"),
        *_descriptive_rows("edge_circuity", circuitousness, units="ratio", population="positive-chord physical edges", interpretation="per-edge L_i / D_i circuity"),
        _metric("network_circuity", math.fsum(float(row["geometry_length_local"]) for row in eligible) / math.fsum(float(row["chord_length_local"]) for row in eligible), "ratio", "sum(L_i) / sum(D_i)", "aggregate length ratio", "positive-chord physical edges", "network aggregate circuity, distinct from mean per-edge circuity"),
        _metric("geometry_segment_count", len(segment_rows), "segments", "positive consecutive coordinate pairs", "unweighted", "LineString geometry segments", "geometry segments are not graph edges or nodes"),
        _metric("zero_length_geometry_segment_count", sum(int(row["zero_length_segment_count"]) for row in edge_rows), "segments", "segment length <= configured tolerance", "unweighted", "LineString coordinate pairs", "excluded zero-length geometry segments"),
        _metric("total_geometry_segment_length_local", total_segment_length, "local units", "math.fsum", "segment length", "positive LineString geometry segments", "independent sum equal to total physical LineString length"),
    ]
    orientation_summary = [
        _metric("orientation_bin_count", config.orientation_bins, "bins", "180 / configured bin width", "not applicable", "both axial distributions", "number of deterministic axial bins"),
        _metric("orientation_bin_width_degrees", config.orientation_bin_width_degrees, "degrees", "180 / bins", "not applicable", "both axial distributions", "axial histogram bin width"),
        _metric("chord_orientation_observation_count", len(chord_angles), "physical streets", "one chord orientation per eligible edge", "equal physical edge", "positive-chord physical edges", "overall endpoint-to-endpoint street axes"),
        _metric("segment_orientation_observation_count", len(segment_angles), "segments", "one orientation per positive geometry segment", "segment length", "LineString geometry segments", "local geometry orientations including internal curvature"),
        _metric("chord_orientation_entropy", chord_entropy["entropy"], "nats", "-sum(p_i * ln(p_i))", "equal physical edge", "36-bin chord orientations", "raw Shannon orientation entropy"),
        _metric("chord_orientation_maximum_entropy", chord_entropy["maximum_entropy"], "nats", "ln(number_of_bins)", "not applicable", "36-bin chord orientations", "maximum possible entropy for configured bins"),
        _metric("chord_orientation_normalized_entropy", chord_entropy["normalized_entropy"], "proportion", "H / ln(number_of_bins)", "equal physical edge", "36-bin chord orientations", "orientation disorder normalized to [0,1]"),
        _metric("segment_length_weighted_orientation_entropy", segment_entropy["entropy"], "nats", "-sum(p_i * ln(p_i))", "segment_length_local", "36-bin geometry-segment orientations", "raw length-weighted Shannon orientation entropy"),
        _metric("segment_orientation_maximum_entropy", segment_entropy["maximum_entropy"], "nats", "ln(number_of_bins)", "not applicable", "36-bin geometry-segment orientations", "maximum possible entropy for configured bins"),
        _metric("segment_length_weighted_normalized_entropy", segment_entropy["normalized_entropy"], "proportion", "H / ln(number_of_bins)", "segment_length_local", "36-bin geometry-segment orientations", "length-weighted orientation disorder normalized to [0,1]"),
        _metric("phi_chord", phi_chord, "proportion", "|sum(w*exp(i*4*theta))/sum(w)|", "equal physical edge", "positive-chord physical edges", "fourfold orthogonal orientation order"),
        _metric("phi_segment_length_weighted", phi_segment, "proportion", "|sum(w*exp(i*4*theta))/sum(w)|", "segment_length_local", "positive LineString geometry segments", "length-weighted fourfold local-orientation order"),
    ]
    methodology = {
        "schema_version": SCHEMA_VERSION,
        "scope": "planar physical-street geometry, circuity, axial orientation, entropy, and fourfold order",
        "coordinate_system": "GeoGami Local Cartesian",
        "coordinate_units": "local units, not metres",
        "length": "L_i = recomputed canonical LineString.length; stored length_local checked within configured tolerance",
        "chord": "D_i = Euclidean distance between canonical u/v Point geometries; LineString endpoints verified against those nodes",
        "circuity": {
            "edge": "L_i / D_i for D_i above zero-chord tolerance",
            "mean_edge_circuity": "arithmetic mean of individual L_i / D_i ratios",
            "network_circuity": "sum(L_i) / sum(D_i) over eligible positive-chord edges",
            "historical_H4": "not used or claimed supported; Phase 7G formulas are explicitly defined planar ratios",
        },
        "orientation": {
            "convention": "0 degrees North, 90 East, clockwise, axial normalization to [0,180)",
            "chord": "one endpoint-chord orientation per eligible physical canonical edge; internal curvature not represented",
            "segment": "one local axial orientation per positive consecutive LineString coordinate pair; vertices are geometry vertices, not graph nodes",
            "reversal_invariant": True,
        },
        "histogram": {"domain_degrees": [0, 180], "bins": config.orientation_bins, "bin_width_degrees": config.orientation_bin_width_degrees, "boundary_tolerance_degrees": config.orientation_boundary_tolerance_degrees, "boundary_rule": "angle modulo 180; snap only within configured tolerance of an exact boundary; then floor(angle/bin_width); [start,end)"},
        "entropy": {"formula": "H = -sum(p_i * ln(p_i)) over positive probabilities", "logarithm_base": "natural", "maximum": "ln(number_of_bins)", "normalized": "H / H_max"},
        "orientation_order": {"formula": "phi = |sum(w_i * exp(i * 4 * theta_i)) / sum(w_i)|", "theta_units": "radians", "range": [0, 1], "symmetry": config.order_symmetry},
        "figure_semantics": "SVG rose plots mirror each [0,180) axial observation across 180 degrees as an undirected axis; they are not traffic bearings",
        "forbidden_geographic_functions": ["osmnx.distance.add_edge_lengths", "osmnx.bearing.add_edge_bearings", "great-circle distance", "geographic nearest-node", "fake EPSG:4326 reprojection", "osmnx.plot_orientation"],
        "excluded_experimental_metrics": ["splines", "Frechet distance", "turning energy", "experimental curvature estimators"],
    }
    return GeometryCalculation(
        tuple(geometry_summary), tuple(orientation_summary), tuple(edge_rows),
        tuple(segment_rows), chord_histogram, segment_histogram, methodology,
    )


def _write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(value)
        stream.write("\n")


def _directory_hashes(path: Path) -> dict[str, str]:
    return {item.name: sha256_file(item) for item in sorted(path.iterdir()) if item.is_file()} if path.is_dir() else {}


def publish_geometry_metrics(
    selection: CanonicalSelection,
    calculation: GeometryCalculation,
    config: GeometryConfig,
    *,
    output_root: Path,
    project_root: Path = PROJECT_ROOT,
) -> tuple[Path, Path, dict[str, Any]]:
    """Publish geometry artifacts while proving Phase 7F outputs are untouched."""
    run_root = Path(output_root) / selection.environment / selection.run_id
    topology_dir = run_root / "topology"
    topology_before = _directory_hashes(topology_dir)
    output_dir = run_root / "geometry"
    figures_dir = output_dir / "figures"
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "geometry_metrics_summary_csv": output_dir / "geometry_metrics_summary.csv",
        "geometry_metrics_summary_json": output_dir / "geometry_metrics_summary.json",
        "edge_geometry_metrics_csv": output_dir / "edge_geometry_metrics.csv",
        "segment_orientation_metrics_csv": output_dir / "segment_orientation_metrics.csv",
        "orientation_metrics_summary_csv": output_dir / "orientation_metrics_summary.csv",
        "orientation_metrics_summary_json": output_dir / "orientation_metrics_summary.json",
        "geometry_methodology_json": output_dir / "geometry_methodology.json",
        "edge_length_distribution_svg": figures_dir / "edge_length_distribution.svg",
        "edge_circuity_distribution_svg": figures_dir / "edge_circuity_distribution.svg",
        "chord_orientation_histogram_svg": figures_dir / "chord_orientation_histogram.svg",
        "chord_orientation_rose_svg": figures_dir / "chord_orientation_rose.svg",
        "segment_orientation_histogram_svg": figures_dir / "segment_length_weighted_orientation_histogram.svg",
    }
    _write_csv(paths["geometry_metrics_summary_csv"], SUMMARY_FIELDS, calculation.geometry_summary_rows)
    write_json(paths["geometry_metrics_summary_json"], {"schema_version": SCHEMA_VERSION, "metrics": list(calculation.geometry_summary_rows)})
    _write_csv(paths["edge_geometry_metrics_csv"], EDGE_FIELDS, calculation.edge_rows)
    _write_csv(paths["segment_orientation_metrics_csv"], SEGMENT_FIELDS, calculation.segment_rows)
    _write_csv(paths["orientation_metrics_summary_csv"], SUMMARY_FIELDS, calculation.orientation_summary_rows)
    write_json(paths["orientation_metrics_summary_json"], {
        "schema_version": SCHEMA_VERSION,
        "metrics": list(calculation.orientation_summary_rows),
        "chord_histogram": list(calculation.chord_histogram),
        "segment_length_weighted_histogram": list(calculation.segment_histogram),
    })
    methodology = {
        **calculation.methodology,
        "configuration": {
            "path": _display_path(config.path, project_root), "file_sha256": config.file_sha256,
            "zero_chord_tolerance_local": config.zero_chord_tolerance_local,
            "zero_segment_tolerance_local": config.zero_segment_tolerance_local,
            "length_comparison_tolerance_local": config.length_comparison_tolerance_local,
            "circuity_floating_tolerance": config.circuity_floating_tolerance,
            "orientation_bins": config.orientation_bins,
            "orientation_bin_width_degrees": config.orientation_bin_width_degrees,
            "orientation_boundary_tolerance_degrees": config.orientation_boundary_tolerance_degrees,
            "chord_weighting": config.chord_weighting,
            "segment_weighting": config.segment_weighting,
        },
    }
    write_json(paths["geometry_methodology_json"], methodology)
    lengths = [float(row["geometry_length_local"]) for row in calculation.edge_rows]
    circuities = [float(row["circuity"]) for row in calculation.edge_rows if row["circuity"] is not None]
    label = selection.environment.replace("env", "Env", 1)
    _write_text(paths["edge_length_distribution_svg"], numeric_distribution_svg(lengths, title=f"{label} physical-edge length distribution", x_label="LineString length [local units]"))
    _write_text(paths["edge_circuity_distribution_svg"], numeric_distribution_svg(circuities, title=f"{label} edge circuity distribution", x_label="Circuity L / D [ratio]"))
    _write_text(paths["chord_orientation_histogram_svg"], orientation_histogram_svg(calculation.chord_histogram, title=f"{label} chord-orientation distribution", y_label="Equal-edge probability"))
    _write_text(paths["chord_orientation_rose_svg"], axial_rose_svg(calculation.chord_histogram, title=f"{label} axial chord-orientation rose"))
    _write_text(paths["segment_orientation_histogram_svg"], orientation_histogram_svg(calculation.segment_histogram, title=f"{label} segment-length-weighted orientation", y_label="Length-weighted probability"))
    topology_after = _directory_hashes(topology_dir)
    if topology_before != topology_after:
        raise GeometryMetricError("Phase 7F topology artifacts changed during Phase 7G publication.")
    artifacts = {
        name: {"path": _display_path(path, project_root), "file_sha256": sha256_file(path)}
        for name, path in paths.items()
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "analysis_type": "Phase 7G planar geometry and orientation metrics",
        "environment": selection.environment,
        "canonical_run_id": selection.run_id,
        "canonical": {
            "path": _display_path(selection.canonical_path, project_root),
            "file_sha256": selection.file_sha256,
            "scientific_content_signature": selection.scientific_content_signature,
            "topology_signature": selection.topology_signature,
            "publication_git_provenance": None if selection.manifest is None else selection.manifest.get("git_provenance"),
        },
        "analysis_git_provenance": git_provenance(project_root),
        "metrics_configuration": {"path": _display_path(config.path, project_root), "file_sha256": config.file_sha256, "schema_version": config.schema_version},
        "orientation_convention": {
            "angular_reference": "north_clockwise", "axial_domain_degrees": [0, 180],
            "bin_count": config.orientation_bins, "bin_width_degrees": config.orientation_bin_width_degrees,
            "bin_boundary_tolerance_degrees": config.orientation_boundary_tolerance_degrees,
            "entropy_logarithm_base": config.entropy_logarithm_base,
            "phi_formula": "|sum(w_i * exp(i * 4 * theta_i)) / sum(w_i)|",
            "chord_weighting": config.chord_weighting, "segment_weighting": config.segment_weighting,
        },
        "software_environment": software_environment(),
        "phase_7f_artifacts_preserved": {"status": "PASS", "file_sha256": topology_after},
        "artifacts": artifacts,
    }
    for label, path in {
        "phase_7d_manifest_preserved": run_root / "analysis_graph_manifest.json",
        "phase_7f_manifest_preserved": topology_dir / "topology_analysis_manifest.json",
    }.items():
        manifest[label] = {"path": _display_path(path, project_root), "available": path.is_file(), "file_sha256": sha256_file(path) if path.is_file() else None}
    manifest_path = output_dir / "geometry_analysis_manifest.json"
    write_json(manifest_path, manifest)
    return output_dir, manifest_path, manifest


def analyze_geometry(
    environment: str,
    *,
    canonical_run: str = "latest",
    canonical_path: Path | None = None,
    latest_path: Path | None = None,
    config_path: Path | None = None,
    output_root: Path | None = None,
    project_root: Path = PROJECT_ROOT,
    publish: bool = True,
) -> GeometryAnalysisResult:
    """Resolve and verify canonical data, calculate Phase 7G, and optionally publish."""
    project_root = Path(project_root).resolve()
    selection = resolve_canonical_run(
        environment, canonical_run=canonical_run, canonical_path=canonical_path,
        latest_path=latest_path, project_root=project_root,
    )
    raw_nodes, raw_edges = load_canonical_geopackage(selection.canonical_path)
    canonical = validate_canonical_for_analysis(raw_nodes, raw_edges)
    config = load_geometry_config(config_path, project_root=project_root)
    calculation = calculate_geometry_metrics(canonical.nodes, canonical.edges, config)
    chosen_output = Path(output_root) if output_root is not None else project_root / "results" / "analysis"
    if publish:
        output_dir, manifest_path, manifest = publish_geometry_metrics(
            selection, calculation, config, output_root=chosen_output, project_root=project_root
        )
    else:
        output_dir = chosen_output / environment / selection.run_id / "geometry"
        manifest_path = output_dir / "geometry_analysis_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    return GeometryAnalysisResult(selection, canonical, calculation, output_dir, manifest_path, manifest)
