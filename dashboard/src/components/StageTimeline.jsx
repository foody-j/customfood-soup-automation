import { STAGES, STAGE_LABELS } from '../data/schema';

// 공정 단계 진행 스텝퍼 (재료 투입 → 가열 → 조리).
export default function StageTimeline({ currentStage }) {
  const currentIdx = STAGES.indexOf(currentStage);

  return (
    <section className="card" aria-label="공정 단계">
      <div className="card-head"><span className="cap">공정 단계</span></div>
      <ol className="timeline">
        {STAGES.map((stage, i) => {
          const status = i < currentIdx ? 'done' : i === currentIdx ? 'active' : 'upcoming';
          return (
            <li key={stage} className={`timeline-step ${status}`}>
              <span className="timeline-dot">{i < currentIdx ? '✓' : i + 1}</span>
              <span className="timeline-label">{STAGE_LABELS[stage]}</span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
