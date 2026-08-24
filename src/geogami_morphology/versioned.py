"""Versioned, self-contained canonical-run publication and provenance."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
from typing import Any, Iterable
import uuid

import geopandas as gpd
from shapely.wkt import dumps as wkt_dumps

from .canonical import DEFAULT_ENDPOINT_TOLERANCE, PipelineError, run_preserve_topology
from .identity import (
    SCIENTIFIC_CONTENT_SCHEMA_VERSION,
    SCIENTIFIC_CONTENT_SERIALIZATION,
    network_identity,
    scientific_content_signature,
)
from .io import atomic_publish, read_network, sha256_file, write_json
from .validation import TOPOLOGY_SERIALIZATION


MANIFEST_SCHEMA_VERSION = "1.0.0"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_NAMES = {
    "osmnx": "osmnx",
    "networkx": "networkx",
    "geopandas": "geopandas",
    "shapely": "shapely",
    "pandas": "pandas",
    "numpy": "numpy",
    "scipy": "scipy",
    "matplotlib": "matplotlib",
    "pyproj": "pyproj",
    "pyogrio": "pyogrio",
    "fiona": "fiona",
}


@dataclass(frozen=True)
class VersionedRunResult:
    environment: str
    run_id: str
    run_dir: Path
    canonical_path: Path
    manifest_path: Path
    validation_path: Path
    latest_path: Path
    manifest: dict[str, Any]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _environment_slug(environment: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", environment.strip().lower()).strip("-")
    if not slug:
        raise ValueError("Environment must contain at least one filesystem-safe letter or digit.")
    return slug


def _run_id(environment: str, started: datetime, scientific_signature: str) -> str:
    timestamp = started.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{_environment_slug(environment)}_{timestamp}_{scientific_signature[:12].lower()}"


def _failure_id(environment: str, started: datetime) -> str:
    timestamp = started.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{_environment_slug(environment)}_{timestamp}_failed"


def _display_path(path: Path, project_root: Path) -> str:
    resolved = Path(path).resolve(strict=False)
    root = Path(project_root).resolve(strict=False)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved.as_posix()


def _git(*arguments: str, project_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.rstrip("\r\n")


def git_provenance(project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    status = _git("status", "--porcelain", "--untracked-files=all", project_root=project_root)
    status_lines = [] if not status else status.splitlines()
    branch = _git("branch", "--show-current", project_root=project_root) or None
    return {
        "repository": Path(project_root).resolve().name,
        "repository_root": _display_path(project_root, project_root),
        "remote_origin": _git("config", "--get", "remote.origin.url", project_root=project_root),
        "branch": branch,
        "commit_sha": _git("rev-parse", "HEAD", project_root=project_root),
        "dirty": bool(status_lines),
        "changed_paths": [line[3:].replace("\\", "/") for line in status_lines],
    }


def software_environment() -> dict[str, Any]:
    versions: dict[str, str | None] = {}
    for label, distribution in PACKAGE_NAMES.items():
        try:
            versions[label] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[label] = None
    gdal_version: str | None = None
    try:
        import pyogrio

        raw_version = getattr(pyogrio, "__gdal_version__", None)
        if raw_version:
            gdal_version = ".".join(str(component) for component in raw_version)
    except ImportError:
        pass
    versions["gdal"] = gdal_version
    return {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "operating_system": platform.platform(),
        "packages": versions,
    }


def _configuration_records(paths: Iterable[Path], project_root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        path = Path(path)
        records.append(
            {
                "path": _display_path(path, project_root),
                "file_sha256": sha256_file(path) if path.is_file() else None,
                "available": path.is_file(),
            }
        )
    return records


def _layer_identity(path: Path, project_root: Path) -> dict[str, Any]:
    nodes, edges = read_network(path)
    signature, _ = scientific_content_signature(nodes, edges)
    return {
        "path": _display_path(path, project_root),
        "file_sha256": sha256_file(path),
        "scientific_content_signature": signature,
        "layer_names": gpd.list_layers(path)["name"].astype(str).tolist(),
        "feature_counts": {"nodes": len(nodes), "edges": len(edges)},
    }


def _write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            count += 1
    return count


def _canonical_csvs(run_dir: Path, nodes: gpd.GeoDataFrame, edges: gpd.GeoDataFrame) -> dict[str, int]:
    node_rows = []
    for row in nodes.sort_values("node_id", kind="stable").itertuples():
        node_rows.append(
            {
                "node_id": str(row.node_id),
                "x": repr(float(row.geometry.x)),
                "y": repr(float(row.geometry.y)),
                "degree": int(row.degree),
                "geometry_wkt": wkt_dumps(row.geometry, rounding_precision=-1, trim=True),
            }
        )
    edge_rows = []
    ordered = edges.assign(__edge_id=edges.edge_id.astype(str), __key=edges.key.map(int)).sort_values(
        ["__edge_id", "u", "v", "__key"], kind="stable"
    )
    for row in ordered.itertuples():
        edge_rows.append(
            {
                "edge_id": str(row.edge_id),
                "u": str(row.u),
                "v": str(row.v),
                "key": int(row.key),
                "length_local": repr(float(row.geometry.length)),
                "vertex_count": len(row.geometry.coords),
                "geometry_wkt": wkt_dumps(row.geometry, rounding_precision=-1, trim=True),
            }
        )
    return {
        "canonical_nodes.csv": _write_csv(
            run_dir / "canonical_nodes.csv",
            ["node_id", "x", "y", "degree", "geometry_wkt"],
            node_rows,
        ),
        "canonical_edges.csv": _write_csv(
            run_dir / "canonical_edges.csv",
            ["edge_id", "u", "v", "key", "length_local", "vertex_count", "geometry_wkt"],
            edge_rows,
        ),
    }


def _adjustment_rows(adjustments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "edge_id": item["edge_id"],
            "endpoint": item["endpoint"],
            "node_id": item["node_id"],
            "from_x": repr(float(item["from"][0])),
            "from_y": repr(float(item["from"][1])),
            "to_x": repr(float(item["to"][0])),
            "to_y": repr(float(item["to"][1])),
            "distance_local_units": repr(float(item["distance_local_units"])),
        }
        for item in adjustments
    ]


def _write_failure_summary(failed_dir: Path, error: Exception) -> None:
    failed_dir.mkdir(parents=True, exist_ok=True)
    if not (failed_dir / "failure.json").exists():
        write_json(
            failed_dir / "failure.json",
            {"final_result": "FAIL", "message": str(error), "successful_publication": False},
        )


def _atomic_latest(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate = path.with_name(f".{path.name}.candidate-{uuid.uuid4().hex}")
    try:
        write_json(candidate, value)
        with candidate.open("r", encoding="utf-8") as stream:
            if json.load(stream) != value:
                raise RuntimeError("Latest-pointer candidate did not round-trip exactly.")
        atomic_publish(candidate, path)
    finally:
        try:
            if candidate.exists():
                candidate.unlink()
        except OSError:
            # A stale, explicitly named candidate is safer than reporting a
            # failed publication after the atomic replacement already passed.
            pass


def run_versioned_canonical(
    environment: str,
    input_path: Path,
    reference_path: Path,
    runs_root: Path,
    *,
    latest_path: Path | None = None,
    endpoint_tolerance: float = DEFAULT_ENDPOINT_TOLERANCE,
    project_root: Path = PROJECT_ROOT,
    configuration_paths: Iterable[Path] | None = None,
) -> VersionedRunResult:
    input_path, reference_path, runs_root = map(Path, (input_path, reference_path, runs_root))
    project_root = Path(project_root)
    latest_path = Path(latest_path) if latest_path else runs_root.parent / "latest.json"
    configurations = list(configuration_paths) if configuration_paths is not None else [project_root / "environment.yml"]
    started = _utc_now()
    git_state = git_provenance(project_root)
    software = software_environment()
    failure_id = _failure_id(environment, started)
    failed_dir = runs_root / "failed" / failure_id
    scratch_dir = runs_root / ".scratch" / f"{failure_id}-{uuid.uuid4().hex}"
    scratch_dir.mkdir(parents=True, exist_ok=False)
    canonical_scratch = scratch_dir / f"{_environment_slug(environment)}_canonical.gpkg"
    internal_qa = scratch_dir / "_phase7b_internal"
    final_dir: Path | None = None
    latest_updated = False
    try:
        phase7b = run_preserve_topology(
            environment,
            input_path,
            reference_path,
            canonical_scratch,
            diagnostics_dir=failed_dir,
            endpoint_tolerance=endpoint_tolerance,
        )
        # The Phase 7B report is now captured in memory; successful run artifacts
        # use the Phase 7C names and schema below.
        if failed_dir.exists():
            shutil.move(str(failed_dir), str(internal_qa))

        nodes, edges = read_network(canonical_scratch)
        identity = network_identity(nodes, edges)
        scientific_signature = identity["scientific_content_signature"]
        topology_digest = identity["topology_signature"]
        run_id = _run_id(environment, started, scientific_signature)
        final_dir = runs_root / run_id
        if final_dir.exists():
            raise RuntimeError(f"Versioned run directory already exists: {final_dir}")
        canonical_name = f"{_environment_slug(environment)}_canonical.gpkg"
        if canonical_scratch.name != canonical_name:
            raise AssertionError("Unexpected canonical candidate name.")

        csv_counts = _canonical_csvs(scratch_dir, nodes, edges)
        adjustment_count = _write_csv(
            scratch_dir / "endpoint_adjustments.csv",
            ["edge_id", "endpoint", "node_id", "from_x", "from_y", "to_x", "to_y", "distance_local_units"],
            _adjustment_rows(phase7b.report["endpoint_corrections"]),
        )
        diagnostic_count = _write_csv(
            scratch_dir / "diagnostics.csv",
            ["domain", "issue_type", "severity", "message", "node_id", "edge_id", "edge_id_2", "distance_local_units"],
            phase7b.report["diagnostics"],
        )
        (scratch_dir / "topology_signature.txt").write_text(topology_digest + "\n", encoding="utf-8", newline="\n")
        (scratch_dir / "scientific_content_signature.txt").write_text(scientific_signature + "\n", encoding="utf-8", newline="\n")
        if internal_qa.exists():
            shutil.rmtree(internal_qa)

        candidate_file_sha = sha256_file(canonical_scratch)
        reference_nodes, reference_edges = read_network(reference_path)
        reference_scientific, _ = scientific_content_signature(reference_nodes, reference_edges)
        reference_identity = network_identity(reference_nodes, reference_edges)
        input_identity = _layer_identity(input_path, project_root)
        finished = _utc_now()
        final_canonical = final_dir / canonical_name
        final_manifest = final_dir / "canonical_manifest.json"
        final_validation = final_dir / "canonical_validation.json"
        validation = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "phase": "7C",
            "topology_checks": phase7b.report["topology"],
            "geometry_checks": {
                "geometry_error_count": phase7b.report["error_counts"]["geometry"],
                "diagnostics": phase7b.report["diagnostics"],
            },
            "signature_checks": {
                "topology_signature_recalculated": topology_digest == phase7b.report["topology"]["topology_signature"],
                "scientific_content_signature_recalculated": scientific_signature == scientific_content_signature(nodes, edges)[0],
                "canonical_file_sha256_recalculated": candidate_file_sha == sha256_file(canonical_scratch),
            },
            "publication_checks": {
                "phase7b_validation_pass": phase7b.report["final_result"] == "PASS",
                "required_artifacts_exist": False,
                "manifest_created": False,
                "latest_update_eligible": False,
            },
            "topology_status": "PASS" if phase7b.report["error_counts"]["topology"] == 0 else "FAIL",
            "geometry_status": "PASS" if phase7b.report["error_counts"]["geometry"] == 0 else "FAIL",
            "final_result": "PASS",
        }
        write_json(scratch_dir / "canonical_validation.json", validation)

        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "identity": {
                "environment": environment,
                "run_id": run_id,
                "build_mode": "preserve-topology",
            },
            "time": {
                "build_started_at_utc": _utc_text(started),
                "build_finished_at_utc": _utc_text(finished),
            },
            "input_editable_network": input_identity,
            "reference_canonical": {
                **_layer_identity(reference_path, project_root),
                "scientific_content_signature": reference_scientific,
                "topology_signature": reference_identity["topology_signature"],
            },
            "published_canonical": {
                "path": _display_path(final_canonical, project_root),
                "file_sha256": candidate_file_sha,
                "scientific_content_signature": scientific_signature,
                "topology_signature": topology_digest,
            },
            "git_provenance": git_state,
            "software_environment": software,
            "network_identity": identity,
            "validation": {
                "topology_status": validation["topology_status"],
                "geometry_status": validation["geometry_status"],
                "final_status": "PASS",
                "endpoint_adjustment_count": adjustment_count,
                "diagnostic_error_count": sum(item.get("severity") == "error" for item in phase7b.report["diagnostics"]),
                "diagnostic_warning_count": sum(item.get("severity") == "warning" for item in phase7b.report["diagnostics"]),
            },
            "configuration": {
                "canonicalization_mode": "preserve-topology",
                "endpoint_correction_tolerance_local_units": endpoint_tolerance,
                "cli_options": {
                    "environment": environment,
                    "input": _display_path(input_path, project_root),
                    "reference_canonical": _display_path(reference_path, project_root),
                    "runs_root": _display_path(runs_root, project_root),
                    "mode": "preserve-topology",
                },
                "configuration_files": _configuration_records(configurations, project_root),
            },
            "signature_methodology": {
                "file_sha256": "SHA-256 of exact file bytes",
                "scientific_content_schema_version": SCIENTIFIC_CONTENT_SCHEMA_VERSION,
                "scientific_content_serialization": SCIENTIFIC_CONTENT_SERIALIZATION,
                "scientific_content_included_fields": [
                    "CRS WKT",
                    "node_id and exact ordered Point coordinates",
                    "edge_id, u, v, key and exact ordered LineString coordinates",
                ],
                "scientific_content_excluded_fields": [
                    "GeoPackage FIDs and metadata",
                    "derived x, y, degree, length_local and vertex_count",
                    "source_fids and source_fids_ordered provenance",
                ],
                "topology_serialization": TOPOLOGY_SERIALIZATION,
            },
            "artifacts": {
                "canonical_geopackage": _display_path(final_canonical, project_root),
                "canonical_manifest": _display_path(final_manifest, project_root),
                "canonical_validation": _display_path(final_validation, project_root),
                "canonical_nodes_csv": {"path": _display_path(final_dir / "canonical_nodes.csv", project_root), "row_count": csv_counts["canonical_nodes.csv"]},
                "canonical_edges_csv": {"path": _display_path(final_dir / "canonical_edges.csv", project_root), "row_count": csv_counts["canonical_edges.csv"]},
                "topology_signature": _display_path(final_dir / "topology_signature.txt", project_root),
                "scientific_content_signature": _display_path(final_dir / "scientific_content_signature.txt", project_root),
                "endpoint_adjustments_csv": {"path": _display_path(final_dir / "endpoint_adjustments.csv", project_root), "row_count": adjustment_count},
                "diagnostics_csv": {"path": _display_path(final_dir / "diagnostics.csv", project_root), "row_count": diagnostic_count},
                "zero_row_table_convention": "CSV is present with a valid header even when row_count is zero",
            },
        }
        write_json(scratch_dir / "canonical_manifest.json", manifest)

        required_names = {
            canonical_name,
            "canonical_manifest.json",
            "canonical_validation.json",
            "canonical_nodes.csv",
            "canonical_edges.csv",
            "topology_signature.txt",
            "scientific_content_signature.txt",
            "endpoint_adjustments.csv",
            "diagnostics.csv",
        }
        checks = {
            "required_artifacts_exist": all((scratch_dir / name).is_file() for name in required_names),
            "canonical_file_sha256_verified": sha256_file(canonical_scratch) == candidate_file_sha,
            "scientific_content_signature_verified": scientific_content_signature(*read_network(canonical_scratch))[0] == scientific_signature,
            "topology_signature_verified": network_identity(*read_network(canonical_scratch))["topology_signature"] == topology_digest,
            "manifest_round_trip_verified": json.loads((scratch_dir / "canonical_manifest.json").read_text(encoding="utf-8")) == manifest,
        }
        if not all(checks.values()):
            raise RuntimeError(f"Versioned artifact verification failed: {checks}")
        validation["publication_checks"].update(
            {"required_artifacts_exist": True, "manifest_created": True, "latest_update_eligible": True}
        )
        validation["publication_checks"].update(checks)
        write_json(scratch_dir / "canonical_validation.json", validation)

        final_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(scratch_dir, final_dir)
        latest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "environment": environment,
            "run_id": run_id,
            "canonical_path": _display_path(final_canonical, project_root),
            "manifest_path": _display_path(final_manifest, project_root),
            "canonical_file_sha256": candidate_file_sha,
            "scientific_content_signature": scientific_signature,
            "topology_signature": topology_digest,
            "created_at_utc": _utc_text(finished),
        }
        if not final_canonical.is_file() or not final_manifest.is_file():
            raise RuntimeError("Published run is incomplete; latest pointer was not updated.")
        if sha256_file(final_canonical) != candidate_file_sha:
            raise RuntimeError("Published canonical file SHA-256 changed; latest pointer was not updated.")
        _atomic_latest(latest_path, latest)
        latest_updated = True
        for empty_directory in (runs_root / ".scratch", runs_root / "failed"):
            try:
                empty_directory.rmdir()
            except OSError:
                pass
        return VersionedRunResult(
            environment,
            run_id,
            final_dir,
            final_canonical,
            final_manifest,
            final_validation,
            latest_path,
            manifest,
        )
    except Exception as exc:
        if scratch_dir.exists():
            shutil.rmtree(scratch_dir)
        if final_dir is not None and final_dir.exists() and not latest_updated:
            shutil.rmtree(final_dir)
        try:
            (runs_root / ".scratch").rmdir()
        except OSError:
            pass
        _write_failure_summary(failed_dir, exc)
        if isinstance(exc, PipelineError):
            raise
        raise PipelineError(f"Versioned canonical publication failed: {exc}") from exc
