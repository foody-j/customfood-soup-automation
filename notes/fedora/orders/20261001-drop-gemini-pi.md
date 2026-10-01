# 작업 지시서 — Pi: Gemini 2 화면·모의 목록 제외 (2026-10-01)

**시작:** `python3 tools/dev_session.py start pi drop-gemini` → 이 파일을 읽고 수행.
**근거:** `notes/decisions.md` D-039, `notes/fedora/drop-gemini.md`. Gemini 2는 습기에 약해 장비 구성에서 뺀다.

## 목표
Pi 관리 화면·기본 설정·모의 Jetson에서 Gemini 2(`cam_depth_0`)를 기본 구성으로 다루지 않게 한다.

## 할 일
1. `pi-server/app/config.py` `DEFAULT_PREVIEW_CAMERAS`에서 `gemini2` 패널 제거(GMSL2 두 면만).
2. `pi-server/systemd/soup-pi-server.env` 57~58행 주석(기본 3면 설명·예시)을 갱신.
3. `pi-server/app/jetson/mock.py` 모의 센서 목록·스트림에서 `cam_depth_0` 제거.
4. 테스트 `pi-server/tests/test_api.py`: 516행 센서 목록, 580~617행 미리보기 테스트가 `cam_depth_0`을 쓴다.
   미리보기 중계 검증 자체는 유지하되 대상 센서를 `cam_rgb_0` 등으로 옮긴다. `test_preview_relays_each_gemini_stream`은
   삭제 또는 다중 스트림 일반 테스트로 바꾸는 것을 Pi가 판단.
5. `static/camera-preview.js` 상단 주석 예시 갱신. 스트림 전환 기능 자체는 일반 기능이므로 유지.
6. 과거 세션 이력·상세에서 `cam_depth_0`/`depth_usb`가 있어도 표시가 깨지지 않는지 확인(삭제·차단 금지 규칙).
7. 실물 확인(가능할 때): Jetson이 아직 Gemini를 보고해도 화면에 Gemini 패널이 안 나오고 다른 동작 이상 없음.

## 하지 말 것
- 계약·`pi-server/app/models.py`의 `depth_usb` kind 삭제 금지.
- Jetson 코드 수정 금지. 운영 서비스 자동 재시작 금지.

## 완료 조건 / 보고
- `python3 tools/dev_session.py check pi` 통과, `pytest` 결과.
- `notes/pi/drop-gemini.md`: 변경·테스트·실물/모의 여부·남은 일. 브랜치 `pi/drop-gemini` push.
- 운영 적용(진행 중 세션 없을 때 재시작)은 Pi 담당이 하고 노트에 기록. Jetson 작업과 적용 순서 무관.
