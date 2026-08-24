"""Planar axial street-orientation metrics and deterministic SVG figures."""

from __future__ import annotations

import cmath
from html import escape
import math
from typing import Iterable, Sequence


AXIAL_PERIOD_DEGREES = 180.0


class OrientationMetricError(ValueError):
    """Raised when an orientation observation or weight is invalid."""


def planar_axial_orientation(
    start: Sequence[float],
    end: Sequence[float],
    *,
    zero_tolerance: float = 0.0,
) -> float:
    """Return north-clockwise planar axial orientation in ``[0, 180)`` degrees."""
    dx = float(end[0]) - float(start[0])
    dy = float(end[1]) - float(start[1])
    if math.hypot(dx, dy) <= zero_tolerance:
        raise OrientationMetricError("Axial orientation is undefined for a zero-length vector.")
    angle = math.degrees(math.atan2(dx, dy)) % AXIAL_PERIOD_DEGREES
    return 0.0 if math.isclose(angle, AXIAL_PERIOD_DEGREES, abs_tol=1e-12) else angle


def axial_orientation_histogram(
    orientations_deg: Iterable[float],
    *,
    weights: Iterable[float] | None = None,
    bins: int = 36,
    boundary_tolerance_degrees: float = 0.0,
) -> tuple[dict[str, float | int], ...]:
    """Build a deterministic weighted histogram over the axial domain ``[0,180)``."""
    angles = [float(value) for value in orientations_deg]
    if not isinstance(bins, int) or isinstance(bins, bool) or bins <= 0:
        raise OrientationMetricError("Orientation bin count must be a positive integer.")
    if not math.isfinite(boundary_tolerance_degrees) or boundary_tolerance_degrees < 0:
        raise OrientationMetricError("Orientation boundary tolerance must be finite and non-negative.")
    if not angles:
        raise OrientationMetricError("Orientation histogram requires at least one observation.")
    selected_weights = [1.0] * len(angles) if weights is None else [float(value) for value in weights]
    if len(selected_weights) != len(angles):
        raise OrientationMetricError("Orientation observations and weights must have equal length.")
    if any(not math.isfinite(value) for value in angles):
        raise OrientationMetricError("Orientation observations must be finite.")
    if any(not math.isfinite(value) or value < 0 for value in selected_weights):
        raise OrientationMetricError("Orientation weights must be finite and non-negative.")
    total_weight = math.fsum(selected_weights)
    if total_weight <= 0:
        raise OrientationMetricError("Orientation weights must have a positive sum.")
    width = AXIAL_PERIOD_DEGREES / bins
    bin_weights = [0.0] * bins
    bin_counts = [0] * bins
    for raw_angle, weight in zip(angles, selected_weights, strict=True):
        angle = raw_angle % AXIAL_PERIOD_DEGREES
        nearest_boundary = round(angle / width) * width
        if abs(angle - nearest_boundary) <= boundary_tolerance_degrees:
            angle = nearest_boundary % AXIAL_PERIOD_DEGREES
        index = min(int(math.floor(angle / width)), bins - 1)
        bin_counts[index] += 1
        bin_weights[index] += weight
    return tuple(
        {
            "bin_index": index,
            "bin_start_deg": index * width,
            "bin_end_deg": (index + 1) * width,
            "bin_center_deg": (index + 0.5) * width,
            "observation_count": bin_counts[index],
            "weight": bin_weights[index],
            "probability": bin_weights[index] / total_weight,
        }
        for index in range(bins)
    )


def shannon_orientation_entropy(
    histogram: Iterable[dict[str, float | int]],
) -> dict[str, float]:
    """Calculate natural-log Shannon entropy and its bin-count normalization."""
    rows = tuple(histogram)
    if len(rows) < 2:
        raise OrientationMetricError("Normalized orientation entropy requires at least two bins.")
    probabilities = [float(row["probability"]) for row in rows]
    if not math.isclose(math.fsum(probabilities), 1.0, rel_tol=1e-12, abs_tol=1e-12):
        raise OrientationMetricError("Orientation histogram probabilities must sum to one.")
    entropy = -math.fsum(value * math.log(value) for value in probabilities if value > 0)
    maximum = math.log(len(rows))
    normalized = entropy / maximum
    if normalized < 0 and abs(normalized) <= 1e-15:
        normalized = 0.0
    if not 0.0 <= normalized <= 1.0 + 1e-12:
        raise OrientationMetricError("Normalized orientation entropy fell outside [0,1].")
    return {"entropy": entropy, "maximum_entropy": maximum, "normalized_entropy": min(1.0, normalized)}


def fourfold_orientation_order(
    orientations_deg: Iterable[float],
    *,
    weights: Iterable[float] | None = None,
    symmetry: int = 4,
) -> float:
    """Return ``|sum(w*exp(i*m*theta))/sum(w)|`` for configured symmetry ``m``."""
    angles = [float(value) for value in orientations_deg]
    selected_weights = [1.0] * len(angles) if weights is None else [float(value) for value in weights]
    if not angles or len(angles) != len(selected_weights):
        raise OrientationMetricError("Orientation order requires equal non-empty angles and weights.")
    if not isinstance(symmetry, int) or isinstance(symmetry, bool) or symmetry <= 0:
        raise OrientationMetricError("Orientation-order symmetry must be a positive integer.")
    if any(not math.isfinite(value) for value in angles):
        raise OrientationMetricError("Orientation-order angles must be finite.")
    if any(not math.isfinite(value) or value < 0 for value in selected_weights):
        raise OrientationMetricError("Orientation-order weights must be finite and non-negative.")
    total = math.fsum(selected_weights)
    if total <= 0:
        raise OrientationMetricError("Orientation-order weights must have a positive sum.")
    vector = sum(
        (weight * cmath.exp(1j * symmetry * math.radians(angle))
         for angle, weight in zip(angles, selected_weights, strict=True)),
        0j,
    )
    value = abs(vector / total)
    if value > 1.0 and value - 1.0 <= 1e-12:
        value = 1.0
    if not 0.0 <= value <= 1.0:
        raise OrientationMetricError("Orientation order fell outside [0,1].")
    return value


def _axes(width: int, height: int, title: str, x_label: str, y_label: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" ',
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width / 2:.1f}" y="27" text-anchor="middle" font-family="sans-serif" font-size="18">{escape(title)}</text>',
        f'<text x="{width / 2:.1f}" y="{height - 12}" text-anchor="middle" font-family="sans-serif" font-size="13">{escape(x_label)}</text>',
        f'<text x="17" y="{height / 2:.1f}" text-anchor="middle" transform="rotate(-90 17 {height / 2:.1f})" font-family="sans-serif" font-size="13">{escape(y_label)}</text>',
    ]


def numeric_distribution_svg(
    values: Iterable[float],
    *,
    title: str,
    x_label: str,
    bins: int = 12,
    width: int = 680,
    height: int = 380,
) -> str:
    """Render a deterministic headless-safe equal-width numeric histogram."""
    data = [float(value) for value in values]
    if not data or any(not math.isfinite(value) for value in data):
        raise OrientationMetricError("Numeric distribution requires finite observations.")
    minimum, maximum = min(data), max(data)
    if math.isclose(minimum, maximum):
        minimum -= 0.5
        maximum += 0.5
    bin_width = (maximum - minimum) / bins
    counts = [0] * bins
    for value in data:
        index = min(int((value - minimum) / bin_width), bins - 1)
        counts[index] += 1
    left, right, top, bottom = 66.0, 24.0, 50.0, 64.0
    plot_width, plot_height = width - left - right, height - top - bottom
    svg = _axes(width, height, title, x_label, "Physical edge count")
    svg.extend([
        f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}" stroke="#333"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" stroke="#333"/>',
    ])
    maximum_count = max(counts)
    for index, count in enumerate(counts):
        x = left + index * plot_width / bins
        bar_height = plot_height * count / maximum_count
        svg.append(f'<rect x="{x:.3f}" y="{top + plot_height - bar_height:.3f}" width="{plot_width / bins - 1:.3f}" height="{bar_height:.3f}" fill="#35618f"/>')
    svg.extend([
        f'<text x="{left}" y="{top + plot_height + 19}" text-anchor="middle" font-family="sans-serif" font-size="11">{minimum:.4g}</text>',
        f'<text x="{left + plot_width}" y="{top + plot_height + 19}" text-anchor="middle" font-family="sans-serif" font-size="11">{maximum:.4g}</text>',
        '</svg>',
    ])
    return "".join(svg)


def orientation_histogram_svg(
    histogram: Iterable[dict[str, float | int]],
    *,
    title: str,
    y_label: str,
    width: int = 720,
    height: int = 400,
) -> str:
    """Render an axial ``[0,180)`` orientation histogram from metric bins."""
    rows = tuple(histogram)
    if not rows:
        raise OrientationMetricError("Orientation SVG requires histogram rows.")
    left, right, top, bottom = 66.0, 24.0, 50.0, 64.0
    plot_width, plot_height = width - left - right, height - top - bottom
    svg = _axes(width, height, title, "Axial orientation [degrees; 0=N, 90=E]", y_label)
    svg.extend([
        f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}" stroke="#333"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" stroke="#333"/>',
    ])
    maximum = max(float(row["probability"]) for row in rows) or 1.0
    for index, row in enumerate(rows):
        value = float(row["probability"])
        x = left + index * plot_width / len(rows)
        bar_height = plot_height * value / maximum
        svg.append(f'<rect x="{x:.3f}" y="{top + plot_height - bar_height:.3f}" width="{plot_width / len(rows) - 0.6:.3f}" height="{bar_height:.3f}" fill="#35618f"/>')
    for angle in (0, 45, 90, 135, 180):
        x = left + plot_width * angle / 180
        svg.append(f'<text x="{x:.3f}" y="{top + plot_height + 19}" text-anchor="middle" font-family="sans-serif" font-size="11">{angle}°</text>')
    svg.append('</svg>')
    return "".join(svg)


def axial_rose_svg(
    histogram: Iterable[dict[str, float | int]],
    *,
    title: str,
    width: int = 560,
    height: int = 560,
) -> str:
    """Render an explicitly mirrored 180-degree axial rose as orientation axes."""
    rows = tuple(histogram)
    if not rows:
        raise OrientationMetricError("Axial rose requires histogram rows.")
    center_x, center_y, radius = width / 2, height / 2 + 10, min(width, height) * 0.35
    maximum = max(float(row["probability"]) for row in rows) or 1.0
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{center_x:.3f}" y="28" text-anchor="middle" font-family="sans-serif" font-size="18">{escape(title)}</text>',
        f'<text x="{center_x:.3f}" y="48" text-anchor="middle" font-family="sans-serif" font-size="11">Axial [0,180) observations shown as mirrored 180° axes</text>',
        f'<circle cx="{center_x:.3f}" cy="{center_y:.3f}" r="{radius:.3f}" fill="none" stroke="#bbb"/>',
    ]
    for row in rows:
        angle = math.radians(float(row["bin_center_deg"]))
        length = radius * float(row["probability"]) / maximum
        dx, dy = math.sin(angle) * length, -math.cos(angle) * length
        svg.append(f'<line x1="{center_x - dx:.3f}" y1="{center_y - dy:.3f}" x2="{center_x + dx:.3f}" y2="{center_y + dy:.3f}" stroke="#35618f" stroke-width="6" stroke-linecap="round"/>')
    svg.extend([
        f'<circle cx="{center_x:.3f}" cy="{center_y:.3f}" r="3" fill="#222"/>',
        f'<text x="{center_x:.3f}" y="{center_y - radius - 9:.3f}" text-anchor="middle" font-family="sans-serif" font-size="12">N / S (0°)</text>',
        f'<text x="{center_x + radius + 9:.3f}" y="{center_y + 4:.3f}" text-anchor="start" font-family="sans-serif" font-size="12">E / W (90°)</text>',
        '</svg>',
    ])
    return "".join(svg)
