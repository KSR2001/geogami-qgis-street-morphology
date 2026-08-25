# Professor operating manual: Env38 and Env39 street morphology

This is the final operating procedure for the controlled GeoGami Env38/Env39
street-morphology experiment. It is written for a GIS researcher who can use QGIS but
may be new to this repository, Conda, command-line Git, or Jupyter.

The production command-line interface (CLI) is authoritative. QGIS is the geometry
editing interface, Jupyter is for teaching and inspection, and Git/GitHub records
scientific provenance. Do not use a notebook as a substitute for the production CLI.

## Quick reference

Run every command from the repository root after activating `geogami-morphology`.

| Environment | Editable GeoPackage | Dry run | Production run | Canonical latest | Analysis latest | Notebooks |
| --- | --- | --- | --- | --- | --- | --- |
| Env39, grid-like | `data/editable/grid/env39_editable.gpkg` | `python scripts/run_full_analysis.py --environment env39 --input data/editable/grid/env39_editable.gpkg --mode preserve-topology --dry-run` | `python scripts/run_full_analysis.py --environment env39 --input data/editable/grid/env39_editable.gpkg --mode preserve-topology` | `data/canonical/grid/latest.json` | `results/analysis/env39/latest.json` | 01-04 |
| Env38, curvilinear | `data/editable/curvilinear/env38_editable.gpkg` | `python scripts/run_full_analysis.py --environment env38 --input data/editable/curvilinear/env38_editable.gpkg --mode preserve-topology --dry-run` | `python scripts/run_full_analysis.py --environment env38 --input data/editable/curvilinear/env38_editable.gpkg --mode preserve-topology` | `data/canonical/curvilinear/latest.json` | `results/analysis/env38/latest.json` | 05-08 |

## 1. Understand the system

The controlled workflow is:

```text
QGIS editable geometry
    -> version-controlled editable GeoPackage
    -> canonicalization
    -> NetworkX physical graph
    -> OSMnx reciprocal analysis graph
    -> topology metrics
    -> geometry/orientation metrics
    -> integrated metrics
    -> Jupyter inspection
    -> optional Env38-versus-Env39 comparison
```

- **QGIS** edits the spatial realization of an existing street graph.
- **Git/GitHub** preserve the exact editable input and generated research artifacts.
- **The CLI** validates, computes, publishes, and records the reproducible result.
- **Jupyter** reads accepted results for explanation, visualization, and interpretation.

Env39 and Env38 intentionally use the same topology contract: 46 graph nodes and 69
physical streets with the same stable connectivity. Env39 is grid-like and Env38 is
curvilinear. The experiment changes geometry, not topology; neither design is presumed
better.

## 2. Environment file map and protection boundary

The registry `config/environments.yaml` is authoritative for environment paths and
reference images.

| Role | Env39, grid-like | Env38, curvilinear |
| --- | --- | --- |
| Editable input | `data/editable/grid/env39_editable.gpkg` | `data/editable/curvilinear/env38_editable.gpkg` |
| Topology reference | `data/canonical/grid/env39_canonical.gpkg` | `data/canonical/grid/env39_canonical.gpkg` |
| Canonical runs | `data/canonical/grid/runs/` | `data/canonical/curvilinear/runs/` |
| Canonical latest | `data/canonical/grid/latest.json` | `data/canonical/curvilinear/latest.json` |
| Analysis root | `results/analysis/env39/` | `results/analysis/env38/` |
| Reference image | Resolve `environments.env39.reference_image` from `config/environments.yaml` | Resolve `environments.env38.reference_image` from `config/environments.yaml` |

Both topology-reference entries point to Env39's frozen canonical graph because the
scientific design controls connectivity while allowing different spatial geometry.
Reference images are optional visual aids. Resolve their paths from the registry at
the time of use; if a registered file is absent, report the configuration/data drift
rather than guessing another filename or changing scientific data during this workflow.

### File protection table

| Classification | Paths | Rule |
| --- | --- | --- |
| **Professor-editable** | `data/editable/grid/env39_editable.gpkg`<br>`data/editable/curvilinear/env38_editable.gpkg` | Edit only the environment selected for the current branch. |
| **Do not edit directly** | `data/baselines/grid/network_v2.gpkg`<br>`data/canonical/grid/env39_canonical.gpkg`<br>`data/canonical/grid/runs/*`<br>`data/canonical/curvilinear/runs/*`<br>`results/analysis/*`<br>`results/comparison/*`<br>`data/working/curvilinear/env38_working.gpkg` | Frozen inputs, working provenance, and generated research artifacts are immutable. Recreate outputs through the supported CLI, never by hand. |

## 3. One-time Windows setup

Install Git and a Conda distribution first. Then use Anaconda Prompt or PowerShell:

```powershell
git clone https://github.com/KSR2001/geogami-qgis-street-morphology.git
cd geogami-qgis-street-morphology
conda env create --name geogami-morphology --file environment.yml
conda activate geogami-morphology
```

If the environment already exists, update it from the versioned specification:

```powershell
conda env update --name geogami-morphology --file environment.yml --prune
conda activate geogami-morphology
```

Verify the active interpreter and package consistency:

```powershell
where.exe python
python --version
python -c "import sys; print(sys.executable)"
python -c "import osmnx; print(osmnx.__version__)"
python -c "import networkx; print(networkx.__version__)"
python -m pip check
```

The interpreter path must belong to the `geogami-morphology` environment, not
`WindowsApps`. The repository was validated with Python 3.12, OSMnx 2.1, NetworkX
3.6, GeoPandas 1.1, Shapely 2.1, pandas 3.0, NumPy 2.5, pyproj 3.7, JupyterLab 4.4,
and ipykernel 6.30. These describe the validated snapshot; `environment.yml` remains
the installation authority, so do not install packages manually to chase this list.
In Anaconda Prompt, `where python` is equivalent to PowerShell's unambiguous
`where.exe python` command shown above.

### Register the Jupyter kernel

Run once per Windows user account:

```powershell
python -m ipykernel install --user --name geogami-morphology --display-name "GeoGami Morphology"
python -m jupyter lab
```

The accepted kernel name is `geogami-morphology`; its display name is **GeoGami
Morphology**. VS Code can occasionally rewrite notebook kernelspec metadata to
`python3`. Select the accepted kernel and do not commit an unintended kernelspec-only
change.

## 4. Start a safe editing branch

Do not make scientific geometry edits directly on `main`.

```powershell
git switch main
git pull
git status
git switch -c edit-env39-YYYY-MM-DD
```

For Env38, use `git switch -c edit-env38-YYYY-MM-DD`. Before opening QGIS, `git
status` should report a clean working tree. Stop and review unexpected changes rather
than carrying them into a new experiment.

## 5. Open and organize the QGIS project

1. Start QGIS and choose **Project > Open**.
2. Open `qgis/geogami_baselines.qgz`.
3. If the editable layers are absent, choose **Layer > Add Layer > Add Vector Layer**.
4. Browse to the selected editable GeoPackage:
   - Env39: `data/editable/grid/env39_editable.gpkg`
   - Env38: `data/editable/curvilinear/env38_editable.gpkg`
5. Add both `nodes` and `edges`.
6. For each layer, open **Layer Properties > Information** (or **Source**) and verify
   the source begins with `data/editable/`. Never edit a canonical, working, baseline,
   or result path.

Create an obvious `ENV39_EDITABLE` or `ENV38_EDITABLE` layer group. Suggested other
groups are `REFERENCE_IMAGES` and `FROZEN_REFERENCE`. Use distinct styling for the
editable nodes and edges. Turn frozen/canonical layers off while editing unless they
are temporarily needed as a visual reference.

### Configure snapping

Open **Project > Snapping Options**, select **Advanced Configuration**, and configure
only the active editable layers:

| Layer | Enabled | Type | Tolerance |
| --- | --- | --- | --- |
| Editable `nodes` | Yes | Vertex | approximately 10 pixels |
| Editable `edges` | Yes | Vertex | approximately 10 pixels |

Initially keep segment snapping off. For the current preserve-topology procedure also
keep **Topological Editing**, **Snapping on Intersection**, and **Self-snapping** off
unless a separately reviewed edit explicitly justifies one of them. Snapping exists to
make each edge endpoint coincide exactly with its prescribed graph node; it is not
permission to create new junctions.

## 6. Edit geometry without changing topology

### Allowed geometry edits

Under `preserve-topology`, you may:

- move an existing node coordinate;
- reshape an existing physical street;
- move an edge endpoint together with its referenced node;
- add LineString vertices between the endpoints;
- remove unnecessary internal vertices while retaining a valid LineString;
- change curvature, orientation, physical length, and other geometry-derived values.

Do not manually rewrite geometry-derived attributes unless a future validated pipeline
explicitly requires it. The canonical workflow derives or validates geometry.

### Prohibited topology edits

You must not:

- add or delete a graph node or physical street;
- split one canonical street into multiple graph edges;
- merge canonical streets;
- reconnect a street to different endpoints;
- change `node_id`, `edge_id`, `u`, `v`, or `key`;
- introduce an unintended crossing or junction.

The controlled topology is 46 nodes and 69 physical streets. If the research question
requires changed topology, stop: that needs a separate topology-rebuild and scientific
review workflow.

### Graph nodes versus curve vertices

A bend does not require a graph node. A graph node represents controlled network
connectivity and has a stable `node_id`. An internal LineString vertex merely shapes
one street between its existing endpoints. Thus a straight `N001 ----- N002` street
may become a curved LineString while remaining one physical edge from N001 to N002.

### Move a node safely

1. Select the editable `nodes` layer and toggle editing on.
2. Use the Vertex Tool or Move Feature tool to move the existing node.
3. Identify every edge incident to that node.
4. Select the editable `edges` layer and move every incident first/last vertex exactly
   onto the moved node.
5. Preserve every ID and `u/v/key` value.
6. Save both layers and validate before editing a large new area.

Leaving an incident street endpoint at the old coordinate causes an endpoint-node
mismatch and the production gate will stop.

### Reshape a street safely

1. Select the editable `edges` layer and toggle editing on.
2. Select the existing edge with the Vertex Tool.
3. Add or drag internal vertices to follow the intended street form.
4. Keep the first endpoint on its `u` node and the last endpoint on its `v` node.
5. Avoid feature creation, deletion, splitting, or an unintended crossing.
6. Save the layer and inspect the result at a useful zoom.

### Street-network versus non-street objects

This GeoPackage represents the analytical street network. Trees, playgrounds,
buildings, water, landscape objects, decorative elements, and Unity props are not
NetworkX/OSMnx street entities unless a future model explicitly represents them as
such. Moving a tree alone does not alter topology, street length, circuity, orientation
entropy, or fourfold order (`phi`). If moving a non-street object also requires a
street to move, edit the **street geometry** in the appropriate editable GeoPackage.
Unity/non-network placement is a separate environment-design task.

## 7. Save, close, and commit the editable input

Before analysis:

1. Save changes in every edited layer.
2. Toggle editing off and confirm QGIS has no unsaved layer edits.
3. Save the QGIS project only if its configuration was intentionally changed.
4. Close QGIS completely.
5. Confirm there is no active `*.gpkg-wal`, `*.gpkg-shm`, or `*.gpkg-journal` file.

GeoPackages are SQLite databases. Open handles and transaction sidecars can mean that
the saved database is not ready for an atomic, reproducible read. Never delete an
active or unexplained transaction file manually.

The production workflow requires a clean, committed start. Inspect the change:

```powershell
git status --short
```

The intentional data change should primarily be the selected editable GeoPackage. Do
not include transaction sidecars, `.vscode/`, `__pycache__/`, or `*.pyc` files.

For Env39:

```powershell
git add data/editable/grid/env39_editable.gpkg
git commit -m "Update Env39 street geometry"
git status --porcelain
```

For Env38:

```powershell
git add data/editable/curvilinear/env38_editable.gpkg
git commit -m "Update Env38 street geometry"
git status --porcelain
```

`git status --porcelain` must print nothing before a production analysis. If the QGIS
project was deliberately changed, review and commit it separately; never hide an
unrelated dirty path in the geometry commit.

## 8. Validate and run the production pipeline

### Env39

Validate without creating a run or changing a latest pointer:

```powershell
python scripts/run_full_analysis.py --environment env39 --input data/editable/grid/env39_editable.gpkg --mode preserve-topology --dry-run
```

After `FINAL RESULT: DRY RUN PASS`, run production:

```powershell
python scripts/run_full_analysis.py --environment env39 --input data/editable/grid/env39_editable.gpkg --mode preserve-topology
```

### Env38

Validate without creating a run or changing a latest pointer:

```powershell
python scripts/run_full_analysis.py --environment env38 --input data/editable/curvilinear/env38_editable.gpkg --mode preserve-topology --dry-run
```

After `FINAL RESULT: DRY RUN PASS`, run production:

```powershell
python scripts/run_full_analysis.py --environment env38 --input data/editable/curvilinear/env38_editable.gpkg --mode preserve-topology
```

With controlled topology, the result should contain 46 nodes, 69 physical streets,
and 138 reciprocal OSMnx arcs. These are graph counts, not geometry values.

### What the eight gated stages do

1. Record clean-start Git provenance and enforce the frozen baseline guard.
2. Validate the environment label, registered editable input, schema, CRS, and locks.
3. Canonicalize and publish a versioned physical network.
4. Build NetworkX and OSMnx graph representations.
5. Calculate topology metrics.
6. Calculate geometry and orientation metrics.
7. Integrate the accepted metric families and publish machine-readable results.
8. Verify identities, artifacts, provenance, and pointer eligibility.

A failure stops downstream work and returns a non-zero exit status. An incomplete run
cannot replace the analysis latest pointer.

### NetworkX and OSMnx semantics

The NetworkX physical graph is an undirected `MultiGraph` with 69 physical streets.
The OSMnx analysis graph is a `MultiDiGraph` with two reciprocal arcs for each physical
street, hence 138 arcs. The two arcs model opposite directions over one street; they
do not double the physical-street count.

### Local coordinate limitation

GeoGami uses a local Cartesian coordinate system. Lengths are reported in local units,
not asserted metres. Do not apply great-circle distances, geographic bearings,
WGS84/place downloads, a fake `EPSG:4326`, or geographic nearest-node routines.

## 9. Interpret the accepted metric families

| Family | Meaning and examples |
| --- | --- |
| `topology_controlled` | Connectivity-only controls: node/edge counts, degree distribution, dead ends, cycle rank, components, hop shortest paths, and unweighted centrality. These should match between Env38 and Env39. |
| `geometry_weighted_network` | Graph measures whose costs use stored street lengths, including length-weighted shortest paths and centrality. |
| `geometric_morphology` | Physical LineString length, endpoint chord, excess length, and circuity. Edge circuity is street-path length divided by endpoint chord; network circuity uses their aggregate sums. |
| `orientation_morphology` | Axial street-direction distributions, orientation entropy and normalized entropy, and fourfold order `phi`. Higher entropy means a more dispersed distribution; higher `phi` means stronger fourfold axial order. Neither is inherently better. |

Degree counts incident physical streets; a dead end has degree one. Cycle rank counts
independent graph cycles. A shortest path minimizes hops or, when weighted, local
street length. Centrality describes structural position under the stated weighting.
Morphology does not directly measure navigation or human performance; those claims
need independent behavioral evidence.

## 10. Find and inspect outputs

Successful runs are never overwritten:

- Env39 canonical: `data/canonical/grid/runs/<run_id>/`
- Env39 analysis: `results/analysis/env39/<run_id>/`
- Env38 canonical: `data/canonical/curvilinear/runs/<run_id>/`
- Env38 analysis: `results/analysis/env38/<run_id>/`

The convenience pointers identify the newest complete accepted run:

- `data/canonical/grid/latest.json`
- `results/analysis/env39/latest.json`
- `data/canonical/curvilinear/latest.json`
- `results/analysis/env38/latest.json`

Open a latest JSON file in a text editor and follow its `run_id`, `canonical_path`,
`manifest_path`, `analysis_manifest_path`, or `integrated_results_path`. A pointer is
mutable navigation metadata; the referenced versioned directory is the immutable
historical record. Analysis directories contain NetworkX/OSMnx GraphML, topology,
geometry/orientation and integrated CSV/JSON/Markdown results, figures, and manifests.

Within an analysis run, use these exact locations:

- `analysis_graph_manifest.json` records the NetworkX physical `MultiGraph` summary
  and the OSMnx reciprocal `MultiDiGraph` summary;
- the physical-network source is the run-specific canonical GeoPackage plus
  `canonical_nodes.csv` and `canonical_edges.csv` in its canonical run directory;
- `graphs/env39_osmnx.graphml` or `graphs/env38_osmnx.graphml` is the serialized OSMnx
  reciprocal graph;
- `topology/` holds node, edge, summary, method, cross-check, and manifest outputs;
- `geometry/` holds edge geometry, orientation, summary, figure, method, and manifest
  outputs; and
- `integrated/` holds `core_metrics.*`, `comparison_ready_metrics.csv`, the environment
  summary, figures, methodology, and integrated manifest.

## 11. Use the professor notebooks

Launch JupyterLab from the repository root:

```powershell
conda activate geogami-morphology
python -m jupyter lab
```

Choose **GeoGami Morphology**, open the notebook, then choose **Run > Run All Cells**.
Notebook 00 is a shared introduction. Use 01-04 for Env39 and 05-08 for Env38:

1. `00_osmnx_networkx_introduction.ipynb`
2. `01_env39_load_canonical_graph.ipynb`
3. `02_env39_topological_metrics.ipynb`
4. `03_env39_geometry_orientation_metrics.ipynb`
5. `04_env39_integrated_results.ipynb`
6. `05_env38_load_canonical_graph.ipynb`
7. `06_env38_topological_metrics.ipynb`
8. `07_env38_geometry_orientation_metrics.ipynb`
9. `08_env38_integrated_results.ipynb`

The notebooks dynamically resolve the accepted latest run and call the tested package
modules. They are optional for production success. Source notebooks are intentionally
clean; do not commit executed outputs unless repository policy explicitly changes.

## 12. Build the optional formal comparison

The exact routine CLI accepted by `scripts/build_environment_comparison.py` is:

```powershell
python scripts/build_environment_comparison.py
```

Run it only from a clean, committed repository after both `latest.json` pairs identify
accepted analyses. The builder reads those accepted artifacts; it does not re-digitize,
recalculate, or alter either environment. It publishes a new immutable directory at:

```text
results/comparison/env38_vs_env39/env38_vs_env39_<timestamp>_<identity>/
```

There is deliberately no comparison latest pointer and the CLI currently provides no
dry-run or temporary-output option. `--comparison-id` is an advanced optional naming
argument; the `--phase-start-*` flags are provenance overrides for an independently
audited phase, not routine professor options. Never invent an output flag.

The Env38 latest run must also contain a valid Phase 9F independent reproducibility
acceptance (`env38_reproducibility_comparison.json`) for that same run. If a newly
edited Env38 run has not undergone that acceptance, comparison generation must stop;
do not bypass the gate or copy an older audit. Complete the independent reproducibility
review with the project maintainer first.

The comparison is controlled and descriptive, not inferential: it is one accepted
spatial realization per design, not a sample supporting p-values, confidence intervals,
causal claims, or a ranking of the environments.

### Current accepted comparison, concise finding

The accepted versioned package under `results/comparison/env38_vs_env39/` shows that
topology control succeeded. Descriptively, Env38 has higher orientation entropy, lower
fourfold `phi`, greater circuity/excess length, and only a modest difference in mean
length-weighted shortest path. These are morphology observations, not claims that one
design is better. Use the package's `env38_vs_env39_summary.md`, CSV files, JSON files,
figures, and hash manifest for authoritative current values; do not copy a large table
into long-lived documentation.

## 13. Commit generated artifacts and create a pull request

After a successful run, inspect every path:

```powershell
git status --short
```

Stage only the new run directory and its two latest pointers. Replace `<run_id>` with
the ID printed by the successful command.

Env39 example:

```powershell
git add data/canonical/grid/runs/<run_id>
git add data/canonical/grid/latest.json
git add results/analysis/env39/<run_id>
git add results/analysis/env39/latest.json
git commit -m "Record updated Env39 morphology analysis"
```

Env38 example:

```powershell
git add data/canonical/curvilinear/runs/<run_id>
git add data/canonical/curvilinear/latest.json
git add results/analysis/env38/<run_id>
git add results/analysis/env38/latest.json
git commit -m "Record updated Env38 morphology analysis"
```

If a formal comparison was intentionally generated, review and commit its one new
`results/comparison/env38_vs_env39/<comparison_id>/` directory in a separate commit.
There is no comparison latest pointer.

Before pushing:

```powershell
conda run -n geogami-morphology python -m pytest
python scripts/verify_frozen_baselines.py
git diff --check
git status
git push -u origin <branch>
```

Create a GitHub pull request. Review the changed GeoPackage and all generated files;
merge only after tests pass. Do not force-push `main`. After the reviewed pull request
is merged:

```powershell
git switch main
git pull
```

Warnings such as the known optional `GDAL_DATA` lookup warning are not test failures
when the command exits successfully. Any non-zero exit, `FAILED`, or `ERROR` is a
failure and must be resolved before merge.

## 14. Complete worked example: Env39

```powershell
git switch main
git pull
git status
git switch -c edit-env39-YYYY-MM-DD
```

Open `qgis/geogami_baselines.qgz`; verify and edit only the `nodes`/`edges` layers from
`data/editable/grid/env39_editable.gpkg`. Preserve IDs and connectivity. Save layers,
toggle editing off, and close QGIS completely. Then:

```powershell
git status --short
git add data/editable/grid/env39_editable.gpkg
git commit -m "Update Env39 street geometry"
git status --porcelain
python scripts/run_full_analysis.py --environment env39 --input data/editable/grid/env39_editable.gpkg --mode preserve-topology --dry-run
python scripts/run_full_analysis.py --environment env39 --input data/editable/grid/env39_editable.gpkg --mode preserve-topology
```

Follow `data/canonical/grid/latest.json` and `results/analysis/env39/latest.json`.
Optionally inspect notebooks 01-04 with **GeoGami Morphology**, then run the checks in
section 13. Stage the four Env39 run/pointer paths shown there, commit, push the branch,
and create a pull request.

## 15. Complete worked example: Env38

```powershell
git switch main
git pull
git status
git switch -c edit-env38-YYYY-MM-DD
```

Open `qgis/geogami_baselines.qgz`; verify and edit only the `nodes`/`edges` layers from
`data/editable/curvilinear/env38_editable.gpkg`. Preserve IDs and connectivity. Save
layers, toggle editing off, and close QGIS completely. Then:

```powershell
git status --short
git add data/editable/curvilinear/env38_editable.gpkg
git commit -m "Update Env38 street geometry"
git status --porcelain
python scripts/run_full_analysis.py --environment env38 --input data/editable/curvilinear/env38_editable.gpkg --mode preserve-topology --dry-run
python scripts/run_full_analysis.py --environment env38 --input data/editable/curvilinear/env38_editable.gpkg --mode preserve-topology
```

Follow `data/canonical/curvilinear/latest.json` and `results/analysis/env38/latest.json`.
Optionally inspect notebooks 05-08, run the checks in section 13, stage the four Env38
run/pointer paths shown there, commit, push, and create a pull request. A formal
comparison is a later optional step only after the new Env38 result has the required
independent reproducibility acceptance.

## 16. Troubleshooting and safe recovery

### Windows `WinError 32` or a locked GeoPackage

Phase 8C publication avoids known internal reader-lock races and uses bounded retries
for transient sharing violations. A persistent lock usually belongs to QGIS, a file
previewer, antivirus, backup software, or another process. Close QGIS, identify the
external owner, wait until the lock is released, and rerun the unchanged command. Do
not delete an unknown GeoPackage or transaction sidecar.

### Endpoint mismatch

An edge's first or last coordinate does not exactly coincide with its prescribed node.
In QGIS, move that endpoint to the existing referenced node without changing `u`, `v`,
`key`, or IDs. `scripts/sync_env38_edge_endpoints.py` is a specialist Env38 utility:
it defaults to dry-run and changes the editable file only with explicit `--apply`.
Use it only after reviewing its report and retaining a recoverable copy; do not use it
as arbitrary automatic snapping.

### Unintended crossing

A geometry crossing outside an intended canonical node is a geometry-realization
failure. Inspect the reported edges and move their geometry. Do not create a junction
unless a separately reviewed topology redesign is intended.

### Topology-control failure

Likely causes are a node/edge addition or deletion, split, merge, reconnection, or a
changed `node_id`, `edge_id`, `u`, `v`, or `key`. The preserve-topology workflow must
stop. Review the edit against the last intentional commit; do not force it through.

### Dirty-Git failure

`Workflow requires a clean committed repository` means the input state lacks complete
provenance. Run `git status`, review each path, commit intentional editable geometry,
and revert or remove only clearly unintended changes. Never blindly reset valuable
research work.

### Cross-environment input error

For example, `--environment env38` with `env39_editable.gpkg` is intentionally rejected.
Because the networks share topology, silently accepting the wrong file could mislabel
geometry. Use the registered environment/path pair from the quick-reference table.

### Notebook kernel drift

The expected kernel is `geogami-morphology` / **GeoGami Morphology**. If VS Code
rewrites metadata to `python3`, reselect the expected kernel and do not commit the
unintended notebook change.

### `GDAL_DATA` warning

The validated Conda environment can emit a warning about an optional GDAL support file
while GeoPackage reads, writes, and tests still pass. Treat an actual read/write error
or non-zero exit as a failure; reactivate/update the Conda environment and verify the
interpreter before retrying.

### Prominent recovery warning

When research geometry may be uncommitted, do **not** casually use:

```text
git reset --hard
git clean -f
manual deletion of GeoPackage transaction sidecars
```

First close QGIS, inspect `git status`, and create an external backup outside the
repository. Destructive recovery can permanently erase uncommitted scientific work.

## 17. Why this is reproducible

- **Versioned run directories** preserve every accepted execution instead of
  overwriting it.
- **Scientific-content signatures** identify the canonical network meaning.
- **Topology signatures** verify stable IDs and connectivity independently of
  coordinates.
- **Manifests** bind inputs, configuration, software, stages, outputs, and status.
- **Artifact SHA-256 hashes** identify exact published bytes.
- **Clean-start Git provenance** binds production to a committed repository state.
- **Latest pointers** provide convenient navigation and update only after complete
  publication; they are not substitutes for immutable historical directories.

Together these controls distinguish a repeatable computation from an undocumented GIS
edit and make later audit, comparison, and recovery possible.
