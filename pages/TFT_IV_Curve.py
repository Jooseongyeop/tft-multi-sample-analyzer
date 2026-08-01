import streamlit as st

import analyzer_engine as analyzer

probe_mode = st.sidebar.radio(
    "Probe data format",
    ["4F standard table", "6F B1500 raw"],
    help="4F prioritizes table columns; 6F prioritizes B1500 DataName/DataValue rows.",
)

if probe_mode == "6F B1500 raw":
    analyzer.APP_VARIANT = "6F"
    analyzer.APP_TITLE = "TFT Multi-Sample Analyzer - 6F B1500"
    analyzer.PREFER_B1500 = True
else:
    analyzer.APP_VARIANT = "4F"
    analyzer.APP_TITLE = "TFT Multi-Sample Analyzer - 4F Standard"
    analyzer.PREFER_B1500 = False

analyzer.main(configure_page=False)
