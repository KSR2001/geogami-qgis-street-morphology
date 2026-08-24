"""Create the QGIS-editable Env38 Phase 6A template from canonical Env39."""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys

import geopandas as gpd

import validate_env38_topology as validator


def _stabilize_gpkg(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE gpkg_contents SET last_change='2000-01-01T00:00:00.000Z'")
        connection.commit()
        connection.execute("VACUUM")


def create_template(
    canonical_path: Path = validator.DEFAULT_CANONICAL,
    output_path: Path = validator.DEFAULT_INPUT,
) -> Path:
    hash_before = validator.sha256_file(canonical_path)
    if hash_before != validator.EXPECTED_CANONICAL_SHA256:
        raise ValueError(
            f"Frozen canonical SHA-256 mismatch before template creation: {hash_before}"
        )
    nodes, edges = validator._read_layers(canonical_path)
    if nodes.crs is None or edges.crs is None or nodes.crs != edges.crs or nodes.crs.is_geographic:
        raise ValueError("Canonical layers do not share the required local Cartesian CRS.")
    nodes = nodes.copy()
    edges = edges.copy()
    nodes["environment"] = "env38"
    edges["environment"] = "env38"
    nodes["geometry_status"] = "template"
    edges["geometry_status"] = "template"
    nodes["template_source"] = "canonical_env39_phase5"
    edges["template_source"] = "canonical_env39_phase5"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    nodes.to_file(output_path, layer="nodes", driver="GPKG", index=False)
    edges.to_file(output_path, layer="edges", driver="GPKG", mode="a", index=False)
    _stabilize_gpkg(output_path)
    hash_after = validator.sha256_file(canonical_path)
    if hash_after != hash_before:
        raise RuntimeError("Frozen canonical Env39 changed while creating the Env38 template.")
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical", type=Path, default=validator.DEFAULT_CANONICAL)
    parser.add_argument("--output", type=Path, default=validator.DEFAULT_INPUT)
    parser.add_argument("--results-dir", type=Path, default=validator.DEFAULT_RESULTS_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    hash_before = validator.sha256_file(args.canonical)
    path = create_template(args.canonical, args.output)
    result = validator.validate_candidate(path, args.canonical)
    reports = validator.write_reports(result, args.results_dir)
    hash_after = validator.sha256_file(args.canonical)
    if result.report["final_result"] != "PASS":
        raise RuntimeError("New Env38 working template did not pass Phase 6A validation.")
    print(f"Canonical Env39 SHA-256 before/after: {hash_before} / {hash_after}")
    print(f"Env38 template: {path}")
    print(f"Nodes / edges: {result.report['candidate']['node_count']} / {result.report['candidate']['edge_count']}")
    print(f"Topology signature: {result.report['topology_control']['topology_signature_sha256']}")
    print(f"TOPOLOGY CONTROL: {result.report['topology_control_status']}")
    print(f"GEOMETRIC REALIZATION QA: {result.report['geometric_realization_qa_status']}")
    for name, report_path in reports.items(): print(f"{name}: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
