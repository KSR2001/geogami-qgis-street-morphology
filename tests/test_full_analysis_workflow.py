from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from geogami_morphology.graph import resolve_canonical_run
from geogami_morphology.guards import verify_frozen_baselines
from geogami_morphology.io import sha256_file
from geogami_morphology.workflow import (
    WorkflowError,
    preflight_editable_input,
    qgis_lock_files,
    run_full_analysis,
)
from run_full_analysis import parse_args


ACCEPTED_RUN = "env39_20260824T123033467880Z_2046798c1e9c"
ACCEPTED_ANALYSIS = ROOT / "results" / "analysis" / "env39" / ACCEPTED_RUN
FROZEN_BASELINE_SHA = "7698544A01EED008E5451F8368703AFD0DBE232D10C9D7ADAA58C13DA57E0289"
CANONICAL_SHA = "212A2AA583BF77304859DB76639B8AD4B6DC458237EEA24FEB60FC228E5D6847"
CLEAN_GIT = {
    "repository": ROOT.name,
    "repository_root": ".",
    "remote_origin": "test-origin",
    "branch": "test-clean-branch",
    "commit_sha": "1" * 40,
    "dirty": False,
    "changed_paths": [],
}


def directory_hashes(path: Path) -> dict[str, str]:
    return {
        item.relative_to(path).as_posix(): sha256_file(item)
        for item in path.rglob("*") if item.is_file()
    }


class Phase7IFullWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ROOT / "tests" / ".tmp" / f"phase7i-{uuid.uuid4().hex}"
        cls.root.mkdir(parents=True)
        cls.input = cls.root / "env39_editable.gpkg"
        shutil.copy2(ROOT / "data" / "editable" / "grid" / "env39_editable.gpkg", cls.input)
        cls.input_sha = sha256_file(cls.input)
        cls.runs_root = cls.root / "canonical" / "runs"
        cls.canonical_latest = cls.root / "canonical" / "latest.json"
        cls.analysis_root = cls.root / "analysis"
        cls.analysis_latest = cls.analysis_root / "env39" / "latest.json"
        cls.frozen_before = verify_frozen_baselines(ROOT)
        cls.accepted_before = directory_hashes(ACCEPTED_ANALYSIS)

        cls.dry_root = cls.root / "dry"
        cls.dry_canonical_latest = cls.dry_root / "canonical-latest.json"
        cls.dry_analysis_latest = cls.dry_root / "analysis-latest.json"
        cls.dry_canonical_latest.parent.mkdir(parents=True)
        cls.dry_canonical_latest.write_text('{"sentinel":"canonical"}\n', encoding="utf-8")
        cls.dry_analysis_latest.write_text('{"sentinel":"analysis"}\n', encoding="utf-8")
        cls.dry_canonical_before = cls.dry_canonical_latest.read_bytes()
        cls.dry_analysis_before = cls.dry_analysis_latest.read_bytes()
        cls.dry_output: list[str] = []
        with patch("geogami_morphology.workflow.git_provenance", return_value=CLEAN_GIT):
            cls.dry_result = run_full_analysis(
                "env39", input_path=cls.input, mode="preserve-topology",
                reference_canonical=ROOT / "data" / "canonical" / "grid" / "env39_canonical.gpkg",
                runs_root=cls.dry_root / "runs", canonical_latest=cls.dry_canonical_latest,
                analysis_root=cls.dry_root / "analysis", analysis_latest=cls.dry_analysis_latest,
                metrics_config=ROOT / "config" / "metrics.yaml", dry_run=True,
                project_root=ROOT, reporter=cls.dry_output.append,
            )

            cls.output: list[str] = []
            cls.result = run_full_analysis(
                "env39", input_path=cls.input, mode="preserve-topology",
                reference_canonical=ROOT / "data" / "canonical" / "grid" / "env39_canonical.gpkg",
                runs_root=cls.runs_root, canonical_latest=cls.canonical_latest,
                analysis_root=cls.analysis_root, analysis_latest=cls.analysis_latest,
                metrics_config=ROOT / "config" / "metrics.yaml",
                project_root=ROOT, reporter=cls.output.append,
            )
        cls.output_text = "\n".join(cls.output)
        cls.manifest = json.loads(cls.result.end_to_end_manifest_path.read_text(encoding="utf-8"))
        cls.pointer = json.loads(cls.analysis_latest.read_text(encoding="utf-8"))
        cls.accepted_after = directory_hashes(ACCEPTED_ANALYSIS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self):
        self.git_patcher = patch(
            "geogami_morphology.workflow.git_provenance", return_value=CLEAN_GIT
        )
        self.git_patcher.start()

    def tearDown(self):
        self.git_patcher.stop()

    def _fake_selection(self):
        return resolve_canonical_run("env39", canonical_run=ACCEPTED_RUN, project_root=ROOT)

    @staticmethod
    def _fake_graph():
        networkx_graph = MagicMock()
        networkx_graph.number_of_nodes.return_value = 46
        networkx_graph.number_of_edges.return_value = 69
        osmnx_graph = MagicMock()
        osmnx_graph.number_of_nodes.return_value = 46
        osmnx_graph.number_of_edges.return_value = 138
        return SimpleNamespace(
            networkx_graph=networkx_graph, osmnx_graph=osmnx_graph,
            manifest_path=Path("fake-graph-manifest.json"), manifest={},
        )

    @staticmethod
    def _fake_stage_result(name):
        return SimpleNamespace(manifest_path=Path(f"fake-{name}-manifest.json"), manifest={})

    def _gated_call(self):
        return dict(
            environment="env39", input_path=self.input, mode="preserve-topology",
            reference_canonical=ROOT / "data" / "canonical" / "grid" / "env39_canonical.gpkg",
            runs_root=self.root / "gated-runs", canonical_latest=self.root / "gated-canonical.json",
            analysis_root=self.root / "gated-analysis", analysis_latest=self.root / "gated-analysis.json",
            metrics_config=ROOT / "config" / "metrics.yaml", project_root=ROOT,
            reporter=lambda value: None,
        )

    def test_01_dry_run_creates_no_run_artifacts(self):
        self.assertTrue(self.dry_result.dry_run)
        self.assertFalse((self.dry_root / "runs").exists())
        self.assertFalse((self.dry_root / "analysis").exists())

    def test_02_dry_run_does_not_modify_latest_pointers(self):
        self.assertEqual(self.dry_canonical_latest.read_bytes(), self.dry_canonical_before)
        self.assertEqual(self.dry_analysis_latest.read_bytes(), self.dry_analysis_before)

    def test_03_valid_editable_executes_end_to_end_in_isolated_outputs(self):
        self.assertFalse(self.result.dry_run)
        self.assertEqual(self.manifest["final_status"], "PASS")
        self.assertTrue(self.result.canonical_path.is_file())

    def test_04_stage_order_is_strict_and_complete(self):
        positions = [self.output_text.index(f"[{number}/8]") for number in range(1, 9)]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(self.output_text.count("\nPASS"), 8)

    def test_05_canonical_failure_stops_graph_stage(self):
        with patch("geogami_morphology.workflow.run_versioned_canonical", side_effect=RuntimeError("canonical")), patch("geogami_morphology.workflow.build_analysis_graphs") as graph:
            with self.assertRaisesRegex(WorkflowError, "Stage 3"):
                run_full_analysis(**self._gated_call())
        graph.assert_not_called()

    def test_06_graph_failure_stops_metric_stages(self):
        selection = self._fake_selection()
        with patch("geogami_morphology.workflow.run_versioned_canonical"), patch("geogami_morphology.workflow.resolve_canonical_run", return_value=selection), patch("geogami_morphology.workflow.build_analysis_graphs", side_effect=RuntimeError("graph")), patch("geogami_morphology.workflow.analyze_topology") as topology:
            with self.assertRaisesRegex(WorkflowError, "Stage 4"):
                run_full_analysis(**self._gated_call())
        topology.assert_not_called()

    def test_07_topology_failure_stops_geometry_and_integration(self):
        selection, graph = self._fake_selection(), self._fake_graph()
        with patch("geogami_morphology.workflow.run_versioned_canonical"), patch("geogami_morphology.workflow.resolve_canonical_run", return_value=selection), patch("geogami_morphology.workflow.build_analysis_graphs", return_value=graph), patch("geogami_morphology.workflow._record_stage_workflow_start"), patch("geogami_morphology.workflow.analyze_topology", side_effect=RuntimeError("topology")), patch("geogami_morphology.workflow.analyze_geometry") as geometry, patch("geogami_morphology.workflow.integrate_results") as integrated:
            with self.assertRaisesRegex(WorkflowError, "Stage 5"):
                run_full_analysis(**self._gated_call())
        geometry.assert_not_called()
        integrated.assert_not_called()

    def test_08_geometry_failure_stops_integration(self):
        selection, graph = self._fake_selection(), self._fake_graph()
        with patch("geogami_morphology.workflow.run_versioned_canonical"), patch("geogami_morphology.workflow.resolve_canonical_run", return_value=selection), patch("geogami_morphology.workflow.build_analysis_graphs", return_value=graph), patch("geogami_morphology.workflow._record_stage_workflow_start"), patch("geogami_morphology.workflow.analyze_topology", return_value=self._fake_stage_result("topology")), patch("geogami_morphology.workflow.analyze_geometry", side_effect=RuntimeError("geometry")), patch("geogami_morphology.workflow.integrate_results") as integrated:
            with self.assertRaisesRegex(WorkflowError, "Stage 6"):
                run_full_analysis(**self._gated_call())
        integrated.assert_not_called()

    def test_09_integrated_failure_yields_stage_7_failure(self):
        selection, graph = self._fake_selection(), self._fake_graph()
        with patch("geogami_morphology.workflow.run_versioned_canonical"), patch("geogami_morphology.workflow.resolve_canonical_run", return_value=selection), patch("geogami_morphology.workflow.build_analysis_graphs", return_value=graph), patch("geogami_morphology.workflow._record_stage_workflow_start"), patch("geogami_morphology.workflow.analyze_topology", return_value=self._fake_stage_result("topology")), patch("geogami_morphology.workflow.analyze_geometry", return_value=self._fake_stage_result("geometry")), patch("geogami_morphology.workflow.integrate_results", side_effect=RuntimeError("integrated")):
            with self.assertRaisesRegex(WorkflowError, "Stage 7"):
                run_full_analysis(**self._gated_call())

    def test_10_qgis_wal_causes_safe_refusal(self):
        sidecar = Path(str(self.input) + "-wal")
        sidecar.touch()
        try:
            with self.assertRaisesRegex(ValueError, "Close QGIS"):
                preflight_editable_input(self.input, project_root=ROOT)
            self.assertEqual(qgis_lock_files(self.input), (sidecar,))
        finally:
            sidecar.unlink()

    def test_11_qgis_shm_causes_safe_refusal(self):
        sidecar = Path(str(self.input) + "-shm")
        sidecar.touch()
        try:
            with self.assertRaisesRegex(ValueError, "Close QGIS"):
                preflight_editable_input(self.input, project_root=ROOT)
        finally:
            sidecar.unlink()

    def test_12_qgis_journal_is_never_deleted(self):
        sidecar = Path(str(self.input) + "-journal")
        sidecar.touch()
        try:
            with self.assertRaisesRegex(ValueError, "Close QGIS"):
                preflight_editable_input(self.input, project_root=ROOT)
            self.assertTrue(sidecar.is_file())
        finally:
            sidecar.unlink()

    def test_13_frozen_baseline_input_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "cannot be used"):
            preflight_editable_input(ROOT / "data" / "baselines" / "grid" / "network_v2.gpkg", project_root=ROOT)

    def test_14_canonical_input_is_rejected_as_editable(self):
        with self.assertRaisesRegex(ValueError, "cannot be used"):
            preflight_editable_input(ROOT / "data" / "canonical" / "grid" / "env39_canonical.gpkg", project_root=ROOT)

    def test_15_professor_editable_path_is_accepted(self):
        record = preflight_editable_input(ROOT / "data" / "editable" / "grid" / "env39_editable.gpkg", project_root=ROOT)
        self.assertEqual(record["node_count"], 46)
        self.assertEqual(record["edge_count"], 69)

    def test_16_success_prints_all_required_output_locations(self):
        for label in ("Canonical GeoPackage:", "Canonical manifest:", "GraphML:", "Topology results:", "Geometry results:", "Integrated results:", "Comparison-ready metrics:", "Core metrics:", "Baseline summary:", "End-to-end manifest:", "Notebook directory:"):
            self.assertIn(label, self.output_text)
        self.assertIn("FINAL RESULT: PASS", self.output_text)

    def test_17_success_creates_complete_end_to_end_manifest(self):
        self.assertTrue(self.result.end_to_end_manifest_path.is_file())
        self.assertEqual(self.manifest["workflow"], "professor_end_to_end_preserve_topology")
        self.assertEqual(len(self.manifest["stage_manifests"]), 5)
        self.assertIn("workflow_start_git", self.manifest)
        self.assertIn("workflow_end_git", self.manifest)
        self.assertEqual(self.manifest["provenance_acceptance"]["status"], "PASS")

    def test_18_manifest_links_all_stage_manifests_by_valid_hash(self):
        for record in self.manifest["stage_manifests"].values():
            path = ROOT / record["path"]
            self.assertTrue(path.is_file())
            self.assertEqual(sha256_file(path), record["file_sha256"])

    def test_19_final_results_use_one_canonical_run_id(self):
        expected = self.result.run_id
        self.assertEqual(self.manifest["canonical_run_id"], expected)
        for record in self.manifest["stage_manifests"].values():
            document = json.loads((ROOT / record["path"]).read_text(encoding="utf-8"))
            actual = document.get("canonical_run_id", document.get("identity", {}).get("run_id"))
            self.assertEqual(actual, expected)

    def test_20_current_env39_physical_identity_is_preserved(self):
        identity = self.manifest["network_identity"]
        self.assertEqual((identity["node_count"], identity["physical_edge_count"], identity["component_count"]), (46, 69, 1))

    def test_21_directed_arcs_are_not_reported_as_physical_streets(self):
        identity = self.manifest["network_identity"]
        self.assertEqual(identity["osmnx_directed_arc_count"], 138)
        self.assertEqual(identity["osmnx_physical_street_count"], 69)
        self.assertIn("138 directed arcs / 69 physical streets", self.output_text)

    def test_22_comparison_ready_metrics_exists_after_success(self):
        self.assertTrue((self.result.integrated_dir / "comparison_ready_metrics.csv").is_file())

    def test_23_editable_source_remains_unchanged(self):
        self.assertEqual(sha256_file(self.input), self.input_sha)
        self.assertTrue(self.manifest["editable_source"]["unchanged"])

    def test_24_frozen_baselines_remain_byte_identical(self):
        self.assertEqual(verify_frozen_baselines(ROOT), self.frozen_before)
        self.assertEqual(self.frozen_before["data/baselines/grid/network_v2.gpkg"], FROZEN_BASELINE_SHA)
        self.assertEqual(self.frozen_before["data/canonical/grid/env39_canonical.gpkg"], CANONICAL_SHA)

    def test_25_historical_successful_analysis_is_untouched(self):
        self.assertEqual(self.accepted_after, self.accepted_before)

    def test_26_command_works_from_repository_root(self):
        command = [sys.executable, "scripts/run_full_analysis.py", "--environment", "env39", "--input", "data/editable/grid/env39_editable.gpkg", "--mode", "preserve-topology", "--dry-run"]
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=60)
        if completed.returncode == 0:
            self.assertIn("FINAL RESULT: DRY RUN PASS", completed.stdout)
        else:
            self.assertIn("Clean-start Git provenance", completed.stderr)
            self.assertIn("FINAL RESULT: FAIL", completed.stderr)

    def test_27_current_conda_interpreter_executes_cli_deterministically(self):
        self.assertEqual(Path(sys.executable).resolve(), Path(sys.prefix, "python.exe").resolve())
        self.assertIn("geogami-morphology", str(Path(sys.executable)).lower())

    def test_28_professor_documentation_matches_actual_cli_arguments(self):
        document = (ROOT / "docs" / "professor_workflow.md").read_text(encoding="utf-8")
        command = "python scripts/run_full_analysis.py --environment env39 --input data/editable/grid/env39_editable.gpkg --mode preserve-topology"
        self.assertIn(command, document)
        parsed = parse_args(["--environment", "env39", "--input", "data/editable/grid/env39_editable.gpkg", "--mode", "preserve-topology"])
        self.assertEqual(parsed.environment, "env39")
        self.assertEqual(parsed.mode, "preserve-topology")

    def test_29_analysis_latest_pointer_describes_complete_run(self):
        self.assertEqual(self.pointer["canonical_run_id"], self.result.run_id)
        self.assertEqual(ROOT / self.pointer["analysis_manifest_path"], self.result.end_to_end_manifest_path)
        self.assertTrue((ROOT / self.pointer["comparison_ready_metrics_path"]).is_file())

    def test_30_failed_analysis_never_updates_analysis_latest(self):
        before = self.analysis_latest.read_bytes()
        with patch("geogami_morphology.workflow.run_versioned_canonical", side_effect=RuntimeError("stop")):
            with self.assertRaises(WorkflowError):
                run_full_analysis(
                    "env39", input_path=self.input, mode="preserve-topology",
                    reference_canonical=ROOT / "data" / "canonical" / "grid" / "env39_canonical.gpkg",
                    runs_root=self.root / "failed-runs", canonical_latest=self.root / "failed-canonical.json",
                    analysis_root=self.analysis_root, analysis_latest=self.analysis_latest,
                    metrics_config=ROOT / "config" / "metrics.yaml", project_root=ROOT,
                    reporter=lambda value: None,
                )
        self.assertEqual(self.analysis_latest.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
