import { useEffect, useRef, useState } from 'react';
import { infernoColor } from '../data/schema';

// MLX90640 32×24 열배열 → 인페르노 히트맵(지각균일 sequential). 블록 셀 + 셀 호버 툴팁.
export default function ThermalHeatmap({ thermal }) {
  const canvasRef = useRef(null);
  const [hover, setHover] = useState(null);

  const w = thermal?.w ?? 32;
  const h = thermal?.h ?? 24;
  const frame = thermal?.frame;
  const min = thermal?.min ?? 0;
  const max = thermal?.max ?? 100;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !frame) return;
    const ctx = canvas.getContext('2d');
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    const img = ctx.createImageData(w, h);
    const span = max - min || 1;
    for (let i = 0; i < w * h; i++) {
      const t = (frame[i] - min) / span;
      const c = infernoColor(t).match(/\d+/g);
      img.data[i * 4] = +c[0];
      img.data[i * 4 + 1] = +c[1];
      img.data[i * 4 + 2] = +c[2];
      img.data[i * 4 + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
  }, [frame, w, h, min, max]);

  function onMove(e) {
    if (!frame) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const cx = Math.floor(((e.clientX - rect.left) / rect.width) * w);
    const cy = Math.floor(((e.clientY - rect.top) / rect.height) * h);
    if (cx < 0 || cy < 0 || cx >= w || cy >= h) return setHover(null);
    setHover({
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
      temp: frame[cy * w + cx],
    });
  }

  return (
    <section className="card thermal-card" aria-label="열화상 (MLX90640)">
      <div className="card-head">
        <span className="cap">열화상 분포<span className="tile-src mono">MLX90640 · 32×24</span></span>
        <span className="mono num thermal-range">{min.toFixed(0)}–{max.toFixed(0)}℃</span>
      </div>
      <div className="thermal-body" onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <canvas ref={canvasRef} className="thermal-canvas" />
        {hover && (
          <div className="thermal-tip" style={{ left: hover.x, top: hover.y }}>
            <span className="mono num">{hover.temp.toFixed(1)}℃</span>
          </div>
        )}
      </div>
      <div className="thermal-scale">
        <span className="mono num">{min.toFixed(0)}℃</span>
        <div className="thermal-bar" aria-hidden />
        <span className="mono num">{max.toFixed(0)}℃</span>
      </div>
    </section>
  );
}
