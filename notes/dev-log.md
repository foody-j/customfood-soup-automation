# 개발 노트 (Dev Log)

> 의미 있는 작업을 할 때마다 **최신 항목을 위에** 추가한다. 형식: `## YYYY-MM-DD — 제목`

## 2026-07-12 — 방법론 자료조사 + 문제 재정의

- **문제 재정의**: "끓음/넘침 정도" → **"조리 완료(doneness) 판정"** 으로 목표 변경.
- **센서 추가 확정**: MLX90614ESF(GY-906-DCI, 단일점 비접촉 IR, FOV 5°) = 솥 중심온도,
  MLX90640(32×24 thermal array) = 표면 온도분포. RGB 2대(탑뷰 + oblique 다시점).
- **딥리서치 수행**(팬아웃 검색→소스 페치→3표 교차검증, 21소스/검증 100주장 중 반박 1건).
  결과 전문: `docs/research-methodology.md`.
- **핵심 발견**: GT(정답) 미정 문제의 직접 선례 = 튀김 도미 doneness 논문(ScienceDirect 2025).
  관능+이화학(온도·색·시간) 클러스터링으로 doneness 이산화 → DenseNet-121 90%. 색-doneness 상관 0.93.
  → 접근 A(시간·온도)를 접근 B(시각 분류)의 **약지도 라벨러**로 쓰는 상보 전략 도출.
- **권장 로드맵**: GT 정의(도미 논문 방식 이식) → 탑뷰 RGB+MLX90614 DenseNet PoC(TensorRT)
  → RGB-T 4채널 융합 검증 → 필요시 TSM/MS-TCN 단계 세그멘테이션. 어노테이션은 CVAT(시간구간·멀티모달).

- **2차 딥리서치(국/탕 특화)** 완료 → `docs/research-methodology-2-soup-specific.md`.
  색 대신 쓸 신호 확정: **끓음/거품 동역학 · 국물 탁도(우러남) · 건더기 무름(OnionBot식 milestone 분류)**.
  온도+비전 = gated cross-attention(AquaFusionNet), 소량데이터 = 전이학습+SSL+약지도.
  ⚠️ 검증된 Jetson 엣지 실시간 수치는 없음(직접 벤치 필요), 국/탕 직접 논문도 없음(도메인 외삽).
- **GT 설계안** 작성 → `docs/gt-definition-design.md` (3단계 정의, 하이브리드 GT, 도미 vs 국/탕 차이 명시).

### 다음 할 일 (다음 세션 시작점)
- [진행중] **미해결질문 3차 딥리서치** ← MLX90640 32×24 열배열 활용법 등 §미해결 4개 집중.
- [ ] **모델 아키텍처 설계 문서** (TODO) — 조사 종합해 네트워크 구조도(입력→백본→융합→3단계 출력) 확정.
- [ ] **GT 정의 실험 설계 확정** (TODO) — 결정 2개: (1) 관능평가 기준(지도교수), (2) PoC 시작 메뉴 1종.
- [ ] 하드웨어 도착 후: 동기 로깅 스크립트 → 데이터 수집 → schema 3문서 재정의.
- [ ] GT 확정 후 `docs/data-schema.md` / `shared/schema.json` / `dashboard/src/data/schema.js`를
      "끓음 상태" → "조리 완료 단계"로 재정의(3문서 동기화).
- [ ] (기존) MQTT 파이프라인 미리 뚫기 — Mosquitto + 가짜 Jetson 발행자.

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

## 2026-07-12 — 원격 AI 무인 작업 인프라 설정

- Discord 봇(discord-ai-orchestrator) 경유 무인 에이전트에 **코드 수정 권한** 부여:
  `.claude/settings.json` 권한 사전 승인 + 워크스페이스 trust 설정.
- 안전장치: pre-commit hook — 무인 실행(`AI_AGENT_RUNNER=1`)의 main 직접 커밋 차단.
  무인 작업은 `ai/<작업명>` 브랜치에만 커밋, push 금지, 사람이 diff 검토 후 머지.
- CLAUDE.md에 '원격 AI 작업 규칙' 절 추가. git/node/npm을 ~/.local/bin에 심볼릭 링크
  (headless PATH 문제 해결).
