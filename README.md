# GeoGami QGIS street morphology

## Professor quick start

1. Open `qgis/geogami_baselines.qgz` in QGIS.
2. Edit only `data/editable/grid/env39_editable.gpkg`.
3. Save all edits and close QGIS completely.
4. From the repository root, run:

```powershell
conda activate geogami-morphology
python scripts/run_full_analysis.py --environment env39 --input data/editable/grid/env39_editable.gpkg --mode preserve-topology
```

Optionally inspect the same analysis interactively with `python -m jupyter lab`.
The complete installation, dry-run, troubleshooting, results, and recovery guide is
in [`docs/professor_workflow.md`](docs/professor_workflow.md).

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

## Phase 7C: versioned reproducible runs

The normal professor workflow now creates a complete, immutable run directory and
updates a cross-platform JSON pointer to the latest successful run:

```powershell
python scripts/run_canonical_pipeline.py `
  --environment env39 `
  --input data/editable/grid/env39_editable.gpkg `
  --reference-canonical data/canonical/grid/env39_canonical.gpkg `
  --runs-root data/canonical/grid/runs `
  --mode preserve-topology
```

Successful run IDs combine the environment, a UTC provenance timestamp, and the
first 12 characters of the scientific-content signature. Each run contains the
canonical GeoPackage, manifest, validation report, deterministic CSV exports,
signature files, and header-valid endpoint-adjustment and diagnostics tables.

`data/canonical/grid/latest.json` is a regular JSON file rather than a symlink. It
is replaced atomically only after the canonical network, manifest, validation and
all required artifacts have passed verification. Failed builds retain diagnostics
under `runs/failed/` and never change `latest.json`.

### Three distinct identities

- **File SHA-256** hashes exact file bytes. It detects byte-identical GeoPackage
  containers and can change because of scientifically irrelevant SQLite/GDAL
  metadata.
- **Scientific-content signature** hashes deterministic scientific content: CRS,
  ordered canonical IDs, exact `float.hex()` coordinates, connectivity fields and
  ordered geometry vertices. It excludes GeoPackage metadata, derived attributes
  and source-FID provenance.
- **Topology signature** hashes only the established sorted normalized `(u,v,key)`
  tuples. Geometry changes therefore do not change topology identity.

The authoritative geospatial artifact is always the run's canonical GeoPackage;
CSV files are professor-readable explanatory exports. The earlier explicit
`--output` command remains available for backward-compatible Phase 7B workflows,
but it does not create a Phase 7C manifest or update `latest.json`.

## Phase 7D: canonical analysis graphs

Build the analysis representations from the latest verified Phase 7C run:

```powershell
python scripts/build_analysis_graphs.py `
  --environment env39 `
  --canonical-run latest
```

The adapter independently verifies the canonical file SHA-256, scientific-content
signature, topology signature, successful validation, and manifest paths before
loading any graph. An explicit accepted canonical GeoPackage can instead be selected
with `--canonical <path>` for research reproduction and tests. Editable and Phase 5
source GeoPackages are never analysis inputs.

The scientific graph is an undirected NetworkX `MultiGraph` with one edge per
physical canonical street. The OSMnx-compatible graph is a `MultiDiGraph` with two
geometry-oriented reciprocal arcs per physical street. Thus Env39 has 69 physical
streets but 138 directed arcs; directed arc count must not be reported as physical
street count.

Outputs are published under
`results/analysis/<environment>/<canonical_run_id>/`, including portable GraphML and
an `analysis_graph_manifest.json`. Length is always recomputed as `LineString.length`
in GeoGami local Cartesian units. The adapter does not assign EPSG:4326, calculate
geographic bearings, or use great-circle edge-length functions.

## Phase 7E: Jupyter Lab notebooks

The professor-facing notebooks live in `notebooks/` and use the same
`geogami-morphology` environment as the tested pipeline. After creating or updating
that environment, register its selectable kernel once:

```powershell
conda activate geogami-morphology
python -m ipykernel install --user --name geogami-morphology --display-name "GeoGami Morphology"
python -m jupyter lab
```

Inside Jupyter Lab, open `notebooks/` and select the **GeoGami Morphology** kernel.
The professor-facing order is:

1. `00_osmnx_networkx_introduction.ipynb` — offline conceptual introduction;
2. `01_env39_load_canonical_graph.ipynb` — canonical and graph validation;
3. `02_env39_topological_metrics.ipynb` — topology and weighted-network results;
4. `03_env39_geometry_orientation_metrics.ipynb` — geometry and orientation results;
5. `04_env39_integrated_results.ipynb` — principal integrated Env39 results.

For routine analysis, normally choose **Restart Kernel and Run All Cells** for
Notebooks 01, 02, 03, and 04 in that order. Notebook 00 is introductory and can be
revisited whenever a conceptual refresher is useful.

Without activating Conda, the deterministic alternatives are:

```powershell
conda run -n geogami-morphology python -m jupyter lab
conda run -n geogami-morphology python -m ipykernel install --user --name geogami-morphology --display-name "GeoGami Morphology"
```

Verify the active environment and core versions with:

```powershell
python -c "import osmnx, networkx, jupyterlab, ipykernel, nbclient, nbformat; print(osmnx.__version__, networkx.__version__, jupyterlab.__version__, ipykernel.__version__, nbclient.__version__, nbformat.__version__)"
```

On Windows, if bare `python` unexpectedly opens Microsoft Store Python or resolves
through `WindowsApps`, run `where python`. The expected interpreter path must belong
to the `geogami-morphology` environment. Use `conda run -n geogami-morphology
python ...` as the reliable fallback.

Notebook 01 can be executed headlessly without overwriting its committed source:

```powershell
python -m jupyter nbconvert --execute --to notebook --ExecutePreprocessor.timeout=300 `
  --output-dir results/notebooks `
  --output 01_env39_load_canonical_graph.executed `
  notebooks/01_env39_load_canonical_graph.ipynb
```

## Phase 7F: reproducible topology metrics

Calculate the focused topology metric set from the latest independently verified
canonical run with:

```powershell
python scripts/run_topology_metrics.py `
  --environment env39 `
  --canonical-run latest
```

The command reads the versioned options in `config/metrics.yaml`, constructs the
validated Phase 7D physical and reciprocal graph representations, and publishes
stable tables and provenance under
`results/analysis/env39/<canonical_run_id>/topology/`. The complementary
`topology_analysis_manifest.json` records all three canonical identities, both the
canonical publication and analysis Git states, the configuration SHA-256, software
versions, and output hashes. It does not replace the Phase 7D graph manifest.

Open `notebooks/02_env39_topological_metrics.ipynb` in the **GeoGami Morphology**
kernel and choose **Restart Kernel and Run All Cells** for the professor-facing
walkthrough. To execute it headlessly without changing the committed source:

```powershell
python -m jupyter nbconvert --execute --to notebook --ExecutePreprocessor.timeout=300 `
  --output-dir results/notebooks `
  --output 02_env39_topological_metrics.executed `
  notebooks/02_env39_topological_metrics.ipynb
```

Phase 7F keeps three concepts distinct:

| Metric type | Examples | Weight and units |
|---|---|---|
| Topology | degree, cycles, bridges, articulation, hop paths | unweighted; counts, proportions, or hops |
| Weighted network distance | shortest paths, weighted betweenness and closeness | canonical `length`; local units or `1 / local unit` |
| Future geometric morphology (Phase 7G) | circuity, orientation, entropy, curvature | deliberately not calculated in Phase 7F |

The OSMnx cross-check counts 69 undirected physical street segments from the
reciprocal 138-arc representation and verifies every node's OSMnx street count
against its NetworkX physical degree. No geographic distance, bearing, nearest-node,
or fake WGS84 operation is used.

## Phase 7G: planar geometry and orientation

Calculate the geometry metrics for the same verified canonical run with:

```powershell
python scripts/run_geometry_metrics.py `
  --environment env39 `
  --canonical-run latest
```

Results and deterministic SVG figures are written beneath
`results/analysis/env39/<canonical_run_id>/geometry/`. The complementary geometry
manifest records canonical identities, configuration and artifact hashes, Git state,
software versions, orientation conventions, and proof that the Phase 7F outputs were
not changed.

All calculations are planar in **GeoGami Local Cartesian** coordinates. Lengths are
in **local units, not metres**. For physical edge `i`, the implementation defines:

- geometry length `L_i` as recomputed `LineString.length`;
- chord `D_i` as Euclidean distance between canonical `u` and `v` node Points;
- edge circuity as `L_i / D_i` for positive chords;
- mean edge circuity as `mean(L_i / D_i)`;
- network circuity as `sum(L_i) / sum(D_i)`.

Chord orientation supplies one equal-weight endpoint axis per physical street.
Segment orientation instead uses every positive consecutive LineString segment,
weighted by its local length, so it retains local directions inside bent streets.
Both use north-clockwise axial angles over `[0°,180°)` and 36 five-degree bins.
Values within `1e-9°` of an exact boundary are snapped before half-open binning so
floating-point representations of the same cardinal axis are not split.
Shannon entropy is `H = -sum(p_i ln p_i)`, with `H / ln(36)` also reported. Fourfold
order is `phi = |sum(w_i exp(i 4 theta_i)) / sum(w_i)|`.

Open `notebooks/03_env39_geometry_orientation_metrics.ipynb` in the **GeoGami
Morphology** kernel and choose **Restart Kernel and Run All Cells**, or execute a
copy headlessly:

```powershell
python -m jupyter nbconvert --execute --to notebook --ExecutePreprocessor.timeout=300 `
  --output-dir results/notebooks `
  --output 03_env39_geometry_orientation_metrics.executed `
  notebooks/03_env39_geometry_orientation_metrics.ipynb
```

OSMnx geographic bearing, great-circle edge-length, geographic nearest-node,
orientation-plot, and fake WGS84 workflows are deliberately excluded.

## Phase 7H: integrated research results

Build the comparison-ready Env39 package only after the accepted Phase 7F and Phase
7G outputs exist for the same canonical run:

```powershell
python scripts/build_integrated_results.py `
  --environment env39 `
  --canonical-run latest
```

The integration command verifies the canonical identity and upstream manifest hashes,
then reads the stored topology, geometry, and orientation summaries. It does not
reimplement any scientific metric. Outputs are written to
`results/analysis/env39/<canonical_run_id>/integrated/` and include:

- `comparison_ready_metrics.csv`, a long-form 39-metric table with stable,
  environment-independent metric IDs;
- `core_metrics.csv` and `core_metrics.json`, the concise 19-metric research set;
- `integrated_methodology.json`, including family definitions, selection rationale,
  controlled-comparison rules, and the historical-methodology note;
- `integrated_analysis_manifest.json`, linking canonical, Phase 7D, Phase 7F, Phase
  7G, configuration, Git, software, figure, and output hashes;
- `env39_baseline_summary.md`, the machine-produced one-page baseline; and
- two deterministic integrated SVG views, with three accepted Phase 7G figures
  referenced in place to avoid duplication.

Every row is classified as `topology_controlled`, `geometry_weighted_network`,
`geometric_morphology`, or `orientation_morphology`. A later Env38 run can populate
the identical schema. Identical topology-control values will check that frozen
connectivity was maintained; geometric, orientation, and length-weighted routing
values may differ with geometric realization. This design supports interpretation
within the controlled experiment and does not by itself establish broader causality.

Open `notebooks/04_env39_integrated_results.ipynb` for the principal professor-facing
baseline. It explains what every result family means, its units and population,
whether it depends on topology or geometry, and whether it may change for Env38. No
Env38 values or placeholder artifacts are created in Phase 7H.
