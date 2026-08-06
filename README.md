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
- 현재 idle CVG 입력만으로 보수적·대표·낙관적 잔여 횟수 추정
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
