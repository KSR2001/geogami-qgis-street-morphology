"""Professor-facing Phase 7H integrated Env39 results command."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from geogami_morphology.graph import AnalysisGraphError
from geogami_morphology.integrated import IntegratedAnalysisError, integrate_results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Integrate accepted topology, geometry, and orientation results into a comparison-ready package."
    )
    parser.add_argument("--environment", default="env39")
    parser.add_argument("--canonical-run", default="latest")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = integrate_results(
            args.environment, canonical_run=args.canonical_run, project_root=PROJECT_ROOT, publish=True
        )
    except (IntegratedAnalysisError, AnalysisGraphError, ValueError) as exc:
        print(f"INTEGRATED RESULTS: FAIL\n{exc}", file=sys.stderr)
        return 1
    print("INTEGRATED RESULTS: PASS")
    print(f"Canonical run: {result.selection.run_id}")
    print(f"Integrated metrics / core metrics: {len(result.metrics)} / {len(result.core_metrics)}")
    print(f"Artifacts: {result.output_dir}")
    print("FINAL RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
