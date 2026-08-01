# TFT Analyzer Suite

TFT 측정 데이터와 ALD 공정 로그를 브라우저에서 분석하는 Streamlit 웹앱입니다.

## 페이지

### 1. TFT Multi-Sample Analyzer

- 4F 표준 형식과 6F B1500 raw 형식 지원
- 여러 시료의 Mobility, Vth, SS 동시 계산
- Transfer Curve에서 Id, Ig 및 SS fitting 구간 확인
- Origin용 IV, IG, Mobility(FEM) Excel 생성

### 2. Reliability Vth

- NBTS/PBTS 파일 동시 분석
- 기본 `|Id| = 1.2×10⁻⁷ A` 기준 Vth 계산
- 시간별 ΔVth, Transfer Curve 및 결과 Excel 생성
- OriginPro 2020 자동 플롯은 Windows 로컬 전용이며 공개 저장소에는 템플릿을 포함하지 않음

### 3. ALD Process Log

- 현재 idle CVG 기반 O₃ 공정 잔여 횟수 추정
- 여러 ALD TXT/LOG 파일의 실제 BTorr 자동 추출
- Main step와 cycle 요약, 인터랙티브 Plot 및 Excel 생성

## 웹 실행

https://tft-multi-sample-analyzer.streamlit.app/

## 로컬 실행

```bash
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

## 참고

웹 서버에서는 Windows용 OriginPro를 직접 실행할 수 없습니다. Origin 자동화는 템플릿을 보유한 Windows PC의 로컬 버전에서 사용해야 합니다.

업로드 파일은 분석 중 메모리에서 처리되며 GitHub 저장소에 자동으로 저장되지 않습니다. 공개 웹서비스에 회사·연구 보안 데이터 업로드 전에는 소속 기관의 보안 정책을 확인하세요.