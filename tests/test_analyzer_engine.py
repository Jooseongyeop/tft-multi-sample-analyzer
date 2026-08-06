import unittest
from io import BytesIO

import pandas as pd
from openpyxl import load_workbook

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

    def test_gate_current_is_converted_to_absolute_value(self):
        frame = pd.DataFrame({
            "VG": [-1, 0, 1],
            "ID": [1e-12, 1e-9, 1e-6],
            "IG": [-1e-13, 2e-13, -3e-13],
        })
        numeric = analyzer.numeric_frame(frame)
        self.assertEqual(numeric.Ig.tolist(), [1e-13, 2e-13, 3e-13])

    def test_ppt_summary_uses_five_significant_figures(self):
        summary = pd.DataFrame([{
            "Sample": "sample_A",
            "Mobility max [cm2/Vs]": 10.24703435,
            "Vth [V]": 1.6490481,
            "SS [mV/dec]": 76.469075,
        }])
        table = analyzer.ppt_summary_table(summary)
        self.assertEqual(table.iloc[0].to_dict(), {
            "Sample": "sample_A",
            "FEM [cm2/Vs]": "10.247",
            "Vth [V]": "1.6490",
            "SS [mV/dec]": "76.469",
        })

    def test_copy_safe_summary_has_five_significant_figure_strings(self):
        summary = pd.DataFrame([{
            "Sample": "sample_A",
            "Mobility max [cm2/Vs]": 30.74404422,
            "Vth [V]": 0.1642206289,
            "SS [mV/dec]": 44.96501831,
        }])
        copied = analyzer.copy_safe_summary_table(summary)
        self.assertEqual(copied.loc[0, "Mobility max [cm2/Vs]"], "30.744")
        self.assertEqual(copied.loc[0, "Vth [V]"], "0.16422")
        self.assertEqual(copied.loc[0, "SS [mV/dec]"], "44.965")

    def test_ppt_copy_excel_sheet_uses_12_point_font(self):
        summary = pd.DataFrame([{
            "Sample": "sample_A",
            "Mobility max [cm2/Vs]": 30.74404422,
            "Vth [V]": 0.1642206289,
            "SS [mV/dec]": 44.96501831,
        }])
        content = analyzer.workbook_bytes(summary, [], [], [], [], [])
        workbook = load_workbook(BytesIO(content))
        sheet = workbook["PPT_Copy"]
        self.assertEqual(sheet["B2"].value, "30.744")
        self.assertEqual(sheet["C2"].value, "0.16422")
        self.assertEqual(sheet["D2"].value, "44.965")
        self.assertEqual(sheet["B2"].font.sz, 12)

    def test_preview_plot_contains_transfer_and_mobility_panels(self):
        frame = pd.DataFrame({
            "Vg": [-1.0, 0.0, 1.0],
            "abs_Id": [1e-12, 1e-9, 1e-6],
            "Ig": [1e-13, 2e-13, 3e-13],
            "Mobility_cm2_Vs": [0.1, 1.0, 5.0],
            "SS_fit_used": [False, True, True],
        })
        figure = analyzer.plot_all([("sample_A", frame)])
        self.assertEqual(len(figure.axes), 2)
        self.assertEqual(figure.axes[0].get_ylabel(), "|Current| [A]")
        self.assertEqual(figure.axes[1].get_ylabel(), "Mobility [cm2/Vs]")

if __name__ == "__main__":
    unittest.main()
