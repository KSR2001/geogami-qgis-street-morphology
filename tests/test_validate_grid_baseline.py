from pathlib import Path
import sys
import unittest

from shapely.geometry import LineString


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_grid_baseline.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))
import validate_grid_baseline as validator


def candidate(fid, coordinates):
    geometry = LineString(coordinates)
    endpoint_pair = (tuple(geometry.coords[0]), tuple(geometry.coords[-1]))
    return (fid, geometry, endpoint_pair)


class PairwiseTopologyTests(unittest.TestCase):
    def issue_types(self, candidates):
        issues = []
        validator._detect_pairwise_geometry_issues(candidates, issues)
        return [issue.issue_type for issue in issues]

    def test_exact_shared_endpoint_is_valid(self):
        candidates = [
            candidate(1, [(0, 0), (1, 0)]),
            candidate(2, [(1, 0), (1, 1)]),
        ]
        self.assertEqual(self.issue_types(candidates), [])

    def test_endpoint_to_interior_is_unsplit_t_junction(self):
        candidates = [
            candidate(1, [(0, 0), (2, 0)]),
            candidate(2, [(1, 1), (1, 0)]),
        ]
        self.assertEqual(
            self.issue_types(candidates),
            ["endpoint_interior_unsplit_intersection"],
        )

    def test_interior_to_interior_crossing_is_unsplit(self):
        candidates = [
            candidate(1, [(0, 0), (2, 0)]),
            candidate(2, [(1, -1), (1, 1)]),
        ]
        self.assertEqual(
            self.issue_types(candidates),
            ["interior_interior_unsplit_intersection"],
        )

    def test_reversed_duplicate_is_both_duplicate_and_exact_overlap(self):
        candidates = [
            candidate(1, [(0, 0), (2, 0)]),
            candidate(2, [(2, 0), (0, 0)]),
        ]
        self.assertEqual(
            self.issue_types(candidates),
            ["reversed_duplicate_geometry", "exact_overlapping_linestring"],
        )

    def test_partial_collinear_overlap_is_error(self):
        candidates = [
            candidate(1, [(0, 0), (2, 0)]),
            candidate(2, [(1, 0), (3, 0)]),
        ]
        self.assertEqual(self.issue_types(candidates), ["partial_collinear_overlap"])


class NearMissTests(unittest.TestCase):
    def test_near_miss_is_report_only_and_does_not_merge_endpoints(self):
        candidates = [
            candidate(1, [(0, 0), (1, 0)]),
            candidate(2, [(1.005, 0), (2, 0)]),
        ]
        issues = []
        pairs = validator._detect_near_misses(candidates, 0.01, issues)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(issues[0].severity, "warning")
        self.assertNotEqual(pairs[0]["endpoint_1"], pairs[0]["endpoint_2"])

    def test_exact_equal_endpoints_are_not_near_misses(self):
        candidates = [
            candidate(1, [(0, 0), (1, 0)]),
            candidate(2, [(1, 0), (2, 0)]),
        ]
        issues = []
        pairs = validator._detect_near_misses(candidates, 0.01, issues)
        self.assertEqual(pairs, [])
        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
