"""세션 품질 요약(QC)과 카탈로그 행.

입력은 Fedora에 반출·검증된 세션 디렉터리와(있으면) Pi 세션 내보내기 JSON(`pi/<session_id>.json`).
원본을 고치지 않고 숫자만 뽑는다. 해석(좋다/나쁘다)은 사람이 노트에 적는다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .session import ARRAY, IMAGE, SCALAR, Session

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


def session_qc(sess: Session, pi_export: dict[str, Any] | None = None) -> dict[str, Any]:
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
    }


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
    return f"""# 세션 QC — {q['session_id']}

자동 생성(`research/tools/soupctl.py qc`). 해석·특이사항은 아래 "관찰"에 사람이 적는다.

- 이름: {q['name'] or '—'} · 상태: {q['state']} · 길이: {q['duration_s']} s
- 저장 {s.get('frames_written', '—')} · 버림 {s.get('frames_dropped', '—')} · 무효 {s.get('frames_invalid', '—')} · {_gb(s.get('bytes_written'))}
- 무결성: {'OK' if v.get('ok') else ('미검증' if not v else '문제 ' + str(len(v.get('problems', []))))} (sha256 {v.get('checked', '—')}건)
- Jetson 사건: {q['events_by_level']}
- 조건(Pi): {q['pi_conditions'] or '—'} · params: {q['pi_params'] or '—'}

| 스트림 | 종류 | 저장 | 미저장 | fps | 최대 간격 s |
|---|---|---|---|---|---|
{rows}

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


def catalog_row(q: dict[str, Any], verify: dict[str, Any] | None) -> dict[str, Any]:
    s = q["summary"]
    row = {
        "session_id": q["session_id"], "name": q["name"], "state": q["state"], "duration_s": q["duration_s"],
        "frames_written": s.get("frames_written"), "frames_dropped": s.get("frames_dropped"),
        "bytes_written": s.get("bytes_written"),
        "verified": None if verify is None else verify.get("ok"),
        "streams": len(q["streams"]),
        "conditions": q["pi_conditions"],
    }
    for k in DONENESS_MARKS:
        row[f"mark_{k}"] = q["mark_counts"][k]
    for k, v in (q["pi_params"] or {}).items():
        row[f"param_{k}"] = v
    return row
