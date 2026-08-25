from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from geogami_morphology.identity import network_identity
from geogami_morphology.io import (
    GeoPackagePublicationError,
    _run_file_operation,
    geopackage_sidecars,
    publish_validated_geopackage,
    read_network,
    sha256_file,
)


def windows_lock_error(code: int = 32) -> PermissionError:
    error = PermissionError(f"simulated WinError {code}")
    error.winerror = code
    return error


class Phase8CGeoPackagePublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = ROOT / "data" / "canonical" / "grid" / "env39_canonical.gpkg"
        cls.reference_hash = sha256_file(cls.reference)
        cls.reference_nodes, cls.reference_edges = read_network(cls.reference)
        cls.reference_identity = network_identity(cls.reference_nodes, cls.reference_edges)
        cls.scratch = ROOT / "tests" / ".tmp"
        cls.scratch.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.root = self.scratch / f"phase8c-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.candidate = self.root / ".canonical.candidate.gpkg"
        self.output = self.root / "canonical.gpkg"
        shutil.copy2(self.reference, self.candidate)
        self.reopened: list[Path] = []

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _verify(self, path: Path) -> None:
        self.reopened.append(Path(path))
        nodes, edges = read_network(path)
        self.assertEqual((len(nodes), len(edges)), (46, 69))

    def test_01_validated_candidate_uses_fresh_stage_and_reopens_after_publication(self):
        real_replace = os.replace
        observed_sources: list[Path] = []

        def observed_replace(source, destination):
            source = Path(source)
            observed_sources.append(source)
            self.assertIn(".publication-", source.name)
            self.assertNotEqual(source, self.candidate)
            self.assertFalse(self.candidate.exists())
            return real_replace(source, destination)

        with patch("geogami_morphology.io.os.replace", side_effect=observed_replace):
            result = publish_validated_geopackage(
                self.candidate, self.output, verify_published=self._verify
            )
        self.assertEqual(len(observed_sources), 1)
        self.assertEqual(self.reopened, [self.output])
        self.assertTrue(result.fresh_publication_copy)
        self.assertEqual(result.sqlite_integrity_check, "ok")
        self.assertEqual(result.validated_candidate_sha256, self.reference_hash)
        self.assertEqual(result.published_sha256, self.reference_hash)

    def test_02_published_content_layers_crs_attributes_and_identities_are_preserved(self):
        publish_validated_geopackage(self.candidate, self.output, verify_published=self._verify)
        nodes, edges = read_network(self.output)
        identity = network_identity(nodes, edges)
        self.assertEqual(set(nodes.columns), set(self.reference_nodes.columns))
        self.assertEqual(set(edges.columns), set(self.reference_edges.columns))
        self.assertEqual(nodes.crs, self.reference_nodes.crs)
        self.assertEqual(edges.crs, self.reference_edges.crs)
        self.assertEqual(identity["node_count"], 46)
        self.assertEqual(identity["physical_edge_count"], 69)
        self.assertEqual(identity["component_count"], 1)
        self.assertEqual(identity["degree_distribution"], {"1": 10, "3": 16, "4": 20})
        self.assertEqual(
            identity["scientific_content_signature"],
            "2046798C1E9CB35310ABB4EA7134EE212423265572C278075A94ED0E9080D447",
        )
        self.assertEqual(
            identity["topology_signature"],
            "4AF93910532EB430103267DAECF73B3ADC3B3D1599471EDF6B1632B6C5F898CB",
        )

    def test_03_first_windows_sharing_violation_retries_then_succeeds(self):
        real_replace = os.replace
        calls = 0

        def transient_replace(source, destination):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise windows_lock_error(32)
            return real_replace(source, destination)

        with patch("geogami_morphology.io.os.replace", side_effect=transient_replace), patch(
            "geogami_morphology.io.time.sleep"
        ):
            result = publish_validated_geopackage(
                self.candidate, self.output, verify_published=self._verify
            )
        self.assertEqual(calls, 2)
        self.assertEqual(result.replace_attempts, 2)
        self.assertEqual(result.transient_sharing_violations, 1)
        self.assertTrue(self.output.is_file())

    def test_04_permanent_windows_sharing_violation_fails_bounded_and_preserves_output(self):
        shutil.copy2(self.reference, self.output)
        output_before = sha256_file(self.output)
        calls = 0

        def locked_replace(_source, _destination):
            nonlocal calls
            calls += 1
            raise windows_lock_error(32)

        with patch("geogami_morphology.io.os.replace", side_effect=locked_replace), patch(
            "geogami_morphology.io.time.sleep"
        ):
            with self.assertRaisesRegex(GeoPackagePublicationError, "failed after 5 attempts"):
                publish_validated_geopackage(
                    self.candidate, self.output, verify_published=self._verify
                )
        self.assertEqual(calls, 5)
        self.assertEqual(sha256_file(self.output), output_before)
        self.assertEqual(self.reopened, [])

    def test_05_unrelated_permission_error_is_not_retried_or_swallowed(self):
        calls = 0

        def denied_replace(_source, _destination):
            nonlocal calls
            calls += 1
            error = PermissionError("simulated access denied")
            error.winerror = 5
            raise error

        with patch("geogami_morphology.io.os.replace", side_effect=denied_replace), patch(
            "geogami_morphology.io.time.sleep"
        ) as sleeper:
            with self.assertRaisesRegex(PermissionError, "access denied"):
                publish_validated_geopackage(
                    self.candidate, self.output, verify_published=self._verify
                )
        self.assertEqual(calls, 1)
        sleeper.assert_not_called()

    def test_06_post_publication_verification_failure_rolls_back_existing_output(self):
        shutil.copy2(self.reference, self.output)
        original_hash = sha256_file(self.output)

        def reject(_path: Path) -> None:
            raise ValueError("simulated reopen failure")

        with self.assertRaisesRegex(ValueError, "simulated reopen failure"):
            publish_validated_geopackage(self.candidate, self.output, verify_published=reject)
        self.assertEqual(sha256_file(self.output), original_hash)

    def test_07_candidate_transaction_sidecars_are_never_ignored_or_deleted(self):
        for suffix in ("-wal", "-shm", "-journal"):
            with self.subTest(suffix=suffix):
                candidate = self.root / f"candidate-{suffix[1:]}.gpkg"
                shutil.copy2(self.reference, candidate)
                sidecar = Path(str(candidate) + suffix)
                sidecar.touch()
                with self.assertRaisesRegex(GeoPackagePublicationError, "not self-contained"):
                    publish_validated_geopackage(candidate, self.output, verify_published=self._verify)
                self.assertEqual(geopackage_sidecars(candidate), (sidecar,))
                self.assertTrue(sidecar.is_file())
                self.assertFalse(self.output.exists())

    def test_08_posix_path_does_not_retry_windows_specific_error(self):
        calls = 0

        def fail():
            nonlocal calls
            calls += 1
            raise windows_lock_error(32)

        with self.assertRaises(PermissionError):
            _run_file_operation(
                fail,
                description="POSIX isolation test",
                platform_name="posix",
                sleeper=lambda _delay: None,
            )
        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
