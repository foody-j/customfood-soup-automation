# 방법론 심화조사 3 — 미해결 질문 (MLX90640 열배열 중심)

> 2026-07-12 · 딥리서치 3차. 2차(`...-2-soup-specific.md`)가 남긴 미해결 4개 집중.
> 21소스 · 87주장 → 25 검증 → **24 확정 / 1 반박.** MLX90640 앵글이 가장 강하게 실증됨.

---

## 0. TL;DR
- **MLX90640 32×24는 스칼라가 아니라 '이미지'로 딥러닝에 직접 넣는다** — 검증 확실(동일 KU Leuven/Melexis 그룹 다수 논문 + 독립팀). 정석 레시피 = **작은 2D-CNN(프레임별) + 1D TCN(시간축).** <10k 파라미터로 MCU에서 46~87ms → **Jetson Orin Nano엔 여유 넘침.**
- **권고 구조**: RGB 두 뷰와 **별도 경량 thermal CNN branch를 late fusion.** (naive RGB+T 4채널 early stacking은 픽셀 정합 필요해서 비추.) 필요시 손수 만든 공간 피처(열구배·핫스팟 통계)도 병행.
- **완료 vs 과조리는 ordinal 문제** → **CORN**(> CORAL) 쓰고, 완료는 **시간경계 탐지**(타깃 상태와 유사도 peak)로.
- **온도·시간 약지도만으로 완료/과조리 구분은 근거 없음** → **소량 수작업 앵커 불가피할 가능성 큼.**

---

## §1 ★ MLX90640 32×24 열배열 활용법 (최우선 앵글) ✅ [high]

### 핵심: 저해상 배열을 '공간 이미지'로 직접 딥러닝
- **Vandersteegen et al. (CVPRW 2020, arXiv 2004.11623)** — 32×24 MLX90640을 **2D CNN(프레임별) + 1D TCN(시간)** 으로 처리 → 제스처 분류 **95.9% / 탐지 mAP 83%**, one-frame 지연. **이게 정석 템플릿.**
- 독립팀(arXiv 2401.06563, IEEE 2024): 5클래스 캐빈 제스처 **93.9% @ 8FPS**, 다중프레임 시공간 스택.
- Vandersteegen (ISVC 2022, arXiv 2212.08415): "32×24 픽셀에 DL 탐지를 직접 시도한 최초." → **배열은 스칼라가 아니라 2D 시공간 입력.**

### 아키텍처 권고: 2D-CNN(프레임) + 1D TCN(시간), 3D CNN 회피
- ResNet18(또는 더 가벼운) 2D 인코더 → 프레임 임베딩 → **1D TCN이 시간 동역학 모델링.** 논문이 3D CNN을 명시적으로 배제(파라미터·연산 과다). → **국/탕의 끓음·거품·증기 시간 변화를 열배열에서 읽는 데 그대로 매핑.**

### 연산 비용: 사실상 공짜 (Jetson 여유)
- arXiv 2212.08415: 3×3 conv 1개 + depthwise-sep conv 7개 + YOLOv2 헤드, 배경차분 32×24 입력 → **F1 91.62%, <10k 파라미터, 87ms(STM32F407)/46ms(STM32F746).** → **열배열 DL은 Orin Nano 병목 아님.** RGB 2뷰 + thermal branch 동시 여유.

### 대안 경로 1 — 손수 만든 공간 피처 + 고전 ML (동일 센서 실증)
- **TADAR (arXiv 2409.17742, MobiHoc 2024, 오픈소스)** — **정확히 MLX90640BAA(32×24, 110×75° FOV, 16Hz)** 사용. DNN 대신 **Histogram Gradient Boosting + 손수 만든 공간 피처(ROI 풀링, 정렬 피처벡터)** 로 F1 88.8%. → **열구배·핫스팟·공간 불균일(=끓는 위치)** 을 피처로 뽑아 고전 모델 or CNN branch에 병합 가능. 데이터 적을 때 특히 실용적.

### 대안 경로 2 — 열텍스처로 상태/재질 판별 (전이 논거)
- **Deep Thermal Imaging (Cho et al., CHI/IMWUT 2018, arXiv 1803.02310)** — 열텍스처 CNN으로 재질 판별 실내 >98%/실외 >89%. → **표면 온도분포·불균일을 조리상태 단서로** 읽는 근거. *단, FLIR One(~80~160px)이라 32×24보다 고해상 = 전이 논거지 32×24 실증은 아님.*

### 대안 경로 3 — guided super-resolution (픽셀 정합 필요할 때만)
- 저해상 열 + 고해상 RGB 가이드 → 고해상 열. **LapGSR(arXiv 2411.07750, 398K 파라미터, CMPNet 2.02M 대비 SOTA)**, **SwinFuSR(2404.14533)**, **CoReFusion(CVPR PBVS 2023, 2304.01243)**. → RGB에 열을 픽셀 정합하고 싶을 때 옵션. *단, 실제 32×24 MLX90640 검증은 없음 + 단순 thermal branch보다 무거움 → 필요할 때만.*

**→ 결론 권고**: **경로1(2D-CNN+TCN thermal branch) 기본 + 경로1의 손수 피처 보조.** 정합이 꼭 필요할 때만 guided SR.

---

## §2 완료 vs 과조리 경계 변별 ✅ [high/medium]
- **3단계를 ordinal(순서형) 문제로**: 미완<완료<과조리 순서 보존. **CORAL(arXiv 1901.07884)** 은 K-1 이진 서브태스크로 rank 일관성 보장. **CORN(arXiv 2111.08851)** 은 CORAL의 weight-sharing 제약을 조건부확률 chain-rule로 대체 → **성능 더 좋음. CORN 권장.**
- **완료 = 시간경계 탐지**: 이동창 안에서 **타깃 상태와의 유사도 peak** 로 완료 시점 검출(유사 연구 ~90%). (arXiv 2511.16965, 2025-11 preprint — 오븐 음식, 자가보고 ~90%라 medium.)
- ⚠️ 어떤 신호(거품/탁도/증기/수위)가 완료↔과조리에 제일 변별력 있는지 **직접 비교 논문은 여전히 없음**(미해결 유지). 열배열이 이 경계에 실제로 기여하는지는 실측 필요.

## §3 early vs late fusion (상보적 이종 센서) ✅ [medium]
- 'Beyond RGB: Early Stage Fusion'(Electronics 2025, MDPI 14/23/4746): **픽셀 정합된** 해양 데이터셋에서 early RGBT 융합이 악조건에서 RGB 단독보다 크게 우수. **핵심 단서: 이 이득은 픽셀 정합이 전제.**
- **우리 센서(32×24 열 + RGB 2뷰)는 네이티브 정합 안 됨** → **late/decision fusion(모달별 branch)이 더 안전한 기본값.** (직접 벤치는 없음, 정합 난이도 기반 추론.)

## §4 온도·시간 약지도만으로 완료/과조리 구분? ⚠️ [low]
- **근거 없음.** 끓는점 이후 포화 구간에서 Snorkel식 라벨링 함수/누적열량·미분 약지도로 완료↔과조리를 가른 사례 검색 안 됨.
- 대체 방법(타깃 유사도)도 **사람이 고른 타깃/앵커 이미지에 의존.** → **소량 수작업 앵커 불가피할 가능성 큼.** (근거의 부재 + 반박된 주장에 기반해 low 신뢰도.)
- ✗ 반박(0-3): "doneness 직접 분류/잔여시간 회귀는 일반화 나쁘다"는 주장 → 반박됨(직접 분류 접근이 나쁘다는 근거 아님).

---

## §미해결(여전히 실측으로만 풀림)
1. 32×24 해상도가 완료 vs 과조리(둘 다 끓는점 이상) 표면 열패턴을 구분할 만큼인가, 아니면 그 경계는 RGB(거품·탁도·증기)에만 있나 — **열배열의 실제 한계기여**.
2. guided SR(정합)이 Jetson에서 단순 thermal branch 대비 정확도 이득이 복잡도를 정당화하나.
3. 누적열량/온도미분 약지도로 완료/과조리 분리 가능한가, 최소 앵커 몇 장 필요한가.
4. 비정합 이종 센서에서 early vs late 실측 비교가 해양(정합) 결과와 같은가.

---

## §이 과제 최종 권고 (3차 종합)
- **thermal 경로**: MLX90640 → **2D-CNN + 1D TCN 경량 branch**, RGB 2뷰와 **late fusion**. 손수 공간피처(핫스팟/구배) 병행.
- **출력 헤드**: 3단계 **ordinal(CORN)**. 완료 시점은 시간경계(유사도 peak).
- **융합 시점**: **late 기본**(비정합 이종). 정합 필요시에만 LapGSR급 guided SR.
- **라벨**: 온도·시간 약지도 + **소량 수작업 앵커 준비**(순수 약지도로 완료/과조리 못 가름).
- **엣지**: thermal DL은 병목 아님. 실제 지연/전력은 직접 벤치(2차에서 검증수치 없었음).

## §참고문헌
- MLX90640 제스처 2D-CNN+TCN (CVPRW 2020) — https://arxiv.org/pdf/2004.11623
- MLX90640 직접 탐지 <10k (ISVC 2022) — https://arxiv.org/pdf/2212.08415
- 캐빈 제스처 32×24 (IEEE 2024) — https://arxiv.org/pdf/2401.06563
- TADAR, MLX90640BAA 고전ML (MobiHoc 2024, 오픈소스) — https://arxiv.org/pdf/2409.17742
- Deep Thermal Imaging 재질판별 (CHI 2018) — https://arxiv.org/pdf/1803.02310
- LapGSR guided SR (2024) — https://arxiv.org/html/2411.07750 · SwinFuSR — https://arxiv.org/html/2404.14533v1 · CoReFusion — https://arxiv.org/pdf/2304.01243
- CORAL ordinal — https://arxiv.org/abs/1901.07884 · CORN — https://arxiv.org/abs/2111.08851
- early RGBT fusion (해양) — https://www.mdpi.com/2079-9292/14/23/4746
- 조리완료 시간경계/ordinal (2025 preprint) — https://arxiv.org/html/2511.16965
