# 커스텀푸드 국/탕 조리 자동화 (Custom Food Soup Automation)

단국대학교 커스텀푸드 조리 자동화 과제. 국·탕 조리 과정을 자동화하고, 실시간으로
조리 상태를 모니터링하는 대시보드를 구축한다.

## 시스템 구성

```
 ┌────────────────────┐         MQTT          ┌────────────────────────┐
 │  Jetson Orin Nano  │  ── cooking/status ──▶ │   Raspberry Pi 5       │
 │  (AI 추론 / 발행)   │  ── cooking/alerts ──▶ │   Mosquitto 브로커     │
 │                    │                        │   + 대시보드(브라우저) │
 └────────────────────┘                        └────────────────────────┘
        발행자(publisher)                            구독자(subscriber)
```

- **Jetson Orin Nano** — 비전 기반 조리 상태 인식(끓음 정도, 넘침 위험 등) AI 추론 담당. 결과를 MQTT로 **발행**.
- **Raspberry Pi 5** — Mosquitto 브로커 호스팅 + React 대시보드로 데이터 **소비/시각화**.
- **통신** — MQTT (브라우저는 MQTT-over-WebSocket으로 브로커에 직접 구독 → 별도 백엔드 불필요).

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
