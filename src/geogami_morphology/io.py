"""GeoPackage and deterministic artifact I/O helpers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Any, Callable
import uuid

import geopandas as gpd


GEOPACKAGE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
WINDOWS_TRANSIENT_LOCK_ERRORS = {32, 33}
WINDOWS_FILE_OPERATION_DELAYS_SECONDS = (0.05, 0.10, 0.20, 0.40)


class GeoPackagePublicationError(OSError):
    """A validated GeoPackage could not be published without weakening safety."""


@dataclass(frozen=True)
class GeoPackagePublicationResult:
    validated_candidate_sha256: str
    published_sha256: str
    sqlite_integrity_check: str
    fresh_publication_copy: bool
    candidate_cleanup_attempts: int
    replace_attempts: int
    transient_sharing_violations: int


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
    connection = sqlite3.connect(path)
    try:
        connection.execute("UPDATE gpkg_contents SET last_change='2000-01-01T00:00:00.000Z'")
        connection.commit()
        connection.execute("VACUUM")
    finally:
        connection.close()


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


def geopackage_sidecars(path: Path) -> tuple[Path, ...]:
    """Return transaction sidecars without deleting or otherwise altering them."""
    selected = Path(path)
    return tuple(
        sidecar
        for sidecar in (Path(str(selected) + suffix) for suffix in GEOPACKAGE_SIDECAR_SUFFIXES)
        if sidecar.exists()
    )


def sqlite_integrity_check(path: Path) -> str:
    """Run SQLite integrity_check through an explicitly closed read-only connection."""
    selected = Path(path).resolve()
    connection = sqlite3.connect(f"{selected.as_uri()}?mode=ro", uri=True)
    try:
        rows = connection.execute("PRAGMA integrity_check").fetchall()
    finally:
        connection.close()
    if rows != [("ok",)]:
        raise GeoPackagePublicationError(f"GeoPackage SQLite integrity check failed for {selected}: {rows}")
    return "ok"


def _is_windows_sharing_violation(error: BaseException, platform_name: str) -> bool:
    return (
        platform_name == "nt"
        and isinstance(error, OSError)
        and getattr(error, "winerror", None) in WINDOWS_TRANSIENT_LOCK_ERRORS
    )


def _run_file_operation(
    operation: Callable[[], Any],
    *,
    description: str,
    platform_name: str | None = None,
    delays: tuple[float, ...] = WINDOWS_FILE_OPERATION_DELAYS_SECONDS,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[Any, int, int]:
    """Run one file operation with bounded Windows sharing-violation retries only."""
    selected_platform = os.name if platform_name is None else platform_name
    attempts = 0
    transient_errors = 0
    while True:
        attempts += 1
        try:
            return operation(), attempts, transient_errors
        except OSError as exc:
            if not _is_windows_sharing_violation(exc, selected_platform):
                raise
            transient_errors += 1
            if attempts > len(delays):
                raise GeoPackagePublicationError(
                    f"{description} failed after {attempts} attempts because Windows kept the file locked "
                    f"(WinError {exc.winerror})."
                ) from exc
            sleeper(delays[attempts - 1])


def _require_self_contained(path: Path, label: str) -> str:
    sidecars = geopackage_sidecars(path)
    if sidecars:
        names = ", ".join(item.name for item in sidecars)
        raise GeoPackagePublicationError(
            f"{label} GeoPackage is not self-contained; transaction sidecar(s) are present: {names}"
        )
    return sqlite_integrity_check(path)


def publish_validated_geopackage(
    validated_candidate: Path,
    output: Path,
    *,
    verify_published: Callable[[Path], None],
) -> GeoPackagePublicationResult:
    """Publish a validated GeoPackage through a fresh, never-GDAL-opened copy.

    The validated candidate is checked for SQLite integrity and transaction
    sidecars, copied byte-for-byte beside the destination, and then removed.
    Only that fresh copy is atomically renamed. The caller-provided verifier
    reopens the published path after the rename; an existing destination is
    restored if this final verification fails.
    """
    candidate, destination = Path(validated_candidate), Path(output)
    if candidate.resolve() == destination.resolve():
        raise GeoPackagePublicationError("Validated candidate and publication destination must differ.")
    if not candidate.is_file():
        raise FileNotFoundError(f"Validated GeoPackage candidate does not exist: {candidate}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    integrity = _require_self_contained(candidate, "Validated candidate")
    candidate_sha = sha256_file(candidate)
    stage = destination.with_name(f".{destination.stem}.publication-{uuid.uuid4().hex}.gpkg")
    backup = destination.with_name(f".{destination.stem}.rollback-{uuid.uuid4().hex}.gpkg")
    cleanup_attempts = 0
    replace_attempts = 0
    transient_errors = 0
    destination_replaced = False
    had_destination = destination.is_file()
    try:
        if geopackage_sidecars(destination):
            names = ", ".join(item.name for item in geopackage_sidecars(destination))
            raise GeoPackagePublicationError(
                f"Publication destination has active GeoPackage transaction sidecar(s): {names}"
            )
        candidate_sidecars = geopackage_sidecars(candidate)
        if candidate_sidecars:
            names = ", ".join(item.name for item in candidate_sidecars)
            raise GeoPackagePublicationError(
                f"Validated candidate gained transaction sidecar(s) before copying: {names}"
            )
        shutil.copy2(candidate, stage)
        if sha256_file(stage) != candidate_sha:
            raise GeoPackagePublicationError("Fresh publication-stage copy is not byte-identical to the validated candidate.")
        if geopackage_sidecars(stage):
            raise GeoPackagePublicationError("Fresh publication-stage copy unexpectedly has a transaction sidecar.")
        _, cleanup_attempts, cleanup_transients = _run_file_operation(
            candidate.unlink,
            description="Validated candidate cleanup",
        )
        transient_errors += cleanup_transients
        if had_destination:
            shutil.copy2(destination, backup)
            if sha256_file(backup) != sha256_file(destination):
                raise GeoPackagePublicationError("Rollback copy is not byte-identical to the existing destination.")
        _, replace_attempts, replace_transients = _run_file_operation(
            lambda: os.replace(stage, destination),
            description="Atomic GeoPackage publication",
        )
        transient_errors += replace_transients
        destination_replaced = True
        verify_published(destination)
        published_sha = sha256_file(destination)
        if published_sha != candidate_sha:
            raise GeoPackagePublicationError("Published GeoPackage bytes differ from the validated candidate.")
        if backup.exists():
            _run_file_operation(backup.unlink, description="GeoPackage rollback-copy cleanup")
        return GeoPackagePublicationResult(
            validated_candidate_sha256=candidate_sha,
            published_sha256=published_sha,
            sqlite_integrity_check=integrity,
            fresh_publication_copy=True,
            candidate_cleanup_attempts=cleanup_attempts,
            replace_attempts=replace_attempts,
            transient_sharing_violations=transient_errors,
        )
    except Exception as exc:
        if destination_replaced:
            try:
                if had_destination and backup.is_file():
                    _run_file_operation(
                        lambda: os.replace(backup, destination),
                        description="GeoPackage publication rollback",
                    )
                elif destination.exists():
                    _run_file_operation(destination.unlink, description="Failed GeoPackage publication cleanup")
            except OSError as rollback_exc:
                raise GeoPackagePublicationError(
                    f"GeoPackage publication failed ({exc}) and rollback also failed ({rollback_exc})."
                ) from rollback_exc
        raise
    finally:
        for transient in (stage, backup):
            if transient.exists():
                try:
                    _run_file_operation(transient.unlink, description=f"Temporary publication cleanup for {transient.name}")
                except OSError:
                    pass
