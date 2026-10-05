# research/ — Fedora 연구 데이터 관리·데이터셋·분석

Fedora(개발·통합 서버) 담당. 일정·범위는 `docs/roadmap-2026-11.md`, 수집 절차는 `docs/cooking-protocol.md`.

## 설치

```bash
python3 -m venv research/.venv
research/.venv/bin/pip install -r research/requirements.txt
research/.venv/bin/python -m pytest -q research/tests
```

## 데이터 위치 (Git 밖)

`$SOUP_DATA_ROOT`(기본 `~/soup-data`):

```
raw/<session_id>/        Jetson 세션 원본 그대로(검증 통과분만). 고치지 않는다
raw/.incoming/<id>/      받는 중이거나 검증 실패한 사본(원인 확인용으로 보존)
verify/<id>.json         Fedora 검증 결과(sha256 전수)
pi/<id>.json             Pi 세션 내보내기(정답 사건·조건·시계 오차)
catalog.csv              세션 목록·조건·정답 사건 수·검증 여부
```

## 세션 하나 처리 순서

```bash
R=research/.venv/bin/python
SRC=soup-jetson:/home/ubuntu/collector-data        # 기본: 네트워크(Pi 점프). 예비: /run/media/$USER/<SSD>/collector-data
$R research/tools/soupctl.py list  $SRC
$R research/tools/soupctl.py pull  $SRC <session_id>        # 종료·체크섬 완료 세션만, 검증 실패 시 확정 안 함
$R research/tools/soupctl.py pi-meta http://<pi>:8100 <session_id>
$R research/tools/soupctl.py qc <session_id> --out notes/data/experiments/YYYYMMDD_<session_id>.md
$R research/tools/soupctl.py catalog
$R research/tools/soupctl.py labels <session_id>      # 객관 라벨(PT100 곡선)·관능 라벨(Pi 사건)·차이·가열 곡선
$R research/tools/soupctl.py review <session_id> use   # 요약 이미지 보고 판정: use | hold --reason .. | drop --reason ..
$R research/tools/soupctl.py status --src soup-jetson:/home/ubuntu/collector-data   # 한눈에 보기
$R research/tools/soupctl.py summary <session_id>     # 세션 요약 이미지 → ~/soup-data/summary/<id>.png
```

## 매일 밤 자동 처리

Fedora 사용자 타이머 `soup-nightly`(01:00)가 `soupctl.py nightly`를 돌린다 — Pi DB 백업, 새 세션 반출·검증, Pi 내보내기, QC, **요약 이미지**,
카탈로그, Jetson 여유 기록(150 GB 미만이면 경고), 금요일엔 주간 점검(`~/soup-data/weekly/`).
세션 다음 날 `~/soup-data/summary/<id>.png`를 보고 김 서림·가림·탐침 이탈·사건 누락을 확인한 뒤 `soupctl.py review`로 판정을 남긴다
(카탈로그는 밤마다 다시 만들어지므로 손으로 고치지 않는다).
촬영 중이면 반출하지 않는다. 설정·관리 방법은 `notes/fedora/server-setup.md`.

## 라벨 규칙 (D-041)

정답(`label`)은 **PT100 온도 곡선 + 규칙**으로 만든다(`soupdata/heating.py`, `soupdata/labels.py`). Pi 사건 라벨은
`label_sensory`로 따로 두고 검증(시간차·맛보기 일치)에 쓴다. 규칙은 `~/soup-data/label_rules.json`(없으면 기본) 또는 `--rule`:

| 키 | 기본 | 다른 값 |
|---|---|---|
| `done_start` | `boil` — 끓기 시작(포장지 "끓을 때까지" 가정) | `temp:75`(75 ℃를 `hold_s` 유지), `c100:<분>`(조리값 목표 — 생재료 국 표준 레시피 환산) |
| `overcooked` | `mark` — Pi '과조리' 사건 | `boil+<분>`, `evap:<비율>`(증발 추정, 뚜껑 열고 질량을 적은 세션만), `c100:<분>`, `none` |
| `hold_s` / `min_boil_c` / `z` / `guard_s` | 60 / 85 / 33 / 60 | |

- 끓기 시작은 끓는점(100 ℃)을 가정하지 않고 "뜨거운 평탄 구간"으로 찾는다 → 탐침이 낮게 읽어도 시각은 찾고, 평탄 온도가 100 ℃와 2 ℃ 넘게
  다르면 **PT100 보정·탐침 깊이 경고**를 QC·요약 이미지에 띄운다.
- PT100 보정은 `~/soup-data/calibration.json` `{"pt100_0": {"a": .., "b": ..}}` — 75 ℃·C값 같은 절대 온도 규칙은 보정 후 확정.
- 데이터셋 `summary.json`의 `probe_columns`(PT100에서 나온 열)는 **탐침 없는 모델 입력에서 뺀다**(정답과 같은 출처라 순환).

## 데이터셋 버전 만들기

```bash
$R research/tools/soupctl.py build-dataset v1          # 검증 OK 세션 전부 → ~/soup-data/datasets/v1/
```
동결된 버전은 덮어쓰지 않는다(같은 이름이면 거부). 판정이 `hold`·`drop`인 세션은 빠지고, 동결 직전에는 `--require-review`로
`use` 판정 세션만 쓴다. 규칙을 바꿔 다른 버전을 만들 수 있다: `build-dataset v1-temp75 --rule done_start=temp:75`. 설명서 틀은 `research/DATASET.md`, 라벨 규칙은 `soupdata/labels.py` 머리말.
PT100 보정값은 `~/soup-data/calibration.json`: `{"pt100_0": {"a": 1.0, "b": 0.0, "source": "…"}}`.

- Pi DB 백업: `$R research/tools/soupctl.py pi-backup http://100.92.124.114:8100` → `~/soup-data/pi-db/`(정답 사건의 유일한 원본이라 세션마다·매일).
- Jetson 원본 삭제는 하지 않는다. `verify/<id>.json`이 OK인 세션만 Jetson 담당에게 정리 가능하다고 알린다.
- 검증에서 메타 파일(`session.json`·`events.jsonl`·`stats.jsonl`) 불일치는 경고다(체크섬 뒤 Jetson이 갱신할 수 있음).
  데이터·인덱스 불일치는 실패다.

## 구조

- `soupdata/jetson_storage.py` — Jetson `app/storage.py`를 독립 이름으로 불러 `unpack_record`를 재사용(형식 단일 출처).
- `soupdata/session.py` — 세션 읽기(스트림 목록·index·이미지 경로·배열 레코드·스칼라).
- `soupdata/verify.py` — manifest 기준 sha256 전수 검증(디렉터리 규칙은 Jetson `compute_checksums`와 동일).
- `soupdata/qc.py` — 스트림별 저장·미저장·fps·최대 간격, 값 범위, Pi 정답 사건, 카탈로그 행.
- `soupdata/heating.py` — PT100 곡선: 끓기 시작·끓는 구간 온도(보정 점검)·기준 온도 유지·가열 속도·열량·조리값·증발 추정.
- `soupdata/labels.py` — 객관 라벨 규칙(D-041)과 관능 라벨(Pi 사건, 시계 오차 보정·누락·중복 경고)·두 라벨의 차이.
- `soupdata/review.py` — 세션 판정 기록(`review.jsonl`, 덧붙이기만).
- `soupdata/dataset.py` — 세션 → 1 Hz 표(과거 샘플만 사용), 세션 단위 분할, 데이터셋 버전(parquet·summary).
- `tools/soupctl.py` — CLI.
- `tests/` — 가짜 세션을 **Jetson 실제 기록 코드**(StreamWriter·SessionStore)로 만들어 검증한다.
