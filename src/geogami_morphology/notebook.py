"""Small presentation helpers for professor-facing notebooks."""

from __future__ import annotations

from html import escape

import geopandas as gpd


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
