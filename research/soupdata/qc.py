"""세션 품질 요약(QC)과 카탈로그 행.

입력은 Fedora에 반출·검증된 세션 디렉터리와(있으면) Pi 세션 내보내기 JSON(`pi/<session_id>.json`).
원본을 고치지 않고 숫자만 뽑는다. 해석(좋다/나쁘다)은 사람이 노트에 적는다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .session import ARRAY, IMAGE, SCALAR, Session

#: D-039 이후 데이터셋 기본 4센서 스트림 — 없으면 자동 경고
EXPECTED_STREAMS = ("cam_rgb_0/rgb", "cam_rgb_1/rgb", "thermal_0/temp_array", "pt100_0/temp")
CLOCK_OFFSET_WARN_S = 0.5

#: Pi 정답 사건(`mark.<kind>`) — Pi `doneness-marks` 지시서 기준. 없는 사건은 0으로 센다.
DONENESS_MARKS = ("boil_start", "taste", "done_start", "done_end", "overcooked", "lid")


def parse_utc(s: str | None) -> datetime | None:
    """UTC ISO8601 → aware datetime. 시간대 없는 값·해석 불가 값은 None(추정하지 않음 — 호출자가 누락으로 다룬다)."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


def stream_qc(sess: Session, sensor_id: str, stream_id: str) -> dict[str, Any]:
    lines = sess.index(sensor_id, stream_id)
    stored = [ln for ln in lines if ln.get("valid") and ln.get("frame_id") is not None]
    times = [t for t in (parse_utc(ln.get("host_recv_utc")) for ln in stored) if t]
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
    span = (times[-1] - times[0]).total_seconds() if len(times) > 1 else 0.0
    reasons: dict[str, int] = {}
    for ln in lines:
        if not (ln.get("valid") and ln.get("frame_id") is not None):
            r = ln.get("invalid_reason") or "unknown"
            reasons[r] = reasons.get(r, 0) + 1
    return {
        "stream": f"{sensor_id}/{stream_id}",
        "received": len(lines),
        "stored": len(stored),
        "not_stored": len(lines) - len(stored),
        "not_stored_reasons": reasons,
        "fps": round((len(times) - 1) / span, 2) if span > 0 else None,
        "max_gap_s": round(max(gaps), 3) if gaps else None,
        "first_utc": stored[0]["host_recv_utc"] if stored else None,
        "last_utc": stored[-1]["host_recv_utc"] if stored else None,
    }


def scalar_range(sess: Session, sensor_id: str, stream_id: str) -> tuple[float | None, float | None]:
    vals = [v for _, v in sess.scalars(sensor_id, stream_id) if isinstance(v, (int, float))]
    return (min(vals), max(vals)) if vals else (None, None)


def array_max_range(sess: Session, sensor_id: str, stream_id: str, step: int = 10) -> tuple[float | None, float | None]:
    """배열 스트림의 프레임별 최댓값 범위(표본 간격 `step`). 열화상 최고 온도 추이 확인용."""
    import numpy as np

    maxima = [float(np.nanmax(a)) for i, (_, a) in enumerate(sess.iter_arrays(sensor_id, stream_id)) if i % step == 0]
    return (min(maxima), max(maxima)) if maxima else (None, None)


def pi_marks(pi_export: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not pi_export:
        return []
    out = []
    for e in pi_export.get("events", []):
        code = e.get("code") or ""
        if e.get("origin") == "manual" and code.startswith("mark."):
            detail = e.get("detail") or {}
            out.append({"kind": code[len("mark."):], "value": detail.get("value"), "text": detail.get("text"),
                        "at": e.get("occurred_at") or e.get("ts"), "late_entry": detail.get("late_entry")})
    return out


def heating_qc(sess: Session, t0, pi_export: dict[str, Any] | None, calibration: dict[str, Any] | None,
               rules=None) -> dict[str, Any]:
    """PT100 곡선 분석 + 객관 라벨(D-041) + 관능(Pi 사건)과의 차이. PT100이 없으면 빈 dict."""
    from .dataset import session_curve
    from .labels import LabelRules, agreement, build_objective, build_timeline

    rules = rules or LabelRules()
    if t0 is None:
        return {}
    curve = session_curve(sess, t0, pi_export, calibration, rules)
    if curve is None:
        return {}
    sensory = build_timeline(pi_export)
    obj = build_objective(curve, rules, sensory)
    return {"heating": curve.summary().to_dict(), "objective": obj.to_dict(), "agreement": agreement(obj, sensory),
            "rules": rules.to_dict(), "t0": t0.isoformat()}


def session_qc(sess: Session, pi_export: dict[str, Any] | None = None, calibration: dict[str, Any] | None = None,
               rules=None, camera_step_s: float | None = 10.0) -> dict[str, Any]:
    meta, manifest = sess.meta, sess.manifest or {}
    streams = []
    ranges: dict[str, Any] = {}
    for ref in sess.streams():
        streams.append({**stream_qc(sess, ref.sensor_id, ref.stream_id), "kind": ref.kind})
        if ref.kind == SCALAR:
            ranges[ref.rel] = scalar_range(sess, ref.sensor_id, ref.stream_id)
        elif ref.kind == ARRAY:
            ranges[ref.rel] = array_max_range(sess, ref.sensor_id, ref.stream_id)
    events = sess.events()
    phases = meta.get("phases") or {}
    # Jetson `session.py` 단계 이름: running(촬영 시작) → stop_requested/stopping → files_closed → completed
    t0 = parse_utc(phases.get("running"))
    t1 = parse_utc(phases.get("stop_requested") or phases.get("stopping"))
    marks = pi_marks(pi_export)
    cams = []
    if camera_step_s and t0 is not None:
        from .camera import camera_qc

        cams = [camera_qc(sess, r.sensor_id, r.stream_id, t0, camera_step_s).to_dict()
                for r in sess.streams() if r.kind == IMAGE]
    from .labels import clock_offset

    off, off_src = clock_offset(pi_export)
    return {
        "session_id": sess.session_id,
        "name": meta.get("name"),
        "state": manifest.get("state"),
        "duration_s": round((t1 - t0).total_seconds(), 1) if t0 and t1 else None,
        "summary": manifest.get("summary") or {},
        "streams": streams,
        "ranges": ranges,
        "events_by_level": _count(e.get("level") for e in events),
        "marks": marks,
        "mark_counts": {k: sum(1 for m in marks if m["kind"] == k) for k in DONENESS_MARKS},
        "pi_params": ((pi_export or {}).get("session") or {}).get("params"),
        "pi_conditions": ((pi_export or {}).get("session") or {}).get("conditions"),
        "pi_present": pi_export is not None,
        "clock_offset_s": off if off_src != "none" else None,
        "camera": cams,
        **heating_qc(sess, t0, pi_export, calibration, rules),
    }


def auto_flags(q: dict[str, Any], verify: dict[str, Any] | None = None) -> list[str]:
    """세션 하나의 자동 경고 — 사람이 요약 이미지를 보기 전에 문제를 먼저 알린다. 판정(사용/제외)은 사람이 한다."""
    flags: list[str] = []
    if verify is not None and not verify.get("ok"):
        flags.append("무결성 문제: " + "; ".join(verify.get("problems", [])[:3]))
    present = {s["stream"] for s in q["streams"]}
    missing = [s for s in EXPECTED_STREAMS if s not in present]
    if missing:
        flags.append("기본 센서 스트림 없음: " + ", ".join(missing))
    for st in q["streams"]:
        if st["stored"] == 0:
            flags.append(f"{st['stream']}: 저장된 샘플 0")
            continue
        if st["received"] and st["not_stored"] / st["received"] > 0.01:
            why = ", ".join(f"{k} {v}" for k, v in st["not_stored_reasons"].items())
            flags.append(f"{st['stream']}: 미저장 {st['not_stored'] / st['received']:.1%} ({why})")
        if st["fps"] and st["max_gap_s"] and st["max_gap_s"] > max(5.0 / st["fps"], 3.0):
            flags.append(f"{st['stream']}: 최대 수신 간격 {st['max_gap_s']:.1f}초(평소 {1 / st['fps']:.1f}초)")
    for c in q.get("camera") or []:
        flags += c.get("flags") or []
    flags += list((q.get("heating") or {}).get("flags") or []) + list((q.get("objective") or {}).get("flags") or [])
    if not q.get("pi_present"):
        flags.append("Pi 내보내기 없음 — 조건·관능 사건 없음(다음 야간 작업에서 다시 받음)")
    else:
        if not q.get("pi_params"):
            flags.append("Pi 조건 키-값(params) 없음 — 출력·물양·뚜껑·질량 기록 필요")
        miss = [k for k in ("done_start", "overcooked") if q["mark_counts"].get(k, 0) == 0]
        if miss:
            flags.append("Pi 정답 사건 없음: " + ", ".join(miss))
        off = q.get("clock_offset_s")
        if off is None:
            flags.append("Pi↔Jetson 시계 오차 측정 없음")
        elif abs(off) > CLOCK_OFFSET_WARN_S:
            flags.append(f"Pi↔Jetson 시계 오차 {off:+.2f}초 — {CLOCK_OFFSET_WARN_S:g}초 초과")
    return flags


def _min(s, t0_iso=None) -> str:
    return "—" if s is None else f"{s / 60:.1f}분"


def _iso_min(iso: str | None, t0_iso: str | None) -> str:
    if not iso or not t0_iso:
        return "—"
    return f"{(datetime.fromisoformat(iso) - datetime.fromisoformat(t0_iso)).total_seconds() / 60:.1f}분"


def heating_markdown(q: dict[str, Any]) -> str:
    h, o, ag = q.get("heating"), q.get("objective") or {}, q.get("agreement") or {}
    if not h:
        return "## 가열 곡선 (PT100, 자동)\n- PT100 기록 없음\n"
    b = h["boil"]
    fmt = lambda v, f="{:.2f}", unit="": "—" if v is None else f.format(v) + unit  # noqa: E731
    lines = [
        "## 가열 곡선 (PT100, 자동)",
        f"- 끓기 시작: {_min(b['onset_s'])}" + (f" · 끓는 구간 {b['plateau_c']:.1f} ℃ · 끝 {_min(b['end_s'])}" if b["onset_s"] is not None else ""),
        f"- {h['ref_c']:g} ℃ 1분 유지 충족: {_min(h['t_ref_reached_s'])} (참고선)",
        f"- 가열 속도(40→80 ℃): {fmt(h['rate_c_per_min'], '{:.1f}', ' ℃/분')} · 추정 열량 {fmt(h['p_net_kw'], '{:.2f}', ' kW')}"
        f" (투입 {fmt(h['mass_kg'], '{:.2f}', ' kg')})",
        f"- 끓은 시간: {_min(h['boil_duration_s'])} · 증발 추정(상한): {fmt(h['evap_frac'] * 100 if h['evap_frac'] is not None else None, '{:.0f}', '%')}",
        f"- 조리값 C₁₀₀(끝): {fmt(h['c100_end'], '{:.1f}', '분')} · PT100 보정 {'적용' if h['calibrated'] else '없음'}",
        f"- 객관 라벨({o.get('done_source')}/{o.get('over_source')}): 완료 시작 {_iso_min(o.get('done_start'), q.get('t0'))}"
        f" · 과조리 {_iso_min(o.get('overcooked'), q.get('t0'))}",
        f"- 관능과 차이(관능−객관): 완료 {fmt(ag.get('done_start_diff_s'), '{:+.0f}', '초')} · 과조리 "
        f"{fmt(ag.get('overcooked_diff_s'), '{:+.0f}', '초')} · 맛보기 일치 {ag.get('tastes_agree', 0)}/{ag.get('tastes_compared', 0)}",
    ]
    for f in list(h.get("flags") or []) + list(o.get("flags") or []):
        lines.append(f"- ⚠️ {f}")
    return "\n".join(lines) + "\n"


def _count(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        out[str(it)] = out.get(str(it), 0) + 1
    return out


def qc_markdown(q: dict[str, Any], verify: dict[str, Any] | None = None) -> str:
    s = q["summary"]
    rows = "\n".join(
        f"| {st['stream']} | {st['kind']} | {st['stored']:,} | {st['not_stored']:,} | {st['fps'] or '—'} | {st['max_gap_s'] or '—'} |"
        for st in q["streams"])
    ranges = "\n".join(f"- {k}: {_fmt_range(v)}" for k, v in q["ranges"].items()) or "- (없음)"
    marks = "\n".join(f"- {m['at']} `{m['kind']}`" + (f" = {m['value']}" if m["value"] else "")
                      + (f" — {m['text']}" if m["text"] else "") + (" (사후 입력)" if m["late_entry"] else "")
                      for m in q["marks"]) or "- (Pi 정답 사건 없음 또는 Pi 내보내기 미수집)"
    missing = [k for k in ("done_start", "overcooked") if q["mark_counts"].get(k, 0) == 0]
    v = verify or {}
    af = auto_flags(q, verify)
    auto = "\n".join(f"- ⚠️ {f}" for f in af) or "- 없음"
    return f"""# 세션 QC — {q['session_id']}

자동 생성(`research/tools/soupctl.py qc`). 해석·특이사항은 아래 "관찰"에 사람이 적는다.

## 자동 경고 ({len(af)})
{auto}

- 이름: {q['name'] or '—'} · 상태: {q['state']} · 길이: {q['duration_s']} s
- 저장 {s.get('frames_written', '—')} · 버림 {s.get('frames_dropped', '—')} · 무효 {s.get('frames_invalid', '—')} · {_gb(s.get('bytes_written'))}
- 무결성: {'OK' if v.get('ok') else ('미검증' if not v else '문제 ' + str(len(v.get('problems', []))))} (sha256 {v.get('checked', '—')}건)
- Jetson 사건: {q['events_by_level']}
- 조건(Pi): {q['pi_conditions'] or '—'} · params: {q['pi_params'] or '—'}

| 스트림 | 종류 | 저장 | 미저장 | fps | 최대 간격 s |
|---|---|---|---|---|---|
{rows}

{heating_markdown(q)}
## 값 범위
스칼라는 전체 최소~최대, 배열(열화상)은 10프레임마다 뽑은 프레임 최댓값의 최소~최대.
{ranges}

## 정답 사건(Pi)
{marks}
{'' if not missing else chr(10) + '> ⚠️ 정답 사건 누락: ' + ', '.join(missing) + ' — 라벨 생성 불가 구간이 생긴다.' + chr(10)}
## 관찰
-
"""


def _fmt_range(v) -> str:
    lo, hi = v
    return "—" if lo is None else f"{lo:.2f} ~ {hi:.2f}"


def _gb(n) -> str:
    return "—" if n is None else f"{n / 1e9:.2f} GB"


def catalog_row(q: dict[str, Any], verify: dict[str, Any] | None, review: dict[str, Any] | None = None) -> dict[str, Any]:
    s = q["summary"]
    af = auto_flags(q, verify)
    row = {
        "session_id": q["session_id"], "name": q["name"], "state": q["state"], "duration_s": q["duration_s"],
        "frames_written": s.get("frames_written"), "frames_dropped": s.get("frames_dropped"),
        "bytes_written": s.get("bytes_written"),
        "verified": None if verify is None else verify.get("ok"),
        "streams": len(q["streams"]),
        "conditions": q["pi_conditions"],
    }
    row["review"] = (review or {}).get("verdict")
    row["review_reason"] = (review or {}).get("reason")
    h, o = q.get("heating") or {}, q.get("objective") or {}
    b = h.get("boil") or {}
    r1 = lambda v, n=1: None if v is None else round(v, n)  # noqa: E731
    row.update({
        "boil_onset_min": r1(b.get("onset_s") / 60 if b.get("onset_s") is not None else None),
        "boil_plateau_c": r1(b.get("plateau_c")),
        "ref_reached_min": r1(h.get("t_ref_reached_s") / 60 if h.get("t_ref_reached_s") is not None else None),
        "heat_rate_c_per_min": r1(h.get("rate_c_per_min")),
        "c100_end": r1(h.get("c100_end")),
        "evap_frac_est": r1(h.get("evap_frac"), 3),
        "objective_usable": o.get("usable"),
        "heating_flags": len(h.get("flags") or []) + len(o.get("flags") or []),
        "auto_flag_count": len(af),
        "auto_flags": " | ".join(af)[:500],
        "camera_sharp_median": ";".join(f"{c['stream']}={c['sharp_median']:.0f}" for c in (q.get("camera") or [])
                                        if c.get("sharp_median") is not None),
    })
    for k in DONENESS_MARKS:
        row[f"mark_{k}"] = q["mark_counts"][k]
    for k, v in (q["pi_params"] or {}).items():
        row[f"param_{k}"] = v
    return row
