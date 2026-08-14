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


if __name__ == "__main__":
    unittest.main()