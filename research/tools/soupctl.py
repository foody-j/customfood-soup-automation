#!/usr/bin/env python3
"""Fedora 연구 데이터 관리 CLI.

    soupctl.py list   <SRC>                       원격/로컬 Jetson 데이터 루트의 세션 목록
    soupctl.py pull   <SRC> <session_id> [...]    종료·체크섬 완료 세션만 복사 → sha256 전수 검증 → raw/로 확정
    soupctl.py verify <session_id> [...]          이미 받은 세션 재검증
    soupctl.py pi-meta <PI_URL> <session_id> ...  Pi 세션 내보내기(json) 저장 → pi/<id>.json
    soupctl.py qc     <session_id> [--out FILE]   QC 요약 Markdown
    soupctl.py catalog                            catalog.csv 갱신

SRC: rsync 원본 위치. 원격 `user@host:/home/ubuntu/collector-data` 또는 로컬(외장 SSD) 경로.
데이터 루트: `$SOUP_DATA_ROOT`(기본 `~/soup-data`) — raw/<id>, pi/<id>.json, verify/<id>.json, catalog.csv.

원칙: Jetson 원본을 지우거나 고치지 않는다. 진행 중·체크섬 미완료 세션은 받지 않는다(`--allow-unverified`로만 예외).
검증 실패 세션은 raw/.incoming/<id>에 남겨 두고 실패로 끝낸다.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from soupdata import Session, verify_session  # noqa: E402
from soupdata.qc import catalog_row, qc_markdown, session_qc  # noqa: E402
from soupdata.session import read_json  # noqa: E402


def data_root() -> Path:
    return Path(os.environ.get("SOUP_DATA_ROOT", Path.home() / "soup-data")).expanduser()


def _src(src: str, *parts: str) -> str:
    return src.rstrip("/") + "/" + "/".join(parts)


def _rsync(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["rsync", *args], capture_output=True, text=True)


def cmd_list(a) -> int:
    r = _rsync(["--list-only", _src(a.src, "")])
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        return r.returncode
    names = sorted(ln.split()[-1] for ln in r.stdout.splitlines() if ln.startswith("d") and not ln.endswith(" ."))
    have = {p.name for p in (data_root() / "raw").glob("*") if p.is_dir()}
    for n in names:
        print(("[받음] " if n in have else "       ") + n)
    return 0


def cmd_pull(a) -> int:
    root = data_root()
    fails = 0
    for sid in a.session_ids:
        final = root / "raw" / sid
        if final.exists():
            print(f"{sid}: 이미 있음 — 재검증은 verify")
            continue
        with tempfile.TemporaryDirectory() as td:
            r = _rsync([_src(a.src, sid, "manifest.json"), td + "/"])
            if r.returncode != 0:
                print(f"{sid}: manifest를 가져오지 못함\n{r.stderr}", file=sys.stderr)
                fails += 1
                continue
            m = read_json(Path(td) / "manifest.json") or {}
        if m.get("state") != "stopped" or (m.get("checksum_state") != "done" and not a.allow_unverified):
            print(f"{sid}: 받지 않음 — state={m.get('state')} checksum_state={m.get('checksum_state')}", file=sys.stderr)
            fails += 1
            continue
        incoming = root / "raw" / ".incoming" / sid
        incoming.mkdir(parents=True, exist_ok=True)
        progress = ["--info=progress2"] if sys.stdout.isatty() else []
        r = subprocess.run(["rsync", "-a", "--partial", *progress, _src(a.src, sid) + "/", str(incoming) + "/"])
        if r.returncode != 0:
            print(f"{sid}: rsync 실패({r.returncode}) — 다시 실행하면 이어 받는다", file=sys.stderr)
            fails += 1
            continue
        res = _save_verify(sid, incoming, require=not a.allow_unverified)
        if not res["ok"]:
            print(f"{sid}: 검증 실패 — {incoming}에 보존\n  " + "\n  ".join(res["problems"]), file=sys.stderr)
            fails += 1
            continue
        incoming.rename(final)
        print(f"{sid}: 받음·검증 OK (sha256 {res['checked']}건) → {final}")
    return 1 if fails else 0


def _save_verify(sid: str, path: Path, require: bool = True) -> dict:
    res = verify_session(path, require_checksums=require).to_dict()
    out = data_root() / "verify" / f"{sid}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({**res, "path": str(path)}, ensure_ascii=False, indent=2))
    return res


def cmd_verify(a) -> int:
    bad = 0
    for sid in a.session_ids:
        res = _save_verify(sid, data_root() / "raw" / sid)
        print(f"{sid}: {'OK' if res['ok'] else '문제 ' + str(res['problems'])} (sha256 {res['checked']}건)"
              + (f" 경고 {res['warnings']}" if res["warnings"] else ""))
        bad += not res["ok"]
    return 1 if bad else 0


def cmd_pi_meta(a) -> int:
    out_dir = data_root() / "pi"
    out_dir.mkdir(parents=True, exist_ok=True)
    for sid in a.session_ids:
        url = f"{a.pi_url.rstrip('/')}/api/sessions/{sid}/export?format=json"
        with urllib.request.urlopen(url, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        (out_dir / f"{sid}.json").write_text(json.dumps(body, ensure_ascii=False, indent=2))
        print(f"{sid}: Pi 내보내기 저장 (사건 {body.get('counts', {}).get('events')}건)")
    return 0


def _load(sid: str) -> tuple[Session, dict | None, dict | None]:
    root = data_root()
    return (Session(root / "raw" / sid), read_json(root / "pi" / f"{sid}.json"),
            read_json(root / "verify" / f"{sid}.json"))


def cmd_qc(a) -> int:
    sess, pi, ver = _load(a.session_id)
    md = qc_markdown(session_qc(sess, pi), ver)
    if a.out:
        Path(a.out).write_text(md)
        print(f"저장: {a.out}")
    else:
        print(md)
    return 0


def cmd_catalog(a) -> int:
    root = data_root()
    rows = []
    for d in sorted(p for p in (root / "raw").glob("*") if p.is_dir() and not p.name.startswith(".")):
        sess, pi, ver = _load(d.name)
        rows.append(catalog_row(session_qc(sess, pi), ver))
    cols: list[str] = []
    for r in rows:
        cols += [k for k in r if k not in cols]
    out = root / "catalog.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"카탈로그 {len(rows)}세션 → {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list"); s.add_argument("src"); s.set_defaults(fn=cmd_list)
    s = sub.add_parser("pull"); s.add_argument("src"); s.add_argument("session_ids", nargs="+")
    s.add_argument("--allow-unverified", action="store_true", help="Jetson 체크섬 미완료 세션도 받음(검증은 크기만)")
    s.set_defaults(fn=cmd_pull)
    s = sub.add_parser("verify"); s.add_argument("session_ids", nargs="+"); s.set_defaults(fn=cmd_verify)
    s = sub.add_parser("pi-meta"); s.add_argument("pi_url"); s.add_argument("session_ids", nargs="+")
    s.set_defaults(fn=cmd_pi_meta)
    s = sub.add_parser("qc"); s.add_argument("session_id"); s.add_argument("--out"); s.set_defaults(fn=cmd_qc)
    s = sub.add_parser("catalog"); s.set_defaults(fn=cmd_catalog)
    a = p.parse_args(argv)
    if shutil.which("rsync") is None and a.cmd in ("list", "pull"):
        print("rsync가 필요합니다", file=sys.stderr)
        return 2
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
