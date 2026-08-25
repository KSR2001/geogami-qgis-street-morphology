# Notebook guide

- `00_osmnx_networkx_introduction.ipynb`: optional offline introduction.
- `01_env39_load_canonical_graph.ipynb`: load and validate the canonical graph.
- `02_env39_topological_metrics.ipynb`: inspect topology and weighted-network metrics.
- `03_env39_geometry_orientation_metrics.ipynb`: inspect planar geometry and orientation.
- `04_env39_integrated_results.ipynb`: inspect the principal integrated Env39 baseline.
- `05_env38_load_canonical_graph.ipynb`: load and validate the accepted Env38 canonical graph.
- `06_env38_topological_metrics.ipynb`: inspect accepted Env38 topology and weighted-network metrics.
- `07_env38_geometry_orientation_metrics.ipynb`: inspect accepted Env38 planar geometry and orientation.
- `08_env38_integrated_results.ipynb`: inspect the integrated accepted Env38 scientific baseline.

For Env39, run Notebooks 01 through 04. For Env38, run Notebooks 05 through 08.
Use the **GeoGami Morphology** kernel throughout; Notebook 00 is the shared optional
introduction. The notebooks and `scripts/run_full_analysis.py` call the same tested
scientific modules; notebook execution is optional after the CLI succeeds. The Env38
notebooks resolve the currently accepted canonical and analysis runs through their
environment-registry `latest.json` pointers rather than embedding a run identifier.
