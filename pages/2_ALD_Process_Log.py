from __future__ import annotations

import io
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st


APP_DIR = Path(__file__).resolve().parent


def decode_log(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp949", "euc-kr", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def parse_timestamp(value: str) -> pd.Timestamp:
    return pd.to_datetime(value, format="%Y-%m-%d %H:%M:%S:%f", errors="coerce")


def low_fraction_mean(values: pd.Series, fraction: float = 0.05) -> float:
    x = pd.to_numeric(values, errors="coerce").dropna().sort_values()
    if x.empty:
        return np.nan
    n = max(1, math.ceil(len(x) * fraction))
    return float(x.iloc[:n].mean())


def parse_ald_log(raw: bytes, filename: str, settings: dict):
    text = decode_log(raw)
    lines = text.splitlines()
    process_name = ""
    wafer_id = ""
    total_layer = settings["main_cycles"]
    process_start = pd.NaT
    for line in lines[:80]:
        if line.startswith("Process Name"):
            process_name = line.split(":", 1)[-1].strip()
        elif line.startswith("Wafer ID"):
            wafer_id = line.split(":", 1)[-1].strip()
        elif "Total Layer" in line:
            m = re.search(r"Total Layer\s*:\s*(\d+)", line)
            if m:
                total_layer = int(m.group(1))
        elif "Pre-process Start" in line:
            process_start = parse_timestamp(line.split("\t", 1)[0].strip())

    header_index = None
    for i, line in enumerate(lines):
        if "\tBTorr\t" in line and re.match(r"^\d{4}-\d{2}-\d{2}", line):
            header_index = i
            if pd.isna(process_start):
                process_start = parse_timestamp(line.split("\t", 1)[0].strip())
            break
    if header_index is None or pd.isna(process_start):
        raise ValueError("BTorr 실시간 데이터 헤더 또는 Pre-process 시작 시각을 찾지 못했습니다.")

    records = []
    for line in lines[header_index + 1 :]:
        if not re.match(r"^\d{4}-\d{2}-\d{2}", line):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        ts = parse_timestamp(parts[0].strip())
        btorr = pd.to_numeric(parts[1], errors="coerce")
        if pd.isna(ts) or pd.isna(btorr):
            continue
        records.append((ts, float(btorr)))
    if not records:
        raise ValueError("실제 측정 BTorr 행을 찾지 못했습니다.")

    df = pd.DataFrame(records, columns=["timestamp", "BTorr"])
    df["elapsed_s"] = (df.timestamp - process_start).dt.total_seconds()
    df = df[df.elapsed_s >= 0].reset_index(drop=True)

    pre_d = settings["pre_delay_s"] + settings["pre_flow_s"]
    o3_cycle_d = settings["o3_pulse_s"] + settings["o3_purge_s"]
    o3_d = settings["o3_cycles"] * o3_cycle_d
    main_cycle_d = settings["tma_pulse_s"] + settings["tma_purge_s"] + settings["main_o3_pulse_s"] + settings["main_o3_purge_s"]
    main_cycles = total_layer if settings["use_total_layer"] else settings["main_cycles"]
    main_d = main_cycles * main_cycle_d
    post_d = settings["post_flow_s"] + settings["post_delay_s"]
    b0, b1, b2, b3, b4 = 0.0, pre_d, pre_d + o3_d, pre_d + o3_d + main_d, pre_d + o3_d + main_d + post_d

    e = df.elapsed_s.to_numpy()
    df["major_step"] = np.select(
        [e < b1, e < b2, e < b3, e <= b4 + settings["boundary_tolerance_s"]],
        ["1. Pre-process", "2. NCD_O3_ONLY", "3. Main deposition", "4. Post-process"],
        default="5. Outside expected duration",
    )
    df["cycle_no"] = np.nan
    df["detail_step"] = ""

    o3mask = (e >= b1) & (e < b2)
    o3phase = (e[o3mask] - b1) % o3_cycle_d
    df.loc[o3mask, "cycle_no"] = np.floor((e[o3mask] - b1) / o3_cycle_d) + 1
    df.loc[o3mask, "detail_step"] = np.where(o3phase < settings["o3_pulse_s"], "O3 pulse", "O3 purge")

    mmask = (e >= b2) & (e < b3)
    phase = (e[mmask] - b2) % main_cycle_d
    df.loc[mmask, "cycle_no"] = np.floor((e[mmask] - b2) / main_cycle_d) + 1
    a = settings["tma_pulse_s"]
    b = a + settings["tma_purge_s"]
    c = b + settings["main_o3_pulse_s"]
    df.loc[mmask, "detail_step"] = np.select(
        [phase < a, phase < b, phase < c],
        ["TMA pulse", "N2 purge 1", "O3 pulse"],
        default="N2 purge 2",
    )
    df.loc[e < b1, "detail_step"] = np.where(e[e < b1] < settings["pre_delay_s"], "Pre delay", "Pre flow")
    pmask = (e >= b3) & (e <= b4 + settings["boundary_tolerance_s"])
    df.loc[pmask, "detail_step"] = np.where(e[pmask] - b3 < settings["post_flow_s"], "Post flow", "Post delay")
    df["elapsed_time"] = pd.to_datetime(df.elapsed_s, unit="s", origin="2000-01-01")

    step_summary = df.groupby("major_step", sort=False).agg(
        start_s=("elapsed_s", "min"), end_s=("elapsed_s", "max"), points=("BTorr", "size"),
        min_btorr=("BTorr", "min"), median_btorr=("BTorr", "median"), max_btorr=("BTorr", "max"),
    ).reset_index()
    step_summary["duration_s"] = step_summary.end_s - step_summary.start_s
    robust = df.groupby("major_step", sort=False).BTorr.apply(low_fraction_mean).rename("low5_mean_btorr").reset_index()
    step_summary = step_summary.merge(robust, on="major_step", how="left")

    cyc = df[df.major_step.isin(["2. NCD_O3_ONLY", "3. Main deposition"])].copy()
    cycle_summary = cyc.groupby(["major_step", "cycle_no"], sort=False).agg(
        start_s=("elapsed_s", "min"), end_s=("elapsed_s", "max"), points=("BTorr", "size"),
        min_btorr=("BTorr", "min"), median_btorr=("BTorr", "median"), max_btorr=("BTorr", "max"),
    ).reset_index()
    rb = cyc.groupby(["major_step", "cycle_no"], sort=False).BTorr.apply(low_fraction_mean).rename("low5_mean_btorr").reset_index()
    cycle_summary = cycle_summary.merge(rb, on=["major_step", "cycle_no"], how="left")

    metadata = {
        "file_name": filename, "process_name": process_name, "wafer_id": wafer_id,
        "process_start": process_start, "actual_end": df.timestamp.max(), "actual_duration_s": float(df.elapsed_s.max()),
        "total_layer": total_layer, "main_cycles_used": main_cycles, "expected_duration_s": b4,
        "boundary_error_s": float(df.elapsed_s.max() - b4),
        "main_absolute_min_btorr": float(df.loc[mmask, "BTorr"].min()),
        "main_lowest5pct_mean_btorr": low_fraction_mean(df.loc[mmask, "BTorr"]),
    }
    boundaries = [("Pre-process", b0, b1), ("NCD_O3_ONLY", b1, b2), ("Main deposition", b2, b3), ("Post-process", b3, b4)]
    return df, step_summary, cycle_summary, metadata, boundaries, main_cycle_d, o3_cycle_d


def process_plot(df, boundaries, show_cycles, cycle_every, main_cycle_d, o3_cycle_d):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.elapsed_time, y=df.BTorr, mode="lines", name="Measured BTorr", line=dict(color="#17365D", width=1)))
    colors = ["rgba(91,155,213,.14)", "rgba(112,173,71,.13)", "rgba(255,192,0,.13)", "rgba(165,165,165,.14)"]
    base = pd.Timestamp("2000-01-01")
    for (label, start, end), color in zip(boundaries, colors):
        fig.add_vrect(x0=base+pd.Timedelta(seconds=start), x1=base+pd.Timedelta(seconds=end), fillcolor=color, line_width=0, annotation_text=label, annotation_position="top left")
    if show_cycles:
        for label, start, end in boundaries[1:3]:
            duration = o3_cycle_d if label == "NCD_O3_ONLY" else main_cycle_d
            for x in np.arange(start, end + .01, duration * cycle_every):
                fig.add_vline(x=base+pd.Timedelta(seconds=float(x)), line_width=.45, line_dash="dot", line_color="rgba(80,80,80,.35)")
    fig.update_layout(height=560, margin=dict(l=45,r=25,t=55,b=45), hovermode="x unified", legend=dict(orientation="h"),
                      xaxis_title="Process time (hh:mm:ss)", yaxis_title="Measured pressure, BTorr")
    fig.update_xaxes(tickformat="%H:%M:%S", showgrid=True, gridcolor="rgba(0,0,0,.08)")
    fig.update_yaxes(showgrid=True, gridcolor="rgba(0,0,0,.08)")
    return fig


def excel_bytes(df, step_summary, cycle_summary, metadata):
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        pd.DataFrame([metadata]).to_excel(writer, sheet_name="Metadata", index=False)
        step_summary.to_excel(writer, sheet_name="Step_Summary", index=False)
        cycle_summary.to_excel(writer, sheet_name="Cycle_Summary", index=False)
        df.drop(columns=["elapsed_time"]).to_excel(writer, sheet_name="Raw_BTorr", index=False)
        for ws in writer.book.worksheets:
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = cell.font.copy(bold=True, color="FFFFFF")
                cell.fill = __import__('openpyxl').styles.PatternFill("solid", fgColor="17365D")
    return out.getvalue()


def load_private_slopes():
    try:
        config = st.secrets["ald_prediction"]
        values = pd.to_numeric(pd.Series(list(config["cycle_slopes"])), errors="coerce").dropna()
        values = values[values > 0]
        label = str(config.get("model_label", "비공개 오일-cycle 열화 속도"))
        return values, label
    except Exception:
        return pd.Series(dtype=float), ""


def predictor_tab():
    st.subheader("현재 CVG로 남은 O₃ 공정 횟수 추정")
    positive, model_label = load_private_slopes()
    if positive.empty:
        st.warning(
            "비공개 열화 속도 설정이 아직 등록되지 않았습니다. "
            "Streamlit App Settings의 Secrets에 ald_prediction 값을 등록해 주세요."
        )
        with st.expander("관리자용 비공개 설정 형식"):
            st.code(
                '[ald_prediction]\ncycle_slopes = [0.00001, 0.00002, 0.00003]\nmodel_label = "Lab O3 oil cycles"',
                language="toml",
            )
        return

    st.success(f"비공개 예측 모델을 불러왔습니다: {model_label} ({len(positive)} cycles)")
    c1, c2 = st.columns(2)
    current = c1.number_input(
        "현재 idle CVG [Torr]", min_value=0.0, value=0.0050,
        step=0.0001, format="%.5f",
    )
    threshold = c2.number_input(
        "오일 교체 판단 CVG [Torr]", min_value=0.0, value=0.0095,
        step=0.0001, format="%.5f",
    )
    slow = float(positive.quantile(.25))
    typical = float(positive.median())
    fast = float(positive.quantile(.75))

    def remain(slope):
        return max(0, math.floor((threshold - current) / slope)) if slope > 0 else 0

    conservative = remain(fast)
    representative = remain(typical)
    optimistic = remain(slow)
    a, b, c = st.columns(3)
    a.metric("보수적 추정", f"{conservative} 회", help="Q3의 빠른 열화 속도 적용")
    b.metric("대표 추정", f"{representative} 회", help="열화 속도 중앙값 적용")
    c.metric("낙관적 추정", f"{optimistic} 회", help="Q1의 느린 열화 속도 적용")

    if current >= threshold:
        st.error("현재 CVG가 교체 기준 이상입니다. 증착 전 오일 및 장비 상태 확인을 권장합니다.")
    elif conservative <= 1:
        st.warning("보수적 추정상 여유가 1회 이하입니다.")
    elif conservative <= 5:
        st.warning("보수적 추정상 여유가 적습니다. 다음 공정부터 CVG를 집중 확인하세요.")
    else:
        st.success("보수적 추정에서도 여러 회의 공정 여유가 있습니다.")

    left, right = st.columns([1.25, 1])
    with left:
        fig = go.Figure()
        fig.add_trace(go.Box(
            x=positive,
            name="Oil-cycle slopes",
            orientation="h",
            boxpoints="all",
            jitter=0.25,
            pointpos=-1.6,
            marker=dict(size=8, color="#2B6CB0"),
            line=dict(color="#17365D"),
        ))
        fig.add_vline(x=slow, line_dash="dot", line_color="#2CA02C", annotation_text="Q1")
        fig.add_vline(x=typical, line_dash="dash", line_color="#FF7F0E", annotation_text="Median")
        fig.add_vline(x=fast, line_dash="dot", line_color="#D62728", annotation_text="Q3")
        fig.update_layout(
            title="오일 cycle별 CVG 열화 속도 Box Plot",
            xaxis_title="CVG 상승 속도 [Torr/run]",
            yaxis_title="",
            height=390,
            showlegend=False,
            margin=dict(l=30, r=30, t=70, b=50),
        )
        st.plotly_chart(fig, use_container_width=True)
    with right:
        basis = pd.DataFrame({
            "기준": ["Q1 · 낙관적", "중앙값 · 대표", "Q3 · 보수적"],
            "열화 속도 [Torr/run]": [slow, typical, fast],
            "예상 잔여 횟수": [optimistic, representative, conservative],
        })
        st.markdown("#### 계산 기준")
        st.dataframe(
            basis.style.format({"열화 속도 [Torr/run]": "{:.3e}"}),
            use_container_width=True,
            hide_index=True,
        )
        st.latex(r"N_{remain}=\left\lfloor\frac{P_{limit}-P_{current}}{slope}\right\rfloor")
        st.caption("표본 수가 적으므로 장비 상태와 실제 maintenance 기록을 함께 확인해야 합니다.")

def log_tab():
    st.subheader("ALD 공정 로그 자동 정리 및 Step Plot")
    with st.expander("Recipe 시간 설정", expanded=False):
        c1,c2,c3,c4=st.columns(4)
        settings={
            "pre_delay_s":c1.number_input("Pre delay",value=60.0), "pre_flow_s":c2.number_input("Pre flow",value=120.0),
            "o3_pulse_s":c3.number_input("O3 flow pulse",value=50.0), "o3_purge_s":c4.number_input("O3 flow purge",value=10.0),
            "o3_cycles":c1.number_input("O3 flow cycles",value=30,min_value=1), "tma_pulse_s":c2.number_input("TMA pulse",value=.5),
            "tma_purge_s":c3.number_input("Main N2 purge 1",value=20.0), "main_o3_pulse_s":c4.number_input("Main O3 pulse",value=5.0),
            "main_o3_purge_s":c1.number_input("Main N2 purge 2",value=20.0), "main_cycles":c2.number_input("Main cycles",value=101,min_value=1),
            "post_flow_s":c3.number_input("Post flow",value=120.0), "post_delay_s":c4.number_input("Post delay",value=60.0),
            "use_total_layer":st.checkbox("로그의 Total Layer를 main cycle로 사용",value=True), "boundary_tolerance_s":5.0,
        }
    files=st.file_uploader("ALD 공정 로그 TXT 업로드",type=["txt","log"],accept_multiple_files=True)
    if not files:
        st.info("로그를 업로드하면 실제 측정 BTorr를 읽어 step별 그래프와 Excel을 만듭니다.")
        return
    names=[f.name for f in files]; selected=st.selectbox("화면에 표시할 로그",names)
    file=next(f for f in files if f.name==selected)
    try:
        df,ss,cs,meta,bounds,main_d,o3_d=parse_ald_log(file.getvalue(),file.name,settings)
    except Exception as exc:
        st.error(f"로그 해석 실패: {exc}"); return
    c1,c2,c3,c4=st.columns(4)
    c1.metric("실제 공정 시간",str(pd.to_timedelta(meta['actual_duration_s'],unit='s')).split('.')[0])
    c2.metric("Main cycles",str(meta['main_cycles_used']))
    c3.metric("Main absolute min",f"{meta['main_absolute_min_btorr']:.3E} Torr")
    c4.metric("Main lowest 5% mean",f"{meta['main_lowest5pct_mean_btorr']:.3E} Torr")
    if abs(meta['boundary_error_s'])>settings['boundary_tolerance_s']:
        st.warning(f"Recipe 예상 시간과 실제 로그가 {meta['boundary_error_s']:.1f}초 차이납니다. Step 경계를 확인하세요.")
    show=st.checkbox("Cycle 경계 표시",value=False)
    every=st.slider("Cycle 경계 표시 간격",1,20,10,disabled=not show)
    fig=process_plot(df,bounds,show,every,main_d,o3_d); st.plotly_chart(fig,use_container_width=True)
    t1,t2=st.tabs(["Step 요약","Cycle 요약"])
    t1.dataframe(ss,use_container_width=True,hide_index=True); t2.dataframe(cs,use_container_width=True,hide_index=True)
    excel=excel_bytes(df,ss,cs,meta)
    st.download_button("정리된 Excel 다운로드",excel,file_name=f"{Path(file.name).stem}_processed.xlsx",mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.download_button("인터랙티브 Plot HTML 다운로드",fig.to_html(include_plotlyjs=True).encode('utf-8'),file_name=f"{Path(file.name).stem}_plot.html",mime="text/html")


def main():
    st.title("ALD Vacuum Life & Process Log Analyzer")
    st.caption("O₃ 공정 잔여 횟수 예측 · BTorr 공정 로그 자동 step/cycle 정리")
    tab1,tab2=st.tabs(["남은 공정 횟수 예측","공정 로그 자동 Plot"])
    with tab1: predictor_tab()
    with tab2: log_tab()


if __name__ == "__main__":
    main()
