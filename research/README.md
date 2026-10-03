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
$R research/tools/soupctl.py labels <session_id>      # 라벨 구간·경고·맛보기 불일치 확인
```

## 매일 밤 자동 처리

Fedora 사용자 타이머 `soup-nightly`(01:00)가 `soupctl.py nightly`를 돌린다 — Pi DB 백업, 새 세션 반출·검증, Pi 내보내기, QC, 카탈로그.
촬영 중이면 반출하지 않는다. 설정·관리 방법은 `notes/fedora/server-setup.md`.

## 데이터셋 버전 만들기

```bash
$R research/tools/soupctl.py build-dataset v1          # 검증 OK 세션 전부 → ~/soup-data/datasets/v1/
```
동결된 버전은 덮어쓰지 않는다(같은 이름이면 거부). 설명서 틀은 `research/DATASET.md`, 라벨 규칙은 `soupdata/labels.py` 머리말.
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
- `soupdata/labels.py` — Pi 정답 사건 → 미완/완료/과조리 구간(시계 오차 보정, 누락·중복 경고, 맛보기 불일치).
- `soupdata/dataset.py` — 세션 → 1 Hz 표(과거 샘플만 사용), 세션 단위 분할, 데이터셋 버전(parquet·summary).
- `tools/soupctl.py` — CLI.
- `tests/` — 가짜 세션을 **Jetson 실제 기록 코드**(StreamWriter·SessionStore)로 만들어 검증한다.
