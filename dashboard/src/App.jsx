import { useCookingData } from './data/useCookingData';
import StatusCard from './components/StatusCard';
import TemperatureChart from './components/TemperatureChart';
import StageTimeline from './components/StageTimeline';
import AiDetectionCard from './components/AiDetectionCard';
import AlertPanel from './components/AlertPanel';
import './App.css';

export default function App() {
  const { current, history, connected, dataSource } = useCookingData();

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>커스텀푸드 국/탕 조리 자동화</h1>
          <p className="subtitle">단국대학교 · 실시간 조리 모니터링 대시보드</p>
        </div>
        <div className={`conn-badge ${connected ? 'on' : 'off'}`}>
          <span className="conn-dot" />
          {connected ? '연결됨' : '연결 안 됨'}
          <span className="conn-source">{dataSource === 'mock' ? 'MOCK' : dataSource.toUpperCase()}</span>
        </div>
      </header>

      {!current ? (
        <div className="loading">데이터 수신 대기 중…</div>
      ) : (
        <main className="grid">
          <StatusCard state={current} />
          <TemperatureChart history={history} target={current.target_temp_c} />
          <StageTimeline currentStage={current.stage} />
          <AiDetectionCard detection={current.ai_detection} />
          <AlertPanel alerts={current.alerts} />
        </main>
      )}

      <footer className="app-footer">
        마지막 갱신: {current ? new Date(current.timestamp).toLocaleTimeString('ko-KR') : '—'}
      </footer>
    </div>
  );
}
