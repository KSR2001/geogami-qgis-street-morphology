from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import verify_frozen_baselines as guard


class FrozenPhase5BaselineGuardTests(unittest.TestCase):
    def test_both_accepted_phase5_files_are_byte_exact(self):
        self.assertEqual(
            guard.verify_frozen_baselines(PROJECT_ROOT),
            {path.as_posix(): digest for path, digest in guard.FROZEN_BASELINES.items()},
        )


if __name__ == "__main__":
    unittest.main()
