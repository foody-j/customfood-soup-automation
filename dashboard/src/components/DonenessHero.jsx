import {
  DONENESS, DONENESS_LABELS, DONENESS_EN, DONENESS_HINT, DONENESS_TOKEN,
} from '../data/schema';

// 시스템 핵심 출력 — 조리 완료 판정(미완/완료/과조리)을 크게 보여준다.
// 색은 항상 라벨과 함께(색상 단독 금지). 순서형이라 3단계 트랙으로 진행도 표시.
export default function DonenessHero({ state }) {
  if (!state) return null;
  const { doneness, doneness_confidence } = state;
  const token = DONENESS_TOKEN[doneness] || 'raw';
  const pct = Math.round((doneness_confidence ?? 0) * 100);
  const low = (doneness_confidence ?? 1) < 0.6;
  const idx = DONENESS.indexOf(doneness);

  return (
    <section className="card hero" data-done={token} aria-label="조리 완료 판정">
      <div className="hero-top">
        <span className="cap">조리 완료 판정</span>
        <span className="hero-en mono">{DONENESS_EN[doneness] || '—'}</span>
      </div>

      <div className="hero-main">
        <span className="hero-badge" aria-hidden />
        <div className="hero-text">
          <h2 className="hero-label">{DONENESS_LABELS[doneness] || doneness}</h2>
          <p className="hero-hint">{DONENESS_HINT[doneness] || ''}</p>
        </div>
      </div>

      {/* 순서형 3단계 진행 트랙 */}
      <ol className="ord-track" aria-hidden>
        {DONENESS.map((d, i) => (
          <li
            key={d}
            className={`ord-seg ${DONENESS_TOKEN[d]} ${i <= idx ? 'reached' : ''} ${i === idx ? 'current' : ''}`}
          >
            <span className="ord-name">{DONENESS_LABELS[d]}</span>
          </li>
        ))}
      </ol>

      <div className="hero-conf">
        <div className="hero-conf-head">
          <span className="cap">판정 신뢰도</span>
          <span className={`mono num ${low ? 'conf-low' : ''}`}>{pct}%</span>
        </div>
        <div className="meter" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
          <div className={`meter-fill ${low ? 'low' : ''}`} style={{ width: `${pct}%` }} />
        </div>
        {low && <p className="conf-note">신뢰도가 낮습니다 — 판정을 신뢰하기 전 확인 필요</p>}
      </div>
    </section>
  );
}
