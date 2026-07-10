// 공유 데이터 계약의 프론트엔드 상수.
// docs/data-schema.md / shared/schema.json 과 반드시 일치시킬 것.

// 조리 단계
export const STAGES = ['ingredient_add', 'heating', 'boiling', 'done'];

export const STAGE_LABELS = {
  ingredient_add: '재료 투입',
  heating: '가열',
  boiling: '끓임',
  done: '완료',
};

// 끓음 상태 (Jetson 비전 분류 결과)
export const BOIL_STATE_LABELS = {
  none: '끓지 않음',
  simmer: '약하게 끓음',
  rolling_boil: '세게 끓음',
};

// 경고 코드 → 한국어 라벨. 모르는 코드는 코드 문자열 그대로 표시(안정성).
export const ALERT_LABELS = {
  overflow_risk: '넘침 위험',
  temp_too_high: '과열',
  sensor_fault: '센서 이상',
  low_confidence: '추론 신뢰도 낮음',
};

export function alertLabel(code) {
  return ALERT_LABELS[code] || code;
}
