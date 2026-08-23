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


    def test_pecvd_exact_recipe_and_target_thickness_scaling(self):
        result = ALD.calculate_pecvd_process_time(20, 1000, 200)
        self.assertAlmostEqual(result["process_time_s"], 1466.0)
        self.assertEqual(result["method"], "실측 recipe")

    def test_pecvd_interpolates_n2o_at_same_sih4(self):
        result = ALD.calculate_pecvd_process_time(35, 750, 100)
        self.assertFalse(result["extrapolated"])
        self.assertEqual(result["method"], "동일 SiH4 조건의 N2O 보간")
        self.assertGreater(result["deposition_rate_nm_s"], 100 / 470)
        self.assertLess(result["deposition_rate_nm_s"], 100 / 400)

    def test_two_dimensional_model_learns_n2o_effect_and_flags_extrapolation(self):
        measured_175_500 = ALD.calculate_pecvd_process_time(175, 500, 100)
        predicted_175_1000 = ALD.calculate_pecvd_process_time(175, 1000, 100)
        self.assertLess(
            predicted_175_1000["process_time_s"],
            measured_175_500["process_time_s"],
        )
        self.assertTrue(predicted_175_1000["extrapolated"])
        self.assertEqual(predicted_175_1000["method"], "2차원 power-law 외삽")
        self.assertEqual(predicted_175_1000["confidence"], "낮음")
        self.assertGreater(predicted_175_1000["model_r2"], 0.9)
    def test_new_measurement_is_used_immediately(self):
        reference = ALD.pd.concat([
            ALD.PECVD_320C_REFERENCE,
            ALD.pd.DataFrame([{
                "SiH4 [sccm]": 70.0,
                "N2O [sccm]": 1000.0,
                "100 nm time [s]": 250.0,
            }]),
        ], ignore_index=True)
        result = ALD.calculate_pecvd_process_time(70, 1000, 100, reference)
        self.assertAlmostEqual(result["process_time_s"], 250.0)
        self.assertEqual(result["matching_points"], 1)
        self.assertEqual(result["reference_points"], 5)

    def test_repeated_exact_recipe_uses_median_deposition_rate(self):
        reference = ALD.pd.DataFrame([
            {"SiH4 [sccm]": 50.0, "N2O [sccm]": 1000.0, "100 nm time [s]": 200.0},
            {"SiH4 [sccm]": 50.0, "N2O [sccm]": 1000.0, "100 nm time [s]": 400.0},
        ])
        result = ALD.calculate_pecvd_process_time(50, 1000, 100, reference)
        self.assertAlmostEqual(result["deposition_rate_nm_s"], 0.375)
        self.assertAlmostEqual(result["process_time_s"], 100 / 0.375)
        self.assertEqual(result["matching_points"], 2)
if __name__ == "__main__":
    unittest.main()
