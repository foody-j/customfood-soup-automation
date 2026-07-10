import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts';

export default function TemperatureChart({ history, target }) {
  return (
    <div className="card chart-card">
      <div className="card-title">온도 추이</div>
      <div className="chart-wrap">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={history} margin={{ top: 8, right: 16, bottom: 4, left: -8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a333d" />
            <XAxis
              dataKey="elapsed"
              stroke="#7a8894"
              tick={{ fontSize: 11 }}
              tickFormatter={(s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`}
              minTickGap={24}
            />
            <YAxis
              stroke="#7a8894"
              tick={{ fontSize: 11 }}
              domain={[0, 110]}
              ticks={[0, 25, 50, 75, 100]}
              unit="℃"
              width={44}
            />
            <Tooltip
              contentStyle={{ background: '#161c22', border: '1px solid #2a333d', borderRadius: 8, color: '#e6edf3' }}
              labelFormatter={(s) => `경과 ${Math.floor(s / 60)}분 ${s % 60}초`}
              formatter={(v) => [`${v}℃`, '온도']}
            />
            {target != null && (
              <ReferenceLine y={target} stroke="#f0883e" strokeDasharray="4 4"
                label={{ value: `목표 ${target}℃`, fill: '#f0883e', fontSize: 11, position: 'insideTopRight' }} />
            )}
            <Line
              type="monotone"
              dataKey="temp"
              stroke="#ff6b3d"
              strokeWidth={2.5}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
