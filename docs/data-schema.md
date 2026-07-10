# 공유 데이터 계약 (Data Contract)

Jetson Orin Nano(발행)와 대시보드(구독)가 주고받는 데이터 형식. **이 문서가 두 보드의
유일한 계약이다.** 형식을 바꾸면 이 문서, `shared/schema.json`,
`dashboard/src/data/schema.js`를 함께 갱신할 것.

## MQTT 토픽

| 토픽 | 방향 | 설명 | 주기 |
|------|------|------|------|
| `cooking/status` | Jetson → 대시보드 | 현재 조리 상태 전체 스냅샷 | 1초 |
| `cooking/alerts` | Jetson → 대시보드 | 경고 이벤트 (발생 시) | 이벤트 |

## `cooking/status` 페이로드

```jsonc
{
  "timestamp": "2026-07-10T14:30:00Z",  // ISO8601 UTC, 이 상태의 측정 시각
  "recipe_id": "kimchi_jjigae_v1",       // 레시피 식별자
  "stage": "boiling",                    // 조리 단계 (아래 enum)
  "temperature_c": 98.5,                 // 현재 국물 온도 (℃)
  "target_temp_c": 100.0,                // 목표 온도 (℃)
  "elapsed_sec": 620,                    // 조리 시작 후 경과 시간 (초)
  "ai_detection": {                      // Jetson 비전 추론 결과
    "boil_state": "rolling_boil",        // 끓음 상태 (아래 enum)
    "confidence": 0.94                   // 추론 신뢰도 (0.0 ~ 1.0)
  },
  "alerts": ["overflow_risk"]            // 현재 활성 경고 코드 배열 (없으면 [])
}
```

## Enum 정의

### `stage` — 조리 단계
| 값 | 뜻 |
|----|----|
| `ingredient_add` | 재료 투입 |
| `heating` | 가열 (온도 상승) |
| `boiling` | 끓임 (목표 온도 유지) |
| `done` | 완료 |

### `ai_detection.boil_state` — 끓음 상태 (비전 분류 결과)
| 값 | 뜻 |
|----|----|
| `none` | 끓지 않음 |
| `simmer` | 약하게 끓음 (기포 소량) |
| `rolling_boil` | 세게 끓음 (전체적으로 끓어오름) |

### `alerts[]` — 경고 코드
| 값 | 뜻 |
|----|----|
| `overflow_risk` | 넘침 위험 |
| `temp_too_high` | 과열 |
| `sensor_fault` | 센서 이상 |
| `low_confidence` | 추론 신뢰도 낮음 |

## 필드 규약
- `timestamp`는 UTC ISO8601. 대시보드에서 로컬 시간으로 표시.
- 온도는 섭씨(℃), 소수 첫째 자리까지.
- 알 수 없는 값은 필드를 생략하지 말고 `null` 또는 빈 배열로 보낼 것(대시보드 파싱 안정성).
- 대시보드는 모르는 `alerts` 코드를 받아도 죽지 않고 코드 문자열 그대로 표시한다.
