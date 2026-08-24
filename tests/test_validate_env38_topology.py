from pathlib import Path
import sys
import unittest

import geopandas as gpd
from shapely.affinity import translate
from shapely.geometry import LineString, Point


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import build_env38_working as builder
import validate_env38_topology as validator


class Phase6ATopologyControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.canonical = validator.DEFAULT_CANONICAL
        cls.canonical_hash_before = validator.sha256_file(cls.canonical)
        cls.root = PROJECT_ROOT / "results" / ".phase6a_test_work"
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.template = cls.root / "env38_working.gpkg"
        builder.create_template(cls.canonical, cls.template)
        cls.nodes = gpd.read_file(cls.template, layer="nodes")
        cls.edges = gpd.read_file(cls.template, layer="edges")
        cls.baseline = validator.validate_candidate(cls.template, cls.canonical)

    @classmethod
    def tearDownClass(cls):
        for path in cls.root.glob("*"):
            if path.is_file():
                path.unlink()
        cls.root.rmdir()

    def _write(self, name, mutate):
        nodes, edges = self.nodes.copy(), self.edges.copy()
        mutate(nodes, edges)
        path = self.root / f"{name}.gpkg"
        nodes.to_file(path, layer="nodes", driver="GPKG", index=False)
        edges.to_file(path, layer="edges", driver="GPKG", mode="a", index=False)
        return validator.validate_candidate(path, self.canonical)

    def assertTopologyPass(self, result):
        self.assertEqual(result.report["topology_control_status"], "PASS")

    def assertTopologyFail(self, result):
        self.assertEqual(result.report["topology_control_status"], "FAIL")

    def test_01_canonical_source_is_immutable(self):
        self.assertEqual(self.canonical_hash_before, validator.EXPECTED_CANONICAL_SHA256)
        self.assertEqual(validator.sha256_file(self.canonical), self.canonical_hash_before)

    def test_02_working_template_and_provenance(self):
        self.assertEqual(len(self.nodes), 46)
        self.assertEqual(len(self.edges), 69)
        self.assertTrue((self.nodes["environment"] == "env38").all())
        self.assertTrue((self.edges["geometry_status"] == "template").all())
        self.assertEqual(self.nodes.crs, gpd.read_file(self.canonical, layer="nodes").crs)

    def test_03_template_fully_validates(self):
        report = self.baseline.report
        self.assertEqual(report["final_result"], "PASS")
        self.assertEqual(report["topology_control"]["component_count"], 1)
        self.assertEqual(report["topology_control"]["degree_distribution"], {"1": 10, "3": 16, "4": 20})
        self.assertEqual(report["topology_control"]["topology_signature_sha256"], validator.EXPECTED_TOPOLOGY_SIGNATURE)
        self.assertEqual(report["geometric_realization_qa_status"], "PASS")

    def test_04_geometry_independent_topology_signature(self):
        tuples = [validator.normalize_tuple(row.u, row.v, row.key) for row in self.edges.itertuples()]
        first, text = validator.topology_signature(tuples)
        second, _ = validator.topology_signature(reversed(tuples))
        self.assertEqual(first, second)
        self.assertEqual(first, validator.EXPECTED_TOPOLOGY_SIGNATURE)
        self.assertNotIn("LINESTRING", text)

    def test_05_node_coordinate_changes_are_allowed(self):
        def mutate(nodes, edges):
            nodes.geometry = nodes.geometry.map(lambda geometry: translate(geometry, xoff=17.25, yoff=-9.5))
            edges.geometry = edges.geometry.map(lambda geometry: translate(geometry, xoff=17.25, yoff=-9.5))
        result = self._write("translated", mutate)
        self.assertEqual(result.report["final_result"], "PASS")

    def test_06_curved_multivertex_geometry_is_allowed(self):
        def mutate(_nodes, edges):
            geometry = edges.geometry.iloc[0]
            start, end = geometry.coords[0], geometry.coords[-1]
            midpoint = ((start[0] + end[0]) / 2 + 0.01, (start[1] + end[1]) / 2 + 0.01)
            edges.at[edges.index[0], "geometry"] = LineString([start, midpoint, end])
        result = self._write("curved", mutate)
        self.assertEqual(result.report["final_result"], "PASS")

    def test_07_reversed_linestring_orientation_is_allowed(self):
        def mutate(_nodes, edges):
            geometry = edges.geometry.iloc[0]
            edges.at[edges.index[0], "geometry"] = LineString(reversed(geometry.coords))
        result = self._write("reversed", mutate)
        self.assertEqual(result.report["final_result"], "PASS")

    def test_08_edge_length_change_is_allowed(self):
        def mutate(nodes, edges):
            degrees = dict()
            for row in edges.itertuples():
                degrees[row.u] = degrees.get(row.u, 0) + 1
                degrees[row.v] = degrees.get(row.v, 0) + 1
            leaf = next(node for node, degree in degrees.items() if degree == 1)
            node_index = nodes.index[nodes.node_id == leaf][0]
            point = nodes.at[node_index, "geometry"]
            moved = (point.x + 0.02, point.y + 0.02)
            nodes.at[node_index, "geometry"] = Point(moved)
            edge_index = edges.index[(edges.u == leaf) | (edges.v == leaf)][0]
            coordinates = list(edges.at[edge_index, "geometry"].coords)
            if tuple(coordinates[0][:2]) == (point.x, point.y): coordinates[0] = moved
            else: coordinates[-1] = moved
            edges.at[edge_index, "geometry"] = LineString(coordinates)
        result = self._write("length_changed", mutate)
        self.assertEqual(result.report["final_result"], "PASS")

    def test_09_changed_adjacency_fails(self):
        def mutate(_nodes, edges): edges.at[edges.index[0], "u"] = "N046"
        result = self._write("changed_adjacency", mutate)
        self.assertTopologyFail(result)
        self.assertTrue(result.report["topology_control"]["changed_adjacency"])

    def test_10_deleted_and_added_nodes_fail(self):
        deleted = self._write("deleted_node", lambda nodes, _edges: nodes.drop(nodes.index[0], inplace=True))
        self.assertTopologyFail(deleted)
        def add(nodes, _edges):
            nodes.loc[len(nodes)] = {**{column: None for column in nodes.columns}, "node_id": "N047", "geometry": Point(-999, -999)}
            nodes.set_crs(self.nodes.crs, inplace=True, allow_override=True)
        added = self._write("added_node", add)
        self.assertTopologyFail(added)

    def test_11_deleted_and_added_edges_fail(self):
        deleted = self._write("deleted_edge", lambda _nodes, edges: edges.drop(edges.index[0], inplace=True))
        self.assertTopologyFail(deleted)
        def add(_nodes, edges):
            record = edges.iloc[0].copy(); record["edge_id"] = "E070"
            edges.loc[len(edges)] = record
            edges.set_crs(self.edges.crs, inplace=True, allow_override=True)
        added = self._write("added_edge", add)
        self.assertTopologyFail(added)

    def test_12_changed_edge_id_fails(self):
        result = self._write("changed_edge_id", lambda _nodes, edges: edges.__setitem__("edge_id", edges.edge_id.mask(edges.index == edges.index[0], "E999")))
        self.assertTopologyFail(result)

    def test_13_changed_key_fails(self):
        def mutate(_nodes, edges): edges.at[edges.index[0], "key"] = 1
        result = self._write("changed_key", mutate)
        self.assertTopologyFail(result)
        self.assertTrue(result.report["topology_control"]["changed_key"])

    def test_14_endpoint_mismatch_only_fails_geometry(self):
        def mutate(_nodes, edges):
            coordinates = list(edges.geometry.iloc[0].coords)
            coordinates[0] = (coordinates[0][0] + 0.1, coordinates[0][1])
            edges.at[edges.index[0], "geometry"] = LineString(coordinates)
        result = self._write("endpoint_mismatch", mutate)
        self.assertTopologyPass(result)
        self.assertEqual(result.report["geometric_realization_qa_status"], "FAIL")
        self.assertEqual(result.report["topology_control"]["topology_signature_sha256"], validator.EXPECTED_TOPOLOGY_SIGNATURE)

    def test_15_unintended_non_node_crossing_fails_geometry(self):
        def mutate(_nodes, edges):
            for first in range(len(edges)):
                row_1 = edges.iloc[first]
                for second in range(len(edges)):
                    row_2 = edges.iloc[second]
                    if first == second or {row_1.u, row_1.v} & {row_2.u, row_2.v}: continue
                    point = row_2.geometry.interpolate(0.5, normalized=True)
                    candidate = LineString([row_1.geometry.coords[0], (point.x, point.y), row_1.geometry.coords[-1]])
                    if candidate.is_simple and candidate.length > 0:
                        edges.at[edges.index[first], "geometry"] = candidate
                        return
            raise AssertionError("Could not construct crossing test geometry")
        result = self._write("crossing", mutate)
        self.assertTopologyPass(result)
        self.assertEqual(result.report["geometric_realization_qa_status"], "FAIL")
        types = {item["issue_type"] for item in result.report["diagnostics"]}
        self.assertIn("unintended_edge_crossing", types)

    def test_16_canonical_hash_remains_unchanged_after_all_tests(self):
        self.assertEqual(validator.sha256_file(self.canonical), self.canonical_hash_before)


if __name__ == "__main__":
    unittest.main()
