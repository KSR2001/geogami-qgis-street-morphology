from __future__ import annotations

from collections import Counter
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import uuid

import geopandas as gpd
import networkx as nx
from pyproj import CRS
from shapely.geometry import LineString, Point


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from geogami_morphology.graph import (
    AnalysisGraphError,
    CanonicalSelection,
    build_analysis_graphs,
    build_networkx_multigraph,
    build_osmnx_multidigraph,
    directed_arc_count,
    load_canonical_geopackage,
    physical_street_count,
    resolve_canonical_run,
    validate_canonical_for_analysis,
    validate_graph_correspondence,
)
from geogami_morphology.identity import network_identity
from geogami_morphology.io import sha256_file, write_json
import build_analysis_graphs as cli


class Phase7DAnalysisGraphAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.latest_path = ROOT / "data" / "canonical" / "grid" / "latest.json"
        cls.frozen_canonical = ROOT / "data" / "canonical" / "grid" / "env39_canonical.gpkg"
        cls.frozen_source = ROOT / "data" / "baselines" / "grid" / "network_v2.gpkg"
        cls.latest_bytes = cls.latest_path.read_bytes()
        cls.frozen_canonical_hash = sha256_file(cls.frozen_canonical)
        cls.frozen_source_hash = sha256_file(cls.frozen_source)
        cls.selection = resolve_canonical_run("env39", project_root=ROOT)
        nodes, edges = load_canonical_geopackage(cls.selection.canonical_path)
        cls.canonical = validate_canonical_for_analysis(nodes, edges)
        cls.scientific_graph = build_networkx_multigraph(cls.canonical, cls.selection)
        cls.osmnx_graph = build_osmnx_multidigraph(cls.canonical, cls.selection)
        cls.scratch = ROOT / "tests" / ".tmp"
        cls.scratch.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.root = self.scratch / f"phase7d-{uuid.uuid4().hex}"
        self.root.mkdir()

    def tearDown(self):
        shutil.rmtree(self.root)

    def _metadata_copy(self, field: str, value: str) -> Path:
        latest = json.loads(self.latest_path.read_text(encoding="utf-8"))
        manifest_source = ROOT / latest["manifest_path"]
        manifest = json.loads(manifest_source.read_text(encoding="utf-8"))
        manifest["published_canonical"][field] = value
        manifest_path = self.root / "canonical_manifest.json"
        manifest["artifacts"]["canonical_manifest"] = str(manifest_path)
        write_json(manifest_path, manifest)
        latest["manifest_path"] = str(manifest_path)
        latest_path = self.root / "latest.json"
        write_json(latest_path, latest)
        return latest_path

    def test_01_latest_run_resolves_and_recalculates_all_three_identities(self):
        selection = resolve_canonical_run("env39", project_root=ROOT)
        latest = json.loads(self.latest_path.read_text(encoding="utf-8"))
        self.assertEqual(selection.run_id, latest["run_id"])
        self.assertEqual(selection.file_sha256, sha256_file(selection.canonical_path))
        self.assertEqual(selection.scientific_content_signature, latest["scientific_content_signature"])
        self.assertEqual(selection.topology_signature, latest["topology_signature"])

    def test_02_explicit_accepted_canonical_path_works_without_node_renumbering(self):
        selection = resolve_canonical_run(
            "env39", canonical_path=self.frozen_canonical, project_root=ROOT
        )
        self.assertTrue(selection.run_id.startswith("explicit_env39_"))
        nodes, edges = load_canonical_geopackage(selection.canonical_path)
        canonical = validate_canonical_for_analysis(nodes, edges)
        graph = build_networkx_multigraph(canonical, selection)
        self.assertEqual(set(graph), set(nodes["node_id"].astype(str)))

    def test_03_manifest_file_sha_mismatch_fails_before_analysis(self):
        latest = self._metadata_copy("file_sha256", "0" * 64)
        with self.assertRaisesRegex(AnalysisGraphError, "file SHA-256"):
            resolve_canonical_run("env39", latest_path=latest, project_root=ROOT)

    def test_04_manifest_scientific_content_signature_mismatch_fails(self):
        latest = self._metadata_copy("scientific_content_signature", "1" * 64)
        with self.assertRaisesRegex(AnalysisGraphError, "scientific-content signature"):
            resolve_canonical_run("env39", latest_path=latest, project_root=ROOT)

    def test_05_manifest_topology_signature_mismatch_fails(self):
        latest = self._metadata_copy("topology_signature", "2" * 64)
        with self.assertRaisesRegex(AnalysisGraphError, "topology signature"):
            resolve_canonical_run("env39", latest_path=latest, project_root=ROOT)

    def test_06_canonical_env39_counts_ids_and_topology_are_preserved(self):
        identity = self.canonical.identity
        self.assertEqual((identity["node_count"], identity["physical_edge_count"]), (46, 69))
        self.assertEqual(identity["component_count"], 1)
        self.assertEqual(
            identity["topology_signature"],
            "4AF93910532EB430103267DAECF73B3ADC3B3D1599471EDF6B1632B6C5F898CB",
        )
        self.assertEqual(set(self.scientific_graph), set(self.canonical.nodes.node_id))
        self.assertEqual(
            {data["edge_id"] for *_, data in self.scientific_graph.edges(data=True)},
            set(self.canonical.edges.edge_id),
        )

    def test_07_networkx_graph_is_physical_multigraph_with_handshake_regression(self):
        graph = self.scientific_graph
        self.assertIsInstance(graph, nx.MultiGraph)
        self.assertFalse(graph.is_directed())
        self.assertEqual((graph.number_of_nodes(), graph.number_of_edges()), (46, 69))
        self.assertEqual(nx.number_connected_components(graph), 1)
        self.assertEqual(dict(Counter(dict(graph.degree()).values())), {1: 10, 3: 16, 4: 20})
        self.assertEqual(sum(dict(graph.degree()).values()), 138)
        self.assertEqual(2 * graph.number_of_edges(), 138)

    def test_08_osmnx_graph_has_exactly_two_arcs_per_69_physical_ids(self):
        graph = self.osmnx_graph
        self.assertIsInstance(graph, nx.MultiDiGraph)
        self.assertEqual((graph.number_of_nodes(), directed_arc_count(graph)), (46, 138))
        self.assertEqual(physical_street_count(graph), 69)
        counts = Counter(data["canonical_edge_id"] for *_, data in graph.edges(data=True))
        self.assertEqual(set(counts), set(self.canonical.edges.edge_id))
        self.assertEqual(set(counts.values()), {2})

    def test_09_reciprocal_endpoints_and_geometry_orientation_are_exact(self):
        validate_graph_correspondence(
            self.canonical, self.scientific_graph, self.osmnx_graph, self.selection
        )
        arcs = list(
            (u, v, data)
            for u, v, data in self.osmnx_graph.edges(data=True)
            if data["canonical_edge_id"] == "E001"
        )
        forward = next(item for item in arcs if item[2]["arc_direction"] == "forward")
        reverse = next(item for item in arcs if item[2]["arc_direction"] == "reverse")
        self.assertEqual((forward[0], forward[1]), (reverse[1], reverse[0]))
        self.assertEqual(
            tuple(reverse[2]["geometry"].coords),
            tuple(reversed(tuple(forward[2]["geometry"].coords))),
        )

    def test_10_geometry_length_is_authoritative_and_stale_derived_values_fail(self):
        for _, _, data in self.scientific_graph.edges(data=True):
            self.assertEqual(data["length"], data["geometry"].length)
            self.assertEqual(data["length_local"], data["geometry"].length)
        for _, _, data in self.osmnx_graph.edges(data=True):
            self.assertEqual(data["length"], data["geometry"].length)
        bad_edges = self.canonical.edges.copy()
        bad_edges.at[bad_edges.index[0], "length_local"] += 0.1
        with self.assertRaisesRegex(AnalysisGraphError, "stored length_local"):
            validate_canonical_for_analysis(self.canonical.nodes, bad_edges)

    def test_11_local_crs_is_preserved_and_geographic_helpers_are_never_called(self):
        crs = CRS.from_user_input(self.osmnx_graph.graph["crs"])
        self.assertFalse(crs.is_geographic)
        self.assertIsNone(crs.to_epsg())
        with patch("osmnx.distance.add_edge_lengths") as add_lengths, patch(
            "osmnx.bearing.add_edge_bearings"
        ) as add_bearings:
            graph = build_osmnx_multidigraph(self.canonical, self.selection)
        add_lengths.assert_not_called()
        add_bearings.assert_not_called()
        self.assertEqual(graph.graph["coordinate_system"], "GeoGami Local Cartesian")
        self.assertEqual(graph.graph["length_units"], "local_units")

    def test_12_graphml_publication_and_roundtrip_preserve_invariants_and_latest(self):
        result = build_analysis_graphs(
            "env39", output_root=self.root / "analysis", project_root=ROOT
        )
        self.assertTrue(result.graphml_path.is_file())
        self.assertEqual(result.graphml_sha256, sha256_file(result.graphml_path))
        self.assertEqual((result.roundtrip_graph.number_of_nodes(), result.roundtrip_graph.number_of_edges()), (46, 138))
        self.assertEqual(physical_street_count(result.roundtrip_graph), 69)
        self.assertEqual(result.manifest["roundtrip_validation"]["status"], "PASS")
        self.assertEqual(result.manifest["software"]["OSMnx"], importlib.metadata.version("osmnx"))
        self.assertEqual(self.latest_path.read_bytes(), self.latest_bytes)

    def test_13_synthetic_parallel_keys_do_not_depend_on_all_keys_being_zero(self):
        crs = self.canonical.nodes.crs
        nodes = gpd.GeoDataFrame(
            {"node_id": ["A", "B"]},
            geometry=[Point(0, 0), Point(10, 0)],
            crs=crs,
        )
        edges = gpd.GeoDataFrame(
            {
                "edge_id": ["P0", "P1"],
                "u": ["A", "A"],
                "v": ["B", "B"],
                "key": [0, 1],
            },
            geometry=[LineString([(0, 0), (10, 0)]), LineString([(0, 0), (5, 1), (10, 0)])],
            crs=crs,
        )
        canonical = validate_canonical_for_analysis(nodes, edges)
        identity = network_identity(canonical.nodes, canonical.edges)
        selection = CanonicalSelection(
            "synthetic",
            "synthetic_parallel",
            self.root / "synthetic.gpkg",
            None,
            "A" * 64,
            identity["scientific_content_signature"],
            identity["topology_signature"],
            None,
        )
        scientific = build_networkx_multigraph(canonical, selection)
        directed = build_osmnx_multidigraph(canonical, selection)
        self.assertEqual(set(key for *_, key in scientific.edges(keys=True)), {0, 1})
        self.assertEqual((scientific.number_of_edges(), directed.number_of_edges()), (2, 4))
        validate_graph_correspondence(canonical, scientific, directed, selection)

    def test_14_correspondence_rejects_copied_instead_of_reversed_geometry(self):
        graph = self.osmnx_graph.copy()
        edge_id = "E001"
        forward = next(
            (u, v, key, data)
            for u, v, key, data in graph.edges(keys=True, data=True)
            if data["canonical_edge_id"] == edge_id and data["arc_direction"] == "forward"
        )
        reverse = next(
            (u, v, key, data)
            for u, v, key, data in graph.edges(keys=True, data=True)
            if data["canonical_edge_id"] == edge_id and data["arc_direction"] == "reverse"
        )
        graph[reverse[0]][reverse[1]][reverse[2]]["geometry"] = forward[3]["geometry"]
        with self.assertRaisesRegex(AnalysisGraphError, "exact coordinate reversal"):
            validate_graph_correspondence(
                self.canonical, self.scientific_graph, graph, self.selection
            )

    def test_15_professor_cli_supports_explicit_canonical_mode(self):
        exit_code = cli.main(
            [
                "--environment", "env39",
                "--canonical", str(self.frozen_canonical),
                "--output-root", str(self.root / "cli-analysis"),
            ]
        )
        self.assertEqual(exit_code, 0)
        manifests = list((self.root / "cli-analysis").rglob("analysis_graph_manifest.json"))
        self.assertEqual(len(manifests), 1)

    def test_16_frozen_files_environment_contract_and_latest_remain_unchanged(self):
        self.assertEqual(sha256_file(self.frozen_source), self.frozen_source_hash)
        self.assertEqual(sha256_file(self.frozen_canonical), self.frozen_canonical_hash)
        self.assertEqual(self.latest_path.read_bytes(), self.latest_bytes)
        environment = (ROOT / "environment.yml").read_text(encoding="utf-8").splitlines()
        self.assertIn("osmnx=2.1.1", environment)
        self.assertIn("pyproj=3.7.2", environment)
        self.assertNotIn("jupyterlab", environment)
        self.assertNotIn("ipykernel", environment)

    def test_17_editable_and_phase5_source_paths_are_explicitly_rejected(self):
        editable = ROOT / "data" / "editable" / "grid" / "env39_editable.gpkg"
        for prohibited in (editable, self.frozen_source):
            with self.subTest(path=prohibited):
                with self.assertRaisesRegex(AnalysisGraphError, "not editable or baseline"):
                    resolve_canonical_run(
                        "env39", canonical_path=prohibited, project_root=ROOT
                    )


if __name__ == "__main__":
    unittest.main()
