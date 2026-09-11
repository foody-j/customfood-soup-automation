# Pi 관리 서버 (FastAPI)

Raspberry Pi 5에서 **상시 실행**되는 실험장치 관리 서버. Jetson 수집 서비스의 상태를
감시하고, 촬영을 시작/중지하고, 실험 설정과 조작·오류 이력을 남긴다.

> **가장 중요한 성질: Jetson이 꺼져 있어도 이 서버와 관리 화면은 정상 동작한다.**
> (인계 플랜 `docs/handover-plan-2026-09-11.md` §2·§4, 2단계 완료 기준)

| 부분 | 구현 |
|---|---|
| 관리 서버 | Python 3.13 + FastAPI (`app/`) |
| 조작 화면 | 정적 HTML·CSS·JS (`app/static/`) — **빌드 단계 없음** |
| 설정·기록 | SQLite (`data/pi-server.db`) |
| 자동 실행 | systemd (`systemd/soup-pi-server.service`) |

---

## 빠른 시작 (모의 모드)

하드웨어 없이 바로 돈다. 모의 Jetson이 프로세스 안에서 함께 뜬다.

```bash
cd ~/customfood-soup-automation/pi-server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
SOUP_JETSON_MODE=mock SOUP_POWER_MODE=mock .venv/bin/python -m app.main
# 브라우저: http://localhost:8100   (API 문서: /docs)
```

화면 하단 **"모의 장치 조작"** 카드에서 모의 Jetson을 끄고/켜고 네트워크를 끊어 보면
상태 전이(`정상` → `무응답` → `부팅 중` → `정상`)와 이벤트 기록을 확인할 수 있다.

테스트:

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

---

## 상태 판정 — 무엇을 근거로 말하는가

Pi가 프로브로 **알 수 있는 사실**은 두 가지뿐이다.

1. 수집 서비스 API(`GET /api/v1/status`)가 응답하는가
2. 호스트 TCP 포트(기본 22/SSH)가 열려 있는가 — OS 생존 여부

이 둘의 조합으로만 판정하고, **추정을 사실처럼 표시하지 않는다.**

| 표시 | 조건 | 뜻 |
|---|---|---|
| `online` | API 응답 | 정상 |
| `booting` | 호스트 O · API X · 호스트가 막 올라옴 | 부팅 중(수집 서비스 기동 대기) |
| `service_down` | 호스트 O · API X (부팅 유예 경과) | OS는 살아 있고 수집 서비스만 죽음 → 서비스 재시작 대상 |
| `link_lost` | 호스트 X · API X | 무응답. **전원 OFF인지 네트워크 단절인지 구분 불가** |
| `powered_off` | 위 + **전원 제어기가 OFF 보고** | 전원 꺼짐(근거 있음) |

- 전원 제어 회로가 없으면 `powered_off`는 절대 나오지 않는다 — 근거가 없기 때문이다.
- 마지막 정상 응답이 `SOUP_STALE_AFTER`초보다 오래되면 화면이 **"갱신 중단"**을 표시한다.
  낡은 값을 현재값처럼 보여주지 않는다.
- **통신 단절만으로 서버가 하는 조치는 없다.** 기록하고 표시할 뿐, 전원을 끄거나 세션을
  지우지 않는다(플랜 §4·§5).

## 촬영 세션

- Pi가 `session_id`(`sess-20260911T143000Z-a1b2`)를 만들어 Jetson에 내려보낸다.
  Jetson의 원본 저장 경로와 Pi의 실험 메타데이터는 이 키로 이어진다.
- **중복 시작 방지** — 활성 세션이 있으면 409.
- **종료 재시도 처리** — 이미 멈춘 세션에 중지를 다시 보내도 200(멱등).
- **Jetson 무응답 중 중지 요청** — 세션을 `stopping`에 두고 503. 연결이 돌아오면
  자동으로 사실관계를 맞춘다(세션을 임의 종료 처리하지 않는다).
- **재접속 시 재동기화** — 정상 응답을 받을 때마다 Pi 기록과 Jetson 보고를 대조한다.
  진행 중 촬영의 사실관계는 **Jetson이 주인**이다(원본을 쓰는 쪽이므로).

| 상황 | 처리 | 이벤트 코드 |
|---|---|---|
| 양쪽 같은 세션 진행 중 | Jetson 상태로 동기화 | — |
| 서로 다른 세션 진행 중 | Pi 쪽을 `unknown`으로 닫고 Jetson 쪽 채택 | `session.mismatch` |
| Pi만 진행 중으로 앎 | 세션을 `unknown`(확인 불가)으로 표시 | `session.orphaned` |
| Jetson만 진행 중 | Pi 기록으로 인계 | `session.adopted` |
| Jetson이 해당 세션 종료 보고 | 종료로 확정 | `capture.stop_confirmed` |

## 전원 제어

기본값은 **미지원**이다. 회로가 없는데 버튼이 동작하는 것처럼 보이면 안 되므로
(플랜 §5), `SOUP_POWER_MODE=unsupported`에서는 조작 요청이 **501**로 거절되고 화면에도
사유가 표시된다. `mock`으로 두면 모의 Jetson에만 작용하며 응답·화면 모두
`simulated: true`로 표시된다.

실제 GPIO 구현은 캐리어 보드 전원 버튼 배선이 확정된 뒤(플랜 6단계)
`app/power.py`의 `create_power_controller()`에 추가한다. 인터페이스는 이미 고정돼 있다.

---

## 설정 (환경변수)

전체 목록과 기본값은 `systemd/soup-pi-server.env` 참고. 자주 쓰는 것:

| 변수 | 기본 | 설명 |
|---|---|---|
| `SOUP_JETSON_MODE` | `mock` | `mock` / `http` — **Jetson 연동 교체 지점** |
| `SOUP_JETSON_URL` | `http://192.168.0.51:8000` | 실제 Jetson 수집 서비스 주소 |
| `SOUP_JETSON_PROBE_PORT` | `22` | 호스트 생존 확인용 TCP 포트 |
| `SOUP_POWER_MODE` | `unsupported` | `unsupported` / `mock` |
| `SOUP_PROBE_INTERVAL` | `2` | 감시 주기(초) |
| `SOUP_STALE_AFTER` | `8` | 이 시간 넘으면 "갱신 중단" 표시(초) |
| `SOUP_LINK_LOST_CONFIRM` | `20` | 무응답 확정까지 대기(초) |
| `SOUP_PORT` | `8100` | 서버 포트 |
| `SOUP_DB_PATH` | `data/pi-server.db` | SQLite 경로 |

## 상시 실행 (systemd)

```bash
sudo cp systemd/soup-pi-server.service /etc/systemd/system/
sudo cp systemd/soup-pi-server.env /etc/default/soup-pi-server   # 값 수정 후
sudo systemctl daemon-reload
sudo systemctl enable --now soup-pi-server
systemctl status soup-pi-server --no-pager
journalctl -u soup-pi-server -f
```

포트 정리: 관리 서버 **8100**, 조리 대시보드 3000, Mosquitto 1883/9001
(`docs/PI5_SETUP.md`).

## API

`/docs`(Swagger)에 전부 있다. 요약:

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/health` | 관리 서버 생존(Jetson과 무관하게 항상 200) |
| GET | `/api/status` | 종합 상태(화면이 1.5초마다 읽는 것) |
| POST | `/api/status/refresh` | 즉시 프로브 후 상태 반환 |
| POST | `/api/capture/start` · `/stop` | 촬영 시작·중지 |
| GET | `/api/sessions` · `/api/sessions/{id}` | 세션 이력 |
| GET | `/api/events` | 오류·조작 이력 |
| GET·PUT | `/api/config` | 실험 설정 |
| GET | `/api/power`, POST `/api/power/{on,shutdown,force-off}` | 전원(미지원 시 501) |
| POST | `/api/mock/jetson/{power,link}` | 모의 장치 조작(모의 모드에서만 등록) |

Jetson 쪽이 구현해야 할 API 계약은 **`docs/pi-jetson-api.md`** 에 있다.

## 구조

```
app/
  main.py        조립(FastAPI 앱 생성, lifespan에서 감시 시작)
  config.py      환경변수 설정
  models.py      스키마 (Pi 계약의 단일 출처)
  db.py          SQLite (세션·이벤트·설정)
  monitor.py     상태 감시 루프와 판정 규칙
  capture.py     촬영 세션 제어·재동기화
  power.py       전원 제어 어댑터 (기본 미지원)
  routes.py      HTTP API
  jetson/        Jetson 연동 어댑터 (mock / http)
  static/        관리 화면 (빌드 없음)
tests/           API 테스트 14개
```
