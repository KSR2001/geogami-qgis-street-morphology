from __future__ import annotations

from pathlib import Path
import shlex
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_environment_comparison import parse_args as parse_comparison_args
from run_full_analysis import parse_args as parse_analysis_args


DOCUMENT = ROOT / "docs" / "professor_workflow.md"

ANALYSIS_COMMANDS = {
    "env39": (
        "python scripts/run_full_analysis.py --environment env39 --input "
        "data/editable/grid/env39_editable.gpkg --mode preserve-topology"
    ),
    "env38": (
        "python scripts/run_full_analysis.py --environment env38 --input "
        "data/editable/curvilinear/env38_editable.gpkg --mode preserve-topology"
    ),
}

NOTEBOOKS = (
    "00_osmnx_networkx_introduction.ipynb",
    "01_env39_load_canonical_graph.ipynb",
    "02_env39_topological_metrics.ipynb",
    "03_env39_geometry_orientation_metrics.ipynb",
    "04_env39_integrated_results.ipynb",
    "05_env38_load_canonical_graph.ipynb",
    "06_env38_topological_metrics.ipynb",
    "07_env38_geometry_orientation_metrics.ipynb",
    "08_env38_integrated_results.ipynb",
)


class ProfessorWorkflowDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = DOCUMENT.read_text(encoding="utf-8")

    def test_01_registered_fixed_paths_exist_and_are_documented(self):
        paths = (
            "qgis/geogami_baselines.qgz",
            "config/environments.yaml",
            "data/editable/grid/env39_editable.gpkg",
            "data/editable/curvilinear/env38_editable.gpkg",
            "data/canonical/grid/env39_canonical.gpkg",
            "data/canonical/grid/runs",
            "data/canonical/curvilinear/runs",
            "results/analysis/env39",
            "results/analysis/env38",
            "results/comparison/env38_vs_env39",
        )
        for relative in paths:
            with self.subTest(path=relative):
                self.assertTrue((ROOT / relative).exists())
                self.assertIn(relative, self.document)
        self.assertIn("environments.env39.reference_image", self.document)
        self.assertIn("environments.env38.reference_image", self.document)
        self.assertIn("if a registered file is absent", self.document)

    def test_02_analysis_commands_match_current_parser_for_both_environments(self):
        for environment, command in ANALYSIS_COMMANDS.items():
            with self.subTest(environment=environment):
                self.assertIn(command, self.document)
                self.assertIn(f"{command} --dry-run", self.document)
                args = shlex.split(command)[2:]
                parsed = parse_analysis_args(args)
                self.assertEqual(parsed.environment, environment)
                self.assertEqual(parsed.mode, "preserve-topology")
                self.assertFalse(parsed.dry_run)
                dry_parsed = parse_analysis_args(args + ["--dry-run"])
                self.assertTrue(dry_parsed.dry_run)

    def test_03_environment_choices_reject_cross_environment_labels(self):
        with self.assertRaises(SystemExit):
            parse_analysis_args(
                [
                    "--environment", "env40", "--input", "example.gpkg",
                    "--mode", "preserve-topology",
                ]
            )

    def test_04_comparison_command_matches_parser_and_documents_no_dry_run(self):
        command = "python scripts/build_environment_comparison.py"
        self.assertIn(command, self.document)
        parsed = parse_comparison_args([])
        self.assertIsNone(parsed.comparison_id)
        self.assertFalse(parsed.phase_start_clean)
        self.assertIsNone(parsed.phase_start_commit)
        with self.assertRaises(SystemExit):
            parse_comparison_args(["--dry-run"])
        self.assertIn("currently provides no\ndry-run or temporary-output option", self.document)

    def test_05_all_professor_notebooks_exist_and_are_documented(self):
        for filename in NOTEBOOKS:
            with self.subTest(notebook=filename):
                self.assertTrue((ROOT / "notebooks" / filename).is_file())
                self.assertIn(filename, self.document)

    def test_06_protected_paths_and_safety_contract_are_prominent(self):
        protected = (
            "data/baselines/grid/network_v2.gpkg",
            "data/canonical/grid/env39_canonical.gpkg",
            "data/canonical/grid/runs/*",
            "data/canonical/curvilinear/runs/*",
            "results/analysis/*",
            "results/comparison/*",
            "data/working/curvilinear/env38_working.gpkg",
        )
        for path in protected:
            self.assertIn(path, self.document)
        for safety_phrase in (
            "46 nodes and 69 physical streets",
            "Topological Editing",
            "Snapping on Intersection",
            "Self-snapping",
            "git status --porcelain",
            "git reset --hard",
            "git clean -f",
            "external backup",
        ):
            self.assertIn(safety_phrase, self.document)


if __name__ == "__main__":
    unittest.main()
