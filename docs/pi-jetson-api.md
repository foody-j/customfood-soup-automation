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
        "last_frame_at": "2026-09-12T05:00:00.000Z"  // 장치 시각
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

`kind` 값(현재): `rgb_gmsl2` · `depth_usb` · `thermal_i2c` · `point_temp_i2c`.
새 종류는 이 문서와 `pi-server/app/models.py`에 함께 추가한다.

## 3. `POST /api/v1/capture/start`

```jsonc
// 요청 — session_id는 Pi가 만든다(원본 저장 경로 키로 그대로 쓸 수 있는 형식)
{ "session_id": "sess-20260911T143000Z-a1b2", "name": "된장국 3차",
  "config": { "sensors": ["cam_rgb_0","thermal_0"], "fps": 10, "resolution": "1920x1536" } }

// 응답
{ "accepted": true, "session_id": "sess-...", "state": "running", "message": null }
```

- 이미 **같은** `session_id`가 진행 중이면 `accepted: true`(멱등).
- **다른** 세션이 진행 중이면 `accepted: false` + `message`에 사유. HTTP는 200으로 둔다
  (거절은 오류가 아니라 정상 응답이다). Pi는 이를 409로 사용자에게 전달한다.
- 5xx는 "서비스 고장"으로 해석된다.

## 4. `POST /api/v1/capture/stop`

```jsonc
{ "session_id": "sess-...", "reason": "사용자 중지" }
→ { "accepted": true, "session_id": "sess-...", "state": "stopped" }
```

- **저장 완료 후** 응답한다. `state: "stopped"`는 "원본 기록이 끝났다"는 뜻이다.
- 이미 멈춘 세션에 대한 중지는 `accepted: true`(멱등) — Pi는 통신 실패 시 재시도한다.
- 세션 ID가 현재 진행 중인 것과 다르면 `accepted: false`.

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
| 서로 다른 세션 | Pi 쪽 `unknown`으로 닫고 Jetson 쪽 채택 | `session.mismatch` (error) |
| Pi만 진행 중으로 앎 | `unknown`(확인 불가)으로 표시 | `session.orphaned` (warn) |
| Jetson만 진행 중 | Pi 기록으로 인계 | `session.adopted` (warn) |

Jetson 구현 시 주의: **통신이 끊겨도 진행 중 수집을 스스로 중단하지 않는다.**
Pi와의 연결은 관리 경로일 뿐 수집의 전제가 아니다(플랜 §4).

## 7. 미구현 · 다음 단계

| 항목 | 상태 |
|---|---|
| `sensors[].stats` · `last_session_summary` | **정의됨, Jetson 미구현** — §2.1. 모의 구현은 `pi-server/app/jetson/mock.py` |
| `GET /api/v1/preview/{sensor_id}.jpg` (저해상 미리보기) | **미정의** — 플랜 3단계에서 추가 |
| 센서별 설정 조회/변경 (`/api/v1/sensors/...`) | 미정의 — 4단계 |
| 인증 | 없음(로컬 유선망 전제). 운영 전 재검토 |
| 시계 오프셋 보고 | 미정의 — 장치 간 동기화 실측(5단계) 때 필드 추가 |

Jetson 쪽 구현은 아직 없다. Pi 쪽 클라이언트는
`pi-server/app/jetson/http_client.py`에 이 계약대로 이미 작성돼 있어,
`SOUP_JETSON_MODE=http`로 바꾸면 바로 붙는다.
