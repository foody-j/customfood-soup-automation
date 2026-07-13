import { useEffect, useState } from 'react';
import { useCookingData } from './data/useCookingData';
import DonenessHero from './components/DonenessHero';
import StatTiles from './components/StatTiles';
import PotView from './components/PotView';
import ThermalHeatmap from './components/ThermalHeatmap';
import TemperatureChart from './components/TemperatureChart';
import StageTimeline from './components/StageTimeline';
import AlertPanel from './components/AlertPanel';
import './App.css';

function useTheme() {
  const [theme, setTheme] = useState(
    () => localStorage.getItem('cf-theme') || 'dark'
  );
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('cf-theme', theme);
  }, [theme]);
  return [theme, () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))];
}

export default function App() {
  const { current, history, connected, dataSource } = useCookingData();
  const [theme, toggleTheme] = useTheme();
  const live = dataSource !== 'mock';

  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark" aria-hidden />
          <div>
            <h1>국·탕 조리 완료 모니터</h1>
            <p className="subtitle">단국대학교 커스텀푸드 · 실시간 doneness 판정</p>
          </div>
        </div>
        <div className="header-right">
          <div className={`conn-badge ${connected ? 'on' : 'off'}`}>
            <span className="conn-dot" aria-hidden />
            {connected ? '연결됨' : '연결 안 됨'}
            <span className="conn-source mono">{live ? dataSource.toUpperCase() : 'MOCK'}</span>
          </div>
          <button className="theme-btn" onClick={toggleTheme} aria-label="테마 전환">
            {theme === 'dark' ? '☀' : '☾'}
          </button>
        </div>
      </header>

      {!current ? (
        <div className="loading">데이터 수신 대기 중…</div>
      ) : (
        <main className="layout">
          <div className="col-main">
            <DonenessHero state={current} />
            <StatTiles state={current} />
            <TemperatureChart history={history} target={current.target_temp_c} theme={theme} />
          </div>
          <div className="col-side">
            <PotView boil={current.boil_intensity} live={live} />
            <ThermalHeatmap thermal={current.thermal} />
            <StageTimeline currentStage={current.stage} />
            <AlertPanel alerts={current.alerts} />
          </div>
        </main>
      )}

      <footer className="app-footer">
        <span>마지막 갱신 {current ? new Date(current.timestamp).toLocaleTimeString('ko-KR') : '—'}</span>
        <span className="mono">{current?.recipe_id || ''}</span>
      </footer>
    </div>
  );
}
