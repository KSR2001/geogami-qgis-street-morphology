"""Build the formal controlled Env38-versus-Env39 comparison package."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from geogami_morphology.comparison import ComparisonError, build_environment_comparison


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a provenance-locked controlled comparison of accepted Env38 and Env39 results."
    )
    parser.add_argument("--comparison-id")
    parser.add_argument(
        "--phase-start-clean", action="store_true",
        help="Record that the worktree was independently verified clean before Phase 9G mutations.",
    )
    parser.add_argument(
        "--phase-start-commit",
        help="Commit recorded during the independent Phase 9G clean-start audit.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = build_environment_comparison(
            project_root=PROJECT_ROOT,
            comparison_id=args.comparison_id,
            phase_start_clean=True if args.phase_start_clean else None,
            phase_start_commit=args.phase_start_commit,
        )
    except (ComparisonError, OSError, ValueError) as exc:
        print(f"ENV38 VS ENV39 COMPARISON: FAIL\n{exc}", file=sys.stderr)
        return 1
    print("ENV38 VS ENV39 COMPARISON: PASS")
    print(f"Comparison ID: {result.comparison_id}")
    print(f"Env39 / Env38: {result.manifest['environments']['env39']['run_id']} / {result.manifest['environments']['env38']['run_id']}")
    print(f"Metrics / core metrics: {len(result.comparison_rows)} / {len(result.core_rows)}")
    print(f"Artifacts: {result.output_dir}")
    print("FINAL RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
