# GeoGami QGIS street morphology

## Data editing boundary

**FROZEN — DO NOT EDIT IN QGIS:**

- `data/baselines/grid/network_v2.gpkg`
- `data/canonical/grid/env39_canonical.gpkg`

These are byte-frozen Phase 5 scientific artifacts. Check them at any time with:

```powershell
python scripts/verify_frozen_baselines.py
```

**PROFESSOR EDITS ONLY:**

- `data/editable/grid/env39_editable.gpkg`

Generated files under `data/canonical/grid/runs/` are pipeline outputs and must not
be edited manually.

## Phase 7B: QGIS to canonical GeoPackage

The reusable workflow keeps four stages separate:

1. `data/editable/...` is professor-edited source data.
2. validation compares that source with a supplied canonical topology contract.
3. `data/canonical/.../runs/<run_id>/...` is generated output and must not be edited manually.
4. later analysis notebooks consume the generated canonical `nodes` and `edges` layers.

The initial Env39 editable file was copied byte-for-byte from
`data/canonical/grid/env39_canonical.gpkg`. Its adjacent bootstrap manifest records the
exact source and SHA-256. The bootstrap command refuses to overwrite an existing file,
so it cannot silently replace later professor edits.

After editing `data/editable/grid/env39_editable.gpkg` in QGIS, save and close QGIS,
choose a unique run ID, and run:

```powershell
python scripts/run_canonical_pipeline.py `
  --environment env39 `
  --input data/editable/grid/env39_editable.gpkg `
  --reference-canonical data/canonical/grid/env39_canonical.gpkg `
  --output data/canonical/grid/runs/<run_id>/env39_canonical.gpkg `
  --mode preserve-topology
```

On success, the output GeoPackage contains professor-facing `nodes` and `edges`
layers. `x`, `y`, `degree`, `length_local`, and `vertex_count` are recalculated.
The sibling `env39_canonical_qa` directory contains JSON, CSV, and text diagnostics.
On failure, diagnostics remain readable, the command exits non-zero, and no candidate
is published.

Preserve-topology mode permits geometry and node-coordinate changes but requires the
exact reference `node_id` set and exact per-`edge_id` `(u, v, key)` values. It rejects
invalid or self-intersecting lines, unintended crossings, changed identities or
connectivity, geographic/mismatched CRS definitions, reversed provenance orientation,
and endpoint discrepancies over `1e-6` local units. Smaller endpoint-only discrepancies
are corrected in the candidate and reported; the editable source is never changed.

The Phase 5 Env39 builder and frozen baseline remain independent of this workflow.
