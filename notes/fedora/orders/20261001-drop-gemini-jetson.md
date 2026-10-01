# 작업 지시서 — Jetson: Gemini 2 수집 제외 (2026-10-01)

**시작:** `python3 tools/dev_session.py start jetson drop-gemini` → 이 파일을 읽고 수행.
**근거:** `notes/decisions.md` D-039, `notes/fedora/drop-gemini.md`. Gemini 2는 습기에 약해 장비 구성에서 뺀다.

## 목표
수집 서비스 기본 동작에서 Orbbec Gemini 2(`cam_depth_0`)를 탐색·보고·수집하지 않게 한다.

## 할 일
1. `jetson/collector/app/sensors/registry.py` `build_sensors()`는 auto/real 모드에서 `OrbbecGemini2`를 **무조건** 추가한다.
   설정으로 켜고 끄게 바꾸고 **기본값은 끔**으로 한다. 예: `config.py` `orbbec_enabled: bool = False`,
   env `COLLECTOR_ORBBEC_ENABLED=0`. (이름·방식은 Jetson 판단. 코드 삭제 대신 비활성 권장)
2. `jetson/collector/systemd/jetson-collector.env` 주석에서 "Gemini 2 USB·센서 5대" 등 기본 구성 설명을 갱신한다.
3. 모의 센서(`sensors/mock.py`)의 `cam_depth_0`은 그대로 둘지 판단해 노트에 적는다(Pi 모의 목록도 뺄 예정).
4. 테스트: `test_orbbec.py`는 어댑터 단위 테스트이므로 유지. 기본 설정에서 Gemini가 빠지고, 켜면 들어오는 테스트를 추가.
5. 실물 확인(가능할 때): Gemini를 뺀 상태로 서비스 기동 → `/api/v1/status` 센서 목록에 `cam_depth_0` 없음,
   GMSL2 `cam_rgb_0/1` 정상 수신(Gemini가 video0~5를 차지했으므로 `/dev/video` 번호 변화 확인. `gmsl:<포트>` 지정이면 무관),
   상태 갱신 주기에서 Gemini 탐색(≈2초) 사라짐.

## 하지 말 것
- 계약(`docs/pi-jetson-api.md`)에서 `depth_usb`·`cam_depth_0` 삭제 금지 — 과거 세션 호환.
- 과거 원본을 읽는 `storage.unpack_record`·lz4·IR8 처리 삭제 금지.
- Pi 코드 수정 금지. 운영 서비스 자동 재시작 금지.

## 완료 조건 / 보고
- `python3 tools/dev_session.py check jetson` 통과, collector 테스트 결과.
- `notes/jetson/drop-gemini.md`: 변경·테스트·실물/모의 여부·남은 일. 브랜치 `jetson/drop-gemini` push.
- 운영 적용(진행 중 세션 없을 때 재시작)은 Jetson 담당이 하고 노트에 기록.
