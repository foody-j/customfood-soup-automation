"""실험 순서표 — 블록 무작위화(고정 시드) + 기준 조건 반복을 고르게 끼우기 (분석 계획서 §3).

- 조건 변화 세션: 출력 × 추가 물 × 뚜껑 조합을 블록(모든 조합 한 번씩) 단위로 섞어 이어 붙인다 → 어느 시점에 끊겨도 조합이 고르게 쌓인다.
- 기준 조건 반복: 전체 순서에 고르게 끼워 시간에 따른 표류(장비·숙련도)를 볼 수 있게 한다.
- 물 가열 실험: 출력 수준 × 반복을 섞는다.
같은 시드면 항상 같은 순서가 나온다(논문 방법에 시드를 적는다).
"""

from __future__ import annotations

import random
from itertools import product
from typing import Any

HEAT = ("낮음", "중간", "높음")
WATER_ML = (0, 200)
LID = ("엶", "덮음")
REFERENCE = ("중간", 0, "엶")


def make_schedule(n_soup: int = 18, n_ref: int = 5, n_water: int = 6, seed: int = 20261020,
                  heat=HEAT, water=WATER_ML, lid=LID, reference=REFERENCE) -> dict[str, list[dict[str, Any]]]:
    if not 0 <= n_ref <= n_soup:
        raise ValueError("기준 반복 수는 0 이상, 국 세션 수 이하")
    rng = random.Random(seed)
    combos = list(product(heat, water, lid))
    n_var = n_soup - n_ref
    var: list[tuple] = []
    while len(var) < n_var:
        block = combos[:]
        rng.shuffle(block)
        var += block
    var = var[:n_var]
    ref_pos = {min(n_soup - 1, int((k + 0.5) * n_soup / n_ref)) for k in range(n_ref)} if n_ref else set()
    while len(ref_pos) < n_ref:  # 겹치면 빈 자리로
        ref_pos.add(next(i for i in range(n_soup) if i not in ref_pos))
    soup, vi = [], 0
    for pos in range(n_soup):
        if pos in ref_pos:
            h, w, l, kind = *reference, "기준 반복"
        else:
            (h, w, l), kind = var[vi], "조건 변화"
            vi += 1
        soup.append({"order": pos + 1, "kind": kind, "heat": h, "water_ml": w, "lid": l})
    water_runs = [h for h in heat for _ in range(max(n_water // len(heat), 1))][:n_water] if n_water else []
    rng.shuffle(water_runs)
    return {"water": [{"order": i + 1, "kind": "물 가열", "heat": h, "water_ml": None, "lid": "엶"} for i, h in enumerate(water_runs)],
            "soup": soup, "seed": [{"seed": seed}]}
