import { alertLabel } from '../data/schema';

// 경고 패널 — 상태색은 아이콘·라벨과 함께(색상 단독 금지).
export default function AlertPanel({ alerts }) {
  const list = alerts || [];

  return (
    <section className="card" aria-label="경고">
      <div className="card-head">
        <span className="cap">경고</span>
        {list.length > 0 && <span className="alert-count mono num">{list.length}</span>}
      </div>
      {list.length === 0 ? (
        <div className="alert-ok">
          <span className="alert-ok-dot" aria-hidden /> 정상
        </div>
      ) : (
        <ul className="alert-list">
          {list.map((code) => (
            <li key={code} className="alert-item">
              <span className="alert-icon" aria-hidden>⚠</span>
              <span>{alertLabel(code)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
