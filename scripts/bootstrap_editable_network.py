"""Create a QGIS-editable working copy from a validated canonical GeoPackage."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from geogami_morphology.identity import (
    SCIENTIFIC_CONTENT_SCHEMA_VERSION,
    SCIENTIFIC_CONTENT_SERIALIZATION,
    network_identity,
)
from geogami_morphology.io import read_network, sha256_file, sqlite_integrity_check


def _portable(path: Path) -> str:
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(PROJECT_ROOT):
        raise ValueError(f"Bootstrap paths must remain inside the repository: {path}")
    return resolved.relative_to(PROJECT_ROOT).as_posix()


def _exact_scientific_equality(source_frames, editable_frames) -> None:
    source_nodes, source_edges = source_frames
    editable_nodes, editable_edges = editable_frames
    if source_nodes.crs != editable_nodes.crs or source_edges.crs != editable_edges.crs:
        raise RuntimeError("Editable bootstrap did not preserve both layer CRS values exactly.")
    if source_nodes["node_id"].astype(str).tolist() != editable_nodes["node_id"].astype(str).tolist():
        raise RuntimeError("Editable bootstrap did not preserve node IDs and ordering exactly.")
    fields = ["edge_id", "u", "v", "key"]
    for field in fields:
        if source_edges[field].tolist() != editable_edges[field].tolist():
            raise RuntimeError(f"Editable bootstrap did not preserve edge field {field!r} exactly.")
    if source_nodes.geometry.to_wkb().tolist() != editable_nodes.geometry.to_wkb().tolist():
        raise RuntimeError("Editable bootstrap did not preserve Point geometries exactly.")
    if source_edges.geometry.to_wkb().tolist() != editable_edges.geometry.to_wkb().tolist():
        raise RuntimeError("Editable bootstrap did not preserve LineString geometries and vertices exactly.")


def bootstrap(
    source: Path,
    output: Path,
    manifest: Path | None = None,
    expected_source_sha256: str | None = None,
) -> dict[str, str]:
    source, output = Path(source), Path(output)
    manifest = Path(manifest) if manifest else output.with_name(f"{output.stem}_bootstrap.json")
    if output.exists() or manifest.exists():
        raise FileExistsError("Editable output or bootstrap manifest already exists; refusing to overwrite possibly edited data.")
    source = source.resolve()
    output = output.resolve()
    manifest = manifest.resolve()
    _portable(source); _portable(output); _portable(manifest)
    source_frames = read_network(source)
    source_hash = sha256_file(source)
    if expected_source_sha256 and source_hash != expected_source_sha256.upper():
        raise RuntimeError(
            f"Source SHA-256 mismatch: expected {expected_source_sha256.upper()}, observed {source_hash}."
        )
    source_identity = network_identity(*source_frames)
    output.parent.mkdir(parents=True, exist_ok=True)
    candidate = output.with_name(f".{output.stem}.bootstrap-{uuid.uuid4().hex}.gpkg")
    try:
        shutil.copy2(source, candidate)
        output_hash = sha256_file(candidate)
        if output_hash != source_hash:
            raise RuntimeError("Editable bootstrap copy is not byte-identical to its source.")
        sqlite_integrity_check(candidate)
        editable_frames = read_network(candidate)
        _exact_scientific_equality(source_frames, editable_frames)
        editable_identity = network_identity(*editable_frames)
        if editable_identity != source_identity:
            raise RuntimeError("Editable bootstrap scientific identity differs from its source.")
        if sha256_file(source) != source_hash:
            raise RuntimeError("Bootstrap source changed during verification.")
        os.replace(candidate, output)
    finally:
        candidate.unlink(missing_ok=True)
    document = {
        "schema_version": 1,
        "purpose": "QGIS-editable source network; never treat this file as generated canonical output",
        "creation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source_path": _portable(source),
        "source_sha256": source_hash,
        "editable_path": _portable(output),
        "editable_sha256": output_hash,
        "layers": ["nodes", "edges"],
        "node_count": source_identity["node_count"],
        "edge_count": source_identity["physical_edge_count"],
        "component_count": source_identity["component_count"],
        "degree_distribution": source_identity["degree_distribution"],
        "topology_signature": source_identity["topology_signature"],
        "crs": source_identity["crs"],
        "crs_wkt": source_identity["crs_wkt"],
        "scientific_identity": {
            "schema_version": SCIENTIFIC_CONTENT_SCHEMA_VERSION,
            "serialization": SCIENTIFIC_CONTENT_SERIALIZATION,
            "scientific_content_signature_sha256": source_identity["scientific_content_signature"],
            "preserved_exactly": [
                "node IDs",
                "edge IDs",
                "u/v/key",
                "Point coordinates",
                "ordered LineString coordinates including internal vertices",
                "CRS",
            ],
        },
        "bootstrap_method": "byte-for-byte copy to a temporary candidate; SQLite, hash, exact geometry, CRS, topology, and scientific-identity verification; atomic publication",
        "overwrite_policy": "refuse",
    }
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return {"editable": str(output), "manifest": str(manifest), "sha256": output_hash}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--expected-source-sha256")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        print(json.dumps(bootstrap(args.source, args.output, args.manifest, args.expected_source_sha256), indent=2))
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"BOOTSTRAP FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
