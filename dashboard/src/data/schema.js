// 공유 데이터 계약의 프론트엔드 상수.
// docs/data-schema.md / shared/schema.json 과 반드시 일치시킬 것.

// ── 공정 단계 (시간·온도 기반) ──────────────────────────────────────────────
export const STAGES = ['ingredient_add', 'heating', 'cooking'];

export const STAGE_LABELS = {
  ingredient_add: '재료 투입',
  heating: '가열',
  cooking: '조리(끓임)',
};

// ── 조리 완료 판정 (AI 추론, 순서형) ★ 시스템 핵심 출력 ────────────────────
export const DONENESS = ['undercooked', 'done', 'overcooked'];

export const DONENESS_LABELS = {
  undercooked: '미완',
  done: '완료',
  overcooked: '과조리',
};

export const DONENESS_EN = {
  undercooked: 'UNDERCOOKED',
  done: 'DONE',
  overcooked: 'OVERCOOKED',
};

export const DONENESS_HINT = {
  undercooked: '더 조리가 필요합니다',
  done: '지금이 최적 배식 시점입니다',
  overcooked: '졸아듦·품질 저하 — 조리 종료 권장',
};

// 상태 색상 토큰명(CSS 변수와 매칭). 색은 항상 라벨과 함께 표시(색상 단독 금지).
export const DONENESS_TOKEN = {
  undercooked: 'raw',
  done: 'done',
  overcooked: 'over',
};

// ── 경고 코드 → 한국어 라벨. 모르는 코드는 코드 문자열 그대로(안정성). ─────
export const ALERT_LABELS = {
  overflow_risk: '넘침 위험',
  temp_too_high: '과열',
  sensor_fault: '센서 이상',
  low_confidence: '추론 신뢰도 낮음',
  past_done: '완료 시점 경과',
};

export function alertLabel(code) {
  return ALERT_LABELS[code] || code;
}

// ── 열화상 컬러맵: 인페르노 계열(지각균일, 무지개 아님). t: 0..1 → 'rgb(...)' ──
const INFERNO = [
  [0, 0, 4], [40, 11, 84], [101, 21, 110], [159, 42, 99],
  [212, 72, 66], [245, 125, 21], [250, 193, 39], [252, 255, 164],
];
export function infernoColor(t) {
  const x = Math.min(1, Math.max(0, t)) * (INFERNO.length - 1);
  const i = Math.floor(x);
  const f = x - i;
  const a = INFERNO[i];
  const b = INFERNO[Math.min(INFERNO.length - 1, i + 1)];
  const r = Math.round(a[0] + (b[0] - a[0]) * f);
  const g = Math.round(a[1] + (b[1] - a[1]) * f);
  const bl = Math.round(a[2] + (b[2] - a[2]) * f);
  return `rgb(${r},${g},${bl})`;
}
