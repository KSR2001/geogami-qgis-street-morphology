from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from geogami_morphology.guards import verify_frozen_baselines
from geogami_morphology.io import sha256_file
from geogami_morphology.workflow import (
    GitSnapshot,
    WorkflowError,
    capture_git_snapshot,
    classify_workflow_provenance,
    run_full_analysis,
)


ACCEPTED_RUN = "env39_20260824T123033467880Z_2046798c1e9c"
ACCEPTED_ANALYSIS = ROOT / "results" / "analysis" / "env39" / ACCEPTED_RUN
COMMIT = "a" * 40


def snapshot(*, dirty=False, paths=(), commit=COMMIT, branch="clean-branch"):
    return GitSnapshot(
        repository=ROOT.name, repository_root=".", remote_origin="test-origin",
        branch=branch, commit_sha=commit, dirty=dirty,
        changed_paths=tuple(paths),
    )


def mapping(value: GitSnapshot) -> dict:
    return value.as_dict()


def directory_hashes(path: Path) -> dict[str, str]:
    return {
        item.relative_to(path).as_posix(): sha256_file(item)
        for item in path.rglob("*") if item.is_file()
    }


class Phase7JProvenanceFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ROOT / "tests" / ".tmp" / f"phase7jp-{uuid.uuid4().hex}"
        cls.root.mkdir(parents=True)
        cls.input = cls.root / "env39_editable.gpkg"
        shutil.copy2(ROOT / "data" / "editable" / "grid" / "env39_editable.gpkg", cls.input)
        cls.runs_root = cls.root / "canonical" / "runs"
        cls.canonical_latest = cls.root / "canonical" / "latest.json"
        cls.analysis_root = cls.root / "analysis"
        cls.analysis_latest = cls.analysis_root / "env39" / "latest.json"
        cls.accepted_before = directory_hashes(ACCEPTED_ANALYSIS)
        cls.capture_before_outputs = False
        calls = 0

        def workflow_git(_root):
            nonlocal calls
            calls += 1
            if calls == 1:
                cls.capture_before_outputs = (
                    not cls.runs_root.exists()
                    and not cls.analysis_root.exists()
                    and not cls.canonical_latest.exists()
                    and not cls.analysis_latest.exists()
                )
                return mapping(snapshot())
            pointer = json.loads(cls.canonical_latest.read_text(encoding="utf-8"))
            run_id = pointer["run_id"]
            changed = (
                (cls.runs_root / run_id / "env39_canonical.gpkg").relative_to(ROOT).as_posix(),
                cls.canonical_latest.relative_to(ROOT).as_posix(),
                (cls.analysis_root / "env39" / run_id / "end_to_end_analysis_manifest.json").relative_to(ROOT).as_posix(),
                cls.analysis_latest.relative_to(ROOT).as_posix(),
            )
            return mapping(snapshot(dirty=True, paths=changed))

        with patch("geogami_morphology.workflow.git_provenance", side_effect=workflow_git), patch(
            "geogami_morphology.versioned.git_provenance", return_value=mapping(snapshot())
        ):
            cls.result = run_full_analysis(
                "env39", input_path=cls.input, mode="preserve-topology",
                reference_canonical=ROOT / "data" / "canonical" / "grid" / "env39_canonical.gpkg",
                runs_root=cls.runs_root, canonical_latest=cls.canonical_latest,
                analysis_root=cls.analysis_root, analysis_latest=cls.analysis_latest,
                metrics_config=ROOT / "config" / "metrics.yaml",
                project_root=ROOT, reporter=lambda value: None,
            )
        cls.manifest = json.loads(cls.result.end_to_end_manifest_path.read_text(encoding="utf-8"))
        cls.accepted_after = directory_hashes(ACCEPTED_ANALYSIS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def classify(self, end: GitSnapshot, **overrides):
        values = dict(
            allowed_generated_roots=("data/canonical/grid/runs/new", "results/analysis/env39/new"),
            allowed_generated_files=("data/canonical/grid/latest.json", "results/analysis/env39/latest.json"),
        )
        values.update(overrides)
        return classify_workflow_provenance(snapshot(), end, **values)

    def test_01_clean_entry_snapshot_captures_dirty_false(self):
        raw = mapping(snapshot())
        with patch("geogami_morphology.workflow.git_provenance", return_value=raw):
            captured = capture_git_snapshot(ROOT)
        self.assertFalse(captured.dirty)
        self.assertEqual(captured.changed_paths, ())

    def test_02_dirty_entry_fails_before_output_creation(self):
        output = self.root / "dirty-gate"
        dirty = mapping(snapshot(dirty=True, paths=("src/change.py",)))
        with patch("geogami_morphology.workflow.git_provenance", return_value=dirty), patch(
            "geogami_morphology.workflow.run_versioned_canonical"
        ) as canonical:
            with self.assertRaisesRegex(WorkflowError, "Clean-start Git provenance"):
                run_full_analysis(
                    "env39", input_path=self.input, mode="preserve-topology",
                    runs_root=output / "runs", analysis_root=output / "analysis",
                    project_root=ROOT, reporter=lambda value: None,
                )
        canonical.assert_not_called()
        self.assertFalse(output.exists())

    def test_03_start_snapshot_is_captured_before_any_generated_output(self):
        self.assertTrue(self.capture_before_outputs)

    def test_04_start_snapshot_is_detached_and_immutable(self):
        raw = mapping(snapshot())
        with patch("geogami_morphology.workflow.git_provenance", return_value=raw):
            captured = capture_git_snapshot(ROOT)
        raw["dirty"] = True
        raw["changed_paths"].append("src/later.py")
        self.assertFalse(captured.dirty)
        self.assertEqual(captured.changed_paths, ())
        with self.assertRaises(Exception):
            captured.dirty = True

    def test_05_generated_canonical_output_may_make_end_dirty(self):
        result = self.classify(snapshot(dirty=True, paths=("data/canonical/grid/runs/new/env39_canonical.gpkg",)))
        self.assertEqual(result["status"], "PASS")

    def test_06_generated_analysis_output_may_make_end_dirty(self):
        result = self.classify(snapshot(dirty=True, paths=("results/analysis/env39/new/core_metrics.csv",)))
        self.assertEqual(result["status"], "PASS")

    def test_07_generated_dirtiness_does_not_invalidate_clean_start(self):
        result = self.classify(snapshot(dirty=True, paths=("results/analysis/env39/latest.json",)))
        self.assertTrue(result["clean_start"])
        self.assertTrue(result["only_expected_generated_changes"])

    def test_08_workflow_end_git_records_generated_changed_paths(self):
        self.assertTrue(self.manifest["workflow_end_git"]["dirty"])
        self.assertEqual(len(self.manifest["workflow_end_git"]["changed_paths"]), 4)
        self.assertEqual(len(self.manifest["provenance_acceptance"]["allowed_generated_changes"]), 4)

    def test_09_unexpected_src_modification_fails(self):
        result = self.classify(snapshot(dirty=True, paths=("src/geogami_morphology/workflow.py",)))
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["source_code_unchanged"])

    def test_10_unexpected_scripts_modification_fails(self):
        result = self.classify(snapshot(dirty=True, paths=("scripts/run_full_analysis.py",)))
        self.assertEqual(result["status"], "FAIL")

    def test_11_unexpected_config_modification_fails(self):
        result = self.classify(snapshot(dirty=True, paths=("config/metrics.yaml",)))
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["configuration_unchanged"])

    def test_12_frozen_baseline_modification_fails_acceptance(self):
        result = self.classify(snapshot(), frozen_inputs_unchanged=False)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["frozen_inputs_unchanged"])

    def test_13_editable_input_modification_during_run_fails_acceptance(self):
        result = self.classify(snapshot(), editable_input_unchanged=False)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["editable_input_unchanged"])

    def test_14_head_commit_change_fails(self):
        result = self.classify(snapshot(commit="b" * 40))
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["head_unchanged"])

    def test_15_manifest_contains_workflow_start_git(self):
        self.assertIn("workflow_start_git", self.manifest)
        self.assertFalse(self.manifest["workflow_start_git"]["dirty"])

    def test_16_manifest_contains_workflow_end_git(self):
        self.assertIn("workflow_end_git", self.manifest)

    def test_17_manifest_contains_provenance_acceptance(self):
        self.assertIn("provenance_acceptance", self.manifest)
        self.assertEqual(self.manifest["schema_version"], "2.0.0")

    def test_18_clean_start_is_accepted(self):
        self.assertTrue(self.manifest["provenance_acceptance"]["clean_start"])
        self.assertEqual(self.manifest["provenance_acceptance"]["status"], "PASS")

    def test_19_generated_results_are_not_hidden_by_gitignore(self):
        completed = subprocess.run(
            ["git", "check-ignore", "--no-index", "data/canonical/grid/runs/probe/file", "results/analysis/env39/probe/file"],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 1, completed.stdout)

    def test_20_stage_manifests_carry_start_snapshot_alongside_stage_state(self):
        for name, record in self.manifest["stage_manifests"].items():
            if name == "canonical_manifest":
                continue
            document = json.loads((ROOT / record["path"]).read_text(encoding="utf-8"))
            self.assertEqual(document["workflow_start_git"], self.manifest["workflow_start_git"])

    def test_21_canonical_manifest_preserves_clean_publication_provenance(self):
        record = self.manifest["stage_manifests"]["canonical_manifest"]
        document = json.loads((ROOT / record["path"]).read_text(encoding="utf-8"))
        self.assertFalse(document["git_provenance"]["dirty"])
        self.assertEqual(document["git_provenance"]["commit_sha"], COMMIT)

    def test_22_scientific_outputs_and_accepted_run_are_unchanged(self):
        self.assertEqual(self.accepted_before, self.accepted_after)
        self.assertEqual(self.manifest["network_identity"]["node_count"], 46)
        self.assertEqual(self.manifest["network_identity"]["physical_edge_count"], 69)
        self.assertEqual(self.manifest["network_identity"]["osmnx_directed_arc_count"], 138)

    def test_23_frozen_baseline_guard_still_passes(self):
        observed = verify_frozen_baselines(ROOT)
        self.assertEqual(observed["data/baselines/grid/network_v2.gpkg"], "7698544A01EED008E5451F8368703AFD0DBE232D10C9D7ADAA58C13DA57E0289")
        self.assertEqual(observed["data/canonical/grid/env39_canonical.gpkg"], "212A2AA583BF77304859DB76639B8AD4B6DC458237EEA24FEB60FC228E5D6847")


if __name__ == "__main__":
    unittest.main()
