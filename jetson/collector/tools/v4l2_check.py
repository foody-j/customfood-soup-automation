"""ISX031F V4L2 실기기 점검·실측 도구 (플랜 7단계 입력용).

    ~/collector-venv/bin/python tools/v4l2_check.py --device /dev/video4 --frames 90 --jpeg

출력: 링크 상태, 실제 포맷, 프레임별 sequence/timestamp/blank 여부, 수신 FPS,
(옵션) JPEG 인코딩 시간과 크기. 결과 JSON을 `--out`으로 저장하면 notes/data/에 정리할 수 있다.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sensors.base import is_blank_image  # noqa: E402
from app.sensors.v4l2 import gmsl_link_status, read_controls, uyvy_to_bgr  # noqa: E402
from app.sensors.v4l2dev import V4L2Capture  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="/dev/video4")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1536)
    ap.add_argument("--pixfmt", default="UYVY")
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--timeout", type=float, default=2.0)
    ap.add_argument("--jpeg", action="store_true", help="JPEG 인코딩 비용도 측정")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    result: dict = {"device": args.device, "link": gmsl_link_status(), "controls": read_controls(args.device)}
    print("link:", result["link"], "controls:", result["controls"])
    cap = V4L2Capture(args.device, buffers=4)
    try:
        cap.open()
        result["format"] = cap.set_format(args.width, args.height, args.pixfmt)
        print("format:", result["format"])
        cap.start()
        t0 = time.monotonic()
        seqs, ts, blanks, timeouts, errors, enc_ms, sizes = [], [], 0, 0, 0, [], []
        mono_gap = []
        clocks: set[str] = set()
        while len(seqs) < args.frames and time.monotonic() - t0 < args.timeout * 4 + args.frames / 5:
            fr = cap.dequeue(args.timeout)
            if fr is None:
                timeouts += 1
                print("timeout")
                if timeouts >= 3:
                    break
                continue
            now_ns = time.monotonic_ns()
            clocks.add(fr.timestamp_clock)
            seqs.append(fr.sequence)
            ts.append(fr.timestamp_ns)
            if fr.timestamp_clock == "host_monotonic":
                mono_gap.append((now_ns - fr.timestamp_ns) / 1e6)
            errors += int(fr.error_flag)
            b = is_blank_image(fr.data)
            blanks += int(b)
            if args.jpeg and not b:
                t = time.perf_counter()
                import cv2

                ok, buf = cv2.imencode(".jpg", uyvy_to_bgr(fr.data, cap.width, cap.height), [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                enc_ms.append((time.perf_counter() - t) * 1000)
                sizes.append(len(buf) if ok else 0)
        elapsed = time.monotonic() - t0
        gaps = sum(b - a - 1 for a, b in zip(seqs, seqs[1:]) if b > a + 1)
        result.update({
            "frames": len(seqs), "elapsed_sec": round(elapsed, 3),
            "recv_fps": round(len(seqs) / elapsed, 2) if elapsed else None,
            "seq_first": seqs[0] if seqs else None, "seq_last": seqs[-1] if seqs else None, "seq_gaps": gaps,
            "timeouts": timeouts, "error_flags": errors, "blank_frames": blanks,
            "timestamp_clock": sorted(clocks),
            "ts_interval_ms_median": round(statistics.median(b - a for a, b in zip(ts, ts[1:])) / 1e6, 3) if len(ts) > 1 else None,
            "dequeue_latency_ms_median": round(statistics.median(mono_gap), 3) if mono_gap else None,
            "jpeg_encode_ms_median": round(statistics.median(enc_ms), 2) if enc_ms else None,
            "jpeg_bytes_median": int(statistics.median(sizes)) if sizes else None,
        })
    finally:
        cap.close()
    print(json.dumps(result, ensure_ascii=False, indent=1))
    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
