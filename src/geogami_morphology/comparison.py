"""Controlled descriptive comparison of accepted Env38 and Env39 results.

This module never calculates environment-specific morphology metrics. It verifies
accepted artifacts, joins their stored comparison rows by stable ``metric_id``,
and publishes a provenance-locked cross-environment comparison package.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
import json
import math
from pathlib import Path
import re
import shutil
from typing import Any, Iterable

import networkx as nx

from .environments import get_environment
from .graph import load_canonical_geopackage, resolve_canonical_run, validate_canonical_for_analysis
from .integrated import COMPARISON_FIELDS, COMPARISON_ROLES, METRIC_FAMILIES
from .io import sha256_file, write_json
from .versioned import git_provenance, software_environment


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "1.0.0"
OUTPUT_FIELDS = [
    "metric_id", "metric_label", "metric_family", "comparison_role", "unit",
    "population", "env39_value", "env38_value", "absolute_difference",
    "signed_difference_env38_minus_env39", "relative_difference_percent",
    "comparison_status", "interpretation_note",
]
METADATA_FIELDS = [
    "metric_label", "metric_family", "units", "population", "weighting",
    "library_or_method", "higher_lower_interpretation", "comparison_role", "metric_tier",
]
CONTROL_METRICS = {
    "topology.node_count": 46,
    "topology.physical_edge_count": 69,
    "topology.component_count": 1,
    "topology.mean_degree": 3.0,
    "topology.cycle_rank": 24,
}
DETAILED_TOPOLOGY_FIELDS = {
    "node_count": 46,
    "physical_edge_count": 69,
    "connected_component_count": 1,
    "degree_mean": 3.0,
    "dead_end_count": 10,
    "degree_3_count": 16,
    "degree_4_count": 20,
    "cycle_rank": 24,
}
DETAILED_GEOMETRY_FIELDS = (
    "total_network_length_local", "mean_edge_length_local", "median_edge_length_local",
    "total_chord_length_local", "mean_edge_circuity", "network_circuity",
    "geometry_segment_count", "zero_chord_edge_count",
)
DETAILED_ORIENTATION_FIELDS = (
    "orientation_bin_count", "orientation_bin_width_degrees",
    "chord_orientation_observation_count", "segment_orientation_observation_count",
    "chord_orientation_entropy", "chord_orientation_normalized_entropy",
    "segment_length_weighted_orientation_entropy",
    "segment_length_weighted_normalized_entropy", "phi_chord",
    "phi_segment_length_weighted",
)


class ComparisonError(RuntimeError):
    """Raised when accepted inputs cannot support the controlled comparison."""


@dataclass(frozen=True)
class AcceptedEnvironment:
    environment: str
    run_id: str
    run_root: Path
    canonical_path: Path
    canonical_manifest_path: Path
    analysis_manifest_path: Path
    integrated_manifest_path: Path
    comparison_metrics_path: Path
    core_metrics_path: Path
    scientific_content_signature: str
    topology_signature: str
    canonical: Any
    comparison_rows: tuple[dict[str, str], ...]
    core_rows: tuple[dict[str, str], ...]
    integrated_manifest: dict[str, Any]


@dataclass(frozen=True)
class ComparisonResult:
    comparison_id: str
    output_dir: Path
    manifest_path: Path
    manifest: dict[str, Any]
    comparison_rows: tuple[dict[str, Any], ...]
    core_rows: tuple[dict[str, Any], ...]
    summary: dict[str, Any]


def _display_path(path: Path, root: Path) -> str:
    resolved = Path(path).resolve(strict=False)
    try:
        return resolved.relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"Could not read {label}: {exc}") from exc
    if not isinstance(document, dict):
        raise ComparisonError(f"{label} must be a JSON object.")
    return document


def _read_csv(path: Path, label: str) -> tuple[list[str], tuple[dict[str, str], ...]]:
    try:
        with Path(path).open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            fields = list(reader.fieldnames or [])
            rows = tuple(dict(row) for row in reader)
    except OSError as exc:
        raise ComparisonError(f"Could not read {label}: {exc}") from exc
    return fields, rows


def _write_csv(path: Path, fields: list[str], rows: Iterable[dict[str, Any]]) -> None:
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _summary_index(path: Path, label: str) -> dict[str, Any]:
    document = _read_json(path, label)
    rows = document.get("metrics")
    if not isinstance(rows, list):
        raise ComparisonError(f"{label} has no metrics list.")
    return {str(row["metric_name"]): row["value"] for row in rows}


def _tree_signature(path: Path, root: Path) -> dict[str, Any]:
    files = sorted(item for item in Path(path).rglob("*") if item.is_file())
    records = [(item.relative_to(path).as_posix(), sha256_file(item)) for item in files]
    payload = "".join(f"{name}|{digest}\n" for name, digest in records).encode("utf-8")
    import hashlib
    return {
        "path": _display_path(path, root),
        "file_count": len(files),
        "tree_sha256": hashlib.sha256(payload).hexdigest().upper(),
    }


def _source_snapshot(env38: AcceptedEnvironment, env39: AcceptedEnvironment, root: Path) -> dict[str, Any]:
    environment38 = get_environment("env38", repository_root=root)
    environment39 = get_environment("env39", repository_root=root)
    return {
        "env38_canonical_run": _tree_signature(env38.canonical_path.parent, root),
        "env38_analysis_run": _tree_signature(env38.run_root, root),
        "env39_canonical_run": _tree_signature(env39.canonical_path.parent, root),
        "env39_analysis_run": _tree_signature(env39.run_root, root),
        "env38_canonical_latest_sha256": sha256_file(environment38.canonical_latest),
        "env38_analysis_latest_sha256": sha256_file(environment38.analysis_root / "latest.json"),
        "env39_canonical_latest_sha256": sha256_file(environment39.canonical_latest),
        "env39_analysis_latest_sha256": sha256_file(environment39.analysis_root / "latest.json"),
    }


def _resolve_accepted(environment_id: str, root: Path) -> AcceptedEnvironment:
    environment = get_environment(environment_id, repository_root=root)
    canonical_latest = _read_json(environment.canonical_latest, f"{environment_id} canonical latest")
    analysis_latest_path = environment.analysis_root / "latest.json"
    analysis_latest = _read_json(analysis_latest_path, f"{environment_id} analysis latest")
    selection = resolve_canonical_run(
        environment_id,
        canonical_run="latest",
        latest_path=environment.canonical_latest,
        project_root=root,
    )
    if canonical_latest.get("run_id") != analysis_latest.get("canonical_run_id") != selection.run_id:
        raise ComparisonError(f"{environment_id} latest pointers disagree.")
    if selection.run_id != canonical_latest.get("run_id") or selection.run_id != analysis_latest.get("canonical_run_id"):
        raise ComparisonError(f"{environment_id} canonical and analysis latest pointers disagree.")
    expected_canonical = (root / analysis_latest["canonical_path"]).resolve()
    if selection.canonical_path != expected_canonical:
        raise ComparisonError(f"{environment_id} analysis latest references a different canonical file.")
    analysis_manifest_path = (root / analysis_latest["analysis_manifest_path"]).resolve()
    run_root = analysis_manifest_path.parent
    comparison_path = (root / analysis_latest["comparison_ready_metrics_path"]).resolve()
    integrated_dir = (root / analysis_latest["integrated_results_path"]).resolve()
    integrated_manifest_path = integrated_dir / "integrated_analysis_manifest.json"
    core_path = integrated_dir / "core_metrics.csv"
    for label, path in {
        "analysis manifest": analysis_manifest_path,
        "integrated manifest": integrated_manifest_path,
        "comparison metrics": comparison_path,
        "core metrics": core_path,
    }.items():
        if not path.is_file():
            raise ComparisonError(f"{environment_id} {label} is missing: {path}")
    fields, rows = _read_csv(comparison_path, f"{environment_id} comparison metrics")
    core_fields, core_rows = _read_csv(core_path, f"{environment_id} core metrics")
    if fields != COMPARISON_FIELDS or core_fields != COMPARISON_FIELDS:
        raise ComparisonError(f"{environment_id} comparison schema does not match the accepted schema.")
    if len(rows) != 39 or len(core_rows) != 19:
        raise ComparisonError(f"{environment_id} requires 39 comparison metrics and 19 core metrics.")
    raw_nodes, raw_edges = load_canonical_geopackage(selection.canonical_path)
    canonical = validate_canonical_for_analysis(raw_nodes, raw_edges)
    integrated_manifest = _read_json(integrated_manifest_path, f"{environment_id} integrated manifest")
    return AcceptedEnvironment(
        environment_id, selection.run_id, run_root, selection.canonical_path,
        selection.manifest_path, analysis_manifest_path, integrated_manifest_path,
        comparison_path, core_path, selection.scientific_content_signature,
        selection.topology_signature, canonical, rows, core_rows, integrated_manifest,
    )


def _stable_connectivity(canonical) -> tuple[tuple[str, ...], tuple[tuple[str, str, str, str], ...]]:
    nodes = tuple(sorted(str(value) for value in canonical.nodes["node_id"]))
    edges = tuple(sorted(
        (str(row.edge_id), min(str(row.u), str(row.v)), max(str(row.u), str(row.v)), str(row.key))
        for row in canonical.edges.itertuples()
    ))
    return nodes, edges


def _verify_topology_control(env38: AcceptedEnvironment, env39: AcceptedEnvironment) -> dict[str, Any]:
    if env38.topology_signature != env39.topology_signature:
        raise ComparisonError("CONTROL_FAILURE: topology signatures differ.")
    if _stable_connectivity(env38.canonical) != _stable_connectivity(env39.canonical):
        raise ComparisonError("CONTROL_FAILURE: stable node/physical-edge connectivity identities differ.")
    results: dict[str, Any] = {}
    for accepted in (env39, env38):
        topology = _summary_index(
            accepted.run_root / "topology" / "topology_metrics_summary.json",
            f"{accepted.environment} topology summary",
        )
        observed = {name: topology[name] for name in DETAILED_TOPOLOGY_FIELDS}
        if observed != DETAILED_TOPOLOGY_FIELDS:
            raise ComparisonError(f"CONTROL_FAILURE: {accepted.environment} topology values differ from contract.")
        results[accepted.environment] = observed
    return {
        "status": "PASS",
        "topology_signature": env38.topology_signature,
        "stable_node_ids_equal": True,
        "stable_physical_edge_connectivity_equal": True,
        "env39": results["env39"],
        "env38": results["env38"],
    }


def _parse_value(value: str) -> float | bool:
    if value == "True":
        return True
    if value == "False":
        return False
    try:
        number = float(value)
    except ValueError as exc:
        raise ComparisonError(f"Comparison value is not numeric or boolean: {value!r}") from exc
    if not math.isfinite(number):
        raise ComparisonError(f"Comparison value is non-finite: {value!r}")
    return number


def comparison_arithmetic(
    env39_value: float | bool,
    env38_value: float | bool,
    *,
    relative_appropriate: bool = True,
) -> tuple[float | None, float | None, float | None]:
    """Return absolute, signed Env38-minus-Env39, and optional relative difference."""
    if isinstance(env39_value, bool) or isinstance(env38_value, bool):
        return None, None, None
    signed = float(env38_value) - float(env39_value)
    absolute = abs(signed)
    relative = None
    if relative_appropriate and float(env39_value) != 0.0:
        relative = 100.0 * signed / abs(float(env39_value))
    return absolute, signed, relative


def _interpretation(row: dict[str, str], status: str, signed: float | None) -> str:
    if status == "CONTROL_MATCH":
        return "Expected equality confirms the controlled graph topology; it does not imply equal morphology."
    if status == "CONTROL_FAILURE":
        return "Unexpected topology difference invalidates the controlled comparison."
    if status == "DESCRIPTIVE_MATCH":
        return "The stored descriptive values are equal under the accepted shared methodology."
    direction = "greater" if signed is not None and signed > 0 else "lower"
    if row["metric_family"] == "geometry_weighted_network":
        return f"Env38 is {direction}; equal topology isolates a difference in local geometry-based edge costs."
    if row["metric_family"] == "orientation_morphology":
        return f"Env38 is {direction} for this directional morphology descriptor; no navigation claim is implied."
    if row["units"] == "local units":
        return f"Env38 is {direction} for this planar geometry descriptor in GeoGami local units."
    return f"Env38 is {direction} for this dimensionless planar geometry descriptor."


def compare_metric_rows(
    env39_rows: Iterable[dict[str, str]],
    env38_rows: Iterable[dict[str, str]],
) -> tuple[dict[str, Any], ...]:
    """Join accepted rows by metric_id and calculate controlled descriptive differences."""
    env39_rows = tuple(env39_rows)
    env38_rows = tuple(env38_rows)
    left = {row["metric_id"]: row for row in env39_rows}
    right = {row["metric_id"]: row for row in env38_rows}
    if len(left) != len(env39_rows) or len(right) != len(env38_rows):
        raise ComparisonError("Duplicate metric_id values are prohibited.")
    if set(left) != set(right):
        raise ComparisonError("Env38 and Env39 metric-ID sets differ.")
    output: list[dict[str, Any]] = []
    for metric_id in sorted(left):
        env39, env38 = left[metric_id], right[metric_id]
        for field in METADATA_FIELDS:
            if env39[field] != env38[field]:
                raise ComparisonError(f"Metric {metric_id} metadata differs for field {field}.")
        value39, value38 = _parse_value(env39["value"]), _parse_value(env38["value"])
        equal = value39 == value38
        if env39["metric_family"] == "topology_controlled":
            status = "CONTROL_MATCH" if equal else "CONTROL_FAILURE"
        else:
            status = "DESCRIPTIVE_MATCH" if equal else "DESCRIPTIVE_DIFFERENCE"
        relative_appropriate = env39["comparison_role"] in {"geometry_sensitive", "orientation_sensitive"}
        absolute, signed, relative = comparison_arithmetic(
            value39, value38, relative_appropriate=relative_appropriate
        )
        output.append({
            "metric_id": metric_id,
            "metric_label": env39["metric_label"],
            "metric_family": env39["metric_family"],
            "comparison_role": env39["comparison_role"],
            "unit": env39["units"],
            "population": env39["population"],
            "env39_value": env39["value"],
            "env38_value": env38["value"],
            "absolute_difference": absolute,
            "signed_difference_env38_minus_env39": signed,
            "relative_difference_percent": relative,
            "comparison_status": status,
            "interpretation_note": _interpretation(env39, status, signed),
            "metric_tier": env39["metric_tier"],
            "weighting": env39["weighting"],
            "library_or_method": env39["library_or_method"],
        })
    failures = [row["metric_id"] for row in output if row["comparison_status"] == "CONTROL_FAILURE"]
    if failures:
        raise ComparisonError(f"CONTROL_FAILURE for metrics: {failures}")
    return tuple(output)


def _phase9f_verification(env38: AcceptedEnvironment) -> dict[str, Any]:
    path = env38.run_root / "env38_reproducibility_comparison.json"
    audit = _read_json(path, "Phase 9F reproducibility comparison")
    expected = (
        audit.get("final_reproducibility_status") == "PASS"
        and audit.get("scientific_mismatch_count") == 0
        and audit.get("comparison_ready_metrics", {}).get("exact_match_count") == 39
        and audit.get("core_metrics", {}).get("exact_match_count") == 19
        and audit.get("second_clean_run") == env38.run_id
    )
    if not expected:
        raise ComparisonError("Phase 9F reproducibility acceptance is missing or invalid.")
    return {
        "path": _display_path(path, PROJECT_ROOT),
        "file_sha256": sha256_file(path),
        "comparison_ready_exact_match_count": 39,
        "core_exact_match_count": 19,
        "scientific_mismatch_count": 0,
        "first_accepted_run": audit.get("first_accepted_run"),
        "first_freeze_artifact": audit.get("first_freeze_artifact"),
        "second_clean_run": audit.get("second_clean_run"),
        "status": "PASS",
    }


def _detailed_geometry(environment: AcceptedEnvironment) -> dict[str, Any]:
    geometry = _summary_index(
        environment.run_root / "geometry" / "geometry_metrics_summary.json",
        f"{environment.environment} geometry summary",
    )
    _, edge_rows = _read_csv(
        environment.run_root / "geometry" / "edge_geometry_metrics.csv",
        f"{environment.environment} edge geometry",
    )
    return {
        **{name: geometry[name] for name in DETAILED_GEOMETRY_FIELDS},
        "absolute_excess_length_local": math.fsum(float(row["excess_length_local"]) for row in edge_rows),
    }


def _orientation(environment: AcceptedEnvironment) -> tuple[dict[str, Any], dict[str, Any]]:
    path = environment.run_root / "geometry" / "orientation_metrics_summary.json"
    document = _read_json(path, f"{environment.environment} orientation summary")
    metrics = {str(row["metric_name"]): row["value"] for row in document["metrics"]}
    return ({name: metrics[name] for name in DETAILED_ORIENTATION_FIELDS}, document)


def _centrality_leaders(environment: AcceptedEnvironment) -> dict[str, Any]:
    _, node_rows = _read_csv(
        environment.run_root / "topology" / "node_topology_metrics.csv",
        f"{environment.environment} node topology table",
    )
    _, edge_rows = _read_csv(
        environment.run_root / "topology" / "edge_topology_metrics.csv",
        f"{environment.environment} edge topology table",
    )

    def leaders(rows, id_field: str, value_field: str, *, reverse: bool = True):
        selected = sorted(
            ({"id": row[id_field], "value": float(row[value_field])} for row in rows),
            key=lambda row: ((-row["value"] if reverse else row["value"]), row["id"]),
        )
        return selected[:5]

    return {
        "node_betweenness_length_top5": leaders(node_rows, "node_id", "betweenness_length"),
        "node_closeness_length_top5": leaders(node_rows, "node_id", "closeness_length"),
        "edge_betweenness_length_top5": leaders(edge_rows, "edge_id", "betweenness_length"),
    }


def _svg_frame(title: str, body: str, *, width: int = 1200, height: int = 680, subtitle: str = "") -> str:
    subtitle_markup = (
        f'<text x="{width / 2:.1f}" y="48" text-anchor="middle" font-family="sans-serif" '
        f'font-size="13" fill="#444">{escape(subtitle)}</text>' if subtitle else ""
    )
    return "".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width / 2:.1f}" y="27" text-anchor="middle" font-family="sans-serif" font-size="20" font-weight="bold">{escape(title)}</text>',
        subtitle_markup, body, '</svg>',
    ])


def _network_comparison_svg(env39: AcceptedEnvironment, env38: AcceptedEnvironment) -> str:
    environments = (("Env39 grid-like geometry", env39), ("Env38 curvilinear geometry", env38))
    spans = []
    for _, accepted in environments:
        min_x, min_y, max_x, max_y = accepted.canonical.edges.total_bounds
        spans.append((float(min_x), float(min_y), float(max_x), float(max_y)))
    panel_width, panel_height = 540.0, 520.0
    scale = min(
        panel_width / max(max_x - min_x for min_x, _, max_x, _ in spans),
        panel_height / max(max_y - min_y for _, min_y, _, max_y in spans),
    )
    markup = []
    for index, ((label, accepted), (min_x, min_y, max_x, max_y)) in enumerate(zip(environments, spans)):
        x0, y0 = 55.0 + index * 600.0, 95.0
        used_w, used_h = (max_x - min_x) * scale, (max_y - min_y) * scale
        ox, oy = x0 + (panel_width - used_w) / 2, y0 + (panel_height - used_h) / 2

        def project(coordinate):
            x, y = coordinate[:2]
            return ox + (float(x) - min_x) * scale, oy + used_h - (float(y) - min_y) * scale

        markup.append(f'<rect x="{x0}" y="{y0}" width="{panel_width}" height="{panel_height}" fill="#fafafa" stroke="#bbb"/>')
        markup.append(f'<text x="{x0 + panel_width / 2}" y="80" text-anchor="middle" font-family="sans-serif" font-size="16">{escape(label)}</text>')
        for geometry in accepted.canonical.edges.geometry:
            points = " ".join(f"{x:.3f},{y:.3f}" for x, y in map(project, geometry.coords))
            markup.append(f'<polyline points="{points}" fill="none" stroke="#35618f" stroke-width="1.7"/>')
        for geometry in accepted.canonical.nodes.geometry:
            x, y = project(geometry.coords[0])
            markup.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="2.2" fill="#b33a3a"/>')
        markup.append(f'<text x="{x0 + panel_width / 2}" y="638" text-anchor="middle" font-family="sans-serif" font-size="12">69 physical streets; X/Y in local units</text>')
    return _svg_frame(
        "Controlled street-network geometry comparison", "".join(markup),
        subtitle="Same graph topology, different spatial realization; equal scale in both panels.",
    )


def _grouped_bar_svg(
    title: str,
    labels: list[str],
    env39_values: list[float],
    env38_values: list[float],
    *,
    y_label: str,
    subtitle: str,
    maximum: float | None = None,
) -> str:
    width, height = 1200, 680
    left, right, top, bottom = 90.0, 35.0, 90.0, 135.0
    plot_w, plot_h = width - left - right, height - top - bottom
    max_value = maximum if maximum is not None else max([*env39_values, *env38_values, 1e-12])
    slot = plot_w / len(labels)
    markup = [
        f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#333"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#333"/>',
    ]
    for i, (label, value39, value38) in enumerate(zip(labels, env39_values, env38_values)):
        center = left + (i + 0.5) * slot
        bar_w = min(28.0, slot * 0.25)
        for offset, value, color in ((-bar_w, value39, "#35618f"), (0.0, value38, "#c05a35")):
            bar_h = plot_h * max(0.0, value) / max_value
            markup.append(f'<rect x="{center + offset:.3f}" y="{top + plot_h - bar_h:.3f}" width="{bar_w:.3f}" height="{bar_h:.3f}" fill="{color}"/>')
        markup.append(f'<text x="{center:.3f}" y="{top + plot_h + 24:.3f}" text-anchor="middle" font-family="sans-serif" font-size="11" transform="rotate(25 {center:.3f} {top + plot_h + 24:.3f})">{escape(label)}</text>')
    markup.extend([
        f'<rect x="{width - 250}" y="66" width="13" height="13" fill="#35618f"/><text x="{width - 231}" y="77" font-family="sans-serif" font-size="12">Env39</text>',
        f'<rect x="{width - 160}" y="66" width="13" height="13" fill="#c05a35"/><text x="{width - 141}" y="77" font-family="sans-serif" font-size="12">Env38</text>',
        f'<text x="20" y="{height / 2}" text-anchor="middle" transform="rotate(-90 20 {height / 2})" font-family="sans-serif" font-size="13">{escape(y_label)}</text>',
    ])
    return _svg_frame(title, "".join(markup), width=width, height=height, subtitle=subtitle)


def _topology_svg(control: dict[str, Any]) -> str:
    rows = [
        ("Nodes", "node_count"), ("Physical edges", "physical_edge_count"),
        ("Components", "connected_component_count"), ("Mean degree", "degree_mean"),
        ("Dead ends", "dead_end_count"), ("Degree-3 nodes", "degree_3_count"),
        ("Degree-4 nodes", "degree_4_count"), ("Cycle rank", "cycle_rank"),
    ]
    markup = [
        '<rect x="210" y="90" width="780" height="480" fill="#fafafa" stroke="#bbb"/>',
        '<text x="520" y="122" text-anchor="middle" font-family="sans-serif" font-size="14" font-weight="bold">Env39</text>',
        '<text x="700" y="122" text-anchor="middle" font-family="sans-serif" font-size="14" font-weight="bold">Env38</text>',
        '<text x="880" y="122" text-anchor="middle" font-family="sans-serif" font-size="14" font-weight="bold">Status</text>',
    ]
    for index, (label, key) in enumerate(rows):
        y = 162 + index * 47
        markup.extend([
            f'<text x="245" y="{y}" font-family="sans-serif" font-size="14">{escape(label)}</text>',
            f'<text x="520" y="{y}" text-anchor="middle" font-family="monospace" font-size="14">{control["env39"][key]}</text>',
            f'<text x="700" y="{y}" text-anchor="middle" font-family="monospace" font-size="14">{control["env38"][key]}</text>',
            f'<text x="880" y="{y}" text-anchor="middle" font-family="sans-serif" font-size="13" fill="#287a3d">CONTROL MATCH</text>',
        ])
    return _svg_frame(
        "Topology-control verification", "".join(markup),
        subtitle="Stable nodes, physical-edge connectivity, and topology signature are identical.",
    )


def _histogram(values: list[float], edges: list[float]) -> list[int]:
    counts = [0 for _ in range(len(edges) - 1)]
    for value in values:
        index = len(counts) - 1 if value == edges[-1] else next(
            i for i in range(len(counts)) if edges[i] <= value < edges[i + 1]
        )
        counts[index] += 1
    return counts


def _circuity_svg(env39: AcceptedEnvironment, env38: AcceptedEnvironment) -> str:
    values = {}
    for accepted in (env39, env38):
        _, rows = _read_csv(
            accepted.run_root / "geometry" / "edge_geometry_metrics.csv",
            f"{accepted.environment} edge geometry",
        )
        values[accepted.environment] = [float(row["circuity"]) for row in rows if row["circuity"]]
    minimum = min(*values["env39"], *values["env38"])
    maximum = max(*values["env39"], *values["env38"])
    width = (maximum - minimum) / 18.0 if maximum > minimum else 1.0
    edges = [minimum + index * width for index in range(19)]
    edges[-1] = maximum
    labels = [f"{(edges[i] + edges[i + 1]) / 2:.3f}" for i in range(18)]
    count39, count38 = _histogram(values["env39"], edges), _histogram(values["env38"], edges)
    return _grouped_bar_svg(
        "Physical-edge circuity comparison", labels, count39, count38,
        y_label="Physical edge count", subtitle="Common bin edges and a common count scale; 69 physical edges per environment.",
    )


def _orientation_svg(title: str, first: list[dict[str, Any]], second: list[dict[str, Any]], subtitle: str) -> str:
    if [(row["bin_start_deg"], row["bin_end_deg"]) for row in first] != [
        (row["bin_start_deg"], row["bin_end_deg"]) for row in second
    ]:
        raise ComparisonError("Orientation histogram bin edges differ.")
    labels = [f'{row["bin_center_deg"]:g}°' for row in first]
    return _grouped_bar_svg(
        title, labels, [float(row["probability"]) for row in first],
        [float(row["probability"]) for row in second], y_label="Probability",
        subtitle=subtitle, maximum=max(
            max(float(row["probability"]) for row in first),
            max(float(row["probability"]) for row in second),
        ),
    )


def _selected_core_svg(rows: tuple[dict[str, Any], ...]) -> str:
    selected_ids = [
        "network_weighted.mean_shortest_path_length", "geometry.total_length",
        "geometry.mean_edge_length", "geometry.mean_edge_circuity", "geometry.network_circuity",
        "orientation.chord_entropy_normalized",
        "orientation.segment_entropy_length_weighted_normalized", "orientation.phi_chord",
        "orientation.phi_segment_length_weighted",
    ]
    indexed = {row["metric_id"]: row for row in rows}
    ratios = [float(indexed[name]["env38_value"]) / float(indexed[name]["env39_value"]) for name in selected_ids]
    return _grouped_bar_svg(
        "Selected core-metric Env38/Env39 ratios",
        [name.split(".")[-1].replace("_", " ") for name in selected_ids],
        [1.0] * len(selected_ids), ratios, y_label="Ratio (Env39 = 1)",
        subtitle="Mixed-unit values are shown only as within-metric ratios; the common reference line is 1.",
        maximum=max([1.0, *ratios]) * 1.08,
    )


def _figure_documents(
    env39: AcceptedEnvironment,
    env38: AcceptedEnvironment,
    control: dict[str, Any],
    rows: tuple[dict[str, Any], ...],
    orientation39: dict[str, Any],
    orientation38: dict[str, Any],
) -> dict[str, str]:
    indexed = {row["metric_id"]: row for row in rows}
    entropy_ids = ["orientation.chord_entropy_normalized", "orientation.segment_entropy_length_weighted_normalized"]
    phi_ids = ["orientation.phi_chord", "orientation.phi_segment_length_weighted"]
    return {
        "01_physical_networks_equal_scale.svg": _network_comparison_svg(env39, env38),
        "02_topology_control_summary.svg": _topology_svg(control),
        "03_edge_circuity_comparison.svg": _circuity_svg(env39, env38),
        "04_chord_orientation_comparison.svg": _orientation_svg(
            "Chord orientation comparison",
            orientation39["chord_histogram"], orientation38["chord_histogram"],
            "Equal-edge axial probabilities; common 36 bins of 5 degrees over [0, 180).",
        ),
        "05_segment_orientation_comparison.svg": _orientation_svg(
            "Segment-length-weighted orientation comparison",
            orientation39["segment_length_weighted_histogram"],
            orientation38["segment_length_weighted_histogram"],
            "Local-length-weighted axial probabilities; common 36 bins of 5 degrees over [0, 180).",
        ),
        "06_normalized_entropy_comparison.svg": _grouped_bar_svg(
            "Normalized orientation entropy",
            ["Chord", "Segments, length-weighted"],
            [float(indexed[name]["env39_value"]) for name in entropy_ids],
            [float(indexed[name]["env38_value"]) for name in entropy_ids],
            y_label="Normalized Shannon entropy", subtitle="Natural-log entropy normalized by ln(36).", maximum=1.0,
        ),
        "07_phi_comparison.svg": _grouped_bar_svg(
            "Fourfold axial orientation order (phi)",
            ["Chord", "Segments, length-weighted"],
            [float(indexed[name]["env39_value"]) for name in phi_ids],
            [float(indexed[name]["env38_value"]) for name in phi_ids],
            y_label="Fourfold order phi", subtitle="Higher values indicate stronger fourfold axial order; no performance claim.", maximum=1.0,
        ),
        "08_selected_core_metric_comparison.svg": _selected_core_svg(rows),
    }


def _family_summaries(rows: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    summaries = {}
    for family in sorted(METRIC_FAMILIES):
        selected = [row for row in rows if row["metric_family"] == family]
        summaries[family] = {
            "metric_count": len(selected),
            "control_match_count": sum(row["comparison_status"] == "CONTROL_MATCH" for row in selected),
            "descriptive_difference_count": sum(row["comparison_status"] == "DESCRIPTIVE_DIFFERENCE" for row in selected),
            "descriptive_match_count": sum(row["comparison_status"] == "DESCRIPTIVE_MATCH" for row in selected),
            "control_failure_count": sum(row["comparison_status"] == "CONTROL_FAILURE" for row in selected),
            "status": "PASS" if all(row["comparison_status"] != "CONTROL_FAILURE" for row in selected) else "FAIL",
        }
    return summaries


def _comparison_pair(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    output = {}
    for name in first:
        left, right = first[name], second[name]
        if isinstance(left, bool) or isinstance(right, bool):
            absolute = signed = relative = None
        else:
            absolute, signed, relative = comparison_arithmetic(float(left), float(right))
        output[name] = {
            "env39_value": left, "env38_value": right,
            "absolute_difference": absolute,
            "signed_difference_env38_minus_env39": signed,
            "relative_difference_percent": relative,
        }
    return output


def _key_findings(rows: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    candidates = [
        row for row in rows
        if row["metric_family"] != "topology_controlled"
        and row["relative_difference_percent"] is not None
    ]
    selected = sorted(candidates, key=lambda row: (-abs(row["relative_difference_percent"]), row["metric_id"]))[:8]
    return [{key: row[key] for key in (
        "metric_id", "metric_label", "metric_family", "env39_value", "env38_value",
        "signed_difference_env38_minus_env39", "relative_difference_percent",
        "interpretation_note",
    )} for row in selected]


def _summary_markdown(summary: dict[str, Any], output_dir: Path, root: Path) -> str:
    rows = {row["metric_id"]: row for row in summary["comparison_metrics"]}
    geometry = summary["detailed_geometry"]
    orientation = summary["detailed_orientation"]
    return "\n".join([
        "# Controlled Env38 versus Env39 street-morphology comparison", "",
        "## Research question", "",
        "Given equal canonical graph topology, how do the accepted curvilinear Env38 and grid-like Env39 realizations differ in geometry-weighted network structure, planar geometry, and axial orientation morphology?", "",
        "## Experimental control", "",
        f"Both networks contain 46 nodes, 69 physical edges, one component, mean degree 3.0, 10 dead ends, 16 degree-3 nodes, 20 degree-4 nodes, and cycle rank 24. Their topology signature is `{summary['topology_control']['topology_signature']}`. This confirms connectivity control, not morphological identity.", "",
        "## Data provenance", "",
        f"- Env39 accepted run: `{summary['runs']['env39']}`",
        f"- Env38 accepted run: `{summary['runs']['env38']}`",
        f"- Env38 Phase 9F reproducibility: 39/39 comparison metrics exact, 19/19 core metrics exact, zero scientific mismatches.", "",
        "## Geometry-weighted network comparison", "",
        f"Mean length-weighted shortest path is {rows['network_weighted.mean_shortest_path_length']['env39_value']} local units in Env39 and {rows['network_weighted.mean_shortest_path_length']['env38_value']} local units in Env38. Hop-based topology remains controlled; the weighted difference reflects changed local geometric edge costs.", "",
        "## Geometric morphology", "",
        f"Total physical-network length is {geometry['total_network_length_local']['env39_value']} local units in Env39 and {geometry['total_network_length_local']['env38_value']} in Env38. Network circuity is {geometry['network_circuity']['env39_value']} and {geometry['network_circuity']['env38_value']}, respectively. LineString length follows the stored path, chord length joins endpoints, edge circuity is their edge-level ratio, and network circuity is the ratio of aggregate lengths.", "",
        "## Orientation morphology", "",
        f"Normalized chord entropy changes from {orientation['chord_orientation_normalized_entropy']['env39_value']} (Env39) to {orientation['chord_orientation_normalized_entropy']['env38_value']} (Env38), while chord phi changes from {orientation['phi_chord']['env39_value']} to {orientation['phi_chord']['env38_value']}. The same axial [0,180), north-zero/east-90 clockwise convention, 36 five-degree bins, natural-log entropy, and fourfold order definition are used.", "",
        "## Key descriptive findings", "",
        *[f"- `{item['metric_id']}`: Env39 {item['env39_value']}; Env38 {item['env38_value']}; signed change {item['signed_difference_env38_minus_env39']}; relative change {item['relative_difference_percent']:.3f}%." for item in summary["largest_descriptive_differences"]], "",
        "## Methodological limitations", "",
        "This is a controlled descriptive comparison of one accepted spatial realization per design. The second Env38 run is a computational reproducibility replicate, not an independent spatial sample. No p-values, hypothesis tests, confidence intervals, or causal/effect-significance claims are supported.", "",
        "## Interpretation constraints", "",
        "GeoGami Local Cartesian coordinates are not longitude/latitude. Distances remain local units and are not metres. Greater entropy, order, circuity, or weighted distance is not inherently better or worse, and behavioral or navigation conclusions require independent wayfinding evidence.", "",
        "## Machine-readable outputs", "",
        f"- `{_display_path(output_dir / 'comparison_metrics.csv', root)}`",
        f"- `{_display_path(output_dir / 'comparison_core_metrics.csv', root)}`",
        f"- `{_display_path(output_dir / 'comparison_summary.json', root)}`",
        f"- `{_display_path(output_dir / 'comparison_methodology.json', root)}`",
        f"- `{_display_path(output_dir / 'comparison_manifest.json', root)}`", "",
    ])


def build_environment_comparison(
    *,
    project_root: Path = PROJECT_ROOT,
    output_root: Path | None = None,
    comparison_id: str | None = None,
    phase_start_clean: bool | None = None,
    phase_start_commit: str | None = None,
) -> ComparisonResult:
    """Verify accepted runs and publish a versioned controlled comparison package."""
    root = Path(project_root).resolve()
    env39 = _resolve_accepted("env39", root)
    env38 = _resolve_accepted("env38", root)
    phase9f = _phase9f_verification(env38)
    control = _verify_topology_control(env38, env39)
    rows = compare_metric_rows(env39.comparison_rows, env38.comparison_rows)
    if len(rows) != 39:
        raise ComparisonError("Comparison requires exactly 39 aligned metric IDs.")
    core_rows = tuple(row for row in rows if row["metric_tier"] == "core")
    if len(core_rows) != 19:
        raise ComparisonError("Comparison requires exactly 19 aligned core metrics.")
    if {row["metric_family"] for row in rows} != set(METRIC_FAMILIES):
        raise ComparisonError("Metric-family taxonomy differs from the accepted four families.")
    if {row["comparison_role"] for row in rows} != set(COMPARISON_ROLES):
        raise ComparisonError("Comparison-role taxonomy differs from the accepted roles.")

    geometry39, geometry38 = _detailed_geometry(env39), _detailed_geometry(env38)
    orientation_metrics39, orientation39 = _orientation(env39)
    orientation_metrics38, orientation38 = _orientation(env38)
    bin_edges39 = [(row["bin_start_deg"], row["bin_end_deg"]) for row in orientation39["chord_histogram"]]
    bin_edges38 = [(row["bin_start_deg"], row["bin_end_deg"]) for row in orientation38["chord_histogram"]]
    segment_edges39 = [(row["bin_start_deg"], row["bin_end_deg"]) for row in orientation39["segment_length_weighted_histogram"]]
    segment_edges38 = [(row["bin_start_deg"], row["bin_end_deg"]) for row in orientation38["segment_length_weighted_histogram"]]
    if bin_edges39 != bin_edges38 or segment_edges39 != segment_edges38 or bin_edges39 != segment_edges39:
        raise ComparisonError("Accepted orientation distributions do not use identical bin edges.")

    configuration_path = root / "config" / "metrics.yaml"
    registry_path = root / "config" / "environments.yaml"
    current_git = git_provenance(root)
    if phase_start_clean is None:
        phase_start_clean = not current_git["dirty"]
    if phase_start_commit is None:
        phase_start_commit = current_git["commit_sha"]
    if phase_start_commit != current_git["commit_sha"]:
        raise ComparisonError("Recorded Phase 9G clean-start commit differs from current HEAD.")
    identity_payload = "|".join([
        env39.run_id, env38.run_id, sha256_file(configuration_path), sha256_file(registry_path),
    ])
    import hashlib
    identity_digest = hashlib.sha256(identity_payload.encode("utf-8")).hexdigest()[:12]
    created_at = datetime.now(timezone.utc)
    selected_id = comparison_id or (
        f"env38_vs_env39_{created_at.strftime('%Y%m%dT%H%M%S%fZ')}_{identity_digest}"
    )
    if not re.fullmatch(r"env38_vs_env39_[A-Za-z0-9_-]+", selected_id):
        raise ComparisonError("Invalid comparison_id; use env38_vs_env39_ followed by safe characters.")
    chosen_root = Path(output_root).resolve() if output_root else root / "results" / "comparison" / "env38_vs_env39"
    final_dir = chosen_root / selected_id
    candidate = chosen_root / f".{selected_id}.tmp"
    if final_dir.exists() or candidate.exists():
        raise ComparisonError(f"Comparison output already exists: {final_dir}")

    source_before = _source_snapshot(env38, env39, root)
    figures = _figure_documents(env39, env38, control, rows, orientation39, orientation38)
    family_summaries = _family_summaries(rows)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "comparison_id": selected_id,
        "runs": {"env39": env39.run_id, "env38": env38.run_id},
        "research_question": "Given equal canonical topology, how do Env38 and Env39 differ in geometry-weighted network structure, geometric morphology, and orientation morphology?",
        "topology_control": control,
        "comparison_metric_count": len(rows),
        "core_metric_count": len(core_rows),
        "metric_family_summaries": family_summaries,
        "comparison_metrics": list(rows),
        "detailed_geometry": _comparison_pair(geometry39, geometry38),
        "detailed_orientation": _comparison_pair(orientation_metrics39, orientation_metrics38),
        "orientation_distributions": {
            "methodology": {
                "domain_degrees": "[0, 180)", "north_degrees": 0, "east_degrees": 90,
                "rotation": "clockwise", "bin_count": 36, "bin_width_degrees": 5.0,
                "entropy_logarithm": "natural", "order_symmetry": 4,
                "identical_bin_edges": True,
            },
            "bin_edges": [{"start_deg": start, "end_deg": end} for start, end in bin_edges39],
            "chord": {"env39": orientation39["chord_histogram"], "env38": orientation38["chord_histogram"]},
            "segment_length_weighted": {"env39": orientation39["segment_length_weighted_histogram"], "env38": orientation38["segment_length_weighted_histogram"]},
        },
        "length_weighted_centrality_leaders": {
            "env39": _centrality_leaders(env39), "env38": _centrality_leaders(env38),
            "interpretation": "Stored accepted length-weighted centrality rankings are compared by stable node and physical-edge IDs; no centrality is recalculated here.",
        },
        "largest_descriptive_differences": _key_findings(rows),
        "phase9f_reproducibility": phase9f,
        "inferential_statistics": {"performed": False, "reason": "One accepted spatial realization per design; the second Env38 run is a computational replicate."},
        "final_comparison_status": "PASS",
    }
    methodology = {
        "schema_version": SCHEMA_VERSION,
        "comparison_type": "controlled_descriptive_street_morphology",
        "join_key": "metric_id",
        "signed_difference": "Env38 - Env39",
        "relative_difference_percent": "100 * (Env38 - Env39) / abs(Env39), only for nonzero scientifically appropriate geometry- or orientation-sensitive metrics; otherwise null",
        "topology_control_status": {"equal": "CONTROL_MATCH", "different": "CONTROL_FAILURE"},
        "metric_families": sorted(METRIC_FAMILIES),
        "comparison_roles": sorted(COMPARISON_ROLES),
        "orientation_methodology": summary["orientation_distributions"]["methodology"],
        "local_coordinate_limitation": "GeoGami Local Cartesian coordinates are not longitude/latitude. Lengths remain local units, not metres. No WGS84 reprojection, geographic bearing, great-circle distance, or geographic nearest-node operation is used.",
        "line_geometry_definitions": {
            "linestring_length": "Stored physical-edge LineString path length.",
            "endpoint_chord": "Euclidean local-coordinate distance between physical-edge endpoints.",
            "edge_circuity": "LineString length divided by endpoint chord for an eligible edge.",
            "network_circuity": "Sum of eligible LineString lengths divided by sum of corresponding chords.",
        },
        "inferential_statistics": {"performed": False, "prohibited_methods": ["p-values", "t-tests", "ANOVA", "confidence intervals", "effect-significance claims"]},
        "interpretation_constraints": [
            "No environment is objectively better or worse.",
            "Orientation entropy or phi is not universally good or bad.",
            "Behavioral and navigation conclusions require independent wayfinding evidence.",
        ],
        "figure_contract": {
            "physical_networks": "69 physical edges per environment; reciprocal OSMnx arcs are not plotted.",
            "direct_comparisons": "Common bin edges and common scales are used.",
            "format": "deterministic headless-safe SVG",
        },
    }

    try:
        (candidate / "figures").mkdir(parents=True, exist_ok=False)
        _write_csv(candidate / "comparison_metrics.csv", OUTPUT_FIELDS, rows)
        _write_csv(candidate / "comparison_core_metrics.csv", OUTPUT_FIELDS, core_rows)
        write_json(candidate / "comparison_summary.json", summary)
        write_json(candidate / "comparison_methodology.json", methodology)
        for name, document in figures.items():
            (candidate / "figures" / name).write_text(document, encoding="utf-8", newline="\n")
        markdown = _summary_markdown(summary, final_dir, root)
        (candidate / "env38_vs_env39_summary.md").write_text(markdown, encoding="utf-8", newline="\n")

        source_after = _source_snapshot(env38, env39, root)
        if source_before != source_after:
            raise ComparisonError("Accepted environment-specific source artifacts changed during comparison publication.")
        artifact_paths = sorted(
            path for path in candidate.rglob("*")
            if path.is_file() and path.name != "comparison_manifest.json"
        )
        artifacts = {
            path.relative_to(candidate).as_posix(): {
                "path": _display_path(final_dir / path.relative_to(candidate), root),
                "file_sha256": sha256_file(path),
            }
            for path in artifact_paths
        }
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "comparison_id": selected_id,
            "created_at_utc": created_at.isoformat().replace("+00:00", "Z"),
            "environments": {
                "env39": {
                    "run_id": env39.run_id,
                    "canonical_path": _display_path(env39.canonical_path, root),
                    "scientific_content_signature": env39.scientific_content_signature,
                    "topology_signature": env39.topology_signature,
                    "integrated_manifest_path": _display_path(env39.integrated_manifest_path, root),
                    "integrated_manifest_sha256": sha256_file(env39.integrated_manifest_path),
                    "comparison_ready_metrics_path": _display_path(env39.comparison_metrics_path, root),
                    "comparison_ready_metrics_sha256": sha256_file(env39.comparison_metrics_path),
                },
                "env38": {
                    "run_id": env38.run_id,
                    "canonical_path": _display_path(env38.canonical_path, root),
                    "scientific_content_signature": env38.scientific_content_signature,
                    "topology_signature": env38.topology_signature,
                    "integrated_manifest_path": _display_path(env38.integrated_manifest_path, root),
                    "integrated_manifest_sha256": sha256_file(env38.integrated_manifest_path),
                    "comparison_ready_metrics_path": _display_path(env38.comparison_metrics_path, root),
                    "comparison_ready_metrics_sha256": sha256_file(env38.comparison_metrics_path),
                },
            },
            "topology_control": control,
            "phase9f_reproducibility": phase9f,
            "configuration": {
                "metrics": {"path": _display_path(configuration_path, root), "file_sha256": sha256_file(configuration_path)},
                "environment_registry": {"path": _display_path(registry_path, root), "file_sha256": sha256_file(registry_path)},
            },
            "software_environment": software_environment(),
            "git_provenance": {
                "phase_start": {"branch": current_git["branch"], "commit_sha": phase_start_commit, "clean": bool(phase_start_clean)},
                "generation": current_git,
            },
            "accepted_sources_preserved": {"before": source_before, "after": source_after, "status": "PASS"},
            "metric_counts": {"comparison": len(rows), "core": len(core_rows)},
            "artifacts": artifacts,
            "final_comparison_status": "PASS",
        }
        write_json(candidate / "comparison_manifest.json", manifest)
        chosen_root.mkdir(parents=True, exist_ok=True)
        candidate.replace(final_dir)
    except Exception:
        if candidate.is_dir():
            shutil.rmtree(candidate)
        raise
    return ComparisonResult(
        selected_id, final_dir, final_dir / "comparison_manifest.json", manifest,
        rows, core_rows, summary,
    )
