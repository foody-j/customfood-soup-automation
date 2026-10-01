# fedora/drop-gemini — Orbbec Gemini 2를 장비 구성에서 제외 (2026-10-01, D-039)

## 배경
- 사용자 결정: Gemini 2를 장비 구성에서 뺀다. 이유는 **GMSL2 카메라(Sensing ISX031F)보다 습기에 약함** — 국/탕 조리 중 증기 환경.
- Gemini 2는 모델 입력 branch에 쓰지 않았다(`docs/model-architecture.md` §2.0 "수집·점검용"). 모델 설계 영향 없음.
- 깊이(depth)·Gemini IR 데이터는 앞으로 수집하지 않는다. 대체 깊이 센서는 정하지 않았다.

## 이번 브랜치 변경 (공통 문서·노트만)
- `docs/pi-jetson-api.md`: 제외 사실 기록, 라이브 보기 요청 예시에서 `cam_depth_0` 삭제.
  **`depth_usb` kind와 `cam_depth_0` 처리는 계약에서 지우지 않는다**(과거 세션·옛 장비 호환, 공통 계약 규칙 4).
- `docs/model-architecture.md` 센서 표, `README.md` 로드맵 정리.
- 과거 실험 기록(`notes/data/experiments/2026091*~0927*`)과 Gemini 설치 문서(`docs/gemini2-jetson-setup.md`)는 보존.
- Pi·Jetson 코드는 수정하지 않았다(각 장비 담당 범위).

작업 지시서: `notes/fedora/orders/20261001-drop-gemini-jetson.md`, `notes/fedora/orders/20261001-drop-gemini-pi.md`.

## 인계 — Jetson (`jetson/<작업>` 브랜치)
- 운영 env(`jetson/collector/systemd/jetson-collector.env`)·`config.py` 기본 구성에서 Gemini 2 탐색 제외 방법 결정
  (Orbbec 어댑터 코드 삭제 여부는 Jetson 담당 판단. 남겨두되 기본 비활성 권장 — 과거 원본 읽기 `storage.unpack_record`는 유지).
- Gemini를 뺀 뒤 `/dev/video` 번호 변화 확인: 실측상 Gemini가 video0~5를 차지했으므로 GMSL 노드 번호가 바뀔 수 있다.
  env 설명대로 `gmsl:<포트>` 지정이면 영향 없음 — 실물 확인 필요.
- 센서가 없을 때 `/status` 센서 목록·시작 요청(`cam_depth_0` 포함 시) 동작 확인. `orbbec-probe-gil`의 탐색 비용(≈2초)도 사라짐.
- 09-28 남은 확인 거리 "Gemini color가 depth·IR 절반 속도" → **종결(장비 제외)**.

## 인계 — Pi (`pi/<작업>` 브랜치)
- `pi-server/app/config.py` 기본 미리보기 카메라 목록과 `soup-pi-server.env` 주석에서 `gemini2`(cam_depth_0) 패널 제거.
- `pi-server/app/jetson/mock.py` 모의 센서 목록에서 `cam_depth_0` 제거, 관련 테스트 갱신.
- 과거 세션 이력 표시에서 `cam_depth_0`/`depth_usb`가 있어도 깨지지 않는지 확인(삭제·차단 금지 규칙 유지).

## 적용 순서
순서 무관. Jetson이 먼저 빼면 Pi 화면에 Gemini 패널이 "연결 없음"으로 남고, Pi가 먼저 빼면 Jetson이 보고해도 패널만 안 보인다.
운영 적용은 진행 중 세션이 없을 때 각 장비에서 재시작. 물리적으로 장비에서 분리하는 시점은 사용자가 정한다.

## 검증
- 문서·노트 변경만. 코드 테스트 대상 없음. 운영 서비스 미변경.
