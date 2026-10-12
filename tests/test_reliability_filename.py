import ast
import re
import unittest
from pathlib import Path


PAGE_PATH = Path(__file__).resolve().parents[1] / "pages" / "1_Reliability_Vth.py"


def load_filename_parser():
    tree = ast.parse(PAGE_PATH.read_text(encoding="utf-8"))
    selected = []
    wanted_assignments = {
        "INITIAL_FILE_TIMES",
        "FOLLOWUP_FILE_TIMES",
        "FILE_TIMES",
        "TIME_PATTERN",
        "TYPE_PATTERN",
        "CONDITION_ALIASES",
    }
    wanted_functions = {"identify_file"}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if names & wanted_assignments:
                selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted_functions:
            selected.append(node)

    namespace = {
        "re": re,
        "np": __import__("numpy"),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(PAGE_PATH), "exec"), namespace)
    return namespace["identify_file"]


identify_file = load_filename_parser()


class ReliabilityFilenameTests(unittest.TestCase):
    def test_accepts_nbs_as_nbts(self):
        self.assertEqual(identify_file("sample_NBS-400.00s.csv"), ("NBTS", 400.0))

    def test_accepts_pbs_as_pbts(self):
        self.assertEqual(identify_file("sample_PBS_800s.csv"), ("PBTS", 800.0))

    def test_keeps_existing_full_names(self):
        self.assertEqual(identify_file("sample_NBTS-0.00s.csv"), ("NBTS", 0.0))
        self.assertEqual(identify_file("sample_PBTS-1800.00s.csv"), ("PBTS", 1800.0))

    def test_does_not_match_condition_inside_another_word(self):
        with self.assertRaisesRegex(ValueError, "NBTS/NBS"):
            identify_file("sample_ANBSX_100s.csv")


if __name__ == "__main__":
    unittest.main()
