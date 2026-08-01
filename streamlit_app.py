import streamlit as st

st.set_page_config(page_title="TFT Analyzer Suite", page_icon="📈", layout="wide")

navigation = st.navigation(
    [
        st.Page("pages/TFT_IV_Curve.py", title="TFT IV Curve", default=True),
        st.Page("pages/1_Reliability_Vth.py", title="Reliability Vth"),
        st.Page("pages/2_ALD_Process_Log.py", title="ALD Process Log"),
    ]
)
navigation.run()