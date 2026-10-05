# 문헌 조사 — 소고기무국(레토르트 재가열) 조리 상태 3단계 분류 모델 설계

> 작성 2026-10-05(Fedora, 웹 문헌 조사 — 확인 수준 표기를 반드시 보고 인용할 것) · 범위: 비전/열화상 도네스 인식, RGB+저해상 열+스칼라 온도 융합, 소량 데이터용 시간 모델,
> ordinal·time-to-event·라벨 노이즈, 경량 백본.
> 선행 문서(`docs/model-architecture.md`, `gt-definition-design.md`, `research-methodology*.md`, `cooking-protocol.md`,
> `notes/data/bench/summary.md`)를 먼저 읽고 **중복을 피하고 비판**하는 데 초점을 뒀다.
> 표기: **[확인]** = 원문 초록/공식 페이지에서 직접 확인, **[부분]** = 서지는 확인했으나 본문(수치 일부)은 2차 출처,
> **[추정]** = 본 보고서 작성자의 계산·경험치(문헌 수치 아님), **[미확인]** = 확인 못 함.

---

## 0. 결론 요약 (TL;DR)

1. **이 과제의 유효 표본 수는 프레임 수가 아니라 세션 수(16~20)이고, 경계(미완→완료, 완료→과조리)는 세션당 1회씩 = 경계당 16~20개뿐이다.**
   따라서 현 설계(EffB0 ×2 end-to-end + thermal TCN + gated fusion + CORN + 보조 회귀)는 용량 대비 데이터가 1~2자릿수 부족하다.
   다중모달 네트워크는 단일모달보다 쉽게 과적합한다는 결과(Wang et al., CVPR 2020)가 그대로 적용된다.
2. **레토르트 재가열은 "익힘"이 아니라 "가열+졸임"** 이다. 미완→완료는 온도(PT100·열화상)와 끓음 시작으로, 완료→과조리는 끓은 뒤 누적 시간·수위/색 농축으로 대부분 설명될 가능성이 높다.
   → **(A) 수작업 피처 + 그래디언트 부스팅/로지스틱(CPU)** 이 11월 말까지 가장 확실한 결과이며, 이것이 넘어야 할 기준선이 된다.
3. **(B) 동결된 사전학습 인코더(DINOv2/v3 ViT-S, 21M) 임베딩 + 작은 시간 헤드**가 비전의 기여를 보여 주는 현실적 2단계다.
   임베딩은 한 번만 뽑으면 되므로 GPU 드라이버가 없어도 CPU로 서브샘플(0.2~0.5 fps) 추출이 가능하다[추정].
4. **(C) 경량 백본 end-to-end 미세조정**은 GPU 드라이버 확보 후 선택 과제. 16~20세션으로 (B)를 확실히 이기기 어렵다고 보고, ablation 한 줄로 두는 것을 권한다.
5. **평가 단위를 세션으로**: leave-one-session-out(LOSO) 또는 조건 그룹 k-fold, 지표는 프레임 정확도보다 **완료 알림 시각 오차(초)·조기 오경보율**·경계 ±60 s 제외 macro-F1.
6. **"완료 = PT100 ≥ 75 °C" 같은 객관 정의를 쓰면, PT100 입력 모델은 정의를 그대로 외운다(순환).** 연구 질문("카메라/열화상이 탐침을 대체할 수 있나")을 위해 **탐침 미사용 모델을 별도 보고**해야 한다.

---

## 1. 참고문헌 (28편)

### 1-1. 조리 도네스·조리 상태 인식 (비전/열화상)

**[R1] Zhang, Y., Hou, M., Peng, L., Liu, D., Qiu, M., Zheng, O., Chen, K., Sun, Q., Liu, S. (2025).** Construction of an intelligent recognition system for the cooking doneness of deep-fried golden pompano (*Trachinotus ovatus*) based on deep learning. *Food Bioscience*. https://www.sciencedirect.com/science/article/abs/pii/S221242922500820X **[부분]**
- 관능평가+이화학 지표로 4단계(raw/medium rare/fully cooked/overcooked) 정의 → DenseNet-121 90%, 미세조정 GP-Net 약 +6%p.
- 재사용: 이미 GT 설계의 근거. **색 상관 0.93은 튀김 표면 갈변 기반이라 국물에는 전이 불가**(기존 문서도 인정). 클러스터로 단계를 정하는 절차만 차용.
- 규모: DenseNet-121 ≈ 8M params(일반 수치). 데이터는 이미지 단위 분할로 보이며 세션 분할 여부 미확인.

**[R2] Wang, H., Lin, Y., Cheng, J.-H. (2025).** Constructing a deep learning-assisted smartphone application for intelligent recognition of steak doneness during cooking. *Meat Science*. DOI: 10.1016/j.meatsci.2025.109995 · https://pubmed.ncbi.nlm.nih.gov/41241987/ **[부분]**
- 이미지 1,803장 + 153샘플 이화학 특성, 8개 CNN 비교 → DenseNet121 95.30±1.48%, 모바일 앱 실사용 91.93%. 열화상(30~100 °C)은 분석 보조로 제시.
- 재사용: "실험실 정확도 → 실사용에서 3~4%p 하락" 패턴. 이 과제도 LOSO 성능을 실사용 기대치로 보고할 것.

**[R3] Gupta, J., Goyal, S., Kumar, A., Jindal, I. (2025).** Real-Time Cooked Food Image Synthesis and Visual Cooking Progress Monitoring on Edge Devices. arXiv:2511.16965. https://arxiv.org/abs/2511.16965 **[확인]**
- 30레시피·1,708세션. 목표 완료 이미지를 생성하고 현재 프레임과의 유사도(CIS)로 완료 판단. 5 TOPS NPU에서 유사도 0.3 s/장.
- 재사용: **"현재 프레임 vs 기준 상태 거리"** 아이디어 → 본 과제에서는 생성 대신 *세션 첫 프레임/끓음 시작 프레임과의 임베딩 거리*를 피처로 쓰면 된다(조명 차이 상쇄). 기존 문서 §5의 "타깃 유사도 peak"의 출처.

**[R4] (저자 미확인) (2026).** Temporal visual feature-guided cooking state detection for autonomous baking. *Journal of Food Measurement and Characterization*. https://link.springer.com/article/10.1007/s11694-026-04917-3 **[부분 — 저자·모델 크기 미확인]**
- 같은 샘플의 **초기(raw) 이미지를 시간 기준**으로 삼아 현재 프레임·기준·시간 임베딩을 융합, 3단계(raw/partially/cooked) 95.7%.
- 재사용: R3과 같은 결론 — **세션 내부 기준 대비 변화량**이 절대 외형보다 강건하다. 본 과제 (B)안의 "Δ임베딩" 피처 근거.

**[R5] Kawaharazuka, K., Kanazawa, N., Obinata, Y., Okada, K., Inaba, M. (2024).** Continuous Object State Recognition for Cooking Robots Using Pre-Trained Vision-Language Models and Black-box Optimization. *IEEE RA-L*. arXiv:2403.08239. https://arxiv.org/abs/2403.08239 **[확인]**
- VLM 이미지-텍스트 유사도의 시간 곡선을 시그모이드로 맞추고 프롬프트 가중치를 블랙박스 최적화. 물 끓음·버터 녹음·계란·양파 볶음.
- 재사용: **물 끓음을 학습 없이(zero-shot) 연속값으로 추정**한 선례. CLIP/SigLIP 유사도를 `boil_intensity`의 라벨 없는 대체 신호로 실험해 볼 가치. 단 정확도는 정량 비교 약함.

**[R6] Kanazawa, N., Kawaharazuka, K., Obinata, Y., Okada, K., Inaba, M. (2023).** Recognition of Heat-Induced Food State Changes by Time-Series Use of Vision-Language Model for Cooking Robot. IAS-18. arXiv:2309.01528. https://arxiv.org/abs/2309.01528 **[확인]**
- R5의 전신. 가열에 따른 상태 변화를 VLM 시계열로 인식, 프롬프트·이미지 영역(ROI) 선택이 성능을 좌우.
- 재사용: ROI 크롭(솥 영역)이 전체 프레임보다 낫다는 실무 교훈.

**[R7] Sheikh, H., George, K., Nobari, T., Panangadan, A. (2025).** Design and Development of a Real-Time Camera-based Smart Cooking Assistant. IEEE IRI 2025. https://www.fullerton.edu/ecs/faculty/apanangadan/publications/IEEE_IRI_2025__smart_cooking_assistant.pdf **[확인]**
- RPi5 + AI HAT+, YOLO11 분류, 1,339장으로 6개 조리 단계. 선행작은 파스타 끓이기(빈 냄비→물→끓는 물→파스타→익은 파스타)를 RGB+열화상+IR 센서로 330장.
- 재사용: "끓는 물" 상태는 소량 이미지로도 쉽게 분류됨 → **끓음 감지 자체는 어려운 문제가 아님**. 어려운 것은 완료↔과조리.
- 주의: 이미지 단위 평가라 세션 간 일반화 수치로 보기 어려움.

**[R8] Khan, A. M., Ashrafee, A., Sayera, R., Ivan, S., Ahmed, S. (2022).** Rethinking Cooking State Recognition with Vision Transformers. ICCIT 2022. arXiv:2212.08586. https://arxiv.org/abs/2212.08586 **[확인]**
- Cooking State Recognition Challenge(식재료 손질 상태)에서 ViT 전이학습 94.3%(기존 Inception ~76% 대비).
- 재사용: 사전학습 ViT 특징이 조리 상태에 잘 전이됨 → (B)안 근거. 단 도네스가 아닌 손질 상태.

**[R9] Ben Jmaa, A. B., Hanon, C., Parfait, J.-Y., Debaste, F. (2026).** Prediction of cooking-induced food transformations: A survey and outlook toward machine learning. *Journal of Food Engineering*. https://www.sciencedirect.com/science/article/abs/pii/S026087742600213X **[부분]**
- 조리 변환의 물리 모델 분류 + ML 공식화 + **하이브리드/물리정보 결합 로드맵**.
- 재사용: (A)안에 물리 기반 피처(누적 끓음 시간 × 출력 ≈ 증발량 대리변수) 넣는 근거. 식품공학 학과 과제의 서술 틀로 유용.

**[R10] Barrington, H. et al. (2025).** Parallel and High Throughput Reaction Monitoring with Computer Vision. *Angewandte Chemie Int. Ed.* DOI: 10.1002/anie.202413395 · https://onlinelibrary.wiley.com/doi/10.1002/anie.202413395 **[부분]**
- Kineticolor: ROI 평균색 → CIELAB → **첫 프레임 대비 ΔE 시계열**로 반응 속도론 추출.
- 재사용: 국물 ROI의 L\*, a\*, b\*, ΔE(t)와 그 기울기를 (A)안 색 피처로. 조명 고정이 전제(프로토콜 §2 준수).

**[R11] 식품의약품안전처 (보도·홍보 자료).** 대량조리음식 식중독 주의요령 — "육류 중심온도 75 ℃ 1분 이상", "보관 음식은 75 ℃ 이상 재가열". https://mfds.go.kr/brd/m_827/view.do?seq=3607 **[부분 — 문구는 검색 요약 기준, 원문 대조 필요]**
- 재사용: "완료" 객관 정의(PT100 ≥ 75 ℃ 유지)의 규제 근거. 단 관능 "완료"(무 연화·간)와는 별개 → 두 정의의 시간차를 분석 결과로 보고.

### 1-2. 다중모달 융합 (소량 데이터·공정 모니터링)

**[R12] Wang, W., Tran, D., Feiszli, M. (2020).** What Makes Training Multi-Modal Classification Networks Hard? CVPR 2020. arXiv:1905.12681. https://arxiv.org/abs/1905.12681 **[확인]**
- 다중모달 네트워크가 최고 단일모달보다 **오히려 성능이 낮은** 현상 → 원인은 용량 과다로 인한 과적합 + 모달별 일반화 속도 차이. Gradient Blending 제안.
- 재사용: **이 과제에 가장 중요한 경고.** 모달별 단독 모델을 먼저 만들고, 융합은 late(확률/임베딩 수준)·저용량으로, 모달별 성능 대비 이득을 반드시 보고.

**[R13] Petrich, J., Snow, Z., Corbin, D., Reutzel, E. W. (2021).** Multi-modal sensor fusion with machine learning for data-driven process monitoring for additive manufacturing. *Additive Manufacturing* 48, 102364. DOI: 10.1016/j.addma.2021.102364 **[부분]**
- 레이어 이미지·음향·다중분광 방출·스캔 경로를 융합, 결함 이진 분류 98.5%(4-fold).
- 재사용: 산업 공정 모니터링의 표준 패턴 = **모달별 특징 추출 → 정렬된 공통 시간/공간 격자 → 소형 분류기**. 본 과제의 1 Hz 공통 격자 설계와 동일.

**[R14] Orlova, S., Cavagnero, N., Dubbelman, G. (2026).** Towards Data-Efficient Video Pre-training with Frozen Image Foundation Models. CVPR 2026 Workshops (CV4Smalls). arXiv:2605.19137. https://arxiv.org/abs/2605.19137 **[확인]**
- **동결 이미지 인코더 + 순환 시간 모듈 + attentive readout**만 학습해도 비디오 사전학습 모델에 필적. SSv2에서 DINOv3(66.9)·DINOv2(65.6)가 비디오 사전학습 인코더(62.7)보다 높음(검색 요약 수치 **[부분]**).
- 재사용: (B)안의 직접 근거 — 비디오 파운데이션 모델(VideoMAE/V-JEPA) 대신 **이미지 인코더 + 가벼운 시간 헤드**로 충분.

### 1-3. 시간 모델·특징 추출기

**[R15] Oquab, M., Darcet, T., Moutakanni, T., et al. (2023/2024).** DINOv2: Learning Robust Visual Features without Supervision. TMLR. arXiv:2304.07193. https://arxiv.org/abs/2304.07193 **[확인]** (TMLR 게재 표기는 [부분])
- 자기지도 범용 특징, **동결 + 선형 분류기**로 평가. ViT-S/14 21M, ViT-B/14 86M. 코드·가중치 **Apache-2.0**.
- 재사용: (B)안 1순위 인코더(라이선스 단순, timm/torch.hub 지원).

**[R16] Siméoni, O., Vo, H. V., Seitzer, M., Baldassarre, F., Oquab, M., et al. (2025).** DINOv3. arXiv:2508.10104. https://arxiv.org/abs/2508.10104 · https://github.com/facebookresearch/dinov3 **[확인]**
- Gram anchoring으로 조밀 특징 품질 개선. 증류 모델: ViT-S/16 21M, ViT-S+/16 29M, ViT-B/16 86M, ConvNeXt-T 29M 등. **DINOv3 License(별도, 가중치 다운로드 등록 필요)**.
- 재사용: DINOv2 대안. ConvNeXt-T 증류판은 TensorRT 변환이 ViT보다 수월할 수 있음[추정].

**[R17] Tschannen, M., Gritsenko, A., Wang, X., et al. (2025).** SigLIP 2: Multilingual Vision-Language Encoders. arXiv:2502.14786. https://arxiv.org/abs/2502.14786 **[확인]**
- ViT-B 86M ~ g 1B. 텍스트 정렬 특징 → R5식 zero-shot "끓는 국/졸아든 국" 프롬프트 점수 산출 가능.
- 재사용: (B)안 보조 인코더, 텍스트 프롬프트 점수를 피처로.

**[R18] Tong, Z., Song, Y., Wang, J., Wang, L. (2022).** VideoMAE: Masked Autoencoders are Data-Efficient Learners for Self-Supervised Video Pre-Training. NeurIPS 2022. arXiv:2203.12602. https://arxiv.org/abs/2203.12602 **[확인]**
- 3~4천 개 영상으로도 자기지도 사전학습 가능, UCF101 91.3%.
- 재사용 판단: **본 과제에는 과함.** 16클립×224² ViT-B 입력은 학습 VRAM·Jetson 비용이 크고, 조리 변화는 수 분 단위라 16프레임 클립의 모션 모델링 이점이 작다.

**[R19] Assran, M., et al. (2025).** V-JEPA 2: Self-Supervised Video Models Enable Understanding, Prediction and Planning. arXiv:2506.09985. https://arxiv.org/abs/2506.09985 **[확인]**
- 100만 시간 이상 영상 사전학습, EK-100 행동 예측 등. 동결 인코더 + attentive probe 평가.
- 재사용 판단: 최소 모델도 ViT-L급(~300M)이라 16 GB에서 특징 추출은 가능하나 Jetson 배포는 비현실[추정]. **문헌 비교용으로만 언급.**

**[R20] Dempster, A., Schmidt, D. F., Webb, G. I. (2021).** MiniRocket: A Very Fast (Almost) Deterministic Transform for Time Series Classification. KDD 2021. arXiv:2012.08791. https://arxiv.org/abs/2012.08791 **[확인]**
- 고정 합성곱 커널 변환 + 선형 분류기, UCR 109개 데이터셋 학습·평가 10분 미만(CPU).
- 재사용: PT100·열화상 요약 시계열(최근 60~300 s 창)을 **CPU에서 즉시** 분류하는 강한 기준선. 수작업 피처와 비교 ablation.

**[R21] Grinsztajn, L., Oyallon, E., Varoquaux, G. (2022).** Why do tree-based models still outperform deep learning on typical tabular data? NeurIPS 2022 Datasets & Benchmarks. arXiv:2207.08815. https://arxiv.org/abs/2207.08815 **[확인]**
- ~10k 샘플급 이질적 표 데이터에서 트리 모델(XGBoost/RF) 우위.
- 재사용: (A)안(온도·열·색 요약 피처 표)에서 GBDT를 기본으로 두는 근거.

**[R22] Hollmann, N., Müller, S., Purucker, L., et al. (2025).** Accurate predictions on small data with a tabular foundation model (TabPFN). *Nature* 637, 319–326. https://www.nature.com/articles/s41586-024-08328-6 **[확인]** (저자 일부는 [부분])
- ≤10k 샘플·≤500 피처에서 튜닝된 GBDT보다 우수, 단일 forward pass.
- 재사용: (A)안 대안 분류기. 세션 내 프레임을 10~30 s 간격으로 서브샘플하면 10k 이하로 들어옴. **세션 그룹 분할을 지켜야** 이점이 의미 있음.

### 1-4. Ordinal·time-to-event·라벨 노이즈

**[R23] Shi, X., Cao, W., Raschka, S. (2023).** Deep neural networks for rank-consistent ordinal regression based on conditional probabilities (CORN). *Pattern Analysis and Applications* 26(3), 941–955. DOI: 10.1007/s10044-023-01181-9 · arXiv:2111.08851 **[확인]**
- 조건부 학습 부분집합 + 연쇄법칙으로 rank 일관성 보장, CORAL의 가중치 공유 제약 제거.
- 재사용: 현 설계 유지 가능. 단 **3클래스면 이진 로짓 2개**라 CE 대비 이득이 작을 수 있음 → CE vs CORN ablation 한 줄. GBDT에서도 "P(y>0), P(y>1|y>0)" 두 이진 모델로 동일 구조 구현 가능.

**[R24] Yèche, H., Pace, A., Rätsch, G., Kuznetsova, R. (2023).** Temporal Label Smoothing for Early Event Prediction. ICML 2023 (PMLR 202). arXiv:2208.13764. https://proceedings.mlr.press/v202/yeche23a/yeche23a.pdf **[확인]**
- 사건 경계 근처에서 라벨 스무딩 강도를 키워 **경계 노이즈와 오경보**를 줄임. 저오경보 구간에서 놓친 사건 최대 2배 개선(임상).
- 재사용: 사람이 누른 `done_start`/`overcooked` 시각의 ±수십 초 오차를 그대로 반영하는 손실. 완료 알림이 "오경보 최소화" 문제라는 점도 동일.

**[R25] Li, Z., Abu Farha, Y., Gall, J. (2021).** Temporal Action Segmentation from Timestamp Supervision. CVPR 2021. arXiv:2103.06669. https://arxiv.org/abs/2103.06669 **[확인]**
- 사건 시각(타임스탬프)만으로 프레임 라벨을 생성, 경계는 모델 출력으로 추정 + 타임스탬프에서 멀어질수록 확률이 단조 감소하는 confidence loss.
- 재사용: 맛보기 판정(2분 간격 이산 관측)을 **타임스탬프 감독**으로 보고, 경계는 그 사이 구간에서 추정 — 현재 "버튼 시각 = 경계"라는 가정을 완화.

**[R26] Wiegrebe, S., Kopper, P., Sonabend, R., Bischl, B., Bender, A. (2024).** Deep learning for survival analysis: a review. *Artificial Intelligence Review* 57, 65. arXiv:2305.14961 **[확인]**
- 이산시간 생존모형 = 구간별 이진 분류로 환원, DL에서 가장 널리 쓰임.
- 재사용: "완료까지 남은 시간" 문제를 **이산시간 hazard**(매 30 s 구간마다 "이번 구간에 완료 시작?")로 정식화 → 3단계 분류와 일관되고, 완료 알림 시각을 직접 최적화. 세션 수가 적어도 구간 수는 많아 학습 가능.

### 1-5. 경량 백본 (Jetson 배포)

| # | 모델 | 서지 | Params | 연산 | ImageNet top-1 |
|---|---|---|---|---|---|
| R27a | MobileNetV3-Small | Howard et al., ICCV 2019, arXiv:1905.02244 | 2.5M [일반 수치] | ~0.06 GMACs | 67.4% |
| R27b | EfficientNet-B0 | Tan & Le, ICML 2019, arXiv:1905.11946 | 5.3M [확인 timm] | 0.4 GMACs | 77.1% (timm ra 레시피 수치 다름) |
| R27c | MobileNetV4-Conv-S | Qin et al., ECCV 2024, arXiv:2404.10518 | 3.8M [확인 timm] | 0.2 GMACs | 73.8% @224 |
| R27d | RepViT-M0.9 | Wang et al., CVPR 2024, arXiv:2307.09283 | 5.5M [확인 timm] | 0.8 GMACs | iPhone12 1.0 ms에서 80%↑(M1.0급) |
| R27e | FastViT-T8 | Vasu et al., ICCV 2023, arXiv:2303.14189 | 4.0M [확인 timm] | 0.7 GMACs @256 | [미확인] |
| R27f | EfficientViT-B1 | Cai et al., ICCV 2023, arXiv:2205.14756 | 9.1M [확인 timm] | 0.5 GMACs | [미확인] |
| R28 | DINOv2/v3 ViT-S | R15·R16 | 21M | ~4.6 GFLOPs @224 [추정] | 동결 특징용 |

- 재사용: 실측 벤치(EffB0 fp16 2.05 ms, MNv3-S 0.86 ms)에서 보듯 **1 Hz 추론에서 백본 속도는 제약이 아님.** 백본 선택 기준은 속도가 아니라 *소량 데이터 과적합*과 *동결 특징 품질*이다.
- Jetson에서 ViT-S/14(21M) TensorRT fp16은 수~수십 ms 수준으로 예상되나 **미측정**[추정] → 채택 시 `notes/data/bench`에 동일 스크립트로 1회 측정 필요.
- RepViT/FastViT는 재매개변수화(reparam) 후 내보내야 TensorRT 이점이 생김. MobileNetV4 공식 TF 가중치는 미공개, timm 가중치만 있음(timm 카드 명시).

> 제외한 후보: X3D/TSM 계열(기존 조사 S7·S8에 이미 있음, 16~20세션에선 학습 불가에 가까움), LapGSR 등 guided SR(기존 조사 §3, 아래 비판 참고).

---

## 2. 후보 아키텍처 비교

공통: 입력은 1 Hz 공통 격자, 세션 단위 LOSO 평가, 출력 3단계(ordinal) + 선택적으로 이산시간 hazard.
VRAM·시간은 **[추정]**(RTX 5070 Ti 16 GB, AMP 기준, 경험치).

| 후보 | 입력 | 모델 | 학습 Params | 학습 VRAM | CPU만으로 | Jetson | 데이터 요구 | 장점 | 단점 |
|---|---|---|---|---|---|---|---|---|---|
| **A. 피처 + GBDT/로지스틱** | PT100(현재값, dT/dt 30/120/300 s, 끓음 이후 경과·누적), 열화상 요약(최대/평균/표준편차/상위 10% 평균/고온 면적), RGB ROI CIELAB·ΔE·기울기, 조건(출력·뚜껑·물) | LightGBM/XGBoost, 로지스틱, TabPFN, MiniRocket(시계열) | 수천 노드 / 수십 계수 | 불필요 | **가능(분 단위)** | 자명(ONNX/CPU) | 세션 10개부터 의미 | 해석 가능(SHAP), 빠름, 강한 기준선 | 거품·건더기 무름 등 공간 신호 손실, 피처 설계 품 |
| **B. 동결 인코더 임베딩 + 작은 시간 헤드** | RGB 2뷰 → DINOv2/v3 ViT-S 임베딩(384d) 0.2~1 fps, + A의 피처 | PCA(32~64d) → 로지스틱/GBDT, 또는 1D-TCN/GRU(≤100k params, 창 2~5분) | ≤0.1M | <2 GB (임베딩 캐시 후) | 임베딩 추출만 수 시간 [추정], 헤드 학습은 CPU 가능 | ViT-S fp16 TRT, 1 Hz 충분 [미측정] | 세션 16~20으로 가능 | 과적합 적음, 비전 기여를 정량화, 반복 빠름 | 도메인 특화 미세 신호(미세 기포)는 동결 특징이 못 잡을 수 있음 |
| **C. 경량 백본 미세조정 (단일 뷰 → 2뷰)** | RGB 224² (+열 배열 소형 CNN) | MobileNetV4-S / EffB0, 마지막 블록만 또는 전체 미세조정 + CORN | 3.8~5.3M | 4~8 GB @ batch 64 | 비현실(느림) | 실측 완료(EffB0 2 ms) | 수십 세션 권장 | 배포 단순, 도메인 적응 | 16~20세션에서 과적합·세션 암기 위험, GPU 드라이버 필요 |
| **D. 현 설계(EffB0×2 + thermal TCN + gated + CORN + aux)** | 전 모달 | 다분기 end-to-end | ~11M+ | 8~12 GB | 불가 | 실측상 가능 | 수십~수백 세션 | 최종형으로 완결적 | 데이터 대비 용량 과다(R12), boil 회귀 라벨 없음, 디버깅 어려움 |
| (참고) E. 비디오 FM 동결 + probe | 16프레임 클립 | VideoMAE-B / V-JEPA 2-L 특징 | probe만 | 추출 시 8~16 GB | 불가 | 비현실 | — | 모션 표현 | 조리 시간척도(분)에 비해 클립이 짧고 무거움(R18·R19) |

---

## 3. 단계별 권장 계획 (11월 말 기준)

### 3-0. 공통 전처리·평가 (먼저 고정)
- **라벨 3종을 병렬 생성**: (L1) 객관 — PT100 ≥ 75 ℃ 도달 시점 = 완료 시작, `overcooked` 사건 = 과조리 시작; (L2) 관능 — `done_start`/`done_end`/`overcooked` 버튼; (L3) 맛보기 판정 이산 관측(타임스탬프 감독, R25). L1-L2 시간차 자체가 보고할 결과.
- **경계 완충대**: 각 경계 ±60 s(맛보기 간격 1~2분 반영)는 학습 가중치 축소(R24) 또는 평가 제외, 두 방식 모두 보고.
- **분할**: LOSO(세션 16~20 fold). 조건표 12조합이 세션 수와 비슷하므로 같은 조합 반복 세션은 같은 fold로 묶는 grouped k-fold도 병행. 하이퍼파라미터는 내부 fold에서만.
- **지표**: 경계 제외 macro-F1, ordinal MAE, **완료 알림 시각 오차 |t̂−t|(초) 중앙값**, 조기 오경보(완료 전 알림) 세션 비율, 과조리 경고 지연. 히스테리시스 후처리 후 수치로.
- **누수 점검**: 경과시간·조건(뚜껑·출력)만 쓴 모델을 "자명 기준선"으로 반드시 보고 — 이게 이미 높으면 다른 모달의 기여가 과장된다.

### 3-1. (A) 강한 단순 기준선 — **10월 중 확정, 반드시 완료**
- 피처(1 Hz, 창 30/120/300 s): PT100 현재값·기울기·최대값 대비, 끓음 시작 후 경과시간, **끓음 누적시간 × 인덕션 출력(증발량 대리, R9)**; 열화상 ROI 통계·고온면적 비율·공간 표준편차(끓음 시 불균일 증가 가설); RGB ROI L\*a\*b\* 평균·ΔE(세션 첫 프레임 기준, R10)·기울기, 상부 프레임 차분 에너지(거품/움직임).
- 모델: 로지스틱(ordinal 2-이진) → LightGBM → TabPFN 비교. 시계열 원신호에는 MiniRocket(R20) 비교.
- **CPU 전용으로 전부 가능** — GPU 드라이버 대기와 무관.
- 산출: SHAP으로 "어떤 신호가 완료/과조리를 가르는가" — 기존 조사 §미해결 1번(완료↔과조리 변별 신호)에 대한 직접 답.

### 3-2. (B) 동결 인코더 + 작은 시간 헤드 — **11월 주력, 현실적**
- RGB 프레임을 0.2~0.5 fps로 서브샘플(조리 변화는 분 단위), 솥 ROI 크롭, DINOv2 ViT-S/14로 CLS(+평균 패치) 임베딩 추출 후 캐시. 20시간×0.5 fps×2뷰 ≈ 72k장 → CPU 수 시간, GPU 수 분[추정].
- 헤드: (B1) PCA 32d + A의 피처 → GBDT/로지스틱; (B2) 2~5분 창 1D-TCN/GRU(≤100k params) + CORN. Δ임베딩(세션 첫 프레임·끓음 시작 프레임 대비, R3·R4)을 입력에 추가.
- 선택: SigLIP 2 텍스트 프롬프트 점수("boiling soup", "reduced soup")를 피처로(R5·R17).
- 뷰별·인코더별(DINOv2 vs v3 vs ImageNet EffB0 동결) ablation.

### 3-3. (C) end-to-end 경량 미세조정 — **선택(GPU 확보 시)**
- MobileNetV4-Conv-S 또는 EffB0, oblique 단일 뷰, 마지막 1~2 블록만 미세조정 → B 대비 이득이 LOSO에서 유의할 때만 2뷰·열 분기 추가.
- 16~20세션으로는 B를 이긴다는 보장이 없으므로 "실패해도 보고 가능한" ablation으로 위치시킨다.

### 3-4. 보고할 ablation (최소 세트)
1. 자명 기준선(경과시간·조건) vs A vs B vs (C)
2. 모달 기여: PT100만 / 열화상만 / RGB만 / 조합 — **특히 "탐침 없는" 모델(열+RGB)** 성능
3. 뷰: oblique / top / 둘 다
4. 라벨 정의: L1(객관) vs L2(관능) 학습 → 서로에 대해 평가
5. 손실: CE vs CORN, 경계 스무딩 유무(R24)
6. 조건별 분해: **뚜껑 덮음/엶**(덮으면 카메라·열화상이 뚜껑만 봄), 출력 수준
7. 학습 세션 수 곡선(4/8/12/16) — 데이터가 더 필요한지 판단 근거

---

## 4. `docs/model-architecture.md` 비판 (유지 / 변경 / 폐기)

### 유지
- **late fusion, RGB↔thermal 픽셀 정합 안 함** — 문헌(R12·R13)과 일치. 32×24 정합 이득은 불확실.
- **ordinal 3단계 + CORN, 완료는 구간 라벨** — 타당(R23). 다만 3클래스에선 이득이 작으니 CE 비교 필수.
- **EMA + 히스테리시스 후처리, 1 Hz 캐던스** — 오경보 최소화 목표와 일치(R24).
- **열배열을 스칼라가 아닌 이미지로 + 수작업 공간 피처 병행** — 특히 수작업 피처 쪽이 소량 데이터에서 먼저 쓰여야 함(TADAR 사례, 기존 조사).
- **센서 결손 시 0-임베딩 + 경고**, 온도원 메타데이터 기록 원칙.

### 변경
1. **학습 순서 뒤집기**: §6 "① RGB oblique 단독 → … → ③ 전체 fine-tune"을 **"① 피처 GBDT(A) → ② 동결 임베딩(B) → ③ 미세조정(C)"** 으로. 현재 순서는 가장 데이터를 많이 요구하는 모델로 시작한다.
2. **thermal TCN 창 16프레임(8 s) → 다중 척도(30 s / 2 분 / 5 분)**: 과조리(졸임)는 분 단위 현상이라 8 s 창은 끓음 순간 감지 외에는 정보가 부족하다.
3. **백본 선택 근거**: "속도 비제약 → 큰 모델" 논리는 *데이터 비제약*일 때만 성립. 16~20세션에선 동결 특징(DINOv2 ViT-S)이 기본, EffB0/MNv4 미세조정은 ablation(A5 대체).
4. **데이터 규모 서술**: "2~4천 프레임/메뉴", "메뉴당 20~30세션 관능 앵커"는 현 계획(16~20세션, 매 세션 관능 사건 기록)과 불일치 → 세션 수 기준으로 재서술. 앵커 수 스윕(A4: 0/10/20/30세션)은 총 세션보다 커서 수행 불가 → **학습 세션 수 곡선**으로 대체.
5. **gated fusion(AquaFusionNet식)**: 아이디어는 좋으나 게이트도 학습 파라미터라 소량 데이터에서 불안정. (A)/(B)의 GBDT가 "온도 포화 구간에서 시각 피처로 분기"를 자연스럽게 학습하므로 신경망 게이트는 C단계 이후 옵션으로.
6. **GT 정의 순환 문제 명시**: 완료를 PT100 임계로 정의하면 PT100 입력 모델은 자명해짐 → "탐침 포함" / "탐침 제외" 두 트랙으로 결과를 분리.
7. **평가 지표 추가**: §8 표에 *알림 시각 오차(초)*, *조기 오경보율*, LOSO 명시.
8. **문서 간 사실 불일치 정리**: §2.0 FOV 계산의 "지름 40 cm 솥" vs `cooking-protocol.md`의 "약 24 cm 솥"; 입력 1~2 fps vs 수집 2~10 fps; 솥 내장 센서 vs 현 프로토콜의 PT100 사용.

### 폐기(또는 보류)
- **보조 헤드 `boil_intensity` 회귀(MSE)**: 라벨 출처가 없다(사람이 강도를 기록하지 않음). 유지하려면 열화상 공간 표준편차·프레임 차분 에너지·SigLIP 점수 등 *정의된 대리값*을 먼저 정하고, 그렇지 않으면 학습 손실에서 제거하고 대시보드 표시는 규칙 기반으로.
- **A2 guided SR(LapGSR) early fusion 실험**: 32×24 실측 검증 없음 + 복잡도 큼 + 데이터 부족 → 이번 학기 범위에서 제외.
- **SSL 보조손실(rotation/jigsaw)**: 동결 파운데이션 특징(R14·R15)이 같은 목적을 더 싸게 달성 → 제외.
- **2뷰 독립 EffB0 가중치**: 동결 공유 인코더로 대체(뷰 차이는 헤드에서).

---

## 5. 단기(GPU 드라이버 대기 중) CPU 실행 가능 항목

| 작업 | CPU 가능? | 비고 |
|---|---|---|
| 동기화·1 Hz 격자·라벨 3종 생성 | 예 | 첫 우선순위 |
| (A) 피처 + LightGBM/로지스틱/TabPFN(소규모)/MiniRocket | 예 | 분 단위 |
| DINOv2 ViT-S 임베딩 추출(0.2~0.5 fps 서브샘플) | 예(수 시간)[추정] | 하룻밤 배치로 돌리고 캐시 |
| (B) 헤드 학습(PCA+GBDT, 소형 TCN) | 예 | 소형 TCN도 CPU로 충분 |
| (C) 미세조정 | 사실상 아니오 | 드라이버 설치 후 |

---

## 6. 확인하지 못한 것 / 한계

- R4 저자·모델 크기, R1·R2·R9·R13 본문 세부(페이월 403)는 검색 요약 기준. R11은 공식 페이지 문구 원문 대조 필요.
- FastViT-T8·EfficientViT-B1 top-1, MobileNetV3-Small 수치는 원문에서 직접 확인하지 않음(일반적으로 알려진 값 또는 미기재).
- ViT-S/DINOv2의 Jetson Orin Nano TensorRT 지연, CPU 임베딩 처리량, 학습 VRAM은 모두 **[추정]** — 측정 전 확정 근거로 쓰지 말 것.
- **국/탕(특히 레토르트 재가열) 도네스를 직접 다룬 논문은 이번에도 찾지 못했다.** 성능 수치는 인접 도메인(튀김·스테이크·베이킹·로봇 조리) 참고치이며, 본 과제의 결과 자체가 새 기여가 된다.
