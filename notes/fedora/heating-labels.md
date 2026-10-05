# fedora/heating-labels — 온도 곡선 분석·D-041 라벨·세션 판정·상태/주간 점검 (2026-10-05)

사용자 요청: 조리 데이터가 오기 전에 Fedora 쪽 코딩을 최대한 당겨 둔다. D-041(표준 레시피 기준점 + 온도 곡선 환산)을 코드로 옮겼다.

## 변경 (`research/`만)
- `soupdata/heating.py`: PT100 곡선 → 끓기 시작(뜨거운 평탄 구간 — 끓는점 가정 없음), 끓는 구간 온도와 보정 의심 경고,
  75 ℃ `hold_s` 유지 시작·충족, 40→80 ℃ 가열 속도, 질량이 있으면 열량(kW), 조리값 C₁₀₀ 누적, 끓은 뒤 증발 추정(뚜껑 덮으면 안 함).
- `soupdata/labels.py`: `LabelRules`(done_start = boil | temp:X | c100:X, overcooked = mark | boil+N | evap:F | c100:X | none),
  `build_objective`, `agreement`(관능−객관 시간차, 맛보기 일치). 기존 Pi 사건 라벨은 관능 라벨로 유지.
- `soupdata/dataset.py`: `label`=객관, `label_sensory`=관능, `boundary_dist_s`, `c100_cum`·`min_since_boil`·`evap_frac_est` 열,
  `PROBE_COLUMNS`(탐침 없는 모델 입력에서 뺄 열), `label_rules.json`, 판정 반영(`hold`·`drop` 제외, `--require-review`).
- `soupdata/review.py` + `soupctl.py review`: 판정은 `review.jsonl`에 덧붙이기(카탈로그는 밤마다 재생성되어 손 수정이 지워지던 구멍 해결).
- QC 보고서 "가열 곡선" 절, 카탈로그 열(끓기 시작·끓는 구간 ℃·C값·증발·판정), 요약 이미지(끓음 자동 선·75 ℃ 참고선·보정 경고).
- `soupctl.py status`(한눈에 보기), `weekly`(주간 점검), 야간 작업에 Jetson 여유 기록·150 GB 경고·금요일 주간 점검.

- `soupdata/baseline.py` + `soupctl.py baseline`: 문헌 조사 (A)안 평가 틀 — 트랙 trivial/thermal/probe/all, LOSO, 완료 알림 시각 오차·
  조기 경보·놓침, 학습 세션 수 곡선. 탐침 없는 트랙에 탐침 특징이 섞이면 멈춘다. 전부 빈 특징 열은 학습 전에 뺀다.

## 검증
- `research/.venv/bin/python -m pytest -q research/tests` **34 passed**(새: 가열 곡선 4, 규칙·판정·CLI, 기준 모델 4).
- 기준 모델 합성 검증(불 세기 6~16 ℃/분 6세션): 시간만 쓴 모델 완료 알림 오차 중앙 139초(조건 모를 때 166초·조기 경보 33%),
  열화상 모델 40초, 탐침 42초 — 평가 틀이 차이를 잡는다(합성이라 실제 성능 아님).
- 가짜 곡선(10 ℃/분, 480초 끓음): 끓기 시작 472초, 75 ℃ 1분 391초(정답 390), 가열 속도 10.0, 증발·C값 손계산과 일치.
- **실물 9/28 세션:** 끓기 시작 8.8분 자동 검출, "끓는 구간 93.5 ℃ — PT100 보정·탐침 깊이 확인" 자동 경고(Jetson 노트의 사람 기록과 일치).
- 발견: 테스트 가짜 세션이 Jetson 기록기 대기열(64)을 넘겨 샘플을 조용히 버리고 있었음 → 테스트에서 대기열을 넉넉히(실제 장비 동작은 그대로).

## 남은 일
- 포장지 문구·교수 확인 뒤 `label_rules.json` 확정(기본 `boil`/`mark`는 잠정).
- 물 가열 실험으로 PT100 보정(`calibration.json`)과 증발 추정 검증(계량컵 있으면).

## 후속 (같은 날, `fedora/camera-qc`)
- `soupdata/camera.py`: 프레임 선명도(라플라시안 분산)·밝기·화면 정지 점검과 솥 영역 L*a*b*·ΔE·움직임 특징. 축소 디코드 약 6 ms/프레임.
  9/28 실측: GMSL2 ① 선명도 0.8(초점 완전 흐림), **GMSL2 ②도 끓기 시작 후 350→20~160(김 서림)**, Gemini 컬러 ≈1.
  → 판정 기준: 중앙값 < 5 = 흐림·가림, 앞 2분 기준의 30% 미만이 5% 넘으면 김 서림. 9/28에서 세 카메라 모두 맞게 경고.
- `qc.auto_flags`: 기본 4스트림 누락, 미저장 >1%, 최대 수신 간격, 카메라 경고, 가열 곡선 경고, Pi 내보내기·조건·정답 사건·시계 오차 → QC 맨 위,
  카탈로그 `auto_flags`, 요약 이미지 카메라 줄 표시(⚠ 흐림 / ⚠ 김 N분~).
- 야간: 최근 7일 세션의 Pi 기록을 다시 받아 **바뀐 것만 저장**하고 그 세션 QC·요약을 다시 만든다(다음 날 사후 입력 반영 — 구멍 수정).
- 야간 작업 전용 폴더(runtime worktree, 원격 main 고정) — `notes/fedora/server-setup.md`.
- `soupctl heating`(물 실험·반복 비교 표·겹친 그림·출력별 평균±SD), `soupctl calibrate`(2점/1점 → calibration.json, 백업).
- 데이터셋에 `rgb_feat_<카메라>_<지표>` 1 Hz 열, 기준 모델 트랙을 thermal/camera/noprobe로 나눔(센서별 기여 비교).
- `research/soup` 짧은 실행기, `~/soup-data/settings.json` 기본 주소, `docs/daily-routine.md`.
- 테스트 36개 통과.
- **라벨링 프로그램은 따로 만들지 않는다(사용자 질문 답):** 관능 = Pi 버튼(사후 입력 포함), 객관 = PT100 곡선 자동(D-041),
  판정 = 요약 이미지 + `review`. 화면 속 물체(거품 등) 표시가 필요해지면 Label Studio·CVAT 같은 기존 도구를 쓴다.
