from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

import networkx as nx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from geogami_morphology.graph import (
    build_networkx_multigraph,
    load_analysis_graphml,
    load_canonical_geopackage,
    physical_street_count,
    resolve_canonical_run,
    validate_canonical_for_analysis,
    validate_graphml_roundtrip,
)
from geogami_morphology.identity import network_identity
from geogami_morphology.integrated import (
    COMPARISON_FIELDS,
    METRIC_DEFINITIONS,
    assemble_comparison_rows,
)
from geogami_morphology.io import read_network, sha256_file


OFFICIAL_RUN_ID = "env38_20260825T150753632988Z_974f687c1ac4"
RUN_ROOT = ROOT / "results" / "analysis" / "env38" / OFFICIAL_RUN_ID
FREEZE_PATH = RUN_ROOT / "env38_scientific_baseline_freeze.json"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def metric_index(document: dict) -> dict[str, object]:
    return {row["metric_name"]: row["value"] for row in document["metrics"]}


class Phase9DEnv38ScientificBaselineFreezeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.freeze = read_json(FREEZE_PATH)
        cls.canonical_latest = read_json(ROOT / "data/canonical/curvilinear/latest.json")
        cls.analysis_latest = read_json(ROOT / "results/analysis/env38/latest.json")
        cls.topology = read_json(RUN_ROOT / "topology/topology_metrics_summary.json")
        cls.geometry = read_json(RUN_ROOT / "geometry/geometry_metrics_summary.json")
        cls.orientation = read_json(RUN_ROOT / "geometry/orientation_metrics_summary.json")

    def test_01_both_latest_pointers_resolve_the_frozen_official_run(self):
        self.assertEqual(self.canonical_latest["run_id"], OFFICIAL_RUN_ID)
        self.assertEqual(self.analysis_latest["canonical_run_id"], OFFICIAL_RUN_ID)
        self.assertEqual(self.canonical_latest["canonical_path"], self.analysis_latest["canonical_path"])
        self.assertEqual(self.freeze["official_run_id"], OFFICIAL_RUN_ID)
        self.assertEqual(self.freeze["environment"], "env38")
        self.assertEqual(self.freeze["status"], "PASS")

    def test_02_canonical_graph_and_editable_identities_match_the_freeze(self):
        selection = resolve_canonical_run(
            "env38",
            latest_path=ROOT / "data/canonical/curvilinear/latest.json",
            project_root=ROOT,
        )
        nodes, edges = load_canonical_geopackage(selection.canonical_path)
        canonical = validate_canonical_for_analysis(nodes, edges)
        editable_nodes, editable_edges = read_network(
            ROOT / "data/editable/curvilinear/env38_editable.gpkg"
        )
        frozen = self.freeze["canonical_identity"]
        self.assertEqual(canonical.identity["node_count"], frozen["node_count"])
        self.assertEqual(canonical.identity["physical_edge_count"], frozen["physical_edge_count"])
        self.assertEqual(canonical.identity["component_count"], frozen["component_count"])
        self.assertEqual(canonical.identity["degree_distribution"], frozen["degree_distribution"])
        self.assertEqual(
            canonical.identity["scientific_content_signature"],
            network_identity(editable_nodes, editable_edges)["scientific_content_signature"],
        )
        self.assertEqual(sha256_file(selection.canonical_path), frozen["container_sha256"])

        physical = build_networkx_multigraph(canonical, selection)
        directed = load_analysis_graphml(RUN_ROOT / "graphs/env38_osmnx.graphml")
        roundtrip = validate_graphml_roundtrip(canonical, physical, directed, selection)
        self.assertIs(type(physical), nx.MultiGraph)
        self.assertIs(type(directed), nx.MultiDiGraph)
        self.assertEqual(
            (physical.number_of_nodes(), physical.number_of_edges(), sum(dict(physical.degree()).values())),
            (46, 69, 138),
        )
        self.assertEqual(
            (directed.number_of_nodes(), directed.number_of_edges(), physical_street_count(directed)),
            (46, 138, 69),
        )
        self.assertEqual(roundtrip["topology_signature"], frozen["topology_signature"])

    def test_03_accepted_metrics_are_loaded_from_the_stored_phase7_outputs(self):
        topology = metric_index(self.topology)
        geometry = metric_index(self.geometry)
        orientation = metric_index(self.orientation)
        topology_names = {
            "mean_degree": "degree_mean",
            "dead_end_count": "dead_end_count",
            "degree_3_count": "degree_3_count",
            "degree_4_count": "degree_4_count",
            "intersection_count": "osmnx_intersection_count",
            "cycle_rank": "cycle_rank",
            "bridge_count": "bridge_count",
            "articulation_point_count": "articulation_point_count",
            "node_connectivity": "node_connectivity",
            "edge_connectivity": "edge_connectivity",
            "mean_shortest_path_hops": "average_shortest_path_hops",
            "diameter_hops": "diameter_hops",
            "radius_hops": "radius_hops",
            "mean_weighted_shortest_path_local": "average_shortest_path_length_local",
        }
        geometry_names = {
            "total_network_length_local": "total_network_length_local",
            "mean_edge_length_local": "mean_edge_length_local",
            "median_edge_length_local": "median_edge_length_local",
            "total_chord_length_local": "total_chord_length_local",
            "mean_edge_circuity": "mean_edge_circuity",
            "network_circuity": "network_circuity",
            "positive_geometry_segment_count": "geometry_segment_count",
            "zero_chord_edge_count": "zero_chord_edge_count",
        }
        orientation_names = {
            "bin_count": "orientation_bin_count",
            "bin_width_degrees": "orientation_bin_width_degrees",
            "chord_observation_count": "chord_orientation_observation_count",
            "segment_observation_count": "segment_orientation_observation_count",
            "chord_entropy": "chord_orientation_entropy",
            "chord_normalized_entropy": "chord_orientation_normalized_entropy",
            "segment_length_weighted_entropy": "segment_length_weighted_orientation_entropy",
            "segment_length_weighted_normalized_entropy": "segment_length_weighted_normalized_entropy",
            "phi_chord": "phi_chord",
            "phi_segment_length_weighted": "phi_segment_length_weighted",
        }
        frozen = self.freeze["accepted_metrics"]
        for target, source in topology_names.items():
            self.assertEqual(frozen["topology_and_weighted_network"][target], topology[source])
        for target, source in geometry_names.items():
            self.assertEqual(frozen["geometry"][target], geometry[source])
        for target, source in orientation_names.items():
            self.assertEqual(frozen["orientation"][target], orientation[source])
        with (RUN_ROOT / "geometry/edge_geometry_metrics.csv").open(
            encoding="utf-8", newline=""
        ) as stream:
            excess = math.fsum(
                float(row["excess_length_local"]) for row in csv.DictReader(stream)
            )
        self.assertAlmostEqual(frozen["geometry"]["absolute_excess_length_local"], excess)

    def test_04_integrated_rows_are_traceable_and_schema_compatible_with_env39(self):
        rebuilt = assemble_comparison_rows(
            environment="env38",
            canonical_run_id=OFFICIAL_RUN_ID,
            topology_summary=self.topology,
            geometry_summary=self.geometry,
            orientation_summary=self.orientation,
        )
        with (RUN_ROOT / "integrated/comparison_ready_metrics.csv").open(
            encoding="utf-8", newline=""
        ) as stream:
            stored = list(csv.DictReader(stream))
        env39_latest = read_json(ROOT / "results/analysis/env39/latest.json")
        with (ROOT / env39_latest["comparison_ready_metrics_path"]).open(
            encoding="utf-8", newline=""
        ) as stream:
            env39 = list(csv.DictReader(stream))
        self.assertEqual(len(stored), 39)
        self.assertEqual(sum(row["metric_tier"] == "core" for row in stored), 19)
        self.assertEqual([str(row["value"]) for row in rebuilt], [row["value"] for row in stored])
        self.assertEqual(list(stored[0]), COMPARISON_FIELDS)
        self.assertEqual([row["metric_id"] for row in stored], [row["metric_id"] for row in env39])
        self.assertEqual(
            [row["metric_family"] for row in stored],
            [row["metric_family"] for row in env39],
        )
        self.assertTrue(all(not hasattr(definition, "value") for definition in METRIC_DEFINITIONS))

    def test_05_freeze_references_and_all_svg_figures_are_hash_valid(self):
        for section in (
            "source_manifests",
            "source_artifacts",
            "methodology_artifacts",
            "pointers",
        ):
            for record in self.freeze[section].values():
                path = ROOT / record["path"]
                self.assertTrue(path.is_file())
                self.assertEqual(sha256_file(path), record["file_sha256"])
        for key in ("registry", "metrics_configuration"):
            record = self.freeze["provenance"][key]
            self.assertEqual(sha256_file(ROOT / record["path"]), record["file_sha256"])

        geometry_manifest = read_json(RUN_ROOT / "geometry/geometry_analysis_manifest.json")
        integrated_manifest = read_json(RUN_ROOT / "integrated/integrated_analysis_manifest.json")
        referenced = {
            ROOT / record["path"]
            for manifest in (geometry_manifest, integrated_manifest)
            for record in manifest["artifacts"].values()
            if record["path"].endswith(".svg")
        }
        figures = set(RUN_ROOT.rglob("*.svg"))
        self.assertEqual(len(figures), 7)
        self.assertEqual(figures, referenced)
        for figure in figures:
            self.assertTrue(ET.fromstring(figure.read_bytes()).tag.endswith("svg"))

    def test_06_env39_and_env38_inputs_remain_frozen(self):
        expected = {
            "data/baselines/grid/network_v2.gpkg": "7698544A01EED008E5451F8368703AFD0DBE232D10C9D7ADAA58C13DA57E0289",
            "data/canonical/grid/env39_canonical.gpkg": "212A2AA583BF77304859DB76639B8AD4B6DC458237EEA24FEB60FC228E5D6847",
            "data/working/curvilinear/env38_working.gpkg": "B2F608ED60EA4E5B7F6C2A3564102E6ACDF576F36870D6EA666E75600746F429",
            "data/editable/curvilinear/env38_editable.gpkg": "B2F608ED60EA4E5B7F6C2A3564102E6ACDF576F36870D6EA666E75600746F429",
        }
        for path, digest in expected.items():
            self.assertEqual(sha256_file(ROOT / path), digest)


if __name__ == "__main__":
    unittest.main()
