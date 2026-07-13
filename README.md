# 커스텀푸드 국/탕 조리 자동화 (Custom Food Soup Automation)

단국대학교 커스텀푸드 조리 자동화 과제. 국·탕 조리 과정을 자동화하고, 실시간으로
조리 상태를 모니터링하는 대시보드를 구축한다.

## 시스템 구성

```
                      ┌───────────────────────┐
                      │   기가비트 스위치      │  (인터넷·NTP 필요시 공유기로 대체)
                      └──┬─────────┬────────┬──┘
          랜선1개 ┌──────┘         │        └──────┐ 랜선1개
   ┌────────────────┐    ┌────────────────┐    ┌──────────────┐
   │ Jetson Orin    │    │ Raspberry Pi 5 │    │ 로봇 (외주)   │
   │ 센서+AI · 발행  │    │ Mosquitto+대시 │    │ 완료신호 구독 │
   └────────────────┘    └────────────────┘    └──────────────┘
   ① MQTT: 단계·중심온도·신뢰도 · 열화상 32×24(숫자배열→히트맵)
   ② MJPEG(HTTP): 솥 RGB 영상 (Jetson → 대시보드 <img>, 저fps)
```

- **기기 3개, 스위치 중심** — Jetson 랜포트가 1개뿐이라 직결 불가 → 셋(Jetson·Pi·로봇)을 **기가비트 스위치**에 각 1개씩 연결(총 케이블 3개). 인터넷/NTP 시간동기가 필요하면 스위치 대신 공유기.
- **Jetson Orin Nano** — 비전 기반 조리 완료(doneness) 인식 AI 추론 담당. 결과를 MQTT로 **발행**.
- **Raspberry Pi 5** — Mosquitto 브로커 호스팅 + React 대시보드로 데이터 **소비/시각화**.
- **로봇(외주)** — MQTT로 완료 신호 **구독** 후 동작. 담당사엔 브로커 IP + 토픽 + `shared/schema.json`만 전달(내부 구현은 그쪽).
- **통신 ① (데이터)** — MQTT. 브라우저는 MQTT-over-WebSocket으로 브로커에 직접 구독 → 별도 백엔드 불필요. 열화상(32×24)도 영상이 아니라 숫자배열로 MQTT 전송 후 대시보드에서 히트맵 렌더.
- **통신 ② (영상)** — 솥 RGB 화면은 MQTT가 아니라 **MJPEG HTTP 스트림**(저fps). 조리는 느려 저fps로 충분, RTSP/WebRTC는 오버킬.
- ⏱ **시간동기 주의** — 타임스탬프 정합이 중요한데 스위치만 쓰면 인터넷이 없어 NTP 불가 → 공유기로 NTP를 쓰거나 로컬 시간서버를 둔다.

## 저장소 구조

```
customfood-soup-automation/
├── README.md              # 이 문서
├── CLAUDE.md              # 에이전트/기여자용 작업 규칙 (필독)
├── docs/
│   └── data-schema.md     # 공유 데이터 계약 (Jetson ↔ 대시보드)
├── shared/
│   └── schema.json        # 데이터 계약 예시 페이로드 (기계용)
├── notes/                 # 과제 관리: 개발 노트 · 의사결정 · 수집 데이터
│   ├── dev-log.md
│   ├── decisions.md
│   └── data/
└── dashboard/             # React + Vite 대시보드
```

## 현재 진행 단계

**1단계 (진행 중): 대시보드 프론트엔드 우선 구축.**
하드웨어/AI가 준비되기 전, 목(mock) 데이터로 대시보드를 먼저 구동하여 "무엇을 보여줄지"를
확정하고, 이를 통해 Jetson이 무엇을 추론·전송해야 하는지 스펙을 역으로 정한다.

데이터 소스는 추상화되어 있어(`dashboard/src/data/useCookingData.js`), 나중에
`mock → MQTT` 전환이 UI 수정 없이 가능하다.

## 대시보드 실행법

> 이 머신은 Node/git이 사용자 영역(micromamba)에 설치되어 있다. 먼저 PATH 등록:
> ```
> export PATH="$HOME/.local/mamba/envs/dev/bin:$PATH"
> ```

```bash
cd dashboard
npm install      # 최초 1회
npm run dev      # 개발 서버 (http://localhost:5173)
npm run build    # 배포 빌드 (Pi 배포용)
```

## 로드맵

- [x] 저장소 뼈대 + 공유 데이터 계약 정의
- [x] React+Vite 대시보드 (목 데이터 구동)
- [ ] Pi에 Mosquitto 설치 + WebSocket 리스너(9001)
- [ ] `useCookingData.js`를 mqtt.js 구독으로 교체
- [ ] Jetson 발행자 스크립트 (Python paho-mqtt)
- [ ] AI 비전 모델(끓음 상태 분류) 연동
