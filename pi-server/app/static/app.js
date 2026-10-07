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
  'mark.boil_start': '끓음 시작',
  'mark.taste': '맛보기',
  'mark.done_start': '완료 시작',
  'mark.done_end': '완료 끝',
  'mark.overcooked': '과조리',
  'mark.lid': '뚜껑',
};
/** 사건 값 표시 이름(서버 MARK_VALUES와 같음). taste 값은 shared/schema.json doneness 문자열 */
const MARK_VALUE_VIEW = {
  undercooked: '미완', done: '완료', overcooked: '과조리', on: '덮음', off: '엶',
};
/** 데이터셋 세션에서 종료 전에 확인할 정답 사건(라벨의 기준점) */
const DATASET_KEY_MARKS = { 'mark.done_start': '완료 시작', 'mark.overcooked': '과조리' };
const PARAM_FIELDS = ['heat_level', 'water_added_ml', 'lid_initial', 'start_temp_c',
  'probe_depth_mm', 'product_weight_g', 'taster'];
const PARAM_TEXT = new Set(['lid_initial', 'taster']);

const UNKNOWN = '미확인';
let busy = false;
/** 카메라 미리보기 컴포넌트 핸들. 이 화면은 "볼 세션이 있는지"만 알려 준다. */
let cameraView = null;
/** 열화상·PT100 표시 컴포넌트 핸들. 볼 세션 ID만 알려 준다. */
let sensorView = null;
/** 마지막으로 읽은 저장된 실험 설정(/api/config) */
let savedConfig = {};
/** 최근 세션의 실제 쓰기량(바이트/초) — 예상 저장량 추정용. 없으면 null */
let recentRate = null;
/** 이 화면에서 고른 프리셋 키. **저장 설정에는 남기지 않고** 촬영 시작 때만 그 세션 스냅샷의 config.extra.preset에 싣는다
 *  (예전에는 저장 설정에 남아 이후 일반 세션까지 데이터셋으로 취급됐다). 다른 프리셋·'해제'로 바뀐다. */
let pendingPreset = null;

/** 저장 설정의 extra에서 preset 키를 뺀 사본 — 예전 버전이 남긴 값도 지운다 */
function extraWithoutPreset(extra) {
  const { preset: _drop, ...rest } = extra || {};
  return rest;
}
/** 마지막으로 받은 /api/status — 입력 변경 시 시작 전 점검을 바로 다시 그리기 위함 */
let lastStatus = null;

/** 첫 조리 시험 기본 센서(GMSL2 카메라 2 + 열화상 + PT100). 누락 여부를 시작 전에 보여 준다.
 *  Gemini 2(cam_depth_0)는 D-039로 제외 — Jetson은 연결 안 된 센서를 요청하면 시작을 거절하므로 넣지 않는다. */
const TRIAL_SENSORS = ['cam_rgb_0', 'cam_rgb_1', 'thermal_0', 'pt100_0'];
const MAX_DURATION_REASON = 'max_duration_sec=';  // Jetson stop_reason 접두사(자동 중지)
const PRESETS = {
  check: {
    name: '점검 60초 (가열 없음)',
    ingredients: '',
    conditions: '가열 없음 · 전체 센서 수집 점검',
    note: '',
    max_duration_sec: 60,
  },
  dataset: {
    // docs/cooking-protocol.md §4 — 과조리까지 촬영해야 3단계 라벨이 생긴다
    name: '소고기무국 데이터셋',
    ingredients: '비비고 소고기무국 2봉 — 실측 중량 ___ g',
    conditions: '솥 24 cm·인덕션 중앙 · 출력 ___단 · 추가 물 ___ mL · 뚜껑 ___ · 시작 국물 ___ ℃ · 탐침 깊이 ___ mm',
    note: '프로토콜 v0.1 — 재료 투입→가열 변경→끓음 시작→맛보기(2분, 완료 근처 1분)→완료 시작/끝→과조리(완료 끝 뒤 ≥10분)→가열 종료→1분 뒤 중지',
    max_duration_sec: 3600,
  },
  trial: {
    name: '소고기무국 재가열 관찰',
    ingredients: '비비고 소고기무국 2봉 — 포장 중량 ___ g ×2 · 실측 투입량 ___ g · 추가 물 ___ mL',
    conditions: '솥 약 24 cm · 초기 출력 ___ · 뚜껑 ___ · PT100 탐침 위치 ___ · 카메라/열화상 위치·높이 ___',
    note: '재가열·끓음 관찰 (생재료 익음·도네스 검증 아님)',
    max_duration_sec: 600,
  },
};

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

function durationText(sec) {
  if (sec == null || Number.isNaN(sec)) return '선택 안 함';
  if (sec === 0) return '제한 없음';
  const m = Math.floor(sec / 60);
  const r = Math.round(sec % 60);
  if (m === 0) return `${r}초`;
  return r ? `${m}분 ${r}초` : `${m}분`;
}

/** D-037 라이브 보기 — 원본을 저장하지 않는 미리보기 세션 */
function isLive(sess) {
  return !!(sess && sess.config && sess.config.record === false);
}

function isAutoStop(reason) {
  return typeof reason === 'string' && reason.startsWith(MAX_DURATION_REASON);
}

/** 최대 촬영 시간 입력값(초). 고르지 않았거나 잘못되면 null. */
function maxDurationValue() {
  const sel = $('in-maxdur').value;
  if (sel === '') return null;
  const raw = sel === 'custom' ? $('in-maxdur-custom').value : sel;
  if (raw === '') return null;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 && n <= 86400 ? n : null;
}

function setMaxDurationUi(sec) {
  const sel = $('in-maxdur');
  if (sec == null) {
    sel.value = '';
  } else if ([...sel.options].some((o) => o.value === String(sec))) {
    sel.value = String(sec);
  } else {
    sel.value = 'custom';
    $('in-maxdur-custom').value = String(sec);
  }
  $('lbl-maxdur-custom').classList.toggle('hidden', sel.value !== 'custom');
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
    ${maxDurationLine(sess, active)}
    ${paramsLine(sess)}
    ${endLine(sess)}
    <div class="session-meta">
      저장 결과: ${isLive(sess) ? '저장 안 함 (라이브 보기)'
        : sess.jetson_summary ? '확인됨' : `<b>${UNKNOWN}</b>`}
    </div>`;
}

const PARAM_VIEW = {
  heat_level: ['출력', '단'], water_added_ml: ['추가 물', ' mL'], lid_initial: ['뚜껑', ''],
  start_temp_c: ['시작', ' ℃'], probe_depth_mm: ['탐침', ' mm'], product_weight_g: ['중량', ' g'], taster: ['맛', ''],
};

/** 구조화 조건·시계 오차 한 줄. 빈 값은 '미기록'으로 보이게 해서 빠뜨린 칸을 알 수 있게 한다. */
function paramsLine(sess) {
  const parts = [];
  if (sess.params) {
    parts.push(Object.entries(PARAM_VIEW).map(([k, [label, unit]]) => {
      const v = sess.params[k];
      if (v == null) return `${label} <span class="muted">미기록</span>`;
      const shown = k === 'lid_initial' ? (MARK_VALUE_VIEW[v] || v) : escapeHtml(v);
      return `${label} ${shown}${unit}`;
    }).join(' · '));
  }
  const offs = sess.clock_offsets || [];
  if (offs.length) {
    const best = offs.reduce((a, b) => ((b.rtt_s ?? 1e9) < (a.rtt_s ?? 1e9) ? b : a));
    const off = Math.abs(best.offset_s) < 0.0005 ? 0 : best.offset_s;
    parts.push(`시계 오차 Jetson−Pi ${off > 0 ? '+' : off < 0 ? '' : '±'}${off.toFixed(3)}초 (왕복 ${(best.rtt_s * 1000).toFixed(0)} ms, ${offs.length}회 측정)`);
  }
  return parts.map((p) => `<div class="session-meta">${p}</div>`).join('');
}

function maxDurationLine(sess, active) {
  const cfg = sess.config || {};
  if (!('max_duration_sec' in cfg)) return '<div class="session-meta">최대 촬영 시간: 설정 없음</div>';
  const sec = Number(cfg.max_duration_sec);
  let tail = '';
  if (active && sec > 0 && sess.state === 'running' && sess.started_at) {
    // 참고 표시일 뿐 — 실제 종료는 Jetson이 한다(브라우저 타이머로 멈추지 않음)
    const left = sec - (Date.now() - new Date(sess.started_at).getTime()) / 1000;
    tail = left > 0 ? ` · 남은 시간 약 ${durationText(Math.ceil(left))}` : ' · Jetson 종료 대기';
  }
  return `<div class="session-meta">최대 촬영 시간: ${durationText(sec)}${tail}</div>`;
}

function endLine(sess) {
  const end = sess.jetson_end;
  if (sess.state === 'stopping' && !(end && end.stop_reason)) {
    return '<div class="session-meta">종료 처리 중 — Jetson 저장 마무리 대기</div>';
  }
  if (!end || !end.stop_reason) return '';
  const phases = end.phases || {};
  const when = phases.stop_requested ? ` (장치 ${localTime(phases.stop_requested)})` : '';
  const text = isAutoStop(end.stop_reason)
    ? `최대 촬영 시간 도달 — Jetson 자동 중지${when}. 데이터 촬영만 끝났고 인덕션은 별개입니다.`
    : `중지 사유: ${escapeHtml(end.stop_reason)}${when}`;
  const reason = end.end_reason ? ` · 종료 결과 ${escapeHtml(end.end_reason)}` : '';
  return `<div class="session-meta">${text}${reason}</div>`;
}

/** 시작 전 점검 — 기본 센서 누락·모의 여부·남은 용량·미리보기·최대 시간·예상 저장량을 한눈에. */
function renderPreflight(s) {
  const box = $('preflight');
  if (s.active_session && !isLive(s.active_session)) { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  const items = [];
  const report = s.report;
  items.push(['장치', s.link.mock
    ? '<span class="pf-warn">모의 Jetson — 실측 데이터가 아님</span>'
    : `실물 ${escapeHtml(s.link.base_url)}`]);

  const configured = Array.isArray(savedConfig.sensors) ? savedConfig.sensors : [];
  const sensors = (report && report.sensors) || [];
  const connected = new Set(sensors.filter((x) => x.connected).map((x) => x.sensor_id));
  const notConfigured = TRIAL_SENSORS.filter((id) => !configured.includes(id));
  const notConnected = TRIAL_SENSORS.filter((id) => !connected.has(id));
  const parts = [];
  if (!configured.length) parts.push('<span class="pf-warn">설정의 활성 센서가 비어 있음</span>');
  else if (notConfigured.length) parts.push(`<span class="pf-warn">설정에 없음: ${notConfigured.join(', ')}</span>`);
  if (!report) parts.push('<span class="pf-warn">Jetson 보고 없음</span>');
  else if (notConnected.length) parts.push(`<span class="pf-bad">미연결: ${notConnected.join(', ')}</span>`);
  if (!parts.length) parts.push(`${TRIAL_SENSORS.length}개 모두 설정·연결됨`);
  items.push(['기본 센서', parts.join(' · ')]);

  const storage = report && report.storage;
  items.push(['남은 용량', storage ? bytesText(storage.free_bytes) : UNKNOWN]);
  items.push(['프리셋', pendingPreset
    ? `${escapeHtml(PRESETS[pendingPreset].name)}${pendingPreset === 'dataset' ? ' — 조건 전송·종료 전 정답 사건 확인' : ''}
       <button type="button" class="btn btn-small" id="btn-preset-clear">해제</button>`
    : '없음 (일반 세션)']);
  items.push(['미리보기', $('in-preview').checked ? '켬 (1 Hz 출발)' : '끔']);
  const dur = maxDurationValue();
  items.push(['최대 촬영 시간', dur == null
    ? '<span class="pf-bad">선택하세요</span>' : durationText(dur)]);
  let est = '최근 쓰기량 기록 없음 — 추정 불가';
  if (recentRate) {
    const perMin = recentRate.bps * 60;
    est = `최근 세션 기준 약 ${bytesText(perMin)}/분 (추정)`;
    if (dur) est += ` → ${durationText(dur)} 동안 약 ${bytesText(perMin * dur / 60)}`;
    if (dur && storage && perMin * dur / 60 > storage.free_bytes * 0.9) {
      est = `<span class="pf-bad">${est} — 남은 용량 부족</span>`;
    }
  }
  items.push(['예상 저장량', est]);
  box.innerHTML = '<div class="preflight-title">시작 전 점검</div><ul>' +
    items.map(([k, v]) => `<li><span class="pf-k">${k}</span><span>${v}</span></li>`).join('') + '</ul>';
}

/** 저장 결과가 있는 가장 최근 세션의 쓰기 속도. 장치 단계 시각이 있으면 그것을 쓴다. */
function computeRecentRate(rows) {
  for (const r of rows) {
    const bytes = r.jetson_summary && r.jetson_summary.bytes_written;
    if (!bytes) continue;
    const ph = (r.jetson_end && r.jetson_end.phases) || {};
    const t0 = new Date(ph.running || r.started_at).getTime();
    const t1 = new Date(ph.completed || ph.stop_requested || r.stopped_at).getTime();
    const sec = (t1 - t0) / 1000;
    if (Number.isFinite(sec) && sec >= 5) return { bps: bytes / sec, session_id: r.session_id };
  }
  return null;
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

function renderSensorPreviewGate(s) {
  if (!sensorView) return;
  const sess = s.active_session;
  const pv = sess && sess.config && sess.config.preview;
  const on = pv === true || !!(pv && pv.enabled === true);
  if (!sess) sensorView.setActive(false, '진행 중인 세션 없음');
  else if (!on) sensorView.setActive(false, '미리보기를 켜지 않고 시작한 세션입니다. 원본은 정상 저장 중입니다.');
  else if (sess.state === 'stopping') sensorView.setActive(false, '종료 처리 중 — 미리보기 중단');
  else sensorView.setActive(true, '', sess.session_id);
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
    // 진행 중인 세션이 있으면 그 세션 사건만(지난 실험 메모가 섞이지 않게)
    const sid = lastStatus && lastStatus.active_session && lastStatus.active_session.session_id;
    renderMarks(await api(`/api/events?origin=manual&limit=10${sid ? `&session_id=${encodeURIComponent(sid)}` : ''}`));
  } catch (_) { /* 다음 주기에 다시 */ }
}

function renderMarks(marks) {
  $('mark-list').innerHTML = marks.length
    ? marks.slice(0, 8).map((e) => {
        const when = e.occurred_at || e.ts;
        const late = e.occurred_at && e.occurred_at !== e.ts;
        const value = e.detail && e.detail.value;
        const kind = (MARK_VIEW[e.code] || e.code) + (value ? `: ${MARK_VALUE_VIEW[value] || value}` : '');
        const text = (e.detail && e.detail.text) || '';
        return `<li class="mark-item" title="눌러서 정정 메모 쓰기"
            data-fix="${escapeHtml(`정정: ${localTime(when)} ${kind} — `)}"><span class="mark-time">${localTime(when)}</span>
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
  $('status-chip-dot').className = `status-dot ${view.dot}`;
  $('status-chip-text').textContent = `Jetson ${view.label}`;
  $('status-chip').title = `${s.status_reason} — 누르면 장치·설정 탭`;
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
  renderSensorPreviewGate(s);
  renderPreflight(s);
  // 녹화 중에는 시작 입력을 숨긴다. 라이브 보기 중에는 그대로 두어 바로 녹화로 넘어갈 수 있게 한다.
  const live = isLive(s.active_session);
  const recording = !!s.active_session && !live;
  $('start-form').classList.toggle('hidden', recording);
  $('btn-start').classList.toggle('hidden', recording);
  const canStart = s.jetson_status === 'online' && (!s.active_session || live);
  $('btn-start').disabled = !canStart || busy;
  $('btn-start').textContent = live ? '라이브 끝내고 촬영 시작' : '촬영 시작';
  $('btn-stop').disabled = !s.active_session || busy;
  $('btn-stop').textContent = live ? '라이브 끝내기' : '촬영 중지';
  const liveOk = !!(s.report && (s.report.capabilities || []).includes('live_view'));
  $('btn-live').classList.toggle('hidden', !liveOk || !!s.active_session);
  $('btn-live').disabled = s.jetson_status !== 'online' || busy;
  document.querySelectorAll('.btn-mark').forEach((b) => { b.disabled = busy; });
  // 조리 정답은 녹화 중인 세션에만 붙인다(라이브 보기·세션 없음이면 꺼 둠)
  document.querySelectorAll('.btn-gt').forEach((b) => { b.disabled = busy || !recording; });

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

// ── 탭 ─────────────────────────────────────────────────────────────────────
// 숨긴 탭의 미리보기 컴포넌트는 화면 밖으로 판정돼 스스로 요청을 멈춘다(IntersectionObserver).
const TAB_KEY = 'soup.tab';
function showTab(name) {
  const tabs = [...document.querySelectorAll('.tab')];
  if (!tabs.some((t) => t.dataset.tab === name)) name = 'run';
  tabs.forEach((t) => t.setAttribute('aria-selected', t.dataset.tab === name ? 'true' : 'false'));
  document.querySelectorAll('.tab-panel').forEach((p) => p.classList.toggle('hidden', p.dataset.panel !== name));
  try { localStorage.setItem(TAB_KEY, name); } catch (_) { /* 기억 못 해도 동작 */ }
  if (name === 'history') loadSessions();
  window.scrollTo(0, 0);
}

function bindTabs() {
  document.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => showTab(t.dataset.tab)));
  $('status-chip').addEventListener('click', () => showTab($('status-chip').dataset.goto));
  let saved = 'run';
  try { saved = localStorage.getItem(TAB_KEY) || 'run'; } catch (_) { /* 기본 탭 */ }
  showTab(saved);
}

async function loadSessions() {
  try {
    const rows = await api('/api/sessions?limit=12');
    recentRate = computeRecentRate(rows);
    $('session-rows').innerHTML = rows.length
      ? rows.map((r) => {
          const [label, cls] = SESSION_VIEW[r.state] || [r.state, ''];
          const saved = isLive(r) ? '<span class="muted">저장 안 함 (라이브)</span>' : r.jetson_summary
            ? `${r.jetson_summary.files != null ? r.jetson_summary.files.toLocaleString() + '개' : ''} ${bytesText(r.jetson_summary.bytes_written)}`
            : `<span class="muted">${UNKNOWN}</span>`;
          return `<tr>
            <td>${localDateTime(r.started_at)}</td>
            <td>${escapeHtml(r.name)}<div class="muted mono">${escapeHtml(r.session_id)}</div></td>
            <td><span class="pill ${cls}">${label}</span>${
              r.jetson_end && isAutoStop(r.jetson_end.stop_reason) ? '<div class="muted">시간 제한 종료</div>' : ''}</td>
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
    lastStatus = s;
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
    const maxDur = maxDurationValue();
    if (maxDur == null) throw new Error('최대 촬영 시간을 먼저 고르세요 (0 = 제한 없음).');
    const body = {
      name: $('in-name').value || '실험',
      note: $('in-note').value || null,
      ingredients: $('in-ingredients').value || null,
      conditions: $('in-conditions').value || null,
    };
    const params = collectParams();
    if (params) body.params = params;
    const previewOn = $('in-preview').checked;
    try { localStorage.setItem('soup.previewOn', previewOn ? '1' : '0'); } catch (_) { /* 무시 */ }
    // 저장된 실험 설정을 그대로 쓰되 미리보기 켜기/끄기와 최대 촬영 시간은 **명시적으로** 보낸다
    // (체크를 해제해도 저장된 preview.enabled=true가 박제되던 문제 방지).
    // 저장된 preview의 다른 값(depth_max_mm 등)·센서·fps는 유지한다. 이 값이 세션 스냅샷에 박제된다.
    const saved = await api('/api/config');
    savedConfig = saved;
    const prevPreview = saved.preview || {};
    body.config = {
      ...saved,
      preview: { ...prevPreview, enabled: previewOn, max_fps: prevPreview.max_fps || 1 },
      max_duration_sec: maxDur,
      // 프리셋 표시는 이 세션 스냅샷에만(저장 설정에는 남기지 않음)
      extra: pendingPreset ? { ...extraWithoutPreset(saved.extra), preset: pendingPreset } : extraWithoutPreset(saved.extra),
    };
    const missing = TRIAL_SENSORS.filter((id) => !(saved.sensors || []).includes(id));
    const lines = [
      `최대 촬영 시간: ${durationText(maxDur)}` +
        (maxDur ? ' — 시간이 되면 Jetson이 데이터 촬영을 멈춥니다(인덕션은 끄지 않음).' : ' — 직접 중지해야 합니다.'),
      `미리보기: ${previewOn ? '켬' : '끔'}`,
      `센서: ${(saved.sensors || []).join(', ') || '(설정 비어 있음)'}`,
    ];
    if (missing.length) lines.push(`기본 센서 중 설정에 없음: ${missing.join(', ')}`);
    if (lastStatus && isLive(lastStatus.active_session)) lines.unshift('진행 중인 라이브 보기를 끝내고 녹화를 시작합니다.');
    if ($('mock-badge') && !$('mock-badge').classList.contains('hidden')) lines.push('※ 모의 모드 — 실측 데이터가 아닙니다.');
    if (!window.confirm(`이 설정으로 촬영을 시작할까요?\n\n${lines.join('\n')}`)) return null;
    await endLiveIfRunning();
    const sess = await api('/api/capture/start', { method: 'POST', body: JSON.stringify(body) });
    loadSessions();
    return `촬영 시작됨: ${sess.session_id}`;
  }, $('capture-msg')));

  $('btn-stop').addEventListener('click', () => withBusy(async () => {
    const warn = await missingDatasetMarks();
    if (warn && !window.confirm(`정답 사건이 빠져 있습니다: ${warn}\n이대로면 이 세션은 라벨을 만들 수 없습니다(완료 시작·과조리가 기준점).\n\n그래도 중지할까요?`)) return null;
    const sess = await api('/api/capture/stop', { method: 'POST', body: JSON.stringify({}) });
    loadSessions();
    return `촬영 중지됨: ${sess.session_id} (${sess.state})`;
  }, $('capture-msg')));

  document.querySelectorAll('.btn-mark').forEach((btn) => {
    btn.addEventListener('click', () => withBusy(async () => {
      // 조리 정답 버튼은 메모 칸을 쓰지 않는다 — 남아 있던 '정정: …' 문구가 정답 사건에 붙지 않게(지우지도 않음)
      const gt = btn.classList.contains('btn-gt');
      const typed = gt ? '' : $('mark-text').value.trim();
      const quick = btn.dataset.text || '';
      const body = {
        kind: btn.dataset.kind,
        value: btn.dataset.value || null,
        text: (quick && typed ? `${quick} — ${typed}` : quick || typed) || null,
        occurred_at: markTimeToUtc(),
      };
      const ev = await api('/api/marks', { method: 'POST', body: JSON.stringify(body) });
      if (!gt) $('mark-text').value = '';
      $('mark-time').value = '';
      await loadMarks();
      const leftover = gt && $('mark-text').value.trim()
        ? ' (메모 칸 내용은 붙이지 않았습니다 — 메모 버튼으로 남기세요)' : '';
      return `기록됨: ${ev.message}${leftover}`;
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
    savedConfig = await api('/api/config', { method: 'PUT', body: JSON.stringify(body) });
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

  $('in-maxdur').addEventListener('change', () => {
    $('lbl-maxdur-custom').classList.toggle('hidden', $('in-maxdur').value !== 'custom');
    saveMaxDuration();
  });
  $('in-maxdur-custom').addEventListener('change', saveMaxDuration);
  $('in-preview').addEventListener('change', () => { if (lastStatus) renderPreflight(lastStatus); });
  $('btn-live').addEventListener('click', () => withBusy(async () => {
    const sess = await api('/api/live/start', { method: 'POST', body: JSON.stringify({}) });
    return `라이브 보기 시작: ${sess.session_id} — 저장하지 않습니다. 최대 ${durationText(sess.config.max_duration_sec)} 뒤 자동 종료.`;
  }, $('capture-msg')));
  $('btn-preset-dataset').addEventListener('click', () => applyPreset('dataset'));
  $('preflight').addEventListener('click', (ev) => {
    if (ev.target.id !== 'btn-preset-clear') return;
    pendingPreset = null;
    $('params-box').open = false;
    if (lastStatus) renderPreflight(lastStatus);
  });
  $('mark-list').addEventListener('click', (ev) => {
    const li = ev.target.closest('.mark-item');
    if (!li) return;
    $('mark-text').value = li.dataset.fix;  // 삭제 대신 정정 메모 — 원래 사건은 그대로 남는다
    $('mark-text').focus();
  });
  $('btn-preset-check').addEventListener('click', () => applyPreset('check'));
  $('btn-preset-trial').addEventListener('click', () => applyPreset('trial'));

  $('btn-mock-on').addEventListener('click', mock('/api/mock/jetson/power', { on: true }));
  $('btn-mock-off').addEventListener('click', mock('/api/mock/jetson/power', { on: false }));
  $('btn-mock-cut').addEventListener('click', mock('/api/mock/jetson/link', { cut: true }));
  $('btn-mock-join').addEventListener('click', mock('/api/mock/jetson/link', { cut: false }));
}

/** 라이브 보기 중이면 끝내고, Jetson이 세션을 닫을 때까지(최대 20초) 기다린다. 녹화 세션은 건드리지 않는다. */
async function endLiveIfRunning() {
  let s = await api('/api/status');
  if (!s.active_session) return;
  if (!isLive(s.active_session)) throw new Error('진행 중인 녹화가 있습니다. 먼저 중지하세요.');
  await api('/api/capture/stop', { method: 'POST', body: JSON.stringify({ session_id: s.active_session.session_id, reason: 'live_to_recording' }) });
  for (let i = 0; i < 20; i += 1) {
    s = await api('/api/status/refresh', { method: 'POST' });
    if (!s.active_session) return;
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error('라이브 보기가 아직 끝나지 않았습니다. 잠시 뒤 다시 시작하세요.');
}

/** 최대 촬영 시간을 실험 설정에 저장한다(재조회·다음 시작에 그대로 쓰이도록). */
async function saveMaxDuration() {
  const v = maxDurationValue();
  if (v == null) return;
  try {
    savedConfig = await api('/api/config', { method: 'PUT', body: JSON.stringify({ max_duration_sec: v }) });
    showMsg($('capture-msg'), `최대 촬영 시간 저장: ${durationText(v)}`, 'ok');
  } catch (err) {
    showMsg($('capture-msg'), err.message, 'err');
  }
  if (lastStatus) renderPreflight(lastStatus);
}

/** 데이터셋 조건 칸 → params. 하나도 안 채웠고 데이터셋 프리셋도 아니면 null(보내지 않음). 빈 칸은 null. */
function collectParams() {
  const out = {};
  let filled = false;
  PARAM_FIELDS.forEach((k) => {
    const raw = $(`p-${k}`).value.trim();
    if (raw === '') { out[k] = null; return; }
    filled = true;
    out[k] = PARAM_TEXT.has(k) ? raw : Number(raw);
  });
  const dataset = pendingPreset === 'dataset';
  return filled || dataset ? out : null;
}

function isDatasetSession(sess) {
  return !!(sess && sess.config && sess.config.extra && sess.config.extra.preset === 'dataset');
}

/** 데이터셋 세션인데 완료 시작·과조리 사건이 없으면 빠진 이름을 돌려준다(없으면 null). 중지는 막지 않는다. */
async function missingDatasetMarks() {
  const s = await api('/api/status');
  const sess = s.active_session;
  if (!isDatasetSession(sess)) return null;
  const evs = await api(`/api/events?origin=manual&limit=500&session_id=${encodeURIComponent(sess.session_id)}`);
  const seen = new Set(evs.map((e) => e.code));
  const missing = Object.entries(DATASET_KEY_MARKS).filter(([code]) => !seen.has(code)).map(([, name]) => name);
  return missing.length ? missing.join(', ') : null;
}

/** 프리셋: 입력칸을 채우고 센서·fps·최대 시간·미리보기를 실험 설정에 저장한다. 값은 모두 편집 가능. */
function applyPreset(key) {
  const p = PRESETS[key];
  withBusy(async () => {
    $('in-name').value = p.name;
    $('in-ingredients').value = p.ingredients;
    $('in-conditions').value = p.conditions;
    $('in-note').value = p.note;
    $('in-preview').checked = true;
    const saved = await api('/api/config');
    const preview = { ...(saved.preview || {}), enabled: true, max_fps: 1 };
    savedConfig = await api('/api/config', {
      method: 'PUT',
      body: JSON.stringify({
        sensors: TRIAL_SENSORS, fps: 10, max_duration_sec: p.max_duration_sec, preview,
        extra: extraWithoutPreset(saved.extra),  // 프리셋 키는 저장하지 않는다 — pendingPreset으로 시작 때만 싣는다
      }),
    });
    pendingPreset = key;
    $('params-box').open = key === 'dataset';
    setMaxDurationUi(p.max_duration_sec);
    fillConfigForm(savedConfig);
    return `프리셋 적용: ${p.name} — 센서 ${TRIAL_SENSORS.length}개·10 fps·미리보기 1 Hz·최대 ${durationText(p.max_duration_sec)}. ___ 칸은 현장 실측값으로 채우세요.`;
  }, $('capture-msg'));
}

function fillConfigForm(cfg) {
  if (Array.isArray(cfg.sensors)) $('cfg-sensors').value = cfg.sensors.join(', ');
  if (cfg.fps != null) $('cfg-fps').value = cfg.fps;
}

async function loadConfig() {
  try {
    const cfg = await api('/api/config');
    savedConfig = cfg;
    setMaxDurationUi(cfg.max_duration_sec == null ? null : Number(cfg.max_duration_sec));
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

async function mountSensorPreview() {
  try {
    const cfg = await api('/api/sensor-preview/config');
    if (cfg.config_error) console.warn(cfg.config_error);
    sensorView = window.SensorPreview.mount($('sensor-preview'),
      window.SensorPreview.fromServerConfig(cfg, { headers: { 'X-Soup-Client': 'ui' } }));
    sensorView.setActive(false, '상태 확인 중…');
  } catch (err) {
    $('sensor-preview').textContent = `열화상·PT100 설정을 읽지 못했습니다: ${err.message}`;
  }
}

bind();
bindTabs();
try { $('in-preview').checked = localStorage.getItem('soup.previewOn') !== '0'; } catch (_) { /* 기본 켜짐 */ }
mountCameraPreview();
mountSensorPreview();
loadConfig();
loadSessions();
loadMarks();
poll();
setInterval(poll, POLL_MS);
