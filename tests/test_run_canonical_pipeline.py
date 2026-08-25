from __future__ import annotations

from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import uuid

import geopandas as gpd
from shapely.affinity import translate
from shapely.geometry import LineString, Point


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from geogami_morphology.canonical import PipelineError, run_preserve_topology
from geogami_morphology.io import sha256_file
import geogami_morphology.canonical as canonical_module


class Phase7BCanonicalPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = ROOT / "data" / "canonical" / "grid" / "env39_canonical.gpkg"
        cls.reference_hash = sha256_file(cls.reference)
        cls.nodes = gpd.read_file(cls.reference, layer="nodes")
        cls.edges = gpd.read_file(cls.reference, layer="edges")
        cls.scratch = ROOT / "tests" / ".tmp"
        cls.scratch.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.root = self.scratch / f"phase7b-{uuid.uuid4().hex}"
        self.root.mkdir()

    def tearDown(self):
        shutil.rmtree(self.root)

    def _input(self, name, mutate=None):
        path = self.root / f"{name}.gpkg"
        if mutate is None:
            shutil.copy2(self.reference, path)
            return path
        nodes, edges = self.nodes.copy(), self.edges.copy()
        mutate(nodes, edges)
        nodes.to_file(path, layer="nodes", driver="GPKG", index=False)
        edges.to_file(path, layer="edges", driver="GPKG", mode="a", index=False)
        return path

    def _run(self, name, mutate=None, expect_pass=True):
        source = self._input(name, mutate)
        source_hash = sha256_file(source)
        output = self.root / name / "canonical.gpkg"
        if expect_pass:
            result = run_preserve_topology("env39", source, self.reference, output)
            self.assertEqual(result.report["final_result"], "PASS")
            self.assertTrue(output.is_file())
        else:
            with self.assertRaises(PipelineError) as caught:
                run_preserve_topology("env39", source, self.reference, output)
            result = caught.exception
            self.assertFalse(output.exists())
        self.assertEqual(sha256_file(source), source_hash)
        self.assertEqual(sha256_file(self.reference), self.reference_hash)
        return result, source, output

    def test_01_unchanged_env39_passes_and_reports_frozen_topology(self):
        result, _, _ = self._run("unchanged")
        self.assertEqual(result.report["topology"]["topology_signature"], "4AF93910532EB430103267DAECF73B3ADC3B3D1599471EDF6B1632B6C5F898CB")
        self.assertEqual(result.report["topology"]["node_count"], 46)
        self.assertEqual(result.report["topology"]["edge_count"], 69)

    def test_02_geometry_only_translation_passes(self):
        def mutate(nodes, edges):
            nodes.geometry = nodes.geometry.map(lambda value: translate(value, xoff=3.25, yoff=-1.5))
            edges.geometry = edges.geometry.map(lambda value: translate(value, xoff=3.25, yoff=-1.5))
        self._run("translated", mutate)

    def test_03_curved_internal_vertices_pass(self):
        def mutate(_nodes, edges):
            geometry = edges.geometry.iloc[0]
            start, end = geometry.coords[0], geometry.coords[-1]
            middle = ((start[0] + end[0]) / 2 + 0.01, (start[1] + end[1]) / 2 + 0.01)
            edges.at[edges.index[0], "geometry"] = LineString([start, middle, end])
        result, _, output = self._run("curved", mutate)
        published = gpd.read_file(output, layer="edges")
        self.assertEqual(int(published.iloc[0].vertex_count), 3)
        self.assertEqual(float(published.iloc[0].length_local), published.geometry.iloc[0].length)

    def test_04_node_deletion_and_addition_fail(self):
        self._run("node_deleted", lambda nodes, _edges: nodes.drop(nodes.index[0], inplace=True), False)
        def add(nodes, _edges):
            record = nodes.iloc[0].copy(); record["node_id"] = "N999"; record["geometry"] = Point(-1, -1)
            nodes.loc[len(nodes)] = record
        self._run("node_added", add, False)

    def test_05_edge_deletion_and_addition_fail(self):
        self._run("edge_deleted", lambda _nodes, edges: edges.drop(edges.index[0], inplace=True), False)
        def add(_nodes, edges):
            record = edges.iloc[0].copy(); record["edge_id"] = "E999"
            edges.loc[len(edges)] = record
        self._run("edge_added", add, False)

    def test_06_changed_connectivity_key_edge_id_and_node_id_fail(self):
        mutations = {
            "connectivity": lambda nodes, edges: edges.__setitem__("u", edges.u.mask(edges.index == edges.index[0], "N046")),
            "key": lambda nodes, edges: edges.__setitem__("key", edges.key.mask(edges.index == edges.index[0], 9)),
            "edge_id": lambda nodes, edges: edges.__setitem__("edge_id", edges.edge_id.mask(edges.index == edges.index[0], "E999")),
            "node_id": lambda nodes, edges: nodes.__setitem__("node_id", nodes.node_id.mask(nodes.index == nodes.index[0], "N999")),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                self._run(name, mutate, False)

    def test_07_stale_derived_attributes_are_recomputed(self):
        def mutate(nodes, edges):
            nodes["x"], nodes["y"], nodes["degree"] = -999.0, -999.0, -999
            edges["length_local"], edges["vertex_count"] = -999.0, -999
        _, _, output = self._run("stale", mutate)
        nodes, edges = gpd.read_file(output, layer="nodes"), gpd.read_file(output, layer="edges")
        self.assertTrue(all(row.x == row.geometry.x and row.y == row.geometry.y for row in nodes.itertuples()))
        self.assertTrue(all(row.length_local == row.geometry.length and row.vertex_count == len(row.geometry.coords) for row in edges.itertuples()))

    def test_08_tiny_endpoint_mismatch_corrected_only_in_candidate(self):
        def mutate(_nodes, edges):
            coordinates = list(edges.geometry.iloc[0].coords)
            coordinates[0] = (coordinates[0][0] + 1e-8, coordinates[0][1])
            edges.at[edges.index[0], "geometry"] = LineString(coordinates)
        result, source, output = self._run("endpoint_noise", mutate)
        self.assertEqual(len(result.report["endpoint_corrections"]), 1)
        source_edge = gpd.read_file(source, layer="edges").geometry.iloc[0]
        output_edge = gpd.read_file(output, layer="edges").geometry.iloc[0]
        self.assertNotEqual(source_edge.coords[0], output_edge.coords[0])

    def test_09_unintended_noncanonical_crossing_fails(self):
        def mutate(_nodes, edges):
            for first in range(len(edges)):
                row_1 = edges.iloc[first]
                for second in range(len(edges)):
                    row_2 = edges.iloc[second]
                    if first == second or {row_1.u, row_1.v} & {row_2.u, row_2.v}:
                        continue
                    point = row_2.geometry.interpolate(0.5, normalized=True)
                    candidate = LineString([row_1.geometry.coords[0], (point.x, point.y), row_1.geometry.coords[-1]])
                    if candidate.is_simple and candidate.length > 0:
                        edges.at[edges.index[first], "geometry"] = candidate
                        return
            self.fail("Could not construct crossing geometry")
        error, _, _ = self._run("crossing", mutate, False)
        self.assertIn("unintended_edge_intersection", {item["issue_type"] for item in error.report["diagnostics"]})

    def test_10_failed_run_does_not_publish_and_inputs_remain_immutable(self):
        def mutate(_nodes, edges):
            coordinates = list(edges.geometry.iloc[0].coords); coordinates[0] = (coordinates[0][0] + 0.1, coordinates[0][1]); edges.at[edges.index[0], "geometry"] = LineString(coordinates)
        error, _, output = self._run("large_mismatch", mutate, False)
        self.assertFalse(output.exists())
        self.assertIn("endpoint_mismatch", {item["issue_type"] for item in error.report["diagnostics"]})

    def test_11_repeated_runs_have_equivalent_scientific_content(self):
        first, _, _ = self._run("deterministic_a")
        second, _, _ = self._run("deterministic_b")
        self.assertEqual(first.report["candidate"]["scientific_content_sha256"], second.report["candidate"]["scientific_content_sha256"])

    def test_12_frozen_phase5_file_remains_byte_identical(self):
        self.assertEqual(sha256_file(self.reference), self.reference_hash)

    def test_13_reversed_edge_orientation_fails(self):
        def mutate(_nodes, edges):
            geometry = edges.geometry.iloc[0]
            edges.at[edges.index[0], "geometry"] = LineString(reversed(geometry.coords))
        error, _, _ = self._run("reversed", mutate, False)
        self.assertIn("reversed_edge_orientation", {item["issue_type"] for item in error.report["diagnostics"]})

    def test_14_candidate_is_serialized_reread_then_published_through_fresh_copy(self):
        events = []
        real_read = canonical_module.read_network
        real_publish = canonical_module.publish_validated_geopackage

        def tracked_read(path):
            if ".candidate-" in Path(path).name:
                events.append("candidate_scientifically_reread")
            return real_read(path)

        def tracked_publish(*args, **kwargs):
            events.append("publication_started")
            return real_publish(*args, **kwargs)

        with patch.object(canonical_module, "read_network", side_effect=tracked_read), patch.object(
            canonical_module, "publish_validated_geopackage", side_effect=tracked_publish
        ):
            result, _source, output = self._run("publication_sequence")
        self.assertLess(events.index("candidate_scientifically_reread"), events.index("publication_started"))
        publication = result.report["publication"]
        self.assertTrue(publication["fresh_publication_copy"])
        self.assertEqual(publication["sqlite_integrity_check"], "ok")
        self.assertEqual(publication["validated_candidate_sha256"], publication["published_sha256"])
        self.assertEqual(publication["published_sha256"], sha256_file(output))


if __name__ == "__main__":
    unittest.main()
