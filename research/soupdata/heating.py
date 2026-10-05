"""PT100 온도 곡선 분석 — 끓기 시작, 끓는 구간 온도(보정 점검), 기준 온도 유지 도달, 가열 속도, 조리값, 증발 추정.

D-041: 조리 완료 정답의 기준점은 표준 레시피(레토르트는 포장지 조리법)이고, PT100 온도 곡선으로 판정한다.
이 모듈은 그 판정에 필요한 시각·값을 곡선에서 뽑는다. 원본 온도는 고치지 않는다 — 보정값을 쓰려면 호출자가
`a·T + b`로 바꾼 값을 넘긴다(`calibration.json`).

- **끓기 시작:** 충분히 뜨거운(기본 85 ℃ 이상) 구간에서 온도가 `hold_s` 동안 `band` 안에 머무르기 시작한 시각.
  끓는점 자체(≈100 ℃)를 가정하지 않으므로 탐침이 덜 잠겨 낮게 읽혀도(예: 93 ℃) 시각은 찾는다. 대신 평탄 구간 온도가
  기대 끓는점(기본 100 ℃)과 `boil_tol` 넘게 다르면 보정·탐침 깊이 의심 경고를 남긴다.
- **기준 온도 유지:** 처음으로 `threshold`(기본 75 ℃) 이상을 `hold_s`(기본 60초) 동안 계속 유지한 구간 — 시작 시각과 충족 시각.
- **가열 속도:** 끓기 전 주 상승 구간(40→80 ℃)의 직선 기울기(℃/분). 질량을 알면 솥에 실제로 들어간 열량(kW)을 추정한다.
- **조리값 C₁₀₀:** Σ 10^((T−100)/z)·Δt(분). "100 ℃에서 몇 분 끓인 것과 같은가"(Mansfield, z≈33 ℃).
- **증발 추정:** 끓는 동안 들어간 열이 모두 증발에 쓰인다고 본 상한 추정(에너지 수지). 뚜껑을 덮었으면 김이 다시
  떨어져 크게 과대평가되므로 계산하지 않는다. 물 실험(계량컵)으로 검증 전에는 참고값이다.
"""

from __future__ import annotations

import warnings
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

CP_SOUP = 4.0      # kJ/(kg·K) — 국(대부분 물)의 근사 비열. 물은 4.186
H_FG = 2257.0      # kJ/kg — 100 ℃ 물의 증발 잠열


def to_grid(x: np.ndarray, y: np.ndarray, step: float = 1.0, max_gap: float = 5.0) -> tuple[np.ndarray, np.ndarray]:
    """불규칙 샘플(x 초, y ℃) → `step` 초 격자. `max_gap`초보다 긴 빈틈은 메우지 않고 NaN으로 둔다."""
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = np.asarray(x, float)[ok], np.asarray(y, float)[ok]
    if len(x) < 2:
        return np.array([]), np.array([])
    order = np.argsort(x)
    x, y = x[order], y[order]
    grid = np.arange(np.ceil(x[0]), np.floor(x[-1]) + step / 2, step)
    yi = np.interp(grid, x, y)
    right = np.clip(np.searchsorted(x, grid), 0, len(x) - 1)
    left = np.clip(right - 1, 0, len(x) - 1)
    exact = x[right] == grid
    gap = (x[right] - x[left] > max_gap) & ~exact
    yi[gap] = np.nan
    return grid, yi


def rolling_median(y: np.ndarray, w: int) -> np.ndarray:
    """가운데 정렬 이동 중앙값(NaN 무시). 단발성 튐을 지운다."""
    if w <= 1 or len(y) == 0:
        return y.copy()
    w = int(w) | 1
    pad = w // 2
    yp = np.pad(y, pad, mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(yp, w)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # 전부 NaN인 창
        return np.nanmedian(win, axis=1)


@dataclass
class BoilResult:
    onset_s: float | None = None     # 끓는 평탄 구간 시작(기준 시각 t0부터 초)
    end_s: float | None = None       # 평탄 구간 끝(식기 시작 또는 기록 끝)
    plateau_c: float | None = None   # 평탄 구간 중앙값
    flags: list[str] = field(default_factory=list)


def detect_boil(grid: np.ndarray, y: np.ndarray, *, min_temp: float = 85.0, band: float = 1.5, hold_s: float = 60.0,
                expected_c: float = 100.0, boil_tol: float = 2.0, step: float = 1.0) -> BoilResult:
    res = BoilResult()
    if len(grid) == 0:
        res.flags.append("PT100 기록 없음")
        return res
    s = rolling_median(y, 15)
    hot = np.where(s >= min_temp)[0]
    if len(hot) == 0:
        res.flags.append(f"끓는 구간 없음(최고 {np.nanmax(s):.1f} ℃ < {min_temp:g} ℃)")
        return res
    h = max(int(round(hold_s / step)), 2)
    onset = None
    for i in hot:
        win = s[i:i + h]
        if len(win) < h or np.isnan(win).any():
            continue
        if np.nanmax(win) - np.nanmin(win) <= band:
            onset = i
            break
    if onset is None:
        res.flags.append(f"{min_temp:g} ℃ 이상이지만 {hold_s:g}초 평탄 구간이 없음(끓기 전 종료 또는 계속 상승)")
        return res
    level = float(np.nanmedian(s[onset:onset + h]))
    j = onset
    while j + 1 < len(s) and np.isfinite(s[j + 1]) and s[j + 1] >= level - band:
        j += 1
    res.onset_s = float(grid[onset])
    res.end_s = float(grid[j])
    res.plateau_c = float(np.nanmedian(s[onset:j + 1]))
    if abs(res.plateau_c - expected_c) > boil_tol:
        res.flags.append(f"끓는 구간 {res.plateau_c:.1f} ℃ — 기대 ≈{expected_c:g} ℃와 {abs(res.plateau_c - expected_c):.1f} ℃ 차이: "
                         "PT100 보정·탐침 깊이 확인")
    return res


def first_sustained(grid: np.ndarray, y: np.ndarray, threshold: float, hold_s: float = 60.0,
                    step: float = 1.0) -> tuple[float | None, float | None]:
    """처음으로 `threshold` 이상을 `hold_s`초 계속 유지한 구간의 (시작, 충족) 시각. 없으면 (None, None)."""
    if len(grid) == 0:
        return None, None
    s = rolling_median(y, 5)
    above = np.nan_to_num(s, nan=-np.inf) >= threshold
    h = max(int(round(hold_s / step)), 1)
    run = 0
    for i, a in enumerate(above):
        run = run + 1 if a else 0
        if run >= h:
            start = i - h + 1
            return float(grid[start]), float(grid[start]) + hold_s
    return None, None


def heating_rate(grid: np.ndarray, y: np.ndarray, lo: float = 40.0, hi: float = 80.0) -> float | None:
    """끓기 전 주 상승 구간(`lo`→`hi` ℃)의 기울기(℃/분). 처음 `hi`에 닿기 직전 마지막으로 `lo` 이하였던 지점부터."""
    if len(grid) == 0:
        return None
    s = rolling_median(y, 15)
    up = np.where(s >= hi)[0]
    if len(up) == 0:
        return None
    i_hi = up[0]
    below = np.where(s[:i_hi] <= lo)[0]
    i_lo = below[-1] if len(below) else 0
    seg = slice(i_lo, i_hi + 1)
    xs, ys = grid[seg], s[seg]
    ok = np.isfinite(ys)
    if ok.sum() < 30 or ys[ok].max() - ys[ok].min() < 15:
        return None
    slope = np.polyfit(xs[ok], ys[ok], 1)[0]  # ℃/s
    return float(slope * 60.0)


def cook_value(grid: np.ndarray, y: np.ndarray, z: float = 33.0, tref: float = 100.0) -> np.ndarray:
    """누적 조리값 C(분, `tref` 등가). NaN 구간은 더하지 않는다."""
    if len(grid) == 0:
        return np.array([])
    rate = np.nan_to_num(10.0 ** ((y - tref) / z), nan=0.0)
    dt = np.diff(grid, prepend=grid[0])
    return np.cumsum(rate * dt) / 60.0


def net_power_kw(rate_c_per_min: float | None, mass_kg: float | None, cp: float = CP_SOUP) -> float | None:
    if rate_c_per_min is None or not mass_kg:
        return None
    return mass_kg * cp * rate_c_per_min / 60.0


@dataclass
class HeatingSummary:
    boil: BoilResult
    t_ref_start_s: float | None        # 기준 온도(기본 75 ℃) 이상 유지 시작
    t_ref_reached_s: float | None      # 그 유지가 hold_s를 채운 시각
    ref_c: float
    rate_c_per_min: float | None
    mass_kg: float | None
    p_net_kw: float | None
    boil_duration_s: float | None
    evap_kg: float | None
    evap_frac: float | None
    c100_end: float | None
    calibrated: bool
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["flags"] = list(self.boil.flags) + list(self.flags)
        return d


class HeatingCurve:
    """한 세션의 PT100 곡선. 시각은 기준 시각 `t0`(촬영 시작)부터의 초 격자로 다룬다."""

    def __init__(self, times: list[datetime], temps: list[float], t0: datetime, *, calibration: dict | None = None,
                 mass_kg: float | None = None, lid_on: bool = False, ref_c: float = 75.0, hold_s: float = 60.0,
                 min_boil_c: float = 85.0, expected_boil_c: float = 100.0, z: float = 33.0, cp: float = CP_SOUP):
        a, b = (calibration or {}).get("a"), (calibration or {}).get("b")
        self.calibrated = a is not None and b is not None
        x = np.array([(t - t0).total_seconds() for t in times], float)
        y = np.array(temps, float)
        if self.calibrated:
            y = a * y + b
        self.t0, self.z, self.hold_s = t0, z, hold_s
        self.grid, self.y = to_grid(x, y)
        self.boil = detect_boil(self.grid, self.y, min_temp=min_boil_c, hold_s=hold_s, expected_c=expected_boil_c)
        self.ref_start, self.ref_reached = first_sustained(self.grid, self.y, ref_c, hold_s)
        self.rate = heating_rate(self.grid, self.y)
        self.c100 = cook_value(self.grid, self.y, z=z)
        self.mass_kg, self.lid_on, self.ref_c, self.cp = mass_kg, lid_on, ref_c, cp
        self.p_net = net_power_kw(self.rate, mass_kg, cp)

    # ── 시각별 값(표 만들 때) ──
    def c100_at(self, s: float) -> float | None:
        if len(self.grid) == 0 or s < self.grid[0]:
            return None
        return float(np.interp(s, self.grid, self.c100))

    def since_boil_min(self, s: float) -> float | None:
        if self.boil.onset_s is None or s < self.boil.onset_s:
            return None
        return (s - self.boil.onset_s) / 60.0

    def evap_frac_at(self, s: float) -> float | None:
        """끓기 시작부터 s초까지 증발 추정(처음 질량 대비). 뚜껑 덮음·질량 모름·열량 모름이면 None."""
        if self.lid_on or self.p_net is None or not self.mass_kg or self.boil.onset_s is None or s < self.boil.onset_s:
            return None
        return self.p_net * (s - self.boil.onset_s) / H_FG / self.mass_kg

    def time_c100_reaches(self, target: float) -> float | None:
        idx = np.where(self.c100 >= target)[0]
        return float(self.grid[idx[0]]) if len(idx) else None

    def time_evap_reaches(self, frac: float) -> float | None:
        if self.lid_on or not self.p_net or self.p_net <= 0 or not self.mass_kg or self.boil.onset_s is None:
            return None
        return self.boil.onset_s + frac * self.mass_kg * H_FG / self.p_net

    def summary(self) -> HeatingSummary:
        flags = []
        if not self.calibrated:
            flags.append("PT100 보정값 없음(원값 사용) — 75 ℃ 등 절대 온도 기준은 보정 후 확정")
        end = float(self.grid[-1]) if len(self.grid) else None
        dur = (end - self.boil.onset_s) if (end is not None and self.boil.onset_s is not None) else None
        evap_kg = evap_frac = None
        if dur is not None and self.p_net is not None and self.mass_kg and not self.lid_on:
            evap_kg = float(self.p_net * dur / H_FG)
            evap_frac = float(evap_kg / self.mass_kg)
        if self.lid_on:
            flags.append("뚜껑 덮음 — 증발 추정 안 함(김이 다시 떨어져 과대평가)")
        elif self.mass_kg is None:
            flags.append("투입 질량 모름(조건 칸 product_weight_g·water_added_ml) — 열량·증발 추정 안 함")
        return HeatingSummary(self.boil, self.ref_start, self.ref_reached, self.ref_c, self.rate, self.mass_kg,
                              self.p_net, dur, evap_kg, evap_frac,
                              float(self.c100[-1]) if len(self.c100) else None, self.calibrated, flags)


def mass_from_params(params: dict | None) -> float | None:
    """Pi 조건 키-값에서 투입 질량(kg): 제품 실측 g + 추가 물 mL(≈g). 제품 무게가 없으면 None."""
    p = params or {}
    prod = p.get("product_weight_g")
    if not isinstance(prod, (int, float)) or prod <= 0:
        return None
    water = p.get("water_added_ml") if isinstance(p.get("water_added_ml"), (int, float)) else 0.0
    return (float(prod) + float(water)) / 1000.0


def lid_on_from(params: dict | None, marks: list[dict] | None = None) -> bool:
    """처음 뚜껑을 덮었거나 세션 중 한 번이라도 '덮음' 사건이 있으면 True(증발 추정 보수적으로 끔)."""
    if (params or {}).get("lid_initial") == "on":
        return True
    return any(m.get("kind") == "lid" and m.get("value") == "on" for m in (marks or []))
