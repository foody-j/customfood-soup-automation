# Jetson 수집 서비스 (FastAPI)

Jetson Orin Nano에서 **상시 실행**되는 센서 수집·원본 저장·연구용 기록 서비스.
Pi 관리 서버(`pi-server/`)의 상대편이며, 접점은 계약 문서 `docs/pi-jetson-api.md`뿐이다.

> **원칙 세 가지**
> 1. Pi 연결은 관리 경로일 뿐이다. 통신이 끊겨도 진행 중 수집과 기록은 계속된다.
> 2. `state: stopped`는 **버퍼 flush·파일 close·manifest 기록이 끝났다**는 뜻이다.
> 3. 모르는 것은 모른다고 쓴다 — 모의 센서는 `simulated:true`, 미연동 센서는 `connected:false + reason`,
>    장치 시각을 모르면 `device_ts: null`, 누락 근거가 없으면 `gaps_detected: null`.

| 부분 | 구현 |
|---|---|
| API | Python 3.10 + FastAPI (`app/routes.py`, `app/main.py`) |
| 세션 상태기계 | `app/session.py` — starting → running → stopping → stopped / failed |
| 저장 | `app/storage.py` — 세션 디렉터리·index.jsonl·records.bin·manifest.json·복구·체크섬 |
| 센서 어댑터 | `app/sensors/` — mock · v4l2(ISX031F) · Orbbec Gemini 2 · MLX90640×2 · MLX90614×2 · MAX31865/PT100(`i2cmux.py` 버스 잠금) |
| 시스템 상태 | `app/sysmon.py` — CPU·GPU·메모리·온도·디스크 (기본 5초) |
| 자동 실행 | `systemd/jetson-collector.service` |

---

## 빠른 시작

```bash
# venv는 JetPack의 cv2/numpy를 쓰기 위해 --system-site-packages 필수
python3 -m venv ~/collector-venv --system-site-packages
~/collector-venv/bin/pip install -r requirements.txt

cd ~/customfood-soup-automation/jetson/collector
COLLECTOR_SENSOR_MODE=mock ~/collector-venv/bin/python -m app.main     # 모의 센서
COLLECTOR_SENSOR_MODE=auto ~/collector-venv/bin/python -m app.main     # 실기기 탐색 + mock_* 병행
curl -s localhost:8000/api/v1/status | python3 -m json.tool
```

테스트(모의 센서·임시 경로, Pi 계약 모델로 교차 검증):

```bash
~/collector-venv/bin/pip install -r requirements-dev.txt
~/collector-venv/bin/python -m pytest -q
```

Pi와 붙이기: Pi의 `/etc/default/soup-pi-server`에
`SOUP_JETSON_MODE=http`, `SOUP_JETSON_URL=http://10.42.0.52:8000`, `SOUP_JETSON_PROBE_PORT=22`.

## 설정 (`COLLECTOR_*`, `systemd/jetson-collector.env` 참고)

| 변수 | 기본 | 뜻 |
|---|---|---|
| `COLLECTOR_SENSOR_MODE` | `mock` | `mock` 모의만 / `auto` 실기기+미지원 보고+`mock_*` / `real` 실기기만 |
| `COLLECTOR_V4L2_DEVICES` | `/dev/video4` | ISX031F 지정(쉼표 구분) → `cam_rgb_0`, `cam_rgb_1`… 경로 또는 **`gmsl:<포트>`**(Sensing SG4A — 노드 번호가 Gemini 2 때문에 밀려도 포트로 찾는다). 현재 장비는 `gmsl:0,gmsl:1` |
| `COLLECTOR_ORBBEC_SERIAL` | (없음) | Gemini 2가 여러 대일 때 선택할 USB 장치 시리얼 |
| `COLLECTOR_ORBBEC_FPS` | 10 | 세션 설정에 `fps`가 없을 때 Gemini 2 세 스트림의 기본 fps. 0이면 SDK 기본(30 — depth+IR 약 115 MB/s) |
| `COLLECTOR_I2C_THERMAL_BUS` / `I2C_POINT_BUS` | **(없음)** | 열화상·비접촉 온도 버스의 **실측** `/dev/i2c-N` 번호. 비우면 해당 센서는 `connected:false` + 이유 |
| `COLLECTOR_I2C_THERMAL_MUX_ADDR` / `I2C_POINT_MUX_ADDR` | `0x70` | 그 버스의 TCA9548A 주소. `none`이면 mux 없이 직결(단독 시험) |
| `COLLECTOR_THERMAL_CHANNELS` / `POINT_CHANNELS` | `0,1` | `thermal_0,1` / `point_temp_0,1` 순서의 mux 채널 |
| `COLLECTOR_THERMAL_RATE_HZ` / `POINT_RATE_HZ` / `PT100_RATE_HZ` | 2 / 1 / 1 | 센서별 목표 주기(초기 시험 목표). 세션에서는 `per_sensor.<id>.rate_hz` |
| `COLLECTOR_THERMAL_REFRESH_HZ` / `THERMAL_READ_RETRIES` | 8 / 2 | MLX90640 장치 refresh rate(서브페이지 주기) / 일시적 프레임 오류 재시도 |
| `COLLECTOR_PT100_CS_PIN` / `PT100_REF_OHMS` | **(없음)** | MAX31865 별도 GPIO CS의 Blinka 핀 이름(예 `D22`) / 보드 **실물** 기준 저항 Ω |
| `COLLECTOR_PT100_WIRES` / `PT100_NOMINAL_OHMS` | 3 / 100 | RTD 결선 수 / 공칭 저항 |
| `COLLECTOR_SENSOR_FAIL_LIMIT` | 5 | 연속 읽기 실패가 이만큼이면 분리로 보고 재연결 |
| `COLLECTOR_JETSON_MODEL_NAME` | (없음) | 시스템 Jetson.GPIO가 보드를 못 알아볼 때 넘길 모델명(`JETSON_ORIN_NANO`) |
| `COLLECTOR_DATA_ROOT` | `~/collector-data` | 세션 원본 루트 |
| `COLLECTOR_MIN_FREE_BYTES` | 2 GB | 미만이면 시작 거절, 진행 중이면 안전 종료(`failed`, `disk_low`) |
| `COLLECTOR_WRITER_QUEUE_MAX` | 64 | 스트림별 기록 대기열 상한(넘치면 버리고 셈) |
| `COLLECTOR_STATS_INTERVAL` / `SYSTEM_INTERVAL` | 1 / 5 초 | 수집 통계 / 시스템 상태 주기 |
| `COLLECTOR_STOP_WAIT` | 120 초 | stop 응답이 저장 완료를 기다리는 상한 |
| `COLLECTOR_CHECKSUM` | `after_stop` | 세션 종료 후 백그라운드 sha256 (`none`으로 끔) |
| `COLLECTOR_POWEROFF_CMD` | (없음) | shutdown 마지막 단계 명령. 비우면 저장 완료 후 로그만 |
| `COLLECTOR_LOG_LEVEL` / `LOG_FILE` / `LOG_MAX_MB` / `LOG_BACKUPS` | INFO / 없음 / 5 / 3 | 서비스 로그 레벨·회전 파일 |
| `COLLECTOR_LOG_SUMMARY_INTERVAL` | 30 초 | 수집 중 요약 로그 주기 |

## 센서 ID와 스트림

| sensor_id | kind | 스트림 | 상태(2026-09-18) |
|---|---|---|---|
| `cam_rgb_0` / `cam_rgb_1` | rgb_gmsl2 | `rgb` (UYVY→JPEG 프레임 파일, 또는 raw) | ISX031F ×2, Sensing SG4A 보드. **2026-09-21 실기기 확인**(1920×1536, 30→10 fps 추림, 60초 3대 동시 드롭·갭 0). 재부팅마다 드라이버 적재 필요 → `systemd/sensing-gmsl.service` |
| `cam_depth_0` | depth_usb | `color`(JPEG 또는 BGR raw) / `depth`(mm, uint16) / `ir`(intensity, uint16) | 어댑터 구현. **2026-09-18 실기기 단기 촬영 확인**(30 fps 드롭 0, 미리보기 OK). 30분 연속·저장량 대책은 미완 — `notes/data/experiments/20260918_gemini2-jetson-first-capture.md` |
| `thermal_0` / `thermal_1` | thermal_i2c | `temp_array` (24×32 float32 ℃) | MLX90640 55° / 110°. **1대 2026-09-23 실기기 확인**(i2c-7 직결·mux 없음, 2 Hz 3분 360/360, 취득 96~221 ms). 2대 동시(mux CH0/CH1)·1.5 m 배선은 미검증 |
| `point_temp_0` / `point_temp_1` | point_temp_i2c | `temp` `{object_c, ambient_c}` | MLX90614 5° / 35°. 어댑터 있음, **실물 검증 전**(미배선 — 가짜 드라이버 테스트만) |
| `pt100_0` | rtd_spi | `temp` `{temp_c, resistance_ohm, rtd_raw}` | MAX31865 + PT100 3선식. 어댑터 있음, **실물 검증 전**(미배선 — 가짜 드라이버 테스트만) |
| `mock_*` (auto) / 위 ID 그대로 (mock) | — | 위와 같은 스트림 구조 | 모의, 항상 `simulated:true` |

요청 config 예:

```jsonc
{ "sensors": ["cam_rgb_0", "thermal_0"], "fps": 10, "resolution": "1920x1536",
  "encoding": "jpeg",                       // rgb: jpeg | raw
  "per_sensor": { "cam_rgb_0": { "fps": 30 } },
  "project_id": "customfood-soup", "rig_id": "rig-1",
  "calibration": { "id": "calib-2026-09", "version": 3 } }
```

I²C·SPI 센서 5대는 카메라용 전역 `fps`를 따르지 않는다 — 주기는 `per_sensor.<id>.rate_hz`
(예: `"per_sensor": {"thermal_0": {"rate_hz": 2, "refresh_hz": 8}}`)로 주고, 없으면 환경설정 기본값이다.
읽기 실패·fault는 `index.jsonl`에 `valid:false` + `invalid_reason`(`i2c_error…`, `frame_error_after_retries…`,
`max31865_fault:…`, `out_of_range`)으로 남고, 취득 시작 시각·소요·잠금 대기는 `flags`에 남는다.
같은 버스의 센서는 mux 채널 선택~읽기 완료가 버스 잠금으로 직렬화되므로 **동시 측정이 아니다**.

알려진 한계(드라이버 기준, 실물 검증 전): MLX90640 드라이버는 방사율 0.95·반사온도 `Ta-8`을 고정으로 쓰고,
`getFrame()`의 data-ready 대기에 시간 제한이 없다(I²C 오류는 빠져나오지만 ACK만 하고 ready를 안 올리는 장치는
그 읽기를 붙잡는다). 시스템 Jetson.GPIO 2.1.7은 Orin Nano Super에서 `import board`가 실패하므로
`COLLECTOR_JETSON_MODEL_NAME=JETSON_ORIN_NANO`가 필요하다(PT100 경로만 해당).

`sensors`를 비우면 **연결된 센서 전부**를 쓴다. 미연결·미지원 센서를 지정하면 시작을 거절한다(200 + `accepted:false`).

Gemini 2의 Jetson SDK 설치, USB 권한, 수집·미리보기 시험 및 저장 파일 확인은
[`docs/gemini2-jetson-setup.md`](../../docs/gemini2-jetson-setup.md)를 따른다.

## 저장 레이아웃

```
<DATA_ROOT>/<session_id>/          ← session_id는 Pi가 만든 값 그대로
  session.json      메타: project/device/schema_version, 요청 설정 vs 실제 적용 설정, 센서 모델·드라이버·
                    SDK·코드 버전, 시계 관계(boot_id·UTC·monotonic·NTP), install(rig·calibration),
                    phases(요청→시작→수집→중지요청→마무리→완료), config_changes(전후 값·시각), end_reason
  events.jsonl      장치 분리/재연결·쓰기 실패·디스크 부족·설정 변경 등 사건
  stats.jsonl       1초: 스트림별 수신/저장 수·FPS·누락·대기량 / 5초: CPU·GPU·메모리·온도·디스크
  manifest.json     파일별 경로·형식·크기·프레임 수·시작/종료·상태(complete|partial|failed)·sha256
  <sensor_id>/<stream_id>/
    index.jsonl     샘플 1줄: session/sensor/stream/frame_id/seq, host_recv_utc+mono_ns,
                    device_ts{value,unit,clock,source}|null, path/offset/bytes, 해상도·픽셀포맷·노출·게인,
                    valid/invalid_reason. **받았으나 버린 샘플도 path=null로 남는다.**
    frames/NNNNNN.jpg   이미지 스트림
    records.bin         배열 스트림(depth uint16 mm / thermal float32 ℃) — 미리보기가 원본을 대체하지 않음
```

## 시각 규칙

- `host_recv_utc`(UTC ISO8601)와 `host_recv_mono_ns`(CLOCK_MONOTONIC)를 **항상** 기록한다.
- `device_ts`는 장치가 준 값·단위·시계 종류를 그대로 둔다. V4L2는 `clock: host_monotonic`(호스트와 같은 축),
  모의 장치는 `device`(다른 축). 모르면 `null` — 수신 시각을 복사하지 않는다.
- `session.json.clock`에 boot_id, realtime−monotonic 오프셋, NTP 동기화 여부·지터·루트 분산을 남긴다.
  timesyncd는 오프셋을 노출하지 않으므로 `offset_ms: null`(미확인).
- 하드웨어 트리거·플래시는 미구현(`trigger.hardware_trigger: null`). 구현 시 요청 시각과 확인된 발광 시각을 구분해 넣는다.

## 생명주기와 Pi 타임아웃

Pi의 HTTP 타임아웃은 2초(`SOUP_PROBE_TIMEOUT`)다. stop은 저장 완료 후 응답하므로 2초를 넘길 수 있고,
그러면 Pi는 세션을 `stopping`으로 두고 이후 status에서 `stopped`를 보면 `capture.stop_confirmed`로 마무리한다
(`pi-server/app/capture.py` 재동기화). 즉 **응답이 늦어도 사실은 어긋나지 않는다.** 재시도 stop은 멱등이다.

프로세스가 세션 중에 죽으면 다음 기동 때 `state`가 starting/running/stopping인 세션을 찾아
`failed(end_reason=interrupted)`로 닫고 파일을 `partial`로 표시한다. 완료로 보고하지 않는다.

## 로그 — 세 가지 기록의 구분

| 무엇 | 어디에 | 보는 법 |
|---|---|---|
| 서비스 로그(프로그램이 어떻게 돌았나) | journald, 선택 시 회전 파일(`COLLECTOR_LOG_FILE`, `COLLECTOR_LOG_MAX_MB`×(backups+1) 상한) | `journalctl -u jetson-collector -f` |
| 프레임 기록(무엇을 찍었나) | 세션 디렉터리 `*/index.jsonl` | 파일 직접 |
| 세션 메타·사건 | `session.json`, `events.jsonl`, `stats.jsonl` | 파일 직접 또는 `GET /api/v1/sessions/{id}` |

`journalctl -u jetson-collector`에서 볼 수 있는 것: 기동(센서 수·저장 경로), 세션 시작/중지 요청/닫힘(세션 ID·프레임·드롭·바이트·사유),
센서 open 실패·분리·재연결, 저장 실패, 디스크 부족, `COLLECTOR_LOG_SUMMARY_INTERVAL`(기본 30초)마다 스트림별 요약 한 줄,
uvicorn 접속 로그(같은 포맷). 프레임마다 로그를 찍지 않는다. 로깅은 `create_app()`에서 설정되므로 systemd 실행에서도 남는다.

## 브라우저 점검 화면 (`/viewer`)

`http://<Jetson IP>:8000/viewer` — 지금 들어오는 프레임을 눈으로 확인하는 **점검용** 페이지.
기존 `status`와 저속 미리보기(`capture/preview`)만 1초마다 읽으므로 새 데이터 경로가 아니고 원본 저장과도 무관하다.
센서 연결 상태·사유, 스트림별 수신/기록 fps·드롭·무효 수, color/depth/IR 축소 영상을 보여 준다.
"점검 세션 시작" 버튼은 연결된 실물 센서 전부로 `preview.enabled=true` 세션을 연다(Pi 없이 벤치에서 볼 때만 사용 —
**원본이 실제로 저장되므로** 확인 후 "세션 중지"를 누르고 `check-*` 세션은 필요 없으면 지운다).

## 실기기 점검 도구

```bash
~/collector-venv/bin/python tools/v4l2_check.py --device /dev/video4 --frames 90 --jpeg --out result.json
```

링크 상태·실제 포맷·시퀀스 갭·타임스탬프 시계·빈 프레임·수신 FPS·JPEG 인코딩 시간을 잰다(플랜 7단계 입력).

센서 5대 단독 진단([지침서](../../docs/jetson-five-sensor-guide.md) §5 순서). 서비스와 **같은 어댑터**로 읽으며,
서비스가 같은 버스를 쓰는 동안에는 돌리지 않는다(프로세스 간 mux 잠금 없음):

```bash
PY=~/collector-venv/bin/python
$PY tools/sensor_check.py buses                                # 장치 파일·버스 클록(스캔 없음)
$PY tools/sensor_check.py mux --bus <N> --expect 0:0x33 1:0x33 # mux + 채널별 예상 주소만 확인
$PY tools/sensor_check.py thermal --bus <N> --channel 0 --count 20 --out thermal0.json   # + thermal0.npy
$PY tools/sensor_check.py point --bus <N> --channel 0 --count 30
$PY tools/sensor_check.py pt100 --cs-pin <핀> --ref-ohms <Ω> --jetson-model JETSON_ORIN_NANO --count 30
```

성공률·실효 Hz·취득 시간(min/mean/max)·최대 공백·재시도 수를 요약한다. 결과 JSON은 `notes/data/`에 정리한다.
