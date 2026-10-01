# Pi 관리 서버 ↔ Jetson 수집 서비스 API 계약

> 인계 플랜 2단계("Pi 관리 서비스 ↔ Jetson 수집 서비스 기본 통신")의 계약 문서.
> **이 문서가 제어·상태 채널 계약의 단일 출처다.** 바꾸면 `pi-server/app/models.py`를
> 함께 갱신한다.
>
> 조리 텔레메트리(1초 주기 `cooking/status` MQTT)는 별개 계약 — `docs/data-schema.md`.
> 둘은 목적이 다르다: **MQTT = 흘려보내는 상태 방송**, **이 API = 요청·응답 제어**.

---

## 0. 왜 MQTT가 아니라 HTTP인가

| | MQTT (`data-schema.md`) | 이 API |
|---|---|---|
| 성격 | 주기적 브로드캐스트(fire-and-forget) | 요청 1건 ↔ 응답 1건 |
| 쓰는 곳 | 조리 상태·열화상 프레임 → 대시보드·로봇 | 촬영 시작/중지, 상태 조회, 종료 요청 |
| 필요한 성질 | 다수 구독자, 최신값만 중요 | **성공/실패·거절 사유를 그 자리에서 받아야 함** |

촬영 시작 같은 명령은 "받아들여졌는지"를 즉시 알아야 하고, 실패 사유를 사용자에게
보여줘야 한다. MQTT로 하려면 요청/응답 토픽 쌍과 상관관계 ID를 직접 만들어야 하므로
제어 채널은 HTTP로 둔다. 두 채널은 병행한다(브로커는 계속 Pi에 있다).

## 1. 전송

- Jetson이 **서버**, Pi가 **클라이언트**. 기본 `http://<Jetson-IP>:8000`.
- 로컬 유선망 전용(D-009). 개발 단계에서는 인증 없음.
- 모든 시각은 **UTC ISO8601**(`2026-09-11T14:30:00.000Z`).
- 타임아웃은 Pi가 2초(`SOUP_PROBE_TIMEOUT`). 이 안에 못 받으면 무응답으로 본다.

### 호스트 생존 확인(별도)

Pi는 API 실패 시 **TCP 포트(기본 22)** 연결을 따로 시도한다. 이는 "수집 서비스만
죽음"과 "호스트 전체 무응답"을 구분하기 위한 것이며, Jetson 쪽 구현이 필요 없다.
SSH를 막았다면 `SOUP_JETSON_PROBE_PORT`를 상시 열린 다른 포트로 바꾼다.

---

## 2. `GET /api/v1/status`

수집 서비스의 자기 보고. Pi는 이 값을 **가공 없이** 보관·표시한다.

```jsonc
{
  "service": "jetson-collector",
  "version": "0.1.0",
  "device_time": "2026-09-11T14:30:00.000Z",  // Jetson 장치 시각(수신 시각으로 대체 금지)
  "uptime_sec": 3821.5,
  "accepting_new_capture": true,               // 종료 진행 중이면 false
  "capture": {
    "state": "running",                        // idle|starting|running|stopping|stopped|failed|unknown
    "session_id": "sess-20260911T143000Z-a1b2",
    "started_at": "2026-09-11T14:30:00.000Z",
    "frames_written": 12040,
    "frames_dropped": 3,
    "last_error": null
  },
  "storage": { "path": "/data/raw", "total_bytes": 456000000000, "free_bytes": 411000000000 },
  "sensors": [
    { "sensor_id": "cam_rgb_0", "kind": "rgb_gmsl2", "connected": true,
      "simulated": false, "detail": "ISX031F /dev/video4" }
  ],
  "mock": false                                // 모의 장치가 만든 보고면 true
}
```

### 2.1 확장 필드 — **Jetson이 추가로 제공해야 할 것** (2026-09-12 추가)

Pi의 실험 기록 기능이 쓰는 값이다. **전부 선택(optional)** 이라 없어도 기존 동작은
그대로지만, 없으면 Pi 화면·내보내기에 `미확인`으로 표시된다.

```jsonc
{
  "sensors": [
    {
      "sensor_id": "cam_rgb_0", "kind": "rgb_gmsl2", "connected": true, "simulated": false,
      "stats": {                       // ← ① 센서별 수집 통계 (누적)
        "frames_written": 12040,
        "frames_dropped": 3,
        "bytes_written": 2170000000,   // 모르면 생략/null
        "fps_measured": 9.8,           // 실제 측정 FPS
        "last_frame_at": "2026-09-12T05:00:00.000Z"  // 장치 시각(Jetson 호스트 UTC, 마지막으로 저장된 프레임의 수신 시각)
      }
    }
  ],
  "last_session_summary": {            // ← ② 가장 최근에 **닫힌** 세션의 저장 결과
    "session_id": "sess-20260912T040348Z-62fe",
    "path": "/data/raw/sess-...",
    "files": 155,
    "bytes_written": 27900000,
    "frames_written": 310,
    "frames_dropped": 0,
    "closed_at": "2026-09-12T04:03:52.000Z",   // 저장 완료 시각(장치 시각)
    "ok": true,                                 // 온전히 끝났는지. 모르면 null
    "note": null
  }
}
```

**왜 필요한가**

- ① `stats` — Pi는 프레임 단위 기록을 가져오지 않는다(원본은 Jetson 것). 대신 이
  누적 요약만 받아 "어느 센서가 얼마나 찍혔고 얼마나 놓쳤는지"를 화면에 표시한다.
- ② `last_session_summary` — **Pi는 중지 응답만으로 "저장 완료"라고 기록하지 않는다.**
  이 요약을 받아야 비로소 `capture.save_confirmed` 사건을 남기고 실험 기록에 박제한다.
  요약이 오지 않으면 그 실험의 저장 결과는 영원히 `미확인`으로 남는다.
  → **세션을 닫을 때 반드시 채울 것.** `ok=false`면 Pi가 `capture.save_incomplete`(error)로 남긴다.

`closed_at`·`last_frame_at`은 **장치 시각**이다. Pi는 이를 사건의 `occurred_at`에 넣고
자신이 받은 시각은 `ts`에 따로 남긴다(둘을 섞지 않는다).

**필드 규약**

- `sensors[].simulated` — 실물 연동이 끝나지 않은 센서는 반드시 `true`. 모의 구현을
  "연동 완료"로 표시하지 않기 위한 장치다(플랜 §4).
- `capture.session_id` — Pi가 만들어 내려보낸 값을 그대로 되돌려준다.
- 모르는 값은 필드를 생략하지 말고 `null`로 보낸다(Pi 파싱 안정성).
- Pi는 모르는 필드를 받아도 죽지 않는다. 반대로 **Pi가 아는 필드의 타입이 바뀌면**
  스키마 오류로 처리해 `service_down`으로 표시한다.

`kind` 값(현재): `rgb_gmsl2` · `depth_usb` · `thermal_i2c` · `rtd_spi`(MAX31865 + PT100).
새 종류는 이 문서와 `pi-server/app/models.py`에 함께 추가한다.
2026-10-01(D-039): Orbbec Gemini 2(`cam_depth_0`, `depth_usb`)는 장비 구성에서 뺀다. 과거 세션 호환을 위해 `depth_usb` 값과
`cam_depth_0` 응답 처리는 계약에서 지우지 않으며, 새 세션의 기본 센서 목록에는 넣지 않는다.

2026-09-25 동기화(D-030·D-031): MLX90614의 `point_temp_0/1`·`point_temp_i2c`는 현재 수집 목록에서
제외했다. 열화상은 `thermal_0`(D55 한 대, I²C 직결), PT100은 `pt100_0`(개발·검증용)이다.
Pi 모의 목록은 카메라 3대와 열화상 한 대이며(2026-09-27 첫 조리 시험 준비로 `pt100_0` 모의 항목 추가 — `simulated:true`),
실제 연동 모드에서는 Jetson이 보고한 목록을 그대로 표시한다.
과거 세션에 남은 센서 ID나 미지의 `kind` 문자열을 삭제·차단하지 않는다.
솥 내장 온도센서는 모델의 운영 온도원으로 계획했지만 읽기 인터페이스와 수집 담당 장비가 미확정이므로,
연결된 Jetson 센서로 추가하지 않는다. 값을 읽지 못하면 PT100을 운영에 사용하는 방안을 검증한다.
Gemini 2의 `ir` 영상 스트림은 MLX90614와 별개였다(D-039로 Gemini 2는 구성에서 제외).

### 확장 필드 (2026-09-12, Jetson 구현이 추가로 보냄 — Pi는 무시해도 됨)

계약 필드의 **타입은 바꾸지 않았다.** 아래는 Jetson이 덧붙이는 값이며 Pi 화면이 쓰려면
`pi-server/app/models.py`에 같은 이름으로 추가하면 된다(없어도 파싱은 통과한다).

| 위치 | 필드 | 뜻 |
|---|---|---|
| 최상위 | `device_id`, `schema_version`, `sensor_mode` | 장치 식별·저장 스키마 버전·센서 모드(mock/auto/real) |
| 최상위 | `clock` | `boot_id`, `monotonic_ns`, `realtime_minus_monotonic_ns`, `ntp_synchronized`, `ntp_offset_ms`(미확인이면 null), `ntp_jitter_ms`, `ntp_root_dispersion_ms` |
| 최상위 | `system` | CPU·GPU·메모리·온도·디스크 스냅샷(약 5초 주기, 측정 불가 항목 null) |
| 최상위 | `last_session` | 마지막으로 끝난 세션 요약(`end_reason`, 경로, manifest 요약) |
| 최상위 | `recovered_sessions` | 기동 시 발견한 **중단된** 세션 목록(완료로 보고하지 않음) |
| 최상위 | `errors` | 서비스 수준 오류 최근 목록 |
| `capture` | `phases` | `requested`/`starting`/`running`/`stop_requested`/`stopping`/`files_closed`/`completed` 시각(UTC), 스트림별 `first_sample:*` |
| `capture` | `streams[]` | 스트림별 `received`/`written`/`bytes_written`/`invalid`/`dropped{사유:수}`/`gaps_detected`(근거 없으면 null)/`backlog`/`recv_fps`/`write_fps`/`connected`/`reconnects` |
| `capture` | `frames_dropped_detected`, `frames_invalid`, `writer_backlog`, `checksum_state`, `stop_reason`, `name` | 집계·상태 |
| `sensors[]` | `model`, `serial`, `driver`, `verified`, `reason`, `streams` | `verified`=실기기로 검증됐는지(코드가 있어도 검증 전이면 false). `reason`=미연결 사유 |

## 3. `POST /api/v1/capture/start`

```jsonc
// 요청 — session_id는 Pi가 만든다(원본 저장 경로 키로 그대로 쓸 수 있는 형식)
{ "session_id": "sess-20260911T143000Z-a1b2", "name": "된장국 3차",
  "config": { "sensors": ["cam_rgb_0","thermal_0"], "fps": 10, "resolution": "1920x1536" } }

// 응답
{ "accepted": true, "session_id": "sess-...", "state": "running", "message": null }
```

`config.max_duration_sec`(선택, 초, 2026-09-27): running 진입 뒤 이 시간이 지나면 Jetson이 세션을 **정상 중지**한다
(`stop_reason = "max_duration_sec=<값> 도달"`, 이벤트 `session.max_duration`). 없거나 0이면 제한 없음 — 기존 동작 그대로.
Jetson `/viewer` 점검 세션은 기본 600초로 연다.

**Pi 동작(2026-09-27):** Pi 실험 설정(`PUT /api/config`)의 `max_duration_sec`(0~86400, 0=제한 없음)을 저장·재조회하고,
시작 요청의 `config` **최상위**에 그대로 싣는다(`extra.max_duration_sec`는 422로 거절). 화면은 시작 전에 값을 고르고
확인창에서 다시 보여 준다 — 운영 세션에 몰래 제한을 걸지 않는다. Pi는 브라우저 타이머로 멈추지 않는다.
종료 이유는 `capture.stop_reason`/`last_session.stop_reason`의 `max_duration_sec=` 접두사로만 판단하고
(`end_reason=stopped`만으로 시간 제한이라고 추정하지 않음) 세션의 `jetson_end`에 `stop_reason`·`end_reason`·`phases`를 남긴다
(이벤트 `capture.auto_stopped`, 수동 중지 등은 `capture.stop_reason`). **데이터 촬영 종료일 뿐 인덕션 전원과 무관하다.**
시작 요청은 `config.preview.enabled`를 켜기/끄기 모두 명시해 보낸다(저장된 `depth_max_mm` 등은 유지).

- 이미 **같은** `session_id`가 진행 중이면 `accepted: true`(멱등).
- **다른** 세션이 진행 중이면 `accepted: false` + `message`에 사유. HTTP는 200으로 둔다
  (거절은 오류가 아니라 정상 응답이다). Pi는 이를 409로 사용자에게 전달한다.
- 5xx는 "서비스 고장"으로 해석된다.

### 3.1 라이브 보기 — 기록 없는 미리보기 세션 (계약 2026-09-28, D-037 · Jetson 구현 2026-09-28 `jetson/live-view`)

카메라 위치·초점·솥 배치를 맞출 때 **원본을 저장하지 않고** 미리보기만 보기 위한 세션이다.
기존 시작·중지·미리보기·상태 계약을 그대로 쓰고 `config.record`만 추가한다(선택 필드, 기본 `true`).

```jsonc
// Jetson이 지원을 알린다 — GET /api/v1/status 최상위(확장 필드)
{ ..., "capabilities": ["live_view"] }

// 요청 — Pi는 위 capability를 확인한 뒤에만 record:false를 보낸다
{ "session_id": "live-20260928T050000Z-a1b2", "name": "라이브 보기",
  "config": { "sensors": ["cam_rgb_0","cam_rgb_1","thermal_0","pt100_0"], "fps": 10,
              "record": false, "preview": { "enabled": true, "max_fps": 1 }, "max_duration_sec": 600 } }

// 진행 중 status — 기록하지 않음을 되돌려 확인해 준다
{ "capture": { "state": "running", "session_id": "live-...", "record": false, "frames_written": 0, ... } }
```

| 규칙 | 내용 |
|---|---|
| **호환(필수)** | 옛 Jetson은 모르는 `record`를 무시하고 **녹화한다**. 그래서 Pi는 `capabilities`에 `live_view`가 있을 때만 `record:false`를 보낸다. 없으면 라이브 보기 버튼 자체를 숨긴다. `record`가 없거나 `true`면 기존 동작과 완전히 같다 |
| 저장 | 원본(프레임·`records.bin`·`index.jsonl`)과 manifest를 쓰지 않는다. `data_root`에 세션 디렉터리를 만들지 않아 `/api/v1/sessions` 녹화 목록에 섞이지 않는다(디스크 증가 ≈ 0). 사건은 서비스 로그에 남긴다 |
| 미리보기 | `record:false`면 `preview.enabled`를 **켠 것으로 강제**한다(보는 것이 목적). JPEG·`preview_array` 엔드포인트와 규칙은 그대로 |
| 최대 시간 | `max_duration_sec`이 없거나 0이면 Jetson이 **600초**를 적용한다(센서를 잡고 잊히는 것 방지). 정지 사유는 기존과 같은 `max_duration_sec=<값> 도달` |
| 상태 | `capture.record`(확장 필드)로 `false`를 되돌려준다. `frames_written`은 0, 수신량은 `streams[].received`로 본다. 상태 전이(running → stopping → stopped)는 기존과 같다 — 기록기 마무리가 없어 빨리 끝난다 |
| 저장 결과 | `last_session_summary`를 **갱신하지 않는다**(직전 녹화의 요약을 유지 — Pi가 라이브를 '저장 완료'로 적지 않게). `last_session`에는 `record:false`로 남겨도 된다 |
| 배타성 | 세션은 한 번에 하나. 라이브 중 다른 `session_id`의 시작은 기존처럼 `accepted:false`. Pi는 라이브를 먼저 중지한 뒤 녹화를 시작한다 |
| 세션 ID | Pi가 `live-` 접두사로 만든다(녹화 `sess-`와 구분). 저장 디렉터리가 없으므로 같은 ID 재사용 검사는 필요 없다 |

**Pi 동작:** `POST /api/live/start`(선택 본문 `{max_duration_sec}`)가 저장된 실험 설정의 센서·fps에 `record:false`,
미리보기 켬, 최대 시간(기본 600초)을 얹어 시작한다. Jetson이 `live_view`를 알리지 않으면 409.
시작 후 Jetson status가 이 세션을 `record:false`로 되돌려주지 않으면 녹화 중일 수 있으므로 **즉시 중지 요청**하고
`live.not_confirmed`(error)를 남긴다. 라이브 세션은 Pi 기록에 남되 '라이브 보기(저장 안 함)'로 표시하고 저장 결과를 기다리지 않는다.
중지는 기존 `POST /api/capture/stop`. 화면의 '촬영 시작'은 라이브 중이면 라이브를 끝낸 뒤 녹화를 시작한다.

## 4. `POST /api/v1/capture/stop`

```jsonc
{ "session_id": "sess-...", "reason": "사용자 중지" }
→ { "accepted": true, "session_id": "sess-...", "state": "stopped" }
```

- **저장 완료 후** 응답한다. `state: "stopped"`는 "원본 기록이 끝났다"는 뜻이다.
- 이미 멈춘 세션에 대한 중지는 `accepted: true`(멱등) — Pi는 통신 실패 시 재시도한다.
- 세션 ID가 현재 진행 중인 것과 다르면 `accepted: false`.
- **Pi 타임아웃(2초)보다 저장 마무리가 길 수 있다.** 그 경우 Pi는 `ReadTimeout`으로 세션을
  `stopping`에 두고(`capture.stop_deferred`), 이후 status의 `capture.state == stopped`를 보고
  `capture.stop_confirmed`로 마무리한다. Jetson은 `COLLECTOR_STOP_WAIT`(기본 120초)까지 기다린 뒤
  그래도 안 끝나면 `accepted:true, state:"stopping"`으로 응답한다 — Pi는 이 응답이면 세션을 **`stopping`으로 두고**
  (`capture.stop_accepted`) status 재동기화로 `stopped`/`failed`와 저장 결과를 받아 마무리한다(2026-09-27 수정 —
  이전에는 곧바로 `stopped`로 표시했다). `state:"failed"` 응답은 `failed`로 기록한다.
- 진행 중 세션이 없으면 어떤 `session_id`든 `accepted: true`(멱등 — 이미 멈춰 있음).
- 끝난 세션의 `session_id`로 다시 start하면 `accepted:false`(저장 디렉터리가 겹치므로). 새 ID가 필요하다.

## 5. `POST /api/v1/system/shutdown`

정상 종료. **순서를 지킨다**(플랜 §5): 새 촬영 차단 → 수집 중지·저장 완료 → OS 종료.

```jsonc
→ { "accepted": true, "state": "stopping", "message": "정상 종료 진행 중" }
```

응답 직후 `accepting_new_capture: false`가 되고, 잠시 뒤 API가 내려간다. Pi는
이를 무응답으로 보되 **전원이 꺼졌다고는 말하지 않는다**(전원 제어기 근거가 없으면).

## 6. 상태 불일치 처리 — 누가 사실인가

통신이 끊겼다 돌아오면 Pi는 매 정상 응답마다 자기 기록과 위 `capture`를 대조한다.

> **진행 중 촬영의 사실관계는 Jetson이 주인이다.** 원본을 쓰는 쪽이 Jetson이기 때문.
> Pi는 어긋난 사실을 기록으로 남기고 Jetson 쪽을 채택한다.

| 상황 | Pi 처리 | 이벤트 |
|---|---|---|
| 같은 세션 진행 중 | Jetson 상태로 동기화 | — |
| 같은 세션이 Jetson에서 `stopping` | **정상 마무리 단계** — Pi도 `stopping`으로 두고 기다림(고아 처리 안 함) | 중지 사유를 처음 받으면 `capture.auto_stopped`/`capture.stop_reason` |
| 같은 세션이 Jetson에서 `stopped`/`failed` | 그 상태로 종료 | `capture.stop_confirmed` |
| 서로 다른 세션 | Pi 쪽 `unknown`으로 닫고 Jetson 쪽 채택 | `session.mismatch` (error) |
| Pi만 진행 중으로 앎 | `unknown`(확인 불가)으로 표시 | `session.orphaned` (warn) |
| Jetson만 진행 중 | Pi 기록으로 인계 | `session.adopted` (warn) |

Jetson 구현 시 주의: **통신이 끊겨도 진행 중 수집을 스스로 중단하지 않는다.**
Pi와의 연결은 관리 경로일 뿐 수집의 전제가 아니다(플랜 §4).

## 7. 미구현 · 다음 단계

| 항목 | 상태 |
|---|---|
| `sensors[].stats` · `last_session_summary` | **Jetson 구현됨(2026-09-12)** — §2.1. `stats`는 진행 중 세션의 스트림 통계를 센서 단위로 합산(세션 없으면 null), `last_session_summary`는 파일 close·manifest 기록 후 확정(`ok=false`면 `note`에 사유) |
| 열화상·PT100 JSON 미리보기 | **Pi 연동됨(2026-09-27)** — Pi `GET /api/preview_array/{sensor_id}/{stream_id}?session_id=`가 Jetson `preview_array`를 값 변경 없이 중계한다(+`pi_received_at`, `mock`). 진행 중 세션 없음·값 없음 404, 요청 `session_id`가 진행 중 세션과 다르면 409, Jetson 무응답 503, Jetson 오류 502. 카드 설정은 `GET /api/sensor-preview/config`(`SOUP_SENSOR_PREVIEWS`). 200은 "캐시에 값이 있음"일 뿐이라 화면이 `seq` 정체로 갱신 지연을 판단한다 |
| 저해상 미리보기 | **Jetson 구현됨(2026-09-18), Pi 연동됨(2026-09-18)** — 아래 `GET /api/v1/capture/preview/{sensor_id}/{stream_id}`. 활성 세션에서 요청 시에만 사용, 원본 저장과 분리. Pi는 `GET /api/preview/{sensor_id}/{stream_id}`로 그대로 중계하고(저장 안 함) 시작 요청의 `config.preview`로 켠다 — 계약 변경 없음 |
| 센서별 설정 조회/변경 (`/api/v1/sensors/...`) | 미정의 — 4단계 |
| 인증 | 없음(로컬 유선망 전제). 운영 전 재검토 |
| 시계 오프셋 보고 | 확장 필드 `clock`으로 1차 제공(NTP 오프셋은 timesyncd가 노출하지 않아 null). 장치 간 보정은 5단계 |

### Jetson 쪽 추가 엔드포인트 (2026-09-12, 계약 확장 — Pi 클라이언트는 아직 쓰지 않음)

| 엔드포인트 | 뜻 |
|---|---|
| `GET /api/v1/health` | 생존 확인 `{ok:true}` |
| `GET /api/v1/sessions?limit=` | 저장된 세션 목록(session.json 요약) |
| `GET /api/v1/sessions/{session_id}` | `session`(메타) + `manifest`(결과 목록) + `live`(진행 중이면 현재 통계) |
| `POST /api/v1/capture/config` | 실험 중 설정 변경 `{session_id?, sensor_id, changes}` → `{applied, before, after}` 또는 `{accepted:false, message}`. 변경 시각·전후 값이 세션 기록에 남는다 |
| `GET /api/v1/capture/preview/{sensor_id}/{stream_id}?session_id=` | `config.preview.enabled=true`로 시작한 활성 세션의 최근 축소 JPEG. `max_fps` 기본 1, 상한 2. `config.preview.depth_max_mm`(기본 4000, 100~65535)로 깊이 의사색 범위를 정한다 — 작업 거리 0.5 m에서는 1000~1500 권장. 아직 프레임이 없거나 종료되면 404. `Cache-Control: no-store`, 세션 ID·수신 UTC·시퀀스 응답 헤더 포함. 원본 파일·세션 저장을 대신하지 않는다 |
| `GET /api/v1/capture/preview_array/{sensor_id}/{stream_id}?session_id=` | 그림이 아닌 스트림의 최신 미리보기(JSON). 열화상 `temp_array`는 0.1 ℃ 정수 배열(`rows`·`cols`·`deci`·`min/max/mean`), PT100 같은 스칼라 스트림은 `{kind:"scalar", valid, value, invalid_reason}`(fault 샘플도 표시). 규칙은 JPEG 미리보기와 같다 |

Jetson 구현: `jetson/collector/` (README 참고). Pi 쪽 클라이언트
`pi-server/app/jetson/http_client.py`는 `SOUP_JETSON_MODE=http`로 바꾸면 그대로 붙는다
(2026-09-12 Jetson에서 Pi 서버 코드를 HTTP 모드로 띄워 왕복 검증 완료 — `notes/dev-log.md`).
