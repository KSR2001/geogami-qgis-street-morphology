"""Fail clearly if either accepted Phase 5 Env39 baseline changes."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FROZEN_BASELINES = {
    Path("data/baselines/grid/network_v2.gpkg"): "7698544A01EED008E5451F8368703AFD0DBE232D10C9D7ADAA58C13DA57E0289",
    Path("data/canonical/grid/env39_canonical.gpkg"): "212A2AA583BF77304859DB76639B8AD4B6DC458237EEA24FEB60FC228E5D6847",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def verify_frozen_baselines(project_root: Path = PROJECT_ROOT) -> dict[str, str]:
    observed: dict[str, str] = {}
    errors: list[str] = []
    for relative_path, expected in FROZEN_BASELINES.items():
        path = project_root / relative_path
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


def main() -> int:
    try:
        for path, digest in verify_frozen_baselines().items():
            print(f"PASS {path} {digest}")
        return 0
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
