"""솥 ROI 오프라인 도구 — 저장된 프레임(JPEG)에서 수집 서비스와 **같은 검출**(`app/roi.py`)을 돌린다.

수집 서비스는 녹화 중 1분마다 솥 원을 찾아 `<session>/pot_roi.jsonl`에 남긴다. 이 도구는
그 기능이 생기기 전 세션을 다시 계산하거나, 사진 한 장으로 검출을 확인할 때 쓴다. 원본은 고치지 않는다.

    PY=~/collector-venv/bin/python
    $PY tools/pot_roi.py image frame.jpg --overlay roi.jpg               # 사진 한 장
    $PY tools/pot_roi.py session ~/collector-data/check-20261010T082718Z  # 세션: 60초마다 프레임 하나
    $PY tools/pot_roi.py session DIR --every-sec 30 --out roi.json --overlay roi.jpg

`--out` JSON 형식은 session.json `pot_roi.summary`와 같다(센서별 tried·found·circle·max_shift_px) + 검출 목록.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.roi import DEFAULT_MIN_SCORE, cv2, detect_pot, summarize  # noqa: E402


def _overlay(src: Path, res: dict[str, Any] | None, out: Path) -> None:
    img = cv2.imread(str(src))
    if img is None:
        raise SystemExit(f"그림을 읽지 못함: {src}")
    if res and res.get("found"):
        c, r = (int(res["cx"]), int(res["cy"])), int(res["r"])
        cv2.circle(img, c, r, (0, 200, 0), max(2, img.shape[1] // 300))
        cv2.circle(img, c, max(3, img.shape[1] // 200), (0, 200, 0), -1)
    cv2.imwrite(str(out), img)
    print(f"겹쳐 그린 그림: {out}")


def _frames(session: Path, sensor: str) -> list[tuple[dict[str, Any], Path]]:
    stream_dir = next((d for d in (session / sensor / "rgb", session / sensor / "color") if d.is_dir()), None)
    if stream_dir is None:
        raise SystemExit(f"{session}에 {sensor}/rgb 스트림이 없음")
    rows = []
    with open(stream_dir / "index.jsonl", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            path = row.get("path")
            if path and path.endswith(".jpg"):
                rows.append((row, session / path))  # path는 세션 폴더 기준
    return rows


def _row_utc(row: dict[str, Any]) -> str | None:
    return row.get("host_recv_utc")


def _row_mono(row: dict[str, Any]) -> float | None:
    v = row.get("host_recv_mono_ns")
    return v / 1e9 if isinstance(v, (int, float)) else None


def cmd_image(args: argparse.Namespace) -> int:
    img = cv2.imread(args.path)
    if img is None:
        raise SystemExit(f"그림을 읽지 못함: {args.path}")
    res = detect_pot(img, min_score=args.min_score)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if args.overlay:
        _overlay(Path(args.path), res, Path(args.overlay))
    return 0 if res.get("found") else 1


def cmd_session(args: argparse.Namespace) -> int:
    session = Path(args.dir).expanduser()
    rows = _frames(session, args.sensor)
    if not rows:
        raise SystemExit("JPEG 프레임이 없음")
    picked, next_t = [], None
    for i, (row, path) in enumerate(rows):
        t = _row_mono(row)
        t = float(i) / max(args.fps_guess, 0.1) if t is None else t
        if next_t is None or t >= next_t:
            picked.append((row, path))
            next_t = t + args.every_sec
    results = []
    for row, path in picked:
        img = cv2.imread(str(path))
        res = detect_pot(img, min_score=args.min_score) if img is not None else {"found": False, "reason": "decode_failed"}
        res = {"sensor_id": args.sensor, "seq": row.get("seq"), "host_utc": _row_utc(row), "frame": path.name, **res}
        results.append(res)
        mark = f"({res['cx']}, {res['cy']}) r {res['r']}" if res.get("found") else f"못 찾음 {res.get('reason')}"
        print(f"{path.name}  점수 {res.get('score', '-')}  {mark}")
    summary = summarize(results)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps({"session": session.name, "sensor_id": args.sensor,
                                              "summary": summary, "results": results},
                                             ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"저장: {args.out}")
    if args.overlay:
        c = summary.get("circle")
        _overlay(picked[len(picked) // 2][1], {"found": True, **c} if c else None, Path(args.overlay))
    return 0 if summary["found"] else 1


def main(argv: list[str] | None = None) -> int:
    if cv2 is None:
        raise SystemExit("OpenCV(cv2)가 필요하다")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("image", help="사진 한 장에서 솥 원 찾기")
    p.add_argument("path")
    p.add_argument("--overlay", help="원을 겹쳐 그린 그림 저장 경로")
    p.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    p.set_defaults(func=cmd_image)
    p = sub.add_parser("session", help="저장된 세션의 프레임을 일정 간격으로 골라 솥 원 찾기")
    p.add_argument("dir")
    p.add_argument("--sensor", default="cam_rgb_0")
    p.add_argument("--every-sec", type=float, default=60.0)
    p.add_argument("--fps-guess", type=float, default=10.0, help="index에 단조 시각이 없을 때 쓰는 fps")
    p.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    p.add_argument("--out", help="결과 JSON 경로(notes/data/ 등). 세션 폴더에는 쓰지 않는다")
    p.add_argument("--overlay", help="중앙값 원을 가운데 프레임에 겹쳐 그린 그림 경로")
    p.set_defaults(func=cmd_session)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
