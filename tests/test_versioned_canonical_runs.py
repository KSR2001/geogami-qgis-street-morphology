from __future__ import annotations

import csv
import importlib.metadata
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

import geopandas as gpd
from shapely.geometry import LineString
from shapely.wkt import loads as load_wkt


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from geogami_morphology.canonical import PipelineError
from geogami_morphology.identity import network_identity, scientific_content_signature
from geogami_morphology.io import sha256_file
from geogami_morphology.versioned import (
    MANIFEST_SCHEMA_VERSION,
    git_provenance,
    run_versioned_canonical,
    software_environment,
)
import run_canonical_pipeline as cli


class Phase7CVersionedCanonicalRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = ROOT / "data" / "canonical" / "grid" / "env39_canonical.gpkg"
        cls.editable = ROOT / "data" / "editable" / "grid" / "env39_editable.gpkg"
        cls.baseline_source = ROOT / "data" / "baselines" / "grid" / "network_v2.gpkg"
        cls.reference_hash = sha256_file(cls.reference)
        cls.baseline_source_hash = sha256_file(cls.baseline_source)
        cls.nodes = gpd.read_file(cls.reference, layer="nodes")
        cls.edges = gpd.read_file(cls.reference, layer="edges")
        cls.scratch = ROOT / "tests" / ".tmp"
        cls.scratch.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.root = self.scratch / f"phase7c-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.runs_root = self.root / "runs"
        self.latest = self.root / "latest.json"

    def tearDown(self):
        shutil.rmtree(self.root)

    def _input(self, name: str, mutate=None) -> Path:
        path = self.root / f"{name}.gpkg"
        if mutate is None:
            shutil.copy2(self.reference, path)
            return path
        nodes, edges = self.nodes.copy(), self.edges.copy()
        mutate(nodes, edges)
        nodes.to_file(path, layer="nodes", driver="GPKG", index=False)
        edges.to_file(path, layer="edges", driver="GPKG", mode="a", index=False)
        return path

    def _run(self, source: Path | None = None):
        return run_versioned_canonical(
            "env39",
            source or self.editable,
            self.reference,
            self.runs_root,
            latest_path=self.latest,
            project_root=ROOT,
        )

    def test_01_success_creates_safe_versioned_directory_and_required_artifacts(self):
        result = self._run()
        self.assertRegex(result.run_id, r"^env39_\d{8}T\d{12}Z_[0-9a-f]{12}$")
        self.assertEqual(result.run_dir.name, result.run_id)
        required = {
            "env39_canonical.gpkg",
            "canonical_manifest.json",
            "canonical_validation.json",
            "canonical_nodes.csv",
            "canonical_edges.csv",
            "topology_signature.txt",
            "scientific_content_signature.txt",
            "endpoint_adjustments.csv",
            "diagnostics.csv",
        }
        self.assertEqual({path.name for path in result.run_dir.iterdir()}, required)
        for name in required:
            self.assertTrue((result.run_dir / name).is_file())
        for name in ("endpoint_adjustments.csv", "diagnostics.csv"):
            with (result.run_dir / name).open(encoding="utf-8", newline="") as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(len(rows), 1, f"{name} must be a zero-row table with a header")

    def test_02_manifest_schema_sections_and_independent_file_sha_values(self):
        result = self._run()
        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], MANIFEST_SCHEMA_VERSION)
        required_sections = {
            "identity",
            "time",
            "input_editable_network",
            "reference_canonical",
            "published_canonical",
            "git_provenance",
            "software_environment",
            "network_identity",
            "validation",
            "configuration",
            "signature_methodology",
            "artifacts",
        }
        self.assertTrue(required_sections <= set(manifest))
        self.assertEqual(manifest["input_editable_network"]["file_sha256"], sha256_file(self.editable))
        self.assertEqual(manifest["reference_canonical"]["file_sha256"], sha256_file(self.reference))
        self.assertEqual(manifest["published_canonical"]["file_sha256"], sha256_file(result.canonical_path))
        self.assertTrue(manifest["artifacts"]["topology_signature"].endswith("/topology_signature.txt"))
        self.assertTrue(
            manifest["artifacts"]["scientific_content_signature"].endswith(
                "/scientific_content_signature.txt"
            )
        )
        validation = json.loads(result.validation_path.read_text(encoding="utf-8"))
        self.assertEqual(validation["final_result"], "PASS")
        self.assertTrue(all(validation["signature_checks"].values()))
        self.assertTrue(all(validation["publication_checks"].values()))

    def test_03_three_identity_types_are_distinct_and_signature_files_match(self):
        result = self._run()
        published = result.manifest["published_canonical"]
        identities = {
            published["file_sha256"],
            published["scientific_content_signature"],
            published["topology_signature"],
        }
        self.assertEqual(len(identities), 3)
        self.assertEqual(
            (result.run_dir / "scientific_content_signature.txt").read_text(encoding="utf-8").strip(),
            published["scientific_content_signature"],
        )
        self.assertEqual(
            (result.run_dir / "topology_signature.txt").read_text(encoding="utf-8").strip(),
            published["topology_signature"],
        )
        self.assertEqual(published["topology_signature"], "4AF93910532EB430103267DAECF73B3ADC3B3D1599471EDF6B1632B6C5F898CB")

    def test_04_scientific_signature_is_order_independent_and_excludes_derived_provenance(self):
        first = scientific_content_signature(self.nodes, self.edges)[0]
        shuffled_nodes = self.nodes.sample(frac=1, random_state=7).reset_index(drop=True)
        shuffled_edges = self.edges.sample(frac=1, random_state=9).reset_index(drop=True)
        shuffled_nodes["degree"] = -100
        shuffled_nodes["x"] = -100.0
        shuffled_edges["length_local"] = -100.0
        shuffled_edges["source_fids"] = "changed provenance only"
        second = scientific_content_signature(shuffled_nodes, shuffled_edges)[0]
        self.assertEqual(first, second)

    def test_05_equivalent_builds_and_container_noise_keep_scientific_and_topology_identity(self):
        first = self._run()
        second = self._run()
        noisy = self._input("container_noise")
        connection = sqlite3.connect(noisy)
        try:
            connection.execute("UPDATE gpkg_contents SET last_change='2099-01-01T00:00:00.000Z'")
            connection.commit()
        finally:
            connection.close()
        self.assertNotEqual(sha256_file(noisy), sha256_file(self.reference))
        noisy_nodes = gpd.read_file(noisy, layer="nodes")
        noisy_edges = gpd.read_file(noisy, layer="edges")
        self.assertEqual(
            scientific_content_signature(noisy_nodes, noisy_edges)[0],
            scientific_content_signature(self.nodes, self.edges)[0],
        )
        third = self._run(noisy)
        signatures = {
            item.manifest["published_canonical"]["scientific_content_signature"]
            for item in (first, second, third)
        }
        topologies = {
            item.manifest["published_canonical"]["topology_signature"]
            for item in (first, second, third)
        }
        self.assertEqual(len(signatures), 1)
        self.assertEqual(len(topologies), 1)
        self.assertEqual(len({first.run_id, second.run_id, third.run_id}), 3)

    def test_06_geometry_change_changes_scientific_but_not_topology_signature(self):
        baseline = self._run()
        def curve(_nodes, edges):
            geometry = edges.geometry.iloc[0]
            start, end = geometry.coords[0], geometry.coords[-1]
            middle = ((start[0] + end[0]) / 2 + 0.01, (start[1] + end[1]) / 2 + 0.01)
            edges.at[edges.index[0], "geometry"] = LineString([start, middle, end])
        curved = self._run(self._input("curved", curve))
        self.assertNotEqual(
            baseline.manifest["published_canonical"]["scientific_content_signature"],
            curved.manifest["published_canonical"]["scientific_content_signature"],
        )
        self.assertEqual(
            baseline.manifest["published_canonical"]["topology_signature"],
            curved.manifest["published_canonical"]["topology_signature"],
        )

    def test_07_connectivity_change_alters_topology_and_cannot_publish_or_move_latest(self):
        success = self._run()
        latest_before = self.latest.read_bytes()
        success_dirs_before = {path.name for path in self.runs_root.iterdir() if path.is_dir()}
        def reconnect(_nodes, edges):
            edges.at[edges.index[0], "u"] = "N046"
        changed = self._input("changed_connectivity", reconnect)
        changed_identity = network_identity(
            gpd.read_file(changed, layer="nodes"), gpd.read_file(changed, layer="edges")
        )
        self.assertNotEqual(
            changed_identity["topology_signature"],
            success.manifest["published_canonical"]["topology_signature"],
        )
        with self.assertRaises(PipelineError):
            self._run(changed)
        self.assertEqual(self.latest.read_bytes(), latest_before)
        success_dirs_after = {path.name for path in self.runs_root.iterdir() if path.is_dir() and path.name != "failed"}
        self.assertEqual(success_dirs_after, success_dirs_before)
        failed_runs = list((self.runs_root / "failed").iterdir())
        self.assertEqual(len(failed_runs), 1)
        self.assertTrue((failed_runs[0] / "failure.json").is_file())
        self.assertFalse(list(failed_runs[0].glob("*.gpkg")))

    def test_08_software_versions_and_git_revision_dirty_state_are_actual(self):
        expected_git = git_provenance(ROOT)
        expected_software = software_environment()
        result = self._run()
        recorded_git = result.manifest["git_provenance"]
        recorded_software = result.manifest["software_environment"]
        self.assertEqual(recorded_git["commit_sha"], subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip())
        self.assertEqual(recorded_git["dirty"], expected_git["dirty"])
        self.assertEqual(recorded_git["changed_paths"], expected_git["changed_paths"])
        self.assertEqual(recorded_software, expected_software)
        self.assertEqual(recorded_software["python"], platform_python_version())
        self.assertEqual(recorded_software["packages"]["networkx"], importlib.metadata.version("networkx"))

    def test_09_latest_updates_only_after_success_and_points_to_verified_files(self):
        first = self._run()
        first_latest = json.loads(self.latest.read_text(encoding="utf-8"))
        self.assertEqual(first_latest["run_id"], first.run_id)
        second = self._run()
        latest = json.loads(self.latest.read_text(encoding="utf-8"))
        self.assertEqual(latest["run_id"], second.run_id)
        canonical = ROOT / latest["canonical_path"]
        manifest = ROOT / latest["manifest_path"]
        self.assertTrue(canonical.is_file())
        self.assertTrue(manifest.is_file())
        self.assertEqual(latest["canonical_file_sha256"], sha256_file(canonical))
        self.assertNotEqual(first_latest["run_id"], latest["run_id"])

    def test_10_canonical_csvs_match_authoritative_geopackage(self):
        result = self._run()
        nodes = gpd.read_file(result.canonical_path, layer="nodes").set_index("node_id")
        edges = gpd.read_file(result.canonical_path, layer="edges").set_index("edge_id")
        with (result.run_dir / "canonical_nodes.csv").open(encoding="utf-8", newline="") as stream:
            node_rows = list(csv.DictReader(stream))
        with (result.run_dir / "canonical_edges.csv").open(encoding="utf-8", newline="") as stream:
            edge_rows = list(csv.DictReader(stream))
        self.assertEqual(len(node_rows), len(nodes))
        self.assertEqual(len(edge_rows), len(edges))
        for row in node_rows:
            source = nodes.loc[row["node_id"]]
            self.assertEqual(float(row["x"]), source.geometry.x)
            self.assertEqual(float(row["y"]), source.geometry.y)
            self.assertTrue(load_wkt(row["geometry_wkt"]).equals_exact(source.geometry, 0.0))
        for row in edge_rows:
            source = edges.loc[row["edge_id"]]
            self.assertEqual((row["u"], row["v"], int(row["key"])), (source.u, source.v, int(source.key)))
            self.assertTrue(load_wkt(row["geometry_wkt"]).equals_exact(source.geometry, 0.0))

    def test_11_configuration_and_paths_are_repo_relative_and_cross_platform_safe(self):
        result = self._run()
        manifest = result.manifest
        config = manifest["configuration"]["configuration_files"][0]
        self.assertEqual(config["path"], "environment.yml")
        self.assertEqual(config["file_sha256"], sha256_file(ROOT / "environment.yml"))
        for value in (
            manifest["input_editable_network"]["path"],
            manifest["reference_canonical"]["path"],
            manifest["published_canonical"]["path"],
            json.loads(self.latest.read_text(encoding="utf-8"))["manifest_path"],
        ):
            self.assertNotIn("\\", value)
        self.assertEqual(manifest["network_identity"]["coordinate_system"], "GeoGami Local Cartesian")
        self.assertEqual(manifest["network_identity"]["coordinate_units"], "local units")

    def test_12_backward_compatible_phase7b_cli_and_frozen_files_remain_valid(self):
        output = self.root / "legacy" / "env39_canonical.gpkg"
        exit_code = cli.main(
            [
                "--environment", "env39",
                "--input", str(self.editable),
                "--reference-canonical", str(self.reference),
                "--output", str(output),
                "--mode", "preserve-topology",
            ]
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue(output.is_file())
        self.assertEqual(sha256_file(self.baseline_source), self.baseline_source_hash)
        self.assertEqual(sha256_file(self.reference), self.reference_hash)

    def test_13_latest_write_failure_rolls_back_new_success_looking_directory(self):
        first = self._run()
        latest_before = self.latest.read_bytes()
        with patch(
            "geogami_morphology.versioned._atomic_latest",
            side_effect=OSError("simulated latest-pointer failure"),
        ):
            with self.assertRaises(PipelineError):
                self._run()
        self.assertEqual(self.latest.read_bytes(), latest_before)
        successful_runs = [
            path for path in self.runs_root.iterdir()
            if path.is_dir() and path.name not in {"failed", ".scratch"}
        ]
        self.assertEqual(successful_runs, [first.run_dir])
        failed_runs = list((self.runs_root / "failed").iterdir())
        self.assertEqual(len(failed_runs), 1)
        self.assertTrue((failed_runs[0] / "failure.json").is_file())

    def test_14_geopackage_publication_failure_preserves_latest_output_and_inputs(self):
        first = self._run()
        latest_before = self.latest.read_bytes()
        canonical_before = sha256_file(first.canonical_path)
        editable_before = sha256_file(self.editable)
        reference_before = sha256_file(self.reference)
        successful_before = {
            path.name for path in self.runs_root.iterdir()
            if path.is_dir() and path.name not in {"failed", ".scratch"}
        }
        error = PermissionError("simulated permanent candidate publication lock")
        error.winerror = 32
        with patch(
            "geogami_morphology.canonical.publish_validated_geopackage",
            side_effect=error,
        ):
            with self.assertRaisesRegex(PipelineError, "permanent candidate publication lock"):
                self._run()
        self.assertEqual(self.latest.read_bytes(), latest_before)
        self.assertEqual(sha256_file(first.canonical_path), canonical_before)
        self.assertEqual(sha256_file(self.editable), editable_before)
        self.assertEqual(sha256_file(self.reference), reference_before)
        successful_after = {
            path.name for path in self.runs_root.iterdir()
            if path.is_dir() and path.name not in {"failed", ".scratch"}
        }
        self.assertEqual(successful_after, successful_before)


def platform_python_version() -> str:
    import platform

    return platform.python_version()


if __name__ == "__main__":
    unittest.main()
