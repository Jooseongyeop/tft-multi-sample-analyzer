import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st
from openpyxl.styles import Font


APP_VARIANT = "4F"
APP_TITLE = "TFT Multi-Sample Analyzer - 4F Probe"
PREFER_B1500 = False
PRESENTATION_SIGNIFICANT_DIGITS = 5


@dataclass
class Settings:
    w_over_l: float
    tox_nm: float
    eps_r: float
    vth_current: float
    ss_min: float
    ss_max: float
    vd_tolerance: float


def normalized(text):
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


def sample_name(filename):
    return Path(filename).stem.strip() or "sample"


def unique_names(files):
    seen = {}
    names = []
    for uploaded in files:
        base = sample_name(uploaded.name)
        seen[base] = seen.get(base, 0) + 1
        names.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return names


def decode_text(raw):
    for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr", "latin1"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    raise ValueError("Unable to detect text encoding.")


def parse_b1500_dataname(rows):
    header = None
    records = []
    for row in rows:
        if not row:
            continue
        marker = row[0].strip()
        if marker == "DataName":
            header = [cell.strip() for cell in row[1:]]
        elif marker == "DataValue" and header:
            values = row[1:1 + len(header)]
            values += [""] * (len(header) - len(values))
            records.append(values)
    if not header or not records:
        raise ValueError("B1500 DataName/DataValue measurement rows were not found.")
    return pd.DataFrame(records, columns=header)


def parse_b1500_classic(rows):
    """Parse B1500 Classic CSVs with metadata followed by a VG/ID table."""
    vg_aliases = {"vg", "vgs", "gatevoltage", "gatevoltagev"}
    id_aliases = {"id", "ids", "draincurrent", "draincurrenta"}
    for line_number, row in enumerate(rows, start=1):
        header = [cell.strip() for cell in row]
        norms = [normalized(cell) for cell in header]
        vg_positions = [i for i, value in enumerate(norms) if value in vg_aliases]
        id_positions = [i for i, value in enumerate(norms) if value in id_aliases]
        if not vg_positions or not id_positions:
            continue

        vg_index, id_index = vg_positions[0], id_positions[0]
        records = []
        for data_row in rows[line_number:]:
            if len(data_row) < len(header):
                continue
            values = [cell.strip() for cell in data_row[:len(header)]]
            try:
                float(values[vg_index])
                float(values[id_index])
            except (TypeError, ValueError):
                continue
            records.append(values)
        if records:
            return pd.DataFrame(records, columns=header), line_number
    raise ValueError("B1500 VG/ID measurement table was not found.")


def parse_b1500(raw):
    text, encoding = decode_text(raw)
    rows = list(csv.reader(io.StringIO(text)))
    try:
        frame = parse_b1500_dataname(rows)
        return frame, f"B1500 DataName/DataValue ({encoding})"
    except ValueError as dataname_error:
        try:
            frame, line_number = parse_b1500_classic(rows)
            return frame, f"B1500 Classic table ({encoding}, header line {line_number})"
        except ValueError as classic_error:
            raise ValueError(f"{dataname_error} {classic_error}") from classic_error

def parse_standard(raw, filename):
    if filename.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(raw)), "Excel"
    text, encoding = decode_text(raw)
    attempts = (
        {"sep": None, "engine": "python"},
        {"sep": ","},
        {"sep": "\t"},
    )
    last_error = None
    for options in attempts:
        try:
            frame = pd.read_csv(io.StringIO(text), **options)
            if frame.shape[1] >= 2:
                return frame, f"table ({encoding})"
        except Exception as exc:
            last_error = exc
    raise ValueError(f"Unable to read as a table: {last_error}")


def parse_uploaded(uploaded):
    raw = uploaded.getvalue()
    if uploaded.name.lower().endswith((".xlsx", ".xls")):
        return parse_standard(raw, uploaded.name)
    if PREFER_B1500:
        try:
            return parse_b1500(raw)
        except Exception:
            return parse_standard(raw, uploaded.name)
    try:
        return parse_standard(raw, uploaded.name)
    except Exception:
        return parse_b1500(raw)


ALIASES = {
    "vg": ("gatevoltagev", "gatevoltage", "vg", "vgs"),
    "id": ("draincurrenta", "draincurrent", "id", "ids"),
    "ig": ("gatecurrenta", "gatecurrent", "ig"),
    "vd": ("drainvoltagev", "drainvoltage", "vd", "vds"),
}


def find_column(columns, kind, required=True):
    norms = {column: normalized(column) for column in columns}
    for alias in ALIASES[kind]:
        for column, norm in norms.items():
            if norm == alias:
                return column
    for alias in ALIASES[kind]:
        for column, norm in norms.items():
            if alias in norm:
                return column
    if required:
        raise ValueError(f"Could not detect {kind.upper()} column. Columns: {list(columns)}")
    return None


def numeric_frame(frame):
    result = pd.DataFrame()
    vg_col = find_column(frame.columns, "vg")
    id_col = find_column(frame.columns, "id")
    ig_col = find_column(frame.columns, "ig", required=False)
    vd_col = find_column(frame.columns, "vd", required=False)
    result["Vg"] = pd.to_numeric(frame[vg_col], errors="coerce")
    result["Id"] = pd.to_numeric(frame[id_col], errors="coerce")
    result["Ig"] = (
        pd.to_numeric(frame[ig_col], errors="coerce").abs() if ig_col else np.nan
    )
    result["Vd"] = pd.to_numeric(frame[vd_col], errors="coerce") if vd_col else np.nan
    return result.replace([np.inf, -np.inf], np.nan).dropna(subset=["Vg", "Id"]).reset_index(drop=True)


def format_significant(value, digits=PRESENTATION_SIGNIFICANT_DIGITS):
    """Format a calculated value for copy/paste while preserving significant zeros."""
    if value is None or pd.isna(value):
        return ""
    return f"{float(value):#.{digits}g}"


def ppt_summary_table(summary):
    """Return FEM, Vth, and SS as five-significant-figure strings for PPT."""
    columns = {
        "Mobility max [cm2/Vs]": "FEM [cm2/Vs]",
        "Vth [V]": "Vth [V]",
        "SS [mV/dec]": "SS [mV/dec]",
    }
    if summary.empty:
        return pd.DataFrame(columns=["Sample", *columns.values()])
    result = summary[["Sample", *columns]].rename(columns=columns).copy()
    for column in columns.values():
        result[column] = result[column].map(format_significant)
    return result


def copy_safe_summary_table(summary):
    """Format PPT metrics as strings so clipboard data also has five figures."""
    result = summary.copy()
    for column in (
        "Mobility max [cm2/Vs]",
        "Vth [V]",
        "SS [mV/dec]",
    ):
        if column in result:
            result[column] = result[column].map(format_significant)
    return result


def split_vg_segments(frame):
    if len(frame) < 3:
        return [frame.copy()]
    vg = frame["Vg"].to_numpy(float)
    finite_steps = np.abs(np.diff(vg))
    typical = np.nanmedian(finite_steps[finite_steps > 0]) if np.any(finite_steps > 0) else 0.02
    jumps = np.where(np.diff(vg) < -max(0.5, typical * 1.5))[0] + 1
    cuts = [0, *jumps.tolist(), len(frame)]
    return [frame.iloc[cuts[i]:cuts[i + 1]].reset_index(drop=True) for i in range(len(cuts) - 1) if cuts[i + 1] - cuts[i] >= 3]


def choose_first_sweep(frame):
    segments = split_vg_segments(frame)
    return segments[0] if segments else frame


def curves_by_vd(frame, tolerance):
    curves = {}
    if frame["Vd"].notna().any():
        vd = frame["Vd"].to_numpy(float)
        for target in (0.1, 1.0):
            mask = np.isfinite(vd) & (np.abs(vd - target) <= tolerance)
            if np.any(mask):
                curves[target] = choose_first_sweep(frame.loc[mask].reset_index(drop=True))
    else:
        segments = split_vg_segments(frame)
        if segments:
            curves[0.1] = segments[0]
        if len(segments) > 1:
            curves[1.0] = segments[1]
    return curves


def right_side_full_range_mask(vg, current, lower, upper):
    base = np.isfinite(vg) & np.isfinite(current) & (current >= lower) & (current < upper) & (current > 0)
    idx = np.where(base)[0]
    output = np.zeros(len(vg), dtype=bool)
    if len(idx) == 0:
        return output
    ordered = idx[np.argsort(vg[idx])]
    x = vg[ordered]
    unique_x = np.sort(np.unique(vg[np.isfinite(vg)]))
    steps = np.diff(unique_x)
    typical = np.nanmedian(steps[steps > 0]) if np.any(steps > 0) else 0.02
    groups = np.split(ordered, np.where(np.diff(x) > max(typical * 3.0, 0.08))[0] + 1)
    selected = max(groups, key=lambda group: np.nanmedian(vg[group]))
    output[selected] = True
    return output


def interpolate_vth(vg, abs_id, target):
    valid = np.isfinite(vg) & np.isfinite(abs_id) & (abs_id > 0)
    x = vg[valid]
    y = np.log10(abs_id[valid])
    target_log = np.log10(target)
    crossings = []
    for i in range(len(x) - 1):
        if (y[i] - target_log) * (y[i + 1] - target_log) <= 0 and y[i] != y[i + 1]:
            value = x[i] + (target_log - y[i]) * (x[i + 1] - x[i]) / (y[i + 1] - y[i])
            crossings.append(value)
    return max(crossings) if crossings else np.nan


def analyze_curve(curve, settings, vds=0.1):
    data = curve[["Vg", "Id", "Ig"]].copy().dropna(subset=["Vg", "Id"])
    data = data.sort_values("Vg").drop_duplicates("Vg", keep="first").reset_index(drop=True)
    if len(data) < 3:
        raise ValueError("Fewer than 3 valid Vg/Id data points.")
    vg = data["Vg"].to_numpy(float)
    ids = data["Id"].to_numpy(float)
    abs_id = np.abs(ids)
    gm = np.gradient(ids, vg)
    cox = settings.eps_r * 8.8541878128e-12 / (settings.tox_nm * 1e-9)
    mobility = gm / (settings.w_over_l * cox * vds) * 1e4
    ss_mask = right_side_full_range_mask(vg, abs_id, settings.ss_min, settings.ss_max)
    slope = intercept = ss = np.nan
    if ss_mask.sum() >= 2:
        slope, intercept = np.polyfit(vg[ss_mask], np.log10(abs_id[ss_mask]), 1)
        ss = 1000.0 / abs(slope) if slope else np.nan
    data["abs_Id"] = abs_id
    data["gm_S"] = gm
    data["Mobility_cm2_Vs"] = mobility
    data["SS_fit_used"] = ss_mask
    data["SS_fit_log10_Id"] = np.where(
        ss_mask,
        slope * vg + intercept if np.isfinite(slope) else np.nan,
        np.nan,
    )
    positive_mobility = mobility[np.isfinite(mobility) & (mobility > 0)]
    metrics = {
        "Vg min [V]": float(np.nanmin(vg)),
        "Vg max [V]": float(np.nanmax(vg)),
        "Vg points": int(len(vg)),
        "Mobility max [cm2/Vs]": float(np.max(positive_mobility)) if len(positive_mobility) else np.nan,
        "Vth [V]": interpolate_vth(vg, abs_id, settings.vth_current),
        "Vth criterion |Id| [A]": settings.vth_current,
        "SS [mV/dec]": ss,
        "SS points": int(ss_mask.sum()),
        "SS |Id| min [A]": settings.ss_min,
        "SS |Id| max [A]": settings.ss_max,
        "Max |Ig| [A]": float(np.nanmax(np.abs(data["Ig"]))) if data["Ig"].notna().any() else np.nan,
    }
    return data, metrics


def merge_origin_columns(items, value_column, first_name="Vg"):
    merged = None
    for label, frame in items:
        part = frame[["Vg", value_column]].copy().rename(columns={value_column: label})
        part = part.drop_duplicates("Vg", keep="first")
        merged = part if merged is None else pd.merge(merged, part, on="Vg", how="outer")
    if merged is None:
        return pd.DataFrame(columns=[first_name])
    return merged.sort_values("Vg").rename(columns={"Vg": first_name}).reset_index(drop=True)


def safe_sheet_name(index, name):
    clean = re.sub(r"[:\\/?*\[\]]", "_", name)[:22]
    return f"P{index:02d}_{clean}"[:31]


def workbook_bytes(summary, processed, iv_items, ig_items, mobility_items, errors):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        ppt_summary_table(summary).to_excel(writer, sheet_name="PPT_Copy", index=False)
        ppt_sheet = writer.book["PPT_Copy"]
        for row_index, row in enumerate(ppt_sheet.iter_rows(), start=1):
            for cell in row:
                cell.font = Font(name="Arial", size=12, bold=row_index == 1)
        for index, (name, frame) in enumerate(processed, start=1):
            frame.to_excel(writer, sheet_name=safe_sheet_name(index, name), index=False)
        merge_origin_columns(iv_items, "Id").to_excel(writer, sheet_name="IV", index=False)
        merge_origin_columns(ig_items, "Ig").to_excel(writer, sheet_name="IG", index=False)
        merge_origin_columns(mobility_items, "Mobility_cm2_Vs").to_excel(
            writer, sheet_name="Mobility(FEM)", index=False
        )
        if errors:
            pd.DataFrame(errors).to_excel(writer, sheet_name="Errors", index=False)
    return output.getvalue()


def plot_all(samples):
    figure, (axis, mobility_axis) = plt.subplots(1, 2, figsize=(14, 5.5))
    for name, frame in samples:
        line = axis.semilogy(
            frame["Vg"], frame["abs_Id"], linewidth=1.6, label=f"{name} |Id|"
        )[0]
        color = line.get_color()
        if "Ig" in frame.columns and frame["Ig"].notna().any():
            axis.semilogy(
                frame["Vg"], np.abs(frame["Ig"]), linestyle="--", linewidth=1.0,
                color=color, alpha=0.75, label=f"{name} |Ig|"
            )
        if "SS_fit_used" in frame.columns:
            ss_points = frame[frame["SS_fit_used"]]
            if len(ss_points):
                axis.semilogy(
                    ss_points["Vg"], ss_points["abs_Id"], linestyle="", marker="s",
                    markersize=5, markerfacecolor="none", markeredgecolor=color,
                    label=f"{name} SS region"
                )
        if "Mobility_cm2_Vs" in frame.columns:
            mobility_axis.plot(
                frame["Vg"], frame["Mobility_cm2_Vs"],
                linewidth=1.6, color=color, label=name,
            )
    axis.set_xlabel("Vg [V]")
    axis.set_ylabel("|Current| [A]")
    axis.grid(True, which="both", alpha=0.25)
    axis.legend(fontsize=8, ncol=2)
    mobility_axis.axhline(0, color="gray", linewidth=0.8, alpha=0.6)
    mobility_axis.set_xlabel("Vg [V]")
    mobility_axis.set_ylabel("Mobility [cm2/Vs]")
    mobility_axis.grid(True, alpha=0.25)
    mobility_axis.legend(fontsize=8)
    figure.tight_layout()
    return figure
def main(configure_page=True):
    if configure_page:
        st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)
    st.caption(
        "Analyze multiple raw-data files and create sample results plus Origin-ready IV, IG, and Mobility sheets."
    )
    with st.sidebar:
        st.header("Calculation settings")
        w_over_l = st.number_input("W/L", min_value=1e-9, value=12.0, format="%.6g")
        tox_nm = st.number_input("tox [nm]", min_value=1e-9, value=10.0, format="%.6g")
        eps_r = st.number_input("Dielectric constant eps_r", min_value=1e-9, value=8.0, format="%.6g")
        vth_current = st.number_input("Vth criterion |Id| [A]", min_value=1e-30, value=1.2e-7, format="%.3e")
        ss_min = st.number_input("SS minimum |Id| [A] (inclusive)", min_value=1e-30, value=1e-11, format="%.3e")
        ss_max = st.number_input("SS maximum |Id| [A] (exclusive)", min_value=1e-30, value=1e-9, format="%.3e")
        vd_tolerance = st.number_input("Vd detection tolerance [V]", min_value=1e-6, value=0.02, format="%.3g")

    if "tft_upload_version" not in st.session_state:
        st.session_state.tft_upload_version = 0
    upload_column, clear_column = st.columns([5, 1])
    with upload_column:
        uploaded = st.file_uploader(
            "Upload multiple CSV or Excel raw-data files",
            type=["csv", "txt", "xlsx", "xls"],
            accept_multiple_files=True,
            key=f"tft_raw_upload_{st.session_state.tft_upload_version}",
        )
    with clear_column:
        st.write("")
        st.write("")
        if st.button(
            "Clear all files", disabled=not bool(uploaded), use_container_width=True
        ):
            st.session_state.tft_upload_version += 1
            st.rerun()
    if not uploaded:
        st.info("Select files to begin analysis.")
        return

    names = unique_names(uploaded)
    st.subheader("Uploaded files")
    st.dataframe(
        pd.DataFrame({"Order": range(1, len(uploaded) + 1), "Sample": names, "Filename": [f.name for f in uploaded]}),
        hide_index=True,
        use_container_width=True,
    )
    settings = Settings(w_over_l, tox_nm, eps_r, vth_current, ss_min, ss_max, vd_tolerance)
    summary_rows, processed, iv_items, ig_items, mobility_items, errors, preview = [], [], [], [], [], [], []

    for order, (uploaded_file, name) in enumerate(zip(uploaded, names), start=1):
        try:
            raw, parser = parse_uploaded(uploaded_file)
            numeric = numeric_frame(raw)
            curves = curves_by_vd(numeric, settings.vd_tolerance)
            if 0.1 not in curves:
                raise ValueError("Vd=0.1 V curve not found. Check the Vd column or sweep order.")
            calculated, metrics = analyze_curve(curves[0.1], settings, vds=0.1)
            summary_rows.append({"Order": order, "Sample": name, "Parser": parser, **metrics})
            calculated.insert(0, "Sample", name)
            processed.append((name, calculated))
            preview.append((name, calculated))
            mobility_items.append((name, calculated))
            for vd in (0.1, 1.0):
                if vd in curves:
                    label = f"{name}_Vd={vd:g}V"
                    curve = curves[vd].sort_values("Vg").drop_duplicates("Vg", keep="first")
                    iv_items.append((label, curve))
                    ig_items.append((label, curve))
                else:
                    errors.append({"Sample": name, "Stage": "Origin sheet", "Error": f"Vd={vd:g} V curve missing"})
        except Exception as exc:
            errors.append({"Sample": name, "Stage": "Analysis", "Error": str(exc)})

    summary = pd.DataFrame(summary_rows)
    st.subheader("All-sample summary")
    if len(summary):
        st.dataframe(
            copy_safe_summary_table(summary),
            hide_index=True,
            use_container_width=True,
        )
        st.caption(
            "Copy the table below for PPT (5 significant figures, 12 pt display). "
            "The downloaded Excel also includes a 12 pt PPT_Copy sheet."
        )
        st.dataframe(
            ppt_summary_table(summary).style.set_properties(**{"font-size": "12pt"}),
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.error("No files were analyzed successfully.")

    if errors:
        with st.expander(f"Items requiring review ({len(errors)})", expanded=True):
            st.dataframe(pd.DataFrame(errors), hide_index=True, use_container_width=True)

    if preview:
        tabs = st.tabs(["All transfer curves", "Processed data by sample", "Origin sheet preview"])
        with tabs[0]:
            st.pyplot(plot_all(preview), clear_figure=True)
        with tabs[1]:
            selected_name = st.selectbox("Select sample", [name for name, _ in processed])
            selected_frame = next(frame for name, frame in processed if name == selected_name)
            st.dataframe(selected_frame, use_container_width=True)
        with tabs[2]:
            sheet = st.selectbox("Sheet", ["IV", "IG", "Mobility(FEM)"])
            if sheet == "IV":
                view = merge_origin_columns(iv_items, "Id")
            elif sheet == "IG":
                view = merge_origin_columns(ig_items, "Ig")
            else:
                view = merge_origin_columns(mobility_items, "Mobility_cm2_Vs")
            st.dataframe(view, use_container_width=True)

        excel = workbook_bytes(summary, processed, iv_items, ig_items, mobility_items, errors)
        st.download_button(
            "Download all results and Origin-ready Excel",
            excel,
            file_name=f"TFT_multi_sample_{APP_VARIANT}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


if __name__ == "__main__":
    main()

