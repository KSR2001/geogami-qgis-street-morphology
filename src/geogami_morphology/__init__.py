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
from .metrics_geometry import (
    GeometryAnalysisResult,
    GeometryMetricError,
    analyze_geometry,
)
from .integrated import (
    IntegratedAnalysisError,
    IntegratedResult,
    integrate_results,
)
from .versioned import VersionedRunResult, run_versioned_canonical
from .workflow import FullAnalysisResult, WorkflowError, run_full_analysis

__all__ = [
    "PipelineError",
    "PipelineResult",
    "AnalysisBuildResult",
    "AnalysisGraphError",
    "TopologyAnalysisResult",
    "TopologyMetricError",
    "GeometryAnalysisResult",
    "GeometryMetricError",
    "IntegratedAnalysisError",
    "IntegratedResult",
    "VersionedRunResult",
    "FullAnalysisResult",
    "WorkflowError",
    "build_analysis_graphs",
    "analyze_topology",
    "analyze_geometry",
    "integrate_results",
    "resolve_canonical_run",
    "run_preserve_topology",
    "run_versioned_canonical",
    "run_full_analysis",
    "scientific_content_signature",
]
