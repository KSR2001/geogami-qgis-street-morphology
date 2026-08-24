"""Comparison-ready integration of accepted Phase 7F and Phase 7G results.

This module deliberately does not calculate scientific metrics. It verifies and
loads the published machine-readable summaries, classifies a concise selection,
and publishes a provenance-linked Phase 7H research package.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable

from .graph import (
    CanonicalSelection,
    load_canonical_geopackage,
    resolve_canonical_run,
    validate_canonical_for_analysis,
)
from .io import sha256_file, write_json
from .notebook import canonical_network_svg, degree_count_svg
from .versioned import git_provenance, software_environment


PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPARISON_SCHEMA_VERSION = "1.0.0"
METRIC_FAMILIES = frozenset(
    {"topology_controlled", "geometry_weighted_network", "geometric_morphology", "orientation_morphology"}
)
COMPARISON_ROLES = frozenset(
    {"topology_control", "geometry_sensitive", "orientation_sensitive", "context_only"}
)
COMPARISON_FIELDS = [
    "environment", "canonical_run_id", "metric_id", "metric_label", "metric_family",
    "value", "units", "population", "weighting", "library_or_method",
    "higher_lower_interpretation", "comparison_role", "metric_tier",
]


class IntegratedAnalysisError(RuntimeError):
    """Raised when the accepted result chain cannot be integrated safely."""


@dataclass(frozen=True)
class MetricDefinition:
    source: str
    source_metric_name: str
    metric_id: str
    label: str
    family: str
    tier: str
    population: str
    role: str
    selection_rationale: str


@dataclass(frozen=True)
class IntegratedResult:
    selection: CanonicalSelection
    metrics: tuple[dict[str, Any], ...]
    core_metrics: tuple[dict[str, Any], ...]
    output_dir: Path
    manifest_path: Path
    manifest: dict[str, Any]
    figure_catalog: tuple[dict[str, Any], ...]


def _definition(
    source: str, name: str, metric_id: str, label: str, family: str, tier: str,
    population: str, role: str, rationale: str,
) -> MetricDefinition:
    return MetricDefinition(source, name, metric_id, label, family, tier, population, role, rationale)


METRIC_DEFINITIONS = (
    # Core topology controls: compact evidence that connectivity is held fixed.
    _definition("topology", "node_count", "topology.node_count", "Canonical nodes", "topology_controlled", "core", "canonical nodes", "topology_control", "Defines graph order and is a direct topology-control check."),
    _definition("topology", "physical_edge_count", "topology.physical_edge_count", "Physical canonical edges", "topology_controlled", "core", "undirected physical edges", "topology_control", "Defines graph size without counting reciprocal OSMnx arcs."),
    _definition("topology", "connected_component_count", "topology.component_count", "Connected components", "topology_controlled", "core", "physical graph", "topology_control", "Confirms network connectedness and the analyzed component scope."),
    _definition("topology", "degree_mean", "topology.mean_degree", "Mean physical-node degree", "topology_controlled", "core", "canonical nodes", "topology_control", "Summarizes junction connectivity independently of edge geometry."),
    _definition("topology", "dead_end_proportion", "topology.dead_end_proportion", "Dead-end proportion", "topology_controlled", "core", "canonical nodes", "topology_control", "Captures the share of terminal nodes in a size-comparable form."),
    _definition("topology", "degree_3_proportion", "topology.degree_3_proportion", "Degree-3 node proportion", "topology_controlled", "core", "canonical nodes", "topology_control", "Captures three-way junction composition in a size-comparable form."),
    _definition("topology", "degree_4_proportion", "topology.degree_4_proportion", "Degree-4 node proportion", "topology_controlled", "core", "canonical nodes", "topology_control", "Captures four-way junction composition associated with grid structure."),
    _definition("topology", "cycle_rank", "topology.cycle_rank", "Cycle rank", "topology_controlled", "core", "physical graph", "topology_control", "Measures independent cycles and verifies controlled route redundancy."),
    # One core geometry-weighted routing result.
    _definition("topology", "average_shortest_path_length_local", "network_weighted.mean_shortest_path_length", "Mean weighted shortest-path length", "geometry_weighted_network", "core", "unordered canonical-node pairs", "geometry_sensitive", "Summarizes routing cost when physical edge lengths weight the controlled graph."),
    # Core direct geometry.
    _definition("geometry", "total_network_length_local", "geometry.total_length", "Total physical network length", "geometric_morphology", "core", "undirected physical edges", "geometry_sensitive", "Captures total realized street length on the fixed topology."),
    _definition("geometry", "mean_edge_length_local", "geometry.mean_edge_length", "Mean physical-edge length", "geometric_morphology", "core", "undirected physical edges", "geometry_sensitive", "Summarizes the scale of canonical physical edges."),
    _definition("geometry", "mean_edge_circuity", "geometry.mean_edge_circuity", "Mean edge circuity", "geometric_morphology", "core", "eligible undirected physical edges", "geometry_sensitive", "Summarizes edge-level geometric deviation from chords."),
    _definition("geometry", "network_circuity", "geometry.network_circuity", "Network circuity", "geometric_morphology", "core", "eligible undirected physical edges", "geometry_sensitive", "Provides the length-ratio aggregate for the entire physical network."),
    # Core orientation results preserve both observation populations.
    _definition("orientation", "chord_orientation_entropy", "orientation.chord_entropy", "Chord orientation entropy", "orientation_morphology", "core", "physical-edge chords", "orientation_sensitive", "Measures directional dispersion with one equally weighted chord per edge."),
    _definition("orientation", "chord_orientation_normalized_entropy", "orientation.chord_entropy_normalized", "Normalized chord orientation entropy", "orientation_morphology", "core", "physical-edge chords", "orientation_sensitive", "Makes chord entropy interpretable relative to the configured maximum."),
    _definition("orientation", "segment_length_weighted_orientation_entropy", "orientation.segment_entropy_length_weighted", "Segment-length-weighted orientation entropy", "orientation_morphology", "core", "nonzero LineString segments", "orientation_sensitive", "Measures directional dispersion while retaining curved-edge segment geometry and length weighting."),
    _definition("orientation", "segment_length_weighted_normalized_entropy", "orientation.segment_entropy_length_weighted_normalized", "Normalized segment-length-weighted entropy", "orientation_morphology", "core", "nonzero LineString segments", "orientation_sensitive", "Makes segment-weighted entropy interpretable relative to the configured maximum."),
    _definition("orientation", "phi_chord", "orientation.phi_chord", "Fourfold order, chords", "orientation_morphology", "core", "physical-edge chords", "orientation_sensitive", "Quantifies fourfold axial order for equally weighted edge chords."),
    _definition("orientation", "phi_segment_length_weighted", "orientation.phi_segment_length_weighted", "Fourfold order, length-weighted segments", "orientation_morphology", "core", "nonzero LineString segments", "orientation_sensitive", "Quantifies fourfold axial order while retaining segment length weighting."),
    # Selected supplementary controls and distributions; detailed centrality remains in Phase 7F tables.
    _definition("topology", "is_connected", "topology.is_connected", "Graph is connected", "topology_controlled", "supplementary", "physical graph", "topology_control", "Explicit connectedness quality-control result."),
    _definition("topology", "self_loop_count", "topology.self_loop_count", "Self-loop count", "topology_controlled", "supplementary", "undirected physical edges", "topology_control", "Documents self-loop topology without expanding the core table."),
    _definition("topology", "parallel_physical_edge_count", "topology.parallel_edge_count", "Parallel physical-edge count", "topology_controlled", "supplementary", "undirected physical edges", "topology_control", "Documents multigraph structure without changing physical-edge semantics."),
    _definition("topology", "bridge_count", "topology.bridge_count", "Bridge count", "topology_controlled", "supplementary", "simple physical graph edges", "topology_control", "Provides additional topological vulnerability context."),
    _definition("topology", "articulation_point_count", "topology.articulation_point_count", "Articulation-point count", "topology_controlled", "supplementary", "canonical nodes", "topology_control", "Provides additional node-vulnerability context."),
    _definition("topology", "node_connectivity", "topology.node_connectivity", "Node connectivity", "topology_controlled", "supplementary", "physical graph", "topology_control", "Provides global connectivity context."),
    _definition("topology", "edge_connectivity", "topology.edge_connectivity", "Edge connectivity", "topology_controlled", "supplementary", "physical graph", "topology_control", "Provides global edge-connectivity context."),
    _definition("topology", "average_shortest_path_hops", "topology.mean_shortest_path_hops", "Mean shortest-path hops", "topology_controlled", "supplementary", "unordered canonical-node pairs", "topology_control", "Separates unweighted graph distance from geometry-weighted routing cost."),
    _definition("topology", "diameter_hops", "topology.diameter_hops", "Diameter in hops", "topology_controlled", "supplementary", "physical graph", "topology_control", "Documents maximum unweighted graph distance."),
    _definition("topology", "radius_hops", "topology.radius_hops", "Radius in hops", "topology_controlled", "supplementary", "physical graph", "topology_control", "Documents minimum eccentricity in unweighted graph distance."),
    _definition("geometry", "median_edge_length_local", "geometry.median_edge_length", "Median physical-edge length", "geometric_morphology", "supplementary", "undirected physical edges", "geometry_sensitive", "Adds robust edge-length distribution context."),
    _definition("geometry", "maximum_edge_length_local", "geometry.maximum_edge_length", "Maximum physical-edge length", "geometric_morphology", "supplementary", "undirected physical edges", "geometry_sensitive", "Documents the upper edge-length extent."),
    _definition("geometry", "median_edge_circuity", "geometry.median_edge_circuity", "Median edge circuity", "geometric_morphology", "supplementary", "eligible undirected physical edges", "geometry_sensitive", "Adds a robust edge-circuity distribution summary."),
    _definition("geometry", "maximum_edge_circuity", "geometry.maximum_edge_circuity", "Maximum edge circuity", "geometric_morphology", "supplementary", "eligible undirected physical edges", "geometry_sensitive", "Documents the strongest individual-edge deviation from its chord."),
    _definition("geometry", "geometry_segment_count", "geometry.segment_count", "Geometry segment count", "geometric_morphology", "supplementary", "all LineString segments", "context_only", "Documents the segment population used by segment-based orientation analysis."),
    _definition("geometry", "zero_chord_edge_count", "geometry.zero_chord_edge_count", "Zero-chord edge count", "geometric_morphology", "supplementary", "undirected physical edges", "context_only", "Documents circuity eligibility and geometry quality control."),
    _definition("orientation", "orientation_bin_count", "orientation.bin_count", "Orientation-bin count", "orientation_morphology", "supplementary", "configured orientation histogram", "context_only", "Records the fixed discretization needed to interpret entropy."),
    _definition("orientation", "orientation_bin_width_degrees", "orientation.bin_width_degrees", "Orientation-bin width", "orientation_morphology", "supplementary", "configured orientation histogram", "context_only", "Records the fixed angular resolution needed for comparison."),
    _definition("orientation", "chord_orientation_observation_count", "orientation.chord_observation_count", "Chord-orientation observations", "orientation_morphology", "supplementary", "physical-edge chords", "context_only", "Documents the chord observation population."),
    _definition("orientation", "segment_orientation_observation_count", "orientation.segment_observation_count", "Segment-orientation observations", "orientation_morphology", "supplementary", "nonzero LineString segments", "context_only", "Documents the segment observation population."),
)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegratedAnalysisError(f"Could not read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise IntegratedAnalysisError(f"{label} must be a JSON object.")
    return value


def _display_path(path: Path, project_root: Path) -> str:
    resolved, root = Path(path).resolve(strict=False), Path(project_root).resolve(strict=False)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved.as_posix()


def _summary_index(document: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
    rows = document.get("metrics")
    if not isinstance(rows, list):
        raise IntegratedAnalysisError(f"{label} has no metrics list.")
    index = {str(row.get("metric_name")): row for row in rows if isinstance(row, dict)}
    if len(index) != len(rows):
        raise IntegratedAnalysisError(f"{label} contains invalid or duplicate metric names.")
    return index


def assemble_comparison_rows(
    *, environment: str, canonical_run_id: str, topology_summary: dict[str, Any],
    geometry_summary: dict[str, Any], orientation_summary: dict[str, Any],
    definitions: Iterable[MetricDefinition] = METRIC_DEFINITIONS,
) -> tuple[dict[str, Any], ...]:
    """Map stored summary rows into the reusable long-form comparison schema."""
    sources = {
        "topology": _summary_index(topology_summary, "topology summary"),
        "geometry": _summary_index(geometry_summary, "geometry summary"),
        "orientation": _summary_index(orientation_summary, "orientation summary"),
    }
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for definition in definitions:
        if definition.metric_id in seen or environment.lower() in definition.metric_id.lower():
            raise IntegratedAnalysisError(f"Metric ID is duplicate or environment-specific: {definition.metric_id}")
        if definition.family not in METRIC_FAMILIES or definition.role not in COMPARISON_ROLES:
            raise IntegratedAnalysisError(f"Invalid classification for {definition.metric_id}.")
        try:
            source = sources[definition.source][definition.source_metric_name]
        except KeyError as exc:
            raise IntegratedAnalysisError(
                f"Accepted source metric is missing: {definition.source}:{definition.source_metric_name}"
            ) from exc
        if definition.source == "topology":
            library_or_method = f"{source['library']}: {source['function']}"
        else:
            library_or_method = str(source["method"])
        output.append({
            "environment": environment,
            "canonical_run_id": canonical_run_id,
            "metric_id": definition.metric_id,
            "metric_label": definition.label,
            "metric_family": definition.family,
            "value": source["value"],
            "units": source["units"],
            "population": definition.population if definition.source == "topology" else source["population"],
            "weighting": source["weighting"],
            "library_or_method": library_or_method,
            "higher_lower_interpretation": source["interpretation"],
            "comparison_role": definition.role,
            "metric_tier": definition.tier,
        })
        seen.add(definition.metric_id)
    return tuple(output)


def _verify_manifest_artifacts(manifest: dict[str, Any], project_root: Path, label: str) -> None:
    for name, record in manifest.get("artifacts", {}).items():
        if not isinstance(record, dict) or not record.get("path") or not record.get("file_sha256"):
            raise IntegratedAnalysisError(f"Malformed {label} artifact record: {name}")
        path = Path(project_root, record["path"]).resolve()
        if not path.is_file() or sha256_file(path) != record["file_sha256"]:
            raise IntegratedAnalysisError(f"{label} artifact hash mismatch: {name}")


def _write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COMPARISON_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _directory_hashes(path: Path) -> dict[str, str]:
    return {
        item.relative_to(path).as_posix(): sha256_file(item)
        for item in sorted(path.rglob("*")) if item.is_file()
    } if path.is_dir() else {}


def _baseline_markdown(selection: CanonicalSelection, core: tuple[dict[str, Any], ...]) -> str:
    by_id = {row["metric_id"]: row for row in core}
    table = ["| Family | Metric | Value | Units |", "|---|---|---:|---|"]
    for row in core:
        value = row["value"]
        shown = str(value) if isinstance(value, int) else f"{float(value):.12g}"
        table.append(f"| {row['metric_family']} | {row['metric_label']} | {shown} | {row['units']} |")
    return "\n".join([
        "# Env39 baseline summary", "",
        "This machine-produced baseline integrates the accepted Phase 7F topology and Phase 7G geometry/orientation results; it does not recalculate metrics.", "",
        "## Canonical identity", "",
        f"- Canonical run: `{selection.run_id}`",
        f"- Canonical SHA-256: `{selection.file_sha256}`",
        f"- Scientific-content signature: `{selection.scientific_content_signature}`",
        f"- Topology signature: `{selection.topology_signature}`", "",
        "## Core metrics", "", *table, "",
        "## Key findings", "",
        f"- The controlled physical graph has {by_id['topology.node_count']['value']} nodes, {by_id['topology.physical_edge_count']['value']} edges, and {by_id['topology.component_count']['value']} connected component.",
        f"- Mean degree is {float(by_id['topology.mean_degree']['value']):.3f}; dead ends comprise {float(by_id['topology.dead_end_proportion']['value']):.3f} of canonical nodes.",
        f"- Mean edge circuity ({float(by_id['geometry.mean_edge_circuity']['value']):.4f}) and network circuity ({float(by_id['geometry.network_circuity']['value']):.4f}) indicate low planar deviation from straight chords.",
        f"- Normalized chord and segment-length-weighted entropies ({float(by_id['orientation.chord_entropy_normalized']['value']):.4f} and {float(by_id['orientation.segment_entropy_length_weighted_normalized']['value']):.4f}) indicate concentrated axial orientation under the accepted binning.",
        f"- Fourfold order is strong for both chords ({float(by_id['orientation.phi_chord']['value']):.4f}) and length-weighted segments ({float(by_id['orientation.phi_segment_length_weighted']['value']):.4f}).", "",
        "## Limitations", "",
        "These descriptive values characterize Env39 under the accepted local-coordinate, graph, binning, and weighting definitions. They do not establish that one environment is better, nor do they demonstrate causal effects on navigation. A later Env38 comparison must first confirm identical topology-control metrics and then interpret geometry-sensitive differences within the experimental setup.", "",
    ])


def integrate_results(
    environment: str = "env39", *, canonical_run: str = "latest",
    project_root: Path = PROJECT_ROOT, publish: bool = True,
) -> IntegratedResult:
    """Verify accepted inputs, assemble classified results, and optionally publish Phase 7H."""
    root = Path(project_root).resolve()
    selection = resolve_canonical_run(environment, canonical_run=canonical_run, project_root=root)
    run_root = root / "results" / "analysis" / environment / selection.run_id
    topology_dir, geometry_dir = run_root / "topology", run_root / "geometry"
    topology_manifest_path = topology_dir / "topology_analysis_manifest.json"
    geometry_manifest_path = geometry_dir / "geometry_analysis_manifest.json"
    graph_manifest_path = run_root / "analysis_graph_manifest.json"
    topology_manifest = _read_json(topology_manifest_path, "Phase 7F manifest")
    geometry_manifest = _read_json(geometry_manifest_path, "Phase 7G manifest")
    graph_manifest = _read_json(graph_manifest_path, "Phase 7D manifest")
    expected_identity = {
        "file_sha256": selection.file_sha256,
        "scientific_content_signature": selection.scientific_content_signature,
        "topology_signature": selection.topology_signature,
    }
    for label, manifest in (("Phase 7D", graph_manifest), ("Phase 7F", topology_manifest), ("Phase 7G", geometry_manifest)):
        if manifest.get("canonical_run_id") != selection.run_id:
            raise IntegratedAnalysisError(f"{label} canonical run ID mismatch.")
        canonical = manifest.get("canonical", {})
        for key, value in expected_identity.items():
            if canonical.get(key) != value:
                raise IntegratedAnalysisError(f"{label} canonical {key} mismatch.")
    _verify_manifest_artifacts(topology_manifest, root, "Phase 7F")
    _verify_manifest_artifacts(geometry_manifest, root, "Phase 7G")
    config_path = root / geometry_manifest["metrics_configuration"]["path"]
    config_sha = sha256_file(config_path)
    if config_sha != geometry_manifest["metrics_configuration"]["file_sha256"]:
        raise IntegratedAnalysisError("Current metrics configuration does not match the accepted Phase 7G manifest.")
    before_topology, before_geometry = _directory_hashes(topology_dir), _directory_hashes(geometry_dir)
    topology_summary = _read_json(topology_dir / "topology_metrics_summary.json", "Phase 7F topology summary")
    geometry_summary = _read_json(geometry_dir / "geometry_metrics_summary.json", "Phase 7G geometry summary")
    orientation_summary = _read_json(geometry_dir / "orientation_metrics_summary.json", "Phase 7G orientation summary")
    metrics = assemble_comparison_rows(
        environment=environment, canonical_run_id=selection.run_id,
        topology_summary=topology_summary, geometry_summary=geometry_summary,
        orientation_summary=orientation_summary,
    )
    core = tuple(row for row in metrics if row["metric_tier"] == "core")
    output_dir = run_root / "integrated"
    manifest_path = output_dir / "integrated_analysis_manifest.json"
    figure_catalog: tuple[dict[str, Any], ...]
    if publish:
        output_dir.mkdir(parents=True, exist_ok=True)
        figures_dir = output_dir / "figures"
        figures_dir.mkdir(parents=True, exist_ok=True)
        nodes, edges = load_canonical_geopackage(selection.canonical_path)
        canonical = validate_canonical_for_analysis(nodes, edges)
        (figures_dir / "canonical_physical_network.svg").write_text(
            canonical_network_svg(canonical.nodes, canonical.edges, title="Canonical Env39 physical street network"),
            encoding="utf-8", newline="\n",
        )
        topology_values = _summary_index(topology_summary, "topology summary")
        degrees = {
            degree: int(topology_values[f"degree_count_{degree}"]["value"])
            for degree in (1, 3, 4) if f"degree_count_{degree}" in topology_values
        }
        (figures_dir / "degree_junction_structure.svg").write_text(
            degree_count_svg(degrees, title="Env39 physical-node degree structure"),
            encoding="utf-8", newline="\n",
        )
        figure_specs = (
            ("canonical_network", "Canonical physical street network", figures_dir / "canonical_physical_network.svg", "Phase 7H view of canonical geometry"),
            ("degree_structure", "Degree and junction structure", figures_dir / "degree_junction_structure.svg", "Phase 7H view of stored Phase 7F summary"),
            ("edge_circuity", "Edge circuity distribution", geometry_dir / "figures" / "edge_circuity_distribution.svg", "Reused Phase 7G figure"),
            ("chord_orientation", "Chord orientation rose", geometry_dir / "figures" / "chord_orientation_rose.svg", "Reused Phase 7G figure"),
            ("segment_orientation", "Segment-length-weighted orientation distribution", geometry_dir / "figures" / "segment_length_weighted_orientation_histogram.svg", "Reused Phase 7G figure"),
        )
        figure_catalog = tuple({
            "figure_id": figure_id, "label": label, "path": _display_path(path, root),
            "file_sha256": sha256_file(path), "provenance": provenance,
        } for figure_id, label, path, provenance in figure_specs)
        _write_csv(output_dir / "comparison_ready_metrics.csv", metrics)
        _write_csv(output_dir / "core_metrics.csv", core)
        rationale = {definition.metric_id: definition.selection_rationale for definition in METRIC_DEFINITIONS if definition.tier == "core"}
        write_json(output_dir / "core_metrics.json", {
            "comparison_schema_version": COMPARISON_SCHEMA_VERSION,
            "environment": environment, "canonical_run_id": selection.run_id,
            "selection_principle": "A concise control, routing-cost, geometry, and orientation baseline; detailed node/edge metrics remain in Phase 7F/7G artifacts.",
            "selection_rationale": rationale, "metrics": list(core),
        })
        methodology = {
            "comparison_schema_version": COMPARISON_SCHEMA_VERSION,
            "metric_families": {
                "topology_controlled": "Depends only on graph connectivity/topology and should remain identical when frozen topology is maintained.",
                "geometry_weighted_network": "Uses physical edge length as graph cost and may change when geometry changes on fixed connectivity.",
                "geometric_morphology": "Derives directly from planar physical geometry and may change between geometric realizations.",
                "orientation_morphology": "Describes axial street orientation under the fixed Phase 7G binning and weighting definitions.",
            },
            "comparison_roles": sorted(COMPARISON_ROLES),
            "controlled_comparison": {
                "identical_topology_metrics": "Evidence that experimental topology control was maintained.",
                "different_geometric_metrics": "Differences associated with geometric realization rather than graph connectivity; no broader causal claim is implied.",
                "different_geometry_weighted_network_metrics": "Routing-cost consequences of changed edge lengths/geometries on otherwise controlled connectivity.",
            },
            "core_metric_selection_rationale": rationale,
            "historical_methodology_note": "Earlier approximate outputs (orientation entropy 1.5144 and phi 0.9966) are retained only as historical results. Their insufficiently aligned/documented methodology makes them not directly comparable with the explicit Phase 7G chord and segment-length-weighted baselines; the Phase 7G methodology is current.",
            "figures": list(figure_catalog),
        }
        write_json(output_dir / "integrated_methodology.json", methodology)
        (output_dir / "env39_baseline_summary.md").write_text(
            _baseline_markdown(selection, core), encoding="utf-8", newline="\n"
        )
        artifact_paths = {
            "comparison_ready_metrics_csv": output_dir / "comparison_ready_metrics.csv",
            "core_metrics_csv": output_dir / "core_metrics.csv",
            "core_metrics_json": output_dir / "core_metrics.json",
            "integrated_methodology_json": output_dir / "integrated_methodology.json",
            "env39_baseline_summary_md": output_dir / "env39_baseline_summary.md",
            "canonical_network_svg": figures_dir / "canonical_physical_network.svg",
            "degree_structure_svg": figures_dir / "degree_junction_structure.svg",
        }
        manifest = {
            "schema_version": "1.0.0", "analysis_type": "integrated_research_results",
            "comparison_schema_version": COMPARISON_SCHEMA_VERSION,
            "environment": environment, "canonical_run_id": selection.run_id,
            "canonical": {
                "path": _display_path(selection.canonical_path, root),
                "file_sha256": selection.file_sha256,
                "scientific_content_signature": selection.scientific_content_signature,
                "topology_signature": selection.topology_signature,
            },
            "provenance_chain": {
                "phase_7d_graph_manifest": {"path": _display_path(graph_manifest_path, root), "file_sha256": sha256_file(graph_manifest_path)},
                "phase_7f_topology_manifest": {"path": _display_path(topology_manifest_path, root), "file_sha256": sha256_file(topology_manifest_path)},
                "phase_7g_geometry_manifest": {"path": _display_path(geometry_manifest_path, root), "file_sha256": sha256_file(geometry_manifest_path)},
                "metrics_configuration": {"path": _display_path(config_path, root), "file_sha256": config_sha},
            },
            "analysis_git_provenance": git_provenance(root),
            "software_environment": software_environment(),
            "source_artifacts_preserved": {
                "phase_7f": before_topology, "phase_7g": before_geometry,
            },
            "figures": list(figure_catalog),
            "artifacts": {
                name: {"path": _display_path(path, root), "file_sha256": sha256_file(path)}
                for name, path in artifact_paths.items()
            },
        }
        write_json(manifest_path, manifest)
        if _directory_hashes(topology_dir) != before_topology or _directory_hashes(geometry_dir) != before_geometry:
            raise IntegratedAnalysisError("Phase 7H modified an accepted Phase 7F or Phase 7G artifact.")
    else:
        manifest = _read_json(manifest_path, "Phase 7H manifest") if manifest_path.is_file() else {}
        figure_catalog = tuple(manifest.get("figures", ()))
    return IntegratedResult(selection, metrics, core, output_dir, manifest_path, manifest, figure_catalog)
