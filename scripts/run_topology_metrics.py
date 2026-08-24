"""Professor-facing Phase 7F topology metric command."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from geogami_morphology.metrics_topology import TopologyMetricError, analyze_topology


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calculate reproducible topology metrics from a verified canonical run.")
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
        result = analyze_topology(
            args.environment,
            canonical_run=args.canonical_run,
            canonical_path=args.canonical,
            latest_path=args.latest_pointer,
            config_path=args.config,
            output_root=args.output_root,
            project_root=PROJECT_ROOT,
        )
    except (TopologyMetricError, AnalysisGraphError, ValueError) as exc:
        print(f"TOPOLOGY METRICS: FAIL\n{exc}", file=sys.stderr)
        return 1
    metric = result.calculation.metric
    print("TOPOLOGY METRICS: PASS")
    print(f"Canonical run: {result.selection.run_id}")
    print(f"V / E / C: {metric('node_count')} / {metric('physical_edge_count')} / {metric('connected_component_count')}")
    print(f"Handshake: {metric('degree_sum')} = {metric('twice_physical_edge_count')}")
    print(f"Cycle rank: {metric('cycle_rank')}")
    print(f"Physical streets / reciprocal arcs: {metric('physical_edge_count')} / {result.osmnx_graph.number_of_edges()}")
    print("Length-weighted units: local units")
    print(f"Artifacts: {result.output_dir}")
    print("FINAL RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
