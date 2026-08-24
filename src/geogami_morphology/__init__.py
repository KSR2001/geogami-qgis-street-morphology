"""Reusable canonical street-network tooling for GeoGami morphology studies."""

from .canonical import PipelineError, PipelineResult, run_preserve_topology
from .graph import (
    AnalysisBuildResult,
    AnalysisGraphError,
    build_analysis_graphs,
    resolve_canonical_run,
)
from .identity import scientific_content_signature
from .metrics_topology import (
    TopologyAnalysisResult,
    TopologyMetricError,
    analyze_topology,
)
from .versioned import VersionedRunResult, run_versioned_canonical

__all__ = [
    "PipelineError",
    "PipelineResult",
    "AnalysisBuildResult",
    "AnalysisGraphError",
    "TopologyAnalysisResult",
    "TopologyMetricError",
    "VersionedRunResult",
    "build_analysis_graphs",
    "analyze_topology",
    "resolve_canonical_run",
    "run_preserve_topology",
    "run_versioned_canonical",
    "scientific_content_signature",
]
