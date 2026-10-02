from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from conftest import make_session
from soupdata import Session
from soupdata.dataset import build_dataset, session_table, split_of
from soupdata.labels import DONE, OVERCOOKED, UNDERCOOKED, build_timeline, clock_offset

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import soupctl  # noqa: E402

T0 = datetime(2026, 10, 20, 1, 0, 0, tzinfo=timezone.utc)


def iso(sec: float) -> str:
    return (T0 + timedelta(seconds=sec)).isoformat().replace("+00:00", "Z")


def pi_export(marks: list[tuple[str, float, str | None]], session: dict | None = None) -> dict:
    return {"session": session or {}, "events": [
        {"origin": "manual", "code": f"mark.{k}", "ts": iso(t), "occurred_at": iso(t), "detail": {"value": v}}
        for k, t, v in marks]}


def test_label_rules():
    tl = build_timeline(pi_export([("done_start", 60, None), ("done_end", 100, None), ("overcooked", 130, None)],
                                  {"clock_offset_s": 0}))
    at = lambda s: tl.label_at(T0 + timedelta(seconds=s))  # noqa: E731
    assert [at(10), at(60), at(99), at(100), at(129), at(130), at(500)] == \
        [UNDERCOOKED, DONE, DONE, None, None, OVERCOOKED, OVERCOOKED]
    assert tl.flags == []


def test_label_missing_done_start_gives_nothing():
    tl = build_timeline(pi_export([("overcooked", 130, None)]))
    assert not tl.usable and tl.label_at(T0) is None
    assert any("done_start 없음" in f for f in tl.flags) and any("시계 오차" in f for f in tl.flags)


def test_label_missing_done_end_and_duplicates_flagged():
    tl = build_timeline(pi_export([("done_start", 60, None), ("done_start", 70, None), ("overcooked", 130, None)]))
    assert tl.label_at(T0 + timedelta(seconds=65)) == DONE and tl.label_at(T0 + timedelta(seconds=120)) == DONE
    assert any("2회" in f for f in tl.flags) and any("done_end 없음" in f for f in tl.flags)


def test_clock_offset_shifts_marks_and_prefers_shortest_rtt():
    exp = pi_export([("done_start", 60, None)], {"clock_offsets": [{"offset_s": 5.0, "rtt_s": 0.4},
                                                                   {"offset_s": 2.0, "rtt_s": 0.01}]})
    assert clock_offset(exp) == (2.0, "session.clock_offsets")
    tl = build_timeline(exp)
    assert tl.label_at(T0 + timedelta(seconds=61)) == UNDERCOOKED  # Jetson 축 62초에 완료 시작
    assert tl.label_at(T0 + timedelta(seconds=62)) == DONE


def test_taste_disagreement():
    tl = build_timeline(pi_export([("done_start", 60, None), ("taste", 30, "undercooked"), ("taste", 90, "overcooked")]))
    assert tl.disagreements() == [{"at": (T0 + timedelta(seconds=90)).isoformat(), "taste": "overcooked", "label": DONE}]


def test_session_table_rows(tmp_path):
    d = make_session(tmp_path, "sess-long", frames=120, start=T0, step_s=1.0)
    exp = pi_export([("done_start", 50, None), ("done_end", 80, None), ("overcooked", 100, None)],
                    {"clock_offset_s": 0, "params": {"heat_level": 5}})
    rows, info = session_table(Session(d), exp, {"pt100_0": {"a": 1.0, "b": 2.0}})
    assert info["rows"] == len(rows) == 120
    df = pd.DataFrame(rows)
    assert df["label"].value_counts().to_dict() == {UNDERCOOKED: 49, DONE: 30, OVERCOOKED: 21}
    r = df.iloc[9]  # t = 10 s
    assert r["pt100_c"] == 25.0 + 10 and r["pt100_cal_c"] == r["pt100_c"] + 2.0
    assert r["thermal_max_c"] == 30.0 and "frames/000011." in r["rgb_cam_rgb_0_path"]  # cv2 없으면 .raw
    assert r["param_heat_level"] == 5
    assert (df["elapsed_s"].diff().dropna() == 1.0).all()


def test_values_never_come_from_the_future(tmp_path):
    d = make_session(tmp_path, "sess-sparse", frames=10, start=T0, step_s=5.0)  # 5초 간격 샘플
    rows, _ = session_table(Session(d), pi_export([]))
    df = pd.DataFrame(rows)
    # 샘플은 0,5,10…초. 3초 시점은 0초 샘플이 3초 전 → 허용(2초) 초과라 비어야 한다
    assert pd.isna(df.loc[df["elapsed_s"] == 3.0, "pt100_c"]).all()
    assert df.loc[df["elapsed_s"] == 6.0, "pt100_c"].iloc[0] == 26.0


def test_split_is_deterministic():
    assert split_of("sess-a") == split_of("sess-a")
    assert {split_of(f"s{i}") for i in range(200)} == {"train", "val", "test"}


def test_build_dataset_end_to_end(tmp_path, data_root):
    src = tmp_path / "jetson"
    for sid in ("sess-1", "sess-2"):
        make_session(src, sid, frames=60, start=T0, step_s=1.0)
        assert soupctl.main(["pull", str(src), sid]) == 0
    (data_root / "pi").mkdir()
    (data_root / "pi" / "sess-1.json").write_text(json.dumps(
        pi_export([("done_start", 20, None), ("overcooked", 50, None)], {"clock_offset_s": 0})))
    assert soupctl.main(["build-dataset", "v0"]) == 0
    out = data_root / "datasets" / "v0"
    summary = json.loads((out / "summary.json").read_text())
    assert {s["session_id"] for s in summary["sessions"] if "split" in s} == {"sess-1", "sess-2"}
    assert summary["label_counts_rows"]["done"] == 30
    assert summary["label_counts_rows"]["none"] == 60  # sess-2는 정답 사건 없음 → 라벨 없음
    assert len(list((out / "sessions").glob("*.parquet"))) == 2
    with pytest.raises(FileExistsError):
        build_dataset(data_root, "v0")
