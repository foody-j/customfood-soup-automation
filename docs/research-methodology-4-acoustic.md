# 방법론 조사 4 — 음향(소리) 센서로 끓음/조리 상태 감지

> 2026-09-11 · Jetson 세션에서 조사. 질문: **"소리 센서까지 다는 건 헤비한가? FFT로 하면 되나?"**
> 웹 검색 + 소스 12건 확인(접근 차단된 논문은 초록·인용 스니펫 기준, 아래 ⚠ 표시).
> 앞선 조사(1~3차)는 RGB·열화상 중심이었고 음향은 다루지 않았다.

---

## 0. TL;DR

- **헤비하지 않다.** USB 마이크 1개 + `arecord` + 16kHz mono WAV면 끝. 저장 약 2MB/분, 연산은 무시 가능.
  Jetson에서 Pi 관리 서버 왕복 구조와 무관하게 센서 어댑터 하나만 추가하면 된다.
- **FFT만으로 되는 범위와 안 되는 범위가 갈린다.**
  - ✅ **되는 것**: "끓기 시작(simmer onset) / 본격 끓음(rolling boil)" 구분. GE 1999 특허부터 20년 넘게
    **200~5000Hz 대역 에너지 + RMS + 시간 미분** 같은 단순 특징으로 상용화됐다(스마트 주전자·인덕션).
  - ❌ **안 되는 것**: **완료/과조리(doneness)**. 소리는 끓음 강도(열유속·거품 동역학)를 말하지 "다 익었다"를 말하지 않는다.
    소리로 doneness를 보고한 논문은 없다.
- **국/탕에서 소리의 진짜 가치** = 2차 조사가 뽑은 "끓음/거품 동역학" 신호를 **뚜껑이 닫혀 있어도, 카메라 없이도, YOLO 없이도** 얻는 가장 싼 경로.
  탑뷰 거품 탐지(YOLO aux)의 **대체재/보완재**로 자리매김하면 된다.
- **가장 큰 리스크는 알고리즘이 아니라 현장 소음**: 후드·다른 솥·주방 작업 소리. 공기 전파 마이크는 감쇠·잡음 문제가 문헌에서 명시돼 있고,
  **여러 솥이 동시에 끓는 급식 주방이면 "어느 솥 소리인지"를 마이크가 구분 못 한다.** 접촉식(피에조/AE) 센서가 SNR은 좋지만 솥 온도·장착이 문제.
- **권고**: 수집 장치에 **USB 마이크 1개를 추가해 원시 WAV를 그냥 저장**해 둔다(비용·공수 최소). 모델에 쓸지는 8단계에서 결정.
  단, 센서 구성은 "데이터 수집 시작 전 확정"이 원칙(FOV 결정과 같은 이유)이므로 **넣을지 말지는 지금 결정**해야 한다 → `notes/decisions.md` 후보.

---

## §1. 물리: 끓는 소리는 어디서 나고 어떤 대역인가

- 소리의 원천 = 증기 기포의 **생성·성장·붕괴(응축)·이탈**이 만드는 압력파. 붕괴가 가장 큰 임펄스. [S1][S5]
- 가청 대역에서 기포 관련 성분은 **수백 Hz ~ 수 kHz**. 상용 특허의 감시 대역은 **200Hz~5kHz(넓게는 8kHz)**. [S1][S6]
- **단계별 소리 변화** (물 기준, 주전자/냄비): 가열 중 조용 → 바닥 핵비등(기포가 찬물에서 붕괴 → "쉬익" 소리, **음압 최대**) →
  전이(음압 서서히 감소) → 완전 끓음(rolling boil, **일정한 정상 상태 레벨**). 즉 소리는 **끓기 직전이 가장 크고 완전히 끓으면 오히려 안정**된다. [S3][S6]
  → 단순 "소리 크면 끓음" 임계값은 틀린다. **변화율(미분)·안정화**를 봐야 한다는 것이 특허들의 공통 설계.
- 실험실 pool boiling 연구는 열유속·핵비등 시작(ONB)·CHF까지 소리로 판별 가능함을 다수 실증. [S5][S8][S9]

## §2. 단순 FFT/특징 기반 (=상용 제품 방식) ✅ [high, 물 기준]

| 출처 | 센서 | 특징 | 판별 |
|---|---|---|---|
| GE 특허군(1999) [S6][S7] | 조리기 부착 음향/진동 센서 | 밴드패스 4개(200–800 / 1500–2200 / 2200–3000 / 3200–5000Hz) 에너지, RMS, 평활 신호의 1차 미분·영기울기점 | **6상태**: pre-simmer / simmer / pre-boil / boil / boil-dry / boil-over |
| 인덕션·주전자 특허(Whirlpool·LG·Haier 등) [S1][S10] | 피에조/가속도계(구조 전달음), 200Hz–8kHz | 진동 패턴·미분 기반 | 끓음·건조가열 감지 |
| Boil Buddy(DIY, 2019) [S11] | 피에조 디스크를 솥 테두리에 클립 | 80Hz ADC, 평균·표준편차 | 끓음 여부. **센서 내열 70°C 한계**, ADC 불안정으로 실패 |

- 결론: **물/단일 냄비/조용한 환경**이면 FFT 밴드 에너지 + 미분 규칙으로 "simmer 시작·rolling boil 도달"은 충분히 잡힌다. 20년 넘게 안정된 패러다임. [S1]
- 한계: 특허들은 **자사 조리기 내장 센서**(솥 바닥·상판 진동) 전제. 우리처럼 공기 중 마이크로 임의 솥을 볼 때의 성능은 보장 안 됨.

## §3. 학습 기반 (MFCC / 스펙트로그램 + 분류기) ✅ [high, 물·실험실 기준]

- **Applied Acoustics 2013 · 2016 (⚠ 초록만)** [S3][S4] — 냄비 물 끓음을 **4단계(가열 / 핵비등 / 전이 / 막비등)** 로 라벨, **MFCC → Gaussian SVM** 이 최고. "미래 스토브에 설치 가능" 결론. 이 과제와 가장 가까운 소비자 도메인 선례.
- **주방 조리 활동 인식 (Buildings 2024, ⚠ 초록)** [S12] — 실제 가정 주방 3개월 883샘플(끓임/찜 460, 튀김/구이 423). Mel-spectrogram → **CNN이 LSTM/GRU보다 우수하고 데이터량 변동에 둔감**. 조리 "종류" 인식이지 단계는 아님.
- **주방 16종 소리 혼합 인식 (Computación y Sistemas 2022)** [S13] — 3개 소리를 SNR 3dB로 섞은 데이터. **MFCC+ANN 73% vs MSES(다중대역 스펙트럼 엔트로피)+ANN 93%**. → **잡음 섞인 주방에서는 MFCC보다 대역별 스펙트럼 엔트로피가 강함.** 우리 환경(후드·복수 솥)에 직접 시사.
- **조리 상태 음향 이벤트 (CEA 2019, ⚠ 초록)** [S14] — 실제 조리 녹음으로 MFCC/NMF+GMM. 볶기·썰기 등 이벤트 인식. 음향 단독 연구가 드물다고 명시.
- **실험실 pool boiling + DL (2022~2026)** [S5][S8][S9][S15] — 스펙트로그램 CNN/Transformer로 끓음 영역 **97.6%**, 열유속 회귀 NRMSE 0.02, **SNR 0dB까지 정확도 유지**. 단, 전부 **하이드로폰(수중) 또는 접촉식 AE 센서**. 2025 논문이 공기 마이크를 **"감쇠·잡음·감도 부족"** 으로 명시 기각. [S9]
- 공개 데이터셋: 동기화된 음향+광학+열 비등 데이터셋(Harvard Dataverse) [S15] — 실험실 히터라 도메인은 다르지만 전처리·라벨링 참고.

## §4. 국/탕·급식 주방에 적용할 때의 문제 (문헌이 답하지 않는 것)

1. **어느 솥 소리인가** — 문헌은 전부 단일 용기. 여러 솥이 동시에 끓는 주방에서 공기 마이크는 소스 분리가 안 된다. 접촉식(솥에 피에조)이면 해결되지만 솥 온도(피에조 70°C급)·장착·세척이 걸림.
2. **후드/환풍기 소음** — 광대역 정상 잡음. 배경 제거(비끓음 구간 평균 STFT 차감)는 실험실에서 유효했음[S9]. 후드는 켜짐/꺼짐이 바뀌므로 세션마다 캘리브레이션 필요.
3. **건더기·기름·뚜껑** — 거품 동역학이 물과 다르다(2차 조사의 pool boiling 외삽 경고와 동일). 뚜껑은 오히려 음향의 강점(카메라는 못 봄, 소리는 들림)[S2].
4. **doneness는 못 준다** — 완료/과조리는 끓음 유지 이후의 문제. 소리는 "끓고 있음/화력 세기"만 준다. GT 라벨은 여전히 온도+시간+관능 앵커.

## §5. 이 과제에서의 위치 — 권고

- **역할**: 도네스 주 모델의 입력이 아니라 **끓음 시작·화력 상태 보조 채널**. 2차 조사 §1(a) "끓음/거품 동역학"을 **탑뷰 YOLO 대신(또는 함께) 마이크로** 얻는 것. RGB 2뷰 + thermal + audio의 late fusion 후보 하나 추가.
- **수집 단계에서 할 것** (플랜 3단계 범위, 비용 최소):
  - **USB 마이크 1개**(I2S MEMS는 JetPack 6에서 device tree 병합이 필요하고[S16] 우리 보드는 이미 카메라 DTB 오버레이가 올라가 있어 건드리지 않는 게 안전).
  - Jetson 수집 서비스에 `audio_usb` 어댑터: `arecord`/ALSA → **16kHz mono 16bit WAV**, 세션 디렉터리에 `audio_0/session.wav` + manifest에 시작 `host_recv_ts`. 저장 ≈ 1.9MB/분, 1시간 ≈ 115MB.
  - 마이크 위치: 솥 가까이, 후드 반대편. 후드 ON/OFF를 세션 메타에 기록.
  - **처리·모델은 넣지 않는다.** 원시 WAV만 있으면 나중에 FFT든 MSES든 CNN이든 다 된다.
- **8단계에서 평가할 순서**: ① 밴드 에너지(200–800 / 1500–3000Hz) + RMS 미분으로 simmer/rolling boil 규칙 → ② MSES 또는 log-mel + 소형 CNN(YAMNet 전이학습) → ③ 도네스 모델 aux 입력 실험.
- **넣지 않을 근거**: 급식 주방에서 복수 솥 동시 조리가 기본이면 공기 마이크 데이터는 라벨 불가 잡음이 될 수 있음. 이 경우 접촉식 센서 검토 또는 음향 포기. → **현장(커스텀푸드 주방) 솥 배치와 후드 소음을 실측한 뒤 결정**하는 것이 맞다.

---

## 반박/미검증

- "SNR 0dB까지 강건" [S9]는 **하이드로폰+실험실** 결과. 공기 마이크+주방으로 외삽 불가.
- Applied Acoustics 2013/2016의 정확도 수치는 원문 접근 불가(⚠)로 확인 못 함. "Gaussian SVM 최고"만 확인.
- 국/탕(건더기 있는 탁한 국물)의 끓음 소리를 다룬 문헌은 **없음**. 전부 물·실험실 유체.

## 참고문헌

- [S1] Smart Kettle Boil Detection scout report (특허 동향: GE·LG·Whirlpool·Haier) — https://eureka.patsnap.com/blog/scout-report/smart-kettle-boil-detection-acoustic-signals-temperature-sensing-and-dry-run-prevention/
- [S2] Systems and methods for real-time monitoring of boiling fluid for food processing assistance (US 12,137,837; 뚜껑 닫힘 시 음향 유효) — https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/12137837
- [S3] A statistical pattern recognition approach for the classification of cooking stages. The boiling water case (Applied Acoustics 2013) ⚠ — https://www.sciencedirect.com/science/article/abs/pii/S0003682X1300042X
- [S4] Water boiling stages classification using acoustic features (2016) ⚠ — https://www.researchgate.net/publication/301636897
- [S5] A multi-modal approach to pool boiling dynamics: acoustic and vibration data (TSEP 2025) — https://ui.adsabs.harvard.edu/abs/2025TSEP...6403760B/abstract
- [S6] Method and apparatus for boil state detection based on acoustic signal features (US 6,118,104, GE) — https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/6118104
- [S7] Acoustic sensing system for boil state detection (US 6,236,025) · Apparatus and method for boil phase detection (US 6,433,693) — https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/6236025 · https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/6433693
- [S8] Deep learning the sound of boiling for advance prediction of boiling crisis (Cell Rep. Phys. Sci. 2021) — https://www.cell.com/cell-reports-physical-science/fulltext/S2666-3864(21)00072-2
- [S9] A non-intrusive framework using acoustic signals and deep learning for boiling diagnostics in visual-limited environments (Sci. Rep. 2026) — https://pmc.ncbi.nlm.nih.gov/articles/PMC13168418/
- [S10] Induction hob with boiling detection (WO2016041684A1) · Multi-stage structure-borne sound and vibration sensor (US20220386039A1) — https://patents.google.com/patent/WO2016041684A1/en · https://patents.google.com/patent/US20220386039A1/en
- [S11] Boil Buddy: Piezoelectric Pot Watcher (BeagleBoard 프로젝트) — https://www.beagleboard.org/projects/boil-buddy-piezoelectric-pot-watcher
- [S12] Examining Recognition of Occupants' Cooking Activity Based on Sound Data Using Deep Learning Models (Buildings 2024) ⚠ — https://doi.org/10.3390/buildings14020515
- [S13] A Comparative Study in Machine Learning and Audio Features for Kitchen Sounds Recognition (Computación y Sistemas 2022) — https://www.scielo.org.mx/scielo.php?script=sci_arttext&pid=S1405-55462022000200603
- [S14] Cooking State Recognition based on Acoustic Event Detection (CEA 2019) ⚠ — https://dl.acm.org/doi/10.1145/3326458.3326932
- [S15] Predicting boiling heat flux, HTC, and regimes non-intrusively using external acoustics and deep learning (2025, 접촉식 AE) — https://pmc.ncbi.nlm.nih.gov/articles/PMC12214782/ · Multimodal boiling dataset (Harvard Dataverse) — https://pmc.ncbi.nlm.nih.gov/articles/PMC11239449/
- [S16] Running I2S Microphone on Jetson Orin Nano: JetPack 6 pitfalls — https://zenn.dev/ktsushima/articles/jetson-orin-nano-i2s-microphone-setup?locale=en
