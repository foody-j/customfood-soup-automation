'use strict';
/* 카메라 미리보기 컴포넌트 — 기존 화면에 끼워 넣는 **독립 모듈**.
 *
 * 하는 일: Jetson 저속 JPEG 미리보기(관리 서버가 중계하는 GET {apiBase}/{sensor_id}/{stream_id})를
 * 받아 카메라별 패널에 그리고, 스트림 전환(Gemini 2: Color/Depth/IR)·주기 갱신·연결 상태 표시를 맡는다.
 * 하지 않는 일: 촬영·원본 저장·Jetson 설정 변경. 스트림 전환은 **무엇을 볼지**만 바꾼다 —
 * Jetson에는 조회(GET) 말고 아무것도 보내지 않는다.
 *
 * 이 파일은 바깥 화면(app.js, DOM id, 전역 상태)을 전혀 모른다. 카메라 목록과 API 주소는
 * 전부 설정으로 받는다. 스타일은 camera-preview.css(접두사 `.cpv-`).
 *
 *   const view = CameraPreview.mount(document.getElementById('slot'), {
 *     apiBase: '/api/preview',            // 필수. 다른 출처면 'http://pi:8100/api/preview'
 *     cameras: [                          // 필수
 *       { id: 'gmsl2_1', label: 'GMSL2 ①', sensor_id: 'cam_rgb_0', streams: [{ id: 'rgb', label: 'RGB' }] },
 *       { id: 'gemini2', label: 'Gemini 2', sensor_id: 'cam_depth_0',
 *         streams: [{ id: 'color', label: 'Color' }, { id: 'depth', label: 'Depth' }, { id: 'ir', label: 'IR' }] },
 *     ],
 *     intervalMs: 1000,                   // 선택(기본 1000, 하한 250)
 *     staleAfterMs: 5000,                 // 선택 — 이보다 오래 새 프레임이 없으면 "지연"
 *     headers: { 'X-Soup-Client': 'ui' }, // 선택 — 요청에 실을 헤더
 *     storageKey: 'soup.cameraPreview',   // 선택 — 고른 스트림 기억(null이면 기억 안 함)
 *   });
 *   view.setActive(false, '진행 중인 세션 없음');  // 선택 — 볼 것이 없을 때 요청 자체를 멈춘다
 *   view.destroy();                                // 화면에서 떼어낼 때
 *   (GET /api/preview/config 응답을 CameraPreview.fromServerConfig()에 넣으면 위 설정이 된다.)
 *
 * 요청이 멈추는 조건(하나라도 해당하면 타이머 자체가 돌지 않는다):
 *   ① 컴포넌트가 화면 밖(스크롤로 가려짐·display:none·DOM에서 제거)  ② 브라우저 탭이 숨겨짐
 *   ③ setActive(false)  ④ destroy(). 패널 단위로도 화면 밖에 있는 카메라는 부르지 않는다.
 */
(function (global) {
  const STATE_TEXT = {
    idle: '대기',
    loading: '연결 중',
    live: '수신 중',
    stale: '지연',
    noframe: '프레임 없음',
    jetson_down: 'Jetson 무응답',
    server_down: '서버 무응답',
    paused: '일시 중지',
  };
  // 실패가 이어질 때 주기를 늘린다(서버·Jetson이 죽었을 때 두드리지 않도록). 성공하면 즉시 원복.
  const BACKOFF_STEPS = [1, 1, 2, 4, 8];

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function normalizeCameras(cameras) {
    if (!Array.isArray(cameras) || !cameras.length) throw new Error('CameraPreview: cameras가 비어 있음');
    return cameras.map((cam, i) => {
      const sensorId = cam && cam.sensor_id;
      if (!sensorId) throw new Error(`CameraPreview: cameras[${i}].sensor_id 없음`);
      const streams = (cam.streams || []).map((st) =>
        (typeof st === 'string' ? { id: st, label: st } : { id: st.id, label: st.label || st.id }));
      if (!streams.length || streams.some((st) => !st.id)) {
        throw new Error(`CameraPreview: cameras[${i}].streams가 비었거나 id 없음`);
      }
      return { id: String(cam.id || sensorId), label: String(cam.label || sensorId), sensorId, streams };
    });
  }

  function mount(container, options) {
    if (!container) throw new Error('CameraPreview: container 없음');
    const opts = options || {};
    if (!opts.apiBase) throw new Error('CameraPreview: apiBase 없음');
    const apiBase = String(opts.apiBase).replace(/\/+$/, '');
    const intervalMs = Math.max(250, Number(opts.intervalMs) || 1000);
    const staleAfterMs = Math.max(intervalMs * 2, Number(opts.staleAfterMs) || 5000);
    const headers = opts.headers || {};
    const storageKey = opts.storageKey === undefined ? 'soup.cameraPreview' : opts.storageKey;

    let remembered = {};
    if (storageKey) {
      try { remembered = JSON.parse(localStorage.getItem(storageKey) || '{}') || {}; } catch (_) { remembered = {}; }
    }

    let destroyed = false;
    let active = true;
    let inactiveText = '';
    let rootVisible = !('IntersectionObserver' in global);  // 관찰 불가 환경이면 보이는 것으로 본다
    let timer = null;

    const root = el('div', 'cpv');
    const grid = el('div', 'cpv-grid');
    root.appendChild(grid);

    const panels = normalizeCameras(opts.cameras).map((cam) => {
      const panel = {
        cam,
        stream: cam.streams.find((st) => st.id === remembered[cam.id]) || cam.streams[0],
        visible: rootVisible,
        state: 'idle',
        inflight: null,      // AbortController
        lastOkAt: 0,
        failures: 0,
        skip: 0,             // 백오프로 건너뛸 남은 주기 수
        objectUrl: null,
        buttons: [],
      };
      const box = el('figure', 'cpv-panel');
      const head = el('div', 'cpv-head');
      head.appendChild(el('span', 'cpv-title', cam.label));
      panel.badge = el('span', 'cpv-badge');
      head.appendChild(panel.badge);
      box.appendChild(head);

      const stage = el('div', 'cpv-stage');
      panel.img = el('img', 'cpv-img');
      panel.img.alt = `${cam.label} 미리보기`;
      panel.img.hidden = true;
      panel.note = el('div', 'cpv-note');
      stage.appendChild(panel.img);
      stage.appendChild(panel.note);
      box.appendChild(stage);
      panel.stage = stage;

      const foot = el('figcaption', 'cpv-foot');
      if (cam.streams.length > 1) {
        const seg = el('div', 'cpv-seg');
        seg.setAttribute('role', 'group');
        seg.setAttribute('aria-label', `${cam.label} 스트림 선택`);
        cam.streams.forEach((st) => {
          const btn = el('button', 'cpv-seg-btn', st.label);
          btn.type = 'button';
          btn.addEventListener('click', () => selectStream(panel, st));
          panel.buttons.push([st, btn]);
          seg.appendChild(btn);
        });
        foot.appendChild(seg);
      }
      panel.meta = el('span', 'cpv-meta', '—');
      foot.appendChild(panel.meta);
      box.appendChild(foot);

      panel.box = box;
      grid.appendChild(box);
      return panel;
    });

    container.appendChild(root);

    // ── 상태 표시 ───────────────────────────────────────────────────────────
    function setState(panel, state, note) {
      panel.state = state;
      panel.badge.textContent = STATE_TEXT[state] || state;
      panel.badge.dataset.state = state;
      panel.box.dataset.state = state;
      // 끊겼을 때는 마지막 그림을 흐리게 남기고 그 위에 사유를 적는다(무엇을 보고 있었는지 알 수 있게)
      const showImage = !!panel.objectUrl && state !== 'idle' && state !== 'noframe' && state !== 'loading';
      panel.img.hidden = !showImage;
      panel.note.hidden = showImage && state === 'live';
      panel.note.textContent = note || '';
    }

    function paintButtons(panel) {
      panel.buttons.forEach(([st, btn]) => {
        const on = st.id === panel.stream.id;
        btn.classList.toggle('is-on', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
    }

    function dropImage(panel) {
      if (panel.objectUrl) URL.revokeObjectURL(panel.objectUrl);
      panel.objectUrl = null;
      panel.img.removeAttribute('src');
      panel.lastOkAt = 0;
    }

    function selectStream(panel, st) {
      if (panel.stream.id === st.id) return;
      panel.stream = st;
      if (panel.inflight) panel.inflight.abort();  // 이전 스트림 응답이 새 선택을 덮지 않게
      dropImage(panel);
      panel.failures = 0;
      panel.skip = 0;
      paintButtons(panel);
      panel.meta.textContent = '—';
      setState(panel, running() ? 'loading' : panel.state, running() ? '불러오는 중…' : panel.note.textContent);
      if (storageKey) {
        remembered[panel.cam.id] = st.id;
        try { localStorage.setItem(storageKey, JSON.stringify(remembered)); } catch (_) { /* 저장 불가 — 무시 */ }
      }
      if (running()) fetchPanel(panel);
    }

    // ── 요청 ────────────────────────────────────────────────────────────────
    function timeText(iso) {
      const d = new Date(iso);
      return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString('ko-KR', { hour12: false });
    }

    function failed(panel, state, note) {
      panel.failures += 1;
      panel.skip = BACKOFF_STEPS[Math.min(panel.failures, BACKOFF_STEPS.length - 1)] - 1;
      // 방금까지 그림이 나왔으면 잠깐의 실패로 화면을 지우지 않는다 — "지연"으로만 표시
      if (panel.lastOkAt && Date.now() - panel.lastOkAt < staleAfterMs) return;
      if (state === 'noframe') dropImage(panel);
      setState(panel, state, note);
    }

    async function fetchPanel(panel) {
      if (panel.inflight) return;  // 앞 요청이 아직 안 끝났으면 겹쳐 보내지 않는다
      const ctrl = new AbortController();
      const stream = panel.stream;
      panel.inflight = ctrl;
      const url = `${apiBase}/${encodeURIComponent(panel.cam.sensorId)}/${encodeURIComponent(stream.id)}`;
      try {
        const res = await fetch(url, { cache: 'no-store', headers, signal: ctrl.signal });
        if (destroyed || stream !== panel.stream) return;
        if (res.ok) {
          const blob = await res.blob();
          if (!running() || stream !== panel.stream || ctrl.signal.aborted) return;
          const old = panel.objectUrl;
          panel.objectUrl = URL.createObjectURL(blob);
          panel.img.src = panel.objectUrl;
          if (old) URL.revokeObjectURL(old);
          panel.lastOkAt = Date.now();
          panel.failures = 0;
          panel.skip = 0;
          const seq = res.headers.get('x-preview-sequence');
          const at = res.headers.get('x-preview-host-utc');
          panel.meta.textContent = [
            panel.cam.streams.length > 1 ? null : stream.label,
            seq ? `#${seq}` : null,
            at ? `Jetson ${timeText(at)}` : null,
          ].filter(Boolean).join(' · ') || '—';
          setState(panel, 'live');
          return;
        }
        let detail = `HTTP ${res.status}`;
        try { detail = (await res.json()).detail || detail; } catch (_) { /* 본문 없음 */ }
        // 본문을 읽는 사이 멈췄거나(세션 종료·화면 밖) 선택이 바뀌었으면 늦게 온 실패로 표시를 덮지 않는다
        if (!running() || stream !== panel.stream) return;
        if (res.status === 404) failed(panel, 'noframe', String(detail));
        else if (res.status === 502 || res.status === 503) failed(panel, 'jetson_down', String(detail));
        else failed(panel, 'server_down', String(detail));
      } catch (err) {
        if (err && err.name === 'AbortError') return;
        failed(panel, 'server_down', '관리 서버에 닿지 못했습니다.');
      } finally {
        if (panel.inflight === ctrl) panel.inflight = null;
      }
    }

    function tick() {
      const now = Date.now();
      panels.forEach((panel) => {
        if (panel.state === 'live' && now - panel.lastOkAt > staleAfterMs) {
          setState(panel, 'stale', '새 프레임이 들어오지 않습니다.');
        }
        if (!panel.visible) return;
        if (panel.skip > 0) { panel.skip -= 1; return; }
        fetchPanel(panel);
      });
    }

    // ── 실행/중단 판정 ──────────────────────────────────────────────────────
    function running() {
      return !destroyed && active && rootVisible && !document.hidden && root.isConnected;
    }

    function sync() {
      const shouldRun = running();
      if (shouldRun && timer == null) {
        panels.forEach((p) => { p.skip = 0; if (!p.objectUrl) setState(p, 'loading', '불러오는 중…'); });
        tick();
        timer = setInterval(() => (running() ? tick() : sync()), intervalMs);
      } else if (!shouldRun && timer != null) {
        clearInterval(timer);
        timer = null;
        panels.forEach((p) => { if (p.inflight) p.inflight.abort(); });
      }
      if (!shouldRun && !destroyed) {
        panels.forEach((p) => {
          if (!active) { dropImage(p); p.meta.textContent = '—'; setState(p, 'idle', inactiveText); }
          else if (p.state !== 'idle') setState(p, 'paused', p.objectUrl ? '' : '화면에 보일 때 다시 불러옵니다.');
        });
      }
    }

    const observers = [];
    if ('IntersectionObserver' in global) {
      const rootObs = new IntersectionObserver((entries) => {
        rootVisible = entries[entries.length - 1].isIntersecting;
        sync();
      });
      rootObs.observe(root);
      const panelObs = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          const panel = panels.find((p) => p.box === entry.target);
          if (panel) panel.visible = entry.isIntersecting;
        });
      });
      panels.forEach((p) => panelObs.observe(p.box));
      observers.push(rootObs, panelObs);
    }
    const onVisibility = () => sync();
    document.addEventListener('visibilitychange', onVisibility);

    panels.forEach((p) => { paintButtons(p); setState(p, 'idle', ''); });
    sync();

    return {
      /** 볼 것이 없을 때(세션 없음 등) 요청을 멈춘다. text는 패널에 표시할 안내. */
      setActive(on, text) {
        const next = !!on;
        const nextText = text || '';
        if (next === active && nextText === inactiveText) return;
        active = next;
        inactiveText = nextText;
        sync();
      },
      /** 지금 주기 요청이 돌고 있는지(시험·디버깅용). */
      isPolling() { return timer != null; },
      snapshot() {
        return panels.map((p) => ({ id: p.cam.id, sensor_id: p.cam.sensorId, stream: p.stream.id, state: p.state }));
      },
      destroy() {
        destroyed = true;
        sync();
        observers.forEach((o) => o.disconnect());
        document.removeEventListener('visibilitychange', onVisibility);
        panels.forEach(dropImage);
        root.remove();
      },
    };
  }

  /** GET /api/preview/config 응답 → mount() 설정. origin을 주면 다른 출처의 관리 서버를 가리킨다. */
  function fromServerConfig(cfg, extra) {
    const more = extra || {};
    const base = /^https?:/i.test(cfg.api_base) ? cfg.api_base : (more.origin || '') + cfg.api_base;
    return { apiBase: base, cameras: cfg.cameras, intervalMs: cfg.interval_ms, ...more };
  }

  global.CameraPreview = { mount, fromServerConfig };
})(window);
