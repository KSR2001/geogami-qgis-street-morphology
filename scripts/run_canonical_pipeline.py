"""Validate QGIS edits and atomically publish a canonical GeoPackage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from geogami_morphology import PipelineError, run_preserve_topology, run_versioned_canonical


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True, help="Research environment label, for example env39.")
    parser.add_argument("--input", required=True, type=Path, help="Professor-edited GeoPackage; never modified.")
    parser.add_argument("--reference-canonical", required=True, type=Path, help="Frozen canonical topology contract.")
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path, help="Backward-compatible single canonical GeoPackage output.")
    destination.add_argument("--runs-root", type=Path, help="Create a self-contained versioned run and update latest.json.")
    parser.add_argument("--mode", required=True, choices=("preserve-topology",))
    parser.add_argument("--diagnostics-dir", type=Path, help="Defaults to <output_stem>_qa beside the output.")
    parser.add_argument("--latest-pointer", type=Path, help="Versioned mode only; defaults to latest.json beside runs root.")
    parser.add_argument("--endpoint-tolerance", type=float, default=1e-6, help="Maximum endpoint-only correction in local units (default: 1e-6).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.runs_root:
            if args.diagnostics_dir:
                raise ValueError("--diagnostics-dir is only valid with backward-compatible --output mode.")
            result = run_versioned_canonical(
                environment=args.environment,
                input_path=args.input,
                reference_path=args.reference_canonical,
                runs_root=args.runs_root,
                latest_path=args.latest_pointer,
                endpoint_tolerance=args.endpoint_tolerance,
                project_root=PROJECT_ROOT,
            )
            identity = result.manifest["network_identity"]
            published = result.manifest["published_canonical"]
            source = result.manifest["input_editable_network"]
            print("CANONICAL BUILD: PASS")
            print(f"Environment: {args.environment}")
            print("Mode: preserve-topology")
            print(f"Run ID: {result.run_id}")
            print(f"Input file SHA-256: {source['file_sha256']}")
            print(f"Canonical file SHA-256: {published['file_sha256']}")
            print(f"Scientific-content signature: {published['scientific_content_signature']}")
            print(f"Topology signature: {published['topology_signature']}")
            print(
                "Nodes / physical edges / components: "
                f"{identity['node_count']} / {identity['physical_edge_count']} / {identity['component_count']}"
            )
            print(f"Canonical output: {result.canonical_path}")
            print(f"Manifest: {result.manifest_path}")
            print(f"Latest pointer: {result.latest_path}")
            print("FINAL RESULT: PASS")
            return 0
        if args.latest_pointer:
            raise ValueError("--latest-pointer requires --runs-root.")
        result = run_preserve_topology(
            environment=args.environment,
            input_path=args.input,
            reference_path=args.reference_canonical,
            output=args.output,
            diagnostics_dir=args.diagnostics_dir,
            endpoint_tolerance=args.endpoint_tolerance,
        )
        print(json.dumps({"final_result": "PASS", "output": str(result.output), "diagnostics_dir": str(result.diagnostics_dir), "topology": result.report["topology"], "candidate": result.report["candidate"]}, indent=2))
        return 0
    except (PipelineError, OSError, ValueError) as exc:
        print(f"CANONICAL PIPELINE FAILED: {exc}", file=sys.stderr)
        if isinstance(exc, PipelineError) and exc.report:
            print(json.dumps({"final_result": "FAIL", "diagnostics": exc.report.get("diagnostics", []), "error_counts": exc.report.get("error_counts", {})}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
