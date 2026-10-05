"""세션 → 1 Hz 학습 표, 여러 세션 → 데이터셋 버전.

표 한 줄 = Jetson 시계 1초:
`session_id, t_utc, elapsed_s, label, label_sensory, boundary_dist_s, pt100_c, pt100_cal_c, c100_cum, min_since_boil,
 evap_frac_est, thermal_max_c, thermal_mean_c, thermal_p95_c, rgb_<sensor>_path(세션 디렉터리 기준 상대 경로),
 rgb_feat_<sensor>_<지표>(선명도·밝기·L*a*b*·ΔE·움직임, `camera.py`), param_*`.

- **label = 객관 라벨**(D-041: PT100 곡선 + 규칙 `LabelRules`), **label_sensory = Pi 사건(관능) 라벨** — 검증용.
- 값은 그 초 **이전의 가장 가까운 샘플**(허용 간격 안)만 쓴다 — 미래 값을 끌어오지 않는다.
  (단 c100_cum·min_since_boil·evap_frac_est는 그 초까지의 PT100 곡선으로 계산한 누적값이다.)
- PT100 보정은 원본을 바꾸지 않고 `pt100_cal_c = a·pt100_c + b` 열로 따로 둔다(`calibration.json`, 없으면 null).
- **`PROBE_COLUMNS`는 탐침(PT100)에서 나온 열**이다. 정답이 PT100 곡선이므로 "탐침 없는 모델"은 이 열을 입력에서 뺀다.
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

from .heating import HeatingCurve, lid_on_from, mass_from_params
from .labels import LabelRules, agreement, build_objective, build_timeline
from .qc import parse_utc, pi_marks
from .session import ARRAY, IMAGE, SCALAR, Session

PT100 = ("pt100_0", "temp")
THERMAL = ("thermal_0", "temp_array")
MAX_AGE = {"scalar": 2.0, "array": 1.5, "image": 1.0}  # 초 — 이보다 오래된 샘플은 그 초에 쓰지 않는다
#: 탐침(PT100)에서 나온 열 — 정답(D-041)과 같은 출처라 "탐침 없는 모델" 입력에서 뺀다
PROBE_COLUMNS = ("pt100_c", "pt100_cal_c", "c100_cum", "min_since_boil", "evap_frac_est")
#: 정답·식별·경로 열 — 어떤 모델 입력에도 넣지 않는다
NON_FEATURE_COLUMNS = ("session_id", "t_utc", "label", "label_sensory", "boundary_dist_s")


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


def session_curve(sess: Session, t0: datetime, pi_export: dict[str, Any] | None, calibration: dict[str, Any] | None,
                  rules: LabelRules) -> HeatingCurve | None:
    """세션의 PT100 곡선(보정·질량·뚜껑·규칙 반영). PT100 스트림이 없으면 None."""
    if f"{PT100[0]}/{PT100[1]}" not in {r.rel for r in sess.streams()}:
        return None
    pts = [(parse_utc(t), v) for t, v in sess.scalars(*PT100) if v is not None]
    pts = [(t, v) for t, v in pts if t is not None]
    params = ((pi_export or {}).get("session") or {}).get("params") or {}
    return HeatingCurve([t for t, _ in pts], [v for _, v in pts], t0, calibration=(calibration or {}).get(PT100[0]),
                        mass_kg=mass_from_params(params), lid_on=lid_on_from(params, pi_marks(pi_export)),
                        ref_c=rules.ref_c, hold_s=rules.hold_s, min_boil_c=rules.min_boil_c, z=rules.z)


CAMERA_METRICS = ("sharp", "bright", "L", "a", "b", "dE", "motion")


def session_table(sess: Session, pi_export: dict[str, Any] | None, calibration: dict[str, Any] | None = None,
                  offset_s: float | None = None, rules: LabelRules | None = None,
                  camera_features: bool = True) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(행 목록, 세션 정보). 행이 없으면 정보의 `skipped`에 이유."""
    rules = rules or LabelRules()
    info: dict[str, Any] = {"session_id": sess.session_id}
    win = _session_window(sess)
    if win is None:
        return [], {**info, "skipped": "running/stop 단계 시각 없음"}
    t0, t1 = win
    sensory = build_timeline(pi_export, offset_s)
    curve = session_curve(sess, t0, pi_export, calibration, rules)
    if curve is not None:
        obj = build_objective(curve, rules, sensory)
        info["heating"] = curve.summary().to_dict()
    else:
        from .labels import ObjectiveTimeline

        obj = ObjectiveTimeline(None, None, rules.done_start, rules.overcooked, ["PT100 스트림 없음 — 객관 라벨 없음"])
    info["labels"] = {"objective": obj.to_dict(), "sensory": sensory.to_dict(), "agreement": agreement(obj, sensory),
                      "rules": rules.to_dict()}
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
    cam_feats: dict[str, dict[str, _Series]] = {}
    if camera_features:
        from .camera import camera_series

        for r in refs.values():
            if r.kind != IMAGE:
                continue
            ser = camera_series(sess, r.sensor_id, r.stream_id, t0, step_s=1.0)
            cam_feats[r.sensor_id] = {m: _Series([(t0 + timedelta(seconds=x["sec"]), x[m]) for x in ser if x.get(m) is not None])
                                      for m in CAMERA_METRICS}
    cal = (calibration or {}).get(PT100[0]) or {}
    a, b = cal.get("a"), cal.get("b")
    params = ((pi_export or {}).get("session") or {}).get("params") or {}

    rows = []
    start = t0.replace(microsecond=0) + timedelta(seconds=1)
    n = int((t1 - start).total_seconds()) + 1
    for k in range(max(n, 0)):
        t = start + timedelta(seconds=k)
        p = pt.at(t, MAX_AGE["scalar"]) if pt else None
        s = th.at(t, MAX_AGE["array"]) if th else None
        sec = (t - t0).total_seconds()
        row = {
            "session_id": sess.session_id,
            "t_utc": t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "elapsed_s": round(sec, 3),
            "label": obj.label_at(t),
            "label_sensory": sensory.label_at(t),
            "boundary_dist_s": obj.boundary_dist_s(t),
            "pt100_c": p,
            "pt100_cal_c": (a * p + b) if p is not None and a is not None and b is not None else None,
            "c100_cum": curve.c100_at(sec) if curve else None,
            "min_since_boil": curve.since_boil_min(sec) if curve else None,
            "evap_frac_est": curve.evap_frac_at(sec) if curve else None,
            "thermal_max_c": s[0] if s else None,
            "thermal_mean_c": s[1] if s else None,
            "thermal_p95_c": s[2] if s else None,
        }
        for cam, ser in sorted(cams.items()):
            row[f"rgb_{cam}_path"] = ser.at(t, MAX_AGE["image"])
        for cam, feats in sorted(cam_feats.items()):
            for m, ser in feats.items():
                row[f"rgb_feat_{cam}_{m}"] = ser.at(t, 2.0)
        for key, val in params.items():
            row[f"param_{key}"] = val
        rows.append(row)
    info["rows"] = len(rows)
    info["labeled_rows"] = sum(1 for r in rows if r["label"])
    info["labeled_rows_sensory"] = sum(1 for r in rows if r["label_sensory"])
    return rows, info


def split_of(session_id: str, ratios: tuple[float, float] = (0.7, 0.15)) -> str:
    """세션 ID 해시로 train/val/test를 결정적으로 정한다."""
    h = int(hashlib.sha256(session_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "train" if h < ratios[0] else ("val" if h < ratios[0] + ratios[1] else "test")


def load_rules(data_root: Path, override: dict[str, Any] | None = None) -> LabelRules:
    """`label_rules.json`(없으면 기본) 위에 `override`를 덮는다."""
    from .session import read_json

    base = read_json(data_root / "label_rules.json") or {}
    return LabelRules.from_dict({**base, **(override or {})})


def build_dataset(data_root: Path, version: str, session_ids: list[str] | None = None,
                  rules_override: dict[str, Any] | None = None, require_review: bool = False,
                  camera_features: bool = True) -> dict[str, Any]:
    """`data_root/datasets/<version>/`에 세션별 parquet·splits.json·summary.json을 쓴다. 이미 있으면 거부.

    세션 판정(`review.jsonl`)이 drop·hold면 뺀다. `require_review`면 판정이 use인 세션만 쓴다.
    """
    import pandas as pd

    from .review import latest_reviews
    from .session import read_json

    out = data_root / "datasets" / version
    if out.exists():
        raise FileExistsError(f"이미 있는 데이터셋 버전: {out} — 새 버전 이름을 쓴다(동결된 버전은 덮어쓰지 않음)")
    raw = data_root / "raw"
    ids = session_ids or sorted(p.name for p in raw.iterdir() if p.is_dir() and not p.name.startswith("."))
    calibration = read_json(data_root / "calibration.json")
    rules = load_rules(data_root, rules_override)
    reviews = latest_reviews(data_root)
    (out / "sessions").mkdir(parents=True)
    infos, splits = [], {"train": [], "val": [], "test": []}
    unreviewed = []
    for sid in ids:
        ver = read_json(data_root / "verify" / f"{sid}.json")
        if not ver or not ver.get("ok"):
            infos.append({"session_id": sid, "skipped": "Fedora 검증 OK 아님"})
            continue
        rv = reviews.get(sid)
        if rv and rv["verdict"] in ("drop", "hold"):
            infos.append({"session_id": sid, "skipped": f"판정 {rv['verdict']}: {rv.get('reason')}"})
            continue
        if rv is None:
            if require_review:
                infos.append({"session_id": sid, "skipped": "판정 없음(--require-review)"})
                continue
            unreviewed.append(sid)
        rows, info = session_table(Session(raw / sid), read_json(data_root / "pi" / f"{sid}.json"), calibration,
                                   rules=rules, camera_features=camera_features)
        if not rows:
            infos.append(info)
            continue
        pd.DataFrame(rows).to_parquet(out / "sessions" / f"{sid}.parquet", index=False)
        sp = split_of(sid)
        splits[sp].append(sid)
        infos.append({**info, "split": sp})
    labels: dict[str, int] = {}
    labels_s: dict[str, int] = {}
    for f in (out / "sessions").glob("*.parquet"):
        df = pd.read_parquet(f, columns=["label", "label_sensory"])
        for col, acc in (("label", labels), ("label_sensory", labels_s)):
            for k, v in df[col].fillna("none").value_counts().items():
                acc[k] = acc.get(k, 0) + int(v)
    summary = {"version": version, "built_from": str(raw), "calibration": calibration, "label_rules": rules.to_dict(),
               "probe_columns": list(PROBE_COLUMNS), "non_feature_columns": list(NON_FEATURE_COLUMNS),
               "splits": splits, "label_counts_rows": labels, "label_counts_rows_sensory": labels_s,
               "unreviewed_sessions": unreviewed, "sessions": infos}
    (out / "splits.json").write_text(json.dumps(splits, ensure_ascii=False, indent=2))
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return summary
