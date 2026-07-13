# 방법론 자료조사 — 국/탕 조리 완료 판정 (딥리서치 결과)

> 2026-07-12 · 딥리서치 하네스(5각 팬아웃 검색 → 소스 페치 → 3표 교차검증) 결과 정리.
> 21개 소스 · 검증 100개 주장 중 반박 1건, high 신뢰도 70/75. 각 항목 끝 `[S#]`는 소스 번호(맨 아래 참고문헌).

## 0. 핵심 결론 (TL;DR)

- **"조리 완료" 정답(GT) 미정 문제를 가장 깔끔하게 푼 선례**는 튀김 도미(golden pompano) doneness 논문[S2]. **관능평가 + 이화학 지표(중심온도·수율·색·식감)를 시간축(0~1320s)으로 클러스터 분석 → raw/medium/fully cooked/overcooked 4단계로 이산화** 후 CNN 분류. DenseNet-121이 **90%** 정확도. 색(CIELAB a\*/b\*)이 doneness와 상관 0.93/0.92 → RGB 비전으로 doneness 추정 가능함을 뒷받침.
- **권장 전략**: 접근 A(시간·온도)와 B(시각 분류)를 **경쟁이 아니라 상보**로 씀. MLX90614 중심온도 + 시간 + 색을 **GT 자동 생성(weak label)**에 쓰고, 실제 추론은 카메라 기반 분류로. 접근 C(TAS)는 프레임 분류가 노이즈 심할 때 2단계로 승격.
- **엣지 모델**: PoC는 DenseNet-121/EfficientNet급 이미지 분류(TensorRT). 시계열 필요 시 **TSM**(Jetson Nano에서 13ms/76fps, 8W 실증[S6][S8])이 1순위 엣지 후보.
- **어노테이션**: 시간구간 라벨 + RGB+thermal 멀티모달이면 **CVAT**(무료·시간구간·센서퓨전 지원[S19][S20]). 단순 분류/bbox만이면 Roboflow가 빠름.

---

## 1. 최신 논문·데이터셋 (2022~2026)

### 조리 완료/doneness 직접 관련 (가장 중요)
- **튀김 도미 doneness 인식 시스템 (ScienceDirect 2025)** [S2] — **이 과제의 직접 템플릿.** doneness를 4단계 다중분류로 프레이밍. GT를 관능+이화학 클러스터링으로 정의. VGGNet-19=83.53% / ResNet-50=86.75% / **DenseNet-121=90.00%**. 색(CIELAB) 상관 0.93/0.92. *주의: 대상은 튀김(frying) doneness — 국/탕은 아니지만 방법론이 그대로 이식 가능.*
- **Food State Recognition using Deep Learning (IEEE Access 2022)** [S1] — cascaded multi-head로 식재료 state+type 동시 인식. 9 state / 18 type. cascade 87% vs non-cascade 81%. 조리 시스템의 핵심 역량으로 state 인식을 자리매김.

### 조리 상태(state)·단계(TAS) 데이터셋
- **CaptainCook4D (2024)** [S0] — 멀티모달 egocentric, 384영상/94.5h/24절차. step 5,300 + action 10,000 세그먼트. **error 라벨 7종(타이밍·온도 포함)** → 완료/이상 판정에 직결. Omnivore F1=53.9%. *thermal 없음.*
- **COM Kitchens (ECCV 2024)** [S10] — **overhead(탑뷰) 실제 조리 영상** 145편/40h. TAS 세그먼트(≈2,286개, action 131종) — *검증 중 원 저널의 2,852는 오타, 실제 2,286으로 정정됨.* 탑뷰 카메라 기하가 이 과제와 동일.
- **EPFL-Smart-Kitchen-30 (2025)** [S15] — RGB-D 9대 다시점 + HoloLens ego. 29.7h/16명/4레시피, 분당 33.78 세그먼트 조밀 라벨. pose-based TAS + 멀티모달 벤치. *thermal 없음.*
- **Breakfast / 50Salads / GTEA** [S3][S9] — TAS 표준 조리 벤치. **50Salads는 탑다운(탑뷰) 촬영** → 이 과제 탑뷰 선례. Breakfast는 **3~5대 다중 카메라** → 다시점 선례.
- **Cooking State Recognition 챌린지 (arXiv 1805.09967 / VGG 1905.08606)** [S4][S24] — 5,978장/7 state. Inception V3 75.65%. 식재료 손질 상태(썰기 등)이지 국/탕 doneness는 아님.
- **NHK Recipe Dataset (arXiv 2507.17232, 2025)** [S5] — 식재료 state 어노테이션, world-state 형식. 이산 조리 상태 전이를 사람이 신뢰성 있게 라벨 가능(Node F1 0.911)함을 입증.

### 서베이
- **Temporal Action Segmentation: Analysis of Modern Techniques (arXiv 2210.10352, 2022)** [S9] + **TPAMI 2023 (Ding/Sener/Yao)** [S3]. TAS = 프레임 단위 라벨 조밀 부여. **핵심 벤치 대부분이 조리 도메인** → TAS가 조리 모니터링과 잘 맞음.

---

## 2. 모델 아키텍처 — Jetson Orin Nano 제약 반영

### (B) 이미지 분류 — PoC 1순위
- **DenseNet-121** — doneness 연구 우승(90%)[S2]. EfficientNet-lite/ResNet-50도 후보. TensorRT INT8/FP16으로 Orin Nano에서 여유. 가장 싼 데이터·모델.

### (C) 비디오/시계열
- **TSM (Temporal Shift Module, ICCV 2019)** [S6][S8] — **추가 파라미터·FLOPs 0**의 plug-in. **Jetson Nano에서 13ms(76fps), 8W** 실증. TSM-ResNet50 74.1%(Kinetics) > I3D. **엣지 시계열 1순위.** 리포: `mit-han-lab/temporal-shift-module`.
- **X3D-UGT (arXiv 2602.10818, 2026-02)** [S7] — X3D+TSM, 0.96M params, **Jetson Orin Nano MAXN에서 FP16 TensorRT 10.3 FPS, 엔진 5.3MB**. *중요 경고: 너무 작은 TSM계 모델은 TensorRT에서 작은 CUDA 커널 파편화로 memory-bandwidth-bound가 되어 오히려 큰 모델(EPAM-Net 23FPS, DVANet 29FPS)보다 느릴 수 있음.* → 무조건 작은 게 빠른 게 아님.
- **TAS 세그멘테이션**: MS-TCN(2019), ASFormer(2021), Diffusion Action Segmentation(2023), **timestamp/weakly-supervised**(라벨 비용 절감)[S3]. 리포: `nus-cvml/awesome-temporal-action-segmentation`.

### 융합 모델 원칙
- 멀티모달 융합 3분류: **early / late / hybrid**[S13]. **Transformer 융합이 concat/summation보다 modality 결손에 강함**[S13] → 센서 드롭아웃 대비.

---

## 3. 다시점(Top + Oblique) 카메라 — 선례와 이점

- **탑뷰 선례**: 50Salads(탑다운 TAS)[S9], COM Kitchens(overhead)[S10]. 솥 표면·거품·넘침·색 변화 관측에 유리.
- **다시점 선례**: Breakfast 3~5대[S9], EPFL 9대 exo+1 ego[S15], CookingDataset 3뷰(측면/ego/정면)[S16].
- **이점 요약**: 탑뷰=국물 표면 상태(끓음·색·거품)·전체 구도, oblique=재료 부피·건더기·표면 아래 층 가시성. 두 뷰를 **late fusion**(뷰별 특징 추출 후 결합)하면 단일뷰 대비 가림·반사에 강건. *단, 조리 완료 판정에 다시점이 필수라는 정량 증거는 이 도메인에 아직 부족 — 탑뷰 우선, oblique 보조로 시작 권장.*

---

## 4. Thermal(MLX90640) + Point IR(MLX90614) + RGB 융합

- **RGB-T 4D 이미지 융합 (Sensors/MDPI 2023)** [S11] — **당신 하드웨어와 거의 동일 구성**: FLIR Lepton(160×120) + Pi 카메라 + Raspberry Pi. RGB+thermal을 4채널로 쌓아 음식 분할 **F1 0.87 (RGB단독 0.66 / thermal단독 0.64)**. 시각적으로 비슷한 음식·상온 구분에 thermal이 특히 유효. *다만 분할은 K-means(딥러닝 아님).*
- **US Patent 10819905 — 조리기구 온도센싱 데이터 융합** [S12] — 저해상 thermal + 고해상 RGB 상관 → **음식별 고해상 온도맵** 생성. **MLX90640(32×24)급 저해상(50×50, 162×120) 명시.** 캘리브레이션 파라미터 + Hausdorff 거리 매칭으로 픽셀 대응.
- **SwinFuSR (CVPRW 2024)** [S13] — RGB-guided thermal 초해상. **3.3M params(경량, 엣지 친화)**로 28.96 PSNR, GuidedSR(116M)·CoReFusion(46M) 능가. 학습 중 RGB 무작위 드롭 → RGB 결손에 강건.
- **RGB-D-Thermal 체계적 리뷰 (arXiv 2305.11427, 2023)** [S14] — 융합 레벨(Late/Middle/Early/Overlay/Feature). **캘리브레이션: 가열된 bi-material 체커보드로 스테레오 캘리브** → RGB·thermal 둘 다 패턴 검출. LWIR은 픽셀 피치가 커서 저해상이 물리적 한계(회피 불가). 2012~2022 이 분야 논문 70편(작지만 성장).
- **신선식품 표면온도 VIS+thermal 융합 (ScienceDirect 2024)** [S18] — 연속 표면온도 모니터링 선례.

**실무 포인트**: MLX90614(단일점, FOV 5°)는 솥 중심 **중심온도 스칼라 피처**로, MLX90640(32×24)은 **표면 온도분포 채널**로. RGB↔thermal 정합은 **가열 체커보드 캘리브** 필수.

---

## 5. 오픈소스 리포지토리

| 리포 | 용도 |
|---|---|
| `mit-han-lab/temporal-shift-module` [S8] | TSM — 엣지 시계열 인식 |
| `nus-cvml/awesome-temporal-action-segmentation` [S3] | MS-TCN·ASFormer·데이터셋 큐레이션 |
| `omron-sinicx/com_kitchens` [S10] | 탑뷰 조리 TAS 데이터셋·코드 |
| `amathislab/EPFL-Smart-Kitchen` [S15] | 다시점 ego-exo 조리 데이터셋 |
| `sushuzhi/CookingDataset` [S16] | 멀티뷰 조리 활동 데이터셋 |
| `yuanmaoxun/Awesome-RGBT-Fusion` [S17] | RGB-thermal 융합 모델(C2Former, UniRGB-IR, M-SpecGene 등) 색인 |

---

## 6. 데이터 어노테이션 전략 — Roboflow vs 자체 제작

| | Roboflow | CVAT | Label Studio |
|---|---|---|---|
| 형태 | 호스팅(freemium) | 오픈소스 self-host(+클라우드 freemium) | 오픈소스 self-host |
| 시간구간(action) 라벨 | 약함 | **강함(start/end 프레임)**[S19] | 있음(timeline)[S20] |
| 멀티모달/센서퓨전 | 미지원[S20] | **지원(멀티캠)**[S20] | 제한적 |
| AI 보조 | Foundation Model Assistant[S18] | **SAM-2 + 보간(수작업 −90%)**[S19] | 있음 |
| 학습·배포 내장 | **있음**[S18] | 없음 | 없음 |
| export | YOLO/COCO | 다양 | 다양 |

- **분류/bbox만(접근 B)** → **Roboflow**가 빠르고 versioning·증강·배포까지 원스톱.
- **시간구간(접근 C) + RGB+thermal 멀티모달** → **CVAT**. 시간구간 라벨 + 센서퓨전 + SAM2 보간이 결정적. (센서퓨전 최상위는 Supervisely/Encord/CVAT[S20].)
- **팁**: MLX90614 온도·시간으로 **약지도(weak) 라벨 자동 생성** → 수작업량 대폭 절감.

> 🔄 **정정 (3차 조사 이후, 2026-07-12)**: 모델이 **whole-frame 3단계 분류(OnionBot식)** 로 확정되면서 위 "CVAT 필수" 근거가 약해짐. 실제 라벨링은 **프레임당 단계 1개**(bbox·픽셀·조밀 시간구간 아님)라 CVAT의 강점(SAM2·다객체·시간구간)이 거의 안 쓰임. 또 단계 라벨은 **RGB 타임라인에 한 번만** 붙이면 동기화된 thermal에 그대로 적용 → "멀티모달 어노테이션" 부담도 낮음. **결론: 도구가 핵심이 아니라 온도·시간 약지도 스크립트가 본체이고 GUI는 검수용.** whole-frame 분류 검수는 **Label Studio 또는 CVAT(무난, 큰 차이 없음)**, 거품 탐지(bbox) 보조신호를 넣을 때만 그 부분을 **Roboflow**. → CVAT는 "무난한 기본값"이지 유일 정답 아님. 상세: `research-methodology-3-open-questions.md` §4, `gt-definition-design.md` §4.

---

## 7. 이 과제 권장 로드맵 (정답 미정 대응)

1. **GT 정의 = 도미 논문[S2] 방식 이식**: 국/탕별로 소규모 실측(중심온도·색·시간, 필요시 관능) → 클러스터링으로 "미완/완료/과조리" 이산 단계 확정. MLX90614 온도 + 시간 + CIELAB 색이 GT 신호. → 접근 A가 B의 라벨러가 됨.
2. **PoC = 접근 B**: 탑뷰 RGB + MLX90614 스칼라 → DenseNet-121/EfficientNet 분류, TensorRT 배포. 단일 메뉴 먼저.
3. **thermal 추가**: RGB-T 4채널 early fusion[S11]로 성능 이득 검증. 가열 체커보드 캘리브[S14].
4. **접근 C 승격(선택)**: 프레임 분류가 불안정하면 TSM 또는 MS-TCN/ASFormer로 단계 세그멘테이션. 어노테이션은 CVAT.
5. **다시점**: 탑뷰 주(主), oblique 보조. late fusion.
6. **어노테이션**: 온도/시간 약지도 스크립트(본체) + GUI는 검수용(Label Studio 또는 CVAT, 무난한 쪽). 거품 bbox 넣을 때만 Roboflow. (§6 정정 참고 — CVAT 필수 아님.)

---

## 참고문헌 (소스)

- [S0] CaptainCook4D — https://www.emergentmind.com/topics/captaincook4d
- [S1] Food State Recognition using Deep Learning (IEEE Access 2022) — https://www.researchgate.net/publication/366231700
- [S2] Cooking doneness of deep-fried golden pompano (ScienceDirect 2025) — https://www.sciencedirect.com/science/article/abs/pii/S221242922500820X
- [S3] awesome-temporal-action-segmentation — https://github.com/nus-cvml/awesome-temporal-action-segmentation
- [S4] Cooking State Recognition (Inception, arXiv 1805.09967) — https://arxiv.org/pdf/1805.09967
- [S5] NHK Recipe Dataset (arXiv 2507.17232) — https://arxiv.org/html/2507.17232v1
- [S6] TSM (NVIDIA Jetson project) — https://developer.nvidia.com/embedded/community/jetson-projects/tsm_online
- [S7] Resource-Efficient RGB-Only Action Recognition (arXiv 2602.10818) — https://arxiv.org/pdf/2602.10818
- [S8] mit-han-lab/temporal-shift-module — https://github.com/mit-han-lab/temporal-shift-module
- [S9] TAS survey (arXiv 2210.10352) — https://arxiv.org/pdf/2210.10352
- [S10] COM Kitchens (ECCV 2024) — https://link.springer.com/chapter/10.1007/978-3-031-73650-6_8 · 코드 https://github.com/omron-sinicx/com_kitchens
- [S11] Food Image Segmentation w/ Color+Thermal (Sensors 2023) — https://pmc.ncbi.nlm.nih.gov/articles/PMC9860575/
- [S12] US Patent 10819905 (cooking temp data fusion) — https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10819905
- [S13] SwinFuSR (CVPRW 2024) — https://openaccess.thecvf.com/content/CVPR2024W/PBVS/papers/Arnold_SwinFuSR_..._paper.pdf
- [S14] RGB-D & Thermal Fusion 리뷰 (arXiv 2305.11427) — https://arxiv.org/pdf/2305.11427
- [S15] EPFL-Smart-Kitchen-30 — https://github.com/amathislab/EPFL-Smart-Kitchen (arXiv 2506.01608)
- [S16] CookingDataset — https://github.com/sushuzhi/CookingDataset
- [S17] Awesome-RGBT-Fusion — https://github.com/yuanmaoxun/Awesome-RGBT-Fusion
- [S18] 신선식품 VIS+thermal 융합 (ScienceDirect 2024) — https://www.sciencedirect.com/science/article/abs/pii/S0925521424005994
- [S19] Video Annotation Guide (CVAT Blog 2026) — https://www.cvat.ai/resources/blog/video-annotation-guide
- [S20] Best Video Labeling Tools 2026 — https://www.labellerr.com/blog/best-video-annotation-tools-robot-manipulation/
- [S24] VGG Fine-tuning Cooking State (arXiv 1905.08606) — https://arxiv.org/pdf/1905.08606
- 어노테이션 비교: Roboflow vs CVAT — https://roboflow.com/compare-labeling-tools/cvat-vs-roboflow-annotate
