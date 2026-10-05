import numpy as np
import pytest

from soupdata.stats import (bland_altman, bootstrap_ci, condition_effects, fit_first_order, holm, paired_diff_ci,
                            replicate_stats, weighted_kappa, wilcoxon_paired)


def test_bootstrap_ci_contains_truth_and_handles_edge_cases():
    rng = np.random.default_rng(1)
    v = rng.normal(100, 10, 30)
    ci = bootstrap_ci(v, B=1000)
    assert ci["n"] == 30 and ci["lo"] < np.median(v) < ci["hi"] and ci["lo"] < 100 < ci["hi"]
    assert bootstrap_ci([None, float("nan")])["est"] is None
    assert bootstrap_ci([5.0])["lo"] is None
    assert bootstrap_ci(v, seed=3) == bootstrap_ci(v, seed=3)          # 고정 시드 → 재현


def test_paired_tests_and_holm():
    rng = np.random.default_rng(2)
    base = rng.uniform(100, 200, 16)
    better = base - 40 + rng.normal(0, 5, 16)
    w = wilcoxon_paired(better, base, alternative="less")
    assert w["n"] == 16 and w["p"] < 0.001 and w["median_diff"] < 0
    d = paired_diff_ci(better, base)
    assert d["hi"] < 0
    assert wilcoxon_paired([1, 2], [1, 2])["p"] is None                # 차이 0
    adj = holm({"a": 0.01, "b": 0.04, "c": None, "d": 0.03})
    assert adj["c"] is None and adj["a"] == pytest.approx(0.03) and adj["d"] == pytest.approx(0.06)
    assert adj["b"] == pytest.approx(0.06)                              # 단조 보정


def test_bland_altman_and_kappa():
    ref = np.arange(10, 30, 1.0)
    other = ref + 2.0 + np.tile([-0.5, 0.5], 10)
    ba = bland_altman(ref, other)
    assert ba["n"] == 20 and ba["bias"] == pytest.approx(2.0) and ba["loa_low"] < 2 < ba["loa_high"]
    assert ba["bias_ci"][0] < 2.0 < ba["bias_ci"][1]
    y = ["undercooked", "done", "done", "overcooked", "done"]
    assert weighted_kappa(y, y) == pytest.approx(1.0)
    assert weighted_kappa(y, ["overcooked", "done", "done", "undercooked", "done"]) < 0.5
    assert weighted_kappa(["done"], ["done"]) is None


def test_first_order_fit_recovers_parameters():
    t = np.arange(0, 600, 1.0)
    T = 140 - (140 - 20) * np.exp(-t / 900.0)
    f = fit_first_order(t, T + np.random.default_rng(0).normal(0, 0.1, len(t)))
    assert f["ok"] and abs(f["tau_s"] - 900) / 900 < 0.05 and f["r2"] > 0.999
    assert fit_first_order(t[:5], T[:5])["ok"] is False


def test_replicates_and_condition_effects():
    r = replicate_stats([10.0, 11.0, 9.0, None])
    assert r["n"] == 3 and r["mean"] == 10.0 and r["cv_pct"] == pytest.approx(10.0)
    rows = []
    rng = np.random.default_rng(4)
    for heat, base in (("낮음", 14.0), ("중간", 10.0), ("높음", 7.0)):
        for lid in ("엶", "덮음"):
            for _ in range(3):
                rows.append({"heat": heat, "lid": lid, "water_ml": 0, "boil_min": base + (0.5 if lid == "엶" else 0) + rng.normal(0, 0.3)})
    ce = condition_effects(rows, "boil_min")
    assert ce["n"] == 18 and ce["factors"]["heat"]["kruskal_p"] < 0.01
    assert ce["heat_spearman"]["rho"] < -0.8                           # 출력이 높을수록 빨리 끓음
    assert "C(heat)" in ce["ols_anova"] and ce["ols_anova"]["C(heat)"]["p"] < 0.001
    assert ce["factors"]["water_ml"]["kruskal_p"] is None              # 한 수준뿐
