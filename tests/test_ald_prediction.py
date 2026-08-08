import importlib.util
import unittest
from pathlib import Path


PAGE_PATH = Path(__file__).resolve().parents[1] / "pages" / "2_ALD_Process_Log.py"
SPEC = importlib.util.spec_from_file_location("ald_process_log_page", PAGE_PATH)
ALD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ALD)


class ConservativePredictionTests(unittest.TestCase):
    def test_uses_q3_and_floors_remaining_runs(self):
        result = ALD.calculate_conservative_prediction(
            0.0050, 0.0095, [0.0001, 0.0002, 0.0003, 0.0004]
        )
        self.assertAlmostEqual(result["conservative_slope"], 0.000325)
        self.assertAlmostEqual(result["margin"], 0.0045)
        self.assertEqual(result["remaining"], 13)

    def test_returns_zero_at_or_above_replacement_threshold(self):
        result = ALD.calculate_conservative_prediction(
            0.0100, 0.0095, [0.0001, 0.0002, 0.0003]
        )
        self.assertEqual(result["margin"], 0.0)
        self.assertEqual(result["remaining"], 0)


if __name__ == "__main__":
    unittest.main()