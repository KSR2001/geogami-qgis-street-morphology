"""Authoritative, repository-portable environment registry loader."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = PROJECT_ROOT / "config" / "environments.yaml"
SUPPORTED_ENVIRONMENTS = frozenset({"env38", "env39"})
PATH_FIELDS = (
    "editable_source",
    "canonical_root",
    "canonical_runs",
    "canonical_latest",
    "analysis_root",
    "topology_reference",
    "reference_image",
)


class EnvironmentRegistryError(ValueError):
    """The environment registry is absent, malformed, or non-portable."""


@dataclass(frozen=True)
class Environment:
    """Resolved paths for one registered environment."""

    environment_id: str
    repository_root: Path
    editable_source: Path
    canonical_root: Path
    canonical_runs: Path
    canonical_latest: Path
    analysis_root: Path
    topology_reference: Path
    reference_image: Path

    def relative_paths(self) -> dict[str, str]:
        """Return portable POSIX paths relative to the repository root."""
        return {
            field: getattr(self, field).relative_to(self.repository_root).as_posix()
            for field in PATH_FIELDS
        }


def _portable_path(value: Any, field: str, repository_root: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise EnvironmentRegistryError(f"Registry field {field!r} must be a non-empty relative path.")
    if "\\" in value:
        raise EnvironmentRegistryError(f"Registry field {field!r} must use portable forward slashes: {value!r}.")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise EnvironmentRegistryError(f"Registry field {field!r} must remain inside the repository: {value!r}.")
    resolved = (repository_root / Path(*relative.parts)).resolve()
    if not resolved.is_relative_to(repository_root):
        raise EnvironmentRegistryError(f"Registry field {field!r} escapes the repository: {value!r}.")
    return resolved


def load_registry(
    registry_path: Path = DEFAULT_REGISTRY,
    repository_root: Path = PROJECT_ROOT,
) -> dict[str, Environment]:
    """Load and strictly validate all supported environment definitions."""
    root = Path(repository_root).resolve()
    path = Path(registry_path)
    if not path.is_file():
        raise EnvironmentRegistryError(f"Environment registry does not exist: {path}")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise EnvironmentRegistryError(f"Cannot read environment registry {path}: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise EnvironmentRegistryError("Environment registry requires schema_version: 1.")
    records = document.get("environments")
    if not isinstance(records, dict):
        raise EnvironmentRegistryError("Environment registry requires an environments mapping.")
    observed = set(records)
    if observed != SUPPORTED_ENVIRONMENTS:
        missing = sorted(SUPPORTED_ENVIRONMENTS - observed)
        unknown = sorted(observed - SUPPORTED_ENVIRONMENTS)
        raise EnvironmentRegistryError(
            f"Environment registry must define exactly {sorted(SUPPORTED_ENVIRONMENTS)}; "
            f"missing={missing}, unknown={unknown}."
        )

    result: dict[str, Environment] = {}
    required = {"environment_id", *PATH_FIELDS}
    for environment_id in sorted(SUPPORTED_ENVIRONMENTS):
        record = records[environment_id]
        if not isinstance(record, dict):
            raise EnvironmentRegistryError(f"Registry entry {environment_id!r} must be a mapping.")
        missing_fields = sorted(required - set(record))
        unknown_fields = sorted(set(record) - required)
        if missing_fields or unknown_fields:
            raise EnvironmentRegistryError(
                f"Registry entry {environment_id!r} has missing={missing_fields}, unknown={unknown_fields}."
            )
        if record["environment_id"] != environment_id:
            raise EnvironmentRegistryError(
                f"Registry key {environment_id!r} does not match environment_id {record['environment_id']!r}."
            )
        paths = {
            field: _portable_path(record[field], f"{environment_id}.{field}", root)
            for field in PATH_FIELDS
        }
        result[environment_id] = Environment(environment_id, root, **paths)
    return result


def get_environment(
    environment_id: str,
    registry_path: Path = DEFAULT_REGISTRY,
    repository_root: Path = PROJECT_ROOT,
) -> Environment:
    """Resolve a supported environment or fail with an explicit supported-value list."""
    selected = str(environment_id)
    if selected not in SUPPORTED_ENVIRONMENTS:
        raise EnvironmentRegistryError(
            f"Unsupported environment {environment_id!r}; supported values: env38, env39."
        )
    return load_registry(registry_path, repository_root)[selected]
