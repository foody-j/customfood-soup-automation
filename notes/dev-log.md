# 개발 노트 (Dev Log)

> 의미 있는 작업을 할 때마다 **최신 항목을 위에** 추가한다. 형식: `## YYYY-MM-DD — 제목`

## 2026-09-18 — Gemini 2 Jetson 수집 경로 및 실행 지시서

- Jetson 수집 서비스에 Orbbec Python SDK v2 기반 `cam_depth_0` 어댑터를 추가했다.
  color·depth·IR 프레임을 읽고 장치 시각·순번, 실제 적용 프로필·모델·시리얼·펌웨어,
  깊이 scale을 기록한다. SDK/장치가 없으면 `connected:false`로 원인을 보고한다.
- 색상은 JPEG(선택 시 BGR raw), 깊이는 mm `uint16`, IR은 강도 `uint16`으로 기존
  세션 저장 구조에 기록한다. 배열의 실제 dtype·shape를 manifest에 반영한다.
  활성 세션의 받은 샘플로만 선택적 저속 JPEG 미리보기를 제공한다(D-023).
- `docs/gemini2-jetson-setup.md`에 Jetson SDK·udev 설치, USB 점검, 서비스 설정,
  세션 시작/중지, 원본 재열기 및 실기기 합격 기준을 정리했다.
- 가짜 SDK를 사용한 어댑터·API·저장·미리보기 테스트 9건 통과(Windows 개발 머신에서
  Linux 전용 V4L2 모듈만 대체해 실행). 기존 전체 collector 테스트는 Windows의
  `fcntl`/aarch64 구조체·`os.getloadavg`·UTF-8 환경 차이로 통과하지 못했다.
  실제 Jetson USB 카메라 촬영·FPS·연속 운전은 아직 검증하지 않았다.

## 2026-09-18 — Jetson 센서 5대 개발·검증 지침서

- `docs/jetson-five-sensor-guide.md` 작성. MLX90640 2대, MLX90614 2대, MAX31865/PT100
  1대를 센서별 단독 진단 → 동일 주소 2대 검증 → 1.5 m 배선 실측 → 기존
  `jetson/collector` 서비스의 실물 어댑터로 통합하는 순서로 정리했다.
  `docs/JETSON_SETUP.md`에서 지침서로 연결했다.
- 기존 2버스 배선안에는 원격 I²C 4심 케이블이 2가닥 필요하다. 4심 1가닥 대안은
  100 kHz 공유 버스가 되며 열화상 2대 프레임 속도를 실측해야 한다고 명시했다.
- 현재 두 Python 파일은 Raspberry Pi 단일 센서 데모이며, Jetson의 실제 버스 번호와
  mux 채널을 확인한 뒤 통합해야 한다. 이 작업은 문서화만 했고 Jetson 실기기 시험은 미수행.
- Adafruit MAX31865 Python 드라이버를 선택한 경우 하드웨어 SPI CS0(물리 24번)에
  센서 CS를 연결하지 않고 별도 GPIO를 쓰도록 배선안도 수정해
  `docs/jetson-sensor-wiring.md`로 저장소에 넣었다.

## 2026-09-12 — Pi 실험 관리·로그 기능 구현 (기록 스키마 v2)

실험 정보·조작 이력·사건 기록·상태 이력·Pi 운영 기록·내보내기를 한 번에 얹었다.
기존 세션/이벤트 구조를 재사용하고 **열 추가만으로** 확장(D-016). 결정 3건 기록
(**D-016** 스키마·보존, **D-017** 사건 모델·시각 3종, **D-018** 저장 완료 확정 근거·출처).

- **실험 정보** — 이름·재료·조건·메모 + **설정 스냅샷**(시작 시점 박제, 이후 전역 설정이
  바뀌어도 과거 기록 불변) + `project_id`/`device_id`/`schema_version` 공통 식별자.
  `PUT /api/identity`로 과제·장치 구분. **이미 시작된 실험의 소속은 바뀌지 않는다.**
- **조작 이력** — 모든 명령에 `request_id` 발급(응답 헤더 `X-Request-Id`), 요청·확인·거절
  사건을 그 키로 묶음. **보낸 시각(`sent_at`)과 Jetson 확인 시각(`confirmed_at`)을 분리**,
  지연은 monotonic으로 측정. 설정 변경은 전후 값 기록.
- **사건 기록 UI** — 재료 투입/가열 변경/교반/메모 버튼. `origin=manual`로 표시하고,
  사후 입력이면 **발생 시각(`occurred_at`)과 입력 시각(`ts`)을 분리** 저장.
- **상태 이력** — 센서별 수집 통계·저장 결과 요약을 Jetson 보고에서 받아 표시
  (계약 §2.1 확장 필드 신설, 현재는 모의만 채움).
- **Pi 운영 기록** — `boot_id`, CPU·메모리·온도·디스크 주기 측정(기본 10초, 설정 가능).
  외부 의존성 없이 `/proc`·`/sys`만 읽는다. **못 읽은 값은 0이 아니라 null(미확인)**,
  디스크·온도 임계값은 넘나드는 순간에만 사건으로 기록. 재시작 시 미완결 세션을
  `session.reopened_after_restart`로 남김(임의로 닫지 않음).
- **조회·내보내기** — 실험 1건 번들(`/api/sessions/{id}/export`: 실험정보+설정스냅샷+
  사건+저장요약, JSON/JSONL/CSV), 이벤트 CSV/JSONL, **DB 온라인 백업**(`/api/backup`,
  실행 중 파일 복사 금지 — `sqlite3.backup()` 사용).
- **호환성** — 기존 API 응답·CSV 열은 그대로 두고 **뒤에 추가만** 했다(CSV 열 이름을
  바꿨다가 기존 테스트가 깨져 되돌림). 새 필드는 전부 선택적.
- **버그 수정**: `Settings`를 직접 구성하면 `device_id`가 빈 문자열로 기록에 박히던 문제
  → `Identity`에서 호스트명으로 폴백.
- **검증**: pytest **30개**(14→30) 전부 통과. 실기기 확인 — v1 DB → v2 마이그레이션
  (데이터 보존·새 열 NULL 추가·보존 정책이 실험 기록 미삭제), 실험 시작→사건 3건→중지→
  재동기화로 저장 결과 확정(파일 155개·28MB·ok=true), request_id 추적(지연 0.9ms),
  번들 내보내기(사건 8건 누락 없음), DB 백업 열기, Pi 지표 실측(CPU 6%·63.9℃).
  **화면은 Pi 크로미움에서 전체 스크린샷으로 확인** — 사후 입력 배지·센서 통계·
  Pi 운영 상태·실험 이력 표 정상. 이모지 버튼이 네모로 깨져(Pi에 이모지 폰트 없음)
  텍스트로 교체, 사건 목록이 최근 이벤트에 밀려 사라지던 것도 전용 조회로 수정.
- **Jetson에 요청할 것**: `sensors[].stats`, `last_session_summary` (계약 §2.1,
  지시서 §3.5). 특히 후자가 없으면 저장 완료가 영원히 "미확인"으로 남는다.
- **화면 실촬영 증거**를 저장소에 보관: `docs/img/pi-server-ui-20260912.png`(상단),
  `docs/img/pi-server-ui-full-20260912.png`(전체). /tmp에만 두면 재부팅 시 사라지고
  개발 PC에서도 못 본다.

## 2026-09-12 — 로그 판독성 점검: 실제 출력을 보고 2건 수정

"사람도 AI도 알아볼 수 있어야 한다"는 요구로 **실제 로그 출력을 찍어놓고 점검**했다.
문서상 잘 돼 있어도 출력을 보면 안 되는 것이 있었다.

- **문제 1 — 서비스 로그에 사건이 없었다.** 상태 전이·촬영 시작 실패는 전부 SQLite에만
  기록되고 파이썬 로거로는 안 나갔다. 즉 `journalctl -u soup-pi-server`만 보면
  `error` 등급 사건이 일어나도 **아무 일 없는 것처럼 보였다.**
  → `db.log_event()`가 모든 이벤트를 `app.events` 로거로도 미러링하도록 수정.
    등급 매핑(info/warn/error → INFO/WARNING/ERROR)도 함께.
- **문제 2 — 시간대가 두 갈래였다.** 서비스 로그는 `12:39:13`(KST, 표기 없음),
  이벤트 DB는 `03:39:13.301Z`(UTC). 같은 순간인데 9시간 차이로 보이고 어느 쪽이
  무슨 시간대인지 로그에 안 적혀 있었다. → 로그 포맷에 UTC 오프셋 추가
  (`%Y-%m-%d %H:%M:%S%z` → `2026-09-12 12:40:39+0900`).
- **두 기록면을 잇는 키 추가** — 로그 줄 맨 앞에 DB 행 번호를 붙였다:
  `[#5 capture.start_failed] ... (session=sess-...)`. journal에서 본 줄을 DB에서
  정확히 다시 찾을 수 있고 반대도 된다.
- **이벤트 코드 사전** 작성(`pi-server/README.md`) — 전체 코드 20종의 등급·의미.
  반년 뒤 `session.orphaned`를 봐도 뜻을 찾을 수 있게. "로그 한 줄 읽는 법"과
  시각 표기 3종(로그=로컬+오프셋 / DB·API=UTC / 화면=로컬) 규칙도 함께 문서화.
- 회귀 방지 테스트 추가(`test_events_also_land_in_service_log`) — 이벤트가 양쪽에
  남는지, `#id`가 붙는지, 시각에 오프셋이 있는지 검사. 테스트 17 → 18개 전부 통과.

## 2026-09-11 — 로그 관리 설계 보강 (Pi 관리 서버)

- 사용자 지적(이전 **울산 프로젝트**에서 로그 저장·관리를 안 해봐 고생)에 따라 로그를
  "남기기"가 아니라 **보존·회전·조회·내보내기까지** 설계로 끌어올림.
- **실제 버그 발견·수정**: 로깅 설정이 `main()` 안에만 있어 systemd 배포 경로
  (`uvicorn app.main:app`)에서는 실행되지 않았음 → **배포 환경에서만 앱 로그가 사라지는**
  상태였다(개발 실행에선 정상으로 보임). `app/logging_setup.py` 신설 후 `create_app()`에서
  호출하도록 이동. 회귀 방지 테스트 추가(`test_logging_is_configured_by_create_app`).
- uvicorn 접속 로그가 별도 포맷·별도 목적지로 갈라지던 것도 합침
  (`log_config=None` + uvicorn 로거 `propagate=True`). 개발·배포 두 경로에서 실측 확인.
- 로그 3종 구분을 문서화: **서비스 로그**(journald+회전 파일) / **운영 이력**(SQLite events)
  / **수집 원본·매니페스트**(Jetson 로컬). 각각 보존·조회 방법을 표로 정리.
- 이벤트 보존을 **건수(5000) + 기간(90일) 병행**으로 변경. 건수만이면 조용한 기간에 몇 년 전
  기록이 남고, 기간만이면 장애 폭주 시 디스크가 부푼다. 기동 시 1회 + 주기적으로 적용.
- **이벤트 내보내기 API 추가** (`/api/events/export?format=csv|jsonl&days=N`) — 시간 오름차순,
  Excel용 BOM. 보존 기간이 지나면 사라지므로 과제 보고서 근거는 내보내서 보관해야 함.
  관리 화면 이벤트 카드에 CSV/JSONL 링크 노출.
- `docs/jetson-collector-brief.md`에 §5.5 로깅 절 추가 — Jetson 수집 서비스도 같은 원칙으로
  만들도록 지시(특히 배포 경로 로깅 함정, 프레임마다 로그 찍지 말 것, 회전 필수).
## 2026-09-12 — Jetson 수집 서비스 1차 구현 (플랜 3단계, Jetson에서 작업)

- **`jetson/collector/` 신설** — Python 3.10 + FastAPI, `~/collector-venv`(--system-site-packages로 JetPack cv2 사용).
  계약(`docs/pi-jetson-api.md`)대로 status/start/stop/shutdown + 확장(`sessions`, `capture/config`).
  결정 2건: **D-020** V4L2 ioctl 직접 호출, **D-021** 저장 레이아웃.
- **구성**: `session.py`(상태기계·센서별 수집 스레드·1초 통계·디스크 감시) · `storage.py`(스트림 기록기·
  유한 대기열·index.jsonl 버퍼 flush·manifest·복구·백그라운드 체크섬) · `sensors/`(mock 5종 / v4l2 ISX031F /
  unsupported Gemini 2·MLX90640·MLX90614) · `sysmon.py`(5초 시스템 상태) · `service.py`(세션 소유·probe 캐시·
  정상 종료 순서) · systemd 유닛.
- **실기기에서 확인한 것(문서와 달랐던 점)**:
  - OpenCV V4L2 백엔드는 링크 없는 `/dev/video4`에서 `ok=True`·전부 0 프레임·`POS_MSEC`=0·노출/게인 −1을
    돌려줌 → 신뢰 불가. 직접 ioctl로 바꾸고 C 헤더로 구조체 크기 검증(v4l2_buffer 88B, ts@24, seq@56).
  - 직접 DQBUF 결과(링크 없음): 2초 타임아웃 사이에 sequence=0·timestamp=0·전부 0 프레임이 간헐 출력
    → `blank_frame`으로 `valid=false`. `notes/data/experiments/20260912_isx031f_v4l2_link-down_check.md`.
  - `/sys/devices/virtual/thermal/thermal_zone{2,3,4}`(cv*)는 EAGAIN → 온도 항목 null 처리.
  - systemd-timesyncd는 NTP 오프셋을 노출하지 않음 → `clock.ntp_offset_ms: null`, 지터·루트분산만 기록.
  - Orbbec USB 없음·pyorbbecsdk 없음, MLX I2C 응답 없음 → 어댑터를 만들지 않고 `connected:false + reason`.
- **테스트 21개 통과**(`pytest -q`, 모의 센서·임시 경로): Pi 계약 모델(`pi-server/app/models.py`)로 응답 교차
  검증, 중복 시작 200 거절·같은 ID 멱등, stop 저장 완료 후 응답·멱등, 종료 중 status, 센서 분리/재연결,
  전 센서 open 실패, 쓰기 실패→failed, 디스크 부족 시작 거절·진행 중 안전 종료, 대기열 초과 계수+인덱스 기록,
  프로세스 재시작 후 미완료 세션 `failed(interrupted)`·partial, shutdown 순서, 설정 변경 전후 기록, depth 3스트림 분리.
- **Pi 왕복(실제 Pi 클라이언트 코드, Jetson 로컬 8101에 HTTP 모드로 기동)**: 시작→세션 디렉터리 생성 → 중복 시작
  Pi 409 → 중지 0.07s·manifest stopped → 서비스만 SIGINT → Pi `service_down`("OS는 살아 있고 수집 서비스만 무응답")
  → 재기동 후 Jetson 단독 세션 → Pi `session.adopted`로 인계 → Pi에서 중지 성공. **실기 Pi(10.42.0.1:8100)는
  이 시점에 무응답이라 실기 왕복·랜선 뽑기 시험은 미실시**(SSH 키 없음 — Pi 설정은 사람이 해야 함).
  - 관찰: Pi 이벤트에 `session.orphaned`→`session.adopted`가 시작 직후 한 번 찍힘. 시작 명령 **직전에 받은
    낡은 status 보고**를 시작 후 재동기화에 쓰는 Pi 쪽 경합(`monitor.probe_once` → `reconcile`). Jetson 문제 아님.
- **(같은 날 후속) Pi 계약 확장 반영** — 리베이스 중 Pi 쪽 커밋(기록 스키마 v2)이 `sensors[].stats`·
  `last_session_summary`(§2.1)·로그 관리(§5.5)를 요구한 것을 확인하고 구현: 센서 단위 누적 통계(스트림 합산,
  세션 없으면 null), 저장 결과 요약은 **파일 close·manifest 기록 후** `_on_session_finished`에서 확정(`ok`, 실패 시
  `note`에 사유), `logging_setup.py`(create_app에서 설정·uvicorn 합침·`COLLECTOR_LOG_*` 회전 파일·N초 요약 로그).
  갱신된 Pi 서버 코드로 재왕복: Pi 이벤트에 `capture.save_confirmed`(occurred_at=closed_at)·세션 `jetson_summary.ok=true`
  확인. 이전에 봤던 `session.orphaned` 경합은 v2 Pi 코드에서는 재현되지 않았다. 결정 번호는 Pi가 먼저 쓴
  D-016~018을 피해 **D-019(마이크)·D-020(V4L2)·D-021(저장 레이아웃)**으로 재부여. 테스트 23개 통과.
- **미실시/남은 것**: ISX031F 실측(FPS·CPU·JPEG·기록 속도)은 카메라 보드 전원 연결 후 `tools/v4l2_check.py --jpeg`,
  Gemini 2·MLX 어댑터는 실물 확보 후, Pi 화면에 확장 필드(스트림 통계·recovered) 표시, 위 Pi 경합 수정.

## 2026-09-11 — 음향(소리) 센서 방법론 조사 + Jetson 실기기 상태 확인 (플랜 3단계 착수 전)

- **음향 조사** → `docs/research-methodology-4-acoustic.md` (소스 16건). 질문 "소리 센서 헤비한가, FFT로 되나".
  결론: 하드웨어·연산은 가볍다(USB 마이크 + 16kHz WAV ≈ 2MB/분). **FFT 밴드 에너지+미분 규칙으로
  simmer/rolling boil 구분은 상용 특허(GE 1999~)로 20년 검증**됐으나 **doneness(완료/과조리)는 소리로
  못 얻는다.** 국/탕·급식 주방 특유 문제 = 복수 솥 소스 분리·후드 소음(공기 마이크 감쇠·잡음은 2025
  논문이 명시). 권고: 수집 단계에 USB 마이크 원시 WAV 저장만 추가하고 모델 사용은 8단계에서 판단.
  I2S MEMS는 JetPack 6 DTB 병합 필요 + 카메라 DTB 오버레이와 충돌 위험이라 배제.
  → 사용자 결정 **D-019: 마이크 미채택**(doneness 정보 없음 + 복수 솥/후드 소음 리스크).
- **Jetson 실기기 확인(지시서 1절)** — 코드 작성 전 보고 완료:
  - `/dev/video4`(ISX031F) 노드·드라이버 있음, UYVY/NV16 640x514~3840x2160 모두 30fps 고정.
    **그러나 캡처 실패**: `fzcam_cfg` → `Link satus:0-0-0-0`, i2c 버스 9·10 모두 0x29(디시리얼라이저)
    없음 → **FG12-4CH 보드 전원/케이블 미연결 상태**로 판단(부팅 로그도 동일). 7-27 검증 당시와 다름.
  - Orbbec Gemini 2 USB 없음, pyorbbecsdk 없음. MLX90640/90614 I2C 응답 없음(버스 0·1·2·7·9·10).
  - NVMe 410GB 여유, Python 3.10.12, cv2 4.8.0(시스템), FastAPI 미설치, `~/collector-venv` 없음.
  - 유선 10.42.0.52, Pi(10.42.0.1) ping 0.28ms, Pi 8100 응답. NTP 동기화됨.
  - 결론: 실물 센서가 하나도 안 붙은 상태. 3단계는 모의 센서로 API·저장·Pi 왕복·장애 시험까지
    진행 가능하고, V4L2 어댑터 실측은 카메라 보드 전원 연결 후로.

## 2026-09-11 — Pi 관리 서버 1차 구현 (플랜 2단계, Pi에서 작업)

- **`pi-server/` 신설** — Python 3.13 + FastAPI. 인계 플랜 2단계("Pi 관리 서비스 ↔ Jetson
  수집 서비스 기본 통신") 완료 기준인 **"Jetson OFF/미연결 상태에서도 관리 화면 사용,
  상태 조회·명령 왕복"** 을 목표로 함. 결정 3건 기록(**D-013** 스택·화면 방식,
  **D-014** 제어 채널 HTTP 분리, **D-015** 전원 제어 기본 미지원).
- **구성**: `monitor.py`(감시 루프·판정) · `capture.py`(세션 제어·재동기화) · `db.py`(SQLite)
  · `power.py`(전원 어댑터) · `jetson/`(mock ↔ http 어댑터) · `static/`(무빌드 관리 화면)
  · `systemd/`(부팅 자동 실행). 대시보드의 `useCookingData.js` mock↔mqtt 교체 패턴을
  그대로 가져와 **교체 지점을 `create_jetson_client()` 한 곳**으로 모음.
- **상태 판정에서 관측과 추정을 분리** — 프로브로 아는 건 ①수집 서비스 API 응답 여부
  ②호스트 TCP(기본 22) 응답 여부뿐. 이 둘로 `online / booting / service_down / link_lost`를
  구분하고, **"전원 꺼짐"은 전원 제어기가 OFF를 보고할 때만** 표시. 프로브 실패는 전원 OFF와
  네트워크 단절을 구분할 수 없으므로 원인 미상으로만 말한다. 낡은 값은 `stale`로 표시.
- **세션 재동기화** — Jetson이 촬영의 사실관계 주인(원본을 쓰는 쪽). 재접속 시 대조해
  `session.mismatch`/`orphaned`/`adopted`/`stop_confirmed` 이벤트로 남김. 통신 단절만으로
  세션을 임의 정리하거나 전원을 끄지 않음(플랜 §4·§5).
- **모의 Jetson**이 부팅 지연(호스트 먼저 → API 나중)·정상 종료 순서·네트워크 단절을
  재현 → 실물 없이 상태 전이를 실제로 만들어 확인 가능. 모든 응답에 `mock`/`simulated` 표시로
  "실제 연동 완료"로 오인되지 않게 함.
- **검증**: pytest 14개 전부 통과 + 실기동(uvicorn) 확인 —
  전원 OFF 상태 화면 정상 표시, OFF 중 촬영 시작 요청 503+이벤트 기록,
  전원 ON 후 `무응답(3s) → 부팅 중(1s) → 정상` 전이 관측, 촬영 시작→중복 409→중지→재시도 200 왕복,
  서버 재시작 후 이벤트 이력 유지. **관리 화면의 브라우저 렌더는 미확인**(Pi에서 헤드리스
  Chromium이 응답 없음 — 모니터 직결 브라우저로 눈으로 확인 필요).
- **문서**: `docs/pi-jetson-api.md`(Jetson이 구현할 계약) 신규, `docs/PI5_SETUP.md`에 5절
  관리 서버 설치·상시 실행 추가(이후 절 번호 +1, 키오스크는 관리 화면 기준으로 갱신),
  README 시스템 구성·로드맵 갱신.
- **다음**: Jetson 수집 서비스 구현 → `SOUP_JETSON_MODE=http` 전환(계약 그대로 붙음),
  미리보기 API 정의(플랜 3단계), 전원 회로 확정 후 `GpioPowerController` 추가(6단계).

## 2026-09-11 — 멀티센서 실험 장치 인계 플랜 수용 + 저장소 대조 (Jetson에서 작업)

- 사용자가 타 개발 머신에서 작성한 **인계 플랜** 원문을 저장소에 보존:
  `docs/handover-plan-2026-09-11.md`. 방향 전환: 도네스 AI 고정 → **수집 기반 실험 장치 우선**
  (프로파일 프레임 + Gemini 2 + GMSL2 + 열화상/적외선 + 플래시, Pi=관리 PC, 8단계 진행).
- 플랜 1단계(기존/신규/미확정 구분) 수행 → `docs/handover-reconciliation-2026-09-11.md`:
  - **실기기 실측으로 플랜 미확정 다수 즉시 확정** — Orin Nano **8GB** Devkit(Super),
    R36.4.3, **NVMe 456GB 장착(구매 불필요)**, FG12-4CH+ISX031F GMSL2 검증 완료 상태 확인.
  - MLX90640 보유 = 플랜의 열화상 선정 조건(온도 원시 데이터) 충족.
  - **충돌 5건 식별(사용자 확정 대기)**: ①D-008 헤드리스 vs 모니터 직결 ②로봇 범위 제외
    ③D-010 아키텍처 "확정" → 8단계 후보로 지위 변경 ④Pi 역할 확장(겸임 가능)
    ⑤열화상 "미확정"의 의도(추가 구매? 보유 미인지?).
- 다음: 충돌 확정 → decisions.md D-011~ 기록 → 플랜 2단계(Pi 관리 ↔ Jetson 수집 통신 골격).
- (같은 날 후속) 사용자 확정 2건 기록: **D-011** Pi 모니터 직결(D-008 개정),
  **D-012** 열화상 = 보유 MLX90640 유지(신규 구매 없음). 잔여 미확정은 로봇 장기 목표 여부뿐.

## 2026-07-27 — 아키텍처 문서에 실물 센서 하드웨어 구성 반영

- `docs/model-architecture.md` §2.0 신설 — 확보한 실물 기준으로 입력 하드웨어 명시:
  - RGB 2뷰 = **ISX031F GMSL2 ×2** (FG12-4CH, 1대는 `/dev/video4` 검증 완료, USB 웹캠은 폴백으로 강등)
  - **MLX90640 2종**(55°/110°)·**MLX90614 2종**(5°/35°) 보유 — FOV 선택은 솥 지름·장착 높이
    실측 후 확정, 단 **데이터 수집 시작 전 확정 필수**(수집 후 교체 시 분포 틀어짐) 명시
  - FOV 2종 동시 장착은 현 설계 밖(채택 시 §3.2 개정 필요)임을 문서화
- 미결: 열 센서 FOV 선택(장착 지그·거리 확정 대기), 두 번째 ISX031F 연결(`position=Video_1100`).

## 2026-07-24 — 시스템 구성·기기 역할 확정 (decisions D-006~D-009)

- 여러 기기 역할이 붕 떠 있던 걸 정리해 `notes/decisions.md`에 4건 기록:
  - **D-006 데이터 저장**: 원시 데이터 = **Jetson 로컬 SSD(핫)** 1차 저장, 장기보관은 (옵션) 연구실 데스크탑 오프로드(콜드, Tailscale rsync). 소스에서 기록 원칙 + 단일 SSD 백업 없음 리스크 명시.
  - **D-007 기기 역할**: **시스템=Jetson+Pi+로봇(3대)**, 나머지는 보조. 로봇=외주 MQTT 클라이언트(관할 밖). **T470s=시스템 밖** — 헤드리스 Pi 뷰어(브라우저)+제어(SSH), 옵션으로 Jetson 백업통. 엘리트북=일상·개발.
  - **D-008 Pi 헤드리스**: Pi 모니터 없이 운영, 대시보드는 웹 브라우저로 접속, 관리는 SSH. (노트북은 화면 출력전용이라 Pi 물리 모니터 불가 — 근데 필요 없음.)
  - **D-009 네트워크 2평면**: 로컬 실시간=유선 스위치, 원격=Tailscale, 스위치는 공유기 업링크(인터넷/NTP/Tailscale 확보).
- **결론적으로 원래 설계(D-003 브로커=Pi) 그대로 유효** — Pi 제거 여부를 검토했으나, "현장 상설 설치는 노트북 불가, 임베디드 Pi만 가능"이라 Pi 유지로 재확정. 문서(`docs/PI5_SETUP.md`, README) 수정 불필요.
- 다음: Pi·Jetson 실기 세팅 진행.

## 2026-07-24 — Raspberry Pi 5 셋업 문서 작성

- **`docs/PI5_SETUP.md`** 작성 — Pi 5 = 런타임 노드(Mosquitto 브로커 + 대시보드 서빙).
  0)repo clone → 1)OS·고정IP → 2)Mosquitto(리스너 **1883/TCP** + **9001/websockets** 둘 다, `allow_anonymous`) →
  3)Node20·대시보드 빌드(`.env.production`: `VITE_DATA_SOURCE=mqtt`, `VITE_MQTT_URL=ws://<Pi>:9001`, `VITE_POT_MJPEG_URL`) →
  4)정적 서빙 systemd(soup-dashboard) → 5)키오스크(선택) → 6)NTP(스위치만이면 불가 → 공유기/로컬 chrony) →
  7)ufw → 8)운영 전 브로커 인증 → 9)점검 체크리스트 → 10)트러블슈팅.
- 실제 코드값 반영: 대시보드 MQTT 소스는 **MQTT-over-WebSocket(9001)**, env 기본값 `mock`/`ws://raspberrypi.local:9001`
  (`dashboard/src/data/useCookingData.js`), 영상은 `VITE_POT_MJPEG_URL`(`PotView.jsx`).
- 참고: 이 개발용 노트북엔 **자동 백업/동기화 스케줄 없음**(crontab·systemd timer·syncthing 전부 미설정 확인).
  노트북 처분 시 git 밖 데이터(gitignore된 raw, SSH키 등)는 수동 백업 필요.

## 2026-07-14 — 연구추진실적 문서 작성(1차년도 1단계)

- **`연구추진실적.md`** 작성 — 과제 양식(`연구추진실적.png`)의 두 표를 채움.
  대상 단계는 **1차년도 1단계**(AI 알고리즘 구조 설계 · MQTT–JSON 통신 설계 · Jetson AI PC·카메라(Fakra) 연동 구조 설계).
  - **가. 연구개발내용**: dev-log 실적을 4개 항목으로 정리(기관=단국대학교) — ①doneness 3단계 ordinal(CORN) + RGB·열화상·온도 fusion 모델 구조 설계·딥리서치3회·GT정의안, ②MQTT–JSON 통신·데이터계약·mock↔mqtt 추상화, ③Jetson Super 환경·카메라 연동·네트워크 구조 설계 + TensorRT 벤치 하네스, ④대시보드 스펙 역설계.
  - **나. 진도표**: 4개 개발내용 월별 `→` 표기.
- **톤 조정(과다 표기 방지)**: 초안이 진도 88~95%로 "1차년도에 다 끝낸 것"처럼 읽혀 → **설계(안)·기반 마련 수준**으로 재표현하고 항목마다 **잔여 과제**(실물 입고·검증·최종 확정) 명시. 이후 연차 여지 확보.
- **구조·문체 정리**: 1차년도 목표 3개(①AI 알고리즘 ②통신 ③Jetson·카메라)에 맞춰 정렬하고, 대시보드는 별도 항목이 아니라 **②통신의 하위 항목**(전송 데이터 스펙 역설계용)으로 편입. AI 전문용어(딥리서치·CORN·fusion·인페르노·TensorRT 등) 빼고 연구보고서 평문으로 다듬음. 진도율 하향(①50 ②60 ③45, **총 약 52%** = 계획 기간 절반 시점).
- **미기입/확인 필요**: 표의 연구개발비(천원)·기관 가중치(%)는 협약 예산서 값 필요 → 주석 처리. 진도율·월별 구간은 dev-log 기준 추정치로 협약 시작월 대비 조정 필요.
## 2026-07-24 — 모델 아키텍처 설계 문서 확정 (Jetson에서 작업)

- **`docs/model-architecture.md` 작성** — 조사 1~3차 + GT 설계 + 벤치 실측을 종합해 권고를 확정
  설계로 굳힘. 이월돼 온 "다음 할 일" 1번 완료.
- 확정 내용 요약: RGB 2뷰(EfficientNet-B0, fp16) + thermal(2D-CNN+TCN+수작업 피처) late fusion,
  온도 스칼라 gated fusion, CORN ordinal 3단계 + boil_intensity 보조 헤드. 추론 1 Hz,
  완료 알림은 EMA 스무딩+히스테리시스 후처리. 입력 텐서·캐던스·손실·증강·학습 순서까지 명시.
- 조사가 못 푼 미해결 질문 4개는 **ablation 실험 계획(A1~A5)** 으로 치환해 문서에 내장
  (열배열 기여, early vs late, 뷰 기여, 최소 앵커 수, 백본 비교).
- 의사결정 기록: **D-010** (`notes/decisions.md`).

### 다음 할 일 (다음 세션 시작점)
- [ ] **GT 정의 실험 설계 확정** — 결정 2개 대기: (1) 관능평가 기준(지도교수), (2) PoC 메뉴 1종.
- [ ] MQTT 파이프라인 미리 뚫기 — Mosquitto + 가짜 Jetson 발행자 → 대시보드 mqtt 모드 (계속 이월 중).
- [ ] 하드웨어(센서) 도착 후: 동기 로깅 스크립트 → 데이터 수집 → 아키텍처 문서 §8 ablation.
- [ ] GT 확정 후 schema 3문서 재정의 동기화.

## 2026-07-13 — Jetson 사전작업: 셋업 문서 + 엣지 벤치마크 하네스

- **환경 확정**: Jetson Orin Nano **Super**, JetPack 6.2.2 / L4T 36.5 / CUDA 12.6 · TensorRT 10.3 · cuDNN 9.3 · Python 3.10. Claude Code 설치됨. **센서는 아직 없음.**
- **`docs/JETSON_SETUP.md`** 작성 — 0)GitHub clone(SSH) → 1)버전확인 → 2)jtop → 3)전력 MAXN SUPER+jetson_clocks → 4)venv(--system-site-packages) → 5)PyTorch(jetson-ai-lab jp6/cu126) → 6)검증 → 7)trtexec → 8)트러블슈팅.
- **`jetson/bench/` 엣지 벤치마크 하네스**(센서 불필요, 랜덤텐서 → **성능만** 측정): 3차 조사가 남긴 "검증된 Jetson 지연·전력 수치 없음" 공백 메우기.
  - `models.py`: MobileNetV3-Small·EfficientNet-B0(도네스 분류) + ThermalCNN·ThermalSeqTCN(열화상 2D-CNN+1D TCN) + ONNX export.
  - `benchmark.py`: ONNX→`trtexec`(fp16/int8) 지연·FPS 파싱 + `tegrastats` 전력·온도 샘플러 → `notes/data/bench/summary.md`.
  - 접근: torch2trt 회피, **JetPack 기본 trtexec만** 사용(의존성 최소).
- 검증(dev 머신): `py_compile` 통과 + trtexec/tegrastats 파싱 정규식 샘플 단위검증 통과. (실제 실행은 Jetson에서.)

### 다음 할 일 (다음 세션 시작점)
- [ ] **Jetson에서 실행**: `git pull` → `docs/JETSON_SETUP.md`로 툴체인 → `jetson/bench/benchmark.py` → `summary.md` 확인.
- [ ] 벤치 결과로 **모델 아키텍처 확정**(백본·정밀도).
- [ ] **MQTT 발행자 스켈레톤**(우선순위 3) — 가짜 센서값으로 cooking/status 발행 → 대시보드 mqtt 모드.
- [ ] GT 정의 확정(관능 기준·PoC 메뉴).

## 2026-07-12 — 대시보드 전면 재설계 + 데이터 계약 갱신(doneness)

- **데이터 계약을 새 도네스 설계로 동기화**(3문서): `docs/data-schema.md`, `shared/schema.json`,
  `dashboard/src/data/schema.js`. 핵심 출력 `boil_state` → **`doneness`(undercooked/done/overcooked, 순서형)**.
  필드 추가: `doneness_confidence`, `center_temp_c`(구 temperature_c), `boil_intensity`,
  `thermal`(MLX90640 32×24 배열). `stage` enum 정리(ingredient_add/heating/cooking).
- **대시보드 현대적 재구축**(architecture.html과 동일 디자인 시스템: 써모그래피 램프+그래파이트, 라이트/다크 토글):
  - 신규: `DonenessHero`(순서형 3단계 히어로), `StatTiles`(중심온도·경과·끓음강도·공정),
    `ThermalHeatmap`(32×24 인페르노 캔버스+셀 호버), `PotView`(목=캔버스 솥 시뮬/실장치=MJPEG `<img>`).
  - 갱신: `TemperatureChart`(단일시리즈 area+크로스헤어, 테마색 토큰 연동), `StageTimeline`, `AlertPanel`.
  - 제거: `StatusCard`, `AiDetectionCard`(→ Hero/StatTiles로 대체).
- 색: dataviz 검증기로 도네스 3색 CVD 확인(분리도 ΔE 25+, 상태색은 항상 라벨 동반). 열화상은 지각균일 인페르노(무지개 금지 준수).
- 검증: `npm run build` 통과, mock 데이터 계약 런타임 체크 통과(thermal 768개·doneness·컬러맵), preview 서버 서빙 확인.
  ※ 브라우저 부재로 픽셀 단위 시각 확인은 미실시 — 실제로 열어볼 것.
- 실장치 전환: `VITE_DATA_SOURCE=mqtt`(useCookingData) + `VITE_POT_MJPEG_URL`(PotView)만 설정하면 UI 수정 없이 전환.


## 2026-07-12 — 방법론 자료조사 + 문제 재정의

- **문제 재정의**: "끓음/넘침 정도" → **"조리 완료(doneness) 판정"** 으로 목표 변경.
- **센서 추가 확정**: MLX90614ESF(GY-906-DCI, 단일점 비접촉 IR, FOV 5°) = 솥 중심온도,
  MLX90640(32×24 thermal array) = 표면 온도분포. RGB 2대(탑뷰 + oblique 다시점).
- **딥리서치 수행**(팬아웃 검색→소스 페치→3표 교차검증, 21소스/검증 100주장 중 반박 1건).
  결과 전문: `docs/research-methodology.md`.
- **핵심 발견**: GT(정답) 미정 문제의 직접 선례 = 튀김 도미 doneness 논문(ScienceDirect 2025).
  관능+이화학(온도·색·시간) 클러스터링으로 doneness 이산화 → DenseNet-121 90%. 색-doneness 상관 0.93.
  → 접근 A(시간·온도)를 접근 B(시각 분류)의 **약지도 라벨러**로 쓰는 상보 전략 도출.
- **권장 로드맵**: GT 정의(도미 논문 방식 이식) → 탑뷰 RGB+MLX90614 DenseNet PoC(TensorRT)
  → RGB-T 4채널 융합 검증 → 필요시 TSM/MS-TCN 단계 세그멘테이션. 어노테이션은 CVAT(시간구간·멀티모달).

- **2차 딥리서치(국/탕 특화)** 완료 → `docs/research-methodology-2-soup-specific.md`.
  색 대신 쓸 신호 확정: **끓음/거품 동역학 · 국물 탁도(우러남) · 건더기 무름(OnionBot식 milestone 분류)**.
  온도+비전 = gated cross-attention(AquaFusionNet), 소량데이터 = 전이학습+SSL+약지도.
  ⚠️ 검증된 Jetson 엣지 실시간 수치는 없음(직접 벤치 필요), 국/탕 직접 논문도 없음(도메인 외삽).
- **GT 설계안** 작성 → `docs/gt-definition-design.md` (3단계 정의, 하이브리드 GT, 도미 vs 국/탕 차이 명시).
- **구조 시각화** → `docs/architecture.html` (파이프라인·신호·라벨링·로드맵 한 장).

## 2026-07-12 — 네트워크·배치 결정 (Jetson·Pi·로봇)

- **기기 3개 확정**: Jetson(센서+AI/발행), Pi(브로커+대시보드), **로봇(외주, MQTT 구독→동작)**.
- **물리 연결 = 기가비트 스위치**. Jetson 랜포트가 1개뿐이라 직결 불가 → 셋을 스위치에 각 1개씩(총 케이블 3개, Jetson에서 나오는 건 1개). 인터넷/NTP 필요하면 스위치 대신 공유기.
- **통신 = 전부 MQTT**, 브로커는 Pi(Mosquitto). 로봇 담당사엔 브로커 IP+토픽+`shared/schema.json`만 전달.
- **데이터 경로 분리**:
  - ① MQTT: 단계·중심온도·신뢰도 + **열화상 32×24는 숫자배열로 전송 → 대시보드에서 히트맵 렌더**(영상 아님).
  - ② **MJPEG(HTTP)**: 솥 RGB 영상. 조리가 느려 저fps로 충분, Jetson 소형 HTTP 서버 → 대시보드 `<img>`. RTSP/WebRTC는 오버킬.
- ⏱ **시간동기 이슈**: 타임스탬프 정합 중요 → 스위치만이면 인터넷 없어 NTP 불가. 공유기로 NTP 쓰거나 로컬 시간서버 둘 것.
- 반영: `README.md` 시스템 구성 다이어그램, `docs/architecture.html` §02(신설).

- **3차 딥리서치(미해결 질문)** 완료 → `docs/research-methodology-3-open-questions.md`.
  MLX90640 32×24 = **스칼라 아닌 '이미지'로 직접 DL** (2D-CNN+1D TCN, <10k 파라미터, Jetson 여유).
  RGB 2뷰와 **late fusion**(비정합 이종센서). 3단계 출력은 **ordinal CORN**. 완료=시간경계(유사도 peak).
  ⚠️ 온도·시간 약지도만으론 완료/과조리 못 가름 → **소량 수작업 앵커 필요**. 열배열 실제 기여·early/late는 실측으로만.

### 다음 할 일 (다음 세션 시작점)
- [ ] **모델 아키텍처 설계 문서** (TODO) — 3차까지 종합해 구조도 확정: RGB 2뷰 branch + MLX90640 (2D-CNN+TCN) branch → late fusion → ordinal(CORN) 3단계. 온도(MLX90614) gated fusion.
- [ ] **GT 정의 실험 설계 확정** (TODO) — 결정 2개: (1) 관능평가 기준(지도교수), (2) PoC 시작 메뉴 1종.
- [ ] 하드웨어 도착 후: 동기 로깅 스크립트 → 데이터 수집 → schema 3문서 재정의.
- [ ] GT 확정 후 `docs/data-schema.md` / `shared/schema.json` / `dashboard/src/data/schema.js`를
      "끓음 상태" → "조리 완료 단계"로 재정의(3문서 동기화).
- [ ] (기존) MQTT 파이프라인 미리 뚫기 — Mosquitto + 가짜 Jetson 발행자.

## 2026-07-10 — 프로젝트 초기 셋업

- 저장소 생성 및 git 초기화 (`~/customfood-soup-automation`).
- 개발 환경 구축: 시스템에 Node/git이 없어 **micromamba로 사용자 영역에 Node 20 + git 설치**
  (env `dev`). PATH: `export PATH="$HOME/.local/mamba/envs/dev/bin:$PATH"`.
- **공유 데이터 계약 정의** — `docs/data-schema.md`, `shared/schema.json`
  (Jetson ↔ 대시보드 MQTT 페이로드, 조리 단계/끓음 상태/경고 enum).
- **React + Vite 대시보드 스캐폴딩** (`dashboard/`), recharts 추가.
- 데이터 소스 추상화(`useCookingData.js`) + 목 데이터 시뮬레이터(`mockSource.js`) 구현 →
  나중에 MQTT로 교체 시 UI 수정 불필요.
- 대시보드 컴포넌트 5종: StatusCard, TemperatureChart, StageTimeline, AiDetectionCard, AlertPanel.

- 대시보드 목 데이터 구동 확인 완료(사용자 검증).

### 다음 할 일 (다음 세션 시작점)
- [x] **GitHub 원격 연결 + push 완료** — github.com/foody-j/customfood-soup-automation (private), SSH 키(ed25519) 인증
- [ ] MQTT 파이프라인 미리 뚫기: Mosquitto 로컬 브로커 + 가짜 Jetson 발행자(Python) → 대시보드 mqtt 모드 전환 ← 다음 최우선
- [ ] 실제 레시피별 목표 온도/시간 값 채우기 (지도교수 확인)
- [ ] Jetson 발행자에 실제 AI 추론 결과 연결

## 2026-07-12 — 원격 AI 무인 작업 인프라 설정

- Discord 봇(discord-ai-orchestrator) 경유 무인 에이전트에 **코드 수정 권한** 부여:
  `.claude/settings.json` 권한 사전 승인 + 워크스페이스 trust 설정.
- 안전장치: pre-commit hook — 무인 실행(`AI_AGENT_RUNNER=1`)의 main 직접 커밋 차단.
  무인 작업은 `ai/<작업명>` 브랜치에만 커밋, push 금지, 사람이 diff 검토 후 머지.
- CLAUDE.md에 '원격 AI 작업 규칙' 절 추가. git/node/npm을 ~/.local/bin에 심볼릭 링크
  (headless PATH 문제 해결).

## 2026-07-13 — Jetson 실기기 셋업 + 엣지 벤치마크 실측 (Jetson에서 작업)

- **Jetson Orin Nano Super 소프트웨어 스택 셋업 완료** (JetPack 6.2.2 / CUDA 12.6 / TensorRT 10.3).
  venv `~/cf-venv`(--system-site-packages) + torch 2.11.0 / torchvision 0.26.0 / onnx / onnxscript.
- 셋업 중 만난 문제와 해결 (JETSON_SETUP.md 문서와 달랐던 부분):
  - `pypi.jetson-ai-lab.dev` DNS 사망 → **`.io` 도메인**(`https://pypi.jetson-ai-lab.io/jp6/cu126`)으로 설치.
  - `python3.10-venv` 미설치 → `--without-pip`로 venv 생성 후 get-pip.py 부트스트랩(sudo 불필요).
  - torch 2.11 import 시 `libcudss.so.0` 없음 → PyPI `nvidia-cudss-cu12` 설치 +
    activate 스크립트에 LD_LIBRARY_PATH(`site-packages/nvidia/cu12/lib`) 추가로 해결. `cuda True` 확인.
  - torch 2.11의 ONNX export(dynamo 기본)가 `onnxscript` 요구 → 추가 설치.
  - **이 보드의 nvpmodel 인덱스: 0=15W, 1=25W, 2=MAXN_SUPER** (문서의 "보통 0" 아님) → `-m 2` 적용.
  - efficientnet_b0 export 중 프로세스 1회 소리 없이 사망(OOM 추정) → 모델 나눠 재실행으로 해결.
- **벤치마크 실측 완료** (MAXN_SUPER + jetson_clocks, 4모델 × fp16/int8) → `notes/data/bench/summary.md`.
  핵심 수치(평균지연/FPS): mobilenet_v3_small fp16 0.86ms/1160, efficientnet_b0 fp16 2.05ms/487,
  thermal_cnn fp16 0.056ms/11688, thermal_seq_tcn fp16 0.12ms/6878. 전력 7.0~8.2W, 최대온도 ≤54.6℃.
- **결론: 어떤 조합을 골라도 실시간 목표(5~10 FPS) 대비 수십~수백 배 여유.** 도네스 백본은
  EfficientNet-B0도 충분(487 FPS)하므로 백본 선택은 속도가 아니라 **정확도 기준**으로 하면 됨.
  INT8 이득은 efficientnet에서만 유의미(2.05→1.60ms), 나머지는 fp16으로 충분.
- **정책 변경(D-005):** Jetson에서도 직접 커밋/푸시 허용(사용자 승인). "Jetson 읽기 전용" 규칙은
  히스토리 분기 방지용 컨벤션이었으나 실측 데이터 반영엔 비효율 → JETSON_SETUP.md 갱신,
  이번 결과(JSON 8개 + summary + 노트)는 Jetson에서 바로 커밋·푸시함.
