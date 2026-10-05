"""세션 판정(사용 / 보류 / 제외) 기록 — 사람이 요약 이미지·QC를 보고 내린 결정.

`$SOUP_DATA_ROOT/review.jsonl`에 **덧붙이기만** 한다(같은 세션을 다시 판정하면 마지막 것이 유효, 이력은 남는다).
카탈로그(`catalog.csv`)는 밤마다 다시 만들어지므로 손으로 고치지 말고 이 기록을 쓴다. 데이터셋은 `drop`·`hold`를 뺀다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERDICTS = ("use", "hold", "drop")
VERDICT_KO = {"use": "사용", "hold": "보류", "drop": "제외"}


def add_review(root: Path, session_id: str, verdict: str, reason: str | None = None) -> dict[str, Any]:
    if verdict not in VERDICTS:
        raise ValueError(f"판정은 {VERDICTS} 중 하나: {verdict!r}")
    if verdict != "use" and not (reason or "").strip():
        raise ValueError("보류·제외는 이유가 필요하다(--reason)")
    rec = {"session_id": session_id, "verdict": verdict, "reason": (reason or "").strip() or None,
           "at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
    root.mkdir(parents=True, exist_ok=True)
    with open(root / "review.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def latest_reviews(root: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    p = root / "review.jsonl"
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            out[rec["session_id"]] = rec
    return out
