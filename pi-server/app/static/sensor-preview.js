'use strict';
/* 열화상·PT100 표시 컴포넌트 — 기존 화면에 끼워 넣는 **독립 모듈**(camera-preview.js와 같은 방식).
 *
 * 하는 일: 관리 서버가 중계하는 Jetson JSON 미리보기(GET {apiBase}/{sensor_id}/{stream_id}?session_id=)를
 * 받아 카드로 그린다.
 *   - thermal: rows×cols 히트맵, min/max/mean, 누른 화소의 온도. `deci`만 10으로 나눈다(min/max/mean은 이미 ℃).
 *     표시 범위는 고정(설정 range_c)이 기본이고 자동 범위를 고를 수 있다 — 원본 값은 바꾸지 않는다.
 *   - scalar(PT100): `valid`·`value.temp_c`·`invalid_reason`. valid:false는 온도로 그리지 않는다.
 *     null과 0 ℃를 구분하고, 미수신(404)·센서 오류(valid:false)·통신 오류(Jetson/SPI)를 나눠 표시한다.
 * 하지 않는 일: 센서 장치·원본 파일 접근, Jetson 설정 변경, 값 보정. 요청은 조회(GET)뿐이다.
 *
 * 신선도: HTTP 200은 "캐시에 값이 있다"일 뿐이다(열화상 무효 샘플은 Jetson 캐시에 들어가지 않는다).
 * 그래서 `seq`가 staleAfterMs 동안 늘지 않으면 '갱신 지연'으로 표시하고 마지막 값을 흐리게 남긴다.
 * 같은 seq는 새 측정으로 치지 않는다(추이 그래프에 반복 삽입하지 않음).
 *
 *   const view = SensorPreview.mount(el, {
 *     apiBase: '/api/preview_array',
 *     sensors: [{ id, label, sensor_id, stream_id, kind: 'thermal'|'scalar', stale_after_ms, range_c, field, unit, note }],
 *     intervalMs: 1000, headers: { 'X-Soup-Client': 'ui' }, storageKey: 'soup.sensorPreview',
 *   });
 *   view.setActive(true, '', sessionId);   // 볼 세션. 세션이 바뀌면 이전 값·늦은 응답을 버린다
 *   view.setActive(false, '진행 중인 세션 없음');
 *   (GET /api/sensor-preview/config 응답을 SensorPreview.fromServerConfig()에 넣으면 위 설정이 된다.)
 *
 * 요청이 멈추는 조건: 화면 밖·탭 숨김·setActive(false)·destroy(). 카드마다 요청은 하나씩만 돈다(겹치지 않음).
 */
(function (global) {
  const STATE_TEXT = {
    idle: '대기',
    loading: '연결 중',
    live: '수신 중',
    stale: '갱신 지연',
    noframe: '미수신',
    invalid: '센서 오류',
    comm: '통신 오류',
    jetson_down: 'Jetson 통신 오류',
    server_down: '서버 무응답',
    switching: '세션 전환 중',
    paused: '일시 중지',
  };
  const BACKOFF_STEPS = [1, 1, 2, 4, 8];
  const HIST_MAX = 120;
  const SPARK_W = 240;
  const SPARK_H = 56;

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function fmt(v, digits) {
    return typeof v === 'number' && Number.isFinite(v) ? v.toFixed(digits) : '—';
  }

  function timeText(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleTimeString('ko-KR', { hour12: false });
  }

  // 열화상 색 — 어두운 보라 → 빨강 → 노랑 → 흰색(inferno 근사). t는 0~1.
  const RAMP = [[0, 0, 4], [40, 11, 84], [101, 21, 110], [159, 42, 99], [212, 72, 66],
    [245, 125, 21], [250, 193, 39], [252, 255, 164]];
  function rampColor(t) {
    const x = Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0)) * (RAMP.length - 1);
    const i = Math.min(RAMP.length - 2, Math.floor(x));
    const f = x - i;
    const a = RAMP[i];
    const b = RAMP[i + 1];
    return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
  }
  const RAMP_CSS = `linear-gradient(90deg, ${RAMP.map((c, i) =>
    `rgb(${c.join(',')}) ${(i / (RAMP.length - 1) * 100).toFixed(0)}%`).join(', ')})`;

  function normalizeSensors(sensors) {
    if (!Array.isArray(sensors) || !sensors.length) throw new Error('SensorPreview: sensors가 비어 있음');
    return sensors.map((s, i) => {
      if (!s || !s.sensor_id || !s.stream_id) throw new Error(`SensorPreview: sensors[${i}] sensor_id/stream_id 없음`);
      if (s.kind !== 'thermal' && s.kind !== 'scalar') throw new Error(`SensorPreview: sensors[${i}].kind`);
      return {
        id: String(s.id || `${s.sensor_id}/${s.stream_id}`),
        label: String(s.label || s.sensor_id),
        sensorId: s.sensor_id,
        streamId: s.stream_id,
        kind: s.kind,
        staleAfterMs: Number(s.stale_after_ms) > 0 ? Number(s.stale_after_ms) : 5000,
        range: Array.isArray(s.range_c) && s.range_c.length === 2 ? [Number(s.range_c[0]), Number(s.range_c[1])] : [15, 110],
        field: s.field || 'temp_c',
        unit: s.unit || '℃',
        note: s.note || '',
      };
    });
  }

  function mount(container, options) {
    if (!container) throw new Error('SensorPreview: container 없음');
    const opts = options || {};
    if (!opts.apiBase) throw new Error('SensorPreview: apiBase 없음');
    const apiBase = String(opts.apiBase).replace(/\/+$/, '');
    const intervalMs = Math.max(250, Number(opts.intervalMs) || 1000);
    const headers = opts.headers || {};
    const storageKey = opts.storageKey === undefined ? 'soup.sensorPreview' : opts.storageKey;

    let remembered = {};
    if (storageKey) {
      try { remembered = JSON.parse(localStorage.getItem(storageKey) || '{}') || {}; } catch (_) { remembered = {}; }
    }
    function remember(id, key, value) {
      if (!storageKey) return;
      remembered[id] = { ...(remembered[id] || {}), [key]: value };
      try { localStorage.setItem(storageKey, JSON.stringify(remembered)); } catch (_) { /* 저장 불가 — 무시 */ }
    }

    let destroyed = false;
    let active = false;
    let inactiveText = '';
    let sessionId = null;
    let generation = 0;  // 세션이 바뀔 때마다 올린다 — 이전 세션 값의 늦은 응답을 버리는 기준
    let rootVisible = !('IntersectionObserver' in global);
    let timer = null;

    const root = el('div', 'spv');
    const grid = el('div', 'spv-grid');
    root.appendChild(grid);

    const cards = normalizeSensors(opts.sensors).map((cfg) => {
      const card = {
        cfg,
        state: 'idle',
        inflight: null,
        failures: 0,
        skip: 0,
        last: null,          // 마지막으로 받은 본문
        lastSeq: null,
        seqChangedAt: 0,     // 로컬 시각 — seq가 마지막으로 늘어난 때
        hist: [],            // scalar 추이(유효값만, seq 중복 없음)
        auto: !!(remembered[cfg.id] && remembered[cfg.id].auto),
        pick: (remembered[cfg.id] && remembered[cfg.id].pick) || null,  // thermal 선택 화소 [r, c]
      };
      const box = el('section', `spv-card spv-${cfg.kind}`);
      const head = el('div', 'spv-head');
      head.appendChild(el('span', 'spv-title', cfg.label));
      card.sim = el('span', 'spv-sim', '모의');
      card.sim.hidden = true;
      head.appendChild(card.sim);
      card.badge = el('span', 'spv-badge');
      head.appendChild(card.badge);
      box.appendChild(head);

      if (cfg.kind === 'thermal') buildThermal(card, box);
      else buildScalar(card, box);

      card.note = el('div', 'spv-note');
      box.appendChild(card.note);
      card.meta = el('div', 'spv-meta', '—');
      box.appendChild(card.meta);
      card.box = box;
      grid.appendChild(box);
      return card;
    });
    container.appendChild(root);

    // ── thermal ─────────────────────────────────────────────────────────────
    function buildThermal(card, box) {
      const stage = el('div', 'spv-stage');
      card.canvas = el('canvas', 'spv-canvas');
      card.canvas.width = 32;
      card.canvas.height = 24;
      card.canvas.setAttribute('role', 'img');
      card.canvas.setAttribute('aria-label', `${card.cfg.label} 히트맵 — 누르면 그 화소 온도를 표시`);
      card.marker = el('div', 'spv-marker');
      card.marker.hidden = true;
      stage.appendChild(card.canvas);
      stage.appendChild(card.marker);
      box.appendChild(stage);
      card.canvas.addEventListener('click', (ev) => {
        const g = card.last;
        if (!g || !g.rows || !g.cols) return;
        const rect = card.canvas.getBoundingClientRect();
        const c = Math.min(g.cols - 1, Math.max(0, Math.floor((ev.clientX - rect.left) / rect.width * g.cols)));
        const r = Math.min(g.rows - 1, Math.max(0, Math.floor((ev.clientY - rect.top) / rect.height * g.rows)));
        card.pick = [r, c];
        remember(card.cfg.id, 'pick', card.pick);
        paintThermal(card);
      });

      const legend = el('div', 'spv-legend');
      card.lo = el('span', 'spv-lo', '—');
      const bar = el('span', 'spv-bar');
      bar.style.background = RAMP_CSS;
      card.hi = el('span', 'spv-hi', '—');
      legend.append(card.lo, bar, card.hi);
      box.appendChild(legend);

      const stats = el('dl', 'spv-stats');
      card.stat = {};
      [['min', '최저'], ['mean', '평균'], ['max', '최고'], ['pick', '선택 화소']].forEach(([k, label]) => {
        const d = el('div');
        d.appendChild(el('dt', null, label));
        card.stat[k] = el('dd', null, '—');
        d.appendChild(card.stat[k]);
        stats.appendChild(d);
      });
      box.appendChild(stats);

      const seg = el('div', 'spv-seg');
      seg.setAttribute('role', 'group');
      seg.setAttribute('aria-label', `${card.cfg.label} 표시 범위`);
      const [lo, hi] = card.cfg.range;
      card.rangeBtns = [
        [false, el('button', 'spv-seg-btn', `고정 ${lo}–${hi}℃`)],
        [true, el('button', 'spv-seg-btn', '자동 범위')],
      ];
      card.rangeBtns.forEach(([auto, btn]) => {
        btn.type = 'button';
        btn.addEventListener('click', () => {
          card.auto = auto;
          remember(card.cfg.id, 'auto', auto);
          paintRangeButtons(card);
          paintThermal(card);
        });
        seg.appendChild(btn);
      });
      box.appendChild(seg);
      paintRangeButtons(card);
    }

    function paintRangeButtons(card) {
      card.rangeBtns.forEach(([auto, btn]) => {
        const on = auto === card.auto;
        btn.classList.toggle('is-on', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
    }

    function paintThermal(card) {
      const g = card.last;
      const valid = g && Array.isArray(g.deci) && g.rows > 0 && g.cols > 0 && g.deci.length === g.rows * g.cols;
      if (!valid) {
        const ctx = card.canvas.getContext('2d');
        ctx.clearRect(0, 0, card.canvas.width, card.canvas.height);
        ['min', 'mean', 'max', 'pick'].forEach((k) => { card.stat[k].textContent = '—'; });
        card.lo.textContent = '—';
        card.hi.textContent = '—';
        card.marker.hidden = true;
        return;
      }
      let lo;
      let hi;
      if (card.auto) {
        lo = g.min;
        hi = g.max - g.min < 1 ? g.min + 1 : g.max;  // 평탄한 장면에서 잡음이 과장되지 않게
      } else {
        [lo, hi] = card.cfg.range;
      }
      if (card.canvas.width !== g.cols || card.canvas.height !== g.rows) {
        card.canvas.width = g.cols;
        card.canvas.height = g.rows;
      }
      const ctx = card.canvas.getContext('2d');
      const img = ctx.createImageData(g.cols, g.rows);
      for (let i = 0; i < g.deci.length; i += 1) {
        const c = rampColor((g.deci[i] / 10 - lo) / (hi - lo));
        img.data[i * 4] = c[0];
        img.data[i * 4 + 1] = c[1];
        img.data[i * 4 + 2] = c[2];
        img.data[i * 4 + 3] = 255;
      }
      ctx.putImageData(img, 0, 0);
      card.lo.textContent = `${fmt(lo, 1)}℃`;
      card.hi.textContent = `${fmt(hi, 1)}℃`;
      card.stat.min.textContent = `${fmt(g.min, 1)}℃`;
      card.stat.mean.textContent = `${fmt(g.mean, 1)}℃`;
      card.stat.max.textContent = `${fmt(g.max, 1)}℃`;
      if (card.pick && card.pick[0] < g.rows && card.pick[1] < g.cols) {
        const [r, c] = card.pick;
        card.stat.pick.textContent = `${fmt(g.deci[r * g.cols + c] / 10, 1)}℃ (${r},${c})`;
        card.marker.hidden = false;
        card.marker.style.left = `${((c + 0.5) / g.cols * 100).toFixed(2)}%`;
        card.marker.style.top = `${((r + 0.5) / g.rows * 100).toFixed(2)}%`;
      } else {
        card.stat.pick.textContent = '화면을 눌러 선택';
        card.marker.hidden = true;
      }
    }

    // ── scalar ──────────────────────────────────────────────────────────────
    function buildScalar(card, box) {
      card.big = el('div', 'spv-big', '—');
      box.appendChild(card.big);
      if (card.cfg.note) box.appendChild(el('div', 'spv-caveat', card.cfg.note));
      const svgNs = 'http://www.w3.org/2000/svg';
      card.spark = document.createElementNS(svgNs, 'svg');
      card.spark.setAttribute('class', 'spv-spark');
      card.spark.setAttribute('viewBox', `0 0 ${SPARK_W} ${SPARK_H}`);
      card.spark.setAttribute('preserveAspectRatio', 'none');
      card.spark.setAttribute('aria-hidden', 'true');
      card.line = document.createElementNS(svgNs, 'polyline');
      card.line.setAttribute('fill', 'none');
      card.line.setAttribute('stroke', 'currentColor');
      card.line.setAttribute('stroke-width', '1.5');
      card.line.setAttribute('vector-effect', 'non-scaling-stroke');
      card.spark.appendChild(card.line);
      box.appendChild(card.spark);
      card.range = el('div', 'spv-range', '');
      box.appendChild(card.range);
      const details = el('details', 'spv-diag');
      details.appendChild(el('summary', null, '진단값'));
      card.diag = el('dl', 'spv-diag-body');
      details.appendChild(card.diag);
      box.appendChild(details);
    }

    function paintScalar(card) {
      const b = card.last;
      if (!b) {
        card.big.textContent = '—';
        card.big.dataset.kind = 'none';
        card.diag.innerHTML = '';
        return;
      }
      const v = b.value && typeof b.value === 'object' ? b.value : null;
      const raw = v ? v[card.cfg.field] : undefined;
      if (b.valid === true && typeof raw === 'number' && Number.isFinite(raw)) {
        card.big.textContent = `${raw.toFixed(2)} ${card.cfg.unit}`;  // 0 ℃도 숫자로 그린다
        card.big.dataset.kind = 'ok';
      } else if (b.valid === true) {
        card.big.textContent = '값 없음';  // valid인데 숫자가 없다(null) — 0으로 그리지 않는다
        card.big.dataset.kind = 'none';
      } else {
        card.big.textContent = isCommError(b.invalid_reason) ? '통신 오류' : '센서 오류';
        card.big.dataset.kind = 'bad';
      }
      const rows = [];
      if (v) Object.keys(v).forEach((k) => rows.push([k, String(v[k])]));
      rows.push(['valid', String(b.valid)]);
      if (b.invalid_reason) rows.push(['invalid_reason', b.invalid_reason]);
      rows.push(['seq', b.seq == null ? '—' : String(b.seq)]);
      if (b.host_utc) rows.push(['host_utc', b.host_utc]);
      card.diag.innerHTML = '';
      rows.forEach(([k, val]) => {
        const d = el('div');
        d.appendChild(el('dt', null, k));
        d.appendChild(el('dd', null, val));
        card.diag.appendChild(d);
      });
      const h = card.hist;
      if (h.length > 1) {
        let lo = Math.min(...h);
        let hi = Math.max(...h);
        if (hi - lo < 0.5) { const m = (hi + lo) / 2; lo = m - 0.25; hi = m + 0.25; }
        card.line.setAttribute('points', h.map((x, i) =>
          `${(i / (HIST_MAX - 1) * SPARK_W).toFixed(1)},${(SPARK_H - 4 - (x - lo) / (hi - lo) * (SPARK_H - 8)).toFixed(1)}`).join(' '));
        card.range.textContent = `최근 ${h.length}개 · ${Math.min(...h).toFixed(2)} ~ ${Math.max(...h).toFixed(2)} ${card.cfg.unit}`;
      } else {
        card.line.setAttribute('points', '');
        card.range.textContent = '';
      }
    }

    /** MAX31865 fault(센서·배선 문제)와 SPI 입출력 실패(통신 문제)를 구분한다. */
    function isCommError(reason) {
      return typeof reason === 'string' && /^(spi_error|io_error)/.test(reason);
    }

    // ── 상태 표시 ───────────────────────────────────────────────────────────
    function setState(card, state, note) {
      card.state = state;
      card.badge.textContent = STATE_TEXT[state] || state;
      card.badge.dataset.state = state;
      card.box.dataset.state = state;
      card.note.textContent = note || '';
      card.note.hidden = !note;
    }

    function paint(card) {
      if (card.cfg.kind === 'thermal') paintThermal(card);
      else paintScalar(card);
      const b = card.last;
      card.sim.hidden = !(b && (b.mock || b.simulated));
      card.meta.textContent = b
        ? [b.seq != null ? `seq ${b.seq}` : null, b.host_utc ? `Jetson ${timeText(b.host_utc)}` : null]
          .filter(Boolean).join(' · ') || '—'
        : '—';
    }

    function clearCard(card) {
      card.last = null;
      card.lastSeq = null;
      card.seqChangedAt = 0;
      card.hist = [];
      card.failures = 0;
      card.skip = 0;
      paint(card);
    }

    function failed(card, state, note) {
      card.failures += 1;
      card.skip = BACKOFF_STEPS[Math.min(card.failures, BACKOFF_STEPS.length - 1)] - 1;
      // 마지막 값은 남겨 두되(흐리게) 상태로 이유를 알린다 — 오래된 값을 현재 값처럼 보이지 않게
      setState(card, state, note);
    }

    // ── 요청 ────────────────────────────────────────────────────────────────
    async function fetchCard(card) {
      if (card.inflight) return;  // 앞 요청이 끝나기 전에는 보내지 않는다
      const ctrl = new AbortController();
      const gen = generation;
      const sid = sessionId;
      card.inflight = ctrl;
      const q = sid ? `?session_id=${encodeURIComponent(sid)}` : '';
      const url = `${apiBase}/${encodeURIComponent(card.cfg.sensorId)}/${encodeURIComponent(card.cfg.streamId)}${q}`;
      try {
        const res = await fetch(url, { cache: 'no-store', headers, signal: ctrl.signal });
        let body = null;
        try { body = await res.json(); } catch (_) { /* 본문 없음 */ }
        // 기다리는 사이 세션이 바뀌었거나 멈췄으면 늦은 응답으로 화면을 덮지 않는다
        if (destroyed || gen !== generation || !running() || ctrl.signal.aborted) return;
        if (res.ok && body) {
          if (sid && body.session_id && body.session_id !== sid) {
            failed(card, 'switching', '다른 세션의 값이라 버렸습니다.');
            return;
          }
          accept(card, body);
          return;
        }
        const detail = String((body && body.detail) || `HTTP ${res.status}`);
        if (res.status === 404) failed(card, 'noframe', detail);
        else if (res.status === 409) failed(card, 'switching', detail);
        else if (res.status === 502 || res.status === 503) failed(card, 'jetson_down', detail);
        else failed(card, 'server_down', detail);
      } catch (err) {
        if (err && err.name === 'AbortError') return;
        if (gen === generation) failed(card, 'server_down', '관리 서버에 닿지 못했습니다.');
      } finally {
        if (card.inflight === ctrl) card.inflight = null;
      }
    }

    function accept(card, body) {
      const now = Date.now();
      const seq = typeof body.seq === 'number' ? body.seq : null;
      const fresh = seq == null || seq !== card.lastSeq;
      card.failures = 0;
      card.skip = 0;
      if (fresh) {
        card.lastSeq = seq;
        card.seqChangedAt = now;
        card.last = body;
        if (card.cfg.kind === 'scalar' && body.valid === true && body.value
            && typeof body.value[card.cfg.field] === 'number' && Number.isFinite(body.value[card.cfg.field])) {
          card.hist.push(body.value[card.cfg.field]);
          if (card.hist.length > HIST_MAX) card.hist.shift();
        }
        paint(card);
      }
      if (now - card.seqChangedAt > card.cfg.staleAfterMs) {
        setState(card, 'stale', `마지막 값 — ${Math.round((now - card.seqChangedAt) / 1000)}초째 새 측정이 없습니다.`);
      } else if (card.cfg.kind === 'scalar' && body.valid !== true) {
        const commErr = isCommError(body.invalid_reason);
        setState(card, commErr ? 'comm' : 'invalid', body.invalid_reason || '무효 샘플(사유 없음)');
      } else {
        setState(card, 'live');
      }
    }

    function tick() {
      cards.forEach((card) => {
        if (card.skip > 0) { card.skip -= 1; return; }
        fetchCard(card);
      });
    }

    // ── 실행/중단 판정 ──────────────────────────────────────────────────────
    function running() {
      return !destroyed && active && rootVisible && !document.hidden && root.isConnected;
    }

    function sync() {
      const shouldRun = running();
      if (shouldRun && timer == null) {
        cards.forEach((c) => { c.skip = 0; if (!c.last) setState(c, 'loading', '불러오는 중…'); });
        tick();
        timer = setInterval(() => (running() ? tick() : sync()), intervalMs);
      } else if (!shouldRun && timer != null) {
        clearInterval(timer);
        timer = null;
        cards.forEach((c) => { if (c.inflight) c.inflight.abort(); });
      }
      if (!shouldRun && !destroyed) {
        cards.forEach((c) => {
          if (!active) { clearCard(c); setState(c, 'idle', inactiveText); }
          else if (c.state !== 'idle') setState(c, 'paused', '화면에 보일 때 다시 불러옵니다.');
        });
      }
    }

    const observers = [];
    if ('IntersectionObserver' in global) {
      const obs = new IntersectionObserver((entries) => {
        rootVisible = entries[entries.length - 1].isIntersecting;
        sync();
      });
      obs.observe(root);
      observers.push(obs);
    }
    const onVisibility = () => sync();
    document.addEventListener('visibilitychange', onVisibility);

    cards.forEach((c) => { paint(c); setState(c, 'idle', ''); });
    sync();

    return {
      /** 볼 세션을 알려 준다. 세션이 바뀌면 이전 값·추이를 지우고 늦은 응답을 버린다. */
      setActive(on, text, sid) {
        const next = !!on;
        const nextText = text || '';
        const nextSid = next ? (sid || null) : null;
        if (next === active && nextText === inactiveText && nextSid === sessionId) return;
        if (nextSid !== sessionId) {
          generation += 1;
          cards.forEach((c) => { if (c.inflight) c.inflight.abort(); clearCard(c); });
        }
        active = next;
        inactiveText = nextText;
        sessionId = nextSid;
        sync();
      },
      isPolling() { return timer != null; },
      snapshot() {
        return cards.map((c) => ({ id: c.cfg.id, state: c.state, seq: c.lastSeq, hist: c.hist.length }));
      },
      destroy() {
        destroyed = true;
        sync();
        observers.forEach((o) => o.disconnect());
        document.removeEventListener('visibilitychange', onVisibility);
        root.remove();
      },
    };
  }

  /** GET /api/sensor-preview/config 응답 → mount() 설정. */
  function fromServerConfig(cfg, extra) {
    const more = extra || {};
    const base = /^https?:/i.test(cfg.api_base) ? cfg.api_base : (more.origin || '') + cfg.api_base;
    return { apiBase: base, sensors: cfg.sensors, intervalMs: cfg.interval_ms, ...more };
  }

  global.SensorPreview = { mount, fromServerConfig };
})(window);
