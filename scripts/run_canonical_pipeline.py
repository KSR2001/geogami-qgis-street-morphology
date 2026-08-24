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

from geogami_morphology import PipelineError, run_preserve_topology


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True, help="Research environment label, for example env39.")
    parser.add_argument("--input", required=True, type=Path, help="Professor-edited GeoPackage; never modified.")
    parser.add_argument("--reference-canonical", required=True, type=Path, help="Frozen canonical topology contract.")
    parser.add_argument("--output", required=True, type=Path, help="New canonical GeoPackage to publish after QA passes.")
    parser.add_argument("--mode", required=True, choices=("preserve-topology",))
    parser.add_argument("--diagnostics-dir", type=Path, help="Defaults to <output_stem>_qa beside the output.")
    parser.add_argument("--endpoint-tolerance", type=float, default=1e-6, help="Maximum endpoint-only correction in local units (default: 1e-6).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
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
