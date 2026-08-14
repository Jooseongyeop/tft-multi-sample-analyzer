from __future__ import annotations

import io
import hmac
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st


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
    return float(x.iloc[: max(1, math.ceil(len(x) * fraction))].mean())


def extract_log_core(raw: bytes) -> tuple[pd.DataFrame, dict]:
    lines = decode_log(raw).splitlines()
    metadata = {"process_name": "", "wafer_id": "", "total_layer": None, "process_start": pd.NaT}
    for line in lines[:100]:
        if line.startswith("Process Name"):
            metadata["process_name"] = line.split(":", 1)[-1].strip()
        elif line.startswith("Wafer ID"):
            metadata["wafer_id"] = line.split(":", 1)[-1].strip()
        elif "Total Layer" in line:
            match = re.search(r"Total Layer\s*:\s*(\d+)", line)
            if match:
                metadata["total_layer"] = int(match.group(1))
        elif "Pre-process Start" in line:
            metadata["process_start"] = parse_timestamp(line.split("\t", 1)[0].strip())

    header_index = None
    for index, line in enumerate(lines):
        if "\tBTorr\t" in line and re.match(r"^\d{4}-\d{2}-\d{2}", line):
            header_index = index
            if pd.isna(metadata["process_start"]):
                metadata["process_start"] = parse_timestamp(line.split("\t", 1)[0].strip())
            break
    if header_index is None or pd.isna(metadata["process_start"]):
        raise ValueError("BTorr 실시간 데이터 헤더 또는 공정 시작 시각을 찾지 못했습니다.")

    records = []
    for line in lines[header_index + 1 :]:
        if not re.match(r"^\d{4}-\d{2}-\d{2}", line):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        timestamp = parse_timestamp(parts[0].strip())
        btorr = pd.to_numeric(parts[1], errors="coerce")
        if pd.notna(timestamp) and pd.notna(btorr):
            records.append((timestamp, float(btorr)))
    if not records:
        raise ValueError("실제 측정 BTorr 행을 찾지 못했습니다.")

    df = pd.DataFrame(records, columns=["timestamp", "BTorr"])
    df["elapsed_s"] = (df.timestamp - metadata["process_start"]).dt.total_seconds()
    df = df[df.elapsed_s >= 0].reset_index(drop=True)
    return df, metadata


def infer_cycle_counts(raw: bytes, settings: dict) -> dict:
    df, header = extract_log_core(raw)
    total_duration = float(df.elapsed_s.max())
    main_cycle_s = sum(settings[key] for key in ("tma_pulse_s", "tma_purge_s", "main_o3_pulse_s", "main_o3_purge_s"))
    o3_cycle_s = settings["o3_pulse_s"] + settings["o3_purge_s"]
    fixed_s = settings["pre_delay_s"] + settings["pre_flow_s"] + settings["post_flow_s"] + settings["post_delay_s"]
    main_cycles = header["total_layer"]
    source = "로그의 Total Layer"
    if main_cycles is None:
        main_cycles = settings["main_cycles"]
        source = "설정값(로그에 Total Layer 없음)"
    residual = total_duration - fixed_s - main_cycles * main_cycle_s
    o3_cycles = max(1, int(round(residual / o3_cycle_s))) if o3_cycle_s > 0 else settings["o3_cycles"]
    expected = fixed_s + o3_cycles * o3_cycle_s + main_cycles * main_cycle_s
    return {
        "o3_cycles": o3_cycles,
        "main_cycles": int(main_cycles),
        "main_source": source,
        "actual_duration_s": total_duration,
        "expected_duration_s": expected,
        "duration_error_s": total_duration - expected,
    }


def analyze_tma_cycles(df: pd.DataFrame, main_start_s: float, main_cycles: int, settings: dict) -> pd.DataFrame:
    cycle_s = sum(settings[key] for key in ("tma_pulse_s", "tma_purge_s", "main_o3_pulse_s", "main_o3_purge_s"))
    baseline_window_s = float(settings.get("baseline_window_s", 3.0))
    rows = []
    for cycle in range(1, main_cycles + 1):
        start = main_start_s + (cycle - 1) * cycle_s
        pulse_end = start + settings["tma_pulse_s"]
        baseline = df[(df.elapsed_s >= start - baseline_window_s) & (df.elapsed_s < start)].BTorr
        pulse = df[(df.elapsed_s >= start) & (df.elapsed_s < pulse_end)].BTorr
        if baseline.empty or pulse.empty:
            continue
        baseline_mean = float(baseline.mean())
        pulse_peak = float(pulse.max())
        peak_time_s = float(df.loc[pulse.idxmax(), "elapsed_s"])
        delta = pulse_peak - baseline_mean
        rows.append({
            "main_cycle": cycle,
            "cycle_start_s": start,
            "baseline_time_s": start,
            "tma_peak_time_s": peak_time_s,
            "baseline_mean_btorr": baseline_mean,
            "tma_peak_btorr": pulse_peak,
            "pressure_delta_btorr": delta,
            "replacement_needed": bool(delta <= settings["tma_delta_limit"]),
        })
    return pd.DataFrame(rows)


def parse_ald_log(raw: bytes, filename: str, settings: dict):
    df, header = extract_log_core(raw)
    inferred = infer_cycle_counts(raw, settings)
    o3_cycles = inferred["o3_cycles"] if settings.get("auto_cycles", True) else int(settings["o3_cycles"])
    main_cycles = inferred["main_cycles"] if settings.get("auto_cycles", True) else int(settings["main_cycles"])

    pre_duration = settings["pre_delay_s"] + settings["pre_flow_s"]
    o3_cycle_s = settings["o3_pulse_s"] + settings["o3_purge_s"]
    main_cycle_s = sum(settings[key] for key in ("tma_pulse_s", "tma_purge_s", "main_o3_pulse_s", "main_o3_purge_s"))
    post_duration = settings["post_flow_s"] + settings["post_delay_s"]
    boundaries_s = [0.0, pre_duration, pre_duration + o3_cycles * o3_cycle_s]
    boundaries_s += [boundaries_s[-1] + main_cycles * main_cycle_s]
    boundaries_s += [boundaries_s[-1] + post_duration]
    b0, b1, b2, b3, b4 = boundaries_s

    elapsed = df.elapsed_s.to_numpy()
    df["major_step"] = np.select(
        [elapsed < b1, elapsed < b2, elapsed < b3, elapsed <= b4 + settings["boundary_tolerance_s"]],
        ["1. Pre-process", "2. NCD_O3_ONLY", "3. Main deposition", "4. Post-process"],
        default="5. Outside expected duration",
    )
    df["cycle_no"] = np.nan
    df["detail_step"] = ""

    o3_mask = (elapsed >= b1) & (elapsed < b2)
    if o3_mask.any():
        phase = (elapsed[o3_mask] - b1) % o3_cycle_s
        df.loc[o3_mask, "cycle_no"] = np.floor((elapsed[o3_mask] - b1) / o3_cycle_s) + 1
        df.loc[o3_mask, "detail_step"] = np.where(phase < settings["o3_pulse_s"], "O3 pulse", "O3 purge")

    main_mask = (elapsed >= b2) & (elapsed < b3)
    if main_mask.any():
        phase = (elapsed[main_mask] - b2) % main_cycle_s
        df.loc[main_mask, "cycle_no"] = np.floor((elapsed[main_mask] - b2) / main_cycle_s) + 1
        a = settings["tma_pulse_s"]
        b = a + settings["tma_purge_s"]
        c = b + settings["main_o3_pulse_s"]
        df.loc[main_mask, "detail_step"] = np.select(
            [phase < a, phase < b, phase < c],
            ["TMA pulse", "N2 purge 1", "O3 pulse"],
            default="N2 purge 2",
        )
    df.loc[elapsed < b1, "detail_step"] = np.where(elapsed[elapsed < b1] < settings["pre_delay_s"], "Pre delay", "Pre flow")
    post_mask = (elapsed >= b3) & (elapsed <= b4 + settings["boundary_tolerance_s"])
    df.loc[post_mask, "detail_step"] = np.where(elapsed[post_mask] - b3 < settings["post_flow_s"], "Post flow", "Post delay")
    df["elapsed_time"] = pd.to_datetime(df.elapsed_s, unit="s", origin="2000-01-01")

    step_summary = df.groupby("major_step", sort=False).agg(
        start_s=("elapsed_s", "min"), end_s=("elapsed_s", "max"), points=("BTorr", "size"),
        min_btorr=("BTorr", "min"), median_btorr=("BTorr", "median"), max_btorr=("BTorr", "max"),
    ).reset_index()
    step_summary["duration_s"] = step_summary.end_s - step_summary.start_s
    robust = df.groupby("major_step", sort=False).BTorr.apply(low_fraction_mean).rename("low5_mean_btorr").reset_index()
    step_summary = step_summary.merge(robust, on="major_step", how="left")

    cycle_rows = df[df.major_step.isin(["2. NCD_O3_ONLY", "3. Main deposition"])].copy()
    cycle_summary = cycle_rows.groupby(["major_step", "cycle_no"], sort=False).agg(
        start_s=("elapsed_s", "min"), end_s=("elapsed_s", "max"), points=("BTorr", "size"),
        min_btorr=("BTorr", "min"), median_btorr=("BTorr", "median"), max_btorr=("BTorr", "max"),
    ).reset_index()
    if not cycle_rows.empty:
        robust_cycle = cycle_rows.groupby(["major_step", "cycle_no"], sort=False).BTorr.apply(low_fraction_mean).rename("low5_mean_btorr").reset_index()
        cycle_summary = cycle_summary.merge(robust_cycle, on=["major_step", "cycle_no"], how="left")

    tma_summary = analyze_tma_cycles(df, b2, main_cycles, settings)
    metadata = {
        "file_name": filename,
        "process_name": header["process_name"],
        "wafer_id": header["wafer_id"],
        "process_start": header["process_start"],
        "actual_end": df.timestamp.max(),
        "actual_duration_s": float(df.elapsed_s.max()),
        "o3_cycles_used": o3_cycles,
        "main_cycles_used": main_cycles,
        "cycle_detection_source": inferred["main_source"],
        "expected_duration_s": b4,
        "boundary_error_s": float(df.elapsed_s.max() - b4),
        "main_absolute_min_btorr": float(df.loc[main_mask, "BTorr"].min()) if main_mask.any() else np.nan,
        "main_lowest5pct_mean_btorr": low_fraction_mean(df.loc[main_mask, "BTorr"]),
        "first_tma_replacement_cycle": int(tma_summary.loc[tma_summary.replacement_needed, "main_cycle"].iloc[0]) if not tma_summary.empty and tma_summary.replacement_needed.any() else None,
    }
    boundaries = [("Pre-process", b0, b1), ("NCD_O3_ONLY", b1, b2), ("Main deposition", b2, b3), ("Post-process", b3, b4)]
    return df, step_summary, cycle_summary, tma_summary, metadata, boundaries, main_cycle_s, o3_cycle_s


def process_plot(df, boundaries, tma_summary, show_cycles, cycle_every, main_cycle_s, o3_cycle_s):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.elapsed_time, y=df.BTorr, mode="lines", name="Measured BTorr", line=dict(color="#17365D", width=1)))
    colors = ["rgba(91,155,213,.14)", "rgba(112,173,71,.13)", "rgba(255,192,0,.13)", "rgba(165,165,165,.14)"]
    base = pd.Timestamp("2000-01-01")
    for (label, start_s, end_s), color in zip(boundaries, colors):
        fig.add_vrect(x0=base + pd.Timedelta(seconds=start_s), x1=base + pd.Timedelta(seconds=end_s), fillcolor=color, line_width=0, annotation_text=label, annotation_position="top left")
    if show_cycles:
        for label, start_s, end_s in boundaries[1:3]:
            duration = o3_cycle_s if label == "NCD_O3_ONLY" else main_cycle_s
            for value in np.arange(start_s, end_s + 0.01, duration * cycle_every):
                fig.add_vline(x=base + pd.Timedelta(seconds=float(value)), line_width=0.45, line_dash="dot", line_color="rgba(80,80,80,.35)")
    if not tma_summary.empty:
        fig.add_trace(go.Scatter(
            x=base + pd.to_timedelta(tma_summary.baseline_time_s, unit="s"),
            y=tma_summary.baseline_mean_btorr,
            mode="lines",
            name="TMA 직전 baseline 평균",
            line=dict(color="#E45756", width=2),
            customdata=tma_summary[["main_cycle"]],
            hovertemplate="Main cycle %{customdata[0]}<br>Baseline=%{y:.5f} Torr<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=base + pd.to_timedelta(tma_summary.tma_peak_time_s, unit="s"),
            y=tma_summary.tma_peak_btorr,
            mode="markers",
            name="TMA pulse peak",
            marker=dict(color="#F2A900", size=7, symbol="circle-open", line=dict(width=2)),
            customdata=tma_summary[["main_cycle", "baseline_mean_btorr", "pressure_delta_btorr"]],
            hovertemplate="Main cycle %{customdata[0]}<br>Peak=%{y:.5f} Torr<br>Baseline=%{customdata[1]:.5f} Torr<br>ΔP=%{customdata[2]:.5f} Torr<extra></extra>",
        ))
        flagged = tma_summary[tma_summary.replacement_needed]
        if not flagged.empty:
            fig.add_trace(go.Scatter(
                x=base + pd.to_timedelta(flagged.tma_peak_time_s, unit="s"),
                y=flagged.tma_peak_btorr,
                mode="markers",
                name="ΔP ≤ 기준 (교체 검토)",
                marker=dict(color="#D62728", size=10, symbol="x"),
                customdata=flagged[["main_cycle", "baseline_mean_btorr", "pressure_delta_btorr"]],
                hovertemplate="Main cycle %{customdata[0]}<br>Peak=%{y:.5f} Torr<br>Baseline=%{customdata[1]:.5f} Torr<br>ΔP=%{customdata[2]:.5f} Torr<extra></extra>",
            ))
    fig.update_layout(height=600, margin=dict(l=45, r=25, t=55, b=90), hovermode="closest", legend=dict(orientation="h", y=-0.18), xaxis_title="Process time (hh:mm:ss)", yaxis_title="Measured pressure, BTorr")
    fig.update_xaxes(tickformat="%H:%M:%S", showgrid=True, gridcolor="rgba(0,0,0,.08)")
    fig.update_yaxes(showgrid=True, gridcolor="rgba(0,0,0,.08)")
    return fig


def tma_peak_baseline_plot(tma_summary: pd.DataFrame):
    fig = go.Figure()
    if not tma_summary.empty:
        fig.add_trace(go.Scatter(x=tma_summary.main_cycle, y=tma_summary.baseline_mean_btorr, mode="lines+markers", name="Baseline 평균", line=dict(color="#E45756")))
        fig.add_trace(go.Scatter(x=tma_summary.main_cycle, y=tma_summary.tma_peak_btorr, mode="lines+markers", name="TMA pulse peak", line=dict(color="#F2A900")))
    fig.update_layout(height=410, xaxis_title="Main cycle", yaxis_title="Pressure [Torr]", margin=dict(l=40, r=25, t=40, b=45), legend=dict(orientation="h"))
    return fig


def tma_delta_plot(tma_summary: pd.DataFrame, limit: float):
    fig = go.Figure()
    if not tma_summary.empty:
        fig.add_trace(go.Scatter(x=tma_summary.main_cycle, y=tma_summary.pressure_delta_btorr, mode="lines+markers", name="Peak − baseline", line=dict(color="#2B6CB0")))
        fig.add_hline(y=limit, line_dash="dash", line_color="#D62728", annotation_text=f"교체 기준 {limit:.3f} Torr")
        flagged = tma_summary[tma_summary.replacement_needed]
        if not flagged.empty:
            fig.add_trace(go.Scatter(x=flagged.main_cycle, y=flagged.pressure_delta_btorr, mode="markers", name="교체 검토", marker=dict(color="#D62728", size=10, symbol="x")))
    fig.update_layout(height=410, xaxis_title="Main cycle", yaxis_title="TMA peak − baseline 평균 [Torr]", margin=dict(l=40, r=25, t=40, b=45))
    return fig

def excel_bytes(df, step_summary, cycle_summary, tma_summary, metadata):
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        pd.DataFrame([metadata]).to_excel(writer, sheet_name="Metadata", index=False)
        step_summary.to_excel(writer, sheet_name="Step_Summary", index=False)
        cycle_summary.to_excel(writer, sheet_name="Cycle_Summary", index=False)
        tma_summary.to_excel(writer, sheet_name="TMA_Pulse_Analysis", index=False)
        df.drop(columns=["elapsed_time"]).to_excel(writer, sheet_name="Raw_BTorr", index=False)
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            for cell in worksheet[1]:
                cell.font = cell.font.copy(bold=True, color="FFFFFF")
                cell.fill = __import__("openpyxl").styles.PatternFill("solid", fgColor="17365D")
    return out.getvalue()


def load_private_slopes():
    try:
        config = st.secrets["ald_prediction"]
        values = pd.to_numeric(pd.Series(list(config["cycle_slopes"])), errors="coerce").dropna()
        return values[values > 0], str(config.get("model_label", "Lab oil-cycle deterioration rates"))
    except Exception:
        return pd.Series(dtype=float), ""


def calculate_conservative_prediction(current, threshold, slopes):
    values = pd.to_numeric(pd.Series(list(slopes)), errors="coerce").dropna()
    values = values[values > 0]
    if values.empty:
        raise ValueError("양수인 열화 속도 데이터가 필요합니다.")
    q1, median, q3 = float(values.quantile(0.25)), float(values.median()), float(values.quantile(0.75))
    margin = max(0.0, float(threshold) - float(current))
    return {"current": float(current), "threshold": float(threshold), "margin": margin, "q1": q1, "median": median, "conservative_slope": q3, "remaining": max(0, math.floor(margin / q3))}


def supabase_config():
    try:
        config = st.secrets["ald_shared_log"]
        url = re.sub(r"/rest/v1/?$", "", str(config["url"]).rstrip("/"))
        return url, str(config["key"]), str(config.get("table", "ald_run_log"))
    except Exception:
        return None


def supabase_request(method: str, path: str, payload=None):
    config = supabase_config()
    if config is None:
        raise RuntimeError("공유 로그 DB가 아직 연결되지 않았습니다.")
    url, key, table = config
    request_url = f"{url}/rest/v1/{table}{path}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(request_url, data=data, headers=headers, method=method), timeout=15) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else []
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"공유 로그 DB 오류 ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"공유 로그 DB 연결 실패: {exc.reason}") from exc


def read_shared_log() -> pd.DataFrame:
    rows = supabase_request("GET", "?select=*&order=process_date.asc,created_at.asc")
    df = pd.DataFrame(rows)
    for column in ("o3_cycles", "main_cycles", "idle_cvg"):
        if column in df:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def add_shared_log(payload: dict):
    return supabase_request("POST", "", payload)


def update_shared_log(record_id: int, payload: dict):
    return supabase_request("PATCH", f"?id=eq.{int(record_id)}", payload)


def delete_shared_log(record_id: int):
    return supabase_request("DELETE", f"?id=eq.{int(record_id)}")


def shared_log_edit_password() -> str:
    try:
        return str(st.secrets["ald_shared_log"].get("edit_password", ""))
    except Exception:
        return ""

def predictor_tab():
    st.subheader("현재 CVG로 남은 O₃ 공정 횟수 추정")
    positive, model_label = load_private_slopes()
    if positive.empty:
        st.warning("비공개 열화 속도가 등록되지 않았습니다. Streamlit Secrets에 ald_prediction을 등록해 주세요.")
        with st.expander("관리자용 Secrets 형식"):
            st.code('[ald_prediction]\ncycle_slopes = [0.00001, 0.00002, 0.00003]\nmodel_label = "Lab O3 oil cycles"', language="toml")
        return
    st.success(f"비공개 예측 모델: {model_label} ({len(positive)} oil cycles)")
    current, threshold = st.columns(2)
    current_value = current.number_input("현재 idle CVG [Torr]", min_value=0.0, value=0.0050, step=0.0001, format="%.5f")
    threshold_value = threshold.number_input("오일 교체 판단 CVG [Torr]", min_value=0.0, value=0.0095, step=0.0001, format="%.5f")
    prediction = calculate_conservative_prediction(current_value, threshold_value, positive)
    st.metric("보수적 예상 잔여 O₃ 공정 횟수", f"{prediction['remaining']} 회")
    if current_value >= threshold_value:
        st.error("현재 CVG가 교체 기준 이상입니다. 증착 전 오일 및 장비 상태 확인을 권장합니다.")
    elif prediction["remaining"] <= 5:
        st.warning("보수적 추정 여유가 적습니다. 다음 공정부터 CVG를 집중 확인하세요.")

    with st.expander("계산 방법과 과거 열화 속도 분포 보기"):
        st.caption("각 pump-oil 사용 주기의 ALD 공정 횟수당 idle CVG 상승 속도(Torr/run)를 구하고, 빠른 열화를 반영하는 Q3(75백분위수)를 적용합니다.")
        basis = pd.DataFrame({"계산 항목": ["현재 idle CVG", "오일 교체 기준", "남은 CVG 여유", "보수적 열화 속도", "예상 잔여 횟수"], "적용값": [f"{prediction['current']:.5f} Torr", f"{prediction['threshold']:.5f} Torr", f"{prediction['margin']:.5f} Torr", f"{prediction['conservative_slope']:.3e} Torr/run (Q3)", f"{prediction['remaining']} 회"]})
        st.dataframe(basis, use_container_width=True, hide_index=True)
        st.latex(r"N_{remain}=\left\lfloor\frac{P_{limit}-P_{current}}{slope_{Q3}}\right\rfloor")
        fig = go.Figure(go.Box(x=positive, name="Oil-cycle slopes", orientation="h", boxpoints="all", jitter=0.25, pointpos=-1.6, marker=dict(size=8, color="#2B6CB0"), line=dict(color="#17365D")))
        fig.add_vline(x=prediction["q1"], line_dash="dot", line_color="#2CA02C", annotation_text="Q1")
        fig.add_vline(x=prediction["median"], line_dash="dash", line_color="#FF7F0E", annotation_text="Median")
        fig.add_vline(x=prediction["conservative_slope"], line_dash="dot", line_color="#D62728", annotation_text="Q3 · Conservative")
        fig.update_layout(height=360, xaxis_title="CVG 상승 속도 [Torr/run]", showlegend=False)
        st.plotly_chart(fig, use_container_width=True)


def shared_log_tab():
    st.subheader("연구실 ALD 공정 공동 로그")
    st.caption("기록은 GitHub가 아닌 비공개 Supabase 테이블에 저장되어 연구실 사용자가 함께 조회합니다.")
    if supabase_config() is None:
        st.warning("공유 DB가 연결되지 않아 아직 저장할 수 없습니다.")
        with st.expander("관리자 설정 방법", expanded=True):
            st.markdown("Supabase SQL Editor에서 아래 SQL을 한 번 실행하고, Streamlit Secrets에 연결 정보를 등록하세요.")
            st.code("""create table if not exists public.ald_run_log (
  id bigint generated by default as identity primary key,
  created_at timestamptz default now(),
  process_date date not null,
  operator text not null,
  o3_cycles integer not null check (o3_cycles >= 0),
  main_cycles integer not null check (main_cycles >= 0),
  idle_cvg double precision not null check (idle_cvg >= 0),
  note text
);
alter table public.ald_run_log enable row level security;
create policy \"lab read\" on public.ald_run_log for select to anon using (true);
create policy \"lab insert\" on public.ald_run_log for insert to anon with check (true);""", language="sql")
            st.code('[ald_shared_log]\nurl = "https://YOUR_PROJECT.supabase.co"\nkey = "YOUR_ANON_KEY"\ntable = "ald_run_log"', language="toml")
        return

    with st.form("ald_shared_log_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        process_date = c1.date_input("공정일", value=date.today())
        operator = c2.text_input("작성자")
        c3, c4, c5 = st.columns(3)
        o3_cycles = c3.number_input("O₃ cycle 횟수", min_value=0, value=0, step=1)
        main_cycles = c4.number_input("Main step 횟수", min_value=0, value=0, step=1)
        idle_cvg = c5.number_input("현재 idle CVG [Torr]", min_value=0.0, value=0.0050, step=0.0001, format="%.5f")
        note = st.text_input("메모(선택)")
        submitted = st.form_submit_button("공정 기록 저장", type="primary")
    if submitted:
        if not operator.strip():
            st.error("작성자를 입력해 주세요.")
        else:
            try:
                add_shared_log({"process_date": str(process_date), "operator": operator.strip(), "o3_cycles": int(o3_cycles), "main_cycles": int(main_cycles), "idle_cvg": float(idle_cvg), "note": note.strip()})
                st.success("공정 기록을 저장했습니다.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    try:
        records = read_shared_log()
    except Exception as exc:
        st.error(str(exc))
        return
    if records.empty:
        st.info("아직 저장된 공정 기록이 없습니다.")
        return
    m1, m2, m3 = st.columns(3)
    m1.metric("누적 O₃ cycles", f"{int(records.o3_cycles.sum()):,}")
    m2.metric("누적 Main cycles", f"{int(records.main_cycles.sum()):,}")
    m3.metric("최근 idle CVG", f"{float(records.idle_cvg.iloc[-1]):.5f} Torr")
    display = records[[column for column in ["process_date", "operator", "o3_cycles", "main_cycles", "idle_cvg", "note", "created_at"] if column in records]].copy()
    display.rename(columns={"process_date": "공정일", "operator": "작성자", "o3_cycles": "O₃ cycles", "main_cycles": "Main cycles", "idle_cvg": "Idle CVG [Torr]", "note": "메모", "created_at": "저장 시각"}, inplace=True)
    st.dataframe(display.sort_index(ascending=False), use_container_width=True, hide_index=True)
    with st.expander("기록 수정 · 삭제", expanded=False):
        configured_password = shared_log_edit_password()
        if not configured_password:
            st.warning("수정·삭제 관리 비밀번호가 아직 설정되지 않았습니다.")
            with st.expander("관리자 초기 설정: UPDATE/DELETE 권한과 비밀번호"):
                st.markdown("Supabase SQL Editor에서 아래 SQL을 한 번 실행하세요.")
                st.code('''drop policy if exists "lab update" on public.ald_run_log;
    drop policy if exists "lab delete" on public.ald_run_log;
    create policy "lab update" on public.ald_run_log
      for update to anon using (true) with check (true);
    create policy "lab delete" on public.ald_run_log
      for delete to anon using (true);''', language="sql")
                st.markdown("그다음 Streamlit Secrets의 `[ald_shared_log]` 아래에 관리 비밀번호를 추가하세요.")
                st.code('edit_password = "연구실에서 사용할 관리 비밀번호"', language="toml")
        else:
            records_for_edit = records.sort_values(["process_date", "created_at"], ascending=False).copy()
            labels = {
                int(row.id): f"#{int(row.id)} · {row.process_date} · {row.operator} · O₃ {int(row.o3_cycles)} / Main {int(row.main_cycles)}"
                for row in records_for_edit.itertuples()
            }
            selected_id = st.selectbox(
                "수정하거나 삭제할 기록",
                options=list(labels),
                format_func=lambda value: labels[value],
            )
            selected_row = records_for_edit.loc[records_for_edit.id == selected_id].iloc[0]
            with st.form("ald_shared_log_edit_form"):
                e1, e2 = st.columns(2)
                edit_date = e1.date_input("공정일 수정", value=pd.to_datetime(selected_row.process_date).date())
                edit_operator = e2.text_input("작성자 수정", value=str(selected_row.operator))
                e3, e4, e5 = st.columns(3)
                edit_o3 = e3.number_input("O₃ cycle 횟수 수정", min_value=0, value=int(selected_row.o3_cycles), step=1)
                edit_main = e4.number_input("Main step 횟수 수정", min_value=0, value=int(selected_row.main_cycles), step=1)
                edit_cvg = e5.number_input("Idle CVG 수정 [Torr]", min_value=0.0, value=float(selected_row.idle_cvg), step=0.0001, format="%.5f")
                existing_note = "" if pd.isna(selected_row.get("note")) else str(selected_row.get("note"))
                edit_note = st.text_input("메모 수정", value=existing_note)
                edit_password = st.text_input("관리 비밀번호", type="password")
                update_submitted = st.form_submit_button("선택 기록 수정")
            if update_submitted:
                if not hmac.compare_digest(edit_password, configured_password):
                    st.error("관리 비밀번호가 올바르지 않습니다.")
                elif not edit_operator.strip():
                    st.error("작성자를 입력해 주세요.")
                else:
                    try:
                        update_shared_log(selected_id, {
                            "process_date": str(edit_date), "operator": edit_operator.strip(),
                            "o3_cycles": int(edit_o3), "main_cycles": int(edit_main),
                            "idle_cvg": float(edit_cvg), "note": edit_note.strip(),
                        })
                        st.success("선택한 기록을 수정했습니다.")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

            with st.expander("선택 기록 삭제", expanded=False):
                st.warning("삭제한 기록은 앱에서 복구할 수 없습니다.")
                delete_confirmed = st.checkbox("선택한 기록을 영구 삭제하겠습니다.", key=f"delete_confirm_{selected_id}")
                delete_password = st.text_input("삭제 관리 비밀번호", type="password", key=f"delete_password_{selected_id}")
                if st.button("선택 기록 삭제", type="secondary", disabled=not delete_confirmed):
                    if not hmac.compare_digest(delete_password, configured_password):
                        st.error("관리 비밀번호가 올바르지 않습니다.")
                    else:
                        try:
                            delete_shared_log(selected_id)
                            st.success("선택한 기록을 삭제했습니다.")
                            st.rerun()
                        except Exception as exc:
                            st.error(str(exc))

            with st.expander("Supabase 수정·삭제 권한 SQL 보기"):
                st.code('''drop policy if exists "lab update" on public.ald_run_log;
    drop policy if exists "lab delete" on public.ald_run_log;
    create policy "lab update" on public.ald_run_log
      for update to anon using (true) with check (true);
    create policy "lab delete" on public.ald_run_log
      for delete to anon using (true);''', language="sql")
    st.download_button("공동 로그 CSV 다운로드", records.to_csv(index=False).encode("utf-8-sig"), "ald_shared_log.csv", "text/csv")


def recipe_settings_panel(defaults: dict) -> dict:
    with st.expander("Recipe 시간 설정", expanded=False):
        auto_cycles = st.checkbox("로그 업로드 시 O₃/Main cycle 수 자동 계산", value=True)
        c1, c2, c3, c4 = st.columns(4)
        values = {
            "pre_delay_s": c1.number_input("Pre delay", value=float(defaults["pre_delay_s"])),
            "pre_flow_s": c2.number_input("Pre flow", value=float(defaults["pre_flow_s"])),
            "o3_pulse_s": c3.number_input("O3 flow pulse", value=float(defaults["o3_pulse_s"])),
            "o3_purge_s": c4.number_input("O3 flow purge", value=float(defaults["o3_purge_s"])),
            "o3_cycles": c1.number_input("O3 flow cycles", value=int(defaults["o3_cycles"]), min_value=1, disabled=auto_cycles),
            "tma_pulse_s": c2.number_input("TMA pulse", value=float(defaults["tma_pulse_s"])),
            "tma_purge_s": c3.number_input("Main N2 purge 1", value=float(defaults["tma_purge_s"])),
            "main_o3_pulse_s": c4.number_input("Main O3 pulse", value=float(defaults["main_o3_pulse_s"])),
            "main_o3_purge_s": c1.number_input("Main N2 purge 2", value=float(defaults["main_o3_purge_s"])),
            "main_cycles": c2.number_input("Main cycles", value=int(defaults["main_cycles"]), min_value=1, disabled=auto_cycles),
            "post_flow_s": c3.number_input("Post flow", value=float(defaults["post_flow_s"])),
            "post_delay_s": c4.number_input("Post delay", value=float(defaults["post_delay_s"])),
            "baseline_window_s": c1.number_input("TMA baseline 평균 구간 [s]", value=float(defaults["baseline_window_s"]), min_value=0.1),
            "tma_delta_limit": c2.number_input("TMA 교체 기준 ΔP [Torr]", value=float(defaults["tma_delta_limit"]), min_value=0.0, step=0.001, format="%.3f"),
            "auto_cycles": auto_cycles,
            "boundary_tolerance_s": 5.0,
        }
    return values


def log_tab():
    st.subheader("ALD 공정 로그 자동 정리 · Step Plot")
    files = st.file_uploader("ALD 공정 로그 TXT 업로드", type=["txt", "log"], accept_multiple_files=True)
    defaults = {"pre_delay_s": 60.0, "pre_flow_s": 120.0, "o3_pulse_s": 50.0, "o3_purge_s": 10.0, "o3_cycles": 30, "tma_pulse_s": 0.5, "tma_purge_s": 20.0, "main_o3_pulse_s": 5.0, "main_o3_purge_s": 20.0, "main_cycles": 101, "post_flow_s": 120.0, "post_delay_s": 60.0, "baseline_window_s": 3.0, "tma_delta_limit": 0.01}
    settings = recipe_settings_panel(defaults)
    if not files:
        st.info("로그를 업로드하면 O₃/Main cycle을 자동 계산하고, TMA pulse 응답과 교체 필요 구간을 분석합니다.")
        return

    names = [file.name for file in files]
    selected = st.selectbox("화면에 표시할 로그", names)
    selected_file = next(file for file in files if file.name == selected)
    try:
        df, step_summary, cycle_summary, tma_summary, metadata, boundaries, main_cycle_s, o3_cycle_s = parse_ald_log(selected_file.getvalue(), selected_file.name, settings)
    except Exception as exc:
        st.error(f"로그 해석 실패: {exc}")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("자동 O₃ cycles", str(metadata["o3_cycles_used"]))
    c2.metric("자동 Main cycles", str(metadata["main_cycles_used"]))
    c3.metric("TMA ΔP 평균", f"{tma_summary.pressure_delta_btorr.mean():.5f} Torr" if not tma_summary.empty else "N/A")
    first_bad = metadata["first_tma_replacement_cycle"]
    c4.metric("TMA 교체 필요 시작", f"Main {first_bad} cycle" if first_bad else "감지 안 됨")
    st.caption(f"Cycle 자동 계산 근거: {metadata['cycle_detection_source']} · 예상/실제 시간 차이 {metadata['boundary_error_s']:.1f} s")

    show_cycles = st.checkbox("Cycle 경계 표시", value=False)
    cycle_every = st.slider("Cycle 경계 표시 간격", 1, 20, 10, disabled=not show_cycles)
    process_figure = process_plot(df, boundaries, tma_summary, show_cycles, cycle_every, main_cycle_s, o3_cycle_s)
    st.plotly_chart(process_figure, use_container_width=True)
    st.markdown("#### TMA pulse 응답 분석")
    st.caption("각 Main cycle에서 빨간 baseline은 TMA pulse 직전 설정 구간의 평균 압력이고, 주황색 마커는 해당 TMA pulse 구간의 실제 최댓값입니다. ΔP = peak − baseline이며, ΔP ≤ 0.01 Torr는 교체 검토 구간으로 표시합니다.")
    comparison_col, delta_col = st.columns(2)
    with comparison_col:
        st.plotly_chart(tma_peak_baseline_plot(tma_summary), use_container_width=True)
    with delta_col:
        st.plotly_chart(tma_delta_plot(tma_summary, settings["tma_delta_limit"]), use_container_width=True)

    tabs = st.tabs(["Step 요약", "Cycle 요약", "TMA pulse 분석", "업로드 로그 전체 TMA 수명"])
    tabs[0].dataframe(step_summary, use_container_width=True, hide_index=True)
    tabs[1].dataframe(cycle_summary, use_container_width=True, hide_index=True)
    tabs[2].dataframe(tma_summary, use_container_width=True, hide_index=True)

    history_rows = []
    cumulative = 0
    for file in files:
        try:
            _, _, _, summary, meta, _, _, _ = parse_ald_log(file.getvalue(), file.name, settings)
            first_failure = int(summary.loc[summary.replacement_needed, "main_cycle"].iloc[0]) if not summary.empty and summary.replacement_needed.any() else None
            history_rows.append({"process_start": meta["process_start"], "file": file.name, "main_cycles": meta["main_cycles_used"], "first_failure_in_file": first_failure})
        except Exception:
            continue
    history = pd.DataFrame(history_rows)
    if not history.empty:
        history = history.sort_values("process_start").reset_index(drop=True)
        history["cumulative_before"] = history.main_cycles.cumsum().shift(fill_value=0)
        history["estimated_replacement_total_pulses"] = history.apply(lambda row: int(row.cumulative_before + row.first_failure_in_file) if pd.notna(row.first_failure_in_file) else np.nan, axis=1)
        estimated = history.estimated_replacement_total_pulses.dropna()
        if not estimated.empty:
            tabs[3].metric("업로드 이력 기준 TMA 교체 예상 누적 pulse", f"{int(estimated.iloc[0]):,} pulses")
            tabs[3].caption("업로드한 로그를 공정 시작 시각순으로 정렬한 뒤, 최초 ΔP ≤ 기준이 나타난 Main cycle까지의 TMA pulse를 누적했습니다.")
        else:
            tabs[3].info("업로드한 로그에서 TMA 교체 기준 이하 구간이 감지되지 않았습니다.")
        tabs[3].dataframe(history, use_container_width=True, hide_index=True)

    workbook = excel_bytes(df, step_summary, cycle_summary, tma_summary, metadata)
    st.download_button("정리된 Excel 다운로드", workbook, file_name=f"{Path(selected_file.name).stem}_processed.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.download_button("인터랙티브 Plot HTML 다운로드", process_figure.to_html(include_plotlyjs=True).encode("utf-8"), file_name=f"{Path(selected_file.name).stem}_plot.html", mime="text/html")


def main():
    st.title("ALD Vacuum Life & Process Log Analyzer")
    st.caption("O₃ 공정 잔여 횟수 예측 · BTorr 로그 자동 cycle 정리 · TMA 공급 상태 분석")
    predictor, shared, log = st.tabs(["남은 공정 횟수 예측", "공동 공정 로그", "공정 로그 자동 Plot"])
    with predictor:
        predictor_tab()
    with shared:
        shared_log_tab()
    with log:
        log_tab()


if __name__ == "__main__":
    main()