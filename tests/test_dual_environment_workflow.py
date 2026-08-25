from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from geogami_morphology.environments import get_environment
from geogami_morphology.io import sha256_file
from geogami_morphology.workflow import WorkflowError, run_full_analysis
import run_full_analysis as cli


ENV39_SCIENTIFIC = "2046798C1E9CB35310ABB4EA7134EE212423265572C278075A94ED0E9080D447"
ENV38_SCIENTIFIC = "974F687C1AC4E1CDA548366DFB89FFBFCDBE05F44BA28342EF52149A4C607945"
TOPOLOGY = "4AF93910532EB430103267DAECF73B3ADC3B3D1599471EDF6B1632B6C5F898CB"
ENV39_SHA = "212A2AA583BF77304859DB76639B8AD4B6DC458237EEA24FEB60FC228E5D6847"
ENV38_SHA = "B2F608ED60EA4E5B7F6C2A3564102E6ACDF576F36870D6EA666E75600746F429"
BASELINE_SHA = "7698544A01EED008E5451F8368703AFD0DBE232D10C9D7ADAA58C13DA57E0289"
CLEAN_GIT = {
    "repository": ROOT.name,
    "repository_root": ".",
    "remote_origin": "test-origin",
    "branch": "phase-9-test",
    "commit_sha": "9" * 40,
    "dirty": False,
    "changed_paths": [],
}


def file_or_absent(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


class Phase9BDualEnvironmentWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env39 = get_environment("env39")
        cls.env38 = get_environment("env38")
        cls.registry = ROOT / "config/environments.yaml"
        cls.registry_sha = sha256_file(cls.registry)
        cls.root = ROOT / "tests/.tmp" / f"phase9b-{uuid.uuid4().hex}"
        cls.root.mkdir(parents=True)
        cls.protected = {
            path: sha256_file(path)
            for path in (
                cls.env39.editable_source,
                cls.env39.topology_reference,
                ROOT / "data/baselines/grid/network_v2.gpkg",
                cls.env38.editable_source,
                ROOT / "data/working/curvilinear/env38_working.gpkg",
            )
        }
        cls.production_pointers = {
            cls.env39.canonical_latest: file_or_absent(cls.env39.canonical_latest),
            cls.env39.analysis_root / "latest.json": file_or_absent(cls.env39.analysis_root / "latest.json"),
            cls.env38.canonical_latest: file_or_absent(cls.env38.canonical_latest),
            cls.env38.analysis_root / "latest.json": file_or_absent(cls.env38.analysis_root / "latest.json"),
        }

        cls.dry_outputs: dict[str, list[str]] = {}
        cls.dry_results = {}
        with patch("geogami_morphology.workflow.git_provenance", return_value=CLEAN_GIT):
            for environment, config in (("env39", cls.env39), ("env38", cls.env38)):
                output: list[str] = []
                cls.dry_results[environment] = run_full_analysis(
                    environment,
                    input_path=config.editable_source,
                    mode="preserve-topology",
                    dry_run=True,
                    project_root=ROOT,
                    reporter=output.append,
                )
                cls.dry_outputs[environment] = output

            cls.full_results = {}
            for environment, config in (("env39", cls.env39), ("env38", cls.env38)):
                environment_root = cls.root / environment
                cls.full_results[environment] = run_full_analysis(
                    environment,
                    input_path=config.editable_source,
                    mode="preserve-topology",
                    reference_canonical=config.topology_reference,
                    runs_root=environment_root / "canonical/runs",
                    canonical_latest=environment_root / "canonical/latest.json",
                    analysis_root=cls.root / "analysis",
                    analysis_latest=cls.root / "analysis" / environment / "latest.json",
                    project_root=ROOT,
                    reporter=lambda _value: None,
                )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_01_professor_cli_routes_env39_without_advanced_arguments(self):
        with patch.object(cli, "run_full_analysis") as run:
            self.assertEqual(cli.main([
                "--environment", "env39", "--input", "data/editable/grid/env39_editable.gpkg",
                "--mode", "preserve-topology", "--dry-run",
            ]), 0)
        kwargs = run.call_args.kwargs
        self.assertIsNone(kwargs["reference_canonical"])
        self.assertIsNone(kwargs["runs_root"])
        self.assertIsNone(kwargs["analysis_root"])

    def test_02_professor_cli_routes_env38_without_advanced_arguments(self):
        with patch.object(cli, "run_full_analysis") as run:
            self.assertEqual(cli.main([
                "--environment", "env38", "--input", "data/editable/curvilinear/env38_editable.gpkg",
                "--mode", "preserve-topology", "--dry-run",
            ]), 0)
        self.assertEqual(run.call_args.args[0], "env38")
        self.assertIsNone(run.call_args.kwargs["canonical_latest"])

    def test_03_env39_dry_run_paths_are_registry_driven(self):
        text = "\n".join(self.dry_outputs["env39"])
        self.assertIn("data/canonical/grid/runs/<new_run_id>/env39_canonical.gpkg", text)
        self.assertIn("results/analysis/env39/<new_run_id>", text)
        self.assertFalse(self.dry_results["env39"].manifest.get("metrics_calculated", False))

    def test_04_env38_dry_run_paths_are_registry_driven(self):
        text = "\n".join(self.dry_outputs["env38"])
        self.assertIn("data/canonical/curvilinear/runs/<new_run_id>/env38_canonical.gpkg", text)
        self.assertIn("results/analysis/env38/<new_run_id>", text)

    def test_05_dry_runs_do_not_double_nest_environment(self):
        combined = "\n".join(sum(self.dry_outputs.values(), []))
        self.assertNotIn("env39/env39/", combined)
        self.assertNotIn("env38/env38/", combined)

    def test_06_cross_environment_inputs_are_rejected_with_paths(self):
        cases = (("env38", self.env39.editable_source, self.env38), ("env39", self.env38.editable_source, self.env39))
        with patch("geogami_morphology.workflow.git_provenance", return_value=CLEAN_GIT):
            for environment, received, expected in cases:
                with self.subTest(environment=environment):
                    with self.assertRaises(WorkflowError) as caught:
                        run_full_analysis(
                            environment, input_path=received, mode="preserve-topology",
                            dry_run=True, project_root=ROOT, reporter=lambda _value: None,
                        )
                    message = str(caught.exception)
                    self.assertIn(f"Environment {environment}", message)
                    self.assertIn(expected.editable_source.relative_to(ROOT).as_posix(), message)
                    self.assertIn(received.relative_to(ROOT).as_posix(), message)

    def test_07_unknown_environment_fails_before_output(self):
        with self.assertRaisesRegex(WorkflowError, "Unsupported environment 'env40'"):
            run_full_analysis(
                "env40", input_path=self.env39.editable_source, dry_run=True,
                project_root=ROOT, reporter=lambda _value: None,
            )

    def test_08_env39_latest_resolution_is_preserved(self):
        with patch("geogami_morphology.workflow.git_provenance", return_value=CLEAN_GIT):
            result = run_full_analysis(
                "env39", canonical_run="latest", dry_run=True,
                project_root=ROOT, reporter=lambda _value: None,
            )
        self.assertTrue(result.dry_run)
        self.assertEqual(
            result.manifest["environment_routing"]["resolved_canonical_latest_pointer"],
            "data/canonical/grid/latest.json",
        )

    def test_09_env38_missing_latest_fails_without_fallback(self):
        self.assertFalse(self.env38.canonical_latest.exists())
        with patch("geogami_morphology.workflow.git_provenance", return_value=CLEAN_GIT):
            with self.assertRaisesRegex(WorkflowError, "Latest canonical pointer does not exist.*curvilinear"):
                run_full_analysis(
                    "env38", canonical_run="latest", dry_run=True,
                    project_root=ROOT, reporter=lambda _value: None,
                )

    def test_10_registry_provenance_is_complete_in_both_manifests(self):
        required = {
            "environment_id", "environment_registry_path", "environment_registry_sha256",
            "resolved_editable_source", "resolved_topology_reference",
            "resolved_canonical_runs_root", "resolved_canonical_latest_pointer",
            "resolved_analysis_environment_root",
        }
        for environment, result in self.full_results.items():
            with self.subTest(environment=environment):
                end = json.loads(result.end_to_end_manifest_path.read_text(encoding="utf-8"))
                canonical = json.loads(result.canonical_manifest_path.read_text(encoding="utf-8"))
                self.assertTrue(required.issubset(end["environment_routing"]))
                self.assertEqual(end["environment_routing"]["environment_registry_sha256"], self.registry_sha)
                self.assertEqual(canonical["environment_routing"], end["environment_routing"])

    def test_11_env38_topology_reference_is_frozen_env39_canonical(self):
        routing = self.full_results["env38"].manifest["environment_routing"]
        self.assertEqual(routing["resolved_topology_reference"], "data/canonical/grid/env39_canonical.gpkg")

    def test_12_all_three_configuration_files_are_retained(self):
        expected = {"environment.yml", "config/metrics.yaml", "config/environments.yaml"}
        for result in self.full_results.values():
            manifest = json.loads(result.canonical_manifest_path.read_text(encoding="utf-8"))
            observed = {record["path"] for record in manifest["configuration"]["configuration_files"]}
            self.assertEqual(observed, expected)

    def test_13_isolated_env39_end_to_end_passes_with_unchanged_identity(self):
        result = self.full_results["env39"]
        self.assertEqual(result.manifest["final_status"], "PASS")
        self.assertEqual(result.manifest["canonical"]["scientific_content_signature"], ENV39_SCIENTIFIC)
        self.assertTrue(result.integrated_dir.is_dir())

    def test_14_isolated_env38_end_to_end_passes_with_own_geometry(self):
        result = self.full_results["env38"]
        self.assertEqual(result.manifest["final_status"], "PASS")
        self.assertEqual(result.manifest["canonical"]["scientific_content_signature"], ENV38_SCIENTIFIC)
        self.assertTrue(result.integrated_dir.is_dir())

    def test_15_env38_graph_identity_is_46_69_1_138(self):
        identity = self.full_results["env38"].manifest["network_identity"]
        self.assertEqual(
            (identity["node_count"], identity["physical_edge_count"], identity["component_count"], identity["osmnx_directed_arc_count"]),
            (46, 69, 1, 138),
        )

    def test_16_both_topology_signatures_match_frozen_contract(self):
        for result in self.full_results.values():
            self.assertEqual(result.manifest["canonical"]["topology_signature"], TOPOLOGY)

    def test_17_isolated_analysis_paths_have_exact_single_environment_level(self):
        for environment, result in self.full_results.items():
            expected = self.root / "analysis" / environment / result.run_id
            self.assertEqual(result.end_to_end_manifest_path.parent, expected)
            self.assertNotIn(f"/{environment}/{environment}/", expected.as_posix())

    def test_18_production_pointers_are_unchanged_and_env38_absent(self):
        for path, before in self.production_pointers.items():
            self.assertEqual(file_or_absent(path), before)
        self.assertIsNone(self.production_pointers[self.env38.canonical_latest])
        self.assertIsNone(self.production_pointers[self.env38.analysis_root / "latest.json"])

    def test_19_all_protected_geopackages_are_unchanged(self):
        for path, before in self.protected.items():
            self.assertEqual(sha256_file(path), before)
        self.assertEqual(self.protected[ROOT / "data/baselines/grid/network_v2.gpkg"], BASELINE_SHA)
        self.assertEqual(self.protected[self.env39.topology_reference], ENV39_SHA)
        self.assertEqual(self.protected[self.env39.editable_source], ENV39_SHA)
        self.assertEqual(self.protected[self.env38.editable_source], ENV38_SHA)
        self.assertEqual(self.protected[ROOT / "data/working/curvilinear/env38_working.gpkg"], ENV38_SHA)


if __name__ == "__main__":
    unittest.main()
