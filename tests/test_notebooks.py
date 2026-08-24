from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import shutil
import sys
import unittest

from nbclient import NotebookClient
import nbformat
import jupyter_core.paths as jupyter_paths


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS = ROOT / "notebooks"


class Phase7EThrough7GNotebookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.introduction_path = NOTEBOOKS / "00_osmnx_networkx_introduction.ipynb"
        cls.canonical_path = NOTEBOOKS / "01_env39_load_canonical_graph.ipynb"
        cls.topology_path = NOTEBOOKS / "02_env39_topological_metrics.ipynb"
        cls.geometry_path = NOTEBOOKS / "03_env39_geometry_orientation_metrics.ipynb"
        cls.latest_path = ROOT / "data" / "canonical" / "grid" / "latest.json"
        cls.latest_bytes = cls.latest_path.read_bytes()
        cls._original_path = os.environ.get("PATH", "")
        cls._original_runtime = os.environ.get("JUPYTER_RUNTIME_DIR")
        cls._original_insecure_writes = os.environ.get("JUPYTER_ALLOW_INSECURE_WRITES")
        cls._original_ipython_dir = os.environ.get("IPYTHONDIR")
        cls._original_jupyter_insecure_flag = jupyter_paths.allow_insecure_writes
        cls._original_event_loop_policy = asyncio.get_event_loop_policy()
        # The installed kernelspec intentionally uses generic `python`. Put the
        # active test interpreter first without registering/modifying user kernels.
        os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + cls._original_path
        cls.runtime_dir = ROOT / "tests" / ".tmp" / "jupyter-runtime"
        cls.ipython_dir = ROOT / "tests" / ".tmp" / "ipython"
        cls.runtime_dir.mkdir(parents=True, exist_ok=True)
        cls.ipython_dir.mkdir(parents=True, exist_ok=True)
        os.environ["JUPYTER_RUNTIME_DIR"] = str(cls.runtime_dir)
        os.environ["IPYTHONDIR"] = str(cls.ipython_dir)
        # Windows workspace sandboxes can deny the ACL-tightening syscall even
        # for a private test scratch file. This affects test connection files only.
        os.environ["JUPYTER_ALLOW_INSECURE_WRITES"] = "true"
        # jupyter_core reads this environment setting once at import time.
        jupyter_paths.allow_insecure_writes = True
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        cls._executed_00 = None
        cls._executed_01_root = None
        cls._executed_02_root = None
        cls._executed_03_root = None

    @classmethod
    def tearDownClass(cls):
        os.environ["PATH"] = cls._original_path
        if cls._original_runtime is None:
            os.environ.pop("JUPYTER_RUNTIME_DIR", None)
        else:
            os.environ["JUPYTER_RUNTIME_DIR"] = cls._original_runtime
        if cls._original_insecure_writes is None:
            os.environ.pop("JUPYTER_ALLOW_INSECURE_WRITES", None)
        else:
            os.environ["JUPYTER_ALLOW_INSECURE_WRITES"] = cls._original_insecure_writes
        if cls._original_ipython_dir is None:
            os.environ.pop("IPYTHONDIR", None)
        else:
            os.environ["IPYTHONDIR"] = cls._original_ipython_dir
        jupyter_paths.allow_insecure_writes = cls._original_jupyter_insecure_flag
        asyncio.set_event_loop_policy(cls._original_event_loop_policy)
        shutil.rmtree(cls.runtime_dir, ignore_errors=True)
        shutil.rmtree(cls.ipython_dir, ignore_errors=True)

    @classmethod
    def _execute(cls, path: Path, working_directory: Path):
        notebook = nbformat.read(path, as_version=4)
        client = NotebookClient(
            notebook,
            timeout=300,
            kernel_name="python3",
            resources={"metadata": {"path": str(working_directory)}},
        )
        try:
            return client.execute()
        except Exception as exc:
            executed = [
                cell.id for cell in notebook.cells
                if cell.cell_type == "code" and cell.execution_count is not None
            ]
            raise AssertionError(
                f"Notebook execution stopped after code cells: {executed}"
            ) from exc

    @staticmethod
    def _text(notebook) -> str:
        chunks: list[str] = []
        for cell in notebook.cells:
            for output in cell.get("outputs", []):
                if output.output_type == "stream":
                    chunks.append(output.text)
                elif output.output_type in {"execute_result", "display_data"}:
                    chunks.append(str(output.get("data", {}).get("text/plain", "")))
        return "\n".join(chunks)

    @staticmethod
    def _code(notebook) -> str:
        return "\n".join(cell.source for cell in notebook.cells if cell.cell_type == "code")

    def test_01_source_notebooks_are_valid_clean_nbformat_documents(self):
        for path in (self.introduction_path, self.canonical_path, self.topology_path, self.geometry_path):
            with self.subTest(path=path.name):
                notebook = nbformat.read(path, as_version=4)
                nbformat.validate(notebook)
                self.assertEqual(notebook.metadata.kernelspec.name, "geogami-morphology")
                self.assertEqual(notebook.metadata.kernelspec.display_name, "GeoGami Morphology")
                for cell in notebook.cells:
                    if cell.cell_type == "code":
                        self.assertIsNone(cell.execution_count)
                        self.assertEqual(cell.outputs, [])

    def test_02_introduction_contains_required_teaching_sections_and_executes_offline(self):
        source = self.introduction_path.read_text(encoding="utf-8")
        for phrase in (
            "Research context",
            "What is a graph?",
            "What is NetworkX?",
            "What is OSMnx?",
            "Scientific `MultiGraph`",
            "OSMnx `MultiDiGraph`",
            "69 physical canonical streets = 138 directed analysis arcs",
            "GeoGami Local Cartesian",
            "Reproducible workflow overview",
        ):
            self.assertIn(phrase, source)
        if self.__class__._executed_00 is None:
            self.__class__._executed_00 = self._execute(self.introduction_path, ROOT)
        output = self._text(self.__class__._executed_00)
        self.assertIn("NOTEBOOK_00_OFFLINE_EXECUTION: PASS", output)
        self.assertIn("Internet access: NOT REQUIRED", output)

    def test_03_canonical_notebook_executes_end_to_end_from_repository_root(self):
        if self.__class__._executed_01_root is None:
            self.__class__._executed_01_root = self._execute(self.canonical_path, ROOT)
        output = self._text(self.__class__._executed_01_root)
        for marker in (
            "REPOSITORY_DISCOVERY: PASS",
            "CANONICAL_RUN_RESOLUTION: PASS",
            "CANONICAL_IDENTITY: PASS",
            "GEOMETRY_DERIVED_VALUES: PASS",
            "NETWORKX_VALIDATION: 46 nodes | 69 physical edges | 1 component | PASS",
            "OSMNX_VALIDATION: 46 nodes | 138 directed arcs | 69 physical canonical streets | PASS",
            "RECIPROCAL_EDGE_DEMONSTRATION: PASS",
            "PHYSICAL_NETWORK_VISUALIZATION: PASS",
            "GRAPHML_ROUNDTRIP: 46 nodes | 138 directed arcs | 69 physical IDs | PASS",
            "NOTEBOOK_01_EXECUTION: PASS",
        ):
            self.assertIn(marker, output)

    def test_04_canonical_notebook_uses_latest_run_and_preserves_local_crs(self):
        if self.__class__._executed_01_root is None:
            self.__class__._executed_01_root = self._execute(self.canonical_path, ROOT)
        output = self._text(self.__class__._executed_01_root)
        self.assertIn("env39_20260824T123033467880Z_2046798c1e9c", output)
        self.assertIn("Coordinate system: GeoGami Local Cartesian", output)
        self.assertIn("Units: local units", output)
        self.assertIn("GraphML round-trip validation: PASS", output)

    def test_05_notebook_code_has_no_prohibited_sources_geographic_calls_or_absolute_paths(self):
        introduction = nbformat.read(self.introduction_path, as_version=4)
        canonical = nbformat.read(self.canonical_path, as_version=4)
        topology = nbformat.read(self.topology_path, as_version=4)
        geometry = nbformat.read(self.geometry_path, as_version=4)
        code = self._code(introduction) + "\n" + self._code(canonical) + "\n" + self._code(topology) + "\n" + self._code(geometry)
        for forbidden in (
            "data/editable/grid/env39_editable.gpkg",
            "data/baselines/grid/network_v2.gpkg",
            "add_edge_lengths(",
            "add_edge_bearings(",
            "graph_from_place(",
        ):
            self.assertNotIn(forbidden, code)
        self.assertIsNone(re.search(r"[A-Za-z]:[\\/]", code))

    def test_06_notebook_contains_no_phase7f_or_phase7g_metric_computation(self):
        notebook = nbformat.read(self.canonical_path, as_version=4)
        code = self._code(notebook)
        for metric_call in (
            "betweenness_centrality(",
            "closeness_centrality(",
            "diameter(",
            "orientation_entropy(",
            "circuity",
            "rose",
        ):
            self.assertNotIn(metric_call, code)

    def test_07_clean_rerun_from_notebooks_directory_succeeds(self):
        rerun = self._execute(self.canonical_path, NOTEBOOKS)
        output = self._text(rerun)
        self.assertIn("REPOSITORY_DISCOVERY: PASS", output)
        self.assertIn("NOTEBOOK_01_EXECUTION: PASS", output)

    def test_08_notebook_execution_does_not_modify_latest_pointer(self):
        self.assertEqual(self.latest_path.read_bytes(), self.latest_bytes)

    def test_09_topology_notebook_contains_all_professor_facing_sections(self):
        source = self.topology_path.read_text(encoding="utf-8")
        for number, title in enumerate((
            "Purpose",
            "Selected canonical run",
            "Scientific graph representations",
            "Metric configuration",
            "Network identity",
            "Degree and junction structure",
            "NetworkX / OSMnx street-count cross-check",
            "Connectedness and cycle rank",
            "Bridges and articulation points",
            "Shortest-path structure",
            "Centrality",
            "Compact result table",
            "Minimal figures",
            "Exported artifacts",
            "Interpretation",
            "Final reproducibility summary",
        ), start=1):
            self.assertIn(f"## {number}. {title}", source)

    def test_10_topology_notebook_executes_end_to_end_from_clean_kernel(self):
        if self.__class__._executed_02_root is None:
            self.__class__._executed_02_root = self._execute(self.topology_path, ROOT)
        output = self._text(self.__class__._executed_02_root)
        for marker in (
            "CANONICAL_RUN_RESOLUTION: PASS",
            "GRAPH_REPRESENTATIONS: PASS",
            "NETWORK_IDENTITY_AND_HANDSHAKE: PASS",
            "DEGREE_STRUCTURE: PASS",
            "OSMNX_NODE_BY_NODE_CROSSCHECK: PASS",
            "CONNECTEDNESS_AND_CYCLE_RANK: PASS",
            "HEADLESS_DEGREE_FIGURE: PASS",
            "EXPORTED_ARTIFACTS: PASS",
            "NOTEBOOK_02_EXECUTION: PASS",
        ):
            self.assertIn(marker, output)

    def test_11_topology_notebook_calls_module_and_excludes_phase7g_metrics(self):
        notebook = nbformat.read(self.topology_path, as_version=4)
        code = self._code(notebook)
        self.assertIn("analyze_topology(", code)
        for forbidden in (
            "orientation_entropy(",
            "add_edge_bearings(",
            "add_edge_lengths(",
            "circuity_avg(",
            "graph_from_place(",
        ):
            self.assertNotIn(forbidden, code)

    def test_12_topology_notebook_execution_does_not_modify_latest_pointer(self):
        if self.__class__._executed_02_root is None:
            self.__class__._executed_02_root = self._execute(self.topology_path, ROOT)
        self.assertEqual(self.latest_path.read_bytes(), self.latest_bytes)

    def test_13_geometry_notebook_contains_all_professor_facing_sections_and_formulas(self):
        source = self.geometry_path.read_text(encoding="utf-8")
        for number, title in enumerate((
            "Purpose",
            "Why this phase uses planar geometry",
            "Selected canonical run/provenance",
            "Coordinate system and units",
            "Edge geometry length",
            "Chord length",
            "Circuity definitions",
            "Edge-chord orientation",
            "LineString-segment orientation",
            "Orientation histogram and binning",
            "Shannon orientation entropy",
            "Fourfold orientation order phi",
            "Geometry metric summary",
            "Scientific figures",
            "Exported artifacts",
            "Interpretation for grid-like Env39",
            "How these metrics will later compare to Env38",
            "Reproducibility summary",
        ), start=1):
            self.assertIn(f"## {number}. {title}", source)
        for formula in ("L_i/D_i", "sum L_i", "H=-", "exp(i4"):
            self.assertIn(formula, source)

    def test_14_geometry_notebook_executes_end_to_end_from_clean_kernel(self):
        if self.__class__._executed_03_root is None:
            self.__class__._executed_03_root = self._execute(self.geometry_path, ROOT)
        output = self._text(self.__class__._executed_03_root)
        for marker in (
            "CANONICAL_GEOMETRY_RESOLUTION: PASS",
            "PLANAR_CONFIGURATION: PASS",
            "EDGE_LENGTH_METRICS: PASS",
            "CHORD_LENGTH_METRICS: PASS",
            "PLANAR_CIRCUITY: PASS",
            "CHORD_ORIENTATION: PASS",
            "SEGMENT_ORIENTATION: PASS",
            "AXIAL_HISTOGRAMS: PASS",
            "SHANNON_ENTROPY: PASS",
            "FOURFOLD_ORIENTATION_ORDER: PASS",
            "HEADLESS_GEOMETRY_FIGURES: PASS",
            "EXPORTED_GEOMETRY_ARTIFACTS: PASS",
            "NOTEBOOK_03_EXECUTION: PASS",
        ):
            self.assertIn(marker, output)

    def test_15_geometry_notebook_calls_module_and_has_no_competing_or_geographic_algorithms(self):
        notebook = nbformat.read(self.geometry_path, as_version=4)
        code = self._code(notebook)
        self.assertIn("analyze_geometry(", code)
        for forbidden in (
            "atan2(",
            "add_edge_bearings(",
            "add_edge_lengths(",
            "plot_orientation(",
            "graph_from_place(",
        ):
            self.assertNotIn(forbidden, code)

    def test_16_geometry_notebook_execution_preserves_latest_and_phase7f_outputs(self):
        topology_dir = ROOT / "results" / "analysis" / "env39" / "env39_20260824T123033467880Z_2046798c1e9c" / "topology"
        before = {path.name: path.read_bytes() for path in topology_dir.iterdir() if path.is_file()}
        if self.__class__._executed_03_root is None:
            self.__class__._executed_03_root = self._execute(self.geometry_path, ROOT)
        after = {path.name: path.read_bytes() for path in topology_dir.iterdir() if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(self.latest_path.read_bytes(), self.latest_bytes)


if __name__ == "__main__":
    unittest.main()
