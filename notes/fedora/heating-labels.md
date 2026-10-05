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

## 검증
- `research/.venv/bin/python -m pytest -q research/tests` **30 passed**(새: 가열 곡선 4, 규칙·판정·CLI).
- 가짜 곡선(10 ℃/분, 480초 끓음): 끓기 시작 472초, 75 ℃ 1분 391초(정답 390), 가열 속도 10.0, 증발·C값 손계산과 일치.
- **실물 9/28 세션:** 끓기 시작 8.8분 자동 검출, "끓는 구간 93.5 ℃ — PT100 보정·탐침 깊이 확인" 자동 경고(Jetson 노트의 사람 기록과 일치).
- 발견: 테스트 가짜 세션이 Jetson 기록기 대기열(64)을 넘겨 샘플을 조용히 버리고 있었음 → 테스트에서 대기열을 넉넉히(실제 장비 동작은 그대로).

## 남은 일
- 포장지 문구·교수 확인 뒤 `label_rules.json` 확정(기본 `boil`/`mark`는 잠정).
- 물 가열 실험으로 PT100 보정(`calibration.json`)과 증발 추정 검증(계량컵 있으면).
