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


# ── D-041: 객관 라벨(PT100 곡선 기준) ────────────────────────────────────────────
# 정답의 기준점은 표준 레시피(레토르트는 포장지 조리법)이고 PT100 곡선으로 판정한다. 위의 Pi 사건 라벨(build_timeline)은
# "관능 라벨"로 남겨 검증(시간차·맛보기 일치)에 쓴다.

@dataclass
class LabelRules:
    """객관 라벨 규칙. 문자열 규칙:

    done_start: ``boil``(끓기 시작, 기본 — 포장지 "끓을 때까지" 가정) | ``temp:75``(75 ℃를 hold_s 유지한 시각) |
                ``c100:<분>``(조리값 누적이 목표에 닿은 시각 — 생재료 국의 표준 레시피 환산)
    overcooked: ``mark``(Pi '과조리' 사건, 기본) | ``boil+<분>``(끓기 시작 후 N분) | ``evap:<비율>``(증발 추정 비율) |
                ``c100:<분>`` | ``none``
    """

    done_start: str = "boil"
    overcooked: str = "mark"
    ref_c: float = 75.0
    hold_s: float = 60.0
    min_boil_c: float = 85.0
    z: float = 33.0
    guard_s: float = 60.0  # 경계 ±guard_s는 `boundary_dist_s`로 표시(학습 제외 여부는 모델 쪽에서)

    def __post_init__(self) -> None:
        if self.done_start.startswith("temp:"):  # 기준 온도는 규칙 숫자를 따른다(temp:70이면 70 ℃)
            self.ref_c = float(_rule_value(self.done_start, "temp:"))

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "LabelRules":
        base = cls()
        for k, v in (d or {}).items():
            if hasattr(base, k):
                setattr(base, k, type(getattr(base, k))(v))
        base.__post_init__()
        return base


@dataclass
class ObjectiveTimeline:
    done_start: datetime | None
    overcooked: datetime | None
    done_source: str
    over_source: str
    flags: list[str] = field(default_factory=list)

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
        return DONE

    def boundary_dist_s(self, t: datetime) -> float | None:
        ds = [abs((t - b).total_seconds()) for b in (self.done_start, self.overcooked) if b is not None]
        return min(ds) if ds else None

    def to_dict(self) -> dict[str, Any]:
        iso = lambda d: d.isoformat() if d else None  # noqa: E731
        return {"done_start": iso(self.done_start), "overcooked": iso(self.overcooked), "done_source": self.done_source,
                "over_source": self.over_source, "usable": self.usable, "flags": self.flags}


def _rule_value(rule: str, prefix: str) -> float | None:
    if not rule.startswith(prefix):
        return None
    try:
        return float(rule[len(prefix):])
    except ValueError:
        raise ValueError(f"규칙 숫자 해석 불가: {rule!r}") from None


def build_objective(curve, rules: LabelRules, sensory: LabelTimeline | None = None) -> ObjectiveTimeline:
    """PT100 곡선(`heating.HeatingCurve`)과 규칙으로 객관 라벨 시각을 정한다. PT100은 Jetson 시계라 시계 보정이 필요 없다."""
    from datetime import timedelta as _td

    flags: list[str] = []
    at = (lambda s: curve.t0 + _td(seconds=s) if s is not None else None)  # noqa: E731
    r = rules.done_start
    if r == "boil":
        ds = at(curve.boil.onset_s)
        if ds is None:
            flags.append("끓기 시작을 못 찾음 — 객관 라벨 없음: " + "; ".join(curve.boil.flags))
    elif r.startswith("temp:"):
        thr = _rule_value(r, "temp:")
        if thr != curve.ref_c:
            raise ValueError(f"temp 규칙({thr:g})과 곡선 기준 온도({curve.ref_c:g})가 다르다 — 곡선을 rules.ref_c로 만든다")
        ds = at(curve.ref_reached)
        if ds is None:
            flags.append(f"{thr:g} ℃ {rules.hold_s:g}초 유지 없음 — 객관 라벨 없음")
    elif r.startswith("c100:"):
        ds = at(curve.time_c100_reaches(_rule_value(r, "c100:")))
        if ds is None:
            flags.append(f"조리값이 {r} 목표에 못 닿음 — 객관 라벨 없음")
    else:
        raise ValueError(f"알 수 없는 done_start 규칙: {r!r}")
    if not curve.calibrated and r.startswith(("temp:", "c100:")):
        flags.append("PT100 보정 전 원값으로 판정 — 보정 후 다시 계산할 것")

    o = rules.overcooked
    if o == "mark":
        oc = sensory.overcooked if sensory else None
        if oc is None and ds is not None:
            flags.append("Pi '과조리' 사건 없음 — 과조리 구간 없음")
    elif o == "none":
        oc = None
    elif o.startswith("boil+"):
        oc = at(curve.boil.onset_s + 60 * _rule_value(o, "boil+")) if curve.boil.onset_s is not None else None
    elif o.startswith("evap:"):
        oc = at(curve.time_evap_reaches(_rule_value(o, "evap:")))
        if oc is None:
            flags.append("증발 추정 불가(뚜껑·질량·열량) — 과조리 구간 없음")
    elif o.startswith("c100:"):
        oc = at(curve.time_c100_reaches(_rule_value(o, "c100:")))
    else:
        raise ValueError(f"알 수 없는 overcooked 규칙: {o!r}")
    if ds is not None and oc is not None and oc <= ds:
        flags.append("과조리 시각이 완료 시작보다 이르거나 같음 — 과조리 무시")
        oc = None
    return ObjectiveTimeline(ds, oc, r, o, flags)


def agreement(obj: ObjectiveTimeline, sensory: LabelTimeline) -> dict[str, Any]:
    """객관 라벨 vs 관능(Pi 사건) — 경계 시각 차이(초, 관능−객관)와 맛보기 판정 일치."""
    def diff(a, b):
        return round((b - a).total_seconds(), 1) if a is not None and b is not None else None

    tastes = [(t, v) for t, v in sensory.tastes if v in DONENESS]
    hits = [(v, obj.label_at(t)) for t, v in tastes if obj.label_at(t) is not None]
    return {
        "done_start_diff_s": diff(obj.done_start, sensory.done_start),
        "overcooked_diff_s": diff(obj.overcooked, sensory.overcooked),
        "tastes": len(tastes),
        "tastes_compared": len(hits),
        "tastes_agree": sum(1 for v, lab in hits if v == lab),
        "taste_pairs": [{"taste": v, "objective": lab} for v, lab in hits],
    }
