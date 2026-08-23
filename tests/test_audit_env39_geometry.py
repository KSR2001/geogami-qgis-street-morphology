import json
from pathlib import Path
import sys
import unittest

from shapely.geometry import LineString


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import audit_env39_geometry as audit


def edge_row(fid, coordinates):
    geometry = LineString(coordinates)
    return (fid, geometry, audit.analyze_edge(fid, geometry))


class ExactTopologyTests(unittest.TestCase):
    def test_exact_endpoint_node_derivation_and_degree(self):
        rows = [
            edge_row(1, [(0, 0), (1, 0)]),
            edge_row(2, [(1, 0), (2, 0)]),
            edge_row(3, [(1, 0), (1, 1)]),
        ]
        graph, nodes, _degree2 = audit.derive_nodes_and_graph(rows)
        self.assertEqual(graph.number_of_nodes(), 4)
        by_coordinate = {(record["x"], record["y"]): record for record in nodes}
        self.assertEqual(by_coordinate[(1.0, 0.0)]["degree"], 3)
        self.assertEqual(by_coordinate[(1.0, 0.0)]["incident_edge_count"], 3)
        self.assertEqual(by_coordinate[(1.0, 0.0)]["incident_source_fids"], [1, 2, 3])

    def test_close_but_unequal_endpoints_remain_distinct(self):
        rows = [
            edge_row(1, [(0, 0), (1, 0)]),
            edge_row(2, [(1.0000001, 0), (2, 0)]),
        ]
        graph, _nodes, _degree2 = audit.derive_nodes_and_graph(rows)
        self.assertEqual(graph.number_of_nodes(), 4)


class DegreeTwoAngleTests(unittest.TestCase):
    def test_straight_continuation(self):
        rows = [
            edge_row(1, [(0, 0), (1, 0)]),
            edge_row(2, [(1, 0), (2, 0)]),
        ]
        _graph, _nodes, degree2 = audit.derive_nodes_and_graph(rows)
        self.assertEqual(len(degree2), 1)
        self.assertAlmostEqual(degree2[0]["angle_between_outgoing_directions_degrees"], 180.0)
        self.assertEqual(degree2[0]["classification"], "approximately_straight_continuation")

    def test_right_angle_bend(self):
        rows = [
            edge_row(1, [(0, 0), (1, 0)]),
            edge_row(2, [(1, 0), (1, 1)]),
        ]
        _graph, _nodes, degree2 = audit.derive_nodes_and_graph(rows)
        self.assertEqual(len(degree2), 1)
        self.assertAlmostEqual(degree2[0]["angle_between_outgoing_directions_degrees"], 90.0)
        self.assertEqual(degree2[0]["classification"], "angular_bend")


class GeometryClassificationTests(unittest.TestCase):
    def test_horizontal_and_vertical_orientation(self):
        horizontal = audit.analyze_edge(1, LineString([(0, 2), (3, 2)]))
        vertical = audit.analyze_edge(2, LineString([(4, 1), (4, 5)]))
        self.assertTrue(horizontal["is_exactly_horizontal"])
        self.assertFalse(horizontal["is_exactly_vertical"])
        self.assertEqual(horizontal["endpoint_orientation_mod_180_degrees"], 0.0)
        self.assertTrue(vertical["is_exactly_vertical"])
        self.assertFalse(vertical["is_exactly_horizontal"])
        self.assertEqual(vertical["endpoint_orientation_mod_180_degrees"], 90.0)

    def test_multivertex_collinearity(self):
        collinear = audit.analyze_edge(
            1, LineString([(0, 0), (0, 0), (1, 1), (2, 2), (2, 2)])
        )
        bent = audit.analyze_edge(2, LineString([(0, 0), (1, 1.1), (2, 2)]))
        self.assertTrue(collinear["all_vertices_collinear_within_numerical_precision"])
        self.assertEqual(collinear["consecutive_duplicate_vertex_count"], 2)
        self.assertFalse(bent["all_vertices_collinear_within_numerical_precision"])
        self.assertGreater(bent["maximum_intermediate_deviation_local_units"], 0)


class FullAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PROJECT_ROOT / "data" / "baselines" / "grid" / "network_v2.gpkg"

    def test_source_immutability(self):
        before = audit.sha256_file(self.source)
        result = audit.audit_source(self.source)
        after = audit.sha256_file(self.source)
        self.assertEqual(before, after)
        self.assertEqual(result.report["topology"]["edge_count"], 76)

    def test_deterministic_output_bytes(self):
        source_hash = audit.sha256_file(self.source)
        results = [audit.audit_source(self.source), audit.audit_source(self.source)]
        serialized = []
        for result in results:
            result.report["source_integrity"] = {
                "sha256_before": source_hash,
                "sha256_after": source_hash,
                "unchanged": True,
            }
            audit.finalize_result(result)
            serialized.append(
                {
                    "json": json.dumps(result.report, sort_keys=True, allow_nan=False),
                    "edges": json.dumps(audit._csv_ready(result.edge_records), sort_keys=True),
                    "nodes": json.dumps(audit._csv_ready(result.node_records), sort_keys=True),
                    "degree2": json.dumps(audit._csv_ready(result.degree2_records), sort_keys=True),
                    "multivertex": json.dumps(audit._csv_ready(result.multivertex_records), sort_keys=True),
                    "non_axis": json.dumps(audit._csv_ready(result.non_axis_records), sort_keys=True),
                }
            )
        self.assertEqual(serialized[0], serialized[1])
        self.assertEqual(source_hash, audit.sha256_file(self.source))


if __name__ == "__main__":
    unittest.main()
