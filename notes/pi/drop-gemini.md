# pi/drop-gemini — Gemini 2 화면·모의 목록 제외 (2026-10-02, D-039)

지시서 `notes/fedora/orders/20261001-drop-gemini-pi.md` 수행. 시작: `python3 tools/dev_session.py start --prepare-only pi drop-gemini`
(이 대화의 Pi Claude가 직접 수행해 런처의 Claude 실행은 생략).

## 변경 (`pi-server/`만)
1. `app/config.py` `DEFAULT_PREVIEW_CAMERAS`: `gemini2` 패널 제거 → GMSL2 ①·② 2면.
2. `systemd/soup-pi-server.env` 미리보기 주석: 기본 2면, 예시를 GMSL2로. 여러 스트림 카메라는 `streams` 나열로 전환 버튼이 생긴다고 설명.
3. `app/jetson/mock.py`: 모의 센서·스트림에서 `cam_depth_0` 제거(남은 모의: cam_rgb_0/1·thermal_0·pt100_0).
   미리보기 대상 스트림 목록(color/depth/ir 포함)은 일반 규칙이라 그대로 둠.
4. **지시서 밖 — 함께 수정:** `app/static/app.js` 프리셋 기본 센서 `TRIAL_SENSORS`에서 `cam_depth_0` 제거(4개).
   Jetson `service.py`는 요청 센서가 미연결이면 `센서 미연결`로 **시작을 거절**한다 → Jetson이 Gemini를 빼면 프리셋 시작이 실패했을 것.
   프리셋 안내 문구의 센서 수도 목록 길이로 표시.
5. `app/static/camera-preview.js` 상단 주석 예시를 GMSL2로. 스트림 전환 기능은 유지.
6. `README.md`: 기본 2면, 프리셋 센서 4개, 9/18 Gemini 캡처는 과거 화면임을 표시.
- `app/models.py`의 `depth_usb` kind, 계약 문서는 건드리지 않음(지시서 '하지 말 것').

## 테스트
- `test_api.py`: 센서 목록 4개, 미리보기 테스트 대상을 `cam_rgb_0/1`로. `test_preview_relays_each_gemini_stream` →
  `test_preview_relays_each_camera`(두 카메라 중계 + 그 센서에 없는 스트림 404 + `cam_depth_0` 404). 기본 패널 2면, 잘못된 설정 폴백 2면,
  설정 주입 예시는 다중 스트림 `cam_rgb_0`(rgb/raw)로. HTTP 클라이언트 중계 시험도 GMSL 경로로.
- `test_first_trial.py`·`test_live_view.py`: 센서 목록 4개, 미리보기 경로 GMSL로.
- 신규 `test_drop_gemini.py`(지시서 6번): 과거 세션 설정에 `cam_depth_0`이 있어도 이력·상세·JSON/CSV/JSONL 내보내기 정상이고
  기록을 고치지 않음 / 실물 Jetson(http)이 `depth_usb` 센서를 보고해도 상태에 그대로 싣고 카메라 패널에는 없음.
- `pytest` **60 passed**. `tools/dev_session.py check pi` 통과.
- 모의 서버 + 헤드리스 Firefox: 카메라 패널 GMSL2 ①·② 2면 '수신 중', 프리셋 '센서 4개', 시작 전 점검 '4개 모두 설정·연결됨', 콘솔 오류 없음.

## 미검증 / 남은 일
- **실물(지시서 7번) 미확인:** 2026-10-02 작업 시점 Pi eth0 링크 없음(carrier 0), Jetson 약 49분째 무응답.
  링크 복구 후 확인할 것: Jetson이 아직 Gemini를 보고해도 패널 없음, 프리셋 시작이 4센서로 정상.
- 운영 적용: main 병합 후 Pi 운영 폴더 pull + `sudo systemctl restart soup-pi-server`(진행 중 세션 없을 때, Pi 담당). Jetson 작업과 순서 무관.
- 운영 Pi의 저장된 실험 설정은 `sensors: []`(9/28 리허설 때부터)이라 Gemini가 들어 있지 않다. 프리셋을 누르면 4센서로 저장된다.
