"""논문용 표·그림 생성기 (분석 계획서 §10) — `research/soup paper <버전>`.

데이터셋 버전(parquet·summary)과 기록(검증·판정)에서 표 5개와 그림 9개를 만들고 출처 도장을 함께 남긴다.
데이터가 늘면 다시 돌리면 된다. 그림은 PNG(300 dpi)와 PDF, 표는 CSV·Markdown·LaTeX. 기본 영어(국제 학술지), `lang="ko"`는 보고서용.

표: T1 데이터셋 요약 · T2 장비·센서(사양은 `paper_meta.json`, 없으면 저장소 문서 값 + TBD) · T3 모델 비교(95% CI·검정)
    · T4 조건별 가열 특성(+ 조건 효과) · T5 라벨 일치(Bland–Altman·κ)
그림: F2 세션 흐름도 · F3 대표 세션 · F4 조건별 가열 곡선 · F5 Bland–Altman · F6 모델 비교 · F7 세션별 알림 오차 · F8 학습 곡선(선택)
    · F9 혼동 행렬 · F10 센서 묶음별 중요도  (F1 시스템·센서 배치는 사진·도식이라 사람이 준비)
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .baseline import CLASSES, CODE, TRACKS, build_features, evaluate, learning_curve, summarize
from .stats import (bland_altman, bootstrap_ci, condition_effects, fit_first_order, holm, replicate_stats,
                    weighted_kappa, wilcoxon_paired)
from .summary import GRID, INK, INK2, MUTED, SURFACE

# 트랙 색 — 기준선(trivial)은 회색으로 물러서게, 나머지는 dataviz 범주 순서 고정
TRACK_COLORS = {"trivial": MUTED, "thermal": "#2a78d6", "camera": "#eb6834", "noprobe": "#1baf7a", "probe": "#eda100", "all": "#e87ba4"}
CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
ORDINAL = {"undercooked": "#86b6ef", "done": "#2a78d6", "overcooked": "#184f95"}  # 파랑 순서형(250·450·600)

TXT = {
    "en": {
        "undercooked": "Undercooked", "done": "Done", "overcooked": "Overcooked", "time_min": "Time since start (min)",
        "temp": "Temperature (°C)", "pt100": "Core (PT100)", "thmax": "Surface max (thermal)", "boil": "Boil onset (auto)",
        "sens_done": "Sensory done", "sens_over": "Sensory overcooked", "motion": "Camera motion (a.u.)",
        "heat": "Heat level", "since_start": "Time since start (min)", "ba_x": "Mean of objective and sensory done onset (min)",
        "ba_y": "Sensory − objective (min)", "bias": "Bias", "loa": "95% LoA", "alert_err": "Done-alert absolute error (s)",
        "macro_f1": "Macro-F1", "signed_err": "Done-alert error (s, + = late)", "train_n": "Training sessions",
        "pred": "Predicted", "true": "True", "importance": "Drop in macro-F1 when permuted", "flow_recorded": "Sessions recorded",
        "flow_integrity": "Integrity verified (SHA-256)", "flow_review": "Reviewed as usable", "flow_dataset": "In dataset",
        "flow_label": "Objective label available", "flow_eval": "Model evaluation (LOSO)", "excluded": "Excluded",
        "unreviewed": "unreviewed", "groups": {"time": "Elapsed time", "conditions": "Conditions", "thermal": "Thermal", "camera": "Camera"},
    },
    "ko": {
        "undercooked": "미완", "done": "완료", "overcooked": "과조리", "time_min": "촬영 시작 후 경과 (분)",
        "temp": "온도 (°C)", "pt100": "중심(PT100)", "thmax": "표면 최고(열화상)", "boil": "끓기 시작(자동)",
        "sens_done": "관능 완료", "sens_over": "관능 과조리", "motion": "카메라 움직임",
        "heat": "출력", "since_start": "촬영 시작 후 경과 (분)", "ba_x": "객관·관능 완료 시작 평균 (분)",
        "ba_y": "관능 − 객관 (분)", "bias": "편향", "loa": "95% 일치 한계", "alert_err": "완료 알림 절대오차 (초)",
        "macro_f1": "Macro-F1", "signed_err": "완료 알림 오차 (초, +는 늦음)", "train_n": "학습 세션 수",
        "pred": "예측", "true": "정답", "importance": "섞었을 때 macro-F1 감소", "flow_recorded": "기록된 세션",
        "flow_integrity": "무결성 확인(SHA-256)", "flow_review": "사용 판정", "flow_dataset": "데이터셋 포함",
        "flow_label": "객관 라벨 있음", "flow_eval": "모델 평가(LOSO)", "excluded": "제외",
        "unreviewed": "미판정", "groups": {"time": "경과 시간", "conditions": "조건", "thermal": "열화상", "camera": "카메라"},
    },
}

DEFAULT_META = [  # 저장소 문서에 근거한 값만 — 모르는 사양은 TBD(논문 전에 사람이 채운다)
    {"component": "RGB camera ×2", "model": "Sensing ISX031F (GMSL2)", "spec": "1920×1536, JPEG frames", "rate": "2–10 fps (per protocol)", "accuracy": "—"},
    {"component": "Thermal array", "model": "Melexis MLX90640 (D55, 55°×35°)", "spec": "32×24 px", "rate": "2 Hz", "accuracy": "TBD"},
    {"component": "Core temperature", "model": "PT100 (3-wire) + MAX31865", "spec": "immersed probe, fixed depth", "rate": "1 Hz", "accuracy": "TBD (2-point calibration)"},
    {"component": "Edge computer", "model": "NVIDIA Jetson Orin Nano Super 8 GB", "spec": "acquisition & storage", "rate": "—", "accuracy": "—"},
    {"component": "Controller", "model": "Raspberry Pi 5", "spec": "experiment control, event logging", "rate": "—", "accuracy": "—"},
]


# ── 공통 ────────────────────────────────────────────────────────────────────────
def _setup(lang: str):
    import logging

    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import font_manager

    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    matplotlib.rcParams.update({"axes.unicode_minus": False, "font.size": 9, "axes.titlesize": 10, "axes.labelsize": 9,
                                "xtick.labelsize": 8, "ytick.labelsize": 8, "legend.fontsize": 8})
    if lang == "ko":
        for name in ("Noto Sans CJK KR", "NanumGothic"):
            if any(f.name == name for f in font_manager.fontManager.ttflist):
                matplotlib.rcParams["font.family"] = name
                break
    else:
        matplotlib.rcParams["font.family"] = "DejaVu Sans"


def _style(ax):
    ax.set_facecolor(SURFACE)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(MUTED)
    ax.tick_params(colors=INK2)
    ax.yaxis.label.set_color(INK2)
    ax.xaxis.label.set_color(INK2)


def _save(fig, out: Path, name: str, lang: str) -> list[str]:
    import matplotlib.pyplot as plt

    if lang == "ko":  # 가변 글꼴만 있을 때 가는 글씨 보정(summary와 같은 방식)
        from matplotlib import patheffects
        from matplotlib.text import Text

        for t in fig.findobj(Text):
            if t.get_text():
                t.set_path_effects([patheffects.withStroke(linewidth=0.5, foreground=t.get_color())])
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for ext in ("png", "pdf"):
        p = out / f"{name}.{ext}"
        fig.savefig(p, dpi=300, facecolor=SURFACE, bbox_inches="tight")
        files.append(p.name)
    plt.close(fig)
    return files


def _write_table(rows: list[dict[str, Any]], out: Path, name: str, caption: str) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    if not rows:
        (out / f"{name}.md").write_text(f"**{caption}**\n\n(데이터 없음)\n", encoding="utf-8")
        return [f"{name}.md"]
    cols = list(rows[0].keys())
    for r in rows[1:]:
        cols += [k for k in r if k not in cols]
    with open(out / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    cell = lambda v: "" if v is None else str(v)  # noqa: E731
    md = [f"**{caption}**", "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    md += ["| " + " | ".join(cell(r.get(c)) for c in cols) + " |" for r in rows]
    (out / f"{name}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    esc = lambda s: re.sub(r"([%&_#$])", r"\\\1", cell(s))  # noqa: E731
    tex = ["\\begin{table}[ht]", "\\centering", f"\\caption{{{esc(caption)}}}", "\\begin{tabular}{" + "l" * len(cols) + "}",
           "\\toprule", " & ".join(esc(c) for c in cols) + " \\\\", "\\midrule"]
    tex += [" & ".join(esc(r.get(c)) for c in cols) + " \\\\" for r in rows]
    tex += ["\\bottomrule", "\\end{tabular}", "\\end{table}"]
    (out / f"{name}.tex").write_text("\n".join(tex) + "\n", encoding="utf-8")
    return [f"{name}.csv", f"{name}.md", f"{name}.tex"]


def _f(v, nd=1):
    return None if v is None or (isinstance(v, float) and not np.isfinite(v)) else round(float(v), nd)


def _ci_txt(ci: dict[str, Any], nd=0) -> str | None:
    if ci.get("est") is None:
        return None
    if ci.get("lo") is None:
        return f"{ci['est']:.{nd}f}"
    return f"{ci['est']:.{nd}f} [{ci['lo']:.{nd}f}, {ci['hi']:.{nd}f}]"


def _t0(df: pd.DataFrame) -> datetime | None:
    if df.empty or not isinstance(df["t_utc"].iloc[0], str):
        return None
    t = datetime.fromisoformat(df["t_utc"].iloc[0].replace("Z", "+00:00"))
    return t - pd.Timedelta(seconds=float(df["elapsed_s"].iloc[0]))


def _iso_min(iso: str | None, t0: datetime | None) -> float | None:
    if not iso or t0 is None:
        return None
    return (datetime.fromisoformat(iso) - t0).total_seconds() / 60.0


def _param(df: pd.DataFrame, key: str):
    col = f"param_{key}"
    if col not in df or df[col].isna().all():
        return None
    v = df[col].dropna().iloc[0]
    return v.item() if hasattr(v, "item") else v


# ── 데이터 모으기 ────────────────────────────────────────────────────────────────
def session_records(tables: dict[str, pd.DataFrame], summary: dict[str, Any]) -> list[dict[str, Any]]:
    """세션별: 조건·가열 특성·객관/관능 완료 시각. 가열 곡선 1차 모델은 끓기 전 PT100으로 적합."""
    infos = {s["session_id"]: s for s in summary.get("sessions", []) if "skipped" not in s}
    recs = []
    for sid, df in tables.items():
        df = df.sort_values("elapsed_s")
        info = infos.get(sid, {})
        h = info.get("heating") or {}
        b = h.get("boil") or {}
        lab = info.get("labels") or {}
        t0 = _t0(df)
        sig = "pt100_cal_c" if "pt100_cal_c" in df and df["pt100_cal_c"].notna().any() else "pt100_c"
        fit = {"ok": False}
        if b.get("onset_s") is not None and sig in df:
            pre = df[df["elapsed_s"] <= b["onset_s"]]
            fit = fit_first_order(pre["elapsed_s"].to_numpy(), pd.to_numeric(pre[sig], errors="coerce").to_numpy())
        recs.append({
            "session_id": sid, "heat": _param(df, "heat_level"), "water_ml": _param(df, "water_added_ml"), "lid": _param(df, "lid_initial"),
            "duration_min": float(df["elapsed_s"].max()) / 60.0,
            "boil_min": None if b.get("onset_s") is None else b["onset_s"] / 60.0, "plateau_c": b.get("plateau_c"),
            "rate": h.get("rate_c_per_min"), "c100": h.get("c100_end"), "tau_s": fit.get("tau_s") if fit.get("ok") else None,
            "obj_done_min": _iso_min((lab.get("objective") or {}).get("done_start"), t0),
            "sens_done_min": _iso_min((lab.get("sensory") or {}).get("done_start"), t0),
            "taste_pairs": (lab.get("agreement") or {}).get("taste_pairs") or [],
        })
    return recs


def session_flow(data_root: Path, summary: dict[str, Any]) -> dict[str, Any]:
    """세션 흐름도 숫자: 기록 → 무결성 → 판정 → 데이터셋 → 객관 라벨. 제외 사유는 판정 이유 앞 코드(E1~E6)로 묶는다."""
    from .review import latest_reviews
    from .session import read_json

    raw = sorted(p.name for p in (data_root / "raw").glob("*") if p.is_dir() and not p.name.startswith("."))
    ok = [s for s in raw if (read_json(data_root / "verify" / f"{s}.json") or {}).get("ok")]
    reviews = latest_reviews(data_root)
    usable = [s for s in ok if (reviews.get(s) or {}).get("verdict", "use") == "use"]
    codes: dict[str, int] = {}
    for s in ok:
        rv = reviews.get(s)
        if rv and rv["verdict"] in ("hold", "drop"):
            m = re.match(r"\s*(E\d)", rv.get("reason") or "")
            key = f"{rv['verdict']}:{m.group(1) if m else 'E6'}"
            codes[key] = codes.get(key, 0) + 1
    in_ds = [s["session_id"] for s in summary.get("sessions", []) if "skipped" not in s]
    labeled = [s["session_id"] for s in summary.get("sessions", [])
               if "skipped" not in s and ((s.get("labels") or {}).get("objective") or {}).get("usable")]
    return {"recorded": len(raw), "integrity_ok": len(ok), "integrity_failed": len(raw) - len(ok), "usable": len(usable),
            "unreviewed": sum(1 for s in ok if s not in reviews), "excluded_by_code": codes, "in_dataset": len(in_ds),
            "objective_labeled": len(labeled), "no_objective_label": len(in_ds) - len(labeled)}


def grouped_importance(tables: dict[str, pd.DataFrame], track: str = "noprobe", target: str = "label", repeats: int = 3,
                       seed: int = 0, train_step: int = 5) -> dict[str, dict[str, float]]:
    """센서 묶음(경과 시간·조건·열화상·카메라)을 통째로 섞었을 때 시험 세션 macro-F1이 얼마나 떨어지는지(LOSO 평균)."""
    from sklearn.metrics import f1_score

    from .baseline import _make_model, _proba

    feats = {sid: build_features(df, track) for sid, df in tables.items()}
    ordered = {sid: df.sort_values("elapsed_s").reset_index(drop=True) for sid, df in tables.items()}
    labeled = [s for s, df in ordered.items() if df[target].notna().any()]
    rng = np.random.default_rng(seed)
    drops: dict[str, list[float]] = {}
    for test in labeled:
        train = [s for s in labeled if s != test]
        if not train:
            continue
        Xs, ys = [], []
        for sid in train:
            m = ordered[sid][target].notna().to_numpy()
            idx = np.where(m)[0][::train_step]
            Xs.append(feats[sid].iloc[idx])
            ys.append(ordered[sid][target].iloc[idx].map(CODE))
        X_tr, y_tr = pd.concat(Xs), pd.concat(ys)
        if y_tr.nunique() < 2:
            continue
        cols = [c for c in X_tr.columns if X_tr[c].notna().any()]
        model = _make_model(seed).fit(X_tr[cols], y_tr)
        m = ordered[test][target].notna().to_numpy()
        X_te = feats[test].reindex(columns=cols)[m].reset_index(drop=True)
        y_te = ordered[test][target][m].map(CODE).to_numpy()
        base = f1_score(y_te, _proba(model, X_te).argmax(1), average="macro", labels=sorted(set(y_te)))
        groups = {"time": [c for c in cols if c == "elapsed_min"], "conditions": [c for c in cols if c.startswith("param_")],
                  "thermal": [c for c in cols if c.startswith("thermal")], "camera": [c for c in cols if c.startswith("rgb_feat_")]}
        for g, gc in groups.items():
            if not gc:
                continue
            vals = []
            for _ in range(repeats):
                Xp = X_te.copy()
                perm = rng.permutation(len(Xp))
                Xp[gc] = Xp[gc].to_numpy()[perm]
                vals.append(base - f1_score(y_te, _proba(model, Xp).argmax(1), average="macro", labels=sorted(set(y_te))))
            drops.setdefault(g, []).append(float(np.mean(vals)))
    return {g: {"mean": float(np.mean(v)), "sd": float(np.std(v, ddof=1)) if len(v) > 1 else 0.0, "n": len(v)} for g, v in drops.items()}


# ── 표 ──────────────────────────────────────────────────────────────────────────
def table_dataset(recs, tables, summary, flow) -> list[dict[str, Any]]:
    dur = replicate_stats([r["duration_min"] for r in recs])
    counts = summary.get("label_counts_rows") or {}
    total = sum(v for k, v in counts.items() if k != "none") or 1
    rows = [
        {"item": "Sessions recorded / in dataset", "value": f"{flow['recorded']} / {flow['in_dataset']}"},
        {"item": "Sessions with objective label", "value": str(flow["objective_labeled"])},
        {"item": "Session duration, min (mean ± SD)", "value": f"{dur.get('mean', 0):.1f} ± {dur.get('sd') or 0:.1f}" if dur.get("n") else "—"},
        {"item": "Seconds per class (1 Hz rows)", "value": ", ".join(f"{k} {counts.get(k, 0)} ({counts.get(k, 0) / total:.0%})" for k in CLASSES)},
        {"item": "Unlabeled rows (boundary / no label)", "value": str(counts.get("none", 0))},
    ]
    conds: dict[str, int] = {}
    for r in recs:
        key = f"heat {r['heat']}, water {r['water_ml']} mL, lid {r['lid']}"
        conds[key] = conds.get(key, 0) + 1
    rows += [{"item": f"Condition: {k}", "value": str(v)} for k, v in sorted(conds.items())]
    rules = summary.get("label_rules") or {}
    rows.append({"item": "Label rule (done / overcooked)", "value": f"{rules.get('done_start')} / {rules.get('overcooked')}"})
    return rows


def table_models(folds: dict[str, list], tracks: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    per = {t: {r.session_id: r for r in folds.get(t, [])} for t in tracks}
    rows, tests = [], {}
    base = per.get("trivial", {})
    pv: dict[str, float | None] = {}
    for t in tracks:
        if t == "trivial" or not base:
            continue
        sids = sorted(set(per[t]) & set(base))
        a = [abs(per[t][s].done_err_s) if per[t][s].done_err_s is not None else None for s in sids]
        b = [abs(base[s].done_err_s) if base[s].done_err_s is not None else None for s in sids]
        alt = "less" if t == "noprobe" else "two-sided"
        w = wilcoxon_paired(a, b, alternative=alt)
        tests[t] = w
        if t != "noprobe":
            pv[t] = w["p"]
    adj = holm(pv)
    for t in tracks:
        rs = folds.get(t, [])
        s = summarize(rs)
        errs = [abs(r.done_err_s) for r in rs if r.done_err_s is not None]
        f1s = [r.macro_f1 for r in rs if r.macro_f1 is not None]
        p = tests.get(t, {}).get("p")
        rows.append({
            "track": t, "sessions": s.get("folds"),
            "done_alert_abs_err_s_median [95% CI]": _ci_txt(bootstrap_ci(errs)),
            "early_alarm_rate": _f(s.get("done_early_rate"), 2), "miss_rate": _f(s.get("done_miss_rate"), 2),
            "macro_F1 [95% CI]": _ci_txt(bootstrap_ci(f1s, stat=np.mean), 2),
            "macro_F1_boundary_excl": _f(s.get("macro_f1_guard"), 2), "ordinal_MAE": _f(s.get("ord_mae"), 2),
            "p_vs_trivial": None if p is None else (f"{p:.3g} (H1, one-sided)" if t == "noprobe" else f"{adj.get(t):.3g} (Holm)"),
        })
    return rows, tests


def table_heating(recs) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    groups: dict[str, list[dict]] = {}
    for r in recs:
        groups.setdefault(f"{r['heat']} / {r['water_ml']} mL / {r['lid']}", []).append(r)
    rows = []
    for k, rs in sorted(groups.items()):
        bs, rt, tau = replicate_stats([r["boil_min"] for r in rs]), replicate_stats([r["rate"] for r in rs]), replicate_stats([r["tau_s"] for r in rs])
        ms = lambda s, nd=1: "—" if not s.get("n") else (f"{s['mean']:.{nd}f}" + (f" ± {s['sd']:.{nd}f}" if s.get("sd") is not None else ""))  # noqa: E731
        rows.append({"condition (heat / water / lid)": k, "n": len(rs), "boil_onset_min": ms(bs), "boil_onset_CV_%": _f(bs.get("cv_pct")),
                     "heating_rate_C_per_min": ms(rt), "tau_s": ms(tau, 0),
                     "plateau_C": ms(replicate_stats([r["plateau_c"] for r in rs])), "C100_end_min": ms(replicate_stats([r["c100"] for r in rs]))})
    eff = {"boil_min": condition_effects(recs, "boil_min"), "rate": condition_effects(recs, "rate")}
    return rows, eff


def table_agreement(recs) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ba = bland_altman([r["obj_done_min"] for r in recs], [r["sens_done_min"] for r in recs])
    pairs = [p for r in recs for p in r["taste_pairs"]]
    agree = sum(1 for p in pairs if p.get("taste") == p.get("objective"))
    kappa = weighted_kappa([p.get("taste") for p in pairs], [p.get("objective") for p in pairs])
    rows = [{"measure": "Sessions with both done onsets", "value": ba.get("n", 0)},
            {"measure": "Bias, sensory − objective (min)", "value": _f(ba.get("bias"), 2)},
            {"measure": "Bias 95% CI (min)", "value": None if "bias_ci" not in ba else f"[{ba['bias_ci'][0]:.2f}, {ba['bias_ci'][1]:.2f}]"},
            {"measure": "95% limits of agreement (min)", "value": None if "loa_low" not in ba else f"[{ba['loa_low']:.2f}, {ba['loa_high']:.2f}]"},
            {"measure": "Taste checks vs objective label: agreement", "value": f"{agree}/{len(pairs)}" if pairs else None},
            {"measure": "Quadratic-weighted Cohen's kappa", "value": _f(kappa, 2)}]
    return rows, ba


# ── 그림 ────────────────────────────────────────────────────────────────────────
def fig_flow(flow: dict[str, Any], out: Path, lang: str) -> list[str]:
    import matplotlib.pyplot as plt

    T = TXT[lang]
    steps = [(T["flow_recorded"], flow["recorded"], None),
             (T["flow_integrity"], flow["integrity_ok"], f"{T['excluded']}: {flow['integrity_failed']}" if flow["integrity_failed"] else None),
             (T["flow_review"], flow["usable"], " · ".join(
                 [f"{T['excluded']} {k} {v}" for k, v in sorted(flow["excluded_by_code"].items())]
                 + ([f"({T['unreviewed']} {flow['unreviewed']})"] if flow["unreviewed"] else [])) or None),
             (T["flow_dataset"], flow["in_dataset"], None),
             (T["flow_label"], flow["objective_labeled"], f"{T['excluded']}: {flow['no_objective_label']}" if flow["no_objective_label"] else None),
             (T["flow_eval"], flow["objective_labeled"], None)]
    fig, ax = plt.subplots(figsize=(6.0, 0.95 * len(steps)), facecolor=SURFACE)
    ax.axis("off")
    for i, (name, n, side) in enumerate(steps):
        y = len(steps) - 1 - i
        ax.add_patch(plt.Rectangle((0.05, y + 0.15), 0.5, 0.6, fill=False, ec=INK2, lw=1))
        ax.text(0.30, y + 0.45, f"{name}\nn = {n}", ha="center", va="center", color=INK, fontsize=8.5)
        if i < len(steps) - 1:
            ax.annotate("", xy=(0.30, y - 1 + 0.75), xytext=(0.30, y + 0.15), arrowprops={"arrowstyle": "->", "color": MUTED})
        if side:
            ax.text(0.62, y + 0.45, side, ha="left", va="center", color=INK2, fontsize=8)
    ax.set_xlim(0, 1.25)
    ax.set_ylim(-0.1, len(steps))
    return _save(fig, out, "F2_session_flow", lang)


def fig_session(df: pd.DataFrame, info: dict[str, Any], out: Path, lang: str) -> list[str]:
    import matplotlib.pyplot as plt

    T = TXT[lang]
    df = df.sort_values("elapsed_s")
    x = df["elapsed_s"].to_numpy() / 60
    cam_cols = [c for c in df.columns if c.startswith("rgb_feat_") and c.endswith("_motion")]
    nrows = 2 if cam_cols else 1
    fig, axes = plt.subplots(nrows, 1, figsize=(7.0, 2.8 + 1.4 * (nrows - 1)), sharex=True, facecolor=SURFACE,
                             gridspec_kw={"height_ratios": [2.2, 1] if nrows == 2 else [1]})
    axes = np.atleast_1d(axes)
    ax = axes[0]
    lab = df["label"].to_numpy()
    for cls in CLASSES:
        m = lab == cls
        if m.any():
            ax.fill_between(x, 0, 1, where=m, transform=ax.get_xaxis_transform(), color=ORDINAL[cls], alpha=0.22, lw=0,
                            label=T[cls])
    sig = "pt100_cal_c" if "pt100_cal_c" in df and df["pt100_cal_c"].notna().any() else "pt100_c"
    ax.plot(x, df[sig], color=INK, lw=1.6, label=T["pt100"])
    if "thermal_max_c" in df:
        ax.plot(x, df["thermal_max_c"], color="#eb6834", lw=1.2, label=T["thmax"])
    b = ((info.get("heating") or {}).get("boil") or {}).get("onset_s")
    if b is not None:
        ax.axvline(b / 60, color=INK2, lw=1, ls=(0, (4, 3)), label=T["boil"])
    sens = df["label_sensory"].to_numpy() if "label_sensory" in df else np.array([None] * len(df))
    for cls, key in (("done", "sens_done"), ("overcooked", "sens_over")):
        idx = np.where(sens == cls)[0]
        if len(idx):
            ax.axvline(x[idx[0]], color=MUTED, lw=1, ls=":", label=T[key])
    ax.set_ylabel(T["temp"])
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.02), frameon=False, ncol=4, borderaxespad=0)  # 그래프 위 바깥 — 곡선을 가리지 않게
    _style(ax)
    if nrows == 2:
        ax2 = axes[1]
        ax2.plot(x, df[cam_cols[0]], color="#2a78d6", lw=1)
        ax2.set_ylabel(T["motion"])
        _style(ax2)
    axes[-1].set_xlabel(T["time_min"])
    return _save(fig, out, "F3_representative_session", lang)


def fig_heating(tables: dict[str, pd.DataFrame], recs, out: Path, lang: str) -> list[str]:
    import matplotlib.pyplot as plt

    T = TXT[lang]
    heat_of = {r["session_id"]: r["heat"] for r in recs}
    def _order(s: str):
        try:
            return (0, float(s), "")
        except ValueError:
            return (1 if s != "None" else 2, 0.0, s)

    levels = sorted({str(v) for v in heat_of.values()}, key=_order)  # 숫자는 숫자 순(6, 8, 10 …), 글자·없음은 뒤
    fig, ax = plt.subplots(figsize=(6.5, 3.4), facecolor=SURFACE)
    grid = np.arange(0, max(float(df["elapsed_s"].max()) for df in tables.values()) + 10, 10.0)
    for li, lv in enumerate(levels):
        color = CAT[li % len(CAT)]
        curves = []
        for sid, df in tables.items():
            if str(heat_of.get(sid)) != lv:
                continue
            sig = "pt100_cal_c" if "pt100_cal_c" in df and df["pt100_cal_c"].notna().any() else "pt100_c"
            d = df.sort_values("elapsed_s")
            y = np.interp(grid, d["elapsed_s"], pd.to_numeric(d[sig], errors="coerce").ffill(), right=np.nan)
            curves.append(y)
            ax.plot(grid / 60, y, color=color, lw=0.6, alpha=0.35)
        if curves:
            c = np.vstack(curves)
            with np.errstate(all="ignore"):
                ax.plot(grid / 60, np.nanmedian(c, 0), color=color, lw=2, label=f"{T['heat']} {lv} (n={len(curves)})")
                if len(curves) > 1:
                    ax.fill_between(grid / 60, np.nanpercentile(c, 25, 0), np.nanpercentile(c, 75, 0), color=color, alpha=0.15, lw=0)
    ax.set_xlabel(T["since_start"])
    ax.set_ylabel(T["temp"])
    ax.grid(axis="y", color=GRID, lw=0.6)
    ax.legend(frameon=False)
    _style(ax)
    return _save(fig, out, "F4_heating_curves", lang)


def fig_bland_altman(ba: dict[str, Any], out: Path, lang: str) -> list[str]:
    import matplotlib.pyplot as plt

    T = TXT[lang]
    fig, ax = plt.subplots(figsize=(4.8, 3.4), facecolor=SURFACE)
    if ba.get("n"):
        ax.scatter(ba["means"], ba["diffs"], s=40, color="#2a78d6", edgecolor=SURFACE, linewidth=1.5, zorder=3)
        ax.axhline(ba["bias"], color=INK2, lw=1.2)
        ax.text(1.0, ba["bias"], f" {T['bias']} {ba['bias']:.2f}", transform=ax.get_yaxis_transform(), va="center", color=INK2, fontsize=8)
        for k in ("loa_low", "loa_high"):
            if k in ba:
                ax.axhline(ba[k], color=MUTED, lw=1, ls=(0, (4, 3)))
                ax.text(1.0, ba[k], f" {T['loa']} {ba[k]:.2f}", transform=ax.get_yaxis_transform(), va="center", color=MUTED, fontsize=8)
    ax.axhline(0, color=GRID, lw=0.8, zorder=0)
    ax.set_xlabel(T["ba_x"])
    ax.set_ylabel(T["ba_y"])
    _style(ax)
    return _save(fig, out, "F5_bland_altman", lang)


def fig_models(folds: dict[str, list], tracks: list[str], out: Path, lang: str) -> list[str]:
    import matplotlib.pyplot as plt

    T = TXT[lang]
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.8), facecolor=SURFACE)
    for ax, key, stat, label in ((axes[0], "err", np.median, T["alert_err"]), (axes[1], "f1", np.mean, T["macro_f1"])):
        for i, t in enumerate(tracks):
            rs = folds.get(t, [])
            vals = [abs(r.done_err_s) for r in rs if r.done_err_s is not None] if key == "err" else [r.macro_f1 for r in rs if r.macro_f1 is not None]
            ci = bootstrap_ci(vals, stat=stat)
            if ci["est"] is None:
                continue
            ax.plot([ci["est"]], [i], "o", ms=8, color=TRACK_COLORS.get(t, INK2), mec=SURFACE, mew=1.5, zorder=3)
            if ci["lo"] is not None:
                ax.plot([ci["lo"], ci["hi"]], [i, i], color=TRACK_COLORS.get(t, INK2), lw=2, solid_capstyle="round")
        ax.set_yticks(range(len(tracks)), tracks)
        ax.invert_yaxis()
        ax.set_xlabel(label)
        ax.grid(axis="x", color=GRID, lw=0.6)
        _style(ax)
    return _save(fig, out, "F6_model_comparison", lang)


def fig_alert_errors(folds: dict[str, list], tracks: list[str], out: Path, lang: str) -> list[str]:
    import matplotlib.pyplot as plt

    T = TXT[lang]
    fig, ax = plt.subplots(figsize=(6.0, 2.9), facecolor=SURFACE)
    rng = np.random.default_rng(0)
    for i, t in enumerate(tracks):
        errs = np.array([r.done_err_s for r in folds.get(t, []) if r.done_err_s is not None])
        if len(errs):
            ax.scatter(errs, i + rng.uniform(-0.15, 0.15, len(errs)), s=22, color=TRACK_COLORS.get(t, INK2), edgecolor=SURFACE,
                       linewidth=1, zorder=3)
            ax.plot([np.median(errs)] * 2, [i - 0.3, i + 0.3], color=INK, lw=1.5)
    ax.axvline(0, color=MUTED, lw=1)
    ax.set_yticks(range(len(tracks)), tracks)
    ax.invert_yaxis()
    ax.set_xlabel(T["signed_err"])
    ax.grid(axis="x", color=GRID, lw=0.6)
    _style(ax)
    return _save(fig, out, "F7_alert_errors", lang)


def fig_learning_curve(curves: dict[str, list], out: Path, lang: str) -> list[str]:
    import matplotlib.pyplot as plt

    T = TXT[lang]
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.8), facecolor=SURFACE)
    for t, rows in curves.items():
        n = [r["train_sessions"] for r in rows]
        for ax, k in ((axes[0], "done_abs_err_median_s"), (axes[1], "macro_f1")):
            y = [r.get(k) for r in rows]
            ax.plot(n, y, "-o", color=TRACK_COLORS.get(t, INK2), lw=2, ms=6, label=t)
    axes[0].set_ylabel(T["alert_err"])
    axes[1].set_ylabel(T["macro_f1"])
    for ax in axes:
        ax.set_xlabel(T["train_n"])
        ax.grid(axis="y", color=GRID, lw=0.6)
        _style(ax)
    axes[1].legend(frameon=False)
    return _save(fig, out, "F8_learning_curve", lang)


def fig_confusion(tables: dict[str, pd.DataFrame], track: str, out: Path, lang: str, target: str = "label") -> tuple[list[str], np.ndarray]:
    """LOSO 예측을 모아 행 정규화 혼동 행렬(정답 행 = 100%)."""
    import matplotlib.pyplot as plt
    from sklearn.metrics import confusion_matrix

    from .baseline import _make_model, _proba

    T = TXT[lang]
    feats = {sid: build_features(df, track) for sid, df in tables.items()}
    ordered = {sid: df.sort_values("elapsed_s").reset_index(drop=True) for sid, df in tables.items()}
    labeled = [s for s, df in ordered.items() if df[target].notna().any()]
    yt, yp = [], []
    for test in labeled:
        train = [s for s in labeled if s != test]
        Xs, ys = [], []
        for sid in train:
            m = ordered[sid][target].notna().to_numpy()
            idx = np.where(m)[0][::5]
            Xs.append(feats[sid].iloc[idx])
            ys.append(ordered[sid][target].iloc[idx].map(CODE))
        if not Xs:
            continue
        X_tr, y_tr = pd.concat(Xs), pd.concat(ys)
        if y_tr.nunique() < 2:
            continue
        cols = [c for c in X_tr.columns if X_tr[c].notna().any()]
        model = _make_model().fit(X_tr[cols], y_tr)
        m = ordered[test][target].notna().to_numpy()
        yt += list(ordered[test][target][m].map(CODE))
        yp += list(_proba(model, feats[test].reindex(columns=cols)[m]).argmax(1))
    cm = confusion_matrix(yt, yp, labels=[0, 1, 2]) if yt else np.zeros((3, 3), int)
    with np.errstate(all="ignore"):
        pct = np.nan_to_num(cm / cm.sum(1, keepdims=True) * 100)
    fig, ax = plt.subplots(figsize=(3.6, 3.2), facecolor=SURFACE)
    ax.imshow(pct, cmap="Blues", vmin=0, vmax=100)
    names = [T[c] for c in CLASSES]
    for i in range(3):
        for j in range(3):
            ax.text(j, i, f"{pct[i, j]:.0f}%\n({cm[i, j]})", ha="center", va="center", fontsize=8,
                    color="white" if pct[i, j] > 55 else INK)
    ax.set_xticks(range(3), names)
    ax.set_yticks(range(3), names)
    ax.set_xlabel(T["pred"])
    ax.set_ylabel(T["true"])
    ax.set_title(track, color=INK2)
    return _save(fig, out, f"F9_confusion_{track}", lang), cm


def fig_importance(imp: dict[str, dict[str, float]], track: str, out: Path, lang: str) -> list[str]:
    import matplotlib.pyplot as plt

    T = TXT[lang]
    items = sorted(imp.items(), key=lambda kv: kv[1]["mean"], reverse=True)
    fig, ax = plt.subplots(figsize=(4.8, 0.5 + 0.45 * max(len(items), 1)), facecolor=SURFACE)
    for i, (g, v) in enumerate(items):
        ax.barh(i, v["mean"], color="#2a78d6", height=0.55)
        ax.errorbar(v["mean"], i, xerr=v["sd"], color=INK2, capsize=3, lw=1)
    ax.set_yticks(range(len(items)), [T["groups"].get(g, g) for g, _ in items])
    ax.invert_yaxis()
    ax.axvline(0, color=MUTED, lw=1)
    ax.set_xlabel(T["importance"])
    ax.set_title(track, color=INK2)
    _style(ax)
    return _save(fig, out, f"F10_importance_{track}", lang)


# ── 전체 ────────────────────────────────────────────────────────────────────────
def build_paper(data_root: Path, version: str, out_root: Path | None = None, *, lang: str = "en",
                tracks: tuple[str, ...] = ("trivial", "thermal", "camera", "noprobe", "probe"), session: str | None = None,
                learning_sizes: list[int] | None = None, importance: bool = True, target: str = "label") -> dict[str, Any]:
    from .baseline import load_dataset
    from .provenance import dataset_fingerprint, stamp, stamp_line

    if lang not in TXT:
        raise ValueError(f"lang은 {list(TXT)} 중 하나")
    bad = [t for t in tracks if t not in TRACKS]
    if bad:
        raise ValueError(f"알 수 없는 트랙 {bad}")
    _setup(lang)
    vdir = data_root / "datasets" / version
    tables, summary = load_dataset(vdir)
    if not tables:
        raise ValueError(f"데이터셋에 세션이 없음: {vdir}")
    out = (out_root or data_root / "paper" / version) / f"{datetime.now():%Y%m%dT%H%M%S}-{lang}"
    fig_dir, tab_dir = out / "figures", out / "tables"
    made: dict[str, list[str]] = {"figures": [], "tables": []}
    notes: list[str] = []

    recs = session_records(tables, summary)
    flow = session_flow(data_root, summary)
    folds = {t: evaluate(tables, t, target=target) for t in tracks}
    infos = {s["session_id"]: s for s in summary.get("sessions", []) if "skipped" not in s}

    meta = json.loads((data_root / "paper_meta.json").read_text(encoding="utf-8")) if (data_root / "paper_meta.json").exists() else DEFAULT_META
    made["tables"] += _write_table(table_dataset(recs, tables, summary, flow), tab_dir, "T1_dataset", "Table 1. Dataset summary")
    made["tables"] += _write_table(meta, tab_dir, "T2_sensors", "Table 2. Sensors and equipment")
    trows, tests = table_models(folds, list(tracks))
    made["tables"] += _write_table(trows, tab_dir, "T3_models", "Table 3. Model comparison (LOSO; medians with session-bootstrap 95% CI)")
    hrows, effects = table_heating(recs)
    made["tables"] += _write_table(hrows, tab_dir, "T4_heating", "Table 4. Heating characteristics by condition")
    arows, ba = table_agreement(recs)
    made["tables"] += _write_table(arows, tab_dir, "T5_label_agreement", "Table 5. Objective vs sensory label agreement")

    made["figures"] += fig_flow(flow, fig_dir, lang)
    rep = session or next((s for s in sorted(tables, key=lambda k: abs(tables[k]["elapsed_s"].max()
                                                                        - np.median([d["elapsed_s"].max() for d in tables.values()])))), None)
    if rep in tables:
        made["figures"] += fig_session(tables[rep], infos.get(rep, {}), fig_dir, lang)
    made["figures"] += fig_heating(tables, recs, fig_dir, lang)
    if ba.get("n"):
        made["figures"] += fig_bland_altman(ba, fig_dir, lang)
    else:
        notes.append("F5 Bland–Altman 생략: 객관·관능 완료 시작이 모두 있는 세션 없음")
    made["figures"] += fig_models(folds, list(tracks), fig_dir, lang)
    made["figures"] += fig_alert_errors(folds, list(tracks), fig_dir, lang)
    curves = None
    if learning_sizes:
        curves = {t: learning_curve(tables, t, learning_sizes, target=target) for t in ("trivial", "noprobe") if t in tracks}
        made["figures"] += fig_learning_curve(curves, fig_dir, lang)
    main = "noprobe" if "noprobe" in tracks else tracks[-1]
    files, cm = fig_confusion(tables, main, fig_dir, lang, target)
    made["figures"] += files
    imp = grouped_importance(tables, main, target) if importance else {}
    if imp:
        made["figures"] += fig_importance(imp, main, fig_dir, lang)

    prov = stamp(dataset_version=version, dataset_fingerprint=dataset_fingerprint(vdir), label_rules=summary.get("label_rules"),
                 calibration=summary.get("calibration"), tracks=list(tracks), lang=lang, target=target, representative_session=rep)
    stats = {"tests": tests, "condition_effects": effects, "bland_altman": {k: v for k, v in ba.items() if k not in ("means", "diffs")},
             "flow": flow, "importance": imp, "confusion": cm.tolist(), "learning_curve": curves}
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out / "provenance.json").write_text(json.dumps(prov, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    readme = [f"# Paper figures & tables — dataset {version} ({lang})", "", stamp_line(prov), "",
              "## Tables", *[f"- tables/{f}" for f in made["tables"] if f.endswith(".md")], "",
              "## Figures", *[f"- figures/{f}" for f in made["figures"] if f.endswith(".png")], "",
              "F1 (system & sensor layout) is prepared by hand (photo/diagram).", ""]
    if notes:
        readme += ["## Notes", *[f"- {n}" for n in notes]]
    (out / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    return {"out": out, "made": made, "notes": notes, "provenance": prov}
