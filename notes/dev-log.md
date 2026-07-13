# 개발 노트 (Dev Log)

> 의미 있는 작업을 할 때마다 **최신 항목을 위에** 추가한다. 형식: `## YYYY-MM-DD — 제목`

## 2026-07-13 — Jetson 사전작업: 셋업 문서 + 엣지 벤치마크 하네스

- **환경 확정**: Jetson Orin Nano **Super**, JetPack 6.2.2 / L4T 36.5 / CUDA 12.6 · TensorRT 10.3 · cuDNN 9.3 · Python 3.10. Claude Code 설치됨. **센서는 아직 없음.**
- **`docs/JETSON_SETUP.md`** 작성 — 0)GitHub clone(SSH) → 1)버전확인 → 2)jtop → 3)전력 MAXN SUPER+jetson_clocks → 4)venv(--system-site-packages) → 5)PyTorch(jetson-ai-lab jp6/cu126) → 6)검증 → 7)trtexec → 8)트러블슈팅.
- **`jetson/bench/` 엣지 벤치마크 하네스**(센서 불필요, 랜덤텐서 → **성능만** 측정): 3차 조사가 남긴 "검증된 Jetson 지연·전력 수치 없음" 공백 메우기.
  - `models.py`: MobileNetV3-Small·EfficientNet-B0(도네스 분류) + ThermalCNN·ThermalSeqTCN(열화상 2D-CNN+1D TCN) + ONNX export.
  - `benchmark.py`: ONNX→`trtexec`(fp16/int8) 지연·FPS 파싱 + `tegrastats` 전력·온도 샘플러 → `notes/data/bench/summary.md`.
  - 접근: torch2trt 회피, **JetPack 기본 trtexec만** 사용(의존성 최소).
- 검증(dev 머신): `py_compile` 통과 + trtexec/tegrastats 파싱 정규식 샘플 단위검증 통과. (실제 실행은 Jetson에서.)

### 다음 할 일 (다음 세션 시작점)
- [ ] **Jetson에서 실행**: `git pull` → `docs/JETSON_SETUP.md`로 툴체인 → `jetson/bench/benchmark.py` → `summary.md` 확인.
- [ ] 벤치 결과로 **모델 아키텍처 확정**(백본·정밀도).
- [ ] **MQTT 발행자 스켈레톤**(우선순위 3) — 가짜 센서값으로 cooking/status 발행 → 대시보드 mqtt 모드.
- [ ] GT 정의 확정(관능 기준·PoC 메뉴).

## 2026-07-12 — 대시보드 전면 재설계 + 데이터 계약 갱신(doneness)

- **데이터 계약을 새 도네스 설계로 동기화**(3문서): `docs/data-schema.md`, `shared/schema.json`,
  `dashboard/src/data/schema.js`. 핵심 출력 `boil_state` → **`doneness`(undercooked/done/overcooked, 순서형)**.
  필드 추가: `doneness_confidence`, `center_temp_c`(구 temperature_c), `boil_intensity`,
  `thermal`(MLX90640 32×24 배열). `stage` enum 정리(ingredient_add/heating/cooking).
- **대시보드 현대적 재구축**(architecture.html과 동일 디자인 시스템: 써모그래피 램프+그래파이트, 라이트/다크 토글):
  - 신규: `DonenessHero`(순서형 3단계 히어로), `StatTiles`(중심온도·경과·끓음강도·공정),
    `ThermalHeatmap`(32×24 인페르노 캔버스+셀 호버), `PotView`(목=캔버스 솥 시뮬/실장치=MJPEG `<img>`).
  - 갱신: `TemperatureChart`(단일시리즈 area+크로스헤어, 테마색 토큰 연동), `StageTimeline`, `AlertPanel`.
  - 제거: `StatusCard`, `AiDetectionCard`(→ Hero/StatTiles로 대체).
- 색: dataviz 검증기로 도네스 3색 CVD 확인(분리도 ΔE 25+, 상태색은 항상 라벨 동반). 열화상은 지각균일 인페르노(무지개 금지 준수).
- 검증: `npm run build` 통과, mock 데이터 계약 런타임 체크 통과(thermal 768개·doneness·컬러맵), preview 서버 서빙 확인.
  ※ 브라우저 부재로 픽셀 단위 시각 확인은 미실시 — 실제로 열어볼 것.
- 실장치 전환: `VITE_DATA_SOURCE=mqtt`(useCookingData) + `VITE_POT_MJPEG_URL`(PotView)만 설정하면 UI 수정 없이 전환.


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
- **구조 시각화** → `docs/architecture.html` (파이프라인·신호·라벨링·로드맵 한 장).

## 2026-07-12 — 네트워크·배치 결정 (Jetson·Pi·로봇)

- **기기 3개 확정**: Jetson(센서+AI/발행), Pi(브로커+대시보드), **로봇(외주, MQTT 구독→동작)**.
- **물리 연결 = 기가비트 스위치**. Jetson 랜포트가 1개뿐이라 직결 불가 → 셋을 스위치에 각 1개씩(총 케이블 3개, Jetson에서 나오는 건 1개). 인터넷/NTP 필요하면 스위치 대신 공유기.
- **통신 = 전부 MQTT**, 브로커는 Pi(Mosquitto). 로봇 담당사엔 브로커 IP+토픽+`shared/schema.json`만 전달.
- **데이터 경로 분리**:
  - ① MQTT: 단계·중심온도·신뢰도 + **열화상 32×24는 숫자배열로 전송 → 대시보드에서 히트맵 렌더**(영상 아님).
  - ② **MJPEG(HTTP)**: 솥 RGB 영상. 조리가 느려 저fps로 충분, Jetson 소형 HTTP 서버 → 대시보드 `<img>`. RTSP/WebRTC는 오버킬.
- ⏱ **시간동기 이슈**: 타임스탬프 정합 중요 → 스위치만이면 인터넷 없어 NTP 불가. 공유기로 NTP 쓰거나 로컬 시간서버 둘 것.
- 반영: `README.md` 시스템 구성 다이어그램, `docs/architecture.html` §02(신설).

- **3차 딥리서치(미해결 질문)** 완료 → `docs/research-methodology-3-open-questions.md`.
  MLX90640 32×24 = **스칼라 아닌 '이미지'로 직접 DL** (2D-CNN+1D TCN, <10k 파라미터, Jetson 여유).
  RGB 2뷰와 **late fusion**(비정합 이종센서). 3단계 출력은 **ordinal CORN**. 완료=시간경계(유사도 peak).
  ⚠️ 온도·시간 약지도만으론 완료/과조리 못 가름 → **소량 수작업 앵커 필요**. 열배열 실제 기여·early/late는 실측으로만.

### 다음 할 일 (다음 세션 시작점)
- [ ] **모델 아키텍처 설계 문서** (TODO) — 3차까지 종합해 구조도 확정: RGB 2뷰 branch + MLX90640 (2D-CNN+TCN) branch → late fusion → ordinal(CORN) 3단계. 온도(MLX90614) gated fusion.
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
