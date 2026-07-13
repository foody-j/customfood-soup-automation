# 공유 데이터 계약 (Data Contract)

Jetson Orin Nano(발행)와 대시보드·로봇(구독)이 주고받는 데이터 형식. **이 문서가 계약의
단일 출처다.** 형식을 바꾸면 이 문서, `shared/schema.json`,
`dashboard/src/data/schema.js`를 함께 갱신할 것.

> 문제 정의가 "끓음/넘침"에서 **"조리 완료(doneness) 판정"** 으로 바뀌면서(2026-07-12) 계약도
> 갱신됨. 핵심 출력은 `doneness`(미완/완료/과조리). 관련: `research-methodology*.md`, `architecture.html`.

## MQTT 토픽

| 토픽 | 방향 | 설명 | 주기 |
|------|------|------|------|
| `cooking/status` | Jetson → 대시보드·로봇 | 현재 조리 상태 전체 스냅샷 | 1초 |
| `cooking/alerts` | Jetson → 대시보드·로봇 | 경고 이벤트 (발생 시) | 이벤트 |

> 솥 RGB **영상은 MQTT가 아니라 별도 MJPEG(HTTP) 스트림**으로 전송(대용량). 열화상(32×24)은
> 작아서 아래 `thermal.frame` 숫자배열로 이 토픽에 실어 보내고 대시보드가 히트맵으로 렌더한다.

## `cooking/status` 페이로드

```jsonc
{
  "timestamp": "2026-07-12T14:30:00Z", // ISO8601 UTC, 이 상태의 측정 시각
  "recipe_id": "kimchi_jjigae_v1",      // 레시피 식별자
  "stage": "cooking",                   // 공정 단계 (아래 enum)
  "doneness": "done",                   // ★ 조리 완료 판정 (아래 enum) — 시스템 핵심 출력
  "doneness_confidence": 0.93,          // 완료 판정 신뢰도 (0.0 ~ 1.0)
  "center_temp_c": 98.5,                // MLX90614 중심온도 (℃)
  "target_temp_c": 100.0,               // 목표 온도 (℃)
  "elapsed_sec": 620,                   // 조리 시작 후 경과 시간 (초)
  "boil_intensity": 0.82,               // 비전 기반 끓음 강도 (0.0 ~ 1.0)
  "thermal": {                          // MLX90640 32×24 열배열
    "w": 32, "h": 24, "unit": "C",
    "min": 74.2, "max": 101.6,          // 프레임 최소/최대 온도(℃) — 히트맵 스케일용
    "frame": [/* 768개 온도값, 행 우선(row-major) */]
  },
  "alerts": ["past_done"]               // 현재 활성 경고 코드 배열 (없으면 [])
}
```

## Enum 정의

### `stage` — 공정 단계 (시간·온도 기반, 규칙적)
| 값 | 뜻 |
|----|----|
| `ingredient_add` | 재료 투입 |
| `heating` | 가열 (온도 상승) |
| `cooking` | 조리 (끓이며 진행) |

### `doneness` — 조리 완료 판정 (AI 추론 결과, 순서형 ordinal) ★
| 값 | 뜻 | 대시보드 |
|----|----|----|
| `undercooked` | 미완 — 더 조리 필요 | 대기 |
| `done` | 완료 — 최적 배식 시점 | **완료 알림 (핵심 출력)** |
| `overcooked` | 과조리 — 졸아듦/품질 저하 | 경고 |

### `alerts[]` — 경고 코드
| 값 | 뜻 |
|----|----|
| `overflow_risk` | 넘침 위험 |
| `temp_too_high` | 과열 |
| `sensor_fault` | 센서 이상 |
| `low_confidence` | 추론 신뢰도 낮음 |
| `past_done` | 완료 시점 경과 (과조리 진입) |

## 필드 규약
- `timestamp`는 UTC ISO8601. 대시보드에서 로컬 시간으로 표시. (NTP 시간동기 전제 — `architecture.html` 참고)
- 온도는 섭씨(℃), 소수 첫째 자리까지. `thermal.frame`은 32×24=768개, **행 우선(row-major)**.
- `doneness`는 순서형(미완 < 완료 < 과조리) — 모델은 CORN 등 순위일관 분류로 출력.
- 알 수 없는 값은 필드를 생략하지 말고 `null` 또는 빈 배열/객체로 보낼 것(대시보드 파싱 안정성).
- 대시보드는 모르는 `alerts` 코드를 받아도 죽지 않고 코드 문자열 그대로 표시한다.
