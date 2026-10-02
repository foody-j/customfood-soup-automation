# 소고기무국 조리 데이터셋 — 설명서 (틀)

> 버전을 만들 때마다(`soupctl.py build-dataset <version>`) 이 문서의 수치를 `datasets/<version>/summary.json`과
> `catalog.csv`에서 옮겨 적는다. 아직 실제 세션 없음(2026-10-02).

## 1. 개요
- 목적: 국/탕 조리 완료 시점(미완/완료/과조리) 판정 연구용 멀티센서 기록.
- 메뉴: 비비고 소고기무국 2봉 재가열(1종). 수집 기간: ____ ~ ____. 세션 수: __ (train __ / val __ / test __).
- 수집 장치: Jetson Orin Nano + Raspberry Pi 5 (`docs/architecture.html`, `docs/pi-jetson-api.md`).

## 2. 센서
| 센서 ID | 장치 | 저장 | 주기 |
|---|---|---|---|
| cam_rgb_0 / cam_rgb_1 | Sensing ISX031F GMSL2 (1920×1536) | JPEG 프레임 | __ fps |
| thermal_0 | MLX90640 D55 32×24 | float32 배열(lz4) | 2 Hz |
| pt100_0 | PT100 + MAX31865 | 스칼라(`temp_c`) | 1 Hz |

PT100 보정: `calibration.json`(얼음물·끓는 물 2점) — 원본 값은 바꾸지 않고 `pt100_cal_c` 열로 제공.

## 3. 수집 절차와 조건
- 절차: `docs/cooking-protocol.md` v__ (과조리까지 촬영).
- 조건 변수: 인덕션 출력(`param_heat_level`), 추가 물(`param_water_added_ml`), 뚜껑(`param_lid_initial`) …
- 조건별 세션 수 표: (catalog.csv에서)

## 4. 라벨
- 정답원: 실험 중 Pi 화면 사건(`done_start`·`done_end`·`overcooked`, 맛보기 `taste`). 판정자: __. 기준: 프로토콜 5절(지도교수 확인 __).
- 규칙: `research/soupdata/labels.py` — done_start 이전 미완, done_start~done_end 완료, done_end~overcooked **제외(None)**, overcooked 이후 과조리.
- 시계: 사건(Pi)과 프레임(Jetson) 시계 차이를 세션별로 측정해 보정(`offset_s`).
- 라벨 분포(행 수, 1 Hz): 미완 __ / 완료 __ / 과조리 __ / 제외 __.
- 맛보기와 라벨 불일치: __건.

## 5. 형식
```
datasets/<version>/
  sessions/<session_id>.parquet   1 Hz 표 (아래 열)
  splits.json                     세션 단위 train/val/test (세션 ID 해시, 결정적)
  summary.json                    사용·제외 세션과 이유, 라벨 분포, 보정값
raw/<session_id>/                 원본(Jetson 형식 그대로, sha256 검증됨) — 프레임 경로는 이 디렉터리 기준 상대 경로
```
열: `session_id, t_utc, elapsed_s, label, pt100_c, pt100_cal_c, thermal_max_c, thermal_mean_c, thermal_p95_c,
rgb_cam_rgb_0_path, rgb_cam_rgb_1_path, param_*`. 값은 그 초 이전 가장 가까운 샘플(허용 간격 PT100 2 s·열화상 1.5 s·영상 1 s).

## 6. 품질
- 세션별 QC: `notes/data/experiments/` — 드롭·무효·최대 수신 간격·센서 끊김.
- 제외 세션과 이유: (summary.json)

## 7. 한계
- 메뉴 1종·재가열(생재료 조리 아님), 같은 솥·주방, 판정자 소수, 김서림·조명 영향, 깊이 센서 없음(D-039).
