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

    def test_07_reversed_edge_orientation_is_rejected(self):
        path = self._fixture("reversed")
        _nodes, edges = self._frames(path)
        index = edges.index[edges.edge_id == "E001"][0]
        edges.at[index, "geometry"] = LineString(reversed(edges.at[index, "geometry"].coords))
        self._write_layer(path, edges, "edges")
        with self.assertRaises(sync.ReversedOrientationError):
            sync.build_sync_plan(path, self.canonical)

    def test_08_canonical_env39_remains_unchanged(self):
        path = self._fixture("canonical_unchanged")
        self._tiny_mismatch(path)
        sync.apply_sync_plan(sync.build_sync_plan(path, self.canonical))
        self.assertEqual(validator.sha256_file(self.canonical), self.canonical_hash)

    def test_09_dry_run_makes_no_file_changes(self):
        path = self._fixture("dry_run")
        self._tiny_mismatch(path)
        before = validator.sha256_file(path)
        plan = sync.build_sync_plan(path, self.canonical)
        self.assertEqual(plan.endpoint_count, 1)
        self.assertEqual(validator.sha256_file(path), before)

    def test_10_applied_result_passes_phase6a_validator(self):
        path = self._fixture("phase6a")
        self._tiny_mismatch(path, curved=True)
        sync.apply_sync_plan(sync.build_sync_plan(path, self.canonical))
        report = validator.validate_candidate(path, self.canonical).report
        self.assertEqual(report["topology_control_status"], "PASS")
        self.assertEqual(report["geometric_realization_qa_status"], "PASS")
        self.assertEqual(report["final_result"], "PASS")


if __name__ == "__main__":
    unittest.main()
