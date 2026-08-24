from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from geogami_morphology.integrated import (
    COMPARISON_FIELDS,
    COMPARISON_ROLES,
    METRIC_DEFINITIONS,
    METRIC_FAMILIES,
    assemble_comparison_rows,
    integrate_results,
)
from geogami_morphology.io import sha256_file


RUN_ID = "env39_20260824T123033467880Z_2046798c1e9c"
RUN_ROOT = ROOT / "results" / "analysis" / "env39" / RUN_ID
FROZEN_BASELINE_SHA = "7698544A01EED008E5451F8368703AFD0DBE232D10C9D7ADAA58C13DA57E0289"
CANONICAL_SHA = "212A2AA583BF77304859DB76639B8AD4B6DC458237EEA24FEB60FC228E5D6847"
TOPOLOGY_SIGNATURE = "4AF93910532EB430103267DAECF73B3ADC3B3D1599471EDF6B1632B6C5F898CB"


def directory_hashes(path: Path) -> dict[str, str]:
    return {
        item.relative_to(path).as_posix(): sha256_file(item)
        for item in path.rglob("*") if item.is_file()
    }


class Phase7HIntegratedResultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = integrate_results(
            "env39", canonical_run=RUN_ID, project_root=ROOT, publish=False
        )
        cls.rows = list(cls.result.metrics)
        cls.core = list(cls.result.core_metrics)
        cls.by_id = {row["metric_id"]: row for row in cls.rows}
        cls.output_dir = RUN_ROOT / "integrated"
        cls.topology_summary = json.loads((RUN_ROOT / "topology" / "topology_metrics_summary.json").read_text(encoding="utf-8"))
        cls.geometry_summary = json.loads((RUN_ROOT / "geometry" / "geometry_metrics_summary.json").read_text(encoding="utf-8"))
        cls.orientation_summary = json.loads((RUN_ROOT / "geometry" / "orientation_metrics_summary.json").read_text(encoding="utf-8"))

    def test_01_metrics_are_loaded_from_source_summaries(self):
        changed = copy.deepcopy(self.geometry_summary)
        source = next(row for row in changed["metrics"] if row["metric_name"] == "total_network_length_local")
        source["value"] = 123.456
        rows = assemble_comparison_rows(
            environment="study", canonical_run_id="run", topology_summary=self.topology_summary,
            geometry_summary=changed, orientation_summary=self.orientation_summary,
        )
        mapped = {row["metric_id"]: row["value"] for row in rows}
        self.assertEqual(mapped["geometry.total_length"], 123.456)

    def test_02_every_core_metric_has_stable_metric_id(self):
        self.assertEqual(len(self.core), 19)
        self.assertTrue(all(row["metric_id"] and "." in row["metric_id"] for row in self.core))

    def test_03_metric_ids_are_unique(self):
        ids = [row["metric_id"] for row in self.rows]
        self.assertEqual(len(ids), len(set(ids)))

    def test_04_every_metric_has_family(self):
        self.assertTrue(all(row["metric_family"] for row in self.rows))

    def test_05_only_valid_metric_families_are_used(self):
        self.assertLessEqual({row["metric_family"] for row in self.rows}, METRIC_FAMILIES)
        self.assertEqual({row["metric_family"] for row in self.rows}, METRIC_FAMILIES)

    def test_06_topology_controls_are_classified_correctly(self):
        topology = [row for row in self.rows if row["metric_family"] == "topology_controlled"]
        self.assertTrue(topology)
        self.assertTrue(all(row["comparison_role"] == "topology_control" for row in topology))

    def test_07_geometry_sensitive_metrics_are_classified_correctly(self):
        geometry = [row for row in self.rows if row["comparison_role"] == "geometry_sensitive"]
        self.assertTrue(geometry)
        self.assertTrue(all(row["metric_family"] in {"geometry_weighted_network", "geometric_morphology"} for row in geometry))

    def test_08_only_valid_comparison_roles_are_used(self):
        self.assertLessEqual({row["comparison_role"] for row in self.rows}, COMPARISON_ROLES)

    def test_09_units_are_preserved_from_sources(self):
        self.assertEqual(self.by_id["geometry.total_length"]["units"], "local units")
        self.assertEqual(self.by_id["geometry.network_circuity"]["units"], "ratio")
        self.assertEqual(self.by_id["orientation.chord_entropy"]["units"], "nats")

    def test_10_local_quantities_are_never_labeled_metres(self):
        local_rows = [row for row in self.rows if "local" in row["units"]]
        self.assertTrue(local_rows)
        self.assertTrue(all("metre" not in row["units"].lower() and "meter" not in row["units"].lower() for row in local_rows))

    def test_11_comparison_schema_has_all_required_columns(self):
        required = {
            "environment", "canonical_run_id", "metric_id", "metric_label", "metric_family",
            "value", "units", "population", "weighting", "library_or_method",
            "higher_lower_interpretation", "comparison_role",
        }
        self.assertLessEqual(required, set(COMPARISON_FIELDS))
        self.assertTrue(all(set(row) == set(COMPARISON_FIELDS) for row in self.rows))

    def test_12_schema_is_reusable_for_future_env38(self):
        rows = assemble_comparison_rows(
            environment="env38", canonical_run_id="future_run", topology_summary=self.topology_summary,
            geometry_summary=self.geometry_summary, orientation_summary=self.orientation_summary,
        )
        self.assertEqual({row["environment"] for row in rows}, {"env38"})
        self.assertEqual([row["metric_id"] for row in rows], [row["metric_id"] for row in self.rows])

    def test_13_metric_ids_have_no_env39_specific_text(self):
        self.assertTrue(all("env39" not in row["metric_id"].lower() for row in self.rows))

    def test_14_env39_topology_regression_values(self):
        self.assertEqual(self.by_id["topology.node_count"]["value"], 46)
        self.assertEqual(self.by_id["topology.physical_edge_count"]["value"], 69)
        self.assertEqual(self.by_id["topology.component_count"]["value"], 1)
        self.assertEqual(self.by_id["topology.mean_degree"]["value"], 3.0)
        self.assertEqual(self.by_id["topology.cycle_rank"]["value"], 24)

    def test_15_env39_geometry_regression_values(self):
        self.assertAlmostEqual(self.by_id["geometry.total_length"]["value"], 10945.657681153116)
        self.assertAlmostEqual(self.by_id["geometry.mean_edge_circuity"]["value"], 1.0111449497153886)
        self.assertAlmostEqual(self.by_id["geometry.network_circuity"]["value"], 1.022596526286689)

    def test_16_env39_orientation_regression_values(self):
        self.assertAlmostEqual(self.by_id["orientation.chord_entropy"]["value"], 0.8233712721085531)
        self.assertAlmostEqual(self.by_id["orientation.segment_entropy_length_weighted"]["value"], 0.6896030136902007)
        self.assertAlmostEqual(self.by_id["orientation.phi_chord"]["value"], 0.9511069268378259)
        self.assertEqual(self.by_id["orientation.phi_segment_length_weighted"]["value"], 1.0)

    def test_17_canonical_and_frozen_baseline_hashes_are_unchanged(self):
        self.assertEqual(sha256_file(ROOT / "data" / "baselines" / "grid" / "network_v2.gpkg"), FROZEN_BASELINE_SHA)
        self.assertEqual(sha256_file(self.result.selection.canonical_path), CANONICAL_SHA)

    def test_18_topology_signature_is_unchanged(self):
        self.assertEqual(self.result.selection.topology_signature, TOPOLOGY_SIGNATURE)

    def test_19_integrated_outputs_have_expected_schema_and_rows(self):
        with (self.output_dir / "comparison_ready_metrics.csv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(list(rows[0]), COMPARISON_FIELDS)
        self.assertEqual(len(rows), len(METRIC_DEFINITIONS))
        self.assertEqual(sum(row["metric_tier"] == "core" for row in rows), 19)

    def test_20_publication_preserves_phase7f_and_phase7g(self):
        topology_before = directory_hashes(RUN_ROOT / "topology")
        geometry_before = directory_hashes(RUN_ROOT / "geometry")
        fixture_root = ROOT / "tests" / ".tmp" / f"phase7h-publish-{uuid.uuid4().hex}"
        fixture_run = fixture_root / "env39" / RUN_ID
        fixture_run.mkdir(parents=True)
        shutil.copy2(RUN_ROOT / "analysis_graph_manifest.json", fixture_run / "analysis_graph_manifest.json")
        shutil.copytree(RUN_ROOT / "topology", fixture_run / "topology")
        shutil.copytree(RUN_ROOT / "geometry", fixture_run / "geometry")
        try:
            integrate_results(
                "env39", canonical_run=RUN_ID, output_root=fixture_root,
                project_root=ROOT, publish=True,
            )
            self.assertEqual(directory_hashes(fixture_run / "topology"), topology_before)
            self.assertEqual(directory_hashes(fixture_run / "geometry"), geometry_before)
        finally:
            shutil.rmtree(fixture_root, ignore_errors=True)
        self.assertEqual(directory_hashes(RUN_ROOT / "topology"), topology_before)
        self.assertEqual(directory_hashes(RUN_ROOT / "geometry"), geometry_before)

    def test_21_manifest_chain_hashes_are_valid(self):
        manifest = json.loads((self.output_dir / "integrated_analysis_manifest.json").read_text(encoding="utf-8"))
        for record in manifest["provenance_chain"].values():
            self.assertEqual(sha256_file(ROOT / record["path"]), record["file_sha256"])
        for record in manifest["artifacts"].values():
            self.assertEqual(sha256_file(ROOT / record["path"]), record["file_sha256"])

    def test_22_all_referenced_figures_exist_and_match_hashes(self):
        manifest = json.loads((self.output_dir / "integrated_analysis_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["figures"]), 5)
        for figure in manifest["figures"]:
            path = ROOT / figure["path"]
            self.assertTrue(path.is_file())
            self.assertEqual(sha256_file(path), figure["file_sha256"])

    def test_23_core_selection_has_documented_rationale(self):
        document = json.loads((self.output_dir / "core_metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(set(document["selection_rationale"]), {row["metric_id"] for row in self.core})
        self.assertTrue(all(document["selection_rationale"].values()))

    def test_24_historical_values_are_separate_from_current_metrics(self):
        methodology = json.loads((self.output_dir / "integrated_methodology.json").read_text(encoding="utf-8"))
        note = methodology["historical_methodology_note"]
        self.assertIn("not directly comparable", note)
        self.assertIn("Phase 7G methodology is current", note)
        self.assertNotIn(1.5144, [row["value"] for row in self.rows])

    def test_25_no_fake_env38_result_artifact_is_created(self):
        self.assertFalse((ROOT / "results" / "analysis" / "env38").exists())
        self.assertTrue(all(row["environment"] == "env39" for row in self.rows))


if __name__ == "__main__":
    unittest.main()
