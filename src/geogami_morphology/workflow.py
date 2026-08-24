"""Fail-fast professor workflow composed from the tested Phase 7 components."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Callable
import uuid

import geopandas as gpd

from .graph import AnalysisBuildResult, CanonicalSelection, build_analysis_graphs, resolve_canonical_run
from .guards import verify_frozen_baselines
from .integrated import IntegratedResult, integrate_results
from .io import sha256_file, write_json
from .metrics_geometry import GeometryAnalysisResult, analyze_geometry
from .metrics_topology import TopologyAnalysisResult, analyze_topology
from .versioned import VersionedRunResult, git_provenance, run_versioned_canonical, software_environment


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_SCHEMA_VERSION = "1.0.0"
SUPPORTED_MODE = "preserve-topology"
LOCK_SUFFIXES = ("-wal", "-shm", "-journal")


class WorkflowError(RuntimeError):
    """A named workflow stage failed and downstream stages were not run."""

    def __init__(self, stage_number: int, stage_name: str, cause: Exception | str):
        self.stage_number = stage_number
        self.stage_name = stage_name
        self.cause = cause
        super().__init__(f"Stage {stage_number} ({stage_name}) failed: {cause}")


@dataclass(frozen=True)
class FullAnalysisResult:
    environment: str
    dry_run: bool
    run_id: str | None
    canonical_path: Path | None
    canonical_manifest_path: Path | None
    graphml_path: Path | None
    topology_dir: Path | None
    geometry_dir: Path | None
    integrated_dir: Path | None
    end_to_end_manifest_path: Path | None
    analysis_latest_path: Path
    manifest: dict[str, Any]


def _utc_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _display_path(path: Path, project_root: Path) -> str:
    resolved, root = Path(path).resolve(strict=False), Path(project_root).resolve(strict=False)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved.as_posix()


def qgis_lock_files(input_path: Path) -> tuple[Path, ...]:
    """Return GeoPackage transaction sidecars without deleting or judging them."""
    selected = Path(input_path)
    return tuple(path for path in (Path(str(selected) + suffix) for suffix in LOCK_SUFFIXES) if path.exists())


def preflight_editable_input(input_path: Path, *, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Perform cheap read-only checks before creating any versioned output."""
    root, selected = Path(project_root).resolve(), Path(input_path).resolve()
    if not selected.is_file():
        raise ValueError(f"Editable input does not exist: {selected}")
    if selected.suffix.lower() != ".gpkg":
        raise ValueError("Editable input must be a .gpkg GeoPackage.")
    protected_roots = (root / "data" / "baselines", root / "data" / "canonical")
    for protected in protected_roots:
        try:
            selected.relative_to(protected.resolve())
        except ValueError:
            continue
        raise ValueError(
            "Frozen baseline and canonical GeoPackages cannot be used as professor editable input. "
            "Use data/editable/grid/env39_editable.gpkg."
        )
    locks = qgis_lock_files(selected)
    if locks:
        names = ", ".join(path.name for path in locks)
        raise ValueError(
            "Close QGIS and save all layer edits before running the analysis. "
            f"GeoPackage transaction file(s) detected: {names}"
        )
    try:
        layer_names = set(gpd.list_layers(selected)["name"].astype(str))
        missing_layers = {"nodes", "edges"} - layer_names
        if missing_layers:
            raise ValueError(f"Editable GeoPackage is missing layer(s): {', '.join(sorted(missing_layers))}")
        nodes = gpd.read_file(selected, layer="nodes")
        edges = gpd.read_file(selected, layer="edges")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Editable GeoPackage could not be read: {exc}") from exc
    missing_node_fields = {"node_id", "geometry"} - set(nodes.columns)
    missing_edge_fields = {"edge_id", "u", "v", "key", "geometry"} - set(edges.columns)
    if missing_node_fields or missing_edge_fields:
        raise ValueError(
            f"Editable scientific identity fields are missing: "
            f"nodes={sorted(missing_node_fields)}, edges={sorted(missing_edge_fields)}"
        )
    if nodes.crs is None or edges.crs is None or nodes.crs != edges.crs:
        raise ValueError("Editable nodes and edges require the same valid CRS.")
    if nodes.crs.is_geographic:
        raise ValueError("Editable input must use the GeoGami projected/local Cartesian CRS, not a geographic CRS.")
    return {
        "path": _display_path(selected, root),
        "file_sha256": sha256_file(selected),
        "layers": sorted(layer_names),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "crs": nodes.crs.to_wkt(),
    }


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object.")
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        write_json(candidate, value)
        os.replace(candidate, path)
    finally:
        if candidate.exists():
            candidate.unlink()


def _stage(number: int, name: str, action: Callable[[], Any], report: Callable[[str], None]) -> Any:
    report(f"[{number}/8] {name}")
    try:
        result = action()
    except Exception as exc:
        report("FAIL")
        raise WorkflowError(number, name, exc) from exc
    report("PASS")
    report("")
    return result


def _verify_and_publish_summary(
    *, project_root: Path, environment: str, selection: CanonicalSelection,
    editable: dict[str, Any] | None, editable_path: Path | None,
    frozen_before: dict[str, str], graph: AnalysisBuildResult,
    topology: TopologyAnalysisResult, geometry: GeometryAnalysisResult,
    integrated: IntegratedResult, metrics_config: Path, analysis_latest: Path,
) -> tuple[Path, dict[str, Any]]:
    run_id = selection.run_id
    stage_manifests = {
        "canonical_manifest": selection.manifest_path,
        "analysis_graph_manifest": graph.manifest_path,
        "phase_7f_topology_manifest": topology.manifest_path,
        "phase_7g_geometry_manifest": geometry.manifest_path,
        "phase_7h_integrated_manifest": integrated.manifest_path,
    }
    if any(path is None or not Path(path).is_file() for path in stage_manifests.values()):
        raise RuntimeError("One or more required stage manifests are missing.")
    canonical_document = _read_json(Path(selection.manifest_path), "canonical manifest")
    graph_document = _read_json(graph.manifest_path, "analysis graph manifest")
    topology_document = _read_json(topology.manifest_path, "topology manifest")
    geometry_document = _read_json(geometry.manifest_path, "geometry manifest")
    integrated_document = _read_json(integrated.manifest_path, "integrated manifest")
    observed_run_ids = {
        canonical_document.get("identity", {}).get("run_id"),
        graph_document.get("canonical_run_id"),
        topology_document.get("canonical_run_id"),
        geometry_document.get("canonical_run_id"),
        integrated_document.get("canonical_run_id"),
    }
    if observed_run_ids != {run_id}:
        raise RuntimeError(f"Cross-stage canonical run IDs disagree: {sorted(map(str, observed_run_ids))}")
    identity = canonical_document.get("network_identity", {})
    graph_nx, graph_ox = graph_document.get("networkx_graph", {}), graph_document.get("osmnx_graph", {})
    expected_nodes, expected_edges = identity.get("node_count"), identity.get("physical_edge_count")
    if graph_nx.get("node_count") != expected_nodes or graph_nx.get("physical_edge_count") != expected_edges:
        raise RuntimeError("NetworkX graph identity disagrees with the canonical manifest.")
    if graph_ox.get("physical_canonical_edge_count") != expected_edges:
        raise RuntimeError("OSMnx physical street count disagrees with the canonical manifest.")
    if graph_ox.get("directed_arc_count") != 2 * expected_edges:
        raise RuntimeError("OSMnx reciprocal directed-arc count is not twice the physical street count.")
    required_outputs = (
        selection.canonical_path, graph.graphml_path,
        integrated.output_dir / "comparison_ready_metrics.csv",
        integrated.output_dir / "core_metrics.csv",
        integrated.output_dir / f"{environment}_baseline_summary.md",
    )
    missing = [str(path) for path in required_outputs if not Path(path).is_file()]
    if missing:
        raise RuntimeError(f"Complete analysis output is missing: {missing}")
    config_sha = sha256_file(metrics_config)
    if geometry_document.get("metrics_configuration", {}).get("file_sha256") != config_sha:
        raise RuntimeError("Geometry manifest configuration hash does not match the selected metrics configuration.")
    upstream = integrated_document.get("provenance_chain", {})
    expected_upstream = {
        "phase_7d_graph_manifest": sha256_file(graph.manifest_path),
        "phase_7f_topology_manifest": sha256_file(topology.manifest_path),
        "phase_7g_geometry_manifest": sha256_file(geometry.manifest_path),
        "metrics_configuration": config_sha,
    }
    for name, digest in expected_upstream.items():
        if upstream.get(name, {}).get("file_sha256") != digest:
            raise RuntimeError(f"Integrated provenance link is invalid: {name}")
    if editable_path is not None and editable is not None:
        after = sha256_file(editable_path)
        if after != editable["file_sha256"]:
            raise RuntimeError("Professor editable GeoPackage changed during analysis execution.")
    else:
        after = None
    frozen_after = verify_frozen_baselines(project_root)
    if frozen_after != frozen_before:
        raise RuntimeError("A frozen baseline changed during analysis execution.")
    end_manifest_path = graph.output_dir / "end_to_end_analysis_manifest.json"
    if end_manifest_path.exists():
        raise RuntimeError(f"End-to-end manifest already exists and will not be overwritten: {end_manifest_path}")
    manifest = {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "workflow": "professor_end_to_end_preserve_topology",
        "final_status": "PASS",
        "created_at_utc": _utc_text(),
        "environment": environment,
        "canonical_run_id": run_id,
        "editable_source": None if editable is None else {
            "path": editable["path"],
            "file_sha256_before": editable["file_sha256"],
            "file_sha256_after": after,
            "unchanged": after == editable["file_sha256"],
        },
        "canonical": {
            "path": _display_path(selection.canonical_path, project_root),
            "file_sha256": selection.file_sha256,
            "scientific_content_signature": selection.scientific_content_signature,
            "topology_signature": selection.topology_signature,
        },
        "network_identity": {
            "node_count": expected_nodes,
            "physical_edge_count": expected_edges,
            "component_count": identity.get("component_count"),
            "osmnx_directed_arc_count": graph_ox.get("directed_arc_count"),
            "osmnx_physical_street_count": graph_ox.get("physical_canonical_edge_count"),
        },
        "stage_manifests": {
            name: {"path": _display_path(Path(path), project_root), "file_sha256": sha256_file(Path(path))}
            for name, path in stage_manifests.items()
        },
        "metrics_configuration": {
            "path": _display_path(metrics_config, project_root), "file_sha256": config_sha,
        },
        "frozen_baseline_guard": {"before": frozen_before, "after": frozen_after, "status": "PASS"},
        "git_provenance": git_provenance(project_root),
        "software_environment": software_environment(),
        "principal_outputs": {
            "graphml": _display_path(graph.graphml_path, project_root),
            "topology_results": _display_path(topology.output_dir, project_root),
            "geometry_results": _display_path(geometry.output_dir, project_root),
            "integrated_results": _display_path(integrated.output_dir, project_root),
            "comparison_ready_metrics": _display_path(integrated.output_dir / "comparison_ready_metrics.csv", project_root),
            "core_metrics": _display_path(integrated.output_dir / "core_metrics.csv", project_root),
            "baseline_summary": _display_path(integrated.output_dir / f"{environment}_baseline_summary.md", project_root),
        },
    }
    write_json(end_manifest_path, manifest)
    pointer = {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "environment": environment,
        "canonical_run_id": run_id,
        "canonical_path": _display_path(selection.canonical_path, project_root),
        "integrated_results_path": _display_path(integrated.output_dir, project_root),
        "comparison_ready_metrics_path": _display_path(integrated.output_dir / "comparison_ready_metrics.csv", project_root),
        "analysis_manifest_path": _display_path(end_manifest_path, project_root),
        "created_at_utc": manifest["created_at_utc"],
    }
    _atomic_json(analysis_latest, pointer)
    return end_manifest_path, manifest


def run_full_analysis(
    environment: str, *, input_path: Path | None = None, canonical_run: str | None = None,
    mode: str = SUPPORTED_MODE, reference_canonical: Path | None = None,
    runs_root: Path | None = None, canonical_latest: Path | None = None,
    analysis_root: Path | None = None, analysis_latest: Path | None = None,
    metrics_config: Path | None = None, endpoint_tolerance: float = 1e-6,
    dry_run: bool = False, verbose: bool = False,
    project_root: Path = PROJECT_ROOT, reporter: Callable[[str], None] = print,
) -> FullAnalysisResult:
    """Run or plan the strictly gated Phase 7B-through-7H workflow."""
    root = Path(project_root).resolve()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", environment):
        raise WorkflowError(2, "Editable input validation", "Environment must use letters, digits, underscore, or hyphen.")
    if mode != SUPPORTED_MODE:
        raise WorkflowError(2, "Editable input validation", f"Unsupported mode: {mode}")
    if (input_path is None) == (canonical_run is None):
        raise WorkflowError(2, "Editable input validation", "Specify exactly one of input_path or canonical_run.")
    reference = Path(reference_canonical) if reference_canonical is not None else root / "data" / "canonical" / "grid" / "env39_canonical.gpkg"
    canonical_runs = Path(runs_root) if runs_root is not None else root / "data" / "canonical" / "grid" / "runs"
    canonical_pointer = Path(canonical_latest) if canonical_latest is not None else canonical_runs.parent / "latest.json"
    analysis_output = Path(analysis_root) if analysis_root is not None else root / "results" / "analysis"
    analysis_pointer = Path(analysis_latest) if analysis_latest is not None else analysis_output / environment / "latest.json"
    config = Path(metrics_config) if metrics_config is not None else root / "config" / "metrics.yaml"
    selected_input = None if input_path is None else Path(input_path).resolve()
    reporter("GEOGAMI STREET MORPHOLOGY ANALYSIS")
    reporter("")
    reporter(f"Environment: {environment}")
    reporter(f"Input: {_display_path(selected_input, root) if selected_input else '(existing canonical run)'}")
    reporter(f"Mode: {mode}")
    reporter("")
    frozen_before = _stage(1, "Frozen baseline guard", lambda: verify_frozen_baselines(root), reporter)

    preselected: CanonicalSelection | None = None

    def validate_inputs() -> dict[str, Any] | None:
        nonlocal preselected
        if not config.is_file():
            raise ValueError(f"Metrics configuration does not exist: {config}")
        if selected_input is None:
            preselected = resolve_canonical_run(
                environment, canonical_run=str(canonical_run), latest_path=canonical_pointer,
                project_root=root,
            )
            return None
        if not reference.is_file():
            raise ValueError(f"Reference canonical GeoPackage does not exist: {reference}")
        return preflight_editable_input(selected_input, project_root=root)

    editable = _stage(2, "Editable input validation", validate_inputs, reporter)
    if dry_run:
        run_token = preselected.run_id if preselected is not None else "<new_run_id>"
        reporter("DRY RUN: no canonical run, analysis artifacts, metrics, or pointers will be created.")
        reporter("Planned stages: canonical publication/selection -> graph build -> topology metrics -> geometry/orientation metrics -> integrated results -> final verification")
        reporter(f"Planned canonical: {_display_path(canonical_runs / run_token / f'{environment}_canonical.gpkg', root)}")
        reporter(f"Planned analysis: {_display_path(analysis_output / environment / run_token, root)}")
        reporter(f"Canonical latest pointer: {_display_path(canonical_pointer, root)} (unchanged)")
        reporter(f"Analysis latest pointer: {_display_path(analysis_pointer, root)} (unchanged)")
        reporter("FINAL RESULT: DRY RUN PASS")
        return FullAnalysisResult(environment, True, None, None, None, None, None, None, None, None, analysis_pointer, {})

    canonical_result: VersionedRunResult | None = None

    def canonical_stage() -> CanonicalSelection:
        nonlocal canonical_result
        if selected_input is None:
            if preselected is None:  # pragma: no cover - Stage 2 establishes this invariant
                raise RuntimeError("Existing canonical run was not preflighted.")
            return preselected
        canonical_result = run_versioned_canonical(
            environment, selected_input, reference, canonical_runs,
            latest_path=canonical_pointer, endpoint_tolerance=endpoint_tolerance,
            project_root=root, configuration_paths=(root / "environment.yml", config),
        )
        return resolve_canonical_run(
            environment, canonical_path=canonical_result.canonical_path, project_root=root
        )

    selection = _stage(3, "Canonical network build", canonical_stage, reporter)
    identity = selection.manifest.get("network_identity", {}) if selection.manifest else {}
    reporter(f"Run ID: {selection.run_id}")
    reporter(
        "Nodes / physical edges / components: "
        f"{identity.get('node_count')} / {identity.get('physical_edge_count')} / {identity.get('component_count')}"
    )
    reporter("")
    graph = _stage(4, "NetworkX / OSMnx graph build", lambda: build_analysis_graphs(
        environment, canonical_path=selection.canonical_path,
        output_root=analysis_output, project_root=root,
    ), reporter)
    reporter(f"NetworkX: {graph.networkx_graph.number_of_nodes()} nodes / {graph.networkx_graph.number_of_edges()} physical streets")
    reporter(f"OSMnx: {graph.osmnx_graph.number_of_nodes()} nodes / {graph.osmnx_graph.number_of_edges()} directed arcs / {graph.networkx_graph.number_of_edges()} physical streets")
    reporter("")
    topology = _stage(5, "Topology metrics", lambda: analyze_topology(
        environment, canonical_path=selection.canonical_path, config_path=config,
        output_root=analysis_output, project_root=root, publish=True,
    ), reporter)
    geometry = _stage(6, "Geometry / orientation metrics", lambda: analyze_geometry(
        environment, canonical_path=selection.canonical_path, config_path=config,
        output_root=analysis_output, project_root=root, publish=True,
    ), reporter)
    integrated = _stage(7, "Integrated results", lambda: integrate_results(
        environment, canonical_path=selection.canonical_path,
        output_root=analysis_output, project_root=root, publish=True,
    ), reporter)
    end_manifest_path, manifest = _stage(8, "Final cross-stage verification", lambda: _verify_and_publish_summary(
        project_root=root, environment=environment, selection=selection,
        editable=editable, editable_path=selected_input, frozen_before=frozen_before,
        graph=graph, topology=topology, geometry=geometry, integrated=integrated,
        metrics_config=config, analysis_latest=analysis_pointer,
    ), reporter)
    if verbose:
        reporter(f"Canonical SHA-256: {selection.file_sha256}")
        reporter(f"Topology signature: {selection.topology_signature}")
        reporter(f"Metrics config SHA-256: {sha256_file(config)}")
        reporter("")
    reporter("FINAL RESULTS:")
    reporter(f"Canonical GeoPackage: {selection.canonical_path}")
    reporter(f"Canonical manifest: {selection.manifest_path}")
    reporter(f"GraphML: {graph.graphml_path}")
    reporter(f"Topology results: {topology.output_dir}")
    reporter(f"Geometry results: {geometry.output_dir}")
    reporter(f"Integrated results: {integrated.output_dir}")
    reporter(f"Comparison-ready metrics: {integrated.output_dir / 'comparison_ready_metrics.csv'}")
    reporter(f"Core metrics: {integrated.output_dir / 'core_metrics.csv'}")
    reporter(f"Baseline summary: {integrated.output_dir / f'{environment}_baseline_summary.md'}")
    reporter(f"End-to-end manifest: {end_manifest_path}")
    reporter(f"Analysis latest pointer: {analysis_pointer}")
    reporter(f"Notebook directory: {root / 'notebooks'}")
    reporter("")
    reporter("FINAL RESULT: PASS")
    return FullAnalysisResult(
        environment, False, selection.run_id, selection.canonical_path,
        selection.manifest_path, graph.graphml_path, topology.output_dir,
        geometry.output_dir, integrated.output_dir, end_manifest_path,
        analysis_pointer, manifest,
    )
