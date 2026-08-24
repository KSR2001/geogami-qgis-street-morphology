"""Read-only guards for accepted frozen GeoGami baseline artifacts."""

from __future__ import annotations

from pathlib import Path

from .io import sha256_file


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FROZEN_BASELINES = {
    Path("data/baselines/grid/network_v2.gpkg"): "7698544A01EED008E5451F8368703AFD0DBE232D10C9D7ADAA58C13DA57E0289",
    Path("data/canonical/grid/env39_canonical.gpkg"): "212A2AA583BF77304859DB76639B8AD4B6DC458237EEA24FEB60FC228E5D6847",
}


def verify_frozen_baselines(project_root: Path = PROJECT_ROOT) -> dict[str, str]:
    """Return observed hashes or fail without modifying either baseline."""
    root = Path(project_root).resolve()
    observed: dict[str, str] = {}
    errors: list[str] = []
    for relative_path, expected in FROZEN_BASELINES.items():
        path = root / relative_path
        if not path.is_file():
            errors.append(f"{relative_path.as_posix()}: missing; expected SHA-256 {expected}")
            continue
        actual = sha256_file(path)
        observed[relative_path.as_posix()] = actual
        if actual != expected:
            errors.append(
                f"{relative_path.as_posix()}: frozen baseline changed; "
                f"expected SHA-256 {expected}, observed {actual}"
            )
    if errors:
        raise RuntimeError("Frozen Phase 5 baseline guard failed:\n" + "\n".join(errors))
    return observed
