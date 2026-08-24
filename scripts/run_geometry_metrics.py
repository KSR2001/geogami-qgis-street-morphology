"""Professor-facing Phase 7G planar geometry and orientation command."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from geogami_morphology.graph import AnalysisGraphError
from geogami_morphology.metrics_geometry import GeometryMetricError, analyze_geometry


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calculate planar geometry, circuity, and axial-orientation metrics from canonical data.")
    parser.add_argument("--environment", required=True)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--canonical-run", default="latest")
    source.add_argument("--canonical", type=Path)
    parser.add_argument("--latest-pointer", type=Path)
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config" / "metrics.yaml")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "results" / "analysis")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = analyze_geometry(
            args.environment, canonical_run=args.canonical_run, canonical_path=args.canonical,
            latest_path=args.latest_pointer, config_path=args.config,
            output_root=args.output_root, project_root=PROJECT_ROOT,
        )
    except (GeometryMetricError, AnalysisGraphError, ValueError) as exc:
        print(f"GEOMETRY METRICS: FAIL\n{exc}", file=sys.stderr)
        return 1
    metric = result.calculation.metric
    print("GEOMETRY METRICS: PASS")
    print(f"Canonical run: {result.selection.run_id}")
    print(f"Nodes / physical edge geometries: {metric('node_count')} / {metric('physical_edge_geometry_count')}")
    print(f"Total network length: {metric('total_network_length_local')} local units")
    print(f"Mean edge / network circuity: {metric('mean_edge_circuity')} / {metric('network_circuity')}")
    print(f"Chord entropy / normalized: {metric('chord_orientation_entropy')} / {metric('chord_orientation_normalized_entropy')}")
    print(f"Segment entropy / normalized: {metric('segment_length_weighted_orientation_entropy')} / {metric('segment_length_weighted_normalized_entropy')}")
    print(f"Chord phi / segment-length-weighted phi: {metric('phi_chord')} / {metric('phi_segment_length_weighted')}")
    print("Orientation: planar north-clockwise axial [0,180); no geographic functions")
    print(f"Artifacts: {result.output_dir}")
    print("FINAL RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
