import pandas as pd

from soupdata.provenance import code_version, dataset_fingerprint, stamp, stamp_line


def test_stamp_and_fingerprint(tmp_path):
    v = tmp_path / "v1" / "sessions"
    v.mkdir(parents=True)
    pd.DataFrame({"a": [1, 2]}).to_parquet(v / "s1.parquet")
    fp1 = dataset_fingerprint(tmp_path / "v1")
    assert fp1 == dataset_fingerprint(tmp_path / "v1")
    pd.DataFrame({"a": [1, 3]}).to_parquet(v / "s1.parquet")
    assert dataset_fingerprint(tmp_path / "v1") != fp1
    assert dataset_fingerprint(tmp_path / "없음") is None
    st = stamp(dataset_version="v1", dataset_fingerprint=fp1)
    assert st["code"]["commit"] and len(st["code"]["short"]) == 7 and "numpy" in st["environment"]
    assert "v1" in stamp_line(st) and fp1[:12] in stamp_line(st)
    assert code_version()["research_dirty"] in (True, False)
