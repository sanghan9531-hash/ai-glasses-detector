# AI 안경 탐지 시스템

시험 중 AI 스마트 안경 사용을 점검하기 위한 감독 보조 프로그램입니다.

- **기기 스캔**: 주변 블루투스 기기 중 AI 안경 의심 기기 식별
- **위치 추적**: 선택한 기기의 신호 세기로 근접도 측정
- **렌즈 탐지**: 웹캠 영상에서 카메라 렌즈 반사광 검출 (기준 화면 비교 + 지속성 검증)
- **육안 점검**: 감독관 확인용 점검 항목
- **점검 기록**: 결과 저장 및 CSV 내보내기
- **시뮬레이션 모드**: 블루투스·카메라 없이 가상 데이터로 동작 확인

> 판정 결과는 참고용이며, 최종 판단은 감독관이 직접 확인 후 내립니다.

## 안드로이드 앱 (직접 탐지: 렌즈 반사 + 블루투스 자동 감시 + 위치 추적)

**APK 내려받기:** https://github.com/sanghan9531-hash/ai-glasses-detector/releases/download/android-latest/AIGlassesDetector.apk

- 블루투스 자동 감시: 제조사 ID(이름을 숨겨도 감지)·기기명·신호 세기로 판별, 감지 시 진동 알림
- 위치 추적: 신호 세기로 근접도 표시, 가까울수록 진동 간격이 짧아짐
- 렌즈 반사 탐지: 폰 카메라 + 플래시
- 앱은 `index.html`을 Capacitor로 포장한 것이며, GitHub Actions(`build-apk.yml`)가 자동으로 만든다

## 웹 버전 (링크로 바로 실행)

**https://sanghan9531-hash.github.io/ai-glasses-detector/**

- 안드로이드 크롬 권장: 폰 카메라 + 플래시로 렌즈 탐지, 블루투스 기기 찾기
- 아이폰: 렌즈 탐지·육안 점검·기록은 가능, 블루투스 기기 찾기는 미지원
- 웹 버전은 신호 세기 측정·위치 추적을 지원하지 않음 (PC 버전 사용)

## 실행 방법 1: exe (파이썬 설치 불필요)

1. 이 저장소의 **Actions** 탭 → 가장 최근 **Build Windows EXE** 실행 선택
2. 아래 **Artifacts**의 `AIGlassesDetector-windows` 다운로드 → 압축 해제
3. `AIGlassesDetector.exe` 실행
   - "Windows의 PC 보호" 창이 뜨면 **추가 정보 → 실행** 선택 (서명되지 않은 프로그램이라 뜨는 경고)

## 실행 방법 2: 파이썬으로 직접 실행

```bash
git clone <저장소 주소>
cd ai-glasses-detector
pip install -r requirements.txt
python ai_glasses_detector_app.py
```

## 노트북 테스트 체크리스트

- [ ] 실제 모드에서 블루투스 스캔이 되는가
- [ ] 무선 이어폰(에어팟/버즈)이 "일반"으로 분류되는가
- [ ] 위치 추적: 이어폰에 다가가면 근접도가 올라가는가
- [ ] 렌즈 탐지: 기준 화면 등록 후, 아무것도 없을 때 "검출된 반사광 없음"이 나오는가
- [ ] 렌즈 탐지: 휴대폰 카메라 렌즈에 손전등을 비추면 검출되는가
- [ ] AI 안경 실기기의 기기명·제조사 ID 확인 (`GLASSES_COMPANY_IDS` 검증)
- [ ] 점검 기록 저장 및 CSV가 엑셀에서 한글 깨짐 없이 열리는가

문제가 생기면 프로그램 폴더의 `error_log.txt`를 확인하세요.
