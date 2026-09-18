# Jetson 수집 서비스 구현 지시서 (플랜 3단계 착수)

> 이 문서는 **Jetson 실기기에서 작업하는 에이전트/개발자용 작업 지시서**다.
> 작성: 2026-09-11, Pi에서. 계약 문서는 `docs/pi-jetson-api.md`(단일 출처).
> Pi 쪽 상대편 구현은 `pi-server/`에 이미 완성돼 있고 모의 Jetson으로 검증됐다.
> **현재 상태(2026-09-18):** 이 문서의 Gemini 2 "미연동" 표기는 작성 시점의 기록이다.
> Jetson SDK 어댑터와 실행 절차는 [`gemini2-jetson-setup.md`](gemini2-jetson-setup.md) 참고.
> 실기기 촬영 검증은 아직 남아 있다.

---

## 0. 시작 전에 반드시 할 것

```bash
cd ~/customfood-soup-automation && git pull        # D-005: 작업 전 pull 필수
cat docs/pi-jetson-api.md                          # 계약 — 이 문서가 사실상의 스펙
cat CLAUDE.md                                      # 과제 관리 규칙(노트 갱신 의무)
```

Pi 쪽 참고 구현을 먼저 읽으면 이해가 빠르다. 특히 어떤 값을 어떻게 해석하는지:

- `pi-server/app/models.py` — 주고받는 스키마(Pi 계약의 단일 출처)
- `pi-server/app/jetson/mock.py` — **모의 Jetson이 곧 기대 동작의 참조 구현이다.**
  상태 전이·멱등 처리·거절 응답을 여기 맞춰 만들면 Pi가 그대로 붙는다.
- `pi-server/app/capture.py` — Pi가 불일치를 어떻게 처리하는지(재동기화 규칙)

## 1. 먼저 실기기 상태를 확인하고 **보고부터** 하라

추측으로 코드를 쓰지 말 것. 아래를 실행해 결과를 먼저 정리한다.

```bash
# 카메라 노드와 지원 포맷
ls -l /dev/video*
v4l2-ctl --list-devices
v4l2-ctl -d /dev/video4 --list-formats-ext | head -40

# Orbbec Gemini 2가 실제로 붙어 있는지 (USB)
lsusb | grep -i -E "orbbec|2bc5"

# I2C 열화상/점온도 (MLX90640 / MLX90614)
ls /dev/i2c-*
i2cdetect -y -r 7 2>/dev/null || echo "(버스 번호는 배선 확인 필요)"

# 저장소·파이썬
df -h / && python3 -V && python3 -c "import cv2; print('cv2', cv2.__version__)"
ip -brief addr                                    # Pi(10.42.0.1)와 같은 대역인지
```

**보고 항목**: 붙어 있는 센서 목록, `/dev/video4`의 실제 포맷·해상도·최대 FPS,
Gemini 2 유무, 저장 여유 공간, Python/OpenCV 버전, Jetson의 IP.

> 문서상 확정된 것: Orin Nano Super 8GB · JetPack R36.4.3 · NVMe 456GB ·
> FG12-4CH + **ISX031F 1대 = `/dev/video4` 캡처 검증 완료**.
> Gemini 2·열화상·적외선은 **문서상 미연동**이다. 실물이 없으면 없다고 보고할 것.

## 2. 만들 것

```
jetson/collector/
  app/
    main.py          FastAPI 조립 + lifespan
    config.py        환경변수 설정 (COLLECTOR_*)
    models.py        docs/pi-jetson-api.md 스키마 (Pi의 models.py와 필드 일치)
    session.py       촬영 세션 상태기계 · 저장 완료 보장
    storage.py       세션 디렉터리 · manifest 기록 · 디스크 여유 확인
    sensors/
      base.py        SensorAdapter 인터페이스
      mock.py        모의 센서 (항상 simulated=True)
      v4l2.py        ISX031F GMSL2 (/dev/video4)
      orbbec.py      Gemini 2 — SDK 없으면 "미지원"으로 명시 (스텁 금지, 거짓 보고 금지)
    routes.py        API
  systemd/jetson-collector.service
  requirements.txt
  README.md
  tests/             모의 센서로 도는 테스트
```

Pi 쪽(`pi-server/`)과 같은 구조·같은 원칙으로 만들면 된다. 코드를 공유 패키지로
묶지 말 것 — 두 서비스는 독립 배포되고, 계약 문서가 접점이다.

## 3. API 동작 — 반드시 지킬 것

`docs/pi-jetson-api.md`가 스펙이고, 아래는 그중 틀리기 쉬운 부분이다.

| 요구 | 의미 |
|---|---|
| **중복 시작 거절** | 다른 세션이 진행 중이면 `accepted:false` + `message`에 사유. **HTTP는 200.** 거절은 오류가 아니라 정상 응답이다(5xx로 답하지 말 것) |
| **같은 세션 재시작은 멱등** | 같은 `session_id`로 다시 start → `accepted:true` |
| **중지는 멱등** | 이미 멈춘 세션에 stop → `accepted:true`. Pi는 통신 실패 시 재시도한다 |
| **저장 완료 후 응답** | `state:"stopped"`는 "버퍼 flush·파일 close까지 끝났다"는 뜻. 파일 쓰기 전에 응답하지 말 것 |
| **통신이 끊겨도 수집 계속** | Pi 연결은 관리 경로일 뿐 수집의 전제가 아니다. Pi가 안 보인다고 세션을 끊지 말 것 |
| **`simulated` 정직하게** | 실물 연동이 끝나지 않은 센서는 `simulated:true`. 모의 데이터를 실연동으로 표시하면 안 된다 |
| **`device_time`** | Jetson 장치 시각(UTC ISO8601). 값을 모르면 `null` — 수신 시각으로 대체 표기 금지 |
| **정상 종료 순서** | shutdown: 새 촬영 차단 → 수집 중지·저장 완료 → OS 종료. 순서를 건너뛰지 말 것 |

`session_id`는 **Pi가 만들어 내려보낸 값을 그대로 쓴다.** Jetson이 새로 만들지 않는다.

## 3.5 Pi가 필요로 하는 확장 필드 (2026-09-12 추가)

`docs/pi-jetson-api.md` **§2.1**에 정의된 두 필드를 `GET /api/v1/status`에 실어야 한다.
없어도 통신은 되지만 Pi 화면·실험 기록에 `미확인`으로 남는다.

1. **`sensors[].stats`** — 센서별 누적 통계(기록 프레임·누락·측정 FPS·마지막 프레임 시각).
   프레임 단위 기록은 Pi로 보내지 않는다. 이 누적 요약만 보낸다.
2. **`last_session_summary`** — 세션을 닫을 때 확정하는 **저장 결과 요약**
   (파일 수·바이트·프레임·`closed_at`·`ok`).
   **Pi는 중지 응답만으로 "저장 완료"라고 기록하지 않는다.** 이 요약을 받아야
   `capture.save_confirmed`를 남기므로, 세션 종료 처리에 반드시 포함할 것.

참조 구현(모의)은 `pi-server/app/jetson/mock.py`의 `_sensor_stats()` · `_build_summary()`.
→ **2026-09-12 Jetson 구현 완료**: `jetson/collector/app/service.py`의 `_sensor_stats()` · `_on_session_finished()`.

## 4. 저장 레이아웃

```
<DATA_ROOT>/<session_id>/
  manifest.jsonl              프레임 1개 = 1줄 (JSON Lines)
  session.json                세션 메타(설정 스냅샷·센서 목록·시작/종료 시각·버전)
  <sensor_id>/000001.jpg ...  센서별 원본
```

`manifest.jsonl` 한 줄의 필수 필드:

```jsonc
{"session_id":"sess-...","sensor_id":"cam_rgb_0","frame_id":1,
 "host_recv_ts":"2026-09-11T14:30:00.123Z",   // 호스트가 프레임을 받은 시각 (항상 기록)
 "device_ts":"2026-09-11T14:30:00.118Z",      // 장치가 주는 노출/캡처 시각. 못 얻으면 null
 "path":"cam_rgb_0/000001.jpg","bytes":184320,
 "width":1920,"height":1536,"pixel_format":"UYVY","exposure":null,"gain":null}
```

> **`device_ts`를 모를 때 `host_recv_ts`를 복사해 넣지 말 것.** 수신 시각을 노출 시각으로
> 표기하지 않는다는 것이 인계 플랜 §4의 명시 규칙이다. 모르면 `null`이 정답이다.

기록 실패(디스크 부족·장치 분리)는 삼키지 말고 세션을 `failed`로 닫고 `last_error`에 남긴다.

## 5. 센서 어댑터

```python
class SensorAdapter(Protocol):
    sensor_id: str
    kind: str            # rgb_gmsl2 | depth_usb | thermal_i2c | point_temp_i2c
    simulated: bool
    def probe(self) -> SensorInfo: ...        # 연결 여부·지원 기능
    def open(self, config: dict) -> None: ...
    def read(self) -> Frame | None: ...       # 타임스탬프 포함
    def close(self) -> None: ...
```

- **V4L2(ISX031F)**: 1절에서 확인한 실제 포맷/해상도를 그대로 쓴다. OpenCV
  `VideoCapture(4, cv2.CAP_V4L2)`로 시작하되, 포맷 협상이 안 되면 GStreamer 파이프라인으로 전환.
  **Orin Nano에는 NVENC가 없다** — 영상 인코딩은 CPU다. 초기에는 프레임 단위 JPEG 저장으로
  두고 실제 FPS·CPU·기록 속도를 측정해 보고할 것(플랜 7단계 입력).
- **Orbbec Gemini 2**: SDK v2가 설치돼 있지 않으면 어댑터를 "미지원"으로 보고한다.
  동작하는 척하는 스텁을 만들지 말 것.
- 장치 분리/재접속을 처리한다. 한 센서가 죽어도 서비스 전체가 죽으면 안 된다.

## 5.5 로그 — 처음부터 "관리"까지 설계할 것

> **이 항목을 나중으로 미루지 말 것.** 이전 프로젝트(울산)에서 로그를 남기기만 하고
> 보존·조회 설계를 빠뜨려 실제로 애먹은 적이 있다. 수집 서비스는 장시간 무인으로 돌기
> 때문에 "그때 무슨 일이 있었는지"를 사후에 못 읽으면 실험 자체를 다시 해야 한다.

Pi 쪽 구현(`pi-server/app/logging_setup.py`, `pi-server/README.md`의 "로그" 절)을
그대로 참고해 같은 방식으로 만든다. 반드시 지킬 것:

1. **로깅 설정은 앱 조립 지점(`create_app()` 등)에서 한다.** `main()`/`if __name__`
   블록에만 두면 systemd가 `uvicorn app.main:app`으로 띄울 때 실행되지 않아
   **배포 환경에서만 로그가 사라진다.** Pi 쪽에서 실제로 났던 버그다.
2. **접속 로그(uvicorn)를 같은 포맷·같은 목적지로 합친다.** `uvicorn.run(..., log_config=None)`
   또는 uvicorn 로거의 `propagate=True` 처리. 갈라져 있으면 장애 시각 대조가 어렵다.
3. **레벨·파일 경로·회전 크기를 환경변수로 노출**하고 기본값은 journald(표준출력)로 둔다.
   파일로 남길 때는 **반드시 크기 기반 회전**(`RotatingFileHandler`)을 쓴다 —
   무한히 커지면 NVMe가 차고 수집이 멈춘다.
4. **세 가지 기록을 구분**한다.
   | 무엇 | 어디에 | 비고 |
   |---|---|---|
   | 서비스 로그 | journald(+회전 파일) | 프로그램이 어떻게 돌았나 |
   | 프레임 기록 | `manifest.jsonl` | 무엇을 찍었나 — 4절 |
   | 세션 메타 | `session.json` | 설정·장치·버전 스냅샷 |
5. **수집 중 반드시 남길 것**: 세션 시작/종료(세션ID 포함), 프레임 누락·드롭 수,
   장치 분리/재연결, 디스크 여유 경고, 저장 실패, 예외 스택. 조용히 삼키지 말 것.
6. **주기 로그는 과하지 않게.** 프레임마다 로그를 찍으면 10 FPS × 수 시간에 로그가
   원본보다 커진다. 프레임 단위 기록은 manifest가 담당하고, 서비스 로그는
   N초 요약(기록 FPS·누적 프레임·드롭)으로 남긴다.
7. **`journalctl -u jetson-collector` 로 무엇을 볼 수 있는지 README에 적는다.**

→ **2026-09-12 반영**: `jetson/collector/app/logging_setup.py`(create_app에서 호출, uvicorn 합침,
`COLLECTOR_LOG_*` 환경변수, 크기 회전), 세션 로그는 시작/중지/분리/실패 + `COLLECTOR_LOG_SUMMARY_INTERVAL`초 요약.

## 6. 실행 환경

```bash
python3 -m venv ~/collector-venv --system-site-packages   # cv2(JetPack 제공) 쓰려면 필요
~/collector-venv/bin/pip install fastapi "uvicorn[standard]"
```

벤치용 `~/cf-venv`(torch 포함)는 건드리지 말고 별도 venv를 쓴다.
기본 바인딩은 `0.0.0.0:8000`(계약 기본값). 저장 경로는 환경변수로 바꿀 수 있게 하고
기본값은 홈 아래(`~/collector-data`)로 둔다 — `/data`는 sudo가 필요하다.

## 7. 검증 — Pi와 실제로 왕복시킬 것

모의 센서만으로도 여기까지는 반드시 확인한다.

```bash
# Jetson에서 서비스 기동 후, 자기 자신 확인
curl -s localhost:8000/api/v1/status | python3 -m json.tool

# Pi(10.42.0.1)에서 — Jetson IP를 1절에서 확인한 값으로
#   /etc/default/soup-pi-server 또는 실행 환경변수:
#     SOUP_JETSON_MODE=http
#     SOUP_JETSON_URL=http://<JetsonIP>:8000
#     SOUP_JETSON_PROBE_PORT=22
#   → Pi 관리 화면(8100)에서 "정상"으로 뜨고, 촬영 시작/중지가 왕복되면 3단계 통신 성립
```

체크리스트:

- [ ] Pi 화면에 `정상`, 센서 목록·저장소 여유가 표시된다
- [ ] Pi에서 촬영 시작 → Jetson에 세션 디렉터리·manifest가 생긴다
- [ ] 진행 중 다시 시작 → Pi에 409(=Jetson이 `accepted:false`)
- [ ] 중지 → 파일이 닫힌 뒤 응답, Pi 세션이 `stopped`
- [ ] **Jetson 서비스만 죽여 본다** → Pi가 `수집 서비스 중단`으로 표시(호스트는 살아 있음)
- [ ] **랜선을 뽑아 본다** → Pi가 `무응답`으로 표시하고, 다시 꽂으면 진행 중 세션을 인계받는다
      (`session.adopted` 이벤트) — 이게 재동기화가 실제로 도는지 보는 시험이다

## 8. 하지 말 것

- 미확정 하드웨어를 확정된 것처럼 코드/문서에 박기 (Gemini 2·열화상·플래시는 아직 미연동)
- 모의 구현을 "연동 완료"로 표시 (`simulated` 플래그를 생략하거나 false로 두기)
- Pi 연결이 끊겼다고 수집을 중단하거나 스스로 전원을 끄기
- 계약(`docs/pi-jetson-api.md`)을 말없이 바꾸기 — 바꿔야 하면 **문서와 `pi-server/app/models.py`를
  함께 고치고** 이유를 `notes/decisions.md`에 남긴다
- AI 추론 기능 추가 — 이번 범위 밖이다(플랜 8단계)

## 9. 마무리

- `notes/dev-log.md`에 날짜와 함께 기록 (실기기에서 겪은 문제·해결을 반드시 포함 —
  `JETSON_SETUP.md` 때처럼 문서와 달랐던 점이 가장 값지다)
- 설계 결정이 생기면 `notes/decisions.md`에 "결정/이유/대안"
- 실측값(FPS·CPU·기록 속도·디스크 증가율)은 `notes/data/`에 정리
- 커밋은 의미 단위·한국어. **D-005에 따라 Jetson에서 직접 커밋·푸시해도 된다**
  (단 작업 전 `git pull` 먼저). 무인 실행이면 `ai/<작업명>` 브랜치 + push 금지.
