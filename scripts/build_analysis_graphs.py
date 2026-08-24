"""Professor-facing Phase 7D canonical-to-analysis graph command."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from geogami_morphology.graph import AnalysisGraphError, build_analysis_graphs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build rigorously corresponding NetworkX and OSMnx graphs from canonical data."
    )
    parser.add_argument("--environment", required=True, help="Research environment, for example env39.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--canonical-run",
        default="latest",
        help="Phase 7C run ID or 'latest' (default: latest).",
    )
    source.add_argument("--canonical", type=Path, help="Explicit accepted canonical GeoPackage path.")
    parser.add_argument(
        "--latest-pointer",
        type=Path,
        help="Override latest.json location for reproduction/testing.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "results" / "analysis",
        help="Analysis publication root (default: results/analysis).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = build_analysis_graphs(
            args.environment,
            canonical_run=args.canonical_run,
            canonical_path=args.canonical,
            latest_path=args.latest_pointer,
            output_root=args.output_root,
            project_root=PROJECT_ROOT,
        )
    except (AnalysisGraphError, ValueError) as exc:
        print(f"ANALYSIS GRAPH BUILD: FAIL\n{exc}", file=sys.stderr)
        return 1

    scientific = result.manifest["networkx_graph"]
    directed = result.manifest["osmnx_graph"]
    print("ANALYSIS GRAPH BUILD: PASS")
    print(f"Canonical run: {result.selection.run_id}")
    print("\nNETWORKX SCIENTIFIC GRAPH")
    print(f"Nodes: {scientific['node_count']}")
    print(f"Physical edges: {scientific['physical_edge_count']}")
    print(f"Components: {scientific['component_count']}")
    print("\nOSMNX REPRESENTATION")
    print(f"Nodes: {directed['node_count']}")
    print(f"Directed arcs: {directed['directed_arc_count']}")
    print(f"Physical canonical streets: {directed['physical_canonical_edge_count']}")
    print("\nCRS: GeoGami Local Cartesian")
    print("Length units: local units")
    print("Geographic bearing functions: NOT USED")
    print("Geographic great-circle edge-length functions: NOT USED")
    print(f"GraphML: {result.graphml_path}")
    print(f"GraphML SHA-256: {result.graphml_sha256}")
    print("FINAL RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
