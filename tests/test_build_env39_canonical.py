import copy
from collections import Counter
import math
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import audit_env39_geometry as phase5a
import build_env39_canonical as canonical


class Phase5BCanonicalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = canonical.DEFAULT_SOURCE
        cls.hash_before = phase5a.sha256_file(cls.source)
        cls.build = canonical.build_canonical(cls.source)
        cls.hash_after = phase5a.sha256_file(cls.source)
        cls.nodes_by_id = {record["node_id"]: record for record in cls.build.node_records}
        cls.edges_by_id = {record["edge_id"]: record for record in cls.build.edge_records}

    def test_01_source_geopackage_immutability(self):
        self.assertEqual(self.hash_before, canonical.EXPECTED_SOURCE_SHA256)
        self.assertEqual(self.hash_before, self.hash_after)
        self.assertEqual(self.hash_after, phase5a.sha256_file(self.source))

    def test_02_exact_endpoint_graph_reconstruction(self):
        self.assertEqual(self.build.source_graph.number_of_nodes(), 53)
        self.assertEqual(self.build.source_graph.number_of_edges(), 76)
        exact_nodes = {
            tuple(float(value) for value in endpoint)
            for _fid, geometry in self.build.source_frame.geometry.items()
            for endpoint in (geometry.coords[0], geometry.coords[-1])
        }
        self.assertEqual(set(self.build.source_graph.nodes), exact_nodes)

    def test_03_all_seven_approved_nodes_are_suppressed(self):
        self.assertEqual(len(self.build.suppression_records), 7)
        self.assertEqual(
            {record["source_tmp_node_id"] for record in self.build.suppression_records},
            {approval.temporary_node_id for approval in canonical.APPROVED_SUPPRESSIONS},
        )
        surviving_coordinates = {
            tuple(data["coordinate"])
            for _node, data in self.build.canonical_graph.nodes(data=True)
        }
        self.assertTrue(
            all(tuple(approval.coordinate) not in surviving_coordinates for approval in canonical.APPROVED_SUPPRESSIONS)
        )

    def test_04_canonical_node_count(self):
        self.assertEqual(self.build.canonical_graph.number_of_nodes(), 46)

    def test_05_canonical_edge_count(self):
        self.assertEqual(self.build.canonical_graph.number_of_edges(), 69)

    def test_06_canonical_degree_distribution(self):
        observed = dict(sorted(Counter(dict(self.build.canonical_graph.degree()).values()).items()))
        self.assertEqual(observed, {1: 10, 3: 16, 4: 20})
        self.assertNotIn(2, observed)

    def test_07_single_connected_component(self):
        self.assertEqual(canonical.nx.number_connected_components(self.build.canonical_graph), 1)

    def test_08_handshake_theorem(self):
        degree_sum = sum(dict(self.build.canonical_graph.degree()).values())
        self.assertEqual(degree_sum, 138)
        self.assertEqual(degree_sum, 2 * self.build.canonical_graph.number_of_edges())

    def test_09_ninety_degree_bends_remain_internal_vertices(self):
        angular = [item for item in canonical.APPROVED_SUPPRESSIONS if item.classification == "angular_bend"]
        self.assertEqual(len(angular), 2)
        for approval in angular:
            log = next(item for item in self.build.suppression_records if item["source_tmp_node_id"] == approval.temporary_node_id)
            coordinates = tuple(tuple(value) for value in self.edges_by_id[log["resulting_canonical_edge"]]["coordinates"])
            self.assertIn(tuple(approval.coordinate), coordinates[1:-1])
            turns = phase5a.internal_turn_diagnostics(coordinates)["turns"]
            matching = [item for item in turns if (item["x"], item["y"]) == approval.coordinate]
            self.assertTrue(matching)
            self.assertTrue(any(math.isclose(item["direction_change_degrees"], 90.0, abs_tol=1e-9) for item in matching))

    def test_10_straight_continuation_nodes_are_internal_vertices(self):
        straight = [item for item in canonical.APPROVED_SUPPRESSIONS if item.classification == "straight_continuation"]
        self.assertEqual(len(straight), 5)
        for approval in straight:
            log = next(item for item in self.build.suppression_records if item["source_tmp_node_id"] == approval.temporary_node_id)
            coordinates = tuple(tuple(value) for value in self.edges_by_id[log["resulting_canonical_edge"]]["coordinates"])
            self.assertIn(tuple(approval.coordinate), coordinates[1:-1])
            turns = phase5a.internal_turn_diagnostics(coordinates)["turns"]
            matching = [item for item in turns if (item["x"], item["y"]) == approval.coordinate]
            self.assertTrue(matching)
            self.assertTrue(any(math.isclose(item["direction_change_degrees"], 0.0, abs_tol=1e-9) for item in matching))

    def test_11_consecutive_duplicates_removed_only_in_derived_output(self):
        self.assertEqual(self.build.duplicate_coordinates_removed, 25)
        source_duplicates = sum(
            first == second
            for geometry in self.build.source_frame.geometry
            for first, second in zip(geometry.coords, list(geometry.coords)[1:])
        )
        self.assertEqual(source_duplicates, 25)
        for record in self.build.edge_records:
            coordinates = record["coordinates"]
            self.assertFalse(any(first == second for first, second in zip(coordinates, coordinates[1:])))
        self.assertEqual(self.hash_before, phase5a.sha256_file(self.source))

    def test_12_total_network_length_preserved(self):
        self.assertTrue(
            math.isclose(
                self.build.source_total_length,
                self.build.canonical_total_length,
                rel_tol=0.0,
                abs_tol=canonical.LENGTH_ABSOLUTE_TOLERANCE,
            )
        )
        self.assertTrue(
            math.isclose(
                self.build.source_total_length,
                canonical.EXPECTED_SOURCE_LENGTH,
                rel_tol=0.0,
                abs_tol=canonical.LENGTH_ABSOLUTE_TOLERANCE,
            )
        )

    def test_13_canonical_node_id_determinism(self):
        coordinates = [tuple(data["coordinate"]) for _node, data in self.build.canonical_graph.nodes(data=True)]
        first = canonical.assign_canonical_node_ids(coordinates)
        second = canonical.assign_canonical_node_ids(reversed(coordinates))
        self.assertEqual(first, second)
        ordered = sorted(first, key=lambda coordinate: (-coordinate[1], coordinate[0], coordinate[2:]))
        self.assertEqual([first[item] for item in ordered], [f"N{index:03d}" for index in range(1, 47)])

    def test_14_canonical_edge_id_determinism(self):
        node_ids = {tuple(data["coordinate"]): node for node, data in self.build.canonical_graph.nodes(data=True)}
        candidates = canonical._path_candidates(
            self.build.source_graph,
            {tuple(item.coordinate): item for item in canonical.APPROVED_SUPPRESSIONS},
        )
        first = canonical.assign_canonical_edges(copy.deepcopy(candidates), node_ids)
        second = canonical.assign_canonical_edges(list(reversed(copy.deepcopy(candidates))), node_ids)
        identity = lambda records: [(item["edge_id"], item["u"], item["v"], item["key"]) for item in records]
        self.assertEqual(identity(first), identity(second))
        self.assertEqual([item["edge_id"] for item in first], [f"E{index:03d}" for index in range(1, 70)])

    def test_15_canonical_ids_are_independent_of_source_fids(self):
        node_ids = {tuple(data["coordinate"]): node for node, data in self.build.canonical_graph.nodes(data=True)}
        candidates = canonical._path_candidates(
            self.build.source_graph,
            {tuple(item.coordinate): item for item in canonical.APPROVED_SUPPRESSIONS},
        )
        changed = copy.deepcopy(candidates)
        for candidate in changed:
            candidate["source_fids_ordered"] = [fid + 1000 for fid in candidate["source_fids_ordered"]]
        original_edges = canonical.assign_canonical_edges(copy.deepcopy(candidates), node_ids)
        changed_edges = canonical.assign_canonical_edges(changed, node_ids)
        scientific_ids = lambda records: [(item["edge_id"], item["u"], item["v"], item["key"]) for item in records]
        self.assertEqual(scientific_ids(original_edges), scientific_ids(changed_edges))

    def test_16_topology_signature_determinism(self):
        first_hash, first_text = canonical.topology_signature(self.build.edge_records)
        second_hash, second_text = canonical.topology_signature(list(reversed(self.build.edge_records)))
        self.assertEqual(first_hash, second_hash)
        self.assertEqual(first_text, second_text)
        self.assertEqual(first_hash, self.build.topology_signature_sha256)
        self.assertNotIn("source_fids", first_text)

    def test_17_edge_geometry_endpoints_match_canonical_nodes(self):
        for record in self.build.edge_records:
            coordinates = record["coordinates"]
            u = self.nodes_by_id[record["u"]]
            v = self.nodes_by_id[record["v"]]
            self.assertEqual(tuple(coordinates[0][:2]), (u["x"], u["y"]))
            self.assertEqual(tuple(coordinates[-1][:2]), (v["x"], v["y"]))

    def test_18_local_cartesian_crs_preserved(self):
        crs = self.build.source_frame.crs
        self.assertIsNotNone(crs)
        self.assertFalse(crs.is_geographic)
        self.assertIn("GeoGami Local Cartesian", str(crs))


if __name__ == "__main__":
    unittest.main()
