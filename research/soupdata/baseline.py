"""기준 모델 — 문헌 조사 (A)안: 수작업 특징 + 그래디언트 부스팅, 세션 단위 LOSO 평가.

입력 묶음(트랙):
  trivial  경과 시간 + 조건(param_*) — "시간만 재도 되나" 기준선. 이게 높으면 다른 센서의 기여가 과장된다.
  thermal  trivial + 열화상 특징
  camera   trivial + 카메라 특징(`rgb_feat_*`: 선명도·밝기·색·ΔE·움직임)
  noprobe  trivial + 열화상 + 카메라 — **탐침 없는 모델(연구 질문: 비접촉 센서로 판단 가능한가)**
  probe    trivial + PT100 특징 — 정답(D-041)과 같은 출처라 순환. 참고용 상한으로만 본다
  all      trivial + 열화상 + 카메라 + PT100

평가는 세션 하나씩 빼고 나머지로 학습해 빠진 세션을 예측한다(LOSO — 같은 세션이 학습·평가에 섞이지 않음).
지표: macro-F1(전체 / 경계 ±guard 제외), 정확도, 순서 오차(MAE), **완료 알림 시각 오차(초)·조기 경보·놓침**, 과조리 경보 오차.
특징은 모두 그 시각까지의 값만 쓴다(창 평균·기울기는 과거 창). 탐침 없는 트랙에 탐침 특징이 섞이면 멈춘다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .labels import DONE, OVERCOOKED, UNDERCOOKED

CLASSES = (UNDERCOOKED, DONE, OVERCOOKED)
CODE = {c: i for i, c in enumerate(CLASSES)}
TRACKS = ("trivial", "thermal", "camera", "noprobe", "probe", "all")
NO_PROBE_TRACKS = ("trivial", "thermal", "camera", "noprobe")
THERMAL_COLS = ("thermal_max_c", "thermal_mean_c", "thermal_p95_c")
WINDOWS = (30, 120, 300)  # 초 — 끓음(수십 초)·졸임(수 분) 둘 다 보이게
PROBE_PREFIXES = ("pt100", "c100", "min_since_boil", "evap")


def _window_features(s: pd.Series, prefix: str) -> pd.DataFrame:
    out = {prefix: s}
    for w in WINDOWS:
        out[f"{prefix}_mean{w}"] = s.rolling(w, min_periods=max(5, w // 4)).mean()
        out[f"{prefix}_d{w}"] = (s - s.shift(w)) / (w / 60.0)  # ℃/분, 과거 w초
    cmax = s.cummax()
    out[f"{prefix}_max_sofar"] = cmax
    out[f"{prefix}_from_max"] = s - cmax  # 최고점 이후 하락(끓은 뒤 김에 가려짐 등)
    return pd.DataFrame(out)


def _param_features(df: pd.DataFrame) -> pd.DataFrame:
    out = {}
    for c in df.columns:
        if not c.startswith("param_") or c == "param_taster":
            continue
        col = df[c]
        if c == "param_lid_initial":
            out[c] = col.map({"on": 1.0, "off": 0.0})
        else:
            num = pd.to_numeric(col, errors="coerce")
            if num.notna().any():
                out[c] = num
    return pd.DataFrame(out, index=df.index)


def build_features(df: pd.DataFrame, track: str) -> pd.DataFrame:
    """한 세션의 1 Hz 표(시간순) → 특징 표. 트랙에 없는 센서는 쓰지 않는다."""
    if track not in TRACKS:
        raise ValueError(f"트랙은 {TRACKS} 중 하나: {track!r}")
    df = df.sort_values("elapsed_s").reset_index(drop=True)
    parts = [pd.DataFrame({"elapsed_min": df["elapsed_s"] / 60.0}), _param_features(df)]
    if track in ("thermal", "noprobe", "all"):
        for c in THERMAL_COLS:
            if c in df:
                parts.append(_window_features(pd.to_numeric(df[c], errors="coerce"), c.replace("_c", "")))
    if track in ("camera", "noprobe", "all"):
        for c in (c for c in df.columns if c.startswith("rgb_feat_")):
            parts.append(_window_features(pd.to_numeric(df[c], errors="coerce"), c))
    if track in ("probe", "all"):
        sig = df["pt100_cal_c"] if "pt100_cal_c" in df and df["pt100_cal_c"].notna().any() else df.get("pt100_c")
        if sig is not None:
            parts.append(_window_features(pd.to_numeric(sig, errors="coerce"), "pt100"))
        for c in ("c100_cum", "min_since_boil", "evap_frac_est"):
            if c in df:
                parts.append(pd.to_numeric(df[c], errors="coerce").rename(c).to_frame())
    feats = pd.concat(parts, axis=1)
    if track in NO_PROBE_TRACKS:
        leaked = [c for c in feats.columns if c.startswith(PROBE_PREFIXES)]
        if leaked:
            raise AssertionError(f"탐침 없는 트랙에 탐침 특징이 섞임: {leaked}")
    return feats


def alert_time(t_s: np.ndarray, p: np.ndarray, *, threshold: float = 0.5, hold_s: int = 30,
               ema_s: float = 20.0) -> float | None:
    """확률 시계열 → 경보 시각(초). 지수 평활(시상수 `ema_s`) 뒤 `threshold` 이상이 `hold_s`초 이어지는 **그 순간** 울린다
    (과거 값만 쓰는 실시간 규칙 — 구간 시작으로 되돌려 적지 않는다)."""
    if len(p) == 0:
        return None
    alpha = 1.0 / max(ema_s, 1.0)
    run, sm = 0, None
    for i, v in enumerate(np.nan_to_num(p, nan=0.0)):
        sm = v if sm is None else sm + alpha * (v - sm)
        run = run + 1 if sm >= threshold else 0
        if run >= hold_s:
            return float(t_s[i])
    return None


def _true_time(labels: pd.Series, t_s: np.ndarray, codes: tuple[int, ...]) -> float | None:
    hit = np.where(labels.map(CODE).isin(codes).to_numpy())[0]
    return float(t_s[hit[0]]) if len(hit) else None


@dataclass
class FoldResult:
    session_id: str
    track: str
    n_rows: int
    macro_f1: float | None
    macro_f1_guard: float | None
    accuracy: float | None
    ord_mae: float | None
    done_true_s: float | None
    done_alert_s: float | None
    over_true_s: float | None
    over_alert_s: float | None
    train_sessions: list[str] = field(default_factory=list)

    @property
    def done_err_s(self) -> float | None:
        return None if self.done_true_s is None or self.done_alert_s is None else self.done_alert_s - self.done_true_s

    @property
    def over_err_s(self) -> float | None:
        return None if self.over_true_s is None or self.over_alert_s is None else self.over_alert_s - self.over_true_s


def _make_model(seed: int = 0):
    from sklearn.ensemble import HistGradientBoostingClassifier

    return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, l2_regularization=1.0,
                                          class_weight="balanced", early_stopping=False, random_state=seed)


def _proba(model, X: pd.DataFrame) -> np.ndarray:
    """학습에 없던 클래스(예: 과조리 세션이 하나도 없음)는 확률 0으로 채워 항상 3열."""
    p = model.predict_proba(X)
    out = np.zeros((len(X), len(CLASSES)))
    for j, c in enumerate(model.classes_):
        out[:, int(c)] = p[:, j]
    return out


def evaluate(tables: dict[str, pd.DataFrame], track: str, *, target: str = "label", guard_s: float = 60.0,
             train_step: int = 5, guard_train: bool = False, seed: int = 0, fit_hook=None,
             train_subsets: dict[str, list[str]] | None = None) -> list[FoldResult]:
    """LOSO. `tables`: 세션 ID → 1 Hz 표. `train_subsets`를 주면 시험 세션별 학습 세션을 그 목록으로 제한(학습 곡선용)."""
    from sklearn.metrics import accuracy_score, f1_score

    feats = {sid: build_features(df, track) for sid, df in tables.items()}
    ordered = {sid: df.sort_values("elapsed_s").reset_index(drop=True) for sid, df in tables.items()}
    labeled = [sid for sid, df in ordered.items() if df[target].notna().any()]
    results = []
    for test in labeled:
        train_ids = [s for s in (train_subsets or {}).get(test, labeled) if s != test and s in labeled]
        if not train_ids:
            continue
        Xs, ys = [], []
        for sid in train_ids:
            df, X = ordered[sid], feats[sid]
            m = df[target].notna()
            if guard_train and "boundary_dist_s" in df:
                m &= ~(df["boundary_dist_s"] <= guard_s)
            idx = np.where(m.to_numpy())[0][::max(train_step, 1)]
            Xs.append(X.iloc[idx])
            ys.append(df[target].iloc[idx].map(CODE))
        X_tr, y_tr = pd.concat(Xs), pd.concat(ys)
        if y_tr.nunique() < 2:
            continue
        cols = [c for c in X_tr.columns if X_tr[c].notna().any()]  # 전부 빈 열(질량 미기록 세션의 증발 등)은 뺀다
        X_tr = X_tr[cols]
        model = _make_model(seed)
        model.fit(X_tr, y_tr)
        if fit_hook:
            fit_hook(test, train_ids)
        df, X = ordered[test], feats[test].reindex(columns=cols)
        prob = _proba(model, X)
        t_s = df["elapsed_s"].to_numpy()
        m = df[target].notna().to_numpy()
        y_true = df[target][m].map(CODE).to_numpy()
        y_pred = prob[m].argmax(axis=1)
        guard = m & ~(df.get("boundary_dist_s", pd.Series(np.inf, index=df.index)).fillna(np.inf).to_numpy() <= guard_s)
        yg_true = df[target][guard].map(CODE).to_numpy()
        yg_pred = prob[guard].argmax(axis=1)
        f1 = lambda a, b: float(f1_score(a, b, average="macro", labels=sorted(set(a) | set(b)))) if len(a) else None  # noqa: E731
        results.append(FoldResult(
            session_id=test, track=track, n_rows=int(m.sum()),
            macro_f1=f1(y_true, y_pred), macro_f1_guard=f1(yg_true, yg_pred),
            accuracy=float(accuracy_score(y_true, y_pred)) if len(y_true) else None,
            ord_mae=float(np.mean(np.abs(y_true - y_pred))) if len(y_true) else None,
            done_true_s=_true_time(df[target], t_s, (1, 2)), done_alert_s=alert_time(t_s, prob[:, 1] + prob[:, 2]),
            over_true_s=_true_time(df[target], t_s, (2,)), over_alert_s=alert_time(t_s, prob[:, 2]),
            train_sessions=train_ids))
    return results


def summarize(results: list[FoldResult], early_tol_s: float = 60.0) -> dict[str, Any]:
    if not results:
        return {"folds": 0}
    done_true = [r for r in results if r.done_true_s is not None]
    errs = [r.done_err_s for r in done_true if r.done_err_s is not None]
    over_true = [r for r in results if r.over_true_s is not None]
    over_errs = [r.over_err_s for r in over_true if r.over_err_s is not None]
    mean = lambda xs: float(np.mean(xs)) if xs else None  # noqa: E731
    return {
        "folds": len(results),
        "macro_f1": mean([r.macro_f1 for r in results if r.macro_f1 is not None]),
        "macro_f1_guard": mean([r.macro_f1_guard for r in results if r.macro_f1_guard is not None]),
        "accuracy": mean([r.accuracy for r in results if r.accuracy is not None]),
        "ord_mae": mean([r.ord_mae for r in results if r.ord_mae is not None]),
        "done_abs_err_median_s": float(np.median(np.abs(errs))) if errs else None,
        "done_early_rate": (sum(e < -early_tol_s for e in errs) / len(done_true)) if done_true else None,
        "done_miss_rate": (sum(r.done_alert_s is None for r in done_true) / len(done_true)) if done_true else None,
        "over_abs_err_median_s": float(np.median(np.abs(over_errs))) if over_errs else None,
        "over_miss_rate": (sum(r.over_alert_s is None for r in over_true) / len(over_true)) if over_true else None,
    }


def load_dataset(version_dir: Path) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    summary = json.loads((version_dir / "summary.json").read_text(encoding="utf-8"))
    tables = {p.stem: pd.read_parquet(p) for p in sorted((version_dir / "sessions").glob("*.parquet"))}
    return tables, summary


def learning_curve(tables: dict[str, pd.DataFrame], track: str, sizes: list[int], *, repeats: int = 3, seed: int = 0,
                   **kw) -> list[dict[str, Any]]:
    """학습 세션 수를 늘려 가며(각 크기 `repeats`번 무작위) LOSO 지표 — 데이터를 더 모아야 하는지 판단 근거."""
    rng = np.random.default_rng(seed)
    target = kw.get("target", "label")
    labeled = [s for s, df in tables.items() if df[target].notna().any()]
    out = []
    for n in sizes:
        rows = []
        for rep in range(repeats):
            subsets = {}
            for test in labeled:
                pool = [s for s in labeled if s != test]
                if len(pool) < n:
                    continue
                subsets[test] = list(rng.choice(pool, size=n, replace=False))
            if not subsets:
                continue
            res = [r for r in evaluate(tables, track, train_subsets=subsets, seed=seed + rep, **kw)
                   if r.session_id in subsets]
            rows.append(summarize(res))
        if rows:
            keys = [k for k in rows[0] if k != "folds"]
            out.append({"train_sessions": n, "repeats": len(rows),
                        **{k: (float(np.mean([r[k] for r in rows if r.get(k) is not None]))
                               if any(r.get(k) is not None for r in rows) else None) for k in keys}})
    return out


def report_markdown(version: str, summaries: dict[str, dict[str, Any]], folds: dict[str, list[FoldResult]],
                    rules: dict | None, target: str, curve: dict[str, list] | None = None) -> str:
    def f(v, nd=2):
        return "—" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))

    lines = [f"# 기준 모델 결과 — 데이터셋 {version} (정답 `{target}`)", "",
             f"라벨 규칙: `{json.dumps(rules or {}, ensure_ascii=False)}`. 평가: 세션 하나씩 빼고 학습(LOSO).",
             "**probe·all은 정답(PT100)과 같은 출처라 순환 — 참고용.** 핵심 비교는 trivial(시간·조건) vs noprobe(열화상+카메라, 탐침 없음)"
             " — thermal·camera는 센서별 기여.", "",
             "| 트랙 | 세션 | macro-F1 | F1(경계 제외) | 정확도 | 순서 오차 | 완료 알림 오차 중앙값(초) | 조기 경보율 | 놓침률 | 과조리 오차(초) |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for t, s in summaries.items():
        lines.append(f"| {t} | {s.get('folds')} | {f(s.get('macro_f1'))} | {f(s.get('macro_f1_guard'))} | {f(s.get('accuracy'))} | "
                     f"{f(s.get('ord_mae'))} | {f(s.get('done_abs_err_median_s'), 0)} | {f(s.get('done_early_rate'))} | "
                     f"{f(s.get('done_miss_rate'))} | {f(s.get('over_abs_err_median_s'), 0)} |")
    lines += ["", "## 세션별 완료 알림 오차(초, +면 늦음)", "| 세션 | " + " | ".join(summaries) + " |",
              "|---|" + "---|" * len(summaries)]
    sids = sorted({r.session_id for rs in folds.values() for r in rs})
    for sid in sids:
        cells = []
        for t in summaries:
            r = next((x for x in folds[t] if x.session_id == sid), None)
            cells.append("—" if r is None or r.done_err_s is None else f"{r.done_err_s:+.0f}")
        lines.append(f"| {sid} | " + " | ".join(cells) + " |")
    if curve:
        lines += ["", "## 학습 세션 수 곡선", "| 트랙 | 학습 세션 | macro-F1 | 완료 알림 오차 중앙값(초) |", "|---|---|---|---|"]
        for t, rows in curve.items():
            for r in rows:
                lines.append(f"| {t} | {r['train_sessions']} | {f(r.get('macro_f1'))} | {f(r.get('done_abs_err_median_s'), 0)} |")
    return "\n".join(lines) + "\n"
