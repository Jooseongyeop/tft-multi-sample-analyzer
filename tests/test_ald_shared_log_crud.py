import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


PAGE_PATH = Path(__file__).resolve().parents[1] / "pages" / "2_ALD_Process_Log.py"
SPEC = importlib.util.spec_from_file_location("ald_process_log_shared_crud", PAGE_PATH)
ALD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ALD)


class SharedLogCrudTests(unittest.TestCase):
    def test_update_targets_only_selected_record(self):
        with patch.object(ALD, "supabase_request", return_value=[{"id": 7}]) as request:
            ALD.update_shared_log(7, {"idle_cvg": 0.006})
        request.assert_called_once_with("PATCH", "?id=eq.7", {"idle_cvg": 0.006})

    def test_delete_targets_only_selected_record(self):
        with patch.object(ALD, "supabase_request", return_value=[{"id": 7}]) as request:
            ALD.delete_shared_log(7)
        request.assert_called_once_with("DELETE", "?id=eq.7")


    def test_management_panel_is_collapsed_by_default(self):
        page_path = Path(__file__).resolve().parents[1] / "pages" / "2_ALD_Process_Log.py"
        source = page_path.read_text(encoding="utf-8")
        self.assertIn('with st.expander("기록 수정 · 삭제", expanded=False):', source)
        self.assertNotIn('Supabase 수정·삭제 권한 SQL 보기', source)
    def test_oil_change_preserves_lifetime_totals_and_resets_current_totals(self):
        records = pd.DataFrame([
            {"id": 1, "process_date": "2026-08-01", "created_at": "2026-08-01T01:00:00Z", "o3_cycles": 20, "main_cycles": 100, "note": "before"},
            {"id": 2, "process_date": "2026-08-02", "created_at": "2026-08-02T01:00:00Z", "o3_cycles": 0, "main_cycles": 0, "note": ALD.build_shared_log_note("oil changed", True)},
            {"id": 3, "process_date": "2026-08-03", "created_at": "2026-08-03T01:00:00Z", "o3_cycles": 7, "main_cycles": 40, "note": "after"},
        ])

        totals = ALD.calculate_shared_log_totals(records)

        self.assertEqual(totals["current_o3"], 7)
        self.assertEqual(totals["current_main"], 40)
        self.assertEqual(totals["lifetime_o3"], 27)
        self.assertEqual(totals["lifetime_main"], 140)
        self.assertEqual(int(totals["last_reset"]["id"]), 2)

    def test_oil_change_marker_is_hidden_from_display_note(self):
        stored = ALD.build_shared_log_note("pump oil replaced", True)
        self.assertTrue(ALD.is_oil_change_record(stored))
        self.assertEqual(ALD.clean_shared_log_note(stored), "pump oil replaced")
if __name__ == "__main__":
    unittest.main()
