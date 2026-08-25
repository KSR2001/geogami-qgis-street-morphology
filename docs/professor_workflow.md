# Professor workflow: GeoGami street-morphology analysis

## 1. Purpose

This workflow analyzes synthetic GeoGami street networks with QGIS, GeoPandas,
NetworkX, OSMnx, and JupyterLab. It is designed for the controlled Env38/Env39
experiment in which graph topology is held constant while street geometry may change.

The command-line workflow produces all machine-readable results. Jupyter notebooks use
the same tested Python modules for teaching, inspection, and visual interpretation.

## 2. First-time installation

From Anaconda Prompt or PowerShell in the repository root, create the tested Conda
environment:

```powershell
conda env create --name geogami-morphology --file environment.yml
```

If the environment already exists, update it from the versioned specification:

```powershell
conda env update --name geogami-morphology --file environment.yml --prune
```

## 3. Activate the environment

```powershell
conda activate geogami-morphology
```

## 4. Verify the interpreter

```powershell
where.exe python
python -c "import sys; print(sys.executable)"
```

The displayed interpreter must belong to the `geogami-morphology` Conda environment.
If it points to `WindowsApps`, Microsoft Store Python is shadowing Conda. Reactivate
the environment or use the deterministic form `conda run -n geogami-morphology
python ...`.

## 5. Register the Jupyter kernel

This is required once per user account:

```powershell
python -m ipykernel install --user --name geogami-morphology --display-name "GeoGami Morphology"
```

## 6. Open QGIS

Open the repository project:

```text
qgis/geogami_baselines.qgz
```

## 7. Which layer to edit

Edit only:

```text
data/editable/grid/env39_editable.gpkg
```

Do not edit:

```text
data/baselines/grid/network_v2.gpkg
data/canonical/grid/env39_canonical.gpkg
data/canonical/grid/runs/<run_id>/*
```

The first two are frozen scientific artifacts. Versioned canonical runs are generated
outputs and must remain immutable.

## 8. Allowed QGIS edits

The supported mode is `preserve-topology`. You may change node positions, street
curvature, LineString geometry, internal vertices, edge lengths, and orientations.

You must preserve node IDs, edge IDs, `u/v/key` connectivity, component structure, and
degree structure. Adding/deleting streets or nodes, changing connectivity, or
splitting/merging canonical streets will fail validation.

Topology-changing QGIS edits require a separate topology-rebuild workflow and are not
part of the controlled Env38/Env39 experiment. Phase 7I does not implement such a
workflow.

## 9. Save procedure

1. Save all edited layer changes in QGIS.
2. Save the QGIS project if its presentation changed.
3. Close QGIS completely.

Closing QGIS matters because a GeoPackage is a SQLite database. The analysis refuses
to run when `.gpkg-wal`, `.gpkg-shm`, or `.gpkg-journal` files are present. It never
deletes these files or assumes that they are stale.

## 10. Optional dry run

From the repository root:

```powershell
python scripts/run_full_analysis.py --environment env39 --input data/editable/grid/env39_editable.gpkg --mode preserve-topology --dry-run
```

The dry run verifies the environment label, input, layers, fields, CRS, frozen hashes,
QGIS lock state, and metrics configuration. It prints the planned stages and output
locations but creates no run, calculates no metrics, and changes no latest pointer.

## 11. Run the complete analysis

```powershell
python scripts/run_full_analysis.py --environment env39 --input data/editable/grid/env39_editable.gpkg --mode preserve-topology
```

The deterministic Conda alternative is:

```powershell
conda run -n geogami-morphology python scripts/run_full_analysis.py --environment env39 --input data/editable/grid/env39_editable.gpkg --mode preserve-topology
```

Each successful QGIS-edit run creates a new canonical run. For a verified canonical run
that does not yet have analysis outputs, the advanced analysis-only form is:

```powershell
python scripts/run_full_analysis.py --environment env39 --canonical-run latest --mode preserve-topology
```

## 12. Interpret PASS/FAIL

The command is strictly gated:

1. frozen baseline guard;
2. editable input validation;
3. canonical network build;
4. NetworkX/OSMnx graph build;
5. topology metrics;
6. geometry/orientation metrics;
7. integrated results;
8. final cross-stage verification.

`PASS` means that stage completed and its required identity checks succeeded. `FAIL`
stops the command immediately; downstream stages are not run and the process exits
non-zero. A failed or incomplete analysis never updates the analysis latest pointer.

The clean-Git requirement applies at workflow entry: committed code, configuration,
documentation, notebooks, tests, frozen inputs, and the selected editable GeoPackage
must have no uncommitted change when execution starts. The end-to-end manifest records
this immutable `workflow_start_git` snapshot separately from `workflow_end_git`.
Versioned canonical and analysis artifacts may legitimately make the post-run tree
dirty when those research outputs are tracked. They are accepted only when every
changed path belongs to the actual new run or its two latest pointers; any unexpected
source, configuration, frozen-input, or editable-input change still fails final
verification.

Repository text bytes are governed by `.gitattributes`: source, configuration,
notebook, documentation, GraphML, SVG, and tabular research artifacts use LF on every
platform, including Windows installations with `core.autocrlf=true`. GeoPackages, QGIS
projects, and raster images are explicitly binary and are never subject to line-ending
conversion. Artifact SHA-256 values continue to identify exact file bytes; validation
does not normalize content while hashing.

## 13. Where results are stored

Canonical publication:

```text
data/canonical/grid/runs/<canonical_run_id>/
```

Analysis publication:

```text
results/analysis/env39/<canonical_run_id>/
```

The analysis directory contains GraphML, topology results, geometry/orientation
results, integrated CSV/JSON/Markdown results, figures, stage manifests, and
`end_to_end_analysis_manifest.json`. After complete success only,
`results/analysis/env39/latest.json` points to that complete analysis.

The command prints the exact canonical, manifest, GraphML, result-directory, core CSV,
comparison CSV, summary, and notebook paths at completion.

## 14. Open JupyterLab

```powershell
python -m jupyter lab
```

Select the **GeoGami Morphology** kernel. The CLI and notebooks are not separate
scientific implementations: both call the same tested package modules. The CLI is for
automation and reproduction; Jupyter is for teaching, inspection, and visual
interpretation. You do not need to rerun notebooks to produce machine-readable results
after the full-analysis command passes.

## 15. Notebook order

1. `00_osmnx_networkx_introduction.ipynb` — optional introduction
2. `01_env39_load_canonical_graph.ipynb` — canonical graph
3. `02_env39_topological_metrics.ipynb` — topology metrics
4. `03_env39_geometry_orientation_metrics.ipynb` — geometry/orientation metrics
5. `04_env39_integrated_results.ipynb` — integrated baseline

For routine inspection, run 01 through 04. Notebook 00 is optional.

## 16. Routine workflow

- Activate `geogami-morphology`.
- Open `qgis/geogami_baselines.qgz`.
- Edit only `data/editable/grid/env39_editable.gpkg` geometry.
- Save the layer and project; close QGIS completely.
- Run the optional `--dry-run` command.
- Run the one-command complete analysis.
- Confirm `FINAL RESULT: PASS` and use the printed output paths.
- Optionally inspect Notebooks 01–04 in JupyterLab.

## 17. Troubleshooting

### Wrong Python interpreter

Run `where.exe python` and `python -c "import sys; print(sys.executable)"`. Avoid the
`WindowsApps` interpreter; reactivate Conda or use `conda run -n
geogami-morphology`.

### QGIS still open or WAL/SHM/journal detected

Save all edits and close every QGIS window. The workflow deliberately does not delete
SQLite sidecars. If a sidecar remains, inspect the situation in QGIS rather than
deleting it automatically.

### Topology-control failure

Confirm no node/street was added, deleted, split, merged, or reconnected. Restore the
expected IDs and `u/v/key` connectivity. A topology-rebuild workflow is outside this
experiment.

### Endpoint mismatch

Ensure each edge endpoint coincides with its referenced `u` and `v` node. Only tiny
endpoint discrepancies within the configured tolerance can be corrected in the
generated candidate; the editable source is never changed.

### Unintended crossing or overlap

Inspect the reported edge IDs in QGIS. Move geometry so streets do not cross, overlap,
or form an unsplit intersection unless that structure is represented by the controlled
topology.

### Jupyter uses the wrong kernel

Register the kernel as shown above and select **GeoGami Morphology** in JupyterLab.

### `GDAL_DATA` warning

The tested Conda environment can emit a warning about optional GDAL support files while
GeoPackage tests still pass. If a read/write operation actually fails, reactivate or
update the Conda environment and verify that all packages came from that environment.

### OSMnx local-coordinate limitation

GeoGami uses a local Cartesian CRS and local units, not WGS84 or metres. Do not apply
OSMnx geographic bearing, great-circle distance, place-download, or nearest-node
workflows to these graphs. The reciprocal OSMnx graph has 138 directed arcs for 69
physical streets; directed arcs must never be reported as physical-street count.

## 18. Recoverability

Successful canonical and analysis runs are versioned by canonical run ID. Old
successful runs are not overwritten. The canonical latest pointer updates only after
canonical publication succeeds; the separate analysis latest pointer updates only
after every analysis stage and final cross-stage verification succeeds. Failed runs
cannot replace the latest complete analysis.
