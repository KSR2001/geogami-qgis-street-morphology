"""Small presentation helpers for professor-facing notebooks."""

from __future__ import annotations

from html import escape

import geopandas as gpd


def degree_count_svg(
    degree_counts: dict[int, int],
    *,
    title: str = "Physical node degree counts",
    width: int = 640,
    height: int = 360,
) -> str:
    """Render a deterministic headless-safe degree-frequency bar chart."""
    if not degree_counts or any(int(degree) < 0 or int(count) < 0 for degree, count in degree_counts.items()):
        raise ValueError("Degree SVG requires non-negative degree counts.")
    items = sorted((int(degree), int(count)) for degree, count in degree_counts.items())
    maximum = max(count for _, count in items)
    if maximum == 0:
        raise ValueError("Degree SVG requires at least one node.")
    left, right, top, bottom = 64.0, 24.0, 52.0, 62.0
    plot_width, plot_height = width - left - right, height - top - bottom
    slot = plot_width / len(items)
    bars: list[str] = []
    labels: list[str] = []
    for index, (degree, count) in enumerate(items):
        bar_width = slot * 0.58
        x = left + index * slot + (slot - bar_width) / 2
        bar_height = plot_height * count / maximum
        y = top + plot_height - bar_height
        bars.append(
            f'<rect x="{x:.3f}" y="{y:.3f}" width="{bar_width:.3f}" '
            f'height="{bar_height:.3f}" fill="#35618f"/>'
        )
        labels.extend(
            [
                f'<text x="{x + bar_width / 2:.3f}" y="{y - 7:.3f}" text-anchor="middle" '
                f'font-family="sans-serif" font-size="12">{count}</text>',
                f'<text x="{x + bar_width / 2:.3f}" y="{top + plot_height + 21:.3f}" '
                f'text-anchor="middle" font-family="sans-serif" font-size="12">{degree}</text>',
            ]
        )
    return "".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" ',
            f'viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
            '<rect width="100%" height="100%" fill="white"/>',
            f'<text x="{width / 2:.1f}" y="27" text-anchor="middle" font-family="sans-serif" '
            f'font-size="18">{escape(title)}</text>',
            f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" '
            f'y2="{top + plot_height}" stroke="#333"/>',
            f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" stroke="#333"/>',
            *bars,
            *labels,
            f'<text x="{width / 2:.1f}" y="{height - 13}" text-anchor="middle" '
            'font-family="sans-serif" font-size="13">Physical node degree</text>',
            f'<text x="17" y="{height / 2:.1f}" text-anchor="middle" '
            f'transform="rotate(-90 17 {height / 2:.1f})" font-family="sans-serif" '
            'font-size="13">Node count</text>',
            '</svg>',
        ]
    )


def canonical_network_svg(
    nodes: gpd.GeoDataFrame,
    edges: gpd.GeoDataFrame,
    *,
    title: str = "Canonical physical street network",
    size: int = 720,
) -> str:
    """Render canonical geometry as a deterministic equal-scale inline SVG."""
    if nodes.empty or edges.empty:
        raise ValueError("Canonical SVG requires non-empty node and edge layers.")
    minimum_x, minimum_y, maximum_x, maximum_y = edges.total_bounds
    span_x, span_y = float(maximum_x - minimum_x), float(maximum_y - minimum_y)
    if span_x <= 0 or span_y <= 0:
        raise ValueError("Canonical SVG requires non-zero X and Y extents.")
    left, right, top, bottom = 82.0, 24.0, 52.0, 76.0
    plot_width, plot_height = size - left - right, size - top - bottom
    scale = min(plot_width / span_x, plot_height / span_y)
    used_width, used_height = span_x * scale, span_y * scale
    offset_x = left + (plot_width - used_width) / 2
    offset_y = top + (plot_height - used_height) / 2

    def project(coordinate):
        x, y = coordinate[:2]
        return (
            offset_x + (float(x) - minimum_x) * scale,
            offset_y + used_height - (float(y) - minimum_y) * scale,
        )

    polylines = []
    for geometry in edges.geometry:
        points = " ".join(f"{x:.3f},{y:.3f}" for x, y in map(project, geometry.coords))
        polylines.append(
            f'<polyline points="{points}" fill="none" stroke="#35618f" stroke-width="1.8"/>'
        )
    circles = []
    for geometry in nodes.geometry:
        x, y = project(geometry.coords[0])
        circles.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="2.6" fill="#b33a3a"/>')

    axis_y = offset_y + used_height
    return "".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" ',
            f'viewBox="0 0 {size} {size}" role="img" aria-label="{escape(title)}">',
            '<rect width="100%" height="100%" fill="white"/>',
            f'<text x="{size / 2:.1f}" y="26" text-anchor="middle" ',
            f'font-family="sans-serif" font-size="18">{escape(title)}</text>',
            f'<line x1="{offset_x:.3f}" y1="{axis_y:.3f}" x2="{offset_x + used_width:.3f}" ',
            'y2="{0:.3f}" stroke="#333"/>'.format(axis_y),
            f'<line x1="{offset_x:.3f}" y1="{offset_y:.3f}" x2="{offset_x:.3f}" ',
            f'y2="{axis_y:.3f}" stroke="#333"/>',
            *polylines,
            *circles,
            f'<text x="{offset_x:.3f}" y="{axis_y + 20:.3f}" text-anchor="middle" ',
            f'font-family="sans-serif" font-size="11">{minimum_x:.1f}</text>',
            f'<text x="{offset_x + used_width:.3f}" y="{axis_y + 20:.3f}" text-anchor="middle" ',
            f'font-family="sans-serif" font-size="11">{maximum_x:.1f}</text>',
            f'<text x="{size / 2:.1f}" y="{size - 18}" text-anchor="middle" ',
            'font-family="sans-serif" font-size="14">X [local units]</text>',
            f'<text x="18" y="{size / 2:.1f}" text-anchor="middle" ',
            f'transform="rotate(-90 18 {size / 2:.1f})" font-family="sans-serif" ',
            'font-size="14">Y [local units]</text>',
            f'<text x="{offset_x - 8:.3f}" y="{offset_y + 4:.3f}" text-anchor="end" ',
            f'font-family="sans-serif" font-size="11">{maximum_y:.1f}</text>',
            f'<text x="{offset_x - 8:.3f}" y="{axis_y + 4:.3f}" text-anchor="end" ',
            f'font-family="sans-serif" font-size="11">{minimum_y:.1f}</text>',
            '</svg>',
        ]
    )
