# TFT Multi-Sample Analyzer

TFT transfer-curve raw data를 여러 개 업로드하여 Mobility, Vth, SS를 계산하고 Origin용 Excel을 생성하는 Streamlit 웹앱입니다.

## 주요 기능

- 4F 표 형식 및 6F B1500 `DataName`/`DataValue` 형식 선택
- 여러 파일 동시 업로드 및 파일명을 샘플명으로 사용
- Vd=0.1 V 데이터로 Mobility 계산
- 기본 Vth 기준: `|Id| = 1.2e-7 A` (변경 가능)
- 지정 전류 범위의 오른쪽 turn-on branch 전체로 SS fitting
- Transfer Curve에서 `|Id|`, `|Ig|`, SS fitting 영역 미리보기
- 샘플별 Summary 및 Processed Data
- Origin용 `IV`, `IG`, `Mobility(FEM)` Excel 시트 생성

## Streamlit Community Cloud 배포

- Repository: 이 GitHub 저장소
- Branch: `main`
- Main file path: `streamlit_app.py`

## 로컬 실행

```bash
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

## 데이터 보안

업로드한 파일은 앱 메모리에서 분석되며 GitHub 저장소에 자동 저장되지 않습니다. 회사·연구실 기밀 데이터 또는 개인정보가 포함된 파일을 공개 웹서비스에 올리기 전에는 내부 보안 정책을 확인하세요.

## 입력 형식

### 4F standard table

`gateVoltage[V]`, `gateCurrent[A]`, `drainVoltage[V]`, `drainCurrent[A]` 열을 우선 인식합니다.

### 6F B1500 raw

`DataName` 아래의 `DataValue` 행을 읽습니다. Vd 열이 없으면 Vg reset을 기준으로 sweep을 나누고 첫 구간을 0.1 V, 두 번째 구간을 1 V로 취급합니다. 실제 측정 순서가 다르면 결과의 오류 목록과 Origin 미리보기를 확인하세요.
