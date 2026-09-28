# jetson/live-view — 라이브 보기(`record:false`) Jetson 구현 (2026-09-28, D-037)

인계: `notes/pi/live-view.md`, 계약 `docs/pi-jetson-api.md` §3.1. 인계 항목 1~5를 그대로 구현했다.

## 구현
- `storage.NullStore`: SessionStore와 같은 메서드, 디스크에 아무것도 쓰지 않음(메타는 메모리, 사건은 서비스 로그 `[라이브 …]`).
- `storage.NullWriter`: StreamWriter 모양 그대로, 수신 통계만(`written`·`bytes_written` 0, backlog 0). 수신 계수 로직은
  `_count_received`로 StreamWriter와 공유 — 그래서 `_capture_loop`·`_update_preview`는 **손대지 않고** 라이브에서도 돈다
  (인계 노트가 짚은 "writer가 None이면 미리보기까지 끊김" 문제를 피함).
- `CaptureSession`: `config.record is False` → NullStore·NullWriter, 미리보기 강제 켬(max_fps 상한 2 그대로),
  `max_duration_sec` 없거나 0이면 `LIVE_DEFAULT_MAX_DURATION_SEC = 600`, 디스크 여유 감시·체크섬 생략, `snapshot()["record"]`.
- `CollectorService`: `status.capabilities = ["live_view"]`, 라이브 시작 시 `session.json` 존재·디스크 여유 검사 생략,
  종료 시 `last_session_summary` **갱신 안 함**(`last_session`에는 `record:false`, `path:null`).
- 모델: `JetsonReport.capabilities`, `JetsonCapture.record`. `record` 미지정·true는 기존과 완전히 같다.
- 라이브 세션은 디렉터리가 없어 `/api/v1/sessions` 목록·`/sessions/{id}` 상세(404)에 나오지 않는다.

## 검증
- 테스트 신규 4(`tests/test_live_view.py`): capability 알림 / 무저장·JPEG·preview_array 200·목록에 없음 /
  기본 최대 시간 자동 정지(값 주입)·사유 문자열 / 직전 녹화 요약 유지·라이브 중 다른 ID 녹화 거절. 전체 **68 통과**, 기존 64 회귀 없음.
- **실물 1분**(worktree 코드, 127.0.0.1:8020, 운영 env `/etc/default/jetson-collector`, 운영 data_root):
  세션 `live-20260928T061101Z-jt01`, 센서 5개 — 수신 GMSL 각 641·Gemini color/depth/IR 634~635(모두 10.0 fps)·
  열화상 128(2 Hz)·PT100 64(1 Hz), `record:false`, `frames_written 0`. 미리보기 6개 엔드포인트 모두 200.
  **data_root 새 항목 없음, `du -sb` 전후 32,176,262,516 바이트로 동일.** 중지 → `stopped`, `last_session.record=false`,
  `last_session_summary` 변화 없음.
- 미검증: Pi 화면의 '라이브 끝내고 촬영 시작' 전환(Pi 담당 실물 확인 예정), 운영 8000 적용(병합 후 재시작 필요).
