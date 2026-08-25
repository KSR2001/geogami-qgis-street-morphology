import math
from pathlib import Path
import shutil
import sys
import unittest

import geopandas as gpd
from shapely.geometry import LineString, Point


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import build_env38_working as builder
import sync_env38_edge_endpoints as sync
import validate_env38_topology as validator


class Phase6BEndpointSynchronizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.canonical = validator.DEFAULT_CANONICAL
        cls.canonical_hash = validator.sha256_file(cls.canonical)
        cls.root = PROJECT_ROOT / "results" / ".phase6b_test_work"
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.base = cls.root / "base.gpkg"
        builder.create_template(cls.canonical, cls.base)

    @classmethod
    def tearDownClass(cls):
        for path in cls.root.glob("*"):
            if path.is_file():
                path.unlink()
        cls.root.rmdir()

    def _fixture(self, name):
        path = self.root / f"{name}.gpkg"
        shutil.copy2(self.base, path)
        return path

    def _frames(self, path):
        return gpd.read_file(path, layer="nodes"), gpd.read_file(path, layer="edges")

    def _write_layer(self, path, frame, layer):
        frame.to_file(path, layer=layer, driver="GPKG", mode="w", index=False)

    def _tiny_mismatch(self, path, curved=False):
        nodes, edges = self._frames(path)
        index = edges.index[edges.edge_id == "E001"][0]
        coordinates = list(edges.at[index, "geometry"].coords)
        if curved:
            start, end = coordinates[0], coordinates[-1]
            coordinates = [start, ((start[0] + end[0]) / 2 + 0.01, (start[1] + end[1]) / 2 + 0.02), end]
        coordinates[0] = (math.nextafter(coordinates[0][0], math.inf), coordinates[0][1])
        edges.at[index, "geometry"] = LineString(coordinates)
        self._write_layer(path, edges, "edges")

    def _mutate_edge(self, path, edge_id, transform):
        _nodes, edges = self._frames(path)
        index = edges.index[edges.edge_id == edge_id][0]
        coordinates = list(edges.at[index, "geometry"].coords)
        edges.at[index, "geometry"] = LineString(transform(coordinates))
        self._write_layer(path, edges, "edges")

    def _orientation(self, plan, edge_id):
        return next(item for item in plan.orientations if item.edge_id == edge_id)

    def test_01_tiny_endpoint_mismatch_is_corrected_exactly(self):
        path = self._fixture("tiny")
        self._tiny_mismatch(path)
        plan = sync.build_sync_plan(path, self.canonical)
        self.assertEqual(plan.endpoint_count, 1)
        self.assertEqual(plan.changes[0].edge_id, "E001")
        self.assertAlmostEqual(plan.changes[0].distance_changed, 2.8e-14, delta=1e-14)
        result = sync.apply_sync_plan(plan)
        self.assertTrue(result["applied"])
        nodes, edges = self._frames(path)
        edge = edges.loc[edges.edge_id == "E001"].iloc[0]
        node = nodes.loc[nodes.node_id == edge.u].iloc[0]
        self.assertEqual(tuple(edge.geometry.coords[0]), tuple(node.geometry.coords[0]))

    def test_02_large_mismatch_after_manual_node_move_is_corrected(self):
        path = self._fixture("moved_node")
        nodes, edges = self._frames(path)
        edge = edges.loc[edges.edge_id == "E001"].iloc[0]
        node_index = nodes.index[nodes.node_id == edge.u][0]
        point = nodes.at[node_index, "geometry"]
        nodes.at[node_index, "geometry"] = Point(point.x + 1.0, point.y + 1.0)
        self._write_layer(path, nodes, "nodes")
        plan = sync.build_sync_plan(path, self.canonical)
        self.assertGreater(plan.changes[0].distance_changed, 1.0)
        sync.apply_sync_plan(plan)
        nodes_after, edges_after = self._frames(path)
        moved = nodes_after.loc[nodes_after.node_id == edge.u].iloc[0].geometry
        changed = edges_after.loc[edges_after.edge_id == "E001"].iloc[0].geometry
        self.assertEqual(tuple(changed.coords[0]), tuple(moved.coords[0]))

    def test_03_internal_curve_vertices_remain_exactly_unchanged(self):
        path = self._fixture("internal_vertices")
        self._tiny_mismatch(path, curved=True)
        _nodes, before = self._frames(path)
        internal_before = tuple(before.loc[before.edge_id == "E001"].iloc[0].geometry.coords[1:-1])
        sync.apply_sync_plan(sync.build_sync_plan(path, self.canonical))
        _nodes, after = self._frames(path)
        internal_after = tuple(after.loc[after.edge_id == "E001"].iloc[0].geometry.coords[1:-1])
        self.assertEqual(internal_after, internal_before)

    def test_04_node_geometries_remain_exactly_unchanged(self):
        path = self._fixture("nodes_unchanged")
        self._tiny_mismatch(path)
        nodes_before, _edges = self._frames(path)
        wkb_before = nodes_before.geometry.to_wkb().tolist()
        sync.apply_sync_plan(sync.build_sync_plan(path, self.canonical))
        nodes_after, _edges = self._frames(path)
        self.assertEqual(nodes_after.geometry.to_wkb().tolist(), wkb_before)

    def test_05_scientific_identity_and_connectivity_fields_remain_unchanged(self):
        path = self._fixture("fields_unchanged")
        self._tiny_mismatch(path)
        nodes_before, edges_before = self._frames(path)
        sync.apply_sync_plan(sync.build_sync_plan(path, self.canonical))
        nodes_after, edges_after = self._frames(path)
        self.assertEqual(nodes_after.node_id.tolist(), nodes_before.node_id.tolist())
        for field in ("edge_id", "u", "v", "key"):
            self.assertEqual(edges_after[field].tolist(), edges_before[field].tolist())

    def test_06_topology_signature_remains_unchanged(self):
        path = self._fixture("signature")
        self._tiny_mismatch(path)
        result = sync.apply_sync_plan(sync.build_sync_plan(path, self.canonical))
        self.assertEqual(result["topology_signature_sha256"], validator.EXPECTED_TOPOLOGY_SIGNATURE)

    def test_07_forward_exact_edge_is_classified_and_unchanged(self):
        path = self._fixture("forward_exact")
        _nodes, edges_before = self._frames(path)
        before = tuple(edges_before.loc[edges_before.edge_id == "E001"].iloc[0].geometry.coords)
        plan = sync.build_sync_plan(path, self.canonical)
        record = self._orientation(plan, "E001")
        self.assertEqual(record.classification, "forward/unambiguous")
        self.assertFalse(record.endpoint_synchronization_required)
        self.assertNotIn("E001", plan.replacement_geometries)
        _nodes, edges_after = self._frames(path)
        self.assertEqual(tuple(edges_after.loc[edges_after.edge_id == "E001"].iloc[0].geometry.coords), before)

    def test_08_reversed_exact_edge_is_classified_and_unchanged(self):
        path = self._fixture("reversed")
        self._mutate_edge(path, "E001", lambda coordinates: reversed(coordinates))
        before_hash = validator.sha256_file(path)
        _nodes, edges_before = self._frames(path)
        before = tuple(edges_before.loc[edges_before.edge_id == "E001"].iloc[0].geometry.coords)
        plan = sync.build_sync_plan(path, self.canonical)
        record = self._orientation(plan, "E001")
        self.assertEqual(record.classification, "reversed/unambiguous")
        self.assertEqual(record.reverse_assignment_cost, 0.0)
        self.assertFalse(record.endpoint_synchronization_required)
        self.assertEqual(plan.endpoint_count, 0)
        self.assertNotIn("E001", plan.replacement_geometries)
        self.assertEqual(validator.sha256_file(path), before_hash)
        _nodes, edges_after = self._frames(path)
        self.assertEqual(tuple(edges_after.loc[edges_after.edge_id == "E001"].iloc[0].geometry.coords), before)

    def test_09_canonical_env39_remains_unchanged(self):
        path = self._fixture("canonical_unchanged")
        self._tiny_mismatch(path)
        sync.apply_sync_plan(sync.build_sync_plan(path, self.canonical))
        self.assertEqual(validator.sha256_file(self.canonical), self.canonical_hash)

    def test_10_dry_run_makes_no_file_changes(self):
        path = self._fixture("dry_run")
        self._tiny_mismatch(path)
        before = validator.sha256_file(path)
        plan = sync.build_sync_plan(path, self.canonical)
        self.assertEqual(plan.endpoint_count, 1)
        self.assertEqual(validator.sha256_file(path), before)

    def test_11_applied_result_passes_phase6a_validator(self):
        path = self._fixture("phase6a")
        self._tiny_mismatch(path, curved=True)
        sync.apply_sync_plan(sync.build_sync_plan(path, self.canonical))
        report = validator.validate_candidate(path, self.canonical).report
        self.assertEqual(report["topology_control_status"], "PASS")
        self.assertEqual(report["geometric_realization_qa_status"], "PASS")
        self.assertEqual(report["final_result"], "PASS")

    def test_12_forward_and_reversed_endpoint_mismatches_use_assigned_nodes(self):
        cases = (
            ("forward_first", False, 0, "u", "first"),
            ("forward_last", False, -1, "v", "last"),
            ("reversed_first", True, 0, "v", "first"),
            ("reversed_last", True, -1, "u", "last"),
        )
        for name, reverse, position, node_field, endpoint_name in cases:
            with self.subTest(name=name):
                path = self._fixture(name)

                def mutate(coordinates):
                    if reverse:
                        coordinates.reverse()
                    x, y = coordinates[position]
                    coordinates[position] = (math.nextafter(x, math.inf), y)
                    return coordinates

                self._mutate_edge(path, "E001", mutate)
                plan = sync.build_sync_plan(path, self.canonical)
                self.assertEqual(plan.endpoint_count, 1)
                self.assertEqual(plan.changes[0].endpoint, endpoint_name)
                _nodes, edges = self._frames(path)
                edge = edges.loc[edges.edge_id == "E001"].iloc[0]
                self.assertEqual(plan.changes[0].node_id, str(getattr(edge, node_field)))
                before_internal = tuple(edge.geometry.coords[1:-1])
                sync.apply_sync_plan(plan)
                nodes_after, edges_after = self._frames(path)
                changed = edges_after.loc[edges_after.edge_id == "E001"].iloc[0]
                target = nodes_after.loc[nodes_after.node_id == getattr(changed, node_field)].iloc[0].geometry
                actual = changed.geometry.coords[position]
                self.assertEqual(tuple(actual), tuple(target.coords[0]))
                self.assertEqual(tuple(changed.geometry.coords[1:-1]), before_internal)

    def test_13_ambiguous_endpoint_assignment_fails_safely(self):
        path = self._fixture("ambiguous")
        nodes, edges = self._frames(path)
        edge = edges.loc[edges.edge_id == "E001"].iloc[0]
        u = nodes.loc[nodes.node_id == edge.u].iloc[0].geometry
        v = nodes.loc[nodes.node_id == edge.v].iloc[0].geometry
        midpoint = ((u.x + v.x) / 2.0, (u.y + v.y) / 2.0)
        dx, dy = v.x - u.x, v.y - u.y
        scale = math.hypot(dx, dy)
        perpendicular = (-dy / scale, dx / scale)
        coordinates = [
            (midpoint[0] + perpendicular[0], midpoint[1] + perpendicular[1]),
            (midpoint[0] - perpendicular[0], midpoint[1] - perpendicular[1]),
        ]
        index = edges.index[edges.edge_id == "E001"][0]
        edges.at[index, "geometry"] = LineString(coordinates)
        self._write_layer(path, edges, "edges")
        with self.assertRaisesRegex(sync.AmbiguousOrientationError, "ambiguous endpoint assignment"):
            sync.build_sync_plan(path, self.canonical)

    def test_14_current_professor_env38_dry_run_has_expected_changes_and_e042(self):
        path = validator.DEFAULT_INPUT
        before = validator.sha256_file(path)
        canonical_before = validator.sha256_file(self.canonical)
        plan = sync.build_sync_plan(path, self.canonical)
        self.assertEqual(plan.edges_total, 69)
        self.assertEqual(plan.affected_edge_ids, ["E019", "E020", "E021"])
        self.assertEqual(plan.affected_node_ids, ["N013"])
        self.assertEqual(plan.endpoint_count, 3)
        self.assertEqual(
            {change.required_coordinate for change in plan.changes},
            {(98.48348990297171, 864.9274336319993)},
        )
        e042 = self._orientation(plan, "E042")
        self.assertEqual(e042.classification, "reversed/unambiguous")
        self.assertEqual(e042.reverse_assignment_cost, 0.0)
        self.assertFalse(e042.endpoint_synchronization_required)
        self.assertNotIn("E042", plan.replacement_geometries)
        self.assertEqual(validator.sha256_file(path), before)
        self.assertEqual(validator.sha256_file(self.canonical), canonical_before)


if __name__ == "__main__":
    unittest.main()
