"""세션 → 1 Hz 학습 표, 여러 세션 → 데이터셋 버전.

표 한 줄 = Jetson 시계 1초:
`session_id, t_utc, elapsed_s, label, pt100_c, pt100_cal_c, thermal_max_c, thermal_mean_c, thermal_p95_c,
 rgb_<sensor>_path(세션 디렉터리 기준 상대 경로), 조건(param_*)`.

- 값은 그 초 **이전의 가장 가까운 샘플**(허용 간격 안)만 쓴다 — 미래 값을 끌어오지 않는다.
- PT100 보정은 원본을 바꾸지 않고 `pt100_cal_c = a·pt100_c + b` 열로 따로 둔다(`calibration.json`, 없으면 null).
- 분할은 **세션 단위**(같은 세션이 train·test에 섞이지 않음). 세션 ID 해시로 결정적이다.
"""

from __future__ import annotations

import bisect
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .labels import build_timeline
from .qc import parse_utc
from .session import ARRAY, IMAGE, SCALAR, Session

PT100 = ("pt100_0", "temp")
THERMAL = ("thermal_0", "temp_array")
MAX_AGE = {"scalar": 2.0, "array": 1.5, "image": 1.0}  # 초 — 이보다 오래된 샘플은 그 초에 쓰지 않는다


class _Series:
    """시각 정렬된 (t, 값) — t 이전 가장 가까운 값을 찾는다."""

    def __init__(self, items: list[tuple[datetime, Any]]):
        items.sort(key=lambda x: x[0])
        self.ts = [t.timestamp() for t, _ in items]
        self.vals = [v for _, v in items]

    def at(self, t: datetime, max_age: float) -> Any:
        i = bisect.bisect_right(self.ts, t.timestamp()) - 1
        if i < 0 or t.timestamp() - self.ts[i] > max_age:
            return None
        return self.vals[i]


def _session_window(sess: Session) -> tuple[datetime, datetime] | None:
    ph = sess.meta.get("phases") or {}
    t0 = parse_utc(ph.get("running"))
    t1 = parse_utc(ph.get("stop_requested") or ph.get("stopping") or ph.get("files_closed"))
    return (t0, t1) if t0 and t1 and t1 > t0 else None


def session_table(sess: Session, pi_export: dict[str, Any] | None, calibration: dict[str, Any] | None = None,
                  offset_s: float | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(행 목록, 세션 정보). 행이 없으면 정보의 `skipped`에 이유."""
    info: dict[str, Any] = {"session_id": sess.session_id}
    win = _session_window(sess)
    if win is None:
        return [], {**info, "skipped": "running/stop 단계 시각 없음"}
    tl = build_timeline(pi_export, offset_s)
    info["labels"] = tl.to_dict()
    refs = {r.rel: r for r in sess.streams()}

    pt = None
    if f"{PT100[0]}/{PT100[1]}" in refs:
        pt = _Series([(parse_utc(t), v) for t, v in sess.scalars(*PT100) if v is not None])
    th = None
    if f"{THERMAL[0]}/{THERMAL[1]}" in refs:
        stats = []
        for ln, arr in sess.iter_arrays(*THERMAL):
            a = np.asarray(arr, dtype=np.float64)
            stats.append((parse_utc(ln["host_recv_utc"]),
                          (float(np.nanmax(a)), float(np.nanmean(a)), float(np.nanpercentile(a, 95)))))
        th = _Series(stats)
    cams = {r.sensor_id: _Series([(parse_utc(ln["host_recv_utc"]), ln["path"]) for ln in sess.stored(r.sensor_id, r.stream_id)])
            for r in refs.values() if r.kind == IMAGE}
    cal = (calibration or {}).get(PT100[0]) or {}
    a, b = cal.get("a"), cal.get("b")
    params = ((pi_export or {}).get("session") or {}).get("params") or {}

    rows = []
    t0, t1 = win
    start = t0.replace(microsecond=0) + timedelta(seconds=1)
    n = int((t1 - start).total_seconds()) + 1
    for k in range(max(n, 0)):
        t = start + timedelta(seconds=k)
        p = pt.at(t, MAX_AGE["scalar"]) if pt else None
        s = th.at(t, MAX_AGE["array"]) if th else None
        row = {
            "session_id": sess.session_id,
            "t_utc": t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "elapsed_s": round((t - t0).total_seconds(), 3),
            "label": tl.label_at(t),
            "pt100_c": p,
            "pt100_cal_c": (a * p + b) if p is not None and a is not None and b is not None else None,
            "thermal_max_c": s[0] if s else None,
            "thermal_mean_c": s[1] if s else None,
            "thermal_p95_c": s[2] if s else None,
        }
        for cam, ser in sorted(cams.items()):
            row[f"rgb_{cam}_path"] = ser.at(t, MAX_AGE["image"])
        for key, val in params.items():
            row[f"param_{key}"] = val
        rows.append(row)
    info["rows"] = len(rows)
    info["labeled_rows"] = sum(1 for r in rows if r["label"])
    return rows, info


def split_of(session_id: str, ratios: tuple[float, float] = (0.7, 0.15)) -> str:
    """세션 ID 해시로 train/val/test를 결정적으로 정한다."""
    h = int(hashlib.sha256(session_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "train" if h < ratios[0] else ("val" if h < ratios[0] + ratios[1] else "test")


def build_dataset(data_root: Path, version: str, session_ids: list[str] | None = None) -> dict[str, Any]:
    """`data_root/datasets/<version>/`에 세션별 parquet·splits.json·summary.json을 쓴다. 이미 있으면 거부."""
    import pandas as pd

    from .session import read_json

    out = data_root / "datasets" / version
    if out.exists():
        raise FileExistsError(f"이미 있는 데이터셋 버전: {out} — 새 버전 이름을 쓴다(동결된 버전은 덮어쓰지 않음)")
    raw = data_root / "raw"
    ids = session_ids or sorted(p.name for p in raw.iterdir() if p.is_dir() and not p.name.startswith("."))
    calibration = read_json(data_root / "calibration.json")
    (out / "sessions").mkdir(parents=True)
    infos, splits = [], {"train": [], "val": [], "test": []}
    for sid in ids:
        ver = read_json(data_root / "verify" / f"{sid}.json")
        if not ver or not ver.get("ok"):
            infos.append({"session_id": sid, "skipped": "Fedora 검증 OK 아님"})
            continue
        rows, info = session_table(Session(raw / sid), read_json(data_root / "pi" / f"{sid}.json"), calibration)
        if not rows:
            infos.append(info)
            continue
        pd.DataFrame(rows).to_parquet(out / "sessions" / f"{sid}.parquet", index=False)
        sp = split_of(sid)
        splits[sp].append(sid)
        infos.append({**info, "split": sp})
    labels: dict[str, int] = {}
    for f in (out / "sessions").glob("*.parquet"):
        for k, v in pd.read_parquet(f, columns=["label"])["label"].fillna("none").value_counts().items():
            labels[k] = labels.get(k, 0) + int(v)
    summary = {"version": version, "built_from": str(raw), "calibration": calibration, "splits": splits,
               "label_counts_rows": labels, "sessions": infos}
    (out / "splits.json").write_text(json.dumps(splits, ensure_ascii=False, indent=2))
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return summary
