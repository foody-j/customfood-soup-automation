"""I²C·SPI 센서 단독 진단 도구 (docs/jetson-five-sensor-guide.md §3·§5).

수집 서비스와 **같은 어댑터**로 읽는다 — 여기서 검증한 읽기 경로가 그대로 운영에 쓰인다.
수집 서비스가 같은 버스를 쓰는 중에는 돌리지 않는다(프로세스 간에는 mux 잠금이 없다).

    PY=~/collector-venv/bin/python
    $PY tools/sensor_check.py buses                                   # 장치 파일·버스 클록 목록(스캔 없음)
    $PY tools/sensor_check.py mux --bus 7 --expect 0:0x33 1:0x33       # mux와 채널별 예상 주소만 확인
    $PY tools/sensor_check.py thermal --bus 7 --channel 0 --count 20 --out thermal0.json
    $PY tools/sensor_check.py pt100 --cs-pin CE0 --ref-ohms 430 --count 30

버스 번호·CS 핀·기준 저항에는 기본값이 없다 — 실물에서 확인한 값을 직접 준다.
`--mux-addr none`이면 mux 없이 직결된 센서 1대를 읽는다. `--out` JSON은 notes/data/에 정리한다.
"""

from __future__ import annotations

import argparse
import glob
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.clock import utcnow_iso  # noqa: E402
from app.sensors.base import SensorAdapter, SensorError  # noqa: E402
from app.sensors.i2cmux import get_bus  # noqa: E402
from app.sensors.rtd_max31865 import Max31865Rtd  # noqa: E402
from app.sensors.thermal_mlx90640 import Mlx90640Thermal  # noqa: E402


def _int(text: str) -> int:
    return int(text, 0)


def _opt_int(text: str) -> int | None:
    return None if text.lower() in ("none", "off") else int(text, 0)


def _bus_clock_hz(bus: int) -> int | None:
    """장치 트리에 적힌 버스 클록. mux 하위 버스 등 값이 없으면 None."""
    try:
        raw = Path(f"/sys/bus/i2c/devices/i2c-{bus}/of_node/clock-frequency").read_bytes()
    except OSError:
        return None
    return int.from_bytes(raw[:4], "big")


def cmd_buses(args: argparse.Namespace) -> dict[str, Any]:
    buses = []
    for path in sorted(glob.glob("/dev/i2c-*"), key=lambda p: int(p.rsplit("-", 1)[1])):
        n = int(path.rsplit("-", 1)[1])
        try:
            name = Path(f"/sys/bus/i2c/devices/i2c-{n}/name").read_text().strip()
        except OSError:
            name = None
        buses.append({"dev": path, "name": name, "clock_hz": _bus_clock_hz(n)})
        print(f"{path:<14} {name or '?':<36} clock={buses[-1]['clock_hz'] or '?'}")
    spidevs = sorted(glob.glob("/dev/spidev*"))
    print("SPI:", ", ".join(spidevs) or "없음 — jetson-io에서 SPI 활성화 필요")
    print("※ 이 목록은 J12 핀과의 대응을 알려 주지 않는다. mux 하나만 붙여 `mux` 명령으로 버스를 가려낸다.")
    return {"i2c": buses, "spidev": spidevs}


def cmd_mux(args: argparse.Namespace) -> dict[str, Any]:
    out: dict[str, Any] = {"bus": args.bus, "mux_addr": args.mux_addr, "clock_hz": _bus_clock_hz(args.bus), "channels": []}
    bus = get_bus(args.bus, args.mux_addr)
    for item in args.expect:
        ch_text, addr_text = item.split(":")
        ch, addr = int(ch_text), int(addr_text, 0)
        try:
            present: bool | None = bus.ack(ch, addr)
            err = None
        except OSError as exc:
            present, err = None, f"mux 0x{args.mux_addr:02x} 응답 없음: {exc}"
        out["channels"].append({"channel": ch, "addr": hex(addr), "present": present, "error": err})
        print(f"i2c-{args.bus} CH{ch} 0x{addr:02x}: {'응답' if present else err or '응답 없음'}")
    return out


def _value_summary(sample: Any) -> dict[str, float]:
    if isinstance(sample.data, np.ndarray):
        a = sample.data
        return {"min_c": float(a.min()), "max_c": float(a.max()), "mean_c": float(a.mean()),
                "center_c": float(a[a.shape[0] // 2 - 1: a.shape[0] // 2 + 1, a.shape[1] // 2 - 1: a.shape[1] // 2 + 1].mean())}
    return dict(sample.data)


def run_reads(sensor: SensorAdapter, args: argparse.Namespace) -> dict[str, Any]:
    probe = sensor.probe()
    print(f"[probe] connected={probe.connected} reason={probe.reason} facts={probe.facts}")
    result: dict[str, Any] = {"sensor_id": sensor.sensor_id, "kind": sensor.kind, "started_utc": utcnow_iso(),
                              "probe": {"connected": probe.connected, "reason": probe.reason, "facts": probe.facts},
                              "versions": sensor.version_info(), "samples": []}
    if not probe.connected:
        return result
    sensor.open({"rate_hz": args.rate} if args.rate else {})
    result["applied_config"] = sensor.applied_config()
    print(f"[open] {result['applied_config']}")
    frames: list[np.ndarray] = []
    t_start = time.monotonic()
    try:
        while len(result["samples"]) < args.count:
            try:
                got = sensor.read()
            except SensorError as exc:  # 연속 실패 한도 — 서비스라면 여기서 재연결한다
                print(f"[중단] {exc}")
                result["aborted"] = str(exc)
                break
            for s in got:
                rec = {"seq": s.seq, "host_recv_utc": s.host.utc, "host_recv_mono_ns": s.host.mono_ns, "valid": s.valid,
                       "invalid_reason": s.invalid_reason, "flags": s.flags, "value": _value_summary(s) if s.valid else None}
                result["samples"].append(rec)
                if s.valid and isinstance(s.data, np.ndarray):
                    frames.append(s.data.copy())
                shown = {k: round(v, 2) if isinstance(v, float) else v for k, v in (rec["value"] or {}).items()}
                print(f"#{s.seq:<4} {'ok ' if s.valid else 'BAD'} {s.flags.get('acquire_ms', '-'):>8} ms  "
                      f"{shown if s.valid else s.invalid_reason}")
    except KeyboardInterrupt:
        print("(중단)")
    finally:
        sensor.close()
    elapsed = time.monotonic() - t_start
    samples = result["samples"]
    valid = [r for r in samples if r["valid"]]
    monos = [r["host_recv_mono_ns"] for r in valid]
    gaps = [(b - a) / 1e6 for a, b in zip(monos, monos[1:])]
    acq = [r["flags"]["acquire_ms"] for r in valid if "acquire_ms" in r["flags"]]
    result["summary"] = {
        "requested": args.count, "read": len(samples), "valid": len(valid),
        "success_rate": round(len(valid) / len(samples), 4) if samples else None,
        "elapsed_sec": round(elapsed, 2), "valid_hz": round(len(valid) / elapsed, 3) if elapsed > 0 else None,
        "acquire_ms": {"min": min(acq), "mean": round(statistics.fmean(acq), 2), "max": max(acq)} if acq else None,
        "max_gap_ms": round(max(gaps), 1) if gaps else None,
        "retries": sum(r["flags"].get("attempts", 1) - 1 for r in valid),
    }
    print(f"[요약] {json.dumps(result['summary'], ensure_ascii=False)}")
    if frames and args.out:
        npy = Path(args.out).with_suffix(".npy")
        np.save(npy, np.stack(frames))  # (n, 24, 32) float32 원시 프레임
        result["frames_npy"] = npy.name
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--out", default=None, help="결과 JSON 경로(열화상은 같은 이름의 .npy에 원시 프레임)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("buses", parents=[common])
    mux = sub.add_parser("mux", parents=[common])
    mux.add_argument("--bus", type=int, required=True)
    mux.add_argument("--mux-addr", type=_int, default=0x70)
    mux.add_argument("--expect", nargs="+", required=True, metavar="CH:ADDR", help="예: 0:0x33 1:0x33")
    readers = {}
    for name in ("thermal", "pt100"):
        p = readers[name] = sub.add_parser(name, parents=[common])
        p.add_argument("--count", type=int, default=20)
        p.add_argument("--rate", type=float, default=None, help="목표 주기(Hz). 기본: 열화상 2, 나머지 1")
        p.add_argument("--sensor-id", default=None)
        if name != "pt100":
            p.add_argument("--bus", type=int, required=True)
            p.add_argument("--mux-addr", type=_opt_int, default=0x70)
            p.add_argument("--channel", type=int, default=0)
    readers["thermal"].add_argument("--refresh", type=float, default=8.0, help="장치 refresh rate(Hz)")
    readers["thermal"].add_argument("--retries", type=int, default=2)
    readers["pt100"].add_argument("--cs-pin", required=True, help="CE0(J12 24번, 하드웨어 CS) 또는 Blinka GPIO 핀 이름(15번 = D22)")
    readers["pt100"].add_argument("--ref-ohms", type=float, required=True, help="보드 실물 기준 저항(Ω)")
    readers["pt100"].add_argument("--wires", type=int, default=3)
    readers["pt100"].add_argument("--jetson-model", default="", help="Jetson.GPIO가 보드를 못 알아볼 때: JETSON_ORIN_NANO")
    args = ap.parse_args()

    try:
        if args.cmd == "buses":
            result = cmd_buses(args)
        elif args.cmd == "mux":
            result = cmd_mux(args)
        else:
            channel = None if args.cmd != "pt100" and args.mux_addr is None else getattr(args, "channel", None)
            if args.cmd == "thermal":
                sensor: SensorAdapter = Mlx90640Thermal(args.sensor_id or f"thermal_{args.channel}", bus_no=args.bus,
                                                        mux_addr=args.mux_addr, channel=channel, refresh_hz=args.refresh,
                                                        retries=args.retries)
            else:
                sensor = Max31865Rtd(args.sensor_id or "pt100_0", cs_pin=args.cs_pin, ref_ohms=args.ref_ohms,
                                     wires=args.wires, jetson_model_name=args.jetson_model)
            result = run_reads(sensor, args)
    except SensorError as exc:
        print(f"[실패] {exc}")
        return 1
    if args.out:
        Path(args.out).write_text(json.dumps({"command": args.cmd, "args": vars(args), **result}, ensure_ascii=False, indent=1, default=str))
        print(f"저장: {args.out}")
    if args.cmd in ("thermal", "pt100"):
        return 0 if (result.get("summary") or {}).get("valid") else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
