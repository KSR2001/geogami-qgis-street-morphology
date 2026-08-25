"""Run the complete professor-facing GeoGami morphology workflow."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from geogami_morphology.workflow import SUPPORTED_MODE, WorkflowError, run_full_analysis


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a professor-edited GeoPackage and run the complete versioned analysis."
    )
    parser.add_argument("--environment", required=True, choices=("env38", "env39"), help="Registered research environment.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="Professor-edited GeoPackage; never modified.")
    source.add_argument("--canonical-run", help="Analyze an existing verified canonical run, for example latest.")
    parser.add_argument("--mode", required=True, choices=(SUPPORTED_MODE,))
    parser.add_argument(
        "--reference-canonical", type=Path,
        help="Frozen topology contract used only with --input.",
    )
    parser.add_argument("--metrics-config", type=Path, default=PROJECT_ROOT / "config" / "metrics.yaml")
    parser.add_argument("--endpoint-tolerance", type=float, default=1e-6)
    parser.add_argument("--runs-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--canonical-latest", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--analysis-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--analysis-latest", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--environment-registry", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", help="Validate and show the plan without creating or updating anything.")
    parser.add_argument("--verbose", action="store_true", help="Print additional identity and configuration hashes.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run_full_analysis(
            args.environment, input_path=args.input, canonical_run=args.canonical_run,
            mode=args.mode, reference_canonical=args.reference_canonical,
            runs_root=args.runs_root, canonical_latest=args.canonical_latest,
            analysis_root=args.analysis_root, analysis_latest=args.analysis_latest,
            environment_registry=args.environment_registry,
            metrics_config=args.metrics_config, endpoint_tolerance=args.endpoint_tolerance,
            dry_run=args.dry_run, verbose=args.verbose, project_root=PROJECT_ROOT,
        )
        return 0
    except WorkflowError as exc:
        print(f"WORKFLOW FAILED AT [{exc.stage_number}/8] {exc.stage_name}: {exc.cause}", file=sys.stderr)
        print("Downstream stages were not run.", file=sys.stderr)
        print("FINAL RESULT: FAIL", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
