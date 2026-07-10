import { alertLabel } from '../data/schema';

export default function AlertPanel({ alerts }) {
  const list = alerts || [];

  return (
    <div className="card">
      <div className="card-title">경고</div>
      {list.length === 0 ? (
        <div className="alert-ok">
          <span className="alert-ok-dot" /> 정상
        </div>
      ) : (
        <ul className="alert-list">
          {list.map((code) => (
            <li key={code} className="alert-item">
              <span className="alert-icon" aria-hidden>⚠</span>
              {alertLabel(code)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
