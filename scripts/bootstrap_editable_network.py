"""Create a QGIS-editable working copy from a validated canonical GeoPackage."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from geogami_morphology.io import read_network, sha256_file


def bootstrap(source: Path, output: Path, manifest: Path | None = None) -> dict[str, str]:
    source, output = Path(source), Path(output)
    manifest = Path(manifest) if manifest else output.with_name(f"{output.stem}_bootstrap.json")
    if output.exists() or manifest.exists():
        raise FileExistsError("Editable output or bootstrap manifest already exists; refusing to overwrite possibly edited data.")
    read_network(source)
    source_hash = sha256_file(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)
    output_hash = sha256_file(output)
    if output_hash != source_hash:
        output.unlink(missing_ok=True)
        raise RuntimeError("Editable bootstrap copy is not byte-identical to its source.")
    document = {
        "purpose": "QGIS-editable source network; never treat this file as generated canonical output",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "source_sha256": source_hash,
        "editable": str(output),
        "editable_sha256_at_bootstrap": output_hash,
        "layers": ["nodes", "edges"],
        "overwrite_policy": "refuse",
    }
    with manifest.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return {"editable": str(output), "manifest": str(manifest), "sha256": output_hash}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        print(json.dumps(bootstrap(args.source, args.output, args.manifest), indent=2))
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"BOOTSTRAP FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
