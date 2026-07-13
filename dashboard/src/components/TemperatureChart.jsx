import { useMemo } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts';

// 중심온도 추이 — 단일 시리즈(범례 불필요). 크로스헤어 툴팁·목표 기준선.
// theme prop이 바뀌면 CSS 토큰을 다시 읽어 색을 맞춘다(단일 색 출처).
function useThemeColors(theme) {
  return useMemo(() => {
    const s = getComputedStyle(document.documentElement);
    const v = (n, f) => (s.getPropertyValue(n).trim() || f);
    return {
      line: v('--accent', '#e0602a'),
      grid: v('--line', '#2c313b'),
      axis: v('--ink-faint', '#8a92a0'),
      target: v('--s-over', '#d9571f'),
      surface: v('--surface', '#191c22'),
      ink: v('--ink', '#e7e9ed'),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme]);
}

const fmtTime = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;

export default function TemperatureChart({ history, target, theme }) {
  const c = useThemeColors(theme);

  return (
    <section className="card chart-card" aria-label="중심온도 추이">
      <div className="card-head">
        <span className="cap">중심온도 추이<span className="tile-src mono">최근 {history.length}s</span></span>
      </div>
      <div className="chart-wrap">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={history} margin={{ top: 10, right: 14, bottom: 4, left: -10 }}>
            <defs>
              <linearGradient id="tempFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={c.line} stopOpacity={0.28} />
                <stop offset="100%" stopColor={c.line} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke={c.grid} strokeDasharray="2 4" vertical={false} />
            <XAxis
              dataKey="elapsed" stroke={c.grid} tick={{ fontSize: 11, fill: c.axis }}
              tickFormatter={fmtTime} minTickGap={30} tickLine={false}
            />
            <YAxis
              stroke={c.grid} tick={{ fontSize: 11, fill: c.axis }} tickLine={false}
              domain={[0, 110]} ticks={[0, 25, 50, 75, 100]} width={40} unit="℃"
            />
            <Tooltip
              cursor={{ stroke: c.axis, strokeDasharray: '3 3' }}
              contentStyle={{
                background: c.surface, border: `1px solid ${c.grid}`,
                borderRadius: 10, color: c.ink, fontSize: 12, boxShadow: '0 6px 20px rgba(0,0,0,.25)',
              }}
              labelFormatter={(s) => `경과 ${fmtTime(s)}`}
              formatter={(v) => [`${v}℃`, '중심온도']}
            />
            {target != null && (
              <ReferenceLine
                y={target} stroke={c.target} strokeDasharray="5 4"
                label={{ value: `목표 ${target}℃`, fill: c.target, fontSize: 11, position: 'insideTopRight' }}
              />
            )}
            <Area
              type="monotone" dataKey="temp" stroke={c.line} strokeWidth={2}
              fill="url(#tempFill)" dot={false} isAnimationActive={false}
              activeDot={{ r: 4, fill: c.line, stroke: c.surface, strokeWidth: 2 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}
