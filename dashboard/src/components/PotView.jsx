import { useEffect, useRef } from 'react';

// 솥 탑뷰. 실장치(dataSource!=='mock')에선 Jetson의 MJPEG(HTTP) 스트림을 <img>로 표시.
// 목 모드에선 캔버스로 위에서 본 솥을 시뮬레이션(끓음 강도에 따라 기포 증가)해 레이아웃을 실증.
const MJPEG_URL = import.meta.env.VITE_POT_MJPEG_URL || '';

export default function PotView({ boil = 0, live = false }) {
  const canvasRef = useRef(null);
  const boilRef = useRef(boil);
  boilRef.current = boil;

  useEffect(() => {
    if (live) return; // 실장치 모드는 <img> 사용
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    let raf, t = 0;
    const bubbles = Array.from({ length: 46 }, () => ({
      a: Math.random() * Math.PI * 2,
      r: Math.random(),
      z: Math.random(),
      s: 0.4 + Math.random() * 0.9,
    }));

    function resize() {
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const box = canvas.getBoundingClientRect();
      canvas.width = box.width * dpr;
      canvas.height = box.height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    resize();

    function draw() {
      const W = canvas.getBoundingClientRect().width;
      const H = canvas.getBoundingClientRect().height;
      const cx = W / 2, cy = H / 2;
      const R = Math.min(W, H) * 0.44;
      const b = boilRef.current;

      ctx.clearRect(0, 0, W, H);

      // 솥 외벽(스테인리스 링)
      ctx.beginPath();
      ctx.arc(cx, cy, R + 8, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(160,168,180,0.18)';
      ctx.fill();

      // 국물 표면 (라디얼: 중앙 따뜻)
      const g = ctx.createRadialGradient(cx, cy - R * 0.15, R * 0.15, cx, cy, R);
      g.addColorStop(0, '#7a3418');
      g.addColorStop(0.6, '#5e2913');
      g.addColorStop(1, '#3f1c0e');
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, Math.PI * 2);
      ctx.fillStyle = g;
      ctx.fill();
      ctx.save();
      ctx.clip();

      // 기포: 끓음 강도에 비례해 크기·개수·속도 증가
      for (const bub of bubbles) {
        bub.z += (0.004 + b * 0.02) * bub.s;
        if (bub.z > 1) { bub.z = 0; bub.a = Math.random() * Math.PI * 2; bub.r = Math.random(); }
        const rr = bub.r * R * 0.92;
        const bx = cx + Math.cos(bub.a) * rr;
        const by = cy + Math.sin(bub.a) * rr;
        const size = (0.6 + bub.z * 2.4) * (0.6 + b * 1.6) * bub.s;
        const alpha = (0.15 + b * 0.5) * (1 - bub.z);
        ctx.beginPath();
        ctx.arc(bx, by, size, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255,236,206,${alpha})`;
        ctx.fill();
      }

      // 표면 하이라이트(젖은 광택)
      const hl = ctx.createRadialGradient(cx - R * 0.3, cy - R * 0.35, 2, cx - R * 0.3, cy - R * 0.35, R * 0.9);
      hl.addColorStop(0, 'rgba(255,240,220,0.16)');
      hl.addColorStop(1, 'rgba(255,240,220,0)');
      ctx.fillStyle = hl;
      ctx.fillRect(cx - R, cy - R, R * 2, R * 2);
      ctx.restore();

      // 림 라인
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, Math.PI * 2);
      ctx.strokeStyle = 'rgba(210,216,226,0.25)';
      ctx.lineWidth = 2;
      ctx.stroke();

      if (reduce) return; // 정지 프레임
      t += 1;
      raf = requestAnimationFrame(draw);
    }
    draw();

    const ro = new ResizeObserver(() => { resize(); if (reduce) draw(); });
    ro.observe(canvas);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); };
  }, [live]);

  return (
    <section className="card pot-card" aria-label="솥 탑뷰">
      <div className="card-head">
        <span className="cap">솥 탑뷰<span className="tile-src mono">{live ? 'MJPEG' : '목 시뮬레이션'}</span></span>
        <span className="pot-badge mono">{live ? 'LIVE' : 'SIM'}</span>
      </div>
      <div className="pot-body">
        {live ? (
          MJPEG_URL
            ? <img className="pot-img" src={MJPEG_URL} alt="솥 탑뷰 실시간 영상" />
            : <div className="pot-nostream">MJPEG 스트림 URL 미설정<br /><span className="mono">VITE_POT_MJPEG_URL</span></div>
        ) : (
          <canvas ref={canvasRef} className="pot-canvas" />
        )}
      </div>
    </section>
  );
}
