from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st


BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / "top_gate_reliability_template.opju"
WORKER_PATH = BASE_DIR / "origin2020_worker_v5.py"

# File names contain interval/measurement times. Origin displays accumulated times.
INITIAL_FILE_TIMES = (0.0, 1.0)
FOLLOWUP_FILE_TIMES = [100.0, 400.0, 500.0, 800.0, 1800.0]
FILE_TIMES = [*INITIAL_FILE_TIMES, *FOLLOWUP_FILE_TIMES]
PLOT_TIME = {
    0.0: 1.0,
    1.0: 1.0,
    100.0: 100.0,
    400.0: 500.0,
    500.0: 1000.0,
    800.0: 1800.0,
    1800.0: 3600.0,
}
TIME_PATTERN = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*s\b", re.IGNORECASE)
TYPE_PATTERN = re.compile(r"(?<![A-Z])(NBTS|PBTS)(?![A-Z])", re.IGNORECASE)


def read_measurement(uploaded) -> pd.DataFrame:
    raw = uploaded.getvalue()
    last_error = None
    for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr", "latin1"):
        try:
            df = pd.read_csv(io.BytesIO(raw), encoding=encoding)
            if df.shape[1] >= 2:
                return df
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"파일을 읽을 수 없습니다: {last_error}")


def identify_file(filename: str) -> tuple[str, float]:
    type_match = TYPE_PATTERN.search(filename)
    if not type_match:
        raise ValueError("파일명에서 NBTS/PBTS를 찾지 못했습니다.")
    time_match = TIME_PATTERN.search(filename)
    if not time_match:
        raise ValueError("파일명에서 측정 시간(예: 400.00s)을 찾지 못했습니다.")
    measured = float(time_match.group(1))
    matches = [t for t in FILE_TIMES if np.isclose(measured, t)]
    if not matches:
        raise ValueError(f"지원하지 않는 측정 시간입니다: {measured:g} s")
    return type_match.group(1).upper(), matches[0]


def ordered_file_times(curves: dict[float, pd.DataFrame]) -> list[float]:
    """Use either a 0 s or 1 s file as the first reliability measurement."""
    initial = next((time for time in INITIAL_FILE_TIMES if time in curves), None)
    if initial is None:
        raise ValueError("The first reliability file must be labeled 0 s or 1 s.")
    return [initial, *FOLLOWUP_FILE_TIMES]


def pick_column(columns: Iterable[str], keywords: tuple[str, ...]) -> str | None:
    normalized = {
        col: re.sub(r"[^a-z0-9]", "", str(col).lower()) for col in columns
    }
    for keyword in keywords:
        key = re.sub(r"[^a-z0-9]", "", keyword.lower())
        for original, value in normalized.items():
            if key == value or key in value:
                return original
    return None


def constant_current_vth(vg, current, target: float) -> float:
    vg = np.asarray(vg, dtype=float)
    current = np.abs(np.asarray(current, dtype=float))
    valid = np.isfinite(vg) & np.isfinite(current) & (current > 0)
    vg, current = vg[valid], current[valid]
    if len(vg) < 2:
        raise ValueError("유효한 Vg/Id 데이터가 부족합니다.")
    order = np.argsort(vg)
    vg, current = vg[order], current[order]
    log_i, log_target = np.log10(current), np.log10(target)
    crossing = np.where(
        (log_i[:-1] - log_target) * (log_i[1:] - log_target) <= 0
    )[0]
    crossing = [i for i in crossing if log_i[i + 1] != log_i[i]]
    if not crossing:
        raise ValueError(f"|Id|={target:.2e} A 교차점을 찾지 못했습니다.")
    i = crossing[0]
    return float(
        vg[i]
        + (log_target - log_i[i])
        * (vg[i + 1] - vg[i])
        / (log_i[i + 1] - log_i[i])
    )


def build_wide(curves: dict[float, pd.DataFrame]) -> pd.DataFrame:
    wide = None
    for file_time in ordered_file_times(curves):
        accumulated = int(PLOT_TIME[file_time])
        part = curves[file_time][["Vg", "Id"]].copy()
        part = part.apply(pd.to_numeric, errors="coerce").dropna(subset=["Vg"])
        part = part.drop_duplicates("Vg", keep="first")
        part = part.rename(columns={"Id": f"Id_{accumulated}s"})
        wide = part if wide is None else wide.merge(part, on="Vg", how="outer")
    return wide.sort_values("Vg").reset_index(drop=True)


def origin_table(wide: pd.DataFrame, result: pd.DataFrame) -> pd.DataFrame:
    table = wide.copy()
    delta = result[["Accumulated Time (s)", "Delta Vth vs 1 s (V)"]].reset_index(
        drop=True
    )
    table["Stress_Time_s"] = pd.Series(delta["Accumulated Time (s)"])
    table["Delta_Vth_V"] = pd.Series(delta["Delta Vth vs 1 s (V)"])
    return table


def excel_bytes(all_results: pd.DataFrame, wides: dict[str, pd.DataFrame]) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        all_results.to_excel(writer, sheet_name="Vth_Result", index=False)
        for condition, wide in wides.items():
            wide.to_excel(writer, sheet_name=f"{condition}_Transfer_XY", index=False)
    return output.getvalue()


def run_origin(
    tables: dict[str, pd.DataFrame], output_dir: Path, sample_name: str
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tft_origin_v5_") as temp_dir:
        temp = Path(temp_dir)
        result_json = temp / "result.json"
        safe = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", sample_name).strip("_")
        safe = safe or "TFT_reliability"
        command = [
            sys.executable,
            str(WORKER_PATH),
            "--template",
            str(TEMPLATE_PATH),
            "--output-opju",
            str(output_dir / f"{safe}_Origin.opju"),
            "--export-dir",
            str(output_dir),
            "--figure-stem",
            safe,
            "--result-json",
            str(result_json),
        ]
        for condition in ("NBTS", "PBTS"):
            if condition in tables:
                csv_path = temp / f"{condition}.csv"
                tables[condition].to_csv(csv_path, index=False, encoding="utf-8-sig")
                command += [f"--{condition.lower()}-csv", str(csv_path)]
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=150
        )
        if result_json.exists():
            result = json.loads(result_json.read_text(encoding="utf-8"))
        else:
            result = {
                "ok": False,
                "error": completed.stderr or completed.stdout or "Origin worker failed",
            }
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "Origin 자동화 실패"))
        return result


st.title("TFT 신뢰성 Vth + OriginPro 2020 자동 플롯 v5")
st.caption(
    "NBTS/PBTS 동시 분석 · 파일 측정시간을 누적시간 "
    "1/100/500/1000/1800/3600 s로 변환"
)

with st.sidebar:
    st.header("분석 조건")
    target_current = st.number_input(
        "Vth 기준 |Id| (A)",
        min_value=1e-15,
        max_value=1e-2,
        value=1.2e-7,
        step=1e-8,
        format="%.2e",
    )
    st.markdown(
        "**시간 변환**  \n"
        "0→1, 100→100, 400→500, 500→1000, 800→1800, 1800→3600 s"
    )

files = st.file_uploader(
    "NBTS/PBTS 파일을 함께 업로드하세요(조건별 6개, 최대 12개)",
    accept_multiple_files=True,
)
if not files:
    st.info("파일명에 NBTS 또는 PBTS와 0.00s 같은 측정 시간이 필요합니다.")
    st.stop()

curves: dict[str, dict[float, pd.DataFrame]] = {"NBTS": {}, "PBTS": {}}
records, errors = [], []
for uploaded in files:
    try:
        condition, file_time = identify_file(uploaded.name)
        df = read_measurement(uploaded)
        vg_col = pick_column(df.columns, ("gateVoltage", "Vg"))
        id_col = pick_column(df.columns, ("drainCurrent", "Id"))
        if vg_col is None or id_col is None:
            raise ValueError("gateVoltage 또는 drainCurrent 열을 찾지 못했습니다.")
        vg = pd.to_numeric(df[vg_col], errors="coerce").to_numpy()
        drain = pd.to_numeric(df[id_col], errors="coerce").to_numpy()
        curves[condition][file_time] = pd.DataFrame({"Vg": vg, "Id": drain})
        records.append(
            {
                "Condition": condition,
                "File Time (s)": file_time,
                "Accumulated Time (s)": PLOT_TIME[file_time],
                "Vth (V)": constant_current_vth(vg, drain, target_current),
                "File": uploaded.name,
            }
        )
    except Exception as exc:
        errors.append(f"{uploaded.name}: {exc}")
for error in errors:
    st.warning(error)

complete_conditions = []
for condition in ("NBTS", "PBTS"):
    if not curves[condition]:
        continue
    missing = [t for t in FOLLOWUP_FILE_TIMES if t not in curves[condition]]
    if not any(t in curves[condition] for t in INITIAL_FILE_TIMES):
        missing.insert(0, "0 s or 1")
    if missing:
        st.warning(
            f"{condition} 누락 파일 시간: "
            + ", ".join(
                f"{time:g} s" if isinstance(time, (int, float)) else f"{time} s"
                for time in missing
            )
        )
    else:
        complete_conditions.append(condition)
if not complete_conditions:
    st.error("NBTS 또는 PBTS 중 한 조건의 파일 6개가 모두 필요합니다.")
    st.stop()

all_results, wides, tables = [], {}, {}
for condition in complete_conditions:
    result = (
        pd.DataFrame([row for row in records if row["Condition"] == condition])
        .drop_duplicates("File Time (s)", keep="last")
        .sort_values("Accumulated Time (s)")
        .reset_index(drop=True)
    )
    baseline = float(
        result.loc[np.isclose(result["Accumulated Time (s)"], 1), "Vth (V)"].iloc[0]
    )
    result["Delta Vth vs 1 s (V)"] = result["Vth (V)"] - baseline
    wide = build_wide(curves[condition])
    all_results.append(result)
    wides[condition] = wide
    tables[condition] = origin_table(wide, result)

combined = pd.concat(all_results, ignore_index=True)
st.subheader("Vth 및 누적시간 기준 ΔVth")
st.dataframe(combined, use_container_width=True)

tabs = st.tabs(complete_conditions)
for tab, condition in zip(tabs, complete_conditions):
    with tab:
        st.dataframe(wides[condition], use_container_width=True, height=300)
        left, right = st.columns(2)
        result = next(df for df in all_results if df.iloc[0]["Condition"] == condition)
        with left:
            fig, ax = plt.subplots(figsize=(7, 5))
            for file_time in ordered_file_times(curves[condition]):
                data = curves[condition][file_time].dropna().sort_values("Vg")
                ax.plot(
                    data["Vg"],
                    np.abs(data["Id"]),
                    label=f"{int(PLOT_TIME[file_time])} s",
                )
            ax.axhline(target_current, color="gray", linestyle="--")
            ax.set_yscale("log")
            ax.set_xlabel("Gate Voltage (V)")
            ax.set_ylabel("Drain Current (A)")
            ax.set_title(condition)
            ax.legend()
            ax.grid(alpha=0.25, which="both")
            st.pyplot(fig)
            plt.close(fig)
        with right:
            fig, ax = plt.subplots(figsize=(7, 5))
            ax.plot(
                result["Accumulated Time (s)"],
                result["Delta Vth vs 1 s (V)"],
                marker="o",
            )
            ax.set_xlabel("Accumulated Stress Time (s)")
            ax.set_ylabel("ΔVth (V)")
            ax.set_title(f"{condition} ΔVth")
            ax.grid(alpha=0.3)
            st.pyplot(fig)
            plt.close(fig)

st.download_button(
    "NBTS/PBTS 통합 결과 Excel 다운로드",
    excel_bytes(combined, wides),
    "TFT_reliability_NBTS_PBTS_result.xlsx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

st.divider()
st.subheader("OriginPro 2020 자동 플롯 안내")
st.info(
    "OriginPro 2020 자동 플롯은 Origin이 설치된 Windows PC에서만 실행할 수 있습니다. "
    "공개 웹사이트에서는 Vth·ΔVth 분석과 Excel 다운로드까지만 제공하며, "
    "Origin 템플릿은 연구실 파일 보호를 위해 저장소에 포함하지 않습니다."
)