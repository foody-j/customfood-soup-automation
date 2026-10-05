"""논문 생성기 — 합성 데이터셋(정답 구조를 아는)으로 표·그림·통계가 끝까지 만들어지는지."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from test_baseline import synth_session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import soupctl  # noqa: E402

T0 = datetime(2026, 10, 22, 1, 0, tzinfo=timezone.utc)


def make_paper_dataset(root: Path, n: int = 5) -> str:
    rates = [6, 8, 10, 12, 14][:n]
    sess_infos = []
    vdir = root / "datasets" / "vp" / "sessions"
    vdir.mkdir(parents=True)
    for i, r in enumerate(rates):
        sid = f"sess-20261022T0{i}0000Z-p{i}"
        df = synth_session(sid, r, seed=i)
        t0 = T0 + timedelta(hours=i)
        df["t_utc"] = [(t0 + timedelta(seconds=float(s))).isoformat().replace("+00:00", "Z") for s in df["elapsed_s"]]
        t_boil = 80.0 / r * 60.0
        sens_done = t_boil + 60 * (i - 2)                         # 관능이 −2~+2분 흔들림
        df["label_sensory"] = np.where(df["elapsed_s"] < sens_done, "undercooked", "done")
        df.to_parquet(vdir / f"{sid}.parquet", index=False)
        iso = lambda s: (t0 + timedelta(seconds=s)).isoformat()  # noqa: E731
        sess_infos.append({"session_id": sid, "split": "train", "rows": len(df),
                           "heating": {"boil": {"onset_s": t_boil, "plateau_c": 100.0, "end_s": None, "flags": []},
                                       "rate_c_per_min": float(r), "c100_end": 10.0 + i, "flags": []},
                           "labels": {"objective": {"done_start": iso(t_boil), "usable": True},
                                      "sensory": {"done_start": iso(sens_done)},
                                      "agreement": {"taste_pairs": [{"taste": "done", "objective": "done"},
                                                                    {"taste": "undercooked", "objective": "done" if i == 0 else "undercooked"}]}}})
        (root / "raw" / sid).mkdir(parents=True)
        (root / "verify").mkdir(exist_ok=True)
        (root / "verify" / f"{sid}.json").write_text(json.dumps({"ok": True}))
    (root / "raw" / "sess-bad").mkdir()
    (vdir.parent / "summary.json").write_text(json.dumps({"label_rules": {"done_start": "boil", "overcooked": "mark"},
                                                          "label_counts_rows": {"undercooked": 100, "done": 200, "overcooked": 50},
                                                          "sessions": sess_infos + [{"session_id": "sess-bad", "skipped": "검증"}]}))
    return "vp"


def test_build_paper_end_to_end(tmp_path, data_root):
    from soupdata.paper import build_paper

    v = make_paper_dataset(data_root)
    soupctl.main(["review", "sess-20261022T000000Z-p0", "use"])
    res = build_paper(data_root, v, tracks=("trivial", "thermal", "camera", "noprobe"), learning_sizes=[2, 3])
    out = res["out"]
    figs = {p.stem for p in (out / "figures").glob("*.png")}
    assert {"F2_session_flow", "F3_representative_session", "F4_heating_curves", "F5_bland_altman", "F6_model_comparison",
            "F7_alert_errors", "F8_learning_curve", "F9_confusion_noprobe", "F10_importance_noprobe"} <= figs
    assert len(list((out / "figures").glob("*.pdf"))) == len(figs)
    for t in ("T1_dataset", "T2_sensors", "T3_models", "T4_heating", "T5_label_agreement"):
        assert (out / "tables" / f"{t}.md").exists() and (out / "tables" / f"{t}.tex").exists()
    t3 = (out / "tables" / "T3_models.md").read_text()
    assert "noprobe" in t3 and "H1, one-sided" in t3
    stats = json.loads((out / "stats.json").read_text())
    assert stats["flow"]["recorded"] == 6 and stats["flow"]["integrity_ok"] == 5 and stats["flow"]["objective_labeled"] == 5
    assert abs(stats["bland_altman"]["bias"]) < 1e-9                 # 관능 −2~+2분 → 편향 0
    assert set(stats["importance"]) >= {"time", "thermal", "camera"}
    prov = json.loads((out / "provenance.json").read_text())
    assert prov["dataset_fingerprint"] and prov["code"]["commit"]
    assert "Paper figures" in (out / "README.md").read_text()


def test_paper_cli_korean_and_errors(tmp_path, data_root):
    v = make_paper_dataset(data_root, n=4)
    assert soupctl.main(["paper", v, "--lang", "ko", "--tracks", "trivial,noprobe", "--no-importance"]) == 0
    out = next((data_root / "paper" / v).iterdir())
    assert out.name.endswith("-ko") and (out / "figures" / "F6_model_comparison.png").exists()
    assert soupctl.main(["paper", "없음"]) == 1
    from soupdata.paper import build_paper
    with pytest.raises(ValueError):
        build_paper(data_root, v, tracks=("microphone",))
