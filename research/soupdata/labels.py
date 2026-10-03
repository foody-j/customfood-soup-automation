"""Pi 정답 사건 → 시간축 3단계 라벨(미완/완료/과조리).

규칙(`docs/cooking-protocol.md` 4·5절, `docs/gt-definition-design.md` §1):

    t <  done_start                 → undercooked
    done_start ≤ t < done_end       → done
    done_end   ≤ t < overcooked     → None(경계 모호 — 학습에서 제외)
    overcooked ≤ t                  → overcooked

- `done_start`가 없으면 라벨을 만들지 않는다(전부 None) — 추정하지 않는다.
- `done_end`가 없으면 완료 구간을 `overcooked`까지로 보고 `flags`에 남긴다. 둘 다 없으면 완료 구간은 끝이 없다.
- 같은 사건이 여러 번이면 **처음 것**을 쓰고 `flags`에 남긴다(Pi는 사건 삭제가 없고 정정은 메모로 한다).
- 사건 시각은 Pi 시계다. 프레임 시각은 Jetson 시계다. `offset_s`(= Jetson 시계 − Pi 시계)를 더해 Jetson 축으로 옮긴다.
- 라벨 값 문자열은 `shared/schema.json`의 `doneness`와 같다.
- 맛보기(`taste`) 판정은 라벨을 만들지 않고 **검증**에 쓴다: 그 시각의 라벨과 다르면 `disagreements`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .qc import parse_utc, pi_marks

UNDERCOOKED, DONE, OVERCOOKED = "undercooked", "done", "overcooked"
DONENESS = (UNDERCOOKED, DONE, OVERCOOKED)


@dataclass
class LabelTimeline:
    done_start: datetime | None
    done_end: datetime | None
    overcooked: datetime | None
    offset_s: float
    offset_source: str
    flags: list[str] = field(default_factory=list)
    tastes: list[tuple[datetime, str]] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.done_start is not None

    def label_at(self, t: datetime) -> str | None:
        if self.done_start is None:
            return None
        if t < self.done_start:
            return UNDERCOOKED
        if self.overcooked is not None and t >= self.overcooked:
            return OVERCOOKED
        if self.done_end is None or t < self.done_end:
            return DONE
        return None

    def disagreements(self) -> list[dict[str, Any]]:
        out = []
        for t, verdict in self.tastes:
            lab = self.label_at(t)
            if lab is not None and verdict in DONENESS and verdict != lab:
                out.append({"at": t.isoformat(), "taste": verdict, "label": lab})
        return out

    def to_dict(self) -> dict[str, Any]:
        iso = lambda d: d.isoformat() if d else None  # noqa: E731
        return {"done_start": iso(self.done_start), "done_end": iso(self.done_end), "overcooked": iso(self.overcooked),
                "offset_s": self.offset_s, "offset_source": self.offset_source, "usable": self.usable,
                "flags": self.flags, "disagreements": self.disagreements()}


def clock_offset(pi_export: dict[str, Any] | None) -> tuple[float, str]:
    """Jetson 시계 − Pi 시계(초). Pi `doneness-marks` 작업이 남기는 측정값을 찾고, 없으면 0과 출처 'none'.

    받아들이는 형태(Pi 구현 확정 전이라 넓게 받는다): 세션의 `clock_offset_s`(숫자) 또는
    `clock_offsets`/`clock` 안의 `offset_s` 값 목록 — 여러 개면 왕복 시간(`rtt_s`)이 가장 짧은 측정을 쓴다.
    """
    sess = (pi_export or {}).get("session") or {}
    if isinstance(sess.get("clock_offset_s"), (int, float)):
        return float(sess["clock_offset_s"]), "session.clock_offset_s"
    for key in ("clock_offsets", "clock"):
        v = sess.get(key)
        items = v if isinstance(v, list) else (list(v.values()) if isinstance(v, dict) else [])
        samples = [x for x in items if isinstance(x, dict) and isinstance(x.get("offset_s"), (int, float))]
        if samples:
            best = min(samples, key=lambda x: x.get("rtt_s") if isinstance(x.get("rtt_s"), (int, float)) else 1e9)
            return float(best["offset_s"]), f"session.{key}"
    return 0.0, "none"


def build_timeline(pi_export: dict[str, Any] | None, offset_s: float | None = None) -> LabelTimeline:
    off, src = (offset_s, "argument") if offset_s is not None else clock_offset(pi_export)
    shift = timedelta(seconds=off)
    marks = pi_marks(pi_export)
    flags: list[str] = []
    if src == "none":
        flags.append("시계 오차 측정 없음 — offset 0으로 가정")

    bad = [m for m in marks if m["at"] and parse_utc(m["at"]) is None]
    if bad:
        flags.append(f"시각 형식 불량 사건 {len(bad)}건 제외: " + ", ".join(f"{m['kind']}={m['at']}" for m in bad[:3]))

    def first(kind: str) -> datetime | None:
        hits = sorted(t for t in (parse_utc(m["at"]) for m in marks if m["kind"] == kind) if t)
        if len(hits) > 1:
            flags.append(f"{kind} {len(hits)}회 — 처음 것 사용")
        return hits[0] + shift if hits else None

    ds, de, oc = first("done_start"), first("done_end"), first("overcooked")
    if ds is None:
        flags.append("done_start 없음 — 라벨 없음")
    if de is None and ds is not None:
        flags.append("done_end 없음 — 완료 구간을 overcooked까지로 봄")
    if oc is None and ds is not None:
        flags.append("overcooked 없음 — 과조리 구간 없음")
    for a, b, name in ((ds, de, "done_end"), (ds, oc, "overcooked"), (de, oc, "overcooked")):
        if a and b and b < a:
            flags.append(f"{name}가 앞 사건보다 이름 — 사건 순서 확인 필요")
    tastes = [(parse_utc(m["at"]) + shift, m["value"]) for m in marks if m["kind"] == "taste" and parse_utc(m["at"])]
    return LabelTimeline(ds, de, oc, off, src, flags, tastes)
