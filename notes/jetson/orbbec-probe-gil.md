# Gemini 2 probe가 GIL을 쥐어 /status가 멈추던 문제 (2026-09-28)

## 배경
- Pi 실물 연동(9/27)에서 Jetson `/status`가 간헐적으로 2초를 넘어 Pi가 순간 `service_down`을 기록.
- 1차 수정(`status-probe-bg`, main 병합됨): probe를 백그라운드 스레드로 옮기고 `/status`는 캐시만 읽게 함.
  → 배포 후에도 **최대 2.13 s, 약 12초 간격**(TTL 10 s + probe 2 s)으로 재현.

## 원인
- 센서별 probe: GMSL 126/150 ms, 열화상 32 ms, PT100 4 ms, **Gemini `cam_depth_0` 2,277 ms**(SDK `Context()`+`query_devices`).
- 다른 스레드가 10 ms마다 깨어나는 시험에서 Gemini probe 동안 **최대 1,243 ms 공백** — pyorbbecsdk가 탐색 중 GIL을
  놓지 않는다. 그래서 백그라운드 스레드로 옮겨도 같은 프로세스의 HTTP 응답이 멈췄다(1차 회귀 테스트는 `time.sleep`
  probe라 GIL을 놓아 통과했다 — 이 한계는 시험으로 재현하지 못함).

## 수정
- `OrbbecGemini2.probe()`: sysfs(`/sys/bus/usb/devices/*/idVendor == 2bc5`)의 serial·busnum·devnum을 먼저 본다.
  - 장치 없음 → SDK 없이 즉시 미연결(`reason: … (sysfs)`).
  - 지난 SDK 성공 결과와 같은 serial·busnum·devnum → 그 결과 재사용(SDK 호출 없음, 조회 ≈ 8 ms).
  - 처음·재연결(devnum 변경)·serial 불일치 → SDK로 확인.
  - sysfs를 읽을 수 없는 환경(주입한 가짜 SDK 등)은 기존처럼 항상 SDK.
- sysfs serial(`AY6G65300YT`)이 SDK 보고 serial과 같은 것을 실물로 확인.

## 검증
- 테스트: 같은 USB면 SDK 재호출 없음·재연결 시 재확인·분리 시 SDK 없이 미연결(신규 1). 전체 **64 통과**.
- 실물: 새 코드 임시 인스턴스(127.0.0.1:8020, 운영 env)로 `/status` 90회(약 45 s, 갱신 4회 이상) —
  **중앙 29 ms, p99 34 ms, 최대 53 ms**(운영 8000의 이전 코드는 같은 조건 최대 2.13 s). 센서 5개 모두 connected.
- 미적용: 운영 서비스 재시작은 병합 후 사람이 수행.

## 참고
- 세션 시작·녹화 중 Gemini 읽기는 이 경로와 무관(세션 중 열린 센서는 probe하지 않음).
