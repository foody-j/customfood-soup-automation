// 목(mock) 데이터 소스 — 하드웨어/AI 없이 대시보드를 구동하기 위한 시뮬레이터.
// 국/탕 한 사이클(재료투입 → 가열 → 끓임 → 완료)을 반복하며 cooking/status 페이로드를 방출한다.
// 실제 Jetson이 보낼 데이터와 동일한 형태를 유지한다(docs/data-schema.md).

const CYCLE_SEC = 285; // 한 조리 사이클 길이(초)

// 약간의 노이즈
function noise(amp) {
  return (Math.random() - 0.5) * 2 * amp;
}

// 경과 시간(초)으로부터 조리 상태 스냅샷을 계산한다.
function computeState(elapsed) {
  let stage, temperature, boil_state, confidence;
  const target = 100;
  const alerts = [];

  if (elapsed < 15) {
    // 재료 투입
    stage = 'ingredient_add';
    temperature = 20 + noise(0.5);
    boil_state = 'none';
    confidence = 0.9 + noise(0.03);
  } else if (elapsed < 135) {
    // 가열: 20 → 100 ℃ 상승
    stage = 'heating';
    const p = (elapsed - 15) / (135 - 15);
    temperature = 20 + p * 80 + noise(0.6);
    if (temperature > 98) boil_state = 'rolling_boil';
    else if (temperature > 88) boil_state = 'simmer';
    else boil_state = 'none';
    // 온도 급변 구간은 추론 신뢰도가 조금 낮음
    confidence = 0.8 + p * 0.15 + noise(0.03);
  } else if (elapsed < 255) {
    // 끓임: 목표 온도 유지
    stage = 'boiling';
    temperature = 99 + noise(1.2);
    boil_state = 'rolling_boil';
    confidence = 0.94 + noise(0.03);
    // 끓임 중반에 간헐적 넘침 위험
    if (elapsed > 180 && elapsed < 210) alerts.push('overflow_risk');
    if (temperature > 100.5) alerts.push('temp_too_high');
  } else {
    // 완료
    stage = 'done';
    temperature = 96 + noise(0.8);
    boil_state = 'simmer';
    confidence = 0.92 + noise(0.03);
  }

  confidence = Math.min(0.99, Math.max(0.5, confidence));
  if (confidence < 0.6) alerts.push('low_confidence');

  return {
    timestamp: new Date().toISOString(),
    recipe_id: 'kimchi_jjigae_v1',
    stage,
    temperature_c: Math.round(temperature * 10) / 10,
    target_temp_c: target,
    elapsed_sec: elapsed,
    ai_detection: {
      boil_state,
      confidence: Math.round(confidence * 100) / 100,
    },
    alerts,
  };
}

// 소스 인터페이스: subscribe(onData) → unsubscribe 함수 반환.
// MQTT 소스도 동일한 인터페이스를 구현하면 useCookingData에서 그대로 교체 가능.
export function createMockSource({ intervalMs = 1000 } = {}) {
  return {
    subscribe(onData) {
      let elapsed = 0;
      // 즉시 첫 값 방출
      onData(computeState(elapsed));
      const id = setInterval(() => {
        elapsed = (elapsed + 1) % CYCLE_SEC;
        onData(computeState(elapsed));
      }, intervalMs);
      return () => clearInterval(id);
    },
  };
}
