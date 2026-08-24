"""GeoPackage and deterministic artifact I/O helpers."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

import geopandas as gpd


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def read_network(path: Path) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"GeoPackage does not exist: {path}")
    layers = set(gpd.list_layers(path)["name"].astype(str))
    missing = {"nodes", "edges"} - layers
    if missing:
        raise ValueError(f"Missing required layer(s): {', '.join(sorted(missing))}")
    return gpd.read_file(path, layer="nodes"), gpd.read_file(path, layer="edges")


def stabilize_geopackage(path: Path) -> None:
    """Remove timestamps as a source of byte-level output variation."""
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE gpkg_contents SET last_change='2000-01-01T00:00:00.000Z'")
        connection.commit()
        connection.execute("VACUUM")


def write_network(path: Path, nodes: gpd.GeoDataFrame, edges: gpd.GeoDataFrame) -> None:
    path = Path(path)
    if path.exists():
        path.unlink()
    nodes.to_file(path, layer="nodes", driver="GPKG", index=False)
    edges.to_file(path, layer="edges", driver="GPKG", mode="a", index=False)
    stabilize_geopackage(path)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def atomic_publish(candidate: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    os.replace(candidate, output)
