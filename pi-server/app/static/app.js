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

const MARK_VIEW = {
  'mark.ingredient': '재료 투입',
  'mark.heat': '가열 변경',
  'mark.stir': '교반',
  'mark.note': '메모',
};

const UNKNOWN = '미확인';
let busy = false;
/** 카메라 미리보기 컴포넌트 핸들. 이 화면은 "볼 세션이 있는지"만 알려 준다. */
let cameraView = null;

// ── 공통 ───────────────────────────────────────────────────────────────────
async function api(path, options) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', 'X-Soup-Client': 'ui' },
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

function localDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('ko-KR', { hour12: false, month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit' });
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
  if (n == null) return UNKNOWN;
  const gb = n / 1e9;
  if (gb >= 1000) return `${(gb / 1000).toFixed(2)} TB`;
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  return `${(n / 1e6).toFixed(0)} MB`;
}

function elapsedText(startedIso) {
  if (!startedIso) return '—';
  const sec = Math.max(0, (Date.now() - new Date(startedIso).getTime()) / 1000);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  return h > 0 ? `${h}시간 ${m}분 ${s}초` : `${m}분 ${s}초`;
}

function escapeHtml(str) {
  return String(str == null ? '' : str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── 렌더 ───────────────────────────────────────────────────────────────────
function renderServerBadge(alive) {
  const badge = $('server-badge');
  badge.className = `conn-badge ${alive ? 'on' : 'off'}`;
  $('server-badge-text').textContent = alive ? '관리 서버 연결됨' : '관리 서버 연결 끊김';
}

function renderHost(host) {
  if (!host) {
    $('host-meta').textContent = '아직 측정 전입니다.';
    return;
  }
  // 못 읽은 값은 0이 아니라 "미확인" — 서버가 null로 준다.
  $('host-cpu').textContent = host.cpu_percent == null
    ? UNKNOWN : `${host.cpu_percent.toFixed(0)}%` + (host.load1 != null ? ` (load ${host.load1})` : '');
  $('host-mem').textContent = (host.mem_used_bytes == null || host.mem_total_bytes == null)
    ? UNKNOWN : `${bytesText(host.mem_used_bytes)} / ${bytesText(host.mem_total_bytes)}`;
  $('host-temp').textContent = host.temp_c == null ? UNKNOWN : `${host.temp_c.toFixed(1)}℃`;
  $('host-disk').textContent = host.disk_free_bytes == null
    ? UNKNOWN : `${bytesText(host.disk_free_bytes)} / ${bytesText(host.disk_total_bytes)}`;
  const up = host.uptime_sec == null ? UNKNOWN : `${Math.floor(host.uptime_sec / 3600)}시간`;
  $('host-meta').textContent = `측정 ${agoText(host.ts)} · 가동 ${up} · ${host.boot_id}`;
}

function renderSensors(report) {
  const sensors = (report && report.sensors) || [];
  $('sensor-list').innerHTML = sensors.length
    ? sensors.map((x) => {
        const st = x.stats;
        const stat = st
          ? `${st.frames_written.toLocaleString()}프레임` +
            (st.frames_dropped ? ` · 누락 ${st.frames_dropped}` : '') +
            (st.fps_measured != null ? ` · ${st.fps_measured}fps` : '')
          : `통계 ${UNKNOWN}`;
        return `
        <li>
          <span class="dot ${x.connected ? 'on' : ''}"></span>
          <span class="name">${escapeHtml(x.sensor_id)}</span>
          <span class="muted">${escapeHtml(x.kind)}</span>
          ${x.simulated ? '<span class="pill wait">모의</span>' : ''}
          <span class="detail">${escapeHtml(stat)}</span>
        </li>`;
      }).join('')
    : '<li class="muted">보고 없음</li>';

  const box = $('save-summary');
  const sum = report && report.last_session_summary;
  if (!sum) { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  box.innerHTML = `
    <div class="save-title">최근 저장 결과 <span class="mono">${escapeHtml(sum.session_id)}</span></div>
    <div class="save-body">
      파일 ${sum.files != null ? sum.files.toLocaleString() : UNKNOWN} ·
      ${bytesText(sum.bytes_written)} ·
      프레임 ${sum.frames_written != null ? sum.frames_written.toLocaleString() : UNKNOWN}
      ${sum.ok === false ? '<span class="pill bad">불완전</span>' : ''}
      ${sum.path ? `<div class="muted mono">${escapeHtml(sum.path)}</div>` : ''}
    </div>`;
}

function renderSession(s) {
  const box = $('session-box');
  const sess = s.active_session || s.last_session;
  if (!sess) {
    box.innerHTML = '<div class="session-none">진행 중인 세션 없음</div>';
    return;
  }
  const [label, cls] = SESSION_VIEW[sess.state] || [sess.state, ''];
  const frames = s.report && s.report.capture && s.report.capture.session_id === sess.session_id
    ? s.report.capture.frames_written : null;
  const active = !!s.active_session;
  box.innerHTML = `
    <div class="session-id">${escapeHtml(sess.session_id)}</div>
    <div class="session-name">${escapeHtml(sess.name)} <span class="pill ${cls}">${label}</span>
      ${sess.jetson_ack ? '' : '<span class="pill bad">Jetson 미확인</span>'}</div>
    <div class="session-meta">
      시작 ${localTime(sess.started_at)}${active ? ` · 경과 ${elapsedText(sess.started_at)}` : ''}
      ${frames != null ? ` · 프레임 ${frames.toLocaleString()}` : ''}
    </div>
    ${sess.ingredients ? `<div class="session-meta">재료: ${escapeHtml(sess.ingredients)}</div>` : ''}
    ${sess.conditions ? `<div class="session-meta">조건: ${escapeHtml(sess.conditions)}</div>` : ''}
    ${sess.note ? `<div class="session-meta">메모: ${escapeHtml(sess.note)}</div>` : ''}
    <div class="session-meta">
      저장 결과: ${sess.jetson_summary ? '확인됨' : `<b>${UNKNOWN}</b>`}
    </div>`;
}

/** 미리보기 컴포넌트에 **요청을 돌릴지 말지**만 알려 준다(그리기·폴링은 컴포넌트가 한다). */
function renderCameraPreviewGate(s) {
  if (!cameraView) return;
  const sess = s.active_session;
  const pv = sess && sess.config && sess.config.preview;
  const on = pv === true || !!(pv && pv.enabled === true);
  if (!sess) cameraView.setActive(false, '진행 중인 세션 없음');
  else if (!on) cameraView.setActive(false, '미리보기를 켜지 않고 시작한 세션입니다. 원본은 정상 저장 중입니다.');
  else cameraView.setActive(true);  // Jetson 끊김 표시는 컴포넌트가 응답 코드로 직접 한다
}

function renderEvents(rows) {
  $('event-rows').innerHTML = rows.length
    ? rows.map((e) => {
        const late = e.occurred_at && e.occurred_at !== e.ts;
        return `
        <tr>
          <td>${localTime(e.ts)}${late ? `<div class="muted">발생 ${localTime(e.occurred_at)}</div>` : ''}</td>
          <td><span class="lv ${e.level}">${e.level.toUpperCase()}</span>
              ${e.origin === 'manual' ? '<span class="pill wait">수동</span>' : ''}</td>
          <td>${escapeHtml(e.message)}<span class="code">${escapeHtml(e.code)}</span></td>
        </tr>`;
      }).join('')
    : '<tr><td colspan="3" class="muted">기록 없음</td></tr>';
}

/** 수동 사건은 전용 조회로 가져온다.
 *  최근 이벤트 목록에서 걸러내면 링크 상태 변화 같은 잦은 이벤트에 밀려 사라진다. */
async function loadMarks() {
  try {
    renderMarks(await api('/api/events?origin=manual&limit=10'));
  } catch (_) { /* 다음 주기에 다시 */ }
}

function renderMarks(marks) {
  $('mark-list').innerHTML = marks.length
    ? marks.slice(0, 8).map((e) => {
        const when = e.occurred_at || e.ts;
        const late = e.occurred_at && e.occurred_at !== e.ts;
        const kind = MARK_VIEW[e.code] || e.code;
        const text = (e.detail && e.detail.text) || '';
        return `<li><span class="mark-time">${localTime(when)}</span>
          <span class="mark-kind">${escapeHtml(kind)}</span>
          <span>${escapeHtml(text)}</span>
          ${late ? '<span class="pill wait">사후 입력</span>' : ''}</li>`;
      }).join('')
    : '<li class="muted">기록된 사건 없음</li>';
}

function render(s) {
  $('site-name').textContent = s.site_name;
  $('mock-badge').classList.toggle('hidden', !s.mock_mode);

  if (s.identity) {
    $('identity-badge').textContent = `${s.identity.project_id} / ${s.identity.device_id}`;
    $('foot-schema').textContent =
      `schema v${s.identity.schema_version} · ${s.identity.boot_id}`;
  }

  const view = STATUS_VIEW[s.jetson_status] || STATUS_VIEW.unknown;
  $('status-dot').className = `status-dot ${view.dot}`;
  $('status-label').textContent = `Jetson — ${view.label}`;
  $('status-reason').textContent = s.status_reason;

  $('link-state').textContent = LINK_VIEW[s.link.state] || s.link.state;
  $('link-target').textContent = s.link.mock ? '모의 장치 (mock)' : s.link.base_url;
  $('link-lastok').textContent = agoText(s.link.last_ok_at, s.link.age_sec);

  const p = s.power;
  $('power-state').textContent = p.supported
    ? `${p.state === 'on' ? '켜짐' : p.state === 'off' ? '꺼짐' : UNKNOWN}${p.simulated ? ' (모의)' : ''}`
    : '제어 미지원';
  $('power-note').textContent = p.note || '';
  const powerDisabled = !p.supported;
  $('btn-power-on').disabled = powerDisabled;
  $('btn-power-shutdown').disabled = powerDisabled;
  $('btn-power-force').disabled = powerDisabled;

  $('stale-warning').classList.toggle('hidden', !s.link.stale || s.link.state === 'unknown');

  renderSession(s);
  renderCameraPreviewGate(s);
  const canStart = s.jetson_status === 'online' && !s.active_session;
  $('btn-start').disabled = !canStart || busy;
  $('btn-stop').disabled = !s.active_session || busy;
  document.querySelectorAll('.btn-mark').forEach((b) => { b.disabled = busy; });

  const storage = s.report && s.report.storage;
  if (storage) {
    const usedPct = Math.min(100, Math.max(0,
      (1 - storage.free_bytes / Math.max(1, storage.total_bytes)) * 100));
    $('storage-box').innerHTML =
      `<div>${escapeHtml(storage.path)} — 여유 <b>${bytesText(storage.free_bytes)}</b>
        / ${bytesText(storage.total_bytes)}</div>
       <div class="bar"><i style="width:${usedPct.toFixed(1)}%"></i></div>`;
  } else {
    $('storage-box').innerHTML = `<span class="muted">보고 없음</span>`;
  }

  renderSensors(s.report);
  renderHost(s.host);
  renderEvents(s.recent_events || []);

  $('foot-time').textContent = `서버 시각 ${localTime(s.server_time)}`;
  $('mock-card').classList.toggle('hidden', !s.link.mock);
}

async function loadSessions() {
  try {
    const rows = await api('/api/sessions?limit=12');
    $('session-rows').innerHTML = rows.length
      ? rows.map((r) => {
          const [label, cls] = SESSION_VIEW[r.state] || [r.state, ''];
          const saved = r.jetson_summary
            ? `${r.jetson_summary.files != null ? r.jetson_summary.files.toLocaleString() + '개' : ''} ${bytesText(r.jetson_summary.bytes_written)}`
            : `<span class="muted">${UNKNOWN}</span>`;
          return `<tr>
            <td>${localDateTime(r.started_at)}</td>
            <td>${escapeHtml(r.name)}<div class="muted mono">${escapeHtml(r.session_id)}</div></td>
            <td><span class="pill ${cls}">${label}</span></td>
            <td>${saved}</td>
            <td><a href="/api/sessions/${encodeURIComponent(r.session_id)}/export?format=json" download>JSON</a>
              · <a href="/api/sessions/${encodeURIComponent(r.session_id)}/export?format=csv" download>CSV</a></td>
          </tr>`;
        }).join('')
      : '<tr><td colspan="5" class="muted">실험 기록 없음</td></tr>';
  } catch (_) { /* 서버가 잠깐 안 뜬 경우 — 다음 주기에 다시 */ }
}

// ── 폴링 ───────────────────────────────────────────────────────────────────
let sessionTick = 0;
async function poll() {
  try {
    const s = await api('/api/status');
    renderServerBadge(true);
    render(s);
    if (sessionTick++ % 4 === 0) { loadSessions(); loadMarks(); }
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

/** datetime-local 입력(로컬 시각) → UTC ISO8601. 비어 있으면 null. */
function markTimeToUtc() {
  const raw = $('mark-time').value;
  if (!raw) return null;
  const d = new Date(raw);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

function bind() {
  $('btn-refresh').addEventListener('click', async () => {
    render(await api('/api/status/refresh', { method: 'POST' }));
  });

  $('btn-start').addEventListener('click', () => withBusy(async () => {
    const body = {
      name: $('in-name').value || '실험',
      note: $('in-note').value || null,
      ingredients: $('in-ingredients').value || null,
      conditions: $('in-conditions').value || null,
    };
    try { localStorage.setItem('soup.previewOn', $('in-preview').checked ? '1' : '0'); } catch (_) { /* 무시 */ }
    if ($('in-preview').checked) {
      // 저장된 실험 설정에 미리보기만 얹는다 — 수집 대상 센서·fps·해상도는 건드리지 않는다.
      // 이 값도 시작 시점 설정 스냅샷에 함께 박제된다.
      // 저장된 preview의 다른 값(예: depth_max_mm — 깊이 의사색 범위)은 유지한다.
      const saved = await api('/api/config');
      body.config = { ...saved, preview: { ...(saved.preview || {}), enabled: true, max_fps: 2 } };
    }
    const sess = await api('/api/capture/start', { method: 'POST', body: JSON.stringify(body) });
    loadSessions();
    return `촬영 시작됨: ${sess.session_id}`;
  }, $('capture-msg')));

  $('btn-stop').addEventListener('click', () => withBusy(async () => {
    const sess = await api('/api/capture/stop', { method: 'POST', body: JSON.stringify({}) });
    loadSessions();
    return `촬영 중지됨: ${sess.session_id} (${sess.state})`;
  }, $('capture-msg')));

  document.querySelectorAll('.btn-mark').forEach((btn) => {
    btn.addEventListener('click', () => withBusy(async () => {
      const body = {
        kind: btn.dataset.kind,
        text: $('mark-text').value || null,
        occurred_at: markTimeToUtc(),
      };
      const ev = await api('/api/marks', { method: 'POST', body: JSON.stringify(body) });
      $('mark-text').value = '';
      $('mark-time').value = '';
      await loadMarks();
      return `기록됨: ${ev.message}`;
    }, $('mark-msg')));
  });

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
    return '설정 저장됨 (진행 중 실험에는 영향 없음)';
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

async function mountCameraPreview() {
  try {
    const cfg = await api('/api/preview/config');
    if (cfg.config_error) console.warn(cfg.config_error);
    cameraView = window.CameraPreview.mount($('camera-preview'),
      window.CameraPreview.fromServerConfig(cfg, { headers: { 'X-Soup-Client': 'ui' } }));
    cameraView.setActive(false, '상태 확인 중…');
  } catch (err) {
    $('camera-preview').textContent = `미리보기 설정을 읽지 못했습니다: ${err.message}`;
  }
}

bind();
try { $('in-preview').checked = localStorage.getItem('soup.previewOn') !== '0'; } catch (_) { /* 기본 켜짐 */ }
mountCameraPreview();
loadConfig();
loadSessions();
loadMarks();
poll();
setInterval(poll, POLL_MS);
