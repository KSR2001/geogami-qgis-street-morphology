from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import uuid

import geopandas as gpd
from shapely.geometry import LineString, Point


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from geogami_morphology.graph import load_canonical_geopackage, resolve_canonical_run, validate_canonical_for_analysis
from geogami_morphology.io import sha256_file
from geogami_morphology.metrics_geometry import (
    EDGE_FIELDS,
    SEGMENT_FIELDS,
    SUMMARY_FIELDS,
    GeometryMetricError,
    calculate_geometry_metrics,
    linestring_segment_records,
    load_geometry_config,
    planar_chord_length,
    planar_edge_circuity,
    publish_geometry_metrics,
)
from geogami_morphology.metrics_orientation import (
    OrientationMetricError,
    axial_orientation_histogram,
    fourfold_orientation_order,
    planar_axial_orientation,
    shannon_orientation_entropy,
)
from geogami_morphology.versioned import git_provenance


def directory_hashes(path: Path) -> dict[str, str]:
    return {item.name: sha256_file(item) for item in sorted(path.iterdir()) if item.is_file()}


class Phase7GGeometryOrientationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selection = resolve_canonical_run("env39", project_root=ROOT)
        nodes, edges = load_canonical_geopackage(cls.selection.canonical_path)
        cls.canonical = validate_canonical_for_analysis(nodes, edges)
        cls.crs = cls.canonical.nodes.crs
        cls.config = load_geometry_config(project_root=ROOT)
        cls.env39 = calculate_geometry_metrics(cls.canonical.nodes, cls.canonical.edges, cls.config)
        cls.topology_dir = ROOT / "results" / "analysis" / "env39" / cls.selection.run_id / "topology"
        cls.topology_before = directory_hashes(cls.topology_dir)
        cls.latest_path = ROOT / "data" / "canonical" / "grid" / "latest.json"
        cls.latest_before = cls.latest_path.read_bytes()
        cls.frozen_source = ROOT / "data" / "baselines" / "grid" / "network_v2.gpkg"
        cls.frozen_canonical = ROOT / "data" / "canonical" / "grid" / "env39_canonical.gpkg"
        cls.frozen_before = (sha256_file(cls.frozen_source), sha256_file(cls.frozen_canonical))
        cls.scratch = ROOT / "tests" / ".tmp"
        cls.scratch.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        if directory_hashes(cls.topology_dir) != cls.topology_before:
            raise AssertionError("Phase 7F outputs changed during Phase 7G tests.")
        if cls.latest_path.read_bytes() != cls.latest_before:
            raise AssertionError("latest.json changed during Phase 7G tests.")
        if (sha256_file(cls.frozen_source), sha256_file(cls.frozen_canonical)) != cls.frozen_before:
            raise AssertionError("Frozen inputs changed during Phase 7G tests.")

    def setUp(self):
        self.root = self.scratch / f"phase7g-{uuid.uuid4().hex}"
        self.root.mkdir()

    def tearDown(self):
        shutil.rmtree(self.root)

    def _network(self, specifications):
        node_coordinates: dict[str, tuple[float, float]] = {}
        edge_records = []
        geometries = []
        for edge_id, u, v, coordinates in specifications:
            node_coordinates.setdefault(u, tuple(coordinates[0]))
            node_coordinates.setdefault(v, tuple(coordinates[-1]))
            geometry = LineString(coordinates)
            edge_records.append({"edge_id": edge_id, "u": u, "v": v, "key": 0, "length_local": geometry.length})
            geometries.append(geometry)
        nodes = gpd.GeoDataFrame(
            {"node_id": sorted(node_coordinates)},
            geometry=[Point(node_coordinates[node]) for node in sorted(node_coordinates)],
            crs=self.crs,
        )
        edges = gpd.GeoDataFrame(edge_records, geometry=geometries, crs=self.crs)
        return nodes, edges

    def test_01_north_edge_is_zero_degrees(self):
        self.assertEqual(planar_axial_orientation((0, 0), (0, 1)), 0.0)

    def test_02_south_edge_is_axial_zero_degrees(self):
        self.assertEqual(planar_axial_orientation((0, 1), (0, 0)), 0.0)

    def test_03_east_edge_is_ninety_degrees(self):
        self.assertEqual(planar_axial_orientation((0, 0), (1, 0)), 90.0)

    def test_04_west_edge_is_axial_ninety_degrees(self):
        self.assertEqual(planar_axial_orientation((1, 0), (0, 0)), 90.0)

    def test_05_intercardinal_orientations_follow_north_clockwise_convention(self):
        self.assertEqual(planar_axial_orientation((0, 0), (1, 1)), 45.0)
        self.assertEqual(planar_axial_orientation((0, 0), (-1, 1)), 135.0)

    def test_06_reversing_endpoints_preserves_axial_orientation(self):
        forward = planar_axial_orientation((2, 3), (8, 12))
        reverse = planar_axial_orientation((8, 12), (2, 3))
        self.assertAlmostEqual(forward, reverse)

    def test_07_straight_horizontal_edge_has_unit_circuity(self):
        self.assertEqual(planar_edge_circuity(2, 2, zero_chord_tolerance=1e-9, floating_tolerance=1e-12), 1.0)

    def test_08_straight_vertical_edge_has_unit_circuity(self):
        self.assertEqual(planar_edge_circuity(3, 3, zero_chord_tolerance=1e-9, floating_tolerance=1e-12), 1.0)

    def test_09_bent_linestring_has_circuity_greater_than_one(self):
        geometry = LineString([(0, 0), (1, 1), (2, 0)])
        value = planar_edge_circuity(geometry.length, 2, zero_chord_tolerance=1e-9, floating_tolerance=1e-12)
        self.assertGreater(value, 1.0)

    def test_10_zero_chord_is_explicitly_ineligible(self):
        self.assertIsNone(planar_edge_circuity(2, 0, zero_chord_tolerance=1e-9, floating_tolerance=1e-12))

    def test_10b_zero_chord_network_record_is_classified_without_infinite_values(self):
        nodes, edges = self._network([
            ("E0", "A", "B", [(0, 0), (1, 0), (0, 0)]),
            ("E1", "B", "C", [(0, 0), (0, 2)]),
        ])
        calculation = calculate_geometry_metrics(nodes, edges, self.config)
        edge = next(row for row in calculation.edge_rows if row["edge_id"] == "E0")
        self.assertTrue(edge["zero_chord"])
        self.assertIsNone(edge["circuity"])
        self.assertIsNone(edge["chord_orientation_deg"])
        self.assertEqual(calculation.metric("zero_chord_edge_count"), 1)

    def test_11_zero_length_vector_and_segment_are_explicit(self):
        with self.assertRaises(OrientationMetricError):
            planar_axial_orientation((0, 0), (0, 0), zero_tolerance=1e-12)
        records, excluded = linestring_segment_records("E", LineString([(0, 0), (0, 0), (2, 0)]), zero_tolerance=1e-12)
        self.assertEqual((len(records), excluded, records[0]["segment_index"]), (1, 1, 1))

    def test_12_bent_polyline_yields_expected_segment_orientations(self):
        records, excluded = linestring_segment_records("E", LineString([(0, 0), (1, 1), (2, 0)]), zero_tolerance=1e-12)
        self.assertEqual(excluded, 0)
        self.assertEqual([row["segment_orientation_deg"] for row in records], [45.0, 135.0])

    def test_13_segment_length_weights_are_geometry_lengths(self):
        records, _ = linestring_segment_records("E", LineString([(0, 0), (3, 4), (3, 6)]), zero_tolerance=1e-12)
        self.assertEqual([row["segment_length_local"] for row in records], [5.0, 2.0])

    def test_14_single_orientation_bin_has_zero_entropy(self):
        entropy = shannon_orientation_entropy(axial_orientation_histogram([0, 0, 1], bins=36))
        self.assertEqual(entropy["entropy"], 0.0)

    def test_15_equal_known_bins_have_log_k_entropy(self):
        entropy = shannon_orientation_entropy(axial_orientation_histogram([0, 10, 20, 30], bins=36))
        self.assertAlmostEqual(entropy["entropy"], math.log(4))

    def test_16_normalized_entropy_is_bounded(self):
        entropy = shannon_orientation_entropy(axial_orientation_histogram(range(0, 180, 5), bins=36))
        self.assertGreaterEqual(entropy["normalized_entropy"], 0)
        self.assertLessEqual(entropy["normalized_entropy"], 1)

    def test_17_weighting_changes_histogram_probabilities(self):
        rows = axial_orientation_histogram([0, 10], weights=[3, 1], bins=36)
        self.assertEqual((rows[0]["probability"], rows[2]["probability"]), (0.75, 0.25))

    def test_17b_floating_values_near_a_bin_boundary_are_not_artificially_split(self):
        rows = axial_orientation_histogram(
            [90.0 - 1e-12, 90.0, 90.0 + 1e-12],
            bins=36,
            boundary_tolerance_degrees=1e-9,
        )
        self.assertEqual(rows[18]["observation_count"], 3)
        self.assertEqual(sum(row["observation_count"] for row in rows), 3)

    def test_18_pure_orthogonal_network_has_unit_fourfold_order(self):
        self.assertAlmostEqual(fourfold_orientation_order([0, 90, 0, 90]), 1.0)

    def test_19_rotating_all_orthogonal_streets_preserves_fourfold_order(self):
        self.assertAlmostEqual(fourfold_orientation_order([30, 120, 30, 120]), 1.0)

    def test_20_controlled_disordered_fixture_has_lower_fourfold_order(self):
        self.assertAlmostEqual(fourfold_orientation_order([0, 22.5, 45, 67.5]), 0.0, places=14)

    def test_21_phi_is_always_bounded_for_weighted_fixture(self):
        value = fourfold_orientation_order([1, 17, 64, 103], weights=[1, 4, 2, 8])
        self.assertGreaterEqual(value, 0)
        self.assertLessEqual(value, 1)

    def test_22_sum_segment_lengths_equals_linestring_lengths(self):
        nodes, edges = self._network([("E1", "A", "B", [(0, 0), (1, 1), (2, 0)]), ("E2", "B", "C", [(2, 0), (2, 3)])])
        calculation = calculate_geometry_metrics(nodes, edges, self.config)
        self.assertAlmostEqual(calculation.metric("total_geometry_segment_length_local"), calculation.metric("total_network_length_local"))

    def test_23_chord_reversal_does_not_change_length(self):
        first, second = Point(1, 2), Point(7, 11)
        self.assertEqual(planar_chord_length(first, second), planar_chord_length(second, first))

    def test_24_total_network_length_is_sum_of_edge_geometry_lengths(self):
        nodes, edges = self._network([("E1", "A", "B", [(0, 0), (1, 1), (2, 0)]), ("E2", "B", "C", [(2, 0), (2, 3)])])
        calculation = calculate_geometry_metrics(nodes, edges, self.config)
        self.assertAlmostEqual(calculation.metric("total_network_length_local"), sum(edge.length for edge in edges.geometry))

    def test_25_network_circuity_formula_is_independently_verified(self):
        nodes, edges = self._network([("E1", "A", "B", [(0, 0), (1, 1), (2, 0)]), ("E2", "B", "C", [(2, 0), (2, 3)])])
        calculation = calculate_geometry_metrics(nodes, edges, self.config)
        expected = sum(edge.length for edge in edges.geometry) / (2 + 3)
        self.assertAlmostEqual(calculation.metric("network_circuity"), expected)
        self.assertNotEqual(calculation.metric("network_circuity"), calculation.metric("mean_edge_circuity"))

    def test_26_env39_has_one_geometry_record_per_69_physical_edges(self):
        self.assertEqual(len(self.env39.edge_rows), 69)
        self.assertEqual(self.env39.metric("physical_edge_geometry_count"), 69)

    def test_27_env39_retains_46_canonical_nodes(self):
        self.assertEqual(self.env39.metric("node_count"), 46)

    def test_28_env39_analysis_does_not_alter_topology(self):
        self.assertEqual(self.selection.topology_signature, self.canonical.identity["topology_signature"])
        self.assertEqual((self.canonical.identity["node_count"], self.canonical.identity["physical_edge_count"]), (46, 69))

    def test_29_env39_topology_signature_regression(self):
        self.assertEqual(self.selection.topology_signature, "4AF93910532EB430103267DAECF73B3ADC3B3D1599471EDF6B1632B6C5F898CB")

    def test_30_no_geographic_osmnx_function_is_called(self):
        with patch("osmnx.distance.add_edge_lengths") as lengths, patch("osmnx.bearing.add_edge_bearings") as bearings:
            calculate_geometry_metrics(self.canonical.nodes, self.canonical.edges, self.config)
        lengths.assert_not_called()
        bearings.assert_not_called()

    def test_31_all_env39_eligible_circuities_are_at_least_one(self):
        values = [row["circuity"] for row in self.env39.edge_rows if row["circuity"] is not None]
        self.assertTrue(all(value >= 1 - self.config.circuity_floating_tolerance for value in values))

    def test_32_all_env39_orientations_are_in_axial_domain(self):
        chord = [row["chord_orientation_deg"] for row in self.env39.edge_rows if row["chord_orientation_deg"] is not None]
        segment = [row["segment_orientation_deg"] for row in self.env39.segment_rows]
        self.assertTrue(all(0 <= value < 180 for value in chord + segment))

    def test_33_env39_entropies_are_finite_and_normalized(self):
        names = ["chord_orientation_entropy", "segment_length_weighted_orientation_entropy"]
        self.assertTrue(all(math.isfinite(self.env39.metric(name)) for name in names))
        self.assertTrue(0 <= self.env39.metric("chord_orientation_normalized_entropy") <= 1)
        self.assertTrue(0 <= self.env39.metric("segment_length_weighted_normalized_entropy") <= 1)

    def test_34_env39_phi_values_are_finite_and_bounded(self):
        for name in ("phi_chord", "phi_segment_length_weighted"):
            self.assertTrue(math.isfinite(self.env39.metric(name)))
            self.assertTrue(0 <= self.env39.metric(name) <= 1)

    def test_35_phase7f_outputs_remain_byte_identical(self):
        self.assertEqual(directory_hashes(self.topology_dir), self.topology_before)

    def test_36_publication_has_stable_schemas_hashes_figures_and_provenance(self):
        expected_git = git_provenance(ROOT)
        output, manifest_path, manifest = publish_geometry_metrics(
            self.selection, self.env39, self.config, output_root=self.root, project_root=ROOT
        )
        self.assertTrue(manifest_path.is_file())
        self.assertEqual(manifest["canonical"]["topology_signature"], self.selection.topology_signature)
        recorded_git = manifest["analysis_git_provenance"]
        self.assertEqual(recorded_git, expected_git)
        self.assertEqual(recorded_git["repository"], ROOT.name)
        self.assertEqual(recorded_git["repository_root"], ".")
        self.assertRegex(recorded_git["commit_sha"], r"^[0-9a-f]{40}$")
        self.assertIsInstance(recorded_git["dirty"], bool)
        self.assertEqual(recorded_git["dirty"], bool(recorded_git["changed_paths"]))
        if expected_git["branch"] is None:
            self.assertIsNone(recorded_git["branch"])
        else:
            self.assertTrue(recorded_git["branch"])
        self.assertEqual(recorded_git["remote_origin"], expected_git["remote_origin"])
        self.assertEqual(manifest["orientation_convention"]["bin_count"], 36)
        for record in manifest["artifacts"].values():
            self.assertEqual(record["file_sha256"], sha256_file(ROOT / record["path"]))
        for filename, fields in (
            ("geometry_metrics_summary.csv", SUMMARY_FIELDS),
            ("edge_geometry_metrics.csv", EDGE_FIELDS),
            ("segment_orientation_metrics.csv", SEGMENT_FIELDS),
        ):
            with (output / filename).open(encoding="utf-8", newline="") as stream:
                self.assertEqual(next(csv.reader(stream)), fields)
        for figure in (output / "figures").glob("*.svg"):
            self.assertIn("<svg", figure.read_text(encoding="utf-8"))
        first = {name: record["file_sha256"] for name, record in manifest["artifacts"].items()}
        _, _, repeated = publish_geometry_metrics(self.selection, self.env39, self.config, output_root=self.root, project_root=ROOT)
        self.assertEqual(first, {name: record["file_sha256"] for name, record in repeated["artifacts"].items()})

    def test_37_methodology_preserves_historical_h4_distinction(self):
        self.assertIn("not used", self.env39.methodology["circuity"]["historical_H4"])
        self.assertNotIn("osmnx.plot_orientation", json.dumps(self.env39.edge_rows))

    def test_38_frozen_hashes_latest_and_config_identity_are_preserved(self):
        self.assertEqual(self.frozen_before, (
            "7698544A01EED008E5451F8368703AFD0DBE232D10C9D7ADAA58C13DA57E0289",
            "212A2AA583BF77304859DB76639B8AD4B6DC458237EEA24FEB60FC228E5D6847",
        ))
        self.assertEqual(self.latest_path.read_bytes(), self.latest_before)
        self.assertEqual(self.config.file_sha256, sha256_file(ROOT / "config" / "metrics.yaml"))


if __name__ == "__main__":
    unittest.main()
