# 작업 지시서 — Pi: 카메라 1대(top view) 반영 + 테스트 시각 비교 고침 (2026-10-10)

**시작:** `python3 tools/dev_session.py start pi topview` → 이 파일을 읽고 수행.
**근거:** D-042(카메라는 top view `cam_rgb_0` 1대), Jetson `notes/jetson/topview-only.md`의 Pi 인계, Fedora 검토 `notes/fedora/review-20261010.md`.

## 왜 급한가
Jetson 운영 env가 `COLLECTOR_V4L2_DEVICES=gmsl:0`으로 바뀌면 `cam_rgb_1`이 사라진다. 지금 Pi 프리셋 `TRIAL_SENSORS`에 `cam_rgb_1`이 있어
**녹화 시작이 거절된다**(`알 수 없는 센서: cam_rgb_1`). Jetson은 이 Pi 작업이 운영에 적용될 때까지 env를 바꾸지 않고 기다린다.

## 할 일
1. `app/static/app.js` `TRIAL_SENSORS`에서 `cam_rgb_1` 제거 → `['cam_rgb_0', 'thermal_0', 'pt100_0']`. 프리셋 안내 문구의 센서 수는 목록 길이 그대로.
2. `app/config.py` `DEFAULT_PREVIEW_CAMERAS`에서 `gmsl2_2`(cam_rgb_1) 제거 → top view 1면. 라벨은 "GMSL2 (top view)" 정도.
3. `systemd/soup-pi-server.env` 미리보기 주석, `jetson/mock.py` 모의 센서(`cam_rgb_1`) 정리. 다시 2대로 돌아갈 수 있게 주석에 방법만 남긴다.
4. 과거 세션 이력·상세·내보내기에서 `cam_rgb_1`이 있어도 깨지지 않는지 테스트(삭제·차단 금지 규칙).
5. **테스트 고침:** `tests/test_api.py::test_manual_mark_separates_occurred_and_recorded_time`이 가끔 실패한다(Fedora에서 4번 중 1번).
   사후 입력 시각을 `started_at + 1 ms`로 잡아 같은 밀리초에 기록되면 `ts == occurred_at`가 된다. 시각이 같은지가 아니라
   `late_entry`와 두 값이 따로 저장되는지를 확인하거나, 사후 시각을 세션 시작 직후로 두되 기록 시각과 다르게 보장한다.
6. 실물(가능할 때): Jetson이 아직 `cam_rgb_1`을 보고해도 프리셋 시작이 3센서로 되는지.

## 하지 말 것
- 계약·Jetson 코드 수정 금지. 운영 서비스 자동 재시작 금지. 과거 데이터 변경 금지.

## 완료 조건 / 보고
- `pytest` 통과(5번 반복 실행해 안정 확인), `python3 tools/dev_session.py check pi` 통과.
- `notes/pi/topview.md`. 브랜치 `pi/topview` push. **운영 적용 후 Jetson 담당에게 env 적용해도 된다고 알린다**(사람).
