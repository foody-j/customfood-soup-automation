from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from soupdata.heating import HeatingCurve, detect_boil, first_sustained, heating_rate, lid_on_from, mass_from_params, to_grid

T0 = datetime(2026, 10, 20, 1, 0, tzinfo=timezone.utc)


def curve(offset=0.0, noise=0.15, end=1800, seed=0):
    rng = np.random.default_rng(seed)
    xs = np.arange(0, end, 1.0)
    ys = np.where(xs < 480, 20 + xs / 6, 100) + rng.normal(0, noise, len(xs)) + offset  # 10 ℃/분, 480초에 끓음
    return [T0 + timedelta(seconds=float(x)) for x in xs], list(ys)


def test_synthetic_curve_values():
    t, y = curve()
    hc = HeatingCurve(t, y, T0, mass_kg=1.0, calibration={"a": 1.0, "b": 0.0})
    s = hc.summary()
    assert 465 <= s.boil.onset_s <= 485 and abs(s.boil.plateau_c - 100) < 0.5 and not s.boil.flags
    assert abs(s.t_ref_reached_s - 390) <= 3                  # 75 ℃ 도달 330초 + 60초
    assert abs(s.rate_c_per_min - 10.0) < 0.2
    assert abs(s.p_net_kw - 1.0 * 4.0 * 10 / 60) < 0.01
    assert abs(s.evap_frac - s.p_net_kw * s.boil_duration_s / 2257) < 1e-6
    assert 22.5 < s.c100_end < 24.5                           # 끓은 22분 + 상승 중 약 1.4분
    assert hc.since_boil_min(s.boil.onset_s + 120) == 2.0 and hc.since_boil_min(100) is None


def test_low_reading_probe_still_finds_boil_but_flags_it():
    t, y = curve(offset=-6.5)
    b = HeatingCurve(t, y, T0).boil
    assert 465 <= b.onset_s <= 485 and any("PT100 보정" in f for f in b.flags)


def test_no_boil_and_gaps():
    xs = np.arange(0, 900, 1.0)
    ys = 20 + np.minimum(xs / 10, 50)                         # 최고 70 ℃
    b = detect_boil(*to_grid(xs, ys))
    assert b.onset_s is None and "끓는 구간 없음" in b.flags[0]
    g, yi = to_grid(np.array([0, 1, 2, 30, 31.0]), np.array([1, 2, 3, 4, 5.0]))
    assert np.isnan(yi[10]) and yi[1] == 2.0                  # 5초 넘는 빈틈은 메우지 않음
    assert first_sustained(*to_grid(xs, ys), threshold=75) == (None, None)
    assert heating_rate(np.array([]), np.array([])) is None


def test_params_helpers():
    assert mass_from_params({"product_weight_g": 1000, "water_added_ml": 200}) == 1.2
    assert mass_from_params({"water_added_ml": 200}) is None
    assert lid_on_from({"lid_initial": "off"}, [{"kind": "lid", "value": "on"}]) is True
    assert lid_on_from({"lid_initial": "off"}, []) is False
