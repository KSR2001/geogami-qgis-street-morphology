from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from geogami_morphology.comparison import (
    ComparisonError,
    OUTPUT_FIELDS,
    build_environment_comparison,
    comparison_arithmetic,
)
from geogami_morphology.integrated import COMPARISON_ROLES, METRIC_FAMILIES
from geogami_morphology.io import sha256_file
from geogami_morphology.versioned import git_provenance


class Phase9GEnvironmentComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.output_root = ROOT / "tests" / ".tmp" / "phase9g-comparison"
        if cls.output_root.exists():
            shutil.rmtree(cls.output_root)
        cls.protected_roots = (
            ROOT / "data" / "canonical" / "curvilinear" / "runs",
            ROOT / "results" / "analysis" / "env38",
            ROOT / "data" / "canonical" / "grid" / "runs",
            ROOT / "results" / "analysis" / "env39",
            ROOT / "notebooks",
        )
        cls.protected_files = (
            ROOT / "data" / "canonical" / "curvilinear" / "latest.json",
            ROOT / "data" / "canonical" / "grid" / "latest.json",
            ROOT / "data" / "editable" / "curvilinear" / "env38_editable.gpkg",
            ROOT / "data" / "working" / "curvilinear" / "env38_working.gpkg",
            ROOT / "data" / "baselines" / "grid" / "network_v2.gpkg",
            ROOT / "data" / "canonical" / "grid" / "env39_canonical.gpkg",
            ROOT / "config" / "metrics.yaml",
            ROOT / "config" / "environments.yaml",
        )
        cls.protected_before = cls._protected_hashes()
        provenance = git_provenance(ROOT)
        cls.result = build_environment_comparison(
            project_root=ROOT,
            output_root=cls.output_root,
            comparison_id="env38_vs_env39_test_fixture",
            phase_start_clean=True,
            phase_start_commit=provenance["commit_sha"],
        )
        cls.manifest = json.loads(cls.result.manifest_path.read_text(encoding="utf-8"))
        cls.summary = json.loads(
            (cls.result.output_dir / "comparison_summary.json").read_text(encoding="utf-8")
        )
        cls.methodology = json.loads(
            (cls.result.output_dir / "comparison_methodology.json").read_text(encoding="utf-8")
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.output_root, ignore_errors=True)

    @classmethod
    def _protected_hashes(cls):
        records = {}
        for root in cls.protected_roots:
            for path in root.rglob("*"):
                if path.is_file():
                    records[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        for path in cls.protected_files:
            records[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        return records

    def test_01_topology_control_is_exact_and_stable_connectivity_matches(self):
        control = self.summary["topology_control"]
        self.assertEqual(control["status"], "PASS")
        self.assertEqual(
            control["topology_signature"],
            "4AF93910532EB430103267DAECF73B3ADC3B3D1599471EDF6B1632B6C5F898CB",
        )
        self.assertTrue(control["stable_node_ids_equal"])
        self.assertTrue(control["stable_physical_edge_connectivity_equal"])
        expected = {
            "node_count": 46, "physical_edge_count": 69, "connected_component_count": 1,
            "degree_mean": 3.0, "dead_end_count": 10, "degree_3_count": 16,
            "degree_4_count": 20, "cycle_rank": 24,
        }
        self.assertEqual(control["env39"], expected)
        self.assertEqual(control["env38"], expected)

    def test_02_metric_and_core_alignment_use_stable_ids(self):
        self.assertEqual(len(self.result.comparison_rows), 39)
        self.assertEqual(len(self.result.core_rows), 19)
        self.assertEqual(len({row["metric_id"] for row in self.result.comparison_rows}), 39)
        self.assertEqual(
            {row["metric_id"] for row in self.result.core_rows},
            {row["metric_id"] for row in self.result.comparison_rows if row["metric_tier"] == "core"},
        )
        with (self.result.output_dir / "comparison_metrics.csv").open(
            encoding="utf-8", newline=""
        ) as stream:
            reader = csv.DictReader(stream)
            self.assertEqual(reader.fieldnames, OUTPUT_FIELDS)
            self.assertEqual(len(list(reader)), 39)

    def test_03_family_role_and_control_classification_are_preserved(self):
        rows = self.result.comparison_rows
        self.assertEqual({row["metric_family"] for row in rows}, set(METRIC_FAMILIES))
        self.assertEqual({row["comparison_role"] for row in rows}, set(COMPARISON_ROLES))
        controlled = [row for row in rows if row["metric_family"] == "topology_controlled"]
        self.assertEqual(len(controlled), 18)
        self.assertTrue(all(row["comparison_status"] == "CONTROL_MATCH" for row in controlled))
        self.assertFalse(any(row["comparison_status"] == "CONTROL_FAILURE" for row in rows))

    def test_04_difference_arithmetic_and_zero_denominator_are_explicit(self):
        absolute, signed, relative = comparison_arithmetic(4.0, 5.0)
        self.assertEqual((absolute, signed, relative), (1.0, 1.0, 25.0))
        self.assertEqual(comparison_arithmetic(0.0, 5.0), (5.0, 5.0, None))
        self.assertEqual(comparison_arithmetic(True, False), (None, None, None))
        for row in self.result.comparison_rows:
            if row["signed_difference_env38_minus_env39"] is not None:
                expected = float(row["env38_value"]) - float(row["env39_value"])
                self.assertEqual(row["signed_difference_env38_minus_env39"], expected)
                self.assertEqual(row["absolute_difference"], abs(expected))

    def test_05_no_inferential_statistics_or_geographic_operations(self):
        self.assertFalse(self.summary["inferential_statistics"]["performed"])
        self.assertFalse(self.methodology["inferential_statistics"]["performed"])
        self.assertIn("local units, not metres", self.methodology["local_coordinate_limitation"])
        source = (
            (ROOT / "src/geogami_morphology/comparison.py").read_text(encoding="utf-8")
            + (ROOT / "scripts/build_environment_comparison.py").read_text(encoding="utf-8")
        )
        for forbidden in (
            "scipy.stats", "ttest", "f_oneway", "graph_from_place(",
            "add_edge_bearings(", "add_edge_lengths(", "to_crs(", "EPSG:4326",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIsNone(re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]", source))

    def test_06_orientation_distributions_use_identical_accepted_bins(self):
        distributions = self.summary["orientation_distributions"]
        method = distributions["methodology"]
        self.assertTrue(method["identical_bin_edges"])
        self.assertEqual((method["bin_count"], method["bin_width_degrees"]), (36, 5.0))
        self.assertEqual(len(distributions["bin_edges"]), 36)
        self.assertAlmostEqual(
            sum(row["probability"] for row in distributions["chord"]["env39"]), 1.0
        )
        self.assertAlmostEqual(
            sum(row["probability"] for row in distributions["chord"]["env38"]), 1.0
        )
        self.assertAlmostEqual(
            sum(row["probability"] for row in distributions["segment_length_weighted"]["env39"]), 1.0
        )

    def test_07_all_eight_headless_svg_figures_are_valid(self):
        figures = sorted((self.result.output_dir / "figures").glob("*.svg"))
        self.assertEqual(len(figures), 8)
        self.assertEqual(
            [path.name for path in figures],
            [
                "01_physical_networks_equal_scale.svg", "02_topology_control_summary.svg",
                "03_edge_circuity_comparison.svg", "04_chord_orientation_comparison.svg",
                "05_segment_orientation_comparison.svg", "06_normalized_entropy_comparison.svg",
                "07_phi_comparison.svg", "08_selected_core_metric_comparison.svg",
            ],
        )
        for path in figures:
            self.assertTrue(ET.fromstring(path.read_bytes()).tag.endswith("svg"))
        network_figure = figures[0].read_text(encoding="utf-8")
        self.assertIn("69 physical streets", network_figure)
        self.assertNotIn("138", network_figure)

    def test_08_manifest_locks_runs_sources_configuration_and_artifact_hashes(self):
        manifest = self.manifest
        self.assertEqual(manifest["final_comparison_status"], "PASS")
        self.assertTrue(manifest["git_provenance"]["phase_start"]["clean"])
        self.assertEqual(
            manifest["environments"]["env38"]["run_id"],
            json.loads((ROOT / "results/analysis/env38/latest.json").read_text())["canonical_run_id"],
        )
        self.assertEqual(
            manifest["environments"]["env39"]["run_id"],
            json.loads((ROOT / "results/analysis/env39/latest.json").read_text())["canonical_run_id"],
        )
        self.assertEqual(
            manifest["environments"]["env38"]["topology_signature"],
            manifest["environments"]["env39"]["topology_signature"],
        )
        self.assertEqual(manifest["accepted_sources_preserved"]["status"], "PASS")
        for record in manifest["artifacts"].values():
            path = ROOT / record["path"]
            self.assertTrue(path.is_file(), record["path"])
            self.assertEqual(sha256_file(path), record["file_sha256"])

    def test_09_phase9f_geometry_orientation_and_centrality_provenance_pass(self):
        phase9f = self.summary["phase9f_reproducibility"]
        self.assertEqual(phase9f["status"], "PASS")
        self.assertEqual(phase9f["comparison_ready_exact_match_count"], 39)
        self.assertEqual(phase9f["core_exact_match_count"], 19)
        self.assertEqual(phase9f["scientific_mismatch_count"], 0)
        self.assertIn("total_chord_length_local", self.summary["detailed_geometry"])
        self.assertIn("absolute_excess_length_local", self.summary["detailed_geometry"])
        self.assertIn("phi_chord", self.summary["detailed_orientation"])
        leaders = self.summary["length_weighted_centrality_leaders"]
        for environment in ("env39", "env38"):
            self.assertEqual(len(leaders[environment]["node_betweenness_length_top5"]), 5)
            self.assertEqual(len(leaders[environment]["edge_betweenness_length_top5"]), 5)

    def test_10_research_summary_is_descriptive_noncausal_and_machine_readable(self):
        source = (self.result.output_dir / "env38_vs_env39_summary.md").read_text(encoding="utf-8")
        for phrase in (
            "Research question", "Experimental control", "Data provenance",
            "Geometry-weighted network comparison", "Geometric morphology",
            "Orientation morphology", "Methodological limitations",
            "not longitude/latitude", "not metres", "Machine-readable outputs",
        ):
            self.assertIn(phrase, source)
        self.assertNotIn("objectively better", source)
        self.assertNotIn("causes better", source)

    def test_11_accepted_environment_artifacts_and_notebooks_are_immutable(self):
        self.assertEqual(self._protected_hashes(), self.protected_before)

    def test_12_official_versioned_comparison_package_is_hash_valid(self):
        packages = sorted(
            path for path in (ROOT / "results/comparison/env38_vs_env39").glob(
                "env38_vs_env39_*"
            ) if (path / "comparison_manifest.json").is_file()
        )
        self.assertGreaterEqual(len(packages), 1)
        for package in packages:
            manifest = json.loads(
                (package / "comparison_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["comparison_id"], package.name)
            self.assertEqual(manifest["final_comparison_status"], "PASS")
            self.assertEqual(manifest["metric_counts"], {"comparison": 39, "core": 19})
            for record in manifest["artifacts"].values():
                path = ROOT / record["path"]
                self.assertTrue(path.is_file())
                self.assertEqual(sha256_file(path), record["file_sha256"])


if __name__ == "__main__":
    unittest.main()
