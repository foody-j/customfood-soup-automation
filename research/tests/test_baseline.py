"""기준 모델 평가 틀 검증 — 정답 구조를 아는 합성 데이터셋으로."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from soupdata.baseline import alert_time, build_features, evaluate, learning_curve, summarize

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import soupctl  # noqa: E402


def synth_session(sid: str, rate: float, seed: int, over_after_min: float = 8.0, extra_min: float = 6.0) -> pd.DataFrame:
    """rate ℃/분으로 20→100 ℃, 끓기 시작 = 완료 시작, 끓은 뒤 over_after_min분 = 과조리 시작.
    열화상 최고는 PT100보다 낮고(표면) 끓은 뒤 김에 가려 서서히 떨어진다 — 비접촉으로도 끓음이 보이게."""
    rng = np.random.default_rng(seed)
    t_boil = 80.0 / rate * 60.0
    t_over = t_boil + over_after_min * 60.0
    t = np.arange(1.0, t_over + extra_min * 60.0, 1.0)
    pt = np.minimum(20 + rate * t / 60.0, 100.0) + rng.normal(0, 0.2, len(t))
    steam = np.clip((t - t_boil) / 300.0, 0, 1) * 8.0
    th_max = np.minimum(18 + rate * t / 60.0 * 0.9, 88.0) - steam + rng.normal(0, 0.8, len(t))
    label = np.where(t < t_boil, "undercooked", np.where(t < t_over, "done", "overcooked"))
    bd = np.minimum(np.abs(t - t_boil), np.abs(t - t_over))
    return pd.DataFrame({
        "session_id": sid, "t_utc": None, "elapsed_s": t, "label": label, "label_sensory": None, "boundary_dist_s": bd,
        "pt100_c": pt, "pt100_cal_c": None, "c100_cum": np.nan, "min_since_boil": np.where(t >= t_boil, (t - t_boil) / 60, np.nan),
        "evap_frac_est": np.nan, "thermal_max_c": th_max, "thermal_mean_c": th_max - 25, "thermal_p95_c": th_max - 5,
        "param_heat_level": rate, "param_lid_initial": "off", "param_taster": "YJ"})


def tables(n=6):
    rates = [6, 8, 10, 12, 14, 16][:n]
    return {f"s{i}": synth_session(f"s{i}", r, seed=i) for i, r in enumerate(rates)}


def test_alert_time_rule():
    t = np.arange(0, 200, 1.0)
    p = np.where(t >= 100, 0.9, 0.1)
    a = alert_time(t, p, threshold=0.5, hold_s=30, ema_s=10)
    assert 100 + 30 <= a <= 100 + 45            # 평활 지연 + 30초 유지 뒤 울림(과거 값만 사용)
    assert alert_time(t, np.full(200, 0.2)) is None


def test_no_probe_features_in_probe_free_tracks():
    df = synth_session("x", 10, 0)
    for track in ("trivial", "thermal"):
        cols = build_features(df, track).columns
        assert not any(c.startswith(("pt100", "c100", "min_since_boil", "evap")) for c in cols)
    assert "param_taster" not in build_features(df, "trivial").columns
    assert any(c.startswith("pt100") for c in build_features(df, "probe").columns)
    # 창 특징은 과거만: 첫 행의 30초 기울기는 비어 있어야 한다
    f = build_features(df, "thermal")
    assert np.isnan(f["thermal_max_d30"].iloc[0]) and not np.isnan(f["thermal_max_d30"].iloc[40])


def test_loso_never_trains_on_test_session_and_metrics_make_sense():
    tb = tables()
    seen = []
    res = evaluate(tb, "probe", fit_hook=lambda test, train: seen.append((test, train)))
    assert len(res) == 6 and all(test not in train for test, train in seen)
    s = summarize(res)
    assert s["macro_f1"] > 0.8 and s["done_abs_err_median_s"] < 120   # 탐침으로는 끓기 시작이 쉽게 보인다
    th = summarize(evaluate(tb, "thermal"))
    assert th["folds"] == 6 and th["macro_f1"] is not None
    with pytest.raises(ValueError):
        build_features(tb["s0"], "camera")


def test_learning_curve_and_cli(tmp_path, data_root):
    tb = tables(5)
    lc = learning_curve(tb, "probe", [2, 4], repeats=2)
    assert [r["train_sessions"] for r in lc] == [2, 4]
    vdir = data_root / "datasets" / "vsyn" / "sessions"
    vdir.mkdir(parents=True)
    for sid, df in tb.items():
        df.to_parquet(vdir / f"{sid}.parquet", index=False)
    (vdir.parent / "summary.json").write_text(json.dumps({"label_rules": {"done_start": "boil"}}))
    assert soupctl.main(["baseline", "vsyn", "--tracks", "trivial,thermal", "--train-sizes", "2"]) == 0
    out = next((data_root / "results" / "vsyn").iterdir())
    rep = (out / "report.md").read_text()
    assert "trivial" in rep and "thermal" in rep and "학습 세션 수 곡선" in rep
    m = json.loads((out / "metrics.json").read_text())
    assert m["summary"]["thermal"]["folds"] == 5
    assert soupctl.main(["baseline", "없는버전"]) == 1
