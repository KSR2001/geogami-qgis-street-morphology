"""Fail clearly if either accepted Phase 5 Env39 baseline changes."""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from geogami_morphology.guards import FROZEN_BASELINES, verify_frozen_baselines


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
