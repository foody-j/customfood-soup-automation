"""논문 통계 (분석 계획서 §6~§8) — 세션 단위로 계산한다(같은 세션의 프레임은 독립 표본이 아니다).

- `bootstrap_ci`: 세션 값의 통계량(기본 중앙값) 95% 신뢰구간 — 세션을 복원 추출(B = 2000, 고정 시드).
- `paired_diff_ci`, `wilcoxon_paired`, `holm`: 두 모델의 세션별 오차 비교(차이의 CI, 부호순위 검정, 다중 비교 보정).
- `bland_altman`: 두 측정(관능 vs 객관 완료 시각)의 편향·95% 일치 한계.
- `weighted_kappa`: 순서형 3단계 판정 일치(2차 가중 Cohen's κ).
- `fit_first_order`: 끓기 전 가열 곡선 T(t) = T∞ − (T∞ − T₀)·e^(−t/τ) 적합.
- `replicate_stats`, `condition_effects`: 반복 재현성(CV)과 조건 효과(Kruskal–Wallis·Spearman, 보조로 OLS 유형 II 분산분석).
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

import numpy as np

HEAT_ORDER = {"낮음": 1, "중간": 2, "높음": 3, "low": 1, "mid": 2, "medium": 2, "high": 3}


def _clean(x: Sequence[float | None]) -> np.ndarray:
    a = np.array([np.nan if v is None else v for v in x], dtype=float)
    return a[np.isfinite(a)]


def bootstrap_ci(values: Sequence[float | None], stat: Callable = np.median, B: int = 2000, alpha: float = 0.05,
                 seed: int = 0) -> dict[str, Any]:
    v = _clean(values)
    if len(v) == 0:
        return {"n": 0, "est": None, "lo": None, "hi": None}
    if len(v) == 1:
        return {"n": 1, "est": float(stat(v)), "lo": None, "hi": None}
    rng = np.random.default_rng(seed)
    boots = np.array([stat(v[rng.integers(0, len(v), len(v))]) for _ in range(B)])
    return {"n": int(len(v)), "est": float(stat(v)), "lo": float(np.quantile(boots, alpha / 2)),
            "hi": float(np.quantile(boots, 1 - alpha / 2))}


def paired_diff_ci(a: Sequence[float | None], b: Sequence[float | None], stat: Callable = np.median, **kw) -> dict[str, Any]:
    """세션별 (a − b)의 통계량과 CI. 두 값이 모두 있는 세션만 쓴다."""
    aa = np.array([np.nan if v is None else v for v in a], float)
    bb = np.array([np.nan if v is None else v for v in b], float)
    m = np.isfinite(aa) & np.isfinite(bb)
    return bootstrap_ci(aa[m] - bb[m], stat=stat, **kw)


def wilcoxon_paired(a: Sequence[float | None], b: Sequence[float | None], alternative: str = "two-sided") -> dict[str, Any]:
    """세션별 짝지은 Wilcoxon 부호순위 검정. alternative='less'면 a < b(예: 새 모델 오차가 더 작다)."""
    from scipy.stats import wilcoxon

    aa = np.array([np.nan if v is None else v for v in a], float)
    bb = np.array([np.nan if v is None else v for v in b], float)
    m = np.isfinite(aa) & np.isfinite(bb)
    d = aa[m] - bb[m]
    out: dict[str, Any] = {"n": int(m.sum()), "median_diff": float(np.median(d)) if len(d) else None,
                           "alternative": alternative, "statistic": None, "p": None}
    if len(d) < 2 or np.allclose(d, 0):
        return out
    res = wilcoxon(aa[m], bb[m], alternative=alternative, zero_method="wilcox")
    out.update(statistic=float(res.statistic), p=float(res.pvalue))
    return out


def holm(pvals: dict[str, float | None]) -> dict[str, float | None]:
    """Holm–Bonferroni 보정 p값(단조 보정). None은 그대로 둔다."""
    items = sorted(((k, p) for k, p in pvals.items() if p is not None), key=lambda kv: kv[1])
    m, out, running = len(items), {k: None for k in pvals}, 0.0
    for i, (k, p) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        out[k] = running
    return out


def bland_altman(reference: Sequence[float | None], other: Sequence[float | None]) -> dict[str, Any]:
    """차이 = other − reference. 편향(평균 차이)·SD·95% 일치 한계·편향의 95% CI(t 분포)·그림용 평균/차이 배열."""
    from scipy.stats import t as tdist

    r = np.array([np.nan if v is None else v for v in reference], float)
    o = np.array([np.nan if v is None else v for v in other], float)
    m = np.isfinite(r) & np.isfinite(o)
    d, mean = o[m] - r[m], (o[m] + r[m]) / 2
    n = int(m.sum())
    if n == 0:
        return {"n": 0}
    bias = float(d.mean())
    sd = float(d.std(ddof=1)) if n > 1 else None
    out = {"n": n, "bias": bias, "sd": sd, "means": mean.tolist(), "diffs": d.tolist()}
    if sd is not None:
        half = float(tdist.ppf(0.975, n - 1) * sd / np.sqrt(n))
        out.update(loa_low=bias - 1.96 * sd, loa_high=bias + 1.96 * sd, bias_ci=(bias - half, bias + half))
    return out


def weighted_kappa(y1: Sequence[str], y2: Sequence[str], labels: Sequence[str] = ("undercooked", "done", "overcooked")) -> float | None:
    from sklearn.metrics import cohen_kappa_score

    pairs = [(a, b) for a, b in zip(y1, y2) if a in labels and b in labels]
    if len(pairs) < 2 or len({p for pair in pairs for p in pair}) < 2:
        return None
    a, b = zip(*pairs)
    return float(cohen_kappa_score(a, b, labels=list(labels), weights="quadratic"))


def fit_first_order(t_s: Sequence[float], temp: Sequence[float]) -> dict[str, Any]:
    """T(t) = T∞ − (T∞ − T₀)·e^(−t/τ). 끓기 전 구간만 넣는다. 정속 가열에 가까우면 τ가 매우 크게(거의 직선) 나온다."""
    from scipy.optimize import curve_fit

    t = np.asarray(t_s, float)
    y = np.asarray(temp, float)
    ok = np.isfinite(t) & np.isfinite(y)
    t, y = t[ok], y[ok]
    if len(t) < 20 or y.max() - y.min() < 10:
        return {"ok": False, "reason": "구간이 짧거나 온도 변화가 작음"}
    t = t - t[0]

    def model(tt, tinf, t0, tau):
        return tinf - (tinf - t0) * np.exp(-tt / tau)

    p0 = (y.max() + 30.0, y[0], max(t[-1], 1.0))
    try:
        (tinf, t0, tau), _ = curve_fit(model, t, y, p0=p0, bounds=([y.max(), -50.0, 1.0], [1000.0, 150.0, 1e6]), maxfev=20000)
    except Exception as exc:
        return {"ok": False, "reason": f"적합 실패: {exc}"}
    pred = model(t, tinf, t0, tau)
    ss_res, ss_tot = float(((y - pred) ** 2).sum()), float(((y - y.mean()) ** 2).sum())
    return {"ok": True, "T_inf": float(tinf), "T0": float(t0), "tau_s": float(tau), "r2": 1 - ss_res / ss_tot if ss_tot else None,
            "initial_rate_c_per_min": float((tinf - t0) / tau * 60.0)}


def replicate_stats(values: Sequence[float | None]) -> dict[str, Any]:
    v = _clean(values)
    if len(v) == 0:
        return {"n": 0}
    mean = float(v.mean())
    sd = float(v.std(ddof=1)) if len(v) > 1 else None
    return {"n": int(len(v)), "mean": mean, "sd": sd, "cv_pct": (sd / mean * 100) if sd is not None and mean else None,
            "min": float(v.min()), "max": float(v.max())}


def condition_effects(rows: list[dict[str, Any]], response: str, factors: Sequence[str] = ("heat", "water_ml", "lid")) -> dict[str, Any]:
    """요인별 Kruskal–Wallis(집단 2개 이상), 출력 수준 Spearman, 보조 OLS 유형 II 분산분석(표본이 충분할 때)."""
    from scipy.stats import kruskal, spearmanr

    data = [r for r in rows if r.get(response) is not None and np.isfinite(r[response])]
    out: dict[str, Any] = {"response": response, "n": len(data), "factors": {}}
    for f in factors:
        groups: dict[Any, list[float]] = {}
        for r in data:
            if r.get(f) is not None:
                groups.setdefault(r[f], []).append(float(r[response]))
        summ = {str(k): {"n": len(v), "median": float(np.median(v)), "mean": float(np.mean(v))} for k, v in groups.items()}
        res: dict[str, Any] = {"groups": summ, "kruskal_H": None, "kruskal_p": None}
        valid = [v for v in groups.values() if len(v) >= 1]
        if len(valid) >= 2 and sum(len(v) for v in valid) > len(valid):
            try:
                kh = kruskal(*valid)
                res.update(kruskal_H=float(kh.statistic), kruskal_p=float(kh.pvalue))
            except ValueError:
                pass
        out["factors"][f] = res
    heat = [(HEAT_ORDER.get(r.get("heat"), r.get("heat")), r[response]) for r in data if r.get("heat") is not None]
    heat = [(float(h), float(y)) for h, y in heat if isinstance(h, (int, float))]
    if len(heat) >= 3 and len({h for h, _ in heat}) >= 2:
        rho = spearmanr([h for h, _ in heat], [y for _, y in heat])
        out["heat_spearman"] = {"rho": float(rho.statistic), "p": float(rho.pvalue), "n": len(heat)}
    present = [f for f in factors if len({r.get(f) for r in data if r.get(f) is not None}) >= 2]
    if present and len(data) >= len(present) * 2 + 3:
        try:
            import pandas as pd
            import statsmodels.api as sm
            import statsmodels.formula.api as smf

            df = pd.DataFrame([{**{f: str(r.get(f)) for f in present}, "y": float(r[response])} for r in data])
            fit = smf.ols("y ~ " + " + ".join(f"C({f})" for f in present), data=df).fit()
            table = sm.stats.anova_lm(fit, typ=2)
            out["ols_anova"] = {idx: {"F": (None if not np.isfinite(row["F"]) else float(row["F"])),
                                      "p": (None if not np.isfinite(row["PR(>F)"]) else float(row["PR(>F)"]))}
                                for idx, row in table.iterrows() if idx != "Residual"}
            out["ols_r2"] = float(fit.rsquared)
        except Exception as exc:  # 표본이 작으면 특이 행렬 등 — 보조 분석이라 멈추지 않는다
            out["ols_error"] = str(exc)
    return out
