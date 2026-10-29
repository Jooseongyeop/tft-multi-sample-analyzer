import ast
import unittest
from pathlib import Path
from types import SimpleNamespace


ENGINE_PATH = Path(__file__).resolve().parents[1] / "analyzer_engine.py"


def load_upload_helpers():
    tree = ast.parse(ENGINE_PATH.read_text(encoding="utf-8"))
    wanted = {
        "sample_name",
        "unique_names",
        "combine_upload_groups",
        "upload_source",
    }
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {"Path": Path}
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), str(ENGINE_PATH), "exec"),
        namespace,
    )
    return namespace


HELPERS = load_upload_helpers()


class MultiFolderUploadTests(unittest.TestCase):
    def test_combines_files_and_multiple_folder_batches(self):
        a = SimpleNamespace(name="individual.csv")
        b = SimpleNamespace(name="folder_A/sample.csv")
        c = SimpleNamespace(name="folder_B/sample.csv")
        combined = HELPERS["combine_upload_groups"]([a], [b], [c])
        self.assertEqual(combined, [a, b, c])

    def test_preserves_folder_source_and_unique_sample_names(self):
        files = [
            SimpleNamespace(name="folder_A/sample.csv"),
            SimpleNamespace(name="folder_B/sample.csv"),
        ]
        self.assertEqual(HELPERS["upload_source"](files[0].name), "folder_A")
        self.assertEqual(HELPERS["unique_names"](files), ["sample", "sample_2"])

    def test_labels_individually_selected_files(self):
        self.assertEqual(
            HELPERS["upload_source"]("sample.csv"),
            "Individual file",
        )


if __name__ == "__main__":
    unittest.main()
