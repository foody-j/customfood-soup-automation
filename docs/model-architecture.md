# 모델 아키텍처 설계 — 도네스(조리 완료) 인식

> 2026-07-24 확정. 조사 1~3차(`research-methodology*.md`) + GT 설계(`gt-definition-design.md`) +
> Jetson 실측 벤치(`notes/data/bench/summary.md`)를 종합해 **권고를 확정 설계로 굳힌 문서.**
> 이후 데이터 수집 스펙·구현·학습은 이 문서를 기준으로 한다. 구조를 바꾸면 이 문서를 먼저 고치고
> `notes/decisions.md`에 결정을 남긴다. (출력 계약은 `docs/data-schema.md`가 단일 출처.)

---

## 0. TL;DR — 전체 구조

**메뉴별 whole-frame 3단계 ordinal 분류기** (OnionBot식). 모달별 branch → late fusion →
CORN ordinal 헤드. 온도 스칼라는 gated fusion으로 주입.

```
RGB oblique (3×224×224) ──▶ EfficientNet-B0 ──▶ 1280d → proj 256d ─┐
RGB top     (3×224×224) ──▶ EfficientNet-B0 ──▶ 1280d → proj 256d ─┤
                                                                    ├─ concat ─▶ 융합 MLP ─▶ f_fused
MLX90640 열배열                                                     │              ▲
 (16f × 1×24×32) ──▶ 2D-CNN(프레임별) ─▶ 1D TCN ─▶ 128d ───────────┤              │ gated fusion
 └─ 수작업 공간피처(핫스팟·열구배 등) ─▶ 위 128d에 concat           │              │ g=σ(Wg[f;t])
MLX90614 중심온도 시계열(1Hz, 60s 창)                               │              │
 └─ 파생피처(현재값·dT/dt·누적열량) ─▶ MLP ─▶ 64d ──────────────────┴──────────────┘
                                                    │
                                                    ▼
                              ┌─ 주 헤드: CORN ordinal (미완<완료<과조리, 로짓 2개)
                              └─ 보조 헤드: boil_intensity 회귀 (0~1)
```

- **백본 = EfficientNet-B0 (fp16)** — 실측 487 FPS로 속도 제약 없음이 확인됨 → 후보군 중 용량이
  가장 큰 것을 정확도 기준으로 선택. (근거: bench summary + 도미 논문에서 큰 백본이 우세한 경향)
- **thermal = 2D-CNN + 1D TCN** (Vandersteegen 정석 템플릿), 3D CNN 배제.
- **융합 = late fusion** (RGB↔thermal 비정합이므로 early stacking 배제), 온도 스칼라는
  **gated fusion**(AquaFusionNet식)으로 신뢰도를 모델이 스스로 조절.
- **출력 = CORN ordinal 3단계** + 완료 시점은 후처리(스무딩+히스테리시스)로 확정.

---

## 1. 문제 정의와 출력

- 태스크: 프레임(+최근 시간창) 입력 → **doneness ∈ {undercooked < done < overcooked}** (순서형).
  핵심 출력은 **0→1 전이 시점(완료 알림)**. 과조리는 안전망. (`gt-definition-design.md` §1)
- 메뉴(레시피)별 개별 모델을 학습한다(OnionBot식). 공통 아키텍처·공통 3단계 스키마, 가중치만 메뉴별.
- 출력 → `cooking/status` 페이로드 매핑:

| 모델 출력 | 페이로드 필드 | 산출 방법 |
|---|---|---|
| CORN 단계 예측 | `doneness` | §5 추론 규칙(스무딩+히스테리시스) 적용 후 |
| 단계 확률 | `doneness_confidence` | 예측 단계의 확률 P(ŷ) |
| 보조 회귀 | `boil_intensity` | 보조 헤드 출력 (0~1 클램프) |
| (센서 직접) | `center_temp_c`, `thermal.*` | 모델 경유 없이 센서값 그대로 |

## 2. 입력 사양과 캐던스

| 모달 | 텐서 | 원 주기 | 비고 |
|---|---|---|---|
| RGB oblique(주) | 3×224×224 | 1~2 fps | 건더기 무름·국물 탁도 담당 |
| RGB top(보조) | 3×224×224 | 1~2 fps | 수위·거품·증기·넘침 담당 |
| MLX90640 열배열 | 16프레임 × 1×24×32 | 센서 최대 16 Hz → **2 Hz 서브샘플** | 16프레임 창 = 최근 8초 |
| MLX90614 중심온도 | 60s 창 (1 Hz × 60) | 1 Hz, hold-last | 파생: 현재값, dT/dt, 누적열량 |

- **융합·추론 캐던스 = 1 Hz** (계약의 `cooking/status` 주기와 일치). 느린 센서는 hold-last로
  최신값 유지(어류 가공라인 패턴). 조리는 느린 과정이라 1 Hz면 충분.
- 전처리: RGB는 ImageNet 정규화. 열배열은 세션 시작 온도 기준 배경차분 + [0,1] 정규화
  (ISVC 2022가 배경차분 입력으로 <10k 파라미터 성능 달성).
- RGB↔thermal **픽셀 정합은 하지 않는다** (late fusion이므로 불필요). 가열 체커보드 캘리브는
  ROI 크롭(솥 영역 잘라내기) 용도로만 1회 수행.

## 3. 브랜치 설계

### 3.1 RGB branch ×2 (뷰별 독립 가중치)
- **EfficientNet-B0**, ImageNet 사전학습 → 전이학습(소량 데이터 전략 1순위).
- 뷰마다 별도 인코더(보는 물리신호가 다름: oblique=무름·탁도, top=거품·수위) → GAP 1280d →
  Linear+GELU로 **256d 투영**.
- 백본 선택 근거: 실측 fp16 2.05 ms/487 FPS(2뷰여도 ~4 ms)로 속도는 비제약 → 정확도 기준 선택.
  MobileNetV3-Small은 정확도 열세 시 폴백이 아니라 **전력 절감이 필요할 때만** 고려.

### 3.2 Thermal branch (MLX90640)
- 정석 템플릿(CVPRW 2020): **프레임별 경량 2D-CNN → 프레임 임베딩 시퀀스 → 1D TCN**이 시간
  동역학(끓음 확산·핫스팟 이동) 모델링. 3D CNN은 파라미터·연산 과다로 배제.
- 규모: conv 3×3 + depthwise-sep conv 스택, 수만 파라미터 이하 (실측 0.12 ms — 사실상 공짜).
- **수작업 공간 피처 병행**(TADAR식): 프레임당 [최대/평균/표준편차 온도, 핫스팟 좌표·면적,
  반경방향 열구배] 등 ~10개 스칼라 → TCN 출력에 concat. 데이터 적을 때 특히 유효.
- 출력 **128d**.

### 3.3 스칼라 branch (MLX90614 + 시간)
- 60초 창에서 파생피처: 현재 온도, 이동평균 dT/dt, 세션 누적열량(∫T dt), 경과시간.
- 2층 MLP → **64d**.

## 4. 융합

1. **late fusion**: [oblique 256 ‖ top 256 ‖ thermal 128] concat → MLP(704→256) = 시각 융합 f_v.
   - 근거: RGB와 32×24 열배열은 네이티브 정합이 안 됨 → early(채널 스태킹)는 정합 전제라 배제.
2. **gated fusion**(AquaFusionNet식): 스칼라 임베딩 t(64d→256d 투영)와
   `g = σ(Wg [f_v ; t])`, `f_fused = g ⊙ t' + (1−g) ⊙ f_v`.
   - 온도가 유의한 구간(가열·미완↔완료)에선 게이트가 열리고, 온도가 포화된 구간
     (완료↔과조리, 둘 다 끓는점)에선 시각 신호에 자동으로 무게가 실리는 구조.
3. 센서 드롭아웃 대비: 학습 중 branch 무작위 드롭(모달 결손 강건성, SwinFuSR·융합 서베이 권고).
   추론 시 결손 모달은 0-임베딩 + `sensor_fault` 경고.

## 5. 출력 헤드와 추론 규칙

- **주 헤드: CORN** (CORAL의 weight-sharing 제약 제거판, 성능 우위). 3단계 → 조건부 이진 로짓
  **2개**: P(y>미완), P(y>완료 | y>미완). rank 일관성 보장.
- **보조 헤드: boil_intensity 회귀** (시각 융합 f_v에서 분기, 0~1). 대시보드 표시용이자
  끓음이라는 중간 신호를 강제하는 보조 손실 역할.
- **추론 후처리** (0→1 전이 = 완료 알림이므로 오탐 방지가 중요):
  1. 1 Hz 단계 확률을 **EMA 스무딩**(α≈0.3).
  2. **히스테리시스**: `done` 선언은 P(y≥done) > 0.7이 N초(기본 10초) 연속일 때.
     되돌림(done→undercooked)은 P < 0.4에서만 — 경계 진동 방지.
  3. `overcooked` 진입 시 `past_done` 경고 발행.
  - 시간경계 탐지(타깃 유사도 peak)는 **오프라인 GT 보정용**으로 사용(§6), 런타임은 위 규칙.

## 6. 학습 전략

- **손실**: `L = L_CORN + λ1·L_boil(MSE) [+ λ2·L_SSL]`. λ1≈0.3.
  SSL 보조손실(rotation/jigsaw, 타겟 데이터셋 내부 unlabeled만)은 라벨 부족 시 옵션(ECCV 2020:
  데이터 적을수록 이득).
- **라벨**: 약지도 스크립트가 본체 — 온도·시간 임계값으로 미완↔완료 대량 자동라벨.
  **완료↔과조리 경계는 약지도로 못 가름**(둘 다 끓는점 이후) → 메뉴당 20~30세션 관능 앵커 +
  클러스터링으로 경계 확정(`gt-definition-design.md` §2). 완료는 점이 아닌 **구간** 라벨.
- **클래스 균형**: 느린 단계(미완) undersampling(OnionBot). 목표 데이터 규모 2~4천 프레임/메뉴.
- **증강**: RGB — 색 지터는 약하게(탁도·색이 신호이므로), 기하·블러·김서림 시뮬 위주.
  thermal — 소폭 온도 오프셋·노이즈. 시간축 — 창 시프트.
- **학습 순서**: ① RGB oblique 단독(최소 기능 모델) → ② +top, +thermal, +gated 온도 순차 추가하며
  각 단계 기여를 ablation으로 기록(§8) → ③ 전체 fine-tune.

## 7. 엣지 배포 (Jetson Orin Nano Super)

- 내보내기: PyTorch → ONNX → **TensorRT fp16** (JETSON_SETUP.md 툴체인, trtexec).
- **정밀도: 전 branch fp16.** INT8은 efficientnet에서만 유의미(2.05→1.60 ms)했고 캘리브레이션
  비용 대비 이득 없음 — 정확도 리스크만 있으므로 채택 안 함.
- 지연 예산 (실측, MAXN_SUPER): EffB0 ×2뷰 ≈ 4.1 ms + thermal_seq_tcn 0.12 ms + 융합/헤드 ≪ 1 ms
  → **합계 < 10 ms @ 1 Hz 추론** (예산의 1% 미만). 전력 7~8 W, 온도 여유.
- 실행 형태: 단일 프로세스가 1 Hz 루프에서 [센서 수집 → 추론 → 후처리 → MQTT 발행]을 수행.
  MJPEG 스트림은 별도 스레드/프로세스.

## 8. 검증 계획 — 이 설계가 "기본값"인 항목 (실측으로 확정)

조사가 못 푼 미해결 질문(3차 §미해결)을 ablation으로 치환한 것. 데이터 수집 후 반드시 실행.

| # | 질문 | 실험 | 판단 기준 |
|---|---|---|---|
| A1 | 열배열이 완료↔과조리 경계에 실제 기여하나 | thermal branch on/off | 경계 구간 F1 향상 유무 |
| A2 | late vs early(guided SR 정합 후 stacking) | 기본 late vs LapGSR 정합 early | 정확도 이득이 복잡도 정당화할 때만 교체 |
| A3 | 뷰 기여 (oblique 주 가설) | 단일뷰 vs 2뷰 | 뷰별 성능 + 2뷰 이득 |
| A4 | 약지도 최소 앵커 수 | 앵커 수 스윕(0/10/20/30세션) | 경계 정확도 포화점 |
| A5 | 백본 (B0 vs V3-Small) | 동일 데이터 학습 비교 | 정확도 우선, 동률이면 저전력 |

## 9. 확장 경로 (지금은 안 함)

- **TSM/MS-TCN 승격**: 1 Hz 프레임 분류가 불안정하면 RGB에도 시계열 모듈 도입(TSM은 Jetson 실증
  있음). 현 설계는 시간 정보를 thermal TCN + 후처리 스무딩으로만 다룸 — 먼저 이걸로 검증.
- **거품 YOLO 보조 신호**: 탑뷰 거품 이산클래스 탐지(쿡탑 논문 recall 0.996)를 별도 aux 입력으로.
- **guided SR(LapGSR급)**: A2에서 early 융합이 유의하게 이길 때만.

---

## 근거 문서 매핑
- 백본·정밀도·지연: `notes/data/bench/summary.md` (2026-07-13 실측)
- thermal 2D-CNN+TCN·수작업 피처·CORN·late fusion: `research-methodology-3-open-questions.md`
- gated fusion·2뷰 역할·약지도·소량 데이터: `research-methodology-2-soup-specific.md`
- OnionBot 프레이밍·도미 논문 GT·TSM: `research-methodology.md`
- 3단계 정의·GT 파이프라인·앵커: `gt-definition-design.md`
- 출력 계약: `docs/data-schema.md` (`doneness`, `doneness_confidence`, `boil_intensity`)
