"""브라우저 점검 화면 — `GET /viewer`.

Jetson에서 지금 들어오는 프레임을 눈으로 확인하기 위한 **점검용** 페이지다. 새 데이터 경로를
만들지 않는다: 기존 `GET /api/v1/status`와 저속 미리보기
`GET /api/v1/capture/preview/{sensor_id}/{stream_id}`만 주기적으로 읽는다. 따라서 미리보기를
켠(`config.preview.enabled=true`) 활성 세션이 있어야 그림이 나온다. 운영 중 세션 제어는 Pi가
맡고, 이 화면의 시작/중지 버튼은 Pi 없이 벤치에서 확인할 때만 쓴다(세션이 이미 있으면 서비스가 거절한다).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

viewer_router = APIRouter()

_PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Jetson 수집 점검</title>
<style>
 :root{color-scheme:light dark;--bg:#f6f7f9;--fg:#15181d;--mut:#667085;--card:#fff;--line:#d9dde3;--ok:#137a3f;--bad:#b42318}
 @media(prefers-color-scheme:dark){:root{--bg:#111418;--fg:#e8eaed;--mut:#9aa4b2;--card:#1a1f26;--line:#2c333d;--ok:#4ade80;--bad:#f97066}}
 body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif}
 h1{font-size:18px;margin:0 0 4px} .mut{color:var(--mut)} .ok{color:var(--ok)} .bad{color:var(--bad)}
 .bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:12px 0}
 button,select{font:inherit;padding:6px 12px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg);cursor:pointer}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
 .card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px;min-width:0}
 .card img{width:100%;display:block;border-radius:4px;background:#000;min-height:120px}
 .card.arr{max-width:340px}
 .card canvas{width:100%;max-width:240px;display:block;margin:0 auto;border-radius:4px;background:#000;image-rendering:pixelated;cursor:crosshair}
 .card .ramp,.card .ticks{max-width:240px;margin-left:auto;margin-right:auto}
 .ramp{height:8px;border-radius:999px;margin-top:6px;background:linear-gradient(90deg,#000004,#4a0c6b,#a52c60,#ed6925,#f7d13d,#fcffa4)}
 .ticks{display:flex;justify-content:space-between;font-size:11px;color:var(--mut);font-variant-numeric:tabular-nums}
 .card h2{font-size:14px;margin:0 0 6px} .stat{font-variant-numeric:tabular-nums;font-size:12px;margin-top:6px}
 table{border-collapse:collapse;width:100%;font-size:12px} td,th{padding:3px 6px;border-bottom:1px solid var(--line);text-align:left}
 .scroll{overflow-x:auto}
</style></head><body>
<h1>Jetson 수집 점검 화면</h1>
<div class="mut">미리보기는 축소 JPEG(최대 2 fps)이고 열화상은 <b>숫자 배열</b>을 받아 화면에서 히트맵으로 그린다. 저장되는 원본과 별개다. 점검 세션은 10 fps·깊이 의사색 0~1.5 m로 열리고 <b>원본이 실제로 저장된다</b> — 확인 후 세션 중지.</div>
<div class="bar">
 <span id="state">상태 읽는 중…</span>
 <button id="start">점검 세션 시작</button><button id="stop">세션 중지</button>
 <label class="mut">갱신 <select id="period"><option value="1000">1초</option><option value="500">0.5초</option><option value="2000">2초</option></select></label>
</div>
<div id="msg" class="bad"></div>
<div class="grid" id="views"></div>
<h2 style="font-size:15px;margin:18px 0 6px">센서</h2>
<div class="card scroll"><table id="sensors"></table></div>
<script>
const API = "/api/v1", PREVIEWABLE = ["rgb","color","depth","ir","left_ir","right_ir"];
const ARRAY_STREAMS = ["temp_array"];  // 그림이 아니라 숫자 배열로 받아 화면에서 히트맵을 그린다
const RAMP = [[0,4,4],[27,12,65],[74,12,107],[120,28,109],[165,44,96],[207,68,70],[237,105,37],[251,155,6],[247,209,61],[252,255,164]];
function rampColor(t){ t = t<0?0:t>1?1:t; const x=t*(RAMP.length-1), i=Math.min(Math.floor(x),RAMP.length-2), f=x-i;
  const a=RAMP[i], b=RAMP[i+1]; return [a[0]+(b[0]-a[0])*f, a[1]+(b[1]-a[1])*f, a[2]+(b[2]-a[2])*f]; }
const $ = id => document.getElementById(id);
let timer = null, cards = {};
function esc(s){return String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}
async function tick(){
  let st;
  try { st = await (await fetch(API + "/status", {cache:"no-store"})).json(); $("msg").textContent = ""; }
  catch(e){ $("state").innerHTML = '<span class="bad">서비스 응답 없음</span>'; return; }
  const c = st.capture || {};
  $("state").innerHTML = `세션 <b>${esc(c.state)}</b> ${esc(c.session_id || "")} · 기록 ${c.frames_written ?? 0} · 드롭 ${c.frames_dropped ?? 0}`
    + (c.last_error ? ` · <span class="bad">${esc(c.last_error)}</span>` : "");
  $("sensors").innerHTML = "<tr><th>sensor_id</th><th>kind</th><th>연결</th><th>설명 / 사유</th></tr>" + (st.sensors || []).map(s =>
    `<tr><td>${esc(s.sensor_id)}</td><td>${esc(s.kind)}</td><td class="${s.connected ? "ok" : "bad"}">${s.connected ? "연결" : "없음"}${s.simulated ? " (모의)" : ""}</td><td>${esc(s.reason || s.detail)}</td></tr>`).join("");
  const live = (c.state === "running" ? (c.streams || []) : []);
  const streams = live.filter(s => PREVIEWABLE.includes(s.stream_id));
  const arrays = live.filter(s => ARRAY_STREAMS.includes(s.stream_id));
  const keys = streams.concat(arrays).map(s => s.sensor_id + "/" + s.stream_id);
  for (const k of Object.keys(cards)) if (!keys.includes(k)) { cards[k].remove(); delete cards[k]; }
  if (!streams.length && !arrays.length && !Object.keys(cards).length) $("views").innerHTML = '<div class="card mut">실행 중인 세션이 없거나 미리보기 대상 스트림이 없다.</div>';
  for (const s of streams) {
    const k = s.sensor_id + "/" + s.stream_id;
    if (!cards[k]) {
      if (!Object.keys(cards).length) $("views").innerHTML = "";
      const d = document.createElement("div"); d.className = "card";
      d.innerHTML = `<h2>${esc(k)}</h2><img alt="${esc(k)}"><div class="stat"></div>`;
      $("views").appendChild(d); cards[k] = d;
    }
    const d = cards[k];
    d.querySelector(".stat").textContent = `수신 ${s.recv_fps ?? "-"} fps · 기록 ${s.write_fps ?? "-"} fps · 누적 ${s.written} · 드롭 ${s.dropped_total} · 무효 ${s.invalid} · 대기열 ${s.backlog}` + (s.connected === false ? " · 센서 끊김" : "");
    try {
      const r = await fetch(`${API}/capture/preview/${s.sensor_id}/${s.stream_id}`, {cache:"no-store"});
      if (r.ok) { const img = d.querySelector("img"), old = img.src; img.src = URL.createObjectURL(await r.blob()); if (old.startsWith("blob:")) URL.revokeObjectURL(old); }
      else d.querySelector(".stat").textContent += " · 미리보기 없음(세션을 preview.enabled=true로 시작해야 함)";
    } catch(e){}
  }
  for (const s of arrays) {
    const k = s.sensor_id + "/" + s.stream_id;
    if (!cards[k]) {
      if (!Object.keys(cards).length) $("views").innerHTML = "";
      const d = document.createElement("div"); d.className = "card arr";
      d.innerHTML = `<h2>${esc(k)}</h2><canvas width="32" height="24"></canvas>`
        + `<div class="ramp"></div><div class="ticks"><span class="t0">-</span><span class="t1">-</span></div>`
        + `<div class="stat"></div><div class="stat pix mut">화소를 짚으면 그 지점 온도</div>`;
      $("views").appendChild(d); cards[k] = d;
      const cv = d.querySelector("canvas");
      cv.addEventListener("mousemove", ev => {
        const g = cv._grid; if (!g) return;
        const r = cv.getBoundingClientRect();
        const cx = Math.floor((ev.clientX - r.left) / r.width * g.cols);
        const cy = Math.floor((ev.clientY - r.top) / r.height * g.rows);
        if (cx < 0 || cy < 0 || cx >= g.cols || cy >= g.rows) return;
        d.querySelector(".pix").textContent = `${(g.deci[cy*g.cols+cx]/10).toFixed(1)} \u2103 · ${cx},${cy}`;
      });
      cv.addEventListener("mouseleave", () => { d.querySelector(".pix").textContent = "화소를 짚으면 그 지점 온도"; });
    }
    const d = cards[k];
    d.querySelector(".stat").textContent = `수신 ${s.recv_fps ?? "-"} Hz · 기록 ${s.write_fps ?? "-"} Hz · 누적 ${s.written} · 무효 ${s.invalid} · 대기열 ${s.backlog}` + (s.connected === false ? " · 센서 끊김" : "");
    try {
      const r = await fetch(`${API}/capture/preview_array/${s.sensor_id}/${s.stream_id}`, {cache:"no-store"});
      if (!r.ok) { d.querySelector(".stat").textContent += " · 미리보기 없음(preview.enabled=true로 시작해야 함)"; continue; }
      const a = await r.json();
      const cv = d.querySelector("canvas");
      if (cv.width !== a.cols || cv.height !== a.rows) { cv.width = a.cols; cv.height = a.rows; }
      cv._grid = {rows: a.rows, cols: a.cols, deci: a.deci};
      const lo = a.min, hi = (a.max - a.min < 1 ? a.min + 1 : a.max);   // 평탄한 장면에서 잡음이 과장되지 않게
      const ctx = cv.getContext("2d"), img = ctx.createImageData(a.cols, a.rows);
      for (let i = 0; i < a.deci.length; i++) {
        const cc = rampColor((a.deci[i]/10 - lo) / (hi - lo));
        img.data[i*4] = cc[0]; img.data[i*4+1] = cc[1]; img.data[i*4+2] = cc[2]; img.data[i*4+3] = 255;
      }
      ctx.putImageData(img, 0, 0);
      d.querySelector(".t0").textContent = lo.toFixed(1) + " \u2103";
      d.querySelector(".t1").textContent = hi.toFixed(1) + " \u2103";
      d.querySelector(".stat").textContent += ` · 평균 ${a.mean.toFixed(1)} \u2103 · seq ${a.seq}`;
    } catch(e){}
  }
}
function schedule(){ clearInterval(timer); timer = setInterval(tick, Number($("period").value)); }
$("period").onchange = schedule;
$("start").onclick = async () => {
  const st = await (await fetch(API + "/status")).json();
  const sensors = (st.sensors || []).filter(s => s.connected && !s.simulated).map(s => s.sensor_id);
  if (!sensors.length) { $("msg").textContent = "연결된 실물 센서가 없다."; return; }
  const sid = "check-" + new Date().toISOString().replace(/[-:]/g, "").slice(0, 15) + "Z";
  const r = await (await fetch(API + "/capture/start", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({session_id: sid, name: "viewer 점검 세션", config: {sensors, fps: 10, preview: {enabled: true, max_fps: 2, depth_max_mm: 1500}}})})).json();
  $("msg").textContent = r.accepted ? "" : ("시작 거절: " + (r.message || ""));
  tick();
};
$("stop").onclick = async () => {
  const r = await (await fetch(API + "/capture/stop", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({reason: "viewer"})})).json();
  $("msg").textContent = r.accepted ? "" : ("중지 거절: " + (r.message || ""));
  tick();
};
tick(); schedule();
</script></body></html>
"""


@viewer_router.get("/viewer", response_class=HTMLResponse, include_in_schema=False)
async def viewer() -> HTMLResponse:
    return HTMLResponse(_PAGE, headers={"Cache-Control": "no-store"})
