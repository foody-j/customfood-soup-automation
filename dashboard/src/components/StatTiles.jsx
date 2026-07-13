import { STAGE_LABELS } from '../data/schema';

function formatElapsed(sec) {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

// 핵심 수치 4종 타일: 중심온도 · 경과시간 · 끓음강도 · 공정단계.
export default function StatTiles({ state }) {
  if (!state) return null;
  const tempPct = Math.min(100, Math.max(0, (state.center_temp_c / (state.target_temp_c || 100)) * 100));
  const boilPct = Math.round((state.boil_intensity ?? 0) * 100);

  return (
    <div className="tiles">
      <div className="tile">
        <span className="cap">중심온도<span className="tile-src mono">MLX90614</span></span>
        <span className="tile-val num">
          {state.center_temp_c.toFixed(1)}<small>℃</small>
        </span>
        <div className="tile-track"><div className="tile-track-fill temp" style={{ width: `${tempPct}%` }} /></div>
        <span className="tile-sub">목표 {state.target_temp_c}℃</span>
      </div>

      <div className="tile">
        <span className="cap">경과 시간</span>
        <span className="tile-val num">{formatElapsed(state.elapsed_sec)}</span>
        <span className="tile-sub">mm:ss</span>
      </div>

      <div className="tile">
        <span className="cap">끓음 강도<span className="tile-src mono">비전</span></span>
        <span className="tile-val num">{boilPct}<small>%</small></span>
        <div className="tile-track"><div className="tile-track-fill boil" style={{ width: `${boilPct}%` }} /></div>
        <span className="tile-sub">거품 동역학</span>
      </div>

      <div className="tile">
        <span className="cap">공정 단계</span>
        <span className="tile-val tile-val-sm">{STAGE_LABELS[state.stage] || state.stage}</span>
        <span className="tile-sub">시간·온도 기반</span>
      </div>
    </div>
  );
}
