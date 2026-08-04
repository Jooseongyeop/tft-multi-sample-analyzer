import unittest

import pandas as pd

import analyzer_engine as analyzer


class ParserTests(unittest.TestCase):
    def test_b1500_classic_header_and_dynamic_range(self):
        text = "\n".join([
            'Setup title,"dynamic range"',
            'Channel.UnitType,SMU,SMU,SMU',
            'Measurement.Primary.Start,-12',
            'Measurement.Primary.Stop,12',
            'VG,ID,IG,gm',
            '-12,1e-12,2e-13,0',
            '0,1e-8,2e-13,1e-9',
            '12,1e-5,2e-13,1e-7',
            '-12,2e-12,3e-13,0',
            '0,2e-8,3e-13,1e-9',
            '12,2e-5,3e-13,1e-7',
        ])
        frame, parser = analyzer.parse_b1500(text.encode("utf-8"))
        numeric = analyzer.numeric_frame(frame)
        curves = analyzer.curves_by_vd(numeric, 0.02)
        self.assertIn("Classic", parser)
        self.assertEqual(set(curves), {0.1, 1.0})
        self.assertEqual((curves[0.1].Vg.min(), curves[0.1].Vg.max()), (-12.0, 12.0))

    def test_legacy_b1500_still_supported(self):
        text = "\n".join([
            "DataName,VG,IG,ID",
            "DataValue,-2,1e-13,1e-12",
            "DataValue,0,1e-13,1e-9",
            "DataValue,2,1e-13,1e-6",
        ])
        frame, parser = analyzer.parse_b1500(text.encode("utf-8"))
        self.assertEqual(frame.shape, (3, 3))
        self.assertIn("DataName/DataValue", parser)

    def test_origin_merge_accepts_different_vg_ranges(self):
        narrow = pd.DataFrame({"Vg": [-2.0, 0.0, 2.0], "Id": [1.0, 2.0, 3.0]})
        wide = pd.DataFrame({"Vg": [-12.0, 0.0, 12.0], "Id": [4.0, 5.0, 6.0]})
        merged = analyzer.merge_origin_columns([("narrow", narrow), ("wide", wide)], "Id")
        self.assertEqual(merged.Vg.tolist(), [-12.0, -2.0, 0.0, 2.0, 12.0])
        self.assertTrue(pd.isna(merged.loc[merged.Vg == -12.0, "narrow"]).all())


if __name__ == "__main__":
    unittest.main()