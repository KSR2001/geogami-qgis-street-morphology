import json
from pathlib import Path
import shutil
import sys
import unittest

import geopandas as gpd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from geogami_morphology.environments import (
    EnvironmentRegistryError,
    PATH_FIELDS,
    get_environment,
    load_registry,
)
from geogami_morphology.identity import network_identity
from geogami_morphology.io import read_network, sha256_file
import bootstrap_editable_network as bootstrapper
import validate_env38_topology as env38_validator


ENV38_SHA256 = "B2F608ED60EA4E5B7F6C2A3564102E6ACDF576F36870D6EA666E75600746F429"
ENV39_EDITABLE_SHA256 = "212A2AA583BF77304859DB76639B8AD4B6DC458237EEA24FEB60FC228E5D6847"
ENV39_CANONICAL_SHA256 = "212A2AA583BF77304859DB76639B8AD4B6DC458237EEA24FEB60FC228E5D6847"


class Phase9AEnvironmentRegistryTests(unittest.TestCase):
    def test_01_env39_resolves_to_current_paths(self):
        environment = get_environment("env39")
        self.assertEqual(environment.editable_source, PROJECT_ROOT / "data/editable/grid/env39_editable.gpkg")
        self.assertEqual(environment.canonical_root, PROJECT_ROOT / "data/canonical/grid")
        self.assertEqual(environment.canonical_runs, PROJECT_ROOT / "data/canonical/grid/runs")
        self.assertEqual(environment.canonical_latest, PROJECT_ROOT / "data/canonical/grid/latest.json")
        self.assertEqual(environment.analysis_root, PROJECT_ROOT / "results/analysis/env39")
        self.assertEqual(environment.topology_reference, PROJECT_ROOT / "data/canonical/grid/env39_canonical.gpkg")

    def test_02_env38_resolves_to_curvilinear_paths_and_env39_contract(self):
        environment = get_environment("env38")
        self.assertEqual(environment.editable_source, PROJECT_ROOT / "data/editable/curvilinear/env38_editable.gpkg")
        self.assertEqual(environment.canonical_root, PROJECT_ROOT / "data/canonical/curvilinear")
        self.assertEqual(environment.canonical_runs, PROJECT_ROOT / "data/canonical/curvilinear/runs")
        self.assertEqual(environment.canonical_latest, PROJECT_ROOT / "data/canonical/curvilinear/latest.json")
        self.assertEqual(environment.analysis_root, PROJECT_ROOT / "results/analysis/env38")
        self.assertEqual(environment.topology_reference, PROJECT_ROOT / "data/canonical/grid/env39_canonical.gpkg")

    def test_03_unknown_environment_fails_clearly(self):
        with self.assertRaisesRegex(EnvironmentRegistryError, "Unsupported environment.*env38, env39"):
            get_environment("env40")
        with self.assertRaisesRegex(EnvironmentRegistryError, "Unsupported environment.*env38, env39"):
            get_environment("ENV38")

    def test_04_all_registry_paths_are_portable_and_repository_relative(self):
        for environment in load_registry().values():
            relative = environment.relative_paths()
            self.assertEqual(set(relative), set(PATH_FIELDS))
            for value in relative.values():
                self.assertNotIn("\\", value)
                self.assertFalse(Path(value).is_absolute())
                self.assertNotIn("..", Path(value).parts)


class Phase9AEnv38BootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PROJECT_ROOT / "data/working/curvilinear/env38_working.gpkg"
        cls.production = get_environment("env38").editable_source
        cls.manifest = cls.production.with_name("env38_editable_bootstrap.json")
        cls.work = PROJECT_ROOT / "results/.phase9a_test_work"
        cls.work.mkdir(parents=True, exist_ok=True)
        cls.output = cls.work / "env38_editable.gpkg"
        cls.output_manifest = cls.work / "env38_editable_bootstrap.json"
        cls.source_hash_before = sha256_file(cls.source)
        cls.env39_editable = get_environment("env39").editable_source
        cls.env39_canonical = get_environment("env39").topology_reference
        cls.env39_editable_hash_before = sha256_file(cls.env39_editable)
        cls.env39_canonical_hash_before = sha256_file(cls.env39_canonical)
        cls.result = bootstrapper.bootstrap(
            cls.source, cls.output, cls.output_manifest, ENV38_SHA256
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work)

    def test_05_bootstrap_succeeds_and_manifest_is_complete(self):
        self.assertTrue(self.output.is_file())
        manifest = json.loads(self.output_manifest.read_text(encoding="utf-8"))
        required = {
            "source_path", "source_sha256", "editable_path", "editable_sha256",
            "creation_timestamp_utc", "node_count", "edge_count", "component_count",
            "degree_distribution", "topology_signature", "crs", "scientific_identity",
            "bootstrap_method",
        }
        self.assertTrue(required.issubset(manifest))

    def test_06_source_remains_byte_identical(self):
        self.assertEqual(self.source_hash_before, ENV38_SHA256)
        self.assertEqual(sha256_file(self.source), ENV38_SHA256)

    def test_07_node_and_edge_counts(self):
        nodes, edges = read_network(self.output)
        self.assertEqual(len(nodes), 46)
        self.assertEqual(len(edges), 69)

    def test_08_topology_signature_equals_frozen_contract(self):
        identity = network_identity(*read_network(self.output))
        self.assertEqual(identity["topology_signature"], env38_validator.EXPECTED_TOPOLOGY_SIGNATURE)
        self.assertEqual(identity["component_count"], 1)
        self.assertEqual(identity["degree_distribution"], {"1": 10, "3": 16, "4": 20})

    def test_09_geometry_ids_connectivity_and_crs_equal_source_exactly(self):
        source_nodes, source_edges = read_network(self.source)
        output_nodes, output_edges = read_network(self.output)
        self.assertEqual(source_nodes.node_id.tolist(), output_nodes.node_id.tolist())
        for field in ("edge_id", "u", "v", "key"):
            self.assertEqual(source_edges[field].tolist(), output_edges[field].tolist())
        self.assertEqual(source_nodes.geometry.to_wkb().tolist(), output_nodes.geometry.to_wkb().tolist())
        self.assertEqual(source_edges.geometry.to_wkb().tolist(), output_edges.geometry.to_wkb().tolist())
        self.assertEqual(source_nodes.crs, output_nodes.crs)
        self.assertEqual(source_edges.crs, output_edges.crs)
        self.assertEqual(sha256_file(self.output), ENV38_SHA256)

    def test_10_production_bootstrap_has_expected_identity(self):
        self.assertTrue(self.production.is_file())
        self.assertTrue(self.manifest.is_file())
        self.assertEqual(sha256_file(self.production), ENV38_SHA256)
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["topology_signature"], env38_validator.EXPECTED_TOPOLOGY_SIGNATURE)
        self.assertEqual(manifest["source_sha256"], ENV38_SHA256)
        self.assertEqual(manifest["editable_sha256"], ENV38_SHA256)

    def test_11_env39_protected_files_unchanged(self):
        self.assertEqual(self.env39_editable_hash_before, ENV39_EDITABLE_SHA256)
        self.assertEqual(sha256_file(self.env39_editable), ENV39_EDITABLE_SHA256)
        self.assertEqual(self.env39_canonical_hash_before, ENV39_CANONICAL_SHA256)
        self.assertEqual(sha256_file(self.env39_canonical), ENV39_CANONICAL_SHA256)

    def test_12_phase8_validator_still_passes_both_domains(self):
        report = env38_validator.validate_candidate(self.source, self.env39_canonical).report
        self.assertEqual(report["topology_control_status"], "PASS")
        self.assertEqual(report["geometric_realization_qa_status"], "PASS")


if __name__ == "__main__":
    unittest.main()
