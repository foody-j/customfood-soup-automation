# 개발 노트 (Dev Log)

> 의미 있는 작업을 할 때마다 **최신 항목을 위에** 추가한다. 형식: `## YYYY-MM-DD — 제목`

## 2026-07-10 — 프로젝트 초기 셋업

- 저장소 생성 및 git 초기화 (`~/customfood-soup-automation`).
- 개발 환경 구축: 시스템에 Node/git이 없어 **micromamba로 사용자 영역에 Node 20 + git 설치**
  (env `dev`). PATH: `export PATH="$HOME/.local/mamba/envs/dev/bin:$PATH"`.
- **공유 데이터 계약 정의** — `docs/data-schema.md`, `shared/schema.json`
  (Jetson ↔ 대시보드 MQTT 페이로드, 조리 단계/끓음 상태/경고 enum).
- **React + Vite 대시보드 스캐폴딩** (`dashboard/`), recharts 추가.
- 데이터 소스 추상화(`useCookingData.js`) + 목 데이터 시뮬레이터(`mockSource.js`) 구현 →
  나중에 MQTT로 교체 시 UI 수정 불필요.
- 대시보드 컴포넌트 5종: StatusCard, TemperatureChart, StageTimeline, AiDetectionCard, AlertPanel.

- 대시보드 목 데이터 구동 확인 완료(사용자 검증).

### 다음 할 일 (다음 세션 시작점)
- [x] **GitHub 원격 연결 + push 완료** — github.com/foody-j/customfood-soup-automation (private), SSH 키(ed25519) 인증
- [ ] MQTT 파이프라인 미리 뚫기: Mosquitto 로컬 브로커 + 가짜 Jetson 발행자(Python) → 대시보드 mqtt 모드 전환 ← 다음 최우선
- [ ] 실제 레시피별 목표 온도/시간 값 채우기 (지도교수 확인)
- [ ] Jetson 발행자에 실제 AI 추론 결과 연결
