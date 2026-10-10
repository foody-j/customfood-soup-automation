# pi/topview-only — Pi 기본 구성에서 비스듬한 GMSL2 ②(`cam_rgb_1`) 제외 (2026-10-10)

근거: `notes/jetson/topview-only.md`의 **Pi 인계(필수)** — Jetson 운영 env가 `COLLECTOR_V4L2_DEVICES=gmsl:0`(top view만)로 바뀌어
`/status`에 `cam_rgb_1`이 없다. Pi 프리셋이 `cam_rgb_1`을 요청하면 Jetson이 `accepted:false, "알 수 없는 센서: cam_rgb_1"`로 녹화 시작을 거절한다.
10/10 실물 확인: Jetson 보고 센서 = `cam_rgb_0`(연결) · `thermal_0`(**미연결**, i2c-7 0x33 무응답) · `pt100_0`(연결).

## 변경 (`pi-server/`)
- `app/static/app.js` `TRIAL_SENSORS` = `cam_rgb_0, thermal_0, pt100_0`(프리셋 4종 모두 이 목록을 쓴다).
- `app/config.py` 기본 미리보기 1면(`gmsl2_1`, 표시 이름 'GMSL2 top view'). `gmsl2_2` 제거.
- `app/jetson/mock.py` 모의 센서에서 `cam_rgb_1` 제거(운영 구성과 맞춤).
- `systemd/soup-pi-server.env` 미리보기 주석: 기본 1면, 비스듬한 카메라를 다시 붙일 때의 예시(Jetson `gmsl:0,gmsl:1`과 함께).
- `README.md`, `camera-preview.js` 주석 예시 갱신. 계약·`models.py` 변경 없음.

## 테스트
- 기존 테스트의 센서 목록·미리보기 대상에서 `cam_rgb_1` 제거, 기본 패널 1면, `cam_rgb_1` 미리보기 404 확인 추가.
- 신규(`test_drop_gemini.py`): 전환 전 세션(`cam_rgb_1` 포함) 상세·내보내기 그대로 / **프리셋 `TRIAL_SENSORS`가 모의 Jetson 보고 센서의
  부분집합**(app.js를 읽어 검사 — 다음에 Jetson 구성이 바뀌어 어긋나면 이 테스트가 먼저 깨진다).
- `pytest` **80 passed**. `tools/dev_session.py check pi` 통과.

## 남은 일
- **열화상 미연결이면 프리셋 시작도 거절된다**(Jetson은 요청 센서가 미연결이면 `센서 미연결`로 거절). 10/10 현재 `thermal_0` 미연결 —
  배선 확인 필요. 급하면 '장치·설정'에서 활성 센서를 `cam_rgb_0, pt100_0`으로 저장하고 프리셋 없이 시작(데이터셋에는 비권장).
- 운영 적용: main 병합 → 운영 폴더 pull → `sudo systemctl restart soup-pi-server`(진행 중 세션 없을 때). marks-hardening도 함께 적용된다.
