# TFT Analyzer Suite

TFT 측정 데이터와 ALD 공정 로그를 브라우저에서 분석하는 Streamlit 웹앱입니다.

## 페이지

### 1. TFT Multi-Sample Analyzer

- 4F 표준 형식과 6F B1500 raw 형식 지원
- B1500 `DataName/DataValue` 및 metadata 뒤 `VG,ID,IG,gm` Classic CSV 자동 인식
- 파일별 실제 VG 최소·최대 범위와 sweep reset 자동 감지 (`-2~2 V`, `-12~12 V` 등)
- 서로 다른 VG 범위의 샘플도 Origin용 Excel에서 VG 기준 outer merge
- 여러 시료의 Mobility, Vth, SS 동시 계산
- Transfer Curve에서 Id, Ig 및 SS fitting 구간 확인
- Origin용 IV, IG, Mobility(FEM) Excel 생성

### 2. Reliability Vth

- NBTS/PBTS 파일 동시 분석
- 기본 `|Id| = 1.2×10⁻⁷ A` 기준 Vth 계산
- 시간별 ΔVth, Transfer Curve 및 결과 Excel 생성
- OriginPro 2020 자동 플롯은 Windows 로컬 전용이며 공개 저장소에는 템플릿을 포함하지 않음

### 3. ALD Process Log

- Streamlit 비공개 설정의 오일 cycle별 열화 속도를 사용
- 현재 idle CVG 입력으로 Q3 열화 속도를 적용한 보수적 잔여 횟수만 표시
- 현재값·교체 기준·CVG 여유·Q3 열화 속도와 실제 대입 계산식 표시
- Q1·중앙값·Q3와 cycle별 열화 속도 Box Plot 표시
- 여러 ALD TXT/LOG 파일의 실제 BTorr 자동 추출
- Main step와 cycle 요약, 인터랙티브 Plot 및 Excel 생성

## 웹 실행

https://tft-multi-sample-analyzer.streamlit.app/

## 로컬 실행

```bash
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```


## ALD 비공개 예측 설정

Streamlit Community Cloud의 App settings → Secrets에 다음 형식으로 오일 cycle별 열화 속도를 등록합니다.

```toml
[ald_prediction]
cycle_slopes = [0.00001, 0.00002, 0.00003]
model_label = "Lab O3 oil-cycle model"
```

실제 값은 GitHub 코드나 README에 넣지 마세요. 로컬 실행 시에는 `.streamlit/secrets.toml.example`을 참고해 `.streamlit/secrets.toml`을 만들 수 있으며, 실제 secrets 파일은 Git에서 제외됩니다.
## 참고

웹 서버에서는 Windows용 OriginPro를 직접 실행할 수 없습니다. Origin 자동화는 템플릿을 보유한 Windows PC의 로컬 버전에서 사용해야 합니다.

업로드 파일은 분석 중 메모리에서 처리되며 GitHub 저장소에 자동으로 저장되지 않습니다. 공개 웹서비스에 회사·연구 보안 데이터 업로드 전에는 소속 기관의 보안 정책을 확인하세요.
## Recent updates

- PPT copy table shows FEM, Vth, and SS with 5 significant figures.
- Excel exports include a PPT_Copy sheet with five-significant-figure values and 12 pt Arial font.
- Gate current is converted to absolute value before preview, plotting, and Excel export.
- Reliability analysis accepts either 0 s or 1 s as the first measurement.

- Uploaded raw-data files can be cleared together with one button.
- The TFT preview shows transfer and Vd=0.1 V mobility curves side by side.

- Reliability uploads can also be cleared together with one button.
- Transfer-curve legends show one solid-line entry per sample below the plots.

## ALD analyzer additions

The ALD page now supports:

- automatic O3-flow and Main cycle counts after log upload (`Total Layer` is used for Main cycles; O3 cycles are inferred from measured duration and recipe step times)
- cycle-by-cycle Main-step TMA response: TMA-pulse peak pressure minus the mean pressure in the preceding baseline window
- red marking and a separate trend plot when TMA delta-P is at or below the editable replacement threshold (default `0.01 Torr`)
- cumulative TMA-pulse replacement estimate across all uploaded logs, ordered by process start time
- an optional shared laboratory process log backed by a private Supabase project

### Shared ALD process log setup

The Streamlit server filesystem is temporary, so persistent multi-user records are stored outside GitHub. Create a private Supabase project, run the SQL shown in the app under **ALD Process Log > 공동 공정 로그 > 관리자 설정 방법**, then add this to Streamlit Community Cloud **App settings > Secrets**:

```toml
[ald_shared_log]
url = "https://YOUR_PROJECT.supabase.co"
key = "YOUR_ANON_KEY"
table = "ald_run_log"
```

Do not commit real Supabase keys or raw laboratory logs to GitHub. The table records process date, operator, O3 cycles, Main cycles, idle CVG, and an optional note. The app displays cumulative O3/Main cycles across all saved rows.

For a pump-oil replacement, save an **오일 교체 · 누적 초기화** record instead of deleting earlier rows. The app keeps the complete lifetime history in Supabase and restarts the displayed O3/Main cumulative counts from the most recent oil-change marker. Hard delete is reserved only for incorrectly entered records.

### TMA calculation definition

For each Main cycle:

```text
baseline mean = average BTorr during the configurable window immediately before TMA pulse
TMA pulse peak = maximum BTorr during the configured TMA pulse interval
delta-P = TMA pulse peak - baseline mean
```

The process plot overlays the per-cycle pre-pulse baseline as a red line and each detected TMA maximum as an orange marker; hover shows peak, baseline, and delta-P together.

Large logs are parsed with vectorized timestamp/pressure conversion and are not parsed twice for the selected preview, keeping 4-18 MB equipment logs responsive on Streamlit Cloud.

TMA timing is detected from the original numeric BTorr signal, not from a rendered graph image. Large Main-step O3 responses are used as measured cycle anchors, and the app searches backward around the expected valve lag to find each TMA-window maximum. This avoids cumulative recipe-timing drift; the search tolerance is editable in Recipe settings.

A cycle is marked for TMA replacement review when `delta-P <= 0.01 Torr` by default. This is a process-monitoring rule, not a standalone proof that the TMA source is exhausted; confirm valve operation, line temperature, pressure sensor condition, and recipe timing before replacement.

### Editing and deleting shared ALD log records

The **기록 수정 · 삭제** panel is collapsed by default so the shared log table remains easy to scan; expand it only when a record needs maintenance.

Shared records can be edited or deleted from the ALD shared-log tab after two one-time administrator steps:

1. Run the UPDATE/DELETE policy SQL displayed in the app's management expander.
2. Add an edit password inside the existing Streamlit secret section:

```toml
[ald_shared_log]
url = "https://YOUR_PROJECT.supabase.co"
key = "YOUR_PUBLISHABLE_KEY"
table = "ald_run_log"
edit_password = "YOUR_LAB_ADMIN_PASSWORD"
```

The password is stored only in Streamlit Secrets and must not be committed to GitHub. Deletion requires both the password and an explicit confirmation checkbox. The app also accepts a Supabase URL accidentally copied with a trailing `/rest/v1` and normalizes it automatically.
