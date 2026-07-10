import { STAGES, STAGE_LABELS } from '../data/schema';

export default function StageTimeline({ currentStage }) {
  const currentIdx = STAGES.indexOf(currentStage);

  return (
    <div className="card">
      <div className="card-title">조리 단계</div>
      <ol className="timeline">
        {STAGES.map((stage, i) => {
          const status =
            i < currentIdx ? 'done' : i === currentIdx ? 'active' : 'upcoming';
          return (
            <li key={stage} className={`timeline-step ${status}`}>
              <span className="timeline-dot">{i < currentIdx ? '✓' : i + 1}</span>
              <span className="timeline-label">{STAGE_LABELS[stage]}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
