import { BOIL_STATE_LABELS } from '../data/schema';

export default function AiDetectionCard({ detection }) {
  if (!detection) return null;
  const { boil_state, confidence } = detection;
  const pct = Math.round(confidence * 100);
  const low = confidence < 0.6;

  return (
    <div className="card">
      <div className="card-title">AI 추론 (Jetson 비전)</div>

      <div className="ai-boil" data-boil={boil_state}>
        <span className="ai-boil-icon" aria-hidden>♨</span>
        <span className="ai-boil-label">{BOIL_STATE_LABELS[boil_state] || boil_state}</span>
      </div>

      <div className="ai-confidence">
        <div className="ai-confidence-head">
          <span>신뢰도</span>
          <span className={low ? 'conf-low' : ''}>{pct}%</span>
        </div>
        <div className="confidence-bar">
          <div
            className={`confidence-fill ${low ? 'low' : ''}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
    </div>
  );
}
