# 2026-09-27 — Pi 실물 연동: 가열 없는 60초 전체 센서 수집 점검

- **목적:** Pi 관리 화면(http 모드)으로 실제 Jetson을 조작해 5센서 수집 → `max_duration_sec` 자동 종료 → 저장 결과 수신 →
  메모·설정 포함 내보내기까지 한 번에 확인. 운영 8000 서비스의 D-035 적용 여부 확인. **가열 없음**(솥 빈 상태, 상온).
- **세션:** `sess-20260927T074757Z-f355` (Pi 화면 '점검 60초 (가열 없음)' 프리셋으로 시작, 16:47:57 KST)
- **원본 위치:** Jetson `/home/ubuntu/collector-data/sess-20260927T074757Z-f355` (git 미포함)
- **기록:** `notes/data/logs/20260927_check60s_pi-export.json`(Pi 내보내기), `..._jetson-session.json`(Jetson 세션 상세),
  화면 `docs/img/pi-real-60s-cameras-20260927.png`, `docs/img/pi-real-60s-thermal-pt100-20260927.png`

## 설정(세션 스냅샷)
센서 `cam_rgb_0, cam_rgb_1, cam_depth_0, thermal_0, pt100_0` · fps 10 · 미리보기 켬 1 Hz · `max_duration_sec` 60(config 최상위)

## 결과
| 항목 | 값 |
|---|---|
| 종료 | 장치 16:48:59 `stop_reason = max_duration_sec=60 도달`, `end_reason = stopped` (Pi 이벤트 `capture.auto_stopped`) |
| Pi 세션 상태 | `stopped`, `jetson_ack` true, `session.orphaned` 없음 |
| 저장 결과 | `ok` true · 파일 16 · 프레임 2,950 · 드롭 0 · 1.36 GB (≈ 1.36 GB/분, 이 장면 실측 — 보장값 아님) |
| cam_rgb_0 / cam_rgb_1 | 597 / 598 프레임 (JPEG) |
| cam_depth_0 | color 536 · depth 518 (`lz4`, uint16) · ir 518 (`lz4`, **uint8**) |
| thermal_0 | 121 레코드 (`lz4`, float32 24×32) |
| pt100_0 | 62 레코드 (index.jsonl) |
| 화면 | 카메라 3면·열화상·PT100 '수신 중'. 20초 시점 열화상 22.1~32.3 ℃(평균 24.9), PT100 23.03~23.13 ℃ |

## 판단
- **D-035는 운영 8000에 적용됨** — 이번 세션이 lz4·IR uint8로 저장되고 max_duration으로 스스로 멈췄다(SSH 없이 동작으로 확인).
- **PT100 값은 보정 전 실측**(3선 탐침을 `wires=4`로 판독) — 기준 온도로 쓰지 않는다.

## 관찰·남은 일
- GMSL2 ①(`cam_rgb_0`) 화면이 뿌옇다 — 렌즈 가림/초점/방향 현장 확인 필요.
- Gemini depth·IR은 518프레임(≈ 8.6 fps)으로 RGB(≈ 10 fps)보다 적다. 드롭 0이므로 장치 출력 속도로 보임 — 추후 확인.
- Jetson `/api/v1/status`가 가끔 2초 넘게 걸려(40회 중 1회 2.34 s, 평소 ≈ 30 ms) Pi가 순간적으로 `service_down`을 기록한다.
- Jetson `sensor_mode=auto` 보고에 `mock_*` 센서 4개(simulated)가 함께 나온다 — Jetson 담당 확인.
