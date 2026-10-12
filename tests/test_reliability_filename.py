import ast
import csv
import io
import re
import unittest
from pathlib import Path

import pandas as pd


PAGE_PATH = Path(__file__).resolve().parents[1] / "pages" / "1_Reliability_Vth.py"


def load_reliability_helpers():
    tree = ast.parse(PAGE_PATH.read_text(encoding="utf-8"))
    selected = []
    wanted_assignments = {
        "INITIAL_FILE_TIMES",
        "TIME_SCHEDULES",
        "FILE_TIMES",
        "TIME_PATTERN",
        "TYPE_PATTERN",
        "CONDITION_ALIASES",
    }
    wanted_functions = {
        "read_measurement",
        "identify_file",
        "resolve_time_schedule",
        "constant_current_vth",
    }
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if names & wanted_assignments:
                selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted_functions:
            selected.append(node)

    namespace = {
        "csv": csv,
        "io": io,
        "pd": pd,
        "re": re,
        "np": __import__("numpy"),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(PAGE_PATH), "exec"), namespace)
    return namespace


HELPERS = load_reliability_helpers()
identify_file = HELPERS["identify_file"]
read_measurement = HELPERS["read_measurement"]
resolve_time_schedule = HELPERS["resolve_time_schedule"]
constant_current_vth = HELPERS["constant_current_vth"]


class UploadedFile:
    def __init__(self, name, content):
        self.name = name
        self._content = content

    def getvalue(self):
        return self._content


class ReliabilityFilenameTests(unittest.TestCase):
    def test_accepts_nbs_as_nbts(self):
        self.assertEqual(identify_file("sample_NBS-400.00s.csv"), ("NBTS", 400.0))

    def test_accepts_pbs_as_pbts(self):
        self.assertEqual(identify_file("sample_PBS_800s.csv"), ("PBTS", 800.0))

    def test_keeps_existing_full_names(self):
        self.assertEqual(identify_file("sample_NBTS-0.00s.csv"), ("NBTS", 0.0))
        self.assertEqual(identify_file("sample_PBTS-1800.00s.csv"), ("PBTS", 1800.0))

    def test_accepts_accumulated_3600_second_file(self):
        self.assertEqual(identify_file("sample_NBS_3600s.csv"), ("NBTS", 3600.0))

    def test_does_not_match_condition_inside_another_word(self):
        with self.assertRaisesRegex(ValueError, "NBTS/NBS"):
            identify_file("sample_ANBSX_100s.csv")

    def test_reads_b1500_dataname_datavalue_rows(self):
        raw = "\n".join(
            [
                "SetupTitle, NBS sample",
                "DataName, VG, IG, ID, Gm",
                "DataValue, -2, -1e-13, 1e-12, 0",
                "DataValue, 2, -2e-13, 1e-6, 1e-7",
            ]
        ).encode("utf-8")
        frame = read_measurement(UploadedFile("sample_NBS_1s.csv", raw))
        self.assertEqual(frame.columns.tolist(), ["VG", "IG", "ID", "Gm"])
        self.assertEqual(frame.shape, (2, 4))

    def test_detects_accumulated_time_schedule(self):
        curves = {time: pd.DataFrame() for time in (1, 100, 500, 800, 1800, 3600)}
        name, ordered, plot_time = resolve_time_schedule(curves)
        self.assertEqual(name, "accumulated")
        self.assertEqual(ordered, [1.0, 100.0, 500.0, 800.0, 1800.0, 3600.0])
        self.assertEqual(plot_time[500.0], 500.0)

    def test_keeps_legacy_interval_time_schedule(self):
        curves = {time: pd.DataFrame() for time in (1, 100, 400, 500, 800, 1800)}
        name, ordered, plot_time = resolve_time_schedule(curves)
        self.assertEqual(name, "interval")
        self.assertEqual(ordered, [1.0, 100.0, 400.0, 500.0, 800.0, 1800.0])
        self.assertEqual(plot_time[500.0], 1000.0)


if __name__ == "__main__":
    unittest.main()
