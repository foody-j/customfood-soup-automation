# fedora/roadmap-phase1 — 로드맵·프로토콜·지시서 + 연구 데이터 파이프라인 1차 (2026-10-02)

## 배경
사용자와 11월 말 제출 범위 확정(소고기무국 데이터셋 + 보고서). 수집 이후(반출·검증·카탈로그·라벨·데이터셋)가 비어 있어
Fedora가 맡는다. 계획 전문은 `docs/roadmap-2026-11.md`.

## 변경
- 문서: `docs/roadmap-2026-11.md`, `docs/cooking-protocol.md`(v0.1), `docs/development-workflow.md`(Fedora 범위에 `research/`).
- 지시서: `notes/fedora/orders/20261002-doneness-marks-pi.md`, `20261002-cook-ready-jetson.md`.
- `research/`: 세션 읽기·sha256 검증·반출(rsync)·Pi 내보내기 저장·QC Markdown·카탈로그 CLI(`tools/soupctl.py`).
  - 배열 해제는 Jetson `storage.unpack_record`를 경로로 불러 재사용(패키지 이름 `app` 충돌 피하려 `_jetson_collector_app`으로 적재).
  - 발견: PT100 스칼라 `value`는 dict(`temp_c`·`resistance_ohm`·`rtd_raw`), 단계 이름은 `running`·`stop_requested`·`stopping`.
    lz4 프레임 끝은 0 바이트라 손상 테스트는 첫 바이트를 바꾼다.

## 검증
- `research/.venv/bin/python -m pytest -q research/tests` **8 passed**. 가짜 세션은 Jetson `StreamWriter`·`SessionStore`·
  `compute_checksums`로 생성 → Fedora 검증이 Jetson 체크섬과 일치, 데이터 손상·미완료 세션 거부·검증 실패 사본 보존 확인.
- 로컬 경로 SRC로 list→pull→qc 수동 실행 확인. **실물 Jetson 세션·원격 rsync는 미검증**(전송 경로 미정).

## Fedora 서버 상태 (2026-10-02)
- GPU RTX 5070 Ti는 nouveau로 동작 — NVIDIA 드라이버 없음(CUDA 불가). Secure Boot 켜짐 → akmod 서명 키(MOK) 등록에
  부팅 화면이 필요한데 Fedora PC에 모니터가 없어 **보류**. 필수 결과물(데이터셋·보고서)은 CPU로 진행, 모니터를 연결할 수 있을 때
  RPM Fusion `akmod-nvidia`(open 모듈) 설치.
- 미세팅: SSH 키 없음, Jetson(10.42.0.52) 접속 불가, Pi Tailscale offline, 보조 디스크 sdb(477 GB NTFS) 미마운트, Node 없음.

## 첫 실물 반출 (2026-10-02)
- 9/28 물 끓이기 `sess-20260928T113621Z-2b33`(8.0 GB, Gemini 포함 7스트림)을 네트워크(Pi 점프)로 반출: **1,794 s(≈4.8 MB/s)**,
  sha256 16건 전수 일치. 이후 반출은 외장 SSD로 한다(10/02 결정).
- QC 15.8 s: 수치가 `notes/jetson/boil-rehearsal.md`와 일치(RGB 6,009/6,009, 열화상 1,202, PT100 602, PT100 최고 93.82 ℃,
  열화상 최고 87.6 ℃, depth 무효 2). 1 Hz 표 601행 0.45 s — 정답 사건 없는 세션이라 라벨은 비어 있음(예상대로).
- 고친 점: 검증 기록(`verify/<id>.json`)의 `path`가 임시 폴더로 남던 문제 → 확정 위치로 다시 기록.

## 남은 일
- 전송 경로 결정 후 실물 세션으로 pull·검증(10분 세션 기준 소요 시간 기록).
- 4단계: 라벨(`labels.py`, 시계 오차 보정), 1 Hz 표·parquet, `DATASET.md`.
- Pi `doneness-marks` 반영 후 `pi-meta`·QC 정답 사건 표시를 실제 내보내기로 재확인(필드 이름 `detail.value` 가정).
