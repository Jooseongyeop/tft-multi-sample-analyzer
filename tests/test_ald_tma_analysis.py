import importlib.util
import unittest
from pathlib import Path

import pandas as pd


PAGE_PATH = Path(__file__).resolve().parents[1] / "pages" / "2_ALD_Process_Log.py"
SPEC = importlib.util.spec_from_file_location("ald_process_log_page_features", PAGE_PATH)
ALD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ALD)


class TmaCycleAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "tma_pulse_s": 1.0, "tma_purge_s": 2.0,
            "main_o3_pulse_s": 1.0, "main_o3_purge_s": 2.0,
            "baseline_window_s": 1.0, "tma_delta_limit": 0.01,
        }

    def test_flags_small_tma_pressure_response(self):
        df = pd.DataFrame({"elapsed_s": [9.0, 10.0, 15.0, 16.0], "BTorr": [0.300, 0.305, 0.300, 0.330]})
        result = ALD.analyze_tma_cycles(df, 10.0, 2, self.settings)
        self.assertEqual(len(result), 2)
        self.assertTrue(bool(result.iloc[0].replacement_needed))
        self.assertFalse(bool(result.iloc[1].replacement_needed))
        self.assertAlmostEqual(result.iloc[0].pressure_delta_btorr, 0.005)

    def test_uses_tma_peak_not_pulse_average(self):
        df = pd.DataFrame({
            "elapsed_s": [9.0, 10.0, 10.5],
            "BTorr": [0.300, 0.300, 0.318],
        })
        result = ALD.analyze_tma_cycles(df, 10.0, 1, self.settings)
        self.assertAlmostEqual(result.iloc[0].tma_peak_btorr, 0.318)
        self.assertAlmostEqual(result.iloc[0].baseline_mean_btorr, 0.300)
        self.assertAlmostEqual(result.iloc[0].tma_peak_time_s, 10.5)
        self.assertAlmostEqual(result.iloc[0].pressure_delta_btorr, 0.018)
        self.assertFalse(bool(result.iloc[0].replacement_needed))

    def test_process_plot_shows_baseline_and_tma_peak(self):
        df = pd.DataFrame({
            "elapsed_time": pd.to_datetime([0.0, 1.0], unit="s", origin="2000-01-01"),
            "BTorr": [0.300, 0.318],
        })
        summary = pd.DataFrame({
            "main_cycle": [1], "cycle_start_s": [1.0],
            "baseline_time_s": [1.0], "tma_peak_time_s": [1.5],
            "baseline_mean_btorr": [0.300], "tma_peak_btorr": [0.318],
            "pressure_delta_btorr": [0.018], "replacement_needed": [False],
        })
        figure = ALD.process_plot(
            df, [("Pre-process", 0.0, 0.5), ("NCD_O3_ONLY", 0.5, 1.0),
                 ("Main deposition", 1.0, 2.0), ("Post-process", 2.0, 2.5)],
            summary, False, 10, 1.0, 0.5,
        )
        names = [trace.name for trace in figure.data]
        self.assertIn("TMA 직전 baseline 평균", names)
        self.assertIn("TMA pulse peak", names)

    def test_infers_cycles_from_total_layer_and_duration(self):
        settings = {
            "pre_delay_s": 1.0, "pre_flow_s": 1.0,
            "o3_pulse_s": 2.0, "o3_purge_s": 1.0,
            "tma_pulse_s": 1.0, "tma_purge_s": 1.0,
            "main_o3_pulse_s": 1.0, "main_o3_purge_s": 1.0,
            "post_flow_s": 1.0, "post_delay_s": 1.0,
            "o3_cycles": 1, "main_cycles": 1,
        }
        # fixed=4 s, O3=3*3 s, Main=2*4 s -> total 21 s
        lines = ["Total Layer : 2", "2026-01-01 00:00:00:000\tBTorr\tState"]
        lines += [f"2026-01-01 00:00:{second:02d}:000\t0.3\t1" for second in range(1, 22)]
        result = ALD.infer_cycle_counts("\n".join(lines).encode(), settings)
        self.assertEqual(result["main_cycles"], 2)
        self.assertEqual(result["o3_cycles"], 3)
        self.assertAlmostEqual(result["duration_error_s"], 0.0)


if __name__ == "__main__":
    unittest.main()