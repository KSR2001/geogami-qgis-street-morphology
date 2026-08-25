from __future__ import annotations

from collections import Counter
import csv
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import uuid

import networkx as nx
import osmnx as ox


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from geogami_morphology.graph import (
    build_networkx_multigraph,
    build_osmnx_multidigraph,
    load_canonical_geopackage,
    resolve_canonical_run,
    validate_canonical_for_analysis,
)
from geogami_morphology.io import sha256_file
from geogami_morphology.metrics_topology import (
    EDGE_FIELDS,
    NODE_FIELDS,
    SUMMARY_FIELDS,
    TopologyMetricError,
    calculate_topology_metrics,
    load_topology_config,
    osmnx_street_crosscheck,
    parallel_physical_edge_count,
    publish_topology_metrics,
    validated_simple_graph,
)


def reciprocal_fixture(edges: list[tuple[str, str, str, float]]) -> tuple[nx.MultiGraph, nx.MultiDiGraph]:
    physical = nx.MultiGraph()
    directed = nx.MultiDiGraph()
    for edge_id, u, v, length in edges:
        physical.add_edge(
            u,
            v,
            key=0,
            edge_id=edge_id,
            canonical_u=u,
            canonical_v=v,
            canonical_key=0,
            length=length,
        )
        physical[u][v][0]["key"] = 0
        directed.add_edge(u, v, key=0, canonical_edge_id=edge_id, length=length)
        directed.add_edge(v, u, key=0, canonical_edge_id=edge_id, length=length)
    return physical, directed


class Phase7FTopologyMetricTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_topology_config(project_root=ROOT)
        cls.selection = resolve_canonical_run("env39", project_root=ROOT)
        nodes, edges = load_canonical_geopackage(cls.selection.canonical_path)
        canonical = validate_canonical_for_analysis(nodes, edges)
        cls.physical = build_networkx_multigraph(canonical, cls.selection)
        cls.directed = build_osmnx_multidigraph(canonical, cls.selection)
        cls.env39 = calculate_topology_metrics(cls.physical, cls.directed, cls.config)
        cls.frozen_source = ROOT / "data" / "baselines" / "grid" / "network_v2.gpkg"
        cls.frozen_canonical = ROOT / "data" / "canonical" / "grid" / "env39_canonical.gpkg"
        cls.before_hashes = (sha256_file(cls.frozen_source), sha256_file(cls.frozen_canonical))
        cls.scratch = ROOT / "tests" / ".tmp"
        cls.scratch.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        after = (sha256_file(cls.frozen_source), sha256_file(cls.frozen_canonical))
        if after != cls.before_hashes:
            raise AssertionError("Frozen inputs changed during Phase 7F tests.")

    def setUp(self):
        self.root = self.scratch / f"phase7f-{uuid.uuid4().hex}"
        self.root.mkdir()

    def tearDown(self):
        shutil.rmtree(self.root)

    def _path(self):
        return reciprocal_fixture([("E1", "A", "B", 1.0), ("E2", "B", "C", 2.0)])

    def _triangle(self):
        return reciprocal_fixture(
            [("E1", "A", "B", 1.0), ("E2", "B", "C", 1.0), ("E3", "A", "C", 10.0)]
        )

    def test_01_configuration_is_versioned_hashed_and_explicit(self):
        self.assertEqual(self.config.schema_version, "1.0.0")
        self.assertEqual(self.config.physical_length_attribute, "length")
        self.assertEqual(self.config.osmnx_intersection_min_streets, 2)
        self.assertEqual(self.config.file_sha256, sha256_file(ROOT / "config" / "metrics.yaml"))

    def test_02_unknown_configuration_fields_fail(self):
        path = self.root / "bad.yaml"
        path.write_text("schema_version: 1.0.0\ntopology: {unexpected: true}\n", encoding="utf-8")
        with self.assertRaisesRegex(TopologyMetricError, "unknown or missing"):
            load_topology_config(path, project_root=ROOT)

    def test_03_simple_conversion_preserves_known_topology_and_edge_ids(self):
        physical, _ = self._path()
        simple = validated_simple_graph(physical)
        self.assertEqual((simple.number_of_nodes(), simple.number_of_edges()), (3, 2))
        self.assertEqual({data["edge_id"] for *_, data in simple.edges(data=True)}, {"E1", "E2"})

    def test_04_simple_conversion_rejects_self_loops(self):
        physical = nx.MultiGraph()
        physical.add_edge("A", "A", key=0, edge_id="L", length=1.0)
        with self.assertRaisesRegex(TopologyMetricError, "self-loop"):
            validated_simple_graph(physical)

    def test_05_simple_conversion_rejects_parallel_physical_edges(self):
        physical = nx.MultiGraph()
        physical.add_edge("A", "B", key=0, edge_id="P0", length=1.0)
        physical.add_edge("A", "B", key=1, edge_id="P1", length=2.0)
        self.assertEqual(parallel_physical_edge_count(physical), 1)
        with self.assertRaisesRegex(TopologyMetricError, "parallel"):
            validated_simple_graph(physical)

    def test_06_disconnected_graph_fails_connected_only_metrics(self):
        physical, directed = reciprocal_fixture([("E1", "A", "B", 1.0), ("E2", "C", "D", 1.0)])
        with self.assertRaisesRegex(TopologyMetricError, "require one connected component"):
            calculate_topology_metrics(physical, directed, self.config)

    def test_07_known_path_degree_counts_and_mean_are_correct(self):
        physical, directed = self._path()
        calculation = calculate_topology_metrics(physical, directed, self.config)
        self.assertEqual(Counter(row["degree"] for row in calculation.node_rows), {1: 2, 2: 1})
        self.assertAlmostEqual(calculation.metric("degree_mean"), 4 / 3)

    def test_08_handshake_theorem_is_calculated_independently(self):
        physical, directed = self._path()
        calculation = calculate_topology_metrics(physical, directed, self.config)
        self.assertEqual(calculation.metric("degree_sum"), 4)
        self.assertEqual(calculation.metric("twice_physical_edge_count"), 4)

    def test_09_cycle_rank_formula_on_tree_and_triangle(self):
        path = calculate_topology_metrics(*self._path(), self.config)
        triangle = calculate_topology_metrics(*self._triangle(), self.config)
        self.assertEqual(path.metric("cycle_rank"), 0)
        self.assertEqual(triangle.metric("cycle_rank"), 1)

    def test_10_dead_ends_are_degree_one_nodes(self):
        calculation = calculate_topology_metrics(*self._path(), self.config)
        self.assertEqual(calculation.metric("dead_end_count"), 2)
        self.assertAlmostEqual(calculation.metric("dead_end_proportion"), 2 / 3)

    def test_11_bridges_preserve_canonical_edge_ids(self):
        calculation = calculate_topology_metrics(*self._path(), self.config)
        self.assertEqual({row["edge_id"] for row in calculation.edge_rows if row["is_bridge"]}, {"E1", "E2"})

    def test_12_articulation_points_preserve_canonical_node_ids(self):
        calculation = calculate_topology_metrics(*self._path(), self.config)
        self.assertEqual({row["node_id"] for row in calculation.node_rows if row["is_articulation_point"]}, {"B"})

    def test_13_unweighted_shortest_path_metrics_match_known_path(self):
        calculation = calculate_topology_metrics(*self._path(), self.config)
        self.assertAlmostEqual(calculation.metric("average_shortest_path_hops"), 4 / 3)
        self.assertEqual(calculation.metric("diameter_hops"), 2)
        self.assertEqual(calculation.metric("radius_hops"), 1)

    def test_14_weighted_shortest_path_uses_local_length_cost(self):
        calculation = calculate_topology_metrics(*self._path(), self.config)
        self.assertAlmostEqual(calculation.metric("average_shortest_path_length_local"), 2.0)

    def test_15_weighted_betweenness_uses_length_and_differs_when_expected(self):
        calculation = calculate_topology_metrics(*self._triangle(), self.config)
        b = next(row for row in calculation.node_rows if row["node_id"] == "B")
        self.assertEqual(b["betweenness_unweighted"], 0.0)
        self.assertEqual(b["betweenness_length"], 1.0)

    def test_16_weighted_closeness_uses_length_distance(self):
        calculation = calculate_topology_metrics(*self._triangle(), self.config)
        values = {row["node_id"]: row for row in calculation.node_rows}
        self.assertAlmostEqual(values["B"]["closeness_length"], 1.0)
        self.assertAlmostEqual(values["A"]["closeness_length"], 2 / 3)

    def test_17_edge_betweenness_maps_deterministically_to_edge_id(self):
        calculation = calculate_topology_metrics(*self._path(), self.config)
        self.assertEqual([row["edge_id"] for row in calculation.edge_rows], ["E1", "E2"])
        self.assertTrue(all(row["betweenness_unweighted"] > 0 for row in calculation.edge_rows))

    def test_17b_edge_output_preserves_canonical_orientation_if_graph_insertion_is_reversed(self):
        physical, directed = reciprocal_fixture([("E1", "B", "A", 1.0), ("E2", "B", "C", 1.0)])
        physical["B"]["A"][0]["canonical_u"] = "A"
        physical["B"]["A"][0]["canonical_v"] = "B"
        calculation = calculate_topology_metrics(physical, directed, self.config)
        edge = next(row for row in calculation.edge_rows if row["edge_id"] == "E1")
        self.assertEqual((edge["u"], edge["v"], edge["key"]), ("A", "B", 0))

    def test_18_osmnx_count_streets_per_node_handles_reciprocal_fixture(self):
        _, directed = self._path()
        self.assertEqual(ox.stats.count_streets_per_node(directed), {"A": 1, "B": 2, "C": 1})

    def test_19_osmnx_and_networkx_counts_agree_node_by_node(self):
        physical, directed = self._path()
        crosscheck = osmnx_street_crosscheck(physical, directed, min_streets=2)
        self.assertTrue(all(row["networkx_degree"] == row["osmnx_street_count"] for row in crosscheck["node_by_node"]))

    def test_20_osmnx_street_segment_count_is_physical_not_directed(self):
        physical, directed = self._path()
        crosscheck = osmnx_street_crosscheck(physical, directed, min_streets=2)
        self.assertEqual(crosscheck["physical_street_segment_count"], 2)
        self.assertEqual(crosscheck["directed_arc_count_not_physical_streets"], 4)

    def test_21_osmnx_intersection_count_uses_configured_minimum(self):
        physical, directed = self._path()
        self.assertEqual(osmnx_street_crosscheck(physical, directed, min_streets=2)["intersection_count"], 1)
        self.assertEqual(osmnx_street_crosscheck(physical, directed, min_streets=3)["intersection_count"], 0)

    def test_22_osmnx_self_loop_proportion_fixture(self):
        graph = nx.MultiGraph()
        graph.add_edge("A", "A", key=0)
        graph.add_edge("A", "B", key=0)
        self.assertEqual(ox.stats.self_loop_proportion(graph), 0.5)

    def test_23_env39_identity_regression(self):
        self.assertEqual((self.env39.metric("node_count"), self.env39.metric("physical_edge_count"), self.env39.metric("connected_component_count")), (46, 69, 1))

    def test_24_env39_degree_distribution_and_mean_regression(self):
        self.assertEqual(Counter(row["degree"] for row in self.env39.node_rows), {1: 10, 3: 16, 4: 20})
        self.assertEqual(self.env39.metric("degree_mean"), 3.0)

    def test_25_env39_handshake_and_cycle_rank_regression(self):
        self.assertEqual((self.env39.metric("degree_sum"), self.env39.metric("twice_physical_edge_count")), (138, 138))
        self.assertEqual(self.env39.metric("cycle_rank"), 24)

    def test_26_published_outputs_have_stable_schemas_hashes_and_provenance(self):
        output_dir, manifest_path, manifest = publish_topology_metrics(
            self.selection,
            self.env39,
            self.config,
            self.physical,
            self.directed,
            output_root=self.root,
            project_root=ROOT,
        )
        self.assertTrue(manifest_path.is_file())
        self.assertEqual(manifest["canonical"]["file_sha256"], self.selection.file_sha256)
        self.assertEqual(
            manifest["canonical"]["scientific_content_signature"],
            self.selection.scientific_content_signature,
        )
        self.assertEqual(manifest["canonical"]["topology_signature"], self.selection.topology_signature)
        publication_git = manifest["canonical"]["publication_git_provenance"]
        self.assertFalse(publication_git["dirty"])
        self.assertEqual(publication_git["changed_paths"], [])
        self.assertTrue(publication_git["branch"])
        self.assertRegex(publication_git["commit_sha"], r"^[0-9a-f]{40}$")

        analysis_latest = json.loads(
            (ROOT / "results" / "analysis" / "env39" / "latest.json").read_text(encoding="utf-8")
        )
        workflow_manifest = json.loads(
            (ROOT / analysis_latest["analysis_manifest_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(workflow_manifest["canonical_run_id"], analysis_latest["canonical_run_id"])
        workflow_start_git = workflow_manifest["workflow_start_git"]
        workflow_end_git = workflow_manifest["workflow_end_git"]
        acceptance = workflow_manifest["provenance_acceptance"]
        self.assertFalse(workflow_start_git["dirty"])
        self.assertEqual(workflow_start_git["changed_paths"], [])
        self.assertEqual(workflow_start_git["commit_sha"], publication_git["commit_sha"])
        self.assertEqual(workflow_start_git["branch"], publication_git["branch"])
        self.assertEqual(workflow_end_git["commit_sha"], workflow_start_git["commit_sha"])
        self.assertEqual(workflow_end_git["branch"], workflow_start_git["branch"])
        for field in (
            "clean_start",
            "head_unchanged",
            "source_code_unchanged",
            "configuration_unchanged",
            "editable_input_unchanged",
            "frozen_inputs_unchanged",
            "only_expected_generated_changes",
        ):
            self.assertTrue(acceptance[field], field)
        self.assertEqual(acceptance["status"], "PASS")
        self.assertEqual(acceptance["unexpected_changes"], [])
        self.assertEqual(manifest["metrics_configuration"]["file_sha256"], self.config.file_sha256)
        for artifact in manifest["artifacts"].values():
            self.assertEqual(artifact["file_sha256"], sha256_file(ROOT / artifact["path"]))
        with (output_dir / "node_topology_metrics.csv").open(encoding="utf-8", newline="") as stream:
            self.assertEqual(next(csv.reader(stream)), NODE_FIELDS)
        with (output_dir / "edge_topology_metrics.csv").open(encoding="utf-8", newline="") as stream:
            self.assertEqual(next(csv.reader(stream)), EDGE_FIELDS)
        with (output_dir / "topology_metrics_summary.csv").open(encoding="utf-8", newline="") as stream:
            self.assertEqual(next(csv.reader(stream)), SUMMARY_FIELDS)
        first_hashes = {name: record["file_sha256"] for name, record in manifest["artifacts"].items()}
        _, _, repeated_manifest = publish_topology_metrics(
            self.selection,
            self.env39,
            self.config,
            self.physical,
            self.directed,
            output_root=self.root,
            project_root=ROOT,
        )
        self.assertEqual(
            first_hashes,
            {name: record["file_sha256"] for name, record in repeated_manifest["artifacts"].items()},
        )

    def test_27_local_unit_labels_and_methodology_are_explicit(self):
        row = next(row for row in self.env39.summary_rows if row["metric_name"] == "average_shortest_path_length_local")
        self.assertEqual((row["units"], row["weighting"]), ("local units", "weight = length"))
        self.assertEqual(self.env39.methodology["centrality"]["closeness_length"].split(";")[-1].strip(), "units are 1 / local unit")

    def test_28_no_geographic_osmnx_function_is_invoked(self):
        physical, directed = self._path()
        with patch("osmnx.distance.add_edge_lengths") as lengths, patch("osmnx.bearing.add_edge_bearings") as bearings:
            calculate_topology_metrics(physical, directed, self.config)
        lengths.assert_not_called()
        bearings.assert_not_called()

    def test_29_frozen_hashes_and_topology_signature_are_unchanged(self):
        self.assertEqual(self.before_hashes, (
            "7698544A01EED008E5451F8368703AFD0DBE232D10C9D7ADAA58C13DA57E0289",
            "212A2AA583BF77304859DB76639B8AD4B6DC458237EEA24FEB60FC228E5D6847",
        ))
        self.assertEqual(self.selection.topology_signature, "4AF93910532EB430103267DAECF73B3ADC3B3D1599471EDF6B1632B6C5F898CB")


if __name__ == "__main__":
    unittest.main()
