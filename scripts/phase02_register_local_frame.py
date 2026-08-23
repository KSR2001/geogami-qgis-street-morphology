from pathlib import Path
from PIL import Image
import shutil
import hashlib
import json


# ============================================================
# Phase 2 — Common local Cartesian reference frame
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SOURCE_DIR = PROJECT_ROOT / "data" / "source_images"
REFERENCE_DIR = PROJECT_ROOT / "data" / "reference"

REFERENCE_DIR.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------
# Common experimental frame
# ------------------------------------------------------------

FRAME_X_MIN = 0.0
FRAME_Y_MIN = 0.0
FRAME_WIDTH = 1000.0
FRAME_HEIGHT = 1208.0

FRAME_X_MAX = FRAME_X_MIN + FRAME_WIDTH
FRAME_Y_MAX = FRAME_Y_MIN + FRAME_HEIGHT


FILES = {
    "env38": SOURCE_DIR / "env38_original.jpg",
    "env39": SOURCE_DIR / "env39_original.jpg",
}


def sha256_file(path: Path) -> str:
    sha = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            sha.update(chunk)

    return sha.hexdigest()


manifest = {
    "coordinate_system": "local_cartesian",
    "crs": None,
    "units": "local_units",
    "extent": {
        "xmin": FRAME_X_MIN,
        "ymin": FRAME_Y_MIN,
        "xmax": FRAME_X_MAX,
        "ymax": FRAME_Y_MAX,
    },
    "rasters": {},
}


for environment, source_path in FILES.items():

    if not source_path.exists():
        raise FileNotFoundError(
            f"Missing source image: {source_path}"
        )

    output_image = (
        REFERENCE_DIR / f"{environment}_reference.jpg"
    )

    shutil.copy2(source_path, output_image)

    with Image.open(source_path) as image:
        width_px, height_px = image.size

    # Pixel dimensions in local Cartesian map units.
    pixel_size_x = FRAME_WIDTH / width_px
    pixel_size_y = FRAME_HEIGHT / height_px

    # ESRI/GDAL JPEG world file:
    #
    # line 1 = pixel X size
    # line 2 = rotation about Y axis
    # line 3 = rotation about X axis
    # line 4 = negative pixel Y size
    # line 5 = X coordinate of upper-left pixel centre
    # line 6 = Y coordinate of upper-left pixel centre
    #
    # The complete raster extent therefore becomes:
    # X = 0 ... 1000
    # Y = 0 ... 1208

    world_file_values = [
        pixel_size_x,
        0.0,
        0.0,
        -pixel_size_y,
        FRAME_X_MIN + (pixel_size_x / 2.0),
        FRAME_Y_MAX - (pixel_size_y / 2.0),
    ]

    world_file = (
        REFERENCE_DIR / f"{environment}_reference.jgw"
    )

    with world_file.open("w", encoding="utf-8") as file:
        for value in world_file_values:
            file.write(f"{value:.12f}\n")

    manifest["rasters"][environment] = {
        "source_file": str(source_path.relative_to(PROJECT_ROOT)),
        "reference_file": str(output_image.relative_to(PROJECT_ROOT)),
        "world_file": str(world_file.relative_to(PROJECT_ROOT)),
        "source_sha256": sha256_file(source_path),
        "pixel_width": width_px,
        "pixel_height": height_px,
        "pixel_size_x_local": pixel_size_x,
        "pixel_size_y_local": pixel_size_y,
    }

    print()
    print(environment)
    print("-" * 50)
    print(f"Source pixels: {width_px} x {height_px}")
    print(f"Local extent:  0–{FRAME_WIDTH} x 0–{FRAME_HEIGHT}")
    print(f"Pixel X size:  {pixel_size_x}")
    print(f"Pixel Y size:  {pixel_size_y}")
    print(f"Reference:     {output_image}")
    print(f"World file:    {world_file}")


manifest_path = (
    REFERENCE_DIR / "phase02_local_frame_manifest.json"
)

with manifest_path.open("w", encoding="utf-8") as file:
    json.dump(
        manifest,
        file,
        indent=2,
    )


print()
print("=" * 60)
print("PHASE 2 LOCAL-FRAME REGISTRATION COMPLETE")
print("=" * 60)
print(f"Manifest: {manifest_path}")