// 목(mock) 데이터 소스 — 하드웨어/AI 없이 대시보드를 구동하기 위한 시뮬레이터.
// 국/탕 한 사이클(재료투입 → 가열 → 조리: 미완→완료→과조리)을 반복하며
// cooking/status 페이로드를 방출한다. 실제 Jetson이 보낼 형태와 동일(docs/data-schema.md).

const CYCLE_SEC = 300; // 한 조리 사이클 길이(초)
const TW = 32, TH = 24; // MLX90640 해상도

function noise(amp) {
  return (Math.random() - 0.5) * 2 * amp;
}
function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v));
}

// 32×24 열배열 시뮬레이션: 솥 중앙이 뜨겁고, 끓음 강도에 따라 표면 핫스팟이 요동친다.
function makeThermalFrame(centerTemp, boil, t) {
  const frame = new Array(TW * TH);
  let min = Infinity, max = -Infinity;
  const cx = TW / 2, cy = TH / 2;
  const rimTemp = centerTemp - 22; // 솥 가장자리는 더 차갑다
  for (let y = 0; y < TH; y++) {
    for (let x = 0; x < TW; x++) {
      const dx = (x - cx) / cx, dy = (y - cy) / cy;
      const r = Math.min(1, Math.sqrt(dx * dx + dy * dy));
      // 반경 방향 온도장 (중앙 뜨거움 → 가장자리 차가움)
      let temp = centerTemp - (centerTemp - rimTemp) * Math.pow(r, 1.7);
      // 끓음 핫스팟: 움직이는 정현파 간섭 + 난류 노이즈
      const bubble =
        Math.sin(x * 0.9 + t * 2.1) * Math.cos(y * 1.1 - t * 1.7) +
        Math.sin((x + y) * 0.7 + t * 3.3);
      temp += boil * (bubble * 2.2 + noise(2.5)) * (1 - r * 0.5);
      frame[y * TW + x] = Math.round(temp * 10) / 10;
      if (temp < min) min = temp;
      if (temp > max) max = temp;
    }
  }
  return { w: TW, h: TH, unit: 'C', min: Math.round(min * 10) / 10, max: Math.round(max * 10) / 10, frame };
}

// 경과 시간(초)으로부터 조리 상태 스냅샷을 계산한다.
function computeState(elapsed) {
  const target = 100;
  const alerts = [];
  let stage, temp, boil, doneness, dConf;

  if (elapsed < 18) {
    // 재료 투입
    stage = 'ingredient_add';
    temp = 22 + noise(0.5);
    boil = 0;
    doneness = 'undercooked';
    dConf = 0.88 + noise(0.03);
  } else if (elapsed < 130) {
    // 가열: 22 → 99 ℃
    stage = 'heating';
    const p = (elapsed - 18) / (130 - 18);
    temp = 22 + p * 77 + noise(0.6);
    boil = clamp((temp - 88) / 12, 0, 1) * 0.6;
    doneness = 'undercooked';
    dConf = 0.82 + p * 0.12 + noise(0.03); // 상승 구간은 신뢰도 다소 낮음
  } else {
    // 조리(끓임): 미완 → 완료 → 과조리
    stage = 'cooking';
    temp = 99 + noise(1.1);
    boil = clamp(0.85 + noise(0.12), 0, 1);
    if (elapsed < 190) {
      doneness = 'undercooked';
      dConf = 0.9 + noise(0.03);
    } else if (elapsed < 250) {
      doneness = 'done';
      dConf = 0.94 + noise(0.03);
    } else {
      doneness = 'overcooked';
      dConf = 0.9 + noise(0.03);
      alerts.push('past_done');
    }
    // 격렬히 끓을 때 간헐적 넘침 위험
    if (elapsed > 150 && elapsed < 175) alerts.push('overflow_risk');
    if (temp > 100.6) alerts.push('temp_too_high');
  }

  dConf = clamp(dConf, 0.5, 0.99);
  if (dConf < 0.6) alerts.push('low_confidence');

  const centerTemp = Math.round(temp * 10) / 10;
  const tPhase = elapsed * 0.35; // 열화상 애니메이션 위상

  return {
    timestamp: new Date().toISOString(),
    recipe_id: 'kimchi_jjigae_v1',
    stage,
    doneness,
    doneness_confidence: Math.round(dConf * 100) / 100,
    center_temp_c: centerTemp,
    target_temp_c: target,
    elapsed_sec: elapsed,
    boil_intensity: Math.round(boil * 100) / 100,
    thermal: makeThermalFrame(centerTemp, boil, tPhase),
    alerts,
  };
}

// 소스 인터페이스: subscribe(onData) → unsubscribe 함수 반환.
// MQTT 소스도 동일한 인터페이스를 구현하면 useCookingData에서 그대로 교체 가능.
export function createMockSource({ intervalMs = 1000 } = {}) {
  return {
    subscribe(onData) {
      let elapsed = 0;
      onData(computeState(elapsed)); // 즉시 첫 값
      const id = setInterval(() => {
        elapsed = (elapsed + 1) % CYCLE_SEC;
        onData(computeState(elapsed));
      }, intervalMs);
      return () => clearInterval(id);
    },
  };
}
