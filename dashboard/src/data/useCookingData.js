import { useEffect, useRef, useState } from 'react';
import { createMockSource } from './mockSource';

// ─────────────────────────────────────────────────────────────────────────────
// ★ 데이터 소스 교체 지점 ★
// 지금은 목 데이터. 나중에 MQTT 연동 시 아래 createSource()만 바꾸면 되고,
// UI 컴포넌트는 전혀 손대지 않는다.
//
// MQTT 전환 예시 (mqtt.js over WebSocket, Pi의 Mosquitto:9001 구독):
//   import mqtt from 'mqtt';
//   function createMqttSource(url) {
//     return {
//       subscribe(onData) {
//         const client = mqtt.connect(url);
//         client.on('connect', () => client.subscribe('cooking/status'));
//         client.on('message', (_t, buf) => onData(JSON.parse(buf.toString())));
//         return () => client.end();
//       },
//     };
//   }
// ─────────────────────────────────────────────────────────────────────────────
const DATA_SOURCE = import.meta.env.VITE_DATA_SOURCE || 'mock';

function createSource() {
  switch (DATA_SOURCE) {
    // case 'mqtt':
    //   return createMqttSource(import.meta.env.VITE_MQTT_URL || 'ws://raspberrypi.local:9001');
    case 'mock':
    default:
      return createMockSource();
  }
}

const HISTORY_LEN = 120; // 온도 차트에 유지할 최근 데이터 포인트 수

// 대시보드가 쓰는 유일한 데이터 훅.
// 반환: { current, history, connected }
export function useCookingData() {
  const [current, setCurrent] = useState(null);
  const [history, setHistory] = useState([]);
  const [connected, setConnected] = useState(false);
  const sourceRef = useRef(null);

  useEffect(() => {
    const source = createSource();
    sourceRef.current = source;

    const unsubscribe = source.subscribe((data) => {
      setConnected(true);
      setCurrent(data);
      setHistory((prev) => {
        const point = {
          elapsed: data.elapsed_sec,
          temp: data.center_temp_c,
          doneness: data.doneness,
        };
        // 사이클이 되감기면(경과시간 급감) 히스토리 초기화
        const reset = prev.length > 0 && point.elapsed < prev[prev.length - 1].elapsed;
        const base = reset ? [] : prev;
        const next = [...base, point];
        return next.length > HISTORY_LEN ? next.slice(-HISTORY_LEN) : next;
      });
    });

    return () => {
      if (unsubscribe) unsubscribe();
      setConnected(false);
    };
  }, []);

  return { current, history, connected, dataSource: DATA_SOURCE };
}
