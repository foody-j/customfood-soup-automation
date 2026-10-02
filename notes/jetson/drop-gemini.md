# jetson/drop-gemini — Gemini 2 수집 제외 (2026-10-02, D-039)

지시서: `notes/fedora/orders/20261001-drop-gemini-jetson.md`. 근거: D-039, `notes/fedora/drop-gemini.md`.

## 변경
- `config.py`: `orbbec_enabled: bool = False`, env `COLLECTOR_ORBBEC_ENABLED`(기본 0).
- `registry.build_sensors()`: auto/real에서 `orbbec_enabled`일 때만 `OrbbecGemini2`(`cam_depth_0`) 추가.
  끄면 탐색(SDK ≈2 s)·`/status` 보고·수집 모두 없음. 시작 요청에 `cam_depth_0`이 오면 기존 규칙대로
  `accepted:false, "알 수 없는 센서: cam_depth_0 (사용 가능: …)"`.
- 어댑터(`sensors/orbbec.py`)·`storage.unpack_record`·lz4·IR8 처리·계약의 `depth_usb`/`cam_depth_0`은 **그대로**(삭제 금지, 다시 켤 수 있음).
- `systemd/jetson-collector.env`: `COLLECTOR_ORBBEC_ENABLED=0` 추가, 센서 구성·`/dev/video` 설명 갱신.
- **모의 센서(`sensors/mock.py`)의 `cam_depth_0`은 유지** — mock 모드는 개발·시연용이고, 배열(uint16 depth)·lz4·미리보기
  테스트가 이 모의 센서로 배열 경로를 검사한다. 운영(real)에는 영향 없음. Pi 모의 목록에서 빼는 것과 독립.
- 테스트 결함 수정: `test_sdk_absent_and_non_gemini_devices_are_not_reported_connected`가 실제 sysfs USB 목록을 읽어
  Gemini 분리 시 실패했다(9/28 `orbbec-probe-gil`의 sysfs 경로 도입 때 생긴 환경 의존). `usb_lookup=lambda: None` 주입.

## 검증
- 테스트: 신규 `test_registry_excludes_gemini_by_default_and_includes_when_enabled`(real·auto 기본 제외, 켜면 포함),
  기존 Gemini 레지스트리 테스트는 `orbbec_enabled=True`로 갱신. 전체 **68 통과, 1 건너뜀**(실물 Gemini 필요 테스트).
- 실물(부분, 2026-10-02 11:30, worktree 코드 127.0.0.1:8020 + 운영 env):
  - Gemini USB 분리 상태 확인(sysfs vendor 2bc5 없음). GMSL 노드가 **video6~9 → video0~3**으로 당겨졌고 `gmsl:0/1`이
    `/dev/video0/1`로 정상 해석됨 — 포트 지정이라 번호 변화 영향 없음.
  - `/status` 센서 목록에 `cam_depth_0` 없음(cam_rgb_0/1·thermal_0·pt100_0). `/status` 60회 중앙 27 ms·최대 53 ms.
  - `cam_depth_0` 시작 요청 → `accepted:false`(알 수 없는 센서).
  - **센서 수신은 미확인**: 이 시점 운영 8000도 포함해 GMSL 링크 없음·열화상 0x33 무응답·PT100 무응답 — 장비가 물리적으로
    연결돼 있지 않았다(코드와 무관). 재연결 후 운영 적용 때 수신 확인 필요.

## 남은 일 / 운영 적용
- main 병합 → 운영 env 교체(`sudo cp … /etc/default/jetson-collector`) → 진행 중 세션 없을 때 재시작(Jetson 담당, 사람).
  env를 교체하지 않아도 코드 기본값이 끔이라 Gemini는 빠진다.
- 장비 재연결 후: 4센서 수신·미리보기 확인.
