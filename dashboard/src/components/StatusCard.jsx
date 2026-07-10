import { STAGE_LABELS } from '../data/schema';

function formatElapsed(sec) {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

export default function StatusCard({ state }) {
  if (!state) return null;
  const stageLabel = STAGE_LABELS[state.stage] || state.stage;

  return (
    <div className="card status-card">
      <div className="card-title">현재 조리 상태</div>

      <div className="status-recipe">{state.recipe_id}</div>

      <div className="status-stage" data-stage={state.stage}>
        {stageLabel}
      </div>

      <div className="status-metrics">
        <div className="metric">
          <span className="metric-label">국물 온도</span>
          <span className="metric-value temp">
            {state.temperature_c.toFixed(1)}<small>℃</small>
          </span>
          <span className="metric-sub">목표 {state.target_temp_c}℃</span>
        </div>
        <div className="metric">
          <span className="metric-label">경과 시간</span>
          <span className="metric-value">{formatElapsed(state.elapsed_sec)}</span>
          <span className="metric-sub">mm:ss</span>
        </div>
      </div>
    </div>
  );
}
