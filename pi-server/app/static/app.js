'use strict';
/* 관리 화면.
 * 서버(/api/status)를 주기적으로 읽어 그리기만 한다. 판정·기록은 전부 서버가 한다
 * — 브라우저를 닫아도 감시가 계속되려면 상태가 화면에 있으면 안 되기 때문. */

const POLL_MS = 1500;
const $ = (id) => document.getElementById(id);

const STATUS_VIEW = {
  online:       { label: '정상',              dot: 'ok' },
  booting:      { label: '부팅 중',           dot: 'cool' },
  service_down: { label: '수집 서비스 중단',  dot: 'warn' },
  link_lost:    { label: '무응답 (원인 미상)', dot: 'crit' },
  powered_off:  { label: '전원 꺼짐',         dot: 'crit' },
  unknown:      { label: '확인 중',           dot: '' },
};

const LINK_VIEW = {
  online: '수집 서비스 응답',
  service_down: '호스트만 응답 (서비스 무응답)',
  unreachable: '호스트·서비스 모두 무응답',
  unknown: '미확인',
};

const SESSION_VIEW = {
  starting: ['시작 중', 'wait'],
  running:  ['진행 중', 'run'],
  stopping: ['중지 중', 'wait'],
  stopped:  ['종료', ''],
  failed:   ['실패', 'bad'],
  unknown:  ['확인 불가', 'bad'],
  idle:     ['대기', ''],
};

let lastStatus = null;
let busy = false;

// ── 공통 ───────────────────────────────────────────────────────────────────
async function api(path, options) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  let body = null;
  try { body = await res.json(); } catch (_) { /* 본문 없음 */ }
  if (!res.ok) {
    const detail = (body && body.detail) || `HTTP ${res.status}`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return body;
}

function showMsg(el, text, kind) {
  el.textContent = text;
  el.className = `msg${kind ? ' ' + kind : ''}`;
  el.classList.remove('hidden');
}

function localTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString('ko-KR', { hour12: false });
}

function agoText(iso, ageSec) {
  if (!iso) return '없음';
  const sec = ageSec != null ? ageSec : (Date.now() - new Date(iso).getTime()) / 1000;
  if (sec < 2) return `${localTime(iso)} (방금)`;
  if (sec < 60) return `${localTime(iso)} (${Math.round(sec)}초 전)`;
  if (sec < 3600) return `${localTime(iso)} (${Math.round(sec / 60)}분 전)`;
  return `${localTime(iso)} (${Math.round(sec / 3600)}시간 전)`;
}

function bytesText(n) {
  if (n == null) return '—';
  const gb = n / 1e9;
  return gb >= 1000 ? `${(gb / 1000).toFixed(2)} TB` : `${gb.toFixed(1)} GB`;
}

function elapsedText(startedIso) {
  if (!startedIso) return '—';
  const sec = Math.max(0, (Date.now() - new Date(startedIso).getTime()) / 1000);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  return h > 0 ? `${h}시간 ${m}분 ${s}초` : `${m}분 ${s}초`;
}

// ── 렌더 ───────────────────────────────────────────────────────────────────
function renderServerBadge(alive) {
  const badge = $('server-badge');
  badge.className = `conn-badge ${alive ? 'on' : 'off'}`;
  $('server-badge-text').textContent = alive ? '관리 서버 연결됨' : '관리 서버 연결 끊김';
}

function render(s) {
  lastStatus = s;
  $('site-name').textContent = s.site_name;
  $('mock-badge').classList.toggle('hidden', !s.mock_mode);

  const view = STATUS_VIEW[s.jetson_status] || STATUS_VIEW.unknown;
  $('status-dot').className = `status-dot ${view.dot}`;
  $('status-label').textContent = `Jetson — ${view.label}`;
  $('status-reason').textContent = s.status_reason;

  $('link-state').textContent = LINK_VIEW[s.link.state] || s.link.state;
  $('link-target').textContent = s.link.mock ? '모의 장치 (mock)' : s.link.base_url;
  $('link-lastok').textContent = agoText(s.link.last_ok_at, s.link.age_sec);

  const p = s.power;
  $('power-state').textContent = p.supported
    ? `${p.state === 'on' ? '켜짐' : p.state === 'off' ? '꺼짐' : '알 수 없음'}${p.simulated ? ' (모의)' : ''}`
    : '제어 미지원';
  $('power-note').textContent = p.note || '';
  const powerDisabled = !p.supported;
  $('btn-power-on').disabled = powerDisabled;
  $('btn-power-shutdown').disabled = powerDisabled;
  $('btn-power-force').disabled = powerDisabled;

  $('stale-warning').classList.toggle('hidden', !s.link.stale || s.link.state === 'unknown');

  // 세션
  const box = $('session-box');
  const sess = s.active_session || s.last_session;
  if (!sess) {
    box.innerHTML = '<div class="session-none">진행 중인 세션 없음</div>';
  } else {
    const [label, cls] = SESSION_VIEW[sess.state] || [sess.state, ''];
    const frames = s.report && s.report.capture && s.report.capture.session_id === sess.session_id
      ? s.report.capture.frames_written : null;
    const active = !!s.active_session;
    box.innerHTML = `
      <div class="session-id">${sess.session_id}</div>
      <div class="session-name">${escapeHtml(sess.name)} <span class="pill ${cls}">${label}</span>
        ${sess.jetson_ack ? '' : '<span class="pill bad">Jetson 미확인</span>'}</div>
      <div class="session-meta">
        시작 ${localTime(sess.started_at)}${active ? ` · 경과 ${elapsedText(sess.started_at)}` : ''}
        ${frames != null ? ` · 프레임 ${frames.toLocaleString()}` : ''}
        ${sess.note ? ` · ${escapeHtml(sess.note)}` : ''}
      </div>`;
  }

  const canStart = s.jetson_status === 'online' && !s.active_session;
  $('btn-start').disabled = !canStart || busy;
  $('btn-stop').disabled = !s.active_session || busy;

  // 저장소·센서
  const storage = s.report && s.report.storage;
  if (storage) {
    const usedPct = Math.min(100, Math.max(0,
      (1 - storage.free_bytes / Math.max(1, storage.total_bytes)) * 100));
    $('storage-box').innerHTML =
      `<div>${escapeHtml(storage.path)} — 여유 <b>${bytesText(storage.free_bytes)}</b>
        / ${bytesText(storage.total_bytes)}</div>
       <div class="bar"><i style="width:${usedPct.toFixed(1)}%"></i></div>`;
  } else {
    $('storage-box').innerHTML = '<span class="muted">보고 없음</span>';
  }

  const sensors = (s.report && s.report.sensors) || [];
  $('sensor-list').innerHTML = sensors.length
    ? sensors.map((x) => `
        <li>
          <span class="dot ${x.connected ? 'on' : ''}"></span>
          <span class="name">${escapeHtml(x.sensor_id)}</span>
          <span class="muted">${escapeHtml(x.kind)}</span>
          ${x.simulated ? '<span class="pill wait">모의</span>' : ''}
          <span class="detail">${escapeHtml(x.detail || '')}</span>
        </li>`).join('')
    : '<li class="muted">보고 없음</li>';

  // 이벤트
  const rows = s.recent_events || [];
  $('event-rows').innerHTML = rows.length
    ? rows.map((e) => `
        <tr>
          <td>${localTime(e.ts)}</td>
          <td><span class="lv ${e.level}">${e.level.toUpperCase()}</span></td>
          <td>${escapeHtml(e.message)}<span class="code">${escapeHtml(e.code)}</span></td>
        </tr>`).join('')
    : '<tr><td colspan="3" class="muted">기록 없음</td></tr>';

  $('foot-time').textContent = `서버 시각 ${localTime(s.server_time)}`;
  $('mock-card').classList.toggle('hidden', !s.link.mock);
}

function escapeHtml(str) {
  return String(str == null ? '' : str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── 폴링 ───────────────────────────────────────────────────────────────────
async function poll() {
  try {
    const s = await api('/api/status');
    renderServerBadge(true);
    render(s);
  } catch (err) {
    renderServerBadge(false);
  }
}

// ── 조작 ───────────────────────────────────────────────────────────────────
async function withBusy(fn, msgEl) {
  busy = true;
  try {
    const text = await fn();
    if (text) showMsg(msgEl, text, 'ok');
  } catch (err) {
    showMsg(msgEl, err.message, 'err');
  } finally {
    busy = false;
    await poll();
  }
}

function bind() {
  $('btn-refresh').addEventListener('click', async () => {
    const s = await api('/api/status/refresh', { method: 'POST' });
    render(s);
  });

  $('btn-start').addEventListener('click', () => withBusy(async () => {
    const body = { name: $('in-name').value || '실험', note: $('in-note').value || null };
    const sess = await api('/api/capture/start', { method: 'POST', body: JSON.stringify(body) });
    return `촬영 시작됨: ${sess.session_id}`;
  }, $('capture-msg')));

  $('btn-stop').addEventListener('click', () => withBusy(async () => {
    const sess = await api('/api/capture/stop', { method: 'POST', body: JSON.stringify({}) });
    return `촬영 중지됨: ${sess.session_id} (${sess.state})`;
  }, $('capture-msg')));

  $('btn-save-config').addEventListener('click', () => withBusy(async () => {
    const body = {
      sensors: $('cfg-sensors').value.split(',').map((x) => x.trim()).filter(Boolean),
      fps: $('cfg-fps').value ? Number($('cfg-fps').value) : null,
      resolution: $('cfg-resolution').value || null,
      exposure: $('cfg-exposure').value || null,
      lighting: $('cfg-lighting').value || null,
      note: $('cfg-note').value || null,
    };
    await api('/api/config', { method: 'PUT', body: JSON.stringify(body) });
    return '설정 저장됨';
  }, $('config-msg')));

  const power = (path, confirmText) => () => withBusy(async () => {
    if (confirmText && !window.confirm(confirmText)) return null;
    const res = await api(path, { method: 'POST' });
    return res.message;
  }, $('power-msg'));

  $('btn-power-on').addEventListener('click', power('/api/power/on'));
  $('btn-power-shutdown').addEventListener('click',
    power('/api/power/shutdown', 'Jetson을 정상 종료합니다. 진행 중 촬영은 중지됩니다. 계속할까요?'));
  $('btn-power-force').addEventListener('click',
    power('/api/power/force-off', '강제로 전원을 끊습니다. 저장 중인 데이터가 손실될 수 있습니다. 계속할까요?'));

  const mock = (path, body) => () => withBusy(async () => {
    const res = await api(path, { method: 'POST', body: JSON.stringify(body) });
    return JSON.stringify(res);
  }, $('capture-msg'));

  $('btn-mock-on').addEventListener('click', mock('/api/mock/jetson/power', { on: true }));
  $('btn-mock-off').addEventListener('click', mock('/api/mock/jetson/power', { on: false }));
  $('btn-mock-cut').addEventListener('click', mock('/api/mock/jetson/link', { cut: true }));
  $('btn-mock-join').addEventListener('click', mock('/api/mock/jetson/link', { cut: false }));
}

async function loadConfig() {
  try {
    const cfg = await api('/api/config');
    if (Array.isArray(cfg.sensors)) $('cfg-sensors').value = cfg.sensors.join(', ');
    if (cfg.fps != null) $('cfg-fps').value = cfg.fps;
    if (cfg.resolution) $('cfg-resolution').value = cfg.resolution;
    if (cfg.exposure) $('cfg-exposure').value = cfg.exposure;
    if (cfg.lighting) $('cfg-lighting').value = cfg.lighting;
    if (cfg.note) $('cfg-note').value = cfg.note;
  } catch (_) { /* 설정 없음 — 기본값 유지 */ }
}

bind();
loadConfig();
poll();
setInterval(poll, POLL_MS);
