from collections import Counter

import pytest

from soupdata.design import REFERENCE, make_schedule


def test_schedule_deterministic_balanced_and_interleaved():
    a, b = make_schedule(seed=7), make_schedule(seed=7)
    assert a == b and make_schedule(seed=8)["soup"] != a["soup"]
    soup = a["soup"]
    assert len(soup) == 18 and sum(r["kind"] == "기준 반복" for r in soup) == 5
    refs = [r["order"] for r in soup if r["kind"] == "기준 반복"]
    assert max(b - a for a, b in zip(refs, refs[1:])) <= 5            # 고르게 끼움
    var = Counter((r["heat"], r["water_ml"], r["lid"]) for r in soup if r["kind"] == "조건 변화")
    assert len(var) == 12 and max(var.values()) <= 2                   # 13회 = 12조합 한 번씩 + 1
    assert all((r["heat"], r["water_ml"], r["lid"]) == REFERENCE for r in soup if r["kind"] == "기준 반복")
    assert Counter(r["heat"] for r in a["water"]) == {"낮음": 2, "중간": 2, "높음": 2}
    with pytest.raises(ValueError):
        make_schedule(n_soup=3, n_ref=5)
